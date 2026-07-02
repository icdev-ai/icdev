-- Migration 238: RFI Style Guide system — Phase A
-- Company-wide style guide table + per-session overrides + per-section limits.

-- Company-wide style guide (singleton managed via args/govcon/company_style_guide.yaml)
CREATE TABLE IF NOT EXISTS rfi_company_style_guide (
  id SERIAL PRIMARY KEY,
  name TEXT NOT NULL DEFAULT 'default',
  is_active BOOLEAN NOT NULL DEFAULT TRUE,
  tone TEXT NOT NULL DEFAULT 'formal',
  forbidden_phrases TEXT NOT NULL DEFAULT '[]',
  required_headings TEXT NOT NULL DEFAULT '[]',
  compliance_notes TEXT NOT NULL DEFAULT '',
  sample_writing TEXT NOT NULL DEFAULT '',
  words_per_page INTEGER NOT NULL DEFAULT 250,
  created_at TIMESTAMPTZ DEFAULT NOW(),
  updated_at TIMESTAMPTZ DEFAULT NOW()
);

-- Per-session style guide overrides (JSON blob merged with company base at generation time)
ALTER TABLE rfi_workbench_sessions ADD COLUMN IF NOT EXISTS style_guide_overrides TEXT DEFAULT '{}';
-- Total page limit for the whole submission (NULL = unlimited)
ALTER TABLE rfi_workbench_sessions ADD COLUMN IF NOT EXISTS page_limit INTEGER;
-- Words per page for this session (overrides company default of 250)
ALTER TABLE rfi_workbench_sessions ADD COLUMN IF NOT EXISTS words_per_page INTEGER;

-- Per-section limits (NULL = use session/default)
ALTER TABLE rfi_workbench_sections ADD COLUMN IF NOT EXISTS word_limit INTEGER;
ALTER TABLE rfi_workbench_sections ADD COLUMN IF NOT EXISTS page_limit REAL;
