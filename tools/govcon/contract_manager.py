# CUI // SP-CTI
# ICDEV™ GovProposal — Contract Manager (Phase 60, D-CPMP-1)
# CRUD for contracts, CLINs, WBS, deliverables with status transition enforcement.

"""
Contract Manager — Core CRUD and status transition enforcement for CPMP.

Manages:
    - Contracts (create, update, status transitions)
    - CLINs (create, update)
    - WBS elements (create, update, hierarchical tree)
    - Deliverables (create, update, status pipeline)

All status transitions are enforced via configurable state machines loaded
from args/govcon_config.yaml. Every transition is recorded in cpmp_status_history.

Usage:
    python tools/govcon/contract_manager.py --list-contracts --json
    python tools/govcon/contract_manager.py --get-contract --contract-id <id> --json
    python tools/govcon/contract_manager.py --create-contract --data '{}' --json
    python tools/govcon/contract_manager.py --update-contract --contract-id <id> --data '{}' --json
    python tools/govcon/contract_manager.py --transition-contract --contract-id <id> --new-status active --json
    python tools/govcon/contract_manager.py --list-deliverables --contract-id <id> --json
    python tools/govcon/contract_manager.py --create-deliverable --contract-id <id> --data '{}' --json
    python tools/govcon/contract_manager.py --transition-deliverable --deliverable-id <id> --new-status submitted --json
    python tools/govcon/contract_manager.py --wbs-tree --contract-id <id> --json
    python tools/govcon/contract_manager.py --obligation-summary --contract-id <id> --json
"""

import argparse
import json
import os
import uuid
from tools.db.storage import get_connection
from datetime import date, datetime, timezone
from pathlib import Path

import yaml
from tools.logging.icdev_logger import get_logger

logger = get_logger("icdev.govcon.contract_manager")

_ROOT = Path(__file__).resolve().parent.parent.parent
_DB_PATH = Path(os.environ.get("ICDEV_DB_PATH", str(_ROOT / "data" / "icdev.db")))
_CONFIG_PATH = _ROOT / "args" / "govcon_config.yaml"

# Placeholder title stamped on a contract created without one. It is a
# PLACEHOLDER, not a name: it is identical on every such row, so anything that
# identifies a contract to a human must treat it as absent rather than as a
# title. Exported so those consumers can test against it instead of repeating
# the literal and drifting from it — see cpmp_monitor._contract_label().
DEFAULT_CONTRACT_TITLE = "Untitled Contract"


# ── Config ───────────────────────────────────────────────────────────


def _load_config():
    if _CONFIG_PATH.exists():
        with open(_CONFIG_PATH) as f:
            return yaml.safe_load(f).get("cpmp", {})
    return {}


_CFG = _load_config()

CONTRACT_TRANSITIONS = _CFG.get(
    "contract_transitions",
    {
        "draft": ["active"],
        "active": ["option_pending", "complete", "terminated"],
        "option_pending": ["active", "complete", "terminated"],
        "complete": ["closed"],
        "closed": [],
        "terminated": ["closed"],
    },
)

DELIVERABLE_TRANSITIONS = _CFG.get(
    "deliverable_transitions",
    {
        "not_started": ["in_progress"],
        "in_progress": ["draft_complete", "overdue"],
        "draft_complete": ["internal_review"],
        "internal_review": ["submitted", "in_progress"],
        "submitted": ["government_review"],
        "government_review": ["accepted", "rejected"],
        "accepted": [],
        "rejected": ["resubmitted"],
        "resubmitted": ["government_review"],
        "overdue": ["in_progress", "submitted"],
    },
)


# ── Delinquent (overdue) deliverables — ONE definition ───────────────
#
# Two definitions is how the board and every CPMP screen came to disagree.
# `pmo_ai_advisor` counted date-wise (`due_date < today AND status NOT IN
# ('accepted','rejected')`) and filed high-priority kanban cards from it, while
# contract health (`get_contract`), the CPARS schedule dimension, the portfolio
# rollup and `negative_event_tracker` all read `status = 'overdue'` /
# `days_overdue` instead. Those two fields have exactly one writer —
# `compute_overdue_deliverables()` below — which until now had no caller but
# its own CLI flag, so they were never written: on 2026-08-13 the live board
# held 26 CDRLs 44 days past due, 0 rows with `status='overdue'`, 0 rows with
# `days_overdue > 0`, health green on all nine contracts, and a board card
# saying "5 CDRL(s) are past due".
#
# A deliverable handed to the government on time is NOT delinquent while it
# sits in government review: the contractor met the date. Marking it overdue
# charges them for the government's review clock and drags down the CPARS
# schedule rating on a delivery they actually made — so the delivered statuses
# are excluded, as is any row carrying a submitted_date.
DELIVERED_DELIVERABLE_STATUSES = (
    "submitted",
    "government_review",
    "accepted",
    "rejected",
    "resubmitted",
)

# Rendered once from the tuple above so the SQL cannot drift from the vocabulary.
DELIVERED_STATUS_SQL_LIST = ", ".join(f"'{s}'" for s in DELIVERED_DELIVERABLE_STATUSES)

# A single `%s` binds the cutoff date (today, YYYY-MM-DD). due_date is a TEXT
# column and is NULL or '' on rows created without one; neither is a missed date.
OVERDUE_DELIVERABLE_SQL = (
    "due_date IS NOT NULL AND due_date <> '' AND due_date < %s "
    "AND submitted_date IS NULL "
    f"AND status NOT IN ({DELIVERED_STATUS_SQL_LIST})"
)


# ── Helpers ──────────────────────────────────────────────────────────


def _get_db():
    conn = get_connection()
    # Govcon tools are service-layer operations — clear any Flask RLS context
    # so that complex queries don't fail with RLS column injection errors.
    conn.set_security_context(None)  # rls-bypass: govcon service-layer; complex queries fail with RLS column injection
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def _now():
    return datetime.now(timezone.utc).isoformat()


def _uuid():
    return str(uuid.uuid4())


def _audit(conn, action, details="", actor="contract_manager"):
    try:
        conn.execute(
            "INSERT INTO audit_trail (event_type, actor, action, details, session_id) "
            "VALUES (%s, %s, %s, %s, %s)",
            ("hook_event_logged", actor, action, details, "cpmp"),
        )
    except Exception as exc:  # noqa: BLE001 - best-effort persistence; logged, never raised
        logger.warning("_audit: best-effort INSERT into audit_trail failed (non-blocking): %s", exc)


def _record_status_change(conn, entity_type, entity_id, old_status, new_status, changed_by=None, reason=None):
    """Record status change in cpmp_status_history (append-only, NIST AU-2)."""
    conn.execute(
        "INSERT INTO cpmp_status_history (entity_type, entity_id, old_status, new_status, changed_by, reason) "
        "VALUES (%s, %s, %s, %s, %s, %s)",
        (entity_type, entity_id, old_status, new_status, changed_by, reason),
    )


# ── Contracts ────────────────────────────────────────────────────────


def create_contract(data):
    """Create a new contract."""
    contract_id = _uuid()
    conn = _get_db()
    conn.execute(
        "INSERT INTO cpmp_contracts "
        "(id, contract_number, title, agency, "
        "cor_name, cor_email, cor_phone, contract_type, idiq_contract_id, naics_code, "
        "total_value, funded_value, ceiling_value, pop_start, pop_end, "
        "status, opportunity_id, notes, "
        "created_at, updated_at, created_by) "
        "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
        (
            contract_id,
            data.get("contract_number", ""),
            data.get("title", DEFAULT_CONTRACT_TITLE),
            data.get("agency", ""),
            data.get("cor_name"),
            data.get("cor_email"),
            data.get("cor_phone"),
            data.get("contract_type", "FFP"),
            data.get("idiq_contract_id"),
            data.get("naics_code"),
            data.get("total_value", 0.0),
            data.get("funded_value", 0.0),
            data.get("ceiling_value"),
            data.get("pop_start"),
            data.get("pop_end"),
            "draft",
            data.get("opportunity_id"),
            data.get("notes"),
            _now(),
            _now(),
            data.get("created_by"),
        ),
    )
    _record_status_change(conn, "contract", contract_id, None, "draft", "system", "Contract created")
    _audit(conn, "create_contract", f"Created contract {data.get('contract_number', contract_id)}")
    conn.commit()
    conn.close()
    return {"status": "ok", "contract_id": contract_id}


def get_contract(contract_id):
    """Get a single contract with summary counts."""
    conn = _get_db()
    row = conn.execute("SELECT * FROM cpmp_contracts WHERE id = %s", (contract_id,)).fetchone()
    if not row:
        conn.close()
        return {"status": "error", "message": f"Contract {contract_id} not found"}

    contract = dict(row)

    # Enrich with counts
    contract["clin_count"] = conn.execute(
        "SELECT COUNT(*) FROM cpmp_clins WHERE contract_id = %s", (contract_id,)
    ).fetchone()[0]
    contract["wbs_count"] = conn.execute(
        "SELECT COUNT(*) FROM cpmp_wbs WHERE contract_id = %s", (contract_id,)
    ).fetchone()[0]
    contract["deliverable_count"] = conn.execute(
        "SELECT COUNT(*) FROM cpmp_deliverables WHERE contract_id = %s", (contract_id,)
    ).fetchone()[0]
    contract["overdue_count"] = conn.execute(
        "SELECT COUNT(*) FROM cpmp_deliverables WHERE contract_id = %s AND status = 'overdue'", (contract_id,)
    ).fetchone()[0]
    contract["subcontractor_count"] = conn.execute(
        "SELECT COUNT(*) FROM cpmp_subcontractors WHERE contract_id = %s", (contract_id,)
    ).fetchone()[0]

    conn.close()
    return {"status": "ok", "contract": contract}


def list_contracts(status=None, health=None, limit=50):
    """List contracts with optional filters."""
    conn = _get_db()
    query = "SELECT * FROM cpmp_contracts WHERE 1=1"
    params = []
    if status:
        query += " AND status = ?"
        params.append(status)
    if health:
        query += " AND health = ?"
        params.append(health)
    query += " ORDER BY updated_at DESC LIMIT ?"
    params.append(limit)

    rows = conn.execute(query, params).fetchall()
    conn.close()
    return {"status": "ok", "total": len(rows), "contracts": [dict(r) for r in rows]}


def update_contract(contract_id, data):
    """Update mutable contract fields (not status — use transition_contract)."""
    conn = _get_db()
    row = conn.execute("SELECT id FROM cpmp_contracts WHERE id = %s", (contract_id,)).fetchone()
    if not row:
        conn.close()
        return {"status": "error", "message": f"Contract {contract_id} not found"}

    updatable = [
        "contract_number",
        "title",
        "agency",
        "cor_name",
        "cor_email",
        "cor_phone",
        "contract_type",
        "total_value",
        "funded_value",
        "ceiling_value",
        "pop_start",
        "pop_end",
        "naics_code",
        "notes",
        "idiq_contract_id",
    ]
    sets = []
    params = []
    for field in updatable:
        if field in data:
            sets.append(f"{field} = ?")
            params.append(data[field])

    if not sets:
        conn.close()
        return {"status": "error", "message": "No updatable fields provided"}

    sets.append("updated_at = ?")
    params.append(_now())
    params.append(contract_id)

    conn.execute(f"UPDATE cpmp_contracts SET {', '.join(sets)} WHERE id = %s", params)  # nosec B608 -- table/column names are internal constants, not user input
    _audit(conn, "update_contract", f"Updated contract {contract_id}: {list(data.keys())}")
    conn.commit()
    conn.close()
    return {"status": "ok", "contract_id": contract_id, "updated_fields": list(data.keys())}


def transition_contract(contract_id, new_status, changed_by=None, reason=None):
    """Transition contract status with state machine enforcement."""
    conn = _get_db()
    row = conn.execute("SELECT id, status FROM cpmp_contracts WHERE id = %s", (contract_id,)).fetchone()
    if not row:
        conn.close()
        return {"status": "error", "message": f"Contract {contract_id} not found"}

    old_status = row["status"]
    allowed = CONTRACT_TRANSITIONS.get(old_status, [])
    if new_status not in allowed:
        conn.close()
        return {
            "status": "error",
            "message": f"Invalid transition: {old_status} → {new_status}. Allowed: {allowed}",
        }

    conn.execute(
        "UPDATE cpmp_contracts SET status = %s, updated_at = %s WHERE id = %s",
        (new_status, _now(), contract_id),
    )
    _record_status_change(conn, "contract", contract_id, old_status, new_status, changed_by, reason)
    _audit(conn, "transition_contract", f"Contract {contract_id}: {old_status} → {new_status}")
    conn.commit()
    conn.close()
    return {"status": "ok", "contract_id": contract_id, "old_status": old_status, "new_status": new_status}


# ── CLINs ────────────────────────────────────────────────────────────


def create_clin(contract_id, data):
    """Create a CLIN under a contract."""
    conn = _get_db()
    if not conn.execute("SELECT id FROM cpmp_contracts WHERE id = %s", (contract_id,)).fetchone():
        conn.close()
        return {"status": "error", "message": f"Contract {contract_id} not found"}

    clin_id = _uuid()
    conn.execute(
        "INSERT INTO cpmp_clins (id, contract_id, clin_number, description, clin_type, "
        "total_value, funded_value, billed_value, status, created_at, updated_at) "
        "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
        (
            clin_id,
            contract_id,
            data.get("clin_number", ""),
            data.get("description"),
            data.get("clin_type", data.get("type", "labor")),
            data.get("total_value", 0.0),
            data.get("funded_value", 0.0),
            data.get("billed_value", 0.0),
            "active",
            _now(),
            _now(),
        ),
    )
    _audit(conn, "create_clin", f"Created CLIN {data.get('clin_number')} on contract {contract_id}")
    conn.commit()
    conn.close()
    return {"status": "ok", "clin_id": clin_id}


def list_clins(contract_id):
    """List CLINs for a contract."""
    conn = _get_db()
    rows = conn.execute(
        "SELECT * FROM cpmp_clins WHERE contract_id = %s ORDER BY clin_number", (contract_id,)
    ).fetchall()
    conn.close()
    return {"status": "ok", "total": len(rows), "clins": [dict(r) for r in rows]}


def update_clin(clin_id, data):
    """Update mutable CLIN fields."""
    conn = _get_db()
    row = conn.execute("SELECT id FROM cpmp_clins WHERE id = %s", (clin_id,)).fetchone()
    if not row:
        conn.close()
        return {"status": "error", "message": f"CLIN {clin_id} not found"}

    updatable = ["clin_number", "description", "clin_type", "total_value", "funded_value", "billed_value", "status"]
    sets, params = [], []
    for field in updatable:
        if field in data:
            sets.append(f"{field} = ?")
            params.append(data[field])
    if not sets:
        conn.close()
        return {"status": "error", "message": "No updatable fields provided"}
    sets.append("updated_at = ?")
    params.extend([_now(), clin_id])
    conn.execute(f"UPDATE cpmp_clins SET {', '.join(sets)} WHERE id = %s", params)  # nosec B608 -- table/column names are internal constants, not user input
    conn.commit()
    conn.close()
    return {"status": "ok", "clin_id": clin_id}


# ── Obligation Tracking & Burn Rate ─────────────────────────────────


def get_obligation_summary(contract_id):
    """Compute burn rate and outstanding obligation for a contract.

    Aggregates funded/billed totals from CLINs (falling back to the
    contract-level columns when no CLINs are recorded) to derive:
        - spent_so_far: total billed to date
        - total_owed: remaining obligation (funded_value - billed, floored at 0)
        - burn_rate_pct: spent_so_far as a percentage of funded_value

    When an option period is currently exercised and in-window, its
    ceiling_value is reported separately as `current_option` so base-period
    and option-period spend are never conflated.
    """
    conn = _get_db()
    contract = conn.execute(
        "SELECT id, contract_number, funded_value, billed_value FROM cpmp_contracts WHERE id = %s",
        (contract_id,),
    ).fetchone()
    if not contract:
        conn.close()
        return {"status": "error", "message": f"Contract {contract_id} not found"}

    clin_totals = conn.execute(
        "SELECT COALESCE(SUM(funded_value), 0) AS funded, COALESCE(SUM(billed_value), 0) AS billed "
        "FROM cpmp_clins WHERE contract_id = %s",
        (contract_id,),
    ).fetchone()

    funded = clin_totals["funded"] or contract["funded_value"] or 0.0
    spent_so_far = clin_totals["billed"] or contract["billed_value"] or 0.0
    total_owed = max(funded - spent_so_far, 0.0)
    burn_rate_pct = round((spent_so_far / funded * 100.0), 1) if funded else 0.0

    base_period = {
        "funded_value": round(funded, 2),
        "spent_so_far": round(spent_so_far, 2),
        "total_owed": round(total_owed, 2),
        "burn_rate_pct": burn_rate_pct,
    }

    current_option = None
    try:
        today = date.today().isoformat()
        opt_row = conn.execute(
            "SELECT option_number, ceiling_value, period_start, period_end "
            "FROM cpmp_option_periods WHERE contract_id = %s AND status = 'exercised' "
            "AND (period_start IS NULL OR period_start <= %s) "
            "AND (period_end IS NULL OR period_end >= %s) "
            "ORDER BY option_number DESC LIMIT 1",
            (contract_id, today, today),
        ).fetchone()
        if opt_row:
            ceiling = opt_row["ceiling_value"] or 0.0
            current_option = {
                "option_number": opt_row["option_number"],
                "ceiling_value": round(ceiling, 2),
                "period_start": opt_row["period_start"],
                "period_end": opt_row["period_end"],
            }
    except Exception:
        current_option = None  # cpmp_option_periods may not exist in this environment

    conn.close()
    return {
        "status": "ok",
        "contract_id": contract_id,
        "contract_number": contract["contract_number"],
        "burn_rate_pct": burn_rate_pct,
        "total_owed": base_period["total_owed"],
        "spent_so_far": base_period["spent_so_far"],
        "base_period": base_period,
        "current_option": current_option,
    }


# ── WBS ──────────────────────────────────────────────────────────────


def create_wbs(contract_id, data):
    """Create a WBS element (supports hierarchy via parent_id)."""
    conn = _get_db()
    if not conn.execute("SELECT id FROM cpmp_contracts WHERE id = %s", (contract_id,)).fetchone():
        conn.close()
        return {"status": "error", "message": f"Contract {contract_id} not found"}

    wbs_id = _uuid()
    parent_id = data.get("parent_id")
    level = 1
    if parent_id:
        parent = conn.execute("SELECT level FROM cpmp_wbs WHERE id = %s", (parent_id,)).fetchone()
        if parent:
            level = parent["level"] + 1

    conn.execute(
        "INSERT INTO cpmp_wbs (id, contract_id, parent_id, wbs_number, title, description, "
        "budget_at_completion, planned_start, planned_finish, "
        "status, created_at, updated_at) "
        "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
        (
            wbs_id,
            contract_id,
            parent_id,
            data.get("wbs_number", ""),
            data.get("title", ""),
            data.get("description"),
            data.get("budget_at_completion", 0.0),
            data.get("planned_start"),
            data.get("planned_finish"),
            "not_started",
            _now(),
            _now(),
        ),
    )
    _record_status_change(conn, "wbs", wbs_id, None, "not_started", "system", "WBS created")
    _audit(conn, "create_wbs", f"Created WBS {data.get('wbs_number')} on contract {contract_id}")
    conn.commit()
    conn.close()
    return {"status": "ok", "wbs_id": wbs_id, "level": level}


def list_wbs(contract_id):
    """List WBS elements for a contract."""
    conn = _get_db()
    rows = conn.execute("SELECT * FROM cpmp_wbs WHERE contract_id = %s ORDER BY wbs_number", (contract_id,)).fetchall()
    conn.close()
    return {"status": "ok", "total": len(rows), "wbs_elements": [dict(r) for r in rows]}


def build_wbs_tree(contract_id):
    """Build hierarchical WBS tree from flat list."""
    conn = _get_db()
    rows = conn.execute("SELECT * FROM cpmp_wbs WHERE contract_id = %s ORDER BY wbs_number", (contract_id,)).fetchall()
    conn.close()

    elements = {r["id"]: dict(r) for r in rows}
    for el in elements.values():
        el["children"] = []

    roots = []
    for el in elements.values():
        parent_id = el.get("parent_id")
        if parent_id and parent_id in elements:
            elements[parent_id]["children"].append(el)
        else:
            roots.append(el)

    return {"status": "ok", "tree": roots, "total": len(elements)}


def update_wbs(wbs_id, data):
    """Update mutable WBS fields."""
    conn = _get_db()
    row = conn.execute("SELECT id, status FROM cpmp_wbs WHERE id = %s", (wbs_id,)).fetchone()
    if not row:
        conn.close()
        return {"status": "error", "message": f"WBS {wbs_id} not found"}

    updatable = [
        "wbs_number",
        "title",
        "description",
        "budget_at_completion",
        "pv_cumulative",
        "ev_cumulative",
        "ac_cumulative",
        "percent_complete",
        "planned_start",
        "planned_finish",
        "actual_start",
        "actual_finish",
        "status",
    ]
    old_status = row["status"]
    sets, params = [], []
    for field in updatable:
        if field in data:
            sets.append(f"{field} = ?")
            params.append(data[field])
    if not sets:
        conn.close()
        return {"status": "error", "message": "No updatable fields provided"}

    sets.append("updated_at = ?")
    params.extend([_now(), wbs_id])
    conn.execute(f"UPDATE cpmp_wbs SET {', '.join(sets)} WHERE id = %s", params)  # nosec B608 -- table/column names are internal constants, not user input

    if "status" in data and data["status"] != old_status:
        _record_status_change(conn, "wbs", wbs_id, old_status, data["status"])

    conn.commit()
    conn.close()
    return {"status": "ok", "wbs_id": wbs_id}


# ── Deliverables ─────────────────────────────────────────────────────


def create_deliverable(contract_id, data):
    """Create a deliverable / CDRL under a contract."""
    conn = _get_db()
    if not conn.execute("SELECT id FROM cpmp_contracts WHERE id = %s", (contract_id,)).fetchone():
        conn.close()
        return {"status": "error", "message": f"Contract {contract_id} not found"}

    deliv_id = _uuid()
    conn.execute(
        "INSERT INTO cpmp_deliverables "
        "(id, contract_id, cdrl_number, did_number, title, description, "
        "deliverable_type, frequency, due_date, status, wbs_id, notes, "
        "created_at, updated_at) "
        "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
        (
            deliv_id,
            contract_id,
            data.get("cdrl_number"),
            data.get("did_number"),
            data.get("title", ""),
            data.get("description"),
            data.get("deliverable_type", data.get("type", "cdrl")),
            data.get("frequency"),
            data.get("due_date"),
            "not_started",
            data.get("wbs_id"),
            data.get("notes"),
            _now(),
            _now(),
        ),
    )
    _record_status_change(conn, "deliverable", deliv_id, None, "not_started", "system", "Deliverable created")
    _audit(
        conn, "create_deliverable", f"Created deliverable {data.get('cdrl_number', deliv_id)} on contract {contract_id}"
    )
    conn.commit()
    conn.close()
    return {"status": "ok", "deliverable_id": deliv_id}


def list_deliverables(contract_id, status=None, deliverable_type=None):
    """List deliverables for a contract with optional filters."""
    conn = _get_db()
    query = "SELECT * FROM cpmp_deliverables WHERE contract_id = ?"
    params = [contract_id]
    if status:
        query += " AND status = ?"
        params.append(status)
    if deliverable_type:
        query += " AND deliverable_type = ?"
        params.append(deliverable_type)
    query += " ORDER BY due_date ASC"

    rows = conn.execute(query, params).fetchall()
    conn.close()
    return {"status": "ok", "total": len(rows), "deliverables": [dict(r) for r in rows]}


def get_deliverable(deliverable_id):
    """Get a single deliverable with generation history."""
    conn = _get_db()
    row = conn.execute("SELECT * FROM cpmp_deliverables WHERE id = %s", (deliverable_id,)).fetchone()
    if not row:
        conn.close()
        return {"status": "error", "message": f"Deliverable {deliverable_id} not found"}

    deliverable = dict(row)
    generations = conn.execute(
        "SELECT * FROM cpmp_cdrl_generations WHERE deliverable_id = %s ORDER BY created_at DESC", (deliverable_id,)
    ).fetchall()
    deliverable["generations"] = [dict(g) for g in generations]

    history = conn.execute(
        "SELECT * FROM cpmp_status_history WHERE entity_type = 'deliverable' AND entity_id = %s ORDER BY created_at DESC",
        (deliverable_id,),
    ).fetchall()
    deliverable["status_history"] = [dict(h) for h in history]

    conn.close()
    return {"status": "ok", "deliverable": deliverable}


def update_deliverable(deliverable_id, data):
    """Update mutable deliverable fields (not status — use transition_deliverable)."""
    conn = _get_db()
    row = conn.execute("SELECT id FROM cpmp_deliverables WHERE id = %s", (deliverable_id,)).fetchone()
    if not row:
        conn.close()
        return {"status": "error", "message": f"Deliverable {deliverable_id} not found"}

    updatable = [
        "cdrl_number",
        "did_number",
        "title",
        "description",
        "deliverable_type",
        "frequency",
        "due_date",
        "submitted_date",
        "accepted_date",
        "rejected_date",
        "days_overdue",
        "generated_by_tool",
        "wbs_id",
        "notes",
    ]
    sets, params = [], []
    for field in updatable:
        if field in data:
            sets.append(f"{field} = ?")
            params.append(data[field])
    if not sets:
        conn.close()
        return {"status": "error", "message": "No updatable fields provided"}
    sets.append("updated_at = ?")
    params.extend([_now(), deliverable_id])
    conn.execute(f"UPDATE cpmp_deliverables SET {', '.join(sets)} WHERE id = %s", params)  # nosec B608 -- table/column names are internal constants, not user input
    conn.commit()
    conn.close()
    return {"status": "ok", "deliverable_id": deliverable_id}


def transition_deliverable(deliverable_id, new_status, changed_by=None, reason=None):
    """Transition deliverable status with pipeline enforcement."""
    conn = _get_db()
    row = conn.execute("SELECT id, status FROM cpmp_deliverables WHERE id = %s", (deliverable_id,)).fetchone()
    if not row:
        conn.close()
        return {"status": "error", "message": f"Deliverable {deliverable_id} not found"}

    old_status = row["status"]
    allowed = DELIVERABLE_TRANSITIONS.get(old_status, [])
    if new_status not in allowed:
        conn.close()
        return {
            "status": "error",
            "message": f"Invalid transition: {old_status} → {new_status}. Allowed: {allowed}",
        }

    updates = {"status": new_status, "updated_at": _now()}
    if new_status == "submitted":
        updates["submitted_date"] = _now()
    elif new_status == "accepted":
        updates["accepted_date"] = _now()
        updates["days_overdue"] = 0
    elif new_status == "rejected":
        updates["rejected_date"] = _now()

    set_clauses = ", ".join(f"{k} = ?" for k in updates)
    params = list(updates.values()) + [deliverable_id]
    conn.execute(f"UPDATE cpmp_deliverables SET {set_clauses} WHERE id = %s", params)  # nosec B608 -- table/column names are internal constants, not user input
    _record_status_change(conn, "deliverable", deliverable_id, old_status, new_status, changed_by, reason)
    _audit(conn, "transition_deliverable", f"Deliverable {deliverable_id}: {old_status} → {new_status}")
    conn.commit()
    conn.close()
    return {"status": "ok", "deliverable_id": deliverable_id, "old_status": old_status, "new_status": new_status}


def _parse_due_date(raw):
    """Return a ``date`` for a stored due_date, or None if it is unusable.

    due_date is a TEXT column, so a seeded, imported or hand-edited row can
    hold anything. This was a bare ``datetime.fromisoformat`` in the middle of
    the marking loop: one unparseable row raised, the loop died before
    ``conn.commit()``, and NOTHING was marked — an entire sweep silently undone
    by a single bad row. Now called every 3h by the cpmp_monitor reflex, that
    would have been a permanent outage of the overdue state rather than a bad
    afternoon for whoever ran the CLI.
    """
    if not raw:
        return None
    try:
        return datetime.fromisoformat(str(raw).replace("Z", "+00:00")).date()
    except ValueError:
        return None


def compute_overdue_deliverables(contract_id=None):
    """Detect and mark overdue deliverables, and refresh how late they are.

    The ONLY writer of ``cpmp_deliverables.status = 'overdue'`` and
    ``days_overdue`` — see the OVERDUE_DELIVERABLE_SQL block above for the five
    readers that depend on it and what happened while it went uncalled.

    Rows already marked overdue are re-visited to refresh ``days_overdue``
    rather than skipped. Marking is prompt (every 3h), so a CDRL caught the day
    it slips is stamped ``days_overdue = 1``; frozen there it is both wrong on
    screen and, more quietly, still > 0 — which is the only thing
    ``negative_event_tracker.auto_detect_delinquent`` checks, so a CDRL now
    months late would be escalated as a one-day slip forever.
    """
    conn = _get_db()
    today = datetime.now(timezone.utc).date()
    query = (
        "SELECT id, due_date, status, days_overdue FROM cpmp_deliverables "
        f"WHERE {OVERDUE_DELIVERABLE_SQL}"  # nosec B608 -- module constant built from an internal tuple, not user input
    )
    params = [today.isoformat()]
    if contract_id:
        query += " AND contract_id = %s"
        params.append(contract_id)

    rows = conn.execute(query, params).fetchall()
    marked = 0
    refreshed = 0
    unparseable = 0
    for row in rows:
        due = _parse_due_date(row["due_date"])
        if due is None:
            unparseable += 1
            continue
        days = (today - due).days
        if days < 1:
            # due_date carried a time component that sorts before today's date
            # without a full day having elapsed. Not yet late.
            continue

        if row["status"] == "overdue":
            if row["days_overdue"] != days:
                conn.execute(
                    "UPDATE cpmp_deliverables SET days_overdue = %s, updated_at = %s WHERE id = %s",
                    (days, _now(), row["id"]),
                )
                refreshed += 1
            continue

        conn.execute(
            "UPDATE cpmp_deliverables SET status = 'overdue', days_overdue = %s, updated_at = %s WHERE id = %s",
            (days, _now(), row["id"]),
        )
        _record_status_change(
            conn, "deliverable", row["id"], row["status"], "overdue", "system", f"{days} days overdue"
        )
        marked += 1

    _audit(
        conn,
        "compute_overdue",
        f"Marked {marked} deliverables as overdue; refreshed days_overdue on {refreshed}",
    )
    conn.commit()
    conn.close()
    return {
        "status": "ok",
        "overdue_count": marked,
        "days_refreshed": refreshed,
        "unparseable_due_dates": unparseable,
    }


# ── CLI ──────────────────────────────────────────────────────────────


def main():
    parser = argparse.ArgumentParser(description="ICDEV™ GovProposal Contract Manager (Phase 60)")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--list-contracts", action="store_true")
    group.add_argument("--get-contract", action="store_true")
    group.add_argument("--create-contract", action="store_true")
    group.add_argument("--update-contract", action="store_true")
    group.add_argument("--transition-contract", action="store_true")
    group.add_argument("--list-clins", action="store_true")
    group.add_argument("--create-clin", action="store_true")
    group.add_argument("--list-deliverables", action="store_true")
    group.add_argument("--create-deliverable", action="store_true")
    group.add_argument("--get-deliverable", action="store_true")
    group.add_argument("--transition-deliverable", action="store_true")
    group.add_argument("--wbs-tree", action="store_true")
    group.add_argument("--create-wbs", action="store_true")
    group.add_argument("--compute-overdue", action="store_true")
    group.add_argument("--obligation-summary", action="store_true")

    parser.add_argument("--contract-id")
    parser.add_argument("--deliverable-id")
    parser.add_argument("--clin-id")
    parser.add_argument("--wbs-id")
    parser.add_argument("--new-status")
    parser.add_argument("--changed-by")
    parser.add_argument("--reason")
    parser.add_argument("--data", help="JSON data for create/update")
    parser.add_argument("--status-filter")
    parser.add_argument("--health-filter")
    parser.add_argument("--type-filter")
    parser.add_argument("--json", action="store_true")

    args = parser.parse_args()
    data = json.loads(args.data) if args.data else {}

    if args.list_contracts:
        result = list_contracts(status=args.status_filter, health=args.health_filter)
    elif args.get_contract:
        result = get_contract(args.contract_id)
    elif args.create_contract:
        result = create_contract(data)
    elif args.update_contract:
        result = update_contract(args.contract_id, data)
    elif args.transition_contract:
        result = transition_contract(args.contract_id, args.new_status, args.changed_by, args.reason)
    elif args.list_clins:
        result = list_clins(args.contract_id)
    elif args.create_clin:
        result = create_clin(args.contract_id, data)
    elif args.list_deliverables:
        result = list_deliverables(args.contract_id, status=args.status_filter, deliverable_type=args.type_filter)
    elif args.create_deliverable:
        result = create_deliverable(args.contract_id, data)
    elif args.get_deliverable:
        result = get_deliverable(args.deliverable_id)
    elif args.transition_deliverable:
        result = transition_deliverable(args.deliverable_id, args.new_status, args.changed_by, args.reason)
    elif args.wbs_tree:
        result = build_wbs_tree(args.contract_id)
    elif args.create_wbs:
        result = create_wbs(args.contract_id, data)
    elif args.compute_overdue:
        result = compute_overdue_deliverables(args.contract_id)
    elif args.obligation_summary:
        result = get_obligation_summary(args.contract_id)
    else:
        result = {"status": "error", "message": "Unknown command"}

    print(json.dumps(result, indent=2, default=str))


if __name__ == "__main__":
    main()
