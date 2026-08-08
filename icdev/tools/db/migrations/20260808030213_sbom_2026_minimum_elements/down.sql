-- Rollback: 20260808030213_sbom_2026_minimum_elements
-- CUI // SP-CTI
--
-- Drops the 2026 SBOM element columns and the dependency edge table.
--
-- TWO THINGS ARE DELIBERATELY NOT ROLLED BACK: sbom_records.classification and
-- the tenant_id columns. Both are RLS columns, and get_connection injects a
-- predicate on them for every caller inside a request context — dropping them
-- puts the tables back into the state where every query from the browser raises
-- UndefinedColumn. On PostgreSQL, sbom_records.classification also PREDATES
-- this migration: it is present in tools/db/schema/pg_consolidated.sql, so a
-- rollback that dropped it would delete a column this migration never created.
-- Leaving nullable, defaulted columns in place costs nothing.
--
-- Indexes are dropped before their columns because SQLite refuses
-- ALTER TABLE ... DROP COLUMN while an index references the column.

-- ── SQLite ────────────────────────────────────────────────────────────────
-- @sqlite-only
DROP TABLE IF EXISTS sbom_dependencies;

DROP INDEX IF EXISTS idx_sbom_rec_serial;
DROP INDEX IF EXISTS idx_sbom_rec_supersedes;
DROP INDEX IF EXISTS idx_sbom_comp_producer;
DROP INDEX IF EXISTS idx_sbom_comp_hash;

ALTER TABLE sbom_records DROP COLUMN sbom_author;
ALTER TABLE sbom_records DROP COLUMN author_signature;
ALTER TABLE sbom_records DROP COLUMN signature_algorithm;
ALTER TABLE sbom_records DROP COLUMN data_format_name;
ALTER TABLE sbom_records DROP COLUMN data_format_version;
ALTER TABLE sbom_records DROP COLUMN generation_context;
ALTER TABLE sbom_records DROP COLUMN tool_name;
ALTER TABLE sbom_records DROP COLUMN tool_version;
ALTER TABLE sbom_records DROP COLUMN sbom_version;
ALTER TABLE sbom_records DROP COLUMN serial_number;
ALTER TABLE sbom_records DROP COLUMN supersedes_sbom_id;

ALTER TABLE sbom_components DROP COLUMN producer;
ALTER TABLE sbom_components DROP COLUMN hash_value;
ALTER TABLE sbom_components DROP COLUMN hash_algorithm;
ALTER TABLE sbom_components DROP COLUMN identifiers_json;
ALTER TABLE sbom_components DROP COLUMN unknown_fields_json;
ALTER TABLE sbom_components DROP COLUMN withheld_fields_json;

-- ── PostgreSQL ────────────────────────────────────────────────────────────
-- @pg-only
DROP TABLE IF EXISTS sbom_dependencies;

DROP INDEX IF EXISTS idx_sbom_rec_serial;
DROP INDEX IF EXISTS idx_sbom_rec_supersedes;
DROP INDEX IF EXISTS idx_sbom_comp_producer;
DROP INDEX IF EXISTS idx_sbom_comp_hash;

ALTER TABLE sbom_records DROP COLUMN IF EXISTS sbom_author;
ALTER TABLE sbom_records DROP COLUMN IF EXISTS author_signature;
ALTER TABLE sbom_records DROP COLUMN IF EXISTS signature_algorithm;
ALTER TABLE sbom_records DROP COLUMN IF EXISTS data_format_name;
ALTER TABLE sbom_records DROP COLUMN IF EXISTS data_format_version;
ALTER TABLE sbom_records DROP COLUMN IF EXISTS generation_context;
ALTER TABLE sbom_records DROP COLUMN IF EXISTS tool_name;
ALTER TABLE sbom_records DROP COLUMN IF EXISTS tool_version;
ALTER TABLE sbom_records DROP COLUMN IF EXISTS sbom_version;
ALTER TABLE sbom_records DROP COLUMN IF EXISTS serial_number;
ALTER TABLE sbom_records DROP COLUMN IF EXISTS supersedes_sbom_id;

ALTER TABLE sbom_components DROP COLUMN IF EXISTS producer;
ALTER TABLE sbom_components DROP COLUMN IF EXISTS hash_value;
ALTER TABLE sbom_components DROP COLUMN IF EXISTS hash_algorithm;
ALTER TABLE sbom_components DROP COLUMN IF EXISTS identifiers_json;
ALTER TABLE sbom_components DROP COLUMN IF EXISTS unknown_fields_json;
ALTER TABLE sbom_components DROP COLUMN IF EXISTS withheld_fields_json;

-- @all
