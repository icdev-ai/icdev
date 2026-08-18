-- Migration: 20260817014511_rag_provenance_ledger_retrieval_events
-- CUI // SP-CTI
--
-- cef-fnd-05: let rag_provenance_ledger record a RETRIEVAL event.
--
-- The table held 0 rows against 2,430 rows in rag_retrieval_log: it shipped
-- with three readers (dic/provenance_adapter, genesis/reflexes/aidp_monitor,
-- quality/citation_grounding) and NO writer anywhere in the tree. A citation
-- produced from a retrieved chunk therefore could not be traced back to its
-- source record after the fact, leaving the TRUST invariant half-met --
-- inline [source: ...] citations with no persisted provenance behind them.
--
-- Two schema changes are needed before a writer can exist:
--
-- 1. event_type CHECK admitted only ('ingest','chain_of_custody'). Writing
--    'retrieval' would raise a CHECK violation, and the retriever's step 7 sat
--    inside `except Exception: pass` -- so the naive fix would have left the
--    table at 0 rows with every test green. This is migration
--    20260815002727's defect one table over (a value no DDL allowed, dropped
--    silently by a best-effort INSERT). The vocabulary is derived from
--    tools/rag/provenance_ledger.py::PROVENANCE_EVENT_TYPES; widen there and
--    in every DDL copy together, never in one place.
--
-- 2. retrieval_log_id: the ledger had no column tying a row to the retrieval
--    event that produced it. chunk_uuid gave chunk -> source; this gives
--    chunk -> source -> retrieval event, which is what makes a citation
--    traceable. Nullable, because a ledger row is still worth keeping when the
--    rag_retrieval_log INSERT itself failed -- prompt_sha256 equals that
--    table's query_hash and remains a second path back to the event.
--
-- Widening only: no existing row can violate the new constraint, and the new
-- column is nullable. Idempotent (IF NOT EXISTS / DROP ... IF EXISTS), so it is
-- safe on a database where init_icdev_db.py already created the widened form.

-- @pg-only
ALTER TABLE rag_provenance_ledger
    ADD COLUMN IF NOT EXISTS retrieval_log_id INTEGER;

-- @pg-only
ALTER TABLE rag_provenance_ledger
    DROP CONSTRAINT IF EXISTS rag_provenance_ledger_event_type_check;

-- @pg-only
ALTER TABLE rag_provenance_ledger
    ADD CONSTRAINT rag_provenance_ledger_event_type_check
    CHECK (event_type IN ('ingest', 'chain_of_custody', 'retrieval'));

-- @pg-only
CREATE INDEX IF NOT EXISTS idx_rag_prov_retrieval_log
    ON rag_provenance_ledger(retrieval_log_id);

-- @sqlite-only
-- SQLite cannot ALTER a CHECK constraint, and rebuilding the table is not an
-- option on an append-only ledger (NIST AU -- the rows are the evidence). SQLite
-- is an init-only fallback here: tools/db/init_icdev_db.py carries the widened
-- CHECK and the new column for a fresh database, as does tests/conftest.py.
SELECT 1;
