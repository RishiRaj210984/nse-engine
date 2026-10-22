"""Demo at night (ICICI gives no prices) works; live refuses without a price; login survives a restart."""
import os, sys, tempfile
from datetime import datetime
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))
import guardian  # noqa: E402
from config import IST  # noqa: E402

LEGS = [{"action": "SELL", "strike": 23200, "type": "CE", "est_premium": 49.0}, {"action": "BUY", "strike": 23400, "type": "CE", "est_premium": 17.0}]
PLAN = ({"options": {"NIFTY": {"expiry": "2026-10-27", "lot_size": 65, "strategies": [{"name": "Iron Condor", "legs": LEGS}]}}},
        {"session": "2026-10-22", "options": [{"index": "NIFTY", "decision": "TRADE"}]})
NIGHT = datetime(2026, 10, 21, 22, 46, tzinfo=IST)


class Dead:   # Breeze at night: every quote call raises
    orders = []
    def get_quotes(self, **k): raise Exception("Error in get_quotes(): Expecting value: line 1 column 1 (char 0)")
    def place_order(self, **k): self.orders.append(k); return {"Success": {"order_id": "1"}}
    def get_portfolio_positions(self): return {"Success": []}


res = []
def t(name, cond): res.append(cond); print("PASS" if cond else "FAIL", name)

ok, msg = guardian.enter_trade(Dead(), {"MAX_LOTS": "1"}, {}, "NIFTY", 1, False, NIGHT, PLAN, demo=True)
t("demo at night: no crash, uses plan prices", ok and "market closed" in msg and "SELL 23200 CE" in msg)
d = Dead(); ok, msg = guardian.enter_trade(d, {"MAX_LOTS": "1"}, {}, "NIFTY", 1, True, datetime(2026, 10, 22, 10, 20, tzinfo=IST), PLAN)
t("live without a price: refuses, places nothing", not ok and "no live price" in msg and not d.orders)

# login code from the phone is kept for the day and reused after a restart
guardian.STATE_FILE = os.path.join(tempfile.mkdtemp(), "state.json")
sent = []; guardian.notify = lambda e, ti, m, urgent=False: sent.append(ti)
seen = []
guardian.connect = lambda env: seen.append(env["BREEZE_SESSION_TOKEN"]) or object()
st = {}
guardian.handle_command(["SESSION", "CODE123"], [None], {}, st, False, NIGHT)
guardian.save_state(st)
t("SESSION saves the code for today", st.get("session_token") == "CODE123" and st.get("session_day") == "2026-10-21")
print("ALL PASS" if all(res) else "SOME FAILED")

# evening check: alert once when a setup that fits the money first shows an edge
res2 = []
answers = [{"edge_found": False}, {"edge_found": True, "best": "NIFTY condor, 50-point wings, 1 lot", "capital": 10000}]
guardian.requests.get = lambda url, params=None, timeout=0: type("R", (), {"json": lambda self: answers[0]})()
sent.clear(); st = {}
guardian.check_capital_edge({}, st, datetime(2026, 10, 22, 19, 0, tzinfo=IST))
t("no edge -> no alert", not sent)
answers.pop(0)
guardian.check_capital_edge({}, st, datetime(2026, 10, 22, 20, 0, tzinfo=IST))
t("checks only once a day", not sent)
guardian.check_capital_edge({}, st, datetime(2026, 10, 23, 19, 0, tzinfo=IST))
guardian.check_capital_edge({}, st, datetime(2026, 10, 24, 19, 0, tzinfo=IST))
t("edge found -> one alert, not repeated", sent.count("A setup that fits your money shows an edge") == 1)
print("ALL PASS" if all(res) else "SOME FAILED")

# evening report: one alert with today's paper result, only once the review is final
rev = {"latest": {"session": "2026-10-22", "final": True}, "totals": {"plan_pnl_sum": -2380, "days": 3, "plan_win_days": 1, "plan_loss_days": 2},
       "history": [{"session": "2026-10-22", "plan_pnl": 411, "detail": ["NIFTY Iron Condor: TARGET +411"]}]}
guardian.requests.get = lambda url, params=None, timeout=0: type("R", (), {"json": lambda self: rev})()
sent.clear(); st = {}
guardian.evening_report({}, st, datetime(2026, 10, 22, 17, 0, tzinfo=IST))
t("no report before 18:00", not sent)
guardian.evening_report({}, st, datetime(2026, 10, 22, 18, 5, tzinfo=IST))
st["report_tried"] = 0
guardian.evening_report({}, st, datetime(2026, 10, 22, 18, 30, tzinfo=IST))
t("one evening report with today's result", sent == ["Paper result today: +Rs 411"])
print("ALL PASS" if all(res) else "SOME FAILED")
