# CUI // SP-CTI
"""Quality Design Canvas — DB initializer.

Creates schema and seeds canonical templates, snippets, runbooks, and SOPs.
Dual-backend: SQLite (default) or PostgreSQL.
"""

import json
import os
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path

_ICDEV_ROOT = Path(__file__).resolve().parents[3]
DB_PATH = _ICDEV_ROOT / "data" / "qdc_canvas.db"

_QDC_BACKEND = os.environ.get("QDC_STORAGE_BACKEND", os.environ.get("ICDEV_CANVAS_STORAGE_BACKEND", os.environ.get("ICDEV_STORAGE_BACKEND", "postgresql"))).lower()


def get_connection():
    """Get a database connection — SQLite or PostgreSQL.

    Uses get_canvas_connection() for PostgreSQL because qdc_* tables have no
    tenant_id/classification columns; get_connection() would inject RLS and
    raise UndefinedColumn.
    """
    if _QDC_BACKEND == "postgresql":
        try:
            from tools.db.storage import get_canvas_connection

            return get_canvas_connection("QDC_PG_DATABASE")
        except Exception:
            pass
    # SQLite (default) — per-canvas DB, distinct from icdev.db
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    try:
        # Wrap so the %s placeholders used across init_db + blueprint + IQE
        # adapter translate to ? on SQLite (mirrors tools/security_canvas). Without
        # this the raw sqlite3 connection raises "near %: syntax error" on every
        # seed/query, and the canvas is unusable on the SQLite backend.
        from tools.db.storage import StorageConnection

        return StorageConnection(conn, "sqlite")
    except Exception:
        return conn


SCHEMA = """
CREATE TABLE IF NOT EXISTS qdc_designs (
    id              TEXT PRIMARY KEY,
    name            TEXT NOT NULL,
    description     TEXT,
    graph_json      TEXT NOT NULL DEFAULT '{"nodes":[],"edges":[]}',
    template_id     TEXT,
    project_id      TEXT,
    classification  TEXT DEFAULT 'CUI',
    created_at      TEXT DEFAULT CURRENT_TIMESTAMP,
    updated_at      TEXT DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS qdc_templates (
    id            TEXT PRIMARY KEY,
    name          TEXT NOT NULL,
    category      TEXT,
    description   TEXT,
    graph_json    TEXT NOT NULL DEFAULT '{"nodes":[],"edges":[]}',
    compliance_target TEXT,
    tags          TEXT DEFAULT '[]'
);

CREATE TABLE IF NOT EXISTS qdc_snippets (
    id          TEXT PRIMARY KEY,
    name        TEXT NOT NULL,
    category    TEXT,
    description TEXT,
    graph_json  TEXT NOT NULL DEFAULT '{"nodes":[],"edges":[]}',
    node_count  INTEGER DEFAULT 0,
    tags        TEXT DEFAULT '[]'
);

CREATE TABLE IF NOT EXISTS qdc_assessments (
    id              TEXT PRIMARY KEY,
    design_id       TEXT NOT NULL,
    assessment_type TEXT DEFAULT 'quality',
    findings_json   TEXT DEFAULT '[]',
    score           REAL DEFAULT 0.0,
    uqs_score       REAL DEFAULT 0.0,
    uqs_breakdown   TEXT DEFAULT '{}',
    sa11_mapping    TEXT DEFAULT '{}',
    created_at      TEXT DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS qdc_versions (
    id              TEXT PRIMARY KEY,
    design_id       TEXT REFERENCES qdc_designs(id),
    version_number  INTEGER NOT NULL,
    graph_json      TEXT NOT NULL DEFAULT '{"nodes":[],"edges":[]}',
    change_summary  TEXT DEFAULT '',
    user_id         TEXT DEFAULT '',
    created_at      TEXT DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_qdc_versions_design ON qdc_versions(design_id);

CREATE TABLE IF NOT EXISTS qdc_audit (
    id              TEXT PRIMARY KEY,
    design_id       TEXT,
    "user"          TEXT,
    action          TEXT,
    detail          TEXT,
    classification  TEXT DEFAULT 'CUI',
    created_at      TEXT DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS qdc_runbooks (
    id                   TEXT PRIMARY KEY,
    name                 TEXT NOT NULL,
    trigger_gate         TEXT,
    steps_json           TEXT DEFAULT '[]',
    body_markdown        TEXT DEFAULT '',
    auto_executable      INTEGER DEFAULT 0,
    confidence_threshold REAL DEFAULT 0.7,
    last_run             TEXT,
    run_count            INTEGER DEFAULT 0,
    created_at           TEXT DEFAULT CURRENT_TIMESTAMP,
    updated_at           TEXT DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS qdc_sops (
    id              TEXT PRIMARY KEY,
    sop_number      TEXT NOT NULL,
    title           TEXT NOT NULL,
    version         INTEGER DEFAULT 1,
    frequency       TEXT,
    audience        TEXT,
    body_markdown   TEXT DEFAULT '',
    approval_status TEXT DEFAULT 'draft',
    approved_by     TEXT,
    approved_at     TEXT,
    classification  TEXT DEFAULT 'CUI',
    created_at      TEXT DEFAULT CURRENT_TIMESTAMP,
    updated_at      TEXT DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS qdc_gate_results (
    id              TEXT PRIMARY KEY,
    design_id       TEXT NOT NULL,
    gate_id         TEXT NOT NULL,
    sa11_control    TEXT,
    status          TEXT DEFAULT 'skip',
    evidence_json   TEXT DEFAULT '{}',
    oscal_artifact  TEXT DEFAULT '{}',
    executed_at     TEXT DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_qdc_gate_design ON qdc_gate_results(design_id);

CREATE TABLE IF NOT EXISTS qdc_uqs_history (
    id              TEXT PRIMARY KEY,
    design_id       TEXT NOT NULL,
    uqs_score       REAL DEFAULT 0.0,
    dimension_scores TEXT DEFAULT '{}',
    computed_at     TEXT DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_qdc_uqs_design ON qdc_uqs_history(design_id);

CREATE TABLE IF NOT EXISTS qdc_cross_canvas_links (
    id                TEXT PRIMARY KEY,
    design_id         TEXT NOT NULL,
    source_canvas     TEXT NOT NULL,
    source_design_id  TEXT,
    quality_score     REAL DEFAULT 0.0,
    last_synced       TEXT DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS qdc_collab_sessions (
    id          TEXT PRIMARY KEY,
    design_id   TEXT NOT NULL REFERENCES qdc_designs(id) ON DELETE CASCADE,
    user_id     TEXT NOT NULL,
    user_name   TEXT NOT NULL DEFAULT '',
    color       TEXT NOT NULL DEFAULT '#2196f3',
    joined_at   TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    last_seen   TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    is_active   INTEGER NOT NULL DEFAULT 1
);

CREATE INDEX IF NOT EXISTS idx_qdc_collab_design ON qdc_collab_sessions(design_id);

CREATE TABLE IF NOT EXISTS qdc_collab_ops (
    id          TEXT PRIMARY KEY,
    design_id   TEXT NOT NULL,
    seq         INTEGER NOT NULL DEFAULT 0,
    session_id  TEXT,
    user_id     TEXT,
    operation   TEXT NOT NULL DEFAULT '{}',
    created_at  TEXT DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_qdc_collab_ops_design ON qdc_collab_ops(design_id, seq);

-- QDC digital twin (twx-cov-01). Snapshots follow the PDC retention pattern
-- (sha256 dedup + bounded auto-snapshot retention) — NOT append-only. No
-- tenant_id/classification columns: canvas tables, accessed via QDC's
-- get_canvas_connection()-backed get_connection().
CREATE TABLE IF NOT EXISTS qdc_twin_snapshots (
    id          TEXT PRIMARY KEY,
    design_id   TEXT NOT NULL,
    label       TEXT,
    graph_json  TEXT NOT NULL DEFAULT '{"nodes":[],"edges":[]}',
    node_count  INTEGER DEFAULT 0,
    edge_count  INTEGER DEFAULT 0,
    created_by  TEXT,
    created_at  TEXT DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_qdc_twin_snapshots_design ON qdc_twin_snapshots(design_id);

CREATE TABLE IF NOT EXISTS qdc_simulations (
    id                TEXT PRIMARY KEY,
    design_id         TEXT NOT NULL,
    baseline_snap_id  TEXT,
    delta_graph_json  TEXT NOT NULL DEFAULT '{"nodes":[],"edges":[]}',
    verdict           TEXT NOT NULL DEFAULT 'unknown',
    findings_json     TEXT DEFAULT '[]',
    diff_json         TEXT DEFAULT '{}',
    created_by        TEXT,
    created_at        TEXT DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_qdc_simulations_design ON qdc_simulations(design_id);
"""


# ── Helpers ──────────────────────────────────────────────────────────────────


def _uid():
    return uuid.uuid4().hex[:10]


def _now():
    return datetime.now(timezone.utc).isoformat()


def _node(ntype, label, x, y, **kwargs):
    n = {
        "id": f"n-{_uid()}",
        "type": ntype,
        "label": label,
        "x": x,
        "y": y,
        "w": kwargs.get("w", 130),
        "h": kwargs.get("h", 50),
    }
    n.update({k: v for k, v in kwargs.items() if k not in ("w", "h")})
    return n


def _edge(src, tgt, label=""):
    return {"id": f"e-{_uid()}", "source": src, "target": tgt, "label": label}


# ── Templates ────────────────────────────────────────────────────────────────


def _seed_templates(conn):
    """Seed 5 QA/QC templates."""
    count = conn.execute("SELECT COUNT(*) FROM qdc_templates").fetchone()[0]
    if count > 0:
        return

    templates = []

    # 1. FedRAMP Moderate QA
    nodes = [
        _node("src-repo", "Code Repository", 50, 50),
        _node("src-pipeline", "CI/CD Pipeline", 50, 150),
        _node("gate-sast", "SAST Gate", 250, 30),
        _node("gate-sca", "SCA Gate", 250, 100),
        _node("gate-unit", "Unit Test Gate", 250, 170),
        _node("gate-e2e", "E2E Test Gate", 250, 240),
        _node("gate-review", "Code Review Gate", 250, 310),
        _node("tgt-stig", "STIG Compliance", 450, 80),
        _node("con-compliance", "Compliance Report", 450, 180),
        _node("con-oscal", "OSCAL Artifact", 450, 260),
        _node("tgt-fedramp-mod", "FedRAMP Moderate", 650, 150),
    ]
    edges = [
        _edge(nodes[0]["id"], nodes[2]["id"], "scan"),
        _edge(nodes[0]["id"], nodes[3]["id"], "audit"),
        _edge(nodes[1]["id"], nodes[4]["id"], "run"),
        _edge(nodes[1]["id"], nodes[5]["id"], "run"),
        _edge(nodes[0]["id"], nodes[6]["id"], "review"),
        _edge(nodes[2]["id"], nodes[8]["id"]),
        _edge(nodes[3]["id"], nodes[8]["id"]),
        _edge(nodes[4]["id"], nodes[8]["id"]),
        _edge(nodes[7]["id"], nodes[8]["id"]),
        _edge(nodes[8]["id"], nodes[9]["id"]),
        _edge(nodes[9]["id"], nodes[10]["id"]),
    ]
    templates.append(
        (
            f"tpl-{_uid()}",
            "FedRAMP Moderate QA",
            "compliance",
            "Standard FedRAMP Moderate ATO quality topology with SAST, SCA, unit tests, E2E, code review, STIG, and OSCAL.",  # noqa: E501
            json.dumps({"nodes": nodes, "edges": edges}),
            "fedramp_moderate",
            '["fedramp", "moderate", "ato"]',
        )
    )

    # 2. FedRAMP High QA
    nodes2 = [
        _node("src-repo", "Code Repository", 50, 50),
        _node("src-pipeline", "CI/CD Pipeline", 50, 180),
        _node("gate-sast", "SAST Gate", 250, 20),
        _node("gate-dast", "DAST Gate", 250, 80),
        _node("gate-sca", "SCA Gate", 250, 140),
        _node("gate-secret", "Secret Scan", 250, 200),
        _node("gate-container", "Container Scan", 250, 260),
        _node("gate-unit", "Unit Test", 250, 320),
        _node("gate-e2e", "E2E Test", 250, 380),
        _node("gate-fuzz", "Fuzz Test", 250, 440),
        _node("gate-review", "Code Review", 450, 100),
        _node("gate-pentest", "Pen Test", 450, 200),
        _node("tgt-fedramp-high", "FedRAMP High", 650, 200),
        _node("con-oscal", "OSCAL Artifact", 650, 300),
    ]
    edges2 = [
        _edge(nodes2[0]["id"], nodes2[2]["id"]),
        _edge(nodes2[0]["id"], nodes2[3]["id"]),
        _edge(nodes2[0]["id"], nodes2[4]["id"]),
        _edge(nodes2[0]["id"], nodes2[5]["id"]),
        _edge(nodes2[1]["id"], nodes2[6]["id"]),
        _edge(nodes2[1]["id"], nodes2[7]["id"]),
        _edge(nodes2[1]["id"], nodes2[8]["id"]),
        _edge(nodes2[1]["id"], nodes2[9]["id"]),
        _edge(nodes2[10]["id"], nodes2[12]["id"]),
        _edge(nodes2[11]["id"], nodes2[12]["id"]),
        _edge(nodes2[12]["id"], nodes2[13]["id"]),
    ]
    templates.append(
        (
            f"tpl-{_uid()}",
            "FedRAMP High QA",
            "compliance",
            "Enhanced FedRAMP High topology: all Moderate gates plus DAST, pen test, fuzz, container scan.",
            json.dumps({"nodes": nodes2, "edges": edges2}),
            "fedramp_high",
            '["fedramp", "high", "ato", "dast", "pentest"]',
        )
    )

    # 3. CMMC Level 2 QA
    nodes3 = [
        _node("src-repo", "Code Repository", 50, 80),
        _node("gate-sast", "SAST Gate", 250, 30),
        _node("gate-sca", "SCA Gate", 250, 100),
        _node("gate-unit", "Unit Test", 250, 170),
        _node("gate-review", "Code Review", 250, 240),
        _node("gate-secret", "Secret Scan", 250, 310),
        _node("tgt-cmmc-l2", "CMMC Level 2", 450, 150),
        _node("con-compliance", "Compliance Report", 450, 260),
    ]
    edges3 = [
        _edge(nodes3[0]["id"], nodes3[1]["id"]),
        _edge(nodes3[0]["id"], nodes3[2]["id"]),
        _edge(nodes3[0]["id"], nodes3[3]["id"]),
        _edge(nodes3[0]["id"], nodes3[4]["id"]),
        _edge(nodes3[0]["id"], nodes3[5]["id"]),
        _edge(nodes3[1]["id"], nodes3[6]["id"]),
        _edge(nodes3[6]["id"], nodes3[7]["id"]),
    ]
    templates.append(
        (
            f"tpl-{_uid()}",
            "CMMC Level 2 QA",
            "compliance",
            "CMMC L2 quality gates for CUI protection: SAST, SCA, unit tests, code review, secret scan.",
            json.dumps({"nodes": nodes3, "edges": edges3}),
            "cmmc_l2",
            '["cmmc", "level2", "cui"]',
        )
    )

    # 4. cATO Continuous
    nodes4 = [
        _node("src-pipeline", "CI/CD Pipeline", 50, 100),
        _node("src-health", "Health Monitor", 50, 250),
        _node("gate-sast", "SAST Gate", 250, 30),
        _node("gate-unit", "Unit Test", 250, 100),
        _node("gate-e2e", "E2E Test", 250, 170),
        _node("gate-review", "Code Review", 250, 240),
        _node("src-observability", "Observability", 250, 330),
        _node("con-cato", "cATO Evidence", 450, 100),
        _node("con-uqs", "UQS Dashboard", 450, 200),
        _node("con-trend", "Trend Report", 450, 300),
        _node("tgt-cato", "cATO Continuous", 650, 180),
    ]
    edges4 = [
        _edge(nodes4[0]["id"], nodes4[2]["id"]),
        _edge(nodes4[0]["id"], nodes4[3]["id"]),
        _edge(nodes4[0]["id"], nodes4[4]["id"]),
        _edge(nodes4[0]["id"], nodes4[5]["id"]),
        _edge(nodes4[1]["id"], nodes4[6]["id"]),
        _edge(nodes4[2]["id"], nodes4[7]["id"]),
        _edge(nodes4[3]["id"], nodes4[7]["id"]),
        _edge(nodes4[7]["id"], nodes4[8]["id"]),
        _edge(nodes4[6]["id"], nodes4[9]["id"]),
        _edge(nodes4[8]["id"], nodes4[10]["id"]),
    ]
    templates.append(
        (
            f"tpl-{_uid()}",
            "cATO Continuous",
            "continuous",
            "Continuous ATO with real-time evidence, UQS trending, and observability integration.",
            json.dumps({"nodes": nodes4, "edges": edges4}),
            "cato",
            '["cato", "continuous", "evidence", "monitoring"]',
        )
    )

    # 5. Rapid Prototype
    nodes5 = [
        _node("src-repo", "Code Repository", 50, 80),
        _node("gate-sast", "SAST Gate", 250, 50),
        _node("gate-unit", "Unit Test", 250, 130),
        _node("gate-review", "Code Review", 250, 210),
        _node("con-uqs", "UQS Dashboard", 450, 130),
    ]
    edges5 = [
        _edge(nodes5[0]["id"], nodes5[1]["id"]),
        _edge(nodes5[0]["id"], nodes5[2]["id"]),
        _edge(nodes5[0]["id"], nodes5[3]["id"]),
        _edge(nodes5[1]["id"], nodes5[4]["id"]),
        _edge(nodes5[2]["id"], nodes5[4]["id"]),
    ]
    templates.append(
        (
            f"tpl-{_uid()}",
            "Rapid Prototype",
            "development",
            "Minimal quality gates for dev/sandbox: SAST, unit tests, code review.",
            json.dumps({"nodes": nodes5, "edges": edges5}),
            "rapid",
            '["prototype", "sandbox", "minimal"]',
        )
    )

    for t in templates:
        conn.execute(
            "INSERT INTO qdc_templates "
            "(id, name, category, description, graph_json, compliance_target, tags) "
            "VALUES (%s,%s,%s,%s,%s,%s,%s)",
            t,
        )
    conn.commit()


# ── Snippets ─────────────────────────────────────────────────────────────────


def _seed_snippets(conn):
    """Seed 8 reusable snippet patterns."""
    count = conn.execute("SELECT COUNT(*) FROM qdc_snippets").fetchone()[0]
    if count > 0:
        return

    snippets = [
        (
            "SAST+DAST Pipeline",
            "security",
            "Basic security testing chain: static then dynamic analysis.",
            [
                _node("gate-sast", "SAST", 50, 50),
                _node("gate-dast", "DAST", 250, 50),
                _node("con-compliance", "Report", 450, 50),
            ],
            3,
            '["sast", "dast", "pipeline"]',
        ),
        (
            "Full Test Pyramid",
            "testing",
            "Complete test coverage stack: unit → BDD → E2E → fuzz.",
            [
                _node("gate-unit", "Unit", 50, 50),
                _node("gate-bdd", "BDD", 200, 50),
                _node("gate-e2e", "E2E", 350, 50),
                _node("gate-fuzz", "Fuzz", 500, 50),
            ],
            4,
            '["testing", "pyramid", "coverage"]',
        ),
        (
            "Compliance Gate Chain",
            "compliance",
            "Compliance validation pipeline: STIG → FedRAMP → cATO.",
            [
                _node("tgt-stig", "STIG", 50, 50),
                _node("tgt-fedramp-mod", "FedRAMP", 250, 50),
                _node("con-cato", "cATO Evidence", 450, 50),
            ],
            3,
            '["compliance", "stig", "fedramp", "cato"]',
        ),
        (
            "Container Security",
            "security",
            "Container supply chain: SCA → container scan → SBOM → registry.",
            [
                _node("gate-sca", "SCA", 50, 50),
                _node("gate-container", "Container Scan", 250, 50),
                _node("con-compliance", "SBOM", 450, 50),
                _node("src-registry", "Registry", 650, 50),
            ],
            4,
            '["container", "sbom", "supply-chain"]',
        ),
        (
            "Code Quality Triad",
            "quality",
            "Code quality validation: complexity → coherence → review.",
            [
                _node("gate-sast", "Complexity", 50, 50),
                _node("gate-review", "Coherence", 250, 50),
                _node("gate-review", "Review", 450, 50),
            ],
            3,
            '["quality", "complexity", "coherence"]',
        ),
        (
            "Cross-Canvas Hub",
            "integration",
            "All-canvas quality aggregation hub.",
            [
                _node("xc-idc", "IDC", 50, 20),
                _node("xc-sdc", "SDC", 50, 80),
                _node("xc-bdc", "BDC", 50, 140),
                _node("xc-pdc", "PDC", 50, 200),
                _node("xc-odc", "ODC", 50, 260),
                _node("xc-ddc", "DDC", 50, 320),
                _node("xc-ndc", "NDC", 50, 380),
                _node("con-uqs", "UQS", 300, 200),
            ],
            8,
            '["cross-canvas", "aggregation", "hub"]',
        ),
        (
            "Incident Response QA",
            "operations",
            "Post-incident quality loop: alert → triage → fix → retest → close.",
            [
                _node("src-health", "Alert", 50, 50),
                _node("gate-sast", "Triage", 200, 50),
                _node("gate-unit", "Fix & Test", 350, 50),
                _node("con-trend", "Close", 500, 50),
            ],
            4,
            '["incident", "response", "remediation"]',
        ),
        (
            "AI Code Assurance",
            "ai",
            "Quality gates for AI-generated code: AI gen → SAST → review → coverage.",
            [
                _node("src-repo", "AI Gen", 50, 50),
                _node("gate-sast", "SAST", 200, 50),
                _node("gate-review", "Review", 350, 50),
                _node("gate-unit", "Coverage", 500, 50),
            ],
            4,
            '["ai", "generated", "assurance"]',
        ),
    ]

    for name, cat, desc, nodes, nc, tags in snippets:
        edges = []
        for i in range(len(nodes) - 1):
            edges.append(_edge(nodes[i]["id"], nodes[i + 1]["id"]))
        conn.execute(
            "INSERT INTO qdc_snippets "
            "(id, name, category, description, graph_json, node_count, tags) "
            "VALUES (%s,%s,%s,%s,%s,%s,%s)",
            (f"snp-{_uid()}", name, cat, desc, json.dumps({"nodes": nodes, "edges": edges}), nc, tags),
        )
    conn.commit()


# ── Runbooks ─────────────────────────────────────────────────────────────────


def _seed_runbooks(conn):
    """Seed 6 executable runbooks."""
    count = conn.execute("SELECT COUNT(*) FROM qdc_runbooks").fetchone()[0]
    if count > 0:
        return

    now = _now()
    runbooks = [
        (
            "SAST Failure Remediation",
            "sast",
            json.dumps(
                [
                    "Read bandit/SAST findings JSON",
                    "Classify by severity (CAT1/CAT2/CAT3)",
                    "Auto-fix CAT3 issues (safe patterns)",
                    "Flag CAT1/CAT2 for manual review",
                    "Re-run SAST gate to verify",
                ]
            ),
            "## SAST Failure Remediation\n\nTriggered when the SAST gate fails.\n\n"
            "1. Run `python tools/security/sast_runner.py --project-dir . --json`\n"
            "2. Parse findings by severity\n"
            "3. For CAT3: apply auto-fix patterns\n"
            "4. For CAT1/CAT2: create POAM items\n"
            "5. Re-run gate and verify pass\n",
            1,
            0.7,
        ),
        (
            "Test Coverage Recovery",
            "coverage",
            json.dumps(
                [
                    "Run stub_detector to find untested modules",
                    "Identify modules below coverage threshold",
                    "Generate test stubs via TDD workflow",
                    "Run pytest --cov to verify improvement",
                    "Update UQS coverage dimension",
                ]
            ),
            "## Test Coverage Recovery\n\nTriggered when coverage drops below threshold.\n\n"
            "1. Run `python tools/testing/stub_detector.py --project-dir . --json`\n"
            "2. Run `python -m pytest tests/ --cov --cov-report=term-missing`\n"
            "3. Generate tests for uncovered modules\n"
            "4. Re-run coverage check\n",
            1,
            0.7,
        ),
        (
            "cATO Evidence Refresh",
            "assessment",
            json.dumps(
                [
                    "Run cato_scheduler for due evidence",
                    "Collect fresh evidence from all tools",
                    "Generate OSCAL assessment artifacts",
                    "Update cATO dashboard metrics",
                    "Notify assessor of refreshed evidence",
                ]
            ),
            "## cATO Evidence Refresh\n\nTriggered when evidence is older than 30 days.\n\n"
            "1. Run `python tools/compliance/cato_scheduler.py --project-id PROJ --run-due`\n"
            "2. Run `python tools/compliance/evidence_collector.py --project-id PROJ --json`\n"
            "3. Run `python tools/compliance/oscal_generator.py --project-id PROJ --artifact ar`\n"
            "4. Update dashboard with fresh timestamps\n",
            1,
            0.8,
        ),
        (
            "Security Gate Escalation",
            "sast",
            json.dumps(
                [
                    "Triage CVE via dependency_auditor",
                    "Check EPSS score for exploitability",
                    "If exploitable: create POAM immediately",
                    "Apply patch within SLA window",
                    "Re-scan and verify fix",
                    "Close POAM item",
                ]
            ),
            "## Security Gate Escalation\n\nTriggered when critical/high vulnerability found.\n\n"
            "1. Run `python tools/security/dependency_auditor.py --project-dir . --json`\n"
            "2. Check EPSS via `python tools/supply_chain/cve_triager.py --sla-check --json`\n"
            "3. Create POAM if exploitable\n"
            "4. Apply remediation and re-scan\n",
            0,
            0.5,
        ),
        (
            "Cross-Canvas Quality Sync",
            None,
            json.dumps(
                [
                    "Rebuild KG for changed canvas",
                    "Re-assess affected quality gates",
                    "Recompute UQS score",
                    "Emit delta compliance evidence",
                    "Update trend data in qdc_uqs_history",
                ]
            ),
            "## Cross-Canvas Quality Sync\n\nTriggered when any canvas design is saved.\n\n"
            "1. Run `python tools/canvas/kg_builder.py --canvas <key> --design-id <id>`\n"
            "2. Re-assess linked quality gates\n"
            "3. Recompute UQS and store in history\n"
            "4. Emit OSCAL evidence for changed gates\n",
            1,
            0.9,
        ),
        (
            "Pre-Release Quality Gate",
            None,
            json.dumps(
                [
                    "Run all SA-11 gates sequentially",
                    "Verify UQS >= release threshold",
                    "Generate OSCAL assessment report",
                    "Check STIG compliance (0 CAT1)",
                    "Produce release quality certificate",
                ]
            ),
            "## Pre-Release Quality Gate\n\nTriggered when release branch created.\n\n"
            "1. Run full assessment: `POST /quality/api/designs/<id>/assess`\n"
            "2. Check UQS >= 90 (release threshold)\n"
            "3. Generate OSCAL: `python tools/compliance/oscal_generator.py --artifact ar`\n"
            "4. Verify STIG: `python tools/compliance/stig_checker.py --project-id PROJ`\n"
            "5. Sign quality certificate\n",
            0,
            0.5,
        ),
    ]

    for name, trigger, steps, body, auto, conf in runbooks:
        conn.execute(
            "INSERT INTO qdc_runbooks "
            "(id, name, trigger_gate, steps_json, body_markdown, "
            "auto_executable, confidence_threshold, created_at, updated_at) "
            "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)",
            (f"rb-{_uid()}", name, trigger, steps, body, auto, conf, now, now),
        )
    conn.commit()


# ── SOPs ─────────────────────────────────────────────────────────────────────


def _seed_sops(conn):
    """Seed 4 Standard Operating Procedures."""
    count = conn.execute("SELECT COUNT(*) FROM qdc_sops").fetchone()[0]
    if count > 0:
        return

    now = _now()
    sops = [
        (
            "SOP-QDC-001",
            "Quality Gate Configuration",
            "on_project_init",
            "DevSecOps Lead",
            "## SOP-QDC-001: Quality Gate Configuration\n\n"
            "### Purpose\nDefine the process for configuring UQS weights, gate thresholds, "
            "and compliance targets when initializing a new project.\n\n"
            "### Procedure\n"
            "1. Review project classification (IL2/IL4/IL5/IL6)\n"
            "2. Select appropriate QDC template (FedRAMP/CMMC/cATO/Rapid)\n"
            "3. Adjust UQS dimension weights if needed\n"
            "4. Set gate thresholds per `args/qdc_canvas_config.yaml`\n"
            "5. Link cross-canvas designs from existing canvases\n"
            "6. Run initial assessment to establish baseline UQS\n"
            "7. Document configuration decisions in audit trail\n",
        ),
        (
            "SOP-QDC-002",
            "Continuous Quality Monitoring",
            "daily",
            "SRE / QA Team",
            "## SOP-QDC-002: Continuous Quality Monitoring\n\n"
            "### Purpose\nDefine daily quality monitoring procedures.\n\n"
            "### Procedure\n"
            "1. Review UQS dashboard for score changes > 5 points\n"
            "2. Check for new CAT1/CAT2 findings\n"
            "3. Verify no expired cATO evidence\n"
            "4. Review trend report for declining dimensions\n"
            "5. Escalate any dimension below threshold\n"
            "6. Run applicable runbooks for failed gates\n"
            "7. Update daily quality log\n",
        ),
        (
            "SOP-QDC-003",
            "Quality Evidence Collection for ATO",
            "per_assessment",
            "ISSO / Assessor",
            "## SOP-QDC-003: Quality Evidence Collection for ATO\n\n"
            "### Purpose\nCollect and package quality evidence for ATO assessments.\n\n"
            "### Procedure\n"
            "1. Export current UQS breakdown as JSON\n"
            "2. Export all SA-11 gate results with OSCAL artifacts\n"
            "3. Generate STIG compliance report\n"
            "4. Generate SBOM (CycloneDX format)\n"
            "5. Collect cross-canvas quality scores\n"
            "6. Package as OSCAL assessment-results\n"
            "7. Submit to eMASS/Xacta via export engine\n",
        ),
        (
            "SOP-QDC-004",
            "Quality Incident Response",
            "on_gate_failure",
            "Dev Team",
            "## SOP-QDC-004: Quality Incident Response\n\n"
            "### Purpose\nTriage and remediate quality gate failures within SLA.\n\n"
            "### Procedure\n"
            "1. Identify failed gate and severity (CAT1/CAT2/CAT3)\n"
            "2. CAT1: Immediate response — fix within 24 hours\n"
            "3. CAT2: Standard response — fix within 5 business days\n"
            "4. CAT3: Planned response — schedule in next sprint\n"
            "5. Execute applicable runbook if available\n"
            "6. Re-run gate to verify remediation\n"
            "7. Update POAM if fix extends beyond SLA\n"
            "8. Record incident in audit trail\n",
        ),
    ]

    for sop_num, title, freq, audience, body in sops:
        conn.execute(
            "INSERT INTO qdc_sops "
            "(id, sop_number, title, version, frequency, audience, "
            "body_markdown, approval_status, classification, created_at, updated_at) "
            "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
            (f"sop-{_uid()}", sop_num, title, 1, freq, audience, body, "draft", "CUI", now, now),
        )
    conn.commit()


# ── Init ─────────────────────────────────────────────────────────────────────


def init_db():
    """Initialize QDC database: create schema and seed data."""
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = get_connection()
    try:
        conn.executescript(SCHEMA)
        _seed_templates(conn)
        _seed_snippets(conn)
        _seed_runbooks(conn)
        _seed_sops(conn)
    finally:
        conn.close()


if __name__ == "__main__":
    init_db()
    conn = get_connection()
    try:
        tables = conn.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name").fetchall()
        print(f"QDC Canvas DB initialized: {len(tables)} tables")
        for t in tables:
            count = conn.execute(f"SELECT COUNT(*) FROM [{t[0]}]").fetchone()[0]  # noqa: S608
            print(f"  {t[0]}: {count} rows")
    finally:
        conn.close()
