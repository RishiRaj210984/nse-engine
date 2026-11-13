# Exit guardian: trial guide

The guardian watches **your real ICICI Direct F&O positions** and applies the app's exit rules, so exits
happen even when you are not looking. Start in **PAPER** mode: it only alerts what it *would* do.

| Rule | Condors / sold options | Bought options |
|---|---|---|
| Book profit | 55% of the credit is in hand | +60% |
| Stop | loss = 1× the credit | −30% |
| Protect gains | – | stop to cost after +20%; trails 25% below the peak after +40% |
| Time | hold to expiry | exit by 15:15 |

**Safety locks:** exits only (never opens or adds), never more than you hold, 09:20–15:25 IST on trading
days only, max 12 orders/day, sold legs closed before bought legs, and a file named `STOP` in the
project folder (or the app's Pause button) pauses every automatic action until Resume.

**Run it on a server with a static IP:** see **ORACLE_SETUP.md** (free Oracle Cloud). From the phone you
send today's login code, *Enter via guardian* (places today's plan trade, BUY legs first, checks the plan,
the 10:10–11:30 window and the entry condition), *Status*, *Exit all*, *Pause*, *Resume*. LIVE needs `MODE=live` in guardian.env **and** `--live` when starting.

## One-time setup (about 20 minutes)
1. **Breeze API key.** Log in to ICICI's Breeze API portal (https://api.icicidirect.com/apiuser/home),
   register an app, and note the **API key** and **secret key**. For LIVE orders, register your
   **static IP** there (SEBI requires API orders from a registered static IP; most home broadband
   IPs change, so this usually means a static-IP plan or a small cloud server).
2. **Python** on the computer that will run it: in the project folder run
   `pip install -r requirements-guardian.txt` (plus `backend/requirements.txt`).
3. Copy `guardian.env.example` to `guardian.env` and fill in the key and secret. Keep this file only on
   that computer: not in Google Drive, email, chat or GitHub.
4. **Phone alerts (free):** install **ntfy** (Android/iPhone), subscribe to a long random topic name
   (e.g. `rishi-guard-7x2k9q`), and put the same name in guardian.env as `NTFY_TOPIC`.

## Every trading day (about 2 minutes, before 09:20)
1. `python backend/guardian.py login` → open the link, log in to ICICI, copy the `apisession` value
   from the address bar into guardian.env as `BREEZE_SESSION_TOKEN=...` (it expires at midnight).
2. `python backend/guardian.py run` (paper) — leave the window open until 15:30.
3. Watch ntfy alerts and `guardian.log`. To stop at any time: create a file named `STOP` in the folder,
   or close the window.

## Trial checklist (paper mode, a few trading days)
- [ ] It reads your positions (the log lists each trade with P&L every ~20 s).
- [ ] Its P&L matches ICICI's Positions screen within a few rupees.
- [ ] Alerts arrive on your phone through ntfy.
- [ ] The "would BOOK PROFIT / EXIT NOW" moments are ones you agree with.

Only after all four: set `MODE=live`, start with `python backend/guardian.py run --live`, 1 lot only.
If ICICI rejects an order (for example, no registered static IP) you get an urgent "EXIT BY HAND NOW" alert.

**Tested offline** (`python tests/test_guardian.py`): condor hold / book / stop, bought-option stop,
trailing exit and book, paper mode sends no orders, STOP file, rejected-order alert.

## One real test trade (₹15,000)
When the guardian runs in LIVE mode it places **one** real trade, then goes back to paper entries:
- only **NIFTY**, only from **4 Nov 2026** (after the 3 Nov expiry), **1 lot**;
- the plan's Iron Condor with **150-point wings** (same sold strikes, bought strikes 150 points out),
  so the worst case is about ₹7,500 instead of about ₹12,000;
- before any order it asks **ICICI's margin calculator** for the whole basket and reads your free F&O money.
  If the margin is unknown, above ₹15,000, or above your free F&O money, nothing is placed;
- BankNifty and every later entry are paper. The real position is exited automatically
  (55% target, 1× stop, 10:15 on expiry day), and the emergency Close all still works.

Settings (guardian.env): `CAPITAL`, `LIVE_INDEX`, `LIVE_FROM`, `LIVE_WING`, `LIVE_MAX_TRADES`.
Run `python backend/guardian.py check` once before the day: line 7 shows ICICI's margin for the test trade.

## 3rd trade: NIFTY first-hour momentum buy
If NIFTY moves 0.5% or more from 09:15 to 10:15, the engine marks a momentum buy: 1 lot of the ATM call (up)
or put (down), not on expiry day, premium up to ₹15,000. It is paper-tracked by itself on real NSE prices.
In the app (step 3) it gets its own card and button between 10:15 and 11:30, like the condor.
Tapping it sends `ENTER MOMENTUM`: in paper mode the guardian answers with what it would buy; real orders
only with `LIVE_MOMENTUM=1` in guardian.env **and** the guardian in live mode. Real exits are automatic:
stop −30%, cost after +20%, trail from +40%, book +60%, close 15:15. It is refused if you already hold a NIFTY
position on the same expiry, because the exits treat one expiry as one structure.

## 4th trade: stock calls and puts
Every stock the plan recommends gets a call for a BUY pick or a put for a SELL pick (SELL picks are the
mirror of the BUY rules and appear only while NIFTY is below its 50-day average and down over 20 days).
When the share crosses its entry between 09:30 and 10:30, the paper tracker buys 1 lot in **each of three
budgets** (app v53): the strike nearest the money whose lot costs up to ₹5,000, ₹5,000–10,000 and
₹10,000–15,000, at the real NSE mid price with a two-sided quote (monthly expiry at least 7 days away).
Since v54: strikes priced under ₹2 are skipped (one tick moved them 2.5%+, so the stop rules fired on noise),
and a share already more than 1% past its entry level at a check is not chased (it can still be bought later
in the window if it comes back within 1%).
Each budget keeps its own record. In the app (step 3, section 4) each budget is a subsection, and each open
trade has its own card and button until 11:30. Tapping sends `ENTER STOCK_<SYMBOL>_<5K|10K|15K>`: in paper
mode the guardian answers with what it would buy; real orders only with `LIVE_STOCKOPT=1` **and** live mode.
One real buy per stock a day: all three budgets use the same expiry, so the exits could not tell two apart.
Exits are the same as every bought option: stop −30%, cost after +20%, trail from +40%, book +60%, close 15:15.
In a 2-year test on NSE's real prices every budget lost on average (up to ₹5k −₹232 a trade, ₹5–10k −₹312,
₹10–15k −₹412), and booking early at +₹1–2k was worse. Past winning stocks, results days and news-like gaps
did not hold up either. It is tracked on paper to see each budget's live record, not because it has an edge.

## 5th trade: overnight stock option (paper only, app v57)
Between 15:00 and 15:30 the engine checks every F&O stock in a trend that matches NIFTY's (call: above its 20-day
average, the 20-day above the 50-day, NIFTY above its 50-day; put: the mirror). It buys 1 lot of the strike one step in
the money (monthly expiry at least 7 days away, up to ₹15,000) only when the option is cheap: implied volatility under
0.8× the share's own movement over the last 20 days. It sells at the next trading day's close, with no stop. In a
2-year test on NSE's real closing prices it was the only rule of 194 that made money in both years (+₹478, then +₹404 a
trade, 45% won). It is weak, though: calls won only in year 1 and puts only in year 2, and one trade a day fell up to
₹92,779 from its best point. The guardian never places it; it is tracked on paper only.

## Total-money limit (every real entry)
Each trade has its own limit (premium or margin up to ₹15,000), and on top of that the guardian checks the
total before **every real order** (condor, momentum buy, stock option): the money already in your open F&O
trades plus the new trade must stay within `CAPITAL` (₹15,000 by default). "Already in trades" is read from
ICICI each time: the margin ICICI holds for your trades (`block_by_trade_fno`) plus the premium paid for every
bought option you still hold, including trades you placed yourself. It is counted on the safe side: if ICICI's
blocked figure already includes some of that premium, it counts twice, so the guardian may refuse a little early,
never late. If ICICI's positions or funds cannot be read, nothing is placed. The alert for a real entry says
how much is in trades after it, e.g. "Rs 10,500 of your Rs 15,000 limit". Paper trades are never limited:
the paper book tracks every trade so their records can be judged.
