# CUI // SP-CTI
"""AI-ify Canvas — DB initializer.

Dual-backend: PostgreSQL (default) or SQLite fallback.
DB file: data/aiify_canvas.db  |  env: AIIFY_STORAGE_BACKEND, AIIFY_DB_PATH
"""
from __future__ import annotations

import os
from pathlib import Path

from tools.logging.icdev_logger import get_logger

from tools.aiify.constants import (
    SUPPORTED_LANGUAGES,
    CHECK_PATTERN_TYPE,
    CHECK_AI_PARADIGM,
    CHECK_AI_READINESS,
    CHECK_CATEGORY,
    CHECK_OVERALL_AI_READINESS,
)

logger = get_logger(__name__)

_ICDEV_ROOT = Path(__file__).resolve().parents[3]
DB_PATH = Path(os.environ.get("AIIFY_DB_PATH", str(_ICDEV_ROOT / "data" / "aiify_canvas.db")))

# Backend is resolved at call time (see _backend()) so that env overrides — set
# by tests via monkeypatch, or by an operator between processes — take effect
# without a module reload. The module-level constant is retained for callers /
# tests that import it, but the functions below never read it directly.
_AIIFY_BACKEND = os.environ.get(
    "AIIFY_STORAGE_BACKEND",
    os.environ.get("ICDEV_CANVAS_STORAGE_BACKEND", "postgresql"),
).lower()

_TRUE_SET = {"1", "true", "yes", "on"}

_lang_list = ", ".join(f"'{lang}'" for lang in SUPPORTED_LANGUAGES)
_CHECK_LANGUAGE = f"language IN ({_lang_list})"


def _backend() -> str:
    """Resolve the storage backend from env at call time."""
    return os.environ.get(
        "AIIFY_STORAGE_BACKEND",
        os.environ.get("ICDEV_CANVAS_STORAGE_BACKEND", "postgresql"),
    ).lower()


def _sqlite_fallback_allowed() -> bool:
    """Whether an explicit opt-in permits SQLite fallback on a PG failure.

    Mirrors the fail-closed-capable posture of the TRUST redaction toggles: the
    dangerous behavior (silently splitting canvas data across two stores when
    PostgreSQL is transiently unreachable) is OFF unless an operator explicitly
    sets AIIFY_ALLOW_SQLITE_FALLBACK=true.
    """
    return os.environ.get("AIIFY_ALLOW_SQLITE_FALLBACK", "").strip().lower() in _TRUE_SET


def _sqlite_connection():
    import sqlite3
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def get_connection():
    """Return a canvas DB connection.

    On the PostgreSQL path a connection failure is FAIL-CLOSED by default: the
    exception re-raises rather than silently degrading to a separate SQLite
    store (which would fork canvas data and mask a real outage). SQLite fallback
    happens only when AIIFY_ALLOW_SQLITE_FALLBACK is explicitly enabled, and is
    logged loudly. When the backend is explicitly SQLite, this branch is skipped
    entirely and behaviour is unchanged.
    """
    if _backend() == "postgresql":
        try:
            from tools.db.storage import get_canvas_connection
            return get_canvas_connection("AIIFY_PG_DATABASE")
        except Exception as exc:
            if not _sqlite_fallback_allowed():
                logger.error(
                    "aiify: PostgreSQL connection failed and AIIFY_ALLOW_SQLITE_FALLBACK "
                    "is not set — refusing silent SQLite fallback (fail-closed): %s",
                    exc,
                )
                raise
            logger.warning(
                "aiify: PostgreSQL connection failed; AIIFY_ALLOW_SQLITE_FALLBACK is set — "
                "falling back to SQLite at %s. Canvas data may split across two stores "
                "until PostgreSQL is restored: %s",
                DB_PATH, exc,
            )
    return _sqlite_connection()


# Static DDL — no dynamic expressions; must appear as plain string literals so
# the gap-detector AST scanner can find the CREATE TABLE declarations.
_SCHEMA_PG_PRE = f"""
CREATE TABLE IF NOT EXISTS aiify_scans (
    scan_id          SERIAL PRIMARY KEY,
    input_type       TEXT NOT NULL,
    input_ref        TEXT NOT NULL,
    language_profile JSONB,
    total_files      INTEGER DEFAULT 0,
    total_loc        INTEGER DEFAULT 0,
    status           TEXT NOT NULL DEFAULT 'pending' CHECK(status IN ('pending','running','completed','failed')),
    project_summary  TEXT,
    overall_verdict      TEXT,
    overall_ai_readiness TEXT CHECK({CHECK_OVERALL_AI_READINESS} OR overall_ai_readiness IS NULL),
    overall_rationale    TEXT,
    created_at       TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    completed_at     TIMESTAMP
)"""

# aiify_opportunities uses CHECK constraints derived from Python constants, so
# this table's DDL must be an f-string.  It is placed between _SCHEMA_PG_PRE
# and _SCHEMA_PG_POST so that foreign-key dependency order is preserved:
# aiify_scans → aiify_opportunities → aiify_scores.
_SCHEMA_PG_OPPS = f"""
CREATE TABLE IF NOT EXISTS aiify_opportunities (
    opportunity_id       SERIAL PRIMARY KEY,
    scan_id              INTEGER NOT NULL REFERENCES aiify_scans(scan_id) ON DELETE CASCADE,
    module_path          TEXT NOT NULL,
    function_name        TEXT NOT NULL,
    line_start           INTEGER,
    line_end             INTEGER,
    language             TEXT NOT NULL CHECK({_CHECK_LANGUAGE}),
    pattern_type         TEXT NOT NULL CHECK({CHECK_PATTERN_TYPE}),
    pattern_detail       JSONB,
    ai_paradigm          TEXT NOT NULL CHECK({CHECK_AI_PARADIGM}),
    il_recommended_model TEXT,
    data_requirements    JSONB,
    created_at           TIMESTAMP DEFAULT CURRENT_TIMESTAMP
)"""

# Static DDL — plain string so the gap-detector finds all CREATE TABLE names.
_SCHEMA_PG_POST = f"""
CREATE TABLE IF NOT EXISTS aiify_scores (
    score_id          SERIAL PRIMARY KEY,
    opportunity_id    INTEGER NOT NULL REFERENCES aiify_opportunities(opportunity_id) ON DELETE CASCADE,
    value_score       REAL,
    feasibility_score REAL,
    risk_score        REAL,
    composite_score   REAL,
    score_detail      JSONB,
    verdict           TEXT,
    ai_readiness      TEXT CHECK({CHECK_AI_READINESS} OR ai_readiness IS NULL),
    rationale         TEXT,
    pros              JSONB,
    cons              JSONB,
    category          TEXT CHECK({CHECK_CATEGORY} OR category IS NULL),
    scored_at         TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS aiify_roadmaps (
    id                SERIAL PRIMARY KEY,
    scan_id           INTEGER NOT NULL REFERENCES aiify_scans(scan_id) ON DELETE CASCADE,
    roadmap_id        TEXT NOT NULL UNIQUE,
    title             TEXT NOT NULL,
    phases            JSONB,
    total_effort_days INTEGER DEFAULT 0,
    aimc_links        JSONB,
    aadc_links        JSONB,
    created_at        TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS aiify_audit_log (
    id         SERIAL PRIMARY KEY,
    event_type TEXT NOT NULL,
    scan_id    INTEGER REFERENCES aiify_scans(scan_id) ON DELETE SET NULL,
    actor      TEXT NOT NULL DEFAULT 'system',
    detail     JSONB,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS aiify_hitl_decisions (
    id          SERIAL PRIMARY KEY,
    source_type TEXT NOT NULL CHECK(source_type IN ('innovation','creative','research','prd')),
    source_id   TEXT NOT NULL,
    phase_id    TEXT,
    decision    TEXT NOT NULL CHECK(decision IN ('accept','reject')),
    reason      TEXT,
    actor       TEXT NOT NULL DEFAULT 'user',
    decided_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS aiify_posture_snapshots (
    id                SERIAL PRIMARY KEY,
    overall_score     REAL DEFAULT 0,
    grade             TEXT DEFAULT 'F',
    posture           TEXT DEFAULT 'critical',
    scan_count        INTEGER DEFAULT 0,
    opportunity_count INTEGER DEFAULT 0,
    dimensions_json   JSONB,
    snapshot_json     JSONB,
    created_at        TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS aiify_prd_provenance (
    id                SERIAL PRIMARY KEY,
    roadmap_id        TEXT NOT NULL,
    phase_id          TEXT NOT NULL,
    ai_boosted        INTEGER NOT NULL DEFAULT 0,
    generation_model  TEXT,
    citation_valid    INTEGER NOT NULL DEFAULT 1,
    citation_report   JSONB,
    evidence_sources  JSONB,
    provenance        JSONB,
    created_at        TIMESTAMP DEFAULT CURRENT_TIMESTAMP
)"""

SCHEMA_PG = _SCHEMA_PG_PRE + ";\n" + _SCHEMA_PG_OPPS + ";\n" + _SCHEMA_PG_POST

SCHEMA_SQLITE = (
    SCHEMA_PG
    .replace("SERIAL PRIMARY KEY", "INTEGER PRIMARY KEY AUTOINCREMENT")
    .replace("JSONB", "TEXT")
    .replace("TIMESTAMP DEFAULT CURRENT_TIMESTAMP", "TEXT DEFAULT CURRENT_TIMESTAMP")
)


def init_db() -> None:
    backend = _backend()
    conn = get_connection()
    schema = SCHEMA_PG if backend == "postgresql" else SCHEMA_SQLITE
    try:
        for stmt in schema.split(";"):
            stmt = stmt.strip()
            if stmt:
                conn.execute(stmt)
        conn.commit()
        # Lazy migration: add columns that may be missing on pre-existing DBs.
        _lazy_cols = [
            ("aiify_scans", "project_summary", "TEXT"),
            ("aiify_scans", "overall_verdict", "TEXT"),
            ("aiify_scans", "overall_ai_readiness", "TEXT"),
            ("aiify_scans", "overall_rationale", "TEXT"),
            ("aiify_scores", "verdict", "TEXT"),
            ("aiify_scores", "ai_readiness", "TEXT"),
            ("aiify_scores", "rationale", "TEXT"),
            ("aiify_scores", "pros", "TEXT"),
            ("aiify_scores", "cons", "TEXT"),
            ("aiify_scores", "category", "TEXT"),
        ]
        for _t, _c, _ty in _lazy_cols:
            try:
                conn.execute(f"ALTER TABLE {_t} ADD COLUMN {_c} {_ty}")
                conn.commit()
            except Exception:
                conn.rollback()
        print(f"[init_db] AI-ify schema ready ({backend})")
    finally:
        conn.close()


if __name__ == "__main__":
    init_db()
