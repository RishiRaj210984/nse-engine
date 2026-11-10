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
    def __init__(self, ask=21, held=None):
        self.ask, self.held, self.orders = ask, held or [], []
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
t("a second tap the same day is refused", "already placed" in cmd(FB(), env, st)[1])
fb = FB(ask=31); title, msg = cmd(fb, env, {})
t("premium 31 x 500 = Rs 15,500 above Rs 15,000: not placed", "NOT DONE" in title and "above Rs 15,000" in msg and not fb.orders)
fb = FB(); title, msg = cmd(fb, env, {}, sym="ONGC")
t("a pick that did not trigger: nothing placed, says why", "NOT DONE" in title and "did not cross" in msg and not fb.orders)
fb = FB(); title, msg = cmd(fb, env, {}, now=datetime(2026, 11, 12, 11, 45, tzinfo=IST))
t("after 11:30: refused", "NOT DONE" in title and "window" in msg and not fb.orders)
held = [{"product_type": "Options", "stock_code": "HAVIND", "quantity": "500", "action": "Buy", "expiry_date": "24-Nov-2026",
         "strike_price": "1180", "right": "Call", "average_price": "12", "ltp": "13"}]
fb = FB(held=held); title, msg = cmd(fb, env, {})
t("already holds a HAVELLS option on that expiry: refused", "NOT DONE" in title and "same expiry" in msg and not fb.orders)
t("evening alert line", "HAVELLS CE +Rs 1,200" in guardian.stockopt_line(
    {"today": [{"date": "2026-11-12", "symbol": "HAVELLS", "kind": "CE", "status": "CLOSED", "pnl": 1200, "reason": "BOOKED at +61%"}],
     "total": 1200, "trades": 1, "wins": 1}, "2026-11-12"))
print("ALL PASS" if all(res) else "SOME FAILED")
