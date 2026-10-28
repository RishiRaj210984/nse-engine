"""Paper book on real NSE prices: entry at the real 10:15-10:35 price, target/stop/expiry on real values,
estimate only as a fallback; a position entered on estimates is re-based once at its first real price."""
import os, sys, json, tempfile
from datetime import date
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))
import pandas as pd  # noqa: E402
import store, review as R, live  # noqa: E402
from config import IST  # noqa: E402

tmp = tempfile.mkdtemp()
store.state_path = lambda n: os.path.join(tmp, n)
res = []
def t(name, c): res.append(c); print("PASS" if c else "FAIL", name)
LEGS = [{"action": "SELL", "strike": 23300, "type": "CE", "est_premium": 30}, {"action": "BUY", "strike": 23550, "type": "CE", "est_premium": 10},
        {"action": "SELL", "strike": 22500, "type": "PE", "est_premium": 45}, {"action": "BUY", "strike": 22250, "type": "PE", "est_premium": 15}]
ARCH = {"bias": {"options": {"NIFTY": {"expiry": "2026-11-03", "lot_size": 65, "iv_used_pct": 16.0,
                                      "strategies": [{"name": "Iron Condor", "legs": LEGS}]}}},
        "plan": {"options": [{"index": "NIFTY", "decision": "TRADE"}]}}
KEY = live.legs_key("NIFTY", "2026-11-03", LEGS)
bars = lambda day, lvl=22900: (lambda idx: pd.DataFrame({"Open": lvl, "High": lvl + 5, "Low": lvl - 5, "Close": lvl}, index=idx))(
    pd.date_range(f"{day} 09:15", f"{day} 15:25", freq="5min", tz=IST))
def marks(day, rows):
    json.dump({day: {KEY: rows}}, open(os.path.join(tmp, "paper_marks.json"), "w"))

# entry at the real price; real values reach the target at 13:00
marks("2026-10-28", [["10:20", 22900, -20.0, -45.0, 4], ["11:00", 22910, -16.0, -38.0, 4], ["12:00", 22905, -12.0, -30.0, 4],
                     ["13:00", 22900, -8.0, -20.0, 4], ["14:00", 22900, -7.0, -18.0, 4]])
out = R.paper_book(date(2026, 10, 28), ARCH, {"NIFTY": bars("2026-10-28"), "BANKNIFTY": None}, None)
c = out["closed"][-1] if out["closed"] else {}
t("entry uses the real premium (65 x 65 = Rs 4,225)", c.get("credit_rs") == 4225)
t("target booked on real prices at 13:00", "TARGET" in c.get("reason", "") and "13:00" in c.get("reason", ""))
t("result = (65 - 28) x 65 - costs = Rs 2,005", c.get("pnl") == round((65 - 28) * 65) - 400)

# no real prices that day: the estimate is used, marked as such
os.remove(os.path.join(tmp, "paper_book.json")); marks("2026-10-28", [])
out = R.paper_book(date(2026, 10, 28), ARCH, {"NIFTY": bars("2026-10-28"), "BANKNIFTY": None}, None)
t("without real prices: entry on estimate, clearly labelled", any("estimated prices" in x for x in out["days"][0]["detail"]))
# next day real prices appear: re-based once, then valued on real prices
marks("2026-10-29", [["09:20", 22900, -15.0, -40.0, 4], ["10:00", 22900, -15.0, -40.0, 4], ["11:00", 22900, -15.0, -40.0, 4]])
out = R.paper_book(date(2026, 10, 29), {"bias": {"options": {}}, "plan": {"options": []}}, {"NIFTY": bars("2026-10-29"), "BANKNIFTY": None}, None)
t("re-based once at the first real price", any("re-based" in x for x in out["days"][0]["detail"]))
o = out["open"][0] if out["open"] else {}
book = json.load(open(os.path.join(tmp, "paper_book.json")))
p = book["open"]["NIFTY"]
t("value after re-base follows the real prices (unchanged real -> value equals the 09:20 value)", p["basis"] == "NSE" and o.get("mark") == round((p["realized"] + sum({"CE": -15.0, "PE": -40.0}[g["legs"][0]["type"]] - g["v0"] for g in p["groups"])) * 65))
# today's paper trade, live (before the evening review)
from datetime import datetime
os.remove(os.path.join(tmp, "paper_book.json")) if os.path.exists(os.path.join(tmp, "paper_book.json")) else None
BIAS = {"options": {"NIFTY": {**ARCH["bias"]["options"]["NIFTY"], "setup": {"decision": "TRADE"}}}}
marks("2026-10-28", [["10:10", 22890, -21.0, -46.0, 4], ["10:20", 22900, -20.0, -45.0, 4], ["11:00", 22910, -16.0, -38.0, 4]])
x = live.paper_today(BIAS, datetime(2026, 10, 28, 11, 0, tzinfo=IST))[0]
t("today: entered at the first real price from 10:15 (10:20), premium Rs 4,225", x.get("entered_at") == "10:20" and x["credit_rs"] == 4225)
t("today: value now = (65 - 54) x 65 = Rs 715", x["value_now"] == 715 and x["status"] == "HOLD")
marks("2026-10-28", [["10:05", 22890, -21.0, -46.0, 4]])
t("before 10:15: waiting", live.paper_today(BIAS, datetime(2026, 10, 28, 10, 10, tzinfo=IST))[0]["status"] == "WAITING")
marks("2026-10-28", [["10:20", 23400, -20.0, -45.0, 4]])
t("index outside the short strikes at entry: not entered", live.paper_today(BIAS, datetime(2026, 10, 28, 11, 0, tzinfo=IST))[0]["status"] == "NOT ENTERED")
print("ALL PASS" if all(res) else "SOME FAILED")
