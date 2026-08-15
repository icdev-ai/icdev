-- Migration rollback: 20260815002727_rag_retrieval_log_reflective_modes
-- CUI // SP-CTI
--
-- Narrow the CHECK back to the pre-reflection value set. NOT loss-free: any row
-- already logged with 'reflective_reranked' or 'reflective_degraded' violates
-- the narrowed constraint and the ALTER fails. That is the correct outcome --
-- rag_retrieval_log is retrieval telemetry, and the fix for a mode value in the
-- table is to leave the constraint wide, not to delete the evidence that the
-- feature ran.

-- @pg-only
ALTER TABLE rag_retrieval_log
    DROP CONSTRAINT IF EXISTS rag_retrieval_log_retrieval_mode_check;

-- @pg-only
ALTER TABLE rag_retrieval_log
    ADD CONSTRAINT rag_retrieval_log_retrieval_mode_check
    CHECK (retrieval_mode IN ('vector','bm25','hybrid','rrf_hybrid','reranked'));

-- @sqlite-only
SELECT 1;
