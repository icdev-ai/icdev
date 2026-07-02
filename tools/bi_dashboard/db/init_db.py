# CUI // SP-CTI
"""BI Dashboard Canvas database schema initialization.

All bi_* tables carry tenant_id + classification columns and use
get_connection() (NOT get_canvas_connection()) so RLS is enforced across
all queries, following the same pattern as tools/document_intelligence/db/init_db.py.
"""
from __future__ import annotations

from tools.logging.icdev_logger import get_logger

logger = get_logger(__name__)

_SCHEMA_PG = """
CREATE TABLE IF NOT EXISTS bi_dashboards (
    id              TEXT        PRIMARY KEY,
    title           TEXT        NOT NULL,
    owner_id        TEXT        DEFAULT '',
    tiles_json      TEXT        DEFAULT '[]',
    created_at      TIMESTAMPTZ DEFAULT NOW(),
    updated_at      TIMESTAMPTZ DEFAULT NOW(),
    tenant_id       TEXT        DEFAULT 'default',
    classification  TEXT        DEFAULT 'CUI'
);
CREATE INDEX IF NOT EXISTS idx_bi_dashboards_tenant ON bi_dashboards(tenant_id);

CREATE TABLE IF NOT EXISTS bi_data_sources (
    id               TEXT        PRIMARY KEY,
    name             TEXT        NOT NULL,
    source_type      TEXT        DEFAULT 'upload' CHECK (source_type IN ('upload', 'iqe')),
    columns_json     TEXT        DEFAULT '[]',
    dimensions_json  TEXT        DEFAULT '[]',
    measures_json    TEXT        DEFAULT '[]',
    rows_json        TEXT        DEFAULT '[]',
    row_count        INTEGER     DEFAULT 0,
    created_at       TIMESTAMPTZ DEFAULT NOW(),
    tenant_id        TEXT        DEFAULT 'default',
    classification   TEXT        DEFAULT 'CUI'
);
CREATE INDEX IF NOT EXISTS idx_bi_data_sources_tenant ON bi_data_sources(tenant_id);

CREATE TABLE IF NOT EXISTS bi_generation_log (
    id              TEXT        PRIMARY KEY,
    dashboard_id    TEXT        DEFAULT '',
    prompt          TEXT        NOT NULL,
    structure_json  TEXT        DEFAULT '{}',
    method          TEXT        DEFAULT 'heuristic' CHECK (method IN ('llm', 'llm_retry', 'heuristic')),
    accepted         INTEGER    DEFAULT 1,
    created_at      TIMESTAMPTZ DEFAULT NOW(),
    tenant_id       TEXT        DEFAULT 'default',
    classification  TEXT        DEFAULT 'CUI'
);
CREATE INDEX IF NOT EXISTS idx_bi_generation_log_dashboard ON bi_generation_log(dashboard_id);
"""

_SCHEMA_SQLITE = """
CREATE TABLE IF NOT EXISTS bi_dashboards (
    id              TEXT    PRIMARY KEY,
    title           TEXT    NOT NULL,
    owner_id        TEXT    DEFAULT '',
    tiles_json      TEXT    DEFAULT '[]',
    created_at      TEXT    DEFAULT (datetime('now')),
    updated_at      TEXT    DEFAULT (datetime('now')),
    tenant_id       TEXT    DEFAULT 'default',
    classification  TEXT    DEFAULT 'CUI'
);
CREATE INDEX IF NOT EXISTS idx_bi_dashboards_tenant ON bi_dashboards(tenant_id);

CREATE TABLE IF NOT EXISTS bi_data_sources (
    id               TEXT    PRIMARY KEY,
    name             TEXT    NOT NULL,
    source_type      TEXT    DEFAULT 'upload' CHECK (source_type IN ('upload', 'iqe')),
    columns_json     TEXT    DEFAULT '[]',
    dimensions_json  TEXT    DEFAULT '[]',
    measures_json    TEXT    DEFAULT '[]',
    rows_json        TEXT    DEFAULT '[]',
    row_count        INTEGER DEFAULT 0,
    created_at       TEXT    DEFAULT (datetime('now')),
    tenant_id        TEXT    DEFAULT 'default',
    classification   TEXT    DEFAULT 'CUI'
);
CREATE INDEX IF NOT EXISTS idx_bi_data_sources_tenant ON bi_data_sources(tenant_id);

CREATE TABLE IF NOT EXISTS bi_generation_log (
    id              TEXT    PRIMARY KEY,
    dashboard_id    TEXT    DEFAULT '',
    prompt          TEXT    NOT NULL,
    structure_json  TEXT    DEFAULT '{}',
    method          TEXT    DEFAULT 'heuristic' CHECK (method IN ('llm', 'llm_retry', 'heuristic')),
    accepted        INTEGER DEFAULT 1,
    created_at      TEXT    DEFAULT (datetime('now')),
    tenant_id       TEXT    DEFAULT 'default',
    classification  TEXT    DEFAULT 'CUI'
);
CREATE INDEX IF NOT EXISTS idx_bi_generation_log_dashboard ON bi_generation_log(dashboard_id);
"""

_INIT_DONE = False


def init_db() -> None:
    global _INIT_DONE
    if _INIT_DONE:
        return
    try:
        import os
        from tools.db.storage import get_connection

        conn = get_connection()
        is_pg = os.environ.get("ICDEV_STORAGE_BACKEND", "postgresql").lower() in (
            "postgresql", "postgres", "pg"
        )
        schema = _SCHEMA_PG if is_pg else _SCHEMA_SQLITE
        cur = conn.cursor()
        for stmt in schema.strip().split(";"):
            stmt = stmt.strip()
            if stmt:
                cur.execute(stmt)
        conn.commit()
        conn.close()
        _INIT_DONE = True
        logger.info("BI Dashboard schema initialized")
    except Exception as exc:
        logger.warning("BI Dashboard db init error: %s", exc)


def get_connection():
    from tools.db.storage import get_connection as _gc
    return _gc()
