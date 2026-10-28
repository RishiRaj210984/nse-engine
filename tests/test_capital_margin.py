"""The nightly 'fits your money?' check counts ICICI margin, not only the worst loss."""
import os, sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))
import options_lab as L  # noqa: E402

res = []
def t(name, c): res.append(c); print("PASS" if c else "FAIL", name)
m = L.sold_margin("NIFTY", 22700, 150)
t("150-point NIFTY condor margin matches ICICI's Rs 72,727 (28 Oct 2026) within 1%", abs(m - 72727) / 72727 < 0.01)
t("narrow wings barely lower the margin: 50-point still above Rs 60,000", L.sold_margin("NIFTY", 22700, 50) > 60000)
t("BankNifty condor margin also far above Rs 15,000", L.sold_margin("BANKNIFTY", 54000, 300) > 70000)
print("ALL PASS" if all(res) else "SOME FAILED")
