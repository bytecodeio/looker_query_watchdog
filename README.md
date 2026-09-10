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
│   └── deploy.sh                  # Scripted gcloud deployment (Secret Manager, Function, Scheduler)
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

### Cost

At a 5-minute polling interval this runs well within GCP's always-free tier (~8,640 invocations/month against a 2,000,000/month free allowance, similarly small compute/Secret Manager usage) -- realistically $0/month. See the deployment discussion in the project history for the full breakdown.

## Known SDK gotchas

- **`LOOKERSDK_*` environment variables silently override `looker.ini`.** If auth keeps failing with stale-looking values even after editing `looker.ini`, check for leftover `LOOKERSDK_BASE_URL`/`LOOKERSDK_CLIENT_ID`/`LOOKERSDK_CLIENT_SECRET` env vars in your shell/kernel -- they take precedence over the ini file. The notebook's SDK-init cell clears these defensively before authenticating.
- **`all_running_queries()` does not return a `dashboard_id` field**, and its nested `user` object (`UserPublic`) has no `email` field -- only `display_name`/`first_name`/`last_name`/etc. Both `main.py` and the notebook use `user.display_name` and omit `dashboard_id` accordingly.
- **`all_running_queries()` only sees queries in-flight at the exact instant it's called** -- Looker's own `runtime` field stays `0`/`null` until a query finishes, which is why elapsed time is computed as `now_utc - created_at` instead.
