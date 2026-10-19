"""Options lab: re-learns every night which option setups actually make money.

For NIFTY (weekly expiry) and BANKNIFTY (monthly expiry) it replays, on ~2
years of hourly data, the exact structures the plan recommends:

  condor       iron condor, enter 10:15 IST, short strikes ±0.9× expected move,
               wings 0.5× EM, exit at 45% of credit kept or at a loss of 1× credit,
               else hold to expiry. bull_put / bear_call: one side of it.
               Scored on days the bias model called SIDEWAYS when there are
               enough of them, else on all days.
  bull_spread  ATM bull call spread on days the model called BULLISH
  bear_spread  ATM bear put spread on days the model called BEARISH
               (spreads: target 60% of max profit, stop at half the debit,
               otherwise exit at 15:15 on day 2 or at expiry if sooner)

Bias labels come from the macro model's walk-forward (out-of-sample) calls, so
the lab never sees a call the model could not have made at the time.
Results are grouped by days to expiry, after costs and slippage, so the plan
can say "this setup won X% of N times, avg ₹Y per lot" and SKIP setups that
lost. Premiums are Black-Scholes with India VIX (BankNifty scaled by its
realised volatility), so these are estimates, not broker fills.
"""
import logging
from datetime import date, datetime, timedelta

import numpy as np
import pandas as pd
import yfinance as yf

import data
import macro
import store
from config import GLOBAL_TICKERS, INDEX_SPECS, IST
from options import bs_price

log = logging.getLogger(__name__)

# ₹ per lot per round trip: brokerage + charges + slippage (bid-ask) on every leg
COSTS = {("NIFTY", "condor"): 400, ("NIFTY", "spread"): 240,
         ("BANKNIFTY", "condor"): 500, ("BANKNIFTY", "spread"): 290}
RULES = {"condor": {"min_trades": 25, "min_win": 70.0}, "spread": {"min_trades": 20, "min_win": 0.0}}
MIN_EDGE = 100   # ₹ per lot: a setup must average at least this to be a TRADE; 0..MIN_EDGE = CAUTION
HEAVYWEIGHTS = {"HDFCBANK": "HDFC Bank", "RELIANCE": "Reliance", "ICICIBANK": "ICICI Bank", "INFY": "Infosys",
                "BHARTIARTL": "Bharti Airtel", "LT": "L&T", "ITC": "ITC", "TCS": "TCS", "AXISBANK": "Axis Bank",
                "SBIN": "SBI", "KOTAKBANK": "Kotak Bank"}
BANK_HEAVY = {"HDFCBANK", "ICICIBANK", "AXISBANK", "SBIN", "KOTAKBANK"}


# ------------------------------------------------------------------ expiry calendars
def _last_weekday(y, m, wd):
    x = date(y + (m == 12), m % 12 + 1, 1) - timedelta(days=1)
    while x.weekday() != wd:
        x -= timedelta(days=1)
    return x


def expiry_for(index, d):
    """Expiry of the contract the plan trades on session d (holiday shifts ignored).

    NIFTY weekly: Thursday until Aug-2025, Tuesday from Sep-2025.
    BANKNIFTY monthly: last Thursday (to Feb-2024), last Wednesday (Mar-Dec 2024),
    last Thursday (Jan-Aug 2025), last Tuesday (from Sep-2025).
    """
    if index == "NIFTY":
        wd = 1 if d >= date(2025, 9, 1) else 3
        return d + timedelta(days=(wd - d.weekday()) % 7)
    def wd_for(x):
        if x >= date(2025, 9, 1):
            return 1
        if date(2024, 3, 1) <= x <= date(2024, 12, 31):
            return 2
        return 3
    e = _last_weekday(d.year, d.month, wd_for(d))
    if e < d:
        y, m = (d.year + 1, 1) if d.month == 12 else (d.year, d.month + 1)
        e = _last_weekday(y, m, wd_for(date(y, m, 1)))
    return e


def bucket(index, dte):
    if index == "NIFTY":
        return str(dte)
    for lo, hi, name in ((0, 0, "0"), (1, 2, "1-2"), (3, 6, "3-6"), (7, 13, "7-13"), (14, 99, "14+")):
        if lo <= dte <= hi:
            return name


BUCKET_LABELS = {
    "NIFTY": {"0": "Expiry day", "1": "1 day before", "2": "2 days before", "3": "3 days before",
              "4": "4 days before", "5": "5 days before", "6": "6 days before"},
    "BANKNIFTY": {"0": "Expiry day", "1-2": "1-2 days before", "3-6": "3-6 days before",
                  "7-13": "1-2 weeks before", "14+": "2+ weeks before"},
}


# ------------------------------------------------------------------ data
def _hourly(ticker):
    df = yf.download(ticker, period="730d", interval="1h", auto_adjust=False, progress=False)
    if df is None or df.empty:
        return None
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(0)
    idx = pd.to_datetime(df.index)
    idx = (idx.tz_localize("UTC") if idx.tz is None else idx).tz_convert(IST).tz_localize(None)
    df.index = idx
    return df.dropna(subset=["Close"])


def _value(legs, S, T, iv):
    return sum((1 if a == "B" else -1) * bs_price(S, k, T, iv, t) for a, k, t in legs)


def _bias_labels(hist, params):
    """Out-of-sample bias label per session date."""
    wf = macro.walk_forward(macro.build_dataset(hist), test=560)
    return {ts.date(): macro.label_for(z, v, params) for ts, z, v in zip(wf.index, wf["z"], wf["vix"])}


# ------------------------------------------------------------------ simulation
def simulate(index, ih, vix_h, vix_d, iv_scale, labels):
    spec = INDEX_SPECS[index]
    step, lot = spec["step"], spec["lot"]
    trades = []
    for d in sorted(set(ih.index.date)):
        day = ih[ih.index.date == d]
        prev = ih[ih.index.date < d]
        vv = vix_d[vix_d.index.date < d]
        if len(day) < 5 or not len(prev) or not len(vv):
            continue
        pc = float(prev["Close"].iloc[-1])
        scale = float(iv_scale.asof(pd.Timestamp(d))) if len(iv_scale) else 1.0
        iv0 = float(vv.iloc[-1]) / 100 * (scale if np.isfinite(scale) else 1.0)
        exp = expiry_for(index, d)
        dte = (exp - d).days
        exp_close = datetime.combine(exp, datetime.min.time()) + timedelta(hours=15, minutes=30)
        em = pc * iv0 * np.sqrt((dte + 0.75) / 365)
        width = max(2 * step, round(0.5 * em / step) * step)
        T = lambda ts: max((exp_close - ts).total_seconds() / 86400, 0.002) / 365
        k_scale = iv0 / (float(vv.iloc[-1]) / 100)

        def iv(ts):
            s = vix_h["Close"][vix_h.index <= ts] if vix_h is not None else []
            return float(s.iloc[-1]) / 100 * k_scale if len(s) else iv0

        o, S0, t_entry = float(day["Open"].iloc[0]), float(day["Close"].iloc[0]), day.index[0]
        t0 = t_entry + timedelta(hours=1)
        label = labels.get(d)
        path_all = ih[(ih.index > t_entry) & (ih.index < exp_close)]
        if not len(path_all) or exp_close > path_all.index[-1] + timedelta(hours=2):
            continue  # expiry not reached in the data yet
        base = {"date": d.isoformat(), "dte": dte, "bucket": bucket(index, dte), "label": label,
                "first_hr": round((S0 / o - 1) * 100, 3)}

        # --- condor
        sc, sp = round((pc + 0.9 * em) / step) * step, round((pc - 0.9 * em) / step) * step
        if sp < S0 < sc:
            legs = [("S", sc, "CE"), ("B", sc + width, "CE"), ("S", sp, "PE"), ("B", sp - width, "PE")]
            v0 = _value(legs, S0, T(t0), iv(t_entry))
            credit, pnl = -v0, None
            for ts, b in path_all.iterrows():
                v = _value(legs, float(b["Close"]), T(ts + timedelta(hours=1)), iv(ts))
                if -v <= 0.45 * credit or (v - v0) <= -credit:   # target, or stop at loss = 1x credit
                    pnl = v - v0
                    break
            if pnl is None:
                pnl = _value(legs, float(path_all["Close"].iloc[-1]), 0, iv0) - v0
            trades.append({**base, "strategy": "condor", "pnl": round(pnl * lot - COSTS[(index, "condor")])})

        # --- credit spread on the side away from the bias (half a condor), held like the condor
        if label in ("BULLISH", "BEARISH"):
            bull = label == "BULLISH"
            legs = [("S", sp, "PE"), ("B", sp - width, "PE")] if bull else [("S", sc, "CE"), ("B", sc + width, "CE")]
            if (bull and S0 > sp) or (not bull and S0 < sc):
                v0 = _value(legs, S0, T(t0), iv(t_entry))
                credit, pnl = -v0, None
                for ts, b in path_all.iterrows():
                    v = _value(legs, float(b["Close"]), T(ts + timedelta(hours=1)), iv(ts))
                    if -v <= 0.45 * credit or (v - v0) <= -credit:
                        pnl = v - v0
                        break
                if pnl is None:
                    pnl = _value(legs, float(path_all["Close"].iloc[-1]), 0, iv0) - v0
                trades.append({**base, "strategy": "bull_put" if bull else "bear_call",
                               "pnl": round(pnl * lot - COSTS[(index, "spread")])})

        # --- directional debit spreads, only on the days the model made that call
        if label in ("BULLISH", "BEARISH"):
            bull = label == "BULLISH"
            k1 = round(S0 / step) * step
            kind = "CE" if bull else "PE"
            k2 = k1 + (width if bull else -width)
            legs = [("B", k1, kind), ("S", k2, kind)]
            v0 = _value(legs, S0, T(t0), iv(t_entry))
            if v0 > 0:
                tgt = v0 + 0.6 * (width - v0)
                day2_end = None
                later_days = sorted(set(path_all.index.date))
                if len(later_days) >= 2:
                    day2_end = datetime.combine(later_days[1], datetime.min.time()) + timedelta(hours=15, minutes=15)
                pnl = None
                for ts, b in path_all.iterrows():
                    v = _value(legs, float(b["Close"]), T(ts + timedelta(hours=1)), iv(ts))
                    if v >= tgt or v <= 0.5 * v0 or (day2_end and ts >= day2_end):
                        pnl = v - v0
                        break
                if pnl is None:
                    pnl = _value(legs, float(path_all["Close"].iloc[-1]), 0, iv0) - v0
                trades.append({**base, "strategy": "bull_spread" if bull else "bear_spread",
                               "pnl": round(pnl * lot - COSTS[(index, "spread")])})
    return pd.DataFrame(trades)


def _group(R):
    if R is None or not len(R):
        return None
    return {"trades": int(len(R)), "win_pct": round(100 * float((R.pnl > 0).mean()), 1),
            "avg_pnl": int(R.pnl.mean()), "worst": int(R.pnl.min()), "best": int(R.pnl.max()),
            "total": int(R.pnl.sum())}


def _summarise(index, R):
    out = {}
    regime_start = pd.Timestamp("2025-09-01")
    for strat in ("condor", "bull_spread", "bear_spread", "bull_put", "bear_call"):
        S = R[R.strategy == strat] if len(R) else R
        if not len(S):
            continue
        kind = "condor" if strat == "condor" else "spread"
        rule = RULES[kind]
        # condors: prefer the days the plan would actually trade them (SIDEWAYS calls)
        if strat == "condor":
            side = S[S.label == "RANGEBOUND"]
            S_use, basis = (side, "days the model called SIDEWAYS") if len(side) >= 4 * rule["min_trades"] else (S, "all days")
        else:
            S_use, basis = S, f"days the model called {'BULLISH' if strat in ('bull_spread', 'bull_put') else 'BEARISH'}"
        by = {}
        for b in BUCKET_LABELS[index]:
            all_b = S_use[S_use.bucket == b]
            reg_b = all_b[pd.to_datetime(all_b.date) >= regime_start]
            use = reg_b if len(reg_b) >= rule["min_trades"] else all_b
            g = _group(use)
            if g is None:
                continue
            g["sample"] = "current expiry schedule" if use is reg_b else "full 2 years"
            g["allowed"] = bool(g["trades"] >= rule["min_trades"] and g["win_pct"] >= rule["min_win"] and g["avg_pnl"] >= MIN_EDGE)
            g["marginal"] = bool(not g["allowed"] and g["trades"] >= rule["min_trades"] and 0 < g["avg_pnl"] < MIN_EDGE)
            if strat == "condor":
                g["quiet_first_hour"] = _group(use[use.first_hr.abs() < 0.25])
                g["trending_first_hour"] = _group(use[use.first_hr.abs() >= 0.25])
            by[b] = g
        # spreads: too few calls per bucket, so also keep the all-days verdict
        out[strat] = {"basis": basis, "overall": _group(S_use), "by_bucket": by}
        ov = out[strat]["overall"]
        out[strat]["overall_allowed"] = bool(ov and ov["trades"] >= rule["min_trades"] and ov["win_pct"] >= rule["min_win"]
                                             and ov["avg_pnl"] >= MIN_EDGE)
    return out


def run():
    state = store.latest_state()
    hist = data.history(list(GLOBAL_TICKERS.values()), period="5y", ttl=3600)
    labels = _bias_labels(hist, state["params"])
    vh = _hourly("^INDIAVIX")
    vd = hist["^INDIAVIX"]["Close"]
    n_d, b_d = hist["^NSEI"]["Close"], hist["^NSEBANK"]["Close"]
    ratio = (b_d.pct_change().rolling(20).std() / n_d.pct_change().rolling(20).std()).clip(0.8, 1.8).dropna()
    lab = {"generated_at": store.now_ist().isoformat(), "costs_per_lot": {f"{k[0]} {k[1]}": v for k, v in COSTS.items()},
           "bucket_labels": BUCKET_LABELS}
    for index, scale in (("NIFTY", pd.Series(dtype=float)), ("BANKNIFTY", ratio)):
        ih = _hourly(INDEX_SPECS[index]["ticker"])
        if ih is None:
            continue
        R = simulate(index, ih, vh, vd, scale, labels)
        lab[index] = _summarise(index, R)
    store._write(store.state_path("options_lab.json"), lab, indent=1)
    return lab


def load():
    return store._read(store.state_path("options_lab.json"), None)


# ------------------------------------------------------------------ morning verdict
STRATEGY_KEY = {"Iron Condor": "condor", "Bull Call Spread": "bull_spread", "Bear Put Spread": "bear_spread",
                "Bull Put Spread": "bull_put", "Bear Call Spread": "bear_call"}


def setup_verdict(index, strategy_name, session, lab=None, events=None):
    """TRADE / SKIP / CAUTION for the plan's primary structure, with the evidence."""
    lab = lab or load()
    key = STRATEGY_KEY.get(strategy_name)
    d = date.fromisoformat(session)
    dte = (expiry_for(index, d) - d).days
    b = bucket(index, dte)
    when = BUCKET_LABELS[index].get(b, b)
    if key is None:
        return {"decision": "CAUTION", "dte": dte, "reason": f"{strategy_name} is not tested by the options lab.", "stats": None}
    node = (lab or {}).get(index, {}).get(key)
    if not node:
        return {"decision": "CAUTION", "dte": dte, "reason": "Options lab has no history for this yet.", "stats": None}
    g = node["by_bucket"].get(b)
    kind = "condor" if key == "condor" else "spread"
    if g is None or (kind == "spread" and g["trades"] < RULES["spread"]["min_trades"]):
        # spreads: few calls per bucket, fall back to all buckets for this call
        ov = node.get("overall")
        if ov and node.get("overall_allowed"):
            return {"decision": "TRADE", "dte": dte, "stats": {**ov, "sample": node["basis"]},
                    "reason": f"On {node['basis']}, this spread won {ov['win_pct']}% of {ov['trades']} trades, "
                              f"avg ₹{ov['avg_pnl']:,} per lot after costs (worst ₹{ov['worst']:,})."}
        if ov:
            return {"decision": "SKIP", "dte": dte, "stats": {**ov, "sample": node["basis"]},
                    "reason": f"On {node['basis']}, this spread lost on average: {ov['win_pct']}% wins, "
                              f"avg ₹{ov['avg_pnl']:,} per lot over {ov['trades']} trades."}
        return {"decision": "CAUTION", "dte": dte, "reason": "Too little history.", "stats": None}
    g = {**g, "sample": f"{node['basis']}, {g['sample']}"}
    heavy = BANK_HEAVY if index == "BANKNIFTY" else set(HEAVYWEIGHTS)
    recent = [e for e in (events or []) if e.get("affects") == session and e["symbol"] in heavy]
    if kind == "condor" and dte <= 1 and recent:
        names = ", ".join(e["name"] for e in recent)
        return {"decision": "SKIP", "dte": dte, "stats": g,
                "reason": f"{when}, right after {names} results: condors broke 60% of the time in this combination (small sample)."}
    if g.get("marginal"):
        return {"decision": "CAUTION", "dte": dte, "stats": g,
                "reason": f"{when}: barely positive historically ({g['win_pct']}% wins, avg ₹{g['avg_pnl']:,} per lot over "
                          f"{g['trades']} trades). Too thin an edge after model error; half size or skip."}
    if not g["allowed"]:
        return {"decision": "SKIP", "dte": dte, "stats": g,
                "reason": f"{when}: this setup lost money historically ({g['win_pct']}% wins, avg ₹{g['avg_pnl']:,} "
                          f"per lot over {g['trades']} trades)."}
    tip = ""
    q, t = g.get("quiet_first_hour"), g.get("trending_first_hour")
    if q and t and q["win_pct"] - t["win_pct"] >= 5 and t["avg_pnl"] < q["avg_pnl"]:
        tip = (f" If the first hour moves more than 0.25%, the win rate drops to {t['win_pct']}% "
               f"(from {q['win_pct']}% on quiet mornings): consider skipping.")
    return {"decision": "TRADE", "dte": dte, "stats": g,
            "reason": f"{when}: this setup won {g['win_pct']}% of {g['trades']} trades, avg ₹{g['avg_pnl']:,} per lot "
                      f"after costs (worst ₹{g['worst']:,})." + tip}


# ------------------------------------------------------------------ events
def events_calendar(days_back=4, days_ahead=8):
    """Results dates of Nifty heavyweights around now, and which session each affects."""
    now = datetime.now(IST)
    out = []
    for sym, name in HEAVYWEIGHTS.items():
        try:
            df = yf.Ticker(sym + ".NS").get_earnings_dates(limit=8)
        except Exception:
            continue
        if df is None or df.empty:
            continue
        for t in pd.to_datetime(df.index):
            t = t.tz_convert(IST) if t.tz is not None else t.tz_localize(IST)
            if not (now - timedelta(days=days_back) <= t <= now + timedelta(days=days_ahead)):
                continue
            after = t.hour >= 15 or t.weekday() >= 5 or t.hour == 0
            aff = t.date() + timedelta(days=1) if after else t.date()
            while aff.weekday() >= 5:
                aff += timedelta(days=1)
            out.append({"symbol": sym, "name": name, "when": t.strftime("%a %d %b"), "date": t.date().isoformat(),
                        "affects": aff.isoformat(), "past": t < now, "bank": sym in BANK_HEAVY})
    return sorted(out, key=lambda e: e["date"])
