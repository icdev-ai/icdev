-- Migration 239: RFI engine weights + past-performance uploads
-- Phase B: weighted context assembly for AI section generation

-- Per-session past-performance and reference doc uploads
CREATE TABLE IF NOT EXISTS rfi_session_uploads (
    id          TEXT PRIMARY KEY,
    session_id  TEXT NOT NULL,
    filename    TEXT NOT NULL,
    file_path   TEXT NOT NULL,
    upload_type TEXT NOT NULL DEFAULT 'past_performance',
    extracted_text TEXT,
    file_size   INTEGER,
    created_at  TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_rfi_uploads_session ON rfi_session_uploads(session_id);

-- Engine weight config per session (JSON blob of 6 engine knobs)
ALTER TABLE rfi_workbench_sessions ADD COLUMN IF NOT EXISTS engine_weights TEXT DEFAULT NULL;

-- Per-section engine weight override (NULL = inherit from session)
ALTER TABLE rfi_workbench_sections ADD COLUMN IF NOT EXISTS engine_weights TEXT DEFAULT NULL;
