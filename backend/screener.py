"""Module B: high-volume 1-2 day momentum screener for NSE stocks."""
from datetime import datetime

import pandas as pd

import data
import learning
import macro
import news
import store
from config import IST, JOURNAL_GATE, UNIVERSE
from indicators import enrich, feature_vector, regime_ok, trade_levels


def checks_for(close, ema20, ema50, rsi, dist, vol_ratio, p):
    return {
        "volume": vol_ratio >= p["vol_mult"],
        "momentum": p["rsi_lo"] <= rsi <= p["rsi_hi"],
        "trend": close > ema20 and close > ema50,
        "breakout": dist <= p["near_high_pct"],
    }


def in_journal_gate(close, ema20, ema50, rsi, dist, vol_ratio, g=JOURNAL_GATE):
    return (close > ema20 and close > ema50 and vol_ratio >= g["vol_mult"]
            and g["rsi_lo"] <= rsi <= g["rsi_hi"] and dist <= g["near_high_pct"])


def grade(win_prob, expected_r, model_used):
    """A/B/C so the user can decide at a glance."""
    if expected_r >= 0.35 and win_prob >= 55:
        return "A"
    if expected_r >= 0.1:
        return "B"
    return "C"


def action_text(x, qty):
    return (f"BUY {x['symbol']} near ₹{x['entry']:,.2f} (skip if it opens above ₹{x['t1']:,.2f}) · "
            f"SL ₹{x['stop']:,.2f} · T1 ₹{x['t1']:,.2f} · T2 ₹{x['t2']:,.2f}"
            + (f" · Qty {qty} for ₹10k @1% risk" if qty else ""))


def scan_full(with_news=True, news_top=10):
    """Returns (public_result, journal_rows)."""
    state = store.latest_state()
    p = state["params"]
    hist = data.history([s + ".NS" for s in UNIVERSE] + ["^NSEI"], period="1y")
    nifty = hist.get("^NSEI")
    nclose = nifty["Close"] if nifty is not None else None
    try:
        bias = macro.global_bias()["bias"]
    except Exception:
        bias = {"score": 0, "label": "UNKNOWN"}

    now = datetime.now(IST)
    live = data.market_open(now)
    frac = data.session_fraction(now)
    rows = []
    for sym, name in UNIVERSE.items():
        df = hist.get(sym + ".NS")
        if df is None or len(df) < 60:
            continue
        e = enrich(df, nclose)
        r = e.iloc[-1]
        if pd.isna(r["vol_avg20"]) or pd.isna(r["atr"]) or r["atr"] <= 0:
            continue
        vr, projected = float(r["vol_ratio"]), False
        if live and e.index[-1].date() == now.date():
            vr, projected = float(r["Volume"] / max(frac, 0.1) / r["vol_avg20"]), True
        args = (float(r["Close"]), float(r["ema20"]), float(r["ema50"]), float(r["rsi"]), float(r["dist_high_pct"]), vr)
        chk = checks_for(*args, p)
        n = sum(chk.values())
        if n < 3 and not in_journal_gate(*args):
            continue
        lv = trade_levels(r, p)
        risk = lv["entry"] - lv["stop"]
        rows.append({
            "symbol": sym, "name": name, "bar_date": e.index[-1].date().isoformat(),
            "cmp": round(float(r["Close"]), 2), "day_chg": round(float(r["day_chg"]), 2),
            "vol_ratio": round(vr, 2), "vol_badge": f"{vr:.1f}x Vol Surge", "volume_projected": projected,
            "rsi": round(float(r["rsi"]), 1), "ema20": round(float(r["ema20"]), 2),
            "ema50": round(float(r["ema50"]), 2), "high20": round(float(r["high20"]), 2),
            "dist_high_pct": round(float(r["dist_high_pct"]), 2), "atr": round(lv["atr"], 2),
            "checks": chk, "checks_passed": n, "passed": n == 4,
            "entry": round(lv["entry"], 2), "stop": round(lv["stop"], 2),
            "t1": round(lv["t1"], 2), "t2": round(lv["t2"], 2), "pivots": lv["pivots"],
            "risk_per_share": round(risk, 2),
            "rr_t1": round((lv["t1"] - lv["entry"]) / risk, 2), "rr_t2": round((lv["t2"] - lv["entry"]) / risk, 2),
            "trail_rule": "After T1: book half, move SL to entry, trail 0.5×ATR below highest close.",
            "features": feature_vector(r, vr, bias["score"], 0.0),
            "r1_a": lv["r1_a"], "r2_a": lv["r2_a"],
            "news": [], "news_sentiment": None,
        })

    rows.sort(key=lambda x: (-x["checks_passed"], -x["vol_ratio"]))
    if with_news:
        for x in rows[:news_top]:
            nn = news.ticker_news(x["symbol"], x["name"])
            x["news"] = nn["items"][:5]
            x["news_sentiment"] = nn["summary"]
            x["features"]["news_sent"] = nn["summary"]["score"]

    for x in rows:
        pr = learning.predict(x["features"], state)
        x.update(pr)
        x["grade"] = grade(pr["win_prob"], pr["expected_r"], pr["model_used"])
        x["skip_reason"] = None
        if p.get("regime_gate") and not regime_ok(x["features"]):
            x["grade"], x["skip_reason"] = "C", "Market gate closed: Nifty below 50-DMA or down over 20 days"
        elif x["day_chg"] > p.get("max_day_chg", 99):
            x["grade"], x["skip_reason"] = "C", f"Already up {x['day_chg']}% today: reversal risk"
        elif x["news_sentiment"] and x["news_sentiment"]["score"] < -0.25:
            x["grade"], x["skip_reason"] = "C", "Negative news flow"
        qty = int(min(10000 * 0.01 / x["risk_per_share"], 10000 / x["entry"])) if x["risk_per_share"] > 0 else 0
        x["action"] = action_text(x, qty)

    rows.sort(key=lambda x: (not x["passed"], x["grade"], -x["expected_r"]))
    public = {
        "generated_at": now.isoformat(),
        "market_open": live,
        "volume_projected": live,
        "bias": {"label": bias.get("label"), "score": bias.get("score")},
        "rules": {k: p[k] for k in ("vol_mult", "rsi_lo", "rsi_hi", "near_high_pct", "sl_atr", "t1_atr",
                                    "t2_atr", "max_day_chg", "regime_gate")},
        "market_gate_open": bool(rows and regime_ok(rows[0]["features"])) if rows else None,
        "model": learning.model_info(state),
        "candidates": [x for x in rows if x["passed"]],
        "near_misses": [x for x in rows if not x["passed"] and x["checks_passed"] == 3][:15],
        "scanned": len(UNIVERSE),
    }
    return public, rows


def scan(with_news=True):
    cached = store.cache_get("scan:public")
    if cached is not None:
        return cached
    public, _ = scan_full(with_news)
    store.cache_set("scan:public", public, data.default_ttl())
    return public
