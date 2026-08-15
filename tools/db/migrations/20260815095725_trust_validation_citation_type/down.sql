-- Rollback: 20260815095725_trust_validation_citation_type
-- CUI // SP-CTI
--
-- Narrows the vocabulary back to the migration-330 set.
--
-- DESTRUCTIVE IF ROWS EXIST. Unlike widening, narrowing an enum can be
-- rejected: PostgreSQL validates the new CHECK against every existing row, so
-- this fails outright while any trust_validation citation is present. That is
-- the correct behaviour and it is deliberately not forced — those rows are
-- provenance evidence for artifacts a human approved, and several of them are
-- already anchored to the chain by merkle_root. Deleting them to make a
-- rollback succeed would destroy the only local record of what a given anchored
-- Merkle root actually attests.
--
-- To roll back for real: first establish that no trust_validation row exists
-- (SELECT count(*) FROM source_citation_registry WHERE
-- citation_type='trust_validation'), and if any do, export them before
-- deciding.

-- @pg-only
ALTER TABLE source_citation_registry
    DROP CONSTRAINT IF EXISTS source_citation_registry_citation_type_check;
-- @pg-only
ALTER TABLE source_citation_registry
    ADD CONSTRAINT source_citation_registry_citation_type_check
    CHECK (citation_type IN ('hitl', 'rag', 'prov_entity', 'prov_activity', 'canvas_ai', 'slsa', 'sbom', 'compliance_evidence', 'agent_decision', 'manual', 'web', 'cortex', 'asset_token'));

-- @sqlite-only
SELECT 1;
