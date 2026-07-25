-- CUI // SP-CTI
-- Migration 287: sag_user_profiles — standalone-agent per-user profile memory (sag-mem-01)
--
-- Backs tools/agent_runtime/profile_memory.py. One row per (user_id, tenant_id)
-- holding two JSON blobs:
--   * preferences_json — durable operator preferences (tone, default toolset,
--     approval mode, etc.) injected into the agent's system prompt at session start.
--   * facts_json       — durable facts about the user/work, each an object
--     {text, confidence, source, updated_at}, deduped by normalised text.
--
-- This is deliberately a LIGHTWEIGHT KV-style store, distinct from the Second
-- Brain `user_identity_profiles` table (a rich, toggle-gated identity record):
-- SAG must work without the Second Brain subsystem enabled, so it owns this small
-- table rather than coupling to that one.
--
-- Conventions (mirrors migration 286 notification_preferences): first-class
-- tenant_id + classification columns make it RLS-eligible via
-- tools.db.storage get_connection(); CREATE TABLE IF NOT EXISTS with TEXT-only
-- columns is idempotent and dialect-neutral (PostgreSQL primary, SQLite
-- init/test fallback). NOT append-only — a profile is mutable state (upsert /
-- forget); durable audit of changes is out of scope here. The runtime module also
-- self-creates this via _ensure_schema(), so a checkout that has not run this
-- migration still degrades gracefully.

CREATE TABLE IF NOT EXISTS sag_user_profiles (
    user_id          TEXT NOT NULL,
    tenant_id        TEXT DEFAULT '',
    classification   TEXT DEFAULT 'CUI',
    preferences_json TEXT DEFAULT '{}',
    facts_json       TEXT DEFAULT '[]',
    updated_at       TEXT,
    PRIMARY KEY (user_id, tenant_id)
);

CREATE INDEX IF NOT EXISTS idx_sag_user_profiles_tenant
    ON sag_user_profiles (tenant_id);
