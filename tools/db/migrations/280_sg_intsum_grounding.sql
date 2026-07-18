-- CUI // SP-CTI
-- Migration 279: INTSUM grounding verdict (nav-strat-01, TRUST invariant)
-- Persists the per-INTSUM and per-paragraph citation-grounding verdict produced
-- by tools/strategos/intsum.py, plus an append-only audit of HITL
-- force-ungrounded overrides. sg_intsum_grounding_audit is registered in
-- APPEND_ONLY_TABLES (.claude/hooks/pre_tool_use.py) — never UPDATE or DELETE.

ALTER TABLE sg_intsums ADD COLUMN grounding_status TEXT;
ALTER TABLE sg_intsums ADD COLUMN grounding_json TEXT;

ALTER TABLE sg_intsum_paragraphs ADD COLUMN grounded INTEGER DEFAULT 1;
ALTER TABLE sg_intsum_paragraphs ADD COLUMN require_citations INTEGER DEFAULT 0;
ALTER TABLE sg_intsum_paragraphs ADD COLUMN citations TEXT;

CREATE TABLE IF NOT EXISTS sg_intsum_grounding_audit (
    id          TEXT PRIMARY KEY,
    intsum_id   TEXT NOT NULL,
    findings    TEXT,                    -- JSON array of the overridden citation defects
    created_at  TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_sg_intsum_grounding_audit_intsum
    ON sg_intsum_grounding_audit(intsum_id);
