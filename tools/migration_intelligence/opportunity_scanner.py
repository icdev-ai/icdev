#!/usr/bin/env python3
# CUI // SP-CTI
"""Migration Intelligence — Opportunity Scanner.

Cross-canvas discovery: NDC topology, hardware EOL, MDC designs, SDC gaps.
Air-gap safe — reads only local SQLite databases.
"""

from __future__ import annotations

import json
import os
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

BASE_DIR = Path(__file__).resolve().parent.parent.parent
_AIRGAP = os.environ.get("ICDEV_AIRGAP", "").lower() in ("true", "1", "yes")

_MI_DB = BASE_DIR / "data" / "migration_intel.db"
_NC_DB = BASE_DIR / "data" / "network_canvas.db"
_MC_DB = BASE_DIR / "data" / "migration_canvas.db"
_IC_DB = BASE_DIR / "data" / "icdev.db"


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _get_mi_conn(db_path: str | None = None):
    from tools.migration_intelligence.db.init_db import get_connection
    return get_connection(db_path)


def _get_nc_conn():
    """Cross-canvas read connection to the Network Canvas store.

    cnr-mi-01: routed through the NC canvas connection helper (StorageConnection,
    RLS disabled) instead of a raw sqlite3.connect, so %s placeholders translate
    per backend and PG-primary reads hit the shared icdev db. Returns None if the
    canvas store is unavailable.
    """
    try:
        from tools.network.db.init_db import get_connection as _nc_get_connection
        return _nc_get_connection()
    except Exception:
        return None


def _get_mc_conn():
    """Cross-canvas read connection to the Migration Canvas store.

    cnr-mi-01: routed through the MC canvas connection helper (StorageConnection,
    RLS disabled) instead of a raw sqlite3.connect. Returns None if unavailable.
    """
    try:
        from tools.migration_canvas.db.init_db import get_connection as _mc_get_connection
        return _mc_get_connection()
    except Exception:
        return None


def _get_ic_conn():
    """Main icdev.db — read-only for scanning."""
    try:
        from tools.db.storage import get_connection
        return get_connection()
    except Exception:
        return None


# =========================================================================
# HARDWARE EOL SCANNER (NDC)
# =========================================================================

def scan_hardware_eol(horizon_days: int = 730, db_path: str | None = None) -> dict[str, Any]:
    """Scan nc_hardware_profiles for EOL assets within horizon_days."""
    nc = _get_nc_conn()
    if not nc:
        return {"opportunities": [], "count": 0, "error": "network_canvas.db not found"}

    from datetime import timedelta
    cutoff = (datetime.now(timezone.utc) + timedelta(days=horizon_days)).strftime("%Y-%m-%d")
    results = {"opportunities": [], "count": 0, "canvas": "ndc"}

    try:
        rows = nc.execute(
            """SELECT vendor, model, product_family, eol_date, eos_date,
                      ports_json, throughput_gbps, form_factor
               FROM nc_hardware_profiles
               WHERE (eol_date IS NOT NULL AND eol_date <= %s)
                  OR (eos_date IS NOT NULL AND eos_date <= %s)
               ORDER BY eol_date ASC""",
            (cutoff, cutoff),
        ).fetchall()
    except Exception as exc:
        return {"opportunities": [], "count": 0, "error": str(exc)}
    finally:
        nc.close()

    mi = _get_mi_conn(db_path)
    created = 0
    try:
        for r in rows:
            eol = r["eol_date"] or r["eos_date"]
            days_left = _days_until(eol)
            urgency = _eol_urgency_score(days_left)

            opp_id = f"opp-eol-{uuid.uuid4().hex[:8]}"
            title = f"EOL: {r['vendor']} {r['model']} — replace before {eol}"
            desc = (
                f"{r['vendor']} {r['model']} ({r['product_family'] or 'N/A'}) "
                f"reaches end-of-life on {eol}. "
                f"Estimated {days_left} days remaining."
            )

            _upsert_opportunity(mi, {
                "id": opp_id,
                "title": title,
                "description": desc,
                "opportunity_type": "eol_replacement",
                "source_canvas": "ndc",
                "source_entity_name": f"{r['vendor']} {r['model']}",
                "current_platform": r["model"],
                "eol_date": eol,
                "eol_urgency_score": urgency,
                "composite_score": urgency * 0.5,  # will be re-scored later
                "priority": _urgency_priority(days_left),
                "status": "identified",
            })
            results["opportunities"].append({"id": opp_id, "title": title, "days_left": days_left})
            created += 1
    finally:
        mi.close()

    results["count"] = created
    return results


# =========================================================================
# NETWORK TOPOLOGY SCANNER (NDC)
# =========================================================================

def scan_network_topology(db_path: str | None = None) -> dict[str, Any]:
    """Scan NDC topology nodes for consolidation and redesign opportunities."""
    nc = _get_nc_conn()
    if not nc:
        return {"opportunities": [], "count": 0, "error": "network_canvas.db not found"}

    results = {"opportunities": [], "count": 0, "canvas": "ndc"}
    try:
        # Schema: tools/db/migrations/020_nc_topologies_schema.py (id, name, description, created_at, updated_at)
        topologies = nc.execute(
            "SELECT id, name FROM nc_topologies ORDER BY updated_at DESC LIMIT 20"
        ).fetchall()
    except Exception:
        topologies = []

    try:
        for t in topologies:
            try:
                node_count = nc.execute(
                    "SELECT COUNT(*) as c FROM nc_nodes WHERE topology_id = %s", (t["id"],)
                ).fetchone()["c"]
                if node_count > 50:
                    # Large topology → network redesign opportunity
                    mi = _get_mi_conn(db_path)
                    try:
                        opp_id = f"opp-topo-{uuid.uuid4().hex[:8]}"
                        _upsert_opportunity(mi, {
                            "id": opp_id,
                            "title": f"Network Redesign: {t['name']} ({node_count} nodes)",
                            "description": (
                                f"Topology '{t['name']}' contains {node_count} nodes — "
                                "large topology is a candidate for segmentation or SD-WAN overlay."
                            ),
                            "opportunity_type": "network_redesign",
                            "source_canvas": "ndc",
                            "source_entity_id": t["id"],
                            "source_entity_name": t["name"],
                            "business_value_score": 0.4,
                            "composite_score": 0.3,
                            "priority": "medium",
                            "status": "identified",
                        })
                        results["opportunities"].append({"id": opp_id, "topology": t["name"], "nodes": node_count})
                        results["count"] += 1
                    finally:
                        mi.close()
            except Exception:
                continue
    finally:
        nc.close()

    return results


# =========================================================================
# MIGRATION CANVAS SCANNER (MDC)
# =========================================================================

def scan_migration_designs(db_path: str | None = None) -> dict[str, Any]:
    """Scan MDC migration designs for stalled or unscored opportunities."""
    mc = _get_mc_conn()
    if not mc:
        return {"opportunities": [], "count": 0, "error": "migration_canvas.db not found"}

    results = {"opportunities": [], "count": 0, "canvas": "mdc"}
    try:
        rows = mc.execute(
            """SELECT id, name, description, migration_type, classification, created_at
               FROM migration_designs
               WHERE status IN ('draft','in_progress')
               ORDER BY created_at DESC LIMIT 50"""
        ).fetchall()
    except Exception as exc:
        mc.close()
        return {"opportunities": [], "count": 0, "error": str(exc)}
    finally:
        mc.close()

    mi = _get_mi_conn(db_path)
    try:
        for r in rows:
            mtype = r["migration_type"] or "technology_refresh"
            opp_type = _map_migration_type(mtype)
            opp_id = f"opp-mdc-{uuid.uuid4().hex[:8]}"
            _upsert_opportunity(mi, {
                "id": opp_id,
                "title": f"In-Progress Migration: {r['name']}",
                "description": r["description"] or f"Migration design '{r['name']}' in MDC needs strategy and roadmap.",
                "opportunity_type": opp_type,
                "source_canvas": "mdc",
                "source_entity_id": r["id"],
                "source_entity_name": r["name"],
                "business_value_score": 0.5,
                "composite_score": 0.4,
                "priority": "medium",
                "status": "identified",
            })
            results["opportunities"].append({"id": opp_id, "design": r["name"]})
            results["count"] += 1
    finally:
        mi.close()

    return results


# =========================================================================
# ACTIVE NETWORK MIGRATIONS SCANNER (NMCE → MI bridge)
# =========================================================================

def scan_active_network_migrations(db_path: str | None = None) -> dict[str, Any]:
    """Scan mc_net_sessions for active network migrations and surface as MI opportunities.

    Bridges the Network Migration Canvas (NMCE) into the Migration Intelligence
    pipeline so that active device-level migrations appear as trackable opportunities
    alongside canvas-level migration designs.
    """
    mc = _get_mc_conn()
    if not mc:
        return {"opportunities": [], "count": 0, "error": "migration_canvas.db not found"}

    results: dict[str, Any] = {"opportunities": [], "count": 0, "canvas": "nmce"}
    try:
        rows = mc.execute(
            """SELECT id, src_model, tgt_model, src_device_name, status,
                      config_parsed, created_at, updated_at
               FROM mc_net_sessions
               WHERE status IN ('draft', 'in_progress')
               ORDER BY updated_at DESC LIMIT 100"""
        ).fetchall()
    except Exception as exc:
        mc.close()
        return {"opportunities": [], "count": 0, "error": str(exc)}
    finally:
        mc.close()

    mi = _get_mi_conn(db_path)
    try:
        for r in rows:
            src = r["src_model"] or "unknown device"
            tgt = r["tgt_model"] or "TBD"
            device_name = r["src_device_name"] or src
            opp_id = f"opp-nmce-{uuid.uuid4().hex[:8]}"
            title = f"Network Migration: {src} → {tgt} [{r['status']}]"
            desc = (
                f"Active network migration session {r['id']} — "
                f"{device_name} ({src}) to {tgt}. "
                f"Config parsed: {'yes' if r['config_parsed'] else 'no'}. "
                f"Started: {r['created_at']}."
            )
            _upsert_opportunity(mi, {
                "id": opp_id,
                "title": title,
                "description": desc,
                "opportunity_type": "technology_refresh",
                "source_canvas": "nmce",
                "source_entity_id": r["id"],
                "source_entity_name": device_name,
                "current_platform": src,
                "business_value_score": 0.6,
                "composite_score": 0.55,
                "priority": "high" if r["status"] == "in_progress" else "medium",
                "status": "in_progress" if r["status"] == "in_progress" else "identified",
            })
            results["opportunities"].append({"id": opp_id, "session": r["id"], "src": src, "tgt": tgt})
            results["count"] += 1
    finally:
        mi.close()

    return results


# =========================================================================
# FULL SCAN
# =========================================================================

def run_full_scan(config: dict | None = None, db_path: str | None = None) -> dict[str, Any]:
    """Run all canvas scanners and record a scan history entry."""
    cfg = config or {}
    horizon_days = cfg.get("scan", {}).get("eol_horizon_days", 730)

    scan_id = f"scan-{uuid.uuid4().hex[:10]}"
    started_at = _now()

    mi = _get_mi_conn(db_path)
    try:
        mi.execute(
            """INSERT INTO mi_scans
               (id, scan_type, airgap_mode, canvases_scanned, status, started_at)
               VALUES (%s,%s,%s,%s,%s,%s)""",
            (scan_id, "full", int(_AIRGAP), json.dumps(["ndc", "mdc", "nmce"]), "running", started_at),
        )
        mi.commit()
    finally:
        mi.close()

    results = {
        "scan_id": scan_id,
        "started_at": started_at,
        "airgap_mode": _AIRGAP,
        "sub_scans": {},
    }

    results["sub_scans"]["hardware_eol"] = scan_hardware_eol(horizon_days=horizon_days, db_path=db_path)
    results["sub_scans"]["network_topology"] = scan_network_topology(db_path=db_path)
    results["sub_scans"]["migration_designs"] = scan_migration_designs(db_path=db_path)
    results["sub_scans"]["active_network_migrations"] = scan_active_network_migrations(db_path=db_path)

    total_found = sum(s.get("count", 0) for s in results["sub_scans"].values())
    completed_at = _now()

    mi = _get_mi_conn(db_path)
    try:
        mi.execute(
            """UPDATE mi_scans
               SET status='completed', opportunities_found=%s, opportunities_new=%s,
                   canvases_scanned=%s, completed_at=%s
               WHERE id=%s""",
            (total_found, total_found, json.dumps(["ndc", "mdc", "nmce"]), completed_at, scan_id),
        )
        mi.commit()
    finally:
        mi.close()

    results["total_found"] = total_found
    results["completed_at"] = completed_at
    return results


# =========================================================================
# HELPERS
# =========================================================================

def _days_until(date_str: str) -> int:
    try:
        eol = datetime.strptime(date_str[:10], "%Y-%m-%d").replace(tzinfo=timezone.utc)
        return max(0, (eol - datetime.now(timezone.utc)).days)
    except Exception:
        return 9999


def _eol_urgency_score(days_left: int) -> float:
    if days_left <= 90:
        return 1.0
    if days_left <= 365:
        return 0.75
    if days_left <= 730:
        return 0.50
    return 0.25


def _urgency_priority(days_left: int) -> str:
    if days_left <= 90:
        return "critical"
    if days_left <= 180:
        return "high"
    if days_left <= 365:
        return "medium"
    return "low"


def _map_migration_type(mtype: str) -> str:
    _MAP = {
        "cloud": "cloud_migration",
        "network": "network_redesign",
        "application": "application_migration",
        "platform": "platform_modernization",
        "data": "data_migration",
        "security": "security_uplift",
    }
    for k, v in _MAP.items():
        if k in mtype.lower():
            return v
    return "technology_refresh"


def _upsert_opportunity(conn, fields: dict) -> None:
    """Insert opportunity if not already present (by title match)."""
    existing = conn.execute(
        "SELECT id FROM mi_opportunities WHERE title = %s", (fields["title"],)
    ).fetchone()
    if existing:
        return  # skip duplicates

    now = _now()
    conn.execute(
        """INSERT OR IGNORE INTO mi_opportunities
           (id, title, description, opportunity_type, source_canvas,
            source_entity_id, source_entity_name, current_platform,
            eol_date, eol_urgency_score, business_value_score,
            composite_score, priority, status, created_at, updated_at)
           VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
        (
            fields.get("id", f"opp-{uuid.uuid4().hex[:8]}"),
            fields.get("title", ""),
            fields.get("description", ""),
            fields.get("opportunity_type", "technology_refresh"),
            fields.get("source_canvas", ""),
            fields.get("source_entity_id", ""),
            fields.get("source_entity_name", ""),
            fields.get("current_platform", ""),
            fields.get("eol_date", ""),
            fields.get("eol_urgency_score", 0.0),
            fields.get("business_value_score", 0.0),
            fields.get("composite_score", 0.0),
            fields.get("priority", "medium"),
            fields.get("status", "identified"),
            now, now,
        ),
    )
    conn.commit()
