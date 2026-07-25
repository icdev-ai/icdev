# CUI // SP-CTI
"""AIMC — AI/ML Model Canvas DB initializer.

Creates the aiml_canvas.db schema and seeds canonical templates + snippets.

Dual-backend: SQLite (default) or PostgreSQL.
Set AIMC_STORAGE_BACKEND=postgresql + AIMC_PG_* env vars for PostgreSQL.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

_ICDEV_ROOT = Path(__file__).resolve().parents[3]
DB_PATH = _ICDEV_ROOT / "data" / "aiml_canvas.db"

_AIMC_BACKEND = os.environ.get(
    "AIMC_STORAGE_BACKEND",
    os.environ.get("ICDEV_CANVAS_STORAGE_BACKEND", os.environ.get("ICDEV_STORAGE_BACKEND", "postgresql")),
).lower()


def get_connection():
    """DB connection — SQLite or PostgreSQL, row-factory enabled.

    Canvas tables (aiml_*) have no classification/tenant_id columns, so the
    PostgreSQL path MUST use get_canvas_connection() to avoid RLS injection
    adding a non-existent ``classification`` predicate.
    """
    if _AIMC_BACKEND == "postgresql":
        try:
            from tools.db.storage import get_canvas_connection
            return get_canvas_connection("AIMC_PG_DATABASE")
        except Exception:
            pass
    import sqlite3 as _sqlite3
    conn = _sqlite3.connect(str(DB_PATH))
    conn.row_factory = _sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


SCHEMA = """
CREATE TABLE IF NOT EXISTS aiml_designs (
    id              TEXT PRIMARY KEY,
    name            TEXT NOT NULL,
    description     TEXT DEFAULT '',
    graph_json      TEXT NOT NULL DEFAULT '{"nodes":[],"edges":[],"boundaries":[]}',
    template_id     TEXT,
    classification  TEXT DEFAULT 'CUI',
    il_level        TEXT DEFAULT 'IL4',
    primary_use_case TEXT DEFAULT '',
    adaptation_strategy TEXT DEFAULT 'prompt',
    created_at      TEXT DEFAULT CURRENT_TIMESTAMP,
    updated_at      TEXT DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS aiml_nodes (
    id              TEXT PRIMARY KEY,
    design_id       TEXT NOT NULL REFERENCES aiml_designs(id) ON DELETE CASCADE,
    node_type       TEXT NOT NULL,
    label           TEXT DEFAULT '',
    x               REAL DEFAULT 0,
    y               REAL DEFAULT 0,
    width           REAL DEFAULT 160,
    height          REAL DEFAULT 60,
    classification  TEXT DEFAULT 'CUI',
    properties_json TEXT DEFAULT '{}',
    created_at      TEXT DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_aiml_nodes_design ON aiml_nodes(design_id);
CREATE INDEX IF NOT EXISTS idx_aiml_nodes_type   ON aiml_nodes(node_type);

CREATE TABLE IF NOT EXISTS aiml_edges (
    id              TEXT PRIMARY KEY,
    design_id       TEXT NOT NULL REFERENCES aiml_designs(id) ON DELETE CASCADE,
    source_node_id  TEXT NOT NULL,
    target_node_id  TEXT NOT NULL,
    edge_type       TEXT DEFAULT 'data-flow',
    label           TEXT DEFAULT '',
    classification  TEXT DEFAULT 'CUI',
    created_at      TEXT DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_aiml_edges_design ON aiml_edges(design_id);

CREATE TABLE IF NOT EXISTS aiml_templates (
    id          TEXT PRIMARY KEY,
    name        TEXT NOT NULL,
    category    TEXT DEFAULT 'general',
    description TEXT DEFAULT '',
    graph_json  TEXT NOT NULL DEFAULT '{"nodes":[],"edges":[],"boundaries":[]}',
    il_level    TEXT DEFAULT 'IL4',
    tags        TEXT DEFAULT '[]',
    created_at  TEXT DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS aiml_snippets (
    id          TEXT PRIMARY KEY,
    name        TEXT NOT NULL,
    category    TEXT DEFAULT 'general',
    description TEXT DEFAULT '',
    graph_json  TEXT NOT NULL DEFAULT '{"nodes":[],"edges":[]}',
    tags        TEXT DEFAULT '[]'
);

CREATE TABLE IF NOT EXISTS aiml_assessments (
    id              TEXT PRIMARY KEY,
    design_id       TEXT REFERENCES aiml_designs(id) ON DELETE CASCADE,
    framework_id    TEXT NOT NULL,
    framework_name  TEXT NOT NULL,
    findings_json   TEXT DEFAULT '[]',
    score           REAL DEFAULT 0.0,
    passed          INTEGER DEFAULT 0,
    created_at      TEXT DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_aiml_assessments_design ON aiml_assessments(design_id);

CREATE TABLE IF NOT EXISTS aiml_artifacts (
    id              TEXT PRIMARY KEY,
    design_id       TEXT REFERENCES aiml_designs(id) ON DELETE CASCADE,
    artifact_type   TEXT NOT NULL,
    title           TEXT NOT NULL,
    content_json    TEXT DEFAULT '{}',
    format          TEXT DEFAULT 'json',
    created_at      TEXT DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_aiml_artifacts_design ON aiml_artifacts(design_id);

CREATE TABLE IF NOT EXISTS aiml_versions (
    id              TEXT PRIMARY KEY,
    design_id       TEXT REFERENCES aiml_designs(id) ON DELETE CASCADE,
    version_number  INTEGER NOT NULL,
    graph_json      TEXT NOT NULL,
    change_summary  TEXT DEFAULT '',
    created_at      TEXT DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_aiml_versions_design ON aiml_versions(design_id);

CREATE TABLE IF NOT EXISTS aiml_audit (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    design_id       TEXT,
    user_id         TEXT DEFAULT 'system',
    action          TEXT NOT NULL,
    detail          TEXT DEFAULT '',
    classification  TEXT DEFAULT 'CUI // SP-CTI',
    created_at      TEXT DEFAULT CURRENT_TIMESTAMP
);

-- AIML digital twin (twx-cov-02, wave-2). PDC dedup/retention (NOT append-only).
-- No tenant_id/classification RLS columns: canvas tables, via AIML's
-- get_canvas_connection()-backed get_connection().
CREATE TABLE IF NOT EXISTS aiml_twin_snapshots (
    id          TEXT PRIMARY KEY,
    design_id   TEXT NOT NULL,
    label       TEXT,
    graph_json  TEXT NOT NULL DEFAULT '{"nodes":[],"edges":[]}',
    node_count  INTEGER DEFAULT 0,
    edge_count  INTEGER DEFAULT 0,
    created_by  TEXT,
    created_at  TEXT DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_aiml_twin_snapshots_design ON aiml_twin_snapshots(design_id);

CREATE TABLE IF NOT EXISTS aiml_simulations (
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
CREATE INDEX IF NOT EXISTS idx_aiml_simulations_design ON aiml_simulations(design_id);
"""

# ── Seed Templates ────────────────────────────────────────────────────────────
_TEMPLATES = [
    {
        "id": "tpl-rag-govdoc",
        "name": "RAG — Government Document Q&A",
        "category": "rag",
        "il_level": "IL4",
        "description": "Foundation for a CUI document Q&A system: document corpus → embedding → vector store → LLM with guardrails.",
        "tags": ["rag", "govdoc", "il4", "document-qa"],
        "graph_json": json.dumps({
            "nodes": [
                {"id": "n1", "type": "data-corpus",      "label": "CUI Document Corpus",   "x": 80,  "y": 200, "color": "#64748b"},
                {"id": "n2", "type": "model-embedding",  "label": "Nomic Embed Text",       "x": 280, "y": 200, "color": "#06b6d4"},
                {"id": "n3", "type": "data-vector-store","label": "FAISS Vector Store",     "x": 480, "y": 200, "color": "#0891b2"},
                {"id": "n4", "type": "adapt-rag",        "label": "RAG Pipeline",           "x": 480, "y": 320, "color": "#3b82f6"},
                {"id": "n5", "type": "safety-guardrail", "label": "Input Guardrail",        "x": 680, "y": 150, "color": "#ef4444"},
                {"id": "n6", "type": "model-llm",        "label": "Qwen3 (Local)",          "x": 680, "y": 280, "color": "#6366f1"},
                {"id": "n7", "type": "safety-output-validator", "label": "Output Validator","x": 880, "y": 280, "color": "#f97316"},
                {"id": "n8", "type": "gov-model-card",   "label": "Model Card",             "x": 680, "y": 420, "color": "#6366f1"},
                {"id": "n9", "type": "gov-nist-ai-rmf",  "label": "NIST AI RMF",            "x": 880, "y": 420, "color": "#10b981"},
            ],
            "edges": [
                {"id": "e1", "source": "n1", "target": "n2", "type": "data-flow",      "label": "chunk + embed"},
                {"id": "e2", "source": "n2", "target": "n3", "type": "data-flow",      "label": "store vectors"},
                {"id": "e3", "source": "n3", "target": "n4", "type": "retrieval",      "label": "retrieve top-k"},
                {"id": "e4", "source": "n4", "target": "n6", "type": "inference-call", "label": "augmented prompt"},
                {"id": "e5", "source": "n5", "target": "n6", "type": "safety-check",   "label": "validated input"},
                {"id": "e6", "source": "n6", "target": "n7", "type": "data-flow",      "label": "raw output"},
                {"id": "e7", "source": "n8", "target": "n6", "type": "governance",     "label": "model card"},
                {"id": "e8", "source": "n9", "target": "n6", "type": "governance",     "label": "RMF coverage"},
            ],
            "boundaries": [
                {"id": "b1", "type": "bnd-il-zone", "label": "IL4 — CUI Zone",
                 "x": 60, "y": 60, "width": 880, "height": 480, "il_level": "IL4"},
            ],
        }),
    },
    {
        "id": "tpl-finetune-domain",
        "name": "Fine-Tune — Domain Specialist (Air-Gap)",
        "category": "finetune",
        "il_level": "IL5",
        "description": "QLoRA fine-tuning pipeline for classified domain specialization: dataset → training → eval → Ollama deployment.",
        "tags": ["finetune", "qlora", "airgap", "il5", "domain"],
        "graph_json": json.dumps({
            "nodes": [
                {"id": "n1", "type": "data-dataset",    "label": "Training Dataset (ShareGPT)", "x": 80,  "y": 200},
                {"id": "n2", "type": "data-eval-set",   "label": "Domain Eval Set",             "x": 80,  "y": 360},
                {"id": "n3", "type": "adapt-finetune",  "label": "QLoRA Fine-Tune Job",         "x": 320, "y": 200},
                {"id": "n4", "type": "model-llm",       "label": "Llama 3 8B (base)",           "x": 320, "y": 80},
                {"id": "n5", "type": "eval-benchmark",  "label": "Domain Benchmark",            "x": 560, "y": 360},
                {"id": "n6", "type": "eval-rubric",     "label": "LLM-as-Judge Rubric",         "x": 560, "y": 200},
                {"id": "n7", "type": "model-judge",     "label": "LLM Judge (Qwen3)",           "x": 760, "y": 200},
                {"id": "n8", "type": "deploy-ollama",   "label": "Ollama (Q4_K_M)",             "x": 560, "y": 80},
                {"id": "n9", "type": "gov-model-card",  "label": "Model Card",                  "x": 760, "y": 80},
                {"id": "n10","type": "gov-ai-bom",      "label": "AI BOM",                      "x": 760, "y": 360},
            ],
            "edges": [
                {"id": "e1", "source": "n1", "target": "n3", "type": "training-feed", "label": "training pairs"},
                {"id": "e2", "source": "n4", "target": "n3", "type": "data-flow",     "label": "base weights"},
                {"id": "e3", "source": "n3", "target": "n8", "type": "deployment",    "label": "GGUF export"},
                {"id": "e4", "source": "n2", "target": "n5", "type": "evaluation",    "label": "eval queries"},
                {"id": "e5", "source": "n8", "target": "n6", "type": "inference-call","label": "model outputs"},
                {"id": "e6", "source": "n6", "target": "n7", "type": "evaluation",    "label": "score request"},
                {"id": "e7", "source": "n9", "target": "n8", "type": "governance",    "label": "model card"},
                {"id": "e8", "source": "n10","target": "n4", "type": "governance",    "label": "BOM entry"},
            ],
            "boundaries": [
                {"id": "b1", "type": "bnd-air-gap",    "label": "Air-Gap Enclave",
                 "x": 50, "y": 40, "width": 800, "height": 500, "il_level": "IL5"},
            ],
        }),
    },
    {
        "id": "tpl-prompt-only",
        "name": "Prompt Chain — Zero-Shot Workflow",
        "category": "prompt",
        "il_level": "IL2",
        "description": "Rapid prototype with prompt-only adaptation: system prompt → few-shot examples → output parser. No training data required.",
        "tags": ["prompt", "zero-shot", "il2", "rapid-prototype"],
        "graph_json": json.dumps({
            "nodes": [
                {"id": "n1", "type": "safety-guardrail",   "label": "Input Guardrail",      "x": 80,  "y": 200},
                {"id": "n2", "type": "adapt-prompt",       "label": "Prompt Chain",         "x": 280, "y": 200},
                {"id": "n3", "type": "adapt-fewshot",      "label": "Few-Shot Examples",    "x": 280, "y": 340},
                {"id": "n4", "type": "model-llm",          "label": "Claude Sonnet (Bedrock)","x": 480,"y": 200},
                {"id": "n5", "type": "safety-output-validator","label": "Output Validator", "x": 680, "y": 200},
                {"id": "n6", "type": "eval-ab-test",       "label": "A/B Prompt Test",      "x": 480, "y": 360},
                {"id": "n7", "type": "gov-system-card",    "label": "System Card (OMB M-25-21)","x": 680,"y": 360},
            ],
            "edges": [
                {"id": "e1", "source": "n1", "target": "n2", "type": "safety-check",   "label": "validated"},
                {"id": "e2", "source": "n2", "target": "n4", "type": "inference-call", "label": "prompt"},
                {"id": "e3", "source": "n3", "target": "n2", "type": "data-flow",      "label": "examples"},
                {"id": "e4", "source": "n4", "target": "n5", "type": "data-flow",      "label": "output"},
                {"id": "e5", "source": "n4", "target": "n6", "type": "evaluation",     "label": "A/B compare"},
                {"id": "e6", "source": "n7", "target": "n4", "type": "governance",     "label": "use case reg"},
            ],
            "boundaries": [
                {"id": "b1", "type": "bnd-il-zone", "label": "IL2 — Public",
                 "x": 50, "y": 100, "width": 760, "height": 400, "il_level": "IL2"},
            ],
        }),
    },
    {
        "id": "tpl-hybrid-il6",
        "name": "Hybrid RAG+FT — IL6 SECRET",
        "category": "hybrid",
        "il_level": "IL6",
        "description": "Maximum capability for SECRET-classified corpora: QLoRA fine-tuned base + RAG over classified documents, fully air-gapped.",
        "tags": ["hybrid", "rag", "finetune", "il6", "secret", "airgap"],
        "graph_json": json.dumps({
            "nodes": [
                {"id": "n1",  "type": "data-corpus",         "label": "SECRET Document Corpus", "x": 60,  "y": 180},
                {"id": "n2",  "type": "model-embedding",     "label": "Nomic Embed (Local)",    "x": 240, "y": 180},
                {"id": "n3",  "type": "data-vector-store",   "label": "FAISS (Local)",          "x": 420, "y": 180},
                {"id": "n4",  "type": "data-dataset",        "label": "SECRET Training Data",   "x": 60,  "y": 360},
                {"id": "n5",  "type": "adapt-finetune",      "label": "QLoRA Fine-Tune",        "x": 240, "y": 360},
                {"id": "n6",  "type": "adapt-hybrid",        "label": "RAG + FT Hybrid",        "x": 600, "y": 270},
                {"id": "n7",  "type": "safety-guardrail",    "label": "Input Guardrail",        "x": 800, "y": 180},
                {"id": "n8",  "type": "safety-pii-scrubber", "label": "PII/CUI Scrubber",       "x": 800, "y": 270},
                {"id": "n9",  "type": "model-llm",           "label": "Llama 3 70B (Q4_K_M)",  "x": 1000,"y": 270},
                {"id": "n10", "type": "safety-output-validator","label": "Output Validator",    "x": 1000,"y": 400},
                {"id": "n11", "type": "deploy-ollama",       "label": "Ollama — Air-Gap",       "x": 1200,"y": 270},
                {"id": "n12", "type": "gov-model-card",      "label": "Model Card",             "x": 1000,"y": 80},
                {"id": "n13", "type": "gov-dod-rai",         "label": "DoD RAI 5 Principles",  "x": 1200,"y": 80},
                {"id": "n14", "type": "gov-nist-ai-rmf",     "label": "NIST AI RMF",            "x": 1200,"y": 400},
                {"id": "n15", "type": "eval-red-team",       "label": "Red Team Set",           "x": 600,  "y": 440},
            ],
            "edges": [
                {"id": "e1",  "source": "n1",  "target": "n2",  "type": "data-flow",      "label": "chunk"},
                {"id": "e2",  "source": "n2",  "target": "n3",  "type": "data-flow",      "label": "embed"},
                {"id": "e3",  "source": "n3",  "target": "n6",  "type": "retrieval",      "label": "top-k"},
                {"id": "e4",  "source": "n4",  "target": "n5",  "type": "training-feed",  "label": "pairs"},
                {"id": "e5",  "source": "n5",  "target": "n6",  "type": "data-flow",      "label": "FT weights"},
                {"id": "e6",  "source": "n6",  "target": "n9",  "type": "inference-call", "label": "augmented"},
                {"id": "e7",  "source": "n7",  "target": "n9",  "type": "safety-check",   "label": "validated"},
                {"id": "e8",  "source": "n8",  "target": "n9",  "type": "safety-check",   "label": "scrubbed"},
                {"id": "e9",  "source": "n9",  "target": "n10", "type": "data-flow",      "label": "output"},
                {"id": "e10", "source": "n9",  "target": "n11", "type": "deployment",     "label": "serve"},
                {"id": "e11", "source": "n12", "target": "n9",  "type": "governance",     "label": "model card"},
                {"id": "e12", "source": "n13", "target": "n9",  "type": "governance",     "label": "RAI"},
                {"id": "e13", "source": "n14", "target": "n9",  "type": "governance",     "label": "RMF"},
                {"id": "e14", "source": "n15", "target": "n9",  "type": "evaluation",     "label": "red team"},
            ],
            "boundaries": [
                {"id": "b1", "type": "bnd-air-gap",        "label": "Air-Gap Enclave (SIPR)",
                 "x": 30, "y": 30, "width": 1400, "height": 580, "il_level": "IL6"},
                {"id": "b2", "type": "bnd-classification", "label": "SECRET // NOFORN",
                 "x": 40, "y": 50, "width": 600, "height": 440, "classification": "SECRET"},
            ],
        }),
    },
    # ── CSP Multi-Cloud Templates ─────────────────────────────────────────────
    {
        "id": "tpl-azure-openai-rag",
        "name": "Azure OpenAI RAG — GPT-4o + AI Search (IL4)",
        "category": "rag",
        "il_level": "IL4",
        "description": "Enterprise RAG on Azure Government: GPT-4o + Azure AI Search + content filter + model card. FedRAMP High / IL4 ready.",
        "tags": ["rag", "azure", "gpt-4o", "il4", "govcloud"],
        "graph_json": json.dumps({
            "nodes": [
                {"id": "n1", "type": "data-corpus",         "label": "CUI Document Corpus",        "x": 80,  "y": 200, "color": "#64748b"},
                {"id": "n2", "type": "data-azure-ai-search","label": "Azure AI Search (Vector+BM25)","x": 300, "y": 200, "color": "#0078d4"},
                {"id": "n3", "type": "adapt-rag",           "label": "RAG Pipeline",               "x": 520, "y": 200, "color": "#3b82f6"},
                {"id": "n4", "type": "safety-guardrail",    "label": "Input Guardrail",            "x": 520, "y": 80,  "color": "#ef4444"},
                {"id": "n5", "type": "safety-content-filter","label": "Azure Content Filter",      "x": 740, "y": 80,  "color": "#eab308"},
                {"id": "n6", "type": "model-llm",           "label": "GPT-4o (Azure OpenAI)",      "x": 740, "y": 200, "color": "#6366f1",
                 "properties_json": {"model_id": "gpt-4o"}},
                {"id": "n7", "type": "safety-output-validator","label": "Output Validator",        "x": 960, "y": 200, "color": "#f97316"},
                {"id": "n8", "type": "deploy-azure-ml",     "label": "Azure ML Endpoint",         "x": 960, "y": 80,  "color": "#0078d4"},
                {"id": "n9", "type": "gov-model-card",      "label": "Model Card",                 "x": 740, "y": 360, "color": "#6366f1"},
                {"id": "n10","type": "gov-nist-ai-rmf",     "label": "NIST AI RMF",                "x": 960, "y": 360, "color": "#10b981"},
            ],
            "edges": [
                {"id": "e1", "source": "n1", "target": "n2", "type": "data-flow",      "label": "index docs"},
                {"id": "e2", "source": "n2", "target": "n3", "type": "retrieval",      "label": "hybrid search"},
                {"id": "e3", "source": "n3", "target": "n6", "type": "inference-call", "label": "augmented prompt"},
                {"id": "e4", "source": "n4", "target": "n6", "type": "safety-check",   "label": "validated input"},
                {"id": "e5", "source": "n5", "target": "n6", "type": "safety-check",   "label": "content filtered"},
                {"id": "e6", "source": "n6", "target": "n7", "type": "data-flow",      "label": "raw output"},
                {"id": "e7", "source": "n6", "target": "n8", "type": "deployment",     "label": "managed endpoint"},
                {"id": "e8", "source": "n9", "target": "n6", "type": "governance",     "label": "model card"},
                {"id": "e9", "source": "n10","target": "n6", "type": "governance",     "label": "RMF coverage"},
            ],
            "boundaries": [
                {"id": "b1", "type": "bnd-il-zone", "label": "IL4 — Azure Government",
                 "x": 50, "y": 40, "width": 1000, "height": 480, "il_level": "IL4"},
            ],
        }),
    },
    {
        "id": "tpl-gcp-vertex-production",
        "name": "GCP Vertex AI — Gemini 1.5 Pro Production (IL4)",
        "category": "production",
        "il_level": "IL4",
        "description": "Production RAG on GCP: Gemini 1.5 Pro + 1M context + Vertex AI endpoint + eval benchmark. FedRAMP High.",
        "tags": ["gcp", "gemini", "vertex-ai", "il4", "production"],
        "graph_json": json.dumps({
            "nodes": [
                {"id": "n1", "type": "data-corpus",      "label": "Document Corpus (GCS)",         "x": 80,  "y": 200, "color": "#64748b"},
                {"id": "n2", "type": "model-embedding",  "label": "textembedding-gecko@003",       "x": 300, "y": 200, "color": "#06b6d4",
                 "properties_json": {"model_id": "textembedding-gecko-003"}},
                {"id": "n3", "type": "data-vector-store","label": "Vertex AI Vector Search",       "x": 520, "y": 200, "color": "#0891b2"},
                {"id": "n4", "type": "adapt-rag",        "label": "RAG Pipeline",                  "x": 520, "y": 340, "color": "#3b82f6"},
                {"id": "n5", "type": "safety-guardrail", "label": "Input Guardrail",               "x": 740, "y": 100, "color": "#ef4444"},
                {"id": "n6", "type": "model-llm",        "label": "Gemini 1.5 Pro (Vertex AI)",    "x": 740, "y": 260, "color": "#6366f1",
                 "properties_json": {"model_id": "gemini-1.5-pro"}},
                {"id": "n7", "type": "deploy-vertex-ai", "label": "Vertex AI Prediction",          "x": 960, "y": 160, "color": "#4285f4"},
                {"id": "n8", "type": "eval-benchmark",   "label": "Domain Benchmark",              "x": 960, "y": 340, "color": "#6366f1"},
                {"id": "n9", "type": "gov-model-card",   "label": "Model Card",                    "x": 740, "y": 420, "color": "#6366f1"},
                {"id": "n10","type": "gov-system-card",  "label": "System Card (OMB M-25-21)",     "x": 960, "y": 480, "color": "#8b5cf6"},
            ],
            "edges": [
                {"id": "e1", "source": "n1", "target": "n2", "type": "data-flow",      "label": "chunk + embed"},
                {"id": "e2", "source": "n2", "target": "n3", "type": "data-flow",      "label": "index vectors"},
                {"id": "e3", "source": "n3", "target": "n4", "type": "retrieval",      "label": "top-k retrieve"},
                {"id": "e4", "source": "n4", "target": "n6", "type": "inference-call", "label": "augmented"},
                {"id": "e5", "source": "n5", "target": "n6", "type": "safety-check",   "label": "validated"},
                {"id": "e6", "source": "n6", "target": "n7", "type": "deployment",     "label": "endpoint"},
                {"id": "e7", "source": "n6", "target": "n8", "type": "evaluation",     "label": "benchmark"},
                {"id": "e8", "source": "n9", "target": "n6", "type": "governance",     "label": "model card"},
                {"id": "e9", "source": "n10","target": "n6", "type": "governance",     "label": "system card"},
            ],
            "boundaries": [
                {"id": "b1", "type": "bnd-il-zone", "label": "IL4 — GCP FedRAMP High",
                 "x": 50, "y": 40, "width": 1020, "height": 560, "il_level": "IL4"},
            ],
        }),
    },
    {
        "id": "tpl-oci-govcloud-il4",
        "name": "OCI GovCloud — Cohere Command R+ + NIST RMF (IL4)",
        "category": "governance",
        "il_level": "IL4",
        "description": "RAG-native Cohere Command R+ on OCI GovCloud with NIST AI RMF governance and DoD RAI node.",
        "tags": ["oci", "cohere", "il4", "govcloud", "nist-rmf", "dod-rai"],
        "graph_json": json.dumps({
            "nodes": [
                {"id": "n1", "type": "data-corpus",       "label": "CUI Document Corpus",         "x": 80,  "y": 200, "color": "#64748b"},
                {"id": "n2", "type": "data-vector-store",  "label": "OCI OpenSearch (k-NN)",      "x": 300, "y": 200, "color": "#0891b2"},
                {"id": "n3", "type": "adapt-rag",          "label": "RAG Pipeline (grounded)",    "x": 520, "y": 200, "color": "#3b82f6"},
                {"id": "n4", "type": "safety-guardrail",   "label": "Input Guardrail",            "x": 520, "y": 80,  "color": "#ef4444"},
                {"id": "n5", "type": "model-llm",          "label": "Cohere Command R+ (OCI)",    "x": 740, "y": 200, "color": "#6366f1",
                 "properties_json": {"model_id": "cohere-command-r-plus-oci"}},
                {"id": "n6", "type": "safety-output-validator","label": "Output Validator",       "x": 940, "y": 200, "color": "#f97316"},
                {"id": "n7", "type": "deploy-oci-genai",   "label": "OCI GenAI Endpoint",        "x": 940, "y": 80,  "color": "#f80000"},
                {"id": "n8", "type": "gov-nist-ai-rmf",    "label": "NIST AI RMF",               "x": 740, "y": 380, "color": "#10b981"},
                {"id": "n9", "type": "gov-dod-rai",        "label": "DoD RAI 5 Principles",      "x": 940, "y": 380, "color": "#ef4444"},
                {"id": "n10","type": "gov-ai-bom",         "label": "AI BOM",                    "x": 740, "y": 500, "color": "#0ea5e9"},
            ],
            "edges": [
                {"id": "e1", "source": "n1", "target": "n2", "type": "data-flow",      "label": "index"},
                {"id": "e2", "source": "n2", "target": "n3", "type": "retrieval",      "label": "retrieve"},
                {"id": "e3", "source": "n3", "target": "n5", "type": "inference-call", "label": "grounded prompt"},
                {"id": "e4", "source": "n4", "target": "n5", "type": "safety-check",   "label": "validated"},
                {"id": "e5", "source": "n5", "target": "n6", "type": "data-flow",      "label": "response"},
                {"id": "e6", "source": "n5", "target": "n7", "type": "deployment",     "label": "endpoint"},
                {"id": "e7", "source": "n8", "target": "n5", "type": "governance",     "label": "RMF"},
                {"id": "e8", "source": "n9", "target": "n5", "type": "governance",     "label": "DoD RAI"},
                {"id": "e9", "source": "n10","target": "n5", "type": "governance",     "label": "BOM"},
            ],
            "boundaries": [
                {"id": "b1", "type": "bnd-il-zone", "label": "IL4 — OCI GovCloud",
                 "x": 50, "y": 40, "width": 1000, "height": 580, "il_level": "IL4"},
            ],
        }),
    },
    {
        "id": "tpl-ibm-watsonx-enterprise",
        "name": "IBM watsonx.ai — Granite 13B Enterprise (IL4)",
        "category": "production",
        "il_level": "IL4",
        "description": "Enterprise deployment of IBM Granite 13B on watsonx.ai GovCloud with DoD RAI governance and AI BOM.",
        "tags": ["ibm", "watsonx", "granite", "il4", "govcloud", "enterprise"],
        "graph_json": json.dumps({
            "nodes": [
                {"id": "n1", "type": "data-knowledge-base","label": "Regulatory Knowledge Base",  "x": 80,  "y": 200, "color": "#059669"},
                {"id": "n2", "type": "adapt-rag",          "label": "RAG Pipeline",               "x": 300, "y": 200, "color": "#3b82f6"},
                {"id": "n3", "type": "safety-guardrail",   "label": "Input Guardrail",            "x": 520, "y": 80,  "color": "#ef4444"},
                {"id": "n4", "type": "safety-pii-scrubber","label": "PII/CUI Scrubber",           "x": 520, "y": 200, "color": "#ec4899"},
                {"id": "n5", "type": "model-llm",          "label": "Granite 13B Instruct",       "x": 740, "y": 200, "color": "#6366f1",
                 "properties_json": {"model_id": "granite-13b-instruct"}},
                {"id": "n6", "type": "safety-output-validator","label": "Output Validator",       "x": 940, "y": 200, "color": "#f97316"},
                {"id": "n7", "type": "deploy-watsonx-ai",  "label": "watsonx.ai Deployment Space","x": 940, "y": 80,  "color": "#0f62fe"},
                {"id": "n8", "type": "eval-benchmark",     "label": "Benchmark Suite",            "x": 740, "y": 80,  "color": "#6366f1"},
                {"id": "n9", "type": "gov-model-card",     "label": "Model Card",                 "x": 740, "y": 360, "color": "#6366f1"},
                {"id": "n10","type": "gov-dod-rai",        "label": "DoD RAI 5 Principles",      "x": 940, "y": 360, "color": "#ef4444"},
                {"id": "n11","type": "gov-ai-bom",         "label": "AI BOM (Granite lineage)",  "x": 740, "y": 480, "color": "#0ea5e9"},
            ],
            "edges": [
                {"id": "e1", "source": "n1", "target": "n2", "type": "data-flow",      "label": "retrieve"},
                {"id": "e2", "source": "n2", "target": "n5", "type": "inference-call", "label": "context"},
                {"id": "e3", "source": "n3", "target": "n5", "type": "safety-check",   "label": "validated"},
                {"id": "e4", "source": "n4", "target": "n5", "type": "safety-check",   "label": "scrubbed"},
                {"id": "e5", "source": "n5", "target": "n6", "type": "data-flow",      "label": "output"},
                {"id": "e6", "source": "n5", "target": "n7", "type": "deployment",     "label": "watsonx deploy"},
                {"id": "e7", "source": "n5", "target": "n8", "type": "evaluation",     "label": "benchmark"},
                {"id": "e8", "source": "n9", "target": "n5", "type": "governance",     "label": "model card"},
                {"id": "e9", "source": "n10","target": "n5", "type": "governance",     "label": "DoD RAI"},
                {"id": "e10","source": "n11","target": "n5", "type": "governance",     "label": "BOM"},
            ],
            "boundaries": [
                {"id": "b1", "type": "bnd-il-zone", "label": "IL4 — IBM GovCloud",
                 "x": 50, "y": 40, "width": 1020, "height": 580, "il_level": "IL4"},
            ],
        }),
    },
    {
        "id": "tpl-multi-cloud-hybrid",
        "name": "Multi-Cloud Hybrid — Claude (Bedrock) + Gemini Flash (Vertex)",
        "category": "hybrid",
        "il_level": "IL2",
        "description": "Dual-CSP routing: Claude Opus on Bedrock for complex reasoning, Gemini 1.5 Flash on Vertex for high-volume classification. IL2 / public cloud.",
        "tags": ["multi-cloud", "bedrock", "vertex-ai", "claude", "gemini", "il2"],
        "graph_json": json.dumps({
            "nodes": [
                {"id": "n1", "type": "safety-guardrail",   "label": "Input Guardrail",            "x": 80,  "y": 200, "color": "#ef4444"},
                {"id": "n2", "type": "model-classifier",   "label": "Router / Classifier",        "x": 300, "y": 200, "color": "#14b8a6"},
                {"id": "n3", "type": "model-llm",          "label": "Claude Opus 4.7 (Bedrock)",  "x": 540, "y": 100, "color": "#6366f1",
                 "properties_json": {"model_id": "claude-opus-4-7"}},
                {"id": "n4", "type": "model-llm",          "label": "Gemini 1.5 Flash (Vertex)",  "x": 540, "y": 320, "color": "#6366f1",
                 "properties_json": {"model_id": "gemini-1.5-flash"}},
                {"id": "n5", "type": "safety-output-validator","label": "Output Validator",       "x": 760, "y": 200, "color": "#f97316"},
                {"id": "n6", "type": "deploy-bedrock",     "label": "AWS Bedrock (Claude)",       "x": 960, "y": 100, "color": "#f97316"},
                {"id": "n7", "type": "deploy-vertex-ai",   "label": "Vertex AI (Gemini Flash)",   "x": 960, "y": 320, "color": "#4285f4"},
                {"id": "n8", "type": "eval-ab-test",       "label": "A/B Cost/Quality Test",      "x": 540, "y": 480, "color": "#14b8a6"},
                {"id": "n9", "type": "gov-model-card",     "label": "Model Card (multi-model)",   "x": 760, "y": 420, "color": "#6366f1"},
                {"id": "n10","type": "gov-system-card",    "label": "System Card (OMB M-25-21)",  "x": 960, "y": 480, "color": "#8b5cf6"},
            ],
            "edges": [
                {"id": "e1", "source": "n1", "target": "n2", "type": "safety-check",   "label": "validated"},
                {"id": "e2", "source": "n2", "target": "n3", "type": "inference-call", "label": "complex queries"},
                {"id": "e3", "source": "n2", "target": "n4", "type": "inference-call", "label": "bulk classify"},
                {"id": "e4", "source": "n3", "target": "n5", "type": "data-flow",      "label": "response"},
                {"id": "e5", "source": "n4", "target": "n5", "type": "data-flow",      "label": "response"},
                {"id": "e6", "source": "n3", "target": "n6", "type": "deployment",     "label": "Bedrock"},
                {"id": "e7", "source": "n4", "target": "n7", "type": "deployment",     "label": "Vertex"},
                {"id": "e8", "source": "n3", "target": "n8", "type": "evaluation",     "label": "A/B"},
                {"id": "e9", "source": "n4", "target": "n8", "type": "evaluation",     "label": "A/B"},
                {"id": "e10","source": "n9", "target": "n3", "type": "governance",     "label": "model card"},
                {"id": "e11","source": "n10","target": "n3", "type": "governance",     "label": "system card"},
            ],
            "boundaries": [
                {"id": "b1", "type": "bnd-il-zone", "label": "IL2 — Multi-Cloud Public",
                 "x": 50, "y": 40, "width": 1040, "height": 580, "il_level": "IL2"},
            ],
        }),
    },
]

_SNIPPETS = [
    {
        "id": "snp-rag-core",
        "name": "RAG Core (Embed → Store → Retrieve)",
        "category": "rag",
        "description": "Minimum viable RAG: embedding model + vector store + retrieval edge.",
        "tags": ["rag", "retrieval"],
        "graph_json": json.dumps({
            "nodes": [
                {"id": "s1", "type": "model-embedding",   "label": "Embedding Model", "x": 0,   "y": 0},
                {"id": "s2", "type": "data-vector-store", "label": "Vector Store",    "x": 200, "y": 0},
                {"id": "s3", "type": "adapt-rag",         "label": "RAG Pipeline",    "x": 200, "y": 120},
            ],
            "edges": [
                {"id": "se1", "source": "s1", "target": "s2", "type": "data-flow", "label": "embed"},
                {"id": "se2", "source": "s2", "target": "s3", "type": "retrieval", "label": "retrieve"},
            ],
        }),
    },
    {
        "id": "snp-safety-sandwich",
        "name": "Safety Sandwich (Guard → LLM → Validator)",
        "category": "safety",
        "description": "Input guardrail + LLM + output validator — the baseline safety pattern.",
        "tags": ["safety", "guardrail", "owasp-llm"],
        "graph_json": json.dumps({
            "nodes": [
                {"id": "s1", "type": "safety-guardrail",       "label": "Input Guardrail",  "x": 0,   "y": 0},
                {"id": "s2", "type": "model-llm",              "label": "LLM",              "x": 200, "y": 0},
                {"id": "s3", "type": "safety-output-validator","label": "Output Validator", "x": 400, "y": 0},
            ],
            "edges": [
                {"id": "se1", "source": "s1", "target": "s2", "type": "safety-check",  "label": "validated"},
                {"id": "se2", "source": "s2", "target": "s3", "type": "data-flow",     "label": "output"},
            ],
        }),
    },
    {
        "id": "snp-governance-trio",
        "name": "Governance Trio (Model Card + NIST RMF + DoD RAI)",
        "category": "governance",
        "description": "Three governance nodes covering NIST AI 100-1, AI RMF, and DoD Responsible AI — connect to any LLM node.",
        "tags": ["governance", "nist", "dod-rai", "model-card"],
        "graph_json": json.dumps({
            "nodes": [
                {"id": "s1", "type": "gov-model-card",  "label": "Model Card",    "x": 0,   "y": 0},
                {"id": "s2", "type": "gov-nist-ai-rmf", "label": "NIST AI RMF",  "x": 200, "y": 0},
                {"id": "s3", "type": "gov-dod-rai",     "label": "DoD RAI",      "x": 400, "y": 0},
            ],
            "edges": [],
        }),
    },
    {
        "id": "snp-eval-full",
        "name": "Evaluation Full Stack (Benchmark + Rubric + Red Team)",
        "category": "eval",
        "description": "Complete evaluation coverage: standard benchmark + LLM-as-judge rubric + red team adversarial set.",
        "tags": ["eval", "benchmark", "red-team", "judge"],
        "graph_json": json.dumps({
            "nodes": [
                {"id": "s1", "type": "eval-benchmark", "label": "Benchmark Suite", "x": 0,   "y": 0},
                {"id": "s2", "type": "eval-rubric",    "label": "LLM-as-Judge",   "x": 200, "y": 0},
                {"id": "s3", "type": "eval-red-team",  "label": "Red Team Set",   "x": 400, "y": 0},
                {"id": "s4", "type": "model-judge",    "label": "Judge Model",    "x": 200, "y": 120},
            ],
            "edges": [
                {"id": "se1", "source": "s2", "target": "s4", "type": "evaluation", "label": "score"},
            ],
        }),
    },
    {
        "id": "snp-ft-pipeline",
        "name": "Fine-Tune Pipeline (Dataset → QLoRA → GGUF)",
        "category": "finetune",
        "description": "QLoRA training + GGUF export for local Ollama deployment.",
        "tags": ["finetune", "qlora", "gguf", "ollama"],
        "graph_json": json.dumps({
            "nodes": [
                {"id": "s1", "type": "data-dataset",   "label": "Training Dataset", "x": 0,   "y": 0},
                {"id": "s2", "type": "adapt-finetune", "label": "QLoRA Fine-Tune",  "x": 200, "y": 0},
                {"id": "s3", "type": "deploy-ollama",  "label": "Ollama (GGUF)",   "x": 400, "y": 0},
            ],
            "edges": [
                {"id": "se1", "source": "s1", "target": "s2", "type": "training-feed", "label": "pairs"},
                {"id": "se2", "source": "s2", "target": "s3", "type": "deployment",    "label": "GGUF export"},
            ],
        }),
    },
]


def init_db(verbose: bool = True) -> None:
    """Create schema and seed templates/snippets."""
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = get_connection()
    try:
        conn.executescript(SCHEMA)
        conn.commit()

        ph = "%s" if _AIMC_BACKEND == "postgresql" else "?"

        # Seed templates (upsert)
        for tpl in _TEMPLATES:
            existing = conn.execute(
                f"SELECT id FROM aiml_templates WHERE id={ph}", (tpl["id"],)
            ).fetchone()
            if not existing:
                conn.execute(
                    f"""INSERT INTO aiml_templates
                       (id, name, category, description, graph_json, il_level, tags)
                       VALUES ({ph},{ph},{ph},{ph},{ph},{ph},{ph})""",
                    (
                        tpl["id"], tpl["name"], tpl["category"],
                        tpl["description"], tpl["graph_json"],
                        tpl["il_level"], json.dumps(tpl["tags"]),
                    ),
                )
        conn.commit()

        # Seed snippets (upsert)
        for snp in _SNIPPETS:
            existing = conn.execute(
                f"SELECT id FROM aiml_snippets WHERE id={ph}", (snp["id"],)
            ).fetchone()
            if not existing:
                conn.execute(
                    f"""INSERT INTO aiml_snippets
                       (id, name, category, description, graph_json, tags)
                       VALUES ({ph},{ph},{ph},{ph},{ph},{ph})""",
                    (
                        snp["id"], snp["name"], snp["category"],
                        snp["description"], snp["graph_json"],
                        json.dumps(snp["tags"]),
                    ),
                )
        conn.commit()

        if verbose:
            tpl_count = conn.execute("SELECT COUNT(*) FROM aiml_templates").fetchone()[0]
            snp_count = conn.execute("SELECT COUNT(*) FROM aiml_snippets").fetchone()[0]
            print(f"[AIMC init_db] Schema ready. {tpl_count} templates, {snp_count} snippets.")
    finally:
        conn.close()


if __name__ == "__main__":
    init_db()
