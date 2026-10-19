"""Render the daily plan as one self-contained HTML page (docs/index.html).

Works on any phone browser with no install. GitHub Pages can serve it straight
from the repo's /public folder.
"""
import html
import json
import os
from datetime import date

from config import PUBLIC_DIR

E = html.escape


def _load(name):
    with open(os.path.join(PUBLIC_DIR, name), encoding="utf-8") as f:
        return json.load(f)


def _inr(x, d=0):
    return "—" if x is None else f"₹{x:,.{d}f}"


CSS = """
:root{--bg:#f6f7f5;--panel:#ffffff;--ink:#14201c;--dim:#5d6b66;--line:#dfe4e1;--accent:#0f6b5c;
--up:#13804b;--down:#c03a2b;--warn:#a86a00;--chip:#eef2f0;
--sans:'IBM Plex Sans',system-ui,-apple-system,Segoe UI,sans-serif;--mono:'IBM Plex Mono',ui-monospace,Consolas,monospace}
@media (prefers-color-scheme:dark){:root:not([data-theme="light"]){--bg:#0d1412;--panel:#141d1a;--ink:#e3ebe7;--dim:#8fa19a;
--line:#24312d;--accent:#3fb59f;--up:#3ccf84;--down:#ff6b5b;--warn:#f0b23a;--chip:#1b2622;color-scheme:dark}}
:root[data-theme="dark"]{--bg:#0d1412;--panel:#141d1a;--ink:#e3ebe7;--dim:#8fa19a;--line:#24312d;--accent:#3fb59f;
--up:#3ccf84;--down:#ff6b5b;--warn:#f0b23a;--chip:#1b2622;color-scheme:dark}
body{background:var(--bg);color:var(--ink);font-family:var(--sans);font-size:15px;line-height:1.5}
.wrap{max-width:760px;margin:0 auto;padding-block:20px 48px;padding-left:max(16px,env(safe-area-inset-left,0px));
padding-right:max(16px,env(safe-area-inset-right,0px));display:grid;gap:22px}
h1{font-size:1.15rem;margin:0;font-weight:600;letter-spacing:.01em}
h2{font-size:.78rem;text-transform:uppercase;letter-spacing:.09em;color:var(--dim);margin:0 0 8px;font-weight:600}
.meta{color:var(--dim);font-size:.85rem}
.mono,td.n,.n{font-family:var(--mono);font-variant-numeric:tabular-nums}
.verdict{background:var(--panel);border:1px solid var(--line);border-radius:10px;padding:18px;display:grid;gap:6px}
.verdict .label{font-family:var(--mono);font-size:2rem;font-weight:600;letter-spacing:.04em;line-height:1.1}
.BULLISH{color:var(--up)}.BEARISH{color:var(--down)}.RANGEBOUND{color:var(--warn)}
.trade{background:var(--panel);border:1px solid var(--line);border-radius:10px;padding:16px;display:grid;gap:10px}
.trade header{display:flex;justify-content:space-between;gap:8px;flex-wrap:wrap;align-items:baseline}
.chip{background:var(--chip);border-radius:999px;padding:2px 10px;font-size:.8rem;color:var(--dim);font-family:var(--mono)}
.legs{display:flex;flex-wrap:wrap;gap:6px}
.leg{font-family:var(--mono);font-size:.85rem;padding:4px 8px;border-radius:6px;border:1px solid var(--line)}
.leg.SELL{color:var(--down)}.leg.BUY{color:var(--up)}
dl{display:grid;grid-template-columns:max-content 1fr;gap:4px 14px;margin:0}
dt{color:var(--dim)}dd{margin:0;min-width:0}
.risk{display:flex;gap:18px;flex-wrap:wrap;font-family:var(--mono);font-size:.9rem}
.risk b{display:block;font-size:.72rem;color:var(--dim);font-family:var(--sans);font-weight:500;text-transform:uppercase;letter-spacing:.06em}
.gate{border-left:3px solid var(--warn);padding:10px 14px;background:var(--panel);border-radius:0 8px 8px 0}
.tbl{overflow-x:auto;background:var(--panel);border:1px solid var(--line);border-radius:10px}
table{border-collapse:collapse;width:100%;font-size:.88rem}
th,td{padding:8px 10px;text-align:left;border-bottom:1px solid var(--line);white-space:nowrap}
th{color:var(--dim);font-weight:500;font-size:.75rem;text-transform:uppercase;letter-spacing:.06em}
tr:last-child td{border-bottom:0}td.n{text-align:right}
.up{color:var(--up)}.down{color:var(--down)}.warn{color:var(--warn)}
ol.time{list-style:none;margin:0;padding:0;display:grid;gap:0;background:var(--panel);border:1px solid var(--line);border-radius:10px}
ol.time li{display:grid;grid-template-columns:11.5rem 1fr;gap:12px;padding:10px 14px;border-bottom:1px solid var(--line)}
ol.time li:last-child{border-bottom:0}
ol.time .when{font-family:var(--mono);font-size:.82rem;color:var(--accent)}
@media (max-width:520px){ol.time li{grid-template-columns:1fr;gap:2px}}
.stats{display:grid;grid-template-columns:repeat(auto-fit,minmax(200px,1fr));gap:10px}
.stat{background:var(--panel);border:1px solid var(--line);border-radius:10px;padding:12px 14px}
.stat .v{font-family:var(--mono);font-size:1.3rem;font-weight:600}
.stat p{margin:2px 0 0;color:var(--dim);font-size:.82rem}
ul.rules{margin:0;padding-left:1.1rem;display:grid;gap:4px}
.fine{font-size:.78rem;color:var(--dim);line-height:1.5}
"""


def render(body_only=False):
    plan, bias, scan, perf = (_load(n) for n in ("plan.json", "global-bias.json", "volume-breakouts.json", "performance.json"))
    b = bias["bias"]
    d = date.fromisoformat(plan["session"])
    out = []
    w = out.append
    w(f"<title>NSE Daily Plan</title>")
    w('<meta name="claude:viewport-fit-sides" content="cover">')
    w('<link rel="preconnect" href="https://fonts.googleapis.com"><link rel="stylesheet" '
      'href="https://fonts.googleapis.com/css2?family=IBM+Plex+Mono:wght@400;600&family=IBM+Plex+Sans:wght@400;500;600&display=swap">')
    w(f"<style>{CSS}</style>")
    w('<main class="wrap">')
    w(f'<div><h1>Plan for {d:%A %d %B %Y}</h1><div class="meta">Built {E(plan["generated_at"][:16].replace("T", " "))} IST '
      f'from the US session of {E(bias["us_session_used"])} and NSE close of {E(bias["reference_date"])}</div></div>')

    acc = plan.get("bias_track_record_pct")
    w(f'<section class="verdict"><div class="label {E(plan["bias"])}">{E(plan["bias"])}</div>'
      f'<div><strong>{E(plan["headline"])}</strong></div>'
      f'<div class="meta">Model expects Nifty {b["predicted_nifty_move_pct"]:+.2f}% (typical day ±{b["typical_daily_move_pct"]:.2f}%) · '
      f'India VIX {b["india_vix"]:.2f} · out-of-sample hit rate {acc if acc is not None else "—"}% on '
      f'{bias["model"].get("directional_calls", "—")} directional calls</div></section>')

    w("<section><h2>Options</h2><div style='display:grid;gap:12px'>")
    for name, o in bias["options"].items():
        s = o["strategies"][0]
        legs = "".join(f'<span class="leg {E(l["action"])}">{E(l["action"])} {l["strike"]} {E(l["type"])} · ≈₹{l["est_premium"]}</span>' for l in s["legs"])
        rr = s["reward_risk"]
        w(f'<article class="trade"><header><strong>{E(name)} · {E(s["name"])}</strong>'
          f'<span class="chip">expiry {E(o["expiry"])} · lot {o["lot_size"]}</span></header>'
          f'<div class="legs">{legs}</div>'
          f'<dl><dt>Enter</dt><dd>{E(s["entry"])}</dd><dt>Target</dt><dd class="up">{E(s["target"])}</dd>'
          f'<dt>Stop</dt><dd class="down">{E(s["stop_loss"])}</dd>'
          f'<dt>Breakeven</dt><dd class="mono">{" / ".join(f"{x:,}" for x in s["breakeven"])}</dd></dl>'
          f'<div class="risk"><div><b>Max loss / lot</b><span class="down">{_inr(s["max_loss"])}</span></div>'
          f'<div><b>Max reward / lot</b><span class="up">{_inr(s["max_reward"]) if s["max_reward"] is not None else "open"}</span></div>'
          f'<div><b>Reward : risk</b>{rr if rr is not None else "—"}</div>'
          f'<div><b>Spot / expected move</b>{o["spot"]:,.0f} ± {o["expected_move"]:,}</div></div>'
          f'<div class="fine">{E(s["notes"])} Premiums are Black-Scholes estimates from India VIX; check the live option chain.</div></article>')
    w("</div></section>")

    w("<section><h2>Stocks for 1–2 days</h2>")
    if plan.get("market_gate_open") is False:
        w('<div class="gate">Market gate is <strong>closed</strong>: Nifty is below its 50-day average or down over 20 days. '
          "Momentum buys are paused, so the list below is a watchlist, not a buy list.</div>")
    rows = (scan["candidates"] + scan["near_misses"])[:10]
    if plan["stocks"]:
        for s in plan["stocks"]:
            w(f'<p><strong>Grade {E(s["grade"])}</strong> · {E(s["action"])}</p>')
    elif not rows:
        w(f'<p>{E(plan["no_trade_reason"] or "")}</p>')
    if rows:
        w('<div class="tbl" style="margin-top:10px"><table><thead><tr><th>Stock</th><th>Grade</th><th>Rules</th>'
          '<th>Close</th><th>Day</th><th>Vol</th><th>RSI</th><th>SL</th><th>T1</th><th>T2</th><th>Note</th></tr></thead><tbody>')
        for c in rows:
            cls = "up" if c["day_chg"] >= 0 else "down"
            w(f'<tr><td><strong>{E(c["symbol"])}</strong></td><td>{E(c["grade"])}</td><td class="n">{c["checks_passed"]}/4</td>'
              f'<td class="n">{c["cmp"]:,.2f}</td><td class="n {cls}">{c["day_chg"]:+.2f}%</td><td class="n">{c["vol_ratio"]:.1f}x</td>'
              f'<td class="n">{c["rsi"]:.0f}</td><td class="n down">{c["stop"]:,.2f}</td><td class="n up">{c["t1"]:,.2f}</td>'
              f'<td class="n up">{c["t2"]:,.2f}</td><td class="warn" style="white-space:normal;min-width:12rem">{E(c.get("skip_reason") or "")}</td></tr>')
        w("</tbody></table></div>")
    w("</section>")

    w('<section><h2>Timeline (IST)</h2><ol class="time">')
    for t in plan["timeline"]:
        w(f'<li><span class="when">{E(t["when"])}</span><span>{E(t["do"])}</span></li>')
    w("</ol></section>")

    w('<section><h2>Global close</h2><div class="tbl"><table><thead><tr><th>Market</th><th style="text-align:right">Last</th>'
      '<th style="text-align:right">Change</th><th>As of</th></tr></thead><tbody>')
    for t in bias["tickers"]:
        cls = "up" if t["change"] >= 0 else "down"
        unit = "%" if t["change_unit"] == "%" else " pts"
        w(f'<tr><td>{E(t["name"])}</td><td class="n">{t["last"]:,.2f}</td><td class="n {cls}">{t["change"]:+.2f}{unit}</td><td class="mono">{E(t["as_of"])}</td></tr>')
    w("</tbody></table></div></section>")

    bt, bw = perf["backtest_stocks"], perf.get("bias_walk_forward") or {}
    w('<section><h2>Track record so far</h2><div class="stats">')
    w(f'<div class="stat"><div class="v">{bw.get("directional_accuracy", "—")}%</div><p>Bias calls correct, out-of-sample, {bw.get("directional_calls", "—")} calls in {bw.get("oos_days", "—")} days</p></div>')
    if bt.get("trades"):
        w(f'<div class="stat"><div class="v">{bt["win_rate"]}%</div><p>Stock win rate, 2-year backtest, {bt["trades"]} trades</p></div>')
        w(f'<div class="stat"><div class="v">{bt["avg_r"]:+.2f}R</div><p>Average result per stock trade after costs (1R = planned risk)</p></div>')
    lv = perf["live_stocks"]
    w(f'<div class="stat"><div class="v">{lv.get("trades", 0)}</div><p>Live trades graded. Live results start 2 trading days after the first signal.</p></div>')
    w("</div></section>")

    w('<section><h2>Rules</h2><ul class="rules">' + "".join(f"<li>{E(r)}</li>" for r in plan["golden_rules"]) + "</ul></section>")
    w(f'<p class="fine">{E(plan["disclaimer"])}</p>')
    w("</main>")
    page = "\n".join(out)
    if body_only:
        return page
    return ('<!doctype html><html lang="en"><head><meta charset="utf-8">'
            '<meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover"></head><body>'
            + page + "</body></html>")


def write(path=None, body_only=False):
    path = path or os.path.join(PUBLIC_DIR, "index.html")
    with open(path, "w", encoding="utf-8") as f:
        f.write(render(body_only))
    return path


if __name__ == "__main__":
    import sys
    print(write(sys.argv[1] if len(sys.argv) > 1 else None, body_only="--body" in sys.argv))
