"""NSE trading calendar: holidays, next session, and expiry dates with holiday shifts.

Holidays come from NSE's own holiday API when reachable, else from the last
saved copy (state/holidays.json), else from the list below. When an expiry day
is a holiday, NSE moves the expiry to the previous trading day.
"""
import json
import logging
import os
from datetime import date, timedelta

import requests

import store

log = logging.getLogger(__name__)
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126 Safari/537.36"

# From NSE holiday-master (F&O = cash market) for 2026. Refreshed automatically when NSE is reachable.
FALLBACK = {
    "2026-01-15", "2026-01-26", "2026-02-15", "2026-03-03", "2026-03-21", "2026-03-26", "2026-03-31",
    "2026-04-03", "2026-04-14", "2026-05-01", "2026-05-28", "2026-06-26", "2026-08-15", "2026-09-14",
    "2026-10-02", "2026-10-20", "2026-11-08", "2026-11-10", "2026-11-24", "2026-12-25",
}


def _fetch():
    s = requests.Session()
    s.headers.update({"User-Agent": UA, "Accept-Language": "en-US,en;q=0.9"})
    s.get("https://www.nseindia.com/resources/exchange-communication-holidays", timeout=10)
    r = s.get("https://www.nseindia.com/api/holiday-master?type=trading", timeout=10,
              headers={"Referer": "https://www.nseindia.com/resources/exchange-communication-holidays"})
    r.raise_for_status()
    from datetime import datetime
    return sorted({datetime.strptime(x["tradingDate"], "%d-%b-%Y").date().isoformat() for x in r.json()["FO"]})


def holidays():
    cached = store.cache_get("holidays")
    if cached is not None:
        return cached
    path = store.state_path("holidays.json")
    saved = set(store._read(path, []))
    try:
        fresh = set(_fetch())
        if fresh:
            saved |= fresh
            store._write(path, sorted(saved), indent=1)
    except Exception as exc:
        log.info("NSE holiday API unreachable (%s); using saved list", exc)
    out = frozenset(saved | FALLBACK)
    store.cache_set("holidays", out, 12 * 3600)
    return out


def is_trading_day(d):
    return d.weekday() < 5 and d.isoformat() not in holidays()


def next_trading_day(d, include=False):
    d = d if include else d + timedelta(days=1)
    while not is_trading_day(d):
        d += timedelta(days=1)
    return d


def prev_trading_day(d, include=False):
    d = d if include else d - timedelta(days=1)
    while not is_trading_day(d):
        d -= timedelta(days=1)
    return d


def shift_expiry(e):
    """Expiry on a holiday or weekend moves to the previous trading day."""
    return prev_trading_day(e, include=True)
