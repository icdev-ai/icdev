# CUI // SP-CTI
# ICDEV™ GovProposal — Risk Manager (Phase 60, prop-pm-02)
# Program-level risk register: CRUD + exposure matrix.

"""
Risk Manager — CRUD for cpmp_risks.

Links risks to milestones (prop-pm-01) and negative events for root-cause
traceability.  Exposure = probability × impact (1–25 integer stored on write).

Usage:
    python tools/govcon/risk_manager.py --list --contract-id <id> --json
    python tools/govcon/risk_manager.py --create --contract-id <id> --data '{}' --json
    python tools/govcon/risk_manager.py --update --risk-id <id> --data '{}' --json
    python tools/govcon/risk_manager.py --matrix --contract-id <id> --json
"""

import argparse
import json
import os
import uuid
from datetime import datetime, timezone
from pathlib import Path

from tools.db.storage import get_connection
from tools.logging.icdev_logger import get_logger

logger = get_logger("icdev.govcon.risk_manager")

_ROOT = Path(__file__).resolve().parent.parent.parent
_DB_PATH = Path(os.environ.get("ICDEV_DB_PATH", str(_ROOT / "data" / "icdev.db")))

RISK_CATEGORIES = ("cost", "schedule", "technical", "cyber", "supply_chain", "compliance", "staffing", "other")
RISK_STATUSES = ("open", "mitigating", "accepted", "closed", "transferred")

# Exposure thresholds for colour coding
_LOW = 5
_MEDIUM = 10
_HIGH = 15


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
            ("hook_event_logged", "risk_manager", action, details, "cpmp"),
        )
    except Exception as exc:  # noqa: BLE001 - best-effort persistence; logged, never raised
        logger.warning("_audit: best-effort INSERT into audit_trail failed (non-blocking): %s", exc)


def exposure_label(score):
    """Map a 1–25 exposure score to a severity label."""
    if score <= _LOW:
        return "low"
    if score <= _MEDIUM:
        return "medium"
    if score <= _HIGH:
        return "high"
    return "critical"


# ── CRUD ──────────────────────────────────────────────────────────────


def list_risks(contract_id, status=None, category=None):
    """List risks for a contract, with optional status/category filter."""
    conn = _get_db()
    try:
        params = [contract_id]
        where = "r.contract_id = ?"
        if status:
            where += " AND r.status = ?"
            params.append(status)
        if category:
            where += " AND r.category = ?"
            params.append(category)
        rows = conn.execute(
            """
            SELECT r.*,
                   m.title AS milestone_title,
                   ne.event_type AS neg_event_type
            FROM   cpmp_risks r
            LEFT JOIN cpmp_milestones       m  ON m.id  = r.milestone_id
            LEFT JOIN cpmp_negative_events  ne ON ne.id = r.negative_event_id
            WHERE  """ + where + """
            ORDER  BY r.exposure DESC, r.created_at ASC
            """,
            params,
        ).fetchall()
        return {"status": "ok", "risks": [dict(r) for r in rows]}
    except Exception as exc:
        return {"status": "error", "message": str(exc)}
    finally:
        conn.close()


def get_risk(risk_id):
    conn = _get_db()
    try:
        row = conn.execute(
            """
            SELECT r.*,
                   m.title AS milestone_title,
                   ne.event_type AS neg_event_type
            FROM   cpmp_risks r
            LEFT JOIN cpmp_milestones       m  ON m.id  = r.milestone_id
            LEFT JOIN cpmp_negative_events  ne ON ne.id = r.negative_event_id
            WHERE  r.id = %s
            """,
            (risk_id,),
        ).fetchone()
        if not row:
            return {"status": "error", "message": "Risk not found"}
        return {"status": "ok", "risk": dict(row)}
    except Exception as exc:
        return {"status": "error", "message": str(exc)}
    finally:
        conn.close()


def create_risk(data):
    """Create a new risk. Required: contract_id, title, category."""
    contract_id = data.get("contract_id")
    title = data.get("title", "").strip()
    category = data.get("category", "other")
    if not contract_id:
        return {"status": "error", "message": "contract_id required"}
    if not title:
        return {"status": "error", "message": "title required"}
    if category not in RISK_CATEGORIES:
        return {"status": "error", "message": f"category must be one of {RISK_CATEGORIES}"}
    status = data.get("status", "open")
    if status not in RISK_STATUSES:
        return {"status": "error", "message": f"status must be one of {RISK_STATUSES}"}
    try:
        probability = int(data.get("probability", 3))
        impact = int(data.get("impact", 3))
    except (ValueError, TypeError):
        return {"status": "error", "message": "probability and impact must be integers 1–5"}
    if not (1 <= probability <= 5 and 1 <= impact <= 5):
        return {"status": "error", "message": "probability and impact must be between 1 and 5"}
    exposure = probability * impact
    rid = _uuid()
    conn = _get_db()
    try:
        conn.execute(
            """
            INSERT INTO cpmp_risks
                (id, contract_id, title, category, probability, impact, exposure,
                 mitigation, owner, status, milestone_id, negative_event_id,
                 classification, tenant_id, metadata, created_at, updated_at)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
            """,
            (
                rid,
                contract_id,
                title,
                category,
                probability,
                impact,
                exposure,
                data.get("mitigation"),
                data.get("owner"),
                status,
                data.get("milestone_id"),
                data.get("negative_event_id"),
                data.get("classification", "CUI"),
                data.get("tenant_id"),
                json.dumps(data.get("metadata", {})),
                _now(),
                _now(),
            ),
        )
        _audit(conn, "create_risk", f"contract={contract_id} id={rid}")
        conn.commit()
        return {"status": "ok", "risk_id": rid}
    except Exception as exc:
        conn.rollback()
        return {"status": "error", "message": str(exc)}
    finally:
        conn.close()


def update_risk(risk_id, data):
    """Update editable fields on a risk."""
    conn = _get_db()
    try:
        row = conn.execute("SELECT id FROM cpmp_risks WHERE id = %s", (risk_id,)).fetchone()
        if not row:
            return {"status": "error", "message": "Risk not found"}
        allowed = {
            "title", "category", "probability", "impact", "mitigation",
            "owner", "status", "milestone_id", "negative_event_id",
            "classification", "tenant_id", "metadata",
        }
        updates = {k: v for k, v in data.items() if k in allowed}
        if not updates:
            return {"status": "ok", "message": "nothing to update"}
        if "category" in updates and updates["category"] not in RISK_CATEGORIES:
            return {"status": "error", "message": f"category must be one of {RISK_CATEGORIES}"}
        if "status" in updates and updates["status"] not in RISK_STATUSES:
            return {"status": "error", "message": f"status must be one of {RISK_STATUSES}"}
        if "metadata" in updates and not isinstance(updates["metadata"], str):
            updates["metadata"] = json.dumps(updates["metadata"])
        # Re-compute exposure if probability or impact changed
        needs_exposure = "probability" in updates or "impact" in updates
        if needs_exposure:
            cur = conn.execute(
                "SELECT probability, impact FROM cpmp_risks WHERE id = %s", (risk_id,)
            ).fetchone()
            p = int(updates.get("probability", cur["probability"]))
            i = int(updates.get("impact", cur["impact"]))
            if not (1 <= p <= 5 and 1 <= i <= 5):
                return {"status": "error", "message": "probability and impact must be between 1 and 5"}
            updates["exposure"] = p * i
        updates["updated_at"] = _now()
        set_clause = ", ".join(f"{k} = ?" for k in updates)
        conn.execute(
            f"UPDATE cpmp_risks SET {set_clause} WHERE id = %s",
            list(updates.values()) + [risk_id],
        )
        _audit(conn, "update_risk", f"id={risk_id} fields={list(updates)}")
        conn.commit()
        return {"status": "ok", "risk_id": risk_id}
    except Exception as exc:
        conn.rollback()
        return {"status": "error", "message": str(exc)}
    finally:
        conn.close()


def delete_risk(risk_id):
    conn = _get_db()
    try:
        row = conn.execute("SELECT id FROM cpmp_risks WHERE id = %s", (risk_id,)).fetchone()
        if not row:
            return {"status": "error", "message": "Risk not found"}
        conn.execute("DELETE FROM cpmp_risks WHERE id = %s", (risk_id,))
        _audit(conn, "delete_risk", f"id={risk_id}")
        conn.commit()
        return {"status": "ok"}
    except Exception as exc:
        conn.rollback()
        return {"status": "error", "message": str(exc)}
    finally:
        conn.close()


# ── Matrix ────────────────────────────────────────────────────────────


def get_risk_matrix(contract_id):
    """Return a 5×5 matrix of risks by probability (row) × impact (col).

    Each cell contains the list of risk IDs + titles at that position.
    """
    result = list_risks(contract_id)
    if result.get("status") != "ok":
        return result
    risks = result["risks"]

    # Build a 5×5 grid indexed [prob][impact] (1-based)
    grid = {p: {i: [] for i in range(1, 6)} for p in range(1, 6)}
    counts = {"critical": 0, "high": 0, "medium": 0, "low": 0}
    for r in risks:
        p = r.get("probability", 3)
        i = r.get("impact", 3)
        if 1 <= p <= 5 and 1 <= i <= 5:
            grid[p][i].append({"id": r["id"], "title": r["title"], "status": r["status"]})
        label = exposure_label(r.get("exposure", p * i))
        counts[label] = counts.get(label, 0) + 1

    return {
        "status": "ok",
        "matrix": grid,
        "summary": {
            "total": len(risks),
            "open": sum(1 for r in risks if r.get("status") == "open"),
            "critical": counts["critical"],
            "high": counts["high"],
            "medium": counts["medium"],
            "low": counts["low"],
        },
    }


def get_portfolio_risk_summary():
    """Return aggregate risk counts across all contracts."""
    conn = _get_db()
    try:
        rows = conn.execute(
            """
            SELECT contract_id,
                   COUNT(*) AS total,
                   SUM(CASE WHEN status = 'open'    THEN 1 ELSE 0 END) AS open_count,
                   SUM(CASE WHEN exposure >= 16     THEN 1 ELSE 0 END) AS critical_count,
                   SUM(CASE WHEN exposure BETWEEN 11 AND 15 THEN 1 ELSE 0 END) AS high_count
            FROM   cpmp_risks
            GROUP  BY contract_id
            """
        ).fetchall()
        total = sum(r["total"] for r in rows)
        open_total = sum(r["open_count"] for r in rows)
        critical_total = sum(r["critical_count"] for r in rows)
        high_total = sum(r["high_count"] for r in rows)
        return {
            "status": "ok",
            "summary": {
                "total": total,
                "open": open_total,
                "critical": critical_total,
                "high": high_total,
                "by_contract": [dict(r) for r in rows],
            },
        }
    except Exception as exc:
        return {"status": "error", "message": str(exc)}
    finally:
        conn.close()


# ── CLI ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="CPMP Risk Manager")
    ap.add_argument("--list", action="store_true", help="List risks")
    ap.add_argument("--get", action="store_true", help="Get a single risk")
    ap.add_argument("--create", action="store_true", help="Create risk")
    ap.add_argument("--update", action="store_true", help="Update risk")
    ap.add_argument("--delete", action="store_true", help="Delete risk")
    ap.add_argument("--matrix", action="store_true", help="Get risk matrix")
    ap.add_argument("--portfolio-summary", action="store_true", help="Portfolio risk summary")
    ap.add_argument("--contract-id")
    ap.add_argument("--risk-id")
    ap.add_argument("--status")
    ap.add_argument("--category")
    ap.add_argument("--data", default="{}")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    result = {}
    if args.list:
        result = list_risks(args.contract_id, status=args.status, category=args.category)
    elif args.get:
        result = get_risk(args.risk_id)
    elif args.create:
        result = create_risk(json.loads(args.data))
    elif args.update:
        result = update_risk(args.risk_id, json.loads(args.data))
    elif args.delete:
        result = delete_risk(args.risk_id)
    elif args.matrix:
        result = get_risk_matrix(args.contract_id)
    elif args.portfolio_summary:
        result = get_portfolio_risk_summary()
    print(json.dumps(result, indent=2))
