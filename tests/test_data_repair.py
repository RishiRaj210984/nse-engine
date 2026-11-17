"""Stale Yahoo bars: between midnight and 07:30 IST Yahoo's daily history often lacks the last NSE session (3-17 Nov
2026 most morning plans used a NIFTY close one session old, and the stock picks old share prices). The missing bar
is filled from NSE; a late copy of the evening job is skipped; the overnight watch list never goes backwards.
Fake NSE, no network."""
import os, sys, tempfile
from datetime import date, datetime
import numpy as np
import pandas as pd
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))
import store, data, jobs, overnight  # noqa: E402
from config import IST  # noqa: E402

res = []
def t(name, c): res.append(c); print("PASS" if c else "FAIL", name)
at = lambda d, hm: datetime.fromisoformat(f"{d}T{hm}:00").replace(tzinfo=IST)

t("before the open on Tue 17 Nov the last session is Mon 16", data.last_session(at("2026-11-17", "07:13")) == date(2026, 11, 16))
t("after the close it is the same day", data.last_session(at("2026-11-17", "16:18")) == date(2026, 11, 17))
t("on Monday morning it is Friday", data.last_session(at("2026-11-16", "07:13")) == date(2026, 11, 13))
t("on a Saturday it is Friday", data.last_session(at("2026-11-14", "13:00")) == date(2026, 11, 13))


def frame(days, start=100.0):
    idx = pd.DatetimeIndex([pd.Timestamp(d) for d in days])
    c = start + np.arange(len(idx), dtype=float)
    return pd.DataFrame({"Open": c, "High": c + 1, "Low": c - 1, "Close": c, "Volume": 1000.0}, index=idx)


OLD = ["2026-11-10", "2026-11-11", "2026-11-12", "2026-11-13"]
data.nse_index_bars = lambda: {"NIFTY 50": (date(2026, 11, 16), 25206.2, 25337.6, 25172.1, 25291.1),
                               "INDIA VIX": (date(2026, 11, 16), 15.9, 16.0, 15.5, 15.68)}
asked = []
data.nse_share_bars = lambda d: asked.append(d) or {"RELIANCE": (1339.7, 1347.3, 1333.8, 1344.8, 7653528.0)}
H = {"^NSEI": frame(OLD), "^INDIAVIX": frame(OLD, 15), "RELIANCE.NS": frame(OLD), "^GSPC": frame(OLD),
     "ABB.NS": frame(OLD + ["2026-11-15"]), "TCS.NS": frame(OLD + ["2026-11-16"])}
filled = data.repair_nse(H, at("2026-11-17", "07:13"))
t("NIFTY's missing Monday close is filled from NSE",
  H["^NSEI"].index[-1] == pd.Timestamp("2026-11-16") and H["^NSEI"]["Close"].iloc[-1] == 25291.1)
t("a share's missing Monday bar comes from NSE's cash-market file for that day",
  H["RELIANCE.NS"]["Close"].iloc[-1] == 1344.8 and H["RELIANCE.NS"]["Volume"].iloc[-1] == 7653528.0 and asked[0] == date(2026, 11, 16))
t("non-NSE tickers are left alone", H["^GSPC"].index[-1] == pd.Timestamp("2026-11-13"))
t("a Sunday-dated bar is dropped (and the session filled if NSE has it)", pd.Timestamp("2026-11-15") not in H["ABB.NS"].index)
t("a ticker that already has the session is not touched", "TCS.NS" not in filled and len(H["TCS.NS"]) == 5)
t("the filled list is reported", set(filled) == {"^NSEI", "^INDIAVIX", "RELIANCE.NS"})
H2 = {"^NSEI": frame(OLD)}
data.nse_index_bars = lambda: {"NIFTY 50": (date(2026, 11, 13), 1, 1, 1, 1)}
t("NSE's list for another day is not used", not data.repair_nse(H2, at("2026-11-17", "07:13")) and H2["^NSEI"].index[-1] == pd.Timestamp("2026-11-13"))
t("the model's 'prior' day is then Monday, not Friday",
  H["^NSEI"][H["^NSEI"].index < pd.Timestamp("2026-11-17")].index[-1] == pd.Timestamp("2026-11-16"))

# --- a late copy of the evening job is skipped before it touches anything
store.now_ist = lambda: at("2026-11-17", "02:11")
jobs.review.run = lambda: (_ for _ in ()).throw(AssertionError("must not run"))
os.environ.pop("POSTCLOSE_ANYTIME", None)
r = jobs.postclose()
t("an evening job starting at 02:11 is skipped", "skipped" in r)

# --- the overnight watch list never goes backwards
tmp = tempfile.mkdtemp()
store.state_path = lambda n: os.path.join(tmp, n)
store._write(store.state_path(overnight.WATCH), {"as_of": "2026-11-16", "nifty": {}, "stocks": {"X": {}}})
import nse_prices  # noqa: E402
nse_prices.lots = lambda: {"AAA": 100}
days = pd.bdate_range("2026-08-01", "2026-11-13")
data.history = lambda tickers, period="1y", ttl=None: {"AAA.NS": frame(days), "^NSEI": frame(days, 25000)}
msg = overnight.refresh_watch()
t("older prices do not replace a newer watch list", "kept" in msg and store._read(store.state_path(overnight.WATCH), {})["as_of"] == "2026-11-16")
w = overnight.make_watch({"AAA.NS": frame(days[:-1]), "BBB.NS": frame(days), "^NSEI": frame(days, 25000)}, ["AAA", "BBB"])
t("a share whose last bar is older than NIFTY's is left out", list(w["stocks"]) == ["BBB"] and w["as_of"] == "2026-11-13")
print("ALL PASS" if all(res) else "SOME FAILED")
