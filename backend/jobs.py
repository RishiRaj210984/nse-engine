"""Daily jobs. Run by GitHub Actions on a cron (see .github/workflows/daily-engine.yml)
or by the FastAPI scheduler if you host the API yourself."""
import logging
from datetime import date, timedelta

import data
import learning
import macro
import screener
import store

log = logging.getLogger(__name__)

DISCLAIMER = ("Algorithmic research output based on technical rules and statistics. Not investment "
              "advice. Not a SEBI-registered investment adviser or research analyst. No returns are "
              "guaranteed; options can lose 100% of premium and more for sellers. Verify every level "
              "with your broker and consult a SEBI-registered adviser before trading.")


def _next_weekday(d):
    d += timedelta(days=1)
    while d.weekday() >= 5:
        d += timedelta(days=1)
    return d


def timeline(session, expiry):
    """Clock-based action plan (IST). NSE holidays are not modelled — skip holidays."""
    d1 = date.fromisoformat(session)
    d2 = _next_weekday(d1)
    D1, D2 = f"{d1:%a %d %b}", f"{d2:%a %d %b}"
    return [
        {"when": f"{D1} · 08:00–09:00", "do": "Open the app. Read the bias and plan. Set price alerts at each entry and stop-loss."},
        {"when": f"{D1} · 09:00–09:15", "do": "Pre-open. Place no market orders. Note where your stocks are indicated to open."},
        {"when": f"{D1} · 09:15–09:30", "do": "WAIT. Opening 15 min is the most volatile. Cancel any stock that opens above its T1 or below its SL."},
        {"when": f"{D1} · 09:30–10:30", "do": "STOCK BUY WINDOW. Buy only if price is holding above the entry and the 15-min opening high. Place the SL order immediately."},
        {"when": f"{D1} · 09:45–11:00", "do": "OPTIONS WINDOW. Spreads / ATM buys only if the index respects the support/resistance shown. Iron condor / fly only after 10:00 with the index inside the range."},
        {"when": f"{D1} · after 11:30", "do": "No new stock entries. If it did not trigger, skip it."},
        {"when": f"{D1} · 14:30–15:00", "do": "Option BUYERS: exit by 15:00 if target not hit (time decay). Spreads/condors: hold only if still inside the plan."},
        {"when": f"{D2} · 09:15–15:15", "do": "DAY 2. Book half at T1, trail SL to entry. EXIT everything still open by 15:15 (this is a 1–2 day setup)."},
        {"when": f"Expiry {expiry}", "do": "Do not hold short options into the last hour of expiry day."},
    ]


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
        })
    picks = [c for c in scan["candidates"] if c["grade"] in ("A", "B")][:5]
    conf = bias["model"].get("directional_accuracy")
    return {
        "generated_at": store.now_ist().isoformat(),
        "session": bias["target_session"],
        "headline": {"BULLISH": "Market bias UP — favour buying dips",
                     "BEARISH": "Market bias DOWN — favour selling rallies / puts",
                     "RANGEBOUND": "Market bias SIDEWAYS — favour hedged premium selling"}[label],
        "bias": label, "score": bias["bias"]["score"],
        "bias_track_record_pct": conf,
        "options": opts,
        "stocks": [{"symbol": c["symbol"], "grade": c["grade"], "action": c["action"], "entry": c["entry"],
                    "stop": c["stop"], "t1": c["t1"], "t2": c["t2"], "win_prob": c["win_prob"],
                    "vol_badge": c["vol_badge"]} for c in picks],
        "market_gate_open": scan.get("market_gate_open"),
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


def publish_all(bias=None, scan=None):
    bias = bias or macro.global_bias()
    scan = scan or screener.scan()
    bias = {**bias, "disclaimer": DISCLAIMER}
    scan = {**scan, "disclaimer": DISCLAIMER}
    store.publish("global-bias.json", bias)
    store.publish("volume-breakouts.json", scan)
    store.publish("performance.json", learning.performance())
    store.publish("plan.json", build_plan(bias, scan))
    store.publish("meta.json", {"updated": store.now_ist().isoformat()})
    import render_page
    render_page.write()


def premarket():
    bias = macro.global_bias(force=True)
    learning.log_bias(bias)
    learning.journal_index_views(bias)
    publish_all(bias=bias)
    return {"bias": bias["bias"]["label"], "session": bias["target_session"]}


def postclose():
    """Close of day: journal today's scan, grade older signals, retrain, publish tomorrow's view."""
    out = {}
    if data.last_bar_date() == data.today_ist():
        state = store.latest_state()
        public, rows = screener.scan_full(with_news=True)
        out["journaled"] = learning.journal_scan(rows, state)
    else:
        out["journaled"] = "skipped (no NSE session today)"
    learning.fill_bias_actuals()
    out["evaluated"] = learning.evaluate_open()
    out["retrain"] = learning.retrain()["note"]
    store._cache.clear()  # new params -> fresh scan & bias
    bias = macro.global_bias(force=True)
    publish_all(bias=bias, scan=screener.scan())
    return out


def learn():
    learning.fill_bias_actuals()
    n = learning.evaluate_open()
    r = learning.retrain()
    return {"evaluated": n, **r}
