"""The app's "next run" line: weekday runs, plus the Saturday 07:13 weekend refresh (Friday's US close)."""
import os, sys
from datetime import datetime
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))
import jobs  # noqa: E402
from config import IST  # noqa: E402

res = []
def t(name, c): res.append(c); print("PASS" if c else "FAIL", name)
nr = lambda s: jobs.next_run(datetime.fromisoformat(s).replace(tzinfo=IST))

t("Fri 23:00: next is the Saturday weekend refresh", nr("2026-10-30T23:00") == {"at": "2026-10-31T07:13:00+05:30", "name": "weekend refresh"})
t("Sat 08:00: next is Monday's midnight preview", nr("2026-10-31T08:00") == {"at": "2026-11-02T00:07:00+05:30", "name": "midnight preview"})
t("Sun 20:00: next is Monday's midnight preview", nr("2026-11-01T20:00")["at"] == "2026-11-02T00:07:00+05:30")
t("Thu 23:00: next is Friday's midnight preview (weekdays unchanged)", nr("2026-10-29T23:00") == {"at": "2026-10-30T00:07:00+05:30", "name": "midnight preview"})
t("Tue 10:00: next is the after-close learning", nr("2026-11-03T10:00")["name"] == "after-close learning")
print("ALL PASS" if all(res) else "SOME FAILED")
