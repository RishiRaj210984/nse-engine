"""Build docs/USER_GUIDE.pdf (no secrets in it). Run: python tools/make_guide.py docs/USER_GUIDE.pdf (Windows fonts)."""
import sys
from reportlab.lib import colors
from reportlab.lib.enums import TA_LEFT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import (KeepTogether, PageBreak, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle)

pdfmetrics.registerFont(TTFont("Seg", r"C:\Windows\Fonts\segoeui.ttf"))
pdfmetrics.registerFont(TTFont("SegB", r"C:\Windows\Fonts\segoeuib.ttf"))
pdfmetrics.registerFont(TTFont("Mono", r"C:\Windows\Fonts\consola.ttf"))
from reportlab.pdfbase.pdfmetrics import registerFontFamily
registerFontFamily("Seg", normal="Seg", bold="SegB", italic="Seg", boldItalic="SegB")

INK, DIM, ACC, LINE, PANEL, WARN = (colors.HexColor(c) for c in ("#13201b", "#5b6c65", "#0d7a62", "#d9e2dd", "#eef3f0", "#9a6400"))
S = {
    "title": ParagraphStyle("t", fontName="SegB", fontSize=22, leading=27, textColor=INK, spaceAfter=4),
    "sub": ParagraphStyle("s", fontName="Seg", fontSize=10.5, leading=15, textColor=DIM, spaceAfter=10),
    "h1": ParagraphStyle("h1", fontName="SegB", fontSize=15, leading=20, textColor=ACC, spaceBefore=12, spaceAfter=6, keepWithNext=1),
    "h2": ParagraphStyle("h2", fontName="SegB", fontSize=11.5, leading=16, textColor=INK, spaceBefore=8, spaceAfter=3, keepWithNext=1),
    "p": ParagraphStyle("p", fontName="Seg", fontSize=10, leading=14.5, textColor=INK, spaceAfter=4, alignment=TA_LEFT),
    "small": ParagraphStyle("sm", fontName="Seg", fontSize=8.5, leading=12, textColor=DIM),
    "cell": ParagraphStyle("c", fontName="Seg", fontSize=9, leading=12.5, textColor=INK),
    "cellb": ParagraphStyle("cb", fontName="SegB", fontSize=9, leading=12.5, textColor=INK),
    "code": ParagraphStyle("code", fontName="Mono", fontSize=8.8, leading=12, textColor=INK, backColor=PANEL,
                           borderPadding=(5, 6, 5, 6), spaceBefore=3, spaceAfter=7, leftIndent=6, rightIndent=6),
    "warn": ParagraphStyle("w", fontName="Seg", fontSize=9.5, leading=13.5, textColor=WARN, borderColor=WARN, borderWidth=0.6,
                           borderPadding=(6, 7, 6, 7), spaceBefore=5, spaceAfter=9, leftIndent=7, rightIndent=7),
}
story = []
P = lambda t, st="p": story.append(Paragraph(t, S[st]))
def code(*lines):
    story.append(Paragraph("<br/>".join(l.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;") for l in lines), S["code"]))
def table(rows, widths, head=True):
    data = [[Paragraph(str(c), S["cellb" if (head and i == 0) or j == 0 and not head else "cell"]) for j, c in enumerate(r)] for i, r in enumerate(rows)]
    t = Table(data, colWidths=[w * mm for w in widths], repeatRows=1 if head else 0)
    st = [("GRID", (0, 0), (-1, -1), 0.4, LINE), ("VALIGN", (0, 0), (-1, -1), "TOP"),
          ("TOPPADDING", (0, 0), (-1, -1), 4), ("BOTTOMPADDING", (0, 0), (-1, -1), 4)]
    if head:
        st.append(("BACKGROUND", (0, 0), (-1, 0), PANEL))
    t.setStyle(TableStyle(st))
    story.append(t)
    story.append(Spacer(1, 7))

# ---------------- cover / overview
P("NSE Trade Desk: your guide", "title")
P("Daily routine, placing trades in ICICI Direct, the exit guardian, going live, and where every setting lives. "
  "Version of 21 Oct 2026. Keep this with you; the local project folder is no longer needed.", "sub")
P("Research tool only. Not investment advice and not a SEBI-registered adviser. No returns are guaranteed. "
  "Options can lose money quickly. Start with 1 lot and only trade what you can afford to lose.", "warn")

P("1. What you have and where it lives", "h1")
table([
    ["Piece", "Where", "What it does"],
    ["Trade Desk app", "rishiraj210984.github.io/nse-engine (home-screen icon on your phone)", "Plan, decisions (TRADE / SKIP), live prices, ICICI order tickets, your trades, results"],
    ["Engine + learning", "GitHub repo RishiRaj210984/nse-engine", "Runs 00:07, 07:13, 16:17 IST and every 10 min in market hours; re-learns each evening"],
    ["Exit guardian", "Oracle Cloud server, Mumbai, IP 144.24.100.32, user ubuntu", "Reads your ICICI positions, alerts or exits by the rules; enters the plan trade on command"],
    ["ICICI Breeze API", "api.icicidirect.com/apiuser/home (app 'guardian')", "ICICI's official API. Registered with the server's static IP"],
    ["Alerts and commands", "ntfy app on your phone + Trade Desk Live tab", "Alerts topic: guardian to you. Command topic + PIN: you to guardian"],
    ["Server key file", "Your safe place (not Drive, email or GitHub). A copy is in Oracle Cloud Shell", "Lets you log in to the server"],
], [32, 62, 76])

# ---------------- daily routine
P("2. Daily routine (trading days)", "h1")
table([
    ["Time (IST)", "Where", "Do this"],
    ["After 00:07 (optional)", "Trade Desk > Home", "Preview of tomorrow. Don't act on it."],
    ["Before 09:20", "Trade Desk > Live > Guardian", "1. Log in to ICICI (open in Chrome). Copy the number after apisession= from the address bar "
                                                      "(the page shows '405', that is normal). 2. Paste it. 3. Send login code. Wait for the alert 'Guardian: logged in'."],
    ["07:20 to 09:00", "Trade Desk > Home", "Read the FINAL plan: Quick view, global markets, Nifty and BankNifty boxes. Only boxes marked TRADE are trades."],
    ["09:15 to 10:15", "-", "Wait. The first hour is the noisiest."],
    ["10:15 to 11:30", "ICICI app, or Guardian button", "If a box says TRADE and the entry condition holds: place it (section 3) or tap 'Enter ... via guardian'."],
    ["During the day", "ntfy alerts", "Nothing to do. Alerts say BOOK PROFIT / EXIT NOW. In paper mode they say what it would do."],
    ["After 16:17", "Trade Desk > Home", "Check the profit and loss bar for the day."],
], [28, 40, 102])
P("If the app says a run is late (red notice), tap the refresh button after a few minutes. NSE holidays show 'Market closed'.", "small")

# ---------------- manual trade
P("3. Placing a condor by hand in ICICI Direct (1 lot)", "h1")
P("Open the box marked TRADE on Home and expand <b>ICICI Direct order ticket</b>. It lists the 4 legs, prices and the GTT values.")
table([
    ["Step", "In the ICICI Direct app"],
    ["1. Basket", "Create a basket. From the option chain (right expiry) add the 4 legs, BUY legs first. Quantity = lot size (Nifty 65, BankNifty 30). "
                  "Product: Options (NOT Options Plus, which closes at 3:15 PM). Order type: Limit at the ticket price. Check the margin shown."],
    ["2. Enter", "At 10:15, if the index is between the two SELL strikes, execute the basket. Check all 4 legs filled in Positions."],
    ["3. Protect", "Place one GTT OCO BUY on each SOLD leg with the ticket's target and stop. Set each limit Rs 1 to 3 above its trigger. "
                   "If your fills differ: target = 45% of your sell price; stop = your sell price + your total credit."],
    ["4. Record", "Trade Desk > Live > 'I took today's ... Iron Condor' (or add it by hand with your fills)."],
    ["5. Close", "When both sold legs are bought back (GTT), sell the 2 bought legs or let them expire if almost worthless. "
                 "If one stop fires, close the rest the same day. If you close a leg yourself, cancel its GTT."],
], [24, 146])

# ---------------- guardian
P("4. The exit guardian", "h1")
P("Runs on your Oracle server all day. <b>Paper mode</b> (now): alerts only, places no orders. <b>Live mode</b>: places the plan trade when you tap Enter, "
  "and exits whole trades by the rules. It never opens a trade by itself and never trades more than you hold.")
table([
    ["Rule", "Condors / sold options", "Bought options"],
    ["Book profit", "55% of the credit is in hand", "+60% (book at least half)"],
    ["Stop", "Loss equals the credit", "-30%"],
    ["Protect gains", "-", "Stop moves to cost after +20%; trails 25% below the peak after +40%"],
    ["Time", "Hold to expiry", "Exit by 15:15"],
], [30, 65, 75])
table([
    ["Button (Trade Desk > Live > Guardian)", "What happens"],
    ["1 / 2 / 3 Log in and send code", "Gives the guardian today's ICICI session (needed every trading day, ICICI rule)"],
    ["Enter NIFTY / BANKNIFTY via guardian", "Only if today's plan says TRADE, 10:10 to 11:30, index inside the short strikes. BUY legs first, limit orders, checks each fill"],
    ["Status", "Alert listing every open trade with P&amp;L and what the rules say"],
    ["Exit all", "Closes every option position now (live) or says what it would close (paper)"],
    ["Pause / Resume", "Stops / restarts all automatic entries and exits"],
], [62, 108])
P("If anything fails (rejected order, part fill, expired login) you get an urgent alert. Then act by hand in ICICI.", "small")

story.append(PageBreak())
# ---------------- going live
P("5. Going live: checklist and exact steps", "h1")
P("Only when all of these are true:")
table([
    ["Check", "Done?"],
    ["At least 3 to 5 paper days where the alerts matched what you would want", ""],
    ["Guardian P&amp;L matched ICICI's Positions screen within a few rupees", ""],
    ["ICICI account has enough margin for 1 lot (check the basket margin; usually tens of thousands of rupees)", ""],
    ["You know how to Pause, and how to exit by hand in ICICI", ""],
], [150, 20])
P("Switch to live (on the server, see section 7 for how to connect):", "h2")
code("nano ~/nse-engine/guardian.env        # change MODE=paper to MODE=live, keep MAX_LOTS=1",
     "                                      # save: F3 then Enter, exit: Ctrl+X",
     "sudo sed -i 's#guardian.py run$#guardian.py run --live#' /etc/systemd/system/guardian.service",
     "sudo systemctl daemon-reload && sudo systemctl restart guardian")
P("Your phone gets 'Guardian started · Mode: LIVE'. Both switches are needed (MODE=live and --live); either alone keeps it in paper mode.")
P("Back to paper at any time:", "h2")
code("nano ~/nse-engine/guardian.env        # MODE=paper",
     "sudo sed -i 's#guardian.py run --live#guardian.py run#' /etc/systemd/system/guardian.service",
     "sudo systemctl daemon-reload && sudo systemctl restart guardian")
P("Emergency: tap Pause in the app (or Exit all), then close positions in ICICI by hand if needed.", "warn")

# ---------------- settings
P("6. Where every setting lives", "h1")
P("<b>On the server</b>: file <font name='Mono'>~/nse-engine/guardian.env</font> (only there; never shared, never in GitHub).")
table([
    ["Setting", "Meaning"],
    ["BREEZE_API_KEY, BREEZE_API_SECRET", "From the Breeze portal (your app 'guardian')"],
    ["BREEZE_SESSION_TOKEN", "Today's login code. Normally sent from the app each morning; no need to edit"],
    ["MODE", "paper (alerts only) or live (may place and exit orders; also needs --live)"],
    ["NTFY_TOPIC", "Alerts to your phone (subscribe to it in the ntfy app)"],
    ["NTFY_CMD_TOPIC, GUARDIAN_PIN", "Commands from your phone. Keep both private; the PIN must not be your username or birth date"],
    ["MAX_LOTS", "Most lots one Enter may place (keep 1 at first)"],
    ["MAX_DAILY_ORDERS, POLL_SECONDS, TRAIL_PCT", "Safety cap (12), check interval (20 s), trailing gap for bought options (25%)"],
    ["GITHUB_TOKEN", "Starts the engine runs on time from the server (section 8)"],
], [62, 108])
P("<b>On your phone</b>: Trade Desk > Live > Guardian settings (command topic, PIN, Breeze API key) and Live prices relay (optional). "
  "Capital and risk % for share sizing: Stocks tab. These stay only on that phone.")
P("<b>Lot sizes and expiry days</b> follow NSE circulars (Nifty 65, BankNifty 30 in 2026). If NSE changes them, ask Claude to update "
  "backend/config.py in the repo.")

story.append(PageBreak())
# ---------------- server commands
P("7. Server: connect and common commands", "h1")
P("Oracle console (cloud.oracle.com) > the <b>&gt;_</b> Cloud Shell icon. Prompt <font name='Mono'>rishirajme@cloudshell</font> = Cloud Shell; "
  "connect to the server with:")
code("ssh -i ~/ssh-key-*.key ubuntu@144.24.100.32")
P("You are on the server when the prompt shows <font name='Mono'>ubuntu@guardian</font>. Then:")
table([
    ["Task", "Command"],
    ["Is the guardian running?", "<font name='Mono'>sudo systemctl status guardian --no-pager | head -5</font>"],
    ["Last 30 guardian lines", "<font name='Mono'>journalctl -u guardian -n 30 --no-pager</font>"],
    ["Restart it", "<font name='Mono'>sudo systemctl restart guardian</font>"],
    ["Stop it", "<font name='Mono'>sudo systemctl stop guardian</font>"],
    ["Get latest code", "<font name='Mono'>cd ~/nse-engine &amp;&amp; git pull &amp;&amp; sudo systemctl restart guardian</font>"],
    ["Read-only check", "<font name='Mono'>cd ~/nse-engine &amp;&amp; .venv/bin/python backend/guardian.py check</font>"],
    ["Edit settings", "<font name='Mono'>nano ~/nse-engine/guardian.env</font> (save F3 + Enter, exit Ctrl+X)"],
    ["On-time run log", "<font name='Mono'>tail -5 ~/nse-engine/trigger.log</font>"],
], [44, 126])
P("In the browser, Ctrl+O opens a file window: use F3 in nano instead. Cloud Shell disconnects after a while; just run the ssh line again.", "small")

# ---------------- on-time runs
P("8. One-time: make the morning runs on time", "h1")
P("GitHub's own schedule ran the 07:13 plan at 12:03 on 21 Oct. The server now starts each run on the minute. Needs one GitHub token (5 minutes):")
table([
    ["Step", "Do"],
    ["1", "github.com > your photo > Settings > Developer settings > Personal access tokens > Fine-grained tokens > Generate new token"],
    ["2", "Name: engine-trigger. Expiration: 1 year. Repository access: Only select repositories > nse-engine. "
          "Permissions > Repository permissions > Actions: Read and write. Generate, copy it (shown once)."],
    ["3", "On the server: <font name='Mono'>nano ~/nse-engine/guardian.env</font>, add a line <font name='Mono'>GITHUB_TOKEN=</font> followed by the token. Save (F3, Enter), exit (Ctrl+X)."],
    ["4", "On the server: <font name='Mono'>cd ~/nse-engine &amp;&amp; git pull &amp;&amp; bash server/install-cron.sh</font>. It ends with 'publish -&gt; HTTP 204 ok'."],
], [12, 158])
P("Renew the token before it expires (GitHub emails a reminder). Never paste it into a chat.", "small")

# ---------------- troubleshooting
P("9. Troubleshooting", "h1")
table([
    ["You see", "Do this"],
    ["App shows an old time / red 'run is late'", "Wait a few minutes and refresh. If it keeps happening, check section 8 is done."],
    ["'Couldn't download the latest plan'", "Phone offline, or office Wi-Fi blocking github.io. Use mobile data."],
    ["App looks old after an update", "Close it fully and reopen; if still old, remove the home-screen icon and add it again from Chrome."],
    ["'Guardian: not logged in' / 'cannot read positions'", "Send today's login code again (section 2)."],
    ["'405 Not Allowed' after ICICI login", "Normal. Copy the number after apisession= from the address bar."],
    ["No alerts at all", "ntfy app: subscribed to the exact alerts topic? On the server: guardian running? (section 7)"],
    ["'EXIT FAILED' / 'CHECK ICICI NOW'", "Open ICICI, see what filled, close or adjust by hand. Then Pause the guardian if unsure."],
    ["Box says NO TRADE", "Nothing to place today. Not trading is a valid decision."],
], [60, 110])

# ---------------- safety / help
P("10. Safety rules and getting help", "h1")
for t in ["Never share your ICICI password, Breeze API key or secret, PIN, session codes, GitHub token or the server key file, with anyone including Claude.",
          "Keep personal trading off work devices and work networks.",
          "Only boxes marked TRADE. Start with 1 lot. Respect the worst cases shown in each box.",
          "Back-tested numbers are estimates from rebuilt option prices, not broker fills. Live results decide.",
          "To change anything later: start a new Claude session and say: Clone https://github.com/RishiRaj210984/nse-engine and continue. "
          "Paste error messages (never keys) and Claude can fix them."]:
    P("• " + t)

def footer(canvas, doc):
    canvas.saveState()
    canvas.setFont("Seg", 8)
    canvas.setFillColor(DIM)
    canvas.drawString(18 * mm, 10 * mm, "NSE Trade Desk guide · research tool, not investment advice")
    canvas.drawRightString(192 * mm, 10 * mm, f"Page {doc.page}")
    canvas.restoreState()

out = sys.argv[1]
SimpleDocTemplate(out, pagesize=A4, leftMargin=18 * mm, rightMargin=18 * mm, topMargin=16 * mm, bottomMargin=18 * mm,
                  title="NSE Trade Desk guide", author="NSE Trade Desk").build(story, onFirstPage=footer, onLaterPages=footer)
print("written", out)
