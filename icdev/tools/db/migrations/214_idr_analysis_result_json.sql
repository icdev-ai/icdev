-- Migration 214: Persist full analyzer result on idr_analyses
-- Allows context_builder (Stage 4) to load config/IaC/diagram findings
-- without re-running the analysis or querying external canvas tables.

ALTER TABLE idr_analyses ADD COLUMN IF NOT EXISTS result_json TEXT;
