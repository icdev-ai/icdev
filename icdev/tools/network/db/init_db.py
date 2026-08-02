"""
Network Design Canvas — DB initializer
Creates schema and seeds 12 canonical network templates.

Dual-backend: SQLite (default) or PostgreSQL.
Set NC_STORAGE_BACKEND=postgresql + NC_PG_* env vars to use PostgreSQL.
SQLite is the default for dev, air-gap, and single-user deployments.
PostgreSQL is recommended for production multi-user/global deployments.
"""

import json
import os
import sys
import uuid
import re as _re_cksites
from datetime import datetime, timezone
from pathlib import Path

from icdev.tools.network.db.constants import (  # noqa: F401
    _check,
    ACTION_TYPES,
    ALERT_ACTIONS,
    APPLICATION_TYPES,
    APPROVAL_STATUS,
    AUDIT_ACTIONS,
    CLASSIFICATIONS,
    CVE_DATA_SOURCES,
    DOC_SOURCES,
    DOC_TYPES,
    DOMAIN_TYPES,
    EXCEPTION_TYPES,
    EXPOSURE_TYPES,
    FINDING_DATA_SOURCES,
    FINDING_STATUS,
    FLOW_CLASSIFICATIONS,
    HITL_STATUS,
    ICON_TYPES,
    IMPACT_LEVELS,
    INGEST_CHANNELS,
    INGEST_RUN_STATUS,
    INGEST_STATUS,
    LINK_EVENT_TYPES,
    NQE_SOURCES,
    NQE_SOURCES_STATIC,
    PARTNER_TYPES,
    PATCH_STATUS,
    PEER_STATUS,
    PERSONA_IDS,
    RECURRENCES,
    REMEDIATION_STATUS,
    RESULT_STATUS,
    RISK_LEVELS,
    SEVERITY_HML,
    SEVERITY_LEVELS,
    SIMULATION_STATUS,
    TRENDS,
)

# When integrated into ICDEV, DB lives in data/ directory
_ICDEV_ROOT = Path(__file__).resolve().parents[3]  # tools/network/db -> ICDev root
DB_PATH = _ICDEV_ROOT / "data" / "network_canvas.db"

# Backend detection — PG-primary: inherit the platform backend (no hard sqlite
# default). NC_STORAGE_BACKEND overrides for a dedicated network_canvas backend.
_NC_BACKEND = os.environ.get("NC_STORAGE_BACKEND", os.environ.get("ICDEV_CANVAS_STORAGE_BACKEND", os.environ.get("ICDEV_STORAGE_BACKEND", "postgresql"))).lower()


def get_connection():
    """Get a database connection — SQLite or PostgreSQL.

    Returns a connection that supports:
        conn.execute(sql, params) — with ? placeholders (auto-translated for PG)
        conn.commit()
        conn.close()
        row["column_name"] — dict-like row access

    For PostgreSQL, uses ICDEV's StorageConnection wrapper which
    auto-translates SQLite SQL to PostgreSQL (? → %s, PRAGMA → no-op, etc.)

    cvx-sql-03: this is the canvas-connection pattern — it already disables RLS
    by clearing the security context below (see the annotated call). It is NOT renamed to
    get_canvas_connection() because that helper targets the shared icdev DB on PG,
    which would break this canvas's dedicated NC_PG_DATABASE=network_canvas contract.
    """
    if _NC_BACKEND == "postgresql":
        try:
            from tools.db.storage import get_connection as _icdev_conn

            # Use ICDEV's storage layer which handles PG translation
            conn = _icdev_conn(db_path=os.environ.get("NC_PG_DATABASE", "network_canvas"))
            # Canvas tables have no tenant_id/classification columns — disable
            # RLS so the global row-level predicate does not raise UndefinedColumn.
            conn.set_security_context(None)  # rls-bypass: canvas tables lack tenant_id/classification columns; RLS predicate would raise UndefinedColumn (ndc program)
            return conn
        except ImportError:
            pass  # Fall through to SQLite
    # SQLite fallback (backup / air-gap). Wrap in StorageConnection so NC callers'
    # PG-native %s placeholders translate to ? on the SQLite path too.
    import sqlite3 as _sqlite3
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = _sqlite3.connect(str(DB_PATH))
    conn.row_factory = _sqlite3.Row
    try:
        from tools.db.storage import StorageConnection

        return StorageConnection(conn, "sqlite")
    except ImportError:
        return conn



# ---------------------------------------------------------------------------
# CHECK-constraint derivation (cvx-sql-04)
# ---------------------------------------------------------------------------
# Every string-valued CHECK constraint in the schema below is a marker
# (@@CK<n>@@) rendered from a Python constant, so the SQL can never silently
# drift from the enum. _CHECK_SITES is ordered to match the markers in
# _SCHEMA_TEMPLATE; the value-set parity test asserts the rendered SQL matches
# these constants exactly.
_CHECK_SITES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("partner_type", PARTNER_TYPES),
    ("status", PEER_STATUS),
    ("classification", CLASSIFICATIONS),
    ("impact_level", IMPACT_LEVELS),
    ("doc_type", DOC_TYPES),
    ("status", INGEST_STATUS),
    ("channel", INGEST_CHANNELS),
    ("status", INGEST_RUN_STATUS),
    ("app_type", APPLICATION_TYPES),
    ("classification", FLOW_CLASSIFICATIONS),
    ("domain_type", DOMAIN_TYPES),
    ("action_type", ACTION_TYPES),
    ("persona_id", PERSONA_IDS),
    ("icon_type", ICON_TYPES),
    ("doc_source", DOC_SOURCES),
    ("severity", SEVERITY_LEVELS),
    ("data_source", FINDING_DATA_SOURCES),
    ("hitl_status", HITL_STATUS),
    ("status", FINDING_STATUS),
    ("severity", SEVERITY_LEVELS),
    ("status", REMEDIATION_STATUS),
    ("exception_type", EXCEPTION_TYPES),
    ("risk_level", RISK_LEVELS),
    ("status", APPROVAL_STATUS),
    ("result", RESULT_STATUS),
    ("severity", SEVERITY_HML),
    ("action", ALERT_ACTIONS),
    ("data_source", CVE_DATA_SOURCES),
    ("action", AUDIT_ACTIONS),
    ("trend", TRENDS),
    ("exposure_type", EXPOSURE_TYPES),
    ("nqe_source", NQE_SOURCES),
    ("status", PATCH_STATUS),
    ("recurrence", RECURRENCES),
    ("simulation_status", SIMULATION_STATUS),
    ("risk_tier", RISK_LEVELS),
    ("nqe_source", NQE_SOURCES_STATIC),
    ("event_type", LINK_EVENT_TYPES),
    ("flap_risk", RISK_LEVELS),
    ("risk_tier", RISK_LEVELS),
    ("risk_tier", RISK_LEVELS),
    ("risk_tier", RISK_LEVELS),
    ("vendor_risk_rating", RISK_LEVELS),
)


def _render_schema(template: str) -> str:
    """Substitute @@CK<n>@@ markers with CHECK clauses derived from constants."""
    out = template
    for i, (col, values) in enumerate(_CHECK_SITES, start=1):
        out = out.replace(f"@@CK{i}@@", _check(col, values))
    return out


_SCHEMA_TEMPLATE = """
CREATE TABLE IF NOT EXISTS topologies (
    id          TEXT PRIMARY KEY,
    name        TEXT NOT NULL,
    description TEXT,
    graph_json  TEXT NOT NULL DEFAULT '{"nodes":[],"edges":[]}',
    template_id TEXT,
    classification TEXT DEFAULT 'public',
    created_at  TEXT DEFAULT CURRENT_TIMESTAMP,
    updated_at  TEXT DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS nc_templates (
    id            TEXT PRIMARY KEY,
    name          TEXT NOT NULL,
    category      TEXT,
    description   TEXT,
    graph_json    TEXT NOT NULL DEFAULT '{"nodes":[],"edges":[]}',
    thumbnail_svg TEXT,
    tags          TEXT DEFAULT '[]'
);

CREATE TABLE IF NOT EXISTS nc_template_docs (
    id            TEXT PRIMARY KEY,
    template_id   TEXT NOT NULL,
    doc_type      TEXT NOT NULL DEFAULT 'sop',
    title         TEXT NOT NULL,
    body_markdown TEXT NOT NULL DEFAULT '',
    created_at    TEXT DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (template_id) REFERENCES nc_templates(id)
);

-- Named nc_simulation_results, not simulation_results: the unprefixed name
-- belongs to the Digital Program Twin (tools/simulation/*, declared in
-- tools/db/init_icdev_db.py with a completely different shape). Both used
-- CREATE TABLE IF NOT EXISTS, so in the shared PostgreSQL database whichever
-- init ran first silently took the name from the other. See migration
-- 327_nc_simulation_results_rename.
CREATE TABLE IF NOT EXISTS nc_simulation_results (
    id          TEXT PRIMARY KEY,
    topology_id TEXT REFERENCES topologies(id),
    sim_type    TEXT NOT NULL,
    input_json  TEXT DEFAULT '{}',
    result_json TEXT DEFAULT '{}',
    ran_at      TEXT DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS nc_objects (
    id          TEXT PRIMARY KEY,
    topology_id TEXT REFERENCES topologies(id),
    object_type TEXT NOT NULL,
    label       TEXT,
    config_json TEXT DEFAULT '{}',
    pos_x       REAL DEFAULT 0,
    pos_y       REAL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS nc_audit (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    action      TEXT NOT NULL,
    entity_type TEXT,
    entity_id   TEXT,
    details     TEXT,
    user_id     TEXT,
    classification TEXT DEFAULT 'CUI // SP-CTI',
    ts          TEXT DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS nc_circuits (
    id TEXT PRIMARY KEY,
    topology_id TEXT REFERENCES topologies(id),
    circuit_id TEXT NOT NULL,
    carrier TEXT,
    circuit_type TEXT,
    bandwidth TEXT,
    handoff_a TEXT,
    handoff_z TEXT,
    customer TEXT,
    site TEXT,
    monthly_cost_usd REAL DEFAULT 0,
    contract_start TEXT,
    contract_end TEXT,
    sla_uptime_pct REAL DEFAULT 99.9,
    install_status TEXT DEFAULT 'planned',
    notes TEXT,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS nc_customers (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    customer_type TEXT DEFAULT 'customer',
    contact_name TEXT,
    contact_email TEXT,
    contract_ref TEXT,
    notes TEXT,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS nc_sites (
    id TEXT PRIMARY KEY,
    customer_id TEXT REFERENCES nc_customers(id),
    name TEXT NOT NULL,
    address TEXT,
    city TEXT,
    state TEXT,
    country TEXT DEFAULT 'US',
    site_type TEXT DEFAULT 'office',
    classification TEXT DEFAULT 'public',
    created_at TEXT DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS nc_ipam_blocks (
    id TEXT PRIMARY KEY,
    topology_id TEXT REFERENCES topologies(id),
    network TEXT NOT NULL,
    address_family TEXT DEFAULT 'ipv4',   -- ipv4, ipv6
    vlan_id INTEGER,
    vrf TEXT DEFAULT 'global',
    description TEXT,
    site_id TEXT,
    gateway TEXT,
    gateway_v6 TEXT,                      -- IPv6 gateway
    utilization_pct REAL DEFAULT 0,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS nc_cables (
    id TEXT PRIMARY KEY,
    topology_id TEXT REFERENCES topologies(id),
    cable_id TEXT NOT NULL,
    cable_type TEXT,
    src_device TEXT,
    src_port TEXT,
    dst_device TEXT,
    dst_port TEXT,
    patch_panel TEXT,
    length_m REAL,
    status TEXT DEFAULT 'active',
    notes TEXT,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS nc_cross_connects (
    id TEXT PRIMARY KEY,
    topology_id TEXT REFERENCES topologies(id),
    xconn_id TEXT NOT NULL,
    facility TEXT,
    meet_me_room TEXT,
    src_device TEXT,
    src_port TEXT,
    dst_device TEXT,
    dst_port TEXT,
    media_type TEXT DEFAULT 'SMF',
    bandwidth TEXT,
    provider_a TEXT,
    provider_z TEXT,
    loa_status TEXT DEFAULT 'pending',
    monthly_cost_usd REAL DEFAULT 0,
    install_date TEXT,
    status TEXT DEFAULT 'planned',
    notes TEXT,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS nc_versions (
    id TEXT PRIMARY KEY,
    topology_id TEXT REFERENCES topologies(id),
    version_num INTEGER NOT NULL,
    label TEXT,
    phase TEXT,
    graph_json TEXT NOT NULL,
    created_by TEXT,
    notes TEXT,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS nc_compliance_checks (
    id TEXT PRIMARY KEY,
    topology_id TEXT REFERENCES topologies(id),
    check_type TEXT NOT NULL,
    passed INTEGER DEFAULT 0,
    failed INTEGER DEFAULT 0,
    findings_json TEXT DEFAULT '[]',
    ran_at TEXT DEFAULT CURRENT_TIMESTAMP
);

-- Topology compliance profile (target regime + declared classification)
CREATE TABLE IF NOT EXISTS nc_compliance_profiles (
    id          TEXT PRIMARY KEY,
    topology_id TEXT UNIQUE REFERENCES topologies(id),
    regimes     TEXT DEFAULT '["fisma_high"]',  -- JSON array of active regime IDs
    classification TEXT DEFAULT 'CUI',          -- CUI, SECRET, TOP SECRET, PUBLIC
    environment TEXT DEFAULT 'IL4',             -- IL2, IL4, IL5, IL6
    auto_audit  INTEGER DEFAULT 1,             -- run audit on every save
    created_at  TEXT DEFAULT CURRENT_TIMESTAMP,
    updated_at  TEXT DEFAULT CURRENT_TIMESTAMP
);

-- Persistent findings (tracked over time for trend analysis)
CREATE TABLE IF NOT EXISTS nc_compliance_findings (
    id          TEXT PRIMARY KEY,
    topology_id TEXT REFERENCES topologies(id),
    audit_id    TEXT REFERENCES nc_compliance_checks(id),
    rule_id     TEXT NOT NULL,                 -- e.g. NET-ENC-001
    regime      TEXT NOT NULL,                 -- fisma_high, stig, fips, zta, cjis, icd503, cnss1253
    severity    TEXT DEFAULT 'CAT2',           -- CAT1, CAT2, CAT3
    title       TEXT NOT NULL,
    description TEXT,
    affected_entity TEXT,                      -- node/edge ID or label
    affected_type TEXT,                        -- node, edge, topology
    status      TEXT DEFAULT 'open',           -- open, remediated, accepted_risk, false_positive
    fix_action  TEXT,                          -- JSON: {action, params} for one-click fix
    remediated_at TEXT,
    created_at  TEXT DEFAULT CURRENT_TIMESTAMP
);

-- Projects: group multiple topologies, circuits, IPAM under one engagement
CREATE TABLE IF NOT EXISTS nc_projects (
    id          TEXT PRIMARY KEY,
    name        TEXT NOT NULL,
    customer_id TEXT REFERENCES nc_customers(id),
    description TEXT,
    status      TEXT DEFAULT 'draft',  -- draft, in_review, approved, deployed, archived
    owner       TEXT,
    created_at  TEXT DEFAULT CURRENT_TIMESTAMP,
    updated_at  TEXT DEFAULT CURRENT_TIMESTAMP
);

-- Link topologies to projects (many-to-many)
CREATE TABLE IF NOT EXISTS nc_project_topologies (
    project_id  TEXT REFERENCES nc_projects(id),
    topology_id TEXT REFERENCES topologies(id),
    PRIMARY KEY (project_id, topology_id)
);

-- CSP Group Containers (nestable visual containers)
CREATE TABLE IF NOT EXISTS nc_groups (
    id          TEXT PRIMARY KEY,
    topology_id TEXT REFERENCES topologies(id),
    parent_id   TEXT,                   -- NULL = top-level, else nested inside parent group
    csp         TEXT,                   -- aws, azure, gcp, oci, ibm, custom
    group_type  TEXT DEFAULT 'full',    -- full (with components), outline (high-level only)
    label       TEXT NOT NULL,
    description TEXT,
    auto_nodes_json TEXT DEFAULT '[]',  -- auto-populated node IDs when group_type=full
    pos_x       REAL DEFAULT 0,
    pos_y       REAL DEFAULT 0,
    width       REAL DEFAULT 400,
    height      REAL DEFAULT 300,
    color       TEXT,                   -- override color (default from CSP)
    collapsed   INTEGER DEFAULT 0,     -- 1 = collapsed (hide children)
    created_at  TEXT DEFAULT CURRENT_TIMESTAMP
);

-- Monte Carlo simulation scenarios
CREATE TABLE IF NOT EXISTS nc_mc_scenarios (
    id          TEXT PRIMARY KEY,
    topology_id TEXT REFERENCES topologies(id),
    name        TEXT NOT NULL,
    scenario_type TEXT DEFAULT 'random', -- random, named, circuit_change
    description TEXT,
    config_json TEXT DEFAULT '{}',       -- iterations, failure_probs, named_failures, etc.
    created_at  TEXT DEFAULT CURRENT_TIMESTAMP
);

-- Monte Carlo simulation runs (each run = N iterations)
CREATE TABLE IF NOT EXISTS nc_mc_runs (
    id          TEXT PRIMARY KEY,
    scenario_id TEXT REFERENCES nc_mc_scenarios(id),
    topology_id TEXT REFERENCES topologies(id),
    iterations  INTEGER DEFAULT 1000,
    result_json TEXT DEFAULT '{}',       -- risk_score, confidence_intervals, cascading_effects, etc.
    ai_recommendations TEXT,             -- AI-generated resilience recommendations
    ran_at      TEXT DEFAULT CURRENT_TIMESTAMP
);

-- Backup registry (point-in-time restore)
CREATE TABLE IF NOT EXISTS nc_backups (
    id          TEXT PRIMARY KEY,
    backup_type TEXT DEFAULT 'manual',   -- manual, scheduled
    file_path   TEXT NOT NULL,
    file_size_bytes INTEGER DEFAULT 0,
    includes_json TEXT DEFAULT '[]',      -- ["icdev.db", "network_canvas.db", "args/", ...]
    notes       TEXT,
    created_at  TEXT DEFAULT CURRENT_TIMESTAMP
);

-- ATO Package artifacts generated from topology regions
CREATE TABLE IF NOT EXISTS nc_ato_packages (
    id          TEXT PRIMARY KEY,
    topology_id TEXT REFERENCES topologies(id),
    region_id   TEXT,                   -- NULL = full topology, else nc_groups.id
    system_name TEXT NOT NULL,
    classification TEXT DEFAULT 'CUI',
    regimes     TEXT DEFAULT '["fisma_high","stig"]',  -- JSON array
    package_json TEXT NOT NULL DEFAULT '{}',            -- full generated package
    summary_json TEXT NOT NULL DEFAULT '{}',            -- readiness summary
    overall_readiness TEXT DEFAULT 'RED',               -- GREEN, YELLOW, RED
    stig_pass_rate REAL DEFAULT 0,
    compliance_score REAL DEFAULT 0,
    created_by  TEXT,
    created_at  TEXT DEFAULT CURRENT_TIMESTAMP
);

-- NC-GAP-001: User authentication
CREATE TABLE IF NOT EXISTS nc_users (
    id          TEXT PRIMARY KEY,
    username    TEXT UNIQUE NOT NULL,
    display_name TEXT,
    password_hash TEXT NOT NULL,
    role        TEXT DEFAULT 'editor',  -- viewer, editor, admin
    is_active   INTEGER DEFAULT 1,
    last_login  TEXT,
    created_at  TEXT DEFAULT CURRENT_TIMESTAMP
);

-- STIG XCCDF/CKL import records (links external scan results to topology devices)
CREATE TABLE IF NOT EXISTS nc_stig_imports (
    id          TEXT PRIMARY KEY,
    topology_id TEXT REFERENCES topologies(id),
    filename    TEXT NOT NULL,
    format      TEXT NOT NULL,           -- ckl, xccdf
    stig_name   TEXT,
    stig_version TEXT,
    total_hosts INTEGER DEFAULT 0,
    matched_hosts INTEGER DEFAULT 0,
    result_json TEXT DEFAULT '{}',       -- full match/color result
    imported_at TEXT DEFAULT CURRENT_TIMESTAMP
);

-- Security Boundary Auto-Fencing (enclave zones drawn around devices)
CREATE TABLE IF NOT EXISTS nc_boundaries (
    id              TEXT PRIMARY KEY,
    topology_id     TEXT REFERENCES topologies(id),
    label           TEXT NOT NULL DEFAULT 'Enclave',
    classification  TEXT DEFAULT 'CUI',           -- CUI, SECRET, TOP SECRET, PUBLIC
    color           TEXT DEFAULT '#e94560',        -- zone border/fill color
    fill_opacity    REAL DEFAULT 0.08,
    node_ids        TEXT DEFAULT '[]',             -- JSON array of contained node IDs
    stig_tags       TEXT DEFAULT '[]',             -- JSON array of auto-generated STIG boundary tags
    pos_x           REAL DEFAULT 0,
    pos_y           REAL DEFAULT 0,
    width           REAL DEFAULT 400,
    height          REAL DEFAULT 300,
    snap_grid       INTEGER DEFAULT 10,            -- snap-to-grid size
    notes           TEXT,
    created_at      TEXT DEFAULT CURRENT_TIMESTAMP,
    updated_at      TEXT DEFAULT CURRENT_TIMESTAMP
);

-- Intent-Based Validation: user-defined constraint policies
CREATE TABLE IF NOT EXISTS nc_intent_policies (
    id          TEXT PRIMARY KEY,
    topology_id TEXT REFERENCES topologies(id),
    name        TEXT NOT NULL,
    description TEXT,
    is_active   INTEGER DEFAULT 1,
    created_at  TEXT DEFAULT CURRENT_TIMESTAMP,
    updated_at  TEXT DEFAULT CURRENT_TIMESTAMP
);

-- Individual constraints within an intent policy
CREATE TABLE IF NOT EXISTS nc_intent_constraints (
    id          TEXT PRIMARY KEY,
    policy_id   TEXT REFERENCES nc_intent_policies(id),
    constraint_type TEXT NOT NULL,  -- bandwidth, redundancy, isolation, latency, encryption, custom
    severity    TEXT DEFAULT 'CAT2',  -- CAT1, CAT2, CAT3
    rule_json   TEXT NOT NULL DEFAULT '{}',
    description TEXT,
    is_active   INTEGER DEFAULT 1,
    created_at  TEXT DEFAULT CURRENT_TIMESTAMP
);

-- Validation run results against intent policies
CREATE TABLE IF NOT EXISTS nc_intent_validations (
    id          TEXT PRIMARY KEY,
    topology_id TEXT REFERENCES topologies(id),
    policy_id   TEXT REFERENCES nc_intent_policies(id),
    total_constraints INTEGER DEFAULT 0,
    passed      INTEGER DEFAULT 0,
    failed      INTEGER DEFAULT 0,
    violations_json TEXT DEFAULT '[]',
    ran_at      TEXT DEFAULT CURRENT_TIMESTAMP
);

-- Change Request Markup Mode — CAB review workflow
CREATE TABLE IF NOT EXISTS nc_change_requests (
    id              TEXT PRIMARY KEY,
    topology_id     TEXT REFERENCES topologies(id),
    title           TEXT NOT NULL,
    description     TEXT,
    status          TEXT DEFAULT 'draft',  -- draft|submitted|approved|rejected|withdrawn
    submitter_name  TEXT,
    submitted_at    TEXT,
    document_json   TEXT DEFAULT '{}',
    created_at      TEXT DEFAULT CURRENT_TIMESTAMP,
    updated_at      TEXT DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS nc_change_request_items (
    id              TEXT PRIMARY KEY,
    cr_id           TEXT REFERENCES nc_change_requests(id) ON DELETE CASCADE,
    topology_id     TEXT REFERENCES topologies(id),
    action_type     TEXT NOT NULL,  -- add|remove|modify
    entity_id       TEXT NOT NULL,  -- node/edge ID on canvas
    entity_type     TEXT DEFAULT 'node',  -- node|edge|group|boundary
    entity_label    TEXT,
    before_json     TEXT DEFAULT '{}',
    after_json      TEXT DEFAULT '{}',
    justification   TEXT,
    created_by      TEXT,
    created_at      TEXT DEFAULT CURRENT_TIMESTAMP
);

-- NC-GAP-009: Classification on audit + immutability triggers
-- (classification column added inline above)

-- NC-GAP-013: Audit trail immutability triggers
-- NOTE: These triggers use SQLite-specific syntax (SELECT RAISE).
-- For PostgreSQL, equivalent triggers are created in init_db() via PL/pgSQL.

-- ── NetBox IPAM Integration ────────────────────────────────────────────────

-- NetBox connection configuration (one row per canvas instance)
CREATE TABLE IF NOT EXISTS nc_netbox_config (
    id          TEXT PRIMARY KEY DEFAULT 'default',
    url         TEXT NOT NULL DEFAULT '',
    token       TEXT NOT NULL DEFAULT '',
    site_filter TEXT DEFAULT '',        -- optional site slug to limit pulls
    timeout_sec INTEGER DEFAULT 15,
    auto_sync   INTEGER DEFAULT 0,      -- 1 = sync on canvas load
    last_tested TEXT,                   -- ISO timestamp of last successful test
    created_at  TEXT DEFAULT CURRENT_TIMESTAMP,
    updated_at  TEXT DEFAULT CURRENT_TIMESTAMP
);

-- Sync run history
CREATE TABLE IF NOT EXISTS nc_netbox_sync_log (
    id          TEXT PRIMARY KEY,
    direction   TEXT NOT NULL,          -- pull | push
    resource    TEXT NOT NULL,          -- devices | ips | vlans | racks | circuits | all
    topology_id TEXT REFERENCES topologies(id),
    status      TEXT DEFAULT 'ok',      -- ok | error
    records_in  INTEGER DEFAULT 0,      -- records received from NetBox
    records_out INTEGER DEFAULT 0,      -- records written to canvas
    error_msg   TEXT,
    ran_at      TEXT DEFAULT CURRENT_TIMESTAMP
);

-- Object ID mapping: NetBox ID ↔ canvas node ID
CREATE TABLE IF NOT EXISTS nc_netbox_objects (
    id              TEXT PRIMARY KEY,
    topology_id     TEXT REFERENCES topologies(id),
    netbox_id       INTEGER NOT NULL,
    netbox_resource TEXT NOT NULL,      -- device | ip-address | vlan | rack | circuit
    canvas_node_id  TEXT,               -- NULL until placed on canvas
    last_synced     TEXT DEFAULT CURRENT_TIMESTAMP
);

-- Auto-discovery scan results
CREATE TABLE IF NOT EXISTS nc_discovery_scans (
    id              TEXT PRIMARY KEY,
    topology_id     TEXT REFERENCES topologies(id),  -- NULL if standalone scan
    name            TEXT NOT NULL,
    method          TEXT NOT NULL DEFAULT 'snmp',     -- snmp, ssh, ping
    targets         TEXT NOT NULL DEFAULT '[]',       -- JSON array of IPs/CIDRs
    config_json     TEXT DEFAULT '{}',                -- community, credentials ref, etc.
    status          TEXT DEFAULT 'pending',           -- pending, running, completed, failed
    devices_json    TEXT DEFAULT '[]',                -- full discovery records
    graph_json      TEXT NOT NULL DEFAULT '{"nodes":[],"edges":[]}',
    stats_json      TEXT DEFAULT '{}',
    error           TEXT,
    started_at      TEXT,
    completed_at    TEXT,
    created_at      TEXT DEFAULT CURRENT_TIMESTAMP
);

-- Discovery diff results (as-designed vs as-built)
CREATE TABLE IF NOT EXISTS nc_discovery_diffs (
    id              TEXT PRIMARY KEY,
    scan_id         TEXT REFERENCES nc_discovery_scans(id),
    topology_id     TEXT REFERENCES topologies(id),
    diff_json       TEXT NOT NULL DEFAULT '{}',       -- full diff output
    drift_score     REAL DEFAULT 0,                   -- 0-100 percentage
    matched         INTEGER DEFAULT 0,
    designed_only   INTEGER DEFAULT 0,
    discovered_only INTEGER DEFAULT 0,
    with_drift      INTEGER DEFAULT 0,
    created_at      TEXT DEFAULT CURRENT_TIMESTAMP
);

-- ── P3: Project Milestones ────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS nc_project_milestones (
    id          TEXT PRIMARY KEY,
    project_id  TEXT REFERENCES nc_projects(id) ON DELETE CASCADE,
    title       TEXT NOT NULL,
    due_date    TEXT,              -- ISO date (YYYY-MM-DD)
    status      TEXT DEFAULT 'pending',  -- pending, in_progress, completed, missed
    predecessor_id TEXT,           -- dependency: must complete before this one
    notes       TEXT,
    created_at  TEXT DEFAULT CURRENT_TIMESTAMP
);

-- ── P3: Project Notes / Comments ──────────────────────────────────────────
CREATE TABLE IF NOT EXISTS nc_project_notes (
    id          TEXT PRIMARY KEY,
    project_id  TEXT REFERENCES nc_projects(id) ON DELETE CASCADE,
    author      TEXT,
    body        TEXT NOT NULL,
    created_at  TEXT DEFAULT CURRENT_TIMESTAMP
);

-- ── P3: Tags (polymorphic — projects and topologies) ──────────────────────
CREATE TABLE IF NOT EXISTS nc_tags (
    id          TEXT PRIMARY KEY,
    entity_type TEXT NOT NULL,     -- project, topology
    entity_id   TEXT NOT NULL,
    tag         TEXT NOT NULL,
    created_at  TEXT DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(entity_type, entity_id, tag)
);

-- ── P3: Project Templates (save reusable project structures) ──────────────
CREATE TABLE IF NOT EXISTS nc_project_templates (
    id          TEXT PRIMARY KEY,
    name        TEXT NOT NULL,
    description TEXT,
    structure_json TEXT NOT NULL DEFAULT '{}',  -- snapshot of project + topos
    created_by  TEXT,
    created_at  TEXT DEFAULT CURRENT_TIMESTAMP
);

-- ── Phase A: Review Board Pipeline ────────────────────────────────────────
CREATE TABLE IF NOT EXISTS nc_review_boards (
    id          TEXT PRIMARY KEY,
    name        TEXT NOT NULL,
    short_name  TEXT NOT NULL,
    description TEXT,
    required_for_status TEXT,
    is_optional INTEGER DEFAULT 0,    -- 0 = required, 1 = optional (team decides)
    sort_order  INTEGER DEFAULT 0,
    created_at  TEXT DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS nc_board_reviews (
    id              TEXT PRIMARY KEY,
    project_id      TEXT REFERENCES nc_projects(id) ON DELETE CASCADE,
    board_id        TEXT REFERENCES nc_review_boards(id),
    phase           INTEGER DEFAULT 1,
    status          TEXT DEFAULT 'pending',
    scheduled_date  TEXT,
    presented_date  TEXT,
    decision        TEXT,
    decision_notes  TEXT,
    conditions      TEXT DEFAULT '[]',
    reviewer_names  TEXT DEFAULT '[]',
    package_json    TEXT DEFAULT '{}',
    created_at      TEXT DEFAULT CURRENT_TIMESTAMP,
    updated_at      TEXT DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS nc_safe_bridge (
    id              TEXT PRIMARY KEY,
    project_id      TEXT REFERENCES nc_projects(id) ON DELETE CASCADE,
    session_id      TEXT,
    safe_feature_id TEXT,
    roi_json        TEXT DEFAULT '{}',
    justification   TEXT,
    alternatives    TEXT,
    created_at      TEXT DEFAULT CURRENT_TIMESTAMP,
    updated_at      TEXT DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS nc_project_phases (
    id          TEXT PRIMARY KEY,
    project_id  TEXT REFERENCES nc_projects(id) ON DELETE CASCADE,
    phase_num   INTEGER NOT NULL,
    phase_name  TEXT NOT NULL,
    status      TEXT DEFAULT 'pending',
    entered_at  TEXT,
    completed_at TEXT,
    created_at  TEXT DEFAULT CURRENT_TIMESTAMP
);

-- ── Charts Data (P2) ──────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS nc_compliance_history (
    id          TEXT PRIMARY KEY,
    project_id  TEXT REFERENCES nc_projects(id) ON DELETE CASCADE,
    compliance_pct INTEGER,
    open_findings INTEGER DEFAULT 0,
    cat1_count  INTEGER DEFAULT 0,
    recorded_at TEXT DEFAULT CURRENT_TIMESTAMP
);

-- ── Alert Thresholds (P1) ─────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS nc_alert_rules (
    id          TEXT PRIMARY KEY,
    name        TEXT NOT NULL,
    metric      TEXT NOT NULL,            -- peering_utilization, compliance_pct, eol_months, port_utilization, power_pct, rack_utilization
    operator    TEXT DEFAULT 'gt',        -- gt, lt, eq, gte, lte
    threshold   REAL NOT NULL,
    severity    TEXT DEFAULT 'warning',   -- info, warning, critical
    enabled     INTEGER DEFAULT 1,
    created_at  TEXT DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS nc_alert_events (
    id          TEXT PRIMARY KEY,
    rule_id     TEXT REFERENCES nc_alert_rules(id),
    rule_name   TEXT,
    severity    TEXT,
    message     TEXT NOT NULL,
    entity_type TEXT,
    entity_id   TEXT,
    acknowledged INTEGER DEFAULT 0,
    created_at  TEXT DEFAULT CURRENT_TIMESTAMP
);

-- ── Favorites/Pinned Views (P2) ──────────────────────────────────────────
CREATE TABLE IF NOT EXISTS nc_favorites (
    id          TEXT PRIMARY KEY,
    entity_type TEXT NOT NULL,            -- project, topology, peering
    entity_id   TEXT NOT NULL,
    label       TEXT,
    user_id     TEXT,
    created_at  TEXT DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(entity_type, entity_id, user_id)
);

-- ── Peering Agreements ────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS nc_peering_agreements (
    id              TEXT PRIMARY KEY,
    peer_name       TEXT NOT NULL,
    peer_asn        TEXT,
    our_asn         TEXT,
    peering_type    TEXT DEFAULT 'settlement_free',  -- settlement_free, paid, transit, partial_transit
    routing_method  TEXT DEFAULT 'bgp',              -- bgp, static, ospf, isis, pbr, l2, none
    status          TEXT DEFAULT 'evaluation',       -- evaluation, negotiation, agreement_signed, technical_design, implemented, operational, decommissioned
    purpose         TEXT,                            -- business reason
    purpose_category TEXT DEFAULT 'connectivity',    -- connectivity, cost_optimization, redundancy, cloud_onramp, customer, partner, regulatory, content_delivery
    business_justification TEXT,
    locations       TEXT DEFAULT '[]',               -- JSON: IXPs/facilities
    port_speed      TEXT,                            -- 1G, 10G, 100G
    contract_start  TEXT,
    contract_end    TEXT,
    monthly_cost    REAL DEFAULT 0,
    traffic_commit  TEXT,                            -- e.g., "10Gbps commit"
    ratio_limit     TEXT,                            -- e.g., "2:1 max"
    sla_latency_ms  REAL,
    sla_packet_loss REAL,
    sla_uptime_pct  REAL DEFAULT 99.9,
    noc_contact     TEXT,
    noc_email       TEXT,
    noc_phone       TEXT,
    legal_entity    TEXT,
    notes           TEXT,
    project_id      TEXT REFERENCES nc_projects(id),
    created_at      TEXT DEFAULT CURRENT_TIMESTAMP,
    updated_at      TEXT DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS nc_peering_sessions (
    id              TEXT PRIMARY KEY,
    agreement_id    TEXT REFERENCES nc_peering_agreements(id) ON DELETE CASCADE,
    location        TEXT NOT NULL,                   -- IXP name or facility
    routing_method  TEXT DEFAULT 'bgp',              -- bgp, static, ospf, isis, l2
    our_ip          TEXT,
    peer_ip         TEXT,
    our_ipv6        TEXT,
    peer_ipv6       TEXT,
    our_asn         TEXT,
    peer_asn        TEXT,
    prefix_limit    INTEGER,
    md5_enabled     INTEGER DEFAULT 0,
    local_pref      INTEGER,
    med             INTEGER,
    communities     TEXT DEFAULT '[]',               -- JSON
    static_routes   TEXT DEFAULT '[]',               -- JSON: [{prefix, next_hop}] for static routing
    status          TEXT DEFAULT 'planned',           -- planned, configured, up, down, decommissioned
    port_speed      TEXT,
    notes           TEXT,
    created_at      TEXT DEFAULT CURRENT_TIMESTAMP
);

-- ── Partner Registry ──────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS nc_partners (
    id               TEXT PRIMARY KEY,
    name             TEXT NOT NULL,
    partner_type     TEXT NOT NULL DEFAULT 'isp'
                         @@CK1@@,
    asn              INTEGER,
    noc_email        TEXT DEFAULT '',
    noc_phone        TEXT DEFAULT '',
    legal_entity     TEXT DEFAULT '',
    contract_manager TEXT DEFAULT '',
    status           TEXT NOT NULL DEFAULT 'active'
                         @@CK2@@,
    notes            TEXT DEFAULT '',
    classification   TEXT DEFAULT 'CUI',
    created_at       TEXT DEFAULT CURRENT_TIMESTAMP,
    updated_at       TEXT DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_nc_partners_status ON nc_partners(status);
CREATE INDEX IF NOT EXISTS idx_nc_partners_asn    ON nc_partners(asn);

-- ── Agreement Amendments (APPEND-ONLY) ────────────────────────────────────
CREATE TABLE IF NOT EXISTS nc_agreement_amendments (
    id               TEXT PRIMARY KEY,
    agreement_id     TEXT NOT NULL REFERENCES nc_peering_agreements(id) ON DELETE CASCADE,
    amendment_number INTEGER NOT NULL DEFAULT 1,
    changes_json     TEXT NOT NULL DEFAULT '{}',
    amended_by       TEXT NOT NULL DEFAULT '',
    reason           TEXT DEFAULT '',
    effective_date   TEXT DEFAULT '',
    classification   TEXT DEFAULT 'CUI',
    created_at       TEXT DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_nc_agreement_amendments_agreement ON nc_agreement_amendments(agreement_id);

CREATE TABLE IF NOT EXISTS nc_peering_traffic (
    id              TEXT PRIMARY KEY,
    session_id      TEXT REFERENCES nc_peering_sessions(id) ON DELETE CASCADE,
    inbound_mbps    REAL DEFAULT 0,
    outbound_mbps   REAL DEFAULT 0,
    ratio           REAL DEFAULT 1.0,                -- inbound/outbound
    measurement     TEXT DEFAULT 'peak',              -- peak, average, 95th
    measured_at     TEXT DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS nc_peering_evaluations (
    id              TEXT PRIMARY KEY,
    peer_name       TEXT NOT NULL,
    peer_asn        TEXT,
    traffic_volume  REAL DEFAULT 0,                  -- Mbps exchanged via transit today
    geographic_overlap TEXT DEFAULT 'medium',          -- low, medium, high
    noc_quality     TEXT DEFAULT 'unknown',            -- poor, fair, good, excellent, unknown
    network_capacity TEXT DEFAULT 'unknown',
    prefix_count    INTEGER DEFAULT 0,
    peering_policy  TEXT,                             -- open, selective, restrictive
    score           REAL DEFAULT 0,                   -- auto-computed
    recommendation  TEXT DEFAULT 'evaluate',           -- peer, defer, decline
    notes           TEXT,
    created_at      TEXT DEFAULT CURRENT_TIMESTAMP
);

-- ── Capacity Planning (Port/Slot/Fiber/Circuit) ──────────────────────────
CREATE TABLE IF NOT EXISTS nc_port_inventory (
    id              TEXT PRIMARY KEY,
    device_label    TEXT NOT NULL,
    topology_id     TEXT REFERENCES topologies(id),
    total_ports     INTEGER DEFAULT 0,
    used_ports      INTEGER DEFAULT 0,
    port_breakdown  TEXT DEFAULT '{}',               -- JSON: {"1G": {total:24, used:18}, "10G": {total:4, used:2}}
    last_updated    TEXT DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS nc_module_inventory (
    id              TEXT PRIMARY KEY,
    device_label    TEXT NOT NULL,
    topology_id     TEXT REFERENCES topologies(id),
    slot_number     TEXT NOT NULL,
    module_type     TEXT,                            -- empty, 4x10G, 2x100G, 48x1G, etc.
    is_empty        INTEGER DEFAULT 1,
    compatible_modules TEXT DEFAULT '[]',             -- JSON: what can go in this slot
    notes           TEXT,
    created_at      TEXT DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS nc_fiber_inventory (
    id              TEXT PRIMARY KEY,
    path_name       TEXT NOT NULL,                   -- e.g., "DC-East to DC-West"
    path_a          TEXT,                            -- site/facility A
    path_z          TEXT,                            -- site/facility Z
    fiber_type      TEXT DEFAULT 'SMF',              -- SMF, MMF
    total_strands   INTEGER DEFAULT 0,
    lit_strands     INTEGER DEFAULT 0,
    available_strands INTEGER DEFAULT 0,
    total_lambdas   INTEGER DEFAULT 0,               -- DWDM wavelengths
    active_lambdas  INTEGER DEFAULT 0,
    available_lambdas INTEGER DEFAULT 0,
    per_lambda_gbps REAL DEFAULT 100,                -- capacity per wavelength
    conduit_ducts   INTEGER DEFAULT 0,
    conduit_used    INTEGER DEFAULT 0,
    diverse_path    INTEGER DEFAULT 0,               -- 1 = confirmed physically diverse
    notes           TEXT,
    created_at      TEXT DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS nc_carrier_availability (
    id              TEXT PRIMARY KEY,
    carrier         TEXT NOT NULL,
    path_name       TEXT,                            -- same path reference as fiber_inventory
    service_type    TEXT,                            -- DIA, MPLS, wavelength, dark_fiber, Ethernet
    available_bandwidth TEXT,                         -- e.g., "up to 100G"
    lead_time_days  INTEGER DEFAULT 30,
    monthly_cost_est REAL DEFAULT 0,
    contract_term   TEXT,                            -- e.g., "12 months"
    notes           TEXT,
    created_at      TEXT DEFAULT CURRENT_TIMESTAMP
);

-- ── Facilities / DCIM ────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS nc_facilities (
    id              TEXT PRIMARY KEY,
    name            TEXT NOT NULL,
    facility_type   TEXT DEFAULT 'datacenter',        -- datacenter, colo, office, pop, hub
    address         TEXT,
    city            TEXT,
    state           TEXT,
    country         TEXT DEFAULT 'US',
    operator        TEXT,                            -- Equinix, QTS, CyrusOne, self-operated
    total_racks     INTEGER DEFAULT 0,
    used_racks      INTEGER DEFAULT 0,
    total_power_kw  REAL DEFAULT 0,
    used_power_kw   REAL DEFAULT 0,
    total_cooling_tons REAL DEFAULT 0,
    used_cooling_tons REAL DEFAULT 0,
    ups_capacity_kva REAL DEFAULT 0,
    ups_load_kva    REAL DEFAULT 0,
    ups_runtime_min REAL DEFAULT 15,
    generator_kw    REAL DEFAULT 0,
    generator_load_kw REAL DEFAULT 0,
    generator_fuel_hours REAL DEFAULT 0,
    notes           TEXT,
    created_at      TEXT DEFAULT CURRENT_TIMESTAMP,
    updated_at      TEXT DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS nc_racks (
    id              TEXT PRIMARY KEY,
    facility_id     TEXT REFERENCES nc_facilities(id) ON DELETE CASCADE,
    rack_name       TEXT NOT NULL,                   -- e.g., "Row-A Rack-12"
    total_ru        INTEGER DEFAULT 42,
    used_ru         INTEGER DEFAULT 0,
    reserved_ru     INTEGER DEFAULT 0,
    power_circuit_a TEXT,                            -- PDU A feed
    power_circuit_b TEXT,                            -- PDU B feed
    max_power_kw    REAL DEFAULT 5.0,
    current_power_kw REAL DEFAULT 0,
    weight_capacity_lbs REAL DEFAULT 2500,
    current_weight_lbs REAL DEFAULT 0,
    notes           TEXT,
    created_at      TEXT DEFAULT CURRENT_TIMESTAMP
);

-- ── NDC Case Workflow (Phase 4) ────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS nc_case_workflows (
    id          TEXT PRIMARY KEY,
    project_id  TEXT REFERENCES nc_projects(id) ON DELETE CASCADE,
    current_state TEXT DEFAULT 'concept',
    lifecycle_json TEXT NOT NULL,
    created_at  TEXT DEFAULT CURRENT_TIMESTAMP,
    updated_at  TEXT DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS nc_case_history (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    workflow_id TEXT REFERENCES nc_case_workflows(id),
    from_state  TEXT NOT NULL,
    to_state    TEXT NOT NULL,
    changed_by  TEXT,
    comment     TEXT,
    changed_at  TEXT DEFAULT CURRENT_TIMESTAMP
);

-- ── Device Command Profiles (Phase 2 Network Intelligence) ───────────────
CREATE TABLE IF NOT EXISTS nc_device_profiles (
    id          TEXT PRIMARY KEY,
    vendor      TEXT NOT NULL,
    platform    TEXT NOT NULL,           -- IOS, IOS-XE, NX-OS, EOS, JunOS, PAN-OS, FortiOS, etc.
    description TEXT,
    commands_json TEXT NOT NULL DEFAULT '{}',  -- {command_name: {command, parser, timeout_sec}}
    is_builtin  INTEGER DEFAULT 0,
    created_by  TEXT,
    created_at  TEXT DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS nc_hardware_profiles (
    id                  TEXT PRIMARY KEY,
    vendor              TEXT NOT NULL,
    model               TEXT NOT NULL,
    model_family        TEXT,
    device_type         TEXT NOT NULL,
    form_factor         TEXT DEFAULT 'rack',
    rack_units          INTEGER DEFAULT 1,
    weight_kg           REAL,
    depth_mm            INTEGER,
    width_mm            INTEGER,
    height_mm           INTEGER,
    power_typical_w     INTEGER,
    power_max_w         INTEGER,
    psu_count           INTEGER DEFAULT 2,
    psu_type            TEXT,
    operating_temp_min_c INTEGER DEFAULT 0,
    operating_temp_max_c INTEGER DEFAULT 40,
    humidity_min_pct    INTEGER DEFAULT 10,
    humidity_max_pct    INTEGER DEFAULT 85,
    airflow_direction   TEXT,
    altitude_max_m      INTEGER DEFAULT 3000,
    acoustic_dba        REAL,
    throughput_gbps     REAL,
    pps_mpps            REAL,
    routing_table_size  INTEGER,
    arp_table_size      INTEGER,
    mac_table_size      INTEGER,
    nat_sessions        INTEGER,
    vpn_tunnels         INTEGER,
    vlan_count          INTEGER,
    ports_json          TEXT DEFAULT '[]',
    components_json     TEXT DEFAULT '[]',
    mgmt_ports_json     TEXT DEFAULT '[]',
    os_options          TEXT DEFAULT '[]',
    license_model       TEXT,
    eol_date            TEXT,
    eos_date            TEXT,
    replacement_cost    REAL,
    annual_maintenance_pct REAL DEFAULT 0.15,
    datasheet_url       TEXT,
    image_url           TEXT,
    tags                TEXT DEFAULT '[]',
    is_builtin          INTEGER DEFAULT 0,
    created_by          TEXT,
    created_at          TEXT DEFAULT CURRENT_TIMESTAMP,
    updated_at          TEXT DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(vendor, model)
);

CREATE TABLE IF NOT EXISTS nc_naming_conventions (
    id              TEXT PRIMARY KEY,
    name            TEXT NOT NULL UNIQUE,
    description     TEXT,
    pattern         TEXT NOT NULL,
    fields_json     TEXT NOT NULL DEFAULT '[]',
    separator       TEXT DEFAULT '',
    max_length      INTEGER DEFAULT 63,
    case_rule       TEXT DEFAULT 'upper',
    example         TEXT,
    is_builtin      INTEGER DEFAULT 0,
    created_at      TEXT DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS nc_naming_sequences (
    id              TEXT PRIMARY KEY,
    convention_id   TEXT REFERENCES nc_naming_conventions(id),
    scope_key       TEXT NOT NULL,
    topology_id     TEXT,
    current_value   INTEGER DEFAULT 0,
    UNIQUE(convention_id, scope_key, topology_id)
);

CREATE TABLE IF NOT EXISTS nc_discovery_configs (
    id              TEXT PRIMARY KEY,
    name            TEXT NOT NULL,
    profile_id      TEXT REFERENCES nc_device_profiles(id),
    targets         TEXT NOT NULL DEFAULT '[]',  -- JSON array of IPs/CIDRs
    credential_ref  TEXT,                        -- reference to credential store (never plaintext)
    method          TEXT DEFAULT 'ssh',           -- ssh, snmp, ping
    read_only       INTEGER DEFAULT 1,            -- MUST be 1 for non-intrusive
    rate_limit_per_sec REAL DEFAULT 1.0,
    max_concurrent  INTEGER DEFAULT 5,
    timeout_per_cmd INTEGER DEFAULT 10,
    timeout_per_device INTEGER DEFAULT 60,
    hop_limit       INTEGER DEFAULT 2,
    max_devices     INTEGER DEFAULT 100,
    whitelist_subnets TEXT DEFAULT '[]',
    blacklist_subnets TEXT DEFAULT '[]',
    created_at      TEXT DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS nc_collected_configs (
    id          TEXT PRIMARY KEY,
    device_ip   TEXT NOT NULL,
    hostname    TEXT,
    profile_id  TEXT REFERENCES nc_device_profiles(id),
    command_name TEXT NOT NULL,
    output_text TEXT NOT NULL,
    parsed_json TEXT DEFAULT '{}',
    collected_at TEXT DEFAULT CURRENT_TIMESTAMP,
    topology_id TEXT REFERENCES topologies(id)
);

-- ── Innovation Flywheel (Phase 7 Network Intelligence) ───────────────────
CREATE TABLE IF NOT EXISTS nc_innovation_ideas (
    id          TEXT PRIMARY KEY,
    title       TEXT NOT NULL,
    description TEXT,
    category    TEXT DEFAULT 'improvement', -- improvement, cost_reduction, security, automation, new_capability
    submitted_by TEXT,
    impact_score INTEGER DEFAULT 0,         -- 1-10
    feasibility_score INTEGER DEFAULT 0,    -- 1-10
    cost_score  INTEGER DEFAULT 0,          -- 1-10 (10 = cheapest)
    total_score REAL DEFAULT 0,             -- auto-computed weighted
    status      TEXT DEFAULT 'submitted',   -- submitted, under_review, approved, in_progress, completed, rejected
    project_id  TEXT REFERENCES nc_projects(id),
    votes       INTEGER DEFAULT 0,
    created_at  TEXT DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS nc_tech_radar (
    id          TEXT PRIMARY KEY,
    technology  TEXT NOT NULL,
    ring        TEXT DEFAULT 'assess',      -- adopt, trial, assess, hold
    category    TEXT DEFAULT 'networking',   -- networking, security, cloud, automation, observability
    description TEXT,
    moved_from  TEXT,                        -- previous ring (for tracking movement)
    updated_by  TEXT,
    created_at  TEXT DEFAULT CURRENT_TIMESTAMP,
    updated_at  TEXT DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS nc_lessons_learned (
    id          TEXT PRIMARY KEY,
    project_id  TEXT REFERENCES nc_projects(id),
    title       TEXT NOT NULL,
    category    TEXT DEFAULT 'technical',    -- technical, process, communication, tooling
    what_happened TEXT,
    root_cause  TEXT,
    lesson      TEXT,
    recommendation TEXT,
    submitted_by TEXT,
    created_at  TEXT DEFAULT CURRENT_TIMESTAMP
);

-- ── Tech Refresh (Phase 6 Network Intelligence) ──────────────────────────
CREATE TABLE IF NOT EXISTS nc_replacement_map (
    id          TEXT PRIMARY KEY,
    old_vendor  TEXT NOT NULL,
    old_model   TEXT NOT NULL,
    new_vendor  TEXT NOT NULL,
    new_model   TEXT NOT NULL,
    new_cost    REAL DEFAULT 0,
    migration_effort TEXT DEFAULT 'medium', -- low, medium, high
    notes       TEXT,
    is_builtin  INTEGER DEFAULT 0,
    created_at  TEXT DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS nc_refresh_plans (
    id          TEXT PRIMARY KEY,
    project_id  TEXT REFERENCES nc_projects(id) ON DELETE CASCADE,
    device_label TEXT NOT NULL,
    old_model   TEXT,
    eol_date    TEXT,
    priority    TEXT DEFAULT 'medium',    -- critical, high, medium, low
    replacement_model TEXT,
    replacement_cost REAL DEFAULT 0,
    target_year INTEGER,                  -- fiscal year for budget
    status      TEXT DEFAULT 'planned',   -- planned, budgeted, ordered, completed
    notes       TEXT,
    created_at  TEXT DEFAULT CURRENT_TIMESTAMP
);

-- ── Device Geolocation (Phase 4 Network Intelligence) ────────────────────
CREATE TABLE IF NOT EXISTS nc_device_geo (
    id          TEXT PRIMARY KEY,
    topology_id TEXT REFERENCES topologies(id),
    node_id     TEXT,
    label       TEXT,
    site_name   TEXT,
    latitude    REAL,
    longitude   REAL,
    city        TEXT,
    state       TEXT,
    country     TEXT DEFAULT 'US',
    facility    TEXT,                     -- data center, colo, office
    created_at  TEXT DEFAULT CURRENT_TIMESTAMP
);

-- ── Routing Table Entries (Phase 3 Network Intelligence) ─────────────────
CREATE TABLE IF NOT EXISTS nc_routing_entries (
    id          TEXT PRIMARY KEY,
    device_ip   TEXT NOT NULL,
    hostname    TEXT,
    prefix      TEXT NOT NULL,           -- e.g., 10.0.0.0/24 or 2001:db8::/32
    next_hop    TEXT,
    protocol    TEXT,                     -- connected, static, ospf, bgp, eigrp, isis
    metric      INTEGER DEFAULT 0,
    admin_distance INTEGER DEFAULT 0,
    interface   TEXT,
    vrf         TEXT DEFAULT 'default',
    address_family TEXT DEFAULT 'ipv4',  -- ipv4 or ipv6
    collected_at TEXT DEFAULT CURRENT_TIMESTAMP,
    topology_id TEXT REFERENCES topologies(id)
);

-- ── ARB/ERB Documentation (Architect Workbench) ──────────────────────────
-- Alternatives Analysis Matrix
CREATE TABLE IF NOT EXISTS nc_alternatives (
    id          TEXT PRIMARY KEY,
    project_id  TEXT REFERENCES nc_projects(id) ON DELETE CASCADE,
    option_name TEXT NOT NULL,
    description TEXT,
    is_recommended INTEGER DEFAULT 0,
    scores_json TEXT DEFAULT '{}',      -- {criterion_name: {score: 1-10, notes: ""}}
    total_score REAL DEFAULT 0,
    created_at  TEXT DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS nc_alt_criteria (
    id          TEXT PRIMARY KEY,
    project_id  TEXT REFERENCES nc_projects(id) ON DELETE CASCADE,
    name        TEXT NOT NULL,
    weight_pct  INTEGER DEFAULT 20,     -- percentage weight (all should sum to 100)
    sort_order  INTEGER DEFAULT 0,
    created_at  TEXT DEFAULT CURRENT_TIMESTAMP
);

-- Risk Register
CREATE TABLE IF NOT EXISTS nc_risks (
    id          TEXT PRIMARY KEY,
    project_id  TEXT REFERENCES nc_projects(id) ON DELETE CASCADE,
    title       TEXT NOT NULL,
    category    TEXT DEFAULT 'technical', -- technical, schedule, cost, operational, security
    probability TEXT DEFAULT 'medium',    -- low, medium, high
    impact      TEXT DEFAULT 'medium',    -- low, medium, high, critical
    risk_score  INTEGER DEFAULT 0,        -- auto-computed: prob * impact
    mitigation  TEXT,
    owner       TEXT,
    status      TEXT DEFAULT 'open',      -- open, mitigated, accepted, closed
    created_at  TEXT DEFAULT CURRENT_TIMESTAMP
);

-- Enhanced BOM line items
CREATE TABLE IF NOT EXISTS nc_bom_items (
    id          TEXT PRIMARY KEY,
    project_id  TEXT REFERENCES nc_projects(id) ON DELETE CASCADE,
    category    TEXT DEFAULT 'hardware',  -- hardware, software, circuit, labor, other
    vendor      TEXT,
    model       TEXT,
    part_number TEXT,
    description TEXT,
    quantity    INTEGER DEFAULT 1,
    unit_cost   REAL DEFAULT 0,
    extended_cost REAL DEFAULT 0,
    annual_maint REAL DEFAULT 0,         -- SmartNet/TAC/support
    license_cost REAL DEFAULT 0,         -- software licensing
    lead_time_days INTEGER DEFAULT 0,
    contract_vehicle TEXT,               -- GSA, SEWP, BPA, direct
    notes       TEXT,
    created_at  TEXT DEFAULT CURRENT_TIMESTAMP
);

-- Lab Test Results
CREATE TABLE IF NOT EXISTS nc_lab_tests (
    id          TEXT PRIMARY KEY,
    project_id  TEXT REFERENCES nc_projects(id) ON DELETE CASCADE,
    test_name   TEXT NOT NULL,
    category    TEXT DEFAULT 'functional', -- functional, failover, performance, security, interop
    methodology TEXT,
    result      TEXT DEFAULT 'pending',    -- pending, pass, fail, partial
    measurements TEXT DEFAULT '{}',        -- JSON: {convergence_ms, throughput_mbps, etc.}
    firmware_versions TEXT DEFAULT '{}',   -- JSON: {device: version}
    notes       TEXT,
    tested_by   TEXT,
    tested_at   TEXT,
    created_at  TEXT DEFAULT CURRENT_TIMESTAMP
);

-- Migration/Cutover Plan
CREATE TABLE IF NOT EXISTS nc_migration_phases (
    id          TEXT PRIMARY KEY,
    project_id  TEXT REFERENCES nc_projects(id) ON DELETE CASCADE,
    phase_num   INTEGER NOT NULL,
    title       TEXT NOT NULL,
    description TEXT,
    duration_days INTEGER DEFAULT 0,
    parallel_run INTEGER DEFAULT 0,       -- 1 = parallel operation with old system
    rollback_criteria TEXT,
    maintenance_window TEXT,
    dependencies TEXT DEFAULT '[]',       -- JSON array of predecessor phase IDs
    status      TEXT DEFAULT 'planned',   -- planned, in_progress, completed, rolled_back
    classification TEXT DEFAULT 'CUI'
        @@CK3@@,
    impact_level TEXT DEFAULT 'IL4'
        @@CK4@@,
    created_at  TEXT DEFAULT CURRENT_TIMESTAMP
);

-- Topology Snapshots (captured when a migration phase completes)
CREATE TABLE IF NOT EXISTS nc_topology_snapshots (
    id         TEXT PRIMARY KEY,
    topo_id    TEXT NOT NULL REFERENCES topologies(id),
    phase_id   TEXT REFERENCES nc_migration_phases(id),
    label      TEXT,
    graph_json TEXT NOT NULL,
    created_at TEXT DEFAULT (datetime('now'))
);

-- Capacity Growth Projections
CREATE TABLE IF NOT EXISTS nc_capacity_projections (
    id          TEXT PRIMARY KEY,
    project_id  TEXT REFERENCES nc_projects(id) ON DELETE CASCADE,
    metric_name TEXT NOT NULL,            -- bandwidth_gbps, users, devices, circuits
    current_value REAL DEFAULT 0,
    year1_value REAL DEFAULT 0,
    year3_value REAL DEFAULT 0,
    year5_value REAL DEFAULT 0,
    growth_rate_pct REAL DEFAULT 20,
    threshold_pct REAL DEFAULT 80,        -- upgrade trigger
    notes       TEXT,
    created_at  TEXT DEFAULT CURRENT_TIMESTAMP
);

-- Bandwidth Capacity Planning Simulations
CREATE TABLE IF NOT EXISTS nc_bw_simulations (
    id              TEXT PRIMARY KEY,
    topology_id     TEXT REFERENCES topologies(id) ON DELETE CASCADE,
    topology_name   TEXT,
    params_json     TEXT DEFAULT '{}',
    result_json     TEXT DEFAULT '{}',
    overall_health  TEXT DEFAULT 'ok',
    bottleneck_count INTEGER DEFAULT 0,
    warning_count    INTEGER DEFAULT 0,
    total_links      INTEGER DEFAULT 0,
    avg_util_pct     REAL DEFAULT 0,
    ran_at          TEXT DEFAULT CURRENT_TIMESTAMP
);

-- Standards Alignment Checklist
CREATE TABLE IF NOT EXISTS nc_standards_checks (
    id          TEXT PRIMARY KEY,
    project_id  TEXT REFERENCES nc_projects(id) ON DELETE CASCADE,
    standard    TEXT NOT NULL,            -- e.g., "Enterprise Ref Arch", "Vendor Approved List"
    check_item  TEXT NOT NULL,
    status      TEXT DEFAULT 'pending',   -- pending, compliant, deviation, waiver
    deviation_reason TEXT,
    waiver_ref  TEXT,
    created_at  TEXT DEFAULT CURRENT_TIMESTAMP
);

-- Resource Plan
CREATE TABLE IF NOT EXISTS nc_resource_plan (
    id          TEXT PRIMARY KEY,
    project_id  TEXT REFERENCES nc_projects(id) ON DELETE CASCADE,
    phase       TEXT,                     -- lifecycle phase this resource is needed
    role        TEXT NOT NULL,            -- Network Engineer, Security Analyst, PM, etc.
    name        TEXT,
    hours       REAL DEFAULT 0,
    rate_per_hour REAL DEFAULT 0,
    is_contractor INTEGER DEFAULT 0,
    skill_requirements TEXT,
    notes       TEXT,
    created_at  TEXT DEFAULT CURRENT_TIMESTAMP
);

-- ── Design Pattern Library (Phase 2) ──────────────────────────────────────
CREATE TABLE IF NOT EXISTS nc_design_patterns (
    id          TEXT PRIMARY KEY,
    name        TEXT NOT NULL,
    category    TEXT NOT NULL,         -- routing, redundancy, security, cloud, wan, campus, custom
    description TEXT,
    graph_json  TEXT NOT NULL DEFAULT '{"nodes":[],"edges":[]}',
    is_builtin  INTEGER DEFAULT 0,     -- 1 = shipped with ICDEV, 0 = user-created
    tags        TEXT DEFAULT '[]',
    created_by  TEXT,
    created_at  TEXT DEFAULT CURRENT_TIMESTAMP
);

-- ── Phase B: Global Connectivity (Interconnects) ──────────────────────────
CREATE TABLE IF NOT EXISTS nc_interconnects (
    id              TEXT PRIMARY KEY,
    src_project_id  TEXT REFERENCES nc_projects(id),
    src_topology_id TEXT REFERENCES topologies(id),
    src_node_id     TEXT,
    dst_project_id  TEXT REFERENCES nc_projects(id),
    dst_topology_id TEXT REFERENCES topologies(id),
    dst_node_id     TEXT,
    circuit_id      TEXT,
    protocol        TEXT,
    bandwidth       TEXT,
    notes           TEXT,
    created_at      TEXT DEFAULT CURRENT_TIMESTAMP
);

-- ── Phase C+: Notifications ───────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS nc_notifications (
    id          TEXT PRIMARY KEY,
    project_id  TEXT REFERENCES nc_projects(id) ON DELETE CASCADE,
    event_type  TEXT NOT NULL,        -- review_submitted, review_decided, gate_blocked, phase_changed
    title       TEXT NOT NULL,
    body        TEXT,
    is_read     INTEGER DEFAULT 0,
    created_at  TEXT DEFAULT CURRENT_TIMESTAMP
);

-- ── ACAS / Nessus vulnerability overlay ──────────────────────────────────────

CREATE TABLE IF NOT EXISTS nc_vuln_scans (
    id          TEXT PRIMARY KEY,
    topology_id TEXT REFERENCES topologies(id) ON DELETE CASCADE,
    scan_name   TEXT NOT NULL DEFAULT '',
    policy      TEXT DEFAULT '',
    scan_start  TEXT DEFAULT '',
    scan_end    TEXT DEFAULT '',
    file_name   TEXT DEFAULT '',
    host_count  INTEGER DEFAULT 0,
    created_at  TEXT DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS nc_vuln_hosts (
    id           TEXT PRIMARY KEY,
    scan_id      TEXT REFERENCES nc_vuln_scans(id) ON DELETE CASCADE,
    ip           TEXT NOT NULL,
    fqdn         TEXT DEFAULT '',
    netbios      TEXT DEFAULT '',
    os           TEXT DEFAULT '',
    cnt_critical INTEGER DEFAULT 0,
    cnt_high     INTEGER DEFAULT 0,
    cnt_medium   INTEGER DEFAULT 0,
    cnt_low      INTEGER DEFAULT 0,
    cnt_info     INTEGER DEFAULT 0,
    node_id      TEXT,                -- matched canvas node id (nullable)
    created_at   TEXT DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS nc_vuln_findings (
    id              TEXT PRIMARY KEY,
    host_id         TEXT REFERENCES nc_vuln_hosts(id) ON DELETE CASCADE,
    scan_id         TEXT REFERENCES nc_vuln_scans(id) ON DELETE CASCADE,
    plugin_id       TEXT DEFAULT '',
    plugin_name     TEXT DEFAULT '',
    severity        INTEGER DEFAULT 0,   -- 0=info 1=low 2=medium 3=high 4=critical
    severity_label  TEXT DEFAULT 'info',
    risk_factor     TEXT DEFAULT 'none',
    cve             TEXT DEFAULT '',
    cvss_base_score TEXT DEFAULT '',
    port            TEXT DEFAULT '',
    protocol        TEXT DEFAULT '',
    synopsis        TEXT DEFAULT '',
    description     TEXT DEFAULT '',
    solution        TEXT DEFAULT '',
    plugin_output   TEXT DEFAULT ''
);

CREATE INDEX IF NOT EXISTS idx_vuln_hosts_scan ON nc_vuln_hosts(scan_id);
CREATE INDEX IF NOT EXISTS idx_vuln_hosts_ip ON nc_vuln_hosts(ip);
CREATE INDEX IF NOT EXISTS idx_vuln_hosts_node ON nc_vuln_hosts(node_id);
CREATE INDEX IF NOT EXISTS idx_vuln_findings_host ON nc_vuln_findings(host_id);
CREATE INDEX IF NOT EXISTS idx_vuln_findings_severity ON nc_vuln_findings(severity);

-- Natural Language Query log (append-only: NL questions and answers over topologies)
CREATE TABLE IF NOT EXISTS nc_query_log (
    id          TEXT PRIMARY KEY,
    topology_id TEXT REFERENCES topologies(id),
    question    TEXT NOT NULL,
    intent      TEXT DEFAULT 'general',  -- path, failure, neighbor, inventory, compliance, general
    answer      TEXT DEFAULT '',
    engine      TEXT DEFAULT '',         -- path, failure, neighbor, inventory, compliance, llm
    ts          TEXT DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_query_log_topo ON nc_query_log(topology_id);
CREATE INDEX IF NOT EXISTS idx_query_log_ts   ON nc_query_log(ts);

-- ── Enclave-in-a-Box Snippets ─────────────────────────────────────────────
-- Pre-built compliance-validated sub-topologies (SIPR, IL5 DMZ, Tactical Edge)
-- Drag onto canvas; all STIG properties pre-populated.

CREATE TABLE IF NOT EXISTS nc_collab_sessions (
    id          TEXT PRIMARY KEY,
    design_id   TEXT NOT NULL REFERENCES topologies(id) ON DELETE CASCADE,
    user_id     TEXT NOT NULL,
    user_name   TEXT NOT NULL DEFAULT '',
    color       TEXT NOT NULL DEFAULT '#3498db',
    joined_at   TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    last_seen   TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    is_active   INTEGER NOT NULL DEFAULT 1
);

CREATE INDEX IF NOT EXISTS idx_nc_collab_design ON nc_collab_sessions(design_id);

CREATE TABLE IF NOT EXISTS nc_enclave_snippets (
    id                  TEXT PRIMARY KEY,
    name                TEXT NOT NULL,
    category            TEXT NOT NULL DEFAULT 'Enclave',
    description         TEXT,
    classification_level TEXT DEFAULT 'CUI',   -- CUI, SECRET, TS
    impact_level        TEXT DEFAULT 'IL4',    -- IL2, IL4, IL5, IL6
    graph_json          TEXT NOT NULL DEFAULT '{"nodes":[],"edges":[]}',
    stig_controls       TEXT DEFAULT '[]',     -- JSON array of NIST/STIG control IDs
    tags                TEXT DEFAULT '[]',
    created_at          TEXT DEFAULT CURRENT_TIMESTAMP
);

-- NDC Runbooks: network incident response playbooks
CREATE TABLE IF NOT EXISTS ndc_runbooks (
    id              TEXT PRIMARY KEY,
    title           TEXT NOT NULL,
    trigger_event   TEXT NOT NULL DEFAULT 'link_failure',
    severity        TEXT NOT NULL DEFAULT 'high',
    owner           TEXT,
    topology_id     TEXT REFERENCES topologies(id),
    description     TEXT,
    steps_json      TEXT NOT NULL DEFAULT '[]',
    classification  TEXT DEFAULT 'CUI',
    created_at      TEXT DEFAULT CURRENT_TIMESTAMP,
    updated_at      TEXT DEFAULT CURRENT_TIMESTAMP
);

-- ── Network Infrastructure Intelligence (NII) ──────────────────────────────

-- Device inventory with EOL tracking and criticality scoring
CREATE TABLE IF NOT EXISTS ni_devices (
    id                      TEXT PRIMARY KEY,
    topology_id             TEXT REFERENCES topologies(id),
    node_id                 TEXT NOT NULL,
    label                   TEXT NOT NULL,
    device_type             TEXT NOT NULL,
    vendor                  TEXT,
    model                   TEXT,
    firmware_version        TEXT,
    eol_date                TEXT,
    eos_date                TEXT,
    purchase_date           TEXT,
    purchase_cost           REAL DEFAULT 0,
    annual_maintenance_cost REAL DEFAULT 0,
    replacement_cost        REAL DEFAULT 0,
    site                    TEXT,
    rack_location           TEXT,
    criticality_score       REAL DEFAULT 0,
    downstream_count        INTEGER DEFAULT 0,
    notes                   TEXT,
    properties_json         TEXT DEFAULT '{}',
    created_at              TEXT DEFAULT CURRENT_TIMESTAMP,
    updated_at              TEXT DEFAULT CURRENT_TIMESTAMP
);

-- Cached analysis results (redundancy, EOL, blast radius, capacity, etc.)
CREATE TABLE IF NOT EXISTS ni_analyses (
    id              TEXT PRIMARY KEY,
    topology_id     TEXT REFERENCES topologies(id),
    analysis_type   TEXT NOT NULL,
    query_text      TEXT,
    input_json      TEXT DEFAULT '{}',
    result_json     TEXT DEFAULT '{}',
    result_summary  TEXT,
    created_at      TEXT DEFAULT CURRENT_TIMESTAMP
);

-- Temporal state snapshots for change tracking
CREATE TABLE IF NOT EXISTS ni_state_snapshots (
    id                  TEXT PRIMARY KEY,
    topology_id         TEXT REFERENCES topologies(id),
    snapshot_type       TEXT DEFAULT 'manual',
    graph_json          TEXT NOT NULL,
    device_count        INTEGER DEFAULT 0,
    link_count          INTEGER DEFAULT 0,
    change_description  TEXT,
    created_at          TEXT DEFAULT CURRENT_TIMESTAMP
);

-- Device configuration version tracking (drift detection + self-provisioning)
CREATE TABLE IF NOT EXISTS ni_device_configs (
    id              TEXT PRIMARY KEY,
    device_id       TEXT REFERENCES ni_devices(id),
    config_type     TEXT NOT NULL,
    config_text     TEXT NOT NULL,
    config_hash     TEXT NOT NULL,
    source          TEXT,
    version         INTEGER DEFAULT 1,
    created_at      TEXT DEFAULT CURRENT_TIMESTAMP
);

-- ── Ingestion subsystem (Phase NDC-Ingest) ──────────────────────────────────

-- Network documents (runbooks, SOPs, as-built docs, change requests)
CREATE TABLE IF NOT EXISTS nc_documents (
    id              TEXT PRIMARY KEY,
    file_name       TEXT NOT NULL,
    file_path       TEXT,
    file_hash       TEXT NOT NULL,
    file_size_bytes INTEGER DEFAULT 0,
    doc_type        TEXT DEFAULT 'general'
        @@CK5@@,
    extracted_text  TEXT DEFAULT '',
    page_count      INTEGER DEFAULT 0,
    provider_used   TEXT DEFAULT '',
    topology_id     TEXT REFERENCES topologies(id),
    project_id      TEXT DEFAULT 'default',
    classification  TEXT DEFAULT 'CUI // SP-CTI',
    status          TEXT DEFAULT 'pending'
        @@CK6@@,
    error           TEXT,
    ingested_at     TEXT DEFAULT CURRENT_TIMESTAMP,
    created_at      TEXT DEFAULT CURRENT_TIMESTAMP
);

-- Ingestion audit log (append-only, NIST AU)
CREATE TABLE IF NOT EXISTS nc_ingestion_log (
    id              TEXT PRIMARY KEY,
    channel         TEXT NOT NULL
        @@CK7@@,
    file_name       TEXT,
    file_type       TEXT,
    file_hash       TEXT,
    source_adapter  TEXT,
    status          TEXT DEFAULT 'started'
        @@CK8@@,
    result_json     TEXT DEFAULT '{}',
    error           TEXT,
    topology_id     TEXT,
    project_id      TEXT DEFAULT 'default',
    user_id         TEXT,
    classification  TEXT DEFAULT 'CUI // SP-CTI',
    created_at      TEXT DEFAULT CURRENT_TIMESTAMP,
    completed_at    TEXT
);

-- NMS adapter connection configurations
CREATE TABLE IF NOT EXISTS nc_nms_connections (
    id              TEXT PRIMARY KEY,
    adapter_name    TEXT NOT NULL,
    display_name    TEXT,
    connection_json TEXT DEFAULT '{}',
    last_sync_at    TEXT,
    last_sync_status TEXT DEFAULT 'never',
    enabled         INTEGER DEFAULT 1,
    created_at      TEXT DEFAULT CURRENT_TIMESTAMP
);

-- ── GNS3 / Lab Integration ──────────────────────────────────────────────────

-- Lab run: a single emulation session tied to a topology
CREATE TABLE IF NOT EXISTS nc_lab_runs (
    id          TEXT PRIMARY KEY,
    topology_id TEXT NOT NULL REFERENCES topologies(id) ON DELETE CASCADE,
    name        TEXT NOT NULL DEFAULT '',
    backend     TEXT DEFAULT 'stub',   -- stub, gns3, containerlab, eve-ng
    status      TEXT DEFAULT 'running', -- running, stopped, error
    gns3_project_id TEXT DEFAULT '',   -- GNS3 project UUID if applicable
    started_at  TEXT DEFAULT CURRENT_TIMESTAMP,
    stopped_at  TEXT
);
CREATE INDEX IF NOT EXISTS idx_nc_lab_runs_topo ON nc_lab_runs(topology_id);

-- Packet capture: one per link per session
CREATE TABLE IF NOT EXISTS nc_packet_captures (
    id          TEXT PRIMARY KEY,
    link_id     TEXT NOT NULL,          -- JointJS link/edge ID on the canvas
    lab_run_id  TEXT REFERENCES nc_lab_runs(id) ON DELETE SET NULL,
    topology_id TEXT REFERENCES topologies(id) ON DELETE CASCADE,
    src_label   TEXT DEFAULT '',        -- source node label
    dst_label   TEXT DEFAULT '',        -- destination node label
    protocol    TEXT DEFAULT '',        -- link protocol (BGP, OSPF, etc.)
    status      TEXT DEFAULT 'running', -- running, complete, error
    size_bytes  INTEGER DEFAULT 0,
    sha256      TEXT DEFAULT '',
    expiry_at   TEXT,                   -- auto-expire after 7 days
    backend_ref TEXT DEFAULT '{}',      -- JSON: GNS3 node/adapter refs
    pcap_data   BLOB,                   -- stored PCAP bytes (stub/small captures)
    created_at  TEXT DEFAULT CURRENT_TIMESTAMP,
    stopped_at  TEXT
);
CREATE INDEX IF NOT EXISTS idx_nc_captures_link ON nc_packet_captures(link_id);
CREATE INDEX IF NOT EXISTS idx_nc_captures_run  ON nc_packet_captures(lab_run_id);

-- ── Design Snapshots ────────────────────────────────────────────────────────
-- Frozen, restorable point-in-time captures of a design (topology + configs).
CREATE TABLE IF NOT EXISTS nc_design_snapshots (
    snap_id TEXT PRIMARY KEY,
    design_id TEXT NOT NULL,
    lab_run_id TEXT,                  -- optional: lab execution that produced snapshot
    manifest_json TEXT NOT NULL,      -- JSON: topology + device configs + lineage
    blob_uri TEXT,                    -- optional: path to .gns3project tarball
    sha256 TEXT NOT NULL,
    classification TEXT DEFAULT 'CUI',
    created_by TEXT,
    created_at TEXT DEFAULT (datetime('now')),
    description TEXT,
    size_bytes INTEGER
);
CREATE INDEX IF NOT EXISTS idx_nc_snapshots_design ON nc_design_snapshots(design_id);
CREATE INDEX IF NOT EXISTS idx_nc_snapshots_created ON nc_design_snapshots(created_at);

-- ── Sanitize → Lab Mode Clones ──────────────────────────────────────────────
-- Redacted clones of production designs for safe lab execution.
CREATE TABLE IF NOT EXISTS nc_lab_clones (
    clone_id TEXT PRIMARY KEY,
    parent_design_id TEXT NOT NULL,
    lineage TEXT NOT NULL,            -- JSON array of parent chain
    redaction_log TEXT NOT NULL,      -- JSON list of redactions
    created_by TEXT,
    created_at TEXT DEFAULT (datetime('now')),
    classification TEXT DEFAULT 'UNCLASSIFIED',
    lab_backend TEXT,                 -- gns3, containerlab, etc.
    lab_project_id TEXT               -- foreign lab system project ID
);
CREATE INDEX IF NOT EXISTS idx_nc_lab_clones_parent ON nc_lab_clones(parent_design_id);

-- ── NDC Standard Operating Procedures (SOPs) ───────────────────────────────
CREATE TABLE IF NOT EXISTS ndc_sops (
    sop_id TEXT PRIMARY KEY,
    title TEXT NOT NULL,
    category TEXT NOT NULL,              -- change_window, provisioning, firewall, dns, failover, etc.
    version INTEGER DEFAULT 1,
    status TEXT DEFAULT 'draft',         -- draft, review, approved, deprecated
    description TEXT,
    prerequisites TEXT,                   -- JSON list
    steps TEXT NOT NULL,                  -- JSON list of {number, action, verify, rollback, time_est}
    validation TEXT,                      -- JSON list of validation checks
    rollback TEXT,                        -- JSON rollback plan
    escalation TEXT,                      -- JSON escalation contacts
    classification TEXT DEFAULT 'CUI',
    author TEXT,
    reviewer TEXT,
    approver TEXT,
    approved_at TEXT,
    created_at TEXT DEFAULT (datetime('now')),
    updated_at TEXT DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_ndc_sops_category ON ndc_sops(category);
CREATE INDEX IF NOT EXISTS idx_ndc_sops_status ON ndc_sops(status);

CREATE TABLE IF NOT EXISTS ndc_sop_approval_log (
    id TEXT PRIMARY KEY,
    sop_id TEXT NOT NULL,
    actor TEXT NOT NULL,
    action TEXT NOT NULL,                 -- submitted, reviewed, approved, rejected, deprecated
    comment TEXT,
    timestamp TEXT DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_ndc_sop_log_sop ON ndc_sop_approval_log(sop_id);

-- ── Cloud Connectivity Patterns Reference ─────────────────────────────────────
CREATE TABLE IF NOT EXISTS nc_connectivity_patterns (
    id          TEXT PRIMARY KEY,
    csp_pair    TEXT NOT NULL,
    pattern_key TEXT NOT NULL,
    label       TEXT NOT NULL,
    description TEXT,
    resiliency  TEXT DEFAULT 'high',
    cost_tier   TEXT DEFAULT 'medium',
    use_cases   TEXT DEFAULT '[]',
    node_types  TEXT DEFAULT '[]',
    sop_refs    TEXT DEFAULT '[]',
    created_at  TEXT DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_nc_cp_csp ON nc_connectivity_patterns(csp_pair);

-- ── Traffic Flow Walkthroughs (DoD BCAP path analysis) ───────────────────────
CREATE TABLE IF NOT EXISTS nc_traffic_flows (
    id                  TEXT PRIMARY KEY,
    topology_id         TEXT NOT NULL,
    name                TEXT NOT NULL,
    description         TEXT DEFAULT '',
    src_zone            TEXT NOT NULL,
    dst_zone            TEXT NOT NULL,
    app_type            TEXT NOT NULL @@CK9@@,
    protocols           TEXT DEFAULT '[]',
    classification      TEXT DEFAULT 'NIPR' @@CK10@@,
    path_nodes          TEXT DEFAULT '[]',
    phase_id            TEXT REFERENCES nc_migration_phases(id),
    created_at          TEXT DEFAULT CURRENT_TIMESTAMP,
    updated_at          TEXT DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_nc_tf_topology ON nc_traffic_flows(topology_id);

-- Per-node security domain policy overrides
CREATE TABLE IF NOT EXISTS nc_security_domain_policies (
    id              TEXT PRIMARY KEY,
    topology_id     TEXT NOT NULL,
    node_id         TEXT NOT NULL,
    domain_type     TEXT NOT NULL @@CK11@@,
    domain_label    TEXT DEFAULT '',
    security_policy TEXT DEFAULT '{}',
    routing_policy  TEXT DEFAULT '{}',
    vpn_policy      TEXT DEFAULT '{}',
    created_at      TEXT DEFAULT CURRENT_TIMESTAMP,
    updated_at      TEXT DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(topology_id, node_id)
);
CREATE INDEX IF NOT EXISTS idx_nc_sdp_topology ON nc_security_domain_policies(topology_id);
CREATE INDEX IF NOT EXISTS idx_nc_sdp_node ON nc_security_domain_policies(node_id);

-- Walkthrough steps generated by TrafficFlowEngine.generate_walkthrough()
CREATE TABLE IF NOT EXISTS nc_flow_walkthrough_steps (
    id              TEXT PRIMARY KEY,
    flow_id         TEXT NOT NULL REFERENCES nc_traffic_flows(id) ON DELETE CASCADE,
    step_number     INTEGER NOT NULL,
    node_id         TEXT DEFAULT '',
    node_label      TEXT DEFAULT '',
    action_type     TEXT NOT NULL @@CK12@@,
    security_detail TEXT DEFAULT '{}',
    network_detail  TEXT DEFAULT '{}',
    narrative       TEXT DEFAULT '',
    created_at      TEXT DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(flow_id, step_number)
);
CREATE INDEX IF NOT EXISTS idx_nc_fws_flow ON nc_flow_walkthrough_steps(flow_id);

-- Per-step persona narrative responses
CREATE TABLE IF NOT EXISTS nc_step_persona_responses (
    id          TEXT PRIMARY KEY,
    step_id     TEXT NOT NULL REFERENCES nc_flow_walkthrough_steps(id) ON DELETE CASCADE,
    persona_id  TEXT NOT NULL @@CK13@@,
    narrative   TEXT DEFAULT '',
    detail_json TEXT DEFAULT '{}',
    created_at  TEXT DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(step_id, persona_id)
);
CREATE INDEX IF NOT EXISTS idx_nc_spr_step ON nc_step_persona_responses(step_id);

-- ── Vendor Stencil Libraries (Cisco, Juniper, AWS, Azure, Custom) ────────────
CREATE TABLE IF NOT EXISTS nc_stencil_libraries (
    id          TEXT PRIMARY KEY,
    vendor      TEXT NOT NULL,          -- cisco | juniper | aws | azure | custom
    name        TEXT NOT NULL,
    category    TEXT DEFAULT '',
    source_url  TEXT DEFAULT '',
    raw_format  TEXT DEFAULT 'vssx',    -- vssx | vss_zip | svg_pack | vsdx
    shape_count INTEGER DEFAULT 0,
    imported_at TEXT DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_nc_sl_vendor ON nc_stencil_libraries(vendor);

CREATE TABLE IF NOT EXISTS nc_stencil_shapes (
    id           TEXT PRIMARY KEY,
    library_id   TEXT NOT NULL REFERENCES nc_stencil_libraries(id) ON DELETE CASCADE,
    name         TEXT NOT NULL,
    name_u       TEXT DEFAULT '',       -- internal/universal name (NameU in Visio)
    category     TEXT DEFAULT '',
    icon_data    TEXT,                  -- base64-encoded PNG or SVG bytes (may be NULL for text-only fallback)
    icon_type    TEXT DEFAULT 'png' @@CK14@@,
    metadata_json TEXT DEFAULT '{}'
);
CREATE INDEX IF NOT EXISTS idx_nc_ss_library ON nc_stencil_shapes(library_id);
CREATE INDEX IF NOT EXISTS idx_nc_ss_name ON nc_stencil_shapes(name);

CREATE TABLE IF NOT EXISTS nc_ai_history (
    id           TEXT PRIMARY KEY,
    description  TEXT NOT NULL,
    short_desc   TEXT NOT NULL,
    node_count   INTEGER DEFAULT 0,
    edge_count   INTEGER DEFAULT 0,
    provider     TEXT DEFAULT '',
    is_migration INTEGER DEFAULT 0,
    graph_json   TEXT,
    created_at   TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_nc_ai_history_created ON nc_ai_history(created_at DESC);

CREATE TABLE IF NOT EXISTS chat_messages (
    id           TEXT PRIMARY KEY,
    context_id   TEXT NOT NULL,
    turn_number  INTEGER NOT NULL,
    role         TEXT NOT NULL,
    content      TEXT NOT NULL,
    content_type TEXT,
    created_at   TEXT
);
CREATE INDEX IF NOT EXISTS idx_ncmsg_ctx  ON chat_messages(context_id);
CREATE INDEX IF NOT EXISTS idx_ncmsg_turn ON chat_messages(context_id, turn_number);

-- Subnet Calculator: history of CIDR calculations, deduped per project
CREATE TABLE IF NOT EXISTS nc_subnet_calc_history (
    id            TEXT PRIMARY KEY,
    project_id    TEXT NOT NULL REFERENCES nc_projects(id) ON DELETE CASCADE,
    cidr          TEXT NOT NULL,
    network_addr  TEXT,
    broadcast     TEXT,
    first_host    TEXT,
    last_host     TEXT,
    total_hosts   INTEGER,
    usable_hosts  INTEGER,
    prefix_len    INTEGER,
    subnet_mask   TEXT,
    wildcard_mask TEXT,
    address_family TEXT DEFAULT 'ipv4',
    ip_class      TEXT,
    notes         TEXT,
    created_at    TEXT DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(cidr, project_id)
);
CREATE INDEX IF NOT EXISTS idx_nc_subnet_calc_proj ON nc_subnet_calc_history(project_id);

-- Migration Phase Info Box Overrides (user-editable overrides for computed values)
CREATE TABLE IF NOT EXISTS nc_phase_infoboxes (
    id          TEXT PRIMARY KEY,
    topo_id     TEXT NOT NULL,
    phase_key   TEXT NOT NULL,       -- 'current', 'phase-1', 'phase-2', 'final'
    box_id      TEXT NOT NULL,       -- 'device-inventory', 'link-utilization', etc.
    override_json TEXT DEFAULT '{}', -- JSON: {rows: [{label, value, status}], color, title}
    created_at  TEXT DEFAULT CURRENT_TIMESTAMP,
    updated_at  TEXT DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(topo_id, phase_key, box_id)
);
CREATE INDEX IF NOT EXISTS idx_nc_phase_infoboxes_topo ON nc_phase_infoboxes(topo_id);

-- Consolidation Analysis Results (cached per topology)
CREATE TABLE IF NOT EXISTS nc_consolidation_analysis (
    id                   TEXT PRIMARY KEY,
    topo_id              TEXT NOT NULL UNIQUE,
    current_device_count INTEGER DEFAULT 0,
    final_device_count   INTEGER DEFAULT 0,
    devices_removed      INTEGER DEFAULT 0,
    rack_units_freed     INTEGER DEFAULT 0,
    power_saved_watts    INTEGER DEFAULT 0,
    capex_delta          REAL DEFAULT 0,
    opex_annual_delta    REAL DEFAULT 0,
    tco_3yr_delta        REAL DEFAULT 0,
    bw_increase_pct      REAL DEFAULT 0,
    spof_count_before    INTEGER DEFAULT 0,
    spof_count_after     INTEGER DEFAULT 0,
    stig_compliance_before REAL DEFAULT 0,
    stig_compliance_after  REAL DEFAULT 0,
    analysis_json        TEXT DEFAULT '{}',  -- full analysis dict
    created_at           TEXT DEFAULT CURRENT_TIMESTAMP,
    updated_at           TEXT DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_nc_consolidation_topo ON nc_consolidation_analysis(topo_id);

-- Migration Hub: join table linking documents/runbooks/SOPs to specific phases
CREATE TABLE IF NOT EXISTS nc_phase_documents (
    id              TEXT PRIMARY KEY,
    phase_id        TEXT REFERENCES nc_migration_phases(id) ON DELETE CASCADE,
    project_id      TEXT REFERENCES nc_projects(id) ON DELETE CASCADE,
    doc_source      TEXT NOT NULL
        @@CK15@@,
    doc_id          TEXT NOT NULL,   -- FK into nc_documents / ndc_runbooks / ndc_sops
    doc_title       TEXT,
    doc_type        TEXT,            -- mirrors type from source table for quick display
    relevance_note  TEXT,            -- why this doc is relevant to this phase
    display_order   INTEGER DEFAULT 0,
    created_at      TEXT DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_nc_phase_docs_phase ON nc_phase_documents(phase_id);
CREATE INDEX IF NOT EXISTS idx_nc_phase_docs_project ON nc_phase_documents(project_id);

-- CVE Advisory tracking (NQE-sourced + manual)
CREATE TABLE IF NOT EXISTS nc_advisories (
    id              TEXT PRIMARY KEY,
    cve_id          TEXT NOT NULL,
    vendor          TEXT NOT NULL DEFAULT '',
    severity        TEXT NOT NULL DEFAULT 'medium'
                        @@CK16@@,
    published_date  TEXT,
    total_devices   INTEGER DEFAULT 0,
    impacted_devices INTEGER DEFAULT 0,
    remediation_pct REAL DEFAULT 0.0,
    data_source     TEXT DEFAULT 'manual'
                        @@CK17@@,
    hitl_status     TEXT DEFAULT 'pending'
                        @@CK18@@,
    hitl_approved_by TEXT,
    hitl_approved_at TEXT,
    description     TEXT,
    remediation_guidance TEXT,
    status          TEXT DEFAULT 'open'
                        @@CK19@@,
    created_at      TEXT DEFAULT CURRENT_TIMESTAMP,
    updated_at      TEXT DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_nc_advisories_cve ON nc_advisories(cve_id);
CREATE INDEX IF NOT EXISTS idx_nc_advisories_severity ON nc_advisories(severity);
CREATE INDEX IF NOT EXISTS idx_nc_advisories_status ON nc_advisories(status);

-- POAM items (Plan of Action & Milestones)
CREATE TABLE IF NOT EXISTS nc_poam_items (
    id              TEXT PRIMARY KEY,
    poam_id         TEXT UNIQUE NOT NULL,
    advisory_id     TEXT REFERENCES nc_advisories(id),
    cve_id          TEXT,
    weakness        TEXT,
    control_id      TEXT,
    severity        TEXT DEFAULT 'medium'
                        @@CK20@@,
    affected_assets_json TEXT DEFAULT '[]',
    scheduled_completion TEXT,
    actual_completion    TEXT,
    status          TEXT DEFAULT 'open'
                        @@CK21@@,
    twin_validated  INTEGER DEFAULT 0,
    responsible_party TEXT,
    milestones_json TEXT DEFAULT '[]',
    resources       TEXT,
    created_at      TEXT DEFAULT CURRENT_TIMESTAMP,
    updated_at      TEXT DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_nc_poam_advisory ON nc_poam_items(advisory_id);
CREATE INDEX IF NOT EXISTS idx_nc_poam_status ON nc_poam_items(status);

-- Exception registry (ISSO/ISSM/AO approval chain)
CREATE TABLE IF NOT EXISTS nc_exceptions (
    id              TEXT PRIMARY KEY,
    device_id       TEXT,
    device_name     TEXT NOT NULL DEFAULT '',
    exception_type  TEXT NOT NULL DEFAULT 'risk-acceptance'
                        @@CK22@@,
    risk_level      TEXT DEFAULT 'medium'
                        @@CK23@@,
    justification   TEXT,
    expiry_date     TEXT,
    isso_approved   INTEGER DEFAULT 0,
    isso_approved_by TEXT,
    isso_approved_at TEXT,
    issm_approved   INTEGER DEFAULT 0,
    issm_approved_by TEXT,
    issm_approved_at TEXT,
    ao_approved     INTEGER DEFAULT 0,
    ao_approved_by  TEXT,
    ao_approved_at  TEXT,
    status          TEXT DEFAULT 'pending'
                        @@CK24@@,
    advisory_id     TEXT REFERENCES nc_advisories(id),
    created_at      TEXT DEFAULT CURRENT_TIMESTAMP,
    updated_at      TEXT DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_nc_exceptions_status ON nc_exceptions(status);
CREATE INDEX IF NOT EXISTS idx_nc_exceptions_device ON nc_exceptions(device_id);

-- Remediation actions (append-only audit trail)
CREATE TABLE IF NOT EXISTS nc_remediation_actions (
    id              TEXT PRIMARY KEY,
    advisory_id     TEXT REFERENCES nc_advisories(id),
    device_id       TEXT,
    action_type     TEXT NOT NULL DEFAULT 'patch',
    performed_by    TEXT,
    notes           TEXT,
    result          TEXT DEFAULT 'pending'
                        @@CK25@@,
    created_at      TEXT DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_nc_remediation_advisory ON nc_remediation_actions(advisory_id);

-- HITL conflict resolutions
CREATE TABLE IF NOT EXISTS nc_conflict_resolutions (
    id              TEXT PRIMARY KEY,
    conflict_type   TEXT NOT NULL,
    detail          TEXT NOT NULL,
    severity        TEXT NOT NULL DEFAULT 'medium'
                        @@CK26@@,
    action          TEXT NOT NULL DEFAULT 'acknowledged'
                        @@CK27@@,
    note            TEXT DEFAULT '',
    resolved_by     TEXT DEFAULT '',
    resolved_at     TEXT DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_nc_conflict_res_type ON nc_conflict_resolutions(conflict_type);
CREATE INDEX IF NOT EXISTS idx_nc_conflict_res_action ON nc_conflict_resolutions(action);

-- ─────────────────────────────────────────────────────────────────────────────
-- Migration 220: NQE advisory tables (SQLite fresh-install backfill)
-- These tables exist in migrations/220_nqe_advisory.sql for PostgreSQL.
-- ─────────────────────────────────────────────────────────────────────────────

-- Impact assessment results (APPEND-ONLY)
CREATE TABLE IF NOT EXISTS nc_advisory_assessments (
    id                          TEXT PRIMARY KEY,
    advisory_id                 TEXT REFERENCES nc_advisories(id),
    network_id                  TEXT,
    fwd_snapshot_id             TEXT,
    data_source                 TEXT NOT NULL DEFAULT 'icdev-internal'
                                    @@CK28@@,
    nql_total                   TEXT,
    nql_impacted                TEXT,
    nql_ai_generated            TEXT,
    nql_template_based          TEXT,
    total_devices               INTEGER,
    impacted_count              INTEGER,
    impacted_devices_json       TEXT,
    raw_response_total_json     TEXT,
    raw_response_impacted_json  TEXT,
    raw_response_hash           TEXT,
    ai_confidence               REAL,
    cross_validation_delta_pct  REAL,
    cross_validation_warning    INTEGER DEFAULT 0,
    approved_by                 TEXT,
    approved_at                 TEXT,
    created_at                  TEXT DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_nc_advisory_assessments_advisory
    ON nc_advisory_assessments(advisory_id);
CREATE INDEX IF NOT EXISTS idx_nc_advisory_assessments_created
    ON nc_advisory_assessments(created_at DESC);

-- NQE query cache (NOT append-only — TTL-expired rows prunable)
CREATE TABLE IF NOT EXISTS nc_nqe_cache (
    id          TEXT PRIMARY KEY,
    nql_hash    TEXT NOT NULL,
    network_id  TEXT,
    result_json TEXT,
    created_at  TEXT DEFAULT CURRENT_TIMESTAMP,
    expires_at  TEXT NOT NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_nc_nqe_cache_hash ON nc_nqe_cache(nql_hash);
CREATE INDEX IF NOT EXISTS idx_nc_nqe_cache_expires ON nc_nqe_cache(expires_at);

-- Full audit log (APPEND-ONLY — NIST AU)
CREATE TABLE IF NOT EXISTS nc_nqe_audit_log (
    id                  TEXT PRIMARY KEY,
    session_id          TEXT,
    user_session        TEXT,
    action              TEXT NOT NULL
                            @@CK29@@,
    input_text          TEXT,
    nql_generated       TEXT,
    fwd_snapshot_id     TEXT,
    data_source         TEXT,
    result_summary      TEXT,
    raw_response_hash   TEXT,
    confidence          REAL,
    advisory_id         TEXT,
    assessment_id       TEXT,
    created_at          TEXT DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_nc_nqe_audit_advisory ON nc_nqe_audit_log(advisory_id);
CREATE INDEX IF NOT EXISTS idx_nc_nqe_audit_created  ON nc_nqe_audit_log(created_at DESC);

-- ─────────────────────────────────────────────────────────────────────────────
-- Migration 221: PVM — Predictive Vulnerability Management
-- APPEND-ONLY: nc_vuln_predictions, nc_patch_plans
-- Mutable: nc_attack_surface, nc_triage_queue, nc_maintenance_windows
-- ─────────────────────────────────────────────────────────────────────────────

-- Time-series risk scores per CVE (APPEND-ONLY)
CREATE TABLE IF NOT EXISTS nc_vuln_predictions (
    id                      TEXT PRIMARY KEY,
    advisory_id             TEXT NOT NULL,
    assessment_id           TEXT,
    risk_score_composite    REAL NOT NULL CHECK(risk_score_composite BETWEEN 0.0 AND 1.0),
    risk_score_30d          REAL NOT NULL CHECK(risk_score_30d BETWEEN 0.0 AND 1.0),
    risk_score_90d          REAL NOT NULL CHECK(risk_score_90d BETWEEN 0.0 AND 1.0),
    trend                   TEXT NOT NULL @@CK30@@,
    confidence              REAL NOT NULL CHECK(confidence BETWEEN 0.0 AND 1.0),
    cvss_base               REAL,
    exploit_weight          REAL,
    patch_lag_norm          REAL,
    impacted_trend          REAL,
    model_version           TEXT NOT NULL DEFAULT '1.0',
    predicted_at            TEXT DEFAULT CURRENT_TIMESTAMP,
    created_at              TEXT DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_nc_vuln_predictions_advisory
    ON nc_vuln_predictions(advisory_id);
CREATE INDEX IF NOT EXISTS idx_nc_vuln_predictions_predicted_at
    ON nc_vuln_predictions(predicted_at DESC);
CREATE INDEX IF NOT EXISTS idx_nc_vuln_predictions_risk
    ON nc_vuln_predictions(risk_score_composite DESC);

-- NQE-correlated attack surface per device×CVE (mutable — refreshed per run)
CREATE TABLE IF NOT EXISTS nc_attack_surface (
    id              TEXT PRIMARY KEY,
    device_id       TEXT,
    device_name     TEXT NOT NULL,
    ip              TEXT,
    cve_id          TEXT NOT NULL,
    advisory_id     TEXT,
    exposure_type   TEXT NOT NULL DEFAULT 'unknown'
                        @@CK31@@,
    reachable       INTEGER NOT NULL DEFAULT 0 CHECK(reachable IN (0,1)),
    bgp_exposed     INTEGER NOT NULL DEFAULT 0 CHECK(bgp_exposed IN (0,1)),
    criticality     INTEGER NOT NULL DEFAULT 3 CHECK(criticality BETWEEN 1 AND 5),
    surface_score   REAL NOT NULL CHECK(surface_score BETWEEN 0.0 AND 1.0),
    nqe_source      TEXT NOT NULL DEFAULT 'local_mapping'
                        @@CK32@@,
    nessus_scan_id  TEXT,
    assessed_at     TEXT DEFAULT CURRENT_TIMESTAMP,
    updated_at      TEXT DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_nc_attack_surface_device ON nc_attack_surface(device_name);
CREATE INDEX IF NOT EXISTS idx_nc_attack_surface_cve    ON nc_attack_surface(cve_id);
CREATE INDEX IF NOT EXISTS idx_nc_attack_surface_score  ON nc_attack_surface(surface_score DESC);
CREATE UNIQUE INDEX IF NOT EXISTS idx_nc_attack_surface_device_cve
    ON nc_attack_surface(device_name, cve_id);

-- Vulnerability triage queue (mutable — status transitions allowed)
CREATE TABLE IF NOT EXISTS nc_triage_queue (
    id                      TEXT PRIMARY KEY,
    advisory_id             TEXT NOT NULL,
    priority_score          REAL NOT NULL CHECK(priority_score BETWEEN 0.0 AND 1.0),
    kev_exploited           INTEGER NOT NULL DEFAULT 0 CHECK(kev_exploited IN (0,1)),
    asset_criticality_norm  REAL,
    network_exposure_norm   REAL,
    temporal_urgency        REAL,
    rank                    INTEGER,
    rationale_json          TEXT,
    status                  TEXT NOT NULL DEFAULT 'pending'
                                @@CK33@@,
    auto_approved           INTEGER NOT NULL DEFAULT 0 CHECK(auto_approved IN (0,1)),
    approved_by             TEXT,
    approved_at             TEXT,
    created_at              TEXT DEFAULT CURRENT_TIMESTAMP,
    updated_at              TEXT DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_nc_triage_queue_advisory  ON nc_triage_queue(advisory_id);
CREATE INDEX IF NOT EXISTS idx_nc_triage_queue_status    ON nc_triage_queue(status);
CREATE INDEX IF NOT EXISTS idx_nc_triage_queue_priority  ON nc_triage_queue(priority_score DESC);
CREATE UNIQUE INDEX IF NOT EXISTS idx_nc_triage_queue_advisory_unique
    ON nc_triage_queue(advisory_id);

-- Operator-configured maintenance windows (mutable)
CREATE TABLE IF NOT EXISTS nc_maintenance_windows (
    id                  TEXT PRIMARY KEY,
    site                TEXT NOT NULL,
    label               TEXT,
    start_utc           TEXT NOT NULL,
    end_utc             TEXT NOT NULL,
    recurrence          TEXT @@CK34@@,
    blackout_days_json  TEXT DEFAULT '[]',
    active              INTEGER NOT NULL DEFAULT 1 CHECK(active IN (0,1)),
    created_at          TEXT DEFAULT CURRENT_TIMESTAMP,
    updated_at          TEXT DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_nc_maintenance_windows_site   ON nc_maintenance_windows(site);
CREATE INDEX IF NOT EXISTS idx_nc_maintenance_windows_active ON nc_maintenance_windows(active);

-- AI-generated patch schedules (APPEND-ONLY — immutable once created)
CREATE TABLE IF NOT EXISTS nc_patch_plans (
    id                      TEXT PRIMARY KEY,
    plan_id                 TEXT NOT NULL,
    batch_id                TEXT NOT NULL,
    advisory_id             TEXT,
    device_name             TEXT NOT NULL,
    action                  TEXT NOT NULL,
    scheduled_at            TEXT,
    maintenance_window_id   TEXT,
    blast_radius_json       TEXT,
    simulation_status       TEXT DEFAULT 'pending'
                                @@CK35@@,
    risk_reduction          REAL,
    approved_by             TEXT,
    created_at              TEXT DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_nc_patch_plans_plan_id   ON nc_patch_plans(plan_id);
CREATE INDEX IF NOT EXISTS idx_nc_patch_plans_advisory  ON nc_patch_plans(advisory_id);
CREATE INDEX IF NOT EXISTS idx_nc_patch_plans_scheduled ON nc_patch_plans(scheduled_at);

-- ── Migration 222: PNA — Predictive Network Analytics ─────────────────────────

-- Device end-of-life / end-of-support risk predictions (APPEND-ONLY)
CREATE TABLE IF NOT EXISTS nc_eol_predictions (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    device_name         TEXT NOT NULL,
    vendor              TEXT,
    model               TEXT,
    os_version          TEXT,
    eos_date            TEXT,
    eol_date            TEXT,
    days_remaining      INTEGER,
    has_active_cves     INTEGER NOT NULL DEFAULT 0 CHECK(has_active_cves IN (0,1)),
    active_cve_count    INTEGER NOT NULL DEFAULT 0,
    risk_score          REAL NOT NULL CHECK(risk_score BETWEEN 0.0 AND 1.0),
    risk_tier           TEXT NOT NULL DEFAULT 'medium'
                            @@CK36@@,
    nqe_source          TEXT NOT NULL DEFAULT 'local_mapping'
                            @@CK37@@,
    model_version       TEXT NOT NULL DEFAULT '1.0',
    predicted_at        TEXT DEFAULT CURRENT_TIMESTAMP,
    created_at          TEXT DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_nc_eol_predictions_device   ON nc_eol_predictions(device_name);
CREATE INDEX IF NOT EXISTS idx_nc_eol_predictions_risk     ON nc_eol_predictions(risk_score DESC);
CREATE INDEX IF NOT EXISTS idx_nc_eol_predictions_eos_date ON nc_eol_predictions(eos_date ASC);
CREATE INDEX IF NOT EXISTS idx_nc_eol_predictions_vendor   ON nc_eol_predictions(vendor);

-- BGP session event log — rolling window, pruned after 90 days (MUTABLE)
CREATE TABLE IF NOT EXISTS nc_bgp_events (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    session_key TEXT NOT NULL,
    device_name TEXT NOT NULL,
    peer_ip     TEXT NOT NULL,
    peer_asn    INTEGER,
    event_type  TEXT NOT NULL @@CK38@@,
    event_at    TEXT DEFAULT CURRENT_TIMESTAMP,
    created_at  TEXT DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_nc_bgp_events_session ON nc_bgp_events(session_key, event_at DESC);
CREATE INDEX IF NOT EXISTS idx_nc_bgp_events_device  ON nc_bgp_events(device_name, event_at DESC);
CREATE INDEX IF NOT EXISTS idx_nc_bgp_events_type    ON nc_bgp_events(event_type, event_at DESC);

-- BGP session instability forecasts (APPEND-ONLY)
CREATE TABLE IF NOT EXISTS nc_bgp_predictions (
    id                   INTEGER PRIMARY KEY AUTOINCREMENT,
    session_key          TEXT NOT NULL,
    device_name          TEXT NOT NULL,
    peer_ip              TEXT NOT NULL,
    peer_asn             INTEGER,
    stability_score      REAL NOT NULL CHECK(stability_score BETWEEN 0.0 AND 1.0),
    flap_count_24h       INTEGER NOT NULL DEFAULT 0,
    flap_count_7d        INTEGER NOT NULL DEFAULT 0,
    flap_risk            TEXT NOT NULL DEFAULT 'low'
                             @@CK39@@,
    route_count          INTEGER,
    session_state        TEXT,
    predicted_outage_hrs REAL,
    confidence           REAL NOT NULL CHECK(confidence BETWEEN 0.0 AND 1.0),
    model_version        TEXT NOT NULL DEFAULT '1.0',
    predicted_at         TEXT DEFAULT CURRENT_TIMESTAMP,
    created_at           TEXT DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_nc_bgp_predictions_session ON nc_bgp_predictions(session_key, predicted_at DESC);
CREATE INDEX IF NOT EXISTS idx_nc_bgp_predictions_device  ON nc_bgp_predictions(device_name);
CREATE INDEX IF NOT EXISTS idx_nc_bgp_predictions_risk    ON nc_bgp_predictions(stability_score ASC);

-- STIG/compliance baseline drift predictions (APPEND-ONLY)
CREATE TABLE IF NOT EXISTS nc_compliance_drift (
    id                        INTEGER PRIMARY KEY AUTOINCREMENT,
    device_name               TEXT NOT NULL,
    framework                 TEXT NOT NULL,
    last_compliant_score      REAL CHECK(last_compliant_score BETWEEN 0.0 AND 1.0),
    current_score             REAL NOT NULL CHECK(current_score BETWEEN 0.0 AND 1.0),
    drift_delta               REAL NOT NULL,
    drift_rate_per_day        REAL,
    failing_controls          INTEGER NOT NULL DEFAULT 0,
    critical_controls_failing INTEGER NOT NULL DEFAULT 0,
    predicted_fail_date       TEXT,
    days_to_failure           INTEGER,
    risk_score                REAL NOT NULL CHECK(risk_score BETWEEN 0.0 AND 1.0),
    risk_tier                 TEXT NOT NULL DEFAULT 'medium'
                                  @@CK40@@,
    assessed_at               TEXT DEFAULT CURRENT_TIMESTAMP,
    created_at                TEXT DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_nc_compliance_drift_device    ON nc_compliance_drift(device_name, assessed_at DESC);
CREATE INDEX IF NOT EXISTS idx_nc_compliance_drift_framework ON nc_compliance_drift(framework);
CREATE INDEX IF NOT EXISTS idx_nc_compliance_drift_risk      ON nc_compliance_drift(risk_score DESC);

-- Bandwidth / capacity exhaustion forecasts (APPEND-ONLY)
CREATE TABLE IF NOT EXISTS nc_capacity_predictions (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    device_name         TEXT NOT NULL,
    interface_name      TEXT NOT NULL,
    interface_id        TEXT,
    current_util_pct    REAL NOT NULL CHECK(current_util_pct BETWEEN 0.0 AND 100.0),
    peak_util_pct       REAL CHECK(peak_util_pct BETWEEN 0.0 AND 100.0),
    avg_util_pct_7d     REAL,
    trend_slope         REAL NOT NULL,
    days_to_saturation  INTEGER,
    saturation_date     TEXT,
    confidence          REAL NOT NULL CHECK(confidence BETWEEN 0.0 AND 1.0),
    risk_score          REAL NOT NULL CHECK(risk_score BETWEEN 0.0 AND 1.0),
    risk_tier           TEXT NOT NULL DEFAULT 'low'
                            @@CK41@@,
    nqe_source          TEXT NOT NULL DEFAULT 'local_mapping',
    model_version       TEXT NOT NULL DEFAULT '1.0',
    predicted_at        TEXT DEFAULT CURRENT_TIMESTAMP,
    created_at          TEXT DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_nc_capacity_predictions_device      ON nc_capacity_predictions(device_name, interface_name, predicted_at DESC);
CREATE INDEX IF NOT EXISTS idx_nc_capacity_predictions_saturation  ON nc_capacity_predictions(days_to_saturation ASC);
CREATE INDEX IF NOT EXISTS idx_nc_capacity_predictions_risk        ON nc_capacity_predictions(risk_score DESC);

-- Pre-change failure probability scoring (APPEND-ONLY)
CREATE TABLE IF NOT EXISTS nc_change_risk (
    id                           INTEGER PRIMARY KEY AUTOINCREMENT,
    change_request_id            TEXT NOT NULL,
    device_name                  TEXT NOT NULL,
    action_type                  TEXT,
    failure_probability          REAL NOT NULL CHECK(failure_probability BETWEEN 0.0 AND 1.0),
    blast_radius_size            INTEGER NOT NULL DEFAULT 0,
    concurrent_change_count      INTEGER NOT NULL DEFAULT 0,
    maintenance_window_compliant INTEGER NOT NULL DEFAULT 1 CHECK(maintenance_window_compliant IN (0,1)),
    device_criticality           INTEGER NOT NULL DEFAULT 3 CHECK(device_criticality BETWEEN 1 AND 5),
    risk_factors_json            TEXT,
    risk_tier                    TEXT NOT NULL DEFAULT 'medium'
                                     @@CK42@@,
    simulation_verdict           TEXT,
    model_version                TEXT NOT NULL DEFAULT '1.0',
    predicted_at                 TEXT DEFAULT CURRENT_TIMESTAMP,
    created_at                   TEXT DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_nc_change_risk_change_request ON nc_change_risk(change_request_id);
CREATE INDEX IF NOT EXISTS idx_nc_change_risk_device         ON nc_change_risk(device_name, predicted_at DESC);
CREATE INDEX IF NOT EXISTS idx_nc_change_risk_probability    ON nc_change_risk(failure_probability DESC);

-- Vendor supply-chain risk aggregation (APPEND-ONLY)
CREATE TABLE IF NOT EXISTS nc_supply_chain_risk (
    id                    INTEGER PRIMARY KEY AUTOINCREMENT,
    vendor                TEXT NOT NULL,
    device_count          INTEGER NOT NULL DEFAULT 0,
    model_count           INTEGER NOT NULL DEFAULT 0,
    cve_count             INTEGER NOT NULL DEFAULT 0,
    kev_count             INTEGER NOT NULL DEFAULT 0,
    critical_cve_count    INTEGER NOT NULL DEFAULT 0,
    high_cve_count        INTEGER NOT NULL DEFAULT 0,
    risk_score            REAL NOT NULL CHECK(risk_score BETWEEN 0.0 AND 1.0),
    vendor_risk_rating    TEXT NOT NULL DEFAULT 'medium'
                              @@CK43@@,
    top_cves_json         TEXT,
    nqe_device_sample_json TEXT,
    model_version         TEXT NOT NULL DEFAULT '1.0',
    assessed_at           TEXT DEFAULT CURRENT_TIMESTAMP,
    created_at            TEXT DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_nc_supply_chain_risk_vendor ON nc_supply_chain_risk(vendor, assessed_at DESC);
CREATE INDEX IF NOT EXISTS idx_nc_supply_chain_risk_score  ON nc_supply_chain_risk(risk_score DESC);
CREATE INDEX IF NOT EXISTS idx_nc_supply_chain_risk_kev    ON nc_supply_chain_risk(kev_count DESC);

-- ── Config Review Assistant ─────────────────────────────────────────────────
-- These two tables were read by tools/iqe/adapters/ndc.py and by the
-- /network/config-review page, and referenced by migration 211's comments, but
-- had no DDL anywhere: only the test fixture created them. init_db therefore
-- never made them, so the feature could not persist a review.
--
-- config_text_hash, not config_text: a device configuration is sensitive and
-- the review only needs to detect that the same config was submitted again.
CREATE TABLE IF NOT EXISTS nc_config_reviews (
    id                TEXT PRIMARY KEY,
    title             TEXT,
    vendor            TEXT,
    role_key          TEXT,
    answers_json      TEXT DEFAULT '{}',
    config_text_hash  TEXT,
    status            TEXT DEFAULT 'pending',
    result_json       TEXT DEFAULT '{}',
    created_at        TEXT DEFAULT CURRENT_TIMESTAMP,
    updated_at        TEXT DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_nc_config_reviews_created ON nc_config_reviews(created_at DESC);

CREATE TABLE IF NOT EXISTS nc_config_review_findings (
    id                    TEXT PRIMARY KEY,
    review_id             TEXT REFERENCES nc_config_reviews(id) ON DELETE CASCADE,
    category              TEXT,
    severity              TEXT,
    title                 TEXT,
    detail                TEXT,
    remediation           TEXT,
    sample_config_snippet TEXT,
    references_json       TEXT DEFAULT '[]',
    created_at            TEXT DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_nc_config_review_findings_review ON nc_config_review_findings(review_id);
"""

SCHEMA = _render_schema(_SCHEMA_TEMPLATE)


# ---------------------------------------------------------------------------
# Constraint repair (PostgreSQL only) — generalized pattern-setter
# ---------------------------------------------------------------------------
# CREATE TABLE IF NOT EXISTS never repairs a CHECK on a pre-existing table, so a
# live PostgreSQL database whose <table>_<col>_check drifted from these Python
# constants keeps raising CheckViolation on every write (the drift class that
# broke ACE live). repair_check_constraints() re-derives each string CHECK from
# the rendered SCHEMA and rewrites it in place when it has drifted. Data-driven
# from SCHEMA itself, so it stays in sync automatically as constants change.
_QUOTED_RE = _re_cksites.compile(r"'([^']*)'")


def _iter_string_checks(schema: str):
    """Yield (table, column, frozenset(values)) for each string CHECK(col IN ...)."""
    table = None
    for m in _re_cksites.finditer(
        r"CREATE TABLE IF NOT EXISTS (\w+)|CHECK\((\w+) IN \(([^)]*)\)\)", schema
    ):
        if m.group(1):
            table = m.group(1)
        elif table is not None:
            vals = _QUOTED_RE.findall(m.group(3))
            if vals:  # skip boolean IN (0,1)
                yield table, m.group(2), frozenset(vals)


def repair_check_constraints(conn) -> dict:
    """Rewrite drifted string CHECK constraints from the Python constants.

    PostgreSQL only; a no-op on SQLite (the harness recreates tables from
    SCHEMA, so the constraint is always fresh there). Best-effort and
    idempotent: a second call after a repair reports "ok" for every constraint.
    Returns a ``{constraint_name: action}`` map (ok / repaired / added /
    skipped:<reason>).
    """
    from icdev.tools.db.storage import is_pg

    results: dict = {}
    if not is_pg(conn):
        return {"_backend": "skipped:sqlite"}

    for table, col, expected in _iter_string_checks(SCHEMA):
        cname = f"{table}_{col}_check"
        try:
            row = conn.execute(
                "SELECT pg_get_constraintdef(c.oid) "
                "FROM pg_constraint c JOIN pg_class t ON t.oid = c.conrelid "
                "WHERE t.relname = %s AND c.conname = %s",
                (table, cname),
            ).fetchone()
        except Exception as exc:
            results[cname] = f"skipped:{type(exc).__name__}"
            continue

        current = frozenset(_QUOTED_RE.findall(row[0])) if row else None
        if current == expected:
            results[cname] = "ok"
            continue

        joined = ", ".join(f"'{v}'" for v in sorted(expected))
        try:
            if row is not None:
                conn.execute(f"ALTER TABLE {table} DROP CONSTRAINT {cname}")
            conn.execute(
                f"ALTER TABLE {table} ADD CONSTRAINT {cname} "
                f"CHECK ({col} IN ({joined}))"
            )
            conn.commit()
            results[cname] = "repaired" if row is not None else "added"
        except Exception as exc:
            try:
                conn.rollback()
            except Exception:
                pass
            results[cname] = f"skipped:{type(exc).__name__}"

    return results


# ── Template seeds ────────────────────────────────────────────────────────────


def _node(nid, label, ntype, x, y, extra=None):
    n = {"id": nid, "label": label, "type": ntype, "x": x, "y": y}
    if extra:
        n.update(extra)
    return n


def _edge(src, dst, label="", protocol=""):
    return {"id": str(uuid.uuid4())[:8], "source": src, "target": dst, "label": label, "protocol": protocol}


TEMPLATES = [
    # 1 ─ GRE over IPSec
    {
        "id": "tpl-gre-ipsec",
        "name": "GRE over IPSec",
        "category": "WAN / VPN",
        "description": "Point-to-point GRE tunnel encapsulated inside IPSec ESP transport mode. Common for secure branch connectivity.",
        "tags": json.dumps(["vpn", "ipsec", "gre", "wan"]),
        "graph_json": json.dumps(
            {
                "nodes": [
                    _node("r1", "HQ Router", "router", 100, 200),
                    _node("fw1", "HQ Firewall", "firewall", 300, 200),
                    _node("isp1", "ISP Cloud", "cloud", 500, 200),
                    _node("fw2", "Branch FW", "firewall", 700, 200),
                    _node("r2", "Branch Router", "router", 900, 200),
                ],
                "edges": [
                    _edge("r1", "fw1", "LAN", ""),
                    _edge("fw1", "isp1", "WAN", "IPSec ESP"),
                    _edge("isp1", "fw2", "WAN", "IPSec ESP"),
                    _edge("fw2", "r2", "LAN", ""),
                    _edge("r1", "r2", "GRE Tunnel", "GRE/IPSec"),
                ],
            }
        ),
    },
    # 2 ─ Three-Tier
    {
        "id": "tpl-three-tier",
        "name": "Three-Tier (Core / Distribution / Access)",
        "category": "Campus LAN",
        "description": "Classic hierarchical campus network: core L3 switches, distribution switches with SVIs, and access layer PoE switches.",
        "tags": json.dumps(["campus", "hierarchical", "stp", "hsrp"]),
        "graph_json": json.dumps(
            {
                "nodes": [
                    _node("core1", "Core-SW-1", "switch-l3", 400, 80),
                    _node("core2", "Core-SW-2", "switch-l3", 600, 80),
                    _node("dist1", "Dist-SW-A", "switch-l3", 200, 240),
                    _node("dist2", "Dist-SW-B", "switch-l3", 500, 240),
                    _node("dist3", "Dist-SW-C", "switch-l3", 800, 240),
                    _node("acc1", "Access-1A", "switch-l2", 100, 400),
                    _node("acc2", "Access-1B", "switch-l2", 300, 400),
                    _node("acc3", "Access-2A", "switch-l2", 450, 400),
                    _node("acc4", "Access-2B", "switch-l2", 600, 400),
                    _node("acc5", "Access-3A", "switch-l2", 750, 400),
                    _node("acc6", "Access-3B", "switch-l2", 900, 400),
                ],
                "edges": [
                    _edge("core1", "core2", "ISL", "OSPF"),
                    _edge("core1", "dist1", "", "OSPF"),
                    _edge("core1", "dist2", "", "OSPF"),
                    _edge("core2", "dist2", "", "OSPF"),
                    _edge("core2", "dist3", "", "OSPF"),
                    _edge("dist1", "acc1", "", "STP"),
                    _edge("dist1", "acc2", "", "STP"),
                    _edge("dist2", "acc3", "", "STP"),
                    _edge("dist2", "acc4", "", "STP"),
                    _edge("dist3", "acc5", "", "STP"),
                    _edge("dist3", "acc6", "", "STP"),
                ],
            }
        ),
    },
    # 3 ─ Spine-Leaf
    {
        "id": "tpl-spine-leaf",
        "name": "Spine-Leaf (Clos) — BGP EVPN/VXLAN",
        "category": "Data Center",
        "description": "Two-tier Clos fabric with BGP EVPN/VXLAN overlay for multi-tenant DC workloads. Equal-cost multi-path (ECMP).",
        "tags": json.dumps(["data-center", "vxlan", "evpn", "bgp", "ecmp"]),
        "graph_json": json.dumps(
            {
                "nodes": [
                    _node("sp1", "Spine-1", "switch-l3", 300, 80),
                    _node("sp2", "Spine-2", "switch-l3", 600, 80),
                    _node("lf1", "Leaf-1", "switch-l3", 100, 280),
                    _node("lf2", "Leaf-2", "switch-l3", 300, 280),
                    _node("lf3", "Leaf-3", "switch-l3", 500, 280),
                    _node("lf4", "Leaf-4", "switch-l3", 700, 280),
                    _node("srv1", "Server Rack A", "server", 100, 440),
                    _node("srv2", "Server Rack B", "server", 300, 440),
                    _node("srv3", "Server Rack C", "server", 500, 440),
                    _node("srv4", "Server Rack D", "server", 700, 440),
                ],
                "edges": [
                    _edge("sp1", "lf1", "", "BGP EVPN"),
                    _edge("sp1", "lf2", "", "BGP EVPN"),
                    _edge("sp1", "lf3", "", "BGP EVPN"),
                    _edge("sp1", "lf4", "", "BGP EVPN"),
                    _edge("sp2", "lf1", "", "BGP EVPN"),
                    _edge("sp2", "lf2", "", "BGP EVPN"),
                    _edge("sp2", "lf3", "", "BGP EVPN"),
                    _edge("sp2", "lf4", "", "BGP EVPN"),
                    _edge("lf1", "srv1", "", "VXLAN"),
                    _edge("lf2", "srv2", "", "VXLAN"),
                    _edge("lf3", "srv3", "", "VXLAN"),
                    _edge("lf4", "srv4", "", "VXLAN"),
                ],
            }
        ),
    },
    # 4 ─ SD-WAN
    {
        "id": "tpl-sdwan",
        "name": "SD-WAN",
        "category": "WAN / VPN",
        "description": "Software-defined WAN with centralized orchestrator, vSmart controllers, and vEdge CPEs across MPLS + internet dual-homed branches.",
        "tags": json.dumps(["sdwan", "viptela", "wan", "orchestration"]),
        "graph_json": json.dumps(
            {
                "nodes": [
                    _node("orch", "vManage Orchestrator", "server", 500, 60),
                    _node("vsmart1", "vSmart-1", "server", 300, 180),
                    _node("vsmart2", "vSmart-2", "server", 700, 180),
                    _node("hub", "DC vEdge Hub", "router", 500, 300),
                    _node("br1", "Branch vEdge 1", "router", 200, 440),
                    _node("br2", "Branch vEdge 2", "router", 500, 440),
                    _node("br3", "Branch vEdge 3", "router", 800, 440),
                    _node("mpls", "MPLS Cloud", "cloud", 300, 360),
                    _node("inet", "Internet", "cloud", 700, 360),
                ],
                "edges": [
                    _edge("orch", "vsmart1", "OMP", "DTLS"),
                    _edge("orch", "vsmart2", "OMP", "DTLS"),
                    _edge("vsmart1", "hub", "OMP", "DTLS"),
                    _edge("vsmart2", "hub", "OMP", "DTLS"),
                    _edge("hub", "mpls", "", "MPLS"),
                    _edge("hub", "inet", "", "IPSec"),
                    _edge("mpls", "br1", "", "MPLS"),
                    _edge("inet", "br2", "", "IPSec"),
                    _edge("mpls", "br3", "", "MPLS"),
                    _edge("inet", "br3", "", "IPSec"),
                ],
            }
        ),
    },
    # 5 ─ MPLS L3VPN
    {
        "id": "tpl-mpls-l3vpn",
        "name": "MPLS L3VPN (RFC 4364)",
        "category": "Service Provider",
        "description": "RFC 4364 MPLS L3VPN with dedicated PE/P node types, route reflector, "
        "VRF customer separation, dual POP sites, and fiber patch panels.",
        "tags": json.dumps(["mpls", "l3vpn", "vrf", "bgp", "service-provider", "pop"]),
        "graph_json": json.dumps(
            {
                "nodes": [
                    # Customer A site
                    _node("ce-a1", "CE Router A1", "router", 50, 200),
                    _node("ce-a2", "CE Router A2", "router", 50, 400),
                    # POP 1
                    _node("pop1", "POP-1 (West)", "pop", 200, 300),
                    _node("fpp1", "Fiber Patch POP-1", "patch-panel-fiber", 280, 220),
                    _node("pe1", "PE-1", "mpls-pe", 350, 300),
                    # Core
                    _node("p1", "P-Core-1", "mpls-p", 520, 160),
                    _node("p2", "P-Core-2", "mpls-p", 520, 440),
                    _node("rr", "Route Reflector", "route-reflector", 520, 300),
                    # POP 2
                    _node("pe2", "PE-2", "mpls-pe", 690, 300),
                    _node("fpp2", "Fiber Patch POP-2", "patch-panel-fiber", 760, 220),
                    _node("pop2", "POP-2 (East)", "pop", 840, 300),
                    # Customer B site
                    _node("ce-b1", "CE Router B1", "router", 950, 200),
                    _node("ce-b2", "CE Router B2", "router", 950, 400),
                    # VRFs
                    _node("vrf-a", "VRF: Cust-A", "vrf", 350, 100),
                    _node("vrf-b", "VRF: Cust-B", "vrf", 690, 100),
                ],
                "edges": [
                    # Customer A → POP 1
                    _edge("ce-a1", "pop1", "GbE", ""),
                    _edge("ce-a2", "pop1", "GbE", ""),
                    _edge("pop1", "fpp1", "Fiber Patch", ""),
                    _edge("fpp1", "pe1", "10G SM", ""),
                    _edge("ce-a1", "pe1", "CE-PE", "eBGP"),
                    _edge("ce-a2", "pe1", "CE-PE", "eBGP"),
                    # MPLS Core
                    _edge("pe1", "p1", "MPLS LSP", "LDP"),
                    _edge("pe1", "p2", "MPLS LSP", "LDP"),
                    _edge("p1", "pe2", "MPLS LSP", "LDP"),
                    _edge("p2", "pe2", "MPLS LSP", "LDP"),
                    _edge("p1", "p2", "MPLS Core", "LDP"),
                    # Route Reflector
                    _edge("pe1", "rr", "iBGP", "MP-BGP"),
                    _edge("pe2", "rr", "iBGP", "MP-BGP"),
                    # POP 2 → Customer B
                    _edge("pe2", "fpp2", "10G SM", ""),
                    _edge("fpp2", "pop2", "Fiber Patch", ""),
                    _edge("pop2", "ce-b1", "GbE", ""),
                    _edge("pop2", "ce-b2", "GbE", ""),
                    _edge("pe2", "ce-b1", "CE-PE", "eBGP"),
                    _edge("pe2", "ce-b2", "CE-PE", "eBGP"),
                    # VRF associations
                    _edge("pe1", "vrf-a", "VRF Import", ""),
                    _edge("pe2", "vrf-b", "VRF Import", ""),
                ],
            }
        ),
    },
    # 6 ─ SONET/DWDM Ring
    {
        "id": "tpl-sonet-dwdm",
        "name": "SONET/DWDM Ring (BLSR)",
        "category": "Transport",
        "description": "Professional SONET/DWDM bidirectional line-switched ring with ROADM, "
        "OADM, EDFA amplifiers, transponders, ODF frames, SONET ADMs, "
        "and POP sites. 40-channel C-band DWDM, OC-192/STM-64.",
        "tags": json.dumps(["sonet", "dwdm", "optical", "ring", "transport", "roadm", "oadm", "edfa"]),
        "graph_json": json.dumps(
            {
                "nodes": [
                    # POP A — Hub
                    _node("pop-a", "POP-A (Hub)", "pop", 480, 20),
                    _node("odf-a", "ODF-A", "odf", 480, 80),
                    _node("roadm-a", "ROADM-A", "roadm", 480, 160),
                    _node("txp-a1", "Transponder A-1", "transponder", 380, 80),
                    _node("txp-a2", "Transponder A-2", "transponder", 580, 80),
                    _node("adm-a", "SONET ADM-A", "sonet-adm", 480, 240),
                    # Span A→B (East)
                    _node("edfa-ab", "EDFA (A→B)", "edfa", 720, 160),
                    _node("fiber-ab", "40km SM Fiber", "media-fiber", 780, 230),
                    # POP B
                    _node("pop-b", "POP-B", "pop", 880, 300),
                    _node("odf-b", "ODF-B", "odf", 820, 300),
                    _node("oadm-b", "OADM-B", "oadm", 740, 300),
                    _node("fpp-b", "Fiber Patch B", "patch-panel-fiber", 880, 380),
                    _node("adm-b", "SONET ADM-B", "sonet-adm", 740, 380),
                    # Span B→C (South)
                    _node("edfa-bc", "EDFA (B→C)", "edfa", 780, 460),
                    # POP C
                    _node("pop-c", "POP-C", "pop", 620, 520),
                    _node("odf-c", "ODF-C", "odf", 550, 520),
                    _node("oadm-c", "OADM-C", "oadm", 480, 460),
                    _node("adm-c", "SONET ADM-C", "sonet-adm", 480, 540),
                    # Span C→D (West)
                    _node("edfa-cd", "EDFA (C→D)", "edfa", 320, 460),
                    # POP D
                    _node("pop-d", "POP-D", "pop", 180, 380),
                    _node("odf-d", "ODF-D", "odf", 180, 310),
                    _node("oadm-d", "OADM-D", "oadm", 250, 300),
                    _node("fpp-d", "Fiber Patch D", "patch-panel-fiber", 100, 380),
                    _node("adm-d", "SONET ADM-D", "sonet-adm", 250, 380),
                    # Span D→A (North) — closing the ring
                    _node("edfa-da", "EDFA (D→A)", "edfa", 250, 200),
                ],
                "edges": [
                    # POP A internal
                    _edge("pop-a", "odf-a", "Trunk", ""),
                    _edge("odf-a", "txp-a1", "λ1-20 (C-band)", ""),
                    _edge("odf-a", "txp-a2", "λ21-40 (C-band)", ""),
                    _edge("txp-a1", "roadm-a", "10G Client", ""),
                    _edge("txp-a2", "roadm-a", "10G Client", ""),
                    _edge("roadm-a", "adm-a", "OC-192", "SONET"),
                    # East span: A → B
                    _edge("roadm-a", "edfa-ab", "λ1+λ2 East", "OC-192"),
                    _edge("edfa-ab", "fiber-ab", "Amplified", ""),
                    _edge("fiber-ab", "oadm-b", "40km", ""),
                    # POP B internal
                    _edge("pop-b", "odf-b", "Trunk", ""),
                    _edge("odf-b", "oadm-b", "Patch", ""),
                    _edge("oadm-b", "adm-b", "OC-192", "SONET"),
                    _edge("oadm-b", "fpp-b", "Drop", ""),
                    # South span: B → C
                    _edge("oadm-b", "edfa-bc", "λ3+λ4 South", "OC-192"),
                    _edge("edfa-bc", "oadm-c", "Amplified", ""),
                    # POP C internal
                    _edge("pop-c", "odf-c", "Trunk", ""),
                    _edge("odf-c", "oadm-c", "Patch", ""),
                    _edge("oadm-c", "adm-c", "OC-192", "SONET"),
                    # West span: C → D
                    _edge("oadm-c", "edfa-cd", "λ5+λ6 West", "OC-192"),
                    _edge("edfa-cd", "oadm-d", "Amplified", ""),
                    # POP D internal
                    _edge("pop-d", "odf-d", "Trunk", ""),
                    _edge("odf-d", "oadm-d", "Patch", ""),
                    _edge("oadm-d", "adm-d", "OC-192", "SONET"),
                    _edge("oadm-d", "fpp-d", "Drop", ""),
                    # North span: D → A (ring closure)
                    _edge("oadm-d", "edfa-da", "λ7+λ8 North", "OC-192"),
                    _edge("edfa-da", "roadm-a", "Ring Close", ""),
                ],
            }
        ),
    },
    # 7 ─ SDN OpenFlow
    {
        "id": "tpl-sdn-openflow",
        "name": "SDN (OpenFlow)",
        "category": "Data Center",
        "description": "OpenFlow-based SDN with centralized controller cluster, southbound OpenFlow 1.3 to programmable switches.",
        "tags": json.dumps(["sdn", "openflow", "controller", "programmable"]),
        "graph_json": json.dumps(
            {
                "nodes": [
                    _node("ctrl1", "SDN Controller 1", "server", 350, 80),
                    _node("ctrl2", "SDN Controller 2", "server", 600, 80),
                    _node("of1", "OF Switch 1", "switch-l3", 150, 280),
                    _node("of2", "OF Switch 2", "switch-l3", 350, 280),
                    _node("of3", "OF Switch 3", "switch-l3", 550, 280),
                    _node("of4", "OF Switch 4", "switch-l3", 750, 280),
                    _node("h1", "Host 1", "server", 150, 440),
                    _node("h2", "Host 2", "server", 350, 440),
                    _node("h3", "Host 3", "server", 550, 440),
                    _node("h4", "Host 4", "server", 750, 440),
                ],
                "edges": [
                    _edge("ctrl1", "of1", "OF 1.3", "TLS"),
                    _edge("ctrl1", "of2", "OF 1.3", "TLS"),
                    _edge("ctrl2", "of3", "OF 1.3", "TLS"),
                    _edge("ctrl2", "of4", "OF 1.3", "TLS"),
                    _edge("of1", "of2", "Data Plane", ""),
                    _edge("of2", "of3", "Data Plane", ""),
                    _edge("of3", "of4", "Data Plane", ""),
                    _edge("of1", "h1", "", ""),
                    _edge("of2", "h2", "", ""),
                    _edge("of3", "h3", "", ""),
                    _edge("of4", "h4", "", ""),
                ],
            }
        ),
    },
    # 8 ─ Zero Trust (NIST 800-207)
    {
        "id": "tpl-zero-trust",
        "name": "Zero Trust Architecture (NIST 800-207)",
        "category": "Security",
        "description": "NIST SP 800-207 compliant ZTA with Policy Engine (PE), Policy Administrator (PA), and Policy Enforcement Points (PEP).",
        "tags": json.dumps(["zero-trust", "nist", "pep", "iam", "microsegmentation"]),
        "graph_json": json.dumps(
            {
                "nodes": [
                    _node("idp", "Identity Provider (IdP)", "server", 500, 60),
                    _node("pe", "Policy Engine (PE)", "server", 300, 180),
                    _node("pa", "Policy Admin (PA)", "server", 700, 180),
                    _node("pep1", "PEP — Corp Net", "firewall", 200, 340),
                    _node("pep2", "PEP — Cloud", "firewall", 500, 340),
                    _node("pep3", "PEP — Remote", "firewall", 800, 340),
                    _node("res1", "Corporate Resources", "server", 200, 480),
                    _node("res2", "Cloud Workloads", "server", 500, 480),
                    _node("usr1", "Remote Users", "server", 800, 480),
                    _node("siem", "SIEM / CDM", "server", 500, 200),
                ],
                "edges": [
                    _edge("idp", "pe", "AuthN", "SAML/OIDC"),
                    _edge("pe", "pa", "Policy", "mTLS"),
                    _edge("pa", "pep1", "Enforce", "gRPC"),
                    _edge("pa", "pep2", "Enforce", "gRPC"),
                    _edge("pa", "pep3", "Enforce", "gRPC"),
                    _edge("pep1", "res1", "Allow/Deny", ""),
                    _edge("pep2", "res2", "Allow/Deny", ""),
                    _edge("pep3", "usr1", "Allow/Deny", ""),
                    _edge("siem", "pe", "Telemetry", ""),
                ],
            }
        ),
    },
    # 9 ─ QKD Point-to-Point
    {
        "id": "tpl-qkd-p2p",
        "name": "QKD Point-to-Point",
        "category": "Quantum",
        "description": "BB84 QKD link between two sites using dedicated dark fiber. Key Management Stations (KMS) feed symmetric keys to classical encryption engines.",
        "tags": json.dumps(["quantum", "qkd", "bb84", "encryption", "pqc"]),
        "graph_json": json.dumps(
            {
                "nodes": [
                    _node("qkd1", "QKD Transmitter (Alice)", "server", 150, 240),
                    _node("qkd2", "QKD Receiver (Bob)", "server", 750, 240),
                    _node("kms1", "KMS Site A", "server", 150, 380),
                    _node("kms2", "KMS Site B", "server", 750, 380),
                    _node("enc1", "Crypto Engine A", "firewall", 150, 500),
                    _node("enc2", "Crypto Engine B", "firewall", 750, 500),
                    _node("fiber", "Dark Fiber (Quantum Channel)", "cloud", 450, 240),
                    _node("classic", "Classical Auth Channel", "cloud", 450, 380),
                ],
                "edges": [
                    _edge("qkd1", "fiber", "Photon stream", "BB84"),
                    _edge("fiber", "qkd2", "Photon stream", "BB84"),
                    _edge("qkd1", "classic", "Sifting/Reconcile", "TLS"),
                    _edge("qkd2", "classic", "Sifting/Reconcile", "TLS"),
                    _edge("qkd1", "kms1", "QKM", ""),
                    _edge("qkd2", "kms2", "QKM", ""),
                    _edge("kms1", "enc1", "Symmetric Key", ""),
                    _edge("kms2", "enc2", "Symmetric Key", ""),
                ],
            }
        ),
    },
    # 10 ─ QKD Trusted-Node Relay
    {
        "id": "tpl-qkd-relay",
        "name": "QKD Trusted-Node Relay",
        "category": "Quantum",
        "description": "Multi-hop QKD network using trusted relay nodes to extend range beyond single-fiber attenuation limits.",
        "tags": json.dumps(["quantum", "qkd", "relay", "trusted-node"]),
        "graph_json": json.dumps(
            {
                "nodes": [
                    _node("site_a", "Site A (Alice)", "server", 80, 280),
                    _node("tn1", "Trusted Node 1", "router", 280, 280),
                    _node("tn2", "Trusted Node 2", "router", 480, 280),
                    _node("tn3", "Trusted Node 3", "router", 680, 280),
                    _node("site_b", "Site B (Bob)", "server", 880, 280),
                    _node("kms_net", "QKD Key Mgmt Network", "cloud", 480, 120),
                ],
                "edges": [
                    _edge("site_a", "tn1", "QKD Link 1", "BB84"),
                    _edge("tn1", "tn2", "QKD Link 2", "BB84"),
                    _edge("tn2", "tn3", "QKD Link 3", "BB84"),
                    _edge("tn3", "site_b", "QKD Link 4", "BB84"),
                    _edge("tn1", "kms_net", "Key relay", ""),
                    _edge("tn2", "kms_net", "Key relay", ""),
                    _edge("tn3", "kms_net", "Key relay", ""),
                ],
            }
        ),
    },
    # 11 ─ AI/ML Network Fabric
    {
        "id": "tpl-aiml-fabric",
        "name": "AI/ML Network Fabric (RDMA/RoCE)",
        "category": "Data Center",
        "description": "High-performance compute cluster network for AI/ML training: RDMA over Converged Ethernet (RoCE v2), 400G spine, in-network computing.",
        "tags": json.dumps(["ai-ml", "rdma", "roce", "infiniband", "gpu"]),
        "graph_json": json.dumps(
            {
                "nodes": [
                    _node("sp400_1", "400G Spine 1", "switch-l3", 300, 80),
                    _node("sp400_2", "400G Spine 2", "switch-l3", 600, 80),
                    _node("lf1", "100G Leaf 1", "switch-l3", 100, 280),
                    _node("lf2", "100G Leaf 2", "switch-l3", 300, 280),
                    _node("lf3", "100G Leaf 3", "switch-l3", 500, 280),
                    _node("lf4", "100G Leaf 4", "switch-l3", 700, 280),
                    _node("gpu1", "GPU Node A (8×H100)", "server", 100, 460),
                    _node("gpu2", "GPU Node B (8×H100)", "server", 300, 460),
                    _node("gpu3", "GPU Node C (8×H100)", "server", 500, 460),
                    _node("gpu4", "GPU Node D (8×H100)", "server", 700, 460),
                    _node("stor", "All-Flash Storage (NVMe/TCP)", "server", 900, 280),
                ],
                "edges": [
                    _edge("sp400_1", "lf1", "", "RoCEv2"),
                    _edge("sp400_1", "lf2", "", "RoCEv2"),
                    _edge("sp400_2", "lf3", "", "RoCEv2"),
                    _edge("sp400_2", "lf4", "", "RoCEv2"),
                    _edge("sp400_1", "sp400_2", "ISL", "400G"),
                    _edge("lf1", "gpu1", "", "100G RDMA"),
                    _edge("lf2", "gpu2", "", "100G RDMA"),
                    _edge("lf3", "gpu3", "", "100G RDMA"),
                    _edge("lf4", "gpu4", "", "100G RDMA"),
                    _edge("sp400_2", "stor", "", "NVMe/TCP"),
                ],
            }
        ),
    },
    # 12 ─ Crypto/Security Zones DMZ
    {
        "id": "tpl-security-zones",
        "name": "Crypto/Security Zones (DMZ)",
        "category": "Security",
        "description": "Multi-zone security architecture: Internet → DMZ → Application → Data tiers, dual-firewall, IDS/IPS inline, WAF, and jump host.",
        "tags": json.dumps(["dmz", "firewall", "ids", "waf", "zones", "nist"]),
        "graph_json": json.dumps(
            {
                "nodes": [
                    _node("internet", "Internet", "cloud", 500, 40),
                    _node("waf", "WAF / DDoS Scrubber", "firewall", 500, 140),
                    _node("fw_outer", "Outer Firewall (Untrust→DMZ)", "firewall", 500, 240),
                    _node("dmz_web", "DMZ Web Servers", "server", 300, 360),
                    _node("dmz_dns", "DMZ DNS / Email", "server", 700, 360),
                    _node("ids", "IDS/IPS (Inline)", "firewall", 500, 360),
                    _node("fw_inner", "Inner Firewall (DMZ→App)", "firewall", 500, 460),
                    _node("app_tier", "App Tier (mTLS)", "server", 350, 560),
                    _node("db_tier", "Data Tier (Encrypted)", "server", 650, 560),
                    _node("jump", "Jump Host / PAM", "server", 900, 460),
                    _node("siem", "SIEM / SOAR", "server", 900, 360),
                ],
                "edges": [
                    _edge("internet", "waf", "", "HTTPS"),
                    _edge("waf", "fw_outer", "", ""),
                    _edge("fw_outer", "dmz_web", "HTTP/S", ""),
                    _edge("fw_outer", "dmz_dns", "DNS/25", ""),
                    _edge("fw_outer", "ids", "Span", ""),
                    _edge("ids", "fw_inner", "", ""),
                    _edge("fw_inner", "app_tier", "8443", "mTLS"),
                    _edge("app_tier", "db_tier", "5432", "TLS"),
                    _edge("jump", "fw_inner", "22/3389", ""),
                    _edge("ids", "siem", "Events", "Syslog"),
                ],
            }
        ),
    },
    # 13 ─ Campus Area Network
    {
        "id": "tpl-campus-area",
        "name": "Campus Area Network (CAN)",
        "category": "Campus LAN",
        "description": "Multi-building campus network with redundant core, distribution per building, access layer with PoE APs, centralized data center, and WAN edge. OSPF backbone, STP at access.",
        "tags": json.dumps(["campus", "can", "multi-building", "ospf", "poe", "wap"]),
        "graph_json": json.dumps(
            {
                "nodes": [
                    # WAN Edge
                    _node("wan-gw1", "WAN Gateway-1", "router", 450, 20),
                    _node("wan-gw2", "WAN Gateway-2", "router", 650, 20),
                    _node("fw-edge", "Edge Firewall", "firewall", 550, 100),
                    # Core
                    _node("core1", "Core-SW-1", "switch-l3", 400, 200),
                    _node("core2", "Core-SW-2", "switch-l3", 700, 200),
                    # Building A — Admin
                    _node("dist-a", "Bldg-A Dist", "switch-l3", 120, 340),
                    _node("acc-a1", "Bldg-A Acc-1", "switch-l2", 50, 480),
                    _node("acc-a2", "Bldg-A Acc-2", "switch-l2", 190, 480),
                    _node("wap-a", "Bldg-A WAP", "wap", 120, 580),
                    # Building B — Engineering
                    _node("dist-b", "Bldg-B Dist", "switch-l3", 400, 340),
                    _node("acc-b1", "Bldg-B Acc-1", "switch-l2", 330, 480),
                    _node("acc-b2", "Bldg-B Acc-2", "switch-l2", 470, 480),
                    _node("wap-b", "Bldg-B WAP", "wap", 400, 580),
                    # Building C — Operations
                    _node("dist-c", "Bldg-C Dist", "switch-l3", 700, 340),
                    _node("acc-c1", "Bldg-C Acc-1", "switch-l2", 630, 480),
                    _node("acc-c2", "Bldg-C Acc-2", "switch-l2", 770, 480),
                    _node("wap-c", "Bldg-C WAP", "wap", 700, 580),
                    # Data Center
                    _node("dc-sw", "DC Spine", "switch-l3", 950, 200),
                    _node("srv-cluster", "Server Cluster", "server", 950, 340),
                    _node("lb", "Load Balancer", "load-balancer", 950, 100),
                ],
                "edges": [
                    # WAN Edge
                    _edge("wan-gw1", "fw-edge", "ISP-A", "BGP"),
                    _edge("wan-gw2", "fw-edge", "ISP-B", "BGP"),
                    _edge("fw-edge", "core1", "Trunk", "OSPF"),
                    _edge("fw-edge", "core2", "Trunk", "OSPF"),
                    # Core ISL
                    _edge("core1", "core2", "ISL", "OSPF"),
                    # Core to Distribution
                    _edge("core1", "dist-a", "OSPF Area 1", "OSPF"),
                    _edge("core1", "dist-b", "OSPF Area 2", "OSPF"),
                    _edge("core2", "dist-b", "OSPF Area 2", "OSPF"),
                    _edge("core2", "dist-c", "OSPF Area 3", "OSPF"),
                    _edge("core1", "dist-c", "OSPF Area 3", "OSPF"),
                    _edge("core2", "dist-a", "OSPF Area 1", "OSPF"),
                    # Building A access
                    _edge("dist-a", "acc-a1", "", "STP"),
                    _edge("dist-a", "acc-a2", "", "STP"),
                    _edge("acc-a1", "wap-a", "PoE", ""),
                    # Building B access
                    _edge("dist-b", "acc-b1", "", "STP"),
                    _edge("dist-b", "acc-b2", "", "STP"),
                    _edge("acc-b1", "wap-b", "PoE", ""),
                    # Building C access
                    _edge("dist-c", "acc-c1", "", "STP"),
                    _edge("dist-c", "acc-c2", "", "STP"),
                    _edge("acc-c1", "wap-c", "PoE", ""),
                    # Data Center
                    _edge("core2", "dc-sw", "10G", "OSPF"),
                    _edge("core1", "dc-sw", "10G", "OSPF"),
                    _edge("dc-sw", "srv-cluster", "25G", ""),
                    _edge("lb", "dc-sw", "VIP", ""),
                ],
            }
        ),
    },
    # 14 ─ Tactical Edge (DDIL)
    {
        "id": "tpl-tactical-ddil",
        "name": "Tactical Edge (DDIL)",
        "category": "Tactical / Military",
        "description": "Denied, Disrupted, Intermittent, Limited (DDIL) tactical network. Mesh radio backbone, SATCOM uplink, TOC servers, sensor feeds, and disconnected operation capability. MANET/OLSR routing.",
        "tags": json.dumps(["tactical", "ddil", "military", "manet", "satcom", "mesh", "edge"]),
        "graph_json": json.dumps(
            {
                "nodes": [
                    # SATCOM uplink
                    _node("satcom", "SATCOM Terminal", "cloud", 500, 20),
                    _node("crypto-sat", "KG-175D (SATCOM)", "kg-175d", 500, 120),
                    # TOC (Tactical Operations Center)
                    _node("toc-rtr", "TOC Router", "router", 500, 230),
                    _node("toc-sw", "TOC Switch", "switch-l3", 500, 340),
                    _node("toc-srv", "TOC Server (C2)", "server", 350, 340),
                    _node("toc-fw", "TOC Firewall", "firewall", 650, 340),
                    _node("toc-kg", "KG-175G (TOC)", "kg-175g", 350, 230),
                    # Mesh radio backbone
                    _node("mesh-1", "Radio Node Alpha", "wap", 180, 460),
                    _node("mesh-2", "Radio Node Bravo", "wap", 400, 460),
                    _node("mesh-3", "Radio Node Charlie", "wap", 620, 460),
                    _node("mesh-4", "Radio Node Delta", "wap", 840, 460),
                    # Forward edge nodes with KG-245X
                    _node("fwd-1", "FWD Team 1", "server", 100, 600),
                    _node("kg-fwd1", "KG-245X (FWD-1)", "kg-245x", 100, 530),
                    _node("fwd-2", "FWD Team 2", "server", 300, 600),
                    _node("fwd-3", "FWD Team 3", "server", 520, 600),
                    _node("fwd-4", "FWD Team 4", "server", 740, 600),
                    # Sensor feeds
                    _node("sensor-uav", "UAV Sensor", "cloud", 920, 600),
                    _node("sensor-gnd", "Ground Sensor", "patch-panel", 920, 460),
                    # Store-and-forward relay
                    _node("relay", "Store-Fwd Relay", "router", 180, 340),
                ],
                "edges": [
                    # SATCOM to TOC
                    _edge("satcom", "crypto-sat", "Ku/Ka-band", ""),
                    _edge("crypto-sat", "toc-rtr", "Type 1 Encrypt", "IPSec"),
                    _edge("toc-rtr", "toc-kg", "Red Side", ""),
                    _edge("toc-kg", "toc-sw", "Black Side GbE", "OSPF"),
                    _edge("toc-sw", "toc-srv", "C2/SA", ""),
                    _edge("toc-sw", "toc-fw", "CROSS-DOMAIN", ""),
                    # TOC to mesh backbone
                    _edge("toc-fw", "mesh-3", "MANET", "OLSR"),
                    _edge("toc-sw", "mesh-2", "MANET", "OLSR"),
                    # Mesh interconnects (resilient)
                    _edge("mesh-1", "mesh-2", "RF Mesh", "OLSR"),
                    _edge("mesh-2", "mesh-3", "RF Mesh", "OLSR"),
                    _edge("mesh-3", "mesh-4", "RF Mesh", "OLSR"),
                    _edge("mesh-1", "mesh-3", "RF Mesh Alt", "OLSR"),
                    _edge("mesh-2", "mesh-4", "RF Mesh Alt", "OLSR"),
                    # Forward teams
                    _edge("mesh-1", "kg-fwd1", "Tactical Radio", ""),
                    _edge("kg-fwd1", "fwd-1", "Encrypted", "IPSec"),
                    _edge("mesh-2", "fwd-2", "Tactical Radio", ""),
                    _edge("mesh-3", "fwd-3", "Tactical Radio", ""),
                    _edge("mesh-4", "fwd-4", "Tactical Radio", ""),
                    # Sensors
                    _edge("mesh-4", "sensor-gnd", "Serial/IP", ""),
                    _edge("sensor-uav", "mesh-4", "Datalink", ""),
                    # Store-and-forward (DDIL resilience)
                    _edge("relay", "mesh-1", "Store-Fwd", ""),
                    _edge("relay", "toc-sw", "Delay-Tolerant", ""),
                ],
            }
        ),
    },
    # 15 ─ Webscale East-West Fabric
    {
        "id": "tpl-east-west-fabric",
        "name": "Webscale East-West Fabric",
        "category": "Data Center",
        "description": "Hyperscale data center optimized for east-west (server-to-server) traffic. "
        "5-stage Clos with super-spines, ECMP across all tiers, "
        "dedicated border-leaf pair for north-south. "
        "80%+ traffic stays within the fabric.",
        "tags": json.dumps(["east-west", "webscale", "clos", "ecmp", "bgp", "hyperscale", "data-center"]),
        "graph_json": json.dumps(
            {
                "nodes": [
                    # North-South border
                    _node("border-lf1", "Border-Leaf-1", "switch-l3", 50, 20),
                    _node("border-lf2", "Border-Leaf-2", "switch-l3", 200, 20),
                    _node("edge-fw", "Edge Firewall", "firewall", 125, 100),
                    _node("wan-rtr", "WAN Router", "router", 125, 180),
                    # Super-spine (stage 3 of 5-stage Clos)
                    _node("ss1", "Super-Spine-1", "switch-l3", 350, 20),
                    _node("ss2", "Super-Spine-2", "switch-l3", 550, 20),
                    _node("ss3", "Super-Spine-3", "switch-l3", 750, 20),
                    _node("ss4", "Super-Spine-4", "switch-l3", 950, 20),
                    # Pod A — Compute
                    _node("sp-a1", "Pod-A Spine-1", "switch-l3", 250, 160),
                    _node("sp-a2", "Pod-A Spine-2", "switch-l3", 450, 160),
                    _node("lf-a1", "Pod-A Leaf-1", "switch-l3", 170, 290),
                    _node("lf-a2", "Pod-A Leaf-2", "switch-l3", 310, 290),
                    _node("lf-a3", "Pod-A Leaf-3", "switch-l3", 450, 290),
                    _node("srv-a1", "Compute Rack A1", "server", 170, 400),
                    _node("srv-a2", "Compute Rack A2", "server", 310, 400),
                    _node("srv-a3", "Compute Rack A3", "server", 450, 400),
                    # Pod B — Storage / DB
                    _node("sp-b1", "Pod-B Spine-1", "switch-l3", 650, 160),
                    _node("sp-b2", "Pod-B Spine-2", "switch-l3", 850, 160),
                    _node("lf-b1", "Pod-B Leaf-1", "switch-l3", 570, 290),
                    _node("lf-b2", "Pod-B Leaf-2", "switch-l3", 720, 290),
                    _node("lf-b3", "Pod-B Leaf-3", "switch-l3", 870, 290),
                    _node("srv-b1", "Storage Rack B1", "server", 570, 400),
                    _node("srv-b2", "DB Rack B2", "server", 720, 400),
                    _node("srv-b3", "Cache Rack B3", "server", 870, 400),
                    # East-West indicator VRFs
                    _node("vrf-app", "VRF: App-Tier", "vrf", 240, 500),
                    _node("vrf-data", "VRF: Data-Tier", "vrf", 720, 500),
                ],
                "edges": [
                    # Border leaf → N/S path
                    _edge("wan-rtr", "edge-fw", "N-S Uplink", "BGP"),
                    _edge("edge-fw", "border-lf1", "N-S", ""),
                    _edge("edge-fw", "border-lf2", "N-S", ""),
                    _edge("border-lf1", "ss1", "100G", "eBGP"),
                    _edge("border-lf2", "ss2", "100G", "eBGP"),
                    # Super-spine ↔ Pod A spines (full mesh ECMP)
                    _edge("ss1", "sp-a1", "100G ECMP", "eBGP"),
                    _edge("ss2", "sp-a1", "100G ECMP", "eBGP"),
                    _edge("ss3", "sp-a2", "100G ECMP", "eBGP"),
                    _edge("ss4", "sp-a2", "100G ECMP", "eBGP"),
                    # Super-spine ↔ Pod B spines
                    _edge("ss1", "sp-b1", "100G ECMP", "eBGP"),
                    _edge("ss2", "sp-b1", "100G ECMP", "eBGP"),
                    _edge("ss3", "sp-b2", "100G ECMP", "eBGP"),
                    _edge("ss4", "sp-b2", "100G ECMP", "eBGP"),
                    # Pod A: spine ↔ leaf
                    _edge("sp-a1", "lf-a1", "25G", "eBGP"),
                    _edge("sp-a1", "lf-a2", "25G", "eBGP"),
                    _edge("sp-a2", "lf-a2", "25G", "eBGP"),
                    _edge("sp-a2", "lf-a3", "25G", "eBGP"),
                    _edge("sp-a1", "lf-a3", "25G", "eBGP"),
                    _edge("sp-a2", "lf-a1", "25G", "eBGP"),
                    # Pod B: spine ↔ leaf
                    _edge("sp-b1", "lf-b1", "25G", "eBGP"),
                    _edge("sp-b1", "lf-b2", "25G", "eBGP"),
                    _edge("sp-b2", "lf-b2", "25G", "eBGP"),
                    _edge("sp-b2", "lf-b3", "25G", "eBGP"),
                    _edge("sp-b1", "lf-b3", "25G", "eBGP"),
                    _edge("sp-b2", "lf-b1", "25G", "eBGP"),
                    # Leaf ↔ servers
                    _edge("lf-a1", "srv-a1", "25G", "VXLAN"),
                    _edge("lf-a2", "srv-a2", "25G", "VXLAN"),
                    _edge("lf-a3", "srv-a3", "25G", "VXLAN"),
                    _edge("lf-b1", "srv-b1", "25G NVMe-oF", "VXLAN"),
                    _edge("lf-b2", "srv-b2", "25G", "VXLAN"),
                    _edge("lf-b3", "srv-b3", "25G", "VXLAN"),
                    # East-West VRF groupings
                    _edge("srv-a1", "vrf-app", "E-W", ""),
                    _edge("srv-a2", "vrf-app", "E-W", ""),
                    _edge("srv-a3", "vrf-app", "E-W", ""),
                    _edge("srv-b1", "vrf-data", "E-W", ""),
                    _edge("srv-b2", "vrf-data", "E-W", ""),
                    _edge("srv-b3", "vrf-data", "E-W", ""),
                    # Cross-pod E-W (the key traffic path)
                    _edge("vrf-app", "vrf-data", "E-W Cross-Pod", "VXLAN"),
                ],
            }
        ),
    },
    # 16 ─ Micro-Segmentation (NSX-style)
    {
        "id": "tpl-microseg",
        "name": "Micro-Segmentation (Distributed Firewall)",
        "category": "Security",
        "description": "Workload-level segmentation with distributed firewalls. "
        "Each server/VM group gets its own security policy enforced at the vNIC, "
        "not at a chokepoint. Security zones isolate tiers; "
        "VLANs separate tenants within each zone. Zero lateral movement by default.",
        "tags": json.dumps(["micro-segmentation", "nsx", "distributed-firewall", "zero-trust", "security-zone"]),
        "graph_json": json.dumps(
            {
                "nodes": [
                    # Spine fabric (simplified)
                    _node("spine1", "Spine-1", "switch-l3", 350, 20),
                    _node("spine2", "Spine-2", "switch-l3", 650, 20),
                    # Perimeter
                    _node("perim-fw", "Perimeter FW", "firewall", 500, 100),
                    # Leaf switches
                    _node("leaf-web", "Leaf-Web", "switch-l3", 150, 180),
                    _node("leaf-app", "Leaf-App", "switch-l3", 500, 180),
                    _node("leaf-db", "Leaf-DB", "switch-l3", 850, 180),
                    # Security Zones
                    _node("zone-web", "Zone: Web-DMZ", "security-zone", 150, 270),
                    _node("zone-app", "Zone: App-Tier", "security-zone", 500, 270),
                    _node("zone-db", "Zone: Data-Tier", "security-zone", 850, 270),
                    # Distributed Firewalls (per-zone enforcement)
                    _node("dfw-web", "DFW: Web Policy", "firewall", 150, 350),
                    _node("dfw-app", "DFW: App Policy", "firewall", 500, 350),
                    _node("dfw-db", "DFW: DB Policy", "firewall", 850, 350),
                    # Workloads — Web tier
                    _node("web-1", "Web-VM-1", "server", 50, 450),
                    _node("web-2", "Web-VM-2", "server", 250, 450),
                    _node("vlan-web-t1", "VLAN 100 Tenant-A", "vlan", 50, 540),
                    _node("vlan-web-t2", "VLAN 200 Tenant-B", "vlan", 250, 540),
                    # Workloads — App tier
                    _node("app-1", "App-VM-1", "server", 400, 450),
                    _node("app-2", "App-VM-2", "server", 600, 450),
                    _node("vlan-app-t1", "VLAN 110 Tenant-A", "vlan", 400, 540),
                    _node("vlan-app-t2", "VLAN 210 Tenant-B", "vlan", 600, 540),
                    # Workloads — DB tier
                    _node("db-1", "DB-VM-1", "server", 750, 450),
                    _node("db-2", "DB-VM-2", "server", 950, 450),
                    _node("vlan-db-t1", "VLAN 120 Tenant-A", "vlan", 750, 540),
                    _node("vlan-db-t2", "VLAN 220 Tenant-B", "vlan", 950, 540),
                ],
                "edges": [
                    # Spine to perimeter
                    _edge("spine1", "perim-fw", "N-S", "BGP"),
                    _edge("spine2", "perim-fw", "N-S", "BGP"),
                    # Spine to leaves (ECMP)
                    _edge("spine1", "leaf-web", "ECMP", "eBGP"),
                    _edge("spine2", "leaf-web", "ECMP", "eBGP"),
                    _edge("spine1", "leaf-app", "ECMP", "eBGP"),
                    _edge("spine2", "leaf-app", "ECMP", "eBGP"),
                    _edge("spine1", "leaf-db", "ECMP", "eBGP"),
                    _edge("spine2", "leaf-db", "ECMP", "eBGP"),
                    # Leaf → Security Zone
                    _edge("leaf-web", "zone-web", "Trunk", ""),
                    _edge("leaf-app", "zone-app", "Trunk", ""),
                    _edge("leaf-db", "zone-db", "Trunk", ""),
                    # Zone → DFW (policy enforcement point)
                    _edge("zone-web", "dfw-web", "Enforce", ""),
                    _edge("zone-app", "dfw-app", "Enforce", ""),
                    _edge("zone-db", "dfw-db", "Enforce", ""),
                    # DFW → workloads (per-vNIC policy)
                    _edge("dfw-web", "web-1", "Allow :443", ""),
                    _edge("dfw-web", "web-2", "Allow :443", ""),
                    _edge("dfw-app", "app-1", "Allow :8080", ""),
                    _edge("dfw-app", "app-2", "Allow :8080", ""),
                    _edge("dfw-db", "db-1", "Allow :5432", ""),
                    _edge("dfw-db", "db-2", "Allow :5432", ""),
                    # VLAN tenant isolation
                    _edge("web-1", "vlan-web-t1", "Tenant-A", ""),
                    _edge("web-2", "vlan-web-t2", "Tenant-B", ""),
                    _edge("app-1", "vlan-app-t1", "Tenant-A", ""),
                    _edge("app-2", "vlan-app-t2", "Tenant-B", ""),
                    _edge("db-1", "vlan-db-t1", "Tenant-A", ""),
                    _edge("db-2", "vlan-db-t2", "Tenant-B", ""),
                    # Allowed cross-zone flows (micro-seg: explicit allow only)
                    _edge("dfw-web", "dfw-app", "Allow Web→App", ""),
                    _edge("dfw-app", "dfw-db", "Allow App→DB", ""),
                    # DENY: web→db (no direct path = blocked by default)
                ],
            }
        ),
    },
    # 17 ─ Hyper-Segmentation (Service Mesh + Identity)
    {
        "id": "tpl-hyperseg",
        "name": "Hyper-Segmentation (Service Mesh / Identity)",
        "category": "Security",
        "description": "Per-process, per-API flow isolation using service mesh sidecars (Envoy), "
        "SPIFFE identity, and network policy enforcement at the pod level. "
        "Every flow is authenticated (mTLS), authorized (OPA), "
        "and encrypted end-to-end. Goes beyond micro-seg to per-call granularity.",
        "tags": json.dumps(
            [
                "hyper-segmentation",
                "service-mesh",
                "istio",
                "envoy",
                "spiffe",
                "mTLS",
                "zero-trust",
                "opa",
                "kubernetes",
            ]
        ),
        "graph_json": json.dumps(
            {
                "nodes": [
                    # Ingress layer
                    _node("ingress-gw", "Ingress Gateway", "load-balancer", 450, 20),
                    _node("waf", "WAF / API GW", "firewall", 450, 100),
                    # Control plane
                    _node("mesh-cp", "Mesh Control Plane", "server", 100, 100),
                    _node("spiffe", "SPIFFE CA (Identity)", "security-zone", 100, 200),
                    _node("opa", "OPA Policy Engine", "security-zone", 100, 300),
                    # Service A: Frontend
                    _node("ns-frontend", "NS: frontend", "subnet", 350, 200),
                    _node("svc-fe1", "frontend-v1", "server", 300, 290),
                    _node("sidecar-fe1", "Envoy Sidecar", "firewall", 420, 290),
                    # Service B: Order API
                    _node("ns-order", "NS: order-api", "subnet", 350, 390),
                    _node("svc-order1", "order-api-v1", "server", 300, 480),
                    _node("sidecar-order1", "Envoy Sidecar", "firewall", 420, 480),
                    # Service C: Payment
                    _node("ns-payment", "NS: payment", "subnet", 650, 200),
                    _node("svc-pay1", "payment-v1", "server", 600, 290),
                    _node("sidecar-pay1", "Envoy Sidecar", "firewall", 720, 290),
                    # Service D: Inventory
                    _node("ns-inventory", "NS: inventory", "subnet", 650, 390),
                    _node("svc-inv1", "inventory-v1", "server", 600, 480),
                    _node("sidecar-inv1", "Envoy Sidecar", "firewall", 720, 480),
                    # Data layer
                    _node("ns-data", "NS: data-stores", "subnet", 500, 570),
                    _node("db-orders", "orders-db", "server", 400, 640),
                    _node("sidecar-db-ord", "Envoy Sidecar", "firewall", 400, 720),
                    _node("db-inventory", "inventory-db", "server", 600, 640),
                    _node("sidecar-db-inv", "Envoy Sidecar", "firewall", 600, 720),
                    # Network policies
                    _node("netpol", "K8s NetworkPolicy", "security-zone", 850, 400),
                ],
                "edges": [
                    # Ingress
                    _edge("ingress-gw", "waf", "TLS Terminate", "HTTPS"),
                    _edge("waf", "sidecar-fe1", "mTLS", ""),
                    # Control plane → sidecars (config push)
                    _edge("mesh-cp", "sidecar-fe1", "xDS Config", "gRPC"),
                    _edge("mesh-cp", "sidecar-order1", "xDS Config", "gRPC"),
                    _edge("mesh-cp", "sidecar-pay1", "xDS Config", "gRPC"),
                    _edge("mesh-cp", "sidecar-inv1", "xDS Config", "gRPC"),
                    _edge("mesh-cp", "sidecar-db-ord", "xDS Config", "gRPC"),
                    _edge("mesh-cp", "sidecar-db-inv", "xDS Config", "gRPC"),
                    # Identity issuance
                    _edge("spiffe", "mesh-cp", "SVID Certs", ""),
                    # Policy push
                    _edge("opa", "mesh-cp", "AuthZ Policy", ""),
                    # Service ↔ sidecar (localhost)
                    _edge("svc-fe1", "sidecar-fe1", "localhost", ""),
                    _edge("svc-order1", "sidecar-order1", "localhost", ""),
                    _edge("svc-pay1", "sidecar-pay1", "localhost", ""),
                    _edge("svc-inv1", "sidecar-inv1", "localhost", ""),
                    _edge("db-orders", "sidecar-db-ord", "localhost", ""),
                    _edge("db-inventory", "sidecar-db-inv", "localhost", ""),
                    # Service-to-service flows (mTLS between sidecars)
                    _edge("sidecar-fe1", "sidecar-order1", "mTLS :8080", ""),
                    _edge("sidecar-fe1", "sidecar-pay1", "mTLS :8081", ""),
                    _edge("sidecar-order1", "sidecar-inv1", "mTLS :8082", ""),
                    _edge("sidecar-order1", "sidecar-db-ord", "mTLS :5432", ""),
                    _edge("sidecar-inv1", "sidecar-db-inv", "mTLS :5432", ""),
                    # Namespace boundaries
                    _edge("ns-frontend", "svc-fe1", "", ""),
                    _edge("ns-order", "svc-order1", "", ""),
                    _edge("ns-payment", "svc-pay1", "", ""),
                    _edge("ns-inventory", "svc-inv1", "", ""),
                    _edge("ns-data", "db-orders", "", ""),
                    _edge("ns-data", "db-inventory", "", ""),
                    # Network policy enforcement
                    _edge("netpol", "ns-frontend", "Deny All Default", ""),
                    _edge("netpol", "ns-order", "Deny All Default", ""),
                    _edge("netpol", "ns-payment", "Deny All Default", ""),
                    _edge("netpol", "ns-inventory", "Deny All Default", ""),
                    _edge("netpol", "ns-data", "Deny All Default", ""),
                ],
            }
        ),
    },
    # 18 ─ Metropolitan Area Network (MAN)
    {
        "id": "tpl-man",
        "name": "Metropolitan Area Network (MAN)",
        "category": "Metropolitan",
        "description": "Multi-site metro ring connecting campus locations, data centers, and "
        "a colocation facility over dark fiber and DWDM optical transport. "
        "FIPS 140-2 L2 encryption at each site handoff. MPLS/SR core with "
        "redundant ring topology for sub-50ms failover.",
        "tags": json.dumps(["man", "metro", "dwdm", "dark-fiber", "fips", "mpls", "ring"]),
        "graph_json": json.dumps(
            {
                "nodes": [
                    # Metro Core Ring (optical)
                    _node("opt-1", "Metro DWDM Node-A", "media-optical", 300, 60),
                    _node("opt-2", "Metro DWDM Node-B", "media-optical", 700, 60),
                    _node("opt-3", "Metro DWDM Node-C", "media-optical", 700, 400),
                    _node("opt-4", "Metro DWDM Node-D", "media-optical", 300, 400),
                    # PE routers
                    _node("pe-hq", "PE-HQ", "router", 150, 160),
                    _node("pe-dc", "PE-DC", "router", 850, 160),
                    _node("pe-colo", "PE-Colo", "router", 850, 300),
                    _node("pe-branch", "PE-Branch", "router", 150, 300),
                    # FIPS encryptors at each handoff
                    _node("fips-hq", "KG-340 (HQ)", "kg-340", 250, 160),
                    _node("fips-dc", "KG-250 (DC)", "kg-250", 750, 160),
                    _node("fips-colo", "KG-340 (Colo)", "kg-340", 750, 300),
                    _node("fips-branch", "KG-175D (Branch)", "kg-175d", 250, 300),
                    # Fiber interconnects
                    _node("fiber-hq", "10G Fiber", "media-fiber", 200, 110),
                    _node("fiber-dc", "10G Fiber", "media-fiber", 800, 110),
                    _node("fiber-colo", "10G Fiber", "media-fiber", 800, 350),
                    _node("fiber-branch", "10G Fiber", "media-fiber", 200, 350),
                    # Sites
                    _node("hq-core", "HQ Core-SW", "switch-l3", 50, 160),
                    _node("dc-spine", "DC Spine-SW", "switch-l3", 950, 160),
                    _node("colo-sw", "Colo Switch", "switch-l3", 950, 300),
                    _node("branch-sw", "Branch-SW", "switch-l3", 50, 300),
                    # Services
                    _node("dc-srv", "DC Servers", "server", 950, 60),
                    _node("colo-cloud", "Cloud Onramp", "cloud", 950, 400),
                    _node("hq-users", "HQ Users", "server", 50, 60),
                    _node("branch-users", "Branch Users", "server", 50, 400),
                ],
                "edges": [
                    # Optical ring
                    _edge("opt-1", "opt-2", "DWDM Ring", ""),
                    _edge("opt-2", "opt-3", "DWDM Ring", ""),
                    _edge("opt-3", "opt-4", "DWDM Ring", ""),
                    _edge("opt-4", "opt-1", "DWDM Ring", ""),
                    # Fiber from optical to encryptor
                    _edge("opt-1", "fiber-hq", "Lambda", ""),
                    _edge("opt-2", "fiber-dc", "Lambda", ""),
                    _edge("opt-3", "fiber-colo", "Lambda", ""),
                    _edge("opt-4", "fiber-branch", "Lambda", ""),
                    _edge("fiber-hq", "fips-hq", "10G SM", ""),
                    _edge("fiber-dc", "fips-dc", "10G SM", ""),
                    _edge("fiber-colo", "fips-colo", "10G SM", ""),
                    _edge("fiber-branch", "fips-branch", "10G SM", ""),
                    # FIPS to PE
                    _edge("fips-hq", "pe-hq", "Encrypted", "MPLS"),
                    _edge("fips-dc", "pe-dc", "Encrypted", "MPLS"),
                    _edge("fips-colo", "pe-colo", "Encrypted", "MPLS"),
                    _edge("fips-branch", "pe-branch", "Encrypted", "MPLS"),
                    # PE to site switch
                    _edge("pe-hq", "hq-core", "GbE", "OSPF"),
                    _edge("pe-dc", "dc-spine", "10G", "OSPF"),
                    _edge("pe-colo", "colo-sw", "GbE", "BGP"),
                    _edge("pe-branch", "branch-sw", "GbE", "OSPF"),
                    # Site endpoints
                    _edge("hq-core", "hq-users", "", ""),
                    _edge("dc-spine", "dc-srv", "25G", ""),
                    _edge("colo-sw", "colo-cloud", "DCI", ""),
                    _edge("branch-sw", "branch-users", "", ""),
                ],
            }
        ),
    },
    # 19 ─ Wide Area Network (WAN)
    {
        "id": "tpl-wan",
        "name": "Wide Area Network (WAN) — Multi-Transport",
        "category": "WAN / VPN",
        "description": "Enterprise WAN with MPLS primary, broadband SD-WAN secondary, and "
        "LTE/5G tertiary. Hub-and-spoke with dual-hub redundancy. "
        "FIPS 140-3 L3 HSM at hubs, FIPS 140-2 L2 at branches. "
        "GE copper last-mile, fiber backhaul, optical long-haul.",
        "tags": json.dumps(["wan", "sdwan", "mpls", "lte", "5g", "fips", "hsm", "multi-transport"]),
        "graph_json": json.dumps(
            {
                "nodes": [
                    # Hub A (Primary DC)
                    _node("hub-a-rtr", "Hub-A Router", "router", 300, 20),
                    _node("hub-a-fw", "Hub-A Firewall", "firewall", 300, 100),
                    _node("hub-a-hsm", "KG-250 (Hub-A)", "kg-250", 180, 60),
                    _node("hub-a-fips", "KG-340 (Hub-A)", "kg-340", 420, 60),
                    _node("hub-a-fiber", "100G Fiber", "media-fiber", 300, 170),
                    # Hub B (DR site)
                    _node("hub-b-rtr", "Hub-B Router", "router", 700, 20),
                    _node("hub-b-fw", "Hub-B Firewall", "firewall", 700, 100),
                    _node("hub-b-hsm", "KG-250 (Hub-B)", "kg-250", 580, 60),
                    _node("hub-b-fips", "KG-340 (Hub-B)", "kg-340", 820, 60),
                    _node("hub-b-fiber", "100G Fiber", "media-fiber", 700, 170),
                    # WAN Transport Core
                    _node("mpls-cloud", "MPLS Cloud (Carrier)", "cloud", 500, 240),
                    _node("inet-cloud", "Internet / SD-WAN", "cloud", 300, 320),
                    _node("lte-cloud", "LTE/5G Wireless", "cloud", 700, 320),
                    _node("optical-ll", "Optical Long-Haul", "media-optical", 500, 160),
                    # Branch A
                    _node("br-a-ce", "Branch-A CE Router", "router", 100, 430),
                    _node("br-a-fips", "KG-175D (Br-A)", "kg-175d", 100, 360),
                    _node("br-a-ge", "GbE Handoff", "media-ge", 100, 500),
                    _node("br-a-sw", "Branch-A Switch", "switch-l2", 100, 570),
                    # Branch B
                    _node("br-b-ce", "Branch-B CE Router", "router", 370, 430),
                    _node("br-b-fips", "KG-175D (Br-B)", "kg-175d", 370, 360),
                    _node("br-b-ge", "GbE Handoff", "media-ge", 370, 500),
                    _node("br-b-sw", "Branch-B Switch", "switch-l2", 370, 570),
                    # Branch C
                    _node("br-c-ce", "Branch-C CE Router", "router", 630, 430),
                    _node("br-c-fips", "KG-175D (Br-C)", "kg-175d", 630, 360),
                    _node("br-c-ge", "GbE Handoff", "media-ge", 630, 500),
                    _node("br-c-sw", "Branch-C Switch", "switch-l2", 630, 570),
                    # Remote/Tactical
                    _node("remote-ce", "Remote CE", "router", 900, 430),
                    _node("remote-fips", "KG-245X (Remote)", "kg-245x", 900, 360),
                    _node("remote-srv", "Remote Server", "server", 900, 570),
                ],
                "edges": [
                    # Hub A internal
                    _edge("hub-a-rtr", "hub-a-fw", "Inside", ""),
                    _edge("hub-a-rtr", "hub-a-hsm", "Key Mgmt", ""),
                    _edge("hub-a-rtr", "hub-a-fips", "WAN Encrypt", ""),
                    _edge("hub-a-fw", "hub-a-fiber", "DC Uplink", ""),
                    # Hub B internal
                    _edge("hub-b-rtr", "hub-b-fw", "Inside", ""),
                    _edge("hub-b-rtr", "hub-b-hsm", "Key Mgmt", ""),
                    _edge("hub-b-rtr", "hub-b-fips", "WAN Encrypt", ""),
                    _edge("hub-b-fw", "hub-b-fiber", "DR Uplink", ""),
                    # Hub-to-Hub
                    _edge("hub-a-fiber", "optical-ll", "100G LL", ""),
                    _edge("hub-b-fiber", "optical-ll", "100G LL", ""),
                    # Hub to WAN transports
                    _edge("hub-a-fips", "mpls-cloud", "MPLS PE", "BGP"),
                    _edge("hub-b-fips", "mpls-cloud", "MPLS PE", "BGP"),
                    _edge("hub-a-fips", "inet-cloud", "SD-WAN", "IPSec"),
                    _edge("hub-b-fips", "lte-cloud", "LTE Backup", ""),
                    # Branch A — dual transport
                    _edge("mpls-cloud", "br-a-fips", "MPLS Primary", ""),
                    _edge("inet-cloud", "br-a-fips", "SD-WAN Secondary", ""),
                    _edge("br-a-fips", "br-a-ce", "Decrypted", "OSPF"),
                    _edge("br-a-ce", "br-a-ge", "GbE", ""),
                    _edge("br-a-ge", "br-a-sw", "Copper", ""),
                    # Branch B — dual transport
                    _edge("mpls-cloud", "br-b-fips", "MPLS Primary", ""),
                    _edge("inet-cloud", "br-b-fips", "SD-WAN Secondary", ""),
                    _edge("br-b-fips", "br-b-ce", "Decrypted", "OSPF"),
                    _edge("br-b-ce", "br-b-ge", "GbE", ""),
                    _edge("br-b-ge", "br-b-sw", "Copper", ""),
                    # Branch C — MPLS + LTE
                    _edge("mpls-cloud", "br-c-fips", "MPLS Primary", ""),
                    _edge("lte-cloud", "br-c-fips", "LTE Tertiary", ""),
                    _edge("br-c-fips", "br-c-ce", "Decrypted", "OSPF"),
                    _edge("br-c-ce", "br-c-ge", "GbE", ""),
                    _edge("br-c-ge", "br-c-sw", "Copper", ""),
                    # Remote — LTE only
                    _edge("lte-cloud", "remote-fips", "LTE/5G Only", ""),
                    _edge("remote-fips", "remote-ce", "Decrypted", ""),
                    _edge("remote-ce", "remote-srv", "GbE", ""),
                ],
            }
        ),
    },
    # 20 ─ Pure SONET Ring (UPSR + BLSR)
    {
        "id": "tpl-sonet-ring",
        "name": "SONET Ring (UPSR / BLSR)",
        "category": "Transport",
        "description": "Pure SONET ring with 6 ADM nodes, dual-fiber BLSR protection, "
        "DS-3/OC-3 tributaries, and a SONET DCS at the hub. "
        "No DWDM — single lambda per fiber. Shows both working and protect paths "
        "with APS 1+1 on access rings and 2-fiber BLSR on backbone.",
        "tags": json.dumps(["sonet", "blsr", "upsr", "adm", "oc-48", "oc-3", "aps", "ring", "transport"]),
        "graph_json": json.dumps(
            {
                "nodes": [
                    # Hub / CO
                    _node("dcs", "SONET DCS (Hub)", "sonet-adm", 450, 40),
                    _node("fpp-hub", "Fiber Patch (Hub)", "patch-panel-fiber", 330, 40),
                    _node("pop-hub", "Central Office", "pop", 570, 40),
                    # BLSR Backbone Ring — 4 ADMs
                    _node("adm-1", "ADM-1 (North)", "sonet-adm", 450, 160),
                    _node("adm-2", "ADM-2 (East)", "sonet-adm", 720, 300),
                    _node("adm-3", "ADM-3 (South)", "sonet-adm", 450, 460),
                    _node("adm-4", "ADM-4 (West)", "sonet-adm", 180, 300),
                    # Fiber spans
                    _node("fiber-ne", "OC-48 Fiber (NE)", "media-fiber", 600, 200),
                    _node("fiber-se", "OC-48 Fiber (SE)", "media-fiber", 600, 400),
                    _node("fiber-sw", "OC-48 Fiber (SW)", "media-fiber", 300, 400),
                    _node("fiber-nw", "OC-48 Fiber (NW)", "media-fiber", 300, 200),
                    # UPSR Access Ring A (off ADM-2)
                    _node("acc-a1", "Access ADM-A1", "sonet-adm", 900, 200),
                    _node("acc-a2", "Access ADM-A2", "sonet-adm", 900, 400),
                    _node("ge-a1", "GbE (Site A1)", "media-ge", 1000, 200),
                    _node("ge-a2", "GbE (Site A2)", "media-ge", 1000, 400),
                    # UPSR Access Ring B (off ADM-4)
                    _node("acc-b1", "Access ADM-B1", "sonet-adm", 50, 200),
                    _node("acc-b2", "Access ADM-B2", "sonet-adm", 50, 400),
                    _node("ge-b1", "GbE (Site B1)", "media-ge", -50, 200),
                    _node("ge-b2", "GbE (Site B2)", "media-ge", -50, 400),
                    # Tributary interfaces
                    _node("ds3-1", "DS-3 Trib (Hub)", "patch-panel", 330, 120),
                    _node("oc3-1", "OC-3 Trib (South)", "patch-panel", 330, 460),
                ],
                "edges": [
                    # Hub internal
                    _edge("pop-hub", "dcs", "Trunk", ""),
                    _edge("dcs", "fpp-hub", "OC-48", "SONET"),
                    _edge("fpp-hub", "adm-1", "Working", "SONET"),
                    _edge("dcs", "ds3-1", "DS-3 x28", ""),
                    # BLSR Backbone — Working ring (clockwise)
                    _edge("adm-1", "fiber-ne", "OC-48 W", "SONET"),
                    _edge("fiber-ne", "adm-2", "Working", ""),
                    _edge("adm-2", "fiber-se", "OC-48 W", "SONET"),
                    _edge("fiber-se", "adm-3", "Working", ""),
                    _edge("adm-3", "fiber-sw", "OC-48 W", "SONET"),
                    _edge("fiber-sw", "adm-4", "Working", ""),
                    _edge("adm-4", "fiber-nw", "OC-48 W", "SONET"),
                    _edge("fiber-nw", "adm-1", "Working", ""),
                    # BLSR Protect ring (counter-clockwise, dashed in real diagrams)
                    _edge("adm-1", "adm-4", "OC-48 Protect", "SONET"),
                    _edge("adm-4", "adm-3", "OC-48 Protect", "SONET"),
                    _edge("adm-3", "adm-2", "OC-48 Protect", "SONET"),
                    _edge("adm-2", "adm-1", "OC-48 Protect", "SONET"),
                    # Tributary at south
                    _edge("adm-3", "oc3-1", "OC-3 Drop", ""),
                    # UPSR Access Ring A (off ADM-2)
                    _edge("adm-2", "acc-a1", "OC-3 UPSR", "SONET"),
                    _edge("acc-a1", "acc-a2", "OC-3 UPSR", "SONET"),
                    _edge("acc-a2", "adm-2", "OC-3 UPSR", "SONET"),
                    _edge("acc-a1", "ge-a1", "GbE Drop", ""),
                    _edge("acc-a2", "ge-a2", "GbE Drop", ""),
                    # UPSR Access Ring B (off ADM-4)
                    _edge("adm-4", "acc-b1", "OC-3 UPSR", "SONET"),
                    _edge("acc-b1", "acc-b2", "OC-3 UPSR", "SONET"),
                    _edge("acc-b2", "adm-4", "OC-3 UPSR", "SONET"),
                    _edge("acc-b1", "ge-b1", "GbE Drop", ""),
                    _edge("acc-b2", "ge-b2", "GbE Drop", ""),
                ],
            }
        ),
    },
    # ── AWS Multi-Home DX to CAN ──────────────────────────────────────────
    {
        "id": "tpl-aws-dx-can",
        "name": "AWS Well-Architected Hybrid DX to Campus (CAN)",
        "category": "Hybrid Cloud",
        "description": "AWS Well-Architected hybrid networking: dual DX with BFD, DX Gateway, VPN backup, Transit Gateway hub, PrivateLink, Shield, Flow Logs. Maximum resiliency (99.99% SLA) through diverse colocation facilities. Based on AWS Hybrid Networking Lens.",
        "tags": json.dumps(
            [
                "aws",
                "direct-connect",
                "hybrid",
                "campus",
                "multi-home",
                "dx",
                "well-architected",
                "bfd",
                "vpn-backup",
                "privatelink",
                "shield",
                "99.99-sla",
            ]
        ),
        "graph_json": json.dumps(
            {
                "nodes": [
                    # AWS Cloud — Well-Architected Hybrid Networking
                    _node("aws-tgw", "Transit Gateway", "aws-tgw", 500, 40),
                    _node("aws-dxgw", "DX Gateway", "aws-dx-gw", 500, 140),
                    _node(
                        "aws-vpc1",
                        "Prod VPC",
                        "aws-vpc",
                        300,
                        180,
                        {"config": {"flow_logs_enabled": True, "cidr": "10.1.0.0/16"}},
                    ),
                    _node(
                        "aws-vpc2",
                        "Shared Svcs VPC",
                        "aws-vpc",
                        700,
                        180,
                        {"config": {"flow_logs_enabled": True, "cidr": "10.2.0.0/16"}},
                    ),
                    _node("aws-sub1", "Prod Private Subnet", "aws-subnet", 200, 300),
                    _node("aws-sub2", "Prod Public Subnet", "aws-subnet", 400, 300),
                    _node("aws-sub3", "Shared Subnet", "aws-subnet", 700, 300),
                    _node("aws-nfw", "Network Firewall", "aws-nfw", 300, 400),
                    _node("aws-r53", "Route 53", "aws-r53", 900, 40),
                    _node("aws-shield", "Shield Advanced", "aws-shield", 900, 140),
                    _node("aws-pl", "PrivateLink (S3/DDB)", "aws-privatelink", 700, 400),
                    _node("aws-alb", "ALB", "aws-alb", 100, 400),
                    # VPN Backup (Well-Architected: always have VPN backup for DX)
                    _node("aws-vpn", "VPN Backup", "aws-vpn", 500, 520, {"config": {"tunnels": 2, "ecmp": True}}),
                    # Direct Connect (dual diverse locations — 99.99% Maximum Resiliency)
                    _node(
                        "dx-a",
                        "DX Circuit A (10G)",
                        "aws-dx",
                        250,
                        520,
                        {
                            "config": {
                                "bfd_enabled": True,
                                "bandwidth": "10G",
                                "vif_type": "transit",
                                "location": "Equinix DC5",
                            }
                        },
                    ),
                    _node(
                        "dx-b",
                        "DX Circuit B (10G)",
                        "aws-dx",
                        750,
                        520,
                        {
                            "config": {
                                "bfd_enabled": True,
                                "bandwidth": "10G",
                                "vif_type": "transit",
                                "location": "CoreSite VA1",
                            }
                        },
                    ),
                    # Meet-Me Rooms at diverse colocation facilities
                    _node("mmr-a", "MMR Equinix DC5", "meet-me-room", 250, 640),
                    _node("mmr-b", "MMR CoreSite VA1", "meet-me-room", 750, 640),
                    # Cross-connects
                    _node("xx-a", "XConn-A SMF", "cross-connect", 250, 720),
                    _node("xx-b", "XConn-B SMF", "cross-connect", 750, 720),
                    # Campus border (dual routers with BFD)
                    _node(
                        "br1",
                        "Border-RTR-1",
                        "router",
                        250,
                        840,
                        {"config": {"asn": "65001", "protocol": "BGP", "bfd_enabled": True}},
                    ),
                    _node(
                        "br2",
                        "Border-RTR-2",
                        "router",
                        750,
                        840,
                        {"config": {"asn": "65001", "protocol": "BGP", "bfd_enabled": True}},
                    ),
                    _node(
                        "fw1",
                        "Campus-FW-1 (HA)",
                        "firewall",
                        350,
                        980,
                        {"config": {"ha_mode": "active-standby", "default_policy": "deny-all"}},
                    ),
                    _node(
                        "fw2",
                        "Campus-FW-2 (HA)",
                        "firewall",
                        650,
                        980,
                        {"config": {"ha_mode": "active-standby", "default_policy": "deny-all"}},
                    ),
                    # Campus core
                    _node("core1", "Core-SW-1", "switch-l3", 400, 1120),
                    _node("core2", "Core-SW-2", "switch-l3", 600, 1120),
                    # Distribution
                    _node("dist1", "Dist-A", "switch-l3", 300, 1260),
                    _node("dist2", "Dist-B", "switch-l3", 700, 1260),
                    # Access
                    _node("acc1", "Access-1", "switch-l2", 200, 1400),
                    _node("acc2", "Access-2", "switch-l2", 500, 1400),
                    _node("acc3", "Access-3", "switch-l2", 800, 1400),
                ],
                "edges": [
                    # AWS internal — Well-Architected
                    _edge("aws-tgw", "aws-dxgw", "DX GW Assoc", ""),
                    _edge("aws-tgw", "aws-vpc1", "TGW Attach", ""),
                    _edge("aws-tgw", "aws-vpc2", "TGW Attach", ""),
                    _edge("aws-vpc1", "aws-sub1", "", ""),
                    _edge("aws-vpc1", "aws-sub2", "", ""),
                    _edge("aws-vpc2", "aws-sub3", "", ""),
                    _edge("aws-sub1", "aws-nfw", "Inspection", ""),
                    _edge("aws-sub2", "aws-nfw", "Inspection", ""),
                    _edge("aws-tgw", "aws-r53", "DNS", ""),
                    _edge("aws-vpc1", "aws-pl", "PrivateLink", ""),
                    _edge("aws-sub2", "aws-alb", "L7 LB", ""),
                    _edge("aws-alb", "aws-shield", "DDoS", ""),
                    # DX via DX Gateway (global resource — NOT a SPOF per ARC322)
                    _edge("aws-dxgw", "dx-a", "Transit VIF + BFD", "BGP"),
                    _edge("aws-dxgw", "dx-b", "Transit VIF + BFD", "BGP"),
                    # VPN backup (automatic failover when DX fails)
                    _edge("aws-tgw", "aws-vpn", "VPN Backup (2 tunnels)", "IPSec"),
                    # DX to Meet-Me Rooms
                    _edge("dx-a", "mmr-a", "DX Handoff", ""),
                    _edge("dx-b", "mmr-b", "DX Handoff", ""),
                    # Cross-connects in MMR
                    _edge("mmr-a", "xx-a", "SMF XConn", ""),
                    _edge("mmr-b", "xx-b", "SMF XConn", ""),
                    # Cross-connects to border routers (diverse paths)
                    _edge("xx-a", "br1", "10GbE + BFD", "BGP"),
                    _edge("xx-b", "br2", "10GbE + BFD", "BGP"),
                    _edge("xx-a", "br2", "10GbE Backup", "BGP"),
                    _edge("xx-b", "br1", "10GbE Backup", "BGP"),
                    # VPN lands on border routers too
                    _edge("aws-vpn", "br1", "IPSec Tunnel", "IPSec"),
                    _edge("aws-vpn", "br2", "IPSec Tunnel", "IPSec"),
                    # Border to firewalls
                    _edge("br1", "fw1", "10GbE", "OSPF"),
                    _edge("br2", "fw2", "10GbE", "OSPF"),
                    _edge("br1", "fw2", "10GbE Failover", "OSPF"),
                    _edge("br2", "fw1", "10GbE Failover", "OSPF"),
                    # Firewall to core
                    _edge("fw1", "core1", "10GbE", "OSPF"),
                    _edge("fw2", "core2", "10GbE", "OSPF"),
                    # Core ISL
                    _edge("core1", "core2", "40GbE ISL", "OSPF"),
                    # Core to dist
                    _edge("core1", "dist1", "10GbE", "OSPF"),
                    _edge("core1", "dist2", "10GbE", "OSPF"),
                    _edge("core2", "dist1", "10GbE", "OSPF"),
                    _edge("core2", "dist2", "10GbE", "OSPF"),
                    # Dist to access
                    _edge("dist1", "acc1", "1GbE", "STP"),
                    _edge("dist1", "acc2", "1GbE", "STP"),
                    _edge("dist2", "acc2", "1GbE", "STP"),
                    _edge("dist2", "acc3", "1GbE", "STP"),
                ],
            }
        ),
    },
    # ── Azure Multi-Home ExpressRoute to MAN ──────────────────────────────
    {
        "id": "tpl-azure-er-man",
        "name": "Azure Well-Architected ExpressRoute to Metro (MAN)",
        "category": "Hybrid Cloud",
        "description": "Azure Virtual WAN hub (natively global) with dual zone-redundant ExpressRoute, Global Reach for on-prem site connectivity, VPN Gateway backup, Private Link, DDoS Protection, VNet Flow Logs. Based on AWS/Azure Well-Architected equivalence.",
        "tags": json.dumps(
            [
                "azure",
                "expressroute",
                "hybrid",
                "metro",
                "man",
                "multi-home",
                "well-architected",
                "global-reach",
                "private-link",
                "ddos",
                "vpn-backup",
            ]
        ),
        "graph_json": json.dumps(
            {
                "nodes": [
                    # Azure Cloud — Well-Architected
                    _node("az-vwan", "Virtual WAN Hub", "az-vwan", 500, 40),
                    _node(
                        "az-vnet1",
                        "Prod VNet",
                        "az-vnet",
                        300,
                        180,
                        {"config": {"flow_logs_enabled": True, "address_space": "10.1.0.0/16"}},
                    ),
                    _node(
                        "az-vnet2",
                        "DMZ VNet",
                        "az-vnet",
                        700,
                        180,
                        {"config": {"flow_logs_enabled": True, "address_space": "10.2.0.0/16"}},
                    ),
                    _node("az-fw", "Azure Firewall Premium", "az-fw", 500, 180),
                    _node("az-sub1", "App Subnet", "az-subnet", 300, 320),
                    _node("az-sub2", "DMZ Subnet", "az-subnet", 700, 320),
                    _node("az-fd", "Front Door (L7+CDN+WAF)", "az-front", 800, 40),
                    _node("az-dns", "Azure DNS", "az-dns", 200, 40),
                    _node("az-ddos", "DDoS Protection", "az-ddos", 900, 180),
                    _node("az-pl", "Private Link", "az-privatelink", 500, 320),
                    _node("az-nsg", "NSG (default deny)", "az-nsg", 200, 320),
                    # Global Reach — connect on-prem sites via Microsoft backbone
                    _node("az-gr", "ER Global Reach", "az-er-global", 500, 420),
                    # VPN Gateway backup
                    _node(
                        "az-vpn",
                        "VPN Gateway Backup",
                        "az-vpn-gw",
                        500,
                        520,
                        {"config": {"sku": "VpnGw2AZ", "active_active": True}},
                    ),
                    # ExpressRoute (dual zone-redundant from diverse peering locs)
                    _node(
                        "er-a",
                        "ER Circuit A (Equinix)",
                        "az-er",
                        250,
                        520,
                        {"config": {"bfd_enabled": True, "bandwidth": "10G", "peering_type": "private"}},
                    ),
                    _node(
                        "er-b",
                        "ER Circuit B (Megaport)",
                        "az-er",
                        750,
                        520,
                        {"config": {"bfd_enabled": True, "bandwidth": "10G", "peering_type": "private"}},
                    ),
                    # Meet-Me Rooms
                    _node("mmr-a", "MMR Equinix CH1", "meet-me-room", 250, 640),
                    _node("mmr-b", "MMR Megaport SYD", "meet-me-room", 750, 640),
                    # Cross-connects
                    _node("xx-a", "XConn-A SMF", "cross-connect", 250, 720),
                    _node("xx-b", "XConn-B SMF", "cross-connect", 750, 720),
                    # Metro PE routers
                    _node("pe1", "MPLS-PE-1", "mpls-pe", 200, 820, {"config": {"bfd_enabled": True}}),
                    _node("pe2", "MPLS-PE-2", "mpls-pe", 800, 820, {"config": {"bfd_enabled": True}}),
                    # Metro core
                    _node("p1", "MPLS-P-Core", "mpls-p", 500, 820),
                    # Metro sites
                    _node("site-fw1", "Site-A FW", "firewall", 200, 980),
                    _node("site-fw2", "Site-B FW", "firewall", 500, 980),
                    _node("site-fw3", "Site-C FW", "firewall", 800, 980),
                    _node("sw-a", "Site-A Core", "switch-l3", 200, 1120),
                    _node("sw-b", "Site-B Core", "switch-l3", 500, 1120),
                    _node("sw-c", "Site-C Core", "switch-l3", 800, 1120),
                ],
                "edges": [
                    # Azure internal — Well-Architected
                    _edge("az-vwan", "az-vnet1", "VNet Peering", ""),
                    _edge("az-vwan", "az-vnet2", "VNet Peering", ""),
                    _edge("az-vnet1", "az-fw", "Routing Intent", ""),
                    _edge("az-vnet2", "az-fw", "Routing Intent", ""),
                    _edge("az-vnet1", "az-sub1", "", ""),
                    _edge("az-vnet2", "az-sub2", "", ""),
                    _edge("az-vwan", "az-fd", "Global L7+CDN+WAF", ""),
                    _edge("az-vwan", "az-dns", "DNS", ""),
                    _edge("az-fd", "az-ddos", "DDoS Protect", ""),
                    _edge("az-sub1", "az-pl", "Private Link", ""),
                    _edge("az-sub1", "az-nsg", "NSG", ""),
                    # Global Reach (connect on-prem sites via MS backbone)
                    _edge("er-a", "az-gr", "Global Reach", "BGP"),
                    _edge("er-b", "az-gr", "Global Reach", "BGP"),
                    # ExpressRoute to VWAN (zone-redundant)
                    _edge("az-vwan", "er-a", "ER 10G Primary + BFD", "BGP"),
                    _edge("az-vwan", "er-b", "ER 10G Secondary + BFD", "BGP"),
                    # VPN backup
                    _edge("az-vwan", "az-vpn", "VPN Backup (active-active)", "IPSec"),
                    # ER to Meet-Me Rooms
                    _edge("er-a", "mmr-a", "ER Handoff", ""),
                    _edge("er-b", "mmr-b", "ER Handoff", ""),
                    # Cross-connects in MMR
                    _edge("mmr-a", "xx-a", "SMF XConn", ""),
                    _edge("mmr-b", "xx-b", "SMF XConn", ""),
                    # Cross-connect to PE
                    _edge("xx-a", "pe1", "10GbE + BFD", "BGP"),
                    _edge("xx-b", "pe2", "10GbE + BFD", "BGP"),
                    _edge("xx-a", "pe2", "10GbE Backup", "BGP"),
                    _edge("xx-b", "pe1", "10GbE Backup", "BGP"),
                    # VPN also lands on PE routers
                    _edge("az-vpn", "pe1", "IPSec Tunnel", "IPSec"),
                    _edge("az-vpn", "pe2", "IPSec Tunnel", "IPSec"),
                    # MPLS core
                    _edge("pe1", "p1", "100GbE", "MPLS"),
                    _edge("pe2", "p1", "100GbE", "MPLS"),
                    # PE/P to site firewalls
                    _edge("pe1", "site-fw1", "10GbE", "OSPF"),
                    _edge("p1", "site-fw2", "10GbE", "OSPF"),
                    _edge("pe2", "site-fw3", "10GbE", "OSPF"),
                    # Firewall to site core
                    _edge("site-fw1", "sw-a", "10GbE", "OSPF"),
                    _edge("site-fw2", "sw-b", "10GbE", "OSPF"),
                    _edge("site-fw3", "sw-c", "10GbE", "OSPF"),
                ],
            }
        ),
    },
    # ── Multi-Cloud (AWS + Azure) Multi-Home to MAN with Dual POPs ───────
    {
        "id": "tpl-multicloud-dual-pop",
        "name": "Multi-Cloud (AWS + Azure) Dual POP to Metro (MAN)",
        "category": "Hybrid Cloud",
        "description": "AWS and Azure multi-cloud with multi-home connectivity through two geographically diverse POPs (West Coast and East Coast), connecting to a metro area network via MPLS backbone.",
        "tags": json.dumps(
            ["multi-cloud", "aws", "azure", "dual-pop", "metro", "man", "multi-home", "meet-me-room", "cross-connect"]
        ),
        "graph_json": json.dumps(
            {
                "nodes": [
                    # AWS Cloud
                    _node("aws-tgw", "AWS Transit GW", "aws-tgw", 200, 60),
                    _node("aws-vpc", "AWS Prod VPC", "aws-vpc", 100, 200),
                    _node("aws-nfw", "AWS Network FW", "aws-nfw", 300, 200),
                    _node("aws-dx-w", "DX West (LAX)", "aws-dx", 100, 380),
                    _node("aws-dx-e", "DX East (IAD)", "aws-dx", 300, 380),
                    # Azure Cloud
                    _node("az-vwan", "Azure VWAN Hub", "az-vwan", 800, 60),
                    _node("az-vnet", "Azure Prod VNet", "az-vnet", 700, 200),
                    _node("az-fw", "Azure Firewall", "az-fw", 900, 200),
                    _node("az-er-w", "ER West (LAX)", "az-er", 700, 380),
                    _node("az-er-e", "ER East (IAD)", "az-er", 900, 380),
                    # Cloud-to-cloud peering
                    _node("c2c", "Cloud Peering", "cloud-peering", 500, 200),
                    # West Coast Meet-Me Room (LAX colo)
                    _node("mmr-w", "MMR West (Equinix LA1)", "meet-me-room", 250, 480),
                    # East Coast Meet-Me Room (IAD colo)
                    _node("mmr-e", "MMR East (Equinix DC5)", "meet-me-room", 750, 480),
                    # Cross-connects at each POP
                    _node("xx-w-aws", "XConn AWS-W", "cross-connect", 100, 540),
                    _node("xx-w-az", "XConn AZ-W", "cross-connect", 400, 540),
                    _node("xx-e-aws", "XConn AWS-E", "cross-connect", 600, 540),
                    _node("xx-e-az", "XConn AZ-E", "cross-connect", 900, 540),
                    # West Coast POP (LAX)
                    _node(
                        "pop-w-rtr1",
                        "POP-West RTR-1",
                        "router",
                        150,
                        660,
                        {"config": {"asn": "65100", "protocol": "BGP"}},
                    ),
                    _node(
                        "pop-w-rtr2",
                        "POP-West RTR-2",
                        "router",
                        350,
                        660,
                        {"config": {"asn": "65100", "protocol": "BGP"}},
                    ),
                    _node("pop-w-fw", "POP-West FW", "firewall", 250, 660),
                    # East Coast POP (IAD)
                    _node(
                        "pop-e-rtr1",
                        "POP-East RTR-1",
                        "router",
                        650,
                        660,
                        {"config": {"asn": "65200", "protocol": "BGP"}},
                    ),
                    _node(
                        "pop-e-rtr2",
                        "POP-East RTR-2",
                        "router",
                        850,
                        660,
                        {"config": {"asn": "65200", "protocol": "BGP"}},
                    ),
                    _node("pop-e-fw", "POP-East FW", "firewall", 750, 660),
                    # MPLS Backbone
                    _node("mpls-pe-w", "MPLS-PE West", "mpls-pe", 250, 820),
                    _node("mpls-p1", "MPLS-P Core-1", "mpls-p", 400, 820),
                    _node("mpls-p2", "MPLS-P Core-2", "mpls-p", 600, 820),
                    _node("mpls-pe-e", "MPLS-PE East", "mpls-pe", 750, 820),
                    # Metro Sites
                    _node("man-fw1", "MAN Site-A FW", "firewall", 250, 980),
                    _node("man-fw2", "MAN Site-B FW", "firewall", 500, 980),
                    _node("man-fw3", "MAN Site-C FW", "firewall", 750, 980),
                    _node("man-sw1", "Site-A Core", "switch-l3", 250, 1120),
                    _node("man-sw2", "Site-B Core", "switch-l3", 500, 1120),
                    _node("man-sw3", "Site-C Core", "switch-l3", 750, 1120),
                ],
                "edges": [
                    # AWS internal
                    _edge("aws-tgw", "aws-vpc", "", ""),
                    _edge("aws-vpc", "aws-nfw", "", ""),
                    _edge("aws-tgw", "aws-dx-w", "DX 10G", "BGP"),
                    _edge("aws-tgw", "aws-dx-e", "DX 10G", "BGP"),
                    # Azure internal
                    _edge("az-vwan", "az-vnet", "", ""),
                    _edge("az-vnet", "az-fw", "", ""),
                    _edge("az-vwan", "az-er-w", "ER 10G", "BGP"),
                    _edge("az-vwan", "az-er-e", "ER 10G", "BGP"),
                    # Cloud peering
                    _edge("aws-tgw", "c2c", "Peering", "BGP"),
                    _edge("az-vwan", "c2c", "Peering", "BGP"),
                    # CSP circuits to Meet-Me Rooms
                    _edge("aws-dx-w", "mmr-w", "DX Handoff", ""),
                    _edge("aws-dx-e", "mmr-e", "DX Handoff", ""),
                    _edge("az-er-w", "mmr-w", "ER Handoff", ""),
                    _edge("az-er-e", "mmr-e", "ER Handoff", ""),
                    # Cross-connects from MMR to customer side
                    _edge("mmr-w", "xx-w-aws", "SMF XConn", ""),
                    _edge("mmr-w", "xx-w-az", "SMF XConn", ""),
                    _edge("mmr-e", "xx-e-aws", "SMF XConn", ""),
                    _edge("mmr-e", "xx-e-az", "SMF XConn", ""),
                    # Cross-connects to POP routers
                    _edge("xx-w-aws", "pop-w-rtr1", "10GbE", "BGP"),
                    _edge("xx-w-az", "pop-w-rtr2", "10GbE", "BGP"),
                    _edge("xx-e-aws", "pop-e-rtr1", "10GbE", "BGP"),
                    _edge("xx-e-az", "pop-e-rtr2", "10GbE", "BGP"),
                    # POP internal
                    _edge("pop-w-rtr1", "pop-w-fw", "10GbE", ""),
                    _edge("pop-w-rtr2", "pop-w-fw", "10GbE", ""),
                    _edge("pop-e-rtr1", "pop-e-fw", "10GbE", ""),
                    _edge("pop-e-rtr2", "pop-e-fw", "10GbE", ""),
                    # POP cross-connect (resilience)
                    _edge("pop-w-rtr1", "pop-w-rtr2", "ISL", "OSPF"),
                    _edge("pop-e-rtr1", "pop-e-rtr2", "ISL", "OSPF"),
                    # POP to MPLS backbone
                    _edge("pop-w-fw", "mpls-pe-w", "10GbE", "OSPF"),
                    _edge("pop-e-fw", "mpls-pe-e", "10GbE", "OSPF"),
                    # MPLS backbone
                    _edge("mpls-pe-w", "mpls-p1", "100GbE", "MPLS"),
                    _edge("mpls-p1", "mpls-p2", "100GbE", "MPLS"),
                    _edge("mpls-p2", "mpls-pe-e", "100GbE", "MPLS"),
                    _edge("mpls-pe-w", "mpls-p2", "100GbE Diverse", "MPLS"),
                    # MPLS to MAN sites
                    _edge("mpls-pe-w", "man-fw1", "10GbE", "OSPF"),
                    _edge("mpls-p1", "man-fw2", "10GbE", "OSPF"),
                    _edge("mpls-pe-e", "man-fw3", "10GbE", "OSPF"),
                    # MAN firewall to core
                    _edge("man-fw1", "man-sw1", "10GbE", "OSPF"),
                    _edge("man-fw2", "man-sw2", "10GbE", "OSPF"),
                    _edge("man-fw3", "man-sw3", "10GbE", "OSPF"),
                ],
            }
        ),
    },
    # 24 ─ ICS/SCADA Purdue Model
    {
        "id": "tpl-ics-purdue",
        "name": "ICS/SCADA Purdue Model (L0–L5)",
        "category": "OT / Industrial",
        "description": "Purdue Enterprise Reference Architecture for ICS/SCADA networks with 6 security levels, "
        "DMZ between IT and OT, and zone labels using drawing shapes.",
        "tags": json.dumps(["ics", "scada", "purdue", "ot", "industrial", "nist-800-82"]),
        "graph_json": json.dumps(
            {
                "nodes": [
                    # Zone labels (headings)
                    _node(
                        "lbl-l5",
                        "Level 5 — Enterprise Network",
                        "text-heading",
                        320,
                        10,
                        {"config": {"_textColor": "#74b9ff"}},
                    ),
                    _node(
                        "lbl-l4",
                        "Level 4 — Site Business / IT",
                        "text-heading",
                        320,
                        130,
                        {"config": {"_textColor": "#74b9ff"}},
                    ),
                    _node("lbl-dmz", "IT / OT DMZ", "text-heading", 380, 270, {"config": {"_textColor": "#f39c12"}}),
                    _node(
                        "lbl-l3",
                        "Level 3 — Site Operations",
                        "text-heading",
                        330,
                        400,
                        {"config": {"_textColor": "#27ae60"}},
                    ),
                    _node(
                        "lbl-l2",
                        "Level 2 — Area Supervisory",
                        "text-heading",
                        330,
                        530,
                        {"config": {"_textColor": "#27ae60"}},
                    ),
                    _node(
                        "lbl-l1",
                        "Level 1 — Basic Control",
                        "text-heading",
                        340,
                        660,
                        {"config": {"_textColor": "#e67e22"}},
                    ),
                    _node(
                        "lbl-l0",
                        "Level 0 — Physical Process",
                        "text-heading",
                        340,
                        790,
                        {"config": {"_textColor": "#e74c3c"}},
                    ),
                    # Zone boundary boxes
                    _node(
                        "zone-enterprise",
                        "",
                        "draw-rect",
                        20,
                        35,
                        {"config": {"_fill": "#0a1628", "_stroke": "#74b9ff", "_width": 900, "_height": 85}},
                    ),
                    _node(
                        "zone-it",
                        "",
                        "draw-rect",
                        20,
                        155,
                        {"config": {"_fill": "#0a1628", "_stroke": "#74b9ff", "_width": 900, "_height": 100}},
                    ),
                    _node(
                        "zone-dmz",
                        "",
                        "draw-rect",
                        20,
                        295,
                        {
                            "config": {
                                "_fill": "#1a1500",
                                "_stroke": "#f39c12",
                                "_width": 900,
                                "_height": 85,
                                "_strokeWidth": 3,
                            }
                        },
                    ),
                    _node(
                        "zone-ops",
                        "",
                        "draw-rect",
                        20,
                        425,
                        {"config": {"_fill": "#0a180a", "_stroke": "#27ae60", "_width": 900, "_height": 85}},
                    ),
                    _node(
                        "zone-super",
                        "",
                        "draw-rect",
                        20,
                        555,
                        {"config": {"_fill": "#0a180a", "_stroke": "#27ae60", "_width": 900, "_height": 85}},
                    ),
                    _node(
                        "zone-control",
                        "",
                        "draw-rect",
                        20,
                        685,
                        {"config": {"_fill": "#1a1000", "_stroke": "#e67e22", "_width": 900, "_height": 85}},
                    ),
                    _node(
                        "zone-process",
                        "",
                        "draw-rect",
                        20,
                        815,
                        {"config": {"_fill": "#1a0a0a", "_stroke": "#e74c3c", "_width": 900, "_height": 85}},
                    ),
                    # L5 — Enterprise
                    _node("erp", "ERP / SAP", "server", 120, 50),
                    _node("email", "Email Server", "server", 350, 50),
                    _node("inet-fw", "Internet FW", "firewall", 650, 50),
                    _node("inet", "Internet", "cloud", 830, 50),
                    # L4 — IT
                    _node("ad", "Active Directory", "server", 100, 170),
                    _node("av", "AV / Patch Mgmt", "server", 300, 170),
                    _node("historian-mirror", "Historian Mirror", "server", 530, 170),
                    _node("it-sw", "IT Core Switch", "switch-l3", 750, 170),
                    # DMZ
                    _node("dmz-fw-top", "IT/OT FW (North)", "firewall", 200, 310),
                    _node("jump", "Jump Server", "server", 430, 310),
                    _node("ids", "IDS / IPS", "siem", 650, 310),
                    _node("dmz-fw-bot", "IT/OT FW (South)", "firewall", 830, 310),
                    # L3 — Operations
                    _node("historian", "OT Historian", "server", 120, 440),
                    _node("ops-sw", "OT Core Switch", "switch-l3", 380, 440),
                    _node("eng-ws", "Engineering WS", "endpoint-pc", 600, 440),
                    _node("patch-mgmt", "OT Patch Server", "server", 800, 440),
                    # L2 — Supervisory
                    _node("hmi1", "HMI Station 1", "endpoint-pc", 120, 570),
                    _node("hmi2", "HMI Station 2", "endpoint-pc", 320, 570),
                    _node("scada-sw", "SCADA Switch", "switch-l2", 530, 570),
                    _node("scada-srv", "SCADA Server", "server", 750, 570),
                    # L1 — Control
                    _node("plc1", "PLC-01", "endpoint-iot", 120, 700),
                    _node("plc2", "PLC-02", "endpoint-iot", 300, 700),
                    _node("rtu1", "RTU-01", "endpoint-iot", 500, 700),
                    _node("ctrl-sw", "Control Switch", "switch-l2", 700, 700),
                    _node("safety", "Safety Controller", "endpoint-iot", 860, 700),
                    # L0 — Process
                    _node("sensor1", "Temp Sensor", "endpoint-iot", 120, 830),
                    _node("sensor2", "Pressure Sensor", "endpoint-iot", 300, 830),
                    _node("actuator1", "Valve Actuator", "endpoint-iot", 500, 830),
                    _node("actuator2", "Motor Drive", "endpoint-iot", 700, 830),
                    _node("cam1", "Process Camera", "endpoint-camera", 870, 830),
                ],
                "edges": [
                    _edge("erp", "it-sw", "10GbE", ""),
                    _edge("email", "it-sw", "10GbE", ""),
                    _edge("inet-fw", "inet", "WAN", ""),
                    _edge("inet-fw", "it-sw", "10GbE", ""),
                    _edge("ad", "it-sw", "10GbE", ""),
                    _edge("av", "it-sw", "10GbE", ""),
                    _edge("historian-mirror", "it-sw", "10GbE", ""),
                    _edge("it-sw", "dmz-fw-top", "10GbE", ""),
                    _edge("dmz-fw-top", "jump", "", ""),
                    _edge("jump", "ids", "", ""),
                    _edge("ids", "dmz-fw-bot", "", ""),
                    _edge("dmz-fw-bot", "ops-sw", "1GbE", ""),
                    _edge("historian", "ops-sw", "1GbE", ""),
                    _edge("eng-ws", "ops-sw", "1GbE", ""),
                    _edge("patch-mgmt", "ops-sw", "1GbE", ""),
                    _edge("ops-sw", "scada-sw", "1GbE", ""),
                    _edge("hmi1", "scada-sw", "1GbE", ""),
                    _edge("hmi2", "scada-sw", "1GbE", ""),
                    _edge("scada-srv", "scada-sw", "1GbE", ""),
                    _edge("scada-sw", "ctrl-sw", "1GbE", ""),
                    _edge("plc1", "ctrl-sw", "100Mbps", ""),
                    _edge("plc2", "ctrl-sw", "100Mbps", ""),
                    _edge("rtu1", "ctrl-sw", "100Mbps", ""),
                    _edge("safety", "ctrl-sw", "100Mbps", ""),
                    _edge("plc1", "sensor1", "Modbus", ""),
                    _edge("plc1", "sensor2", "Modbus", ""),
                    _edge("plc2", "actuator1", "Modbus", ""),
                    _edge("plc2", "actuator2", "Modbus", ""),
                    _edge("rtu1", "cam1", "Ethernet/IP", ""),
                ],
            }
        ),
    },
    # 25 ─ SASE / SSE Architecture
    {
        "id": "tpl-sase-sse",
        "name": "SASE / SSE Architecture",
        "category": "Security",
        "description": "Secure Access Service Edge — cloud-delivered security with ZTNA, SWG, CASB, and FWaaS. "
        "Branch, remote, and campus users connect through SASE PoPs.",
        "tags": json.dumps(["sase", "sse", "ztna", "swg", "casb", "fwaas", "zero-trust"]),
        "graph_json": json.dumps(
            {
                "nodes": [
                    # Zone labels
                    _node("lbl-users", "User Locations", "text-heading", 80, 10, {"config": {"_textColor": "#74b9ff"}}),
                    _node(
                        "lbl-sase", "SASE / SSE Cloud", "text-heading", 380, 10, {"config": {"_textColor": "#f39c12"}}
                    ),
                    _node("lbl-apps", "Applications", "text-heading", 750, 10, {"config": {"_textColor": "#27ae60"}}),
                    # Zone boxes
                    _node(
                        "zone-users",
                        "",
                        "draw-rounded-rect",
                        20,
                        40,
                        {"config": {"_fill": "#0a1628", "_stroke": "#74b9ff", "_width": 250, "_height": 520}},
                    ),
                    _node(
                        "zone-sase",
                        "",
                        "draw-rounded-rect",
                        310,
                        40,
                        {
                            "config": {
                                "_fill": "#1a1500",
                                "_stroke": "#f39c12",
                                "_width": 340,
                                "_height": 520,
                                "_strokeWidth": 3,
                            }
                        },
                    ),
                    _node(
                        "zone-apps",
                        "",
                        "draw-rounded-rect",
                        690,
                        40,
                        {"config": {"_fill": "#0a180a", "_stroke": "#27ae60", "_width": 260, "_height": 520}},
                    ),
                    # SASE service badges
                    _node(
                        "badge-ztna",
                        "ZTNA",
                        "text-badge",
                        340,
                        60,
                        {"config": {"_fill": "#2b0f3a", "_stroke": "#a29bfe"}},
                    ),
                    _node(
                        "badge-swg",
                        "SWG",
                        "text-badge",
                        420,
                        60,
                        {"config": {"_fill": "#2b0f3a", "_stroke": "#a29bfe"}},
                    ),
                    _node(
                        "badge-casb",
                        "CASB",
                        "text-badge",
                        490,
                        60,
                        {"config": {"_fill": "#2b0f3a", "_stroke": "#a29bfe"}},
                    ),
                    _node(
                        "badge-fwaas",
                        "FWaaS",
                        "text-badge",
                        567,
                        60,
                        {"config": {"_fill": "#2b0f3a", "_stroke": "#a29bfe"}},
                    ),
                    # Users
                    _node("campus", "Campus Users", "endpoint-pc", 70, 80),
                    _node("branch1", "Branch Office A", "router", 70, 180),
                    _node("branch2", "Branch Office B", "router", 70, 280),
                    _node("remote", "Remote Worker", "endpoint-pc", 70, 380),
                    _node("mobile", "Mobile User", "endpoint-phone", 70, 470),
                    # SD-WAN edges
                    _node("sdwan1", "SD-WAN Edge", "sdwan-edge", 200, 180),
                    _node("sdwan2", "SD-WAN Edge", "sdwan-edge", 200, 280),
                    # SASE PoPs
                    _node("pop1", "SASE PoP (East)", "sase-pop", 380, 140),
                    _node("pop2", "SASE PoP (West)", "sase-pop", 380, 260),
                    _node("pop3", "SASE PoP (Central)", "sase-pop", 380, 380),
                    # SASE services
                    _node("ztna", "ZTNA Broker", "firewall", 530, 160),
                    _node("swg", "Secure Web GW", "firewall", 530, 280),
                    _node("casb", "CASB Proxy", "firewall", 530, 400),
                    # Applications
                    _node("saas", "SaaS (M365/GWS)", "cloud", 740, 100),
                    _node("iaas", "IaaS (AWS/Azure)", "aws-vpc", 740, 210),
                    _node("dc-app", "On-Prem DC App", "server", 740, 330),
                    _node("private", "Private App", "server", 740, 440),
                    # Internet
                    _node("inet", "Internet", "cloud", 530, 500),
                ],
                "edges": [
                    _edge("campus", "pop1", "Direct", ""),
                    _edge("branch1", "sdwan1", "LAN", ""),
                    _edge("branch2", "sdwan2", "LAN", ""),
                    _edge("sdwan1", "pop1", "IPSec Tunnel", "IPSec"),
                    _edge("sdwan2", "pop2", "IPSec Tunnel", "IPSec"),
                    _edge("remote", "pop3", "ZTNA Agent", "mTLS"),
                    _edge("mobile", "pop3", "ZTNA Agent", "mTLS"),
                    _edge("pop1", "ztna", "Inspect", ""),
                    _edge("pop2", "swg", "Inspect", ""),
                    _edge("pop3", "casb", "Inspect", ""),
                    _edge("pop1", "pop2", "Backbone", ""),
                    _edge("pop2", "pop3", "Backbone", ""),
                    _edge("ztna", "saas", "Proxy", "mTLS"),
                    _edge("ztna", "iaas", "Proxy", "mTLS"),
                    _edge("swg", "dc-app", "Proxy", ""),
                    _edge("casb", "private", "Proxy", "mTLS"),
                    _edge("swg", "inet", "Filtered", ""),
                ],
            }
        ),
    },
    # 26 ─ Disaster Recovery (Active-Passive)
    {
        "id": "tpl-dr-active-passive",
        "name": "Disaster Recovery (Active-Passive)",
        "category": "Data Center",
        "description": "Active-passive disaster recovery with primary and secondary data center sites, "
        "async replication, and automated failover via DNS/GSLB.",
        "tags": json.dumps(["dr", "disaster-recovery", "active-passive", "replication", "failover"]),
        "graph_json": json.dumps(
            {
                "nodes": [
                    # Zone labels
                    _node(
                        "lbl-primary",
                        "PRIMARY SITE (Active)",
                        "text-heading",
                        100,
                        10,
                        {"config": {"_textColor": "#27ae60"}},
                    ),
                    _node(
                        "lbl-dr", "DR SITE (Standby)", "text-heading", 620, 10, {"config": {"_textColor": "#e74c3c"}}
                    ),
                    _node(
                        "lbl-global", "Global Services", "text-heading", 370, 430, {"config": {"_textColor": "#f39c12"}}
                    ),
                    # Zone boxes
                    _node(
                        "zone-primary",
                        "",
                        "draw-rect",
                        20,
                        40,
                        {"config": {"_fill": "#0a180a", "_stroke": "#27ae60", "_width": 420, "_height": 370}},
                    ),
                    _node(
                        "zone-dr",
                        "",
                        "draw-rect",
                        500,
                        40,
                        {
                            "config": {
                                "_fill": "#1a0a0a",
                                "_stroke": "#e74c3c",
                                "_width": 420,
                                "_height": 370,
                                "_strokeWidth": 2,
                            }
                        },
                    ),
                    _node(
                        "zone-global",
                        "",
                        "draw-rounded-rect",
                        250,
                        460,
                        {"config": {"_fill": "#1a1500", "_stroke": "#f39c12", "_width": 440, "_height": 130}},
                    ),
                    # Status badges
                    _node(
                        "badge-active",
                        "ACTIVE",
                        "text-badge",
                        40,
                        50,
                        {"config": {"_fill": "#0a2a0a", "_stroke": "#27ae60"}},
                    ),
                    _node(
                        "badge-standby",
                        "STANDBY",
                        "text-badge",
                        520,
                        50,
                        {"config": {"_fill": "#2a0a0a", "_stroke": "#e74c3c"}},
                    ),
                    # Primary site
                    _node("p-fw", "Primary FW", "firewall", 80, 90),
                    _node("p-lb", "Primary LB", "load-balancer", 250, 90),
                    _node("p-web1", "Web Server 1", "server", 80, 200),
                    _node("p-web2", "Web Server 2", "server", 250, 200),
                    _node("p-app", "App Server", "server", 160, 300),
                    _node("p-db", "Primary DB", "server", 320, 300),
                    # DR site
                    _node("dr-fw", "DR Firewall", "firewall", 560, 90),
                    _node("dr-lb", "DR Load Balancer", "load-balancer", 730, 90),
                    _node("dr-web1", "DR Web 1", "server", 560, 200),
                    _node("dr-web2", "DR Web 2", "server", 730, 200),
                    _node("dr-app", "DR App Server", "server", 640, 300),
                    _node("dr-db", "DR DB (Replica)", "server", 800, 300),
                    # Global services
                    _node("gslb", "GSLB / DNS", "load-balancer", 310, 490),
                    _node("monitor", "Health Monitor", "siem", 490, 490),
                    _node("users", "End Users", "cloud", 80, 490),
                    # Replication arrow label
                    _node(
                        "lbl-repl", "Async Replication", "text-label", 400, 330, {"config": {"_textColor": "#f39c12"}}
                    ),
                ],
                "edges": [
                    # Primary
                    _edge("p-fw", "p-lb", "10GbE", ""),
                    _edge("p-lb", "p-web1", "10GbE", ""),
                    _edge("p-lb", "p-web2", "10GbE", ""),
                    _edge("p-web1", "p-app", "10GbE", ""),
                    _edge("p-web2", "p-app", "10GbE", ""),
                    _edge("p-app", "p-db", "10GbE", ""),
                    # DR
                    _edge("dr-fw", "dr-lb", "10GbE", ""),
                    _edge("dr-lb", "dr-web1", "10GbE", ""),
                    _edge("dr-lb", "dr-web2", "10GbE", ""),
                    _edge("dr-web1", "dr-app", "10GbE", ""),
                    _edge("dr-web2", "dr-app", "10GbE", ""),
                    _edge("dr-app", "dr-db", "10GbE", ""),
                    # Cross-site replication
                    _edge("p-db", "dr-db", "Async Repl", "IPSec"),
                    # Global
                    _edge("users", "gslb", "DNS", ""),
                    _edge("gslb", "p-fw", "Active", ""),
                    _edge("gslb", "dr-fw", "Failover", ""),
                    _edge("monitor", "p-lb", "Health Check", ""),
                    _edge("monitor", "dr-lb", "Health Check", ""),
                ],
            }
        ),
    },
    # 27 ─ Air-Gapped / Cross-Domain Solution (CDS)
    {
        "id": "tpl-airgap-cds",
        "name": "Air-Gapped / Cross-Domain Solution (CDS)",
        "category": "Security",
        "description": "Air-gapped network with cross-domain solution for controlled data transfer between "
        "classification levels (Unclass ↔ CUI ↔ SECRET). Uses NSA Type 1 encryptors and data diodes.",
        "tags": json.dumps(["airgap", "cds", "cross-domain", "classification", "type1", "diode", "secret", "cui"]),
        "graph_json": json.dumps(
            {
                "nodes": [
                    # Zone labels
                    _node(
                        "lbl-unclass",
                        "UNCLASSIFIED (NIPRNet)",
                        "text-heading",
                        60,
                        10,
                        {"config": {"_textColor": "#27ae60"}},
                    ),
                    _node(
                        "lbl-cui",
                        "CUI // SP-CTI (IL4/IL5)",
                        "text-heading",
                        360,
                        10,
                        {"config": {"_textColor": "#f39c12"}},
                    ),
                    _node(
                        "lbl-secret",
                        "SECRET (SIPRNet / IL6)",
                        "text-heading",
                        700,
                        10,
                        {"config": {"_textColor": "#e74c3c"}},
                    ),
                    _node(
                        "lbl-cds",
                        "Cross-Domain Solution",
                        "text-heading",
                        250,
                        440,
                        {"config": {"_textColor": "#a29bfe"}},
                    ),
                    # Zone boxes
                    _node(
                        "zone-unclass",
                        "",
                        "draw-rect",
                        20,
                        40,
                        {"config": {"_fill": "#0a180a", "_stroke": "#27ae60", "_width": 250, "_height": 380}},
                    ),
                    _node(
                        "zone-cui",
                        "",
                        "draw-rect",
                        320,
                        40,
                        {
                            "config": {
                                "_fill": "#1a1500",
                                "_stroke": "#f39c12",
                                "_width": 280,
                                "_height": 380,
                                "_strokeWidth": 3,
                            }
                        },
                    ),
                    _node(
                        "zone-secret",
                        "",
                        "draw-rect",
                        650,
                        40,
                        {
                            "config": {
                                "_fill": "#1a0a0a",
                                "_stroke": "#e74c3c",
                                "_width": 280,
                                "_height": 380,
                                "_strokeWidth": 3,
                            }
                        },
                    ),
                    _node(
                        "zone-cds",
                        "",
                        "draw-rounded-rect",
                        160,
                        470,
                        {
                            "config": {
                                "_fill": "#120a20",
                                "_stroke": "#a29bfe",
                                "_width": 620,
                                "_height": 160,
                                "_strokeWidth": 3,
                            }
                        },
                    ),
                    # Classification badges
                    _node(
                        "badge-u",
                        "UNCLASSIFIED",
                        "text-badge",
                        50,
                        55,
                        {"config": {"_fill": "#0a2a0a", "_stroke": "#27ae60"}},
                    ),
                    _node(
                        "badge-cui",
                        "CUI // SP-CTI",
                        "text-badge",
                        370,
                        55,
                        {"config": {"_fill": "#2a1a00", "_stroke": "#f39c12"}},
                    ),
                    _node(
                        "badge-s",
                        "SECRET",
                        "text-badge",
                        720,
                        55,
                        {"config": {"_fill": "#2a0a0a", "_stroke": "#e74c3c"}},
                    ),
                    # UNCLASSIFIED network
                    _node("u-fw", "NIPR Firewall", "firewall", 60, 100),
                    _node("u-sw", "NIPR Switch", "switch-l3", 60, 200),
                    _node("u-ws1", "Workstation", "endpoint-pc", 60, 300),
                    _node("u-srv", "Web Server", "server", 180, 200),
                    _node("inet", "Internet / NIPRNet", "cloud", 180, 100),
                    # CUI network
                    _node("c-fw", "CUI Firewall", "firewall", 370, 100),
                    _node("c-enc", "KG-175D (TACLANE)", "kg-175d", 370, 200),
                    _node("c-sw", "CUI Switch", "switch-l3", 500, 200),
                    _node("c-ws1", "CUI Workstation", "endpoint-pc", 370, 310),
                    _node("c-srv", "CUI App Server", "server", 500, 310),
                    # SECRET network
                    _node("s-fw", "SIPR Firewall", "firewall", 710, 100),
                    _node("s-enc", "KG-250 (100G)", "kg-250", 710, 200),
                    _node("s-sw", "SIPR Switch", "switch-l3", 840, 200),
                    _node("s-ws1", "SIPR Workstation", "endpoint-pc", 710, 310),
                    _node("s-srv", "SIPR Server", "server", 840, 310),
                    # CDS components
                    _node("cds-guard", "CDS Guard", "firewall", 260, 510),
                    _node("cds-diode-up", "Data Diode (Up)", "type1-encryptor", 430, 500),
                    _node("cds-diode-dn", "Data Diode (Down)", "type1-encryptor", 430, 570),
                    _node("cds-filter", "Content Filter", "siem", 600, 510),
                    _node(
                        "cds-audit",
                        "Audit Logger",
                        "server",
                        430,
                        650,
                        {"config": {"notes": "Immutable audit trail — NIST AU-6"}},
                    ),
                    # CDS annotation
                    _node(
                        "lbl-diode-note",
                        "One-way data flow (hardware-enforced)",
                        "text-label",
                        350,
                        640,
                        {"config": {"_textColor": "#7a8cb0"}},
                    ),
                ],
                "edges": [
                    # UNCLASS internal
                    _edge("inet", "u-fw", "NIPRNet", ""),
                    _edge("u-fw", "u-sw", "10GbE", ""),
                    _edge("u-sw", "u-ws1", "1GbE", ""),
                    _edge("u-sw", "u-srv", "10GbE", ""),
                    # CUI internal
                    _edge("c-fw", "c-enc", "10GbE", "IPSec"),
                    _edge("c-enc", "c-sw", "10GbE", ""),
                    _edge("c-sw", "c-ws1", "1GbE", ""),
                    _edge("c-sw", "c-srv", "10GbE", ""),
                    # SECRET internal
                    _edge("s-fw", "s-enc", "10GbE", "IPSec"),
                    _edge("s-enc", "s-sw", "10GbE", ""),
                    _edge("s-sw", "s-ws1", "1GbE", ""),
                    _edge("s-sw", "s-srv", "10GbE", ""),
                    # CDS paths
                    _edge("u-sw", "cds-guard", "To CDS", ""),
                    _edge("cds-guard", "cds-diode-up", "Filtered", ""),
                    _edge("cds-diode-up", "cds-filter", "One-Way Up", ""),
                    _edge("cds-filter", "c-fw", "To CUI", ""),
                    _edge("c-sw", "cds-guard", "To CDS", ""),
                    _edge("cds-diode-dn", "cds-filter", "One-Way", ""),
                    _edge("cds-filter", "s-fw", "To SECRET", ""),
                    _edge("cds-guard", "cds-audit", "All Events", ""),
                ],
            }
        ),
    },
    # 28 ─ 5G Private Network (MEC)
    {
        "id": "tpl-5g-private-mec",
        "name": "5G Private Network (MEC)",
        "category": "Wireless / 5G",
        "description": "Private 5G network with Multi-access Edge Computing (MEC), 5G Core (UPF/AMF/SMF), "
        "gNodeBs, and local breakout for ultra-low-latency applications.",
        "tags": json.dumps(["5g", "private-5g", "mec", "edge", "gnodeb", "upf", "amf"]),
        "graph_json": json.dumps(
            {
                "nodes": [
                    # Zone labels
                    _node(
                        "lbl-ran",
                        "Radio Access Network (RAN)",
                        "text-heading",
                        60,
                        10,
                        {"config": {"_textColor": "#74b9ff"}},
                    ),
                    _node(
                        "lbl-edge",
                        "Multi-access Edge Computing (MEC)",
                        "text-heading",
                        340,
                        10,
                        {"config": {"_textColor": "#f39c12"}},
                    ),
                    _node(
                        "lbl-core", "5G Core Network", "text-heading", 710, 10, {"config": {"_textColor": "#9b59b6"}}
                    ),
                    _node(
                        "lbl-apps",
                        "Application Services",
                        "text-heading",
                        370,
                        470,
                        {"config": {"_textColor": "#27ae60"}},
                    ),
                    # Zone boxes
                    _node(
                        "zone-ran",
                        "",
                        "draw-rounded-rect",
                        20,
                        40,
                        {"config": {"_fill": "#0a1628", "_stroke": "#74b9ff", "_width": 250, "_height": 400}},
                    ),
                    _node(
                        "zone-edge",
                        "",
                        "draw-rounded-rect",
                        310,
                        40,
                        {
                            "config": {
                                "_fill": "#1a1500",
                                "_stroke": "#f39c12",
                                "_width": 320,
                                "_height": 400,
                                "_strokeWidth": 3,
                            }
                        },
                    ),
                    _node(
                        "zone-core",
                        "",
                        "draw-rounded-rect",
                        670,
                        40,
                        {"config": {"_fill": "#120a20", "_stroke": "#9b59b6", "_width": 270, "_height": 400}},
                    ),
                    _node(
                        "zone-apps",
                        "",
                        "draw-rounded-rect",
                        230,
                        500,
                        {"config": {"_fill": "#0a180a", "_stroke": "#27ae60", "_width": 500, "_height": 140}},
                    ),
                    # RAN
                    _node("gnb1", "gNodeB (Macro)", "wap", 60, 80),
                    _node("gnb2", "gNodeB (Small Cell)", "wap", 60, 190),
                    _node("gnb3", "gNodeB (Indoor)", "wap", 60, 300),
                    _node("du1", "DU (Distributed Unit)", "server", 180, 130),
                    _node("du2", "DU (Distributed Unit)", "server", 180, 280),
                    _node("ue1", "UE — Smartphone", "endpoint-phone", 60, 390),
                    _node("ue2", "UE — IoT Sensor", "endpoint-iot", 180, 390),
                    # Badges for RAN
                    _node(
                        "badge-nr",
                        "NR n78",
                        "text-badge",
                        40,
                        50,
                        {"config": {"_fill": "#0a1628", "_stroke": "#74b9ff"}},
                    ),
                    # MEC
                    _node("cu", "CU (Central Unit)", "server", 360, 80),
                    _node("mec-srv1", "MEC App Server", "server", 360, 190),
                    _node("mec-srv2", "MEC AI Inference", "server", 530, 190),
                    _node("upf-local", "UPF (Local Breakout)", "router", 450, 300),
                    _node("mec-sw", "MEC Switch", "switch-l3", 360, 300),
                    _node("mec-fw", "MEC Firewall", "firewall", 530, 300),
                    _node("mec-mon", "Edge Monitor", "siem", 530, 80),
                    # 5G Core
                    _node("amf", "AMF", "server", 720, 80),
                    _node("smf", "SMF", "server", 850, 80),
                    _node("upf-central", "UPF (Central)", "router", 720, 190),
                    _node("nrf", "NRF", "server", 850, 190),
                    _node("ausf", "AUSF / UDM", "server", 720, 300),
                    _node("nssf", "NSSF (Slicing)", "server", 850, 300),
                    _node("inet", "Internet / DN", "cloud", 790, 390),
                    # Applications
                    _node("app-ar", "AR/VR App", "server", 280, 530),
                    _node("app-video", "Video Analytics", "server", 430, 530),
                    _node("app-iot", "IoT Platform", "server", 580, 530),
                    _node("app-db", "Edge Database", "server", 430, 590),
                ],
                "edges": [
                    # RAN fronthaul
                    _edge("gnb1", "du1", "eCPRI", ""),
                    _edge("gnb2", "du1", "eCPRI", ""),
                    _edge("gnb3", "du2", "eCPRI", ""),
                    # RAN midhaul
                    _edge("du1", "cu", "F1", ""),
                    _edge("du2", "cu", "F1", ""),
                    # MEC internal
                    _edge("cu", "mec-sw", "25GbE", ""),
                    _edge("mec-sw", "mec-srv1", "25GbE", ""),
                    _edge("mec-sw", "mec-srv2", "25GbE", ""),
                    _edge("mec-sw", "upf-local", "25GbE", ""),
                    _edge("mec-sw", "mec-fw", "25GbE", ""),
                    _edge("mec-mon", "mec-sw", "10GbE", ""),
                    # MEC to Core (N2/N4)
                    _edge("cu", "amf", "N2", ""),
                    _edge("upf-local", "smf", "N4", ""),
                    _edge("upf-local", "upf-central", "N9", ""),
                    # Core internal
                    _edge("amf", "smf", "SBI", ""),
                    _edge("amf", "ausf", "SBI", ""),
                    _edge("smf", "upf-central", "N4", ""),
                    _edge("smf", "nrf", "SBI", ""),
                    _edge("nrf", "nssf", "SBI", ""),
                    _edge("upf-central", "inet", "N6", ""),
                    # Apps
                    _edge("mec-fw", "app-ar", "1GbE", ""),
                    _edge("mec-fw", "app-video", "10GbE", ""),
                    _edge("mec-fw", "app-iot", "1GbE", ""),
                    _edge("app-video", "app-db", "10GbE", ""),
                    _edge("app-iot", "app-db", "1GbE", ""),
                ],
            }
        ),
    },
    # 29 ─ Satellite / SATCOM Backhaul
    {
        "id": "tpl-satcom-backhaul",
        "name": "Satellite / SATCOM Backhaul",
        "category": "Tactical / Remote",
        "description": "Remote site connectivity via satellite (LEO/GEO) with SATCOM terminals, "
        "crypto devices, and tactical edge networking for DDIL environments.",
        "tags": json.dumps(["satcom", "satellite", "vsat", "tactical", "ddil", "remote", "leo", "geo"]),
        "graph_json": json.dumps(
            {
                "nodes": [
                    # Zone labels
                    _node(
                        "lbl-remote",
                        "Remote / Tactical Site",
                        "text-heading",
                        50,
                        10,
                        {"config": {"_textColor": "#e67e22"}},
                    ),
                    _node("lbl-space", "Space Segment", "text-heading", 380, 10, {"config": {"_textColor": "#74b9ff"}}),
                    _node(
                        "lbl-hub",
                        "Teleport / Hub Station",
                        "text-heading",
                        650,
                        10,
                        {"config": {"_textColor": "#27ae60"}},
                    ),
                    _node(
                        "lbl-noc",
                        "Network Operations Center",
                        "text-heading",
                        650,
                        380,
                        {"config": {"_textColor": "#9b59b6"}},
                    ),
                    # Zone boxes
                    _node(
                        "zone-remote",
                        "",
                        "draw-rect",
                        20,
                        40,
                        {
                            "config": {
                                "_fill": "#1a1000",
                                "_stroke": "#e67e22",
                                "_width": 280,
                                "_height": 530,
                                "_strokeWidth": 2,
                            }
                        },
                    ),
                    _node(
                        "zone-space",
                        "",
                        "draw-ellipse",
                        340,
                        60,
                        {"config": {"_fill": "#0a1020", "_stroke": "#74b9ff", "_width": 240, "_height": 200}},
                    ),
                    _node(
                        "zone-hub",
                        "",
                        "draw-rect",
                        620,
                        40,
                        {"config": {"_fill": "#0a180a", "_stroke": "#27ae60", "_width": 300, "_height": 310}},
                    ),
                    _node(
                        "zone-noc",
                        "",
                        "draw-rounded-rect",
                        620,
                        400,
                        {"config": {"_fill": "#120a20", "_stroke": "#9b59b6", "_width": 300, "_height": 160}},
                    ),
                    # Status badge
                    _node(
                        "badge-ddil",
                        "DDIL Environment",
                        "text-badge",
                        40,
                        55,
                        {"config": {"_fill": "#2a1500", "_stroke": "#e67e22"}},
                    ),
                    # Remote site
                    _node("vsat1", "VSAT Terminal", "wap", 60, 100),
                    _node("bgan", "BGAN Terminal", "wap", 200, 100),
                    _node("r-enc", "KG-175G", "kg-175g", 130, 200),
                    _node("r-rtr", "Tactical Router", "router", 130, 290),
                    _node("r-sw", "Field Switch", "switch-l2", 60, 380),
                    _node("r-ws", "Command WS", "endpoint-pc", 60, 470),
                    _node("r-radio", "Tactical Radio", "wap", 200, 380),
                    _node("r-sensor", "ISR Sensor", "endpoint-camera", 200, 470),
                    _node("r-fw", "Edge Firewall", "firewall", 200, 290),
                    # Space
                    _node("sat-geo", "GEO Satellite", "cloud", 390, 90),
                    _node("sat-leo", "LEO Constellation", "cloud", 390, 180),
                    _node("lbl-ka", "Ka/Ku-Band", "text-label", 360, 260, {"config": {"_textColor": "#74b9ff"}}),
                    # Hub station
                    _node("h-dish1", "Hub Antenna 1", "wap", 680, 70),
                    _node("h-dish2", "Hub Antenna 2", "wap", 830, 70),
                    _node("h-enc", "KG-250", "kg-250", 750, 160),
                    _node("h-rtr", "Hub Router", "router", 750, 240),
                    _node("h-fw", "Hub Firewall", "firewall", 680, 300),
                    _node("mpls-pe", "MPLS PE", "mpls-pe", 840, 300),
                    # NOC
                    _node("noc-siem", "SIEM / SOC", "siem", 680, 430),
                    _node("noc-nms", "NMS / Monitoring", "server", 830, 430),
                    _node("noc-ws", "NOC Operator", "endpoint-pc", 750, 510),
                    # Backbone
                    _node("backbone", "Enterprise WAN", "cloud", 480, 450),
                ],
                "edges": [
                    # Remote to space
                    _edge("vsat1", "sat-geo", "Ka-Band Uplink", ""),
                    _edge("bgan", "sat-leo", "L-Band", ""),
                    # Space to hub
                    _edge("sat-geo", "h-dish1", "Ka-Band Downlink", ""),
                    _edge("sat-leo", "h-dish2", "Ku-Band Downlink", ""),
                    # Remote internal
                    _edge("vsat1", "r-enc", "Ethernet", ""),
                    _edge("bgan", "r-enc", "Ethernet", ""),
                    _edge("r-enc", "r-rtr", "Encrypted", "IPSec"),
                    _edge("r-rtr", "r-fw", "10/100", ""),
                    _edge("r-rtr", "r-sw", "10/100", ""),
                    _edge("r-sw", "r-ws", "1GbE", ""),
                    _edge("r-fw", "r-radio", "Tactical", ""),
                    _edge("r-radio", "r-sensor", "UHF/VHF", ""),
                    # Hub internal
                    _edge("h-dish1", "h-enc", "Ethernet", ""),
                    _edge("h-dish2", "h-enc", "Ethernet", ""),
                    _edge("h-enc", "h-rtr", "Decrypted", ""),
                    _edge("h-rtr", "h-fw", "10GbE", "OSPF"),
                    _edge("h-rtr", "mpls-pe", "10GbE", "OSPF"),
                    _edge("mpls-pe", "backbone", "MPLS VPN", "MPLS"),
                    # NOC
                    _edge("h-fw", "noc-siem", "10GbE", ""),
                    _edge("h-fw", "noc-nms", "10GbE", ""),
                    _edge("noc-siem", "noc-ws", "1GbE", ""),
                    _edge("noc-nms", "noc-ws", "1GbE", ""),
                    _edge("backbone", "noc-siem", "Telemetry", ""),
                ],
            }
        ),
    },
    # 30 ─ GCP Cloud Interconnect Hybrid (Well-Architected)
    {
        "id": "tpl-gcp-ic-hybrid",
        "name": "GCP Cloud Interconnect Hybrid (Well-Architected)",
        "category": "Hybrid Cloud",
        "description": "GCP global VPC with redundant Dedicated Cloud Interconnect via Cloud Routers, HA VPN backup, Network Connectivity Center hub, Private Service Connect, Cloud Armor, Flow Logs. Leverages GCP's global VPC (subnets span regions) and single-anycast global LB.",
        "tags": json.dumps(
            [
                "gcp",
                "cloud-interconnect",
                "hybrid",
                "well-architected",
                "global-vpc",
                "ha-vpn",
                "psc",
                "cloud-armor",
                "ncc",
            ]
        ),
        "graph_json": json.dumps(
            {
                "nodes": [
                    # GCP Cloud — Well-Architected (global VPC)
                    _node("gcp-ncc", "Network Connectivity Center", "gcp-ncc", 500, 40),
                    _node(
                        "gcp-vpc",
                        "Global VPC",
                        "gcp-vpc",
                        500,
                        140,
                        {"config": {"flow_logs_enabled": True, "scope": "global"}},
                    ),
                    _node("gcp-sub-us", "US Subnet (us-east1)", "gcp-subnet", 300, 260),
                    _node("gcp-sub-eu", "EU Subnet (europe-west1)", "gcp-subnet", 700, 260),
                    _node(
                        "gcp-router-us",
                        "Cloud Router US",
                        "gcp-router",
                        200,
                        380,
                        {"config": {"bfd_enabled": True, "asn": "16550"}},
                    ),
                    _node(
                        "gcp-router-eu",
                        "Cloud Router EU",
                        "gcp-router",
                        800,
                        380,
                        {"config": {"bfd_enabled": True, "asn": "16550"}},
                    ),
                    _node("gcp-glb", "Global External LB (anycast)", "gcp-gfe", 500, 260),
                    _node("gcp-armor", "Cloud Armor (DDoS+WAF)", "gcp-armor", 700, 140),
                    _node("gcp-psc", "Private Service Connect", "gcp-psc", 300, 380),
                    _node("gcp-dns", "Cloud DNS", "gcp-dns", 300, 140),
                    # HA VPN backup
                    _node("gcp-vpn", "HA VPN Backup", "gcp-vpn", 500, 480, {"config": {"tunnels": 4, "sla": "99.99%"}}),
                    # Dedicated Interconnect (dual diverse locations)
                    _node(
                        "ic-us",
                        "Interconnect US (10G)",
                        "gcp-ic",
                        200,
                        520,
                        {
                            "config": {
                                "bfd_enabled": True,
                                "bandwidth": "10G",
                                "type": "dedicated",
                                "location": "Equinix DC5",
                            }
                        },
                    ),
                    _node(
                        "ic-eu",
                        "Interconnect EU (10G)",
                        "gcp-ic",
                        800,
                        520,
                        {
                            "config": {
                                "bfd_enabled": True,
                                "bandwidth": "10G",
                                "type": "dedicated",
                                "location": "Equinix FR5",
                            }
                        },
                    ),
                    # Colocation
                    _node("mmr-us", "MMR Equinix DC5", "meet-me-room", 200, 640),
                    _node("mmr-eu", "MMR Equinix FR5", "meet-me-room", 800, 640),
                    _node("xx-us", "XConn US SMF", "cross-connect", 200, 720),
                    _node("xx-eu", "XConn EU SMF", "cross-connect", 800, 720),
                    # On-prem (US + EU)
                    _node(
                        "rtr-us",
                        "US Border Router",
                        "router",
                        200,
                        840,
                        {"config": {"asn": "65001", "bfd_enabled": True}},
                    ),
                    _node(
                        "rtr-eu",
                        "EU Border Router",
                        "router",
                        800,
                        840,
                        {"config": {"asn": "65001", "bfd_enabled": True}},
                    ),
                    _node("fw-us", "US Site Firewall", "firewall", 200, 960),
                    _node("fw-eu", "EU Site Firewall", "firewall", 800, 960),
                    _node("sw-us", "US Core Switch", "switch-l3", 200, 1080),
                    _node("sw-eu", "EU Core Switch", "switch-l3", 800, 1080),
                ],
                "edges": [
                    # GCP internal
                    _edge("gcp-ncc", "gcp-vpc", "NCC Hub", ""),
                    _edge("gcp-vpc", "gcp-sub-us", "US Region", ""),
                    _edge("gcp-vpc", "gcp-sub-eu", "EU Region", ""),
                    _edge("gcp-sub-us", "gcp-router-us", "Cloud Router", "BGP"),
                    _edge("gcp-sub-eu", "gcp-router-eu", "Cloud Router", "BGP"),
                    _edge("gcp-vpc", "gcp-glb", "Global Anycast LB", ""),
                    _edge("gcp-glb", "gcp-armor", "DDoS+WAF", ""),
                    _edge("gcp-sub-us", "gcp-psc", "Private Svc Connect", ""),
                    _edge("gcp-vpc", "gcp-dns", "Cloud DNS", ""),
                    # Interconnect via Cloud Routers
                    _edge("gcp-router-us", "ic-us", "VLAN Attach + BFD", "BGP"),
                    _edge("gcp-router-eu", "ic-eu", "VLAN Attach + BFD", "BGP"),
                    # HA VPN backup
                    _edge("gcp-ncc", "gcp-vpn", "HA VPN Backup (4 tunnels)", "IPSec"),
                    # Interconnect to colocation
                    _edge("ic-us", "mmr-us", "IC Handoff", ""),
                    _edge("ic-eu", "mmr-eu", "IC Handoff", ""),
                    _edge("mmr-us", "xx-us", "SMF XConn", ""),
                    _edge("mmr-eu", "xx-eu", "SMF XConn", ""),
                    # Cross-connects to on-prem routers
                    _edge("xx-us", "rtr-us", "10GbE + BFD", "BGP"),
                    _edge("xx-eu", "rtr-eu", "10GbE + BFD", "BGP"),
                    # VPN also lands on-prem
                    _edge("gcp-vpn", "rtr-us", "IPSec Tunnel", "IPSec"),
                    _edge("gcp-vpn", "rtr-eu", "IPSec Tunnel", "IPSec"),
                    # On-prem internal
                    _edge("rtr-us", "fw-us", "10GbE", "OSPF"),
                    _edge("rtr-eu", "fw-eu", "10GbE", "OSPF"),
                    _edge("fw-us", "sw-us", "10GbE", "OSPF"),
                    _edge("fw-eu", "sw-eu", "10GbE", "OSPF"),
                ],
            }
        ),
    },
    # 31 ─ OCI FastConnect Hybrid (Well-Architected)
    {
        "id": "tpl-oci-fc-hybrid",
        "name": "OCI FastConnect Hybrid (Well-Architected)",
        "category": "Hybrid Cloud",
        "description": "OCI with redundant FastConnect via DRG v2 (free transitive routing), VCN Flow Logs, free DDoS protection, free egress over FastConnect. OCI is ~10x cheaper on egress than AWS/Azure/GCP. Three-tier isolation: Region > AD > Fault Domain.",
        "tags": json.dumps(
            ["oci", "fastconnect", "hybrid", "well-architected", "drg-v2", "free-egress", "free-ddos", "fault-domain"]
        ),
        "graph_json": json.dumps(
            {
                "nodes": [
                    # OCI Cloud — Well-Architected
                    _node("oci-drg", "DRG v2 (free transit)", "oci-drg", 500, 40),
                    _node(
                        "oci-vcn1",
                        "Prod VCN",
                        "oci-vcn",
                        300,
                        180,
                        {"config": {"flow_logs_enabled": True, "cidr": "10.1.0.0/16"}},
                    ),
                    _node(
                        "oci-vcn2",
                        "Shared Svcs VCN",
                        "oci-vcn",
                        700,
                        180,
                        {"config": {"flow_logs_enabled": True, "cidr": "10.2.0.0/16"}},
                    ),
                    _node("oci-sub1", "App Subnet (AD1)", "oci-subnet", 200, 320),
                    _node("oci-sub2", "DB Subnet (AD2)", "oci-subnet", 400, 320),
                    _node("oci-sub3", "Shared Subnet", "oci-subnet", 700, 320),
                    _node("oci-lb", "Load Balancer", "oci-lb", 300, 180),
                    _node("oci-waf", "WAF", "oci-waf", 800, 40),
                    _node("oci-ddos", "DDoS (FREE)", "oci-ddos", 200, 40),
                    _node("oci-nsg", "NSG", "oci-nsg", 700, 320),
                    _node("oci-fd1", "Fault Domain 1", "oci-fd", 200, 420),
                    _node("oci-fd2", "Fault Domain 2", "oci-fd", 400, 420),
                    _node("oci-fd3", "Fault Domain 3", "oci-fd", 600, 420),
                    # FastConnect (dual redundant — FREE egress)
                    _node(
                        "fc-a",
                        "FastConnect A (10G)",
                        "oci-fc",
                        250,
                        540,
                        {
                            "config": {
                                "bfd_enabled": True,
                                "bandwidth": "10G",
                                "location": "Equinix DC5",
                                "egress_cost": "FREE",
                            }
                        },
                    ),
                    _node(
                        "fc-b",
                        "FastConnect B (10G)",
                        "oci-fc",
                        750,
                        540,
                        {
                            "config": {
                                "bfd_enabled": True,
                                "bandwidth": "10G",
                                "location": "CoreSite VA1",
                                "egress_cost": "FREE",
                            }
                        },
                    ),
                    # Colocation
                    _node("mmr-a", "MMR Equinix DC5", "meet-me-room", 250, 660),
                    _node("mmr-b", "MMR CoreSite VA1", "meet-me-room", 750, 660),
                    _node("xx-a", "XConn-A SMF", "cross-connect", 250, 740),
                    _node("xx-b", "XConn-B SMF", "cross-connect", 750, 740),
                    # On-prem
                    _node(
                        "rtr1", "Border Router 1", "router", 250, 860, {"config": {"asn": "65001", "bfd_enabled": True}}
                    ),
                    _node(
                        "rtr2", "Border Router 2", "router", 750, 860, {"config": {"asn": "65001", "bfd_enabled": True}}
                    ),
                    _node("fw1", "Site Firewall HA", "firewall", 500, 960, {"config": {"ha_mode": "active-standby"}}),
                    _node("core1", "Core Switch", "switch-l3", 500, 1080),
                ],
                "edges": [
                    # OCI internal — DRG v2 provides free transitive routing
                    _edge("oci-drg", "oci-vcn1", "VCN Attach (free)", ""),
                    _edge("oci-drg", "oci-vcn2", "VCN Attach (free)", ""),
                    _edge("oci-vcn1", "oci-sub1", "AD1", ""),
                    _edge("oci-vcn1", "oci-sub2", "AD2", ""),
                    _edge("oci-vcn2", "oci-sub3", "", ""),
                    _edge("oci-vcn1", "oci-lb", "L7 LB", ""),
                    _edge("oci-lb", "oci-waf", "WAF", ""),
                    _edge("oci-lb", "oci-ddos", "DDoS (FREE)", ""),
                    _edge("oci-sub3", "oci-nsg", "NSG", ""),
                    # Fault Domains (3-tier isolation)
                    _edge("oci-sub1", "oci-fd1", "FD1", ""),
                    _edge("oci-sub1", "oci-fd2", "FD2", ""),
                    _edge("oci-sub2", "oci-fd3", "FD3", ""),
                    # FastConnect via DRG (FREE egress)
                    _edge("oci-drg", "fc-a", "FC 10G + BFD", "BGP"),
                    _edge("oci-drg", "fc-b", "FC 10G + BFD", "BGP"),
                    # FastConnect to colocation
                    _edge("fc-a", "mmr-a", "FC Handoff", ""),
                    _edge("fc-b", "mmr-b", "FC Handoff", ""),
                    _edge("mmr-a", "xx-a", "SMF XConn", ""),
                    _edge("mmr-b", "xx-b", "SMF XConn", ""),
                    # Cross-connects to on-prem
                    _edge("xx-a", "rtr1", "10GbE + BFD", "BGP"),
                    _edge("xx-b", "rtr2", "10GbE + BFD", "BGP"),
                    _edge("xx-a", "rtr2", "10GbE Backup", "BGP"),
                    _edge("xx-b", "rtr1", "10GbE Backup", "BGP"),
                    # On-prem
                    _edge("rtr1", "fw1", "10GbE", "OSPF"),
                    _edge("rtr2", "fw1", "10GbE", "OSPF"),
                    _edge("fw1", "core1", "10GbE", "OSPF"),
                ],
            }
        ),
    },
    # 32 ─ AWS Well-Architected Landing Zone (Central Networking)
    {
        "id": "tpl-aws-landing-zone",
        "name": "AWS Well-Architected Landing Zone (Central Networking)",
        "category": "Hybrid Cloud",
        "description": "AWS Landing Zone pattern from Well-Architected Hybrid Networking Lens: central networking account with Transit Gateway shared via RAM, DX Gateway, VPN backup, multiple spoke VPCs (Prod, Dev, Shared Services), Network Firewall inspection, and Guard Duty. Based on Control Tower + Organizations.",
        "tags": json.dumps(
            [
                "aws",
                "landing-zone",
                "well-architected",
                "control-tower",
                "ram",
                "central-networking",
                "multi-account",
                "transit-gateway",
            ]
        ),
        "graph_json": json.dumps(
            {
                "nodes": [
                    # Central Networking Account
                    _node("tgw", "Transit Gateway (shared via RAM)", "aws-tgw", 500, 60),
                    _node("dxgw", "DX Gateway (global)", "aws-dx-gw", 500, 160),
                    _node("nfw", "Central Network FW", "aws-nfw", 500, 260),
                    _node("r53", "Route 53 Resolver", "aws-r53", 800, 60),
                    _node("shield", "Shield Advanced", "aws-shield", 800, 160),
                    _node("netmgr", "Network Manager", "aws-netmgr", 200, 60),
                    # Spoke VPCs (different accounts via RAM)
                    _node(
                        "vpc-prod",
                        "Prod VPC (Acct 1)",
                        "aws-vpc",
                        200,
                        360,
                        {"config": {"flow_logs_enabled": True, "cidr": "10.1.0.0/16"}},
                    ),
                    _node(
                        "vpc-dev",
                        "Dev VPC (Acct 2)",
                        "aws-vpc",
                        500,
                        360,
                        {"config": {"flow_logs_enabled": True, "cidr": "10.2.0.0/16"}},
                    ),
                    _node(
                        "vpc-shared",
                        "Shared Svcs VPC (Acct 3)",
                        "aws-vpc",
                        800,
                        360,
                        {"config": {"flow_logs_enabled": True, "cidr": "10.3.0.0/16"}},
                    ),
                    # Subnets
                    _node("sub-prod", "Prod Private", "aws-subnet", 200, 460),
                    _node("sub-dev", "Dev Private", "aws-subnet", 500, 460),
                    _node("sub-shared", "Shared Private", "aws-subnet", 800, 460),
                    # PrivateLink
                    _node("pl", "PrivateLink Endpoints", "aws-privatelink", 800, 260),
                    # Connectivity (DX + VPN backup)
                    _node(
                        "dx-a",
                        "DX Primary (10G)",
                        "aws-dx",
                        300,
                        560,
                        {
                            "config": {
                                "bfd_enabled": True,
                                "bandwidth": "10G",
                                "vif_type": "transit",
                                "location": "Equinix DC5",
                            }
                        },
                    ),
                    _node(
                        "dx-b",
                        "DX Secondary (10G)",
                        "aws-dx",
                        700,
                        560,
                        {
                            "config": {
                                "bfd_enabled": True,
                                "bandwidth": "10G",
                                "vif_type": "transit",
                                "location": "CoreSite VA1",
                            }
                        },
                    ),
                    _node("vpn", "VPN Backup", "aws-vpn", 500, 560, {"config": {"tunnels": 2, "ecmp": True}}),
                    # Colocation
                    _node("mmr-a", "MMR Equinix DC5", "meet-me-room", 300, 680),
                    _node("mmr-b", "MMR CoreSite VA1", "meet-me-room", 700, 680),
                    _node("xx-a", "XConn-A", "cross-connect", 300, 760),
                    _node("xx-b", "XConn-B", "cross-connect", 700, 760),
                    # On-prem
                    _node("br1", "Border RTR 1", "router", 300, 860, {"config": {"asn": "65001", "bfd_enabled": True}}),
                    _node("br2", "Border RTR 2", "router", 700, 860, {"config": {"asn": "65001", "bfd_enabled": True}}),
                    _node("fw-onprem", "On-Prem Firewall", "firewall", 500, 960),
                ],
                "edges": [
                    # TGW hub — central networking
                    _edge("tgw", "dxgw", "DX GW Assoc", ""),
                    _edge("tgw", "nfw", "Inspection Route", ""),
                    _edge("tgw", "r53", "DNS", ""),
                    _edge("tgw", "netmgr", "Monitoring", ""),
                    # TGW to spoke VPCs (shared via RAM)
                    _edge("tgw", "vpc-prod", "RAM Shared Attach", ""),
                    _edge("tgw", "vpc-dev", "RAM Shared Attach", ""),
                    _edge("tgw", "vpc-shared", "RAM Shared Attach", ""),
                    # VPC internals
                    _edge("vpc-prod", "sub-prod", "", ""),
                    _edge("vpc-dev", "sub-dev", "", ""),
                    _edge("vpc-shared", "sub-shared", "", ""),
                    _edge("vpc-shared", "pl", "PrivateLink", ""),
                    _edge("vpc-prod", "shield", "DDoS", ""),
                    # DX via DX Gateway
                    _edge("dxgw", "dx-a", "Transit VIF + BFD", "BGP"),
                    _edge("dxgw", "dx-b", "Transit VIF + BFD", "BGP"),
                    _edge("tgw", "vpn", "VPN Backup", "IPSec"),
                    # Colocation
                    _edge("dx-a", "mmr-a", "DX Handoff", ""),
                    _edge("dx-b", "mmr-b", "DX Handoff", ""),
                    _edge("mmr-a", "xx-a", "SMF XConn", ""),
                    _edge("mmr-b", "xx-b", "SMF XConn", ""),
                    # To on-prem
                    _edge("xx-a", "br1", "10GbE + BFD", "BGP"),
                    _edge("xx-b", "br2", "10GbE + BFD", "BGP"),
                    _edge("vpn", "br1", "IPSec", "IPSec"),
                    _edge("vpn", "br2", "IPSec", "IPSec"),
                    # On-prem
                    _edge("br1", "fw-onprem", "10GbE", "OSPF"),
                    _edge("br2", "fw-onprem", "10GbE", "OSPF"),
                ],
            }
        ),
    },
    # 33 ─ AWS SCCA Multi-Account (VDSS / VDMS / TCCM with LZA)
    {
        "id": "tpl-scca-aws",
        "name": "AWS SCCA Multi-Account (GovCloud)",
        "category": "SCCA / Landing Zone",
        "description": "AWS SCCA Multi-Account reference architecture using Landing Zone Accelerator (LZA). VDSS account with inspection VPC and Network Firewall, VDMS account with managed services, Transit Gateway shared via RAM, Direct Connect primary/secondary with VPN backup, and Mission VPC with app/data subnets. IL4/IL5 GovCloud deployment.",
        "tags": json.dumps(
            ["scca", "aws", "govcloud", "multi-account", "disa", "vdss", "vdms", "tccm", "il4", "il5", "landing-zone"]
        ),
        "graph_json": json.dumps(
            {
                "nodes": [
                    # VDSS Account — Inspection / Perimeter
                    _node(
                        "vdss-vpc",
                        "VDSS Inspection VPC",
                        "aws-vpc",
                        200,
                        60,
                        {"config": {"cidr": "10.0.0.0/16", "flow_logs_enabled": True}},
                    ),
                    _node("nfw", "Network Firewall", "aws-nfw", 200, 160),
                    _node("waf", "WAF (Regional)", "aws-waf", 400, 60),
                    _node("shield", "Shield Advanced", "aws-shield", 400, 160),
                    _node("gd-vdss", "GuardDuty", "aws-guardduty", 600, 60),
                    # VDMS Account — Management / Shared Services
                    _node(
                        "vdms-vpc",
                        "VDMS Mgmt VPC",
                        "aws-vpc",
                        800,
                        60,
                        {"config": {"cidr": "10.10.0.0/16", "flow_logs_enabled": True}},
                    ),
                    _node("ad", "Managed AD (DS)", "aws-directory", 800, 160),
                    _node("ssm", "Systems Manager", "aws-ssm", 1000, 60),
                    _node("inspector", "Inspector", "aws-inspector", 1000, 160),
                    _node("sechub", "Security Hub", "aws-sechub", 800, 260),
                    _node("config", "AWS Config", "aws-config", 1000, 260),
                    _node("ct", "CloudTrail (Org)", "aws-cloudtrail", 1200, 60),
                    _node("kms", "KMS (FIPS 140-2)", "aws-kms", 1200, 160),
                    # Network Hub — Transit
                    _node(
                        "tgw",
                        "Transit Gateway (RAM)",
                        "aws-tgw",
                        600,
                        360,
                        {"config": {"asn": "64512", "auto_accept_shared": True}},
                    ),
                    _node("dxgw", "DX Gateway", "aws-dx-gw", 600, 460),
                    # Log Archive Account
                    _node(
                        "log-s3",
                        "S3 Log Bucket (Immutable)",
                        "aws-s3",
                        200,
                        360,
                        {"config": {"versioning": True, "object_lock": True}},
                    ),
                    _node("cw-logs", "CloudWatch Logs", "aws-cloudwatch", 200, 460),
                    # Mission VPC
                    _node(
                        "mission-vpc",
                        "Mission VPC",
                        "aws-vpc",
                        600,
                        560,
                        {"config": {"cidr": "10.20.0.0/16", "flow_logs_enabled": True}},
                    ),
                    _node("app-sub", "App Subnet", "aws-subnet", 500, 660),
                    _node("data-sub", "Data Subnet", "aws-subnet", 700, 660),
                    _node("vpce", "VPC Endpoints (S3, SSM)", "aws-privatelink", 900, 560),
                    # Connectivity — Direct Connect + VPN
                    _node(
                        "dx-pri",
                        "DX Primary (10G)",
                        "aws-dx",
                        400,
                        760,
                        {
                            "config": {
                                "bfd_enabled": True,
                                "bandwidth": "10G",
                                "vif_type": "transit",
                                "location": "Equinix DC5",
                            }
                        },
                    ),
                    _node(
                        "dx-sec",
                        "DX Secondary (10G)",
                        "aws-dx",
                        800,
                        760,
                        {
                            "config": {
                                "bfd_enabled": True,
                                "bandwidth": "10G",
                                "vif_type": "transit",
                                "location": "CoreSite VA1",
                            }
                        },
                    ),
                    _node("vpn-bk", "VPN Backup", "aws-vpn", 600, 760, {"config": {"tunnels": 2, "ecmp": True}}),
                    _node("mmr-a", "MMR Equinix DC5", "meet-me-room", 400, 860),
                    _node("mmr-b", "MMR CoreSite VA1", "meet-me-room", 800, 860),
                    _node("br1", "Border RTR 1", "router", 400, 960, {"config": {"asn": "65001", "bfd_enabled": True}}),
                    _node("br2", "Border RTR 2", "router", 800, 960, {"config": {"asn": "65001", "bfd_enabled": True}}),
                ],
                "edges": [
                    # VDSS inspection chain
                    _edge("vdss-vpc", "nfw", "Inspection", ""),
                    _edge("vdss-vpc", "waf", "L7 Filter", ""),
                    _edge("vdss-vpc", "shield", "DDoS", ""),
                    _edge("vdss-vpc", "gd-vdss", "Threat Intel", ""),
                    # VDMS management
                    _edge("vdms-vpc", "ad", "Directory", "LDAPS"),
                    _edge("vdms-vpc", "ssm", "Mgmt Plane", ""),
                    _edge("vdms-vpc", "inspector", "Vuln Scan", ""),
                    _edge("sechub", "config", "Compliance", ""),
                    _edge("sechub", "gd-vdss", "Findings", ""),
                    _edge("ct", "log-s3", "Trail Logs", ""),
                    _edge("ct", "kms", "Encryption", ""),
                    # TGW hub connections (RAM shared)
                    _edge("tgw", "vdss-vpc", "RAM Attach", ""),
                    _edge("tgw", "vdms-vpc", "RAM Attach", ""),
                    _edge("tgw", "mission-vpc", "RAM Attach", ""),
                    _edge("tgw", "dxgw", "DX GW Assoc", ""),
                    _edge("tgw", "nfw", "Inspection Route", ""),
                    # Logging
                    _edge("cw-logs", "log-s3", "Export", ""),
                    _edge("mission-vpc", "cw-logs", "VPC Flow Logs", ""),
                    # Mission VPC internals
                    _edge("mission-vpc", "app-sub", "", ""),
                    _edge("mission-vpc", "data-sub", "", ""),
                    _edge("mission-vpc", "vpce", "PrivateLink", ""),
                    # DX via DX Gateway
                    _edge("dxgw", "dx-pri", "Transit VIF + BFD", "BGP"),
                    _edge("dxgw", "dx-sec", "Transit VIF + BFD", "BGP"),
                    _edge("tgw", "vpn-bk", "VPN Backup", "IPSec"),
                    # Colocation
                    _edge("dx-pri", "mmr-a", "DX Handoff", ""),
                    _edge("dx-sec", "mmr-b", "DX Handoff", ""),
                    # On-prem
                    _edge("mmr-a", "br1", "10GbE + BFD", "BGP"),
                    _edge("mmr-b", "br2", "10GbE + BFD", "BGP"),
                    _edge("vpn-bk", "br1", "IPSec", "IPSec"),
                    _edge("vpn-bk", "br2", "IPSec", "IPSec"),
                ],
            }
        ),
    },
    # 34 ─ Azure SACA Hub-Spoke (VDSS / VDMS)
    {
        "id": "tpl-scca-azure",
        "name": "Azure SACA Hub-Spoke",
        "category": "SCCA / Landing Zone",
        "description": "Azure Secure Azure Computing Architecture (SACA) hub-spoke topology. Hub VNet with Azure Firewall, App Gateway WAF, and Bastion; identity via Entra ID and Key Vault; Sentinel/Defender monitoring; Prod and Dev spoke VNets peered to hub; ExpressRoute to BCAP with VPN backup. IL4/IL5 Azure Government.",
        "tags": json.dumps(["scca", "saca", "azure", "government", "hub-spoke", "vdss", "vdms", "il4", "il5"]),
        "graph_json": json.dumps(
            {
                "nodes": [
                    # Hub VNet — VDSS perimeter
                    _node(
                        "hub-vnet",
                        "Hub VNet",
                        "azure-vnet",
                        500,
                        60,
                        {"config": {"cidr": "10.0.0.0/16", "flow_logs_enabled": True}},
                    ),
                    _node(
                        "az-fw",
                        "Azure Firewall (Premium)",
                        "azure-firewall",
                        300,
                        160,
                        {"config": {"sku": "Premium", "idps_mode": "Alert and Deny"}},
                    ),
                    _node("agw-waf", "App Gateway + WAF v2", "azure-appgw", 500, 160),
                    _node("bastion", "Azure Bastion", "azure-bastion", 700, 160),
                    _node("ddos", "DDoS Protection Plan", "azure-ddos", 900, 60),
                    # Identity — VDMS
                    _node("entra", "Entra ID (CAC/PIV)", "azure-entra", 200, 260),
                    _node(
                        "kv",
                        "Key Vault (FIPS 140-2 L3)",
                        "azure-keyvault",
                        400,
                        260,
                        {"config": {"sku": "premium", "purge_protection": True}},
                    ),
                    # Monitoring
                    _node("sentinel", "Microsoft Sentinel", "azure-sentinel", 600, 260),
                    _node("defender", "Defender for Cloud", "azure-defender", 800, 260),
                    _node("monitor", "Azure Monitor", "azure-monitor", 1000, 260),
                    # Shared services
                    _node("policy", "Azure Policy", "azure-policy", 200, 60),
                    _node("nsg", "NSG (Hub)", "azure-nsg", 900, 160),
                    # Spoke VNet — Prod
                    _node(
                        "spoke-prod",
                        "Spoke VNet (Prod)",
                        "azure-vnet",
                        300,
                        460,
                        {"config": {"cidr": "10.1.0.0/16", "flow_logs_enabled": True}},
                    ),
                    _node("prod-app", "Prod App Subnet", "azure-subnet", 200, 560),
                    _node("prod-data", "Prod Data Subnet", "azure-subnet", 400, 560),
                    # Spoke VNet — Dev
                    _node(
                        "spoke-dev",
                        "Spoke VNet (Dev)",
                        "azure-vnet",
                        700,
                        460,
                        {"config": {"cidr": "10.2.0.0/16", "flow_logs_enabled": True}},
                    ),
                    _node("dev-app", "Dev App Subnet", "azure-subnet", 700, 560),
                    # Connectivity
                    _node(
                        "er",
                        "ExpressRoute to BCAP",
                        "azure-expressroute",
                        400,
                        660,
                        {"config": {"bandwidth": "10G", "peering": "private", "fastpath_enabled": True}},
                    ),
                    _node(
                        "vpn-gw",
                        "VPN Gateway (Backup)",
                        "azure-vpn",
                        700,
                        660,
                        {"config": {"sku": "VpnGw2AZ", "active_active": True}},
                    ),
                    # On-prem / BCAP
                    _node(
                        "bcap",
                        "BCAP / On-Prem Edge",
                        "router",
                        400,
                        760,
                        {"config": {"asn": "65100", "bfd_enabled": True}},
                    ),
                    _node("onprem-fw", "On-Prem Firewall", "firewall", 700, 760),
                ],
                "edges": [
                    # Hub internals
                    _edge("hub-vnet", "az-fw", "Forced Tunnel", ""),
                    _edge("hub-vnet", "agw-waf", "L7 Ingress", ""),
                    _edge("hub-vnet", "bastion", "Mgmt Plane", ""),
                    _edge("hub-vnet", "nsg", "NSG Rules", ""),
                    _edge("hub-vnet", "ddos", "DDoS", ""),
                    # Identity & security
                    _edge("hub-vnet", "entra", "AAD Auth", "SAML"),
                    _edge("hub-vnet", "kv", "Secrets", "TLS"),
                    _edge("sentinel", "defender", "Threat Feed", ""),
                    _edge("sentinel", "monitor", "Diagnostics", ""),
                    _edge("policy", "hub-vnet", "Governance", ""),
                    _edge("policy", "spoke-prod", "Governance", ""),
                    _edge("policy", "spoke-dev", "Governance", ""),
                    # Hub-Spoke peering
                    _edge("hub-vnet", "spoke-prod", "VNet Peering", ""),
                    _edge("hub-vnet", "spoke-dev", "VNet Peering", ""),
                    # Spoke internals
                    _edge("spoke-prod", "prod-app", "", ""),
                    _edge("spoke-prod", "prod-data", "", ""),
                    _edge("spoke-dev", "dev-app", "", ""),
                    # Spoke traffic through hub firewall
                    _edge("spoke-prod", "az-fw", "UDR Forced Tunnel", ""),
                    _edge("spoke-dev", "az-fw", "UDR Forced Tunnel", ""),
                    # Connectivity
                    _edge("hub-vnet", "er", "ER Circuit", "BGP"),
                    _edge("hub-vnet", "vpn-gw", "VPN Backup", "IPSec"),
                    _edge("er", "bcap", "ExpressRoute Private", "BGP"),
                    _edge("vpn-gw", "onprem-fw", "IPSec Tunnel", "IPSec"),
                ],
            }
        ),
    },
    # 35 ─ OCI SCCA Landing Zone (SCCAv1)
    {
        "id": "tpl-scca-oci",
        "name": "OCI SCCA Landing Zone",
        "category": "SCCA / Landing Zone",
        "description": "Oracle Cloud Infrastructure SCCA Landing Zone (SCCAv1) reference architecture. VDSS VCN with OCI Network Firewall and WAF, VDMS VCN with Cloud Guard and Vault, Workload VCN with app/data subnets, DRG hub for east-west routing, FastConnect primary with VPN backup. IL4/IL5.",
        "tags": json.dumps(["scca", "oci", "oracle", "drg", "vdss", "vdms", "il4", "il5", "landing-zone"]),
        "graph_json": json.dumps(
            {
                "nodes": [
                    # VDSS VCN — Perimeter
                    _node("vdss-vcn", "VDSS VCN", "oci-vcn", 200, 60, {"config": {"cidr": "10.0.0.0/16"}}),
                    _node("oci-nfw", "OCI Network Firewall", "oci-nfw", 200, 160),
                    _node("oci-waf", "OCI WAF", "oci-waf", 400, 60),
                    _node("oci-ddos", "DDoS Protection", "oci-ddos", 400, 160),
                    _node("oci-lb", "Load Balancer", "oci-lb", 600, 60),
                    _node("oci-nsg", "NSG (VDSS)", "oci-nsg", 600, 160),
                    # VDMS VCN — Management
                    _node("vdms-vcn", "VDMS VCN", "oci-vcn", 800, 60, {"config": {"cidr": "10.10.0.0/16"}}),
                    _node("cg", "Cloud Guard", "oci-cloudguard", 800, 160),
                    _node(
                        "vault",
                        "OCI Vault (HSM)",
                        "oci-vault",
                        1000,
                        60,
                        {"config": {"key_type": "AES-256", "hsm_backed": True}},
                    ),
                    _node("vscan", "Vulnerability Scanning", "oci-vscan", 1000, 160),
                    _node("iddom", "Identity Domains (CAC)", "oci-identity", 800, 260),
                    _node("audit", "Audit Service", "oci-audit", 1000, 260),
                    # Hub — DRG
                    _node("drg", "Dynamic Routing Gateway", "oci-drg", 500, 360, {"config": {"type": "DRGv2"}}),
                    # Workload VCN
                    _node("wl-vcn", "Workload VCN", "oci-vcn", 500, 460, {"config": {"cidr": "10.20.0.0/16"}}),
                    _node("wl-app", "App Subnet", "oci-subnet", 400, 560),
                    _node("wl-data", "Data Subnet", "oci-subnet", 600, 560),
                    # Connectivity
                    _node(
                        "fc",
                        "FastConnect (10G)",
                        "oci-fastconnect",
                        300,
                        660,
                        {"config": {"bandwidth": "10G", "redundancy": "HA"}},
                    ),
                    _node("vpn-oci", "IPSec VPN Backup", "oci-vpn", 700, 660, {"config": {"tunnels": 2}}),
                    # On-prem
                    _node(
                        "cpe",
                        "CPE (On-Prem Edge)",
                        "router",
                        500,
                        760,
                        {"config": {"asn": "65200", "bfd_enabled": True}},
                    ),
                ],
                "edges": [
                    # VDSS internals
                    _edge("vdss-vcn", "oci-nfw", "Inspection", ""),
                    _edge("vdss-vcn", "oci-waf", "L7 Filter", ""),
                    _edge("vdss-vcn", "oci-ddos", "DDoS", ""),
                    _edge("vdss-vcn", "oci-lb", "Ingress", ""),
                    _edge("vdss-vcn", "oci-nsg", "SL/NSG Rules", ""),
                    # VDMS internals
                    _edge("vdms-vcn", "cg", "Posture Mgmt", ""),
                    _edge("vdms-vcn", "vault", "Key Mgmt", ""),
                    _edge("vdms-vcn", "vscan", "Vuln Scan", ""),
                    _edge("vdms-vcn", "iddom", "IAM", ""),
                    _edge("audit", "vdms-vcn", "Audit Logs", ""),
                    # DRG hub
                    _edge("drg", "vdss-vcn", "VCN Attach", ""),
                    _edge("drg", "vdms-vcn", "VCN Attach", ""),
                    _edge("drg", "wl-vcn", "VCN Attach", ""),
                    # Workload internals
                    _edge("wl-vcn", "wl-app", "", ""),
                    _edge("wl-vcn", "wl-data", "", ""),
                    # Connectivity
                    _edge("drg", "fc", "FastConnect", "BGP"),
                    _edge("drg", "vpn-oci", "IPSec VPN", "IPSec"),
                    _edge("fc", "cpe", "10GbE + BFD", "BGP"),
                    _edge("vpn-oci", "cpe", "IPSec Tunnel", "IPSec"),
                ],
            }
        ),
    },
    # 36 ─ GCP Assured Workloads (IL4/IL5)
    {
        "id": "tpl-scca-gcp",
        "name": "GCP Assured Workloads (IL4/IL5)",
        "category": "SCCA / Landing Zone",
        "description": "GCP IL4 Assured Workloads reference architecture. Shared VPC in a host project with Cloud Router and Cloud NAT, Cloud Armor for L7 protection, Security Command Center, Cloud KMS with EKM, Org Policy constraints, service project (Prod) with app/data subnets, Cloud Interconnect for dedicated connectivity, and Assured Workloads folder for compliance boundary.",
        "tags": json.dumps(["scca", "gcp", "assured-workloads", "shared-vpc", "il4", "il5"]),
        "graph_json": json.dumps(
            {
                "nodes": [
                    # Governance
                    _node(
                        "aw-folder",
                        "Assured Workloads Folder",
                        "gcp-folder",
                        500,
                        60,
                        {"config": {"compliance_regime": "IL4", "restriction": "us-regions-only"}},
                    ),
                    # Host Project — Shared VPC
                    _node("host-proj", "Host Project", "gcp-project", 300, 160),
                    _node(
                        "shared-vpc",
                        "Shared VPC",
                        "gcp-vpc",
                        300,
                        260,
                        {"config": {"cidr": "10.0.0.0/16", "flow_logs_enabled": True}},
                    ),
                    _node("cloud-rtr", "Cloud Router", "gcp-router", 100, 360, {"config": {"asn": "16550"}}),
                    _node("cloud-nat", "Cloud NAT", "gcp-nat", 100, 260),
                    # Security
                    _node("armor", "Cloud Armor", "gcp-armor", 700, 160),
                    _node("scc", "Security Command Center", "gcp-scc", 700, 260),
                    _node(
                        "cloud-kms",
                        "Cloud KMS (EKM)",
                        "gcp-kms",
                        900,
                        160,
                        {"config": {"protection_level": "HSM", "ekm_enabled": True}},
                    ),
                    _node("org-policy", "Org Policy", "gcp-orgpolicy", 900, 260),
                    # Service Project — Prod
                    _node("svc-proj", "Service Project (Prod)", "gcp-project", 500, 360),
                    _node("prod-app", "Prod App Subnet", "gcp-subnet", 400, 460),
                    _node("prod-data", "Prod Data Subnet", "gcp-subnet", 600, 460),
                    # Logging
                    _node("log-sink", "Log Sink (Cloud Logging)", "gcp-logging", 900, 360),
                    # Connectivity
                    _node(
                        "interconnect",
                        "Cloud Interconnect (Dedicated)",
                        "gcp-interconnect",
                        300,
                        560,
                        {"config": {"bandwidth": "10G", "redundancy": "HA"}},
                    ),
                    _node(
                        "onprem-rtr",
                        "On-Prem Router",
                        "router",
                        300,
                        660,
                        {"config": {"asn": "65300", "bfd_enabled": True}},
                    ),
                ],
                "edges": [
                    # Governance
                    _edge("aw-folder", "host-proj", "Compliance Boundary", ""),
                    _edge("aw-folder", "svc-proj", "Compliance Boundary", ""),
                    _edge("org-policy", "aw-folder", "Constraints", ""),
                    # Host project
                    _edge("host-proj", "shared-vpc", "Shared VPC Host", ""),
                    _edge("shared-vpc", "cloud-rtr", "Dynamic Routes", "BGP"),
                    _edge("shared-vpc", "cloud-nat", "Egress NAT", ""),
                    # Security
                    _edge("shared-vpc", "armor", "L7 WAF", ""),
                    _edge("scc", "shared-vpc", "Threat Detection", ""),
                    _edge("cloud-kms", "svc-proj", "CMEK", ""),
                    _edge("scc", "log-sink", "Findings", ""),
                    # Service project (shared VPC consumer)
                    _edge("shared-vpc", "svc-proj", "Subnet Sharing", ""),
                    _edge("svc-proj", "prod-app", "", ""),
                    _edge("svc-proj", "prod-data", "", ""),
                    # Connectivity
                    _edge("cloud-rtr", "interconnect", "Cloud Interconnect", "BGP"),
                    _edge("interconnect", "onprem-rtr", "Dedicated 10GbE", "BGP"),
                ],
            }
        ),
    },
    # 37 ─ IBM Cloud DoD VPC Landing Zone
    {
        "id": "tpl-scca-ibm",
        "name": "IBM Cloud DoD VPC Landing Zone",
        "category": "SCCA / Landing Zone",
        "description": "IBM Cloud VPC Landing Zone for DoD workloads. Management VPC with Bastion and monitoring, Workload VPC with app/data subnets, Transit Gateway for VPC interconnect, Direct Link for dedicated on-prem connectivity, Security and Compliance Center (SCC), Key Protect and HPCS for key management, and App ID for identity. FedRAMP High.",
        "tags": json.dumps(["scca", "ibm", "vpc", "transit-gateway", "fedramp-high"]),
        "graph_json": json.dumps(
            {
                "nodes": [
                    # Management VPC
                    _node(
                        "mgmt-vpc",
                        "Management VPC",
                        "ibm-vpc",
                        200,
                        60,
                        {"config": {"cidr": "10.0.0.0/16", "flow_logs_enabled": True}},
                    ),
                    _node("bastion", "Bastion (Teleport)", "ibm-vsi", 200, 160),
                    _node("monitoring", "IBM Monitoring", "ibm-monitoring", 200, 260),
                    # Workload VPC
                    _node(
                        "wl-vpc",
                        "Workload VPC",
                        "ibm-vpc",
                        600,
                        60,
                        {"config": {"cidr": "10.10.0.0/16", "flow_logs_enabled": True}},
                    ),
                    _node("wl-app", "App Subnet", "ibm-subnet", 500, 160),
                    _node("wl-data", "Data Subnet", "ibm-subnet", 700, 160),
                    # Security
                    _node("scc", "Security & Compliance Center", "ibm-scc", 400, 360),
                    _node("kp", "Key Protect", "ibm-keyprotect", 600, 360, {"config": {"fips_validated": True}}),
                    _node(
                        "hpcs",
                        "HPCS (FIPS 140-2 L4)",
                        "ibm-hpcs",
                        800,
                        360,
                        {"config": {"protection_level": "HSM", "fips_level": "L4"}},
                    ),
                    # Network
                    _node("tgw", "Transit Gateway", "ibm-tgw", 400, 160),
                    _node(
                        "dl",
                        "Direct Link (Dedicated)",
                        "ibm-directlink",
                        400,
                        460,
                        {"config": {"bandwidth": "10G", "redundancy": "HA"}},
                    ),
                    # Identity
                    _node("appid", "App ID (MFA/SSO)", "ibm-appid", 800, 60),
                    # On-prem
                    _node(
                        "onprem-rtr",
                        "On-Prem Router",
                        "router",
                        400,
                        560,
                        {"config": {"asn": "65400", "bfd_enabled": True}},
                    ),
                ],
                "edges": [
                    # Management VPC internals
                    _edge("mgmt-vpc", "bastion", "SSH Jump", "SSH"),
                    _edge("mgmt-vpc", "monitoring", "Sysdig/LogDNA", ""),
                    # Workload VPC internals
                    _edge("wl-vpc", "wl-app", "", ""),
                    _edge("wl-vpc", "wl-data", "", ""),
                    _edge("wl-vpc", "appid", "IAM Auth", "OIDC"),
                    # Transit Gateway connects VPCs
                    _edge("tgw", "mgmt-vpc", "TGW Attach", ""),
                    _edge("tgw", "wl-vpc", "TGW Attach", ""),
                    # Security
                    _edge("scc", "mgmt-vpc", "Compliance Scan", ""),
                    _edge("scc", "wl-vpc", "Compliance Scan", ""),
                    _edge("kp", "wl-vpc", "Envelope Encryption", ""),
                    _edge("hpcs", "kp", "Root Key", ""),
                    # Connectivity
                    _edge("tgw", "dl", "Direct Link", "BGP"),
                    _edge("dl", "onprem-rtr", "Dedicated 10GbE", "BGP"),
                    # Cross-VPC management
                    _edge("bastion", "wl-app", "SSH Mgmt", "SSH"),
                ],
            }
        ),
    },
    # 38 ─ Azure Security Baseline — Hub-Spoke
    {
        "id": "tpl-az-security-baseline",
        "name": "Azure Security Baseline — Hub-Spoke",
        "category": "Well-Architected",
        "description": "Defense-in-depth hub-spoke architecture aligned to the Microsoft Cloud Security Benchmark (MCSB). Hub VNet with Azure Firewall, Bastion, and NSG for perimeter. Microsoft Defender for Cloud and Sentinel for detection. Azure Monitor, Key Vault, and Entra ID for operations. Azure Policy for governance. Spoke VNet with App/Data subnets. ExpressRoute for hybrid connectivity and DDoS Protection at the edge.",
        "tags": json.dumps(["azure", "mcsb", "security-baseline", "hub-spoke", "well-architected"]),
        "graph_json": json.dumps(
            {
                "nodes": [
                    _node(
                        "hub-vnet",
                        "Hub VNet",
                        "azure-vnet",
                        400,
                        80,
                        {"config": {"cidr": "10.0.0.0/16", "role": "hub"}},
                    ),
                    _node("az-fw", "Azure Firewall", "firewall", 400, 180),
                    _node("bastion", "Azure Bastion", "azure-bastion", 200, 180),
                    _node("nsg", "Network Security Group", "azure-nsg", 600, 180),
                    _node("defender", "Defender for Cloud", "azure-defender", 800, 80),
                    _node("sentinel", "Microsoft Sentinel", "azure-sentinel", 800, 180),
                    _node("monitor", "Azure Monitor", "azure-monitor", 800, 280),
                    _node(
                        "keyvault",
                        "Key Vault (FIPS 140-2)",
                        "azure-keyvault",
                        600,
                        380,
                        {"config": {"fips_validated": True}},
                    ),
                    _node("entra", "Entra ID", "azure-entra", 200, 380),
                    _node("policy", "Azure Policy (CIS)", "azure-policy", 200, 80),
                    _node(
                        "spoke-vnet",
                        "Spoke VNet",
                        "azure-vnet",
                        400,
                        280,
                        {"config": {"cidr": "10.1.0.0/16", "role": "spoke"}},
                    ),
                    _node(
                        "app-sub",
                        "App Subnet",
                        "azure-subnet",
                        300,
                        380,
                        {"config": {"cidr": "10.1.1.0/24", "tier": "app"}},
                    ),
                    _node(
                        "data-sub",
                        "Data Subnet",
                        "azure-subnet",
                        500,
                        380,
                        {"config": {"cidr": "10.1.2.0/24", "tier": "data"}},
                    ),
                    _node("expressroute", "ExpressRoute", "azure-expressroute", 100, 180),
                    _node("ddos", "DDoS Protection", "azure-ddos", 100, 80),
                    _node("monitor-diag", "Diagnostic Settings", "azure-diagnostics", 600, 80),
                ],
                "edges": [
                    _edge("hub-vnet", "az-fw", "Inspection", ""),
                    _edge("hub-vnet", "bastion", "Mgmt Access", "SSH/RDP"),
                    _edge("hub-vnet", "nsg", "Segmentation", ""),
                    _edge("hub-vnet", "spoke-vnet", "VNet Peering", ""),
                    _edge("az-fw", "spoke-vnet", "Filtered Traffic", ""),
                    _edge("spoke-vnet", "app-sub", "", ""),
                    _edge("spoke-vnet", "data-sub", "", ""),
                    _edge("defender", "sentinel", "Alerts", ""),
                    _edge("sentinel", "monitor", "Log Analytics", ""),
                    _edge("keyvault", "data-sub", "Encryption Keys", ""),
                    _edge("entra", "hub-vnet", "IAM Auth", "SAML/OIDC"),
                    _edge("policy", "hub-vnet", "Governance", ""),
                    _edge("expressroute", "hub-vnet", "Hybrid Link", ""),
                    _edge("ddos", "hub-vnet", "DDoS Protect", ""),
                    _edge("monitor-diag", "sentinel", "Activity Logs", ""),
                ],
            }
        ),
    },
    # 39 ─ GCP Security Foundations Baseline
    {
        "id": "tpl-gcp-security-baseline",
        "name": "GCP Security Foundations Baseline",
        "category": "Well-Architected",
        "description": "GCP security foundations architecture based on Google Cloud Security Foundations Guide. Shared VPC with Cloud Router and Cloud NAT for network egress. Cloud Armor for L7 DDoS/WAF, Security Command Center for threat detection, Cloud KMS for encryption, Org Policies for governance guardrails, Assured Workloads for compliance boundary. Service project with App/Data subnets, Log Sink for audit, and Cloud Interconnect for hybrid connectivity.",
        "tags": json.dumps(["gcp", "security-foundations", "shared-vpc", "well-architected"]),
        "graph_json": json.dumps(
            {
                "nodes": [
                    _node(
                        "shared-vpc",
                        "Shared VPC (Host)",
                        "gcp-vpc",
                        400,
                        80,
                        {"config": {"cidr": "10.0.0.0/16", "role": "host"}},
                    ),
                    _node("cloud-router", "Cloud Router", "gcp-router", 200, 180),
                    _node("cloud-nat", "Cloud NAT", "gcp-nat", 200, 280),
                    _node("cloud-armor", "Cloud Armor (WAF)", "gcp-armor", 600, 80),
                    _node("scc", "Security Command Center", "gcp-scc", 800, 80),
                    _node("cloud-kms", "Cloud KMS (HSM)", "gcp-kms", 800, 180, {"config": {"protection_level": "HSM"}}),
                    _node("org-policy", "Org Policy Service", "gcp-orgpolicy", 200, 80),
                    _node("assured", "Assured Workloads", "gcp-assured", 600, 180),
                    _node("svc-project", "Service Project", "gcp-project", 400, 280),
                    _node(
                        "app-sub",
                        "App Subnet",
                        "gcp-subnet",
                        300,
                        380,
                        {"config": {"cidr": "10.0.1.0/24", "tier": "app"}},
                    ),
                    _node(
                        "data-sub",
                        "Data Subnet",
                        "gcp-subnet",
                        500,
                        380,
                        {"config": {"cidr": "10.0.2.0/24", "tier": "data"}},
                    ),
                    _node("log-sink", "Log Sink (GCS)", "gcp-logsink", 800, 280),
                    _node("interconnect", "Cloud Interconnect", "gcp-interconnect", 100, 180),
                    _node("iap", "Identity-Aware Proxy", "gcp-iap", 600, 280),
                ],
                "edges": [
                    _edge("shared-vpc", "cloud-router", "Routing", "BGP"),
                    _edge("cloud-router", "cloud-nat", "NAT Gateway", ""),
                    _edge("cloud-armor", "shared-vpc", "L7 Protect", ""),
                    _edge("scc", "shared-vpc", "Threat Detection", ""),
                    _edge("cloud-kms", "data-sub", "Encryption Keys", ""),
                    _edge("org-policy", "shared-vpc", "Governance", ""),
                    _edge("assured", "svc-project", "Compliance Boundary", ""),
                    _edge("shared-vpc", "svc-project", "Shared VPC", ""),
                    _edge("svc-project", "app-sub", "", ""),
                    _edge("svc-project", "data-sub", "", ""),
                    _edge("log-sink", "scc", "Audit Logs", ""),
                    _edge("interconnect", "shared-vpc", "Hybrid Link", ""),
                    _edge("iap", "app-sub", "Zero Trust Access", ""),
                ],
            }
        ),
    },
    # 40 ─ OCI Security Posture Baseline
    {
        "id": "tpl-oci-security-baseline",
        "name": "OCI Security Posture Baseline",
        "category": "Well-Architected",
        "description": "OCI security posture architecture aligned to CIS Oracle Cloud Infrastructure Benchmark. VCN with DRG and Network Firewall for perimeter. Cloud Guard for threat detection, OCI Vault (HSM-backed) for key management, VSS for vulnerability scanning, Identity Domains for IAM. Audit service for compliance logging, workload/app/data subnets for segmentation, and FastConnect for hybrid connectivity.",
        "tags": json.dumps(["oci", "cis-benchmark", "cloud-guard", "well-architected"]),
        "graph_json": json.dumps(
            {
                "nodes": [
                    _node("vcn", "Production VCN", "oci-vcn", 400, 80, {"config": {"cidr": "10.0.0.0/16"}}),
                    _node("drg", "Dynamic Routing GW", "oci-drg", 200, 80),
                    _node("nfw", "Network Firewall", "oci-nfw", 400, 180),
                    _node("nsg", "Network Security Group", "oci-nsg", 600, 180),
                    _node("cloud-guard", "Cloud Guard", "oci-cloudguard", 800, 80),
                    _node(
                        "vault", "OCI Vault (HSM)", "oci-vault", 800, 180, {"config": {"vault_type": "VIRTUAL_PRIVATE"}}
                    ),
                    _node("vss", "Vulnerability Scanning", "oci-vss", 800, 280),
                    _node("identity", "Identity Domains", "oci-identity", 200, 380),
                    _node("audit", "Audit Service", "oci-audit", 600, 380),
                    _node(
                        "workload-sub",
                        "Workload Subnet",
                        "oci-subnet",
                        300,
                        280,
                        {"config": {"cidr": "10.0.1.0/24", "tier": "workload"}},
                    ),
                    _node(
                        "app-sub",
                        "App Subnet",
                        "oci-subnet",
                        400,
                        380,
                        {"config": {"cidr": "10.0.2.0/24", "tier": "app"}},
                    ),
                    _node(
                        "data-sub",
                        "Data Subnet",
                        "oci-subnet",
                        600,
                        280,
                        {"config": {"cidr": "10.0.3.0/24", "tier": "data"}},
                    ),
                    _node("fastconnect", "FastConnect", "oci-fastconnect", 100, 80),
                    _node("events", "Events Service", "oci-events", 600, 80),
                ],
                "edges": [
                    _edge("drg", "vcn", "Hub Routing", ""),
                    _edge("vcn", "nfw", "Inspection", ""),
                    _edge("nfw", "workload-sub", "Filtered", ""),
                    _edge("vcn", "nsg", "Segmentation", ""),
                    _edge("cloud-guard", "vcn", "Threat Detection", ""),
                    _edge("vault", "data-sub", "Encryption Keys", ""),
                    _edge("vss", "workload-sub", "Vuln Scan", ""),
                    _edge("identity", "vcn", "IAM Auth", ""),
                    _edge("audit", "events", "Audit Trail", ""),
                    _edge("vcn", "app-sub", "", ""),
                    _edge("vcn", "data-sub", "", ""),
                    _edge("fastconnect", "drg", "Hybrid Link", ""),
                    _edge("events", "cloud-guard", "Notifications", ""),
                ],
            }
        ),
    },
    # 41 ─ IBM Cloud Security Baseline
    {
        "id": "tpl-ibm-security-baseline",
        "name": "IBM Cloud Security Baseline",
        "category": "Well-Architected",
        "description": "IBM Cloud security baseline architecture aligned to FedRAMP High and IBM Cloud Framework for Financial Services. Management VPC and Workload VPC connected via Transit Gateway. Security Groups for micro-segmentation, SCC for compliance posture, Key Protect and HPCS for key management, App ID for identity, Activity Tracker for audit. Direct Link for hybrid connectivity with App/Data subnets in workload VPC.",
        "tags": json.dumps(["ibm", "scc", "fedramp-high", "well-architected"]),
        "graph_json": json.dumps(
            {
                "nodes": [
                    _node("mgmt-vpc", "Management VPC", "ibm-vpc", 200, 80, {"config": {"role": "management"}}),
                    _node("workload-vpc", "Workload VPC", "ibm-vpc", 600, 80, {"config": {"role": "workload"}}),
                    _node("transit-gw", "Transit Gateway", "ibm-tgw", 400, 80),
                    _node("sg", "Security Groups", "ibm-sg", 400, 180),
                    _node("scc", "Security & Compliance Center", "ibm-scc", 800, 80),
                    _node("key-protect", "Key Protect", "ibm-kp", 800, 180),
                    _node("hpcs", "Hyper Protect Crypto", "ibm-hpcs", 800, 280),
                    _node("appid", "App ID", "ibm-appid", 200, 280),
                    _node("at", "Activity Tracker", "ibm-at", 600, 280),
                    _node("direct-link", "Direct Link", "ibm-dl", 100, 80),
                    _node("app-sub", "App Subnet", "ibm-subnet", 500, 380, {"config": {"zone": "1", "tier": "app"}}),
                    _node("data-sub", "Data Subnet", "ibm-subnet", 700, 380, {"config": {"zone": "2", "tier": "data"}}),
                ],
                "edges": [
                    _edge("mgmt-vpc", "transit-gw", "Peering", ""),
                    _edge("workload-vpc", "transit-gw", "Peering", ""),
                    _edge("sg", "workload-vpc", "Micro-Seg", ""),
                    _edge("sg", "mgmt-vpc", "Micro-Seg", ""),
                    _edge("scc", "workload-vpc", "Compliance Scan", ""),
                    _edge("key-protect", "data-sub", "Encryption Keys", ""),
                    _edge("hpcs", "key-protect", "BYOK / HSM", ""),
                    _edge("appid", "mgmt-vpc", "IAM Auth", "OIDC"),
                    _edge("at", "scc", "Audit Trail", ""),
                    _edge("direct-link", "mgmt-vpc", "Hybrid Link", ""),
                    _edge("workload-vpc", "app-sub", "", ""),
                    _edge("workload-vpc", "data-sub", "", ""),
                ],
            }
        ),
    },
    # 42 ─ AWS Well-Architected Security Baseline
    {
        "id": "tpl-wa-security-baseline",
        "name": "AWS Well-Architected Security Baseline",
        "category": "Well-Architected",
        "description": "Defense-in-depth architecture aligned to the AWS Well-Architected Security Pillar (SEC01-SEC11). VPC with public/private subnets, ALB at edge, Network Firewall for inspection, WAF + Shield for perimeter protection, GuardDuty + Security Hub + Config + Inspector for detection, CloudTrail + CloudWatch for logging, KMS + Secrets Manager for data protection, IAM Identity Center for identity, and VPC Flow Logs for network visibility.",
        "tags": json.dumps(["well-architected", "security-pillar", "defense-in-depth", "sec01-sec11", "aws"]),
        "graph_json": json.dumps(
            {
                "nodes": [
                    # VPC and subnets
                    _node(
                        "vpc",
                        "Production VPC",
                        "aws-vpc",
                        500,
                        60,
                        {"config": {"cidr": "10.0.0.0/16", "flow_logs_enabled": True}},
                    ),
                    _node(
                        "pub-sub",
                        "Public Subnet",
                        "aws-subnet",
                        300,
                        160,
                        {"config": {"cidr": "10.0.1.0/24", "tier": "public"}},
                    ),
                    _node(
                        "app-sub",
                        "App Subnet (Private)",
                        "aws-subnet",
                        500,
                        160,
                        {"config": {"cidr": "10.0.10.0/24", "tier": "private"}},
                    ),
                    _node(
                        "data-sub",
                        "Data Subnet (Private)",
                        "aws-subnet",
                        700,
                        160,
                        {"config": {"cidr": "10.0.20.0/24", "tier": "private"}},
                    ),
                    # Edge / Perimeter (SEC05)
                    _node("alb", "Application LB", "aws-alb", 300, 260),
                    _node("waf", "WAF (Regional)", "aws-waf", 150, 260),
                    _node("shield", "Shield Advanced", "aws-shield", 150, 360),
                    _node("nfw", "Network Firewall", "aws-nfw", 500, 260, {"config": {"stateful_inspection": True}}),
                    # Detection (SEC04)
                    _node("gd", "GuardDuty", "aws-guardduty", 800, 260),
                    _node("sechub", "Security Hub (NIST 800-53)", "aws-securityhub", 800, 360),
                    _node("config", "AWS Config", "aws-config", 1000, 260),
                    _node("inspector", "Inspector", "aws-inspector", 1000, 360),
                    # Logging (SEC04)
                    _node("ct", "CloudTrail", "aws-ct", 800, 460),
                    _node("cw", "CloudWatch", "aws-cloudwatch", 1000, 460),
                    _node("flowlogs", "VPC Flow Logs", "aws-flowlogs", 500, 460),
                    # Data Protection (SEC07/08/09)
                    _node("kms", "KMS (FIPS 140-2)", "aws-kms", 700, 560, {"config": {"fips_validated": True}}),
                    _node("secrets", "Secrets Manager", "aws-secretsmanager", 900, 560),
                    # Identity (SEC02/03)
                    _node("idc", "IAM Identity Center", "aws-idc", 300, 560),
                    # Network visibility
                    _node("vpce", "VPC Endpoints (S3, SSM)", "aws-privatelink", 500, 560),
                ],
                "edges": [
                    # VPC structure
                    _edge("vpc", "pub-sub", "", ""),
                    _edge("vpc", "app-sub", "", ""),
                    _edge("vpc", "data-sub", "", ""),
                    # Edge chain
                    _edge("waf", "alb", "L7 Filter", "HTTPS"),
                    _edge("shield", "alb", "DDoS Protect", ""),
                    _edge("alb", "app-sub", "Forward", "HTTPS"),
                    # Inspection
                    _edge("nfw", "vpc", "Inspection", ""),
                    _edge("nfw", "pub-sub", "Ingress Inspection", ""),
                    # Detection
                    _edge("gd", "sechub", "Findings", ""),
                    _edge("config", "sechub", "Compliance", ""),
                    _edge("inspector", "sechub", "Vuln Findings", ""),
                    # Logging
                    _edge("ct", "cw", "Trail Logs", ""),
                    _edge("flowlogs", "cw", "Flow Data", ""),
                    _edge("vpc", "flowlogs", "VPC Flow Logs", ""),
                    # Data protection
                    _edge("kms", "data-sub", "Encryption Keys", ""),
                    _edge("secrets", "app-sub", "Secrets", ""),
                    # Identity
                    _edge("idc", "vpc", "IAM Auth", "SAML"),
                    # Private endpoints
                    _edge("vpce", "app-sub", "PrivateLink", ""),
                    _edge("vpce", "data-sub", "PrivateLink", ""),
                ],
            }
        ),
    },
    # 43 ─ SCCA IL6 SECRET Multi-Enclave
    {
        "id": "tpl-scca-il6-secret",
        "name": "SCCA IL6 SECRET Multi-Enclave",
        "category": "SCCA / Landing Zone",
        "description": "IL6 SECRET SCCA architecture for AWS Secret Region or Azure Government Secret. Air-gapped with no internet gateway, NSA Type 1 encryption, Cross-Domain Solution (CDS) for classification boundaries, CloudHSM/mHSM for FIPS 140-2 L3, HBSS endpoint security, and SIPRNet demarcation. All traffic encrypted with Type 1 encryptors.",
        "tags": json.dumps(
            ["scca", "il6", "secret", "air-gapped", "type-1", "cds", "cross-domain", "cloudhsm", "hbss", "siprnet"]
        ),
        "graph_json": json.dumps(
            {
                "nodes": [
                    # Classification Boundary (left column)
                    _node(
                        "sipr-border",
                        "SIPRNet Border Router",
                        "router",
                        60,
                        60,
                        {"config": {"asn": "65500", "bfd_enabled": True}},
                    ),
                    _node(
                        "type1-a",
                        "Type 1 Encryptor (KG-175D)",
                        "encryptor",
                        60,
                        180,
                        {"config": {"encryption": "NSA Type 1", "speed_mbps": 10000}},
                    ),
                    _node(
                        "cds",
                        "Cross-Domain Solution",
                        "firewall",
                        60,
                        300,
                        {"config": {"classification_high": "SECRET", "classification_low": "CUI"}},
                    ),
                    _node("guard", "Content Guard / Filter", "firewall", 60, 420),
                    # VDSS Enclave (center-left)
                    _node(
                        "vdss-vpc",
                        "VDSS Enclave (SECRET)",
                        "aws-vpc",
                        300,
                        60,
                        {"config": {"cidr": "10.100.0.0/16", "flow_logs_enabled": True, "classification": "SECRET"}},
                    ),
                    _node("nfw", "Network Firewall (IDS/IPS)", "aws-nfw", 300, 180),
                    _node("waf", "WAF (L7 Inspection)", "aws-waf", 300, 300),
                    _node("gwlb", "Gateway LB (Inline)", "aws-gwlb", 300, 420),
                    # VDMS Enclave (center)
                    _node(
                        "vdms-vpc",
                        "VDMS Enclave (SECRET)",
                        "aws-vpc",
                        540,
                        60,
                        {"config": {"cidr": "10.101.0.0/16", "flow_logs_enabled": True}},
                    ),
                    _node(
                        "hsm",
                        "CloudHSM (FIPS 140-2 L3)",
                        "aws-kms",
                        540,
                        180,
                        {"config": {"fips_level": "140-2 L3", "dedicated": True}},
                    ),
                    _node("ad", "Managed AD (CAC/PIV)", "aws-ad", 540, 300),
                    _node("ssm", "Systems Manager (HBSS)", "aws-ssm", 540, 420),
                    _node("ct", "CloudTrail (Org Trail)", "aws-ct", 540, 540),
                    # Hub
                    _node("tgw", "Transit Gateway (Isolated)", "aws-tgw", 420, 660),
                    # Mission Enclave (center-right)
                    _node(
                        "mission-vpc",
                        "Mission VPC (SECRET)",
                        "aws-vpc",
                        660,
                        60,
                        {"config": {"cidr": "10.200.0.0/16", "flow_logs_enabled": True, "classification": "SECRET"}},
                    ),
                    _node("app-sub", "App Subnet (Private)", "aws-subnet", 660, 180),
                    _node("data-sub", "Data Subnet (Isolated)", "aws-subnet", 660, 300),
                    _node("ep-ssm", "VPC Endpoint (SSM)", "aws-gw-ep", 660, 420),
                    _node("ep-kms", "VPC Endpoint (KMS)", "aws-gw-ep", 660, 540),
                    # Log Archive (right)
                    _node(
                        "log-vpc", "Log Archive (Immutable)", "aws-vpc", 840, 60, {"config": {"cidr": "10.250.0.0/16"}}
                    ),
                    _node(
                        "s3-logs",
                        "S3 Log Bucket (WORM)",
                        "server",
                        840,
                        180,
                        {"config": {"mfa_delete": True, "versioning": True, "object_lock": True}},
                    ),
                    _node("guardduty", "GuardDuty (SECRET)", "aws-guardduty", 840, 300),
                    _node("sechub", "Security Hub", "aws-securityhub", 840, 420),
                    # On-prem
                    _node("onprem-fw", "On-Prem SECRET FW", "firewall", 60, 660),
                ],
                "edges": [
                    # Classification boundary chain
                    _edge("sipr-border", "type1-a", "Encrypted", "Type 1"),
                    _edge("type1-a", "cds", "SECRET→CUI boundary", ""),
                    _edge("cds", "guard", "Content filter", ""),
                    _edge("guard", "vdss-vpc", "Inspected", ""),
                    # VDSS inspection chain
                    _edge("vdss-vpc", "nfw", "Inspection", ""),
                    _edge("nfw", "waf", "L7 Filter", ""),
                    _edge("waf", "gwlb", "Inline", ""),
                    # VDMS management
                    _edge("vdms-vpc", "hsm", "Key Mgmt", ""),
                    _edge("vdms-vpc", "ad", "Directory", "LDAPS"),
                    _edge("vdms-vpc", "ssm", "Endpoint Mgmt", ""),
                    _edge("vdms-vpc", "ct", "Audit Trail", ""),
                    # TGW hub connections
                    _edge("tgw", "vdss-vpc", "Isolated Attach", ""),
                    _edge("tgw", "vdms-vpc", "Isolated Attach", ""),
                    _edge("tgw", "mission-vpc", "Isolated Attach", ""),
                    _edge("tgw", "log-vpc", "Isolated Attach", ""),
                    _edge("gwlb", "tgw", "Inspection Route", ""),
                    # Mission VPC internals
                    _edge("mission-vpc", "app-sub", "", ""),
                    _edge("mission-vpc", "data-sub", "", ""),
                    _edge("mission-vpc", "ep-ssm", "PrivateLink", ""),
                    _edge("mission-vpc", "ep-kms", "PrivateLink", ""),
                    # Log Archive
                    _edge("log-vpc", "s3-logs", "WORM Logs", ""),
                    _edge("log-vpc", "guardduty", "Threat Intel", ""),
                    _edge("log-vpc", "sechub", "Findings", ""),
                    # On-prem connectivity
                    _edge("sipr-border", "onprem-fw", "10GbE", "OSPF"),
                    _edge("onprem-fw", "tgw", "IPSec", "IPSec"),
                ],
            }
        ),
    },
    # 44 ─ DISA BCAP / CNAP Reference Design
    {
        "id": "tpl-scca-bcap-cnap",
        "name": "DISA BCAP / CNAP Reference Design",
        "category": "SCCA / Landing Zone",
        "description": "DISA Cloud Native Access Point (CNAP) reference design — boundary protection between DISN and commercial cloud. Shows BCAP firewall chain, IDS/IPS sensors, traffic aggregation, multi-CSP gateway, and TCCM credential validation at boundary. Based on DISA SCCA FRD §2.1.1.",
        "tags": json.dumps(
            ["scca", "bcap", "cnap", "disa", "disn", "boundary", "ids-ips", "tccm", "multi-csp", "colocation"]
        ),
        "graph_json": json.dumps(
            {
                "nodes": [
                    # DISN Side
                    _node(
                        "disn-rtr", "DISN Router", "router", 60, 200, {"config": {"asn": "65000", "network": "DISN"}}
                    ),
                    _node("disn-fw", "DISN Perimeter FW", "firewall", 60, 350),
                    # BCAP Zone
                    _node("bcap-fw-ext", "BCAP External Firewall", "firewall", 300, 100),
                    _node(
                        "bcap-ids",
                        "BCAP IDS/IPS Sensor",
                        "firewall",
                        300,
                        250,
                        {"config": {"mode": "inline", "signatures": "DoD"}},
                    ),
                    _node("bcap-proxy", "Reverse Proxy / TLS Termination", "load-balancer", 300, 400),
                    _node("bcap-fw-int", "BCAP Internal Firewall", "firewall", 300, 550),
                    _node(
                        "tccm-gate",
                        "TCCM Credential Gate",
                        "aws-idc",
                        300,
                        700,
                        {"config": {"purpose": "Validate cloud credentials before DISN connectivity"}},
                    ),
                    _node("bcap-log", "BCAP Syslog Collector", "server", 480, 100),
                    _node("bcap-mgmt", "BCAP Management (OOB)", "server", 480, 250),
                    # Cloud Gateway
                    _node(
                        "dx-a",
                        "DX / ER Circuit A",
                        "aws-dx",
                        600,
                        150,
                        {"config": {"bandwidth": "10G", "bfd_enabled": True}},
                    ),
                    _node(
                        "dx-b",
                        "DX / ER Circuit B (Diverse)",
                        "aws-dx",
                        600,
                        350,
                        {"config": {"bandwidth": "10G", "bfd_enabled": True, "location": "Diverse Path"}},
                    ),
                    _node("vpn-backup", "VPN Backup (IPSec)", "aws-vpn", 600, 550),
                    # Cloud Side
                    _node("tgw-aws", "AWS TGW (GovCloud)", "aws-tgw", 800, 150),
                    _node("vwan-az", "Azure vWAN (Gov)", "az-vwan", 800, 350),
                    _node("drg-oci", "OCI DRG (Gov)", "oci-drg", 800, 550),
                    # Colocation
                    _node("mmr", "Meet-Me Room (Equinix)", "meet-me-room", 480, 400),
                    _node("xconn-a", "Cross-Connect A", "cross-connect", 480, 550),
                    _node("xconn-b", "Cross-Connect B", "cross-connect", 480, 700),
                    # Monitoring
                    _node("siem", "DoD SIEM (Splunk/ArcSight)", "server", 800, 700),
                ],
                "edges": [
                    # DISN to BCAP
                    _edge("disn-rtr", "disn-fw", "10GbE", "OSPF"),
                    _edge("disn-fw", "bcap-fw-ext", "Perimeter", ""),
                    # BCAP inspection chain
                    _edge("bcap-fw-ext", "bcap-ids", "Inline Inspection", ""),
                    _edge("bcap-ids", "bcap-proxy", "TLS Termination", ""),
                    _edge("bcap-proxy", "bcap-fw-int", "Inspected", ""),
                    _edge("bcap-fw-int", "tccm-gate", "Credential Check", ""),
                    # BCAP logging
                    _edge("bcap-fw-ext", "bcap-log", "Syslog", ""),
                    _edge("bcap-ids", "bcap-log", "Alert Feed", ""),
                    _edge("bcap-log", "siem", "SIEM Feed", "TLS"),
                    _edge("bcap-mgmt", "bcap-ids", "OOB Mgmt", "SSH"),
                    # TCCM to colocation
                    _edge("tccm-gate", "mmr", "Authorized", ""),
                    _edge("mmr", "xconn-a", "", ""),
                    _edge("mmr", "xconn-b", "", ""),
                    # Cross-connects to cloud
                    _edge("xconn-a", "dx-a", "SMF", ""),
                    _edge("xconn-b", "dx-b", "SMF", ""),
                    _edge("tccm-gate", "vpn-backup", "IPSec Backup", "IPSec"),
                    # Cloud side
                    _edge("dx-a", "tgw-aws", "Transit VIF", "BGP"),
                    _edge("dx-b", "vwan-az", "ER Circuit", "BGP"),
                    _edge("vpn-backup", "drg-oci", "IPSec", "IPSec"),
                ],
            }
        ),
    },
    # 45 ─ Multi-Cloud SCCA (AWS + Azure)
    {
        "id": "tpl-scca-multicloud-aws-azure",
        "name": "Multi-Cloud SCCA (AWS + Azure)",
        "category": "SCCA / Landing Zone",
        "description": "Coordinated SCCA deployment spanning AWS GovCloud and Azure Government. Shared BCAP boundary, unified TCCM credential federation (IAM Identity Center ↔ Entra ID), cross-CSP logging aggregation, and dual VDSS stacks with synchronized security policies. Connected via Megaport/Equinix Fabric cloud peering.",
        "tags": json.dumps(
            [
                "scca",
                "multi-cloud",
                "aws",
                "azure",
                "govcloud",
                "government",
                "megaport",
                "federation",
                "vdss",
                "vdms",
                "tccm",
            ]
        ),
        "graph_json": json.dumps(
            {
                "nodes": [
                    # AWS Side
                    _node("aws-tgw", "AWS Transit Gateway", "aws-tgw", 60, 100),
                    _node("aws-nfw", "AWS Network Firewall", "aws-nfw", 60, 250),
                    _node("aws-waf", "AWS WAF", "aws-waf", 60, 400),
                    _node("aws-gd", "GuardDuty", "aws-guardduty", 240, 100),
                    _node("aws-sh", "Security Hub", "aws-securityhub", 240, 250),
                    _node("aws-ct", "CloudTrail", "aws-ct", 240, 400),
                    _node("aws-kms", "KMS (GovCloud)", "aws-kms", 240, 550),
                    _node("aws-idc", "IAM Identity Center", "aws-idc", 60, 550),
                    _node(
                        "aws-mission",
                        "AWS Mission VPC",
                        "aws-vpc",
                        360,
                        100,
                        {"config": {"cidr": "10.1.0.0/16", "flow_logs_enabled": True}},
                    ),
                    _node("aws-app", "App Subnet", "aws-subnet", 360, 250),
                    _node("aws-data", "Data Subnet", "aws-subnet", 360, 400),
                    # Cloud Peering Bridge (center)
                    _node(
                        "peering",
                        "Cloud Peering (Megaport)",
                        "cloud-peering",
                        480,
                        300,
                        {"config": {"provider": "Megaport", "bandwidth": "10G"}},
                    ),
                    _node(
                        "unified-log",
                        "Unified Log Aggregator",
                        "server",
                        480,
                        500,
                        {"config": {"purpose": "Cross-CSP SIEM correlation"}},
                    ),
                    # Azure Side
                    _node("az-vwan", "Azure Virtual WAN", "az-vwan", 900, 100),
                    _node("az-fw", "Azure Firewall Premium", "az-fw", 900, 250),
                    _node("az-appgw", "App Gateway WAF", "az-appgw", 900, 400),
                    _node("az-def", "Defender for Cloud", "az-defender", 720, 100),
                    _node("az-sen", "Sentinel", "az-sentinel", 720, 250),
                    _node("az-mon", "Monitor", "az-monitor", 720, 400),
                    _node("az-kv", "Key Vault (Gov)", "az-keyvault", 720, 550),
                    _node("az-entra", "Entra ID (Federation)", "az-entra", 900, 550),
                    _node(
                        "az-mission",
                        "Azure Mission VNet",
                        "az-vnet",
                        600,
                        100,
                        {"config": {"cidr": "10.2.0.0/16", "flow_logs_enabled": True}},
                    ),
                    _node("az-app", "App Subnet", "az-subnet", 600, 250),
                    _node("az-data", "Data Subnet", "az-subnet", 600, 400),
                    # BCAP (bottom center)
                    _node(
                        "bcap",
                        "DISA BCAP (Shared)",
                        "firewall",
                        480,
                        700,
                        {"config": {"purpose": "Shared DISN boundary for both CSPs"}},
                    ),
                ],
                "edges": [
                    # AWS internal
                    _edge("aws-tgw", "aws-nfw", "Inspection", ""),
                    _edge("aws-nfw", "aws-waf", "L7 Filter", ""),
                    _edge("aws-tgw", "aws-mission", "TGW Attach", ""),
                    _edge("aws-mission", "aws-app", "", ""),
                    _edge("aws-mission", "aws-data", "", ""),
                    # AWS security
                    _edge("aws-tgw", "aws-gd", "Threat Intel", ""),
                    _edge("aws-tgw", "aws-sh", "Findings", ""),
                    _edge("aws-sh", "aws-ct", "Trail Logs", ""),
                    _edge("aws-mission", "aws-kms", "Encryption Keys", ""),
                    # Azure internal
                    _edge("az-vwan", "az-fw", "Inspection", ""),
                    _edge("az-fw", "az-appgw", "L7 Filter", ""),
                    _edge("az-vwan", "az-mission", "VNet Attach", ""),
                    _edge("az-mission", "az-app", "", ""),
                    _edge("az-mission", "az-data", "", ""),
                    # Azure security
                    _edge("az-vwan", "az-def", "Threat Intel", ""),
                    _edge("az-def", "az-sen", "Alerts", ""),
                    _edge("az-sen", "az-mon", "Log Analytics", ""),
                    _edge("az-mission", "az-kv", "Encryption Keys", ""),
                    # Bridge — Cloud Peering
                    _edge("aws-tgw", "peering", "Cloud Peering", "BGP"),
                    _edge("peering", "az-vwan", "Cloud Peering", "BGP"),
                    # Federation
                    _edge("aws-idc", "az-entra", "SAML Federation", "SAML"),
                    # Logging
                    _edge("aws-ct", "unified-log", "Log Feed", "TLS"),
                    _edge("az-mon", "unified-log", "Log Feed", "TLS"),
                    # BCAP
                    _edge("bcap", "aws-tgw", "DX", "BGP"),
                    _edge("bcap", "az-vwan", "ER", "BGP"),
                ],
            }
        ),
    },
    # 46 ─ SCCA Disaster Recovery & Continuity
    {
        "id": "tpl-scca-dr-continuity",
        "name": "SCCA Disaster Recovery & Continuity",
        "category": "SCCA / Landing Zone",
        "description": "SCCA-compliant disaster recovery architecture with dual-region VDSS/VDMS replicas, cross-region TGW peering, S3 cross-region replication for immutable logs, mission VPC failover with Route 53 health checks, and BCAP redundant connectivity. Supports RTO <4h / RPO <1h for IL4/IL5 workloads.",
        "tags": json.dumps(
            [
                "scca",
                "disaster-recovery",
                "dr",
                "continuity",
                "dual-region",
                "failover",
                "route53",
                "crr",
                "rto-4h",
                "rpo-1h",
            ]
        ),
        "graph_json": json.dumps(
            {
                "nodes": [
                    # Primary Region (us-gov-west-1)
                    _node("pri-tgw", "TGW Primary (us-gov-west-1)", "aws-tgw", 100, 60),
                    _node("pri-nfw", "Network Firewall (Primary)", "aws-nfw", 100, 200),
                    _node(
                        "pri-vdms",
                        "VDMS VPC (Primary)",
                        "aws-vpc",
                        280,
                        60,
                        {"config": {"cidr": "10.1.0.0/16", "flow_logs_enabled": True}},
                    ),
                    _node("pri-ad", "Managed AD (Primary)", "aws-ad", 280, 200),
                    _node("pri-sechub", "Security Hub (Primary)", "aws-securityhub", 280, 340),
                    _node(
                        "pri-mission",
                        "Mission VPC (Primary)",
                        "aws-vpc",
                        100,
                        380,
                        {"config": {"cidr": "10.10.0.0/16", "flow_logs_enabled": True}},
                    ),
                    _node("pri-app", "App Subnet", "aws-subnet", 100, 500),
                    _node("pri-data", "Data Subnet (RDS Multi-AZ)", "aws-subnet", 280, 500),
                    _node(
                        "pri-s3",
                        "Log Bucket (Primary)",
                        "server",
                        280,
                        640,
                        {"config": {"versioning": True, "crr_enabled": True}},
                    ),
                    # DR Region (us-gov-east-1)
                    _node("dr-tgw", "TGW DR (us-gov-east-1)", "aws-tgw", 640, 60),
                    _node("dr-nfw", "Network Firewall (DR)", "aws-nfw", 640, 200),
                    _node(
                        "dr-vdms",
                        "VDMS VPC (DR)",
                        "aws-vpc",
                        820,
                        60,
                        {"config": {"cidr": "10.2.0.0/16", "flow_logs_enabled": True}},
                    ),
                    _node("dr-ad", "Managed AD (DR Replica)", "aws-ad", 820, 200),
                    _node("dr-sechub", "Security Hub (DR)", "aws-securityhub", 820, 340),
                    _node(
                        "dr-mission",
                        "Mission VPC (DR Standby)",
                        "aws-vpc",
                        640,
                        380,
                        {"config": {"cidr": "10.20.0.0/16", "flow_logs_enabled": True}},
                    ),
                    _node("dr-app", "App Subnet (Standby)", "aws-subnet", 640, 500),
                    _node("dr-data", "Data Subnet (RDS Read Replica)", "aws-subnet", 820, 500),
                    _node(
                        "dr-s3",
                        "Log Bucket (DR Replica)",
                        "server",
                        820,
                        640,
                        {"config": {"versioning": True, "crr_target": True}},
                    ),
                    # Shared / Cross-Region
                    _node(
                        "r53",
                        "Route 53 (Health Check + Failover)",
                        "aws-r53",
                        460,
                        60,
                        {"config": {"routing_policy": "failover", "health_check": True}},
                    ),
                    _node(
                        "tgw-peer",
                        "TGW Peering (Cross-Region)",
                        "aws-tgw",
                        460,
                        200,
                        {"config": {"purpose": "Cross-region TGW peering for DR failover"}},
                    ),
                    _node("bcap-pri", "BCAP (Primary Path)", "firewall", 460, 500),
                    _node("bcap-dr", "BCAP (DR Path)", "firewall", 460, 640),
                ],
                "edges": [
                    # Primary internal
                    _edge("pri-tgw", "pri-nfw", "Inspection", ""),
                    _edge("pri-tgw", "pri-vdms", "Attach", ""),
                    _edge("pri-tgw", "pri-mission", "Attach", ""),
                    _edge("pri-vdms", "pri-ad", "Directory", ""),
                    _edge("pri-vdms", "pri-sechub", "Findings", ""),
                    _edge("pri-mission", "pri-app", "", ""),
                    _edge("pri-mission", "pri-data", "", ""),
                    _edge("pri-vdms", "pri-s3", "Log Export", ""),
                    # DR internal
                    _edge("dr-tgw", "dr-nfw", "Inspection", ""),
                    _edge("dr-tgw", "dr-vdms", "Attach", ""),
                    _edge("dr-tgw", "dr-mission", "Attach", ""),
                    _edge("dr-vdms", "dr-ad", "Directory", ""),
                    _edge("dr-vdms", "dr-sechub", "Findings", ""),
                    _edge("dr-mission", "dr-app", "", ""),
                    _edge("dr-mission", "dr-data", "", ""),
                    _edge("dr-vdms", "dr-s3", "Log Export", ""),
                    # Cross-region
                    _edge("pri-tgw", "tgw-peer", "Peering", "BGP"),
                    _edge("tgw-peer", "dr-tgw", "Peering", "BGP"),
                    _edge("pri-s3", "dr-s3", "S3 CRR", "TLS"),
                    _edge("pri-ad", "dr-ad", "AD Replication", ""),
                    # DNS
                    _edge("r53", "pri-mission", "Primary", ""),
                    _edge("r53", "dr-mission", "Failover", ""),
                    # BCAP
                    _edge("bcap-pri", "pri-tgw", "DX", "BGP"),
                    _edge("bcap-dr", "dr-tgw", "DX", "BGP"),
                ],
            }
        ),
    },
    # 47 ─ SCCA Cost-Optimized (Shared Services)
    {
        "id": "tpl-scca-cost-optimized",
        "name": "SCCA Cost-Optimized (Shared Services)",
        "category": "SCCA / Landing Zone",
        "description": "Cost-optimized SCCA architecture with shared VDSS/VDMS serving multiple mission VPCs. Consolidates security stack (single Network Firewall, shared AD, shared Security Hub) across 3 mission owners to reduce per-mission cost. Transit Gateway route tables isolate mission traffic while sharing inspection path. Suitable for IL4 workloads with moderate security requirements.",
        "tags": json.dumps(
            ["scca", "cost-optimized", "shared-services", "multi-mission", "il4", "consolidated", "transit-gateway"]
        ),
        "graph_json": json.dumps(
            {
                "nodes": [
                    # Shared VDSS/VDMS (top)
                    _node(
                        "tgw",
                        "Transit Gateway (Shared)",
                        "aws-tgw",
                        400,
                        60,
                        {"config": {"route_tables": ["shared-security", "mission-1", "mission-2", "mission-3"]}},
                    ),
                    _node(
                        "nfw",
                        "Network Firewall (Shared)",
                        "aws-nfw",
                        200,
                        60,
                        {"config": {"purpose": "Single NFW serves all missions — cost savings ~60%"}},
                    ),
                    _node("waf", "WAF (Shared Rules)", "aws-waf", 200, 200),
                    _node(
                        "shield",
                        "Shield Advanced (Org)",
                        "aws-shield",
                        400,
                        200,
                        {"config": {"purpose": "Org-wide Shield — $3K/mo covers all accounts"}},
                    ),
                    _node("ad", "Managed AD (Shared)", "aws-ad", 600, 60),
                    _node("sechub", "Security Hub (Aggregator)", "aws-securityhub", 600, 200),
                    _node("kms", "KMS (Shared CMK)", "aws-kms", 400, 340),
                    _node("ct", "CloudTrail (Org Trail)", "aws-ct", 600, 340),
                    # Mission VPC 1
                    _node(
                        "m1-vpc",
                        "Mission 1 VPC",
                        "aws-vpc",
                        100,
                        500,
                        {"config": {"cidr": "10.1.0.0/16", "flow_logs_enabled": True, "owner": "Team Alpha"}},
                    ),
                    _node("m1-app", "M1 App Subnet", "aws-subnet", 100, 640),
                    # Mission VPC 2
                    _node(
                        "m2-vpc",
                        "Mission 2 VPC",
                        "aws-vpc",
                        400,
                        500,
                        {"config": {"cidr": "10.2.0.0/16", "flow_logs_enabled": True, "owner": "Team Bravo"}},
                    ),
                    _node("m2-app", "M2 App Subnet", "aws-subnet", 400, 640),
                    # Mission VPC 3
                    _node(
                        "m3-vpc",
                        "Mission 3 VPC",
                        "aws-vpc",
                        700,
                        500,
                        {"config": {"cidr": "10.3.0.0/16", "flow_logs_enabled": True, "owner": "Team Charlie"}},
                    ),
                    _node("m3-app", "M3 App Subnet", "aws-subnet", 700, 640),
                    # Connectivity
                    _node(
                        "dx",
                        "Direct Connect (Shared)",
                        "aws-dx",
                        200,
                        340,
                        {"config": {"bandwidth": "10G", "bfd_enabled": True}},
                    ),
                    _node("vpn", "VPN Backup", "aws-vpn", 100, 200),
                    # Cost annotations
                    _node(
                        "cost-note",
                        "Cost Savings Notes",
                        "server",
                        700,
                        340,
                        {
                            "config": {
                                "note": "Shared NFW saves ~$0.40/GB vs per-VPC; shared Shield $3K vs $15K; shared AD $0.09/hr vs $0.45/hr"
                            }
                        },
                    ),
                ],
                "edges": [
                    # Hub
                    _edge("tgw", "nfw", "Inspection", ""),
                    _edge("nfw", "waf", "L7", ""),
                    _edge("tgw", "shield", "DDoS", ""),
                    # Shared services
                    _edge("tgw", "ad", "Directory", ""),
                    _edge("tgw", "sechub", "Findings", ""),
                    _edge("sechub", "ct", "Trail Logs", ""),
                    _edge("tgw", "kms", "Encryption", ""),
                    # Missions
                    _edge("tgw", "m1-vpc", "TGW Attach", ""),
                    _edge("tgw", "m2-vpc", "TGW Attach", ""),
                    _edge("tgw", "m3-vpc", "TGW Attach", ""),
                    _edge("m1-vpc", "m1-app", "", ""),
                    _edge("m2-vpc", "m2-app", "", ""),
                    _edge("m3-vpc", "m3-app", "", ""),
                    # Connectivity
                    _edge("dx", "tgw", "DX", "BGP"),
                    _edge("vpn", "tgw", "Backup", "IPSec"),
                    # Cost references
                    _edge("cost-note", "shield", "Ref", ""),
                    _edge("cost-note", "nfw", "Ref", ""),
                ],
            }
        ),
    },
    # 48 ─ DevSecOps Pipeline in SCCA Network
    {
        "id": "tpl-devsecops-scca",
        "name": "DevSecOps Pipeline in SCCA Network",
        "category": "SCCA / Landing Zone",
        "description": "Secure CI/CD pipeline deployed within an SCCA-compliant network. Container build pipeline (CodePipeline → CodeBuild → ECR → EKS) isolated in a DevTools VPC behind VDSS inspection. SAST/DAST scanning gates, SBOM generation, image signing (AWS Signer), and artifact scanning before promotion to mission VPC. All traffic routes through TGW and Network Firewall.",
        "tags": json.dumps(
            [
                "scca",
                "devsecops",
                "cicd",
                "pipeline",
                "sast",
                "sbom",
                "container",
                "eks",
                "security-gate",
                "supply-chain",
            ]
        ),
        "graph_json": json.dumps(
            {
                "nodes": [
                    # VDSS Inspection (top-left)
                    _node("tgw", "Transit Gateway", "aws-tgw", 100, 60),
                    _node("nfw", "Network Firewall", "aws-nfw", 100, 200),
                    # Security Services (top-right)
                    _node("sechub", "Security Hub", "aws-securityhub", 400, 60),
                    _node("inspector", "Inspector (CVE Scan)", "aws-inspector", 600, 60),
                    _node("guardduty", "GuardDuty", "aws-guardduty", 400, 200),
                    _node("kms", "KMS (Artifact Signing)", "aws-kms", 600, 200),
                    # DevTools VPC (center — pipeline flow)
                    _node(
                        "dev-vpc",
                        "DevTools VPC",
                        "aws-vpc",
                        300,
                        380,
                        {"config": {"cidr": "10.50.0.0/16", "flow_logs_enabled": True, "purpose": "CI/CD pipeline"}},
                    ),
                    _node(
                        "codecommit",
                        "CodeCommit (Source)",
                        "server",
                        100,
                        380,
                        {"config": {"purpose": "Git repository — air-gap safe"}},
                    ),
                    _node(
                        "codebuild",
                        "CodeBuild (Build + SAST)",
                        "server",
                        100,
                        520,
                        {"config": {"purpose": "Build container + run bandit/ruff/SAST"}},
                    ),
                    _node(
                        "ecr",
                        "ECR (Signed Images)",
                        "server",
                        300,
                        520,
                        {"config": {"purpose": "Container registry — image scanning enabled", "scan_on_push": True}},
                    ),
                    _node(
                        "signer",
                        "AWS Signer (Integrity)",
                        "server",
                        500,
                        520,
                        {"config": {"purpose": "Code signing for artifact integrity"}},
                    ),
                    _node(
                        "sbom",
                        "SBOM Generator",
                        "server",
                        300,
                        660,
                        {"config": {"purpose": "CycloneDX SBOM for supply chain compliance"}},
                    ),
                    # Gate (promotion checkpoint)
                    _node(
                        "gate",
                        "Security Gate (Pass/Fail)",
                        "firewall",
                        500,
                        380,
                        {
                            "config": {
                                "purpose": "Blocks deploy if: CAT1 STIG, critical CVE, unsigned image, missing SBOM"
                            }
                        },
                    ),
                    # Mission VPC (right — deployment target)
                    _node(
                        "mission-vpc",
                        "Mission VPC (Prod)",
                        "aws-vpc",
                        700,
                        380,
                        {"config": {"cidr": "10.10.0.0/16", "flow_logs_enabled": True}},
                    ),
                    _node(
                        "eks",
                        "EKS Cluster (Prod)",
                        "server",
                        700,
                        520,
                        {"config": {"purpose": "Production Kubernetes — hardened, read-only rootfs"}},
                    ),
                    _node("alb", "ALB (Internal)", "aws-alb", 700, 660),
                    # Logging
                    _node("ct", "CloudTrail (Pipeline Audit)", "aws-ct", 100, 660),
                    _node("flowlogs", "VPC Flow Logs", "aws-flowlogs", 500, 660),
                ],
                "edges": [
                    # Pipeline flow
                    _edge("codecommit", "codebuild", "Source", ""),
                    _edge("codebuild", "ecr", "Push Image", ""),
                    _edge("ecr", "signer", "Sign", ""),
                    _edge("ecr", "sbom", "Generate SBOM", ""),
                    _edge("signer", "gate", "Verify", ""),
                    _edge("gate", "mission-vpc", "Promote", ""),
                    # VPC
                    _edge("dev-vpc", "codecommit", "Attach", ""),
                    _edge("dev-vpc", "codebuild", "Attach", ""),
                    _edge("dev-vpc", "ecr", "Attach", ""),
                    _edge("dev-vpc", "signer", "Attach", ""),
                    _edge("dev-vpc", "sbom", "Attach", ""),
                    # Security
                    _edge("inspector", "ecr", "CVE Scan", ""),
                    _edge("inspector", "gate", "Findings", ""),
                    _edge("sechub", "gate", "Compliance", ""),
                    _edge("guardduty", "sechub", "Threats", ""),
                    # Mission
                    _edge("mission-vpc", "eks", "", ""),
                    _edge("eks", "alb", "Ingress", ""),
                    # Network
                    _edge("tgw", "nfw", "Inspection", ""),
                    _edge("tgw", "dev-vpc", "Attach", ""),
                    _edge("tgw", "mission-vpc", "Attach", ""),
                    # Audit
                    _edge("codebuild", "ct", "Build Log", ""),
                    _edge("dev-vpc", "flowlogs", "Logging", ""),
                    # Signing
                    _edge("kms", "signer", "Signing Key", ""),
                ],
            }
        ),
    },
    # 49 ─ PCI-DSS v4.0 Network Segmentation
    {
        "id": "tpl-pci-dss-network",
        "name": "PCI-DSS v4.0 Network Segmentation",
        "category": "Compliance",
        "description": "PCI-DSS v4.0 cardholder data environment network with explicit CDE boundary, segmentation controls, and monitoring.",
        "tags": json.dumps(["pci-dss", "compliance", "cde", "segmentation", "payment", "cardholder"]),
        "graph_json": json.dumps(
            {
                "nodes": [
                    _node("inet-rtr", "Internet Router", "router", 400, 40),
                    _node("ext-fw", "External Firewall", "firewall", 400, 160),
                    _node("dmz-web", "DMZ Web Server", "server", 200, 280),
                    _node("int-fw", "Internal Firewall", "firewall", 400, 280),
                    _node("cde-sw", "CDE Switch", "switch-l3", 400, 400),
                    _node("pos", "POS Terminal", "server", 200, 520),
                    _node("card-db", "Card Database", "database", 400, 520),
                    _node("qsa", "QSA Audit Server", "server", 600, 520),
                    _node("log-col", "Log Collector", "siem", 600, 280),
                    _node("ids", "IDS/IPS", "firewall", 600, 160),
                ],
                "edges": [
                    _edge("inet-rtr", "ext-fw", "Untrusted", ""),
                    _edge("ext-fw", "dmz-web", "DMZ Zone", "HTTPS"),
                    _edge("ext-fw", "int-fw", "Filtered", ""),
                    _edge("ext-fw", "ids", "Mirror", ""),
                    _edge("int-fw", "cde-sw", "CDE Boundary", ""),
                    _edge("cde-sw", "pos", "POS Traffic", "TLS"),
                    _edge("cde-sw", "card-db", "Card Data", "TLS"),
                    _edge("cde-sw", "qsa", "Audit Access", "TLS"),
                    _edge("ids", "log-col", "Alerts", ""),
                    _edge("int-fw", "log-col", "FW Logs", ""),
                    _edge("card-db", "log-col", "DB Audit", "TLS"),
                ],
            }
        ),
    },
    # 50 ─ HIPAA Network Isolation
    {
        "id": "tpl-hipaa-network",
        "name": "HIPAA Network Isolation",
        "category": "Compliance",
        "description": "HIPAA-compliant network with PHI data isolation, encrypted transit, access logging, and BAA partner connectivity.",
        "tags": json.dumps(["hipaa", "compliance", "phi", "healthcare", "encryption", "baa"]),
        "graph_json": json.dumps(
            {
                "nodes": [
                    _node("igw", "Internet Gateway", "router", 400, 40),
                    _node("fw", "Firewall", "firewall", 400, 160),
                    _node("phi-app", "PHI Application Server", "server", 200, 300),
                    _node("phi-db", "PHI Database", "database", 200, 440),
                    _node("vpn-baa", "VPN Gateway (BAA Partner)", "router", 600, 160),
                    _node("audit-log", "Audit Log Server", "siem", 600, 300),
                    _node("enc-gw", "Encryption Gateway", "firewall", 400, 300),
                    _node("nac", "NAC Controller", "server", 400, 440),
                ],
                "edges": [
                    _edge("igw", "fw", "Perimeter", ""),
                    _edge("fw", "enc-gw", "Encrypted Transit", "TLS"),
                    _edge("enc-gw", "phi-app", "PHI Access", "TLS"),
                    _edge("phi-app", "phi-db", "PHI Query", "TLS"),
                    _edge("vpn-baa", "fw", "BAA Tunnel", "IPSec"),
                    _edge("fw", "audit-log", "Access Logs", "TLS"),
                    _edge("phi-app", "audit-log", "PHI Access Log", "TLS"),
                    _edge("phi-db", "audit-log", "DB Audit Log", "TLS"),
                    _edge("nac", "fw", "Policy Enforcement", ""),
                    _edge("nac", "phi-app", "Endpoint Validation", ""),
                ],
            }
        ),
    },
    # 51 ─ Kubernetes CNI Network Architecture
    {
        "id": "tpl-k8s-cni",
        "name": "Kubernetes CNI Network Architecture",
        "category": "Container",
        "description": "Kubernetes cluster networking with CNI (Calico/Cilium), network policies, service mesh, and ingress controller.",
        "tags": json.dumps(["kubernetes", "k8s", "cni", "calico", "cilium", "istio", "service-mesh", "container"]),
        "graph_json": json.dumps(
            {
                "nodes": [
                    _node("ingress", "Ingress Controller", "router", 400, 40),
                    _node("api-server", "K8s API Server", "server", 200, 160),
                    _node("coredns", "CoreDNS", "server", 600, 160),
                    _node("cni", "Calico/Cilium CNI", "switch-l3", 400, 280),
                    _node("pod-net", "Pod Network", "cloud", 400, 400),
                    _node("mesh", "Service Mesh (Istio)", "server", 200, 400),
                    _node("ext-lb", "External LB", "router", 400, 520),
                    _node("etcd", "etcd", "database", 600, 400),
                ],
                "edges": [
                    _edge("ingress", "cni", "Ingress-to-Pod", "HTTPS"),
                    _edge("cni", "pod-net", "Pod-to-Pod", ""),
                    _edge("pod-net", "mesh", "Service-to-Service", "mTLS"),
                    _edge("api-server", "etcd", "Cluster State", "TLS"),
                    _edge("api-server", "cni", "Network Policy", ""),
                    _edge("coredns", "pod-net", "DNS Resolution", ""),
                    _edge("ingress", "ext-lb", "External Traffic", "HTTPS"),
                    _edge("mesh", "api-server", "Config Sync", "TLS"),
                    _edge("cni", "coredns", "Service Discovery", ""),
                ],
            }
        ),
    },
    # 52 ─ Post-Quantum Cryptography Transition
    {
        "id": "tpl-pqc-transition",
        "name": "Post-Quantum Cryptography Transition",
        "category": "Quantum",
        "description": "Hybrid classical + post-quantum cryptography transition architecture with PQC key exchange, algorithm agility, and migration phases.",
        "tags": json.dumps(["pqc", "post-quantum", "ml-kem", "ml-dsa", "hybrid-tls", "cryptography", "quantum"]),
        "graph_json": json.dumps(
            {
                "nodes": [
                    _node("pqc-gw", "PQC Gateway", "firewall", 400, 40),
                    _node("classical", "Classical Crypto (Legacy)", "server", 200, 180),
                    _node("hybrid-tls", "Hybrid TLS Terminator", "firewall", 400, 180),
                    _node("ml-kem", "ML-KEM Key Exchange", "server", 600, 180),
                    _node("ml-dsa", "ML-DSA Signer", "server", 600, 320),
                    _node("ca", "Certificate Authority", "server", 200, 320),
                    _node("kms", "Key Management Server", "database", 400, 320),
                ],
                "edges": [
                    _edge("pqc-gw", "hybrid-tls", "Inbound TLS", "Hybrid-TLS"),
                    _edge("hybrid-tls", "classical", "Classical Fallback", "RSA/ECDH"),
                    _edge("hybrid-tls", "ml-kem", "PQC Key Exchange", "ML-KEM-768"),
                    _edge("ml-kem", "kms", "Key Storage", "TLS"),
                    _edge("ml-dsa", "ca", "PQC Cert Signing", "ML-DSA-65"),
                    _edge("ca", "kms", "Key Material", "TLS"),
                    _edge("ca", "hybrid-tls", "Cert Issuance", ""),
                    _edge("classical", "kms", "Legacy Keys", "TLS"),
                ],
            }
        ),
    },
    # 53 ─ Distributed Cloud Architecture
    {
        "id": "tpl-distributed-cloud",
        "name": "Distributed Cloud Architecture",
        "category": "Hybrid",
        "description": "Multi-site distributed cloud with edge locations, central control plane, and consistent networking across Anthos/Arc/Outposts.",
        "tags": json.dumps(
            ["distributed-cloud", "hybrid", "edge", "anthos", "arc", "outposts", "sd-wan", "multi-site"]
        ),
        "graph_json": json.dumps(
            {
                "nodes": [
                    _node("ctrl", "Central Control Plane", "cloud", 400, 40),
                    _node("edge1", "Edge Location 1", "server", 150, 200),
                    _node("edge2", "Edge Location 2", "server", 650, 200),
                    _node("hub", "Cloud Hub", "cloud", 400, 200),
                    _node("tgw", "Transit Gateway", "router", 400, 340),
                    _node("sdwan", "SD-WAN Controller", "router", 150, 340),
                    _node("glb", "Global LB", "router", 650, 340),
                    _node("mon", "Monitoring", "siem", 400, 460),
                ],
                "edges": [
                    _edge("ctrl", "hub", "Control Plane", "TLS"),
                    _edge("ctrl", "edge1", "Policy Push", "TLS"),
                    _edge("ctrl", "edge2", "Policy Push", "TLS"),
                    _edge("hub", "tgw", "Cloud Routing", ""),
                    _edge("tgw", "edge1", "Site Link", "IPSec"),
                    _edge("tgw", "edge2", "Site Link", "IPSec"),
                    _edge("sdwan", "edge1", "WAN Overlay", ""),
                    _edge("sdwan", "edge2", "WAN Overlay", ""),
                    _edge("glb", "edge1", "Traffic Steering", ""),
                    _edge("glb", "edge2", "Traffic Steering", ""),
                    _edge("mon", "ctrl", "Health Check", "TLS"),
                    _edge("mon", "hub", "Metrics", "TLS"),
                ],
            }
        ),
    },
    # 54 ─ IoT Gateway Security Architecture
    {
        "id": "tpl-iot-gateway",
        "name": "IoT Gateway Security Architecture",
        "category": "IoT",
        "description": "IoT device gateway with protocol translation, edge compute, device identity management, and firmware OTA security.",
        "tags": json.dumps(["iot", "mqtt", "coap", "edge-compute", "ota", "device-identity", "gateway"]),
        "graph_json": json.dumps(
            {
                "nodes": [
                    _node("iot1", "IoT Device 1", "server", 100, 40),
                    _node("iot2", "IoT Device 2", "server", 300, 40),
                    _node("iot3", "IoT Device 3", "server", 500, 40),
                    _node("proto-gw", "Protocol Gateway (MQTT/CoAP)", "router", 300, 180),
                    _node("edge", "Edge Compute", "server", 300, 320),
                    _node("cloud", "Cloud Backend", "cloud", 300, 460),
                    _node("dev-id", "Device Identity Service", "server", 600, 180),
                    _node("ota", "Firmware OTA Server", "server", 600, 320),
                    _node("fw", "Firewall", "firewall", 100, 320),
                    _node("siem", "SIEM", "siem", 100, 460),
                ],
                "edges": [
                    _edge("iot1", "proto-gw", "Telemetry", "MQTT"),
                    _edge("iot2", "proto-gw", "Telemetry", "MQTT"),
                    _edge("iot3", "proto-gw", "Telemetry", "CoAP"),
                    _edge("proto-gw", "edge", "Translated", "TLS"),
                    _edge("edge", "cloud", "Aggregated Data", "TLS"),
                    _edge("dev-id", "proto-gw", "Auth Token", "TLS"),
                    _edge("dev-id", "iot1", "Identity", ""),
                    _edge("dev-id", "iot2", "Identity", ""),
                    _edge("dev-id", "iot3", "Identity", ""),
                    _edge("ota", "proto-gw", "FW Update", "TLS"),
                    _edge("fw", "edge", "Inspection", ""),
                    _edge("fw", "siem", "Security Logs", "TLS"),
                    _edge("edge", "siem", "Edge Logs", "TLS"),
                ],
            }
        ),
    },
    # ── VDI / Virtual Desktop Infrastructure Templates ──────────────────────
    # 13 ─ Azure Virtual Desktop (AVD) Hub-Spoke
    {
        "id": "tpl-avd-hub-spoke",
        "name": "Azure Virtual Desktop (AVD) Hub-Spoke",
        "category": "VDI / DaaS",
        "description": "CUI // SP-CTI — AVD host pool with connection broker, FSLogix on Azure Files, Azure Firewall hub, "
        "and ExpressRoute connectivity. Session hosts on dedicated subnet per NIST SC-7.",
        "tags": json.dumps(["vdi", "avd", "azure", "fslogix", "hub-spoke", "expressroute", "cui"]),
        "graph_json": json.dumps(
            {
                "nodes": [
                    _node("er", "ExpressRoute Circuit", "az-er", 100, 200),
                    _node("hub-vnet", "Hub VNet", "az-vnet", 300, 200),
                    _node("az-fw", "Azure Firewall", "az-fw", 300, 80),
                    _node("bastion", "Azure Bastion", "az-bastion", 500, 80),
                    _node("spoke-vnet", "AVD Spoke VNet", "az-vnet", 550, 200),
                    _node("avd-sub", "Session Host Subnet", "az-subnet", 550, 320),
                    _node("sh1", "Session Host Pool", "avd-hostpool", 700, 320),
                    _node("broker", "AVD Connection Broker", "vdi-connection-broker", 700, 200),
                    _node("ws", "AVD Workspace", "avd-workspace", 900, 200),
                    _node("fslogix", "FSLogix Profile (Azure Files)", "vdi-profile-server", 700, 440),
                    _node("gw", "AVD Gateway (RD Web)", "vdi-gateway", 900, 80),
                    _node("nsg", "NSG (Session Hosts)", "az-nsg", 400, 320),
                    _node("img", "Compute Gallery (Images)", "vdi-image-store", 550, 440),
                ],
                "edges": [
                    _edge("er", "hub-vnet", "Private Peering", "ExpressRoute"),
                    _edge("hub-vnet", "az-fw", "Default Route", ""),
                    _edge("hub-vnet", "spoke-vnet", "VNet Peering", ""),
                    _edge("spoke-vnet", "avd-sub", "Subnet", ""),
                    _edge("avd-sub", "sh1", "10G", ""),
                    _edge("nsg", "avd-sub", "ACL", ""),
                    _edge("sh1", "broker", "RDP Shortpath", "TLS"),
                    _edge("broker", "ws", "App Group", "TLS"),
                    _edge("sh1", "fslogix", "SMB 3.1.1", "TLS"),
                    _edge("gw", "broker", "HTTPS", "TLS"),
                    _edge("gw", "sh1", "RDP (reverse connect)", "TLS"),
                    _edge("bastion", "sh1", "Admin SSH/RDP", "TLS"),
                    _edge("img", "sh1", "Image Deploy", ""),
                    _edge("az-fw", "spoke-vnet", "Filtered Egress", ""),
                ],
            }
        ),
    },
    # 14 ─ Citrix DaaS Multi-Cloud
    {
        "id": "tpl-citrix-daas-multicloud",
        "name": "Citrix DaaS Multi-Cloud",
        "category": "VDI / DaaS",
        "description": "CUI // SP-CTI — Citrix Cloud delivery controller with Citrix Gateway, session hosts across AWS and Azure, "
        "NetScaler ADC load balancing, and StoreFront for app enumeration.",
        "tags": json.dumps(["vdi", "citrix", "daas", "multi-cloud", "netscaler", "aws", "azure", "cui"]),
        "graph_json": json.dumps(
            {
                "nodes": [
                    _node("ctx-cloud", "Citrix Cloud Controller", "citrix-cloud", 500, 60),
                    _node("ctx-gw", "Citrix Gateway", "vdi-gateway", 500, 180),
                    _node("netscaler", "NetScaler ADC", "load-balancer", 300, 180),
                    _node("az-vnet", "Azure VNet (VDA Pool)", "az-vnet", 200, 320),
                    _node("az-sh", "Azure Session Hosts", "vdi-session-host", 200, 440),
                    _node("aws-vpc", "AWS VPC (VDA Pool)", "aws-vpc", 800, 320),
                    _node("aws-sh", "AWS Session Hosts", "vdi-session-host", 800, 440),
                    _node("profile", "Citrix UPM Store", "vdi-profile-server", 500, 440),
                    _node("broker", "Citrix Broker (HA)", "vdi-connection-broker", 650, 60),
                    _node("storefront", "StoreFront", "server", 300, 60),
                    _node("fw-az", "Azure Firewall", "az-fw", 200, 200),
                    _node("fw-aws", "AWS Network FW", "aws-nfw", 800, 200),
                    _node("lic", "Citrix License Server", "vdi-license-server", 650, 180),
                ],
                "edges": [
                    _edge("ctx-cloud", "ctx-gw", "Cloud Connector", "TLS"),
                    _edge("ctx-cloud", "broker", "Brokering", "TLS"),
                    _edge("ctx-gw", "netscaler", "GSLB", "TLS"),
                    _edge("netscaler", "az-sh", "ICA Proxy", "ICA-TLS"),
                    _edge("netscaler", "aws-sh", "ICA Proxy", "ICA-TLS"),
                    _edge("az-vnet", "az-sh", "Subnet", ""),
                    _edge("aws-vpc", "aws-sh", "Subnet", ""),
                    _edge("fw-az", "az-vnet", "Filtered", ""),
                    _edge("fw-aws", "aws-vpc", "Filtered", ""),
                    _edge("az-sh", "profile", "UPM Sync", "TLS"),
                    _edge("aws-sh", "profile", "UPM Sync", "TLS"),
                    _edge("storefront", "ctx-cloud", "App Enum", "TLS"),
                    _edge("lic", "az-sh", "License", ""),
                    _edge("lic", "aws-sh", "License", ""),
                ],
            }
        ),
    },
    # 15 ─ Amazon WorkSpaces Managed DaaS
    {
        "id": "tpl-aws-workspaces",
        "name": "Amazon WorkSpaces Managed DaaS",
        "category": "VDI / DaaS",
        "description": "CUI // SP-CTI — Amazon WorkSpaces in a dedicated VPC with AWS Directory Service, "
        "Direct Connect on-prem link, CloudWatch monitoring, and S3 for user backup.",
        "tags": json.dumps(["vdi", "aws", "workspaces", "daas", "directory-service", "direct-connect", "cui"]),
        "graph_json": json.dumps(
            {
                "nodes": [
                    _node("dx", "Direct Connect", "aws-dx", 100, 200),
                    _node("vpc", "WorkSpaces VPC", "aws-vpc", 350, 200),
                    _node("sub-ws", "WorkSpaces Subnet (Private)", "aws-subnet", 350, 320),
                    _node("ws", "WorkSpaces Fleet", "aws-workspaces", 500, 320),
                    _node("ad", "AWS Directory Service (MAD)", "server", 500, 440),
                    _node("nfw", "AWS Network Firewall", "aws-nfw", 200, 200),
                    _node("r53", "Route 53 (Private Zone)", "aws-r53", 700, 200),
                    _node("cw", "CloudWatch", "server", 700, 320),
                    _node("s3", "S3 User Backup", "server", 700, 440),
                    _node("wac", "WorkSpaces Web Client", "vdi-web-client", 500, 80),
                    _node("onprem-ad", "On-Prem AD (Trust)", "server", 100, 320),
                    _node("waf", "AWS WAF", "aws-waf", 350, 80),
                ],
                "edges": [
                    _edge("dx", "vpc", "Private VIF", "IPSec"),
                    _edge("vpc", "sub-ws", "Subnet", ""),
                    _edge("sub-ws", "ws", "ENI", ""),
                    _edge("ws", "ad", "LDAPS Auth", "TLS"),
                    _edge("nfw", "vpc", "Inspection", ""),
                    _edge("ws", "cw", "Metrics/Logs", "TLS"),
                    _edge("ws", "s3", "User Backup", "TLS"),
                    _edge("wac", "ws", "WSP Streaming", "TLS"),
                    _edge("waf", "wac", "WAF Filter", ""),
                    _edge("r53", "vpc", "DNS", ""),
                    _edge("dx", "onprem-ad", "On-Prem Link", "IPSec"),
                    _edge("onprem-ad", "ad", "AD Trust", "TLS"),
                ],
            }
        ),
    },
    # 16 ─ VMware Horizon Cloud on Azure
    {
        "id": "tpl-horizon-cloud-azure",
        "name": "VMware Horizon Cloud on Azure",
        "category": "VDI / DaaS",
        "description": "CUI // SP-CTI — VMware Horizon Cloud pods on Azure VNet with Unified Access Gateway (UAG), "
        "vGPU hosts for graphics workloads, App Volumes, and DEM profile management.",
        "tags": json.dumps(["vdi", "vmware", "horizon", "azure", "uag", "vgpu", "cui"]),
        "graph_json": json.dumps(
            {
                "nodes": [
                    _node("hcs", "Horizon Cloud Service", "horizon-cloud", 500, 60),
                    _node("uag", "Unified Access Gateway", "vdi-gateway", 500, 180),
                    _node("az-vnet", "Azure VNet (Horizon)", "az-vnet", 300, 280),
                    _node("mgmt-sub", "Management Subnet", "az-subnet", 200, 380),
                    _node("desktop-sub", "Desktop Subnet", "az-subnet", 500, 380),
                    _node("sh-pool", "Session Host Pool", "vdi-session-host", 500, 500),
                    _node("gpu-host", "vGPU Hosts (NV-series)", "vdi-gpu-host", 700, 500),
                    _node("cs", "Horizon Connection Server", "vdi-connection-broker", 200, 500),
                    _node("dem", "DEM Profile Mgmt", "vdi-profile-server", 350, 620),
                    _node("appvol", "App Volumes", "vdi-image-store", 650, 620),
                    _node("az-fw", "Azure Firewall", "az-fw", 100, 280),
                    _node("nsg", "NSG (Desktop Subnet)", "az-nsg", 700, 380),
                ],
                "edges": [
                    _edge("hcs", "uag", "Horizon Protocol", "TLS"),
                    _edge("uag", "az-vnet", "VNet Link", ""),
                    _edge("az-vnet", "mgmt-sub", "Subnet", ""),
                    _edge("az-vnet", "desktop-sub", "Subnet", ""),
                    _edge("desktop-sub", "sh-pool", "ENI", ""),
                    _edge("desktop-sub", "gpu-host", "ENI", ""),
                    _edge("nsg", "desktop-sub", "ACL", ""),
                    _edge("mgmt-sub", "cs", "Mgmt Traffic", "TLS"),
                    _edge("cs", "sh-pool", "Brokering", "TLS"),
                    _edge("cs", "gpu-host", "Brokering", "TLS"),
                    _edge("sh-pool", "dem", "Profile Sync", "TLS"),
                    _edge("gpu-host", "dem", "Profile Sync", "TLS"),
                    _edge("appvol", "sh-pool", "App Attach", ""),
                    _edge("appvol", "gpu-host", "App Attach", ""),
                    _edge("az-fw", "az-vnet", "Filtered Egress", ""),
                ],
            }
        ),
    },
    # 17 ─ Thin Client Campus (IGEL/Wyse)
    {
        "id": "tpl-thin-client-campus",
        "name": "Thin Client Campus (IGEL/Wyse)",
        "category": "VDI / DaaS",
        "description": "CUI // SP-CTI — Campus deployment with thin clients on 802.1X VLAN, RD Gateway for external access, "
        "RDSH session hosts, FSLogix profile server, and golden image store. Compliant with NIST SC-7, IA-3.",
        "tags": json.dumps(["vdi", "thin-client", "campus", "igel", "wyse", "802.1x", "rdsh", "cui"]),
        "graph_json": json.dumps(
            {
                "nodes": [
                    _node("tc1", "IGEL Thin Client (Bldg A)", "thin-client", 100, 60),
                    _node("tc2", "Wyse Thin Client (Bldg B)", "thin-client", 300, 60),
                    _node("tc3", "Thin Client (Bldg C)", "thin-client", 500, 60),
                    _node("acc-sw", "Access Switch (802.1X)", "switch-l2", 300, 180),
                    _node("dist-sw", "Distribution Switch", "switch-l3", 300, 300),
                    _node("fw", "Perimeter Firewall", "firewall", 100, 300),
                    _node("rdgw", "RD Gateway", "vdi-gateway", 100, 180),
                    _node("broker", "RDS Connection Broker", "vdi-connection-broker", 500, 300),
                    _node("sh1", "RDSH Session Host 1", "vdi-session-host", 500, 420),
                    _node("sh2", "RDSH Session Host 2", "vdi-session-host", 700, 420),
                    _node("profile", "FSLogix Profile Server", "vdi-profile-server", 700, 300),
                    _node("img", "Golden Image Store", "vdi-image-store", 700, 180),
                    _node("lic", "RDS License Server", "vdi-license-server", 500, 180),
                    _node("internet", "Internet", "cloud", 100, 420),
                ],
                "edges": [
                    _edge("tc1", "acc-sw", "802.1X VLAN", ""),
                    _edge("tc2", "acc-sw", "802.1X VLAN", ""),
                    _edge("tc3", "acc-sw", "802.1X VLAN", ""),
                    _edge("acc-sw", "dist-sw", "Trunk", ""),
                    _edge("dist-sw", "broker", "RDP", "TLS"),
                    _edge("broker", "sh1", "Session Assign", "TLS"),
                    _edge("broker", "sh2", "Session Assign", "TLS"),
                    _edge("sh1", "profile", "SMB 3.1.1", "TLS"),
                    _edge("sh2", "profile", "SMB 3.1.1", "TLS"),
                    _edge("img", "sh1", "Image Deploy", ""),
                    _edge("img", "sh2", "Image Deploy", ""),
                    _edge("lic", "broker", "CAL", ""),
                    _edge("fw", "internet", "WAN", "IPSec"),
                    _edge("rdgw", "fw", "HTTPS 443", "TLS"),
                    _edge("rdgw", "broker", "RDP Gateway", "TLS"),
                    _edge("dist-sw", "fw", "Default Route", ""),
                ],
            }
        ),
    },
    # 18 ─ SCCA Network Architecture (AWS GovCloud) — DoD Multi-Account Reference
    {
        "id": "tpl-scca-aws-govcloud",
        "name": "SCCA Network Architecture (AWS GovCloud)",
        "category": "SCCA / Landing Zone",
        "description": "DoD SCCA multi-account network topology — Inspection VPC with "
        "Network Firewall and IDS/IPS in dual AZs, Transit Gateway hub "
        "connecting workload VPCs (Dev/Test/Prod), Shared Services VPC "
        "(Active Directory, container registry), External Access VPC "
        "(public/private subnets, IGW), Direct Connect and VPN "
        "connectivity to on-premises. IL4/IL5 compliant.",
        "tags": json.dumps(
            ["scca", "dod", "bcap", "govcloud", "transit-gateway", "il4", "il5", "multi-account", "inspection", "nfw"]
        ),
        "graph_json": json.dumps(
            {
                "nodes": [
                    # ── Network Account — Inspection VPC ──
                    _node("insp-vpc", "Inspection VPC", "aws-vpc", 100, 50),
                    _node("fw-sub-a", "Firewall Subnet AZ-A", "aws-subnet", 100, 150),
                    _node("fw-sub-b", "Firewall Subnet AZ-B", "aws-subnet", 300, 150),
                    _node("nfw", "Network Firewall", "aws-nfw", 200, 250),
                    _node("ids-a", "IDS/IPS (AZ-A)", "firewall", 100, 350),
                    _node("ids-b", "IDS/IPS (AZ-B)", "firewall", 300, 350),
                    _node("insp-priv-a", "Private Subnet AZ-A", "aws-subnet", 100, 450),
                    _node("insp-priv-b", "Private Subnet AZ-B", "aws-subnet", 300, 450),
                    _node("insp-tgw-a", "TGW Subnet AZ-A", "aws-subnet", 100, 540),
                    _node("insp-tgw-b", "TGW Subnet AZ-B", "aws-subnet", 300, 540),
                    # ── Transit Gateway Hub ──
                    _node("tgw", "AWS Transit Gateway", "aws-tgw", 500, 350),
                    # ── External Connectivity ──
                    _node("dx", "Direct Connect Gateway", "aws-dx", 500, 100),
                    _node("vpn-gw", "Virtual Private Gateway", "aws-vpn", 700, 100),
                    _node("onprem-rtr", "On-Premises Router", "router", 700, 220),
                    # ── Production Workload VPC ──
                    _node("prod-vpc", "Production Application VPC", "aws-vpc", 700, 350),
                    _node("app-sub-a", "App Subnet AZ-A", "aws-subnet", 700, 450),
                    _node("app-sub-b", "App Subnet AZ-B", "aws-subnet", 900, 450),
                    _node("data-sub-a", "Data Subnet AZ-A", "aws-subnet", 700, 540),
                    _node("data-sub-b", "Data Subnet AZ-B", "aws-subnet", 900, 540),
                    _node("wl-tgw-a", "TGW Subnet AZ-A", "aws-subnet", 700, 630),
                    _node("wl-tgw-b", "TGW Subnet AZ-B", "aws-subnet", 900, 630),
                    # ── Shared Services Account ──
                    _node("ext-vpc", "External Access VPC", "aws-vpc", 100, 700),
                    _node("ext-pub-a", "Public Subnet AZ-A", "aws-subnet", 100, 790),
                    _node("ext-priv-a", "Private Subnet AZ-A", "aws-subnet", 300, 790),
                    _node("ext-igw", "Internet Gateway", "aws-gw-ep", 200, 880),
                    _node("ext-tgw-a", "TGW Subnet AZ-A", "aws-subnet", 100, 880),
                    _node("shared-vpc", "Shared Services VPC", "aws-vpc", 500, 700),
                    _node("shared-priv-a", "Private Subnet AZ-A", "aws-subnet", 500, 790),
                    _node("shared-priv-b", "Private Subnet AZ-B", "aws-subnet", 700, 790),
                    _node("shared-ad", "Active Directory", "server", 500, 880),
                    _node("shared-ecr", "Container Registry", "server", 700, 880),
                    _node("shared-tgw-a", "TGW Subnet AZ-A", "aws-subnet", 500, 960),
                    _node("shared-tgw-b", "TGW Subnet AZ-B", "aws-subnet", 700, 960),
                    # ── Route 53 ──
                    _node("r53", "Route 53 (Private)", "aws-r53", 900, 350),
                ],
                "edges": [
                    # Inspection VPC internal flow
                    _edge("fw-sub-a", "insp-vpc", "Firewall AZ-A", ""),
                    _edge("fw-sub-b", "insp-vpc", "Firewall AZ-B", ""),
                    _edge("nfw", "fw-sub-a", "Stateful Inspection", ""),
                    _edge("nfw", "fw-sub-b", "Stateful Inspection", ""),
                    _edge("ids-a", "nfw", "IDS Alert Feed", ""),
                    _edge("ids-b", "nfw", "IDS Alert Feed", ""),
                    _edge("insp-priv-a", "ids-a", "Filtered Traffic", ""),
                    _edge("insp-priv-b", "ids-b", "Filtered Traffic", ""),
                    _edge("insp-tgw-a", "insp-priv-a", "TGW Attach AZ-A", ""),
                    _edge("insp-tgw-b", "insp-priv-b", "TGW Attach AZ-B", ""),
                    # Inspection TGW subnets → Transit Gateway
                    _edge("insp-tgw-a", "tgw", "Inspection Route", ""),
                    _edge("insp-tgw-b", "tgw", "Inspection Route", ""),
                    # External connectivity
                    _edge("dx", "tgw", "DISN Circuit", "BGP"),
                    _edge("vpn-gw", "tgw", "VPN Tunnel", "IKEv2"),
                    _edge("onprem-rtr", "dx", "Dedicated 10G", ""),
                    _edge("onprem-rtr", "vpn-gw", "Backup VPN", "IKEv2"),
                    # TGW → Workload VPC
                    _edge("tgw", "wl-tgw-a", "Workload Route", ""),
                    _edge("tgw", "wl-tgw-b", "Workload Route", ""),
                    _edge("wl-tgw-a", "prod-vpc", "TGW Attach", ""),
                    _edge("wl-tgw-b", "prod-vpc", "TGW Attach", ""),
                    # Workload VPC subnets
                    _edge("app-sub-a", "prod-vpc", "App Tier AZ-A", ""),
                    _edge("app-sub-b", "prod-vpc", "App Tier AZ-B", ""),
                    _edge("data-sub-a", "prod-vpc", "Data Tier AZ-A", ""),
                    _edge("data-sub-b", "prod-vpc", "Data Tier AZ-B", ""),
                    # TGW → Shared Services
                    _edge("tgw", "shared-tgw-a", "Shared Svc Route", ""),
                    _edge("tgw", "shared-tgw-b", "Shared Svc Route", ""),
                    _edge("shared-tgw-a", "shared-vpc", "TGW Attach", ""),
                    _edge("shared-tgw-b", "shared-vpc", "TGW Attach", ""),
                    _edge("shared-priv-a", "shared-vpc", "Private AZ-A", ""),
                    _edge("shared-priv-b", "shared-vpc", "Private AZ-B", ""),
                    _edge("shared-ad", "shared-priv-a", "Domain Services", "LDAPS"),
                    _edge("shared-ecr", "shared-priv-b", "Registry Pull", "TLS"),
                    # TGW → External Access
                    _edge("tgw", "ext-tgw-a", "External Route", ""),
                    _edge("ext-tgw-a", "ext-vpc", "TGW Attach", ""),
                    _edge("ext-pub-a", "ext-vpc", "Public AZ-A", ""),
                    _edge("ext-priv-a", "ext-vpc", "Private AZ-A", ""),
                    _edge("ext-igw", "ext-pub-a", "Internet Egress", ""),
                    # DNS
                    _edge("r53", "shared-vpc", "DNS Resolution", ""),
                    _edge("r53", "prod-vpc", "DNS Resolution", ""),
                ],
            }
        ),
    },
    # 19 ─ Azure SCCA Hub-Spoke
    {
        "id": "tpl-scca-azure-hub-spoke",
        "name": "Azure SCCA Hub-Spoke",
        "category": "SCCA / Landing Zone",
        "description": "Azure Government network for SCCA compliance with Azure Firewall hub VNet, "
        "ExpressRoute and VPN connectivity, Bastion jump host, shared services VNet, "
        "and workload spoke VNets peered through the hub.",
        "tags": json.dumps(["scca", "azure-gov", "hub-spoke", "firewall", "expressroute"]),
        "graph_json": json.dumps(
            {
                "nodes": [
                    # Hub VNet
                    _node("hub-vnet", "Hub VNet", "az-vnet", 400, 100),
                    _node("az-fw", "Azure Firewall", "az-fw", 400, 220),
                    _node("bastion", "Bastion", "az-bastion", 600, 100),
                    # Connectivity
                    _node("er", "ExpressRoute", "az-er", 100, 100),
                    _node("vpn-gw", "VPN Gateway", "az-vpn-gw", 100, 220),
                    # Shared
                    _node("shared-vnet", "Shared Services VNet", "az-vnet", 400, 380),
                    _node("priv-dns", "Private DNS", "az-dns", 600, 380),
                    # Spokes
                    _node("spoke1", "Workload Spoke 1", "az-vnet", 200, 380),
                    _node("spoke2", "Workload Spoke 2", "az-vnet", 200, 480),
                    # Security
                    _node("hub-nsg", "Hub NSG", "az-nsg", 600, 220),
                    # Additional spoke
                    _node("spoke3", "Mgmt Spoke", "az-vnet", 600, 480),
                    _node("log-analytics", "Log Analytics", "server", 400, 480),
                ],
                "edges": [
                    _edge("er", "hub-vnet", "ExpressRoute Peering", "BGP"),
                    _edge("vpn-gw", "hub-vnet", "VPN Tunnel", "IKEv2"),
                    _edge("az-fw", "spoke1", "Spoke Peering", ""),
                    _edge("az-fw", "spoke2", "Spoke Peering", ""),
                    _edge("az-fw", "shared-vnet", "Shared Peering", ""),
                    _edge("az-fw", "spoke3", "Mgmt Peering", ""),
                    _edge("bastion", "hub-vnet", "Secure RDP/SSH", "TLS"),
                    _edge("hub-nsg", "hub-vnet", "NSG Rules", ""),
                    _edge("priv-dns", "shared-vnet", "DNS", ""),
                    _edge("log-analytics", "az-fw", "Diagnostics", ""),
                ],
            }
        ),
    },
    # 20 ─ GCP Shared VPC Landing Zone Network
    {
        "id": "tpl-gcp-shared-vpc-lz",
        "name": "GCP Shared VPC Landing Zone Network",
        "category": "SCCA / Landing Zone",
        "description": "GCP network with Shared VPC host project, Cloud Router, Cloud NAT, "
        "Cloud Interconnect and HA VPN for hybrid connectivity, Cloud Armor for "
        "DDoS/WAF, and service project subnets for prod/dev workloads.",
        "tags": json.dumps(["gcp", "shared-vpc", "landing-zone", "interconnect"]),
        "graph_json": json.dumps(
            {
                "nodes": [
                    # Host Project
                    _node("shared-vpc", "Shared VPC (Host)", "gcp-vpc", 400, 100),
                    _node("cloud-rtr", "Cloud Router", "gcp-router", 400, 220),
                    _node("cloud-nat", "Cloud NAT", "gcp-nat", 600, 220),
                    # Connectivity
                    _node("ic", "Cloud Interconnect", "gcp-ic", 100, 100),
                    _node("ha-vpn", "HA VPN", "gcp-vpn", 100, 220),
                    # Security
                    _node("armor", "Cloud Armor", "gcp-armor", 600, 100),
                    _node("priv-dns", "Cloud DNS (Private)", "gcp-dns", 400, 340),
                    # Service Projects
                    _node("prod-subnet", "Prod Subnet", "gcp-subnet", 200, 340),
                    _node("dev-subnet", "Dev Subnet", "gcp-subnet", 600, 340),
                    # Additional
                    _node("fw-rules", "VPC Firewall Rules", "firewall", 200, 220),
                    _node("lb", "Internal LB", "load-balancer", 200, 100),
                ],
                "edges": [
                    _edge("ic", "cloud-rtr", "Interconnect Attach", "BGP"),
                    _edge("ha-vpn", "cloud-rtr", "VPN Tunnel", "IKEv2"),
                    _edge("cloud-rtr", "shared-vpc", "BGP Routes", ""),
                    _edge("armor", "shared-vpc", "DDoS/WAF", ""),
                    _edge("prod-subnet", "shared-vpc", "Shared VPC Link", ""),
                    _edge("dev-subnet", "shared-vpc", "Shared VPC Link", ""),
                    _edge("cloud-nat", "shared-vpc", "NAT Gateway", ""),
                    _edge("priv-dns", "shared-vpc", "Private DNS Zone", ""),
                    _edge("fw-rules", "shared-vpc", "Firewall Policy", ""),
                    _edge("lb", "prod-subnet", "Backend Service", ""),
                ],
            }
        ),
    },
    # ── AWS Direct Connect — SOP / Runbook ────────────────────────────────────
    {
        "id": "tpl-aws-directconnect",
        "name": "AWS Direct Connect — SOP & Runbook",
        "category": "WAN / Hybrid Cloud",
        "description": (
            "End-to-end SOP and runbook for establishing AWS Direct Connect (DX) "
            "1/10/100 Gbps dedicated circuits. Covers customer-side and AWS "
            "provider-side provisioning, BGP peering (ASN 7224), BFD fast failover, "
            "and jumbo frame MTU 9001. Includes Cisco IOS-XE and Juniper JunOS "
            "router configurations. See template docs for full SOP, Mermaid diagrams, "
            "and failure runbook."
        ),
        "tags": json.dumps(
            [
                "aws",
                "direct-connect",
                "dx",
                "hybrid-cloud",
                "bgp",
                "sop",
                "runbook",
                "cisco",
                "juniper",
                "bfd",
            ]
        ),
        "graph_json": json.dumps(
            {
                "nodes": [
                    _node(
                        "cust-rtr",
                        "Customer Edge Router",
                        "router",
                        100,
                        200,
                        {
                            "config": {
                                "role": "CE router — terminates DX 802.1Q sub-interface",
                                "protocols": "BGP (eBGP to AWS ASN 7224), BFD",
                                "mtu": "9216 physical / 9001 IP (jumbo frames)",
                                "redundancy": "Dual routers recommended (LAG or active/standby)",
                            }
                        },
                    ),
                    _node(
                        "cust-patch",
                        "Customer Patch Panel",
                        "odf",
                        280,
                        200,
                        {
                            "config": {
                                "role": "Customer cage patch panel in colocation facility",
                                "fiber_type": "Single-mode (1000BASE-LX / 10GBASE-LR)",
                                "notes": "Label both A and B strands per LOA-CFA",
                            }
                        },
                    ),
                    _node(
                        "mmr",
                        "Meet-Me Room",
                        "odf",
                        460,
                        200,
                        {
                            "config": {
                                "role": "Colocation meet-me room — cross-connect termination",
                                "owner": "Colocation provider (Equinix, CoreSite, QTS, etc.)",
                                "notes": "Cross-connect built by colo per LOA-CFA",
                            }
                        },
                    ),
                    _node(
                        "aws-dx-port",
                        "AWS DX Port",
                        "odf",
                        640,
                        200,
                        {
                            "config": {
                                "role": "AWS Direct Connect port on AWS cage router",
                                "speed": "1 Gbps / 10 Gbps / 100 Gbps",
                                "notes": "Port ID shown in LOA-CFA document",
                            }
                        },
                    ),
                    _node(
                        "aws-dx-rtr",
                        "AWS DX Router",
                        "router",
                        820,
                        200,
                        {
                            "config": {
                                "role": "AWS-managed DX router — BGP peer (ASN 7224)",
                                "owner": "AWS (managed — no customer access)",
                                "bgp_asn": "7224 (Amazon default) or custom private ASN",
                            }
                        },
                    ),
                    _node(
                        "vgw",
                        "Virtual Private Gateway",
                        "router",
                        820,
                        380,
                        {
                            "config": {
                                "role": "VGW attached to VPC — terminates Private VIF",
                                "alternatives": "Transit Gateway (TGW) for multi-VPC via Transit VIF",
                                "notes": "VGW propagates routes to VPC route tables automatically",
                            }
                        },
                    ),
                    _node(
                        "vpc",
                        "AWS VPC",
                        "aws-vpc",
                        820,
                        520,
                        {
                            "config": {
                                "role": "Target VPC with private subnets",
                                "cidr": "172.16.0.0/16 (example)",
                                "notes": "Enable route propagation on subnet route tables",
                            }
                        },
                    ),
                    _node(
                        "on-prem",
                        "On-Premises Network",
                        "switch-l3",
                        100,
                        380,
                        {
                            "config": {
                                "role": "Corporate LAN / data center network",
                                "cidr": "10.0.0.0/16 (example — advertised to AWS via BGP)",
                                "notes": "Summarize prefixes to avoid route table limits (100 max on VGW)",
                            }
                        },
                    ),
                ],
                "edges": [
                    _edge("on-prem", "cust-rtr", "Uplink", "OSPF/IS-IS"),
                    _edge("cust-rtr", "cust-patch", "Fiber (SM)", "802.1Q"),
                    _edge("cust-patch", "mmr", "Cross-Connect", ""),
                    _edge("mmr", "aws-dx-port", "Cross-Connect", ""),
                    _edge("aws-dx-port", "aws-dx-rtr", "Internal", ""),
                    _edge("aws-dx-rtr", "vgw", "Private VIF", "eBGP"),
                    _edge("vgw", "vpc", "Route Propagation", ""),
                ],
            }
        ),
    },
    # ── Azure ExpressRoute — SOP / Runbook ────────────────────────────────────
    {
        "id": "tpl-azure-expressroute",
        "name": "Azure ExpressRoute — SOP & Runbook",
        "category": "WAN / Hybrid Cloud",
        "description": (
            "End-to-end SOP and runbook for establishing Azure ExpressRoute (ER) "
            "circuits with primary/secondary paths for 99.95% SLA. Covers customer-side "
            "and provider-side provisioning, BGP peering (Azure ASN 12076), BFD, "
            "Private and Microsoft Peering. Includes Cisco IOS-XE and Juniper JunOS "
            "router configurations. See template docs for full SOP, Mermaid diagrams, "
            "and failure runbook."
        ),
        "tags": json.dumps(
            [
                "azure",
                "expressroute",
                "hybrid-cloud",
                "bgp",
                "msee",
                "sop",
                "runbook",
                "cisco",
                "juniper",
                "bfd",
            ]
        ),
        "graph_json": json.dumps(
            {
                "nodes": [
                    _node(
                        "cust-rtr-pri",
                        "Customer CE (Primary)",
                        "router",
                        100,
                        160,
                        {
                            "config": {
                                "role": "Primary CE router — BGP to MSEE primary",
                                "protocols": "BGP (eBGP to Azure ASN 12076), BFD",
                                "notes": "MUST be separate device from secondary for Azure SLA",
                            }
                        },
                    ),
                    _node(
                        "cust-rtr-sec",
                        "Customer CE (Secondary)",
                        "router",
                        100,
                        340,
                        {
                            "config": {
                                "role": "Secondary CE router — BGP to MSEE secondary",
                                "protocols": "BGP (eBGP to Azure ASN 12076), BFD",
                                "notes": "Azure requires primary + secondary for 99.95% SLA",
                            }
                        },
                    ),
                    _node(
                        "provider-pe",
                        "Provider PE / Exchange",
                        "mpls-pe",
                        340,
                        250,
                        {
                            "config": {
                                "role": "Connectivity provider PE router or cloud exchange fabric",
                                "models": "Equinix Fabric, Megaport, AT&T NetBond, Verizon, etc.",
                                "notes": "L2 or L3 connectivity depending on provider model",
                            }
                        },
                    ),
                    _node(
                        "msee-pri",
                        "MSEE Primary",
                        "router",
                        580,
                        160,
                        {
                            "config": {
                                "role": "Microsoft Enterprise Edge router — primary",
                                "owner": "Microsoft (managed — no customer access)",
                                "bgp_asn": "12076 (Microsoft)",
                                "peering_types": "Private Peering, Microsoft Peering",
                            }
                        },
                    ),
                    _node(
                        "msee-sec",
                        "MSEE Secondary",
                        "router",
                        580,
                        340,
                        {
                            "config": {
                                "role": "Microsoft Enterprise Edge router — secondary",
                                "owner": "Microsoft (managed)",
                                "bgp_asn": "12076 (Microsoft)",
                                "notes": "Active-active with primary for high availability",
                            }
                        },
                    ),
                    _node(
                        "er-gw",
                        "ExpressRoute Gateway",
                        "router",
                        780,
                        250,
                        {
                            "config": {
                                "role": "VNet Gateway (SKU: ErGw1Az / ErGw2Az / ErGw3Az)",
                                "notes": "Zone-redundant SKUs (*Az) recommended for production",
                                "fastpath": "Enable FastPath on ErGw3Az for bypass (latency reduction)",
                            }
                        },
                    ),
                    _node(
                        "vnet",
                        "Azure VNet",
                        "az-vnet",
                        940,
                        250,
                        {
                            "config": {
                                "role": "Target Virtual Network with GatewaySubnet",
                                "requirements": "GatewaySubnet (/27 minimum), no NSG on GatewaySubnet",
                                "cidr": "172.16.0.0/16 (example)",
                            }
                        },
                    ),
                    _node(
                        "on-prem",
                        "On-Premises Network",
                        "switch-l3",
                        100,
                        500,
                        {
                            "config": {
                                "role": "Corporate LAN / data center",
                                "cidr": "10.0.0.0/16 (advertised to Azure via BGP)",
                                "notes": "Use route filters to limit prefixes advertised to Azure",
                            }
                        },
                    ),
                ],
                "edges": [
                    _edge("on-prem", "cust-rtr-pri", "Uplink", "OSPF/IS-IS"),
                    _edge("on-prem", "cust-rtr-sec", "Uplink", "OSPF/IS-IS"),
                    _edge("cust-rtr-pri", "provider-pe", "Primary Circuit", "802.1Q"),
                    _edge("cust-rtr-sec", "provider-pe", "Secondary Circuit", "802.1Q"),
                    _edge("provider-pe", "msee-pri", "Primary Path", "L2"),
                    _edge("provider-pe", "msee-sec", "Secondary Path", "L2"),
                    _edge("msee-pri", "er-gw", "Private Peering", "eBGP"),
                    _edge("msee-sec", "er-gw", "Private Peering", "eBGP"),
                    _edge("er-gw", "vnet", "Gateway Connection", ""),
                ],
            }
        ),
    },
    # ── GCP Cloud Interconnect — SOP / Runbook ────────────────────────────────
    {
        "id": "tpl-gcp-interconnect",
        "name": "GCP Cloud Interconnect — SOP & Runbook",
        "category": "WAN / Hybrid Cloud",
        "description": (
            "End-to-end SOP and runbook for establishing GCP Dedicated and Partner "
            "Cloud Interconnect (10/100 Gbps). Covers customer-side and Google "
            "provider-side provisioning, Cloud Router BGP (Google ASN 16550), "
            "link-local 169.254.x.x peering, VLAN attachments. Includes Cisco IOS-XE "
            "and Juniper JunOS router configurations. See template docs for full SOP, "
            "Mermaid diagrams, and failure runbook."
        ),
        "tags": json.dumps(
            [
                "gcp",
                "cloud-interconnect",
                "hybrid-cloud",
                "bgp",
                "sop",
                "runbook",
                "cisco",
                "juniper",
                "dedicated",
                "partner",
            ]
        ),
        "graph_json": json.dumps(
            {
                "nodes": [
                    _node(
                        "cust-rtr",
                        "Customer Edge Router",
                        "router",
                        100,
                        200,
                        {
                            "config": {
                                "role": "CE router — BGP to Cloud Router (Google ASN 16550)",
                                "protocols": "BGP, link-local 169.254.x.x peer addressing",
                                "mtu": "1440 (GCP Interconnect default)",
                            }
                        },
                    ),
                    _node(
                        "cust-patch",
                        "Customer Patch Panel",
                        "odf",
                        260,
                        200,
                        {
                            "config": {
                                "role": "Customer cage patch panel in colocation",
                                "fiber_type": "Single-mode (10GBASE-LR)",
                            }
                        },
                    ),
                    _node(
                        "mmr",
                        "Meet-Me Room",
                        "odf",
                        420,
                        200,
                        {
                            "config": {
                                "role": "Colocation meet-me room cross-connect",
                                "notes": "Cross-connect per Google LOA",
                            }
                        },
                    ),
                    _node(
                        "gcp-port",
                        "Google Interconnect Port",
                        "odf",
                        580,
                        200,
                        {
                            "config": {
                                "role": "Google peering edge port at colocation",
                                "speed": "10 Gbps (Dedicated) or 50M-50G (Partner)",
                            }
                        },
                    ),
                    _node(
                        "gcp-edge",
                        "Google Peering Edge",
                        "router",
                        740,
                        200,
                        {
                            "config": {
                                "role": "Google-managed peering edge router",
                                "owner": "Google (managed)",
                                "bgp_asn": "16550",
                            }
                        },
                    ),
                    _node(
                        "cloud-rtr",
                        "Cloud Router",
                        "router",
                        740,
                        380,
                        {
                            "config": {
                                "role": "GCP Cloud Router — regional, software-defined",
                                "notes": "Automatically learns and advertises VPC subnet routes",
                                "features": "Custom route advertisements, ASN configuration",
                            }
                        },
                    ),
                    _node(
                        "vpc",
                        "GCP VPC Network",
                        "gcp-vpc",
                        740,
                        520,
                        {
                            "config": {
                                "role": "Target VPC with subnets in interconnect region",
                                "cidr": "172.16.0.0/16 (example)",
                                "notes": "Firewall rules must allow ingress from on-prem CIDRs",
                            }
                        },
                    ),
                    _node(
                        "on-prem",
                        "On-Premises Network",
                        "switch-l3",
                        100,
                        380,
                        {
                            "config": {
                                "role": "Corporate LAN / data center",
                                "cidr": "10.0.0.0/16 (advertised to GCP via BGP)",
                            }
                        },
                    ),
                ],
                "edges": [
                    _edge("on-prem", "cust-rtr", "Uplink", "OSPF/IS-IS"),
                    _edge("cust-rtr", "cust-patch", "Fiber (SM)", "802.1Q"),
                    _edge("cust-patch", "mmr", "Cross-Connect", ""),
                    _edge("mmr", "gcp-port", "Cross-Connect", ""),
                    _edge("gcp-port", "gcp-edge", "Internal", ""),
                    _edge("gcp-edge", "cloud-rtr", "VLAN Attachment", "eBGP"),
                    _edge("cloud-rtr", "vpc", "Route Propagation", ""),
                ],
            }
        ),
    },
    # ── OCI FastConnect — SOP / Runbook ───────────────────────────────────────
    {
        "id": "tpl-oci-fastconnect",
        "name": "OCI FastConnect — SOP & Runbook",
        "category": "WAN / Hybrid Cloud",
        "description": (
            "End-to-end SOP and runbook for establishing Oracle Cloud FastConnect "
            "(1/10 Gbps) via colocation, provider, or exchange models. Covers "
            "customer-side and Oracle provider-side provisioning, BGP peering "
            "(Oracle ASN 31898), DRG v2 routing. Includes Cisco IOS-XE and Juniper "
            "JunOS router configurations. See template docs for full SOP, Mermaid "
            "diagrams, and failure runbook."
        ),
        "tags": json.dumps(
            [
                "oci",
                "oracle",
                "fastconnect",
                "hybrid-cloud",
                "bgp",
                "drg",
                "sop",
                "runbook",
                "cisco",
                "juniper",
            ]
        ),
        "graph_json": json.dumps(
            {
                "nodes": [
                    _node(
                        "cust-rtr",
                        "Customer Edge Router",
                        "router",
                        100,
                        200,
                        {
                            "config": {
                                "role": "CE router — BGP to Oracle edge (ASN 31898)",
                                "protocols": "BGP (eBGP to Oracle ASN 31898)",
                            }
                        },
                    ),
                    _node(
                        "cust-patch",
                        "Customer Patch Panel",
                        "odf",
                        260,
                        200,
                        {
                            "config": {
                                "role": "Customer cage patch panel in colocation",
                                "fiber_type": "Single-mode (1000BASE-LX / 10GBASE-LR)",
                            }
                        },
                    ),
                    _node(
                        "mmr",
                        "Meet-Me Room",
                        "odf",
                        420,
                        200,
                        {
                            "config": {
                                "role": "Colocation meet-me room cross-connect",
                            }
                        },
                    ),
                    _node(
                        "oci-port",
                        "OCI FastConnect Port",
                        "odf",
                        580,
                        200,
                        {
                            "config": {
                                "role": "Oracle edge port at FastConnect location",
                                "speed": "1 Gbps / 10 Gbps",
                            }
                        },
                    ),
                    _node(
                        "oci-edge",
                        "Oracle Edge Router",
                        "router",
                        740,
                        200,
                        {
                            "config": {
                                "role": "Oracle-managed edge router",
                                "owner": "Oracle (managed)",
                                "bgp_asn": "31898",
                            }
                        },
                    ),
                    _node(
                        "drg",
                        "Dynamic Routing Gateway",
                        "router",
                        740,
                        380,
                        {
                            "config": {
                                "role": "DRG — OCI virtual router for hybrid connectivity",
                                "notes": "DRG v2 supports multiple VCN attachments and transit routing",
                                "features": "Route tables, import/export route distributions",
                            }
                        },
                    ),
                    _node(
                        "vcn",
                        "OCI VCN",
                        "oci-vcn",
                        740,
                        520,
                        {
                            "config": {
                                "role": "Target Virtual Cloud Network with private subnets",
                                "cidr": "172.16.0.0/16 (example)",
                                "notes": "Add route rule: on-prem CIDR -> DRG in subnet route table",
                            }
                        },
                    ),
                    _node(
                        "on-prem",
                        "On-Premises Network",
                        "switch-l3",
                        100,
                        380,
                        {
                            "config": {
                                "role": "Corporate LAN / data center",
                                "cidr": "10.0.0.0/16 (advertised to OCI via BGP)",
                            }
                        },
                    ),
                ],
                "edges": [
                    _edge("on-prem", "cust-rtr", "Uplink", "OSPF/IS-IS"),
                    _edge("cust-rtr", "cust-patch", "Fiber (SM)", "802.1Q"),
                    _edge("cust-patch", "mmr", "Cross-Connect", ""),
                    _edge("mmr", "oci-port", "Cross-Connect", ""),
                    _edge("oci-port", "oci-edge", "Internal", ""),
                    _edge("oci-edge", "drg", "Private Virtual Circuit", "eBGP"),
                    _edge("drg", "vcn", "DRG Attachment", ""),
                ],
            }
        ),
    },
    # ══════════════════════════════════════════════════════════════════════════
    # DWDM Metro Ring — Expandable Ring Backbone
    # ══════════════════════════════════════════════════════════════════════════
    # ── DWDM Metro Ring (8-node, expandable) ─────────────────────────��───────
    {
        "id": "tpl-dwdm-metro-ring",
        "name": "DWDM Metro Ring — 8-Node Expandable",
        "category": "Transport / Optical",
        "description": (
            "Scalable 8-node metro DWDM ring with ROADM hubs and OADM access sites. "
            "96-channel C+L band (100G/400G coherent), EDFA amplifiers per span, "
            "bidirectional line-switched ring (BLSR) protection with sub-50ms failover. "
            "Expandable by duplicating OADM site groups and reconnecting ring edges. "
            "See template docs for Mermaid ring diagram, optical parameters, and "
            "expansion procedure."
        ),
        "tags": json.dumps(
            [
                "dwdm",
                "metro",
                "ring",
                "optical",
                "transport",
                "roadm",
                "oadm",
                "edfa",
                "blsr",
                "expandable",
                "scalable",
                "coherent",
                "100g",
                "400g",
            ]
        ),
        "graph_json": json.dumps(
            {
                "nodes": [
                    # ── Site A — ROADM Hub (Top Center) ──
                    _node(
                        "pop-a",
                        "Site A — ROADM Hub",
                        "pop",
                        500,
                        60,
                        {
                            "config": {
                                "role": "Primary hub site — full ROADM, 4-degree capable",
                                "facility": "Carrier POP / Data Center",
                            }
                        },
                    ),
                    _node(
                        "odf-a",
                        "ODF-A",
                        "odf",
                        420,
                        60,
                        {"config": {"role": "Fiber termination — ring East + West trunks"}},
                    ),
                    _node(
                        "roadm-a",
                        "ROADM-A (Hub)",
                        "roadm",
                        500,
                        140,
                        {
                            "config": {
                                "role": "Full ROADM — colorless/directionless add-drop",
                                "degree": "4-degree (2 ring + 2 express/add)",
                                "channels": "96-ch C+L band, 50 GHz grid",
                                "features": "WSS (Wavelength Selective Switch), OCM, VOA",
                            }
                        },
                    ),
                    _node(
                        "txp-a",
                        "Transponders-A",
                        "transponder",
                        580,
                        60,
                        {
                            "config": {
                                "role": "Coherent transponders — 100G/400G client interfaces",
                                "capacity": "Up to 96 wavelengths",
                            }
                        },
                    ),
                    # ── Site B — OADM (Upper Right) ──
                    _node(
                        "pop-b", "Site B — OADM", "pop", 800, 200, {"config": {"role": "Access site — OADM add/drop"}}
                    ),
                    _node("odf-b", "ODF-B", "odf", 880, 200),
                    _node(
                        "oadm-b",
                        "OADM-B",
                        "oadm",
                        800,
                        280,
                        {
                            "config": {
                                "role": "Fixed or flex-grid OADM — add/drop 8-16 channels",
                                "channels": "8-16 ch add/drop, remainder express-through",
                            }
                        },
                    ),
                    _node("txp-b", "Transponders-B", "transponder", 880, 280),
                    # ── Site C — ROADM (Right) ──
                    _node(
                        "pop-c",
                        "Site C — ROADM",
                        "pop",
                        900,
                        440,
                        {"config": {"role": "Secondary ROADM — intermediate hub"}},
                    ),
                    _node("odf-c", "ODF-C", "odf", 980, 440),
                    _node(
                        "roadm-c",
                        "ROADM-C",
                        "roadm",
                        900,
                        520,
                        {
                            "config": {
                                "role": "2-degree ROADM — full add-drop capability",
                                "degree": "2-degree (ring East + West)",
                            }
                        },
                    ),
                    _node("txp-c", "Transponders-C", "transponder", 980, 520),
                    # ── Site D — OADM (Lower Right) ──
                    _node("pop-d", "Site D — OADM", "pop", 800, 640, {"config": {"role": "Access site — OADM"}}),
                    _node("odf-d", "ODF-D", "odf", 880, 640),
                    _node("oadm-d", "OADM-D", "oadm", 800, 720, {"config": {"role": "Fixed OADM — 8 ch add/drop"}}),
                    _node("txp-d", "Transponders-D", "transponder", 880, 720),
                    # ── Site E — ROADM Hub (Bottom Center) ──
                    _node(
                        "pop-e",
                        "Site E — ROADM Hub",
                        "pop",
                        500,
                        780,
                        {
                            "config": {
                                "role": "Secondary hub — ROADM, DC interconnect",
                                "facility": "Disaster Recovery DC / Secondary POP",
                            }
                        },
                    ),
                    _node("odf-e", "ODF-E", "odf", 420, 780),
                    _node(
                        "roadm-e",
                        "ROADM-E (Hub)",
                        "roadm",
                        500,
                        700,
                        {
                            "config": {
                                "role": "Full ROADM — secondary hub for DC interconnect",
                                "degree": "4-degree",
                            }
                        },
                    ),
                    _node("txp-e", "Transponders-E", "transponder", 580, 780),
                    # ── Site F — OADM (Lower Left) ──
                    _node("pop-f", "Site F — OADM", "pop", 200, 640, {"config": {"role": "Access site — OADM"}}),
                    _node("odf-f", "ODF-F", "odf", 120, 640),
                    _node("oadm-f", "OADM-F", "oadm", 200, 720, {"config": {"role": "Fixed OADM — 8 ch add/drop"}}),
                    _node("txp-f", "Transponders-F", "transponder", 120, 720),
                    # ── Site G — OADM (Left) ──
                    _node("pop-g", "Site G — OADM", "pop", 100, 440, {"config": {"role": "Access site — OADM"}}),
                    _node("odf-g", "ODF-G", "odf", 20, 440),
                    _node("oadm-g", "OADM-G", "oadm", 100, 520, {"config": {"role": "Fixed OADM — 8 ch add/drop"}}),
                    _node("txp-g", "Transponders-G", "transponder", 20, 520),
                    # ── Site H — OADM (Upper Left) ──
                    _node("pop-h", "Site H — OADM", "pop", 200, 200, {"config": {"role": "Access site — OADM"}}),
                    _node("odf-h", "ODF-H", "odf", 120, 200),
                    _node("oadm-h", "OADM-H", "oadm", 200, 280, {"config": {"role": "Fixed OADM — 8 ch add/drop"}}),
                    _node("txp-h", "Transponders-H", "transponder", 120, 280),
                    # ── EDFA amplifiers (one per span) ──
                    _node(
                        "edfa-ab",
                        "EDFA A-B",
                        "edfa",
                        660,
                        120,
                        {"config": {"span": "A->B", "distance_km": "40", "gain_db": "22"}},
                    ),
                    _node(
                        "edfa-bc",
                        "EDFA B-C",
                        "edfa",
                        870,
                        360,
                        {"config": {"span": "B->C", "distance_km": "25", "gain_db": "18"}},
                    ),
                    _node(
                        "edfa-cd",
                        "EDFA C-D",
                        "edfa",
                        870,
                        580,
                        {"config": {"span": "C->D", "distance_km": "35", "gain_db": "20"}},
                    ),
                    _node(
                        "edfa-de",
                        "EDFA D-E",
                        "edfa",
                        660,
                        740,
                        {"config": {"span": "D->E", "distance_km": "50", "gain_db": "24"}},
                    ),
                    _node(
                        "edfa-ef",
                        "EDFA E-F",
                        "edfa",
                        340,
                        740,
                        {"config": {"span": "E->F", "distance_km": "30", "gain_db": "19"}},
                    ),
                    _node(
                        "edfa-fg",
                        "EDFA F-G",
                        "edfa",
                        130,
                        580,
                        {"config": {"span": "F->G", "distance_km": "20", "gain_db": "16"}},
                    ),
                    _node(
                        "edfa-gh",
                        "EDFA G-H",
                        "edfa",
                        130,
                        360,
                        {"config": {"span": "G->H", "distance_km": "45", "gain_db": "23"}},
                    ),
                    _node(
                        "edfa-ha",
                        "EDFA H-A",
                        "edfa",
                        340,
                        120,
                        {"config": {"span": "H->A", "distance_km": "35", "gain_db": "20"}},
                    ),
                ],
                "edges": [
                    # ── Internal site wiring ──
                    _edge("pop-a", "odf-a", "Trunk fiber", ""),
                    _edge("odf-a", "roadm-a", "λ1-96", "DWDM"),
                    _edge("roadm-a", "txp-a", "Client 100G/400G", ""),
                    _edge("pop-b", "odf-b", "Trunk fiber", ""),
                    _edge("odf-b", "oadm-b", "λ add/drop", "DWDM"),
                    _edge("oadm-b", "txp-b", "Client", ""),
                    _edge("pop-c", "odf-c", "Trunk fiber", ""),
                    _edge("odf-c", "roadm-c", "λ1-96", "DWDM"),
                    _edge("roadm-c", "txp-c", "Client 100G/400G", ""),
                    _edge("pop-d", "odf-d", "Trunk fiber", ""),
                    _edge("odf-d", "oadm-d", "λ add/drop", "DWDM"),
                    _edge("oadm-d", "txp-d", "Client", ""),
                    _edge("pop-e", "odf-e", "Trunk fiber", ""),
                    _edge("odf-e", "roadm-e", "λ1-96", "DWDM"),
                    _edge("roadm-e", "txp-e", "Client 100G/400G", ""),
                    _edge("pop-f", "odf-f", "Trunk fiber", ""),
                    _edge("odf-f", "oadm-f", "λ add/drop", "DWDM"),
                    _edge("oadm-f", "txp-f", "Client", ""),
                    _edge("pop-g", "odf-g", "Trunk fiber", ""),
                    _edge("odf-g", "oadm-g", "λ add/drop", "DWDM"),
                    _edge("oadm-g", "txp-g", "Client", ""),
                    _edge("pop-h", "odf-h", "Trunk fiber", ""),
                    _edge("odf-h", "oadm-h", "λ add/drop", "DWDM"),
                    _edge("oadm-h", "txp-h", "Client", ""),
                    # ── Ring spans (clockwise: A→B→C→D→E→F→G→H→A) ──
                    _edge("roadm-a", "edfa-ab", "East λ1-96 (40km)", "DWDM BLSR"),
                    _edge("edfa-ab", "oadm-b", "Amplified", ""),
                    _edge("oadm-b", "edfa-bc", "Express λ (25km)", "DWDM BLSR"),
                    _edge("edfa-bc", "roadm-c", "Amplified", ""),
                    _edge("roadm-c", "edfa-cd", "East λ1-96 (35km)", "DWDM BLSR"),
                    _edge("edfa-cd", "oadm-d", "Amplified", ""),
                    _edge("oadm-d", "edfa-de", "Express λ (50km)", "DWDM BLSR"),
                    _edge("edfa-de", "roadm-e", "Amplified", ""),
                    _edge("roadm-e", "edfa-ef", "West λ1-96 (30km)", "DWDM BLSR"),
                    _edge("edfa-ef", "oadm-f", "Amplified", ""),
                    _edge("oadm-f", "edfa-fg", "Express λ (20km)", "DWDM BLSR"),
                    _edge("edfa-fg", "oadm-g", "Amplified", ""),
                    _edge("oadm-g", "edfa-gh", "Express λ (45km)", "DWDM BLSR"),
                    _edge("edfa-gh", "oadm-h", "Amplified", ""),
                    _edge("oadm-h", "edfa-ha", "Express λ (35km)", "DWDM BLSR"),
                    _edge("edfa-ha", "roadm-a", "Amplified (ring close)", ""),
                ],
            }
        ),
    },
    # ── Customer-to-Provider DWDM Access — Detailed Demarcation ──────────────
    {
        "id": "tpl-dwdm-customer-provider-access",
        "name": "DWDM Customer-Provider Access — Demarcation & Redundancy",
        "category": "Transport / Optical",
        "description": (
            "Comprehensive customer-to-provider DWDM wavelength service demarcation "
            "with dual-path redundancy. Shows every physical object: CE routers, "
            "transponders, customer patch panels, diverse meet-me rooms, cross-connects, "
            "provider patch panels, ROADM/OADM, EDFA, and DWDM ring nodes. "
            "See template docs for full SOP (customer and provider sides), Mermaid "
            "diagrams, and failure runbook."
        ),
        "tags": json.dumps(
            [
                "dwdm",
                "customer",
                "provider",
                "demarcation",
                "cross-connect",
                "meet-me",
                "pop",
                "oadm",
                "roadm",
                "edfa",
                "transponder",
                "odf",
                "redundancy",
                "diverse",
                "blsr",
                "wavelength",
                "sop",
                "runbook",
            ]
        ),
        "graph_json": json.dumps(
            {
                "nodes": [
                    # ════════════════════════════════════════════════════
                    # CUSTOMER SIDE
                    # ════════════════════════════════════════════════════
                    # ── Customer Core ──
                    _node(
                        "cust-sw",
                        "Customer Core Switch",
                        "switch-l3",
                        60,
                        380,
                        {
                            "config": {
                                "role": "Core/distribution switch — feeds both CE routers",
                                "redundancy": "VSS/vPC stack for single point of management",
                            }
                        },
                    ),
                    _node(
                        "ce-rtr-1",
                        "CE Router 1 (Primary)",
                        "router",
                        200,
                        240,
                        {
                            "config": {
                                "role": "Primary customer edge router",
                                "interface": "100G QSFP28 towards transponder",
                                "protocols": "BGP / OSPF / MPLS",
                                "redundancy": "Active path — primary DWDM wavelength",
                            }
                        },
                    ),
                    _node(
                        "ce-rtr-2",
                        "CE Router 2 (Secondary)",
                        "router",
                        200,
                        520,
                        {
                            "config": {
                                "role": "Secondary customer edge router (diverse path)",
                                "interface": "100G QSFP28 towards transponder",
                                "redundancy": "Standby or ECMP — diverse DWDM wavelength",
                            }
                        },
                    ),
                    # ── Customer Cage (Colocation) — Primary ──
                    _node(
                        "cust-txp-1",
                        "Cust Transponder 1",
                        "transponder",
                        360,
                        240,
                        {
                            "config": {
                                "role": "Customer-owned coherent transponder (primary)",
                                "modulation": "DP-QPSK (100G) or DP-16QAM (400G)",
                                "wavelength": "Assigned by carrier (e.g., λ32 = 1551.72nm)",
                                "notes": "Must match carrier wavelength plan exactly",
                            }
                        },
                    ),
                    _node(
                        "cust-pp-1",
                        "Cust Patch Panel 1",
                        "patch-panel-fiber",
                        500,
                        240,
                        {
                            "config": {
                                "role": "Customer cage fiber patch panel (primary path)",
                                "connector": "LC-UPC duplex, single-mode",
                                "labeling": "Port A1: CE-RTR-1 Tx, Port A2: CE-RTR-1 Rx",
                            }
                        },
                    ),
                    # ── Customer Cage (Colocation) — Secondary ──
                    _node(
                        "cust-txp-2",
                        "Cust Transponder 2",
                        "transponder",
                        360,
                        520,
                        {
                            "config": {
                                "role": "Customer-owned coherent transponder (secondary/diverse)",
                                "modulation": "DP-QPSK (100G) or DP-16QAM (400G)",
                                "wavelength": "Assigned by carrier (diverse λ, e.g., λ64 = 1558.17nm)",
                            }
                        },
                    ),
                    _node(
                        "cust-pp-2",
                        "Cust Patch Panel 2",
                        "patch-panel-fiber",
                        500,
                        520,
                        {
                            "config": {
                                "role": "Customer cage fiber patch panel (diverse path)",
                                "connector": "LC-UPC duplex, single-mode",
                            }
                        },
                    ),
                    # ════════════════════════════════════════════════════
                    # DEMARCATION / MEET-ME ROOMS
                    # ════════════════════════════════════════════════════
                    _node(
                        "mmr-a",
                        "Meet-Me Room A (Primary)",
                        "odf",
                        660,
                        240,
                        {
                            "config": {
                                "role": "Colocation meet-me room — primary cross-connect",
                                "owner": "Colocation provider (Equinix, CoreSite, QTS, etc.)",
                                "notes": "Demarcation point — customer responsibility ends here",
                            }
                        },
                    ),
                    _node(
                        "xc-a",
                        "Cross-Connect A",
                        "odf",
                        660,
                        320,
                        {
                            "config": {
                                "role": "Physical cross-connect: customer cage -> provider cage",
                                "fiber_type": "Single-mode, LC-UPC or SC-APC",
                                "install": "Ordered via LOA-CFA from customer to colocation",
                                "sla": "Typically 5 business days for install",
                            }
                        },
                    ),
                    _node(
                        "mmr-b",
                        "Meet-Me Room B (Diverse)",
                        "odf",
                        660,
                        520,
                        {
                            "config": {
                                "role": "Diverse meet-me room — physically separate from MMR-A",
                                "diversity": "Different floor, riser, or building wing",
                                "notes": "Ensures no single point of failure at colo level",
                            }
                        },
                    ),
                    _node(
                        "xc-b",
                        "Cross-Connect B",
                        "odf",
                        660,
                        440,
                        {
                            "config": {
                                "role": "Physical cross-connect: customer cage -> provider cage (diverse)",
                                "fiber_type": "Single-mode, LC-UPC or SC-APC",
                                "diversity": "Routed through separate conduit/riser from XC-A",
                            }
                        },
                    ),
                    # ════════════════════════════════════════════════════
                    # PROVIDER SIDE — POP A (Primary)
                    # ════════════════════════════════════════════════════
                    _node(
                        "prov-pp-a",
                        "Provider Patch Panel A",
                        "patch-panel-fiber",
                        820,
                        200,
                        {
                            "config": {
                                "role": "Provider cage fiber patch panel — terminates customer cross-connect",
                                "labeling": "Circuit ID, customer name, wavelength assignment",
                            }
                        },
                    ),
                    _node(
                        "prov-txp-a",
                        "Provider Transponder A",
                        "transponder",
                        820,
                        280,
                        {
                            "config": {
                                "role": "Provider-side transponder (if customer doesn't own optics)",
                                "notes": "Used in managed wavelength service model",
                                "modulation": "Must match customer transponder modulation/FEC",
                            }
                        },
                    ),
                    _node(
                        "prov-oadm-a",
                        "ROADM / OADM A",
                        "roadm",
                        960,
                        240,
                        {
                            "config": {
                                "role": "Provider ROADM at POP A — adds customer wavelength to ring",
                                "add_drop": "Add: customer λ onto ring East direction",
                                "express": "All other wavelengths pass through",
                                "monitoring": "OCM per-channel power, OSNR",
                            }
                        },
                    ),
                    _node(
                        "prov-edfa-a",
                        "EDFA A (Booster)",
                        "edfa",
                        1080,
                        240,
                        {
                            "config": {
                                "role": "Post-ROADM booster amplifier — launches signal onto ring fiber",
                                "gain": "18-24 dB (auto-gain control)",
                                "output_power": "+17 dBm aggregate",
                            }
                        },
                    ),
                    _node(
                        "prov-pop-a",
                        "Provider POP A",
                        "pop",
                        960,
                        160,
                        {
                            "config": {
                                "role": "Provider POP — primary ring access point",
                                "facility": "Carrier central office or colocation",
                            }
                        },
                    ),
                    # ════════════════════════════════════════════════════
                    # PROVIDER SIDE — POP B (Diverse)
                    # ════════════════════════════════════════════════════
                    _node(
                        "prov-pp-b",
                        "Provider Patch Panel B",
                        "patch-panel-fiber",
                        820,
                        480,
                        {
                            "config": {
                                "role": "Provider cage patch panel — diverse path",
                            }
                        },
                    ),
                    _node(
                        "prov-txp-b",
                        "Provider Transponder B",
                        "transponder",
                        820,
                        560,
                        {
                            "config": {
                                "role": "Provider-side transponder (diverse path)",
                            }
                        },
                    ),
                    _node(
                        "prov-oadm-b",
                        "ROADM / OADM B",
                        "roadm",
                        960,
                        520,
                        {
                            "config": {
                                "role": "Provider ROADM at POP B — diverse add/drop point",
                                "add_drop": "Add: customer λ onto ring West direction (diverse from POP A)",
                                "diversity": "Different ring segment from POP A",
                            }
                        },
                    ),
                    _node(
                        "prov-edfa-b",
                        "EDFA B (Booster)",
                        "edfa",
                        1080,
                        520,
                        {
                            "config": {
                                "role": "Post-ROADM booster amplifier — diverse ring direction",
                                "gain": "18-24 dB",
                            }
                        },
                    ),
                    _node(
                        "prov-pop-b",
                        "Provider POP B (Diverse)",
                        "pop",
                        960,
                        600,
                        {
                            "config": {
                                "role": "Provider POP — diverse ring access point",
                                "diversity": "Different physical location from POP A on the ring",
                            }
                        },
                    ),
                    # ════════════════════════════════════════════════════
                    # DWDM RING (Abstracted — 4 ring nodes)
                    # ════════════════════════════════════════════════════
                    _node(
                        "ring-n1",
                        "Ring Node 1",
                        "roadm",
                        1200,
                        200,
                        {"config": {"role": "DWDM ring node — express-through"}},
                    ),
                    _node(
                        "ring-n2",
                        "Ring Node 2",
                        "roadm",
                        1320,
                        380,
                        {"config": {"role": "DWDM ring node — express-through"}},
                    ),
                    _node(
                        "ring-n3",
                        "Ring Node 3",
                        "roadm",
                        1200,
                        560,
                        {"config": {"role": "DWDM ring node — express-through"}},
                    ),
                    _node(
                        "ring-edfa-1",
                        "EDFA (Ring)",
                        "edfa",
                        1280,
                        280,
                        {"config": {"role": "Inline EDFA — ring span amplification"}},
                    ),
                    _node(
                        "ring-edfa-2",
                        "EDFA (Ring)",
                        "edfa",
                        1280,
                        480,
                        {"config": {"role": "Inline EDFA — ring span amplification"}},
                    ),
                ],
                "edges": [
                    # ── Customer internal ──
                    _edge("cust-sw", "ce-rtr-1", "10G/100G LAG", ""),
                    _edge("cust-sw", "ce-rtr-2", "10G/100G LAG", ""),
                    # ── Customer to transponders ──
                    _edge("ce-rtr-1", "cust-txp-1", "100G QSFP28 (gray)", ""),
                    _edge("ce-rtr-2", "cust-txp-2", "100G QSFP28 (gray)", ""),
                    # ── Transponders to customer patch panels ──
                    _edge("cust-txp-1", "cust-pp-1", "Colored λ (SM fiber)", "DWDM"),
                    _edge("cust-txp-2", "cust-pp-2", "Colored λ (SM fiber)", "DWDM"),
                    # ── Customer patch panels to meet-me rooms ──
                    _edge("cust-pp-1", "mmr-a", "Intra-colo fiber", ""),
                    _edge("cust-pp-2", "mmr-b", "Intra-colo fiber (diverse)", ""),
                    # ── Meet-me room cross-connects ──
                    _edge("mmr-a", "xc-a", "Cross-Connect A", ""),
                    _edge("xc-a", "prov-pp-a", "Cust->Provider (Primary)", ""),
                    _edge("mmr-b", "xc-b", "Cross-Connect B", ""),
                    _edge("xc-b", "prov-pp-b", "Cust->Provider (Diverse)", ""),
                    # ── Provider POP A internal ──
                    _edge("prov-pop-a", "prov-pp-a", "Fiber trunk", ""),
                    _edge("prov-pp-a", "prov-txp-a", "Client handoff", ""),
                    _edge("prov-txp-a", "prov-oadm-a", "Line side λ", "DWDM"),
                    _edge("prov-oadm-a", "prov-edfa-a", "Amplified WDM", ""),
                    # ── Provider POP B internal ──
                    _edge("prov-pop-b", "prov-pp-b", "Fiber trunk", ""),
                    _edge("prov-pp-b", "prov-txp-b", "Client handoff", ""),
                    _edge("prov-txp-b", "prov-oadm-b", "Line side λ", "DWDM"),
                    _edge("prov-oadm-b", "prov-edfa-b", "Amplified WDM", ""),
                    # ── Provider to DWDM Ring ──
                    _edge("prov-edfa-a", "ring-n1", "East ring (primary)", "DWDM"),
                    _edge("prov-edfa-b", "ring-n3", "West ring (diverse)", "DWDM"),
                    # ── Ring interconnection ──
                    _edge("ring-n1", "ring-edfa-1", "Span (working)", "DWDM BLSR"),
                    _edge("ring-edfa-1", "ring-n2", "Amplified", ""),
                    _edge("ring-n2", "ring-edfa-2", "Span (working)", "DWDM BLSR"),
                    _edge("ring-edfa-2", "ring-n3", "Amplified", ""),
                    _edge("ring-n3", "ring-n1", "Protect path", "DWDM BLSR"),
                ],
            }
        ),
    },
    # ══════════════════════════════════════════════════════════════════════════
    # AWS Transit Gateway / DX Gateway / VPN Gateway Templates
    # ══════════════════════════════════════════════════════════════════════════
    # ── 1. AWS Transit Gateway Hub-and-Spoke (Multi-VPC Segmentation) ────────
    {
        "id": "tpl-aws-tgw-hub-spoke",
        "name": "AWS Transit Gateway — Hub-and-Spoke Multi-VPC",
        "category": "AWS / Hybrid Cloud",
        "description": (
            "Transit Gateway as a regional hub connecting multiple segmented VPCs "
            "(production, development, shared services, inspection) with route table "
            "isolation. Network Firewall in centralized inspection VPC for east-west "
            "traffic. Supports 5,000 attachments per TGW, 50 Gbps per AZ burst. "
            "See template docs for full SOP, Mermaid diagrams, and route table design."
        ),
        "tags": json.dumps(
            [
                "aws",
                "tgw",
                "transit-gateway",
                "hub-spoke",
                "multi-vpc",
                "network-firewall",
                "segmentation",
                "route-table",
                "sop",
                "runbook",
            ]
        ),
        "graph_json": json.dumps(
            {
                "nodes": [
                    # ── Transit Gateway (Center Hub) ──
                    _node(
                        "tgw",
                        "Transit Gateway",
                        "aws-tgw",
                        500,
                        350,
                        {
                            "config": {
                                "role": "Regional hub — all VPC-to-VPC and VPC-to-on-prem traffic",
                                "asn": "64512 (TGW BGP ASN — configurable)",
                                "features": "Multicast, inter-region peering, ECMP, route tables",
                                "limits": "5,000 attachments, 10,000 routes per table, 50 Gbps/AZ burst",
                            }
                        },
                    ),
                    # ── TGW Route Tables (logical, shown as labels) ──
                    _node(
                        "rt-prod",
                        "RT: Production",
                        "aws-subnet",
                        340,
                        250,
                        {
                            "config": {
                                "role": "TGW route table — Production segment",
                                "associations": "Prod VPC attachment",
                                "propagations": "Shared Services, Inspection (no Dev)",
                                "blackhole": "Dev VPC CIDRs (isolation)",
                            }
                        },
                    ),
                    _node(
                        "rt-dev",
                        "RT: Development",
                        "aws-subnet",
                        660,
                        250,
                        {
                            "config": {
                                "role": "TGW route table — Dev/Staging segment",
                                "associations": "Dev VPC attachment",
                                "propagations": "Shared Services, Inspection (no Prod)",
                                "blackhole": "Prod VPC CIDRs (isolation)",
                            }
                        },
                    ),
                    _node(
                        "rt-shared",
                        "RT: Shared Services",
                        "aws-subnet",
                        340,
                        450,
                        {
                            "config": {
                                "role": "TGW route table — Shared Services (DNS, AD, logging)",
                                "associations": "Shared VPC attachment",
                                "propagations": "All segments (hub for shared access)",
                            }
                        },
                    ),
                    _node(
                        "rt-inspect",
                        "RT: Inspection",
                        "aws-subnet",
                        660,
                        450,
                        {
                            "config": {
                                "role": "TGW route table — Centralized inspection VPC",
                                "associations": "Inspection VPC attachment",
                                "propagations": "All segments (forces traffic through NFW)",
                                "notes": "Default route 0.0.0.0/0 -> Inspection VPC for egress",
                            }
                        },
                    ),
                    # ── Production VPC ──
                    _node(
                        "vpc-prod",
                        "Production VPC",
                        "aws-vpc",
                        140,
                        100,
                        {
                            "config": {
                                "cidr": "10.1.0.0/16",
                                "subnets": "Private app (10.1.1.0/24, 10.1.2.0/24), Private DB (10.1.10.0/24, 10.1.11.0/24), TGW (10.1.255.0/28, 10.1.255.16/28)",
                                "az": "us-east-1a, us-east-1b (multi-AZ)",
                            }
                        },
                    ),
                    _node("alb-prod", "ALB (Prod)", "aws-alb", 60, 100),
                    _node("sub-prod-app", "App Subnet (Prod)", "aws-subnet", 140, 180),
                    _node("sub-prod-db", "DB Subnet (Prod)", "aws-subnet", 60, 180),
                    # ── Development VPC ──
                    _node(
                        "vpc-dev",
                        "Development VPC",
                        "aws-vpc",
                        860,
                        100,
                        {
                            "config": {
                                "cidr": "10.2.0.0/16",
                                "subnets": "Private app, Private DB, TGW attachment",
                            }
                        },
                    ),
                    _node("alb-dev", "ALB (Dev)", "aws-alb", 940, 100),
                    _node("sub-dev-app", "App Subnet (Dev)", "aws-subnet", 860, 180),
                    # ── Shared Services VPC ──
                    _node(
                        "vpc-shared",
                        "Shared Services VPC",
                        "aws-vpc",
                        140,
                        560,
                        {
                            "config": {
                                "cidr": "10.0.0.0/16",
                                "services": "Route 53 Resolver, AD Connector, CloudWatch, S3 endpoints",
                            }
                        },
                    ),
                    _node("r53", "Route 53 Resolver", "aws-r53", 60, 560),
                    _node("ad", "Managed AD", "aws-ad", 60, 640),
                    _node("ep-s3", "S3 Gateway Endpoint", "aws-gw-ep", 140, 640),
                    # ── Centralized Inspection VPC ──
                    _node(
                        "vpc-inspect",
                        "Inspection VPC",
                        "aws-vpc",
                        860,
                        560,
                        {
                            "config": {
                                "cidr": "100.64.0.0/16 (RFC 6598 — non-routable)",
                                "subnets": "Firewall (100.64.1.0/24), TGW (100.64.255.0/28), NAT GW (100.64.2.0/24)",
                                "notes": "All east-west and egress traffic forced through NFW",
                            }
                        },
                    ),
                    _node(
                        "nfw",
                        "Network Firewall",
                        "aws-nfw",
                        860,
                        640,
                        {
                            "config": {
                                "role": "Stateful inspection — IDS/IPS, domain filtering, TLS inspection",
                                "rules": "Suricata-compatible, managed rule groups + custom",
                            }
                        },
                    ),
                    _node(
                        "nat-gw",
                        "NAT Gateway",
                        "aws-subnet",
                        940,
                        640,
                        {"config": {"role": "Egress to internet via NFW -> NAT GW -> IGW"}},
                    ),
                    _node("igw", "Internet Gateway", "aws-subnet", 940, 560),
                    # ── On-Premises (via DX or VPN) ──
                    _node(
                        "on-prem",
                        "On-Premises DC",
                        "router",
                        500,
                        650,
                        {
                            "config": {
                                "role": "Corporate data center — connected via DX or VPN to TGW",
                                "cidr": "172.16.0.0/12 (on-prem aggregate)",
                            }
                        },
                    ),
                    # ── Monitoring ──
                    _node(
                        "netmgr",
                        "Network Manager",
                        "aws-netmgr",
                        500,
                        180,
                        {"config": {"role": "TGW route analysis, topology visualization, CloudWatch metrics"}},
                    ),
                    _node(
                        "flowlogs",
                        "VPC Flow Logs",
                        "aws-flowlogs",
                        500,
                        100,
                        {"config": {"role": "All VPCs — flow logs to S3 + CloudWatch for SIEM"}},
                    ),
                ],
                "edges": [
                    # ── TGW attachments ──
                    _edge("vpc-prod", "tgw", "TGW Attachment (Prod)", ""),
                    _edge("vpc-dev", "tgw", "TGW Attachment (Dev)", ""),
                    _edge("vpc-shared", "tgw", "TGW Attachment (Shared)", ""),
                    _edge("vpc-inspect", "tgw", "TGW Attachment (Inspect)", ""),
                    # ── Route table associations ──
                    _edge("rt-prod", "tgw", "Prod RT", ""),
                    _edge("rt-dev", "tgw", "Dev RT", ""),
                    _edge("rt-shared", "tgw", "Shared RT", ""),
                    _edge("rt-inspect", "tgw", "Inspect RT", ""),
                    # ── VPC internals ──
                    _edge("vpc-prod", "alb-prod", "", ""),
                    _edge("vpc-prod", "sub-prod-app", "", ""),
                    _edge("vpc-prod", "sub-prod-db", "", ""),
                    _edge("vpc-dev", "alb-dev", "", ""),
                    _edge("vpc-dev", "sub-dev-app", "", ""),
                    _edge("vpc-shared", "r53", "DNS", ""),
                    _edge("vpc-shared", "ad", "AD DS", ""),
                    _edge("vpc-shared", "ep-s3", "S3 Endpoint", ""),
                    _edge("vpc-inspect", "nfw", "Firewall subnet", ""),
                    _edge("nfw", "nat-gw", "Post-inspection egress", ""),
                    _edge("nat-gw", "igw", "Internet", ""),
                    # ── On-prem connectivity ──
                    _edge("on-prem", "tgw", "DX or VPN", "BGP"),
                    # ── Monitoring ──
                    _edge("tgw", "netmgr", "Route analysis", ""),
                    _edge("flowlogs", "tgw", "All VPC logs", ""),
                ],
            }
        ),
    },
    # ── 2. AWS DX Gateway — Multi-Region Global Connectivity ─────────────────
    {
        "id": "tpl-aws-dxgw-multi-region",
        "name": "AWS DX Gateway — Multi-Region with TGW Peering",
        "category": "AWS / Hybrid Cloud",
        "description": (
            "DX Gateway as a global fabric connecting on-premises to multiple AWS "
            "regions via Transit Gateways and VGWs. Shows dual DX circuits (primary/"
            "secondary) at diverse locations, DX Gateway associating up to 6 TGWs "
            "across regions, inter-region TGW peering, and allowed prefix filters. "
            "See template docs for full SOP, Mermaid diagrams, and prefix management."
        ),
        "tags": json.dumps(
            [
                "aws",
                "dx-gateway",
                "dxgw",
                "multi-region",
                "transit-gateway",
                "direct-connect",
                "global",
                "bgp",
                "sop",
                "runbook",
            ]
        ),
        "graph_json": json.dumps(
            {
                "nodes": [
                    # ── On-Premises ──
                    _node(
                        "ce-rtr-1",
                        "CE Router 1 (Primary)",
                        "router",
                        80,
                        350,
                        {
                            "config": {
                                "role": "Primary CE — DX Location A (e.g., Equinix DC)",
                                "bgp_asn": "65001 (customer)",
                            }
                        },
                    ),
                    _node(
                        "ce-rtr-2",
                        "CE Router 2 (Secondary)",
                        "router",
                        80,
                        500,
                        {
                            "config": {
                                "role": "Secondary CE — DX Location B (diverse facility)",
                                "bgp_asn": "65001 (customer)",
                            }
                        },
                    ),
                    _node("on-prem-sw", "On-Prem Core", "switch-l3", 80, 425, {"config": {"cidr": "172.16.0.0/12"}}),
                    # ── DX Connections ──
                    _node(
                        "dx-pri",
                        "DX Primary (10G)",
                        "aws-dx",
                        250,
                        350,
                        {
                            "config": {
                                "speed": "10 Gbps dedicated",
                                "location": "DX Location A (e.g., Equinix DC2 Ashburn)",
                                "lag": "LAG with 2x10G for 20G aggregate (optional)",
                            }
                        },
                    ),
                    _node(
                        "dx-sec",
                        "DX Secondary (10G)",
                        "aws-dx",
                        250,
                        500,
                        {
                            "config": {
                                "speed": "10 Gbps dedicated",
                                "location": "DX Location B (diverse — e.g., CoreSite VA1)",
                                "diversity": "Different facility from primary for maximum resilience",
                            }
                        },
                    ),
                    # ── Transit VIFs ──
                    _node(
                        "tvif-pri",
                        "Transit VIF (Primary)",
                        "aws-subnet",
                        400,
                        350,
                        {
                            "config": {
                                "type": "Transit Virtual Interface",
                                "vlan": "100",
                                "bgp_asn_aws": "64512 (TGW ASN)",
                                "bgp_asn_cust": "65001",
                                "notes": "Transit VIF connects DX -> DX Gateway -> TGW (not VGW)",
                            }
                        },
                    ),
                    _node(
                        "tvif-sec",
                        "Transit VIF (Secondary)",
                        "aws-subnet",
                        400,
                        500,
                        {
                            "config": {
                                "type": "Transit Virtual Interface",
                                "vlan": "200",
                            }
                        },
                    ),
                    _node(
                        "pvif-legacy",
                        "Private VIF (Legacy)",
                        "aws-subnet",
                        400,
                        650,
                        {
                            "config": {
                                "type": "Private Virtual Interface",
                                "notes": "Private VIF -> DX Gateway -> VGW (for non-TGW VPCs)",
                            }
                        },
                    ),
                    # ── DX Gateway (Global) ──
                    _node(
                        "dxgw",
                        "DX Gateway (Global)",
                        "aws-dx-gw",
                        580,
                        425,
                        {
                            "config": {
                                "role": "Global resource — not region-specific",
                                "limits": "Up to 6 TGW associations + 20 VGW associations",
                                "asn": "64512 (DX Gateway ASN — must differ from TGW ASN)",
                                "prefixes": "Allowed prefix filters per association (max 20 per TGW, 100 per VGW)",
                                "notes": "Single DX Gateway can serve ALL regions simultaneously",
                            }
                        },
                    ),
                    # ── Region 1: us-east-1 ──
                    _node(
                        "tgw-use1",
                        "TGW (us-east-1)",
                        "aws-tgw",
                        780,
                        200,
                        {
                            "config": {
                                "region": "us-east-1",
                                "asn": "64513",
                                "attachments": "DX Gateway, VPCs, VPN",
                            }
                        },
                    ),
                    _node(
                        "vpc-prod-use1",
                        "Prod VPC (us-east-1)",
                        "aws-vpc",
                        940,
                        120,
                        {"config": {"cidr": "10.1.0.0/16"}},
                    ),
                    _node(
                        "vpc-shared-use1",
                        "Shared VPC (us-east-1)",
                        "aws-vpc",
                        940,
                        200,
                        {"config": {"cidr": "10.0.0.0/16"}},
                    ),
                    _node("nfw-use1", "NFW (us-east-1)", "aws-nfw", 940, 280),
                    # ── Region 2: us-west-2 ──
                    _node(
                        "tgw-usw2",
                        "TGW (us-west-2)",
                        "aws-tgw",
                        780,
                        425,
                        {
                            "config": {
                                "region": "us-west-2",
                                "asn": "64514",
                                "attachments": "DX Gateway, VPCs, VPN",
                            }
                        },
                    ),
                    _node(
                        "vpc-prod-usw2",
                        "Prod VPC (us-west-2)",
                        "aws-vpc",
                        940,
                        380,
                        {"config": {"cidr": "10.3.0.0/16"}},
                    ),
                    _node(
                        "vpc-dr-usw2", "DR VPC (us-west-2)", "aws-vpc", 940, 460, {"config": {"cidr": "10.4.0.0/16"}}
                    ),
                    # ── Region 3: eu-west-1 ──
                    _node(
                        "tgw-euw1",
                        "TGW (eu-west-1)",
                        "aws-tgw",
                        780,
                        620,
                        {
                            "config": {
                                "region": "eu-west-1",
                                "asn": "64515",
                            }
                        },
                    ),
                    _node("vpc-eu", "EU VPC (eu-west-1)", "aws-vpc", 940, 620, {"config": {"cidr": "10.5.0.0/16"}}),
                    # ── Legacy Region (VGW path) ──
                    _node(
                        "vgw-legacy",
                        "VGW (ap-southeast-1)",
                        "aws-vpn",
                        780,
                        750,
                        {
                            "config": {
                                "role": "Virtual Private Gateway — legacy VPC (no TGW)",
                                "region": "ap-southeast-1",
                                "notes": "DX Gateway -> VGW (Private VIF path, not Transit VIF)",
                            }
                        },
                    ),
                    _node(
                        "vpc-legacy", "Legacy VPC (ap-se-1)", "aws-vpc", 940, 750, {"config": {"cidr": "10.8.0.0/16"}}
                    ),
                    # ── VPN Backup ──
                    _node(
                        "vpn-backup",
                        "VPN Backup (us-east-1)",
                        "aws-vpn",
                        580,
                        120,
                        {
                            "config": {
                                "role": "Site-to-Site VPN as backup for DX failure",
                                "tunnels": "2 IPsec tunnels (active/active or active/passive)",
                                "throughput": "Up to 1.25 Gbps per tunnel (ECMP across tunnels)",
                                "bgp": "BGP over VPN — AS-PATH prepend to prefer DX",
                            }
                        },
                    ),
                    # ── Inter-Region TGW Peering ──
                    _node(
                        "peer-use1-usw2",
                        "TGW Peering",
                        "aws-tgw",
                        860,
                        310,
                        {
                            "config": {
                                "role": "Inter-region TGW peering (us-east-1 <-> us-west-2)",
                                "bandwidth": "Up to 50 Gbps",
                                "notes": "Static routes only (no BGP), requires route entries in both TGW RTs",
                            }
                        },
                    ),
                ],
                "edges": [
                    # On-prem to DX
                    _edge("on-prem-sw", "ce-rtr-1", "Uplink", "OSPF"),
                    _edge("on-prem-sw", "ce-rtr-2", "Uplink", "OSPF"),
                    _edge("ce-rtr-1", "dx-pri", "DX Primary (10G)", "802.1Q"),
                    _edge("ce-rtr-2", "dx-sec", "DX Secondary (10G)", "802.1Q"),
                    # DX to Transit VIFs
                    _edge("dx-pri", "tvif-pri", "Transit VIF", ""),
                    _edge("dx-sec", "tvif-sec", "Transit VIF", ""),
                    _edge("dx-pri", "pvif-legacy", "Private VIF", ""),
                    # Transit VIFs to DX Gateway
                    _edge("tvif-pri", "dxgw", "Primary path", "eBGP"),
                    _edge("tvif-sec", "dxgw", "Secondary path", "eBGP"),
                    _edge("pvif-legacy", "dxgw", "Legacy path", "eBGP"),
                    # DX Gateway to regional TGWs
                    _edge("dxgw", "tgw-use1", "Association (us-east-1)", "BGP"),
                    _edge("dxgw", "tgw-usw2", "Association (us-west-2)", "BGP"),
                    _edge("dxgw", "tgw-euw1", "Association (eu-west-1)", "BGP"),
                    _edge("dxgw", "vgw-legacy", "Association (ap-se-1)", "BGP"),
                    # Regional TGWs to VPCs
                    _edge("tgw-use1", "vpc-prod-use1", "Attachment", ""),
                    _edge("tgw-use1", "vpc-shared-use1", "Attachment", ""),
                    _edge("tgw-use1", "nfw-use1", "Inspection", ""),
                    _edge("tgw-usw2", "vpc-prod-usw2", "Attachment", ""),
                    _edge("tgw-usw2", "vpc-dr-usw2", "Attachment", ""),
                    _edge("tgw-euw1", "vpc-eu", "Attachment", ""),
                    _edge("vgw-legacy", "vpc-legacy", "VGW Attachment", ""),
                    # VPN backup
                    _edge("ce-rtr-1", "vpn-backup", "IPsec VPN (backup)", "BGP"),
                    _edge("vpn-backup", "tgw-use1", "VPN Attachment", ""),
                    # Inter-region peering
                    _edge("tgw-use1", "peer-use1-usw2", "Peering", "Static"),
                    _edge("peer-use1-usw2", "tgw-usw2", "Peering", "Static"),
                ],
            }
        ),
    },
    # ── 3. AWS Site-to-Site VPN at Scale ──────────────────────────────────────
    {
        "id": "tpl-aws-vpn-at-scale",
        "name": "AWS Site-to-Site VPN — Multi-Site at Scale",
        "category": "AWS / Hybrid Cloud",
        "description": (
            "Site-to-Site VPN at scale using Transit Gateway as a VPN concentrator "
            "for 20+ remote sites. Shows accelerated VPN (Global Accelerator), "
            "ECMP across tunnels, BGP dynamic routing, certificate-based auth, and "
            "VPN failover patterns. Covers both AWS-managed and customer gateway "
            "configurations. See template docs for full SOP and Mermaid diagrams."
        ),
        "tags": json.dumps(
            [
                "aws",
                "vpn",
                "site-to-site",
                "tgw",
                "transit-gateway",
                "ipsec",
                "bgp",
                "ecmp",
                "global-accelerator",
                "sop",
                "runbook",
            ]
        ),
        "graph_json": json.dumps(
            {
                "nodes": [
                    # ── TGW VPN Hub ──
                    _node(
                        "tgw",
                        "Transit Gateway (VPN Hub)",
                        "aws-tgw",
                        500,
                        300,
                        {
                            "config": {
                                "role": "VPN concentrator — all remote sites terminate here",
                                "asn": "64512",
                                "ecmp": "ECMP enabled across VPN tunnels for aggregate throughput",
                                "limits": "Up to 5,000 VPN attachments",
                            }
                        },
                    ),
                    _node(
                        "ga",
                        "Global Accelerator",
                        "aws-ga",
                        500,
                        180,
                        {
                            "config": {
                                "role": "Accelerated VPN — AWS global backbone instead of public internet",
                                "benefit": "Reduced latency, jitter, packet loss for VPN tunnels",
                                "notes": "Enable 'Accelerate' flag on VPN connection creation",
                            }
                        },
                    ),
                    # ── Hub VPCs ──
                    _node("vpc-prod", "Production VPC", "aws-vpc", 300, 120, {"config": {"cidr": "10.1.0.0/16"}}),
                    _node(
                        "vpc-shared", "Shared Services VPC", "aws-vpc", 700, 120, {"config": {"cidr": "10.0.0.0/16"}}
                    ),
                    _node("nfw", "Network Firewall", "aws-nfw", 500, 120),
                    # ── Remote Site A (Primary) ──
                    _node(
                        "cgw-a1",
                        "CGW: HQ Primary",
                        "router",
                        100,
                        460,
                        {
                            "config": {
                                "role": "Customer Gateway — Headquarters (Primary ISP)",
                                "public_ip": "203.0.113.10",
                                "bgp_asn": "65001",
                                "device": "Cisco ISR 4451 / ASR 1001-X",
                            }
                        },
                    ),
                    _node(
                        "cgw-a2",
                        "CGW: HQ Secondary",
                        "router",
                        100,
                        560,
                        {
                            "config": {
                                "role": "Customer Gateway — HQ (Secondary ISP, diverse)",
                                "public_ip": "198.51.100.20",
                                "bgp_asn": "65001",
                            }
                        },
                    ),
                    _node(
                        "vpn-a1",
                        "VPN: HQ-Primary",
                        "aws-vpn",
                        280,
                        460,
                        {
                            "config": {
                                "tunnels": "2 IPsec tunnels per VPN connection",
                                "throughput": "1.25 Gbps per tunnel (2.5 Gbps ECMP)",
                                "accelerated": "Yes (Global Accelerator)",
                            }
                        },
                    ),
                    _node(
                        "vpn-a2",
                        "VPN: HQ-Secondary",
                        "aws-vpn",
                        280,
                        560,
                        {
                            "config": {
                                "tunnels": "2 IPsec tunnels (diverse ISP path)",
                                "notes": "4 tunnels total across 2 VPN connections = 5 Gbps ECMP",
                            }
                        },
                    ),
                    # ── Remote Site B (Branch Office) ──
                    _node(
                        "cgw-b",
                        "CGW: Branch-NYC",
                        "router",
                        100,
                        700,
                        {
                            "config": {
                                "role": "Branch office — New York",
                                "public_ip": "192.0.2.50",
                                "bgp_asn": "65002",
                                "device": "Cisco ISR 1100 / Meraki MX",
                            }
                        },
                    ),
                    _node("vpn-b", "VPN: Branch-NYC", "aws-vpn", 280, 700),
                    # ── Remote Sites C-F (representing scale) ──
                    _node("cgw-c", "CGW: Branch-Chicago", "router", 500, 560, {"config": {"bgp_asn": "65003"}}),
                    _node("vpn-c", "VPN: Branch-CHI", "aws-vpn", 500, 480),
                    _node("cgw-d", "CGW: Branch-LA", "router", 700, 560, {"config": {"bgp_asn": "65004"}}),
                    _node("vpn-d", "VPN: Branch-LA", "aws-vpn", 700, 480),
                    _node("cgw-e", "CGW: Branch-London", "router", 900, 460, {"config": {"bgp_asn": "65005"}}),
                    _node("vpn-e", "VPN: Branch-LDN", "aws-vpn", 900, 380),
                    _node("cgw-f", "CGW: Branch-Tokyo", "router", 900, 560, {"config": {"bgp_asn": "65006"}}),
                    _node("vpn-f", "VPN: Branch-TKY", "aws-vpn", 900, 480),
                    # ── More sites indicator ──
                    _node(
                        "more-sites",
                        "... 15+ more sites",
                        "cloud",
                        500,
                        700,
                        {
                            "config": {
                                "notes": "Pattern repeats: CGW + VPN connection per site, all to TGW",
                            }
                        },
                    ),
                    # ── Monitoring ──
                    _node(
                        "netmgr",
                        "Network Manager",
                        "aws-netmgr",
                        300,
                        300,
                        {"config": {"role": "VPN tunnel status, reachability, route analysis"}},
                    ),
                    _node(
                        "cw",
                        "CloudWatch Alarms",
                        "aws-flowlogs",
                        700,
                        300,
                        {"config": {"role": "TunnelState, TunnelDataIn/Out, BGP status metrics"}},
                    ),
                ],
                "edges": [
                    # VPC attachments
                    _edge("vpc-prod", "tgw", "TGW Attachment", ""),
                    _edge("vpc-shared", "tgw", "TGW Attachment", ""),
                    _edge("nfw", "tgw", "Inspection", ""),
                    # Global Accelerator
                    _edge("ga", "tgw", "Accelerated path", ""),
                    # HQ (dual VPN for max throughput)
                    _edge("cgw-a1", "vpn-a1", "IPsec (2 tunnels)", "BGP"),
                    _edge("cgw-a2", "vpn-a2", "IPsec (2 tunnels)", "BGP"),
                    _edge("vpn-a1", "tgw", "VPN Attachment", "ECMP"),
                    _edge("vpn-a2", "tgw", "VPN Attachment", "ECMP"),
                    # Branch sites
                    _edge("cgw-b", "vpn-b", "IPsec", "BGP"),
                    _edge("vpn-b", "tgw", "VPN Attachment", ""),
                    _edge("cgw-c", "vpn-c", "IPsec", "BGP"),
                    _edge("vpn-c", "tgw", "VPN Attachment", ""),
                    _edge("cgw-d", "vpn-d", "IPsec", "BGP"),
                    _edge("vpn-d", "tgw", "VPN Attachment", ""),
                    _edge("cgw-e", "vpn-e", "IPsec", "BGP"),
                    _edge("vpn-e", "tgw", "VPN Attachment", ""),
                    _edge("cgw-f", "vpn-f", "IPsec", "BGP"),
                    _edge("vpn-f", "tgw", "VPN Attachment", ""),
                    # Monitoring
                    _edge("tgw", "netmgr", "Route analysis", ""),
                    _edge("tgw", "cw", "Metrics", ""),
                ],
            }
        ),
    },
    # ── 4. AWS Hybrid Connectivity — Art of the Possible ─────────────────────
    {
        "id": "tpl-aws-hybrid-art-of-possible",
        "name": "AWS Hybrid Connectivity — Art of the Possible",
        "category": "AWS / Hybrid Cloud",
        "description": (
            "Comprehensive AWS hybrid connectivity showing every gateway type working "
            "together: Transit Gateway, DX Gateway, VPN Gateway, Cloud WAN, PrivateLink, "
            "Gateway LB, inter-region TGW peering, and Global Accelerator. Demonstrates "
            "DX + VPN failover, multi-region, multi-account, centralized inspection, "
            "and third-party appliance integration. See template docs for full SOP."
        ),
        "tags": json.dumps(
            [
                "aws",
                "hybrid",
                "art-of-possible",
                "tgw",
                "dx-gateway",
                "vpn",
                "cloud-wan",
                "privatelink",
                "gwlb",
                "global-accelerator",
                "multi-region",
                "multi-account",
                "sop",
                "runbook",
            ]
        ),
        "graph_json": json.dumps(
            {
                "nodes": [
                    # ═══════════════════════════════
                    # ON-PREMISES (Left)
                    # ═══════════════════════════════
                    _node(
                        "dc-core",
                        "DC Core Router",
                        "router",
                        60,
                        400,
                        {
                            "config": {
                                "role": "Data center core — dual-homed to DX and VPN",
                                "bgp_asn": "65001",
                            }
                        },
                    ),
                    _node(
                        "branch-rtr",
                        "Branch Router",
                        "router",
                        60,
                        600,
                        {
                            "config": {
                                "role": "Branch office — VPN only (no DX)",
                                "bgp_asn": "65002",
                            }
                        },
                    ),
                    _node(
                        "partner-rtr",
                        "Partner Network",
                        "router",
                        60,
                        200,
                        {"config": {"role": "B2B partner — PrivateLink consumer"}},
                    ),
                    # ═══════════════════════════════
                    # DX + VPN LAYER
                    # ═══════════════════════════════
                    _node("dx-pri", "DX Primary (10G)", "aws-dx", 220, 350),
                    _node("dx-sec", "DX Secondary (10G)", "aws-dx", 220, 450),
                    _node(
                        "vpn-dc",
                        "VPN (DC Backup)",
                        "aws-vpn",
                        220,
                        550,
                        {
                            "config": {
                                "role": "Backup for DX — AS-PATH prepend to deprioritize",
                                "accelerated": "Yes (Global Accelerator)",
                            }
                        },
                    ),
                    _node("vpn-branch", "VPN (Branch)", "aws-vpn", 220, 650),
                    _node(
                        "ga",
                        "Global Accelerator",
                        "aws-ga",
                        220,
                        250,
                        {"config": {"role": "Accelerated VPN + anycast endpoints"}},
                    ),
                    # ═══════════════════════════════
                    # DX GATEWAY (Global)
                    # ═══════════════════════════════
                    _node(
                        "dxgw",
                        "DX Gateway",
                        "aws-dx-gw",
                        400,
                        400,
                        {
                            "config": {
                                "role": "Global DX fabric — connects DX to TGWs in any region",
                                "associations": "TGW us-east-1, TGW us-west-2, VGW ap-southeast-1",
                            }
                        },
                    ),
                    # ═══════════════════════════════
                    # REGION 1: us-east-1 (Primary)
                    # ═══════════════════════════════
                    _node(
                        "tgw-1",
                        "TGW (us-east-1)",
                        "aws-tgw",
                        600,
                        300,
                        {
                            "config": {
                                "asn": "64513",
                                "role": "Primary region hub — DX, VPN, VPC, CloudWAN, GWLB",
                            }
                        },
                    ),
                    # Cloud WAN
                    _node(
                        "cloudwan",
                        "Cloud WAN",
                        "aws-cloudwan",
                        600,
                        140,
                        {
                            "config": {
                                "role": "Global network — policy-based segmentation across regions",
                                "segments": "Production, Development, Shared Services",
                                "attachment": "TGW attachment to Cloud WAN core network",
                            }
                        },
                    ),
                    # Inspection VPC with GWLB
                    _node("vpc-inspect", "Inspection VPC", "aws-vpc", 780, 180, {"config": {"cidr": "100.64.0.0/16"}}),
                    _node(
                        "gwlb",
                        "Gateway LB",
                        "aws-gwlb",
                        900,
                        180,
                        {
                            "config": {
                                "role": "L3 bump-in-the-wire — routes traffic to 3rd-party appliances",
                                "appliances": "Palo Alto, Fortinet, Check Point (auto-scaling group)",
                                "notes": "GENEVE encapsulation, preserves src/dst IP for inspection",
                            }
                        },
                    ),
                    _node(
                        "nfw",
                        "Network Firewall",
                        "aws-nfw",
                        780,
                        100,
                        {"config": {"role": "AWS-managed stateful inspection (IDS/IPS, domain filtering)"}},
                    ),
                    # Production VPC
                    _node("vpc-prod", "Production VPC", "aws-vpc", 780, 320, {"config": {"cidr": "10.1.0.0/16"}}),
                    _node("alb-prod", "ALB", "aws-alb", 900, 280),
                    _node(
                        "ep-prod",
                        "Interface Endpoint",
                        "aws-gw-ep",
                        900,
                        360,
                        {"config": {"role": "PrivateLink endpoint — access AWS services privately"}},
                    ),
                    # Shared Services VPC
                    _node(
                        "vpc-shared", "Shared Services VPC", "aws-vpc", 780, 460, {"config": {"cidr": "10.0.0.0/16"}}
                    ),
                    _node("r53", "Route 53 Resolver", "aws-r53", 900, 420),
                    _node("ad", "Managed AD", "aws-ad", 900, 460),
                    _node("kms", "KMS", "aws-kms", 900, 500),
                    _node("ct", "CloudTrail", "aws-ct", 900, 540),
                    # Egress VPC
                    _node(
                        "vpc-egress",
                        "Egress VPC",
                        "aws-vpc",
                        600,
                        500,
                        {
                            "config": {
                                "cidr": "100.65.0.0/16",
                                "role": "Centralized internet egress — NAT GW + NFW",
                            }
                        },
                    ),
                    # ═══════════════════════════════
                    # REGION 2: us-west-2 (DR)
                    # ═══════════════════════════════
                    _node(
                        "tgw-2",
                        "TGW (us-west-2)",
                        "aws-tgw",
                        600,
                        650,
                        {
                            "config": {
                                "asn": "64514",
                                "role": "DR region — peered to primary TGW",
                            }
                        },
                    ),
                    _node("vpc-dr", "DR VPC (us-west-2)", "aws-vpc", 780, 650, {"config": {"cidr": "10.3.0.0/16"}}),
                    _node("vpc-dev", "Dev VPC (us-west-2)", "aws-vpc", 780, 730, {"config": {"cidr": "10.2.0.0/16"}}),
                    # Inter-region peering
                    _node(
                        "tgw-peer",
                        "TGW Peering",
                        "aws-tgw",
                        680,
                        480,
                        {"config": {"role": "Inter-region peering (us-east-1 <-> us-west-2)"}},
                    ),
                    # ═══════════════════════════════
                    # PRIVATELINK (B2B Partner Access)
                    # ═══════════════════════════════
                    _node(
                        "pl-service",
                        "PrivateLink Service",
                        "aws-privatelink",
                        400,
                        180,
                        {
                            "config": {
                                "role": "Expose internal service to partner via PrivateLink",
                                "nlb": "NLB behind PrivateLink endpoint service",
                                "notes": "Partner consumes via Interface Endpoint in their VPC",
                            }
                        },
                    ),
                    _node(
                        "pl-consumer",
                        "PrivateLink Consumer",
                        "aws-privatelink",
                        220,
                        140,
                        {"config": {"role": "Partner's VPC endpoint consuming the service"}},
                    ),
                    # ═══════════════════════════════
                    # MONITORING & SECURITY
                    # ═══════════════════════════════
                    _node("netmgr", "Network Manager", "aws-netmgr", 400, 80),
                    _node("guardduty", "GuardDuty", "aws-guardduty", 400, 600),
                    _node("sechub", "Security Hub", "aws-securityhub", 400, 680),
                    _node("shield", "Shield Advanced", "aws-shield", 400, 760),
                ],
                "edges": [
                    # On-prem to DX/VPN
                    _edge("dc-core", "dx-pri", "DX Primary", "802.1Q"),
                    _edge("dc-core", "dx-sec", "DX Secondary", "802.1Q"),
                    _edge("dc-core", "vpn-dc", "VPN Backup (accelerated)", "IPsec/BGP"),
                    _edge("branch-rtr", "vpn-branch", "VPN (accelerated)", "IPsec/BGP"),
                    _edge("ga", "vpn-dc", "Accelerated path", ""),
                    _edge("ga", "vpn-branch", "Accelerated path", ""),
                    # DX to DX Gateway
                    _edge("dx-pri", "dxgw", "Transit VIF (primary)", "eBGP"),
                    _edge("dx-sec", "dxgw", "Transit VIF (secondary)", "eBGP"),
                    # DX Gateway to TGWs
                    _edge("dxgw", "tgw-1", "Association (us-east-1)", "BGP"),
                    _edge("dxgw", "tgw-2", "Association (us-west-2)", "BGP"),
                    # VPN to TGW
                    _edge("vpn-dc", "tgw-1", "VPN Attachment", ""),
                    _edge("vpn-branch", "tgw-1", "VPN Attachment", ""),
                    # TGW-1 attachments
                    _edge("tgw-1", "cloudwan", "Cloud WAN Attachment", ""),
                    _edge("tgw-1", "vpc-inspect", "Inspection Attachment", ""),
                    _edge("tgw-1", "vpc-prod", "Prod Attachment", ""),
                    _edge("tgw-1", "vpc-shared", "Shared Attachment", ""),
                    _edge("tgw-1", "vpc-egress", "Egress Attachment", ""),
                    # Inspection VPC internals
                    _edge("vpc-inspect", "gwlb", "GENEVE encap", ""),
                    _edge("vpc-inspect", "nfw", "AWS NFW", ""),
                    # Prod VPC internals
                    _edge("vpc-prod", "alb-prod", "ALB", ""),
                    _edge("vpc-prod", "ep-prod", "PrivateLink EP", ""),
                    # Shared VPC internals
                    _edge("vpc-shared", "r53", "DNS", ""),
                    _edge("vpc-shared", "ad", "AD DS", ""),
                    _edge("vpc-shared", "kms", "Key Mgmt", ""),
                    _edge("vpc-shared", "ct", "Audit", ""),
                    # Inter-region peering
                    _edge("tgw-1", "tgw-peer", "Peering", "Static"),
                    _edge("tgw-peer", "tgw-2", "Peering", "Static"),
                    # TGW-2 attachments
                    _edge("tgw-2", "vpc-dr", "DR Attachment", ""),
                    _edge("tgw-2", "vpc-dev", "Dev Attachment", ""),
                    # PrivateLink (B2B partner)
                    _edge("partner-rtr", "pl-consumer", "Partner VPC EP", ""),
                    _edge("pl-consumer", "pl-service", "PrivateLink (private)", ""),
                    _edge("pl-service", "vpc-prod", "NLB -> targets", ""),
                    # Monitoring
                    _edge("netmgr", "tgw-1", "Route analysis", ""),
                    _edge("guardduty", "tgw-1", "Threat detection", ""),
                    _edge("sechub", "guardduty", "Findings", ""),
                    _edge("shield", "alb-prod", "DDoS protection", ""),
                ],
            }
        ),
    },
    # ══════════════════════════════════════════════════════════════════════════
    # Azure Virtual WAN / ExpressRoute Gateway / VPN Gateway Templates
    # ══════════════════════════════════════════════════════════════════════════
    # ── 1. Azure Virtual WAN Hub-and-Spoke ───────────────────────────────────
    {
        "id": "tpl-az-vwan-hub-spoke",
        "name": "Azure Virtual WAN — Hub-and-Spoke Multi-VNet",
        "category": "Azure / Hybrid Cloud",
        "description": (
            "Azure Virtual WAN as a managed hub connecting multiple segmented VNets "
            "(production, dev, shared services) with integrated ExpressRoute gateway, "
            "VPN gateway, Azure Firewall, and routing intent. Supports any-to-any "
            "transit, branch-to-VNet, and VNet-to-VNet via Microsoft backbone. "
            "See template docs for full SOP, Mermaid diagrams, and routing policies."
        ),
        "tags": json.dumps(
            [
                "azure",
                "vwan",
                "virtual-wan",
                "hub-spoke",
                "multi-vnet",
                "firewall",
                "routing-intent",
                "expressroute",
                "vpn",
                "sop",
                "runbook",
            ]
        ),
        "graph_json": json.dumps(
            {
                "nodes": [
                    # ── Virtual WAN + Hubs ──
                    _node(
                        "vwan",
                        "Virtual WAN",
                        "az-vwan",
                        500,
                        80,
                        {
                            "config": {
                                "role": "Global managed WAN fabric — Standard SKU",
                                "features": "Any-to-any, routing intent, inter-hub transit, BGP peering",
                            }
                        },
                    ),
                    _node(
                        "hub-east",
                        "vWAN Hub (East US)",
                        "az-vwan",
                        350,
                        200,
                        {
                            "config": {
                                "region": "East US",
                                "address_space": "10.100.0.0/23",
                                "routing_intent": "Enabled — private + internet via Azure Firewall",
                            }
                        },
                    ),
                    _node(
                        "hub-west",
                        "vWAN Hub (West US)",
                        "az-vwan",
                        650,
                        200,
                        {
                            "config": {
                                "region": "West US 2",
                                "address_space": "10.101.0.0/23",
                            }
                        },
                    ),
                    # ── Gateways in East Hub ──
                    _node(
                        "er-gw-east",
                        "ER Gateway (East)",
                        "az-er",
                        200,
                        200,
                        {
                            "config": {
                                "sku": "ErGwScale (auto-scale up to 40 Gbps)",
                                "role": "ExpressRoute termination in vWAN hub",
                            }
                        },
                    ),
                    _node(
                        "vpn-gw-east",
                        "VPN Gateway (East)",
                        "az-vpn-gw",
                        200,
                        300,
                        {
                            "config": {
                                "sku": "Up to 20 Gbps aggregate",
                                "tunnels": "S2S VPN to branches, BGP over IPsec",
                            }
                        },
                    ),
                    _node(
                        "az-fw-east",
                        "Azure Firewall (East)",
                        "az-fw",
                        350,
                        320,
                        {
                            "config": {
                                "sku": "Premium",
                                "features": "TLS inspection, IDPS, URL filtering, web categories",
                                "role": "Routing intent — all private + internet traffic inspected",
                            }
                        },
                    ),
                    # ── Gateways in West Hub ──
                    _node("er-gw-west", "ER Gateway (West)", "az-er", 800, 200),
                    _node("vpn-gw-west", "VPN Gateway (West)", "az-vpn-gw", 800, 300),
                    _node("az-fw-west", "Azure Firewall (West)", "az-fw", 650, 320),
                    # ── Spoke VNets (East) ──
                    _node(
                        "vnet-prod",
                        "Prod VNet (East)",
                        "az-vnet",
                        200,
                        460,
                        {"config": {"cidr": "10.1.0.0/16", "peering": "vWAN hub connection"}},
                    ),
                    _node(
                        "vnet-shared",
                        "Shared Services VNet",
                        "az-vnet",
                        350,
                        460,
                        {
                            "config": {
                                "cidr": "10.0.0.0/16",
                                "services": "DNS Private Resolver, Managed AD, Key Vault",
                            }
                        },
                    ),
                    _node("vnet-dev", "Dev VNet (East)", "az-vnet", 500, 460, {"config": {"cidr": "10.2.0.0/16"}}),
                    # ── Spoke VNets (West) ──
                    _node("vnet-dr", "DR VNet (West)", "az-vnet", 650, 460, {"config": {"cidr": "10.3.0.0/16"}}),
                    _node("vnet-dmz", "DMZ VNet (West)", "az-vnet", 800, 460, {"config": {"cidr": "10.4.0.0/16"}}),
                    # ── Shared Services Detail ──
                    _node("dns", "DNS Private Resolver", "az-dns", 280, 560),
                    _node("ad", "Managed AD (AAD DS)", "az-entra", 400, 560),
                    _node("kv", "Key Vault", "az-keyvault", 520, 560),
                    # ── On-Premises ──
                    _node(
                        "on-prem-dc",
                        "On-Prem DC",
                        "router",
                        60,
                        250,
                        {"config": {"role": "ExpressRoute + VPN backup", "bgp_asn": "65001"}},
                    ),
                    _node("branch-1", "Branch Office 1", "router", 60, 380),
                    _node("branch-2", "Branch Office 2", "router", 60, 460),
                    # ── Monitoring ──
                    _node("sentinel", "Microsoft Sentinel", "az-sentinel", 500, 80),
                    _node("netwatcher", "Network Watcher", "az-netwatcher", 350, 80),
                ],
                "edges": [
                    # vWAN to hubs
                    _edge("vwan", "hub-east", "Hub (East US)", ""),
                    _edge("vwan", "hub-west", "Hub (West US 2)", ""),
                    _edge("hub-east", "hub-west", "Inter-hub transit", "Microsoft backbone"),
                    # Gateways
                    _edge("er-gw-east", "hub-east", "ER Gateway", ""),
                    _edge("vpn-gw-east", "hub-east", "VPN Gateway", ""),
                    _edge("az-fw-east", "hub-east", "Routing Intent", ""),
                    _edge("er-gw-west", "hub-west", "ER Gateway", ""),
                    _edge("vpn-gw-west", "hub-west", "VPN Gateway", ""),
                    _edge("az-fw-west", "hub-west", "Routing Intent", ""),
                    # Spoke connections
                    _edge("hub-east", "vnet-prod", "VNet Connection", ""),
                    _edge("hub-east", "vnet-shared", "VNet Connection", ""),
                    _edge("hub-east", "vnet-dev", "VNet Connection", ""),
                    _edge("hub-west", "vnet-dr", "VNet Connection", ""),
                    _edge("hub-west", "vnet-dmz", "VNet Connection", ""),
                    # Shared services
                    _edge("vnet-shared", "dns", "DNS", ""),
                    _edge("vnet-shared", "ad", "AD DS", ""),
                    _edge("vnet-shared", "kv", "Key Vault", ""),
                    # On-prem
                    _edge("on-prem-dc", "er-gw-east", "ExpressRoute", "eBGP (ASN 12076)"),
                    _edge("on-prem-dc", "vpn-gw-east", "VPN Backup", "IPsec/BGP"),
                    _edge("branch-1", "vpn-gw-east", "S2S VPN", "IPsec/BGP"),
                    _edge("branch-2", "vpn-gw-west", "S2S VPN", "IPsec/BGP"),
                    # Monitoring
                    _edge("sentinel", "vwan", "SIEM", ""),
                    _edge("netwatcher", "hub-east", "Flow logs", ""),
                ],
            }
        ),
    },
    # ── 2. Azure ExpressRoute Gateway — Multi-Region Global Reach ────────────
    {
        "id": "tpl-az-er-multi-region",
        "name": "Azure ExpressRoute Gateway — Multi-Region + Global Reach",
        "category": "Azure / Hybrid Cloud",
        "description": (
            "ExpressRoute with Global Reach enabling on-premises-to-on-premises "
            "transit via Microsoft backbone. Shows dual ER circuits (primary/secondary), "
            "ExpressRoute Direct (MACsec), multi-region with vWAN hub ER gateways, "
            "FastPath for latency-sensitive workloads, and ER + VPN coexistence. "
            "See template docs for full SOP and Mermaid diagrams."
        ),
        "tags": json.dumps(
            [
                "azure",
                "expressroute",
                "global-reach",
                "multi-region",
                "er-direct",
                "macsec",
                "fastpath",
                "bgp",
                "sop",
                "runbook",
            ]
        ),
        "graph_json": json.dumps(
            {
                "nodes": [
                    # On-Prem Sites
                    _node(
                        "dc-east",
                        "DC East (Primary)",
                        "router",
                        60,
                        250,
                        {"config": {"bgp_asn": "65001", "location": "Equinix DC (Ashburn)"}},
                    ),
                    _node(
                        "dc-west",
                        "DC West (Diverse)",
                        "router",
                        60,
                        450,
                        {"config": {"bgp_asn": "65001", "location": "CoreSite (Santa Clara)"}},
                    ),
                    _node(
                        "dc-europe",
                        "DC Europe",
                        "router",
                        60,
                        650,
                        {"config": {"bgp_asn": "65002", "location": "Equinix LD5 (London)"}},
                    ),
                    # ER Circuits
                    _node(
                        "er-pri",
                        "ER Circuit (Primary)",
                        "az-er",
                        250,
                        250,
                        {
                            "config": {
                                "bandwidth": "10 Gbps",
                                "provider": "Equinix / Megaport",
                                "sku": "Premium (global route access)",
                            }
                        },
                    ),
                    _node(
                        "er-sec",
                        "ER Circuit (Secondary)",
                        "az-er",
                        250,
                        450,
                        {
                            "config": {
                                "bandwidth": "10 Gbps",
                                "provider": "CoreSite / AT&T",
                                "diversity": "Different provider + peering location",
                            }
                        },
                    ),
                    _node(
                        "er-direct",
                        "ER Direct (100G)",
                        "az-er",
                        250,
                        100,
                        {
                            "config": {
                                "bandwidth": "100 Gbps",
                                "macsec": "MACsec 256-bit AES-GCM (ER Direct only)",
                                "notes": "Direct port at Microsoft peering edge — no provider",
                            }
                        },
                    ),
                    _node("er-europe", "ER Circuit (Europe)", "az-er", 250, 650),
                    # Global Reach
                    _node(
                        "global-reach",
                        "ExpressRoute Global Reach",
                        "az-er-global",
                        400,
                        450,
                        {
                            "config": {
                                "role": "On-prem to on-prem transit via Microsoft backbone",
                                "path": "DC East <-> Microsoft backbone <-> DC Europe",
                                "notes": "No VNet traversal — direct on-prem-to-on-prem at MSEE",
                            }
                        },
                    ),
                    # MSEE (Microsoft Edge)
                    _node(
                        "msee-east",
                        "MSEE (East US)",
                        "router",
                        400,
                        250,
                        {"config": {"owner": "Microsoft", "bgp_asn": "12076"}},
                    ),
                    _node(
                        "msee-west",
                        "MSEE (West US)",
                        "router",
                        400,
                        350,
                        {"config": {"owner": "Microsoft", "bgp_asn": "12076"}},
                    ),
                    # Region 1: East US
                    _node(
                        "gw-east",
                        "ER Gateway (East US)",
                        "az-er",
                        600,
                        200,
                        {
                            "config": {
                                "sku": "ErGw3Az (Ultra Performance, zone-redundant)",
                                "fastpath": "Enabled — bypasses gateway for data plane (latency)",
                            }
                        },
                    ),
                    _node(
                        "vnet-prod-east", "Prod VNet (East)", "az-vnet", 760, 150, {"config": {"cidr": "10.1.0.0/16"}}
                    ),
                    _node(
                        "vnet-shared-east",
                        "Shared VNet (East)",
                        "az-vnet",
                        760,
                        250,
                        {"config": {"cidr": "10.0.0.0/16"}},
                    ),
                    # Region 2: West US
                    _node(
                        "gw-west",
                        "ER Gateway (West US)",
                        "az-er",
                        600,
                        400,
                        {"config": {"sku": "ErGw2Az (High Performance, zone-redundant)"}},
                    ),
                    _node(
                        "vnet-prod-west", "Prod VNet (West)", "az-vnet", 760, 350, {"config": {"cidr": "10.3.0.0/16"}}
                    ),
                    _node("vnet-dr-west", "DR VNet (West)", "az-vnet", 760, 450, {"config": {"cidr": "10.4.0.0/16"}}),
                    # Region 3: UK South
                    _node("gw-uk", "ER Gateway (UK South)", "az-er", 600, 600),
                    _node("vnet-eu", "EU VNet (UK South)", "az-vnet", 760, 600, {"config": {"cidr": "10.5.0.0/16"}}),
                    # VPN Backup
                    _node(
                        "vpn-backup",
                        "VPN Gateway (Backup)",
                        "az-vpn-gw",
                        600,
                        100,
                        {
                            "config": {
                                "role": "IPsec VPN backup for ER failure",
                                "sku": "VpnGw2AZ",
                                "coexistence": "ER + VPN on same VNet gateway subnet",
                            }
                        },
                    ),
                    # Monitoring
                    _node("monitor", "Azure Monitor", "az-monitor", 500, 700),
                    _node("netwatcher", "Network Watcher", "az-netwatcher", 650, 700),
                ],
                "edges": [
                    # DC to ER circuits
                    _edge("dc-east", "er-pri", "ER Primary", "BGP (ASN 12076)"),
                    _edge("dc-east", "er-direct", "ER Direct (100G)", "MACsec"),
                    _edge("dc-west", "er-sec", "ER Secondary (diverse)", "BGP"),
                    _edge("dc-europe", "er-europe", "ER Europe", "BGP"),
                    # ER to MSEE
                    _edge("er-pri", "msee-east", "Private Peering", ""),
                    _edge("er-direct", "msee-east", "Direct Peering", ""),
                    _edge("er-sec", "msee-west", "Private Peering", ""),
                    _edge("er-europe", "global-reach", "Global Reach leg", ""),
                    _edge("er-pri", "global-reach", "Global Reach leg", ""),
                    # MSEE to ER Gateways
                    _edge("msee-east", "gw-east", "ER Connection", ""),
                    _edge("msee-west", "gw-west", "ER Connection", ""),
                    # ER Gateways to VNets
                    _edge("gw-east", "vnet-prod-east", "Gateway Connection", ""),
                    _edge("gw-east", "vnet-shared-east", "Gateway Connection", ""),
                    _edge("gw-west", "vnet-prod-west", "Gateway Connection", ""),
                    _edge("gw-west", "vnet-dr-west", "Gateway Connection", ""),
                    _edge("gw-uk", "vnet-eu", "Gateway Connection", ""),
                    # ER Circuit to UK gateway
                    _edge("er-europe", "gw-uk", "ER Connection", ""),
                    # VPN backup
                    _edge("dc-east", "vpn-backup", "IPsec VPN (backup)", "BGP"),
                    _edge("vpn-backup", "vnet-prod-east", "VPN Connection", ""),
                    # Monitoring
                    _edge("monitor", "gw-east", "ER metrics", ""),
                    _edge("netwatcher", "gw-east", "Connection monitor", ""),
                ],
            }
        ),
    },
    # ── 3. Azure VPN Gateway — Multi-Site at Scale ───────────────────────────
    {
        "id": "tpl-az-vpn-at-scale",
        "name": "Azure VPN Gateway — Multi-Site at Scale",
        "category": "Azure / Hybrid Cloud",
        "description": (
            "Azure VPN Gateway at scale using Virtual WAN for 100+ branch sites. "
            "Shows S2S VPN with BGP, P2S VPN for remote users (OpenVPN/IKEv2), "
            "active-active gateways for 99.99% SLA, NAT rules for overlapping "
            "CIDRs, and VPN over ExpressRoute private peering for double encryption. "
            "See template docs for full SOP and Mermaid diagrams."
        ),
        "tags": json.dumps(
            [
                "azure",
                "vpn-gateway",
                "site-to-site",
                "point-to-site",
                "vwan",
                "bgp",
                "active-active",
                "nat-rules",
                "sop",
                "runbook",
            ]
        ),
        "graph_json": json.dumps(
            {
                "nodes": [
                    # vWAN Hub
                    _node(
                        "vwan-hub",
                        "vWAN Hub (East US)",
                        "az-vwan",
                        500,
                        250,
                        {
                            "config": {
                                "role": "Managed hub — integrated VPN + ER + Firewall",
                                "vpn_scale": "Up to 20 Gbps aggregate S2S VPN",
                            }
                        },
                    ),
                    _node(
                        "vpn-gw",
                        "VPN Gateway (vWAN)",
                        "az-vpn-gw",
                        350,
                        250,
                        {
                            "config": {
                                "sku": "Scale units (1 unit = 500 Mbps, max 20 Gbps)",
                                "mode": "Active-Active (99.99% SLA)",
                                "bgp_asn": "65515 (Azure default for vWAN)",
                            }
                        },
                    ),
                    _node(
                        "az-fw",
                        "Azure Firewall",
                        "az-fw",
                        500,
                        160,
                        {"config": {"sku": "Premium", "routing_intent": "Enabled"}},
                    ),
                    _node(
                        "er-gw",
                        "ER Gateway (coexist)",
                        "az-er",
                        650,
                        250,
                        {"config": {"role": "ER + VPN coexistence on same hub"}},
                    ),
                    # P2S (Remote Users)
                    _node(
                        "p2s",
                        "P2S VPN Gateway",
                        "az-vpn-gw",
                        500,
                        60,
                        {
                            "config": {
                                "role": "Point-to-Site VPN for remote/mobile users",
                                "protocols": "OpenVPN, IKEv2",
                                "auth": "Azure AD (Entra ID) / Certificate / RADIUS",
                                "clients": "Up to 10,000 concurrent P2S connections",
                            }
                        },
                    ),
                    _node("remote-users", "Remote Users", "endpoint-pc", 500, -20),
                    # Spoke VNets
                    _node("vnet-prod", "Prod VNet", "az-vnet", 350, 420, {"config": {"cidr": "10.1.0.0/16"}}),
                    _node("vnet-shared", "Shared VNet", "az-vnet", 500, 420, {"config": {"cidr": "10.0.0.0/16"}}),
                    _node("vnet-dev", "Dev VNet", "az-vnet", 650, 420, {"config": {"cidr": "10.2.0.0/16"}}),
                    # Branch Sites (S2S VPN)
                    _node(
                        "hq",
                        "HQ (Active-Active)",
                        "router",
                        60,
                        160,
                        {
                            "config": {
                                "bgp_asn": "65001",
                                "vpn": "2 S2S connections (active-active) = 4 tunnels",
                            }
                        },
                    ),
                    _node("branch-a", "Branch A", "router", 60, 280),
                    _node("branch-b", "Branch B", "router", 60, 360),
                    _node(
                        "branch-c",
                        "Branch C (NAT)",
                        "router",
                        60,
                        440,
                        {
                            "config": {
                                "cidr": "10.1.0.0/16 (overlaps with Prod!)",
                                "notes": "VPN NAT rules translate to 10.200.0.0/16",
                            }
                        },
                    ),
                    _node("branch-d", "Branch D", "router", 160, 500),
                    _node("branch-e", "Branch E", "router", 260, 500),
                    _node("more-branches", "... 95+ more branches", "cloud", 60, 540),
                    # VPN over ER
                    _node(
                        "vpn-over-er",
                        "VPN over ER",
                        "az-vpn-gw",
                        760,
                        160,
                        {
                            "config": {
                                "role": "IPsec over ExpressRoute private peering (double encryption)",
                                "notes": "Customer CE tunnel endpoint to VPN GW via ER private peering",
                            }
                        },
                    ),
                    _node("on-prem-dc", "On-Prem DC", "router", 900, 250, {"config": {"bgp_asn": "65001"}}),
                ],
                "edges": [
                    # Hub internals
                    _edge("vpn-gw", "vwan-hub", "VPN Gateway", ""),
                    _edge("az-fw", "vwan-hub", "Routing Intent", ""),
                    _edge("er-gw", "vwan-hub", "ER Gateway", ""),
                    _edge("p2s", "vwan-hub", "P2S Gateway", ""),
                    _edge("remote-users", "p2s", "OpenVPN / IKEv2", ""),
                    # Spoke connections
                    _edge("vwan-hub", "vnet-prod", "VNet Connection", ""),
                    _edge("vwan-hub", "vnet-shared", "VNet Connection", ""),
                    _edge("vwan-hub", "vnet-dev", "VNet Connection", ""),
                    # Branch VPN connections
                    _edge("hq", "vpn-gw", "S2S VPN (active-active)", "BGP"),
                    _edge("branch-a", "vpn-gw", "S2S VPN", "BGP"),
                    _edge("branch-b", "vpn-gw", "S2S VPN", "BGP"),
                    _edge("branch-c", "vpn-gw", "S2S VPN + NAT rules", "BGP"),
                    _edge("branch-d", "vpn-gw", "S2S VPN", "BGP"),
                    _edge("branch-e", "vpn-gw", "S2S VPN", "BGP"),
                    # VPN over ER (double encryption)
                    _edge("on-prem-dc", "er-gw", "ExpressRoute", "BGP"),
                    _edge("on-prem-dc", "vpn-over-er", "IPsec over ER", "BGP"),
                    _edge("vpn-over-er", "vnet-prod", "Double encrypted path", ""),
                ],
            }
        ),
    },
    # ── 4. Azure Hybrid Connectivity — Art of the Possible ───────────────────
    {
        "id": "tpl-az-hybrid-art-of-possible",
        "name": "Azure Hybrid Connectivity — Art of the Possible",
        "category": "Azure / Hybrid Cloud",
        "description": (
            "Comprehensive Azure hybrid connectivity showing every service: Virtual WAN "
            "(dual-hub), ExpressRoute + Global Reach, VPN Gateway (S2S + P2S), Azure "
            "Firewall with routing intent, Private Link, Application Gateway + WAF, "
            "Front Door, DDoS Protection, Network Watcher, and Sentinel SIEM. "
            "See template docs for full SOP, gateway comparison, and traffic flows."
        ),
        "tags": json.dumps(
            [
                "azure",
                "hybrid",
                "art-of-possible",
                "vwan",
                "expressroute",
                "vpn",
                "firewall",
                "private-link",
                "front-door",
                "sentinel",
                "multi-region",
                "sop",
                "runbook",
            ]
        ),
        "graph_json": json.dumps(
            {
                "nodes": [
                    # On-Prem
                    _node("dc", "Data Center", "router", 60, 350, {"config": {"bgp_asn": "65001"}}),
                    _node("branch", "Branch", "router", 60, 500),
                    _node("partner", "B2B Partner", "router", 60, 200),
                    _node("remote", "Remote Users", "endpoint-pc", 60, 100),
                    # Connectivity
                    _node("er-pri", "ER Primary", "az-er", 220, 300),
                    _node("er-sec", "ER Secondary", "az-er", 220, 400),
                    _node("vpn-s2s", "S2S VPN", "az-vpn-gw", 220, 500),
                    _node("p2s", "P2S VPN", "az-vpn-gw", 220, 100),
                    _node("global-reach", "Global Reach", "az-er-global", 220, 200),
                    # vWAN
                    _node("vwan", "Virtual WAN", "az-vwan", 420, 100),
                    _node("hub-east", "Hub (East US)", "az-vwan", 420, 250),
                    _node("hub-west", "Hub (West US)", "az-vwan", 420, 450),
                    # Firewalls
                    _node("fw-east", "Azure FW (East)", "az-fw", 420, 340),
                    _node("fw-west", "Azure FW (West)", "az-fw", 420, 540),
                    # Region 1 VNets
                    _node("vnet-prod", "Prod VNet", "az-vnet", 620, 180, {"config": {"cidr": "10.1.0.0/16"}}),
                    _node("vnet-shared", "Shared VNet", "az-vnet", 620, 280, {"config": {"cidr": "10.0.0.0/16"}}),
                    _node("vnet-dmz", "DMZ VNet", "az-vnet", 620, 380),
                    # Region 2 VNets
                    _node("vnet-dr", "DR VNet", "az-vnet", 620, 480, {"config": {"cidr": "10.3.0.0/16"}}),
                    _node("vnet-dev", "Dev VNet", "az-vnet", 620, 560),
                    # Application Layer
                    _node(
                        "front-door",
                        "Azure Front Door",
                        "az-front",
                        800,
                        100,
                        {"config": {"role": "Global L7 LB — WAF, SSL offload, CDN"}},
                    ),
                    _node(
                        "appgw",
                        "App Gateway + WAF",
                        "az-appgw",
                        800,
                        200,
                        {"config": {"role": "Regional L7 LB — WAF v2, SSL termination"}},
                    ),
                    _node(
                        "pl-svc",
                        "Private Link Service",
                        "az-privatelink",
                        800,
                        300,
                        {"config": {"role": "Expose service to partner privately"}},
                    ),
                    # Security
                    _node("ddos", "DDoS Protection", "az-ddos", 800, 400),
                    _node("sentinel", "Microsoft Sentinel", "az-sentinel", 800, 480),
                    _node("defender", "Defender for Cloud", "az-defender", 800, 560),
                    _node("entra", "Entra ID (SSO)", "az-entra", 220, 40),
                ],
                "edges": [
                    # On-prem connectivity
                    _edge("dc", "er-pri", "ER Primary", "BGP"),
                    _edge("dc", "er-sec", "ER Secondary (diverse)", "BGP"),
                    _edge("dc", "vpn-s2s", "VPN Backup", "IPsec/BGP"),
                    _edge("branch", "vpn-s2s", "S2S VPN", "IPsec/BGP"),
                    _edge("remote", "p2s", "P2S VPN (OpenVPN)", ""),
                    _edge("remote", "entra", "SSO Auth", ""),
                    _edge("partner", "global-reach", "On-prem transit", ""),
                    # Connectivity to hubs
                    _edge("er-pri", "hub-east", "ER Connection", ""),
                    _edge("er-sec", "hub-west", "ER Connection (diverse)", ""),
                    _edge("vpn-s2s", "hub-east", "VPN Connection", ""),
                    _edge("p2s", "hub-east", "P2S Connection", ""),
                    _edge("global-reach", "er-pri", "Global Reach", ""),
                    # vWAN
                    _edge("vwan", "hub-east", "Hub", ""),
                    _edge("vwan", "hub-west", "Hub", ""),
                    _edge("hub-east", "hub-west", "Inter-hub transit", ""),
                    _edge("fw-east", "hub-east", "Routing Intent", ""),
                    _edge("fw-west", "hub-west", "Routing Intent", ""),
                    # Spoke VNets
                    _edge("hub-east", "vnet-prod", "", ""),
                    _edge("hub-east", "vnet-shared", "", ""),
                    _edge("hub-east", "vnet-dmz", "", ""),
                    _edge("hub-west", "vnet-dr", "", ""),
                    _edge("hub-west", "vnet-dev", "", ""),
                    # Application layer
                    _edge("front-door", "appgw", "Origin", ""),
                    _edge("appgw", "vnet-prod", "Backend pool", ""),
                    _edge("pl-svc", "vnet-prod", "NLB -> targets", ""),
                    _edge("partner", "pl-svc", "Private Endpoint", ""),
                    # Security
                    _edge("ddos", "vnet-prod", "DDoS plan", ""),
                    _edge("sentinel", "fw-east", "Log Analytics", ""),
                    _edge("defender", "vnet-prod", "CSPM", ""),
                ],
            }
        ),
    },
    # ══════════════════════════════════════════════════════════════════════════
    # GCP Cloud Router / NCC / Interconnect / VPN Templates
    # ══════════════════════════════════════════════════════════════════════════
    # ── 1. GCP Network Connectivity Center (NCC) Hub-and-Spoke ───────────────
    {
        "id": "tpl-gcp-ncc-hub-spoke",
        "name": "GCP Network Connectivity Center — Hub-and-Spoke",
        "category": "GCP / Hybrid Cloud",
        "description": (
            "Network Connectivity Center (NCC) as a centralized hub for multi-VPC, "
            "hybrid, and multi-cloud connectivity. Shows hub-and-spoke with VLAN "
            "attachments (Interconnect), HA VPN spokes, Router appliance spokes, "
            "and VPC spokes with Cloud Router BGP. Includes Private Service Connect "
            "for service consumption. See template docs for full SOP."
        ),
        "tags": json.dumps(
            [
                "gcp",
                "ncc",
                "network-connectivity-center",
                "hub-spoke",
                "cloud-router",
                "ha-vpn",
                "interconnect",
                "bgp",
                "sop",
                "runbook",
            ]
        ),
        "graph_json": json.dumps(
            {
                "nodes": [
                    # NCC Hub
                    _node(
                        "ncc",
                        "Network Connectivity Center",
                        "gcp-ncc",
                        500,
                        250,
                        {
                            "config": {
                                "role": "Global hub — any-to-any transit for Interconnect, VPN, and VPC spokes",
                                "features": "Site-to-site data transfer, spoke-to-spoke routing",
                                "routing": "BGP via Cloud Router at each spoke",
                            }
                        },
                    ),
                    # Cloud Routers
                    _node(
                        "cr-east",
                        "Cloud Router (us-east1)",
                        "gcp-router",
                        350,
                        180,
                        {"config": {"region": "us-east1", "bgp_asn": "16550 (Google)"}},
                    ),
                    _node(
                        "cr-west", "Cloud Router (us-west1)", "gcp-router", 650, 180, {"config": {"region": "us-west1"}}
                    ),
                    _node("cr-eu", "Cloud Router (europe-west1)", "gcp-router", 500, 120),
                    # Interconnect Spokes
                    _node(
                        "ic-pri",
                        "Dedicated Interconnect (Primary)",
                        "gcp-ic",
                        180,
                        250,
                        {
                            "config": {
                                "speed": "10 Gbps",
                                "vlan_attach": "VLAN Attachment -> Cloud Router -> NCC spoke",
                            }
                        },
                    ),
                    _node("ic-sec", "Dedicated Interconnect (Secondary)", "gcp-ic", 180, 350),
                    # HA VPN Spokes
                    _node(
                        "vpn-east",
                        "HA VPN (us-east1)",
                        "gcp-vpn",
                        350,
                        400,
                        {
                            "config": {
                                "tunnels": "4 tunnels (2 interfaces x 2 peers) = 99.99% SLA",
                                "throughput": "3 Gbps per tunnel",
                            }
                        },
                    ),
                    _node("vpn-west", "HA VPN (us-west1)", "gcp-vpn", 650, 400),
                    # VPC Spokes
                    _node(
                        "vpc-prod",
                        "Prod VPC (us-east1)",
                        "gcp-vpc",
                        350,
                        520,
                        {"config": {"cidr": "10.1.0.0/16", "spoke_type": "VPC spoke"}},
                    ),
                    _node(
                        "vpc-shared", "Shared Services VPC", "gcp-vpc", 500, 520, {"config": {"cidr": "10.0.0.0/16"}}
                    ),
                    _node("vpc-dev", "Dev VPC (us-west1)", "gcp-vpc", 650, 520, {"config": {"cidr": "10.2.0.0/16"}}),
                    # Private Service Connect
                    _node(
                        "psc-prod",
                        "PSC Producer",
                        "gcp-psc",
                        350,
                        620,
                        {"config": {"role": "Expose internal service via Private Service Connect"}},
                    ),
                    _node(
                        "psc-consumer",
                        "PSC Consumer",
                        "gcp-psc",
                        500,
                        620,
                        {"config": {"role": "Consume Google APIs or partner services privately"}},
                    ),
                    # Security
                    _node("armor", "Cloud Armor", "gcp-armor", 700, 520),
                    _node("scc", "Security Command Center", "gcp-scc", 700, 620),
                    _node("fw-policy", "Hierarchical FW Policy", "gcp-fw", 500, 420),
                    # On-Prem
                    _node("dc", "On-Prem DC", "router", 60, 300, {"config": {"bgp_asn": "65001"}}),
                    _node("branch", "Branch Office", "router", 60, 450),
                ],
                "edges": [
                    # NCC hub connections
                    _edge("cr-east", "ncc", "NCC Spoke (Interconnect)", ""),
                    _edge("cr-west", "ncc", "NCC Spoke (VPN)", ""),
                    _edge("cr-eu", "ncc", "NCC Spoke (VPC)", ""),
                    # Interconnect
                    _edge("dc", "ic-pri", "Interconnect Primary", "802.1Q"),
                    _edge("dc", "ic-sec", "Interconnect Secondary", "802.1Q"),
                    _edge("ic-pri", "cr-east", "VLAN Attachment", "eBGP"),
                    _edge("ic-sec", "cr-east", "VLAN Attachment", "eBGP"),
                    # HA VPN
                    _edge("branch", "vpn-east", "HA VPN (4 tunnels)", "BGP"),
                    _edge("dc", "vpn-west", "HA VPN (backup)", "BGP"),
                    _edge("vpn-east", "cr-east", "VPN Spoke", ""),
                    _edge("vpn-west", "cr-west", "VPN Spoke", ""),
                    # VPC spokes
                    _edge("ncc", "vpc-prod", "VPC Spoke", ""),
                    _edge("ncc", "vpc-shared", "VPC Spoke", ""),
                    _edge("ncc", "vpc-dev", "VPC Spoke", ""),
                    # PSC
                    _edge("vpc-prod", "psc-prod", "Service Attachment", ""),
                    _edge("vpc-shared", "psc-consumer", "PSC Endpoint", ""),
                    # Security
                    _edge("armor", "vpc-prod", "WAF/DDoS", ""),
                    _edge("scc", "vpc-prod", "Findings", ""),
                    _edge("fw-policy", "ncc", "Org policy", ""),
                ],
            }
        ),
    },
    # ── 2. GCP Hybrid Connectivity — Art of the Possible ─────────────────────
    {
        "id": "tpl-gcp-hybrid-art-of-possible",
        "name": "GCP Hybrid Connectivity — Art of the Possible",
        "category": "GCP / Hybrid Cloud",
        "description": (
            "Comprehensive GCP hybrid connectivity showing every service: Network "
            "Connectivity Center (NCC), Dedicated + Partner Interconnect, HA VPN, "
            "Cloud Router, Shared VPC, Private Service Connect, Cloud NAT, Cloud "
            "Armor, hierarchical firewall policies, and Assured Workloads. "
            "See template docs for full SOP, comparison matrix, and traffic flows."
        ),
        "tags": json.dumps(
            [
                "gcp",
                "hybrid",
                "art-of-possible",
                "ncc",
                "interconnect",
                "ha-vpn",
                "cloud-router",
                "shared-vpc",
                "psc",
                "assured-workloads",
                "sop",
                "runbook",
            ]
        ),
        "graph_json": json.dumps(
            {
                "nodes": [
                    # On-Prem
                    _node("dc", "Data Center", "router", 60, 300, {"config": {"bgp_asn": "65001"}}),
                    _node("branch", "Branch Office", "router", 60, 500),
                    _node("partner", "B2B Partner", "router", 60, 150),
                    # Connectivity Layer
                    _node("ic-ded", "Dedicated Interconnect (10G)", "gcp-ic", 220, 250),
                    _node("ic-part", "Partner Interconnect", "gcp-ic", 220, 350),
                    _node("vpn-ha", "HA VPN", "gcp-vpn", 220, 500),
                    # NCC Hub
                    _node(
                        "ncc",
                        "NCC Hub",
                        "gcp-ncc",
                        420,
                        300,
                        {"config": {"role": "Global connectivity hub — all spokes"}},
                    ),
                    # Cloud Routers
                    _node("cr-1", "Cloud Router (us-east1)", "gcp-router", 420, 200),
                    _node("cr-2", "Cloud Router (us-west1)", "gcp-router", 420, 400),
                    # Host/Shared VPC
                    _node(
                        "vpc-host",
                        "Shared VPC (Host)",
                        "gcp-vpc",
                        620,
                        150,
                        {
                            "config": {
                                "role": "Centralized networking — subnets shared to service projects",
                                "cidr": "10.0.0.0/8 (aggregated)",
                            }
                        },
                    ),
                    _node("vpc-prod", "Prod (Service Project)", "gcp-vpc", 780, 100),
                    _node("vpc-dev", "Dev (Service Project)", "gcp-vpc", 780, 200),
                    _node("vpc-data", "Data (Service Project)", "gcp-vpc", 780, 300),
                    # Standalone VPC
                    _node("vpc-dmz", "DMZ VPC (us-east1)", "gcp-vpc", 620, 400),
                    _node("vpc-dr", "DR VPC (us-west1)", "gcp-vpc", 620, 500),
                    # Security
                    _node("fw-org", "Org Firewall Policy", "gcp-fw", 420, 100),
                    _node("armor", "Cloud Armor", "gcp-armor", 780, 400),
                    _node("scc", "Security Command Center", "gcp-scc", 780, 500),
                    # Services
                    _node(
                        "psc",
                        "Private Service Connect",
                        "gcp-psc",
                        620,
                        600,
                        {"config": {"role": "Private access to Google APIs + partner services"}},
                    ),
                    _node("nat", "Cloud NAT", "gcp-nat", 620, 300),
                    _node("lb", "Global External LB", "gcp-lb", 620, 50),
                    _node("dns", "Cloud DNS", "gcp-dns", 780, 600),
                    # Assured Workloads
                    _node(
                        "assured",
                        "Assured Workloads",
                        "gcp-assured",
                        420,
                        600,
                        {"config": {"role": "IL4/IL5 compliance boundary — US data residency"}},
                    ),
                ],
                "edges": [
                    # On-prem connectivity
                    _edge("dc", "ic-ded", "Dedicated IC (10G)", "802.1Q"),
                    _edge("dc", "ic-part", "Partner IC (backup)", ""),
                    _edge("branch", "vpn-ha", "HA VPN (4 tunnels)", "IPsec/BGP"),
                    _edge("partner", "psc", "PSC Endpoint (B2B)", ""),
                    # To Cloud Routers / NCC
                    _edge("ic-ded", "cr-1", "VLAN Attachment", "eBGP"),
                    _edge("ic-part", "cr-1", "VLAN Attachment", "eBGP"),
                    _edge("vpn-ha", "cr-2", "VPN Tunnel", "BGP"),
                    _edge("cr-1", "ncc", "NCC Spoke", ""),
                    _edge("cr-2", "ncc", "NCC Spoke", ""),
                    # NCC to VPCs
                    _edge("ncc", "vpc-host", "VPC Spoke", ""),
                    _edge("ncc", "vpc-dmz", "VPC Spoke", ""),
                    _edge("ncc", "vpc-dr", "VPC Spoke", ""),
                    # Shared VPC
                    _edge("vpc-host", "vpc-prod", "Shared Subnet", ""),
                    _edge("vpc-host", "vpc-dev", "Shared Subnet", ""),
                    _edge("vpc-host", "vpc-data", "Shared Subnet", ""),
                    # Services
                    _edge("lb", "vpc-prod", "Backend service", ""),
                    _edge("armor", "lb", "WAF/DDoS", ""),
                    _edge("nat", "vpc-host", "Egress NAT", ""),
                    _edge("psc", "vpc-prod", "PSC Attachment", ""),
                    _edge("dns", "vpc-host", "Private zones", ""),
                    # Security
                    _edge("fw-org", "ncc", "Org-level rules", ""),
                    _edge("scc", "vpc-prod", "Findings", ""),
                    _edge("assured", "vpc-prod", "Compliance boundary", ""),
                ],
            }
        ),
    },
    # ══════════════════════════════════════════════════════════════════════════
    # OCI DRG v2 / FastConnect / VPN Connect Templates
    # ══════════════════════════════════════════════════════════════════════════
    # ── 1. OCI DRG v2 Hub-and-Spoke ──────────────────────────────────────────
    {
        "id": "tpl-oci-drg-hub-spoke",
        "name": "OCI DRG v2 — Hub-and-Spoke Multi-VCN",
        "category": "OCI / Hybrid Cloud",
        "description": (
            "Dynamic Routing Gateway v2 as a regional hub connecting multiple VCNs "
            "with route distribution policies for segmentation. Shows FastConnect + "
            "IPsec VPN attachments, VCN attachments with import/export route distributions, "
            "Network Firewall in hub VCN, and Service Gateway for Oracle Services Network. "
            "See template docs for full SOP and Mermaid diagrams."
        ),
        "tags": json.dumps(
            [
                "oci",
                "drg",
                "drg-v2",
                "hub-spoke",
                "multi-vcn",
                "fastconnect",
                "vpn-connect",
                "network-firewall",
                "sop",
                "runbook",
            ]
        ),
        "graph_json": json.dumps(
            {
                "nodes": [
                    # DRG Hub
                    _node(
                        "drg",
                        "DRG v2 (Hub)",
                        "oci-drg",
                        500,
                        300,
                        {
                            "config": {
                                "role": "Regional hub — all VCN, FastConnect, and VPN attachments",
                                "features": "Route distributions, import/export policies, transit routing",
                                "limits": "300 VCN attachments, 100 DRG route tables",
                            }
                        },
                    ),
                    # DRG Route Tables
                    _node(
                        "rt-prod",
                        "DRG RT: Production",
                        "oci-subnet",
                        350,
                        200,
                        {
                            "config": {
                                "role": "DRG route table for production VCN attachment",
                                "import_dist": "Import from Hub, Shared, On-Prem (no Dev)",
                            }
                        },
                    ),
                    _node(
                        "rt-dev",
                        "DRG RT: Development",
                        "oci-subnet",
                        650,
                        200,
                        {"config": {"role": "DRG route table for dev VCN — isolated from Prod"}},
                    ),
                    _node(
                        "rt-onprem",
                        "DRG RT: On-Prem",
                        "oci-subnet",
                        500,
                        160,
                        {
                            "config": {
                                "role": "DRG route table for FastConnect/VPN attachments",
                                "import_dist": "Import from all VCN attachments",
                            }
                        },
                    ),
                    # VCNs
                    _node(
                        "vcn-hub",
                        "Hub VCN (Firewall)",
                        "oci-vcn",
                        500,
                        460,
                        {
                            "config": {
                                "cidr": "10.100.0.0/16",
                                "role": "Centralized inspection — all VCN-to-VCN traffic via NFW",
                            }
                        },
                    ),
                    _node("vcn-prod", "Prod VCN", "oci-vcn", 300, 460, {"config": {"cidr": "10.1.0.0/16"}}),
                    _node("vcn-dev", "Dev VCN", "oci-vcn", 700, 460, {"config": {"cidr": "10.2.0.0/16"}}),
                    _node(
                        "vcn-shared",
                        "Shared Services VCN",
                        "oci-vcn",
                        500,
                        560,
                        {
                            "config": {
                                "cidr": "10.0.0.0/16",
                                "services": "DNS, AD, logging, monitoring",
                            }
                        },
                    ),
                    # Network Firewall
                    _node(
                        "nfw",
                        "OCI Network Firewall",
                        "oci-nfw",
                        500,
                        380,
                        {
                            "config": {
                                "role": "Stateful inspection — IDS/IPS, URL filtering",
                                "placement": "Hub VCN — all transit traffic routed through NFW",
                            }
                        },
                    ),
                    # Service Gateway
                    _node(
                        "sgw",
                        "Service Gateway",
                        "oci-subnet",
                        700,
                        560,
                        {
                            "config": {
                                "role": "Access Oracle Services Network (OSN) privately",
                                "services": "Object Storage, Autonomous DB, OCI APIs",
                            }
                        },
                    ),
                    # Connectivity
                    _node(
                        "fc-pri",
                        "FastConnect (Primary)",
                        "oci-fc",
                        250,
                        300,
                        {"config": {"speed": "10 Gbps", "provider": "Equinix / Megaport"}},
                    ),
                    _node("fc-sec", "FastConnect (Secondary)", "oci-fc", 250, 400),
                    _node(
                        "vpn-1",
                        "IPsec VPN (Site 1)",
                        "oci-vpn",
                        750,
                        300,
                        {
                            "config": {
                                "tunnels": "2 tunnels per connection (BGP or static)",
                                "throughput": "250 Mbps per tunnel",
                            }
                        },
                    ),
                    _node("vpn-2", "IPsec VPN (Site 2)", "oci-vpn", 750, 400),
                    # On-Prem
                    _node("dc", "On-Prem DC", "router", 60, 350, {"config": {"bgp_asn": "65001"}}),
                    _node("branch", "Branch Office", "router", 900, 350),
                    # Monitoring
                    _node("cloudguard", "Cloud Guard", "oci-cloudguard", 350, 620),
                    _node("flowlogs", "VCN Flow Logs", "oci-flowlogs", 650, 620),
                ],
                "edges": [
                    # DRG Route Tables
                    _edge("rt-prod", "drg", "Prod RT", ""),
                    _edge("rt-dev", "drg", "Dev RT", ""),
                    _edge("rt-onprem", "drg", "On-Prem RT", ""),
                    # DRG to VCN attachments
                    _edge("drg", "vcn-hub", "VCN Attachment (Hub)", ""),
                    _edge("drg", "vcn-prod", "VCN Attachment (Prod)", ""),
                    _edge("drg", "vcn-dev", "VCN Attachment (Dev)", ""),
                    _edge("drg", "vcn-shared", "VCN Attachment (Shared)", ""),
                    # Firewall
                    _edge("nfw", "vcn-hub", "Hub VCN Firewall", ""),
                    # Service Gateway
                    _edge("vcn-shared", "sgw", "Oracle Services (OSN)", ""),
                    # Connectivity
                    _edge("dc", "fc-pri", "FastConnect Primary", "802.1Q"),
                    _edge("dc", "fc-sec", "FastConnect Secondary", "802.1Q"),
                    _edge("fc-pri", "drg", "FC Attachment (Primary)", "eBGP (ASN 31898)"),
                    _edge("fc-sec", "drg", "FC Attachment (Secondary)", "eBGP"),
                    _edge("branch", "vpn-1", "IPsec VPN", "BGP"),
                    _edge("branch", "vpn-2", "IPsec VPN (redundant)", "BGP"),
                    _edge("vpn-1", "drg", "VPN Attachment", ""),
                    _edge("vpn-2", "drg", "VPN Attachment", ""),
                    # Monitoring
                    _edge("cloudguard", "vcn-prod", "Security posture", ""),
                    _edge("flowlogs", "vcn-prod", "Flow logs", ""),
                ],
            }
        ),
    },
    # ── 2. OCI Hybrid Connectivity — Art of the Possible ─────────────────────
    {
        "id": "tpl-oci-hybrid-art-of-possible",
        "name": "OCI Hybrid Connectivity — Art of the Possible",
        "category": "OCI / Hybrid Cloud",
        "description": (
            "Comprehensive OCI hybrid connectivity showing every service: DRG v2 with "
            "advanced route distributions, FastConnect (colocation + provider), IPsec VPN "
            "Connect, Remote Peering Connection (inter-region), Network Firewall, WAF, "
            "Service Gateway, Cloud Guard, and cross-tenancy peering. "
            "See template docs for full SOP, comparison matrix, and traffic flows."
        ),
        "tags": json.dumps(
            [
                "oci",
                "hybrid",
                "art-of-possible",
                "drg",
                "fastconnect",
                "vpn-connect",
                "remote-peering",
                "network-firewall",
                "waf",
                "multi-region",
                "sop",
                "runbook",
            ]
        ),
        "graph_json": json.dumps(
            {
                "nodes": [
                    # On-Prem
                    _node("dc", "Data Center", "router", 60, 300, {"config": {"bgp_asn": "65001"}}),
                    _node("branch-1", "Branch 1", "router", 60, 450),
                    _node("branch-2", "Branch 2", "router", 60, 550),
                    # Connectivity
                    _node(
                        "fc-colo",
                        "FastConnect (Colocation)",
                        "oci-fc",
                        220,
                        250,
                        {"config": {"model": "Colocation — direct cross-connect at Equinix/CoreSite"}},
                    ),
                    _node(
                        "fc-prov",
                        "FastConnect (Provider)",
                        "oci-fc",
                        220,
                        350,
                        {"config": {"model": "Provider — via Megaport/AT&T/Verizon"}},
                    ),
                    _node("vpn-1", "VPN Connect (Branch 1)", "oci-vpn", 220, 450),
                    _node("vpn-2", "VPN Connect (Branch 2)", "oci-vpn", 220, 550),
                    # Region 1: Ashburn
                    _node(
                        "drg-1",
                        "DRG v2 (Ashburn)",
                        "oci-drg",
                        420,
                        300,
                        {
                            "config": {
                                "region": "us-ashburn-1",
                                "bgp_asn": "31898",
                            }
                        },
                    ),
                    _node("vcn-hub-1", "Hub VCN (Firewall)", "oci-vcn", 420, 420),
                    _node("nfw-1", "Network Firewall", "oci-nfw", 420, 500),
                    _node("vcn-prod", "Prod VCN", "oci-vcn", 600, 200, {"config": {"cidr": "10.1.0.0/16"}}),
                    _node("vcn-shared", "Shared VCN", "oci-vcn", 600, 300, {"config": {"cidr": "10.0.0.0/16"}}),
                    _node("vcn-db", "DB VCN", "oci-vcn", 600, 400, {"config": {"cidr": "10.10.0.0/16"}}),
                    # Region 2: Phoenix (DR)
                    _node("drg-2", "DRG v2 (Phoenix)", "oci-drg", 420, 650, {"config": {"region": "us-phoenix-1"}}),
                    _node("vcn-dr", "DR VCN", "oci-vcn", 600, 600, {"config": {"cidr": "10.3.0.0/16"}}),
                    _node("vcn-dev", "Dev VCN", "oci-vcn", 600, 700, {"config": {"cidr": "10.2.0.0/16"}}),
                    # Remote Peering (inter-region)
                    _node(
                        "rpc",
                        "Remote Peering Connection",
                        "oci-drg",
                        420,
                        570,
                        {
                            "config": {
                                "role": "Cross-region DRG peering — Ashburn <-> Phoenix",
                                "notes": "Route advertisements propagated between DRGs",
                            }
                        },
                    ),
                    # Application Layer
                    _node("lb-pub", "Public Load Balancer", "oci-lb", 780, 200),
                    _node("waf", "WAF", "oci-waf", 780, 280),
                    _node("sgw", "Service Gateway (OSN)", "oci-subnet", 780, 400),
                    # Security
                    _node("cloudguard", "Cloud Guard", "oci-cloudguard", 780, 500),
                    _node("vault", "OCI Vault (KMS)", "oci-vault", 780, 580),
                    _node("vss", "Vulnerability Scanning", "oci-vss", 780, 660),
                    _node("nsg-prod", "NSG (Prod)", "oci-nsg", 600, 120),
                ],
                "edges": [
                    # On-prem to connectivity
                    _edge("dc", "fc-colo", "FastConnect (Colo)", "802.1Q"),
                    _edge("dc", "fc-prov", "FastConnect (Provider)", ""),
                    _edge("branch-1", "vpn-1", "IPsec VPN", "BGP"),
                    _edge("branch-2", "vpn-2", "IPsec VPN", "BGP"),
                    # To DRG
                    _edge("fc-colo", "drg-1", "FC Attachment", "eBGP"),
                    _edge("fc-prov", "drg-1", "FC Attachment", "eBGP"),
                    _edge("vpn-1", "drg-1", "VPN Attachment", ""),
                    _edge("vpn-2", "drg-1", "VPN Attachment", ""),
                    # DRG-1 to VCNs
                    _edge("drg-1", "vcn-hub-1", "Hub VCN Attachment", ""),
                    _edge("drg-1", "vcn-prod", "Prod Attachment", ""),
                    _edge("drg-1", "vcn-shared", "Shared Attachment", ""),
                    _edge("drg-1", "vcn-db", "DB Attachment", ""),
                    _edge("vcn-hub-1", "nfw-1", "Firewall Subnet", ""),
                    # Inter-region peering
                    _edge("drg-1", "rpc", "RPC (Ashburn side)", ""),
                    _edge("rpc", "drg-2", "RPC (Phoenix side)", ""),
                    # DRG-2 to VCNs
                    _edge("drg-2", "vcn-dr", "DR Attachment", ""),
                    _edge("drg-2", "vcn-dev", "Dev Attachment", ""),
                    # Application layer
                    _edge("lb-pub", "vcn-prod", "Backend set", ""),
                    _edge("waf", "lb-pub", "WAF policy", ""),
                    _edge("vcn-db", "sgw", "Oracle Services (OSN)", ""),
                    _edge("nsg-prod", "vcn-prod", "Network Security Group", ""),
                    # Security
                    _edge("cloudguard", "vcn-prod", "Security posture", ""),
                    _edge("vault", "vcn-prod", "Key management", ""),
                    _edge("vss", "vcn-prod", "Vulnerability scan", ""),
                ],
            }
        ),
    },
    # ── JWICS / C2S / C2E Templates ─────────────────────────────────────────────

    # A1 — DoD Agency → JWICS Connection
    {
        "id": "tpl-jwics-agency-connect",
        "name": "DoD Agency → JWICS Connection",
        "category": "DoD Secret | JWICS",
        "description": (
            "Reference design for a DoD agency connecting to the Joint Worldwide Intelligence "
            "Communications System (JWICS). Shows the complete path from SCIF workstation through "
            "NSA Type 1 encryption (KG-250A/TACLANE), agency JWICS gateway, JWICS backbone, "
            "DIA hub router, and classified services (DNS, mail relay, application server). "
            "Based on DISA JWICS connection requirements and DIA network standards. IL6/SECRET."
        ),
        "tags": json.dumps(["jwics", "secret", "il6", "type1", "scif", "dia", "agency-connect", "ospf", "dod"]),
        "graph_json": json.dumps(
            {
                "nodes": [
                    _node("scif-ws",    "SCIF Workstation",         "endpoint-pc",   60,  300, {"config": {"classification": "SECRET", "cac_required": "yes", "hbss_enabled": "yes", "notes": "CAC + PIN required. No removable media. SCIF physical security enforced."}}),
                    _node("t1-enc",     "Type 1 Encryptor (KG-250A)", "fips-140-l3", 240, 300, {"config": {"classification": "SECRET", "device": "KG-250A / TACLANE Flex", "key_fill": "KYK-13 Simple Key Loader", "notes": "NSA Type 1 HAIPE-compliant. Key material loaded via fill device. Alarm monitored by DISA NOC."}}),
                    _node("jwics-gw",   "Agency JWICS Gateway",     "router",        420, 300, {"config": {"classification": "SECRET", "routing_protocol": "OSPF Area 0", "stig_baseline": "DISA Network STIG V3R9", "notes": "Agency-managed DISA-approved router. OSPF adjacency to DIA hub."}}),
                    _node("jwics-bb",   "JWICS Backbone",           "cloud",         600, 300, {"config": {"classification": "SECRET", "managed_by": "DIA / DISA", "circuit_type": "T3/DS3 or OC-3", "notes": "DIA-managed classified backbone. Physically separated from NIPRNet."}}),
                    _node("dia-hub",    "DIA Hub Router",           "router",        780, 300, {"config": {"classification": "SECRET", "asn": "JWICS-AS", "routing_protocol": "OSPF Area 0", "notes": "DIA hub — distributes classified routes across JWICS backbone."}}),
                    _node("jwics-dns",  "JWICS DNS Resolver",       "dod-jwics-dns", 970, 140, {"config": {"classification": "SECRET", "zones": "*.jwics.gov, *.dia.smil.mil", "dnssec": "yes", "notes": "DIA-managed recursive resolver. No Internet DNS. DNSSEC with NSS PKI keys."}}),
                    _node("jwics-mail", "JWICS Mail Relay",         "server",        970, 300, {"config": {"classification": "SECRET", "smime_required": "yes", "hbss_scan": "yes", "notes": "HBSS content scan at each relay hop. S/MIME with NSS PKI cert mandatory. No Internet relay path."}}),
                    _node("app-srv",    "Application Server",       "server",        970, 460, {"config": {"classification": "SECRET", "pki_cert": "NSS CA", "hbss_enabled": "yes", "acas_scanned": "yes"}}),
                    _node("acas-hbss",  "ACAS / HBSS Scanner",     "siem",          780, 480, {"config": {"classification": "SECRET", "purpose": "Vulnerability scanning (ACAS/Nessus) and host-based IDS (HBSS/McAfee ePO)", "scan_frequency": "continuous"}}),
                ],
                "edges": [
                    _edge("scif-ws",   "t1-enc",    "SCIF LAN (FIPS 140-2)",    ""),
                    _edge("t1-enc",    "jwics-gw",  "Encrypted Circuit",        "Type 1 AES-256 HAIPE"),
                    _edge("jwics-gw",  "jwics-bb",  "T3/DS3 Circuit",           "OSPF Area 0"),
                    _edge("jwics-bb",  "dia-hub",   "JWICS Backbone",           "OSPF"),
                    _edge("dia-hub",   "jwics-dns", "DNS Query",                "UDP/53 DNSSEC"),
                    _edge("dia-hub",   "jwics-mail","SMTP Relay",               "SMTP/S 587"),
                    _edge("dia-hub",   "app-srv",   "Application Traffic",      "HTTPS/443"),
                    _edge("t1-enc",    "acas-hbss", "ACAS Probe",               ""),
                    _edge("app-srv",   "jwics-dns", "Reverse DNS",              "UDP/53"),
                    _edge("app-srv",   "acas-hbss", "HBSS Agent",               ""),
                ],
            }
        ),
    },
    # A2 — DoD Agency → C2S (AWS Secret Region)
    {
        "id": "tpl-c2s-agency-connect",
        "name": "DoD Agency → C2S (AWS Secret Region)",
        "category": "DoD Secret | C2S",
        "description": (
            "Reference design for a DoD agency connecting to AWS C2S (Commercial Cloud Services — "
            "AWS Secret Region, us-gov-secret-1) via JWICS and DISA Secret Cloud Access Point (CAP). "
            "Shows SCIF workstation through Type 1 encryption, JWICS backbone, Secret BCAP/CAP, "
            "ClassifiedConnect 10G circuit, C2S Transit Gateway, mission and management VPCs. "
            "All traffic stays within the classified enclave — no Internet path. IL6/SECRET."
        ),
        "tags": json.dumps(["c2s", "aws", "secret", "il6", "jwics", "classifiedconnect", "disa-cap", "tgw", "dod"]),
        "graph_json": json.dumps(
            {
                "nodes": [
                    _node("scif-ws2",      "SCIF Workstation",             "endpoint-pc",      60,  310, {"config": {"classification": "SECRET", "cac_required": "yes"}}),
                    _node("t1-enc2",       "Type 1 Encryptor (TACLANE)",   "fips-140-l3",      240, 310, {"config": {"classification": "SECRET", "device": "TACLANE Flex / KG-250A"}}),
                    _node("jwics-gw2",     "Agency JWICS Gateway",         "router",           420, 310, {"config": {"classification": "SECRET", "routing_protocol": "OSPF Area 0"}}),
                    _node("jwics-bb2",     "JWICS Backbone",               "cloud",            600, 310, {"config": {"classification": "SECRET", "managed_by": "DIA / DISA"}}),
                    _node("secret-cap",    "DISA Secret BCAP/CAP",         "firewall",         780, 310, {"config": {"classification": "SECRET", "function": "SCCA boundary inspection at SECRET level", "vdss": "yes", "vdms": "yes", "tccm": "yes", "notes": "Applies full SCCA FRD inspection chain: VDSS → VDMS → TCCM at SECRET classification."}}),
                    _node("c2s-cc",        "C2S ClassifiedConnect",        "aws-dx",           960, 310, {"config": {"classification": "SECRET", "bandwidth": "10G", "bfd_enabled": True, "notes": "ClassifiedConnect = AWS Direct Connect variant for Secret Region. LOA-CFA from AWS Secret team."}}),
                    _node("c2s-tgw2",      "C2S Transit Gateway",          "aws-tgw",         1130, 310, {"config": {"classification": "SECRET", "region": "us-gov-secret-1", "route_tables": "mission, mgmt, shared-svc", "bgp_propagation": True}}),
                    _node("c2s-vpc-m",     "C2S Mission VPC",              "aws-vpc",         1310, 180, {"config": {"classification": "SECRET", "cidr": "10.200.0.0/16", "flow_logs": True, "guardduty": True}}),
                    _node("c2s-vpc-mgmt",  "C2S Mgmt VPC",                 "aws-vpc",         1310, 440, {"config": {"classification": "SECRET", "cidr": "10.201.0.0/16", "purpose": "ACAS, HBSS ePO, patch mgmt, syslog"}}),
                    _node("c2s-guardduty", "C2S GuardDuty / CloudTrail",   "server",          1130, 530, {"config": {"classification": "SECRET", "services": "GuardDuty, CloudTrail, Security Hub, Config"}}),
                ],
                "edges": [
                    _edge("scif-ws2",    "t1-enc2",       "SCIF LAN",                "FIPS 140-2"),
                    _edge("t1-enc2",     "jwics-gw2",     "Encrypted",               "Type 1 AES-256"),
                    _edge("jwics-gw2",   "jwics-bb2",     "T3/DS3 Circuit",          "OSPF Area 0"),
                    _edge("jwics-bb2",   "secret-cap",    "JWICS Circuit",           "BGP"),
                    _edge("secret-cap",  "c2s-cc",        "SCCA Boundary",           ""),
                    _edge("c2s-cc",      "c2s-tgw2",      "ClassifiedConnect 10G",   "BGP eBGP MD5"),
                    _edge("c2s-tgw2",    "c2s-vpc-m",     "Mission VPC Attachment",  ""),
                    _edge("c2s-tgw2",    "c2s-vpc-mgmt",  "Mgmt VPC Attachment",     ""),
                    _edge("c2s-vpc-m",   "c2s-guardduty", "Flow Logs / Findings",    ""),
                    _edge("c2s-vpc-mgmt","c2s-guardduty", "CloudTrail",              ""),
                ],
            }
        ),
    },
    # A3 — DoD Agency → C2E (Azure Government Secret)
    {
        "id": "tpl-c2e-agency-connect",
        "name": "DoD Agency → C2E (Azure Government Secret)",
        "category": "DoD Secret | C2E",
        "description": (
            "Reference design for a DoD agency connecting to Azure C2E (Commercial Cloud Enterprise — "
            "Azure Government Secret) via JWICS and DISA Secret Cloud Access Point. "
            "Shows SCIF workstation through Type 1 encryption, JWICS backbone, Secret BCAP/CAP, "
            "C2E ExpressRoute circuit, ER Gateway, Hub VNet with Azure Firewall Premium, and spoke "
            "workload VNet with Azure Private DNS. IL6/SECRET."
        ),
        "tags": json.dumps(["c2e", "azure", "secret", "il6", "jwics", "expressroute", "disa-cap", "vnet", "dod"]),
        "graph_json": json.dumps(
            {
                "nodes": [
                    _node("scif-ws3",    "SCIF Workstation",               "endpoint-pc",   60,  310, {"config": {"classification": "SECRET", "cac_required": "yes"}}),
                    _node("t1-enc3",     "Type 1 Encryptor (KG-250A)",     "fips-140-l3",   240, 310, {"config": {"classification": "SECRET", "device": "KG-250A / TACLANE Flex"}}),
                    _node("jwics-gw3",   "Agency JWICS Gateway",           "router",        420, 310, {"config": {"classification": "SECRET", "routing_protocol": "OSPF Area 0"}}),
                    _node("jwics-bb3",   "JWICS Backbone",                 "cloud",         600, 310, {"config": {"classification": "SECRET", "managed_by": "DIA / DISA"}}),
                    _node("secret-cap3", "DISA Secret BCAP/CAP",           "firewall",      780, 310, {"config": {"classification": "SECRET", "function": "SCCA inspection at SECRET; VDSS+VDMS+TCCM"}}),
                    _node("c2e-er",      "C2E ExpressRoute Circuit",       "server",        960, 310, {"config": {"classification": "SECRET", "bandwidth": "10G", "provider": "DISA / AT&T", "bgp_md5": True, "notes": "NSA Type 1 applied on physical layer. Primary + secondary paths (active/active)."}}),
                    _node("c2e-ergw",    "C2E ER Gateway",                 "server",       1130, 310, {"config": {"classification": "SECRET", "sku": "ErGw3AZ (Ultra Performance)", "fastpath": True, "region": "usgovsecret"}}),
                    _node("c2e-hub",     "C2E Hub VNet",                   "server",       1310, 180, {"config": {"classification": "SECRET", "cidr": "10.210.0.0/16", "azure_firewall": "Premium IDPS", "defender": True}}),
                    _node("c2e-spoke",   "C2E Spoke VNet (Workload)",      "server",       1310, 440, {"config": {"classification": "SECRET", "cidr": "10.211.0.0/24", "peered_to": "c2e-hub"}}),
                    _node("c2e-dns",     "C2E Private DNS Zone",           "server",       1130, 530, {"config": {"classification": "SECRET", "zones": ".c2e.microsoft.com, agency.secret.gov", "private_resolver": True, "disa_forwarder": ".smil.mil"}}),
                    _node("c2e-keyvault","C2E Key Vault (HSM)",            "server",       1310, 310, {"config": {"classification": "SECRET", "sku": "Premium (HSM-backed)", "fips": "FIPS 140-3 Level 3"}}),
                ],
                "edges": [
                    _edge("scif-ws3",    "t1-enc3",      "SCIF LAN",                "FIPS 140-2"),
                    _edge("t1-enc3",     "jwics-gw3",    "Encrypted",               "Type 1 AES-256"),
                    _edge("jwics-gw3",   "jwics-bb3",    "T3/DS3",                  "OSPF Area 0"),
                    _edge("jwics-bb3",   "secret-cap3",  "JWICS Circuit",           "BGP"),
                    _edge("secret-cap3", "c2e-er",       "SCCA Boundary",           ""),
                    _edge("c2e-er",      "c2e-ergw",     "ExpressRoute 10G",        "BGP eBGP MD5"),
                    _edge("c2e-ergw",    "c2e-hub",      "VNet Gateway",            ""),
                    _edge("c2e-hub",     "c2e-spoke",    "VNet Peering",            ""),
                    _edge("c2e-hub",     "c2e-dns",      "Private DNS",             ""),
                    _edge("c2e-spoke",   "c2e-keyvault", "PrivateLink",             ""),
                    _edge("c2e-dns",     "secret-cap3",  "DISA DNS Forward",        "UDP/53"),
                ],
            }
        ),
    },
    # B — DISA Full Network Panorama
    {
        "id": "tpl-disa-full-panorama",
        "name": "DISA Full Network Panorama (NIPR + JWICS + C2S + C2E)",
        "category": "DoD | DISA Full Topology",
        "description": (
            "Complete DISA network panorama showing all three classification tiers in one diagram: "
            "NIPR (unclassified), JWICS (SECRET), and classified commercial cloud (C2S/C2E). "
            "Three-row layout — NIPR/IL4 cloud on top, DISN shared infrastructure in the middle, "
            "JWICS/SECRET cloud on the bottom. Cross-Domain Solution (CDS) bridges the middle "
            "and secret rows. Use this for executive briefings, ATO boundary documentation, "
            "and understanding the full DISA cloud connectivity model. IL6/SECRET (highest tier shown)."
        ),
        "tags": json.dumps(["panorama", "nipr", "jwics", "c2s", "c2e", "bcap", "scca", "disn", "full-topology", "disa", "cds", "dod"]),
        "graph_json": json.dumps(
            {
                "nodes": [
                    # ── Row 1: NIPR / Unclassified (y=80) ───────────────────────────────────
                    _node("nipr-ws",     "Agency NIPR\nWorkstation",      "endpoint-pc",      60,   80),
                    _node("nipr-onramp", "NIPRNet\nOn-ramp",              "router",           230,  80, {"config": {"managed_by": "DISA"}}),
                    _node("bcap-fw-p",   "DISA BCAP\nFirewall",           "firewall",         400,  80, {"config": {"classification": "CUI", "scca_component": "BCAP"}}),
                    _node("vdss-p",      "VDSS Stack\n(IDS/WAF/TLS)",     "server",           570,  80, {"config": {"classification": "CUI", "scca_component": "VDSS", "tls_inspection": True}}),
                    _node("vdms-p",      "VDMS Stack\n(ACAS/HBSS/Patch)","server",           740,  80, {"config": {"classification": "CUI", "scca_component": "VDMS"}}),
                    _node("tccm-p",      "TCCM\n(IAM/CAC Gate)",         "server",           910,  80, {"config": {"classification": "CUI", "scca_component": "TCCM"}}),
                    _node("aws-gov-il4", "AWS GovCloud\nIL4",             "cloud",           1090,  30, {"config": {"classification": "CUI", "il": "IL4", "region": "us-gov-east-1"}}),
                    _node("az-gov-il4",  "Azure Gov\nIL4",                "cloud",           1090, 130, {"config": {"classification": "CUI", "il": "IL4", "region": "usgovvirginia"}}),
                    # ── Row 2: DISN Shared Infrastructure (y=310) ────────────────────────────
                    _node("disn-bb",     "DISN Backbone\n(DISA-managed)", "router",           430, 310, {"config": {"classification": "CUI", "managed_by": "DISA"}}),
                    _node("disn-ntp",    "DISN NTP\nStratum 1",           "server",           600, 310),
                    _node("disa-siem",   "DISA SIEM\n(Splunk/ArcSight)",  "siem",             770, 310, {"config": {"classification": "CUI", "purpose": "Centralized log aggregation both NIPR and JWICS feeds"}}),
                    _node("disa-acas",   "DISA ACAS\n(Nessus SC)",        "server",           940, 310, {"config": {"classification": "CUI", "purpose": "Vulnerability scanning across NIPR and SECRET enclaves"}}),
                    # ── Cross-Domain Solution (bridges rows 2 and 3) ─────────────────────────
                    _node("cds-p",       "Cross-Domain\nSolution (CDS)",  "fips-140-l3",      250, 470, {"config": {"classification": "SECRET", "device": "Forcepoint / Owl / Everfox", "fips": "FIPS 140-3 L3", "direction": "NIPR→SIPR/JWICS (allowlist)", "notes": "NSA-evaluated. All data flow hardware-enforced. No bidirectional unless dual-CDS."}}),
                    # ── Row 3: SECRET / JWICS (y=600) ────────────────────────────────────────
                    _node("scif-ws-p",   "SCIF Workstation",              "endpoint-pc",      60,  600, {"config": {"classification": "SECRET", "cac_required": "yes"}}),
                    _node("t1-enc-p",    "Type 1 Encryptor\n(KG-250A)",   "fips-140-l3",      230, 600, {"config": {"classification": "SECRET", "device": "KG-250A / TACLANE Flex"}}),
                    _node("jwics-bb-p",  "JWICS Backbone\n(DIA)",         "cloud",            410, 600, {"config": {"classification": "SECRET", "managed_by": "DIA"}}),
                    _node("dia-hub-p",   "DIA Hub\nRouter",               "router",           580, 600, {"config": {"classification": "SECRET", "routing": "OSPF Area 0"}}),
                    _node("secret-cap-p","DISA Secret\nBCAP/CAP",         "firewall",         760, 600, {"config": {"classification": "SECRET", "scca_applied": "yes"}}),
                    _node("c2s-p",       "C2S\n(AWS Secret us-gov-secret-1)", "cloud",       950, 540, {"config": {"classification": "SECRET", "il": "IL6", "region": "us-gov-secret-1"}}),
                    _node("c2e-p",       "C2E\n(Azure Gov Secret)",       "cloud",            950, 660, {"config": {"classification": "SECRET", "il": "IL6", "region": "usgovsecret"}}),
                ],
                "edges": [
                    # Row 1: NIPR flow
                    _edge("nipr-ws",     "nipr-onramp",  "NIPRNet",             ""),
                    _edge("nipr-onramp", "bcap-fw-p",    "DISN On-ramp",        ""),
                    _edge("bcap-fw-p",   "vdss-p",       "Inspection Chain",    ""),
                    _edge("vdss-p",      "vdms-p",       "VDSS→VDMS",     ""),
                    _edge("vdms-p",      "tccm-p",       "TCCM Cred Check",     ""),
                    _edge("tccm-p",      "aws-gov-il4",  "DX/10G",              "BGP"),
                    _edge("tccm-p",      "az-gov-il4",   "ExpressRoute 10G",    "BGP"),
                    # Row 2: DISN shared
                    _edge("nipr-onramp", "disn-bb",      "DISN Routing",        "OSPF"),
                    _edge("disn-bb",     "disn-ntp",     "NTP Sync",            "NTP/123"),
                    _edge("disn-bb",     "disa-siem",    "Syslog",              "syslog-TLS"),
                    _edge("disn-bb",     "disa-acas",    "Scan Traffic",        ""),
                    _edge("vdss-p",      "disa-siem",    "Alert Feed",          ""),
                    _edge("secret-cap-p","disa-siem",    "Secret Syslog",       "syslog-TLS"),
                    # CDS bridge
                    _edge("disn-bb",     "cds-p",        "NIPR→CDS",      ""),
                    _edge("cds-p",       "jwics-bb-p",   "CDS→JWICS",     "Filtered"),
                    # Row 3: SECRET flow
                    _edge("scif-ws-p",   "t1-enc-p",     "SCIF LAN",            ""),
                    _edge("t1-enc-p",    "jwics-bb-p",   "Type 1 Encrypted",    "HAIPE"),
                    _edge("jwics-bb-p",  "dia-hub-p",    "JWICS Backbone",      "OSPF"),
                    _edge("dia-hub-p",   "secret-cap-p", "Secret SCCA Boundary",""),
                    _edge("secret-cap-p","c2s-p",        "ClassifiedConnect 10G","BGP"),
                    _edge("secret-cap-p","c2e-p",        "ExpressRoute 10G",    "BGP"),
                ],
                "_annotationLegend": [
                    {"legend_id": "tier-nipr",   "label": "NIPR / CUI Tier (IL2–IL5)",     "color": "#2ecc71", "nodes": ["nipr-ws","nipr-onramp","bcap-fw-p","vdss-p","vdms-p","tccm-p","aws-gov-il4","az-gov-il4"]},
                    {"legend_id": "tier-disn",   "label": "DISN Shared Infrastructure",         "color": "#3498db", "nodes": ["disn-bb","disn-ntp","disa-siem","disa-acas"]},
                    {"legend_id": "tier-secret", "label": "SECRET Tier (IL6 / JWICS + C2S/C2E)","color": "#e74c3c", "nodes": ["scif-ws-p","t1-enc-p","jwics-bb-p","dia-hub-p","secret-cap-p","c2s-p","c2e-p"]},
                    {"legend_id": "tier-cds",    "label": "Cross-Domain Boundary",              "color": "#f39c12", "nodes": ["cds-p"]},
                ],
            }
        ),
    },
    # S1 — DNS Traffic Flow: JWICS + C2S/C2E Split-Horizon
    {
        "id": "tpl-dns-flow-jwics",
        "name": "DNS Traffic Flow — JWICS + C2S/C2E Split-Horizon",
        "category": "Traffic Flow | DNS",
        "description": (
            "Scenario diagram illustrating split-horizon DNS across three security domains: "
            "NIPR (Internet DNS), JWICS (DIA-managed classified DNS), and C2S (Route 53 PHZ). "
            "Color-coded paths show what is reachable, what is blocked, and where conditional "
            "forwarding applies. Use for troubleshooting DNS, application onboarding, and "
            "explaining classified DNS isolation to new team members. IL6/SECRET."
        ),
        "tags": json.dumps(["dns", "traffic-flow", "jwics", "c2s", "c2e", "split-horizon", "dnssec", "scenario", "dod"]),
        "graph_json": json.dumps(
            {
                "nodes": [
                    # Row 1: NIPR DNS path (y=80)
                    _node("nipr-user",     "NIPR User\n(Workstation)",     "endpoint-pc",  80,  80),
                    _node("nipr-stub",     "Stub Resolver\n(OS)",          "server",       280,  80),
                    _node("nipr-rec",      "NIPR Recursive\nResolver",     "server",       480,  80, {"config": {"resolver": "DISA NIPR resolver or ISP", "zones": "*.mil, *.gov + Internet"}}),
                    _node("inet-root",     "Internet\nRoot DNS",           "cloud",        700,  80, {"config": {"notes": "Available from NIPR. NOT accessible from JWICS or C2S."}}),
                    # Row 2: JWICS DNS path (y=280)
                    _node("scif-user",     "SCIF User\n(JWICS)",           "endpoint-pc",  80,  280, {"config": {"classification": "SECRET"}}),
                    _node("jwics-stub-d",  "Stub Resolver\n(JWICS OS)",    "server",       280, 280, {"config": {"classification": "SECRET", "notes": "Configured to query JWICS resolver only. No Internet DNS entries."}}),
                    _node("jwics-rec",     "JWICS Recursive\nResolver",    "dod-jwics-dns", 480, 280, {"config": {"classification": "SECRET", "managed_by": "DIA", "zones": "*.jwics.gov, *.dia.smil.mil, *.smil.mil", "dnssec": "yes"}}),
                    _node("dia-auth",      "DIA Authoritative\nDNS",       "dod-jwics-dns", 700, 280, {"config": {"classification": "SECRET", "authoritative_for": "*.jwics.gov, *.dia.smil.mil", "dnssec_signed": "yes"}}),
                    # Row 3: C2S DNS path (y=480)
                    _node("c2s-ec2-d",     "C2S Application\n(EC2)",       "server",       80,  480, {"config": {"classification": "SECRET", "region": "us-gov-secret-1"}}),
                    _node("r53-resolver",  "Route 53\nResolver (169.254.169.253)", "server",280, 480, {"config": {"classification": "SECRET", "notes": "VPC+2 address. Evaluates PHZ rules first."}}),
                    _node("r53-phz",       "Route 53 PHZ\n(.c2s.ic.gov)",  "server",       480, 480, {"config": {"classification": "SECRET", "zone": ".c2s.ic.gov", "notes": "Private Hosted Zone — C2S-internal records only."}}),
                    _node("disa-dns-fwd",  "DISA DNS\n(via ClassifiedConnect)", "server",  700, 480, {"config": {"classification": "SECRET", "path": "Resolver Rule forward to DISA DNS — NOT Internet", "resolves": "*.smil.mil, *.dia.mil"}}),
                    # Blocked path marker (visual only)
                    _node("blocked-path",  "⛔ BLOCKED\n(No Cross-Domain DNS)", "server", 480, 390, {"config": {"notes": "JWICS resolver cannot forward to NIPRNet/Internet. CDS required.", "stig": "SC-3, SC-7"}}),
                ],
                "edges": [
                    # NIPR path
                    _edge("nipr-user",    "nipr-stub",    "query",               "UDP/53"),
                    _edge("nipr-stub",    "nipr-rec",     "forward",             "UDP/53"),
                    _edge("nipr-rec",     "inet-root",    "Internet recursive",  "UDP/53 DNSSEC"),
                    # JWICS path
                    _edge("scif-user",    "jwics-stub-d", "JWICS query",         "UDP/53"),
                    _edge("jwics-stub-d", "jwics-rec",    "forward",             "UDP/53"),
                    _edge("jwics-rec",    "dia-auth",     "authoritative lookup","UDP/53 DNSSEC"),
                    # C2S path
                    _edge("c2s-ec2-d",   "r53-resolver", "169.254.169.253:53",  "UDP/53"),
                    _edge("r53-resolver", "r53-phz",      "PHZ match",           ""),
                    _edge("r53-resolver", "disa-dns-fwd", "Resolver Rule forward","UDP/53"),
                    # Blocked indicator
                    _edge("jwics-rec",    "blocked-path", "BLOCKED — no cross-domain", ""),
                    _edge("nipr-rec",     "blocked-path", "BLOCKED — no classified",   ""),
                ],
                "_annotationLegend": [
                    {"legend_id": "dns-nipr",   "label": "NIPR Internet DNS path",     "color": "#95a5a6", "nodes": ["nipr-user","nipr-stub","nipr-rec","inet-root"]},
                    {"legend_id": "dns-jwics",  "label": "JWICS Classified DNS path",  "color": "#2980b9", "nodes": ["scif-user","jwics-stub-d","jwics-rec","dia-auth"]},
                    {"legend_id": "dns-c2s",    "label": "C2S Private DNS path",       "color": "#e67e22", "nodes": ["c2s-ec2-d","r53-resolver","r53-phz","disa-dns-fwd"]},
                    {"legend_id": "dns-block",  "label": "Blocked Cross-Domain paths", "color": "#e74c3c", "nodes": ["blocked-path"]},
                ],
            }
        ),
    },
    # S2 — Email Traffic Flow: JWICS + C2S/C2E
    {
        "id": "tpl-email-flow-jwics",
        "name": "Email Traffic Flow — JWICS + C2S/C2E (S/MIME + PKI)",
        "category": "Traffic Flow | Email",
        "description": (
            "Scenario diagram showing classified email flow on JWICS from SCIF sender to recipient, "
            "including S/MIME signing, NSS PKI certificate lookup, agency SMTP relay, HBSS content "
            "scan, DIA JWICS relay, and final delivery. Also shows C2S and C2E email paths through "
            "DISA Email Gateway. Use for application onboarding, troubleshooting email delivery, "
            "and explaining PKI requirements to new engineers. IL6/SECRET."
        ),
        "tags": json.dumps(["email", "smtp", "smime", "pki", "nss", "hbss", "traffic-flow", "jwics", "c2s", "scenario", "dod"]),
        "graph_json": json.dumps(
            {
                "nodes": [
                    _node("sender-ws",    "Sender\n(SCIF Workstation)",    "endpoint-pc",  60,  300, {"config": {"classification": "SECRET", "email_client": "Outlook/Thunderbird + S/MIME plugin"}}),
                    _node("nss-pki",      "NSS PKI\n(DoD CA Hierarchy)",   "server",       280, 130, {"config": {"classification": "SECRET", "ca": "NSS Root CA → DoD CA-59 / DoD EMAIL CA-59", "protocol": "LDAP/636 (LDAPS)", "notes": "Cert lookup and CRL/OCSP validation. Cert must include email EKU."}}),
                    _node("smime-sign",   "S/MIME\nSigning",               "fips-140-l3",  280, 300, {"config": {"classification": "SECRET", "alg": "RSA-2048 / ECC-P384", "notes": "Message signed with sender private key + NSS PKI cert. Encrypt with recipient public key."}}),
                    _node("agency-relay", "Agency\nSMTP Relay",            "server",       480, 300, {"config": {"classification": "SECRET", "port": "587 STARTTLS", "auth": "CAC + LDAP", "notes": "Agency-managed relay. Routes based on recipient MX via JWICS DNS."}}),
                    _node("hbss-scan1",   "HBSS Content\nScan (Agency)",   "siem",         480, 470, {"config": {"classification": "SECRET", "component": "HBSS DLPe + ePO", "notes": "Content scan before forwarding to JWICS gateway. Rejects classified spill triggers."}}),
                    _node("jwics-gw-mail","JWICS Email\nGateway",          "server",       680, 300, {"config": {"classification": "SECRET", "smime_enforcement": "yes", "notes": "Validates S/MIME cert chain against NSS PKI. Rejects unsigned mail."}}),
                    _node("dia-relay",    "DIA JWICS\nMail Relay",         "server",       880, 300, {"config": {"classification": "SECRET", "notes": "Central DIA relay hub. Routes .dia.smil.mil and other JWICS domains."}}),
                    _node("hbss-scan2",   "HBSS Content\nScan (DIA)",      "siem",         880, 470, {"config": {"classification": "SECRET", "notes": "Second HBSS scan at DIA relay hop. Policy: keyword filter + attachment type check."}}),
                    _node("rcpt-relay",   "Recipient Agency\nSMTP Relay",  "server",      1080, 300, {"config": {"classification": "SECRET"}}),
                    _node("rcpt-ws",      "Recipient\n(SCIF Workstation)",  "endpoint-pc", 1280, 300, {"config": {"classification": "SECRET", "notes": "Verifies S/MIME signature against NSS PKI. Decrypts with own private key."}}),
                    # C2S path indicator
                    _node("c2s-ses",      "C2S SES\n(internal endpoint)",  "server",       680, 550, {"config": {"classification": "SECRET", "notes": "Internal SES VPC endpoint (us-gov-secret-1). NOT public SES. Routes to DISA gateway."}}),
                    _node("disa-email-gw","DISA Email\nGateway (C2S)",     "server",       880, 550, {"config": {"classification": "SECRET", "notes": "DISA Email Gateway in C2S. Stamps classification header. Routes to JWICS relay."}}),
                ],
                "edges": [
                    _edge("sender-ws",    "nss-pki",       "Cert/CRL lookup",     "LDAPS/636"),
                    _edge("sender-ws",    "smime-sign",    "compose + sign",      "S/MIME"),
                    _edge("smime-sign",   "agency-relay",  "submit",              "SMTP 587 STARTTLS"),
                    _edge("agency-relay", "hbss-scan1",    "content scan",        ""),
                    _edge("hbss-scan1",   "jwics-gw-mail", "cleared",             "SMTP"),
                    _edge("jwics-gw-mail","dia-relay",     "relay",               "SMTP/S"),
                    _edge("dia-relay",    "hbss-scan2",    "HBSS scan",           ""),
                    _edge("hbss-scan2",   "rcpt-relay",    "forward",             "SMTP"),
                    _edge("rcpt-relay",   "rcpt-ws",       "deliver",             "IMAP/993"),
                    # C2S path
                    _edge("c2s-ses",      "disa-email-gw", "outbound mail",       "SMTP"),
                    _edge("disa-email-gw","dia-relay",     "to JWICS relay",      "SMTP/S"),
                ],
                "_annotationLegend": [
                    {"legend_id": "email-jwics", "label": "JWICS Email path",         "color": "#2980b9", "nodes": ["sender-ws","smime-sign","agency-relay","hbss-scan1","jwics-gw-mail","dia-relay","hbss-scan2","rcpt-relay","rcpt-ws"]},
                    {"legend_id": "email-pki",   "label": "NSS PKI / S/MIME",         "color": "#8e44ad", "nodes": ["nss-pki","smime-sign"]},
                    {"legend_id": "email-c2s",   "label": "C2S Application Email",    "color": "#e67e22", "nodes": ["c2s-ses","disa-email-gw"]},
                ],
            }
        ),
    },
    # S3 — JWICS Troubleshooting Topology
    {
        "id": "tpl-troubleshooting-jwics",
        "name": "JWICS Troubleshooting Topology",
        "category": "Scenario | Troubleshooting",
        "description": (
            "JWICS troubleshooting diagram with annotated failure zones, monitoring hooks, "
            "and diagnostic command references at each hop. Four failure zones: "
            "Zone 1 (Type 1 crypto sync), Zone 2 (BGP/routing), Zone 3 (DNS resolution), "
            "Zone 4 (email relay). Includes SNMP, syslog, and ACAS monitoring paths. "
            "Use during incident response, NOC escalation, and training exercises. IL6/SECRET."
        ),
        "tags": json.dumps(["troubleshooting", "jwics", "c2s", "c2e", "bgp", "dns", "type1", "scenario", "noc", "dod"]),
        "graph_json": json.dumps(
            {
                "nodes": [
                    # Main path
                    _node("ts-scif",    "SCIF Workstation",           "endpoint-pc",  60,  260, {"config": {"classification": "SECRET", "check": "ping <jwics-gw-ip> — if fails: check CAC auth, HBSS agent, SCIF LAN cable"}}),
                    _node("ts-t1",      "Type 1 Encryptor\n(Zone 1)",  "fips-140-l3", 240, 260, {"config": {"classification": "SECRET", "failure_zone": "Zone 1: Crypto Sync", "alarms": "Red LED = key expiry or sync loss; call DISA NOC 1-800-FOR-DISA", "cmds": "Check alarm panel. Reload key via KYK-13. Verify BIT (Built-In-Test) passes.", "stig": "SC-8"}}),
                    _node("ts-gw",      "JWICS Gateway\n(Zone 2)",     "router",      420, 260, {"config": {"classification": "SECRET", "failure_zone": "Zone 2: BGP/Routing", "cmds": "show ospf neighbor; show ip route; ping <dia-hub-ip> source <loopback>", "escalate": "If OSPF down >5min: call DISA NOC + DIA NOC"}}),
                    _node("ts-bb",      "JWICS Backbone",              "cloud",       600, 260, {"config": {"classification": "SECRET", "notes": "If backbone outage: DISA NOC incident # required. Check DIA NOC status page."}}),
                    _node("ts-dia",     "DIA Hub Router",              "router",      780, 260, {"config": {"classification": "SECRET", "cmds": "show ospf database; traceroute <app-ip>; debug ospf events"}}),
                    _node("ts-dns",     "JWICS DNS\n(Zone 3)",         "server",      970, 100, {"config": {"classification": "SECRET", "failure_zone": "Zone 3: DNS Resolution", "cmds": "dig @<resolver-ip> hostname.jwics.gov; dig @<resolver-ip> +dnssec; check /var/log/named/", "escalate": "If zone missing: contact DIA DNS team (dia-dns@dia.smil.mil)"}}),
                    _node("ts-mail",    "JWICS Mail\n(Zone 4)",        "server",      970, 260, {"config": {"classification": "SECRET", "failure_zone": "Zone 4: Email Relay", "cmds": "telnet <relay-ip> 587; openssl s_client -connect <relay>:587; check HBSS DLPe policy logs", "escalate": "If cert expired: emergency re-cert via NSS PKI; contact DIA PKI team"}}),
                    _node("ts-app",     "Application\nServer",         "server",      970, 420, {"config": {"classification": "SECRET", "cmds": "curl -v https://<app>:443; check HBSS agent status; check ACAS scan results"}}),
                    # Monitoring infrastructure
                    _node("ts-snmp",    "SNMP Trap\nCollector",        "siem",        580, 470, {"config": {"classification": "SECRET", "protocol": "SNMP v3 (authPriv)", "traps": "BGP neighbor down, interface down, CPU/memory threshold"}}),
                    _node("ts-syslog",  "Syslog / SIEM",               "server",      580, 600, {"config": {"classification": "SECRET", "protocol": "syslog-TLS/6514", "retention": "1 year (NIST AU-11)"}}),
                    _node("ts-acas",    "ACAS Scanner\n(Nessus SC)",   "server",      780, 500, {"config": {"classification": "SECRET", "scan_frequency": "continuous", "reports_to": "DISA ACAS console"}}),
                    _node("ts-noc",     "DISA/DIA NOC\n1-800-FOR-DISA","server",      200, 500, {"config": {"notes": "24/7 NOC. Escalation tiers: Agency NOC → DISA NOC → DIA NOC → NSA CSS"}}),
                ],
                "edges": [
                    # Main path
                    _edge("ts-scif",  "ts-t1",    "SCIF LAN",             ""),
                    _edge("ts-t1",    "ts-gw",    "Encrypted Circuit",    "Type 1 AES-256"),
                    _edge("ts-gw",    "ts-bb",    "T3/DS3",               "OSPF Area 0"),
                    _edge("ts-bb",    "ts-dia",   "JWICS Backbone",       "OSPF"),
                    _edge("ts-dia",   "ts-dns",   "DNS",                  "UDP/53"),
                    _edge("ts-dia",   "ts-mail",  "SMTP",                 "SMTP/S"),
                    _edge("ts-dia",   "ts-app",   "Application",          "HTTPS/443"),
                    # Monitoring hooks
                    _edge("ts-t1",    "ts-syslog","Alarm/Syslog",         "syslog-TLS"),
                    _edge("ts-gw",    "ts-snmp",  "SNMP trap",            "SNMP v3"),
                    _edge("ts-gw",    "ts-syslog","Syslog",               "syslog-TLS"),
                    _edge("ts-dia",   "ts-snmp",  "SNMP",                 "SNMP v3"),
                    _edge("ts-dia",   "ts-acas",  "ACAS scan",            ""),
                    _edge("ts-app",   "ts-acas",  "Vulnerability scan",   ""),
                    _edge("ts-snmp",  "ts-syslog","SNMP to SIEM",         ""),
                    # NOC escalation
                    _edge("ts-noc",   "ts-t1",    "key resync assist",    ""),
                    _edge("ts-noc",   "ts-gw",    "routing triage",       ""),
                ],
                "_annotationLegend": [
                    {"legend_id": "zone1", "label": "Zone 1: Type 1 Crypto Failure", "color": "#e74c3c", "nodes": ["ts-t1"]},
                    {"legend_id": "zone2", "label": "Zone 2: BGP / Routing Failure",  "color": "#e67e22", "nodes": ["ts-gw"]},
                    {"legend_id": "zone3", "label": "Zone 3: DNS Resolution Failure", "color": "#f39c12", "nodes": ["ts-dns"]},
                    {"legend_id": "zone4", "label": "Zone 4: Email Relay Failure",    "color": "#9b59b6", "nodes": ["ts-mail"]},
                    {"legend_id": "monitoring", "label": "Monitoring / SIEM path",   "color": "#27ae60", "nodes": ["ts-snmp","ts-syslog","ts-acas"]},
                ],
            }
        ),
    },
    # S4 — New Application Onboarding (JWICS / C2S / C2E)
    {
        "id": "tpl-onboarding-new-app-jwics",
        "name": "New Application Onboarding — JWICS / C2S / C2E",
        "category": "Scenario | Onboarding",
        "description": (
            "Step-by-step onboarding diagram for adding a new application to JWICS, C2S, or C2E. "
            "Five phases color-coded: Phase 1 (ATO/ISSO registration), Phase 2 (DNS record), "
            "Phase 3 (Firewall ACL), Phase 4 (PKI certificate), Phase 5 (smoke test + validation). "
            "Each node shows the specific action, responsible team, NIST control, and command. "
            "Use as a visual checklist for new application deployments. IL6/SECRET."
        ),
        "tags": json.dumps(["onboarding", "new-app", "jwics", "c2s", "c2e", "ato", "dns", "pki", "firewall", "scenario", "dod"]),
        "graph_json": json.dumps(
            {
                "nodes": [
                    # Infrastructure
                    _node("ob-scif",   "SCIF Workstation\n(End User)",    "endpoint-pc",  60,  350, {"config": {"classification": "SECRET"}}),
                    _node("ob-t1",     "Type 1\nEncryptor",               "fips-140-l3",  230, 350, {"config": {"classification": "SECRET"}}),
                    _node("ob-gw",     "JWICS\nGateway",                  "router",       400, 350, {"config": {"classification": "SECRET"}}),
                    _node("ob-bb",     "JWICS\nBackbone",                 "cloud",        570, 350, {"config": {"classification": "SECRET"}}),
                    _node("ob-app",    "New Application\nHost",           "server",       820, 350, {"config": {"classification": "SECRET", "phase": "Phase 5 — Smoke Test", "action": "Verify HTTPS/443 reachable. Check HBSS agent installed. Confirm ACAS scan clean.", "nist": "CA-7, SI-3, RA-5"}}),
                    # Phase nodes
                    _node("ob-ato",    "Ph1: ATO\nRegistration",          "server",       400, 130, {"config": {"phase": "Phase 1 — ATO / ISSO", "action": "Submit system registration to eMASS or xACTA. Obtain ATO from DAA/AO. Register in DISA Asset Management.", "team": "ISSO + AO", "nist": "CA-1, CA-2, CA-9", "time_est": "4-12 weeks"}}),
                    _node("ob-dns",    "Ph2: DNS\nRecord Request",        "server",       570, 130, {"config": {"phase": "Phase 2 — DNS Record", "action": "Email dia-dns@dia.smil.mil: request A/AAAA record + PTR. Provide zone name, IP, TTL, SOA. Wait 1-3 business days.", "team": "DIA DNS Admins", "nist": "CM-7, CM-8", "cmd": "dig @<jwics-resolver> <hostname> to verify after creation"}}),
                    _node("ob-fw",     "Ph3: Firewall\nACL Request",      "firewall",     730, 130, {"config": {"phase": "Phase 3 — Firewall Rule", "action": "Submit DISA firewall change request (ITCS ticket). Specify src/dst IP, ports, protocol, justification + ATO reference.", "team": "DISA NOC Firewall Team", "nist": "SC-7, CM-5", "time_est": "1-5 business days"}}),
                    _node("ob-pki",    "Ph4: PKI\nCertificate",           "fips-140-l3",  570, 550, {"config": {"phase": "Phase 4 — NSS PKI Cert", "action": "Submit CSR to NSS PKI (DoD CA). Use server cert template. Install signed cert + DoD CA chain. Configure TLS 1.2+ FIPS cipher suite.", "team": "DoD PKI Office / NSS CA", "nist": "IA-5(2), SC-8, SC-8(1)", "cmd": "openssl verify -CAfile dod-ca-chain.pem server.crt"}}),
                    _node("ob-hbss",   "Ph5: HBSS\nAgent Deploy",        "siem",         820, 550, {"config": {"phase": "Phase 5 — HBSS + ACAS", "action": "Install HBSS McAfee ePO agent. Register with DISA ACAS Nessus SC scanner. Run initial scan; remediate all CAT1 STIGs before go-live.", "team": "HBSS Admins + ISSO", "nist": "SI-3, SI-4, RA-5", "cmd": "nessuscli scan --policy DISA-STIG"}}),
                    _node("ob-syslog", "Ph5: Syslog\nSIEM",              "server",      1020, 350, {"config": {"phase": "Phase 5 — Audit Logging", "action": "Configure rsyslog/syslog-ng to forward to DISA SIEM (syslog-TLS port 6514). Verify log receipt. Set retention = 1 year.", "team": "ISSO + DISA SIEM Team", "nist": "AU-2, AU-9, AU-11"}}),
                ],
                "edges": [
                    _edge("ob-scif", "ob-t1",     "SCIF LAN",          ""),
                    _edge("ob-t1",   "ob-gw",     "Encrypted",         "Type 1"),
                    _edge("ob-gw",   "ob-bb",     "JWICS",             "OSPF"),
                    _edge("ob-bb",   "ob-app",    "Application path",  "HTTPS/443"),
                    # Phase wires
                    _edge("ob-ato",  "ob-app",    "ATO grants access", ""),
                    _edge("ob-dns",  "ob-app",    "DNS name created",  ""),
                    _edge("ob-fw",   "ob-gw",     "ACL rule added",    ""),
                    _edge("ob-pki",  "ob-app",    "Cert installed",    ""),
                    _edge("ob-hbss", "ob-app",    "HBSS agent live",   ""),
                    _edge("ob-app",  "ob-syslog", "Audit logs",        "syslog-TLS/6514"),
                ],
                "_annotationLegend": [
                    {"legend_id": "ph1", "label": "Phase 1: ATO / ISSO Registration",   "color": "#e74c3c", "nodes": ["ob-ato"]},
                    {"legend_id": "ph2", "label": "Phase 2: DNS Record Creation",        "color": "#3498db", "nodes": ["ob-dns"]},
                    {"legend_id": "ph3", "label": "Phase 3: Firewall ACL",               "color": "#e67e22", "nodes": ["ob-fw"]},
                    {"legend_id": "ph4", "label": "Phase 4: PKI Certificate",            "color": "#9b59b6", "nodes": ["ob-pki"]},
                    {"legend_id": "ph5", "label": "Phase 5: Smoke Test + Validation",    "color": "#27ae60", "nodes": ["ob-app","ob-hbss","ob-syslog"]},
                ],
            }
        ),
    },
    # S5 — C2S + C2E Full Architecture (Dual-Cloud Secret)
    {
        "id": "tpl-c2s-c2e-full-architecture",
        "name": "C2S + C2E Full Architecture (Dual-Cloud SECRET)",
        "category": "DoD Secret | Multi-Cloud",
        "description": (
            "Side-by-side architecture showing C2S (AWS Secret Region) and C2E (Azure Government Secret) "
            "both hanging off the same DISA Secret BCAP/CAP, fed by JWICS backbone. "
            "Shows cross-cloud peering path via DISA backbone (not Internet), centralized SIEM "
            "and compliance logging, and the full security stack for each CSP. "
            "Use for multi-cloud ATO planning, capacity decisions, and explaining classified cloud "
            "options to mission owners. IL6/SECRET."
        ),
        "tags": json.dumps(["c2s", "c2e", "dual-cloud", "secret", "il6", "jwics", "disa-cap", "multi-csp", "aws", "azure", "dod"]),
        "graph_json": json.dumps(
            {
                "nodes": [
                    # JWICS / DISA center
                    _node("dc-jwics",   "JWICS Backbone",               "cloud",      180, 350, {"config": {"classification": "SECRET", "managed_by": "DIA"}}),
                    _node("dc-cap",     "DISA Secret BCAP/CAP",         "firewall",   400, 350, {"config": {"classification": "SECRET", "vdss": "yes", "vdms": "yes", "tccm": "yes", "notes": "Applies SCCA inspection chain at SECRET level to both C2S and C2E traffic."}}),
                    _node("dc-siem",    "DISA SIEM\n(Splunk/ArcSight)", "siem",       400, 130, {"config": {"classification": "SECRET", "feeds": "CloudTrail (C2S) + Azure Monitor (C2E) + JWICS syslog"}}),
                    _node("dc-acas",    "DISA ACAS\n(Nessus SC)",       "server",     400, 570, {"config": {"classification": "SECRET", "purpose": "Cross-cloud vulnerability scanning"}}),
                    # C2S branch (AWS)
                    _node("dc-c2s-dx",  "C2S ClassifiedConnect",        "aws-dx",     640, 180, {"config": {"classification": "SECRET", "bandwidth": "10G", "bfd": True}}),
                    _node("dc-c2s-tgw", "C2S Transit Gateway",          "aws-tgw",    840, 180, {"config": {"classification": "SECRET", "region": "us-gov-secret-1"}}),
                    _node("dc-c2s-vm",  "C2S Mission VPC",              "aws-vpc",   1060, 100, {"config": {"classification": "SECRET", "cidr": "10.200.0.0/16"}}),
                    _node("dc-c2s-mg",  "C2S Mgmt VPC",                 "aws-vpc",   1060, 260, {"config": {"classification": "SECRET", "cidr": "10.201.0.0/16", "purpose": "HBSS ePO, ACAS, patch"}}),
                    _node("dc-c2s-ct",  "C2S CloudTrail\n+ GuardDuty",  "server",     840, 320, {"config": {"classification": "SECRET", "exports_to": "DISA SIEM via ClassifiedConnect"}}),
                    # C2E branch (Azure)
                    _node("dc-c2e-er",  "C2E ExpressRoute",             "server",     640, 520, {"config": {"classification": "SECRET", "bandwidth": "10G", "bgp_md5": True}}),
                    _node("dc-c2e-ergw","C2E ER Gateway",               "server",     840, 520, {"config": {"classification": "SECRET", "sku": "ErGw3AZ"}}),
                    _node("dc-c2e-hub", "C2E Hub VNet\n(Firewall Premium)","server",1060, 430, {"config": {"classification": "SECRET", "cidr": "10.210.0.0/16", "azure_fw": "Premium IDPS"}}),
                    _node("dc-c2e-spk", "C2E Spoke VNet\n(Workload)",   "server",    1060, 620, {"config": {"classification": "SECRET", "cidr": "10.211.0.0/24"}}),
                    _node("dc-c2e-def", "C2E Defender\n+ Azure Monitor","server",     840, 680, {"config": {"classification": "SECRET", "exports_to": "DISA SIEM via ExpressRoute"}}),
                ],
                "edges": [
                    # JWICS to DISA CAP
                    _edge("dc-jwics",  "dc-cap",     "JWICS Circuit",            "BGP"),
                    # DISA CAP to C2S
                    _edge("dc-cap",    "dc-c2s-dx",  "ClassifiedConnect 10G",   "BGP eBGP MD5"),
                    _edge("dc-c2s-dx", "dc-c2s-tgw", "TGW Attachment",          "BGP"),
                    _edge("dc-c2s-tgw","dc-c2s-vm",  "Mission VPC",             ""),
                    _edge("dc-c2s-tgw","dc-c2s-mg",  "Mgmt VPC",                ""),
                    _edge("dc-c2s-tgw","dc-c2s-ct",  "CloudTrail Events",       ""),
                    # DISA CAP to C2E
                    _edge("dc-cap",    "dc-c2e-er",  "ExpressRoute 10G",        "BGP eBGP MD5"),
                    _edge("dc-c2e-er", "dc-c2e-ergw","ER Gateway",              "BGP"),
                    _edge("dc-c2e-ergw","dc-c2e-hub","Hub VNet",                ""),
                    _edge("dc-c2e-hub","dc-c2e-spk", "VNet Peering",            ""),
                    _edge("dc-c2e-hub","dc-c2e-def", "Azure Monitor",           ""),
                    # Logging to DISA SIEM
                    _edge("dc-c2s-ct", "dc-siem",    "CloudTrail/GD Findings",  "TLS"),
                    _edge("dc-c2e-def","dc-siem",    "Defender/Monitor Alerts", "TLS"),
                    # ACAS scanning
                    _edge("dc-acas",   "dc-c2s-mg",  "Nessus scan",             ""),
                    _edge("dc-acas",   "dc-c2e-spk", "Nessus scan",             ""),
                ],
                "_annotationLegend": [
                    {"legend_id": "dc-shared", "label": "DISA Shared (JWICS + CAP + Logging)", "color": "#2c3e50", "nodes": ["dc-jwics","dc-cap","dc-siem","dc-acas"]},
                    {"legend_id": "dc-c2s",    "label": "C2S (AWS Secret Region)",              "color": "#e67e22", "nodes": ["dc-c2s-dx","dc-c2s-tgw","dc-c2s-vm","dc-c2s-mg","dc-c2s-ct"]},
                    {"legend_id": "dc-c2e",    "label": "C2E (Azure Government Secret)",        "color": "#2980b9", "nodes": ["dc-c2e-er","dc-c2e-ergw","dc-c2e-hub","dc-c2e-spk","dc-c2e-def"]},
                ],
            }
        ),
    },
]


# ── Enclave-in-a-Box snippet seeds ────────────────────────────────────────────

ENCLAVE_SNIPPETS = [
    # ── JWICS / C2S / C2E Enclave Snippets ─────────────────────────────────────

    # JWICS Enclave Starter
    {
        "id": "snip-jwics-enclave",
        "name": "JWICS Enclave Starter",
        "category": "Enclave",
        "description": (
            "Minimal JWICS SECRET enclave: SCIF LAN switch, Type 1 encryptor (KG-250A), "
            "JWICS gateway router, classified DNS resolver, JWICS mail relay, and ACAS/HBSS server. "
            "All STIG CAT I controls pre-populated. DNSSEC and S/MIME required. IL6/SECRET."
        ),
        "classification_level": "SECRET",
        "impact_level": "IL6",
        "stig_controls": json.dumps([
            "SC-8", "SC-8(1)", "SC-28", "AU-2", "AU-9",
            "IA-2", "IA-5(2)", "CA-3", "SI-3", "AC-17", "CM-7",
        ]),
        "tags": json.dumps(["jwics", "secret", "il6", "scif", "type1", "dia", "hbss", "dns", "mail"]),
        "graph_json": json.dumps(
            {
                "nodes": [
                    _node("jw-sw",   "SCIF LAN Switch",       "switch-l2",   100, 200, {"config": {"classification": "SECRET", "stig_baseline": "DISA Layer 2 Switch STIG V2R4", "vlan_isolation": "yes", "notes": "SCIF physical boundary. No wireless ports. All ports CAC-authenticated."}}),
                    _node("jw-t1",   "Type 1 Encryptor\n(KG-250A)", "fips-140-l3", 300, 200, {"config": {"classification": "SECRET", "device": "KG-250A / TACLANE Flex", "fips": "FIPS 140-3 Level 3", "key_fill": "KYK-13 Simple Key Loader", "key_rotation": "annually or on compromise", "notes": "NSA Type 1 HAIPE. Alarm annunciation to DISA NOC."}}),
                    _node("jw-rtr",  "JWICS Gateway\nRouter", "router",      500, 200, {"config": {"classification": "SECRET", "stig_baseline": "DISA Network STIG V3R9", "routing_protocol": "OSPF Area 0", "auth": "CAC + TACACS+", "notes": "Agency-managed. DISA-approved platform. OSPF adjacency to DIA hub router."}}),
                    _node("jw-dns",  "JWICS DNS\nResolver",   "server",      300, 380, {"config": {"classification": "SECRET", "software": "BIND 9 (DISA-hardened)", "stig_baseline": "DISA DNS STIG V2R5", "dnssec": "yes", "zones": "*.jwics.gov, *.dia.smil.mil", "forwarders": "DIA authoritative only", "notes": "No Internet forwarders. DNSSEC mandatory."}}),
                    _node("jw-mail", "JWICS Mail\nRelay",     "server",      500, 380, {"config": {"classification": "SECRET", "protocol": "SMTP/S (STARTTLS)", "smime_required": "yes", "hbss_scan": "yes", "pki": "NSS PKI (DoD CA)", "notes": "S/MIME with NSS cert mandatory. HBSS DLPe content scan at relay."}}),
                    _node("jw-acas", "ACAS / HBSS\n(ePO)",   "siem",        700, 290, {"config": {"classification": "SECRET", "components": "HBSS McAfee ePO + Nessus SC (ACAS)", "scan_frequency": "continuous", "reports_to": "DISA ACAS console + SIEM", "notes": "HBSS mandatory for all IL6 hosts. ACAS scans weekly minimum."}}),
                ],
                "edges": [
                    _edge("jw-sw",   "jw-t1",   "SCIF LAN (1GbE)",   ""),
                    _edge("jw-t1",   "jw-rtr",  "Type 1 Encrypted",  "HAIPE AES-256"),
                    _edge("jw-rtr",  "jw-dns",  "DNS Query",         "UDP/53 DNSSEC"),
                    _edge("jw-rtr",  "jw-mail", "SMTP Relay",        "SMTP/S 587"),
                    _edge("jw-dns",  "jw-acas", "HBSS Agent",        ""),
                    _edge("jw-mail", "jw-acas", "HBSS Scan",         ""),
                    _edge("jw-rtr",  "jw-acas", "ACAS Probe",        ""),
                ],
            }
        ),
    },
    # C2S Compartment Starter (AWS Secret Region)
    {
        "id": "snip-c2s-compartment",
        "name": "C2S Compartment Starter (AWS Secret Region)",
        "category": "Cloud",
        "description": (
            "Minimal C2S (AWS Secret Region) compartment: ClassifiedConnect circuit, Transit Gateway, "
            "mission VPC, management VPC, Route 53 Private Hosted Zone, and CloudTrail/GuardDuty. "
            "All IL6 STIG controls pre-populated. No Internet path. IL6/SECRET."
        ),
        "classification_level": "SECRET",
        "impact_level": "IL6",
        "stig_controls": json.dumps([
            "AU-2", "AU-9", "AC-4", "SC-7", "SC-8", "SI-4", "CM-6", "IA-2(1)", "IA-2(2)", "RA-5",
        ]),
        "tags": json.dumps(["c2s", "aws", "secret", "il6", "cloud", "classifiedconnect", "tgw", "vpc", "guardduty"]),
        "graph_json": json.dumps(
            {
                "nodes": [
                    _node("cs-cc",   "C2S ClassifiedConnect",   "aws-dx",    100, 200, {"config": {"classification": "SECRET", "bandwidth": "10G", "bfd_enabled": True, "notes": "AWS ClassifiedConnect = Direct Connect for Secret Region. LOA-CFA from AWS Secret team."}}),
                    _node("cs-tgw",  "C2S Transit Gateway",     "aws-tgw",   300, 200, {"config": {"classification": "SECRET", "region": "us-gov-secret-1", "route_tables": "mission, mgmt, shared-svc", "bgp_propagation": True}}),
                    _node("cs-vm",   "C2S Mission VPC",         "aws-vpc",   500, 100, {"config": {"classification": "SECRET", "cidr": "10.200.0.0/16", "flow_logs_enabled": True, "guardduty": True, "notes": "Workload VPC. Flow Logs and GuardDuty mandatory for IL6."}}),
                    _node("cs-mg",   "C2S Mgmt VPC",            "aws-vpc",   500, 300, {"config": {"classification": "SECRET", "cidr": "10.201.0.0/16", "purpose": "ACAS Nessus SC, HBSS ePO, patch management, syslog forwarding"}}),
                    _node("cs-phz",  "C2S Route 53 PHZ\n(.c2s.ic.gov)", "server", 700, 200, {"config": {"classification": "SECRET", "zone": ".c2s.ic.gov", "dnssec": "yes", "forwarder": ".smil.mil → DISA DNS via ClassifiedConnect"}}),
                    _node("cs-ct",   "CloudTrail\n+ GuardDuty\n+ Security Hub", "server", 300, 380, {"config": {"classification": "SECRET", "exports_to": "DISA SIEM via ClassifiedConnect", "trail_type": "multi-region (Secret)", "notes": "CloudTrail must be enabled in all regions. GuardDuty Malware Protection enabled."}}),
                ],
                "edges": [
                    _edge("cs-cc",  "cs-tgw",  "ClassifiedConnect 10G", "BGP eBGP MD5"),
                    _edge("cs-tgw", "cs-vm",   "Mission VPC Attachment",""),
                    _edge("cs-tgw", "cs-mg",   "Mgmt VPC Attachment",   ""),
                    _edge("cs-vm",  "cs-phz",  "Route 53 Resolver",     "UDP/53"),
                    _edge("cs-mg",  "cs-phz",  "DNS Query",             "UDP/53"),
                    _edge("cs-vm",  "cs-ct",   "Flow Logs / Events",    ""),
                    _edge("cs-mg",  "cs-ct",   "CloudTrail",            ""),
                    _edge("cs-ct",  "cs-cc",   "SIEM Export",           "TLS"),
                ],
            }
        ),
    },
    # C2E Cloud Enclave Starter (Azure Government Secret)
    {
        "id": "snip-c2e-cloud-enclave",
        "name": "C2E Enclave Starter (Azure Government Secret)",
        "category": "Cloud",
        "description": (
            "Minimal C2E (Azure Government Secret) enclave: ExpressRoute circuit, ER Gateway, "
            "Hub VNet with Azure Firewall Premium, Spoke VNet for workloads, Azure Private DNS Zone, "
            "Microsoft Defender for Cloud, and HSM-backed Key Vault. IL6/SECRET."
        ),
        "classification_level": "SECRET",
        "impact_level": "IL6",
        "stig_controls": json.dumps([
            "AU-2", "AU-9", "AC-4", "SC-7", "SC-8", "SI-4", "CM-6", "IA-2(1)", "IA-2(2)", "RA-5",
        ]),
        "tags": json.dumps(["c2e", "azure", "secret", "il6", "cloud", "expressroute", "vnet", "defender", "keyvault"]),
        "graph_json": json.dumps(
            {
                "nodes": [
                    _node("ce-er",    "C2E ExpressRoute\nCircuit",         "server",   100, 200, {"config": {"classification": "SECRET", "bandwidth": "10G", "bgp_md5": True, "primary_secondary": "active/active", "notes": "NSA Type 1 on physical layer. Primary + diverse secondary paths required."}}),
                    _node("ce-ergw",  "C2E ER Gateway\n(ErGw3AZ)",         "server",   300, 200, {"config": {"classification": "SECRET", "sku": "ErGw3AZ (Ultra Performance)", "fastpath": True, "region": "usgovsecret"}}),
                    _node("ce-hub",   "C2E Hub VNet\n(Azure FW Premium)",  "server",   500, 100, {"config": {"classification": "SECRET", "cidr": "10.210.0.0/16", "azure_firewall_sku": "Premium (IDPS)", "forced_tunnel": True, "notes": "All spoke VNet traffic routes through Hub FW for inspection."}}),
                    _node("ce-spk",   "C2E Spoke VNet\n(Workload)",        "server",   500, 300, {"config": {"classification": "SECRET", "cidr": "10.211.0.0/24", "peered_to": "Hub VNet", "route_table": "UDR → Azure FW"}}),
                    _node("ce-dns",   "C2E Private\nDNS Zone",             "server",   700, 200, {"config": {"classification": "SECRET", "zones": ".c2e.microsoft.com + agency.secret.gov", "private_resolver": True, "conditional_fwd": ".smil.mil → DISA DNS via ExpressRoute"}}),
                    _node("ce-def",   "Defender for Cloud\n+ Azure Monitor","server",  300, 380, {"config": {"classification": "SECRET", "exports_to": "DISA SIEM via ExpressRoute", "workspaces": "Log Analytics (Government Secret)"}}),
                    _node("ce-kv",    "Key Vault\n(Premium HSM)",          "server",   700, 380, {"config": {"classification": "SECRET", "sku": "Premium", "fips": "FIPS 140-3 Level 3", "purge_protection": True, "notes": "All secrets, keys, certs stored in HSM-backed Key Vault. RBAC only (no Vault Access Policies)."}}),
                ],
                "edges": [
                    _edge("ce-er",   "ce-ergw",  "ExpressRoute 10G",  "BGP eBGP MD5"),
                    _edge("ce-ergw", "ce-hub",   "Hub VNet Gateway",  ""),
                    _edge("ce-hub",  "ce-spk",   "VNet Peering",      ""),
                    _edge("ce-hub",  "ce-dns",   "Private DNS",       ""),
                    _edge("ce-spk",  "ce-dns",   "DNS Query",         "UDP/53"),
                    _edge("ce-hub",  "ce-def",   "Azure Monitor",     ""),
                    _edge("ce-spk",  "ce-kv",    "PrivateLink",       ""),
                    _edge("ce-def",  "ce-er",    "SIEM Export",       "TLS"),
                ],
            }
        ),
    },
    # Cross-Domain Solution Boundary (HIGH-to-LOW)
    {
        "id": "snip-cds-boundary",
        "name": "Cross-Domain Solution Boundary (HIGH-to-LOW)",
        "category": "Cross-Domain",
        "description": (
            "Cross-Domain Solution (CDS) boundary snippet for HIGH-to-LOW data transfer "
            "(e.g., SIPR/JWICS → NIPRNet). Shows HIGH-side firewall, CDS appliance "
            "(Forcepoint / Owl Cyber Defense / Everfox), LOW-side firewall, audit log server, "
            "and CDS policy engine. Allowlist-only, hardware-enforced. NSA-evaluated. IL6/SECRET."
        ),
        "classification_level": "SECRET",
        "impact_level": "IL6",
        "stig_controls": json.dumps([
            "SC-8", "SC-10", "AC-4", "AC-4(1)", "AU-9", "CA-3(5)", "SI-3", "CM-7",
        ]),
        "tags": json.dumps(["cds", "cross-domain", "secret", "nipr", "jwics", "sipr", "guard", "il6", "allowlist"]),
        "graph_json": json.dumps(
            {
                "nodes": [
                    _node("cds-h-fw",  "HIGH-Side Firewall\n(SECRET)",    "firewall",   100, 200, {"config": {"classification": "SECRET", "zone": "HIGH (SIPR/JWICS)", "stig_baseline": "DISA Network STIG V3R9", "default_deny": True}}),
                    _node("cds-dev",   "CDS Appliance\n(Guard)",          "fips-140-l3",300, 200, {"config": {"classification": "SECRET→CUI", "device": "Forcepoint TGS / Owl OPDS / Everfox HVT", "fips": "FIPS 140-3 Level 3", "evaluation": "NSA CSFC / CC EAL4+", "direction": "HIGH→LOW only (one-way guard)", "filter_policy": "allowlist: approved content types only", "notes": "Hardware-enforced data flow. No bidirectional unless dual-CDS pair."}}),
                    _node("cds-l-fw",  "LOW-Side Firewall\n(CUI/NIPR)",  "firewall",   500, 200, {"config": {"classification": "CUI", "zone": "LOW (NIPRNet/CUI)", "stig_baseline": "DISA Network STIG V3R9"}}),
                    _node("cds-audit", "CDS Audit Log\nServer",           "server",     300, 380, {"config": {"classification": "SECRET", "retention": "7 years (NIST AU-11)", "immutable": True, "stig": "AU-2, AU-9", "notes": "Append-only audit log of all CDS transfer events. Tamper-evident storage."}}),
                    _node("cds-policy","CDS Policy Engine\n(Content Filter)", "server", 500, 380, {"config": {"classification": "SECRET", "policy_type": "allowlist (file type + keyword + size)", "review_cycle": "annual", "approval": "AO + DISA CDS PMO"}}),
                ],
                "edges": [
                    _edge("cds-h-fw",  "cds-dev",    "HIGH traffic",      ""),
                    _edge("cds-dev",   "cds-l-fw",   "Filtered/Approved", "allowlist-only"),
                    _edge("cds-dev",   "cds-audit",  "Audit event",       "syslog-TLS"),
                    _edge("cds-dev",   "cds-policy", "Content check",     ""),
                    _edge("cds-policy","cds-dev",    "Policy enforce",    ""),
                ],
            }
        ),
    },

    # 1 ─ SIPR Enclave Starter
    {
        "id": "snip-sipr-enclave",
        "name": "SIPR Enclave Starter",
        "category": "Enclave",
        "description": (
            "Minimal SECRET-network enclave: perimeter firewall, cross-domain solution (CDS), "
            "IDS/IPS sensor, syslog collector, and admin workstation. "
            "All STIG CAT I controls pre-populated (FIPS 140-2, HBSS, audit logging)."
        ),
        "classification_level": "SECRET",
        "impact_level": "IL6",
        "stig_controls": json.dumps(
            [
                "SC-8",
                "SC-8(1)",
                "AU-2",
                "AU-9",
                "SC-28",
                "CA-3",
                "SI-3",
                "IA-2",
                "AC-17",
                "CM-7",
            ]
        ),
        "tags": json.dumps(["sipr", "secret", "il6", "cds", "hbss", "fips"]),
        "graph_json": json.dumps(
            {
                "nodes": [
                    _node(
                        "s-fw",
                        "SIPR Perimeter FW",
                        "firewall",
                        300,
                        100,
                        {
                            "config": {
                                "classification": "SECRET",
                                "stig_baseline": "DISA Network STIG V3R9",
                                "fips_mode": "FIPS 140-2 Level 2",
                                "hbss_enabled": "yes",
                                "audit_logging": "yes",
                                "stig_cat1_open": "0",
                                "notes": "SIPR perimeter firewall — DISA-approved platform. FIPS 140-2 enforced.",
                            }
                        },
                    ),
                    _node(
                        "s-cds",
                        "Cross-Domain Solution",
                        "fips-140-l3",
                        500,
                        100,
                        {
                            "config": {
                                "classification": "SECRET",
                                "stig_baseline": "DISA CDS STIG V2R4",
                                "fips_mode": "FIPS 140-3 Level 3",
                                "approval_authority": "DISA CDS PMO",
                                "data_flow_direction": "high-to-low",
                                "filter_policy": "allowlist",
                                "notes": "NSA-evaluated CDS. Data-flow direction enforced by hardware guard.",
                            }
                        },
                    ),
                    _node(
                        "s-ids",
                        "IDS/IPS Sensor",
                        "siem",
                        300,
                        250,
                        {
                            "config": {
                                "classification": "SECRET",
                                "stig_baseline": "DISA HBSS STIG V2R2",
                                "sensor_type": "inline IPS",
                                "hbss_component": "HIPS + DLPe",
                                "alert_destination": "SIPR SIEM",
                                "notes": "Host-Based Security System (HBSS) IDS/IPS — mandatory for DoD IL6.",
                            }
                        },
                    ),
                    _node(
                        "s-log",
                        "Syslog / SIEM Collector",
                        "server",
                        500,
                        250,
                        {
                            "config": {
                                "classification": "SECRET",
                                "stig_baseline": "DISA Log Server STIG V1R1",
                                "fips_mode": "FIPS 140-2 Level 1",
                                "log_retention_days": "365",
                                "protocols": "syslog-TLS, SNMP-v3",
                                "notes": "Centralized audit log collection per NIST AU-2/AU-9. Immutable storage.",
                            }
                        },
                    ),
                    _node(
                        "s-aws",
                        "Admin Workstation",
                        "endpoint-pc",
                        700,
                        175,
                        {
                            "config": {
                                "classification": "SECRET",
                                "stig_baseline": "DISA Windows 11 STIG V2R2",
                                "fips_mode": "FIPS 140-2 Level 1",
                                "hbss_enabled": "yes",
                                "cac_required": "yes",
                                "notes": "Admin workstation — CAC authentication mandatory. No removable media.",
                            }
                        },
                    ),
                ],
                "edges": [
                    _edge("s-fw", "s-cds", "Encrypted", "Type 1 / HAIPE"),
                    _edge("s-fw", "s-ids", "Mirror Port", "SPAN"),
                    _edge("s-ids", "s-log", "syslog-TLS/514", "TLS"),
                    _edge("s-fw", "s-log", "syslog-TLS/514", "TLS"),
                    _edge("s-cds", "s-aws", "1GbE", ""),
                    _edge("s-aws", "s-log", "syslog-TLS/514", "TLS"),
                ],
            }
        ),
    },
    # 2 ─ IL5 DMZ Pattern
    {
        "id": "snip-il5-dmz",
        "name": "IL5 DMZ Pattern",
        "category": "Enclave",
        "description": (
            "Dual-firewall DMZ for IL5 (CUI/Dedicated). "
            "Outer firewall faces untrusted networks, DMZ hosts reside between firewalls, "
            "inner firewall guards the internal CUI enclave. "
            "STIG baselines and FIPS 140-2 enforcement pre-populated per DISA Cloud SRG IL5."
        ),
        "classification_level": "CUI",
        "impact_level": "IL5",
        "stig_controls": json.dumps(
            [
                "SC-7",
                "SC-7(3)",
                "SC-8",
                "AC-4",
                "CA-3",
                "SI-4",
                "AU-2",
                "AU-12",
                "RA-5",
                "CM-6",
            ]
        ),
        "tags": json.dumps(["il5", "cui", "dmz", "dual-firewall", "fisma-high"]),
        "graph_json": json.dumps(
            {
                "nodes": [
                    _node(
                        "d-outer-fw",
                        "Outer Firewall (Untrusted)",
                        "firewall",
                        100,
                        200,
                        {
                            "config": {
                                "classification": "CUI",
                                "stig_baseline": "DISA Firewall SRG V2R1",
                                "impact_level": "IL5",
                                "fips_mode": "FIPS 140-2 Level 2",
                                "zone": "untrusted",
                                "rule_review_cycle": "90 days",
                                "notes": "Outer perimeter firewall — default-deny, allow-list only. Faces Internet/NIPRNet.",
                            }
                        },
                    ),
                    _node(
                        "d-web",
                        "DMZ Web Server",
                        "server",
                        350,
                        120,
                        {
                            "config": {
                                "classification": "CUI",
                                "stig_baseline": "DISA Apache Server STIG V3R2",
                                "impact_level": "IL5",
                                "zone": "dmz",
                                "tls_version": "TLS 1.3",
                                "fips_mode": "FIPS 140-2 Level 1",
                                "notes": "DMZ web tier — only 443/TCP inbound. No direct DB access.",
                            }
                        },
                    ),
                    _node(
                        "d-proxy",
                        "Reverse Proxy / WAF",
                        "load-balancer",
                        350,
                        280,
                        {
                            "config": {
                                "classification": "CUI",
                                "stig_baseline": "DISA Web Server SRG V3R1",
                                "impact_level": "IL5",
                                "zone": "dmz",
                                "waf_enabled": "yes",
                                "tls_termination": "yes",
                                "notes": "WAF in front of web tier. OWASP top-10 ruleset enabled.",
                            }
                        },
                    ),
                    _node(
                        "d-ids",
                        "DMZ IDS Sensor",
                        "siem",
                        350,
                        200,
                        {
                            "config": {
                                "classification": "CUI",
                                "stig_baseline": "DISA IDS SRG V1R2",
                                "impact_level": "IL5",
                                "zone": "dmz",
                                "sensor_mode": "passive tap",
                                "notes": "Passive IDS monitoring DMZ segment. Alerts to SIEM.",
                            }
                        },
                    ),
                    _node(
                        "d-inner-fw",
                        "Inner Firewall (CUI Enclave)",
                        "firewall",
                        600,
                        200,
                        {
                            "config": {
                                "classification": "CUI",
                                "stig_baseline": "DISA Firewall SRG V2R1",
                                "impact_level": "IL5",
                                "fips_mode": "FIPS 140-2 Level 2",
                                "zone": "trusted",
                                "micro_segmentation": "yes",
                                "notes": "Inner firewall — enforces east-west micro-segmentation within CUI enclave.",
                            }
                        },
                    ),
                    _node(
                        "d-siem",
                        "SIEM / Log Aggregator",
                        "server",
                        800,
                        120,
                        {
                            "config": {
                                "classification": "CUI",
                                "stig_baseline": "DISA Log Server STIG V1R1",
                                "impact_level": "IL5",
                                "fips_mode": "FIPS 140-2 Level 1",
                                "log_retention_days": "365",
                                "notes": "Central SIEM. Receives logs from all zones per NIST AU-2/AU-12.",
                            }
                        },
                    ),
                    _node(
                        "d-app",
                        "Internal App Server",
                        "server",
                        800,
                        280,
                        {
                            "config": {
                                "classification": "CUI",
                                "stig_baseline": "DISA Application Server SRG V3R3",
                                "impact_level": "IL5",
                                "zone": "trusted",
                                "fips_mode": "FIPS 140-2 Level 1",
                                "notes": "Internal application tier. Communicates only via inner firewall ACL.",
                            }
                        },
                    ),
                ],
                "edges": [
                    _edge("d-outer-fw", "d-web", "HTTPS/443", "TLS 1.3"),
                    _edge("d-outer-fw", "d-proxy", "HTTPS/443", "TLS 1.3"),
                    _edge("d-outer-fw", "d-ids", "SPAN", ""),
                    _edge("d-web", "d-inner-fw", "HTTPS/8443", "TLS 1.3"),
                    _edge("d-proxy", "d-inner-fw", "HTTPS/8443", "TLS 1.3"),
                    _edge("d-ids", "d-siem", "syslog-TLS", "TLS"),
                    _edge("d-inner-fw", "d-app", "TCP/8080", ""),
                    _edge("d-inner-fw", "d-siem", "syslog-TLS", "TLS"),
                    _edge("d-app", "d-siem", "syslog-TLS", "TLS"),
                ],
            }
        ),
    },
    # 3 ─ Tactical Edge Kit
    {
        "id": "snip-tactical-edge",
        "name": "Tactical Edge Kit",
        "category": "Enclave",
        "description": (
            "Deployable/expeditionary network kit: tactical router, NSA Type 1 encryptor, "
            "PACE radio, edge firewall, and managed switch. "
            "Supports SIPR/NIPR transport over satellite or tactical comms. "
            "DoD PACE plan, NSA Type 1 crypto, and STIG controls pre-populated."
        ),
        "classification_level": "SECRET",
        "impact_level": "IL6",
        "stig_controls": json.dumps(
            [
                "SC-8",
                "SC-8(1)",
                "IA-3",
                "CM-7",
                "AC-17",
                "SC-28",
                "CP-8",
                "CP-9",
                "AU-2",
                "SI-4",
            ]
        ),
        "tags": json.dumps(["tactical", "expeditionary", "pace", "il6", "type1", "haipe", "satcom"]),
        "graph_json": json.dumps(
            {
                "nodes": [
                    _node(
                        "t-sat",
                        "SATCOM Terminal",
                        "endpoint-iot",
                        100,
                        200,
                        {
                            "config": {
                                "classification": "SECRET",
                                "stig_baseline": "DISA Satellite Terminal STIG V1R1",
                                "pace_priority": "P (Primary)",
                                "frequency_band": "Ka/X-band",
                                "notes": "Primary SATCOM terminal. PACE Priority 1 — Primary transport.",
                            }
                        },
                    ),
                    _node(
                        "t-radio",
                        "Tactical Radio (PACE-2)",
                        "endpoint-iot",
                        100,
                        320,
                        {
                            "config": {
                                "classification": "SECRET",
                                "stig_baseline": "DISA WLAN STIG V7R1",
                                "pace_priority": "A (Alternate)",
                                "waveform": "SINCGARS / SRW",
                                "encryption": "NSA Type 1",
                                "notes": "Alternate transport — tactical VHF/UHF radio. PACE Priority 2.",
                            }
                        },
                    ),
                    _node(
                        "t-enc",
                        "KG-175D (TACLANE)",
                        "kg-175d",
                        300,
                        260,
                        {
                            "config": {
                                "classification": "SECRET",
                                "stig_baseline": "NSA TACLANE STIG V3R1",
                                "fips_mode": "FIPS 140-2 Level 3",
                                "key_management": "KMI / EKMS",
                                "throughput": "Up to 2 Gbps",
                                "notes": "NSA Type 1 HAIPE encryptor. Protects all RED traffic over BLACK network.",
                            }
                        },
                    ),
                    _node(
                        "t-rtr",
                        "Tactical Router",
                        "router",
                        500,
                        200,
                        {
                            "config": {
                                "classification": "SECRET",
                                "stig_baseline": "DISA Cisco Router STIG V3R3",
                                "impact_level": "IL6",
                                "fips_mode": "FIPS 140-2 Level 2",
                                "protocol": "OSPF",
                                "vrf": "SIPR",
                                "notes": "Core tactical router. RED side only — all traffic exits via KG-175D.",
                            }
                        },
                    ),
                    _node(
                        "t-fw",
                        "Edge Firewall",
                        "firewall",
                        500,
                        350,
                        {
                            "config": {
                                "classification": "SECRET",
                                "stig_baseline": "DISA Firewall SRG V2R1",
                                "impact_level": "IL6",
                                "fips_mode": "FIPS 140-2 Level 2",
                                "stig_cat1_open": "0",
                                "notes": "Edge stateful firewall. Default-deny. Only DoD-approved traffic permitted.",
                            }
                        },
                    ),
                    _node(
                        "t-sw",
                        "Managed Switch",
                        "switch-l2",
                        700,
                        270,
                        {
                            "config": {
                                "classification": "SECRET",
                                "stig_baseline": "DISA L2 Switch STIG V3R3",
                                "impact_level": "IL6",
                                "port_security": "yes",
                                "storm_control": "yes",
                                "stp_bpduguard": "yes",
                                "notes": "Access layer switch. Port security enabled; unused ports shut.",
                            }
                        },
                    ),
                    _node(
                        "t-ws",
                        "Commander Workstation",
                        "endpoint-pc",
                        900,
                        270,
                        {
                            "config": {
                                "classification": "SECRET",
                                "stig_baseline": "DISA Windows 11 STIG V2R2",
                                "impact_level": "IL6",
                                "fips_mode": "FIPS 140-2 Level 1",
                                "hbss_enabled": "yes",
                                "cac_required": "yes",
                                "notes": "Commander workstation — CAC + PIN required. HBSS enforced.",
                            }
                        },
                    ),
                ],
                "edges": [
                    _edge("t-sat", "t-enc", "BLACK / WAN", "HAIPE"),
                    _edge("t-radio", "t-enc", "BLACK / Radio", "HAIPE"),
                    _edge("t-enc", "t-rtr", "RED / 1GbE", "OSPF"),
                    _edge("t-rtr", "t-fw", "1GbE", ""),
                    _edge("t-rtr", "t-sw", "1GbE", ""),
                    _edge("t-fw", "t-sw", "1GbE", ""),
                    _edge("t-sw", "t-ws", "1GbE", ""),
                ],
            }
        ),
    },
    # 5 ─ Defense-in-Depth Security Stack
    {
        "id": "snip-defense-in-depth",
        "name": "Defense-in-Depth Security Stack",
        "category": "Security",
        "description": (
            "Multi-layer security stack showing edge-to-perimeter-to-app-to-data "
            "protection with encryption and logging at each layer. "
            "WAF at the edge, Shield for DDoS, Firewall for network inspection, "
            "ALB for application distribution, App and Data tiers, "
            "KMS for encryption at rest, and centralized logging."
        ),
        "classification_level": "CUI",
        "impact_level": "IL4",
        "stig_controls": json.dumps(
            [
                "SC-7",
                "SC-8",
                "SC-28",
                "AU-2",
                "AU-12",
                "SI-4",
                "AC-4",
                "SC-13",
            ]
        ),
        "tags": json.dumps(["defense-in-depth", "security-stack", "multi-layer", "waf", "kms"]),
        "graph_json": json.dumps(
            {
                "nodes": [
                    _node("did-waf", "WAF (Edge)", "aws-waf", 100, 200),
                    _node("did-shield", "Shield / DDoS", "aws-shield", 250, 200),
                    _node("did-fw", "Firewall", "firewall", 400, 200),
                    _node("did-alb", "Application LB", "aws-alb", 550, 200),
                    _node("did-app", "Application Tier", "server", 700, 200),
                    _node("did-data", "Data Tier", "database", 850, 200),
                    _node("did-kms", "KMS / Encryption", "aws-kms", 850, 340),
                    _node("did-logs", "Centralized Logs", "siem", 100, 340),
                ],
                "edges": [
                    _edge("did-waf", "did-shield", "L7 Filter", "HTTPS"),
                    _edge("did-shield", "did-fw", "DDoS Scrubbed", ""),
                    _edge("did-fw", "did-alb", "Inspected", ""),
                    _edge("did-alb", "did-app", "Forward", "HTTPS"),
                    _edge("did-app", "did-data", "Query", "TLS"),
                    _edge("did-data", "did-kms", "Encryption Keys", ""),
                    _edge("did-app", "did-logs", "App Logs", "TLS"),
                    _edge("did-fw", "did-logs", "FW Logs", "TLS"),
                ],
            }
        ),
    },
    # 6 ─ Zero Trust Network Segment
    {
        "id": "snip-zero-trust-network",
        "name": "Zero Trust Network Segment",
        "category": "Security",
        "description": (
            "Zero Trust Architecture micro-segment with explicit verify at every hop. "
            "Identity provider authenticates, policy engine authorizes, micro-segmentation "
            "firewall enforces, application processes, data store persists, "
            "and audit trail captures all decisions."
        ),
        "classification_level": "CUI",
        "impact_level": "IL4",
        "stig_controls": json.dumps(
            [
                "AC-4",
                "AC-17",
                "IA-2",
                "IA-8",
                "SC-7",
                "AU-2",
            ]
        ),
        "tags": json.dumps(["zero-trust", "zta", "micro-segmentation", "identity", "policy"]),
        "graph_json": json.dumps(
            {
                "nodes": [
                    _node("zt-idp", "Identity Provider", "azure-entra", 100, 200),
                    _node("zt-policy", "Policy Engine", "server", 280, 200),
                    _node("zt-msfw", "MicroSeg Firewall", "firewall", 460, 200),
                    _node("zt-app", "Application", "server", 640, 200),
                    _node("zt-data", "Data Store", "database", 820, 200),
                    _node("zt-audit", "Audit Trail", "siem", 460, 340),
                ],
                "edges": [
                    _edge("zt-idp", "zt-policy", "Authenticate", "SAML/OIDC"),
                    _edge("zt-policy", "zt-msfw", "Authorize", ""),
                    _edge("zt-msfw", "zt-app", "Allow/Deny", "mTLS"),
                    _edge("zt-app", "zt-data", "Query", "TLS"),
                    _edge("zt-policy", "zt-audit", "Decision Log", ""),
                    _edge("zt-msfw", "zt-audit", "Access Log", ""),
                ],
            }
        ),
    },
    # 7 ─ SIEM & Centralized Logging
    {
        "id": "snip-siem-logging-stack",
        "name": "SIEM & Centralized Logging",
        "category": "Security",
        "description": (
            "Centralized security monitoring pipeline from source to SIEM to alerting. "
            "Cloud audit trails and flow logs feed into a security hub or SCC, "
            "which forwards to SIEM for correlation and analysis. "
            "SIEM triggers alerts and archives raw logs to object storage."
        ),
        "classification_level": "CUI",
        "impact_level": "IL4",
        "stig_controls": json.dumps(
            [
                "AU-2",
                "AU-6",
                "AU-9",
                "AU-12",
                "SI-4",
                "IR-4",
            ]
        ),
        "tags": json.dumps(["siem", "logging", "audit", "securityhub", "alerting"]),
        "graph_json": json.dumps(
            {
                "nodes": [
                    _node("sl-trail", "CloudTrail / Audit", "aws-ct", 100, 200),
                    _node("sl-flow", "Flow Logs", "aws-flowlogs", 100, 340),
                    _node("sl-hub", "SecurityHub / SCC", "aws-securityhub", 350, 270),
                    _node("sl-siem", "SIEM Platform", "siem", 550, 270),
                    _node("sl-alerts", "Alerts & Notifications", "server", 750, 200),
                    _node("sl-archive", "S3 / GCS Archive", "cloud", 750, 340),
                ],
                "edges": [
                    _edge("sl-trail", "sl-hub", "Audit Events", ""),
                    _edge("sl-flow", "sl-hub", "Network Flows", ""),
                    _edge("sl-hub", "sl-siem", "Findings", ""),
                    _edge("sl-siem", "sl-alerts", "Triggered Alerts", ""),
                    _edge("sl-siem", "sl-archive", "Raw Log Archive", ""),
                    _edge("sl-trail", "sl-archive", "Trail Backup", ""),
                ],
            }
        ),
    },
    # 8 ─ SCCA Mission Spoke VPC
    {
        "id": "snip-scca-mission-spoke",
        "name": "SCCA Mission Spoke VPC",
        "category": "SCCA",
        "description": (
            "Reusable SCCA mission owner spoke VPC/VNet/VCN pattern. "
            "Private-only subnets (no IGW), TGW/DRG attachment routing all egress "
            "through VDSS inspection, VPC endpoints for AWS services, flow logs enabled. "
            "Drag onto any SCCA landing zone template and connect to the transit hub."
        ),
        "classification_level": "CUI",
        "impact_level": "IL5",
        "stig_controls": json.dumps(
            [
                "SC-7",
                "SC-7(3)",
                "AC-4",
                "AU-2",
                "AU-12",
                "SC-8",
            ]
        ),
        "tags": json.dumps(["scca", "mission", "spoke", "vpc", "reusable", "no-igw", "tgw-attachment"]),
        "graph_json": json.dumps(
            {
                "nodes": [
                    _node(
                        "mvpc",
                        "Mission VPC",
                        "aws-vpc",
                        200,
                        60,
                        {
                            "config": {
                                "cidr": "10.x.0.0/16",
                                "flow_logs_enabled": True,
                                "internet_gateway": False,
                            }
                        },
                    ),
                    _node("app", "App Subnet (Private)", "aws-subnet", 100, 200, {"config": {"tier": "application"}}),
                    _node("data", "Data Subnet (Isolated)", "aws-subnet", 300, 200, {"config": {"tier": "database"}}),
                    _node("ep-s3", "VPC Endpoint (S3)", "aws-gw-ep", 100, 340),
                    _node("ep-ssm", "VPC Endpoint (SSM)", "aws-gw-ep", 300, 340),
                    _node(
                        "tgw-att",
                        "TGW Attachment",
                        "aws-tgw",
                        200,
                        460,
                        {
                            "config": {
                                "note": "Connect to SCCA Transit Gateway; default route 0.0.0.0/0 \u2192 TGW",
                            }
                        },
                    ),
                    _node("flowlog", "VPC Flow Logs", "aws-flowlogs", 400, 60),
                ],
                "edges": [
                    _edge("mvpc", "app", "Subnet", ""),
                    _edge("mvpc", "data", "Subnet", ""),
                    _edge("mvpc", "ep-s3", "Endpoint", ""),
                    _edge("mvpc", "ep-ssm", "Endpoint", ""),
                    _edge("mvpc", "tgw-att", "TGW Attach", ""),
                    _edge("mvpc", "flowlog", "Logging", ""),
                    _edge("app", "data", "App\u2192DB", ""),
                ],
            }
        ),
    },
    # 9 ─ SCCA VDSS Security Stack
    {
        "id": "snip-scca-vdss-stack",
        "name": "SCCA VDSS Security Stack",
        "category": "SCCA",
        "description": (
            "Virtual Datacenter Security Stack (VDSS) building block per DISA FRD \u00a72.1.2. "
            "Includes network firewall (IDS/IPS), WAF (L7), DDoS protection, "
            "Gateway LB for inline inspection, and flow logs. "
            "Satisfies FRD requirements 2.1.2.1 through 2.1.2.18."
        ),
        "classification_level": "CUI",
        "impact_level": "IL5",
        "stig_controls": json.dumps(
            [
                "SC-7",
                "SC-7(3)",
                "SC-5",
                "SI-3",
                "SI-4",
                "AU-2",
                "AU-12",
                "SC-8",
            ]
        ),
        "tags": json.dumps(["scca", "vdss", "firewall", "ids-ips", "waf", "ddos", "ppsm", "frd-2.1.2"]),
        "graph_json": json.dumps(
            {
                "nodes": [
                    _node(
                        "vdss-vpc",
                        "VDSS VPC",
                        "aws-vpc",
                        200,
                        60,
                        {
                            "config": {
                                "cidr": "10.100.0.0/16",
                                "flow_logs_enabled": True,
                                "purpose": "VDSS",
                            }
                        },
                    ),
                    _node(
                        "nfw",
                        "Network Firewall (IDS/IPS)",
                        "aws-nfw",
                        100,
                        200,
                        {
                            "config": {
                                "inspection": "stateful",
                                "tls_inspection": True,
                                "ppsm": True,
                            }
                        },
                    ),
                    _node(
                        "waf",
                        "WAF (HTTP Inspection)",
                        "aws-waf",
                        300,
                        200,
                        {
                            "config": {
                                "managed_rules": [
                                    "AWSManagedRulesCommonRuleSet",
                                    "AWSManagedRulesKnownBadInputsRuleSet",
                                ],
                            }
                        },
                    ),
                    _node("shield", "Shield Advanced", "aws-shield", 200, 340),
                    _node(
                        "gwlb",
                        "Gateway LB (Inline)",
                        "aws-gwlb",
                        200,
                        460,
                        {
                            "config": {
                                "purpose": "Transparent bump-in-wire for third-party appliances",
                            }
                        },
                    ),
                    _node("flowlog", "VPC Flow Logs", "aws-flowlogs", 400, 60),
                    _node(
                        "tgw-att",
                        "TGW Attachment",
                        "aws-tgw",
                        200,
                        580,
                        {
                            "config": {
                                "note": "All mission VPC traffic routes through VDSS for inspection",
                            }
                        },
                    ),
                    _node(
                        "pcap",
                        "Packet Capture (S3)",
                        "server",
                        400,
                        200,
                        {
                            "config": {
                                "purpose": "Full packet capture per FRD \u00a72.1.2.16",
                            }
                        },
                    ),
                ],
                "edges": [
                    _edge("vdss-vpc", "nfw", "Inspection", ""),
                    _edge("vdss-vpc", "waf", "L7", ""),
                    _edge("vdss-vpc", "shield", "DDoS", ""),
                    _edge("nfw", "gwlb", "Inline", ""),
                    _edge("waf", "gwlb", "Inline", ""),
                    _edge("gwlb", "tgw-att", "To Hub", ""),
                    _edge("vdss-vpc", "flowlog", "Logging", ""),
                    _edge("nfw", "pcap", "Capture", ""),
                ],
            }
        ),
    },
    # 10 ─ SCCA VDMS Managed Services
    {
        "id": "snip-scca-vdms-services",
        "name": "SCCA VDMS Managed Services",
        "category": "SCCA",
        "description": (
            "Virtual Datacenter Managed Services (VDMS) building block per DISA FRD \u00a72.1.3. "
            "Includes ACAS-equivalent scanning (Inspector), HBSS endpoint security (SSM), "
            "identity/CAC auth (Managed AD), patch management, KMS, and centralized logging "
            "(CloudTrail + Security Hub). Satisfies FRD requirements 2.1.3.1 through 2.1.3.9."
        ),
        "classification_level": "CUI",
        "impact_level": "IL5",
        "stig_controls": json.dumps(
            [
                "RA-5",
                "SI-2",
                "IA-2",
                "IA-5",
                "SC-12",
                "SC-13",
                "AU-2",
                "AU-12",
                "CM-6",
            ]
        ),
        "tags": json.dumps(["scca", "vdms", "acas", "hbss", "cac-piv", "identity", "logging", "siem", "frd-2.1.3"]),
        "graph_json": json.dumps(
            {
                "nodes": [
                    _node(
                        "vdms-vpc",
                        "VDMS VPC",
                        "aws-vpc",
                        200,
                        60,
                        {
                            "config": {
                                "cidr": "10.101.0.0/16",
                                "flow_logs_enabled": True,
                                "purpose": "VDMS",
                            }
                        },
                    ),
                    _node(
                        "inspector",
                        "Inspector (ACAS)",
                        "aws-inspector",
                        60,
                        200,
                        {
                            "config": {
                                "purpose": "Continuous vulnerability scanning per FRD \u00a72.1.3.1",
                            }
                        },
                    ),
                    _node(
                        "ssm",
                        "Systems Manager (HBSS)",
                        "aws-ssm",
                        200,
                        200,
                        {
                            "config": {
                                "purpose": "Endpoint security + patch management per FRD \u00a72.1.3.2/2.1.3.4",
                            }
                        },
                    ),
                    _node(
                        "ad",
                        "Managed AD (CAC/PIV)",
                        "aws-ad",
                        340,
                        200,
                        {
                            "config": {
                                "edition": "Enterprise",
                                "mfa": "CAC/PIV",
                                "purpose": "Identity per FRD \u00a72.1.3.3",
                            }
                        },
                    ),
                    _node(
                        "kms",
                        "KMS (FIPS 140-2)",
                        "aws-kms",
                        60,
                        340,
                        {
                            "config": {
                                "fips": "140-2",
                                "purpose": "Key management per FRD \u00a72.1.2.13",
                            }
                        },
                    ),
                    _node(
                        "ct",
                        "CloudTrail (Org)",
                        "aws-ct",
                        200,
                        340,
                        {
                            "config": {
                                "purpose": "API audit logging per FRD \u00a72.1.3.7",
                            }
                        },
                    ),
                    _node(
                        "sechub",
                        "Security Hub (SIEM)",
                        "aws-securityhub",
                        340,
                        340,
                        {
                            "config": {
                                "standards": ["NIST-800-53-v5"],
                                "purpose": "SIEM per FRD \u00a72.1.2.12",
                            }
                        },
                    ),
                    _node(
                        "config",
                        "AWS Config",
                        "aws-config",
                        200,
                        460,
                        {
                            "config": {
                                "purpose": "Compliance monitoring per FRD \u00a72.1.3.1",
                            }
                        },
                    ),
                ],
                "edges": [
                    _edge("vdms-vpc", "inspector", "Scanning", ""),
                    _edge("vdms-vpc", "ssm", "Management", ""),
                    _edge("vdms-vpc", "ad", "Identity", ""),
                    _edge("vdms-vpc", "kms", "Keys", ""),
                    _edge("vdms-vpc", "ct", "Audit", ""),
                    _edge("vdms-vpc", "sechub", "SIEM", ""),
                    _edge("inspector", "sechub", "Findings", ""),
                    _edge("ssm", "sechub", "Compliance", ""),
                    _edge("ct", "sechub", "Events", ""),
                    _edge("sechub", "config", "Compliance", ""),
                ],
            }
        ),
    },
    # 11 ─ SCCA TCCM Credential Management
    {
        "id": "snip-scca-tccm-credentials",
        "name": "SCCA TCCM Credential Management",
        "category": "SCCA",
        "description": (
            "Trusted Cloud Credential Manager (TCCM) building block per DISA FRD \u00a72.1.4. "
            "Includes centralized IAM/SSO (IAM Identity Center), RBAC with least-privilege "
            "permission sets, API audit trail (CloudTrail), credential rotation "
            "(Secrets Manager), and break-glass emergency access. "
            "Satisfies FRD requirements 2.1.4.1 through 2.1.4.6."
        ),
        "classification_level": "CUI",
        "impact_level": "IL5",
        "stig_controls": json.dumps(
            [
                "AC-2",
                "AC-6",
                "IA-2",
                "IA-5",
                "AU-2",
                "AU-12",
            ]
        ),
        "tags": json.dumps(["scca", "tccm", "iam", "sso", "rbac", "credentials", "audit", "break-glass", "frd-2.1.4"]),
        "graph_json": json.dumps(
            {
                "nodes": [
                    _node(
                        "idc",
                        "IAM Identity Center (SSO)",
                        "aws-idc",
                        200,
                        60,
                        {
                            "config": {
                                "purpose": "Centralized SSO/RBAC per FRD \u00a72.1.4.6",
                                "mfa": "enforced",
                            }
                        },
                    ),
                    _node(
                        "ad",
                        "Directory Service (CAC)",
                        "aws-ad",
                        60,
                        200,
                        {
                            "config": {
                                "federation": "SAML 2.0",
                                "purpose": "CAC/PIV authentication",
                            }
                        },
                    ),
                    _node(
                        "ct",
                        "CloudTrail (Audit)",
                        "aws-ct",
                        340,
                        200,
                        {
                            "config": {
                                "purpose": "Portal activity logging per FRD \u00a72.1.4.2",
                                "org_trail": True,
                            }
                        },
                    ),
                    _node(
                        "secrets",
                        "Secrets Manager",
                        "server",
                        60,
                        340,
                        {
                            "config": {
                                "purpose": "Credential rotation per FRD \u00a72.1.4.5",
                                "auto_rotate": True,
                            }
                        },
                    ),
                    _node(
                        "breakglass",
                        "Break-Glass Role",
                        "server",
                        340,
                        340,
                        {
                            "config": {
                                "purpose": "Emergency access per FRD \u00a72.1.4.6",
                                "requires_approval": True,
                            }
                        },
                    ),
                    _node(
                        "alerts",
                        "CloudWatch Alerts",
                        "server",
                        200,
                        460,
                        {
                            "config": {
                                "purpose": "Forward activity alerts per FRD \u00a72.1.4.3",
                            }
                        },
                    ),
                ],
                "edges": [
                    _edge("idc", "ad", "Federation", "SAML"),
                    _edge("idc", "ct", "Audit Trail", ""),
                    _edge("idc", "secrets", "Credential Mgmt", ""),
                    _edge("idc", "breakglass", "Emergency", ""),
                    _edge("ct", "alerts", "Alert Fwd", ""),
                    _edge("alerts", "idc", "Notification", ""),
                ],
            }
        ),
    },
    # 11 ─ Kubernetes Network Policy
    {
        "id": "snip-k8s-netpol",
        "name": "Kubernetes Network Policy",
        "category": "Container",
        "description": (
            "Kubernetes NetworkPolicy snippet showing default-deny with explicit "
            "allow rules between tiers. Ingress controller forwards to frontend pods, "
            "frontend allowed to backend, backend allowed to database."
        ),
        "classification_level": "CUI",
        "impact_level": "IL4",
        "stig_controls": json.dumps(
            [
                "AC-4",
                "SC-7",
                "SC-7(5)",
                "CM-7",
            ]
        ),
        "tags": json.dumps(["kubernetes", "k8s", "network-policy", "container", "micro-segmentation"]),
        "graph_json": json.dumps(
            {
                "nodes": [
                    _node("np-ingress", "Ingress Controller", "router", 300, 40),
                    _node("np-frontend", "Frontend Pod", "server", 300, 180),
                    _node("np-backend", "Backend Pod", "server", 300, 320),
                    _node("np-db", "Database Pod", "database", 300, 460),
                    _node("np-deny", "Deny-All Default Policy", "firewall", 550, 250),
                ],
                "edges": [
                    _edge("np-ingress", "np-frontend", "Allow Ingress", "HTTPS"),
                    _edge("np-frontend", "np-backend", "Allow Frontend→Backend", "gRPC"),
                    _edge("np-backend", "np-db", "Allow Backend→DB", "TCP/5432"),
                    _edge("np-deny", "np-frontend", "Default Deny", ""),
                    _edge("np-deny", "np-backend", "Default Deny", ""),
                    _edge("np-deny", "np-db", "Default Deny", ""),
                ],
            }
        ),
    },
    # 12 ─ PCI CDE Isolation Zone
    {
        "id": "snip-pci-cde-zone",
        "name": "PCI CDE Isolation Zone",
        "category": "Compliance",
        "description": (
            "Complete PCI-DSS Cardholder Data Environment micro-zone. "
            "Dedicated CDE firewall isolates POS devices, card processor, "
            "and HSM for cryptographic key management. All flows encrypted."
        ),
        "classification_level": "CUI",
        "impact_level": "IL4",
        "stig_controls": json.dumps(
            [
                "SC-7",
                "SC-8",
                "SC-8(1)",
                "SC-13",
                "SC-28",
            ]
        ),
        "tags": json.dumps(["pci-dss", "cde", "compliance", "hsm", "payment", "segmentation"]),
        "graph_json": json.dumps(
            {
                "nodes": [
                    _node("cde-fw", "CDE Firewall", "firewall", 300, 40),
                    _node("cde-sw", "CDE Switch", "switch-l3", 300, 180),
                    _node("pos-dev", "POS Device", "server", 100, 320),
                    _node("card-proc", "Card Processor", "server", 300, 320),
                    _node("hsm", "HSM", "server", 500, 320),
                ],
                "edges": [
                    _edge("cde-fw", "cde-sw", "CDE Boundary", ""),
                    _edge("cde-sw", "pos-dev", "POS Traffic", "TLS"),
                    _edge("cde-sw", "card-proc", "Transaction", "TLS"),
                    _edge("card-proc", "hsm", "Key Ops", "TLS"),
                    _edge("pos-dev", "card-proc", "Card Data", "TLS"),
                ],
            }
        ),
    },
    # 13 ─ HIPAA PHI Data Flow
    {
        "id": "snip-hipaa-phi-flow",
        "name": "HIPAA PHI Data Flow",
        "category": "Compliance",
        "description": (
            "HIPAA-compliant Protected Health Information data flow. "
            "All PHI flows encrypted end-to-end through an encryption gateway. "
            "Every access logged to audit logger for HIPAA audit trail requirements."
        ),
        "classification_level": "CUI",
        "impact_level": "IL4",
        "stig_controls": json.dumps(
            [
                "SC-8",
                "SC-8(1)",
                "SC-28",
                "AU-2",
                "AU-12",
                "AC-3",
            ]
        ),
        "tags": json.dumps(["hipaa", "phi", "compliance", "encryption", "audit", "healthcare"]),
        "graph_json": json.dumps(
            {
                "nodes": [
                    _node("ehr", "EHR System", "server", 100, 200),
                    _node("phi-db", "PHI Database", "database", 400, 200),
                    _node("enc-gw", "Encryption Gateway", "firewall", 250, 60),
                    _node("audit-log", "Audit Logger", "siem", 250, 340),
                ],
                "edges": [
                    _edge("ehr", "enc-gw", "PHI Request", "TLS"),
                    _edge("enc-gw", "phi-db", "Encrypted PHI", "TLS"),
                    _edge("ehr", "audit-log", "Access Log", "TLS"),
                    _edge("phi-db", "audit-log", "Query Log", "TLS"),
                    _edge("enc-gw", "audit-log", "Crypto Log", "TLS"),
                ],
            }
        ),
    },
    # 14 ─ PQC Hybrid Key Exchange
    {
        "id": "snip-pqc-hybrid",
        "name": "PQC Hybrid Key Exchange",
        "category": "Quantum",
        "description": (
            "Post-quantum hybrid key exchange snippet showing algorithm negotiation. "
            "Client connects to PQC-TLS terminator which negotiates ML-KEM with the key server "
            "or falls back to classical ECDH when PQC is unavailable."
        ),
        "classification_level": "CUI",
        "impact_level": "IL5",
        "stig_controls": json.dumps(
            [
                "SC-8",
                "SC-8(1)",
                "SC-13",
                "SC-12",
            ]
        ),
        "tags": json.dumps(["pqc", "post-quantum", "ml-kem", "hybrid-tls", "key-exchange", "quantum"]),
        "graph_json": json.dumps(
            {
                "nodes": [
                    _node("client", "Client", "server", 100, 200),
                    _node("pqc-tls", "PQC-TLS Terminator", "firewall", 300, 200),
                    _node("ml-kem-srv", "ML-KEM Key Server", "server", 500, 100),
                    _node("classical-fb", "Classical Fallback", "server", 500, 300),
                ],
                "edges": [
                    _edge("client", "pqc-tls", "TLS ClientHello", "Hybrid-TLS"),
                    _edge("pqc-tls", "ml-kem-srv", "PQC Negotiation", "ML-KEM-768"),
                    _edge("pqc-tls", "classical-fb", "ECDH Fallback", "X25519"),
                    _edge("ml-kem-srv", "pqc-tls", "Encapsulated Key", "ML-KEM-768"),
                ],
            }
        ),
    },
    # 15 ─ IoT Device Onboarding
    {
        "id": "snip-iot-onboarding",
        "name": "IoT Device Onboarding",
        "category": "IoT",
        "description": (
            "Secure IoT device provisioning flow. New device contacts bootstrap server, "
            "obtains certificate from CA, registers in device registry, "
            "and receives policy from policy engine before joining the network."
        ),
        "classification_level": "CUI",
        "impact_level": "IL4",
        "stig_controls": json.dumps(
            [
                "IA-3",
                "IA-5",
                "CM-2",
                "CM-8",
                "SC-17",
            ]
        ),
        "tags": json.dumps(["iot", "onboarding", "provisioning", "device-identity", "certificate", "bootstrap"]),
        "graph_json": json.dumps(
            {
                "nodes": [
                    _node("new-dev", "New IoT Device", "server", 100, 200),
                    _node("bootstrap", "Bootstrap Server", "server", 300, 100),
                    _node("ca", "Certificate Authority", "server", 500, 100),
                    _node("registry", "Device Registry", "database", 500, 300),
                    _node("policy", "Policy Engine", "server", 300, 300),
                ],
                "edges": [
                    _edge("new-dev", "bootstrap", "Discovery", "CoAP"),
                    _edge("bootstrap", "ca", "Cert Request", "TLS"),
                    _edge("ca", "new-dev", "Device Cert", "TLS"),
                    _edge("bootstrap", "registry", "Register", "TLS"),
                    _edge("registry", "policy", "Device Profile", ""),
                    _edge("policy", "new-dev", "Access Policy", "TLS"),
                ],
            }
        ),
    },
    # 16 ─ SCCA BCAP Network Pattern
    {
        "id": "snip-scca-bcap-network",
        "name": "SCCA BCAP Network Pattern",
        "category": "SCCA",
        "description": (
            "Minimal SCCA Boundary Cloud Access Point network pattern: BCAP firewall, "
            "BCAP router, AWS Network Firewall for deep-packet inspection, and Transit "
            "Gateway hub. Drop onto any GovCloud topology to add SCCA-compliant ingress."
        ),
        "classification_level": "CUI",
        "impact_level": "IL5",
        "stig_controls": json.dumps(
            [
                "SC-7",
                "SC-7(5)",
                "AC-4",
                "SI-4",
                "AU-2",
            ]
        ),
        "tags": json.dumps(["scca", "bcap", "dod", "govcloud", "transit-gateway"]),
        "graph_json": json.dumps(
            {
                "nodes": [
                    _node("bcap-fw", "BCAP FW", "firewall", 100, 100),
                    _node("bcap-rtr", "BCAP Router", "router", 300, 100),
                    _node("bcap-nfw", "Network Firewall", "aws-nfw", 500, 100),
                    _node("tgw", "Transit GW", "aws-tgw", 700, 100),
                ],
                "edges": [
                    _edge("bcap-fw", "bcap-rtr", "Filtered", ""),
                    _edge("bcap-rtr", "bcap-nfw", "Inspection", ""),
                    _edge("bcap-nfw", "tgw", "Forwarded", ""),
                ],
            }
        ),
    },
    # 17 ─ Hub-Spoke Network
    {
        "id": "snip-hub-spoke-network",
        "name": "Hub-Spoke Network",
        "category": "Landing Zone",
        "description": (
            "Minimal hub-spoke network pattern with Azure Firewall hub and two spoke VNets. "
            "Use as a starting point for any hub-spoke landing zone design."
        ),
        "classification_level": "CUI",
        "impact_level": "IL4",
        "stig_controls": json.dumps(
            [
                "SC-7",
                "AC-4",
                "SC-7(5)",
            ]
        ),
        "tags": json.dumps(["hub-spoke", "azure", "firewall", "landing-zone"]),
        "graph_json": json.dumps(
            {
                "nodes": [
                    _node("hub-fw", "Hub Firewall", "az-fw", 300, 100),
                    _node("spoke1", "Spoke 1", "az-vnet", 100, 280),
                    _node("spoke2", "Spoke 2", "az-vnet", 500, 280),
                ],
                "edges": [
                    _edge("spoke1", "hub-fw", "Spoke Peering", ""),
                    _edge("spoke2", "hub-fw", "Spoke Peering", ""),
                ],
            }
        ),
    },
    # ── AWS Direct Connect GovCloud (IL4/IL5) ─────────────────────────────────
    {
        "id": "snip-aws-dx-govcloud",
        "name": "AWS Direct Connect — GovCloud (IL4/IL5)",
        "category": "Hybrid Cloud",
        "description": (
            "AWS Direct Connect to GovCloud (US) region for CUI workloads. "
            "Includes FIPS 140-2 validated encryption (MACsec or IPsec overlay), "
            "dedicated 10 Gbps connection via GovCloud-approved colocation, "
            "BGP with MD5 authentication, and BFD fast failover. "
            "Compliant with FedRAMP High, DoD IL4/IL5, and NIST 800-53 Rev 5."
        ),
        "classification_level": "CUI",
        "impact_level": "IL5",
        "stig_controls": json.dumps(
            [
                "SC-7",
                "SC-7(4)",
                "SC-8",
                "SC-8(1)",
                "SC-13",
                "AC-4",
                "CA-3",
                "AU-2",
                "AU-12",
                "SI-4",
                "IA-2",
                "IA-7",
            ]
        ),
        "tags": json.dumps(
            [
                "aws",
                "direct-connect",
                "govcloud",
                "il4",
                "il5",
                "cui",
                "fips",
                "fedramp-high",
                "macsec",
                "bgp",
                "sop",
                "runbook",
            ]
        ),
        "graph_json": json.dumps(
            {
                "nodes": [
                    _node(
                        "cust-rtr",
                        "CE Router (FIPS)",
                        "router",
                        100,
                        200,
                        {
                            "config": {
                                "classification": "CUI",
                                "stig_baseline": "DISA Network STIG V3R9",
                                "fips_mode": "FIPS 140-2 Level 2",
                                "role": "Customer edge — BGP to AWS GovCloud DX (ASN 7224)",
                                "macsec": "MACsec 256-bit AES-GCM (if supported by DX location)",
                                "notes": "If MACsec unavailable, use IPsec VPN overlay for SC-8(1) encryption",
                            }
                        },
                    ),
                    _node(
                        "dx-port",
                        "AWS DX Port (GovCloud)",
                        "odf",
                        340,
                        200,
                        {
                            "config": {
                                "classification": "CUI",
                                "role": "DX port at GovCloud-approved colocation",
                                "locations": "Equinix DC, CoreSite VA, QTS (ITAR/GovCloud approved)",
                                "speed": "1 Gbps / 10 Gbps dedicated",
                            }
                        },
                    ),
                    _node(
                        "dx-rtr",
                        "AWS DX Router (GovCloud)",
                        "router",
                        540,
                        200,
                        {
                            "config": {
                                "classification": "CUI",
                                "owner": "AWS (managed)",
                                "bgp_asn": "7224",
                                "notes": "GovCloud DX endpoints are physically in US only",
                            }
                        },
                    ),
                    _node(
                        "vgw",
                        "VGW / TGW (GovCloud)",
                        "router",
                        540,
                        380,
                        {
                            "config": {
                                "classification": "CUI",
                                "role": "Virtual Private Gateway or Transit Gateway in us-gov-west-1",
                                "notes": "TGW recommended for multi-VPC GovCloud architectures",
                            }
                        },
                    ),
                    _node(
                        "vpc",
                        "GovCloud VPC",
                        "aws-vpc",
                        540,
                        520,
                        {
                            "config": {
                                "classification": "CUI",
                                "role": "GovCloud VPC (us-gov-west-1 / us-gov-east-1)",
                                "notes": "FedRAMP High authorized, ITAR compliant",
                            }
                        },
                    ),
                    _node(
                        "on-prem",
                        "On-Prem (CUI Enclave)",
                        "switch-l3",
                        100,
                        380,
                        {
                            "config": {
                                "classification": "CUI",
                                "stig_baseline": "DISA Network STIG V3R9",
                                "role": "CUI-authorized on-premises network",
                            }
                        },
                    ),
                ],
                "edges": [
                    _edge("on-prem", "cust-rtr", "Uplink (encrypted)", "OSPF"),
                    _edge("cust-rtr", "dx-port", "Dedicated DX (MACsec)", "802.1Q"),
                    _edge("dx-port", "dx-rtr", "Internal", ""),
                    _edge("dx-rtr", "vgw", "Private VIF", "eBGP + MD5"),
                    _edge("vgw", "vpc", "Route Propagation", ""),
                ],
            }
        ),
    },
    # ── Azure ExpressRoute Government (IL4/IL5) ──────────────────────────────
    {
        "id": "snip-azure-er-gov",
        "name": "Azure ExpressRoute — Government (IL4/IL5)",
        "category": "Hybrid Cloud",
        "description": (
            "Azure ExpressRoute to Azure Government regions for CUI workloads. "
            "Includes FIPS 140-2 validated encryption (MACsec on ER Direct or IPsec), "
            "dual primary/secondary circuits for 99.95% SLA, BGP with MD5, BFD. "
            "Compliant with FedRAMP High, DoD IL4/IL5, and NIST 800-53 Rev 5."
        ),
        "classification_level": "CUI",
        "impact_level": "IL5",
        "stig_controls": json.dumps(
            [
                "SC-7",
                "SC-7(4)",
                "SC-8",
                "SC-8(1)",
                "SC-13",
                "AC-4",
                "CA-3",
                "AU-2",
                "AU-12",
                "SI-4",
                "IA-2",
                "IA-7",
            ]
        ),
        "tags": json.dumps(
            [
                "azure",
                "expressroute",
                "government",
                "il4",
                "il5",
                "cui",
                "fips",
                "fedramp-high",
                "macsec",
                "bgp",
                "sop",
                "runbook",
            ]
        ),
        "graph_json": json.dumps(
            {
                "nodes": [
                    _node(
                        "ce-pri",
                        "CE Router Primary (FIPS)",
                        "router",
                        100,
                        160,
                        {
                            "config": {
                                "classification": "CUI",
                                "stig_baseline": "DISA Network STIG V3R9",
                                "fips_mode": "FIPS 140-2 Level 2",
                                "role": "Primary CE — BGP to MSEE (Azure ASN 12076)",
                                "macsec": "MACsec on ER Direct (10/100 Gbps) or IPsec overlay",
                            }
                        },
                    ),
                    _node(
                        "ce-sec",
                        "CE Router Secondary (FIPS)",
                        "router",
                        100,
                        340,
                        {
                            "config": {
                                "classification": "CUI",
                                "stig_baseline": "DISA Network STIG V3R9",
                                "fips_mode": "FIPS 140-2 Level 2",
                                "role": "Secondary CE — required for Azure SLA",
                            }
                        },
                    ),
                    _node(
                        "msee-pri",
                        "MSEE Primary (AzGov)",
                        "router",
                        400,
                        160,
                        {
                            "config": {
                                "classification": "CUI",
                                "owner": "Microsoft (managed)",
                                "bgp_asn": "12076",
                                "region": "Azure Government (USGov Virginia / USGov Arizona)",
                            }
                        },
                    ),
                    _node(
                        "msee-sec",
                        "MSEE Secondary (AzGov)",
                        "router",
                        400,
                        340,
                        {
                            "config": {
                                "classification": "CUI",
                                "owner": "Microsoft (managed)",
                                "bgp_asn": "12076",
                            }
                        },
                    ),
                    _node(
                        "er-gw",
                        "ER Gateway (AzGov)",
                        "router",
                        620,
                        250,
                        {
                            "config": {
                                "classification": "CUI",
                                "role": "Zone-redundant ER Gateway (ErGw1Az/ErGw2Az/ErGw3Az)",
                                "region": "USGov Virginia / USGov Arizona",
                            }
                        },
                    ),
                    _node(
                        "vnet",
                        "AzGov VNet",
                        "az-vnet",
                        800,
                        250,
                        {
                            "config": {
                                "classification": "CUI",
                                "role": "Azure Government VNet (FedRAMP High, IL5 authorized)",
                            }
                        },
                    ),
                    _node(
                        "on-prem",
                        "On-Prem (CUI Enclave)",
                        "switch-l3",
                        100,
                        500,
                        {
                            "config": {
                                "classification": "CUI",
                                "stig_baseline": "DISA Network STIG V3R9",
                            }
                        },
                    ),
                ],
                "edges": [
                    _edge("on-prem", "ce-pri", "Uplink", "OSPF"),
                    _edge("on-prem", "ce-sec", "Uplink", "OSPF"),
                    _edge("ce-pri", "msee-pri", "Primary (MACsec/IPsec)", "eBGP + MD5"),
                    _edge("ce-sec", "msee-sec", "Secondary (MACsec/IPsec)", "eBGP + MD5"),
                    _edge("msee-pri", "er-gw", "Private Peering", ""),
                    _edge("msee-sec", "er-gw", "Private Peering", ""),
                    _edge("er-gw", "vnet", "Gateway Connection", ""),
                ],
            }
        ),
    },
    # ── GCP Cloud Interconnect Government (IL4/IL5) ──────────────────────────
    {
        "id": "snip-gcp-interconnect-gov",
        "name": "GCP Cloud Interconnect — Assured Workloads (IL4/IL5)",
        "category": "Hybrid Cloud",
        "description": (
            "GCP Dedicated Interconnect to Assured Workloads environment for CUI. "
            "Includes FIPS 140-2 validated encryption via HA VPN overlay on Interconnect, "
            "Cloud Router with custom route advertisements, and BFD-equivalent health checks. "
            "Compliant with FedRAMP High, DoD IL4/IL5, and NIST 800-53 Rev 5. "
            "Note: GCP uses Assured Workloads (not a separate region) for IL4/IL5 compliance."
        ),
        "classification_level": "CUI",
        "impact_level": "IL5",
        "stig_controls": json.dumps(
            [
                "SC-7",
                "SC-7(4)",
                "SC-8",
                "SC-8(1)",
                "SC-13",
                "AC-4",
                "CA-3",
                "AU-2",
                "AU-12",
                "SI-4",
                "IA-2",
                "IA-7",
            ]
        ),
        "tags": json.dumps(
            [
                "gcp",
                "cloud-interconnect",
                "assured-workloads",
                "il4",
                "il5",
                "cui",
                "fips",
                "fedramp-high",
                "bgp",
                "sop",
                "runbook",
            ]
        ),
        "graph_json": json.dumps(
            {
                "nodes": [
                    _node(
                        "cust-rtr",
                        "CE Router (FIPS)",
                        "router",
                        100,
                        200,
                        {
                            "config": {
                                "classification": "CUI",
                                "stig_baseline": "DISA Network STIG V3R9",
                                "fips_mode": "FIPS 140-2 Level 2",
                                "role": "CE router — BGP to Cloud Router (Google ASN 16550)",
                                "encryption": "HA VPN over Interconnect (IPsec) for SC-8(1) compliance",
                                "notes": "GCP Interconnect does not natively support MACsec; use HA VPN overlay",
                            }
                        },
                    ),
                    _node(
                        "gcp-port",
                        "GCP Interconnect Port",
                        "odf",
                        340,
                        200,
                        {
                            "config": {
                                "classification": "CUI",
                                "role": "Google peering edge at Assured Workloads-approved facility",
                                "speed": "10 Gbps / 100 Gbps dedicated",
                            }
                        },
                    ),
                    _node(
                        "gcp-edge",
                        "Google Peering Edge",
                        "router",
                        540,
                        200,
                        {
                            "config": {
                                "classification": "CUI",
                                "owner": "Google (managed)",
                                "bgp_asn": "16550",
                            }
                        },
                    ),
                    _node(
                        "ha-vpn",
                        "HA VPN Gateway",
                        "fips-140-l3",
                        540,
                        350,
                        {
                            "config": {
                                "classification": "CUI",
                                "role": "HA VPN over Interconnect — FIPS-validated IPsec encryption",
                                "encryption": "AES-256-GCM, IKEv2, FIPS 140-2 validated",
                                "notes": "Required for SC-8(1) transit encryption on GCP Interconnect",
                            }
                        },
                    ),
                    _node(
                        "cloud-rtr",
                        "Cloud Router (Assured)",
                        "router",
                        540,
                        500,
                        {
                            "config": {
                                "classification": "CUI",
                                "role": "GCP Cloud Router in Assured Workloads folder",
                            }
                        },
                    ),
                    _node(
                        "vpc",
                        "Assured Workloads VPC",
                        "gcp-vpc",
                        740,
                        500,
                        {
                            "config": {
                                "classification": "CUI",
                                "role": "VPC in Assured Workloads folder (IL4/IL5)",
                                "notes": "Org policy restricts to US regions, CMEK required",
                            }
                        },
                    ),
                    _node(
                        "on-prem",
                        "On-Prem (CUI Enclave)",
                        "switch-l3",
                        100,
                        400,
                        {
                            "config": {
                                "classification": "CUI",
                                "stig_baseline": "DISA Network STIG V3R9",
                            }
                        },
                    ),
                ],
                "edges": [
                    _edge("on-prem", "cust-rtr", "Uplink", "OSPF"),
                    _edge("cust-rtr", "gcp-port", "Dedicated Interconnect", "802.1Q"),
                    _edge("gcp-port", "gcp-edge", "Internal", ""),
                    _edge("gcp-edge", "ha-vpn", "HA VPN over Interconnect", "IPsec"),
                    _edge("ha-vpn", "cloud-rtr", "Encrypted Tunnel", "eBGP"),
                    _edge("cloud-rtr", "vpc", "Route Propagation", ""),
                ],
            }
        ),
    },
    # ── OCI FastConnect Government (IL4/IL5) ─────────────────────────────────
    {
        "id": "snip-oci-fc-gov",
        "name": "OCI FastConnect — Government Cloud (IL4/IL5)",
        "category": "Hybrid Cloud",
        "description": (
            "OCI FastConnect to Oracle Government Cloud for CUI workloads. "
            "Includes FIPS 140-2 encryption via IPsec VPN overlay, "
            "DRG v2 with advanced route distribution, and private peering. "
            "Compliant with FedRAMP High, DoD IL4/IL5, DISA STIG, and NIST 800-53 Rev 5. "
            "OCI Government Cloud regions: us-langley-1 (IC Gov), us-luke-1 (DoD), "
            "us-gov-ashburn-1, us-gov-chicago-1, us-gov-phoenix-1."
        ),
        "classification_level": "CUI",
        "impact_level": "IL5",
        "stig_controls": json.dumps(
            [
                "SC-7",
                "SC-7(4)",
                "SC-8",
                "SC-8(1)",
                "SC-13",
                "AC-4",
                "CA-3",
                "AU-2",
                "AU-12",
                "SI-4",
                "IA-2",
                "IA-7",
            ]
        ),
        "tags": json.dumps(
            [
                "oci",
                "oracle",
                "fastconnect",
                "government",
                "il4",
                "il5",
                "cui",
                "fips",
                "fedramp-high",
                "bgp",
                "drg",
                "sop",
                "runbook",
            ]
        ),
        "graph_json": json.dumps(
            {
                "nodes": [
                    _node(
                        "cust-rtr",
                        "CE Router (FIPS)",
                        "router",
                        100,
                        200,
                        {
                            "config": {
                                "classification": "CUI",
                                "stig_baseline": "DISA Network STIG V3R9",
                                "fips_mode": "FIPS 140-2 Level 2",
                                "role": "CE router — BGP to Oracle Gov edge (ASN 31898)",
                                "encryption": "IPsec VPN overlay for SC-8(1) transit encryption",
                            }
                        },
                    ),
                    _node(
                        "fc-port",
                        "OCI FC Port (Gov)",
                        "odf",
                        340,
                        200,
                        {
                            "config": {
                                "classification": "CUI",
                                "role": "FastConnect port at Gov-approved colocation",
                                "speed": "1 Gbps / 10 Gbps",
                                "locations": "Equinix DC, CoreSite VA (FedRAMP authorized)",
                            }
                        },
                    ),
                    _node(
                        "oci-edge",
                        "Oracle Gov Edge Router",
                        "router",
                        540,
                        200,
                        {
                            "config": {
                                "classification": "CUI",
                                "owner": "Oracle (managed)",
                                "bgp_asn": "31898",
                            }
                        },
                    ),
                    _node(
                        "ipsec-gw",
                        "IPsec VPN Overlay",
                        "fips-140-l3",
                        540,
                        350,
                        {
                            "config": {
                                "classification": "CUI",
                                "role": "CPE IPsec tunnel over FastConnect for FIPS encryption",
                                "encryption": "AES-256-GCM, IKEv2, FIPS 140-2",
                                "notes": "OCI FastConnect does not natively encrypt; use IPsec overlay",
                            }
                        },
                    ),
                    _node(
                        "drg",
                        "DRG v2 (Gov Cloud)",
                        "router",
                        540,
                        500,
                        {
                            "config": {
                                "classification": "CUI",
                                "role": "Dynamic Routing Gateway in OCI Gov region",
                                "features": "Route distribution policies, multi-VCN attachment",
                                "region": "us-gov-ashburn-1 / us-gov-phoenix-1",
                            }
                        },
                    ),
                    _node(
                        "vcn",
                        "Gov Cloud VCN",
                        "oci-vcn",
                        740,
                        500,
                        {
                            "config": {
                                "classification": "CUI",
                                "role": "VCN in OCI Government Cloud (FedRAMP High, IL5)",
                                "notes": "Security lists + NSGs must restrict to CUI-authorized CIDRs",
                            }
                        },
                    ),
                    _node(
                        "on-prem",
                        "On-Prem (CUI Enclave)",
                        "switch-l3",
                        100,
                        400,
                        {
                            "config": {
                                "classification": "CUI",
                                "stig_baseline": "DISA Network STIG V3R9",
                            }
                        },
                    ),
                ],
                "edges": [
                    _edge("on-prem", "cust-rtr", "Uplink", "OSPF"),
                    _edge("cust-rtr", "fc-port", "FastConnect (Dedicated)", "802.1Q"),
                    _edge("fc-port", "oci-edge", "Internal", ""),
                    _edge("oci-edge", "ipsec-gw", "IPsec over FastConnect", "IPsec"),
                    _edge("ipsec-gw", "drg", "Encrypted Private VC", "eBGP"),
                    _edge("drg", "vcn", "DRG Attachment", ""),
                ],
            }
        ),
    },
    # ── DWDM Ring + MPLS Overlay — CUI/IL5 Compliant ────────────────────────
    {
        "id": "snip-dwdm-mpls-cui",
        "name": "DWDM Ring + MPLS Overlay — CUI (IL4/IL5)",
        "category": "Transport / Optical",
        "description": (
            "DWDM metro ring with MPLS L3VPN overlay for CUI transport. "
            "Layer 1: DWDM 96-channel ring with BLSR protection. "
            "Layer 2: KG-series Type 1 encryptors (FIPS 140-2/3) at every handoff. "
            "Layer 3: MPLS PE routers with VRF isolation, MP-BGP, BFD. "
            "Compliant with NIST 800-53, DISA STIG, FedRAMP High, DoD IL4/IL5."
        ),
        "classification_level": "CUI",
        "impact_level": "IL5",
        "stig_controls": json.dumps(
            [
                "SC-7",
                "SC-7(4)",
                "SC-8",
                "SC-8(1)",
                "SC-12",
                "SC-13",
                "SC-28",
                "AC-4",
                "CA-3",
                "AU-2",
                "AU-12",
                "SI-4",
                "IA-2",
                "IA-7",
                "CM-6",
                "CM-7",
            ]
        ),
        "tags": json.dumps(
            [
                "dwdm",
                "mpls",
                "ring",
                "cui",
                "il4",
                "il5",
                "fips",
                "kg",
                "type-1",
                "blsr",
                "l3vpn",
                "stig",
                "fedramp-high",
            ]
        ),
        "graph_json": json.dumps(
            {
                "nodes": [
                    # ── DWDM Ring Layer (4 ring nodes) ──
                    _node(
                        "roadm-hub",
                        "ROADM Hub (Site A)",
                        "roadm",
                        400,
                        100,
                        {
                            "config": {
                                "classification": "CUI",
                                "role": "Primary ROADM hub — 96-ch C+L band",
                                "degree": "4-degree, WSS",
                                "stig_baseline": "DISA Network STIG V3R9",
                            }
                        },
                    ),
                    _node(
                        "oadm-b",
                        "OADM (Site B)",
                        "oadm",
                        700,
                        300,
                        {
                            "config": {
                                "classification": "CUI",
                                "role": "OADM access — 16-ch add/drop",
                            }
                        },
                    ),
                    _node(
                        "roadm-c",
                        "ROADM (Site C)",
                        "roadm",
                        400,
                        500,
                        {
                            "config": {
                                "classification": "CUI",
                                "role": "Secondary ROADM — DR site",
                            }
                        },
                    ),
                    _node(
                        "oadm-d",
                        "OADM (Site D)",
                        "oadm",
                        100,
                        300,
                        {
                            "config": {
                                "classification": "CUI",
                                "role": "OADM access — 16-ch add/drop",
                            }
                        },
                    ),
                    # EDFA per span
                    _node("edfa-ab", "EDFA A-B", "edfa", 580, 180),
                    _node("edfa-bc", "EDFA B-C", "edfa", 580, 420),
                    _node("edfa-cd", "EDFA C-D", "edfa", 220, 420),
                    _node("edfa-da", "EDFA D-A", "edfa", 220, 180),
                    # ── KG Encryption Layer ──
                    _node(
                        "kg-a",
                        "KG-250 (Site A)",
                        "fips-140-l3",
                        400,
                        210,
                        {
                            "config": {
                                "classification": "CUI",
                                "role": "NSA Type 1 encryptor — DWDM to MPLS handoff",
                                "model": "KG-250 (100G inline)",
                                "fips_mode": "FIPS 140-3 Level 3",
                                "stig_baseline": "DISA HAIPE STIG",
                                "encryption": "Suite B / AES-256, Type 1",
                            }
                        },
                    ),
                    _node(
                        "kg-b",
                        "KG-175D (Site B)",
                        "fips-140-l3",
                        600,
                        300,
                        {
                            "config": {
                                "classification": "CUI",
                                "model": "KG-175D (TACLANE, 1-10G)",
                                "fips_mode": "FIPS 140-2 Level 2",
                            }
                        },
                    ),
                    _node(
                        "kg-c",
                        "KG-250 (Site C)",
                        "fips-140-l3",
                        400,
                        390,
                        {
                            "config": {
                                "classification": "CUI",
                                "model": "KG-250 (100G inline)",
                                "fips_mode": "FIPS 140-3 Level 3",
                            }
                        },
                    ),
                    _node(
                        "kg-d",
                        "KG-175D (Site D)",
                        "fips-140-l3",
                        200,
                        300,
                        {
                            "config": {
                                "classification": "CUI",
                                "model": "KG-175D (TACLANE, 1-10G)",
                                "fips_mode": "FIPS 140-2 Level 2",
                            }
                        },
                    ),
                    # ── MPLS PE Layer ──
                    _node(
                        "pe-a",
                        "MPLS PE-A",
                        "mpls-pe",
                        400,
                        310,
                        {
                            "config": {
                                "classification": "CUI",
                                "stig_baseline": "DISA Network STIG V3R9",
                                "role": "MPLS PE — BGP RR client, LDP, VRF",
                                "protocols": "MP-BGP, LDP, OSPF (area 0), BFD",
                            }
                        },
                    ),
                    _node(
                        "pe-b",
                        "MPLS PE-B",
                        "mpls-pe",
                        500,
                        300,
                        {
                            "config": {
                                "classification": "CUI",
                                "stig_baseline": "DISA Network STIG V3R9",
                            }
                        },
                    ),
                    _node(
                        "pe-c",
                        "MPLS PE-C",
                        "mpls-pe",
                        400,
                        500,
                        {
                            "config": {
                                "classification": "CUI",
                                "stig_baseline": "DISA Network STIG V3R9",
                            }
                        },
                    ),
                    _node(
                        "pe-d",
                        "MPLS PE-D",
                        "mpls-pe",
                        300,
                        300,
                        {
                            "config": {
                                "classification": "CUI",
                                "stig_baseline": "DISA Network STIG V3R9",
                            }
                        },
                    ),
                    _node(
                        "rr",
                        "Route Reflector",
                        "route-reflector",
                        400,
                        400,
                        {
                            "config": {
                                "classification": "CUI",
                                "role": "MP-BGP Route Reflector for VPNv4",
                                "stig_baseline": "DISA Network STIG V3R9",
                            }
                        },
                    ),
                ],
                "edges": [
                    # Ring
                    _edge("roadm-hub", "edfa-ab", "East λ (working)", "DWDM"),
                    _edge("edfa-ab", "oadm-b", "Amplified", ""),
                    _edge("oadm-b", "edfa-bc", "Express λ", "DWDM"),
                    _edge("edfa-bc", "roadm-c", "Amplified", ""),
                    _edge("roadm-c", "edfa-cd", "West λ", "DWDM"),
                    _edge("edfa-cd", "oadm-d", "Amplified", ""),
                    _edge("oadm-d", "edfa-da", "Express λ", "DWDM"),
                    _edge("edfa-da", "roadm-hub", "Ring close", ""),
                    # KG encryption
                    _edge("roadm-hub", "kg-a", "100G λ handoff", ""),
                    _edge("oadm-b", "kg-b", "10G λ handoff", ""),
                    _edge("roadm-c", "kg-c", "100G λ handoff", ""),
                    _edge("oadm-d", "kg-d", "10G λ handoff", ""),
                    # KG to MPLS PE
                    _edge("kg-a", "pe-a", "Encrypted 100G", "IPsec Type1"),
                    _edge("kg-b", "pe-b", "Encrypted 10G", "IPsec Type1"),
                    _edge("kg-c", "pe-c", "Encrypted 100G", "IPsec Type1"),
                    _edge("kg-d", "pe-d", "Encrypted 10G", "IPsec Type1"),
                    # MPLS core
                    _edge("pe-a", "rr", "MP-BGP (VPNv4)", "iBGP"),
                    _edge("pe-b", "rr", "MP-BGP (VPNv4)", "iBGP"),
                    _edge("pe-c", "rr", "MP-BGP (VPNv4)", "iBGP"),
                    _edge("pe-d", "rr", "MP-BGP (VPNv4)", "iBGP"),
                ],
            }
        ),
    },
]


def init_db():
    conn = get_connection()
    try:
        if _NC_BACKEND == "postgresql":
            # PostgreSQL: execute each statement individually
            # ICDEV's StorageConnection translates SQL automatically
            for stmt in SCHEMA.split(";"):
                stmt = stmt.strip()
                if stmt and not stmt.startswith("--"):
                    try:
                        conn.execute(stmt)
                    except Exception:
                        pass  # table/index already exists
            conn.commit()
            # PG audit immutability triggers (PL/pgSQL syntax)
            try:
                conn.execute("""
                    CREATE OR REPLACE FUNCTION nc_audit_immutable()
                    RETURNS TRIGGER AS $$
                    BEGIN
                        RAISE EXCEPTION 'Audit records are immutable — NIST AU-6';
                    END;
                    $$ LANGUAGE plpgsql
                """)
                conn.execute("""
                    DROP TRIGGER IF EXISTS nc_audit_no_update ON nc_audit
                """)
                conn.execute("""
                    CREATE TRIGGER nc_audit_no_update
                    BEFORE UPDATE ON nc_audit
                    FOR EACH ROW EXECUTE FUNCTION nc_audit_immutable()
                """)
                conn.execute("""
                    DROP TRIGGER IF EXISTS nc_audit_no_delete ON nc_audit
                """)
                conn.execute("""
                    CREATE TRIGGER nc_audit_no_delete
                    BEFORE DELETE ON nc_audit
                    FOR EACH ROW EXECUTE FUNCTION nc_audit_immutable()
                """)
                conn.commit()
            except Exception:
                pass  # triggers may already exist
            # Repair any string CHECK constraints on pre-existing PG tables that
            # have drifted from the Python constants (CREATE TABLE IF NOT EXISTS
            # can't fix a constraint on a table that already exists). No-op on
            # fresh installs where every constraint was just derived. Best-effort.
            try:
                repair_check_constraints(conn)
            except Exception:
                pass
            print("[init_db] Schema created (PostgreSQL)", file=sys.stderr)
        else:
            # SQLite: executescript for all-at-once
            conn.executescript(SCHEMA)
            # SQLite audit immutability triggers
            try:
                conn.executescript("""
                    CREATE TRIGGER IF NOT EXISTS nc_audit_no_update
                    BEFORE UPDATE ON nc_audit
                    BEGIN
                        SELECT RAISE(ABORT, 'Audit records are immutable');
                    END;
                    CREATE TRIGGER IF NOT EXISTS nc_audit_no_delete
                    BEFORE DELETE ON nc_audit
                    BEGIN
                        SELECT RAISE(ABORT, 'Audit records cannot be deleted');
                    END;
                """)
            except Exception:
                pass
            conn.commit()
            print(f"[init_db] Schema created at {DB_PATH}", file=sys.stderr)

        # Migration: add columns to existing tables if missing
        _migrations = [
            ("nc_audit", "user_id", "TEXT DEFAULT ''"),
            ("nc_audit", "classification", "TEXT DEFAULT 'CUI // SP-CTI'"),
            ("nc_backups", "file_hash", "TEXT"),
            # P3: per-topology assignment
            ("nc_project_topologies", "assignee", "TEXT DEFAULT ''"),
            ("nc_review_boards", "is_optional", "INTEGER DEFAULT 0"),
            ("nc_project_milestones", "predecessor_id", "TEXT"),
            ("nc_ipam_blocks", "address_family", "TEXT DEFAULT 'ipv4'"),
            ("nc_ipam_blocks", "gateway_v6", "TEXT"),
            # NetBox integration tables (added via schema above; migrations cover pre-existing DBs)
            # TFW-01: Traffic flow walkthrough tables — new columns on existing tables
            ("nc_traffic_flows", "description", "TEXT DEFAULT ''"),
            ("nc_traffic_flows", "src_zone", "TEXT DEFAULT ''"),
            ("nc_traffic_flows", "dst_zone", "TEXT DEFAULT ''"),
            ("nc_traffic_flows", "app_type", "TEXT DEFAULT 'custom'"),
            ("nc_traffic_flows", "protocols", "TEXT DEFAULT '[]'"),
            ("nc_traffic_flows", "path_nodes", "TEXT DEFAULT '[]'"),
            ("nc_traffic_flows", "updated_at", "TEXT DEFAULT CURRENT_TIMESTAMP"),
            ("nc_security_domain_policies", "domain_label", "TEXT DEFAULT ''"),
            ("nc_security_domain_policies", "security_policy", "TEXT DEFAULT '{}'"),
            ("nc_security_domain_policies", "routing_policy", "TEXT DEFAULT '{}'"),
            ("nc_security_domain_policies", "vpn_policy", "TEXT DEFAULT '{}'"),
            ("nc_security_domain_policies", "updated_at", "TEXT DEFAULT CURRENT_TIMESTAMP"),
            # Connectivity: csp column on ndc_sops for SOP library CSP filter
            ("ndc_sops", "csp", "TEXT DEFAULT 'multi'"),
            # NDC↔Migration integration: phase lifecycle metadata
            ("nc_migration_phases", "classification", "TEXT DEFAULT 'CUI'"),
            ("nc_migration_phases", "impact_level", "TEXT DEFAULT 'IL4'"),
            ("nc_migration_phases", "properties_json", "TEXT DEFAULT '{}'"),
            # NDC↔Migration integration: traffic flow ↔ phase link
            ("nc_traffic_flows", "phase_id", "TEXT"),
            # Partner registry: add partner_id + approval columns to nc_peering_agreements
            ("nc_peering_agreements", "partner_id", "TEXT"),
            ("nc_peering_agreements", "approver_name", "TEXT DEFAULT ''"),
            ("nc_peering_agreements", "approver_role", "TEXT DEFAULT ''"),
            ("nc_peering_agreements", "approved_at", "TEXT DEFAULT ''"),
            # NDC AI-assisted migration: COA selection + feedback
            ("nc_projects", "selected_coa", "INTEGER DEFAULT 0"),
            ("nc_projects", "coa_feedback", "TEXT DEFAULT ''"),
            ("nc_projects", "coa_json", "TEXT DEFAULT '{}'"),
        ]
        for table, col, coltype in _migrations:
            try:
                conn.execute(f"SELECT {col} FROM {table} LIMIT 1")  # nosec B608 -- table/column names are internal constants, not user input
            except Exception:
                try:
                    conn.execute(f"ALTER TABLE {table} ADD COLUMN {col} {coltype}")
                    conn.commit()
                    print(f"[init_db] Migrated: added {col} to {table}", file=sys.stderr)
                except Exception:
                    pass  # Column might already exist with different syntax

        # ndc-fix-03: guarantee the UNIQUE index that backs
        # save_consolidation()'s ON CONFLICT(topo_id) upsert. Fresh installs get
        # it from the column-level UNIQUE in the CREATE TABLE above; this
        # retrofits pre-existing databases created before that constraint
        # shipped, so the upsert can never silently no-op. A UNIQUE index is a
        # valid ON CONFLICT arbiter in both SQLite and PostgreSQL, and
        # CREATE UNIQUE INDEX IF NOT EXISTS is idempotent on both backends.
        try:
            conn.execute(
                "CREATE UNIQUE INDEX IF NOT EXISTS ux_nc_consolidation_topo_id "
                "ON nc_consolidation_analysis(topo_id)"
            )
            conn.commit()
        except Exception:
            # Missing table (not yet created) or duplicate topo_id rows on a
            # legacy DB — best-effort; the column-level UNIQUE still covers
            # fresh installs.
            pass

        # Seed templates (upsert — inserts new templates even if some already exist)
        cur = conn.cursor()
        cur.execute("SELECT COUNT(*) FROM nc_templates")
        count = cur.fetchone()[0]
        added = 0
        for t in TEMPLATES:
            cur.execute("SELECT 1 FROM nc_templates WHERE id=%s", (t["id"],))
            if not cur.fetchone():
                conn.execute(
                    "INSERT INTO nc_templates (id, name, category, description, graph_json, tags) VALUES (?,?,?,?,?,?)",
                    (t["id"], t["name"], t["category"], t["description"], t["graph_json"], t["tags"]),
                )
                added += 1
        if added:
            conn.commit()
            print(f"[init_db] Seeded {added} new templates (total: {count + added}).", file=sys.stderr)
        else:
            print(f"[init_db] All {count} templates up to date.", file=sys.stderr)

        # Seed enclave snippets (upsert — inserts new snippets even if some already exist)
        snip_count = conn.execute("SELECT COUNT(*) FROM nc_enclave_snippets").fetchone()[0]
        snip_added = 0
        for s in ENCLAVE_SNIPPETS:
            exists = conn.execute("SELECT 1 FROM nc_enclave_snippets WHERE id=?", (s["id"],)).fetchone()
            if not exists:
                conn.execute(
                    "INSERT INTO nc_enclave_snippets "
                    "(id, name, category, description, classification_level, impact_level, "
                    " graph_json, stig_controls, tags) "
                    "VALUES (?,?,?,?,?,?,?,?,?)",
                    (
                        s["id"],
                        s["name"],
                        s["category"],
                        s["description"],
                        s["classification_level"],
                        s["impact_level"],
                        s["graph_json"],
                        s["stig_controls"],
                        s["tags"],
                    ),
                )
                snip_added += 1
        if snip_added:
            conn.commit()
            print(f"[init_db] Seeded {snip_added} new enclave snippets (total: {snip_count + snip_added}).", file=sys.stderr)
        else:
            print(f"[init_db] All {snip_count} enclave snippets up to date.", file=sys.stderr)

        # Seed template docs (SOP / Runbook markdown from docs/network/sops/)
        sops_dir = _ICDEV_ROOT / "docs" / "network" / "sops"
        if sops_dir.exists():
            doc_added = 0
            doc_templates = {
                "tpl-aws-directconnect": "AWS Direct Connect — SOP & Runbook",
                "tpl-azure-expressroute": "Azure ExpressRoute — SOP & Runbook",
                "tpl-gcp-interconnect": "GCP Cloud Interconnect — SOP & Runbook",
                "tpl-oci-fastconnect": "OCI FastConnect — SOP & Runbook",
                "tpl-dwdm-metro-ring": "DWDM Metro Ring — Architecture & Expansion Guide",
                "tpl-dwdm-customer-provider-access": "DWDM Customer-Provider Access — SOP & Runbook",
                "tpl-aws-tgw-hub-spoke": "AWS Transit Gateway Hub-and-Spoke — SOP & Runbook",
                "tpl-aws-dxgw-multi-region": "AWS DX Gateway Multi-Region — SOP & Runbook",
                "tpl-aws-vpn-at-scale": "AWS Site-to-Site VPN at Scale — SOP & Runbook",
                "tpl-aws-hybrid-art-of-possible": "AWS Hybrid Connectivity — Art of the Possible — SOP & Runbook",
                "tpl-az-vwan-hub-spoke": "Azure Virtual WAN Hub-and-Spoke — SOP & Runbook",
                "tpl-az-er-multi-region": "Azure ExpressRoute Gateway Multi-Region — SOP & Runbook",
                "tpl-az-vpn-at-scale": "Azure VPN Gateway Multi-Site at Scale — SOP & Runbook",
                "tpl-az-hybrid-art-of-possible": "Azure Hybrid Connectivity — Art of the Possible — SOP & Runbook",
                "tpl-gcp-ncc-hub-spoke": "GCP NCC Hub-and-Spoke — SOP & Runbook",
                "tpl-gcp-hybrid-art-of-possible": "GCP Hybrid Connectivity — Art of the Possible — SOP & Runbook",
                "tpl-oci-drg-hub-spoke": "OCI DRG v2 Hub-and-Spoke — SOP & Runbook",
                "tpl-oci-hybrid-art-of-possible": "OCI Hybrid Connectivity — Art of the Possible — SOP & Runbook",
            }
            for tpl_id, title in doc_templates.items():
                doc_id = f"doc-{tpl_id}"
                md_file = sops_dir / f"{tpl_id}.md"
                if not md_file.exists():
                    continue
                exists = conn.execute("SELECT 1 FROM nc_template_docs WHERE id = ?", (doc_id,)).fetchone()
                if not exists:
                    body = md_file.read_text(encoding="utf-8")
                    conn.execute(
                        "INSERT INTO nc_template_docs "
                        "(id, template_id, doc_type, title, body_markdown) "
                        "VALUES (?, ?, ?, ?, ?)",
                        (doc_id, tpl_id, "sop", title, body),
                    )
                    doc_added += 1
            if doc_added:
                conn.commit()
                print(f"[init_db] Seeded {doc_added} template docs.", file=sys.stderr)
            else:
                existing = conn.execute("SELECT COUNT(*) FROM nc_template_docs").fetchone()[0]
                print(f"[init_db] All {existing} template docs up to date.", file=sys.stderr)

        # Seed default admin user (password: admin — MUST change on first login)
        import hashlib

        admin_exists = conn.execute("SELECT COUNT(*) FROM nc_users WHERE username='admin'").fetchone()[0]
        if not admin_exists:
            pw_hash = hashlib.sha256("admin".encode()).hexdigest()
            conn.execute(
                "INSERT INTO nc_users (id, username, display_name, password_hash, role) VALUES (?,?,?,?,?)",
                ("usr-admin", "admin", "Administrator", pw_hash, "admin"),
            )
            conn.commit()
            print("[init_db] Default admin user created (username: admin, password: admin).", file=sys.stderr)

        # Seed review boards (ARB, ERB, CCB)
        board_count = conn.execute("SELECT COUNT(*) FROM nc_review_boards").fetchone()[0]
        if board_count == 0:
            for b in [
                (
                    "board-arb",
                    "Architecture Review Board",
                    "ARB",
                    "Reviews design decisions, protocol choices, enclave alignment",
                    "in_review",
                    0,
                    1,
                ),
                (
                    "board-erb",
                    "Engineering Review Board",
                    "ERB",
                    "Reviews feasibility, BOM, capacity, timeline",
                    "approved",
                    0,
                    2,
                ),
                (
                    "board-ccb",
                    "Change Control Board",
                    "CCB",
                    "Reviews blast radius, risk to production, rollback plan",
                    "deployed",
                    0,
                    3,
                ),
                (
                    "board-ssp",
                    "System Security Plan Review",
                    "SSP",
                    "Reviews SSP documentation before project can proceed — required by some teams, optional for others",
                    "design",
                    1,
                    4,
                ),
                (
                    "board-secappr",
                    "Security Policy Approval",
                    "SEC",
                    "Security team approves firewall rules, IDS/IPS policies, ACLs — separation of duty for security-impacting changes",
                    "implementation",
                    1,
                    5,
                ),
            ]:
                conn.execute(
                    "INSERT OR IGNORE INTO nc_review_boards "
                    "(id, name, short_name, description, "
                    " required_for_status, is_optional, sort_order) "
                    "VALUES (?,?,?,?,?,?,?)",
                    b,
                )
            conn.commit()
            print("[init_db] Seeded 3 review boards (ARB, ERB, CCB).", file=sys.stderr)

        # Seed design patterns
        pattern_count = conn.execute("SELECT COUNT(*) FROM nc_design_patterns").fetchone()[0]
        if pattern_count == 0:
            _patterns = [
                (
                    "pat-redundant-core",
                    "Redundant Core",
                    "redundancy",
                    "2x core routers with VRRP, LACP uplinks, OSPF area 0",
                    json.dumps(
                        {
                            "nodes": [
                                _node("cr1", "Core-R1", "router", 100, 100),
                                _node("cr2", "Core-R2", "router", 300, 100),
                            ],
                            "edges": [
                                _edge("cr1", "cr2", "VRRP/OSPF", "ospf"),
                            ],
                        }
                    ),
                    1,
                    '["core","vrrp","ospf","lacp"]',
                ),
                (
                    "pat-dist-block",
                    "Distribution Block",
                    "campus",
                    "2x L3 distribution switches with STP root/secondary",
                    json.dumps(
                        {
                            "nodes": [
                                _node("ds1", "Dist-SW1", "switch-l3", 100, 100),
                                _node("ds2", "Dist-SW2", "switch-l3", 300, 100),
                            ],
                            "edges": [
                                _edge("ds1", "ds2", "LACP Trunk", "lacp"),
                            ],
                        }
                    ),
                    1,
                    '["campus","stp","distribution"]',
                ),
                (
                    "pat-dmz-sandwich",
                    "DMZ Sandwich",
                    "security",
                    "2x firewalls + DMZ switch for public-facing services",
                    json.dumps(
                        {
                            "nodes": [
                                _node("fw-ext", "FW-External", "firewall", 200, 50),
                                _node("dmz-sw", "DMZ-Switch", "switch-l2", 200, 200),
                                _node("fw-int", "FW-Internal", "firewall", 200, 350),
                            ],
                            "edges": [
                                _edge("fw-ext", "dmz-sw", "DMZ-Out", ""),
                                _edge("dmz-sw", "fw-int", "DMZ-In", ""),
                            ],
                        }
                    ),
                    1,
                    '["dmz","firewall","security"]',
                ),
                (
                    "pat-wan-edge",
                    "WAN Edge",
                    "wan",
                    "Router + encryptor + carrier handoff for site-to-site WAN",
                    json.dumps(
                        {
                            "nodes": [
                                _node("wan-r", "WAN-Router", "router", 100, 100),
                                _node("wan-enc", "Encryptor", "type1-encryptor", 300, 100),
                                _node("wan-ho", "Carrier Handoff", "cloud", 500, 100),
                            ],
                            "edges": [
                                _edge("wan-r", "wan-enc", "Encrypted", "ipsec"),
                                _edge("wan-enc", "wan-ho", "WAN Circuit", ""),
                            ],
                        }
                    ),
                    1,
                    '["wan","encryption","carrier"]',
                ),
                (
                    "pat-sdwan-overlay",
                    "SD-WAN Overlay",
                    "wan",
                    "SD-WAN edge with dual WAN transports and IPSec overlay",
                    json.dumps(
                        {
                            "nodes": [
                                _node("sdw", "SD-WAN Edge", "sdwan-edge", 200, 100),
                                _node("wan1", "MPLS Transport", "cloud", 100, 250),
                                _node("wan2", "DIA Transport", "cloud", 300, 250),
                            ],
                            "edges": [
                                _edge("sdw", "wan1", "MPLS", "mpls"),
                                _edge("sdw", "wan2", "Internet", "ipsec"),
                            ],
                        }
                    ),
                    1,
                    '["sdwan","overlay","dual-transport"]',
                ),
                (
                    "pat-bgp-peering",
                    "BGP Peering",
                    "routing",
                    "eBGP peering between two ASNs with prefix filtering",
                    json.dumps(
                        {
                            "nodes": [
                                _node("pe1", "My-PE", "router", 100, 100),
                                _node("pe2", "Partner-PE", "router", 400, 100),
                            ],
                            "edges": [
                                _edge("pe1", "pe2", "eBGP", "bgp"),
                            ],
                        }
                    ),
                    1,
                    '["bgp","peering","partner"]',
                ),
                (
                    "pat-cross-connect",
                    "Cross-Connect",
                    "wan",
                    "Colo cross-connect: patch panel + meet-me room + demarc",
                    json.dumps(
                        {
                            "nodes": [
                                _node("pp", "Patch Panel", "patch-panel", 100, 100),
                                _node("mmr", "Meet-Me Room", "meet-me-room", 300, 100),
                                _node("dm", "Demarc", "demarc", 500, 100),
                            ],
                            "edges": [
                                _edge("pp", "mmr", "Fiber", "smf"),
                                _edge("mmr", "dm", "Cross-Connect", ""),
                            ],
                        }
                    ),
                    1,
                    '["colo","cross-connect","facility"]',
                ),
                (
                    "pat-mpls-pe",
                    "MPLS PE Node",
                    "routing",
                    "MPLS PE router with VRF and route reflector connection",
                    json.dumps(
                        {
                            "nodes": [
                                _node("pe", "MPLS-PE", "mpls-pe", 200, 100),
                                _node("rr", "Route Reflector", "route-reflector", 200, 300),
                            ],
                            "edges": [
                                _edge("pe", "rr", "iBGP/RR", "bgp"),
                            ],
                        }
                    ),
                    1,
                    '["mpls","pe","route-reflector"]',
                ),
                (
                    "pat-backup-power",
                    "Backup Power",
                    "reliability",
                    "Dual PDU A/B feeds, UPS, generator for power redundancy",
                    json.dumps(
                        {
                            "nodes": [
                                _node("pdu-a", "PDU-A", "server", 100, 100),
                                _node("pdu-b", "PDU-B", "server", 300, 100),
                                _node("ups", "UPS", "server", 200, 250),
                            ],
                            "edges": [
                                _edge("pdu-a", "ups", "A-Feed", ""),
                                _edge("pdu-b", "ups", "B-Feed", ""),
                            ],
                        }
                    ),
                    1,
                    '["power","ups","redundancy"]',
                ),
            ]
            for p in _patterns:
                conn.execute(
                    "INSERT OR IGNORE INTO nc_design_patterns "
                    "(id, name, category, description, graph_json, "
                    " is_builtin, tags) VALUES (?,?,?,?,?,?,?)",
                    p,
                )
            conn.commit()
            print(f"[init_db] Seeded {len(_patterns)} design patterns.", file=sys.stderr)

        # Seed device profiles
        prof_count = conn.execute("SELECT COUNT(*) FROM nc_device_profiles").fetchone()[0]
        if prof_count == 0:
            _profiles = [
                (
                    "prof-cisco-ios",
                    "Cisco",
                    "IOS / IOS-XE",
                    "Cisco IOS and IOS-XE routers and switches",
                    json.dumps(
                        {
                            "running_config": {
                                "command": "show running-config",
                                "parser": "ios_running_config",
                                "timeout_sec": 30,
                            },
                            "routing_table_v4": {
                                "command": "show ip route",
                                "parser": "ios_ip_route",
                                "timeout_sec": 15,
                            },
                            "routing_table_v6": {
                                "command": "show ipv6 route",
                                "parser": "ios_ipv6_route",
                                "timeout_sec": 15,
                            },
                            "interfaces": {
                                "command": "show ip interface brief",
                                "parser": "ios_interface_brief",
                                "timeout_sec": 10,
                            },
                            "version": {"command": "show version", "parser": "ios_version", "timeout_sec": 10},
                            "bgp_summary": {
                                "command": "show ip bgp summary",
                                "parser": "ios_bgp_summary",
                                "timeout_sec": 10,
                            },
                            "ospf_neighbors": {
                                "command": "show ip ospf neighbor",
                                "parser": "ios_ospf_neighbor",
                                "timeout_sec": 10,
                            },
                            "cdp_neighbors": {
                                "command": "show cdp neighbors detail",
                                "parser": "ios_cdp_detail",
                                "timeout_sec": 10,
                            },
                            "lldp_neighbors": {
                                "command": "show lldp neighbors detail",
                                "parser": "ios_lldp_detail",
                                "timeout_sec": 10,
                            },
                            "arp_table": {"command": "show arp", "parser": "ios_arp", "timeout_sec": 10},
                            "inventory": {"command": "show inventory", "parser": "ios_inventory", "timeout_sec": 10},
                        }
                    ),
                    1,
                ),
                (
                    "prof-cisco-nxos",
                    "Cisco",
                    "NX-OS",
                    "Cisco Nexus switches (NX-OS)",
                    json.dumps(
                        {
                            "running_config": {
                                "command": "show running-config",
                                "parser": "nxos_running_config",
                                "timeout_sec": 30,
                            },
                            "routing_table_v4": {
                                "command": "show ip route",
                                "parser": "nxos_ip_route",
                                "timeout_sec": 15,
                            },
                            "interfaces": {
                                "command": "show ip interface brief",
                                "parser": "nxos_interface_brief",
                                "timeout_sec": 10,
                            },
                            "version": {"command": "show version", "parser": "nxos_version", "timeout_sec": 10},
                            "bgp_summary": {
                                "command": "show ip bgp summary",
                                "parser": "nxos_bgp_summary",
                                "timeout_sec": 10,
                            },
                            "lldp_neighbors": {
                                "command": "show lldp neighbors detail",
                                "parser": "nxos_lldp_detail",
                                "timeout_sec": 10,
                            },
                            "vpc_status": {"command": "show vpc brief", "parser": "nxos_vpc", "timeout_sec": 10},
                        }
                    ),
                    1,
                ),
                (
                    "prof-arista-eos",
                    "Arista",
                    "EOS",
                    "Arista switches (EOS)",
                    json.dumps(
                        {
                            "running_config": {
                                "command": "show running-config",
                                "parser": "eos_running_config",
                                "timeout_sec": 30,
                            },
                            "routing_table_v4": {
                                "command": "show ip route",
                                "parser": "eos_ip_route",
                                "timeout_sec": 15,
                            },
                            "interfaces": {
                                "command": "show ip interface brief",
                                "parser": "eos_interface_brief",
                                "timeout_sec": 10,
                            },
                            "version": {"command": "show version", "parser": "eos_version", "timeout_sec": 10},
                            "bgp_summary": {
                                "command": "show ip bgp summary",
                                "parser": "eos_bgp_summary",
                                "timeout_sec": 10,
                            },
                            "lldp_neighbors": {
                                "command": "show lldp neighbors detail",
                                "parser": "eos_lldp_detail",
                                "timeout_sec": 10,
                            },
                            "mlag_status": {"command": "show mlag detail", "parser": "eos_mlag", "timeout_sec": 10},
                        }
                    ),
                    1,
                ),
                (
                    "prof-juniper-junos",
                    "Juniper",
                    "JunOS",
                    "Juniper routers and switches (JunOS)",
                    json.dumps(
                        {
                            "running_config": {
                                "command": "show configuration | display set",
                                "parser": "junos_config_set",
                                "timeout_sec": 30,
                            },
                            "routing_table_v4": {
                                "command": "show route table inet.0",
                                "parser": "junos_route",
                                "timeout_sec": 15,
                            },
                            "routing_table_v6": {
                                "command": "show route table inet6.0",
                                "parser": "junos_route_v6",
                                "timeout_sec": 15,
                            },
                            "interfaces": {
                                "command": "show interfaces terse",
                                "parser": "junos_interface_terse",
                                "timeout_sec": 10,
                            },
                            "version": {"command": "show version", "parser": "junos_version", "timeout_sec": 10},
                            "bgp_summary": {
                                "command": "show bgp summary",
                                "parser": "junos_bgp_summary",
                                "timeout_sec": 10,
                            },
                            "ospf_neighbors": {
                                "command": "show ospf neighbor",
                                "parser": "junos_ospf_neighbor",
                                "timeout_sec": 10,
                            },
                            "lldp_neighbors": {
                                "command": "show lldp neighbors",
                                "parser": "junos_lldp",
                                "timeout_sec": 10,
                            },
                        }
                    ),
                    1,
                ),
                (
                    "prof-paloalto-panos",
                    "Palo Alto",
                    "PAN-OS",
                    "Palo Alto firewalls (PAN-OS)",
                    json.dumps(
                        {
                            "running_config": {
                                "command": "show config running",
                                "parser": "panos_config",
                                "timeout_sec": 30,
                            },
                            "interfaces": {
                                "command": "show interface all",
                                "parser": "panos_interface",
                                "timeout_sec": 10,
                            },
                            "version": {
                                "command": "show system info",
                                "parser": "panos_system_info",
                                "timeout_sec": 10,
                            },
                            "routing_table_v4": {
                                "command": "show routing route",
                                "parser": "panos_route",
                                "timeout_sec": 15,
                            },
                            "security_rules": {
                                "command": "show running security-policy",
                                "parser": "panos_security_policy",
                                "timeout_sec": 15,
                            },
                            "arp_table": {"command": "show arp all", "parser": "panos_arp", "timeout_sec": 10},
                        }
                    ),
                    1,
                ),
                (
                    "prof-fortinet-fortios",
                    "Fortinet",
                    "FortiOS",
                    "Fortinet FortiGate firewalls (FortiOS)",
                    json.dumps(
                        {
                            "running_config": {
                                "command": "show full-configuration",
                                "parser": "fortios_config",
                                "timeout_sec": 30,
                            },
                            "interfaces": {
                                "command": "get system interface",
                                "parser": "fortios_interface",
                                "timeout_sec": 10,
                            },
                            "version": {"command": "get system status", "parser": "fortios_status", "timeout_sec": 10},
                            "routing_table_v4": {
                                "command": "get router info routing-table all",
                                "parser": "fortios_route",
                                "timeout_sec": 15,
                            },
                            "security_rules": {
                                "command": "show firewall policy",
                                "parser": "fortios_policy",
                                "timeout_sec": 15,
                            },
                            "arp_table": {"command": "get system arp", "parser": "fortios_arp", "timeout_sec": 10},
                        }
                    ),
                    1,
                ),
                (
                    "prof-nokia-sros",
                    "Nokia",
                    "SR-OS",
                    "Nokia service routers (SR-OS / TiMOS)",
                    json.dumps(
                        {
                            "running_config": {
                                "command": "admin display-config",
                                "parser": "sros_config",
                                "timeout_sec": 30,
                            },
                            "routing_table_v4": {
                                "command": "show router route-table",
                                "parser": "sros_route",
                                "timeout_sec": 15,
                            },
                            "interfaces": {"command": "show port", "parser": "sros_port", "timeout_sec": 10},
                            "version": {
                                "command": "show system information",
                                "parser": "sros_system_info",
                                "timeout_sec": 10,
                            },
                            "bgp_summary": {
                                "command": "show router bgp summary",
                                "parser": "sros_bgp_summary",
                                "timeout_sec": 10,
                            },
                            "ospf_neighbors": {
                                "command": "show router ospf neighbor",
                                "parser": "sros_ospf_neighbor",
                                "timeout_sec": 10,
                            },
                        }
                    ),
                    1,
                ),
            ]
            for p in _profiles:
                conn.execute(
                    "INSERT OR IGNORE INTO nc_device_profiles "
                    "(id, vendor, platform, description, commands_json, "
                    " is_builtin) VALUES (?,?,?,?,?,?)",
                    p,
                )
            conn.commit()
            print(f"[init_db] Seeded {len(_profiles)} device profiles.", file=sys.stderr)

        # ── Seed hardware profiles ────────────────────────────────────────
        hw_count = conn.execute("SELECT COUNT(*) FROM nc_hardware_profiles").fetchone()[0]
        if hw_count == 0:
            _hw_profiles = [
                # Juniper MX Series
                ("hw-juniper-mx304", "Juniper", "MX304", "MX Series", "router", "rack", 2, 12.25, 508, 438, 88, 350, 480, 2, "AC/DC", 0, 40, 10, 85, "front-to-back", 3000, 65, 4800, 2000, 4000000, 256000, None, None, None, None,
                 json.dumps([{"count":36,"speed":"100GbE","type":"QSFP28","breakout":"4x25GbE or 2x50GbE","if_prefix":"et-0/0/","if_start":0,"if_end":35},{"count":2,"speed":"400GbE","type":"QSFP-DD","breakout":"4x100GbE","if_prefix":"et-0/0/","if_start":36,"if_end":37}]),
                 json.dumps([{"type":"Chassis","model":"MX304-PREM","qty":1},{"type":"RE","model":"JNP304-RE-S","qty":2},{"type":"Fan","model":"JNP-FAN-2RU","qty":3},{"type":"PSU","model":"JNP-PWR2200-AC","qty":2}]),
                 json.dumps([{"type":"Console RJ45"},{"type":"USB"},{"type":"1GbE Mgmt"}]),
                 json.dumps(["JunOS","JunOS Evolved"]), "subscription", "2031-12-31", "2029-12-31", 85000, 0.15,
                 "https://www.juniper.net/documentation/us/en/hardware/mx304/", None, json.dumps(["core","edge","peering"]), 1),
                ("hw-juniper-mx204", "Juniper", "MX204", "MX Series", "router", "rack", 1, 3.4, 298, 438, 44, 200, 275, 2, "AC/DC", 0, 40, 10, 85, "front-to-back", 3000, 55, 400, 200, 2000000, 128000, None, None, None, None,
                 json.dumps([{"count":4,"speed":"100GbE","type":"QSFP28","breakout":"4x25GbE"},{"count":8,"speed":"10GbE","type":"SFP+"}]),
                 json.dumps([{"type":"Chassis","model":"MX204","qty":1},{"type":"PSU","model":"JNP-PWR-350-AC","qty":2}]),
                 json.dumps([{"type":"Console RJ45"},{"type":"USB"},{"type":"1GbE Mgmt"}]),
                 json.dumps(["JunOS"]), "subscription", "2030-06-30", "2028-06-30", 35000, 0.15, None, None, json.dumps(["access","cpe","small-core"]), 1),
                ("hw-juniper-mx10003", "Juniper", "MX10003", "MX10000 Series", "router", "chassis", 3, 27.0, 610, 438, 131, 800, 1200, 4, "AC/DC", 0, 40, 10, 85, "front-to-back", 3000, 72, 9600, 4000, 8000000, 512000, None, None, None, None,
                 json.dumps([{"count":3,"speed":"line card slot","type":"MPC","breakout":"varies","note":"Modular — actual ports depend on installed MICs. Common: MPC7E-100G (2x MIC, 12x100GE/slot), MPC7E-MRATE (2x MIC, 24x10GE/slot), MPC10E-15C-MRATE (15x100GE/slot). Parse running-config for as-built interfaces."}]),
                 json.dumps([{"type":"Chassis","model":"MX10003","qty":1},{"type":"RE","model":"RE-MX10003","qty":2},{"type":"Fan","model":"MX10003-FAN","qty":6},{"type":"PSU","model":"PWR-MX10003-AC","qty":4}]),
                 json.dumps([{"type":"Console RJ45"},{"type":"USB"},{"type":"1GbE Mgmt"}]),
                 json.dumps(["JunOS","JunOS Evolved"]), "subscription", "2033-12-31", "2031-12-31", 180000, 0.12, None, None, json.dumps(["core","backbone","dc-edge"]), 1),
                ("hw-juniper-mx10008", "Juniper", "MX10008", "MX10000 Series", "router", "chassis", 13, 105.0, 610, 438, 572, 3000, 4500, 8, "AC/DC", 0, 40, 10, 85, "front-to-back", 3000, 78, 28800, 12000, 16000000, 1024000, None, None, None, None,
                 json.dumps([{"count":8,"speed":"line card slot","type":"MPC","breakout":"varies"}]),
                 json.dumps([{"type":"Chassis","model":"MX10008","qty":1},{"type":"RE","model":"RE-MX10008","qty":2},{"type":"SCB","model":"SCB-MX10008","qty":3},{"type":"Fan","model":"MX10008-FAN","qty":8},{"type":"PSU","model":"PWR-MX10008-AC","qty":8}]),
                 json.dumps([{"type":"Console RJ45"},{"type":"USB"},{"type":"1GbE Mgmt"}]),
                 json.dumps(["JunOS","JunOS Evolved"]), "subscription", "2034-12-31", "2032-12-31", 450000, 0.10, None, None, json.dumps(["core","backbone","ix"]), 1),
                ("hw-juniper-mx480", "Juniper", "MX480", "MX Series", "router", "chassis", 8, 41.7, 610, 438, 355, 1200, 1800, 4, "AC/DC", 0, 40, 10, 85, "front-to-back", 3000, 72, 2400, 960, 4000000, 256000, None, None, None, None,
                 json.dumps([{"count":6,"speed":"MPC slot","type":"MPC","breakout":"varies"}]),
                 json.dumps([{"type":"Chassis","model":"MX480","qty":1},{"type":"RE","model":"RE-S-2X00x6","qty":2}]),
                 json.dumps([{"type":"Console RJ45"},{"type":"1GbE Mgmt"}]),
                 json.dumps(["JunOS"]), "perpetual", "2029-03-31", "2027-03-31", 120000, 0.15, None, None, json.dumps(["core","aggregation"]), 1),
                ("hw-juniper-mx960", "Juniper", "MX960", "MX Series", "router", "chassis", 16, 85.0, 610, 438, 710, 2500, 3800, 8, "AC/DC", 0, 40, 10, 85, "front-to-back", 3000, 75, 4800, 2000, 8000000, 512000, None, None, None, None,
                 json.dumps([{"count":12,"speed":"MPC slot","type":"MPC","breakout":"varies"}]),
                 json.dumps([{"type":"Chassis","model":"MX960","qty":1},{"type":"RE","model":"RE-S-2X00x6","qty":2}]),
                 json.dumps([{"type":"Console RJ45"},{"type":"1GbE Mgmt"}]),
                 json.dumps(["JunOS"]), "perpetual", "2029-03-31", "2027-03-31", 200000, 0.12, None, None, json.dumps(["core","backbone","peering"]), 1),
                ("hw-juniper-mx240", "Juniper", "MX240", "MX Series", "router", "chassis", 5, 24.5, 610, 438, 222, 600, 900, 2, "AC/DC", 0, 40, 10, 85, "front-to-back", 3000, 68, 960, 480, 2000000, 128000, None, None, None, None,
                 json.dumps([{"count":2,"speed":"MPC slot","type":"MPC","breakout":"varies"}]),
                 json.dumps([{"type":"Chassis","model":"MX240","qty":1},{"type":"RE","model":"RE-S-1800x4","qty":2}]),
                 json.dumps([{"type":"Console RJ45"},{"type":"1GbE Mgmt"}]),
                 json.dumps(["JunOS"]), "perpetual", "2028-12-31", "2026-12-31", 65000, 0.15, None, None, json.dumps(["edge","aggregation"]), 1),
                ("hw-juniper-mx150", "Juniper", "MX150", "MX Series", "router", "rack", 1, 3.6, 254, 440, 44, 75, 100, 1, "AC", 0, 40, 10, 85, "side-to-side", 3000, 45, 50, 30, 500000, 64000, None, None, None, None,
                 json.dumps([{"count":10,"speed":"1GbE","type":"RJ45"},{"count":2,"speed":"10GbE","type":"SFP+"}]),
                 json.dumps([{"type":"Chassis","model":"MX150","qty":1}]),
                 json.dumps([{"type":"Console RJ45"},{"type":"USB"},{"type":"1GbE Mgmt"}]),
                 json.dumps(["JunOS"]), "subscription", "2029-06-30", "2027-06-30", 12000, 0.18, None, None, json.dumps(["cpe","branch","sd-wan"]), 1),
                # Cisco 8000 / ASR Series
                ("hw-cisco-8101-32h", "Cisco", "8101-32H", "8000 Series", "router", "rack", 1, 9.5, 600, 438, 44, 400, 550, 2, "AC/DC", 0, 40, 10, 90, "front-to-back", 3000, 73, 12800, 4600, 10000000, 512000, None, None, None, None,
                 json.dumps([{"count":32,"speed":"400GbE","type":"QSFP-DD","breakout":"4x100GbE"}]),
                 json.dumps([{"type":"Chassis","model":"8101-32H","qty":1},{"type":"Fan","model":"8100-FAN","qty":4},{"type":"PSU","model":"PSU2KW-ACDD","qty":2}]),
                 json.dumps([{"type":"Console RJ45"},{"type":"USB-C"},{"type":"1GbE Mgmt"}]),
                 json.dumps(["IOS-XR"]), "subscription", "2034-12-31", "2032-12-31", 125000, 0.12, None, None, json.dumps(["core","spine","peering"]), 1),
                ("hw-cisco-8102-64h", "Cisco", "8102-64H", "8000 Series", "router", "rack", 2, 18.0, 600, 438, 88, 800, 1100, 2, "AC/DC", 0, 40, 10, 90, "front-to-back", 3000, 75, 25600, 9200, 16000000, 1024000, None, None, None, None,
                 json.dumps([{"count":64,"speed":"400GbE","type":"QSFP-DD","breakout":"4x100GbE"}]),
                 json.dumps([{"type":"Chassis","model":"8102-64H","qty":1},{"type":"Fan","model":"8100-FAN","qty":6},{"type":"PSU","model":"PSU3KW-ACDD","qty":2}]),
                 json.dumps([{"type":"Console RJ45"},{"type":"USB-C"},{"type":"1GbE Mgmt"}]),
                 json.dumps(["IOS-XR"]), "subscription", "2034-12-31", "2032-12-31", 250000, 0.12, None, None, json.dumps(["core","spine","backbone"]), 1),
                ("hw-cisco-8201", "Cisco", "8201", "8000 Series", "router", "rack", 1, 10.0, 600, 438, 44, 450, 600, 2, "AC/DC", 0, 40, 10, 90, "front-to-back", 3000, 72, 10800, 4000, 8000000, 512000, None, None, None, None,
                 json.dumps([{"count":24,"speed":"400GbE","type":"QSFP-DD","breakout":"4x100GbE"},{"count":12,"speed":"100GbE","type":"QSFP28"}]),
                 json.dumps([{"type":"Chassis","model":"8201","qty":1},{"type":"Fan","model":"8200-FAN","qty":4},{"type":"PSU","model":"PSU2KW-ACDD","qty":2}]),
                 json.dumps([{"type":"Console RJ45"},{"type":"USB-C"},{"type":"1GbE Mgmt"}]),
                 json.dumps(["IOS-XR"]), "subscription", "2034-12-31", "2032-12-31", 110000, 0.12, None, None, json.dumps(["core","edge","peering"]), 1),
                ("hw-cisco-8202", "Cisco", "8202", "8000 Series", "router", "rack", 2, 17.0, 600, 438, 88, 700, 950, 2, "AC/DC", 0, 40, 10, 90, "front-to-back", 3000, 74, 10800, 4000, 8000000, 512000, None, None, None, None,
                 json.dumps([{"count":12,"speed":"400GbE","type":"QSFP-DD","breakout":"4x100GbE"},{"count":60,"speed":"100GbE","type":"QSFP28"}]),
                 json.dumps([{"type":"Chassis","model":"8202","qty":1},{"type":"Fan","model":"8200-FAN","qty":6},{"type":"PSU","model":"PSU3KW-ACDD","qty":2}]),
                 json.dumps([{"type":"Console RJ45"},{"type":"USB-C"},{"type":"1GbE Mgmt"}]),
                 json.dumps(["IOS-XR"]), "subscription", "2034-12-31", "2032-12-31", 135000, 0.12, None, None, json.dumps(["leaf","aggregation","dc-gw"]), 1),
                ("hw-cisco-asr1001x", "Cisco", "ASR-1001-X", "ASR 1000 Series", "router", "rack", 1, 8.6, 445, 438, 44, 200, 275, 2, "AC", 0, 40, 10, 90, "side-to-side", 3000, 55, 20, 10, 1000000, 128000, None, 1000000, 2000, 4094,
                 json.dumps([{"count":6,"speed":"1GbE","type":"RJ45"},{"count":2,"speed":"10GbE","type":"SFP+"}]),
                 json.dumps([{"type":"Chassis","model":"ASR-1001-X","qty":1},{"type":"ESP","model":"ESP-20G","qty":1},{"type":"RP","model":"RP2","qty":1}]),
                 json.dumps([{"type":"Console RJ45"},{"type":"USB"},{"type":"1GbE Mgmt"}]),
                 json.dumps(["IOS-XE"]), "perpetual", "2028-12-31", "2026-12-31", 45000, 0.15, None, None, json.dumps(["branch","wan-edge","sd-wan"]), 1),
                ("hw-cisco-asr1002hx", "Cisco", "ASR-1002-HX", "ASR 1000 Series", "router", "rack", 2, 14.5, 445, 438, 88, 350, 475, 2, "AC", 0, 40, 10, 90, "side-to-side", 3000, 60, 100, 50, 2000000, 256000, None, 2000000, 5000, 4094,
                 json.dumps([{"count":8,"speed":"10GbE","type":"SFP+"},{"count":4,"speed":"1GbE","type":"RJ45"}]),
                 json.dumps([{"type":"Chassis","model":"ASR-1002-HX","qty":1},{"type":"ESP","model":"ESP-100G","qty":1},{"type":"RP","model":"RP2","qty":1}]),
                 json.dumps([{"type":"Console RJ45"},{"type":"USB"},{"type":"1GbE Mgmt"}]),
                 json.dumps(["IOS-XE"]), "perpetual", "2029-06-30", "2027-06-30", 75000, 0.15, None, None, json.dumps(["wan-edge","aggregation"]), 1),
                ("hw-cisco-asr9901", "Cisco", "ASR-9901", "ASR 9000 Series", "router", "rack", 2, 17.0, 600, 438, 88, 600, 850, 2, "AC/DC", 0, 40, 10, 90, "front-to-back", 3000, 72, 2400, 1000, 6000000, 256000, None, None, None, None,
                 json.dumps([{"count":20,"speed":"100GbE","type":"QSFP28"},{"count":4,"speed":"10GbE","type":"SFP+"}]),
                 json.dumps([{"type":"Chassis","model":"ASR-9901","qty":1},{"type":"RSP","model":"RSP-880","qty":2},{"type":"Fan","model":"ASR-9901-FAN","qty":4},{"type":"PSU","model":"PSU-2KW-AC","qty":2}]),
                 json.dumps([{"type":"Console RJ45"},{"type":"USB"},{"type":"1GbE Mgmt"}]),
                 json.dumps(["IOS-XR"]), "subscription", "2031-12-31", "2029-12-31", 160000, 0.12, None, None, json.dumps(["core","peering","aggregation"]), 1),
                ("hw-cisco-8111-32eh", "Cisco", "8111-32EH", "8000 Series", "router", "rack", 1, 9.5, 600, 438, 44, 380, 520, 2, "AC/DC", 0, 40, 10, 90, "front-to-back", 3000, 72, 12800, 4600, 10000000, 512000, None, None, None, None,
                 json.dumps([{"count":32,"speed":"400GbE","type":"QSFP-DD","breakout":"4x100GbE"}]),
                 json.dumps([{"type":"Chassis","model":"8111-32EH","qty":1},{"type":"Fan","model":"8100-FAN","qty":4},{"type":"PSU","model":"PSU2KW-ACDD","qty":2}]),
                 json.dumps([{"type":"Console RJ45"},{"type":"USB-C"},{"type":"1GbE Mgmt"}]),
                 json.dumps(["IOS-XR"]), "subscription", "2035-12-31", "2033-12-31", 130000, 0.12, None, None, json.dumps(["core","spine","peering"]), 1),
            ]
            for p in _hw_profiles:
                conn.execute(
                    "INSERT OR IGNORE INTO nc_hardware_profiles "
                    "(id, vendor, model, model_family, device_type, form_factor, rack_units, "
                    "weight_kg, depth_mm, width_mm, height_mm, power_typical_w, power_max_w, "
                    "psu_count, psu_type, operating_temp_min_c, operating_temp_max_c, "
                    "humidity_min_pct, humidity_max_pct, airflow_direction, altitude_max_m, "
                    "acoustic_dba, throughput_gbps, pps_mpps, routing_table_size, arp_table_size, "
                    "mac_table_size, nat_sessions, vpn_tunnels, vlan_count, "
                    "ports_json, components_json, mgmt_ports_json, os_options, license_model, "
                    "eol_date, eos_date, replacement_cost, annual_maintenance_pct, "
                    "datasheet_url, image_url, tags, is_builtin) "
                    "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    p,
                )
            conn.commit()
            print(f"[init_db] Seeded {len(_hw_profiles)} hardware profiles.", file=sys.stderr)

        # ── Seed naming conventions ─────────────────────────────────────
        nc_count = conn.execute("SELECT COUNT(*) FROM nc_naming_conventions").fetchone()[0]
        if nc_count == 0:
            _conventions = [
                ("nc-site-seq", "Site-Sequence", "Simple site code + sequence number",
                 "{SITE}{SEQ}", json.dumps([
                     {"name":"SITE","description":"Site/location code","type":"alpha","width":3,"pad":"none","values":["NYC","LAX","DCA","IAD","ORD","SFO","SEA","ATL","DAL","CHI"],"required":True},
                     {"name":"SEQ","description":"Sequence number","type":"sequence","width":2,"pad":"0","start":1,"required":True}
                 ]), "", 63, "upper", "NYC01", 1),
                ("nc-site-role-seq", "Site-Role-Sequence", "Site code, device role, and sequence with dash separator",
                 "{SITE}{SEP}{ROLE}{SEP}{SEQ}", json.dumps([
                     {"name":"SITE","description":"Site/location code","type":"alpha","width":3,"pad":"none","values":["NYC","LAX","DCA","IAD","ORD"],"required":True},
                     {"name":"ROLE","description":"Device role","type":"enum","width":4,"values":["CORE","DIST","ACCS","FWLL","VPNG","SRVR","WLAN","MGMT"],"required":True},
                     {"name":"SEQ","description":"Sequence number","type":"sequence","width":2,"pad":"0","start":1,"required":True}
                 ]), "-", 63, "upper", "NYC-CORE-01", 1),
                ("nc-citybldg-role", "City-Building-Role", "City code, building ID, floor, role and sequence",
                 "{CITY}{BLDG}{FLOOR}{SEP}{ROLE}{SEQ}", json.dumps([
                     {"name":"CITY","description":"City code","type":"alpha","width":3,"pad":"none","required":True},
                     {"name":"BLDG","description":"Building code","type":"alphanumeric","width":3,"pad":"none","required":True},
                     {"name":"FLOOR","description":"Floor number","type":"numeric","width":1,"pad":"none","required":False},
                     {"name":"ROLE","description":"Device role","type":"alpha","width":4,"pad":"none","required":True},
                     {"name":"SEQ","description":"Sequence","type":"sequence","width":2,"pad":"0","start":1,"required":True}
                 ]), "-", 63, "upper", "NYCWS3-CORE01", 1),
                ("nc-dod-standard", "DoD Standard", "COCOM, base, building, role, and 3-digit sequence",
                 "{COCOM}{SEP}{BASE}{SEP}{BLDG}{SEP}{ROLE}{SEP}{SEQ}", json.dumps([
                     {"name":"COCOM","description":"Combatant command","type":"enum","width":6,"values":["CONUS","EUCOM","PACOM","CENTM","AFRCM","SOUTM"],"required":True},
                     {"name":"BASE","description":"Installation name","type":"alpha","width":6,"pad":"none","required":True},
                     {"name":"BLDG","description":"Building ID","type":"alphanumeric","width":4,"pad":"none","required":True},
                     {"name":"ROLE","description":"Device role","type":"enum","width":3,"values":["RTR","SWI","FWL","SRV","VPN","WAP","LBR"],"required":True},
                     {"name":"SEQ","description":"Sequence number","type":"sequence","width":3,"pad":"0","start":1,"required":True}
                 ]), "-", 63, "upper", "CONUS-BRAGG-B12-RTR-001", 1),
                ("nc-simple", "Simple", "Device type prefix with 3-digit sequence",
                 "{TYPE}{SEP}{SEQ}", json.dumps([
                     {"name":"TYPE","description":"Device type prefix","type":"enum","width":3,"values":["RTR","SW","FW","SRV","VPN","AP","LB","WAN"],"required":True},
                     {"name":"SEQ","description":"Sequence number","type":"sequence","width":3,"pad":"0","start":1,"required":True}
                 ]), "-", 63, "upper", "RTR-001", 1),
            ]
            for c in _conventions:
                conn.execute(
                    "INSERT OR IGNORE INTO nc_naming_conventions "
                    "(id, name, description, pattern, fields_json, separator, max_length, "
                    "case_rule, example, is_builtin) VALUES (?,?,?,?,?,?,?,?,?,?)",
                    c,
                )
            conn.commit()
            print(f"[init_db] Seeded {len(_conventions)} naming conventions.", file=sys.stderr)

        # ── Seed NDC SOPs ──────────────────────────────────────────────
        sop_count = conn.execute("SELECT COUNT(*) FROM ndc_sops").fetchone()[0]
        if sop_count == 0:
            _sops = [
                {
                    "sop_id": "sop-change-window-001",
                    "title": "Network Change Window Execution",
                    "category": "change_window",
                    "description": "Standard procedure for executing approved network changes within a scheduled maintenance window.",
                    "prerequisites": [
                        "Approved CAB change ticket",
                        "Pre-change configuration backup stored",
                        "Stakeholders notified ≥ 24h in advance",
                        "Rollback plan reviewed and signed off",
                    ],
                    "steps": [
                        {"number": 1, "action": "Announce window start in #netops-change channel", "verify": "Post timestamped; acknowledgement from on-call SRE", "rollback": "N/A", "time_est": "5m"},
                        {"number": 2, "action": "Verify pre-change baseline (ping, BGP sessions, SNMP up)", "verify": "All target devices respond; baseline captured to runbook log", "rollback": "Abort if baseline unhealthy", "time_est": "10m"},
                        {"number": 3, "action": "Apply change per approved config diff", "verify": "Commit confirmed on device; syslog shows no errors", "rollback": "`rollback` / `configure replace` to prior config", "time_est": "15m"},
                        {"number": 4, "action": "Post-change validation checks", "verify": "Validation list passes 100%", "rollback": "Rollback if any critical check fails", "time_est": "15m"},
                        {"number": 5, "action": "Close window; update ticket with outcome", "verify": "Ticket status = Implemented; window closed in calendar", "rollback": "N/A", "time_est": "5m"},
                    ],
                    "validation": [
                        "All affected BGP/OSPF neighbors re-established",
                        "End-to-end ping between representative endpoints < 5ms loss",
                        "Monitoring dashboards show no new alarms for 15 minutes",
                        "Traffic volume returned to ±10% of baseline",
                    ],
                    "rollback": {
                        "trigger": "Any critical validation check fails OR unplanned outage > 2 minutes",
                        "procedure": "Execute `configure replace <backup>` on affected devices, verify baseline restored, open P1 incident.",
                        "max_duration": "10m",
                    },
                    "escalation": [
                        {"tier": 1, "role": "On-call Network Engineer", "contact": "netops-oncall@agency.gov"},
                        {"tier": 2, "role": "Network Operations Manager", "contact": "netops-mgr@agency.gov"},
                        {"tier": 3, "role": "CIO / Authorizing Official", "contact": "ao@agency.gov"},
                    ],
                },
                {
                    "sop_id": "sop-circuit-provision-001",
                    "title": "WAN Circuit Provisioning",
                    "category": "provisioning",
                    "description": "End-to-end procedure to provision, test, and accept a new WAN circuit from carrier.",
                    "prerequisites": [
                        "Signed carrier service order with LOA-CFA",
                        "Demarc location and rack space confirmed",
                        "IP addressing assigned in IPAM",
                        "Router port reserved and labeled",
                    ],
                    "steps": [
                        {"number": 1, "action": "Verify physical demarc (fiber/copper tested by carrier)", "verify": "Carrier light levels within spec; LOS alarm clear", "rollback": "Reject turn-up; carrier re-dispatch", "time_est": "30m"},
                        {"number": 2, "action": "Cross-connect from demarc to CE router port", "verify": "Layer-1 link up, correct optics, no CRC errors", "rollback": "Re-seat / replace SFP; open ticket", "time_est": "20m"},
                        {"number": 3, "action": "Apply interface and routing config (BGP peering)", "verify": "BGP session Established; prefixes received == expected", "rollback": "Shut interface; restore prior config", "time_est": "30m"},
                        {"number": 4, "action": "Carrier handoff testing (RFC 2544 or Y.1564)", "verify": "Throughput ≥ CIR, latency/jitter within SLA", "rollback": "Carrier re-turn-up if out of spec", "time_est": "60m"},
                        {"number": 5, "action": "Update CMDB, IPAM, and monitoring", "verify": "Circuit visible in NMS, asset record complete, alerts wired", "rollback": "N/A", "time_est": "20m"},
                    ],
                    "validation": [
                        "BGP session UP and exchanging expected prefix count",
                        "RFC 2544 or Y.1564 test results at or above SLA",
                        "No interface errors for 24h soak period",
                        "Monitoring receives SNMP/flow data",
                    ],
                    "rollback": {
                        "trigger": "Acceptance test fails OR > 0.1% packet loss sustained",
                        "procedure": "Shutdown interface, remove BGP config, notify carrier, document failure mode.",
                        "max_duration": "30m",
                    },
                    "escalation": [
                        {"tier": 1, "role": "Provisioning Engineer", "contact": "provisioning@agency.gov"},
                        {"tier": 2, "role": "Carrier NOC", "contact": "noc@carrier.example"},
                        {"tier": 3, "role": "Network Architect", "contact": "net-arch@agency.gov"},
                    ],
                },
                {
                    "sop_id": "sop-firewall-rule-001",
                    "title": "Firewall Rule Change",
                    "category": "firewall",
                    "description": "Procedure to add, modify, or remove a firewall rule with compliance review and validation.",
                    "prerequisites": [
                        "Approved firewall change request with business justification",
                        "Security review sign-off (least privilege verified)",
                        "Current ruleset backup exported",
                        "Test traffic source/destination identified",
                    ],
                    "steps": [
                        {"number": 1, "action": "Backup current firewall configuration", "verify": "Backup file hash recorded and stored off-device", "rollback": "N/A", "time_est": "5m"},
                        {"number": 2, "action": "Stage rule change in candidate config (do not commit)", "verify": "Syntax check passes; no shadowed/redundant rules flagged", "rollback": "Discard candidate", "time_est": "10m"},
                        {"number": 3, "action": "Peer review of rule (source, dest, port, action, logging)", "verify": "Reviewer signs off in ticket; least privilege confirmed", "rollback": "Return to staging if changes required", "time_est": "15m"},
                        {"number": 4, "action": "Commit change to active policy", "verify": "Commit ID captured; policy version incremented", "rollback": "`rollback` to last-known-good commit", "time_est": "5m"},
                        {"number": 5, "action": "Validate permitted and denied flows", "verify": "Expected traffic flows; denied traffic blocked and logged", "rollback": "Rollback if validation fails", "time_est": "15m"},
                    ],
                    "validation": [
                        "Permit test: packet reaches destination from approved source",
                        "Deny test: unauthorized source is blocked and logged",
                        "Hit counter on new rule > 0 within 1h of production traffic",
                        "No spike in drops on adjacent rules (rule placement correct)",
                    ],
                    "rollback": {
                        "trigger": "Legitimate traffic blocked OR unauthorized traffic permitted",
                        "procedure": "Rollback firewall commit to backup; open incident; re-review rule.",
                        "max_duration": "10m",
                    },
                    "escalation": [
                        {"tier": 1, "role": "Firewall Administrator", "contact": "fw-admin@agency.gov"},
                        {"tier": 2, "role": "Security Operations Center", "contact": "soc@agency.gov"},
                        {"tier": 3, "role": "ISSO", "contact": "isso@agency.gov"},
                    ],
                },
                {
                    "sop_id": "sop-dns-update-001",
                    "title": "DNS Record Update",
                    "category": "dns",
                    "description": "Procedure to add, modify, or remove an authoritative DNS record with TTL management and propagation verification.",
                    "prerequisites": [
                        "Approved DNS change ticket with record details",
                        "Current zone file exported / version tagged",
                        "TTL reduction applied ≥ 2x previous TTL before change (for modifies)",
                        "Downstream systems (certs, SPF, DMARC) reviewed for impact",
                    ],
                    "steps": [
                        {"number": 1, "action": "Export current zone file and commit to version control", "verify": "Zone serial captured; git commit hash recorded", "rollback": "N/A", "time_est": "5m"},
                        {"number": 2, "action": "Edit record (add/modify/delete) and bump SOA serial", "verify": "Zone file validates (`named-checkzone`); serial incremented", "rollback": "Revert file; re-load prior zone", "time_est": "10m"},
                        {"number": 3, "action": "Reload zone on primary authoritative server", "verify": "Reload succeeds; log shows new serial loaded", "rollback": "Reload prior zone; confirm old serial active", "time_est": "5m"},
                        {"number": 4, "action": "Verify NOTIFY/AXFR to secondaries", "verify": "All secondaries show new serial within 60s", "rollback": "Force AXFR if needed; escalate if mismatched", "time_est": "10m"},
                        {"number": 5, "action": "External resolution check from multiple resolvers", "verify": "`dig @8.8.8.8`, `@1.1.1.1`, and internal resolvers all return expected record", "rollback": "Rollback zone if resolution incorrect", "time_est": "15m"},
                    ],
                    "validation": [
                        "Record resolves correctly from ≥ 3 external resolvers",
                        "All authoritative secondaries on matching serial",
                        "Dependent services (HTTPS cert SNI, mail, API) functional",
                        "No increase in NXDOMAIN / SERVFAIL rate in DNS telemetry",
                    ],
                    "rollback": {
                        "trigger": "Dependent service outage OR resolution returns wrong value",
                        "procedure": "Restore prior zone file, bump serial, reload, notify secondaries, verify resolution.",
                        "max_duration": "15m (plus TTL expiry window)",
                    },
                    "escalation": [
                        {"tier": 1, "role": "DNS Administrator", "contact": "dns-admin@agency.gov"},
                        {"tier": 2, "role": "Infrastructure Lead", "contact": "infra-lead@agency.gov"},
                        {"tier": 3, "role": "CISO", "contact": "ciso@agency.gov"},
                    ],
                },
            ]
            _now_iso = datetime.now(timezone.utc).isoformat()
            for s in _sops:
                conn.execute(
                    "INSERT OR IGNORE INTO ndc_sops "
                    "(sop_id, title, category, version, status, description, "
                    " prerequisites, steps, validation, rollback, escalation, "
                    " classification, author, created_at, updated_at) "
                    "VALUES (?, ?, ?, 1, 'draft', ?, ?, ?, ?, ?, ?, 'CUI', 'seed', ?, ?)",
                    (
                        s["sop_id"], s["title"], s["category"], s["description"],
                        json.dumps(s["prerequisites"]),
                        json.dumps(s["steps"]),
                        json.dumps(s["validation"]),
                        json.dumps(s["rollback"]),
                        json.dumps(s["escalation"]),
                        _now_iso, _now_iso,
                    ),
                )
                conn.execute(
                    "INSERT OR IGNORE INTO ndc_sop_approval_log "
                    "(id, sop_id, actor, action, comment, timestamp) "
                    "VALUES (?, ?, 'seed', 'created', 'Seeded example SOP', ?)",
                    (f"log-seed-{s['sop_id']}", s["sop_id"], _now_iso),
                )
            conn.commit()
            print(f"[init_db] Seeded {len(_sops)} NDC SOPs.", file=sys.stderr)

        # ── Auto-seed full SOP library if approved count is low ────────────
        approved_count = conn.execute(
            "SELECT COUNT(*) FROM ndc_sops WHERE status='approved'"
        ).fetchone()[0]
        if approved_count < 20:
            try:
                from tools.network.seed_sops import seed as _seed_sops
                result = _seed_sops(status="approved")
                if result["seeded"] > 0:
                    print(f"[init_db] Auto-seeded {result['seeded']} approved SOPs via seed_sops.", file=sys.stderr)
            except Exception as _e:
                print(f"[init_db] seed_sops auto-seed skipped: {_e}", file=sys.stderr)

        # ── Auto-seed demo migration projects if none exist ────────────────
        migration_count = conn.execute(
            "SELECT COUNT(*) FROM nc_migration_phases"
        ).fetchone()[0]
        if migration_count == 0:
            try:
                from tools.network.seed_migration_demo import seed_demo_migrations
                seed_demo_migrations()
                print("[init_db] Auto-seeded demo migration projects.", file=sys.stderr)
            except Exception as _e:
                print(f"[init_db] demo migration seed skipped: {_e}", file=sys.stderr)

        conn.execute(
            "INSERT INTO nc_audit (action, entity_type, details) VALUES (?,?,?)",
            ("INIT", "database", f"Schema initialized at {datetime.now(timezone.utc).isoformat()}"),
        )
        conn.commit()
        print("[init_db] Done.", file=sys.stderr)
    finally:
        conn.close()


if __name__ == "__main__":
    init_db()
    if "--json" in sys.argv:
        import json as _json

        print(_json.dumps({"status": "ok", "db": str(DB_PATH), "templates": len(TEMPLATES)}))
