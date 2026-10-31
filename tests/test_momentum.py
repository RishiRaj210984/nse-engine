"""Paper-only third trade: NIFTY first-hour momentum buy, on real NSE prices (fake chains here)."""
import os, sys, tempfile
from datetime import datetime
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))
import store, momentum as M, live  # noqa: E402
from config import IST  # noqa: E402

tmp = tempfile.mkdtemp()
store.state_path = lambda n: os.path.join(tmp, n)
res = []
def t(name, c): res.append(c); print("PASS" if c else "FAIL", name)
def reset():
    p = os.path.join(tmp, M.PATH)
    if os.path.exists(p): os.remove(p)
def at(day, hm): return datetime.fromisoformat(f"{day}T{hm}:00").replace(tzinfo=IST)
def chain(ce, pe=100.0, atm=22700, exp="03-Nov-2026"):
    return {"atm": atm, "spot": atm + 5, "expiry": exp, "strikes": [
        {"strike": atm, "ce_ltp": ce, "ce_bid": ce - 0.5, "ce_ask": ce + 0.5, "pe_ltp": pe, "pe_bid": pe - 0.5, "pe_ask": pe + 0.5}]}
UP = {"open": 22560.0, "at_1015": 22700.0}      # +0.62%
FLAT = {"open": 22640.0, "at_1015": 22700.0}    # +0.27%
DOWN = {"open": 22840.0, "at_1015": 22700.0}    # -0.61%
D = "2026-10-29"; NEXT = "2026-11-03"

reset()
t("before 10:15: nothing recorded", M.step(at(D, "10:05"), UP, chain(120), NEXT)["today"] is None)
t("first hour +0.27%: NO SIGNAL", M.step(at(D, "10:20"), FLAT, chain(120), NEXT)["today"]["status"] == "NO SIGNAL")
reset()
t("expiry day: NO TRADE", M.step(at(NEXT, "10:20"), UP, chain(120), NEXT)["today"]["status"] == "NO TRADE")
reset()
x = M.step(at(D, "10:20"), UP, chain(400), NEXT)["today"]
t("premium 400 x 65 = Rs 26,000 above Rs 15,000: SKIPPED", x["status"] == "SKIPPED")

# up day: buys the ATM call at the real mid price, then a target
reset()
x = M.step(at(D, "10:20"), UP, chain(120), NEXT)["today"]
t("first hour +0.62%: buys the 22700 CE at Rs 120 (mid), 10:20", x["status"] == "OPEN" and x["kind"] == "CE" and x["strike"] == 22700 and x["entry_px"] == 120)
t("keeps the open strike in the saved chain", M.keep_strikes(at(D, "10:30")) == {22700})
x = M.step(at(D, "10:30"), UP, chain(150), NEXT)["today"]
t("value now = (150-120) x 65 = Rs 1,950; up 25%, so the stop moved to cost", x["value_now"] == 1950 and x["stop_pct"] == 0)
x = M.step(at(D, "10:40"), UP, chain(195), NEXT)["today"]
t("+62.5%: BOOKED, result (195-120) x 65 - 150 = Rs 4,725", x["status"] == "CLOSED" and "BOOKED" in x["reason"] and x["pnl"] == 4725)
x = M.step(at(D, "10:50"), UP, chain(100), NEXT)["today"]
t("closed trade does not move again", x["status"] == "CLOSED" and x["pnl"] == 4725)

# down day: put, stop
reset()
x = M.step(at(D, "10:20"), DOWN, chain(120, pe=110), NEXT)["today"]
t("first hour -0.61%: buys the 22700 PE", x["kind"] == "PE" and x["entry_px"] == 110)
x = M.step(at(D, "11:00"), DOWN, chain(120, pe=76), NEXT)["today"]
t("-31%: STOP", x["status"] == "CLOSED" and x["reason"] == "STOP -30%" and x["pnl"] == round((76 - 110) * 65 - 150))

# up 25% then back to cost
reset()
M.step(at(D, "10:20"), UP, chain(100), NEXT)
M.step(at(D, "10:30"), UP, chain(125), NEXT)
x = M.step(at(D, "10:40"), UP, chain(100), NEXT)["today"]
t("after +25%, back to cost: closed at cost", x["status"] == "CLOSED" and "back to cost" in x["reason"] and x["pnl"] == -150)

# up 50%, trail 25 below the peak
reset()
M.step(at(D, "10:20"), UP, chain(100), NEXT)
M.step(at(D, "10:30"), UP, chain(150), NEXT)
x = M.step(at(D, "10:40"), UP, chain(130), NEXT)["today"]
t("+50% then +30%: still open (trail at +25%)", x["status"] == "OPEN" and x["stop_pct"] == 25)
x = M.step(at(D, "10:50"), UP, chain(124), NEXT)["today"]
t("falls to +24%: TRAIL exit", x["status"] == "CLOSED" and "TRAIL" in x["reason"])

# held to 15:15
reset()
M.step(at(D, "10:20"), UP, chain(100), NEXT)
M.step(at(D, "13:00"), UP, chain(110), NEXT)
x = M.step(at(D, "15:20"), UP, chain(105), NEXT)["today"]
t("15:15 time exit", x["status"] == "CLOSED" and x["reason"] == "15:15 time exit" and x["pnl"] == 5 * 65 - 150)

# missed runs: left open, closed the next day at its last price
reset()
M.step(at(D, "10:20"), UP, chain(100), NEXT)
M.step(at(D, "12:00"), UP, chain(112), NEXT)
s = M.step(at("2026-10-30", "09:30"), UP, None, NEXT)
r = M.summary(day=D)["today"]
t("left open by missed runs: closed next day at the last price seen", r["status"] == "CLOSED" and r["exit_px"] == 112 and "missed" in r["reason"])
t("summary totals count closed trades", s["trades"] == 1 and s["total"] == 12 * 65 - 150)

# no chain after 10:45: NO ENTRY
reset()
M.step(at(D, "10:20"), UP, None, NEXT)
t("no NSE chain by 10:45: NO ENTRY", M.step(at(D, "10:50"), UP, None, NEXT)["today"]["status"] == "NO ENTRY")

# Yahoo 1-minute data: day's open and the 10:15 price
ts = [int(at(D, hm).timestamp()) for hm in ("09:15", "09:16", "10:13", "10:14", "10:15", "10:16")]
body = {"chart": {"result": [{"meta": {"regularMarketPrice": 22710, "chartPreviousClose": 22600, "regularMarketTime": ts[-1]},
                              "timestamp": ts, "indicators": {"quote": [{"open": [22560, 22570, 22690, 22695, 22702, 22705],
                                                                         "close": [22568, 22580, 22694, 22700, 22704, 22710],
                                                                         "high": [22570] * 6, "low": [22550] * 6}]}}]}}
live.requests.get = lambda *a, **k: type("R", (), {"json": lambda self: body})()
q = live.yahoo_quotes(["^NSEI"])["^NSEI"]
t("Yahoo: open = first bar open, 10:15 price = close of the 10:14 bar", q["open"] == 22560 and q["at_1015"] == 22700)
body["chart"]["result"][0]["timestamp"] = ts[:3]
t("Yahoo: before 10:14 there is no 10:15 price yet", live.yahoo_quotes(["^NSEI"])["^NSEI"]["at_1015"] is None)
# day before expiry: traded as before, flagged, and left out of the comparison total
reset()
M.step(at(D, "10:20"), UP, chain(120), NEXT)                       # 29 Oct, expiry 03 Nov: 5 days
M.step(at(D, "10:40"), UP, chain(195), NEXT)                       # booked +4,725
x = M.step(at("2026-11-02", "10:20"), UP, chain(120), NEXT)        # Mon 02 Nov, expiry Tue 03 Nov
t("day before expiry: still bought, flagged", x["today"]["status"] == "OPEN" and x["today"]["day_before_expiry"] is True)
x = M.step(at("2026-11-02", "11:00"), UP, chain(80), NEXT)         # -33%: stop
sk = x["skip_day_before"]
t("totals: both trades; comparison: only the 29 Oct one",
  x["trades"] == 2 and x["total"] == 4725 + round((80 - 120) * 65 - 150) and sk == {"trades": 1, "wins": 1, "total": 4725})
t("recent rows carry the flag", [r["day_before_expiry"] for r in x["recent"]] == [True, False])
import guardian  # noqa: E402
line = guardian.momentum_line(x, "2026-11-02")
t("evening alert shows both totals", "[day before expiry]" in line and "Without day-before-expiry trades: Rs +4,725 (1 trades, 1 won)" in line)
print("ALL PASS" if all(res) else "SOME FAILED")
