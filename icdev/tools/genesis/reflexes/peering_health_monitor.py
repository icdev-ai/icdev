# CUI // SP-CTI
"""Genesis Reflex — Peering Health Monitor (6h cadence).

Re-syncs BGP peers whose PeeringDB data is stale (>7 days old) and
re-validates RPKI for high-traffic peers (traffic_ratio > 0.5).
Publishes canvas events for peers with newly discovered RPKI-invalid
prefixes or significant policy changes.

Air-gap safe: uses stdlib urllib for PeeringDB and Cloudflare RPKI APIs.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Dict

logger = logging.getLogger(__name__)

CADENCE_HOURS = 6

_STALE_THRESHOLD_DAYS = 7
_HIGH_TRAFFIC_RATIO_THRESHOLD = 0.5


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _days_since(iso_str: str) -> int:
    if not iso_str:
        return 999
    try:
        dt = datetime.fromisoformat(iso_str.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return (datetime.now(timezone.utc) - dt).days
    except Exception:
        return 999


def _try_exec(conn, sql_pg: str, sql_sq: str, params: tuple = ()) -> Any:
    try:
        return conn.execute(sql_pg, params)
    except Exception:
        return conn.execute(sql_sq, params)


def run(ctx: Dict[str, Any], conn=None) -> Dict[str, Any]:
    """Re-sync stale peers and re-validate RPKI for high-traffic peers.

    Returns:
        peers_checked: int
        stale_synced: int
        rpki_revalidated: int
        rpki_violations_found: int
        events_published: int
        errors: list[str]
    """
    dry_run = ctx.get("dry_run", False)
    result: Dict[str, Any] = {
        "cadence_hours": CADENCE_HOURS,
        "peers_checked": 0,
        "stale_synced": 0,
        "rpki_revalidated": 0,
        "rpki_violations_found": 0,
        "events_published": 0,
        "errors": [],
        "status": "ok",
    }

    try:
        from tools.pmc_canvas.db.init_db import get_connection as pmc_conn
        db = pmc_conn()
        try:
            _monitor_peers(db, dry_run, result)
        finally:
            db.close()
    except Exception as exc:
        logger.error("peering_health_monitor reflex error: %s", exc)
        result["status"] = "error"
        result["errors"].append(str(exc))

    return result


def _monitor_peers(conn, dry_run: bool, result: Dict[str, Any]) -> None:
    # Fetch all active peers
    try:
        rows = _try_exec(
            conn,
            "SELECT id, asn, org_name, peeringdb_sync, traffic_ratio, status "
            "FROM peering_peers WHERE status = %s",
            "SELECT id, asn, org_name, peeringdb_sync, traffic_ratio, status "
            "FROM peering_peers WHERE status = ?",
            ("active",),
        ).fetchall()
    except Exception as exc:
        result["errors"].append(f"peer_fetch: {exc}")
        return

    result["peers_checked"] = len(rows)

    for row in rows:
        if hasattr(row, "keys"):
            peer_id = row["id"]
            asn = row["asn"]
            org = row["org_name"]
            last_sync = row["peeringdb_sync"]
            traffic_ratio = float(row["traffic_ratio"] or 0)
        else:
            peer_id, asn, org, last_sync, traffic_ratio = row[0], row[1], row[2], row[3], float(row[4] or 0)

        # Re-sync stale PeeringDB data
        stale_days = _days_since(str(last_sync) if last_sync else "")
        if stale_days >= _STALE_THRESHOLD_DAYS:
            if not dry_run:
                _sync_peer_peeringdb(conn, peer_id, asn, org, result)
            result["stale_synced"] += 1

        # Re-validate RPKI for high-traffic peers
        if traffic_ratio >= _HIGH_TRAFFIC_RATIO_THRESHOLD:
            if not dry_run:
                violations = _revalidate_rpki(conn, peer_id, asn, org, result)
                if violations:
                    result["rpki_violations_found"] += violations
                    _publish_event(result, "pmc.rpki.violations_found", {
                        "peer_id": peer_id,
                        "asn": asn,
                        "org_name": org,
                        "violation_count": violations,
                        "traffic_ratio": traffic_ratio,
                    })
            result["rpki_revalidated"] += 1


def _sync_peer_peeringdb(conn, peer_id: int, asn: int, org: str, result: Dict[str, Any]) -> None:
    try:
        from tools.pmc_canvas.peeringdb_client import sync_peer_from_peeringdb
        sync_peer_from_peeringdb(asn, conn)
        result.setdefault("_synced_asns", []).append(asn)
    except Exception as exc:
        result["errors"].append(f"peeringdb_sync(AS{asn}): {exc}")


def _revalidate_rpki(conn, peer_id: int, asn: int, org: str, result: Dict[str, Any]) -> int:
    """Re-validate all prefixes for a peer. Return count of new RPKI violations."""
    try:
        # Fetch peer prefixes
        prefixes = _try_exec(
            conn,
            "SELECT id, prefix, origin_asn FROM peering_prefixes WHERE peer_id = %s",
            "SELECT id, prefix, origin_asn FROM peering_prefixes WHERE peer_id = ?",
            (peer_id,),
        ).fetchall()
    except Exception as exc:
        result["errors"].append(f"prefix_fetch(AS{asn}): {exc}")
        return 0

    if not prefixes:
        return 0

    try:
        from tools.pmc_canvas.rpki_validator import validate_prefix
    except Exception:
        result["errors"].append(f"rpki_validator import failed for AS{asn}")
        return 0

    violations = 0
    for prefix_row in prefixes:
        if hasattr(prefix_row, "keys"):
            prefix_id = prefix_row["id"]
            prefix = prefix_row["prefix"]
            origin_asn = prefix_row["origin_asn"]
        else:
            prefix_id, prefix, origin_asn = prefix_row[0], prefix_row[1], prefix_row[2]

        try:
            val = validate_prefix(prefix, origin_asn or asn)
            new_status = val.get("status", "unknown")

            # Update the prefix record
            _try_exec(
                conn,
                "UPDATE peering_prefixes SET rpki_status = %s, roa_found = %s WHERE id = %s",
                "UPDATE peering_prefixes SET rpki_status = ?, roa_found = ? WHERE id = ?",
                (new_status, 1 if val.get("roa_found") else 0, prefix_id),
            )
            try:
                conn.commit()
            except Exception:
                pass

            if new_status == "invalid":
                violations += 1
        except Exception as exc:
            result["errors"].append(f"rpki_validate({prefix}): {exc}")

    return violations


def _publish_event(result: Dict[str, Any], event: str, payload: Dict[str, Any]) -> None:
    try:
        from tools.canvas.event_bus import publish
        publish("pmc", event, payload)
        result["events_published"] += 1
    except Exception as exc:
        result["errors"].append(f"event_bus({event}): {exc}")


if __name__ == "__main__":
    import json as _json
    print(_json.dumps(run({"dry_run": True}), indent=2))
