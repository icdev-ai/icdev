# CUI // SP-CTI
"""AIMC — AI/ML Model Canvas constants.

Node palette, foundation model catalog, IL suitability ratings,
compliance rules, and assessment frameworks for the AIMC visual designer.
"""

from __future__ import annotations

# ── Feature Flag ──────────────────────────────────────────────────────────────
AIMC_FEATURE_FLAG = "ICDEV_AIML_CANVAS_ENABLED"

# ── Canvas Node Palette ───────────────────────────────────────────────────────
AIMC_NODE_PALETTE = {
    "models": [
        {
            "type": "model-llm",
            "label": "LLM",
            "icon": "LLM",
            "desc": "Large Language Model — text generation, instruction following, reasoning",
            "color": "#6366f1",
        },
        {
            "type": "model-vlm",
            "label": "Vision-Language Model",
            "icon": "VLM",
            "desc": "Multimodal model — processes text + images (OCR, chart analysis, satellite imagery)",
            "color": "#8b5cf6",
        },
        {
            "type": "model-embedding",
            "label": "Embedding Model",
            "icon": "EMB",
            "desc": "Text/image embedding model — produces dense vectors for semantic search and RAG",
            "color": "#06b6d4",
        },
        {
            "type": "model-reranker",
            "label": "Re-ranker",
            "icon": "RNK",
            "desc": "Cross-encoder re-ranker — improves RAG retrieval precision",
            "color": "#0ea5e9",
        },
        {
            "type": "model-classifier",
            "label": "Classifier",
            "icon": "CLS",
            "desc": "Lightweight classification model — intent detection, topic routing, PII detection",
            "color": "#14b8a6",
        },
        {
            "type": "model-code",
            "label": "Code Model",
            "icon": "COD",
            "desc": "Code generation / completion / review model (StarCoder, CodeLlama, DeepSeek-Coder)",
            "color": "#f59e0b",
        },
        {
            "type": "model-judge",
            "label": "LLM Judge",
            "icon": "JDG",
            "desc": "LLM-as-judge evaluator — scores outputs against rubrics (Prometheus pattern)",
            "color": "#ef4444",
        },
    ],
    "adaptation": [
        {
            "type": "adapt-prompt",
            "label": "Prompt Chain",
            "icon": "PRM",
            "desc": "Prompt-only adaptation — system prompt + user template + output parser, no training data required",
            "color": "#10b981",
        },
        {
            "type": "adapt-rag",
            "label": "RAG Pipeline",
            "icon": "RAG",
            "desc": "Retrieval-Augmented Generation — inject retrieved context at inference time from vector store",
            "color": "#3b82f6",
        },
        {
            "type": "adapt-finetune",
            "label": "Fine-tune Job",
            "icon": "FTJ",
            "desc": "QLoRA / LoRA fine-tuning — update model weights on domain-specific data (GPU required)",
            "color": "#f97316",
        },
        {
            "type": "adapt-hybrid",
            "label": "RAG + Fine-tune",
            "icon": "HYB",
            "desc": "Hybrid adaptation — fine-tuned model + RAG retrieval for peak accuracy on classified corpora",
            "color": "#a855f7",
        },
        {
            "type": "adapt-fewshot",
            "label": "Few-shot Examples",
            "icon": "FSH",
            "desc": "In-context few-shot examples — N example pairs injected into prompt (no training)",
            "color": "#84cc16",
        },
    ],
    "data": [
        {
            "type": "data-corpus",
            "label": "Document Corpus",
            "icon": "DCO",
            "desc": "Unstructured document corpus — PDFs, Word docs, STIGs, regulations, field manuals",
            "color": "#64748b",
        },
        {
            "type": "data-vector-store",
            "label": "Vector Store",
            "icon": "VDB",
            "desc": "Vector/embedding store — ChromaDB, FAISS, pgvector, SQLite-vec",
            "color": "#0891b2",
        },
        {
            "type": "data-dataset",
            "label": "Training Dataset",
            "icon": "DTS",
            "desc": "Fine-tuning dataset — instruction-response pairs (ShareGPT, Alpaca, custom)",
            "color": "#dc2626",
        },
        {
            "type": "data-eval-set",
            "label": "Evaluation Set",
            "icon": "EVS",
            "desc": "Gold-standard evaluation examples with expected outputs for automated scoring",
            "color": "#7c3aed",
        },
        {
            "type": "data-knowledge-base",
            "label": "Knowledge Base",
            "icon": "KNW",
            "desc": "Structured knowledge base — SOPs, runbooks, TTPs, doctrine documents",
            "color": "#059669",
        },
    ],
    "safety": [
        {
            "type": "safety-guardrail",
            "label": "Input Guardrail",
            "icon": "GRD",
            "desc": "Pre-inference safety layer — prompt injection detection, jailbreak resistance, PII scrub",
            "color": "#ef4444",
        },
        {
            "type": "safety-output-validator",
            "label": "Output Validator",
            "icon": "OVL",
            "desc": "Post-inference validation — classification leak check, hallucination detection, format enforcement",
            "color": "#f97316",
        },
        {
            "type": "safety-content-filter",
            "label": "Content Filter",
            "icon": "CFT",
            "desc": "Content safety filter — harmful content detection, CBRN topic blocking, NSFW guard",
            "color": "#eab308",
        },
        {
            "type": "safety-pii-scrubber",
            "label": "PII Scrubber",
            "icon": "PII",
            "desc": "PII/CUI detection and redaction in both input and output streams",
            "color": "#ec4899",
        },
    ],
    "eval": [
        {
            "type": "eval-benchmark",
            "label": "Benchmark Suite",
            "icon": "BNK",
            "desc": "Standard benchmark — MMLU, HumanEval, TruthfulQA, HELM, custom domain eval",
            "color": "#6366f1",
        },
        {
            "type": "eval-rubric",
            "label": "LLM-as-Judge Rubric",
            "icon": "RBC",
            "desc": "Prometheus-pattern rubric — dimension-weighted scoring (accuracy, helpfulness, safety, relevance)",
            "color": "#8b5cf6",
        },
        {
            "type": "eval-red-team",
            "label": "Red Team Set",
            "icon": "RTM",
            "desc": "Adversarial evaluation — ATLAS techniques, jailbreak catalog, harmful instruction set",
            "color": "#ef4444",
        },
        {
            "type": "eval-ab-test",
            "label": "A/B Test",
            "icon": "ABT",
            "desc": "Model A vs B comparison — side-by-side evaluation on matched prompts",
            "color": "#14b8a6",
        },
    ],
    "deployment": [
        {
            "type": "deploy-ollama",
            "label": "Ollama (Local)",
            "icon": "OLL",
            "desc": "Local inference via Ollama — air-gap safe, GGUF quantized models, GPU/CPU",
            "color": "#22c55e",
        },
        {
            "type": "deploy-vllm",
            "label": "vLLM",
            "icon": "VLM",
            "desc": "High-throughput GPU inference server — PagedAttention, continuous batching, OpenAI-compatible API",
            "color": "#3b82f6",
        },
        {
            "type": "deploy-bedrock",
            "label": "AWS Bedrock",
            "icon": "BDR",
            "desc": "Managed inference — Bedrock FedRAMP High, GovCloud, no GPU management",
            "color": "#f97316",
        },
        {
            "type": "deploy-tgi",
            "label": "TGI (HuggingFace)",
            "icon": "TGI",
            "desc": "Text Generation Inference — containerized serving, flash attention, tensor parallelism",
            "color": "#f59e0b",
        },
        {
            "type": "deploy-triton",
            "label": "Triton Inference Server",
            "icon": "TRI",
            "desc": "NVIDIA Triton — multi-model serving, dynamic batching, ONNX/TensorRT optimization",
            "color": "#84cc16",
        },
    ],
    "governance": [
        {
            "type": "gov-model-card",
            "label": "Model Card",
            "icon": "MCD",
            "desc": "NIST AI 100-1 model card — intended use, limitations, performance, bias, safety",
            "color": "#6366f1",
        },
        {
            "type": "gov-system-card",
            "label": "System Card",
            "icon": "SYC",
            "desc": "OMB M-25-21 system card — AI use case registration for federal agencies",
            "color": "#8b5cf6",
        },
        {
            "type": "gov-ai-bom",
            "label": "AI BOM",
            "icon": "ABO",
            "desc": "AI Bill of Materials — model provenance, training data lineage, third-party components",
            "color": "#0ea5e9",
        },
        {
            "type": "gov-nist-ai-rmf",
            "label": "NIST AI RMF",
            "icon": "RMF",
            "desc": "NIST AI RMF 1.0 — GOVERN/MAP/MEASURE/MANAGE functions assessment",
            "color": "#10b981",
        },
        {
            "type": "gov-dod-rai",
            "label": "DoD RAI",
            "icon": "RAI",
            "desc": "DoD Responsible AI 5 Principles — Responsible, Equitable, Traceable, Reliable, Governable",
            "color": "#ef4444",
        },
    ],
    "boundaries": [
        {
            "type": "bnd-il-zone",
            "label": "IL Zone",
            "icon": "ILZ",
            "desc": "Impact Level boundary — all components inside share an IL designation (IL2/4/5/6)",
            "color": "#f97316",
        },
        {
            "type": "bnd-air-gap",
            "label": "Air-Gap Boundary",
            "icon": "AGP",
            "desc": "Air-gap enclave — no internet connectivity; all models must be available locally",
            "color": "#ef4444",
        },
        {
            "type": "bnd-classification",
            "label": "Classification Zone",
            "icon": "CLZ",
            "desc": "CUI/SECRET/TS classification boundary — governs data handling within the zone",
            "color": "#dc2626",
        },
        {
            "type": "bnd-trust",
            "label": "Trust Boundary",
            "icon": "TRB",
            "desc": "Trust boundary between model components — marks where authentication or validation is required",
            "color": "#7c3aed",
        },
    ],
}

# Flat list of all node types (for SQL CHECK constraints)
AIMC_NODE_TYPES: list[str] = [
    node["type"]
    for group in AIMC_NODE_PALETTE.values()
    for node in group
]

# ── Foundation Model Catalog ──────────────────────────────────────────────────
# Each entry: id, name, provider, family, context_window, vram_gb, cost_per_1k_tokens,
#             il_suitability [2,4,5,6], air_gap_ready, quantization_options, notes
FOUNDATION_MODELS = [
    # ── Ollama / Local ────────────────────────────────────────────────────────
    {
        "id": "qwen3-local",
        "name": "Qwen3 (Local)",
        "provider": "Ollama",
        "family": "Qwen3",
        "type": "llm",
        "context_window": 32768,
        "vram_gb": 8,
        "cost_per_1k_tokens": 0.0,
        "il_suitability": [2, 4, 5, 6],
        "air_gap_ready": True,
        "quantization_options": ["Q4_K_M", "Q5_K_M", "Q8_0", "fp16"],
        "notes": "Primary ICDEV™ model — fully air-gap safe, GGUF quantized",
    },
    {
        "id": "llama3-70b-instruct",
        "name": "Llama 3 70B Instruct",
        "provider": "Ollama / Meta",
        "family": "Llama3",
        "type": "llm",
        "context_window": 8192,
        "vram_gb": 48,
        "cost_per_1k_tokens": 0.0,
        "il_suitability": [2, 4, 5, 6],
        "air_gap_ready": True,
        "quantization_options": ["Q4_K_M", "Q5_K_M", "Q8_0"],
        "notes": "Strong reasoning; 48GB VRAM for fp16, 24GB for Q4",
    },
    {
        "id": "llama3-8b-instruct",
        "name": "Llama 3 8B Instruct",
        "provider": "Ollama / Meta",
        "family": "Llama3",
        "type": "llm",
        "context_window": 8192,
        "vram_gb": 8,
        "cost_per_1k_tokens": 0.0,
        "il_suitability": [2, 4, 5, 6],
        "air_gap_ready": True,
        "quantization_options": ["Q4_K_M", "Q5_K_M", "Q8_0"],
        "notes": "Edge deployment; fits RTX 4060 Ti 8GB",
    },
    {
        "id": "mistral-7b-instruct",
        "name": "Mistral 7B Instruct",
        "provider": "Ollama / Mistral AI",
        "family": "Mistral",
        "type": "llm",
        "context_window": 32768,
        "vram_gb": 8,
        "cost_per_1k_tokens": 0.0,
        "il_suitability": [2, 4, 5, 6],
        "air_gap_ready": True,
        "quantization_options": ["Q4_K_M", "Q5_K_M", "Q8_0"],
        "notes": "Long context, efficient; good for document analysis",
    },
    {
        "id": "codellama-34b-instruct",
        "name": "CodeLlama 34B Instruct",
        "provider": "Ollama / Meta",
        "family": "CodeLlama",
        "type": "code",
        "context_window": 16384,
        "vram_gb": 24,
        "cost_per_1k_tokens": 0.0,
        "il_suitability": [2, 4, 5, 6],
        "air_gap_ready": True,
        "quantization_options": ["Q4_K_M", "Q5_K_M"],
        "notes": "Code generation; FIM fill-in-the-middle supported",
    },
    {
        "id": "nomic-embed-text",
        "name": "Nomic Embed Text",
        "provider": "Ollama / Nomic AI",
        "family": "Nomic",
        "type": "embedding",
        "context_window": 8192,
        "vram_gb": 1,
        "cost_per_1k_tokens": 0.0,
        "il_suitability": [2, 4, 5, 6],
        "air_gap_ready": True,
        "quantization_options": ["fp32"],
        "notes": "768-dim embeddings; default ICDEV™ RAG embedding model",
    },
    {
        "id": "mxbai-embed-large",
        "name": "mxbai-embed-large",
        "provider": "Ollama / MixedBread",
        "family": "mxbai",
        "type": "embedding",
        "context_window": 512,
        "vram_gb": 2,
        "cost_per_1k_tokens": 0.0,
        "il_suitability": [2, 4, 5, 6],
        "air_gap_ready": True,
        "quantization_options": ["fp32"],
        "notes": "1024-dim; top MTEB performance for air-gap retrieval",
    },
    # ── AWS Bedrock (GovCloud) ────────────────────────────────────────────────
    {
        "id": "claude-sonnet-4-6",
        "name": "Claude Sonnet 4.6 (Bedrock)",
        "provider": "AWS Bedrock",
        "family": "Claude",
        "type": "llm",
        "context_window": 200000,
        "vram_gb": 0,
        "cost_per_1k_tokens": 0.003,
        "il_suitability": [2, 4],
        "air_gap_ready": False,
        "quantization_options": [],
        "notes": "FedRAMP High (GovCloud); 200K context; prompt caching available",
    },
    {
        "id": "claude-opus-4-7",
        "name": "Claude Opus 4.7 (Bedrock)",
        "provider": "AWS Bedrock",
        "family": "Claude",
        "type": "llm",
        "context_window": 200000,
        "vram_gb": 0,
        "cost_per_1k_tokens": 0.015,
        "il_suitability": [2, 4],
        "air_gap_ready": False,
        "quantization_options": [],
        "notes": "Most capable Claude; use for complex analysis + ANVIL critique",
    },
    {
        "id": "amazon-titan-text-lite",
        "name": "Amazon Titan Text Lite (Bedrock)",
        "provider": "AWS Bedrock",
        "family": "Titan",
        "type": "llm",
        "context_window": 4096,
        "vram_gb": 0,
        "cost_per_1k_tokens": 0.0003,
        "il_suitability": [2, 4],
        "air_gap_ready": False,
        "quantization_options": [],
        "notes": "Cheapest Bedrock option; batch summarization and classification",
    },
    {
        "id": "amazon-titan-embed-v2",
        "name": "Amazon Titan Embed v2 (Bedrock)",
        "provider": "AWS Bedrock",
        "family": "Titan",
        "type": "embedding",
        "context_window": 8192,
        "vram_gb": 0,
        "cost_per_1k_tokens": 0.00002,
        "il_suitability": [2, 4],
        "air_gap_ready": False,
        "quantization_options": [],
        "notes": "1536-dim; binary/int8 quantization option; FedRAMP High",
    },
    {
        "id": "meta-llama3-70b-bedrock",
        "name": "Llama 3 70B Instruct (Bedrock)",
        "provider": "AWS Bedrock",
        "family": "Llama3",
        "type": "llm",
        "context_window": 8192,
        "vram_gb": 0,
        "cost_per_1k_tokens": 0.00265,
        "il_suitability": [2, 4],
        "air_gap_ready": False,
        "quantization_options": [],
        "notes": "Open weights on managed infra; useful for reproducibility audits",
    },
]

# ── IL Suitability Matrix ─────────────────────────────────────────────────────
IL_LEVELS = {
    "IL2": {"label": "IL2 — Public", "color": "#22c55e", "min_risk": "LOW"},
    "IL4": {"label": "IL4 — CUI/GovCloud", "color": "#f59e0b", "min_risk": "MEDIUM"},
    "IL5": {"label": "IL5 — CUI Dedicated", "color": "#f97316", "min_risk": "HIGH"},
    "IL6": {"label": "IL6 — SECRET/SIPR", "color": "#ef4444", "min_risk": "CRITICAL"},
}

IL_REQUIREMENTS = {
    "IL6": {
        "must_be_air_gap": True,
        "must_be_local": True,
        "allowed_providers": ["Ollama"],
        "required_encryption": "NSA Type 1",
        "notes": "SIPR only — no internet-connected models; all inference on-prem",
    },
    "IL5": {
        "must_be_air_gap": True,
        "must_be_local": True,
        "allowed_providers": ["Ollama"],
        "required_encryption": "FIPS 140-3",
        "notes": "Dedicated cloud or on-prem; no shared multi-tenant inference",
    },
    "IL4": {
        "must_be_air_gap": False,
        "must_be_local": False,
        "allowed_providers": ["Ollama", "AWS Bedrock"],
        "required_encryption": "FIPS 140-2",
        "notes": "GovCloud permitted; FedRAMP High authorization required",
    },
    "IL2": {
        "must_be_air_gap": False,
        "must_be_local": False,
        "allowed_providers": ["Ollama", "AWS Bedrock"],
        "required_encryption": "TLS 1.2+",
        "notes": "Public cloud permitted; no CUI processing",
    },
}

# ── Compliance Rules (deterministic checks on canvas graph) ───────────────────
AIMC_COMPLIANCE_RULES = [
    {
        "id": "AIMC-IL-001",
        "title": "IL5/IL6 must use air-gap-ready models only",
        "severity": "CAT1",
        "category": "il_suitability",
        "description": "Any model node in an IL5 or IL6 zone must have air_gap_ready=True. Cloud-connected models are prohibited at IL5+.",
        "check": "il_model_air_gap",
    },
    {
        "id": "AIMC-IL-002",
        "title": "Model IL rating covers zone IL level",
        "severity": "CAT1",
        "category": "il_suitability",
        "description": "Every model node's il_suitability list must include the IL level of its containing IL Zone boundary.",
        "check": "model_il_coverage",
    },
    {
        "id": "AIMC-SAF-001",
        "title": "Input guardrail on every LLM accepting external input",
        "severity": "CAT1",
        "category": "safety",
        "description": "LLM nodes with edges from external data sources must have an Input Guardrail node on the incoming path (OWASP LLM01 — prompt injection).",
        "check": "input_guardrail_present",
    },
    {
        "id": "AIMC-SAF-002",
        "title": "Output validator on every user-facing LLM",
        "severity": "CAT2",
        "category": "safety",
        "description": "LLM nodes whose output reaches end-users or downstream systems must have an Output Validator node (OWASP LLM05 — improper output handling).",
        "check": "output_validator_present",
    },
    {
        "id": "AIMC-SAF-003",
        "title": "PII scrubber on CUI/SECRET zone inputs",
        "severity": "CAT1",
        "category": "safety",
        "description": "Data flowing into models inside CUI or SECRET classification zones must pass through a PII Scrubber node (NIST PT-2, DFARS 252.204-7012).",
        "check": "pii_scrubber_on_classified_input",
    },
    {
        "id": "AIMC-GOV-001",
        "title": "Model card required for each LLM/VLM",
        "severity": "CAT2",
        "category": "governance",
        "description": "Each LLM or VLM node must have a Model Card governance node connected (NIST AI 100-1, OMB M-25-21).",
        "check": "model_card_connected",
    },
    {
        "id": "AIMC-GOV-002",
        "title": "NIST AI RMF node required",
        "severity": "CAT2",
        "category": "governance",
        "description": "Every design must include at least one NIST AI RMF governance node (Executive Order 14110, OMB M-25-21).",
        "check": "nist_ai_rmf_present",
    },
    {
        "id": "AIMC-GOV-003",
        "title": "DoD RAI node required for DoD systems",
        "severity": "CAT2",
        "category": "governance",
        "description": "Designs targeting DoD IL4+ must include a DoD Responsible AI governance node (DoDI 3000.09, DoD AI Strategy).",
        "check": "dod_rai_present_for_dod_il",
    },
    {
        "id": "AIMC-GOV-004",
        "title": "AI BOM connected to all models",
        "severity": "CAT2",
        "category": "governance",
        "description": "All foundation model nodes must be connected to an AI BOM governance node (EO 14028, NTIA SBOM requirements).",
        "check": "ai_bom_connected",
    },
    {
        "id": "AIMC-EVL-001",
        "title": "Evaluation suite required before deployment",
        "severity": "CAT2",
        "category": "evaluation",
        "description": "Every design with a Deployment node must have at least one Evaluation node (Benchmark, Rubric, or Red Team).",
        "check": "eval_before_deploy",
    },
    {
        "id": "AIMC-EVL-002",
        "title": "Red team set required for adversarial risk",
        "severity": "CAT2",
        "category": "evaluation",
        "description": "Designs with models processing classified data or OSINT must include a Red Team evaluation node (MITRE ATLAS AML.M0017).",
        "check": "red_team_for_classified",
    },
    {
        "id": "AIMC-RAG-001",
        "title": "RAG pipeline must connect to vector store",
        "severity": "CAT1",
        "category": "architecture",
        "description": "RAG Pipeline adaptation nodes must have an edge to a Vector Store data node — orphaned RAG nodes indicate incomplete design.",
        "check": "rag_has_vector_store",
    },
    {
        "id": "AIMC-FTJ-001",
        "title": "Fine-tune job must connect to training dataset",
        "severity": "CAT1",
        "category": "architecture",
        "description": "Fine-tune Job nodes must have an edge from a Training Dataset data node — no dataset means no training.",
        "check": "finetune_has_dataset",
    },
    {
        "id": "AIMC-DEP-001",
        "title": "IL6 deployment uses Ollama (local) only",
        "severity": "CAT1",
        "category": "deployment",
        "description": "In IL6 zones, only Ollama (Local) deployment nodes are permitted — vLLM on isolated hardware also acceptable if documented.",
        "check": "il6_local_deploy_only",
    },
    {
        "id": "AIMC-CLF-001",
        "title": "All components within a classification zone",
        "severity": "CAT1",
        "category": "classification",
        "description": "Every model, adaptation, and deployment node must reside within a Classification Zone or IL Zone boundary — ungrouped components are a marking violation.",
        "check": "components_classified",
    },
]

# Flat list of compliance rule IDs (for DB CHECK constraint)
AIMC_COMPLIANCE_RULE_IDS: list[str] = [r["id"] for r in AIMC_COMPLIANCE_RULES]

# ── Assessment Frameworks ────────────────────────────────────────────────────
AIMC_ASSESSMENT_FRAMEWORKS = [
    {
        "id": "nist-ai-rmf",
        "name": "NIST AI RMF 1.0",
        "tool": "tools.compliance.nist_ai_rmf_assessor",
        "description": "4 functions: GOVERN, MAP, MEASURE, MANAGE — 12 requirements",
    },
    {
        "id": "mitre-atlas",
        "name": "MITRE ATLAS v5.4",
        "tool": "tools.compliance.atlas_assessor",
        "description": "34 mitigations across adversarial ML attack lifecycle",
    },
    {
        "id": "owasp-llm",
        "name": "OWASP LLM Top 10 v2025",
        "tool": "tools.compliance.owasp_llm_assessor",
        "description": "10 risk categories for LLM-based applications",
    },
    {
        "id": "dod-rai",
        "name": "DoD Responsible AI 5 Principles",
        "tool": "tools.compliance.dod_rai_assessor",
        "description": "Responsible, Equitable, Traceable, Reliable, Governable",
    },
    {
        "id": "iso-42001",
        "name": "ISO/IEC 42001:2023",
        "tool": "tools.compliance.iso42001_assessor",
        "description": "18 AI Management System requirements",
    },
]

AIMC_ASSESSMENT_FRAMEWORK_IDS: list[str] = [f["id"] for f in AIMC_ASSESSMENT_FRAMEWORKS]

# ── Adaptation Decision Matrix ─────────────────────────────────────────────────
# Used by adaptation_engine.py to recommend strategy
ADAPTATION_MATRIX = {
    "prompt_only": {
        "label": "Prompt-Only",
        "recommended_when": [
            "General-purpose task (summarization, Q&A, translation)",
            "No domain-specific training data available",
            "Latency budget < 200ms",
            "Rapid prototyping / proof of concept",
        ],
        "not_recommended_when": [
            "Specialized jargon or domain terminology required",
            "Consistent output format enforcement needed",
            "High accuracy on narrow task required (> 90%)",
        ],
        "cost": "LOW",
        "time_to_deploy": "HOURS",
        "accuracy_ceiling": "MEDIUM",
    },
    "rag": {
        "label": "RAG Pipeline",
        "recommended_when": [
            "Large corpus of reference documents (SOPs, regs, doctrine)",
            "Answers must cite specific sources",
            "Knowledge changes frequently (corpus can be updated without retraining)",
            "CUI documents too sensitive to include in training",
        ],
        "not_recommended_when": [
            "No document corpus available",
            "Latency budget < 100ms (retrieval adds 50-200ms)",
            "Model needs to learn new reasoning patterns (not just facts)",
        ],
        "cost": "LOW-MEDIUM",
        "time_to_deploy": "DAYS",
        "accuracy_ceiling": "HIGH",
    },
    "finetune": {
        "label": "Fine-Tuning (QLoRA/LoRA)",
        "recommended_when": [
            "Specialized domain requiring new reasoning patterns",
            "Consistent output format / style enforcement",
            "High-volume inference where prompt size reduction saves cost",
            "Domain-specific instruction following (military TOC, SIGINT reporting)",
        ],
        "not_recommended_when": [
            "Less than 500 high-quality training examples",
            "No GPU available (requires 8GB+ VRAM for QLoRA)",
            "Knowledge changes faster than retraining cadence",
        ],
        "cost": "MEDIUM-HIGH",
        "time_to_deploy": "WEEKS",
        "accuracy_ceiling": "VERY HIGH",
    },
    "hybrid": {
        "label": "Fine-Tune + RAG",
        "recommended_when": [
            "Peak accuracy on classified corpus required",
            "Model must learn domain reasoning AND retrieve current facts",
            "Evaluation score < 75% with RAG-only and < 75% with FT-only",
        ],
        "not_recommended_when": [
            "Time or GPU budget is constrained",
            "RAG-only achieves > 85% on evaluation set",
        ],
        "cost": "HIGH",
        "time_to_deploy": "MONTHS",
        "accuracy_ceiling": "MAXIMUM",
    },
}

# ── Quantization Options ──────────────────────────────────────────────────────
QUANTIZATION_CONFIGS = {
    "Q4_K_M": {"label": "Q4_K_M (4-bit)", "vram_multiplier": 0.25, "quality": "GOOD", "notes": "Best quality/size tradeoff for local deployment"},
    "Q5_K_M": {"label": "Q5_K_M (5-bit)", "vram_multiplier": 0.31, "quality": "BETTER", "notes": "Slightly larger, noticeably better quality than Q4"},
    "Q8_0":   {"label": "Q8_0 (8-bit)",   "vram_multiplier": 0.50, "quality": "BEST",   "notes": "Near fp16 quality; use if VRAM permits"},
    "fp16":   {"label": "fp16 (16-bit)",  "vram_multiplier": 1.00, "quality": "NATIVE", "notes": "Full precision; maximum VRAM; use for fine-tuning"},
    "int4":   {"label": "INT4 (AWQ/GPTQ)", "vram_multiplier": 0.22, "quality": "GOOD",  "notes": "GPU-native 4-bit; faster than GGUF Q4 on CUDA"},
}

# ── DoD RAI 5 Principles ──────────────────────────────────────────────────────
DOD_RAI_PRINCIPLES = [
    {
        "id": "rai-responsible",
        "principle": "Responsible",
        "description": "DoD personnel exercise appropriate levels of judgment and care while remaining responsible for the development, deployment, and use of AI capabilities.",
        "nist_controls": ["CA-2", "PL-8", "SA-11", "SI-7"],
        "checks": [
            "Human-in-the-loop gate defined for high-stakes decisions",
            "Operator training requirements documented",
            "Accountability chain established",
        ],
    },
    {
        "id": "rai-equitable",
        "principle": "Equitable",
        "description": "The Department takes deliberate steps to minimize unintended bias in AI capabilities, and seeks to prevent the use of AI capabilities in ways that unlawfully discriminate against individuals.",
        "nist_controls": ["CA-7", "SI-10", "RA-3"],
        "checks": [
            "Bias/fairness evaluation included in eval suite",
            "Training data demographic analysis documented",
            "Disparate impact assessment performed",
        ],
    },
    {
        "id": "rai-traceable",
        "principle": "Traceable",
        "description": "The Department's AI capabilities will be developed and deployed such that relevant personnel understand the technology, development processes, and operational methods applicable to AI capabilities.",
        "nist_controls": ["AU-2", "AU-3", "AU-12", "SA-8"],
        "checks": [
            "Model card documents training provenance",
            "Inference logs retained (audit trail)",
            "XAI/explainability method configured",
        ],
    },
    {
        "id": "rai-reliable",
        "principle": "Reliable",
        "description": "The Department's AI capabilities will have explicit, well-defined uses, and will be tested and assured for safety and effectiveness across their entire life cycles.",
        "nist_controls": ["CA-2", "SA-11", "SI-2", "RA-5"],
        "checks": [
            "Evaluation suite with pass threshold defined",
            "Performance monitoring / drift detection configured",
            "Fallback / fail-safe behavior documented",
        ],
    },
    {
        "id": "rai-governable",
        "principle": "Governable",
        "description": "The Department will design and engineer AI capabilities to fulfill their intended functions while possessing the ability to detect and avoid unintended consequences.",
        "nist_controls": ["CA-7", "IR-4", "PL-8", "SA-15"],
        "checks": [
            "Kill switch / disable mechanism defined",
            "Out-of-distribution detection configured",
            "Incident response plan includes AI failure scenarios",
        ],
    },
]

# ── Edge Types ────────────────────────────────────────────────────────────────
AIMC_EDGE_TYPES = [
    "data-flow",        # Data flows from node A to node B
    "inference-call",   # Model inference request
    "retrieval",        # RAG retrieval edge (corpus → vector store → model)
    "training-feed",    # Training data feeds into fine-tune job
    "evaluation",       # Eval set evaluates model output
    "governance",       # Governance block governs a model node
    "safety-check",     # Safety layer validates data on this edge
    "deployment",       # Model deployed via inference server
    "audit-trail",      # Audit/logging edge
]

EDGE_TYPE_VALUES = AIMC_EDGE_TYPES  # alias for SQL CHECK

# ── NIST AI RMF Control Families (for governance block) ──────────────────────
NIST_AI_RMF_FUNCTIONS = {
    "GOVERN": [
        "GOVERN 1.1 — Policies, processes, procedures, and practices for AI risk management",
        "GOVERN 1.2 — Accountability mechanisms for AI lifecycle",
        "GOVERN 2.1 — Scientific and established knowledge used throughout AI lifecycle",
        "GOVERN 4.1 — Organizational teams understand their AI risk and benefits roles",
        "GOVERN 5.1 — Organizational policies and practices for AI risk management",
        "GOVERN 6.1 — Policies and procedures for AI risk assessment across lifecycle",
    ],
    "MAP": [
        "MAP 1.1 — Context established for AI risk assessment",
        "MAP 1.5 — Organizational risk tolerances documented",
        "MAP 2.1 — Scientific findings and prior risk impacts considered",
        "MAP 3.1 — AI system deployment context categorized",
        "MAP 5.1 — Likelihood and magnitude of potential AI system risks identified",
    ],
    "MEASURE": [
        "MEASURE 1.1 — Approaches for testing and evaluating AI risk identified",
        "MEASURE 2.1 — Test sets reflect deployment conditions",
        "MEASURE 2.5 — AI system to be deployed meets intended purpose",
        "MEASURE 2.10 — Fairness and bias are evaluated",
        "MEASURE 4.1 — Measurement approaches for identified AI risks are in use",
    ],
    "MANAGE": [
        "MANAGE 1.1 — AI risks based on assessments prioritized",
        "MANAGE 2.2 — Mechanisms to sustain effectiveness of AI risk management",
        "MANAGE 3.1 — Responses to identified AI risks developed",
        "MANAGE 4.1 — Post-deployment AI risks and benefits monitored",
    ],
}
