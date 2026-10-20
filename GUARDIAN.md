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
