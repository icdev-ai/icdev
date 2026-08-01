# CUI // SP-CTI
"""AI-ify Canvas — DB initializer.

Dual-backend: PostgreSQL (default) or SQLite fallback.
DB file: data/aiify_canvas.db  |  env: AIIFY_STORAGE_BACKEND, AIIFY_DB_PATH
"""
from __future__ import annotations

import os
import sys
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


def _sqlite_storage_connection():
    """Wrap the raw SQLite connection in a translating StorageConnection.

    Returns a ``StorageConnection`` (RLS disabled) bound to the same
    ``_sqlite_connection()`` (PRAGMA foreign_keys=ON + WAL preserved). Because it
    translates ``%s`` → ``?`` at execute time, callers author ONE PG-native
    placeholder style that flows through this seam on SQLite exactly as it does on
    PostgreSQL — this is what lets engine.py drop its blind ``_exec`` retry
    (penta-aiify-06).
    """
    from tools.db.storage import StorageConnection
    conn = StorageConnection(_sqlite_connection(), "sqlite")
    conn.set_security_context(None)  # rls-bypass: canvas tables have no tenant_id/classification columns, so RLS injection would raise UndefinedColumn on every query
    return conn


def get_connection():
    """Return a translating canvas DB connection (StorageConnection).

    Both backends return a translating ``StorageConnection`` so a single
    ``%s`` placeholder style works everywhere:

      * PostgreSQL — the shared icdev DB via ``get_canvas_connection`` (RLS off).
        A connection failure is FAIL-CLOSED by default: the exception re-raises
        rather than silently degrading to a separate SQLite store. SQLite fallback
        happens only when AIIFY_ALLOW_SQLITE_FALLBACK is explicitly enabled.
      * SQLite — the dedicated ``.db`` at ``DB_PATH``, wrapped so ``%s`` → ``?``
        translation happens transparently.
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
    return _sqlite_storage_connection()


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


# Directory of versioned .sql migrations (registry-declared: component_registry
# aiify.db_migration = tools/aiify/db/migrations). Files apply in filename order,
# each tracked once in aiify_schema_migrations.
_MIGRATIONS_DIR = Path(__file__).resolve().parent / "migrations"

# Columns that pre-date-feature DBs may lack. Introspection-based backfill (see
# _ensure_columns) — replaces the former blind ALTER-in-try/except loop with an
# orderly "add only if missing" reconcile that never swallows real errors.
_BACKFILL_COLUMNS: list[tuple[str, str, str]] = [
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


def _existing_columns(conn, table: str) -> set[str]:
    """Return the set of column names on ``table`` (empty if it doesn't exist).

    Uses PRAGMA table_info on SQLite; the translating StorageConnection rewrites
    it to an information_schema query on PostgreSQL. Best-effort by design.
    """
    try:
        rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
        cols: set[str] = set()
        for r in rows:
            if hasattr(r, "keys"):
                cols.add(r["name"])
            else:
                cols.add(r[1])
        return cols
    except Exception:
        return set()


def _ensure_columns(conn) -> None:
    """Add any missing backfill columns, checking existence first (idempotent).

    Unlike the prior blind ``try: ALTER … except: rollback`` loop, this queries
    the live column set and only issues an ALTER for a genuinely absent column,
    so a real DDL error is no longer masked as a benign "already exists".
    """
    by_table: dict[str, list[tuple[str, str]]] = {}
    for table, col, coltype in _BACKFILL_COLUMNS:
        by_table.setdefault(table, []).append((col, coltype))
    for table, cols in by_table.items():
        present = _existing_columns(conn, table)
        if not present:
            continue  # table absent (fresh schema already created it with all cols)
        for col, coltype in cols:
            if col not in present:
                conn.execute(f"ALTER TABLE {table} ADD COLUMN {col} {coltype}")
    conn.commit()


def _applied_migrations(conn) -> set[str]:
    conn.execute(
        "CREATE TABLE IF NOT EXISTS aiify_schema_migrations ("
        "version TEXT PRIMARY KEY, applied_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)"
    )
    conn.commit()
    try:
        rows = conn.execute("SELECT version FROM aiify_schema_migrations").fetchall()
        return {(r["version"] if hasattr(r, "keys") else r[0]) for r in rows}
    except Exception:
        return set()


def _run_file_migrations(conn) -> None:
    """Apply any unapplied ``migrations/*.sql`` in filename order, tracked once.

    Statements are PG-authored and flow through the translating connection, so
    the same file applies on SQLite and PostgreSQL. CREATE … IF NOT EXISTS keeps
    re-runs idempotent.
    """
    if not _MIGRATIONS_DIR.is_dir():
        return
    applied = _applied_migrations(conn)
    for path in sorted(_MIGRATIONS_DIR.glob("*.sql")):
        version = path.stem
        if version in applied:
            continue
        sql = path.read_text(encoding="utf-8")
        for stmt in sql.split(";"):
            stmt = stmt.strip()
            if stmt:
                conn.execute(stmt)
        conn.execute(
            "INSERT INTO aiify_schema_migrations (version) VALUES (%s)", (version,)
        )
        conn.commit()


def init_db() -> None:
    backend = _backend()
    conn = get_connection()
    # SQLite needs the AUTOINCREMENT form (translate_sql does NOT rewrite
    # PG ``SERIAL`` → ``INTEGER PRIMARY KEY AUTOINCREMENT``), so keep the
    # backend-specific baseline. The connection still translates other dialect
    # differences (%s, JSONB, datetime) at execute time.
    schema = SCHEMA_PG if backend == "postgresql" else SCHEMA_SQLITE
    try:
        for stmt in schema.split(";"):
            stmt = stmt.strip()
            if stmt:
                conn.execute(stmt)
        conn.commit()
        # Orderly, tracked file migrations + idempotent column reconcile.
        _run_file_migrations(conn)
        _ensure_columns(conn)
        print(f"[init_db] AI-ify schema ready ({backend})", file=sys.stderr)
    finally:
        conn.close()


if __name__ == "__main__":
    init_db()
