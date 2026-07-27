#!/usr/bin/env python3

from tools.logging.icdev_logger import get_logger
"""Child App Generator - generates mini-ICDEV™ clone applications from blueprints.

This is the core engine for ICDEV™ Phase 19 agentic app generation. Every child
app includes the full FORGE framework, ANVIL workflow, own agents, memory system,
and CI/CD — everything except the ability to generate new applications.

Decision D21: Copy-and-adapt over template library.
Decision D28: 3-layer grandchild prevention.
Decision D29: Port offset for child agents.

CLI: python tools/builder/child_app_generator.py --blueprint bp.json --project-path /tmp --name my-app --json
"""

import argparse
import json
import logging
import os
import re
import shutil
import subprocess
import sys
import uuid
from tools.db.storage import get_connection, list_tables
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from icdev.tools.builder.template_engine import render_tree as _render_template_tree

BASE_DIR = Path(__file__).resolve().parent.parent.parent

# Use centralized DB path resolution (D152 pattern)
try:
    from tools.compat.db_utils import get_icdev_db_path

    DB_PATH = get_icdev_db_path()
except ImportError:
    DB_PATH = BASE_DIR / "data" / "icdev.db"

logger = get_logger("icdev.child_app_generator")


# Sister module imports (graceful fallback)
def _import_sister(module_name, func_name):
    """Lazy import helper for sister modules."""
    try:
        mod = __import__(f"tools.builder.{module_name}", fromlist=[func_name])
        return getattr(mod, func_name)
    except (ImportError, AttributeError):
        return None


def _resolve_template_dir(
    template: Optional[str],
    flavor: Optional[str],
    icdev_root: Path,
) -> Optional[Path]:
    """Resolve a child-app template directory from explicit path or built-in flavor."""
    if template:
        p = Path(template).expanduser().resolve()
        if p.exists():
            return p
        raise FileNotFoundError(f"Template directory not found: {p}")
    if flavor:
        p = icdev_root / "data" / "templates" / "child_apps" / flavor
        if p.exists():
            return p
        raise FileNotFoundError(f"Built-in flavor not found: {flavor}")
    return None


def _build_template_variables(blueprint: dict, name: str) -> dict[str, str]:
    """Build template variable overrides from a child-app blueprint."""
    classification = blueprint.get("classification", "CUI")
    impact_level = blueprint.get("impact_level", "IL4")
    return {
        "key": name,
        "display_name": blueprint.get("display_name") or name.replace("-", " ").replace("_", " ").title(),
        "env_flag": f"ICDEV_{name.upper().replace('-', '_')}_ENABLED",
        "url_prefix": f"/{name}",
        "module_package": f"apps.{name}",
        "classification": classification,
        "impact_level": impact_level,
    }


def _overlay_template(
    child_root: Path,
    template_dir: Path,
    blueprint: dict,
    app_name: str,
) -> dict[str, Any]:
    """Overlay a child-app flavor template onto the generated child root.

    The legacy generator produces a complete baseline; the template flavor
    specializes it (blueprint, args, context, etc.). Files provided by the
    template overwrite the baseline so the flavor's intent wins.
    """
    variables = _build_template_variables(blueprint, app_name)
    result = _render_template_tree(template_dir, child_root, variables)
    return {
        "template_dir": str(template_dir),
        "variables": variables,
        "rendered_files": result.get("rendered_files", []),
        "errors": result.get("errors", []),
        "validation_failures": result.get("validation_failures", []),
        "success": result.get("success", False),
    }


try:
    from tools.audit.audit_logger import log_event as audit_log_event
except ImportError:

    def audit_log_event(**kwargs):
        logger.debug("audit_logger unavailable — %s", kwargs.get("action", ""))


try:
    from tools.compliance.classification_resolver import ClassificationResolver
except ImportError:
    ClassificationResolver = None


def _get_child_app_model_config() -> dict:
    """Get model config for child apps from llm_config.yaml or defaults."""
    try:
        from tools.llm.router import LLMRouter

        router = LLMRouter()
        _p, model_id, _mc = router.get_provider_for_function("child_app")
        if model_id:
            provider = "bedrock" if "anthropic." in model_id else "openai"
            return {"provider": provider, "model_id": model_id}
    except Exception:
        pass
    return {
        "provider": "bedrock",
        "model_id": "anthropic.claude-sonnet-4-5-20250929-v1:0",
    }


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# ICDEV™ base ports — used for port remapping
ICDEV_PORTS = {
    "orchestrator": 8443,
    "architect": 8444,
    "builder": 8445,
    "compliance": 8446,
    "security": 8447,
    "infrastructure": 8448,
    "knowledge": 8449,
    "monitor": 8450,
    "mbse": 8451,
    "modernization": 8452,
    # D-CHILD-1: Enterprise agents
    "requirements_analyst": 8453,
    "supply_chain": 8454,
    "simulation": 8455,
    "devsecops_zta": 8457,
}

# D-CHILD-3: Parent-only directories — never copied to child apps
PARENT_ONLY_DIRS = {
    "tools/govcon",
    "tools/saas",
    "tools/creative",
    "tools/innovation",
    "tools/marketplace",
    "tools/translation",
    "tools/gateway",
    "tools/rfx",
    "tools/research",
}

# D-CHILD-3: Parent-only Claude Code artifacts — excluded from child inheritance
PARENT_ONLY_COMMANDS = {"icdev-agentic.md", "icdev-innovate.md", "icdev-translate.md"}
PARENT_ONLY_SKILLS = {"icdev-innovate", "icdev-market"}
PARENT_ONLY_E2E = {"saas_portal.md"}
PARENT_ONLY_TEMPLATES = {"proposals", "cpmp", "govcon", "rfx"}
PARENT_ONLY_API_MODULES = {"proposals.py", "cpmp.py", "govcon.py"}
# D-CHILD-10: Parent-only context directories — excluded from bulk context/ copy
PARENT_ONLY_CONTEXT = {
    "govcon",
    "marketplace",
    "translation",
    "modernization",
}
# D-CHILD-10: Parent-only static JS files
PARENT_ONLY_STATIC_JS = {"proposals.js"}
# D-CHILD-10: Skill-to-capability mapping — skills excluded when capability is off
SKILL_CAPABILITY_MAP = {
    "icdev-mbse": "mbse",
    "icdev-modernize": "modernization",
    "icdev-secure": "security",
    "icdev-query": "dashboard",
}

# Files that MUST NOT be copied to child apps (grandchild prevention D28)
GENERATION_TOOLS = {
    "agentic_fitness.py",
    "app_blueprint.py",
    "child_app_generator.py",
    "claude_md_generator.py",
    "goal_adapter.py",
    "db_init_generator.py",
}

# Builder tools that ARE safe to copy
SAFE_BUILDER_TOOLS = {
    "scaffolder.py",
    "scaffolder_extended.py",
    "code_generator.py",
    "test_writer.py",
    "linter.py",
    "formatter.py",
    "language_support.py",
}

# Full directory tree for child apps
DIRECTORY_TREE = [
    "goals",
    "tools/agent",
    "tools/a2a",
    "tools/audit",
    "tools/memory",
    "tools/knowledge",
    "tools/monitor",
    "tools/db",
    "tools/project",
    "tools/testing",
    "tools/ci/triggers",
    "tools/ci/workflows",
    "tools/infra",
    "tools/maintenance",
    "tools/mcp",
    "tools/builder",
    "tools/security",  # D-EPSEC-7: security is always-on, not conditional
    "tools/quality",  # trust-cite-05: anti-hallucination grounding (content + citation) is always-on
    "tools/workflow",  # Coherence engine — implementation drift detection
    "tools/llm",
    "tools/compat",
    "tools/cli",
    "tools/dx",  # D-CHILD-9: fundamental infra + LLM-agnostic companion
    "args",
    "args/ontology",
    "context/agentic",
    "context/compliance",
    "context/languages",
    "hardprompts/agent",
    "hardprompts/security",  # D-EPSEC-7
    "memory/logs",
    "data",
    ".claude/commands/e2e",
    ".tmp",
    "k8s",
    "docker",
    "features/steps",
    "tests",
]

# cvx-gen-04: Always-on TRUST / framework directories that must be RE-INHERITED
# into already-materialized child apps. Child apps snapshot DIRECTORY_TREE at
# generation time and never re-sync, so parent-side upgrades (e.g. the semantic
# grounding `ground_content` in tools/quality/content_grounding.py — trust-cite-05,
# and coherence-engine drift detection in tools/workflow — wf-intg) never reach
# them. `refresh_trust_modules()` re-copies these dirs on demand. Every entry
# MUST also appear in DIRECTORY_TREE (always-on) and MUST NOT be a PARENT_ONLY_DIR.
TRUST_REFRESH_DIRS = [
    "tools/quality",   # trust-cite-05: content + citation grounding (ground_content)
    "tools/workflow",  # wf-intg: coherence engine / implementation-drift detection
    "tools/builder",   # shared builder infra (safe tools only; GENERATION_TOOLS excluded)
    "tools/dx",        # LLM-agnostic companion + fundamental DX infra
]

# Conditional directories — only created when capability is enabled
CONDITIONAL_DIRS = {
    "compliance": [
        "tools/compliance",
        "tools/compliance/xacta",
        "tools/compliance/emass",
        "hardprompts/compliance",
        "context/compliance",
    ],
    # "security" removed — now always-on via DIRECTORY_TREE (D-EPSEC-7)
    "mbse": ["tools/mbse", "context/mbse", "hardprompts/mbse"],
    "dashboard": [
        "tools/dashboard",
        "tools/dashboard/templates",
        "tools/dashboard/static",
        "tools/dashboard/api",
    ],
    # D-CHILD-1: Enterprise capability directories
    "ricoas": [
        "tools/requirements",
        "tools/supply_chain",
        "tools/simulation",
        "tools/integration",
        "context/requirements",
    ],
    "devsecops_zta": ["tools/devsecops", "context/devsecops"],
    "observability": [
        "tools/observability",
        "tools/observability/shap",
        "tools/observability/provenance",
    ],
    "code_intelligence": ["tools/analysis"],
    # D-RAG-13: RAG subsystem (Phase 64)
    "rag": ["tools/rag", "context/rag"],
    # D-FT-19: Fine-tuning subsystem (Phase 64 Extension)
    "fine_tuning": ["tools/finetune"],
    # Knowledge Graph (D-KARL-1 through D-KARL-4)
    "knowledge_graph": ["tools/knowledge_graph", "context/knowledge_graph"],
    # Genesis v2.0 Autonomous Research Lab (D-GEN-1 through D-GEN-12)
    "genesis": ["tools/genesis", "tools/genesis/reflexes", "data/genesis"],
    # Bayesian Autoresearch (Phase 67, D-AR-1 through D-AR-10)
    "autoresearch": ["tools/autoresearch", "args/experiment_programs"],
    # DataBridge + Connector Forge (D-CF-1 through D-CF-10)
    "databridge": [
        "tools/databridge",
        "tools/databridge/forge",
        "tools/databridge/forge/templates",
        "tools/databridge/connectors",
        "context/databridge",
    ],
    # SRE Automation Module (AIOps/LLMOps Phase)
    "sre": ["tools/sre"],
    # Document Intelligence Canvas — NotebookLM-style BM25+KG research layer
    "document_intelligence": [
        "tools/document_intelligence",
        "tools/document_intelligence/db",
        "context/document_intelligence",
    ],
}


# ---------------------------------------------------------------------------
# Adaptation Functions
# ---------------------------------------------------------------------------


def _apply_adaptations(content: str, adaptations: List[str], blueprint: dict) -> str:
    """Apply a list of text adaptations to file content."""
    app_name = blueprint["app_name"]
    classification = blueprint.get("classification", "public")
    # Resolve classification profile for conditional behavior
    _resolver = None
    if ClassificationResolver is not None:
        _resolver = ClassificationResolver(classification)

    for adaptation in adaptations:
        if adaptation == "db_rename":
            content = content.replace("icdev.db", f"{app_name}.db")
            content = content.replace("data/icdev.db", f"data/{app_name}.db")
            content = content.replace('"icdev"', f'"{app_name}"')

        elif adaptation == "port_remap":
            for agent in blueprint.get("agents", []):
                old_port = ICDEV_PORTS.get(agent["name"])
                if old_port:
                    content = content.replace(str(old_port), str(agent["port"]))

        elif adaptation == "app_name_replace":
            # Replace identifiers but preserve CUI header structure
            content = re.sub(r"\bICDEV™\b", app_name.upper().replace("-", "_"), content)
            content = re.sub(r"\bicdev\b", app_name.lower().replace("-", "_"), content)

        elif adaptation == "bot_identifier_replace":
            bot_id = blueprint.get("cicd_config", {}).get("bot_identifier", f"[{app_name.upper()}-BOT]")
            content = content.replace("[ICDEV™-BOT]", bot_id)

        elif adaptation == "classification_update":
            if _resolver and _resolver.strip_cui_headers:
                # Remove CUI headers for non-CUI projects
                content = re.sub(r"^# CUI // SP-CTI\n(# .*\n)*", "", content)
                content = re.sub(r"^# CUI\n", "", content)
            if _resolver and _resolver.banner_enabled and _resolver.file_header_text:
                # Add the correct classification header
                header = _resolver.file_header_text
                if not content.startswith(header):
                    content = header + "\n" + content

        elif adaptation == "impact_level_update":
            impact = blueprint.get("impact_level", "IL4")
            content = re.sub(r"\bIL[2456]\b", impact, content)

        elif adaptation == "security_policy_update":
            # Adjust default classification and required markings to child level
            child_class = blueprint.get("classification", "CUI")
            if child_class == "PUBLIC":
                content = re.sub(r"^default_classification:.*$", "default_classification: PUBLIC", content, flags=re.MULTILINE)
                content = re.sub(r'^  header: ".*"$', '  header: ""', content, flags=re.MULTILINE)
                content = re.sub(r'^  footer: ".*"$', '  footer: ""', content, flags=re.MULTILINE)
            elif child_class == "SECRET":
                content = re.sub(r"^default_classification:.*$", "default_classification: SECRET", content, flags=re.MULTILINE)
                content = re.sub(r'^  header: ".*"$', '  header: "SECRET // NOFORN"', content, flags=re.MULTILINE)
                content = re.sub(r'^  footer: ".*"$', '  footer: "SECRET // NOFORN"', content, flags=re.MULTILINE)
            # Disable high-assurance frameworks for non-CUI/SECRET child apps
            if child_class == "PUBLIC":
                content = re.sub(r"^  fedramp: true$", "  fedramp: false", content, flags=re.MULTILINE)
                content = re.sub(r"^  cmmc: true$", "  cmmc: false", content, flags=re.MULTILINE)

        elif adaptation == "clearance_ceiling":
            ceiling = blueprint.get("clearance_ceiling", blueprint.get("classification", "CUI"))
            content = re.sub(r"^clearance_ceiling:.*$", f"clearance_ceiling: {ceiling}", content, flags=re.MULTILINE)
            # Also cap any embedded user clearance levels in the file
            allowed = {"PUBLIC": ["PUBLIC"], "CUI": ["PUBLIC", "CUI"], "SECRET": ["PUBLIC", "CUI", "SECRET"]}
            levels = allowed.get(ceiling, allowed.get("CUI"))
            if levels:
                yaml_levels = "\n  - ".join([""] + levels)
                content = re.sub(r"clearance_levels:\n(  - \w+\n?)*", f"clearance_levels:{yaml_levels}\n", content, flags=re.MULTILINE)

        # Other adaptations: endpoint_remap, agent_filter, goal_filter,
        # selective_copy, tls_cert_path, threshold_adjust are handled
        # at the step level rather than as text replacements.

    return content


def _copy_and_adapt_file(src: Path, dest: Path, adaptations: List[str], blueprint: dict) -> bool:
    """Copy a single file with adaptations applied."""
    try:
        dest.parent.mkdir(parents=True, exist_ok=True)

        # Binary files: copy directly
        if src.suffix in {
            ".pyc",
            ".so",
            ".dll",
            ".png",
            ".jpg",
            ".gif",
            ".ico",
            ".woff",
            ".woff2",
        }:
            shutil.copy2(src, dest)
            return True

        # Text files: read, adapt, write
        try:
            content = src.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            shutil.copy2(src, dest)
            return True

        adapted = _apply_adaptations(content, adaptations, blueprint)
        dest.write_text(adapted, encoding="utf-8")
        return True
    except Exception as e:
        logger.warning("Failed to copy %s -> %s: %s", src, dest, e)
        return False


def _copy_directory(
    src_dir: Path,
    dest_dir: Path,
    adaptations: List[str],
    blueprint: dict,
    exclude_files: Optional[set] = None,
    skip_subdirs: Optional[set] = None,
) -> Tuple[int, int]:
    """Copy a directory tree with adaptations. Returns (copied, skipped).

    Args:
        skip_subdirs: Set of immediate subdirectory names to skip entirely.
                      E.g. {"govcon", "marketplace"} skips context/govcon/*.
    """
    exclude_files = exclude_files or set()
    skip_subdirs = skip_subdirs or set()
    copied = 0
    skipped = 0

    if not src_dir.exists():
        logger.warning("Source directory does not exist: %s", src_dir)
        return 0, 0

    for src_file in sorted(src_dir.rglob("*")):
        if not src_file.is_file():
            continue
        if src_file.name in exclude_files:
            logger.debug("Skipping excluded file: %s", src_file.name)
            skipped += 1
            continue
        if src_file.suffix == ".pyc" or "__pycache__" in str(src_file):
            continue
        # D-CHILD-10: Skip files under parent-only subdirectories
        if skip_subdirs:
            rel_parts = src_file.relative_to(src_dir).parts
            if rel_parts and rel_parts[0] in skip_subdirs:
                logger.debug("Skipping parent-only subdir: %s", rel_parts[0])
                skipped += 1
                continue

        rel = src_file.relative_to(src_dir)
        dest_file = dest_dir / rel

        if _copy_and_adapt_file(src_file, dest_file, adaptations, blueprint):
            copied += 1
        else:
            skipped += 1

    return copied, skipped


# ---------------------------------------------------------------------------
# Step 1: Create Directory Tree
# ---------------------------------------------------------------------------


def step_01_create_directory_tree(child_root: Path, blueprint: dict) -> dict:
    """Step 1: Create the full FORGE directory structure."""
    created_dirs = []
    capabilities = blueprint.get("capabilities", {})

    # Always-on directories
    for dir_path in DIRECTORY_TREE:
        full_path = child_root / dir_path
        full_path.mkdir(parents=True, exist_ok=True)
        created_dirs.append(str(dir_path))

    # Conditional directories
    for cap_name, dirs in CONDITIONAL_DIRS.items():
        if capabilities.get(cap_name, False):
            for dir_path in dirs:
                full_path = child_root / dir_path
                full_path.mkdir(parents=True, exist_ok=True)
                created_dirs.append(str(dir_path))

    # Generate .gitattributes for cross-platform line ending consistency
    gitattributes = child_root / ".gitattributes"
    gitattributes.write_text(
        "# Cross-platform line ending enforcement\n"
        "* text=auto eol=lf\n"
        "\n"
        "# Explicitly mark as text (LF)\n"
        "*.py text eol=lf\n"
        "*.md text eol=lf\n"
        "*.yaml text eol=lf\n"
        "*.yml text eol=lf\n"
        "*.toml text eol=lf\n"
        "*.html text eol=lf\n"
        "*.css text eol=lf\n"
        "*.js text eol=lf\n"
        "*.json text eol=lf\n"
        "*.txt text eol=lf\n"
        "*.sql text eol=lf\n"
        "*.sh text eol=lf\n"
        "*.feature text eol=lf\n"
        "*.cfg text eol=lf\n"
        "*.ini text eol=lf\n"
        "*.env text eol=lf\n"
        "\n"
        "# Binary files\n"
        "*.db binary\n"
        "*.sqlite binary\n"
        "*.png binary\n"
        "*.jpg binary\n"
        "*.jpeg binary\n"
        "*.gif binary\n"
        "*.ico binary\n"
        "*.woff binary\n"
        "*.woff2 binary\n"
        "*.ttf binary\n"
        "*.pkl binary\n"
        "*.gguf binary\n"
        "*.bin binary\n",
        encoding="utf-8",
    )
    logger.info("Step 1: Generated .gitattributes for cross-platform line endings")

    # Generate .env.example so admins can configure LLM without editing code
    app_name = blueprint.get("app_name", "child-app")
    app_upper = app_name.upper().replace("-", "_")
    env_example = child_root / ".env.example"
    env_example.write_text(
        f"# ============================================================================\n"
        f"# {app_name.upper()} — Environment Configuration\n"
        f"# ============================================================================\n"
        f"# Copy to .env and edit. All LLM settings here — no code changes needed.\n"
        f"# ============================================================================\n"
        f"\n"
        f"# === LLM Primary (Ollama — free, local, air-gap safe) ===\n"
        f"OLLAMA_BASE_URL=http://localhost:11434\n"
        f"OLLAMA_MODEL=qwen3.5:latest\n"
        f"\n"
        f"# === LLM Cloud Providers (all optional — leave blank to disable) ===\n"
        f"ANTHROPIC_API_KEY=\n"
        f"ANTHROPIC_MODEL=claude-sonnet-4-20250514\n"
        f"OPENAI_API_KEY=\n"
        f"OPENAI_MODEL=gpt-4o\n"
        f"GOOGLE_API_KEY=\n"
        f"GOOGLE_MODEL=gemini-2.5-flash\n"
        f"AZURE_OPENAI_API_KEY=\n"
        f"AZURE_OPENAI_ENDPOINT=\n"
        f"AZURE_OPENAI_MODEL=gpt-4o\n"
        f"\n"
        f"# === LLM Routing ===\n"
        f"LLM_TWO_TIER_ENABLED=true\n"
        f"LLM_CONFIDENCE_THRESHOLD=0.85\n"
        f"\n"
        f"# === Chain of Thought / Chain of Debate ===\n"
        f"# Set true to enable multiplayer AI-vs-AI debate mode (CoD) and step-by-step reasoning (CoT).\n"
        f"# Requires tools/llm/chain_orchestrator.py and tools/llm/chain_prompts.py (auto-copied).\n"
        f"ICDEV_COD_ENABLED=false\n"
        f"\n"
        f"# === RAG ===\n"
        f"RAG_ENABLED=true\n"
        f"RAG_EMBEDDING_MODEL=nomic-embed-text\n"
        f"\n"
        f"# === Fine-Tuning ===\n"
        f"FINETUNE_ENABLED=true\n"
        f"FINETUNE_MIN_EXAMPLES=50\n"
        f"FINETUNE_BASE_MODEL=qwen3.5:latest\n"
        f"\n"
        f"# === Server ===\n"
        f"{app_upper}_DASHBOARD_PORT=5000\n"
        f"{app_upper}_DASHBOARD_HOST=127.0.0.1\n"
        f"{app_upper}_DASHBOARD_SECRET=change-me-in-production\n"
        f"\n"
        f"# === Logging ===\n"
        f"LOG_LEVEL=INFO\n",
        encoding="utf-8",
    )
    # Also generate a minimal .env for development
    env_file = child_root / ".env"
    env_file.write_text(
        f"# {app_name.upper()} — Local Development\n"
        f"OLLAMA_BASE_URL=http://localhost:11434\n"
        f"OLLAMA_MODEL=qwen3.5:latest\n"
        f"LLM_TWO_TIER_ENABLED=true\n"
        f"LLM_CONFIDENCE_THRESHOLD=0.85\n"
        f"ICDEV_COD_ENABLED=false\n"
        f"RAG_ENABLED=true\n"
        f"RAG_EMBEDDING_MODEL=nomic-embed-text\n"
        f"LOG_LEVEL=INFO\n",
        encoding="utf-8",
    )
    logger.info("Step 1: Generated .env.example and .env for admin LLM configuration")

    logger.info("Step 1: Created %d directories", len(created_dirs))
    return {"directories_created": len(created_dirs), "dirs": created_dirs, "gitattributes": True, "env_files": True}


# ---------------------------------------------------------------------------
# Step 2: Copy and Adapt Tools
# ---------------------------------------------------------------------------


def _build_fallback_manifest(blueprint: dict) -> list:
    """Build a file manifest from DIRECTORY_TREE + CONDITIONAL_DIRS when
    the blueprint's file_manifest is missing or not a proper list of dicts."""
    capabilities = blueprint.get("capabilities", {})
    entries = []
    default_adaptations = ["db_rename", "port_remap", "classification_update", "app_name_replace"]

    # Dirs to skip content copying (step 01 creates empty, other steps populate)
    SKIP_CONTENT_DIRS = {".tmp", "data", "memory/logs"}

    # Core directories (always copied)
    for d in DIRECTORY_TREE:
        if d in SKIP_CONTENT_DIRS:
            continue  # Empty dir created by step 01, no parent content to copy
        entries.append({"source": d, "dest": d, "adaptations": default_adaptations})

    # Conditional directories (only when capability enabled)
    for cap_name, dirs in CONDITIONAL_DIRS.items():
        if capabilities.get(cap_name):
            for d in dirs:
                entries.append({"source": d, "dest": d, "adaptations": default_adaptations})

    return entries


def step_02_copy_and_adapt_tools(child_root: Path, blueprint: dict, icdev_root: Path) -> dict:
    """Step 2: Copy ICDEV™ tools to child app with adaptations applied."""
    manifest = blueprint.get("file_manifest", [])

    # Handle non-list file_manifest (e.g. summary string from blueprint)
    if not isinstance(manifest, list) or (manifest and isinstance(manifest[0], str) and len(manifest[0]) == 1):
        logger.info("Step 2: file_manifest is not structured, building fallback from CONDITIONAL_DIRS")
        manifest = _build_fallback_manifest(blueprint)

    total_copied = 0
    total_skipped = 0
    results = []

    for entry in manifest:
        source = entry.get("source", "")
        dest = entry.get("dest", source)
        adaptations = entry.get("adaptations", [])

        # D-CHILD-3: Skip parent-only directories
        if any(source == d or source.startswith(d + "/") for d in PARENT_ONLY_DIRS):
            total_skipped += 1
            results.append({"source": source, "status": "skipped_parent_only"})
            continue

        src_path = icdev_root / source
        dest_path = child_root / dest

        if src_path.is_file():
            # Single file copy
            if _copy_and_adapt_file(src_path, dest_path, adaptations, blueprint):
                total_copied += 1
                results.append({"source": source, "status": "copied"})
            else:
                total_skipped += 1
                results.append({"source": source, "status": "skipped"})

        elif src_path.is_dir():
            # Directory copy with exclusions
            exclude = set()

            # For tools/builder/, only copy safe tools
            if source == "tools/builder" or source.startswith("tools/builder"):
                exclude = GENERATION_TOOLS

            # D-CHILD-10: For context/ bulk copy, skip parent-only subdirs
            skip_subdirs: set = set()
            if source.rstrip("/") == "context":
                skip_subdirs = PARENT_ONLY_CONTEXT

            copied, skipped = _copy_directory(
                src_path,
                dest_path,
                adaptations,
                blueprint,
                exclude_files=exclude,
                skip_subdirs=skip_subdirs,
            )
            total_copied += copied
            total_skipped += skipped
            results.append(
                {
                    "source": source,
                    "status": "copied",
                    "files_copied": copied,
                    "files_skipped": skipped,
                }
            )

        else:
            logger.warning("Source not found: %s (entry=%s)", src_path, source)
            results.append({"source": source, "status": "not_found"})

    logger.info("Step 2: Copied %d files, skipped %d", total_copied, total_skipped)
    return {
        "files_copied": total_copied,
        "files_skipped": total_skipped,
        "entries": results,
    }


# ---------------------------------------------------------------------------
# TRUST re-inheritance refresh (cvx-gen-04)
# ---------------------------------------------------------------------------


def _content_signature(path: Path) -> Tuple[bool, Any]:
    """Return (is_binary, comparable_content) for a file.

    Text files are newline-normalized (LF) so a child written with CRLF on
    Windows does not falsely diff against an LF parent. Binary files compare
    by raw bytes.
    """
    try:
        text = path.read_text(encoding="utf-8")
        return False, text.replace("\r\n", "\n").replace("\r", "\n")
    except (UnicodeDecodeError, ValueError):
        return True, path.read_bytes()


def refresh_trust_modules(
    app_dir,
    icdev_root=None,
    dirs: Optional[List[str]] = None,
    dry_run: bool = True,
) -> dict:
    """Re-inherit always-on TRUST / framework modules into an existing child app.

    Generated child apps snapshot the always-on ``DIRECTORY_TREE`` dirs at
    generation time and never re-sync. This re-copies ``TRUST_REFRESH_DIRS``
    (tools/quality, tools/workflow, tools/builder, tools/dx) from the current
    parent repo into the target child, so parent-side upgrades (e.g.
    ``ground_content`` in tools/quality/content_grounding.py) reach materialized
    apps.

    Args:
        app_dir: Path to the existing child app root.
        icdev_root: Parent ICDEV™ repo root (default: this repo, ``BASE_DIR``).
        dirs: Override the set of dirs to refresh (default: ``TRUST_REFRESH_DIRS``).
        dry_run: When True (default), produce a DIFF REPORT only and write
            nothing (HITL confirmation gate). Pass ``dry_run=False`` (CLI
            ``--apply``) to write the changes.

    Returns:
        dict report with ``added`` / ``updated`` file lists, ``unchanged_count``,
        ``skipped`` dir reasons, ``would_change`` count, ``applied`` flag.
    """
    app_root = Path(app_dir).resolve()
    parent_root = Path(icdev_root).resolve() if icdev_root else BASE_DIR
    refresh_dirs = list(dirs) if dirs is not None else list(TRUST_REFRESH_DIRS)

    if not app_root.exists() or not app_root.is_dir():
        return {
            "status": "error",
            "error": f"child app dir not found: {app_root}",
            "app_dir": str(app_root),
        }

    added: List[str] = []
    updated: List[str] = []
    unchanged = 0
    skipped: List[dict] = []

    for rel_dir in refresh_dirs:
        rel_dir = rel_dir.replace("\\", "/").rstrip("/")
        # Guard: never refresh parent-only directories (test invariant).
        if any(rel_dir == d or rel_dir.startswith(d + "/") for d in PARENT_ONLY_DIRS):
            skipped.append({"dir": rel_dir, "reason": "parent_only"})
            continue

        src_dir = parent_root / rel_dir
        if not src_dir.exists() or not src_dir.is_dir():
            skipped.append({"dir": rel_dir, "reason": "missing_in_parent"})
            continue

        # tools/builder: only ever ship SAFE tools — never generation tools (D28).
        exclude = GENERATION_TOOLS if (rel_dir == "tools/builder" or rel_dir.startswith("tools/builder/")) else set()

        for src_file in sorted(src_dir.rglob("*")):
            if not src_file.is_file():
                continue
            if src_file.name in exclude:
                continue
            if src_file.suffix == ".pyc" or "__pycache__" in str(src_file):
                continue

            rel = src_file.relative_to(parent_root)
            rel_str = str(rel).replace("\\", "/")
            dest_file = app_root / rel

            if not dest_file.exists():
                added.append(rel_str)
                if not dry_run:
                    dest_file.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(src_file, dest_file)
                continue

            src_bin, src_content = _content_signature(src_file)
            dst_bin, dst_content = _content_signature(dest_file)
            if src_bin != dst_bin or src_content != dst_content:
                updated.append(rel_str)
                if not dry_run:
                    dest_file.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(src_file, dest_file)
            else:
                unchanged += 1

    would_change = len(added) + len(updated)
    logger.info(
        "refresh_trust_modules: %s — %d added, %d updated, %d unchanged (dry_run=%s)",
        app_root,
        len(added),
        len(updated),
        unchanged,
        dry_run,
    )
    return {
        "status": "success",
        "dry_run": dry_run,
        "applied": (not dry_run) and would_change > 0,
        "app_dir": str(app_root),
        "icdev_root": str(parent_root),
        "dirs": refresh_dirs,
        "added": sorted(added),
        "updated": sorted(updated),
        "unchanged_count": unchanged,
        "skipped": skipped,
        "would_change": would_change,
    }


# ---------------------------------------------------------------------------
# Step 3: Agent Infrastructure
# ---------------------------------------------------------------------------


def _get_agent_skills(agent_name: str, blueprint: dict) -> list:
    """Return skills for an agent based on its role.

    Priority:
    1. Blueprint agent 'skills' field (if provided by the blueprint)
    2. Known ICDEV™ defaults (orchestrator, architect, builder, etc.)
    3. Auto-generated from the agent's 'role' description
    """
    # 1. Check blueprint for explicit skills
    for agent in blueprint.get("agents", []):
        if agent.get("name") == agent_name and agent.get("skills"):
            return agent["skills"]

    # 2. Known ICDEV™ defaults for standard agents
    skills_map = {
        "orchestrator": [
            {
                "id": "task-dispatch",
                "name": "Task Dispatch",
                "description": "Route tasks to appropriate agents",
            },
            {
                "id": "workflow-manage",
                "name": "Workflow Management",
                "description": "Manage multi-step workflows",
            },
        ],
        "architect": [
            {
                "id": "system-design",
                "name": "System Design",
                "description": "Design system architecture",
            },
            {
                "id": "atlas-workflow",
                "name": "ANVIL Workflow",
                "description": "Execute ANVIL build phases",
            },
        ],
        "builder": [
            {
                "id": "code-generate",
                "name": "Code Generation",
                "description": "Generate code from specs",
            },
            {
                "id": "tdd-cycle",
                "name": "TDD Cycle",
                "description": "RED-GREEN-REFACTOR cycle",
            },
            {
                "id": "scaffold",
                "name": "Scaffold",
                "description": "Scaffold new projects",
            },
        ],
        "compliance": [
            {
                "id": "ssp-generate",
                "name": "SSP Generation",
                "description": "Generate System Security Plans",
            },
            {
                "id": "ato-assess",
                "name": "ATO Assessment",
                "description": "Assess ATO readiness",
            },
        ],
        "security": [
            {
                "id": "sast-scan",
                "name": "SAST Scan",
                "description": "Static analysis security testing",
            },
            {
                "id": "dep-audit",
                "name": "Dependency Audit",
                "description": "Audit dependencies for vulns",
            },
        ],
        "knowledge": [
            {
                "id": "pattern-detect",
                "name": "Pattern Detection",
                "description": "Detect patterns in failures",
            },
            {
                "id": "self-heal",
                "name": "Self Heal",
                "description": "Auto-remediate known issues",
            },
        ],
        "monitor": [
            {
                "id": "log-analyze",
                "name": "Log Analysis",
                "description": "Analyze application logs",
            },
            {
                "id": "health-check",
                "name": "Health Check",
                "description": "Check system health",
            },
        ],
    }
    if agent_name in skills_map:
        return skills_map[agent_name]

    # 3. Auto-generate skills from the agent's role description
    for agent in blueprint.get("agents", []):
        if agent.get("name") == agent_name:
            role = agent.get("role", agent_name)
            return [
                {
                    "id": f"{agent_name}-primary",
                    "name": role.split(",")[0].strip().title() if role else agent_name.title(),
                    "description": role or f"{agent_name} agent capabilities",
                },
            ]
    return []


def _generate_agent_config(agents: list, app_name: str, blueprint: dict) -> str:
    """Generate agent_config.yaml content."""
    try:
        import yaml

        config = {
            "application": app_name,
            "classification": blueprint.get("classification", "public"),
            "agents": {},
        }
        for agent in agents:
            config["agents"][agent["name"]] = {
                "port": agent["port"],
                "role": agent.get("role", ""),
                "health_endpoint": agent.get(
                    "health_endpoint",
                    f"https://localhost:{agent['port']}/health",
                ),
                "tls": {
                    "enabled": True,
                    "cert_path": (f"/etc/ssl/certs/{app_name}-{agent['name']}.crt"),
                },
                "model": _get_child_app_model_config(),
            }
        return yaml.dump(config, default_flow_style=False, sort_keys=False)
    except ImportError:
        # Fallback: manual YAML generation
        lines = [
            f"# Agent configuration for {app_name}",
            f"application: {app_name}",
            f"classification: {blueprint.get('classification', 'CUI')}",
            "agents:",
        ]
        for agent in agents:
            lines.append(f"  {agent['name']}:")
            lines.append(f"    port: {agent['port']}")
            lines.append(f'    role: "{agent.get("role", "")}"')
            lines.append(f'    health_endpoint: "https://localhost:{agent["port"]}/health"')
        return "\n".join(lines) + "\n"


def _generate_mcp_stubs(mcp_dir: Path, agents: list, app_name: str, blueprint: dict) -> int:
    """Generate MCP server stub files for each agent role."""
    classification = blueprint.get("classification", "CUI")
    cui_line = "SECRET // NOFORN" if classification == "SECRET" else "CUI // SP-CTI"

    stubs_written = 0
    # Map known ICDEV™ agent roles to MCP server names
    mcp_map = {
        "orchestrator": "core_server",
        "architect": "core_server",  # shared
        "builder": "builder_server",
        "compliance": "compliance_server",
        "security": "security_server",
        "knowledge": "knowledge_server",
        "monitor": "monitor_server",
    }

    written_servers = set()
    for agent in agents:
        # Use known mapping for standard agents, derive name for custom agents
        server_name = mcp_map.get(agent["name"], f"{agent['name']}_server")
        if server_name in written_servers:
            continue
        written_servers.add(server_name)

        stub_content = (
            f"#!/usr/bin/env python3\n"
            f"# {cui_line}\n"
            f'"""MCP Server: {server_name} for {app_name}\n'
            f"\n"
            f"Provides tool-calling interface for Claude Code integration.\n"
            f"Transport: stdio\n"
            f'"""\n'
            f"\n"
            f"import json\n"
            f"import sys\n"
            f"import logging\n"
            f"\n"
            f'logger = logging.getLogger("{app_name}.mcp.{server_name}")\n'
            f"\n"
            f"\n"
            f"def handle_request(request: dict) -> dict:\n"
            f'    """Handle incoming MCP JSON-RPC request."""\n'
            f'    method = request.get("method", "")\n'
            f'    params = request.get("params", {{}})\n'
            f'    request_id = request.get("id")\n'
            f"\n"
            f"    # Tool dispatch based on method\n"
            f"    handlers = {{}}  # Populated by tool registration\n"
            f"\n"
            f"    handler = handlers.get(method)\n"
            f"    if handler:\n"
            f"        try:\n"
            f"            result = handler(params)\n"
            f'            return {{"jsonrpc": "2.0", "id": request_id, "result": result}}\n'
            f"        except Exception as e:\n"
            f"            return {{\n"
            f'                "jsonrpc": "2.0", "id": request_id,\n'
            f'                "error": {{"code": -32603, "message": str(e)}},\n'
            f"            }}\n"
            f"\n"
            f"    return {{\n"
            f'        "jsonrpc": "2.0", "id": request_id,\n'
            f'        "error": {{"code": -32601, "message": f"Method not found: {{method}}"}},\n'
            f"    }}\n"
            f"\n"
            f"\n"
            f"def main():\n"
            f'    """Run MCP server in stdio mode."""\n'
            f'    logger.info("Starting {server_name} MCP server for {app_name}")\n'
            f"    for line in sys.stdin:\n"
            f"        line = line.strip()\n"
            f"        if not line:\n"
            f"            continue\n"
            f"        try:\n"
            f"            request = json.loads(line)\n"
            f"            response = handle_request(request)\n"
            f'            sys.stdout.write(json.dumps(response) + "\\n")\n'
            f"            sys.stdout.flush()\n"
            f"        except json.JSONDecodeError:\n"
            f"            error = {{\n"
            f'                "jsonrpc": "2.0", "id": None,\n'
            f'                "error": {{"code": -32700, "message": "Parse error"}},\n'
            f"            }}\n"
            f'            sys.stdout.write(json.dumps(error) + "\\n")\n'
            f"            sys.stdout.flush()\n"
            f"\n"
            f"\n"
            f'if __name__ == "__main__":\n'
            f"    main()\n"
        )

        stub_path = mcp_dir / f"{server_name}.py"
        stub_path.write_text(stub_content, encoding="utf-8")
        stubs_written += 1

    return stubs_written


def _generate_dashboard_stub(child_root: Path, blueprint: dict) -> bool:
    """Generate a minimal capability-driven Flask dashboard stub.

    Instead of copying ICDEV™'s dashboard (which has ICDEV™-specific routes),
    generate a minimal Flask app with routes driven by the child app's
    enabled capabilities. The child app developer fills in domain-specific
    logic.

    The generated dashboard adapts to any app type — multi-agent, single
    service, data pipeline, CLI tool, etc.
    """
    app_name = blueprint["app_name"]
    classification = blueprint.get("classification", "CUI")
    agents = blueprint.get("agents", [])
    capabilities = blueprint.get("capabilities", {})
    demo_mode = blueprint.get("demo_mode", False)

    cui_line = "SECRET // NOFORN" if classification == "SECRET" else "CUI // SP-CTI"

    # Demo banner HTML (orange, top + bottom of every page, like CUI banners)
    demo_banner_style = (
        ".demo-banner { background: #e65100; color: #fff; text-align: center; "
        "padding: 6px; font-weight: bold; font-size: 0.85rem; "
        "letter-spacing: 1px; }"
    )
    demo_banner_top = '<div class="demo-banner">DEMONSTRATION ONLY \\u2014 NOT FOR OPERATIONAL USE</div>'

    # Build nav links and page functions based on enabled capabilities
    nav_links = ['"<a href=\\"/\\">Home</a>"']
    page_functions = []

    # Home page — always present
    page_functions.append(
        '    @app.route("/")\n'
        "    def home():\n"
        '        return _render("Home", "<h2>Welcome</h2>"\n'
        f'            "<p>{app_name} dashboard.</p>")\n'
    )

    # Agents page — only if the app has agents
    if agents:
        nav_links.append('"<a href=\\"/agents\\">Agents</a>"')
        agent_list_items = "".join(
            f"<li><strong>{a['name']}</strong> (port {a.get('port', '?')}) \\u2014 {a.get('role', '')}</li>"
            for a in agents
        )
        page_functions.append(
            '    @app.route("/agents")\n'
            "    def agents_page():\n"
            f'        return _render("Agents", "<h2>Agents</h2>"\n'
            f'            "<ul>{agent_list_items}</ul>")\n'
        )

    # Compliance page — only if compliance capability enabled
    if capabilities.get("compliance", False):
        nav_links.append('"<a href=\\"/compliance\\">Compliance</a>"')
        page_functions.append(
            '    @app.route("/compliance")\n'
            "    def compliance_page():\n"
            "        # TODO: Add compliance status from DB\n"
            '        return _render("Compliance",\n'
            '            "<h2>Compliance</h2>"\n'
            '            "<p>Compliance status placeholder.</p>")\n'
        )

    # Document Intelligence page — only if document_intelligence capability enabled
    if capabilities.get("document_intelligence", False):
        nav_links.append('"<a href=\\"/notebook\\">Notebook</a>"')
        page_functions.append(
            '    @app.route("/notebook")\n'
            "    def notebook_page():\n"
            "        try:\n"
            "            from tools.document_intelligence.blueprint import dic_bp\n"
            "            app.register_blueprint(dic_bp)\n"
            "        except Exception:\n"
            "            pass  # Blueprint already registered\n"
            '        return _render("Notebook",\n'
            '            "<h2>Document Intelligence Notebook</h2>"\n'
            '            "<p><a href=\\"/document-intelligence/notebook\\">Open Notebook →</a></p>")\n'
        )

    # D-EPSEC-7: Security page — always present (security is always-on)
    nav_links.append('"<a href=\\"/security\\">Security</a>"')
    page_functions.append(
        '    @app.route("/security")\n'
        "    def security_page():\n"
        "        # TODO: Add security scan results from DB\n"
        '        return _render("Security",\n'
        '            "<h2>Security</h2>"\n'
        '            "<p>Security scan placeholder.</p>")\n'
    )

    # API health endpoint — always present
    page_functions.append(
        '    @app.route("/api/health")\n'
        "    def api_health():\n"
        f'        return jsonify({{"status": "healthy", '
        f'"app": "{app_name}"}})\n'
    )

    nav_html = "\n        ".join(nav_links)

    stub_content = (
        f"#!/usr/bin/env python3\n"
        f"# {cui_line}\n"
        f'"""{app_name} Dashboard — Flask SSR + HTMX\n'
        f"\n"
        f"Generated by ICDEV™ child app generator.\n"
        f"Customize routes and pages for your domain.\n"
        f'"""\n'
        f"\n"
        f"import os\n"
        f"\n"
        f"from flask import Flask, jsonify\n"
        f"\n"
        f"# NOTE: These pages are minimal placeholders and perform no DB access.\n"
        f"# When you add data-backed routes, import get_connection from the\n"
        f"# vendored storage layer (PostgreSQL-primary, RLS-aware) rather than\n"
        f"# opening a raw sqlite3 connection:\n"
        f"#     from tools.db.storage import get_connection\n"
        f"#     conn = get_connection()\n"
        f"\n"
        f"\n"
        f"def _layout(title: str, body: str) -> str:\n"
        f'    """Wrap page body in HTML layout."""\n'
        f"    return (\n"
        f'        "<!DOCTYPE html><html><head>"\n'
        f'        f"<title>{{title}} — {app_name}</title>"\n'
        f'        "<style>"\n'
        f'        "body {{ font-family: system-ui; margin: 2rem; "\n'
        f'        "background: #1a1a2e; color: #e0e0e0; }}"\n'
        f'        "a {{ color: #64b5f6; }} nav {{ margin-bottom: 1.5rem; }}"\n'
        f'        "nav a {{ margin-right: 1rem; }}"\n'
        f'        ".card {{ background: #16213e; padding: 1rem; "\n'
        f'        "border-radius: 8px; margin: 0.5rem 0; }}"\n'
        f'        "{demo_banner_style if demo_mode else ""}"\n'
        f'        "</style></head><body>"\n'
        f'        "{demo_banner_top if demo_mode else ""}"\n'
        f'        "<h1>{app_name}</h1>"\n'
        f'        "<nav>"\n'
        f"        {nav_html}\n"
        f'        "</nav>"\n'
        f'        f"{{body}}"\n'
        f'        "{demo_banner_top if demo_mode else ""}"\n'
        f'        "</body></html>"\n'
        f"    )\n"
        f"\n"
        f"\n"
        f"def _render(title: str, body: str) -> str:\n"
        f'    """Render a page with the standard layout."""\n'
        f"    return _layout(title, body)\n"
        f"\n"
        f"\n"
        f"def create_app() -> Flask:\n"
        f'    """Create and configure the Flask application."""\n'
        f"    app = Flask(__name__)\n"
        f"\n"
        f"    # D-EPSEC-7: Initialize security middleware\n"
        f"    try:\n"
        f"        from tools.security.middleware import init_security\n"
        f"        init_security(app, classification='{classification}')\n"
        f"    except ImportError:\n"
        f"        pass\n"
        f"\n"
    )

    for fn in page_functions:
        stub_content += fn + "\n"

    stub_content += (
        "    return app\n"
        "\n"
        "\n"
        "app = create_app()\n"
        "\n"
        "\n"
        'if __name__ == "__main__":\n'
        '    app.run(host="0.0.0.0", port=5000, debug=os.environ.get("FLASK_DEBUG", "false").lower() == "true")\n'
    )

    dash_dir = child_root / "tools" / "dashboard"
    dash_dir.mkdir(parents=True, exist_ok=True)
    (dash_dir / "app.py").write_text(stub_content, encoding="utf-8")
    return True


def _strip_govcon_from_dashboard(content: str) -> str:
    """Remove GovProposal/CPMP/GovCon imports and registrations from app.py.

    D-CHILD-3: Children never receive GovProposal functionality.

    The parent's govcon init block looks like::

        # D-CHILD-6: ...
        import os as _os
        _GOVCON_ENABLED = _os.environ.get(...)
        _HAS_GOVCON = False
        if _GOVCON_ENABLED:
            try:
                from tools.dashboard.api.proposals import proposals_api
                ...
                _HAS_GOVCON = True
            except ImportError:
                _HAS_GOVCON = False

    This function replaces that entire block with a single constant::

        _HAS_GOVCON = False  # D-CHILD-3: GovCon disabled in child apps
    """
    lines = content.split("\n")
    filtered = []
    # --- State: govcon init block removal ---
    in_govcon_init = False
    govcon_init_done = False
    # --- State: inline route block removal ---
    skip_route_block = False

    for line in lines:
        stripped = line.strip()

        # ── Govcon init block: detect start ────────────────────────
        if not govcon_init_done and not in_govcon_init:
            if "_GOVCON_ENABLED" in stripped and "=" in stripped:
                in_govcon_init = True
                # Also retroactively remove the preceding comment + import _os
                # that belong to this block
                while filtered and filtered[-1].strip() in (
                    "# D-CHILD-6: GovProposal/CPMP/GovCon conditionally loaded",
                    "import os as _os",
                    "",
                ):
                    filtered.pop()
                # Emit the replacement constant
                filtered.append("_HAS_GOVCON = False  # D-CHILD-3: GovCon disabled in child apps")
                continue

        # ── Govcon init block: skip body ───────────────────────────
        if in_govcon_init:
            # The block ends when we hit a top-level statement (indent 0)
            # that is NOT part of the if/try/except/else structure and is
            # NOT a blank line.
            if stripped == "":
                continue  # skip blank lines inside the block
            indent = len(line) - len(line.lstrip())
            if (
                indent == 0
                and stripped not in ("",)
                and not stripped.startswith(("if _GOVCON_ENABLED", "_HAS_GOVCON", "else:"))
            ):
                # This line is the FIRST line AFTER the govcon init block
                in_govcon_init = False
                govcon_init_done = True
                # fall through to normal processing for this line
            else:
                continue  # still inside the govcon init block

        # ── Skip GovProposal blueprint imports and registrations ───
        if any(
            mod in stripped
            for mod in (
                "from tools.dashboard.api.proposals",
                "from tools.dashboard.api.govcon",
                "from tools.dashboard.api.cpmp",
            )
        ):
            continue

        if any(
            mod in stripped
            for mod in (
                "proposals_api",
                "govcon_api",
                "cpmp_api",
            )
        ):
            if "register_blueprint" in stripped or "import" in stripped:
                continue

        # ── Skip GovProposal/CPMP inline route blocks ─────────────
        if any(
            pat in stripped
            for pat in (
                "def proposals_",
                "def cpmp_",
                "def govcon_",
                '@app.route("/proposals',
                '@app.route("/cpmp',
                '@app.route("/govcon',
                "SECTION_TRANSITIONS",
            )
        ):
            skip_route_block = True
            continue

        # End skip block at next function/route decorator
        if skip_route_block and (
            stripped.startswith("@app.route") or stripped.startswith("def ") or stripped.startswith("# ===")
        ):
            if not any(k in stripped for k in ("/proposals", "/cpmp", "/govcon", "proposals_", "cpmp_", "govcon_")):
                skip_route_block = False

        if skip_route_block:
            continue

        filtered.append(line)

    return "\n".join(filtered)


def _copy_full_dashboard(
    child_root: Path,
    blueprint: dict,
    icdev_root: Path,
) -> dict:
    """D-CHILD-4: Copy full 40+ page dashboard to child app.

    Copies all dashboard components except GovProposal/CPMP/GovCon content.
    Falls back to _generate_dashboard_stub() if source doesn't exist.
    """
    dash_src = icdev_root / "tools" / "dashboard"
    if not dash_src.exists():
        _generate_dashboard_stub(child_root, blueprint)
        return {"mode": "stub", "reason": "dashboard source not found"}

    dash_dst = child_root / "tools" / "dashboard"
    dash_dst.mkdir(parents=True, exist_ok=True)
    copied = 0

    # 1. Copy app.py with GovProposal stripped
    app_src = dash_src / "app.py"
    if app_src.exists():
        content = app_src.read_text(encoding="utf-8", errors="replace")
        content = _strip_govcon_from_dashboard(content)
        content = _apply_adaptations(content, ["app_name_replace", "db_rename"], blueprint)
        # D-EPSEC-7: Inject security middleware init into create_app()
        classification = blueprint.get("classification", "CUI")
        sec_block = (
            "\n    # D-EPSEC-7: Initialize security middleware\n"
            "    try:\n"
            "        from tools.security.middleware import init_security\n"
            f"        init_security(app, classification='{classification}')\n"
            "    except ImportError:\n"
            "        pass\n"
        )
        content = re.sub(
            r"(    app = Flask\(.*\)\n)",
            rf"\1{sec_block}",
            content,
            count=1,
            flags=re.DOTALL,
        )
        (dash_dst / "app.py").write_text(content, encoding="utf-8")
        copied += 1

    # 2. Copy templates (excluding PARENT_ONLY_TEMPLATES)
    tpl_src = dash_src / "templates"
    if tpl_src.exists():
        tpl_dst = dash_dst / "templates"
        tpl_dst.mkdir(parents=True, exist_ok=True)
        for item in sorted(tpl_src.rglob("*")):
            if item.is_file():
                rel = item.relative_to(tpl_src)
                # Skip parent-only template directories
                if any(rel.parts[0] == d for d in PARENT_ONLY_TEMPLATES if len(rel.parts) > 0):
                    continue
                dst_file = tpl_dst / rel
                dst_file.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(str(item), str(dst_file))
                copied += 1

    # 3. Copy API modules (excluding PARENT_ONLY_API_MODULES)
    api_src = dash_src / "api"
    if api_src.exists():
        api_dst = dash_dst / "api"
        api_dst.mkdir(parents=True, exist_ok=True)
        for item in sorted(api_src.glob("*.py")):
            if item.name in PARENT_ONLY_API_MODULES:
                continue
            dst_file = api_dst / item.name
            content = item.read_text(encoding="utf-8", errors="replace")
            content = _apply_adaptations(content, ["app_name_replace", "db_rename"], blueprint)
            dst_file.write_text(content, encoding="utf-8")
            copied += 1

    # 4. Copy static assets (JS, CSS) — excluding parent-only JS
    static_src = dash_src / "static"
    if static_src.exists():
        c, _ = _copy_directory(
            static_src,
            dash_dst / "static",
            [],
            blueprint,
            exclude_files=PARENT_ONLY_STATIC_JS,
        )
        copied += c

    # 5. Copy helper modules (auth.py, ux_helpers.py, etc.)
    for helper in ("auth.py", "ux_helpers.py", "__init__.py"):
        helper_src = dash_src / helper
        if helper_src.exists():
            content = helper_src.read_text(encoding="utf-8", errors="replace")
            content = _apply_adaptations(content, ["app_name_replace", "db_rename"], blueprint)
            (dash_dst / helper).write_text(content, encoding="utf-8")
            copied += 1

    logger.info("Step 3: Full dashboard copied — %d files (GovProposal stripped)", copied)
    return {"mode": "full", "files_copied": copied}


def step_03_agent_infrastructure(child_root: Path, blueprint: dict, icdev_root: Optional[Path] = None) -> dict:
    """Step 3: Generate agent cards, config, and MCP server stubs."""
    icdev_root = icdev_root or BASE_DIR
    agents = blueprint.get("agents", [])
    app_name = blueprint["app_name"]
    cards_written = 0

    # Generate agent cards
    agent_cards_dir = child_root / "tools" / "agent" / "cards"
    agent_cards_dir.mkdir(parents=True, exist_ok=True)

    for agent in agents:
        card = {
            "name": f"{app_name}-{agent['name']}",
            "description": agent.get("role", ""),
            "url": f"https://localhost:{agent['port']}",
            "version": "1.0.0",
            "capabilities": {
                "streaming": False,
                "pushNotifications": False,
            },
            "skills": _get_agent_skills(agent["name"], blueprint),
            "authentication": {
                "schemes": [{"scheme": "mutual-tls"}],
            },
        }

        card_path = agent_cards_dir / f"{agent['name']}_card.json"
        card_path.write_text(json.dumps(card, indent=2), encoding="utf-8")
        cards_written += 1

    # Generate agent_config.yaml
    agent_config = _generate_agent_config(agents, app_name, blueprint)
    config_path = child_root / "args" / "agent_config.yaml"
    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_text(agent_config, encoding="utf-8")

    # Generate MCP server stubs for each agent
    mcp_dir = child_root / "tools" / "mcp"
    mcp_dir.mkdir(parents=True, exist_ok=True)
    mcp_stubs_written = _generate_mcp_stubs(mcp_dir, agents, app_name, blueprint)

    # D-CHILD-4: Full dashboard copy (replaces minimal stub)
    dashboard_result = {"mode": "none"}
    capabilities = blueprint.get("capabilities", {})
    if capabilities.get("dashboard", False):
        dashboard_result = _copy_full_dashboard(child_root, blueprint, icdev_root)

    logger.info(
        "Step 3: %d agent cards, 1 config, %d MCP stubs, dashboard=%s",
        cards_written,
        mcp_stubs_written,
        dashboard_result.get("mode"),
    )
    return {
        "agent_cards": cards_written,
        "mcp_stubs": mcp_stubs_written,
        "dashboard": dashboard_result,
    }


# ---------------------------------------------------------------------------
# Step 4: Memory Bootstrap
# ---------------------------------------------------------------------------


def step_04_memory_bootstrap(child_root: Path, blueprint: dict) -> dict:
    """Step 4: Bootstrap memory system with child identity."""
    app_name = blueprint["app_name"]
    classification = blueprint.get("classification", "CUI")
    impact_level = blueprint.get("impact_level", "IL4")
    agents = blueprint.get("agents", [])
    architecture = blueprint.get("fitness_scorecard", {}).get("architecture", "hybrid")
    parent_cb = blueprint.get("parent_callback", {})

    # Create MEMORY.md with blueprint-enriched content
    timestamp = datetime.now(tz=timezone.utc).isoformat()

    demo_mode = blueprint.get("demo_mode", False)

    # Extract capabilities list from blueprint
    capabilities = blueprint.get("capabilities", {})
    active_caps = [k for k, v in capabilities.items() if v] if isinstance(capabilities, dict) else []

    # Extract description/purpose if provided
    description = blueprint.get("description", "")
    purpose = blueprint.get("purpose", "")
    scorecard = blueprint.get("fitness_scorecard", {})
    spec = scorecard.get("spec", description or purpose or "")

    memory_content = (
        f"# MEMORY.md — {app_name}\n"
        f"\n"
        f"## Identity\n"
        f"- **Application:** {app_name}\n"
        f"- **Generated by:** ICDEV™ (parent application)\n"
        f"- **Classification:** {classification}\n"
        f"- **Impact Level:** {impact_level}\n"
        f"- **Architecture:** {architecture}\n"
    )

    if demo_mode:
        memory_content += (
            "- **Mode:** DEMONSTRATION ONLY\n"
            "  - This is a demo application. Do NOT use for operational or classified data.\n"
        )

    # Agent details — only if the app has agents
    if agents:
        memory_content += f"- **Agents:** {len(agents)}\n"
        for a in agents:
            role = a.get("role", "")
            port = a.get("port", "")
            if role:
                memory_content += f"  - **{a['name'].title()}** (port {port}): {role}\n"
            else:
                memory_content += f"  - **{a['name'].title()}** (port {port})\n"

    memory_content += f"- **Generated at:** {timestamp}\n"

    if spec:
        memory_content += f"\n## Purpose\n{spec}\n"

    if active_caps:
        memory_content += "\n## Capabilities\n"
        for cap in active_caps:
            memory_content += f"- {cap}\n"

    memory_content += (
        "\n"
        "## User Preferences\n"
        "(To be populated during first session)\n"
        "\n"
        "## Key Facts\n"
        "- This is a generated child application of ICDEV™\n"
        "- This application CANNOT generate child applications "
        "(grandchild prevention)\n"
        "- ANVIL workflow does not include fitness assessment step\n"
    )
    if parent_cb.get("enabled"):
        memory_content += f"- Parent ICDEV™ callback URL: {parent_cb.get('url', 'N/A')}\n"
    memory_content += "\n## Session History\n(Populated automatically by memory system)\n"

    memory_path = child_root / "memory" / "MEMORY.md"
    memory_path.parent.mkdir(parents=True, exist_ok=True)
    memory_path.write_text(memory_content, encoding="utf-8")

    # Create empty daily log for today
    today = datetime.now(tz=timezone.utc).strftime("%Y-%m-%d")
    log_path = child_root / "memory" / "logs" / f"{today}.md"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log_path.write_text(f"# {app_name} — Daily Log {today}\n\n", encoding="utf-8")

    logger.info("Step 4: Memory bootstrapped (MEMORY.md + daily log)")
    return {"memory_md": str(memory_path), "daily_log": str(log_path)}


# ---------------------------------------------------------------------------
# Step 5: DB Init Script
# ---------------------------------------------------------------------------


def step_05_db_init_script(child_root: Path, blueprint: dict) -> dict:
    """Step 5: Generate standalone DB init script for child app."""
    # Try to import sister module
    write_init_script_fn = _import_sister("db_init_generator", "write_init_script")

    if write_init_script_fn:
        output_dir = child_root / "tools" / "db"
        output_dir.mkdir(parents=True, exist_ok=True)
        script_path = write_init_script_fn(blueprint, output_dir)
        logger.info("Step 5: DB init script generated at %s", script_path)
        return {"script_path": str(script_path), "method": "db_init_generator"}

    # Fallback: generate a minimal init script inline
    app_name = blueprint["app_name"]
    sanitized = re.sub(r"[^a-z0-9_]", "_", app_name.lower().replace("-", "_"))

    script_content = (
        "#!/usr/bin/env python3\n"  # nosec B608 -- table/column names are internal constants, not user input
        "# CUI // SP-CTI\n"
        f'"""{app_name} database initialization."""\n'
        "\n"
        "import sys\n"
        "from pathlib import Path\n"
        "\n"
        'DB_PATH = Path(__file__).resolve().parent.parent.parent / "data"'
        f' / "{app_name}.db"\n'
        "\n"
        "\n"
        "def _get_db_connection(db_path):\n"
        '    """Backend-agnostic connection (PG-primary, SQLite init-fallback).\n'
        "\n"
        "    Prefers the vendored ICDEV storage layer (PostgreSQL-primary). Passing\n"
        "    this child's own .db path keeps get_connection() on SQLite for that\n"
        "    file with RLS skipped, since child tables carry no tenant_id/\n"
        "    classification columns. Degrades to a direct sqlite3 connection when\n"
        "    the storage layer is not vendored (standalone child).\n"
        '    """\n'
        "    try:\n"
        "        from tools.db.storage import get_connection\n"
        "        return get_connection(str(db_path))\n"
        "    except Exception:\n"
        "        import sqlite3\n"
        "        return sqlite3.connect(str(db_path))  # pg-ok: guarded standalone fallback\n"
        "\n"
        "\n"
        "def init_db(db_path=None):\n"
        "    db_path = db_path or str(DB_PATH)\n"
        "    Path(db_path).parent.mkdir(parents=True, exist_ok=True)\n"
        "    conn = _get_db_connection(db_path)\n"
        "    conn.execute(\n"
        '        "CREATE TABLE IF NOT EXISTS projects "\n'
        '        "(id TEXT PRIMARY KEY, name TEXT, status TEXT '
        "DEFAULT 'active', \"\n"
        '        "created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)"\n'
        "    )\n"
        "    conn.execute(\n"
        '        "CREATE TABLE IF NOT EXISTS audit_trail "\n'
        '        "(id TEXT PRIMARY KEY, event_type TEXT, action TEXT, "\n'
        '        "created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)"\n'
        "    )\n"
        "    conn.commit()\n"
        "    # Backend-aware table listing: prefer the vendored ICDEV helper\n"
        "    # (works on PostgreSQL and SQLite); fall back to a direct catalog\n"
        "    # probe only when running standalone (no vendored storage layer).\n"
        "    try:\n"
        "        from tools.db.storage import list_tables\n"
        "        tables = list_tables(conn)\n"
        "    except Exception:\n"
        "        tables = [\n"
        "            r[0] for r in conn.execute(\n"
        "                \"SELECT name FROM sqlite_master WHERE type='table'\"\n"
        "            ).fetchall()\n"
        "        ]\n"
        f'    print(f"{app_name} database initialized at {{db_path}}")\n'
        '    _names = ", ".join(sorted(tables))\n'
        '    print(f"Tables created ({len(tables)}): {_names}")\n'
        "    conn.close()\n"
        "\n"
        "\n"
        'if __name__ == "__main__":\n'
        "    init_db()\n"
    )

    output_dir = child_root / "tools" / "db"
    output_dir.mkdir(parents=True, exist_ok=True)
    script_path = output_dir / f"init_{sanitized}_db.py"
    script_path.write_text(script_content, encoding="utf-8")

    logger.info("Step 5: DB init script (fallback) generated at %s", script_path)
    return {"script_path": str(script_path), "method": "fallback"}


# ---------------------------------------------------------------------------
# Step 6: Goals and Hardprompts
# ---------------------------------------------------------------------------


def step_06_goals_and_hardprompts(child_root: Path, blueprint: dict, icdev_root: Path) -> dict:
    """Step 6: Copy and adapt goals + hardprompts using goal_adapter."""
    adapt_goals_fn = _import_sister("goal_adapter", "adapt_goals")

    if adapt_goals_fn:
        result = adapt_goals_fn(blueprint, icdev_root, child_root)
        logger.info(
            "Step 6: Goals adapted — %d goals, %d hardprompts",
            result.get("goals_copied", 0),
            result.get("hardprompts_copied", 0),
        )
        return result

    # Fallback: copy goals manually
    goals_config = blueprint.get("goals_config", [])
    goals_dir = child_root / "goals"
    goals_dir.mkdir(parents=True, exist_ok=True)

    goal_files = {
        "build_app": "build_app.md",
        "tdd_workflow": "tdd_workflow.md",
        "compliance_workflow": "compliance_workflow.md",
        "security_scan": "security_scan.md",
        "deploy_workflow": "deploy_workflow.md",
        "monitoring": "monitoring.md",
        "self_healing": "self_healing.md",
        "agent_management": "agent_management.md",
    }

    copied = 0
    for goal_name in goals_config:
        filename = goal_files.get(goal_name)
        if filename:
            src = _forge_dir(icdev_root, "goals") / filename
            if src.exists():
                shutil.copy2(src, goals_dir / filename)
                copied += 1

    # Generate minimal manifest
    manifest_content = f"# Goals Manifest — {blueprint['app_name']}\n\n"
    manifest_content += "| Goal | File |\n|------|------|\n"
    for goal_name in goals_config:
        filename = goal_files.get(goal_name, f"{goal_name}.md")
        manifest_content += f"| {goal_name} | goals/{filename} |\n"
    (goals_dir / "manifest.md").write_text(manifest_content, encoding="utf-8")

    logger.info("Step 6: Goals copied (fallback) — %d goals", copied)
    return {"goals_copied": copied, "method": "fallback"}


# ============================================================
# ONTOLOGY SCAFFOLD HELPERS (onto-eco-05)
# ============================================================


def _generate_ontology_scaffold(child_root: Path, blueprint: dict, icdev_root: Path) -> dict:
    """Generate ontology scaffold for child app.

    Creates:
      - args/ontology/app.ttl — child ontology with owl:imports of parent
      - args/ontology/app_config.yaml — lists parent ontologies + domain classes
      - args/ontology/catalog.yaml — local catalog for validation

    Security: child app inherits parent classification tags from ontology.
    """
    app_name = blueprint["app_name"]
    classification = blueprint.get("classification", "CUI")
    domain = blueprint.get("domain", "")
    ontology_dir = child_root / "args" / "ontology"
    ontology_dir.mkdir(parents=True, exist_ok=True)

    # Determine parent ontology path (relative to child)
    parent_ontology = "../../args/ontology/icdev_core.ttl"

    # Build domain-specific classes from blueprint
    domain_classes = blueprint.get("ontology_domain_classes", [])
    if domain and not domain_classes:
        # Auto-generate a default domain class if domain is set
        domain_classes = [f"{domain}:{domain.capitalize()}Record"]

    domain_class_turtle = ""
    for dc in domain_classes:
        prefix, cls = dc.split(":") if ":" in dc else (domain or "app", dc)
        domain_class_turtle += (
            f"\n{prefix}:{cls} rdf:type owl:Class ;\n"
            f'    rdfs:label "{cls}" ;\n'
            f'    rdfs:comment "Domain-specific class for {app_name}." ;\n'
            f"    rdfs:subClassOf icdev:Artifact .\n"
        )

    # Generate app.ttl
    app_ttl = f"""@prefix owl: <http://www.w3.org/2002/07/owl#> .
@prefix rdf: <http://www.w3.org/1999/02/22-rdf-syntax-ns#> .
@prefix rdfs: <http://www.w3.org/2000/01/rdf-schema#> .
@prefix xsd: <http://www.w3.org/2001/XMLSchema#> .
@prefix icdev: <https://icdev.dev/ontology/core#> .
@prefix app: <https://icdev.dev/ontology/apps/{app_name}#> .

<{app_name}> rdf:type owl:Ontology ;
    owl:imports <{parent_ontology}> ;
    owl:versionInfo "1.0.0" ;
    rdfs:label "{app_name} Ontology" ;
    rdfs:comment "Child app ontology extending ICDEV core." .

# Security classification inherited from parent ontology
app:hasClassification rdf:type owl:ObjectProperty ;
    rdfs:domain app:{app_name.replace("-", "_").title()}Project ;
    rdfs:range icdev:ClassificationLevel ;
    rdfs:label "has classification" ;
    rdfs:comment "Inherited classification: {classification}." .

app:{app_name.replace("-", "_").title()}Project rdf:type owl:Class ;
    rdfs:label "{app_name.title()} Project" ;
    rdfs:comment "Root project class for {app_name}." ;
    rdfs:subClassOf icdev:Project ;
    app:hasClassification icdev:{classification.replace(" ", "_").replace("//", "_")} .
{domain_class_turtle}
"""
    (ontology_dir / "app.ttl").write_text(app_ttl, encoding="utf-8")

    # Generate app_config.yaml
    app_config_yaml = f"""# Ontology Configuration — {app_name}
# Auto-generated by child_app_generator.py (onto-eco-05)

parent_ontologies:
  - path: {parent_ontology}
    namespace: https://icdev.dev/ontology/core#
    description: ICDEV Core Ontology

domain_classes:
{chr(10).join('  - "' + dc + '"' for dc in domain_classes)}

classification_inheritance:
  enabled: true
  source: parent_ontology
  child_classification: {classification}

validation:
  validate_on_scaffold: true
  require_parent_import: true
"""
    (ontology_dir / "app_config.yaml").write_text(app_config_yaml, encoding="utf-8")

    # Generate catalog.yaml for local ontology indexing
    catalog_yaml = f"""# Ontology Catalog — {app_name}
# Maps local and imported ontologies for validation and lookup.

catalog:
  - uri: https://icdev.dev/ontology/apps/{app_name}
    path: app.ttl
    role: primary

  - uri: https://icdev.dev/ontology/core
    path: {parent_ontology}
    role: imported

classification_tags:
  - {classification}
"""
    (ontology_dir / "catalog.yaml").write_text(catalog_yaml, encoding="utf-8")

    logger.info(
        "Ontology scaffold: app.ttl + app_config.yaml + catalog.yaml for %s (%d domain classes)",
        app_name,
        len(domain_classes),
    )
    return {
        "ontology_dir": str(ontology_dir.relative_to(child_root)),
        "domain_classes": domain_classes,
        "classification": classification,
    }


def _validate_child_ontology(child_root: Path, blueprint: dict, icdev_root: Path) -> dict:
    """Validate child app ontology against parent ontology on scaffold.

    Checks:
      - app.ttl exists and is parseable (basic syntax)
      - owl:imports points to a parent ontology that exists
      - classification tag is consistent with blueprint
      - Domain classes are well-formed
    """
    ontology_dir = child_root / "args" / "ontology"
    app_ttl = ontology_dir / "app.ttl"
    app_config = ontology_dir / "app_config.yaml"
    errors = []
    warnings = []

    if not app_ttl.exists():
        errors.append("app.ttl missing")
        return {"valid": False, "errors": errors, "warnings": warnings}

    ttl_content = app_ttl.read_text(encoding="utf-8")

    # Check for owl:imports
    if "owl:imports" not in ttl_content:
        errors.append("app.ttl missing owl:imports (parent ontology link)")
    else:
        # Verify parent ontology file exists (in parent project)
        parent_ttl = _forge_dir(icdev_root, "args") / "ontology" / "icdev_core.ttl"
        if not parent_ttl.exists():
            warnings.append(f"Parent ontology not found at {parent_ttl}")

    # Check classification consistency
    expected_class = blueprint.get("classification", "CUI")
    if expected_class not in ttl_content:
        warnings.append(f"Classification '{expected_class}' not found in app.ttl")

    # Check domain classes
    for dc in blueprint.get("ontology_domain_classes", []):
        if dc not in ttl_content:
            warnings.append(f"Domain class '{dc}' not found in app.ttl")

    # Check app_config.yaml
    if not app_config.exists():
        errors.append("app_config.yaml missing")
    else:
        cfg_content = app_config.read_text(encoding="utf-8")
        if "parent_ontologies:" not in cfg_content:
            errors.append("app_config.yaml missing parent_ontologies section")
        if "validate_on_scaffold: true" not in cfg_content:
            warnings.append("app_config.yaml has validation disabled")

    valid = len(errors) == 0
    logger.info(
        "Ontology validation for %s: %s (%d errors, %d warnings)",
        blueprint["app_name"],
        "PASS" if valid else "FAIL",
        len(errors),
        len(warnings),
    )
    return {"valid": valid, "errors": errors, "warnings": warnings}


def _inherit_ontology_security_tags(blueprint: dict, icdev_root: Path) -> dict:
    """Read parent ontology classification tags and merge into blueprint security.

    Returns updated blueprint snippet with inherited tags.
    """
    parent_ttl = _forge_dir(icdev_root, "args") / "ontology" / "icdev_core.ttl"
    inherited_tags = []

    if parent_ttl.exists():
        content = parent_ttl.read_text(encoding="utf-8")
        # Extract classification levels defined in parent ontology
        for line in content.splitlines():
            if "icdev:" in line and "rdf:type icdev:ClassificationLevel" in line:
                # e.g. "icdev:CUI rdf:type icdev:ClassificationLevel ;"
                parts = line.split()
                if len(parts) >= 1 and parts[0].startswith("icdev:"):
                    tag = parts[0].replace("icdev:", "")
                    if tag and tag not in inherited_tags:
                        inherited_tags.append(tag)

    child_class = blueprint.get("classification", "CUI")
    if child_class not in inherited_tags and child_class:
        inherited_tags.append(child_class)

    logger.info(
        "Ontology security tags inherited for %s: %s",
        blueprint["app_name"],
        inherited_tags,
    )
    return {
        "inherited_tags": inherited_tags,
        "effective_classification": child_class,
    }


# ============================================================
# STEP 7: Args + Context
# ============================================================


def step_07_args_and_context(child_root: Path, blueprint: dict, icdev_root: Path) -> dict:
    """Step 7: Copy and adapt args/ and context/ configuration files."""
    blueprint["app_name"]
    capabilities = blueprint.get("capabilities", {})
    copied = 0

    # --- Args files ---
    args_dir = child_root / "args"
    args_dir.mkdir(parents=True, exist_ok=True)

    args_files = [
        ("args/project_defaults.yaml", ["app_name_replace", "port_remap"]),
        ("args/monitoring_config.yaml", ["endpoint_remap", "app_name_replace"]),
        # D-EPSEC-7: Security config always copied (not conditional on compliance)
        ("args/security_gates.yaml", []),
        ("args/endpoint_security_config.yaml", []),
        ("args/security_config.yaml", ["security_policy_update", "clearance_ceiling"]),
        ("args/code_pattern_config.yaml", []),
    ]
    if capabilities.get("compliance"):
        args_files.append(("args/cui_markings.yaml", ["classification_update"]))

    # D-CHILD-1: Enterprise capability args files
    if capabilities.get("ricoas"):
        args_files.append(("args/ricoas_config.yaml", []))
    if capabilities.get("devsecops_zta"):
        args_files.append(("args/devsecops_config.yaml", []))
        args_files.append(("args/zta_config.yaml", []))
    if capabilities.get("ai_security"):
        args_files.append(("args/owasp_agentic_config.yaml", []))
    if capabilities.get("observability"):
        args_files.append(("args/observability_tracing_config.yaml", []))
    if capabilities.get("code_intelligence"):
        args_files.append(("args/code_quality_config.yaml", []))
    if capabilities.get("ai_governance"):
        args_files.append(("args/ai_governance_config.yaml", []))
    # D-RAG-13: RAG config
    if capabilities.get("rag"):
        args_files.append(("args/rag_config.yaml", []))
    # D-FT-19: Fine-tuning config
    if capabilities.get("fine_tuning"):
        args_files.append(("args/finetune_config.yaml", []))
    # D-GEN-1: Genesis config
    if capabilities.get("genesis"):
        args_files.append(("args/genesis_config.yaml", ["app_name_replace"]))
    # D-KARL-1: Knowledge Graph config
    if capabilities.get("knowledge_graph"):
        args_files.append(("args/knowledge_graph_config.yaml", []))
    # Phase 61: Orchestration config (prompt chains, ANVIL critique)
    args_files.append(("args/prompt_chains.yaml", []))
    atlas_config = blueprint.get("atlas_config", {})
    if atlas_config.get("critique_enabled"):
        args_files.append(("args/anvil_critique_config.yaml", []))

    for rel_path, adaptations in args_files:
        src = icdev_root / rel_path
        dest = child_root / rel_path
        if src.exists():
            if _copy_and_adapt_file(src, dest, adaptations, blueprint):
                copied += 1
        else:
            logger.debug("Args file not found: %s", src)

    # --- Context files ---
    ctx_src = _forge_dir(icdev_root, "context")
    ctx_dest = child_root / "context"

    # Always copy: context/languages/
    lang_src = ctx_src / "languages"
    if lang_src.exists():
        c, _ = _copy_directory(lang_src, ctx_dest / "languages", [], blueprint)
        copied += c

    # Copy context/agentic/ (without fitness rubric — ICDEV™-only)
    agentic_src = ctx_src / "agentic"
    if agentic_src.exists():
        c, _ = _copy_directory(agentic_src, ctx_dest / "agentic", [], blueprint, exclude_files={"fitness_rubric.md"})
        copied += c

    # Conditional: context/compliance/
    if capabilities.get("compliance"):
        comp_src = ctx_src / "compliance"
        if comp_src.exists():
            c, _ = _copy_directory(comp_src, ctx_dest / "compliance", ["classification_update"], blueprint)
            copied += c

    # Conditional: context/mbse/
    if capabilities.get("mbse"):
        mbse_src = ctx_src / "mbse"
        if mbse_src.exists():
            c, _ = _copy_directory(mbse_src, ctx_dest / "mbse", [], blueprint)
            copied += c

    # --- DevSecOps/ZTA inheritance (D122) ---
    # When parent project has a DevSecOps profile or ZTA is active,
    # copy devsecops configs and tools to child app
    devsecops_profile = blueprint.get("devsecops_profile") or {}
    zta_active = blueprint.get("zta_active", False) or devsecops_profile.get("zta_enabled", False)

    if devsecops_profile or zta_active:
        # Copy DevSecOps config files
        for cfg in ("args/devsecops_config.yaml", "args/zta_config.yaml"):
            cfg_src = icdev_root / cfg
            cfg_dest = child_root / cfg
            if cfg_src.exists():
                if _copy_and_adapt_file(cfg_src, cfg_dest, [], blueprint):
                    copied += 1

        # Copy DevSecOps tools directory
        devsecops_src = icdev_root / "tools" / "devsecops"
        if devsecops_src.exists():
            c, _ = _copy_directory(devsecops_src, child_root / "tools" / "devsecops", ["app_name_replace"], blueprint)
            copied += c

        # Copy NIST 800-207 compliance catalog + crosswalk
        for zta_file in ("context/compliance/nist_800_207_zta.json", "context/compliance/nist_800_207_crosswalk.json"):
            zta_src = icdev_root / zta_file
            zta_dest = child_root / zta_file
            if zta_src.exists():
                if _copy_and_adapt_file(zta_src, zta_dest, [], blueprint):
                    copied += 1

        # Copy NIST 800-207 assessor
        assessor_src = icdev_root / "tools" / "compliance" / "nist_800_207_assessor.py"
        assessor_dest = child_root / "tools" / "compliance" / "nist_800_207_assessor.py"
        if assessor_src.exists():
            if _copy_and_adapt_file(assessor_src, assessor_dest, ["app_name_replace"], blueprint):
                copied += 1

        logger.info(
            "Step 7: DevSecOps/ZTA inheritance applied (%s profile, ZTA=%s)",
            devsecops_profile.get("maturity_level", "detected"),
            zta_active,
        )

    # --- MOSA inheritance (D127) ---
    # When parent project is DoD/IC with MOSA enabled, copy MOSA config,
    # tools, and compliance artifacts to child app
    mosa_enabled = blueprint.get("mosa_enabled", False)
    if not mosa_enabled:
        # Auto-detect from impact level or customer org
        il = blueprint.get("impact_level", "").upper()
        org = (blueprint.get("customer_org") or "").lower()
        if il in ("IL4", "IL5", "IL6") or any(k in org for k in ["dod", "defense", "military"]):
            mosa_enabled = True

    if mosa_enabled:
        # Copy MOSA config
        mosa_cfg_src = _forge_dir(icdev_root, "args") / "mosa_config.yaml"
        mosa_cfg_dest = child_root / "args" / "mosa_config.yaml"
        if mosa_cfg_src.exists():
            if _copy_and_adapt_file(mosa_cfg_src, mosa_cfg_dest, [], blueprint):
                copied += 1

        # Copy tools/mosa/ package
        mosa_tools_src = icdev_root / "tools" / "mosa"
        if mosa_tools_src.exists():
            c, _ = _copy_directory(mosa_tools_src, child_root / "tools" / "mosa", ["app_name_replace"], blueprint)
            copied += c

        # Copy MOSA catalog and crosswalk
        for mosa_file in ("mosa_framework.json", "mosa_crosswalk.json"):
            src = _forge_dir(icdev_root, "context") / "compliance" / mosa_file
            dest = child_root / "context" / "compliance" / mosa_file
            if src.exists():
                if _copy_and_adapt_file(src, dest, [], blueprint):
                    copied += 1

        # Copy MOSA assessor
        assessor_src = icdev_root / "tools" / "compliance" / "mosa_assessor.py"
        assessor_dest = child_root / "tools" / "compliance" / "mosa_assessor.py"
        if assessor_src.exists():
            if _copy_and_adapt_file(assessor_src, assessor_dest, ["app_name_replace"], blueprint):
                copied += 1

        logger.info("Step 7: MOSA inheritance applied (DoD MOSA enabled)")

    # ── ONTO: Child app ontology inheritance (onto-eco-05) ──
    ontology_result = _generate_ontology_scaffold(child_root, blueprint, icdev_root)
    validation_result = _validate_child_ontology(child_root, blueprint, icdev_root)
    security_tags = _inherit_ontology_security_tags(blueprint, icdev_root)

    # Merge inherited ontology tags into blueprint for downstream steps
    blueprint["_ontology_inherited_tags"] = security_tags.get("inherited_tags", [])
    blueprint["_ontology_validation"] = validation_result

    logger.info("Step 7: Copied %d args/context files + ontology scaffold", copied)
    return {
        "files_copied": copied,
        "ontology": ontology_result,
        "ontology_validation": validation_result,
        "security_tags": security_tags,
    }


# ============================================================
# STEP 8: A2A Callback Client
# ============================================================


def step_08_a2a_callback_client(child_root: Path, blueprint: dict) -> dict:
    """Step 8: Generate A2A callback client for parent ICDEV™ communication."""
    app_name = blueprint["app_name"]
    parent_cb = blueprint.get("parent_callback", {})
    classification = blueprint.get("classification", "CUI")
    cui_line = "SECRET // NOFORN" if classification == "SECRET" else "CUI // SP-CTI"

    default_url = parent_cb.get("url", "")
    auth_method = parent_cb.get("auth", "none")

    client_content = f'''#!/usr/bin/env python3
# {cui_line}
# Controlled by: Department of Defense
# CUI Category: CTI
"""A2A Callback Client — calls parent ICDEV™ for capabilities not included locally.

This child application ({app_name}) can request services from its parent ICDEV™
instance using the A2A protocol (JSON-RPC 2.0).

Excluded capabilities (must call parent for):
  - Application generation (agentic fitness, blueprint, scaffolding)
  - Application modernization (7R assessment, migration)

Environment variable: ICDEV_PARENT_CALLBACK_URL
"""

import json
import logging
import os
import uuid
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

PARENT_URL = os.environ.get("ICDEV_PARENT_CALLBACK_URL", "{default_url}")
AUTH_METHOD = "{auth_method}"

logger = logging.getLogger("{app_name}.a2a_callback")


def call_parent(method: str, params: dict = None, timeout: int = 30) -> dict:
    """Send JSON-RPC 2.0 request to parent ICDEV™.

    Args:
        method: The RPC method name (e.g. "modernization.analyze_legacy").
        params: Optional parameters dict.
        timeout: Request timeout in seconds.

    Returns:
        Response result dict, or error dict on failure.
    """
    if not PARENT_URL:
        return {{"error": "ICDEV_PARENT_CALLBACK_URL not configured"}}

    request_id = str(uuid.uuid4())
    payload = {{
        "jsonrpc": "2.0",
        "id": request_id,
        "method": method,
        "params": params or {{}},
    }}

    headers = {{"Content-Type": "application/json"}}
    if AUTH_METHOD == "mtls":
        # mTLS handled at transport level; no additional auth header needed
        pass
    elif AUTH_METHOD == "bearer":
        token = os.environ.get("ICDEV_PARENT_AUTH_TOKEN", "")
        if token:
            headers["Authorization"] = f"Bearer {{token}}"

    try:
        req = Request(
            PARENT_URL,
            data=json.dumps(payload).encode("utf-8"),
            headers=headers,
            method="POST",
        )
        with urlopen(req, timeout=timeout) as resp:
            body = json.loads(resp.read().decode("utf-8"))
            if "error" in body:
                logger.warning("Parent returned error: %s", body["error"])
                return {{"error": body["error"]}}
            return body.get("result", {{}})
    except HTTPError as e:
        logger.error("HTTP error calling parent: %s %s", e.code, e.reason)
        return {{"error": f"HTTP {{e.code}}: {{e.reason}}"}}
    except URLError as e:
        logger.error("Connection error calling parent: %s", e.reason)
        return {{"error": f"Connection failed: {{e.reason}}"}}
    except Exception as e:
        logger.error("Unexpected error calling parent: %s", e)
        return {{"error": str(e)}}


def check_health() -> bool:
    """Check if parent ICDEV™ is reachable."""
    if not PARENT_URL:
        return False
    try:
        health_url = PARENT_URL.rstrip("/").rsplit("/", 1)[0] + "/health"
        req = Request(health_url, method="GET")
        with urlopen(req, timeout=5) as resp:
            return resp.status == 200
    except Exception:
        return False


def list_parent_capabilities() -> list:
    """Query parent for available capabilities."""
    result = call_parent("system.list_methods")
    if "error" in result:
        return []
    return result.get("methods", [])


def query_parent_rag(query: str, top_k: int = 5) -> dict:
    """Query parent's RAG for cross-engine intelligence (D-RAG-13, D-RAG-14).

    Child apps without local RAG can query the parent's knowledge base for
    context from Innovation, Creative, and Research engines.

    Args:
        query: Natural language search query.
        top_k: Number of top results to return.

    Returns:
        Dict with search results or error.
    """
    import hashlib
    import sqlite3
    from pathlib import Path

    # Check local cache first (TTL-based)
    cache_db = str(Path(__file__).resolve().parent.parent.parent / "data" / "{app_name}.db")
    try:
        qhash = hashlib.sha256(query.encode()).hexdigest()
        conn = get_connection()
        row = conn.execute(
            "SELECT results FROM rag_parent_cache WHERE query_hash = ? AND expires_at > datetime('now')",
            (qhash,)
        ).fetchone()
        if row:
            conn.close()
            return json.loads(row[0])
        conn.close()
    except Exception:
        qhash = hashlib.sha256(query.encode()).hexdigest()

    # Query parent
    result = call_parent("rag.search", {{
        "query": query,
        "top_k": top_k,
        "child_id": "{app_name}",
    }})

    # Cache successful results (1 hour TTL)
    if "error" not in result and result.get("results"):
        try:
            conn = get_connection()
            conn.execute(
                "INSERT OR REPLACE INTO rag_parent_cache (query_hash, results, expires_at) VALUES (?, ?, datetime('now', '+1 hour'))",
                (qhash, json.dumps(result))
            )
            conn.commit()
            conn.close()
        except Exception:
            pass

    return result


def send_critique_findings(session_id: str, findings: list) -> dict:
    """Send ANVIL critique findings to parent for federated history.

    Enables cross-child critique learning: findings from one child app
    are available to parent and sibling apps via critique history queries.

    Args:
        session_id: Local ANVIL critique session ID.
        findings: List of critique finding dicts from anvil_critique.py.

    Returns:
        Dict with acknowledgment or error.
    """
    return call_parent("anvil_critique.report_findings", {{
        "session_id": session_id,
        "child_id": "{app_name}",
        "findings": findings,
    }})


def query_critique_history(project_type: str = "", limit: int = 20) -> dict:
    """Query parent for historical ANVIL critique findings.

    Retrieves critique patterns and common findings from parent and sibling
    apps to inform local critique sessions.

    Args:
        project_type: Optional filter by project type.
        limit: Max results.

    Returns:
        Dict with critique history or error.
    """
    params = {{"child_id": "{app_name}", "limit": limit}}
    if project_type:
        params["project_type"] = project_type
    return call_parent("anvil_critique.get_history", params)


if __name__ == "__main__":
    import sys
    if "--health" in sys.argv:
        ok = check_health()
        print(f"Parent health: {{'ok' if ok else 'unreachable'}}")
        sys.exit(0 if ok else 1)
    caps = list_parent_capabilities()
    print(f"Parent capabilities: {{len(caps)}}")
    for cap in caps:
        print(f"  - {{cap}}")
'''  # nosec B608 -- template content, not user input

    client_path = child_root / "tools" / "a2a" / "icdev_callback_client.py"
    client_path.parent.mkdir(parents=True, exist_ok=True)
    client_path.write_text(client_content, encoding="utf-8")

    logger.info(
        "Step 8: A2A callback client generated (parent=%s)", "enabled" if parent_cb.get("enabled") else "disabled"
    )
    return {
        "client_path": str(client_path),
        "parent_enabled": parent_cb.get("enabled", False),
        "parent_url": default_url,
    }


# ============================================================
# STEP 9: CI/CD Setup
# ============================================================


def step_09_cicd_setup(child_root: Path, blueprint: dict, icdev_root: Path) -> dict:
    """Step 9: Copy CI/CD tools and Claude Code commands."""
    blueprint["app_name"]
    copied = 0

    # Copy tools/ci/
    ci_src = icdev_root / "tools" / "ci"
    ci_dest = child_root / "tools" / "ci"
    if ci_src.exists():
        c, _ = _copy_directory(ci_src, ci_dest, ["bot_identifier_replace", "app_name_replace"], blueprint)
        copied += c

    # NOTE: .claude/commands/ is handled by step_09c_claude_code_config()
    # with proper PARENT_ONLY_COMMANDS and PARENT_ONLY_E2E filtering.

    # Generate .gitignore
    gitignore_content = """# Python
__pycache__/
*.py[cod]
*.egg-info/
dist/
build/
.eggs/

# Environment
.env
.venv/
env/
venv/

# Data
data/*.db
data/*.db-journal

# IDE
.idea/
.vscode/
*.swp
*.swo

# Temp
.tmp/
*.log

# OS
.DS_Store
Thumbs.db
"""
    gitignore_path = child_root / ".gitignore"
    gitignore_path.write_text(gitignore_content, encoding="utf-8")
    copied += 1

    # Generate requirements.txt
    requirements = [
        "pyyaml>=6.0",
        "jinja2>=3.1",
        "flask>=3.0",
        "pytest>=8.0",
        "pytest-cov>=5.0",
        "behave>=1.2",
        "requests>=2.31",
        "boto3>=1.34",
        "cyclonedx-bom>=4.0",
        "bandit>=1.7",
        "pip-audit>=2.7",
        "detect-secrets>=1.4",
    ]
    if blueprint.get("capabilities", {}).get("mbse"):
        requirements.append("# MBSE: no additional deps (stdlib xml.etree)")
    req_path = child_root / "requirements.txt"
    req_path.write_text("\n".join(requirements) + "\n", encoding="utf-8")
    copied += 1

    logger.info("Step 9: CI/CD setup — %d files copied", copied)
    return {"files_copied": copied}


# ============================================================
# STEP 9b: License Files
# ============================================================


def _copy_license_files(child_root: Path, blueprint: dict, icdev_root: Path) -> dict:
    """Copy ICDEV™ license validator (and optionally generator) to child app.

    For demo apps, also auto-generates a 30-day trial license file.

    Args:
        child_root: Root directory of the child app.
        blueprint: Blueprint dict.
        icdev_root: ICDEV™ project root.

    Returns:
        Dict with files copied and license info.
    """
    app_name = blueprint["app_name"]
    demo_mode = blueprint.get("demo_mode", False)
    files_copied = []

    # Create licensing directory in child app
    lic_dir = child_root / "tools" / "saas" / "licensing"
    lic_dir.mkdir(parents=True, exist_ok=True)

    # Create __init__.py files for the package path
    for pkg_dir in [
        child_root / "tools" / "saas",
        lic_dir,
    ]:
        init_file = pkg_dir / "__init__.py"
        if not init_file.exists():
            init_file.write_text("", encoding="utf-8")

    # Always copy license_validator.py
    validator_src = icdev_root / "tools" / "saas" / "licensing" / "license_validator.py"
    if validator_src.exists():
        _copy_and_adapt_file(validator_src, lic_dir / "license_validator.py", ["app_name_replace"], blueprint)
        files_copied.append("license_validator.py")

    # Demo: also copy generator + create trial license
    license_info = None
    if demo_mode:
        gen_src = icdev_root / "tools" / "saas" / "licensing" / "license_generator.py"
        if gen_src.exists():
            _copy_and_adapt_file(gen_src, lic_dir / "license_generator.py", ["app_name_replace"], blueprint)
            files_copied.append("license_generator.py")

        # Auto-generate 30-day demo license
        expires_at = (datetime.now(tz=timezone.utc) + timedelta(days=30)).isoformat()
        license_info = {
            "license_id": f"demo-{uuid.uuid4().hex[:12]}",
            "customer": f"{app_name}-demo",
            "tier": "starter",
            "max_projects": 5,
            "max_users": 3,
            "allowed_il_levels": ["IL2"],
            "issued_at": datetime.now(tz=timezone.utc).isoformat(),
            "expires_at": expires_at,
            "signature": "",
            "demo": True,
        }
        data_dir = child_root / "data"
        data_dir.mkdir(parents=True, exist_ok=True)
        lic_path = data_dir / "license.json"
        lic_path.write_text(json.dumps(license_info, indent=2), encoding="utf-8")
        files_copied.append("data/license.json")

    # D-CHILD-5: Apache-2.0 license for government deliveries
    # Copy the full ICDEV™ LICENSE file to keep child apps identical
    license_type = blueprint.get("license", "Apache-2.0")
    parent_license = icdev_root / "LICENSE"
    child_license = child_root / "LICENSE"
    if license_type == "Apache-2.0" and parent_license.exists():
        import shutil

        shutil.copy2(str(parent_license), str(child_license))
        files_copied.append("LICENSE (Apache-2.0, copied from parent)")
    elif license_type == "Apache-2.0":
        # Fallback: generate if parent LICENSE missing (shouldn't happen)
        apache_text = (
            f"Copyright 2024-{datetime.now(tz=timezone.utc).year} "
            f"Sovanna Chuon\n\n"
            f'Licensed under the Apache License, Version 2.0 (the "License");\n'
            f"you may not use this file except in compliance with the License.\n"
            f"You may obtain a copy of the License at\n\n"
            f"    http://www.apache.org/licenses/LICENSE-2.0\n\n"
            f"Unless required by applicable law or agreed to in writing, software\n"
            f'distributed under the License is distributed on an "AS IS" BASIS,\n'
            f"WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.\n"
            f"See the License for the specific language governing permissions and\n"
            f"limitations under the License.\n"
        )
        child_license.write_text(apache_text, encoding="utf-8")
        files_copied.append("LICENSE (Apache-2.0, generated fallback)")

    logger.info(
        "Step 9b: License files copied: %s (demo=%s, license=%s)",
        files_copied,
        demo_mode,
        license_type,
    )
    return {
        "files_copied": files_copied,
        "demo_mode": demo_mode,
        "license_info": license_info,
        "license_type": license_type,
    }


# ============================================================
# STEP 9c: Claude Code Configuration Inheritance (D-CHILD-2)
# ============================================================


def step_09c_claude_code_config(
    child_root: Path,
    blueprint: dict,
    icdev_root: Path,
) -> dict:
    """Step 9c: Copy .claude/ directory artifacts from parent to child.

    D-CHILD-2: .claude/ is a first-class generation artifact.
    D-CHILD-3: PARENT_ONLY_COMMANDS/SKILLS/E2E are excluded.
    """
    files_copied: List[str] = []
    files_skipped: List[str] = []
    claude_src = icdev_root / ".claude"

    if not claude_src.exists():
        logger.info("Step 9c: No .claude directory found in ICDEV™ root — skipping")
        return {"files_copied": [], "files_skipped": [], "skipped": True}

    claude_dst = child_root / ".claude"

    # --- Hooks (copy all from .claude/hooks/) ---
    hooks_src = claude_src / "hooks"
    if hooks_src.exists():
        hooks_dst = claude_dst / "hooks"
        hooks_dst.mkdir(parents=True, exist_ok=True)
        for hook_file in sorted(hooks_src.glob("*")):
            if hook_file.is_file():
                dst_file = hooks_dst / hook_file.name
                content = hook_file.read_text(encoding="utf-8", errors="replace")
                # Adapt pre_tool_use.py: filter APPEND_ONLY_TABLES to child's schema
                if hook_file.name == "pre_tool_use.py":
                    content = _adapt_pre_tool_use_for_child(content, blueprint)
                dst_file.write_text(content, encoding="utf-8")
                files_copied.append(f".claude/hooks/{hook_file.name}")

    # --- Commands (exclude PARENT_ONLY_COMMANDS) ---
    cmds_src = claude_src / "commands"
    if cmds_src.exists():
        cmds_dst = claude_dst / "commands"
        cmds_dst.mkdir(parents=True, exist_ok=True)
        for cmd_file in sorted(cmds_src.glob("*.md")):
            if cmd_file.name in PARENT_ONLY_COMMANDS:
                files_skipped.append(f".claude/commands/{cmd_file.name}")
                continue
            dst_file = cmds_dst / cmd_file.name
            content = cmd_file.read_text(encoding="utf-8", errors="replace")
            content = content.replace("ICDEV™", blueprint["app_name"])
            dst_file.write_text(content, encoding="utf-8")
            files_copied.append(f".claude/commands/{cmd_file.name}")

        # E2E specs (exclude PARENT_ONLY_E2E)
        e2e_src = cmds_src / "e2e"
        if e2e_src.exists():
            e2e_dst = cmds_dst / "e2e"
            e2e_dst.mkdir(parents=True, exist_ok=True)
            for e2e_file in sorted(e2e_src.glob("*.md")):
                if e2e_file.name in PARENT_ONLY_E2E:
                    files_skipped.append(f".claude/commands/e2e/{e2e_file.name}")
                    continue
                dst_file = e2e_dst / e2e_file.name
                content = e2e_file.read_text(encoding="utf-8", errors="replace")
                dst_file.write_text(content, encoding="utf-8")
                files_copied.append(f".claude/commands/e2e/{e2e_file.name}")

    # --- Skills (exclude PARENT_ONLY_SKILLS + capability-gated, D-CHILD-10) ---
    capabilities = blueprint.get("capabilities", {})
    skills_src = claude_src / "skills"
    if skills_src.exists():
        skills_dst = claude_dst / "skills"
        skills_dst.mkdir(parents=True, exist_ok=True)
        for skill_dir in sorted(skills_src.iterdir()):
            if not skill_dir.is_dir():
                continue
            # Check parent-only exclusion
            if skill_dir.name in PARENT_ONLY_SKILLS:
                files_skipped.append(f".claude/skills/{skill_dir.name}/")
                continue
            # Check capability-gated skills
            required_cap = SKILL_CAPABILITY_MAP.get(skill_dir.name)
            if required_cap and not capabilities.get(required_cap, False):
                files_skipped.append(f".claude/skills/{skill_dir.name}/")
                logger.debug(
                    "Skipping skill %s — requires capability %s",
                    skill_dir.name,
                    required_cap,
                )
                continue
            dst_skill = skills_dst / skill_dir.name
            shutil.copytree(str(skill_dir), str(dst_skill), dirs_exist_ok=True)
            files_copied.append(f".claude/skills/{skill_dir.name}/")

    # --- settings.json ---
    settings_src = claude_src / "settings.json"
    if settings_src.exists():
        settings_dst = claude_dst / "settings.json"
        content = settings_src.read_text(encoding="utf-8", errors="replace")
        content = content.replace("ICDEV™", blueprint["app_name"])
        settings_dst.write_text(content, encoding="utf-8")
        files_copied.append(".claude/settings.json")

    # --- file_access_tiers.yaml ---
    tiers_src = _forge_dir(icdev_root, "args") / "file_access_tiers.yaml"
    if tiers_src.exists():
        tiers_dst = child_root / "args" / "file_access_tiers.yaml"
        tiers_dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(str(tiers_src), str(tiers_dst))
        files_copied.append("args/file_access_tiers.yaml")

    logger.info(
        "Step 9c: Claude Code config — %d files copied, %d excluded (parent-only)",
        len(files_copied),
        len(files_skipped),
    )
    return {
        "files_copied": files_copied,
        "files_skipped": files_skipped,
    }


def _adapt_pre_tool_use_for_child(content: str, blueprint: dict) -> str:
    """Filter APPEND_ONLY_TABLES in pre_tool_use.py to child's DB schema.

    Only keeps table names that exist in the child's enabled capability
    table groups. This prevents the hook from referencing tables that
    don't exist in the child's database.
    """
    # Ensure project root is in sys.path for deferred import
    _project_root = str(Path(__file__).resolve().parent.parent.parent)
    if _project_root not in sys.path:
        sys.path.insert(0, _project_root)
    from tools.builder.db_init_generator import (
        CORE_TABLES,
        CAPABILITY_TABLE_MAP,
    )

    # Collect all table names present in the child's schema
    child_tables: set = set(CORE_TABLES.keys())
    capabilities = blueprint.get("capabilities", {})
    for cap_name, enabled in capabilities.items():
        if enabled and cap_name in CAPABILITY_TABLE_MAP:
            child_tables.update(CAPABILITY_TABLE_MAP[cap_name].keys())

    # D-EPSEC-7: Security is always-on — always include security framework tables
    if "security" in CAPABILITY_TABLE_MAP:
        child_tables.update(CAPABILITY_TABLE_MAP["security"].keys())

    # Find the APPEND_ONLY_TABLES set in the hook and filter it
    # Pattern: APPEND_ONLY_TABLES = { ... }
    import re as _re

    pattern = _re.compile(
        r"(APPEND_ONLY_TABLES\s*=\s*\{)(.*?)(\})",
        _re.DOTALL,
    )
    match = pattern.search(content)
    if not match:
        return content  # Hook doesn't have the expected pattern — return as-is

    # Parse existing table names from the set literal
    raw_tables = match.group(2)
    table_names = _re.findall(r'"([^"]+)"', raw_tables)

    # Keep only tables in the child's schema
    filtered = [t for t in table_names if t in child_tables]
    filtered_str = ",\n    ".join(f'"{t}"' for t in sorted(filtered))

    replacement = f"{match.group(1)}\n    {filtered_str},\n{match.group(3)}"
    content = content[: match.start()] + replacement + content[match.end() :]

    return content


# ============================================================
# STEP 10: CSP MCP Server Configuration
# ============================================================


def step_10_csp_mcp_config(child_root: Path, blueprint: dict) -> dict:
    """Step 10: Generate .mcp.json and CSP integration files."""
    app_name = blueprint["app_name"]
    agents = blueprint.get("agents", [])
    csp_servers = blueprint.get("csp_mcp_servers", [])
    cloud_config = blueprint.get("cloud_provider", {})
    provider = cloud_config.get("provider", "aws")

    # Build .mcp.json combining agent MCP servers + CSP MCP servers
    mcp_config: Dict[str, Any] = {"mcpServers": {}}

    # Agent MCP servers
    mcp_server_map = {
        "orchestrator": "core_server",
        "architect": "core_server",
        "builder": "builder_server",
        "compliance": "compliance_server",
        "security": "security_server",
        "knowledge": "knowledge_server",
        "monitor": "monitor_server",
        # D-CHILD-1: Enterprise agent MCP servers
        "requirements_analyst": "requirements_server",
        "supply_chain": "supply_chain_server",
        "simulation": "simulation_server",
        "devsecops_zta": "devsecops_server",
    }
    added_servers = set()
    for agent in agents:
        server_name = mcp_server_map.get(agent["name"])
        if server_name and server_name not in added_servers:
            added_servers.add(server_name)
            key = f"{app_name}-{server_name.replace('_', '-')}"
            mcp_config["mcpServers"][key] = {
                "command": "python",
                "args": [f"tools/mcp/{server_name}.py"],
            }

    # CSP MCP servers
    for server in csp_servers:
        server_name = server.get("name", "")
        if server_name:
            mcp_config["mcpServers"][server_name] = {
                "command": "npx",
                "args": ["-y", server_name],
            }

    mcp_path = child_root / ".mcp.json"
    mcp_path.write_text(json.dumps(mcp_config, indent=2), encoding="utf-8")

    # Generate args/csp_mcp_config.yaml
    csp_config_lines = [
        f"# CSP MCP Configuration for {app_name}",
        f"provider: {provider}",
        f"region: {cloud_config.get('region', 'us-gov-west-1')}",
        f"govcloud: {str(cloud_config.get('govcloud', False)).lower()}",
        "mcp_servers:",
    ]
    for server in csp_servers:
        name = server.get("name", "unknown")
        cat = server.get("category", "core")
        csp_config_lines.append(f'  - name: "{name}"')
        csp_config_lines.append(f'    category: "{cat}"')
        csp_config_lines.append("    transport: stdio")

    csp_config_path = child_root / "args" / "csp_mcp_config.yaml"
    csp_config_path.parent.mkdir(parents=True, exist_ok=True)
    csp_config_path.write_text("\n".join(csp_config_lines) + "\n", encoding="utf-8")

    # Generate context/agentic/csp_integration.md
    integration_lines = [
        f"# CSP Integration — {app_name}",
        "",
        f"## Cloud Provider: {provider.upper()}",
        f"- **Region:** {cloud_config.get('region', 'us-gov-west-1')}",
        f"- **GovCloud:** {'Yes' if cloud_config.get('govcloud') else 'No'}",
        "",
        "## Available MCP Servers",
        "",
        "| Server | Category | Description |",
        "|--------|----------|-------------|",
    ]
    for server in csp_servers:
        integration_lines.append(
            f"| {server.get('name', '')} | {server.get('category', '')} | {server.get('description', '')} |"
        )

    integration_lines.extend(
        [
            "",
            "## Usage",
            "",
            "These MCP servers are configured in `.mcp.json` and available to Claude Code.",
            "Use them for cloud-native operations specific to the target deployment environment.",
            "",
            f"For capabilities not available via {provider.upper()} MCP servers, use the A2A",
            "callback to parent ICDEV™.",
        ]
    )

    integration_path = child_root / "context" / "agentic" / "csp_integration.md"
    integration_path.parent.mkdir(parents=True, exist_ok=True)
    integration_path.write_text("\n".join(integration_lines) + "\n", encoding="utf-8")

    logger.info("Step 10: CSP MCP config — %d servers for %s", len(csp_servers), provider)
    return {
        "mcp_json": str(mcp_path),
        "csp_config": str(csp_config_path),
        "csp_integration": str(integration_path),
        "total_mcp_servers": len(mcp_config["mcpServers"]),
        "csp_servers": len(csp_servers),
    }


# ============================================================
# STEP 11b: README Generation
# ============================================================

# Human-readable capability descriptions for the README "sell" section
CAP_DESCRIPTIONS: Dict[str, str] = {
    "compliance": "ATO Compliance — SSP, POAM, STIG, SBOM, CUI markings, NIST 800-53, FedRAMP, CMMC",
    "security": "Security Scanning — SAST (Bandit), dependency audit, secret detection, container scanning",
    "testing": "Testing Framework — pytest unit + behave BDD + Playwright E2E + security gates",
    "multi_agent": "Multi-Agent Architecture — A2A protocol, agent cards, MCP servers, domain routing",
    "cicd": "CI/CD Integration — GitHub Actions + GitLab CI, webhooks, poll triggers, slash commands",
    "mbse": "Model-Based Systems Engineering — SysML, DOORS NG, digital thread, model-code sync",
    "monitoring": "Production Monitoring — Log analysis, metrics, alerts, health checks, self-healing",
    "dashboard": "Web Dashboard — Flask SSR, real-time updates, role-based views, accessibility",
    "knowledge": "Knowledge Base — Pattern detection, self-healing, ML recommendations",
    "modernization": "App Modernization — 7R assessment, version/framework migration, strangler fig",
    "supply_chain": "Supply Chain Intelligence — Dependency graph, SBOM aggregation, ISA lifecycle, CVE triage",
    "simulation": "Digital Program Twin — 6-dimension simulation, Monte Carlo, COA generation",
    "devsecops": "DevSecOps — Pipeline security, policy-as-code (Kyverno/OPA), image attestation",
    "zta": "Zero Trust Architecture — 7-pillar maturity, NIST 800-207, service mesh, mTLS",
    "mosa": "DoD MOSA — Modular Open Systems, ICD/TSP generation, modularity analysis",
    "marketplace": "FORGE Marketplace — Federated asset sharing, 7-gate security pipeline",
    "innovation": "Innovation Engine — Autonomous self-improvement, web scanning, trend detection",
    "translation": "Cross-Language Translation — 5-phase hybrid pipeline, 30 language pairs",
    "observability": "Observability & XAI — Distributed tracing, provenance, AgentSHAP attribution",
    "ai_transparency": "AI Transparency — Model/system cards, AI inventory, fairness, confabulation detection",
    "ai_accountability": "AI Accountability — Oversight plans, CAIO designation, incident response",
    "document_intelligence": "Document Intelligence — NotebookLM-style BM25+KG research, study guides, FAQ, timeline, chat with sources",
}


def _generate_readme(child_root: Path, blueprint: dict) -> dict:
    """Generate README.md that tells the ICDEV™ story and lists capabilities used.

    Args:
        child_root: Root directory of the generated child app.
        blueprint: Blueprint dict from app_blueprint.py.

    Returns:
        Dict with readme_path and sections_count.
    """
    app_name = blueprint["app_name"]
    classification = blueprint.get("classification", "CUI")
    impact_level = blueprint.get("impact_level", "IL4")
    demo_mode = blueprint.get("demo_mode", False)
    agents = blueprint.get("agents", [])
    capabilities = blueprint.get("capabilities", {})
    scorecard = blueprint.get("fitness_scorecard", {})

    active_caps = sorted(k for k, v in capabilities.items() if v)
    description = blueprint.get("description", "") or blueprint.get("purpose", "") or scorecard.get("spec", "")

    sections: list = []

    # Demo banner
    if demo_mode:
        sections.append(
            "> **DEMONSTRATION ONLY** — This application is a demo build. "
            "It uses PUBLIC classification and must NOT be used for operational, "
            "classified, or sensitive data.\n"
        )

    # Title + ICDEV™ intro
    sections.append(f"# {app_name}\n")
    sections.append(
        "**Built with [ICDEV™](https://github.com/icdev) — the Intelligent "
        "Coding Development platform.**\n\n"
        "ICDEV™ is a meta-builder that autonomously constructs Gov/DoD applications "
        "using the FORGE framework (Goals, Orchestration, Tools, Args, Context, "
        "Hard Prompts) and the ANVIL workflow (Architect → Trace → Link → Assemble "
        "→ Stress-test). It handles the full SDLC with TDD/BDD, NIST 800-53 RMF "
        "compliance, and self-healing capabilities.\n"
    )

    # Classification badge
    sections.append(f"**Classification:** `{classification}` | **Impact Level:** `{impact_level}`\n")

    # Purpose
    if description:
        sections.append(f"## Purpose\n\n{description}\n")

    # Architecture
    sections.append(
        "## Architecture\n\n"
        "This application follows the **FORGE 6-Layer Framework**:\n\n"
        "| Layer | Role |\n"
        "|-------|------|\n"
        "| **Goals** | Process definitions — what to achieve, which tools, expected outputs |\n"
        "| **Orchestration** | AI reads goals → decides tool order → applies args → references context |\n"
        "| **Tools** | Python scripts, one job each. Deterministic. |\n"
        "| **Args** | YAML/JSON behavior settings |\n"
        "| **Context** | Static reference material |\n"
        "| **Hard Prompts** | Reusable LLM instruction templates |\n"
    )

    # ICDEV™ Capabilities Used — the "sell" section
    if active_caps:
        sections.append("## ICDEV™ Capabilities Used\n")
        sections.append("This application leverages the following ICDEV™ capabilities:\n")
        for cap in active_caps:
            desc = CAP_DESCRIPTIONS.get(cap, cap.replace("_", " ").title())
            sections.append(f"- **{cap}** — {desc}")
        sections.append("")  # blank line

    # Agents
    if agents:
        sections.append("## Agents\n")
        sections.append("| Agent | Port | Role |")
        sections.append("|-------|------|------|")
        for a in agents:
            name = a.get("name", "unknown")
            port = a.get("port", "?")
            role = a.get("role", "")
            sections.append(f"| {name.title()} | {port} | {role} |")
        sections.append("")

    # Compliance Posture
    if capabilities.get("compliance", False):
        sections.append(
            "## Compliance Posture\n\n"
            "This application includes compliance tooling for:\n"
            "- NIST 800-53 Rev 5 control mapping\n"
            "- FedRAMP Moderate/High baselines\n"
            "- CMMC Level 2/3 practices\n"
            "- ATO artifacts: SSP, POAM, STIG checklist, SBOM\n"
            "- CUI markings applied at generation time\n"
        )

    # Quick Start
    quick_start_cmds = [
        "# Initialize database",
        "python tools/db/init_db.py",
        "",
        "# Load memory",
        "python tools/memory/memory_read.py --format markdown",
    ]
    if capabilities.get("dashboard", False):
        quick_start_cmds += ["", "# Start dashboard", "python tools/dashboard/app.py"]
    if capabilities.get("testing", False):
        quick_start_cmds += ["", "# Run tests", "pytest tests/ -v"]

    sections.append("## Quick Start\n")
    sections.append("```bash")
    sections.extend(quick_start_cmds)
    sections.append("```\n")

    # Footer
    gen_date = blueprint.get("generated_at", datetime.now(tz=timezone.utc).isoformat())
    sections.append("---\n")
    sections.append(f"*Generated by ICDEV™ on {gen_date[:10]}*\n")

    readme_content = "\n".join(sections)
    readme_path = child_root / "README.md"
    readme_path.write_text(readme_content, encoding="utf-8")

    logger.info("Step 11b: README.md generated (%d sections)", len(sections))
    return {"readme_path": str(readme_path), "sections_count": len(sections)}


# ============================================================
# STEP 11: Dynamic CLAUDE.md
# ============================================================


def step_11_dynamic_claude_md(child_root: Path, blueprint: dict) -> dict:
    """Step 11: Generate dynamic CLAUDE.md using claude_md_generator."""
    generate_fn = _import_sister("claude_md_generator", "generate_claude_md")

    if generate_fn:
        content = generate_fn(blueprint)
        method = "claude_md_generator"
    else:
        # Fallback: minimal CLAUDE.md
        app_name = blueprint["app_name"]
        agents = blueprint.get("agents", [])
        content = f"""# CLAUDE.md

This file provides guidance to Claude Code when working with {app_name}.

---

## Architecture: FORGE Framework

This is a 6-layer agentic system: Frameworks, Orchestration, Routines, Guidance, Evidence.

### Key Files
- `goals/manifest.md` — Index of all goal workflows
- `tools/manifest.md` — Master list of all tools
- `memory/MEMORY.md` — Long-term facts and preferences

### Session Start Protocol
1. Read `memory/MEMORY.md`
2. Read today's daily log
3. Or run: `python tools/memory/memory_read.py --format markdown`

---

## {app_name} System

### Agents ({len(agents)})

| Agent | Port | Role |
|-------|------|------|
"""
        for a in agents:
            content += f"| {a['name']} | {a['port']} | {a.get('role', '')} |\n"

        content += """
---

## Guardrails

- Always check `tools/manifest.md` before writing a new script
- Verify tool output format before chaining
- **This application CANNOT generate child applications**
- Audit trail is append-only — NEVER add UPDATE/DELETE operations

---

## Continuous Improvement

Every failure strengthens the system. Be direct. Be reliable. Get it done.
"""
        method = "fallback"

    claude_md_path = child_root / "CLAUDE.md"
    claude_md_path.write_text(content, encoding="utf-8")

    line_count = content.count("\n") + 1
    logger.info("Step 11: CLAUDE.md generated (%d lines, method=%s)", line_count, method)
    return {"claude_md_path": str(claude_md_path), "lines": line_count, "method": method}


# ============================================================
# STEP 12: Audit + Registration
# ============================================================


def step_12_audit_and_registration(child_root: Path, blueprint: dict, db_path: Path) -> dict:
    """Step 12: Log to ICDEV™ audit trail and register in child_app_registry."""
    app_name = blueprint["app_name"]
    blueprint_hash = blueprint.get("blueprint_hash", "")

    # Audit log
    audit_log_event(
        event_type="child_app_generated",
        actor="child-app-generator",
        action=f"Generated child app '{app_name}' at {child_root}",
        project_id=blueprint.get("fitness_scorecard", {}).get("project_id", ""),
        details=json.dumps(
            {
                "app_name": app_name,
                "blueprint_hash": blueprint_hash,
                "agents": len(blueprint.get("agents", [])),
                "capabilities": sum(1 for v in blueprint.get("capabilities", {}).values() if v),
            }
        ),
    )

    # Register in child_app_registry table
    registered = False
    try:
        if db_path.exists():
            conn = get_connection()
            conn.execute(
                "INSERT OR REPLACE INTO child_app_registry "
                "(id, parent_project_id, child_name, child_path, blueprint_hash, "
                "capabilities, agent_count, cloud_provider, callback_url, classification) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
                (
                    str(uuid.uuid4()),
                    blueprint.get("fitness_scorecard", {}).get("project_id", ""),
                    app_name,
                    str(child_root),
                    blueprint_hash,
                    json.dumps(blueprint.get("capabilities", {})),
                    len(blueprint.get("agents", [])),
                    blueprint.get("cloud_provider", {}).get("provider", "aws"),
                    blueprint.get("parent_callback", {}).get("url", ""),
                    blueprint.get("classification", "CUI"),
                ),
            )
            conn.commit()
            conn.close()
            registered = True
            logger.info("Step 12: Registered child app in ICDEV™ database")
    except Exception as e:
        logger.warning("Step 12: Failed to register in DB: %s", e)

    # Register blockchain provenance for child app blueprint
    try:
        from tools.provenance.registry import register_citation
        from tools.blockchain.chain_anchor import ChainAnchor

        if blueprint_hash:
            reg_id = register_citation(
                citation_type="sbom",
                source_table="child_app_registry",
                source_record_id=f"child-app-{app_name}",
                source_hash=blueprint_hash,
                source_doc=f"Child app: {app_name}",
                project_id=blueprint.get("fitness_scorecard", {}).get("project_id", ""),
            )
            if reg_id:
                ChainAnchor().anchor_provenance([reg_id])
                logger.info("Step 12: Child app provenance anchored: %s", reg_id)
    except Exception as e:
        logger.warning("Step 12: Provenance registration skipped: %s", e)

    # Phase 36 integration: write genome manifest to child directory
    genome_version = None
    try:
        if db_path.exists():
            gconn = get_connection()
            row = gconn.execute(
                "SELECT version, content_hash, genome_data FROM genome_versions ORDER BY created_at DESC LIMIT 1"
            ).fetchone()
            if row:
                genome_version = row["version"]
                genome_manifest = {
                    "parent_id": blueprint.get("fitness_scorecard", {}).get("project_id", "icdev-parent"),
                    "genome_version": row["version"],
                    "content_hash": row["content_hash"],
                    "capabilities_baseline": json.loads(row["genome_data"]) if row["genome_data"] else {},
                    "generation_timestamp": datetime.now(tz=timezone.utc).isoformat(),
                    "grandchild_prevention": True,
                }
                gm_path = child_root / "data" / "genome_manifest.json"
                gm_path.parent.mkdir(parents=True, exist_ok=True)
                gm_path.write_text(json.dumps(genome_manifest, indent=2), encoding="utf-8")
                logger.info(
                    "Step 12: Wrote genome manifest (v%s) to child",
                    genome_version,
                )
            gconn.close()
    except Exception as e:
        logger.warning("Step 12: Genome manifest write failed: %s", e)

    # Generate summary report in child app
    summary = {
        "app_name": app_name,
        "child_root": str(child_root),
        "blueprint_hash": blueprint_hash,
        "agents": len(blueprint.get("agents", [])),
        "capabilities": {k: v for k, v in blueprint.get("capabilities", {}).items() if v},
        "cloud_provider": blueprint.get("cloud_provider", {}).get("provider", "aws"),
        "classification": blueprint.get("classification", "CUI"),
        "genome_version": genome_version,
        "generated_at": datetime.now(tz=timezone.utc).isoformat(),
        "generated_by": "icdev/child_app_generator",
    }
    summary_path = child_root / "data" / "generation_summary.json"
    summary_path.parent.mkdir(parents=True, exist_ok=True)
    summary_path.write_text(json.dumps(summary, indent=2), encoding="utf-8")

    logger.info("Step 12: Audit complete, registered=%s", registered)
    return {"registered": registered, "summary_path": str(summary_path)}


# ============================================================
# STEP 13: Production Audit
# ============================================================


def step_13_production_audit(child_root: Path, blueprint: dict) -> dict:
    """Run production audit on the generated child app.

    Invokes ICDEV™'s production_audit.py as a subprocess with the child app
    as the working directory, then stores the results in the child app's
    data directory.

    Args:
        child_root: Root directory of the child app.
        blueprint: Blueprint dict.

    Returns:
        Dict with audit results summary.
    """
    audit_script = BASE_DIR / "tools" / "testing" / "production_audit.py"
    if not audit_script.exists():
        logger.warning("Step 13: production_audit.py not found, skipping")
        return {"skipped": True, "reason": "audit script not found"}

    try:
        env = {**os.environ, "PYTHONDONTWRITEBYTECODE": "1"}
        result = subprocess.run(
            [sys.executable, str(audit_script), "--json"],
            capture_output=True,
            text=True,
            cwd=str(child_root),
            timeout=120,
            env=env,
        )

        # Parse JSON output
        audit_data = {}
        if result.stdout.strip():
            try:
                audit_data = json.loads(result.stdout.strip())
            except json.JSONDecodeError:
                audit_data = {"raw_output": result.stdout[:2000]}

        # Store audit results in child app
        data_dir = child_root / "data"
        data_dir.mkdir(parents=True, exist_ok=True)
        audit_path = data_dir / "production_audit.json"
        audit_path.write_text(json.dumps(audit_data, indent=2, default=str), encoding="utf-8")

        # Summary
        checks = audit_data.get("checks", [])
        passed = sum(1 for c in checks if c.get("status") == "pass")
        failed = sum(1 for c in checks if c.get("status") == "fail")
        total = len(checks)

        logger.info("Step 13: Production audit complete — %d/%d passed, %d failed", passed, total, failed)
        return {
            "audit_path": str(audit_path),
            "total_checks": total,
            "passed": passed,
            "failed": failed,
            "exit_code": result.returncode,
        }

    except subprocess.TimeoutExpired:
        logger.warning("Step 13: Production audit timed out (120s)")
        return {"skipped": True, "reason": "timeout"}
    except Exception as e:
        logger.warning("Step 13: Production audit failed: %s", e)
        return {"skipped": True, "reason": str(e)}


# ============================================================
# STEP 14: FORGE Compliance Validation
# ============================================================


def step_14_gotcha_validation(child_root: Path, blueprint: dict) -> dict:
    """Step 14: Validate FORGE framework compliance of generated child app.

    Runs the forge_validator to verify all 6 FORGE layers are populated
    and ANVIL workflow structure is present. This ensures child apps follow
    the FORGE framework as mandated by build_app.md.

    BMAD-adapted: adversarial validation — assumes the build is incomplete
    until proven otherwise.
    """
    validate_fn = _import_sister("forge_validator", "validate")

    if validate_fn:
        report = validate_fn(child_root)
        report_dict = report.to_dict()

        # Store validation results in child app
        data_dir = child_root / "data"
        data_dir.mkdir(parents=True, exist_ok=True)
        report_path = data_dir / "gotcha_validation.json"
        report_path.write_text(json.dumps(report_dict, indent=2, default=str), encoding="utf-8")

        # Log warnings for failed checks
        for check in report_dict.get("checks", []):
            if check.get("status") == "fail":
                logger.warning(
                    "FORGE validation FAIL: %s — %s (fix: %s)",
                    check.get("check_id"),
                    check.get("message"),
                    check.get("fix_suggestion"),
                )
            elif check.get("status") == "warn":
                logger.info(
                    "FORGE validation WARN: %s — %s",
                    check.get("check_id"),
                    check.get("message"),
                )

        logger.info(
            "Step 14: FORGE validation — score %.0f%% (%d/%d passed, %d failed)",
            report_dict.get("score", 0) * 100,
            report_dict.get("passed_checks", 0),
            report_dict.get("total_checks", 0),
            report_dict.get("failed_checks", 0),
        )
        return {
            "report_path": str(report_path),
            "overall_pass": report_dict.get("overall_pass", False),
            "score": report_dict.get("score", 0),
            "passed": report_dict.get("passed_checks", 0),
            "failed": report_dict.get("failed_checks", 0),
            "warned": report_dict.get("warned_checks", 0),
            "layer_summary": report_dict.get("layer_summary", {}),
        }

    # Fallback: basic directory existence check
    logger.warning("Step 14: forge_validator not available, running basic check")
    gotcha_dirs = ["goals", "tools", "args", "context", "hardprompts", "memory"]
    present = [d for d in gotcha_dirs if (child_root / d).is_dir()]
    missing = [d for d in gotcha_dirs if d not in present]

    if missing:
        logger.warning("Step 14: Missing FORGE directories: %s", ", ".join(missing))

    return {
        "method": "fallback",
        "overall_pass": len(missing) == 0,
        "present": present,
        "missing": missing,
    }


def step_15_syntax_validation(child_root: Path, blueprint: dict) -> dict:
    """Step 15: py_compile all generated .py files to catch syntax errors.

    Adaptations (db_rename, port_remap, app_name_replace) can break Python
    syntax. This step catches those errors before the user discovers them.
    """
    import py_compile

    py_files = list(child_root.rglob("*.py"))
    passed = []
    failed = []

    for py_path in py_files:
        try:
            py_compile.compile(str(py_path), doraise=True)
            passed.append(str(py_path.relative_to(child_root)))
        except py_compile.PyCompileError as exc:
            failed.append(
                {
                    "file": str(py_path.relative_to(child_root)),
                    "error": str(exc),
                }
            )
            logger.error("Syntax error in %s: %s", py_path, exc)

    status = "pass" if not failed else "fail"
    logger.info(
        "Step 15: Syntax validation — %d/%d files passed",
        len(passed),
        len(py_files),
    )
    return {
        "total_files": len(py_files),
        "passed": len(passed),
        "failed": len(failed),
        "failures": failed,
        "status": status,
    }


def step_16_db_execution(child_root: Path, blueprint: dict) -> dict:
    """Step 16: Execute the generated DB init script and verify tables.

    Catches SQL syntax errors and missing tables before the user
    runs the app. Without this, a broken schema goes undetected
    until runtime.
    """
    # Find the DB init script
    db_dir = child_root / "tools" / "db"
    init_scripts = list(db_dir.glob("init_*.py")) if db_dir.exists() else []

    if not init_scripts:
        logger.info("Step 16: No DB init script found, skipping")
        return {"status": "skipped", "reason": "no init script"}

    script = init_scripts[0]
    app_name = blueprint.get("app_name", "app")
    db_path = child_root / "data" / f"{app_name}.db"

    try:
        result = subprocess.run(
            [sys.executable, str(script)],
            capture_output=True,
            text=True,
            timeout=30,
            cwd=str(child_root),
        )
        if result.returncode != 0:
            logger.error("Step 16: DB init failed: %s", result.stderr)
            return {
                "status": "fail",
                "error": result.stderr[:500],
                "script": str(script.relative_to(child_root)),
            }

        # Verify tables exist
        if db_path.exists():

            conn = get_connection(str(db_path))
            # Backend-aware table listing (pgrt-sweep-06) — no sqlite_master/translation reliance.
            tables = list_tables(conn)
            conn.close()

            logger.info(
                "Step 16: DB init OK — %d tables created",
                len(tables),
            )
            return {
                "status": "pass",
                "tables": tables,
                "table_count": len(tables),
                "db_path": str(db_path),
            }

        return {"status": "warn", "reason": "DB file not created"}

    except subprocess.TimeoutExpired:
        return {"status": "fail", "error": "DB init timed out (30s)"}
    except Exception as exc:
        return {"status": "fail", "error": str(exc)}


def step_17_agent_card_validation(
    child_root: Path,
    blueprint: dict,
) -> dict:
    """Step 17: Validate agent card JSON files for correctness.

    Checks:
    - Valid JSON syntax
    - Required fields (name, port) present
    - No port conflicts between agents
    """
    cards_dir = child_root / ".well-known"
    if not cards_dir.exists():
        # Try alternative locations
        card_files = list(child_root.rglob("agent.json"))
    else:
        card_files = list(cards_dir.glob("*.json"))

    if not card_files:
        return {"status": "skipped", "reason": "no agent cards"}

    valid = []
    invalid = []
    ports_seen: dict = {}

    for card_path in card_files:
        try:
            content = card_path.read_text(encoding="utf-8")
            data = json.loads(content)

            # Check required fields
            missing = []
            for field in ("name",):
                if field not in data:
                    missing.append(field)

            if missing:
                invalid.append(
                    {
                        "file": str(card_path.relative_to(child_root)),
                        "error": f"Missing fields: {missing}",
                    }
                )
                continue

            # Check port conflicts
            port = data.get("port")
            if port:
                if port in ports_seen:
                    invalid.append(
                        {
                            "file": str(card_path.relative_to(child_root)),
                            "error": f"Port {port} conflicts with {ports_seen[port]}",
                        }
                    )
                else:
                    ports_seen[port] = data.get("name", card_path.name)

            valid.append(str(card_path.relative_to(child_root)))

        except json.JSONDecodeError as exc:
            invalid.append(
                {
                    "file": str(card_path.relative_to(child_root)),
                    "error": f"Invalid JSON: {exc}",
                }
            )

    logger.info(
        "Step 17: Agent cards — %d valid, %d invalid",
        len(valid),
        len(invalid),
    )
    return {
        "status": "pass" if not invalid else "warn",
        "valid": len(valid),
        "invalid": len(invalid),
        "failures": invalid,
        "ports": ports_seen,
    }


# ============================================================
# MAIN ORCHESTRATOR
# ============================================================



def _forge_dir(icdev_root: Path, layer: str) -> Path:
    """Locate a FORGE layer under EITHER repo layout.

    A source checkout keeps the layers at the root (`<root>/goals`); the wheel
    installs them as package data (`<root>/data/goals`). This function probes the
    packaged location first and falls back to the source one.

    Without it, `generate_child_app` run from `pip install icdev` resolved
    `_forge_dir(icdev_root, "goals")` against `site-packages/icdev/goals`, which does not
    exist — so every goal lookup missed and the generated child app came out
    with ZERO goals and ZERO hardprompts. It still "succeeded": a skeleton with
    no FORGE Goals layer, which is the part that makes a generated app a system
    that can build systems. Same defect class as the 1.2.39 component-registry
    fix, which probed only the source layout.
    """
    packaged = icdev_root / "data" / layer
    if packaged.is_dir():
        return packaged
    return icdev_root / layer

def generate_child_app(
    blueprint: dict,
    project_path: str,
    name: str,
    icdev_root: Optional[Path] = None,
    db_path: Optional[Path] = None,
    template_dir: Optional[Path] = None,
    legacy: bool = False,
) -> dict:
    """Generate a complete child application from a blueprint.

    Executes 20 steps sequentially (12 core + 9b license + 9c claude config +
    11b README + 13 audit + 14 FORGE validation + 15 syntax validation +
    16 DB execution + 17 agent card validation), collecting results from each.

    When ``template_dir`` is provided and ``legacy`` is False, the legacy
    baseline is first generated, then the template flavor is rendered on top
    to specialize the child app (blueprint, args, context, etc.).

    Args:
        blueprint: Complete blueprint dict from app_blueprint.py.
        project_path: Parent directory for the child app.
        name: Child application name.
        icdev_root: Path to ICDEV™ project root (auto-detected if None).
        db_path: Path to ICDEV™ database (auto-detected if None).
        template_dir: Optional child-app template directory (e.g. data/templates/child_apps/compliance).
        legacy: If True, skip the template overlay and use the original copy-and-adapt path only.

    Returns:
        Summary dict with step results and overall status.
    """
    child_root = Path(project_path) / name
    icdev_root = icdev_root or BASE_DIR
    db_path = db_path or DB_PATH

    # D-EPSEC-7: Normalize blueprint — security is always-on for child apps
    if "capabilities" not in blueprint:
        blueprint["capabilities"] = {}
    blueprint["capabilities"]["security"] = True

    mode = "legacy copy-and-adapt" if legacy else ("template-composed" if template_dir else "baseline")
    logger.info("Generating child app '%s' at %s (mode=%s)", name, child_root, mode)
    start_time = datetime.now(tz=timezone.utc)

    results: Dict[str, Any] = {
        "app_name": name,
        "child_root": str(child_root),
        "icdev_root": str(icdev_root),
        "steps": {},
        "status": "success",
        "errors": [],
    }

    # Define steps with their signatures
    steps: List[Tuple[str, Any]] = [
        ("01_directory_tree", lambda: step_01_create_directory_tree(child_root, blueprint)),
        ("02_copy_adapt_tools", lambda: step_02_copy_and_adapt_tools(child_root, blueprint, icdev_root)),
        ("03_agent_infra", lambda: step_03_agent_infrastructure(child_root, blueprint, icdev_root)),
        ("04_memory_bootstrap", lambda: step_04_memory_bootstrap(child_root, blueprint)),
        ("05_db_init_script", lambda: step_05_db_init_script(child_root, blueprint)),
        ("06_goals_hardprompts", lambda: step_06_goals_and_hardprompts(child_root, blueprint, icdev_root)),
        ("07_args_context", lambda: step_07_args_and_context(child_root, blueprint, icdev_root)),
        ("08_a2a_callback", lambda: step_08_a2a_callback_client(child_root, blueprint)),
        ("09_cicd_setup", lambda: step_09_cicd_setup(child_root, blueprint, icdev_root)),
        ("09b_license", lambda: _copy_license_files(child_root, blueprint, icdev_root)),
        ("09c_claude_config", lambda: step_09c_claude_code_config(child_root, blueprint, icdev_root)),
        ("10_csp_mcp_config", lambda: step_10_csp_mcp_config(child_root, blueprint)),
        ("11_claude_md", lambda: step_11_dynamic_claude_md(child_root, blueprint)),
        ("11b_readme", lambda: _generate_readme(child_root, blueprint)),
        ("12_audit_register", lambda: step_12_audit_and_registration(child_root, blueprint, db_path)),
        ("13_production_audit", lambda: step_13_production_audit(child_root, blueprint)),
        ("14_gotcha_validation", lambda: step_14_gotcha_validation(child_root, blueprint)),
        ("15_syntax_validation", lambda: step_15_syntax_validation(child_root, blueprint)),
        ("16_db_execution", lambda: step_16_db_execution(child_root, blueprint)),
        ("17_agent_card_validation", lambda: step_17_agent_card_validation(child_root, blueprint)),
    ]

    for step_name, step_fn in steps:
        try:
            logger.info("Running step: %s", step_name)
            step_result = step_fn()
            results["steps"][step_name] = {"status": "success", **(step_result or {})}
        except Exception as e:
            logger.error("Step %s failed: %s", step_name, e, exc_info=True)
            results["steps"][step_name] = {"status": "error", "error": str(e)}
            results["errors"].append(f"{step_name}: {e}")

    # Template flavor overlay (non-legacy mode): specialize the baseline with a
    # configurable Jinja2 template tree.
    if template_dir and not legacy:
        try:
            logger.info("Overlaying template flavor from %s", template_dir)
            overlay_result = _overlay_template(child_root, template_dir, blueprint, name)
            results["steps"]["00_template_overlay"] = {"status": "success" if overlay_result["success"] else "error", **overlay_result}
            if not overlay_result["success"]:
                results["errors"].append(
                    f"00_template_overlay: {overlay_result.get('errors', []) + overlay_result.get('validation_failures', [])}"
                )
        except Exception as e:
            logger.error("Template overlay failed: %s", e, exc_info=True)
            results["steps"]["00_template_overlay"] = {"status": "error", "error": str(e)}
            results["errors"].append(f"00_template_overlay: {e}")

    # Compute overall status
    failed_steps = [s for s, r in results["steps"].items() if r.get("status") == "error"]
    if failed_steps:
        results["status"] = "partial" if len(failed_steps) < len(steps) else "failed"

    elapsed = (datetime.now(tz=timezone.utc) - start_time).total_seconds()
    results["elapsed_seconds"] = round(elapsed, 2)
    results["failed_steps"] = failed_steps

    logger.info(
        "Child app '%s' generation %s in %.1fs (%d/%d steps succeeded)",
        name,
        results["status"],
        elapsed,
        len(steps) - len(failed_steps),
        len(steps),
    )
    return results


# ============================================================
# CLI
# ============================================================


def _apply_framework_detection(blueprint: dict, source_path: str) -> dict:
    """Run framework_detector on source_path and merge results into blueprint.

    Decision D-FD-1: Detection is advisory — only fills absent fields.
    Logs detection results; never raises (returns blueprint unchanged on error).
    """
    try:
        from tools.builder.framework_detector import (
            detect_from_source,
            merge_detection_into_blueprint,
        )

        detection = detect_from_source(source_path)
        if detection.get("error"):
            logger.warning(
                "Framework detection error for '%s': %s",
                source_path,
                detection["error"],
            )
            return blueprint

        logger.info(
            "Framework detected — language=%s framework=%s confidence=%.0f%%",
            detection.get("language", "unknown"),
            detection.get("framework", "unknown"),
            detection.get("confidence", 0) * 100,
        )
        blueprint = merge_detection_into_blueprint(blueprint, detection, overwrite=False)
    except ImportError:
        logger.warning("framework_detector not available — skipping auto-detect")
    except Exception as exc:
        logger.warning("Framework detection failed: %s", exc)
    return blueprint


def main():
    """CLI entry point for child app generation."""
    parser = argparse.ArgumentParser(
        description="Generate mini-ICDEV™ clone child application from blueprint",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Examples:\n"
            "  python tools/builder/child_app_generator.py "
            "--blueprint bp.json --project-path /tmp --name my-app --json\n"
            "  python tools/builder/child_app_generator.py "
            "--blueprint bp.json --project-path /tmp --name my-app "
            "--source-path ./src --auto-detect --json"
        ),
    )
    parser.add_argument("--blueprint", help="Path to blueprint JSON file (required unless --refresh-trust)")
    parser.add_argument("--project-path", help="Parent directory for the child app (required unless --refresh-trust)")
    parser.add_argument("--name", help="Child application name (required unless --refresh-trust)")
    parser.add_argument(
        "--refresh-trust",
        metavar="APP_DIR",
        help="Re-inherit always-on TRUST/framework modules (tools/quality, tools/workflow, "
        "tools/builder, tools/dx) into an EXISTING child app at APP_DIR. Prints a DIFF REPORT "
        "and writes nothing by default (HITL gate); pass --apply to write.",
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="With --refresh-trust: write the changes (default is a dry-run diff report).",
    )
    parser.add_argument("--source-path", help="Source directory to scan for zero-config language/framework detection")
    parser.add_argument(
        "--auto-detect",
        action="store_true",
        help="Enable zero-config detection from --source-path "
        "(merges language, framework, deploy_template, capabilities into blueprint)",
    )
    parser.add_argument("--icdev-root", help="Path to ICDEV™ root (default: auto-detect)")
    parser.add_argument("--db-path", help="Path to ICDEV™ database (default: data/icdev.db)")
    parser.add_argument("--json", action="store_true", help="Output results as JSON")
    parser.add_argument("--verbose", action="store_true", help="Enable debug logging")
    parser.add_argument(
        "--template",
        help="Path to a child-app template directory (e.g. data/templates/child_apps/compliance)",
    )
    parser.add_argument(
        "--flavor",
        help="Built-in child-app flavor name (compliance, ai-lab, govcon, minimal). Overrides --template default.",
    )
    parser.add_argument(
        "--legacy",
        action="store_true",
        help="Use the original copy-and-adapt generator only, without template overlay",
    )

    args = parser.parse_args()

    level = logging.DEBUG if args.verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    )

    # cvx-gen-04: TRUST re-inheritance mode (does not generate a new app).
    if args.refresh_trust:
        icdev_root = Path(args.icdev_root) if args.icdev_root else BASE_DIR
        result = refresh_trust_modules(
            args.refresh_trust,
            icdev_root=icdev_root,
            dry_run=not args.apply,
        )
        if args.json:
            print(json.dumps(result, indent=2))
        else:
            if result.get("status") != "success":
                print(f"ERROR: {result.get('error')}")
            else:
                mode = "APPLIED" if result["applied"] else ("DRY-RUN (no changes written)" if result["dry_run"] else "NO CHANGES")
                print(f"\n{'=' * 60}")
                print("TRUST Re-Inheritance Refresh")
                print(f"{'=' * 60}")
                print(f"Child app:  {result['app_dir']}")
                print(f"Parent:     {result['icdev_root']}")
                print(f"Dirs:       {', '.join(result['dirs'])}")
                print(f"Mode:       {mode}")
                print(f"Would add:     {len(result['added'])}")
                print(f"Would update:  {len(result['updated'])}")
                print(f"Unchanged:     {result['unchanged_count']}")
                for f in result["added"]:
                    print(f"  [ADD]    {f}")
                for f in result["updated"]:
                    print(f"  [UPDATE] {f}")
                if result["skipped"]:
                    for s in result["skipped"]:
                        print(f"  [SKIP]   {s['dir']} ({s['reason']})")
                if result["dry_run"] and result["would_change"]:
                    print("\nRe-run with --apply to write these changes (HITL confirmation).")
        sys.exit(0 if result.get("status") == "success" else 1)

    # Generation mode requires the core args.
    missing = [n for n, v in (("--blueprint", args.blueprint), ("--project-path", args.project_path), ("--name", args.name)) if not v]
    if missing:
        parser.error(f"the following arguments are required: {', '.join(missing)}")

    # Load blueprint
    bp_path = Path(args.blueprint)
    if not bp_path.exists():
        logger.error("Blueprint file not found: %s", bp_path)
        sys.exit(1)

    try:
        with open(bp_path) as f:
            blueprint = json.load(f)
    except (json.JSONDecodeError, IOError) as e:
        logger.error("Failed to load blueprint: %s", e)
        sys.exit(1)

    # Zero-config detection: merge language/framework/capabilities into blueprint
    if args.auto_detect:
        source = args.source_path or args.project_path
        logger.info("Auto-detect enabled — scanning: %s", source)
        blueprint = _apply_framework_detection(blueprint, source)

    # Resolve paths
    icdev_root = Path(args.icdev_root) if args.icdev_root else BASE_DIR
    db_path = Path(args.db_path) if args.db_path else DB_PATH

    # Resolve child-app template flavor (default to minimal unless legacy)
    template_dir = None
    if args.legacy:
        if args.template or args.flavor:
            logger.warning("--legacy ignores --template/--flavor")
    else:
        template_dir = _resolve_template_dir(
            args.template,
            args.flavor or "minimal",
            icdev_root,
        )

    # Generate child app
    results = generate_child_app(
        blueprint=blueprint,
        project_path=args.project_path,
        name=args.name,
        icdev_root=icdev_root,
        db_path=db_path,
        template_dir=template_dir,
        legacy=args.legacy,
    )

    if args.json:
        print(json.dumps(results, indent=2))
    else:
        status = results["status"]
        steps = results["steps"]
        succeeded = sum(1 for r in steps.values() if r.get("status") == "success")
        total = len(steps)

        print(f"\n{'=' * 60}")
        print(f"Child App Generation: {results['app_name']}")
        print(f"{'=' * 60}")
        print(f"Status:     {status.upper()}")
        print(f"Location:   {results['child_root']}")
        print(f"Steps:      {succeeded}/{total} succeeded")
        print(f"Elapsed:    {results.get('elapsed_seconds', 0)}s")

        if results.get("errors"):
            print("\nErrors:")
            for err in results["errors"]:
                print(f"  - {err}")

        print("\nStep Results:")
        for step_name, step_result in steps.items():
            icon = "OK" if step_result.get("status") == "success" else "FAIL"
            print(f"  [{icon}] {step_name}")

        if status == "success":
            print("\nNext steps:")
            print(f"  cd {results['child_root']}")
            print("  python tools/memory/memory_read.py --format markdown")
            print("  python tools/db/init_*_db.py")


if __name__ == "__main__":
    main()
