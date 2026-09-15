#!/usr/bin/env python3
"""Fires a single authenticated request at the deployed Looker Query Watchdog
Cloud Function and prints its response.

Run this right after `./deploy.sh` to confirm the deployed function actually
works end-to-end, without waiting on Cloud Scheduler's next tick.

Usage:
    python trigger_once.py
    python trigger_once.py --region us-east1
    python trigger_once.py --url https://looker-query-watchdog-abc123-uc.a.run.app
"""

import argparse
import sys

from trigger_common import (
    FUNCTION_NAME,
    get_identity_token,
    pretty_print,
    resolve_function_url,
    trigger_once,
)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--function-name", default=FUNCTION_NAME, help="Deployed Cloud Function name.")
    parser.add_argument("--region", default="us-central1", help="Region the function was deployed to.")
    parser.add_argument("--url", default=None, help="Skip auto-discovery and call this URL directly.")
    args = parser.parse_args()

    try:
        function_url = resolve_function_url(args.url, args.function_name, args.region)
        print(f"Triggering: {function_url}")
        token = get_identity_token(audience=function_url)
        result = trigger_once(function_url, token)
    except Exception as err:
        print(f"Failed to trigger the watchdog: {err}", file=sys.stderr)
        return 1

    pretty_print(result)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
