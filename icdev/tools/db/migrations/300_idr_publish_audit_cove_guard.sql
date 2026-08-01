-- CUI // SP-CTI
-- Migration 300: allow 'cove_guard' in idr_publish_audit.gate.
--
-- idr_publish_audit records HITL force-overrides of a publish gate. Its CHECK
-- constraint listed only 'citation_guard' and 'placeholder_guard', so wiring
-- the shipped CoVe guard (tools/quality/cove_guard.py, agx-verify-01) as a
-- third gate had nowhere to record an override: the INSERT would fail on the
-- constraint at exactly the moment a reviewer overrode a verification failure,
-- which is the one event that must never go unrecorded (NIST AU).
--
-- The value set is now derived from
-- tools.quality.citation_grounding.PUBLISH_GATES, per the CLAUDE.md rule that
-- SQL CHECK constraints come from a Python constant rather than a hardcoded
-- list. tests/test_publish_gates.py asserts the constant, this migration and
-- pg_consolidated.sql all agree, so adding a fourth gate without a migration
-- fails in CI instead of at 2am on a reviewer's override.
--
-- Idempotent: DROP CONSTRAINT IF EXISTS then ADD. Widening only — every value
-- previously accepted is still accepted, so no existing row can violate it and
-- no data migration is required.
--
-- Version 300 deliberately skips 298/299: those were offered to the two open
-- PRs (#824, #819) that still claim 295, so that resolving one collision does
-- not create another. Picking a migration number is a repo-wide claim — check
-- open PRs, not just main. See docs/features/migration-backlog-triage.md.

-- @pg-only
ALTER TABLE idr_publish_audit
    DROP CONSTRAINT IF EXISTS idr_publish_audit_gate_check;

-- @pg-only
ALTER TABLE idr_publish_audit
    ADD CONSTRAINT idr_publish_audit_gate_check
    CHECK (gate IN ('citation_guard','cove_guard','placeholder_guard'));

-- @sqlite-only
-- SQLite cannot ALTER a CHECK constraint. It is an init-only fallback here, so
-- init_icdev_db.py creates the table fresh with the constraint rendered from
-- PUBLISH_GATES. Nothing to normalise: 'cove_guard' is a NEW value, so no
-- existing row can violate the old constraint.
SELECT 1;
