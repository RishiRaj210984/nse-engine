"""Intraday live snapshot -> docs/live.json (run every ~10 min in market hours).

  - Nifty / BankNifty spot and India VIX (Yahoo, 1-minute bars)
  - NSE option chain for the plan's expiry: real premiums, open interest,
    where call writers (resistance) and put writers (support) are sitting,
    put-call ratio, max pain, and the ATM straddle (market's implied move)
  - status of today's plan: each option leg's live premium and P&L per lot vs
    its target/stop, whether stock ideas have triggered, hit SL or target

If NSE blocks the request (it often blocks cloud servers), premiums fall back
to Black-Scholes estimates from live spot + VIX and the file says so.
"""
import json
import logging
import math
import os
from datetime import datetime

import requests

import store
from config import INDEX_SPECS, IST, PUBLIC_DIR
from options import bs_price

log = logging.getLogger(__name__)
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126 Safari/537.36"
NSE = "https://www.nseindia.com"


# ------------------------------------------------------------------ sources
def yahoo_quotes(symbols):
    out = {}
    for sym in symbols:
        try:
            r = requests.get(f"https://query1.finance.yahoo.com/v8/finance/chart/{requests.utils.quote(sym)}",
                             params={"interval": "1m", "range": "1d"}, headers={"User-Agent": UA}, timeout=10)
            res = r.json()["chart"]["result"][0]
            meta = res["meta"]
            q = res["indicators"]["quote"][0]
            closes = [c for c in q.get("close") or [] if c is not None]
            highs = [h for h in q.get("high") or [] if h is not None]
            lows = [x for x in q.get("low") or [] if x is not None]
            last = float(meta.get("regularMarketPrice") or (closes[-1] if closes else 0))
            prev = float(meta.get("chartPreviousClose") or meta.get("previousClose") or last)
            t = meta.get("regularMarketTime")
            out[sym] = {"last": round(last, 2), "prev_close": round(prev, 2), "chg_pct": round((last / prev - 1) * 100, 2) if prev else 0,
                        "high": round(max(highs), 2) if highs else None, "low": round(min(lows), 2) if lows else None,
                        "as_of": datetime.fromtimestamp(t, IST).strftime("%H:%M") if t else None}
        except Exception as exc:
            log.info("yahoo %s failed: %s", sym, exc)
    return out


class NSEClient:
    def __init__(self):
        self.s = requests.Session()
        self.s.headers.update({"User-Agent": UA, "Accept-Language": "en-US,en;q=0.9", "Accept": "application/json, text/plain, */*"})
        self.ok = False

    def warm(self):
        r = self.s.get(f"{NSE}/option-chain", timeout=10)
        self.ok = r.status_code == 200
        return self.ok

    def get(self, path, **params):
        r = self.s.get(f"{NSE}/api/{path}", params=params, timeout=12, headers={"Referer": f"{NSE}/option-chain"})
        r.raise_for_status()
        return r.json()

    def expiries(self, symbol):
        return self.get("option-chain-contract-info", symbol=symbol)["expiryDates"]

    def chain(self, symbol, expiry):
        """expiry as DD-Mon-YYYY (NSE format)."""
        return self.get("option-chain-v3", type="Indices", symbol=symbol, expiry=expiry)["records"]


def nse_date(iso):
    return datetime.fromisoformat(iso).strftime("%d-%b-%Y")


# ------------------------------------------------------------------ analysis
def summarise_chain(rec, step, window=12):
    spot = float(rec.get("underlyingValue") or 0)
    rows = []
    for r in rec.get("data", []):
        k = float(r.get("strikePrice") or 0)
        ce, pe = r.get("CE") or {}, r.get("PE") or {}
        rows.append({"strike": int(k),
                     "ce_ltp": ce.get("lastPrice"), "ce_oi": ce.get("openInterest", 0) or 0, "ce_chg_oi": ce.get("changeinOpenInterest", 0) or 0,
                     "ce_iv": ce.get("impliedVolatility"), "ce_bid": ce.get("buyPrice1"), "ce_ask": ce.get("sellPrice1"),
                     "pe_ltp": pe.get("lastPrice"), "pe_oi": pe.get("openInterest", 0) or 0, "pe_chg_oi": pe.get("changeinOpenInterest", 0) or 0,
                     "pe_iv": pe.get("impliedVolatility"), "pe_bid": pe.get("buyPrice1"), "pe_ask": pe.get("sellPrice1")})
    rows.sort(key=lambda x: x["strike"])
    if not rows or not spot:
        return None
    atm = min(rows, key=lambda x: abs(x["strike"] - spot))["strike"]
    tot_ce, tot_pe = sum(x["ce_oi"] for x in rows), sum(x["pe_oi"] for x in rows)
    above = [x for x in rows if x["strike"] >= spot]
    below = [x for x in rows if x["strike"] <= spot]
    call_walls = sorted(above, key=lambda x: -x["ce_oi"])[:3]
    put_walls = sorted(below, key=lambda x: -x["pe_oi"])[:3]
    # max pain: strike where option writers pay out the least at expiry
    def pain(k):
        return sum(max(0, k - x["strike"]) * x["ce_oi"] + max(0, x["strike"] - k) * x["pe_oi"] for x in rows)
    near = [x for x in rows if abs(x["strike"] - spot) <= 30 * step]
    max_pain = min(near or rows, key=lambda x: pain(x["strike"]))["strike"]
    a = next(x for x in rows if x["strike"] == atm)
    straddle = (a["ce_ltp"] or 0) + (a["pe_ltp"] or 0)
    adds_ce = sorted(rows, key=lambda x: -x["ce_chg_oi"])[:2]
    adds_pe = sorted(rows, key=lambda x: -x["pe_chg_oi"])[:2]
    view = []
    if call_walls and put_walls:
        view.append(f"Call writers are heaviest at {call_walls[0]['strike']:,} (resistance) and put writers at "
                    f"{put_walls[0]['strike']:,} (support).")
    pcr = tot_pe / tot_ce if tot_ce else None
    if pcr is not None:
        view.append(f"Put-call ratio {pcr:.2f}: " + ("puts dominate, writers lean bullish." if pcr > 1.2 else
                                                   "calls dominate, writers lean bearish." if pcr < 0.8 else "balanced."))
    view.append(f"ATM straddle ≈ ₹{straddle:.0f}: the market is pricing about ±{straddle:.0f} points to expiry.")
    return {"spot": round(spot, 2), "atm": atm, "pcr_oi": round(pcr, 2) if pcr else None, "max_pain": max_pain,
            "straddle": round(straddle, 1), "implied_move_pct": round(100 * straddle / spot, 2) if spot else None,
            "call_walls": [{"strike": x["strike"], "oi": x["ce_oi"], "chg_oi": x["ce_chg_oi"]} for x in call_walls],
            "put_walls": [{"strike": x["strike"], "oi": x["pe_oi"], "chg_oi": x["pe_chg_oi"]} for x in put_walls],
            "fresh_call_writing": [{"strike": x["strike"], "chg_oi": x["ce_chg_oi"]} for x in adds_ce if x["ce_chg_oi"] > 0],
            "fresh_put_writing": [{"strike": x["strike"], "chg_oi": x["pe_chg_oi"]} for x in adds_pe if x["pe_chg_oi"] > 0],
            "view": view,
            "note": "Context only: in 200 days of NSE data, OI walls, PCR and max pain did not predict moves better than chance.",
            "strikes": [x for x in rows if abs(x["strike"] - atm) <= window * step]}


def leg_price(leg, chain, spot, T, iv):
    if chain:
        for x in chain["strikes"]:
            if x["strike"] == leg["strike"]:
                p = x["ce_ltp"] if leg["type"] == "CE" else x["pe_ltp"]
                if p:
                    return float(p), "NSE"
    return bs_price(spot, leg["strike"], T, iv, leg["type"]), "estimate"


def plan_status(bias, chains, quotes, now):
    out = []
    for name, o in (bias.get("options") or {}).items():
        s = o["strategies"][0]
        verdict = (s.get("verdict") or {}).get("decision", "TRADE")
        spec = INDEX_SPECS[name]
        q = quotes.get(spec["ticker"]) or {}
        spot = q.get("last") or o["spot"]
        vix = (quotes.get("^INDIAVIX") or {}).get("last") or o["iv_used_pct"]
        iv = (o["iv_used_pct"] / 100) * (vix / max(o["iv_used_pct"], 1e-9)) if name == "NIFTY" else o["iv_used_pct"] / 100
        expiry_close = datetime.fromisoformat(o["expiry"]).replace(hour=15, minute=30, tzinfo=IST)
        T = max((expiry_close - now).total_seconds() / 86400, 0.002) / 365
        legs, val_now, val_entry, src = [], 0.0, 0.0, set()
        for leg in s["legs"]:
            p, where = leg_price(leg, chains.get(name), spot, T, iv)
            sign = 1 if leg["action"] == "BUY" else -1
            val_now += sign * p
            val_entry += sign * leg["est_premium"]
            src.add(where)
            legs.append({**leg, "live": round(p, 2), "source": where})
        lot = o["lot_size"]
        pnl = round((val_now - val_entry) * lot)
        credit = -val_entry
        status, hint = "WATCH", ""
        session = bias.get("target_session")
        entry_hm = "10:15" if credit > 0 else "09:45"
        if session != now.date().isoformat() or now.strftime("%H:%M") < entry_hm:
            when = "the session day" if session != now.date().isoformat() else entry_hm + " IST"
            out.append({"index": name, "strategy": s["name"], "decision": verdict, "spot": spot, "legs": legs,
                        "entry_value": round(val_entry, 2), "value_now": round(val_now, 2), "pnl_per_lot": None,
                        "status": "NOT ENTERED", "price_source": "NSE" if src == {"NSE"} else "mixed",
                        "hint": f"Entry is at {when}. Live premiums now: net {'credit' if val_now < 0 else 'debit'} "
                                f"₹{abs(val_now):.1f} vs plan estimate ₹{abs(val_entry):.1f}."})
            continue
        if credit > 0:   # credit structure: target = keep 55% of credit, stop = loss of 1x credit
            if -val_now <= 0.45 * credit:
                status, hint = "TARGET", "55% of the credit is in hand: book profit."
            elif (val_now - val_entry) <= -credit:
                status, hint = "STOP", "Loss has reached 1× credit: exit."
            shorts = sorted(l["strike"] for l in s["legs"] if l["action"] == "SELL")
            if shorts and (spot <= shorts[0] or spot >= shorts[-1]) and len(shorts) > 1:
                hint += f" {name} is at a short strike ({spot:,.0f}): be ready to exit."
        else:            # debit structure
            if val_now >= val_entry * 1.6:
                status, hint = "TARGET", "Up 60% on the debit: book at least half."
            elif val_now <= val_entry * 0.5:
                status, hint = "STOP", "Half the debit is gone: exit."
        out.append({"index": name, "strategy": s["name"], "decision": verdict, "spot": spot, "legs": legs,
                    "entry_value": round(val_entry, 2), "value_now": round(val_now, 2), "pnl_per_lot": pnl,
                    "status": status, "hint": hint.strip(), "price_source": "NSE" if src == {"NSE"} else
                    ("estimate" if src == {"estimate"} else "mixed")})
    return out


def stock_status(scan, quotes, now, session=None):
    out = []
    today = session == now.date().isoformat()
    for c in (scan.get("candidates", []) + scan.get("near_misses", []))[:12]:
        q = quotes.get(c["symbol"] + ".NS")
        if not q:
            continue
        last = q["last"]
        st = "WAITING"
        if not today:
            st = "NOT TODAY"
        elif q.get("low") is not None and q["low"] <= c["stop"]:
            st = "SL ZONE"
        elif q.get("high") is not None and q["high"] >= c["t1"]:
            st = "T1 HIT"
        elif last >= c["entry"] and now.strftime("%H:%M") >= "09:30":
            st = "TRIGGERED"
        out.append({"symbol": c["symbol"], "grade": c["grade"], "last": last, "chg_pct": q["chg_pct"],
                    "entry": c["entry"], "stop": c["stop"], "t1": c["t1"], "t2": c["t2"], "status": st,
                    "high": q.get("high"), "low": q.get("low")})
    return out


def run(force=False):
    now = datetime.now(IST)
    from nse_calendar import is_trading_day
    if not force and not os.getenv("LIVE_FORCE") and (not is_trading_day(now.date()) or not ("09:00" <= now.strftime("%H:%M") <= "15:45")):
        return {"skipped": "outside market hours"}
    bias = store._read(os.path.join(PUBLIC_DIR, "global-bias.json"), {})
    scan = store._read(os.path.join(PUBLIC_DIR, "volume-breakouts.json"), {})
    syms = ["^NSEI", "^NSEBANK", "^INDIAVIX"] + [c["symbol"] + ".NS" for c in (scan.get("candidates", []) + scan.get("near_misses", []))[:12]]
    quotes = yahoo_quotes(syms)
    chains, chain_src, err = {}, "NSE", None
    nse = NSEClient()
    try:
        nse.warm()
        for name in ("NIFTY", "BANKNIFTY"):
            o = (bias.get("options") or {}).get(name)
            exps = nse.expiries(name)
            exp = nse_date(o["expiry"]) if o and nse_date(o["expiry"]) in exps else exps[0]
            rec = nse.chain(name, exp)
            sm = summarise_chain(rec, INDEX_SPECS[name]["step"])
            if sm:
                sm["expiry"] = exp
                chains[name] = sm
    except Exception as exc:
        chain_src, err = "unavailable", str(exc)[:160]
        log.warning("NSE option chain unavailable: %s", exc)
    out = {
        "updated": now.isoformat(), "session": bias.get("target_session"),
        "sources": {"quotes": "Yahoo (1-min, may lag 1–15 min)", "option_chain": chain_src, "error": err},
        "indices": {k: quotes.get(t) for k, t in (("NIFTY", "^NSEI"), ("BANKNIFTY", "^NSEBANK"), ("INDIAVIX", "^INDIAVIX"))},
        "chains": chains,
        "plan": plan_status(bias, chains, quotes, now) if bias else [],
        "stocks": stock_status(scan, quotes, now, bias.get("target_session")),
    }
    store.publish("live.json", out)
    return {"chain": chain_src, "plan": [(p["index"], p["status"], p["pnl_per_lot"]) for p in out["plan"]],
            "nifty": (out["indices"]["NIFTY"] or {}).get("last")}
