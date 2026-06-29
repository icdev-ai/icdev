-- Migration 233: role-adaptive customer expectation fields
-- Adds expectations_json (role-driven key/value pairs) and commitment_date
-- to user_relationships. Both columns are optional; existing rows default to
-- empty JSON object and NULL commitment date.

ALTER TABLE user_relationships
    ADD COLUMN IF NOT EXISTS expectations_json TEXT DEFAULT '{}';

ALTER TABLE user_relationships
    ADD COLUMN IF NOT EXISTS commitment_date TEXT;
