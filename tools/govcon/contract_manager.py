# CUI // SP-CTI
# ICDEV GovProposal — Contract Manager (Phase 60, D-CPMP-1)
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
"""

import argparse
import json
import os
import sqlite3
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

import yaml

_ROOT = Path(__file__).resolve().parent.parent.parent
_DB_PATH = Path(os.environ.get("ICDEV_DB_PATH", str(_ROOT / "data" / "icdev.db")))
_CONFIG_PATH = _ROOT / "args" / "govcon_config.yaml"


# ── Config ───────────────────────────────────────────────────────────

def _load_config():
    if _CONFIG_PATH.exists():
        with open(_CONFIG_PATH) as f:
            return yaml.safe_load(f).get("cpmp", {})
    return {}


_CFG = _load_config()

CONTRACT_TRANSITIONS = _CFG.get("contract_transitions", {
    "draft": ["active"],
    "active": ["option_pending", "complete", "terminated"],
    "option_pending": ["active", "complete", "terminated"],
    "complete": ["closed"],
    "closed": [],
    "terminated": ["closed"],
})

DELIVERABLE_TRANSITIONS = _CFG.get("deliverable_transitions", {
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
})


# ── Helpers ──────────────────────────────────────────────────────────

def _get_db():
    conn = sqlite3.connect(str(_DB_PATH))
    conn.row_factory = sqlite3.Row
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
            "INSERT INTO audit_trail (id, timestamp, event_type, actor, action, details, session_id) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (_uuid(), _now(), "cpmp.contract_manager", actor, action, details, "cpmp"),
        )
    except Exception:
        pass


def _record_status_change(conn, entity_type, entity_id, old_status, new_status, changed_by=None, reason=None):
    """Record status change in cpmp_status_history (append-only, NIST AU-2)."""
    conn.execute(
        "INSERT INTO cpmp_status_history (entity_type, entity_id, old_status, new_status, changed_by, reason) "
        "VALUES (?, ?, ?, ?, ?, ?)",
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
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            contract_id,
            data.get("contract_number", ""),
            data.get("title", "Untitled Contract"),
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
            _now(), _now(),
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
    row = conn.execute("SELECT * FROM cpmp_contracts WHERE id = ?", (contract_id,)).fetchone()
    if not row:
        conn.close()
        return {"status": "error", "message": f"Contract {contract_id} not found"}

    contract = dict(row)

    # Enrich with counts
    contract["clin_count"] = conn.execute(
        "SELECT COUNT(*) FROM cpmp_clins WHERE contract_id = ?", (contract_id,)
    ).fetchone()[0]
    contract["wbs_count"] = conn.execute(
        "SELECT COUNT(*) FROM cpmp_wbs WHERE contract_id = ?", (contract_id,)
    ).fetchone()[0]
    contract["deliverable_count"] = conn.execute(
        "SELECT COUNT(*) FROM cpmp_deliverables WHERE contract_id = ?", (contract_id,)
    ).fetchone()[0]
    contract["overdue_count"] = conn.execute(
        "SELECT COUNT(*) FROM cpmp_deliverables WHERE contract_id = ? AND status = 'overdue'",
        (contract_id,)
    ).fetchone()[0]
    contract["subcontractor_count"] = conn.execute(
        "SELECT COUNT(*) FROM cpmp_subcontractors WHERE contract_id = ?", (contract_id,)
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
    row = conn.execute("SELECT id FROM cpmp_contracts WHERE id = ?", (contract_id,)).fetchone()
    if not row:
        conn.close()
        return {"status": "error", "message": f"Contract {contract_id} not found"}

    updatable = [
        "contract_number", "title", "agency", "cor_name", "cor_email", "cor_phone",
        "contract_type", "total_value", "funded_value", "ceiling_value",
        "pop_start", "pop_end", "naics_code", "notes", "idiq_contract_id",
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

    conn.execute(f"UPDATE cpmp_contracts SET {', '.join(sets)} WHERE id = ?", params)
    _audit(conn, "update_contract", f"Updated contract {contract_id}: {list(data.keys())}")
    conn.commit()
    conn.close()
    return {"status": "ok", "contract_id": contract_id, "updated_fields": list(data.keys())}


def transition_contract(contract_id, new_status, changed_by=None, reason=None):
    """Transition contract status with state machine enforcement."""
    conn = _get_db()
    row = conn.execute("SELECT id, status FROM cpmp_contracts WHERE id = ?", (contract_id,)).fetchone()
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
        "UPDATE cpmp_contracts SET status = ?, updated_at = ? WHERE id = ?",
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
    if not conn.execute("SELECT id FROM cpmp_contracts WHERE id = ?", (contract_id,)).fetchone():
        conn.close()
        return {"status": "error", "message": f"Contract {contract_id} not found"}

    clin_id = _uuid()
    conn.execute(
        "INSERT INTO cpmp_clins (id, contract_id, clin_number, description, type, "
        "total_value, funded_value, billed_value, status, created_at, updated_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            clin_id, contract_id, data.get("clin_number", ""),
            data.get("description"), data.get("type", "labor"),
            data.get("total_value", 0.0), data.get("funded_value", 0.0),
            data.get("billed_value", 0.0),
            "active", _now(), _now(),
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
        "SELECT * FROM cpmp_clins WHERE contract_id = ? ORDER BY clin_number", (contract_id,)
    ).fetchall()
    conn.close()
    return {"status": "ok", "total": len(rows), "clins": [dict(r) for r in rows]}


def update_clin(clin_id, data):
    """Update mutable CLIN fields."""
    conn = _get_db()
    row = conn.execute("SELECT id FROM cpmp_clins WHERE id = ?", (clin_id,)).fetchone()
    if not row:
        conn.close()
        return {"status": "error", "message": f"CLIN {clin_id} not found"}

    updatable = ["clin_number", "description", "type", "total_value", "funded_value",
                 "billed_value", "status"]
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
    conn.execute(f"UPDATE cpmp_clins SET {', '.join(sets)} WHERE id = ?", params)
    conn.commit()
    conn.close()
    return {"status": "ok", "clin_id": clin_id}


# ── WBS ──────────────────────────────────────────────────────────────

def create_wbs(contract_id, data):
    """Create a WBS element (supports hierarchy via parent_id)."""
    conn = _get_db()
    if not conn.execute("SELECT id FROM cpmp_contracts WHERE id = ?", (contract_id,)).fetchone():
        conn.close()
        return {"status": "error", "message": f"Contract {contract_id} not found"}

    wbs_id = _uuid()
    parent_id = data.get("parent_id")
    level = 1
    if parent_id:
        parent = conn.execute("SELECT level FROM cpmp_wbs WHERE id = ?", (parent_id,)).fetchone()
        if parent:
            level = parent["level"] + 1

    conn.execute(
        "INSERT INTO cpmp_wbs (id, contract_id, parent_id, wbs_number, title, description, "
        "budget_at_completion, planned_start, planned_finish, "
        "status, created_at, updated_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            wbs_id, contract_id, parent_id, data.get("wbs_number", ""),
            data.get("title", ""), data.get("description"),
            data.get("budget_at_completion", 0.0),
            data.get("planned_start"), data.get("planned_finish"),
            "not_started", _now(), _now(),
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
    rows = conn.execute(
        "SELECT * FROM cpmp_wbs WHERE contract_id = ? ORDER BY wbs_number", (contract_id,)
    ).fetchall()
    conn.close()
    return {"status": "ok", "total": len(rows), "wbs_elements": [dict(r) for r in rows]}


def build_wbs_tree(contract_id):
    """Build hierarchical WBS tree from flat list."""
    conn = _get_db()
    rows = conn.execute(
        "SELECT * FROM cpmp_wbs WHERE contract_id = ? ORDER BY wbs_number", (contract_id,)
    ).fetchall()
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
    row = conn.execute("SELECT id, status FROM cpmp_wbs WHERE id = ?", (wbs_id,)).fetchone()
    if not row:
        conn.close()
        return {"status": "error", "message": f"WBS {wbs_id} not found"}

    updatable = ["wbs_number", "title", "description", "budget_at_completion",
                 "pv_cumulative", "ev_cumulative", "ac_cumulative", "percent_complete",
                 "planned_start", "planned_finish", "actual_start", "actual_finish",
                 "status"]
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
    conn.execute(f"UPDATE cpmp_wbs SET {', '.join(sets)} WHERE id = ?", params)

    if "status" in data and data["status"] != old_status:
        _record_status_change(conn, "wbs", wbs_id, old_status, data["status"])

    conn.commit()
    conn.close()
    return {"status": "ok", "wbs_id": wbs_id}


# ── Deliverables ─────────────────────────────────────────────────────

def create_deliverable(contract_id, data):
    """Create a deliverable / CDRL under a contract."""
    conn = _get_db()
    if not conn.execute("SELECT id FROM cpmp_contracts WHERE id = ?", (contract_id,)).fetchone():
        conn.close()
        return {"status": "error", "message": f"Contract {contract_id} not found"}

    deliv_id = _uuid()
    conn.execute(
        "INSERT INTO cpmp_deliverables "
        "(id, contract_id, cdrl_number, did_number, title, description, "
        "type, cdrl_type, frequency, due_date, status, wbs_id, notes, "
        "created_at, updated_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            deliv_id, contract_id,
            data.get("cdrl_number"), data.get("did_number"),
            data.get("title", ""), data.get("description"),
            data.get("type", "cdrl"), data.get("cdrl_type"),
            data.get("frequency"), data.get("due_date"),
            "not_started", data.get("wbs_id"), data.get("notes"),
            _now(), _now(),
        ),
    )
    _record_status_change(conn, "deliverable", deliv_id, None, "not_started", "system", "Deliverable created")
    _audit(conn, "create_deliverable", f"Created deliverable {data.get('cdrl_number', deliv_id)} on contract {contract_id}")
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
        query += " AND type = ?"
        params.append(deliverable_type)
    query += " ORDER BY due_date ASC"

    rows = conn.execute(query, params).fetchall()
    conn.close()
    return {"status": "ok", "total": len(rows), "deliverables": [dict(r) for r in rows]}


def get_deliverable(deliverable_id):
    """Get a single deliverable with generation history."""
    conn = _get_db()
    row = conn.execute("SELECT * FROM cpmp_deliverables WHERE id = ?", (deliverable_id,)).fetchone()
    if not row:
        conn.close()
        return {"status": "error", "message": f"Deliverable {deliverable_id} not found"}

    deliverable = dict(row)
    generations = conn.execute(
        "SELECT * FROM cpmp_cdrl_generations WHERE deliverable_id = ? ORDER BY created_at DESC",
        (deliverable_id,)
    ).fetchall()
    deliverable["generations"] = [dict(g) for g in generations]

    history = conn.execute(
        "SELECT * FROM cpmp_status_history WHERE entity_type = 'deliverable' AND entity_id = ? ORDER BY created_at DESC",
        (deliverable_id,)
    ).fetchall()
    deliverable["status_history"] = [dict(h) for h in history]

    conn.close()
    return {"status": "ok", "deliverable": deliverable}


def update_deliverable(deliverable_id, data):
    """Update mutable deliverable fields (not status — use transition_deliverable)."""
    conn = _get_db()
    row = conn.execute("SELECT id FROM cpmp_deliverables WHERE id = ?", (deliverable_id,)).fetchone()
    if not row:
        conn.close()
        return {"status": "error", "message": f"Deliverable {deliverable_id} not found"}

    updatable = ["cdrl_number", "did_number", "title", "description", "type",
                 "cdrl_type", "frequency", "due_date", "submitted_date", "accepted_date",
                 "rejected_date", "days_overdue", "generated_by_tool", "wbs_id", "notes"]
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
    conn.execute(f"UPDATE cpmp_deliverables SET {', '.join(sets)} WHERE id = ?", params)
    conn.commit()
    conn.close()
    return {"status": "ok", "deliverable_id": deliverable_id}


def transition_deliverable(deliverable_id, new_status, changed_by=None, reason=None):
    """Transition deliverable status with pipeline enforcement."""
    conn = _get_db()
    row = conn.execute("SELECT id, status FROM cpmp_deliverables WHERE id = ?", (deliverable_id,)).fetchone()
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
    conn.execute(f"UPDATE cpmp_deliverables SET {set_clauses} WHERE id = ?", params)
    _record_status_change(conn, "deliverable", deliverable_id, old_status, new_status, changed_by, reason)
    _audit(conn, "transition_deliverable", f"Deliverable {deliverable_id}: {old_status} → {new_status}")
    conn.commit()
    conn.close()
    return {"status": "ok", "deliverable_id": deliverable_id, "old_status": old_status, "new_status": new_status}


def compute_overdue_deliverables(contract_id=None):
    """Detect and mark overdue deliverables."""
    conn = _get_db()
    now_date = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    query = (
        "SELECT id, due_date, status FROM cpmp_deliverables "
        "WHERE due_date < ? AND status NOT IN ('accepted', 'overdue', 'rejected')"
    )
    params = [now_date]
    if contract_id:
        query += " AND contract_id = ?"
        params.append(contract_id)

    rows = conn.execute(query, params).fetchall()
    updated = 0
    for row in rows:
        due = datetime.fromisoformat(row["due_date"])
        days = (datetime.now(timezone.utc) - due.replace(tzinfo=timezone.utc)).days
        conn.execute(
            "UPDATE cpmp_deliverables SET status = 'overdue', days_overdue = ?, updated_at = ? WHERE id = ?",
            (days, _now(), row["id"]),
        )
        _record_status_change(conn, "deliverable", row["id"], row["status"], "overdue", "system", f"{days} days overdue")
        updated += 1

    _audit(conn, "compute_overdue", f"Marked {updated} deliverables as overdue")
    conn.commit()
    conn.close()
    return {"status": "ok", "overdue_count": updated}


# ── CLI ──────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="ICDEV GovProposal Contract Manager (Phase 60)")
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
    else:
        result = {"status": "error", "message": "Unknown command"}

    print(json.dumps(result, indent=2, default=str))


if __name__ == "__main__":
    main()
