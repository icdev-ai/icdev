-- Migration 001: general_presentation deck support
-- Runs under PostgreSQL and SQLite; runner tolerates already-present columns.

ALTER TABLE slides_decks ADD COLUMN IF NOT EXISTS tone TEXT DEFAULT 'professional';
ALTER TABLE slides_decks ADD COLUMN IF NOT EXISTS occasion TEXT;
ALTER TABLE slides_decks ADD COLUMN IF NOT EXISTS target_audience TEXT;
ALTER TABLE slides_decks ADD COLUMN IF NOT EXISTS citation_style TEXT DEFAULT 'inline_links';
ALTER TABLE slides_decks ADD COLUMN IF NOT EXISTS output_formats JSONB DEFAULT '["pptx"]';
ALTER TABLE slides_decks ADD COLUMN IF NOT EXISTS pdf_path TEXT;
ALTER TABLE slides_decks ADD COLUMN IF NOT EXISTS html_path TEXT;

ALTER TABLE slides_slides ADD COLUMN IF NOT EXISTS citations JSONB DEFAULT '[]';
