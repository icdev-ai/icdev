-- Migration 235: customer interaction history
CREATE TABLE IF NOT EXISTS user_relationship_interactions (
    id               TEXT PRIMARY KEY,
    relationship_id  TEXT NOT NULL,
    user_id          TEXT NOT NULL,
    tenant_id        TEXT NOT NULL DEFAULT 'default',
    interaction_date TEXT NOT NULL,
    title            TEXT NOT NULL,
    notes            TEXT,
    action_items     TEXT DEFAULT '[]',
    follow_up_date   TEXT,
    interaction_type TEXT DEFAULT 'meeting'
        CHECK(interaction_type IN ('meeting','email','call','delivery','review','other')),
    created_at       TEXT DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
);
CREATE INDEX IF NOT EXISTS idx_rel_interactions ON user_relationship_interactions(relationship_id, user_id, interaction_date DESC);
