"""Paper-only fourth trade: stock calls and puts on the app's stock picks, in three budgets.

Rule: each stock the plan recommends gets a CALL for a BUY pick (breakout) or a PUT for a SELL pick (breakdown,
only while NIFTY is in a downtrend), bought when the share crosses its entry level between 09:30 and 10:30, using
the monthly expiry at least 7 days away. One paper lot is bought in EACH budget: the strike nearest the money
whose 1 lot costs up to Rs 5,000, Rs 5,000-10,000 and Rs 10,000-15,000 (cheaper = further out of the money,
dearer = in the money), at the real NSE mid price with a two-sided quote. Each budget keeps its own record, so
you can see which one works before going live. Exits are the same as the momentum buy and every bought option: stop -30%;
once up 20% the stop moves to cost; once up 40% it trails 25 points below the peak; book at +60%; otherwise
close at 15:15 the same day. The guardian places it only on your tap (real money only with LIVE_STOCKOPT=1).

Research before adding it (11 Nov 2026, NSE's real daily option prices, 218 F&O stocks, Oct 2024 - Nov 2026):
buying the ATM option at the next open and selling at that day's close LOST on average for every signal set:
breakout calls (premium up to Rs 15,000) 427 trades, 33% won, avg -Rs 616; breakdown puts 444 trades, 38% won,
avg -Rs 463. The shares themselves moved about 0% after the signals, and option buyers pay time decay and wide
spreads. It is tracked on paper, on real prices, so its live record can be judged, not because it has an edge.

Budget research (12 Nov 2026, same data, same-day trades, the app's 82 stocks, calls and puts, the strike nearest
the money that fits each budget, strike chosen from the share's open): every budget LOST on average.
Up to Rs 5k: 348 trades, 30% won, -Rs 232 a trade; Rs 5-10k: 954 trades, 35% won, -Rs 312; Rs 10-15k: 1,163 trades,
36% won, -Rs 412. Booking early at +Rs 1,000 or +Rs 2,000 (stop -30%) was worse: small wins, larger losses.
Also tested: stocks that won in year 1 (lost in year 2), results-day gaps (about break-even, not reliable),
straddles before results (-Rs 3,588 a trade), big news-like gaps (about break-even). None is used as a rule.
"""
from datetime import date, datetime

import store
from momentum import BOOK, stop_level

ENTRY_FROM, ENTRY_TO, EXIT_AT = "09:30", "10:30", "15:15"
MAX_PREMIUM = 15000
# (code, low, high, label): one paper lot per budget, the strike nearest the money whose lot costs low < cost <= high
TIERS = [("5K", 0, 5000, "up to Rs 5,000"), ("10K", 5000, 10000, "Rs 5,000-10,000"), ("15K", 10000, 15000, "Rs 10,000-15,000")]
MAX_SPREAD = 0.15     # a strike counts only with a two-sided quote no wider than 15% of its mid price
MIN_DAYS = 7          # expiry at least this many days away (as in the research)
COST = 100            # brokerage and charges for 1 lot, in and out
SLIP = 0.015          # stock option spreads are wide: 1.5% of the premium each way, as in the research
PATH = "stockopt.json"
RESEARCH = ("2-year test on NSE's real prices, same-day trades: every budget LOST on average (up to Rs 5k: -Rs 232 a trade, "
            "30% won; Rs 5-10k: -Rs 312, 35% won; Rs 10-15k: -Rs 412, 36% won). Booking early at +Rs 1-2k was worse, and "
            "past winning stocks, results days and news gaps did not hold up either. Paper only, to see how each budget does.")


def key(p, tier=None):
    return f"STOCK_{p['symbol']}" + (f"_{tier}" if tier else "")


def tier_of(premium):
    """Budget code for a lot cost (records from before the budgets had no tier)."""
    return next((c for c, lo, hi, _ in TIERS if lo < (premium or 0) <= hi), None)


def pick_tier(chain, kind, lot, lo, hi, quoted=True):
    """The strike nearest the money whose 1 lot costs lo < cost <= hi: {"strike", "px", "premium"} or None.
    quoted=True needs a two-sided quote no wider than MAX_SPREAD (live entries); False also takes the last
    traded price (estimates before the open, when there are no quotes)."""
    if not chain or not lot:
        return None
    k, atm, best = kind.lower(), chain["atm"], None
    for r in chain.get("strikes") or []:
        b, a = r.get(f"{k}_bid"), r.get(f"{k}_ask")
        if b and a and a >= b > 0:
            px = (b + a) / 2
            if quoted and (a - b) / px > MAX_SPREAD:
                continue
        elif quoted:
            continue
        else:
            px = r.get(f"{k}_ltp")
            if not px:
                continue
        px = round(float(px), 2)
        cost = px * lot
        if lo < cost <= hi:
            cand = (abs(r["strike"] - atm), cost, r["strike"], px)
            best = cand if best is None or cand < best else best
    return {"strike": best[2], "px": best[3], "premium": round(best[1])} if best else None


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
    chains = {}

    def chain(sym, exp=None):
        if (sym, exp) not in chains:
            ch = chains[(sym, exp)] = chain_for(sym, exp) if exp else chain_for(sym)
            if ch and not exp:
                chains.setdefault((sym, ch["expiry"]), ch)
        return chains[(sym, exp)]

    if plan.get("session") == day:
        recs = days.setdefault(day, {})
        for p in plan.get("stock_options") or []:
            if key(p) in recs:      # bought before the budgets existed: its exits run below, nothing new
                continue
            todo = [t for t in TIERS if key(p, t[0]) not in recs]
            if not todo:
                continue
            base = {"date": day, "symbol": p["symbol"], "kind": p["kind"], "trigger": p["trigger"]}
            ok, why = triggered(p, quotes.get(p["symbol"] + ".NS") or {}, hm)
            if why:
                for c, *_ in todo:
                    recs[key(p, c)] = {**base, "tier": c, "status": "NO ENTRY", "note": why}
                continue
            if ok and hm <= ENTRY_TO:
                if key(p) not in tried.setdefault(day, []):
                    tried[day].append(key(p))
                ch, lot = chain(p["symbol"]), lots.get(p["symbol"])
                if ch and lot:
                    for c, lo, hi, label in todo:
                        x = pick_tier(ch, p["kind"], lot, lo, hi)
                        recs[key(p, c)] = ({**base, "tier": c, "status": "OPEN", "strike": x["strike"], "expiry": ch["expiry"],
                                            "lot": lot, "entry_px": x["px"], "entry_at": hm, "spot": round(ch["spot"], 2),
                                            "premium": x["premium"], "atm": ch["atm"], "peak": 0.0, "marks": [[hm, x["px"]]]}
                                           if x else
                                           {**base, "tier": c, "status": "SKIPPED", "lot": lot,
                                            "note": f"no {p['kind']} strike with a live two-sided price costs {label} for 1 lot ({lot} qty)"})
            if hm > ENTRY_TO:
                for c, *_ in todo:
                    if key(p, c) not in recs:
                        recs[key(p, c)] = {**base, "tier": c, "status": "NO ENTRY",
                                           "note": f"no live option price or lot size between {ENTRY_FROM} and {ENTRY_TO}"
                                           if key(p) in tried.get(day, []) else f"did not cross {p['trigger']} between {ENTRY_FROM} and {ENTRY_TO}"}
        for r in recs.values():
            if r.get("status") != "OPEN":
                continue
            ch = chain(r["symbol"], r["expiry"])
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
            elif hm > EXIT_AT and r.get("marks") and r["marks"][-1][0] != hm:   # no price at the close: the last one seen
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
    return list(dict.fromkeys((r["symbol"], r["expiry"]) for r in recs.values() if r.get("status") == "OPEN"))


def summary(now=None, day=None):
    """Today's records and the running totals of closed trades (same shape as momentum.summary)."""
    book = store._read(store.state_path(PATH), {}) or {}
    days = book.get("days") or {}
    day = day or (now.date().isoformat() if now else max(days, default=None))
    closed = [{**r, "tier": r.get("tier") or tier_of(r.get("premium"))}
              for d in sorted(days) for r in days[d].values() if r.get("status") == "CLOSED"]
    today = []
    for r in (days.get(day) or {}).values():
        t = dict(r)
        t["tier"] = t.get("tier") or tier_of(t.get("premium"))
        if t.get("status") == "OPEN":
            last = t["marks"][-1]
            t.update(value_now=round((last[1] - t["entry_px"]) * t["lot"]), pct_now=round((last[1] / t["entry_px"] - 1) * 100, 1),
                     at=last[0], stop_pct=stop_level(t["peak"]))
        t.pop("marks", None)
        today.append(t)
    return {"date": day, "today": today, "trades": len(closed), "wins": sum(r["pnl"] > 0 for r in closed),
            "total": sum(r["pnl"] for r in closed),
            "tiers": {c: {"label": label, "trades": len(x), "wins": sum(r["pnl"] > 0 for r in x), "total": sum(r["pnl"] for r in x)}
                      for c, _, _, label in TIERS for x in [[r for r in closed if r["tier"] == c]]},
            "recent": [{k: r.get(k) for k in ("date", "symbol", "kind", "tier", "strike", "entry_px", "exit_px", "pnl", "pct", "reason")}
                       for r in closed[::-1][:8]],
            "rule": (f"Paper only. Each stock pick (call for a BUY pick, put for a SELL pick) is bought when the share crosses its entry "
                     f"between {ENTRY_FROM} and {ENTRY_TO}: 1 lot in each budget, the strike nearest the money costing up to Rs 5,000, "
                     f"Rs 5,000-10,000 and Rs 10,000-15,000. Stop -30%, cost after +20%, trail from +40%, book +60%, else close "
                     f"{EXIT_AT}. Costs Rs {COST} + {SLIP * 100:.1f}% each way."),
            "research": RESEARCH}


def estimate_premiums(picks, today=None):
    """Money for 1 lot of each pick's ATM option and the strike each budget would buy, from NSE's chain (last traded
    prices before the open). {symbol: {"lot", "strike", "premium", "tiers": {code: {"strike", "premium"} | None}}};
    empty when NSE does not answer. Cached 30 minutes."""
    import live   # here, not at the top: live imports this module
    import nse_prices
    cached = store.cache_get("stockopt:est")
    want = sorted({p["symbol"] for p in picks})
    if cached is not None and cached.get("syms") == want:
        return cached["est"]
    lots, nse, est = nse_prices.lots(), live.NSEClient(), {}
    chain_for = live.stock_chain(nse)
    for p in picks:
        lot = lots.get(p["symbol"])
        ch = chain_for(p["symbol"]) if lot else None
        px = price(ch, ch["atm"], p["kind"]) if ch else None
        if px:
            tiers = {c: pick_tier(ch, p["kind"], lot, lo, hi, quoted=False) for c, lo, hi, _ in TIERS}
            est[p["symbol"]] = {"lot": lot, "strike": ch["atm"], "premium": round(px * lot),
                                "tiers": {c: x and {"strike": x["strike"], "premium": x["premium"]} for c, x in tiers.items()}}
    store.cache_set("stockopt:est", {"syms": want, "est": est}, 1800)
    return est
