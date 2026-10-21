"""Daily jobs. Run by GitHub Actions on a cron (see .github/workflows/daily-engine.yml)
or by the FastAPI scheduler if you host the API yourself."""
import logging
from datetime import date, datetime, timedelta

import os

import data
import learning
import review
import macro
import screener
import store

log = logging.getLogger(__name__)

DISCLAIMER = ("Algorithmic research output based on technical rules and statistics. Not investment "
              "advice. Not a SEBI-registered investment adviser or research analyst. No returns are "
              "guaranteed; options can lose 100% of premium and more for sellers. Verify every level "
              "with your broker and consult a SEBI-registered adviser before trading.")


def _next_weekday(d):
    from nse_calendar import next_trading_day
    return next_trading_day(d)


def timeline(session, expiry):
    """Clock-based action plan (IST). NSE holidays are not modelled — skip holidays."""
    d1 = date.fromisoformat(session)
    d2 = _next_weekday(d1)
    steps = [
        (d1, "07:45", "09:00", "prep", "Read the final plan", "The ~07:15 run is final (US close, Dow and US futures, Europe, Asia open). Read the expected open, bias and trades. Set price alerts at each entry and stop-loss."),
        (d1, "09:00", "09:15", "wait", "Pre-open", "Place no market orders. Note where your stocks are indicated to open."),
        (d1, "09:15", "09:30", "wait", "Wait", "Opening 15 min is the most volatile. Cancel any stock that opens above its T1 or below its SL."),
        (d1, "09:30", "10:30", "act", "Stock buy window", "Buy only if price is holding above the entry and the 15-min opening high. Place the SL order immediately."),
        (d1, "09:45", "11:00", "act", "Options window", "Spreads / ATM buys only if the index respects the support/resistance shown. Iron condor / fly only after 10:00 with the index inside the range."),
        (d1, "11:30", "14:30", "manage", "Manage only", "No new stock entries. If it did not trigger, skip it. Keep stop-loss orders in place."),
        (d1, "14:30", "15:00", "exit", "Option buyers exit", "Exit option buys by 15:00 if target not hit (time decay). Spreads/condors: hold only if still inside the plan."),
        (d1, "16:30", "17:00", "review", "Review", "The engine grades today's plan. Check the Results tab: what worked, what the gap was."),
        (d2, "09:15", "15:15", "exit", "Day 2: exit by 15:15", "Book half at T1, trail SL to entry. Exit everything still open by 15:15 (this is a 1–2 day setup)."),
    ]
    out = [{"date": d.isoformat(), "start": a, "end": b, "kind": k, "title": t, "do": txt,
            "when": f"{d:%a %d %b} · {a}–{b}"} for d, a, b, k, t, txt in steps]
    out.append({"date": expiry, "start": "14:30", "end": "15:30", "kind": "exit", "title": "Expiry day",
                "do": "Do not hold short options into the last hour of expiry day.", "when": f"Expiry {expiry}"})
    return out


def _expected_open(bias):
    pre = bias.get("preopen") or {}
    if "expected_gap_pct" not in pre:
        return None
    g = pre["expected_gap_pct"]
    if abs(g) >= 0.5:
        tip = (f"Big gap {'up' if g > 0 else 'down'} expected. Place no market orders at 09:15. Stocks that open above "
               "their T1 are skipped. Let the first hour settle before any option entry.")
    elif abs(g) >= 0.2:
        tip = f"Moderate gap {'up' if g > 0 else 'down'} expected. Use limit orders only, and wait for 09:30."
    else:
        tip = "Flat open expected. Normal entry windows apply."
    return {"gap_pct": g, "error_pct": pre.get("gap_typical_error_pct"), "day_pct": pre.get("expected_day_pct"),
            "snapshot": pre.get("snapshot_ist"), "stage": pre.get("stage"), "tip": tip,
            "markets": pre.get("markets", []), "drivers": pre.get("drivers", []), "wf": pre.get("wf", {})}


def build_plan(bias, scan):
    """One-screen decision summary for the app's home tab."""
    label = bias["bias"]["label"]
    opts = []
    for name, o in bias["options"].items():
        s = o["strategies"][0]
        legs = " + ".join(f"{l['action']} {l['strike']} {l['type']}" for l in s["legs"])
        opts.append({
            "index": name, "strategy": s["name"], "legs": legs, "expiry": o["expiry"],
            "lot_size": o["lot_size"], "entry": s["entry"], "target": s["target"], "stop_loss": s["stop_loss"],
            "max_loss": s["max_loss"], "max_reward": s["max_reward"], "reward_risk": s["reward_risk"],
            "decision": o.get("setup", {}).get("decision", "TRADE"), "decision_reason": o.get("setup", {}).get("reason"),
            "enter_at": "10:15 IST, after the first hour" if s["net_premium"] > 0 else "09:45–11:00 IST",
            "exit_by": f"target / stop, else expiry {o['expiry']}" if s["net_premium"] > 0 else "15:00 IST same day if no target",
            "setup_stats": o.get("setup", {}).get("stats"),
        })
    picks = [c for c in scan["candidates"] if c["grade"] in ("A", "B")][:5]
    conf = bias["model"].get("directional_accuracy")
    return {
        "generated_at": store.now_ist().isoformat(),
        "session": bias["target_session"],
        "stage": (bias.get("preopen") or {}).get("stage", "final"),
        "expected_open": _expected_open(bias),
        "headline": {"BULLISH": "Market bias UP — favour buying dips",
                     "BEARISH": "Market bias DOWN — favour selling rallies / puts",
                     "RANGEBOUND": "Market bias SIDEWAYS — favour hedged premium selling"}[label],
        "bias": label, "score": bias["bias"]["score"],
        "bias_track_record_pct": conf,
        "options": opts,
        "stocks": [{"symbol": c["symbol"], "grade": c["grade"], "action": c["action"], "entry": c["entry"],
                    "enter_at": "09:30–10:30 IST, only above the 15-min opening high", "exit_by": "day 2, 15:15 IST",
                    "stop": c["stop"], "t1": c["t1"], "t2": c["t2"], "win_prob": c["win_prob"],
                    "vol_badge": c["vol_badge"]} for c in picks],
        "market_gate_open": scan.get("market_gate_open"),
        "events": [e for e in bias.get("events", []) if not e.get("past") or e.get("affects") == bias["target_session"]],
        "stock_track_record": None,
        "no_trade_reason": None if picks else "No stock passed all 4 rules with a positive expected value. Sitting out is a valid decision.",
        "timeline": timeline(bias["target_session"], opts[0]["expiry"] if opts else "-"),
        "golden_rules": [
            "Only grade A/B stocks. C = watch, don't trade.",
            "Stop-loss order goes in the moment you buy. Never move it down.",
            "Risk max 1% of capital per trade, max 3 open trades.",
            "No trigger in the buy window = no trade. Missing a trade costs nothing.",
            "If the app's live hit-rate (Learning tab) drops below its backtest, cut size in half.",
        ],
        "disclaimer": DISCLAIMER,
    }


RUNS = [("00:07", "midnight preview"), ("07:13", "final pre-market plan"), ("16:17", "after-close learning")]


def next_run(now=None):
    now = now or store.now_ist()
    d = now.date()
    from nse_calendar import is_trading_day, next_trading_day
    for _ in range(8):
        # the midnight run belongs to the coming session; skip days with no session
        if is_trading_day(d):
            for hm, name in RUNS:
                t = datetime.combine(d, datetime.strptime(hm, "%H:%M").time(), store.now_ist().tzinfo)
                if t > now:
                    return {"at": t.isoformat(), "name": name}
        d += timedelta(days=1)
    return None


def publish_all(bias=None, scan=None, task="manual"):
    bias = bias or macro.global_bias()
    scan = scan or screener.scan()
    bias = {**bias, "disclaimer": DISCLAIMER}
    scan = {**scan, "disclaimer": DISCLAIMER}
    store.publish("global-bias.json", bias)
    store.publish("volume-breakouts.json", scan)
    store.publish("performance.json", learning.performance())
    store.publish("plan.json", build_plan(bias, scan))
    if store._read(os.path.join(store.PUBLIC_DIR, "review.json"), None) is None:
        store.publish("review.json", {"latest": None, "history": [], "totals": {}})
    plan = build_plan(bias, scan)
    import nse_calendar
    store.publish("meta.json", {"updated": store.now_ist().isoformat(), "session": plan["session"], "task": task,
                                "stage": plan.get("stage"), "next_run": next_run(),
                                "holidays": sorted(h for h in nse_calendar.holidays() if h >= store.now_ist().date().isoformat())[:30]})
    # Freeze the plan as it stood before its session opened; the evening review grades exactly this.
    now = store.now_ist()
    sess = date.fromisoformat(plan["session"])
    if sess > now.date() or (sess == now.date() and now.strftime("%H:%M") < "09:15"):
        review.archive_plan(bias, plan, scan)
    import render_page
    render_page.write(os.path.join(render_page.PUBLIC_DIR, "static.html"))


def premarket():
    bias = macro.global_bias(force=True)
    stage = (bias.get("preopen") or {}).get("stage", "final")
    if stage == "final":  # only the 07:30 run is graded; the midnight run is a preview
        learning.log_bias(bias)
        learning.journal_index_views(bias)
    publish_all(bias=bias, task="final pre-market plan" if stage == "final" else "midnight preview")
    return {"bias": bias["bias"]["label"], "session": bias["target_session"], "stage": stage,
            "source": bias["bias"].get("source")}


def postclose():
    """Close of day: journal today's scan, grade older signals, retrain, publish tomorrow's view."""
    out = {}
    try:
        rv = review.run()
        out["review"] = rv["summary"] if rv else "no archived plan for today"
    except Exception as exc:  # a failed review must never block learning
        out["review"] = f"failed: {exc}"
    if data.last_bar_date() == data.today_ist():
        state = store.latest_state()
        public, rows = screener.scan_full(with_news=True)
        out["journaled"] = learning.journal_scan(rows, state)
    else:
        out["journaled"] = "skipped (no NSE session today)"
    learning.fill_bias_actuals()
    out["evaluated"] = learning.evaluate_open()
    out["retrain"] = learning.retrain()["note"]
    try:
        import options_lab
        options_lab.run()
        out["options_lab"] = "updated"
    except Exception as exc:
        out["options_lab"] = f"failed: {exc}"
    store._cache.clear()  # new params -> fresh scan & bias
    bias = macro.global_bias(force=True)
    publish_all(bias=bias, scan=screener.scan(), task="after-close learning")
    return out


def learn():
    learning.fill_bias_actuals()
    n = learning.evaluate_open()
    r = learning.retrain()
    return {"evaluated": n, **r}
