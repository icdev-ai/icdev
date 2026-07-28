# CUI // SP-CTI
"""Agentic AI Design Canvas — DB initializer.

Creates schema and seeds 12 built-in templates + 12 built-in snippets.
Dual-backend: PostgreSQL (default) or SQLite. All SQL is authored with
PostgreSQL ``%s`` placeholders; both backends run through the storage shim's
translating ``StorageConnection`` (``tools/db/storage.py``), which rewrites
``%s`` → ``?`` for SQLite and provides a cross-backend ``executescript()``.

Backend selection (first set wins, default ``postgresql``):
    AADC_STORAGE_BACKEND → ICDEV_CANVAS_STORAGE_BACKEND → ICDEV_STORAGE_BACKEND

Usage:
    python tools/agentic_ai_canvas/db/init_db.py
"""

from __future__ import annotations

import json
import os
import uuid
from datetime import datetime, timezone
from pathlib import Path

_ICDEV_ROOT = Path(__file__).resolve().parents[3]
DB_PATH = _ICDEV_ROOT / "data" / "agentic_ai_canvas.db"

_BACKEND = os.environ.get(
    "AADC_STORAGE_BACKEND",
    os.environ.get("ICDEV_CANVAS_STORAGE_BACKEND", os.environ.get("ICDEV_STORAGE_BACKEND", "postgresql")),
).lower()


def get_connection():
    """Get a database connection — SQLite or PostgreSQL.

    Both paths return a translating ``StorageConnection`` from the storage
    shim so the PG-style ``%s`` placeholders used throughout this module work
    identically on either backend.

    PostgreSQL uses get_canvas_connection() because aadc_* tables have no
    tenant_id/classification columns; get_connection() would inject RLS and
    raise UndefinedColumn on every query.

    SQLite targets the dedicated per-canvas ``.db`` file (DB_PATH) but is
    still wrapped in ``StorageConnection`` so ``%s`` → ``?`` translation and
    ``executescript()`` behave the same as on PG. A raw ``sqlite3.connect``
    here would raise ``OperationalError`` on the first ``%s`` statement.
    """
    if _BACKEND == "postgresql":
        try:
            from tools.db.storage import get_canvas_connection
            return get_canvas_connection("AADC_PG_DATABASE")
        except Exception:
            pass

    # SQLite path — dedicated canvas .db file wrapped in the translating shim.
    from tools.db.storage import StorageConnection, _get_sqlite_connection
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    raw_conn = _get_sqlite_connection(str(DB_PATH))
    conn = StorageConnection(raw_conn, "sqlite")
    # Canvas tables have no tenant_id/classification columns — no RLS.
    conn.set_security_context(None)  # rls-bypass: canvas tables have no tenant_id/classification columns, so RLS injection would raise UndefinedColumn on every query
    return conn


SCHEMA = """
CREATE TABLE IF NOT EXISTS aadc_designs (
    id              TEXT PRIMARY KEY,
    name            TEXT NOT NULL,
    description     TEXT DEFAULT '',
    domain          TEXT DEFAULT '',
    classification  TEXT DEFAULT 'CUI',
    graph_json      TEXT NOT NULL DEFAULT '{"nodes":[],"edges":[]}',
    template_id     TEXT,
    project_id      TEXT,
    autonomy_max    INTEGER DEFAULT 0,
    safety_impacting INTEGER DEFAULT 0,
    rights_impacting INTEGER DEFAULT 0,
    hitl_required   INTEGER DEFAULT 0,
    created_at      TEXT DEFAULT CURRENT_TIMESTAMP,
    updated_at      TEXT DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS aadc_templates (
    id              TEXT PRIMARY KEY,
    name            TEXT NOT NULL,
    category        TEXT DEFAULT 'general',
    description     TEXT DEFAULT '',
    graph_json      TEXT NOT NULL DEFAULT '{"nodes":[],"edges":[]}',
    compliance_badges TEXT DEFAULT '{}',
    autonomy_max    INTEGER DEFAULT 0,
    tags            TEXT DEFAULT '[]',
    is_builtin      INTEGER DEFAULT 1,
    created_by      TEXT DEFAULT 'system',
    created_at      TEXT DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS aadc_snippets (
    id              TEXT PRIMARY KEY,
    name            TEXT NOT NULL,
    category        TEXT DEFAULT 'general',
    description     TEXT DEFAULT '',
    graph_json      TEXT NOT NULL DEFAULT '{"nodes":[],"edges":[]}',
    node_count      INTEGER DEFAULT 0,
    tags            TEXT DEFAULT '[]',
    is_builtin      INTEGER DEFAULT 1,
    created_at      TEXT DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS aadc_assessments (
    id              TEXT PRIMARY KEY,
    design_id       TEXT NOT NULL,
    score           REAL DEFAULT 0.0,
    nist_rmf_score  REAL DEFAULT 0.0,
    owasp_score     REAL DEFAULT 0.0,
    omb_compliant   INTEGER DEFAULT 0,
    autonomy_max    INTEGER DEFAULT 0,
    safety_impacting INTEGER DEFAULT 0,
    rights_impacting INTEGER DEFAULT 0,
    findings_json   TEXT DEFAULT '[]',
    atlas_threats   TEXT DEFAULT '[]',
    created_at      TEXT DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_aadc_assess_design ON aadc_assessments(design_id);

CREATE TABLE IF NOT EXISTS aadc_artifacts (
    id              TEXT PRIMARY KEY,
    design_id       TEXT NOT NULL,
    artifact_type   TEXT NOT NULL,
    title           TEXT NOT NULL,
    content_json    TEXT DEFAULT '{}',
    content_md      TEXT DEFAULT '',
    created_at      TEXT DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_aadc_artifact_design ON aadc_artifacts(design_id);

CREATE TABLE IF NOT EXISTS aadc_workflow_links (
    id              TEXT PRIMARY KEY,
    design_id       TEXT NOT NULL,
    wf_instance_id  TEXT NOT NULL,
    status          TEXT DEFAULT 'pending',
    created_at      TEXT DEFAULT CURRENT_TIMESTAMP,
    resolved_at     TEXT
);
CREATE INDEX IF NOT EXISTS idx_aadc_wflink_design ON aadc_workflow_links(design_id);

CREATE TABLE IF NOT EXISTS aadc_loop_links (
    id              TEXT PRIMARY KEY,
    design_id       TEXT NOT NULL,
    loop_id         TEXT NOT NULL,
    phase           TEXT DEFAULT 'plan',
    created_at      TEXT DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_aadc_looplink_design ON aadc_loop_links(design_id);

CREATE TABLE IF NOT EXISTS aadc_versions (
    id              TEXT PRIMARY KEY,
    design_id       TEXT REFERENCES aadc_designs(id),
    version_number  INTEGER NOT NULL DEFAULT 1,
    graph_json      TEXT NOT NULL DEFAULT '{"nodes":[],"edges":[]}',
    change_summary  TEXT DEFAULT '',
    created_at      TEXT DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_aadc_ver_design ON aadc_versions(design_id);

CREATE TABLE IF NOT EXISTS aadc_audit (
    id              TEXT PRIMARY KEY,
    design_id       TEXT,
    action          TEXT NOT NULL,
    detail          TEXT DEFAULT '',
    classification  TEXT DEFAULT 'CUI',
    created_at      TEXT DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS aadc_safety_graphs (
    id              TEXT PRIMARY KEY,
    design_id       TEXT NOT NULL,
    score           REAL DEFAULT 0.0,
    protected_count INTEGER DEFAULT 0,
    unprotected_count INTEGER DEFAULT 0,
    analysis_json   TEXT NOT NULL DEFAULT '{}',
    created_at      TEXT DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_aadc_safety_graphs_design ON aadc_safety_graphs(design_id);

CREATE TABLE IF NOT EXISTS aadc_agent_simulations (
    id              TEXT PRIMARY KEY,
    design_id       TEXT NOT NULL,
    start_node_id   TEXT NOT NULL DEFAULT '',
    input_payload   TEXT DEFAULT '{}',
    trace_json      TEXT NOT NULL DEFAULT '[]',
    decisions_json  TEXT NOT NULL DEFAULT '[]',
    status          TEXT DEFAULT 'complete',
    steps_count     INTEGER DEFAULT 0,
    halted_by       TEXT DEFAULT '',
    halted_by_label TEXT DEFAULT '',
    created_at      TEXT DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_aadc_agent_simulations_design ON aadc_agent_simulations(design_id);

CREATE TABLE IF NOT EXISTS aadc_risk_items (
    id              TEXT PRIMARY KEY,
    design_id       TEXT NOT NULL,
    title           TEXT NOT NULL DEFAULT '',
    description     TEXT DEFAULT '',
    risk_category   TEXT DEFAULT 'operational',
    severity        TEXT DEFAULT 'MEDIUM',
    likelihood      TEXT DEFAULT 'MEDIUM',
    impact          TEXT DEFAULT 'MEDIUM',
    status          TEXT DEFAULT 'open',
    owner           TEXT DEFAULT '',
    mitigation      TEXT DEFAULT '',
    finding_id      TEXT DEFAULT '',
    node_id         TEXT DEFAULT '',
    created_at      TEXT DEFAULT CURRENT_TIMESTAMP,
    updated_at      TEXT DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_aadc_risk_items_design ON aadc_risk_items(design_id);
CREATE INDEX IF NOT EXISTS idx_aadc_risk_items_status ON aadc_risk_items(status);

CREATE TABLE IF NOT EXISTS aadc_threat_models (
    id              TEXT PRIMARY KEY,
    design_id       TEXT NOT NULL,
    stride_json     TEXT NOT NULL DEFAULT '[]',
    atlas_threats   TEXT NOT NULL DEFAULT '[]',
    threat_count    INTEGER DEFAULT 0,
    high_count      INTEGER DEFAULT 0,
    created_at      TEXT DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_aadc_threat_models_design ON aadc_threat_models(design_id);

CREATE TABLE IF NOT EXISTS aadc_ato_reports (
    id              TEXT PRIMARY KEY,
    design_id       TEXT NOT NULL,
    score_pct       REAL DEFAULT 0.0,
    ato_ready       INTEGER DEFAULT 0,
    passed          INTEGER DEFAULT 0,
    failed          INTEGER DEFAULT 0,
    critical_failed INTEGER DEFAULT 0,
    report_json     TEXT NOT NULL DEFAULT '{}',
    created_at      TEXT DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_aadc_ato_reports_design ON aadc_ato_reports(design_id);

CREATE TABLE IF NOT EXISTS aadc_regulatory_gaps (
    id              TEXT PRIMARY KEY,
    design_id       TEXT NOT NULL,
    score_pct       REAL DEFAULT 0.0,
    compliant       INTEGER DEFAULT 0,
    gaps            INTEGER DEFAULT 0,
    critical_gaps   INTEGER DEFAULT 0,
    report_json     TEXT NOT NULL DEFAULT '{}',
    created_at      TEXT DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_aadc_regulatory_gaps_design ON aadc_regulatory_gaps(design_id);

CREATE TABLE IF NOT EXISTS aadc_red_team_reports (
    id              TEXT PRIMARY KEY,
    design_id       TEXT NOT NULL,
    overall_risk    TEXT DEFAULT 'UNKNOWN',
    applicable      INTEGER DEFAULT 0,
    unmitigated     INTEGER DEFAULT 0,
    critical_unmitigated INTEGER DEFAULT 0,
    avg_exploitability REAL DEFAULT 0.0,
    report_json     TEXT NOT NULL DEFAULT '{}',
    created_at      TEXT DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_aadc_red_team_design ON aadc_red_team_reports(design_id);

CREATE TABLE IF NOT EXISTS aadc_lint_reports (
    id              TEXT PRIMARY KEY,
    design_id       TEXT NOT NULL,
    lint_score      REAL DEFAULT 100.0,
    total_issues    INTEGER DEFAULT 0,
    critical_issues INTEGER DEFAULT 0,
    report_json     TEXT NOT NULL DEFAULT '{}',
    created_at      TEXT DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_aadc_lint_design ON aadc_lint_reports(design_id);

CREATE TABLE IF NOT EXISTS aadc_pattern_reports (
    id              TEXT PRIMARY KEY,
    design_id       TEXT NOT NULL,
    dominant_pattern TEXT DEFAULT 'UNCLASSIFIED',
    pattern_json    TEXT NOT NULL DEFAULT '{}',
    created_at      TEXT DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_aadc_pattern_design ON aadc_pattern_reports(design_id);

CREATE TABLE IF NOT EXISTS aadc_impact_reports (
    id              TEXT PRIMARY KEY,
    design_id       TEXT NOT NULL,
    resilience_score REAL DEFAULT 100.0,
    spof_count      INTEGER DEFAULT 0,
    overall_risk_level TEXT DEFAULT 'LOW',
    report_json     TEXT NOT NULL DEFAULT '{}',
    created_at      TEXT DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_aadc_impact_design ON aadc_impact_reports(design_id);

CREATE TABLE IF NOT EXISTS aadc_scorecard_snapshots (
    id              TEXT PRIMARY KEY,
    design_id       TEXT NOT NULL,
    overall_score   REAL DEFAULT 0.0,
    health          TEXT DEFAULT 'UNRATED',
    snapshot_json   TEXT NOT NULL DEFAULT '{}',
    created_at      TEXT DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_aadc_scorecard_design ON aadc_scorecard_snapshots(design_id);

CREATE TABLE IF NOT EXISTS aadc_deploy_gates (
    id              TEXT PRIMARY KEY,
    design_id       TEXT NOT NULL,
    verdict         TEXT DEFAULT 'BLOCKED',
    blocker_count   INTEGER DEFAULT 0,
    warning_count   INTEGER DEFAULT 0,
    gate_json       TEXT NOT NULL DEFAULT '{}',
    created_at      TEXT DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_aadc_deploy_gate_design ON aadc_deploy_gates(design_id);

CREATE TABLE IF NOT EXISTS aadc_lifecycle_states (
    id              TEXT PRIMARY KEY,
    design_id       TEXT NOT NULL,
    from_state      TEXT NOT NULL DEFAULT 'DRAFT',
    to_state        TEXT NOT NULL,
    actor           TEXT DEFAULT '',
    reason          TEXT DEFAULT '',
    created_at      TEXT DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_aadc_lifecycle_design ON aadc_lifecycle_states(design_id);

CREATE TABLE IF NOT EXISTS aadc_review_comments (
    id              TEXT PRIMARY KEY,
    design_id       TEXT NOT NULL,
    reviewer        TEXT NOT NULL DEFAULT '',
    comment_type    TEXT NOT NULL DEFAULT 'COMMENT',
    body            TEXT DEFAULT '',
    node_id         TEXT DEFAULT '',
    created_at      TEXT DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_aadc_review_design ON aadc_review_comments(design_id);
"""


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _uid() -> str:
    return uuid.uuid4().hex[:10]


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _node(ntype: str, label: str, x: int, y: int, **kw) -> dict:
    n = {"id": f"n-{_uid()}", "type": ntype, "label": label,
         "x": x, "y": y, "w": kw.pop("w", 140), "h": kw.pop("h", 50)}
    n.update(kw)
    return n


def _edge(src: str, tgt: str, label: str = "") -> dict:
    return {"id": f"e-{_uid()}", "source": src, "target": tgt, "label": label}


def _chain(nodes: list[dict]) -> list[dict]:
    return [_edge(nodes[i]["id"], nodes[i + 1]["id"]) for i in range(len(nodes) - 1)]


# ---------------------------------------------------------------------------
# Template seeds
# ---------------------------------------------------------------------------

def _seed_templates(conn) -> None:
    if conn.execute("SELECT COUNT(*) FROM aadc_templates").fetchone()[0] > 0:
        return

    templates = []

    # 1. Simple RAG Q&A
    ns = [
        _node("inference-input",    "User Query",       50,  150),
        _node("input-sanitizer",    "Input Sanitizer",  220, 150),
        _node("chunker",            "Chunker",          50,  280),
        _node("embedding-model",    "Embedder",         220, 280),
        _node("vector-db",          "Vector DB",        390, 280),
        _node("doc-store",          "Document Store",   50,  400),
        _node("llm",                "LLM",              390, 150),
        _node("guardrail",          "Content Filter",   560, 150),
        _node("output-validator",   "Output Validator", 730, 150),
    ]
    es = [
        _edge(ns[0]["id"], ns[1]["id"], "query"),
        _edge(ns[1]["id"], ns[6]["id"], "sanitized"),
        _edge(ns[2]["id"], ns[3]["id"], "chunks"),
        _edge(ns[3]["id"], ns[4]["id"], "vectors"),
        _edge(ns[5]["id"], ns[2]["id"], "docs"),
        _edge(ns[4]["id"], ns[6]["id"], "context"),
        _edge(ns[6]["id"], ns[7]["id"], "response"),
        _edge(ns[7]["id"], ns[8]["id"], "filtered"),
    ]
    templates.append(("Simple RAG Q&A", "retrieval",
        "Minimal grounded Q&A: user query → sanitizer → RAG retrieval → LLM → content filter → validator.",
        {"nodes": ns, "edges": es},
        {"nist_ai_rmf": 40, "owasp_llm": 55}, 0,
        '["rag", "qa", "grounded", "minimal"]'))

    # 2. HITL-Gated RAG
    ns2 = [
        _node("inference-input",    "User Query",       50,  150),
        _node("input-sanitizer",    "Input Sanitizer",  220, 150),
        _node("chunker",            "Chunker",          50,  280),
        _node("embedding-model",    "Embedder",         220, 280),
        _node("vector-db",          "Vector DB",        390, 280),
        _node("doc-store",          "Document Store",   50,  400),
        _node("confidence-threshold","Confidence Gate", 390, 150),
        _node("llm",                "LLM",              560, 150),
        _node("hitl-gate",          "HITL Gate",        730, 150),
        _node("audit-logger",       "Audit Logger",     730, 280),
        _node("output-validator",   "Output Validator", 900, 150),
    ]
    es2 = [
        _edge(ns2[0]["id"], ns2[1]["id"], "query"),
        _edge(ns2[1]["id"], ns2[6]["id"], "sanitized"),
        _edge(ns2[2]["id"], ns2[3]["id"], "chunks"),
        _edge(ns2[3]["id"], ns2[4]["id"], "vectors"),
        _edge(ns2[5]["id"], ns2[2]["id"], "docs"),
        _edge(ns2[4]["id"], ns2[6]["id"], "context"),
        _edge(ns2[6]["id"], ns2[7]["id"], "context+query"),
        _edge(ns2[7]["id"], ns2[8]["id"], "draft"),
        _edge(ns2[8]["id"], ns2[9]["id"], "log"),
        _edge(ns2[8]["id"], ns2[10]["id"], "approved"),
    ]
    templates.append(("HITL-Gated RAG", "retrieval-gov",
        "Safety-first RAG: adds confidence gate and human review before output reaches users.",
        {"nodes": ns2, "edges": es2},
        {"nist_ai_rmf": 90, "owasp_llm": 80, "omb_m25_21": "compliant"}, 1,
        '["rag", "hitl", "governance", "safety-impacting"]'))

    # 3. Single Autonomous Agent
    ns3 = [
        _node("inference-input",    "Task Input",       50,  200),
        _node("input-sanitizer",    "Input Sanitizer",  220, 200),
        _node("autonomous-agent",   "Autonomous Agent", 390, 200),
        _node("mcp-gateway",        "MCP Gateway",      560, 100),
        _node("mcp-server",         "MCP Server",       730, 100),
        _node("tool-chain",         "Tool Chain",       730, 200),
        _node("short-term-mem",     "Short-Term Mem",   390, 350),
        _node("circuit-breaker",    "Circuit Breaker",  560, 250),
        _node("output-validator",   "Output Validator", 900, 200),
        _node("audit-logger",       "Audit Logger",     900, 350),
        _node("confidence-threshold","Confidence Gate", 390, 80),
    ]
    es3 = [
        _edge(ns3[0]["id"], ns3[1]["id"], "task"),
        _edge(ns3[1]["id"], ns3[2]["id"], "sanitized"),
        _edge(ns3[2]["id"], ns3[10]["id"], "plan"),
        _edge(ns3[10]["id"], ns3[3]["id"], "if confident"),
        _edge(ns3[3]["id"], ns3[4]["id"], "routed"),
        _edge(ns3[4]["id"], ns3[5]["id"], "tools"),
        _edge(ns3[5]["id"], ns3[7]["id"], "result"),
        _edge(ns3[7]["id"], ns3[8]["id"], "checked"),
        _edge(ns3[2]["id"], ns3[6]["id"], "context"),
        _edge(ns3[8]["id"], ns3[9]["id"], "log"),
    ]
    templates.append(("Single Autonomous Agent", "agent",
        "Autonomous agent with MCP tools, short-term memory, circuit breaker, and audit logging.",
        {"nodes": ns3, "edges": es3},
        {"nist_ai_rmf": 70, "owasp_llm": 75}, 4,
        '["agent", "autonomous", "mcp", "tools"]'))

    # 4. Multi-Agent Orchestrator
    ns4 = [
        _node("inference-input",    "Task Input",       50,  300),
        _node("orchestrator",       "Orchestrator",     220, 300),
        _node("sub-agent",          "Research Agent",   420, 100),
        _node("sub-agent",          "Writer Agent",     420, 250),
        _node("sub-agent",          "Reviewer Agent",   420, 400),
        _node("long-term-mem",      "Shared Memory",    220, 480),
        _node("circuit-breaker",    "Circuit Breaker",  600, 250),
        _node("hitl-gate",          "HITL Escalation",  780, 300),
        _node("audit-logger",       "Audit Logger",     780, 480),
        _node("output-validator",   "Final Validator",  950, 300),
    ]
    es4 = [
        _edge(ns4[0]["id"], ns4[1]["id"], "task"),
        _edge(ns4[1]["id"], ns4[2]["id"], "research"),
        _edge(ns4[1]["id"], ns4[3]["id"], "write"),
        _edge(ns4[1]["id"], ns4[4]["id"], "review"),
        _edge(ns4[2]["id"], ns4[6]["id"], "result"),
        _edge(ns4[3]["id"], ns4[6]["id"], "result"),
        _edge(ns4[4]["id"], ns4[6]["id"], "result"),
        _edge(ns4[6]["id"], ns4[7]["id"], "escalate"),
        _edge(ns4[7]["id"], ns4[9]["id"], "approved"),
        _edge(ns4[9]["id"], ns4[8]["id"], "log"),
        _edge(ns4[1]["id"], ns4[5]["id"], "memory"),
    ]
    templates.append(("Multi-Agent Orchestrator", "multi-agent",
        "Orchestrator dispatches to 3 specialized sub-agents with shared memory, circuit breaker, and HITL escalation.",
        {"nodes": ns4, "edges": es4},
        {"nist_ai_rmf": 85, "owasp_llm": 70}, 4,
        '["multi-agent", "orchestrator", "hitl", "enterprise"]'))

    # 5. Agentic Research Pipeline
    ns5 = [
        _node("inference-input",    "Research Query",   50,  200),
        _node("researcher-agent",   "Research Agent",   220, 200),
        _node("web-search",         "Web Search",       390, 100),
        _node("chunker",            "Chunker",          390, 250),
        _node("embedding-model",    "Embedder",         560, 250),
        _node("vector-db",          "Vector DB",        730, 250),
        _node("reranker",           "Re-Ranker",        730, 100),
        _node("llm",                "Synthesis LLM",    900, 200),
        _node("confidence-threshold","Confidence Gate", 900, 350),
        _node("output-validator",   "Output Validator", 1070,200),
        _node("audit-logger",       "Audit Logger",     1070,350),
    ]
    es5 = [
        _edge(ns5[0]["id"], ns5[1]["id"], "query"),
        _edge(ns5[1]["id"], ns5[2]["id"], "search"),
        _edge(ns5[1]["id"], ns5[3]["id"], "docs"),
        _edge(ns5[3]["id"], ns5[4]["id"], "chunks"),
        _edge(ns5[4]["id"], ns5[5]["id"], "vectors"),
        _edge(ns5[2]["id"], ns5[6]["id"], "results"),
        _edge(ns5[5]["id"], ns5[6]["id"], "retrieved"),
        _edge(ns5[6]["id"], ns5[7]["id"], "reranked"),
        _edge(ns5[7]["id"], ns5[8]["id"], "draft"),
        _edge(ns5[8]["id"], ns5[9]["id"], "if confident"),
        _edge(ns5[9]["id"], ns5[10]["id"], "log"),
    ]
    templates.append(("Agentic Research Pipeline", "research",
        "Research agent + web search + RAG + re-ranker + synthesis LLM + confidence gate. Genesis v2 pattern.",
        {"nodes": ns5, "edges": es5},
        {"nist_ai_rmf": 65, "owasp_llm": 65, "nist_ai_600": "low-risk"}, 3,
        '["research", "rag", "web-search", "genesis"]'))

    # 6. Proposal Genesis Pattern
    ns6 = [
        _node("inference-input",    "RFP/SOW Input",    50,  250),
        _node("classifier",         "Capture Manager",  220, 250),
        _node("researcher-agent",   "Research Engine",  390, 100),
        _node("writer-agent",       "Writer Agent",     390, 250),
        _node("reviewer-agent",     "Reviewer Agent",   390, 400),
        _node("vector-db",          "Knowledge Base",   220, 400),
        _node("confidence-threshold","Confidence Gate", 560, 250),
        _node("hitl-gate",          "HITL Approval",    730, 250),
        _node("audit-logger",       "Audit Logger",     730, 400),
        _node("compliance-reporter","Compliance Rpt",   900, 250),
        _node("output-validator",   "Final Validator",  900, 400),
    ]
    es6 = [
        _edge(ns6[0]["id"], ns6[1]["id"], "rfp"),
        _edge(ns6[1]["id"], ns6[2]["id"], "research"),
        _edge(ns6[1]["id"], ns6[3]["id"], "write"),
        _edge(ns6[1]["id"], ns6[4]["id"], "review"),
        _edge(ns6[5]["id"], ns6[2]["id"], "kb"),
        _edge(ns6[2]["id"], ns6[6]["id"], "findings"),
        _edge(ns6[3]["id"], ns6[6]["id"], "draft"),
        _edge(ns6[4]["id"], ns6[6]["id"], "critique"),
        _edge(ns6[6]["id"], ns6[7]["id"], "for approval"),
        _edge(ns6[7]["id"], ns6[8]["id"], "log"),
        _edge(ns6[7]["id"], ns6[9]["id"], "approved"),
        _edge(ns6[9]["id"], ns6[10]["id"], "validate"),
    ]
    templates.append(("Proposal Genesis Pattern", "enterprise",
        "Capture manager + research + writer + reviewer + HITL approval + compliance report. GovCon proposal automation.",
        {"nodes": ns6, "edges": es6},
        {"nist_ai_rmf": 85, "omb_m25_21": "compliant", "owasp_llm": 70}, 3,
        '["proposal", "govcon", "genesis", "enterprise"]'))

    # 7. RAG + Fine-Tuning Loop
    ns7 = [
        _node("doc-store",          "Document Store",   50,  200),
        _node("chunker",            "Chunker",          220, 200),
        _node("embedding-model",    "Embedder",         390, 200),
        _node("vector-db",          "Vector DB",        560, 200),
        _node("llm",                "Base LLM",         730, 200),
        _node("inference-input",    "User Query",       50,  80),
        _node("reranker",           "Re-Ranker",        560, 80),
        _node("feedback-collector", "Feedback",         900, 200),
        _node("rlhf-pipeline",      "RLHF Pipeline",    900, 350),
        _node("fine-tuned-adapter", "Fine-Tuned Adapter",730,350),
        _node("audit-logger",       "Audit Logger",     1070,275),
        _node("model-registry",     "Model Registry",   560, 350),
    ]
    es7 = [
        _edge(ns7[0]["id"], ns7[1]["id"], "docs"),
        _edge(ns7[1]["id"], ns7[2]["id"], "chunks"),
        _edge(ns7[2]["id"], ns7[3]["id"], "vectors"),
        _edge(ns7[5]["id"], ns7[6]["id"], "query"),
        _edge(ns7[3]["id"], ns7[6]["id"], "retrieved"),
        _edge(ns7[6]["id"], ns7[4]["id"], "context"),
        _edge(ns7[4]["id"], ns7[7]["id"], "output"),
        _edge(ns7[7]["id"], ns7[8]["id"], "preferences"),
        _edge(ns7[8]["id"], ns7[9]["id"], "trained"),
        _edge(ns7[9]["id"], ns7[11]["id"], "register"),
        _edge(ns7[9]["id"], ns7[4]["id"], "replace"),
        _edge(ns7[10]["id"], ns7[7]["id"]),
    ]
    templates.append(("RAG + Fine-Tuning Loop", "learning",
        "Complete learning loop: RAG retrieval → LLM → feedback collection → RLHF → fine-tuned adapter → model registry.",
        {"nodes": ns7, "edges": es7},
        {"nist_ai_rmf": 60, "owasp_llm": 55}, 2,
        '["rag", "fine-tuning", "rlhf", "learning-loop"]'))

    # 8. MCP Server Cluster
    ns8 = [
        _node("inference-input",    "Agent Request",    50,  200),
        _node("mcp-gateway",        "MCP Gateway",      220, 200),
        _node("rate-limiter",       "Rate Limiter",     220, 350),
        _node("mcp-server",         "MCP Server A",     420, 80),
        _node("mcp-server",         "MCP Server B",     420, 200),
        _node("mcp-server",         "MCP Server C",     420, 320),
        _node("tool-chain",         "Tool Chain",       600, 200),
        _node("output-validator",   "Response Validator",780,200),
        _node("audit-logger",       "Audit Logger",     780, 350),
    ]
    es8 = [
        _edge(ns8[0]["id"], ns8[1]["id"], "request"),
        _edge(ns8[1]["id"], ns8[2]["id"], "rate check"),
        _edge(ns8[1]["id"], ns8[3]["id"], "route A"),
        _edge(ns8[1]["id"], ns8[4]["id"], "route B"),
        _edge(ns8[1]["id"], ns8[5]["id"], "route C"),
        _edge(ns8[3]["id"], ns8[6]["id"]),
        _edge(ns8[4]["id"], ns8[6]["id"]),
        _edge(ns8[5]["id"], ns8[6]["id"]),
        _edge(ns8[6]["id"], ns8[7]["id"], "result"),
        _edge(ns8[7]["id"], ns8[8]["id"], "log"),
    ]
    templates.append(("MCP Server Cluster", "infrastructure",
        "Enterprise MCP deployment: gateway auth + 3 MCP servers + rate limiter + response validator + audit log.",
        {"nodes": ns8, "edges": es8},
        {"nist_ai_rmf": 60, "owasp_llm": 80}, 1,
        '["mcp", "infrastructure", "enterprise", "cluster"]'))

    # 9. Compliance-First AI
    ns9 = [
        _node("inference-input",    "User Input",       50,  200),
        _node("input-sanitizer",    "Input Sanitizer",  220, 200),
        _node("pii-detector",       "PII Detector",     390, 200),
        _node("redaction-engine",   "Redaction",        560, 200),
        _node("llm",                "LLM",              730, 200),
        _node("guardrail",          "Guardrail",        730, 350),
        _node("hitl-gate",          "HITL Gate",        900, 200),
        _node("audit-logger",       "Audit Logger",     900, 350),
        _node("compliance-reporter","Compliance Rpt",   1070,200),
        _node("output-validator",   "Output Validator", 1070,350),
    ]
    es9 = [
        _edge(ns9[0]["id"], ns9[1]["id"], "raw"),
        _edge(ns9[1]["id"], ns9[2]["id"], "sanitized"),
        _edge(ns9[2]["id"], ns9[3]["id"], "detected"),
        _edge(ns9[3]["id"], ns9[4]["id"], "redacted"),
        _edge(ns9[4]["id"], ns9[5]["id"], "response"),
        _edge(ns9[5]["id"], ns9[6]["id"], "for review"),
        _edge(ns9[6]["id"], ns9[7]["id"], "log"),
        _edge(ns9[6]["id"], ns9[8]["id"], "approved"),
        _edge(ns9[8]["id"], ns9[9]["id"], "validate"),
    ]
    templates.append(("Compliance-First AI", "regulated",
        "Regulated environment pattern: PII detection + redaction + HITL gate + compliance reporter. Full OMB M-25-21.",
        {"nodes": ns9, "edges": es9},
        {"nist_ai_rmf": 95, "owasp_llm": 90, "omb_m25_21": "compliant"}, 1,
        '["compliance", "regulated", "pii", "hitl", "fedramp"]'))

    # 10. Digital Twin AI
    ns10 = [
        _node("inference-input",    "Scenario Input",   50,  200),
        _node("llm",                "Domain LLM",       220, 200),
        _node("knowledge-graph",    "Domain KG",        220, 350),
        _node("tool-chain",         "Simulation Engine",390, 200),
        _node("tool-chain",         "Monte Carlo",      560, 200),
        _node("classifier",         "Scenario Manager", 560, 350),
        _node("confidence-threshold","Confidence Gate", 730, 200),
        _node("compliance-reporter","Report Generator", 900, 200),
        _node("audit-logger",       "Audit Logger",     900, 350),
        _node("output-validator",   "Output Validator", 1070,200),
    ]
    es10 = [
        _edge(ns10[0]["id"], ns10[1]["id"], "scenario"),
        _edge(ns10[2]["id"], ns10[1]["id"], "domain ctx"),
        _edge(ns10[1]["id"], ns10[3]["id"], "parameters"),
        _edge(ns10[3]["id"], ns10[4]["id"], "simulate"),
        _edge(ns10[4]["id"], ns10[5]["id"], "outcomes"),
        _edge(ns10[5]["id"], ns10[6]["id"], "ranked"),
        _edge(ns10[6]["id"], ns10[7]["id"], "result"),
        _edge(ns10[7]["id"], ns10[8]["id"], "log"),
        _edge(ns10[7]["id"], ns10[9]["id"], "validate"),
    ]
    templates.append(("Digital Twin AI", "simulation",
        "Domain LLM + simulation engine + Monte Carlo + scenario manager + report generator. DoD program twin pattern.",
        {"nodes": ns10, "edges": es10},
        {"nist_ai_rmf": 70, "dod_ai_ethics": "traceable"}, 2,
        '["digital-twin", "simulation", "monte-carlo", "dod"]'))

    # 11. Conversational Intake Agent
    ns11 = [
        _node("inference-input",    "User Message",     50,  200),
        _node("classifier",         "Intent Classifier",220, 200),
        _node("short-term-mem",     "Dialog Manager",   390, 200),
        _node("vector-db",          "RAG KB",           390, 350),
        _node("llm",                "Response LLM",     560, 200),
        _node("classifier",         "Form Extractor",   560, 350),
        _node("data-validator",     "Validator",        730, 200),
        _node("confidence-threshold","Confidence Gate", 730, 350),
        _node("hitl-gate",          "Human Escalation", 900, 275),
        _node("audit-logger",       "Audit Logger",     900, 430),
        _node("output-validator",   "Output Validator", 1070,275),
    ]
    es11 = [
        _edge(ns11[0]["id"], ns11[1]["id"], "message"),
        _edge(ns11[1]["id"], ns11[2]["id"], "intent"),
        _edge(ns11[3]["id"], ns11[2]["id"], "context"),
        _edge(ns11[2]["id"], ns11[4]["id"], "prompt"),
        _edge(ns11[4]["id"], ns11[5]["id"], "extract"),
        _edge(ns11[5]["id"], ns11[6]["id"], "form data"),
        _edge(ns11[4]["id"], ns11[7]["id"], "confidence"),
        _edge(ns11[6]["id"], ns11[8]["id"], "if uncertain"),
        _edge(ns11[7]["id"], ns11[8]["id"], "if low"),
        _edge(ns11[8]["id"], ns11[9]["id"], "log"),
        _edge(ns11[8]["id"], ns11[10]["id"], "output"),
    ]
    templates.append(("Conversational Intake Agent", "gov-dod",
        "Intent classifier + dialog manager + RAG + form extractor + HITL escalation. RICOAS intake pattern.",
        {"nodes": ns11, "edges": es11},
        {"nist_ai_rmf": 80, "omb_m25_21": "compliant", "owasp_llm": 65}, 2,
        '["intake", "conversational", "gov", "dod", "ricoas"]'))

    # 12. AI Security Monitor
    ns12 = [
        _node("baseline-snapshot",  "Behavioral Baseline",50, 200),
        _node("drift-detector",     "Drift Detector",   220, 200),
        _node("classifier",         "Anomaly Detector", 390, 200),
        _node("alert-manager",      "Alert Manager",    560, 200),
        _node("circuit-breaker",    "Circuit Breaker",  560, 350),
        _node("hitl-gate",          "HITL Review",      730, 200),
        _node("siem-forwarder",     "SIEM Forwarder",   730, 350),
        _node("audit-logger",       "Audit Logger",     900, 275),
        _node("compliance-reporter","Incident Report",  1070,275),
    ]
    es12 = [
        _edge(ns12[0]["id"], ns12[1]["id"], "baseline"),
        _edge(ns12[1]["id"], ns12[2]["id"], "delta"),
        _edge(ns12[2]["id"], ns12[3]["id"], "anomaly"),
        _edge(ns12[3]["id"], ns12[4]["id"], "trigger"),
        _edge(ns12[4]["id"], ns12[5]["id"], "escalate"),
        _edge(ns12[3]["id"], ns12[6]["id"], "forward"),
        _edge(ns12[5]["id"], ns12[7]["id"], "log"),
        _edge(ns12[7]["id"], ns12[8]["id"], "report"),
    ]
    templates.append(("AI Security Monitor", "security",
        "Behavioral drift detector + anomaly detector + circuit breaker + SIEM forwarder. MITRE ATLAS coverage.",
        {"nodes": ns12, "edges": es12},
        {"nist_ai_rmf": 75, "mitre_atlas": "covered"}, 1,
        '["security", "monitoring", "drift", "atlas", "siem"]'))

    for name, cat, desc, graph, badges, autonomy, tags in templates:
        conn.execute(
            "INSERT INTO aadc_templates "
            "(id, name, category, description, graph_json, compliance_badges, autonomy_max, tags) "
            "VALUES (%s,%s,%s,%s,%s,%s,%s,%s)",
            (f"tpl-{_uid()}", name, cat, desc,
             json.dumps(graph), json.dumps(badges), autonomy, tags),
        )
    conn.commit()


# ---------------------------------------------------------------------------
# Snippet seeds
# ---------------------------------------------------------------------------

def _seed_snippets(conn) -> None:
    if conn.execute("SELECT COUNT(*) FROM aadc_snippets").fetchone()[0] > 0:
        return

    snippets: list[tuple] = []

    # 1. Basic RAG Retrieval
    ns = [_node("chunker","Chunker",50,50), _node("embedding-model","Embedder",200,50),
          _node("vector-db","Vector DB",350,50), _node("reranker","Re-Ranker",500,50),
          _node("doc-store","Doc Store",50,150)]
    snippets.append(("Basic RAG Retrieval", "retrieval",
        "Chunker → Embedder → Vector DB → Re-Ranker. Core RAG retrieval chain.", ns, _chain(ns[:4]) + [_edge(ns[4]["id"], ns[0]["id"])], 5, '["rag","retrieval","embeddings"]'))

    # 2. HITL Approval Gate
    ns = [_node("confidence-threshold","Confidence Gate",50,50),
          _node("hitl-gate","HITL Review",200,50),
          _node("audit-logger","Audit Log",350,50),
          _node("output-validator","Approved Output",500,50)]
    snippets.append(("HITL Approval Gate", "governance",
        "Confidence gate → human review → audit log → validated output.", ns, _chain(ns), 4, '["hitl","governance","approval"]'))

    # 3. Safety Guardrail Stack
    ns = [_node("input-sanitizer","Input Sanitizer",50,50),
          _node("pii-detector","PII Detector",200,50),
          _node("toxicity-filter","Toxicity Filter",350,50),
          _node("output-validator","Output Validator",500,50)]
    snippets.append(("Safety Guardrail Stack", "safety",
        "Input sanitizer → PII detector → toxicity filter → output validator.", ns, _chain(ns), 4, '["safety","pii","guardrail","owasp"]'))

    # 4. Confidence + Circuit Breaker
    ns = [_node("llm","LLM",50,50),
          _node("confidence-threshold","Confidence Gate",220,50),
          _node("circuit-breaker","Circuit Breaker",390,150),
          _node("hitl-gate","Human Escalation",560,150),
          _node("output-validator","Continue",390,50)]
    es = [_edge(ns[0]["id"],ns[1]["id"]),
          _edge(ns[1]["id"],ns[4]["id"],"if confident"),
          _edge(ns[1]["id"],ns[2]["id"],"if low"),
          _edge(ns[2]["id"],ns[3]["id"],"escalate")]
    snippets.append(("Confidence + Circuit Breaker", "safety",
        "LLM output → confidence gate → circuit breaker on low confidence, continue on high.", ns, es, 5, '["confidence","circuit-breaker","safety","owasp09"]'))

    # 5. MCP Tool Surface
    ns = [_node("mcp-gateway","MCP Gateway",50,50),
          _node("mcp-server","MCP Server",220,50),
          _node("tool-chain","Tool Chain",390,50),
          _node("external-api","External API",560,50),
          _node("audit-logger","Audit Log",390,150)]
    es = _chain(ns[:4]) + [_edge(ns[2]["id"],ns[4]["id"],"log")]
    snippets.append(("MCP Tool Surface", "infrastructure",
        "Auth gateway → MCP server → tool chain → external API + audit log.", ns, es, 5, '["mcp","tools","owasp07"]'))

    # 6. Agent Memory System
    ns = [_node("short-term-mem","Short-Term Mem",50,50),
          _node("long-term-mem","Long-Term Mem",220,50),
          _node("episodic-buffer","Episodic Buffer",390,50),
          _node("embedding-cache","Embed Cache",560,50)]
    snippets.append(("Agent Memory System", "memory",
        "Short-term → long-term → episodic buffer → embedding cache. Full agent memory stack.", ns, _chain(ns), 4, '["memory","agent","episodic"]'))

    # 7. Feedback Loop
    ns = [_node("feedback-collector","Feedback",50,50),
          _node("classifier","Preference Labeler",220,50),
          _node("rlhf-pipeline","RLHF Trainer",390,50),
          _node("fine-tuned-adapter","Model Updater",560,50)]
    snippets.append(("Feedback Loop", "learning",
        "Feedback collector → preference labeler → RLHF trainer → fine-tuned adapter.", ns, _chain(ns), 4, '["rlhf","feedback","learning"]'))

    # 8. Behavioral Baseline
    ns = [_node("baseline-snapshot","Baseline Snapshot",50,50),
          _node("drift-detector","Drift Detector",220,50),
          _node("alert-manager","Alert Manager",390,50),
          _node("audit-logger","Audit Logger",560,50)]
    snippets.append(("Behavioral Baseline", "monitoring",
        "Baseline snapshot → drift detector → alert manager → audit log.", ns, _chain(ns), 4, '["drift","monitoring","baseline","atlas"]'))

    # 9. Agent Spawn Pattern
    ns = [_node("orchestrator","Orchestrator",50,200),
          _node("classifier","Task Decomposer",220,200),
          _node("sub-agent","Sub-Agent A",390,80),
          _node("sub-agent","Sub-Agent B",390,200),
          _node("sub-agent","Sub-Agent C",390,320),
          _node("output-validator","Aggregator",560,200),
          _node("circuit-breaker","Validator",730,200)]
    es = [_edge(ns[0]["id"],ns[1]["id"],"task"),
          _edge(ns[1]["id"],ns[2]["id"]), _edge(ns[1]["id"],ns[3]["id"]), _edge(ns[1]["id"],ns[4]["id"]),
          _edge(ns[2]["id"],ns[5]["id"]), _edge(ns[3]["id"],ns[5]["id"]), _edge(ns[4]["id"],ns[5]["id"]),
          _edge(ns[5]["id"],ns[6]["id"],"validate")]
    snippets.append(("Agent Spawn Pattern", "multi-agent",
        "Orchestrator → task decomposer → N sub-agents → result aggregator → validator.", ns, es, 7, '["multi-agent","spawn","orchestrator"]'))

    # 10. Token Budget Enforcer
    ns = [_node("inference-input","Request",50,50),
          _node("token-budget","Token Counter",220,50),
          _node("rate-limiter","Budget Gate",390,50),
          _node("llm","LLM",560,50),
          _node("guardrail","Truncate/Summarize",390,150)]
    es = [_edge(ns[0]["id"],ns[1]["id"]),
          _edge(ns[1]["id"],ns[2]["id"]),
          _edge(ns[2]["id"],ns[3]["id"],"if ok"),
          _edge(ns[2]["id"],ns[4]["id"],"if over")]
    snippets.append(("Token Budget Enforcer", "infrastructure",
        "Request → token counter → budget gate → LLM (or truncate if over budget).", ns, es, 5, '["token","budget","cost","owasp04"]'))

    # 11. Prompt Registry Lookup
    ns = [_node("prompt-registry","Prompt Registry",50,50),
          _node("classifier","Version Resolver",220,50),
          _node("llm","Template Filler",390,50),
          _node("input-sanitizer","Injection Guard",560,50)]
    snippets.append(("Prompt Registry Lookup", "governance",
        "Prompt registry → version resolver → template filler → injection guard.", ns, _chain(ns), 4, '["prompts","registry","governance","owasp01"]'))

    # 12. AI Audit Trail
    ns = [_node("audit-logger","Event Emitter",50,50),
          _node("guardrail","HMAC Signer",220,50),
          _node("audit-logger","Append-Only Log",390,50),
          _node("siem-forwarder","SIEM Forwarder",560,50)]
    snippets.append(("AI Audit Trail", "compliance",
        "Event emitter → HMAC signer → append-only log → SIEM forwarder.", ns, _chain(ns), 4, '["audit","siem","compliance","nist"]'))

    for name, cat, desc, nodes, edges, nc, tags in snippets:
        conn.execute(
            "INSERT INTO aadc_snippets "
            "(id, name, category, description, graph_json, node_count, tags) "
            "VALUES (%s,%s,%s,%s,%s,%s,%s)",
            (f"snp-{_uid()}", name, cat, desc,
             json.dumps({"nodes": nodes, "edges": edges}), nc, tags),
        )
    conn.commit()


# ---------------------------------------------------------------------------
# Solution Pack seeding
# ---------------------------------------------------------------------------

def _seed_solution_packs(conn) -> None:
    if conn.execute(
        "SELECT COUNT(*) FROM aadc_templates WHERE category='solution-pack'"
    ).fetchone()[0] > 0:
        return

    from tools.agentic_ai_canvas.solution_packs import SOLUTION_PACKS  # noqa: PLC0415
    for p in SOLUTION_PACKS:
        conn.execute(
            "INSERT INTO aadc_templates "
            "(id, name, category, description, graph_json, compliance_badges, autonomy_max, tags) "
            "VALUES (%s,%s,%s,%s,%s,%s,%s,%s)",
            (
                f"sp-{_uid()}",
                p["name"],
                p["category"],
                p["description"],
                json.dumps(p["graph"]),
                json.dumps(p["badges"]),
                p["autonomy_max"],
                p["tags"],
            ),
        )
    conn.commit()


# ---------------------------------------------------------------------------
# Enhancement schema — design links + cost estimates (added post-Phase 10)
# ---------------------------------------------------------------------------

ENHANCEMENT_SCHEMA = """
CREATE TABLE IF NOT EXISTS aadc_design_links (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    src_design_id   TEXT NOT NULL REFERENCES aadc_designs(id),
    tgt_design_id   TEXT NOT NULL REFERENCES aadc_designs(id),
    link_type       TEXT DEFAULT 'calls',
    link_label      TEXT DEFAULT '',
    auto_detected   INTEGER DEFAULT 0,
    metadata_json   TEXT DEFAULT '{}',
    created_at      TEXT DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(src_design_id, tgt_design_id, link_type)
);
CREATE INDEX IF NOT EXISTS idx_aadc_links_src ON aadc_design_links(src_design_id);
CREATE INDEX IF NOT EXISTS idx_aadc_links_tgt ON aadc_design_links(tgt_design_id);

CREATE TABLE IF NOT EXISTS aadc_cost_estimates (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    design_id       TEXT NOT NULL REFERENCES aadc_designs(id),
    model_breakdown TEXT DEFAULT '{}',
    total_per_run   REAL DEFAULT 0,
    total_monthly   REAL DEFAULT 0,
    runs_per_month  INTEGER DEFAULT 1000,
    optimization_hints TEXT DEFAULT '[]',
    generated_at    TEXT DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_aadc_cost_design ON aadc_cost_estimates(design_id);

CREATE TABLE IF NOT EXISTS aadc_aimc_model_refs (
    id              TEXT PRIMARY KEY,
    aadc_design_id  TEXT NOT NULL REFERENCES aadc_designs(id) ON DELETE CASCADE,
    aadc_node_id    TEXT NOT NULL,
    aimc_model_id   TEXT NOT NULL,
    aimc_design_id  TEXT,
    notes           TEXT DEFAULT '',
    created_at      TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_aadc_aimc_refs_design ON aadc_aimc_model_refs(aadc_design_id);
CREATE INDEX IF NOT EXISTS idx_aadc_aimc_refs_model  ON aadc_aimc_model_refs(aimc_model_id);

-- AADC digital twin (twx-cov-01). Snapshots follow the PDC retention pattern
-- (sha256 dedup + bounded auto-snapshot retention) — NOT append-only. No
-- tenant_id/classification columns: canvas tables, accessed via AADC's
-- get_canvas_connection()-backed get_connection().
CREATE TABLE IF NOT EXISTS aadc_twin_snapshots (
    id          TEXT PRIMARY KEY,
    design_id   TEXT NOT NULL,
    label       TEXT,
    graph_json  TEXT NOT NULL DEFAULT '{"nodes":[],"edges":[]}',
    node_count  INTEGER DEFAULT 0,
    edge_count  INTEGER DEFAULT 0,
    created_by  TEXT,
    created_at  TEXT DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_aadc_twin_snapshots_design ON aadc_twin_snapshots(design_id);

CREATE TABLE IF NOT EXISTS aadc_simulations (
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
CREATE INDEX IF NOT EXISTS idx_aadc_simulations_design ON aadc_simulations(design_id);
"""


def _migrate_aadc_enhancement_tables(conn) -> None:
    conn.executescript(ENHANCEMENT_SCHEMA)
    conn.commit()


# ---------------------------------------------------------------------------
# Init
# ---------------------------------------------------------------------------

def init_db() -> None:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = get_connection()
    try:
        conn.executescript(SCHEMA)
        _migrate_aadc_enhancement_tables(conn)
        _seed_templates(conn)
        _seed_snippets(conn)
        _seed_solution_packs(conn)
    finally:
        conn.close()


if __name__ == "__main__":
    init_db()
    conn = get_connection()
    try:
        from tools.db.storage import is_pg
        if is_pg(conn):
            rows = conn.execute(
                "SELECT tablename FROM pg_catalog.pg_tables "
                "WHERE tablename LIKE 'aadc_%' ORDER BY tablename"
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT name FROM sqlite_master "
                "WHERE type='table' AND name LIKE 'aadc_%' ORDER BY name"
            ).fetchall()
        tables = [r[0] for r in rows]
        print(f"AADC Canvas DB initialized: {len(tables)} tables")
        for t in tables:
            count = conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]  # noqa: S608
            print(f"  {t}: {count} rows")
    finally:
        conn.close()
