-- CUI // SP-CTI
-- Migration 329: 'cortex' + 'asset_token' citation types (cxo-trust-01).
--
-- Two subsystems have been writing provenance with a citation_type this
-- vocabulary did not contain, and both failed silently for their entire
-- existence.
--
--   * tools/cortex/governance.py registers the Cortex governance pipeline's
--     provenance gate as citation_type="cortex". register_citation() validates
--     against CITATION_TYPES BEFORE the INSERT and raises ValueError; the gate
--     catches Exception and records provenance="warn". Because
--     governance.fail_closed ships false, nothing blocked and nothing alerted.
--
--   * tools/blockchain/asset_ledger.py registers asset tokenization as
--     citation_type="asset_token". There the raise is swallowed by a try/except
--     with an `if reg_id:` guard, so reg_id stayed None, anchor_status stayed
--     "skipped", and the registry_id/tx_id back-fill never ran — GovChain
--     asset tokenization has never anchored to the chain.
--
-- Measured 2026-08-02 against live PostgreSQL: source_citation_registry held
-- 285 rows of exactly two types (prov_entity 187, canvas_ai 98). Zero 'cortex',
-- zero 'asset_token'. cortex_audit showed 95 warn against 14 pass, and no
-- Cortex operation had ever recorded a clean pass.
--
-- Two independent subsystems shipping the identical bug makes this a MISSING
-- GATE rather than two defects, which is why cxo-trust-02 adds a static check
-- that every citation_type literal under tools/ is in CITATION_TYPES.
--
-- The CHECK clause below is RENDERED FROM the Python constant in
-- tools/provenance/citation_types.py::check_constraint_sql(). Do not hand-edit
-- it — tests/provenance/test_citation_types.py asserts the shipped SQL still
-- matches the constant, so an edit in one place without the other fails CI
-- rather than failing at INSERT time in production.
--
-- Companion updates required by the same guardrail (mirrors migrations 254/297):
--   * tools/db/schema/pg_consolidated.sql constraint updated
--
-- No companion detail table: unlike 'web', neither type carries provenance
-- fields that are meaningless to the others, so DETAIL_TABLES is unchanged.
--
-- Backward compatible: widening an enum never invalidates an existing row, so
-- this is safe against the 285 rows already present. Idempotent:
-- DROP CONSTRAINT IF EXISTS before ADD.

-- @pg-only
ALTER TABLE source_citation_registry
    DROP CONSTRAINT IF EXISTS source_citation_registry_citation_type_check;
-- @pg-only
ALTER TABLE source_citation_registry
    ADD CONSTRAINT source_citation_registry_citation_type_check
    CHECK (citation_type IN ('hitl', 'rag', 'prov_entity', 'prov_activity', 'canvas_ai', 'slsa', 'sbom', 'compliance_evidence', 'agent_decision', 'manual', 'web', 'cortex', 'asset_token'));
