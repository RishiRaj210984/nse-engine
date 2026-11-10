"""Bias model inputs: US moves are added up over every US session since the last NSE close."""
import os, sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))
import numpy as np, pandas as pd  # noqa: E402
import macro  # noqa: E402

res = []
def t(name, c): res.append(c); print("PASS" if c else "FAIL", name)

nse = pd.DatetimeIndex(["2026-11-05", "2026-11-06", "2026-11-09", "2026-11-11", "2026-11-12"])   # 10 Nov: NSE holiday
us = pd.DataFrame({"spx": [1.0, -2.0, 2.35, -0.7, 0.5], "ndx": 0.0, "tnx": 0.0, "crude": 0.0, "dxy": 0.0},
                  index=pd.DatetimeIndex(["2026-11-05", "2026-11-06", "2026-11-09", "2026-11-10", "2026-11-11"]))
s = macro.us_since_close(us, nse)
t("a US session dated the same day as NSE counts for the next NSE session", s.loc["2026-11-06", "spx"] == 1.0)
t("after the 10 Nov NSE holiday both Monday and Tuesday US sessions count", np.isclose(s.loc["2026-11-11", "spx"], 1.65))
t("first NSE day with no earlier US session: no move", s.loc["2026-11-05", "spx"] == 0.0)

us2 = us.drop(pd.Timestamp("2026-11-05"))     # US holiday on 5 Nov
s2 = macro.us_since_close(us2, nse)
t("after a US holiday the old move is not counted twice", s2.loc["2026-11-06", "spx"] == 0.0)

# live features for 11 Nov use both sessions too
idx = pd.bdate_range("2023-01-02", "2026-11-09")
idx = idx[idx != pd.Timestamp("2026-11-10")]
close = pd.Series(np.linspace(18000, 25000, len(idx)), index=idx)
usd = pd.bdate_range("2023-01-02", "2026-11-10")
hist = {"^NSEI": pd.DataFrame({"Close": close}),
        "^GSPC": pd.DataFrame({"Close": pd.Series(5000.0, index=usd)})}
hist["^GSPC"].loc["2026-11-09", "Close"] = 5000 * 1.0235
hist["^GSPC"].loc["2026-11-10", "Close"] = 5000 * 1.0235 * 0.993
x, raw, used, _ = macro._latest_features(hist, pd.Timestamp("2026-11-11").date())
t("plan for 11 Nov sees the US move since the 9 Nov close (+2.35% then -0.7%), not just Tuesday",
  np.isclose(raw["spx"], 2.35 - 0.7, atol=0.01))
print("ALL PASS" if all(res) else "SOME FAILED")
