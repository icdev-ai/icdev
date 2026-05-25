
from tools.logging.icdev_logger import get_logger
# CUI // SP-CTI
"""ICDEV™ Pulse configuration — integrated as ICDEV™ marketplace module.

Updated 2026-03-12: Added template support, LLM router integration,
and multi-stage pipeline configuration.
"""

import os
from pathlib import Path

import yaml

logger = get_logger(__name__)

# Load .env if present (for OPENAI_API_KEY, etc.)
_env_path = Path(__file__).resolve().parent.parent.parent / ".env"
if _env_path.exists():
    try:
        from dotenv import load_dotenv

        load_dotenv(_env_path)
    except ImportError:
        # Manual fallback: parse KEY=VALUE lines
        for line in _env_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, _, v = line.partition("=")
                os.environ.setdefault(k.strip(), v.strip())

# Paths — relative to ICDEV™ project root
BASE_DIR = Path(__file__).resolve().parent.parent.parent  # ICDev/
PULSE_DIR = Path(__file__).resolve().parent  # tools/pulse/
DATA_DIR = BASE_DIR / "data"
CONTENT_DIR = BASE_DIR / "data" / "pulse_content"
DRAFTS_DIR = CONTENT_DIR / "drafts"
PUBLISHED_DIR = CONTENT_DIR / "published"
EXPORTS_DIR = CONTENT_DIR / "exports"
TEMPLATES_DIR = PULSE_DIR / "templates"
ARGS_DIR = BASE_DIR / "args"

# Ensure directories exist
for d in [DATA_DIR, DRAFTS_DIR, PUBLISHED_DIR, EXPORTS_DIR, TEMPLATES_DIR]:
    d.mkdir(parents=True, exist_ok=True)

# LLM Configuration
# Pipeline: Ollama qwen3.5 (research+draft) → WriteGuard (deterministic) →
#           Claude Sonnet (rewrite via LLM router) → final WriteGuard check
OLLAMA_BASE_URL = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434")
OLLAMA_DRAFT_MODEL = os.getenv("OLLAMA_DRAFT_MODEL", "qwen3.5:latest")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")  # For DALL-E image generation only

# Image Generation
DALLE_MODEL = os.getenv("DALLE_MODEL", "dall-e-3")
UNSPLASH_ACCESS_KEY = os.getenv("UNSPLASH_ACCESS_KEY", "")

# Video Generation (LTX-Video 2B on local GPU)
VIDEO_GENERATION_ENABLED = os.getenv("PULSE_VIDEO_GENERATION", "true").lower() == "true"
VIDEO_WIDTH = int(os.getenv("PULSE_VIDEO_WIDTH", "704"))
VIDEO_HEIGHT = int(os.getenv("PULSE_VIDEO_HEIGHT", "480"))
VIDEO_FPS = int(os.getenv("PULSE_VIDEO_FPS", "24"))
VIDEO_NUM_FRAMES = int(os.getenv("PULSE_VIDEO_FRAMES", "97"))  # ~4s at 24fps
VIDEO_STEPS = int(os.getenv("PULSE_VIDEO_STEPS", "20"))
VIDEO_DIR = CONTENT_DIR / "videos"
VIDEO_DIR.mkdir(parents=True, exist_ok=True)

# Search Configuration
SEARCH_MAX_RESULTS = int(os.getenv("SEARCH_MAX_RESULTS", "20"))
SEARCH_SOURCES = [
    "reddit",
    "stackoverflow",
    "hackernews",
    "linkedin",
    "dev.to",
    "medium",
]

# Content Configuration
TARGET_WORD_COUNT_MIN = 5000
TARGET_WORD_COUNT_MAX = 6000
GITHUB_REPO_URL = "https://github.com/icdev-ai"
SITE_DOMAIN = "icdev.ai"
SITE_URL = "https://icdev.ai"

# ---------------------------------------------------------------------------
# Research Topics — derived from ICDEV™ capabilities
# ---------------------------------------------------------------------------

# Challenge-Solution: industry pain points that ICDEV™ solves
CHALLENGE_SOLUTION_TOPICS = [
    # Compliance & Authorization
    "FedRAMP authorization takes 12-18 months — how automation cuts it to weeks",
    "CMMC Level 2 certification gaps crushing defense contractors",
    "ATO process bottlenecks: 560 hours of manual security assessment effort",
    "continuous ATO vs point-in-time compliance: why cATO changes everything",
    "managing compliance across 8 frameworks simultaneously (NIST, FedRAMP, CMMC, 800-171, CISA SbD, IEEE 1012, DoDI 5000.87, IEC 62443)",  # noqa: E501
    # Security
    "zero trust architecture implementation challenges for federal agencies",
    "supply chain security and SBOM management at scale",
    "AI security threats: prompt injection, model poisoning, and agentic drift",
    "OWASP LLM Top 10 and MITRE ATLAS defense for AI-powered applications",
    "secure by design: CISA pledge requirements and implementation gaps",
    # DevSecOps
    "DevSecOps pipeline security: shifting left without slowing down delivery",
    "policy-as-code with Kyverno and OPA for Kubernetes security",
    "DORA metrics and value stream bottleneck detection in GovTech",
    # AI & Automation
    "agentic AI governance: trust scoring, behavioral drift, and oversight",
    "explainable AI compliance: SHAP attribution and W3C provenance for auditors",
    "multi-agent orchestration challenges in regulated environments",
    # Requirements & Planning
    "requirements intake gaps that derail federal software projects",
    "digital program twin simulation for risk and schedule prediction",
    "model-based systems engineering (MBSE) and digital thread traceability",
    # Embedded & IoT
    "embedded firmware security and compliance for defense IoT systems",
    "TinyML and edge AI deployment challenges on resource-constrained devices",
]

# Feature Spotlight: specific ICDEV™ features to deep-dive
FEATURE_SPOTLIGHT_TOPICS = [
    # Core Platform
    "ICDEV™ FORGE Framework: 6-layer architecture that separates AI from business logic",
    "ICDEV™ multi-agent architecture: 12 specialized agents with A2A protocol",
    "ICDEV™ LLM Router: token-optimized three-tier model routing (scanner, worker, planner)",
    # Compliance Tools
    "ICDEV™ cATO Live Evidence Engine: continuous OSCAL streaming for real-time compliance",
    "ICDEV™ Crosswalk Engine: implement one NIST control, auto-populate FedRAMP, CMMC, and 800-171",
    "ICDEV™ SSP/POAM Generator: automated ATO artifact generation with CUI markings",
    "ICDEV™ Secure by Design Assessor: CISA SbD and Cloudyrion 8-pillar alignment",
    # Security Tools
    "ICDEV™ Threat Modeler: automated STRIDE analysis with NIST control mapping",
    "ICDEV™ AI Security Suite: prompt injection detection, ATLAS assessment, and trust scoring",
    "ICDEV™ Agent Trust Scorer: behavioral drift monitoring for agentic AI systems",
    # DevOps & Intelligence
    "ICDEV™ PR Intelligence: compliance and security pre-checks before merge",
    "ICDEV™ Developer Scorecard: 6-dimension project health monitoring",
    "ICDEV™ VSM Dashboard: DORA metrics and pipeline bottleneck detection",
    "ICDEV™ Golden Path Scaffolder: compliant project templates with security bootstrap",
    # Requirements & Simulation
    "ICDEV™ RICOAS: AI-driven requirements intake with gap detection and SAFe decomposition",
    "ICDEV™ ATO Simulator: Monte Carlo timeline prediction for authorization planning",
    "ICDEV™ Digital Program Twin: what-if simulation and COA generation",
    # Knowledge & Documentation
    "ICDEV™ GraphRAG: knowledge graph retrieval with scoring profiles and context compression",
    "ICDEV™ DocHub: living documentation engine with 8 audience profiles and 8 export formats",
    "ICDEV™ WriteGuard: deterministic writing quality analysis for compliance documents",
    # Embedded & IoT
    "ICDEV™ SparkPilot: natural language to firmware with FreeRTOS and TinyML",
    "ICDEV™ Fleet Manager: device registry, OTA updates, and canary deployments",
    "ICDEV™ Connector Forge: dynamic API connector generation with sandbox validation",
    # Integration
    "ICDEV™ Harness Engineering: AI agent maturity assessment and loop detection",
    "ICDEV™ Observability Suite: distributed tracing, SHAP attribution, and XAI compliance",
]

# Combined list for backward compatibility (used by researcher.py)
RESEARCH_TOPICS = CHALLENGE_SOLUTION_TOPICS + FEATURE_SPOTLIGHT_TOPICS

# WriteGuard bridge
WRITEGUARD_TONE = "thought-leadership-educational"
WRITEGUARD_QUALITY_THRESHOLD = 60  # Minimum overall score to pass
GOVCON_QUALITY_THRESHOLD = 75  # Enforced gate for GovProposal content

# Google Analytics
GA_MEASUREMENT_ID = os.getenv("GA_MEASUREMENT_ID", "")

# Scheduling
SCHEDULE_INTERVAL = os.getenv("PULSE_SCHEDULE", "daily")

# ---------------------------------------------------------------------------
# Article Templates (loaded from args/pulse_templates.yaml)
# ---------------------------------------------------------------------------

ARTICLE_TEMPLATES = {}
TEMPLATE_DEFAULTS = {}

_templates_path = ARGS_DIR / "pulse_templates.yaml"
if _templates_path.exists():
    try:
        with open(_templates_path, "r", encoding="utf-8") as f:
            _tmpl_data = yaml.safe_load(f) or {}
        ARTICLE_TEMPLATES = _tmpl_data.get("templates", {})
        TEMPLATE_DEFAULTS = _tmpl_data.get("defaults", {})
    except Exception as e:
        logger.warning("Failed to load pulse_templates.yaml: %s", e)

# Available template names for UI
TEMPLATE_NAMES = {
    "challenge_solution": "Challenge-Solution",
    "feature_spotlight": "Feature Spotlight",
}

# Pipeline stages for multi-step wizard
PIPELINE_STAGES = [
    {"id": "research", "label": "Research", "description": "Web scraping + Ollama synthesis"},
    {"id": "draft", "label": "Draft", "description": "Qwen3.5 generates article from template"},
    {"id": "quality_check", "label": "Quality Check", "description": "WriteGuard deterministic analysis"},
    {"id": "rewrite", "label": "Rewrite", "description": "Claude Sonnet rewrites based on findings"},
    {"id": "review", "label": "Review", "description": "Human approval gate"},
    {"id": "publish", "label": "Publish", "description": "Export and push to WordPress"},
]
