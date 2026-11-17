"""Market data via yfinance with an in-memory cache to avoid rate limits."""
import logging
from datetime import date, datetime, time as dtime, timedelta

import pandas as pd
import yfinance as yf

import store
from config import IST

log = logging.getLogger(__name__)

SESSION_OPEN = dtime(9, 15)
SESSION_CLOSE = dtime(15, 30)


def market_open(now=None):
    now = now or datetime.now(IST)
    return now.weekday() < 5 and SESSION_OPEN <= now.time() <= SESSION_CLOSE


def session_fraction(now=None):
    """Fraction of the NSE cash session elapsed (used to project partial-day volume)."""
    now = now or datetime.now(IST)
    if not market_open(now):
        return 1.0
    start = now.replace(hour=9, minute=15, second=0, microsecond=0)
    return min(1.0, max(0.0, (now - start).total_seconds() / (375 * 60)))


def default_ttl():
    return 300 if market_open() else 3600


def history(tickers, period="1y", ttl=None):
    """Daily OHLCV per ticker -> {ticker: DataFrame}. Missing tickers are omitted."""
    ttl = ttl or default_ttl()
    out, missing = {}, []
    for t in tickers:
        df = store.cache_get(f"hist:{period}:{t}")
        if df is None:
            missing.append(t)
        else:
            out[t] = df

    for i in range(0, len(missing), 40):
        chunk = missing[i:i + 40]
        try:
            raw = yf.download(chunk, period=period, interval="1d", group_by="ticker",
                              auto_adjust=True, threads=True, progress=False)
        except Exception as exc:  # network / Yahoo hiccup: serve what we have
            log.warning("yfinance download failed for %s: %s", chunk, exc)
            continue
        if raw is None or raw.empty:
            continue
        for t in chunk:
            try:
                df = raw[t] if isinstance(raw.columns, pd.MultiIndex) else raw
            except KeyError:
                continue
            df = df.dropna(subset=["Close"]).copy()
            if df.empty:
                continue
            idx = pd.to_datetime(df.index)
            if idx.tz is not None:
                idx = idx.tz_localize(None)
            df.index = idx
            store.cache_set(f"hist:{period}:{t}", df, ttl)
            out[t] = df
    try:
        repair_nse(out)
    except Exception as exc:   # a repair must never stop the data
        log.warning("NSE bar repair failed: %s", exc)
    return out


# ------------------------------------------------------------------ missing NSE bars
# Between about midnight and 07:30 IST Yahoo's daily history often lacks the last NSE session's bar (and once
# carried a Sunday-dated one). From 3 to 17 Nov 2026 most weekday morning plans were built on a NIFTY close one
# session old, and the stock picks on old share prices. So an NSE ticker whose last bar is older than the last
# completed session gets that session's bar from NSE itself: index closes from NSE's index list, share prices from
# NSE's daily cash-market file (published in the evening).
NSE_INDEX = {"^NSEI": "NIFTY 50", "^NSEBANK": "NIFTY BANK", "^INDIAVIX": "INDIA VIX"}
CM_URL = "https://nsearchives.nseindia.com/content/cm/BhavCopy_NSE_CM_0_0_0_{:%Y%m%d}_F_0000.csv.zip"
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126 Safari/537.36"


def last_session(now=None):
    """The last NSE session whose close (15:30) has happened."""
    from nse_calendar import is_trading_day
    now = now or datetime.now(IST)
    d = now.date()
    if is_trading_day(d) and now.time() >= SESSION_CLOSE:
        return d
    d -= timedelta(days=1)
    while not is_trading_day(d):
        d -= timedelta(days=1)
    return d


def nse_index_bars():
    """{index name: (date, open, high, low, close)} for the last session, from NSE's index list. {} on failure."""
    cached = store.cache_get("nse:allIndices")
    if cached is not None:
        return cached
    out = {}
    try:
        import live
        nse = live.NSEClient()
        nse.warm()
        d = nse.get("allIndices")
        day = datetime.strptime(d["timestamp"][:11], "%d-%b-%Y").date()
        for r in d.get("data") or []:
            if r.get("index") in NSE_INDEX.values() and r.get("last"):
                out[r["index"]] = (day, float(r.get("open") or r["last"]), float(r.get("high") or r["last"]),
                                   float(r.get("low") or r["last"]), float(r["last"]))
    except Exception as exc:
        log.warning("NSE index list unavailable: %s", exc)
    store.cache_set("nse:allIndices", out, 600)
    return out


def nse_share_bars(day):
    """{symbol: (open, high, low, close, volume)} for one session from NSE's cash-market file. {} if not out yet."""
    key = f"nse:cm:{day}"
    cached = store.cache_get(key)
    if cached is not None:
        return cached
    out = {}
    try:
        import io
        import zipfile
        import requests
        r = requests.get(CM_URL.format(day), headers={"User-Agent": UA}, timeout=30)
        if r.status_code == 200:
            z = zipfile.ZipFile(io.BytesIO(r.content))
            X = pd.read_csv(z.open(z.namelist()[0]))
            X = X[X["SctySrs"].isin(["EQ", "BE", "BZ"])]
            out = {s: (float(o), float(h), float(lo), float(c), float(v)) for s, o, h, lo, c, v in
                   X[["TckrSymb", "OpnPric", "HghPric", "LwPric", "ClsPric", "TtlTradgVol"]].itertuples(index=False)}
    except Exception as exc:
        log.warning("NSE cash-market file for %s unavailable: %s", day, exc)
    store.cache_set(key, out, 1800)
    return out


def repair_nse(hist, now=None):
    """Drop weekend-dated bars of NSE tickers and add the last session's bar when Yahoo lacks it. Returns the
    tickers that were filled."""
    want = last_session(now)
    filled = []
    for t, df in list(hist.items()):
        if not (t.endswith(".NS") or t in NSE_INDEX) or df is None or df.empty:
            continue
        df = df[df.index.dayofweek < 5]
        last = df.index[-1].date() if len(df) else None
        if last is not None and last < want and (want - last).days <= 10:
            if t in NSE_INDEX:
                b = nse_index_bars().get(NSE_INDEX[t])
                bar = b[1:] + (0.0,) if b and b[0] == want else None
            else:
                bar = nse_share_bars(want).get(t[:-3])
            if bar:
                row = pd.DataFrame([dict(zip(["Open", "High", "Low", "Close", "Volume"], bar))], index=[pd.Timestamp(want)])
                df = pd.concat([df, row[[c for c in row.columns if c in df.columns]]])
                filled.append(t)
        hist[t] = df
    if filled:
        log.warning("filled %d NSE bar(s) for %s from NSE (Yahoo lacked them): %s", len(filled), want, ", ".join(filled[:8]))
    return filled


def quote_table(hist, names):
    """Last price and day change for a {name: ticker} map."""
    rows = []
    for name, t in names.items():
        df = hist.get(t)
        if df is None or len(df) < 2:
            continue
        last, prev = float(df["Close"].iloc[-1]), float(df["Close"].iloc[-2])
        chg = (last - prev) if t == "^TNX" else (last / prev - 1) * 100
        rows.append({
            "name": name, "symbol": t, "last": round(last, 2),
            "change": round(chg, 3), "change_unit": "pts" if t == "^TNX" else "%",
            "as_of": df.index[-1].date().isoformat(),
        })
    return rows


def last_bar_date(ticker="^NSEI"):
    df = history([ticker], period="1mo").get(ticker)
    return None if df is None or df.empty else df.index[-1].date()


def today_ist():
    return datetime.now(IST).date()


def next_session_date(now=None):
    """The session the plan is for: today if it is a trading day and before the close, else the next trading day."""
    from nse_calendar import is_trading_day, next_trading_day
    now = now or datetime.now(IST)
    d = now.date()
    if is_trading_day(d) and now.time() < SESSION_CLOSE:
        return d
    return next_trading_day(d)


def as_date(x):
    if isinstance(x, date) and not isinstance(x, datetime):
        return x
    return pd.Timestamp(x).date()
