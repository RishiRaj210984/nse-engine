#!/usr/bin/env bash
# Install the on-time schedule on this server (times are IST; setup.sh set the server to IST).
set -uo pipefail
D="$HOME/nse-engine"
chmod +x "$D/server/trigger.sh"
{ crontab -l 2>/dev/null | grep -v 'nse-engine-trigger' || true
  echo "7 0 * * 1-5 $D/server/trigger.sh premarket # nse-engine-trigger (00:07 preview)"
  echo "13 7 * * 1-5 $D/server/trigger.sh premarket # nse-engine-trigger (07:13 final plan)"
  echo "17 16 * * 1-5 $D/server/trigger.sh postclose # nse-engine-trigger (16:17 learning)"
  echo "*/10 9-15 * * 1-5 $D/server/trigger.sh live # nse-engine-trigger (live snapshots)"
} | crontab -
echo "Installed. Current schedule:"; crontab -l
echo; echo "Test now (starts one run):"; "$D/server/trigger.sh" publish; tail -1 "$D/trigger.log"
