"""Exit guardian (TRIAL): watches your ICICI Direct F&O positions through the Breeze API and
applies the app's exit rules, so exits happen even when you are not watching.

  PAPER (default): reads positions and prices, logs and alerts what it WOULD do. No orders.
  LIVE:            only when guardian.env has MODE=live AND you start it with --live.
                   Squares off the whole trade (sold legs first, then bought legs).

Safety: exits only (never opens or adds), never more than the quantity you hold, works only
09:20-15:25 IST on trading days, only limit orders (Breeze allows no market orders), exits never blocked by an order cap, and stops at once if
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
# Sold structures still open on expiry day are closed at 10:15: of exits up to 12:00 it was the best
# in a 2-year replay (Nifty about equal to holding to the close, BankNifty clearly better)
EXPIRY_EXIT = "10:15"


def _expiry_date(exp):
    for fmt in ("%d-%b-%Y", "%Y-%m-%d"):
        try:
            return datetime.strptime(str(exp)[:11].strip(), fmt).date()
        except ValueError:
            pass
    return None


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
    try:
        os.chmod(STATE_FILE, 0o600)   # holds today's login code
    except OSError:
        pass


_sent = {}   # (title, msg) -> last send time, so a loop cannot flood the phone
_sent_times = []


def notify(env, title, msg, urgent=False):
    log.warning("%s | %s", title, msg)
    now = time.time()
    if now - _sent.get((title, msg), 0) < 15 * 60:
        return
    _sent_times[:] = [t for t in _sent_times if now - t < 3600]
    if len(_sent_times) >= 20:
        log.warning("alert limit reached (20 per hour); not sent")
        return
    _sent[(title, msg)] = now
    _sent_times.append(now)
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


def evaluate(t, state, now_hm, trail_pct=25, today=None):
    """Same rules as the app. Returns (status, note, pnl, pct). `today` (a date) enables the expiry-day exit."""
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
        if today and _expiry_date(t["legs"][0].get("expiry")) == today and now_hm >= EXPIRY_EXIT:
            return "EXIT NOW", f"expiry day: closing at {EXPIRY_EXIT} to keep the premium left", pnl, pct
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


# ------------------------------------------------------------------ robust orders
# Breeze does not allow market orders. Every order is a limit order that starts at the touch and,
# if it does not fill, is cancelled and re-placed further through the spread, for up to FILL_TIMEOUT.
# After that the guardian decides on its own (finish, undo or keep retrying) and alerts you.
FILL_TIMEOUT = 300                                    # seconds per leg: 5 minutes
FILL_STEPS = (0.0, 0.01, 0.02, 0.04, 0.06, 0.08, 0.10, 0.15)   # how far past the touch each try goes
FILL_POLL = 40                                        # seconds to wait for one try to fill
PERMANENT = ("static ip", "margin", "insufficient", "not allowed", "not permitted", "invalid", "blocked")
TICK = 0.05


def _tick(px):
    return max(TICK, round(round(px / TICK) * TICK, 2))


def _opt_quote(breeze, code, right, strike, expiry_iso):
    try:
        r = breeze.get_quotes(stock_code=code, exchange_code="NFO", expiry_date=expiry_iso, product_type="options",
                              right=right.lower(), strike_price=str(int(strike)))
        rows = (r or {}).get("Success") or []
        return rows[0] if rows else {}
    except Exception as exc:
        log.info("quote failed for %s %s: %s", strike, right, exc)
        return {}


def fill_leg(breeze, code, right, strike, expiry_iso, qty, action, now, ref_px=0.0, closing=False):
    """Fill one leg with limit orders, improving the price on every try for up to FILL_TIMEOUT.
    action 'buy' / 'sell'; closing=True squares off an open position. Returns (filled, price, error)."""
    start, err, tries = time.time(), None, 0
    for step in FILL_STEPS:
        if time.time() - start > FILL_TIMEOUT:
            break
        q = _opt_quote(breeze, code, right, strike, expiry_iso)
        ltp = float(q.get("ltp") or ref_px or 0)
        if not ltp:
            err = "no live price"
            time.sleep(10)
            continue
        if action == "buy":
            px = _tick(float(q.get("best_offer_price") or ltp * 1.01) * (1 + step))
        else:
            px = _tick(float(q.get("best_bid_price") or ltp * 0.99) * (1 - step))
        common = dict(exchange_code="NFO", product="options", stock_code=code, expiry_date=expiry_iso, right=right.lower(),
                      strike_price=str(int(strike)), action=action, order_type="limit", quantity=str(qty), price=f"{px:.2f}",
                      validity="day", validity_date=now.strftime("%Y-%m-%dT06:00:00.000Z"), disclosed_quantity="0")
        try:
            r = (breeze.square_off(stoploss="0", trade_password="", **common) if closing
                 else breeze.place_order(stoploss="", user_remark="tradedesk", **common))
        except Exception as exc:
            r = {"Error": str(exc)}
        tries += 1
        oid = ((r or {}).get("Success") or {}).get("order_id")
        if not oid:
            err = str((r or {}).get("Error") or r)[:150]
            if tries >= 2 and any(p in err.lower() for p in PERMANENT):
                break          # retrying will not help (IP, margin, ...)
            time.sleep(5)
            continue
        status = ""
        for _ in range(max(1, FILL_POLL // 5)):
            try:
                d = ((breeze.get_order_detail(exchange_code="NFO", order_id=oid) or {}).get("Success") or [{}])[0]
            except Exception:
                d = {}
            status = str(d.get("status", "")).lower()
            if status in ("executed", "complete", "completed", "traded"):
                return True, px, None
            if status in ("rejected", "cancelled", "canceled"):
                err = f"order {status}: {str(d.get('cancellation_reason') or d.get('remarks') or '')[:100]}"
                break
            time.sleep(5)
        if status not in ("rejected", "cancelled", "canceled"):
            try:
                breeze.cancel_order(exchange_code="NFO", order_id=oid)   # not filled at this price: try a better one
            except Exception as exc:
                log.info("cancel failed for %s: %s", oid, exc)
            err = f"not filled up to {px:.2f}"
    return False, None, err or "not filled in 5 minutes"


def exit_trade(breeze, t, env, state):
    """Close every leg: sold legs first (removes risk), then bought legs, each with improving limit
    orders for up to 5 minutes. If a sold leg cannot be bought back, its bought (protective) legs are
    kept so the position never becomes unlimited-risk; the main loop retries on its next pass."""
    results, stuck = [], False
    now = datetime.now(IST)
    for l in sorted(t["legs"], key=lambda l: 0 if l["side"] == "sell" else 1):
        if stuck and l["side"] == "buy":
            results.append({"leg": l, "error": "kept: protects a sold leg that is still open"})
            continue
        ok, px, err = fill_leg(breeze, l["stock_code"], l["right"], l["strike"], iso_expiry(l["expiry"]), int(l["qty"]),
                               "buy" if l["side"] == "sell" else "sell", now, ref_px=l.get("ltp") or 0, closing=True)
        results.append({"leg": l, "response": {"Success": {"price": px}} if ok else None, "error": err})
        if not ok and l["side"] == "sell":
            stuck = True
    return results


# ------------------------------------------------------------------ main loop
def _breeze_class():
    """Import BreezeConnect without our backend/config.py shadowing Breeze's own `import config`."""
    if "breeze_connect.breeze_connect" in sys.modules:
        return sys.modules["breeze_connect.breeze_connect"].BreezeConnect
    ours = sys.modules.pop("config", None)
    saved_path = list(sys.path)
    sys.path[:] = [p for p in sys.path if os.path.abspath(p or ".") not in (HERE, ROOT)]
    try:
        from breeze_connect import BreezeConnect
    finally:
        sys.path[:] = saved_path
        if ours is not None:
            sys.modules["config"] = ours   # Breeze keeps its own reference to its config
    # Breeze logs every API reply (bank account, balances) at DEBUG to logs/apiLogs.log and the console.
    # Keep only its warnings and errors.
    for name in ("APILogger", "WebsocketLogger"):
        lg = logging.getLogger(name)
        lg.setLevel(logging.WARNING)
        lg.propagate = False
    return BreezeConnect


def connect(env):
    BreezeConnect = _breeze_class()
    for k in ("BREEZE_API_KEY", "BREEZE_API_SECRET", "BREEZE_SESSION_TOKEN"):
        if not env.get(k):
            raise SystemExit(f"{k} is missing in guardian.env. Run: python guardian.py login")
    b = BreezeConnect(api_key=env["BREEZE_API_KEY"])
    b.generate_session(api_secret=env["BREEZE_API_SECRET"], session_token=env["BREEZE_SESSION_TOKEN"])
    return b


def market_window(now):
    import nse_calendar
    return nse_calendar.is_trading_day(now.date()) and "09:20" <= now.strftime("%H:%M") <= "15:25"


# ------------------------------------------------------------------ phone commands (ntfy)
# Send from the Trade Desk app (Live tab) or the ntfy app to your private command topic:
#   "<PIN> SESSION <code>"   today's ICICI login code
#   "<PIN> ENTER NIFTY [lots]" / "<PIN> ENTER BANKNIFTY [lots]"   place today's plan trade
#   "<PIN> EXIT ALL"   close everything now      "<PIN> STATUS"   P&L of every trade
#   "<PIN> STOP" / "<PIN> RESUME"   pause / resume all automatic actions
PLAN_URL = "https://rishiraj210984.github.io/nse-engine"
BREEZE_CODE = {"NIFTY": "NIFTY", "BANKNIFTY": "CNXBAN"}


def poll_commands(env, state):
    topic, pin = env.get("NTFY_CMD_TOPIC"), env.get("GUARDIAN_PIN")
    if not topic or not pin or len(pin) < 6:
        return []
    import hmac
    try:
        since = state.get("cmd_since", "10m")
        r = requests.get(f"https://ntfy.sh/{topic}/json", params={"poll": "1", "since": since}, timeout=10)
        msgs = [json.loads(x) for x in r.text.splitlines() if x.strip()]
    except Exception as exc:
        log.info("command poll failed: %s", exc)
        return []
    out = []
    for m in msgs:
        if m.get("event") != "message":
            continue
        state["cmd_since"] = m["id"]
        parts = (m.get("message") or "").strip().split()
        if len(parts) >= 2 and hmac.compare_digest(parts[0], pin):
            out.append([p.upper() if i == 0 else p for i, p in enumerate(parts[1:])])
        else:
            log.warning("ignored a command with a wrong or missing PIN")
    return out


def fetch_plan():
    b = requests.get(f"{PLAN_URL}/global-bias.json", params={"t": int(time.time())}, timeout=15).json()
    p = requests.get(f"{PLAN_URL}/plan.json", params={"t": int(time.time())}, timeout=15).json()
    return b, p


def _q(breeze, index, l, expiry_iso):
    try:
        r = breeze.get_quotes(stock_code=BREEZE_CODE[index], exchange_code="NFO", expiry_date=expiry_iso, product_type="options",
                              right="call" if l["type"] == "CE" else "put", strike_price=str(int(l["strike"])))
    except Exception as exc:     # no prices outside market hours
        log.info("quote failed for %s %s: %s", l["strike"], l["type"], exc)
        return {}
    rows = (r or {}).get("Success") or []
    return rows[0] if rows else {}


def _place_legs(breeze, index, legs, expiry_iso, qty, now, live):
    """Paper: price each leg at the touch. Live: fill each leg in order with improving limit orders
    (up to 5 minutes each). If a leg cannot be filled, the legs already filled are closed again
    automatically, so you are never left with half a structure. Returns (ok, plan_lines, placed, error)."""
    plan_lines, placed, filled = [], [], []
    for l in legs:
        right = "call" if l["type"] == "CE" else "put"
        if not live:
            q = _q(breeze, index, l, expiry_iso)
            ltp = float(q.get("ltp") or l["est_premium"])
            px = float(q.get("best_offer_price") or ltp * 1.02) if l["action"] == "BUY" else float(q.get("best_bid_price") or ltp * 0.98)
            plan_lines.append(f"{l['action']} {l['strike']} {l['type']} x{qty} @ {_tick(px):.2f}")
            continue
        ok, px, err = fill_leg(breeze, BREEZE_CODE[index], right, l["strike"], expiry_iso, qty, l["action"].lower(), now)
        if ok:
            line = f"{l['action']} {l['strike']} {l['type']} x{qty} @ {px:.2f}"
            plan_lines.append(line); placed.append(line); filled.append(l)
            continue
        # could not complete the structure: undo what was filled, automatically
        undone, left = [], []
        for f in reversed(filled):
            r2 = "call" if f["type"] == "CE" else "put"
            ok2, px2, err2 = fill_leg(breeze, BREEZE_CODE[index], r2, f["strike"], expiry_iso, qty,
                                      "sell" if f["action"] == "BUY" else "buy", now, closing=True)
            (undone if ok2 else left).append(f"{f['action']} {f['strike']} {f['type']}" + ("" if ok2 else f" ({err2})"))
        msg = f"could not fill {l['action']} {l['strike']} {l['type']} after 5 minutes of better prices ({err}). "
        if not filled:
            msg += "Nothing was placed."
        elif not left:
            msg += f"Undone automatically: closed {', '.join(undone)}. You hold nothing from this trade."
        else:
            msg += f"Undo FAILED for {', '.join(left)}. CHECK ICICI NOW and close these by hand."
        return False, plan_lines, placed, msg
    return True, plan_lines, placed, None


def enter_trade(breeze, env, state, index, lots, live, now, plan=None, demo=False):
    """Place today's plan trade for `index`: BUY legs first, then SELL legs, limit orders at the touch.
    Refuses unless the plan is for today, marks the index TRADE, and the entry window/condition holds."""
    lots = max(1, min(int(lots), int(env.get("MAX_LOTS", "1"))))
    bias, p = plan or fetch_plan()
    opt = next((o for o in p.get("options", []) if o["index"] == index), None)
    full = (bias.get("options") or {}).get(index)
    if demo:
        live = False   # a demo never places orders
        if not full:
            return False, f"no {index} structure in the plan"
    elif p.get("session") != now.date().isoformat():
        return False, f"plan is for {p.get('session')}, not today"
    elif not opt or not full or opt.get("decision") != "TRADE":
        return False, f"{index} is not marked TRADE today ({(opt or {}).get('decision')})"
    hm = now.strftime("%H:%M")
    if not demo and not ("10:10" <= hm <= "11:30"):
        return False, f"entry window is 10:10-11:30 IST (now {hm})"
    if live:   # one trade per index at a time: never stack a second structure on an open one
        try:
            held = [l for l in legs_from_positions(breeze.get_portfolio_positions()) if l["stock_code"] == BREEZE_CODE[index]]
        except Exception as exc:
            return False, f"could not read your positions to check for an open {index} trade ({str(exc)[:80]}). Not placed."
        if held:
            return False, f"you already hold an open {index} position ({len(held)} legs). One trade per index at a time: not placed."
    s = full["strategies"][0]
    expiry_iso = datetime.strptime(full["expiry"], "%Y-%m-%d").strftime("%Y-%m-%dT06:00:00.000Z")
    shorts = sorted(l["strike"] for l in s["legs"] if l["action"] == "SELL")
    try:
        spot = float(((breeze.get_quotes(stock_code=BREEZE_CODE[index], exchange_code="NSE", product_type="cash") or {})
                      .get("Success") or [{}])[0].get("ltp") or 0)
    except Exception:
        spot = 0
    if not demo and spot and len(shorts) >= 2 and not (shorts[0] < spot < shorts[-1]):
        return False, f"{index} {spot:,.0f} is outside the short strikes {shorts[0]:,}-{shorts[-1]:,}: entry condition not met"
    qty = full["lot_size"] * lots
    legs = sorted(s["legs"], key=lambda l: 0 if l["action"] == "BUY" else 1)
    ok, plan_lines, placed, err = _place_legs(breeze, index, legs, expiry_iso, qty, now, live)
    if not ok:
        return False, err
    if not live:
        tag = "DEMO (paper, checks skipped)" if demo else "PAPER"
        if not spot:
            tag += ", plan prices (market closed)"
        return True, f"{tag}: {s['name']} exp {full['expiry']}: would place " + "; ".join(plan_lines) + (f". {index} now {spot:,.0f}." if spot else "")
    return True, "Entered: " + "; ".join(placed)


def check_capital_edge(env, state, now):
    """Once each evening: tell the phone the first time a setup that fits your money shows a real edge."""
    day = now.date().isoformat()
    if now.strftime("%H:%M") < "18:00" or state.get("capital_checked") == day:
        return
    state["capital_checked"] = day
    try:
        r = requests.get(f"{PLAN_URL}/small-capital.json", params={"t": int(time.time())}, timeout=15).json()
    except Exception as exc:
        log.info("small-capital check failed: %s", exc)
        return
    if r.get("edge_found") and state.get("edge_alerted") != r.get("best"):
        state["edge_alerted"] = r["best"]
        notify(env, "A setup that fits your money shows an edge",
               f"{r['best']} (money: Rs {r.get('capital', 0):,}). Check it in the app, then decide on live. Still paper until you switch.",
               urgent=True)
    elif not r.get("edge_found"):
        state.pop("edge_alerted", None)


def evening_report(env, state, now):
    """Once each evening: today's paper result and the running total, as one phone alert."""
    day = now.date().isoformat()
    if now.strftime("%H:%M") < "18:00" or state.get("report_sent") == day or time.time() - state.get("report_tried", 0) < 600:
        return
    state["report_tried"] = time.time()   # look again every 10 minutes until the evening review is out
    try:
        r = requests.get(f"{PLAN_URL}/review.json", params={"t": int(time.time())}, timeout=15).json()
    except Exception as exc:
        log.info("evening report failed: %s", exc)
        return
    latest, tot = r.get("latest") or {}, r.get("totals") or {}
    if latest.get("session") != day or not latest.get("final"):
        return   # the evening review has not run yet; try again on the next loop
    state["report_sent"] = day
    pb = r.get("paper") or {}
    d0 = next((x for x in pb.get("days") or [] if x.get("session") == day), None)
    if d0 is not None:   # the paper book: what you would really hold
        pnl = d0.get("pnl", 0)
        notify(env, f"Paper result today: {'+' if pnl >= 0 else '-'}Rs {abs(pnl):,}",
               "\n".join(d0.get("detail") or ["No trade today."]) + f"\nTotal: Rs {pb.get('total', 0):+,} "
               f"({pb.get('trades', 0)} closed trades, {pb.get('wins', 0)} won; open now Rs {pb.get('open_value', 0):+,}). Paper only, 1 lot.")
        return
    h0 = next((x for x in r.get("history") or [] if x.get("session") == day), {})
    pnl = h0.get("plan_pnl", 0)
    notify(env, f"Paper result today: {'+' if pnl >= 0 else '-'}Rs {abs(pnl):,}",
           "\n".join(h0.get("detail") or []) + f"\nTotal: Rs {tot.get('plan_pnl_sum', 0):+,} over {tot.get('days', 0)} days "
           f"({tot.get('plan_win_days', 0)} profit, {tot.get('plan_loss_days', 0)} loss). Paper only, 1 lot.")


def handle_command(cmd, breeze_box, env, state, live, now):
    _sent.clear()   # a command you sent always gets its answer, even a repeated one
    name = cmd[0]
    if name == "SESSION" and len(cmd) >= 2:
        env["BREEZE_SESSION_TOKEN"] = cmd[1]
        state["session_day"] = now.date().isoformat()
        try:
            breeze_box[0] = connect(env)
            state["session_token"] = cmd[1]
            notify(env, "Guardian: logged in", "Session accepted for today.")
        except BaseException as exc:
            notify(env, "Guardian: login failed", f"{str(exc)[:150]}. Get a fresh code and send SESSION again.", urgent=True)
    elif name == "STOP":
        open(STOP_FILE, "w").write(now.isoformat())
        notify(env, "Guardian paused", "No automatic entries or exits until RESUME.", urgent=True)
    elif name == "RESUME":
        if os.path.exists(STOP_FILE):
            os.remove(STOP_FILE)
        notify(env, "Guardian resumed", f"Mode: {'LIVE' if live else 'PAPER'}.")
    elif breeze_box[0] is None:
        notify(env, "Guardian: not logged in", "Send today's SESSION code first.", urgent=True)
    elif os.path.exists(STOP_FILE):
        notify(env, "Guardian is paused", "Send RESUME first.", urgent=True)
    elif name == "ENTER" and len(cmd) >= 2 and cmd[1].upper() in BREEZE_CODE:
        demo = any(c.upper() == "DEMO" for c in cmd[2:])
        if demo and live:
            notify(env, "Guardian: demo refused", "Demo works only in PAPER mode.", urgent=True)
            return
        lots = next((c for c in cmd[2:] if c.isdigit()), 1)
        ok, msg = enter_trade(breeze_box[0], env, state, cmd[1].upper(), lots, live, now, demo=demo)
        notify(env, f"Guardian ENTER {cmd[1].upper()}: {'OK' if ok else 'NOT DONE'}", msg, urgent=not ok)
    elif name in ("EXIT", "STATUS"):
        trades = group_trades(legs_from_positions(breeze_box[0].get_portfolio_positions()))
        lines = []
        for t in trades:
            st, note, pnl, pct = evaluate(t, state, now.strftime("%H:%M"), int(env["TRAIL_PCT"]), now.date())
            lines.append(f"{label(t)}: {pnl:+,.0f} ({pct:+.1f}%) {st}")
            if name == "EXIT":
                if live:
                    exit_trade(breeze_box[0], t, env, state)
                    lines[-1] += " -> exit sent"
                else:
                    lines[-1] += " -> PAPER: would exit"
        notify(env, f"Guardian {name}", "\n".join(lines) or "No open option positions.", urgent=name == "EXIT")


def run(live_flag=False, breeze=None, once=False, clock=None):
    env = load_env()
    live = live_flag and env["MODE"].lower() == "live"
    box = [breeze]
    state = load_state()
    if state.get("session_day") == datetime.now(IST).date().isoformat() and state.get("session_token"):
        env["BREEZE_SESSION_TOKEN"] = state["session_token"]   # today's login from the phone survives a restart
    if box[0] is None and env.get("BREEZE_SESSION_TOKEN"):
        try:
            box[0] = connect(env)
            log.warning("logged in with today's saved session")
        except BaseException as exc:
            log.warning("startup login failed (%s); waiting for a SESSION command", exc)
    # a restart within 30 minutes (crash, update) stays quiet
    if time.time() - state.get("started_at", 0) > 30 * 60:
        notify(env, "Guardian started", f"Mode: {'LIVE (may place and exit orders)' if live else 'PAPER (alerts only, no orders)'}"
                                        f"{'' if box[0] else '. Waiting for today’s SESSION code.'}")
    state["started_at"] = time.time()
    save_state(state)
    while True:
        now = clock() if clock else datetime.now(IST)
        check_capital_edge(env, state, now)
        evening_report(env, state, now)
        cmds = poll_commands(env, state)
        save_state(state)   # mark commands as read first, so a crash never replays them
        for cmd in cmds:
            try:
                handle_command(cmd, box, env, state, live, now)
            except (KeyboardInterrupt, SystemExit):
                raise
            except BaseException as exc:
                notify(env, "Guardian command failed", f"{' '.join(cmd[:2])}: {str(exc)[:200]}", urgent=True)
        save_state(state)
        if os.path.exists(STOP_FILE) or box[0] is None or (not market_window(now) and not once):
            if once:
                return state
            time.sleep(15)
            continue
        breeze = box[0]
        try:
            trades = group_trades(legs_from_positions(breeze.get_portfolio_positions()))
        except Exception as exc:
            notify(env, "Guardian: cannot read positions", f"{exc}. Session may have expired: send a new SESSION code.", urgent=True)
            if once:
                return state
            time.sleep(60)
            continue
        alerted = state.setdefault("alerted", {})
        for t in trades:
            status, note, pnl, pct = evaluate(t, state, now.strftime("%H:%M"), int(env["TRAIL_PCT"]), now.date())
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
            if not ok:
                alerted.pop(t["id"], None)   # keep trying on the next pass, with fresh prices
            notify(env, f"LIVE: {'EXITED' if ok else 'EXIT FAILED'} {status}",
                   f"{label(t)}: P&L {pnl:+,.0f}. {note}. " + ("All legs filled." if ok else
                   "Not closed after 5 minutes of better prices: " + "; ".join(
                       f"{int(r['leg']['strike'])}{r['leg']['right'][0]} {r.get('error')}" for r in res if r.get("error"))[:300]
                   + ". The guardian keeps retrying every pass. EXIT BY HAND NOW if you can."),
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
            st, note, pnl, pct = evaluate(t, {}, datetime.now(IST).strftime("%H:%M"), int(env["TRAIL_PCT"]), datetime.now(IST).date())
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
    # Root stays at WARNING so the Breeze library's own logs (full API replies with bank details) are not
    # printed or saved; only the guardian's lines are logged at INFO.
    logging.basicConfig(level=logging.WARNING, format="%(asctime)s %(message)s",
                        handlers=[logging.FileHandler(LOG_FILE, encoding="utf-8"), logging.StreamHandler()])
    log.setLevel(logging.INFO)
    for name in list(logging.root.manager.loggerDict):
        if name != "guardian":
            logging.getLogger(name).setLevel(logging.WARNING)
    cmd = sys.argv[1] if len(sys.argv) > 1 else ""
    if cmd == "login":
        login_link()
    elif cmd == "check":
        check()
    elif cmd == "run":
        run(live_flag="--live" in sys.argv)
    else:
        print(__doc__)
