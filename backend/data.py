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
    return out


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
    """The session the pre-market bias is for: today before the close, else next weekday."""
    now = now or datetime.now(IST)
    d = now.date()
    if now.weekday() < 5 and now.time() < SESSION_CLOSE:
        return d
    d += timedelta(days=1)
    while d.weekday() >= 5:
        d += timedelta(days=1)
    return d


def as_date(x):
    if isinstance(x, date) and not isinstance(x, datetime):
        return x
    return pd.Timestamp(x).date()
