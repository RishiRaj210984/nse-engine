"""Real NSE closing prices for NIFTY and BANKNIFTY options, from NSE's daily F&O bhavcopy.

One small file per trading day in state/nse_prices/ (only the strikes and expiries the options lab can use),
so the lab replays its setups on what the options really closed at instead of Black-Scholes estimates.
NSE publishes each day's file in the evening (around 17:30-19:00 IST): the 16:17 run picks up yesterday's.
"""
import io
import logging
import os
import zipfile
from datetime import date, timedelta

import pandas as pd
import requests

import store

log = logging.getLogger(__name__)
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126 Safari/537.36"
URL = "https://nsearchives.nseindia.com/content/fo/BhavCopy_NSE_FO_0_0_0_{:%Y%m%d}_F_0000.csv.zip"
# Kept per day: expiries up to this many days ahead and strikes within this share of the index close.
KEEP = {"NIFTY": (14, 0.10), "BANKNIFTY": (40, 0.20)}


def folder():
    return store.state_path("nse_prices")


def _path(d):
    return os.path.join(folder(), f"{d.isoformat()}.csv.gz")


def compact(raw):
    """Bhavcopy rows (NSE's column names) -> the rows the lab keeps. type 'IX' rows hold the index close."""
    if "FinInstrmTp" in raw:
        raw = raw[raw["FinInstrmTp"] == "IDO"]   # index options only
    raw = raw[raw["TckrSymb"].isin(list(KEEP))]
    out = []
    for sym, (days, band) in KEEP.items():
        X = raw[raw["TckrSymb"] == sym]
        if not len(X):
            continue
        d = pd.to_datetime(X["TradDt"].iloc[0]).date()
        spot = float(X["UndrlygPric"].median())
        exp = pd.to_datetime(X["XpryDt"]).dt.date
        k = X["StrkPric"].astype(float)
        X = X[(exp <= d + timedelta(days=days)) & ((k / spot - 1).abs() <= band)]
        out.append(pd.DataFrame({"sym": sym, "expiry": pd.to_datetime(X["XpryDt"]).dt.date.astype(str),
                                 "strike": X["StrkPric"].astype(float).round().astype(int), "type": X["OptnTp"],
                                 "close": X["ClsPric"].astype(float), "vol": X["TtlTradgVol"].astype(int)}))
        out.append(pd.DataFrame([{"sym": sym, "expiry": "", "strike": 0, "type": "IX", "close": spot, "vol": 0}]))
    return pd.concat(out, ignore_index=True) if out else None


def lots_path():
    return store.state_path("nse_lots.json")


def save_lots(raw, d):
    """Lot size of every F&O stock (from its futures rows), for the stock option trade. Newer days win."""
    if "FinInstrmTp" not in raw or "NewBrdLotQty" not in raw:
        return
    f = raw[raw["FinInstrmTp"] == "STF"]
    if not len(f):
        return
    cur = store._read(lots_path(), {}) or {}
    if cur.get("as_of", "") > d.isoformat():
        return
    lots = {s: int(v) for s, v in f.groupby("TckrSymb")["NewBrdLotQty"].first().items()}
    store._write(lots_path(), {"as_of": d.isoformat(), "lots": lots})


def lots():
    return (store._read(lots_path(), {}) or {}).get("lots") or {}


def fetch_day(d):
    """Download and save one day's file. True if saved, False if NSE has none (holiday / not published yet)."""
    r = requests.get(URL.format(d), headers={"User-Agent": UA}, timeout=30)
    if r.status_code == 404:
        return False
    r.raise_for_status()
    z = zipfile.ZipFile(io.BytesIO(r.content))
    raw = pd.read_csv(z.open(z.namelist()[0]))
    try:
        save_lots(raw, d)
    except Exception as exc:   # lot sizes are a side product: never block the prices
        log.warning("lot sizes not saved: %s", exc)
    rows = compact(raw)
    if rows is None or not len(rows):
        return False
    os.makedirs(folder(), exist_ok=True)
    rows.to_csv(_path(d), index=False, compression="gzip")
    return True


def update(today=None, days_back=12):
    """Fetch the recent days that are missing. Never raises: the lab then uses what is already saved."""
    today = today or store.now_ist().date()
    saved = 0
    for n in range(days_back, -1, -1):
        d = today - timedelta(days=n)
        if d.weekday() >= 5 or os.path.exists(_path(d)):
            continue
        try:
            saved += fetch_day(d)
        except Exception as exc:
            log.warning("NSE bhavcopy %s failed: %s", d, exc)
    return saved


def load():
    """All saved days -> one DataFrame with a `date` column (datetime.date)."""
    if not os.path.isdir(folder()):
        return None
    frames = []
    for f in sorted(os.listdir(folder())):
        if f.endswith(".csv.gz"):
            df = pd.read_csv(os.path.join(folder(), f), keep_default_na=False)
            df["date"] = date.fromisoformat(f[:10])
            frames.append(df)
    return pd.concat(frames, ignore_index=True) if frames else None
