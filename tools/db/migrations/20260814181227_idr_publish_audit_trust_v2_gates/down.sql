-- Migration rollback: 20260814181227_idr_publish_audit_trust_v2_gates
-- CUI // SP-CTI
--
-- Narrow the CHECK back to the migration-300 value set.
--
-- NOT loss-free by construction: any override already recorded against
-- 'claim_guard' or 'constitution_guard' would violate the narrowed constraint
-- and the ALTER will fail. That is the correct behaviour — idr_publish_audit is
-- append-only evidence (NIST AU), so the fix is to leave the constraint wide,
-- never to delete the rows that make it necessary.

-- @pg-only
ALTER TABLE idr_publish_audit
    DROP CONSTRAINT IF EXISTS idr_publish_audit_gate_check;

-- @pg-only
ALTER TABLE idr_publish_audit
    ADD CONSTRAINT idr_publish_audit_gate_check
    CHECK (gate IN ('citation_guard','cove_guard','placeholder_guard'));

-- @sqlite-only
SELECT 1;
