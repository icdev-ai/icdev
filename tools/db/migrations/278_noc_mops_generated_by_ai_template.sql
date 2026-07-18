-- CUI // SP-CTI
-- Migration 278: widen noc_mops.generated_by CHECK to include 'ai_template'.
--
-- Root cause (fix/noc-pg-500s): mop_generator.generate_mop() records
-- generated_by='ai_template' whenever the LLM is unavailable and it falls back
-- to the deterministic template (the common CI / air-gap path). The original
-- CHECK only allowed ('manual','ai'), so POST /api/noc/mops/generate raised a
-- CheckViolation → HTTP 500 on PostgreSQL. The SQLite schema omitted the CHECK,
-- so it passed local tests. Enum source of truth: MOP_GENERATED_BY in
-- tools/noc_canvas/constants.py; baseline already carries the widened form in
-- tools/db/schema/pg_consolidated.sql.
--
-- PG-only: SQLite noc_mops has no CHECK on generated_by and cannot ALTER a
-- CHECK constraint in place.

-- @pg-only
ALTER TABLE noc_mops DROP CONSTRAINT IF EXISTS noc_mops_generated_by_check;
ALTER TABLE noc_mops ADD CONSTRAINT noc_mops_generated_by_check
    CHECK (generated_by = ANY (ARRAY['manual'::text, 'ai'::text, 'ai_template'::text]));
-- @all
