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

import momentum
import store
from config import BOOK_COSTS, INDEX_SPECS, IST, PUBLIC_DIR
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
            # the day's open and the price at 10:15 (close of the 10:14 one-minute bar), for the first-hour move
            stamps = [datetime.fromtimestamp(x, IST).strftime("%H:%M") for x in res.get("timestamp") or []]
            opens = [o for o in q.get("open") or [] if o is not None]
            upto = [c for hm, c in zip(stamps, q.get("close") or []) if c is not None and hm <= "10:14"]
            out[sym] = {"last": round(last, 2), "prev_close": round(prev, 2), "chg_pct": round((last / prev - 1) * 100, 2) if prev else 0,
                        "high": round(max(highs), 2) if highs else None, "low": round(min(lows), 2) if lows else None,
                        "as_of": datetime.fromtimestamp(t, IST).strftime("%H:%M") if t else None,
                        "open": round(opens[0], 2) if opens else None,
                        "at_1015": round(upto[-1], 2) if upto and any(hm >= "10:14" for hm in stamps) else None}
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
def summarise_chain(rec, step, window=12, keep=()):
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
            "strikes": [x for x in rows if abs(x["strike"] - atm) <= window * step or x["strike"] in keep]}


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


# ------------------------------------------------------------------ real prices for the paper book
def legs_key(index, expiry_iso, legs):
    return f"{index}|{expiry_iso}|" + ",".join(sorted(f"{l['action'][0]}{int(l['strike'])}{l['type']}" for l in legs))


def real_sides(legs, chain, spot, T, vix):
    """Real NSE value per unit of each side (calls, puts): bid/ask mid, else last price; a strike outside the
    saved window is priced with the real implied volatility of the nearest saved strike. -> (v_ce, v_pe, nse_legs)"""
    by = {x["strike"]: x for x in chain["strikes"]}
    ks = sorted(by)
    side, n = {"CE": 0.0, "PE": 0.0}, 0
    for l in legs:
        key = "ce" if l["type"] == "CE" else "pe"
        x = by.get(l["strike"])
        if x and x.get(f"{key}_ltp"):
            b, a = x.get(f"{key}_bid"), x.get(f"{key}_ask")
            p = (b + a) / 2 if b and a and a >= b > 0 else float(x[f"{key}_ltp"])
            n += 1
        else:
            near = by[min(ks, key=lambda k: abs(k - l["strike"]))] if ks else {}
            p = bs_price(spot, l["strike"], T, (near.get(f"{key}_iv") or vix) / 100, l["type"])
        side[l["type"]] += (1 if l["action"] == "BUY" else -1) * p
    return round(side["CE"], 3), round(side["PE"], 3), n


def _chain_for(chains, index, expiry_iso):
    ch = (chains or {}).get(index)
    if ch and ch.get("strikes") and datetime.strptime(ch["expiry"], "%d-%b-%Y").date().isoformat() == expiry_iso:
        return ch
    return None


def record_marks(bias, chains, quotes, now):
    """Save, every live run, the real NSE value of each held paper trade and of today's planned structures.
    The evening review uses these instead of estimates (state/paper_marks.json, last 15 days)."""
    if not ("09:15" <= now.strftime("%H:%M") <= "15:30"):
        return 0
    vix = (quotes.get("^INDIAVIX") or {}).get("last")
    book = store._read(store.state_path("paper_book.json"), {}) or {}
    todo = [(p["index"], p["expiry"], p["legs"]) for p in (book.get("open") or {}).values()]
    for name, o in (bias.get("options") or {}).items():
        todo.append((name, o["expiry"], o["strategies"][0]["legs"]))
    path = store.state_path("paper_marks.json")
    marks = store._read(path, {}) or {}
    day = marks.setdefault(now.date().isoformat(), {})
    n = 0
    for name, exp, legs in todo:
        ch, spot = _chain_for(chains, name, exp), (quotes.get(INDEX_SPECS[name]["ticker"]) or {}).get("last")
        if not ch or not spot or not vix:
            continue
        T = max((datetime.fromisoformat(exp).replace(hour=15, minute=30, tzinfo=IST) - now).total_seconds() / 86400, 0.002) / 365
        v_ce, v_pe, nse = real_sides(legs, ch, spot, T, vix)
        rows = day.setdefault(legs_key(name, exp, legs), [])
        if not rows or rows[-1][0] != now.strftime("%H:%M"):
            rows.append([now.strftime("%H:%M"), round(spot, 2), v_ce, v_pe, nse])
            n += 1
    for d in sorted(marks)[:-15]:
        marks.pop(d)
    store._write(path, marks)
    return n


def first_exit(rows, base, credit, expiry_iso, day_iso, stop_credit=None):
    """The evening review's exit rule over today's real NSE marks, in time order: the first mark at the stop
    (loss 1x credit), the target (55% of credit kept) or, on expiry day, from 10:15. Once reached the trade
    stays booked there even if prices come back, as the evening review books it.
    base(row) -> value per unit. -> (status, time, value per unit) or None"""
    for r in rows:
        v = base(r)
        if v <= -(credit if stop_credit is None else stop_credit):
            return "STOP", r[0], v
        if v >= 0.55 * credit:
            return "TARGET", r[0], v
        if day_iso >= expiry_iso and r[0] >= "10:15":
            return "EXPIRY 10:15", r[0], v
    return None


def paper_today(bias, now):
    """Today's new paper trade, before the evening review adds it to the book: the plan's TRADE structure,
    entered at its first real price between 10:15 and 10:35 if the index was between the short strikes,
    valued at the latest real price. Mirrors the evening review's entry rule."""
    book = store._read(store.state_path("paper_book.json"), {}) or {}
    day = (store._read(store.state_path("paper_marks.json"), {}) or {}).get(now.date().isoformat(), {})
    out = []
    for name, o in (bias.get("options") or {}).items():
        if (o.get("setup") or {}).get("decision") != "TRADE" or name in (book.get("open") or {}):
            continue
        legs = o["strategies"][0]["legs"]
        if not any(l["action"] == "SELL" for l in legs):
            continue
        rows = day.get(legs_key(name, o["expiry"], legs), [])
        entry = next((r for r in rows if "10:15" <= r[0] <= "10:35"), None)
        base = {"index": name, "strategy": o["strategies"][0]["name"], "expiry": o["expiry"],
                "legs": " + ".join(f"{l['action']} {l['strike']} {l['type']}" for l in legs)}
        if entry is None:
            out.append({**base, "status": "WAITING" if now.strftime("%H:%M") < "10:35" else "NO REAL PRICE AT 10:15"})
            continue
        shorts = sorted(l["strike"] for l in legs if l["action"] == "SELL")
        if len(shorts) >= 2 and not (shorts[0] < entry[1] < shorts[-1]):
            out.append({**base, "status": "NOT ENTERED", "note": f"{name} {entry[1]:,.0f} was outside {shorts[0]:,}-{shorts[-1]:,} at {entry[0]}"})
            continue
        lot, v_e, last = o["lot_size"], entry[2] + entry[3], rows[-1]
        credit, value = round(-v_e * lot), round(((last[2] + last[3]) - v_e) * lot)
        hit = first_exit([r for r in rows if r[0] > entry[0]], lambda r: (r[2] + r[3]) - v_e, -v_e, o["expiry"], now.date().isoformat())
        booked = {}
        if hit:
            value = round(hit[2] * lot)
            booked = {"reached_at": hit[1], "result_rs": value - BOOK_COSTS[name]}
        out.append({**base, "status": hit[0] if hit else "HOLD", "entered_at": entry[0], "credit_rs": credit, "value_now": value,
                    "target_rs": round(0.55 * credit), "stop_rs": -credit, "at": last[0], "spot": last[1], **booked})
    return out


def paper_open(quotes, now, book_file="paper_book.json", chains=None):
    """Value the trades the paper book holds, every live run, on the same basis the evening review uses (real NSE
    prices, else Black-Scholes + India VIX), and book a target or stop reached earlier today at that moment, so the
    daytime number and the evening number agree.
    book_file="paper_book_adjusted.json" values the comparison book (same trades with the adjustment)."""
    book = store._read(store.state_path(book_file), {}) or {}
    day = (store._read(store.state_path("paper_marks.json"), {}) or {}).get(now.date().isoformat(), {})
    out = []
    for name, p in (book.get("open") or {}).items():
        spot = (quotes.get(INDEX_SPECS[name]["ticker"]) or {}).get("last")
        if not spot:
            continue
        vix = (quotes.get("^INDIAVIX") or {}).get("last")
        iv = p["iv0"] * (vix / p["vix0"]) if vix and p.get("vix0") else p["iv0"]
        exp = datetime.fromisoformat(p["expiry"]).replace(hour=15, minute=30, tzinfo=IST)
        T = max((exp - now).total_seconds() / 86400, 0.002) / 365
        val = lambda legs: sum((1 if l["action"] == "BUY" else -1) * bs_price(spot, l["strike"], T, iv, l["type"]) for l in legs)
        real_basis = p.get("basis") == "NSE" and len(p.get("groups") or []) == 2
        # today's real NSE marks (every 10 min); the comparison book is valued on estimates, like the evening review
        rows = day.get(legs_key(name, p["expiry"], p["legs"]), []) if real_basis and book_file == "paper_book.json" else []
        unit = lambda v: p.get("realized", 0) + sum(v[g["legs"][0]["type"]] - g["v0"] for g in p["groups"])
        ch = _chain_for(chains, name, p["expiry"]) if real_basis else None
        at = now.strftime("%H:%M")
        if ch and vix and (at <= "15:30" or not rows):   # real NSE prices, same basis as the entry; after the close the 15:30 mark, as the evening review
            v_ce, v_pe, _ = real_sides(p["legs"], ch, spot, T, vix)
            value = round(unit({"CE": v_ce, "PE": v_pe}) * p["lot"])
        elif rows:       # no chain passed (Close all): the latest saved real NSE mark
            value, at = round(unit({"CE": rows[-1][2], "PE": rows[-1][3]}) * p["lot"]), rows[-1][0]
        elif p.get("groups"):   # realized part of an adjusted trade + what is still open
            value = round((p.get("realized", 0) + sum(val(g["legs"]) - g["v0"] for g in p["groups"])) * p["lot"])
        else:
            value = round((val(p["legs"]) - p["v_e"]) * p["lot"])
        credit = round(-p["v_e"] * p["lot"])
        hit = first_exit(rows, lambda r: unit({"CE": r[2], "PE": r[3]}), p.get("credit_all", -p["v_e"]), p["expiry"],
                         now.date().isoformat(), stop_credit=p.get("credit0", -p["v_e"]))
        if hit:
            status, value = hit[0], round(hit[2] * p["lot"])
            booked = {"reached_at": hit[1], "result_rs": value - BOOK_COSTS[name] - p.get("extra_cost", 0)}
        else:
            status = ("TARGET" if value >= 0.55 * credit else "STOP" if value <= -credit else
                      "EXPIRY 10:15" if now.date().isoformat() >= p["expiry"] and now.strftime("%H:%M") >= "10:15" else "HOLD")
            booked = {}
        out.append({"index": name, "strategy": p["strategy"], "expiry": p["expiry"], "entry_session": p["entry_session"],
                    "credit_rs": credit, "value_now": value,
                    "since_last_evening": booked.get("result_rs", value) - p.get("mark", 0),
                    "status": status, "spot": spot, "at": at, **booked})
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
    chains, chain_src, err, nearest = {}, "NSE", None, {}
    nse = NSEClient()
    try:
        nse.warm()
        for name in ("NIFTY", "BANKNIFTY"):
            o = (bias.get("options") or {}).get(name)
            exps = nse.expiries(name)
            nearest[name] = datetime.strptime(exps[0], "%d-%b-%Y").date().isoformat()
            exp = nse_date(o["expiry"]) if o and nse_date(o["expiry"]) in exps else exps[0]
            rec = nse.chain(name, exp)
            # always keep the strikes the plan and any held paper trade use, so their real prices are saved
            keep = {int(l["strike"]) for l in (o["strategies"][0]["legs"] if o else [])}
            keep |= {int(l["strike"]) for p in (store._read(store.state_path("paper_book.json"), {}) or {}).get("open", {}).values()
                     if p["index"] == name for l in p["legs"]}
            if name == "NIFTY":
                keep |= momentum.keep_strikes(now)   # today's paper momentum buy
            sm = summarise_chain(rec, INDEX_SPECS[name]["step"], keep=keep)
            if sm:
                sm["expiry"] = exp
                chains[name] = sm
    except Exception as exc:
        chain_src, err = "unavailable", str(exc)[:160]
        log.warning("NSE option chain unavailable: %s", exc)
    try:
        import global_table
        gt = global_table.fetch_and_build(bias.get("preopen") if (bias.get("preopen") or {}).get("markets") else None)
    except Exception as exc:
        gt = {"error": str(exc)[:200], "rows": []}
    out = {
        "updated": now.isoformat(), "session": bias.get("target_session"), "global_table": gt,
        "sources": {"quotes": "Yahoo (1-min, may lag 1–15 min)", "option_chain": chain_src, "error": err},
        "indices": {k: quotes.get(t) for k, t in (("NIFTY", "^NSEI"), ("BANKNIFTY", "^NSEBANK"), ("INDIAVIX", "^INDIAVIX"))},
        "chains": chains,
        "plan": plan_status(bias, chains, quotes, now) if bias else [],
        "stocks": stock_status(scan, quotes, now, bias.get("target_session")),
    }
    try:
        out["paper_marks"] = record_marks(bias, chains, quotes, now)
        out["paper_today"] = paper_today(bias, now)
        out["paper_open"] = paper_open(quotes, now, chains=chains)
        out["paper_open_adjusted"] = paper_open(quotes, now, "paper_book_adjusted.json")
    except Exception as exc:   # never let this block the live snapshot
        out["paper_open"] = []
        log.warning("paper_open failed: %s", exc)
    try:   # paper-only third trade: NIFTY first-hour momentum buy
        out["momentum"] = momentum.step(now, quotes.get("^NSEI"), chains.get("NIFTY"), nearest.get("NIFTY"))
    except Exception as exc:
        log.warning("momentum step failed: %s", exc)
    store.publish("live.json", out)
    try:
        import markets
        markets.build()
    except Exception as exc:
        log.warning("markets table failed: %s", exc)
    return {"chain": chain_src, "plan": [(p["index"], p["status"], p["pnl_per_lot"]) for p in out["plan"]],
            "nifty": (out["indices"]["NIFTY"] or {}).get("last")}
