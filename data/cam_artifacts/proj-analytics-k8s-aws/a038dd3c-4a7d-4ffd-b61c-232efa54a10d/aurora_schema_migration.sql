-- CUI // SP-CTI
-- Aurora PostgreSQL schema migration scaffold
-- Generated for: Oracle DB 19c (oracle → aurora-postgresql)
-- Review and adjust column types before executing against Aurora.

-- Enable required extensions
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";
CREATE EXTENSION IF NOT EXISTS pg_stat_statements;

-- Example table migration (replace with actual schema from AWs SCT output)
-- Oracle NUMBER(10) → INTEGER, NUMBER(19,4) → NUMERIC(19,4)
-- Oracle VARCHAR2(n) → VARCHAR(n), CLOB → TEXT, DATE → TIMESTAMP
-- Oracle BLOB → BYTEA, XMLTYPE → XML, ROWID → UUID DEFAULT uuid_generate_v4()

CREATE TABLE IF NOT EXISTS example_entity (
    id          UUID        PRIMARY KEY DEFAULT uuid_generate_v4(),
    legacy_id   INTEGER     NOT NULL,            -- maps Oracle ROWNUM / ROWID
    name        VARCHAR(255) NOT NULL,
    description TEXT,
    amount      NUMERIC(19,4),
    created_at  TIMESTAMP   NOT NULL DEFAULT NOW(),
    updated_at  TIMESTAMP   NOT NULL DEFAULT NOW(),
    is_active   BOOLEAN     NOT NULL DEFAULT TRUE
);

CREATE INDEX IF NOT EXISTS idx_example_entity_legacy_id ON example_entity(legacy_id);
CREATE INDEX IF NOT EXISTS idx_example_entity_created ON example_entity(created_at);

-- Row count validation (run before cutover to confirm DMS load)
-- SELECT COUNT(*) FROM source_schema.example_entity;   -- Oracle
-- SELECT COUNT(*) FROM example_entity;                 -- Aurora
