# CUI // SP-CTI
"""Slide Deck Generator Canvas — DB initializer.

Dual-backend: PostgreSQL (default) or SQLite fallback.
DB file: data/slides_canvas.db  |  env: SLIDES_STORAGE_BACKEND, SLIDES_DB_PATH

IMPORTANT: Uses get_canvas_connection("SLIDES_PG_DATABASE") for PostgreSQL —
NOT get_connection(). Canvas tables have no classification/tenant_id columns
and get_connection() would inject RLS predicates that raise UndefinedColumn.
"""
from __future__ import annotations

import os
from pathlib import Path

from tools.slides.constants import (
    CHECK_DECK_TYPE,
    CHECK_SLIDE_TYPE,
    CHECK_THEME,
    CHECK_DECK_STATUS,
)

_ICDEV_ROOT = Path(__file__).resolve().parents[4]
DB_PATH = Path(
    os.environ.get("SLIDES_DB_PATH", str(_ICDEV_ROOT / "data" / "slides_canvas.db"))
)

_SLIDES_BACKEND = os.environ.get(
    "SLIDES_STORAGE_BACKEND",
    os.environ.get("ICDEV_CANVAS_STORAGE_BACKEND", "postgresql"),
).lower()


def get_connection():
    if _SLIDES_BACKEND == "postgresql":
        try:
            from tools.db.storage import get_canvas_connection
            return get_canvas_connection("SLIDES_PG_DATABASE")
        except Exception:
            pass
    import sqlite3
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


_SCHEMA_PG = f"""
CREATE TABLE IF NOT EXISTS slides_decks (
    deck_id         SERIAL PRIMARY KEY,
    title           TEXT NOT NULL,
    deck_type       TEXT NOT NULL DEFAULT 'executive_overview' CHECK({CHECK_DECK_TYPE}),
    theme           TEXT NOT NULL DEFAULT 'midnight_executive' CHECK({CHECK_THEME}),
    tone            TEXT DEFAULT 'professional',
    occasion        TEXT,
    target_audience TEXT,
    citation_style  TEXT DEFAULT 'inline_links',
    output_formats  JSONB DEFAULT '["pptx"]',
    status          TEXT NOT NULL DEFAULT 'pending' CHECK({CHECK_DECK_STATUS}),
    source_types    JSONB DEFAULT '[]',
    pptx_path       TEXT,
    pdf_path        TEXT,
    html_path       TEXT,
    slide_count     INTEGER DEFAULT 0,
    error_message   TEXT,
    created_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    completed_at    TIMESTAMP
);

CREATE TABLE IF NOT EXISTS slides_slides (
    slide_id       SERIAL PRIMARY KEY,
    deck_id        INTEGER NOT NULL REFERENCES slides_decks(deck_id) ON DELETE CASCADE,
    position       INTEGER NOT NULL,
    slide_type     TEXT NOT NULL DEFAULT 'content' CHECK({CHECK_SLIDE_TYPE}),
    title          TEXT NOT NULL,
    bullets        JSONB DEFAULT '[]',
    speaker_notes  TEXT,
    citations      JSONB DEFAULT '[]',
    image_path     TEXT,
    image_prompt   TEXT,
    provenance     TEXT DEFAULT 'llm',
    created_at     TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS slides_audit (
    audit_id  SERIAL PRIMARY KEY,
    deck_id   INTEGER REFERENCES slides_decks(deck_id),
    action    TEXT NOT NULL,
    actor     TEXT DEFAULT 'system',
    details   JSONB,
    ts        TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS slides_templates (
    template_id     SERIAL PRIMARY KEY,
    filename        TEXT NOT NULL,
    path            TEXT NOT NULL,
    slide_count     INTEGER DEFAULT 0,
    shape_map_json  JSONB DEFAULT '[]',
    uploaded_at     TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_slides_deck_id ON slides_slides(deck_id);
CREATE INDEX IF NOT EXISTS idx_slides_audit_deck_id ON slides_audit(deck_id);
"""

_SCHEMA_SQLITE = """
CREATE TABLE IF NOT EXISTS slides_decks (
    deck_id              INTEGER PRIMARY KEY AUTOINCREMENT,
    title                TEXT NOT NULL,
    deck_type            TEXT NOT NULL DEFAULT 'executive_overview',
    theme                TEXT NOT NULL DEFAULT 'midnight_executive',
    tone                 TEXT DEFAULT 'professional',
    occasion             TEXT,
    target_audience      TEXT,
    citation_style       TEXT DEFAULT 'inline_links',
    output_formats       TEXT DEFAULT '["pptx"]',
    status               TEXT NOT NULL DEFAULT 'pending',
    source_types         TEXT DEFAULT '[]',
    pptx_path            TEXT,
    pdf_path             TEXT,
    html_path            TEXT,
    slide_count          INTEGER DEFAULT 0,
    error_message        TEXT,
    enable_rich_diagrams INTEGER DEFAULT 0,
    audience_mode        TEXT,
    created_at           DATETIME DEFAULT CURRENT_TIMESTAMP,
    completed_at         DATETIME
);

CREATE TABLE IF NOT EXISTS slides_slides (
    slide_id             INTEGER PRIMARY KEY AUTOINCREMENT,
    deck_id              INTEGER NOT NULL REFERENCES slides_decks(deck_id) ON DELETE CASCADE,
    position             INTEGER NOT NULL,
    slide_type           TEXT NOT NULL DEFAULT 'content',
    title                TEXT NOT NULL,
    bullets              TEXT DEFAULT '[]',
    speaker_notes        TEXT,
    citations            TEXT DEFAULT '[]',
    image_path           TEXT,
    image_prompt         TEXT,
    mermaid_code         TEXT,
    three_scene_config   TEXT,
    excalidraw_elements  TEXT,
    provenance           TEXT DEFAULT 'llm',
    created_at           DATETIME DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS slides_audit (
    audit_id  INTEGER PRIMARY KEY AUTOINCREMENT,
    deck_id   INTEGER REFERENCES slides_decks(deck_id),
    action    TEXT NOT NULL,
    actor     TEXT DEFAULT 'system',
    details   TEXT,
    ts        DATETIME DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS slides_templates (
    template_id     INTEGER PRIMARY KEY AUTOINCREMENT,
    filename        TEXT NOT NULL,
    path            TEXT NOT NULL,
    slide_count     INTEGER DEFAULT 0,
    shape_map_json  TEXT DEFAULT '[]',
    uploaded_at     DATETIME DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_slides_deck_id ON slides_slides(deck_id);
CREATE INDEX IF NOT EXISTS idx_slides_audit_deck_id ON slides_audit(deck_id);
"""

_MIGRATIONS_DIR = Path(__file__).resolve().parent / "migrations"


def _parse_migration_version(filename: str) -> int:
    """Return numeric prefix from filenames like '001_general_presentation.sql'."""
    digits = []
    for ch in filename:
        if ch.isdigit():
            digits.append(ch)
        else:
            break
    return int("".join(digits)) if digits else 0


def _conn_is_pg(conn) -> bool:
    """Resolve dialect from the actual connection, not the configured backend.

    ``_SLIDES_BACKEND`` defaults to "postgresql" and get_connection() falls
    back to SQLite silently when PG is unreachable — using the *configured*
    backend to pick PG-dialect SQL (e.g. SERIAL PRIMARY KEY) against a
    connection that actually fell back to SQLite silently produces a broken
    schema (SERIAL parses but never autoincrements). Ask the connection.
    """
    try:
        from tools.db.storage import is_pg
        return is_pg(conn)
    except Exception:
        return _SLIDES_BACKEND == "postgresql"


def _apply_migrations(conn) -> None:
    """Apply pending .sql migrations from tools/slides/db/migrations/."""
    if not _MIGRATIONS_DIR.is_dir():
        return

    files = sorted(
        (p for p in _MIGRATIONS_DIR.iterdir() if p.suffix.lower() == ".sql"),
        key=lambda p: _parse_migration_version(p.name),
    )
    if not files:
        return

    is_pg = _conn_is_pg(conn)

    if is_pg:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS slides_schema_migrations (
                version     INTEGER PRIMARY KEY,
                applied_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        conn.commit()
        row = conn.execute(
            "SELECT MAX(version) as v FROM slides_schema_migrations"
        ).fetchone()
        current = int(row[0] if row and row[0] is not None else 0)
    else:
        cur = conn.execute("PRAGMA user_version")
        current = int(cur.fetchone()[0])

    applied = 0
    for path in files:
        version = _parse_migration_version(path.name)
        if version <= current:
            continue
        sql = path.read_text(encoding="utf-8")
        for stmt in sql.strip().split(";"):
            stmt = stmt.strip()
            if stmt:
                try:
                    conn.execute(stmt)
                except Exception:
                    # Idempotent — tolerate already-present columns/constraints.
                    # Rollback so PostgreSQL transaction does not stay in aborted state.
                    try:
                        conn.rollback()
                    except Exception:
                        pass
        if is_pg:
            conn.execute(
                "INSERT INTO slides_schema_migrations (version) VALUES (%s)",
                (version,),
            )
        current = version
        applied += 1

    if not is_pg and applied:
        conn.execute(f"PRAGMA user_version = {current}")
    conn.commit()


_INIT_DONE = False


def init_db() -> None:
    global _INIT_DONE
    if _INIT_DONE:
        return
    conn = get_connection()
    try:
        schema = _SCHEMA_PG if _conn_is_pg(conn) else _SCHEMA_SQLITE
        for stmt in schema.strip().split(";"):
            stmt = stmt.strip()
            if stmt:
                conn.execute(stmt)
        _apply_migrations(conn)
    finally:
        conn.close()
    _INIT_DONE = True
