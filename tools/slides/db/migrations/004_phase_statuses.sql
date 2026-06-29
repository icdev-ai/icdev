-- Migration 004: Add generation phase status values to slides_decks CHECK constraint
-- Drops the old constraint and recreates it with gathering/planning/generating/graphics/building

ALTER TABLE slides_decks DROP CONSTRAINT IF EXISTS slides_decks_status_check;
ALTER TABLE slides_decks ADD CONSTRAINT slides_decks_status_check
    CHECK (status IN ('pending','running','gathering','planning','generating','graphics','building','completed','failed','auto'));
