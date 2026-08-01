-- Security Design Canvas (SDC) core schema — canvas-local reference copy.
-- CUI // SP-CTI
--
-- This is a byte-for-byte reference mirror of the authoritative main-chain
-- migration tools/db/migrations/272_security_canvas_core.sql. It exists to make
-- the component_registry `sdc.completeness.db_migration:
-- tools/security_canvas/db/migrations` path real. The MAIN chain (272) is what
-- MigrationRunner actually applies; this copy is documentation/reference only
-- and is NOT discovered by the global runner.
--
-- Provisions every core table for the /security canvas so a production
-- PostgreSQL instance bootstrapped from the consolidated schema (or migrated
-- via the main chain) can serve /security WITHOUT first running the runtime
-- initializer at tools/security_canvas/db/init_db.py.
--
-- Parity source of truth: tools/security_canvas/db/init_db.py::SCHEMA.
-- Every statement is idempotent (CREATE TABLE / INDEX IF NOT EXISTS) so this
-- migration coexists with the runtime init_db.py on existing installs — running
-- either first is safe. DDL is authored in the portable dialect used across the
-- main chain; on PostgreSQL the migration runner translates it (e.g.
-- INTEGER PRIMARY KEY AUTOINCREMENT -> SERIAL PRIMARY KEY).
--
-- NO data seeding here: init_db.py::_seed_zig and the TEMPLATES/SNIPPETS seeds
-- remain authoritative for ZIG pillars/capabilities/activities and template rows.

-- ── Security design + composition tables ─────────────────────────────────────

CREATE TABLE IF NOT EXISTS security_designs (
    id              TEXT PRIMARY KEY,
    name            TEXT NOT NULL,
    description     TEXT,
    graph_json      TEXT NOT NULL DEFAULT '{"nodes":[],"edges":[],"boundaries":[]}',
    template_id     TEXT,
    source_topology_id TEXT,
    classification  TEXT DEFAULT 'CUI',
    created_at      TEXT DEFAULT CURRENT_TIMESTAMP,
    updated_at      TEXT DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS sc_assets (
    id              TEXT PRIMARY KEY,
    design_id       TEXT REFERENCES security_designs(id),
    asset_type      TEXT NOT NULL,
    label           TEXT,
    description     TEXT,
    classification  TEXT DEFAULT 'CUI',
    config_json     TEXT DEFAULT '{}',
    pos_x           REAL DEFAULT 0,
    pos_y           REAL DEFAULT 0,
    source_node_id  TEXT,
    created_at      TEXT DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS sc_threats (
    id              TEXT PRIMARY KEY,
    design_id       TEXT REFERENCES security_designs(id),
    threat_category TEXT NOT NULL,
    mitre_technique TEXT,
    mitre_tactic    TEXT,
    title           TEXT NOT NULL,
    description     TEXT,
    likelihood      TEXT DEFAULT 'medium',
    impact          TEXT DEFAULT 'medium',
    risk_score      REAL DEFAULT 0,
    affected_assets TEXT DEFAULT '[]',
    status          TEXT DEFAULT 'open',
    is_stale        INTEGER DEFAULT 0,
    created_at      TEXT DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS sc_controls (
    id                      TEXT PRIMARY KEY,
    design_id               TEXT REFERENCES security_designs(id),
    control_family          TEXT NOT NULL,
    control_id              TEXT NOT NULL,
    title                   TEXT NOT NULL,
    description             TEXT,
    implementation_status   TEXT DEFAULT 'planned',
    implementation_notes    TEXT,
    mitigates_threats       TEXT DEFAULT '[]',
    protects_assets         TEXT DEFAULT '[]',
    evidence_json           TEXT DEFAULT '{}',
    created_at              TEXT DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS sc_trust_boundaries (
    id              TEXT PRIMARY KEY,
    design_id       TEXT REFERENCES security_designs(id),
    label           TEXT NOT NULL,
    boundary_type   TEXT DEFAULT 'network',
    classification  TEXT DEFAULT 'CUI',
    il_level        TEXT DEFAULT 'IL4',
    color           TEXT DEFAULT '#e94560',
    fill_opacity    REAL DEFAULT 0.08,
    contained_assets TEXT DEFAULT '[]',
    pos_x           REAL DEFAULT 0,
    pos_y           REAL DEFAULT 0,
    width           REAL DEFAULT 400,
    height          REAL DEFAULT 300,
    created_at      TEXT DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS sc_data_flows (
    id                  TEXT PRIMARY KEY,
    design_id           TEXT REFERENCES security_designs(id),
    source_asset_id     TEXT REFERENCES sc_assets(id),
    target_asset_id     TEXT REFERENCES sc_assets(id),
    label               TEXT,
    protocol            TEXT,
    data_classification TEXT DEFAULT 'CUI',
    encrypted           INTEGER DEFAULT 0,
    authenticated       INTEGER DEFAULT 0,
    crosses_boundary    INTEGER DEFAULT 0,
    ports               TEXT DEFAULT '[]',
    created_at          TEXT DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS sc_assessments (
    id              TEXT PRIMARY KEY,
    design_id       TEXT REFERENCES security_designs(id),
    assessment_type TEXT NOT NULL,
    trigger_source  TEXT,
    source_entity_id TEXT,
    total_threats   INTEGER DEFAULT 0,
    total_controls  INTEGER DEFAULT 0,
    risk_score      REAL DEFAULT 0,
    posture_grade   TEXT DEFAULT 'F',
    findings_json   TEXT DEFAULT '[]',
    recommendations_json TEXT DEFAULT '[]',
    ran_at          TEXT DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS sc_remediation_plans (
    id                  TEXT PRIMARY KEY,
    design_id           TEXT REFERENCES security_designs(id),
    assessment_id       TEXT REFERENCES sc_assessments(id),
    title               TEXT NOT NULL,
    priority            TEXT DEFAULT 'medium',
    status              TEXT DEFAULT 'open',
    remediation_steps   TEXT DEFAULT '[]',
    estimated_effort    TEXT,
    assigned_to         TEXT,
    target_date         TEXT,
    completed_at        TEXT,
    created_at          TEXT DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS sc_templates (
    id              TEXT PRIMARY KEY,
    name            TEXT NOT NULL,
    category        TEXT,
    description     TEXT,
    graph_json      TEXT NOT NULL DEFAULT '{"nodes":[],"edges":[],"boundaries":[]}',
    thumbnail_svg   TEXT,
    tags            TEXT DEFAULT '[]'
);

CREATE TABLE IF NOT EXISTS sc_snippets (
    id              TEXT PRIMARY KEY,
    name            TEXT NOT NULL,
    category        TEXT,
    description     TEXT,
    graph_json      TEXT NOT NULL DEFAULT '{"nodes":[],"edges":[],"boundaries":[]}',
    tags            TEXT DEFAULT '[]'
);

CREATE TABLE IF NOT EXISTS sc_versions (
    id              TEXT PRIMARY KEY,
    design_id       TEXT NOT NULL,
    version_number  INTEGER NOT NULL,
    graph_json      TEXT NOT NULL,
    change_summary  TEXT DEFAULT '',
    user_id         TEXT DEFAULT '',
    created_at      TEXT NOT NULL,
    FOREIGN KEY (design_id) REFERENCES security_designs(id)
);
CREATE INDEX IF NOT EXISTS idx_sc_versions_design ON sc_versions(design_id, version_number);

CREATE TABLE IF NOT EXISTS sc_audit (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    action          TEXT NOT NULL,
    entity_type     TEXT,
    entity_id       TEXT,
    details         TEXT,
    user_id         TEXT,
    classification  TEXT DEFAULT 'CUI // SP-CTI',
    ts              TEXT DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS sdc_attack_snapshots (
    id              TEXT PRIMARY KEY,
    component_id    TEXT NOT NULL,
    nodes_json      TEXT NOT NULL DEFAULT '[]',
    edges_json      TEXT NOT NULL DEFAULT '[]',
    caldera_op_id   TEXT,
    created_at      TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_sdc_attack_component ON sdc_attack_snapshots(component_id);
CREATE INDEX IF NOT EXISTS idx_sdc_attack_created ON sdc_attack_snapshots(created_at);

CREATE TABLE IF NOT EXISTS sdc_sops (
    id                  TEXT PRIMARY KEY,
    title               TEXT NOT NULL,
    sop_type            TEXT NOT NULL,
    description         TEXT,
    purpose             TEXT,
    scope               TEXT,
    steps               TEXT DEFAULT '[]',
    nist_controls       TEXT DEFAULT '[]',
    owner               TEXT,
    reviewer            TEXT,
    approval_status     TEXT DEFAULT 'draft' CHECK(approval_status IN ('draft','pending_review','approved','rejected')),
    approved_by         TEXT,
    approved_at         TEXT,
    rejected_reason     TEXT,
    version             TEXT DEFAULT '1.0',
    next_review_date    TEXT,
    classification      TEXT DEFAULT 'CUI',
    created_at          TEXT DEFAULT CURRENT_TIMESTAMP,
    updated_at          TEXT DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_sdc_sops_type ON sdc_sops(sop_type);
CREATE INDEX IF NOT EXISTS idx_sdc_sops_status ON sdc_sops(approval_status);

-- ── NSA ZIG (Zero Trust Implementation Guide) tables ─────────────────────────

CREATE TABLE IF NOT EXISTS zig_pillars (
    slug            TEXT PRIMARY KEY,
    name            TEXT NOT NULL,
    full_name       TEXT,
    pillar_weight   REAL DEFAULT 0.14,
    icon            TEXT,
    color           TEXT,
    csi_url         TEXT,
    description     TEXT,
    ficam_components TEXT DEFAULT '[]',
    created_at      TEXT DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS zig_capabilities (
    id              TEXT PRIMARY KEY,
    pillar_slug     TEXT NOT NULL REFERENCES zig_pillars(slug),
    title           TEXT NOT NULL,
    phase           TEXT NOT NULL CHECK(phase IN ('discovery','phase1','phase2')),
    maturity_level  TEXT NOT NULL CHECK(maturity_level IN ('basic','intermediate','advanced')),
    description     TEXT,
    nist_controls   TEXT DEFAULT '[]',
    target_fy2027   INTEGER DEFAULT 1,
    implementation_status TEXT DEFAULT 'not_started'
        CHECK(implementation_status IN ('not_started','planned','in_progress','implemented')),
    evidence_note   TEXT,
    updated_at      TEXT DEFAULT CURRENT_TIMESTAMP,
    created_at      TEXT DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_zig_cap_pillar ON zig_capabilities(pillar_slug);
CREATE INDEX IF NOT EXISTS idx_zig_cap_phase ON zig_capabilities(phase);

CREATE TABLE IF NOT EXISTS zig_activities (
    id              TEXT PRIMARY KEY,
    capability_id   TEXT NOT NULL REFERENCES zig_capabilities(id),
    phase           TEXT NOT NULL CHECK(phase IN ('discovery','phase1','phase2')),
    title           TEXT NOT NULL,
    description     TEXT,
    nist_control_ref TEXT,
    created_at      TEXT DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_zig_act_cap ON zig_activities(capability_id);
CREATE INDEX IF NOT EXISTS idx_zig_act_phase ON zig_activities(phase);

CREATE TABLE IF NOT EXISTS zig_activity_completions (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    activity_id     TEXT NOT NULL REFERENCES zig_activities(id),
    target_id       TEXT NOT NULL DEFAULT 'icdev-self',
    status          TEXT NOT NULL DEFAULT 'not_started'
        CHECK(status IN ('not_started','in_progress','complete')),
    evidence_note   TEXT,
    completed_by    TEXT,
    completed_at    TEXT,
    updated_at      TEXT DEFAULT CURRENT_TIMESTAMP
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_zig_comp_act ON zig_activity_completions(activity_id, target_id);

CREATE TABLE IF NOT EXISTS zig_maturity_scores (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    pillar_slug     TEXT NOT NULL,
    score           REAL NOT NULL DEFAULT 0.0,
    maturity_level  TEXT,
    capability_count INTEGER DEFAULT 0,
    activity_count  INTEGER DEFAULT 0,
    complete_activities INTEGER DEFAULT 0,
    assessment_run_at TEXT DEFAULT CURRENT_TIMESTAMP,
    created_at      TEXT DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_zig_score_pillar ON zig_maturity_scores(pillar_slug);

-- zig_targets: external ZIG assessment targets (systems assessed against ZIG).
-- Written by tools/security_canvas/blueprint.py + zig_portfolio.py and read by
-- the ZIG portfolio and the zig.targets IQE collection, but NOT created by
-- init_db.py (its DDL previously lived only in tests/test_zig_external_targets.py).
-- Included here so /security's ZIG portfolio functions on a bootstrapped PG DB.
CREATE TABLE IF NOT EXISTS zig_targets (
    id              TEXT PRIMARY KEY,
    name            TEXT NOT NULL,
    description     TEXT,
    system_type     TEXT DEFAULT 'general',
    classification  TEXT DEFAULT 'CUI',
    status          TEXT DEFAULT 'active',
    pillar_focus    TEXT DEFAULT '',
    created_at      TEXT DEFAULT CURRENT_TIMESTAMP,
    updated_at      TEXT DEFAULT CURRENT_TIMESTAMP
);

-- ── FedRAMP ATO package tables ───────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS fedramp_ato_packages (
    id                  TEXT PRIMARY KEY,
    system_name         TEXT NOT NULL,
    ato_status          TEXT NOT NULL DEFAULT 'in_progress'
                        CHECK (ato_status IN ('in_progress', 'authorized', 'conditional', 'denied', 'expired')),
    authorization_date  TEXT,
    expiry_date         TEXT,
    package_type        TEXT DEFAULT 'moderate'
                        CHECK (package_type IN ('low', 'moderate', 'high')),
    authorizing_official TEXT,
    notes               TEXT DEFAULT '',
    classification      TEXT DEFAULT 'CUI // SP-CTI',
    created_at          TEXT DEFAULT CURRENT_TIMESTAMP,
    updated_at          TEXT DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS fedramp_controls (
    id                     TEXT PRIMARY KEY,
    package_id             TEXT NOT NULL REFERENCES fedramp_ato_packages(id) ON DELETE CASCADE,
    control_id             TEXT NOT NULL,
    control_name           TEXT DEFAULT '',
    implementation_status  TEXT NOT NULL DEFAULT 'not_implemented'
                           CHECK (implementation_status IN (
                               'implemented', 'partially_implemented',
                               'planned', 'not_implemented', 'not_applicable'
                           )),
    implementation_origin  TEXT DEFAULT 'service_provider'
                           CHECK (implementation_origin IN (
                               'service_provider', 'customer', 'hybrid', 'inherited'
                           )),
    responsible_role       TEXT DEFAULT '',
    implementation_detail  TEXT DEFAULT '',
    assessment_date        TEXT,
    assessed_by            TEXT DEFAULT '',
    classification         TEXT DEFAULT 'CUI // SP-CTI',
    created_at             TEXT DEFAULT CURRENT_TIMESTAMP,
    updated_at             TEXT DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_fedramp_controls_package ON fedramp_controls(package_id);
CREATE INDEX IF NOT EXISTS idx_fedramp_controls_status  ON fedramp_controls(implementation_status);
