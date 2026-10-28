"""Offline tests: the one real test trade (Rs 15,000). Only NIFTY, only from LIVE_FROM, 1 lot, 150-point wings,
ICICI margin checked first, then every later entry is paper. Fake ICICI: no real orders."""
import os, sys, tempfile
from datetime import datetime
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))
import guardian
from config import IST

tmp = tempfile.mkdtemp()
guardian.STATE_FILE = os.path.join(tmp, "s.json"); guardian.STOP_FILE = os.path.join(tmp, "STOP"); guardian.ENV_FILE = os.path.join(tmp, "g.env")
env = guardian.load_env() | {"MODE": "live", "MAX_LOTS": "2", "GUARDIAN_PIN": "839201", "NTFY_CMD_TOPIC": "x"}
sent = []; guardian.notify = lambda e, t, m, urgent=False: sent.append((t, m))
guardian.time.sleep = lambda s: None
DAY = datetime(2026, 11, 4, 10, 20, tzinfo=IST)
LEGS = [{"action": "SELL", "strike": 23300, "type": "CE", "est_premium": 53.2}, {"action": "BUY", "strike": 23550, "type": "CE", "est_premium": 18.8},
        {"action": "SELL", "strike": 22500, "type": "PE", "est_premium": 44.7}, {"action": "BUY", "strike": 22250, "type": "PE", "est_premium": 14.2}]
def plan(session="2026-11-04", name="Iron Condor", legs=LEGS):
    return ({"options": {"NIFTY": {"expiry": "2026-11-10", "lot_size": 65, "strategies": [{"name": name, "legs": legs}]},
                         "BANKNIFTY": {"expiry": "2026-11-24", "lot_size": 30, "strategies": [{"name": "Iron Condor", "legs": [
                             {"action": "SELL", "strike": 55800, "type": "CE", "est_premium": 90}, {"action": "BUY", "strike": 56800, "type": "CE", "est_premium": 30},
                             {"action": "SELL", "strike": 52100, "type": "PE", "est_premium": 80}, {"action": "BUY", "strike": 51100, "type": "PE", "est_premium": 25}]}]}}},
            {"session": session, "options": [{"index": "NIFTY", "decision": "TRADE"}, {"index": "BANKNIFTY", "decision": "TRADE"}]})

class FB:
    def __init__(self, margin=9800, free=15000, margin_resp=None, spot=22900):
        self.margin, self.free, self.margin_resp, self.spot = margin, free, margin_resp, spot
        self.orders, self.margin_calls = [], []
    def get_quotes(self, stock_code="", exchange_code="", product_type="", strike_price="", **k):
        if exchange_code == "NSE": return {"Success": [{"ltp": self.spot if stock_code == "NIFTY" else 54000}], "Status": 200}
        base = {"23300": 50, "23450": 22, "22500": 43, "22350": 19}.get(strike_price, 20)
        return {"Success": [{"ltp": base, "best_bid_price": base - 0.5, "best_offer_price": base + 0.5}], "Status": 200}
    def margin_calculator(self, rows, exchange_code=""):
        self.margin_calls.append(rows)
        if self.margin_resp is not None: return self.margin_resp
        return {"Success": {"span_margin_required": str(self.margin * 0.8), "non_span_margin_required": str(self.margin * 0.2),
                            "order_margin": "0", "trade_margin": "0"}, "Status": 200, "Error": None}
    def get_funds(self): return {"Success": {"allocated_fno": str(self.free + 1000), "block_by_trade_fno": "1000"}, "Status": 200}
    def place_order(self, **kw):
        self.orders.append((kw["action"], kw["strike_price"], kw["right"], kw["quantity"], kw["price"]))
        return {"Success": {"order_id": "O%d" % len(self.orders)}, "Status": 200}
    def get_order_detail(self, exchange_code="", order_id=""): return {"Success": [{"status": "Executed"}], "Status": 200}
    def cancel_order(self, **k): return {"Success": {}}
    def get_portfolio_positions(self): return {"Success": []}

res = []
def t(name, cond): res.append(cond); print("PASS" if cond else "FAIL", name)
def enter(ix, fb, st, now=DAY, p=None):
    guardian.fetch_plan = lambda: p or plan(now.date().isoformat())
    sent.clear(); guardian.handle_command(["ENTER", ix, "2"], [fb], env, st, True, now)
    return sent[-1]

t("defaults: Rs 15,000, NIFTY, from 4 Nov, 150-point wings, 1 real trade",
  (env["CAPITAL"], env["LIVE_INDEX"], env["LIVE_FROM"], env["LIVE_WING"], env["LIVE_MAX_TRADES"]) == ("15000", "NIFTY", "2026-11-04", "150", "1"))
t("test legs: same sold strikes, bought 150 points beyond",
  [(l["action"], l["strike"]) for l in guardian.test_legs(LEGS, 150)] == [("SELL", 23300), ("BUY", 23450), ("SELL", 22500), ("BUY", 22350)])
t("test legs: a spread is not a condor -> None", guardian.test_legs(LEGS[:2], 150) is None)

# before 4 Nov: paper, even in live mode
fb, st = FB(), {}
title, msg = enter("NIFTY", fb, st, datetime(2026, 11, 3, 10, 20, tzinfo=IST))
t("3 Nov (before LIVE_FROM): paper, no orders, says why", not fb.orders and msg.startswith("Paper, not real: the real test starts 2026-11-04") and not st.get("live_trades"))

# BankNifty: always paper in the test
fb = FB(); title, msg = enter("BANKNIFTY", fb, {})
t("BANKNIFTY: paper only, no orders", not fb.orders and "BANKNIFTY is paper only" in msg)

# margin problems: nothing placed
for name, kw, word in (("margin above free F&O money", {"margin": 14000, "free": 9000}, "add funds"),
                       ("margin above Rs 15,000", {"margin": 21000, "free": 50000}, "above your Rs 15,000 limit"),
                       ("ICICI gives no margin figure", {"margin_resp": {"Success": None, "Error": "invalid", "Status": 500}}, "no margin figure")):
    fb, st = FB(**kw), {}
    title, msg = enter("NIFTY", fb, st)
    t(f"{name}: not placed, nothing bought", "NOT DONE" in title and word in msg and not fb.orders and not st.get("live_trades"))

# the real test trade
fb, st = FB(), {}
title, msg = enter("NIFTY", fb, st)
t("real test: 4 orders, 1 lot (65) even when 2 lots asked, BUYs first",
  "OK" in title and [o[0] for o in fb.orders] == ["buy", "buy", "sell", "sell"] and all(o[3] == "65" for o in fb.orders))
t("real test: bought strikes are 23450 CE and 22350 PE (150-point wings)", sorted(o[1] for o in fb.orders if o[0] == "buy") == ["22350", "23450"])
t("real test: ICICI margin asked for all 4 legs first, qty 65", len(fb.margin_calls) == 1 and len(fb.margin_calls[0]) == 4
  and all(r["quantity"] == "65" for r in fb.margin_calls[0]))
t("real test: alert says REAL TEST, margin and 'paper again'", msg.startswith("REAL TEST TRADE placed") and "ICICI margin Rs 9,800" in msg and "paper again" in msg)
t("real test: recorded once", len(st["live_trades"]) == 1 and st["live_trades"][0]["index"] == "NIFTY")

# after it: paper again
fb2 = FB(); title, msg = enter("NIFTY", fb2, st, datetime(2026, 11, 5, 10, 20, tzinfo=IST))
t("next day: paper again, no orders", not fb2.orders and "already done" in msg and len(st["live_trades"]) == 1)

# not a condor on the day: refused (no real spread)
fb, st = FB(), {}
title, msg = enter("NIFTY", fb, st, p=plan(name="Bull Put Spread", legs=LEGS[2:]))
t("plan is a spread: real test refused, nothing bought", "NOT DONE" in title and "Iron Condor only" in msg and not fb.orders)

# demo in live mode never places orders
fb = FB(); guardian.fetch_plan = lambda: plan()
sent.clear(); guardian.handle_command(["ENTER", "NIFTY", "1", "DEMO"], [fb], env, {}, True, datetime(2026, 11, 4, 19, 0, tzinfo=IST))
t("DEMO in live mode: allowed, no orders", sent[-1][1].startswith("DEMO") and not fb.orders)
print("ALL PASS" if all(res) else "SOME FAILED")
