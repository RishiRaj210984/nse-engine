"""5th trade (paper): overnight stock option. 15:00-15:30: a share in a trend with NIFTY whose in-the-money option
is cheap (implied volatility under 0.8x the share's 20-day movement) gets 1 lot, sold at the next day's close.
Fake NSE chains and prices, no network."""
import math, os, sys, tempfile
from datetime import datetime
import numpy as np
import pandas as pd
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))
import store, overnight as O  # noqa: E402
from config import IST  # noqa: E402

res = []
def t(name, c): res.append(c); print("PASS" if c else "FAIL", name)

tmp = tempfile.mkdtemp()
store.state_path = lambda n: os.path.join(tmp, n)
at = lambda d, hm: datetime.fromisoformat(f"{d}T{hm}:00").replace(tzinfo=IST)

# --- the maths matches pandas (the research used pandas)
rng = np.random.default_rng(1)
c = pd.Series(100 * np.exp(np.cumsum(rng.normal(0.002, 0.02, 80))), index=pd.bdate_range("2026-07-01", periods=80))
w = O.make_watch({"AAA.NS": pd.DataFrame({"Close": c[:-1]}), "^NSEI": pd.DataFrame({"Close": c[:-1] * 200})}, ["AAA", "MISSING"])["stocks"]["AAA"]
t("today's 20/50-day averages from yesterday's + the live price equal pandas'",
  abs(O.ema_now(w["ema20"], c.iloc[-1], 20) - c.ewm(span=20, adjust=False).mean().iloc[-1]) < 1e-3
  and abs(O.ema_now(w["ema50"], c.iloc[-1], 50) - c.ewm(span=50, adjust=False).mean().iloc[-1]) < 1e-3)
t("20-day movement equals pandas' rolling figure",
  abs(O.rv20_now(w["closes"], c.iloc[-1]) - np.log(c).diff().rolling(20).std().iloc[-1] * math.sqrt(252)) < 1e-3)
t("implied volatility gives back the price it came from", abs(O.implied_vol(O.bs_price(500, 480, 0.05, 0.31, True), 500, 480, 0.05, True) - 0.31) < 1e-3)
t("a stock with too little history is left out", "MISSING" not in O.make_watch({}, ["MISSING"])["stocks"])
up = {"ema20": 95.0, "ema50": 90.0}; dn = {"ema20": 105.0, "ema50": 110.0}
t("call side: share above a rising average, NIFTY up", O.trend_side(up, 100, True) == "CE")
t("no call when NIFTY is below its 50-day", O.trend_side(up, 100, False) is None)
t("put side: share below a falling average, NIFTY down", O.trend_side(dn, 100, False) == "PE")

# --- a run: watch list from Thu 12 Nov, live run Fri 13 Nov 15:07
closes = [round(500 * math.exp(0.025 * ((-1) ** i) + 0.001 * i), 2) for i in range(20)]   # about 80% yearly movement
WATCH = {"as_of": "2026-11-12", "nifty": {"close": 25100.0, "ema50": 24000.0}, "stocks": {
    "CHEAP": {"close": 520.0, "ema20": 500.0, "ema50": 480.0, "closes": closes},
    "DEAR": {"close": 520.0, "ema20": 500.0, "ema50": 480.0, "closes": closes},
    "BIGLOT": {"close": 520.0, "ema20": 500.0, "ema50": 480.0, "closes": closes},
    "FLAT": {"close": 500.0, "ema20": 505.0, "ema50": 500.0, "closes": closes}}}
LOTS = {"CHEAP": 250, "DEAR": 250, "BIGLOT": 5000, "FLAT": 250}
IVS = {"CHEAP": 0.20, "DEAR": 1.00, "BIGLOT": 0.20, "FLAT": 0.20}
level = {"CHEAP": 1.0}
CALLS = []


def chain_for(sym, expiry=None):
    CALLS.append(sym)
    S = 522.0 * level.get(sym, 1.0)
    rows = []
    for k in (500.0, 510.0, 520.0, 530.0, 540.0):
        p = O.bs_price(S, k, 11 / 365, IVS[sym], True)
        rows.append({"strike": k, "ce_bid": round(p * 0.98, 2), "ce_ask": round(p * 1.02, 2), "ce_ltp": round(p, 2),
                     "pe_bid": 1.0, "pe_ask": 1.1, "pe_ltp": 1.05})
    return {"expiry": expiry or "24-Nov-2026", "spot": S, "atm": min((500.0, 510.0, 520.0, 530.0, 540.0), key=lambda k: abs(k - S)), "strikes": rows}


store._write(store.state_path(O.WATCH), WATCH)
PRICES = {"CHEAP": 522.0, "DEAR": 522.0, "BIGLOT": 522.0, "FLAT": 499.0}
s = O.step(at("2026-11-13", "14:47"), chain_for, LOTS, PRICES, 25200.0)
t("before 15:00: nothing bought, no chain read", not s["now"] and not CALLS)
s = O.step(at("2026-11-13", "15:07"), chain_for, LOTS, PRICES, 25200.0)
held = {r["symbol"]: r for r in s["now"]}
t("15:07: the trending share with a cheap option is bought", list(held) == ["CHEAP"])
r = held.get("CHEAP", {})
t("it buys the strike one step in the money (ATM 520 -> 510 call)", r.get("strike") == 510.0 and r.get("kind") == "CE")
t("the record shows IV against the share's movement", r.get("iv_rv", 9) < O.IV_MAX and r.get("iv") and r.get("rv20"))
t("sold on the next trading day (Fri -> Mon)", O.store._read(O.store.state_path(O.PATH), {})["trades"]["2026-11-13_CHEAP"]["sell_on"] == "2026-11-16")
t("a non-trending share is not even looked up", "FLAT" not in CALLS)
t("dear option and too-big lot are not bought", "DEAR" in CALLS and "BIGLOT" in CALLS and "DEAR" not in held and "BIGLOT" not in held)
n = len(CALLS)
O.step(at("2026-11-13", "15:17"), chain_for, LOTS, PRICES, 25200.0)
t("the next run does not check the same shares again", len(CALLS) == n)

# --- stale watch list: no buying
store._write(store.state_path(O.PATH), {})
store._write(store.state_path(O.WATCH), {**WATCH, "as_of": "2026-11-11"})
s = O.step(at("2026-11-13", "15:07"), chain_for, LOTS, PRICES, 25200.0)
t("a watch list older than the last trading day is not used", not s["now"])
store._write(store.state_path(O.WATCH), WATCH)
store._write(store.state_path(O.PATH), {})
O.step(at("2026-11-13", "15:07"), chain_for, LOTS, PRICES, 25200.0)

# --- Monday: held, priced once an hour, sold from 15:00
CALLS.clear(); level["CHEAP"] = 1.03
s = O.step(at("2026-11-16", "11:07"), chain_for, LOTS, {}, 25300.0)
r = s["now"][0]
t("Monday morning: still held, worth more now", r["status"] == "OPEN" and r["value_now"] > 0 and CALLS == ["CHEAP"])
O.step(at("2026-11-16", "11:17"), chain_for, LOTS, {}, 25300.0)
t("priced once an hour while held (no read at 11:17)", CALLS == ["CHEAP"])
s = O.step(at("2026-11-16", "15:07"), chain_for, LOTS, {}, 25300.0)
r = next(x for x in s["now"] if x["symbol"] == "CHEAP")
expect = round((r["exit_px"] - r["entry_px"]) * r["lot"] - (100 + 0.015 * (r["entry_px"] + r["exit_px"]) * r["lot"])) if r.get("exit_px") else None
t("15:07 Monday: sold at the next day's close, costs as in the research", r["status"] == "CLOSED" and r["pnl"] == expect and r["pnl"] > 0)
t("totals and the one-a-day view count it", s["trades"] == 1 and s["wins"] == 1 and s["one_a_day"]["trades"] == 1)

# --- runs missed on the selling day: sold at the last price seen
store._write(store.state_path(O.PATH), {})
level["CHEAP"] = 1.0
O.step(at("2026-11-13", "15:07"), chain_for, LOTS, PRICES, 25200.0)
level["CHEAP"] = 0.97
O.step(at("2026-11-16", "10:07"), chain_for, LOTS, {}, 25300.0)
s = O.step(at("2026-11-17", "09:17"), chain_for, LOTS, {}, 25300.0)
book = store._read(store.state_path(O.PATH), {})["trades"]["2026-11-13_CHEAP"]
t("no run on Monday afternoon: sold at Monday's last price on Tuesday", book["status"] == "CLOSED" and "missed" in book["reason"]
  and book["exit_at"].startswith("2026-11-16") and book["pnl"] < 0)

# --- one a day = each day's cheapest
cl = [{"date": "d1", "iv_rv": 0.7, "pnl": -500}, {"date": "d1", "iv_rv": 0.5, "pnl": 900}, {"date": "d2", "iv_rv": 0.6, "pnl": 100}]
t("one a day takes each day's cheapest option", [x["pnl"] for x in O.one_a_day(cl)] == [900, 100])
print("ALL PASS" if all(res) else "SOME FAILED")
