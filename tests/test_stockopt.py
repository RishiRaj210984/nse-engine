"""4th trade (paper): stock calls and puts on the plan's stock picks, bought when the share crosses its entry
between 09:30 and 10:30, one lot in each budget (up to Rs 5k, 5-10k, 10-15k: the strike nearest the money whose
lot fits), exited by the bought-option rules (stop -30%, cost after +20%, trail, +60%, 15:15)."""
import os, sys, tempfile
from datetime import date, datetime
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))
import store, stockopt as S  # noqa: E402
from config import IST  # noqa: E402

res = []
def t(name, c): res.append(c); print("PASS" if c else "FAIL", name)

tmp = tempfile.mkdtemp()
store.state_path = lambda n: os.path.join(tmp, n)
plan = {"session": "2026-11-12", "stock_options": [
    {"symbol": "HAVELLS", "kind": "CE", "side": "BUY", "trigger": 1159.5, "stop": 1117.7, "t1": 1209.7},
    {"symbol": "ONGC", "kind": "PE", "side": "SELL", "trigger": 233.5, "stop": 240.0, "t1": 227.0},
    {"symbol": "IDEA", "kind": "CE", "side": "BUY", "trigger": 12.7, "stop": 12.2, "t1": 13.4}]}
LOTS = {"HAVELLS": 500, "ONGC": 2250, "IDEA": 71475}
# option prices by strike (HAVELLS calls, ONGC puts, IDEA calls); `mult` moves a whole chain up or down
BOOK = {"HAVELLS": (1160.0, 1160, {1140: 26.0, 1160: 20.0, 1180: 11.0, 1200: 5.0, 1220: 2.0}),
        "ONGC": (232.5, 233, {227.5: 1.6, 230: 2.6, 232.5: 4.0, 235: 5.6, 237.5: 7.4}),
        "IDEA": (13.0, 12.7, {13.0: 0.40, 14.0: 0.10})}
mult = {"HAVELLS": 1.0, "ONGC": 1.0, "IDEA": 1.0}
WIDE = {("IDEA", 14.0)}   # quote 20% wide: not used


CALLS = []


def chain_for(sym, expiry=None):
    CALLS.append(sym)
    atm, spot, pxs = BOOK[sym]
    rows = []
    for k, p in pxs.items():
        p = round(p * mult[sym], 2)
        w = 0.1 if (sym, k) in WIDE else 0.03
        rows.append({"strike": float(k), "ce_bid": round(p * (1 - w), 4), "ce_ask": round(p * (1 + w), 4), "ce_ltp": p,
                     "pe_bid": round(p * (1 - w), 4), "pe_ask": round(p * (1 + w), 4), "pe_ltp": p})
    return {"expiry": expiry or "24-Nov-2026", "spot": spot, "atm": atm, "strikes": rows}


def run(day, hm, quotes):
    plan["session"] = day
    y, mo, d = map(int, day.split("-")); h, m = map(int, hm.split(":"))
    s = S.step(datetime(y, mo, d, h, m, tzinfo=IST), plan, quotes, chain_for, LOTS)
    return s, {(r["symbol"], r.get("tier")): r for r in s["today"]}

q = lambda last, lo, hi, op: {"last": last, "low": lo, "high": hi, "open": op}
D1 = "2026-11-12"
s, R = run(D1, "09:20", {"HAVELLS.NS": q(1165, 1150, 1166, 1152)})
t("nothing is bought before 09:30", not s["today"])
s, R = run(D1, "09:40", {"HAVELLS.NS": q(1162, 1150, 1166, 1152), "ONGC.NS": q(234, 233.8, 236, 235), "IDEA.NS": q(12.8, 12.5, 12.9, 12.6)})
h5, h10, h15 = R[("HAVELLS", "5K")], R[("HAVELLS", "10K")], R[("HAVELLS", "15K")]
t("call bought in all three budgets once the share is above its entry", all(x["status"] == "OPEN" for x in (h5, h10, h15)))
t("each budget takes the strike nearest the money that fits: 1200 (Rs 2,500), 1160 (Rs 10,000), 1140 (Rs 13,000)",
  (h5["strike"], h5["premium"], h10["strike"], h10["premium"], h15["strike"], h15["premium"]) == (1200, 2500, 1160, 10000, 1140, 13000))
t("real mid price and 1 lot", h10["entry_px"] == 20.0 and h10["lot"] == 500 and h10["tier"] == "10K")
t("put not bought while the share is above its entry", ("ONGC", "5K") not in R)
t("IDEA: no strike with a usable quote fits any budget (lot Rs 28,590; the cheap strike's quote is 20% wide): skipped, says why",
  all(R[("IDEA", c)]["status"] == "SKIPPED" for c in ("5K", "10K", "15K")) and "two-sided" in R[("IDEA", "5K")]["note"])
CALLS.clear()
s, R = run(D1, "10:00", {"HAVELLS.NS": q(1170, 1150, 1172, 1152), "ONGC.NS": q(233.0, 232.9, 236, 235)})
t("put bought in each budget when the share falls below its entry: 227.5, 232.5, 235",
  [R[("ONGC", c)]["strike"] for c in ("5K", "10K", "15K")] == [227.5, 232.5, 235.0])
t("one chain read per stock per run, not one per budget (3 HAVELLS trades + 3 ONGC buys = 2 reads)", sorted(CALLS) == ["HAVELLS", "ONGC"])

mult["HAVELLS"] = 1.3     # +30%: the stop moves to cost
run(D1, "10:30", {"HAVELLS.NS": q(1175, 1150, 1176, 1152), "ONGC.NS": q(232, 231, 236, 235)})
mult["HAVELLS"] = 0.995   # back below cost after being up 20%
s, R = run(D1, "11:00", {"HAVELLS.NS": q(1158, 1150, 1176, 1152), "ONGC.NS": q(232, 231, 236, 235)})
h = R[("HAVELLS", "10K")]
t("closed back at cost after being up 20%; costs Rs 100 + 1.5% each way",
  h["status"] == "CLOSED" and "cost" in h["reason"] and h["pnl"] == round((19.9 - 20) * 500 - (100 + 0.015 * 39.9 * 500)))
t("every budget follows its own option", R[("HAVELLS", "5K")]["status"] == "CLOSED" and R[("HAVELLS", "5K")]["exit_px"] == round(5.0 * 0.995, 2))
mult["ONGC"] = 0.675      # -32.5%
s, R = run(D1, "11:10", {"ONGC.NS": q(236, 231, 236.5, 235)})
t("stop at -30%", all(R[("ONGC", c)]["reason"] == "STOP -30%" for c in ("5K", "10K", "15K")))
t("summary totals count closed trades, overall and per budget",
  s["trades"] == 6 and s["total"] == sum(r["pnl"] for r in s["today"] if r["status"] == "CLOSED")
  and s["tiers"]["10K"]["trades"] == 2 and s["tiers"]["5K"]["total"] == R[("HAVELLS", "5K")]["pnl"] + R[("ONGC", "5K")]["pnl"])

# a new day: book at +60%, no trigger by 10:30
D2 = "2026-11-13"
mult.update(HAVELLS=1.0, ONGC=1.0)
run(D2, "09:35", {"HAVELLS.NS": q(1161, 1150, 1162, 1152), "ONGC.NS": q(234, 233.8, 236, 235), "IDEA.NS": q(12.6, 12.5, 12.65, 12.6)})
mult["HAVELLS"] = 1.625
s, R = run(D2, "10:45", {"HAVELLS.NS": q(1190, 1150, 1191, 1152), "ONGC.NS": q(234, 233.8, 236, 235), "IDEA.NS": q(12.8, 12.5, 12.9, 12.6)})
t("booked at +60%", R[("HAVELLS", "15K")]["status"] == "CLOSED" and R[("HAVELLS", "15K")]["reason"].startswith("BOOKED"))
t("no entry if it did not cross its entry by 10:30, in every budget",
  all(R[(sy, c)]["status"] == "NO ENTRY" for sy in ("ONGC", "IDEA") for c in ("5K", "10K", "15K")) and "did not cross" in R[("IDEA", "5K")]["note"])

mult["HAVELLS"] = 1.0
s, R = run("2026-11-16", "09:31", {"HAVELLS.NS": q(1240, 1215, 1241, 1215)})
t("skipped in every budget when the share opened above T1", all(R[("HAVELLS", c)]["status"] == "NO ENTRY" for c in ("5K", "10K", "15K")))
run("2026-11-17", "09:31", {"HAVELLS.NS": q(1161, 1150, 1162, 1152)})
mult["HAVELLS"] = 1.1
s, R = run("2026-11-17", "15:16", {"HAVELLS.NS": q(1165, 1150, 1170, 1152)})
t("time exit at 15:15 the same day", R[("HAVELLS", "10K")]["reason"] == "15:15 time exit")
t("open trades listed once per stock and expiry", S.open_keys(datetime(2026, 11, 17, 12, 0, tzinfo=IST)) == [])

# a trade from before the budgets (no tier) is left alone, still exits, and is counted in the budget its cost fits
import json
path = store.state_path(S.PATH)
book = json.load(open(path))
book["days"]["2026-11-18"] = {"STOCK_HAVELLS": {"date": "2026-11-18", "symbol": "HAVELLS", "kind": "CE", "trigger": 1159.5, "status": "OPEN",
                                                "strike": 1160.0, "expiry": "24-Nov-2026", "lot": 500, "entry_px": 20.0, "entry_at": "09:40",
                                                "premium": 10000, "peak": 0.0, "marks": [["09:40", 20.0]]}}
json.dump(book, open(path, "w"))
mult["HAVELLS"] = 1.7
s, R = run("2026-11-18", "10:00", {"HAVELLS.NS": q(1180, 1150, 1181, 1152)})
old = [r for r in s["today"] if r["symbol"] == "HAVELLS"]
t("old untiered trade: no new budget trades for that stock, and it still exits (booked)",
  len(old) == 1 and old[0]["status"] == "CLOSED" and old[0]["reason"].startswith("BOOKED") and old[0]["tier"] == "10K")

t("pick_tier estimates fall back to the last price (no quotes before the open)",
  S.pick_tier({"atm": 100.0, "strikes": [{"strike": 100.0, "ce_ltp": 8.0}]}, "CE", 500, 0, 5000, quoted=False)["premium"] == 4000
  and S.pick_tier({"atm": 100.0, "strikes": [{"strike": 100.0, "ce_ltp": 8.0}]}, "CE", 500, 0, 5000) is None)
t("expiry at least 7 days away", S.pick_expiry(["24-Nov-2026", "29-Dec-2026"], date(2026, 11, 18)) == "29-Dec-2026"
  and S.pick_expiry(["24-Nov-2026", "29-Dec-2026"], date(2026, 11, 17)) == "24-Nov-2026")
print("ALL PASS" if all(res) else "SOME FAILED")
