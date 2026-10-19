"""Module A part 1: overnight global moves -> next-session Nifty bias.

A ridge regression maps the last US session (S&P, Nasdaq, 10Y yield, crude,
dollar) plus Nifty's own state (prior return, distance from EMA20, India VIX)
to the next Nifty close-to-close return. It is refit on a rolling ~3-year
window every time it runs, so it re-learns daily, and its walk-forward
(out-of-sample) accuracy is reported so you can judge how far to trust it.

GIFT Nifty is not available on Yahoo Finance. Its information is mostly a
function of the same US moves, which is what this model uses instead.
"""
from datetime import datetime

import numpy as np
import pandas as pd

import data
import store
import news
import options
from config import GLOBAL_TICKERS, IST
from indicators import ema

US_FEATURES = ["spx", "ndx", "tnx", "crude", "dxy"]
IN_FEATURES = ["nifty_prev", "nifty_ext", "vix", "vix_chg"]
FEATURES = US_FEATURES + IN_FEATURES
FEATURE_LABELS = {
    "spx": "S&P 500 overnight %", "ndx": "Nasdaq minus S&P (tech tilt) %", "tnx": "US 10Y yield chg (pts)",
    "crude": "Crude oil %", "dxy": "Dollar index %", "nifty_prev": "Nifty previous day %",
    "nifty_ext": "Nifty vs EMA20 %", "vix": "India VIX level", "vix_chg": "India VIX change",
}
WINDOW = 750
US_SOURCES = {"spx": ("^GSPC", "pct"), "ndx": ("^IXIC", "pct"), "tnx": ("^TNX", "diff"),
              "crude": ("CL=F", "pct"), "dxy": ("DX-Y.NYB", "pct")}


def _us_frame(hist):
    cols = {}
    for f, (t, kind) in US_SOURCES.items():
        df = hist.get(t)
        if df is not None:
            c = df["Close"]
            cols[f] = c.diff() if kind == "diff" else c.pct_change() * 100
    us = pd.concat(cols, axis=1).sort_index() if cols else pd.DataFrame()
    for f in US_FEATURES:
        if f not in us:
            us[f] = 0.0
    us = us[US_FEATURES].fillna(0.0)  # US holiday for one asset -> zero move
    us["ndx"] = us["ndx"] - us["spx"]  # tech tilt; avoids S&P/Nasdaq collinearity
    return us


def _vix(hist, index):
    v = hist.get("^INDIAVIX")
    if v is None:
        return pd.Series(15.0, index=index)
    return v["Close"].reindex(index.union(v.index)).ffill().reindex(index)


def build_dataset(hist):
    n = hist["^NSEI"]
    c = n["Close"]
    nd = pd.DataFrame(index=n.index)
    nd["target"] = c.pct_change() * 100
    nd["nifty_prev"] = nd["target"].shift(1)
    nd["nifty_ext"] = ((c / ema(c, 20) - 1) * 100).shift(1)
    v = _vix(hist, n.index)
    nd["vix"] = v.shift(1)
    nd["vix_chg"] = v.diff().shift(1)
    frame = pd.merge_asof(nd.sort_index(), _us_frame(hist), left_index=True, right_index=True,
                          allow_exact_matches=False)
    return frame.dropna()


def fit_ridge(X, y, lam=5.0):
    mu, sd = X.mean(0), X.std(0) + 1e-9
    Z = (X - mu) / sd
    w = np.linalg.solve(Z.T @ Z + lam * np.eye(Z.shape[1]), Z.T @ (y - y.mean()))
    resid = y - (Z @ w + y.mean())
    return {"mu": mu, "sd": sd, "w": w, "b": float(y.mean()), "resid_sd": float(resid.std() + 1e-9)}


def predict_ridge(m, X):
    return ((X - m["mu"]) / m["sd"]) @ m["w"] + m["b"]


def walk_forward(frame, test=250, block=20, min_train=250):
    """Out-of-sample predictions: each block is predicted by a model fit only on earlier data."""
    rows = []
    start = max(min_train, len(frame) - test)
    for s in range(start, len(frame), block):
        train = frame.iloc[max(0, s - WINDOW):s]
        m = fit_ridge(train[FEATURES].values, train["target"].values)
        blk = frame.iloc[s:s + block]
        pred = predict_ridge(m, blk[FEATURES].values)
        for ts, p, a, vix in zip(blk.index, pred, blk["target"].values, blk["vix"].values):
            rows.append({"date": ts, "pred": float(p), "actual": float(a),
                         "z": float(p / m["resid_sd"]), "vix": float(vix)})
    return pd.DataFrame(rows).set_index("date") if rows else pd.DataFrame()


def label_for(z, vix, params):
    thr = params["bias_threshold"]
    if vix >= params["vix_high"] and abs(z) < 2 * thr:
        return "RANGEBOUND"
    if z >= thr:
        return "BULLISH"
    if z <= -thr:
        return "BEARISH"
    return "RANGEBOUND"


def wf_metrics(wf, params):
    if wf.empty:
        return {}
    labels = [label_for(z, v, params) for z, v in zip(wf["z"], wf["vix"])]
    calls = wf[[lab != "RANGEBOUND" for lab in labels]]
    hits = (np.sign(calls["pred"]) == np.sign(calls["actual"])).mean() if len(calls) else float("nan")
    return {
        "oos_days": int(len(wf)),
        "directional_calls": int(len(calls)),
        "directional_accuracy": None if np.isnan(hits) else round(float(hits) * 100, 1),
        "sign_accuracy_all_days": round(float((np.sign(wf["pred"]) == np.sign(wf["actual"])).mean()) * 100, 1),
    }


def _latest_features(hist, target_date):
    ts = pd.Timestamp(target_date)
    us = _us_frame(hist)
    us = us[us.index < ts]
    n = hist["^NSEI"]
    prior = n[n.index < ts]
    c = prior["Close"]
    v = _vix(hist, prior.index)
    row = {**us.iloc[-1].to_dict(),
           "nifty_prev": float(c.pct_change().iloc[-1] * 100),
           "nifty_ext": float((c.iloc[-1] / ema(c, 20).iloc[-1] - 1) * 100),
           "vix": float(v.iloc[-1]), "vix_chg": float(v.diff().iloc[-1])}
    return np.array([row[f] for f in FEATURES]), row, us.index[-1].date(), prior


def global_bias(force=False):
    if not force:
        cached = store.cache_get("bias:snapshot")
        if cached is not None:
            return cached

    state = store.latest_state()
    p = state["params"]
    now = datetime.now(IST)
    hist = data.history(list(GLOBAL_TICKERS.values()), period="5y")
    if "^NSEI" not in hist:
        raise RuntimeError("Nifty data unavailable from Yahoo Finance right now")

    frame = build_dataset(hist)
    m = fit_ridge(frame[FEATURES].values[-WINDOW:], frame["target"].values[-WINDOW:])
    target_date = data.next_session_date(now)
    x, raw, us_date, nifty_prior = _latest_features(hist, target_date)
    pred = float(predict_ridge(m, x[None, :])[0])
    z_model = pred / m["resid_sd"]

    mnews = news.market_news()
    news_score = mnews["summary"]["score"]
    news_z = float(np.clip(news_score / 0.15, -2, 2))
    z = z_model + p["news_weight"] * news_z
    label = label_for(z, raw["vix"], p)

    contrib = ((x - m["mu"]) / m["sd"]) * m["w"]
    components = sorted(
        [{"feature": f, "label": FEATURE_LABELS[f], "value": round(float(raw[f]), 3),
          "contribution_pct": round(float(cv), 3)} for f, cv in zip(FEATURES, contrib)],
        key=lambda r: -abs(r["contribution_pct"]))

    wf = walk_forward(frame)
    metrics = wf_metrics(wf, p)

    # Options structures for both indices
    idx_opts = {}
    vix_iv = raw["vix"] / 100
    nifty_rv = nifty_prior["Close"].pct_change().tail(20).std()
    for name, t in (("NIFTY", "^NSEI"), ("BANKNIFTY", "^NSEBANK")):
        df = hist.get(t)
        if df is None:
            continue
        prior = df[df.index < pd.Timestamp(target_date)]
        live = float(df["Close"].iloc[-1])
        iv = vix_iv
        if name == "BANKNIFTY" and nifty_rv > 0:
            iv = vix_iv * float(np.clip(prior["Close"].pct_change().tail(20).std() / nifty_rv, 0.8, 1.8))
        idx_opts[name] = options.build(name, label, prior, live, iv, target_date, now)

    import options_lab
    try:
        events = store.cache_get("events") or options_lab.events_calendar()
        store.cache_set("events", events, 6 * 3600)
    except Exception:
        events = []
    lab = options_lab.load()
    rank = {"TRADE": 0, "CAUTION": 1, "SKIP": 2}
    for name, o in idx_opts.items():
        for s in o["strategies"]:
            s["verdict"] = options_lab.setup_verdict(name, s["name"], target_date.isoformat(), lab, events)
        # best evidence first: TRADE before CAUTION before SKIP, then by historical average P&L
        o["strategies"].sort(key=lambda s: (rank[s["verdict"]["decision"]],
                                            -((s["verdict"].get("stats") or {}).get("avg_pnl") or -10**9)))
        o["setup"] = o["strategies"][0]["verdict"]
        if o["setup"]["decision"] == "SKIP":
            o["setup"] = {**o["setup"], "reason": "No tested structure has a positive record for today. "
                          + o["setup"]["reason"]}

    score = int(round(100 * np.tanh(z)))
    result = {
        "generated_at": now.isoformat(),
        "target_session": target_date.isoformat(),
        "reference_date": nifty_prior.index[-1].date().isoformat(),
        "us_session_used": us_date.isoformat(),
        "tickers": data.quote_table(hist, GLOBAL_TICKERS),
        "bias": {
            "label": label, "score": score, "z": round(float(z), 3),
            "model_z": round(float(z_model), 3),
            "predicted_nifty_move_pct": round(pred, 3),
            "typical_daily_move_pct": round(m["resid_sd"], 3),
            "india_vix": round(raw["vix"], 2),
            "components": components,
        },
        "news": {"summary": mnews["summary"], "weight": p["news_weight"], "headlines": mnews["items"][:12]},
        "model": {**metrics, "trained_rows": int(min(len(frame), WINDOW)),
                  "version": state["version"], "bias_threshold": p["bias_threshold"]},
        "options": idx_opts,
        "events": events,
        "notes": [
            "GIFT Nifty is not on Yahoo Finance; overnight US moves are used as the proxy.",
            "Option premiums are theoretical estimates. Confirm on a live option chain.",
        ],
    }
    store.cache_set("bias:snapshot", result, 600)
    return result


def historical_bias_scores(hist=None, test=600):
    """Out-of-sample bias score per date, used to label backtest signals."""
    hist = hist or data.history(list(GLOBAL_TICKERS.values()), period="5y")
    if "^NSEI" not in hist:
        return pd.Series(dtype=float)
    wf = walk_forward(build_dataset(hist), test=test)
    return (100 * np.tanh(wf["z"])).round() if not wf.empty else pd.Series(dtype=float)
