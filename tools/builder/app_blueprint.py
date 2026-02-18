#!/usr/bin/env python3
# CUI // SP-CTI
# Controlled by: Department of Defense
# CUI Category: CTI
# Distribution: D
# POC: ICDEV System Administrator
"""App Blueprint Engine - generates deployment blueprint from fitness scorecard.

Consumes an agentic fitness scorecard (JSON output from tools/builder/agentic_fitness.py)
and user decisions to produce a comprehensive blueprint JSON that drives all downstream
child app generators.

Architecture Decision D23: Blueprint-driven generation -- single config drives all
generators; no hardcoded decisions.

CLI: python tools/builder/app_blueprint.py \
       --fitness-scorecard /path/to/scorecard.json \
       --user-decisions '{"ato_required": true}' \
       --app-name "my-child-app" \
       --json
"""

import argparse
import hashlib
import json
import logging
import sys
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

BASE_DIR = Path(__file__).resolve().parent.parent.parent
DB_PATH = BASE_DIR / "data" / "icdev.db"
CAPABILITY_REGISTRY_PATH = BASE_DIR / "context" / "agentic" / "capability_registry.yaml"
CSP_REGISTRY_PATH = BASE_DIR / "context" / "agentic" / "csp_mcp_registry.yaml"

logger = logging.getLogger("icdev.app_blueprint")

try:
    import yaml
except ImportError:
    yaml = None

try:
    from tools.audit.audit_logger import log_event as audit_log_event
except ImportError:
    def audit_log_event(**kwargs):
        logger.debug("audit_logger unavailable — skipping audit event")


# ============================================================
# DEFAULT REGISTRIES — fallback when YAML files not available
# ============================================================

DEFAULT_CAPABILITY_REGISTRY: Dict[str, Any] = {
    "capabilities": {
        "core": {
            "description": "Core agent framework (orchestration, A2A, audit)",
            "always_on": True,
            "condition": None,
        },
        "compliance": {
            "description": "ATO artifacts, 9-framework compliance engine",
            "always_on": False,
            "condition": "compliance_sensitivity >= 6 OR user_decisions.ato_required",
        },
        "security": {
            "description": "SAST, dependency audit, secret detection, container scan",
            "always_on": False,
            "condition": "overall_score >= 5 OR user_decisions.security_required",
        },
        "mbse": {
            "description": "Model-Based Systems Engineering integration",
            "always_on": False,
            "condition": "user_decisions.mbse_enabled",
        },
        "cicd": {
            "description": "CI/CD pipeline integration (GitHub + GitLab)",
            "always_on": True,
            "condition": None,
        },
        "testing": {
            "description": "Testing framework (unit, BDD, E2E, gates)",
            "always_on": True,
            "condition": None,
        },
        "dashboard": {
            "description": "Flask web dashboard for monitoring and status",
            "always_on": False,
            "condition": "user_interaction >= 4",
        },
        "knowledge": {
            "description": "Self-healing patterns, ML, recommendations",
            "always_on": True,
            "condition": None,
        },
        "modernization": {
            "description": "Legacy app modernization (excluded from child apps)",
            "always_on": False,
            "condition": "never",
        },
        "infra": {
            "description": "Terraform, Ansible, K8s, pipeline generation",
            "always_on": False,
            "condition": "capabilities.cicd",
        },
        "db": {
            "description": "Database initialization and management",
            "always_on": True,
            "condition": None,
        },
        "project": {
            "description": "Project lifecycle management",
            "always_on": True,
            "condition": None,
        },
        "memory": {
            "description": "Memory system (markdown + SQLite + embeddings)",
            "always_on": True,
            "condition": None,
        },
    },
}

DEFAULT_CSP_REGISTRY: Dict[str, Any] = {
    "providers": {
        "aws": {
            "display_name": "Amazon Web Services",
            "govcloud_regions": ["us-gov-west-1", "us-gov-east-1"],
            "commercial_regions": ["us-east-1", "us-west-2", "eu-west-1"],
            "mcp_servers": {
                "core": {
                    "name": "aws-core",
                    "description": "S3, SQS, SNS, CloudWatch",
                    "always_on": True,
                },
                "bedrock": {
                    "name": "aws-bedrock",
                    "description": "Amazon Bedrock LLM inference",
                    "always_on": True,
                },
                "secrets": {
                    "name": "aws-secrets-manager",
                    "description": "AWS Secrets Manager",
                    "always_on": True,
                },
                "compliance": {
                    "name": "aws-config",
                    "description": "AWS Config for compliance monitoring",
                    "requires_capability": "compliance",
                },
                "security": {
                    "name": "aws-security-hub",
                    "description": "AWS Security Hub findings",
                    "requires_capability": "security",
                },
                "container": {
                    "name": "aws-ecr",
                    "description": "Elastic Container Registry",
                    "requires_capability": "cicd",
                },
                "monitoring": {
                    "name": "aws-cloudwatch",
                    "description": "CloudWatch metrics and logs",
                    "requires_capability": "knowledge",
                },
            },
            "knowledge_bases": [
                {"id": "kb-govcloud-patterns", "name": "GovCloud Architecture Patterns"},
                {"id": "kb-nist-controls", "name": "NIST 800-53 Control Implementations"},
            ],
        },
        "gcp": {
            "display_name": "Google Cloud Platform",
            "govcloud_regions": [],
            "commercial_regions": ["us-central1", "us-east1", "europe-west1"],
            "mcp_servers": {
                "core": {
                    "name": "gcp-core",
                    "description": "GCS, Pub/Sub, Cloud Logging",
                    "always_on": True,
                },
                "ai": {
                    "name": "gcp-vertex-ai",
                    "description": "Vertex AI LLM inference",
                    "always_on": True,
                },
                "secrets": {
                    "name": "gcp-secret-manager",
                    "description": "GCP Secret Manager",
                    "always_on": True,
                },
                "security": {
                    "name": "gcp-security-command-center",
                    "description": "Security Command Center",
                    "requires_capability": "security",
                },
                "container": {
                    "name": "gcp-artifact-registry",
                    "description": "Artifact Registry",
                    "requires_capability": "cicd",
                },
            },
            "knowledge_bases": [
                {"id": "kb-gcp-patterns", "name": "GCP Architecture Patterns"},
            ],
        },
        "azure": {
            "display_name": "Microsoft Azure",
            "govcloud_regions": ["usgovvirginia", "usgovarizona"],
            "commercial_regions": ["eastus", "westus2", "westeurope"],
            "mcp_servers": {
                "core": {
                    "name": "azure-core",
                    "description": "Blob Storage, Service Bus, Monitor",
                    "always_on": True,
                },
                "ai": {
                    "name": "azure-openai",
                    "description": "Azure OpenAI Service",
                    "always_on": True,
                },
                "secrets": {
                    "name": "azure-key-vault",
                    "description": "Azure Key Vault",
                    "always_on": True,
                },
                "compliance": {
                    "name": "azure-policy",
                    "description": "Azure Policy for compliance",
                    "requires_capability": "compliance",
                },
                "security": {
                    "name": "azure-defender",
                    "description": "Microsoft Defender for Cloud",
                    "requires_capability": "security",
                },
                "container": {
                    "name": "azure-acr",
                    "description": "Azure Container Registry",
                    "requires_capability": "cicd",
                },
            },
            "knowledge_bases": [
                {"id": "kb-azure-patterns", "name": "Azure Gov Architecture Patterns"},
            ],
        },
        "oracle": {
            "display_name": "Oracle Cloud Infrastructure",
            "govcloud_regions": ["us-langley-1", "us-luke-1"],
            "commercial_regions": ["us-ashburn-1", "us-phoenix-1"],
            "mcp_servers": {
                "core": {
                    "name": "oci-core",
                    "description": "Object Storage, Streaming, Logging",
                    "always_on": True,
                },
                "ai": {
                    "name": "oci-generative-ai",
                    "description": "OCI Generative AI Service",
                    "always_on": True,
                },
                "secrets": {
                    "name": "oci-vault",
                    "description": "OCI Vault",
                    "always_on": True,
                },
                "security": {
                    "name": "oci-cloud-guard",
                    "description": "OCI Cloud Guard",
                    "requires_capability": "security",
                },
            },
            "knowledge_bases": [
                {"id": "kb-oci-patterns", "name": "OCI Gov Architecture Patterns"},
            ],
        },
    },
}


# ============================================================
# CORE / CONDITIONAL AGENT DEFINITIONS
# ============================================================

CORE_AGENTS: List[Dict[str, Any]] = [
    {
        "name": "orchestrator",
        "base_port": 8443,
        "role": "Task routing, workflow management",
    },
    {
        "name": "architect",
        "base_port": 8444,
        "role": "ATLAS A/T phases, system design",
    },
    {
        "name": "builder",
        "base_port": 8445,
        "role": "TDD code gen (RED->GREEN->REFACTOR)",
    },
    {
        "name": "knowledge",
        "base_port": 8449,
        "role": "Self-healing patterns, recommendations",
    },
    {
        "name": "monitor",
        "base_port": 8450,
        "role": "Log analysis, metrics, alerts, health checks",
    },
]

CONDITIONAL_AGENTS: List[Dict[str, Any]] = [
    {
        "name": "compliance",
        "base_port": 8446,
        "role": "ATO artifacts, 9-framework compliance",
        "requires": "compliance",
    },
    {
        "name": "security",
        "base_port": 8447,
        "role": "SAST, dep audit, secret detection",
        "requires": "security",
    },
]

# ============================================================
# ESSENTIAL GOALS (8 core goals for child apps)
# ============================================================

ESSENTIAL_GOALS: List[str] = [
    "build_app",
    "tdd_workflow",
    "compliance_workflow",
    "security_scan",
    "deploy_workflow",
    "monitoring",
    "self_healing",
    "agent_management",
]

# ============================================================
# CAPABILITY -> SOURCE DIRECTORY MAPPING
# ============================================================

CAPABILITY_SOURCES: Dict[str, List[str]] = {
    "core": ["tools/agent", "tools/a2a", "tools/audit"],
    "memory": ["tools/memory"],
    "knowledge": ["tools/knowledge", "tools/monitor"],
    "compliance": ["tools/compliance"],
    "security": ["tools/security"],
    "mbse": ["tools/mbse"],
    "cicd": ["tools/ci"],
    "testing": ["tools/testing"],
    "dashboard": ["tools/dashboard"],
    "infra": ["tools/infra"],
    "db": ["tools/db"],
    "project": ["tools/project"],
}

# Adaptations applied per source directory
DIRECTORY_ADAPTATIONS: Dict[str, List[str]] = {
    "tools/agent": ["port_remap", "db_rename", "app_name_replace"],
    "tools/a2a": ["port_remap", "tls_cert_path"],
    "tools/audit": ["db_rename", "classification_update"],
    "tools/memory": ["db_rename", "app_name_replace"],
    "tools/knowledge": ["db_rename"],
    "tools/monitor": ["endpoint_remap", "app_name_replace"],
    "tools/compliance": ["db_rename", "classification_update", "impact_level_update"],
    "tools/security": ["app_name_replace"],
    "tools/mbse": ["db_rename"],
    "tools/ci": ["bot_identifier_replace", "app_name_replace"],
    "tools/testing": ["app_name_replace"],
    "tools/dashboard": ["port_remap", "db_rename", "app_name_replace"],
    "tools/infra": ["region_replace", "app_name_replace"],
    "tools/db": ["db_rename"],
    "tools/project": ["db_rename", "app_name_replace"],
}


# ============================================================
# HELPER FUNCTIONS
# ============================================================

def _load_yaml(path: Path, default: Dict[str, Any]) -> Dict[str, Any]:
    """Load YAML configuration with fallback to hardcoded defaults.

    Args:
        path: Path to the YAML file.
        default: Default dict to return if YAML unavailable.

    Returns:
        Parsed YAML content or default dict.
    """
    if yaml is None:
        logger.debug("PyYAML not installed — using defaults for %s", path.name)
        return default.copy()

    if not path.exists():
        logger.debug("YAML file not found: %s — using defaults", path)
        return default.copy()

    try:
        with open(path, encoding="utf-8") as f:
            data = yaml.safe_load(f)
        if not data or not isinstance(data, dict):
            logger.warning("Empty or invalid YAML in %s — using defaults", path)
            return default.copy()
        logger.info("Loaded configuration from %s", path)
        return data
    except Exception as e:
        logger.warning("Failed to load %s: %s — using defaults", path, e)
        return default.copy()


def _compute_blueprint_hash(blueprint: Dict[str, Any]) -> str:
    """Compute SHA-256 hash of the blueprint for integrity verification.

    Excludes the hash field itself and timestamps from the computation
    to ensure deterministic hashing.

    Args:
        blueprint: Blueprint dict (blueprint_hash field is excluded).

    Returns:
        Hex-encoded SHA-256 hash string.
    """
    hashable = {k: v for k, v in blueprint.items()
                if k not in ("blueprint_hash", "generated_at")}
    serialized = json.dumps(hashable, sort_keys=True, default=str)
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


def _safe_get_score(scorecard: Dict[str, Any], dimension: str, default: int = 0) -> int:
    """Safely extract a dimension score from the fitness scorecard.

    Args:
        scorecard: Fitness scorecard dict.
        dimension: Name of the scoring dimension.
        default: Default value if dimension not found.

    Returns:
        Integer score value.
    """
    scores = scorecard.get("scores", {})
    value = scores.get(dimension, default)
    try:
        return int(value)
    except (TypeError, ValueError):
        logger.warning("Non-integer score for %s: %s — using default %d",
                       dimension, value, default)
        return default


# ============================================================
# CORE FUNCTIONS
# ============================================================

def resolve_capabilities(
    scorecard: Dict[str, Any],
    user_decisions: Dict[str, Any],
) -> Dict[str, bool]:
    """Resolve which capabilities are enabled for the child app.

    Reads context/agentic/capability_registry.yaml (or uses defaults)
    and maps scorecard dimensions + user decisions to a capability map.

    Rules:
        - core: Always on
        - compliance: When compliance_sensitivity >= 6 OR user_decisions.ato_required
        - security: When overall_score >= 5 OR user_decisions.security_required
        - mbse: When user_decisions.mbse_enabled
        - cicd: Always on
        - testing: Always on
        - dashboard: When user_interaction >= 4
        - knowledge: Always on
        - modernization: Never (excluded from child apps)
        - infra: On when cicd is on
        - db: Always on
        - project: Always on
        - memory: Always on

    Args:
        scorecard: Fitness scorecard from agentic_fitness.py.
        user_decisions: User-provided decision overrides.

    Returns:
        Dict mapping capability name to boolean enabled status.
    """
    registry = _load_yaml(CAPABILITY_REGISTRY_PATH, DEFAULT_CAPABILITY_REGISTRY)
    caps_registry = registry.get("capabilities", DEFAULT_CAPABILITY_REGISTRY["capabilities"])

    overall_score = scorecard.get("overall_score", 0.0)
    compliance_score = _safe_get_score(scorecard, "compliance_sensitivity")
    interaction_score = _safe_get_score(scorecard, "user_interaction")

    capabilities: Dict[str, bool] = {}

    for cap_name, cap_def in caps_registry.items():
        if cap_def.get("always_on", False):
            capabilities[cap_name] = True
            continue

        condition = cap_def.get("condition", "")

        if condition == "never":
            capabilities[cap_name] = False
            continue

        # Evaluate conditions based on scorecard and user decisions
        enabled = False

        if cap_name == "compliance":
            enabled = (
                compliance_score >= 6
                or user_decisions.get("ato_required", False)
            )
        elif cap_name == "security":
            enabled = (
                overall_score >= 5
                or user_decisions.get("security_required", False)
            )
        elif cap_name == "mbse":
            enabled = user_decisions.get("mbse_enabled", False)
        elif cap_name == "dashboard":
            enabled = interaction_score >= 4
        elif cap_name == "infra":
            # Infra is on whenever cicd is on (cicd is always on)
            enabled = True
        elif cap_name == "modernization":
            enabled = False
        else:
            # Unknown capabilities default to off unless always_on
            enabled = False

        capabilities[cap_name] = enabled

    # Apply explicit user overrides — user can force capabilities on/off
    explicit_overrides = user_decisions.get("capabilities_override", {})
    for cap_name, override_value in explicit_overrides.items():
        if cap_name in capabilities:
            previous = capabilities[cap_name]
            capabilities[cap_name] = bool(override_value)
            if previous != capabilities[cap_name]:
                logger.info("User override: %s %s -> %s",
                            cap_name, previous, capabilities[cap_name])

    # Modernization is NEVER enabled in child apps regardless of overrides
    capabilities["modernization"] = False

    logger.info("Resolved capabilities: %s",
                {k: v for k, v in capabilities.items() if v})
    return capabilities


def build_agent_roster(
    capabilities: Dict[str, bool],
    port_offset: int = 1000,
) -> List[Dict[str, Any]]:
    """Build the list of agent specifications for the child app.

    5 core agents are always included. Conditional agents are added
    based on enabled capabilities.

    Each agent spec includes: name, port, role, health_endpoint, agent_card_path.

    Args:
        capabilities: Resolved capability map from resolve_capabilities().
        port_offset: Port offset from ICDEV base ports (default 1000).

    Returns:
        List of agent specification dicts.
    """
    roster: List[Dict[str, Any]] = []

    # Always include core agents
    for agent_def in CORE_AGENTS:
        port = agent_def["base_port"] + port_offset
        roster.append({
            "name": agent_def["name"],
            "port": port,
            "role": agent_def["role"],
            "health_endpoint": f"https://localhost:{port}/health",
            "agent_card_path": "/.well-known/agent.json",
            "core": True,
        })

    # Conditionally include domain agents
    for agent_def in CONDITIONAL_AGENTS:
        required_cap = agent_def.get("requires", "")
        if capabilities.get(required_cap, False):
            port = agent_def["base_port"] + port_offset
            roster.append({
                "name": agent_def["name"],
                "port": port,
                "role": agent_def["role"],
                "health_endpoint": f"https://localhost:{port}/health",
                "agent_card_path": "/.well-known/agent.json",
                "core": False,
            })
            logger.debug("Added conditional agent: %s (port %d)", agent_def["name"], port)
        else:
            logger.debug("Skipped agent %s — capability '%s' not enabled",
                         agent_def["name"], required_cap)

    logger.info("Agent roster: %d agents (%d core, %d conditional)",
                len(roster),
                sum(1 for a in roster if a.get("core")),
                sum(1 for a in roster if not a.get("core")))
    return roster


def build_file_manifest(blueprint: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Build the file manifest describing what to copy and adapt for the child app.

    Does NOT scan the filesystem -- records directory patterns and adaptation
    rules for the child_app_generator (Phase 3) to handle actual file copying.

    Each manifest entry contains:
        - source: Relative source directory or file pattern in ICDEV
        - dest: Relative destination in child app
        - adaptations: List of adaptation types to apply

    Args:
        blueprint: Partial blueprint dict with capabilities resolved.

    Returns:
        List of manifest entry dicts.
    """
    capabilities = blueprint.get("capabilities", {})
    app_name = blueprint.get("app_name", "child-app")
    manifest: List[Dict[str, Any]] = []

    # Always-included directories
    always_include = ["core", "memory", "knowledge", "db", "project"]

    # Conditionally included based on capabilities
    conditional = ["compliance", "security", "mbse", "cicd", "testing",
                    "dashboard", "infra"]

    included_caps = always_include.copy()
    for cap in conditional:
        if capabilities.get(cap, False):
            included_caps.append(cap)

    for cap_name in included_caps:
        source_dirs = CAPABILITY_SOURCES.get(cap_name, [])
        for source_dir in source_dirs:
            adaptations = DIRECTORY_ADAPTATIONS.get(source_dir, ["app_name_replace"])
            manifest.append({
                "source": source_dir,
                "dest": source_dir,
                "capability": cap_name,
                "adaptations": adaptations,
            })

    # Always include top-level config files
    config_files = [
        {
            "source": "args/project_defaults.yaml",
            "dest": "args/project_defaults.yaml",
            "capability": "core",
            "adaptations": ["app_name_replace", "port_remap"],
        },
        {
            "source": "args/agent_config.yaml",
            "dest": "args/agent_config.yaml",
            "capability": "core",
            "adaptations": ["port_remap", "agent_filter"],
        },
        {
            "source": "args/monitoring_config.yaml",
            "dest": "args/monitoring_config.yaml",
            "capability": "knowledge",
            "adaptations": ["endpoint_remap", "app_name_replace"],
        },
    ]

    # Conditionally include compliance/security config files
    if capabilities.get("compliance", False):
        config_files.extend([
            {
                "source": "args/cui_markings.yaml",
                "dest": "args/cui_markings.yaml",
                "capability": "compliance",
                "adaptations": ["classification_update"],
            },
            {
                "source": "args/security_gates.yaml",
                "dest": "args/security_gates.yaml",
                "capability": "compliance",
                "adaptations": ["threshold_adjust"],
            },
        ])

    manifest.extend(config_files)

    # Goals directory — include essential goals that map to enabled capabilities
    goals_to_include = _resolve_goals_for_capabilities(capabilities)
    for goal_name in goals_to_include:
        manifest.append({
            "source": f"goals/{goal_name}.md",
            "dest": f"goals/{goal_name}.md",
            "capability": "core",
            "adaptations": ["app_name_replace"],
        })

    # Always include goals/manifest.md
    manifest.append({
        "source": "goals/manifest.md",
        "dest": "goals/manifest.md",
        "capability": "core",
        "adaptations": ["goal_filter", "app_name_replace"],
    })

    # Context files
    manifest.append({
        "source": "context/",
        "dest": "context/",
        "capability": "core",
        "adaptations": ["selective_copy"],
    })

    # Hard prompts
    manifest.append({
        "source": "hardprompts/",
        "dest": "hardprompts/",
        "capability": "core",
        "adaptations": ["selective_copy"],
    })

    logger.info("File manifest: %d entries for %d capabilities",
                len(manifest), len(included_caps))
    return manifest


def _resolve_goals_for_capabilities(
    capabilities: Dict[str, bool],
) -> List[str]:
    """Determine which essential goals to include based on capabilities.

    Args:
        capabilities: Resolved capability map.

    Returns:
        List of goal file names (without extension).
    """
    # Mapping from goal to required capability (None = always include)
    goal_capability_map: Dict[str, Optional[str]] = {
        "build_app": None,
        "tdd_workflow": None,
        "compliance_workflow": "compliance",
        "security_scan": "security",
        "deploy_workflow": "cicd",
        "monitoring": None,
        "self_healing": None,
        "agent_management": None,
    }

    goals: List[str] = []
    for goal_name, required_cap in goal_capability_map.items():
        if required_cap is None or capabilities.get(required_cap, False):
            goals.append(goal_name)

    return goals


def resolve_csp_mcp_servers(
    cloud_config: Dict[str, Any],
    capabilities: Dict[str, bool],
) -> List[Dict[str, Any]]:
    """Resolve which CSP MCP servers to include based on cloud provider and capabilities.

    Reads context/agentic/csp_mcp_registry.yaml (or uses defaults) and selects
    servers based on the target cloud provider and enabled capabilities.

    Args:
        cloud_config: Cloud provider configuration from the blueprint.
        capabilities: Resolved capability map.

    Returns:
        List of MCP server config dicts for .mcp.json generation.
    """
    registry = _load_yaml(CSP_REGISTRY_PATH, DEFAULT_CSP_REGISTRY)
    providers = registry.get("providers", DEFAULT_CSP_REGISTRY["providers"])

    provider_name = cloud_config.get("provider", "aws")
    provider_def = providers.get(provider_name)

    if not provider_def:
        logger.warning("Unknown cloud provider '%s' — falling back to aws", provider_name)
        provider_def = providers.get("aws", {})
        provider_name = "aws"

    mcp_servers_def = provider_def.get("mcp_servers", {})
    capability_mapping = registry.get("capability_mapping", {})
    selected_servers: List[Dict[str, Any]] = []
    included_categories: set = set()

    # Determine which server categories to include based on capabilities
    # Always include "core" and "docs" categories
    included_categories.add("core")
    included_categories.add("docs")

    for cap_name, cap_enabled in capabilities.items():
        if not cap_enabled:
            continue
        cap_map = capability_mapping.get(cap_name, {})
        provider_categories = cap_map.get(provider_name, [])
        for cat in provider_categories:
            included_categories.add(cat)

    # Iterate through server categories and collect matching servers
    for category, server_list in mcp_servers_def.items():
        if category not in included_categories:
            continue
        # Each category maps to a list of server defs
        if not isinstance(server_list, list):
            server_list = [server_list]
        for server_def in server_list:
            if isinstance(server_def, dict):
                server_name = server_def.get("name", category)
                description = server_def.get("description", "")
            else:
                server_name = str(server_def)
                description = ""
            selected_servers.append({
                "name": server_name,
                "description": description,
                "provider": provider_name,
                "category": category,
            })

    logger.info("CSP MCP servers for %s: %d selected from %d categories",
                provider_name,
                len(selected_servers),
                len(included_categories))
    return selected_servers


# ============================================================
# MAIN ORCHESTRATOR
# ============================================================

def generate_blueprint(
    scorecard: Dict[str, Any],
    user_decisions: Dict[str, Any],
    app_name: str,
    port_offset: int = 1000,
    cloud_provider: str = "aws",
    cloud_region: str = "us-gov-west-1",
    govcloud: bool = False,
    parent_callback_url: Optional[str] = None,
    impact_level: str = "IL4",
) -> Dict[str, Any]:
    """Generate a complete deployment blueprint from fitness scorecard and user decisions.

    This is the main entry point that orchestrates all sub-functions to produce
    a comprehensive blueprint JSON for downstream child app generators.

    Args:
        scorecard: Fitness scorecard from agentic_fitness.py (JSON dict).
        user_decisions: User-provided decisions and overrides.
        app_name: Name for the child application.
        port_offset: Port offset from ICDEV base ports (default 1000).
        cloud_provider: Target cloud provider (aws, gcp, azure, oracle).
        cloud_region: Target deployment region.
        govcloud: Whether to use GovCloud partition.
        parent_callback_url: Optional URL for parent ICDEV callback.
        impact_level: DoD Impact Level (IL2, IL4, IL5, IL6).

    Returns:
        Complete blueprint dict ready for serialization and downstream consumption.
    """
    blueprint_id = str(uuid.uuid4())
    logger.info("Generating blueprint %s for app '%s'", blueprint_id, app_name)

    # Step 1: Resolve capabilities from scorecard + user decisions
    capabilities = resolve_capabilities(scorecard, user_decisions)

    # Step 2: Build cloud provider configuration
    csp_registry = _load_yaml(CSP_REGISTRY_PATH, DEFAULT_CSP_REGISTRY)
    provider_data = csp_registry.get("providers", {}).get(cloud_provider, {})
    knowledge_bases = provider_data.get("knowledge_bases", [])

    cloud_config = {
        "provider": cloud_provider,
        "region": cloud_region,
        "govcloud": govcloud,
        "knowledge_bases": knowledge_bases,
        "mcp_servers": [],  # Populated below
    }

    # Step 3: Resolve CSP MCP servers
    csp_servers = resolve_csp_mcp_servers(cloud_config, capabilities)
    cloud_config["mcp_servers"] = [s["name"] for s in csp_servers]

    # Step 4: Build agent roster
    agents = build_agent_roster(capabilities, port_offset)

    # Step 5: Determine classification from impact level
    classification_map = {
        "IL2": "PUBLIC",
        "IL4": "CUI",
        "IL5": "CUI",
        "IL6": "SECRET",
    }
    classification = classification_map.get(impact_level, "CUI")

    # Step 6: Build DB, memory, and CI/CD configs
    db_config = {
        "engine": "sqlite",
        "name": f"{app_name}.db",
        "path": f"data/{app_name}.db",
        "initial_tables": "minimal",
        "migration_supported": True,
    }

    memory_config = {
        "memory_md": True,
        "daily_logs": True,
        "sqlite_db": True,
        "semantic_search": True,
        "embeddings": True,
    }

    cicd_config = {
        "github": True,
        "gitlab": True,
        "webhooks": True,
        "polling": True,
        "slash_commands": True,
        "bot_identifier": f"[{app_name.upper()}-BOT]",
    }

    # Step 7: Resolve goals
    goals_config = _resolve_goals_for_capabilities(capabilities)

    # Step 8: Build parent callback config
    parent_callback = {
        "enabled": parent_callback_url is not None,
        "url": parent_callback_url or "",
        "auth": "bearer_token" if parent_callback_url else "none",
    }

    # Step 9: ATLAS config — fitness step is disabled in child apps
    atlas_config = {
        "fitness_step": False,
        "model_phase": capabilities.get("mbse", False),
        "phases": ["architect", "trace", "link", "assemble", "stress_test"],
    }
    if atlas_config["model_phase"]:
        atlas_config["phases"].insert(0, "model")

    # Step 10: Grandchild prevention — prevents recursive child app generation
    grandchild_prevention = {
        "enabled": True,
        "config_flag": True,
        "scaffolder_strip": True,
        "claude_md_doc": True,
        "description": (
            "Child apps MUST NOT generate their own child apps. "
            "The agentic fitness assessor and app blueprint engine are "
            "stripped from child app scaffolds. CLAUDE.md documents this restriction."
        ),
    }

    # Assemble the blueprint (without hash — hash computed after assembly)
    blueprint: Dict[str, Any] = {
        "blueprint_id": blueprint_id,
        "app_name": app_name,
        "classification": classification,
        "impact_level": impact_level,
        "fitness_scorecard": {
            "component": scorecard.get("component", "unknown"),
            "overall_score": scorecard.get("overall_score", 0.0),
            "scores": scorecard.get("scores", {}),
            "architecture": scorecard.get("recommendations", {}).get(
                "architecture", "traditional"
            ),
        },
        "capabilities": capabilities,
        "agents": agents,
        "cloud_provider": cloud_config,
        "csp_mcp_servers": csp_servers,
        "db_config": db_config,
        "memory_config": memory_config,
        "cicd_config": cicd_config,
        "goals_config": goals_config,
        "parent_callback": parent_callback,
        "atlas_config": atlas_config,
        "grandchild_prevention": grandchild_prevention,
        "file_manifest": [],  # Populated below
        "generated_at": datetime.now(tz=__import__('datetime').timezone.utc).isoformat(),
        "generated_by": "icdev/app_blueprint",
        "blueprint_hash": "",  # Computed below
    }

    # Step 11: Build file manifest (needs partial blueprint for capability reference)
    blueprint["file_manifest"] = build_file_manifest(blueprint)

    # Step 12: Compute integrity hash
    blueprint["blueprint_hash"] = _compute_blueprint_hash(blueprint)

    # Step 13: Audit trail
    _log_blueprint_audit(blueprint)

    logger.info(
        "Blueprint %s generated: %d capabilities, %d agents, %d manifest entries, hash=%s",
        blueprint_id,
        sum(1 for v in capabilities.values() if v),
        len(agents),
        len(blueprint["file_manifest"]),
        blueprint["blueprint_hash"][:16] + "...",
    )

    return blueprint


def _log_blueprint_audit(blueprint: Dict[str, Any]) -> None:
    """Log blueprint generation to audit trail.

    Args:
        blueprint: Generated blueprint dict.
    """
    try:
        audit_log_event(
            event_type="blueprint.generated",
            actor="builder/app_blueprint",
            action=f"Generated blueprint for '{blueprint.get('app_name', 'unknown')}'",
            project_id=blueprint.get("blueprint_id", ""),
            details=json.dumps({
                "blueprint_id": blueprint.get("blueprint_id"),
                "app_name": blueprint.get("app_name"),
                "impact_level": blueprint.get("impact_level"),
                "capabilities_enabled": sum(
                    1 for v in blueprint.get("capabilities", {}).values() if v
                ),
                "agent_count": len(blueprint.get("agents", [])),
                "manifest_entries": len(blueprint.get("file_manifest", [])),
                "cloud_provider": blueprint.get("cloud_provider", {}).get("provider"),
                "blueprint_hash": blueprint.get("blueprint_hash", "")[:32],
            }),
        )
    except Exception as e:
        logger.debug("Audit log failed: %s", e)


def _persist_blueprint(blueprint: Dict[str, Any]) -> bool:
    """Persist blueprint to the ICDEV database.

    Args:
        blueprint: Generated blueprint dict.

    Returns:
        True if persisted successfully, False otherwise.
    """
    if not DB_PATH.exists():
        logger.debug("Database not found at %s — skipping persistence", DB_PATH)
        return False

    try:
        import sqlite3
        conn = sqlite3.connect(str(DB_PATH))
        conn.execute(
            """INSERT OR REPLACE INTO app_blueprints
               (id, app_name, classification, impact_level, capabilities,
                agents, cloud_provider, blueprint_hash, generated_at, full_blueprint)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                blueprint.get("blueprint_id"),
                blueprint.get("app_name"),
                blueprint.get("classification"),
                blueprint.get("impact_level"),
                json.dumps(blueprint.get("capabilities", {})),
                json.dumps(blueprint.get("agents", [])),
                blueprint.get("cloud_provider", {}).get("provider", "aws"),
                blueprint.get("blueprint_hash", ""),
                blueprint.get("generated_at", ""),
                json.dumps(blueprint, default=str),
            ),
        )
        conn.commit()
        conn.close()
        logger.info("Blueprint %s persisted to database", blueprint.get("blueprint_id"))
        return True
    except Exception as e:
        logger.warning("Blueprint DB persistence failed: %s", e)
        return False


def _load_scorecard_file(path: str) -> Dict[str, Any]:
    """Load a fitness scorecard from a JSON file.

    Args:
        path: Path to the scorecard JSON file.

    Returns:
        Parsed scorecard dict.

    Raises:
        FileNotFoundError: If the file does not exist.
        json.JSONDecodeError: If the file is not valid JSON.
    """
    scorecard_path = Path(path)
    if not scorecard_path.exists():
        raise FileNotFoundError(f"Fitness scorecard not found: {path}")

    with open(scorecard_path, encoding="utf-8") as f:
        data = json.load(f)

    if not isinstance(data, dict):
        raise ValueError(f"Scorecard must be a JSON object, got {type(data).__name__}")

    # Validate minimal required fields
    if "scores" not in data and "overall_score" not in data:
        logger.warning("Scorecard missing 'scores' and 'overall_score' fields")

    return data


def _parse_user_decisions(raw: str) -> Dict[str, Any]:
    """Parse user decisions from a JSON string or file path.

    Args:
        raw: JSON string or path to a JSON file.

    Returns:
        Parsed user decisions dict.

    Raises:
        ValueError: If the input cannot be parsed as JSON.
    """
    # Try as file path first
    path = Path(raw)
    if path.exists() and path.is_file():
        try:
            with open(path, encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, dict):
                return data
        except (json.JSONDecodeError, OSError):
            pass  # Fall through to try as raw JSON string

    # Try as raw JSON string
    try:
        data = json.loads(raw)
        if isinstance(data, dict):
            return data
        raise ValueError(f"User decisions must be a JSON object, got {type(data).__name__}")
    except json.JSONDecodeError as e:
        raise ValueError(f"Failed to parse user decisions as JSON: {e}") from e


# ============================================================
# CLI ENTRY POINT
# ============================================================

def main():
    """CLI entry point for the App Blueprint Engine."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    )

    parser = argparse.ArgumentParser(
        description="App Blueprint Engine - generate deployment blueprint from fitness scorecard",
    )
    parser.add_argument(
        "--fitness-scorecard",
        required=True,
        help="Path to fitness scorecard JSON file (output of agentic_fitness.py)",
    )
    parser.add_argument(
        "--user-decisions",
        required=True,
        help='User decisions as JSON string or path to JSON file '
             '(e.g., \'{"ato_required": true, "mbse_enabled": false}\')',
    )
    parser.add_argument(
        "--app-name",
        required=True,
        help="Name for the child application",
    )
    parser.add_argument(
        "--port-offset",
        type=int,
        default=1000,
        help="Port offset from ICDEV base ports (default: 1000)",
    )
    parser.add_argument(
        "--cloud-provider",
        choices=["aws", "gcp", "azure", "oracle"],
        default="aws",
        help="Target cloud service provider (default: aws)",
    )
    parser.add_argument(
        "--cloud-region",
        default="us-gov-west-1",
        help="Target deployment region (default: us-gov-west-1)",
    )
    parser.add_argument(
        "--govcloud",
        action="store_true",
        help="Use GovCloud partition",
    )
    parser.add_argument(
        "--parent-callback-url",
        default=None,
        help="URL for parent ICDEV callback (optional)",
    )
    parser.add_argument(
        "--impact-level",
        choices=["IL2", "IL4", "IL5", "IL6"],
        default="IL4",
        help="DoD Impact Level (default: IL4)",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        dest="json_output",
        help="Output blueprint as JSON",
    )
    parser.add_argument(
        "--output",
        default=None,
        help="Write blueprint JSON to file path",
    )
    parser.add_argument(
        "--persist",
        action="store_true",
        help="Persist blueprint to ICDEV database",
    )
    parser.add_argument(
        "--verbose", "-v",
        action="store_true",
        help="Enable debug logging",
    )

    args = parser.parse_args()

    if args.verbose:
        logging.getLogger().setLevel(logging.DEBUG)

    # Load inputs
    try:
        scorecard = _load_scorecard_file(args.fitness_scorecard)
    except (FileNotFoundError, json.JSONDecodeError, ValueError) as e:
        logger.error("Failed to load fitness scorecard: %s", e)
        sys.exit(1)

    try:
        user_decisions = _parse_user_decisions(args.user_decisions)
    except ValueError as e:
        logger.error("Failed to parse user decisions: %s", e)
        sys.exit(1)

    # Generate blueprint
    blueprint = generate_blueprint(
        scorecard=scorecard,
        user_decisions=user_decisions,
        app_name=args.app_name,
        port_offset=args.port_offset,
        cloud_provider=args.cloud_provider,
        cloud_region=args.cloud_region,
        govcloud=args.govcloud,
        parent_callback_url=args.parent_callback_url,
        impact_level=args.impact_level,
    )

    # Persist to DB if requested
    if args.persist:
        success = _persist_blueprint(blueprint)
        if not success:
            logger.warning("Blueprint persistence requested but failed")

    # Output
    if args.json_output or args.output:
        output_json = json.dumps(blueprint, indent=2, default=str)

        if args.output:
            output_path = Path(args.output)
            output_path.parent.mkdir(parents=True, exist_ok=True)
            output_path.write_text(output_json, encoding="utf-8")
            logger.info("Blueprint written to %s", args.output)

        if args.json_output:
            print(output_json)
    else:
        # Human-readable summary
        _print_blueprint_summary(blueprint)


def _print_blueprint_summary(blueprint: Dict[str, Any]) -> None:
    """Print a human-readable summary of the blueprint.

    Args:
        blueprint: Generated blueprint dict.
    """
    caps = blueprint.get("capabilities", {})
    agents = blueprint.get("agents", [])
    manifest = blueprint.get("file_manifest", [])
    cloud = blueprint.get("cloud_provider", {})
    scorecard = blueprint.get("fitness_scorecard", {})

    print(f"\n{'='*70}")
    print(f"  APP BLUEPRINT: {blueprint.get('app_name', 'unknown')}")
    print(f"{'='*70}")
    print(f"  Blueprint ID:    {blueprint.get('blueprint_id', 'N/A')}")
    print(f"  Classification:  {blueprint.get('classification', 'N/A')}")
    print(f"  Impact Level:    {blueprint.get('impact_level', 'N/A')}")
    print(f"  Architecture:    {scorecard.get('architecture', 'N/A').upper()}")
    print(f"  Overall Score:   {scorecard.get('overall_score', 0.0):.2f} / 10.0")
    print(f"  Hash:            {blueprint.get('blueprint_hash', 'N/A')[:32]}...")
    print(f"{'='*70}")

    print(f"\n  Capabilities ({sum(1 for v in caps.values() if v)} enabled):")
    for cap_name, enabled in sorted(caps.items()):
        status = "[ON] " if enabled else "[OFF]"
        print(f"    {status} {cap_name}")

    print(f"\n  Agents ({len(agents)}):")
    for agent in agents:
        core_tag = " (core)" if agent.get("core") else ""
        print(f"    - {agent['name']:<15s} port {agent['port']}{core_tag}")

    print("\n  Cloud Provider:")
    print(f"    Provider:  {cloud.get('provider', 'N/A')}")
    print(f"    Region:    {cloud.get('region', 'N/A')}")
    print(f"    GovCloud:  {cloud.get('govcloud', False)}")
    mcp_names = cloud.get("mcp_servers", [])
    if mcp_names:
        print(f"    MCP Servers: {', '.join(mcp_names)}")

    print(f"\n  Goals ({len(blueprint.get('goals_config', []))}):")
    for goal in blueprint.get("goals_config", []):
        print(f"    - {goal}")

    print(f"\n  File Manifest: {len(manifest)} entries")
    print(f"  Grandchild Prevention: "
          f"{'ENABLED' if blueprint.get('grandchild_prevention', {}).get('enabled') else 'DISABLED'}")
    print(f"  Parent Callback: "
          f"{'ENABLED' if blueprint.get('parent_callback', {}).get('enabled') else 'DISABLED'}")
    print(f"\n  Generated: {blueprint.get('generated_at', 'N/A')}")
    print(f"{'='*70}\n")


if __name__ == "__main__":
    main()
