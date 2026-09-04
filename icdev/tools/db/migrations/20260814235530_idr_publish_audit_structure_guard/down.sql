-- Migration rollback: 20260814235530_idr_publish_audit_structure_guard
-- CUI // SP-CTI
--
-- Narrow the CHECK back to the pre-structure_guard value set. NOT loss-free:
-- any override already recorded against 'structure_guard' violates the
-- narrowed constraint and the ALTER fails. That is correct -- idr_publish_audit
-- is append-only evidence (NIST AU), so the fix is to leave the constraint
-- wide, never to delete the rows that make it necessary.

-- @pg-only
ALTER TABLE idr_publish_audit
    DROP CONSTRAINT IF EXISTS idr_publish_audit_gate_check;

-- @pg-only
ALTER TABLE idr_publish_audit
    ADD CONSTRAINT idr_publish_audit_gate_check
    CHECK (gate IN ('citation_guard','claim_guard','constitution_guard','cove_guard','kg_guard','placeholder_guard'));

-- @sqlite-only
SELECT 1;
