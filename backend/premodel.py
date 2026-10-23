"""Pre-open global snapshot model.

At run time (midnight preview or ~07:30 IST final) it measures how every global
market has moved since Nifty's last close: US index futures (S&P, Nasdaq, Dow),
Europe's close (DAX, FTSE, Euro Stoxx), Nikkei so far today, crude, gold,
dollar index and USD/INR, plus Nifty's previous day and India VIX.

A ridge regression trained on the SAME time-of-day snapshot over ~2 years of
hourly data predicts (a) the opening gap and (b) the full-day Nifty move.
Walk-forward (out-of-sample) accuracy is reported, and the model is refit on
every run, so it re-learns daily. GIFT Nifty has no free feed; it largely
reflects these same inputs.
"""
import logging
from datetime import datetime, timedelta, timezone

import numpy as np
import pandas as pd
import yfinance as yf

import store
from config import IST

log = logging.getLogger(__name__)

MARKETS = {
    "es": ("S&P 500 futures", "ES=F"), "nq": ("Nasdaq futures", "NQ=F"), "ym": ("Dow futures", "YM=F"),
    "dax": ("DAX (Germany)", "^GDAXI"), "ftse": ("FTSE 100 (UK)", "^FTSE"), "stoxx": ("Euro Stoxx 50", "^STOXX50E"),
    "n225": ("Nikkei 225", "^N225"), "cl": ("Crude oil", "CL=F"), "gold": ("Gold", "GC=F"),
    "dxy": ("Dollar index", "DX-Y.NYB"), "inr": ("USD/INR", "INR=X"),
}
FEATURES = list(MARKETS) + ["nprev", "vix"]
LABELS = {**{k: v[0] for k, v in MARKETS.items()}, "nprev": "Nifty previous day", "vix": "India VIX level"}
WINDOW, LAM, TEST, BLOCK = 400, 5.0, 300, 20


def _hourly(ticker):
    df = yf.download(ticker, period="730d", interval="1h", auto_adjust=False, progress=False)
    if df is None or df.empty:
        return None
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(0)
    idx = pd.to_datetime(df.index)
    df.index = idx.tz_localize("UTC") if idx.tz is None else idx.tz_convert("UTC")
    return df.dropna(subset=["Close"])


def _daily(ticker):
    df = yf.download(ticker, period="3y", interval="1d", auto_adjust=False, progress=False)
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(0)
    df.index = pd.to_datetime(df.index)
    if df.index.tz is not None:
        df.index = df.index.tz_localize(None)
    return df.dropna(subset=["Close"])   # at night Yahoo can add a row for the day in progress with no close


def load_data():
    cached = store.cache_get("premodel:data")
    if cached is not None:
        return cached
    H = {k: _hourly(t) for k, (_, t) in MARKETS.items()}
    H = {k: v for k, v in H.items() if v is not None}
    D = {"nifty": _daily("^NSEI"), "vix": _daily("^INDIAVIX")}
    store.cache_set("premodel:data", (H, D), 1800)
    return H, D


def _px(df, ts):
    s = df["Close"][df.index <= ts]
    return float(s.iloc[-1]) if len(s) else np.nan


def _row(H, D, session, offset_h, prev_close_date, nprev, vix):
    """Moves since Nifty's last close (prev session 15:30 IST = 10:00 UTC) up to the snapshot."""
    t_close = pd.Timestamp(prev_close_date).tz_localize("UTC") + pd.Timedelta(hours=10)
    t_snap = pd.Timestamp(session).tz_localize("UTC") + pd.Timedelta(hours=offset_h)
    r = {"nprev": nprev, "vix": vix}
    for k in MARKETS:
        if k not in H:
            r[k] = 0.0
            continue
        a, b = _px(H[k], t_close), _px(H[k], t_snap)
        r[k] = (b / a - 1) * 100 if a and np.isfinite(a) and np.isfinite(b) else 0.0
    return r


def build_frame(H, D, offset_h):
    n, v = D["nifty"], D["vix"]["Close"]
    start = min(df.index[0] for df in H.values()).tz_localize(None) + pd.Timedelta(days=7)
    rows = []
    for j in range(2, len(n)):
        d = n.index[j]
        if d < start:
            continue
        vv = v[v.index < d]
        r = _row(H, D, d.date(), offset_h, n.index[j - 1].date(),
                 (n.Close.iloc[j - 1] / n.Close.iloc[j - 2] - 1) * 100, float(vv.iloc[-1]) if len(vv) else 15.0)
        r.update(date=d, gap=(n.Open.iloc[j] / n.Close.iloc[j - 1] - 1) * 100, ret=(n.Close.iloc[j] / n.Close.iloc[j - 1] - 1) * 100)
        rows.append(r)
    return pd.DataFrame(rows).set_index("date").replace([np.inf, -np.inf], np.nan).dropna()


def _fit(X, y):
    mu, sd = X.mean(0), X.std(0) + 1e-9
    Z = (X - mu) / sd
    w = np.linalg.solve(Z.T @ Z + LAM * np.eye(Z.shape[1]), Z.T @ (y - y.mean()))
    res = y - (Z @ w + y.mean())
    return {"mu": mu, "sd": sd, "w": w, "b": float(y.mean()), "resid_sd": float(res.std() + 1e-9)}


def _pred(m, X):
    return ((X - m["mu"]) / m["sd"]) @ m["w"] + m["b"]


def walk_forward(F, target, test=TEST):
    out = []
    for s in range(max(150, len(F) - test), len(F), BLOCK):
        tr = F.iloc[max(0, s - WINDOW):s]
        m = _fit(tr[FEATURES].values, tr[target].values)
        blk = F.iloc[s:s + BLOCK]
        p = _pred(m, blk[FEATURES].values)
        for ts, pi, ai, vi in zip(blk.index, p, blk[target].values, blk["vix"].values):
            out.append({"date": ts, "pred": float(pi), "actual": float(ai), "z": float(pi / m["resid_sd"]), "vix": float(vi)})
    return pd.DataFrame(out).set_index("date") if out else pd.DataFrame()


def snapshot_offset(session, now=None):
    """Hours from session-date 00:00 UTC to now, clamped to [prev close, open]."""
    now = now or datetime.now(timezone.utc)
    base = datetime(session.year, session.month, session.day, tzinfo=timezone.utc)
    h = (now - base).total_seconds() / 3600
    return float(np.clip(h, -14.0, 3.5))  # 3.5h UTC = 09:00 IST


def predict(session, params, now=None):
    H, D = load_data()
    if "es" not in H:
        raise RuntimeError("global hourly data unavailable")
    off = snapshot_offset(session, now)
    F = build_frame(H, D, off)
    n = D["nifty"]
    prior = n[n.index < pd.Timestamp(session)]
    vv = D["vix"]["Close"][D["vix"].index < pd.Timestamp(session)]
    live = _row(H, D, session, off, prior.index[-1].date(),
                (prior.Close.iloc[-1] / prior.Close.iloc[-2] - 1) * 100, float(vv.iloc[-1]))
    live = {k: (float(v) if np.isfinite(v) else 0.0) for k, v in live.items()}   # a missing input counts as "no move"
    x = np.array([[live[f] for f in FEATURES]])
    tr = F.iloc[-WINDOW:]
    m_gap, m_ret = _fit(tr[FEATURES].values, tr["gap"].values), _fit(tr[FEATURES].values, tr["ret"].values)
    gap, ret = float(_pred(m_gap, x)[0]), float(_pred(m_ret, x)[0])
    z = ret / m_ret["resid_sd"]
    wf_g, wf_r = walk_forward(F, "gap"), walk_forward(F, "ret")
    thr = params["bias_threshold"]
    calls = wf_r[wf_r.z.abs() >= thr]
    contrib = ((x[0] - m_ret["mu"]) / m_ret["sd"]) * m_ret["w"]
    snap_ist = min((datetime(session.year, session.month, session.day, tzinfo=timezone.utc) + timedelta(hours=off)),
                   now or datetime.now(timezone.utc)).astimezone(IST)   # never label a time in the future
    return {
        "snapshot_ist": snap_ist.strftime("%a %d %b %H:%M IST"),
        "stage": "final" if off >= 1.0 else "preview",     # after ~06:30 IST US close + Asia open are in
        "expected_gap_pct": round(gap, 2),
        "gap_typical_error_pct": round(float((wf_g.pred - wf_g.actual).abs().mean()), 2) if len(wf_g) else None,
        "expected_day_pct": round(ret, 2),
        "z": round(float(z), 3),
        "typical_daily_move_pct": round(m_ret["resid_sd"], 3),
        "markets": [{"key": k, "name": LABELS[k], "move_pct": round(live[k], 2)} for k in MARKETS],
        "drivers": sorted([{"feature": f, "label": LABELS[f], "value": round(float(live[f]), 2),
                            "contribution_pct": round(float(c), 3)} for f, c in zip(FEATURES, contrib)],
                          key=lambda r: -abs(r["contribution_pct"]))[:6],
        "wf": {
            "oos_days": int(len(wf_r)),
            "gap_corr": round(float(np.corrcoef(wf_g.pred, wf_g.actual)[0, 1]), 2) if len(wf_g) > 5 else None,
            "gap_sign_pct": round(100 * float((np.sign(wf_g.pred) == np.sign(wf_g.actual)).mean()), 1) if len(wf_g) else None,
            "directional_calls": int(len(calls)),
            "directional_accuracy": round(100 * float((np.sign(calls.pred) == np.sign(calls.actual)).mean()), 1) if len(calls) else None,
        },
        "vix": round(live["vix"], 2),
        "_wf_ret": wf_r,
    }


def history_z(params, test=600):
    """Out-of-sample z per session at the 07:30 IST snapshot (for the options lab's labels)."""
    H, D = load_data()
    F = build_frame(H, D, 2.0)
    return walk_forward(F, "ret", test=test)
