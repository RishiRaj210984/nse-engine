"""Guardian side of the 4th trade (stock calls/puts): the app's button sends ENTER STOCK_<SYMBOL>.
Paper by default; real only with LIVE_STOCKOPT=1 and the guardian live. Fake ICICI: no real orders."""
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
NOW = datetime(2026, 11, 12, 10, 5, tzinfo=IST)
SO = {"date": "2026-11-12", "today": [
    {"date": "2026-11-12", "symbol": "HAVELLS", "kind": "CE", "status": "OPEN", "strike": 1160.0, "expiry": "24-Nov-2026",
     "lot": 500, "entry_px": 20.0, "entry_at": "09:40", "trigger": 1159.5},
    {"date": "2026-11-12", "symbol": "ONGC", "kind": "PE", "status": "NO ENTRY", "note": "did not cross 233.5 between 09:30 and 10:30"}]}

class FB:
    def __init__(self, ask=21, held=None, blocked=0):
        self.ask, self.held, self.orders, self.blocked = ask, held or [], [], blocked
    def get_funds(self): return {"Success": {"allocated_fno": "50000", "block_by_trade_fno": str(self.blocked)}, "Status": 200}
    def get_names(self, exchange_code="", stock_code=""): return {"isec_stock_code": "HAVIND"}
    def get_quotes(self, stock_code="", exchange_code="", strike_price="", **k):
        return {"Success": [{"ltp": self.ask - 0.5, "best_bid_price": self.ask - 1, "best_offer_price": self.ask}], "Status": 200}
    def place_order(self, **kw):
        self.orders.append((kw["stock_code"], kw["action"], kw["strike_price"], kw["right"], kw["quantity"], kw["price"]))
        return {"Success": {"order_id": "O%d" % len(self.orders)}, "Status": 200}
    def get_order_detail(self, **k): return {"Success": [{"status": "Executed"}], "Status": 200}
    def cancel_order(self, **k): return {"Success": {}}
    def get_portfolio_positions(self): return {"Success": self.held}

res = []
def t(name, c): res.append(c); print("PASS" if c else "FAIL", name)
def cmd(fb, env, st, sym="HAVELLS", live=True, now=NOW, extra=()):
    guardian.fetch_stockopt = lambda: SO
    sent.clear(); guardian.handle_command(["ENTER", "STOCK_" + sym, "1", *extra], [fb], env, st, live, now)
    return sent[-1]

t("default: LIVE_STOCKOPT is 0 (paper)", base_env["LIVE_STOCKOPT"] == "0")
fb = FB(); title, msg = cmd(fb, base_env, {}, live=False)
t("paper mode: OK, says what it would buy with ICICI's code, no orders",
  "ENTER STOCK_HAVELLS: OK" in title and "would place BUY HAVELLS 1160 CE" in msg and "x500" in msg and "HAVIND" in msg and not fb.orders)
fb = FB(); title, msg = cmd(fb, base_env, {})
t("live guardian but LIVE_STOCKOPT=0: paper, says why, no orders", msg.startswith("Paper, not real") and not fb.orders)
env = base_env | {"LIVE_STOCKOPT": "1"}
fb, st = FB(), {}
title, msg = cmd(fb, env, st)
t("LIVE_STOCKOPT=1: one real BUY of the HAVELLS 1160 call, 500 qty, ICICI code, at the offer",
  msg.startswith("REAL stock option buy") and fb.orders == [("HAVIND", "buy", "1160", "call", "500", "21.00")])
t("real buy: alert says the money in trades against the limit", "Rs 10,500 of your Rs 15,000 limit" in msg)
t("a second tap the same day is refused", "already placed" in cmd(FB(), env, st)[1])
fb = FB(ask=31); title, msg = cmd(fb, env, {})
t("premium 31 x 500 = Rs 15,500 above Rs 15,000: not placed", "NOT DONE" in title and "above Rs 15,000" in msg and not fb.orders)
fb = FB(blocked=5000); title, msg = cmd(fb, env, {})
t("limit: Rs 5,000 margin held + Rs 10,500 = Rs 15,500 > Rs 15,000: not placed",
  "NOT DONE" in title and "total-money limit" in msg and "Rs 15,500" in msg and not fb.orders)
other = [{"product_type": "Options", "stock_code": "SBIN", "quantity": "750", "action": "Buy", "expiry_date": "24-Nov-2026",
          "strike_price": "960", "right": "Call", "average_price": "8", "ltp": "9"}]
fb = FB(held=other); title, msg = cmd(fb, env, {})
t("limit: another stock option held (Rs 6,000 paid) + Rs 10,500: not placed", "NOT DONE" in title and "total-money limit" in msg and not fb.orders)
fb = FB(); title, msg = cmd(fb, env, {}, sym="ONGC")
t("a pick that did not trigger: nothing placed, says why", "NOT DONE" in title and "did not cross" in msg and not fb.orders)
fb = FB(); title, msg = cmd(fb, env, {}, now=datetime(2026, 11, 12, 11, 45, tzinfo=IST))
t("after 11:30: refused", "NOT DONE" in title and "window" in msg and not fb.orders)
held = [{"product_type": "Options", "stock_code": "HAVIND", "quantity": "500", "action": "Buy", "expiry_date": "24-Nov-2026",
         "strike_price": "1180", "right": "Call", "average_price": "12", "ltp": "13"}]
fb = FB(held=held); title, msg = cmd(fb, env, {})
t("already holds a HAVELLS option on that expiry: refused", "NOT DONE" in title and "same expiry" in msg and not fb.orders)
# budgets (v53): the button sends ENTER STOCK_<SYMBOL>_<5K|10K|15K>
SO_T = {"date": "2026-11-12", "today": [
    {"date": "2026-11-12", "symbol": "HAVELLS", "kind": "CE", "tier": "5K", "status": "OPEN", "strike": 1200.0, "expiry": "24-Nov-2026",
     "lot": 500, "entry_px": 5.0, "entry_at": "09:40", "trigger": 1159.5},
    {"date": "2026-11-12", "symbol": "HAVELLS", "kind": "CE", "tier": "10K", "status": "OPEN", "strike": 1160.0, "expiry": "24-Nov-2026",
     "lot": 500, "entry_px": 20.0, "entry_at": "09:40", "trigger": 1159.5},
    {"date": "2026-11-12", "symbol": "HAVELLS", "kind": "CE", "tier": "15K", "status": "SKIPPED", "lot": 500,
     "note": "no CE strike with a live two-sided price costs Rs 10,000-15,000 for 1 lot (500 qty)"}]}
def cmd_t(fb, env, st, name, live=True):
    guardian.fetch_stockopt = lambda: SO_T
    sent.clear(); guardian.handle_command(["ENTER", name, "1"], [fb], env, st, live, NOW)
    return sent[-1]
t("split_stock reads the budget, and old names still work",
  guardian.split_stock("HAVELLS_5K") == ("HAVELLS", "5K") and guardian.split_stock("havells") == ("HAVELLS", None)
  and guardian.split_stock("BAJAJ-AUTO_15K") == ("BAJAJ-AUTO", "15K"))
fb = FB(ask=5.2); title, msg = cmd_t(fb, base_env, {}, "STOCK_HAVELLS_5K", live=False)
t("paper: the 5K tap would place the 1200 call (the 5K budget's strike), and names the budget",
  "would place BUY HAVELLS 1200 CE" in msg and "up to Rs 5,000 budget" in msg and not fb.orders)
fb, st = FB(ask=5.2), {}
title, msg = cmd_t(fb, env, st, "STOCK_HAVELLS_5K")
t("LIVE_STOCKOPT=1: the 5K tap buys the 1200 call", msg.startswith("REAL") and fb.orders == [("HAVIND", "buy", "1200", "call", "500", "5.20")])
title, msg = cmd_t(FB(), env, st, "STOCK_HAVELLS_10K")
t("then the 10K tap the same day is refused: one a day per stock (same expiry)", "NOT DONE" in title and "One a day per stock" in msg)
title, msg = cmd_t(FB(), env, {}, "STOCK_HAVELLS_15K")
t("a budget the tracker skipped: nothing placed, says why", "NOT DONE" in title and "Rs 10,000-15,000 budget" in msg and "two-sided" in msg)
t("evening line shows each budget's total", "By budget: 5K Rs +300 (1, 1 won)" in guardian.stockopt_line(
    {"today": [], "total": 300, "trades": 1, "wins": 1,
     "tiers": {"5K": {"total": 300, "trades": 1, "wins": 1}, "10K": {"total": 0, "trades": 0, "wins": 0}}}, "2026-11-12"))
t("evening alert line", "HAVELLS CE +Rs 1,200" in guardian.stockopt_line(
    {"today": [{"date": "2026-11-12", "symbol": "HAVELLS", "kind": "CE", "status": "CLOSED", "pnl": 1200, "reason": "BOOKED at +61%"}],
     "total": 1200, "trades": 1, "wins": 1}, "2026-11-12"))
print("ALL PASS" if all(res) else "SOME FAILED")
