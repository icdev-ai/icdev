-- Migration 240: RFI section version history
-- Stores previous content before each save so users can revert to prior versions.

CREATE TABLE IF NOT EXISTS rfi_section_history (
    id TEXT PRIMARY KEY,
    section_id TEXT NOT NULL,
    session_id TEXT NOT NULL,
    content TEXT NOT NULL,
    saved_at TEXT NOT NULL,
    save_type TEXT NOT NULL DEFAULT 'manual'
);

CREATE INDEX IF NOT EXISTS idx_rfi_history_section ON rfi_section_history(section_id);
CREATE INDEX IF NOT EXISTS idx_rfi_history_session ON rfi_section_history(session_id);
