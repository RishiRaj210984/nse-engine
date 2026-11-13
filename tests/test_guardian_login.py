"""Logging in: ICICI's real reason is shown instead of Breeze's "try again later", brief failures are retried
automatically, and a restart resumes today's session without spending the login code again. Fake ICICI, no network."""
import os, sys, tempfile, types
from datetime import datetime
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))
import guardian  # noqa: E402
from config import IST  # noqa: E402

tmp = tempfile.mkdtemp()
guardian.STATE_FILE = os.path.join(tmp, "s.json"); guardian.STOP_FILE = os.path.join(tmp, "STOP"); guardian.ENV_FILE = os.path.join(tmp, "g.env")
guardian.time.sleep = lambda s: None
sent = []; guardian.notify = lambda e, ti, m, urgent=False: sent.append((ti, m, urgent))
NOW = datetime(2026, 11, 13, 11, 0, tzinfo=IST)
DAY = NOW.date().isoformat()
res = []
def t(name, cond): res.append(cond); print("PASS" if cond else "FAIL", name)

HIDDEN = Exception("Unexpected error: Unable to retrieve customer details at the moment. Please try again later.")
plan = {"gen": [], "probe": ("refused", "Invalid session."), "funds": {"Success": {"x": 1}, "Status": 200}}
calls = {"gen": 0, "probe": 0, "funds": 0, "codes": []}


class FakeBreeze:
    def __init__(self, api_key):
        self.api_key = api_key
    def generate_session(self, api_secret, session_token):
        calls["gen"] += 1; calls["codes"].append(session_token)
        r = plan["gen"].pop(0) if plan["gen"] else None
        if r:
            raise r
        self.user_id, self.session_key = "U1", "K-" + session_token
    def get_stock_script_list(self): pass
    def get_funds(self):
        calls["funds"] += 1
        return plan["funds"]


sys.modules["breeze_connect.breeze_connect"] = types.SimpleNamespace(
    BreezeConnect=FakeBreeze, ApificationBreeze=lambda b: "handler")
guardian._breeze_class = lambda: FakeBreeze
real_probe = guardian.icici_login_reason
guardian.icici_login_reason = lambda env: (calls.__setitem__("probe", calls["probe"] + 1), plan["probe"])[1]
ENV = {"BREEZE_API_KEY": "k", "BREEZE_API_SECRET": "s", "NTFY_TOPIC": "x"}

# --- the code is cleaned up
t("a pasted address gives the code", guardian.clean_session_code("https://x/?apisession=12345678&a=1") == "12345678")
t("stray spaces and quotes are dropped", guardian.clean_session_code(' "1234 5678". ') == "12345678")

# --- a refused code: ICICI's own words, not "try again later", and no pointless retries
plan["gen"] = [HIDDEN]; calls["gen"] = 0
try:
    guardian.connect(dict(ENV, BREEZE_SESSION_TOKEN="111")); err = None
except guardian.LoginError as e:
    err = e
t("Breeze's hidden refusal is explained with ICICI's real reason",
  err and 'ICICI says: "Invalid session."' in str(err) and "try again later" not in str(err) and not err.temporary)
t("the advice is to get a new code from the login link", err and "login link" in str(err) and "new code" in str(err))
t("a refusal is checked once with ICICI, not hammered", calls["gen"] == 1)

# --- a brief network drop: retried, and works
plan["gen"] = [ConnectionError("Connection reset by peer"), None]; calls["gen"] = 0
b = guardian.connect(dict(ENV, BREEZE_SESSION_TOKEN="222"))
t("a network drop during login is retried and logs in", calls["gen"] == 2 and b.session_key == "K-222")

# --- ICICI's API unreachable for all tries: temporary, no new code needed
plan["gen"] = [ConnectionError("Max retries exceeded")] * 3; plan["probe"] = ("network", "ConnectTimeout")
try:
    guardian.connect(dict(ENV, BREEZE_SESSION_TOKEN="333")); err = None
except guardian.LoginError as e:
    err = e
t("unreachable API: says the app/website can still work and no new code is needed",
  err and err.temporary and "No new code needed" in str(err) and "app and website" in str(err))

# --- other reasons get matching advice
t("an expired code says so", "expired" in guardian.login_error_text("refused", "Resource not available."))
t("a wrong API key says so", "API key" in guardian.login_error_text("refused", "Public Key does not exist."))
t("an IP refusal points at the static IP", "static IP" in guardian.login_error_text("refused", "Request from unregistered IP"))

# --- SESSION command: refused code alerted with the reason
plan["gen"] = [HIDDEN]; plan["probe"] = ("refused", "Invalid session."); sent.clear(); st = {}
guardian.handle_command(["SESSION", "444"], [None], dict(ENV), st, False, NOW)
t("SESSION refused: one urgent alert with ICICI's reason", len(sent) == 1 and sent[0][0] == "Guardian: login failed"
  and "Invalid session" in sent[0][1] and sent[0][2])
t("a refused code is not retried in the background", "session_token" not in st)

# --- SESSION command when ICICI's API is briefly unreachable: kept, retried quietly, all-clear when it works
plan["gen"] = [ConnectionError("Connection reset by peer")] * 3; plan["probe"] = ("network", "ConnectionError")
sent.clear(); st = {}; box = [None]; env = dict(ENV)
guardian.handle_command(["SESSION", "555"], box, env, st, False, NOW)
t("unreachable at SESSION: code kept for the retry", st.get("session_token") == "555" and box[0] is None)
guardian.retry_login(box, env, st, NOW)
t("no retry before 5 minutes", box[0] is None)
st["login_tried"] -= 6 * 60; plan["gen"] = [None]; sent.clear()
guardian.retry_login(box, env, st, NOW)
t("retry after 5 minutes logs in and says so", box[0] is not None and [s[0] for s in sent] == ["Guardian: logged in"])
t("the session (not the code) is saved for today", st.get("breeze_session", {}).get("key") == "K-555"
  and st["breeze_session"]["day"] == DAY)

# --- a restart resumes the saved session without spending the code
calls["gen"] = 0; calls["funds"] = 0
b = guardian.login(dict(ENV, BREEZE_SESSION_TOKEN="555"), st, DAY)
t("restart: saved session resumed, code not used again", calls["gen"] == 0 and calls["funds"] == 1 and b.session_key == "K-555")
plan["funds"] = {"Success": None, "Status": 401, "Error": "Session expired"}
calls["gen"] = 0
b = guardian.login(dict(ENV, BREEZE_SESSION_TOKEN="555"), st, DAY)
t("a saved session ICICI no longer accepts falls back to the code", calls["gen"] == 1)
plan["funds"] = {"Success": {"x": 1}, "Status": 200}
t("yesterday's saved session is not used", guardian.resume_session(ENV, {"breeze_session": {"day": "2026-11-12", "user_id": "U", "key": "K"}}, DAY) is None)

# --- the probe never puts the code in its text
class R:
    status_code = 401
    def json(self): return {"Success": None, "Status": 401, "Error": "Invalid session 98765."}
guardian.requests.get = lambda *a, **k: R()
kind, why = real_probe(dict(ENV, BREEZE_SESSION_TOKEN="98765"))
t("ICICI's reply is read, with the code masked", kind == "refused" and "98765" not in why and "<code>" in why)
print("ALL PASS" if all(res) else "SOME FAILED")
