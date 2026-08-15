-- Migration: 20260815002727_rag_retrieval_log_reflective_modes
-- CUI // SP-CTI
--
-- Allow 'reflective_reranked' and 'reflective_degraded' in
-- rag_retrieval_log.retrieval_mode.
--
-- oss-meas-01 wired step 5b and started writing retrieval_mode =
-- 'reflective_reranked' without widening this CHECK, and no migration in the
-- tree adds it. The write is inside _log_retrieval's best-effort try/except, so
-- on any database whose constraint was not patched out of band, every
-- reflectively reranked retrieval simply never appears in the retrieval log and
-- nothing reports an error -- the swallowed-INSERT defect CLAUDE.md describes,
-- on the very telemetry a reviewer would use to check whether the feature ran.
--
-- trust-self-02 adopts the toggle for chat_rag and adds the second value.
-- 'reflective_degraded' is the state that must be distinguishable: the
-- reflection pass RAN and judged nothing (unreachable model, malformed output),
-- so the ordering handed back is the incoming one. Folding that into
-- 'reflective_reranked' would claim a decision that was never made, and folding
-- it back into 'reranked' would erase the fact that the attempt happened and
-- cost something -- which is precisely the "measured, no benefit" vs "never
-- reached" confusion this card exists to end.
--
-- Idempotent: DROP CONSTRAINT IF EXISTS then ADD. Widening only, so no existing
-- row can violate the new constraint.

-- @pg-only
ALTER TABLE rag_retrieval_log
    DROP CONSTRAINT IF EXISTS rag_retrieval_log_retrieval_mode_check;

-- @pg-only
ALTER TABLE rag_retrieval_log
    ADD CONSTRAINT rag_retrieval_log_retrieval_mode_check
    CHECK (retrieval_mode IN ('vector','bm25','hybrid','rrf_hybrid','reranked',
                              'reflective_reranked','reflective_degraded'));

-- @sqlite-only
-- SQLite cannot ALTER a CHECK constraint; it is an init-only fallback here and
-- tools/db/init_icdev_db.py carries the widened list for a fresh database.
SELECT 1;
