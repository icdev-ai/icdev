-- CUI // SP-CTI
-- Migration 209: Create sbom_components, supply_chain_vulnerabilities, and
--                supply_chain_risk_scores tables.
--
-- These tables back the supply chain risk notification handler
-- (tools/notification_service/handler_service.py::handle_supply_chain_risk_handler)
-- and are declared as expected compliance-base tables in
-- tools/installer/compliance_configurator.py::_FRAMEWORK_DB_TABLES.
-- The Internal Awareness Engine gap detector flagged all three as orphan_db_table
-- findings (referenced via SELECT in handler_service.py but absent from any
-- numbered migration).
--
-- sbom_components          — individual SBOM component records with provenance.
-- supply_chain_vulnerabilities — CVE findings per SBOM component (NIST SI-2).
-- supply_chain_risk_scores — aggregated risk/exploitability scores (NIST SA-12).

-- ── SQLite ────────────────────────────────────────────────────────────────
-- @sqlite-only
CREATE TABLE IF NOT EXISTS sbom_components (
    id              TEXT    PRIMARY KEY,
    component_name  TEXT    NOT NULL,
    version         TEXT,
    vendor          TEXT,
    component_type  TEXT    CHECK(component_type IN (
                                'library', 'framework', 'container', 'os',
                                'firmware', 'device', 'application', 'service', 'other')),
    purl            TEXT,
    license         TEXT,
    classification  TEXT    NOT NULL DEFAULT 'CUI',
    created_at      TEXT    DEFAULT (datetime('now')),
    updated_at      TEXT    DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_sbom_comp_name   ON sbom_components(component_name);
CREATE INDEX IF NOT EXISTS idx_sbom_comp_vendor ON sbom_components(vendor);

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
CREATE INDEX IF NOT EXISTS idx_scv_sbom_id    ON supply_chain_vulnerabilities(sbom_id);
CREATE INDEX IF NOT EXISTS idx_scv_cvss_score ON supply_chain_vulnerabilities(cvss_score);

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
CREATE INDEX IF NOT EXISTS idx_scrs_sbom_id      ON supply_chain_risk_scores(sbom_id);
CREATE INDEX IF NOT EXISTS idx_scrs_last_assessed ON supply_chain_risk_scores(last_assessed);

-- ── PostgreSQL ────────────────────────────────────────────────────────────
-- @pg-only
CREATE TABLE IF NOT EXISTS sbom_components (
    id              TEXT    PRIMARY KEY,
    component_name  TEXT    NOT NULL,
    version         TEXT,
    vendor          TEXT,
    component_type  TEXT    CHECK(component_type IN (
                                'library', 'framework', 'container', 'os',
                                'firmware', 'device', 'application', 'service', 'other')),
    purl            TEXT,
    license         TEXT,
    classification  TEXT    NOT NULL DEFAULT 'CUI',
    created_at      TEXT    DEFAULT (now()::text),
    updated_at      TEXT    DEFAULT (now()::text)
);
CREATE INDEX IF NOT EXISTS idx_sbom_comp_name   ON sbom_components(component_name);
CREATE INDEX IF NOT EXISTS idx_sbom_comp_vendor ON sbom_components(vendor);

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
CREATE INDEX IF NOT EXISTS idx_scv_sbom_id    ON supply_chain_vulnerabilities(sbom_id);
CREATE INDEX IF NOT EXISTS idx_scv_cvss_score ON supply_chain_vulnerabilities(cvss_score);

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
CREATE INDEX IF NOT EXISTS idx_scrs_sbom_id      ON supply_chain_risk_scores(sbom_id);
CREATE INDEX IF NOT EXISTS idx_scrs_last_assessed ON supply_chain_risk_scores(last_assessed);

-- @all
