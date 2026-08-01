# CUI // SP-CTI
"""OHC — Ops Hub Canvas DB initializer.

Creates ohc_canvas.db with tables for MLOps tracking, model registry,
dataset versioning, and adapter health. Phase 70 SRE/LLM tables remain
in icdev.db — this schema covers OHC-specific state only.

Dual-backend: SQLite (default) or PostgreSQL.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

_ICDEV_ROOT = Path(__file__).resolve().parents[3]
DB_PATH = _ICDEV_ROOT / "data" / "ohc_canvas.db"

_OHC_BACKEND = os.environ.get(
    "OHC_STORAGE_BACKEND",
    os.environ.get("ICDEV_CANVAS_STORAGE_BACKEND", os.environ.get("ICDEV_STORAGE_BACKEND", "postgresql")),
).lower()


def get_connection():
    """Return a DB connection — SQLite or PostgreSQL with row-factory.

    Uses get_canvas_connection() for PostgreSQL because ohc_* tables have no
    tenant_id/classification columns; get_connection() would inject RLS and
    raise UndefinedColumn.
    """
    if _OHC_BACKEND == "postgresql":
        try:
            from tools.db.storage import get_canvas_connection
            return get_canvas_connection("OHC_PG_DATABASE")
        except ImportError:
            pass
    import sqlite3 as _sqlite3
    conn = _sqlite3.connect(str(DB_PATH))
    conn.row_factory = _sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


SCHEMA = """
-- ── MLOps: Experiment Tracking ──────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS ohc_experiments (
    id              TEXT PRIMARY KEY,
    name            TEXT NOT NULL UNIQUE,
    description     TEXT DEFAULT '',
    tags_json       TEXT DEFAULT '{}',
    classification  TEXT DEFAULT 'CUI',
    created_at      TEXT DEFAULT CURRENT_TIMESTAMP,
    updated_at      TEXT DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS ohc_experiment_runs (
    id              TEXT PRIMARY KEY,
    experiment_id   TEXT NOT NULL REFERENCES ohc_experiments(id) ON DELETE CASCADE,
    run_name        TEXT DEFAULT '',
    status          TEXT DEFAULT 'running',
    start_time      TEXT DEFAULT CURRENT_TIMESTAMP,
    end_time        TEXT,
    duration_ms     INTEGER,
    params_json     TEXT DEFAULT '{}',
    metrics_json    TEXT DEFAULT '{}',
    tags_json       TEXT DEFAULT '{}',
    artifacts_json  TEXT DEFAULT '[]',
    source_adapter  TEXT DEFAULT 'internal',
    classification  TEXT DEFAULT 'CUI',
    created_at      TEXT DEFAULT CURRENT_TIMESTAMP,
    updated_at      TEXT DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_ohc_runs_experiment  ON ohc_experiment_runs(experiment_id);
CREATE INDEX IF NOT EXISTS idx_ohc_runs_status      ON ohc_experiment_runs(status);
CREATE INDEX IF NOT EXISTS idx_ohc_runs_start       ON ohc_experiment_runs(start_time);

-- ── MLOps: Model Registry ────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS ohc_model_registry (
    id              TEXT PRIMARY KEY,
    name            TEXT NOT NULL,
    version         TEXT NOT NULL DEFAULT '1',
    stage           TEXT NOT NULL DEFAULT 'none',
    description     TEXT DEFAULT '',
    run_id          TEXT REFERENCES ohc_experiment_runs(id),
    artifact_path   TEXT DEFAULT '',
    model_type      TEXT DEFAULT 'llm',
    framework       TEXT DEFAULT '',
    metrics_json    TEXT DEFAULT '{}',
    tags_json       TEXT DEFAULT '{}',
    source_adapter  TEXT DEFAULT 'internal',
    classification  TEXT DEFAULT 'CUI',
    created_at      TEXT DEFAULT CURRENT_TIMESTAMP,
    updated_at      TEXT DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(name, version)
);

CREATE INDEX IF NOT EXISTS idx_ohc_registry_name    ON ohc_model_registry(name);
CREATE INDEX IF NOT EXISTS idx_ohc_registry_stage   ON ohc_model_registry(stage);

-- ── MLOps: Dataset Versioning ────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS ohc_dataset_versions (
    id              TEXT PRIMARY KEY,
    name            TEXT NOT NULL,
    version         TEXT NOT NULL DEFAULT '1',
    description     TEXT DEFAULT '',
    path            TEXT DEFAULT '',
    size_bytes      INTEGER DEFAULT 0,
    row_count       INTEGER DEFAULT 0,
    schema_json     TEXT DEFAULT '{}',
    drift_score     REAL DEFAULT 0.0,
    quality_score   REAL DEFAULT 1.0,
    tags_json       TEXT DEFAULT '{}',
    source_adapter  TEXT DEFAULT 'internal',
    classification  TEXT DEFAULT 'CUI',
    created_at      TEXT DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(name, version)
);

-- ── Adapter Registry ─────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS ohc_adapter_status (
    id              TEXT PRIMARY KEY,
    adapter_name    TEXT NOT NULL UNIQUE,
    adapter_type    TEXT NOT NULL,
    domain          TEXT NOT NULL,
    available       INTEGER DEFAULT 0,
    version         TEXT DEFAULT '',
    endpoint        TEXT DEFAULT '',
    last_probe      TEXT,
    probe_result    TEXT DEFAULT '{}',
    classification  TEXT DEFAULT 'CUI',
    created_at      TEXT DEFAULT CURRENT_TIMESTAMP,
    updated_at      TEXT DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS ohc_adapter_health_log (
    id              TEXT PRIMARY KEY,
    adapter_name    TEXT NOT NULL,
    status          TEXT NOT NULL,
    latency_ms      INTEGER DEFAULT 0,
    error_msg       TEXT DEFAULT '',
    checked_at      TEXT DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_ohc_adapter_log_name ON ohc_adapter_health_log(adapter_name);
CREATE INDEX IF NOT EXISTS idx_ohc_adapter_log_time ON ohc_adapter_health_log(checked_at);

-- ── MLOps: Data Drift Events ─────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS ohc_data_drift_events (
    id              TEXT PRIMARY KEY,
    dataset_name    TEXT NOT NULL,
    drift_type      TEXT NOT NULL,
    drift_score     REAL NOT NULL,
    threshold       REAL DEFAULT 0.3,
    passed          INTEGER DEFAULT 1,
    details_json    TEXT DEFAULT '{}',
    source_adapter  TEXT DEFAULT 'evidently',
    classification  TEXT DEFAULT 'CUI',
    detected_at     TEXT DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_ohc_drift_dataset    ON ohc_data_drift_events(dataset_name);
CREATE INDEX IF NOT EXISTS idx_ohc_drift_time       ON ohc_data_drift_events(detected_at);
"""


def init_db(conn=None) -> None:
    """Create OHC schema tables (idempotent)."""
    _conn = conn or get_connection()
    try:
        _conn.executescript(SCHEMA)
        _conn.commit()
        print("[ohc-db] Schema initialized OK", file=sys.stderr)
    except Exception as exc:
        print(f"[ohc-db] Schema init error: {exc}", file=sys.stderr)
        raise
    finally:
        if conn is None:
            _conn.close()


if __name__ == "__main__":
    init_db()
    print(f"[ohc-db] Database ready at {DB_PATH}", file=sys.stderr)
