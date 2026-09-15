#!/usr/bin/env bash
# Shows the watchdog's recent activity -- alerts, kills, and dry-run notices --
# from Cloud Logging. This is the default way to see what the watchdog is
# doing when ALERT_WEBHOOK_URL is left blank (dry-run mode, no Slack/Teams
# needed).

set -euo pipefail

REGION="us-central1"
FUNCTION_NAME="looker-query-watchdog"
LIMIT="${1:-50}"   # pass a number as the first arg to see more/fewer lines, e.g. ./view_alerts.sh 200

gcloud functions logs read "${FUNCTION_NAME}" --region="${REGION}" --gen2 --limit="${LIMIT}"
