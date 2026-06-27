-- Migration 003: table slide type

-- PG: expand slide_type CHECK to include 'table'
ALTER TABLE slides_slides DROP CONSTRAINT IF EXISTS chk_slide_type;
ALTER TABLE slides_slides DROP CONSTRAINT IF EXISTS slides_slides_slide_type_check;
ALTER TABLE slides_slides ADD CONSTRAINT chk_slide_type
    CHECK (slide_type IN (
        'title','agenda','content','two_column','quote','data','outro',
        'mermaid_diagram','three_animation','excalidraw_sketch','card_grid','table'
    ));
