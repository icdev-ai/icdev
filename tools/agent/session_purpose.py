# [TEMPLATE: CUI // SP-CTI]
"""Session Purpose Declaration — session-level intent tracking for audit.

Forces declaration of session purpose/intent before work begins. The purpose
is persisted in the DB and can be injected into agent system prompts as a
guardrail and traceability mechanism.

Decision D-ORCH-5: Session purpose for NIST AU-3 event detail traceability.
"""

import argparse
import hashlib
import json
import logging
import os
import sqlite3
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

DB_PATH = BASE_DIR / "data" / "icdev.db"

try:
    from tools.compat.db_utils import get_db_connection
except ImportError:
    get_db_connection = None

try:
    from tools.audit.audit_logger import log_event as audit_log_event
except ImportError:
    def audit_log_event(**kwargs):
        pass

logger = logging.getLogger("icdev.session_purpose")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _get_db(db_path=None) -> sqlite3.Connection:
    """Open a DB connection with row factory."""
    if get_db_connection:
        return get_db_connection(db_path or DB_PATH)
    path = db_path or DB_PATH
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    return conn


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def _ensure_table(db_path=None):
    """Create session_purposes table if not exists."""
    conn = _get_db(db_path)
    try:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS session_purposes (
                id TEXT PRIMARY KEY,
                project_id TEXT,
                purpose TEXT NOT NULL,
                purpose_hash TEXT NOT NULL,
                declared_by TEXT DEFAULT 'user',
                scope TEXT DEFAULT 'session' CHECK(scope IN ('session','workflow','task')),
                status TEXT DEFAULT 'active' CHECK(status IN ('active','completed','abandoned')),
                metadata TEXT DEFAULT '{}',
                created_at TEXT NOT NULL,
                completed_at TEXT
            )
        """)
        conn.commit()
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def declare(purpose: str, project_id: str = None, declared_by: str = "user",
            scope: str = "session", metadata: dict = None, db_path=None) -> dict:
    """Declare a session purpose.

    Args:
        purpose: The intent/purpose text.
        project_id: Optional project context.
        declared_by: Who declared (user, agent, system).
        scope: 'session', 'workflow', or 'task'.
        metadata: Optional key-value metadata.
        db_path: Optional database path override.

    Returns:
        Dict with purpose record.
    """
    _ensure_table(db_path)

    purpose_id = f"purpose-{uuid.uuid4().hex[:12]}"
    now = _now()

    conn = _get_db(db_path)
    try:
        conn.execute(
            """INSERT INTO session_purposes
               (id, project_id, purpose, purpose_hash, declared_by, scope, metadata, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (purpose_id, project_id, purpose, _hash(purpose),
             declared_by, scope, json.dumps(metadata or {}), now),
        )
        conn.commit()
    finally:
        conn.close()

    audit_log_event(
        event_type="session.purpose_declared",
        actor=declared_by,
        action=f"Session purpose declared: {purpose[:80]}",
        details={"purpose_id": purpose_id, "project_id": project_id,
                 "purpose_hash": _hash(purpose), "scope": scope},
        classification="CUI",
    )

    logger.info("Purpose declared: %s [%s]", purpose_id, purpose[:60])

    return {
        "id": purpose_id,
        "project_id": project_id,
        "purpose": purpose,
        "purpose_hash": _hash(purpose),
        "declared_by": declared_by,
        "scope": scope,
        "status": "active",
        "created_at": now,
    }


def get_active(project_id: str = None, db_path=None) -> dict:
    """Get the active session purpose.

    Args:
        project_id: Optional project context filter.
        db_path: Optional database path override.

    Returns:
        Active purpose dict, or None if no active purpose.
    """
    _ensure_table(db_path)

    conn = _get_db(db_path)
    try:
        query = "SELECT * FROM session_purposes WHERE status = 'active'"
        params = []
        if project_id:
            query += " AND project_id = ?"
            params.append(project_id)
        query += " ORDER BY created_at DESC, rowid DESC LIMIT 1"

        row = conn.execute(query, params).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def complete(purpose_id: str, db_path=None) -> bool:
    """Mark a purpose as completed.

    Args:
        purpose_id: The purpose ID to complete.
        db_path: Optional database path override.

    Returns:
        True if updated, False if not found.
    """
    _ensure_table(db_path)

    conn = _get_db(db_path)
    try:
        cursor = conn.execute(
            "UPDATE session_purposes SET status = 'completed', completed_at = ? WHERE id = ? AND status = 'active'",
            (_now(), purpose_id),
        )
        conn.commit()
        return cursor.rowcount > 0
    finally:
        conn.close()


def abandon(purpose_id: str, db_path=None) -> bool:
    """Mark a purpose as abandoned."""
    _ensure_table(db_path)

    conn = _get_db(db_path)
    try:
        cursor = conn.execute(
            "UPDATE session_purposes SET status = 'abandoned', completed_at = ? WHERE id = ? AND status = 'active'",
            (_now(), purpose_id),
        )
        conn.commit()
        return cursor.rowcount > 0
    finally:
        conn.close()


def get_prompt_injection(project_id: str = None, db_path=None) -> str:
    """Get a system prompt injection string for the active purpose.

    Designed to be appended to agent system prompts as a guardrail.

    Args:
        project_id: Optional project context filter.
        db_path: Optional database path override.

    Returns:
        Prompt injection string, or empty string if no active purpose.
    """
    active = get_active(project_id=project_id, db_path=db_path)
    if not active:
        return ""

    return (
        f"\n\n## Session Purpose (NIST AU-3 Traceability)\n"
        f"**Active Purpose:** {active['purpose']}\n"
        f"**Scope:** {active['scope']}\n"
        f"**Declared:** {active['created_at']}\n"
        f"All work in this session must align with this declared purpose. "
        f"If a request is unrelated to this purpose, note the deviation "
        f"in the audit trail before proceeding.\n"
    )


def history(project_id: str = None, limit: int = 20, db_path=None) -> list:
    """Get purpose history.

    Args:
        project_id: Optional project context filter.
        limit: Maximum records to return.
        db_path: Optional database path override.

    Returns:
        List of purpose dicts.
    """
    _ensure_table(db_path)

    conn = _get_db(db_path)
    try:
        query = "SELECT * FROM session_purposes"
        params = []
        if project_id:
            query += " WHERE project_id = ?"
            params.append(project_id)
        query += " ORDER BY created_at DESC LIMIT ?"
        params.append(limit)

        rows = conn.execute(query, params).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    """CLI for session purpose management."""
    parser = argparse.ArgumentParser(
        description="ICDEV Session Purpose — intent tracking for NIST AU-3 traceability"
    )
    parser.add_argument("--json", action="store_true", dest="json_output",
                        help="JSON output")
    parser.add_argument("--project-id", help="Project context")

    sub = parser.add_subparsers(dest="command", help="Purpose command")

    # Declare
    p_declare = sub.add_parser("declare", help="Declare session purpose")
    p_declare.add_argument("--purpose", required=True, help="Purpose text")
    p_declare.add_argument("--scope", default="session",
                           choices=["session", "workflow", "task"])
    p_declare.add_argument("--declared-by", default="user")

    # Active
    sub.add_parser("active", help="Get active purpose")

    # Complete
    p_complete = sub.add_parser("complete", help="Mark purpose completed")
    p_complete.add_argument("--purpose-id", required=True)

    # Abandon
    p_abandon = sub.add_parser("abandon", help="Mark purpose abandoned")
    p_abandon.add_argument("--purpose-id", required=True)

    # History
    p_history = sub.add_parser("history", help="Purpose history")
    p_history.add_argument("--limit", type=int, default=20)

    # Inject
    sub.add_parser("inject", help="Get prompt injection for active purpose")

    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        sys.exit(1)

    result = None

    if args.command == "declare":
        result = declare(
            purpose=args.purpose,
            project_id=args.project_id,
            declared_by=args.declared_by,
            scope=args.scope,
        )
    elif args.command == "active":
        result = get_active(project_id=args.project_id)
        if not result:
            result = {"status": "no_active_purpose"}
    elif args.command == "complete":
        success = complete(args.purpose_id)
        result = {"purpose_id": args.purpose_id, "completed": success}
    elif args.command == "abandon":
        success = abandon(args.purpose_id)
        result = {"purpose_id": args.purpose_id, "abandoned": success}
    elif args.command == "history":
        result = history(project_id=args.project_id, limit=args.limit)
    elif args.command == "inject":
        text = get_prompt_injection(project_id=args.project_id)
        if args.json_output:
            result = {"injection": text, "has_purpose": bool(text)}
        else:
            print(text if text else "(no active purpose)")
            sys.exit(0)

    print(json.dumps(result, indent=2, default=str))


if __name__ == "__main__":
    main()
