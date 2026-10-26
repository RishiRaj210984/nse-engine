"""Emergency 'Close all' and the emergency watch: paper starts the engine's paper close, live closes and
keeps retrying (also while paused), and unusual market moves alert once per reason a day."""
import os, sys, tempfile
from datetime import datetime
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))
import guardian  # noqa: E402
from config import IST  # noqa: E402

tmp = tempfile.mkdtemp()
guardian.STATE_FILE = os.path.join(tmp, "state.json"); guardian.STOP_FILE = os.path.join(tmp, "STOP")
guardian.time.sleep = lambda s: None
NOW = datetime(2026, 10, 26, 11, 0, tzinfo=IST)
res = []
def t(name, cond): res.append(cond); print("PASS" if cond else "FAIL", name)
sent = []; guardian.notify = lambda e, ti, m, urgent=False: sent.append((ti, m, urgent))
posts = []
guardian.requests.post = lambda url, headers=None, json=None, timeout=0: posts.append((url, json)) or type("R", (), {"status_code": 204})()
env = {"GITHUB_TOKEN": "x", "TRAIL_PCT": "25", "MAX_DAILY_ORDERS": "12"}

# paper: no ICICI login needed, starts the engine task
guardian.handle_command(["EXIT", "ALL"], [None], env, {}, False, NOW)
t("paper EXIT ALL starts the engine's paper close (no login needed)",
  posts and posts[-1][1]["inputs"]["task"] == "paperclose" and "paper" in sent[-1][0].lower() and sent[-1][2])

def pos(k, right, side, avg, ltp):
    return {"product_type": "Options", "stock_code": "NIFTY", "expiry_date": "27-Oct-2026", "strike_price": str(k), "right": right,
            "action": side, "quantity": "65", "average_price": str(avg), "ltp": str(ltp)}
class FB:
    def __init__(self): self.sq = []
    def get_portfolio_positions(self): return {"Success": [pos(23300, "Call", "Sell", 50, 30), pos(23550, "Call", "Buy", 18, 9),
                                                           pos(22500, "Put", "Sell", 44, 25), pos(22250, "Put", "Buy", 14, 7)]}
    def get_quotes(self, **k): return {"Success": [{"ltp": 20, "best_bid_price": 19.9, "best_offer_price": 20.1}]}
    def square_off(self, **k): self.sq.append((k["action"], k["strike_price"], k["order_type"])); return {"Success": {"order_id": "S"}}
    def get_order_detail(self, **k): return {"Success": [{"status": "Executed"}]}
    def cancel_order(self, **k): return {"Success": {}}

fb, st = FB(), {}
guardian.handle_command(["EXIT", "ALL"], [fb], env, st, True, NOW)
t("live EXIT ALL: all 4 legs closed with limit orders, sold legs first",
  [x[0] for x in fb.sq] == ["buy", "buy", "sell", "sell"] and all(x[2] == "limit" for x in fb.sq) and "CLOSED" in sent[-1][1])
t("live EXIT ALL: keeps closing on every pass today", st.get("exit_all") == "2026-10-26")
open(guardian.STOP_FILE, "w").write("x")
fb2 = FB(); guardian.handle_command(["EXIT", "ALL"], [fb2], env, {}, True, NOW)
t("works even while the guardian is paused", len(fb2.sq) == 4)
os.remove(guardian.STOP_FILE)
sent.clear()
guardian.handle_command(["EXIT", "ALL"], [None], env, {}, True, NOW)
t("live, not logged in: clear message", "not logged in" in sent[-1][0].lower())

# emergency watch
snap = {"indices": {"NIFTY": {"last": 22100, "chg_pct": -2.4}, "BANKNIFTY": {"last": 54000, "chg_pct": -1.2},
                    "INDIAVIX": {"last": 21, "chg_pct": 22.0}}, "paper_open": []}
guardian.requests.get = lambda url, params=None, timeout=0: type("R", (), {"json": lambda self: snap})()
sent.clear(); st = {}
guardian.emergency_watch(env, st, NOW)
t("big NIFTY fall + VIX jump: one urgent alert naming both", len(sent) == 1 and "NIFTY -2.4%" in sent[0][1] and "VIX" in sent[0][1] and sent[0][2])
st["emerg_checked"] = 0
guardian.emergency_watch(env, st, NOW)
t("same reasons are not repeated the same day", len(sent) == 1)
snap["indices"]["BANKNIFTY"]["chg_pct"] = -2.1; st["emerg_checked"] = 0
guardian.emergency_watch(env, st, NOW)
t("a new reason alerts again", len(sent) == 2 and "BANKNIFTY" in sent[1][1])
snap["indices"] = {"NIFTY": {"chg_pct": 0.3}, "INDIAVIX": {"chg_pct": 2}}; sent.clear()
guardian.emergency_watch(env, {}, NOW)
t("normal day: no alert", not sent)
print("ALL PASS" if all(res) else "SOME FAILED")
