# CUI // SP-CTI
"""Peering Manager → PMC sync connector.

peering-manager (https://github.com/peering-manager/peering-manager) is the
OSS standard for BGP peering session management. This connector reads ASes
and BGP sessions from a peering-manager REST API and imports them into the
PMC peering_peers table.

Operators running peering-manager can import their data without manual re-entry.
Existing PMC peers (by ASN) are never overwritten — PMC data takes precedence.

Configuration (env):
  PEERING_MGR_BASE_URL  — base URL of the peering-manager instance
  PEERING_MGR_TOKEN     — API token (Settings > API Tokens in peering-manager)
  PEERING_MGR_TIMEOUT   — HTTP timeout seconds (default: 15)
"""
from __future__ import annotations

import json as _json
import os
import urllib.error
import urllib.request
from datetime import datetime, timezone

PEERING_MGR_BASE_URL = os.environ.get("PEERING_MGR_BASE_URL", "")
PEERING_MGR_TOKEN    = os.environ.get("PEERING_MGR_TOKEN", "")
PEERING_MGR_TIMEOUT  = int(os.environ.get("PEERING_MGR_TIMEOUT", "15"))


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _get(path: str) -> dict:
    if not PEERING_MGR_BASE_URL:
        return {"error": "PEERING_MGR_BASE_URL not configured"}
    url = f"{PEERING_MGR_BASE_URL.rstrip('/')}{path}"
    headers = {"Accept": "application/json"}
    if PEERING_MGR_TOKEN:
        headers["Authorization"] = f"Token {PEERING_MGR_TOKEN}"
    try:
        req = urllib.request.Request(url, headers=headers)
        with urllib.request.urlopen(req, timeout=PEERING_MGR_TIMEOUT) as resp:
            return _json.loads(resp.read().decode())
    except urllib.error.HTTPError as exc:
        return {"error": f"HTTP {exc.code}", "url": url}
    except Exception as exc:
        return {"error": str(exc), "url": url}


def fetch_autonomous_systems(limit: int = 500) -> list[dict]:
    """Fetch autonomous systems from the peering-manager API."""
    data = _get(f"/api/peering/autonomous-systems/?limit={limit}&format=json")
    if "error" in data:
        return [data]
    return data.get("results", [])


def fetch_bgp_sessions(limit: int = 1000) -> list[dict]:
    """Fetch direct BGP peering sessions from the peering-manager API."""
    data = _get(f"/api/peering/direct-peering-sessions/?limit={limit}&format=json")
    if "error" in data:
        return [data]
    return data.get("results", [])


def sync_peers_to_pmc(pmc_conn) -> dict:
    """Sync peering-manager autonomous systems into PMC peering_peers.

    - Inserts ASes that don't already exist (keyed on asn)
    - Never overwrites existing PMC peers — PMC operator data takes precedence
    - Returns inserted / skipped / error counts
    """
    ases = fetch_autonomous_systems()
    inserted = 0
    skipped = 0
    errors = 0
    now = _now()

    for asn_rec in ases:
        if "error" in asn_rec:
            errors += 1
            continue
        try:
            asn = int(asn_rec.get("asn", 0))
            if asn == 0:
                errors += 1
                continue

            try:
                existing = pmc_conn.execute(
                    "SELECT id FROM peering_peers WHERE asn=%s", (asn,)
                ).fetchone()
            except Exception:
                existing = pmc_conn.execute(
                    "SELECT id FROM peering_peers WHERE asn=%s", (asn,)
                ).fetchone()

            if existing:
                skipped += 1
                continue

            org_name   = asn_rec.get("name", "") or f"AS{asn}"
            irr_as_set = asn_rec.get("irr_as_set", "") or ""
            ipv4_count = int(asn_rec.get("ipv4_max_prefixes", 0) or 0)
            ipv6_count = int(asn_rec.get("ipv6_max_prefixes", 0) or 0)

            try:
                pmc_conn.execute(
                    """INSERT INTO peering_peers
                       (asn, org_name, peer_type, policy, status,
                        ipv4_prefix_count, ipv6_prefix_count, irr_as_set,
                        peeringdb_sync, classification)
                       VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                    (asn, org_name, "public", "selective", "evaluation",
                     ipv4_count, ipv6_count, irr_as_set, 0, "CUI"),
                )
            except Exception:
                pmc_conn.execute(
                    """INSERT INTO peering_peers
                       (asn, org_name, peer_type, policy, status,
                        ipv4_prefix_count, ipv6_prefix_count, irr_as_set,
                        peeringdb_sync, classification)
                       VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                    (asn, org_name, "public", "selective", "evaluation",
                     ipv4_count, ipv6_count, irr_as_set, 0, "CUI"),
                )
            inserted += 1
        except Exception:
            errors += 1

    if inserted > 0:
        pmc_conn.commit()

    return {
        "source": "peering-manager",
        "ases_fetched": len(ases),
        "peers_inserted": inserted,
        "peers_skipped": skipped,
        "errors": errors,
        "synced_at": now,
    }


def health_check() -> dict:
    """Check if peering-manager is reachable."""
    if not PEERING_MGR_BASE_URL:
        return {"configured": False, "error": "PEERING_MGR_BASE_URL not set"}
    data = _get("/api/")
    if "error" in data:
        return {"configured": True, "reachable": False, "error": data["error"]}
    return {
        "configured": True,
        "reachable": True,
        "url": PEERING_MGR_BASE_URL,
        "checked_at": _now(),
    }


if __name__ == "__main__":
    import sys
    import json
    if "--sync" in sys.argv:
        from tools.pmc_canvas.db.init_db import get_connection
        conn = get_connection()
        print(json.dumps(sync_peers_to_pmc(conn), indent=2))
        conn.close()
    else:
        print(json.dumps(health_check(), indent=2))
