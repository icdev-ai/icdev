-- Migration 005: Deck-honesty provenance (nav-intel-04)
-- 1. Track how each slide's content was produced so degraded decks can be
--    honestly flagged instead of silently reporting "completed".
-- 2. Widen the deck status CHECK to allow the new honesty statuses
--    'degraded' (some slides/research fell back) and 'template' (canned outline).
-- Each statement is applied independently and tolerated if already present.

ALTER TABLE slides_slides ADD COLUMN provenance TEXT DEFAULT 'llm';

ALTER TABLE slides_decks DROP CONSTRAINT IF EXISTS slides_decks_status_check;
ALTER TABLE slides_decks ADD CONSTRAINT slides_decks_status_check
    CHECK (status IN ('pending','running','gathering','planning','generating','graphics','building','completed','degraded','template','failed','auto'));
