-- CUI // SP-CTI
-- Migration 291: sag_skill_registry — promoted auto-skill curation (sag-skl-01).
--
-- The standalone agent proposes skills via NOVA's existing generator (queued in
-- agent_improvement_artifacts, status='pending'). A human approves/rejects/edits
-- (HITL); an APPROVED proposal is written to .agents/skills/icdev-auto-<name>/
-- SKILL.md (parseable by tools/skills/registry.py) with provenance frontmatter.
--
-- This table tracks each PROMOTED auto-skill so the curator reflex
-- (tools/genesis/reflexes/sag_skill_curator.py) can enforce lifecycle:
-- use_count / last_activity_at, archive-never-delete after N idle days, and pin
-- support. It does NOT duplicate the proposal queue (that stays in
-- agent_improvement_artifacts) — it is the durable record of what was promoted.
--
-- Conventions (mirror migrations 287-290): TEXT-only, dialect-neutral, CREATE
-- TABLE IF NOT EXISTS is idempotent. Mutable state (use_count bumps, pin toggles,
-- archive), NOT append-only. The module self-creates this via _ensure_schema(),
-- so an un-migrated checkout still works. Provenance of the LLM-generated skill
-- (session id, model, generated_at) is a TRUST record — persisted both here and
-- in the SKILL.md frontmatter.

CREATE TABLE IF NOT EXISTS sag_skill_registry (
    name             TEXT PRIMARY KEY,            -- icdev-auto-<slug>
    artifact_id      TEXT,                        -- source agent_improvement_artifacts row
    skill_dir        TEXT,                        -- .agents/skills/icdev-auto-<slug>
    session_id       TEXT DEFAULT '',             -- provenance: originating SAG session
    model            TEXT DEFAULT '',             -- provenance: generating model
    approved_by      TEXT DEFAULT '',
    status           TEXT DEFAULT 'active',       -- active | archived
    pinned           INTEGER DEFAULT 0,           -- pinned skills are never archived
    use_count        INTEGER DEFAULT 0,
    last_activity_at TEXT,
    classification   TEXT DEFAULT 'CUI',
    created_at       TEXT,
    updated_at       TEXT
);

CREATE INDEX IF NOT EXISTS idx_sag_skill_registry_status
    ON sag_skill_registry (status, last_activity_at);
