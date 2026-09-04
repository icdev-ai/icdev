-- Rollback: 20260817014511_rag_provenance_ledger_retrieval_events
-- CUI // SP-CTI
--
-- Drops the index only. The widened CHECK and the retrieval_log_id column are
-- deliberately LEFT IN PLACE, and that is the whole rollback:
--
-- * rag_provenance_ledger is append-only (NIST AU-3) -- its rows ARE the
--   evidence. Dropping retrieval_log_id would destroy the chunk -> retrieval
--   event link on every row already written, which is a data-destroying
--   "rollback" of an audit table.
-- * Re-narrowing the CHECK to ('ingest','chain_of_custody') would be REFUSED by
--   PostgreSQL the moment one 'retrieval' row exists, so it cannot be the
--   rollback path either.
--
-- Both changes are additive and widening: with the cef-fnd-05 code reverted
-- nothing writes event_type='retrieval', so the schema is inert. That is the
-- reversible part.

-- @pg-only
DROP INDEX IF EXISTS idx_rag_prov_retrieval_log;

-- @sqlite-only
SELECT 1;
