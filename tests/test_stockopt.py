"""4th trade (paper): stock calls and puts on the plan's stock picks, bought when the share crosses its entry
between 09:30 and 10:30, exited by the bought-option rules (stop -30%, cost after +20%, trail, +60%, 15:15)."""
import os, sys, tempfile
from datetime import date, datetime
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))
import store, stockopt as S  # noqa: E402
from config import IST  # noqa: E402

res = []
def t(name, c): res.append(c); print("PASS" if c else "FAIL", name)

tmp = tempfile.mkdtemp()
store.state_path = lambda n: os.path.join(tmp, n)
DAY = "2026-11-12"
plan = {"session": DAY, "stock_options": [
    {"symbol": "HAVELLS", "kind": "CE", "side": "BUY", "trigger": 1159.5, "stop": 1117.7, "t1": 1209.7},
    {"symbol": "ONGC", "kind": "PE", "side": "SELL", "trigger": 233.5, "stop": 240.0, "t1": 227.0},
    {"symbol": "IDEA", "kind": "CE", "side": "BUY", "trigger": 12.7, "stop": 12.2, "t1": 13.4}]}
LOTS = {"HAVELLS": 500, "ONGC": 2250, "IDEA": 71475}
px = {"HAVELLS": 20.0, "ONGC": 4.0, "IDEA": 0.40}


def chain_for(sym, expiry=None):
    spot = {"HAVELLS": 1160, "ONGC": 233, "IDEA": 12.7}[sym]
    k = {"HAVELLS": 1160.0, "ONGC": 232.5, "IDEA": 13.0}[sym]
    p = px[sym]
    return {"expiry": expiry or "24-Nov-2026", "spot": spot, "atm": k,
            "strikes": [{"strike": k, "ce_bid": p - 0.1, "ce_ask": p + 0.1, "ce_ltp": p, "pe_bid": p - 0.1, "pe_ask": p + 0.1, "pe_ltp": p}]}


def run(hm, quotes):
    h, m = map(int, hm.split(":"))
    return S.step(datetime(2026, 11, 12, h, m, tzinfo=IST), plan, quotes, chain_for, LOTS)

q = lambda last, lo, hi, op: {"last": last, "low": lo, "high": hi, "open": op}
s = run("09:20", {"HAVELLS.NS": q(1165, 1150, 1166, 1152)})
t("nothing is bought before 09:30", not s["today"])
s = run("09:40", {"HAVELLS.NS": q(1162, 1150, 1166, 1152), "ONGC.NS": q(234, 233.8, 236, 235), "IDEA.NS": q(12.8, 12.5, 12.9, 12.6)})
rec = {r["symbol"]: r for r in s["today"]}
t("call bought when the share is above its entry: ATM strike, real mid price, 1 lot",
  rec["HAVELLS"]["status"] == "OPEN" and rec["HAVELLS"]["strike"] == 1160 and rec["HAVELLS"]["entry_px"] == 20.0 and rec["HAVELLS"]["lot"] == 500)
t("put not bought while the share is above its entry", "ONGC" not in rec)
t("1 lot above Rs 15,000 is skipped and says why (IDEA: 71,475 x 0.40)",
  rec["IDEA"]["status"] == "SKIPPED" and "28,590" in rec["IDEA"]["note"])
s = run("10:00", {"HAVELLS.NS": q(1170, 1150, 1172, 1152), "ONGC.NS": q(233.0, 232.9, 236, 235)})
t("put bought when the share falls below its entry", {r["symbol"]: r for r in s["today"]}["ONGC"]["status"] == "OPEN")

px["HAVELLS"] = 26.0   # +30%: stop moves to cost
run("10:30", {"HAVELLS.NS": q(1175, 1150, 1176, 1152), "ONGC.NS": q(232, 231, 236, 235)})
px["HAVELLS"] = 19.9   # back below cost after being up 20%
s = run("11:00", {"HAVELLS.NS": q(1158, 1150, 1176, 1152), "ONGC.NS": q(232, 231, 236, 235)})
h = {r["symbol"]: r for r in s["today"]}["HAVELLS"]
t("closed back at cost after being up 20%; costs Rs 100 + 1.5% each way",
  h["status"] == "CLOSED" and "cost" in h["reason"] and h["pnl"] == round((19.9 - 20) * 500 - (100 + 0.015 * 39.9 * 500)))
px["ONGC"] = 2.7       # -32.5%
s = run("11:10", {"ONGC.NS": q(236, 231, 236.5, 235)})
o = {r["symbol"]: r for r in s["today"]}["ONGC"]
t("stop at -30%", o["status"] == "CLOSED" and o["reason"] == "STOP -30%")
t("summary totals count closed trades", s["trades"] == 2 and s["wins"] == 0 and s["total"] == h["pnl"] + o["pnl"])

# a new day: book at +60%, time exit at 15:15, no trigger by 10:30
DAY = plan["session"] = "2026-11-13"
px.update(HAVELLS=20.0, ONGC=4.0)
def run2(hm, quotes):
    h, m = map(int, hm.split(":"))
    return S.step(datetime(2026, 11, 13, h, m, tzinfo=IST), plan, quotes, chain_for, LOTS)
run2("09:35", {"HAVELLS.NS": q(1161, 1150, 1162, 1152), "ONGC.NS": q(234, 233.8, 236, 235), "IDEA.NS": q(12.6, 12.5, 12.65, 12.6)})
px["HAVELLS"] = 32.5
s = run2("10:45", {"HAVELLS.NS": q(1190, 1150, 1191, 1152), "ONGC.NS": q(234, 233.8, 236, 235), "IDEA.NS": q(12.8, 12.5, 12.9, 12.6)})
r2 = {r["symbol"]: r for r in s["today"]}
t("booked at +60%", r2["HAVELLS"]["status"] == "CLOSED" and r2["HAVELLS"]["reason"].startswith("BOOKED"))
t("no entry if it did not cross its entry by 10:30 (a later cross does not count)",
  r2["ONGC"]["status"] == "NO ENTRY" and r2["IDEA"]["status"] == "NO ENTRY" and "did not cross" in r2["IDEA"]["note"])

DAY = plan["session"] = "2026-11-16"
px["HAVELLS"] = 20.0
def run3(hm, quotes):
    h, m = map(int, hm.split(":"))
    return S.step(datetime(2026, 11, 16, h, m, tzinfo=IST), plan, quotes, chain_for, LOTS)
s = run3("09:31", {"HAVELLS.NS": q(1240, 1215, 1241, 1215)})
t("skipped when the share opened above T1", {r["symbol"]: r for r in s["today"]}["HAVELLS"]["status"] == "NO ENTRY")
plan["stock_options"][0]["trigger"] = 1159.5
DAY = plan["session"] = "2026-11-17"
def run4(hm, quotes):
    h, m = map(int, hm.split(":"))
    return S.step(datetime(2026, 11, 17, h, m, tzinfo=IST), plan, quotes, chain_for, LOTS)
run4("09:31", {"HAVELLS.NS": q(1161, 1150, 1162, 1152)})
px["HAVELLS"] = 22.0
s = run4("15:16", {"HAVELLS.NS": q(1165, 1150, 1170, 1152)})
t("time exit at 15:15 the same day", {r["symbol"]: r for r in s["today"]}["HAVELLS"]["reason"] == "15:15 time exit")

t("expiry at least 7 days away", S.pick_expiry(["24-Nov-2026", "29-Dec-2026"], date(2026, 11, 18)) == "29-Dec-2026"
  and S.pick_expiry(["24-Nov-2026", "29-Dec-2026"], date(2026, 11, 17)) == "24-Nov-2026")
print("ALL PASS" if all(res) else "SOME FAILED")
