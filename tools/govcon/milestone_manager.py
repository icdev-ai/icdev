# CUI // SP-CTI
# ICDEV™ GovProposal — Milestone Manager (Phase 60, prop-pm-01)
# Lightweight Integrated Master Schedule: milestones + dependency graph.

"""
Milestone Manager — CRUD for cpmp_milestones and cpmp_milestone_deps.

Links milestones to WBS elements (schedule traceability) and EVM periods
(EVM-to-schedule integration).  No status machine enforcement — milestones
transition freely between: pending / in_progress / complete / missed / on_hold.

Usage:
    python tools/govcon/milestone_manager.py --list --contract-id <id> --json
    python tools/govcon/milestone_manager.py --create --contract-id <id> --data '{}' --json
    python tools/govcon/milestone_manager.py --update --milestone-id <id> --data '{}' --json
    python tools/govcon/milestone_manager.py --list-deps --contract-id <id> --json
"""

import argparse
import json
import os
import uuid
from datetime import datetime, timezone
from pathlib import Path

from tools.db.storage import get_connection
from tools.logging.icdev_logger import get_logger

logger = get_logger("icdev.govcon.milestone_manager")

_ROOT = Path(__file__).resolve().parent.parent.parent
_DB_PATH = Path(os.environ.get("ICDEV_DB_PATH", str(_ROOT / "data" / "icdev.db")))

MILESTONE_STATUSES = ("pending", "in_progress", "complete", "missed", "on_hold")
DEP_TYPES = ("finish_to_start", "start_to_start", "finish_to_finish", "start_to_finish")


def _get_db():
    conn = get_connection()
    conn.set_security_context(None)  # rls-bypass: govcon service-layer; govcon tables lack tenant_id/classification columns
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def _now():
    return datetime.now(timezone.utc).isoformat()


def _uuid():
    return str(uuid.uuid4())


def _audit(conn, action, details=""):
    try:
        conn.execute(
            "INSERT INTO audit_trail (event_type, actor, action, details, session_id) "
            "VALUES (%s, %s, %s, %s, %s)",
            ("hook_event_logged", "milestone_manager", action, details, "cpmp"),
        )
    except Exception as exc:  # noqa: BLE001 - best-effort persistence; logged, never raised
        logger.warning("_audit: best-effort INSERT into audit_trail failed (non-blocking): %s", exc)


# ── Milestones ────────────────────────────────────────────────────────


def list_milestones(contract_id, status=None):
    """List milestones for a contract, joined with WBS number for display."""
    conn = _get_db()
    try:
        params = [contract_id]
        where = "m.contract_id = ?"
        if status:
            where += " AND m.status = ?"
            params.append(status)
        rows = conn.execute(
            """
            SELECT m.*,
                   w.wbs_number,
                   w.title AS wbs_title,
                   e.spi    AS evm_spi,
                   e.cpi    AS evm_cpi
            FROM   cpmp_milestones m
            LEFT JOIN cpmp_wbs          w ON w.id = m.wbs_id
            LEFT JOIN cpmp_evm_periods  e ON e.id = m.evm_period_id
            WHERE  """ + where + """
            ORDER  BY m.baseline_date ASC NULLS LAST, m.created_at ASC
            """,
            params,
        ).fetchall()
        return {"status": "ok", "milestones": [dict(r) for r in rows]}
    except Exception as exc:
        return {"status": "error", "message": str(exc)}
    finally:
        conn.close()


def get_milestone(milestone_id):
    conn = _get_db()
    try:
        row = conn.execute(
            """
            SELECT m.*,
                   w.wbs_number,
                   w.title AS wbs_title,
                   e.spi   AS evm_spi,
                   e.cpi   AS evm_cpi
            FROM   cpmp_milestones m
            LEFT JOIN cpmp_wbs         w ON w.id = m.wbs_id
            LEFT JOIN cpmp_evm_periods e ON e.id = m.evm_period_id
            WHERE  m.id = %s
            """,
            (milestone_id,),
        ).fetchone()
        if not row:
            return {"status": "error", "message": "Milestone not found"}
        return {"status": "ok", "milestone": dict(row)}
    except Exception as exc:
        return {"status": "error", "message": str(exc)}
    finally:
        conn.close()


def create_milestone(data):
    """Create a new milestone. Required: contract_id, title."""
    contract_id = data.get("contract_id")
    title = data.get("title", "").strip()
    if not contract_id:
        return {"status": "error", "message": "contract_id required"}
    if not title:
        return {"status": "error", "message": "title required"}
    status = data.get("status", "pending")
    if status not in MILESTONE_STATUSES:
        return {"status": "error", "message": f"status must be one of {MILESTONE_STATUSES}"}
    mid = _uuid()
    conn = _get_db()
    try:
        conn.execute(
            """
            INSERT INTO cpmp_milestones
                (id, contract_id, wbs_id, title, description,
                 baseline_date, forecast_date, actual_date, status,
                 evm_period_id, responsible_person, notes, metadata,
                 created_at, updated_at, classification)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
            """,
            (
                mid,
                contract_id,
                data.get("wbs_id"),
                title,
                data.get("description"),
                data.get("baseline_date"),
                data.get("forecast_date"),
                data.get("actual_date"),
                status,
                data.get("evm_period_id"),
                data.get("responsible_person"),
                data.get("notes"),
                json.dumps(data.get("metadata", {})),
                _now(),
                _now(),
                data.get("classification", "CUI"),
            ),
        )
        _audit(conn, "create_milestone", f"contract={contract_id} id={mid}")
        conn.commit()
        return {"status": "ok", "milestone_id": mid}
    except Exception as exc:
        conn.rollback()
        return {"status": "error", "message": str(exc)}
    finally:
        conn.close()


def update_milestone(milestone_id, data):
    """Update editable fields on a milestone."""
    conn = _get_db()
    try:
        row = conn.execute(
            "SELECT id FROM cpmp_milestones WHERE id = %s", (milestone_id,)
        ).fetchone()
        if not row:
            return {"status": "error", "message": "Milestone not found"}
        allowed = {
            "wbs_id", "title", "description", "baseline_date", "forecast_date",
            "actual_date", "status", "evm_period_id", "responsible_person",
            "notes", "metadata", "classification",
        }
        updates = {k: v for k, v in data.items() if k in allowed}
        if not updates:
            return {"status": "ok", "message": "nothing to update"}
        if "status" in updates and updates["status"] not in MILESTONE_STATUSES:
            return {"status": "error", "message": f"status must be one of {MILESTONE_STATUSES}"}
        if "metadata" in updates and not isinstance(updates["metadata"], str):
            updates["metadata"] = json.dumps(updates["metadata"])
        updates["updated_at"] = _now()
        set_clause = ", ".join(f"{k} = ?" for k in updates)
        conn.execute(
            f"UPDATE cpmp_milestones SET {set_clause} WHERE id = %s",
            list(updates.values()) + [milestone_id],
        )
        _audit(conn, "update_milestone", f"id={milestone_id} fields={list(updates)}")
        conn.commit()
        return {"status": "ok", "milestone_id": milestone_id}
    except Exception as exc:
        conn.rollback()
        return {"status": "error", "message": str(exc)}
    finally:
        conn.close()


def delete_milestone(milestone_id):
    conn = _get_db()
    try:
        row = conn.execute(
            "SELECT id FROM cpmp_milestones WHERE id = %s", (milestone_id,)
        ).fetchone()
        if not row:
            return {"status": "error", "message": "Milestone not found"}
        conn.execute("DELETE FROM cpmp_milestone_deps WHERE predecessor_id = %s OR successor_id = %s",
                     (milestone_id, milestone_id))
        conn.execute("DELETE FROM cpmp_milestones WHERE id = %s", (milestone_id,))
        _audit(conn, "delete_milestone", f"id={milestone_id}")
        conn.commit()
        return {"status": "ok"}
    except Exception as exc:
        conn.rollback()
        return {"status": "error", "message": str(exc)}
    finally:
        conn.close()


# ── Dependencies ──────────────────────────────────────────────────────


def list_deps(contract_id):
    """List milestone dependencies for a contract."""
    conn = _get_db()
    try:
        rows = conn.execute(
            """
            SELECT d.*,
                   p.title AS predecessor_title,
                   s.title AS successor_title
            FROM   cpmp_milestone_deps d
            JOIN   cpmp_milestones p ON p.id = d.predecessor_id
            JOIN   cpmp_milestones s ON s.id = d.successor_id
            WHERE  d.contract_id = %s
            ORDER  BY d.created_at ASC
            """,
            (contract_id,),
        ).fetchall()
        return {"status": "ok", "deps": [dict(r) for r in rows]}
    except Exception as exc:
        return {"status": "error", "message": str(exc)}
    finally:
        conn.close()


def create_dep(data):
    """Create a milestone dependency. Required: contract_id, predecessor_id, successor_id."""
    contract_id = data.get("contract_id")
    pred_id = data.get("predecessor_id")
    succ_id = data.get("successor_id")
    if not all([contract_id, pred_id, succ_id]):
        return {"status": "error", "message": "contract_id, predecessor_id, successor_id required"}
    if pred_id == succ_id:
        return {"status": "error", "message": "predecessor and successor cannot be the same milestone"}
    dep_type = data.get("dep_type", "finish_to_start")
    if dep_type not in DEP_TYPES:
        return {"status": "error", "message": f"dep_type must be one of {DEP_TYPES}"}
    did = _uuid()
    conn = _get_db()
    try:
        conn.execute(
            """
            INSERT INTO cpmp_milestone_deps
                (id, contract_id, predecessor_id, successor_id, lag_days, dep_type, notes, created_at, classification)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)
            """,
            (
                did,
                contract_id,
                pred_id,
                succ_id,
                int(data.get("lag_days", 0)),
                dep_type,
                data.get("notes"),
                _now(),
                data.get("classification", "CUI"),
            ),
        )
        _audit(conn, "create_dep", f"contract={contract_id} pred={pred_id} succ={succ_id}")
        conn.commit()
        return {"status": "ok", "dep_id": did}
    except Exception as exc:
        conn.rollback()
        return {"status": "error", "message": str(exc)}
    finally:
        conn.close()


def delete_dep(dep_id):
    conn = _get_db()
    try:
        row = conn.execute(
            "SELECT id FROM cpmp_milestone_deps WHERE id = %s", (dep_id,)
        ).fetchone()
        if not row:
            return {"status": "error", "message": "Dependency not found"}
        conn.execute("DELETE FROM cpmp_milestone_deps WHERE id = %s", (dep_id,))
        _audit(conn, "delete_dep", f"id={dep_id}")
        conn.commit()
        return {"status": "ok"}
    except Exception as exc:
        conn.rollback()
        return {"status": "error", "message": str(exc)}
    finally:
        conn.close()


# ── CLI ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="CPMP Milestone Manager")
    ap.add_argument("--list", action="store_true", help="List milestones")
    ap.add_argument("--create", action="store_true", help="Create milestone")
    ap.add_argument("--update", action="store_true", help="Update milestone")
    ap.add_argument("--delete", action="store_true", help="Delete milestone")
    ap.add_argument("--list-deps", action="store_true", help="List dependencies")
    ap.add_argument("--create-dep", action="store_true", help="Create dependency")
    ap.add_argument("--delete-dep", action="store_true", help="Delete dependency")
    ap.add_argument("--contract-id")
    ap.add_argument("--milestone-id")
    ap.add_argument("--dep-id")
    ap.add_argument("--data", default="{}")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    result = {}
    if args.list:
        result = list_milestones(args.contract_id)
    elif args.create:
        result = create_milestone(json.loads(args.data))
    elif args.update:
        result = update_milestone(args.milestone_id, json.loads(args.data))
    elif args.delete:
        result = delete_milestone(args.milestone_id)
    elif args.list_deps:
        result = list_deps(args.contract_id)
    elif args.create_dep:
        result = create_dep(json.loads(args.data))
    elif args.delete_dep:
        result = delete_dep(args.dep_id)
    print(json.dumps(result, indent=2))
