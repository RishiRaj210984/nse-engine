"""Self-learning loop.

Every day:
  1. JOURNAL  - every signal (passed + near-miss) is written with its features.
  2. EVALUATE - after 2 trading days each open signal is marked: T2 / T1 / SL /
                TIME / NOFILL, using realistic fills (entry at next day's open,
                stop assumed hit first if a bar touches both stop and target).
  3. LEARN    - from all graded outcomes (recent + live weighted higher):
                a) exit geometry (SL / T1 / T2 in ATR) re-simulated on stored price paths
                b) entry thresholds (volume x, RSI band, distance to high)
                c) a logistic-regression ranker -> win probability per stock
                d) the macro bias threshold and news weight
  4. GUARDRAILS - a parameter only moves one grid step per day and only with
                >= MIN_SAMPLES_TUNE trades behind it; the ranker is used only if
                it beats the base rate on a held-out, later time period.
Nothing here can "guarantee" accuracy; it measures itself and adapts slowly.
"""
import math
from datetime import date, datetime

import numpy as np
import pandas as pd

import data
import macro
import store
from config import (GLOBAL_TICKERS, HORIZON_DAYS, INDEX_SPECS, IST, JOURNAL_GATE, LIVE_SAMPLE_WEIGHT,
                    MIN_SAMPLES_MODEL, MIN_SAMPLES_TUNE, PARAM_GRID, RECENCY_HALF_LIFE, UNIVERSE)
from indicators import FEATURES, enrich, feature_vector, trade_levels


def _key(r):
    return (r["trade_date"], r["kind"], r["symbol"], r["source"])


def add_signals(new_rows, journal=None):
    j = journal if journal is not None else store.load_journal()
    keys = {_key(r) for r in j}
    nid = max((r["id"] for r in j), default=0) + 1
    added = 0
    for r in new_rows:
        if _key(r) in keys:
            continue
        r["id"] = nid
        nid += 1
        keys.add(_key(r))
        j.append(r)
        added += 1
    store.save_journal(j)
    return added


# ------------------------------------------------------------------ simulation
def normalize_path(fut, ref, atr, direction):
    """Bars -> [o,h,l,c] in ATR units from the signal close, flipped so 'up' is profit."""
    out = []
    for o, h, l, c in fut[["Open", "High", "Low", "Close"]].itertuples(index=False):
        if direction >= 0:
            out.append([(o - ref) / atr, (h - ref) / atr, (l - ref) / atr, (c - ref) / atr])
        else:
            out.append([(ref - o) / atr, (ref - l) / atr, (ref - h) / atr, (ref - c) / atr])
    return [[round(v, 4) for v in b] for b in out]


def simulate(path, sl, t1, t2):
    """Entry at first bar's open. Half booked at T1 (stop -> entry), rest at T2 / stop / last close."""
    if not path:
        return "NOFILL", 0.0, 0.0, 0.0
    e, stop = path[0][0], -sl
    if e <= stop or e >= t1:
        return "NOFILL", 0.0, 0.0, 0.0
    risk = sl  # R is measured against the PLANNED stop distance (how positions are sized)
    mfe = (max(b[1] for b in path) - e) / risk
    mae = (e - min(b[2] for b in path)) / risk
    hit1, realized = False, 0.0
    for i, (o, h, l, c) in enumerate(path):
        if i > 0 and o <= stop:  # gap through the stop
            return ("T1", realized + 0.5 * (o - e) / risk, mfe, mae) if hit1 else ("SL", (o - e) / risk, mfe, mae)
        if not hit1:
            if l <= stop:
                return "SL", (stop - e) / risk, mfe, mae
            if h >= t1:
                hit1, realized, stop = True, 0.5 * (t1 - e) / risk, e
                if h >= t2:
                    return "T2", realized + 0.5 * (t2 - e) / risk, mfe, mae
        else:
            if l <= stop:
                return "T1", realized, mfe, mae
            if h >= t2:
                return "T2", realized + 0.5 * (t2 - e) / risk, mfe, mae
    last = path[-1][3]
    if hit1:
        return "T1", realized + 0.5 * (last - e) / risk, mfe, mae
    return "TIME", (last - e) / risk, mfe, mae


def outcome_for(rec, fut):
    if rec["direction"] == 0:  # range view: win if price stays inside
        inside = fut["High"].max() < rec["stop"] and fut["Low"].min() > rec["t1"]
        return {"result": "WIN" if inside else "LOSS", "r_multiple": 1.0 if inside else -1.0,
                "evaluated_at": store.now_ist().isoformat()}
    a, d = rec["atr"], rec["direction"]
    path = normalize_path(fut, rec["entry"], a, d)
    sl = abs(rec["entry"] - rec["stop"]) / a
    t1 = abs(rec["t1"] - rec["entry"]) / a
    t2 = abs(rec["t2"] - rec["entry"]) / a
    res, r, mfe, mae = simulate(path, sl, t1, t2)
    return {"result": res, "r_multiple": round(r, 3), "mfe_r": round(mfe, 3), "mae_r": round(mae, 3),
            "path": path, "evaluated_at": store.now_ist().isoformat()}


# ------------------------------------------------------------------ journaling
def journal_scan(rows, state):
    recs = []
    for x in rows:
        recs.append({
            "created_at": store.now_ist().isoformat(), "trade_date": x["bar_date"], "kind": "stock",
            "symbol": x["symbol"], "direction": 1, "entry": x["entry"], "stop": x["stop"],
            "t1": x["t1"], "t2": x["t2"], "atr": x["atr"], "features": x["features"],
            "passed": int(x["passed"]), "score": x.get("expected_r"), "grade": x.get("grade"),
            "model_version": state["version"], "source": "live", "status": "open",
            "meta": {"r1_a": x["r1_a"], "r2_a": x["r2_a"]}, "outcome": None,
        })
    return add_signals(recs)


def journal_index_views(bias):
    label = bias["bias"]["label"]
    recs = []
    for name, o in bias["options"].items():
        lv, pv = o["levels"], o["levels"]["pivots"]
        rec = {"created_at": store.now_ist().isoformat(), "trade_date": bias["reference_date"],
               "kind": "index_view", "symbol": name, "entry": lv["prev_close"], "atr": lv["atr"],
               "features": {"z": bias["bias"]["z"], "vix": bias["bias"]["india_vix"]},
               "passed": 1, "score": bias["bias"]["score"], "model_version": bias["model"]["version"],
               "source": "live", "status": "open", "meta": {"label": label}, "outcome": None}
        if label == "BULLISH":
            rec.update(direction=1, stop=min(pv["S1"], lv["support"]), t1=pv["R1"], t2=pv["R2"])
        elif label == "BEARISH":
            rec.update(direction=-1, stop=max(pv["R1"], lv["resistance"]), t1=pv["S1"], t2=pv["S2"])
        else:
            legs = o["strategies"][0]["legs"]
            rec.update(direction=0, stop=legs[0]["strike"], t1=legs[2]["strike"], t2=None)
        recs.append(rec)
    return add_signals(recs)


def log_bias(bias):
    log = store.load_bias_log()
    b = bias["bias"]
    log[bias["target_session"]] = {**log.get(bias["target_session"], {}),
                                   "created_at": store.now_ist().isoformat(), "predicted": b["predicted_nifty_move_pct"],
                                   "z": b["z"], "model_z": b["model_z"], "bias": b["label"],
                                   "news_score": bias["news"]["summary"]["score"],
                                   "typical_move": b["typical_daily_move_pct"]}
    store.save_bias_log(log)


def fill_bias_actuals():
    log = store.load_bias_log()
    n = data.history(["^NSEI"], period="3mo").get("^NSEI")
    if n is None:
        return
    chg = n["Close"].pct_change() * 100
    live_today = data.market_open() and n.index[-1].date() == data.today_ist()
    for d, row in log.items():
        ts = pd.Timestamp(d)
        if row.get("actual") is None and ts in chg.index and not (live_today and ts.date() == data.today_ist()):
            row["actual"] = round(float(chg.loc[ts]), 3)
    store.save_bias_log(log)


# ------------------------------------------------------------------ evaluation
def _yahoo(rec):
    return INDEX_SPECS[rec["symbol"]]["ticker"] if rec["kind"] == "index_view" else rec["symbol"] + ".NS"


def evaluate_open():
    j = store.load_journal()
    open_ = [r for r in j if r["status"] == "open"]
    if not open_:
        return 0
    hist = data.history(sorted({_yahoo(r) for r in open_}), period="6mo", ttl=900)
    today, live = data.today_ist(), data.market_open()
    done = 0
    for r in open_:
        df = hist.get(_yahoo(r))
        if df is None:
            continue
        fut = df[df.index.date > date.fromisoformat(r["trade_date"])]
        if live and len(fut) and fut.index[-1].date() == today:
            fut = fut.iloc[:-1]  # never grade on a partial bar
        if len(fut) < HORIZON_DAYS:
            continue
        r["outcome"] = outcome_for(r, fut.iloc[:HORIZON_DAYS])
        r["status"] = "closed"
        done += 1
    store.save_journal(j)
    return done


# ------------------------------------------------------------------ learning
def _sigmoid(z):
    return 1 / (1 + np.exp(-np.clip(z, -30, 30)))


def fit_logit(X, y, w, lam=2.0):
    mu, sd = X.mean(0), X.std(0) + 1e-9
    Z = np.hstack([np.ones((len(X), 1)), (X - mu) / sd])
    beta = np.zeros(Z.shape[1])
    reg = lam * np.eye(Z.shape[1])
    reg[0, 0] = 0
    for _ in range(30):
        p = _sigmoid(Z @ beta)
        W = w * p * (1 - p) + 1e-9
        g = Z.T @ (w * (p - y)) + reg @ beta
        H = Z.T @ (Z * W[:, None]) + reg
        step = np.linalg.solve(H, g)
        beta -= step
        if np.abs(step).max() < 1e-6:
            break
    return {"mu": mu.tolist(), "sd": sd.tolist(), "beta": beta.tolist(), "features": FEATURES}


def _predict_logit(m, X):
    Z = (np.asarray(X) - np.asarray(m["mu"])) / np.asarray(m["sd"])
    b = np.asarray(m["beta"])
    return _sigmoid(b[0] + Z @ b[1:])


def _logloss(p, y, w):
    p = np.clip(p, 1e-6, 1 - 1e-6)
    return float(-(w * (y * np.log(p) + (1 - y) * np.log(1 - p))).sum() / w.sum())


def _auc(p, y):
    pos, neg = p[y == 1], p[y == 0]
    if len(pos) == 0 or len(neg) == 0:
        return None
    ranks = pd.Series(np.concatenate([pos, neg])).rank().values
    return float((ranks[:len(pos)].sum() - len(pos) * (len(pos) + 1) / 2) / (len(pos) * len(neg)))


def _dataset(j):
    rows = [r for r in j if r["kind"] == "stock" and r["status"] == "closed" and r.get("outcome")
            and r["outcome"]["result"] != "NOFILL" and r["outcome"].get("path")]
    rows.sort(key=lambda r: r["trade_date"])
    if not rows:
        return rows, np.array([])
    last = np.datetime64(rows[-1]["trade_date"])
    age = np.array([np.busday_count(np.datetime64(r["trade_date"]), last) for r in rows])
    w = 0.5 ** (age / RECENCY_HALF_LIFE)
    w *= np.array([LIVE_SAMPLE_WEIGHT if r["source"] == "live" else 1.0 for r in rows])
    return rows, w


def _arrays(rows):
    g = lambda k, d=0.0: np.array([r["features"].get(k, d) for r in rows], dtype=float)
    return {"vr": np.exp(g("log_vol_ratio")), "rsi": g("rsi"), "dist": g("dist_high_pct"),
            "day": g("day_chg"), "regime": (g("mkt_above50", 1.0) > 0) & (g("mkt_ret20") > 0),
            "atr_pct": np.maximum(g("atr_pct", 2.0), 0.1)}


def _entry_mask(rows, p, A=None):
    A = A or _arrays(rows)
    m = ((A["vr"] >= p["vol_mult"]) & (A["rsi"] >= p["rsi_lo"]) & (A["rsi"] <= p["rsi_hi"])
         & (A["dist"] <= p["near_high_pct"]) & (A["day"] <= p.get("max_day_chg", 99)))
    if p.get("regime_gate"):
        m &= A["regime"]
    return m


def _sim_r(rows, sl, t1a, t2a, cost_pct=0.0):
    """R per trade NET of costs (cost converted to R via each stock's ATR%)."""
    out = np.empty(len(rows))
    for i, r in enumerate(rows):
        t1 = max(r["meta"].get("r1_a", 0), t1a)
        t2 = max(r["meta"].get("r2_a", 0), t2a, t1 + 0.25)
        res, rr, _, _ = simulate(r["outcome"]["path"], sl, t1, t2)
        cost_r = cost_pct / max(r["features"].get("atr_pct", 2.0), 0.1) / sl
        out[i] = np.nan if res == "NOFILL" else rr - cost_r
    return out


def _score(r, w):
    ok = ~np.isnan(r)
    if ok.sum() < MIN_SAMPLES_TUNE:
        return -np.inf, int(ok.sum()), None
    r, w = r[ok], w[ok]
    m = np.average(r, weights=w)
    sd = math.sqrt(np.average((r - m) ** 2, weights=w)) + 1e-9
    n_eff = w.sum() ** 2 / (w ** 2).sum()
    return m / sd * math.sqrt(n_eff), int(ok.sum()), float(m)


def _step_toward(cur, best, grid):
    """Move at most one grid step from cur toward best (stability guardrail)."""
    if cur not in grid:
        cur = min(grid, key=lambda g: abs(g - cur))
    i, k = grid.index(cur), grid.index(best)
    return grid[i + (k > i) - (k < i)]


def retrain():
    from config import DEFAULT_PARAMS
    old = {**DEFAULT_PARAMS, **store.baseline_state()["params"]}
    p = dict(old)
    j = store.load_journal()
    rows, w = _dataset(j)
    notes, metrics = [], {"samples": len(rows), "live_samples": sum(r["source"] == "live" for r in rows)}

    if len(rows) >= MIN_SAMPLES_TUNE:
        # a) exits, on rows that pass the current entry rules (fallback: all rows)
        mask = _entry_mask(rows, p)
        sub = [r for r, m in zip(rows, mask) if m] if mask.sum() >= MIN_SAMPLES_TUNE else rows
        wsub = w[mask] if mask.sum() >= MIN_SAMPLES_TUNE else w
        best, best_s = None, -np.inf
        for sl in PARAM_GRID["sl_atr"]:
            for t1 in PARAM_GRID["t1_atr"]:
                for t2 in PARAM_GRID["t2_atr"]:
                    if t2 <= t1:
                        continue
                    s, _, _ = _score(_sim_r(sub, sl, t1, t2, p["cost_pct"]), wsub)
                    if s > best_s:
                        best, best_s = (sl, t1, t2), s
        if best:
            for k, v in zip(("sl_atr", "t1_atr", "t2_atr"), best):
                p[k] = _step_toward(p[k], v, PARAM_GRID[k])

        # b) entry thresholds, scored with the (new) exits
        r_all = _sim_r(rows, p["sl_atr"], p["t1_atr"], p["t2_atr"], p["cost_pct"])
        A = _arrays(rows)
        keys = ("vol_mult", "rsi_lo", "rsi_hi", "near_high_pct", "max_day_chg", "regime_gate")
        best, best_s = None, -np.inf
        import itertools
        for combo in itertools.product(*(PARAM_GRID[k] for k in keys)):
            m = _entry_mask(rows, dict(zip(keys, combo)), A)
            s, n, _ = _score(np.where(m, r_all, np.nan), w)
            if s > best_s:
                best, best_s = combo, s
        if best:
            for k, v in zip(keys, best):
                p[k] = _step_toward(p[k], v, PARAM_GRID[k])

        # stats under the deployed rules (used for expected-R)
        r_dep = np.where(_entry_mask(rows, p), _sim_r(rows, p["sl_atr"], p["t1_atr"], p["t2_atr"], p["cost_pct"]), np.nan)
        ok = ~np.isnan(r_dep)
        if ok.sum():
            rr, ww = r_dep[ok], w[ok]
            wins = rr > 0
            metrics.update({
                "rule_trades": int(ok.sum()),
                "base_win_rate": round(float(np.average(wins, weights=ww)), 4),
                "avg_win_r": round(float(np.average(rr[wins], weights=ww[wins])), 3) if wins.any() else 1.0,
                "avg_loss_r": round(float(-np.average(rr[~wins], weights=ww[~wins])), 3) if (~wins).any() else 1.0,
                "expectancy_r": round(float(np.average(rr, weights=ww)), 3),
            })
    else:
        notes.append(f"waiting for {MIN_SAMPLES_TUNE} graded trades before tuning")

    # c) ranker
    weights = None
    if len(rows) >= MIN_SAMPLES_MODEL:
        X = np.array([[r["features"].get(f, 0.0) for f in FEATURES] for r in rows])
        y = np.array([1.0 if r["outcome"]["r_multiple"] > 0 else 0.0 for r in rows])
        cut = int(len(rows) * 0.8)
        m = fit_logit(X[:cut], y[:cut], w[:cut])
        ph = _predict_logit(m, X[cut:])
        base = np.full(len(ph), np.average(y[:cut], weights=w[:cut]))
        ll_m, ll_b = _logloss(ph, y[cut:], w[cut:]), _logloss(base, y[cut:], w[cut:])
        auc = _auc(ph, y[cut:])
        metrics.update({"holdout_logloss": round(ll_m, 4), "baseline_logloss": round(ll_b, 4),
                        "holdout_auc": None if auc is None else round(auc, 3), "holdout_n": int(len(ph))})
        if ll_m < ll_b and (auc or 0) > 0.52:
            weights = fit_logit(X, y, w)
            notes.append(f"ranker ON (holdout AUC {auc:.2f})")
        else:
            notes.append("ranker OFF: no out-of-sample edge, using rule stats only")

    # d) macro bias threshold (walk-forward) + news weight (live log)
    try:
        hist = data.history(list(GLOBAL_TICKERS.values()), period="5y")
        wf = macro.walk_forward(macro.build_dataset(hist), test=500)
        best, best_s = p["bias_threshold"], -1
        for thr in PARAM_GRID["bias_threshold"]:
            mt = macro.wf_metrics(wf, {**p, "bias_threshold": thr})
            if mt.get("directional_accuracy") is None or mt["directional_calls"] < 0.25 * mt["oos_days"]:
                continue
            if mt["directional_accuracy"] > best_s:
                best, best_s = thr, mt["directional_accuracy"]
        p["bias_threshold"] = _step_toward(p["bias_threshold"], best, PARAM_GRID["bias_threshold"])
        metrics["bias_wf"] = macro.wf_metrics(wf, p)
    except Exception as exc:
        notes.append(f"bias tuning skipped: {exc}")

    log = [v for v in store.load_bias_log().values() if v.get("actual") is not None and v.get("typical_move")]
    if len(log) >= 20:
        resid = np.array([v["actual"] - v["predicted"] for v in log]) / np.array([v["typical_move"] for v in log])
        nz = np.clip(np.array([v["news_score"] for v in log]) / 0.15, -2, 2)
        slope = float((nz * resid).sum() / ((nz * nz).sum() + 1e-9))
        p["news_weight"] = round(float(np.clip(0.7 * p["news_weight"] + 0.3 * slope, 0.0, 0.5)), 3)

    changes = [f"{k} {old[k]}→{p[k]}" for k in p if old.get(k) != p[k]]
    note = "; ".join(changes + notes) or "no change"
    version = store.save_state(p, weights, metrics, note)
    return {"version": version, "note": note, "metrics": metrics}


def predict(features, state):
    m = state.get("metrics") or {}
    base = m.get("base_win_rate", 0.5)
    aw, al = m.get("avg_win_r", 1.2), m.get("avg_loss_r", 1.0)
    w = state.get("weights")
    if w:
        pwin = float(_predict_logit(w, [[features.get(f, 0.0) for f in w["features"]]])[0])
    else:
        pwin = base
    return {"win_prob": round(pwin * 100, 1), "expected_r": round(pwin * aw - (1 - pwin) * al, 2),
            "model_used": bool(w)}


def model_info(state):
    m = state.get("metrics") or {}
    return {"version": state["version"], "updated": state["created_at"], "ranker_on": bool(state.get("weights")),
            "samples": m.get("samples", 0), "live_samples": m.get("live_samples", 0),
            "base_win_rate": m.get("base_win_rate"), "expectancy_r": m.get("expectancy_r"),
            "holdout_auc": m.get("holdout_auc"), "note": state.get("note")}


# ------------------------------------------------------------------ cold start
def bootstrap(days=500):
    """Seed the journal from history so learning starts on day one (source='backtest')."""
    state = store.latest_state()
    p = state["params"]
    hist = data.history([s + ".NS" for s in UNIVERSE] + ["^NSEI"], period="3y", ttl=3600)
    nclose = hist["^NSEI"]["Close"] if "^NSEI" in hist else None
    bias_hist = macro.historical_bias_scores(test=days + 60)
    g = JOURNAL_GATE
    recs = []
    for sym in UNIVERSE:
        df = hist.get(sym + ".NS")
        if df is None or len(df) < 120:
            continue
        e = enrich(df, nclose)
        mask = ((e["Close"] > e["ema20"]) & (e["Close"] > e["ema50"]) & (e["vol_ratio"] >= g["vol_mult"])
                & e["rsi"].between(g["rsi_lo"], g["rsi_hi"]) & (e["dist_high_pct"] <= g["near_high_pct"])
                & e["atr"].gt(0) & e["vol_avg20"].notna())
        start = max(60, len(e) - days - HORIZON_DAYS)
        for i in np.where(mask.values)[0]:
            if i < start or i > len(e) - 1 - HORIZON_DAYS:
                continue
            row, d = e.iloc[i], e.index[i]
            bs = float(bias_hist.asof(d)) if len(bias_hist) and d >= bias_hist.index[0] else 0.0
            lv = trade_levels(row, p)
            passed = (row["vol_ratio"] >= p["vol_mult"] and p["rsi_lo"] <= row["rsi"] <= p["rsi_hi"]
                      and row["dist_high_pct"] <= p["near_high_pct"])
            rec = {"created_at": store.now_ist().isoformat(), "trade_date": d.date().isoformat(), "kind": "stock",
                   "symbol": sym, "direction": 1, "entry": lv["entry"], "stop": lv["stop"], "t1": lv["t1"],
                   "t2": lv["t2"], "atr": lv["atr"], "features": feature_vector(row, None, bs, 0.0),
                   "passed": int(passed), "score": None, "model_version": state["version"],
                   "source": "backtest", "status": "closed", "meta": {"r1_a": lv["r1_a"], "r2_a": lv["r2_a"]}}
            rec["outcome"] = outcome_for(rec, e.iloc[i + 1:i + 1 + HORIZON_DAYS])
            recs.append(rec)
    added = add_signals(recs)
    return {"backtest_signals_added": added}


# ------------------------------------------------------------------ reporting
def _stats(rows):
    rs = [r["outcome"]["r_multiple"] for r in rows]
    if not rs:
        return {"trades": 0}
    wins = [x for x in rs if x > 0]
    by = {}
    for r in rows:
        by[r["outcome"]["result"]] = by.get(r["outcome"]["result"], 0) + 1
    return {"trades": len(rs), "win_rate": round(100 * len(wins) / len(rs), 1),
            "avg_r": round(sum(rs) / len(rs), 3), "total_r": round(sum(rs), 2), "by_result": by}


def performance():
    j = store.load_journal()
    closed = [r for r in j if r["status"] == "closed" and r.get("outcome") and r["outcome"]["result"] != "NOFILL"]
    live_stock = [r for r in closed if r["kind"] == "stock" and r["source"] == "live" and r["passed"]]
    state = store.latest_state()
    p = state["params"]
    bt_rows = [r for r in _dataset(j)[0] if r["source"] == "backtest"]
    bt_r = _sim_r(bt_rows, p["sl_atr"], p["t1_atr"], p["t2_atr"], p["cost_pct"]) if bt_rows else np.array([])
    bt_mask = _entry_mask(bt_rows, p) if bt_rows else np.array([], dtype=bool)
    bt_stock = [{**r, "outcome": {**r["outcome"], "r_multiple": float(x)}}
                for r, x, m in zip(bt_rows, bt_r, bt_mask) if m and not np.isnan(x)]
    idx = [r for r in closed if r["kind"] == "index_view"]
    curve, cum = [], 0.0
    for r in sorted(live_stock or bt_stock[-250:], key=lambda r: r["trade_date"]):
        cum += r["outcome"]["r_multiple"]
        curve.append({"date": r["trade_date"], "cum_r": round(cum, 2)})
    blog = store.load_bias_log()
    graded = []
    for d, v in sorted(blog.items()):
        if v.get("actual") is None:
            continue
        tm = v.get("typical_move") or 1.0
        ok = {"BULLISH": v["actual"] > 0, "BEARISH": v["actual"] < 0}.get(v["bias"], abs(v["actual"]) < 0.5 * tm)
        graded.append({"date": d, "bias": v["bias"], "actual": v["actual"], "correct": bool(ok)})
    return {
        "generated_at": store.now_ist().isoformat(),
        "live_stocks": _stats(live_stock),
        "backtest_stocks": {**_stats(bt_stock), "note": "current rules, net of costs, entry at next open"},
        "index_views": _stats(idx),
        "bias_live": {"days": len(graded),
                      "accuracy": round(100 * sum(g["correct"] for g in graded) / len(graded), 1) if graded else None,
                      "recent": graded[-15:][::-1]},
        "bias_walk_forward": (state.get("metrics") or {}).get("bias_wf"),
        "equity_curve": curve[-200:],
        "curve_source": "live" if live_stock else "backtest",
        "recent_live": [{"date": r["trade_date"], "symbol": r["symbol"], "kind": r["kind"],
                         "result": r["outcome"]["result"], "r": r["outcome"]["r_multiple"]}
                        for r in sorted([r for r in closed if r["source"] == "live"],
                                        key=lambda r: r["trade_date"])[-25:][::-1]],
        "open_signals": sum(1 for r in j if r["status"] == "open"),
        "model": model_info(state),
        "params": state["params"],
        "history": store.model_history(15),
        "options_lab": store._read(store.state_path("options_lab.json"), None),
    }
