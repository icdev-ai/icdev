# [CUI // SP-CTI]
"""
Security Design Canvas — DB initializer
Creates schema and seeds 8 canonical security design templates.

Dual-backend: SQLite (default) or PostgreSQL.
Set SC_STORAGE_BACKEND=postgresql + SC_PG_* env vars to use PostgreSQL.
SQLite is the default for dev, air-gap, and single-user deployments.
PostgreSQL is recommended for production multi-user/global deployments.
"""

import json
import os
import sqlite3
import sys
import uuid
from pathlib import Path

# When integrated into ICDEV, DB lives in data/ directory
_ICDEV_ROOT = Path(__file__).resolve().parents[3]  # tools/security_canvas/db -> ICDev root
DB_PATH = _ICDEV_ROOT / "data" / "security_canvas.db"

# Backend detection — SC_STORAGE_BACKEND only (NOT inherited from ICDEV_STORAGE_BACKEND)
# SDC has its own DB (security_canvas.db). Set SC_STORAGE_BACKEND=postgresql to use PG.
_SC_BACKEND = os.environ.get("SC_STORAGE_BACKEND", os.environ.get("ICDEV_CANVAS_STORAGE_BACKEND", os.environ.get("ICDEV_STORAGE_BACKEND", "postgresql"))).lower()


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
    if _SC_BACKEND == "postgresql":
        try:
            from tools.db.storage import get_canvas_connection
            # Canvas tables (security_designs, sc_assessments, zig_*) have no
            # classification/tenant_id columns — must bypass RLS via get_canvas_connection.
            return get_canvas_connection("SC_STORAGE_BACKEND")
        except ImportError:
            pass  # Fall through to SQLite
    # SQLite (default) — per-canvas DB, distinct from icdev.db
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    try:
        from tools.db.storage import StorageConnection

        return StorageConnection(conn, "sqlite")  # so NC-style %s placeholders translate
    except ImportError:
        return conn


SCHEMA = """
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

-- ── NSA ZIG (Zero Trust Implementation Guide) Tables ────────────────────────

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
    target_id       TEXT NOT NULL DEFAULT 'icdev-self',
    score           REAL NOT NULL DEFAULT 0.0,
    maturity_level  TEXT,
    capability_count INTEGER DEFAULT 0,
    activity_count  INTEGER DEFAULT 0,
    complete_activities INTEGER DEFAULT 0,
    assessment_run_at TEXT DEFAULT CURRENT_TIMESTAMP,
    created_at      TEXT DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_zig_score_pillar ON zig_maturity_scores(pillar_slug);
CREATE INDEX IF NOT EXISTS idx_zig_score_target ON zig_maturity_scores(target_id);

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
"""

# ── Template seeds ────────────────────────────────────────────────────────────


def _node(nid, label, ntype, x, y, extra=None):
    """Create a node dict for graph_json."""
    n = {"id": nid, "type": ntype, "label": label, "x": x, "y": y}
    if extra:
        n.update(extra)
    return n


def _edge(src, dst, label="", protocol="", encrypted=False, authenticated=False):
    """Create an edge dict for graph_json."""
    return {
        "id": str(uuid.uuid4())[:8],
        "source": src,
        "target": dst,
        "label": label,
        "protocol": protocol,
        "encrypted": encrypted,
        "authenticated": authenticated,
    }


def _boundary(bid, label, btype, classification, il_level, color, x, y, w, h, assets):
    """Create a trust boundary dict for graph_json."""
    return {
        "id": bid,
        "label": label,
        "boundary_type": btype,
        "classification": classification,
        "il_level": il_level,
        "color": color,
        "x": x,
        "y": y,
        "width": w,
        "height": h,
        "contained_assets": assets,
    }


def _threat(tid, category, title, affected):
    """Create a threat dict for template graph_json."""
    return {
        "id": tid,
        "category": category,
        "title": title,
        "affected_assets": affected,
    }


TEMPLATES = [
    # 1 — Web Application 3-Tier
    {
        "id": "sct-web-3tier",
        "name": "Web Application 3-Tier",
        "category": "Application Security",
        "description": "Classic 3-tier web application with DMZ, app zone, and data zone. "
        "Includes WAF, IdP, and SIEM integration.",
        "tags": json.dumps(["web", "3-tier", "waf", "dmz", "stride"]),
        "graph_json": json.dumps(
            {
                "nodes": [
                    _node("user", "User", "asset-client", 50, 200),
                    _node("waf", "WAF / Firewall", "ctrl-firewall", 200, 200),
                    _node("web", "Web Server", "asset-server", 400, 200),
                    _node("app", "App Server", "asset-server", 600, 200),
                    _node("db", "Database", "asset-database", 800, 200),
                    _node("siem", "SIEM", "ctrl-siem", 500, 50),
                    _node("idp", "IdP / SSO", "ctrl-idp", 300, 50),
                    _node("inet", "Internet", "boundary-internet", 50, 50),
                ],
                "edges": [
                    _edge("user", "waf", "HTTPS", "TLS 1.3", True, True),
                    _edge("waf", "web", "HTTP", "HTTP/2", False, True),
                    _edge("web", "app", "API", "gRPC", True, True),
                    _edge("app", "db", "SQL", "TLS", True, True),
                    _edge("web", "siem", "Logs", "Syslog"),
                    _edge("app", "siem", "Logs", "Syslog"),
                    _edge("db", "siem", "Audit", "Syslog"),
                    _edge("web", "idp", "AuthN", "SAML"),
                ],
                "boundaries": [
                    _boundary("bnd-dmz", "DMZ", "network", "CUI", "IL4", "#ff6b6b", 150, 150, 300, 150, ["waf", "web"]),
                    _boundary("bnd-app", "App Zone", "network", "CUI", "IL4", "#4ecdc4", 550, 150, 150, 150, ["app"]),
                    _boundary("bnd-data", "Data Zone", "network", "CUI", "IL5", "#45b7d1", 750, 150, 150, 150, ["db"]),
                ],
                "threats": [
                    _threat("thr-spoof", "Spoofing", "User identity spoofing", ["user"]),
                    _threat("thr-sqli", "Tampering", "SQL injection on database", ["db"]),
                    _threat("thr-xss", "Tampering", "Cross-site scripting on web server", ["web"]),
                ],
            }
        ),
    },
    # 2 — Zero Trust Microservices
    {
        "id": "sct-zt-micro",
        "name": "Zero Trust Microservices",
        "category": "Cloud Native",
        "description": "Microservices architecture with service mesh (Istio), mTLS everywhere, "
        "API gateway, and no implicit trust between services.",
        "tags": json.dumps(["zero-trust", "microservices", "istio", "mtls", "cloud-native"]),
        "graph_json": json.dumps(
            {
                "nodes": [
                    _node("apigw", "API Gateway", "ctrl-gateway", 100, 200),
                    _node("svc-a", "Service A", "asset-server", 350, 150),
                    _node("svc-b", "Service B", "asset-server", 350, 300),
                    _node("mesh", "Service Mesh (Istio)", "ctrl-mesh", 550, 200),
                    _node("mtls", "mTLS Control", "ctrl-encryption", 550, 50),
                    _node("siem", "SIEM", "ctrl-siem", 100, 50),
                    _node("kms", "KMS", "ctrl-kms", 350, 50),
                ],
                "edges": [
                    _edge("apigw", "svc-a", "API", "mTLS", True, True),
                    _edge("apigw", "svc-b", "API", "mTLS", True, True),
                    _edge("svc-a", "svc-b", "gRPC", "mTLS", True, True),
                    _edge("svc-a", "mesh", "Sidecar", "Envoy"),
                    _edge("svc-b", "mesh", "Sidecar", "Envoy"),
                    _edge("mesh", "mtls", "Cert Mgmt", ""),
                    _edge("mesh", "siem", "Telemetry", "OTLP"),
                ],
                "boundaries": [],
                "threats": [],
            }
        ),
    },
    # 3 — FedRAMP Moderate Boundary
    {
        "id": "sct-fedramp-mod",
        "name": "FedRAMP Moderate Boundary",
        "category": "Compliance",
        "description": "FedRAMP Moderate authorization boundary with NIST 800-53 control badges. "
        "Includes WAF, ALB, IdP/MFA, KMS, PAM, CSPM, and vulnerability scanner.",
        "tags": json.dumps(["fedramp", "nist-800-53", "moderate", "ato", "compliance"]),
        "graph_json": json.dumps(
            {
                "nodes": [
                    _node("inet", "Internet", "boundary-internet", 50, 200),
                    _node("waf", "WAF", "ctrl-firewall", 200, 200),
                    _node("alb", "ALB", "asset-server", 350, 200),
                    _node("app", "Application", "asset-server", 500, 200),
                    _node("db", "Database", "asset-database", 650, 200),
                    _node("siem", "SIEM", "ctrl-siem", 350, 50),
                    _node("idp", "IdP / MFA", "ctrl-idp", 500, 50),
                    _node("kms", "KMS", "ctrl-kms", 650, 50),
                    _node("pam", "PAM", "ctrl-pam", 200, 50),
                    _node("cspm", "CSPM", "ctrl-cspm", 800, 50),
                    _node("scanner", "Vuln Scanner", "ctrl-scanner", 800, 200),
                ],
                "edges": [
                    _edge("inet", "waf", "Ingress", "TLS 1.2+", True, False),
                    _edge("waf", "alb", "Filtered", "HTTP/2", False, True),
                    _edge("alb", "app", "Forward", "HTTP", False, True),
                    _edge("app", "db", "SQL", "TLS", True, True),
                    _edge("app", "idp", "AuthN", "OIDC", True, True),
                    _edge("app", "kms", "Encrypt", "TLS", True, True),
                    _edge("app", "siem", "Logs", "Syslog"),
                    _edge("scanner", "app", "Scan", "HTTPS", True, True),
                    _edge("pam", "app", "Admin", "SSH", True, True),
                ],
                "boundaries": [
                    _boundary(
                        "bnd-auth",
                        "Authorization Boundary",
                        "authorization",
                        "CUI",
                        "IL4",
                        "#e94560",
                        150,
                        130,
                        700,
                        150,
                        ["waf", "alb", "app", "db", "scanner"],
                    ),
                ],
                "threats": [],
            }
        ),
    },
    # 4 — DoD IL5 Enclave
    {
        "id": "sct-dod-il5",
        "name": "DoD IL5 Enclave",
        "category": "DoD / SCCA",
        "description": "DoD IL5 enclave following SCCA (Secure Cloud Computing Architecture). "
        "Includes BCAP, VDSS, VDMS, and Mission zones with Type 1 encryption.",
        "tags": json.dumps(["dod", "il5", "scca", "bcap", "vdss", "vdms", "type1"]),
        "graph_json": json.dumps(
            {
                "nodes": [
                    _node("bcap", "BCAP", "boundary-bcap", 50, 200),
                    _node("vdss-fw", "VDSS Firewall", "ctrl-firewall", 200, 150),
                    _node("vdss-ids", "VDSS IDS/IPS", "ctrl-ids", 200, 300),
                    _node("vdms-siem", "VDMS SIEM", "ctrl-siem", 400, 100),
                    _node("vdms-pam", "VDMS PAM", "ctrl-pam", 400, 250),
                    _node("mission-app", "Mission App", "asset-server", 600, 200),
                    _node("mission-db", "Mission DB", "asset-database", 750, 200),
                    _node("encryptor", "Type 1 Encryptor", "ctrl-encryption", 50, 350),
                    _node("kms", "KMS (HSM)", "ctrl-kms", 600, 50),
                ],
                "edges": [
                    _edge("bcap", "vdss-fw", "Filtered", "IPSec", True, True),
                    _edge("vdss-fw", "vdss-ids", "Inspect", ""),
                    _edge("vdss-ids", "mission-app", "Allowed", "TLS", True, True),
                    _edge("mission-app", "mission-db", "Data", "TLS", True, True),
                    _edge("mission-app", "vdms-siem", "Logs", "Syslog"),
                    _edge("vdms-pam", "mission-app", "Admin", "SSH", True, True),
                    _edge("mission-app", "kms", "Keys", "PKCS#11", True, True),
                    _edge("bcap", "encryptor", "WAN", "Type 1", True, True),
                ],
                "boundaries": [
                    _boundary(
                        "bnd-vdss",
                        "VDSS Zone",
                        "network",
                        "CUI",
                        "IL5",
                        "#ff6b6b",
                        150,
                        100,
                        150,
                        280,
                        ["vdss-fw", "vdss-ids"],
                    ),
                    _boundary(
                        "bnd-vdms",
                        "VDMS Zone",
                        "network",
                        "CUI",
                        "IL5",
                        "#4ecdc4",
                        350,
                        60,
                        150,
                        250,
                        ["vdms-siem", "vdms-pam"],
                    ),
                    _boundary(
                        "bnd-mission",
                        "Mission Zone",
                        "network",
                        "CUI",
                        "IL5",
                        "#45b7d1",
                        550,
                        130,
                        280,
                        150,
                        ["mission-app", "mission-db"],
                    ),
                ],
                "threats": [],
            }
        ),
    },
    # 5 — API Gateway Pattern
    {
        "id": "sct-api-gw",
        "name": "API Gateway Pattern",
        "category": "Application Security",
        "description": "API-first architecture with gateway, WAF, rate limiting, OAuth/IdP, and backend service pool.",
        "tags": json.dumps(["api", "gateway", "oauth", "rate-limiting", "waf"]),
        "graph_json": json.dumps(
            {
                "nodes": [
                    _node("client", "Client", "asset-client", 50, 200),
                    _node("waf", "WAF", "ctrl-firewall", 200, 200),
                    _node("apigw", "API Gateway", "ctrl-gateway", 400, 200),
                    _node("rate", "Rate Limiter", "ctrl-ratelimit", 400, 50),
                    _node("idp", "OAuth / IdP", "ctrl-idp", 250, 50),
                    _node("svc", "Backend Services", "asset-server", 600, 200),
                    _node("db", "Database", "asset-database", 750, 200),
                ],
                "edges": [
                    _edge("client", "waf", "HTTPS", "TLS 1.3", True, False),
                    _edge("waf", "apigw", "Filtered", "HTTP/2", False, True),
                    _edge("apigw", "svc", "Route", "gRPC", True, True),
                    _edge("svc", "db", "Query", "TLS", True, True),
                    _edge("apigw", "rate", "Throttle", ""),
                    _edge("apigw", "idp", "Token Verify", "OAuth2", True, True),
                ],
                "boundaries": [
                    _boundary(
                        "bnd-edge",
                        "Edge Zone",
                        "network",
                        "CUI",
                        "IL4",
                        "#ff6b6b",
                        150,
                        150,
                        320,
                        130,
                        ["waf", "apigw"],
                    ),
                    _boundary(
                        "bnd-backend",
                        "Backend Zone",
                        "network",
                        "CUI",
                        "IL4",
                        "#4ecdc4",
                        550,
                        150,
                        260,
                        130,
                        ["svc", "db"],
                    ),
                ],
                "threats": [],
            }
        ),
    },
    # 6 — Container Platform (Big Bang)
    {
        "id": "sct-container-bb",
        "name": "Container Platform (Big Bang)",
        "category": "Cloud Native",
        "description": "DoD Big Bang container platform with secure registry, admission control "
        "(Kyverno), Istio service mesh, and Twistlock runtime security.",
        "tags": json.dumps(["k8s", "big-bang", "istio", "kyverno", "twistlock", "containers"]),
        "graph_json": json.dumps(
            {
                "nodes": [
                    _node("registry", "Container Registry", "asset-registry", 50, 200),
                    _node("admission", "Admission Control (Kyverno)", "ctrl-admission", 250, 200),
                    _node("k8s", "K8s Cluster", "asset-server", 450, 200),
                    _node("istio", "Istio Mesh", "ctrl-mesh", 450, 50),
                    _node("twistlock", "Twistlock", "ctrl-scanner", 650, 200),
                    _node("siem", "SIEM", "ctrl-siem", 650, 50),
                ],
                "edges": [
                    _edge("registry", "admission", "Pull", "TLS", True, True),
                    _edge("admission", "k8s", "Admit", "K8s API", True, True),
                    _edge("k8s", "istio", "Sidecar", "mTLS", True, True),
                    _edge("k8s", "twistlock", "Runtime", "Agent"),
                    _edge("twistlock", "siem", "Alerts", "Syslog"),
                    _edge("istio", "siem", "Telemetry", "OTLP"),
                ],
                "boundaries": [
                    _boundary(
                        "bnd-cluster",
                        "K8s Cluster",
                        "platform",
                        "CUI",
                        "IL4",
                        "#4ecdc4",
                        200,
                        130,
                        520,
                        150,
                        ["admission", "k8s", "twistlock"],
                    ),
                ],
                "threats": [],
            }
        ),
    },
    # 7 — Data Lake Security
    {
        "id": "sct-data-lake",
        "name": "Data Lake Security",
        "category": "Data Protection",
        "description": "Secure data lake architecture with encryption at rest (KMS), DLP scanning, "
        "access logging, and analytics layer.",
        "tags": json.dumps(["data-lake", "s3", "kms", "dlp", "encryption-at-rest"]),
        "graph_json": json.dumps(
            {
                "nodes": [
                    _node("source", "Data Source", "asset-client", 50, 200),
                    _node("ingest", "Ingestion", "asset-server", 200, 200),
                    _node("storage", "Storage (S3)", "asset-storage", 400, 200),
                    _node("kms", "KMS", "ctrl-kms", 400, 50),
                    _node("dlp", "DLP Scanner", "ctrl-dlp", 550, 50),
                    _node("logger", "Access Logger", "ctrl-siem", 200, 50),
                    _node("analytics", "Analytics", "asset-server", 600, 200),
                ],
                "edges": [
                    _edge("source", "ingest", "Raw Data", "TLS", True, False),
                    _edge("ingest", "storage", "Write", "HTTPS", True, True),
                    _edge("storage", "kms", "Encrypt/Decrypt", "PKCS#11", True, True),
                    _edge("storage", "dlp", "Scan", "API", True, True),
                    _edge("storage", "analytics", "Read", "HTTPS", True, True),
                    _edge("ingest", "logger", "Access Log", "Syslog"),
                    _edge("storage", "logger", "Access Log", "Syslog"),
                ],
                "boundaries": [
                    _boundary(
                        "bnd-lake",
                        "Data Lake",
                        "data",
                        "CUI",
                        "IL4",
                        "#45b7d1",
                        150,
                        130,
                        520,
                        150,
                        ["ingest", "storage", "analytics"],
                    ),
                ],
                "threats": [],
            }
        ),
    },
    # 8 — Hybrid Cloud Multi-CSP
    {
        "id": "sct-hybrid-multi",
        "name": "Hybrid Cloud Multi-CSP",
        "category": "Multi-Cloud",
        "description": "Hybrid cloud architecture spanning on-prem data center and two CSPs "
        "with federated identity, unified SIEM, and per-cloud KMS.",
        "tags": json.dumps(["hybrid", "multi-cloud", "federation", "transit", "siem"]),
        "graph_json": json.dumps(
            {
                "nodes": [
                    _node("onprem", "On-Prem DC", "asset-server", 50, 200),
                    _node("cloud-a", "Cloud A (AWS)", "boundary-cloud", 300, 150),
                    _node("cloud-b", "Cloud B (Azure)", "boundary-cloud", 300, 300),
                    _node("transit", "Transit / Interconnect", "asset-network", 150, 250),
                    _node("idp-fed", "IdP Federation", "ctrl-idp", 500, 50),
                    _node("siem", "Unified SIEM", "ctrl-siem", 500, 200),
                    _node("kms-a", "KMS (AWS)", "ctrl-kms", 500, 150),
                    _node("kms-b", "KMS (Azure)", "ctrl-kms", 500, 300),
                ],
                "edges": [
                    _edge("onprem", "transit", "WAN", "IPSec", True, True),
                    _edge("transit", "cloud-a", "DX / ER", "IPSec", True, True),
                    _edge("transit", "cloud-b", "ER", "IPSec", True, True),
                    _edge("cloud-a", "kms-a", "Keys", "TLS", True, True),
                    _edge("cloud-b", "kms-b", "Keys", "TLS", True, True),
                    _edge("cloud-a", "siem", "Logs", "OTLP"),
                    _edge("cloud-b", "siem", "Logs", "OTLP"),
                    _edge("idp-fed", "cloud-a", "SAML", "TLS", True, True),
                    _edge("idp-fed", "cloud-b", "SAML", "TLS", True, True),
                ],
                "boundaries": [
                    _boundary(
                        "bnd-onprem",
                        "On-Premises",
                        "network",
                        "CUI",
                        "IL4",
                        "#ff6b6b",
                        20,
                        150,
                        170,
                        180,
                        ["onprem", "transit"],
                    ),
                    _boundary(
                        "bnd-cloud-a", "AWS GovCloud", "cloud", "CUI", "IL4", "#f0ad4e", 260, 100, 100, 100, ["cloud-a"]
                    ),
                    _boundary(
                        "bnd-cloud-b", "Azure Gov", "cloud", "CUI", "IL4", "#5bc0de", 260, 260, 100, 100, ["cloud-b"]
                    ),
                ],
                "threats": [],
            }
        ),
    },
    # 9 — IoT/OT Security Architecture
    {
        "id": "sct-iot-ot",
        "name": "IoT/OT Security Architecture",
        "category": "Industrial",
        "description": "Industrial IoT/OT security with Purdue model zones (L0-L5), "
        "protocol gateways, data diodes, and SCADA monitoring.",
        "tags": json.dumps(["iot", "ot", "scada", "purdue-model", "data-diode", "industrial"]),
        "graph_json": json.dumps(
            {
                "nodes": [
                    _node("field", "Field Device", "asset-iot", 50, 300),
                    _node("controller", "OT Controller", "asset-server", 200, 300),
                    _node("hmi", "SCADA HMI", "asset-server", 400, 300),
                    _node("historian", "Historian", "asset-database", 600, 300),
                    _node("diode", "Data Diode", "ctrl-firewall", 600, 150),
                    _node("ot-siem", "OT-SIEM", "ctrl-siem", 800, 150),
                    _node("gateway", "Protocol Gateway", "asset-server", 200, 150),
                ],
                "edges": [
                    _edge("field", "controller", "Modbus/OPC", "Modbus", False, False),
                    _edge("controller", "hmi", "HMI Data", "OPC-UA", False, False),
                    _edge("hmi", "historian", "Process Data", "OPC-UA", False, False),
                    _edge("historian", "diode", "Export", "TLS", True, False),
                    _edge("diode", "ot-siem", "One-Way", "TLS", True, False),
                ],
                "boundaries": [
                    _boundary(
                        "bnd-ot",
                        "OT Zone",
                        "enclave",
                        "CUI",
                        "IL4",
                        "#ff6b6b",
                        30,
                        250,
                        640,
                        120,
                        ["field", "controller", "hmi", "historian"],
                    ),
                    _boundary("bnd-dmz-ot", "IT-OT DMZ", "dmz", "CUI", "IL4", "#f0ad4e", 560, 100, 140, 120, ["diode"]),
                ],
                "threats": [],
            }
        ),
    },
    # 10 — Serverless Security Pattern
    {
        "id": "sct-serverless",
        "name": "Serverless Security Pattern",
        "category": "Cloud",
        "description": "Serverless function security with API gateway, WAF, function isolation, "
        "secrets management, and event-driven monitoring.",
        "tags": json.dumps(["serverless", "lambda", "api-gateway", "waf", "cloud"]),
        "graph_json": json.dumps(
            {
                "nodes": [
                    _node("waf", "WAF", "ctrl-firewall", 50, 200),
                    _node("apigw", "API Gateway", "asset-server", 200, 200),
                    _node("lambda", "Lambda Function", "asset-lambda", 400, 200),
                    _node("dynamo", "DynamoDB", "asset-database", 600, 200),
                    _node("s3", "S3 Bucket", "asset-storage", 600, 50),
                    _node("secrets", "Secrets Manager", "ctrl-kms", 400, 50),
                    _node("cw", "CloudWatch", "ctrl-siem", 200, 50),
                ],
                "edges": [
                    _edge("waf", "apigw", "Filtered", "HTTPS", True, True),
                    _edge("apigw", "lambda", "Invoke", "HTTPS", True, True),
                    _edge("lambda", "dynamo", "Query", "TLS", True, True),
                    _edge("lambda", "s3", "Read/Write", "TLS", True, True),
                    _edge("lambda", "secrets", "Get Secret", "TLS", True, True),
                    _edge("lambda", "cw", "Logs/Metrics", "HTTPS", True, False),
                ],
                "boundaries": [
                    _boundary(
                        "bnd-pub",
                        "Public API",
                        "internet",
                        "CUI",
                        "IL4",
                        "#ff6b6b",
                        20,
                        150,
                        250,
                        120,
                        ["waf", "apigw"],
                    ),
                    _boundary(
                        "bnd-svpc",
                        "Serverless VPC",
                        "cloud",
                        "CUI",
                        "IL4",
                        "#4ecdc4",
                        350,
                        130,
                        310,
                        150,
                        ["lambda", "dynamo", "s3"],
                    ),
                ],
                "threats": [],
            }
        ),
    },
    # 11 — Kubernetes Security Baseline
    {
        "id": "sct-k8s-security",
        "name": "Kubernetes Security Baseline",
        "category": "Container",
        "description": "Kubernetes cluster security with RBAC, network policies, pod security "
        "standards, admission control, and runtime protection.",
        "tags": json.dumps(["kubernetes", "k8s", "rbac", "admission", "falco", "runtime"]),
        "graph_json": json.dumps(
            {
                "nodes": [
                    _node("ingress", "Ingress Controller", "asset-server", 50, 200),
                    _node("apiserver", "API Server", "asset-server", 250, 200),
                    _node("etcd", "etcd", "asset-database", 250, 50),
                    _node("worker", "Worker Node", "asset-container", 450, 200),
                    _node("registry", "Registry", "asset-registry", 450, 50),
                    _node("admission", "Admission Controller", "ctrl-scanner", 250, 350),
                    _node("falco", "Falco Runtime", "ctrl-ids", 650, 200),
                    _node("vault", "Vault", "ctrl-kms", 650, 50),
                ],
                "edges": [
                    _edge("ingress", "apiserver", "Route", "TLS", True, True),
                    _edge("apiserver", "etcd", "State", "mTLS", True, True),
                    _edge("apiserver", "worker", "Schedule", "TLS", True, True),
                    _edge("apiserver", "admission", "Webhook", "TLS", True, True),
                    _edge("worker", "registry", "Pull", "TLS", True, True),
                    _edge("worker", "falco", "Syscalls", "Agent"),
                    _edge("worker", "vault", "Secrets", "TLS", True, True),
                ],
                "boundaries": [
                    _boundary(
                        "bnd-cluster",
                        "Cluster Boundary",
                        "enclave",
                        "CUI",
                        "IL4",
                        "#4ecdc4",
                        200,
                        130,
                        520,
                        150,
                        ["apiserver", "worker", "etcd"],
                    ),
                    _boundary(
                        "bnd-ns",
                        "Namespace Isolation",
                        "network",
                        "CUI",
                        "IL4",
                        "#45b7d1",
                        400,
                        160,
                        130,
                        100,
                        ["worker"],
                    ),
                ],
                "threats": [],
            }
        ),
    },
    # 12 — HIPAA Healthcare Security
    {
        "id": "sct-hipaa",
        "name": "HIPAA Healthcare Security",
        "category": "Compliance",
        "description": "HIPAA-compliant architecture with PHI encryption, audit logging, "
        "BAA boundaries, and minimum necessary access.",
        "tags": json.dumps(["hipaa", "phi", "healthcare", "baa", "compliance", "dlp"]),
        "graph_json": json.dumps(
            {
                "nodes": [
                    _node("portal", "Patient Portal", "asset-webapp", 50, 200),
                    _node("ehr", "EHR App", "asset-server", 250, 200),
                    _node("phi-db", "PHI Database", "asset-database", 450, 200),
                    _node("mfa", "MFA", "ctrl-idp", 250, 50),
                    _node("audit", "Audit Logger", "ctrl-siem", 450, 50),
                    _node("encrypt", "Encryption", "ctrl-encryption", 650, 200),
                    _node("dlp", "DLP", "ctrl-dlp", 650, 50),
                    _node("baa-gw", "BAA Gateway", "asset-server", 50, 50),
                ],
                "edges": [
                    _edge("portal", "ehr", "Request", "TLS 1.3", True, True),
                    _edge("ehr", "phi-db", "PHI Query", "TLS", True, True),
                    _edge("ehr", "mfa", "AuthN", "SAML", True, True),
                    _edge("ehr", "audit", "Audit Trail", "Syslog", True, False),
                    _edge("phi-db", "encrypt", "Encrypt/Decrypt", "PKCS#11", True, True),
                    _edge("phi-db", "dlp", "Scan PHI", "API", True, True),
                    _edge("baa-gw", "ehr", "BAA Partner", "TLS", True, True),
                ],
                "boundaries": [
                    _boundary(
                        "bnd-phi",
                        "PHI Zone",
                        "enclave",
                        "CUI",
                        "IL4",
                        "#e94560",
                        200,
                        130,
                        520,
                        150,
                        ["ehr", "phi-db", "encrypt"],
                    ),
                    _boundary(
                        "bnd-baa",
                        "BAA Boundary",
                        "authorization",
                        "CUI",
                        "IL4",
                        "#f0ad4e",
                        20,
                        20,
                        200,
                        100,
                        ["baa-gw"],
                    ),
                ],
                "threats": [],
            }
        ),
    },
    # 13 — PCI-DSS v4.0 Cardholder Data
    {
        "id": "sct-pci-dss",
        "name": "PCI-DSS v4.0 Cardholder Data",
        "category": "Compliance",
        "description": "PCI-DSS v4.0 cardholder data environment with segmentation, "
        "tokenization, encryption, and audit.",
        "tags": json.dumps(["pci-dss", "cardholder", "tokenization", "hsm", "compliance"]),
        "graph_json": json.dumps(
            {
                "nodes": [
                    _node("pay-app", "Payment App", "asset-server", 50, 200),
                    _node("card-db", "Card DB", "asset-database", 250, 200),
                    _node("token-svc", "Tokenization Service", "asset-server", 450, 200),
                    _node("waf", "WAF", "ctrl-firewall", 50, 50),
                    _node("hsm", "HSM", "ctrl-kms", 450, 50),
                    _node("fim", "File Integrity Monitor", "ctrl-ids", 650, 200),
                    _node("log", "Log Collector", "ctrl-siem", 650, 50),
                ],
                "edges": [
                    _edge("waf", "pay-app", "Filtered", "TLS 1.3", True, True),
                    _edge("pay-app", "card-db", "Query", "TLS", True, True),
                    _edge("pay-app", "token-svc", "Tokenize", "TLS", True, True),
                    _edge("token-svc", "hsm", "Key Ops", "PKCS#11", True, True),
                    _edge("card-db", "fim", "Integrity Check", "Agent"),
                    _edge("pay-app", "log", "Audit", "Syslog", True, False),
                    _edge("fim", "log", "Alert", "TLS", True, False),
                ],
                "boundaries": [
                    _boundary(
                        "bnd-cde",
                        "CDE",
                        "enclave",
                        "CUI",
                        "IL4",
                        "#e94560",
                        20,
                        130,
                        500,
                        150,
                        ["pay-app", "card-db", "token-svc"],
                    ),
                    _boundary("bnd-dmz-pci", "DMZ", "dmz", "CUI", "IL4", "#ff6b6b", 20, 20, 150, 80, ["waf"]),
                ],
                "threats": [],
            }
        ),
    },
    # 14 — SaaS Multi-Tenant Isolation
    {
        "id": "sct-saas-multitenant",
        "name": "SaaS Multi-Tenant Isolation",
        "category": "Cloud",
        "description": "Multi-tenant SaaS security with tenant isolation, shared infrastructure, "
        "data partitioning, and per-tenant encryption keys.",
        "tags": json.dumps(["saas", "multi-tenant", "isolation", "tenant-encryption", "cloud"]),
        "graph_json": json.dumps(
            {
                "nodes": [
                    _node("apigw", "API Gateway", "asset-server", 50, 200),
                    _node("auth", "Auth Service", "asset-server", 250, 200),
                    _node("tenant-db", "Tenant DB", "asset-database", 450, 200),
                    _node("cache", "Shared Cache", "asset-storage", 450, 350),
                    _node("idp", "IdP", "ctrl-idp", 250, 50),
                    _node("kms-tenant", "KMS per-tenant", "ctrl-kms", 650, 200),
                    _node("siem", "SIEM", "ctrl-siem", 650, 50),
                    _node("rate", "Rate Limiter", "ctrl-firewall", 50, 50),
                ],
                "edges": [
                    _edge("rate", "apigw", "Throttle", "HTTPS", True, True),
                    _edge("apigw", "auth", "AuthN", "TLS", True, True),
                    _edge("auth", "idp", "Verify", "SAML", True, True),
                    _edge("auth", "tenant-db", "Tenant Query", "TLS", True, True),
                    _edge("tenant-db", "kms-tenant", "Encrypt/Decrypt", "PKCS#11", True, True),
                    _edge("auth", "cache", "Session", "TLS", True, True),
                    _edge("auth", "siem", "Audit", "Syslog", True, False),
                ],
                "boundaries": [
                    _boundary(
                        "bnd-shared",
                        "Shared Infra",
                        "cloud",
                        "CUI",
                        "IL4",
                        "#4ecdc4",
                        20,
                        130,
                        300,
                        150,
                        ["apigw", "auth"],
                    ),
                    _boundary(
                        "bnd-tenant",
                        "Tenant Boundary",
                        "enclave",
                        "CUI",
                        "IL4",
                        "#e94560",
                        400,
                        130,
                        320,
                        280,
                        ["tenant-db", "cache", "kms-tenant"],
                    ),
                ],
                "threats": [],
            }
        ),
    },
    # 15 — CI/CD Pipeline Security
    {
        "id": "sct-cicd-security",
        "name": "CI/CD Pipeline Security",
        "category": "DevSecOps",
        "description": "Secure CI/CD pipeline with code signing, SAST/DAST, container scanning, "
        "SBOM generation, and deployment gates.",
        "tags": json.dumps(["cicd", "devsecops", "sast", "sbom", "signing", "pipeline"]),
        "graph_json": json.dumps(
            {
                "nodes": [
                    _node("repo", "Git Repo", "asset-storage", 50, 200),
                    _node("ci", "CI Runner", "asset-server", 200, 200),
                    _node("artifact", "Artifact Registry", "asset-registry", 400, 200),
                    _node("sast", "SAST Scanner", "ctrl-scanner", 200, 50),
                    _node("container-scan", "Container Scanner", "ctrl-scanner", 400, 50),
                    _node("sign", "Signing Service", "ctrl-kms", 550, 50),
                    _node("gate", "Deploy Gate", "ctrl-firewall", 550, 200),
                    _node("prod", "Production", "asset-server", 700, 200),
                ],
                "edges": [
                    _edge("repo", "ci", "Trigger", "SSH", True, True),
                    _edge("ci", "sast", "Scan Code", "API", True, True),
                    _edge("ci", "artifact", "Push", "TLS", True, True),
                    _edge("artifact", "container-scan", "Scan Image", "API", True, True),
                    _edge("artifact", "sign", "Sign", "PKCS#11", True, True),
                    _edge("artifact", "gate", "Promote", "TLS", True, True),
                    _edge("gate", "prod", "Deploy", "TLS", True, True),
                ],
                "boundaries": [
                    _boundary(
                        "bnd-build",
                        "Build Zone",
                        "network",
                        "CUI",
                        "IL4",
                        "#f0ad4e",
                        150,
                        130,
                        470,
                        150,
                        ["ci", "artifact", "sast", "container-scan"],
                    ),
                    _boundary(
                        "bnd-prod", "Production Zone", "enclave", "CUI", "IL4", "#e94560", 650, 150, 120, 120, ["prod"]
                    ),
                ],
                "threats": [],
            }
        ),
    },
]

# ── Snippet seeds ────────────────────────────────────────────────────────────
# Reusable security building blocks — drag onto canvas to compose architectures.

SNIPPETS = [
    {
        "id": "scs-auth-stack",
        "name": "Authentication Stack",
        "category": "Identity",
        "description": "Complete authentication chain: User → MFA → IdP/SSO → Access Token → Application. Drop onto any design to add federated auth.",
        "tags": json.dumps(["auth", "mfa", "idp", "sso", "identity"]),
        "graph_json": json.dumps(
            {
                "nodes": [
                    _node("user", "User", "asset-user", 50, 100),
                    _node("mfa", "MFA", "ctrl-mfa", 200, 100),
                    _node("idp", "IdP / SSO", "ctrl-idp", 350, 100),
                    _node("app", "Application", "asset-webapp", 500, 100),
                ],
                "edges": [
                    _edge("user", "mfa", "Authenticate", "FIDO2", encrypted=True),
                    _edge("mfa", "idp", "Verify", "SAML", encrypted=True, authenticated=True),
                    _edge("idp", "app", "Token", "OAuth2", encrypted=True, authenticated=True),
                ],
                "boundaries": [],
            }
        ),
    },
    {
        "id": "scs-encryption-layer",
        "name": "Encryption-at-Rest Layer",
        "category": "Data Protection",
        "description": "KMS-backed encryption for data stores: Database + Storage → KMS → HSM. Ensures FIPS 140-2 compliance.",
        "tags": json.dumps(["encryption", "kms", "hsm", "fips", "data-at-rest"]),
        "graph_json": json.dumps(
            {
                "nodes": [
                    _node("db", "Database", "asset-database", 100, 100),
                    _node("storage", "Object Storage", "asset-storage", 300, 100),
                    _node("kms", "KMS / HSM", "ctrl-encryption", 200, 250),
                ],
                "edges": [
                    _edge("db", "kms", "Encrypt/Decrypt", "PKCS#11", encrypted=True, authenticated=True),
                    _edge("storage", "kms", "Encrypt/Decrypt", "PKCS#11", encrypted=True, authenticated=True),
                ],
                "boundaries": [
                    {
                        "id": "bnd-data",
                        "type": "boundary-classify",
                        "label": "CUI Data Zone",
                        "x": 60,
                        "y": 60,
                        "width": 320,
                        "height": 100,
                    },
                ],
            }
        ),
    },
    {
        "id": "scs-siem-pipeline",
        "name": "SIEM Logging Pipeline",
        "category": "Monitoring",
        "description": "Centralized security monitoring: Assets → Log Collector → SIEM → Alerting. Satisfies NIST AU-2, AU-6.",
        "tags": json.dumps(["siem", "logging", "monitoring", "alerting", "nist-au"]),
        "graph_json": json.dumps(
            {
                "nodes": [
                    _node("src1", "App Server", "asset-server", 50, 100),
                    _node("src2", "Database", "asset-database", 50, 250),
                    _node("siem", "SIEM", "ctrl-siem", 250, 175),
                    _node("alert", "Alert / Ticket", "asset-api", 450, 175),
                ],
                "edges": [
                    _edge("src1", "siem", "Syslog", "TLS", encrypted=True),
                    _edge("src2", "siem", "Audit Log", "TLS", encrypted=True),
                    _edge("siem", "alert", "Alert", "HTTPS", encrypted=True, authenticated=True),
                ],
                "boundaries": [],
            }
        ),
    },
    {
        "id": "scs-dmz-pattern",
        "name": "DMZ Perimeter Pattern",
        "category": "Network Security",
        "description": "Classic DMZ: Internet → WAF/Firewall → Reverse Proxy → App. Two trust boundaries with inspection at each.",
        "tags": json.dumps(["dmz", "firewall", "waf", "perimeter", "defense-in-depth"]),
        "graph_json": json.dumps(
            {
                "nodes": [
                    _node("inet", "Internet", "boundary-internet", 50, 150),
                    _node("waf", "WAF / Firewall", "ctrl-firewall", 200, 150),
                    _node("proxy", "Reverse Proxy", "asset-server", 350, 150),
                    _node("app", "Application", "asset-webapp", 500, 150),
                ],
                "edges": [
                    _edge("inet", "waf", "HTTPS", "TLS 1.3", encrypted=True),
                    _edge("waf", "proxy", "Filtered", "HTTP/2"),
                    _edge("proxy", "app", "Forward", "HTTP/2", authenticated=True),
                ],
                "boundaries": [
                    {
                        "id": "bnd-dmz",
                        "type": "boundary-dmz",
                        "label": "DMZ",
                        "x": 160,
                        "y": 100,
                        "width": 250,
                        "height": 120,
                    },
                ],
            }
        ),
    },
    {
        "id": "scs-zt-microseg",
        "name": "Zero Trust Micro-Segment",
        "category": "Zero Trust",
        "description": "Zero Trust segment: every service authenticated via mTLS through service mesh. No implicit trust. NIST 800-207 compliant.",
        "tags": json.dumps(["zero-trust", "mtls", "service-mesh", "micro-segmentation"]),
        "graph_json": json.dumps(
            {
                "nodes": [
                    _node("svc-a", "Service A", "asset-container", 100, 100),
                    _node("svc-b", "Service B", "asset-container", 300, 100),
                    _node("mesh", "Service Mesh", "ctrl-firewall", 200, 250),
                    _node("pep", "Policy Engine", "ctrl-cspm", 400, 250),
                ],
                "edges": [
                    _edge("svc-a", "mesh", "Sidecar", "mTLS", encrypted=True, authenticated=True),
                    _edge("svc-b", "mesh", "Sidecar", "mTLS", encrypted=True, authenticated=True),
                    _edge("mesh", "pep", "Policy Check", "gRPC", encrypted=True, authenticated=True),
                ],
                "boundaries": [],
            }
        ),
    },
    {
        "id": "scs-supply-chain",
        "name": "Supply Chain Security Gate",
        "category": "DevSecOps",
        "description": "Container supply chain: Registry → Scanner → Admission Control → Runtime. Blocks unsigned or vulnerable images.",
        "tags": json.dumps(["supply-chain", "container", "sbom", "signing", "admission"]),
        "graph_json": json.dumps(
            {
                "nodes": [
                    _node("registry", "Container Registry", "asset-storage", 50, 150),
                    _node("scanner", "Vuln Scanner", "ctrl-scanner", 200, 150),
                    _node("admission", "Admission Control", "ctrl-firewall", 350, 150),
                    _node("runtime", "K8s Runtime", "asset-container", 500, 150),
                ],
                "edges": [
                    _edge("registry", "scanner", "Scan", "HTTPS", encrypted=True, authenticated=True),
                    _edge("scanner", "admission", "Pass/Fail", "gRPC", encrypted=True, authenticated=True),
                    _edge("admission", "runtime", "Admit", "K8s API", encrypted=True, authenticated=True),
                ],
                "boundaries": [],
            }
        ),
    },
    {
        "id": "scs-incident-response",
        "name": "Incident Response Chain",
        "category": "Incident Response",
        "description": "IR workflow: Detection (SIEM) → Triage → Containment → Forensics → Recovery. Maps to NIST SP 800-61.",
        "tags": json.dumps(["incident-response", "siem", "forensics", "nist-800-61"]),
        "graph_json": json.dumps(
            {
                "nodes": [
                    _node("detect", "SIEM Detection", "ctrl-siem", 50, 150),
                    _node("triage", "Triage / Classify", "asset-api", 200, 150),
                    _node("contain", "Containment", "ctrl-firewall", 350, 150),
                    _node("forensic", "Forensics", "asset-server", 500, 150),
                    _node("recover", "Recovery", "asset-server", 650, 150),
                ],
                "edges": [
                    _edge("detect", "triage", "Alert", "HTTPS", encrypted=True),
                    _edge("triage", "contain", "Isolate", "API"),
                    _edge("contain", "forensic", "Evidence", "TLS", encrypted=True),
                    _edge("forensic", "recover", "Restore", "SSH", encrypted=True, authenticated=True),
                ],
                "boundaries": [],
            }
        ),
    },
    {
        "id": "scs-pam-bastion",
        "name": "PAM + Bastion Access",
        "category": "Access Control",
        "description": "Privileged access: Admin → PAM → Bastion → Target Server. Session recording, just-in-time access, audit trail.",
        "tags": json.dumps(["pam", "bastion", "privileged-access", "session-recording"]),
        "graph_json": json.dumps(
            {
                "nodes": [
                    _node("admin", "Admin User", "asset-user", 50, 150),
                    _node("pam", "PAM", "ctrl-pam", 200, 150),
                    _node("bastion", "Bastion Host", "asset-server", 350, 150),
                    _node("target", "Target Server", "asset-server", 500, 150),
                    _node("audit", "Audit Log", "ctrl-siem", 350, 300),
                ],
                "edges": [
                    _edge("admin", "pam", "Request", "HTTPS", encrypted=True, authenticated=True),
                    _edge("pam", "bastion", "JIT Access", "SSH", encrypted=True, authenticated=True),
                    _edge("bastion", "target", "Session", "SSH", encrypted=True, authenticated=True),
                    _edge("bastion", "audit", "Record", "TLS", encrypted=True),
                ],
                "boundaries": [],
            }
        ),
    },
    {
        "id": "scs-mtls-setup",
        "name": "mTLS Service-to-Service",
        "category": "Encryption",
        "description": "Mutual TLS between services: Service A ↔ Service B via CA/cert-manager with policy enforcement. All edges encrypted and authenticated.",
        "tags": json.dumps(["mtls", "encryption", "service-mesh", "certificates"]),
        "graph_json": json.dumps(
            {
                "nodes": [
                    _node("svc-a", "Service A", "asset-server", 50, 150),
                    _node("svc-b", "Service B", "asset-server", 400, 150),
                    _node("ca", "CA / cert-manager", "ctrl-kms", 225, 50),
                    _node("policy", "Policy Engine", "ctrl-cspm", 225, 280),
                ],
                "edges": [
                    _edge("svc-a", "svc-b", "mTLS", "TLS 1.3", encrypted=True, authenticated=True),
                    _edge("ca", "svc-a", "Issue Cert", "ACME", encrypted=True, authenticated=True),
                    _edge("ca", "svc-b", "Issue Cert", "ACME", encrypted=True, authenticated=True),
                    _edge("policy", "svc-a", "Enforce", "gRPC", encrypted=True, authenticated=True),
                    _edge("policy", "svc-b", "Enforce", "gRPC", encrypted=True, authenticated=True),
                ],
                "boundaries": [],
            }
        ),
    },
    {
        "id": "scs-secret-rotation",
        "name": "Secret Rotation Pipeline",
        "category": "Operations",
        "description": "Automated secret rotation: Vault → Rotation Lambda → App Config refresh with full audit logging.",
        "tags": json.dumps(["secrets", "rotation", "vault", "operations", "automation"]),
        "graph_json": json.dumps(
            {
                "nodes": [
                    _node("vault", "Vault", "ctrl-kms", 50, 150),
                    _node("rotation", "Rotation Lambda", "asset-lambda", 250, 150),
                    _node("appconfig", "App Config", "asset-server", 450, 150),
                    _node("audit", "Audit Log", "ctrl-siem", 250, 300),
                ],
                "edges": [
                    _edge("vault", "rotation", "Trigger Rotate", "TLS", encrypted=True, authenticated=True),
                    _edge("rotation", "appconfig", "Update Secret", "TLS", encrypted=True, authenticated=True),
                    _edge("rotation", "audit", "Log Rotation", "TLS", encrypted=True),
                    _edge("vault", "audit", "Access Log", "TLS", encrypted=True),
                ],
                "boundaries": [],
            }
        ),
    },
    {
        "id": "scs-backup-dr",
        "name": "Backup & Disaster Recovery",
        "category": "Resilience",
        "description": "Backup and DR pipeline: Primary DB → Encrypted Backup Store → DR Site with KMS key management.",
        "tags": json.dumps(["backup", "disaster-recovery", "resilience", "encryption"]),
        "graph_json": json.dumps(
            {
                "nodes": [
                    _node("primary", "Primary DB", "asset-database", 50, 150),
                    _node("backup", "Backup Store", "asset-storage", 250, 150),
                    _node("dr", "DR Site", "asset-server", 450, 150),
                    _node("kms", "KMS", "ctrl-kms", 250, 300),
                ],
                "edges": [
                    _edge("primary", "backup", "Replicate", "TLS", encrypted=True, authenticated=True),
                    _edge("backup", "dr", "Restore", "TLS", encrypted=True, authenticated=True),
                    _edge("backup", "kms", "Encrypt/Decrypt", "PKCS#11", encrypted=True, authenticated=True),
                    _edge("dr", "kms", "Decrypt", "PKCS#11", encrypted=True, authenticated=True),
                ],
                "boundaries": [],
            }
        ),
    },
    {
        "id": "scs-vuln-management",
        "name": "Vulnerability Management Lifecycle",
        "category": "Operations",
        "description": "Vulnerability lifecycle: Scanner → Asset Inventory → Ticketing → Remediation Queue with SIEM correlation.",
        "tags": json.dumps(["vulnerability", "scanning", "remediation", "operations"]),
        "graph_json": json.dumps(
            {
                "nodes": [
                    _node("scanner", "Scanner", "ctrl-scanner", 50, 150),
                    _node("inventory", "Asset Inventory", "asset-database", 200, 150),
                    _node("ticketing", "Ticketing", "asset-server", 350, 150),
                    _node("siem", "SIEM", "ctrl-siem", 200, 300),
                    _node("remediation", "Remediation Queue", "asset-server", 500, 150),
                ],
                "edges": [
                    _edge("scanner", "inventory", "Findings", "API", encrypted=True, authenticated=True),
                    _edge("inventory", "ticketing", "Create Ticket", "API", encrypted=True, authenticated=True),
                    _edge("ticketing", "remediation", "Assign", "API", encrypted=True, authenticated=True),
                    _edge("scanner", "siem", "Alert", "TLS", encrypted=True),
                    _edge("remediation", "siem", "Status", "TLS", encrypted=True),
                ],
                "boundaries": [],
            }
        ),
    },
    {
        "id": "scs-waf-rules",
        "name": "WAF Rule Pipeline",
        "category": "Perimeter",
        "description": "WAF rule lifecycle: Threat Intel Feed → Rule Engine → WAF deployment with centralized logging.",
        "tags": json.dumps(["waf", "firewall", "threat-intel", "perimeter", "rules"]),
        "graph_json": json.dumps(
            {
                "nodes": [
                    _node("waf", "WAF", "ctrl-firewall", 50, 150),
                    _node("engine", "Rule Engine", "asset-server", 250, 150),
                    _node("feed", "Threat Intel Feed", "asset-storage", 250, 300),
                    _node("logging", "Logging", "ctrl-siem", 450, 150),
                ],
                "edges": [
                    _edge("feed", "engine", "Indicators", "TLS", encrypted=True, authenticated=True),
                    _edge("engine", "waf", "Deploy Rules", "API", encrypted=True, authenticated=True),
                    _edge("waf", "logging", "Traffic Logs", "Syslog", encrypted=True),
                    _edge("engine", "logging", "Rule Changes", "TLS", encrypted=True),
                ],
                "boundaries": [],
            }
        ),
    },
]


def _seed_zig(conn):
    """Seed ZIG pillars, capabilities, and activities (idempotent)."""
    try:
        from tools.security_canvas.constants import (
            ZIG_PILLARS, ZIG_CAPABILITIES, ZIG_ACTIVITIES,
        )
    except ImportError:
        try:
            import sys
            sys.path.insert(0, str(_ICDEV_ROOT))
            from tools.security_canvas.constants import (
                ZIG_PILLARS, ZIG_CAPABILITIES, ZIG_ACTIVITIES,
            )
        except ImportError:
            print("[init_db] WARNING: Could not import ZIG constants — skipping ZIG seed.", file=sys.stderr)
            return

    ph = "%s" if _SC_BACKEND == "postgresql" else "?"

    # Seed pillars — batch existence check in Python (avoids per-row ? placeholder warnings)
    existing_slugs = {r[0] for r in conn.execute("SELECT slug FROM zig_pillars").fetchall()}
    p_added = 0
    for p in ZIG_PILLARS:
        if p["slug"] not in existing_slugs:
            conn.execute(
                "INSERT INTO zig_pillars (slug, name, full_name, pillar_weight, icon, color, csi_url, description, ficam_components) "
                f"VALUES ({ph},{ph},{ph},{ph},{ph},{ph},{ph},{ph},{ph})",
                (p["slug"], p["name"], p.get("full_name", p["name"]),
                 p.get("pillar_weight", 0.143),
                 p.get("icon", "shield"), p.get("color", "#6366f1"),
                 p.get("csi_url", ""), p.get("description", ""),
                 json.dumps(p.get("ficam_components", []))),
            )
            p_added += 1
    if p_added:
        conn.commit()

    # Seed capabilities — batch existence check in Python
    existing_cap_ids = {r[0] for r in conn.execute("SELECT id FROM zig_capabilities").fetchall()}
    c_added = 0
    for c in ZIG_CAPABILITIES:
        if c["id"] not in existing_cap_ids:
            conn.execute(
                "INSERT INTO zig_capabilities (id, pillar_slug, title, phase, maturity_level, description, nist_controls) "
                f"VALUES ({ph},{ph},{ph},{ph},{ph},{ph},{ph})",
                (c["id"], c["pillar"], c["title"], c["phase"], c["maturity_level"],
                 c.get("description", ""), json.dumps(c.get("nist_controls", []))),
            )
            c_added += 1
    if c_added:
        conn.commit()

    # Seed activities — batch existence check in Python
    existing_act_ids = {r[0] for r in conn.execute("SELECT id FROM zig_activities").fetchall()}
    a_added = 0
    for a in ZIG_ACTIVITIES:
        if a["id"] not in existing_act_ids:
            conn.execute(
                "INSERT INTO zig_activities (id, capability_id, phase, title, description, nist_control_ref) "
                f"VALUES ({ph},{ph},{ph},{ph},{ph},{ph})",
                (a["id"], a["capability_id"], a["phase"], a["title"],
                 a.get("description", ""), a.get("nist_control_ref", "")),
            )
            a_added += 1
    if a_added:
        conn.commit()

    print(f"[init_db] ZIG seed: {p_added} pillars, {c_added} capabilities, {a_added} activities added.", file=sys.stderr)


def init_db():
    """Initialize the Security Design Canvas database.

    Creates all tables, audit immutability triggers, seeds templates, and seeds ZIG data.
    """
    conn = get_connection()
    try:
        if _SC_BACKEND == "postgresql":
            # PostgreSQL: execute each statement individually
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
                    CREATE OR REPLACE FUNCTION sc_audit_immutable()
                    RETURNS TRIGGER AS $$
                    BEGIN
                        RAISE EXCEPTION 'Audit records are immutable — NIST AU-6';
                    END;
                    $$ LANGUAGE plpgsql
                """)
                conn.execute("""
                    DROP TRIGGER IF EXISTS sc_audit_no_update ON sc_audit
                """)
                conn.execute("""
                    CREATE TRIGGER sc_audit_no_update
                    BEFORE UPDATE ON sc_audit
                    FOR EACH ROW EXECUTE FUNCTION sc_audit_immutable()
                """)
                conn.execute("""
                    DROP TRIGGER IF EXISTS sc_audit_no_delete ON sc_audit
                """)
                conn.execute("""
                    CREATE TRIGGER sc_audit_no_delete
                    BEFORE DELETE ON sc_audit
                    FOR EACH ROW EXECUTE FUNCTION sc_audit_immutable()
                """)
                conn.commit()
            except Exception:
                pass  # triggers may already exist
            print("[init_db] Schema created (PostgreSQL)", file=sys.stderr)
        else:
            # SQLite: executescript for all-at-once
            conn.executescript(SCHEMA)
            # SQLite audit immutability triggers
            try:
                conn.executescript("""
                    CREATE TRIGGER IF NOT EXISTS sc_audit_no_update
                    BEFORE UPDATE ON sc_audit
                    BEGIN
                        SELECT RAISE(ABORT, 'Audit records are immutable');
                    END;
                    CREATE TRIGGER IF NOT EXISTS sc_audit_no_delete
                    BEFORE DELETE ON sc_audit
                    BEGIN
                        SELECT RAISE(ABORT, 'Audit records cannot be deleted');
                    END;
                """)
            except Exception:
                pass
            conn.commit()
            print(f"[init_db] Schema created at {DB_PATH}", file=sys.stderr)

        # Runtime migration: add is_stale to sc_threats for existing installs
        try:
            conn.execute("ALTER TABLE sc_threats ADD COLUMN is_stale INTEGER DEFAULT 0")
            conn.commit()
        except Exception:
            pass  # column already exists

        # Runtime migration: add target_id to zig_activity_completions for existing installs
        try:
            conn.execute(
                "ALTER TABLE zig_activity_completions ADD COLUMN target_id TEXT NOT NULL DEFAULT 'icdev-self'"
            )
            conn.commit()
        except Exception:
            pass  # column already exists

        ph = "%s" if _SC_BACKEND == "postgresql" else "?"

        # Seed templates — batch existence check in Python
        count = conn.execute("SELECT COUNT(*) FROM sc_templates").fetchone()[0]
        existing_tmpl_ids = {r[0] for r in conn.execute("SELECT id FROM sc_templates").fetchall()}
        added = 0
        for t in TEMPLATES:
            if t["id"] not in existing_tmpl_ids:
                conn.execute(
                    f"INSERT INTO sc_templates (id, name, category, description, graph_json, tags) VALUES ({ph},{ph},{ph},{ph},{ph},{ph})",
                    (t["id"], t["name"], t["category"], t["description"], t["graph_json"], t["tags"]),
                )
                added += 1
        if added:
            conn.commit()
            print(f"[init_db] Seeded {added} new templates (total: {count + added}).", file=sys.stderr)
        else:
            print(f"[init_db] All {count} templates up to date.", file=sys.stderr)

        # Seed snippets — batch existence check in Python
        snip_count = conn.execute("SELECT COUNT(*) FROM sc_snippets").fetchone()[0]
        existing_snip_ids = {r[0] for r in conn.execute("SELECT id FROM sc_snippets").fetchall()}
        snip_added = 0
        for s in SNIPPETS:
            if s["id"] not in existing_snip_ids:
                conn.execute(
                    f"INSERT INTO sc_snippets (id, name, category, description, graph_json, tags) VALUES ({ph},{ph},{ph},{ph},{ph},{ph})",
                    (s["id"], s["name"], s["category"], s["description"], s["graph_json"], s["tags"]),
                )
                snip_added += 1
        if snip_added:
            conn.commit()
            print(f"[init_db] Seeded {snip_added} new snippets (total: {snip_count + snip_added}).", file=sys.stderr)
        else:
            print(f"[init_db] All {snip_count} snippets up to date.", file=sys.stderr)

        # Seed ZIG framework data
        _seed_zig(conn)

    finally:
        conn.close()


if __name__ == "__main__":
    init_db()
