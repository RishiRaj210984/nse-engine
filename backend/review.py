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
        if "Fly" not in kind and "setup" not in o:
            # plan published before the 10:15 / short-strike rule: grade by its own support-resistance rule
            ts_old, spot_old = _at(bars, "10:00")
            ok = lv["support"] <= spot_old <= lv["resistance"]
            cond = f"{name} between {lv['support']:,.0f} and {lv['resistance']:,.0f} at 10:00 (rule of that plan)"
            ts_e, spot_e = ts_old, spot_old
        elif "Fly" not in kind:
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
            if "setup" not in o and (sp > hi_stop or sp < lo_stop):  # older plans also stopped on a strike break
                exit_ts, exit_spot, reason = ts, sp, "STOP: short strike broken"
            elif (v - v_e) <= -credit:  # loss reached 1x credit
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
    stock_pnl = sum(s.get("pnl_10k") or 0 for s in stocks if s["tradeable"])
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
                    "gap": total - planned, "trades": len(traded),
                    "stocks_pnl_10k": stock_pnl, "stock_trades": sum(1 for s in stocks if s["tradeable"] and s.get("entry_fill")),
                    "plan_pnl": total + stock_pnl},
        "lessons": lessons,
        "note": "Option P&L is a Black-Scholes estimate repriced with India VIX, per 1 lot, before costs. "
                "Stock results use a ₹10,000 / 1% risk example.",
    }
    return out


# ------------------------------------------------------------------ paper book
# What you would really hold if you followed the app: only indices the plan marked TRADE, one open
# position per index, entered at 10:15 if the entry condition holds, then carried day to day with the
# guardian's exits (target 55% of credit kept, stop at 1x credit, expiry day 10:15). Costs are charged
# when a trade closes. Each day's P&L is the change in value of what was held, so open trades count too.
BOOK_COSTS = {"NIFTY": 400, "BANKNIFTY": 500}   # Rs per round trip, same as the options lab
EXPIRY_EXIT = time(10, 15)


def _decision(archived, name):
    for o in (archived.get("plan") or {}).get("options") or []:
        if o.get("index") == name:
            return o.get("decision")
    return ((archived["bias"]["options"].get(name) or {}).get("setup") or {}).get("decision")


def _vix_at(vix, ts):
    if vix is None:
        return None
    sub = vix[vix.index <= ts]
    return float(sub["Close"].iloc[-1]) if len(sub) else float(vix["Close"].iloc[0])


def paper_book(session, archived, bars_by_index, vix):
    """Advance the paper book through `session`. Safe to re-run for the same day."""
    path = store.state_path("paper_book.json")
    book = store._read(path, {"open": {}, "closed": [], "days": [], "before": None})
    s_iso = session.isoformat()
    if book.get("before") and book["before"]["session"] == s_iso:      # re-run: undo this day first
        book["open"] = book["before"]["open"]
        book["closed"] = book["closed"][:book["before"]["n_closed"]]
    book["days"] = [d for d in book["days"] if d["session"] != s_iso]
    book["before"] = {"session": s_iso, "open": json.loads(json.dumps(book["open"])), "n_closed": len(book["closed"])}
    day_pnl, detail = 0, []
    for name in ("NIFTY", "BANKNIFTY"):
        bars = bars_by_index.get(name)
        if bars is None:
            continue
        pos = book["open"].get(name)
        start = bars.index[0]
        if pos is None:
            o = archived["bias"]["options"].get(name)
            if not o or _decision(archived, name) != "TRADE":
                continue
            legs = o["strategies"][0]["legs"]
            if not any(l["action"] == "SELL" for l in legs):
                detail.append(f"{name}: not a sold structure, not tracked")
                continue
            ts_e, spot_e = _at(bars, "10:15")
            shorts = sorted(l["strike"] for l in legs if l["action"] == "SELL")
            if ts_e is None or not (len(shorts) < 2 or shorts[0] < spot_e < shorts[-1]):
                detail.append(f"{name}: entry condition not met at 10:15, no trade")
                continue
            exp = date.fromisoformat(o["expiry"])
            iv0, vix0 = o["iv_used_pct"] / 100, _vix_at(vix, ts_e)
            v_e = _value(legs, spot_e, _T(exp, ts_e), iv0)
            pos = {"index": name, "strategy": o["strategies"][0]["name"], "legs": legs, "lot": o["lot_size"],
                   "expiry": o["expiry"], "entry_session": s_iso, "entry_spot": round(spot_e, 2), "v_e": v_e,
                   "credit_rs": round(-v_e * o["lot_size"]), "iv0": iv0, "vix0": vix0, "mark": 0}
            start = ts_e
            detail.append(f"{name}: entered {pos['strategy']} at 10:15, credit Rs {pos['credit_rs']:,}")
        exp = date.fromisoformat(pos["expiry"])
        iv = lambda ts: pos["iv0"] * (_vix_at(vix, ts) / pos["vix0"]) if pos["vix0"] and vix is not None else pos["iv0"]
        credit, v_e, lot = -pos["v_e"], pos["v_e"], pos["lot"]
        after = bars[bars.index > start].resample("15min", label="right", closed="right").last().dropna()
        reason, v = None, v_e
        for ts, row in after.iterrows():
            v = _value(pos["legs"], float(row["Close"]), _T(exp, ts), iv(ts))
            if (v - v_e) <= -credit:
                reason = "STOP: loss reached 1x credit"
            elif -v <= 0.45 * credit:
                reason = "TARGET: 55% of credit kept"
            elif session >= exp and ts.time() >= EXPIRY_EXIT:
                reason = f"EXPIRY DAY: closed at {ts.strftime('%H:%M')}"
            if reason:
                break
        value_rs = round((v - v_e) * lot)
        if reason or session > exp:
            reason = reason or "EXPIRED: settled at last price"
            realized = value_rs - BOOK_COSTS[name]
            day_pnl += realized - pos["mark"]
            book["closed"].append({**{k: pos[k] for k in ("index", "strategy", "expiry", "entry_session", "credit_rs")},
                                   "exit_session": s_iso, "reason": reason, "pnl": realized})
            detail.append(f"{name}: {reason}, trade result Rs {realized:+,} after costs")
            book["open"].pop(name, None)
        else:
            day_pnl += value_rs - pos["mark"]
            detail.append(f"{name}: still open (expiry {exp.strftime('%d %b')}), worth Rs {value_rs:+,} now")
            pos["mark"] = value_rs
            book["open"][name] = pos
    realized = sum(c["pnl"] for c in book["closed"])
    unreal = sum(p["mark"] for p in book["open"].values())
    book["days"].append({"session": s_iso, "pnl": round(day_pnl), "detail": detail, "total": realized + unreal})
    book["days"] = sorted(book["days"], key=lambda d: d["session"])[-250:]
    store._write(path, book, indent=1)
    return {"days": book["days"][::-1][:60], "realized": realized, "open_value": unreal, "total": realized + unreal,
            "open": [{k: p[k] for k in ("index", "strategy", "expiry", "entry_session", "credit_rs", "mark")} for p in book["open"].values()],
            "closed": book["closed"][::-1][:30], "trades": len(book["closed"]),
            "wins": sum(c["pnl"] > 0 for c in book["closed"]),
            "note": "Paper book: TRADE days only, 1 lot, held like the guardian holds it (target, stop, expiry-day 10:15), "
                    "costs charged when a trade closes. Premiums are Black-Scholes estimates with India VIX."}


def _T(expiry, ts):
    mins_left = max((datetime.combine(expiry, time(15, 30), IST) - ts).total_seconds() / 60, 5)
    return mins_left / (60 * 24 * 365)


def save_review(rv, paper=None):
    path = store.state_path("reviews.json")
    hist = store._read(path, [])
    hist = [h for h in hist if h["session"] != rv["session"]] + [rv]
    hist.sort(key=lambda h: h["session"])
    store._write(path, hist[-250:], indent=1)
    slim = [{"session": h["session"], "final": h["final"], "bias": h["bias"]["label"],
             "bias_correct": h["bias"]["correct"], "actual_pct": h["bias"]["actual_pct"],
             "options_pnl": h["summary"]["options_pnl_per_lot"], "trades": h["summary"]["trades"],
             "stocks_pnl": h["summary"].get("stocks_pnl_10k", 0),
             "plan_pnl": h["summary"].get("plan_pnl", h["summary"]["options_pnl_per_lot"]),
             "detail": [f"{o['index']} {o['strategy']}: {o.get('status')} {o['pnl_per_lot']:+,}" for o in h.get("options", [])]}
            for h in hist]
    wins = [h for h in slim if h["final"] and h["trades"]]
    if paper is None:   # keep the last published paper book
        paper = (store._read(os.path.join(store.PUBLIC_DIR, "review.json"), {}) or {}).get("paper")
    store.publish("review.json", {
        "latest": rv, "history": slim[::-1][:60], "paper": paper,
        "totals": {"days": len([h for h in slim if h["final"]]),
                   "bias_hit_pct": round(100 * sum(h["bias_correct"] for h in slim if h["final"]) / max(1, len([h for h in slim if h["final"]])), 1),
                   "options_pnl_sum": sum(h["options_pnl"] for h in wins),
                   "plan_pnl_sum": sum(h["plan_pnl"] for h in slim if h["final"]),
                   "plan_win_days": sum(h["plan_pnl"] > 0 for h in slim if h["final"]),
                   "plan_loss_days": sum(h["plan_pnl"] < 0 for h in slim if h["final"]),
                   "options_win_days": sum(h["options_pnl"] > 0 for h in wins), "options_trade_days": len(wins)},
    })


def run(session=None, archived=None):
    session = session or datetime.now(IST).date()
    rv = review_session(session, archived)
    if rv:
        paper = None
        arch = archived or load_archived(session.isoformat())
        if rv["final"] and arch:
            bars = {n: intraday(INDEX_SPECS[n]["ticker"], session) for n in ("NIFTY", "BANKNIFTY")}
            paper = paper_book(session, arch, bars, intraday("^INDIAVIX", session))
        save_review(rv, paper)
    return rv
