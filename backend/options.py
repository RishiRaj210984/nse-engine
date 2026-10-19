"""Module A part 2: Nifty / BankNifty options structures for the day's bias.

yfinance has no NSE option chain, so premiums are THEORETICAL Black-Scholes
estimates using India VIX as implied volatility. Always confirm live premiums on
your broker's option chain before placing an order.
"""
from datetime import date, datetime, time, timedelta
from math import erf, exp, log, sqrt

from config import INDEX_SPECS, RISK_FREE_RATE
from indicators import atr, ema, pivots


def _N(x):
    return 0.5 * (1 + erf(x / sqrt(2)))


def bs_price(S, K, T, iv, kind, r=RISK_FREE_RATE):
    if T <= 0 or iv <= 0:
        return max(0.0, S - K) if kind == "CE" else max(0.0, K - S)
    d1 = (log(S / K) + (r + 0.5 * iv * iv) * T) / (iv * sqrt(T))
    d2 = d1 - iv * sqrt(T)
    call = S * _N(d1) - K * exp(-r * T) * _N(d2)
    return call if kind == "CE" else call - S + K * exp(-r * T)


def bs_delta(S, K, T, iv, kind, r=RISK_FREE_RATE):
    if T <= 0 or iv <= 0:
        return (1.0 if S > K else 0.0) if kind == "CE" else (-1.0 if S < K else 0.0)
    d1 = (log(S / K) + (r + 0.5 * iv * iv) * T) / (iv * sqrt(T))
    return _N(d1) if kind == "CE" else _N(d1) - 1


def next_expiry(spec, now):
    """Next expiry by rule, moved to the previous trading day when it falls on an NSE holiday."""
    from nse_calendar import shift_expiry
    d, wd = now.date(), spec["weekday"]

    def past(x):
        return x < d or (x == d and now.time() > time(15, 30))

    if spec["expiry"] == "weekly":
        e = d + timedelta(days=(wd - d.weekday()) % 7)
        while past(shift_expiry(e)):
            e += timedelta(days=7)
        return shift_expiry(e)

    def last_wd(y, m):
        x = date(y + (m == 12), m % 12 + 1, 1) - timedelta(days=1)
        while x.weekday() != wd:
            x -= timedelta(days=1)
        return x

    e = shift_expiry(last_wd(d.year, d.month))
    if past(e):
        y, m = (d.year + 1, 1) if d.month == 12 else (d.year, d.month + 1)
        e = shift_expiry(last_wd(y, m))
    return e


def _round(x, step):
    return int(round(x / step) * step)


def index_levels(df_prior):
    """Support / resistance from the last completed session."""
    last = df_prior.iloc[-1]
    pv = pivots(float(last["High"]), float(last["Low"]), float(last["Close"]))
    e20 = float(ema(df_prior["Close"], 20).iloc[-1])
    close = float(last["Close"])
    support = max(pv["S1"], e20) if e20 < close else pv["S1"]
    resistance = min(pv["R1"], e20) if e20 > close else pv["R1"]
    return {
        "prev_close": close, "ema20": e20, "support": support, "resistance": resistance,
        "pivots": pv, "atr": float(atr(df_prior).iloc[-1]),
    }


def build(name, label, df_prior, spot, iv, target_date, now):
    spec = INDEX_SPECS[name]
    step, lot = spec["step"], spec["lot"]
    ref_now = datetime.combine(target_date, time(9, 15)) if target_date > now.date() else now
    expiry = next_expiry(spec, ref_now)
    days = (expiry - ref_now.date()).days + 0.75
    T = days / 365
    em = spot * iv * sqrt(T)  # 1-sigma expected move to expiry
    width = max(2 * step, _round(0.5 * em, step))
    atm = _round(spot, step)
    lv = index_levels(df_prior)
    pv, sup, res = lv["pivots"], lv["support"], lv["resistance"]

    def leg(action, strike, kind):
        return {"action": action, "strike": strike, "type": kind,
                "est_premium": round(bs_price(spot, strike, T, iv, kind), 1)}

    def pack(title, legs, net, max_loss, max_reward, entry, target, stop, breakeven, notes=""):
        return {
            "name": title, "legs": legs, "net_premium": round(net, 1),
            "max_loss": round(max_loss), "max_reward": None if max_reward is None else round(max_reward),
            "reward_risk": None if not max_reward or max_loss <= 0 else round(max_reward / max_loss, 2),
            "entry": entry, "target": target, "stop_loss": stop,
            "breakeven": [round(b) for b in breakeven], "notes": notes,
        }

    strategies = []
    if label in ("BULLISH", "BEARISH"):
        bull = label == "BULLISH"
        kind = "CE" if bull else "PE"
        sign = 1 if bull else -1
        k1, k2 = atm, atm + sign * width
        l1, l2 = leg("BUY", k1, kind), leg("SELL", k2, kind)
        debit = l1["est_premium"] - l2["est_premium"]
        if bull:
            stop_lvl = pv["S1"] if sup > pv["S1"] else pv["S2"]
            tgt_spot = pv["R1"] if pv["R1"] > spot else pv["R2"]
            entry_txt = f"Enter on dips that hold above {sup:,.0f} (EMA20 / S1 support zone)"
            stop_txt = f"{name} closes below {stop_lvl:,.0f}"
        else:
            stop_lvl = pv["R1"] if res < pv["R1"] else pv["R2"]
            tgt_spot = pv["S1"] if pv["S1"] < spot else pv["S2"]
            entry_txt = f"Enter on pullbacks that fail below {res:,.0f} (EMA20 / R1 resistance zone)"
            stop_txt = f"{name} closes above {stop_lvl:,.0f}"
        strategies.append(pack(
            "Bull Call Spread" if bull else "Bear Put Spread", [l1, l2], -debit,
            debit * lot, (width - debit) * lot, entry_txt,
            f"Exit spread near ₹{debit + 0.6 * (width - debit):.1f} or {name} at {k2:,}",
            f"Spread falls to ₹{0.5 * debit:.1f}, or {stop_txt}",
            [k1 + sign * debit], "Defined risk. Preferred structure."))

        p = l1["est_premium"]
        dlt = abs(bs_delta(spot, k1, T, iv, kind))
        tgt_prem = p + dlt * abs(tgt_spot - spot)
        sl_prem = p * 0.7
        strategies.append(pack(
            f"ATM {'Call' if bull else 'Put'} Buy", [leg("BUY", k1, kind)], -p,
            p * lot, (tgt_prem - p) * lot, entry_txt,
            f"Premium ≈ ₹{tgt_prem:.1f} ({name} near {tgt_spot:,.0f})",
            f"Premium ₹{sl_prem:.1f} (-30%), or {stop_txt}",
            [k1 + sign * p], "Max loss = full premium. Theta decay hurts if move is late."))

        # credit spread on the side away from the bias (same geometry as one half of the condor)
        if bull:
            ks = min(atm - step, _round(spot - 0.9 * em, step))
            legs = [leg("SELL", ks, "PE"), leg("BUY", ks - width, "PE")]
            title, cond = "Bull Put Spread", f"only if {name} is above {ks:,}"
            be = [ks - (legs[0]["est_premium"] - legs[1]["est_premium"])]
        else:
            ks = max(atm + step, _round(spot + 0.9 * em, step))
            legs = [leg("SELL", ks, "CE"), leg("BUY", ks + width, "CE")]
            title, cond = "Bear Call Spread", f"only if {name} is below {ks:,}"
            be = [ks + (legs[0]["est_premium"] - legs[1]["est_premium"])]
        credit = legs[0]["est_premium"] - legs[1]["est_premium"]
        strategies.append(pack(
            title, legs, credit, (width - credit) * lot, credit * lot,
            f"Enter at 10:15 (after the first hour) {cond}",
            f"Buy back at ₹{credit * 0.45:.1f} (keep ~55% of credit), else hold to expiry {expiry:%a %d %b}",
            f"Exit if the loss reaches 1× credit (≈₹{credit * lot:,.0f} per lot)",
            be, "Earns time decay; wins if the index does not fall (bull put) / rise (bear call) past the short strike."))
    else:
        wing = width
        sc, sp = max(atm + step, _round(spot + 0.9 * em, step)), min(atm - step, _round(spot - 0.9 * em, step))
        legs = [leg("SELL", sc, "CE"), leg("BUY", sc + wing, "CE"), leg("SELL", sp, "PE"), leg("BUY", sp - wing, "PE")]
        credit = legs[0]["est_premium"] - legs[1]["est_premium"] + legs[2]["est_premium"] - legs[3]["est_premium"]
        strategies.append(pack(
            "Iron Condor", legs, credit, (wing - credit) * lot, credit * lot,
            f"Enter at 10:15 (after the first hour) only if {name} is between {sp:,} and {sc:,}",
            f"Buy back at ₹{credit * 0.45:.1f} (keep ~55% of credit), else hold to expiry {expiry:%a %d %b}",
            f"Exit if the loss reaches 1× credit (≈₹{credit * lot:,.0f} per lot)",
            [sp - credit, sc + credit], "Hedged premium selling. Check SPAN margin with your broker."))

        fly_w = 2 * width
        legs = [leg("SELL", atm, "CE"), leg("SELL", atm, "PE"), leg("BUY", atm + fly_w, "CE"), leg("BUY", atm - fly_w, "PE")]
        credit = legs[0]["est_premium"] + legs[1]["est_premium"] - legs[2]["est_premium"] - legs[3]["est_premium"]
        strategies.append(pack(
            "Iron Fly (hedged short straddle)", legs, credit, (fly_w - credit) * lot, credit * lot,
            f"Enter only if {name} opens within ±{step} of {atm:,}",
            f"Buy back at ₹{credit * 0.7:.1f} (keep ~30% of credit)",
            f"{name} moves beyond {atm - width:,} / {atm + width:,}",
            [atm - credit, atm + credit],
            "Higher reward, tighter range. Never run an unhedged straddle."))

    return {
        "index": name, "spot": round(spot, 2), "atm": atm, "lot_size": lot,
        "expiry": expiry.isoformat(), "days_to_expiry": round(days, 2),
        "iv_used_pct": round(iv * 100, 2), "expected_move": round(em),
        "levels": {
            "prev_close": round(lv["prev_close"], 2), "ema20": round(lv["ema20"], 2),
            "support": round(sup, 2), "resistance": round(res, 2), "atr": round(lv["atr"], 2),
            "pivots": {k: round(v, 2) for k, v in pv.items()},
        },
        "strategies": strategies,
        "premium_note": "Premiums are Black-Scholes estimates from India VIX, not live quotes.",
    }
