-- Migration 002: Rich visual slide types + audience mode + pitch deck type
-- Adds mermaid/three/excalidraw columns to slides_slides,
-- audience_mode + enable_rich_diagrams to slides_decks,
-- and expands CHECK constraints for new type values.

-- slides_slides: diagram content columns
ALTER TABLE slides_slides ADD COLUMN IF NOT EXISTS mermaid_code TEXT;
ALTER TABLE slides_slides ADD COLUMN IF NOT EXISTS three_scene_config JSONB;
ALTER TABLE slides_slides ADD COLUMN IF NOT EXISTS excalidraw_elements JSONB;

-- slides_decks: opt-in flag + audience mode
ALTER TABLE slides_decks ADD COLUMN IF NOT EXISTS enable_rich_diagrams BOOLEAN DEFAULT false;
ALTER TABLE slides_decks ADD COLUMN IF NOT EXISTS audience_mode TEXT DEFAULT NULL;

-- PG: expand slide_type CHECK (drop auto-named, add named for future safety)
ALTER TABLE slides_slides DROP CONSTRAINT IF EXISTS slides_slides_slide_type_check;
ALTER TABLE slides_slides ADD CONSTRAINT chk_slide_type
    CHECK (slide_type IN (
        'title','agenda','content','two_column','quote','data','outro',
        'mermaid_diagram','three_animation','excalidraw_sketch','card_grid'
    ));

-- PG: expand deck_type CHECK
ALTER TABLE slides_decks DROP CONSTRAINT IF EXISTS slides_decks_deck_type_check;
ALTER TABLE slides_decks ADD CONSTRAINT chk_deck_type
    CHECK (deck_type IN (
        'executive_overview','canvas_deep_dive','govcon_proposal','compliance_briefing',
        'weekly_status','custom','general_presentation','pitch_deck'
    ));
