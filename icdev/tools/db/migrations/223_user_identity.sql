-- CUI // SP-CTI
-- Migration 223: Second Brain / AI Executive Assistant — user identity model.
-- Stores per-user profile, objectives, relationships, challenges, integration
-- tokens, and daily briefings.  All tables are canvas-scoped (no classification /
-- tenant_id RLS columns); connections MUST use get_canvas_connection().
--
-- cnr-me-04: timestamp DEFAULTs use the portable CURRENT_TIMESTAMP (valid on
-- both SQLite and PostgreSQL, matching schema/pg_consolidated.sql) rather than
-- the SQLite-only datetime('now'), so the migration is dialect-safe and does not
-- rely on translate_sql rewriting DDL defaults.

CREATE TABLE IF NOT EXISTS user_identity_profiles (
    user_id          TEXT NOT NULL,
    tenant_id        TEXT NOT NULL DEFAULT 'default',
    full_name        TEXT,
    work_email       TEXT,
    title            TEXT,
    seniority_tier   TEXT CHECK(seniority_tier IN ('ic','lead','manager','director','executive')),
    department       TEXT,
    timezone         TEXT NOT NULL DEFAULT 'UTC',
    work_start       TEXT NOT NULL DEFAULT '09:00',
    work_end         TEXT NOT NULL DEFAULT '18:00',
    focus_block      TEXT NOT NULL DEFAULT 'am' CHECK(focus_block IN ('am','pm','none')),
    meeting_heavy_days TEXT NOT NULL DEFAULT '[]',
    briefing_time    TEXT NOT NULL DEFAULT '08:00',
    delivery_channels TEXT NOT NULL DEFAULT '["dashboard"]',
    comm_style       INTEGER NOT NULL DEFAULT 3,
    org_name         TEXT,
    org_industry     TEXT,
    org_size         TEXT,
    team_mission     TEXT,
    profile_summary  TEXT,
    context_complete INTEGER NOT NULL DEFAULT 0,
    created_at       TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at       TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (user_id, tenant_id)
);

CREATE TABLE IF NOT EXISTS user_objectives (
    id          TEXT PRIMARY KEY,
    user_id     TEXT NOT NULL,
    tenant_id   TEXT NOT NULL DEFAULT 'default',
    title       TEXT NOT NULL,
    description TEXT,
    horizon     TEXT CHECK(horizon IN ('week','quarter','long_term')),
    metric      TEXT,
    status      TEXT NOT NULL DEFAULT 'active' CHECK(status IN ('active','done','dropped')),
    sort_order  INTEGER NOT NULL DEFAULT 0,
    created_at  TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_user_obj
    ON user_objectives (user_id, tenant_id, status);

CREATE TABLE IF NOT EXISTS user_relationships (
    id                TEXT PRIMARY KEY,
    user_id           TEXT NOT NULL,
    tenant_id         TEXT NOT NULL DEFAULT 'default',
    name              TEXT NOT NULL,
    title             TEXT,
    email             TEXT,
    org               TEXT,
    relationship_type TEXT CHECK(relationship_type IN ('boss','direct','peer','stakeholder','customer','vendor','other')),
    notes             TEXT,
    last_contact_at   TEXT,
    created_at        TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_user_rel
    ON user_relationships (user_id, tenant_id);

CREATE TABLE IF NOT EXISTS user_challenges (
    id                   TEXT PRIMARY KEY,
    user_id              TEXT NOT NULL,
    tenant_id            TEXT NOT NULL DEFAULT 'default',
    challenge_key        TEXT,
    custom_description   TEXT,
    severity             TEXT NOT NULL DEFAULT 'medium' CHECK(severity IN ('low','medium','high')),
    status               TEXT NOT NULL DEFAULT 'active' CHECK(status IN ('active','resolved')),
    created_at           TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_user_chal
    ON user_challenges (user_id, tenant_id, status);

CREATE TABLE IF NOT EXISTS user_integrations (
    id                TEXT PRIMARY KEY,
    user_id           TEXT NOT NULL,
    tenant_id         TEXT NOT NULL DEFAULT 'default',
    -- Service list derived from icdev.tools.second_brain.constants.INTEGRATION_SERVICES
    -- (guarded by tests/test_second_brain.py::TestMsGraphIntegration). 'msgraph'
    -- MUST be present — blueprint.py persists Microsoft Graph tokens (cnr-me-03).
    service           TEXT NOT NULL CHECK(service IN ('gmail','gcal','slack','msgraph','github','gitlab','jira','linear','notion')),
    access_token_enc  TEXT,
    refresh_token_enc TEXT,
    token_expiry      TEXT,
    scopes            TEXT,
    metadata_json     TEXT NOT NULL DEFAULT '{}',
    status            TEXT NOT NULL DEFAULT 'active' CHECK(status IN ('active','revoked','error')),
    last_sync_at      TEXT,
    created_at        TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(user_id, tenant_id, service)
);

CREATE TABLE IF NOT EXISTS user_daily_briefings (
    id                   TEXT PRIMARY KEY,
    user_id              TEXT NOT NULL,
    tenant_id            TEXT NOT NULL DEFAULT 'default',
    briefing_date        TEXT NOT NULL,
    content_json         TEXT NOT NULL DEFAULT '{}',
    delivery_channels    TEXT NOT NULL DEFAULT '["dashboard"]',
    delivery_status_json TEXT NOT NULL DEFAULT '{}',
    opened_at            TEXT,
    created_at           TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(user_id, tenant_id, briefing_date)
);

CREATE INDEX IF NOT EXISTS idx_briefing
    ON user_daily_briefings (user_id, tenant_id, briefing_date);
