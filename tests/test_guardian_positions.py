"""Reading positions: a dropped ICICI connection (errno 104) is retried and alerted only if it keeps failing,
with words that match the cause; an error reply never counts as 'no positions'. Fake ICICI, no network."""
import os, sys, tempfile
from datetime import datetime
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))
import guardian  # noqa: E402
from config import IST  # noqa: E402

tmp = tempfile.mkdtemp()
guardian.STATE_FILE = os.path.join(tmp, "s.json"); guardian.STOP_FILE = os.path.join(tmp, "STOP"); guardian.ENV_FILE = os.path.join(tmp, "g.env")
guardian.time.sleep = lambda s: None
guardian.requests.get = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("no network in tests"))
sent = []; guardian.notify = lambda e, ti, m, urgent=False: sent.append((ti, m, urgent))
NOW = datetime(2026, 11, 13, 11, 0, tzinfo=IST)
res = []
def t(name, cond): res.append(cond); print("PASS" if cond else "FAIL", name)

DROP = ConnectionError("error in get_portfolio_positions(): Error while trying to make request GET "
                       "https://api.icicidirect.com/breezeapi/api/v1/portfoliopositions: [Errno 104] Connection reset by peer")


class FB:
    def __init__(self, replies):
        self.replies, self.calls = list(replies), 0
    def get_portfolio_positions(self):
        self.calls += 1
        r = self.replies.pop(0) if self.replies else {"Success": [], "Status": 200}
        if isinstance(r, Exception):
            raise r
        return r


fb = FB([DROP, DROP, {"Success": [], "Status": 200}])
t("a dropped connection is retried within the same check (worked on the 3rd try)",
  guardian.read_positions(fb) == {"Success": [], "Status": 200} and fb.calls == 3)
try:
    guardian.read_positions(FB([{"Success": None, "Status": 500, "Error": "Internal Server Error"}] * 3)); ok = False
except RuntimeError as e:
    ok = "500" in str(e)
t("an error reply is a failure, never 'no positions'", ok)
t("ICICI's 'No Data Found' means you hold nothing", guardian.read_positions(FB([{"Success": None, "Status": 500, "Error": "No Data Found"}])) == {"Success": []})

def run(fb):
    sent.clear()
    return guardian.run(live_flag=False, breeze=fb, once=True, clock=lambda: NOW)

st = run(FB([DROP] * 3))
t("first failed check (3 tries): no alert yet", not [s for s in sent if "positions" in s[0]] and st["pos_fails"] == 1)
st = run(FB([DROP] * 3))
alert = [s for s in sent if s[0] == "Guardian: cannot read positions"]
t("second failed check in a row: one urgent alert", len(alert) == 1 and alert[0][2])
t("the alert says it is a network drop, not the login, and what to do",
  "network drop" in alert[0][1] and "not your login" in alert[0][1] and "ICICI app" in alert[0][1] and "SESSION" not in alert[0][1])
st = run(FB([DROP] * 3))
t("third failure: no repeat alert", not [s for s in sent if "positions" in s[0]])
st = run(FB([{"Success": [], "Status": 200}]))
t("works again: a calm all-clear, and the count resets",
  [s[0] for s in sent if "positions" in s[0]] == ["Guardian: reading positions again"] and st["pos_fails"] == 0)
st = run(FB([{"Success": [], "Status": 200}]))
t("no all-clear when nothing had failed", not [s for s in sent if "positions" in s[0]])
t("a login problem says to send a new SESSION code",
  "SESSION" in guardian.positions_error_text(RuntimeError("Session key is expired"), 2))
print("ALL PASS" if all(res) else "SOME FAILED")
