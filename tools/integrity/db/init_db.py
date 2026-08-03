# CUI // SP-CTI
"""SIPA — Software Integrity & Provenance Assessor — DB initializer.

These are **sensitive findings** tables, so every table carries ``tenant_id`` +
``classification`` columns and is accessed through the RLS-aware
``tools.db.storage.get_connection()`` (NOT ``get_canvas_connection``) so the
global row-level-security predicate is enforced on every query.

Dual-backend: PostgreSQL (default) with a SQLite fallback. ``SCHEMA_PG`` is the
single source of DDL truth; ``SCHEMA_SQLITE`` is generated from it via ``.replace()``
transforms so the two backends never drift. The schema actually executed is
selected from the live connection's backend so it can never mismatch.

Append-only tables (``integrity_capabilities``, ``integrity_findings``,
``integrity_verdicts``, ``integrity_authorizations``) are never UPDATEd/DELETEd —
this is additionally enforced at the hook layer (``APPEND_ONLY_TABLES`` in
``.claude/hooks/pre_tool_use.py``). Only ``integrity_assessments`` carries a
mutable lifecycle (status/verdict/risk_score/updated_at).

Every CHECK constraint is derived from the enums in
``tools/integrity/constants.py`` — never hardcode the allowed values here.
"""
from __future__ import annotations

import os
import sys

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


def _check(column: str, values: list[str]) -> str:
    """Build a ``<column> IN ('a', 'b', ...)`` predicate from a constants list.

    Keeps the allowed-value set single-sourced in ``constants.py`` while letting
    each table use its own column name.
    """
    joined = "', '".join(values)
    return f"{column} IN ('{joined}')"


# CHECK predicates — values sourced from constants, column names per the schema.
_CHK_SOURCE_TYPE     = _check("source_type", SOURCE_TYPES)
_CHK_MODE            = _check("mode", ASSESS_MODES)
_CHK_STATUS          = _check("status", ASSESS_STATUS)
_CHK_VERDICT         = _check("verdict", VERDICTS)
_CHK_CAPABILITY_TYPE = _check("capability_type", CAPABILITY_TYPES)
_CHK_SOURCE_SCANNER  = _check("source_scanner", FINDING_SCANNERS)
_CHK_FINDING_TYPE    = _check("finding_type", FINDING_TYPES)
_CHK_SEVERITY        = _check("severity", SEVERITY)


# --------------------------------------------------------------------------- #
# PostgreSQL DDL (canonical). SQLite is derived from this via .replace() below.
# --------------------------------------------------------------------------- #
SCHEMA_PG = f"""
CREATE TABLE IF NOT EXISTS integrity_assessments (
    id              SERIAL PRIMARY KEY,
    source_type     TEXT NOT NULL CHECK ({_CHK_SOURCE_TYPE}),
    source_ref      TEXT NOT NULL,
    mode            TEXT NOT NULL CHECK ({_CHK_MODE}),
    project_id      TEXT,
    session_id      TEXT,
    dir_digest      TEXT,
    status          TEXT NOT NULL DEFAULT 'quarantine' CHECK ({_CHK_STATUS}),
    verdict         TEXT CHECK ({_CHK_VERDICT} OR verdict IS NULL),
    risk_score      REAL DEFAULT 0,
    tenant_id       TEXT NOT NULL DEFAULT 'default',
    classification  TEXT NOT NULL DEFAULT 'CUI',
    created_by      TEXT NOT NULL DEFAULT 'system',
    created_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_integrity_assessments_tenant ON integrity_assessments(tenant_id);
CREATE INDEX IF NOT EXISTS idx_integrity_assessments_project ON integrity_assessments(project_id);

CREATE TABLE IF NOT EXISTS integrity_capabilities (
    id              SERIAL PRIMARY KEY,
    assessment_id   INTEGER NOT NULL REFERENCES integrity_assessments(id) ON DELETE CASCADE,
    file_path       TEXT NOT NULL,
    function_name   TEXT,
    capability_type TEXT NOT NULL CHECK ({_CHK_CAPABILITY_TYPE}),
    evidence        JSONB,
    line_start      INTEGER,
    line_end        INTEGER,
    risk_weight     REAL DEFAULT 0,
    tenant_id       TEXT NOT NULL DEFAULT 'default',
    classification  TEXT NOT NULL DEFAULT 'CUI',
    created_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_integrity_capabilities_assessment ON integrity_capabilities(assessment_id);
CREATE INDEX IF NOT EXISTS idx_integrity_capabilities_tenant ON integrity_capabilities(tenant_id);

CREATE TABLE IF NOT EXISTS integrity_findings (
    id              SERIAL PRIMARY KEY,
    assessment_id   INTEGER NOT NULL REFERENCES integrity_assessments(id) ON DELETE CASCADE,
    source_scanner  TEXT NOT NULL CHECK ({_CHK_SOURCE_SCANNER}),
    finding_type    TEXT NOT NULL CHECK ({_CHK_FINDING_TYPE}),
    severity        TEXT NOT NULL CHECK ({_CHK_SEVERITY}),
    file_path       TEXT,
    line            INTEGER,
    detail          JSONB,
    tenant_id       TEXT NOT NULL DEFAULT 'default',
    classification  TEXT NOT NULL DEFAULT 'CUI',
    created_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_integrity_findings_assessment ON integrity_findings(assessment_id);
CREATE INDEX IF NOT EXISTS idx_integrity_findings_tenant ON integrity_findings(tenant_id);

CREATE TABLE IF NOT EXISTS integrity_verdicts (
    id              SERIAL PRIMARY KEY,
    assessment_id   INTEGER NOT NULL REFERENCES integrity_assessments(id) ON DELETE CASCADE,
    verdict         TEXT NOT NULL CHECK ({_CHK_VERDICT}),
    risk_score      REAL DEFAULT 0,
    rationale       JSONB,
    decided_by      TEXT NOT NULL DEFAULT 'system',
    tenant_id       TEXT NOT NULL DEFAULT 'default',
    classification  TEXT NOT NULL DEFAULT 'CUI',
    created_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_integrity_verdicts_assessment ON integrity_verdicts(assessment_id);
CREATE INDEX IF NOT EXISTS idx_integrity_verdicts_tenant ON integrity_verdicts(tenant_id);

CREATE TABLE IF NOT EXISTS integrity_authorizations (
    id              SERIAL PRIMARY KEY,
    assessment_id   INTEGER NOT NULL REFERENCES integrity_assessments(id) ON DELETE CASCADE,
    capability_id   INTEGER REFERENCES integrity_capabilities(id) ON DELETE CASCADE,
    requirement_id  TEXT,
    claim_ref       TEXT,
    authorized      BOOLEAN NOT NULL DEFAULT FALSE,
    reason          TEXT,
    reviewed_by     TEXT,
    tenant_id       TEXT NOT NULL DEFAULT 'default',
    classification  TEXT NOT NULL DEFAULT 'CUI',
    created_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_integrity_authorizations_assessment ON integrity_authorizations(assessment_id);
CREATE INDEX IF NOT EXISTS idx_integrity_authorizations_tenant ON integrity_authorizations(tenant_id);
"""


# SQLite-flavored DDL — derived from SCHEMA_PG so the two backends never drift.
SCHEMA_SQLITE = (
    SCHEMA_PG
    .replace("SERIAL PRIMARY KEY", "INTEGER PRIMARY KEY AUTOINCREMENT")
    .replace("JSONB", "TEXT")
    .replace("TIMESTAMP DEFAULT CURRENT_TIMESTAMP", "TEXT DEFAULT CURRENT_TIMESTAMP")
    .replace("BOOLEAN NOT NULL DEFAULT FALSE", "INTEGER NOT NULL DEFAULT 0")
)


def _backend_of(conn) -> str:
    """Resolve the backend ('postgresql' | 'sqlite') of a live connection.

    Prefer the connection's own declared backend so the executed schema can never
    mismatch the connection; fall back to the env var (default 'postgresql').
    """
    declared = getattr(conn, "_backend", None)
    if declared:
        return "postgresql" if str(declared).lower().startswith(("postgre", "pg")) else "sqlite"
    backend = os.environ.get("ICDEV_STORAGE_BACKEND", "postgresql").lower()
    return "postgresql" if backend in ("postgresql", "postgres", "pg") else "sqlite"


def init_db(conn=None) -> None:
    """Create all SIPA integrity tables. Idempotent (CREATE TABLE IF NOT EXISTS)
    and graceful if the schema already exists.

    Pass ``conn`` to reuse an existing connection (e.g. tests); otherwise an
    RLS-aware connection is opened via ``get_connection()`` and closed on exit.
    """
    from tools.db.storage import get_connection

    own_conn = conn is None
    if own_conn:
        conn = get_connection()

    try:
        schema = SCHEMA_PG if _backend_of(conn) == "postgresql" else SCHEMA_SQLITE
        cur = conn.cursor()
        for stmt in schema.split(";"):
            stmt = stmt.strip()
            if stmt:
                cur.execute(stmt)
        conn.commit()
    finally:
        if own_conn:
            conn.close()


if __name__ == "__main__":
    init_db()
    print("[init_db] SIPA integrity schema ready", file=sys.stderr)
