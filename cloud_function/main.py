"""Looker Core In-Flight Query Watchdog and Alerting Service.

Monitors active dashboard and explore queries via the Looker 4.0 API.
Sends immediate webhook notifications when queries exceed defined runtime
thresholds and optionally terminates queries exceeding maximum SLA ceilings.
"""

import datetime
import os
from typing import Any, Dict

import functions_framework
import looker_sdk
import requests

# -------------------------------------------------------------------------
# Configuration & Thresholds
# -------------------------------------------------------------------------
LOOKER_BASE_URL = os.environ.get("LOOKER_BASE_URL")  # e.g. "https://myinstance.looker.com"
LOOKER_CLIENT_ID = os.environ.get("LOOKER_CLIENT_ID")
LOOKER_CLIENT_SECRET = os.environ.get("LOOKER_CLIENT_SECRET")
WEBHOOK_URL = os.environ.get("ALERT_WEBHOOK_URL")  # Slack / Google Chat / Teams

ALERT_THRESHOLD_MINUTES = float(os.environ.get("ALERT_THRESHOLD_MINUTES", "15"))
KILL_THRESHOLD_MINUTES = float(os.environ.get("KILL_THRESHOLD_MINUTES", "60"))
ENABLE_AUTO_KILL = os.environ.get("ENABLE_AUTO_KILL", "false").lower() == "true"
SCHEDULE_INTERVAL_MINUTES = float(os.environ.get("SCHEDULE_INTERVAL_MINUTES", "5"))

# Protected sources that MUST NEVER be killed by this watchdog
PROTECTED_SOURCES = {
    "regenerator",       # Persistent Derived Table (PDT) builds
    "alerts",            # Looker scheduled alerts
    "scheduled_task",    # Looker scheduled reports & deliveries
    "scheduler",
}


def get_looker_sdk() -> looker_sdk.methods40.Looker40SDK:
  """Initializes the Looker SDK using environment configuration."""
  os.environ["LOOKERSDK_BASE_URL"] = LOOKER_BASE_URL
  os.environ["LOOKERSDK_CLIENT_ID"] = LOOKER_CLIENT_ID
  os.environ["LOOKERSDK_CLIENT_SECRET"] = LOOKER_CLIENT_SECRET
  os.environ["LOOKERSDK_VERIFY_SSL"] = "true"
  return looker_sdk.init40()


def send_notification(message: str) -> None:
  """Sends notification payload to configured webhook."""
  if not WEBHOOK_URL:
    print(f"[DRY RUN NOTICE]: {message}")
    return

  try:
    response = requests.post(WEBHOOK_URL, json={"text": message}, timeout=10)
    response.raise_for_status()
  except Exception as err:
    print(f"Failed to deliver webhook alert: {err}")


@functions_framework.http
def check_running_queries(request: Any) -> Dict[str, Any]:
  """HTTP Cloud Function entrypoint triggered by Cloud Scheduler."""
  sdk = get_looker_sdk()
  now_utc = datetime.datetime.now(datetime.timezone.utc)

  try:
    active_queries = sdk.all_running_queries()
  except Exception as err:
    error_msg = f"❌ [Looker Watchdog] Failed to retrieve running queries: {err}"
    print(error_msg)
    return {"status": "error", "message": error_msg}

  flagged_queries = 0
  killed_queries = 0

  for q in active_queries:
    source = (q.source or "").lower()

    # Safety Guard: Skip non-interactive or protected system operations
    if source in PROTECTED_SOURCES:
      continue

    # Focus exclusively on interactive dashboard / explore queries
    if source not in ["dashboard", "explore"]:
      continue

    if not q.created_at:
      continue

    # Calculate in-flight runtime
    # Format typically: "2026-09-08T18:00:00.000Z"
    clean_timestamp = q.created_at.replace("Z", "+00:00")
    created_dt = datetime.datetime.fromisoformat(clean_timestamp)
    elapsed_minutes = (now_utc - created_dt).total_seconds() / 60.0

    # NOTE: all_running_queries() returns a redacted UserPublic object for
    # `user` -- it has no `email` field, only display_name/first_name/etc.
    user_display_name = q.user.display_name if q.user and q.user.display_name else "Unknown User"
    query_slug = q.slug or "N/A"
    task_id = q.query_task_id or "N/A"

    # 1. Check for Auto-Kill Threshold
    if ENABLE_AUTO_KILL and elapsed_minutes >= KILL_THRESHOLD_MINUTES and task_id:
      try:
        sdk.kill_query(task_id)
        killed_queries += 1
        kill_notice = (
            f"🛑 *Looker Core: Auto-Killed Runaway Query*\n"
            f"• *User:* {user_display_name}\n"
            f"• *Source:* {source.capitalize()}\n"
            f"• *Runtime:* {elapsed_minutes:.1f} minutes (Ceiling: {KILL_THRESHOLD_MINUTES}m)\n"
            f"• *Connection:* {q.connection_name}\n"
            f"• *Query Task ID:* `{task_id}`"
        )
        print(kill_notice)
        send_notification(kill_notice)
        continue
      except Exception as err:
        print(f"Failed to kill query task {task_id}: {err}")

    # 2. Check for Alert Threshold (De-duplicated within schedule window)
    # Alerts only once when entering the [ALERT, ALERT + INTERVAL] window
    if ALERT_THRESHOLD_MINUTES <= elapsed_minutes < (ALERT_THRESHOLD_MINUTES + SCHEDULE_INTERVAL_MINUTES):
      flagged_queries += 1
      alert_msg = (
          f"⚠️ *Looker Core: Long-Running Query Detected*\n"
          f"• *User:* {user_display_name}\n"
          f"• *Source:* {source.capitalize()}\n"
          f"• *Current Runtime:* {elapsed_minutes:.1f} minutes\n"
          f"• *Connection:* {q.connection_name}\n"
          f"• *Query Slug:* `{query_slug}`\n"
          f"• *Action Required:* Review dashboard filters or cancel task from Admin → Queries."
      )
      print(alert_msg)
      send_notification(alert_msg)

  return {
      "status": "success",
      "inspected_queries": len(active_queries),
      "flagged_alerts": flagged_queries,
      "killed_queries": killed_queries,
  }
