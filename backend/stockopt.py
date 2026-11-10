"""Paper-only fourth trade: stock calls and puts on the app's stock picks.

Rule: each stock the plan recommends gets 1 lot of its at-the-money option at the real NSE price: a CALL for a
BUY pick (breakout), a PUT for a SELL pick (breakdown, only while NIFTY is in a downtrend). It is bought when the
share crosses its entry level between 09:30 and 10:30, using the monthly expiry at least 7 days away, and only
if 1 lot costs at most Rs 15,000. Exits are the same as the momentum buy and every bought option: stop -30%;
once up 20% the stop moves to cost; once up 40% it trails 25 points below the peak; book at +60%; otherwise
close at 15:15 the same day. The guardian places it only on your tap (real money only with LIVE_STOCKOPT=1).

Research before adding it (11 Nov 2026, NSE's real daily option prices, 218 F&O stocks, Oct 2024 - Nov 2026):
buying the ATM option at the next open and selling at that day's close LOST on average for every signal set:
breakout calls (premium up to Rs 15,000) 427 trades, 33% won, avg -Rs 616; breakdown puts 444 trades, 38% won,
avg -Rs 463. The shares themselves moved about 0% after the signals, and option buyers pay time decay and wide
spreads. It is tracked on paper, on real prices, so its live record can be judged, not because it has an edge.
"""
from datetime import date, datetime

import store
from momentum import BOOK, stop_level

ENTRY_FROM, ENTRY_TO, EXIT_AT = "09:30", "10:30", "15:15"
MAX_PREMIUM = 15000
MIN_DAYS = 7          # expiry at least this many days away (as in the research)
COST = 100            # brokerage and charges for 1 lot, in and out
SLIP = 0.015          # stock option spreads are wide: 1.5% of the premium each way, as in the research
PATH = "stockopt.json"
RESEARCH = ("2-year test on NSE's real prices (218 stocks): bought at the open, sold the same day, these LOST on average "
            "(calls -Rs 616 a trade, 33% won; puts -Rs 463, 38% won). Paper only, to see how they do from here.")


def key(p):
    return f"STOCK_{p['symbol']}"


def parse_chain(rec, expiry):
    """NSE option-chain-v3 records for one stock -> the shape momentum._price reads, plus the ATM strike."""
    spot = float(rec.get("underlyingValue") or 0)
    rows = []
    for r in rec.get("data", []):
        ce, pe = r.get("CE") or {}, r.get("PE") or {}
        rows.append({"strike": float(r.get("strikePrice") or 0),
                     "ce_bid": ce.get("buyPrice1"), "ce_ask": ce.get("sellPrice1"), "ce_ltp": ce.get("lastPrice"),
                     "pe_bid": pe.get("buyPrice1"), "pe_ask": pe.get("sellPrice1"), "pe_ltp": pe.get("lastPrice")})
    if not rows or not spot:
        return None
    rows.sort(key=lambda x: x["strike"])
    return {"expiry": expiry, "spot": spot, "atm": min(rows, key=lambda x: abs(x["strike"] - spot))["strike"], "strikes": rows}


def pick_expiry(expiries, today):
    """First expiry (NSE format DD-Mon-YYYY) at least MIN_DAYS away."""
    for e in expiries:
        d = datetime.strptime(e, "%d-%b-%Y").date()
        if (d - today).days >= MIN_DAYS:
            return e
    return None


def price(chain, strike, kind):
    x = next((r for r in (chain or {}).get("strikes") or [] if r["strike"] == strike), None)
    if not x:
        return None
    k = kind.lower()
    b, a, last = x.get(f"{k}_bid"), x.get(f"{k}_ask"), x.get(f"{k}_ltp")
    if b and a and a >= b > 0:
        return round((b + a) / 2, 2)
    return float(last) if last else None


def triggered(p, q, hm):
    """Has the share crossed its entry level in the window, without first going through its stop or T1?"""
    last, lo, hi, op = q.get("last"), q.get("low"), q.get("high"), q.get("open")
    if last is None or hm < ENTRY_FROM:
        return None, None
    if p["kind"] == "CE":
        if op is not None and op >= p["t1"]:
            return False, f"opened at {op} above T1 {p['t1']}: skipped"
        if lo is not None and lo <= p["stop"]:
            return False, f"fell to its stop {p['stop']} before entry: skipped"
        return last >= p["trigger"], None
    if op is not None and op <= p["t1"]:
        return False, f"opened at {op} below T1 {p['t1']}: skipped"
    if hi is not None and hi >= p["stop"]:
        return False, f"rose to its stop {p['stop']} before entry: skipped"
    return last <= p["trigger"], None


def _close(r, px, at, why):
    cost = COST + SLIP * (r["entry_px"] + px) * r["lot"]
    r.update(status="CLOSED", exit_px=px, exit_at=at, reason=why, cost=round(cost),
             pnl=round((px - r["entry_px"]) * r["lot"] - cost), pct=round((px / r["entry_px"] - 1) * 100, 1))


def step(now, plan, quotes, chain_for, lots):
    """One live run (about every 10 minutes). plan: plan.json; quotes: Yahoo quotes keyed 'SYM.NS';
    chain_for(symbol, expiry=None) -> parse_chain() result for that stock or None; lots: {symbol: lot size}."""
    path = store.state_path(PATH)
    book = store._read(path, {}) or {}
    days = book.setdefault("days", {})
    tried = book.setdefault("tried", {})   # crossed its entry in the window but had no option price yet
    day, hm = now.date().isoformat(), now.strftime("%H:%M")
    for d, recs in days.items():   # left open by missed runs: close at the last real price seen that day
        for r in recs.values():
            if d < day and r.get("status") == "OPEN":
                _close(r, r["marks"][-1][1], r["marks"][-1][0], "closed at its last price that day (runs were missed)")
    if plan.get("session") == day:
        recs = days.setdefault(day, {})
        for p in plan.get("stock_options") or []:
            k, r = key(p), recs.get(key(p))
            base = {"date": day, "symbol": p["symbol"], "kind": p["kind"], "trigger": p["trigger"]}
            q = quotes.get(p["symbol"] + ".NS") or {}
            if r is None:
                ok, why = triggered(p, q, hm)
                if why:
                    recs[k] = {**base, "status": "NO ENTRY", "note": why}
                elif ok and hm <= ENTRY_TO:
                    if k not in tried.setdefault(day, []):
                        tried[day].append(k)
                    ch = chain_for(p["symbol"])
                    lot = lots.get(p["symbol"])
                    px = price(ch, ch["atm"], p["kind"]) if ch else None
                    if px and lot and px * lot > MAX_PREMIUM:
                        recs[k] = {**base, "status": "SKIPPED", "note": f"1 lot costs Rs {px * lot:,.0f}, above Rs {MAX_PREMIUM:,}",
                                   "strike": ch["atm"], "lot": lot, "premium": round(px * lot)}
                    elif px and lot:
                        recs[k] = {**base, "status": "OPEN", "strike": ch["atm"], "expiry": ch["expiry"], "lot": lot,
                                   "entry_px": px, "entry_at": hm, "spot": round(ch["spot"], 2), "premium": round(px * lot),
                                   "peak": 0.0, "marks": [[hm, px]]}
                if k not in recs and hm > ENTRY_TO:
                    recs[k] = {**base, "status": "NO ENTRY",
                               "note": f"no live option price or lot size between {ENTRY_FROM} and {ENTRY_TO}"
                               if k in tried.get(day, []) else f"did not cross {p['trigger']} between {ENTRY_FROM} and {ENTRY_TO}"}
            elif r.get("status") == "OPEN":
                ch = chain_for(p["symbol"], r["expiry"])
                px = price(ch, r["strike"], r["kind"]) if ch else None
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
                elif hm > EXIT_AT and r.get("marks"):   # no price at the close: use the last one seen
                    _close(r, r["marks"][-1][1], r["marks"][-1][0], "15:15 time exit (last price seen)")
    for d in sorted(days)[:-300]:
        days.pop(d)
    for d in [d for d in tried if d < day]:
        tried.pop(d)
    store._write(path, book)
    return summary(now)


def open_keys(now):
    """(symbol, expiry) of today's open trades, so the live run fetches their prices."""
    recs = ((store._read(store.state_path(PATH), {}) or {}).get("days") or {}).get(now.date().isoformat()) or {}
    return [(r["symbol"], r["expiry"]) for r in recs.values() if r.get("status") == "OPEN"]


def summary(now=None, day=None):
    """Today's records and the running totals of closed trades (same shape as momentum.summary)."""
    book = store._read(store.state_path(PATH), {}) or {}
    days = book.get("days") or {}
    day = day or (now.date().isoformat() if now else max(days, default=None))
    closed = [r for d in sorted(days) for r in days[d].values() if r.get("status") == "CLOSED"]
    today = []
    for r in (days.get(day) or {}).values():
        t = dict(r)
        if t.get("status") == "OPEN":
            last = t["marks"][-1]
            t.update(value_now=round((last[1] - t["entry_px"]) * t["lot"]), pct_now=round((last[1] / t["entry_px"] - 1) * 100, 1),
                     at=last[0], stop_pct=stop_level(t["peak"]))
        t.pop("marks", None)
        today.append(t)
    return {"date": day, "today": today, "trades": len(closed), "wins": sum(r["pnl"] > 0 for r in closed),
            "total": sum(r["pnl"] for r in closed),
            "recent": [{k: r.get(k) for k in ("date", "symbol", "kind", "strike", "entry_px", "exit_px", "pnl", "pct", "reason")}
                       for r in closed[::-1][:8]],
            "rule": (f"Paper only. Each stock pick gets 1 lot of its ATM option (call for a BUY pick, put for a SELL pick), bought when "
                     f"the share crosses its entry between {ENTRY_FROM} and {ENTRY_TO}, premium up to Rs {MAX_PREMIUM:,}. Stop -30%, cost "
                     f"after +20%, trail from +40%, book +60%, else close {EXIT_AT}. Costs Rs {COST} + {SLIP * 100:.1f}% each way."),
            "research": RESEARCH}
