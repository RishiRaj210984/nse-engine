"""Offline tests: phone commands, PIN check, and automatic entry of the plan trade (fake ICICI)."""
import os, sys, tempfile
from datetime import datetime
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))
import guardian
from config import IST

tmp = tempfile.mkdtemp()
guardian.STATE_FILE = os.path.join(tmp, "s.json"); guardian.STOP_FILE = os.path.join(tmp, "STOP"); guardian.ENV_FILE = os.path.join(tmp, "g.env")
env = {"MODE": "live", "MAX_LOTS": "2", "TRAIL_PCT": "25", "MAX_DAILY_ORDERS": "12", "GUARDIAN_PIN": "839201", "NTFY_CMD_TOPIC": "x"}
sent = []; guardian.notify = lambda e, t, m, urgent=False: sent.append((t, m))
NOW = datetime(2026, 10, 21, 10, 20, tzinfo=IST)
LEGS = [{"action": "SELL", "strike": 23300, "type": "CE", "est_premium": 53.2}, {"action": "BUY", "strike": 23550, "type": "CE", "est_premium": 18.8},
        {"action": "SELL", "strike": 22500, "type": "PE", "est_premium": 44.7}, {"action": "BUY", "strike": 22250, "type": "PE", "est_premium": 14.2}]
PLAN = ({"options": {"NIFTY": {"expiry": "2026-10-27", "lot_size": 65, "strategies": [{"name": "Iron Condor", "legs": LEGS}]}}},
        {"session": "2026-10-21", "options": [{"index": "NIFTY", "decision": "TRADE"}, {"index": "BANKNIFTY", "decision": "CAUTION"}]})

class FB:
    def __init__(self, spot=22900, fill="Executed", reject_at=None): self.spot, self.fill, self.reject_at, self.orders = spot, fill, reject_at, []
    def get_quotes(self, stock_code="", exchange_code="", product_type="", strike_price="", **k):
        if exchange_code == "NSE": return {"Success": [{"ltp": self.spot}], "Status": 200}
        base = {"23300": 50, "23550": 17, "22500": 43, "22250": 13}[strike_price]
        return {"Success": [{"ltp": base, "best_bid_price": base - 0.5, "best_offer_price": base + 0.5}], "Status": 200}
    def place_order(self, **kw):
        self.orders.append((kw["action"], kw["strike_price"], kw["right"], kw["quantity"], kw["price"]))
        if self.reject_at is not None and len(self.orders) - 1 == self.reject_at: return {"Success": None, "Error": "static IP not registered"}
        return {"Success": {"order_id": "O%d" % len(self.orders)}, "Status": 200}
    def get_order_detail(self, exchange_code="", order_id=""): return {"Success": [{"status": self.fill}], "Status": 200}

guardian.time.sleep = lambda s: None
res = []
def t(name, cond): res.append(cond); print("PASS" if cond else "FAIL", name)

ok, msg = guardian.enter_trade(FB(), env, {}, "NIFTY", 1, False, NOW, PLAN)
t("PAPER entry: no orders, lists 4 legs BUYs first", ok and msg.startswith("PAPER") and msg.index("BUY 23550") < msg.index("SELL 23300"))
fb = FB(); ok, msg = guardian.enter_trade(fb, env, {}, "NIFTY", 1, True, NOW, PLAN)
t("LIVE entry: 4 orders, BUYs first, qty 65, buy at offer / sell at bid", ok and [o[0] for o in fb.orders] == ["buy", "buy", "sell", "sell"]
  and all(o[3] == "65" for o in fb.orders) and fb.orders[0][4] == "17.50" and fb.orders[2][4] == "49.50")
fb = FB(); ok, _ = guardian.enter_trade(fb, env, {}, "NIFTY", 5, True, NOW, PLAN)
t("lots capped at MAX_LOTS=2 -> qty 130", ok and all(o[3] == "130" for o in fb.orders))
fb = FB(); ok, msg = guardian.enter_trade(fb, env, {}, "BANKNIFTY", 1, True, NOW, PLAN)
t("refuses an index not marked TRADE", not ok and "not marked TRADE" in msg and not fb.orders)
fb = FB(); ok, msg = guardian.enter_trade(fb, env, {}, "NIFTY", 1, True, datetime(2026, 10, 21, 9, 40, tzinfo=IST), PLAN)
t("refuses outside 10:10-11:30", not ok and "window" in msg and not fb.orders)
fb = FB(spot=23400); ok, msg = guardian.enter_trade(fb, env, {}, "NIFTY", 1, True, NOW, PLAN)
t("refuses when spot is outside the short strikes", not ok and "outside" in msg and not fb.orders)
fb = FB(); ok, msg = guardian.enter_trade(fb, env, {}, "NIFTY", 1, True, datetime(2026, 10, 22, 10, 20, tzinfo=IST), PLAN)
t("refuses a plan for another day", not ok and "not today" in msg)
fb = FB(reject_at=2); ok, msg = guardian.enter_trade(fb, env, {}, "NIFTY", 1, True, NOW, PLAN)
t("rejected SELL after BUYs: stops, reports what was placed", not ok and "CHECK ICICI NOW" in msg and "BUY 23550" in msg and len(fb.orders) == 3)
fb = FB(fill="Ordered"); ok, msg = guardian.enter_trade(fb, env, {}, "NIFTY", 1, True, NOW, PLAN)
t("unfilled first leg: stops before any SELL", not ok and "not filled" in msg and len(fb.orders) == 1)

# commands: PIN check and paused state
guardian.requests.get = lambda url, params=None, timeout=0: type("R", (), {"text": "\n".join([
    '{"event":"message","id":"a1","message":"000000 EXIT ALL"}', '{"event":"message","id":"a2","message":"839201 status"}'])})()
st = {}; cmds = guardian.poll_commands(env, st)
t("wrong PIN ignored, right PIN accepted, cursor saved", cmds == [["STATUS"]] and st["cmd_since"] == "a2")
box = [FB()]; guardian.handle_command(["STOP"], box, env, {}, True, NOW); sent.clear()
guardian.handle_command(["ENTER", "NIFTY"], box, env, {}, True, NOW)
t("ENTER refused while paused", box[0].orders == [] and "paused" in sent[-1][0])
guardian.handle_command(["RESUME"], box, env, {}, True, NOW)
t("RESUME removes the pause", not os.path.exists(guardian.STOP_FILE))
print("ALL PASS" if all(res) else "SOME FAILED")
