# CUI // SP-CTI
"""DIC database schema initialization.

All DIC tables carry tenant_id + classification columns and use get_connection()
(NOT get_canvas_connection()) so RLS is enforced across all queries.
"""
from __future__ import annotations

from tools.logging.icdev_logger import get_logger

logger = get_logger(__name__)

_SCHEMA_PG = """
CREATE TABLE IF NOT EXISTS dic_ingest_jobs (
    job_id        TEXT        PRIMARY KEY,
    filename      TEXT        NOT NULL,
    collection_id TEXT        DEFAULT 'default',
    status        TEXT        DEFAULT 'queued',
    stage_detail  TEXT        DEFAULT '',
    chunks_total  INTEGER     DEFAULT 0,
    chunks_done   INTEGER     DEFAULT 0,
    doc_id        TEXT,
    errors_json   TEXT        DEFAULT '[]',
    created_at    TIMESTAMPTZ DEFAULT NOW(),
    updated_at    TIMESTAMPTZ DEFAULT NOW(),
    tenant_id     TEXT        DEFAULT 'default'
);

CREATE TABLE IF NOT EXISTS dic_collections (
    collection_id   TEXT        PRIMARY KEY,
    name            TEXT        NOT NULL,
    description     TEXT        DEFAULT '',
    owner_id        TEXT        DEFAULT '',
    retention_days  INTEGER     DEFAULT 90,
    classification  TEXT        DEFAULT 'CUI',
    tenant_id       TEXT        DEFAULT 'default',
    created_at      TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS dic_team_access (
    access_id       TEXT        PRIMARY KEY,
    collection_id   TEXT        NOT NULL REFERENCES dic_collections(collection_id),
    user_id         TEXT        NOT NULL,
    role            TEXT        NOT NULL DEFAULT 'viewer',
    granted_by      TEXT        DEFAULT '',
    tenant_id       TEXT        DEFAULT 'default',
    classification  TEXT        DEFAULT 'CUI',
    created_at      TIMESTAMPTZ DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_dic_team_access_collection ON dic_team_access(collection_id);

CREATE TABLE IF NOT EXISTS dic_freshness_scans (
    scan_id         TEXT        PRIMARY KEY,
    collection_id   TEXT        NOT NULL,
    stale_count     INTEGER     DEFAULT 0,
    regen_priority  FLOAT       DEFAULT 0.0,
    scanned_at      TIMESTAMPTZ DEFAULT NOW(),
    tenant_id       TEXT        DEFAULT 'default'
);
CREATE INDEX IF NOT EXISTS idx_dic_freshness_scans_collection ON dic_freshness_scans(collection_id);

CREATE TABLE IF NOT EXISTS dic_doc_freshness (
    doc_id          TEXT        PRIMARY KEY,
    collection_id   TEXT        NOT NULL DEFAULT 'default',
    state           TEXT        DEFAULT 'unknown',
    reason          TEXT        DEFAULT '',
    source_event    TEXT        DEFAULT '',
    score           FLOAT       DEFAULT 0.0,
    updated_at      TIMESTAMPTZ DEFAULT NOW(),
    tenant_id       TEXT        DEFAULT 'default',
    classification  TEXT        DEFAULT 'CUI'
);
CREATE INDEX IF NOT EXISTS idx_dic_doc_freshness_tenant ON dic_doc_freshness(tenant_id);
CREATE INDEX IF NOT EXISTS idx_dic_doc_freshness_collection ON dic_doc_freshness(collection_id);

CREATE TABLE IF NOT EXISTS dic_handoff_sessions (
    session_id          TEXT        PRIMARY KEY,
    departing_owner_id  TEXT        NOT NULL,
    successor_owner_id  TEXT        NOT NULL,
    dest_collection_id  TEXT        NOT NULL,
    title               TEXT        DEFAULT '',
    status              TEXT        DEFAULT 'open' CHECK (status IN ('open', 'closed')),
    agenda_count        INTEGER     DEFAULT 0,
    answered_count      INTEGER     DEFAULT 0,
    generated_count     INTEGER     DEFAULT 0,
    orphan_count        INTEGER     DEFAULT 0,
    created_by          TEXT        DEFAULT 'system',
    created_at          TIMESTAMPTZ DEFAULT NOW(),
    updated_at          TIMESTAMPTZ DEFAULT NOW(),
    tenant_id           TEXT        DEFAULT 'default',
    classification      TEXT        DEFAULT 'CUI'
);
CREATE INDEX IF NOT EXISTS idx_dic_handoff_sessions_tenant ON dic_handoff_sessions(tenant_id);

CREATE TABLE IF NOT EXISTS dic_handoff_items (
    item_id         TEXT        PRIMARY KEY,
    session_id      TEXT        NOT NULL REFERENCES dic_handoff_sessions(session_id),
    item_kind       TEXT        NOT NULL DEFAULT 'interview' CHECK (item_kind IN ('interview', 'generated_doc', 'orphan_flag')),
    finding_id      TEXT        DEFAULT '',
    finding_type    TEXT        DEFAULT '',
    severity        TEXT        DEFAULT '',
    entity_ref      TEXT        DEFAULT '',
    topic           TEXT        DEFAULT '',
    prompt          TEXT        DEFAULT '',
    answer_text     TEXT        DEFAULT '',
    doc_id          TEXT        DEFAULT '',
    version_id      TEXT        DEFAULT '',
    verified        INTEGER     DEFAULT 0,
    abstained       INTEGER     DEFAULT 0,
    status          TEXT        DEFAULT 'pending' CHECK (status IN ('pending', 'answered', 'generated')),
    created_at      TIMESTAMPTZ DEFAULT NOW(),
    updated_at      TIMESTAMPTZ DEFAULT NOW(),
    tenant_id       TEXT        DEFAULT 'default',
    classification  TEXT        DEFAULT 'CUI'
);
CREATE INDEX IF NOT EXISTS idx_dic_handoff_items_session ON dic_handoff_items(session_id);

CREATE TABLE IF NOT EXISTS dic_community_summaries (
    summary_id      TEXT        PRIMARY KEY,
    community_id    TEXT        NOT NULL,
    summary_text    TEXT        NOT NULL,
    citations_list  TEXT        DEFAULT '[]',
    model_version   TEXT        DEFAULT '',
    created_at      TIMESTAMPTZ DEFAULT NOW(),
    updated_at      TIMESTAMPTZ DEFAULT NOW(),
    tenant_id       TEXT        DEFAULT 'default',
    classification  TEXT        DEFAULT 'CUI'
);
CREATE INDEX IF NOT EXISTS idx_dic_community_summaries_community ON dic_community_summaries(community_id);
CREATE INDEX IF NOT EXISTS idx_dic_community_summaries_tenant ON dic_community_summaries(tenant_id);

CREATE TABLE IF NOT EXISTS dic_presence_sessions (
    session_id          TEXT        PRIMARY KEY,
    doc_id              TEXT        NOT NULL,
    user_id             TEXT        NOT NULL,
    active_section_id   TEXT,
    last_seen           TEXT        NOT NULL,
    tenant_id           TEXT        DEFAULT 'default',
    classification      TEXT        DEFAULT 'CUI'
);
CREATE INDEX IF NOT EXISTS idx_dic_presence_sessions_doc ON dic_presence_sessions(doc_id);
CREATE INDEX IF NOT EXISTS idx_dic_presence_sessions_last_seen ON dic_presence_sessions(last_seen);
CREATE TABLE IF NOT EXISTS dic_generated_outputs (
    id              TEXT        PRIMARY KEY,
    output_type     TEXT        NOT NULL,
    collection_id   TEXT        NOT NULL,
    content_json    TEXT        DEFAULT '{}',
    provider        TEXT        DEFAULT '',
    status          TEXT        DEFAULT 'done',
    audio_path      TEXT,
    created_at      TIMESTAMPTZ DEFAULT NOW(),
    tenant_id       TEXT        DEFAULT 'default',
    classification  TEXT        DEFAULT 'CUI'
);
CREATE INDEX IF NOT EXISTS idx_dic_generated_outputs_collection ON dic_generated_outputs(collection_id);
CREATE INDEX IF NOT EXISTS idx_dic_generated_outputs_type ON dic_generated_outputs(output_type);

CREATE TABLE IF NOT EXISTS dic_doc_views (
    view_id         TEXT        PRIMARY KEY,
    doc_id          TEXT        NOT NULL,
    user_id         TEXT        NOT NULL DEFAULT 'anonymous',
    collection_id   TEXT        NOT NULL DEFAULT 'default',
    viewed_at       TIMESTAMPTZ DEFAULT NOW(),
    tenant_id       TEXT        DEFAULT 'default',
    classification  TEXT        DEFAULT 'CUI'
);
CREATE INDEX IF NOT EXISTS idx_dic_doc_views_doc ON dic_doc_views(doc_id);
CREATE INDEX IF NOT EXISTS idx_dic_doc_views_tenant ON dic_doc_views(tenant_id);
CREATE INDEX IF NOT EXISTS idx_dic_doc_views_viewed_at ON dic_doc_views(viewed_at);

-- Inter-document cross-references (dmx-ref-01). One row per textual reference
-- from source_doc_id to another document. target_doc_id is NULL until the
-- resolution pass matches target_doc_ref to a known DIC document — so this table
-- is NOT append-only (resolution UPDATEs target_doc_id/target_section).
CREATE TABLE IF NOT EXISTS dic_cross_references (
    id              TEXT        PRIMARY KEY,
    source_doc_id   TEXT        NOT NULL,
    source_section  TEXT        DEFAULT '',
    target_doc_ref  TEXT        NOT NULL,
    target_doc_id   TEXT,
    target_section  TEXT        DEFAULT '',
    ref_text        TEXT        DEFAULT '',
    tenant_id       TEXT        DEFAULT 'default',
    classification  TEXT        DEFAULT 'CUI',
    extracted_at    TIMESTAMPTZ DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_dic_cross_refs_source ON dic_cross_references(source_doc_id);
CREATE INDEX IF NOT EXISTS idx_dic_cross_refs_target ON dic_cross_references(target_doc_id);
CREATE INDEX IF NOT EXISTS idx_dic_cross_refs_tenant ON dic_cross_references(tenant_id);
"""

_SCHEMA_SQLITE = """
CREATE TABLE IF NOT EXISTS dic_ingest_jobs (
    job_id        TEXT    PRIMARY KEY,
    filename      TEXT    NOT NULL,
    collection_id TEXT    DEFAULT 'default',
    status        TEXT    DEFAULT 'queued',
    stage_detail  TEXT    DEFAULT '',
    chunks_total  INTEGER DEFAULT 0,
    chunks_done   INTEGER DEFAULT 0,
    doc_id        TEXT,
    errors_json   TEXT    DEFAULT '[]',
    created_at    TEXT    DEFAULT (datetime('now')),
    updated_at    TEXT    DEFAULT (datetime('now')),
    tenant_id     TEXT    DEFAULT 'default'
);

CREATE TABLE IF NOT EXISTS dic_collections (
    collection_id   TEXT    PRIMARY KEY,
    name            TEXT    NOT NULL,
    description     TEXT    DEFAULT '',
    owner_id        TEXT    DEFAULT '',
    retention_days  INTEGER DEFAULT 90,
    classification  TEXT    DEFAULT 'CUI',
    tenant_id       TEXT    DEFAULT 'default',
    created_at      TEXT    DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS dic_team_access (
    access_id       TEXT    PRIMARY KEY,
    collection_id   TEXT    NOT NULL,
    user_id         TEXT    NOT NULL,
    role            TEXT    NOT NULL DEFAULT 'viewer',
    granted_by      TEXT    DEFAULT '',
    tenant_id       TEXT    DEFAULT 'default',
    created_at      TEXT    DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_dic_team_access_collection ON dic_team_access(collection_id);

CREATE TABLE IF NOT EXISTS dic_freshness_scans (
    scan_id         TEXT    PRIMARY KEY,
    collection_id   TEXT    NOT NULL,
    stale_count     INTEGER DEFAULT 0,
    regen_priority  REAL    DEFAULT 0.0,
    scanned_at      TEXT    DEFAULT (datetime('now')),
    tenant_id       TEXT    DEFAULT 'default'
);
CREATE INDEX IF NOT EXISTS idx_dic_freshness_scans_collection ON dic_freshness_scans(collection_id);

CREATE TABLE IF NOT EXISTS dic_doc_freshness (
    doc_id          TEXT    PRIMARY KEY,
    collection_id   TEXT    NOT NULL DEFAULT 'default',
    state           TEXT    DEFAULT 'unknown',
    reason          TEXT    DEFAULT '',
    source_event    TEXT    DEFAULT '',
    score           REAL    DEFAULT 0.0,
    updated_at      TEXT    DEFAULT (datetime('now')),
    tenant_id       TEXT    DEFAULT 'default',
    classification  TEXT    DEFAULT 'CUI'
);
CREATE INDEX IF NOT EXISTS idx_dic_doc_freshness_tenant ON dic_doc_freshness(tenant_id);
CREATE INDEX IF NOT EXISTS idx_dic_doc_freshness_collection ON dic_doc_freshness(collection_id);

CREATE TABLE IF NOT EXISTS dic_handoff_sessions (
    session_id          TEXT    PRIMARY KEY,
    departing_owner_id  TEXT    NOT NULL,
    successor_owner_id  TEXT    NOT NULL,
    dest_collection_id  TEXT    NOT NULL,
    title               TEXT    DEFAULT '',
    status              TEXT    DEFAULT 'open' CHECK (status IN ('open', 'closed')),
    agenda_count        INTEGER DEFAULT 0,
    answered_count      INTEGER DEFAULT 0,
    generated_count     INTEGER DEFAULT 0,
    orphan_count        INTEGER DEFAULT 0,
    created_by          TEXT    DEFAULT 'system',
    created_at          TEXT    DEFAULT (datetime('now')),
    updated_at          TEXT    DEFAULT (datetime('now')),
    tenant_id           TEXT    DEFAULT 'default',
    classification      TEXT    DEFAULT 'CUI'
);
CREATE INDEX IF NOT EXISTS idx_dic_handoff_sessions_tenant ON dic_handoff_sessions(tenant_id);

CREATE TABLE IF NOT EXISTS dic_handoff_items (
    item_id         TEXT    PRIMARY KEY,
    session_id      TEXT    NOT NULL,
    item_kind       TEXT    NOT NULL DEFAULT 'interview' CHECK (item_kind IN ('interview', 'generated_doc', 'orphan_flag')),
    finding_id      TEXT    DEFAULT '',
    finding_type    TEXT    DEFAULT '',
    severity        TEXT    DEFAULT '',
    entity_ref      TEXT    DEFAULT '',
    topic           TEXT    DEFAULT '',
    prompt          TEXT    DEFAULT '',
    answer_text     TEXT    DEFAULT '',
    doc_id          TEXT    DEFAULT '',
    version_id      TEXT    DEFAULT '',
    verified        INTEGER DEFAULT 0,
    abstained       INTEGER DEFAULT 0,
    status          TEXT    DEFAULT 'pending' CHECK (status IN ('pending', 'answered', 'generated')),
    created_at      TEXT    DEFAULT (datetime('now')),
    updated_at      TEXT    DEFAULT (datetime('now')),
    tenant_id       TEXT    DEFAULT 'default',
    classification  TEXT    DEFAULT 'CUI'
);
CREATE INDEX IF NOT EXISTS idx_dic_handoff_items_session ON dic_handoff_items(session_id);

CREATE TABLE IF NOT EXISTS dic_community_summaries (
    summary_id      TEXT    PRIMARY KEY,
    community_id    TEXT    NOT NULL,
    summary_text    TEXT    NOT NULL,
    citations_list  TEXT    DEFAULT '[]',
    model_version   TEXT    DEFAULT '',
    created_at      TEXT    DEFAULT (datetime('now')),
    updated_at      TEXT    DEFAULT (datetime('now')),
    tenant_id       TEXT    DEFAULT 'default',
    classification  TEXT    DEFAULT 'CUI'
);
CREATE INDEX IF NOT EXISTS idx_dic_community_summaries_community ON dic_community_summaries(community_id);
CREATE INDEX IF NOT EXISTS idx_dic_community_summaries_tenant ON dic_community_summaries(tenant_id);

CREATE TABLE IF NOT EXISTS dic_presence_sessions (
    session_id          TEXT    PRIMARY KEY,
    doc_id              TEXT    NOT NULL,
    user_id             TEXT    NOT NULL,
    active_section_id   TEXT,
    last_seen           TEXT    NOT NULL,
    tenant_id           TEXT    DEFAULT 'default',
    classification      TEXT    DEFAULT 'CUI'
);
CREATE INDEX IF NOT EXISTS idx_dic_presence_sessions_doc ON dic_presence_sessions(doc_id);
CREATE INDEX IF NOT EXISTS idx_dic_presence_sessions_last_seen ON dic_presence_sessions(last_seen);
CREATE TABLE IF NOT EXISTS dic_generated_outputs (
    id              TEXT        PRIMARY KEY,
    output_type     TEXT        NOT NULL,
    collection_id   TEXT        NOT NULL,
    content_json    TEXT        DEFAULT '{}',
    provider        TEXT        DEFAULT '',
    status          TEXT        DEFAULT 'done',
    audio_path      TEXT,
    created_at      TEXT    DEFAULT (datetime('now')),
    tenant_id       TEXT    DEFAULT 'default',
    classification  TEXT    DEFAULT 'CUI'
);
CREATE INDEX IF NOT EXISTS idx_dic_generated_outputs_collection ON dic_generated_outputs(collection_id);
CREATE INDEX IF NOT EXISTS idx_dic_generated_outputs_type ON dic_generated_outputs(output_type);

CREATE TABLE IF NOT EXISTS dic_doc_views (
    view_id         TEXT    PRIMARY KEY,
    doc_id          TEXT    NOT NULL,
    user_id         TEXT    NOT NULL DEFAULT 'anonymous',
    collection_id   TEXT    NOT NULL DEFAULT 'default',
    viewed_at       TEXT    DEFAULT (datetime('now')),
    tenant_id       TEXT    DEFAULT 'default',
    classification  TEXT    DEFAULT 'CUI'
);
CREATE INDEX IF NOT EXISTS idx_dic_doc_views_doc ON dic_doc_views(doc_id);
CREATE INDEX IF NOT EXISTS idx_dic_doc_views_tenant ON dic_doc_views(tenant_id);
CREATE INDEX IF NOT EXISTS idx_dic_doc_views_viewed_at ON dic_doc_views(viewed_at);

-- Inter-document cross-references (dmx-ref-01). NOT append-only: target_doc_id
-- is NULL until the resolution pass fills it in.
CREATE TABLE IF NOT EXISTS dic_cross_references (
    id              TEXT    PRIMARY KEY,
    source_doc_id   TEXT    NOT NULL,
    source_section  TEXT    DEFAULT '',
    target_doc_ref  TEXT    NOT NULL,
    target_doc_id   TEXT,
    target_section  TEXT    DEFAULT '',
    ref_text        TEXT    DEFAULT '',
    tenant_id       TEXT    DEFAULT 'default',
    classification  TEXT    DEFAULT 'CUI',
    extracted_at    TEXT    DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_dic_cross_refs_source ON dic_cross_references(source_doc_id);
CREATE INDEX IF NOT EXISTS idx_dic_cross_refs_target ON dic_cross_references(target_doc_id);
CREATE INDEX IF NOT EXISTS idx_dic_cross_refs_tenant ON dic_cross_references(tenant_id);
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
        logger.info("DIC schema initialized")
    except Exception as exc:
        logger.warning("DIC db init error: %s", exc)


def get_connection():
    from tools.db.storage import get_connection as _gc
    return _gc()
