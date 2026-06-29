-- Migration 213: Persist WriteGuard-approved document text on idr_sessions
-- Allows the publish step to use the fixed_text from WriteGuard without
-- requiring the client to re-submit the full document body.

ALTER TABLE idr_sessions ADD COLUMN IF NOT EXISTS final_doc_text TEXT;
