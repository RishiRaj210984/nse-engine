"""Alerts cannot flood the phone: repeats are muted, max 20 per hour, quiet restarts."""
import os, sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))
import guardian  # noqa: E402

posts = []
guardian.requests.post = lambda url, data=None, timeout=0, headers=None: posts.append(headers["Title"])
env = {"NTFY_TOPIC": "x"}
res = []
def t(name, cond): res.append(cond); print("PASS" if cond else "FAIL", name)

for _ in range(5):
    guardian.notify(env, "Guardian started", "Mode: PAPER")
t("same alert 5 times -> sent once", posts.count("Guardian started") == 1)
for i in range(40):
    guardian.notify(env, f"alert {i}", "m")
t("at most 20 alerts per hour", len(posts) == 20)
print("ALL PASS" if all(res) else "SOME FAILED")
