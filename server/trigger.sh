#!/usr/bin/env bash
# Start a GitHub engine run on the minute (GitHub's own schedule can be hours late).
# Usage: trigger.sh premarket|postclose|live      (called by cron on this server)
# Needs GITHUB_TOKEN in ~/nse-engine/guardian.env: a fine-grained token for this repo only,
# permission "Actions: Read and write".
set -uo pipefail
TASK="${1:?task}"
ENV="$HOME/nse-engine/guardian.env"
TOKEN="$(grep -E '^GITHUB_TOKEN=' "$ENV" 2>/dev/null | cut -d= -f2-)"
LOG="$HOME/nse-engine/trigger.log"
if [ -z "$TOKEN" ]; then echo "$(date '+%F %T') $TASK skipped: no GITHUB_TOKEN" >> "$LOG"; exit 0; fi
CODE=$(curl -s -o /tmp/trigger.out -w '%{http_code}' -X POST \
  -H "Authorization: Bearer $TOKEN" -H "Accept: application/vnd.github+json" -H "X-GitHub-Api-Version: 2022-11-28" \
  https://api.github.com/repos/RishiRaj210984/nse-engine/actions/workflows/daily-engine.yml/dispatches \
  -d "{\"ref\":\"main\",\"inputs\":{\"task\":\"$TASK\"}}")
echo "$(date '+%F %T') $TASK -> HTTP $CODE $( [ "$CODE" = 204 ] && echo ok || head -c 200 /tmp/trigger.out)" >> "$LOG"
