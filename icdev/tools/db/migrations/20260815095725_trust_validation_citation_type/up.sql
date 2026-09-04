-- Migration: 20260815095725_trust_validation_citation_type
-- CUI // SP-CTI
--
-- Migration: add the 'trust_validation' citation type (trust-anchor-02).
--
-- WHAT IT CARRIES
--
-- One TRUST gate verdict, made anchorable. The registry row's `source_hash` is
-- the composed Merkle leaf
--
--     sha256(artifact_hash | findings_hash | delta_chain_hash | approver)
--
-- rendered by tools/provenance/trust_validation.py::validation_leaf, and
-- `source_doc` carries those four components as JSON so ChainAnchor can
-- RECOMPUTE the leaf at anchor time instead of trusting the stored value. A row
-- whose stored leaf disagrees with its own components is refused, never
-- anchored — anchoring a mismatched leaf would launder tampering onto the
-- chain, which is the opposite of what an anchor is for.
--
-- No new reflex. tools/genesis/reflexes/govchain_anchor.py already runs every
-- 30 minutes and calls ChainAnchor.periodic_anchor(), which sweeps
-- `source_citation_registry WHERE merkle_root IS NULL`. A trust_validation row
-- is swept by that same query, so this capability has a live consumer from the
-- moment it ships rather than a declared one waiting to be wired.
--
-- WHY THIS MIGRATION EXISTS AT ALL
--
-- register_citation() validates the type against CITATION_TYPES in Python and
-- raises ValueError BEFORE the INSERT. Every historical caller that passed an
-- unknown type sat inside a swallowing try/except, so the write simply never
-- happened and nothing went red: citation_type='cortex' recorded 0 of 285 rows
-- for its entire lifetime, and 'asset_token' never anchored once. Adding the
-- value to the Python constant without widening this CHECK reproduces that bug
-- one layer down — the Python gate passes and the database rejects the INSERT.
--
-- The CHECK clause below is RENDERED FROM the Python constant in
-- tools/provenance/citation_types.py::check_constraint_sql(). Do not hand-edit
-- it — tests/provenance/test_citation_types.py asserts the shipped SQL still
-- matches the constant, so an edit in one place without the other fails CI
-- rather than failing at INSERT time in production.
--
-- Companion update required by the same guardrail (mirrors migrations 297/330):
--   * tools/db/schema/pg_consolidated.sql constraint updated
--
-- No companion detail table: unlike 'web', the components live in `source_doc`
-- and are meaningful only to this type, so DETAIL_TABLES is unchanged.
--
-- Backward compatible: widening an enum never invalidates an existing row, so
-- no data migration is required. Idempotent: DROP CONSTRAINT IF EXISTS before
-- ADD.

-- @pg-only
ALTER TABLE source_citation_registry
    DROP CONSTRAINT IF EXISTS source_citation_registry_citation_type_check;
-- @pg-only
ALTER TABLE source_citation_registry
    ADD CONSTRAINT source_citation_registry_citation_type_check
    CHECK (citation_type IN ('hitl', 'rag', 'prov_entity', 'prov_activity', 'canvas_ai', 'slsa', 'sbom', 'compliance_evidence', 'agent_decision', 'manual', 'web', 'cortex', 'asset_token', 'trust_validation'));

-- @sqlite-only
-- SQLite cannot ALTER a CHECK constraint. It is an init-only fallback here, so
-- a fresh SQLite database gets the constraint rendered from the same constant
-- via sqlite_check_clause(). Nothing to normalise: the new value is NEW, so no
-- existing row can violate the old constraint.
SELECT 1;
