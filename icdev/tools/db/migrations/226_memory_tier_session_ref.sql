-- Migration 226: Memory tier + session cross-link + distilled flag
-- Closes the Harness gap: gives memory_entries a formal tier dimension
-- (procedural | episodic | semantic) and links episodic entries back to
-- the agent_loop_session that produced them.

ALTER TABLE memory_entries ADD COLUMN IF NOT EXISTS tier TEXT DEFAULT 'episodic'
    CHECK (tier IN ('procedural', 'episodic', 'semantic'));

ALTER TABLE memory_entries ADD COLUMN IF NOT EXISTS session_ref TEXT DEFAULT NULL;

ALTER TABLE memory_entries ADD COLUMN IF NOT EXISTS distilled INTEGER DEFAULT 0;

-- Backfill: map existing types to their natural tier
UPDATE memory_entries SET tier = 'semantic'
WHERE type IN ('fact', 'preference') AND tier = 'episodic';

CREATE INDEX IF NOT EXISTS idx_memory_tier ON memory_entries (tier);
CREATE INDEX IF NOT EXISTS idx_memory_distilled ON memory_entries (distilled)
    WHERE distilled = 0;
CREATE INDEX IF NOT EXISTS idx_memory_session_ref ON memory_entries (session_ref)
    WHERE session_ref IS NOT NULL;
