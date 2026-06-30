-- Migration 237: RFI Requirements layer + ACE team instance tracking
-- Adds requirements JSON column to sections and ace_instance_id to sessions.

ALTER TABLE rfi_workbench_sections ADD COLUMN IF NOT EXISTS requirements TEXT DEFAULT '[]';
ALTER TABLE rfi_workbench_sessions ADD COLUMN IF NOT EXISTS ace_instance_id TEXT;
CREATE INDEX IF NOT EXISTS idx_rfi_sessions_ace ON rfi_workbench_sessions(ace_instance_id);
