# CUI // SP-CTI
"""Data Design Canvas — DB initializer.

Creates schema and seeds 6 canonical data model templates.

Dual-backend: SQLite (default) or PostgreSQL.
Set DDC_STORAGE_BACKEND=postgresql + DDC_PG_* env vars to use PostgreSQL.
SQLite is the default for dev, air-gap, and single-user deployments.
"""

import json
import logging
import os
import re
import uuid
from pathlib import Path

logger = logging.getLogger(__name__)

# When integrated into ICDEV, DB lives in data/ directory
_ICDEV_ROOT = Path(__file__).resolve().parents[3]  # tools/data_canvas/db -> ICDev root
DB_PATH = _ICDEV_ROOT / "data" / "data_canvas.db"

# Backend detection
_DDC_BACKEND = os.environ.get("DDC_STORAGE_BACKEND", os.environ.get("ICDEV_CANVAS_STORAGE_BACKEND", os.environ.get("ICDEV_STORAGE_BACKEND", "postgresql"))).lower()


_WORD_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
_DOLLAR_RE = re.compile(r"\$[A-Za-z0-9_]*\$")

# Errors that are tolerable during init/seed on the primary (PG) backend:
# the object already exists from a prior run or a migration. These must be
# swallowed so a re-init is idempotent — but any OTHER error is a real defect
# and must be surfaced (logged), never silently discarded.
_BENIGN_DDL_MARKERS = (
    "already exists",
    "duplicate column",
    "duplicate object",
    "duplicate table",
    "duplicate key",
)


def _split_sql_statements(sql):
    """Split a SQL script into individual statements.

    A naive ``sql.split(';')`` fragments SQLite ``CREATE TRIGGER ... BEGIN
    ... END;`` bodies (which contain embedded semicolons) into invalid pieces
    that fail one-by-one — historically swallowed by a blanket ``except: pass``,
    hiding real DDL errors. This splitter only breaks on a semicolon that is at
    the *top level* — i.e. not inside a ``BEGIN..END`` block, not inside a
    ``$tag$..$tag$`` dollar-quoted body, and not inside a ``'...'`` string
    literal or a ``--`` line comment.

    Returns a list of trimmed, non-empty statements. Leading full-line ``--``
    comments are stripped from each statement so it begins at its first real
    token (keeping the ``CREATE TRIGGER``/``CREATE TABLE`` prefix detectable);
    comments embedded inside a statement body are preserved verbatim.
    """

    def _emit(text):
        # Drop leading blank / comment-only lines so the statement starts at
        # its first executable token.
        lines = text.splitlines()
        while lines and (not lines[0].strip() or lines[0].lstrip().startswith("--")):
            lines.pop(0)
        stmt = "\n".join(lines).strip()
        if stmt:
            statements.append(stmt)

    statements = []
    buf = []
    depth = 0            # BEGIN..END nesting depth
    in_single = False    # inside a '...' string literal
    dollar_tag = None    # active $tag$ dollar-quote delimiter, or None
    i = 0
    n = len(sql)
    while i < n:
        ch = sql[i]

        # Inside a dollar-quoted body: consume until the matching close tag.
        if dollar_tag is not None:
            if sql.startswith(dollar_tag, i):
                buf.append(dollar_tag)
                i += len(dollar_tag)
                dollar_tag = None
            else:
                buf.append(ch)
                i += 1
            continue

        # Inside a single-quoted string literal.
        if in_single:
            buf.append(ch)
            if ch == "'":
                if i + 1 < n and sql[i + 1] == "'":  # escaped ''
                    buf.append("'")
                    i += 2
                    continue
                in_single = False
            i += 1
            continue

        # Line comment: consume to end of line (keeps embedded ';' harmless).
        if ch == "-" and i + 1 < n and sql[i + 1] == "-":
            j = sql.find("\n", i)
            if j == -1:
                buf.append(sql[i:])
                i = n
            else:
                buf.append(sql[i:j])
                i = j
            continue

        # Enter a string literal.
        if ch == "'":
            in_single = True
            buf.append(ch)
            i += 1
            continue

        # Enter a dollar-quoted body ($$ or $tag$).
        if ch == "$":
            m = _DOLLAR_RE.match(sql, i)
            if m:
                dollar_tag = m.group(0)
                buf.append(dollar_tag)
                i += len(dollar_tag)
                continue

        # Word: track BEGIN..END nesting on word boundaries.
        if ch.isalpha() or ch == "_":
            m = _WORD_RE.match(sql, i)
            word = m.group(0)
            up = word.upper()
            if up == "BEGIN":
                depth += 1
            elif up == "END" and depth > 0:
                depth -= 1
            buf.append(word)
            i = m.end()
            continue

        # Top-level statement terminator.
        if ch == ";" and depth == 0:
            _emit("".join(buf))
            buf = []
            i += 1
            continue

        buf.append(ch)
        i += 1

    _emit("".join(buf))
    return statements


def _is_benign_ddl_error(exc):
    """True if a DDL error is a tolerable 'already exists' during re-init."""
    msg = str(exc).lower()
    return any(marker in msg for marker in _BENIGN_DDL_MARKERS)


def get_connection():
    """Get a database connection — SQLite or PostgreSQL.

    Returns a connection that supports:
        conn.execute(sql, params) — with ? placeholders (auto-translated for PG)
        conn.commit()
        conn.close()
        row["column_name"] — dict-like row access
    """
    if _DDC_BACKEND == "postgresql":
        try:
            from tools.db.storage import get_canvas_connection as _icdev_canvas_conn

            return _icdev_canvas_conn()
        except ImportError:
            pass  # Fall through to SQLite
    # SQLite (default)
    import sqlite3 as _sqlite3
    conn = _sqlite3.connect(str(DB_PATH))
    conn.row_factory = _sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


SCHEMA = """
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
    user            TEXT,
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

CREATE TABLE IF NOT EXISTS data_nodes (
    id              TEXT PRIMARY KEY,
    design_id       TEXT NOT NULL REFERENCES data_designs(id) ON DELETE CASCADE,
    node_type       TEXT NOT NULL DEFAULT 'table',
    label           TEXT DEFAULT '',
    x               REAL DEFAULT 0,
    y               REAL DEFAULT 0,
    classification  TEXT DEFAULT 'CUI',
    properties_json TEXT DEFAULT '{}',
    created_at      TEXT DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_data_nodes_design ON data_nodes(design_id);
CREATE INDEX IF NOT EXISTS idx_data_nodes_type   ON data_nodes(node_type);

CREATE TABLE IF NOT EXISTS data_edges (
    id              TEXT PRIMARY KEY,
    design_id       TEXT NOT NULL REFERENCES data_designs(id) ON DELETE CASCADE,
    source_node_id  TEXT NOT NULL,
    target_node_id  TEXT NOT NULL,
    edge_type       TEXT DEFAULT '',
    label           TEXT DEFAULT '',
    classification  TEXT DEFAULT 'CUI',
    created_at      TEXT DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_data_edges_design ON data_edges(design_id);
CREATE INDEX IF NOT EXISTS idx_data_edges_source ON data_edges(source_node_id);
CREATE INDEX IF NOT EXISTS idx_data_edges_target ON data_edges(target_node_id);

CREATE TABLE IF NOT EXISTS data_twin_snapshots (
    id              TEXT PRIMARY KEY,
    design_id       TEXT NOT NULL REFERENCES data_designs(id) ON DELETE CASCADE,
    label           TEXT DEFAULT '',
    table_count     INTEGER DEFAULT 0,
    edge_count      INTEGER DEFAULT 0,
    classification  TEXT DEFAULT 'CUI',
    created_at      TEXT DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_data_twin_snapshots_design ON data_twin_snapshots(design_id);

-- Immutability triggers (SQLite)
CREATE TRIGGER IF NOT EXISTS dd_audit_no_update
    BEFORE UPDATE ON dd_audit
    BEGIN
        SELECT RAISE(ABORT, 'Audit records are immutable — NIST AU-6');
    END;

CREATE TRIGGER IF NOT EXISTS dd_audit_no_delete
    BEFORE DELETE ON dd_audit
    BEGIN
        SELECT RAISE(ABORT, 'Audit records cannot be deleted');
    END;

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

-- ── Data Science: Explore (Profiler) ──────────────────────────────────────────
CREATE TABLE IF NOT EXISTS dd_explore_sessions (
    id              TEXT PRIMARY KEY,
    design_id       TEXT,
    user            TEXT DEFAULT '',
    db_conn_json    TEXT DEFAULT '{}',
    status          TEXT DEFAULT 'completed',
    classification  TEXT DEFAULT 'CUI // SP-CTI',
    created_at      TEXT DEFAULT CURRENT_TIMESTAMP
);

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

CREATE TABLE IF NOT EXISTS dd_anomaly_runs (
    id              TEXT PRIMARY KEY,
    profile_id      TEXT,
    findings_json   TEXT,
    overall_risk    TEXT,
    classification  TEXT,
    created_at      TEXT
);

CREATE INDEX IF NOT EXISTS idx_dd_explore_profiles_design ON dd_explore_profiles(design_id);
CREATE INDEX IF NOT EXISTS idx_dd_explore_sessions_design ON dd_explore_sessions(design_id);

-- ── Data Science: Query Sandbox ────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS dd_query_history (
    id              TEXT PRIMARY KEY,
    design_id       TEXT,
    user            TEXT DEFAULT '',
    sql_text        TEXT NOT NULL,
    db_conn_json    TEXT DEFAULT '{}',
    row_count       INTEGER DEFAULT 0,
    exec_ms         INTEGER DEFAULT 0,
    classification  TEXT DEFAULT 'CUI // SP-CTI',
    created_at      TEXT DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_dd_query_history_design ON dd_query_history(design_id);

-- ── Data Science: Quality Rules ────────────────────────────────────────────────
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

CREATE TABLE IF NOT EXISTS dd_quality_runs (
    id              TEXT PRIMARY KEY,
    rule_id         TEXT REFERENCES dd_quality_rules(id) ON DELETE CASCADE,
    db_conn_json    TEXT DEFAULT '{}',
    passed          INTEGER DEFAULT 0,
    actual_value    REAL DEFAULT 0.0,
    threshold       REAL DEFAULT 0.0,
    detail          TEXT DEFAULT '',
    classification  TEXT DEFAULT 'CUI // SP-CTI',
    created_at      TEXT DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_dd_quality_rules_design ON dd_quality_rules(design_id);
CREATE INDEX IF NOT EXISTS idx_dd_quality_runs_rule ON dd_quality_runs(rule_id);

CREATE TABLE IF NOT EXISTS dd_freshness_alerts (
    id              TEXT PRIMARY KEY,
    rule_id         TEXT NOT NULL REFERENCES dd_quality_rules(id),
    design_id       TEXT,
    db_conn_json    TEXT,
    last_checked    TEXT,
    passed          INTEGER,
    actual_max_value TEXT,
    cutoff_value    TEXT,
    detail          TEXT,
    classification  TEXT DEFAULT 'CUI // SP-CTI',
    created_at      TEXT
);

-- ── Data Mesh Foundation Tables ───────────────────────────────────────────────

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

CREATE TABLE IF NOT EXISTS dm_ports (
    id              TEXT PRIMARY KEY,
    product_id      TEXT REFERENCES dm_data_products(id) ON DELETE CASCADE,
    name            TEXT NOT NULL,
    port_type       TEXT NOT NULL DEFAULT 'input'
                    CHECK (port_type IN ('input', 'output')),
    transport_type  TEXT DEFAULT 'api',
    schema_json     TEXT DEFAULT '{}',
    endpoint        TEXT DEFAULT '',
    source_system   TEXT DEFAULT '',
    sla_json        TEXT DEFAULT '{}',
    classification  TEXT DEFAULT 'CUI // SP-CTI',
    created_at      TEXT DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_dm_ports_product   ON dm_ports(product_id);
CREATE INDEX IF NOT EXISTS idx_dm_ports_port_type ON dm_ports(port_type);

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
    user            TEXT DEFAULT '',
    action          TEXT NOT NULL,
    detail          TEXT DEFAULT '',
    classification  TEXT DEFAULT 'CUI // SP-CTI',
    created_at      TEXT DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_dm_audit_domain  ON dm_audit(domain_id);
CREATE INDEX IF NOT EXISTS idx_dm_audit_product ON dm_audit(product_id);

CREATE TRIGGER IF NOT EXISTS dm_audit_no_update
    BEFORE UPDATE ON dm_audit
    BEGIN
        SELECT RAISE(ABORT, 'dm_audit records are immutable — NIST AU-6');
    END;

CREATE TRIGGER IF NOT EXISTS dm_audit_no_delete
    BEFORE DELETE ON dm_audit
    BEGIN
        SELECT RAISE(ABORT, 'dm_audit records cannot be deleted');
    END;

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
    user            TEXT DEFAULT 'system',
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

-- ── AI Data Mapping ───────────────────────────────────────────────────────────

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

CREATE TRIGGER IF NOT EXISTS dd_mapping_transforms_no_update
    BEFORE UPDATE ON dd_mapping_transforms
    BEGIN SELECT RAISE(ABORT,'dd_mapping_transforms is append-only — NIST AU-9'); END;

CREATE TRIGGER IF NOT EXISTS dd_mapping_transforms_no_delete
    BEFORE DELETE ON dd_mapping_transforms
    BEGIN SELECT RAISE(ABORT,'dd_mapping_transforms is append-only — NIST AU-9'); END;

-- ── PII Scanner results ───────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS dd_pii_scans (
    scan_id         TEXT PRIMARY KEY,
    design_id       TEXT NOT NULL DEFAULT '',
    overall_risk    TEXT NOT NULL DEFAULT 'none'
                    CHECK (overall_risk IN ('none','low','medium','high','critical')),
    findings_json   TEXT NOT NULL DEFAULT '[]',
    scanned_at      TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_dd_pii_scans_design   ON dd_pii_scans(design_id);
CREATE INDEX IF NOT EXISTS idx_dd_pii_scans_risk      ON dd_pii_scans(overall_risk);
CREATE INDEX IF NOT EXISTS idx_dd_pii_scans_scanned   ON dd_pii_scans(scanned_at DESC);
"""


# ── Template seeds ────────────────────────────────────────────────────────────


def _node(nid, label, ntype, x, y, extra=None):
    n = {"id": nid, "label": label, "type": ntype, "x": x, "y": y}
    if extra:
        n.update(extra)
    return n


def _edge(src, dst, label="", edge_type=""):
    e = {"id": str(uuid.uuid4())[:8], "source": src, "target": dst, "label": label}
    if edge_type:
        e["type"] = edge_type
    return e


def _boundary(bid, label, btype, contained_nodes, x=0, y=0, width=300, height=250):
    return {
        "id": bid,
        "label": label,
        "type": btype,
        "contained_nodes": contained_nodes,
        "x": x,
        "y": y,
        "width": width,
        "height": height,
    }


TEMPLATES = [
    # 1 — OLTP Microservice (PostgreSQL)
    {
        "id": "tpl-ddc-oltp-microservice",
        "name": "OLTP Microservice (PostgreSQL)",
        "category": "Relational",
        "description": "PostgreSQL tables with PK/FK, PII columns, RBAC, audit logging, and CUI classification zone.",
        "tags": json.dumps(["postgresql", "oltp", "microservice", "cui", "pii"]),
        "graph_json": json.dumps(
            {
                "nodes": [
                    _node("users-tbl", "users", "ent-table", 200, 150),
                    _node("orders-tbl", "orders", "ent-table", 500, 150),
                    _node("audit-tbl", "audit_log", "ent-table", 350, 350),
                    _node("users-pk", "id (PK)", "col-pk", 80, 80),
                    _node("users-email", "email (PII)", "col-pii", 80, 150),
                    _node("users-name", "full_name (PII)", "col-pii", 80, 220),
                    _node("users-cui", "clearance_level (CUI)", "col-cui", 80, 290),
                    _node("users-audit", "created_at", "col-audit", 200, 290),
                    _node("orders-pk", "id (PK)", "col-pk", 620, 80),
                    _node("orders-fk", "user_id (FK)", "col-fk", 620, 150),
                    _node("orders-data", "amount", "col-data", 620, 220),
                    _node("orders-audit", "updated_at", "col-audit", 500, 290),
                    _node("rbac", "RBAC Policy", "ctrl-rbac", 350, 50),
                    _node("enc", "AES-256 TDE", "ctrl-encryption", 200, 450),
                    _node("mask", "PII Masking", "ctrl-masking", 80, 400),
                    _node("auditlog", "Audit Logging", "ctrl-audit-log", 500, 450),
                    _node("retention", "7-Year Retention", "ctrl-retention", 350, 500),
                    _node("backup", "Daily Backup (RPO 1h)", "ctrl-backup-policy", 200, 550),
                    _node("api-flow", "REST API", "flow-api", 350, 150),
                    _node("iac-flyway", "Flyway (Schema Migration)", "flow-etl", 350, 620),
                ],
                "edges": [
                    _edge("users-tbl", "users-pk"),
                    _edge("users-tbl", "users-email"),
                    _edge("users-tbl", "users-name"),
                    _edge("users-tbl", "users-cui"),
                    _edge("users-tbl", "users-audit"),
                    _edge("orders-tbl", "orders-pk"),
                    _edge("orders-tbl", "orders-fk"),
                    _edge("orders-tbl", "orders-data"),
                    _edge("orders-tbl", "orders-audit"),
                    _edge("orders-fk", "users-pk", "FK ref"),
                    _edge("users-tbl", "rbac"),
                    _edge("orders-tbl", "rbac"),
                    _edge("users-tbl", "enc"),
                    _edge("orders-tbl", "enc"),
                    _edge("users-tbl", "mask"),
                    _edge("users-tbl", "auditlog"),
                    _edge("orders-tbl", "auditlog"),
                    _edge("audit-tbl", "auditlog"),
                    _edge("users-tbl", "retention"),
                    _edge("orders-tbl", "retention"),
                    _edge("users-tbl", "backup"),
                    _edge("orders-tbl", "backup"),
                    _edge("users-tbl", "api-flow", "", "flow-api"),
                    _edge("api-flow", "orders-tbl", "", "flow-api"),
                    _edge("iac-flyway", "users-tbl", "migrate schema", "flow-etl"),
                ],
                "boundaries": [
                    _boundary(
                        "cui-zone",
                        "CUI // SP-CTI Zone",
                        "bnd-classification",
                        ["users-tbl", "orders-tbl", "audit-tbl"],
                        x=140,
                        y=80,
                        width=450,
                        height=320,
                    ),
                    _boundary(
                        "us-region",
                        "US East (GovCloud)",
                        "bnd-region",
                        ["users-tbl", "orders-tbl", "audit-tbl"],
                        x=130,
                        y=70,
                        width=470,
                        height=340,
                    ),
                ],
            }
        ),
    },
    # 2 — Data Lake (S3 + Redshift)
    {
        "id": "tpl-ddc-data-lake",
        "name": "Data Lake (S3 + Redshift)",
        "category": "Analytics",
        "description": "S3 data lake with Redshift warehouse, ETL pipeline, and classification zones for CUI analytics.",
        "tags": json.dumps(["s3", "redshift", "datalake", "etl", "analytics"]),
        "graph_json": json.dumps(
            {
                "nodes": [
                    _node("raw-lake", "Raw Data Lake (S3)", "ent-datalake", 100, 150),
                    _node("curated-lake", "Curated Zone (S3)", "ent-datalake", 350, 150),
                    _node("warehouse", "Analytics Warehouse", "ent-warehouse", 600, 150),
                    _node("etl1", "Raw -> Curated ETL", "flow-etl", 225, 150),
                    _node("etl2", "Curated -> Warehouse ETL", "flow-etl", 475, 150),
                    _node("enc", "KMS Encryption", "ctrl-encryption", 350, 50),
                    _node("rbac", "IAM RBAC", "ctrl-rbac", 100, 50),
                    _node("auditlog", "CloudTrail Audit", "ctrl-audit-log", 600, 50),
                    _node("dlp", "DLP Egress Filter", "ctrl-dlp", 600, 300),
                    _node("retention", "5-Year Retention", "ctrl-retention", 100, 300),
                    _node("backup", "Cross-Region Backup", "ctrl-backup-policy", 350, 300),
                    _node("export", "BI Dashboard Export", "flow-export", 750, 150),
                    _node("iac-terraform", "Terraform (IaC)", "flow-etl", 350, 400),
                ],
                "edges": [
                    _edge("raw-lake", "etl1", "extract", "flow-etl"),
                    _edge("etl1", "curated-lake", "load", "flow-etl"),
                    _edge("curated-lake", "etl2", "extract", "flow-etl"),
                    _edge("etl2", "warehouse", "load", "flow-etl"),
                    _edge("warehouse", "export", "export", "flow-export"),
                    _edge("raw-lake", "enc"),
                    _edge("curated-lake", "enc"),
                    _edge("warehouse", "enc"),
                    _edge("raw-lake", "rbac"),
                    _edge("curated-lake", "rbac"),
                    _edge("warehouse", "rbac"),
                    _edge("raw-lake", "auditlog"),
                    _edge("curated-lake", "auditlog"),
                    _edge("warehouse", "auditlog"),
                    _edge("warehouse", "dlp"),
                    _edge("raw-lake", "retention"),
                    _edge("curated-lake", "retention"),
                    _edge("warehouse", "retention"),
                    _edge("raw-lake", "backup"),
                    _edge("warehouse", "backup"),
                    _edge("iac-terraform", "raw-lake", "provision infra", "flow-etl"),
                ],
                "boundaries": [
                    _boundary(
                        "cui-zone",
                        "CUI Analytics Zone",
                        "bnd-classification",
                        ["raw-lake", "curated-lake", "warehouse"],
                        x=50,
                        y=80,
                        width=750,
                        height=150,
                    ),
                    _boundary(
                        "govcloud",
                        "AWS GovCloud US-East",
                        "bnd-region",
                        ["raw-lake", "curated-lake", "warehouse"],
                        x=40,
                        y=70,
                        width=770,
                        height=170,
                    ),
                ],
            }
        ),
    },
    # 3 — Event-Driven (Kafka + MongoDB)
    {
        "id": "tpl-ddc-event-driven",
        "name": "Event-Driven (Kafka + MongoDB)",
        "category": "Event Streaming",
        "description": "Kafka topics, MongoDB collections, CDC streams, and DLP on egress flows.",
        "tags": json.dumps(["kafka", "mongodb", "event-driven", "cdc", "streaming"]),
        "graph_json": json.dumps(
            {
                "nodes": [
                    _node("orders-topic", "orders.events", "ent-topic", 100, 150),
                    _node("payments-topic", "payments.events", "ent-topic", 100, 300),
                    _node("orders-col", "orders", "ent-collection", 400, 150),
                    _node("payments-col", "payments", "ent-collection", 400, 300),
                    _node("cache", "Session Cache", "ent-cache", 250, 450),
                    _node("cdc", "Debezium CDC", "flow-cdc", 550, 225),
                    _node("analytics-wh", "Analytics Warehouse", "ent-warehouse", 700, 225),
                    _node("api-flow", "API Gateway", "flow-api", 250, 50),
                    _node("rbac", "Service RBAC", "ctrl-rbac", 100, 50),
                    _node("enc", "Encryption at Rest", "ctrl-encryption", 400, 50),
                    _node("auditlog", "Centralized Audit", "ctrl-audit-log", 550, 50),
                    _node("dlp", "DLP Egress Policy", "ctrl-dlp", 700, 350),
                    _node("retention", "90-Day Retention", "ctrl-retention", 100, 450),
                    _node("backup", "Backup Policy", "ctrl-backup-policy", 400, 450),
                    _node("iac-terraform", "Terraform (IaC)", "flow-etl", 250, 530),
                ],
                "edges": [
                    _edge("api-flow", "orders-topic", "produce", "flow-api"),
                    _edge("api-flow", "payments-topic", "produce", "flow-api"),
                    _edge("orders-topic", "orders-col", "consume"),
                    _edge("payments-topic", "payments-col", "consume"),
                    _edge("orders-col", "cdc", "CDC", "flow-cdc"),
                    _edge("payments-col", "cdc", "CDC", "flow-cdc"),
                    _edge("cdc", "analytics-wh", "replicate", "flow-cdc"),
                    _edge("orders-col", "rbac"),
                    _edge("payments-col", "rbac"),
                    _edge("orders-col", "enc"),
                    _edge("payments-col", "enc"),
                    _edge("orders-col", "auditlog"),
                    _edge("payments-col", "auditlog"),
                    _edge("analytics-wh", "dlp"),
                    _edge("orders-topic", "retention"),
                    _edge("payments-topic", "retention"),
                    _edge("orders-col", "backup"),
                    _edge("payments-col", "backup"),
                    _edge("api-flow", "cache", "session"),
                    _edge("iac-terraform", "orders-topic", "provision infra", "flow-etl"),
                ],
                "boundaries": [
                    _boundary(
                        "cui-zone",
                        "CUI Processing Zone",
                        "bnd-classification",
                        ["orders-topic", "payments-topic", "orders-col", "payments-col", "cache", "analytics-wh"],
                        x=50,
                        y=80,
                        width=720,
                        height=420,
                    ),
                ],
            }
        ),
    },
    # 4 — HIPAA Compliant (PHI)
    {
        "id": "tpl-ddc-hipaa",
        "name": "HIPAA Compliant (PHI)",
        "category": "Healthcare",
        "description": "PHI-tagged columns, encryption, masking, audit logging, and HIPAA data residency zone.",
        "tags": json.dumps(["hipaa", "phi", "healthcare", "encryption", "masking"]),
        "graph_json": json.dumps(
            {
                "nodes": [
                    _node("patients", "patients", "ent-table", 200, 150),
                    _node("encounters", "encounters", "ent-table", 500, 150),
                    _node("pat-pk", "patient_id (PK)", "col-pk", 80, 80),
                    _node("pat-name", "full_name (PII)", "col-pii", 80, 150),
                    _node("pat-ssn", "ssn (PII)", "col-pii", 80, 220),
                    _node("pat-dx", "diagnosis (PHI)", "col-phi", 80, 290),
                    _node("pat-meds", "medications (PHI)", "col-phi", 80, 360),
                    _node("pat-audit", "created_at", "col-audit", 200, 360),
                    _node("enc-pk", "encounter_id (PK)", "col-pk", 620, 80),
                    _node("enc-fk", "patient_id (FK)", "col-fk", 620, 150),
                    _node("enc-notes", "clinical_notes (PHI)", "col-phi", 620, 220),
                    _node("enc-audit", "updated_at", "col-audit", 500, 290),
                    _node("rbac", "HIPAA RBAC", "ctrl-rbac", 350, 50),
                    _node("enc", "AES-256 TDE", "ctrl-encryption", 200, 450),
                    _node("mask", "PHI Masking", "ctrl-masking", 80, 430),
                    _node("auditlog", "HIPAA Audit Log", "ctrl-audit-log", 500, 450),
                    _node("retention", "6-Year HIPAA Retention", "ctrl-retention", 350, 500),
                    _node("backup", "HIPAA Backup (RPO 15m)", "ctrl-backup-policy", 200, 550),
                    _node("dlp", "PHI DLP Policy", "ctrl-dlp", 500, 550),
                    _node("classification", "PHI Classification", "ctrl-classification", 350, 400),
                    _node("iac-flyway", "Flyway (Schema Migration)", "flow-etl", 350, 630),
                ],
                "edges": [
                    _edge("patients", "pat-pk"),
                    _edge("patients", "pat-name"),
                    _edge("patients", "pat-ssn"),
                    _edge("patients", "pat-dx"),
                    _edge("patients", "pat-meds"),
                    _edge("patients", "pat-audit"),
                    _edge("encounters", "enc-pk"),
                    _edge("encounters", "enc-fk"),
                    _edge("encounters", "enc-notes"),
                    _edge("encounters", "enc-audit"),
                    _edge("enc-fk", "pat-pk", "FK ref"),
                    _edge("patients", "rbac"),
                    _edge("encounters", "rbac"),
                    _edge("patients", "enc"),
                    _edge("encounters", "enc"),
                    _edge("patients", "mask"),
                    _edge("patients", "auditlog"),
                    _edge("encounters", "auditlog"),
                    _edge("patients", "retention"),
                    _edge("encounters", "retention"),
                    _edge("patients", "backup"),
                    _edge("encounters", "backup"),
                    _edge("patients", "dlp"),
                    _edge("encounters", "dlp"),
                    _edge("patients", "classification"),
                    _edge("encounters", "classification"),
                    _edge("iac-flyway", "patients", "migrate schema", "flow-etl"),
                ],
                "boundaries": [
                    _boundary(
                        "hipaa-zone",
                        "HIPAA PHI Zone",
                        "bnd-classification",
                        ["patients", "encounters"],
                        x=140,
                        y=80,
                        width=540,
                        height=250,
                    ),
                    _boundary(
                        "us-hipaa",
                        "US HIPAA Data Residency",
                        "bnd-region",
                        ["patients", "encounters"],
                        x=130,
                        y=70,
                        width=560,
                        height=270,
                    ),
                ],
            }
        ),
    },
    # 5 — Multi-Classification (CUI + SECRET)
    {
        "id": "tpl-ddc-multi-classification",
        "name": "Multi-Classification (CUI + SECRET)",
        "category": "DoD/IC",
        "description": "Two classification zones (CUI and SECRET) with cross-domain guard between them.",
        "tags": json.dumps(["dod", "secret", "cui", "cross-domain", "cds"]),
        "graph_json": json.dumps(
            {
                "nodes": [
                    _node("cui-db", "CUI Operations DB", "ent-table", 150, 200),
                    _node("cui-files", "CUI File Store", "ent-file", 150, 350),
                    _node("secret-db", "SECRET Intel DB", "ent-table", 650, 200),
                    _node("secret-cache", "SECRET Cache", "ent-cache", 650, 350),
                    _node("cui-col", "mission_data (CUI)", "col-cui", 30, 200),
                    _node("sec-col", "intel_report (SECRET)", "col-secret", 770, 200),
                    _node("cui-audit", "created_at", "col-audit", 30, 270),
                    _node("sec-audit", "created_at", "col-audit", 770, 270),
                    _node("cds-flow", "Cross-Domain Guard", "flow-cross-domain", 400, 275),
                    _node("cui-enc", "FIPS 140-2 Encryption", "ctrl-encryption", 150, 100),
                    _node("sec-enc", "NSA Type 1 Encryption", "ctrl-encryption", 650, 100),
                    _node("cui-rbac", "CUI RBAC (CAC)", "ctrl-rbac", 50, 100),
                    _node("sec-rbac", "SECRET RBAC (SCI)", "ctrl-rbac", 750, 100),
                    _node("cui-audit-ctrl", "CUI Audit Log", "ctrl-audit-log", 250, 100),
                    _node("sec-audit-ctrl", "SECRET Audit Log", "ctrl-audit-log", 550, 100),
                    _node("cui-retention", "7-Year Retention", "ctrl-retention", 150, 450),
                    _node("sec-retention", "25-Year Retention", "ctrl-retention", 650, 450),
                    _node("cui-backup", "GovCloud Backup", "ctrl-backup-policy", 50, 450),
                    _node("sec-backup", "SIPR Backup", "ctrl-backup-policy", 750, 450),
                    _node("iac-terraform", "Terraform (IaC)", "flow-etl", 400, 530),
                ],
                "edges": [
                    _edge("cui-db", "cui-col"),
                    _edge("cui-db", "cui-audit"),
                    _edge("secret-db", "sec-col"),
                    _edge("secret-db", "sec-audit"),
                    _edge("cui-db", "cds-flow", "CUI->SECRET", "flow-cross-domain"),
                    _edge("cds-flow", "secret-db", "filtered", "flow-cross-domain"),
                    _edge("cui-db", "cui-enc"),
                    _edge("cui-files", "cui-enc"),
                    _edge("secret-db", "sec-enc"),
                    _edge("secret-cache", "sec-enc"),
                    _edge("cui-db", "cui-rbac"),
                    _edge("cui-files", "cui-rbac"),
                    _edge("secret-db", "sec-rbac"),
                    _edge("secret-cache", "sec-rbac"),
                    _edge("cui-db", "cui-audit-ctrl"),
                    _edge("cui-files", "cui-audit-ctrl"),
                    _edge("secret-db", "sec-audit-ctrl"),
                    _edge("secret-cache", "sec-audit-ctrl"),
                    _edge("cui-db", "cui-retention"),
                    _edge("cui-files", "cui-retention"),
                    _edge("secret-db", "sec-retention"),
                    _edge("secret-cache", "sec-retention"),
                    _edge("cui-db", "cui-backup"),
                    _edge("secret-db", "sec-backup"),
                    _edge("iac-terraform", "cui-db", "provision infra", "flow-etl"),
                ],
                "boundaries": [
                    _boundary(
                        "cui-zone",
                        "CUI // SP-CTI Zone",
                        "bnd-classification",
                        ["cui-db", "cui-files"],
                        x=30,
                        y=120,
                        width=300,
                        height=300,
                    ),
                    _boundary(
                        "secret-zone",
                        "SECRET // NOFORN Zone",
                        "bnd-classification",
                        ["secret-db", "secret-cache"],
                        x=530,
                        y=120,
                        width=300,
                        height=300,
                    ),
                    _boundary(
                        "govcloud",
                        "AWS GovCloud (IL5)",
                        "bnd-region",
                        ["cui-db", "cui-files"],
                        x=20,
                        y=110,
                        width=320,
                        height=320,
                    ),
                    _boundary(
                        "sipr",
                        "SIPR Enclave (IL6)",
                        "bnd-region",
                        ["secret-db", "secret-cache"],
                        x=520,
                        y=110,
                        width=320,
                        height=320,
                    ),
                ],
            }
        ),
    },
    # 6 — Graph + Vector RAG Pipeline
    {
        "id": "tpl-ddc-rag-pipeline",
        "name": "Graph + Vector RAG Pipeline",
        "category": "AI/ML",
        "description": "Knowledge graph, vector DB, and API flows for RAG pipeline with tenant isolation.",
        "tags": json.dumps(["rag", "vector", "graph", "ai", "multi-tenant"]),
        "graph_json": json.dumps(
            {
                "nodes": [
                    _node("docs-store", "Document Store (S3)", "ent-file", 100, 150),
                    _node("vector-db", "Vector DB (pgvector)", "ent-vector", 350, 150),
                    _node("graph-db", "Knowledge Graph (Neo4j)", "ent-graph", 350, 300),
                    _node("cache", "Embedding Cache (Redis)", "ent-cache", 600, 150),
                    _node("api-ingest", "Ingest API", "flow-api", 225, 75),
                    _node("api-query", "Query API", "flow-api", 475, 75),
                    _node("etl", "Embedding ETL", "flow-etl", 225, 225),
                    _node("rbac", "Tenant RBAC", "ctrl-rbac", 100, 50),
                    _node("enc", "AES-256 Encryption", "ctrl-encryption", 350, 50),
                    _node("auditlog", "Query Audit Log", "ctrl-audit-log", 600, 50),
                    _node("retention", "1-Year Retention", "ctrl-retention", 100, 400),
                    _node("backup", "Daily Backup", "ctrl-backup-policy", 350, 400),
                    _node("iac-terraform", "Terraform (IaC)", "flow-etl", 225, 470),
                ],
                "edges": [
                    _edge("api-ingest", "docs-store", "upload", "flow-api"),
                    _edge("docs-store", "etl", "extract", "flow-etl"),
                    _edge("etl", "vector-db", "embed", "flow-etl"),
                    _edge("etl", "graph-db", "extract entities", "flow-etl"),
                    _edge("api-query", "vector-db", "semantic search", "flow-api"),
                    _edge("api-query", "graph-db", "graph traversal", "flow-api"),
                    _edge("vector-db", "cache", "cache embeddings"),
                    _edge("docs-store", "rbac"),
                    _edge("vector-db", "rbac"),
                    _edge("graph-db", "rbac"),
                    _edge("docs-store", "enc"),
                    _edge("vector-db", "enc"),
                    _edge("graph-db", "enc"),
                    _edge("vector-db", "auditlog"),
                    _edge("graph-db", "auditlog"),
                    _edge("docs-store", "retention"),
                    _edge("vector-db", "retention"),
                    _edge("graph-db", "retention"),
                    _edge("docs-store", "backup"),
                    _edge("vector-db", "backup"),
                    _edge("graph-db", "backup"),
                    _edge("iac-terraform", "docs-store", "provision infra", "flow-etl"),
                ],
                "boundaries": [
                    _boundary(
                        "cui-zone",
                        "CUI Data Zone",
                        "bnd-classification",
                        ["docs-store", "vector-db", "graph-db", "cache"],
                        x=50,
                        y=80,
                        width=620,
                        height=290,
                    ),
                    _boundary(
                        "tenant-a",
                        "Tenant A",
                        "bnd-tenant",
                        ["docs-store", "vector-db", "graph-db"],
                        x=60,
                        y=90,
                        width=370,
                        height=270,
                    ),
                ],
            }
        ),
    },
    # 7 — OHDSI CDM v5.4 (OMOP)
    {
        "id": "tpl-ddc-ohdsi-cdm",
        "name": "OHDSI CDM v5.4 (OMOP)",
        "category": "Healthcare",
        "description": "OHDSI/OMOP Common Data Model v5.4 for federal healthcare data interoperability (VA, HHS). Person, visit, condition, drug, measurement, and observation domains with standard SNOMED/ICD/RxNorm vocabulary concepts.",
        "tags": json.dumps(["ohdsi", "omop", "cdm", "healthcare", "va", "hhs", "phi", "hipaa"]),
        "graph_json": json.dumps(
            {
                "nodes": [
                    _node("person", "person", "ent-table", 220, 190),
                    _node("visit", "visit_occurrence", "ent-table", 530, 190),
                    _node("condition", "condition_occurrence", "ent-table", 100, 370),
                    _node("drug", "drug_exposure", "ent-table", 310, 370),
                    _node("measurement", "measurement", "ent-table", 510, 370),
                    _node("observation", "observation", "ent-table", 710, 370),
                    _node("concept", "concept (SNOMED/ICD/RxNorm)", "ent-table", 400, 70),
                    # person columns
                    _node("per-pk", "person_id (PK)", "col-pk", 80, 140),
                    _node("per-phi1", "year_of_birth (PHI)", "col-phi", 80, 200),
                    _node("per-phi2", "gender_concept_id", "col-data", 80, 260),
                    _node("per-fk", "care_site_id (FK)", "col-fk", 80, 320),
                    # visit_occurrence columns
                    _node("vis-pk", "visit_occurrence_id (PK)", "col-pk", 680, 140),
                    _node("vis-fk", "person_id (FK)", "col-fk", 680, 200),
                    _node("vis-date", "visit_start_date", "col-data", 680, 260),
                    # controls
                    _node("rbac", "HIPAA RBAC", "ctrl-rbac", 350, 500),
                    _node("enc", "AES-256 TDE", "ctrl-encryption", 150, 500),
                    _node("mask", "PHI Masking", "ctrl-masking", 50, 470),
                    _node("auditlog", "HIPAA Audit Log", "ctrl-audit-log", 560, 500),
                    _node("retention", "6-Year HIPAA Retention", "ctrl-retention", 350, 570),
                    _node("dlp", "PHI DLP Policy", "ctrl-dlp", 660, 570),
                    _node("etl", "Standardized ETL (dbt/Kettle)", "flow-etl", 350, 650),
                ],
                "edges": [
                    _edge("person", "per-pk"),
                    _edge("person", "per-phi1"),
                    _edge("person", "per-phi2"),
                    _edge("person", "per-fk"),
                    _edge("visit", "vis-pk"),
                    _edge("visit", "vis-fk"),
                    _edge("visit", "vis-date"),
                    _edge("vis-fk", "per-pk", "FK ref"),
                    _edge("condition", "per-pk", "person_id FK"),
                    _edge("drug", "per-pk", "person_id FK"),
                    _edge("measurement", "per-pk", "person_id FK"),
                    _edge("observation", "per-pk", "person_id FK"),
                    _edge("condition", "vis-pk", "visit_occurrence_id FK"),
                    _edge("drug", "vis-pk", "visit_occurrence_id FK"),
                    _edge("measurement", "vis-pk", "visit_occurrence_id FK"),
                    _edge("concept", "condition", "standardize (ICD->SNOMED)", "flow-etl"),
                    _edge("concept", "drug", "standardize (NDC->RxNorm)", "flow-etl"),
                    _edge("concept", "measurement", "standardize (LOINC)", "flow-etl"),
                    _edge("concept", "observation", "standardize", "flow-etl"),
                    _edge("person", "rbac"),
                    _edge("visit", "rbac"),
                    _edge("condition", "rbac"),
                    _edge("drug", "rbac"),
                    _edge("person", "enc"),
                    _edge("visit", "enc"),
                    _edge("person", "mask"),
                    _edge("person", "auditlog"),
                    _edge("visit", "auditlog"),
                    _edge("condition", "auditlog"),
                    _edge("person", "retention"),
                    _edge("visit", "retention"),
                    _edge("condition", "dlp"),
                    _edge("drug", "dlp"),
                    _edge("etl", "person", "load CDM data", "flow-etl"),
                ],
                "boundaries": [
                    _boundary(
                        "phi-zone",
                        "HIPAA PHI Zone",
                        "bnd-classification",
                        ["person", "visit", "condition", "drug", "measurement", "observation"],
                        x=50,
                        y=100,
                        width=740,
                        height=310,
                    ),
                    _boundary(
                        "us-hipaa",
                        "US Healthcare Data Residency (VA/HHS)",
                        "bnd-region",
                        ["person", "visit", "condition", "drug", "measurement", "observation"],
                        x=40,
                        y=90,
                        width=760,
                        height=330,
                    ),
                ],
            }
        ),
    },
    # 8 — Microsoft Common Data Model (CDM)
    {
        "id": "tpl-ddc-ms-cdm",
        "name": "Microsoft Common Data Model (CDM)",
        "category": "Enterprise",
        "description": "Microsoft CDM/Dataverse entity schema for Power Platform, Dynamics 365, and Azure. Account, Contact, Lead, Opportunity, Case, and Activity entities with standard relationships and AAD/Entra RBAC.",
        "tags": json.dumps(["microsoft", "cdm", "dataverse", "dynamics365", "power-platform", "crm", "pii"]),
        "graph_json": json.dumps(
            {
                "nodes": [
                    _node("account", "Account", "ent-table", 260, 190),
                    _node("contact", "Contact", "ent-table", 560, 190),
                    _node("lead", "Lead", "ent-table", 100, 370),
                    _node("opportunity", "Opportunity", "ent-table", 310, 370),
                    _node("activity", "Activity", "ent-table", 510, 370),
                    _node("incident", "Case (Incident)", "ent-table", 710, 370),
                    _node("sysuser", "SystemUser", "ent-table", 400, 70),
                    # Account columns
                    _node("acc-pk", "accountid (PK)", "col-pk", 140, 150),
                    _node("acc-name", "name", "col-data", 140, 200),
                    _node("acc-fk", "parentaccountid (FK)", "col-fk", 140, 250),
                    # Contact columns
                    _node("con-pk", "contactid (PK)", "col-pk", 700, 150),
                    _node("con-fk", "accountid (FK)", "col-fk", 700, 200),
                    _node("con-pii", "emailaddress1 (PII)", "col-pii", 700, 250),
                    # Opportunity columns
                    _node("opp-fk", "accountid (FK)", "col-fk", 200, 370),
                    # controls
                    _node("rbac", "AAD/Entra RBAC", "ctrl-rbac", 360, 500),
                    _node("enc", "TDE + Azure KMS", "ctrl-encryption", 160, 500),
                    _node("mask", "PII Masking", "ctrl-masking", 60, 470),
                    _node("auditlog", "Dataverse Audit Log", "ctrl-audit-log", 570, 500),
                    _node("retention", "Data Retention Policy", "ctrl-retention", 360, 570),
                    _node("dlp", "Power Platform DLP", "ctrl-dlp", 670, 570),
                    _node("api", "Dataverse Web API", "flow-api", 400, 650),
                ],
                "edges": [
                    _edge("account", "acc-pk"),
                    _edge("account", "acc-name"),
                    _edge("account", "acc-fk"),
                    _edge("acc-fk", "acc-pk", "self-ref hierarchy"),
                    _edge("contact", "con-pk"),
                    _edge("contact", "con-fk"),
                    _edge("contact", "con-pii"),
                    _edge("con-fk", "acc-pk", "FK ref"),
                    _edge("opportunity", "opp-fk"),
                    _edge("opp-fk", "acc-pk", "FK ref"),
                    _edge("lead", "account", "qualify->account"),
                    _edge("lead", "contact", "qualify->contact"),
                    _edge("activity", "contact", "regarding", "flow-api"),
                    _edge("activity", "account", "regarding", "flow-api"),
                    _edge("incident", "contact", "contact_id FK"),
                    _edge("incident", "account", "customer_id FK"),
                    _edge("sysuser", "activity", "owner"),
                    _edge("sysuser", "incident", "owner"),
                    _edge("account", "rbac"),
                    _edge("contact", "rbac"),
                    _edge("opportunity", "rbac"),
                    _edge("account", "enc"),
                    _edge("contact", "enc"),
                    _edge("contact", "mask"),
                    _edge("account", "auditlog"),
                    _edge("contact", "auditlog"),
                    _edge("incident", "auditlog"),
                    _edge("account", "retention"),
                    _edge("contact", "dlp"),
                    _edge("api", "account", "CRUD", "flow-api"),
                    _edge("api", "contact", "CRUD", "flow-api"),
                    _edge("api", "incident", "CRUD", "flow-api"),
                ],
                "boundaries": [
                    _boundary(
                        "cdm-zone",
                        "Microsoft CDM / Dataverse",
                        "bnd-classification",
                        ["account", "contact", "lead", "opportunity", "activity", "incident", "sysuser"],
                        x=40,
                        y=40,
                        width=870,
                        height=400,
                    ),
                    _boundary(
                        "azure-tenant",
                        "Azure Commercial / Gov Tenant",
                        "bnd-region",
                        ["account", "contact", "lead", "opportunity", "activity", "incident"],
                        x=30,
                        y=30,
                        width=890,
                        height=420,
                    ),
                ],
            }
        ),
    },
    # ── Data Science Templates ──────────────────────────────────────────────────
    # 7 — ML Feature Store
    {
        "id": "tpl-ddc-ml-feature-store",
        "name": "ML Feature Store",
        "category": "Data Science",
        "description": "Feature engineering pipeline: raw event tables → computed feature store → model registry with quality gates, freshness guardian, and data lineage.",
        "tags": json.dumps(["ml", "feature-store", "data-science", "mlops", "lineage"]),
        "graph_json": json.dumps(
            {
                "nodes": [
                    _node("raw-events", "raw_events", "ent-table", 100, 150),
                    _node("feat-store", "feature_store", "ent-feature-store", 350, 150),
                    _node("model-reg", "model_registry", "ent-model-registry", 600, 150),
                    _node("feat-pk", "feature_id (PK)", "col-pk", 270, 80),
                    _node("feat-name", "feature_name", "col-data", 270, 150),
                    _node("feat-val", "feature_value", "col-data", 270, 220),
                    _node("feat-ts", "computed_at", "col-audit", 270, 290),
                    _node("model-pk", "model_id (PK)", "col-pk", 720, 80),
                    _node("model-ver", "version", "col-data", 720, 150),
                    _node("model-uri", "artifact_uri", "col-data", 720, 220),
                    _node("feat-eng", "Feature Engineering", "flow-etl", 225, 370),
                    _node("train-pipe", "Training Pipeline", "flow-etl", 475, 370),
                    _node("quality", "Quality Gate", "twin-quality-gate", 350, 450),
                    _node("freshness", "Freshness Guardian", "ctrl-retention", 100, 350),
                    _node("lineage", "Data Lineage", "twin-lineage", 600, 350),
                    _node("rbac", "RBAC Policy", "ctrl-rbac", 350, 50),
                ],
                "edges": [
                    _edge("feat-store", "feat-pk"),
                    _edge("feat-store", "feat-name"),
                    _edge("feat-store", "feat-val"),
                    _edge("feat-store", "feat-ts"),
                    _edge("model-reg", "model-pk"),
                    _edge("model-reg", "model-ver"),
                    _edge("model-reg", "model-uri"),
                    _edge("raw-events", "feat-store", "feature engineer", "flow-etl"),
                    _edge("feat-store", "model-reg", "train", "flow-etl"),
                    _edge("raw-events", "freshness"),
                    _edge("feat-store", "quality"),
                    _edge("feat-store", "lineage"),
                    _edge("model-reg", "lineage"),
                    _edge("rbac", "feat-store", "policy"),
                    _edge("rbac", "model-reg", "policy"),
                ],
                "boundaries": [
                    _boundary(
                        "ml-zone",
                        "ML Platform Zone",
                        "bnd-schema",
                        ["raw-events", "feat-store", "model-reg"],
                        x=50,
                        y=30,
                        width=730,
                        height=320,
                    ),
                    _boundary(
                        "cui-zone",
                        "CUI // SP-CTI Zone",
                        "bnd-classification",
                        ["raw-events"],
                        x=60,
                        y=100,
                        width=150,
                        height=150,
                    ),
                ],
            }
        ),
    },
    # 8 — ML Training Pipeline
    {
        "id": "tpl-ddc-ds-pipeline",
        "name": "ML Training Pipeline",
        "category": "Data Science",
        "description": "End-to-end supervised learning: ingest → profile → feature extraction → train/test split → model store → drift monitoring.",
        "tags": json.dumps(["ml", "pipeline", "training", "data-science", "mlops"]),
        "graph_json": json.dumps(
            {
                "nodes": [
                    _node("ingest-lake", "raw_data_lake", "ent-datalake", 80, 180),
                    _node("profile-tbl", "explore_profiles", "ent-table", 230, 180),
                    _node("feat-tbl", "feature_set", "ent-dataset", 380, 180),
                    _node("split-tbl", "train_test_split", "ent-dataset", 530, 180),
                    _node("model-store", "model_store", "ent-model-registry", 680, 180),
                    _node("drift-mon", "drift_monitor", "ent-topic", 680, 360),
                    _node("pii-col", "raw_pii (PII)", "col-pii", 80, 80),
                    _node("feat-col", "feature_vector", "col-data", 380, 80),
                    _node("label-col", "target_label", "col-data", 380, 130),
                    _node("quality-gate", "Quality Gate", "twin-quality-gate", 230, 370),
                    _node("schema-drift", "Schema Drift Detector", "twin-schema-drift", 530, 370),
                    _node("lineage", "Column Lineage", "twin-lineage", 380, 450),
                    _node("pii-scan", "PII Scanner", "ctrl-masking", 80, 370),
                    _node("rbac", "RBAC Policy", "ctrl-rbac", 480, 80),
                ],
                "edges": [
                    _edge("ingest-lake", "pii-col"),
                    _edge("feat-tbl", "feat-col"),
                    _edge("feat-tbl", "label-col"),
                    _edge("ingest-lake", "profile-tbl", "profile", "flow-etl"),
                    _edge("profile-tbl", "feat-tbl", "extract", "flow-etl"),
                    _edge("feat-tbl", "split-tbl", "split", "flow-etl"),
                    _edge("split-tbl", "model-store", "train", "flow-etl"),
                    _edge("model-store", "drift-mon", "monitor", "flow-api"),
                    _edge("ingest-lake", "pii-scan"),
                    _edge("profile-tbl", "quality-gate"),
                    _edge("split-tbl", "schema-drift"),
                    _edge("feat-tbl", "lineage"),
                    _edge("rbac", "feat-tbl", "policy"),
                    _edge("rbac", "model-store", "policy"),
                ],
                "boundaries": [
                    _boundary(
                        "pipeline-zone",
                        "ML Training Pipeline",
                        "bnd-schema",
                        ["ingest-lake", "profile-tbl", "feat-tbl", "split-tbl", "model-store"],
                        x=30,
                        y=140,
                        width=730,
                        height=150,
                    ),
                    _boundary(
                        "cui-zone",
                        "CUI // SP-CTI Zone",
                        "bnd-classification",
                        ["ingest-lake", "pii-col"],
                        x=40,
                        y=50,
                        width=140,
                        height=170,
                    ),
                ],
            }
        ),
    },
    # 9 — Jupyter Lakehouse (DuckDB + Parquet)
    {
        "id": "tpl-ddc-jupyter-lakehouse",
        "name": "Jupyter Lakehouse (DuckDB + Parquet)",
        "category": "Data Science",
        "description": "Interactive analytics lakehouse: Parquet data lake → DuckDB in-process engine → profiler → query sandbox → quality checks with CUI zone and audit trail.",
        "tags": json.dumps(["duckdb", "parquet", "lakehouse", "jupyter", "data-science", "analytics"]),
        "graph_json": json.dumps(
            {
                "nodes": [
                    _node("parquet-lake", "Parquet Lake", "ent-datalake", 100, 150),
                    _node("duckdb", "DuckDB (Compute)", "ent-warehouse", 350, 150),
                    _node("profile", "explore_profiles", "ent-table", 100, 350),
                    _node("query-hist", "query_history", "ent-table", 350, 350),
                    _node("quality-rules", "quality_rules", "ent-table", 600, 350),
                    _node("pii-col", "pii_field (PII)", "col-pii", 50, 250),
                    _node("enc", "AES-256 TDE", "ctrl-encryption", 600, 150),
                    _node("audit-log", "Audit Logging", "ctrl-audit-log", 100, 500),
                    _node("retention", "NARA Retention", "ctrl-retention", 350, 500),
                    _node("rbac", "RBAC Policy", "ctrl-rbac", 600, 50),
                    _node("quality-gate", "Quality Gate", "twin-quality-gate", 600, 250),
                    _node("schema-drift", "Schema Drift Detector", "twin-schema-drift", 350, 50),
                    _node("lineage", "Column Lineage", "flow-column-lineage", 350, 250),
                    _node("etl-ingest", "Ingest ETL", "flow-etl", 225, 250),
                ],
                "edges": [
                    _edge("parquet-lake", "pii-col"),
                    _edge("parquet-lake", "duckdb", "query", "flow-etl"),
                    _edge("duckdb", "profile", "profile run", "flow-api"),
                    _edge("duckdb", "query-hist", "query log", "flow-api"),
                    _edge("duckdb", "quality-rules", "quality check", "flow-api"),
                    _edge("duckdb", "enc"),
                    _edge("duckdb", "quality-gate"),
                    _edge("duckdb", "schema-drift"),
                    _edge("duckdb", "lineage"),
                    _edge("parquet-lake", "audit-log"),
                    _edge("duckdb", "audit-log"),
                    _edge("parquet-lake", "retention"),
                    _edge("duckdb", "retention"),
                    _edge("rbac", "duckdb", "policy"),
                ],
                "boundaries": [
                    _boundary(
                        "lakehouse-zone",
                        "Lakehouse Zone",
                        "bnd-schema",
                        ["parquet-lake", "duckdb", "profile", "query-hist", "quality-rules"],
                        x=40,
                        y=90,
                        width=650,
                        height=330,
                    ),
                    _boundary(
                        "cui-zone",
                        "CUI // SP-CTI Zone",
                        "bnd-classification",
                        ["parquet-lake"],
                        x=50,
                        y=110,
                        width=140,
                        height=160,
                    ),
                ],
            }
        ),
    },
    # ── Data Mesh Templates ─────────────────────────────────────────────────────
    # 10 — Data Mesh Domain
    {
        "id": "tpl-ddc-data-mesh-domain",
        "name": "Data Mesh Domain",
        "category": "Data Mesh",
        "description": "Data Mesh domain with two data products, ODCS contracts, federated governance policy, domain catalog, and SLA quality gates.",
        "tags": json.dumps(["data-mesh", "domain", "data-product", "governance", "odcs"]),
        "graph_json": json.dumps(
            {
                "nodes": [
                    _node("prod-1", "Telemetry Product", "ent-data-product", 150, 200),
                    _node("prod-2", "Analytics Product", "ent-data-product", 450, 200),
                    _node("contract-1", "ODCS Contract (Telemetry)", "ent-contract", 150, 380),
                    _node("contract-2", "ODCS Contract (Analytics)", "ent-contract", 450, 380),
                    _node("catalog", "Domain Catalog", "twin-catalog", 300, 480),
                    _node("policy", "Governance Policy", "ctrl-classification", 300, 80),
                    _node("rbac", "Domain RBAC", "ctrl-rbac", 100, 80),
                    _node("quality-1", "Quality Gate (Telemetry)", "twin-quality-gate", 150, 490),
                    _node("quality-2", "Quality Gate (Analytics)", "twin-quality-gate", 450, 490),
                    _node("lineage", "Lineage Twin", "twin-lineage", 300, 570),
                    _node("sla-col-1", "availability_sla", "col-data", 80, 280),
                    _node("sla-col-2", "latency_sla_ms", "col-data", 530, 280),
                    _node("input-port", "Input Port (CDC)", "ent-input-port", 0, 200),
                    _node("output-port-1", "Output Port (API)", "ent-output-port", 150, 100),
                    _node("output-port-2", "Output Port (Export)", "ent-output-port", 450, 100),
                ],
                "edges": [
                    _edge("prod-1", "contract-1"),
                    _edge("prod-2", "contract-2"),
                    _edge("prod-1", "sla-col-1"),
                    _edge("prod-2", "sla-col-2"),
                    _edge("contract-1", "catalog"),
                    _edge("contract-2", "catalog"),
                    _edge("catalog", "lineage"),
                    _edge("prod-1", "quality-1"),
                    _edge("prod-2", "quality-2"),
                    _edge("quality-1", "lineage"),
                    _edge("quality-2", "lineage"),
                    _edge("policy", "prod-1", "enforce"),
                    _edge("policy", "prod-2", "enforce"),
                    _edge("rbac", "prod-1", "policy"),
                    _edge("rbac", "prod-2", "policy"),
                    _edge("input-port", "prod-1", "ingest", "flow-cdc"),
                    _edge("prod-1", "output-port-1", "serve", "flow-api"),
                    _edge("prod-2", "output-port-2", "export", "flow-export"),
                ],
                "boundaries": [
                    _boundary(
                        "domain-zone",
                        "Domain: Telemetry & Analytics",
                        "bnd-tenant",
                        ["prod-1", "prod-2", "contract-1", "contract-2"],
                        x=80,
                        y=140,
                        width=490,
                        height=310,
                    ),
                ],
            }
        ),
    },
    # 11 — Data Product
    {
        "id": "tpl-ddc-data-product",
        "name": "Data Product",
        "category": "Data Mesh",
        "description": "Self-contained data product: input ports (CDC/API), storage, output ports (API/export/stream), ODCS contract, SLA quality gate, and catalog registration.",
        "tags": json.dumps(["data-mesh", "data-product", "sla", "odcs", "catalog"]),
        "graph_json": json.dumps(
            {
                "nodes": [
                    _node("input-cdc", "Input Port (CDC)", "ent-input-port", 50, 200),
                    _node("input-api", "Input Port (API)", "ent-input-port", 50, 300),
                    _node("storage", "Product Storage", "ent-data-product", 280, 230),
                    _node("output-api", "Output Port (API)", "ent-output-port", 510, 150),
                    _node("output-export", "Output Port (Export)", "ent-output-port", 510, 280),
                    _node("output-stream", "Output Port (Stream)", "ent-output-port", 510, 400),
                    _node("contract", "ODCS Contract", "ent-contract", 280, 430),
                    _node("quality", "SLA Quality Gate", "twin-quality-gate", 280, 80),
                    _node("catalog", "Catalog Entry", "twin-catalog", 510, 530),
                    _node("lineage", "Lineage Twin", "twin-lineage", 50, 430),
                    _node("pk-col", "product_id (PK)", "col-pk", 200, 180),
                    _node("sla-col", "availability_pct", "col-data", 360, 180),
                    _node("owner-col", "domain_owner", "col-data", 200, 280),
                    _node("rbac", "Product RBAC", "ctrl-rbac", 280, 560),
                    _node("retention", "Retention Policy", "ctrl-retention", 510, 620),
                ],
                "edges": [
                    _edge("input-cdc", "storage", "ingest", "flow-cdc"),
                    _edge("input-api", "storage", "ingest", "flow-api"),
                    _edge("storage", "output-api", "serve", "flow-api"),
                    _edge("storage", "output-export", "export", "flow-export"),
                    _edge("storage", "output-stream", "stream", "flow-etl"),
                    _edge("storage", "pk-col"),
                    _edge("storage", "sla-col"),
                    _edge("storage", "owner-col"),
                    _edge("storage", "contract"),
                    _edge("contract", "catalog"),
                    _edge("storage", "quality"),
                    _edge("storage", "lineage"),
                    _edge("contract", "lineage"),
                    _edge("rbac", "storage", "policy"),
                    _edge("retention", "storage"),
                ],
                "boundaries": [
                    _boundary(
                        "product-zone",
                        "Data Product Boundary",
                        "bnd-tenant",
                        ["storage", "contract", "quality", "catalog"],
                        x=160,
                        y=40,
                        width=430,
                        height=560,
                    ),
                ],
            }
        ),
    },
    # 12 — Federated Governance
    {
        "id": "tpl-ddc-federated-governance",
        "name": "Federated Governance",
        "category": "Data Mesh",
        "description": "Federated governance hub: OPA policy engine, global metadata catalog, cross-domain audit, domain maturity scoring, and OpenLineage emitter.",
        "tags": json.dumps(["data-mesh", "governance", "opa", "openlineage", "catalog", "federated"]),
        "graph_json": json.dumps(
            {
                "nodes": [
                    _node("opa-engine", "OPA Policy Engine", "ctrl-classification", 300, 100),
                    _node("global-cat", "Global Metadata Catalog", "twin-catalog", 300, 280),
                    _node("domain-a", "Domain A (Products)", "ent-domain", 80, 440),
                    _node("domain-b", "Domain B (Analytics)", "ent-domain", 300, 440),
                    _node("domain-c", "Domain C (ML)", "ent-domain", 520, 440),
                    _node("audit", "Cross-Domain Audit Log", "ctrl-audit-log", 300, 550),
                    _node("lineage", "OpenLineage Emitter", "twin-lineage", 550, 280),
                    _node("schema-drift", "Schema Drift Detector", "twin-schema-drift", 50, 280),
                    _node("quality-hub", "Quality Score Hub", "twin-quality-gate", 300, 190),
                    _node("dlp", "DLP Egress Filter", "ctrl-dlp", 550, 100),
                    _node("retention", "Global Retention Policy", "ctrl-retention", 50, 100),
                    _node("rbac", "Federation RBAC", "ctrl-rbac", 300, 640),
                ],
                "edges": [
                    _edge("opa-engine", "domain-a", "enforce policy"),
                    _edge("opa-engine", "domain-b", "enforce policy"),
                    _edge("opa-engine", "domain-c", "enforce policy"),
                    _edge("global-cat", "domain-a", "catalog sync"),
                    _edge("global-cat", "domain-b", "catalog sync"),
                    _edge("global-cat", "domain-c", "catalog sync"),
                    _edge("global-cat", "lineage"),
                    _edge("schema-drift", "domain-a"),
                    _edge("schema-drift", "domain-b"),
                    _edge("quality-hub", "domain-a"),
                    _edge("quality-hub", "domain-b"),
                    _edge("quality-hub", "domain-c"),
                    _edge("domain-a", "audit"),
                    _edge("domain-b", "audit"),
                    _edge("domain-c", "audit"),
                    _edge("dlp", "domain-c", "egress filter"),
                    _edge("retention", "global-cat"),
                    _edge("rbac", "opa-engine", "admin policy"),
                ],
                "boundaries": [
                    _boundary(
                        "federation-zone",
                        "Federation Layer",
                        "bnd-classification",
                        ["opa-engine", "global-cat", "quality-hub", "lineage"],
                        x=200,
                        y=60,
                        width=420,
                        height=270,
                    ),
                    _boundary(
                        "domain-ring",
                        "Domain Ring",
                        "bnd-tenant",
                        ["domain-a", "domain-b", "domain-c"],
                        x=20,
                        y=400,
                        width=570,
                        height=110,
                    ),
                ],
            }
        ),
    },
]


SNIPPETS = [
    # 1 — PII-Protected Table
    {
        "id": "snp-ddc-pii-protected",
        "name": "PII-Protected Table",
        "category": "Privacy",
        "description": "Table with PII columns, masking policy, RBAC, and audit logging.",
        "tags": json.dumps(["pii", "masking", "rbac", "privacy"]),
        "graph_json": json.dumps(
            {
                "nodes": [
                    _node("tbl-1", "users", "ent-table", 100, 50),
                    _node("col-email", "email (PII)", "col-pii", 50, 130),
                    _node("col-name", "full_name (PII)", "col-pii", 50, 190),
                    _node("mask-1", "PII Masking", "ctrl-masking", 250, 100),
                    _node("rbac-1", "RBAC Policy", "ctrl-rbac", 250, 170),
                    _node("audit-1", "Audit Log", "ctrl-audit-log", 100, 250),
                ],
                "edges": [
                    _edge("tbl-1", "col-email"),
                    _edge("tbl-1", "col-name"),
                    _edge("tbl-1", "mask-1"),
                    _edge("tbl-1", "rbac-1"),
                    _edge("tbl-1", "audit-1"),
                ],
                "boundaries": [],
            }
        ),
    },
    # 2 — CUI Data Flow
    {
        "id": "snp-ddc-cui-data-flow",
        "name": "CUI Data Flow",
        "category": "Classification",
        "description": "Entity in CUI zone with encryption and DLP on egress.",
        "tags": json.dumps(["cui", "encryption", "dlp", "classification"]),
        "graph_json": json.dumps(
            {
                "nodes": [
                    _node("tbl-cui", "CUI Data Store", "ent-table", 50, 50),
                    _node("col-cui", "mission_data (CUI)", "col-cui", 50, 130),
                    _node("enc-1", "AES-256 TDE", "ctrl-encryption", 250, 50),
                    _node("dlp-1", "DLP Egress Filter", "ctrl-dlp", 250, 130),
                    _node("export-1", "Data Export", "flow-export", 150, 200),
                ],
                "edges": [
                    _edge("tbl-cui", "col-cui"),
                    _edge("tbl-cui", "enc-1"),
                    _edge("tbl-cui", "export-1", "export", "flow-export"),
                    _edge("export-1", "dlp-1"),
                ],
                "boundaries": [
                    _boundary(
                        "cui-zone",
                        "CUI // SP-CTI Zone",
                        "bnd-classification",
                        ["tbl-cui"],
                        x=20,
                        y=20,
                        width=200,
                        height=170,
                    ),
                ],
            }
        ),
    },
    # 3 — CDC Replication
    {
        "id": "snp-ddc-cdc-replication",
        "name": "CDC Replication",
        "category": "Replication",
        "description": "Source table with CDC stream replicating to target table.",
        "tags": json.dumps(["cdc", "replication", "debezium", "streaming"]),
        "graph_json": json.dumps(
            {
                "nodes": [
                    _node("src-tbl", "Source Table", "ent-table", 50, 80),
                    _node("cdc-1", "Debezium CDC", "flow-cdc", 150, 80),
                    _node("tgt-tbl", "Target Table", "ent-table", 250, 80),
                ],
                "edges": [
                    _edge("src-tbl", "cdc-1", "CDC", "flow-cdc"),
                    _edge("cdc-1", "tgt-tbl", "replicate", "flow-cdc"),
                ],
                "boundaries": [],
            }
        ),
    },
    # 4 — Multi-Tenant Isolation
    {
        "id": "snp-ddc-multi-tenant",
        "name": "Multi-Tenant Isolation",
        "category": "Architecture",
        "description": "Two entities in separate tenant boundaries with RBAC.",
        "tags": json.dumps(["multi-tenant", "isolation", "rbac"]),
        "graph_json": json.dumps(
            {
                "nodes": [
                    _node("tbl-a", "Tenant A Data", "ent-table", 50, 80),
                    _node("tbl-b", "Tenant B Data", "ent-table", 250, 80),
                    _node("rbac-1", "Tenant RBAC", "ctrl-rbac", 150, 180),
                ],
                "edges": [
                    _edge("tbl-a", "rbac-1"),
                    _edge("tbl-b", "rbac-1"),
                ],
                "boundaries": [
                    _boundary("tenant-a", "Tenant A", "bnd-tenant", ["tbl-a"], x=20, y=40, width=170, height=120),
                    _boundary("tenant-b", "Tenant B", "bnd-tenant", ["tbl-b"], x=220, y=40, width=170, height=120),
                ],
            }
        ),
    },
    # 5 — HIPAA PHI Store
    {
        "id": "snp-ddc-hipaa-phi",
        "name": "HIPAA PHI Store",
        "category": "Healthcare",
        "description": "PHI-tagged columns with encryption, audit log, and HIPAA zone.",
        "tags": json.dumps(["hipaa", "phi", "encryption", "healthcare"]),
        "graph_json": json.dumps(
            {
                "nodes": [
                    _node("patients", "patients", "ent-table", 100, 50),
                    _node("col-dx", "diagnosis (PHI)", "col-phi", 50, 130),
                    _node("col-meds", "medications (PHI)", "col-phi", 50, 200),
                    _node("enc-1", "AES-256 TDE", "ctrl-encryption", 250, 80),
                    _node("audit-1", "HIPAA Audit Log", "ctrl-audit-log", 250, 160),
                ],
                "edges": [
                    _edge("patients", "col-dx"),
                    _edge("patients", "col-meds"),
                    _edge("patients", "enc-1"),
                    _edge("patients", "audit-1"),
                ],
                "boundaries": [
                    _boundary(
                        "hipaa-zone",
                        "HIPAA PHI Zone",
                        "bnd-classification",
                        ["patients"],
                        x=60,
                        y=20,
                        width=220,
                        height=200,
                    ),
                ],
            }
        ),
    },
    # 6 — ETL Pipeline
    {
        "id": "snp-ddc-etl-pipeline",
        "name": "ETL Pipeline",
        "category": "Analytics",
        "description": "Data lake to warehouse via ETL with classification tagging.",
        "tags": json.dumps(["etl", "datalake", "warehouse", "analytics"]),
        "graph_json": json.dumps(
            {
                "nodes": [
                    _node("lake", "Raw Data Lake", "ent-datalake", 50, 80),
                    _node("etl-1", "ETL Pipeline", "flow-etl", 150, 80),
                    _node("wh", "Analytics Warehouse", "ent-warehouse", 250, 80),
                    _node("class-1", "Data Classification", "ctrl-classification", 150, 180),
                ],
                "edges": [
                    _edge("lake", "etl-1", "extract", "flow-etl"),
                    _edge("etl-1", "wh", "load", "flow-etl"),
                    _edge("lake", "class-1"),
                    _edge("wh", "class-1"),
                ],
                "boundaries": [],
            }
        ),
    },
    # 7 — Cross-Domain Guard
    {
        "id": "snp-ddc-cross-domain",
        "name": "Cross-Domain Guard",
        "category": "DoD/IC",
        "description": "CUI entity and SECRET entity with cross-domain data flow.",
        "tags": json.dumps(["cross-domain", "cds", "cui", "secret", "dod"]),
        "graph_json": json.dumps(
            {
                "nodes": [
                    _node("cui-db", "CUI Operations DB", "ent-table", 50, 80),
                    _node("secret-db", "SECRET Intel DB", "ent-table", 250, 80),
                    _node("cds", "Cross-Domain Guard", "flow-cross-domain", 150, 80),
                    _node("cui-enc", "FIPS Encryption", "ctrl-encryption", 50, 180),
                    _node("sec-enc", "NSA Type 1", "ctrl-encryption", 250, 180),
                ],
                "edges": [
                    _edge("cui-db", "cds", "CUI->SECRET", "flow-cross-domain"),
                    _edge("cds", "secret-db", "filtered", "flow-cross-domain"),
                    _edge("cui-db", "cui-enc"),
                    _edge("secret-db", "sec-enc"),
                ],
                "boundaries": [
                    _boundary(
                        "cui-zone", "CUI Zone", "bnd-classification", ["cui-db"], x=20, y=40, width=170, height=120
                    ),
                    _boundary(
                        "secret-zone",
                        "SECRET Zone",
                        "bnd-classification",
                        ["secret-db"],
                        x=220,
                        y=40,
                        width=170,
                        height=120,
                    ),
                ],
            }
        ),
    },
    # 8 — Backup + Retention
    {
        "id": "snp-ddc-backup-retention",
        "name": "Backup + Retention",
        "category": "Operations",
        "description": "Entity with backup policy and retention policy.",
        "tags": json.dumps(["backup", "retention", "disaster-recovery", "operations"]),
        "graph_json": json.dumps(
            {
                "nodes": [
                    _node("tbl-1", "Production DB", "ent-table", 100, 50),
                    _node("backup-1", "Daily Backup (RPO 1h)", "ctrl-backup-policy", 50, 160),
                    _node("retention-1", "7-Year Retention", "ctrl-retention", 250, 160),
                    _node("enc-1", "Encryption at Rest", "ctrl-encryption", 250, 50),
                ],
                "edges": [
                    _edge("tbl-1", "backup-1"),
                    _edge("tbl-1", "retention-1"),
                    _edge("tbl-1", "enc-1"),
                ],
                "boundaries": [],
            }
        ),
    },
    # ── Data Science Snippets ───────────────────────────────────────────────────
    # 9 — Feature Table
    {
        "id": "snp-ddc-feature-table",
        "name": "Feature Table",
        "category": "Data Science",
        "description": "ML feature table with freshness guardian, quality gate, and data lineage.",
        "tags": json.dumps(["feature-store", "ml", "freshness", "data-science"]),
        "graph_json": json.dumps(
            {
                "nodes": [
                    _node("feat-tbl", "feature_store", "ent-feature-store", 100, 100),
                    _node("feat-pk", "feature_id (PK)", "col-pk", 30, 60),
                    _node("feat-val", "feature_value", "col-data", 30, 120),
                    _node("feat-ts", "computed_at", "col-audit", 30, 180),
                    _node("freshness", "Freshness Guardian", "ctrl-retention", 250, 70),
                    _node("quality", "Quality Gate", "twin-quality-gate", 250, 160),
                    _node("lineage", "Lineage Twin", "twin-lineage", 100, 250),
                ],
                "edges": [
                    _edge("feat-tbl", "feat-pk"),
                    _edge("feat-tbl", "feat-val"),
                    _edge("feat-tbl", "feat-ts"),
                    _edge("feat-tbl", "freshness"),
                    _edge("feat-tbl", "quality"),
                    _edge("feat-tbl", "lineage"),
                ],
                "boundaries": [],
            }
        ),
    },
    # 10 — Training Dataset
    {
        "id": "snp-ddc-training-dataset",
        "name": "Training Dataset",
        "category": "Data Science",
        "description": "Labeled training dataset with PII scan, column lineage, quality gate, and RBAC.",
        "tags": json.dumps(["training", "ml", "dataset", "pii", "data-science"]),
        "graph_json": json.dumps(
            {
                "nodes": [
                    _node("ds-tbl", "training_dataset", "ent-dataset", 100, 80),
                    _node("label-col", "target_label", "col-data", 30, 60),
                    _node("feat-col", "feature_vector", "col-data", 30, 120),
                    _node("pii-col", "raw_pii (PII)", "col-pii", 30, 180),
                    _node("pii-scan", "PII Scanner", "ctrl-masking", 250, 60),
                    _node("quality", "Quality Gate", "twin-quality-gate", 250, 150),
                    _node("lineage", "Column Lineage", "flow-column-lineage", 100, 220),
                    _node("rbac", "RBAC Policy", "ctrl-rbac", 250, 230),
                ],
                "edges": [
                    _edge("ds-tbl", "label-col"),
                    _edge("ds-tbl", "feat-col"),
                    _edge("ds-tbl", "pii-col"),
                    _edge("ds-tbl", "pii-scan"),
                    _edge("ds-tbl", "quality"),
                    _edge("ds-tbl", "lineage"),
                    _edge("rbac", "ds-tbl", "policy"),
                ],
                "boundaries": [],
            }
        ),
    },
    # 11 — Model Registry
    {
        "id": "snp-ddc-model-registry",
        "name": "Model Registry",
        "category": "Data Science",
        "description": "Model version store with artifact URI tracking, audit log, RBAC, and schema drift detection.",
        "tags": json.dumps(["model-registry", "mlops", "versioning", "data-science"]),
        "graph_json": json.dumps(
            {
                "nodes": [
                    _node("model-tbl", "model_registry", "ent-model-registry", 100, 80),
                    _node("model-pk", "model_id (PK)", "col-pk", 30, 60),
                    _node("model-ver", "version", "col-data", 30, 120),
                    _node("model-uri", "artifact_uri", "col-data", 30, 180),
                    _node("model-ts", "trained_at", "col-audit", 30, 240),
                    _node("drift", "Schema Drift Detector", "twin-schema-drift", 260, 80),
                    _node("audit", "Audit Log", "ctrl-audit-log", 260, 180),
                    _node("rbac", "RBAC Policy", "ctrl-rbac", 100, 300),
                ],
                "edges": [
                    _edge("model-tbl", "model-pk"),
                    _edge("model-tbl", "model-ver"),
                    _edge("model-tbl", "model-uri"),
                    _edge("model-tbl", "model-ts"),
                    _edge("model-tbl", "drift"),
                    _edge("model-tbl", "audit"),
                    _edge("rbac", "model-tbl", "policy"),
                ],
                "boundaries": [],
            }
        ),
    },
    # 12 — Anomaly Detection Loop
    {
        "id": "snp-ddc-anomaly-detect",
        "name": "Anomaly Detection Loop",
        "category": "Data Science",
        "description": "CUSUM anomaly detection: source table → profiler → detector → alert stream.",
        "tags": json.dumps(["anomaly-detection", "cusum", "monitoring", "data-science"]),
        "graph_json": json.dumps(
            {
                "nodes": [
                    _node("src-tbl", "source_table", "ent-table", 50, 100),
                    _node("profile", "explore_profile", "ent-table", 200, 100),
                    _node("anomaly", "CUSUM Anomaly Detector", "twin-schema-drift", 350, 100),
                    _node("alert-stream", "alert_stream", "ent-topic", 500, 100),
                    _node("freshness", "Freshness Guardian", "ctrl-retention", 50, 230),
                    _node("audit", "Audit Log", "ctrl-audit-log", 350, 230),
                ],
                "edges": [
                    _edge("src-tbl", "profile", "profile run", "flow-etl"),
                    _edge("profile", "anomaly", "detect"),
                    _edge("anomaly", "alert-stream", "alert", "flow-api"),
                    _edge("src-tbl", "freshness"),
                    _edge("anomaly", "audit"),
                ],
                "boundaries": [],
            }
        ),
    },
    # ── Data Mesh Snippets ──────────────────────────────────────────────────────
    # 13 — Data Product Ports
    {
        "id": "snp-ddc-data-product-port",
        "name": "Data Product Ports",
        "category": "Data Mesh",
        "description": "Input/output port wiring for a data mesh product: CDC input, API output, export output, and SLA contract.",
        "tags": json.dumps(["data-mesh", "data-product", "ports", "sla", "cdc"]),
        "graph_json": json.dumps(
            {
                "nodes": [
                    _node("input-cdc", "Input Port (CDC)", "ent-input-port", 30, 130),
                    _node("product", "Data Product", "ent-data-product", 200, 130),
                    _node("output-api", "Output Port (API)", "ent-output-port", 370, 80),
                    _node("output-exp", "Output Port (Export)", "ent-output-port", 370, 180),
                    _node("contract", "SLA Contract", "ent-contract", 200, 280),
                    _node("quality", "SLA Quality Gate", "twin-quality-gate", 370, 280),
                ],
                "edges": [
                    _edge("input-cdc", "product", "ingest", "flow-cdc"),
                    _edge("product", "output-api", "serve", "flow-api"),
                    _edge("product", "output-exp", "export", "flow-export"),
                    _edge("product", "contract"),
                    _edge("contract", "quality"),
                ],
                "boundaries": [
                    _boundary(
                        "product-zone",
                        "Data Product",
                        "bnd-tenant",
                        ["product", "contract"],
                        x=140,
                        y=80,
                        width=200,
                        height=260,
                    ),
                ],
            }
        ),
    },
    # 14 — Domain Data Contract
    {
        "id": "snp-ddc-domain-contract",
        "name": "Domain Data Contract",
        "category": "Data Mesh",
        "description": "ODCS data contract with governance policy, domain boundary, and catalog registration.",
        "tags": json.dumps(["data-mesh", "odcs", "contract", "governance", "domain"]),
        "graph_json": json.dumps(
            {
                "nodes": [
                    _node("product", "Data Product", "ent-data-product", 150, 100),
                    _node("contract", "ODCS Contract", "ent-contract", 150, 250),
                    _node("policy", "Governance Policy", "ctrl-classification", 320, 100),
                    _node("catalog", "Catalog Entry", "twin-catalog", 320, 250),
                    _node("lineage", "Lineage Twin", "twin-lineage", 230, 370),
                ],
                "edges": [
                    _edge("product", "contract"),
                    _edge("policy", "product", "enforce"),
                    _edge("policy", "contract", "enforce"),
                    _edge("contract", "catalog"),
                    _edge("catalog", "lineage"),
                ],
                "boundaries": [
                    _boundary(
                        "domain-zone",
                        "Domain Boundary",
                        "bnd-tenant",
                        ["product", "contract"],
                        x=80,
                        y=60,
                        width=200,
                        height=260,
                    ),
                ],
            }
        ),
    },
    # 15 — Mesh Governance Policy
    {
        "id": "snp-ddc-mesh-governance",
        "name": "Mesh Governance Policy",
        "category": "Data Mesh",
        "description": "Federated governance: OPA policy engine, cross-domain audit, DLP, and global catalog sync.",
        "tags": json.dumps(["data-mesh", "governance", "opa", "federated", "audit"]),
        "graph_json": json.dumps(
            {
                "nodes": [
                    _node("opa", "OPA Policy Engine", "ctrl-classification", 200, 80),
                    _node("catalog", "Global Catalog", "twin-catalog", 200, 230),
                    _node("audit", "Cross-Domain Audit", "ctrl-audit-log", 200, 370),
                    _node("dlp", "DLP Egress Filter", "ctrl-dlp", 380, 150),
                    _node("lineage", "OpenLineage Emitter", "twin-lineage", 380, 300),
                    _node("rbac", "Federation RBAC", "ctrl-rbac", 30, 150),
                ],
                "edges": [
                    _edge("opa", "catalog", "policy sync"),
                    _edge("opa", "dlp", "enforce"),
                    _edge("catalog", "audit"),
                    _edge("catalog", "lineage"),
                    _edge("dlp", "audit"),
                    _edge("rbac", "opa", "admin"),
                ],
                "boundaries": [],
            }
        ),
    },

    # Data Mesh Snippet 1: Multi-Domain Hub
    {
        "id": "snp-dm-multi-domain-hub",
        "name": "Multi-Domain Hub",
        "category": "Data Mesh",
        "description": "Central analytics domain consuming products from two upstream domains via typed output ports and ODCS contracts.",
        "tags": json.dumps(["data-mesh", "multi-domain", "hub", "contracts", "ports"]),
        "graph_json": json.dumps({
            "nodes": [
                _node("dom-a", "Sales Domain", "ent-domain", 100, 100),
                _node("prod-a", "Sales Data Product", "ent-data-product", 100, 220),
                _node("port-a-out", "daily_orders output", "ent-output-port", 100, 320),
                _node("dom-b", "Finance Domain", "ent-domain", 400, 100),
                _node("prod-b", "Revenue Data Product", "ent-data-product", 400, 220),
                _node("port-b-out", "revenue_feed output", "ent-output-port", 400, 320),
                _node("dom-hub", "Analytics Domain Hub", "ent-domain", 250, 500),
                _node("prod-hub", "Unified Analytics Product", "ent-data-product", 250, 620),
                _node("port-hub-in-a", "orders_input", "ent-input-port", 160, 720),
                _node("port-hub-in-b", "revenue_input", "ent-input-port", 340, 720),
                _node("contract-a", "Sales-Analytics Contract v1", "ent-contract", 160, 420),
                _node("contract-b", "Finance-Analytics Contract v1", "ent-contract", 340, 420),
                _node("policy-hub", "Cross-Domain Access Policy", "ctrl-rbac", 250, 820),
            ],
            "edges": [
                _edge("dom-a", "prod-a"),
                _edge("prod-a", "port-a-out"),
                _edge("dom-b", "prod-b"),
                _edge("prod-b", "port-b-out"),
                _edge("port-a-out", "contract-a"),
                _edge("port-b-out", "contract-b"),
                _edge("contract-a", "port-hub-in-a"),
                _edge("contract-b", "port-hub-in-b"),
                _edge("port-hub-in-a", "prod-hub"),
                _edge("port-hub-in-b", "prod-hub"),
                _edge("dom-hub", "prod-hub"),
                _edge("prod-hub", "policy-hub"),
            ],
            "boundaries": [],
        }),
    },
    # Data Mesh Snippet 2: Cross-Domain Contract SLA
    {
        "id": "snp-dm-cross-domain-sla",
        "name": "Cross-Domain Contract SLA",
        "category": "Data Mesh",
        "description": "Two domains sharing a data product via a gold-tier SLA contract with retention and quality policies.",
        "tags": json.dumps(["data-mesh", "sla", "contract", "gold", "cross-domain"]),
        "graph_json": json.dumps({
            "nodes": [
                _node("provider-dom", "Provider Domain", "ent-domain", 120, 80),
                _node("provider-prod", "Customer 360 Product", "ent-data-product", 120, 200),
                _node("c360-out", "c360_profiles REST output", "ent-output-port", 120, 320),
                _node("sla-contract", "C360 SLA Contract v2 Gold", "ent-contract", 280, 420),
                _node("consumer-dom", "Consumer Domain", "ent-domain", 440, 80),
                _node("consumer-prod", "Personalisation Engine", "ent-data-product", 440, 200),
                _node("c360-in", "c360_input REST input", "ent-input-port", 440, 320),
                _node("quality-pol", "Data Quality Policy", "ctrl-validation", 120, 500),
                _node("retention-pol", "7-Year Retention Policy", "ctrl-retention", 440, 500),
                _node("enc-pol", "Encryption in Transit", "ctrl-encryption", 280, 580),
            ],
            "edges": [
                _edge("provider-dom", "provider-prod"),
                _edge("provider-prod", "c360-out"),
                _edge("c360-out", "sla-contract"),
                _edge("sla-contract", "c360-in"),
                _edge("consumer-dom", "consumer-prod"),
                _edge("c360-in", "consumer-prod"),
                _edge("provider-prod", "quality-pol"),
                _edge("consumer-prod", "retention-pol"),
                _edge("sla-contract", "enc-pol"),
            ],
            "boundaries": [],
        }),
    },
    # Data Mesh Snippet 3: Federated Data Product
    {
        "id": "snp-dm-federated-data-product",
        "name": "Federated Data Product",
        "category": "Data Mesh",
        "description": "Single data product with full port matrix REST plus Kafka plus batch, OPA governance, and CUI classification.",
        "tags": json.dumps(["data-mesh", "federated", "opa", "kafka", "rest", "cui"]),
        "graph_json": json.dumps({
            "nodes": [
                _node("fed-dom", "Federated Domain", "ent-domain", 300, 60),
                _node("fed-prod", "Telemetry Data Product", "ent-data-product", 300, 180),
                _node("in-batch", "s3_raw_events batch input", "ent-input-port", 100, 300),
                _node("in-stream", "kafka_telemetry stream input", "ent-input-port", 300, 300),
                _node("out-rest", "metrics_api REST output", "ent-output-port", 500, 300),
                _node("out-kafka", "telemetry_enriched Kafka output", "ent-output-port", 300, 420),
                _node("contract-rest", "Metrics API Contract v3", "ent-contract", 500, 420),
                _node("opa-pol", "OPA Governance Policy", "ctrl-rbac", 100, 420),
                _node("cui-ctrl", "CUI Boundary Control", "ctrl-encryption", 500, 520),
                _node("audit-ctrl", "Federated Audit Log", "ctrl-audit-log", 100, 520),
                _node("datalake", "Telemetry Lake Zone", "ent-datalake", 300, 540),
            ],
            "edges": [
                _edge("fed-dom", "fed-prod"),
                _edge("in-batch", "fed-prod"),
                _edge("in-stream", "fed-prod"),
                _edge("fed-prod", "out-rest"),
                _edge("fed-prod", "out-kafka"),
                _edge("out-rest", "contract-rest"),
                _edge("fed-prod", "datalake"),
                _edge("fed-prod", "opa-pol"),
                _edge("fed-prod", "audit-ctrl"),
                _edge("out-rest", "cui-ctrl"),
            ],
            "boundaries": [],
        }),
    },
]


RUNBOOKS = [
    {
        "id": "rb-ddc-pii-exposure",
        "title": "PII Exposure Response",
        "category": "pii",
        "severity": "critical",
        "description": "Incident response runbook for detecting, containing, and remediating unauthorized PII exposure in a data design.",
        "trigger_condition": "PII scan finds unmasked PII columns outside approved classification zones, or a data breach notification is received.",
        "steps_json": json.dumps([
            {"order": 1, "title": "Detect & Scope", "description": "Run PII scan on all active data designs. Identify exposed columns (email, SSN, clearance level, DOB). Note design IDs, node IDs, and estimated record count.", "owner": "Data Steward", "sla_minutes": 30},
            {"order": 2, "title": "Immediate Containment", "description": "Revoke read permissions on affected tables/views. Disable API endpoints serving the PII fields. Apply emergency RBAC policy restricting access to data owners only.", "owner": "Security Engineer", "sla_minutes": 60},
            {"order": 3, "title": "Impact Assessment", "description": "Determine number of records exposed, time window, and downstream consumers. Classify severity: <100 records = Low, 100–10k = Medium, >10k = High/Critical.", "owner": "Data Steward", "sla_minutes": 120},
            {"order": 4, "title": "Notify Stakeholders", "description": "If >500 records or classified CUI: notify ISSO/ISSM within 1 hour. If PII of federal employees: notify Agency Privacy Officer. Document in POA&M.", "owner": "ISSO", "sla_minutes": 60},
            {"order": 5, "title": "Remediate Design", "description": "Update data design: add PII Masking control node, connect to all PII columns. Set classification to CUI on all PII fields. Re-run assessment and verify score >= 80.", "owner": "Developer", "sla_minutes": 240},
            {"order": 6, "title": "Verify & Close", "description": "Re-run PII scan — confirm 0 unmasked PII columns outside CUI zone. Re-enable access. Document root cause and lessons learned in audit log.", "owner": "Data Steward", "sla_minutes": 60},
        ]),
        "classification": "CUI // SP-CTI",
        "status": "active",
    },
    {
        "id": "rb-ddc-lineage-break",
        "title": "Data Lineage Break",
        "category": "lineage",
        "severity": "high",
        "description": "Runbook for investigating and restoring a broken data lineage chain in a data design (missing source-to-target edge, orphaned transformation node).",
        "trigger_condition": "Automated lineage check fails, or a downstream consumer reports unexpected NULL values / missing records tracing back to a broken lineage edge.",
        "steps_json": json.dumps([
            {"order": 1, "title": "Identify Broken Edge", "description": "Open the affected data design in DDC. Review the lineage graph for orphaned nodes (no source or no target). Use /api/designs/{id}/lineage to list all edges and find gaps.", "owner": "Data Engineer", "sla_minutes": 30},
            {"order": 2, "title": "Trace Upstream Impact", "description": "Walk upstream from the break point: which source tables feed into the broken node? Verify those sources are still active and accessible.", "owner": "Data Engineer", "sla_minutes": 60},
            {"order": 3, "title": "Trace Downstream Impact", "description": "Walk downstream from the break: which reports, pipelines, or APIs depend on data past the broken node? Flag affected consumers for temporary fallback.", "owner": "Data Engineer", "sla_minutes": 60},
            {"order": 4, "title": "Restore Lineage", "description": "Add the missing lineage edge via POST /api/designs/{id}/lineage with correct source_node_id, target_node_id, and lineage_type. If transformation logic changed, update transform_desc.", "owner": "Developer", "sla_minutes": 120},
            {"order": 5, "title": "Validate End-to-End", "description": "Run an end-to-end data flow test from the upstream source through to the final consumer. Confirm record counts match expectations within tolerance.", "owner": "QA Engineer", "sla_minutes": 90},
            {"order": 6, "title": "Document & Prevent", "description": "Add a version snapshot (POST /api/versions/{id}) with change summary noting the break and fix. Add a lineage validation gate to CI/CD pipeline to detect future breaks before deployment.", "owner": "Data Engineer", "sla_minutes": 60},
        ]),
        "classification": "CUI // SP-CTI",
        "status": "active",
    },
    {
        "id": "rb-ddc-classification-mismatch",
        "title": "Classification Mismatch",
        "category": "classification",
        "severity": "high",
        "description": "Runbook for resolving a data classification mismatch — when a node's classification level is inconsistent with its classification zone boundary or connected nodes.",
        "trigger_condition": "DDC assessment detects a CUI node outside a CUI boundary, a SECRET node in an unclassified zone, or classification coverage score drops below 70%.",
        "steps_json": json.dumps([
            {"order": 1, "title": "Run Classification Assessment", "description": "POST /api/designs/{id}/assess to get classification_coverage report. Note mismatched nodes: their IDs, current classification, and expected classification based on zone.", "owner": "Data Steward", "sla_minutes": 15},
            {"order": 2, "title": "Categorize Mismatches", "description": "Group mismatches by type: (a) node classified too low (e.g., CUI data in UNCLASSIFIED zone), (b) node classified too high (over-classification), (c) missing classification. Prioritize type (a).", "owner": "ISSO", "sla_minutes": 30},
            {"order": 3, "title": "Correct Node Classifications", "description": "For each mismatch: update node classification attribute in the graph to match the enclosing boundary. Move nodes into the correct classification boundary if needed.", "owner": "Developer", "sla_minutes": 120},
            {"order": 4, "title": "Update Boundary Definitions", "description": "If a boundary needs to expand/contract to cover correct nodes, update the boundary's contained_nodes list. Ensure no unclassified nodes exist inside CUI or SECRET boundaries.", "owner": "Data Steward", "sla_minutes": 60},
            {"order": 5, "title": "Re-assess & Verify", "description": "Re-run assessment. Confirm classification_coverage >= 0.9 (90% of nodes classified). Verify posture_grade is B or better. Fix any remaining findings.", "owner": "ISSO", "sla_minutes": 60},
            {"order": 6, "title": "Record in POA&M", "description": "If root cause was a process gap (e.g., developer not trained on classification requirements), open a POA&M item. Document corrective action and expected closure date.", "owner": "ISSO", "sla_minutes": 30},
        ]),
        "classification": "CUI // SP-CTI",
        "status": "active",
    },
    {
        "id": "rb-ddc-retention-violation",
        "title": "Retention Policy Violation",
        "category": "retention",
        "severity": "medium",
        "description": "Runbook for identifying and remediating data retention policy violations — data held beyond policy limits, or no retention control defined on a design.",
        "trigger_condition": "Compliance assessment finds data_designs with no retention control node, or an automated retention scanner reports records older than the defined policy window.",
        "steps_json": json.dumps([
            {"order": 1, "title": "Identify Missing Controls", "description": "Run POST /api/designs/{id}/assess. Check findings for 'No retention policy found' or 'Retention control disconnected'. List all data_designs missing a ctrl-retention node.", "owner": "Data Steward", "sla_minutes": 30},
            {"order": 2, "title": "Determine Policy Requirements", "description": "For each design, identify the governing retention policy: NIST 800-53 AU-11 (3 years for audit), FISMA (varies by data type), DoD 5015.02 (DoD records). Document required retention window.", "owner": "Compliance Officer", "sla_minutes": 60},
            {"order": 3, "title": "Add Retention Control Nodes", "description": "In DDC, add a ctrl-retention node with the correct label (e.g., '3-Year Retention per AU-11'). Connect it to all entity nodes in the design (tables, lakes, collections).", "owner": "Developer", "sla_minutes": 90},
            {"order": 4, "title": "Purge Overdue Records", "description": "For records already past retention window: generate list of tables and row counts. Submit purge job through approved data destruction workflow. Obtain signed Certificate of Destruction.", "owner": "Data Steward", "sla_minutes": 480},
            {"order": 5, "title": "Configure Automated Enforcement", "description": "Enable automated retention enforcement in the data pipeline: set TTL policies, S3 lifecycle rules, or DB partition pruning jobs. Test with a non-production data set.", "owner": "Data Engineer", "sla_minutes": 240},
            {"order": 6, "title": "Verify & Document", "description": "Re-run assessment to confirm retention findings resolved. Score >= 75. Update the data design with retention details. File Certificate of Destruction in the ATO evidence package.", "owner": "ISSO", "sla_minutes": 60},
        ]),
        "classification": "CUI // SP-CTI",
        "status": "active",
    },
]


SOPS = [
    {
        "id": "sop-ddc-data-classification-review",
        "title": "Data Classification Review",
        "category": "data_classification",
        "description": "Procedure for reviewing and labeling all data assets with the correct NIST SP 800-60 impact level and CUI/SECRET markings before ATO submission.",
        "purpose": "Ensure every dataset and table in a data design is assigned an accurate classification level (UNCLASSIFIED, CUI, or SECRET) so that boundary controls, encryption, and access policies can be correctly applied.",
        "scope": "All data designs in the DDC that are scheduled for ATO review or have been flagged with classification coverage below 90% by the automated assessment engine.",
        "steps_json": json.dumps([
            {"order": 1, "title": "Run DDC Classification Assessment", "description": "POST /data/api/designs/{id}/assess and collect the classification_coverage score. Flag any design with score < 0.90 for immediate remediation.", "owner": "Data Steward", "sla_minutes": 15},
            {"order": 2, "title": "Inventory Data Assets", "description": "Export the full node list from the design. For each entity node, record: (a) data type, (b) presence of PII/PHI/CUI fields, (c) current classification label.", "owner": "Data Steward", "sla_minutes": 30},
            {"order": 3, "title": "Apply NIST SP 800-60 Mapping", "description": "Cross-reference each data type against the NIST SP 800-60 Vol. II information type taxonomy. Assign Confidentiality, Integrity, and Availability impact levels (Low/Moderate/High).", "owner": "ISSO", "sla_minutes": 60},
            {"order": 4, "title": "Update Classification Labels in DDC", "description": "Edit each node's classification attribute in the canvas. Move nodes into the correct classification boundary (UNCLASSIFIED, CUI, SECRET). Ensure no CUI node sits outside a CUI boundary.", "owner": "Developer", "sla_minutes": 90},
            {"order": 5, "title": "Verify and Re-assess", "description": "Re-run POST /data/api/designs/{id}/assess. Confirm classification_coverage >= 0.90 and posture_grade is B or better. Resolve any remaining findings.", "owner": "ISSO", "sla_minutes": 30},
            {"order": 6, "title": "Update ATO Evidence Package", "description": "Export the updated design to JSON and attach to the System Security Plan (SSP) Section 13 — Information System Component Inventory. Record review date and reviewer name.", "owner": "ISSO", "sla_minutes": 30},
        ]),
        "version": "1.1",
        "status": "approved",
        "classification": "CUI // SP-CTI",
        "owner": "Data Steward",
        "reviewer": "ISSO",
        "approver": "AO Representative",
    },
    {
        "id": "sop-ddc-retention-policy-enforcement",
        "title": "Retention Policy Enforcement",
        "category": "retention",
        "description": "Procedure for enforcing DoD/FISMA records retention schedules — identifying overdue data, executing approved purge jobs, and obtaining Certificates of Destruction.",
        "purpose": "Comply with NIST 800-53 SI-12 (Information Management and Retention) and DoD 5015.02 records schedules by ensuring data is not held beyond its mandated retention window and that destruction is certified.",
        "scope": "All entity nodes in data designs that store persistent records: relational tables, object storage buckets, data lakes, and NoSQL collections. Applies to IL2 through IL5 environments.",
        "steps_json": json.dumps([
            {"order": 1, "title": "Identify Designs Without Retention Controls", "description": "Run POST /data/api/designs/{id}/assess for each active design. Flag all designs with 'No retention policy found' in findings or missing a ctrl-retention node.", "owner": "Data Steward", "sla_minutes": 30},
            {"order": 2, "title": "Determine Governing Retention Schedule", "description": "For each flagged design, identify the governing policy: NIST AU-11 (3 years for audit logs), FISMA (varies by data type), DoD 5015.02 (DoD official records). Document the required retention window in days.", "owner": "Compliance Officer", "sla_minutes": 60},
            {"order": 3, "title": "Add Retention Control Nodes in DDC", "description": "In the data canvas, add a ctrl-retention node labelled with the policy (e.g., '3-Year Retention per AU-11'). Connect it to all entity nodes in the design. Commit the updated design.", "owner": "Developer", "sla_minutes": 60},
            {"order": 4, "title": "Configure Automated Enforcement", "description": "Enable automated enforcement in the data pipeline: set S3 lifecycle rules, PostgreSQL partition pruning jobs, or TTL indexes on time-series collections. Validate in non-production first.", "owner": "Data Engineer", "sla_minutes": 240},
            {"order": 5, "title": "Execute Approved Purge Jobs", "description": "For records already past their retention window, generate a purge manifest (table, row count, date range). Submit through the approved data destruction workflow. Halt if volume exceeds 1M records — escalate to ISSO.", "owner": "Data Steward", "sla_minutes": 480},
            {"order": 6, "title": "Obtain Certificate of Destruction", "description": "After purge completion, obtain a signed Certificate of Destruction from the storage administrator. Attach to the ATO evidence package under SI-12 evidence.", "owner": "ISSO", "sla_minutes": 60},
            {"order": 7, "title": "Re-assess and Close Finding", "description": "Re-run the DDC assessment to confirm retention score >= 75. Update the design's compliance notes with the retention window and Certificate of Destruction reference number.", "owner": "ISSO", "sla_minutes": 30},
        ]),
        "version": "1.0",
        "status": "approved",
        "classification": "CUI // SP-CTI",
        "owner": "Compliance Officer",
        "reviewer": "Data Steward",
        "approver": "ISSO",
    },
    {
        "id": "sop-ddc-pii-handling-procedure",
        "title": "PII Handling Procedure",
        "category": "pii_handling",
        "description": "Standard procedure for identifying, protecting, and auditing Personally Identifiable Information (PII) throughout its lifecycle in data designs — per Privacy Act of 1974, OMB M-17-12, and NIST SP 800-188.",
        "purpose": "Prevent unauthorized disclosure of PII by ensuring all PII fields are identified in data designs, encrypted at rest and in transit, access-logged, and that breach response procedures are defined before system authorization.",
        "scope": "Any data design that contains col-pii nodes, or entity tables with fields classified as PII under NIST SP 800-188 (name, SSN, address, biometrics, financial account numbers, health information). Applies to all IL levels.",
        "steps_json": json.dumps([
            {"order": 1, "title": "Identify All PII Nodes in Design", "description": "In the DDC canvas, run the assessment and review PII findings. Manually audit each entity node for hidden PII fields not yet labeled (search for: name, email, ssn, dob, phone, address, ip_address, biometric).", "owner": "Data Steward", "sla_minutes": 45},
            {"order": 2, "title": "Label PII Fields as col-pii Nodes", "description": "For every confirmed PII field, ensure it is represented as a col-pii node type in the canvas. Attach it to its parent entity. Add a 'PII' annotation to the node's classification attribute.", "owner": "Developer", "sla_minutes": 60},
            {"order": 3, "title": "Apply Encryption Controls", "description": "Confirm AES-256 encryption at rest for all tables containing PII. Verify TLS 1.2+ is enforced on all data-in-transit paths touching PII. Add ctrl-encrypt nodes to the design for each enforcement point.", "owner": "Security Engineer", "sla_minutes": 120},
            {"order": 4, "title": "Implement Access Logging and RBAC", "description": "Ensure all PII tables have row-level access logging enabled (e.g., PostgreSQL pgaudit, AWS CloudTrail). Define RBAC roles in the design: only data-specific roles may SELECT PII columns. Document in the design's RBAC node.", "owner": "Developer", "sla_minutes": 90},
            {"order": 5, "title": "Define Breach Response Procedure", "description": "Link this SOP to the Incident Response plan. Confirm that a PII breach triggers: (a) notification to ISSO within 1 hour, (b) OMB M-17-12 breach report within 72 hours, (c) affected individual notification per agency policy.", "owner": "ISSO", "sla_minutes": 60},
            {"order": 6, "title": "Validate with DDC Assessment", "description": "Re-run POST /data/api/designs/{id}/assess and verify: pii_coverage >= 0.95, all PII nodes have encryption control edges, no PII node is in an UNCLASSIFIED boundary. Resolve all findings.", "owner": "ISSO", "sla_minutes": 30},
            {"order": 7, "title": "Document PIA Reference", "description": "Record the Privacy Impact Assessment (PIA) document ID in the design's metadata. Attach PIA summary to ATO evidence package under PL-8 (Information Security Architecture).", "owner": "Privacy Officer", "sla_minutes": 30},
        ]),
        "version": "1.2",
        "status": "approved",
        "classification": "CUI // SP-CTI",
        "owner": "Privacy Officer",
        "reviewer": "Security Engineer",
        "approver": "ISSO",
    },
    {
        "id": "sop-ddc-backup-verification",
        "title": "Backup Verification",
        "category": "backup_verification",
        "description": "Procedure for verifying that backup jobs for all DDC-tracked databases and data stores have completed successfully, checksums match, and restoration can be performed within the required RTO — per NIST 800-53 CP-9.",
        "purpose": "Satisfy NIST 800-53 CP-9 (Information System Backup) and CP-10 (Information System Recovery and Reconstitution) by ensuring backups are complete, cryptographically verified, and restorable within the RTO defined in the Contingency Plan.",
        "scope": "All production databases and object storage buckets tracked in data designs with status 'approved'. Applies at every IL level. Test frequency: weekly for IL2, daily for IL4/IL5.",
        "steps_json": json.dumps([
            {"order": 1, "title": "Trigger Scheduled Backup", "description": "Initiate backup job via the approved backup tool (e.g., pg_dump, AWS Backup, Veeam). Record: start time, target storage location, backup type (full/incremental), and job ID.", "owner": "Data Engineer", "sla_minutes": 30},
            {"order": 2, "title": "Verify Backup Completion", "description": "Confirm backup job exited with status 0 (or equivalent success code). Check job logs for warnings. If the job failed or was incomplete, immediately escalate to on-call DBA. Do NOT proceed to step 3.", "owner": "Data Engineer", "sla_minutes": 15},
            {"order": 3, "title": "Validate Checksums", "description": "Compute SHA-256 hash of the backup artifact. Compare against the hash generated by the backup tool. If checksums mismatch, the backup is corrupt — discard and re-run. Log result (PASS/FAIL) with timestamp.", "owner": "Data Engineer", "sla_minutes": 20},
            {"order": 4, "title": "Test Restoration to Isolated Environment", "description": "Restore the backup to an isolated (non-production) environment. Run a minimal smoke test: connect to the restored DB, execute SELECT COUNT(*) on 3 critical tables, verify row counts match the production snapshot taken at backup time.", "owner": "DBA", "sla_minutes": 60},
            {"order": 5, "title": "Verify Data Integrity Post-Restore", "description": "Run referential integrity checks (FOREIGN KEY validation). Confirm no orphaned records. For PostgreSQL: run pg_dump --schema-only on restored DB and diff against the source schema. Record pass/fail.", "owner": "DBA", "sla_minutes": 45},
            {"order": 6, "title": "Log Verification Result", "description": "Record the verification outcome in the backup verification log: date, backup ID, checksum result, restoration result, integrity result, RTO measured. Store log in the ATO evidence package under CP-9 evidence.", "owner": "ISSO", "sla_minutes": 15},
            {"order": 7, "title": "Escalate on Failure", "description": "If any step (2–5) fails: (a) alert ISSO and mission owner immediately, (b) open a P1 incident ticket, (c) initiate the Contingency Plan activation checklist, (d) notify AO if RTO will be exceeded.", "owner": "ISSO", "sla_minutes": 15},
        ]),
        "version": "1.0",
        "status": "draft",
        "classification": "CUI // SP-CTI",
        "owner": "DBA",
        "reviewer": "Data Engineer",
        "approver": "ISSO",
    },
]


def init_db():
    """Initialize the Data Design Canvas database — create tables and seed templates and snippets."""
    conn = get_connection()
    try:
        if _DDC_BACKEND == "postgresql":
            # Use a BEGIN..END-aware splitter instead of a naive split on
            # semicolons: SQLite CREATE TRIGGER bodies contain embedded
            # semicolons that a naive split fragments into invalid pieces
            # (dcpr-db-02). SQLite
            # trigger DDL is not valid on PG anyway — real immutability is
            # rebuilt via the plpgsql trigger blocks below — so those specific
            # statements are expected to fail and are logged at debug level.
            # Any *other* non-benign error is surfaced as a warning instead of
            # being silently swallowed.
            for stmt in _split_sql_statements(SCHEMA):
                if stmt.startswith("--"):
                    continue
                try:
                    conn.execute(stmt)
                except Exception as _e:  # noqa: BLE001 — init-fallback tolerance
                    _head = " ".join(stmt.split())[:120]
                    if _is_benign_ddl_error(_e):
                        continue  # object already exists — idempotent re-init
                    if stmt.upper().lstrip().startswith("CREATE TRIGGER"):
                        # SQLite trigger syntax; PG equivalents built below.
                        logger.debug("[init_db] Skipping SQLite trigger DDL on PG: %s", _head)
                        continue
                    logger.warning("[init_db] DDL statement failed on PG: %s | %s", _head, _e)
            conn.commit()
            # PG audit immutability triggers — rebuild the append-only guards
            # that the SQLite trigger DDL above cannot provide on PostgreSQL.
            # Covers dd_audit, dm_audit, and dd_mapping_transforms (dcpr-db-02).
            try:
                conn.execute("""
                    CREATE OR REPLACE FUNCTION dd_audit_immutable()
                    RETURNS TRIGGER AS $$
                    BEGIN
                        RAISE EXCEPTION 'Audit records are immutable — NIST AU-6';
                        RETURN NULL;
                    END;
                    $$ LANGUAGE plpgsql;
                """)
                conn.execute("""
                    DROP TRIGGER IF EXISTS dd_audit_no_update ON dd_audit;
                    CREATE TRIGGER dd_audit_no_update
                        BEFORE UPDATE ON dd_audit
                        FOR EACH ROW EXECUTE FUNCTION dd_audit_immutable();
                """)
                conn.execute("""
                    DROP TRIGGER IF EXISTS dd_audit_no_delete ON dd_audit;
                    CREATE TRIGGER dd_audit_no_delete
                        BEFORE DELETE ON dd_audit
                        FOR EACH ROW EXECUTE FUNCTION dd_audit_immutable();
                """)
                # dm_audit — data-model audit trail (NIST AU-6)
                conn.execute("""
                    CREATE OR REPLACE FUNCTION dm_audit_immutable()
                    RETURNS TRIGGER AS $$
                    BEGIN
                        RAISE EXCEPTION 'dm_audit records are immutable — NIST AU-6';
                        RETURN NULL;
                    END;
                    $$ LANGUAGE plpgsql;
                """)
                conn.execute("""
                    DROP TRIGGER IF EXISTS dm_audit_no_update ON dm_audit;
                    CREATE TRIGGER dm_audit_no_update
                        BEFORE UPDATE ON dm_audit
                        FOR EACH ROW EXECUTE FUNCTION dm_audit_immutable();
                """)
                conn.execute("""
                    DROP TRIGGER IF EXISTS dm_audit_no_delete ON dm_audit;
                    CREATE TRIGGER dm_audit_no_delete
                        BEFORE DELETE ON dm_audit
                        FOR EACH ROW EXECUTE FUNCTION dm_audit_immutable();
                """)
                # dd_mapping_transforms — append-only transform ledger (NIST AU-9)
                conn.execute("""
                    CREATE OR REPLACE FUNCTION dd_mapping_transforms_immutable()
                    RETURNS TRIGGER AS $$
                    BEGIN
                        RAISE EXCEPTION 'dd_mapping_transforms is append-only — NIST AU-9';
                        RETURN NULL;
                    END;
                    $$ LANGUAGE plpgsql;
                """)
                conn.execute("""
                    DROP TRIGGER IF EXISTS dd_mapping_transforms_no_update ON dd_mapping_transforms;
                    CREATE TRIGGER dd_mapping_transforms_no_update
                        BEFORE UPDATE ON dd_mapping_transforms
                        FOR EACH ROW EXECUTE FUNCTION dd_mapping_transforms_immutable();
                """)
                conn.execute("""
                    DROP TRIGGER IF EXISTS dd_mapping_transforms_no_delete ON dd_mapping_transforms;
                    CREATE TRIGGER dd_mapping_transforms_no_delete
                        BEFORE DELETE ON dd_mapping_transforms
                        FOR EACH ROW EXECUTE FUNCTION dd_mapping_transforms_immutable();
                """)
            except Exception as _e:  # noqa: BLE001 — init-fallback tolerance
                logger.warning("[init_db] Failed to build PG immutability triggers: %s", _e)
            conn.commit()
        else:
            conn.executescript(SCHEMA)
            conn.commit()
            print(f"[init_db] Data Canvas schema created at {DB_PATH}")

        # CAM extension: dd_migration_jobs — tracks live data migration job status
        conn.executescript("""
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
""")
        conn.commit()

        # Migration: add owner_team and owner_email to dm_domains if missing
        for _col, _default in [("owner_team", "''"), ("owner_email", "''")]:
            try:
                conn.execute(f"ALTER TABLE dm_domains ADD COLUMN {_col} TEXT DEFAULT {_default}")
                conn.commit()
                print(f"[init_db] Migration applied: dm_domains.{_col} added.")
            except Exception as _e:
                if "duplicate column" in str(_e).lower() or "already exists" in str(_e).lower():
                    pass
                else:
                    raise

        for _col, _default in [
            ("output_port_type", "'table'"),
            ("sla_tier", "'standard'"),
            ("owner_team", "''"),
        ]:
            try:
                conn.execute(f"ALTER TABLE dm_data_products ADD COLUMN {_col} TEXT DEFAULT {_default}")
                conn.commit()
                print(f"[init_db] Migration applied: dm_data_products.{_col} added.")
            except Exception as _e:
                if "duplicate column" in str(_e).lower() or "already exists" in str(_e).lower():
                    pass
                else:
                    raise

        # Migration: add anomaly_json to dd_explore_profiles if missing
        try:
            conn.execute("ALTER TABLE dd_explore_profiles ADD COLUMN anomaly_json TEXT")
            conn.commit()
            print("[init_db] Migration applied: dd_explore_profiles.anomaly_json added.")
        except Exception as e:
            if "duplicate column" in str(e).lower() or "already exists" in str(e).lower():
                pass  # column already present — idempotent
            else:
                raise

        # Migration: add reflex_run to dd_quality_runs if missing
        try:
            conn.execute("ALTER TABLE dd_quality_runs ADD COLUMN reflex_run TEXT")
            conn.commit()
            print("[init_db] Migration applied: dd_quality_runs.reflex_run added.")
        except Exception as e:
            if "duplicate column" in str(e).lower() or "already exists" in str(e).lower():
                pass  # column already present — idempotent
            else:
                raise

        # Migration: reconcile data_nodes / data_edges with the authoritative SCHEMA.
        # Migration 031_ddc_twin_tables created these tables with a minimal shape
        # (node_id/metadata/source_id/target_id) and ran first on the shared PG
        # `icdev` DB, so CREATE TABLE IF NOT EXISTS above can never add the richer
        # graph columns declared in SCHEMA (x/y/properties_json, source_node_id/
        # target_node_id). Without these, any query reading node positions or edge
        # endpoints raises UndefinedColumn on PostgreSQL. Add them idempotently.
        for _table, _col, _coltype, _default in [
            ("data_nodes", "x", "REAL", "0"),
            ("data_nodes", "y", "REAL", "0"),
            ("data_nodes", "properties_json", "TEXT", "'{}'"),
            ("data_edges", "source_node_id", "TEXT", "''"),
            ("data_edges", "target_node_id", "TEXT", "''"),
        ]:
            try:
                conn.execute(
                    f"ALTER TABLE {_table} ADD COLUMN {_col} {_coltype} DEFAULT {_default}"
                )
                conn.commit()
                print(f"[init_db] Migration applied: {_table}.{_col} added.")
            except Exception as _e:
                if "duplicate column" in str(_e).lower() or "already exists" in str(_e).lower():
                    pass  # column already present — idempotent
                else:
                    raise

        # Seed templates (upsert)
        cur = conn.cursor()
        cur.execute("SELECT COUNT(*) FROM dd_templates")
        count = cur.fetchone()[0]
        added = 0
        for t in TEMPLATES:
            cur.execute("SELECT 1 FROM dd_templates WHERE id=?", (t["id"],))
            if not cur.fetchone():
                conn.execute(
                    "INSERT INTO dd_templates (id, name, category, description, graph_json, tags) VALUES (?,?,?,?,?,?)",
                    (t["id"], t["name"], t["category"], t["description"], t["graph_json"], t["tags"]),
                )
                added += 1
        if added:
            conn.commit()
            print(f"[init_db] Seeded {added} new DDC templates (total: {count + added}).")
        else:
            print(f"[init_db] All {count} DDC templates up to date.")

        # Seed snippets (upsert)
        cur.execute("SELECT COUNT(*) FROM dd_snippets")
        snp_count = cur.fetchone()[0]
        snp_added = 0
        for s in SNIPPETS:
            cur.execute("SELECT 1 FROM dd_snippets WHERE id=?", (s["id"],))
            if not cur.fetchone():
                conn.execute(
                    "INSERT INTO dd_snippets (id, name, category, description, graph_json, tags) VALUES (?,?,?,?,?,?)",
                    (s["id"], s["name"], s["category"], s["description"], s["graph_json"], s["tags"]),
                )
                snp_added += 1
        if snp_added:
            conn.commit()
            print(f"[init_db] Seeded {snp_added} new DDC snippets (total: {snp_count + snp_added}).")
        else:
            print(f"[init_db] All {snp_count} DDC snippets up to date.")

        # Seed runbooks (upsert)
        try:
            cur.execute("SELECT COUNT(*) FROM ddc_runbooks")
            rb_count = cur.fetchone()[0]
        except Exception:
            rb_count = 0
        rb_added = 0
        for rb in RUNBOOKS:
            cur.execute("SELECT 1 FROM ddc_runbooks WHERE id=?", (rb["id"],))
            if not cur.fetchone():
                conn.execute(
                    "INSERT INTO ddc_runbooks "
                    "(id, title, category, severity, description, trigger_condition, steps_json, classification, status) "
                    "VALUES (?,?,?,?,?,?,?,?,?)",
                    (
                        rb["id"],
                        rb["title"],
                        rb["category"],
                        rb["severity"],
                        rb["description"],
                        rb["trigger_condition"],
                        rb["steps_json"],
                        rb["classification"],
                        rb["status"],
                    ),
                )
                rb_added += 1
        if rb_added:
            conn.commit()
            print(f"[init_db] Seeded {rb_added} new DDC runbooks (total: {rb_count + rb_added}).")
        else:
            print(f"[init_db] All {rb_count} DDC runbooks up to date.")

        # Seed SOPs (upsert)
        try:
            cur.execute("SELECT COUNT(*) FROM ddc_sops")
            sop_count = cur.fetchone()[0]
        except Exception:
            sop_count = 0
        sop_added = 0
        for sop in SOPS:
            cur.execute("SELECT 1 FROM ddc_sops WHERE id=?", (sop["id"],))
            if not cur.fetchone():
                conn.execute(
                    "INSERT INTO ddc_sops "
                    "(id, title, category, description, purpose, scope, steps_json, version, "
                    "status, classification, owner, reviewer, approver) "
                    "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (
                        sop["id"],
                        sop["title"],
                        sop["category"],
                        sop["description"],
                        sop["purpose"],
                        sop["scope"],
                        sop["steps_json"],
                        sop["version"],
                        sop["status"],
                        sop["classification"],
                        sop["owner"],
                        sop["reviewer"],
                        sop["approver"],
                    ),
                )
                sop_added += 1
        if sop_added:
            conn.commit()
            print(f"[init_db] Seeded {sop_added} new DDC SOPs (total: {sop_count + sop_added}).")
        else:
            print(f"[init_db] All {sop_count} DDC SOPs up to date.")

    finally:
        conn.close()


if __name__ == "__main__":
    import sys
    if "--reinit" in sys.argv:
        print("[init_db] --reinit: applying schema migrations...")
    init_db()
