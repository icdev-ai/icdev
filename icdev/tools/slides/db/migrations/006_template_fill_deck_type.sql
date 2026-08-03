-- Migration 006: admit the template_fill deck type (sdt-vocab-01 / sdt-mig-01)
--
-- tools/slides/blueprint.py persists deck_type='template_fill' for every deck
-- built by filling an uploaded .pptx. That value was never in the constraint, so
-- the route could not persist a deck against a correctly-created PostgreSQL
-- schema. Verified on the live database before this migration: chk_deck_type
-- listed eight types and template_fill was not one of them.
--
-- Fresh databases do not need this file. _SCHEMA_PG builds the CHECK from
-- constants.py::CHECK_DECK_TYPE, which now derives from PERSISTED_DECK_TYPES
-- (the selectable DECK_TYPES plus SYSTEM_DECK_TYPES), so a new database is
-- already correct. This exists only for databases created before that change.
--
-- The vocabulary is duplicated here because the runner reads .sql and cannot
-- import Python. constants.py::PERSISTED_DECK_TYPES stays canonical -- a new
-- deck type belongs there first, and reaches existing databases through a NEW
-- migration, never by editing an applied one (the runner skips any file whose
-- version is not greater than the stored version, so an edit to an applied
-- migration silently does nothing).
--
-- SQLite is unaffected in both directions: _SCHEMA_SQLITE declares no CHECK on
-- deck_type, so there is nothing to widen and nothing to rebuild. The runner
-- tolerates the failure of the statements below on that engine, which is the
-- same way migrations 002 and 005 behave.

-- 002 replaced the auto-named constraint with chk_deck_type. Drop both, so this
-- works whether the database last passed through 002 or predates it.
ALTER TABLE slides_decks DROP CONSTRAINT IF EXISTS slides_decks_deck_type_check;
ALTER TABLE slides_decks DROP CONSTRAINT IF EXISTS chk_deck_type;
ALTER TABLE slides_decks ADD CONSTRAINT chk_deck_type
    CHECK (deck_type IN (
        'executive_overview','canvas_deep_dive','govcon_proposal','compliance_briefing',
        'weekly_status','custom','general_presentation','pitch_deck',
        'template_fill'
    ));
