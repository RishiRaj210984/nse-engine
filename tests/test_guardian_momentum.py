"""Guardian side of the 3rd trade (NIFTY first-hour momentum buy): the app's button sends ENTER MOMENTUM.
Paper by default; real only with LIVE_MOMENTUM=1 and the guardian live. Fake ICICI: no real orders."""
import os, sys, tempfile
from datetime import datetime
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))
import guardian
from config import IST

tmp = tempfile.mkdtemp()
guardian.STATE_FILE = os.path.join(tmp, "s.json"); guardian.STOP_FILE = os.path.join(tmp, "STOP"); guardian.ENV_FILE = os.path.join(tmp, "g.env")
base_env = guardian.load_env() | {"MODE": "live", "GUARDIAN_PIN": "839201", "NTFY_CMD_TOPIC": "x"}
sent = []; guardian.notify = lambda e, t, m, urgent=False: sent.append((t, m))
guardian.time.sleep = lambda s: None
NOW = datetime(2026, 10, 29, 10, 25, tzinfo=IST)
SIG = {"date": "2026-10-29", "status": "OPEN", "kind": "CE", "strike": 22700, "expiry": "03-Nov-2026", "entry_px": 120,
       "entry_at": "10:20", "first_hour_pct": 0.62}

class FB:
    def __init__(self, ask=121, held=None, blocked=0, funds_ok=True):
        self.ask, self.held, self.orders, self.blocked, self.funds_ok = ask, held or [], [], blocked, funds_ok
    def get_funds(self):
        if not self.funds_ok: raise RuntimeError("funds service down")
        return {"Success": {"allocated_fno": "50000", "block_by_trade_fno": str(self.blocked)}, "Status": 200}
    def get_quotes(self, stock_code="", exchange_code="", strike_price="", **k):
        if exchange_code == "NSE": return {"Success": [{"ltp": 22705}], "Status": 200}
        return {"Success": [{"ltp": self.ask - 1, "best_bid_price": self.ask - 2, "best_offer_price": self.ask}], "Status": 200}
    def place_order(self, **kw):
        self.orders.append((kw["action"], kw["strike_price"], kw["right"], kw["quantity"], kw["price"], kw["expiry_date"]))
        return {"Success": {"order_id": "O%d" % len(self.orders)}, "Status": 200}
    def get_order_detail(self, **k): return {"Success": [{"status": "Executed"}], "Status": 200}
    def cancel_order(self, **k): return {"Success": {}}
    def get_portfolio_positions(self): return {"Success": self.held}

res = []
def t(name, c): res.append(c); print("PASS" if c else "FAIL", name)
def cmd(fb, env, st, live=True, now=NOW, sig=SIG, extra=()):
    guardian.fetch_momentum = lambda: sig
    sent.clear(); guardian.handle_command(["ENTER", "MOMENTUM", "1", *extra], [fb], env, st, live, now)
    return sent[-1]

t("default: LIVE_MOMENTUM is 0 (paper)", base_env["LIVE_MOMENTUM"] == "0")
fb = FB(); title, msg = cmd(fb, base_env, {}, live=False)
t("paper mode: OK, says what it would buy, no orders", "OK" in title and "would place BUY 22700 CE x65" in msg and not fb.orders)
fb = FB(); title, msg = cmd(fb, base_env, {})
t("live guardian but LIVE_MOMENTUM=0: paper, says why, no orders", msg.startswith("Paper, not real") and not fb.orders)

env = base_env | {"LIVE_MOMENTUM": "1"}
fb, st = FB(), {}
title, msg = cmd(fb, env, st)
t("LIVE_MOMENTUM=1: one real BUY of the 22700 CE, 65 qty, at the offer",
  "OK" in title and msg.startswith("REAL momentum buy") and fb.orders == [("buy", "22700", "call", "65", "121.00", "2026-11-03T06:00:00.000Z")])
t("real buy: alert says the money in trades against the limit", "Rs 7,865 of your Rs 15,000 limit" in msg)
t("recorded: a second tap the same day is refused", "already placed" in cmd(FB(), env, st)[1])
fb = FB(ask=260); title, msg = cmd(fb, env, {})
t("premium 260 x 65 = Rs 16,900 above Rs 15,000: not placed", "NOT DONE" in title and "above Rs 15,000" in msg and not fb.orders)
held = [{"product_type": "Options", "stock_code": "NIFTY", "quantity": "65", "action": "Sell", "expiry_date": "03-Nov-2026",
         "strike_price": "23200", "right": "Call", "average_price": "22", "ltp": "20"}]
fb = FB(held=held); title, msg = cmd(fb, env, {})
t("holds a NIFTY condor on the same expiry: refused (exits could not tell them apart)", "NOT DONE" in title and "same expiry" in msg and not fb.orders)
# total-money limit: what is already in open trades + this buy must stay within CAPITAL (Rs 15,000)
stock = [{"product_type": "Options", "stock_code": "HAVIND", "quantity": "500", "action": "Buy", "expiry_date": "24-Nov-2026",
          "strike_price": "1160", "right": "Call", "average_price": "20", "ltp": "21"}]
fb = FB(held=stock); title, msg = cmd(fb, env, {})
t("limit: a Rs 10,000 stock option held + Rs 7,865 = Rs 17,865 > Rs 15,000: not placed",
  "NOT DONE" in title and "total-money limit" in msg and "Rs 17,865" in msg and not fb.orders)
fb = FB(blocked=8000); title, msg = cmd(fb, env, {})
t("limit: Rs 8,000 margin held by ICICI + Rs 7,865: not placed", "NOT DONE" in title and "total-money limit" in msg and not fb.orders)
fb = FB(blocked=7000); title, msg = cmd(fb, env, {})
t("limit: Rs 7,000 held + Rs 7,865 = Rs 14,865 fits: placed", msg.startswith("REAL momentum buy") and len(fb.orders) == 1)
fb = FB(funds_ok=False); title, msg = cmd(fb, env, {})
t("limit: funds cannot be read: not placed (no real order on a guess)", "NOT DONE" in title and "cannot be checked" in msg and not fb.orders)
fb = FB(); title, msg = cmd(fb, env, {}, sig={"date": "2026-10-29", "status": "NO SIGNAL", "note": "first hour +0.27%, needs ±0.5%"})
t("no signal today: nothing placed, says why", "NOT DONE" in title and "+0.27%" in msg and not fb.orders)
fb = FB(); title, msg = cmd(fb, env, {}, now=datetime(2026, 10, 29, 12, 5, tzinfo=IST))
t("after 11:30: refused", "NOT DONE" in title and "window" in msg and not fb.orders)
fb = FB(); title, msg = cmd(fb, env, {}, now=datetime(2026, 10, 29, 19, 0, tzinfo=IST), extra=("DEMO",))
t("demo in the evening: OK, never an order", "OK" in title and msg.startswith("DEMO") and not fb.orders)

# the guardian's exits already treat a single bought option with the same rules as the paper tracker
leg = {"stock_code": "NIFTY", "expiry": "03-Nov-2026", "strike": 22700.0, "right": "Call", "side": "buy", "qty": 65, "avg": 120.0}
tr = lambda ltp: {"id": "NIFTY|03-Nov-2026|22700C", "legs": [dict(leg, ltp=ltp)]}
s = {}
t("exit rules: -31% -> EXIT NOW (stop)", guardian.evaluate(tr(82.0), s, "11:00")[0] == "EXIT NOW")
s = {}; guardian.evaluate(tr(150.0), s, "11:00")
t("exit rules: after +25%, back to cost -> EXIT NOW", guardian.evaluate(tr(119.0), s, "11:10")[0] == "EXIT NOW")
t("exit rules: +62% -> BOOK PROFIT", guardian.evaluate(tr(195.0), {}, "11:00")[0] == "BOOK PROFIT")
t("exit rules: 15:15 -> EXIT NOW", guardian.evaluate(tr(125.0), {}, "15:15")[0] == "EXIT NOW")
print("ALL PASS" if all(res) else "SOME FAILED")
