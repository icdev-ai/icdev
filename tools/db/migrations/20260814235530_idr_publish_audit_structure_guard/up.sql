-- Migration: 20260814235530_idr_publish_audit_structure_guard
-- CUI // SP-CTI
--
-- Allow 'structure_guard' in idr_publish_audit.gate.
--
-- TRUST v2 phase 3 (trust-struct-03) wires contract validation into stage 1.
-- Wiring a guard without widening this constraint reproduces the failure
-- migration 300 was written to fix: the guard blocks, the reviewer
-- force-overrides it, and the INSERT recording that override dies on the CHECK
-- -- losing the one event that must never go unrecorded (NIST AU).
--
-- The value set is derived from
-- tools.quality.citation_grounding.PUBLISH_GATES, per the CLAUDE.md rule that
-- SQL CHECK constraints come from a Python constant rather than a hardcoded
-- list. tests/test_publish_gates.py asserts the constant, this migration and
-- pg_consolidated.sql all agree.
--
-- Idempotent: DROP CONSTRAINT IF EXISTS then ADD. Widening only.

-- @pg-only
ALTER TABLE idr_publish_audit
    DROP CONSTRAINT IF EXISTS idr_publish_audit_gate_check;

-- @pg-only
ALTER TABLE idr_publish_audit
    ADD CONSTRAINT idr_publish_audit_gate_check
    CHECK (gate IN ('citation_guard','claim_guard','constitution_guard','cove_guard','kg_guard','placeholder_guard','structure_guard'));

-- @sqlite-only
-- SQLite cannot ALTER a CHECK constraint; it is an init-only fallback here.
-- 'structure_guard' is a NEW value, so no existing row can violate the old
-- constraint.
SELECT 1;
