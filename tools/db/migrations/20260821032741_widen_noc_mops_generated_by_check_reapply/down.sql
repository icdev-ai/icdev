-- Rollback: 20260821032741_widen_noc_mops_generated_by_check_reapply
-- CUI // SP-CTI
--
-- Narrows the CHECK back to the pre-278 pair. Note this can FAIL if any
-- 'ai_template' row has been written in the meantime — which is the correct
-- behaviour: the rollback must not silently orphan rows it cannot represent.

-- @pg-only
ALTER TABLE noc_mops DROP CONSTRAINT IF EXISTS noc_mops_generated_by_check;
ALTER TABLE noc_mops ADD CONSTRAINT noc_mops_generated_by_check
    CHECK (generated_by = ANY (ARRAY['manual'::text, 'ai'::text]));
-- @all
