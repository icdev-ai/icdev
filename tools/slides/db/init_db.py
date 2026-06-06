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
    deck_id       SERIAL PRIMARY KEY,
    title         TEXT NOT NULL,
    deck_type     TEXT NOT NULL DEFAULT 'executive_overview' CHECK({CHECK_DECK_TYPE}),
    theme         TEXT NOT NULL DEFAULT 'midnight_executive' CHECK({CHECK_THEME}),
    status        TEXT NOT NULL DEFAULT 'pending' CHECK({CHECK_DECK_STATUS}),
    source_types  JSONB DEFAULT '[]',
    pptx_path     TEXT,
    slide_count   INTEGER DEFAULT 0,
    error_message TEXT,
    created_at    TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    completed_at  TIMESTAMP
);

CREATE TABLE IF NOT EXISTS slides_slides (
    slide_id       SERIAL PRIMARY KEY,
    deck_id        INTEGER NOT NULL REFERENCES slides_decks(deck_id) ON DELETE CASCADE,
    position       INTEGER NOT NULL,
    slide_type     TEXT NOT NULL DEFAULT 'content' CHECK({CHECK_SLIDE_TYPE}),
    title          TEXT NOT NULL,
    bullets        JSONB DEFAULT '[]',
    speaker_notes  TEXT,
    image_path     TEXT,
    image_prompt   TEXT,
    chart_json     JSONB,
    table_json     JSONB,
    diagram_json   JSONB,
    kpis_json      JSONB,
    dashboard_json JSONB,
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

CREATE INDEX IF NOT EXISTS idx_slides_deck_id ON slides_slides(deck_id);
CREATE INDEX IF NOT EXISTS idx_slides_audit_deck_id ON slides_audit(deck_id);
"""

_SCHEMA_SQLITE = """
CREATE TABLE IF NOT EXISTS slides_decks (
    deck_id       INTEGER PRIMARY KEY AUTOINCREMENT,
    title         TEXT NOT NULL,
    deck_type     TEXT NOT NULL DEFAULT 'executive_overview',
    theme         TEXT NOT NULL DEFAULT 'midnight_executive',
    status        TEXT NOT NULL DEFAULT 'pending',
    source_types  TEXT DEFAULT '[]',
    pptx_path     TEXT,
    slide_count   INTEGER DEFAULT 0,
    error_message TEXT,
    created_at    DATETIME DEFAULT CURRENT_TIMESTAMP,
    completed_at  DATETIME
);

CREATE TABLE IF NOT EXISTS slides_slides (
    slide_id      INTEGER PRIMARY KEY AUTOINCREMENT,
    deck_id       INTEGER NOT NULL REFERENCES slides_decks(deck_id) ON DELETE CASCADE,
    position      INTEGER NOT NULL,
    slide_type    TEXT NOT NULL DEFAULT 'content',
    title         TEXT NOT NULL,
    bullets       TEXT DEFAULT '[]',
    speaker_notes TEXT,
    image_path    TEXT,
    image_prompt  TEXT,
    chart_json    TEXT,
    table_json    TEXT,
    diagram_json  TEXT,
    kpis_json     TEXT,
    dashboard_json TEXT,
    created_at    DATETIME DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS slides_audit (
    audit_id  INTEGER PRIMARY KEY AUTOINCREMENT,
    deck_id   INTEGER REFERENCES slides_decks(deck_id),
    action    TEXT NOT NULL,
    actor     TEXT DEFAULT 'system',
    details   TEXT,
    ts        DATETIME DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_slides_deck_id ON slides_slides(deck_id);
CREATE INDEX IF NOT EXISTS idx_slides_audit_deck_id ON slides_audit(deck_id);
"""

_INIT_DONE = False

# Additive viz columns (VIZ Epic B). Applied idempotently so already-created
# slides_slides tables gain them without a separate migration runner.
_VIZ_COLUMNS = ("chart_json", "table_json", "diagram_json", "kpis_json", "dashboard_json")


def _migrate_viz_columns(conn) -> None:
    """Add chart/table/diagram/kpis JSON columns if missing (graceful)."""
    col_type = "JSONB" if _SLIDES_BACKEND == "postgresql" else "TEXT"
    for col in _VIZ_COLUMNS:
        try:
            conn.execute(f"ALTER TABLE slides_slides ADD COLUMN {col} {col_type}")
            conn.commit()
        except Exception:
            # Column already exists (or backend rejects IF NOT EXISTS) — fine.
            try:
                conn.rollback()
            except Exception:
                pass


def init_db() -> None:
    global _INIT_DONE
    if _INIT_DONE:
        return
    conn = get_connection()
    try:
        schema = _SCHEMA_PG if _SLIDES_BACKEND == "postgresql" else _SCHEMA_SQLITE
        for stmt in schema.strip().split(";"):
            stmt = stmt.strip()
            if stmt:
                conn.execute(stmt)
        conn.commit()
        _migrate_viz_columns(conn)
    finally:
        conn.close()
    _INIT_DONE = True
