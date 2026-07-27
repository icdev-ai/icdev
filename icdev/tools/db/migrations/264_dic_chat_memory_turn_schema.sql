-- Migration 264: reconcile dic_chat_memory to the turn-based schema the code uses.
--
-- Migration 191 created dic_chat_memory with a message-log schema
-- (memory_id / role / content / token_count) that NO code ever consumed.
-- tools/document_intelligence/chat_memory.py::record_turn INSERTs a DIFFERENT,
-- turn-based schema (turn_id / turn_index / query / answer / subject /
-- subject_doc_id / entities_json / doc_ids_json / citations_json / mode), so
-- every write silently failed (caught -> returns None) and DIC conversational
-- memory has been dead since the module was rewritten.
--
-- The old table has a single (broken) consumer and never received a successful
-- write, so DROP + recreate is safe and loses no real data. Kept byte-for-byte in
-- sync with chat_memory.py::_TURN_TABLE_DDL (which self-heals fresh DBs).

DROP TABLE IF EXISTS dic_chat_memory;

CREATE TABLE IF NOT EXISTS dic_chat_memory (
    turn_id         TEXT PRIMARY KEY,
    session_id      TEXT NOT NULL,
    collection_id   TEXT NOT NULL DEFAULT '',
    turn_index      INTEGER NOT NULL DEFAULT 0,
    query           TEXT NOT NULL DEFAULT '',
    answer          TEXT NOT NULL DEFAULT '',
    subject         TEXT NOT NULL DEFAULT '',
    subject_doc_id  TEXT NOT NULL DEFAULT '',
    entities_json   TEXT NOT NULL DEFAULT '[]',
    doc_ids_json    TEXT NOT NULL DEFAULT '[]',
    citations_json  TEXT NOT NULL DEFAULT '[]',
    mode            TEXT NOT NULL DEFAULT 'grounded',
    created_at      TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    tenant_id       TEXT NOT NULL DEFAULT 'default',
    classification  TEXT NOT NULL DEFAULT 'CUI'
);
CREATE INDEX IF NOT EXISTS idx_dic_chat_memory_session ON dic_chat_memory (session_id, tenant_id);
