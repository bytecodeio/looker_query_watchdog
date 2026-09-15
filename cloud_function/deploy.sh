#!/usr/bin/env bash
# Deploys the Looker Query Watchdog Cloud Function + Cloud Scheduler trigger.
# Run this from inside the cloud_function/ directory.
#
# Prerequisites:
#   - gcloud CLI installed and authenticated (`gcloud auth login`)
#   - A GCP project selected (`gcloud config set project YOUR_PROJECT_ID`)
#   - A Looker service account API key (see watchdog.txt section 3.1)
#
# Fill in the variables below before running.

set -euo pipefail

# --- Edit these ---
REGION="us-central1"
# Cloud Functions Gen2 Python runtime. Different GCP environments (e.g. a
# client's own project) may only offer certain runtimes -- check what's
# available with: gcloud functions runtimes list --region="${REGION}"
RUNTIME="python311"
LOOKER_BASE_URL="https://YOUR_INSTANCE.looker.com"
LOOKER_CLIENT_ID="YOUR_CLIENT_ID"
# Leave blank to run in dry-run mode: alerts are logged (visible via
# `gcloud functions logs read` or view_alerts.sh) instead of posted anywhere.
# Set to a real Slack/Teams/Google Chat incoming webhook URL to also post
# there. See README.md "Sending alerts to Slack" for how to create one.
ALERT_WEBHOOK_URL=""
ALERT_THRESHOLD_MINUTES="15"
KILL_THRESHOLD_MINUTES="60"
ENABLE_AUTO_KILL="false"       # keep false for the first 1-2 weeks
SCHEDULE_INTERVAL_MINUTES="5"
SCHEDULE_CRON="*/5 * * * *"    # must match SCHEDULE_INTERVAL_MINUTES above
# --- End edit ---

FUNCTION_NAME="looker-query-watchdog"
SECRET_NAME="looker-api-client-secret"
INVOKER_SA_NAME="looker-watchdog-invoker"
PROJECT_ID="$(gcloud config get-value project)"
INVOKER_SA_EMAIL="${INVOKER_SA_NAME}@${PROJECT_ID}.iam.gserviceaccount.com"

echo "Deploying to project: ${PROJECT_ID}, region: ${REGION}"

echo "==> Enabling required APIs..."
gcloud services enable \
    secretmanager.googleapis.com \
    cloudfunctions.googleapis.com \
    run.googleapis.com \
    cloudscheduler.googleapis.com

echo "==> Storing Looker client secret in Secret Manager..."
if gcloud secrets describe "${SECRET_NAME}" >/dev/null 2>&1; then
  echo "Secret ${SECRET_NAME} already exists -- adding a new version."
  read -r -s -p "Enter Looker client secret: " LOOKER_CLIENT_SECRET
  echo
  printf '%s' "${LOOKER_CLIENT_SECRET}" | gcloud secrets versions add "${SECRET_NAME}" --data-file=-
else
  read -r -s -p "Enter Looker client secret: " LOOKER_CLIENT_SECRET
  echo
  printf '%s' "${LOOKER_CLIENT_SECRET}" | gcloud secrets create "${SECRET_NAME}" \
      --data-file=- \
      --replication-policy="automatic"
fi
unset LOOKER_CLIENT_SECRET

echo "==> Deploying Cloud Function..."
gcloud functions deploy "${FUNCTION_NAME}" \
    --gen2 \
    --runtime="${RUNTIME}" \
    --region="${REGION}" \
    --source=. \
    --entry-point=check_running_queries \
    --trigger-http \
    --no-allow-unauthenticated \
    --set-env-vars="LOOKER_BASE_URL=${LOOKER_BASE_URL},LOOKER_CLIENT_ID=${LOOKER_CLIENT_ID},ALERT_THRESHOLD_MINUTES=${ALERT_THRESHOLD_MINUTES},KILL_THRESHOLD_MINUTES=${KILL_THRESHOLD_MINUTES},ENABLE_AUTO_KILL=${ENABLE_AUTO_KILL},SCHEDULE_INTERVAL_MINUTES=${SCHEDULE_INTERVAL_MINUTES},ALERT_WEBHOOK_URL=${ALERT_WEBHOOK_URL}" \
    --set-secrets="LOOKER_CLIENT_SECRET=${SECRET_NAME}:latest"

FUNCTION_URI="$(gcloud functions describe "${FUNCTION_NAME}" --region="${REGION}" --format='value(serviceConfig.uri)')"

echo "==> Ensuring Scheduler invoker service account exists..."
if ! gcloud iam service-accounts describe "${INVOKER_SA_EMAIL}" >/dev/null 2>&1; then
  gcloud iam service-accounts create "${INVOKER_SA_NAME}" \
      --display-name="Looker Watchdog Scheduler Invoker"
fi

echo "==> Granting Cloud Run Invoker permission..."
gcloud functions add-iam-policy-binding "${FUNCTION_NAME}" \
    --region="${REGION}" \
    --member="serviceAccount:${INVOKER_SA_EMAIL}" \
    --role="roles/run.invoker"

echo "==> Creating/updating Cloud Scheduler job..."
if gcloud scheduler jobs describe looker-watchdog-trigger --location="${REGION}" >/dev/null 2>&1; then
  gcloud scheduler jobs update http looker-watchdog-trigger \
      --location="${REGION}" \
      --schedule="${SCHEDULE_CRON}" \
      --uri="${FUNCTION_URI}" \
      --http-method=POST \
      --oidc-service-account-email="${INVOKER_SA_EMAIL}" \
      --oidc-token-audience="${FUNCTION_URI}"
else
  gcloud scheduler jobs create http looker-watchdog-trigger \
      --location="${REGION}" \
      --schedule="${SCHEDULE_CRON}" \
      --uri="${FUNCTION_URI}" \
      --http-method=POST \
      --oidc-service-account-email="${INVOKER_SA_EMAIL}" \
      --oidc-token-audience="${FUNCTION_URI}"
fi

echo "==> Done. Function URI: ${FUNCTION_URI}"
echo "Logs: gcloud functions logs read ${FUNCTION_NAME} --region=${REGION} --gen2"
