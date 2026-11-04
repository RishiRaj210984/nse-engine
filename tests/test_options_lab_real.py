"""Options lab on NSE's real closing prices: same strikes and rules as the model replay, checked at each close.
Also: expiry-day entries are skipped, a spread whose edge faded in the last 120 days is skipped,
and the daily price files keep only the strikes / expiries the lab can use."""
import os, sys, tempfile
from datetime import date
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))
import numpy as np, pandas as pd  # noqa: E402
import store, options_lab as L, nse_prices  # noqa: E402

res = []
def t(name, c): res.append(c); print("PASS" if c else "FAIL", name)

# NIFTY expiry Tue 3 Nov 2026. Previous close 22,000, India VIX 15 -> expected move 414 for 5 days:
# short strikes 22,350 CE / 21,650 PE, wings 200 points.
D = [date(2026, 10, 28), date(2026, 10, 29), date(2026, 10, 30), date(2026, 11, 2), date(2026, 11, 3)]
EXP = "2026-11-03"
vix = pd.Series(15.0, index=pd.to_datetime(D))
STRIKES = [(22350, "CE"), (22550, "CE"), (21650, "PE"), (21450, "PE")]


def prices(entry_spot=22000, path=(), vol=1000, expiry_spot=22000):
    """path: one (ce_sold, ce_bought, pe_sold, pe_bought) per day after the entry (29 Oct)."""
    rows = [{"sym": "NIFTY", "expiry": "", "strike": 0, "type": "IX", "close": 22000.0, "vol": 0, "date": D[0]},
            {"sym": "NIFTY", "expiry": "", "strike": 0, "type": "IX", "close": float(entry_spot), "vol": 0, "date": D[1]}]
    for (k, ty), p in zip(STRIKES, (40, 15, 45, 20)):
        rows.append({"sym": "NIFTY", "expiry": EXP, "strike": k, "type": ty, "close": p, "vol": vol, "date": D[1]})
    for d, quad in zip(D[2:4], path):
        rows.append({"sym": "NIFTY", "expiry": "", "strike": 0, "type": "IX", "close": 22000.0, "vol": 0, "date": d})
        for (k, ty), p in zip(STRIKES, quad):
            rows.append({"sym": "NIFTY", "expiry": EXP, "strike": k, "type": ty, "close": p, "vol": 100, "date": d})
    rows.append({"sym": "NIFTY", "expiry": "", "strike": 0, "type": "IX", "close": float(expiry_spot), "vol": 0, "date": D[4]})
    return pd.DataFrame(rows)


def condor(P):
    R = L.simulate_real("NIFTY", P, vix, pd.Series(dtype=float), {}, {})
    R = R[(R.strategy == "condor") & (R.date == "2026-10-29")] if len(R) else R
    return R.iloc[0] if len(R) else None

# credit = 40 - 15 + 45 - 20 = 50 points = Rs 3,250 a lot
c = condor(prices(path=[(20, 8, 15, 7)]))
t("strikes as the model replay: 22,350 / 21,650, 200-point wings (worst loss (200 - 50) x 65 + 400)",
  c is not None and c.max_loss == 150 * 65 + 400)
t("target: 55% of the credit kept at the 30 Oct close -> (50 - 20) x 65 - 400 = Rs 1,550", c is not None and c.pnl == 1550)
c = condor(prices(path=[(110, 30, 5, 2), (140, 40, 3, 1)]))
t("stop: loss reaches 1x credit at the 2 Nov close -> (50 - 102) x 65 - 400 = Rs -3,780", c is not None and c.pnl == -3780)
c = condor(prices(path=[(25, 10, 25, 10), (20, 8, 20, 8)], expiry_spot=22420))
t("held to expiry, settled on the index close: call side 70 in the money -> (50 - 70) x 65 - 400 = Rs -1,700",
  c is not None and c.pnl == -1700)
t("not entered when a leg did not trade that day", condor(prices(path=[(20, 8, 15, 7)], vol=0)) is None)
t("not entered when the index closed outside the short strikes", condor(prices(entry_spot=22400, path=[(20, 8, 15, 7)])) is None)

# plan verdicts on a real-price lab
lab = {"prices": "real", "NIFTY": {"condor": {"basis": "all days", "overall": {"trades": 300, "win_pct": 78, "avg_pnl": 150, "worst": -9000},
                                               "overall_allowed": True, "by_bucket": {"1": {"trades": 40, "win_pct": 80, "avg_pnl": 200, "worst": -5000,
                                                                                            "allowed": True, "marginal": False, "sample": "full 2 years"}}},
                                    "bear_spread": {"basis": "days the model called BEARISH", "overall_allowed": False, "by_bucket": {},
                                                    "overall": {"trades": 115, "win_pct": 42.6, "avg_pnl": 215, "worst": -5196},
                                                    "recent": {"trades": 22, "win_pct": 36.4, "avg_pnl": -276, "worst": -4218}}}}
v = L.setup_verdict("NIFTY", "Iron Condor", "2026-11-03", lab)
t("expiry day: condor skipped (can't be tested on closing prices)", v["decision"] == "SKIP" and "Expiry day" in v["reason"])
t("1 day before still trades", L.setup_verdict("NIFTY", "Iron Condor", "2026-11-02", lab)["decision"] == "TRADE")
v = L.setup_verdict("NIFTY", "Bear Put Spread", "2026-10-29", lab)
t("a spread positive over 2 years but losing in the last 120 days is skipped, and says why",
  v["decision"] == "SKIP" and "faded" in v["reason"])


def spreads(recent_pnl):
    old = [{"strategy": "bear_spread", "date": f"2025-0{1 + i % 8}-1{i % 9}", "dte": 3, "bucket": "3", "label": "BEARISH",
            "pnl": 500, "first_hr": 0.1} for i in range(100)]
    new = [{"strategy": "bear_spread", "date": (pd.Timestamp.now() - pd.Timedelta(days=5 + i)).date().isoformat(), "dte": 3,
            "bucket": "3", "label": "BEARISH", "pnl": recent_pnl, "first_hr": 0.1} for i in range(20)]
    return L._summarise("NIFTY", pd.DataFrame(old + new))["bear_spread"]["overall_allowed"]
t("spread rule: allowed while it still works lately", spreads(+100))
t("spread rule: not allowed when the last 120 days lost money", not spreads(-300))

# daily price files: only NIFTY / BANKNIFTY index options, near expiries, strikes near the index
raw = pd.DataFrame([
    {"TradDt": "2026-11-02", "FinInstrmTp": "IDO", "TckrSymb": "NIFTY", "XpryDt": EXP, "StrkPric": 22350.0, "OptnTp": "CE", "ClsPric": 40.0, "TtlTradgVol": 10, "UndrlygPric": 22000.0},
    {"TradDt": "2026-11-02", "FinInstrmTp": "IDO", "TckrSymb": "NIFTY", "XpryDt": "2026-12-29", "StrkPric": 22350.0, "OptnTp": "CE", "ClsPric": 300.0, "TtlTradgVol": 10, "UndrlygPric": 22000.0},
    {"TradDt": "2026-11-02", "FinInstrmTp": "IDO", "TckrSymb": "NIFTY", "XpryDt": EXP, "StrkPric": 26000.0, "OptnTp": "CE", "ClsPric": 0.05, "TtlTradgVol": 10, "UndrlygPric": 22000.0},
    {"TradDt": "2026-11-02", "FinInstrmTp": "IDF", "TckrSymb": "NIFTY", "XpryDt": "2026-11-24", "StrkPric": 0.0, "OptnTp": "", "ClsPric": 22050.0, "TtlTradgVol": 10, "UndrlygPric": 22000.0},
    {"TradDt": "2026-11-02", "FinInstrmTp": "STO", "TckrSymb": "TCS", "XpryDt": "2026-11-24", "StrkPric": 3000.0, "OptnTp": "CE", "ClsPric": 50.0, "TtlTradgVol": 10, "UndrlygPric": 3100.0}])
C = nse_prices.compact(raw)
t("price file keeps the near NIFTY option and the index close only", len(C) == 2 and set(C.type) == {"CE", "IX"}
  and float(C[C.type == "IX"].close.iloc[0]) == 22000.0)
tmp = tempfile.mkdtemp()
store.state_path = lambda n: os.path.join(tmp, n)
os.makedirs(nse_prices.folder())
C.to_csv(os.path.join(nse_prices.folder(), "2026-11-02.csv.gz"), index=False, compression="gzip")
P = nse_prices.load()
t("saved days load back with their date", P is not None and len(P) == 2 and P.date.iloc[0] == date(2026, 11, 2)
  and P[P.type == "IX"].expiry.iloc[0] == "")
print("ALL PASS" if all(res) else "SOME FAILED")
