# Looker Query Watchdog

Monitors in-flight Looker Core dashboard/explore queries and alerts (and optionally auto-cancels) ones that run too long, since Looker's own System Activity data lags several minutes and doesn't measure live execution time.

## Project layout

```
.
├── Looker_Query_Watchdog.ipynb   # Interactive dev/test notebook (auth via looker.ini)
├── looker.ini                    # Your real Looker credentials -- gitignored, never committed
├── looker.ini.example             # Template for looker.ini (safe to commit)
├── requirements.txt               # Deps for the local venv (notebook + Jupyter)
├── venv/                          # Local Python virtual environment -- gitignored
├── cloud_function/                # The actual deployable Cloud Function
│   ├── main.py                    # Entry point: check_running_queries
│   ├── requirements.txt           # Deps for the Cloud Function only (no pandas/ipykernel)
│   ├── deploy.sh                  # Scripted gcloud deployment (Secret Manager, Function, Scheduler)
│   ├── trigger_common.py          # Shared auth/HTTP helpers for the two trigger scripts below
│   ├── trigger_once.py            # Fires one authenticated request at the deployed function
│   ├── trigger_loop.py            # Repeatedly triggers it -- deployed-function version of the notebook's test loop
│   └── view_alerts.sh             # Convenience wrapper to read the function's recent Cloud Logging output
├── .vscode/                       # Run/Debug configs so the trigger scripts launch with one click
├── .gitignore
└── .gcloudignore                  # Safety net if `gcloud deploy` is ever run from this top-level folder
```

`watchdog.txt` (the original design doc this project is built from) stays on disk locally but is gitignored — it's a planning reference, not part of the shipped project.

## Local setup (notebook)

```bash
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
python -m ipykernel install --user --name=looker-watchdog --display-name="Python (looker-watchdog)"
```

Copy the credentials template and fill in your real Looker instance details:

```bash
cp looker.ini.example looker.ini
```

Edit `looker.ini`:

```ini
[Looker]
base_url=https://YOUR_INSTANCE.looker.com
client_id=YOUR_CLIENT_ID
client_secret=YOUR_CLIENT_SECRET
verify_ssl=True
```

Looker Core uses standard HTTPS on port 443 -- do not append `:19999` (that's only for legacy Looker Original), and do not add a trailing slash to `base_url`.

Open `Looker_Query_Watchdog.ipynb`, select the **Python (looker-watchdog)** kernel, and run the cells top to bottom. The "Continuous Test Loop" section near the bottom polls repeatedly for a configurable duration -- useful for watching it catch queries live during a manual or scale test, instead of re-running cells by hand.

## Looker prerequisites

Create a dedicated Looker service account (Admin → Users) with a role granting, at minimum:

- `see_queries` (system-wide scope, not just the account's own activity)
- `see_users`
- `cancel_queries` (only needed if you plan to enable auto-kill)

Generate an API key (Client ID/Secret) for that account under its user profile.

## Deploying to production

The notebook is for interactive testing only -- it doesn't run unless you have it open and click "run." Real 24/7 monitoring requires the Cloud Function + Cloud Scheduler pattern in `cloud_function/`, which polls on a fixed schedule (e.g. every 5 minutes) independent of any open notebook or laptop.

```bash
cd cloud_function
./deploy.sh
```

Edit the variables at the top of `deploy.sh` first (region, `LOOKER_BASE_URL`, `LOOKER_CLIENT_ID`, webhook URL, thresholds). It will prompt for the Looker client secret interactively rather than storing it in the script, enable the required GCP APIs, create/update the Secret Manager secret, deploy the function, and set up the Cloud Scheduler trigger with a dedicated invoker service account.

**Always deploy with `ENABLE_AUTO_KILL=false` first.** Run in alert-only mode for 1-2 weeks to validate thresholds against real traffic before enabling automatic query cancellation.

Logs: `gcloud functions logs read looker-query-watchdog --region=<region> --gen2`

### Testing the deployed function from VS Code

Once `./deploy.sh` finishes, you don't have to wait up to 5 minutes for Cloud Scheduler's next tick to see if it worked. Two scripts call the real deployed function directly (authenticated the same way Cloud Scheduler is, via a Google-issued identity token) and can be launched either from VS Code's Run panel (`.vscode/launch.json` already has both configured) or the integrated terminal:

```bash
cd cloud_function
python trigger_once.py            # one check, prints the JSON response
python trigger_loop.py --interval 5 --duration 6   # polls every 5s for 6 minutes, Ctrl+C to stop early
```

Both auto-discover the deployed URL via `gcloud functions describe` (override with `--url` if you already know it, or `--region`/`--function-name` if you changed those in `deploy.sh`). This requires the same `gcloud auth login` session `deploy.sh` already needs -- no separate setup. Use `trigger_loop.py` the same way the notebook's Continuous Test Loop was used: fire test queries at your Looker instance and watch it catch them live.

### Pausing, resuming, and changing the polling schedule

Pause it without deleting anything (e.g. between test sessions):

```bash
gcloud scheduler jobs pause looker-watchdog-trigger --location=us-central1
gcloud scheduler jobs resume looker-watchdog-trigger --location=us-central1   # when ready again
```

To change how often it polls (e.g. every 2 or 15 minutes instead of 5), edit **both** related values together in `deploy.sh` and re-run it -- don't edit just the Cloud Scheduler job's cron by itself, since `SCHEDULE_INTERVAL_MINUTES` on the function has to match the real polling cadence for the alert de-duplication window to work correctly:

```bash
SCHEDULE_INTERVAL_MINUTES="2"     # or "15"
SCHEDULE_CRON="*/2 * * * *"       # or "*/15 * * * *" -- must match the line above
```

Then:

```bash
./deploy.sh
```

This updates the existing function and scheduler job in place (no duplicates) and re-prompts for the Looker client secret (safe to re-enter the same one -- Secret Manager just adds a new version).

### Seeing alerts: default (dry-run/logs) vs. Slack

**Default, and the easiest option if you don't have Slack admin access:** `ALERT_WEBHOOK_URL` in `deploy.sh` defaults to blank (`""`). With no webhook configured, the function logs every alert/kill notice instead of posting it anywhere -- no external service, no special permissions beyond your own GCP project needed. View them with:

```bash
cd cloud_function
./view_alerts.sh          # last 50 log entries
./view_alerts.sh 200      # or pass a number for more
```

or directly: `gcloud functions logs read looker-query-watchdog --region=us-central1 --gen2`, or in the Console under **Logging → Logs Explorer** filtered to `resource.type="cloud_run_revision"` and `resource.labels.service_name="looker-query-watchdog"`.

**Optional: also post to Slack (or Teams/Google Chat) as a real-time DM/channel message.** Requires creating a Slack app (not necessarily workspace admin rights, but does require permission to install an app -- check with whoever manages your workspace if `Create New App` isn't available to you):

1. Go to **https://api.slack.com/apps** → **Create New App** → **From scratch**. Name it, pick your workspace.
2. Left sidebar → **Incoming Webhooks** → toggle **Activate Incoming Webhooks** on.
3. **Add New Webhook to Workspace** → in the destination picker, search for **your own name** to have it DM you directly (if your workspace doesn't allow that, pick/create a private channel with just yourself instead) → **Allow**.
4. Copy the generated URL (`https://hooks.slack.com/services/T000/B000/XXXXXXXX`).
5. Paste it into `ALERT_WEBHOOK_URL` in `deploy.sh`, then `./deploy.sh` again.

**A webhook is bound to whatever destination you picked in step 3 -- there's no separate "send to me" setting elsewhere.** Google Chat and Microsoft Teams incoming webhooks work the same way (channel/space-bound, same `{"text": "..."}` JSON payload) if you'd rather use one of those.

To test either mode without waiting for a genuinely long-running query: temporarily set `ALERT_THRESHOLD_MINUTES="0.05"` in `deploy.sh`, redeploy, run `trigger_loop.py` while firing a query at Looker (see below), then set it back to `"15"` and redeploy once more.

### Handing this off to Verizon's environment

Verizon's GCP environment runs a different Python version/tooling than what's been validated here. Before deploying there:

1. Check what Cloud Functions runtimes are actually available in their project: `gcloud functions runtimes list --region=<their-region>`.
2. Update `RUNTIME` at the top of `deploy.sh` to match (it defaults to `python311`, validated in this project's own GCP environment).
3. Re-run through this same deploy → `trigger_once.py` → `trigger_loop.py` sequence in their project before considering it live, since a different runtime can surface issues this environment didn't.

### Cost

At a 5-minute polling interval this runs well within GCP's always-free tier (~8,640 invocations/month against a 2,000,000/month free allowance, similarly small compute/Secret Manager usage) -- realistically $0/month. See the deployment discussion in the project history for the full breakdown.

## Known SDK gotchas

- **`LOOKERSDK_*` environment variables silently override `looker.ini`.** If auth keeps failing with stale-looking values even after editing `looker.ini`, check for leftover `LOOKERSDK_BASE_URL`/`LOOKERSDK_CLIENT_ID`/`LOOKERSDK_CLIENT_SECRET` env vars in your shell/kernel -- they take precedence over the ini file. The notebook's SDK-init cell clears these defensively before authenticating.
- **`all_running_queries()` does not return a `dashboard_id` field**, and its nested `user` object (`UserPublic`) has no `email` field -- only `display_name`/`first_name`/`last_name`/etc. Both `main.py` and the notebook use `user.display_name` and omit `dashboard_id` accordingly.
- **`all_running_queries()` only sees queries in-flight at the exact instant it's called** -- Looker's own `runtime` field stays `0`/`null` until a query finishes, which is why elapsed time is computed as `now_utc - created_at` instead.
- **Check truthiness before defaulting a field to a placeholder string.** `main.py` originally did `task_id = q.query_task_id or "N/A"` and then `... and task_id`, which is always true once defaulted (a non-empty string), silently defeating the intended "skip if no task ID" guard. Fixed by checking the raw value before substituting the display placeholder.
- **A non-empty but fake `ALERT_WEBHOOK_URL` fails silently, not loudly.** The code only enters dry-run/log mode when the URL is truly blank (`""`); a leftover placeholder string is truthy, so it attempts a real POST, fails, and only logs `Failed to deliver webhook alert: ...` -- easy to miss and easy to mistake for "alerts are being sent." `deploy.sh` now defaults `ALERT_WEBHOOK_URL` to `""` for exactly this reason.
- **`gcloud scheduler jobs` commands need an explicit `--location`.** Without one, `gcloud` silently falls back to the project's App Engine app location (with a warning) -- which doesn't exist at all in projects with no App Engine app, turning a warning into a hard failure. `deploy.sh` passes `--location="${REGION}"` explicitly to every scheduler command to avoid depending on that.
