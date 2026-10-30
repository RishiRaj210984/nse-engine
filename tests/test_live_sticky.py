"""Daytime paper values agree with the evening review: once a paper trade reaches its target or stop on real
NSE prices it stays booked there, even if prices come back (29 Oct 2026: NIFTY target at 12:50, back to +2,022
by 14:30). Uses the real saved marks of that day."""
import os, sys, json, copy, tempfile
from datetime import datetime
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))
import store, live  # noqa: E402
from config import IST  # noqa: E402

tmp = tempfile.mkdtemp()
store.state_path = lambda n: os.path.join(tmp, n)
res = []
def t(name, c): res.append(c); print("PASS" if c else "FAIL", name)
def put(name, obj): json.dump(obj, open(os.path.join(tmp, name), "w"))
at = lambda hm: datetime.fromisoformat(f"2026-10-29T{hm}:00").replace(tzinfo=IST)

# the NIFTY condor held into 29 Oct (entered 28 Oct, credit Rs 4,796), with that day's real NSE marks
POS = {"open": {"NIFTY": {"index": "NIFTY", "strategy": "Iron Condor", "expiry": "2026-11-03", "entry_session": "2026-10-28",
                          "lot": 65, "v_e": -73.782, "iv0": 0.16, "vix0": 15.0, "mark": 1690, "basis": "NSE", "realized": 0.0,
                          "credit0": 73.782, "credit_all": 73.782, "extra_cost": 0,
                          "legs": [{"action": "SELL", "strike": 23200, "type": "CE"}, {"action": "BUY", "strike": 23450, "type": "CE"},
                                   {"action": "SELL", "strike": 22300, "type": "PE"}, {"action": "BUY", "strike": 22050, "type": "PE"}],
                          "groups": [{"legs": [{"action": "SELL", "strike": 23200, "type": "CE"}, {"action": "BUY", "strike": 23450, "type": "CE"}], "v0": -15.807},
                                     {"legs": [{"action": "SELL", "strike": 22300, "type": "PE"}, {"action": "BUY", "strike": 22050, "type": "PE"}], "v0": -57.975}]}}}
KEY = live.legs_key("NIFTY", "2026-11-03", POS["open"]["NIFTY"]["legs"])
# real NSE marks of 29 Oct (state/paper_marks.json keeps only 15 days, so a copy of those rows)
real = [["12:40", 22852.9, -23.825, -11.225, 4], ["12:50", 22812.75, -18.35, -13.0, 4], ["13:00", 22833.7, -18.5, -11.975, 4],
        ["14:20", 22899.15, -27.825, -7.4, 4], ["14:30", 22933.6, -36.7, -5.975, 4]]
put("paper_book.json", POS)
Q = {"^NSEI": {"last": 22933.6}, "^INDIAVIX": {"last": 15.0}}

put("paper_marks.json", {"2026-10-29": {KEY: [r for r in real if r[0] <= "12:40"]}})
x = live.paper_open(Q, at("12:40"))[0]
t("12:40: still HOLD, valued at the latest real mark", x["status"] == "HOLD" and "reached_at" not in x)

put("paper_marks.json", {"2026-10-29": {KEY: [r for r in real if r[0] <= "14:30"]}})
x = live.paper_open(Q, at("14:30"))[0]
t("14:30: stays TARGET, reached 12:50 (prices came back to about +2,022)", x["status"] == "TARGET" and x["reached_at"] == "12:50")
t("booked value = the 12:50 value, +Rs 2,758 before costs", x["value_now"] == 2758)
t("result after Rs 400 costs = +Rs 2,358, same as the evening review", x["result_rs"] == 2358)
t("today's change = 2,358 - last evening's 1,690 = +Rs 668", x["since_last_evening"] == 668)

# Close all passes no chain: it must use the latest real mark, not a Black-Scholes estimate
put("paper_marks.json", {"2026-10-29": {KEY: [r for r in real if r[0] <= "12:40"]}})
x = live.paper_open(Q, at("12:45"))[0]
last = [r for r in real if r[0] <= "12:40"][-1]
t("no chain: valued at the latest saved real NSE mark (12:40)",
  x["at"] == "12:40" and x["value_now"] == round(((last[2] + 15.807) + (last[3] + 57.975)) * 65))

# stop is sticky too
p = copy.deepcopy(POS)
put("paper_book.json", p)
put("paper_marks.json", {"2026-10-29": {KEY: [["11:00", 22900, -40.0, -110.0, 4], ["11:10", 22900, -30.0, -60.0, 4]]}})
x = live.paper_open(Q, at("11:10"))[0]
t("loss reached 1x credit at 11:00: stays STOP after a bounce", x["status"] == "STOP" and x["reached_at"] == "11:00")

# today's new trade: target reached then given back, stays booked
BIAS = {"options": {"NIFTY": {"expiry": "2026-11-03", "lot_size": 65, "setup": {"decision": "TRADE"},
                              "strategies": [{"name": "Iron Condor", "legs": POS["open"]["NIFTY"]["legs"]}]}}}
put("paper_book.json", {"open": {}})
put("paper_marks.json", {"2026-10-29": {KEY: [["10:20", 22800, -20.0, -45.0, 4], ["12:00", 22800, -8.0, -20.0, 4], ["13:00", 22800, -16.0, -40.0, 4]]}})
x = live.paper_today(BIAS, at("13:00"))[0]
t("today's trade: TARGET at 12:00 stays, booked +Rs 2,405, after costs +Rs 2,005",
  x["status"] == "TARGET" and x["reached_at"] == "12:00" and x["value_now"] == 2405 and x["result_rs"] == 2005)
put("paper_marks.json", {"2026-10-29": {KEY: [["10:20", 22800, -20.0, -45.0, 4], ["13:00", 22800, -16.0, -40.0, 4]]}})
x = live.paper_today(BIAS, at("13:00"))[0]
t("today's trade below target: HOLD, no booking", x["status"] == "HOLD" and "result_rs" not in x)
# after the close (15:40 run) the chain shows post-close prices: value at the 15:30 mark, as the evening review
put("paper_book.json", POS)
put("paper_marks.json", {"2026-10-29": {KEY: [["15:20", 22900, -20.0, -45.0, 4], ["15:30", 22900, -18.0, -44.0, 4]]}})
live._chain_for = lambda chains, name, expiry: {"fake": True}
live.real_sides = lambda legs, ch, spot, T, vix: (-1.0, -1.0, None)
x = live.paper_open(Q, at("15:40"), chains={"x": 1})[0]
t("15:40: valued at the 15:30 mark, not the post-close chain",
  x["at"] == "15:30" and x["value_now"] == round(((-18.0 + 15.807) + (-44.0 + 57.975)) * 65))
x = live.paper_open(Q, at("15:20"), chains={"x": 1})[0]
t("15:20: in market hours the chain is used", x["at"] == "15:20" and x["value_now"] == round(((-1.0 + 15.807) + (-1.0 + 57.975)) * 65))
print("ALL PASS" if all(res) else "SOME FAILED")
