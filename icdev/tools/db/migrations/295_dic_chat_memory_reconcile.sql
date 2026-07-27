-- Migration 295: reconcile dic_chat_memory to the turn-based schema, safely,
-- on databases where migration 264 never ran.
--
-- WHY THIS EXISTS WHEN 264 ALREADY DOES
--
-- 264 is a bare `DROP TABLE IF EXISTS` + `CREATE TABLE`. It was never applied to
-- the live database (verified: schema_migrations has no row for 263/264/265),
-- and the PostgreSQL consolidation squash captured the PRE-264 state, so
-- pg_consolidated.sql shipped the legacy migration-191 message-log shape too.
-- The result: both migrated AND freshly-bootstrapped databases ended up with a
-- dic_chat_memory whose columns no code writes, every chat_memory.record_turn()
-- INSERT failed against it, the failure was swallowed, and DIC conversational
-- memory sat at 0 rows looking merely idle rather than broken.
--
-- Re-running 264 is not safe as a general repair: an unconditional DROP would
-- discard real turns on any database that DID get the turn schema. This
-- migration is shape-aware instead — it only rebuilds when the marker column
-- `turn_id` is absent, so it is a no-op on a correct database and idempotent on
-- repeat runs. The companion fix in pg_consolidated.sql stops fresh bootstraps
-- from creating the broken shape in the first place.
--
-- Data loss: none. The legacy table never received a successful write (its
-- only writer targets the turn columns), so there is nothing in it to preserve.
-- The guard still checks emptiness and refuses to drop a populated legacy table
-- rather than assume.
--
-- Kept byte-compatible with tools/document_intelligence/chat_memory.py::
-- _TURN_TABLE_DDL and with the pg_consolidated.sql definition.

DO $$
DECLARE
    has_turn_shape boolean;
    table_exists   boolean;
    legacy_rows    bigint := 0;
BEGIN
    SELECT EXISTS (
        SELECT 1 FROM information_schema.tables
        WHERE table_schema = 'public' AND table_name = 'dic_chat_memory'
    ) INTO table_exists;

    IF table_exists THEN
        SELECT EXISTS (
            SELECT 1 FROM information_schema.columns
            WHERE table_schema = 'public'
              AND table_name = 'dic_chat_memory'
              AND column_name = 'turn_id'
        ) INTO has_turn_shape;

        IF has_turn_shape THEN
            RAISE NOTICE 'dic_chat_memory already has the turn schema; nothing to do.';
            RETURN;
        END IF;

        EXECUTE 'SELECT count(*) FROM public.dic_chat_memory' INTO legacy_rows;
        IF legacy_rows > 0 THEN
            RAISE EXCEPTION
                'dic_chat_memory has the legacy schema but holds % row(s). '
                'Refusing to drop data. Inspect and migrate manually.', legacy_rows;
        END IF;

        RAISE NOTICE 'Dropping empty legacy dic_chat_memory (migration-191 shape).';
        DROP TABLE public.dic_chat_memory;
    END IF;

    CREATE TABLE IF NOT EXISTS public.dic_chat_memory (
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
END $$;

CREATE INDEX IF NOT EXISTS idx_dic_chat_memory_session
    ON dic_chat_memory (session_id, tenant_id);
