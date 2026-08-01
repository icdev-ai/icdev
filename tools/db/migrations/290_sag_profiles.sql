-- CUI // SP-CTI
-- Migration 290: sag_profiles + sag_user_profiles.profile — standalone-agent
-- profile isolation (sag-prof-01).
--
-- Adds directory-based operator profiles (work / personal / client-x) that
-- isolate an agent identity WITHOUT forking storage into per-profile .db files:
-- PostgreSQL stays the single primary and the existing tenant plumbing does the
-- filtering (the SAG runtime namespaces the tenant as <tenant>::prof:<name> — see
-- tools/agent_runtime/profiles.py::scoped_tenant).
--
--   * sag_profiles — durable registry so `icdev profile list` can enumerate
--     profiles and tools can discover state directories (~/.icdev/profiles/<name>/).
--     Mutable state (description/state_dir update), NOT append-only.
--   * sag_user_profiles.profile — an additive, nullable TAG column recording which
--     profile a memory row belongs to (derived from the namespaced tenant on
--     write). Isolation is already guaranteed by the namespaced tenant_id, so this
--     column is for queryability/observability and does NOT change the
--     (user_id, tenant_id) primary key — keeping the ALTER safe.
--
-- Conventions (mirror migrations 287/288/289): TEXT-only, dialect-neutral. The
-- ALTER is PG-only (ADD COLUMN IF NOT EXISTS, per migration 275); on the SQLite
-- init/test fallback the runtime's self-create DDL already carries the column, so
-- no ALTER is needed there. The runtime module self-creates sag_profiles via
-- _ensure_schema(), so an un-migrated checkout still works.

CREATE TABLE IF NOT EXISTS sag_profiles (
    name           TEXT PRIMARY KEY,
    state_dir      TEXT,
    description    TEXT DEFAULT '',
    classification TEXT DEFAULT 'CUI',
    created_at     TEXT,
    updated_at     TEXT
);

-- @pg-only
ALTER TABLE sag_user_profiles ADD COLUMN IF NOT EXISTS profile TEXT DEFAULT '';
-- @all
