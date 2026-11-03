"""Comparison book: once a NIFTY condor has been Rs 1,000 up after costs, it closes when it falls back to Rs 0.
Your own book keeps its rules (target 55%, stop 1x credit, expiry day 10:15); BankNifty is not affected."""
import os, sys, json, tempfile
from datetime import date, datetime
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))
import pandas as pd  # noqa: E402
import store, review as R, live  # noqa: E402
from config import IST  # noqa: E402

tmp = tempfile.mkdtemp()
store.state_path = lambda n: os.path.join(tmp, n)
res = []
def t(name, c): res.append(c); print("PASS" if c else "FAIL", name)
LEGS = [{"action": "SELL", "strike": 23300, "type": "CE"}, {"action": "BUY", "strike": 23550, "type": "CE"},
        {"action": "SELL", "strike": 22500, "type": "PE"}, {"action": "BUY", "strike": 22250, "type": "PE"}]
def arch(name):
    return {"bias": {"options": {name: {"expiry": "2026-11-10", "lot_size": 65, "iv_used_pct": 16.0,
                                        "strategies": [{"name": "Iron Condor", "legs": LEGS}]}}},
            "plan": {"options": [{"index": name, "decision": "TRADE"}]}}
bars = lambda day: (lambda idx: pd.DataFrame({"Open": 22900, "High": 22905, "Low": 22895, "Close": 22900}, index=idx))(
    pd.date_range(f"{day} 09:15", f"{day} 15:25", freq="5min", tz=IST))
def marks(name, rows):
    json.dump({"2026-10-28": {live.legs_key(name, "2026-11-10", LEGS): rows}}, open(os.path.join(tmp, "paper_marks.json"), "w"))
def fresh():
    for f in ("paper_book.json", "paper_book_protect.json"):
        if os.path.exists(os.path.join(tmp, f)):
            os.remove(os.path.join(tmp, f))
def run(name, protect):
    return R.paper_book(date(2026, 10, 28), arch(name), {name: bars("2026-10-28")}, None, protect=protect)

# credit 65 x 65 = Rs 4,225; target 55% = Rs 2,324. At 11:00 the trade is (65 - 41) x 65 = Rs 1,560 up = Rs 1,160
# after costs; by 13:00 it is back to (65 - 72) x 65 = -Rs 455 = -Rs 855 after costs; then it recovers.
ROWS = [["10:20", 22900, -20.0, -45.0, 4], ["11:00", 22900, -14.0, -27.0, 4], ["12:00", 22900, -18.0, -35.0, 4],
        ["13:00", 22900, -24.0, -48.0, 4], ["14:00", 22900, -10.0, -25.0, 4]]
fresh(); marks("NIFTY", ROWS)
mine = run("NIFTY", False)
prot = run("NIFTY", True)
c = prot["closed"][-1] if prot["closed"] else {}
t("comparison book: NIFTY closed when the Rs 1,000+ profit fell back to Rs 0 (13:00)", "PROTECT" in c.get("reason", "") and "13:00" in c.get("reason", ""))
t("comparison result = (65 - 72) x 65 - costs = Rs -855", c.get("pnl") == -7 * 65 - 400)
t("your book is unchanged: still open, worth (65 - 35) x 65 = Rs 1,950", not mine["closed"] and mine["open"][0]["mark"] == 30 * 65)
t("the two books are separate files", os.path.exists(os.path.join(tmp, "paper_book_protect.json")) and os.path.exists(os.path.join(tmp, "paper_book.json")))

# never Rs 1,000 up: nothing changes
fresh(); marks("NIFTY", [["10:20", 22900, -20.0, -45.0, 4], ["11:00", 22900, -18.0, -40.0, 4], ["12:00", 22900, -21.0, -48.0, 4], ["13:00", 22900, -22.0, -50.0, 4]])
t("a trade never Rs 1,000 up is held as in your book", not run("NIFTY", True)["closed"])
# BankNifty: rule not used
fresh(); marks("BANKNIFTY", ROWS)
t("BankNifty is not affected by the rule", not run("BANKNIFTY", True)["closed"])

# the live run applies the same rule to today's real marks
v = lambda r: (r[2] + r[3]) + 65
hit = live.first_exit(ROWS[1:], v, 65, "2026-11-10", "2026-10-28", protect=(65, 400, None))
t("live: PROTECT at 13:00 for the comparison book", hit and hit[0] == "PROTECT" and hit[1] == "13:00")
t("live: no PROTECT without the rule (your book)", live.first_exit(ROWS[1:], v, 65, "2026-11-10", "2026-10-28") is None)
t("live: a best value from earlier days counts (Rs 1,200 yesterday, back to 0 at 12:00 today)",
  live.first_exit([["12:00", 22900, -30.0, -45.0, 4]], v, 65, "2026-11-10", "2026-10-28", protect=(65, 400, 1200))[0] == "PROTECT")

# the live run saves real prices for the comparison book's own strikes
fresh()
json.dump({"open": {"NIFTY": {"index": "NIFTY", "expiry": "2026-11-10", "legs": LEGS}}}, open(os.path.join(tmp, "paper_book_protect.json"), "w"))
os.remove(os.path.join(tmp, "paper_marks.json"))
chain = {"NIFTY": {"expiry": "10-Nov-2026", "strikes": [{"strike": k, "ce_ltp": 10.0, "pe_ltp": 10.0} for k in (22250, 22500, 23300, 23550)]}}
n = live.record_marks({"options": {}}, chain, {"^NSEI": {"last": 22900}, "^INDIAVIX": {"last": 15}}, datetime(2026, 10, 28, 11, 0, tzinfo=IST))
t("live: real prices saved for a trade only the comparison book holds", n == 1)
print("ALL PASS" if all(res) else "SOME FAILED")
