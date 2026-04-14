"""
Shared helper for calling secret-gated Convex bridge actions.
"""
import os
from typing import Any

import requests

from config import Config

CONVEX_CALL_TIMEOUT_SECONDS = float(os.environ.get("CONVEX_CALL_TIMEOUT_SECONDS", "8"))


def call_bridge_action(name: str, args: dict[str, Any]) -> Any:
    secret = Config.CONVEX_BRIDGE_SECRET
    if not secret:
        raise RuntimeError("CONVEX_BRIDGE_SECRET or CHAT_INTERNAL_SECRET is not configured")

    payload = {
        "path": f"backendBridge:{name}",
        "args": {
            "secret": secret,
            **args,
        },
        "format": "json",
    }

    response = requests.post(
        f"{Config.CONVEX_URL.rstrip('/')}/api/action",
        json=payload,
        timeout=CONVEX_CALL_TIMEOUT_SECONDS,
    )
    response.raise_for_status()
    body = response.json()
    if body.get("status") != "success":
        raise RuntimeError(body.get("errorMessage", f"Convex bridge action failed: {name}"))
    return body.get("value")
