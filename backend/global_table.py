"""One-glance global markets table with a simple 'effect on Nifty' signal per row.

The colour rules are deliberately simple thresholds so they are easy to read;
the pre-open model's combined forecast (expected gap) is the actual input to
the plan and is shown as the last row.
"""
from datetime import datetime

from config import IST

SYMBOLS = {
    "^GSPC": "S&P 500", "^IXIC": "Nasdaq", "^DJI": "Dow",
    "ES=F": "S&P fut", "NQ=F": "Nasdaq fut", "YM=F": "Dow fut",
    "^GDAXI": "DAX", "^FTSE": "FTSE", "^STOXX50E": "Euro Stoxx",
    "^N225": "Nikkei", "^HSI": "Hang Seng", "^KS11": "Kospi",
    "CL=F": "WTI", "BZ=F": "Brent", "^TNX": "US 10Y", "DX-Y.NYB": "Dollar index",
    "INR=X": "USD/INR", "GC=F": "Gold", "^INDIAVIX": "India VIX",
}
GREEN, FLAT, RED = "green", "flat", "red"


def _avg(vals):
    vals = [v for v in vals if v is not None]
    return sum(vals) / len(vals) if vals else None


def _eq_effect(avg, thr):
    if avg is None:
        return FLAT
    return GREEN if avg >= thr else RED if avg <= -thr else FLAT


def _fmt(q, name):
    return f"{name} {q['chg_pct']:+.1f}%" if q else f"{name} —"


def build(quotes, preopen=None):
    q = lambda s: quotes.get(s)
    chg = lambda s: (q(s) or {}).get("chg_pct")
    rows = []

    us = [chg("^GSPC"), chg("^IXIC")]
    a = _avg(us)
    rows.append({"group": "US, last close", "move": ", ".join(_fmt(q(s), SYMBOLS[s]) for s in ("^GSPC", "^IXIC", "^DJI")),
                 "effect": _eq_effect(a, 0.3),
                 "note": {GREEN: "Positive lead for India", RED: "Negative lead for India", FLAT: "Little signal"}[_eq_effect(a, 0.3)]})

    pm = {m["key"]: m["move_pct"] for m in (preopen or {}).get("markets", [])}
    fut = [pm.get("es"), pm.get("nq")] if pm else [chg("ES=F"), chg("NQ=F")]
    a = _avg(fut)
    src = "since Nifty's close" if pm else "today"
    rows.append({"group": "US futures now",
                 "move": (f"S&P {pm['es']:+.2f}%, Nasdaq {pm['nq']:+.2f}%, Dow {pm.get('ym', 0):+.2f}% ({src})" if pm
                          else ", ".join(_fmt(q(s), SYMBOLS[s]) for s in ("ES=F", "NQ=F", "YM=F"))),
                 "effect": _eq_effect(a, 0.25),
                 "note": {GREEN: "Futures point to a firmer open", RED: "Futures point to a softer open", FLAT: "Flat"}[_eq_effect(a, 0.25)]})

    a = _avg([chg("^GDAXI"), chg("^FTSE"), chg("^STOXX50E")])
    rows.append({"group": "Europe, last close", "move": ", ".join(_fmt(q(s), SYMBOLS[s]) for s in ("^GDAXI", "^FTSE", "^STOXX50E")),
                 "effect": _eq_effect(a, 0.3), "note": {GREEN: "Supportive", RED: "Weak", FLAT: "Mixed / flat"}[_eq_effect(a, 0.3)]})

    asia = [chg("^N225"), chg("^HSI"), chg("^KS11")]
    a = _avg(asia)
    worst = min((v for v in asia if v is not None), default=None)
    rows.append({"group": "Asia today", "move": ", ".join(_fmt(q(s), SYMBOLS[s]) for s in ("^N225", "^HSI", "^KS11")),
                 "effect": _eq_effect(a, 0.4),
                 "note": ("Clearly weak" if a is not None and a <= -1 else "Weak" if _eq_effect(a, 0.4) == RED else
                          "Strong" if _eq_effect(a, 0.4) == GREEN else "Mixed / flat")
                         + (" (one market down over 3%)" if worst is not None and worst <= -3 else "")})

    wti, brent = q("CL=F"), q("BZ=F")
    c = _avg([chg("CL=F"), chg("BZ=F")])
    eff = RED if c is not None and c >= 1 else GREEN if c is not None and c <= -1 else FLAT
    high = brent and brent["last"] >= 95
    rows.append({"group": "Crude oil", "move": f"WTI ~${wti['last']:.0f}, Brent ~${brent['last']:.0f} ({c:+.1f}%)" if wti and brent and c is not None else "—",
                 "effect": RED if (eff == FLAT and high) else eff,
                 "note": " · ".join(x for x in ("Rising oil hurts India" if eff == RED else "Falling oil helps India" if eff == GREEN else "",
                                                 "Price is high for India" if high else "") if x) or "Steady"})

    t = q("^TNX")
    if t:
        bp = (t["last"] - t["prev_close"]) * 100
        eff = RED if bp >= 5 or t["last"] >= 5 else GREEN if bp <= -5 else FLAT
        rows.append({"group": "US 10-year yield", "move": f"{t['last']:.2f}% ({bp:+.0f} bp)", "effect": eff,
                     "note": "High yields pull money from emerging markets" if eff == RED else "Easing yields help" if eff == GREEN else "Steady"})

    d = q("DX-Y.NYB")
    if d:
        eff = RED if d["chg_pct"] >= 0.3 else GREEN if d["chg_pct"] <= -0.3 else FLAT
        rows.append({"group": "Dollar index", "move": f"{d['last']:.2f} ({d['chg_pct']:+.2f}%)", "effect": eff,
                     "note": "Strong dollar weighs on India" if eff == RED else "Weaker dollar helps" if eff == GREEN else "Steady"})

    r = q("INR=X")
    if r:
        eff = RED if r["chg_pct"] >= 0.25 else GREEN if r["chg_pct"] <= -0.25 else FLAT
        rows.append({"group": "Rupee", "move": f"{r['last']:.2f} per $ ({r['chg_pct']:+.2f}%)", "effect": eff,
                     "note": "Rupee weakening" if eff == RED else "Rupee strengthening" if eff == GREEN else "Steady"})

    v = q("^INDIAVIX")
    if v:
        eff = RED if v["chg_pct"] >= 5 else GREEN if v["chg_pct"] <= -5 else FLAT
        rows.append({"group": "India VIX (fear)", "move": f"{v['last']:.2f} ({v['chg_pct']:+.1f}%)", "effect": eff,
                     "note": "Fear rising: wider swings" if eff == RED else "Fear easing" if eff == GREEN else "Normal"})

    if preopen and "expected_gap_pct" in preopen:
        g = preopen["expected_gap_pct"]
        rows.append({"group": "Model: Nifty open", "move": f"{g:+.2f}% (±{preopen.get('gap_typical_error_pct', 0):.2f}%)",
                     "effect": GREEN if g >= 0.2 else RED if g <= -0.2 else FLAT,
                     "note": f"Combined forecast from all markets ({preopen.get('stage', '')})", "model": True})

    score = sum({GREEN: 1, RED: -1, FLAT: 0}[x["effect"]] for x in rows if not x.get("model"))
    return {"updated": datetime.now(IST).isoformat(), "rows": rows,
            "tilt": "positive" if score >= 2 else "negative" if score <= -2 else "mixed", "score": score}


def fetch_and_build(preopen=None):
    import live
    return build(live.yahoo_quotes(list(SYMBOLS)), preopen)
