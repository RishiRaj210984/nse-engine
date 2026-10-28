"""Paper-only third trade: the NIFTY first-hour momentum buy.

Rule: if NIFTY moves 0.5% or more between 09:15 and 10:15, buy 1 lot of the ATM option that way (call if up,
put if down) at the real NSE price, not on expiry day, and only if the premium is at most Rs 15,000.
Exits are the guardian's rules for bought options: stop at -30%; once up 20% the stop moves to cost; once
up 40% it trails 25 points below the peak; book at +60%; otherwise close at 15:15 the same day.

Why: in a 2-year replay (hourly bars, estimated prices) it made 77 trades, 52% won, avg +Rs 1,117 after
costs, positive in 2024, 2025 and 2026. The 0.5% level was picked after seeing the results, so it is
tracked on paper with real prices before any money goes in. Buying the model's UP/DOWN call instead lost.

Every live run (about every 10 minutes) moves it one step. state/momentum.json keeps one record per day.
"""
import store
from config import INDEX_SPECS

THRESH_PCT = 0.5
ENTRY_FROM, ENTRY_TO, EXIT_AT = "10:15", "10:45", "15:15"
MAX_PREMIUM = 15000
COST = 150   # brokerage, STT, exchange charges and slippage for 1 lot, in and out
STOP, BE_AT, TRAIL_AT, TRAIL, BOOK = -30, 20, 40, 25, 60
PATH = "momentum.json"


def _price(chain, strike, kind):
    """Real NSE price: bid/ask mid, else last price."""
    x = next((r for r in (chain or {}).get("strikes") or [] if r["strike"] == strike), None)
    if not x:
        return None
    k = kind.lower()
    b, a, last = x.get(f"{k}_bid"), x.get(f"{k}_ask"), x.get(f"{k}_ltp")
    if b and a and a >= b > 0:
        return round((b + a) / 2, 2)
    return float(last) if last else None


def stop_level(peak):
    return STOP if peak < BE_AT else 0 if peak < TRAIL_AT else max(0, peak - TRAIL)


def _close(r, px, at, why):
    r.update(status="CLOSED", exit_px=px, exit_at=at, reason=why,
             pnl=round((px - r["entry_px"]) * r["lot"] - COST), pct=round((px / r["entry_px"] - 1) * 100, 1))


def keep_strikes(now):
    """Strike of today's open momentum trade, so the live run always saves its real price."""
    r = (store._read(store.state_path(PATH), {}) or {}).get("days", {}).get(now.date().isoformat()) or {}
    return {r["strike"]} if r.get("status") == "OPEN" else set()


def step(now, quote, chain, nearest_expiry):
    """quote: NIFTY quote with 'open' (09:15) and 'at_1015'; chain: summarised NSE chain (with 'expiry' as
    DD-Mon-YYYY) or None; nearest_expiry: ISO date of NIFTY's nearest expiry. Returns summary(now)."""
    path = store.state_path(PATH)
    book = store._read(path, {}) or {}
    days = book.setdefault("days", {})
    day, hm = now.date().isoformat(), now.strftime("%H:%M")
    for d, r in days.items():   # left open by missed runs: close at the last real price seen that day
        if d < day and r.get("status") == "OPEN":
            _close(r, r["marks"][-1][1], r["marks"][-1][0], "closed at its last price that day (runs were missed)")
    r = days.get(day)
    if r is None and ENTRY_FROM <= hm:
        o, s = (quote or {}).get("open"), (quote or {}).get("at_1015")
        fh = round((s / o - 1) * 100, 2) if o and s else None
        base = {"date": day, "first_hour_pct": fh}
        if nearest_expiry == day:
            days[day] = {**base, "status": "NO TRADE", "note": "expiry day"}
        elif fh is not None and abs(fh) < THRESH_PCT:
            days[day] = {**base, "status": "NO SIGNAL", "note": f"first hour {fh:+.2f}%, needs ±{THRESH_PCT}%"}
        elif fh is not None and chain:
            kind, strike = ("CE" if fh > 0 else "PE"), chain["atm"]
            px, lot = _price(chain, strike, kind), INDEX_SPECS["NIFTY"]["lot"]
            if px and px * lot > MAX_PREMIUM:
                days[day] = {**base, "status": "SKIPPED", "note": f"premium Rs {px * lot:,.0f} is above Rs {MAX_PREMIUM:,}"}
            elif px:
                days[day] = {**base, "status": "OPEN", "kind": kind, "strike": strike, "expiry": chain["expiry"], "lot": lot,
                             "entry_px": px, "entry_at": hm, "spot": chain.get("spot"), "peak": 0.0, "marks": [[hm, px]]}
        if day not in days and hm > ENTRY_TO:
            days[day] = {**base, "status": "NO ENTRY", "note": "no live NIFTY or option price between 10:15 and 10:45"}
    elif r and r.get("status") == "OPEN" and chain and chain.get("expiry") == r["expiry"]:
        px = _price(chain, r["strike"], r["kind"])
        if px and r["marks"][-1][0] != hm:
            r["marks"].append([hm, px])
            pct = (px / r["entry_px"] - 1) * 100
            r["peak"] = round(max(r["peak"], pct), 1)
            stop = stop_level(r["peak"])
            if pct <= stop:
                _close(r, px, hm, "STOP -30%" if stop < 0 else "back to cost after being up 20%" if stop == 0
                       else f"TRAIL: gave back from +{r['peak']:.0f}%")
            elif pct >= BOOK:
                _close(r, px, hm, f"BOOKED at +{pct:.0f}%")
            elif hm >= EXIT_AT:
                _close(r, px, hm, "15:15 time exit")
    for d in sorted(days)[:-400]:
        days.pop(d)
    store._write(path, book)
    return summary(now)


def summary(now=None, day=None):
    """What the app and the evening alert show: today's record and the running totals of closed trades."""
    book = store._read(store.state_path(PATH), {}) or {}
    days = book.get("days") or {}
    day = day or (now.date().isoformat() if now else max(days, default=None))
    closed = [r for d, r in sorted(days.items()) if r.get("status") == "CLOSED"]
    t = dict(days.get(day) or {})
    if t.get("status") == "OPEN":
        last = t["marks"][-1]
        t.update(value_now=round((last[1] - t["entry_px"]) * t["lot"]), pct_now=round((last[1] / t["entry_px"] - 1) * 100, 1),
                 at=last[0], stop_pct=stop_level(t["peak"]))
    t.pop("marks", None)
    return {"today": t or None, "trades": len(closed), "wins": sum(r["pnl"] > 0 for r in closed),
            "total": sum(r["pnl"] for r in closed),
            "recent": [{k: r.get(k) for k in ("date", "kind", "strike", "entry_px", "exit_px", "pnl", "pct", "reason", "first_hour_pct")}
                       for r in closed[::-1][:8]],
            "rule": f"Paper only. If NIFTY moves ±{THRESH_PCT}% from 09:15 to 10:15, buy 1 lot of the ATM call/put that way at the "
                    f"real NSE price (not on expiry day, premium up to Rs {MAX_PREMIUM:,}). Stop -30%, cost after +20%, trail from +40%, "
                    f"book +60%, else close {EXIT_AT}. Costs Rs {COST} per trade."}
