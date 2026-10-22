"""World markets in one table -> docs/markets.json, for the app's Markets page.

GIFT Nifty comes from NSE's market-status feed (the real quote); everything else from
Yahoo 1-minute data (may lag 1-15 min). Refreshed by the live run every ~10 min in
market hours and by the midnight, morning and evening runs.
"""
import logging
import os
from datetime import datetime

import store
from config import IST, PUBLIC_DIR
from live import NSEClient, yahoo_quotes

log = logging.getLogger(__name__)

# (name, Yahoo symbol, True if a rise is bad for Indian stocks, None = neutral)
GROUPS = [
    ("India", [("Nifty 50", "^NSEI", False), ("Bank Nifty", "^NSEBANK", False), ("Sensex", "^BSESN", False),
               ("India VIX (fear)", "^INDIAVIX", True)]),
    ("US", [("S&P 500", "^GSPC", False), ("Nasdaq", "^IXIC", False), ("Dow Jones", "^DJI", False),
            ("S&P futures", "ES=F", False), ("Dow futures", "YM=F", False)]),
    ("Europe", [("DAX (Germany)", "^GDAXI", False), ("FTSE (UK)", "^FTSE", False), ("Euro Stoxx 50", "^STOXX50E", False)]),
    ("Asia", [("Nikkei (Japan)", "^N225", False), ("Hang Seng", "^HSI", False), ("Kospi (Korea)", "^KS11", False),
              ("Shanghai", "000001.SS", False)]),
    ("Money & commodities", [("Brent crude", "BZ=F", True), ("Gold", "GC=F", None), ("Dollar index", "DX-Y.NYB", True),
                             ("USD / INR", "INR=X", True), ("US 10-yr yield", "^TNX", True)]),
]


def gift_nifty():
    nse = NSEClient()
    nse.warm()
    g = nse.get("marketStatus").get("giftnifty") or {}
    if not g.get("LASTPRICE"):
        return None
    last, chg = float(g["LASTPRICE"]), float(g.get("DAYCHANGE") or 0)
    return {"last": round(last, 2), "prev_close": round(last - chg, 2), "chg_pct": float(g.get("PERCHANGE") or 0),
            "as_of": str(g.get("TIMESTMP", ""))[-5:] or None}


def _effect(q, bad_up):
    if not q:
        return "na"
    if bad_up is None:
        return "flat"
    c = q["chg_pct"] * (-1 if bad_up else 1)
    return "green" if c > 0.25 else "red" if c < -0.25 else "flat"


def build():
    quotes = yahoo_quotes([sym for _, rows in GROUPS for _, sym, _ in rows])
    try:
        gift = gift_nifty()
    except Exception as exc:   # NSE often blocks cloud servers
        log.info("GIFT Nifty unavailable: %s", exc)
        gift = None
    groups = []
    for gname, rows in GROUPS:
        out = [{"name": n, **(quotes.get(s) or {}), "effect": _effect(quotes.get(s), bad)} for n, s, bad in rows]
        if gname == "India":
            out.insert(0, {"name": "GIFT Nifty", **(gift or {}), "effect": _effect(gift, False),
                           "note": "NSE IX, trades 6:30 am to 2:45 am" if gift else "not available right now"})
        groups.append({"group": gname, "rows": out})
    bias = store._read(os.path.join(PUBLIC_DIR, "global-bias.json"), {})
    gt = bias.get("global_table") or {}
    model = next((r for r in gt.get("rows", []) if r.get("model")), None)
    news = sorted((bias.get("news") or {}).get("headlines") or [], key=lambda h: h.get("published") or "", reverse=True)[:8]
    out = {"updated": datetime.now(IST).isoformat(), "groups": groups, "tilt": gt.get("tilt"),
           "model": model, "news": [{k: h.get(k) for k in ("title", "source", "link", "sentiment")} for h in news],
           "sources": "GIFT Nifty: NSE market status. Others: Yahoo (may lag 1-15 min). News: free RSS headlines."}
    store.publish("markets.json", out)
    return {"markets": sum(1 for g in groups for r in g["rows"] if r.get("last")), "gift": bool(gift)}
