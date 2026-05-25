# CUI // SP-CTI
"""AI Augmentation Canvas (AAC) — DB initializer.

Dual-backend: PostgreSQL (default) or SQLite fallback.
DB file: data/aac_canvas.db  |  env: AAC_STORAGE_BACKEND, AAC_DB_PATH
"""
from __future__ import annotations

import os
from pathlib import Path

from tools.ai_augmentation.constants import (
    SUPPORTED_LANGUAGES,
    CHECK_PATTERN_TYPE,
    CHECK_AI_PARADIGM,
)

_ICDEV_ROOT = Path(__file__).resolve().parents[3]
DB_PATH = Path(os.environ.get("AAC_DB_PATH", str(_ICDEV_ROOT / "data" / "aac_canvas.db")))

_AAC_BACKEND = os.environ.get(
    "AAC_STORAGE_BACKEND",
    os.environ.get("ICDEV_CANVAS_STORAGE_BACKEND", "postgresql"),
).lower()

_lang_list = ", ".join(f"'{lang}'" for lang in SUPPORTED_LANGUAGES)
_CHECK_LANGUAGE = f"language IN ({_lang_list})"


def get_connection():
    if _AAC_BACKEND == "postgresql":
        try:
            from tools.db.storage import get_canvas_connection
            return get_canvas_connection("AAC_PG_DATABASE")
        except Exception:
            pass
    import sqlite3
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


SCHEMA_PG = f"""
CREATE TABLE IF NOT EXISTS aac_scans (
    scan_id          SERIAL PRIMARY KEY,
    input_type       TEXT NOT NULL,
    input_ref        TEXT NOT NULL,
    language_profile JSONB,
    total_files      INTEGER DEFAULT 0,
    total_loc        INTEGER DEFAULT 0,
    status           TEXT NOT NULL DEFAULT 'pending' CHECK(status IN ('pending','running','completed','failed')),
    created_at       TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    completed_at     TIMESTAMP
);

CREATE TABLE IF NOT EXISTS aac_opportunities (
    opportunity_id       SERIAL PRIMARY KEY,
    scan_id              INTEGER NOT NULL REFERENCES aac_scans(scan_id) ON DELETE CASCADE,
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
);

CREATE TABLE IF NOT EXISTS aac_scores (
    score_id          SERIAL PRIMARY KEY,
    opportunity_id    INTEGER NOT NULL REFERENCES aac_opportunities(opportunity_id) ON DELETE CASCADE,
    value_score       REAL,
    feasibility_score REAL,
    risk_score        REAL,
    composite_score   REAL,
    score_detail      JSONB,
    scored_at         TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS aac_roadmaps (
    id                SERIAL PRIMARY KEY,
    scan_id           INTEGER NOT NULL REFERENCES aac_scans(scan_id) ON DELETE CASCADE,
    roadmap_id        TEXT NOT NULL UNIQUE,
    title             TEXT NOT NULL,
    phases            JSONB,
    total_effort_days INTEGER DEFAULT 0,
    aimc_links        JSONB,
    aadc_links        JSONB,
    created_at        TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS aac_audit_log (
    id         SERIAL PRIMARY KEY,
    event_type TEXT NOT NULL,
    scan_id    INTEGER REFERENCES aac_scans(scan_id) ON DELETE SET NULL,
    actor      TEXT NOT NULL DEFAULT 'system',
    detail     JSONB,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
)
"""

SCHEMA_SQLITE = (
    SCHEMA_PG
    .replace("SERIAL PRIMARY KEY", "INTEGER PRIMARY KEY AUTOINCREMENT")
    .replace("JSONB", "TEXT")
    .replace("TIMESTAMP DEFAULT CURRENT_TIMESTAMP", "TEXT DEFAULT CURRENT_TIMESTAMP")
)


def init_db() -> None:
    conn = get_connection()
    schema = SCHEMA_PG if _AAC_BACKEND == "postgresql" else SCHEMA_SQLITE
    try:
        for stmt in schema.split(";"):
            stmt = stmt.strip()
            if stmt:
                conn.execute(stmt)
        conn.commit()
        print(f"[init_db] AAC schema ready ({_AAC_BACKEND})")
    finally:
        conn.close()


if __name__ == "__main__":
    init_db()
