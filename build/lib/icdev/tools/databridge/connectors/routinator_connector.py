# CUI // SP-CTI
"""Routinator RPKI validator connector.

Routinator (https://routinator.nlnetlabs.nl) is an open-source RPKI validator
with a REST API for prefix validation and an RTR server for router integration.

This connector provides on-premises RPKI validation as an alternative to the
Cloudflare external RPKI API used in tools/pmc_canvas/rpki_validator.py.
Prefer this connector for IL4/IL5 air-gap deployments.

Configuration (env):
  ROUTINATOR_BASE_URL  — base URL (default: http://localhost:9556)
  ROUTINATOR_TIMEOUT   — HTTP timeout seconds (default: 10)
"""
from __future__ import annotations

import ipaddress
import json as _json
import os
import urllib.error
import urllib.request
from datetime import datetime, timezone

ROUTINATOR_BASE_URL = os.environ.get("ROUTINATOR_BASE_URL", "http://localhost:9556")
ROUTINATOR_TIMEOUT  = int(os.environ.get("ROUTINATOR_TIMEOUT", "10"))


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _get(path: str) -> dict:
    url = f"{ROUTINATOR_BASE_URL.rstrip('/')}{path}"
    try:
        req = urllib.request.Request(url, headers={"Accept": "application/json"})
        with urllib.request.urlopen(req, timeout=ROUTINATOR_TIMEOUT) as resp:
            return _json.loads(resp.read().decode())
    except urllib.error.HTTPError as exc:
        return {"error": f"HTTP {exc.code}: {exc.reason}", "url": url}
    except Exception as exc:
        return {"error": str(exc), "url": url}


def validate_prefix(prefix: str, origin_asn: int) -> dict:
    """Validate a prefix + origin ASN pair against the routinator RPKI cache.

    Routinator API: GET /api/v1/validity/AS{asn}/{prefix}
    Returns normalised dict with rpki_status: valid | invalid | not-found | unknown
    """
    try:
        ipaddress.ip_network(prefix, strict=False)
    except ValueError:
        return {
            "prefix": prefix, "asn": origin_asn,
            "rpki_status": "unknown", "error": f"Invalid prefix: {prefix}",
            "source": "routinator", "checked_at": _now(),
        }

    data = _get(f"/api/v1/validity/AS{origin_asn}/{prefix}")
    if "error" in data:
        return {
            "prefix": prefix, "asn": origin_asn,
            "rpki_status": "unknown", "error": data["error"],
            "source": "routinator", "checked_at": _now(),
        }

    validity = data.get("validated_route", {}).get("validity", {})
    state = validity.get("state", "unknown").lower()
    rpki_status = {"valid": "valid", "invalid": "invalid", "not-found": "not-found"}.get(
        state, "unknown"
    )
    return {
        "prefix": prefix,
        "asn": origin_asn,
        "rpki_status": rpki_status,
        "validity_state": state,
        "vrps_matched": len(validity.get("VRPs", {}).get("matched", [])),
        "vrps_unmatched": len(validity.get("VRPs", {}).get("unmatched", [])),
        "source": "routinator",
        "checked_at": _now(),
    }


def validate_peer_prefixes(prefixes: list[dict]) -> list[dict]:
    """Validate a list of {prefix, asn} dicts. Returns enriched results."""
    results = []
    for item in prefixes:
        result = validate_prefix(item.get("prefix", ""), int(item.get("asn", 0)))
        result.update({k: v for k, v in item.items() if k not in result})
        results.append(result)
    return results


def get_vrp_counts() -> dict:
    """Return total VRP (Validated ROA Payload) counts from routinator status."""
    data = _get("/api/v1/status")
    if "error" in data:
        return {"error": data["error"], "source": "routinator"}
    return {
        "total_vrps": data.get("vrps", 0),
        "last_update": data.get("lastUpdateStart", ""),
        "serial": data.get("serial", 0),
        "version": data.get("version", ""),
        "source": "routinator",
        "checked_at": _now(),
    }


def health_check() -> dict:
    """Check if routinator is reachable and has data."""
    status = _get("/api/v1/status")
    if "error" in status:
        return {"reachable": False, "error": status["error"], "url": ROUTINATOR_BASE_URL}
    return {
        "reachable": True,
        "url": ROUTINATOR_BASE_URL,
        "vrps": status.get("vrps", 0),
        "serial": status.get("serial", 0),
        "last_update": status.get("lastUpdateStart", ""),
        "checked_at": _now(),
    }


if __name__ == "__main__":
    import sys
    import json
    if "--validate" in sys.argv:
        idx = sys.argv.index("--validate")
        prefix = sys.argv[idx + 1]
        asn = int(sys.argv[idx + 2])
        print(json.dumps(validate_prefix(prefix, asn), indent=2))
    elif "--vrps" in sys.argv:
        print(json.dumps(get_vrp_counts(), indent=2))
    else:
        print(json.dumps(health_check(), indent=2))
