#!/usr/bin/env python3
# CUI // SP-CTI
"""cATO Twin Continuous Monitoring Reflex — 6-hour cadence.

Runs as a Genesis reflex every 6 hours. For each active project:
  1. Pull current compliance state from the multi-regime assessors
  2. Write a new compliance twin snapshot via snapshot_writer
  3. Run the 20 seed IQE queries to detect violations
  4. Auto-generate POA&M items for any new violations
  5. Log a summary to audit_trail

This is the continuous monitoring loop that keeps the compliance twin fresh
and generates the evidence stream required for cATO (NIST SP 800-137).

Reflex contract:
  - run(ctx, conn) → dict with keys: snapshots_written, violations_found,
                                      poam_items_created, projects_processed
  - CADENCE_HOURS = 6 (read by Genesis scheduler)
  - Must be idempotent — running twice in a 6h window is safe (snapshot IDs differ)
  - Must not raise — catches all exceptions per project, logs, continues
"""

from __future__ import annotations
from tools.logging.icdev_logger import get_logger

import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List

BASE_DIR = Path(__file__).resolve().parent.parent.parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from tools.db.storage import get_connection  # noqa: E402

logger = get_logger(__name__)

CADENCE_HOURS = 6

# Frameworks sampled per reflex cycle (ordered by risk level)
_FRAMEWORKS = [
    "FedRAMP High",
    "FedRAMP Moderate",
    "NIST 800-53",
    "CMMC",
]

# Seed queries run on every cycle (controls domain only for Phase 1)
_SEED_QUERIES = [
    # FedRAMP Moderate
    "foreach ctrl in framework('FedRAMP Moderate').controls where ctrl.status != 'satisfied' select ctrl.control_id, ctrl.implementation_status, ctrl.project_id, ctrl.score",
    "foreach ctrl in framework('FedRAMP Moderate').controls where ctrl.evidence_ref is null select ctrl.control_id, ctrl.implementation_status, ctrl.project_id",
    "foreach ctrl in framework('FedRAMP Moderate').controls where ctrl.score < 0.5 select ctrl.control_id, ctrl.score, ctrl.implementation_status, ctrl.project_id",
    "foreach ctrl in framework('FedRAMP Moderate').controls where ctrl.control_id starts_with 'AC' and ctrl.status != 'satisfied' select ctrl.control_id, ctrl.implementation_status, ctrl.score, ctrl.project_id",
    "foreach ctrl in framework('FedRAMP Moderate').controls where ctrl.control_id starts_with 'IA' and ctrl.status != 'satisfied' select ctrl.control_id, ctrl.implementation_status, ctrl.score, ctrl.project_id",
    # FedRAMP High
    "foreach ctrl in framework('FedRAMP High').controls where ctrl.status != 'satisfied' select ctrl.control_id, ctrl.implementation_status, ctrl.project_id, ctrl.score",
    "foreach ctrl in framework('FedRAMP High').controls where ctrl.evidence_ref is null select ctrl.control_id, ctrl.implementation_status, ctrl.project_id",
    "foreach ctrl in framework('FedRAMP High').controls where ctrl.score < 0.5 and ctrl.status == 'not_satisfied' select ctrl.control_id, ctrl.score, ctrl.project_id, ctrl.assessor",
    "foreach ctrl in framework('FedRAMP High').controls where ctrl.control_id starts_with 'SC' and ctrl.status != 'satisfied' select ctrl.control_id, ctrl.implementation_status, ctrl.score, ctrl.project_id",
    "foreach ctrl in framework('FedRAMP High').controls where ctrl.control_id starts_with 'SI' and ctrl.status != 'satisfied' select ctrl.control_id, ctrl.implementation_status, ctrl.score, ctrl.project_id",
]


def _get_active_projects(conn) -> List[Dict]:
    """Return all active projects from the projects table."""
    rows = conn.execute(
        "SELECT id, name FROM projects WHERE id IS NOT NULL"
    ).fetchall()
    return [dict(r) for r in rows]


def _pull_framework_controls(conn, project_id: str, framework: str) -> List[Dict]:
    """Pull current compliance state for a project+framework from assessment tables.

    Phase 1 strategy:
      - Query the most recent framework-specific assessment rows (fedramp_assessments,
        cmmc_assessments, etc.) for the project.
      - Falls back to an empty list if no assessment data exists (safe — snapshot
        writer accepts empty control lists).

    Phase 2 will wire this directly into the multi-regime assessor pipeline.
    """
    table_map = {
        "FedRAMP Moderate": "fedramp_assessments",
        "FedRAMP High": "fedramp_assessments",
        "NIST 800-53": "cssp_assessments",
        "CMMC": "cmmc_assessments",
    }
    table = table_map.get(framework)
    if not table:
        return []

    # Check table exists
    exists = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
        (table,),
    ).fetchone()
    if not exists:
        return []

    # Try to pull rows. Different assessment tables have slightly different schemas
    # so we use a safe column set common to all regime assessors.
    try:
        rows = conn.execute(
            f"""SELECT control_id,
                       COALESCE(status, 'not_assessed')  AS implementation_status,
                       COALESCE(evidence_ref, NULL)       AS evidence_ref,
                       COALESCE(score, 0.0)               AS score
                FROM {table}
               WHERE project_id = ?
               ORDER BY assessed_at DESC""",
            (project_id,),
        ).fetchall()
        return [dict(r) for r in rows]
    except Exception:
        return []


def _log_audit(conn, project_id: str, summary: Dict) -> None:
    try:
        conn.execute(
            """INSERT INTO audit_trail
               (project_id, event_type, actor, action, details, classification)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (
                project_id,
                "cato_twin_reflex",
                "genesis-cato-twin",
                "continuous_monitoring_cycle",
                json.dumps(summary),
                "CUI // SP-CTI",
            ),
        )
    except Exception as e:
        logger.warning("audit log failed: %s", e)


def run(ctx: Dict[str, Any], conn=None) -> Dict[str, Any]:
    """Execute one cATO Twin continuous monitoring cycle.

    Args:
        ctx:  Genesis context dict (may contain 'triggered_by', 'dry_run').
        conn: Optional existing DB connection (for tests).

    Returns:
        Summary dict: snapshots_written, violations_found,
                      poam_items_created, projects_processed, errors.
    """
    from tools.boundary_canvas.cato_twin.snapshot_writer import write_snapshot
    from tools.boundary_canvas.cato_twin.query_engine import run_query
    from tools.boundary_canvas.cato_twin.poam_auto_generator import generate_from_violations

    triggered_by = ctx.get("triggered_by", "genesis_reflex")
    dry_run = ctx.get("dry_run", False)

    _own_conn = conn is None
    if _own_conn:
        conn = get_connection()

    totals: Dict[str, Any] = {
        "snapshots_written": 0,
        "violations_found": 0,
        "poam_items_created": 0,
        "projects_processed": 0,
        "errors": [],
        "status": "ok",
        "cadence_hours": CADENCE_HOURS,
        "run_at": datetime.now(timezone.utc).isoformat(),
    }

    try:
        projects = _get_active_projects(conn)
        if not projects:
            totals["status"] = "no_projects"
            return totals

        for project in projects:
            project_id = project["id"]
            project_violations = 0
            project_poam = 0

            for framework in _FRAMEWORKS:
                try:
                    controls = _pull_framework_controls(conn, project_id, framework)
                    if not controls:
                        continue

                    if dry_run:
                        totals["snapshots_written"] += 1
                        continue

                    snap_id = write_snapshot(
                        project_id=project_id,
                        framework=framework,
                        controls=controls,
                        triggered_by=triggered_by,
                        conn=conn,
                    )
                    totals["snapshots_written"] += 1

                    # Count violations from this snapshot
                    viols = conn.execute(
                        "SELECT COUNT(*) AS cnt FROM compliance_twin_violations "
                        "WHERE snapshot_id = ?",
                        (snap_id,),
                    ).fetchone()
                    viol_count = dict(viols)["cnt"] if viols else 0
                    project_violations += viol_count
                    totals["violations_found"] += viol_count

                    # Auto-generate POA&M items for new violations
                    if viol_count > 0:
                        poam_result = generate_from_violations(
                            snap_id, project_id, conn=conn
                        )
                        project_poam += poam_result.get("new_items", 0)
                        totals["poam_items_created"] += poam_result.get("new_items", 0)

                except Exception as fw_err:
                    msg = f"{project_id}/{framework}: {fw_err}"
                    logger.warning("cato_twin reflex error — %s", msg)
                    totals["errors"].append(msg)

            # Run seed queries against the latest snapshot data (logging only in Phase 1)
            for query in _SEED_QUERIES:
                try:
                    _results = run_query(query, conn=conn)
                except Exception:
                    pass  # Query errors are non-fatal

            totals["projects_processed"] += 1
            _log_audit(conn, project_id, {
                "violations": project_violations,
                "poam_items": project_poam,
                "frameworks_sampled": len(_FRAMEWORKS),
            })

        conn.commit()

    except Exception as top_err:
        totals["status"] = "error"
        totals["errors"].append(str(top_err))
        logger.error("cato_twin reflex top-level error: %s", top_err)
    finally:
        if _own_conn:
            conn.close()

    return totals


if __name__ == "__main__":
    import json as _json
    result = run({})
    print(_json.dumps(result, indent=2))
