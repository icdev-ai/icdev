# CUI // SP-CTI
"""
Migration Design Canvas — DB initializer
Creates schema and seeds canonical migration design templates.

Dual-backend: SQLite (default) or PostgreSQL.
Set MC_STORAGE_BACKEND=postgresql + MC_PG_* env vars to use PostgreSQL.
"""

import json
import os
from pathlib import Path

from tools.db.storage import get_canvas_connection, sql_placeholder

_ICDEV_ROOT = Path(__file__).resolve().parents[3]
DB_PATH = _ICDEV_ROOT / "data" / "migration_canvas.db"

# Env var that carries the dedicated SQLite path when the resolved backend is
# sqlite.  On PostgreSQL (primary) it is ignored; canvas tables live in the
# shared icdev database, namespaced by their `mc_` prefix.
_MC_DB_PATH_ENV = "MC_DB_PATH"
os.environ.setdefault(_MC_DB_PATH_ENV, str(DB_PATH))

_MC_BACKEND = os.environ.get("MC_STORAGE_BACKEND", os.environ.get("ICDEV_CANVAS_STORAGE_BACKEND", os.environ.get("ICDEV_STORAGE_BACKEND", "postgresql"))).lower()


def get_connection():
    """Get a canvas database connection — RLS disabled.

    Migration Canvas tables (mc_*, migration_designs, etc.) do not carry
    tenant_id/classification columns on every row, so the global RLS predicate
    injected by tools.db.storage.get_connection would raise UndefinedColumn on
    PostgreSQL.  get_canvas_connection returns a connection with security
    context None, matching the canvas-table semantics used by other canvases.
    """
    return get_canvas_connection(_MC_DB_PATH_ENV)


SCHEMA = """
CREATE TABLE IF NOT EXISTS migration_designs (
    id              TEXT PRIMARY KEY,
    name            TEXT NOT NULL,
    description     TEXT,
    migration_type  TEXT DEFAULT 'application',
    graph_json      TEXT NOT NULL DEFAULT '{"nodes":[],"edges":[]}',
    template_id     TEXT,
    classification  TEXT DEFAULT 'CUI',
    created_at      TEXT DEFAULT CURRENT_TIMESTAMP,
    updated_at      TEXT DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS mc_templates (
    id              TEXT PRIMARY KEY,
    name            TEXT NOT NULL,
    category        TEXT,
    description     TEXT,
    graph_json      TEXT NOT NULL DEFAULT '{"nodes":[],"edges":[]}',
    tags            TEXT DEFAULT '[]'
);

CREATE TABLE IF NOT EXISTS mc_snippets (
    id          TEXT PRIMARY KEY,
    name        TEXT NOT NULL,
    category    TEXT,
    description TEXT,
    graph_json  TEXT NOT NULL DEFAULT '{"nodes":[],"edges":[]}',
    tags        TEXT DEFAULT '[]'
);

CREATE TABLE IF NOT EXISTS mc_assessments (
    id              TEXT PRIMARY KEY,
    design_id       TEXT REFERENCES migration_designs(id),
    assessment_type TEXT NOT NULL DEFAULT 'full',
    findings_json   TEXT DEFAULT '[]',
    score           REAL DEFAULT 0,
    grade           TEXT DEFAULT 'N/A',
    cat1_findings   INTEGER DEFAULT 0,
    cat2_findings   INTEGER DEFAULT 0,
    cat3_findings   INTEGER DEFAULT 0,
    readiness_score REAL DEFAULT 0,
    created_at      TEXT DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS mc_wave_plans (
    id              TEXT PRIMARY KEY,
    design_id       TEXT REFERENCES migration_designs(id),
    wave_number     INTEGER NOT NULL DEFAULT 1,
    name            TEXT NOT NULL DEFAULT 'Wave 1',
    description     TEXT DEFAULT '',
    node_ids_json   TEXT DEFAULT '[]',
    strategy        TEXT DEFAULT 'rehost',
    status          TEXT DEFAULT 'planned',
    estimated_hours REAL DEFAULT 0,
    risk_score      REAL DEFAULT 0,
    start_date      TEXT DEFAULT '',
    end_date        TEXT DEFAULT '',
    created_at      TEXT DEFAULT CURRENT_TIMESTAMP,
    updated_at      TEXT DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS mc_audit (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    design_id       TEXT,
    user            TEXT DEFAULT '',
    action          TEXT NOT NULL,
    detail          TEXT,
    classification  TEXT DEFAULT 'CUI // SP-CTI',
    created_at      TEXT DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS mc_versions (
    id              TEXT PRIMARY KEY,
    design_id       TEXT REFERENCES migration_designs(id),
    version_number  INTEGER NOT NULL,
    graph_json      TEXT NOT NULL DEFAULT '{"nodes":[],"edges":[]}',
    change_summary  TEXT DEFAULT '',
    user_id         TEXT DEFAULT '',
    created_at      TEXT DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS mc_sops (
    id              TEXT PRIMARY KEY,
    title           TEXT NOT NULL,
    sop_type        TEXT NOT NULL DEFAULT 'custom',
    description     TEXT DEFAULT '',
    purpose         TEXT DEFAULT '',
    scope           TEXT DEFAULT '',
    steps           TEXT DEFAULT '[]',
    nist_controls   TEXT DEFAULT '[]',
    owner           TEXT DEFAULT '',
    reviewer        TEXT DEFAULT '',
    approval_status TEXT NOT NULL DEFAULT 'draft',
    version         TEXT DEFAULT '1.0',
    next_review_date TEXT DEFAULT '',
    classification  TEXT DEFAULT 'CUI // SP-CTI',
    approved_by     TEXT DEFAULT '',
    approved_at     TEXT DEFAULT '',
    rejected_reason TEXT DEFAULT '',
    created_at      TEXT DEFAULT CURRENT_TIMESTAMP,
    updated_at      TEXT DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS mc_runbooks (
    id              TEXT PRIMARY KEY,
    design_id       TEXT REFERENCES migration_designs(id),
    title           TEXT NOT NULL,
    trigger_event   TEXT NOT NULL DEFAULT 'migration_issue',
    severity        TEXT DEFAULT 'high',
    description     TEXT DEFAULT '',
    steps_json      TEXT DEFAULT '[]',
    owner           TEXT DEFAULT '',
    classification  TEXT DEFAULT 'CUI // SP-CTI',
    created_at      TEXT DEFAULT CURRENT_TIMESTAMP,
    updated_at      TEXT DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS mc_oracle_predictions (
    id              TEXT PRIMARY KEY,
    design_id       TEXT,
    lens_id         TEXT NOT NULL DEFAULT 'migration',
    title           TEXT,
    description     TEXT,
    confidence      REAL DEFAULT 0,
    severity        TEXT DEFAULT 'info',
    category        TEXT DEFAULT '',
    recommendations TEXT DEFAULT '[]',
    data_json       TEXT DEFAULT '{}',
    created_at      TEXT DEFAULT CURRENT_TIMESTAMP
);

-- Indexes
CREATE INDEX IF NOT EXISTS idx_mc_assessments_design ON mc_assessments(design_id);
CREATE INDEX IF NOT EXISTS idx_mc_wave_plans_design ON mc_wave_plans(design_id);
CREATE INDEX IF NOT EXISTS idx_mc_audit_design ON mc_audit(design_id);
CREATE INDEX IF NOT EXISTS idx_mc_audit_action ON mc_audit(action);
CREATE INDEX IF NOT EXISTS idx_mc_versions_design ON mc_versions(design_id);
CREATE INDEX IF NOT EXISTS idx_mc_sops_type ON mc_sops(sop_type);
CREATE INDEX IF NOT EXISTS idx_mc_sops_status ON mc_sops(approval_status);
CREATE INDEX IF NOT EXISTS idx_mc_runbooks_design ON mc_runbooks(design_id);
CREATE INDEX IF NOT EXISTS idx_mc_runbooks_trigger ON mc_runbooks(trigger_event);
CREATE INDEX IF NOT EXISTS idx_mc_oracle_design ON mc_oracle_predictions(design_id);
CREATE INDEX IF NOT EXISTS idx_mc_oracle_severity ON mc_oracle_predictions(severity);

CREATE TABLE IF NOT EXISTS mc_inventory_imports (
    id                  TEXT PRIMARY KEY,
    session_id          TEXT NOT NULL,
    format              TEXT NOT NULL CHECK(format IN ('csv','json','aws_hub','azure_migrate','manual')),
    filename            TEXT,
    row_count           INTEGER DEFAULT 0,
    parsed_servers_json TEXT DEFAULT '[]',
    status              TEXT NOT NULL CHECK(status IN ('pending','parsed','error')) DEFAULT 'pending',
    error_msg           TEXT,
    imported_at         TEXT NOT NULL,
    classification      TEXT DEFAULT 'CUI'
) WITHOUT ROWID;

CREATE INDEX IF NOT EXISTS idx_mc_inv_session ON mc_inventory_imports(session_id);
"""


# ── Application Migration Schema ─────────────────────────────────────────────

APP_MIGRATION_SCHEMA = """
CREATE TABLE IF NOT EXISTS mc_app_inventory (
    id                  TEXT PRIMARY KEY,
    session_id          TEXT REFERENCES mc_srv_sessions(id),
    name                TEXT NOT NULL,
    version             TEXT,
    language            TEXT,
    framework           TEXT,
    app_type            TEXT CHECK(app_type IN ('web','api','batch','database','middleware','desktop','mobile','iot','saas')),
    owner               TEXT,
    team                TEXT,
    criticality         TEXT CHECK(criticality IN ('mission_critical','high','medium','low')),
    environment         TEXT CHECK(environment IN ('production','staging','development')),
    stig_category       TEXT CHECK(stig_category IN ('cat1','cat2','cat3','na')),
    license_type        TEXT,
    license_expiry      TEXT,
    source_repo         TEXT,
    artifact_url        TEXT,
    dependencies_json   TEXT DEFAULT '[]',
    migration_strategy  TEXT CHECK(migration_strategy IN ('rehost','replatform','refactor','rearchitect','repurchase','retire','retain')),
    migration_status    TEXT DEFAULT 'pending',
    notes               TEXT,
    classification      TEXT DEFAULT 'CUI',
    created_at          TEXT DEFAULT CURRENT_TIMESTAMP,
    updated_at          TEXT DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_mc_app_inv_session    ON mc_app_inventory(session_id);
CREATE INDEX IF NOT EXISTS idx_mc_app_inv_criticality ON mc_app_inventory(criticality);

CREATE TABLE IF NOT EXISTS mc_app_server_bindings (
    id          TEXT PRIMARY KEY,
    app_id      TEXT NOT NULL REFERENCES mc_app_inventory(id) ON DELETE CASCADE,
    server_id   TEXT NOT NULL REFERENCES mc_srv_inventory(id) ON DELETE CASCADE,
    role        TEXT CHECK(role IN ('primary','replica','standby','worker','scheduler')),
    port        INTEGER,
    protocol    TEXT,
    notes       TEXT,
    created_at  TEXT DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(app_id, server_id, role)
);

CREATE INDEX IF NOT EXISTS idx_mc_app_srv_app    ON mc_app_server_bindings(app_id);
CREATE INDEX IF NOT EXISTS idx_mc_app_srv_server ON mc_app_server_bindings(server_id);

CREATE TABLE IF NOT EXISTS mc_app_data_sources (
    id                      TEXT PRIMARY KEY,
    app_id                  TEXT REFERENCES mc_app_inventory(id) ON DELETE CASCADE,
    source_type             TEXT CHECK(source_type IN ('postgresql','mysql','oracle','mssql','mongodb','redis','elasticsearch','s3','sftp','api')),
    host                    TEXT,
    port                    INTEGER,
    database_name           TEXT,
    schema_version          TEXT,
    estimated_size_gb       REAL,
    replication_lag_seconds INTEGER,
    migration_method        TEXT CHECK(migration_method IN ('dump_restore','cdc','replication','manual')),
    migration_status        TEXT DEFAULT 'pending',
    notes                   TEXT,
    created_at              TEXT DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS mc_app_dependencies (
    id              TEXT PRIMARY KEY,
    source_app_id   TEXT REFERENCES mc_app_inventory(id) ON DELETE CASCADE,
    target_app_id   TEXT REFERENCES mc_app_inventory(id),
    target_server_id TEXT REFERENCES mc_srv_inventory(id),
    target_service  TEXT CHECK(target_service IN ('database','cache','message_queue','api','auth','storage','cdn','monitor')),
    dep_type        TEXT CHECK(dep_type IN ('hard','soft','optional')),
    protocol        TEXT,
    port            INTEGER,
    latency_sla_ms  INTEGER,
    notes           TEXT,
    created_at      TEXT DEFAULT CURRENT_TIMESTAMP,
    CHECK(target_app_id IS NOT NULL OR target_server_id IS NOT NULL)
);

CREATE INDEX IF NOT EXISTS idx_mc_app_dep_source ON mc_app_dependencies(source_app_id);
CREATE INDEX IF NOT EXISTS idx_mc_app_dep_target ON mc_app_dependencies(target_app_id);

CREATE TABLE IF NOT EXISTS mc_app_migration_steps (
    id              TEXT PRIMARY KEY,
    app_id          TEXT REFERENCES mc_app_inventory(id) ON DELETE CASCADE,
    step_order      INTEGER,
    phase           TEXT CHECK(phase IN ('pre','cutover','post','rollback')),
    action          TEXT,
    command         TEXT,
    expected_output TEXT,
    timeout_seconds INTEGER,
    status          TEXT DEFAULT 'pending',
    executed_at     TEXT,
    result          TEXT,
    created_at      TEXT DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_mc_app_steps_app   ON mc_app_migration_steps(app_id);
CREATE INDEX IF NOT EXISTS idx_mc_app_steps_phase ON mc_app_migration_steps(phase);

CREATE TABLE IF NOT EXISTS mc_data_migration (
    id                          TEXT PRIMARY KEY,
    session_id                  TEXT,
    app_id                      TEXT REFERENCES mc_app_inventory(id),
    source_type                 TEXT CHECK(source_type IN ('postgresql','mysql','oracle','mssql','mongodb','redis','files','s3')),
    source_host                 TEXT,
    source_db                   TEXT,
    source_schema               TEXT,
    target_type                 TEXT,
    target_host                 TEXT,
    target_db                   TEXT,
    target_schema               TEXT,
    migration_method            TEXT CHECK(migration_method IN ('dump_restore','cdc','pgloader','mysqldump','mongodump','rsync','aws_dms')),
    estimated_size_gb           REAL,
    estimated_duration_minutes  INTEGER,
    validation_query            TEXT,
    validation_status           TEXT DEFAULT 'pending',
    cutover_type                TEXT CHECK(cutover_type IN ('offline','online_with_cdc','snapshot')),
    rollback_procedure          TEXT,
    status                      TEXT DEFAULT 'planned',
    started_at                  TEXT,
    completed_at                TEXT,
    notes                       TEXT,
    classification              TEXT DEFAULT 'CUI',
    created_at                  TEXT DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_mc_data_mig_app ON mc_data_migration(app_id);
CREATE INDEX IF NOT EXISTS idx_mc_data_mig_status ON mc_data_migration(status);
"""


# ── Network Device Migration Schema ─────────────────────────────────────────

NETWORK_MIGRATION_SCHEMA = """
CREATE TABLE IF NOT EXISTS mc_net_sessions (
    id              TEXT PRIMARY KEY,
    design_id       TEXT REFERENCES migration_designs(id),
    src_model       TEXT NOT NULL DEFAULT 'MX10003',
    tgt_model       TEXT NOT NULL DEFAULT 'MX304',
    src_device_name TEXT DEFAULT '',
    tgt_device_name TEXT DEFAULT '',
    src_site        TEXT DEFAULT '',
    tgt_site        TEXT DEFAULT '',
    src_config_raw  TEXT DEFAULT '',
    target_config   TEXT DEFAULT '',
    config_parsed   INTEGER DEFAULT 0,
    engineer_context TEXT DEFAULT '',
    recommended_coa TEXT CHECK(recommended_coa IN ('coa_a','coa_b','coa_c','')) DEFAULT '',
    selected_coa    TEXT CHECK(selected_coa IN ('coa_a','coa_b','coa_c','')) DEFAULT '',
    coa_rationale   TEXT DEFAULT '',
    topology_json   TEXT DEFAULT '',
    topology_neighbors_json TEXT DEFAULT '',
    readiness_score REAL DEFAULT 0,
    status          TEXT DEFAULT 'in_progress',
    classification  TEXT DEFAULT 'CUI // SP-CTI',
    created_at      TEXT DEFAULT CURRENT_TIMESTAMP,
    updated_at      TEXT DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS mc_net_port_map (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id      TEXT NOT NULL REFERENCES mc_net_sessions(id),
    src_interface   TEXT NOT NULL,
    src_speed_gbps  REAL DEFAULT 0,
    src_media       TEXT DEFAULT '',
    src_optic_type  TEXT DEFAULT '',
    src_ip_address  TEXT DEFAULT '',
    src_description TEXT DEFAULT '',
    src_circuit_id  TEXT DEFAULT '',
    tgt_interface   TEXT DEFAULT '',
    tgt_speed_gbps  REAL DEFAULT 0,
    tgt_optic_required TEXT DEFAULT '',
    optic_change    INTEGER DEFAULT 0,
    speed_mismatch  INTEGER DEFAULT 0,
    cable_id        TEXT DEFAULT '',
    far_end_device  TEXT DEFAULT '',
    far_end_port    TEXT DEFAULT '',
    notes           TEXT DEFAULT '',
    status          TEXT DEFAULT 'pending',
    created_at      TEXT DEFAULT CURRENT_TIMESTAMP,
    updated_at      TEXT DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS mc_net_compat_checks (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id      TEXT NOT NULL REFERENCES mc_net_sessions(id),
    category        TEXT NOT NULL DEFAULT 'hardware',
    check_name      TEXT NOT NULL,
    expected        TEXT DEFAULT '',
    actual          TEXT DEFAULT '',
    severity        TEXT DEFAULT 'cat2',
    status          TEXT DEFAULT 'pending',
    override_reason TEXT DEFAULT '',
    auto_detected   INTEGER DEFAULT 1,
    created_at      TEXT DEFAULT CURRENT_TIMESTAMP,
    updated_at      TEXT DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS mc_net_test_cases (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id      TEXT NOT NULL REFERENCES mc_net_sessions(id),
    phase           TEXT NOT NULL DEFAULT 'pre',
    seq_no          INTEGER DEFAULT 0,
    test_name       TEXT NOT NULL,
    procedure       TEXT DEFAULT '',
    expected_result TEXT DEFAULT '',
    actual_result   TEXT DEFAULT '',
    passed          INTEGER DEFAULT NULL,
    notes           TEXT DEFAULT '',
    executed_at     TEXT DEFAULT '',
    created_at      TEXT DEFAULT CURRENT_TIMESTAMP,
    updated_at      TEXT DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS mc_net_cutover_steps (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id      TEXT NOT NULL REFERENCES mc_net_sessions(id),
    seq_no          INTEGER DEFAULT 0,
    circuit_id      TEXT DEFAULT '',
    interface       TEXT DEFAULT '',
    description     TEXT DEFAULT '',
    drain_action    TEXT DEFAULT '',
    cutover_action  TEXT DEFAULT '',
    verify_action   TEXT DEFAULT '',
    rollback_action TEXT DEFAULT '',
    duration_min    INTEGER DEFAULT 5,
    executed_at     TEXT DEFAULT '',
    status          TEXT DEFAULT 'pending',
    created_at      TEXT DEFAULT CURRENT_TIMESTAMP,
    updated_at      TEXT DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS mc_net_erb_metadata (
    id                   TEXT PRIMARY KEY,
    session_id           TEXT NOT NULL REFERENCES mc_net_sessions(id),
    change_type          TEXT DEFAULT 'hardware_replacement',
    risk_tier            TEXT DEFAULT 'medium',
    business_justification TEXT DEFAULT '',
    impact_summary       TEXT DEFAULT '',
    rollback_plan        TEXT DEFAULT '',
    mw_start             TEXT DEFAULT '',
    mw_end               TEXT DEFAULT '',
    go_nogo_criteria     TEXT DEFAULT '{}',
    requestor            TEXT DEFAULT '',
    sop_id               TEXT DEFAULT '',
    approval_status      TEXT DEFAULT 'draft',
    created_at           TEXT DEFAULT CURRENT_TIMESTAMP,
    updated_at           TEXT DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_mc_net_port_map_session ON mc_net_port_map(session_id);
CREATE INDEX IF NOT EXISTS idx_mc_net_compat_session ON mc_net_compat_checks(session_id);
CREATE INDEX IF NOT EXISTS idx_mc_net_compat_category ON mc_net_compat_checks(category);
CREATE INDEX IF NOT EXISTS idx_mc_net_tests_session ON mc_net_test_cases(session_id);
CREATE INDEX IF NOT EXISTS idx_mc_net_tests_phase ON mc_net_test_cases(phase);
CREATE INDEX IF NOT EXISTS idx_mc_net_cutover_session ON mc_net_cutover_steps(session_id);
CREATE INDEX IF NOT EXISTS idx_mc_net_erb_session ON mc_net_erb_metadata(session_id);

CREATE TABLE IF NOT EXISTS mc_net_ai_sessions (
    id          TEXT PRIMARY KEY,
    session_id  TEXT NOT NULL,
    role        TEXT NOT NULL DEFAULT 'engineer',
    message     TEXT NOT NULL,
    model_used  TEXT DEFAULT '',
    created_at  TEXT DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_mc_net_ai_session ON mc_net_ai_sessions(session_id);

CREATE TABLE IF NOT EXISTS mc_net_protocol_plans (
    id                    TEXT PRIMARY KEY,
    session_id            TEXT NOT NULL,
    protocol              TEXT NOT NULL,
    src_config_json       TEXT DEFAULT '{}',
    tgt_config_json       TEXT DEFAULT '{}',
    migration_steps_json  TEXT DEFAULT '[]',
    risk_level            TEXT DEFAULT 'medium',
    ai_notes              TEXT DEFAULT '',
    status                TEXT DEFAULT 'draft',
    created_at            TEXT DEFAULT CURRENT_TIMESTAMP,
    updated_at            TEXT DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(session_id, protocol)
);
CREATE INDEX IF NOT EXISTS idx_mc_net_proto_session ON mc_net_protocol_plans(session_id);

CREATE TABLE IF NOT EXISTS mc_net_config_map (
    id                  TEXT PRIMARY KEY,
    session_id          TEXT NOT NULL REFERENCES mc_net_sessions(id) ON DELETE CASCADE,
    src_section_type    TEXT NOT NULL,
    src_stanza_text     TEXT NOT NULL,
    src_lines_json      TEXT DEFAULT '[]',
    tgt_section_type    TEXT DEFAULT '',
    tgt_stanza_text     TEXT DEFAULT '',
    mapping_action      TEXT NOT NULL CHECK(mapping_action IN ('direct','rename','merge','split','remove','manual','skip')) DEFAULT 'direct',
    confidence          REAL DEFAULT 0,
    ai_rationale        TEXT DEFAULT '',
    ai_question_key     TEXT DEFAULT '',
    status              TEXT NOT NULL CHECK(status IN ('pending','approved','rejected','skipped','needs_review')) DEFAULT 'pending',
    reviewer_note       TEXT DEFAULT '',
    applied_to_target   INTEGER DEFAULT 0,
    created_at          TEXT DEFAULT CURRENT_TIMESTAMP,
    updated_at          TEXT DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_mc_net_cfgmap_session ON mc_net_config_map(session_id);
CREATE INDEX IF NOT EXISTS idx_mc_net_cfgmap_status ON mc_net_config_map(status);

CREATE TABLE IF NOT EXISTS mc_net_config_questions (
    id              TEXT PRIMARY KEY,
    session_id      TEXT NOT NULL REFERENCES mc_net_sessions(id) ON DELETE CASCADE,
    question_key    TEXT NOT NULL,
    question_text   TEXT NOT NULL,
    default_answer  INTEGER DEFAULT NULL,
    user_answer     INTEGER DEFAULT NULL,
    ai_relevance    TEXT DEFAULT '',
    UNIQUE(session_id, question_key)
);
CREATE INDEX IF NOT EXISTS idx_mc_net_cfgq_session ON mc_net_config_questions(session_id);

CREATE TABLE IF NOT EXISTS mc_net_coa_questions (
    id              TEXT PRIMARY KEY,
    session_id      TEXT NOT NULL REFERENCES mc_net_sessions(id) ON DELETE CASCADE,
    question_key    TEXT NOT NULL,
    question_text   TEXT NOT NULL,
    default_answer  INTEGER DEFAULT NULL,
    user_answer     INTEGER DEFAULT NULL,
    coa_a_weight    REAL DEFAULT 0,
    coa_b_weight    REAL DEFAULT 0,
    coa_c_weight    REAL DEFAULT 0,
    ai_relevance    TEXT DEFAULT '',
    UNIQUE(session_id, question_key)
);
CREATE INDEX IF NOT EXISTS idx_mc_net_coaq_session ON mc_net_coa_questions(session_id);

CREATE TABLE IF NOT EXISTS mc_net_parallel_timelines (
    id                   TEXT PRIMARY KEY,
    session_id           TEXT NOT NULL,
    milestone_name       TEXT NOT NULL,
    description          TEXT DEFAULT '',
    days_before_cutover  INTEGER DEFAULT 0,
    phase                TEXT NOT NULL DEFAULT 'pre_migration',
    owner                TEXT DEFAULT '',
    duration_hours       INTEGER DEFAULT 1,
    status               TEXT DEFAULT 'planned',
    notes                TEXT DEFAULT '',
    created_at           TEXT DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_mc_net_timeline_session ON mc_net_parallel_timelines(session_id);

CREATE TABLE IF NOT EXISTS mc_net_topology_neighbors (
    id              TEXT PRIMARY KEY,
    session_id      TEXT NOT NULL REFERENCES mc_net_sessions(id) ON DELETE CASCADE,
    neighbor_name   TEXT DEFAULT '',
    neighbor_ip     TEXT DEFAULT '',
    relationship    TEXT DEFAULT '',
    source_interface TEXT DEFAULT '',
    is_discovered   INTEGER DEFAULT 0,
    notes           TEXT DEFAULT '',
    created_at      TEXT DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_mc_net_topo_neighbor_session ON mc_net_topology_neighbors(session_id);
"""


def _migrate_network_tables(conn):
    """Idempotently add network migration tables and columns to existing DBs."""
    conn.executescript(NETWORK_MIGRATION_SCHEMA)
    # Add network_session_id to migration_designs if not present (ALTER TABLE is idempotent via try/except)
    try:
        conn.execute("ALTER TABLE migration_designs ADD COLUMN network_session_id TEXT DEFAULT NULL")
        conn.commit()
    except Exception:
        pass  # column already exists
    # Add target_config to mc_net_sessions if not present.
    try:
        conn.execute("ALTER TABLE mc_net_sessions ADD COLUMN target_config TEXT DEFAULT ''")
        conn.commit()
    except Exception:
        pass  # column already exists
    # Add COA/topology columns to mc_net_sessions if not present.
    for col, ddl in [
        ("engineer_context", "TEXT DEFAULT ''"),
        ("recommended_coa", "TEXT DEFAULT ''"),
        ("selected_coa", "TEXT DEFAULT ''"),
        ("coa_rationale", "TEXT DEFAULT ''"),
        ("topology_json", "TEXT DEFAULT ''"),
        ("topology_neighbors_json", "TEXT DEFAULT ''"),
    ]:
        try:
            conn.execute(f"ALTER TABLE mc_net_sessions ADD COLUMN {col} {ddl}")
            conn.commit()
        except Exception:
            pass  # column already exists


# ── Server Migration Schema ──────────────────────────────────────────────────

SERVER_MIGRATION_SCHEMA = """
-- Cloud / on-prem instance catalog (seeded at init; refreshed from APIs when online)
CREATE TABLE IF NOT EXISTS mc_cloud_instances (
    id                   INTEGER PRIMARY KEY AUTOINCREMENT,
    provider             TEXT NOT NULL,
    instance_type        TEXT NOT NULL,
    family               TEXT NOT NULL DEFAULT '',
    vcpus                INTEGER NOT NULL DEFAULT 0,
    ram_gb               REAL NOT NULL DEFAULT 0,
    local_storage_gb     REAL DEFAULT 0,
    storage_type         TEXT DEFAULT '',
    network_gbps         REAL DEFAULT 0,
    premium_disk_opt     INTEGER DEFAULT 0,
    cost_tier            TEXT DEFAULT 'medium',
    govcloud             INTEGER DEFAULT 0,
    il_support           TEXT DEFAULT '[]',
    use_case_tags        TEXT DEFAULT '[]',
    eol_status           TEXT DEFAULT 'active',
    compliance_certs     TEXT DEFAULT '{}',
    source               TEXT DEFAULT 'seed',
    last_refreshed_at    TEXT DEFAULT NULL,
    created_at           TEXT DEFAULT CURRENT_TIMESTAMP
);
CREATE UNIQUE INDEX IF NOT EXISTS uq_mc_cloud_instances ON mc_cloud_instances(provider, instance_type);
CREATE INDEX IF NOT EXISTS idx_mc_cloud_instances_provider ON mc_cloud_instances(provider);
CREATE INDEX IF NOT EXISTS idx_mc_cloud_instances_family ON mc_cloud_instances(family);
CREATE INDEX IF NOT EXISTS idx_mc_cloud_instances_vcpus ON mc_cloud_instances(vcpus);
CREATE INDEX IF NOT EXISTS idx_mc_cloud_instances_ram ON mc_cloud_instances(ram_gb);

-- Server migration session header
CREATE TABLE IF NOT EXISTS mc_srv_sessions (
    id                  TEXT PRIMARY KEY,
    design_id           TEXT REFERENCES migration_designs(id),
    migration_type      TEXT NOT NULL DEFAULT 'p2v_cloud',
    src_hostname        TEXT DEFAULT '',
    src_ip              TEXT DEFAULT '',
    src_os              TEXT DEFAULT '',
    src_os_version      TEXT DEFAULT '',
    src_hypervisor      TEXT DEFAULT '',
    src_datacenter      TEXT DEFAULT '',
    tgt_platform        TEXT NOT NULL DEFAULT '',
    tgt_region          TEXT DEFAULT '',
    tgt_account_id      TEXT DEFAULT '',
    tgt_instance_id     INTEGER REFERENCES mc_cloud_instances(id),
    readiness_score     REAL DEFAULT 0,
    status              TEXT DEFAULT 'in_progress',
    notes               TEXT DEFAULT '',
    classification      TEXT DEFAULT 'CUI // SP-CTI',
    created_at          TEXT DEFAULT CURRENT_TIMESTAMP,
    updated_at          TEXT DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_mc_srv_sessions_status ON mc_srv_sessions(status);

-- Source server hardware inventory
CREATE TABLE IF NOT EXISTS mc_srv_inventory (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id          TEXT NOT NULL REFERENCES mc_srv_sessions(id),
    vcpus               INTEGER DEFAULT 0,
    ram_gb              REAL DEFAULT 0,
    disk_count          INTEGER DEFAULT 0,
    total_disk_gb       REAL DEFAULT 0,
    disk_type           TEXT DEFAULT '',
    nic_count           INTEGER DEFAULT 0,
    primary_nic_gbps    REAL DEFAULT 0,
    os_family           TEXT DEFAULT '',
    os_name             TEXT DEFAULT '',
    os_arch             TEXT DEFAULT '',
    bios_type           TEXT DEFAULT '',
    virtualization_ext  INTEGER DEFAULT 0,
    raw_export_json     TEXT DEFAULT '{}',
    created_at          TEXT DEFAULT CURRENT_TIMESTAMP,
    updated_at          TEXT DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_mc_srv_inventory_session ON mc_srv_inventory(session_id);

-- Installed services / roles on source server
CREATE TABLE IF NOT EXISTS mc_srv_services (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id          TEXT NOT NULL REFERENCES mc_srv_sessions(id),
    service_name        TEXT NOT NULL,
    service_role        TEXT DEFAULT '',
    port                INTEGER DEFAULT 0,
    protocol            TEXT DEFAULT 'tcp',
    status              TEXT DEFAULT 'running',
    auto_detected       INTEGER DEFAULT 0,
    notes               TEXT DEFAULT '',
    created_at          TEXT DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_mc_srv_services_session ON mc_srv_services(session_id);
CREATE INDEX IF NOT EXISTS idx_mc_srv_services_role ON mc_srv_services(service_role);

-- Historical performance metrics (manual or imported)
CREATE TABLE IF NOT EXISTS mc_srv_performance (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id          TEXT NOT NULL REFERENCES mc_srv_sessions(id),
    cpu_avg_pct         REAL DEFAULT 0,
    cpu_peak_pct        REAL DEFAULT 0,
    ram_avg_pct         REAL DEFAULT 0,
    ram_peak_pct        REAL DEFAULT 0,
    disk_iops_avg       REAL DEFAULT 0,
    disk_iops_peak      REAL DEFAULT 0,
    net_mbps_avg        REAL DEFAULT 0,
    net_mbps_peak       REAL DEFAULT 0,
    sample_period_days  INTEGER DEFAULT 30,
    source              TEXT DEFAULT 'manual',
    created_at          TEXT DEFAULT CURRENT_TIMESTAMP,
    updated_at          TEXT DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_mc_srv_perf_session ON mc_srv_performance(session_id);

-- CAT1/CAT2/CAT3 compatibility check results
CREATE TABLE IF NOT EXISTS mc_srv_compat_checks (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id          TEXT NOT NULL REFERENCES mc_srv_sessions(id),
    category            TEXT NOT NULL DEFAULT 'compute',
    check_name          TEXT NOT NULL,
    expected            TEXT DEFAULT '',
    actual              TEXT DEFAULT '',
    severity            TEXT DEFAULT 'cat2',
    status              TEXT DEFAULT 'pending',
    override_reason     TEXT DEFAULT '',
    auto_detected       INTEGER DEFAULT 1,
    created_at          TEXT DEFAULT CURRENT_TIMESTAMP,
    updated_at          TEXT DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_mc_srv_compat_session ON mc_srv_compat_checks(session_id);
CREATE INDEX IF NOT EXISTS idx_mc_srv_compat_sev ON mc_srv_compat_checks(severity);

-- Rightsizing recommendations (top-3, rank 1 = best fit)
CREATE TABLE IF NOT EXISTS mc_srv_rightsizing (
    id                         INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id                 TEXT NOT NULL REFERENCES mc_srv_sessions(id),
    recommended_instance_id    INTEGER REFERENCES mc_cloud_instances(id),
    rank                       INTEGER DEFAULT 1,
    cost_tier                  TEXT DEFAULT 'medium',
    rationale                  TEXT DEFAULT '',
    vcpu_req                   REAL DEFAULT 0,
    ram_req_gb                 REAL DEFAULT 0,
    disk_req_gb                REAL DEFAULT 0,
    iops_req                   REAL DEFAULT 0,
    net_req_mbps               REAL DEFAULT 0,
    headroom_factor            REAL DEFAULT 1.2,
    created_at                 TEXT DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_mc_srv_rightsizing_session ON mc_srv_rightsizing(session_id);

-- Inter-server dependencies
CREATE TABLE IF NOT EXISTS mc_srv_dependencies (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id          TEXT NOT NULL REFERENCES mc_srv_sessions(id),
    dep_hostname        TEXT NOT NULL,
    dep_ip              TEXT DEFAULT '',
    dep_role            TEXT DEFAULT '',
    dep_type            TEXT DEFAULT 'inbound',
    dep_port            INTEGER DEFAULT 0,
    dep_protocol        TEXT DEFAULT 'tcp',
    criticality         TEXT DEFAULT 'medium',
    migration_order     INTEGER DEFAULT 0,
    notes               TEXT DEFAULT '',
    created_at          TEXT DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_mc_srv_deps_session ON mc_srv_dependencies(session_id);

-- NIC-to-NIC mapping
CREATE TABLE IF NOT EXISTS mc_srv_nic_map (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id          TEXT NOT NULL REFERENCES mc_srv_sessions(id),
    src_nic             TEXT NOT NULL,
    src_speed_gbps      REAL DEFAULT 0,
    src_mac             TEXT DEFAULT '',
    src_vlan            TEXT DEFAULT '',
    src_ip              TEXT DEFAULT '',
    src_subnet          TEXT DEFAULT '',
    src_description     TEXT DEFAULT '',
    tgt_nic             TEXT DEFAULT '',
    tgt_speed_gbps      REAL DEFAULT 0,
    tgt_vlan            TEXT DEFAULT '',
    tgt_ip              TEXT DEFAULT '',
    ip_change           INTEGER DEFAULT 0,
    requires_dhcp       INTEGER DEFAULT 0,
    notes               TEXT DEFAULT '',
    status              TEXT DEFAULT 'pending',
    created_at          TEXT DEFAULT CURRENT_TIMESTAMP,
    updated_at          TEXT DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_mc_srv_nic_session ON mc_srv_nic_map(session_id);

-- Disk/volume mapping
CREATE TABLE IF NOT EXISTS mc_srv_storage_map (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id          TEXT NOT NULL REFERENCES mc_srv_sessions(id),
    src_disk            TEXT NOT NULL,
    src_size_gb         REAL DEFAULT 0,
    src_type            TEXT DEFAULT '',
    src_mount           TEXT DEFAULT '',
    src_filesystem      TEXT DEFAULT '',
    src_used_gb         REAL DEFAULT 0,
    tgt_volume          TEXT DEFAULT '',
    tgt_size_gb         REAL DEFAULT 0,
    tgt_type            TEXT DEFAULT '',
    tgt_iops_provisioned INTEGER DEFAULT 0,
    size_increase_pct   REAL DEFAULT 0,
    notes               TEXT DEFAULT '',
    status              TEXT DEFAULT 'pending',
    created_at          TEXT DEFAULT CURRENT_TIMESTAMP,
    updated_at          TEXT DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_mc_srv_storage_session ON mc_srv_storage_map(session_id);

-- Ordered cutover steps (drag-reorderable)
CREATE TABLE IF NOT EXISTS mc_srv_cutover_steps (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id          TEXT NOT NULL REFERENCES mc_srv_sessions(id),
    phase               TEXT NOT NULL DEFAULT 'cutover',
    seq_no              INTEGER DEFAULT 0,
    description         TEXT DEFAULT '',
    action              TEXT DEFAULT '',
    verify_action       TEXT DEFAULT '',
    rollback_action     TEXT DEFAULT '',
    owner               TEXT DEFAULT '',
    duration_min        INTEGER DEFAULT 5,
    executed_at         TEXT DEFAULT '',
    status              TEXT DEFAULT 'pending',
    created_at          TEXT DEFAULT CURRENT_TIMESTAMP,
    updated_at          TEXT DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_mc_srv_cutover_session ON mc_srv_cutover_steps(session_id);
CREATE INDEX IF NOT EXISTS idx_mc_srv_cutover_phase ON mc_srv_cutover_steps(phase);

-- Pre/mid/post migration test cases
CREATE TABLE IF NOT EXISTS mc_srv_test_cases (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id          TEXT NOT NULL REFERENCES mc_srv_sessions(id),
    phase               TEXT NOT NULL DEFAULT 'post',
    seq_no              INTEGER DEFAULT 0,
    test_name           TEXT NOT NULL,
    procedure           TEXT DEFAULT '',
    expected_result     TEXT DEFAULT '',
    actual_result       TEXT DEFAULT '',
    passed              INTEGER DEFAULT NULL,
    notes               TEXT DEFAULT '',
    executed_at         TEXT DEFAULT '',
    created_at          TEXT DEFAULT CURRENT_TIMESTAMP,
    updated_at          TEXT DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_mc_srv_tests_session ON mc_srv_test_cases(session_id);
CREATE INDEX IF NOT EXISTS idx_mc_srv_tests_phase ON mc_srv_test_cases(phase);

-- ERB/CCB change request metadata
CREATE TABLE IF NOT EXISTS mc_srv_erb_metadata (
    id                      TEXT PRIMARY KEY,
    session_id              TEXT NOT NULL REFERENCES mc_srv_sessions(id),
    change_type             TEXT DEFAULT 'server_migration',
    risk_tier               TEXT DEFAULT 'medium',
    business_justification  TEXT DEFAULT '',
    technical_summary       TEXT DEFAULT '',
    impact_summary          TEXT DEFAULT '',
    rollback_plan           TEXT DEFAULT '',
    mw_start                TEXT DEFAULT '',
    mw_end                  TEXT DEFAULT '',
    go_nogo_criteria        TEXT DEFAULT '{}',
    requestor               TEXT DEFAULT '',
    approver                TEXT DEFAULT '',
    approval_status         TEXT DEFAULT 'draft',
    created_at              TEXT DEFAULT CURRENT_TIMESTAMP,
    updated_at              TEXT DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_mc_srv_erb_session ON mc_srv_erb_metadata(session_id);
"""

# ── Wave + Dependency Schema ─────────────────────────────────────────────────

WAVE_DEP_SCHEMA = """
CREATE TABLE IF NOT EXISTS mc_migration_waves (
    id               TEXT PRIMARY KEY,
    session_id       TEXT NOT NULL,
    wave_number      INTEGER NOT NULL,
    name             TEXT NOT NULL,
    cutover_date     TEXT,
    status           TEXT CHECK(status IN ('planned','in_progress','complete','blocked')) DEFAULT 'planned',
    server_ids_json  TEXT DEFAULT '[]',
    notes            TEXT,
    created_at       TEXT NOT NULL,
    classification   TEXT DEFAULT 'CUI',
    app_count        INTEGER DEFAULT 0,
    app_names        TEXT DEFAULT '[]'
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_mc_waves_num ON mc_migration_waves(session_id, wave_number);

CREATE TABLE IF NOT EXISTS mc_server_dependencies (
    id               TEXT PRIMARY KEY,
    session_id       TEXT NOT NULL,
    source_server_id TEXT NOT NULL,
    target_server_id TEXT NOT NULL,
    dep_type         TEXT CHECK(dep_type IN ('network','application','database','auth','storage')) DEFAULT 'network',
    direction        TEXT CHECK(direction IN ('inbound','outbound','bidirectional')) DEFAULT 'bidirectional',
    notes            TEXT,
    created_at       TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_mc_deps_session ON mc_server_dependencies(session_id);

-- crx-mig-01 gap #2: per-wave backout/recovery plan (template-driven, HITL-approved)
CREATE TABLE IF NOT EXISTS mc_wave_backout (
    id                     TEXT PRIMARY KEY,
    session_id             TEXT NOT NULL,
    wave_id                TEXT NOT NULL,
    snapshot_prerequisites TEXT DEFAULT '[]',
    decision_points        TEXT DEFAULT '[]',
    go_no_go_criteria      TEXT DEFAULT '[]',
    recovery_steps         TEXT DEFAULT '[]',
    approved               INTEGER DEFAULT 0,
    approved_by            TEXT,
    approved_at            TEXT,
    created_at             TEXT NOT NULL,
    updated_at             TEXT NOT NULL,
    classification         TEXT DEFAULT 'CUI'
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_mc_wave_backout_wave ON mc_wave_backout(session_id, wave_id);

-- crx-mig-01 gap #3: per-workload post-migration validation checklist (composed engines)
CREATE TABLE IF NOT EXISTS mc_workload_validations (
    id             TEXT PRIMARY KEY,
    session_id     TEXT NOT NULL,
    wave_id        TEXT NOT NULL,
    workload_id    TEXT NOT NULL,
    workload_name  TEXT,
    check_type     TEXT NOT NULL,
    status         TEXT NOT NULL CHECK(status IN ('pass','fail','skip','error','pending')),
    detail         TEXT,
    run_at         TEXT NOT NULL,
    classification TEXT DEFAULT 'CUI'
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_mc_wlval_unique ON mc_workload_validations(session_id, wave_id, workload_id, check_type);
CREATE INDEX IF NOT EXISTS idx_mc_wlval_wave ON mc_workload_validations(session_id, wave_id);

-- crx-mig-01 gap #3: append-only HITL override audit for forced wave close (NIST AU)
CREATE TABLE IF NOT EXISTS mc_wave_close_overrides (
    id              TEXT PRIMARY KEY,
    session_id      TEXT NOT NULL,
    wave_id         TEXT NOT NULL,
    override_user   TEXT NOT NULL,
    reason          TEXT NOT NULL,
    failing_json    TEXT DEFAULT '[]',
    created_at      TEXT NOT NULL,
    classification  TEXT DEFAULT 'CUI'
);
CREATE INDEX IF NOT EXISTS idx_mc_wave_close_ovr_wave ON mc_wave_close_overrides(session_id, wave_id);

CREATE TABLE IF NOT EXISTS mc_compliance_checks (
    id              TEXT PRIMARY KEY,
    session_id      TEXT,
    design_id       TEXT,
    il_level        TEXT NOT NULL,
    target_env      TEXT NOT NULL,
    migration_type  TEXT,
    status          TEXT CHECK(status IN ('pass','warn','block')) DEFAULT 'pass',
    findings_json   TEXT DEFAULT '[]',
    frameworks_json TEXT DEFAULT '[]',
    created_at      TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_mc_cc_session ON mc_compliance_checks(session_id);
CREATE INDEX IF NOT EXISTS idx_mc_cc_design  ON mc_compliance_checks(design_id);
"""


def _migrate_server_tables(conn):
    """Idempotently add server migration tables to existing DBs."""
    conn.executescript(SERVER_MIGRATION_SCHEMA)
    # Column additions for tables that may already exist from a prior init
    _add_col_if_missing = [
        ("mc_srv_sessions", "notes",      "TEXT DEFAULT ''"),
        ("mc_srv_sessions", "src_os_version", "TEXT DEFAULT ''"),
        ("mc_srv_sessions", "src_datacenter",  "TEXT DEFAULT ''"),
        ("mc_srv_sessions", "tgt_account_id",  "TEXT DEFAULT ''"),
    ]
    for tbl, col, typedef in _add_col_if_missing:
        existing = [r[1] for r in conn.execute(f"PRAGMA table_info({tbl})").fetchall()]
        if col not in existing:
            try:
                conn.execute(f"ALTER TABLE {tbl} ADD COLUMN {col} {typedef}")
            except Exception:
                pass  # PG / already-exists race — safe to ignore


def _migrate_wave_dep_tables(conn):
    """Idempotently add wave-planning and server-dependency tables to existing DBs."""
    conn.executescript(WAVE_DEP_SCHEMA)


# ── Gap-Fill Schema (migration 084) ─────────────────────────────────────────

GAP_FILL_SCHEMA = """
-- Hypervisor pull sessions (VMware/Hyper-V/Nutanix live import)
CREATE TABLE IF NOT EXISTS mc_srv_hypervisor_sessions (
    id              TEXT PRIMARY KEY,
    session_id      TEXT NOT NULL,
    adapter_type    TEXT NOT NULL CHECK(adapter_type IN ('vmware','hyperv','nutanix')),
    host            TEXT NOT NULL,
    datacenter      TEXT DEFAULT '',
    cluster         TEXT DEFAULT '',
    pulled_at       TEXT NOT NULL,
    vm_count        INTEGER DEFAULT 0,
    status          TEXT DEFAULT 'ok',
    error_msg       TEXT
);
CREATE INDEX IF NOT EXISTS idx_mc_srv_hvsess_session ON mc_srv_hypervisor_sessions(session_id);

-- Post-migration server validation results
CREATE TABLE IF NOT EXISTS mc_srv_post_migration_tests (
    id          TEXT PRIMARY KEY,
    session_id  TEXT NOT NULL,
    run_at      TEXT NOT NULL,
    check_type  TEXT NOT NULL,
    target      TEXT NOT NULL,
    status      TEXT NOT NULL CHECK(status IN ('pass','fail','skip','error')),
    detail      TEXT,
    elapsed_ms  INTEGER
);
CREATE INDEX IF NOT EXISTS idx_mc_srv_pmtest_session ON mc_srv_post_migration_tests(session_id);
CREATE INDEX IF NOT EXISTS idx_mc_srv_pmtest_status ON mc_srv_post_migration_tests(status);

-- Vendor EOL database cache (network migration)
CREATE TABLE IF NOT EXISTS mc_net_eol_data (
    id            TEXT PRIMARY KEY,
    vendor        TEXT NOT NULL,
    model_pattern TEXT NOT NULL,
    eol_date      TEXT,
    eos_date      TEXT,
    eosm_date     TEXT,
    source        TEXT DEFAULT 'static_seed',
    synced_at     TEXT NOT NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS uq_mc_net_eol ON mc_net_eol_data(vendor, model_pattern);
CREATE INDEX IF NOT EXISTS idx_mc_net_eol_vendor ON mc_net_eol_data(vendor);

-- Post-migration network config validation results
CREATE TABLE IF NOT EXISTS mc_net_config_validation (
    id                  TEXT PRIMARY KEY,
    session_id          TEXT NOT NULL,
    device_id           TEXT,
    run_at              TEXT NOT NULL,
    diff_summary        TEXT DEFAULT '{}',
    completeness_score  REAL DEFAULT 0,
    status              TEXT DEFAULT 'pending'
        CHECK(status IN ('pass','partial','fail','pending'))
);
CREATE INDEX IF NOT EXISTS idx_mc_net_cfgval_session ON mc_net_config_validation(session_id);
"""

_GAP_FILL_ALTER = [
    # Advanced cloud pricing columns
    ("mc_cloud_instances", "pricing_model",     "TEXT DEFAULT 'on_demand'"),
    ("mc_cloud_instances", "spot_price",        "REAL"),
    ("mc_cloud_instances", "reserved_1yr_price","REAL"),
    ("mc_cloud_instances", "reserved_3yr_price","REAL"),
    ("mc_cloud_instances", "savings_plan_price","REAL"),
    ("mc_cloud_instances", "interruption_rate", "TEXT DEFAULT ''"),
    # Advanced protocol planning variants
    ("mc_net_protocol_plans", "variant",        "TEXT DEFAULT 'standard'"),
    ("mc_net_protocol_plans", "advanced_config","TEXT DEFAULT '{}'"),
]


def _migrate_gap_fill_tables(conn):
    """Idempotently add gap-fill tables and columns (migration 084)."""
    conn.executescript(GAP_FILL_SCHEMA)
    existing_cols: dict[str, list[str]] = {}
    for tbl, col, typedef in _GAP_FILL_ALTER:
        if tbl not in existing_cols:
            existing_cols[tbl] = [r[1] for r in conn.execute(f"PRAGMA table_info({tbl})").fetchall()]
        if col not in existing_cols[tbl]:
            try:
                conn.execute(f"ALTER TABLE {tbl} ADD COLUMN {col} {typedef}")
                existing_cols[tbl].append(col)
            except Exception:
                pass


# ── Cloud Instance Seed Data ─────────────────────────────────────────────────

_CLOUD_INSTANCE_SEED = [
    # ── AWS EC2 ──────────────────────────────────────────────────────────────
    # t3 — burstable general purpose
    ("aws","t3.nano","t3",2,0.5,0,"EBS-only",5,0,"low",1,'["2","4","5"]','["burstable","general"]'),
    ("aws","t3.micro","t3",2,1,0,"EBS-only",5,0,"low",1,'["2","4","5"]','["burstable","general"]'),
    ("aws","t3.small","t3",2,2,0,"EBS-only",5,0,"low",1,'["2","4","5"]','["burstable","general"]'),
    ("aws","t3.medium","t3",2,4,0,"EBS-only",5,1,"low",1,'["2","4","5"]','["burstable","general"]'),
    ("aws","t3.large","t3",2,8,0,"EBS-only",5,1,"low",1,'["2","4","5"]','["burstable","general"]'),
    ("aws","t3.xlarge","t3",4,16,0,"EBS-only",5,1,"medium",1,'["2","4","5"]','["burstable","general"]'),
    ("aws","t3.2xlarge","t3",8,32,0,"EBS-only",5,1,"medium",1,'["2","4","5"]','["burstable","general"]'),
    # m6i — general purpose
    ("aws","m6i.large","m6i",2,8,0,"EBS-only",12.5,1,"medium",1,'["2","4","5"]','["general"]'),
    ("aws","m6i.xlarge","m6i",4,16,0,"EBS-only",12.5,1,"medium",1,'["2","4","5"]','["general"]'),
    ("aws","m6i.2xlarge","m6i",8,32,0,"EBS-only",12.5,1,"medium",1,'["2","4","5"]','["general"]'),
    ("aws","m6i.4xlarge","m6i",16,64,0,"EBS-only",12.5,1,"medium",1,'["2","4","5"]','["general"]'),
    ("aws","m6i.8xlarge","m6i",32,128,0,"EBS-only",12.5,1,"high",1,'["2","4","5"]','["general"]'),
    ("aws","m6i.16xlarge","m6i",64,256,0,"EBS-only",25,1,"high",1,'["2","4","5"]','["general"]'),
    # c6i — compute optimized
    ("aws","c6i.large","c6i",2,4,0,"EBS-only",12.5,1,"medium",1,'["2","4","5"]','["compute"]'),
    ("aws","c6i.xlarge","c6i",4,8,0,"EBS-only",12.5,1,"medium",1,'["2","4","5"]','["compute"]'),
    ("aws","c6i.2xlarge","c6i",8,16,0,"EBS-only",12.5,1,"medium",1,'["2","4","5"]','["compute"]'),
    ("aws","c6i.4xlarge","c6i",16,32,0,"EBS-only",12.5,1,"medium",1,'["2","4","5"]','["compute"]'),
    ("aws","c6i.8xlarge","c6i",32,64,0,"EBS-only",12.5,1,"high",1,'["2","4","5"]','["compute"]'),
    ("aws","c6i.16xlarge","c6i",64,128,0,"EBS-only",25,1,"high",1,'["2","4","5"]','["compute"]'),
    # r6i — memory optimized
    ("aws","r6i.large","r6i",2,16,0,"EBS-only",12.5,1,"medium",1,'["2","4","5"]','["memory"]'),
    ("aws","r6i.xlarge","r6i",4,32,0,"EBS-only",12.5,1,"medium",1,'["2","4","5"]','["memory"]'),
    ("aws","r6i.2xlarge","r6i",8,64,0,"EBS-only",12.5,1,"medium",1,'["2","4","5"]','["memory"]'),
    ("aws","r6i.4xlarge","r6i",16,128,0,"EBS-only",12.5,1,"high",1,'["2","4","5"]','["memory"]'),
    ("aws","r6i.8xlarge","r6i",32,256,0,"EBS-only",12.5,1,"high",1,'["2","4","5"]','["memory"]'),
    ("aws","r6i.16xlarge","r6i",64,512,0,"EBS-only",25,1,"very_high",1,'["2","4","5"]','["memory"]'),
    # i3 — storage optimized NVMe
    ("aws","i3.large","i3",2,15.25,475,"NVMe",10,1,"medium",1,'["2","4","5"]','["storage"]'),
    ("aws","i3.xlarge","i3",4,30.5,950,"NVMe",10,1,"medium",1,'["2","4","5"]','["storage"]'),
    ("aws","i3.2xlarge","i3",8,61,1900,"NVMe",10,1,"medium",1,'["2","4","5"]','["storage"]'),
    ("aws","i3.4xlarge","i3",16,122,3800,"NVMe",10,1,"high",1,'["2","4","5"]','["storage"]'),
    ("aws","i3.8xlarge","i3",32,244,6400,"NVMe",10,1,"high",1,'["2","4","5"]','["storage"]'),
    ("aws","i3.16xlarge","i3",64,488,12800,"NVMe",25,1,"very_high",1,'["2","4","5"]','["storage"]'),
    # p3 — GPU
    ("aws","p3.2xlarge","p3",8,61,0,"EBS-only",10,1,"very_high",0,'["2"]','["gpu"]'),
    ("aws","p3.8xlarge","p3",32,244,0,"EBS-only",10,1,"very_high",0,'["2"]','["gpu"]'),
    # ── Azure VMs ─────────────────────────────────────────────────────────────
    # B — burstable
    ("azure","Standard_B2s","B",2,4,0,"SSD",0.8,1,"low",1,'["2","4","5"]','["burstable","general"]'),
    ("azure","Standard_B2ms","B",2,8,0,"SSD",0.8,1,"low",1,'["2","4","5"]','["burstable","general"]'),
    ("azure","Standard_B4ms","B",4,16,0,"SSD",0.8,1,"low",1,'["2","4","5"]','["burstable","general"]'),
    ("azure","Standard_B8ms","B",8,32,0,"SSD",0.8,1,"medium",1,'["2","4","5"]','["burstable","general"]'),
    # D_v5 — general purpose
    ("azure","Standard_D2s_v5","D_v5",2,8,0,"SSD",12.5,1,"medium",1,'["2","4","5"]','["general"]'),
    ("azure","Standard_D4s_v5","D_v5",4,16,0,"SSD",12.5,1,"medium",1,'["2","4","5"]','["general"]'),
    ("azure","Standard_D8s_v5","D_v5",8,32,0,"SSD",12.5,1,"medium",1,'["2","4","5"]','["general"]'),
    ("azure","Standard_D16s_v5","D_v5",16,64,0,"SSD",12.5,1,"high",1,'["2","4","5"]','["general"]'),
    ("azure","Standard_D32s_v5","D_v5",32,128,0,"SSD",16,1,"high",1,'["2","4","5"]','["general"]'),
    # F_v2 — compute optimized
    ("azure","Standard_F2s_v2","F_v2",2,4,0,"SSD",5,1,"medium",1,'["2","4","5"]','["compute"]'),
    ("azure","Standard_F4s_v2","F_v2",4,8,0,"SSD",5,1,"medium",1,'["2","4","5"]','["compute"]'),
    ("azure","Standard_F8s_v2","F_v2",8,16,0,"SSD",5,1,"medium",1,'["2","4","5"]','["compute"]'),
    ("azure","Standard_F16s_v2","F_v2",16,32,0,"SSD",5,1,"high",1,'["2","4","5"]','["compute"]'),
    ("azure","Standard_F32s_v2","F_v2",32,64,0,"SSD",16,1,"high",1,'["2","4","5"]','["compute"]'),
    # E_v5 — memory optimized
    ("azure","Standard_E2s_v5","E_v5",2,16,0,"SSD",3,1,"medium",1,'["2","4","5"]','["memory"]'),
    ("azure","Standard_E4s_v5","E_v5",4,32,0,"SSD",6.25,1,"medium",1,'["2","4","5"]','["memory"]'),
    ("azure","Standard_E8s_v5","E_v5",8,64,0,"SSD",6.25,1,"medium",1,'["2","4","5"]','["memory"]'),
    ("azure","Standard_E16s_v5","E_v5",16,128,0,"SSD",6.25,1,"high",1,'["2","4","5"]','["memory"]'),
    ("azure","Standard_E32s_v5","E_v5",32,256,0,"SSD",16,1,"high",1,'["2","4","5"]','["memory"]'),
    # L_v3 — storage optimized NVMe
    ("azure","Standard_L8s_v3","L_v3",8,64,1920,"NVMe",12.5,1,"high",1,'["2","4","5"]','["storage"]'),
    ("azure","Standard_L16s_v3","L_v3",16,128,3840,"NVMe",12.5,1,"high",1,'["2","4","5"]','["storage"]'),
    ("azure","Standard_L32s_v3","L_v3",32,256,7680,"NVMe",16,1,"very_high",1,'["2","4","5"]','["storage"]'),
    ("azure","Standard_L64s_v3","L_v3",64,512,15360,"NVMe",16,1,"very_high",1,'["2","4","5"]','["storage"]'),
    # NC_v3 — GPU
    ("azure","Standard_NC6s_v3","NC_v3",6,112,736,"SSD",24,1,"very_high",0,'["2"]','["gpu"]'),
    ("azure","Standard_NC12s_v3","NC_v3",12,224,1474,"SSD",24,1,"very_high",0,'["2"]','["gpu"]'),
    # ── GCP Compute ───────────────────────────────────────────────────────────
    # e2 — general purpose
    ("gcp","e2-micro","e2",2,1,0,"SSD",1,0,"low",0,'["2"]','["burstable","general"]'),
    ("gcp","e2-small","e2",2,2,0,"SSD",1,0,"low",0,'["2"]','["burstable","general"]'),
    ("gcp","e2-medium","e2",2,4,0,"SSD",2,0,"low",0,'["2"]','["burstable","general"]'),
    ("gcp","e2-standard-4","e2",4,16,0,"SSD",10,0,"medium",0,'["2"]','["general"]'),
    ("gcp","e2-standard-8","e2",8,32,0,"SSD",16,0,"medium",0,'["2"]','["general"]'),
    # n2 — general purpose (newer)
    ("gcp","n2-standard-2","n2",2,8,0,"SSD",10,0,"medium",0,'["2"]','["general"]'),
    ("gcp","n2-standard-4","n2",4,16,0,"SSD",10,0,"medium",0,'["2"]','["general"]'),
    ("gcp","n2-standard-8","n2",8,32,0,"SSD",16,0,"medium",0,'["2"]','["general"]'),
    ("gcp","n2-standard-16","n2",16,64,0,"SSD",32,0,"high",0,'["2"]','["general"]'),
    ("gcp","n2-standard-32","n2",32,128,0,"SSD",32,0,"high",0,'["2"]','["general"]'),
    # c2 — compute optimized
    ("gcp","c2-standard-4","c2",4,16,0,"SSD",10,0,"medium",0,'["2"]','["compute"]'),
    ("gcp","c2-standard-8","c2",8,32,0,"SSD",16,0,"medium",0,'["2"]','["compute"]'),
    ("gcp","c2-standard-16","c2",16,64,0,"SSD",32,0,"high",0,'["2"]','["compute"]'),
    ("gcp","c2-standard-30","c2",30,120,0,"SSD",32,0,"high",0,'["2"]','["compute"]'),
    # m1 — memory optimized
    ("gcp","m1-megamem-96","m1",96,1433.6,0,"SSD",32,0,"very_high",0,'["2"]','["memory"]'),
    ("gcp","m1-ultramem-40","m1",40,961,0,"SSD",32,0,"very_high",0,'["2"]','["memory"]'),
    # a2 — GPU
    ("gcp","a2-highgpu-1g","a2",12,85,0,"SSD",24,0,"very_high",0,'["2"]','["gpu"]'),
    ("gcp","a2-highgpu-2g","a2",24,170,0,"SSD",24,0,"very_high",0,'["2"]','["gpu"]'),
    # ── OCI Compute ───────────────────────────────────────────────────────────
    ("oci","VM.Standard.E4.Flex.1","VM.Standard.E4.Flex",1,8,0,"Block",1,0,"low",1,'["2","4","5"]','["general"]'),
    ("oci","VM.Standard.E4.Flex.2","VM.Standard.E4.Flex",2,16,0,"Block",2,0,"low",1,'["2","4","5"]','["general"]'),
    ("oci","VM.Standard.E4.Flex.4","VM.Standard.E4.Flex",4,32,0,"Block",4,0,"medium",1,'["2","4","5"]','["general"]'),
    ("oci","VM.Standard.E4.Flex.8","VM.Standard.E4.Flex",8,64,0,"Block",8,0,"medium",1,'["2","4","5"]','["general"]'),
    ("oci","VM.Standard.E4.Flex.16","VM.Standard.E4.Flex",16,128,0,"Block",16,0,"high",1,'["2","4","5"]','["general"]'),
    ("oci","VM.Standard3.Flex.2","VM.Standard3.Flex",2,16,0,"Block",2,0,"low",1,'["2","4","5"]','["general"]'),
    ("oci","VM.Standard3.Flex.4","VM.Standard3.Flex",4,32,0,"Block",4,0,"medium",1,'["2","4","5"]','["general"]'),
    ("oci","VM.Standard3.Flex.8","VM.Standard3.Flex",8,64,0,"Block",8,0,"medium",1,'["2","4","5"]','["general"]'),
    ("oci","BM.Standard.E4.128","BM.Standard.E4",128,2048,0,"NVMe",64,0,"very_high",1,'["2","4","5"]','["memory","compute"]'),
    ("oci","VM.GPU3.1","VM.GPU3",6,90,0,"Block",16,0,"very_high",0,'["2"]','["gpu"]'),
    # ── IBM Cloud ─────────────────────────────────────────────────────────────
    ("ibm","bx2-2x8","bx2",2,8,0,"Block",4,0,"low",1,'["2","4"]','["general"]'),
    ("ibm","bx2-4x16","bx2",4,16,0,"Block",8,0,"low",1,'["2","4"]','["general"]'),
    ("ibm","bx2-8x32","bx2",8,32,0,"Block",16,0,"medium",1,'["2","4"]','["general"]'),
    ("ibm","mx2-8x64","mx2",8,64,0,"Block",16,0,"medium",1,'["2","4"]','["memory"]'),
    ("ibm","mx2-16x128","mx2",16,128,0,"Block",16,0,"high",1,'["2","4"]','["memory"]'),
    ("ibm","cx2-4x8","cx2",4,8,0,"Block",8,0,"medium",1,'["2","4"]','["compute"]'),
    ("ibm","cx2-8x16","cx2",8,16,0,"Block",16,0,"medium",1,'["2","4"]','["compute"]'),
    ("ibm","cx2-16x32","cx2",16,32,0,"Block",16,0,"high",1,'["2","4"]','["compute"]'),
    ("ibm","ox2-16x128","ox2",16,128,0,"Block",16,0,"high",1,'["2","4"]','["memory","compute"]'),
    ("ibm","gx2-8x64x1v100","gx2",8,64,0,"Block",16,0,"very_high",0,'["2"]','["gpu"]'),
    # ── On-prem hypervisor templates ──────────────────────────────────────────
]

# On-prem hypervisor standard templates (7 sizes × 5 providers)
_ONPREM_PROVIDERS = ["vmware", "hyperv", "kvm", "nutanix", "proxmox"]
_ONPREM_SIZES = [
    ("xs",  1,  1,  0, "SSD", 1),
    ("sm",  2,  4,  0, "SSD", 1),
    ("md",  4,  8,  0, "SSD", 2),
    ("lg",  8,  16, 0, "SSD", 4),
    ("xl",  16, 32, 0, "SSD", 10),
    ("2xl", 32, 64, 0, "SSD", 10),
    ("4xl", 64, 128,0, "SSD", 25),
]
_ONPREM_LABELS = {"vmware": "VMware vSphere", "hyperv": "Hyper-V", "kvm": "KVM", "nutanix": "Nutanix AHV", "proxmox": "Proxmox VE"}

for _prov in _ONPREM_PROVIDERS:
    for _sz, _vcpu, _ram, _disk, _st, _net in _ONPREM_SIZES:
        _CLOUD_INSTANCE_SEED.append((
            _prov, f"{_prov}_{_sz}", _prov, _vcpu, _ram, _disk, _st, _net, 0,
            "low", 0, '[]', '["general"]',
        ))


def _migrate_app_tables(conn):
    """Idempotently add application migration tables to existing DBs."""
    conn.executescript(APP_MIGRATION_SCHEMA)


def _seed_cloud_instances(conn):
    """Seed mc_cloud_instances with ~150 pre-built rows (air-gap baseline).

    Uses INSERT OR IGNORE so existing rows (source='api') are never overwritten.
    """
    sql = (
        "INSERT OR IGNORE INTO mc_cloud_instances "
        "(provider, instance_type, family, vcpus, ram_gb, local_storage_gb, "
        "storage_type, network_gbps, premium_disk_opt, cost_tier, govcloud, "
        "il_support, use_case_tags, source) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,'seed')"
    )
    for row in _CLOUD_INSTANCE_SEED:
        try:
            conn.execute(sql, row)
        except Exception:
            pass
    conn.commit()


# ── Seed Templates ──────────────────────────────────────────────────────────


def _tpl_lift_and_shift():
    """Template: Lift-and-Shift Cloud Migration."""
    return {
        "nodes": [
            {"id": "src-dc-1", "type": "src-datacenter", "label": "On-Prem Data Center", "x": 80, "y": 80},
            {"id": "src-app-1", "type": "src-monolith", "label": "Legacy Application", "x": 80, "y": 200},
            {"id": "src-db-1", "type": "src-database", "label": "Oracle Database", "x": 80, "y": 320},
            {"id": "pat-rehost", "type": "pat-rehost", "label": "Rehost (Lift & Shift)", "x": 380, "y": 200},
            {"id": "tgt-cloud-1", "type": "tgt-govcloud", "label": "AWS GovCloud", "x": 680, "y": 80},
            {"id": "tgt-ec2-1", "type": "tgt-vm", "label": "EC2 Instance", "x": 680, "y": 200},
            {"id": "tgt-rds-1", "type": "tgt-managed-db", "label": "RDS PostgreSQL", "x": 680, "y": 320},
            {"id": "ctl-ato-1", "type": "ctl-ato-gate", "label": "ATO Compliance Gate", "x": 380, "y": 400},
        ],
        "edges": [
            {"id": "e1", "source": "src-app-1", "target": "pat-rehost"},
            {"id": "e2", "source": "src-db-1", "target": "pat-rehost"},
            {"id": "e3", "source": "pat-rehost", "target": "tgt-ec2-1"},
            {"id": "e4", "source": "pat-rehost", "target": "tgt-rds-1"},
            {"id": "e5", "source": "tgt-ec2-1", "target": "tgt-cloud-1"},
            {"id": "e6", "source": "tgt-rds-1", "target": "tgt-cloud-1"},
            {"id": "e7", "source": "ctl-ato-1", "target": "pat-rehost"},
        ],
    }


def _tpl_strangler_fig():
    """Template: Strangler Fig Incremental Migration."""
    return {
        "nodes": [
            {"id": "src-mono-1", "type": "src-monolith", "label": "Legacy Monolith", "x": 80, "y": 150},
            {"id": "src-db-1", "type": "src-database", "label": "Shared Database", "x": 80, "y": 300},
            {"id": "pat-strangler", "type": "pat-strangler", "label": "Strangler Fig Pattern", "x": 350, "y": 80},
            {"id": "mid-proxy-1", "type": "mid-proxy", "label": "API Gateway / Proxy", "x": 350, "y": 220},
            {"id": "tgt-svc-1", "type": "tgt-microservice", "label": "Service A (Extracted)", "x": 620, "y": 120},
            {"id": "tgt-svc-2", "type": "tgt-microservice", "label": "Service B (Extracted)", "x": 620, "y": 250},
            {"id": "tgt-db-1", "type": "tgt-managed-db", "label": "Service A DB", "x": 620, "y": 380},
            {"id": "ctl-bridge-1", "type": "ctl-compliance-bridge", "label": "Compliance Bridge", "x": 350, "y": 400},
        ],
        "edges": [
            {"id": "e1", "source": "src-mono-1", "target": "mid-proxy-1"},
            {"id": "e2", "source": "mid-proxy-1", "target": "tgt-svc-1"},
            {"id": "e3", "source": "mid-proxy-1", "target": "tgt-svc-2"},
            {"id": "e4", "source": "tgt-svc-1", "target": "tgt-db-1"},
            {"id": "e5", "source": "src-mono-1", "target": "src-db-1"},
            {"id": "e6", "source": "ctl-bridge-1", "target": "mid-proxy-1"},
        ],
    }


def _tpl_datacenter_consolidation():
    """Template: Data Center Consolidation (DCOI)."""
    return {
        "nodes": [
            {"id": "src-dc-1", "type": "src-datacenter", "label": "Data Center East", "x": 80, "y": 80},
            {"id": "src-dc-2", "type": "src-datacenter", "label": "Data Center West", "x": 80, "y": 250},
            {"id": "src-app-1", "type": "src-legacy", "label": "App Portfolio (120 apps)", "x": 80, "y": 420},
            {"id": "wave-1", "type": "wave-group", "label": "Wave 1: Low-Risk Rehost", "x": 380, "y": 80},
            {"id": "wave-2", "type": "wave-group", "label": "Wave 2: Replatform", "x": 380, "y": 250},
            {"id": "wave-3", "type": "wave-group", "label": "Wave 3: Refactor", "x": 380, "y": 420},
            {"id": "tgt-cloud-1", "type": "tgt-govcloud", "label": "AWS GovCloud Target", "x": 680, "y": 150},
            {"id": "ctl-dcoi", "type": "ctl-compliance-gate", "label": "DCOI Compliance Gate", "x": 680, "y": 350},
        ],
        "edges": [
            {"id": "e1", "source": "src-dc-1", "target": "wave-1"},
            {"id": "e2", "source": "src-dc-2", "target": "wave-2"},
            {"id": "e3", "source": "src-app-1", "target": "wave-3"},
            {"id": "e4", "source": "wave-1", "target": "tgt-cloud-1"},
            {"id": "e5", "source": "wave-2", "target": "tgt-cloud-1"},
            {"id": "e6", "source": "wave-3", "target": "tgt-cloud-1"},
            {"id": "e7", "source": "ctl-dcoi", "target": "tgt-cloud-1"},
        ],
    }


def _tpl_replatform_containers():
    """Template: Replatform to Containers (VM -> K8s)."""
    return {
        "nodes": [
            {"id": "src-app-1", "type": "src-legacy", "label": "Legacy VM Application", "x": 80, "y": 120},
            {"id": "src-db-1", "type": "src-database", "label": "MySQL 5.7", "x": 80, "y": 280},
            {"id": "pat-replatform", "type": "pat-replatform", "label": "Replatform", "x": 350, "y": 120},
            {"id": "mid-etl-1", "type": "mid-etl", "label": "DB Migration Script", "x": 350, "y": 280},
            {"id": "tgt-k8s", "type": "tgt-container", "label": "EKS / AKS Cluster", "x": 620, "y": 120},
            {"id": "tgt-rds", "type": "tgt-managed-db", "label": "Aurora PostgreSQL", "x": 620, "y": 280},
            {"id": "ctl-scan", "type": "ctl-security-scan", "label": "Container Scan", "x": 350, "y": 400},
            {"id": "ctl-test", "type": "ctl-test-gate", "label": "Post-Migration Tests", "x": 620, "y": 400},
        ],
        "edges": [
            {"id": "e1", "source": "src-app-1", "target": "pat-replatform"},
            {"id": "e2", "source": "src-db-1", "target": "mid-etl-1"},
            {"id": "e3", "source": "pat-replatform", "target": "tgt-k8s"},
            {"id": "e4", "source": "mid-etl-1", "target": "tgt-rds"},
            {"id": "e5", "source": "ctl-scan", "target": "tgt-k8s"},
            {"id": "e6", "source": "ctl-test", "target": "tgt-k8s"},
        ],
    }


def _tpl_dod_il4_migration():
    """Template: DoD IL4 Migration with SCCA compliance."""
    return {
        "nodes": [
            {"id": "src-dc", "type": "src-datacenter", "label": "NIPR Data Center", "x": 80, "y": 80},
            {"id": "src-app", "type": "src-legacy", "label": "Mission System", "x": 80, "y": 220},
            {"id": "src-db", "type": "src-database", "label": "Oracle RAC", "x": 80, "y": 360},
            {"id": "pat-replatform", "type": "pat-replatform", "label": "Replatform (IL4)", "x": 350, "y": 150},
            {"id": "ctl-ato", "type": "ctl-ato-gate", "label": "ATO Gate (IL4)", "x": 350, "y": 300},
            {"id": "ctl-bridge", "type": "ctl-compliance-bridge", "label": "NIST Control Bridge", "x": 350, "y": 420},
            {"id": "tgt-gov", "type": "tgt-govcloud", "label": "AWS GovCloud (IL4)", "x": 650, "y": 80},
            {"id": "tgt-eks", "type": "tgt-container", "label": "EKS + Big Bang", "x": 650, "y": 220},
            {"id": "tgt-rds", "type": "tgt-managed-db", "label": "RDS PostgreSQL", "x": 650, "y": 360},
            {"id": "ctl-rollback", "type": "ctl-rollback", "label": "Rollback Point", "x": 500, "y": 420},
        ],
        "edges": [
            {"id": "e1", "source": "src-app", "target": "pat-replatform"},
            {"id": "e2", "source": "src-db", "target": "pat-replatform"},
            {"id": "e3", "source": "pat-replatform", "target": "tgt-eks"},
            {"id": "e4", "source": "pat-replatform", "target": "tgt-rds"},
            {"id": "e5", "source": "ctl-ato", "target": "pat-replatform"},
            {"id": "e6", "source": "ctl-bridge", "target": "ctl-ato"},
            {"id": "e7", "source": "tgt-eks", "target": "tgt-gov"},
        ],
    }


def _tpl_cross_domain():
    """Template: Cross-Domain Migration (Commercial -> GovCloud)."""
    return {
        "nodes": [
            {"id": "src-comm", "type": "tgt-cloud", "label": "AWS Commercial", "x": 80, "y": 100},
            {"id": "src-app", "type": "src-legacy", "label": "Application Stack", "x": 80, "y": 250},
            {"id": "mid-sync", "type": "mid-sync", "label": "Cross-Domain Sync", "x": 350, "y": 180},
            {"id": "ctl-scan", "type": "ctl-security-scan", "label": "CUI Classification Scan", "x": 350, "y": 330},
            {"id": "ctl-ato", "type": "ctl-ato-gate", "label": "FedRAMP High Gate", "x": 500, "y": 330},
            {"id": "tgt-gov", "type": "tgt-govcloud", "label": "AWS GovCloud (IL5)", "x": 650, "y": 100},
            {"id": "tgt-app", "type": "tgt-container", "label": "EKS GovCloud", "x": 650, "y": 250},
        ],
        "edges": [
            {"id": "e1", "source": "src-app", "target": "mid-sync"},
            {"id": "e2", "source": "mid-sync", "target": "tgt-app"},
            {"id": "e3", "source": "ctl-scan", "target": "mid-sync"},
            {"id": "e4", "source": "ctl-ato", "target": "tgt-app"},
            {"id": "e5", "source": "tgt-app", "target": "tgt-gov"},
        ],
    }


def _tpl_blue_green():
    """Template: Blue-Green Zero-Downtime Migration."""
    return {
        "nodes": [
            {"id": "src-blue", "type": "src-legacy", "label": "Blue (Current Prod)", "x": 80, "y": 150},
            {"id": "src-db", "type": "src-database", "label": "Production DB", "x": 80, "y": 320},
            {"id": "mid-proxy", "type": "mid-proxy", "label": "Traffic Router / LB", "x": 350, "y": 80},
            {"id": "mid-sync", "type": "mid-sync", "label": "Real-Time DB Sync", "x": 350, "y": 320},
            {"id": "tgt-green", "type": "tgt-container", "label": "Green (New Env)", "x": 620, "y": 150},
            {"id": "tgt-db", "type": "tgt-managed-db", "label": "Green DB Replica", "x": 620, "y": 320},
            {"id": "ctl-test", "type": "ctl-test-gate", "label": "Smoke Test Gate", "x": 500, "y": 420},
            {"id": "ctl-rollback", "type": "ctl-rollback", "label": "Instant Rollback", "x": 200, "y": 420},
        ],
        "edges": [
            {"id": "e1", "source": "mid-proxy", "target": "src-blue"},
            {"id": "e2", "source": "mid-proxy", "target": "tgt-green"},
            {"id": "e3", "source": "src-db", "target": "mid-sync"},
            {"id": "e4", "source": "mid-sync", "target": "tgt-db"},
            {"id": "e5", "source": "ctl-test", "target": "tgt-green"},
            {"id": "e6", "source": "ctl-rollback", "target": "mid-proxy"},
        ],
    }


def _tpl_data_migration():
    """Template: Enterprise Data Migration (DB + ETL + Validation)."""
    return {
        "nodes": [
            {"id": "src-oracle", "type": "src-database", "label": "Oracle 12c (On-Prem)", "x": 80, "y": 100},
            {"id": "src-mssql", "type": "src-database", "label": "SQL Server 2016", "x": 80, "y": 260},
            {"id": "mid-etl", "type": "mid-etl", "label": "ETL Pipeline (DMS)", "x": 350, "y": 100},
            {"id": "mid-sync", "type": "mid-sync", "label": "CDC Replication", "x": 350, "y": 260},
            {"id": "tgt-aurora", "type": "tgt-managed-db", "label": "Aurora PostgreSQL", "x": 620, "y": 100},
            {"id": "tgt-redshift", "type": "tgt-managed-db", "label": "Redshift (Analytics)", "x": 620, "y": 260},
            {"id": "ctl-test", "type": "ctl-test-gate", "label": "Data Validation Gate", "x": 500, "y": 380},
            {"id": "ctl-scan", "type": "ctl-security-scan", "label": "PII/CUI Scan", "x": 200, "y": 380},
        ],
        "edges": [
            {"id": "e1", "source": "src-oracle", "target": "mid-etl"},
            {"id": "e2", "source": "src-mssql", "target": "mid-sync"},
            {"id": "e3", "source": "mid-etl", "target": "tgt-aurora"},
            {"id": "e4", "source": "mid-sync", "target": "tgt-redshift"},
            {"id": "e5", "source": "ctl-scan", "target": "mid-etl"},
            {"id": "e6", "source": "ctl-test", "target": "tgt-aurora"},
        ],
    }


def _tpl_network_migration():
    """Template: Network Infrastructure Migration (On-Prem -> Cloud VPC)."""
    return {
        "nodes": [
            {"id": "src-net", "type": "src-network", "label": "On-Prem Network (MPLS)", "x": 80, "y": 100},
            {"id": "src-fw", "type": "src-middleware", "label": "Legacy Firewalls", "x": 80, "y": 260},
            {"id": "src-dc", "type": "src-datacenter", "label": "Colo Facility", "x": 80, "y": 400},
            {"id": "pat-replatform", "type": "pat-replatform", "label": "Replatform Network", "x": 350, "y": 180},
            {"id": "mid-proxy", "type": "mid-proxy", "label": "Direct Connect / ExpressRoute", "x": 350, "y": 340},
            {"id": "tgt-vpc", "type": "tgt-govcloud", "label": "Transit Gateway + VPCs", "x": 620, "y": 100},
            {"id": "tgt-sase", "type": "tgt-saas", "label": "SASE/SSE (Zscaler/Palo)", "x": 620, "y": 260},
            {"id": "ctl-compliance", "type": "ctl-compliance-gate", "label": "SCCA/TIC 3.0 Gate", "x": 620, "y": 400},
        ],
        "edges": [
            {"id": "e1", "source": "src-net", "target": "pat-replatform"},
            {"id": "e2", "source": "src-fw", "target": "pat-replatform"},
            {"id": "e3", "source": "pat-replatform", "target": "tgt-vpc"},
            {"id": "e4", "source": "pat-replatform", "target": "tgt-sase"},
            {"id": "e5", "source": "mid-proxy", "target": "tgt-vpc"},
            {"id": "e6", "source": "ctl-compliance", "target": "tgt-vpc"},
        ],
    }


def _tpl_multi_wave():
    """Template: Multi-Phase Staged Migration (5-Wave)."""
    return {
        "nodes": [
            {"id": "src-portfolio", "type": "src-legacy", "label": "App Portfolio (200 apps)", "x": 80, "y": 220},
            {"id": "wave-1", "type": "wave-group", "label": "Wave 1: Retire (40 apps)", "x": 350, "y": 40},
            {"id": "wave-2", "type": "wave-group", "label": "Wave 2: Rehost (60 apps)", "x": 350, "y": 140},
            {"id": "wave-3", "type": "wave-group", "label": "Wave 3: Replatform (50 apps)", "x": 350, "y": 240},
            {"id": "wave-4", "type": "wave-group", "label": "Wave 4: Refactor (30 apps)", "x": 350, "y": 340},
            {"id": "wave-5", "type": "wave-group", "label": "Wave 5: Repurchase (20 apps)", "x": 350, "y": 440},
            {"id": "tgt-cloud", "type": "tgt-govcloud", "label": "Target Cloud", "x": 650, "y": 180},
            {"id": "plan-milestone", "type": "plan-milestone", "label": "Migration Complete", "x": 650, "y": 350},
            {"id": "ctl-gate", "type": "ctl-compliance-gate", "label": "Wave Gate Review", "x": 500, "y": 500},
        ],
        "edges": [
            {"id": "e1", "source": "src-portfolio", "target": "wave-1"},
            {"id": "e2", "source": "src-portfolio", "target": "wave-2"},
            {"id": "e3", "source": "src-portfolio", "target": "wave-3"},
            {"id": "e4", "source": "src-portfolio", "target": "wave-4"},
            {"id": "e5", "source": "src-portfolio", "target": "wave-5"},
            {"id": "e6", "source": "wave-2", "target": "tgt-cloud"},
            {"id": "e7", "source": "wave-3", "target": "tgt-cloud"},
            {"id": "e8", "source": "wave-4", "target": "tgt-cloud"},
            {"id": "e9", "source": "ctl-gate", "target": "plan-milestone"},
        ],
    }


SEED_TEMPLATES = [
    {"id": "tpl-lift-shift", "name": "Lift-and-Shift Cloud Migration", "category": "cloud",
     "description": "Rehost on-prem workloads to cloud VMs with minimal changes. Fastest path but may accumulate tech debt.",
     "graph_json": json.dumps(_tpl_lift_and_shift()), "tags": json.dumps(["rehost", "cloud", "govcloud", "quick-win"])},
    {"id": "tpl-strangler-fig", "name": "Strangler Fig Incremental Migration", "category": "application",
     "description": "Gradually extract services from a monolith behind an API gateway. Safe, reversible, compliance-friendly.",
     "graph_json": json.dumps(_tpl_strangler_fig()), "tags": json.dumps(["strangler-fig", "microservices", "incremental"])},
    {"id": "tpl-dc-consolidation", "name": "Data Center Consolidation (DCOI)", "category": "infrastructure",
     "description": "Consolidate multiple data centers into cloud with wave-based migration and DCOI compliance gates.",
     "graph_json": json.dumps(_tpl_datacenter_consolidation()), "tags": json.dumps(["dcoi", "data-center", "consolidation"])},
    {"id": "tpl-replatform-k8s", "name": "Replatform to Containers (K8s)", "category": "application",
     "description": "Migrate VM-based applications to Kubernetes with managed DB. Includes container security scanning.",
     "graph_json": json.dumps(_tpl_replatform_containers()), "tags": json.dumps(["replatform", "containers", "kubernetes", "eks"])},
    {"id": "tpl-dod-il4", "name": "DoD IL4 Mission System Migration", "category": "cloud",
     "description": "Migrate DoD mission systems to AWS GovCloud IL4 with SCCA, Big Bang, NIST control bridge, and ATO gates.",
     "graph_json": json.dumps(_tpl_dod_il4_migration()), "tags": json.dumps(["dod", "il4", "govcloud", "scca", "big-bang"])},
    {"id": "tpl-cross-domain", "name": "Cross-Domain Migration (Commercial to GovCloud)", "category": "cloud",
     "description": "Migrate workloads from commercial AWS to GovCloud IL5 with CUI classification scanning and FedRAMP gates.",
     "graph_json": json.dumps(_tpl_cross_domain()), "tags": json.dumps(["cross-domain", "govcloud", "il5", "fedramp"])},
    {"id": "tpl-blue-green", "name": "Blue-Green Zero-Downtime Migration", "category": "application",
     "description": "Zero-downtime cutover using traffic routing between blue (old) and green (new) environments with instant rollback.",
     "graph_json": json.dumps(_tpl_blue_green()), "tags": json.dumps(["blue-green", "zero-downtime", "rollback", "canary"])},
    {"id": "tpl-data-migration", "name": "Enterprise Data Migration (Multi-DB)", "category": "data",
     "description": "Migrate Oracle/SQL Server databases to cloud-managed PostgreSQL/Redshift with CDC replication and PII scanning.",
     "graph_json": json.dumps(_tpl_data_migration()), "tags": json.dumps(["data", "database", "etl", "cdc", "oracle", "postgresql"])},
    {"id": "tpl-network-migration", "name": "Network Infrastructure Migration", "category": "network",
     "description": "Migrate on-prem MPLS network to cloud VPC/Transit Gateway with SASE and SCCA/TIC 3.0 compliance.",
     "graph_json": json.dumps(_tpl_network_migration()), "tags": json.dumps(["network", "mpls", "vpc", "sase", "tic3"])},
    {"id": "tpl-multi-wave", "name": "Multi-Phase Staged Migration (5-Wave)", "category": "planning",
     "description": "7Rs portfolio rationalization across 5 waves: Retire, Rehost, Replatform, Refactor, Repurchase with gate reviews.",
     "graph_json": json.dumps(_tpl_multi_wave()), "tags": json.dumps(["multi-wave", "portfolio", "7rs", "staged", "planning"])},
]


# ── Seed Snippets (cross-canvas integration) ────────────────────────────────

def _snip(node_type, label, desc_override=None):
    """Helper: single-node snippet."""
    return json.dumps({"nodes": [{"id": node_type, "type": node_type, "label": label, "x": 100, "y": 100}], "edges": []})

SEED_SNIPPETS = [
    # Controls & Gates
    {"id": "snip-ato-gate", "name": "ATO Compliance Gate", "category": "controls",
     "description": "Pre-built ATO validation checkpoint. Links to Boundary Canvas ATO boundary designs.",
     "graph_json": _snip("ctl-ato-gate", "ATO Gate"), "tags": json.dumps(["ato", "compliance", "boundary-canvas"])},
    {"id": "snip-compliance-bridge", "name": "NIST Control Bridge", "category": "controls",
     "description": "NIST 800-53 control inheritance bridge. Validates controls transfer from legacy to target. Links to Security Canvas.",
     "graph_json": _snip("ctl-compliance-bridge", "Compliance Bridge"), "tags": json.dumps(["nist", "controls", "security-canvas"])},
    {"id": "snip-security-scan", "name": "Pre-Migration Security Scan", "category": "controls",
     "description": "SAST/DAST/SCA scan before migration. Links to Security Canvas threat model assessment.",
     "graph_json": _snip("ctl-security-scan", "Security Scan"), "tags": json.dumps(["sast", "dast", "security-canvas"])},
    {"id": "snip-test-gate", "name": "Post-Migration Test Gate", "category": "controls",
     "description": "Validates functionality and performance after cutover. Links to QDC Canvas quality gates.",
     "graph_json": _snip("ctl-test-gate", "Test Gate"), "tags": json.dumps(["testing", "validation", "qdc-canvas"])},
    {"id": "snip-rollback", "name": "Rollback Checkpoint", "category": "controls",
     "description": "Defined rollback point for safe migration reversal. Critical for blue-green and big-bang patterns.",
     "graph_json": _snip("ctl-rollback", "Rollback Point"), "tags": json.dumps(["rollback", "safety", "risk"])},
    # Middleware & Data
    {"id": "snip-cdc-replication", "name": "CDC Data Replication", "category": "middleware",
     "description": "Change Data Capture for zero-downtime data sync. Links to Data Canvas CDC patterns.",
     "graph_json": _snip("mid-sync", "CDC Replication"), "tags": json.dumps(["cdc", "replication", "data-canvas"])},
    {"id": "snip-etl-pipeline", "name": "ETL Migration Pipeline", "category": "middleware",
     "description": "Extract-Transform-Load pipeline (DMS, Glue, SSIS). Links to Data Canvas ETL patterns.",
     "graph_json": _snip("mid-etl", "ETL Pipeline"), "tags": json.dumps(["etl", "dms", "data-canvas"])},
    {"id": "snip-api-gateway", "name": "API Gateway / Traffic Router", "category": "middleware",
     "description": "Traffic splitting between old and new systems. Links to Network Canvas proxy patterns.",
     "graph_json": _snip("mid-proxy", "API Gateway"), "tags": json.dumps(["api-gateway", "routing", "network-canvas"])},
    {"id": "snip-message-queue", "name": "Async Message Queue", "category": "middleware",
     "description": "Decoupled migration via SQS/Kafka/MQ. Links to Pipeline Canvas event patterns.",
     "graph_json": _snip("mid-queue", "Message Queue"), "tags": json.dumps(["sqs", "kafka", "pipeline-canvas"])},
    {"id": "snip-acl", "name": "Anti-Corruption Layer", "category": "middleware",
     "description": "Translation layer between legacy and modern APIs. Essential for strangler fig patterns.",
     "graph_json": _snip("mid-acl", "Anti-Corruption Layer"), "tags": json.dumps(["acl", "strangler-fig", "adapter"])},
    # Planning
    {"id": "snip-wave-group", "name": "Migration Wave Group", "category": "planning",
     "description": "Wave container for grouping migration targets by phase/priority.",
     "graph_json": _snip("wave-group", "Wave N"), "tags": json.dumps(["wave", "planning", "group"])},
    {"id": "snip-milestone", "name": "Migration Milestone", "category": "planning",
     "description": "Key decision point or phase gate in migration timeline.",
     "graph_json": _snip("plan-milestone", "Milestone"), "tags": json.dumps(["milestone", "decision", "timeline"])},
    {"id": "snip-dependency", "name": "External Dependency", "category": "planning",
     "description": "External dependency (vendor, license, network circuit) that must resolve before migration.",
     "graph_json": _snip("plan-dependency", "Dependency"), "tags": json.dumps(["dependency", "blocker", "external"])},
    # Dual System Patterns (cross-canvas)
    {"id": "snip-dual-monitoring", "name": "Dual System Monitoring", "category": "observability",
     "description": "Monitor both source and target during cutover. Links to Observability Canvas OTel patterns.",
     "graph_json": json.dumps({"nodes": [
         {"id": "src-mon", "type": "src-legacy", "label": "Source Monitoring", "x": 80, "y": 100},
         {"id": "tgt-mon", "type": "tgt-cloud", "label": "Target Monitoring", "x": 320, "y": 100},
     ], "edges": []}), "tags": json.dumps(["monitoring", "observability-canvas", "cutover"])},
    {"id": "snip-dual-pipeline", "name": "Dual CI/CD Pipeline", "category": "pipeline",
     "description": "Run old and new CI/CD pipelines in parallel during migration. Links to Pipeline Canvas.",
     "graph_json": json.dumps({"nodes": [
         {"id": "src-pipe", "type": "src-middleware", "label": "Legacy Pipeline", "x": 80, "y": 100},
         {"id": "tgt-pipe", "type": "tgt-container", "label": "New Pipeline", "x": 320, "y": 100},
     ], "edges": []}), "tags": json.dumps(["cicd", "pipeline-canvas", "parallel"])},
]


# ── Seed Runbooks ────────────────────────────────────────────────────────────

SEED_RUNBOOKS = [
    {"id": "rb-pre-migration", "title": "Pre-Migration Validation", "trigger_event": "migration_start",
     "severity": "high", "description": "Validate all prerequisites before starting migration execution.",
     "steps_json": json.dumps([
         {"order": 1, "action": "Verify source system inventory matches migration design", "responsible": "Migration Lead"},
         {"order": 2, "action": "Confirm target environment provisioned and accessible", "responsible": "Cloud Engineer"},
         {"order": 3, "action": "Run Security Canvas STRIDE assessment on migration design", "responsible": "ISSO"},
         {"order": 4, "action": "Verify Boundary Canvas ATO boundary updates approved", "responsible": "AO"},
         {"order": 5, "action": "Confirm Data Canvas CDC replication operational", "responsible": "DBA"},
         {"order": 6, "action": "Validate Network Canvas connectivity (Direct Connect / ExpressRoute)", "responsible": "Network Engineer"},
         {"order": 7, "action": "Run QDC Canvas pre-release quality gate", "responsible": "QA Lead"},
         {"order": 8, "action": "Confirm rollback procedure tested and documented", "responsible": "Migration Lead"},
     ])},
    {"id": "rb-cutover-execution", "title": "Cutover Execution Procedure", "trigger_event": "cutover_window",
     "severity": "critical", "description": "Step-by-step cutover execution during the migration window.",
     "steps_json": json.dumps([
         {"order": 1, "action": "Notify stakeholders: cutover window open", "responsible": "Migration Lead"},
         {"order": 2, "action": "Stop writes to source system (maintenance mode)", "responsible": "App Team"},
         {"order": 3, "action": "Verify final data sync complete (CDC lag = 0)", "responsible": "DBA"},
         {"order": 4, "action": "Switch DNS / load balancer to target environment", "responsible": "Network Engineer"},
         {"order": 5, "action": "Run smoke test suite against target", "responsible": "QA Lead"},
         {"order": 6, "action": "Monitor Observability Canvas dashboards for 30 minutes", "responsible": "SRE"},
         {"order": 7, "action": "If smoke tests fail: execute rollback runbook", "responsible": "Migration Lead"},
         {"order": 8, "action": "If healthy: notify stakeholders cutover complete", "responsible": "Migration Lead"},
     ])},
    {"id": "rb-rollback", "title": "Migration Rollback Procedure", "trigger_event": "migration_failure",
     "severity": "critical", "description": "Emergency rollback to source system when migration fails.",
     "steps_json": json.dumps([
         {"order": 1, "action": "Decision: confirm rollback (severity assessment)", "responsible": "Migration Lead + AO"},
         {"order": 2, "action": "Switch DNS / load balancer back to source", "responsible": "Network Engineer"},
         {"order": 3, "action": "Re-enable writes on source system", "responsible": "App Team"},
         {"order": 4, "action": "Verify source system operational (smoke tests)", "responsible": "QA Lead"},
         {"order": 5, "action": "Preserve target environment logs for root cause analysis", "responsible": "SRE"},
         {"order": 6, "action": "File incident report with timeline and root cause", "responsible": "Migration Lead"},
     ])},
    {"id": "rb-data-validation", "title": "Post-Migration Data Validation", "trigger_event": "cutover_complete",
     "severity": "high", "description": "Validate data integrity between source and target after cutover.",
     "steps_json": json.dumps([
         {"order": 1, "action": "Run row count comparison (source vs target)", "responsible": "DBA"},
         {"order": 2, "action": "Run checksum validation on critical tables", "responsible": "DBA"},
         {"order": 3, "action": "Verify PII/CUI classification labels preserved", "responsible": "ISSO"},
         {"order": 4, "action": "Test referential integrity across migrated schemas", "responsible": "DBA"},
         {"order": 5, "action": "Run application integration tests against target DB", "responsible": "Dev Lead"},
     ])},
    {"id": "rb-ato-boundary-transition", "title": "ATO Boundary Transition During Migration", "trigger_event": "boundary_change",
     "severity": "high", "description": "Handle ATO authorization boundary changes when systems move between environments.",
     "steps_json": json.dumps([
         {"order": 1, "action": "Review Boundary Canvas: identify affected ATO boundaries", "responsible": "ISSO"},
         {"order": 2, "action": "Update ISA tracker for interconnections to migrated systems", "responsible": "ISSO"},
         {"order": 3, "action": "Run Compliance Bridge: verify NIST controls transfer to target", "responsible": "Compliance Analyst"},
         {"order": 4, "action": "Update PPS matrix with new port/protocol/service requirements", "responsible": "Network Engineer"},
         {"order": 5, "action": "Submit SSP amendment to AO for boundary change approval", "responsible": "ISSO"},
     ])},
    {"id": "rb-network-cutover", "title": "Network Cut-Over Execution", "trigger_event": "network_migration",
     "severity": "critical", "description": "Execute network topology changes during migration window.",
     "steps_json": json.dumps([
         {"order": 1, "action": "Verify Network Canvas target topology operational", "responsible": "Network Engineer"},
         {"order": 2, "action": "Update BGP advertisements / routing tables", "responsible": "Network Engineer"},
         {"order": 3, "action": "Switch VPN/Direct Connect primary path", "responsible": "Network Engineer"},
         {"order": 4, "action": "Verify SCCA/TIC 3.0 compliance on new path", "responsible": "Security Engineer"},
         {"order": 5, "action": "Monitor Network Canvas bandwidth/latency metrics", "responsible": "SRE"},
     ])},
    {"id": "rb-incident-during-migration", "title": "Incident Response During Migration", "trigger_event": "incident_detected",
     "severity": "critical", "description": "Handle security or operational incidents discovered during migration execution.",
     "steps_json": json.dumps([
         {"order": 1, "action": "Assess: is this a migration-caused issue or pre-existing?", "responsible": "Incident Commander"},
         {"order": 2, "action": "If migration-caused: pause migration, do NOT rollback yet", "responsible": "Migration Lead"},
         {"order": 3, "action": "Engage Security Canvas: run STRIDE analysis on incident vector", "responsible": "ISSO"},
         {"order": 4, "action": "Engage Observability Canvas: collect logs from both environments", "responsible": "SRE"},
         {"order": 5, "action": "Decision: remediate-in-place vs rollback vs partial rollback", "responsible": "Incident Commander"},
         {"order": 6, "action": "Execute decision and verify with smoke tests", "responsible": "Migration Lead"},
     ])},
    {"id": "rb-compliance-evidence", "title": "Post-Migration Compliance Evidence Collection", "trigger_event": "migration_complete",
     "severity": "high", "description": "Collect and validate compliance evidence after migration for ATO/FedRAMP/CMMC.",
     "steps_json": json.dumps([
         {"order": 1, "action": "Run QDC Canvas cATO evidence refresh on target system", "responsible": "Compliance Analyst"},
         {"order": 2, "action": "Generate updated SSP sections for migrated components", "responsible": "ISSO"},
         {"order": 3, "action": "Verify Security Canvas threat model reflects new architecture", "responsible": "Security Architect"},
         {"order": 4, "action": "Update Boundary Canvas with final ATO boundary configuration", "responsible": "ISSO"},
         {"order": 5, "action": "Archive migration artifacts (plans, logs, test results)", "responsible": "Migration Lead"},
         {"order": 6, "action": "Submit ATO package update to AO", "responsible": "ISSO"},
     ])},
]


# ── Seed SOPs ────────────────────────────────────────────────────────────────

SEED_SOPS = [
    {"id": "sop-mc-001", "title": "Migration Planning & Readiness Assessment", "sop_type": "migration_readiness",
     "description": "End-to-end procedure for assessing migration readiness across all design canvases.",
     "purpose": "Ensure all prerequisites are met before migration execution begins.",
     "scope": "All migration designs — cloud, application, network, data center.",
     "nist_controls": json.dumps(["CM-3", "SA-10", "CA-2", "CA-7"]),
     "steps": json.dumps([
         {"order": 1, "description": "Create migration design in Migration Canvas with source/target mapping", "responsible_party": "Migration Architect"},
         {"order": 2, "description": "Run Assessment (Assess button) — resolve all CAT1 findings", "responsible_party": "Migration Architect"},
         {"order": 3, "description": "Run Gap Analysis — close all high-severity gaps", "responsible_party": "Migration Lead"},
         {"order": 4, "description": "Verify Boundary Canvas ATO boundary design updated for migration", "responsible_party": "ISSO"},
         {"order": 5, "description": "Verify Security Canvas STRIDE model updated for new architecture", "responsible_party": "Security Architect"},
         {"order": 6, "description": "Verify Data Canvas data flow design for migration ETL/CDC", "responsible_party": "Data Architect"},
         {"order": 7, "description": "Verify Network Canvas topology design for target connectivity", "responsible_party": "Network Architect"},
         {"order": 8, "description": "Run Readiness Score — must be >= 80% to proceed", "responsible_party": "Migration Lead"},
         {"order": 9, "description": "Run Oracle Anticipation Analysis — review predictions", "responsible_party": "Migration Lead"},
         {"order": 10, "description": "Obtain go/no-go decision from stakeholders", "responsible_party": "Program Manager"},
     ])},
    {"id": "sop-mc-002", "title": "Phased Migration Execution", "sop_type": "cutover_planning",
     "description": "Procedure for executing phased migration waves with compliance gates between each wave.",
     "purpose": "Ensure each migration wave completes successfully with compliance verification before proceeding.",
     "scope": "Multi-wave migrations with 2+ migration phases.",
     "nist_controls": json.dumps(["CM-3", "CM-4", "SA-10", "SA-11", "CA-7"]),
     "steps": json.dumps([
         {"order": 1, "description": "Execute Pre-Migration Validation runbook for current wave", "responsible_party": "Migration Lead"},
         {"order": 2, "description": "Execute wave cutover per Cutover Execution runbook", "responsible_party": "Migration Lead"},
         {"order": 3, "description": "Execute Post-Migration Data Validation runbook", "responsible_party": "DBA"},
         {"order": 4, "description": "Run QDC Canvas quality gate on migrated components", "responsible_party": "QA Lead"},
         {"order": 5, "description": "Run Security Canvas scan on migrated environment", "responsible_party": "Security Engineer"},
         {"order": 6, "description": "Update migration tracker with wave completion metrics", "responsible_party": "Migration Lead"},
         {"order": 7, "description": "Obtain wave sign-off before proceeding to next wave", "responsible_party": "Program Manager"},
     ])},
    {"id": "sop-mc-003", "title": "Post-Migration Validation & Sign-Off", "sop_type": "post_migration_validation",
     "description": "Comprehensive validation after all migration waves complete.",
     "purpose": "Ensure the migrated system is fully operational, compliant, and stakeholder-approved.",
     "scope": "Final validation after migration execution completes.",
     "nist_controls": json.dumps(["CA-2", "CA-7", "SA-11", "AU-6", "SI-4"]),
     "steps": json.dumps([
         {"order": 1, "description": "Run full regression test suite against target environment", "responsible_party": "QA Lead"},
         {"order": 2, "description": "Validate Observability Canvas monitoring operational on target", "responsible_party": "SRE"},
         {"order": 3, "description": "Validate Pipeline Canvas CI/CD pipeline operational for target", "responsible_party": "DevOps Lead"},
         {"order": 4, "description": "Execute Post-Migration Compliance Evidence runbook", "responsible_party": "Compliance Analyst"},
         {"order": 5, "description": "Verify Infrastructure Canvas IaC matches deployed target", "responsible_party": "Cloud Engineer"},
         {"order": 6, "description": "Decommission source system (after retention period)", "responsible_party": "Operations"},
         {"order": 7, "description": "Archive migration design and all artifacts", "responsible_party": "Migration Lead"},
         {"order": 8, "description": "Obtain final sign-off from AO and Program Manager", "responsible_party": "Program Manager"},
     ])},
    {"id": "sop-mc-004", "title": "Rollback & Recovery Procedures", "sop_type": "rollback_procedure",
     "description": "Standard procedure for rolling back a failed migration at any stage.",
     "purpose": "Ensure rapid, safe recovery when migration encounters critical failures.",
     "scope": "All migration types — triggers when migration health check fails.",
     "nist_controls": json.dumps(["CP-10", "CP-2", "CM-3", "IR-4"]),
     "steps": json.dumps([
         {"order": 1, "description": "Identify rollback trigger: test failure, data corruption, performance degradation, or security incident", "responsible_party": "Migration Lead"},
         {"order": 2, "description": "Execute Migration Rollback Procedure runbook", "responsible_party": "Migration Lead"},
         {"order": 3, "description": "Verify source system fully operational", "responsible_party": "QA Lead"},
         {"order": 4, "description": "Conduct root cause analysis with cross-canvas review", "responsible_party": "Migration Architect"},
         {"order": 5, "description": "Update migration design to address root cause", "responsible_party": "Migration Architect"},
         {"order": 6, "description": "Re-run Assessment and Readiness before re-attempting", "responsible_party": "Migration Lead"},
     ])},
    {"id": "sop-mc-005", "title": "Stakeholder Communication During Migration", "sop_type": "cutover_planning",
     "description": "Communication plan for all stakeholders during migration execution windows.",
     "purpose": "Keep stakeholders informed of migration progress, risks, and decisions.",
     "scope": "All migration windows and post-migration validation periods.",
     "nist_controls": json.dumps(["PM-1", "IR-6", "CA-7"]),
     "steps": json.dumps([
         {"order": 1, "description": "Send T-72h notification: migration window schedule and impact", "responsible_party": "Program Manager"},
         {"order": 2, "description": "Send T-24h notification: final go/no-go confirmation", "responsible_party": "Migration Lead"},
         {"order": 3, "description": "Send T-0 notification: migration window open", "responsible_party": "Migration Lead"},
         {"order": 4, "description": "Send hourly status updates during cutover window", "responsible_party": "Migration Lead"},
         {"order": 5, "description": "Send completion notification with smoke test results", "responsible_party": "Migration Lead"},
         {"order": 6, "description": "Send T+24h post-migration health report", "responsible_party": "SRE"},
     ])},
    {"id": "sop-mc-006", "title": "Migration Lessons Learned & Closure", "sop_type": "post_migration_validation",
     "description": "Capture lessons learned and formally close the migration project.",
     "purpose": "Improve future migrations by documenting what worked, what failed, and recommendations.",
     "scope": "Post-migration closure — after final sign-off.",
     "nist_controls": json.dumps(["PM-6", "CA-7", "SA-15"]),
     "steps": json.dumps([
         {"order": 1, "description": "Conduct retrospective with all migration team members", "responsible_party": "Migration Lead"},
         {"order": 2, "description": "Document: actual vs estimated timeline, cost, resource utilization", "responsible_party": "Program Manager"},
         {"order": 3, "description": "Document: all incidents, rollbacks, and root causes", "responsible_party": "Migration Lead"},
         {"order": 4, "description": "Document: cross-canvas integration lessons (which canvas integrations were most valuable)", "responsible_party": "Migration Architect"},
         {"order": 5, "description": "Update migration templates based on lessons learned", "responsible_party": "Migration Architect"},
         {"order": 6, "description": "Archive all artifacts and close migration project", "responsible_party": "Program Manager"},
     ])},
]


# ── Cloud Application Migration (CAM) Schema ────────────────────────────────

CAM_SCHEMA = """
-- CAM Project coordinator: links cross-canvas designs + tracks migration projects
CREATE TABLE IF NOT EXISTS mc_projects (
    id                TEXT PRIMARY KEY,
    name              TEXT NOT NULL,
    description       TEXT DEFAULT '',
    customer          TEXT DEFAULT '',
    owner             TEXT DEFAULT '',
    status            TEXT DEFAULT 'draft'
        CHECK(status IN ('draft','in_review','approved','active','complete','archived')),
    classification    TEXT DEFAULT 'CUI'
        CHECK(classification IN ('PUBLIC','CUI','SECRET','TS')),
    impact_level      TEXT DEFAULT 'IL4'
        CHECK(impact_level IN ('IL2','IL4','IL5','IL6')),
    mc_session_id     TEXT REFERENCES mc_srv_sessions(id),
    ddc_design_id     TEXT,      -- pointer → data_canvas.db data_designs.id
    idc_design_id     TEXT,      -- pointer → infra_canvas.db infra_designs.id
    ndc_topology_id   TEXT,      -- pointer → network_canvas.db topologies.id
    source_stack_json TEXT DEFAULT '[]',  -- [{tech, version, platform}, ...]
    target_stack_json TEXT DEFAULT '[]',  -- [{tech, aws_service, strategy_7r}, ...]
    notes             TEXT DEFAULT '',
    created_at        TEXT DEFAULT CURRENT_TIMESTAMP,
    updated_at        TEXT DEFAULT CURRENT_TIMESTAMP
);

-- Migration phases per project (mirrors nc_migration_phases in NDC)
CREATE TABLE IF NOT EXISTS mc_project_phases (
    id                  TEXT PRIMARY KEY,
    project_id          TEXT NOT NULL REFERENCES mc_projects(id) ON DELETE CASCADE,
    phase_num           INTEGER NOT NULL,
    title               TEXT NOT NULL,
    description         TEXT DEFAULT '',
    wave_num            INTEGER DEFAULT 1,
    duration_days       INTEGER DEFAULT 0,
    maintenance_window  TEXT DEFAULT '',
    rollback_criteria   TEXT DEFAULT '',
    depends_on_phase_id TEXT,    -- predecessor phase for dependency tracking
    status              TEXT DEFAULT 'planned'
        CHECK(status IN ('planned','in_progress','completed','rolled_back')),
    classification      TEXT DEFAULT 'CUI',
    created_at          TEXT DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_mc_proj_phases_project ON mc_project_phases(project_id);
CREATE INDEX IF NOT EXISTS idx_mc_proj_phases_status  ON mc_project_phases(status);

-- Phase → SOP links (mirrors nc_phase_documents in NDC)
CREATE TABLE IF NOT EXISTS mc_project_phase_sops (
    id              TEXT PRIMARY KEY,
    phase_id        TEXT NOT NULL REFERENCES mc_project_phases(id) ON DELETE CASCADE,
    project_id      TEXT NOT NULL REFERENCES mc_projects(id) ON DELETE CASCADE,
    sop_source      TEXT NOT NULL DEFAULT 'mc'
        CHECK(sop_source IN ('mc','ddc','idc','ndc')),
    sop_id          TEXT NOT NULL,  -- FK into sop_source canvas's sop table
    sop_title       TEXT DEFAULT '',
    relevance_note  TEXT DEFAULT '',
    display_order   INTEGER DEFAULT 0,
    created_at      TEXT DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(phase_id, sop_id)
);
CREATE INDEX IF NOT EXISTS idx_mc_phase_sops_phase   ON mc_project_phase_sops(phase_id);
CREATE INDEX IF NOT EXISTS idx_mc_phase_sops_project ON mc_project_phase_sops(project_id);

-- AI modernization opportunities per component
CREATE TABLE IF NOT EXISTS mc_ai_opportunities (
    id                TEXT PRIMARY KEY,
    project_id        TEXT NOT NULL REFERENCES mc_projects(id) ON DELETE CASCADE,
    app_id            TEXT REFERENCES mc_app_inventory(id),
    component_name    TEXT NOT NULL,
    opportunity_title TEXT NOT NULL,
    aws_ai_service    TEXT NOT NULL,  -- e.g. 'bedrock-kb','pgvector','semantic-cache'
    benefit_category  TEXT DEFAULT 'efficiency'
        CHECK(benefit_category IN ('search','nlq','pipeline','caching','ux','analytics','security','efficiency')),
    effort_days       INTEGER DEFAULT 0,
    status            TEXT DEFAULT 'identified'
        CHECK(status IN ('identified','scoped','in_progress','complete','deferred')),
    notes             TEXT DEFAULT '',
    created_at        TEXT DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_mc_ai_opps_project   ON mc_ai_opportunities(project_id);
CREATE INDEX IF NOT EXISTS idx_mc_ai_opps_component ON mc_ai_opportunities(component_name);

-- Code refactoring jobs generated by cam_refactor_engine.py
CREATE TABLE IF NOT EXISTS mc_refactor_jobs (
    id              TEXT PRIMARY KEY,
    project_id      TEXT NOT NULL REFERENCES mc_projects(id) ON DELETE CASCADE,
    app_id          TEXT REFERENCES mc_app_inventory(id),
    component_name  TEXT NOT NULL,
    refactor_type   TEXT NOT NULL
        CHECK(refactor_type IN (
            'version_upgrade',
            'framework_migration',
            'db_ddl_generation',
            'code_scaffold',
            'language_translation',
            'ai_integration'
        )),
    source_tech     TEXT DEFAULT '',
    target_tech     TEXT DEFAULT '',
    source_language TEXT DEFAULT '',
    target_language TEXT DEFAULT '',
    source_path     TEXT DEFAULT '',
    output_path     TEXT DEFAULT '',
    params_json     TEXT DEFAULT '{}',
    status          TEXT DEFAULT 'queued'
        CHECK(status IN ('queued','running','completed','failed','skipped')),
    result_summary  TEXT DEFAULT '',
    artifacts_json  TEXT DEFAULT '[]',
    error_message   TEXT DEFAULT '',
    triggered_by    TEXT DEFAULT 'auto',
    phase_id        TEXT REFERENCES mc_project_phases(id),
    ai_opp_id       TEXT REFERENCES mc_ai_opportunities(id),
    started_at      TEXT,
    completed_at    TEXT,
    classification  TEXT DEFAULT 'CUI // SP-CTI',
    created_at      TEXT DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_mc_refactor_project ON mc_refactor_jobs(project_id);
CREATE INDEX IF NOT EXISTS idx_mc_refactor_status  ON mc_refactor_jobs(status);
CREATE INDEX IF NOT EXISTS idx_mc_refactor_type    ON mc_refactor_jobs(refactor_type);
"""


def _migrate_cam_tables(conn):
    """Idempotently add CAM project/phase/SOP/AI-opportunity tables."""
    conn.executescript(CAM_SCHEMA)
    # Auto-seed demo project if none exist
    try:
        count = conn.execute("SELECT COUNT(*) FROM mc_projects").fetchone()[0]
        if count == 0:
            try:
                from tools.migration_canvas.cam_seed_demo import seed as _seed_cam
                _seed_cam()
            except Exception as _e:
                import logging as _log
                _log.getLogger(__name__).info("CAM demo seed skipped: %s", _e)
    except Exception:
        pass  # table may not exist yet if schema apply failed


def init_db():
    """Create tables and seed templates, snippets, runbooks, SOPs, and cloud instances."""
    with get_connection() as conn:
        conn.executescript(SCHEMA)
        _migrate_network_tables(conn)
        _migrate_server_tables(conn)
        _migrate_wave_dep_tables(conn)
        _migrate_gap_fill_tables(conn)
        _migrate_app_tables(conn)
        _migrate_cam_tables(conn)
        _seed_cloud_instances(conn)

        ph = sql_placeholder(conn)
        # Seed templates
        for tpl in SEED_TEMPLATES:
            existing = conn.execute(f"SELECT id FROM mc_templates WHERE id={ph}", (tpl["id"],)).fetchone()
            if not existing:
                conn.execute(
                    f"INSERT INTO mc_templates (id, name, category, description, graph_json, tags) VALUES ({ph},{ph},{ph},{ph},{ph},{ph})",
                    (tpl["id"], tpl["name"], tpl["category"], tpl["description"], tpl["graph_json"], tpl["tags"]),
                )

        # Seed snippets
        for snip in SEED_SNIPPETS:
            existing = conn.execute(f"SELECT id FROM mc_snippets WHERE id={ph}", (snip["id"],)).fetchone()
            if not existing:
                conn.execute(
                    f"INSERT INTO mc_snippets (id, name, category, description, graph_json, tags) VALUES ({ph},{ph},{ph},{ph},{ph},{ph})",
                    (snip["id"], snip["name"], snip["category"], snip["description"], snip["graph_json"], snip["tags"]),
                )

        # Seed runbooks
        for rb in SEED_RUNBOOKS:
            existing = conn.execute(f"SELECT id FROM mc_runbooks WHERE id={ph}", (rb["id"],)).fetchone()
            if not existing:
                conn.execute(
                    f"INSERT INTO mc_runbooks (id, title, trigger_event, severity, description, steps_json) VALUES ({ph},{ph},{ph},{ph},{ph},{ph})",
                    (rb["id"], rb["title"], rb["trigger_event"], rb["severity"], rb["description"], rb["steps_json"]),
                )

        # Seed SOPs
        for sop in SEED_SOPS:
            existing = conn.execute(f"SELECT id FROM mc_sops WHERE id={ph}", (sop["id"],)).fetchone()
            if not existing:
                conn.execute(
                    f"INSERT INTO mc_sops (id, title, sop_type, description, purpose, scope, "
                    f"nist_controls, steps, approval_status) VALUES ({ph},{ph},{ph},{ph},{ph},{ph},{ph},{ph},{ph})",
                    (sop["id"], sop["title"], sop["sop_type"], sop["description"],
                     sop["purpose"], sop["scope"], sop["nist_controls"], sop["steps"], "draft"),
                )

        conn.commit()


if __name__ == "__main__":
    init_db()
    print("Migration Canvas DB initialized at", DB_PATH)
