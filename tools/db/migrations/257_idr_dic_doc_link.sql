-- CUI // SP-CTI
-- 257: docgen -> Tech Writer bridge — persist the DIC document that generation
--      creates so the import route can reuse it instead of rebuilding an empty
--      scaffold (docmod-bridge-01).
--
-- idr_sessions.dic_doc_id        — dic_documents.doc_id produced by stage-5 generation
-- idr_sessions.source_dic_doc_id — reverse bridge: DIC doc a regen session was started from
-- dic_documents.source_wg_result_id    — WriteGuard result carried over from the docgen session
-- dic_documents.source_idr_session_id  — provenance backlink to the originating docgen session

ALTER TABLE idr_sessions ADD COLUMN IF NOT EXISTS dic_doc_id TEXT;
ALTER TABLE idr_sessions ADD COLUMN IF NOT EXISTS source_dic_doc_id TEXT;

ALTER TABLE dic_documents ADD COLUMN IF NOT EXISTS source_wg_result_id TEXT;
ALTER TABLE dic_documents ADD COLUMN IF NOT EXISTS source_idr_session_id TEXT;

-- Safety net: some sites predate migration 217 (final_doc_text); idempotent re-add.
ALTER TABLE idr_sessions ADD COLUMN IF NOT EXISTS final_doc_text TEXT;
