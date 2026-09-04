#!/usr/bin/env python3
# CUI // SP-CTI
"""Migration 179: SIPA — create the 5 integrity_* tables (sipa-db-03).

Idempotently creates the Software Integrity & Provenance Assessor schema on an
existing database (the monolithic init_icdev_db.py covers fresh DBs). These are
sensitive findings tables, so every table carries tenant_id + classification
(RLS-aware) and is created through the RLS-aware get_connection().

CHECK values are derived from tools/integrity/constants.py — when that module is
importable the predicates are built from it so SQL and Python stay in lock-step;
otherwise an inline fallback (identical values) is used so the migration runs on
branches where tools/integrity has not yet merged.

All tables but integrity_assessments are append-only (see APPEND_ONLY_TABLES in
.claude/hooks/pre_tool_use.py). DDL is SQLite-flavored; the migration runner's
SQL translator adapts it for PostgreSQL.
"""

import os
import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent.parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from tools.db.storage import get_connection

DB_PATH = os.environ.get("ICDEV_DB_PATH", str(BASE_DIR / "data" / "icdev.db"))


def _check(column: str, values) -> str:
    """Build a ``<column> IN ('a', 'b', ...)`` predicate from a values list."""
    joined = "', '".join(values)
    return f"{column} IN ('{joined}')"


# Prefer the single source of truth in constants.py; fall back to inline values
# (kept identical) so this migration is self-contained on un-merged branches.
try:
    from tools.integrity.constants import (
        ASSESS_MODES,
        ASSESS_STATUS,
        CAPABILITY_TYPES,
        FINDING_SCANNERS,
        FINDING_TYPES,
        SEVERITY,
        SOURCE_TYPES,
        VERDICTS,
    )
except Exception:  # pragma: no cover - exercised only when constants absent
    SOURCE_TYPES = ["local", "git", "unc", "uri"]
    ASSESS_MODES = ["provenance_aware", "provenance_blind", "auto"]
    ASSESS_STATUS = ["quarantine", "assessed", "approved", "rejected"]
    VERDICTS = ["allow", "review", "quarantine"]
    CAPABILITY_TYPES = [
        "network_egress", "filesystem", "process_exec", "dynamic_code",
        "crypto", "env_secret", "serialization", "obfuscation",
    ]
    FINDING_SCANNERS = [
        "sast", "secrets", "deps", "formal", "container", "semgrep",
        "capability", "reconciliation", "tamper",
    ]
    FINDING_TYPES = [
        "dangerous_api", "secret", "vuln_dependency", "unauthorized_capability",
        "undisclosed_capability", "tamper_mismatch", "known_bad_signature",
    ]
    SEVERITY = ["critical", "high", "medium", "low", "info"]

_CHK_SOURCE_TYPE = _check("source_type", SOURCE_TYPES)
_CHK_MODE = _check("mode", ASSESS_MODES)
_CHK_STATUS = _check("status", ASSESS_STATUS)
_CHK_VERDICT = _check("verdict", VERDICTS)
_CHK_CAPABILITY_TYPE = _check("capability_type", CAPABILITY_TYPES)
_CHK_SOURCE_SCANNER = _check("source_scanner", FINDING_SCANNERS)
_CHK_FINDING_TYPE = _check("finding_type", FINDING_TYPES)
_CHK_SEVERITY = _check("severity", SEVERITY)

DDL = [
    f"""
    CREATE TABLE IF NOT EXISTS integrity_assessments (
        id              INTEGER PRIMARY KEY AUTOINCREMENT,
        source_type     TEXT NOT NULL CHECK({_CHK_SOURCE_TYPE}),
        source_ref      TEXT NOT NULL,
        mode            TEXT NOT NULL CHECK({_CHK_MODE}),
        project_id      TEXT,
        session_id      TEXT,
        dir_digest      TEXT,
        status          TEXT NOT NULL DEFAULT 'quarantine' CHECK({_CHK_STATUS}),
        verdict         TEXT CHECK({_CHK_VERDICT} OR verdict IS NULL),
        risk_score      REAL DEFAULT 0,
        tenant_id       TEXT NOT NULL DEFAULT 'default',
        classification  TEXT NOT NULL DEFAULT 'CUI',
        created_by      TEXT NOT NULL DEFAULT 'system',
        created_at      TEXT DEFAULT CURRENT_TIMESTAMP,
        updated_at      TEXT DEFAULT CURRENT_TIMESTAMP
    )
    """,
    f"""
    CREATE TABLE IF NOT EXISTS integrity_capabilities (
        id              INTEGER PRIMARY KEY AUTOINCREMENT,
        assessment_id   INTEGER NOT NULL REFERENCES integrity_assessments(id) ON DELETE CASCADE,
        file_path       TEXT NOT NULL,
        function_name   TEXT,
        capability_type TEXT NOT NULL CHECK({_CHK_CAPABILITY_TYPE}),
        evidence        TEXT,
        line_start      INTEGER,
        line_end        INTEGER,
        risk_weight     REAL DEFAULT 0,
        tenant_id       TEXT NOT NULL DEFAULT 'default',
        classification  TEXT NOT NULL DEFAULT 'CUI',
        created_at      TEXT DEFAULT CURRENT_TIMESTAMP
    )
    """,
    f"""
    CREATE TABLE IF NOT EXISTS integrity_findings (
        id              INTEGER PRIMARY KEY AUTOINCREMENT,
        assessment_id   INTEGER NOT NULL REFERENCES integrity_assessments(id) ON DELETE CASCADE,
        source_scanner  TEXT NOT NULL CHECK({_CHK_SOURCE_SCANNER}),
        finding_type    TEXT NOT NULL CHECK({_CHK_FINDING_TYPE}),
        severity        TEXT NOT NULL CHECK({_CHK_SEVERITY}),
        file_path       TEXT,
        line            INTEGER,
        detail          TEXT,
        tenant_id       TEXT NOT NULL DEFAULT 'default',
        classification  TEXT NOT NULL DEFAULT 'CUI',
        created_at      TEXT DEFAULT CURRENT_TIMESTAMP
    )
    """,
    f"""
    CREATE TABLE IF NOT EXISTS integrity_verdicts (
        id              INTEGER PRIMARY KEY AUTOINCREMENT,
        assessment_id   INTEGER NOT NULL REFERENCES integrity_assessments(id) ON DELETE CASCADE,
        verdict         TEXT NOT NULL CHECK({_CHK_VERDICT}),
        risk_score      REAL DEFAULT 0,
        rationale       TEXT,
        decided_by      TEXT NOT NULL DEFAULT 'system',
        tenant_id       TEXT NOT NULL DEFAULT 'default',
        classification  TEXT NOT NULL DEFAULT 'CUI',
        created_at      TEXT DEFAULT CURRENT_TIMESTAMP
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS integrity_authorizations (
        id              INTEGER PRIMARY KEY AUTOINCREMENT,
        assessment_id   INTEGER NOT NULL REFERENCES integrity_assessments(id) ON DELETE CASCADE,
        capability_id   INTEGER REFERENCES integrity_capabilities(id) ON DELETE CASCADE,
        requirement_id  TEXT,
        claim_ref       TEXT,
        authorized      INTEGER NOT NULL DEFAULT 0,
        reason          TEXT,
        reviewed_by     TEXT,
        tenant_id       TEXT NOT NULL DEFAULT 'default',
        classification  TEXT NOT NULL DEFAULT 'CUI',
        created_at      TEXT DEFAULT CURRENT_TIMESTAMP
    )
    """,
]

INDEX_DDL = [
    "CREATE INDEX IF NOT EXISTS idx_integrity_assessments_tenant ON integrity_assessments(tenant_id)",
    "CREATE INDEX IF NOT EXISTS idx_integrity_assessments_project ON integrity_assessments(project_id)",
    "CREATE INDEX IF NOT EXISTS idx_integrity_capabilities_assessment ON integrity_capabilities(assessment_id)",
    "CREATE INDEX IF NOT EXISTS idx_integrity_capabilities_tenant ON integrity_capabilities(tenant_id)",
    "CREATE INDEX IF NOT EXISTS idx_integrity_findings_assessment ON integrity_findings(assessment_id)",
    "CREATE INDEX IF NOT EXISTS idx_integrity_findings_tenant ON integrity_findings(tenant_id)",
    "CREATE INDEX IF NOT EXISTS idx_integrity_verdicts_assessment ON integrity_verdicts(assessment_id)",
    "CREATE INDEX IF NOT EXISTS idx_integrity_verdicts_tenant ON integrity_verdicts(tenant_id)",
    "CREATE INDEX IF NOT EXISTS idx_integrity_authorizations_assessment ON integrity_authorizations(assessment_id)",
    "CREATE INDEX IF NOT EXISTS idx_integrity_authorizations_tenant ON integrity_authorizations(tenant_id)",
]

_TABLES = [
    "integrity_authorizations",
    "integrity_verdicts",
    "integrity_findings",
    "integrity_capabilities",
    "integrity_assessments",
]


def up(conn=None):
    """Accept the runner's connection; open one only when called standalone.

    This was `def up()` with no parameters. MigrationRunner calls `mod.up(conn)`,
    so promoting the file to a discoverable migration without this would make it
    run and immediately raise TypeError — a migration that fails on first
    execution is worse than one that never executes, and it was invisible for
    long enough that nobody found out.

    Preferring the passed connection also puts the DDL in the runner's
    transaction and stops this function closing a connection it did not open.
    """
    _own = conn is None
    if _own:
        conn = get_connection(db_path=DB_PATH)
    try:
        for ddl in DDL:
            conn.execute(ddl)
        for idx in INDEX_DDL:
            conn.execute(idx)
        conn.commit()
        print("[migration 179] integrity_* tables created (5 tables)")
    finally:
        if _own:
            conn.close()


def down():
    conn = get_connection(db_path=DB_PATH)
    try:
        for table in _TABLES:  # children first (FK order)
            conn.execute(f"DROP TABLE IF EXISTS {table}")
        conn.commit()
        print("[migration 179] integrity_* tables dropped")
    finally:
        conn.close()


if __name__ == "__main__":
    up()
