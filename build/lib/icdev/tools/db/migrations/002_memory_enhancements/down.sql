-- CUI // SP-CTI
-- Rollback: 002_memory_enhancements
-- Note: SQLite cannot DROP COLUMN. Columns remain but indexes are removed.

DROP TABLE IF EXISTS memory_buffer;
DROP INDEX IF EXISTS idx_memory_content_hash_user;
DROP INDEX IF EXISTS idx_memory_user_id;
DROP INDEX IF EXISTS idx_memory_tenant_id;
