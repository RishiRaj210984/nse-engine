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
    plan_lines, placed = [], []
    for l in legs:
        q = _q(breeze, index, l, expiry_iso)
        if live and not q.get("ltp"):
            return False, f"ICICI gave no live price for {l['strike']} {l['type']}. Placed so far: {placed or 'none'}. Nothing more placed."
        ltp = float(q.get("ltp") or l["est_premium"])
        px = float(q.get("best_offer_price") or ltp * 1.02) if l["action"] == "BUY" else float(q.get("best_bid_price") or ltp * 0.98)
        px = max(0.05, round(px * 20) / 20)
        plan_lines.append(f"{l['action']} {l['strike']} {l['type']} x{qty} @ {px:.2f}")
        if not live:
            continue
        r = breeze.place_order(stock_code=BREEZE_CODE[index], exchange_code="NFO", product="options", action=l["action"].lower(),
                               order_type="limit", stoploss="", quantity=str(qty), price=f"{px:.2f}", validity="day",
                               validity_date=now.strftime("%Y-%m-%dT06:00:00.000Z"), disclosed_quantity="0",
                               expiry_date=expiry_iso, right="call" if l["type"] == "CE" else "put", strike_price=str(int(l["strike"])),
                               user_remark="tradedesk")
        oid = ((r or {}).get("Success") or {}).get("order_id")
        if not oid:
            return False, f"order rejected at {plan_lines[-1]}: {str((r or {}).get('Error'))[:150]}. Placed so far: {placed or 'none'}. CHECK ICICI NOW."
        filled = False
        for _ in range(12):          # wait up to ~60 s for the fill
            d = ((breeze.get_order_detail(exchange_code="NFO", order_id=oid) or {}).get("Success") or [{}])[0]
            st = str(d.get("status", "")).lower()
            if st in ("executed", "complete", "completed", "traded"):
                filled = True
                break
            if st in ("rejected", "cancelled", "canceled"):
                break
            time.sleep(5)
        if not filled:
            return False, f"not filled: {plan_lines[-1]} (order {oid}). Placed so far: {placed or 'none'}. CHECK ICICI NOW: cancel or adjust."
        placed.append(plan_lines[-1])
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
            st, note, pnl, pct = evaluate(t, state, now.strftime("%H:%M"), int(env["TRAIL_PCT"]))
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
