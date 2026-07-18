-- Migration 273: Data Design Canvas (DDC) core schema.
-- CUI // SP-CTI
--
-- Provisions the 38 Data Design Canvas tables that were absent from the
-- consolidated PostgreSQL bootstrap schema so a production PostgreSQL instance
-- bootstrapped from the consolidated snapshot (or migrated via the main chain)
-- can serve /data WITHOUT first running the runtime initializer at
-- tools/data_canvas/db/init_db.py (whose call in blueprint.py is wrapped in a
-- swallowed try/except — a P0 deploy-correctness gap; task dcpr-db-01).
--
-- Parity source of truth: tools/data_canvas/db/init_db.py::SCHEMA (plus the
-- CAM extension table dd_migration_jobs and the idempotent ALTER-added columns
-- that init_db applies at runtime: dm_domains.owner_team/owner_email,
-- dm_data_products.output_port_type/sla_tier/owner_team, dd_quality_runs.reflex_run).
--
-- The 6 DDC tables already present in the consolidated baseline are NOT
-- recreated here (they already have CREATE TABLE statements in the main chain /
-- migration 031): data_nodes, data_edges, data_twin_snapshots,
-- dd_freshness_alerts, dm_ports, dd_pii_scans.
--
-- Every statement is idempotent (CREATE TABLE / INDEX IF NOT EXISTS) so this
-- migration coexists with the runtime init_db.py on existing installs — running
-- either first is safe. DDL is authored in the portable dialect used across the
-- main chain; on PostgreSQL the migration runner translates it (e.g.
-- INTEGER PRIMARY KEY AUTOINCREMENT -> SERIAL PRIMARY KEY, CURRENT_TIMESTAMP
-- default -> NOW()). Tables are ordered so every REFERENCES target precedes its
-- referrers.
--
-- NO data seeding here (init_db.py TEMPLATES/SNIPPETS/RUNBOOKS/SOPS remain
-- authoritative). NO append-only triggers here (dd_audit/dm_audit/
-- dd_mapping_transforms immutability is provisioned separately — task dcpr-db-02).

-- ── Design core ──────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS data_designs (
    id              TEXT PRIMARY KEY,
    name            TEXT NOT NULL,
    description     TEXT,
    graph_json      TEXT NOT NULL DEFAULT '{"nodes":[],"edges":[],"boundaries":[]}',
    template_id     TEXT,
    classification  TEXT DEFAULT 'CUI',
    created_at      TEXT DEFAULT CURRENT_TIMESTAMP,
    updated_at      TEXT DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS dd_templates (
    id            TEXT PRIMARY KEY,
    name          TEXT NOT NULL,
    category      TEXT,
    description   TEXT,
    graph_json    TEXT NOT NULL DEFAULT '{"nodes":[],"edges":[],"boundaries":[]}',
    tags          TEXT DEFAULT '[]'
);

CREATE TABLE IF NOT EXISTS dd_snippets (
    id          TEXT PRIMARY KEY,
    name        TEXT NOT NULL,
    category    TEXT,
    description TEXT,
    graph_json  TEXT NOT NULL DEFAULT '{"nodes":[],"edges":[],"boundaries":[]}',
    tags        TEXT DEFAULT '[]'
);

CREATE TABLE IF NOT EXISTS dd_assessments (
    id              TEXT PRIMARY KEY,
    design_id       TEXT REFERENCES data_designs(id),
    assessment_type TEXT NOT NULL,
    findings_json   TEXT DEFAULT '[]',
    score           REAL DEFAULT 0,
    created_at      TEXT DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS dd_audit (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    design_id       TEXT,
    "user"          TEXT,
    action          TEXT NOT NULL,
    detail          TEXT,
    classification  TEXT DEFAULT 'CUI // SP-CTI',
    created_at      TEXT DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS dd_versions (
    id              TEXT PRIMARY KEY,
    design_id       TEXT REFERENCES data_designs(id),
    version_number  INTEGER NOT NULL,
    graph_json      TEXT NOT NULL DEFAULT '{"nodes":[],"edges":[],"boundaries":[]}',
    change_summary  TEXT DEFAULT '',
    user_id         TEXT DEFAULT '',
    created_at      TEXT DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_dd_versions_design ON dd_versions(design_id);

CREATE TABLE IF NOT EXISTS dd_collab_sessions (
    id          TEXT PRIMARY KEY,
    design_id   TEXT NOT NULL REFERENCES data_designs(id) ON DELETE CASCADE,
    user_id     TEXT NOT NULL,
    user_name   TEXT NOT NULL DEFAULT '',
    color       TEXT NOT NULL DEFAULT '#3498db',
    joined_at   TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    last_seen   TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    is_active   INTEGER NOT NULL DEFAULT 1
);
CREATE INDEX IF NOT EXISTS idx_dd_collab_design ON dd_collab_sessions(design_id);

CREATE TABLE IF NOT EXISTS dd_lineage (
    id              TEXT PRIMARY KEY,
    design_id       TEXT NOT NULL REFERENCES data_designs(id) ON DELETE CASCADE,
    source_node_id  TEXT NOT NULL,
    target_node_id  TEXT NOT NULL,
    lineage_type    TEXT DEFAULT 'flow',
    column_name     TEXT DEFAULT '',
    transform_desc  TEXT DEFAULT '',
    classification  TEXT DEFAULT 'CUI',
    created_at      TEXT DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_dd_lineage_design ON dd_lineage(design_id);
CREATE INDEX IF NOT EXISTS idx_dd_lineage_source ON dd_lineage(source_node_id);
CREATE INDEX IF NOT EXISTS idx_dd_lineage_target ON dd_lineage(target_node_id);

-- ── Ops: runbooks / SOPs ─────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS ddc_runbooks (
    id                  TEXT PRIMARY KEY,
    title               TEXT NOT NULL,
    category            TEXT DEFAULT 'general',
    severity            TEXT DEFAULT 'medium',
    description         TEXT DEFAULT '',
    trigger_condition   TEXT DEFAULT '',
    steps_json          TEXT DEFAULT '[]',
    classification      TEXT DEFAULT 'CUI // SP-CTI',
    status              TEXT DEFAULT 'active',
    linked_design_id    TEXT,
    created_at          TEXT DEFAULT CURRENT_TIMESTAMP,
    updated_at          TEXT DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_ddc_runbooks_category ON ddc_runbooks(category);
CREATE INDEX IF NOT EXISTS idx_ddc_runbooks_severity ON ddc_runbooks(severity);

CREATE TABLE IF NOT EXISTS ddc_runbook_executions (
    id              TEXT PRIMARY KEY,
    runbook_id      TEXT REFERENCES ddc_runbooks(id) ON DELETE CASCADE,
    triggered_by    TEXT DEFAULT '',
    status          TEXT DEFAULT 'in_progress',
    notes           TEXT DEFAULT '',
    started_at      TEXT DEFAULT CURRENT_TIMESTAMP,
    completed_at    TEXT DEFAULT NULL
);
CREATE INDEX IF NOT EXISTS idx_ddc_runbook_exec_runbook ON ddc_runbook_executions(runbook_id);

CREATE TABLE IF NOT EXISTS ddc_sops (
    id                  TEXT PRIMARY KEY,
    title               TEXT NOT NULL,
    category            TEXT DEFAULT 'general',
    description         TEXT DEFAULT '',
    purpose             TEXT DEFAULT '',
    scope               TEXT DEFAULT '',
    steps_json          TEXT DEFAULT '[]',
    references_json     TEXT DEFAULT '[]',
    version             TEXT DEFAULT '1.0',
    status              TEXT DEFAULT 'draft',
    classification      TEXT DEFAULT 'CUI // SP-CTI',
    linked_design_id    TEXT,
    owner               TEXT DEFAULT '',
    reviewer            TEXT DEFAULT '',
    approver            TEXT DEFAULT '',
    created_at          TEXT DEFAULT CURRENT_TIMESTAMP,
    updated_at          TEXT DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_ddc_sops_category ON ddc_sops(category);
CREATE INDEX IF NOT EXISTS idx_ddc_sops_status   ON ddc_sops(status);

CREATE TABLE IF NOT EXISTS ddc_sop_approvals (
    id          TEXT PRIMARY KEY,
    sop_id      TEXT REFERENCES ddc_sops(id) ON DELETE CASCADE,
    reviewer    TEXT NOT NULL,
    action      TEXT NOT NULL,
    comment     TEXT DEFAULT '',
    created_at  TEXT DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_ddc_sop_approvals_sop ON ddc_sop_approvals(sop_id);

-- ── Data Science: Explore / Query / Quality ──────────────────────────────────
CREATE TABLE IF NOT EXISTS dd_explore_sessions (
    id              TEXT PRIMARY KEY,
    design_id       TEXT,
    "user"          TEXT DEFAULT '',
    db_conn_json    TEXT DEFAULT '{}',
    status          TEXT DEFAULT 'completed',
    classification  TEXT DEFAULT 'CUI // SP-CTI',
    created_at      TEXT DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_dd_explore_sessions_design ON dd_explore_sessions(design_id);

CREATE TABLE IF NOT EXISTS dd_explore_profiles (
    id              TEXT PRIMARY KEY,
    design_id       TEXT,
    session_id      TEXT REFERENCES dd_explore_sessions(id) ON DELETE SET NULL,
    db_conn_json    TEXT DEFAULT '{}',
    profile_json    TEXT DEFAULT '{}',
    table_count     INTEGER DEFAULT 0,
    anomaly_json    TEXT,
    classification  TEXT DEFAULT 'CUI // SP-CTI',
    created_at      TEXT DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_dd_explore_profiles_design ON dd_explore_profiles(design_id);

CREATE TABLE IF NOT EXISTS dd_anomaly_runs (
    id              TEXT PRIMARY KEY,
    profile_id      TEXT,
    findings_json   TEXT,
    overall_risk    TEXT,
    classification  TEXT,
    created_at      TEXT
);

CREATE TABLE IF NOT EXISTS dd_query_history (
    id              TEXT PRIMARY KEY,
    design_id       TEXT,
    "user"          TEXT DEFAULT '',
    sql_text        TEXT NOT NULL,
    db_conn_json    TEXT DEFAULT '{}',
    row_count       INTEGER DEFAULT 0,
    exec_ms         INTEGER DEFAULT 0,
    classification  TEXT DEFAULT 'CUI // SP-CTI',
    created_at      TEXT DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_dd_query_history_design ON dd_query_history(design_id);

CREATE TABLE IF NOT EXISTS dd_quality_rules (
    id              TEXT PRIMARY KEY,
    design_id       TEXT,
    name            TEXT NOT NULL,
    table_name      TEXT NOT NULL,
    column_name     TEXT DEFAULT '',
    check_type      TEXT NOT NULL,
    threshold       REAL DEFAULT 90.0,
    params_json     TEXT DEFAULT '{}',
    classification  TEXT DEFAULT 'CUI // SP-CTI',
    enabled         INTEGER DEFAULT 1,
    created_at      TEXT DEFAULT CURRENT_TIMESTAMP,
    updated_at      TEXT DEFAULT CURRENT_TIMESTAMP,
    CHECK (check_type IN ('completeness', 'uniqueness', 'range', 'pattern', 'freshness'))
);
CREATE INDEX IF NOT EXISTS idx_dd_quality_rules_design ON dd_quality_rules(design_id);

CREATE TABLE IF NOT EXISTS dd_quality_runs (
    id              TEXT PRIMARY KEY,
    rule_id         TEXT REFERENCES dd_quality_rules(id) ON DELETE CASCADE,
    db_conn_json    TEXT DEFAULT '{}',
    passed          INTEGER DEFAULT 0,
    actual_value    REAL DEFAULT 0.0,
    threshold       REAL DEFAULT 0.0,
    detail          TEXT DEFAULT '',
    classification  TEXT DEFAULT 'CUI // SP-CTI',
    reflex_run      TEXT,
    created_at      TEXT DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_dd_quality_runs_rule ON dd_quality_runs(rule_id);

-- ── Data Mesh Foundation ─────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS dm_domains (
    id              TEXT PRIMARY KEY,
    name            TEXT NOT NULL,
    description     TEXT DEFAULT '',
    owner           TEXT DEFAULT '',
    steward         TEXT DEFAULT '',
    bounded_context TEXT DEFAULT '',
    maturity_level  INTEGER DEFAULT 0,
    classification  TEXT DEFAULT 'CUI // SP-CTI',
    status          TEXT DEFAULT 'active',
    owner_team      TEXT DEFAULT '',
    owner_email     TEXT DEFAULT '',
    created_at      TEXT DEFAULT CURRENT_TIMESTAMP,
    updated_at      TEXT DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_dm_domains_status ON dm_domains(status);

CREATE TABLE IF NOT EXISTS dm_data_products (
    id              TEXT PRIMARY KEY,
    domain_id       TEXT REFERENCES dm_domains(id) ON DELETE CASCADE,
    name            TEXT NOT NULL,
    description     TEXT DEFAULT '',
    owner           TEXT DEFAULT '',
    version         TEXT DEFAULT '1.0.0',
    availability_sla REAL DEFAULT 99.9,
    latency_sla_ms  INTEGER DEFAULT 500,
    status          TEXT DEFAULT 'active',
    classification  TEXT DEFAULT 'CUI // SP-CTI',
    output_port_type TEXT DEFAULT 'table',
    sla_tier        TEXT DEFAULT 'standard',
    owner_team      TEXT DEFAULT '',
    created_at      TEXT DEFAULT CURRENT_TIMESTAMP,
    updated_at      TEXT DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_dm_data_products_domain ON dm_data_products(domain_id);
CREATE INDEX IF NOT EXISTS idx_dm_data_products_status ON dm_data_products(status);

CREATE TABLE IF NOT EXISTS dm_contracts (
    id              TEXT PRIMARY KEY,
    product_id      TEXT REFERENCES dm_data_products(id) ON DELETE CASCADE,
    title           TEXT NOT NULL,
    version         TEXT DEFAULT '1.0.0',
    schema_json     TEXT DEFAULT '{}',
    sla_json        TEXT DEFAULT '{}',
    quality_rules_json TEXT DEFAULT '[]',
    status          TEXT DEFAULT 'draft',
    classification  TEXT DEFAULT 'CUI // SP-CTI',
    created_at      TEXT DEFAULT CURRENT_TIMESTAMP,
    updated_at      TEXT DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_dm_contracts_product ON dm_contracts(product_id);
CREATE INDEX IF NOT EXISTS idx_dm_contracts_status  ON dm_contracts(status);

CREATE TABLE IF NOT EXISTS dm_input_ports (
    id              TEXT PRIMARY KEY,
    product_id      TEXT REFERENCES dm_data_products(id) ON DELETE CASCADE,
    name            TEXT NOT NULL,
    port_type       TEXT DEFAULT 'cdc',
    schema_json     TEXT DEFAULT '{}',
    source_system   TEXT DEFAULT '',
    classification  TEXT DEFAULT 'CUI // SP-CTI',
    created_at      TEXT DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_dm_input_ports_product ON dm_input_ports(product_id);

CREATE TABLE IF NOT EXISTS dm_output_ports (
    id              TEXT PRIMARY KEY,
    product_id      TEXT REFERENCES dm_data_products(id) ON DELETE CASCADE,
    name            TEXT NOT NULL,
    port_type       TEXT DEFAULT 'api',
    schema_json     TEXT DEFAULT '{}',
    endpoint        TEXT DEFAULT '',
    sla_json        TEXT DEFAULT '{}',
    classification  TEXT DEFAULT 'CUI // SP-CTI',
    created_at      TEXT DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_dm_output_ports_product ON dm_output_ports(product_id);

CREATE TABLE IF NOT EXISTS dm_domain_maturity (
    id              TEXT PRIMARY KEY,
    domain_id       TEXT REFERENCES dm_domains(id) ON DELETE CASCADE,
    maturity_level  INTEGER NOT NULL DEFAULT 0,
    scores_json     TEXT DEFAULT '{}',
    assessed_by     TEXT DEFAULT '',
    notes           TEXT DEFAULT '',
    classification  TEXT DEFAULT 'CUI // SP-CTI',
    created_at      TEXT DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_dm_domain_maturity_domain ON dm_domain_maturity(domain_id);

CREATE TABLE IF NOT EXISTS dm_governance_policies (
    id              TEXT PRIMARY KEY,
    name            TEXT NOT NULL,
    policy_type     TEXT DEFAULT 'opa',
    rules_json      TEXT DEFAULT '[]',
    applies_to      TEXT DEFAULT 'all',
    status          TEXT DEFAULT 'active',
    classification  TEXT DEFAULT 'CUI // SP-CTI',
    created_at      TEXT DEFAULT CURRENT_TIMESTAMP,
    updated_at      TEXT DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_dm_governance_policies_status ON dm_governance_policies(status);

CREATE TABLE IF NOT EXISTS dm_catalog_entries (
    id              TEXT PRIMARY KEY,
    product_id      TEXT REFERENCES dm_data_products(id) ON DELETE CASCADE,
    catalog_name    TEXT NOT NULL,
    tags_json       TEXT DEFAULT '[]',
    metadata_json   TEXT DEFAULT '{}',
    lineage_json    TEXT DEFAULT '{}',
    classification  TEXT DEFAULT 'CUI // SP-CTI',
    created_at      TEXT DEFAULT CURRENT_TIMESTAMP,
    updated_at      TEXT DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_dm_catalog_entries_product ON dm_catalog_entries(product_id);

CREATE TABLE IF NOT EXISTS dm_audit (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    domain_id       TEXT,
    product_id      TEXT,
    "user"          TEXT DEFAULT '',
    action          TEXT NOT NULL,
    detail          TEXT DEFAULT '',
    classification  TEXT DEFAULT 'CUI // SP-CTI',
    created_at      TEXT DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_dm_audit_domain  ON dm_audit(domain_id);
CREATE INDEX IF NOT EXISTS idx_dm_audit_product ON dm_audit(product_id);

CREATE TABLE IF NOT EXISTS dm_opa_policies (
    id              TEXT PRIMARY KEY,
    domain_id       TEXT,
    name            TEXT NOT NULL,
    rego_text       TEXT DEFAULT '',
    policy_path     TEXT DEFAULT 'datamesh/allow',
    enabled         INTEGER DEFAULT 1,
    created_at      TEXT DEFAULT CURRENT_TIMESTAMP,
    updated_at      TEXT DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_dm_opa_policies_domain ON dm_opa_policies(domain_id);
CREATE INDEX IF NOT EXISTS idx_dm_opa_policies_enabled ON dm_opa_policies(enabled);

CREATE TABLE IF NOT EXISTS dm_policy_audit_log (
    id              TEXT PRIMARY KEY,
    policy_id       TEXT,
    "user"          TEXT DEFAULT 'system',
    resource        TEXT DEFAULT '{}',
    decision        INTEGER DEFAULT 0,
    reason          TEXT DEFAULT '',
    method          TEXT DEFAULT 'local',
    classification  TEXT DEFAULT 'CUI // SP-CTI',
    created_at      TEXT DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_dm_policy_audit_created ON dm_policy_audit_log(created_at DESC);

CREATE TABLE IF NOT EXISTS dm_csp_sync_log (
    id              TEXT PRIMARY KEY,
    provider        TEXT NOT NULL,
    domain_id       TEXT DEFAULT '',
    product_id      TEXT DEFAULT '',
    operation       TEXT NOT NULL,
    status          TEXT NOT NULL,
    synced_count    INTEGER DEFAULT 0,
    error_detail    TEXT DEFAULT '',
    created_at      TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_dm_csp_provider ON dm_csp_sync_log(provider, created_at);

CREATE TABLE IF NOT EXISTS dm_product_slas (
    id              TEXT PRIMARY KEY,
    product_id      TEXT REFERENCES dm_data_products(id) ON DELETE CASCADE,
    sla_type        TEXT NOT NULL,
    target_value    REAL NOT NULL,
    unit            TEXT DEFAULT '',
    created_at      TEXT DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_dm_product_slas_product ON dm_product_slas(product_id);

CREATE TABLE IF NOT EXISTS dm_product_subscriptions (
    id              TEXT PRIMARY KEY,
    product_id      TEXT REFERENCES dm_data_products(id) ON DELETE CASCADE,
    subscriber_team TEXT NOT NULL,
    purpose         TEXT DEFAULT '',
    approved        INTEGER DEFAULT 0,
    created_at      TEXT DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_dm_subscriptions_product ON dm_product_subscriptions(product_id);

CREATE TABLE IF NOT EXISTS dm_data_contracts (
    id              TEXT PRIMARY KEY,
    domain_id       TEXT DEFAULT '',
    product_id      TEXT DEFAULT '',
    name            TEXT NOT NULL,
    contract_yaml   TEXT DEFAULT '',
    version         TEXT DEFAULT '1.0.0',
    status          TEXT DEFAULT 'draft',
    classification  TEXT DEFAULT 'CUI // SP-CTI',
    created_at      TEXT DEFAULT CURRENT_TIMESTAMP,
    updated_at      TEXT DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_dm_contracts_domain ON dm_data_contracts(domain_id);
CREATE INDEX IF NOT EXISTS idx_dm_contracts_product ON dm_data_contracts(product_id);
CREATE INDEX IF NOT EXISTS idx_dm_contracts_status  ON dm_data_contracts(status);

CREATE TABLE IF NOT EXISTS dm_contract_test_runs (
    id              TEXT PRIMARY KEY,
    contract_id     TEXT REFERENCES dm_data_contracts(id) ON DELETE CASCADE,
    passed          INTEGER DEFAULT 0,
    error_count     INTEGER DEFAULT 0,
    warnings        INTEGER DEFAULT 0,
    result_json     TEXT DEFAULT '{}',
    method          TEXT DEFAULT 'internal',
    created_at      TEXT DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_dm_test_runs_contract ON dm_contract_test_runs(contract_id);

-- ── AI Data Mapping ──────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS dd_mapping_sessions (
    id                  TEXT PRIMARY KEY,
    name                TEXT NOT NULL DEFAULT 'Untitled Mapping',
    source_format       TEXT NOT NULL DEFAULT 'json_schema',
    target_format       TEXT NOT NULL DEFAULT 'sql_ddl',
    source_schema_json  TEXT DEFAULT '{}',
    target_schema_json  TEXT DEFAULT '{}',
    status              TEXT NOT NULL DEFAULT 'pending'
                        CHECK (status IN ('pending','ingested','suggested','complete','error')),
    field_count         INTEGER DEFAULT 0,
    confirmed_count     INTEGER DEFAULT 0,
    rejected_count      INTEGER DEFAULT 0,
    classification      TEXT NOT NULL DEFAULT 'CUI',
    tenant_id           TEXT NOT NULL DEFAULT 'default',
    created_by          TEXT DEFAULT '',
    created_at          TEXT DEFAULT CURRENT_TIMESTAMP,
    updated_at          TEXT DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_dd_ms_tenant  ON dd_mapping_sessions(tenant_id);
CREATE INDEX IF NOT EXISTS idx_dd_ms_status  ON dd_mapping_sessions(status);
CREATE INDEX IF NOT EXISTS idx_dd_ms_created ON dd_mapping_sessions(created_at DESC);

CREATE TABLE IF NOT EXISTS dd_field_mappings (
    id              TEXT PRIMARY KEY,
    session_id      TEXT NOT NULL REFERENCES dd_mapping_sessions(id) ON DELETE CASCADE,
    source_field    TEXT NOT NULL,
    source_type     TEXT DEFAULT '',
    source_path     TEXT DEFAULT '',
    target_field    TEXT NOT NULL,
    target_type     TEXT DEFAULT '',
    target_path     TEXT DEFAULT '',
    confidence      REAL NOT NULL DEFAULT 0.0,
    match_method    TEXT DEFAULT 'name'
                    CHECK (match_method IN ('name','semantic','type','combined','manual')),
    status          TEXT NOT NULL DEFAULT 'pending'
                    CHECK (status IN ('pending','confirmed','rejected','needs_review')),
    transform_expr  TEXT DEFAULT '',
    notes           TEXT DEFAULT '',
    classification  TEXT NOT NULL DEFAULT 'CUI',
    tenant_id       TEXT NOT NULL DEFAULT 'default',
    created_at      TEXT DEFAULT CURRENT_TIMESTAMP,
    updated_at      TEXT DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_dd_fm_session    ON dd_field_mappings(session_id);
CREATE INDEX IF NOT EXISTS idx_dd_fm_status     ON dd_field_mappings(status);
CREATE INDEX IF NOT EXISTS idx_dd_fm_confidence ON dd_field_mappings(confidence DESC);

CREATE TABLE IF NOT EXISTS dd_mapping_transforms (
    id              TEXT PRIMARY KEY,
    session_id      TEXT NOT NULL REFERENCES dd_mapping_sessions(id),
    artifact_type   TEXT NOT NULL DEFAULT 'sql'
                    CHECK (artifact_type IN ('sql','python','dbt','xslt')),
    artifact_text   TEXT NOT NULL DEFAULT '',
    field_count     INTEGER DEFAULT 0,
    generated_by    TEXT DEFAULT 'ai',
    model_used      TEXT DEFAULT '',
    classification  TEXT NOT NULL DEFAULT 'CUI',
    tenant_id       TEXT NOT NULL DEFAULT 'default',
    created_at      TEXT DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_dd_mt_session ON dd_mapping_transforms(session_id);
CREATE INDEX IF NOT EXISTS idx_dd_mt_created ON dd_mapping_transforms(created_at DESC);

-- ── CAM extension: live data migration job tracking ──────────────────────────
CREATE TABLE IF NOT EXISTS dd_migration_jobs (
    id                  TEXT PRIMARY KEY,
    design_id           TEXT REFERENCES data_designs(id) ON DELETE CASCADE,
    source_type         TEXT NOT NULL
        CHECK(source_type IN ('oracle','mysql','mssql','mongodb','elasticsearch',
                              'redis','postgres','s3','cassandra','dynamodb','other')),
    target_type         TEXT NOT NULL,
    migration_tool      TEXT DEFAULT 'dms'
        CHECK(migration_tool IN ('dms','sct','pgloader','mongodump','snapshot_restore',
                                 'aws_glue','manual','other')),
    status              TEXT DEFAULT 'pending'
        CHECK(status IN ('pending','running','validating','complete','failed','paused')),
    row_count_source    INTEGER DEFAULT 0,
    row_count_target    INTEGER DEFAULT 0,
    validation_query    TEXT DEFAULT '',
    validation_status   TEXT DEFAULT 'pending'
        CHECK(validation_status IN ('pending','pass','fail','skipped')),
    config_json         TEXT DEFAULT '{}',
    notes               TEXT DEFAULT '',
    started_at          TEXT,
    completed_at        TEXT,
    created_at          TEXT DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_dd_migration_jobs_design ON dd_migration_jobs(design_id);
CREATE INDEX IF NOT EXISTS idx_dd_migration_jobs_status ON dd_migration_jobs(status);
