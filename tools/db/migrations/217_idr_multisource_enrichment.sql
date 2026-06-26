-- CUI // SP-CTI
-- Migration 217: IDR Multi-Source Enrichment
-- Enables: email upload type, result_json inline storage, per-section confidence scoring,
--          flagged_sections for HITL, final_doc_text on sessions.

-- 1. Extend upload_type CHECK to include 'email' (PG — drop + re-add constraint).
--    SQLite does not enforce CHECK constraints so no change needed there.
DO $$ BEGIN
  ALTER TABLE idr_uploads DROP CONSTRAINT IF EXISTS idr_uploads_upload_type_check;
  ALTER TABLE idr_uploads ADD CONSTRAINT idr_uploads_upload_type_check
    CHECK (upload_type IN ('diagram','doc','config','iac','supplement','email'));
EXCEPTION WHEN others THEN
  RAISE NOTICE 'idr_uploads upload_type constraint update skipped: %', SQLERRM;
END $$;

-- 2. result_json — stores Stage 2 analysis output inline for context_builder to read.
--    (SQLite fallback: ADD COLUMN without IF NOT EXISTS guard; catches duplicate gracefully
--    via the DO block above; PG uses IF NOT EXISTS natively.)
ALTER TABLE idr_analyses ADD COLUMN IF NOT EXISTS result_json TEXT;

-- 3. confidence_score — overall attribution confidence for the analysis result (0.0–1.0).
ALTER TABLE idr_analyses ADD COLUMN IF NOT EXISTS confidence_score FLOAT;

-- 4. flagged_sections — JSON array of section headings that fell below the 0.7 confidence
--    threshold; surfaced to the HITL review page.
ALTER TABLE idr_artifacts ADD COLUMN IF NOT EXISTS flagged_sections TEXT;

-- 5. final_doc_text — assembled markdown from all generated sections; persisted so
--    WriteGuard and the HITL review page can read it without re-running the generator.
ALTER TABLE idr_sessions ADD COLUMN IF NOT EXISTS final_doc_text TEXT;
