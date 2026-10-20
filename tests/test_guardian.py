"""Offline test of the exit guardian: a fake Breeze client replays prices; no network, no orders."""
import os, sys, tempfile
from datetime import datetime
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))
import guardian
from config import IST

tmp = tempfile.mkdtemp()
guardian.STATE_FILE = os.path.join(tmp, "state.json"); guardian.STOP_FILE = os.path.join(tmp, "STOP")
guardian.ENV_FILE = os.path.join(tmp, "guardian.env")
open(guardian.ENV_FILE, "w").write("MODE=live\nMAX_DAILY_ORDERS=12\n")

def pos(strike, right, action, qty, avg, ltp, code="NIFTY", exp="27-Oct-2026"):
    return {"product_type": "Options", "stock_code": code, "expiry_date": exp, "strike_price": str(strike), "right": right,
            "action": action, "quantity": str(qty), "average_price": str(avg), "ltp": str(ltp)}

class FakeBreeze:
    def __init__(self, frames): self.frames, self.i, self.orders = frames, 0, []
    def get_portfolio_positions(self):
        f = self.frames[min(self.i, len(self.frames) - 1)]; self.i += 1
        return {"Success": f, "Status": 200}
    def square_off(self, **kw):
        self.orders.append(kw); return {"Success": {"order_id": "T%d" % len(self.orders)}, "Status": 200}

clock = lambda: datetime(2026, 10, 21, 11, 0, tzinfo=IST)
condor = lambda sc, bc, sp, bp: [pos(23300, "Call", "Sell", 65, 53.2, sc), pos(23550, "Call", "Buy", 65, 18.8, bc),
                                pos(22500, "Put", "Sell", 65, 44.7, sp), pos(22250, "Put", "Buy", 65, 14.2, bp)]
def check(name, frames, live, expect_orders, expect_status):
    if os.path.exists(guardian.STATE_FILE): os.remove(guardian.STATE_FILE)
    fb = FakeBreeze(frames); st = None
    for _ in frames: st = guardian.run(live_flag=live, breeze=fb, once=True, clock=clock)
    got = list(st.get("alerted", {}).values())
    ok = len(fb.orders) == expect_orders and (expect_status in got if expect_status else not got)
    print(("PASS" if ok else "FAIL"), name, "| alerts", got, "| orders", [(o["action"], o["strike_price"], o["right"]) for o in fb.orders])
    return ok

results = [
    check("condor quiet -> HOLD, no orders", [condor(40, 15, 35, 11)], True, 0, None),
    check("condor decays to 55% credit -> BOOK PROFIT, LIVE exits 4 legs, sold legs first",
          [condor(40, 15, 35, 11), condor(18, 6, 15, 4)], True, 4, "BOOK PROFIT"),
    check("condor blows up -> EXIT NOW", [condor(170, 30, 6, 2)], True, 4, "EXIT NOW"),
    check("PAPER mode never places orders", [condor(170, 30, 6, 2)], False, 0, "EXIT NOW"),
    check("bought call: +50% peak then falls to +20% -> trailing EXIT NOW",
          [[pos(22900, "Call", "Buy", 65, 100, 150)], [pos(22900, "Call", "Buy", 65, 100, 120)]], True, 1, "EXIT NOW"),
    check("bought call -31% -> stop-loss EXIT NOW", [[pos(22900, "Call", "Buy", 65, 100, 69)]], True, 1, "EXIT NOW"),
    check("bought call +65% -> BOOK PROFIT", [[pos(22900, "Call", "Buy", 65, 100, 165)]], True, 1, "BOOK PROFIT"),
]
fb = FakeBreeze([condor(170, 30, 6, 2)]); open(guardian.STOP_FILE, "w").write("x")
guardian.run(live_flag=True, breeze=fb, once=True, clock=clock); os.remove(guardian.STOP_FILE)
results.append(len(fb.orders) == 0); print("PASS" if not fb.orders else "FAIL", "STOP file blocks everything")
fb = FakeBreeze([condor(18, 6, 15, 4)]); guardian.run(live_flag=True, breeze=fb, once=True, clock=clock)
print("Exit order sequence:", [(o["action"], o["strike_price"], o["right"], o["quantity"]) for o in fb.orders])
print("ALL PASS" if all(results) else "SOME FAILED")

class RejectingBreeze(FakeBreeze):
    def square_off(self, **kw):
        self.orders.append(kw); return {"Success": None, "Status": 403, "Error": "Request not from registered static IP"}
if os.path.exists(guardian.STATE_FILE): os.remove(guardian.STATE_FILE)
msgs = []; real = guardian.notify; guardian.notify = lambda env, t, m, urgent=False: msgs.append((t, m, urgent))
rb = RejectingBreeze([condor(170, 30, 6, 2)]); guardian.run(live_flag=True, breeze=rb, once=True, clock=clock); guardian.notify = real
fail = [m for m in msgs if "EXIT FAILED" in m[0]]
ok = len(rb.orders) == 1 and fail and "EXIT BY HAND NOW" in fail[0][1] and fail[0][2]
print("PASS" if ok else "FAIL", "rejected order -> stops after first leg + urgent 'EXIT BY HAND' alert |", fail[0][1][:150] if fail else msgs)
