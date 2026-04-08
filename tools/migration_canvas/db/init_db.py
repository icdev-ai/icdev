# CUI // SP-CTI
"""
Migration Design Canvas — DB initializer
Creates schema and seeds canonical migration design templates.

Dual-backend: SQLite (default) or PostgreSQL.
Set MC_STORAGE_BACKEND=postgresql + MC_PG_* env vars to use PostgreSQL.
"""

import json
import os
import sqlite3
from pathlib import Path

_ICDEV_ROOT = Path(__file__).resolve().parents[3]
DB_PATH = _ICDEV_ROOT / "data" / "migration_canvas.db"

_MC_BACKEND = os.environ.get("MC_STORAGE_BACKEND", os.environ.get("ICDEV_CANVAS_STORAGE_BACKEND", "sqlite")).lower()


def get_connection():
    """Get a database connection — SQLite or PostgreSQL.

    Returns a connection that supports:
        conn.execute(sql, params) — with ? placeholders (auto-translated for PG)
        conn.commit()
        conn.close()
        row["column_name"] — dict-like row access
    """
    if _MC_BACKEND == "postgresql":
        try:
            from tools.db.storage import get_connection as _icdev_conn

            conn = _icdev_conn(db_path=os.environ.get("MC_PG_DATABASE", "migration_canvas"))
            return conn
        except ImportError:
            pass
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


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
"""


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


def init_db():
    """Create tables and seed templates, snippets, runbooks, and SOPs."""
    with get_connection() as conn:
        conn.executescript(SCHEMA)

        # Seed templates
        for tpl in SEED_TEMPLATES:
            existing = conn.execute("SELECT id FROM mc_templates WHERE id=?", (tpl["id"],)).fetchone()
            if not existing:
                conn.execute(
                    "INSERT INTO mc_templates (id, name, category, description, graph_json, tags) VALUES (?,?,?,?,?,?)",
                    (tpl["id"], tpl["name"], tpl["category"], tpl["description"], tpl["graph_json"], tpl["tags"]),
                )

        # Seed snippets
        for snip in SEED_SNIPPETS:
            existing = conn.execute("SELECT id FROM mc_snippets WHERE id=?", (snip["id"],)).fetchone()
            if not existing:
                conn.execute(
                    "INSERT INTO mc_snippets (id, name, category, description, graph_json, tags) VALUES (?,?,?,?,?,?)",
                    (snip["id"], snip["name"], snip["category"], snip["description"], snip["graph_json"], snip["tags"]),
                )

        # Seed runbooks
        for rb in SEED_RUNBOOKS:
            existing = conn.execute("SELECT id FROM mc_runbooks WHERE id=?", (rb["id"],)).fetchone()
            if not existing:
                conn.execute(
                    "INSERT INTO mc_runbooks (id, title, trigger_event, severity, description, steps_json) VALUES (?,?,?,?,?,?)",
                    (rb["id"], rb["title"], rb["trigger_event"], rb["severity"], rb["description"], rb["steps_json"]),
                )

        # Seed SOPs
        for sop in SEED_SOPS:
            existing = conn.execute("SELECT id FROM mc_sops WHERE id=?", (sop["id"],)).fetchone()
            if not existing:
                conn.execute(
                    "INSERT INTO mc_sops (id, title, sop_type, description, purpose, scope, "
                    "nist_controls, steps, approval_status) VALUES (?,?,?,?,?,?,?,?,?)",
                    (sop["id"], sop["title"], sop["sop_type"], sop["description"],
                     sop["purpose"], sop["scope"], sop["nist_controls"], sop["steps"], "draft"),
                )

        conn.commit()


if __name__ == "__main__":
    init_db()
    print("Migration Canvas DB initialized at", DB_PATH)
