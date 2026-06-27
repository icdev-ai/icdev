# CUI // SP-CTI
# Classification: CUI — Controlled Unclassified Information
"""IDC IaC Twin Phase 1 — Snapshot Writer.

Persists IDC twin graph snapshots and compliance violations to SQLite
(or PostgreSQL via get_connection).

Tables:
    idc_twin_snapshots  — one row per imported terraform state / plan
    idc_twin_violations — one row per compliance finding per snapshot

NIST 800-53: AU-9 (Audit Information Protection), CM-8 (Component Inventory)
"""
import json
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional


_TWIN_SCHEMA = """
CREATE TABLE IF NOT EXISTS idc_twin_snapshots (
    id            TEXT PRIMARY KEY,
    source        TEXT NOT NULL DEFAULT 'terraform_show',
    graph_json    TEXT NOT NULL DEFAULT '{"nodes":[],"edges":[]}',
    node_count    INTEGER NOT NULL DEFAULT 0,
    classification TEXT NOT NULL DEFAULT 'CUI',
    created_at    TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS idc_twin_violations (
    id            TEXT PRIMARY KEY,
    snapshot_id   TEXT NOT NULL,
    rule_id       TEXT NOT NULL,
    title         TEXT NOT NULL DEFAULT '',
    severity      TEXT NOT NULL DEFAULT 'CAT3',
    category      TEXT NOT NULL DEFAULT 'general',
    detail        TEXT NOT NULL DEFAULT '',
    classification TEXT NOT NULL DEFAULT 'CUI',
    created_at    TEXT NOT NULL,
    FOREIGN KEY (snapshot_id) REFERENCES idc_twin_snapshots(id)
);

CREATE INDEX IF NOT EXISTS idx_idc_twin_violations_snap ON idc_twin_violations(snapshot_id);
"""


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _get_sqlite_conn(db_path: str) -> sqlite3.Connection:
    """Open a SQLite connection, create tables if needed."""
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    for stmt in _TWIN_SCHEMA.split(";"):
        stmt = stmt.strip()
        if stmt:
            conn.execute(stmt)
    conn.commit()
    return conn


def _resolve_conn(db_path: Optional[str]):
    """Return (conn, is_sqlite) — always SQLite in tests via db_path."""
    if db_path:
        return _get_sqlite_conn(db_path), True
    # Production: use icdev shared get_connection
    try:
        from tools.db.storage import get_connection
        conn = get_connection()
        # Ensure tables exist in shared DB (silently skip if already present)
        for stmt in _TWIN_SCHEMA.split(";"):
            stmt = stmt.strip()
            if stmt:
                try:
                    conn.execute(stmt)
                except (RuntimeError, ValueError):
                    # Raised by some PG wrappers when DDL is already applied
                    pass
        conn.commit()
        return conn, False
    except Exception:
        # Last resort — temp in-memory SQLite
        import tempfile
        tmp = str(Path(tempfile.gettempdir()) / "idc_twin.db")
        return _get_sqlite_conn(tmp), True


def write_snapshot(
    graph: dict,
    *,
    db_path: Optional[str] = None,
    source: str = "terraform_show",
    classification: str = "CUI",
) -> str:
    """Persist an IDC graph snapshot.

    Args:
        graph:          IDC graph dict {"nodes": [...], "edges": [...]}
        db_path:        Optional path to a SQLite file (used in tests).
                        If None, uses the shared get_connection().
        source:         Label for the snapshot origin
                        (e.g. "terraform_show", "terraform_plan").
        classification: CUI / SECRET / etc.

    Returns:
        snapshot_id (str) — unique ID for the stored row.
    """
    snapshot_id = "snap-" + uuid.uuid4().hex[:12]
    graph_json = json.dumps(graph)
    node_count = len(graph.get("nodes", []))
    created_at = _utcnow_iso()

    conn, is_sqlite = _resolve_conn(db_path)
    try:
        conn.execute(
            """
            INSERT INTO idc_twin_snapshots
                (id, source, graph_json, node_count, classification, created_at)
            VALUES (%s, %s, %s, %s, %s, %s)
            """,
            (snapshot_id, source, graph_json, node_count, classification, created_at),
        )
        conn.commit()
    finally:
        conn.close()

    return snapshot_id


def write_violations(
    snapshot_id: str,
    violations: list,
    *,
    db_path: Optional[str] = None,
    classification: str = "CUI",
) -> int:
    """Persist compliance violations for a snapshot.

    Args:
        snapshot_id:    ID returned by write_snapshot().
        violations:     List of finding dicts from infra_engine.assess_infra_design().
        db_path:        Optional SQLite path (used in tests).
        classification: CUI / SECRET / etc.

    Returns:
        Count of rows written.
    """
    if not violations:
        # Still ensure tables exist
        conn, _ = _resolve_conn(db_path)
        conn.close()
        return 0

    created_at = _utcnow_iso()
    conn, _ = _resolve_conn(db_path)
    try:
        for v in violations:
            vid = "viol-" + uuid.uuid4().hex[:10]
            conn.execute(
                """
                INSERT INTO idc_twin_violations
                    (id, snapshot_id, rule_id, title, severity, category, detail, classification, created_at)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    vid,
                    snapshot_id,
                    v.get("rule_id", "UNKNOWN"),
                    v.get("title", ""),
                    v.get("severity", "CAT3"),
                    v.get("category", "general"),
                    v.get("detail", ""),
                    classification,
                    created_at,
                ),
            )
        conn.commit()
    finally:
        conn.close()

    return len(violations)
