"""Paper-only fifth trade: an overnight stock option on a trending share whose option is cheap.

Rule (research 4, 13 Nov 2026): between 15:00 and 15:30, for every F&O stock in a trend
  - call: share above its 20-day average, the 20-day above the 50-day, and NIFTY above its 50-day average;
  - put:  share below its 20-day average, the 20-day below the 50-day, and NIFTY below its 50-day average;
buy 1 lot of the strike one step IN the money (monthly expiry at least 7 days away) only if the option is cheap:
its implied volatility is below 0.8 x the share's own movement over the last 20 days. Premium up to Rs 15,000,
real NSE mid price with a two-sided quote. Sell at the next trading day's close (15:00-15:30 run). No stop.

Research (NSE's real closing prices, 218 F&O stocks, Oct 2024 - Nov 2026, rules chosen on year 1 only): of 194
rule versions only this one made money in both years: year 1 661 trades, 46% won, +Rs 478 a trade; year 2 725
trades, 44% won, +Rs 404 a trade (after Rs 100 + 1.5% each way). It is weak: calls carried year 1 and puts year 2,
it works only in the Rs 10-15k budget, and one trade a day lost Rs 7,901 in year 1 with a fall of Rs 92,779 from
its best point (year 2: +Rs 65,930, fall Rs 51,122; worst single trade about -Rs 10,000). The same idea bought in
the morning and sold the same day failed in year 2. Tracked on paper to see whether it holds on live prices.
"""
import logging
import math
from datetime import date, datetime, timedelta

import store
from stockopt import COST, MAX_SPREAD, MIN_PX, SLIP

log = logging.getLogger(__name__)
WATCH, PATH = "overnight_watch.json", "overnight.json"
BUY_FROM, BUY_TO, SELL_FROM = "15:00", "15:30", "15:00"
IV_MAX = 0.8            # implied volatility below this share of the 20-day realised volatility = "cheap"
MAX_PREMIUM = 15000
MAX_PER_DAY = 25        # safety cap on paper buys a day (the research counted every one; the app marks each day's
                        # cheapest as the one a Rs 15k account could hold)
SCAN_PER_RUN = 40       # option chains read per run (NSE is slow); the 15:07/15:17/15:27 runs cover about 100 stocks
MIN_DAYS = 7
R = 0.065
RESEARCH = ("2-year test on NSE's real closing prices: the only rule of 194 that made money in both years "
            "(+Rs 478 then +Rs 404 a trade, 45% won), but weak: calls won only in year 1 and puts only in year 2, only "
            "the Rs 10-15k budget worked, and one a day fell up to Rs 92,779 from its best point. Paper only.")


# ------------------------------------------------------------------ evening: the watch list
def ema_series(c, n):
    return c.ewm(span=n, adjust=False).mean()


def make_watch(hist, symbols):
    """hist: {ticker: daily DataFrame} -> the evening watch list: each stock's last close, 20/50-day averages and
    last 20 closes, plus NIFTY's close and 50-day average."""
    stocks, as_of = {}, None
    for sym in symbols:
        df = hist.get(sym + ".NS")
        if df is None or len(df) < 60:
            continue
        c = df["Close"].astype(float)
        stocks[sym] = {"close": round(float(c.iloc[-1]), 2), "ema20": round(float(ema_series(c, 20).iloc[-1]), 4),
                       "ema50": round(float(ema_series(c, 50).iloc[-1]), 4), "closes": [round(float(x), 2) for x in c.iloc[-20:]]}
        as_of = max(as_of or "", c.index[-1].date().isoformat())
    n = hist.get("^NSEI")
    nifty = None
    if n is not None and len(n) >= 60:
        c = n["Close"].astype(float)
        nifty = {"close": round(float(c.iloc[-1]), 2), "ema50": round(float(ema_series(c, 50).iloc[-1]), 4)}
    return {"as_of": as_of, "nifty": nifty, "stocks": stocks}


def refresh_watch():
    """Evening job: rebuild the watch list from Yahoo's daily prices for every F&O stock (lot sizes from NSE)."""
    import data
    import nse_prices
    syms = sorted(nse_prices.lots())
    if not syms:
        return "no F&O list yet"
    w = make_watch(data.history([s + ".NS" for s in syms] + ["^NSEI"], period="1y"), syms)
    store._write(store.state_path(WATCH), w)
    return f"{len(w['stocks'])} stocks as of {w['as_of']}"


# ------------------------------------------------------------------ live: maths
def ema_now(prev, last, n):
    """The n-day average updated with today's price (as it will be at today's close)."""
    return prev + 2 / (n + 1) * (last - prev)


def rv20_now(closes, last):
    """Yearly volatility of the last 20 daily moves, today's move (to the live price) included."""
    px = list(closes[-20:]) + [last]
    r = [math.log(b / a) for a, b in zip(px, px[1:]) if a > 0 and b > 0][-20:]
    if len(r) < 10:
        return None
    m = sum(r) / len(r)
    return math.sqrt(sum((x - m) ** 2 for x in r) / (len(r) - 1)) * math.sqrt(252)


def _ncdf(x):
    return 0.5 * (1 + math.erf(x / math.sqrt(2)))


def bs_price(S, K, T, v, call):
    v, sq = max(v, 1e-4), math.sqrt(T)
    d1 = (math.log(S / K) + (R + v * v / 2) * T) / (v * sq)
    c = S * _ncdf(d1) - K * math.exp(-R * T) * _ncdf(d1 - v * sq)
    return c if call else c - S + K * math.exp(-R * T)


def implied_vol(px, S, K, T, call):
    """Implied volatility from a price (bisection); None when the price is at or below its intrinsic value."""
    intrinsic = max(S - K * math.exp(-R * T), 0) if call else max(K * math.exp(-R * T) - S, 0)
    if px <= intrinsic + 0.05 or T <= 0:
        return None
    lo, hi = 0.01, 3.0
    for _ in range(50):
        mid = (lo + hi) / 2
        if bs_price(S, K, T, mid, call) > px:
            hi = mid
        else:
            lo = mid
    v = (lo + hi) / 2
    return v if 0.011 < v < 2.9 else None


def trend_side(w, last, nifty_up):
    """'CE' / 'PE' when the share is in a trend matching NIFTY's, at the live price; else None."""
    e20, e50 = ema_now(w["ema20"], last, 20), ema_now(w["ema50"], last, 50)
    if nifty_up and last > e20 > e50:
        return "CE"
    if nifty_up is False and last < e20 < e50:
        return "PE"
    return None


def quoted(chain, strike, kind):
    """Mid price with a two-sided quote no wider than MAX_SPREAD, at Rs MIN_PX or more; else None."""
    x = next((r for r in (chain or {}).get("strikes") or [] if r["strike"] == strike), None)
    if not x:
        return None
    k = kind.lower()
    b, a = x.get(f"{k}_bid"), x.get(f"{k}_ask")
    if not (b and a and a >= b > 0):
        return None
    px = (a + b) / 2
    return round(px, 2) if (a - b) / px <= MAX_SPREAD and px >= MIN_PX else None


def mark(chain, strike, kind):
    """Price for marking and selling: the mid with any two-sided quote, else the last traded price."""
    x = next((r for r in (chain or {}).get("strikes") or [] if r["strike"] == strike), None)
    if not x:
        return None
    k = kind.lower()
    b, a, last = x.get(f"{k}_bid"), x.get(f"{k}_ask"), x.get(f"{k}_ltp")
    if b and a and a >= b > 0:
        return round((a + b) / 2, 2)
    return float(last) if last else None


def itm_strike(chain, kind):
    """The strike one step in the money from the ATM strike."""
    ks = sorted(r["strike"] for r in chain.get("strikes") or [])
    atm = chain["atm"]
    below, above = [k for k in ks if k < atm], [k for k in ks if k > atm]
    return (below[-1] if below else None) if kind == "CE" else (above[0] if above else None)


def next_trading_day(d, step=1):
    import nse_calendar
    d += timedelta(days=step)
    while not nse_calendar.is_trading_day(d):
        d += timedelta(days=step)
    return d


def check(sym, w, side, chain, lot, today):
    """Would the rule buy this stock's option now? -> (record or None, reason)."""
    if not chain or not lot:
        return None, "no option chain or lot size"
    S = chain["spot"]
    if trend_side(w, S, side == "CE") != side:
        return None, "not in trend at NSE's price"
    k = itm_strike(chain, side)
    px = quoted(chain, k, side) if k else None
    if not px:
        return None, "no tight two-sided price for the in-the-money strike"
    exp = datetime.strptime(chain["expiry"], "%d-%b-%Y").date()
    T = (exp - today).days / 365
    iv, rv = implied_vol(px, S, k, T, side == "CE"), rv20_now(w["closes"], S)
    if not iv or not rv:
        return None, "no implied volatility"
    if px * lot > MAX_PREMIUM:
        return None, f"1 lot costs Rs {px * lot:,.0f}, above Rs {MAX_PREMIUM:,}"
    if iv >= IV_MAX * rv:
        return None, f"option not cheap: IV {iv * 100:.0f}% vs the share's {rv * 100:.0f}%"
    return {"symbol": sym, "kind": side, "strike": k, "expiry": chain["expiry"], "lot": lot, "entry_px": px,
            "spot": round(S, 2), "iv": round(iv * 100, 1), "rv20": round(rv * 100, 1), "iv_rv": round(iv / rv, 2),
            "premium": round(px * lot)}, "bought"


def _close(r, px, at, why):
    cost = COST + SLIP * (r["entry_px"] + px) * r["lot"]
    r.update(status="CLOSED", exit_px=px, exit_at=at, reason=why, cost=round(cost),
             pnl=round((px - r["entry_px"]) * r["lot"] - cost), pct=round((px / r["entry_px"] - 1) * 100, 1))


# ------------------------------------------------------------------ live: one run
def step(now, chain_for, lots, prices, nifty_last):
    """One live run. chain_for(symbol, expiry=None) -> stockopt.parse_chain() result or None; lots {symbol: lot};
    prices {symbol: live price} (only needed in the buying window); nifty_last: NIFTY's live price."""
    book = store._read(store.state_path(PATH), {}) or {}
    trades = book.setdefault("trades", {})
    scanned = book.setdefault("scanned", {})
    today, hm = now.date(), now.strftime("%H:%M")
    day = today.isoformat()
    stamp = f"{day} {hm}"
    # sell first: bought on an earlier day, sold at the next trading day's close
    for r in trades.values():
        if r.get("status") != "OPEN":
            continue
        sell_on = date.fromisoformat(r["sell_on"])
        if today > sell_on:
            _close(r, r["marks"][-1][1], r["marks"][-1][0], "sold at its last price seen (runs were missed)")
            continue
        selling = today == sell_on and hm >= SELL_FROM
        if not selling and r["marks"][-1][0][:13] == stamp[:13]:
            continue   # one price an hour while held (a chain read per trade); every run once it is time to sell
        ch = chain_for(r["symbol"], r["expiry"])
        px = mark(ch, r["strike"], r["kind"]) if ch else None
        if px and r["marks"][-1][0] != stamp:
            r["marks"].append([stamp, px])
        if selling and px:
            _close(r, px, stamp, "sold at the next day's close")
    # buy: 15:00-15:30, trending shares with a cheap in-the-money option
    w = store._read(store.state_path(WATCH), {}) or {}
    bought_today = [r for r in trades.values() if r["date"] == day]
    # the averages are updated with today's price, so the list must be from the last trading day's close
    fresh = w.get("as_of") == next_trading_day(today, -1).isoformat()
    if BUY_FROM <= hm <= BUY_TO and fresh and w.get("nifty") and nifty_last and len(bought_today) < MAX_PER_DAY:
        nifty_up = nifty_last > ema_now(w["nifty"]["ema50"], nifty_last, 50)
        done = scanned.setdefault(day, [])
        cands = []
        for sym, x in w["stocks"].items():
            last = prices.get(sym)
            side = trend_side(x, last, nifty_up) if last else None
            if side and sym not in done and f"{day}_{sym}" not in trades:
                cands.append((sym, side))
        n_trend = len(cands) + len(done)
        for sym, side in sorted(cands)[:SCAN_PER_RUN]:
            done.append(sym)
            rec, why = check(sym, w["stocks"][sym], side, chain_for(sym), lots.get(sym), today)
            if rec:
                trades[f"{day}_{sym}"] = {**rec, "date": day, "entry_at": hm, "sell_on": next_trading_day(today).isoformat(),
                                          "status": "OPEN", "marks": [[stamp, rec["entry_px"]]]}
                bought_today.append(rec)
                if len(bought_today) >= MAX_PER_DAY:
                    break
        book["last_scan"] = {"date": day, "at": hm, "trend": n_trend, "checked": len(done)}
    for d in [d for d in scanned if d < day]:
        scanned.pop(d)
    for k in sorted(trades)[:-400]:
        if trades[k].get("status") == "CLOSED":
            trades.pop(k)
    store._write(store.state_path(PATH), book)
    return summary(now)


def one_a_day(closed):
    """The trade a Rs 15k account could hold: each day's cheapest option (lowest IV against the share's movement)."""
    best = {}
    for r in closed:
        if r["date"] not in best or r["iv_rv"] < best[r["date"]]["iv_rv"]:
            best[r["date"]] = r
    return [best[d] for d in sorted(best)]


def summary(now=None):
    book = store._read(store.state_path(PATH), {}) or {}
    trades = sorted((book.get("trades") or {}).values(), key=lambda r: (r["date"], r["iv_rv"]))
    day = (now or store.now_ist()).date().isoformat()
    closed = [r for r in trades if r.get("status") == "CLOSED"]
    first = one_a_day(closed)
    view = []
    for r in trades:
        if r.get("status") == "OPEN" or r.get("exit_at", "")[:10] == day:
            t = {k: v for k, v in r.items() if k != "marks"}
            if r.get("status") == "OPEN":
                last = r["marks"][-1]
                t.update(value_now=round((last[1] - r["entry_px"]) * r["lot"]), pct_now=round((last[1] / r["entry_px"] - 1) * 100, 1),
                         at=last[0][11:] if last[0][:10] == day else last[0])
            view.append(t)
    return {"date": day, "now": view, "last_scan": book.get("last_scan"),
            "trades": len(closed), "wins": sum(r["pnl"] > 0 for r in closed), "total": sum(r["pnl"] for r in closed),
            "one_a_day": {"trades": len(first), "wins": sum(r["pnl"] > 0 for r in first), "total": sum(r["pnl"] for r in first)},
            "recent": [{k: r.get(k) for k in ("date", "symbol", "kind", "strike", "entry_px", "exit_px", "pnl", "pct", "iv_rv")}
                       for r in closed[::-1][:8]],
            "rule": (f"Paper only. {BUY_FROM}-{BUY_TO}: a share in a trend with NIFTY (call: above its 20-day average, 20-day above "
                     f"50-day, NIFTY above its 50-day; put: the mirror) gets 1 lot of the strike one step in the money (monthly "
                     f"expiry at least {MIN_DAYS} days away) only when the option is cheap: implied volatility under {IV_MAX:g}x the "
                     f"share's 20-day movement. Up to Rs {MAX_PREMIUM:,} for 1 lot. Sold at the next trading "
                     f"day's close; no stop. Costs Rs {COST} + {SLIP * 100:.1f}% each way."),
            "research": RESEARCH}


def live_prices(symbols):
    """Live price of every watched stock in one Yahoo download (only called in the buying window)."""
    import data
    hist = data.history([s + ".NS" for s in symbols], period="5d", ttl=120)
    return {s: float(hist[s + ".NS"]["Close"].iloc[-1]) for s in symbols if s + ".NS" in hist}
