-- Migration 249: persist retrieved source keys per RFI workbench section
-- (trust-cite-03) so citations/provenance survive into the exported response
-- and the Sources panel, instead of being dropped after prompt assembly.

ALTER TABLE rfi_workbench_sections ADD COLUMN IF NOT EXISTS sources_json TEXT DEFAULT '[]';
