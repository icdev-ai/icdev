# CUI // SP-CTI — ICDEV Pipeline Design Canvas DB Initializer
# Classification: CUI — Controlled Unclassified Information
"""
Pipeline Design Canvas — DB initializer.
Creates schema and seeds 12 canonical pipeline templates + 10 pre-built snippets.

Dual-backend: SQLite (default) or PostgreSQL.
Set PC_STORAGE_BACKEND=postgresql + PC_PG_* env vars to use PostgreSQL.
"""

import json
from tools.logging.icdev_logger import get_logger
import os
import sqlite3
import uuid
from pathlib import Path

_ICDEV_ROOT = Path(__file__).resolve().parents[3]
DB_PATH = _ICDEV_ROOT / "data" / "pipeline_canvas.db"

_PC_BACKEND = os.environ.get("PC_STORAGE_BACKEND", os.environ.get("ICDEV_CANVAS_STORAGE_BACKEND", os.environ.get("ICDEV_STORAGE_BACKEND", "postgresql"))).lower()


def get_connection():
    """Get a database connection — SQLite or PostgreSQL.

    cvx-sql-03: this is the canvas-connection pattern — it already disables RLS
    via set_security_context(None) below.

    ON POSTGRESQL THIS CANVAS USES THE SHARED icdev DATABASE. The note here used
    to say it was NOT renamed to get_canvas_connection() "because that helper
    targets the shared icdev DB on PG, which would break this canvas's dedicated
    PC_PG_DATABASE=pipeline_canvas contract" — but there was no such contract to
    break. ``get_connection()`` honours db_path as a SQLite file ONLY when the
    backend is sqlite and the name ends in '.db'; on PostgreSQL it ignores
    db_path and connects to the shared database, as its own comment states.
    Measured 2026-08-16: ``get_connection(db_path='pipeline_canvas')`` reports
    ``current_database() = icdev``. The reason given for keeping this wrapper was
    exactly the thing that was not happening.

    Behaviour is therefore unchanged and only the claim is — table-prefix
    namespacing IS the platform's design for canvas tables on PG. Passing the
    name was worse than useless: on a SQLite backend a bare name does not end in
    '.db', so it created an extension-less database file called
    ``pipeline_canvas`` in whatever directory the process started from, outside
    the ``data/*.db`` ignore rule. One appeared in a repo checkout while this
    canvas was being enabled for CI.
    """
    if _PC_BACKEND == "postgresql":
        try:
            from tools.db.storage import get_connection as _icdev_conn

            # No db_path: the shared icdev database is what this returns anyway.
            conn = _icdev_conn()
            # Canvas tables (pipelines, pc_templates, pc_snippets, etc.) have no
            # tenant_id/classification columns — disable RLS so the global
            # row-level predicate does not raise UndefinedColumn on every query.
            conn.set_security_context(None)  # rls-bypass: canvas tables lack tenant_id/classification cols
            return conn
        except ImportError:
            # pdx-data-02: postgresql was explicitly requested but the PG stack is
            # unavailable — do NOT fall back silently. A silent SQLite fallback
            # splits this canvas's data across two backends (some writes land in PG,
            # others in the local pipeline_canvas.db), which is very hard to diagnose.
            get_logger("icdev.pipeline.db").error(
                "PDC: postgresql requested but psycopg2/storage unavailable — "
                "falling back to sqlite; data will split across backends"
            )
    # SQLite (default) — per-canvas DB, distinct from icdev.db
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    # pdx-data-03: ALL runtime SQL in tools/pipeline (blueprint.py, twin.py,
    # sops.py) is authored with psycopg2-native %s placeholders. A bare
    # sqlite3.Connection raises OperationalError near '%'. Wrap it in the shared
    # StorageConnection(backend="sqlite"), which routes execute/executemany/
    # cursor through StorageCursor → translate_sql(sql, "sqlite") →
    # _translate_pg_to_sqlite (%s → ?, string-literal / quoted-identifier /
    # comment aware; see tools/db/storage.py). No security context is attached,
    # so _inject_rls is a no-op — correct for these canvas tables, which lack
    # tenant_id/classification columns. row_factory (sqlite3.Row), commit/
    # rollback/close, executescript, cursor(), and the context-manager protocol
    # all pass through unchanged, so callers work without modification.
    try:
        from tools.db.storage import StorageConnection

        return StorageConnection(conn, "sqlite")
    except ImportError:
        # Reuse impossible (storage layer unavailable) — return the raw
        # connection so init/seed with native ? placeholders still works.
        return conn


# ── Schema ────────────────────────────────────────────────────────────────────

SCHEMA = """
CREATE TABLE IF NOT EXISTS pipelines (
    id              TEXT PRIMARY KEY,
    name            TEXT NOT NULL,
    description     TEXT,
    graph_json      TEXT NOT NULL DEFAULT '{"nodes":[],"edges":[]}',
    template_id     TEXT,
    classification  TEXT DEFAULT 'public',
    target_csp      TEXT DEFAULT 'generic',
    created_at      TEXT DEFAULT CURRENT_TIMESTAMP,
    updated_at      TEXT DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS pc_templates (
    id              TEXT PRIMARY KEY,
    name            TEXT NOT NULL,
    category        TEXT,
    description     TEXT,
    graph_json      TEXT NOT NULL DEFAULT '{"nodes":[],"edges":[]}',
    thumbnail_svg   TEXT,
    tags            TEXT DEFAULT '[]'
);

CREATE TABLE IF NOT EXISTS pc_snippets (
    id                      TEXT PRIMARY KEY,
    name                    TEXT NOT NULL,
    category                TEXT NOT NULL DEFAULT 'DevSecOps',
    description             TEXT,
    classification_level    TEXT DEFAULT 'CUI',
    impact_level            TEXT DEFAULT 'IL4',
    slsa_level              TEXT DEFAULT 'L1',
    ssdf_practices          TEXT DEFAULT '[]',
    graph_json              TEXT NOT NULL DEFAULT '{"nodes":[],"edges":[]}',
    tags                    TEXT DEFAULT '[]',
    created_at              TEXT DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS pc_versions (
    id              TEXT PRIMARY KEY,
    pipeline_id     TEXT REFERENCES pipelines(id),
    version_num     INTEGER NOT NULL,
    label           TEXT,
    graph_json      TEXT NOT NULL,
    created_by      TEXT,
    notes           TEXT,
    created_at      TEXT DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS pc_audit (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    action          TEXT NOT NULL,
    entity_type     TEXT,
    entity_id       TEXT,
    details         TEXT,
    user_id         TEXT,
    classification  TEXT DEFAULT 'CUI // SP-CTI',
    ts              TEXT DEFAULT CURRENT_TIMESTAMP
);

-- RESERVED (pdx-hyg-01): no runtime reader/writer as of pdx-hyg-01. Only the
-- demo seeder (tools/db/seeds/seed_pdc_demo.py) populates this table, and the PDC
-- delete-cascade list in blueprint.py references it by FK. No route or engine
-- reads pc_stages. Kept (not dropped) to preserve deployed DBs.
CREATE TABLE IF NOT EXISTS pc_stages (
    id              TEXT PRIMARY KEY,
    pipeline_id     TEXT REFERENCES pipelines(id),
    parent_id       TEXT,
    stage_type      TEXT NOT NULL,
    label           TEXT NOT NULL,
    description     TEXT,
    auto_nodes_json TEXT DEFAULT '[]',
    pos_x           REAL DEFAULT 0,
    pos_y           REAL DEFAULT 0,
    width           REAL DEFAULT 300,
    height          REAL DEFAULT 200,
    color           TEXT,
    collapsed       INTEGER DEFAULT 0,
    parallel        INTEGER DEFAULT 0,
    created_at      TEXT DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS pc_compliance_checks (
    id              TEXT PRIMARY KEY,
    pipeline_id     TEXT REFERENCES pipelines(id),
    check_type      TEXT NOT NULL,
    passed          INTEGER DEFAULT 0,
    failed          INTEGER DEFAULT 0,
    findings_json   TEXT DEFAULT '[]',
    ran_at          TEXT DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS pc_compliance_findings (
    id              TEXT PRIMARY KEY,
    pipeline_id     TEXT REFERENCES pipelines(id),
    audit_id        TEXT REFERENCES pc_compliance_checks(id),
    rule_id         TEXT NOT NULL,
    framework       TEXT NOT NULL,
    severity        TEXT DEFAULT 'CAT2',
    title           TEXT NOT NULL,
    description     TEXT,
    affected_entity TEXT,
    affected_type   TEXT,
    status          TEXT DEFAULT 'open',
    fix_action      TEXT,
    remediated_at   TEXT,
    created_at      TEXT DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS pc_boundaries (
    id              TEXT PRIMARY KEY,
    pipeline_id     TEXT REFERENCES pipelines(id),
    label           TEXT NOT NULL DEFAULT 'Stage Boundary',
    classification  TEXT DEFAULT 'CUI',
    color           TEXT DEFAULT '#e94560',
    fill_opacity    REAL DEFAULT 0.08,
    node_ids        TEXT DEFAULT '[]',
    boundary_type   TEXT DEFAULT 'security_zone',
    pos_x           REAL DEFAULT 0,
    pos_y           REAL DEFAULT 0,
    width           REAL DEFAULT 400,
    height          REAL DEFAULT 300,
    created_at      TEXT DEFAULT CURRENT_TIMESTAMP
);

-- RESERVED (pdx-hyg-01): no runtime reader/writer as of pdx-hyg-01. Only the
-- demo seeder (tools/db/seeds/seed_pdc_demo.py) populates this table. No route or
-- engine reads pc_projects. Kept (not dropped) to preserve deployed DBs.
CREATE TABLE IF NOT EXISTS pc_projects (
    id              TEXT PRIMARY KEY,
    name            TEXT NOT NULL,
    description     TEXT,
    status          TEXT DEFAULT 'draft',
    owner           TEXT,
    created_at      TEXT DEFAULT CURRENT_TIMESTAMP,
    updated_at      TEXT DEFAULT CURRENT_TIMESTAMP
);

-- RESERVED (pdx-hyg-01): no runtime reader/writer as of pdx-hyg-01. Only the
-- demo seeder (tools/db/seeds/seed_pdc_demo.py) populates this join table, and the
-- PDC delete-cascade list in blueprint.py references it by FK. No route or engine
-- reads pc_project_pipelines. Kept (not dropped) to preserve deployed DBs.
CREATE TABLE IF NOT EXISTS pc_project_pipelines (
    project_id      TEXT REFERENCES pc_projects(id),
    pipeline_id     TEXT REFERENCES pipelines(id),
    PRIMARY KEY (project_id, pipeline_id)
);

CREATE TABLE IF NOT EXISTS pc_change_requests (
    id              TEXT PRIMARY KEY,
    pipeline_id     TEXT REFERENCES pipelines(id),
    cr_number       TEXT,
    cr_type         TEXT DEFAULT 'modify',
    status          TEXT DEFAULT 'draft',
    markup_json     TEXT DEFAULT '[]',
    created_by      TEXT,
    approved_by     TEXT,
    created_at      TEXT DEFAULT CURRENT_TIMESTAMP,
    updated_at      TEXT DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS pc_collab_sessions (
    id          TEXT PRIMARY KEY,
    design_id   TEXT NOT NULL REFERENCES pipelines(id) ON DELETE CASCADE,
    user_id     TEXT NOT NULL,
    user_name   TEXT NOT NULL DEFAULT '',
    color       TEXT NOT NULL DEFAULT '#3498db',
    joined_at   TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    last_seen   TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    is_active   INTEGER NOT NULL DEFAULT 1
);

CREATE INDEX IF NOT EXISTS idx_pc_collab_design ON pc_collab_sessions(design_id);

CREATE TABLE IF NOT EXISTS pdc_sops (
    id              TEXT PRIMARY KEY,
    title           TEXT NOT NULL,
    sop_type        TEXT NOT NULL DEFAULT 'custom',
    description     TEXT DEFAULT '',
    purpose         TEXT DEFAULT '',
    scope           TEXT DEFAULT '',
    steps           TEXT,
    nist_controls   TEXT,
    owner           TEXT DEFAULT '',
    reviewer        TEXT DEFAULT '',
    approval_status TEXT DEFAULT 'draft',
    version         TEXT DEFAULT '1.0',
    approved_by     TEXT,
    approved_at     TEXT,
    next_review_date TEXT,
    rejected_reason TEXT DEFAULT '',
    classification  TEXT DEFAULT 'CUI // SP-CTI',
    created_at      TEXT DEFAULT CURRENT_TIMESTAMP,
    updated_at      TEXT DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_pdc_sops_type ON pdc_sops(sop_type);
CREATE INDEX IF NOT EXISTS idx_pdc_sops_status ON pdc_sops(approval_status);

CREATE TABLE IF NOT EXISTS pdc_snapshots (
    id              TEXT PRIMARY KEY,
    pipeline_id     TEXT REFERENCES pipelines(id),
    label           TEXT,
    graph_json      TEXT NOT NULL DEFAULT '{"nodes":[],"edges":[]}',
    node_count      INTEGER DEFAULT 0,
    edge_count      INTEGER DEFAULT 0,
    created_by      TEXT,
    created_at      TEXT DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_pdc_snapshots_pipeline ON pdc_snapshots(pipeline_id);

CREATE TABLE IF NOT EXISTS pdc_simulations (
    id              TEXT PRIMARY KEY,
    pipeline_id     TEXT REFERENCES pipelines(id),
    baseline_snap_id TEXT REFERENCES pdc_snapshots(id),
    delta_graph_json TEXT NOT NULL DEFAULT '{"nodes":[],"edges":[]}',
    verdict         TEXT NOT NULL DEFAULT 'unknown',
    antipatterns_json   TEXT DEFAULT '[]',
    slsa_json           TEXT DEFAULT '{}',
    compliance_json     TEXT DEFAULT '{}',
    diff_json           TEXT DEFAULT '{}',
    critical_count  INTEGER DEFAULT 0,
    high_count      INTEGER DEFAULT 0,
    medium_count    INTEGER DEFAULT 0,
    created_by      TEXT,
    created_at      TEXT DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_pdc_simulations_pipeline ON pdc_simulations(pipeline_id);
"""


# ── Helper functions ──────────────────────────────────────────────────────────


def _node(nid, label, ntype, x, y, extra=None):
    """Create a node dict for graph_json."""
    n = {"id": nid, "label": label, "type": ntype, "x": x, "y": y}
    if extra:
        n.update(extra)
    return n


def _edge(src, dst, label="", edge_type="data_flow"):
    """Create an edge dict for graph_json."""
    return {
        "id": str(uuid.uuid4())[:8],
        "source": src,
        "target": dst,
        "label": label,
        "edge_type": edge_type,
    }


# ── Templates (12 canonical pipeline designs) ────────────────────────────────

TEMPLATES = [
    {
        "id": "tpl-basic-cicd",
        "name": "Basic CI/CD",
        "category": "Getting Started",
        "description": "Simple source → build → test → deploy pipeline. Good starting point for teams new to CI/CD.",
        "tags": json.dumps(["basic", "ci", "cd", "beginner"]),
        "graph_json": json.dumps(
            {
                "nodes": [
                    _node("src", "GitLab", "scm-gitlab", 50, 200),
                    _node("build", "Build Runner", "build-runner", 250, 200),
                    _node("test", "Unit Tests", "scan-sast", 450, 200, {"config": {"tool": "basic"}}),
                    _node("deploy", "Deploy", "k8s-cluster", 650, 200),
                ],
                "edges": [
                    _edge("src", "build", "push"),
                    _edge("build", "test", "artifact"),
                    _edge("test", "deploy", "approved"),
                ],
            }
        ),
    },
    {
        "id": "tpl-gitops",
        "name": "GitOps Pipeline",
        "category": "Cloud Native",
        "description": "Source → Build → Push to Registry → ArgoCD sync. GitOps pattern where Git is the single source of truth for deployment state.",
        "tags": json.dumps(["gitops", "argocd", "kubernetes", "declarative"]),
        "graph_json": json.dumps(
            {
                "nodes": [
                    _node("src", "GitLab", "scm-gitlab", 50, 200),
                    _node("ci", "GitLab CI", "cicd-gitlab", 250, 200),
                    _node("scan", "Trivy", "scan-trivy", 250, 350),
                    _node("reg", "Harbor", "registry-harbor", 450, 200),
                    _node("sign", "Cosign", "sign-cosign", 450, 350),
                    _node("argo", "ArgoCD", "gitops-argocd", 650, 200),
                    _node("k8s", "Kubernetes", "k8s-cluster", 850, 200),
                ],
                "edges": [
                    _edge("src", "ci", "push"),
                    _edge("ci", "scan", "image"),
                    _edge("ci", "reg", "push image"),
                    _edge("scan", "reg", "scan pass"),
                    _edge("reg", "sign", "sign"),
                    _edge("sign", "argo", "signed manifest"),
                    _edge("argo", "k8s", "sync"),
                ],
            }
        ),
    },
    {
        "id": "tpl-container-security",
        "name": "Container Security Pipeline",
        "category": "Security",
        "description": "Full container security: build → Trivy scan → Anchore policy → sign → push to Harbor. DoD-aligned container vetting.",
        "tags": json.dumps(["container", "security", "trivy", "cosign", "harbor"]),
        "graph_json": json.dumps(
            {
                "nodes": [
                    _node("src", "Source", "scm-gitlab", 50, 200),
                    _node("build", "Kaniko", "build-kaniko", 250, 200),
                    _node("trivy", "Trivy", "scan-trivy", 450, 150),
                    _node("anchore", "Anchore", "scan-anchore", 450, 300),
                    _node("sign", "Cosign", "sign-cosign", 650, 200),
                    _node("reg", "Harbor", "registry-harbor", 850, 200),
                    _node("gate", "Vuln Gate", "gate-vuln-threshold", 650, 350),
                ],
                "edges": [
                    _edge("src", "build", "push"),
                    _edge("build", "trivy", "image"),
                    _edge("build", "anchore", "image"),
                    _edge("trivy", "gate", "findings"),
                    _edge("anchore", "gate", "policy"),
                    _edge("gate", "sign", "pass"),
                    _edge("sign", "reg", "signed image"),
                ],
            }
        ),
    },
    {
        "id": "tpl-slsa-l3",
        "name": "Supply Chain Verification (SLSA L3)",
        "category": "Supply Chain",
        "description": "SLSA Level 3 pipeline: hermetic build, provenance generation, attestation, signing, verification before deploy.",
        "tags": json.dumps(["slsa", "supply-chain", "provenance", "attestation", "signing"]),
        "graph_json": json.dumps(
            {
                "nodes": [
                    _node("src", "Source (signed commits)", "scm-gitlab", 50, 200),
                    _node("build", "Tekton (hermetic)", "cicd-tekton", 250, 200),
                    _node("sbom", "Syft SBOM", "sbom-syft", 450, 100),
                    _node("prov", "SLSA Provenance", "attest-slsa-gen", 450, 250),
                    _node("sign", "Cosign", "sign-cosign", 650, 200),
                    _node("verify", "SLSA Verifier", "verify-slsa", 850, 200),
                    _node("k8s", "K8s + Kyverno", "k8s-cluster", 1050, 200),
                    _node("kyverno", "Kyverno", "policy-kyverno", 1050, 350),
                ],
                "edges": [
                    _edge("src", "build", "signed commit"),
                    _edge("build", "sbom", "artifact"),
                    _edge("build", "prov", "build metadata"),
                    _edge("sbom", "sign", "SBOM"),
                    _edge("prov", "sign", "provenance"),
                    _edge("sign", "verify", "signed attestations"),
                    _edge("verify", "k8s", "verified"),
                    _edge("kyverno", "k8s", "admission policy"),
                ],
            }
        ),
    },
    {
        "id": "tpl-compliance-first",
        "name": "Compliance-First Pipeline (NIST SSDF)",
        "category": "Compliance",
        "description": "Full SSDF coverage: all 4 practice areas (PO, PS, PW, RV) mapped with evidence collection at each stage.",
        "tags": json.dumps(["ssdf", "nist", "compliance", "evidence", "fedramp"]),
        "graph_json": json.dumps(
            {
                "nodes": [
                    _node("secrets", "Gitleaks", "scan-gitleaks", 50, 100),
                    _node("src", "GitLab (branch protect)", "scm-gitlab", 50, 250),
                    _node("sast", "SonarQube", "scan-sonarqube", 250, 100),
                    _node("sca", "Trivy SCA", "scan-trivy", 250, 250),
                    _node("iac", "Checkov", "scan-checkov", 250, 400),
                    _node("build", "Build + SBOM", "build-kaniko", 450, 200),
                    _node("sbom", "CycloneDX", "sbom-cyclonedx", 450, 350),
                    _node("sign", "Cosign", "sign-cosign", 650, 200),
                    _node("gate", "Policy Gate", "gate-vuln-threshold", 650, 350),
                    _node("deploy", "K8s (staging)", "k8s-cluster", 850, 200),
                    _node("evidence", "Evidence Locker", "comp-evidence", 850, 350),
                    _node("oscal", "OSCAL Export", "comp-oscal", 1050, 350),
                    _node("monitor", "Falco", "mon-falco", 1050, 200),
                ],
                "edges": [
                    _edge("secrets", "src", "pre-commit"),
                    _edge("src", "sast", "PW.7"),
                    _edge("src", "sca", "PW.4"),
                    _edge("src", "iac", "PW.6"),
                    _edge("sast", "build", "pass"),
                    _edge("sca", "build", "pass"),
                    _edge("iac", "build", "pass"),
                    _edge("build", "sbom", "PS.3"),
                    _edge("build", "sign", "artifact"),
                    _edge("sbom", "evidence", "SBOM evidence"),
                    _edge("sign", "gate", "signed"),
                    _edge("gate", "deploy", "approved"),
                    _edge("deploy", "monitor", "RV.1"),
                    _edge("deploy", "evidence", "deploy evidence"),
                    _edge("evidence", "oscal", "generate"),
                ],
            }
        ),
    },
    {
        "id": "tpl-progressive-delivery",
        "name": "Multi-Stage Progressive Delivery",
        "category": "Deployment",
        "description": "Build → Staging → Canary 10% → Canary 50% → Full rollout. Progressive deployment with automated rollback.",
        "tags": json.dumps(["canary", "progressive", "rollout", "deployment"]),
        "graph_json": json.dumps(
            {
                "nodes": [
                    _node("build", "Build", "build-runner", 50, 200),
                    _node("test", "Tests", "scan-sast", 250, 200),
                    _node("staging", "Staging", "k8s-cluster", 450, 200, {"config": {"environment": "staging"}}),
                    _node("smoke", "Smoke Tests", "gate-automated", 650, 200),
                    _node("canary10", "Canary 10%", "deploy-canary", 850, 200),
                    _node("approval", "Manual Approval", "gate-manual", 1050, 200),
                    _node("canary50", "Canary 50%", "deploy-canary", 1250, 200),
                    _node("full", "Full Rollout", "k8s-cluster", 1450, 200, {"config": {"environment": "production"}}),
                    _node("monitor", "Prometheus", "mon-prometheus", 1450, 350),
                ],
                "edges": [
                    _edge("build", "test"),
                    _edge("test", "staging"),
                    _edge("staging", "smoke"),
                    _edge("smoke", "canary10", "pass"),
                    _edge("canary10", "approval"),
                    _edge("approval", "canary50", "approved"),
                    _edge("canary50", "full"),
                    _edge("full", "monitor"),
                ],
            }
        ),
    },
    {
        "id": "tpl-airgap-dod",
        "name": "Air-Gapped DoD Pipeline",
        "category": "DoD / IL5+",
        "description": "Fully air-gapped pipeline for IL5/IL6: self-hosted GitLab, offline Trivy DB, Harbor, RKE2, manual approval gates, FIPS crypto.",
        "tags": json.dumps(["airgap", "dod", "il5", "il6", "secret", "fips", "ironbank"]),
        "graph_json": json.dumps(
            {
                "nodes": [
                    _node(
                        "src", "GitLab (self-hosted)", "scm-gitlab", 50, 200, {"config": {"airgap": True, "fips": True}}
                    ),
                    _node("secrets", "detect-secrets", "scan-detect-secrets", 50, 350),
                    _node("mirror", "Package Mirror", "package-mirror", 50, 50),
                    _node("vuln-db", "Vuln DB Mirror", "vuln-db-mirror", 250, 50),
                    _node("ci", "GitLab CI", "cicd-gitlab", 250, 200),
                    _node("sast", "Bandit", "scan-bandit", 250, 350),
                    _node("trivy", "Trivy (offline)", "scan-trivy", 450, 200, {"config": {"offline": True}}),
                    _node("ironbank", "Iron Bank Base", "registry-ironbank", 450, 350),
                    _node("harbor", "Harbor (air-gapped)", "registry-harbor", 650, 200, {"config": {"airgap": True}}),
                    _node("sign", "Cosign", "sign-cosign", 650, 350),
                    _node("gate", "Manual Gate", "gate-manual", 850, 200),
                    _node("rke2", "RKE2 (hardened)", "rke2", 1050, 200, {"config": {"cis_hardened": True}}),
                    _node("kyverno", "Kyverno", "policy-kyverno", 1050, 350),
                    _node("falco", "Falco", "mon-falco", 1250, 200),
                ],
                "edges": [
                    _edge("mirror", "ci", "dependencies"),
                    _edge("vuln-db", "trivy", "vuln database"),
                    _edge("src", "secrets", "pre-commit"),
                    _edge("src", "ci", "push"),
                    _edge("ci", "sast"),
                    _edge("ci", "trivy"),
                    _edge("ironbank", "ci", "base image"),
                    _edge("sast", "harbor", "pass"),
                    _edge("trivy", "harbor", "scan pass"),
                    _edge("harbor", "sign"),
                    _edge("sign", "gate"),
                    _edge("gate", "rke2", "approved"),
                    _edge("kyverno", "rke2", "admission"),
                    _edge("rke2", "falco", "runtime"),
                ],
            }
        ),
    },
    {
        "id": "tpl-serverless",
        "name": "Serverless CI/CD",
        "category": "Cloud Native",
        "description": "Serverless deployment pipeline: build → scan → package → deploy to Lambda/Functions/Cloud Run.",
        "tags": json.dumps(["serverless", "lambda", "functions", "cloud-run"]),
        "graph_json": json.dumps(
            {
                "nodes": [
                    _node("src", "GitHub", "cicd-github-actions", 50, 200),
                    _node("test", "Unit Tests", "scan-sast", 250, 200),
                    _node("sca", "Snyk", "scan-snyk", 250, 350),
                    _node("build", "Build", "build-runner", 450, 200),
                    _node("deploy", "Serverless Deploy", "deploy-serverless", 650, 200),
                    _node("monitor", "CloudWatch", "aws-cloudwatch", 850, 200),
                ],
                "edges": [
                    _edge("src", "test"),
                    _edge("src", "sca"),
                    _edge("test", "build", "pass"),
                    _edge("sca", "build", "pass"),
                    _edge("build", "deploy"),
                    _edge("deploy", "monitor"),
                ],
            }
        ),
    },
    {
        "id": "tpl-monorepo",
        "name": "Monorepo Multi-Service",
        "category": "Architecture",
        "description": "Parallel builds for multiple services in a monorepo, shared scanning, independent deploys.",
        "tags": json.dumps(["monorepo", "parallel", "multi-service"]),
        "graph_json": json.dumps(
            {
                "nodes": [
                    _node("src", "Source", "scm-gitlab", 50, 250),
                    _node("svc-a", "Service A Build", "build-runner", 300, 100),
                    _node("svc-b", "Service B Build", "build-runner", 300, 250),
                    _node("svc-c", "Service C Build", "build-runner", 300, 400),
                    _node("scan", "Shared Scan", "scan-trivy", 550, 250),
                    _node("reg", "Registry", "registry-harbor", 750, 250),
                    _node("deploy-a", "Deploy A", "k8s-cluster", 950, 100),
                    _node("deploy-b", "Deploy B", "k8s-cluster", 950, 250),
                    _node("deploy-c", "Deploy C", "k8s-cluster", 950, 400),
                ],
                "edges": [
                    _edge("src", "svc-a", "changed: svc-a/"),
                    _edge("src", "svc-b", "changed: svc-b/"),
                    _edge("src", "svc-c", "changed: svc-c/"),
                    _edge("svc-a", "scan"),
                    _edge("svc-b", "scan"),
                    _edge("svc-c", "scan"),
                    _edge("scan", "reg"),
                    _edge("reg", "deploy-a"),
                    _edge("reg", "deploy-b"),
                    _edge("reg", "deploy-c"),
                ],
            }
        ),
    },
    {
        "id": "tpl-feature-flags",
        "name": "Feature Flag Progressive Rollout",
        "category": "Deployment",
        "description": "Build → Deploy → Feature flag enable. Progressive rollout via feature flags instead of canary.",
        "tags": json.dumps(["feature-flags", "progressive", "rollout"]),
        "graph_json": json.dumps(
            {
                "nodes": [
                    _node("src", "Source", "scm-gitlab", 50, 200),
                    _node("build", "Build", "build-runner", 250, 200),
                    _node("test", "Tests", "scan-sast", 450, 200),
                    _node("deploy", "Deploy (all)", "k8s-cluster", 650, 200),
                    _node("ff", "Feature Flag", "deploy-feature-flag", 850, 200),
                    _node("monitor", "Grafana", "mon-grafana", 850, 350),
                ],
                "edges": [
                    _edge("src", "build"),
                    _edge("build", "test"),
                    _edge("test", "deploy"),
                    _edge("deploy", "ff", "enable flag"),
                    _edge("ff", "monitor", "observe"),
                ],
            }
        ),
    },
    {
        "id": "tpl-bluegreen-canary",
        "name": "Canary + Blue-Green Hybrid",
        "category": "Deployment",
        "description": "Canary to staging (automated), blue-green for production (zero-downtime cutover).",
        "tags": json.dumps(["canary", "blue-green", "zero-downtime", "deployment"]),
        "graph_json": json.dumps(
            {
                "nodes": [
                    _node("build", "Build + Scan", "build-runner", 50, 200),
                    _node("canary", "Canary (Staging)", "deploy-canary", 300, 200),
                    _node("auto-gate", "Auto Gate", "gate-automated", 550, 200),
                    _node("approve", "Manual Approve", "gate-manual", 750, 200),
                    _node("bg", "Blue-Green (Prod)", "deploy-bluegreen", 950, 200),
                    _node("mesh", "Istio", "mesh-istio", 950, 350),
                    _node("monitor", "Prometheus", "mon-prometheus", 1150, 200),
                ],
                "edges": [
                    _edge("build", "canary"),
                    _edge("canary", "auto-gate", "metrics ok"),
                    _edge("auto-gate", "approve"),
                    _edge("approve", "bg", "approved"),
                    _edge("mesh", "bg", "traffic routing"),
                    _edge("bg", "monitor"),
                ],
            }
        ),
    },
    {
        "id": "tpl-full-devsecops",
        "name": "Full DevSecOps (All Stages)",
        "category": "Enterprise",
        "description": "Complete DevSecOps pipeline: all 11 stages, all scan types, supply chain signing, progressive delivery, runtime monitoring, compliance evidence.",
        "tags": json.dumps(["full", "devsecops", "enterprise", "complete", "dod"]),
        "graph_json": json.dumps(
            {
                "nodes": [
                    # Pre-commit
                    _node("gitleaks", "Gitleaks", "scan-gitleaks", 50, 100),
                    # Source
                    _node("src", "GitLab", "scm-gitlab", 50, 250),
                    _node("bp", "Branch Policy", "branch-policy", 50, 400),
                    # Build
                    _node("ci", "Tekton", "cicd-tekton", 250, 250),
                    _node("sbom", "Syft SBOM", "sbom-syft", 250, 100),
                    # Test
                    _node("sast", "Semgrep", "scan-semgrep", 450, 100),
                    _node("sca", "Grype", "scan-grype", 450, 250),
                    _node("iac", "Checkov", "scan-checkov", 450, 400),
                    _node("dast", "OWASP ZAP", "scan-zap", 450, 550),
                    # Package
                    _node("trivy", "Trivy Image", "scan-trivy", 650, 200),
                    _node("sign", "Cosign", "sign-cosign", 650, 350),
                    _node("harbor", "Harbor", "registry-harbor", 850, 250),
                    _node("slsa", "SLSA Provenance", "attest-slsa-gen", 650, 500),
                    # Policy Gate
                    _node("gate", "Vuln Threshold", "gate-vuln-threshold", 1050, 250),
                    _node("kyverno", "Kyverno", "policy-kyverno", 1050, 400),
                    # Approval
                    _node("approve", "Manual Approval", "gate-manual", 1250, 250),
                    # Deploy
                    _node("staging", "Staging (K8s)", "k8s-cluster", 1450, 150),
                    _node("canary", "Canary (Prod)", "deploy-canary", 1450, 350),
                    _node("prod", "Production (K8s)", "k8s-cluster", 1650, 250),
                    # Service mesh
                    _node("istio", "Istio", "mesh-istio", 1650, 400),
                    # Monitor
                    _node("prom", "Prometheus", "mon-prometheus", 1850, 150),
                    _node("falco", "Falco", "mon-falco", 1850, 300),
                    _node("grafana", "Grafana", "mon-grafana", 1850, 450),
                    # Compliance
                    _node("evidence", "Evidence Locker", "comp-evidence", 2050, 200),
                    _node("oscal", "OSCAL Export", "comp-oscal", 2050, 350),
                ],
                "edges": [
                    _edge("gitleaks", "src", "pre-commit"),
                    _edge("bp", "src", "enforced"),
                    _edge("src", "ci", "push"),
                    _edge("ci", "sbom", "generate"),
                    _edge("ci", "sast"),
                    _edge("ci", "sca"),
                    _edge("ci", "iac"),
                    _edge("ci", "dast"),
                    _edge("sast", "trivy"),
                    _edge("sca", "trivy"),
                    _edge("iac", "trivy"),
                    _edge("trivy", "sign"),
                    _edge("sign", "harbor"),
                    _edge("slsa", "harbor", "provenance"),
                    _edge("harbor", "gate"),
                    _edge("kyverno", "gate", "policy"),
                    _edge("gate", "approve"),
                    _edge("approve", "staging"),
                    _edge("staging", "canary"),
                    _edge("canary", "prod"),
                    _edge("istio", "prod", "mTLS"),
                    _edge("prod", "prom"),
                    _edge("prod", "falco"),
                    _edge("prom", "grafana"),
                    _edge("falco", "evidence"),
                    _edge("evidence", "oscal"),
                ],
            }
        ),
    },
    {
        "id": "tpl-hybrid-multicloud",
        "name": "Hybrid Multi-Cloud Pipeline (NDC Bridge)",
        "category": "Multi-Cloud / Hybrid",
        "description": "On-premises data center connected to AWS GovCloud via Direct Connect, with a parallel Azure AKS deployment. Uses NDC topology bridge for infrastructure, Harbor for multi-cloud registry replication, and cross-domain guard for classification boundary.",
        "tags": json.dumps(["hybrid", "multi-cloud", "directconnect", "ndc-bridge", "cross-domain", "govcloud"]),
        "graph_json": json.dumps(
            {
                "nodes": [
                    # Infrastructure (NDC bridge layer)
                    _node(
                        "ndc",
                        "NDC Topology",
                        "ndc-topology",
                        50,
                        50,
                        {"config": {"ndc_topology_id": "", "ndc_topology_name": "Link your NDC topology"}},
                    ),
                    _node("dc", "On-Prem DC", "onprem-datacenter", 50, 200),
                    _node("dx", "Direct Connect", "hybrid-directconnect", 250, 200),
                    _node("vpn", "Azure VPN", "hybrid-vpn", 250, 350),
                    # On-prem pipeline
                    _node("src", "GitLab (On-Prem)", "scm-gitlab", 50, 400, {"config": {"airgap": False}}),
                    _node("ci", "GitLab CI", "cicd-gitlab", 250, 400),
                    _node("sast", "SonarQube", "scan-sonarqube", 450, 350),
                    _node("trivy", "Trivy", "scan-trivy", 450, 450),
                    _node("harbor", "Harbor (On-Prem)", "registry-harbor", 650, 400),
                    _node("sign", "Cosign", "sign-cosign", 650, 250),
                    # AWS GovCloud deployment
                    _node("ecr", "ECR (GovCloud)", "aws-ecr", 850, 200, {"config": {"boundary": "govcloud"}}),
                    _node("eks", "EKS (GovCloud)", "aws-eks", 1050, 200, {"config": {"boundary": "govcloud"}}),
                    _node("kyverno", "Kyverno", "policy-kyverno", 1050, 350),
                    # Azure Government deployment
                    _node("acr", "ACR (Azure Gov)", "az-acr", 850, 500, {"config": {"boundary": "govcloud"}}),
                    _node("aks", "AKS (Azure Gov)", "az-aks", 1050, 500, {"config": {"boundary": "govcloud"}}),
                    # Monitoring (multi-cloud)
                    _node("prom", "Prometheus", "mon-prometheus", 1250, 200),
                    _node("grafana", "Grafana", "mon-grafana", 1250, 350),
                    _node("falco", "Falco", "mon-falco", 1250, 500),
                ],
                "edges": [
                    # Infrastructure connectivity
                    _edge("dc", "dx", "dedicated circuit"),
                    _edge("dc", "vpn", "IPSec VPN"),
                    _edge("ndc", "dc", "infrastructure"),
                    # On-prem CI/CD
                    _edge("src", "ci", "push"),
                    _edge("ci", "sast"),
                    _edge("ci", "trivy"),
                    _edge("sast", "harbor"),
                    _edge("trivy", "harbor"),
                    _edge("harbor", "sign"),
                    # Multi-cloud deployment
                    _edge("dx", "ecr", "cross-boundary"),
                    _edge("sign", "ecr", "push image"),
                    _edge("ecr", "eks", "deploy"),
                    _edge("kyverno", "eks", "admission"),
                    _edge("vpn", "acr", "cross-boundary"),
                    _edge("sign", "acr", "push image"),
                    _edge("acr", "aks", "deploy"),
                    # Monitoring
                    _edge("eks", "prom", "metrics"),
                    _edge("aks", "grafana", "metrics"),
                    _edge("eks", "falco", "runtime"),
                    _edge("aks", "falco", "runtime"),
                ],
            }
        ),
    },
    {
        "id": "tpl-sre-full",
        "name": "Full SRE Platform (SLO + Incidents + Chaos + DORA)",
        "category": "SRE",
        "description": "Complete Site Reliability Engineering platform: SLO management with burn-rate alerting, incident lifecycle with postmortem, automated runbooks, chaos engineering with LitmusChaos, DORA metrics tracking, Prometheus+Grafana observability, and Backstage service catalog.",
        "tags": json.dumps(
            ["sre", "slo", "incident", "chaos", "dora", "runbook", "prometheus", "grafana", "backstage", "litmus"]
        ),
        "graph_json": json.dumps(
            {
                "nodes": [
                    # Observability layer (data collection)
                    _node("otel", "OpenTelemetry", "mon-otel", 50, 50),
                    _node("prom", "Prometheus", "mon-prometheus", 250, 50),
                    _node("loki", "Loki", "mon-loki", 250, 180),
                    _node("grafana", "Grafana", "mon-grafana", 450, 100),
                    # SLO management
                    _node("sli", "SLI Metrics", "sre-sli", 50, 300),
                    _node("openslo", "OpenSLO Spec", "sre-openslo", 250, 300),
                    _node("slo", "SLO Manager", "sre-slo", 450, 300),
                    _node("burn", "Burn Rate Alert", "sre-burn-rate", 650, 300),
                    _node("budget", "Error Budget", "sre-error-budget", 650, 180),
                    # Incident management
                    _node("incident", "Incident Commander", "sre-incident", 850, 200),
                    _node("oncall", "PagerDuty", "sre-pagerduty", 850, 50),
                    _node("postmortem", "Postmortem", "sre-postmortem", 1050, 200),
                    _node("status", "Status Page", "sre-statuspage", 1050, 50),
                    # Runbooks & self-healing
                    _node("runbook", "Runbook Executor", "sre-runbook", 850, 400),
                    _node("heal", "Self-Healing", "sre-self-heal", 1050, 400),
                    # Resilience patterns
                    _node("cb", "Circuit Breaker", "sre-circuit-breaker", 50, 450),
                    _node("retry", "Retry + Backoff", "sre-retry", 250, 450),
                    _node("bulkhead", "Bulkhead", "sre-bulkhead", 450, 450),
                    # Chaos engineering
                    _node("chaos", "Chaos Experiments", "sre-chaos", 650, 500),
                    _node("litmus", "LitmusChaos", "sre-chaos-litmus", 850, 550),
                    # DORA metrics
                    _node("dora", "DORA Dashboard", "sre-dora", 1250, 300),
                    _node("df", "Deploy Frequency", "sre-dora-deploy-freq", 1250, 150),
                    _node("lt", "Lead Time", "sre-dora-lead-time", 1250, 450),
                    _node("cfr", "Change Failure Rate", "sre-dora-cfr", 1450, 200),
                    _node("mttr", "MTTR", "sre-dora-mttr", 1450, 400),
                    # Service catalog
                    _node("backstage", "Backstage", "sre-backstage", 1450, 50),
                    # Resilience score
                    _node("resilience", "Resilience Score", "sre-resilience", 1250, 550),
                    # Deploy target
                    _node("k8s", "Kubernetes", "k8s-cluster", 650, 50, {"config": {"environment": "production"}}),
                ],
                "edges": [
                    # Observability flow
                    _edge("otel", "prom", "metrics"),
                    _edge("otel", "loki", "logs"),
                    _edge("prom", "grafana", "dashboards"),
                    _edge("loki", "grafana", "log queries"),
                    # SLO flow
                    _edge("prom", "sli", "SLI data"),
                    _edge("sli", "openslo", "metric spec"),
                    _edge("openslo", "slo", "SLO definition"),
                    _edge("slo", "burn", "burn rate"),
                    _edge("slo", "budget", "budget status"),
                    # SLO breach → incident
                    _edge("burn", "incident", "budget exhausted"),
                    # Incident flow
                    _edge("incident", "oncall", "page"),
                    _edge("incident", "postmortem", "SEV1/2 required"),
                    _edge("incident", "status", "communicate"),
                    _edge("incident", "runbook", "auto-respond"),
                    _edge("runbook", "heal", "auto-fix"),
                    # Chaos → resilience
                    _edge("chaos", "litmus", "experiments"),
                    _edge("litmus", "k8s", "fault inject"),
                    _edge("k8s", "prom", "metrics"),
                    # Resilience patterns → K8s
                    _edge("cb", "k8s", "protect"),
                    _edge("retry", "k8s", "retry calls"),
                    # DORA feeds
                    _edge("incident", "mttr", "resolution time"),
                    _edge("incident", "cfr", "failure event"),
                    _edge("dora", "df"),
                    _edge("dora", "lt"),
                    _edge("dora", "cfr"),
                    _edge("dora", "mttr"),
                    # Resilience aggregation
                    _edge("slo", "resilience", "SLO compliance"),
                    _edge("litmus", "resilience", "chaos results"),
                    _edge("dora", "resilience", "DORA score"),
                    # Backstage catalog
                    _edge("backstage", "slo", "service SLOs"),
                    _edge("backstage", "oncall", "who owns this?"),
                    _edge("backstage", "dora", "team metrics"),
                ],
            }
        ),
    },
    {
        "id": "tpl-devsecops-sre",
        "name": "DevSecOps + SRE Combined",
        "category": "Enterprise",
        "description": "Complete pipeline: CI/CD with security scanning AND SRE observability. Source → Build → SAST/SCA → Container scan → Sign → Deploy → SLO monitoring → Incident response → DORA tracking. The full picture.",
        "tags": json.dumps(["devsecops", "sre", "combined", "enterprise", "slo", "dora", "scanning", "deploy"]),
        "graph_json": json.dumps(
            {
                "nodes": [
                    # DevSecOps pipeline
                    _node("src", "GitLab", "scm-gitlab", 50, 200),
                    _node("ci", "Tekton", "cicd-tekton", 200, 200),
                    _node("sast", "Semgrep", "scan-semgrep", 350, 100),
                    _node("sca", "Trivy SCA", "scan-trivy", 350, 300),
                    _node("build", "Kaniko", "build-kaniko", 500, 200),
                    _node("scan", "Trivy Image", "scan-trivy", 650, 200),
                    _node("sbom", "Syft SBOM", "sbom-syft", 650, 350),
                    _node("sign", "Cosign", "sign-cosign", 800, 200),
                    _node("harbor", "Harbor", "registry-harbor", 950, 200),
                    _node("gate", "Vuln Gate", "gate-vuln-threshold", 950, 350),
                    _node("argo", "ArgoCD", "gitops-argocd", 1100, 200),
                    _node("k8s", "Kubernetes", "k8s-cluster", 1250, 200),
                    # SRE layer
                    _node("prom", "Prometheus", "mon-prometheus", 1250, 50),
                    _node("slo", "SLO Manager", "sre-slo", 1400, 50),
                    _node("burn", "Burn Rate", "sre-burn-rate", 1550, 50),
                    _node("incident", "Incident Mgr", "sre-incident", 1550, 200),
                    _node("runbook", "Runbook", "sre-runbook", 1550, 350),
                    _node("dora", "DORA Metrics", "sre-dora", 1400, 350),
                    _node("grafana", "Grafana", "mon-grafana", 1400, 200),
                ],
                "edges": [
                    _edge("src", "ci"),
                    _edge("ci", "sast"),
                    _edge("ci", "sca"),
                    _edge("sast", "build"),
                    _edge("sca", "build"),
                    _edge("build", "scan"),
                    _edge("build", "sbom"),
                    _edge("scan", "sign"),
                    _edge("sign", "harbor"),
                    _edge("harbor", "gate"),
                    _edge("gate", "argo"),
                    _edge("argo", "k8s"),
                    # SRE wiring
                    _edge("k8s", "prom", "metrics"),
                    _edge("prom", "slo", "SLI data"),
                    _edge("prom", "grafana", "dashboards"),
                    _edge("slo", "burn", "burn rate"),
                    _edge("burn", "incident", "budget breach"),
                    _edge("incident", "runbook", "auto-respond"),
                    _edge("ci", "dora", "pipeline events"),
                    _edge("incident", "dora", "incident data"),
                ],
            }
        ),
    },
    {
        "id": "tpl-zero-trust-pipeline",
        "name": "Zero Trust DevSecOps Pipeline",
        "category": "Security",
        "description": "Every stage enforced by policy: signed commits, branch protection, hermetic builds, signed artifacts, SLSA provenance, admission control, mTLS mesh, runtime monitoring. Trust nothing, verify everything.",
        "tags": json.dumps(["zero-trust", "security", "signed", "policy", "slsa", "mtls", "kyverno"]),
        "graph_json": json.dumps(
            {
                "nodes": [
                    _node("signing", "Commit Signing", "commit-signing", 50, 200),
                    _node("bp", "Branch Policy", "branch-policy", 50, 50),
                    _node("src", "GitLab", "scm-gitlab", 200, 200),
                    _node("ci", "Tekton (hermetic)", "cicd-tekton", 400, 200),
                    _node("sast", "CodeQL", "scan-codeql", 400, 50),
                    _node("secrets", "Gitleaks", "scan-gitleaks", 200, 50),
                    _node("vault", "Vault", "vault-hashicorp", 400, 350),
                    _node("build", "Bazel (hermetic)", "build-bazel", 600, 200),
                    _node("slsa", "SLSA Provenance", "attest-slsa-gen", 600, 50),
                    _node("sign", "Cosign", "sign-cosign", 800, 200),
                    _node("verify", "SLSA Verifier", "verify-slsa", 800, 50),
                    _node("harbor", "Harbor", "registry-harbor", 1000, 200),
                    _node("kyverno", "Kyverno", "policy-kyverno", 1000, 50),
                    _node("k8s", "K8s (restricted PSA)", "k8s-cluster", 1200, 200),
                    _node("istio", "Istio (mTLS)", "mesh-istio", 1200, 350),
                    _node("falco", "Falco", "mon-falco", 1400, 200),
                    _node("evidence", "Evidence Locker", "comp-evidence", 1400, 350),
                ],
                "edges": [
                    _edge("signing", "src", "signed commits"),
                    _edge("bp", "src", "enforce reviews"),
                    _edge("secrets", "src", "pre-commit"),
                    _edge("src", "ci"),
                    _edge("ci", "sast"),
                    _edge("ci", "vault", "inject secrets"),
                    _edge("ci", "build", "hermetic"),
                    _edge("build", "slsa", "provenance"),
                    _edge("build", "sign", "artifact"),
                    _edge("slsa", "verify"),
                    _edge("sign", "harbor"),
                    _edge("verify", "kyverno", "verified"),
                    _edge("kyverno", "k8s", "admission"),
                    _edge("harbor", "k8s", "pull"),
                    _edge("istio", "k8s", "mTLS mesh"),
                    _edge("k8s", "falco", "runtime"),
                    _edge("falco", "evidence", "audit"),
                ],
            }
        ),
    },
    {
        "id": "tpl-fedramp-cato",
        "name": "FedRAMP Continuous ATO Pipeline",
        "category": "Compliance",
        "description": "FedRAMP continuous monitoring pipeline: automated scanning, OSCAL evidence generation, SBOM tracking, STIG checks, compliance dashboard, and ATO package builder. Designed for FedRAMP Moderate/High with IL4+ classification.",
        "tags": json.dumps(["fedramp", "cato", "compliance", "oscal", "stig", "sbom", "il4", "continuous-monitoring"]),
        "graph_json": json.dumps(
            {
                "nodes": [
                    _node("src", "GitLab", "scm-gitlab", 50, 200),
                    _node("ci", "GitLab CI", "cicd-gitlab", 200, 200),
                    _node("sast", "SonarQube", "scan-sonarqube", 350, 100),
                    _node("sca", "Grype", "scan-grype", 350, 300),
                    _node("iac", "Checkov", "scan-checkov", 200, 350),
                    _node("secret", "detect-secrets", "scan-detect-secrets", 50, 50),
                    _node("sbom", "CycloneDX SBOM", "sbom-cyclonedx", 500, 200),
                    _node("sign", "Cosign", "sign-cosign", 650, 200),
                    _node("harbor", "Harbor", "registry-harbor", 800, 200),
                    # Compliance layer
                    _node("evidence", "Evidence Locker", "comp-evidence", 950, 100),
                    _node("oscal", "OSCAL Export", "comp-oscal", 950, 300),
                    _node("stig", "STIG Manager", "comp-stigman", 1100, 100),
                    _node("openscap", "OpenSCAP", "comp-openscap", 1100, 300),
                    _node("dashboard", "Compliance Dashboard", "comp-dashboard", 1250, 200),
                    # SRE (continuous monitoring)
                    _node("prom", "Prometheus", "mon-prometheus", 800, 400),
                    _node("slo", "SLO (99.9%)", "sre-slo", 950, 450),
                    _node("falco", "Falco", "mon-falco", 1100, 450),
                ],
                "edges": [
                    _edge("secret", "src", "pre-commit"),
                    _edge("src", "ci"),
                    _edge("ci", "sast"),
                    _edge("ci", "sca"),
                    _edge("ci", "iac"),
                    _edge("sast", "sbom"),
                    _edge("sca", "sbom"),
                    _edge("sbom", "sign"),
                    _edge("sign", "harbor"),
                    _edge("harbor", "evidence", "artifact evidence"),
                    _edge("sbom", "evidence", "SBOM evidence"),
                    _edge("evidence", "oscal", "generate OSCAL"),
                    _edge("oscal", "stig", "control mapping"),
                    _edge("oscal", "openscap", "scan results"),
                    _edge("stig", "dashboard"),
                    _edge("openscap", "dashboard"),
                    # Continuous monitoring
                    _edge("harbor", "prom", "registry metrics"),
                    _edge("prom", "slo"),
                    _edge("prom", "falco", "alert correlation"),
                    _edge("falco", "evidence", "security events"),
                ],
            }
        ),
    },
]


# ── Snippets (10 pre-built pipeline fragments) ──────────────────────────────

PIPELINE_SNIPPETS = [
    {
        "id": "snip-dod-software-factory",
        "name": "DoD Software Factory (Platform One)",
        "category": "DoD",
        "description": "Platform One pattern: GitLab → Iron Bank → Big Bang K8s with Twistlock, Cosign, OPA Gatekeeper, and STIG Manager.",
        "classification_level": "CUI",
        "impact_level": "IL4",
        "slsa_level": "L2",
        "ssdf_practices": json.dumps(["PO", "PS", "PW", "RV"]),
        "tags": json.dumps(["dod", "platform-one", "ironbank", "bigbang"]),
        "graph_json": json.dumps(
            {
                "nodes": [
                    _node("src", "GitLab (P1)", "scm-gitlab", 50, 200),
                    _node("ci", "GitLab CI", "cicd-gitlab", 250, 200),
                    _node("ib", "Iron Bank", "registry-ironbank", 250, 50),
                    _node("scan", "Twistlock/NeuVector", "scan-neuvector", 450, 200),
                    _node("sign", "Cosign", "sign-cosign", 650, 200),
                    _node("gate", "OPA Gatekeeper", "policy-gatekeeper", 650, 350),
                    _node("bb", "Big Bang", "deploy-bigbang", 850, 200),
                    _node("stig", "STIG Manager", "comp-stigman", 850, 350),
                ],
                "edges": [
                    _edge("ib", "ci", "base images"),
                    _edge("src", "ci"),
                    _edge("ci", "scan"),
                    _edge("scan", "sign"),
                    _edge("sign", "gate"),
                    _edge("gate", "bb"),
                    _edge("bb", "stig"),
                ],
            }
        ),
    },
    {
        "id": "snip-cncf-supply-chain",
        "name": "CNCF Secure Supply Chain",
        "category": "Cloud Native",
        "description": "Tekton + Harbor + Trivy + Cosign + in-toto + Kyverno + Falco. CNCF best practices for supply chain security.",
        "classification_level": "public",
        "impact_level": "IL2",
        "slsa_level": "L3",
        "ssdf_practices": json.dumps(["PS", "PW"]),
        "tags": json.dumps(["cncf", "supply-chain", "tekton", "harbor", "cosign"]),
        "graph_json": json.dumps(
            {
                "nodes": [
                    _node("ci", "Tekton", "cicd-tekton", 50, 200),
                    _node("trivy", "Trivy", "scan-trivy", 250, 200),
                    _node("harbor", "Harbor", "registry-harbor", 450, 200),
                    _node("cosign", "Cosign", "sign-cosign", 450, 350),
                    _node("intoto", "in-toto", "attest-in-toto", 450, 50),
                    _node("kyverno", "Kyverno", "policy-kyverno", 650, 200),
                    _node("falco", "Falco", "mon-falco", 850, 200),
                ],
                "edges": [
                    _edge("ci", "trivy"),
                    _edge("trivy", "harbor"),
                    _edge("harbor", "cosign"),
                    _edge("ci", "intoto", "attestation"),
                    _edge("cosign", "kyverno"),
                    _edge("kyverno", "falco"),
                ],
            }
        ),
    },
    {
        "id": "snip-gitlab-ultimate",
        "name": "GitLab Ultimate Self-Hosted",
        "category": "Self-Hosted",
        "description": "GitLab CI Ultimate with integrated SAST/DAST/SCA/Fuzz, Harbor registry, ArgoCD deployment.",
        "classification_level": "CUI",
        "impact_level": "IL4",
        "slsa_level": "L1",
        "ssdf_practices": json.dumps(["PW"]),
        "tags": json.dumps(["gitlab", "ultimate", "self-hosted", "integrated"]),
        "graph_json": json.dumps(
            {
                "nodes": [
                    _node("src", "GitLab", "scm-gitlab", 50, 200),
                    _node("ci", "GitLab CI Ultimate", "cicd-gitlab", 250, 200),
                    _node("sast", "GL SAST", "scan-sast", 250, 50),
                    _node("dast", "GL DAST", "scan-dast", 250, 350),
                    _node("sca", "GL SCA", "scan-sca", 450, 50),
                    _node("harbor", "Harbor", "registry-harbor", 450, 200),
                    _node("argo", "ArgoCD", "gitops-argocd", 650, 200),
                ],
                "edges": [
                    _edge("src", "ci"),
                    _edge("ci", "sast"),
                    _edge("ci", "dast"),
                    _edge("ci", "sca"),
                    _edge("ci", "harbor"),
                    _edge("harbor", "argo"),
                ],
            }
        ),
    },
    {
        "id": "snip-aws-codepipeline",
        "name": "AWS CodePipeline + Inspector",
        "category": "AWS",
        "description": "AWS-native pipeline: CodeCommit → CodeBuild → ECR → Inspector → CodeDeploy to EKS.",
        "classification_level": "CUI",
        "impact_level": "IL4",
        "slsa_level": "L1",
        "ssdf_practices": json.dumps(["PW"]),
        "tags": json.dumps(["aws", "codepipeline", "inspector", "ecr", "eks"]),
        "graph_json": json.dumps(
            {
                "nodes": [
                    _node("src", "CodeCommit", "aws-codecommit", 50, 200),
                    _node("pipe", "CodePipeline", "aws-codepipeline", 250, 200),
                    _node("build", "CodeBuild", "aws-codebuild", 450, 200),
                    _node("ecr", "ECR", "aws-ecr", 650, 200),
                    _node("insp", "Inspector", "aws-inspector", 650, 350),
                    _node("deploy", "CodeDeploy", "aws-codedeploy", 850, 200),
                    _node("eks", "EKS", "aws-eks", 1050, 200),
                ],
                "edges": [
                    _edge("src", "pipe"),
                    _edge("pipe", "build"),
                    _edge("build", "ecr"),
                    _edge("ecr", "insp", "scan"),
                    _edge("insp", "deploy", "pass"),
                    _edge("deploy", "eks"),
                ],
            }
        ),
    },
    {
        "id": "snip-azure-devops",
        "name": "Azure DevOps + Defender",
        "category": "Azure",
        "description": "Azure-native: Azure Repos → Pipelines → ACR → Defender for Containers → AKS with Azure Policy.",
        "classification_level": "CUI",
        "impact_level": "IL4",
        "slsa_level": "L1",
        "ssdf_practices": json.dumps(["PW"]),
        "tags": json.dumps(["azure", "pipelines", "defender", "acr", "aks"]),
        "graph_json": json.dumps(
            {
                "nodes": [
                    _node("src", "Azure Repos", "az-repos", 50, 200),
                    _node("pipe", "Azure Pipelines", "az-pipelines", 250, 200),
                    _node("acr", "ACR", "az-acr", 450, 200),
                    _node("defender", "Defender", "az-defender", 450, 350),
                    _node("policy", "Azure Policy", "az-policy", 650, 350),
                    _node("aks", "AKS", "az-aks", 650, 200),
                ],
                "edges": [
                    _edge("src", "pipe"),
                    _edge("pipe", "acr"),
                    _edge("acr", "defender"),
                    _edge("defender", "aks"),
                    _edge("policy", "aks", "governance"),
                ],
            }
        ),
    },
    {
        "id": "snip-gcp-binary-auth",
        "name": "GCP Cloud Build + Binary Authorization",
        "category": "GCP",
        "description": "GCP-native: Cloud Source → Cloud Build (SLSA L3) → Artifact Registry → Binary Authorization → GKE.",
        "classification_level": "CUI",
        "impact_level": "IL4",
        "slsa_level": "L3",
        "ssdf_practices": json.dumps(["PS", "PW"]),
        "tags": json.dumps(["gcp", "cloud-build", "binary-authorization", "slsa"]),
        "graph_json": json.dumps(
            {
                "nodes": [
                    _node("src", "Cloud Source", "gcp-source", 50, 200),
                    _node("build", "Cloud Build", "gcp-cloudbuild", 250, 200),
                    _node("gar", "Artifact Registry", "gcp-gar", 450, 200),
                    _node("aa", "Artifact Analysis", "gcp-artifact-analysis", 450, 350),
                    _node("ba", "Binary Auth", "gcp-binary-auth", 650, 200),
                    _node("gke", "GKE", "gcp-gke", 850, 200),
                ],
                "edges": [
                    _edge("src", "build"),
                    _edge("build", "gar"),
                    _edge("gar", "aa", "scan"),
                    _edge("aa", "ba", "attestation"),
                    _edge("ba", "gke", "verified"),
                ],
            }
        ),
    },
    {
        "id": "snip-minimal-startup",
        "name": "Minimal Startup CI/CD",
        "category": "Getting Started",
        "description": "Bare minimum CI/CD for startups: GitHub Actions + Docker Hub + basic tests. No security scanning overhead.",
        "classification_level": "public",
        "impact_level": "IL2",
        "slsa_level": "L0",
        "ssdf_practices": json.dumps([]),
        "tags": json.dumps(["minimal", "startup", "github-actions", "basic"]),
        "graph_json": json.dumps(
            {
                "nodes": [
                    _node("gh", "GitHub Actions", "cicd-github-actions", 50, 200),
                    _node("test", "Tests", "scan-sast", 250, 200),
                    _node("deploy", "Deploy", "deploy-serverless", 450, 200),
                ],
                "edges": [
                    _edge("gh", "test"),
                    _edge("test", "deploy"),
                ],
            }
        ),
    },
    {
        "id": "snip-airgap-il5",
        "name": "Air-Gapped IL5/IL6 Pipeline",
        "category": "DoD",
        "description": "Complete air-gapped pipeline with offline vulnerability DB, package mirror, sneakernet transfer, FIPS crypto, and manual approval gates.",
        "classification_level": "SECRET",
        "impact_level": "IL6",
        "slsa_level": "L2",
        "ssdf_practices": json.dumps(["PO", "PS", "PW", "RV"]),
        "tags": json.dumps(["airgap", "il5", "il6", "secret", "fips", "sneakernet"]),
        "graph_json": json.dumps(
            {
                "nodes": [
                    _node("diode", "Data Diode", "cds-data-diode", 50, 50),
                    _node("sneaker", "Sneakernet", "sneakernet", 50, 350),
                    _node("mirror", "Package Mirror", "package-mirror", 250, 50),
                    _node("vulndb", "Vuln DB Mirror", "vuln-db-mirror", 250, 350),
                    _node(
                        "src", "GitLab (SIPR)", "scm-gitlab", 250, 200, {"config": {"network": "SIPR", "fips": True}}
                    ),
                    _node("ci", "GitLab CI", "cicd-gitlab", 450, 200),
                    _node("scan", "Trivy (offline)", "scan-trivy", 650, 200),
                    _node("harbor", "Harbor", "registry-harbor", 850, 200),
                    _node("gate", "Manual Gate", "gate-manual", 1050, 200),
                    _node("rke2", "RKE2", "rke2", 1250, 200),
                ],
                "edges": [
                    _edge("diode", "mirror", "one-way transfer"),
                    _edge("sneaker", "vulndb", "media transfer"),
                    _edge("mirror", "ci", "deps"),
                    _edge("vulndb", "scan", "offline DB"),
                    _edge("src", "ci"),
                    _edge("ci", "scan"),
                    _edge("scan", "harbor"),
                    _edge("harbor", "gate"),
                    _edge("gate", "rke2"),
                ],
            }
        ),
    },
    {
        "id": "snip-multi-cloud-federated",
        "name": "Multi-Cloud Federated Pipeline",
        "category": "Multi-Cloud",
        "description": "Tekton-based pipeline with multi-cluster deployment to EKS + AKS + GKE via ArgoCD federation.",
        "classification_level": "CUI",
        "impact_level": "IL4",
        "slsa_level": "L2",
        "ssdf_practices": json.dumps(["PS", "PW"]),
        "tags": json.dumps(["multi-cloud", "federated", "eks", "aks", "gke"]),
        "graph_json": json.dumps(
            {
                "nodes": [
                    _node("ci", "Tekton", "cicd-tekton", 50, 250),
                    _node("scan", "Trivy", "scan-trivy", 250, 250),
                    _node("harbor", "Harbor", "registry-harbor", 450, 250),
                    _node("argo", "ArgoCD", "gitops-argocd", 650, 250),
                    _node("eks", "EKS", "aws-eks", 900, 100),
                    _node("aks", "AKS", "az-aks", 900, 250),
                    _node("gke", "GKE", "gcp-gke", 900, 400),
                ],
                "edges": [
                    _edge("ci", "scan"),
                    _edge("scan", "harbor"),
                    _edge("harbor", "argo"),
                    _edge("argo", "eks"),
                    _edge("argo", "aks"),
                    _edge("argo", "gke"),
                ],
            }
        ),
    },
    {
        "id": "snip-ssdf-compliant",
        "name": "NIST SSDF Compliant Pipeline",
        "category": "Compliance",
        "description": "Full SSDF (SP 800-218) compliance: all 4 practice areas mapped with evidence collection, OSCAL export, and continuous monitoring.",
        "classification_level": "CUI",
        "impact_level": "IL4",
        "slsa_level": "L2",
        "ssdf_practices": json.dumps(["PO", "PS", "PW", "RV"]),
        "tags": json.dumps(["ssdf", "nist", "800-218", "compliance", "evidence"]),
        "graph_json": json.dumps(
            {
                "nodes": [
                    _node("secrets", "detect-secrets (PO.3)", "scan-detect-secrets", 50, 100),
                    _node("src", "Git (branch protect, PS.1)", "scm-gitlab", 50, 250),
                    _node("review", "Code Review (PW.7)", "branch-policy", 50, 400),
                    _node("sast", "SAST (PW.7)", "scan-semgrep", 300, 100),
                    _node("sca", "SCA (PW.4)", "scan-grype", 300, 250),
                    _node("build", "Build + SBOM (PS.3)", "build-kaniko", 550, 200),
                    _node("sign", "Sign (PS.2)", "sign-cosign", 750, 200),
                    _node("deploy", "Deploy", "k8s-cluster", 950, 200),
                    _node("monitor", "Monitor (RV.1)", "mon-falco", 1150, 200),
                    _node("evidence", "Evidence (RV.2)", "comp-evidence", 1150, 350),
                    _node("oscal", "OSCAL (RV.3)", "comp-oscal", 1350, 350),
                ],
                "edges": [
                    _edge("secrets", "src"),
                    _edge("review", "src"),
                    _edge("src", "sast"),
                    _edge("src", "sca"),
                    _edge("sast", "build"),
                    _edge("sca", "build"),
                    _edge("build", "sign"),
                    _edge("sign", "deploy"),
                    _edge("deploy", "monitor"),
                    _edge("monitor", "evidence"),
                    _edge("evidence", "oscal"),
                ],
            }
        ),
    },
]


# ── SRE Snippets (composable building blocks) ───────────────────────────────

SRE_SNIPPETS = [
    {
        "id": "snip-sre-slo-monitoring",
        "name": "SLO Monitoring Stack",
        "category": "SRE",
        "description": "Add SLO tracking to any pipeline: Prometheus collects SLI metrics, OpenSLO defines targets, burn rate alerting detects budget exhaustion, Grafana visualizes compliance.",
        "classification_level": "CUI",
        "impact_level": "IL4",
        "slsa_level": "L0",
        "ssdf_practices": json.dumps(["RV"]),
        "tags": json.dumps(["sre", "slo", "prometheus", "grafana", "burn-rate", "error-budget", "observability"]),
        "graph_json": json.dumps(
            {
                "nodes": [
                    _node("prom", "Prometheus", "mon-prometheus", 50, 150),
                    _node("sli", "SLI Metrics", "sre-sli", 250, 50),
                    _node("openslo", "OpenSLO Spec", "sre-openslo", 250, 250),
                    _node("slo", "SLO Manager", "sre-slo", 450, 150),
                    _node("burn", "Burn Rate Alert", "sre-burn-rate", 650, 50),
                    _node("budget", "Error Budget", "sre-error-budget", 650, 250),
                    _node("grafana", "Grafana", "mon-grafana", 850, 150),
                ],
                "edges": [
                    _edge("prom", "sli", "scrape metrics"),
                    _edge("sli", "openslo", "metric spec"),
                    _edge("openslo", "slo", "define SLO"),
                    _edge("slo", "burn", "calculate burn rate"),
                    _edge("slo", "budget", "track budget"),
                    _edge("burn", "grafana", "alert dashboard"),
                    _edge("budget", "grafana", "budget gauge"),
                ],
            }
        ),
    },
    {
        "id": "snip-sre-incident-response",
        "name": "Incident Response Chain",
        "category": "SRE",
        "description": "Complete incident management: auto-create incidents from alerts, page on-call via PagerDuty, trigger automated runbooks, execute self-healing, and generate postmortem reports.",
        "classification_level": "CUI",
        "impact_level": "IL4",
        "slsa_level": "L0",
        "ssdf_practices": json.dumps(["RV"]),
        "tags": json.dumps(["sre", "incident", "pagerduty", "runbook", "self-heal", "postmortem", "on-call"]),
        "graph_json": json.dumps(
            {
                "nodes": [
                    _node("alert", "Burn Rate Alert", "sre-burn-rate", 50, 150),
                    _node("incident", "Incident Commander", "sre-incident", 250, 150),
                    _node("oncall", "PagerDuty", "sre-pagerduty", 250, 0),
                    _node("status", "Status Page", "sre-statuspage", 250, 300),
                    _node("runbook", "Runbook Executor", "sre-runbook", 500, 150),
                    _node("heal", "Self-Healing", "sre-self-heal", 700, 150),
                    _node("postmortem", "Postmortem", "sre-postmortem", 500, 300),
                ],
                "edges": [
                    _edge("alert", "incident", "budget breach"),
                    _edge("incident", "oncall", "page on-call"),
                    _edge("incident", "status", "update status"),
                    _edge("incident", "runbook", "auto-respond"),
                    _edge("runbook", "heal", "execute fix"),
                    _edge("incident", "postmortem", "SEV1/2 required"),
                ],
            }
        ),
    },
    {
        "id": "snip-sre-chaos-engineering",
        "name": "Chaos Engineering Kit",
        "category": "SRE",
        "description": "Validate resilience through controlled failure injection: LitmusChaos on Kubernetes with steady-state hypothesis, SLO-based stop conditions, and resilience scoring.",
        "classification_level": "CUI",
        "impact_level": "IL4",
        "slsa_level": "L0",
        "ssdf_practices": json.dumps([]),
        "tags": json.dumps(["sre", "chaos", "litmus", "resilience", "fault-injection", "gameday"]),
        "graph_json": json.dumps(
            {
                "nodes": [
                    _node("slo", "SLO (stop condition)", "sre-slo", 50, 50),
                    _node("chaos", "Chaos Experiments", "sre-chaos", 50, 200),
                    _node("litmus", "LitmusChaos", "sre-chaos-litmus", 300, 200),
                    _node("k8s", "K8s Target", "k8s-cluster", 550, 200),
                    _node("prom", "Prometheus", "mon-prometheus", 550, 50),
                    _node("score", "Resilience Score", "sre-resilience", 800, 150),
                ],
                "edges": [
                    _edge("chaos", "litmus", "define experiment"),
                    _edge("litmus", "k8s", "inject fault"),
                    _edge("k8s", "prom", "observe metrics"),
                    _edge("slo", "litmus", "stop if SLO breach"),
                    _edge("prom", "score", "health data"),
                    _edge("litmus", "score", "experiment results"),
                ],
            }
        ),
    },
    {
        "id": "snip-sre-dora-metrics",
        "name": "DORA Metrics Add-on",
        "category": "SRE",
        "description": "Track the 4 DORA key metrics: deployment frequency, lead time for changes, change failure rate, and mean time to restore. The bridge between DevOps velocity and reliability.",
        "classification_level": "CUI",
        "impact_level": "IL4",
        "slsa_level": "L0",
        "ssdf_practices": json.dumps([]),
        "tags": json.dumps(["sre", "dora", "metrics", "deployment-frequency", "lead-time", "cfr", "mttr"]),
        "graph_json": json.dumps(
            {
                "nodes": [
                    _node(
                        "ci", "CI/CD Engine", "cicd-gitlab", 50, 150, {"config": {"note": "Source of deploy events"}}
                    ),
                    _node("incidents", "Incident Commander", "sre-incident", 50, 300),
                    _node("dora", "DORA Dashboard", "sre-dora", 350, 200),
                    _node("df", "Deploy Frequency", "sre-dora-deploy-freq", 600, 50),
                    _node("lt", "Lead Time", "sre-dora-lead-time", 600, 150),
                    _node("cfr", "Change Failure Rate", "sre-dora-cfr", 600, 250),
                    _node("mttr", "MTTR", "sre-dora-mttr", 600, 350),
                    _node("backstage", "Backstage", "sre-backstage", 350, 400),
                ],
                "edges": [
                    _edge("ci", "dora", "pipeline events"),
                    _edge("incidents", "dora", "incident data"),
                    _edge("dora", "df"),
                    _edge("dora", "lt"),
                    _edge("dora", "cfr"),
                    _edge("dora", "mttr"),
                    _edge("dora", "backstage", "team dashboard"),
                ],
            }
        ),
    },
    {
        "id": "snip-sre-resilience-patterns",
        "name": "Resilience Patterns",
        "category": "SRE",
        "description": "Microservice resilience building blocks: circuit breaker prevents cascade failures, retry with exponential backoff handles transient errors, bulkhead isolates resource pools.",
        "classification_level": "CUI",
        "impact_level": "IL4",
        "slsa_level": "L0",
        "ssdf_practices": json.dumps([]),
        "tags": json.dumps(["sre", "resilience", "circuit-breaker", "retry", "bulkhead", "microservices"]),
        "graph_json": json.dumps(
            {
                "nodes": [
                    _node(
                        "cb",
                        "Circuit Breaker",
                        "sre-circuit-breaker",
                        50,
                        100,
                        {"config": {"failure_threshold": 5, "recovery_timeout": 30}},
                    ),
                    _node(
                        "retry",
                        "Retry + Backoff",
                        "sre-retry",
                        50,
                        250,
                        {"config": {"max_retries": 3, "base_delay": 1.0}},
                    ),
                    _node("bulk", "Bulkhead", "sre-bulkhead", 50, 400, {"config": {"max_concurrent": 10}}),
                    _node("k8s", "K8s Services", "k8s-cluster", 350, 250),
                    _node("score", "Resilience Score", "sre-resilience", 600, 250),
                    _node("grafana", "Grafana", "mon-grafana", 600, 100),
                ],
                "edges": [
                    _edge("cb", "k8s", "protect calls"),
                    _edge("retry", "k8s", "retry transient"),
                    _edge("bulk", "k8s", "isolate pools"),
                    _edge("k8s", "score", "health metrics"),
                    _edge("score", "grafana", "resilience dashboard"),
                ],
            }
        ),
    },
]

# ── Cross-Domain Pipeline Snippets ───────────────────────────────────────────

CROSS_DOMAIN_SNIPPETS = [
    {
        "id": "snip-cross-domain-deploy",
        "name": "Cross-Domain Deployment (Commercial → GovCloud)",
        "category": "Cross-Domain",
        "description": "Pipeline spanning commercial and GovCloud boundaries with CDS guard, data diode, and separate registries per domain.",
        "classification_level": "CUI",
        "impact_level": "IL5",
        "slsa_level": "L2",
        "ssdf_practices": json.dumps(["PO", "PS", "PW"]),
        "tags": json.dumps(["cross-domain", "govcloud", "cds", "data-diode", "multi-boundary"]),
        "graph_json": json.dumps(
            {
                "nodes": [
                    # Commercial side
                    _node(
                        "src-comm", "GitLab (Commercial)", "scm-gitlab", 50, 200, {"config": {"boundary": "commercial"}}
                    ),
                    _node("ci-comm", "CI (Commercial)", "cicd-gitlab", 250, 200),
                    _node("reg-comm", "Harbor (Commercial)", "registry-harbor", 450, 200),
                    # Cross-domain boundary
                    _node("cds", "Cross-Domain Guard", "cds-guard", 650, 200),
                    _node("emulator", "CDS Emulator (dev)", "cds-emulator", 650, 350),
                    # GovCloud side
                    _node(
                        "reg-gov",
                        "Harbor (GovCloud)",
                        "registry-harbor",
                        850,
                        200,
                        {"config": {"boundary": "govcloud"}},
                    ),
                    _node("scan-gov", "Trivy (GovCloud)", "scan-trivy", 1050, 200),
                    _node("gate-gov", "Policy Gate", "gate-vuln-threshold", 1250, 200),
                    _node("k8s-gov", "EKS (GovCloud)", "aws-eks", 1450, 200, {"config": {"boundary": "govcloud"}}),
                ],
                "edges": [
                    _edge("src-comm", "ci-comm"),
                    _edge("ci-comm", "reg-comm"),
                    _edge("reg-comm", "cds", "cross boundary"),
                    _edge("emulator", "cds", "dev/test path"),
                    _edge("cds", "reg-gov", "approved transfer"),
                    _edge("reg-gov", "scan-gov"),
                    _edge("scan-gov", "gate-gov"),
                    _edge("gate-gov", "k8s-gov"),
                ],
            }
        ),
    },
]


def init_db():
    """Create schema and seed templates/snippets."""
    conn = get_connection()
    try:
        # Create schema
        for stmt in SCHEMA.strip().split(";"):
            stmt = stmt.strip()
            if stmt:
                conn.execute(stmt)
        conn.commit()

        # Seed templates (skip if already seeded)
        existing = conn.execute("SELECT COUNT(*) FROM pc_templates").fetchone()[0]
        if existing == 0:
            for tpl in TEMPLATES:
                conn.execute(
                    "INSERT INTO pc_templates (id, name, category, description, graph_json, tags) "
                    "VALUES (%s, %s, %s, %s, %s, %s)",
                    (tpl["id"], tpl["name"], tpl["category"], tpl["description"], tpl["graph_json"], tpl["tags"]),
                )
            conn.commit()

        # Seed snippets (skip if already seeded)
        existing = conn.execute("SELECT COUNT(*) FROM pc_snippets").fetchone()[0]
        if existing == 0:
            all_snippets = PIPELINE_SNIPPETS + SRE_SNIPPETS + CROSS_DOMAIN_SNIPPETS
            for snip in all_snippets:
                conn.execute(
                    "INSERT INTO pc_snippets "
                    "(id, name, category, description, classification_level, impact_level, "
                    "slsa_level, ssdf_practices, graph_json, tags) "
                    "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
                    (
                        snip["id"],
                        snip["name"],
                        snip["category"],
                        snip["description"],
                        snip["classification_level"],
                        snip["impact_level"],
                        snip["slsa_level"],
                        snip["ssdf_practices"],
                        snip["graph_json"],
                        snip["tags"],
                    ),
                )
            conn.commit()

    finally:
        conn.close()


if __name__ == "__main__":
    import sys

    init_db()
    conn = get_connection()
    tpl_count = conn.execute("SELECT COUNT(*) FROM pc_templates").fetchone()[0]
    snip_count = conn.execute("SELECT COUNT(*) FROM pc_snippets").fetchone()[0]
    conn.close()
    result = {
        "status": "ok",
        "database": str(DB_PATH),
        "templates": tpl_count,
        "snippets": snip_count,
    }
    if "--json" in sys.argv:
        print(json.dumps(result, indent=2))
    else:
        print(f"Pipeline Canvas DB initialized: {DB_PATH}")
        print(f"  Templates: {tpl_count}")
        print(f"  Snippets:  {snip_count}")
