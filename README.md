# NSE Momentum & Options — self-learning research app

> ⚠️ **Disclaimer.** This software gives algorithmic insights based on technical rules and statistics.
> It does **not** guarantee returns and is **not** a SEBI-registered investment adviser or research
> analyst tool. SEBI's own studies show about 9 in 10 retail F&O traders lose money. Option premiums
> shown are estimates. Verify everything with your broker and consult a SEBI-registered adviser.

## What you get

| Tab | What it tells you |
|---|---|
| 🎯 **Today's Plan** | One screen: market bias, the options trade, grade A/B stocks, and a dated IST timeline (when to buy, when to exit) |
| 🌍 **Market** | Global indices, why the bias is what it is, all option structures with strikes / max loss / max reward, news |
| 📈 **Stocks** | Your 4-rule screener. Tap a stock → exact quantity to buy for your capital and risk % |
| 🧠 **Learning** | Live hit-rate vs backtest, and every rule change the engine made |

## How it runs (no server, no database)

```
GitHub Actions (free, cloud)            Your phone
 08:00 IST  premarket  ─┐               Expo app reads
 16:30 IST  postclose  ─┼─ commits ──►  docs/*.json
            (grade, retrain, publish)   (plan, bias, stocks, performance)
 state/*.json = journal + model versions, kept in your repo's git history
```

## Setup — one time, about 15 minutes

### 1. Put the code on GitHub
1. Create a free account at github.com, then click **New repository** and name it, for example, `nse-engine`.
2. Upload this whole folder (drag and drop on the repo page works).
3. Open the repo's **Actions** tab, click **daily-engine**, then **Run workflow** with task `bootstrap`. It takes about 2 minutes and seeds roughly 2 years of history so learning starts on day one.

After that it runs by itself every weekday. *GitHub cron can start 5–30 minutes late, which is why pre-market is scheduled for 08:00.*

> Public repo = the app can read the data with no password. Your data is only market data,
> but if you want it private you will need a token or the Live API option instead.

### 2. Run the app on your phone
On your computer (Node.js 18+ installed):

```bash
npx create-expo-app@latest nse-app --template blank
```
Copy `mobile/App.js` and `mobile/src/` into `nse-app/`, replacing `App.js`. Then, inside `nse-app`:
```bash
npx expo install @tanstack/react-query zustand @react-native-async-storage/async-storage @react-navigation/native @react-navigation/bottom-tabs react-native-screens react-native-safe-area-context
```
```bash
npx expo start
```
Install **Expo Go** on your phone and scan the QR code. In the app's **Settings**, paste:
`https://raw.githubusercontent.com/<your-username>/nse-engine/main/docs`

### 3. (Optional) Live API instead of snapshots
```bash
cd backend && pip install -r requirements.txt && uvicorn main:app --host 0.0.0.0 --port 8000
```
In the app, choose Settings → **Live API** and enter `http://<your-PC-LAN-IP>:8000`.
Endpoints: `/api/global-bias`, `/api/volume-breakouts`, `/api/plan`, `/api/performance`, `POST /api/position-calculator`.

## Daily routine (what the timeline in the app says)

| Time (IST) | Do |
|---|---|
| 08:00–09:00 | Read Today's Plan. Set alerts at entry and SL. |
| 09:15–09:30 | **Wait.** Cancel stocks that open above T1 or below SL. |
| 09:30–10:30 | Stock buy window: only if price holds above entry. SL order immediately. |
| 09:45–11:00 | Options window. Condor / fly only after 10:00 inside the range. |
| after 11:30 | No new stock entries. |
| 14:30–15:00 | Option buyers exit if target not hit. |
| Day 2, by 15:15 | Exit anything still open. Book half at T1, trail the rest. |

## How the self-learning works

1. **Journal**: every signal, including near-misses, is saved with 12 features.
2. **Grade**: after 2 trading days each signal is marked T2 / T1 / SL / TIME. Fills are realistic: entry at the next open, the stop is assumed hit first when one bar touches both stop and target, and costs (0.25%) are deducted.
3. **Learn** (every evening):
   - Re-test SL/target sizes and entry thresholds on all graded trades; recent and live trades count more.
   - A win-probability model (logistic regression) switches on **only if** it beats a simple base rate on later, unseen data.
   - The bias threshold is re-tuned on 500 days of walk-forward predictions. News weight starts at 0 and is learned from live results.
4. **Guardrails**: each rule moves at most one step per day and needs 40 or more trades behind it. Every change is listed in the 🧠 tab.

## Honest numbers (from the first run, 18 Oct 2026)

| | Result |
|---|---|
| Bias model, out-of-sample | ~61–70% correct on directional calls (it calls about 1 day in 4–5; other days = "sideways") |
| Stock rules, 2-yr backtest, **net of costs** | ~49% win rate, about **0R per trade (break-even)** on 116 trades |
| ML ranker | **OFF**: no proven edge yet |

Read this as "the system is not lying to you". The stock module is not yet profitable after costs in
backtest. The engine keeps testing, and the app shows live results so you can judge before risking money.

## Research behind the filters
- **Market-regime gate** (Nifty above 50-DMA and up over 20 days): momentum works after up-markets and fails after down-markets (Cooper, Gutierrez & Hameed, *J. Finance* 2004).
- **Skip one-day spikes over 4–7%**: Indian studies find short-term *reversal* at 1-day to 1-month horizons.
- **High-volume premium**: abnormal volume relative to the stock's own history predicts returns (Gervais, Kaniel & Mingelgrin 2001). It has not been tested specifically on NSE.
- **US overnight to Nifty**: NSE research finds S&P/Nasdaq lead Nifty, mostly in the opening gap.
- **Options**: SEBI FY26 found option *sellers* were the only group with positive median returns, which is why sideways days get hedged condors and never naked straddles.
- No platform (Chartink, Sensibull, Tickertape, and so on) publishes audited success rates. Since May 2026, SEBI's PaRRVA requires verified performance for advertised claims.

## Things to verify yourself
- Lot sizes in `backend/config.py` (Nifty 65, BankNifty 30) and expiry days (Nifty weekly Tuesday, BankNifty monthly). These change by NSE circular.
- NSE holidays are not modelled; expiry shifts to the previous trading day on holidays.
- GIFT Nifty is not on Yahoo Finance; overnight US data is used instead.
- yfinance is unofficial and can break or lag. The app shows "Updated X min ago" on every tab.

## Options lab (added 19 Oct 2026)
Every evening `backend/options_lab.py` replays the plan's exact option structures on ~2 years of hourly
data for **NIFTY (weekly expiry)** and **BANKNIFTY (monthly expiry)**: iron condor, bull/bear credit
spreads and bull/bear debit spreads, with the plan's own entry (10:15), target (keep 55% of credit),
stop (loss = 1× credit) and costs + slippage. Directional structures are tested only on days the
bias model actually called that direction (out-of-sample). Each morning every option idea is marked:

- **TRADE**: this setup averaged ≥ ₹100 per lot with enough history
- **CAUTION**: barely positive (₹0–100), untested, or thin sample: half size or skip
- **SKIP**: lost money historically, or ≤1 day to expiry right after heavyweight results

First run's findings: condors pay on the right days (Nifty 3–5 days before expiry, BankNifty
≥3 days before); expiry-day condors and all directional spreads lost money after costs.

## Pre-open global model and run times (added 19 Oct 2026)
`backend/premodel.py` measures every market's move since Nifty's last close: S&P, Nasdaq and Dow
futures, Europe's close (DAX, FTSE, Euro Stoxx), Nikkei, crude, gold, dollar index and USD/INR,
plus Nifty's previous day and India VIX. It predicts the opening gap and the full-day move,
trained on the same time-of-day snapshot over ~2 years and refit on every run. Out-of-sample at
07:30 IST: gap correlation 0.74, day-direction calls right 75% (156 of 300 days). GIFT Nifty has no
free feed; these inputs drive it.

| Run (IST) | What it is |
|---|---|
| 00:00 | Overnight **preview**: Europe closed, US mid-session. Not graded. |
| 07:30 | **Final** plan: US close, futures, Asia open. Graded by the evening review. |
| 16:30 | Scan, grade, re-learn (rules, options lab, review), publish the next session. |

GitHub can start scheduled jobs 5–30 minutes late.
