#!/usr/bin/env python3
# CUI // SP-CTI
# Controlled by: Department of Defense
# CUI Category: CTI
"""A2A Callback Client — calls parent ICDEV for capabilities not included locally.

This child application (ninjaflow-ai) can request services from its parent ICDEV
instance using the A2A protocol (JSON-RPC 2.0).

Excluded capabilities (must call parent for):
  - Application generation (agentic fitness, blueprint, scaffolding)
  - Application modernization (7R assessment, migration)

Environment variable: ICDEV_PARENT_CALLBACK_URL
"""

import json
import logging
import os
import uuid
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

PARENT_URL = os.environ.get("ICDEV_PARENT_CALLBACK_URL", "")
AUTH_METHOD = "none"

logger = logging.getLogger("ninjaflow-ai.a2a_callback")


def call_parent(method: str, params: dict = None, timeout: int = 30) -> dict:
    """Send JSON-RPC 2.0 request to parent ICDEV.

    Args:
        method: The RPC method name (e.g. "modernization.analyze_legacy").
        params: Optional parameters dict.
        timeout: Request timeout in seconds.

    Returns:
        Response result dict, or error dict on failure.
    """
    if not PARENT_URL:
        return {"error": "ICDEV_PARENT_CALLBACK_URL not configured"}

    request_id = str(uuid.uuid4())
    payload = {
        "jsonrpc": "2.0",
        "id": request_id,
        "method": method,
        "params": params or {},
    }

    headers = {"Content-Type": "application/json"}
    if AUTH_METHOD == "mtls":
        # mTLS handled at transport level; no additional auth header needed
        pass
    elif AUTH_METHOD == "bearer":
        token = os.environ.get("ICDEV_PARENT_AUTH_TOKEN", "")
        if token:
            headers["Authorization"] = f"Bearer {token}"

    try:
        req = Request(
            PARENT_URL,
            data=json.dumps(payload).encode("utf-8"),
            headers=headers,
            method="POST",
        )
        with urlopen(req, timeout=timeout) as resp:
            body = json.loads(resp.read().decode("utf-8"))
            if "error" in body:
                logger.warning("Parent returned error: %s", body["error"])
                return {"error": body["error"]}
            return body.get("result", {})
    except HTTPError as e:
        logger.error("HTTP error calling parent: %s %s", e.code, e.reason)
        return {"error": f"HTTP {e.code}: {e.reason}"}
    except URLError as e:
        logger.error("Connection error calling parent: %s", e.reason)
        return {"error": f"Connection failed: {e.reason}"}
    except Exception as e:
        logger.error("Unexpected error calling parent: %s", e)
        return {"error": str(e)}


def check_health() -> bool:
    """Check if parent ICDEV is reachable."""
    if not PARENT_URL:
        return False
    try:
        health_url = PARENT_URL.rstrip("/").rsplit("/", 1)[0] + "/health"
        req = Request(health_url, method="GET")
        with urlopen(req, timeout=5) as resp:
            return resp.status == 200
    except Exception:
        return False


def list_parent_capabilities() -> list:
    """Query parent for available capabilities."""
    result = call_parent("system.list_methods")
    if "error" in result:
        return []
    return result.get("methods", [])


if __name__ == "__main__":
    import sys
    if "--health" in sys.argv:
        ok = check_health()
        print(f"Parent health: {'ok' if ok else 'unreachable'}")
        sys.exit(0 if ok else 1)
    caps = list_parent_capabilities()
    print(f"Parent capabilities: {len(caps)}")
    for cap in caps:
        print(f"  - {cap}")
