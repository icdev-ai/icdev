# CUI // SP-CTI
"""RPKI validator — validates prefixes against ROAs via Cloudflare RPKI API.

Uses stdlib urllib.request only. No external deps.
Cloudflare RPKI endpoint: https://rpki.cloudflare.com/api/v1/validity/{asn}/{prefix}
"""

from __future__ import annotations

import json
import urllib.request
import urllib.error
from typing import Any

_CF_RPKI_BASE = "https://rpki.cloudflare.com/api/v1/validity"
_TIMEOUT = 8


def validate_prefix(prefix: str, origin_asn: int) -> dict[str, Any]:
    """Validate a single prefix/ASN pair against Cloudflare RPKI API.

    Returns:
        {prefix, origin_asn, status, roa_count, validity_message}
        status: "valid" | "invalid" | "not-found" | "unknown"
    """
    url = f"{_CF_RPKI_BASE}/{origin_asn}/{prefix}"
    try:
        req = urllib.request.Request(url, headers={"Accept": "application/json"})
        with urllib.request.urlopen(req, timeout=_TIMEOUT) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        return {
            "prefix": prefix,
            "origin_asn": origin_asn,
            "status": "unknown",
            "roa_count": 0,
            "validity_message": f"HTTP {exc.code}",
            "error": str(exc),
        }
    except Exception as exc:
        return {
            "prefix": prefix,
            "origin_asn": origin_asn,
            "status": "unknown",
            "roa_count": 0,
            "validity_message": str(exc),
            "error": str(exc),
        }

    validity = data.get("validity", {})
    state = validity.get("state", "unknown").lower()
    # Cloudflare returns: "valid", "invalid", "not-found"
    status_map = {"valid": "valid", "invalid": "invalid", "not-found": "not-found"}
    status = status_map.get(state, "unknown")

    return {
        "prefix": prefix,
        "origin_asn": origin_asn,
        "status": status,
        "roa_count": len(validity.get("VRPs", {}).get("matched", [])),
        "validity_message": validity.get("description", ""),
        "roa_found": status == "valid",
    }


def validate_peer_prefixes(prefixes: list[dict]) -> list[dict]:
    """Validate a list of {prefix, origin_asn} dicts.

    Returns list with added 'rpki_status' and 'roa_found' fields.
    """
    results = []
    for p in prefixes:
        result = validate_prefix(p["prefix"], p.get("origin_asn", 0))
        merged = {**p, "rpki_status": result["status"], "roa_found": result.get("roa_found", False)}
        results.append(merged)
    return results


def generate_roa_report(peer_id: str, prefixes: list[dict]) -> dict[str, Any]:
    """Generate an RPKI ROA coverage report for a peer's prefix set.

    Args:
        peer_id: Peer identifier string
        prefixes: List of prefix dicts with rpki_status already populated

    Returns:
        Summary report dict with counts and coverage percentage
    """
    total = len(prefixes)
    if total == 0:
        return {
            "peer_id": peer_id,
            "total": 0,
            "valid": 0,
            "invalid": 0,
            "not_found": 0,
            "unknown": 0,
            "roa_coverage_pct": 0.0,
            "has_invalids": False,
        }

    counts: dict[str, int] = {"valid": 0, "invalid": 0, "not-found": 0, "unknown": 0}
    for p in prefixes:
        status = p.get("rpki_status", "unknown")
        counts[status] = counts.get(status, 0) + 1

    roa_coverage = round(counts["valid"] / total * 100, 1) if total else 0.0
    return {
        "peer_id": peer_id,
        "total": total,
        "valid": counts["valid"],
        "invalid": counts["invalid"],
        "not_found": counts["not-found"],
        "unknown": counts["unknown"],
        "roa_coverage_pct": roa_coverage,
        "has_invalids": counts["invalid"] > 0,
    }


def get_rpki_summary(conn) -> dict[str, Any]:
    """Aggregate RPKI status across all prefixes in DB."""
    try:
        cur = conn.cursor()
        cur.execute(
            "SELECT rpki_status, COUNT(*) as cnt FROM peering_prefixes GROUP BY rpki_status"
        )
        rows = cur.fetchall()
    except Exception:
        return {"valid": 0, "invalid": 0, "not_found": 0, "unknown": 0, "total": 0}

    counts = {"valid": 0, "invalid": 0, "not_found": 0, "unknown": 0}
    total = 0
    for row in rows:
        status = str(row[0] if not hasattr(row, "keys") else row["rpki_status"])
        cnt = int(row[1] if not hasattr(row, "keys") else row["cnt"])
        key = status.replace("-", "_")
        counts[key] = counts.get(key, 0) + cnt
        total += cnt

    counts["total"] = total
    counts["coverage_pct"] = round(counts["valid"] / total * 100, 1) if total else 0.0
    return counts
