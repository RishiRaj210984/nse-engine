# Put the engine on GitHub: step by step

Takes about 15 minutes. You only do this once. Afterwards the engine runs every weekday on
GitHub's computers for free, and you can reconnect to it from any computer.

Replace `YOUR-NAME` below with the GitHub username you pick.

---

## Part A: Create a GitHub account (skip if you have one)

1. Go to **https://github.com/signup**.
2. Enter your email address and click **Continue**.
   Use a **personal** email for a personal project, and check your company's policy on personal projects on work devices.
3. Create a password (at least 15 characters, or 8 with a number and a lowercase letter) and click **Continue**.
4. Choose a username (for example `rishi-markets`). It will appear in your links. Click **Continue**.
5. Solve the "verify you're human" puzzle and click **Create account**.
6. GitHub emails you an 8-digit code. Type it in.
7. If it asks about plans, choose **Free**. You never need to pay for this.

> GitHub will later ask you to turn on two-factor authentication (2FA). Say yes and use an authenticator app.
> It protects the account.

## Part B: Create the repository (the project's home)

1. When logged in, click the **+** at the top right and choose **New repository**.
2. Fill in:
   - **Repository name:** `nse-engine`
   - **Description** (optional): `Self-learning NSE momentum & options research`
   - Choose **Public**. The phone app and page can then read the data without a password. The repo holds only market data and code, no personal information.
   - Leave **Add a README**, **.gitignore** and **license** all **unticked**. The repo must start empty.
3. Click the green **Create repository** button.

## Part C: Upload the files

1. On the new, empty repo page, find the line *"…or **uploading an existing file**"* and click that link.
2. On your computer, open **File Explorer** and go into the folder **`upload-to-github`**.
   Its location is shown in the chat where you got this guide.
3. **Inside** that folder, press **Ctrl + A** to select everything. You should see 8 items: `.github`, `backend`, `docs`, `mobile`, `state`, `.gitignore`, `GITHUB_SETUP.md`, `README.md`.
4. **Drag** the selected items onto the GitHub page, onto the box that says *"Drag files here"*.
5. Wait until the list finishes loading. Scroll down and check that it includes
   **`.github/workflows/daily-engine.yml`**. This file is the daily timer, and without it nothing runs automatically.
   *If it's missing, see "The .github folder did not upload" at the bottom.*
6. At the bottom, under **Commit changes**, type `First upload` and click the green **Commit changes** button.

## Part D: Allow the engine to save its daily results

1. In your repo, click **Settings** (top menu, gear icon).
2. In the left menu, click **Actions**, then **General**.
3. Scroll to **Workflow permissions** and choose **Read and write permissions**.
4. Click **Save**.

## Part E: Do a first test run

1. Click the **Actions** tab (top menu).
2. If you see a yellow box saying workflows aren't enabled, click **"I understand my workflows, go ahead and enable them"**.
3. In the left list, click **daily-engine**.
4. On the right, click **Run workflow**. In the **task** box type `publish`, then click the green **Run workflow** button.
5. After about 5 seconds a new run appears. Click it to watch. A **green tick** after 2–4 minutes means it worked.
   A **red ✗** means something failed; see Troubleshooting.

From now on it runs **by itself every weekday**:

| Time (IST) | What it does |
|---|---|
| ~08:00 | Pre-market: reads the US close, sets the market bias and the options plan |
| ~16:30 | After close: scans stocks, grades results from 2 days ago, **learns**, publishes tomorrow's view |

GitHub sometimes starts scheduled jobs 5–30 minutes late, which is why the morning run is at 08:00.

## Part F: Turn on the phone-friendly page (no app install needed)

1. **Settings**, then **Pages** in the left menu.
2. Under **Build and deployment**: set **Source** to **Deploy from a branch**.
3. Set **Branch** to **main**, then folder **/docs**, and click **Save**.
4. Wait 1–2 minutes and refresh. The page shows your link:
   **`https://YOUR-NAME.github.io/nse-engine/`**
5. Open that link on your phone and use **Add to Home screen**. It updates itself after every run.

## Part G: Connect the Expo app (optional, for the full app)

In the app's **Settings** tab, choose **Cloud (GitHub)** and paste:
```
https://raw.githubusercontent.com/YOUR-NAME/nse-engine/main/docs
```

---

## Reconnecting later (with Claude or on another computer)

Everything lives in the repo: the code, the learning journal (`state/journal.json`), every model
version (`state/model_state.json`) and the daily outputs (`docs/`). To pick up later:

- **With Claude Code:** start a new session, pick any empty folder, and say:
  *"Clone https://github.com/YOUR-NAME/nse-engine and continue working on it."*
  Claude reads the README and the history and carries on from there.
- **To see the learning history:** open the repo, then click **Commits**. Each daily run is listed as
  `engine: postclose 2026-10-19 16:31 IST`.
- **Keep one source of truth:** the bot commits every day, so a copy on your computer goes stale.
  Always get the latest version (`git pull`, or **Fetch origin** in GitHub Desktop) before changing anything.

---

## Troubleshooting

**Red ✗ on a run.** Click the run, then the failed step, and read the last lines.
- `Permission denied` / `403` on the "Commit" step: redo **Part D**.
- `yfinance` / `Too Many Requests`: Yahoo throttled GitHub's server. Click **Re-run jobs**; it usually passes.

**The .github folder did not upload.** Some browsers skip folders starting with a dot. Fix:
1. In the repo, click **Add file**, then **Create new file**.
2. In the name box type exactly: `.github/workflows/daily-engine.yml`. The slashes create the folders.
3. Open `upload-to-github\.github\workflows\daily-engine.yml` in Notepad, copy everything, and paste it into GitHub.
4. Click **Commit changes**.

**The phone page shows 404.** Pages can take a few minutes the first time. Check that **Part F** says `main` and `/docs`.

**Scheduled runs stopped.** GitHub pauses schedules on repos with no activity for 60 days. The daily
bot commits count as activity, so this only happens if the job keeps failing. Check the Actions tab.
