# Run the guardian on a free Oracle Cloud server (static IP, no desktop needed)

**Why:** SEBI requires API orders from a static IP registered with ICICI. Oracle's *Always Free* tier gives
a small Linux server with a reserved public IP in Mumbai at no cost. The guardian then runs all day there.
Sign-up and every login are yours to do; never share passwords, keys or codes in a chat.

## A. Create the server (about 25 minutes, once)
1. Go to **oracle.com/cloud/free** → **Start for free**. Choose **Home Region: India West (Mumbai)**
   (it cannot be changed later). Oracle asks for a card to verify identity; *Always Free* resources should
   not be charged, but read Oracle's terms on the sign-up page.
2. Console → **Compute → Instances → Create instance**.
   - Name: `guardian`. Image: **Ubuntu 24.04** (or 22.04).
   - Shape: an **Always Free-eligible** one (e.g. *VM.Standard.E2.1.Micro*, or *Ampere A1* with 1 OCPU / 6 GB).
   - Networking: keep "Assign a public IPv4 address".
   - SSH keys: **Generate a key pair for me** → download the **private key** (keep it safe).
   - **Create**. Wait until it shows *Running*.
3. Make the IP permanent: instance → **Attached VNICs** → the VNIC → **IPv4 Addresses** → the row's **⋯ → Edit**
   → **Reserved public IP** → create one → **Update**. Note this IP: it is your **static IP**.
4. Open a terminal on the server: the console's **Cloud Shell** (top bar `>_` icon) and then
   `ssh -i <your-key-file> ubuntu@<static-IP>`, or any SSH app on your computer.

## B. Install the guardian (5 minutes)
On the server, paste:
```
curl -fsSL https://raw.githubusercontent.com/RishiRaj210984/nse-engine/main/server/setup.sh | bash
```
It sets IST time, installs Python, downloads the code, creates the background service and prints the
server's public IP. It should match the static IP from A3.

## C. Register Breeze with that IP (10 minutes)
1. **api.icicidirect.com/apiuser/home** → log in → register an app. Redirect URL `http://127.0.0.1`.
   **Static IP: the server's IP.** Note the **API key** and **secret**.
2. On the server: `nano ~/nse-engine/guardian.env` and fill in
   `BREEZE_API_KEY`, `BREEZE_API_SECRET`, `NTFY_TOPIC` (alerts), `NTFY_CMD_TOPIC` (commands, a different
   long random name), `GUARDIAN_PIN` (6+ digits). Keep `MODE=paper`. Save with Ctrl+O, Enter, Ctrl+X.
3. `sudo systemctl start guardian` → `journalctl -u guardian -f` shows it running. Ctrl+C leaves it running.

## D. On your phone
1. **ntfy app:** subscribe to your `NTFY_TOPIC` (alerts arrive here).
2. **Trade Desk → Live → Guardian:** enter the command topic and PIN once (stored only on your phone).

## E. Every trading day (about 1 minute)
1. Before 09:20: Trade Desk → Live → Guardian → **Log in to ICICI** → log in → copy the `apisession` value
   from the address bar → paste → **Send login code**. The phone shows "Guardian: logged in".
2. If the plan box says **TRADE**: after 10:15 tap **Enter via guardian (1 lot)**. You get an alert listing
   what it placed (PAPER: what it *would* place).
3. Walk away. Exits are automatic (LIVE) or alerted (PAPER). **Pause** at any time with the Pause button.

## F. Going live (only after a few paper days look right)
Edit guardian.env: `MODE=live`, then `sudo sed -i 's#guardian.py run$#guardian.py run --live#' /etc/systemd/system/guardian.service`
and `sudo systemctl daemon-reload && sudo systemctl restart guardian`. Start with `MAX_LOTS=1`.
To go back to paper: undo both changes and restart.
