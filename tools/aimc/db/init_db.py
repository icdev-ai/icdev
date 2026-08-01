# CUI // SP-CTI
"""Initialize AIMC DB tables (idempotent — safe to call at startup).

Creates aimc_models (model inventory metrics) and aimc_deployment (deployment
readiness check state) used by model_scanner.py and deployment_checker.py.
Rows are soft-keyed by (project_id, metric_key) and (project_id, check_key).

PG-primary: TIMESTAMPTZ DEFAULT NOW(). SQLite fallback uses datetime('now').
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(_ROOT))

_IS_PG = os.environ.get("ICDEV_STORAGE_BACKEND", "postgresql").lower() == "postgresql"

_AIMC_MODELS_DDL_PG = """
CREATE TABLE IF NOT EXISTS aimc_models (
    id              TEXT PRIMARY KEY,
    project_id      TEXT NOT NULL,
    metric_key      TEXT NOT NULL,
    metric_value    TEXT NOT NULL,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    classification  TEXT NOT NULL DEFAULT 'CUI'
)
"""

_AIMC_MODELS_DDL_SQLITE = """
CREATE TABLE IF NOT EXISTS aimc_models (
    id              TEXT PRIMARY KEY,
    project_id      TEXT NOT NULL,
    metric_key      TEXT NOT NULL,
    metric_value    TEXT NOT NULL,
    created_at      TEXT NOT NULL DEFAULT (datetime('now')),
    classification  TEXT NOT NULL DEFAULT 'CUI'
)
"""

_AIMC_MODELS_IDX = [
    "CREATE INDEX IF NOT EXISTS idx_aimc_models_project ON aimc_models (project_id)",
    "CREATE INDEX IF NOT EXISTS idx_aimc_models_key ON aimc_models (project_id, metric_key)",
]

_AIMC_DEPLOYMENT_DDL_PG = """
CREATE TABLE IF NOT EXISTS aimc_deployment (
    id              TEXT PRIMARY KEY,
    project_id      TEXT NOT NULL,
    check_key       TEXT NOT NULL,
    check_value     TEXT NOT NULL,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    classification  TEXT NOT NULL DEFAULT 'CUI'
)
"""

_AIMC_DEPLOYMENT_DDL_SQLITE = """
CREATE TABLE IF NOT EXISTS aimc_deployment (
    id              TEXT PRIMARY KEY,
    project_id      TEXT NOT NULL,
    check_key       TEXT NOT NULL,
    check_value     TEXT NOT NULL,
    created_at      TEXT NOT NULL DEFAULT (datetime('now')),
    classification  TEXT NOT NULL DEFAULT 'CUI'
)
"""

_AIMC_DEPLOYMENT_IDX = [
    "CREATE INDEX IF NOT EXISTS idx_aimc_deployment_project ON aimc_deployment (project_id)",
    "CREATE INDEX IF NOT EXISTS idx_aimc_deployment_key ON aimc_deployment (project_id, check_key)",
]


def init_db() -> None:
    """Create AIMC tables if they do not exist."""
    # cvx-sql-03: aimc_* tables carry `classification` but no `tenant_id`, so the
    # storage-global get_connection() would attach the RLS predicate and raise
    # UndefinedColumn. Use get_canvas_connection() (RLS disabled) instead.
    from tools.db.storage import get_canvas_connection

    models_ddl = _AIMC_MODELS_DDL_PG if _IS_PG else _AIMC_MODELS_DDL_SQLITE
    deployment_ddl = _AIMC_DEPLOYMENT_DDL_PG if _IS_PG else _AIMC_DEPLOYMENT_DDL_SQLITE

    with get_canvas_connection("AIMC_DB_URL") as conn:
        cur = conn.cursor()
        cur.execute(models_ddl)
        for idx_sql in _AIMC_MODELS_IDX:
            cur.execute(idx_sql)
        cur.execute(deployment_ddl)
        for idx_sql in _AIMC_DEPLOYMENT_IDX:
            cur.execute(idx_sql)
        conn.commit()


if __name__ == "__main__":
    init_db()
    print("AIMC DB initialised.")
