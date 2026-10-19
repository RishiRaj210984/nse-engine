"""Options lab: re-learns every night which condor setups actually make money.

Replays the plan's Nifty iron condor on ~2 years of hourly data, held to expiry
(enter 10:15 IST after the first hour, short strikes ±0.9× expected move, wings
0.5× EM, exit at 45% of credit kept or at expiry), after costs and slippage.
Results are grouped by days-to-expiry and by how much the first hour moved, so
the morning plan can say "this setup won X% of N times, avg ₹Y per lot" and
skip setups that have lost money. Premiums are Black-Scholes with India VIX,
so these are estimates, not broker fills.

Also builds an event calendar (results dates of Nifty heavyweights).
"""
import logging
from datetime import date, datetime, timedelta

import numpy as np
import pandas as pd
import yfinance as yf

import store
from config import INDEX_SPECS, IST
from options import bs_price

log = logging.getLogger(__name__)
LOT, STEP = INDEX_SPECS["NIFTY"]["lot"], INDEX_SPECS["NIFTY"]["step"]
COST_PER_TRADE = 140 + 260          # brokerage/charges + slippage on 4 legs, ₹ per lot
MIN_TRADES, MIN_WIN, MIN_AVG = 25, 0.70, 0.0
HEAVYWEIGHTS = {"HDFCBANK": "HDFC Bank", "RELIANCE": "Reliance", "ICICIBANK": "ICICI Bank", "INFY": "Infosys",
                "BHARTIARTL": "Bharti Airtel", "LT": "L&T", "ITC": "ITC", "TCS": "TCS", "AXISBANK": "Axis Bank",
                "SBIN": "SBI", "KOTAKBANK": "Kotak Bank"}


def _hourly(ticker):
    df = yf.download(ticker, period="730d", interval="1h", auto_adjust=False, progress=False)
    if df is None or df.empty:
        return None
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(0)
    idx = pd.to_datetime(df.index)
    idx = (idx.tz_localize("UTC") if idx.tz is None else idx).tz_convert(IST).tz_localize(None)
    df.index = idx
    return df.dropna(subset=["Close"])


def expiry_weekday(d):
    """Nifty weekly expiry: Thursday until Aug-2025, Tuesday after."""
    return 1 if d >= date(2025, 9, 1) else 3


def dte_of(d):
    return (expiry_weekday(d) - d.weekday()) % 7


def _value(legs, S, T, iv):
    return sum((1 if a == "B" else -1) * bs_price(S, k, T, iv, t) for a, k, t in legs)


def simulate(nh, vix_h, vix_d):
    out = []
    days = sorted(set(nh.index.date))
    for d in days:
        day = nh[nh.index.date == d]
        prev = nh[nh.index.date < d]
        vv = vix_d[vix_d.index.date < d]
        if len(day) < 5 or not len(prev) or not len(vv):
            continue
        pc, iv0 = float(prev["Close"].iloc[-1]), float(vv.iloc[-1]) / 100
        dte = dte_of(d)
        exp_close = datetime.combine(d + timedelta(days=dte), datetime.min.time()) + timedelta(hours=15, minutes=30)
        em = pc * iv0 * np.sqrt((dte + 0.75) / 365)
        wing = max(2 * STEP, round(0.5 * em / STEP) * STEP)
        sc, sp = round((pc + 0.9 * em) / STEP) * STEP, round((pc - 0.9 * em) / STEP) * STEP
        legs = [("S", sc, "CE"), ("B", sc + wing, "CE"), ("S", sp, "PE"), ("B", sp - wing, "PE")]
        o, S0 = float(day["Open"].iloc[0]), float(day["Close"].iloc[0])
        if not (sp < S0 < sc):
            continue
        T = lambda ts: max((exp_close - ts).total_seconds() / 86400, 0.002) / 365

        def iv(ts):
            s = vix_h["Close"][vix_h.index <= ts] if vix_h is not None else []
            return float(s.iloc[-1]) / 100 if len(s) else iv0

        t0 = day.index[0] + timedelta(hours=1)
        v0 = _value(legs, S0, T(t0), iv(day.index[0]))
        credit = -v0
        path = nh[(nh.index > day.index[0]) & (nh.index < exp_close)]
        pnl = None
        for ts, b in path.iterrows():
            v = _value(legs, float(b["Close"]), T(ts + timedelta(hours=1)), iv(ts))
            if -v <= 0.45 * credit:
                pnl = v - v0
                break
        if pnl is None:
            if not len(path) or exp_close > path.index[-1] + timedelta(hours=2):
                continue  # trade not finished yet (expiry still ahead)
            pnl = _value(legs, float(path["Close"].iloc[-1]), 0, iv0) - v0
        out.append({"date": d.isoformat(), "dte": dte, "first_hr": round((S0 / o - 1) * 100, 3),
                    "credit": round(credit * LOT), "pnl": round(pnl * LOT - COST_PER_TRADE)})
    return pd.DataFrame(out)


def _group(R):
    if not len(R):
        return None
    return {"trades": int(len(R)), "win_pct": round(100 * float((R.pnl > 0).mean()), 1),
            "avg_pnl": int(R.pnl.mean()), "worst": int(R.pnl.min()), "best": int(R.pnl.max())}


def run():
    nh = _hourly("^NSEI")
    vh = _hourly("^INDIAVIX")
    vd = yf.download("^INDIAVIX", period="3y", interval="1d", progress=False)
    if nh is None or vd is None or vd.empty:
        raise RuntimeError("hourly data unavailable")
    if isinstance(vd.columns, pd.MultiIndex):
        vd.columns = vd.columns.get_level_values(0)
    vd.index = pd.to_datetime(vd.index).tz_localize(None) if pd.to_datetime(vd.index).tz is not None else pd.to_datetime(vd.index)
    R = simulate(nh, vh, vd["Close"])
    regime = R[pd.to_datetime(R.date) >= pd.Timestamp("2025-09-01")] if len(R) else R
    by_dte = {}
    for k in sorted(R.dte.unique()):
        s_all, s_reg = R[R.dte == k], regime[regime.dte == k]
        g = _group(s_reg) if len(s_reg) >= MIN_TRADES else _group(s_all)
        q = s_reg if len(s_reg) >= MIN_TRADES else s_all
        g["quiet_first_hour"] = _group(q[q.first_hr.abs() < 0.25])
        g["trending_first_hour"] = _group(q[q.first_hr.abs() >= 0.25])
        g["allowed"] = bool(g["trades"] >= MIN_TRADES and g["win_pct"] >= 100 * MIN_WIN and g["avg_pnl"] > MIN_AVG)
        g["sample"] = "Tuesday-expiry era" if len(s_reg) >= MIN_TRADES else "full 2 years"
        by_dte[int(k)] = g
    lab = {"generated_at": store.now_ist().isoformat(), "rule": "Nifty iron condor, enter 10:15 IST, ±0.9×EM short strikes, "
           "0.5×EM wings, exit at 45% of credit or expiry", "costs_per_lot": COST_PER_TRADE,
           "overall": _group(R), "by_dte": by_dte}
    store._write(store.state_path("options_lab.json"), lab, indent=1)
    return lab


def load():
    return store._read(store.state_path("options_lab.json"), None)


def setup_verdict(session, lab=None, events=None):
    """TRADE / SKIP for the condor on a given session, with the evidence."""
    lab = lab or load()
    d = date.fromisoformat(session)
    dte = dte_of(d)
    if not lab:
        return {"decision": "CAUTION", "dte": dte, "reason": "Options lab has not run yet.", "stats": None}
    g = lab["by_dte"].get(str(dte)) or lab["by_dte"].get(dte)
    recent_results = [e for e in (events or []) if e.get("affects") == session]
    if g is None:
        return {"decision": "CAUTION", "dte": dte, "reason": "No history for this day.", "stats": None}
    if not g["allowed"]:
        why = f"Historically this setup lost: {g['win_pct']}% wins, avg ₹{g['avg_pnl']:,} per lot over {g['trades']} trades."
        return {"decision": "SKIP", "dte": dte, "reason": why, "stats": g}
    if dte <= 1 and recent_results:
        names = ", ".join(e["name"] for e in recent_results)
        return {"decision": "SKIP", "dte": dte, "stats": g,
                "reason": f"{dte} day(s) to expiry right after {names} results: condors broke 60% of the time in this combination (small sample)."}
    q, t = g.get("quiet_first_hour"), g.get("trending_first_hour")
    tip = ""
    if q and t and q["win_pct"] - t["win_pct"] >= 5 and t["avg_pnl"] < q["avg_pnl"]:
        tip = (f" If the first hour moves more than 0.25%, the win rate drops to {t['win_pct']}% "
               f"(from {q['win_pct']}% on quiet mornings): consider skipping.")
    return {"decision": "TRADE", "dte": dte, "stats": g,
            "reason": f"This setup won {g['win_pct']}% of {g['trades']} trades, avg ₹{g['avg_pnl']:,} per lot after costs (worst ₹{g['worst']:,})." + tip}


def events_calendar(days_back=4, days_ahead=8):
    """Results dates of Nifty heavyweights around now, and which session each affects."""
    now = datetime.now(IST)
    out = []
    for sym, name in HEAVYWEIGHTS.items():
        try:
            df = yf.Ticker(sym + ".NS").get_earnings_dates(limit=8)
        except Exception:
            continue
        if df is None or df.empty:
            continue
        for t in pd.to_datetime(df.index):
            t = t.tz_convert(IST) if t.tz is not None else t.tz_localize(IST)
            if not (now - timedelta(days=days_back) <= t <= now + timedelta(days=days_ahead)):
                continue
            after = t.hour >= 15 or t.weekday() >= 5 or t.hour == 0
            aff = t.date() + timedelta(days=1) if after else t.date()
            while aff.weekday() >= 5:
                aff += timedelta(days=1)
            out.append({"symbol": sym, "name": name, "when": t.strftime("%a %d %b"), "date": t.date().isoformat(),
                        "affects": aff.isoformat(), "past": t < now})
    return sorted(out, key=lambda e: e["date"])
