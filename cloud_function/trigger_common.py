"""Shared helpers for triggering the deployed looker-query-watchdog Cloud Function.

Both trigger_once.py and trigger_loop.py call this deployed function directly
over HTTPS -- the same artifact `deploy.sh` deploys, not a local copy -- so
testing here proves the exact thing that will run in production.

The function is deployed with --no-allow-unauthenticated, so every call needs
a Google-issued OIDC identity token. This shells out to the `gcloud` CLI
(the same tool deploy.sh already requires) rather than adding a new Python
auth dependency -- if you can run deploy.sh, you already have what these
scripts need.
"""

import json
import subprocess
from typing import Any, Dict, Optional

import requests

FUNCTION_NAME = "looker-query-watchdog"


def _run_gcloud(args: list) -> str:
    result = subprocess.run(
        ["gcloud", *args],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        raise RuntimeError(
            f"gcloud {' '.join(args)} failed:\n{result.stderr.strip()}"
        )
    return result.stdout.strip()


def get_function_url(function_name: str = FUNCTION_NAME, region: str = "us-central1") -> str:
    """Looks up the deployed function's HTTPS URL via gcloud."""
    return _run_gcloud([
        "functions", "describe", function_name,
        "--region", region,
        "--format", "value(serviceConfig.uri)",
    ])


def get_identity_token(audience: str) -> str:
    """Gets a Google-issued OIDC identity token scoped to the function's URL."""
    return _run_gcloud([
        "auth", "print-identity-token",
        "--audiences", audience,
    ])


def trigger_once(function_url: str, token: str, timeout: int = 60) -> Dict[str, Any]:
    """Sends one authenticated POST to the deployed function, returns the parsed JSON."""
    response = requests.post(
        function_url,
        headers={"Authorization": f"Bearer {token}"},
        timeout=timeout,
    )
    response.raise_for_status()
    try:
        return response.json()
    except ValueError:
        return {"raw_response": response.text}


def resolve_function_url(explicit_url: Optional[str], function_name: str, region: str) -> str:
    """Returns explicit_url if given, otherwise auto-discovers it via gcloud."""
    if explicit_url:
        return explicit_url
    print(f"Looking up deployed URL for '{function_name}' in region '{region}'...")
    return get_function_url(function_name, region)


def pretty_print(result: Dict[str, Any]) -> None:
    print(json.dumps(result, indent=2))
