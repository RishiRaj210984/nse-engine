#!/usr/bin/env bash
# One-time setup of the exit guardian on an Ubuntu server (Oracle Cloud Always Free).
# Run on the server:  curl -fsSL https://raw.githubusercontent.com/RishiRaj210984/nse-engine/main/server/setup.sh | bash
set -euo pipefail
echo "== timezone: IST"
sudo timedatectl set-timezone Asia/Kolkata
echo "== packages"
sudo apt-get update -y -q
sudo apt-get install -y -q python3-venv python3-pip git
echo "== code"
if [ -d "$HOME/nse-engine/.git" ]; then git -C "$HOME/nse-engine" pull -q; else git clone -q https://github.com/RishiRaj210984/nse-engine.git "$HOME/nse-engine"; fi
cd "$HOME/nse-engine"
python3 -m venv .venv
.venv/bin/pip install -q --upgrade pip
.venv/bin/pip install -q -r requirements-guardian.txt
echo "== private settings file (only on this server)"
if [ ! -f guardian.env ]; then cp guardian.env.example guardian.env; fi
chmod 600 guardian.env
echo "== background service (starts on boot, restarts if it stops)"
sudo tee /etc/systemd/system/guardian.service >/dev/null <<UNIT
[Unit]
Description=NSE Trade Desk exit guardian
After=network-online.target
Wants=network-online.target

[Service]
User=$USER
WorkingDirectory=$HOME/nse-engine
ExecStart=$HOME/nse-engine/.venv/bin/python backend/guardian.py run
Restart=always
RestartSec=20

[Install]
WantedBy=multi-user.target
UNIT
echo "== daily code update at 06:45 IST (gets fixes; restarts the guardian)"
# (crontab -l fails when there is no crontab yet; that must not stop the script)
{ crontab -l 2>/dev/null | grep -v nse-engine-update || true; echo "45 6 * * 1-5 cd $HOME/nse-engine && git pull -q && sudo systemctl restart guardian # nse-engine-update"; } | crontab -
sudo systemctl daemon-reload
sudo systemctl enable guardian >/dev/null
echo
echo "Public IP of this server (register THIS in Breeze):"
curl -s https://api.ipify.org; echo
echo
echo "Next: nano ~/nse-engine/guardian.env   (fill API key, secret, NTFY topics, PIN; keep MODE=paper)"
echo "Then: sudo systemctl start guardian && journalctl -u guardian -f"
