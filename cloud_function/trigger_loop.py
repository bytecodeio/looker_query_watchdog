#!/usr/bin/env python3
"""Repeatedly triggers the deployed Looker Query Watchdog Cloud Function,
printing each response live -- the deployed-function equivalent of the
notebook's "Continuous Test Loop."

Use this while firing test queries at your Looker instance to watch the real
deployed Cloud Function catch them, instead of waiting on Cloud Scheduler's
own cadence.

Usage:
    python trigger_loop.py --interval 5 --duration 6
    python trigger_loop.py --interval 5 --duration 6 --region us-east1

Stop early anytime with Ctrl+C.

Note: the identity token is fetched once at startup and reused for the whole
loop. Google-issued identity tokens are valid for about an hour, so for a
loop longer than that, just re-run the script rather than extending --duration.
"""

import argparse
import sys
import time
from datetime import datetime, timedelta, timezone

from trigger_common import (
    FUNCTION_NAME,
    get_identity_token,
    resolve_function_url,
    trigger_once,
)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--function-name", default=FUNCTION_NAME, help="Deployed Cloud Function name.")
    parser.add_argument("--region", default="us-central1", help="Region the function was deployed to.")
    parser.add_argument("--url", default=None, help="Skip auto-discovery and call this URL directly.")
    parser.add_argument("--interval", type=float, default=5.0, help="Seconds between polls.")
    parser.add_argument("--duration", type=float, default=6.0, help="Total minutes to keep polling.")
    args = parser.parse_args()

    try:
        function_url = resolve_function_url(args.url, args.function_name, args.region)
        print(f"Triggering: {function_url}")
        token = get_identity_token(audience=function_url)
    except Exception as err:
        print(f"Failed to set up the trigger loop: {err}", file=sys.stderr)
        return 1

    end_time = datetime.now(timezone.utc) + timedelta(minutes=args.duration)
    poll_count = 0

    print(f"Polling every {args.interval}s for {args.duration} minute(s). Press Ctrl+C to stop early.")

    try:
        while datetime.now(timezone.utc) < end_time:
            poll_count += 1
            now = datetime.now(timezone.utc)
            try:
                result = trigger_once(function_url, token)
                print(
                    f"--- Poll #{poll_count} @ {now:%H:%M:%S} UTC | "
                    f"inspected={result.get('inspected_queries')} "
                    f"flagged={result.get('flagged_alerts')} "
                    f"killed={result.get('killed_queries')} ---"
                )
            except Exception as err:
                print(f"--- Poll #{poll_count} @ {now:%H:%M:%S} UTC | request failed: {err} ---")

            remaining = (end_time - datetime.now(timezone.utc)).total_seconds()
            if remaining > 0:
                time.sleep(min(args.interval, remaining))
    except KeyboardInterrupt:
        print("\nStopped early by user.")

    print(f"\nDone. {poll_count} poll(s) over {args.duration} minute(s).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
