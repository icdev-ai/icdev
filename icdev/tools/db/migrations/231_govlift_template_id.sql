-- CUI // SP-CTI
-- Migration 231: Add template_id column to govlift_runbooks and govlift_marketplace_items
-- These tables are created by tools/govlift/db/init_db.py which already includes template_id
-- in CREATE TABLE IF NOT EXISTS, but existing PG databases need this ALTER TABLE path.
-- IF NOT EXISTS guard makes this idempotent on both fresh and upgraded installs.

ALTER TABLE govlift_runbooks
    ADD COLUMN IF NOT EXISTS template_id TEXT NOT NULL DEFAULT '';

ALTER TABLE govlift_marketplace_items
    ADD COLUMN IF NOT EXISTS template_id TEXT NOT NULL DEFAULT '';

CREATE INDEX IF NOT EXISTS idx_govlift_rb_template ON govlift_runbooks(template_id);
CREATE INDEX IF NOT EXISTS idx_govlift_mp_template ON govlift_marketplace_items(template_id);
