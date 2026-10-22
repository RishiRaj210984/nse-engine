"""Sold structures still open on expiry day are closed at 10:15; other days they hold."""
import os, sys
from datetime import date
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))
import guardian  # noqa: E402

res = []
def t(name, cond): res.append(cond); print("PASS" if cond else "FAIL", name)

def condor(exp):   # credit 60, now worth 50: small profit, no target or stop yet
    mk = lambda side, k, avg, ltp: {"side": side, "strike": k, "right": "call", "avg": avg, "ltp": ltp, "qty": 65, "expiry": exp}
    return {"id": "c", "legs": [mk("sell", 23200, 80, 70), mk("buy", 23400, 20, 20)]}

st = {}
t("expiry day 10:00: hold", guardian.evaluate(condor("27-Oct-2026"), st, "10:00", today=date(2026, 10, 27))[0] == "HOLD")
s = guardian.evaluate(condor("27-Oct-2026"), st, "10:15", today=date(2026, 10, 27))
t("expiry day 10:15: exit, keeps the premium left", s[0] == "EXIT NOW" and "expiry day" in s[1] and s[2] > 0)
t("day before expiry 14:00: hold", guardian.evaluate(condor("27-Oct-2026"), st, "14:00", today=date(2026, 10, 26))[0] == "HOLD")
t("no date given: old behaviour (hold)", guardian.evaluate(condor("27-Oct-2026"), st, "11:00")[0] == "HOLD")
t("ISO expiry also understood", guardian.evaluate(condor("2026-10-27"), st, "11:00", today=date(2026, 10, 27))[0] == "EXIT NOW")
print("ALL PASS" if all(res) else "SOME FAILED")
