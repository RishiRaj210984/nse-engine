"""Exit guardian (TRIAL): watches your ICICI Direct F&O positions through the Breeze API and
applies the app's exit rules, so exits happen even when you are not watching.

  PAPER (default): reads positions and prices, logs and alerts what it WOULD do. No orders.
  LIVE:            only when guardian.env has MODE=live AND you start it with --live.
                   Squares off the whole trade (sold legs first, then bought legs).

Safety: exits only (never opens or adds), never more than the quantity you hold, works only
09:20-15:25 IST on trading days, at most MAX_DAILY_ORDERS orders a day, and stops at once if
a file named STOP exists next to this script. LIVE orders need the static IP you registered
with ICICI (SEBI rule for API orders); if ICICI rejects an order you get an alert to exit by hand.

Credentials live only in guardian.env on your computer (never committed, never shared).

    python guardian.py login     # prints today's ICICI login link
    python guardian.py check     # read-only test of keys, session, positions, quotes, alerts
    python guardian.py run       # paper mode
    python guardian.py run --live
"""
import json
import logging
import os
import sys
import time
from datetime import datetime

import requests

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
ENV_FILE = os.path.join(ROOT, "guardian.env")
STATE_FILE = os.path.join(ROOT, "guardian_state.json")
STOP_FILE = os.path.join(ROOT, "STOP")
LOG_FILE = os.path.join(ROOT, "guardian.log")

sys.path.insert(0, HERE)
from config import IST  # noqa: E402

log = logging.getLogger("guardian")

# Exit rules: identical to the app's Live tab
CREDIT_TARGET = 0.55      # book when 55% of the credit is in hand
CREDIT_STOP = 1.00        # exit when the loss equals the credit
LONG_STOP = -30           # % on bought options
LONG_BREAKEVEN_AT = 20    # once up 20%, stop moves to cost
LONG_TRAIL_AT = 40        # once up 40%, trail below the peak
LONG_BOOK = 60            # book at +60%
LONG_TIME_EXIT = "15:15"


# ------------------------------------------------------------------ config
def load_env():
    env = {}
    if os.path.exists(ENV_FILE):
        for line in open(ENV_FILE, encoding="utf-8"):
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                env[k.strip()] = v.strip()
    env.setdefault("MODE", "paper")
    env.setdefault("POLL_SECONDS", "20")
    env.setdefault("MAX_DAILY_ORDERS", "12")
    env.setdefault("TRAIL_PCT", "25")
    return env


def load_state():
    try:
        return json.load(open(STATE_FILE, encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def save_state(st):
    json.dump(st, open(STATE_FILE, "w", encoding="utf-8"), indent=1)


def notify(env, title, msg, urgent=False):
    log.warning("%s | %s", title, msg)
    topic = env.get("NTFY_TOPIC")
    if topic:
        try:
            requests.post(f"https://ntfy.sh/{topic}", data=msg.encode("utf-8"), timeout=8,
                          headers={"Title": title.encode("ascii", "ignore").decode(), "Priority": "urgent" if urgent else "default",
                                   "Tags": "rotating_light" if urgent else "chart_with_upwards_trend"})
        except Exception as exc:
            log.info("ntfy failed: %s", exc)


# ------------------------------------------------------------------ positions -> trades
def legs_from_positions(resp):
    """Open F&O option legs from get_portfolio_positions()."""
    legs = []
    for p in (resp or {}).get("Success") or []:
        if str(p.get("product_type", "")).lower() != "options":
            continue
        qty = int(float(p.get("quantity") or 0))
        action = str(p.get("action", "")).lower()
        if qty <= 0 or action not in ("buy", "sell"):
            continue
        legs.append({
            "stock_code": p.get("stock_code"), "expiry": p.get("expiry_date"), "strike": float(p.get("strike_price") or 0),
            "right": str(p.get("right", "")).capitalize(), "side": action, "qty": qty,
            "avg": float(p.get("average_price") or 0), "ltp": float(p.get("ltp") or 0),
        })
    return legs


def group_trades(legs):
    """Legs on the same underlying + expiry with both sold and bought legs = one structure (condor/spread).
    Bought-only legs are tracked one by one."""
    by = {}
    for l in legs:
        by.setdefault((l["stock_code"], l["expiry"]), []).append(l)
    trades = []
    for (sym, exp), ls in by.items():
        if any(l["side"] == "sell" for l in ls):
            trades.append({"id": f"{sym}|{exp}|combo", "legs": ls})
        else:
            for l in ls:
                trades.append({"id": f"{sym}|{exp}|{int(l['strike'])}{l['right'][0]}", "legs": [l]})
    return trades


def label(t):
    l0 = t["legs"][0]
    if len(t["legs"]) == 1:
        return f"{l0['stock_code']} {int(l0['strike'])} {l0['right']} {l0['side']} x{l0['qty']}"
    return f"{l0['stock_code']} {l0['expiry']} {len(t['legs'])}-leg ({', '.join(('S' if l['side'] == 'sell' else 'B') + str(int(l['strike'])) + l['right'][0] for l in t['legs'])})"


def evaluate(t, state, now_hm, trail_pct=25):
    """Same rules as the app. Returns (status, note, pnl, pct)."""
    sign = lambda l: 1 if l["side"] == "buy" else -1
    entry = sum(sign(l) * l["avg"] * l["qty"] for l in t["legs"])
    now = sum(sign(l) * l["ltp"] * l["qty"] for l in t["legs"])
    pnl = now - entry
    basis = abs(entry) or 1
    pct = 100 * pnl / basis
    peaks = state.setdefault("peaks", {})
    peak = max(peaks.get(t["id"], pnl), pnl)
    peaks[t["id"]] = peak
    peak_pct = 100 * peak / basis
    if entry < 0:  # credit structure
        if pnl >= CREDIT_TARGET * basis:
            return "BOOK PROFIT", f"{int(CREDIT_TARGET * 100)}% of the credit is in hand", pnl, pct
        if pnl <= -CREDIT_STOP * basis:
            return "EXIT NOW", "loss has reached 1x the credit", pnl, pct
        return "HOLD", f"target +{CREDIT_TARGET * basis:,.0f}, stop -{CREDIT_STOP * basis:,.0f}", pnl, pct
    stop = LONG_STOP
    if peak_pct >= LONG_BREAKEVEN_AT:
        stop = 0
    if peak_pct >= LONG_TRAIL_AT:
        stop = max(0, peak_pct - trail_pct)
    if pct <= stop:
        why = (f"gave back from peak +{peak_pct:.0f}%, locking +{stop:.0f}%" if stop > 0 else
               "back to cost after being up" if stop == 0 else "down 30%: stop-loss")
        return "EXIT NOW", why, pnl, pct
    if pct >= LONG_BOOK:
        return "BOOK PROFIT", f"up {pct:.0f}%", pnl, pct
    if now_hm >= LONG_TIME_EXIT:
        return "EXIT NOW", "15:15 time exit for bought options (time decay)", pnl, pct
    return "HOLD", f"stop at {stop:+.0f}%, peak {peak_pct:+.0f}%", pnl, pct


# ------------------------------------------------------------------ orders
def iso_expiry(exp):
    """'27-Oct-2026' -> '2026-10-27T06:00:00.000Z' (format Breeze expects)."""
    return datetime.strptime(exp, "%d-%b-%Y").strftime("%Y-%m-%dT06:00:00.000Z")


def exit_trade(breeze, t, env, state):
    """Close every leg: sold legs first (removes risk), then bought legs. Returns list of results."""
    results = []
    ordered = sorted(t["legs"], key=lambda l: 0 if l["side"] == "sell" else 1)
    for l in ordered:
        day = state.setdefault("orders", {}).setdefault(datetime.now(IST).date().isoformat(), 0)
        if day >= int(env["MAX_DAILY_ORDERS"]):
            results.append({"leg": l, "error": "daily order cap reached"})
            break
        r = breeze.square_off(exchange_code="NFO", product="options", stock_code=l["stock_code"],
                              expiry_date=iso_expiry(l["expiry"]), right=l["right"], strike_price=str(int(l["strike"])),
                              action="buy" if l["side"] == "sell" else "sell", order_type="market", validity="day",
                              stoploss="0", quantity=str(l["qty"]), price="0",
                              validity_date=datetime.now(IST).strftime("%Y-%m-%dT06:00:00.000Z"),
                              trade_password="", disclosed_quantity="0")
        state["orders"][datetime.now(IST).date().isoformat()] = day + 1
        results.append({"leg": l, "response": r})
        if not (r or {}).get("Success"):
            break  # stop at the first rejection; you will be alerted to exit by hand
    return results


# ------------------------------------------------------------------ main loop
def connect(env):
    from breeze_connect import BreezeConnect
    for k in ("BREEZE_API_KEY", "BREEZE_API_SECRET", "BREEZE_SESSION_TOKEN"):
        if not env.get(k):
            raise SystemExit(f"{k} is missing in guardian.env. Run: python guardian.py login")
    b = BreezeConnect(api_key=env["BREEZE_API_KEY"])
    b.generate_session(api_secret=env["BREEZE_API_SECRET"], session_token=env["BREEZE_SESSION_TOKEN"])
    return b


def market_window(now):
    import nse_calendar
    return nse_calendar.is_trading_day(now.date()) and "09:20" <= now.strftime("%H:%M") <= "15:25"


def run(live_flag=False, breeze=None, once=False, clock=None):
    env = load_env()
    live = live_flag and env["MODE"].lower() == "live"
    breeze = breeze or connect(env)
    state = load_state()
    notify(env, "Guardian started", f"Mode: {'LIVE (will exit positions)' if live else 'PAPER (alerts only, no orders)'}")
    while True:
        now = clock() if clock else datetime.now(IST)
        if os.path.exists(STOP_FILE):
            notify(env, "Guardian stopped", "STOP file found. No more checks or orders.", urgent=True)
            return state
        if not market_window(now) and not once:
            time.sleep(60)
            continue
        try:
            trades = group_trades(legs_from_positions(breeze.get_portfolio_positions()))
        except Exception as exc:
            notify(env, "Guardian: cannot read positions", f"{exc}. Session may have expired: log in again.", urgent=True)
            if once:
                return state
            time.sleep(60)
            continue
        alerted = state.setdefault("alerted", {})
        for t in trades:
            status, note, pnl, pct = evaluate(t, state, now.strftime("%H:%M"), int(env["TRAIL_PCT"]))
            log.info("%s | %s | P&L %+.0f (%+.1f%%) | %s", label(t), status, pnl, pct, note)
            if status == "HOLD":
                alerted.pop(t["id"], None)
                continue
            if alerted.get(t["id"]) == status:
                continue
            alerted[t["id"]] = status
            if not live:
                notify(env, f"PAPER: would {status}", f"{label(t)}: P&L {pnl:+,.0f} ({pct:+.1f}%). {note}.", urgent=status == "EXIT NOW")
                continue
            res = exit_trade(breeze, t, env, state)
            ok = all((r.get("response") or {}).get("Success") for r in res) and len(res) == len(t["legs"])
            notify(env, f"LIVE: {'EXITED' if ok else 'EXIT FAILED'} {status}",
                   f"{label(t)}: P&L {pnl:+,.0f}. {note}. " + ("All legs sent." if ok else
                   f"ICICI response: {json.dumps([r.get('response') or r.get('error') for r in res])[:300]}. EXIT BY HAND NOW."),
                   urgent=True)
        save_state(state)
        if once:
            return state
        time.sleep(int(env["POLL_SECONDS"]))


def _mask(v):
    return f"{v[:3]}…({len(v)} chars)" if v else "MISSING"


def check():
    """Read-only test of every piece. Never prints keys or personal details; places no orders."""
    env = load_env()
    ok = True
    print("1) guardian.env:", "found" if os.path.exists(ENV_FILE) else "NOT FOUND (copy guardian.env.example to guardian.env)")
    for k in ("BREEZE_API_KEY", "BREEZE_API_SECRET", "BREEZE_SESSION_TOKEN"):
        print(f"   {k}: {_mask(env.get(k, ''))}")
        ok &= bool(env.get(k))
    print(f"   MODE: {env['MODE']}   NTFY_TOPIC: {'set' if env.get('NTFY_TOPIC') else 'not set (no phone alerts)'}")
    try:
        ip = requests.get("https://api.ipify.org", timeout=8).text.strip()
        print(f"2) This computer's public IP: {ip}  (for LIVE orders this must be the static IP registered in Breeze)")
    except Exception as exc:
        print("2) Public IP: could not check:", exc)
    if not ok:
        print("\nStop here: fill the missing values, then run this check again.")
        return
    try:
        b = connect(env)
        print("3) Breeze session: OK")
    except BaseException as exc:
        print("3) Breeze session: FAILED:", str(exc)[:200],
              "\n   Most common cause: session token is from an earlier day. Run: python backend/guardian.py login")
        return
    def step(n, name, fn):
        try:
            r = fn()
            good = isinstance(r, dict) and r.get("Status") == 200
            print(f"{n}) {name}: {'OK' if good else 'FAILED'}" + ("" if good else f"  ({str((r or {}).get('Error'))[:160]})"))
            return r if good else None
        except Exception as exc:
            print(f"{n}) {name}: FAILED  ({str(exc)[:160]})")
    step(4, "Read funds", b.get_funds)
    r = step(5, "Read positions", b.get_portfolio_positions)
    if r is not None:
        legs = legs_from_positions(r)
        trades = group_trades(legs)
        print(f"   open option legs: {len(legs)}, grouped into {len(trades)} trade(s)")
        for t in trades:
            st, note, pnl, pct = evaluate(t, {}, datetime.now(IST).strftime("%H:%M"), int(env["TRAIL_PCT"]))
            print(f"   - {label(t)} | P&L {pnl:+,.0f} ({pct:+.1f}%) | guardian says: {st} ({note})")
    q = step(6, "Read a live quote (Nifty)", lambda: b.get_quotes(stock_code="NIFTY", exchange_code="NSE", product_type="cash"))
    if q:
        try:
            print("   Nifty last price:", (q.get("Success") or [{}])[0].get("ltp"))
        except Exception:
            pass
    if env.get("NTFY_TOPIC"):
        notify(env, "Guardian check", "Test alert: if you see this on your phone, alerts work.")
        print("7) Test alert sent to your ntfy topic: check your phone.")
    print("\nNo orders were placed. If 3-6 are OK, you are ready for the paper trial: python backend/guardian.py run")


def login_link():
    env = load_env()
    key = env.get("BREEZE_API_KEY")
    if not key:
        raise SystemExit("Put BREEZE_API_KEY in guardian.env first (see GUARDIAN.md).")
    import urllib.parse
    print("Open this link, log in to ICICI, then copy the 'apisession' value from the address bar:")
    print(f"https://api.icicidirect.com/apiuser/login?api_key={urllib.parse.quote_plus(key)}")
    print("Paste it into guardian.env as BREEZE_SESSION_TOKEN=..., then run: python guardian.py run")


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s",
                        handlers=[logging.FileHandler(LOG_FILE, encoding="utf-8"), logging.StreamHandler()])
    cmd = sys.argv[1] if len(sys.argv) > 1 else ""
    if cmd == "login":
        login_link()
    elif cmd == "check":
        check()
    elif cmd == "run":
        run(live_flag="--live" in sys.argv)
    else:
        print(__doc__)
