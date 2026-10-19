"""Technical indicators, implemented on plain pandas.

pandas-ta is effectively unmaintained and breaks on NumPy 2.x, so the handful
of indicators we need are computed here directly (Wilder RSI/ATR, EMA, pivots).
"""
import numpy as np
import pandas as pd

# Model feature order. Shared by the live screener and the backtest bootstrap.
FEATURES = [
    "log_vol_ratio", "rsi", "dist_high_pct", "ext_ema20", "atr_pct",
    "day_chg", "ret5", "rs20", "bias_score", "news_sent", "mkt_above50", "mkt_ret20",
]


def ema(s, n):
    return s.ewm(span=n, adjust=False).mean()


def rsi(close, n=14):
    d = close.diff()
    up = d.clip(lower=0).ewm(alpha=1 / n, adjust=False).mean()
    dn = (-d.clip(upper=0)).ewm(alpha=1 / n, adjust=False).mean()
    rs = up / dn.replace(0, np.nan)
    return (100 - 100 / (1 + rs)).fillna(100.0)


def atr(df, n=14):
    pc = df["Close"].shift()
    tr = pd.concat([df["High"] - df["Low"], (df["High"] - pc).abs(), (df["Low"] - pc).abs()], axis=1).max(axis=1)
    return tr.ewm(alpha=1 / n, adjust=False).mean()


def pivots(h, l, c):
    p = (h + l + c) / 3
    return {"P": p, "R1": 2 * p - l, "S1": 2 * p - h, "R2": p + (h - l), "S2": p - (h - l)}


def enrich(df, index_close=None):
    """Add every column the screener, levels and model need."""
    e = df.copy()
    c = e["Close"]
    e["ema20"] = ema(c, 20)
    e["ema50"] = ema(c, 50)
    e["rsi"] = rsi(c)
    e["atr"] = atr(e)
    e["vol_avg20"] = e["Volume"].shift(1).rolling(20).mean()
    e["vol_ratio"] = e["Volume"] / e["vol_avg20"]
    e["high20"] = e["High"].rolling(20).max()
    e["dist_high_pct"] = (e["high20"] - c) / e["high20"] * 100
    e["day_chg"] = c.pct_change() * 100
    e["ret5"] = c.pct_change(5) * 100
    e["atr_pct"] = e["atr"] / c * 100
    e["ext_ema20"] = (c / e["ema20"] - 1) * 100
    tp = (e["High"] + e["Low"] + c) / 3
    e["vwap5"] = (tp * e["Volume"]).rolling(5).sum() / e["Volume"].rolling(5).sum()
    if index_close is not None:
        idx = index_close.reindex(e.index).ffill()
        e["rs20"] = (c.pct_change(20) - idx.pct_change(20)) * 100
        e["mkt_above50"] = (idx > ema(idx, 50)).astype(float)
        e["mkt_ret20"] = idx.pct_change(20) * 100
    else:
        e["rs20"], e["mkt_above50"], e["mkt_ret20"] = 0.0, 1.0, 0.0
    return e


def feature_vector(row, vol_ratio=None, bias_score=0.0, news_sent=0.0):
    vr = float(vol_ratio if vol_ratio is not None else row["vol_ratio"])
    return {
        "log_vol_ratio": float(np.log(max(vr, 0.01))),
        "rsi": float(row["rsi"]),
        "dist_high_pct": float(row["dist_high_pct"]),
        "ext_ema20": float(row["ext_ema20"]),
        "atr_pct": float(row["atr_pct"]),
        "day_chg": float(row["day_chg"]),
        "ret5": float(row["ret5"]),
        "rs20": float(row["rs20"]) if pd.notna(row["rs20"]) else 0.0,
        "bias_score": float(bias_score),
        "news_sent": float(news_sent),
        "mkt_above50": float(row["mkt_above50"]),
        "mkt_ret20": float(row["mkt_ret20"]) if pd.notna(row["mkt_ret20"]) else 0.0,
    }


def regime_ok(f):
    return f.get("mkt_above50", 1.0) > 0 and f.get("mkt_ret20", 0.0) > 0


def trade_levels(row, params, direction=1):
    """Entry / stop / targets. Targets are the further of the pivot level and the ATR target."""
    entry, a = float(row["Close"]), float(row["atr"])
    pv = pivots(float(row["High"]), float(row["Low"]), entry)
    if direction == 1:
        stop = entry - params["sl_atr"] * a
        t1 = max(pv["R1"], entry + params["t1_atr"] * a)
        t2 = max(pv["R2"], entry + params["t2_atr"] * a, t1 + 0.25 * a)
    else:
        stop = entry + params["sl_atr"] * a
        t1 = min(pv["S1"], entry - params["t1_atr"] * a)
        t2 = min(pv["S2"], entry - params["t2_atr"] * a, t1 - 0.25 * a)
    return {
        "entry": entry, "stop": stop, "t1": t1, "t2": t2, "atr": a,
        "pivots": {k: round(v, 2) for k, v in pv.items()},
        # pivot distances in ATR units, used when the learner re-simulates exits
        "r1_a": (pv["R1"] - entry) / a if a else 0.0,
        "r2_a": (pv["R2"] - entry) / a if a else 0.0,
    }
