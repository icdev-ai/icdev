-- Migration 279: OPORD paragraph grounding (nav-strat-02)
-- CUI // SP-CTI
--
-- OPORD paragraphs are LLM prose. The TRUST invariant requires every
-- LLM-drafted artifact to carry validated inline [source: ...] citations and a
-- persisted grounding verdict. This adds:
--   * sg_opords.grounding         — JSON map {paragraph_field: verdict}
--   * sg_opords.grounding_status  — rolled-up status (pending/grounded/
--                                    ungrounded/fallback), surfaced on the
--                                    OPORD detail/approval payload
--   * sg_opord_grounding_audit    — append-only (NIST AU) record of force
--                                    overrides that approve an ungrounded OPORD
--
-- Additive only. PG-authored; ADD COLUMN IF NOT EXISTS follows migration 275.

ALTER TABLE sg_opords ADD COLUMN IF NOT EXISTS grounding TEXT;
ALTER TABLE sg_opords ADD COLUMN IF NOT EXISTS grounding_status TEXT DEFAULT 'pending';

CREATE TABLE IF NOT EXISTS sg_opord_grounding_audit (
    id                TEXT PRIMARY KEY,
    opord_id          TEXT NOT NULL,
    action            TEXT NOT NULL,
    grounding_status  TEXT,
    actor             TEXT,
    reason            TEXT,
    created_at        TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_sg_opord_grounding_audit_opord
    ON sg_opord_grounding_audit(opord_id);
