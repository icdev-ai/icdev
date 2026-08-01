# CUI // SP-CTI
"""
Boundary Design Canvas — DB initializer
Creates schema and seeds 5 canonical boundary design templates.

Dual-backend: PostgreSQL (default/primary) or SQLite (init-only fallback).
BDC_STORAGE_BACKEND defaults to ``postgresql``; set BDC_STORAGE_BACKEND=sqlite
to pin the per-canvas SQLite file (``boundary_canvas.db``) for dev, air-gap,
and single-user deployments. PostgreSQL is used for production
multi-user/global deployments.

The SQLite connection is wrapped in ICDEV's translating StorageConnection so
that runtime SQL written with PG-native ``%s`` placeholders is translated to
SQLite ``?`` — a RAW ``sqlite3.connect`` would raise ProgrammingError on ``%s``.
"""

import json
import os
import sqlite3
import sys
from pathlib import Path

# When integrated into ICDEV, DB lives in data/ directory
_ICDEV_ROOT = Path(__file__).resolve().parents[3]  # tools/boundary_canvas/db -> ICDev root
DB_PATH = _ICDEV_ROOT / "data" / "boundary_canvas.db"

# Backend detection — BDC_STORAGE_BACKEND only (NOT inherited from ICDEV_STORAGE_BACKEND)
# BDC has its own DB (boundary_canvas.db). Set BDC_STORAGE_BACKEND=postgresql to use PG.
_BDC_BACKEND = os.environ.get("BDC_STORAGE_BACKEND", os.environ.get("ICDEV_CANVAS_STORAGE_BACKEND", os.environ.get("ICDEV_STORAGE_BACKEND", "postgresql"))).lower()


def get_connection():
    """Get a database connection — SQLite or PostgreSQL.

    Returns a connection that supports:
        conn.execute(sql, params) — with ? placeholders (auto-translated for PG)
        conn.commit()
        conn.close()
        row["column_name"] — dict-like row access

    For PostgreSQL, uses ICDEV's StorageConnection wrapper which
    auto-translates SQLite SQL to PostgreSQL (? → %s, PRAGMA → no-op, etc.)
    """
    if _BDC_BACKEND == "postgresql":
        try:
            from tools.db.storage import get_canvas_connection

            # Use ICDEV's canvas connection (RLS disabled) for PostgreSQL
            conn = get_canvas_connection("BDC_PG_DATABASE")
            return conn
        except ImportError:
            pass  # Fall through to SQLite
    # SQLite (init-only fallback) — per-canvas DB, distinct from icdev.db.
    # Wrap the raw sqlite3 connection in ICDEV's StorageConnection so runtime
    # SQL written with PG-native %s placeholders is translated to ? on this
    # path. A raw sqlite3 connection does NOT translate %s and would raise
    # sqlite3.ProgrammingError on every %s query.
    raw = sqlite3.connect(str(DB_PATH))
    raw.row_factory = sqlite3.Row
    raw.execute("PRAGMA journal_mode=WAL")
    raw.execute("PRAGMA foreign_keys=ON")
    try:
        from tools.db.storage import StorageConnection

        return StorageConnection(raw, "sqlite")
    except ImportError:
        return raw


SCHEMA = """
CREATE TABLE IF NOT EXISTS boundary_designs (
    id              TEXT PRIMARY KEY,
    name            TEXT NOT NULL,
    description     TEXT,
    graph_json      TEXT NOT NULL DEFAULT '{"nodes":[],"edges":[],"boundaries":[]}',
    template_id     TEXT,
    classification  TEXT DEFAULT 'CUI',
    created_at      TEXT DEFAULT CURRENT_TIMESTAMP,
    updated_at      TEXT DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS bd_templates (
    id              TEXT PRIMARY KEY,
    name            TEXT NOT NULL,
    category        TEXT,
    description     TEXT,
    graph_json      TEXT NOT NULL DEFAULT '{"nodes":[],"edges":[],"boundaries":[]}',
    tags            TEXT DEFAULT '[]'
);

CREATE TABLE IF NOT EXISTS bd_snippets (
    id          TEXT PRIMARY KEY,
    name        TEXT NOT NULL,
    category    TEXT,
    description TEXT,
    graph_json  TEXT NOT NULL DEFAULT '{"nodes":[],"edges":[],"boundaries":[]}',
    tags        TEXT DEFAULT '[]'
);

CREATE TABLE IF NOT EXISTS bd_assessments (
    id              TEXT PRIMARY KEY,
    design_id       TEXT REFERENCES boundary_designs(id),
    assessment_type TEXT NOT NULL DEFAULT 'full',
    findings_json   TEXT DEFAULT '[]',
    score           REAL DEFAULT 0,
    grade           TEXT DEFAULT 'N/A',
    cat1_findings   INTEGER DEFAULT 0,
    cat2_findings   INTEGER DEFAULT 0,
    cat3_findings   INTEGER DEFAULT 0,
    nist_coverage_json TEXT DEFAULT '{}',
    created_at      TEXT DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS bd_isa_tracker (
    id              TEXT PRIMARY KEY,
    design_id       TEXT REFERENCES boundary_designs(id),
    interconnection_id TEXT NOT NULL,
    isa_doc_id      TEXT,
    status          TEXT DEFAULT 'draft',
    expiry_date     TEXT,
    review_date     TEXT,
    owner           TEXT,
    notes           TEXT,
    created_at      TEXT DEFAULT CURRENT_TIMESTAMP,
    updated_at      TEXT DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS bd_audit (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    design_id       TEXT,
    "user"          TEXT DEFAULT '',
    action          TEXT NOT NULL,
    detail          TEXT,
    classification  TEXT DEFAULT 'CUI // SP-CTI',
    created_at      TEXT DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS bd_versions (
    id              TEXT PRIMARY KEY,
    design_id       TEXT REFERENCES boundary_designs(id),
    version_number  INTEGER NOT NULL,
    graph_json      TEXT NOT NULL DEFAULT '{"nodes":[],"edges":[],"boundaries":[]}',
    change_summary  TEXT DEFAULT '',
    user_id         TEXT DEFAULT '',
    created_at      TEXT DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS bd_collab_sessions (
    id          TEXT PRIMARY KEY,
    design_id   TEXT NOT NULL REFERENCES boundary_designs(id) ON DELETE CASCADE,
    user_id     TEXT NOT NULL,
    user_name   TEXT NOT NULL DEFAULT '',
    color       TEXT NOT NULL DEFAULT '#3498db',
    joined_at   TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    last_seen   TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    is_active   INTEGER NOT NULL DEFAULT 1
);

CREATE INDEX IF NOT EXISTS idx_bd_collab_design ON bd_collab_sessions(design_id);

CREATE TABLE IF NOT EXISTS bd_alerts (
    id              TEXT PRIMARY KEY,
    design_id       TEXT REFERENCES boundary_designs(id),
    isa_id          TEXT REFERENCES bd_isa_tracker(id),
    alert_type      TEXT NOT NULL,
    severity        TEXT DEFAULT 'medium',
    days_until_expiry INTEGER,
    message         TEXT NOT NULL,
    acknowledged    INTEGER DEFAULT 0,
    acknowledged_by TEXT DEFAULT '',
    acknowledged_at TEXT DEFAULT '',
    created_at      TEXT DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS bdc_runbooks (
    id              TEXT PRIMARY KEY,
    design_id       TEXT REFERENCES boundary_designs(id),
    title           TEXT NOT NULL,
    trigger_event   TEXT NOT NULL DEFAULT 'boundary_breach',
    severity        TEXT DEFAULT 'high',
    description     TEXT DEFAULT '',
    steps_json      TEXT DEFAULT '[]',
    owner           TEXT DEFAULT '',
    classification  TEXT DEFAULT 'CUI // SP-CTI',
    created_at      TEXT DEFAULT CURRENT_TIMESTAMP,
    updated_at      TEXT DEFAULT CURRENT_TIMESTAMP
);

-- Indexes
CREATE INDEX IF NOT EXISTS idx_bd_assessments_design ON bd_assessments(design_id);
CREATE INDEX IF NOT EXISTS idx_bd_isa_tracker_design ON bd_isa_tracker(design_id);
CREATE INDEX IF NOT EXISTS idx_bd_isa_tracker_status ON bd_isa_tracker(status);
CREATE INDEX IF NOT EXISTS idx_bd_audit_design ON bd_audit(design_id);
CREATE INDEX IF NOT EXISTS idx_bd_audit_action ON bd_audit(action);
CREATE INDEX IF NOT EXISTS idx_bd_alerts_design ON bd_alerts(design_id);
CREATE INDEX IF NOT EXISTS idx_bd_alerts_acknowledged ON bd_alerts(acknowledged);
CREATE INDEX IF NOT EXISTS idx_bdc_runbooks_design ON bdc_runbooks(design_id);
CREATE INDEX IF NOT EXISTS idx_bdc_runbooks_trigger ON bdc_runbooks(trigger_event);

CREATE TABLE IF NOT EXISTS bdc_sops (
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

CREATE INDEX IF NOT EXISTS idx_bdc_sops_type ON bdc_sops(sop_type);
CREATE INDEX IF NOT EXISTS idx_bdc_sops_status ON bdc_sops(approval_status);

CREATE TABLE IF NOT EXISTS bd_authorized_components (
    id              TEXT PRIMARY KEY,
    component_type  TEXT NOT NULL DEFAULT 'airgap_bundle',
    name            TEXT NOT NULL,
    version         TEXT DEFAULT '',
    bundle_path     TEXT DEFAULT '',
    sha256_manifest TEXT DEFAULT '',
    sbom_path       TEXT DEFAULT '',
    impact_levels   TEXT DEFAULT '[]',
    file_count      INTEGER DEFAULT 0,
    sbom_count      INTEGER DEFAULT 0,
    classification  TEXT DEFAULT 'CUI // SP-CTI',
    registered_by   TEXT DEFAULT 'icdev-airgap-engine',
    status          TEXT DEFAULT 'authorized' CHECK (status IN ('authorized', 'revoked', 'pending')),
    notes           TEXT DEFAULT '',
    created_at      TEXT DEFAULT CURRENT_TIMESTAMP,
    updated_at      TEXT DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_bd_auth_comp_type ON bd_authorized_components(component_type);
CREATE INDEX IF NOT EXISTS idx_bd_auth_comp_status ON bd_authorized_components(status);
"""


# ── Seed Templates ──────────────────────────────────────────────────────────


def _tpl_single_system_ato():
    """Template 1: Single System ATO Boundary.

    Layout:
    - Row 0 (y=30): ATO boundary (wide, tall container)
    - Row 1 (y=60): DMZ boundary (inside ATO, left side)
    - Row 2 (y=120): Controls row inside ATO (firewall, proxy spaced 170px)
    - Row 3 (y=200): Systems row (app server inside ATO, ISA, external)
    - Row 4 (y=300): Second controls row (IDS, SIEM, mTLS spaced 170px)
    - Row 5 (y=420): Documentation row (ISA doc, PPS, DFD)
    """
    return {
        "nodes": [
            # ATO boundary — wide enough to contain all internal nodes
            {
                "id": "bnd-1",
                "type": "bnd-ato",
                "label": "ATO Boundary",
                "x": 80,
                "y": 30,
                "width": 550,
                "height": 360,
                "contained_nodes": ["sys-1", "ctrl-fw-1", "ctrl-ids-1", "ctrl-siem-1"],
            },
            # DMZ boundary — inside ATO, left side
            {
                "id": "bnd-dmz-1",
                "type": "bnd-dmz",
                "label": "DMZ",
                "x": 110,
                "y": 80,
                "width": 200,
                "height": 120,
                "contained_nodes": ["ctrl-fw-1", "ctrl-proxy-1"],
            },
            # Controls inside DMZ — spaced 170px apart
            {
                "id": "ctrl-fw-1",
                "type": "ctrl-firewall",
                "label": "Boundary Firewall",
                "x": 140,
                "y": 120,
                "properties": {"parent_boundary": "bnd-1"},
            },
            {"id": "ctrl-proxy-1", "type": "ctrl-proxy", "label": "Reverse Proxy", "x": 140, "y": 200},
            # Internal system inside ATO
            {
                "id": "sys-1",
                "type": "sys-internal",
                "label": "Application Server",
                "x": 400,
                "y": 150,
                "properties": {"parent_boundary": "bnd-1"},
            },
            # Controls row inside ATO — spaced horizontally
            {
                "id": "ctrl-ids-1",
                "type": "ctrl-ids-ips",
                "label": "IDS/IPS",
                "x": 200,
                "y": 300,
                "properties": {"parent_boundary": "bnd-1"},
            },
            {"id": "ctrl-siem-1", "type": "ctrl-siem", "label": "SIEM", "x": 400, "y": 300},
            # ISA and external system — outside ATO boundary
            {"id": "isa-api-1", "type": "isa-api", "label": "Partner API ISA", "x": 700, "y": 150},
            {"id": "sys-ext-1", "type": "sys-external", "label": "External Partner API", "x": 900, "y": 150},
            {"id": "ctrl-pki-1", "type": "ctrl-certificate", "label": "mTLS Certs", "x": 700, "y": 300},
            # Documentation row — bottom, spaced 170px apart
            {"id": "doc-isa-1", "type": "doc-isa", "label": "Partner ISA Agreement", "x": 700, "y": 420},
            {"id": "doc-pps-1", "type": "doc-pps-matrix", "label": "PPS Matrix", "x": 120, "y": 420},
            {"id": "doc-dfd-1", "type": "doc-dfd", "label": "Data Flow Diagram", "x": 350, "y": 420},
            # IaC deployment
            {"id": "doc-iac-1", "type": "doc-conops", "label": "Terraform Landing Zone", "x": 550, "y": 420},
        ],
        "edges": [
            {"id": "e1", "source": "sys-1", "target": "isa-api-1", "type": "isa-api"},
            {"id": "e2", "source": "isa-api-1", "target": "sys-ext-1"},
            {"id": "e3", "source": "ctrl-fw-1", "target": "bnd-1"},
            {"id": "e4", "source": "ctrl-ids-1", "target": "bnd-1"},
            {"id": "e5", "source": "ctrl-siem-1", "target": "bnd-1"},
            {"id": "e6", "source": "ctrl-pki-1", "target": "isa-api-1"},
            {"id": "e7", "source": "doc-isa-1", "target": "isa-api-1"},
            {"id": "e8", "source": "doc-pps-1", "target": "bnd-1"},
            {"id": "e9", "source": "doc-dfd-1", "target": "bnd-1"},
            {"id": "e10", "source": "sys-1", "target": "bnd-dmz-1", "label": "resides in"},
            {"id": "e11", "source": "ctrl-proxy-1", "target": "bnd-dmz-1", "label": "protects"},
            {"id": "e12", "source": "doc-iac-1", "target": "bnd-1", "label": "provisions"},
        ],
    }


def _tpl_multi_enclave_dod():
    """Template 2: Multi-Enclave DoD Program.

    Layout:
    - Row 0 (y=20): ATO boundary (full width, tall)
    - Row 1 (y=80): Three classification enclaves side-by-side inside ATO
    - Row 2 (y=160): Systems inside their enclaves
    - Row 3 (y=280): CDS nodes between enclaves
    - Row 4 (y=400): Controls row (firewall, IDS, SIEM, MFA)
    - Row 5 (y=520): VPN + cloud + BCAP
    - Row 6 (y=660): Documentation row
    """
    return {
        "nodes": [
            # Boundaries — with explicit width/height so they contain children
            {
                "id": "bnd-ato-1",
                "type": "bnd-ato",
                "label": "Program ATO Boundary",
                "x": 20,
                "y": 20,
                "width": 1060,
                "height": 580,
            },
            {
                "id": "bnd-cui-1",
                "type": "bnd-classification",
                "label": "CUI Enclave (IL5)",
                "x": 60,
                "y": 80,
                "width": 280,
                "height": 240,
            },
            {
                "id": "bnd-secret-1",
                "type": "bnd-classification",
                "label": "SECRET Enclave (IL6)",
                "x": 400,
                "y": 80,
                "width": 280,
                "height": 240,
            },
            {
                "id": "bnd-ts-1",
                "type": "bnd-classification",
                "label": "TS/SCI SCIF",
                "x": 740,
                "y": 80,
                "width": 280,
                "height": 240,
            },
            # Systems — centered inside their enclave boundaries
            {"id": "sys-cui-1", "type": "sys-internal", "label": "CUI Mission App", "x": 130, "y": 170},
            {"id": "sys-secret-1", "type": "sys-internal", "label": "SECRET C2 System", "x": 470, "y": 170},
            {"id": "sys-ts-1", "type": "sys-internal", "label": "TS/SCI Intel System", "x": 810, "y": 170},
            # Cross-domain solutions — between enclaves
            {"id": "isa-cds-1", "type": "isa-cross-domain", "label": "CUI-to-SECRET CDS", "x": 350, "y": 270},
            {"id": "isa-cds-2", "type": "isa-cross-domain", "label": "SECRET-to-TS CDS", "x": 690, "y": 270},
            # Controls row — spaced horizontally
            {"id": "ctrl-fw-main", "type": "ctrl-firewall", "label": "Program Firewall", "x": 60, "y": 400},
            {"id": "ctrl-ids-main", "type": "ctrl-ids-ips", "label": "Program IDS/IPS", "x": 230, "y": 400},
            {"id": "ctrl-siem-main", "type": "ctrl-siem", "label": "Program SIEM", "x": 400, "y": 400},
            {"id": "ctrl-mfa-1", "type": "ctrl-mfa", "label": "MFA Gateway", "x": 570, "y": 400},
            # Cloud + VPN + BCAP row
            {"id": "ctrl-bcap-1", "type": "ctrl-bcap", "label": "BCAP", "x": 60, "y": 520},
            {"id": "isa-vpn-1", "type": "isa-vpn", "label": "GovCloud VPN", "x": 230, "y": 520},
            {"id": "sys-cloud-1", "type": "sys-cloud", "label": "AWS GovCloud", "x": 430, "y": 520},
            # Documentation row — bottom
            {"id": "doc-isa-vpn", "type": "doc-isa", "label": "GovCloud ISA", "x": 60, "y": 660},
            {"id": "doc-pps-1", "type": "doc-pps-matrix", "label": "PPS Matrix", "x": 230, "y": 660},
            {"id": "doc-dfd-1", "type": "doc-dfd", "label": "Data Flow Diagram", "x": 400, "y": 660},
            # IaC deployment
            {"id": "doc-iac-1", "type": "doc-conops", "label": "Terraform Landing Zone", "x": 600, "y": 660},
        ],
        "edges": [
            # Cross-domain flows
            {"id": "e1", "source": "sys-cui-1", "target": "isa-cds-1", "label": "CUI data"},
            {"id": "e2", "source": "isa-cds-1", "target": "sys-secret-1", "label": "filtered"},
            {"id": "e3", "source": "sys-secret-1", "target": "isa-cds-2", "label": "SECRET data"},
            {"id": "e4", "source": "isa-cds-2", "target": "sys-ts-1", "label": "filtered"},
            # Cloud interconnection
            {"id": "e5", "source": "sys-cui-1", "target": "isa-vpn-1"},
            {"id": "e6", "source": "isa-vpn-1", "target": "sys-cloud-1", "label": "IPSec"},
            {"id": "e7", "source": "ctrl-bcap-1", "target": "isa-vpn-1", "label": "inspect"},
            # Controls → boundary
            {"id": "e8", "source": "ctrl-fw-main", "target": "bnd-ato-1", "label": "perimeter"},
            {"id": "e9", "source": "ctrl-ids-main", "target": "bnd-ato-1", "label": "monitor"},
            {"id": "e10", "source": "ctrl-siem-main", "target": "bnd-ato-1", "label": "log"},
            {"id": "e11", "source": "ctrl-mfa-1", "target": "isa-vpn-1", "label": "auth"},
            # Documentation
            {"id": "e12", "source": "doc-isa-vpn", "target": "isa-vpn-1"},
            {"id": "e13", "source": "doc-pps-1", "target": "bnd-ato-1"},
            {"id": "e14", "source": "doc-dfd-1", "target": "bnd-ato-1"},
            {"id": "e15", "source": "sys-cui-1", "target": "bnd-cui-1", "label": "resides in"},
            {"id": "e16", "source": "sys-secret-1", "target": "bnd-secret-1", "label": "resides in"},
            {"id": "e17", "source": "sys-ts-1", "target": "bnd-ts-1", "label": "resides in"},
            {"id": "e18", "source": "doc-iac-1", "target": "bnd-ato-1", "label": "provisions"},
        ],
    }


def _tpl_fedramp_cloud():
    """Template 3: FedRAMP Cloud Authorization.

    Layout:
    - Row 0 (y=20): FedRAMP boundary (wide, tall)
    - Row 1 (y=80): MFA + SAML federation + Okta (horizontal)
    - Row 2 (y=180): WAF + CSP + Agency App + mTLS (horizontal inside boundary)
    - Row 3 (y=280): IDS + Splunk API + Splunk Cloud (horizontal)
    - Row 4 (y=380): SIEM + Agency VPN + Agency DC (horizontal)
    - Row 5 (y=480): CAP + docs row
    - Row 6 (y=560): Documentation row
    """
    return {
        "nodes": [
            # FedRAMP boundary — wide enough for CSP + Agency App + controls
            {
                "id": "bnd-frp-1",
                "type": "bnd-fedramp",
                "label": "FedRAMP Authorization Boundary",
                "x": 50,
                "y": 20,
                "width": 550,
                "height": 480,
                "contained_nodes": ["sys-csp-1", "sys-int-1", "ctrl-fw-1", "ctrl-ids-1", "ctrl-siem-1"],
            },
            # Row 1: MFA + Federation + Okta
            {"id": "ctrl-mfa-1", "type": "ctrl-mfa", "label": "MFA Gateway", "x": 250, "y": 80},
            {"id": "isa-fed-1", "type": "isa-federation", "label": "SAML Federation", "x": 500, "y": 80},
            {
                "id": "sys-saas-1",
                "type": "sys-saas",
                "label": "Okta (IdP)",
                "x": 700,
                "y": 80,
                "properties": {"fedramp_authorized": True},
            },
            # Row 2: Controls + systems inside FedRAMP boundary
            {
                "id": "ctrl-fw-1",
                "type": "ctrl-firewall",
                "label": "WAF + FW",
                "x": 100,
                "y": 180,
                "properties": {"parent_boundary": "bnd-frp-1"},
            },
            {
                "id": "sys-csp-1",
                "type": "sys-cloud",
                "label": "CSP Infrastructure (IaaS)",
                "x": 300,
                "y": 180,
                "properties": {"parent_boundary": "bnd-frp-1"},
            },
            {
                "id": "sys-int-1",
                "type": "sys-internal",
                "label": "Agency Application",
                "x": 500,
                "y": 180,
                "properties": {"parent_boundary": "bnd-frp-1"},
            },
            {"id": "ctrl-pki-1", "type": "ctrl-certificate", "label": "mTLS", "x": 700, "y": 180},
            # Row 3: IDS + Splunk API + Splunk Cloud
            {"id": "ctrl-ids-1", "type": "ctrl-ids-ips", "label": "IDS/IPS", "x": 100, "y": 280},
            {"id": "isa-api-1", "type": "isa-api", "label": "Splunk API", "x": 500, "y": 280},
            {
                "id": "sys-saas-2",
                "type": "sys-saas",
                "label": "Splunk Cloud",
                "x": 700,
                "y": 280,
                "properties": {"fedramp_authorized": True},
            },
            # Row 4: SIEM + Agency VPN + Agency DC
            {"id": "ctrl-siem-1", "type": "ctrl-siem", "label": "SIEM", "x": 100, "y": 380},
            {"id": "isa-vpn-1", "type": "isa-vpn", "label": "Agency VPN", "x": 500, "y": 380},
            {"id": "sys-ext-1", "type": "sys-external", "label": "Agency Data Center", "x": 700, "y": 380},
            # Row 5: CAP
            {"id": "ctrl-cap-1", "type": "ctrl-cap", "label": "TIC 3.0 CAP", "x": 100, "y": 480},
            # Row 6: ISA docs
            {"id": "doc-isa-2", "type": "doc-isa", "label": "Splunk ISA", "x": 700, "y": 460},
            {"id": "doc-isa-1", "type": "doc-isa", "label": "Agency VPN ISA", "x": 900, "y": 380},
            # Row 7: Documentation bottom row
            {"id": "doc-pps-1", "type": "doc-pps-matrix", "label": "PPS Matrix", "x": 100, "y": 560},
            {"id": "doc-dfd-1", "type": "doc-dfd", "label": "Data Flow Diagram", "x": 350, "y": 560},
            # IaC deployment
            {"id": "doc-iac-1", "type": "doc-conops", "label": "Terraform Landing Zone", "x": 600, "y": 560},
        ],
        "edges": [
            {"id": "e1", "source": "sys-int-1", "target": "isa-fed-1"},
            {"id": "e2", "source": "isa-fed-1", "target": "sys-saas-1"},
            {"id": "e3", "source": "sys-int-1", "target": "isa-api-1"},
            {"id": "e4", "source": "isa-api-1", "target": "sys-saas-2"},
            {"id": "e5", "source": "sys-int-1", "target": "isa-vpn-1"},
            {"id": "e6", "source": "isa-vpn-1", "target": "sys-ext-1"},
            {"id": "e7", "source": "ctrl-fw-1", "target": "bnd-frp-1"},
            {"id": "e8", "source": "ctrl-ids-1", "target": "bnd-frp-1"},
            {"id": "e9", "source": "ctrl-siem-1", "target": "bnd-frp-1"},
            {"id": "e10", "source": "ctrl-cap-1", "target": "bnd-frp-1"},
            {"id": "e11", "source": "ctrl-mfa-1", "target": "isa-fed-1"},
            {"id": "e12", "source": "ctrl-pki-1", "target": "isa-api-1"},
            {"id": "e13", "source": "doc-isa-1", "target": "isa-vpn-1"},
            {"id": "e14", "source": "doc-isa-2", "target": "isa-api-1"},
            {"id": "e15", "source": "doc-pps-1", "target": "bnd-frp-1"},
            {"id": "e16", "source": "doc-dfd-1", "target": "bnd-frp-1"},
            {"id": "e17", "source": "sys-csp-1", "target": "sys-int-1", "label": "hosts"},
            {"id": "e18", "source": "doc-iac-1", "target": "bnd-frp-1", "label": "provisions"},
        ],
    }


def _tpl_healthcare_hipaa():
    """Template 4: Healthcare System (HIPAA).

    Layout:
    - Row 0 (y=20): ATO boundary (wide, tall container)
    - Row 1 (y=80): HIPAA zone (left) + PCI zone (right) inside ATO
    - Row 2 (y=140): Systems inside zones (EHR in HIPAA, Billing in PCI)
    - Row 3 (y=280): Controls row (FW, IDS, PPS, mTLS spaced 170px)
    - Row 4 (y=380): ISA connections (SFTP, Claims API)
    - Row 5 (y=480): External systems (Lab, Payer) + SIEM + DLP
    - Row 6 (y=580): Documentation row
    """
    return {
        "nodes": [
            # ATO boundary — wide enough for both zones + controls
            {
                "id": "bnd-ato-1",
                "type": "bnd-ato",
                "label": "Healthcare ATO Boundary",
                "x": 30,
                "y": 20,
                "width": 800,
                "height": 540,
                "contained_nodes": [
                    "bnd-hipaa-1",
                    "bnd-pci-1",
                    "sys-ehr-1",
                    "sys-billing-1",
                    "ctrl-fw-1",
                    "ctrl-ids-1",
                    "ctrl-siem-1",
                    "ctrl-dlp-1",
                ],
            },
            # HIPAA zone — left side inside ATO
            {
                "id": "bnd-hipaa-1",
                "type": "bnd-hipaa",
                "label": "HIPAA PHI Zone",
                "x": 80,
                "y": 80,
                "width": 300,
                "height": 200,
                "contained_nodes": ["sys-ehr-1"],
            },
            # PCI zone — right side inside ATO
            {
                "id": "bnd-pci-1",
                "type": "bnd-pci",
                "label": "PCI CDE",
                "x": 450,
                "y": 80,
                "width": 300,
                "height": 200,
                "contained_nodes": ["sys-billing-1"],
            },
            # Systems inside zones
            {
                "id": "sys-ehr-1",
                "type": "sys-internal",
                "label": "EHR System",
                "x": 160,
                "y": 160,
                "properties": {"parent_boundary": "bnd-hipaa-1"},
            },
            {
                "id": "sys-billing-1",
                "type": "sys-internal",
                "label": "Billing/Payment System",
                "x": 530,
                "y": 160,
                "properties": {"parent_boundary": "bnd-pci-1"},
            },
            # Controls row — horizontal, spaced 170px
            {"id": "ctrl-fw-1", "type": "ctrl-firewall", "label": "Boundary Firewall", "x": 80, "y": 320},
            {"id": "ctrl-ids-1", "type": "ctrl-ids-ips", "label": "IDS/IPS", "x": 250, "y": 320},
            {"id": "ctrl-pps-1", "type": "ctrl-pps", "label": "PPS Filter", "x": 420, "y": 320},
            {"id": "ctrl-pki-1", "type": "ctrl-certificate", "label": "mTLS Certs", "x": 590, "y": 320},
            # Second controls row — SIEM + DLP
            {"id": "ctrl-siem-1", "type": "ctrl-siem", "label": "SIEM", "x": 80, "y": 420},
            {"id": "ctrl-dlp-1", "type": "ctrl-dlp", "label": "DLP Gateway", "x": 250, "y": 420},
            # ISA connections
            {"id": "isa-file-1", "type": "isa-file", "label": "Lab Results SFTP", "x": 160, "y": 500},
            {"id": "isa-api-1", "type": "isa-api", "label": "Claims API", "x": 530, "y": 500},
            # External systems — outside ATO
            {"id": "sys-ext-lab", "type": "sys-external", "label": "External Lab System", "x": 160, "y": 600},
            {"id": "sys-ext-payer", "type": "sys-external", "label": "Insurance Payer", "x": 530, "y": 600},
            # ISA documents
            {"id": "doc-isa-lab", "type": "doc-isa", "label": "Lab ISA", "x": 350, "y": 600},
            {"id": "doc-isa-payer", "type": "doc-isa", "label": "Payer ISA", "x": 730, "y": 600},
            # Documentation row — bottom
            {"id": "doc-pps-1", "type": "doc-pps-matrix", "label": "PPS Matrix", "x": 100, "y": 700},
            {"id": "doc-dfd-1", "type": "doc-dfd", "label": "Data Flow Diagram", "x": 350, "y": 700},
            # IaC deployment
            {"id": "doc-iac-1", "type": "doc-conops", "label": "Terraform Landing Zone", "x": 600, "y": 700},
        ],
        "edges": [
            {"id": "e1", "source": "sys-ehr-1", "target": "isa-file-1"},
            {"id": "e2", "source": "isa-file-1", "target": "sys-ext-lab"},
            {"id": "e3", "source": "sys-billing-1", "target": "isa-api-1"},
            {"id": "e4", "source": "isa-api-1", "target": "sys-ext-payer"},
            {"id": "e5", "source": "ctrl-fw-1", "target": "bnd-ato-1"},
            {"id": "e6", "source": "ctrl-ids-1", "target": "bnd-ato-1"},
            {"id": "e7", "source": "ctrl-siem-1", "target": "bnd-ato-1"},
            {"id": "e8", "source": "ctrl-dlp-1", "target": "bnd-ato-1"},
            {"id": "e9", "source": "ctrl-pki-1", "target": "isa-file-1"},
            {"id": "e10", "source": "ctrl-pki-1", "target": "isa-api-1"},
            {"id": "e11", "source": "ctrl-pps-1", "target": "isa-file-1"},
            {"id": "e12", "source": "ctrl-pps-1", "target": "isa-api-1"},
            {"id": "e13", "source": "doc-isa-lab", "target": "isa-file-1"},
            {"id": "e14", "source": "doc-isa-payer", "target": "isa-api-1"},
            {"id": "e15", "source": "doc-pps-1", "target": "bnd-ato-1"},
            {"id": "e16", "source": "doc-dfd-1", "target": "bnd-ato-1"},
            {"id": "e17", "source": "sys-ehr-1", "target": "bnd-hipaa-1", "label": "resides in"},
            {"id": "e18", "source": "sys-billing-1", "target": "bnd-pci-1", "label": "resides in"},
            {"id": "e19", "source": "doc-iac-1", "target": "bnd-ato-1", "label": "provisions"},
        ],
    }


def _tpl_hybrid_multi_cloud():
    """Template 5: Hybrid Multi-Cloud Boundary.

    Layout:
    - Row 0 (y=20): ATO boundary (wide, tall)
    - Row 1 (y=80): AWS enclave (left) + Azure enclave (right) inside ATO
    - Row 2 (y=160): Cloud workloads inside enclaves + Azure AD Federation
    - Row 3 (y=300): VPN connections + On-Prem + API ISA (spaced 170px)
    - Row 4 (y=400): Controls row (FW, IDS, SIEM, BCAP) horizontal
    - Row 5 (y=500): PPS + mTLS + partner ISA + external system
    - Row 6 (y=600): Documentation row
    """
    return {
        "nodes": [
            # ATO boundary — wide enough for both cloud enclaves + controls
            {
                "id": "bnd-ato-1",
                "type": "bnd-ato",
                "label": "Enterprise ATO Boundary",
                "x": 20,
                "y": 20,
                "width": 900,
                "height": 560,
                "contained_nodes": [
                    "bnd-enc-aws",
                    "bnd-enc-azure",
                    "sys-onprem-1",
                    "ctrl-fw-1",
                    "ctrl-ids-1",
                    "ctrl-siem-1",
                    "ctrl-bcap-1",
                ],
            },
            # AWS enclave — left side inside ATO
            {
                "id": "bnd-enc-aws",
                "type": "bnd-enclave",
                "label": "AWS GovCloud Enclave",
                "x": 60,
                "y": 80,
                "width": 320,
                "height": 200,
                "contained_nodes": ["sys-aws-1"],
            },
            # Azure enclave — right side inside ATO
            {
                "id": "bnd-enc-azure",
                "type": "bnd-enclave",
                "label": "Azure Gov Enclave",
                "x": 450,
                "y": 80,
                "width": 320,
                "height": 200,
                "contained_nodes": ["sys-azure-1"],
            },
            # Cloud workloads inside enclaves
            {
                "id": "sys-aws-1",
                "type": "sys-cloud",
                "label": "AWS GovCloud Workloads",
                "x": 140,
                "y": 170,
                "properties": {"parent_boundary": "bnd-enc-aws"},
            },
            {
                "id": "sys-azure-1",
                "type": "sys-cloud",
                "label": "Azure Gov Workloads",
                "x": 530,
                "y": 170,
                "properties": {"parent_boundary": "bnd-enc-azure"},
            },
            # Federation
            {"id": "isa-fed-1", "type": "isa-federation", "label": "Azure AD Federation", "x": 700, "y": 120},
            {"id": "ctrl-mfa-1", "type": "ctrl-mfa", "label": "MFA Gateway", "x": 700, "y": 200},
            # VPN + On-Prem row
            {"id": "isa-vpn-1", "type": "isa-vpn", "label": "AWS Direct Connect VPN", "x": 140, "y": 320},
            {
                "id": "sys-onprem-1",
                "type": "sys-internal",
                "label": "On-Prem Data Center",
                "x": 360,
                "y": 320,
                "properties": {"parent_boundary": "bnd-ato-1"},
            },
            {"id": "isa-vpn-2", "type": "isa-vpn", "label": "Azure ExpressRoute VPN", "x": 580, "y": 320},
            # Controls row — horizontal, spaced 170px
            {"id": "ctrl-fw-1", "type": "ctrl-firewall", "label": "Enterprise Firewall", "x": 80, "y": 440},
            {"id": "ctrl-ids-1", "type": "ctrl-ids-ips", "label": "IDS/IPS", "x": 250, "y": 440},
            {"id": "ctrl-siem-1", "type": "ctrl-siem", "label": "Centralized SIEM", "x": 420, "y": 440},
            {"id": "ctrl-bcap-1", "type": "ctrl-bcap", "label": "BCAP", "x": 590, "y": 440},
            # PPS + mTLS row
            {"id": "ctrl-pps-1", "type": "ctrl-pps", "label": "PPS Filter", "x": 250, "y": 540},
            {"id": "ctrl-pki-1", "type": "ctrl-certificate", "label": "mTLS Certs", "x": 420, "y": 540},
            # Partner API + external — outside ATO
            {"id": "isa-api-1", "type": "isa-api", "label": "Partner REST API", "x": 800, "y": 320},
            {"id": "sys-ext-1", "type": "sys-external", "label": "Partner Organization", "x": 1000, "y": 320},
            {"id": "doc-isa-partner", "type": "doc-isa", "label": "Partner ISA", "x": 1000, "y": 420},
            # ISA docs
            {"id": "doc-isa-aws", "type": "doc-isa", "label": "AWS ISA", "x": 140, "y": 620},
            {"id": "doc-isa-azure", "type": "doc-isa", "label": "Azure ISA", "x": 420, "y": 620},
            # Documentation row — bottom
            {"id": "doc-pps-1", "type": "doc-pps-matrix", "label": "PPS Matrix", "x": 80, "y": 700},
            {"id": "doc-dfd-1", "type": "doc-dfd", "label": "Data Flow Diagram", "x": 350, "y": 700},
            # IaC deployment
            {"id": "doc-iac-1", "type": "doc-conops", "label": "Terraform Landing Zone", "x": 600, "y": 700},
        ],
        "edges": [
            {"id": "e1", "source": "sys-onprem-1", "target": "isa-vpn-1"},
            {"id": "e2", "source": "isa-vpn-1", "target": "sys-aws-1"},
            {"id": "e3", "source": "sys-onprem-1", "target": "isa-vpn-2"},
            {"id": "e4", "source": "isa-vpn-2", "target": "sys-azure-1"},
            {"id": "e5", "source": "sys-azure-1", "target": "isa-api-1"},
            {"id": "e6", "source": "isa-api-1", "target": "sys-ext-1"},
            {"id": "e7", "source": "sys-azure-1", "target": "isa-fed-1"},
            {"id": "e8", "source": "ctrl-fw-1", "target": "bnd-ato-1"},
            {"id": "e9", "source": "ctrl-ids-1", "target": "bnd-ato-1"},
            {"id": "e10", "source": "ctrl-siem-1", "target": "bnd-ato-1"},
            {"id": "e11", "source": "ctrl-bcap-1", "target": "bnd-ato-1"},
            {"id": "e12", "source": "ctrl-mfa-1", "target": "isa-fed-1"},
            {"id": "e13", "source": "ctrl-pki-1", "target": "isa-api-1"},
            {"id": "e14", "source": "ctrl-pps-1", "target": "isa-api-1"},
            {"id": "e15", "source": "ctrl-pps-1", "target": "isa-vpn-1"},
            {"id": "e16", "source": "ctrl-pps-1", "target": "isa-vpn-2"},
            {"id": "e17", "source": "doc-isa-aws", "target": "isa-vpn-1"},
            {"id": "e18", "source": "doc-isa-azure", "target": "isa-vpn-2"},
            {"id": "e19", "source": "doc-isa-partner", "target": "isa-api-1"},
            {"id": "e20", "source": "doc-pps-1", "target": "bnd-ato-1"},
            {"id": "e21", "source": "doc-dfd-1", "target": "bnd-ato-1"},
            {"id": "e22", "source": "sys-aws-1", "target": "bnd-enc-aws", "label": "resides in"},
            {"id": "e23", "source": "sys-azure-1", "target": "bnd-enc-azure", "label": "resides in"},
            {"id": "e24", "source": "doc-iac-1", "target": "bnd-ato-1", "label": "provisions"},
        ],
    }


def _tpl_scca_auth_boundary():
    """Template 6: SCCA Authorization Boundary.

    DoD SCCA authorization boundary with all 4 functional areas:
    BCAP (Boundary Cloud Access Point), VDSS (Virtual Data Center Security Stack),
    VDMS (Virtual Data Center Managed Services), TCCM (Tenant Cloud Credential Manager).

    Layout:
    - Row 0 (y=20): ATO boundary (full width, tall)
    - Row 1 (y=60): 4 enclave zones side-by-side inside ATO
    - Row 2 (y=100-200): Systems inside their enclave zones
    - Row 3 (y=320): Controls row (firewall, IDS, SIEM)
    - Row 4 (y=450): Interconnections (DISN, VPN)
    - Row 5 (y=570): Documentation row
    """
    return {
        "nodes": [
            # ATO boundary — wide enough to contain all zones
            {
                "id": "bnd-ato-1",
                "type": "bnd-ato",
                "label": "SCCA ATO Boundary",
                "x": 20,
                "y": 20,
                "width": 1000,
                "height": 600,
                "contained_nodes": [
                    "bnd-bcap",
                    "bnd-vdss",
                    "bnd-vdms",
                    "bnd-tccm",
                    "sys-bcap-proxy",
                    "sys-bcap-nfw",
                    "sys-vdss-stack",
                    "sys-vdms-svc",
                    "sys-tccm-cred",
                    "ctrl-fw-1",
                    "ctrl-ids-1",
                    "ctrl-siem-1",
                ],
            },
            # BCAP Zone
            {
                "id": "bnd-bcap",
                "type": "bnd-enclave",
                "label": "BCAP Zone",
                "x": 40,
                "y": 60,
                "width": 300,
                "height": 180,
                "contained_nodes": ["sys-bcap-proxy", "sys-bcap-nfw"],
            },
            {"id": "sys-bcap-proxy", "type": "sys-internal", "label": "BCAP Proxy", "x": 70, "y": 120},
            {"id": "sys-bcap-nfw", "type": "sys-internal", "label": "Network Firewall", "x": 220, "y": 120},
            # VDSS Zone
            {
                "id": "bnd-vdss",
                "type": "bnd-enclave",
                "label": "VDSS Zone",
                "x": 370,
                "y": 60,
                "width": 300,
                "height": 180,
                "contained_nodes": ["sys-vdss-stack"],
            },
            {"id": "sys-vdss-stack", "type": "sys-internal", "label": "Security Stack", "x": 450, "y": 120},
            # VDMS Zone
            {
                "id": "bnd-vdms",
                "type": "bnd-enclave",
                "label": "VDMS Zone",
                "x": 40,
                "y": 280,
                "width": 300,
                "height": 180,
                "contained_nodes": ["sys-vdms-svc"],
            },
            {"id": "sys-vdms-svc", "type": "sys-internal", "label": "Managed Services", "x": 120, "y": 340},
            # TCCM Zone
            {
                "id": "bnd-tccm",
                "type": "bnd-enclave",
                "label": "TCCM Zone",
                "x": 370,
                "y": 280,
                "width": 300,
                "height": 180,
                "contained_nodes": ["sys-tccm-cred"],
            },
            {"id": "sys-tccm-cred", "type": "sys-internal", "label": "Credential Manager", "x": 450, "y": 340},
            # Controls
            {"id": "ctrl-fw-1", "type": "ctrl-firewall", "label": "Boundary Firewall", "x": 720, "y": 80},
            {"id": "ctrl-ids-1", "type": "ctrl-ids-ips", "label": "IDS/IPS", "x": 720, "y": 200},
            {"id": "ctrl-siem-1", "type": "ctrl-siem", "label": "SIEM", "x": 720, "y": 320},
            # Interconnections — outside ATO boundary
            {"id": "isa-disn", "type": "isa-dedicated", "label": "DISN/DREN Circuit", "x": 720, "y": 480},
            {"id": "isa-vpn-1", "type": "isa-vpn", "label": "VPN to On-Prem", "x": 900, "y": 480},
            # Documentation
            {"id": "doc-isa-1", "type": "doc-isa", "label": "DISN ISA", "x": 720, "y": 570},
            {"id": "doc-pps-1", "type": "doc-pps-matrix", "label": "PPS Matrix", "x": 900, "y": 570},
            # IaC deployment
            {"id": "doc-iac-1", "type": "doc-conops", "label": "Terraform Landing Zone", "x": 720, "y": 650},
        ],
        "edges": [
            {"id": "e1", "source": "sys-bcap-proxy", "target": "bnd-ato-1"},
            {"id": "e2", "source": "sys-vdss-stack", "target": "bnd-ato-1"},
            {"id": "e3", "source": "sys-vdms-svc", "target": "bnd-ato-1"},
            {"id": "e4", "source": "sys-tccm-cred", "target": "bnd-ato-1"},
            {"id": "e5", "source": "ctrl-fw-1", "target": "bnd-ato-1"},
            {"id": "e6", "source": "ctrl-ids-1", "target": "bnd-ato-1"},
            {"id": "e7", "source": "ctrl-siem-1", "target": "bnd-ato-1"},
            {"id": "e8", "source": "isa-disn", "target": "doc-isa-1"},
            {"id": "e9", "source": "doc-isa-1", "target": "bnd-ato-1"},
            {"id": "e10", "source": "sys-bcap-proxy", "target": "bnd-bcap", "label": "resides in"},
            {"id": "e11", "source": "sys-bcap-nfw", "target": "bnd-bcap", "label": "resides in"},
            {"id": "e12", "source": "sys-vdss-stack", "target": "bnd-vdss", "label": "resides in"},
            {"id": "e13", "source": "sys-vdms-svc", "target": "bnd-vdms", "label": "resides in"},
            {"id": "e14", "source": "sys-tccm-cred", "target": "bnd-tccm", "label": "resides in"},
            {"id": "e15", "source": "isa-vpn-1", "target": "bnd-ato-1"},
            {"id": "e16", "source": "doc-pps-1", "target": "bnd-ato-1"},
            {"id": "e17", "source": "doc-iac-1", "target": "bnd-ato-1", "label": "provisions"},
        ],
    }


def _tpl_fedramp_agency_auth():
    """Template 7: FedRAMP Agency Authorization Boundary.

    Agency consuming FedRAMP-authorized CSP with TIC 3.0 compliance.

    Layout:
    - Row 0 (y=20): Agency ATO boundary (wide, tall)
    - Row 1 (y=60): FedRAMP CSP boundary (inside ATO)
    - Row 2 (y=120): CSP IaaS + Agency App inside CSP boundary
    - Row 3 (y=280): TIC 3.0 + Identity controls
    - Row 4 (y=400): SaaS providers, Agency on-prem
    - Row 5 (y=520): Interconnections + Documentation
    """
    return {
        "nodes": [
            # Agency ATO
            {
                "id": "bnd-ato-1",
                "type": "bnd-ato",
                "label": "Agency ATO Boundary",
                "x": 20,
                "y": 20,
                "width": 900,
                "height": 500,
                "contained_nodes": [
                    "bnd-fedramp",
                    "sys-csp",
                    "sys-app",
                    "ctrl-cap-1",
                    "ctrl-fw-1",
                    "isa-fed-1",
                    "ctrl-mfa-1",
                ],
            },
            # FedRAMP CSP boundary inside ATO
            {
                "id": "bnd-fedramp",
                "type": "bnd-fedramp",
                "label": "FedRAMP CSP Boundary",
                "x": 60,
                "y": 60,
                "width": 350,
                "height": 200,
                "contained_nodes": ["sys-csp", "sys-app"],
            },
            {"id": "sys-csp", "type": "sys-cloud", "label": "CSP IaaS", "x": 100, "y": 120},
            {"id": "sys-app", "type": "sys-internal", "label": "Agency App", "x": 280, "y": 120},
            # Agency On-Prem
            {"id": "sys-dc", "type": "sys-external", "label": "Agency Data Center", "x": 500, "y": 120},
            # TIC 3.0 / Identity
            {"id": "ctrl-cap-1", "type": "ctrl-cap", "label": "TIC 3.0 CAP", "x": 500, "y": 280},
            {"id": "ctrl-fw-1", "type": "ctrl-firewall", "label": "Agency Firewall", "x": 700, "y": 280},
            {"id": "isa-fed-1", "type": "isa-federation", "label": "SAML Federation", "x": 100, "y": 280},
            {"id": "ctrl-mfa-1", "type": "ctrl-mfa", "label": "PIV/CAC MFA", "x": 280, "y": 280},
            # SaaS providers
            {"id": "sys-saas-idp", "type": "sys-saas", "label": "FedRAMP SaaS (IdP)", "x": 100, "y": 400},
            {"id": "sys-saas-siem", "type": "sys-saas", "label": "FedRAMP SaaS (SIEM)", "x": 280, "y": 400},
            # Interconnections
            {"id": "isa-vpn-1", "type": "isa-vpn", "label": "Agency VPN", "x": 500, "y": 400},
            {"id": "isa-api-1", "type": "isa-api", "label": "SaaS API", "x": 700, "y": 400},
            # Documentation
            {"id": "doc-isa-1", "type": "doc-isa", "label": "CSP ISA", "x": 100, "y": 520},
            {"id": "doc-pps-1", "type": "doc-pps-matrix", "label": "PPS Matrix", "x": 280, "y": 520},
            # IaC deployment
            {"id": "doc-iac-1", "type": "doc-conops", "label": "Terraform Landing Zone", "x": 500, "y": 520},
        ],
        "edges": [
            {"id": "e1", "source": "sys-csp", "target": "bnd-fedramp", "label": "resides in"},
            {"id": "e2", "source": "sys-app", "target": "bnd-fedramp", "label": "resides in"},
            {"id": "e3", "source": "bnd-fedramp", "target": "bnd-ato-1", "label": "resides in"},
            {"id": "e4", "source": "ctrl-cap-1", "target": "bnd-ato-1"},
            {"id": "e5", "source": "ctrl-fw-1", "target": "bnd-ato-1"},
            {"id": "e6", "source": "isa-fed-1", "target": "sys-saas-idp"},
            {"id": "e7", "source": "ctrl-mfa-1", "target": "isa-fed-1"},
            {"id": "e8", "source": "isa-vpn-1", "target": "sys-dc"},
            {"id": "e9", "source": "isa-api-1", "target": "sys-saas-siem"},
            {"id": "e10", "source": "doc-isa-1", "target": "bnd-fedramp"},
            {"id": "e11", "source": "doc-pps-1", "target": "bnd-ato-1"},
            {"id": "e12", "source": "ctrl-cap-1", "target": "ctrl-fw-1"},
            {"id": "e13", "source": "sys-app", "target": "sys-csp"},
            {"id": "e14", "source": "doc-iac-1", "target": "bnd-ato-1", "label": "provisions"},
        ],
    }


def _tpl_lza_ou_structure():
    """Template 8: AWS LZA Multi-Account OU Structure.

    Layout:
    - Row 0 (y=20): AWS Cloud boundary (outer, wide, tall)
    - Row 1 (y=60): OU: Root (left), OU: Security (right)
    - Row 2 (y=400): OU: Infrastructure (left), OU: Workloads (right)
    - Systems (accounts) inside their OU boundaries
    - Controls + docs + interconnections
    """
    return {
        "nodes": [
            # Boundaries — outer AWS Cloud + 4 OUs
            {
                "id": "bnd-aws-cloud",
                "type": "bnd-ato",
                "label": "AWS Cloud (LZA)",
                "x": 20,
                "y": 20,
                "width": 1200,
                "height": 900,
            },
            {
                "id": "bnd-ou-root",
                "type": "bnd-enclave",
                "label": "OU: Root",
                "x": 40,
                "y": 60,
                "width": 400,
                "height": 300,
            },
            {
                "id": "bnd-ou-security",
                "type": "bnd-enclave",
                "label": "OU: Security",
                "x": 480,
                "y": 60,
                "width": 700,
                "height": 300,
            },
            {
                "id": "bnd-ou-infra",
                "type": "bnd-enclave",
                "label": "OU: Infrastructure",
                "x": 40,
                "y": 400,
                "width": 500,
                "height": 250,
            },
            {
                "id": "bnd-ou-workloads",
                "type": "bnd-enclave",
                "label": "OU: Workloads",
                "x": 580,
                "y": 400,
                "width": 600,
                "height": 250,
            },
            # Systems (accounts) — inside their OU boundaries
            {"id": "sys-mgmt", "type": "sys-internal", "label": "Management Account", "x": 80, "y": 130},
            {"id": "sys-log-archive", "type": "sys-internal", "label": "Log Archive Account", "x": 520, "y": 130},
            {
                "id": "sys-audit",
                "type": "sys-internal",
                "label": "Audit Account (Security-Tooling)",
                "x": 520,
                "y": 230,
            },
            {"id": "sys-network", "type": "sys-internal", "label": "Network Account (Transit)", "x": 80, "y": 470},
            {"id": "sys-shared", "type": "sys-internal", "label": "Shared Services Account", "x": 300, "y": 470},
            {"id": "sys-dev", "type": "sys-internal", "label": "Development Workload Account", "x": 620, "y": 470},
            {"id": "sys-test", "type": "sys-internal", "label": "Testing Workload Account", "x": 800, "y": 470},
            {"id": "sys-prod", "type": "sys-internal", "label": "Production Workload Account", "x": 980, "y": 470},
            # Controls
            {
                "id": "ctrl-sechub",
                "type": "ctrl-siem",
                "label": "Security Hub + GuardDuty + Config",
                "x": 750,
                "y": 130,
            },
            {"id": "ctrl-nfw", "type": "ctrl-firewall", "label": "Network Firewall + Transit GW", "x": 80, "y": 560},
            {"id": "ctrl-ct", "type": "ctrl-mfa", "label": "Control Tower + Organizations", "x": 80, "y": 230},
            # Documentation
            {"id": "doc-lza", "type": "doc-isa", "label": "LZA Terraform/CloudFormation", "x": 250, "y": 230},
            # Interconnections
            {"id": "isa-tgw", "type": "isa-vpn", "label": "Transit Gateway Hub", "x": 400, "y": 560},
            # IaC deployment
            {"id": "doc-iac-1", "type": "doc-conops", "label": "Terraform Landing Zone", "x": 400, "y": 700},
        ],
        "edges": [
            # Accounts → OUs (resides in)
            {"id": "e1", "source": "sys-mgmt", "target": "bnd-ou-root", "label": "resides in"},
            {"id": "e2", "source": "sys-log-archive", "target": "bnd-ou-security", "label": "resides in"},
            {"id": "e3", "source": "sys-audit", "target": "bnd-ou-security", "label": "resides in"},
            {"id": "e4", "source": "sys-network", "target": "bnd-ou-infra", "label": "resides in"},
            {"id": "e5", "source": "sys-shared", "target": "bnd-ou-infra", "label": "resides in"},
            {"id": "e6", "source": "sys-dev", "target": "bnd-ou-workloads", "label": "resides in"},
            {"id": "e7", "source": "sys-test", "target": "bnd-ou-workloads", "label": "resides in"},
            {"id": "e8", "source": "sys-prod", "target": "bnd-ou-workloads", "label": "resides in"},
            # Controls → accounts
            {"id": "e9", "source": "ctrl-ct", "target": "sys-mgmt", "label": "governs"},
            {"id": "e10", "source": "ctrl-sechub", "target": "sys-audit", "label": "monitors"},
            {"id": "e11", "source": "ctrl-nfw", "target": "sys-network", "label": "protects"},
            # Transit GW connectivity
            {"id": "e12", "source": "isa-tgw", "target": "sys-network", "label": "connects"},
            {"id": "e13", "source": "isa-tgw", "target": "sys-prod", "label": "routes"},
            {"id": "e14", "source": "isa-tgw", "target": "sys-shared", "label": "routes"},
            # IaC governance
            {"id": "e15", "source": "doc-lza", "target": "sys-mgmt", "label": "provisions"},
            # Audit trail
            {"id": "e16", "source": "sys-log-archive", "target": "bnd-ou-security", "label": "audit trail"},
            {"id": "e17", "source": "doc-iac-1", "target": "bnd-aws-cloud", "label": "provisions"},
        ],
    }


BDC_SNIPPETS = [
    # 1 — ISA with Firewall
    {
        "id": "snp-bdc-isa-firewall",
        "name": "ISA with Firewall",
        "category": "basic",
        "description": "External system with VPN ISA, boundary firewall, and ISA document.",
        "tags": json.dumps(["isa", "vpn", "firewall", "basic"]),
        "graph_json": json.dumps(
            {
                "nodes": [
                    {"id": "sys-ext-1", "type": "sys-external", "label": "External System", "x": 250, "y": 50},
                    {"id": "isa-vpn-1", "type": "isa-vpn", "label": "VPN ISA", "x": 150, "y": 50},
                    {"id": "ctrl-fw-1", "type": "ctrl-firewall", "label": "Boundary Firewall", "x": 50, "y": 50},
                    {"id": "doc-isa-1", "type": "doc-isa", "label": "ISA Agreement", "x": 150, "y": 150},
                ],
                "edges": [
                    {"id": "e1", "source": "ctrl-fw-1", "target": "isa-vpn-1"},
                    {"id": "e2", "source": "isa-vpn-1", "target": "sys-ext-1"},
                    {"id": "e3", "source": "doc-isa-1", "target": "isa-vpn-1"},
                ],
            }
        ),
    },
    # 2 — FedRAMP SaaS Connection
    {
        "id": "snp-bdc-fedramp-saas",
        "name": "FedRAMP SaaS Connection",
        "category": "fedramp",
        "description": "SaaS provider connected via API ISA with MFA and ISA document.",
        "tags": json.dumps(["fedramp", "saas", "api", "mfa"]),
        "graph_json": json.dumps(
            {
                "nodes": [
                    {"id": "sys-saas-1", "type": "sys-saas", "label": "SaaS Provider", "x": 250, "y": 50},
                    {"id": "isa-api-1", "type": "isa-api", "label": "API ISA", "x": 150, "y": 50},
                    {"id": "ctrl-mfa-1", "type": "ctrl-mfa", "label": "MFA Gateway", "x": 50, "y": 50},
                    {"id": "doc-isa-1", "type": "doc-isa", "label": "ISA Agreement", "x": 150, "y": 150},
                ],
                "edges": [
                    {"id": "e1", "source": "ctrl-mfa-1", "target": "isa-api-1"},
                    {"id": "e2", "source": "isa-api-1", "target": "sys-saas-1"},
                    {"id": "e3", "source": "doc-isa-1", "target": "isa-api-1"},
                ],
            }
        ),
    },
    # 3 — Cross-Domain Solution
    {
        "id": "snp-bdc-cross-domain",
        "name": "Cross-Domain Solution",
        "category": "dod",
        "description": "CUI zone and SECRET zone separated by a cross-domain solution with firewall.",
        "tags": json.dumps(["cross-domain", "cds", "cui", "secret", "dod"]),
        "graph_json": json.dumps(
            {
                "nodes": [
                    {
                        "id": "bnd-cui",
                        "type": "bnd-classification",
                        "label": "CUI Zone",
                        "x": 20,
                        "y": 20,
                        "width": 170,
                        "height": 120,
                        "contained_nodes": ["sys-cui-1"],
                    },
                    {"id": "sys-cui-1", "type": "sys-internal", "label": "CUI System", "x": 50, "y": 70},
                    {"id": "isa-cds-1", "type": "isa-cross-domain", "label": "Cross-Domain Guard", "x": 230, "y": 70},
                    {
                        "id": "bnd-secret",
                        "type": "bnd-classification",
                        "label": "SECRET Zone",
                        "x": 340,
                        "y": 20,
                        "width": 170,
                        "height": 120,
                        "contained_nodes": ["sys-secret-1"],
                    },
                    {"id": "sys-secret-1", "type": "sys-internal", "label": "SECRET System", "x": 370, "y": 70},
                    {"id": "ctrl-fw-1", "type": "ctrl-firewall", "label": "Firewall", "x": 230, "y": 170},
                ],
                "edges": [
                    {"id": "e1", "source": "sys-cui-1", "target": "isa-cds-1"},
                    {"id": "e2", "source": "isa-cds-1", "target": "sys-secret-1"},
                    {"id": "e3", "source": "ctrl-fw-1", "target": "isa-cds-1"},
                    {"id": "e4", "source": "sys-cui-1", "target": "bnd-cui", "label": "resides in"},
                    {"id": "e5", "source": "sys-secret-1", "target": "bnd-secret", "label": "resides in"},
                ],
            }
        ),
    },
    # 4 — BCAP/CAP Egress
    {
        "id": "snp-bdc-bcap-egress",
        "name": "BCAP/CAP Egress",
        "category": "dod",
        "description": "Cloud service through BCAP with IDS/IPS and DLP inspection.",
        "tags": json.dumps(["bcap", "cap", "egress", "dlp", "ids"]),
        "graph_json": json.dumps(
            {
                "nodes": [
                    {"id": "sys-cloud-1", "type": "sys-cloud", "label": "Cloud Service", "x": 50, "y": 50},
                    {"id": "ctrl-bcap-1", "type": "ctrl-bcap", "label": "BCAP", "x": 150, "y": 50},
                    {"id": "ctrl-ids-1", "type": "ctrl-ids-ips", "label": "IDS/IPS", "x": 250, "y": 50},
                    {"id": "ctrl-dlp-1", "type": "ctrl-dlp", "label": "DLP Gateway", "x": 250, "y": 150},
                ],
                "edges": [
                    {"id": "e1", "source": "sys-cloud-1", "target": "ctrl-bcap-1"},
                    {"id": "e2", "source": "ctrl-bcap-1", "target": "ctrl-ids-1"},
                    {"id": "e3", "source": "ctrl-bcap-1", "target": "ctrl-dlp-1"},
                ],
            }
        ),
    },
    # 5 — Identity Federation
    {
        "id": "snp-bdc-identity-federation",
        "name": "Identity Federation",
        "category": "identity",
        "description": "Internal system federated with external IdP via SAML with MFA.",
        "tags": json.dumps(["federation", "saml", "mfa", "identity"]),
        "graph_json": json.dumps(
            {
                "nodes": [
                    {"id": "sys-int-1", "type": "sys-internal", "label": "Internal System", "x": 50, "y": 80},
                    {"id": "sys-ext-idp", "type": "sys-external", "label": "External IdP", "x": 250, "y": 80},
                    {"id": "isa-fed-1", "type": "isa-federation", "label": "SAML Federation", "x": 150, "y": 80},
                    {"id": "ctrl-mfa-1", "type": "ctrl-mfa", "label": "MFA Gateway", "x": 150, "y": 170},
                ],
                "edges": [
                    {"id": "e1", "source": "sys-int-1", "target": "isa-fed-1"},
                    {"id": "e2", "source": "isa-fed-1", "target": "sys-ext-idp"},
                    {"id": "e3", "source": "ctrl-mfa-1", "target": "isa-fed-1"},
                ],
            }
        ),
    },
    # 6 — File Transfer ISA
    {
        "id": "snp-bdc-file-transfer",
        "name": "File Transfer ISA",
        "category": "basic",
        "description": "External system connected via SFTP ISA with DLP and ISA document.",
        "tags": json.dumps(["sftp", "file-transfer", "dlp", "isa"]),
        "graph_json": json.dumps(
            {
                "nodes": [
                    {"id": "sys-ext-1", "type": "sys-external", "label": "External System", "x": 250, "y": 50},
                    {"id": "isa-file-1", "type": "isa-file", "label": "SFTP ISA", "x": 150, "y": 50},
                    {"id": "ctrl-dlp-1", "type": "ctrl-dlp", "label": "DLP Gateway", "x": 50, "y": 50},
                    {"id": "doc-isa-1", "type": "doc-isa", "label": "ISA Agreement", "x": 150, "y": 150},
                ],
                "edges": [
                    {"id": "e1", "source": "ctrl-dlp-1", "target": "isa-file-1"},
                    {"id": "e2", "source": "isa-file-1", "target": "sys-ext-1"},
                    {"id": "e3", "source": "doc-isa-1", "target": "isa-file-1"},
                ],
            }
        ),
    },
    # 7 — DMZ Web Tier
    {
        "id": "snp-bdc-dmz-web-tier",
        "name": "DMZ Web Tier",
        "category": "basic",
        "description": "DMZ boundary with internal system, boundary firewall, and IDS.",
        "tags": json.dumps(["dmz", "web", "firewall", "ids"]),
        "graph_json": json.dumps(
            {
                "nodes": [
                    {
                        "id": "bnd-dmz-1",
                        "type": "bnd-dmz",
                        "label": "DMZ",
                        "x": 20,
                        "y": 20,
                        "width": 200,
                        "height": 120,
                        "contained_nodes": ["sys-web-1"],
                    },
                    {"id": "sys-web-1", "type": "sys-internal", "label": "Web Server", "x": 50, "y": 70},
                    {"id": "sys-app-1", "type": "sys-internal", "label": "App Server", "x": 300, "y": 70},
                    {"id": "ctrl-fw-1", "type": "ctrl-firewall", "label": "Firewall", "x": 300, "y": 170},
                    {"id": "ctrl-ids-1", "type": "ctrl-ids-ips", "label": "IDS/IPS", "x": 50, "y": 170},
                ],
                "edges": [
                    {"id": "e1", "source": "sys-web-1", "target": "ctrl-fw-1"},
                    {"id": "e2", "source": "ctrl-fw-1", "target": "sys-app-1"},
                    {"id": "e3", "source": "ctrl-ids-1", "target": "ctrl-fw-1"},
                    {"id": "e4", "source": "sys-web-1", "target": "bnd-dmz-1", "label": "resides in"},
                ],
            }
        ),
    },
    # 8 — PPS Enforcement
    {
        "id": "snp-bdc-pps-enforcement",
        "name": "PPS Enforcement",
        "category": "compliance",
        "description": "Firewall with PPS filter and PPS matrix documentation.",
        "tags": json.dumps(["pps", "firewall", "compliance", "ports-protocols"]),
        "graph_json": json.dumps(
            {
                "nodes": [
                    {"id": "ctrl-fw-1", "type": "ctrl-firewall", "label": "Boundary Firewall", "x": 50, "y": 80},
                    {"id": "ctrl-pps-1", "type": "ctrl-pps", "label": "PPS Filter", "x": 180, "y": 80},
                    {"id": "doc-pps-1", "type": "doc-pps-matrix", "label": "PPS Matrix Doc", "x": 180, "y": 170},
                ],
                "edges": [
                    {"id": "e1", "source": "ctrl-fw-1", "target": "ctrl-pps-1"},
                    {"id": "e2", "source": "doc-pps-1", "target": "ctrl-pps-1"},
                ],
            }
        ),
    },
    # 9 — SCCA 4-Zone Pattern
    {
        "id": "snp-bdc-scca-4zone",
        "name": "SCCA 4-Zone Pattern",
        "category": "scca",
        "description": "DoD SCCA 4-zone boundary pattern: ATO boundary containing BCAP, VDSS, VDMS, "
        "and TCCM enclave zones with resides-in edges.",
        "tags": json.dumps(["scca", "bcap", "vdss", "vdms", "tccm", "dod"]),
        "graph_json": json.dumps(
            {
                "nodes": [
                    {
                        "id": "bnd-ato-1",
                        "type": "bnd-ato",
                        "label": "SCCA ATO Boundary",
                        "x": 40,
                        "y": 20,
                        "width": 800,
                        "height": 300,
                        "contained_nodes": ["bnd-bcap", "bnd-vdss", "bnd-vdms", "bnd-tccm"],
                    },
                    {
                        "id": "bnd-bcap",
                        "type": "bnd-enclave",
                        "label": "BCAP Zone",
                        "x": 60,
                        "y": 80,
                        "width": 160,
                        "height": 120,
                    },
                    {
                        "id": "bnd-vdss",
                        "type": "bnd-enclave",
                        "label": "VDSS Zone",
                        "x": 260,
                        "y": 80,
                        "width": 160,
                        "height": 120,
                    },
                    {
                        "id": "bnd-vdms",
                        "type": "bnd-enclave",
                        "label": "VDMS Zone",
                        "x": 460,
                        "y": 80,
                        "width": 160,
                        "height": 120,
                    },
                    {
                        "id": "bnd-tccm",
                        "type": "bnd-enclave",
                        "label": "TCCM Zone",
                        "x": 660,
                        "y": 80,
                        "width": 160,
                        "height": 120,
                    },
                ],
                "edges": [
                    {"id": "e1", "source": "bnd-bcap", "target": "bnd-ato-1", "label": "resides in"},
                    {"id": "e2", "source": "bnd-vdss", "target": "bnd-ato-1", "label": "resides in"},
                    {"id": "e3", "source": "bnd-vdms", "target": "bnd-ato-1", "label": "resides in"},
                    {"id": "e4", "source": "bnd-tccm", "target": "bnd-ato-1", "label": "resides in"},
                ],
            }
        ),
    },
    # 10 — TIC 3.0 CAP
    {
        "id": "snp-bdc-tic3-cap",
        "name": "TIC 3.0 CAP",
        "category": "fedramp",
        "description": "TIC 3.0 Cloud Access Point pattern with agency firewall and VPN interconnection.",
        "tags": json.dumps(["tic3", "cap", "firewall", "vpn", "fedramp"]),
        "graph_json": json.dumps(
            {
                "nodes": [
                    {"id": "ctrl-cap-1", "type": "ctrl-cap", "label": "TIC 3.0 CAP", "x": 50, "y": 80},
                    {"id": "ctrl-fw-1", "type": "ctrl-firewall", "label": "Agency Firewall", "x": 200, "y": 80},
                    {"id": "isa-vpn-1", "type": "isa-vpn", "label": "VPN", "x": 350, "y": 80},
                ],
                "edges": [
                    {"id": "e1", "source": "ctrl-cap-1", "target": "ctrl-fw-1"},
                    {"id": "e2", "source": "ctrl-fw-1", "target": "isa-vpn-1"},
                ],
            }
        ),
    },
]

TEMPLATES = [
    {
        "id": "bdc-tpl-single-ato",
        "name": "Single System ATO Boundary",
        "category": "basic",
        "description": "One ATO boundary with DMZ, internal application server, external API partner, "
        "boundary firewall, IDS/IPS, SIEM, and ISA agreement. Ideal starting point for "
        "single-system authorization packages.",
        "graph_json": json.dumps(_tpl_single_system_ato()),
        "tags": json.dumps(["ato", "single-system", "basic", "api", "isa"]),
    },
    {
        "id": "bdc-tpl-multi-enclave-dod",
        "name": "Multi-Enclave DoD Program",
        "category": "dod",
        "description": "DoD program ATO boundary with CUI (IL5), SECRET (IL6), and TS/SCI enclaves. "
        "Includes cross-domain solutions between classification levels, BCAP for cloud "
        "egress, and GovCloud VPN interconnection.",
        "graph_json": json.dumps(_tpl_multi_enclave_dod()),
        "tags": json.dumps(["dod", "multi-enclave", "cross-domain", "il5", "il6", "bcap", "classification"]),
    },
    {
        "id": "bdc-tpl-fedramp-cloud",
        "name": "FedRAMP Cloud Authorization",
        "category": "fedramp",
        "description": "FedRAMP authorization boundary for a CSP with agency application, FedRAMP-authorized "
        "SaaS integrations (IdP, SIEM), TIC 3.0 CAP, SAML federation, and agency VPN ISA.",
        "graph_json": json.dumps(_tpl_fedramp_cloud()),
        "tags": json.dumps(["fedramp", "cloud", "csp", "tic3", "saml", "cap"]),
    },
    {
        "id": "bdc-tpl-healthcare-hipaa",
        "name": "Healthcare System (HIPAA)",
        "category": "healthcare",
        "description": "Healthcare ATO boundary with HIPAA PHI zone and PCI CDE, EHR system, billing, "
        "external lab (SFTP ISA) and insurance payer (API ISA). Includes DLP gateway "
        "for PHI data protection.",
        "graph_json": json.dumps(_tpl_healthcare_hipaa()),
        "tags": json.dumps(["hipaa", "healthcare", "pci", "phi", "dlp", "ehr"]),
    },
    {
        "id": "bdc-tpl-hybrid-multi-cloud",
        "name": "Hybrid Multi-Cloud Boundary",
        "category": "multi-cloud",
        "description": "Enterprise ATO boundary spanning AWS GovCloud and Azure Gov enclaves with on-prem "
        "data center. Includes Direct Connect VPN, ExpressRoute VPN, Azure AD federation, "
        "partner API ISA, and BCAP for cloud traffic.",
        "graph_json": json.dumps(_tpl_hybrid_multi_cloud()),
        "tags": json.dumps(["multi-cloud", "aws", "azure", "hybrid", "vpn", "bcap", "federation"]),
    },
    {
        "id": "bdc-tpl-scca-auth-boundary",
        "name": "SCCA Authorization Boundary",
        "category": "scca",
        "description": "DoD SCCA authorization boundary with all 4 SCCA functional areas: BCAP "
        "(Boundary Cloud Access Point), VDSS (Virtual Data Center Security Stack), "
        "VDMS (Virtual Data Center Managed Services), and TCCM (Tenant Cloud Credential "
        "Manager). Includes boundary firewall, IDS/IPS, SIEM, DISN circuit, and PPS matrix.",
        "graph_json": json.dumps(_tpl_scca_auth_boundary()),
        "tags": json.dumps(["scca", "dod", "bcap", "vdss", "vdms", "tccm", "ato"]),
    },
    {
        "id": "bdc-tpl-fedramp-agency-auth",
        "name": "FedRAMP Agency Authorization Boundary",
        "category": "fedramp",
        "description": "Agency consuming FedRAMP-authorized CSP with TIC 3.0 Cloud Access Point, "
        "PIV/CAC MFA, SAML federation, FedRAMP SaaS integrations (IdP, SIEM), "
        "agency VPN, and ISA documentation. Ideal for agency ATO packages.",
        "graph_json": json.dumps(_tpl_fedramp_agency_auth()),
        "tags": json.dumps(["fedramp", "tic3", "agency", "piv", "cac", "ato"]),
    },
    {
        "id": "bdc-tpl-lza-ou-structure",
        "name": "AWS LZA Multi-Account OU Structure",
        "category": "dod",
        "description": "AWS Landing Zone Accelerator organizational unit hierarchy showing Management, "
        "Security, Infrastructure, and Workload OUs with account boundaries, governance "
        "controls, and Transit Gateway connectivity.",
        "graph_json": json.dumps(_tpl_lza_ou_structure()),
        "tags": json.dumps(
            ["aws", "lza", "landing-zone", "ou", "multi-account", "control-tower", "organizations", "dod"]
        ),
    },
]


# ── Seed Runbooks ─────────────────────────────────────────────────────────────
BDC_SEED_RUNBOOKS = [
    {
        "id": "rb-boundary-breach",
        "title": "Boundary Breach Detected",
        "trigger_event": "boundary_breach",
        "severity": "critical",
        "description": (
            "Unauthorized traffic has been detected crossing an ATO boundary without a valid ISA. "
            "This runbook governs the initial triage, containment, and ISSO notification steps."
        ),
        "steps_json": json.dumps([
            {"order": 1, "action": "Identify source and destination of unauthorized traffic via SIEM query.", "owner": "SOC Analyst"},
            {"order": 2, "action": "Immediately isolate the affected interconnection at the boundary firewall.", "owner": "Network Engineer"},
            {"order": 3, "action": "Notify ISSO and ISSM within 1 hour per NIST IR-6 reporting requirement.", "owner": "SOC Lead"},
            {"order": 4, "action": "Capture packet logs and preserve evidence per AU-9 audit integrity.", "owner": "SOC Analyst"},
            {"order": 5, "action": "Open incident ticket and assign P1 severity in the ITSM system.", "owner": "ISSO"},
            {"order": 6, "action": "Perform root-cause analysis — determine if ISA was missing, expired, or misconfigured.", "owner": "Security Engineer"},
            {"order": 7, "action": "Draft SCAR (Security Corrective Action Report) and update POAM if required.", "owner": "ISSO"},
            {"order": 8, "action": "Restore connectivity only after ISSO approval and CA-3 authorization update.", "owner": "Change Manager"},
        ]),
        "owner": "ISSO",
        "classification": "CUI // SP-CTI",
    },
    {
        "id": "rb-isa-expiry",
        "title": "ISA Expiry Response",
        "trigger_event": "isa_expiry",
        "severity": "high",
        "description": (
            "An Interconnection Security Agreement (ISA) has expired or will expire within 30 days. "
            "This runbook governs renewal, emergency extension, or graceful termination procedures."
        ),
        "steps_json": json.dumps([
            {"order": 1, "action": "Identify all affected interconnections linked to the expiring ISA in the ISA Tracker.", "owner": "ISSO"},
            {"order": 2, "action": "Contact the interconnection owner and counterpart ISSO at the remote system.", "owner": "ISSO"},
            {"order": 3, "action": "Determine if renewal is feasible — initiate ISA renewal package (updated ATO letters, PPS matrix, MOU).", "owner": "Security Engineer"},
            {"order": 4, "action": "If renewal is not feasible within 7 days, request emergency extension via AO approval.", "owner": "ISSO"},
            {"order": 5, "action": "If no extension approved, schedule graceful termination — coordinate with operations on cutover.", "owner": "Change Manager"},
            {"order": 6, "action": "Update boundary design canvas to reflect ISA status change.", "owner": "ISSO"},
            {"order": 7, "action": "Verify SIEM alert rules are in place for post-termination traffic detection.", "owner": "SOC Analyst"},
        ]),
        "owner": "ISSO",
        "classification": "CUI // SP-CTI",
    },
    {
        "id": "rb-unauthorized-interconnection",
        "title": "Unauthorized Interconnection Found",
        "trigger_event": "unauthorized_interconnection",
        "severity": "critical",
        "description": (
            "A system-to-system connection has been discovered that lacks an approved ISA or ATO authorization. "
            "This is a CAT1 BDC-ISA-001 violation requiring immediate remediation."
        ),
        "steps_json": json.dumps([
            {"order": 1, "action": "Confirm the interconnection is unauthorized by checking ISA Tracker and boundary design.", "owner": "SOC Analyst"},
            {"order": 2, "action": "Block traffic at the perimeter firewall immediately — document the block rule.", "owner": "Network Engineer"},
            {"order": 3, "action": "Identify the system owner and business justification for the undocumented connection.", "owner": "ISSO"},
            {"order": 4, "action": "Assess data classification and potential data exfiltration risk.", "owner": "Security Engineer"},
            {"order": 5, "action": "If legitimate need exists, begin emergency ISA drafting process — escalate to AO for 72-hour interim approval.", "owner": "ISSO"},
            {"order": 6, "action": "If no legitimate need, permanently terminate the connection and document in POAM.", "owner": "ISSO"},
            {"order": 7, "action": "Update boundary design canvas and run BDC compliance assessment to verify CAT1 is cleared.", "owner": "Security Engineer"},
            {"order": 8, "action": "Submit incident report to leadership and update supply chain risk register if external system involved.", "owner": "ISSO"},
        ]),
        "owner": "ISSO",
        "classification": "CUI // SP-CTI",
    },
    {
        "id": "rb-pps-violation",
        "title": "PPS Matrix Violation",
        "trigger_event": "pps_violation",
        "severity": "high",
        "description": (
            "Traffic has been detected on a port, protocol, or service not listed in the approved "
            "PPS matrix for an active ISA. This is a BDC-CTL-004 violation (CM-7 non-compliance)."
        ),
        "steps_json": json.dumps([
            {"order": 1, "action": "Query firewall and SIEM logs to identify the specific port/protocol/service in violation.", "owner": "SOC Analyst"},
            {"order": 2, "action": "Cross-reference against the approved PPS matrix for the affected ISA.", "owner": "Security Engineer"},
            {"order": 3, "action": "Block the unauthorized port/protocol immediately and create a change record.", "owner": "Network Engineer"},
            {"order": 4, "action": "Determine if the traffic was malicious (exploit attempt) or misconfiguration.", "owner": "SOC Lead"},
            {"order": 5, "action": "If misconfiguration: update PPS matrix in ISA and obtain AO approval for the change.", "owner": "ISSO"},
            {"order": 6, "action": "If malicious: escalate to incident response and initiate boundary breach runbook.", "owner": "SOC Lead"},
            {"order": 7, "action": "Regenerate PPS matrix in BDC and re-run compliance assessment to verify BDC-CTL-004 is cleared.", "owner": "Security Engineer"},
        ]),
        "owner": "Network Engineer",
        "classification": "CUI // SP-CTI",
    },
]


def init_db():
    """Initialize the boundary_canvas database schema and seed templates."""
    conn = get_connection()
    try:
        if _BDC_BACKEND == "postgresql":
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
                    CREATE OR REPLACE FUNCTION bd_audit_immutable()
                    RETURNS TRIGGER AS $$
                    BEGIN
                        RAISE EXCEPTION 'Audit records are immutable — NIST AU-6';
                    END;
                    $$ LANGUAGE plpgsql
                """)
                conn.execute("""
                    DROP TRIGGER IF EXISTS bd_audit_no_update ON bd_audit
                """)
                conn.execute("""
                    CREATE TRIGGER bd_audit_no_update
                    BEFORE UPDATE ON bd_audit
                    FOR EACH ROW EXECUTE FUNCTION bd_audit_immutable()
                """)
                conn.execute("""
                    DROP TRIGGER IF EXISTS bd_audit_no_delete ON bd_audit
                """)
                conn.execute("""
                    CREATE TRIGGER bd_audit_no_delete
                    BEFORE DELETE ON bd_audit
                    FOR EACH ROW EXECUTE FUNCTION bd_audit_immutable()
                """)
                conn.commit()
            except Exception:
                pass
            print("[init_db] BDC schema created (PostgreSQL)", file=sys.stderr)
        else:
            # SQLite: executescript for all-at-once
            conn.executescript(SCHEMA)
            # SQLite audit immutability triggers
            try:
                conn.executescript("""
                    CREATE TRIGGER IF NOT EXISTS bd_audit_no_update
                    BEFORE UPDATE ON bd_audit
                    BEGIN
                        SELECT RAISE(ABORT, 'Audit records are immutable');
                    END;
                    CREATE TRIGGER IF NOT EXISTS bd_audit_no_delete
                    BEFORE DELETE ON bd_audit
                    BEGIN
                        SELECT RAISE(ABORT, 'Audit records cannot be deleted');
                    END;
                """)
            except Exception:
                pass
            conn.commit()
            print(f"[init_db] BDC schema created at {DB_PATH}", file=sys.stderr)

        # Seed templates (upsert — inserts new templates even if some already exist)
        cur = conn.cursor()
        cur.execute("SELECT COUNT(*) FROM bd_templates")
        count = cur.fetchone()[0]
        added = 0
        for t in TEMPLATES:
            cur.execute("SELECT 1 FROM bd_templates WHERE id=%s", (t["id"],))
            if not cur.fetchone():
                conn.execute(
                    "INSERT INTO bd_templates (id, name, category, description, graph_json, tags) VALUES (%s,%s,%s,%s,%s,%s)",
                    (t["id"], t["name"], t["category"], t["description"], t["graph_json"], t["tags"]),
                )
                added += 1
        if added:
            conn.commit()
            print(f"[init_db] BDC seeded {added} new templates (total: {count + added}).", file=sys.stderr)
        else:
            print(f"[init_db] BDC all {count} templates up to date.", file=sys.stderr)

        # Seed snippets (upsert)
        cur.execute("SELECT COUNT(*) FROM bd_snippets")
        snp_count = cur.fetchone()[0]
        snp_added = 0
        for s in BDC_SNIPPETS:
            cur.execute("SELECT 1 FROM bd_snippets WHERE id=%s", (s["id"],))
            if not cur.fetchone():
                conn.execute(
                    "INSERT INTO bd_snippets (id, name, category, description, graph_json, tags) VALUES (%s,%s,%s,%s,%s,%s)",
                    (s["id"], s["name"], s["category"], s["description"], s["graph_json"], s["tags"]),
                )
                snp_added += 1
        if snp_added:
            conn.commit()
            print(f"[init_db] BDC seeded {snp_added} new snippets (total: {snp_count + snp_added}).", file=sys.stderr)
        else:
            print(f"[init_db] BDC all {snp_count} snippets up to date.", file=sys.stderr)

        # ── Seed runbooks ──────────────────────────────────────────────────
        cur.execute("SELECT COUNT(*) FROM bdc_runbooks")
        rb_count = cur.fetchone()[0]
        rb_added = 0
        for rb in BDC_SEED_RUNBOOKS:
            cur.execute("SELECT 1 FROM bdc_runbooks WHERE id=%s", (rb["id"],))
            if not cur.fetchone():
                conn.execute(
                    "INSERT INTO bdc_runbooks "
                    "(id, title, trigger_event, severity, description, steps_json, owner, classification) "
                    "VALUES (%s,%s,%s,%s,%s,%s,%s,%s)",
                    (
                        rb["id"],
                        rb["title"],
                        rb["trigger_event"],
                        rb["severity"],
                        rb["description"],
                        rb["steps_json"],
                        rb["owner"],
                        rb["classification"],
                    ),
                )
                rb_added += 1
        if rb_added:
            conn.commit()
            print(f"[init_db] BDC seeded {rb_added} new runbooks (total: {rb_count + rb_added}).", file=sys.stderr)
        else:
            print(f"[init_db] BDC all {rb_count} runbooks up to date.", file=sys.stderr)

        # ── Seed SOPs ──────────────────────────────────────────────────────
        try:
            from tools.boundary_canvas.sops import seed_sops
            seed_sops()
            print("[init_db] BDC SOPs seeded.", file=sys.stderr)
        except Exception as _e:
            print(f"[init_db] BDC SOP seed skipped: {_e}", file=sys.stderr)

    finally:
        conn.close()


if __name__ == "__main__":
    init_db()
