"""Plan vs actual: grade each day's published plan against what the market did.

The morning plan is archived (state/plans/<date>.json). After the close the
review replays the day on 5-minute bars, following the plan's own rules
(entry window, entry condition, stop, target, time exit), and estimates P&L.
Option values are Black-Scholes re-pricings with India VIX, so option P&L is an
ESTIMATE, not a broker fill. Results go to state/reviews.json and docs/review.json.
"""
import json
import os
from datetime import date, datetime, time, timedelta

import pandas as pd
import yfinance as yf

import store
from config import INDEX_SPECS, IST, STATE_DIR, UNIVERSE
from options import bs_price

PLANS_DIR = os.path.join(STATE_DIR, "plans")


# ------------------------------------------------------------------ archive
def archive_plan(bias, plan, scan):
    os.makedirs(PLANS_DIR, exist_ok=True)
    keep_bias = {k: bias[k] for k in ("generated_at", "target_session", "reference_date", "bias", "options", "model")}
    stocks = [{k: c[k] for k in ("symbol", "name", "grade", "passed", "entry", "stop", "t1", "t2", "atr", "skip_reason")}
              for c in (scan["candidates"] + scan["near_misses"])[:12]]
    path = os.path.join(PLANS_DIR, f"{bias['target_session']}.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump({"bias": keep_bias, "plan": plan, "stocks": stocks}, f, indent=1, default=str)
    return path


def load_archived(session):
    try:
        with open(os.path.join(PLANS_DIR, f"{session}.json"), encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        return None


# ------------------------------------------------------------------ data
def intraday(ticker, session):
    """5-minute bars for one session in IST."""
    try:
        df = yf.download(ticker, period="5d", interval="5m", auto_adjust=False, progress=False)
    except Exception:
        return None
    if df is None or df.empty:
        return None
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(0)
    idx = pd.to_datetime(df.index)
    idx = idx.tz_localize("UTC") if idx.tz is None else idx
    df.index = idx.tz_convert(IST)
    day = df[df.index.date == session]
    return day if len(day) else None


def _at(df, hhmm):
    """Close of the last bar starting at or before hh:mm."""
    t = time(*map(int, hhmm.split(":")))
    sub = df[df.index.time <= t]
    return (sub.index[-1], float(sub["Close"].iloc[-1])) if len(sub) else (None, None)


# ------------------------------------------------------------------ options replay
def _value(legs, spot, T, iv):
    """Value of holding the position per unit (BUY +, SELL -)."""
    return sum((1 if l["action"] == "BUY" else -1) * bs_price(spot, l["strike"], T, iv, l["type"]) for l in legs)


def replay_option(name, o, session, bars, vix_bars, now_ist):
    s = o["strategies"][0]
    legs, lot = s["legs"], o["lot_size"]
    lv, pv = o["levels"], o["levels"]["pivots"]
    expiry = date.fromisoformat(o["expiry"])
    iv0 = o["iv_used_pct"] / 100
    vix0 = None
    kind = s["name"]
    credit_spread = len(legs) == 2 and legs[0]["action"] == "SELL"
    condor = "Condor" in kind or "Fly" in kind or credit_spread
    entry_hhmm = "10:15" if condor else "09:45"
    res = {"index": name, "strategy": kind, "legs": " + ".join(f"{l['action']} {l['strike']} {l['type']}" for l in legs),
           "lot_size": lot, "planned_max_reward": s["max_reward"], "planned_max_loss": s["max_loss"],
           "day_open": round(float(bars["Open"].iloc[0]), 2), "day_high": round(float(bars["High"].max()), 2),
           "day_low": round(float(bars["Low"].min()), 2), "last": round(float(bars["Close"].iloc[-1]), 2)}

    def iv_at(ts):
        if vix_bars is None or ts is None:
            return iv0
        sub = vix_bars[vix_bars.index <= ts]
        if not len(sub):
            return iv0
        nonlocal vix0
        v = float(sub["Close"].iloc[-1])
        vix0 = vix0 or float(vix_bars["Close"].iloc[0])
        return iv0 * v / vix0 if vix0 else iv0

    def T_at(ts):
        mins_left = max((datetime.combine(expiry, time(15, 30), IST) - ts).total_seconds() / 60, 5)
        return mins_left / (60 * 24 * 365)

    ts_e, spot_e = _at(bars, entry_hhmm)
    if ts_e is None:
        return {**res, "status": "NO DATA YET", "pnl_per_lot": 0}

    # entry condition, as written in the plan
    if condor:
        if "Fly" in kind:
            ok = abs(float(bars["Open"].iloc[0]) - o["atm"]) <= INDEX_SPECS[name]["step"]
            cond = f"open within ±{INDEX_SPECS[name]['step']} of {o['atm']}"
        shorts = sorted(l["strike"] for l in legs if l["action"] == "SELL")
        lo_stop, hi_stop = shorts[0], shorts[-1]
        if credit_spread:  # one-sided: only the short strike matters
            lo_stop, hi_stop = (shorts[0], float("inf")) if legs[0]["type"] == "PE" else (0.0, shorts[0])
        if "Fly" not in kind:
            ok = lo_stop < spot_e < hi_stop
            cond = (f"{name} between {lo_stop:,} and {hi_stop:,} at {entry_hhmm}" if not credit_spread else
                    f"{name} {'above' if legs[0]['type'] == 'PE' else 'below'} {shorts[0]:,} at {entry_hhmm}")
        if o.get("setup", {}).get("decision") == "SKIP":
            ok, cond = False, f"plan said SKIP: {o['setup']['reason']}"
    else:
        bull = legs[0]["type"] == "CE"
        ok = spot_e >= lv["support"] if bull else spot_e <= lv["resistance"]
        cond = f"{name} {'above support ' + format(lv['support'], ',.0f') if bull else 'below resistance ' + format(lv['resistance'], ',.0f')} at {entry_hhmm}"
        stop_lvl = (pv["S1"] if lv["support"] > pv["S1"] else pv["S2"]) if bull else (pv["R1"] if lv["resistance"] < pv["R1"] else pv["R2"])
    res.update(entry_condition=cond, entry_spot=round(spot_e, 2), entry_time=ts_e.strftime("%H:%M"),
               opening_move_pct=round((spot_e / float(bars["Open"].iloc[0]) - 1) * 100, 2))
    if not ok:
        return {**res, "status": "NO ENTRY", "pnl_per_lot": 0,
                "verdict": "Entry condition not met, so no trade. The plan's filter kept you out."}

    v_e = _value(legs, spot_e, T_at(ts_e), iv_at(ts_e))
    credit = -v_e  # >0 for credit strategies
    exit_hhmm = "15:00" if len(legs) == 1 else "15:15"
    after = bars[bars.index > ts_e].resample("15min", label="right", closed="right").last().dropna()
    exit_ts, exit_spot, reason = None, None, None
    for ts, row in after.iterrows():
        sp = float(row["Close"])
        v = _value(legs, sp, T_at(ts), iv_at(ts))
        if condor:
            if (v - v_e) <= -credit:  # loss reached 1x credit
                exit_ts, exit_spot, reason = ts, sp, "STOP: loss reached 1x credit"
            elif -v <= 0.45 * credit:
                exit_ts, exit_spot, reason = ts, sp, "TARGET: 55% of credit kept"
        elif len(legs) == 2:  # debit spread
            width = abs(legs[1]["strike"] - legs[0]["strike"])
            if (bull and sp < stop_lvl) or (not bull and sp > stop_lvl) or v <= 0.5 * v_e:
                exit_ts, exit_spot, reason = ts, sp, "STOP"
            elif v >= v_e + 0.6 * (width - v_e):
                exit_ts, exit_spot, reason = ts, sp, "TARGET"
        else:  # single option
            if v <= 0.7 * v_e or (bull and sp < stop_lvl) or (not bull and sp > stop_lvl):
                exit_ts, exit_spot, reason = ts, sp, "STOP"
        if reason:
            break
        if ts.time() >= time(*map(int, exit_hhmm.split(":"))):
            exit_ts, exit_spot, reason = ts, sp, f"TIME: marked at {exit_hhmm}"
            break
    if reason is None:
        exit_ts = bars.index[-1]
        exit_spot = float(bars["Close"].iloc[-1])
        live = now_ist.date() == session and now_ist.time() < time(15, 30)
        reason = "OPEN: still running (live mark)" if live else "OPEN: marked at last price"
    v_x = _value(legs, exit_spot, T_at(exit_ts), iv_at(exit_ts))
    pnl = round((v_x - v_e) * lot)
    mx = s["max_reward"] or 0
    res.update(status=reason.split(":")[0], exit_reason=reason, exit_time=exit_ts.strftime("%H:%M"),
               exit_spot=round(exit_spot, 2), entry_value=round(v_e, 1), exit_value=round(v_x, 1),
               pnl_per_lot=pnl,
               capture_pct=round(100 * pnl / mx, 1) if mx else None)
    if condor:
        inside = res["day_low"] > lo_stop and res["day_high"] < hi_stop
        res["range_held"] = inside
        shown = " / ".join(f"{x:,.0f}" for x in (lo_stop, hi_stop) if 0 < x < float("inf"))
        res["verdict"] = (f"Short strike {shown} held (day {res['day_low']:,.0f}–{res['day_high']:,.0f}). {reason}."
                          if inside else
                          f"Short strike {shown} was crossed: day {res['day_low']:,.0f}–{res['day_high']:,.0f}. {reason}.")
    else:
        res["verdict"] = f"{reason}. Est. ₹{pnl:,} per lot."
    return res


# ------------------------------------------------------------------ stocks replay
def replay_stock(s, bars):
    o = float(bars["Open"].iloc[0])
    out = {"symbol": s["symbol"], "grade": s["grade"], "entry": s["entry"], "stop": s["stop"], "t1": s["t1"],
           "open": round(o, 2), "high": round(float(bars["High"].max()), 2), "low": round(float(bars["Low"].min()), 2),
           "last": round(float(bars["Close"].iloc[-1]), 2), "tradeable": s["grade"] in ("A", "B")}
    if o >= s["t1"] or o <= s["stop"]:
        return {**out, "status": "SKIP (gap beyond T1/SL)", "r": 0.0}
    win = bars[(bars.index.time >= time(9, 30)) & (bars.index.time <= time(10, 30))]
    trig = win[win["Close"] >= s["entry"]]
    if not len(trig):
        return {**out, "status": "NOT TRIGGERED", "r": 0.0}
    t0 = trig.index[0]
    e = float(trig["Close"].iloc[0])
    risk = e - s["stop"]
    after = bars[bars.index > t0]
    status, r = "OPEN (day 1 of 2)", (float(bars["Close"].iloc[-1]) - e) / risk
    for _, b in after.iterrows():
        if b["Low"] <= s["stop"]:
            status, r = "SL HIT", -1.0
            break
        if b["High"] >= s["t1"]:
            status, r = "T1 HIT (half booked)", 0.5 * (s["t1"] - e) / risk + 0.5 * (float(bars["Close"].iloc[-1]) - e) / risk
            break
    qty = int(min(10000 * 0.01 / risk, 10000 / e)) if risk > 0 else 0
    return {**out, "status": status, "entry_fill": round(e, 2), "entry_time": t0.strftime("%H:%M"),
            "r": round(r, 2), "pnl_10k": round(r * risk * qty), "qty_10k": qty}


# ------------------------------------------------------------------ main
def review_session(session, archived=None):
    archived = archived or load_archived(session.isoformat())
    if archived is None:
        return None
    now = datetime.now(IST)
    b = archived["bias"]
    nifty = intraday("^NSEI", session)
    if nifty is None:
        return None
    vix = intraday("^INDIAVIX", session)
    prev_close = b["options"]["NIFTY"]["levels"]["prev_close"] if "NIFTY" in b["options"] else float(nifty["Open"].iloc[0])
    actual = (float(nifty["Close"].iloc[-1]) / prev_close - 1) * 100
    typical = b["bias"]["typical_daily_move_pct"]
    label = b["bias"]["label"]
    correct = {"BULLISH": actual > 0, "BEARISH": actual < 0}.get(label, abs(actual) < 0.5 * typical)
    final = not (now.date() == session and now.time() < time(15, 30))

    opts = []
    for name, o in b["options"].items():
        bars = nifty if name == "NIFTY" else intraday(INDEX_SPECS[name]["ticker"], session)
        if bars is not None:
            opts.append(replay_option(name, o, session, bars, vix, now))

    stocks = []
    for s in archived.get("stocks", [])[:8]:
        bars = intraday(s["symbol"] + ".NS", session)
        if bars is not None:
            stocks.append(replay_stock(s, bars))

    traded = [x for x in opts if x.get("status") not in ("NO ENTRY", "NO DATA YET")]
    total = sum(x["pnl_per_lot"] for x in traded)
    planned = sum(x["planned_max_reward"] or 0 for x in traded)
    lessons = []
    lessons.append(f"Bias {label}: Nifty {actual:+.2f}% vs prev close. "
                   + ("Call was right." if correct else
                      f"Call was wrong ({'move bigger than the sideways band' if label == 'RANGEBOUND' else 'direction missed'})."))
    for x in opts:
        if x.get("verdict"):
            lessons.append(f"{x['index']}: {x['verdict']}")
    if any(s["tradeable"] for s in stocks) is False and stocks:
        lessons.append("No A/B stocks: watchlist shown for learning only (market gate closed).")
    out = {
        "session": session.isoformat(), "as_of": now.isoformat(), "final": final,
        "plan_built": b["generated_at"],
        "bias": {"label": label, "predicted_pct": b["bias"]["predicted_nifty_move_pct"], "actual_pct": round(actual, 2),
                 "band_pct": round(0.5 * typical, 2), "correct": bool(correct)},
        "options": opts, "stocks": stocks,
        "summary": {"options_pnl_per_lot": total, "planned_max_reward": planned,
                    "gap": total - planned, "trades": len(traded)},
        "lessons": lessons,
        "note": "Option P&L is a Black-Scholes estimate repriced with India VIX, per 1 lot, before costs. "
                "Stock results use a ₹10,000 / 1% risk example.",
    }
    return out


def save_review(rv):
    path = store.state_path("reviews.json")
    hist = store._read(path, [])
    hist = [h for h in hist if h["session"] != rv["session"]] + [rv]
    hist.sort(key=lambda h: h["session"])
    store._write(path, hist[-250:], indent=1)
    slim = [{"session": h["session"], "final": h["final"], "bias": h["bias"]["label"],
             "bias_correct": h["bias"]["correct"], "actual_pct": h["bias"]["actual_pct"],
             "options_pnl": h["summary"]["options_pnl_per_lot"], "trades": h["summary"]["trades"]} for h in hist]
    wins = [h for h in slim if h["final"] and h["trades"]]
    store.publish("review.json", {
        "latest": rv, "history": slim[::-1][:60],
        "totals": {"days": len([h for h in slim if h["final"]]),
                   "bias_hit_pct": round(100 * sum(h["bias_correct"] for h in slim if h["final"]) / max(1, len([h for h in slim if h["final"]])), 1),
                   "options_pnl_sum": sum(h["options_pnl"] for h in wins),
                   "options_win_days": sum(h["options_pnl"] > 0 for h in wins), "options_trade_days": len(wins)},
    })


def run(session=None, archived=None):
    session = session or datetime.now(IST).date()
    rv = review_session(session, archived)
    if rv:
        save_review(rv)
    return rv
