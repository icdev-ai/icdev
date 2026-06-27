#!/usr/bin/env python3
# CUI // SP-CTI
"""PIR/CCIR Manager — CRUD for Priority Intelligence Requirements.

Manages the sg_pir_requirements table which tracks PIR, CCIR, and EEI
collection requirements for Strategos intelligence operations.

Usage:
    python tools/intelligence/pir_manager.py --list --json
    python tools/intelligence/pir_manager.py --get <id> --json
    python tools/intelligence/pir_manager.py --create --pir-type PIR --topic "Enemy armor" --priority 1 --json
    python tools/intelligence/pir_manager.py --update <id> --status satisfied --json
"""
import argparse
import json
import sys
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from tools.db.storage import get_connection

PIR_TYPES = ("PIR", "CCIR", "EEI")
STATUSES = ("active", "satisfied", "cancelled")
PRIORITY_RANGE = range(1, 6)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _row(row) -> Dict[str, Any]:
    if row is None:
        return {}
    return dict(row) if hasattr(row, "keys") else {}


# ---------------------------------------------------------------------------
# Read operations
# ---------------------------------------------------------------------------

def list_pirs(
    pir_type: Optional[str] = None,
    status: Optional[str] = None,
    limit: int = 200,
) -> List[Dict[str, Any]]:
    """Return PIRs/CCIRs ordered by collection_priority then created_at."""
    conn = get_connection()
    try:
        clauses: List[str] = []
        params: List[Any] = []
        if pir_type:
            clauses.append("pir_type = ?")
            params.append(pir_type)
        if status:
            clauses.append("status = ?")
            params.append(status)
        where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
        sql = (
            f"SELECT * FROM sg_pir_requirements {where} "  # nosec B608 — where clause built from validated allowlist
            "ORDER BY collection_priority ASC, created_at DESC "
            "LIMIT ?"
        )
        params.append(limit)
        rows = conn.execute(sql, params).fetchall()
        return [_row(r) for r in rows]
    except Exception as exc:
        if "no such table" in str(exc):
            return []
        raise
    finally:
        conn.close()


def get_pir(pir_id: str) -> Optional[Dict[str, Any]]:
    """Return a single PIR by ID, or None if not found."""
    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT * FROM sg_pir_requirements WHERE id = %s", (pir_id,)
        ).fetchone()
        return _row(row) if row else None
    finally:
        conn.close()


def coverage_gap_hours(pirs: List[Dict[str, Any]]) -> Dict[str, float]:
    """Return a mapping of PIR id → hours since last satisfied update.

    A PIR that was never satisfied gets a sentinel value of -1 (unknown gap).
    Currently uses updated_at as a proxy for last coverage timestamp; a
    dedicated coverage_log table can be added later for finer granularity.
    """
    now = datetime.now(timezone.utc)
    gaps: Dict[str, float] = {}
    for p in pirs:
        if p.get("status") == "satisfied":
            try:
                updated = datetime.fromisoformat(p["updated_at"].replace("Z", "+00:00"))
                gaps[p["id"]] = (now - updated).total_seconds() / 3600
            except Exception:
                gaps[p["id"]] = -1.0
        else:
            gaps[p["id"]] = -1.0
    return gaps


# ---------------------------------------------------------------------------
# Write operations
# ---------------------------------------------------------------------------

def create_pir(
    pir_type: str,
    topic: str,
    description: Optional[str] = None,
    collection_priority: int = 3,
    tasked_to: Optional[str] = None,
    due_by: Optional[str] = None,
) -> Dict[str, Any]:
    """Insert a new PIR/CCIR/EEI row and return the created record."""
    if pir_type not in PIR_TYPES:
        raise ValueError(f"pir_type must be one of {PIR_TYPES}")
    if collection_priority not in PRIORITY_RANGE:
        raise ValueError("collection_priority must be 1–5")
    if not topic or not topic.strip():
        raise ValueError("topic is required")

    pir_id = str(uuid.uuid4())
    now = _now()
    conn = get_connection()
    try:
        conn.execute(
            """INSERT INTO sg_pir_requirements
               (id, pir_type, topic, description, collection_priority,
                status, tasked_to, due_by, created_at, updated_at)
               VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
            (
                pir_id, pir_type, topic.strip(), description,
                collection_priority, "active", tasked_to, due_by, now, now,
            ),
        )
        conn.commit()
        return get_pir(pir_id) or {}
    finally:
        conn.close()


def update_pir(pir_id: str, **fields) -> Optional[Dict[str, Any]]:
    """Patch a PIR with the provided fields.  Only known columns are updated.

    Allowed fields: topic, description, collection_priority, status,
                    tasked_to, due_by, pir_type
    """
    allowed = {
        "pir_type", "topic", "description", "collection_priority",
        "status", "tasked_to", "due_by",
    }
    updates = {k: v for k, v in fields.items() if k in allowed and v is not None}
    if not updates:
        return get_pir(pir_id)

    if "pir_type" in updates and updates["pir_type"] not in PIR_TYPES:
        raise ValueError(f"pir_type must be one of {PIR_TYPES}")
    if "status" in updates and updates["status"] not in STATUSES:
        raise ValueError(f"status must be one of {STATUSES}")
    if "collection_priority" in updates:
        cp = int(updates["collection_priority"])
        if cp not in PRIORITY_RANGE:
            raise ValueError("collection_priority must be 1–5")
        updates["collection_priority"] = cp

    updates["updated_at"] = _now()
    set_clause = ", ".join(f"{col} = ?" for col in updates)
    params = list(updates.values()) + [pir_id]

    conn = get_connection()
    try:
        result = conn.execute(
            f"UPDATE sg_pir_requirements SET {set_clause} WHERE id = ?",  # nosec B608 — set_clause from validated allowlist
            params
        )
        conn.commit()
        if result.rowcount == 0:
            return None
        return get_pir(pir_id)
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _cli() -> None:
    parser = argparse.ArgumentParser(description="PIR/CCIR Manager CLI")
    parser.add_argument("--list", action="store_true")
    parser.add_argument("--get", metavar="ID")
    parser.add_argument("--create", action="store_true")
    parser.add_argument("--update", metavar="ID")
    parser.add_argument("--pir-type", choices=list(PIR_TYPES))
    parser.add_argument("--topic")
    parser.add_argument("--description")
    parser.add_argument("--priority", type=int, default=3)
    parser.add_argument("--status", choices=list(STATUSES))
    parser.add_argument("--tasked-to")
    parser.add_argument("--due-by")
    parser.add_argument("--filter-type", choices=list(PIR_TYPES))
    parser.add_argument("--filter-status", choices=list(STATUSES))
    parser.add_argument("--json", action="store_true", dest="as_json")
    args = parser.parse_args()

    out: Any = None
    if args.list:
        out = list_pirs(pir_type=args.filter_type, status=args.filter_status)
    elif args.get:
        out = get_pir(args.get)
    elif args.create:
        if not args.topic:
            parser.error("--topic required for --create")
        out = create_pir(
            pir_type=args.pir_type or "PIR",
            topic=args.topic,
            description=args.description,
            collection_priority=args.priority,
            tasked_to=args.tasked_to,
            due_by=args.due_by,
        )
    elif args.update:
        out = update_pir(
            args.update,
            pir_type=args.pir_type,
            topic=args.topic,
            description=args.description,
            collection_priority=args.priority if args.priority != 3 else None,
            status=args.status,
            tasked_to=args.tasked_to,
            due_by=args.due_by,
        )
    else:
        parser.print_help()
        sys.exit(1)

    if args.as_json:
        print(json.dumps(out, indent=2, default=str))
    else:
        print(out)


if __name__ == "__main__":
    _cli()
