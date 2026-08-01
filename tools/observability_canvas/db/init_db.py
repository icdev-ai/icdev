# CUI // SP-CTI — ICDEV Observability Design Canvas DB Initializer
# Classification: CUI — Controlled Unclassified Information
"""
Observability Design Canvas — DB initializer.
Creates schema and seeds 5 canonical observability design templates.

Dual-backend: SQLite (default) or PostgreSQL.
Set OC_STORAGE_BACKEND=postgresql + OC_PG_* env vars to use PostgreSQL.
"""

import json
import os
import sqlite3
import sys
import uuid
from pathlib import Path

_ICDEV_ROOT = Path(__file__).resolve().parents[3]
DB_PATH = _ICDEV_ROOT / "data" / "observability_canvas.db"

_OC_BACKEND = os.environ.get("OC_STORAGE_BACKEND", os.environ.get("ICDEV_CANVAS_STORAGE_BACKEND", os.environ.get("ICDEV_STORAGE_BACKEND", "postgresql"))).lower()


def get_connection():
    """Get a database connection — SQLite or PostgreSQL.

    Returns a connection that supports:
        conn.execute(sql, params) — with ? placeholders (auto-translated for PG)
        conn.commit()
        conn.close()
        row["column_name"] — dict-like row access

    For both backends the returned connection auto-translates placeholders:
    PostgreSQL uses ICDEV's StorageConnection (? -> %s, PRAGMA -> no-op); the
    SQLite fallback is ALSO wrapped in StorageConnection (backend="sqlite") so
    that the %s placeholders used throughout this canvas's runtime and seed SQL
    are translated to ?. A raw sqlite3 connection does NOT understand %s and
    raises sqlite3.OperationalError ("near \"%\": syntax error") — that broke
    the template/SOP/runbook seed path and every runbooks.py/sops.py query on
    a fresh SQLite worktree.
    """
    if _OC_BACKEND == "postgresql":
        try:
            from tools.db.storage import get_canvas_connection
            # Canvas tables (observability_designs, od_*) have no classification/tenant_id
            # columns — must bypass RLS via get_canvas_connection.
            return get_canvas_connection("OC_STORAGE_BACKEND")
        except ImportError:
            pass
    # SQLite (default) — per-canvas DB, distinct from icdev.db.
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    raw = sqlite3.connect(str(DB_PATH))
    raw.row_factory = sqlite3.Row
    raw.execute("PRAGMA journal_mode=WAL")
    raw.execute("PRAGMA foreign_keys=ON")
    # Route through the translating StorageConnection wrapper so %s -> ? works
    # everywhere on this path (seed inserts + runbooks/sops runtime queries).
    try:
        from tools.db.storage import StorageConnection
        return StorageConnection(raw, "sqlite")
    except ImportError:
        return raw


# ── Schema ────────────────────────────────────────────────────────────────────

SCHEMA = """
CREATE TABLE IF NOT EXISTS observability_designs (
    id              TEXT PRIMARY KEY,
    name            TEXT NOT NULL,
    description     TEXT,
    graph_json      TEXT NOT NULL DEFAULT '{"nodes":[],"edges":[]}',
    template_id     TEXT,
    classification  TEXT DEFAULT 'CUI',
    created_at      TEXT DEFAULT CURRENT_TIMESTAMP,
    updated_at      TEXT DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS od_templates (
    id              TEXT PRIMARY KEY,
    name            TEXT NOT NULL,
    category        TEXT,
    description     TEXT,
    graph_json      TEXT NOT NULL DEFAULT '{"nodes":[],"edges":[]}',
    tags            TEXT DEFAULT '[]'
);

CREATE TABLE IF NOT EXISTS od_snippets (
    id          TEXT PRIMARY KEY,
    name        TEXT NOT NULL,
    category    TEXT,
    description TEXT,
    graph_json  TEXT NOT NULL DEFAULT '{"nodes":[],"edges":[]}',
    tags        TEXT DEFAULT '[]'
);

CREATE TABLE IF NOT EXISTS od_assessments (
    id              TEXT PRIMARY KEY,
    design_id       TEXT REFERENCES observability_designs(id),
    assessment_type TEXT NOT NULL,
    findings_json   TEXT DEFAULT '[]',
    score           REAL DEFAULT 0,
    grade           TEXT DEFAULT 'F',
    created_at      TEXT DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS od_audit (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    design_id       TEXT,
    actor           TEXT,
    action          TEXT NOT NULL,
    detail          TEXT,
    classification  TEXT DEFAULT 'CUI // SP-CTI',
    created_at      TEXT DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS od_versions (
    id              TEXT PRIMARY KEY,
    design_id       TEXT REFERENCES observability_designs(id),
    version_number  INTEGER NOT NULL,
    graph_json      TEXT NOT NULL DEFAULT '{"nodes":[],"edges":[]}',
    change_summary  TEXT DEFAULT '',
    user_id         TEXT DEFAULT '',
    created_at      TEXT DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_od_versions_design ON od_versions(design_id);

CREATE TABLE IF NOT EXISTS od_collab_sessions (
    id          TEXT PRIMARY KEY,
    design_id   TEXT NOT NULL REFERENCES observability_designs(id) ON DELETE CASCADE,
    user_id     TEXT NOT NULL,
    user_name   TEXT NOT NULL DEFAULT '',
    color       TEXT NOT NULL DEFAULT '#3498db',
    joined_at   TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    last_seen   TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    is_active   INTEGER NOT NULL DEFAULT 1
);

CREATE INDEX IF NOT EXISTS idx_od_collab_design ON od_collab_sessions(design_id);

CREATE TABLE IF NOT EXISTS odc_sops (
    id                  TEXT PRIMARY KEY,
    title               TEXT NOT NULL,
    sop_type            TEXT NOT NULL DEFAULT 'custom',
    description         TEXT DEFAULT '',
    purpose             TEXT DEFAULT '',
    scope               TEXT DEFAULT '',
    steps               TEXT DEFAULT '[]',
    nist_controls       TEXT DEFAULT '[]',
    owner               TEXT DEFAULT '',
    reviewer            TEXT DEFAULT '',
    approval_status     TEXT NOT NULL DEFAULT 'draft'
                            CHECK (approval_status IN ('draft','pending_review','approved','rejected')),
    approved_by         TEXT DEFAULT '',
    approved_at         TEXT DEFAULT '',
    rejected_reason     TEXT DEFAULT '',
    version             TEXT DEFAULT '1.0',
    next_review_date    TEXT DEFAULT '',
    classification      TEXT DEFAULT 'CUI',
    created_at          TEXT DEFAULT CURRENT_TIMESTAMP,
    updated_at          TEXT DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_odc_sops_status ON odc_sops(approval_status);
CREATE INDEX IF NOT EXISTS idx_odc_sops_type ON odc_sops(sop_type);

CREATE TABLE IF NOT EXISTS odc_runbooks (
    id                      TEXT PRIMARY KEY,
    title                   TEXT NOT NULL,
    category                TEXT NOT NULL DEFAULT 'general',
    trigger_condition       TEXT DEFAULT '',
    severity                TEXT NOT NULL DEFAULT 'medium'
                                CHECK (severity IN ('critical','high','medium','low')),
    description             TEXT DEFAULT '',
    steps                   TEXT DEFAULT '[]',
    nist_controls           TEXT DEFAULT '[]',
    tags                    TEXT DEFAULT '[]',
    owner                   TEXT DEFAULT '',
    estimated_duration_min  INTEGER DEFAULT 30,
    last_executed_at        TEXT DEFAULT '',
    execution_count         INTEGER DEFAULT 0,
    classification          TEXT DEFAULT 'CUI',
    created_at              TEXT DEFAULT CURRENT_TIMESTAMP,
    updated_at              TEXT DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_odc_runbooks_category ON odc_runbooks(category);
CREATE INDEX IF NOT EXISTS idx_odc_runbooks_severity ON odc_runbooks(severity);

CREATE TABLE IF NOT EXISTS od_ttp_coverage (
    id              TEXT PRIMARY KEY,
    ttp_id          TEXT NOT NULL,
    design_id       TEXT DEFAULT '',
    state           TEXT NOT NULL
                        CHECK (state IN ('full', 'partial', 'none')),
    sigma_match     INTEGER NOT NULL DEFAULT 0,
    baseline_match  INTEGER NOT NULL DEFAULT 0,
    detail          TEXT DEFAULT '{}',
    verified_at     TEXT DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_od_ttp_cov_ttp   ON od_ttp_coverage(ttp_id);
CREATE INDEX IF NOT EXISTS idx_od_ttp_cov_state ON od_ttp_coverage(state);

CREATE TABLE IF NOT EXISTS odc_gap_scores (
    id                  TEXT PRIMARY KEY,
    design_id           TEXT NOT NULL,
    total_techniques    INTEGER NOT NULL DEFAULT 0,
    covered_count       INTEGER NOT NULL DEFAULT 0,
    partial_count       INTEGER NOT NULL DEFAULT 0,
    gap_count           INTEGER NOT NULL DEFAULT 0,
    overall_gap_score   REAL NOT NULL DEFAULT 0.0,
    by_tactic           TEXT NOT NULL DEFAULT '{}',
    assessed_at         TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_odc_gap_scores_design ON odc_gap_scores(design_id);

CREATE TABLE IF NOT EXISTS odc_technique_coverage (
    id                      TEXT PRIMARY KEY,
    design_id               TEXT NOT NULL,
    technique_id            TEXT NOT NULL,
    coverage_state          TEXT NOT NULL
                                CHECK (coverage_state IN ('covered', 'partial', 'gap')),
    signal_sources_present  TEXT NOT NULL DEFAULT '[]',
    signal_sources_missing  TEXT NOT NULL DEFAULT '[]',
    gap_score               REAL NOT NULL DEFAULT 0.0,
    assessed_at             TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_odc_tc_design    ON odc_technique_coverage(design_id);
CREATE INDEX IF NOT EXISTS idx_odc_tc_technique ON odc_technique_coverage(technique_id);
CREATE INDEX IF NOT EXISTS idx_odc_tc_state     ON odc_technique_coverage(coverage_state);

CREATE TABLE IF NOT EXISTS odc_otel_events (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    design_id       TEXT NOT NULL,
    trace_id        TEXT NOT NULL DEFAULT '',
    span_id         TEXT NOT NULL DEFAULT '',
    event_name      TEXT NOT NULL,
    technique_id    TEXT NOT NULL DEFAULT '',
    signal_source   TEXT NOT NULL DEFAULT '',
    attributes      TEXT NOT NULL DEFAULT '{}',
    received_at     TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_odc_otel_design    ON odc_otel_events(design_id);
CREATE INDEX IF NOT EXISTS idx_odc_otel_technique ON odc_otel_events(technique_id);
CREATE INDEX IF NOT EXISTS idx_odc_otel_received  ON odc_otel_events(received_at);

CREATE TABLE IF NOT EXISTS odc_sdc_verifications (
    id              TEXT PRIMARY KEY,
    design_id       TEXT NOT NULL,
    ttp_list        TEXT NOT NULL DEFAULT '[]',
    covered_ttps    TEXT NOT NULL DEFAULT '[]',
    partial_ttps    TEXT NOT NULL DEFAULT '[]',
    gap_ttps        TEXT NOT NULL DEFAULT '[]',
    coverage_pct    REAL NOT NULL DEFAULT 0.0,
    verified_at     TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_odc_sdcv_design ON odc_sdc_verifications(design_id);

CREATE TABLE IF NOT EXISTS odc_mitre_techniques (
    id              TEXT PRIMARY KEY,
    technique_id    TEXT NOT NULL UNIQUE,
    name            TEXT NOT NULL,
    tactic          TEXT NOT NULL DEFAULT '',
    sigma_template  TEXT NOT NULL DEFAULT '',
    ingested_at     TEXT DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_odc_mt_technique ON odc_mitre_techniques(technique_id);
CREATE INDEX IF NOT EXISTS idx_odc_mt_tactic    ON odc_mitre_techniques(tactic);

CREATE TABLE IF NOT EXISTS odc_twin_snapshots (
    id              TEXT PRIMARY KEY,
    design_id       TEXT NOT NULL,
    label           TEXT NOT NULL DEFAULT '',
    service_count   INTEGER NOT NULL DEFAULT 0,
    coverage_score  REAL NOT NULL DEFAULT 0.0,
    coverage_basis  TEXT NOT NULL DEFAULT 'no_assessment',
    payload_json    TEXT NOT NULL DEFAULT '{}',
    created_at      TEXT DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_odc_twin_snap_design ON odc_twin_snapshots(design_id);
"""

# ── Template seeds ────────────────────────────────────────────────────────────


def _node(nid, label, ntype, x, y, extra=None):
    """Create a node dict for graph_json."""
    n = {"id": nid, "type": ntype, "label": label, "x": x, "y": y}
    if extra:
        n.update(extra)
    return n


def _edge(src, dst, label="", protocol="", encrypted=False):
    """Create an edge dict for graph_json."""
    return {
        "id": str(uuid.uuid4())[:8],
        "source": src,
        "target": dst,
        "label": label,
        "protocol": protocol,
        "encrypted": encrypted,
    }


ODC_SNIPPETS = [
    # 1 — Syslog Pipeline
    {
        "id": "snp-odc-syslog-pipeline",
        "name": "Syslog Pipeline",
        "category": "Collection",
        "description": "OS log collected via syslog-ng and forwarded to Splunk.",
        "tags": json.dumps(["syslog", "splunk", "collection"]),
        "graph_json": json.dumps(
            {
                "nodes": [
                    _node("os-log", "OS/System Logs", "src-os-log", 50, 80),
                    _node("syslog-ng", "Syslog-NG", "col-syslog-ng", 180, 80),
                    _node("splunk", "Splunk", "plt-splunk", 280, 80),
                ],
                "edges": [
                    _edge("os-log", "syslog-ng", "Syslog", "TCP/514"),
                    _edge("syslog-ng", "splunk", "HEC", "HTTPS", True),
                ],
            }
        ),
    },
    # 2 — K8s Monitoring
    {
        "id": "snp-odc-k8s-monitoring",
        "name": "K8s Monitoring",
        "category": "Collection",
        "description": "Container logs collected via Fluentd into Elastic/ELK.",
        "tags": json.dumps(["kubernetes", "fluentd", "elastic", "containers"]),
        "graph_json": json.dumps(
            {
                "nodes": [
                    _node("container-log", "Container Logs", "src-container-log", 50, 80),
                    _node("fluentd", "Fluentd", "col-fluentd", 180, 80),
                    _node("elk", "Elastic/ELK", "plt-elastic", 280, 80),
                ],
                "edges": [
                    _edge("container-log", "fluentd", "Stdout", ""),
                    _edge("fluentd", "elk", "Ingest", "HTTPS", True),
                ],
            }
        ),
    },
    # 3 — SIEM + SOAR Chain
    {
        "id": "snp-odc-siem-soar",
        "name": "SIEM + SOAR Chain",
        "category": "Automation",
        "description": "Splunk SIEM triggers alert rule, SOAR playbook creates ticket and notifies.",
        "tags": json.dumps(["siem", "soar", "splunk", "automation", "alerting"]),
        "graph_json": json.dumps(
            {
                "nodes": [
                    _node("splunk", "Splunk", "plt-splunk", 50, 80),
                    _node("alert-rule", "Alert Rule", "auto-alert-rule", 150, 80),
                    _node("soar", "SOAR Playbook", "auto-soar", 250, 80),
                    _node("ticket", "ServiceNow", "auto-ticket", 200, 170),
                    _node("notify", "PagerDuty", "auto-notification", 100, 170),
                ],
                "edges": [
                    _edge("splunk", "alert-rule", "Triggers", ""),
                    _edge("alert-rule", "soar", "Alert", "API"),
                    _edge("soar", "ticket", "Create Incident", "API", True),
                    _edge("soar", "notify", "Notify", "API", True),
                ],
            }
        ),
    },
    # 4 — OTel Full Stack
    {
        "id": "snp-odc-otel-fullstack",
        "name": "OTel Full Stack",
        "category": "Collection",
        "description": "Application logs, metrics, and traces via OTel Collector to Grafana.",
        "tags": json.dumps(["opentelemetry", "otel", "grafana", "metrics", "traces"]),
        "graph_json": json.dumps(
            {
                "nodes": [
                    _node("app-log", "Application Log", "src-app-log", 50, 30),
                    _node("metrics", "Metrics", "src-metric", 50, 100),
                    _node("traces", "Trace Spans", "src-trace", 50, 170),
                    _node("otel", "OTel Collector", "col-otel", 180, 100),
                    _node("grafana", "Grafana", "plt-grafana", 280, 100),
                ],
                "edges": [
                    _edge("app-log", "otel", "OTLP", "gRPC", True),
                    _edge("metrics", "otel", "OTLP", "gRPC", True),
                    _edge("traces", "otel", "OTLP", "gRPC", True),
                    _edge("otel", "grafana", "Prometheus", "HTTPS", True),
                ],
            }
        ),
    },
    # 5 — Cloud Audit Trail
    {
        "id": "snp-odc-cloud-audit",
        "name": "Cloud Audit Trail",
        "category": "Compliance",
        "description": "Cloud audit log collected via Fluentd with S3 archive and retention policy.",
        "tags": json.dumps(["cloud", "audit", "archive", "retention", "compliance"]),
        "graph_json": json.dumps(
            {
                "nodes": [
                    _node("cloud-log", "Cloud Audit Logs", "src-cloud-log", 50, 80),
                    _node("fluentd", "Fluentd", "col-fluentd", 150, 80),
                    _node("s3-archive", "S3 Log Archive", "col-s3", 250, 80),
                    _node("retention", "Log Retention Policy", "cmp-log-policy", 250, 170),
                ],
                "edges": [
                    _edge("cloud-log", "fluentd", "API", "HTTPS", True),
                    _edge("fluentd", "s3-archive", "Archive", "HTTPS", True),
                    _edge("s3-archive", "retention", "Policy", ""),
                ],
            }
        ),
    },
    # 6 — EDR + Threat Intel
    {
        "id": "snp-odc-edr-threat-intel",
        "name": "EDR + Threat Intel",
        "category": "Security",
        "description": "EDR telemetry via OTel to SIEM with threat intel enrichment.",
        "tags": json.dumps(["edr", "threat-intel", "siem", "enrichment"]),
        "graph_json": json.dumps(
            {
                "nodes": [
                    _node("edr", "EDR Telemetry", "src-endpoint", 50, 80),
                    _node("otel", "OTel Collector", "col-otel", 150, 80),
                    _node("splunk", "Splunk SIEM", "plt-splunk", 250, 80),
                    _node("enrichment", "Threat Intel Enrichment", "auto-enrichment", 250, 170),
                ],
                "edges": [
                    _edge("edr", "otel", "API", "HTTPS", True),
                    _edge("otel", "splunk", "HEC", "HTTPS", True),
                    _edge("splunk", "enrichment", "Lookup", "API", True),
                ],
            }
        ),
    },
    # 7 — Network IDS
    {
        "id": "snp-odc-network-ids",
        "name": "Network IDS",
        "category": "Security",
        "description": "Network flow and packet capture through Suricata IDS into ELK.",
        "tags": json.dumps(["network", "ids", "suricata", "pcap", "elk"]),
        "graph_json": json.dumps(
            {
                "nodes": [
                    _node("flow", "Network Flow", "src-flow", 50, 50),
                    _node("pcap", "Packet Capture", "src-pcap", 50, 140),
                    _node("suricata", "Suricata IDS", "plt-suricata", 180, 80),
                    _node("elk", "Elastic/ELK", "plt-elastic", 280, 80),
                ],
                "edges": [
                    _edge("flow", "suricata", "Forward", "TCP"),
                    _edge("pcap", "suricata", "Mirror/TAP", "Raw"),
                    _edge("suricata", "elk", "EVE JSON", "HTTPS", True),
                ],
            }
        ),
    },
    # 8 — Incident Response Chain
    {
        "id": "snp-odc-incident-response",
        "name": "Incident Response Chain",
        "category": "Automation",
        "description": "Alert rule triggers SOAR, executes runbook, creates ticket, sends notification.",
        "tags": json.dumps(["incident-response", "soar", "runbook", "automation"]),
        "graph_json": json.dumps(
            {
                "nodes": [
                    _node("alert-rule", "Alert Rule", "auto-alert-rule", 50, 80),
                    _node("soar", "SOAR Playbook", "auto-soar", 150, 80),
                    _node("runbook", "Runbook", "auto-runbook", 250, 30),
                    _node("ticket", "ServiceNow", "auto-ticket", 250, 130),
                    _node("notify", "PagerDuty", "auto-notification", 150, 170),
                ],
                "edges": [
                    _edge("alert-rule", "soar", "Alert", "API"),
                    _edge("soar", "runbook", "Execute", "API"),
                    _edge("soar", "ticket", "Create Incident", "API", True),
                    _edge("soar", "notify", "Notify", "API", True),
                ],
            }
        ),
    },
]

TEMPLATES = [
    # 1 — SOC Baseline (Splunk)
    {
        "id": "odt-soc-baseline-splunk",
        "name": "SOC Baseline (Splunk)",
        "category": "SOC",
        "description": "Traditional SOC architecture with Splunk SIEM. Covers OS logs, network "
        "logs, cloud audit logs, Filebeat + Logstash collection, Splunk analytics, "
        "alert rules, SOAR playbooks, PagerDuty notifications, and S3 archive.",
        "tags": json.dumps(["soc", "splunk", "siem", "baseline", "soar", "pagerduty"]),
        "graph_json": json.dumps(
            {
                "nodes": [
                    _node("os-log", "OS/System Logs", "src-os-log", 50, 100),
                    _node("net-log", "Network Logs", "src-network-log", 50, 200),
                    _node("cloud-log", "Cloud Audit Logs", "src-cloud-log", 50, 300),
                    _node("iam-log", "IAM/IdP Logs", "src-iam", 50, 400),
                    _node("filebeat", "Filebeat", "col-filebeat", 250, 150),
                    _node("logstash", "Logstash", "col-logstash", 250, 300),
                    _node("splunk", "Splunk", "plt-splunk", 500, 200),
                    _node("alert-rule", "Alert Rules", "auto-alert-rule", 700, 100),
                    _node("soar", "SOAR Playbook", "auto-soar", 700, 200),
                    _node("pagerduty", "PagerDuty", "auto-notification", 700, 300),
                    _node("ticket", "ServiceNow", "auto-ticket", 700, 400),
                    _node("s3-archive", "S3 Log Archive", "col-s3", 500, 400),
                    _node("retention", "Log Retention Policy", "cmp-log-policy", 500, 500),
                    _node("iac-ansible", "Ansible (IaC)", "auto-runbook", 250, 570),
                ],
                "edges": [
                    _edge("os-log", "filebeat", "Syslog", "TCP/514"),
                    _edge("net-log", "logstash", "Syslog", "UDP/514"),
                    _edge("cloud-log", "logstash", "API", "HTTPS", True),
                    _edge("iam-log", "filebeat", "Log file", ""),
                    _edge("filebeat", "splunk", "HEC", "HTTPS", True),
                    _edge("logstash", "splunk", "HEC", "HTTPS", True),
                    _edge("splunk", "alert-rule", "Triggers", ""),
                    _edge("alert-rule", "soar", "Alert", "API"),
                    _edge("soar", "pagerduty", "Notify", "API", True),
                    _edge("soar", "ticket", "Create Incident", "API", True),
                    _edge("logstash", "s3-archive", "Archive", "HTTPS", True),
                    _edge("s3-archive", "retention", "enforces"),
                    _edge("iac-ansible", "splunk", "provision", "SSH"),
                ],
            }
        ),
    },
    # 2 — Cloud-Native (Sentinel + OTel)
    {
        "id": "odt-cloud-native-sentinel",
        "name": "Cloud-Native (Sentinel + OTel)",
        "category": "Cloud",
        "description": "Cloud-native observability with Microsoft Sentinel SIEM, OpenTelemetry "
        "Collector for unified telemetry, Sentinel Playbooks for SOAR, and "
        "ServiceNow for incident management.",
        "tags": json.dumps(["cloud", "sentinel", "otel", "opentelemetry", "azure", "soar"]),
        "graph_json": json.dumps(
            {
                "nodes": [
                    _node("cloud-log", "Azure Activity Log", "src-cloud-log", 50, 100),
                    _node("container-log", "Container Logs", "src-container-log", 50, 200),
                    _node("app-log", "Application Logs", "src-app-log", 50, 300),
                    _node("traces", "Trace Spans", "src-trace", 50, 400),
                    _node("metrics", "Metrics", "src-metric", 50, 500),
                    _node("iam-log", "Azure AD Logs", "src-iam", 50, 600),
                    _node("otel", "OTel Collector", "col-otel", 300, 300),
                    _node("sentinel", "Microsoft Sentinel", "plt-sentinel", 550, 250),
                    _node("grafana", "Grafana", "plt-grafana", 550, 450),
                    _node("playbooks", "Sentinel Playbooks", "auto-soar", 750, 200),
                    _node("alert-rule", "Alert Rules", "auto-alert-rule", 750, 350),
                    _node("servicenow", "ServiceNow", "auto-ticket", 750, 500),
                    _node("retention", "Log Retention Policy", "cmp-log-policy", 550, 600),
                    _node("iac-terraform", "Terraform (IaC)", "auto-runbook", 300, 670),
                ],
                "edges": [
                    _edge("cloud-log", "otel", "API", "HTTPS", True),
                    _edge("container-log", "otel", "OTLP", "gRPC", True),
                    _edge("app-log", "otel", "OTLP", "gRPC", True),
                    _edge("traces", "otel", "OTLP", "gRPC", True),
                    _edge("metrics", "otel", "OTLP", "gRPC", True),
                    _edge("iam-log", "otel", "API", "HTTPS", True),
                    _edge("otel", "sentinel", "Export", "HTTPS", True),
                    _edge("otel", "grafana", "Prometheus Remote Write", "HTTPS", True),
                    _edge("sentinel", "alert-rule", "Triggers", ""),
                    _edge("alert-rule", "playbooks", "Alert", "API"),
                    _edge("playbooks", "servicenow", "Create Incident", "API", True),
                    _edge("sentinel", "retention", "enforces"),
                    _edge("iac-terraform", "sentinel", "provision", "API"),
                ],
            }
        ),
    },
    # 3 — Network Security Monitoring (Suricata + ELK)
    {
        "id": "odt-nsm-suricata-elk",
        "name": "Network Security Monitoring (Suricata + ELK)",
        "category": "NSM",
        "description": "Network security monitoring stack with NetFlow, packet capture, "
        "Suricata/Zeek IDS, Elastic/ELK for analysis, alert rules, and "
        "TheHive for incident response.",
        "tags": json.dumps(["nsm", "suricata", "zeek", "elk", "elastic", "thehive", "pcap"]),
        "graph_json": json.dumps(
            {
                "nodes": [
                    _node("flow", "NetFlow Data", "src-flow", 50, 100),
                    _node("pcap", "Packet Capture", "src-pcap", 50, 250),
                    _node("net-log", "Network Logs", "src-network-log", 50, 400),
                    _node("os-log", "OS/System Logs", "src-os-log", 50, 550),
                    _node("filebeat", "Filebeat", "col-filebeat", 250, 200),
                    _node("suricata", "Suricata/Zeek", "plt-suricata", 450, 150),
                    _node("elk", "Elastic/ELK", "plt-elastic", 450, 350),
                    _node("alert-rule", "Alert Rules", "auto-alert-rule", 650, 150),
                    _node("enrichment", "Threat Intel Enrichment", "auto-enrichment", 650, 300),
                    _node("thehive", "TheHive", "plt-thehive", 650, 450),
                    _node("retention", "Log Retention Policy", "cmp-log-policy", 450, 550),
                    _node("iac-ansible", "Ansible (IaC)", "auto-runbook", 250, 620),
                ],
                "edges": [
                    _edge("flow", "filebeat", "Forward", "TCP"),
                    _edge("pcap", "suricata", "Mirror/TAP", "Raw"),
                    _edge("net-log", "filebeat", "Syslog", "UDP/514"),
                    _edge("os-log", "filebeat", "File", ""),
                    _edge("filebeat", "elk", "Ingest", "HTTPS", True),
                    _edge("suricata", "elk", "EVE JSON", "HTTPS", True),
                    _edge("elk", "alert-rule", "Triggers", ""),
                    _edge("alert-rule", "enrichment", "Lookup", "API", True),
                    _edge("alert-rule", "thehive", "Create Case", "API", True),
                    _edge("elk", "retention", "enforces"),
                    _edge("iac-ansible", "elk", "provision", "SSH"),
                ],
            }
        ),
    },
    # 4 — Full-Stack Observability (Datadog)
    {
        "id": "odt-fullstack-datadog",
        "name": "Full-Stack Observability (Datadog)",
        "category": "APM",
        "description": "Full-stack observability with Datadog for logs, metrics, and traces. "
        "OTel Collector for vendor-neutral ingestion, Grafana for supplemental "
        "dashboards, PagerDuty for on-call, and alert rules.",
        "tags": json.dumps(["apm", "datadog", "otel", "grafana", "pagerduty", "fullstack"]),
        "graph_json": json.dumps(
            {
                "nodes": [
                    _node("app-log", "Application Logs", "src-app-log", 50, 100),
                    _node("metrics", "App Metrics", "src-metric", 50, 250),
                    _node("traces", "Trace Spans", "src-trace", 50, 400),
                    _node("container-log", "Container Logs", "src-container-log", 50, 550),
                    _node("iam-log", "IAM Logs", "src-iam", 50, 700),
                    _node("otel", "OTel Collector", "col-otel", 300, 300),
                    _node("datadog", "Datadog", "plt-datadog", 550, 250),
                    _node("grafana", "Grafana", "plt-grafana", 550, 450),
                    _node("alert-rule", "Alert Rules", "auto-alert-rule", 750, 200),
                    _node("pagerduty", "PagerDuty", "auto-notification", 750, 350),
                    _node("runbook", "Runbook", "auto-runbook", 750, 500),
                    _node("retention", "Log Retention Policy", "cmp-log-policy", 550, 600),
                    _node("iac-helm", "Helm (IaC)", "auto-runbook", 300, 670),
                ],
                "edges": [
                    _edge("app-log", "otel", "OTLP", "gRPC", True),
                    _edge("metrics", "otel", "OTLP", "gRPC", True),
                    _edge("traces", "otel", "OTLP", "gRPC", True),
                    _edge("container-log", "otel", "OTLP", "gRPC", True),
                    _edge("iam-log", "otel", "API", "HTTPS", True),
                    _edge("otel", "datadog", "DD Agent", "HTTPS", True),
                    _edge("otel", "grafana", "Prometheus", "HTTPS", True),
                    _edge("datadog", "alert-rule", "Monitor", ""),
                    _edge("alert-rule", "pagerduty", "Notify", "API", True),
                    _edge("alert-rule", "runbook", "Trigger", "API"),
                    _edge("datadog", "retention", "enforces"),
                    _edge("iac-helm", "datadog", "deploy", "K8s"),
                ],
            }
        ),
    },
    # 5 — DoD IL4 Compliance Stack
    {
        "id": "odt-dod-il4-compliance",
        "name": "DoD IL4 Compliance Stack",
        "category": "DoD",
        "description": "Comprehensive DoD IL4 observability stack with all source types, "
        "WEF for Windows endpoints, Fluentd collection, Splunk SIEM, "
        "EDR telemetry, IAM logs, SOAR automation, S3 archive, "
        "log retention policy, and MITRE ATT&CK detection baseline.",
        "tags": json.dumps(["dod", "il4", "compliance", "nist", "mitre", "splunk", "edr", "soar"]),
        "graph_json": json.dumps(
            {
                "nodes": [
                    # Sources (all types)
                    _node("app-log", "Application Logs", "src-app-log", 50, 50),
                    _node("os-log", "OS/System Logs", "src-os-log", 50, 130),
                    _node("net-log", "Network Logs", "src-network-log", 50, 210),
                    _node("cloud-log", "Cloud Audit Logs", "src-cloud-log", 50, 290),
                    _node("container-log", "Container Logs", "src-container-log", 50, 370),
                    _node("db-audit", "DB Audit Logs", "src-db-audit", 50, 450),
                    _node("edr", "EDR Telemetry", "src-endpoint", 50, 530),
                    _node("iam-log", "IAM/IdP Logs", "src-iam", 50, 610),
                    _node("vuln", "Vuln Scanner", "src-vulnerability", 50, 690),
                    _node("metrics", "Metrics", "src-metric", 50, 770),
                    _node("traces", "Traces", "src-trace", 50, 850),
                    # Collectors
                    _node("wef", "Windows Event Forwarding", "src-wef", 250, 130),
                    _node("fluentd", "Fluentd", "col-fluentd", 250, 350),
                    _node("otel", "OTel Collector", "col-otel", 250, 600),
                    _node("s3-archive", "S3 Log Archive", "col-s3", 250, 850),
                    # Platform
                    _node("splunk", "Splunk (GovCloud)", "plt-splunk", 500, 300),
                    _node("prometheus", "Prometheus", "plt-prometheus", 500, 600),
                    _node("grafana", "Grafana", "plt-grafana", 500, 750),
                    # Automation
                    _node("alert-rule", "Alert Rules", "auto-alert-rule", 700, 200),
                    _node("soar", "SOAR Playbook", "auto-soar", 700, 350),
                    _node("enrichment", "Threat Intel Enrichment", "auto-enrichment", 700, 500),
                    _node("ticket", "ServiceNow", "auto-ticket", 700, 650),
                    _node("notify", "PagerDuty", "auto-notification", 700, 800),
                    _node("runbook", "Runbook", "auto-runbook", 900, 350),
                    # Compliance
                    _node("retention", "Log Retention Policy", "cmp-log-policy", 500, 900),
                    _node(
                        "baseline",
                        "MITRE ATT&CK Baseline",
                        "cmp-baseline",
                        700,
                        900,
                        extra={
                            "config_json": json.dumps(
                                {
                                    "techniques": [
                                        {"id": "T1059", "name": "Command and Scripting Interpreter", "covered": True},
                                        {"id": "T1078", "name": "Valid Accounts", "covered": True},
                                        {"id": "T1071", "name": "Application Layer Protocol", "covered": True},
                                        {"id": "T1053", "name": "Scheduled Task/Job", "covered": False},
                                        {"id": "T1021", "name": "Remote Services", "covered": False},
                                        {"id": "T1055", "name": "Process Injection", "covered": False},
                                        {"id": "T1003", "name": "OS Credential Dumping", "covered": True},
                                        {"id": "T1110", "name": "Brute Force", "covered": True},
                                        {"id": "T1486", "name": "Data Encrypted for Impact", "covered": True},
                                        {"id": "T1562", "name": "Impair Defenses", "covered": False},
                                    ]
                                }
                            )
                        },
                    ),
                    _node("audit-report", "Audit Report", "cmp-audit-report", 900, 900),
                    _node("iac-terraform", "Terraform (IaC)", "auto-runbook", 250, 970),
                ],
                "edges": [
                    # Sources -> Collectors
                    _edge("app-log", "fluentd", "Forward", "TCP/24224"),
                    _edge("os-log", "wef", "WEF", "WinRM", True),
                    _edge("wef", "fluentd", "Forward", "TCP/24224"),
                    _edge("net-log", "fluentd", "Syslog", "TLS/6514", True),
                    _edge("cloud-log", "fluentd", "API", "HTTPS", True),
                    _edge("container-log", "fluentd", "Stdout", ""),
                    _edge("db-audit", "fluentd", "File", ""),
                    _edge("edr", "otel", "API", "HTTPS", True),
                    _edge("iam-log", "otel", "API", "HTTPS", True),
                    _edge("vuln", "otel", "API", "HTTPS", True),
                    _edge("metrics", "otel", "OTLP", "gRPC", True),
                    _edge("traces", "otel", "OTLP", "gRPC", True),
                    # Collectors -> Platforms
                    _edge("fluentd", "splunk", "HEC", "HTTPS", True),
                    _edge("otel", "splunk", "HEC", "HTTPS", True),
                    _edge("otel", "prometheus", "Remote Write", "HTTPS", True),
                    _edge("fluentd", "s3-archive", "Archive", "HTTPS", True),
                    # Platform -> Visualization
                    _edge("prometheus", "grafana", "Query", "HTTPS", True),
                    # Platform -> Automation
                    _edge("splunk", "alert-rule", "Triggers", ""),
                    _edge("alert-rule", "soar", "Alert", "API"),
                    _edge("alert-rule", "enrichment", "Lookup", "API", True),
                    _edge("soar", "ticket", "Create Incident", "API", True),
                    _edge("soar", "notify", "Notify", "API", True),
                    _edge("soar", "runbook", "Execute", "API"),
                    _edge("s3-archive", "retention", "enforces"),
                    _edge("splunk", "baseline", "maps to"),
                    _edge("splunk", "audit-report", "generates"),
                    _edge("iac-terraform", "splunk", "provision", "API"),
                ],
            }
        ),
    },
]


# ── Additive schema migrations ────────────────────────────────────────────────
#
# obx-fix-04: ODC-NDC-001 (check_nc_audit_to_siem_forwarder) needs to record,
# per ODC design, which NDC topology has its audit events forwarded to a SIEM.
# The base odc_sdc_verifications table only carries MITRE TTP-coverage columns,
# which cannot express audit->SIEM forwarding. These additive columns extend it
# WITHOUT touching the base CREATE TABLE (kept as a separate, idempotent block to
# avoid merge conflicts with concurrent init_db.py edits, e.g. PR #468).
_ODC_SDCV_ADDITIVE_COLUMNS = [
    ("topology_id", "TEXT DEFAULT ''"),      # NDC topology this row's forwarding covers
    ("siem_node_id", "TEXT DEFAULT ''"),     # SIEM node in the ODC design receiving audit
    ("forward_status", "TEXT DEFAULT ''"),   # '' | 'forwarded' | 'verified' | 'unverified'
]


def _existing_columns(conn, table):
    """Return the set of column names on ``table`` (backend-agnostic).

    Uses PRAGMA table_info, which StorageConnection translates to an
    information_schema query on PostgreSQL. Returns an empty set on error.
    """
    cols = set()
    try:
        for r in conn.execute(f"PRAGMA table_info({table})").fetchall():
            try:
                cols.add(r["name"])
            except (KeyError, IndexError, TypeError):
                try:
                    cols.add(r[1])
                except (KeyError, IndexError, TypeError):
                    pass
    except Exception:
        return set()
    return cols


def _ensure_odc_sdcv_columns(conn):
    """Idempotently add the ODC-NDC-001 audit-forwarding columns.

    Only ALTERs columns that are actually missing, so no statement is expected
    to fail — this avoids poisoning a PostgreSQL transaction with an
    already-exists error.
    """
    existing = _existing_columns(conn, "odc_sdc_verifications")
    if not existing:
        # Table not present yet (or introspection unavailable) — the base CREATE
        # TABLE runs first in init_db(), so this should not happen; bail safely.
        return
    for col, decl in _ODC_SDCV_ADDITIVE_COLUMNS:
        if col in existing:
            continue
        try:
            conn.execute(f"ALTER TABLE odc_sdc_verifications ADD COLUMN {col} {decl}")
        except Exception:
            # Best-effort: a concurrent init may have added it between introspection
            # and ALTER. The check that reads these columns is fail-closed regardless.
            pass


# ── Init function ────────────────────────────────────────────────────────────


def init_db():
    """Create schema and seed templates."""
    conn = get_connection()
    try:
        # Create schema
        for stmt in SCHEMA.strip().split(";"):
            stmt = stmt.strip()
            if stmt:
                conn.execute(stmt)
        conn.commit()

        # Additive columns for ODC-NDC-001 audit->SIEM forwarding (obx-fix-04).
        _ensure_odc_sdcv_columns(conn)
        conn.commit()

        # Seed templates (skip if already seeded)
        existing = conn.execute("SELECT COUNT(*) FROM od_templates").fetchone()[0]
        if existing == 0:
            for tpl in TEMPLATES:
                conn.execute(
                    "INSERT INTO od_templates (id, name, category, description, graph_json, tags) "
                    "VALUES (%s, %s, %s, %s, %s, %s)",
                    (tpl["id"], tpl["name"], tpl["category"], tpl["description"], tpl["graph_json"], tpl["tags"]),
                )
            conn.commit()

        # Seed snippets (upsert)
        snp_added = 0
        for s in ODC_SNIPPETS:
            check = conn.execute("SELECT 1 FROM od_snippets WHERE id=%s", (s["id"],)).fetchone()
            if not check:
                conn.execute(
                    "INSERT INTO od_snippets (id, name, category, description, graph_json, tags) VALUES (%s,%s,%s,%s,%s,%s)",
                    (s["id"], s["name"], s["category"], s["description"], s["graph_json"], s["tags"]),
                )
                snp_added += 1
        if snp_added:
            conn.commit()

    finally:
        conn.close()

    # Seed SOPs (separate connection, uses module's get_connection)
    try:
        from tools.observability_canvas.sops import seed_sops
        seed_sops()
    except Exception:
        pass

    # Seed runbooks
    try:
        from tools.observability_canvas.runbooks import seed_runbooks
        seed_runbooks()
    except Exception:
        pass


if __name__ == "__main__":
    init_db()
    conn = get_connection()
    tpl_count = conn.execute("SELECT COUNT(*) FROM od_templates").fetchone()[0]
    design_count = conn.execute("SELECT COUNT(*) FROM observability_designs").fetchone()[0]
    assessment_count = conn.execute("SELECT COUNT(*) FROM od_assessments").fetchone()[0]
    conn.close()
    result = {
        "status": "ok",
        "database": str(DB_PATH),
        "templates": tpl_count,
        "designs": design_count,
        "assessments": assessment_count,
    }
    if "--json" in sys.argv:
        print(json.dumps(result, indent=2))
    else:
        print(f"Observability Canvas DB initialized: {DB_PATH}")
        print(f"  Templates: {tpl_count}")
        print(f"  Designs:   {design_count}")
        print(f"  Assessments: {assessment_count}")
