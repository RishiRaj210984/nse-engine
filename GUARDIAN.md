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
