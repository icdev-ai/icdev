-- Migration: 20260808043009_restore_migration_209_tables_on_postgresql
-- CUI // SP-CTI
--
-- sbx-fld-04 — restore migration 209's tables on PostgreSQL.
--
-- WHAT IS BROKEN
--
-- On a freshly bootstrapped PostgreSQL, `sbom_components`,
-- `supply_chain_vulnerabilities` and `supply_chain_risk_scores` DO NOT EXIST,
-- even though `schema_migrations` claims 209 was applied.
--
-- bootstrap_pg.py loads tools/db/schema/pg_consolidated.sql and marks every
-- migration <= `through_version` applied WITHOUT running it, because the
-- historical chain is not replayable on PG. `through_version` is 301, so 209 is
-- marked applied — but the snapshot does not contain those three tables. It was
-- dumped from a canonical database where 209 had never actually run, so the
-- squash preserved the gap and then certified it as applied. That is exactly the
-- failure pg_consolidated.meta.json's own note warns about: "A value that is too
-- HIGH silently skips schema changes."
--
-- The damage is not confined to 209. Migration 20260808030213 (sbx-fnd-02) adds
-- the 2026 SBOM Minimum Element columns to `sbom_components` and creates
-- `sbom_dependencies` with foreign keys into it. It runs on fresh PG (it is
-- above the pivot) and every one of those statements fails with UndefinedTable,
-- is swallowed by the runner's skip-failed-statement guard, and is recorded as
-- applied. So on fresh PG the whole SBOM storage layer is absent while every
-- version marker says otherwise — a board that lies in the direction that costs
-- you the most.
--
-- Discovered by tests/pg_tier/test_sbom_component_license_pg.py, which is the
-- first runtime test to write `sbom_components` on the ambient backend.
--
-- WHY A NEW MIGRATION RATHER THAN A SNAPSHOT EDIT
--
-- The real repair is regenerating pg_consolidated.sql with pg_dump from a
-- canonical database that has these tables, which needs that database. Hand-
-- editing the dump would make it stop being a dump — the snapshot's whole value
-- is that it is mechanically reproducible. A new migration above the pivot is
-- the sanctioned path: it executes on fresh PG and no-ops everywhere else.
--
-- ORDERING
--
-- This migration sorts AFTER 20260808030213, so on a fresh database sbx-fnd-02's
-- ALTERs have already failed and been skipped by the time it runs. It therefore
-- creates `sbom_components` in its FULL post-fnd-02 shape rather than 209's
-- original one, and creates `sbom_dependencies` too. There is no case where that
-- shape is wrong:
--
--   * table absent  → this migration creates it complete;
--   * table present → 209 ran, so fnd-02's `ADD COLUMN IF NOT EXISTS` succeeded
--                     against it, and every statement here is IF NOT EXISTS.
--
-- Every statement is idempotent on both backends, so replaying it costs nothing.
--
-- NOT FIXED HERE
--
-- Whether other pre-301 migrations are missing from the snapshot the same way.
-- That is a schema-wide audit, not this card's scope; the three tables restored
-- here are the ones the SBOM path and tools/notification_service/handler_service.py
-- actually read and write.

-- ── SQLite ────────────────────────────────────────────────────────────────
-- @sqlite-only
-- Included for completeness only. A SQLite database replays 209 and
-- 20260808030213 normally, so every statement below is already satisfied there.

CREATE TABLE IF NOT EXISTS sbom_components (
    id                   TEXT    PRIMARY KEY,
    component_name       TEXT    NOT NULL,
    version              TEXT,
    vendor               TEXT,
    component_type       TEXT    CHECK(component_type IN (
                                     'library', 'framework', 'container', 'os',
                                     'firmware', 'device', 'application', 'service', 'other')),
    purl                 TEXT,
    license              TEXT,
    classification       TEXT    NOT NULL DEFAULT 'CUI',
    created_at           TEXT    DEFAULT (datetime('now')),
    updated_at           TEXT    DEFAULT (datetime('now')),
    producer             TEXT,
    hash_value           TEXT,
    hash_algorithm       TEXT,
    identifiers_json     TEXT    NOT NULL DEFAULT '{}',
    unknown_fields_json  TEXT    NOT NULL DEFAULT '{}',
    withheld_fields_json TEXT    NOT NULL DEFAULT '{}',
    tenant_id            TEXT
);
CREATE INDEX IF NOT EXISTS idx_sbom_comp_name     ON sbom_components(component_name);
CREATE INDEX IF NOT EXISTS idx_sbom_comp_vendor   ON sbom_components(vendor);
CREATE INDEX IF NOT EXISTS idx_sbom_comp_producer ON sbom_components(producer);
CREATE INDEX IF NOT EXISTS idx_sbom_comp_hash     ON sbom_components(hash_value);
CREATE INDEX IF NOT EXISTS idx_sbom_comp_tenant   ON sbom_components(tenant_id);

CREATE TABLE IF NOT EXISTS sbom_dependencies (
    id                  TEXT    PRIMARY KEY,
    sbom_record_id      INTEGER NOT NULL REFERENCES sbom_records(id),
    parent_component_id TEXT    NOT NULL REFERENCES sbom_components(id),
    child_component_id  TEXT    NOT NULL REFERENCES sbom_components(id),
    relationship_type   TEXT    NOT NULL DEFAULT 'depends_on',
    scope               TEXT,
    classification      TEXT    NOT NULL DEFAULT 'CUI',
    tenant_id           TEXT,
    created_at          TEXT    DEFAULT (datetime('now')),
    UNIQUE (sbom_record_id, parent_component_id, child_component_id, relationship_type)
);
CREATE INDEX IF NOT EXISTS idx_sbom_dep_record ON sbom_dependencies(sbom_record_id);
CREATE INDEX IF NOT EXISTS idx_sbom_dep_parent ON sbom_dependencies(parent_component_id);
CREATE INDEX IF NOT EXISTS idx_sbom_dep_child  ON sbom_dependencies(child_component_id);
CREATE INDEX IF NOT EXISTS idx_sbom_dep_tenant ON sbom_dependencies(tenant_id);

CREATE TABLE IF NOT EXISTS supply_chain_vulnerabilities (
    id                TEXT    PRIMARY KEY,
    sbom_id           TEXT    NOT NULL REFERENCES sbom_components(id),
    cve_id            TEXT    NOT NULL,
    cvss_score        REAL    NOT NULL DEFAULT 0.0,
    severity          TEXT    CHECK(severity IN (
                                  'critical', 'high', 'medium', 'low',
                                  'informational', 'none')),
    affected_versions TEXT,
    fixed_version     TEXT,
    classification    TEXT    NOT NULL DEFAULT 'CUI',
    created_at        TEXT    DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_scv_sbom_id     ON supply_chain_vulnerabilities(sbom_id);
CREATE INDEX IF NOT EXISTS idx_scv_cvss_score  ON supply_chain_vulnerabilities(cvss_score);

CREATE TABLE IF NOT EXISTS supply_chain_risk_scores (
    id              TEXT    PRIMARY KEY,
    sbom_id         TEXT    NOT NULL REFERENCES sbom_components(id),
    risk_level      TEXT    CHECK(risk_level IN (
                                'critical', 'high', 'medium', 'low', 'none')),
    exploitability  TEXT    CHECK(exploitability IN (
                                'functional', 'poc', 'unproven', 'not_defined')),
    patch_available INTEGER NOT NULL DEFAULT 0,
    last_assessed   TEXT    NOT NULL,
    classification  TEXT    NOT NULL DEFAULT 'CUI',
    created_at      TEXT    DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_scrs_sbom_id       ON supply_chain_risk_scores(sbom_id);
CREATE INDEX IF NOT EXISTS idx_scrs_last_assessed ON supply_chain_risk_scores(last_assessed);

-- ── PostgreSQL ────────────────────────────────────────────────────────────
-- @pg-only
-- This is the branch that does the work.

CREATE TABLE IF NOT EXISTS sbom_components (
    id                   TEXT    PRIMARY KEY,
    component_name       TEXT    NOT NULL,
    version              TEXT,
    vendor               TEXT,
    component_type       TEXT    CHECK(component_type IN (
                                     'library', 'framework', 'container', 'os',
                                     'firmware', 'device', 'application', 'service', 'other')),
    purl                 TEXT,
    license              TEXT,
    classification       TEXT    NOT NULL DEFAULT 'CUI',
    created_at           TEXT    DEFAULT (now()::text),
    updated_at           TEXT    DEFAULT (now()::text),
    producer             TEXT,
    hash_value           TEXT,
    hash_algorithm       TEXT,
    identifiers_json     TEXT    NOT NULL DEFAULT '{}',
    unknown_fields_json  TEXT    NOT NULL DEFAULT '{}',
    withheld_fields_json TEXT    NOT NULL DEFAULT '{}',
    tenant_id            TEXT
);
CREATE INDEX IF NOT EXISTS idx_sbom_comp_name     ON sbom_components(component_name);
CREATE INDEX IF NOT EXISTS idx_sbom_comp_vendor   ON sbom_components(vendor);
CREATE INDEX IF NOT EXISTS idx_sbom_comp_producer ON sbom_components(producer);
CREATE INDEX IF NOT EXISTS idx_sbom_comp_hash     ON sbom_components(hash_value);
CREATE INDEX IF NOT EXISTS idx_sbom_comp_tenant   ON sbom_components(tenant_id);

-- Columns the table may predate, for a PG that DID get 209 but not fnd-02's
-- ALTERs. Harmless where fnd-02 already ran.
ALTER TABLE sbom_components ADD COLUMN IF NOT EXISTS producer             TEXT;
ALTER TABLE sbom_components ADD COLUMN IF NOT EXISTS hash_value           TEXT;
ALTER TABLE sbom_components ADD COLUMN IF NOT EXISTS hash_algorithm       TEXT;
ALTER TABLE sbom_components ADD COLUMN IF NOT EXISTS identifiers_json     TEXT NOT NULL DEFAULT '{}';
ALTER TABLE sbom_components ADD COLUMN IF NOT EXISTS unknown_fields_json  TEXT NOT NULL DEFAULT '{}';
ALTER TABLE sbom_components ADD COLUMN IF NOT EXISTS withheld_fields_json TEXT NOT NULL DEFAULT '{}';
ALTER TABLE sbom_components ADD COLUMN IF NOT EXISTS tenant_id            TEXT;
ALTER TABLE sbom_components ADD COLUMN IF NOT EXISTS classification       TEXT NOT NULL DEFAULT 'CUI';

CREATE TABLE IF NOT EXISTS sbom_dependencies (
    id                  TEXT    PRIMARY KEY,
    sbom_record_id      INTEGER NOT NULL REFERENCES sbom_records(id),
    parent_component_id TEXT    NOT NULL REFERENCES sbom_components(id),
    child_component_id  TEXT    NOT NULL REFERENCES sbom_components(id),
    relationship_type   TEXT    NOT NULL DEFAULT 'depends_on',
    scope               TEXT,
    classification      TEXT    NOT NULL DEFAULT 'CUI',
    tenant_id           TEXT,
    created_at          TEXT    DEFAULT (now()::text),
    UNIQUE (sbom_record_id, parent_component_id, child_component_id, relationship_type)
);
CREATE INDEX IF NOT EXISTS idx_sbom_dep_record ON sbom_dependencies(sbom_record_id);
CREATE INDEX IF NOT EXISTS idx_sbom_dep_parent ON sbom_dependencies(parent_component_id);
CREATE INDEX IF NOT EXISTS idx_sbom_dep_child  ON sbom_dependencies(child_component_id);
CREATE INDEX IF NOT EXISTS idx_sbom_dep_tenant ON sbom_dependencies(tenant_id);

CREATE TABLE IF NOT EXISTS supply_chain_vulnerabilities (
    id                TEXT    PRIMARY KEY,
    sbom_id           TEXT    NOT NULL REFERENCES sbom_components(id),
    cve_id            TEXT    NOT NULL,
    cvss_score        REAL    NOT NULL DEFAULT 0.0,
    severity          TEXT    CHECK(severity IN (
                                  'critical', 'high', 'medium', 'low',
                                  'informational', 'none')),
    affected_versions TEXT,
    fixed_version     TEXT,
    classification    TEXT    NOT NULL DEFAULT 'CUI',
    created_at        TEXT    DEFAULT (now()::text)
);
CREATE INDEX IF NOT EXISTS idx_scv_sbom_id     ON supply_chain_vulnerabilities(sbom_id);
CREATE INDEX IF NOT EXISTS idx_scv_cvss_score  ON supply_chain_vulnerabilities(cvss_score);

CREATE TABLE IF NOT EXISTS supply_chain_risk_scores (
    id              TEXT    PRIMARY KEY,
    sbom_id         TEXT    NOT NULL REFERENCES sbom_components(id),
    risk_level      TEXT    CHECK(risk_level IN (
                                'critical', 'high', 'medium', 'low', 'none')),
    exploitability  TEXT    CHECK(exploitability IN (
                                'functional', 'poc', 'unproven', 'not_defined')),
    patch_available INTEGER NOT NULL DEFAULT 0,
    last_assessed   TEXT    NOT NULL,
    classification  TEXT    NOT NULL DEFAULT 'CUI',
    created_at      TEXT    DEFAULT (now()::text)
);
CREATE INDEX IF NOT EXISTS idx_scrs_sbom_id       ON supply_chain_risk_scores(sbom_id);
CREATE INDEX IF NOT EXISTS idx_scrs_last_assessed ON supply_chain_risk_scores(last_assessed);

-- @all
