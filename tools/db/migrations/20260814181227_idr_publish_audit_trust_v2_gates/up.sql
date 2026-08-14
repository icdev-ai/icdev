-- Migration: 20260814181227_idr_publish_audit_trust_v2_gates
-- CUI // SP-CTI
--
-- Allow 'claim_guard' and 'constitution_guard' in idr_publish_audit.gate.
--
-- TRUST v2 (trust-spine-01) wires two guards that had shipped with ZERO
-- production callers: the claim tier in tools/quality/citation_grounding.py
-- (decompose_claims -> bind_claim_span -> verify_claim -> claim_gate) and
-- constitutional_review in tools/quality/constitutional_ai.py, whose entire
-- rule block in args/security_gates.yaml had never been critiqued against.
--
-- Wiring them without widening this constraint would reproduce, exactly, the
-- failure migration 300 was written to fix: the guard blocks correctly, the
-- reviewer force-overrides it, and the INSERT recording that override dies on
-- the CHECK — losing the one event that must never go unrecorded (NIST AU).
--
-- The value set is derived from
-- tools.quality.citation_grounding.PUBLISH_GATES, per the CLAUDE.md rule that
-- SQL CHECK constraints come from a Python constant rather than a hardcoded
-- list. tests/test_publish_gates.py asserts the constant, this migration and
-- pg_consolidated.sql all agree.
--
-- kg_guard (phase 2) and structure_guard (phase 3) are deliberately NOT added
-- here. Each ships with the migration that widens the constraint for it, so a
-- gate value always corresponds to a guard that can actually emit it.
--
-- Idempotent: DROP CONSTRAINT IF EXISTS then ADD. Widening only — every value
-- previously accepted is still accepted, so no existing row can violate it and
-- no data migration is required.

-- @pg-only
ALTER TABLE idr_publish_audit
    DROP CONSTRAINT IF EXISTS idr_publish_audit_gate_check;

-- @pg-only
ALTER TABLE idr_publish_audit
    ADD CONSTRAINT idr_publish_audit_gate_check
    CHECK (gate IN ('citation_guard','claim_guard','constitution_guard','cove_guard','placeholder_guard'));

-- @sqlite-only
-- SQLite cannot ALTER a CHECK constraint. It is an init-only fallback here, so
-- init_icdev_db.py creates the table fresh with the constraint rendered from
-- PUBLISH_GATES. Nothing to normalise: both new values are NEW, so no existing
-- row can violate the old constraint.
SELECT 1;
