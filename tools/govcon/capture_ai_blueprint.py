#!/usr/bin/env python3
# CUI // SP-CTI
# Controlled by: Department of Defense
# CUI Category: CTI
# Distribution: D
# POC: ICDEV™ System Administrator
"""CaptureAI Child App Blueprint Generator — Architecture specification for
AI-powered Capture Management (Shipley Phases 0-2).

Generates a child app blueprint JSON compatible with ``child_app_generator.py``
that defines the CaptureAI application: an AI-driven capture management tool
for government contracting aligned to the Shipley business development lifecycle.

Architecture Decisions:
    D53  — Port offset +1000 for child agents (9443-9458).
    D47  — Blueprint-driven generation, single config drives all generators.
    D52  — Grandchild prevention (CaptureAI cannot generate child apps).
    D6   — Append-only audit trail (NIST AU compliance).

CLI:
    python tools/govcon/capture_ai_blueprint.py --generate --json
    python tools/govcon/capture_ai_blueprint.py --generate --deployment-mode standalone --team-size 5 --json
    python tools/govcon/capture_ai_blueprint.py --interop-schema --json
    python tools/govcon/capture_ai_blueprint.py --estimate-resources --deployment-mode integrated --team-size 10 --json
    python tools/govcon/capture_ai_blueprint.py --validate --blueprint-file /path/to/bp.json --json
    python tools/govcon/capture_ai_blueprint.py --export --json
"""

from __future__ import annotations
from tools.logging.icdev_logger import get_logger

import argparse
import hashlib
import json
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

# ---------------------------------------------------------------------------
# Path bootstrapping
# ---------------------------------------------------------------------------
_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from tools.db.storage import get_connection  # noqa: E402, F401

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
logger = get_logger("icdev.capture_ai_blueprint")

BLUEPRINT_DIR = _ROOT / "data" / "proposal_genesis" / "blueprints"

# Deployment modes
DEPLOYMENT_MODES = ("standalone", "integrated", "embedded")

# Valid compliance frameworks that CaptureAI can target
SUPPORTED_FRAMEWORKS = (
    "fedramp_moderate",
    "fedramp_high",
    "cmmc_l2",
    "cmmc_l3",
    "nist_800_53",
    "nist_800_171",
    "cjis",
    "hipaa",
    "itar",
)

# Parent ICDEV™ agent port range — child must NOT conflict
PARENT_PORT_RANGE = range(8443, 8459)

# Child agent port offset (D53)
CHILD_PORT_OFFSET = 1000

# ---------------------------------------------------------------------------
# Audit helper (graceful fallback)
# ---------------------------------------------------------------------------
try:
    from tools.audit.audit_logger import log_event as audit_log_event  # noqa: E402
except ImportError:

    def audit_log_event(**kwargs: Any) -> None:  # type: ignore[misc]
        logger.debug("audit_logger unavailable — skipping audit event: %s", kwargs.get("action", ""))


# ============================================================
# Module definitions
# ============================================================

CORE_MODULES: List[Dict[str, Any]] = [
    {
        "name": "pipeline_manager",
        "description": "Multi-source opportunity aggregation (SAM.gov, SLED, vehicles, FBO archive)",
        "always_on": True,
        "db_tables": ["capture_opportunities", "pipeline_analytics"],
        "dependencies": [],
    },
    {
        "name": "pwin_scorer",
        "description": "Configurable weighted Pwin/Pgo scoring with Bayesian calibration from win/loss history",
        "always_on": True,
        "db_tables": ["pwin_assessments", "pwin_calibration_log"],
        "dependencies": ["pipeline_manager"],
    },
    {
        "name": "competitive_intel",
        "description": "Competitor profiling, FPDS award analysis, talent velocity tracking, incumbency detection",
        "always_on": True,
        "db_tables": ["competitor_profiles", "competitor_awards", "talent_signals"],
        "dependencies": ["pipeline_manager"],
    },
    {
        "name": "win_strategy",
        "description": "Win theme registry, discriminator library, ghost team strategies, solution shaping",
        "always_on": True,
        "db_tables": ["win_themes", "discriminators", "ghost_strategies"],
        "dependencies": ["competitive_intel", "pwin_scorer"],
    },
    {
        "name": "teaming_engine",
        "description": "Data-driven partner matching, OCI screening, workshare allocation, past-performance aggregation",
        "always_on": True,
        "db_tables": ["teaming_partners", "workshare_allocations", "oci_screenings"],
        "dependencies": ["competitive_intel"],
    },
    {
        "name": "customer_crm",
        "description": "Relationship tracking, engagement scoring, shaping activity history, call-plan management",
        "always_on": True,
        "db_tables": ["customer_contacts", "customer_engagements", "shaping_activities"],
        "dependencies": [],
    },
    {
        "name": "capture_planner",
        "description": "Structured capture plans aligned to Shipley Phase 2, milestone tracking, action items",
        "always_on": True,
        "db_tables": ["capture_plans", "capture_milestones", "capture_action_items"],
        "dependencies": ["pipeline_manager", "win_strategy", "teaming_engine", "customer_crm"],
    },
    {
        "name": "gate_manager",
        "description": "Formal bid/no-bid decision workflow with configurable Pwin threshold and approval chain",
        "always_on": True,
        "db_tables": ["gate_decisions", "gate_criteria", "gate_approvals"],
        "dependencies": ["pwin_scorer", "capture_planner"],
    },
    {
        "name": "process_adapter",
        "description": "Lightweight Shipley process adaptation for team size (scales formality for 3-person vs 50-person teams)",
        "always_on": True,
        "db_tables": [],
        "dependencies": [],
    },
]

OPTIONAL_MODULES: List[Dict[str, Any]] = [
    {
        "name": "compliance_tracker",
        "description": "Compliance requirement extraction and flow-down tracking per opportunity",
        "condition": "compliance_frameworks specified",
        "db_tables": ["capture_compliance_items"],
        "dependencies": ["pipeline_manager"],
    },
    {
        "name": "cost_estimator",
        "description": "ROM cost estimation based on historical contract data and LCAT rates",
        "condition": "deployment_mode != embedded",
        "db_tables": ["capture_cost_estimates"],
        "dependencies": ["pipeline_manager", "teaming_engine"],
    },
]


# ============================================================
# Agent definitions
# ============================================================

CORE_AGENTS: List[Dict[str, Any]] = [
    {
        "name": "orchestrator",
        "base_port": 8443,
        "role": "Task routing, workflow management, capture pipeline orchestration",
        "tier": "core",
    },
    {
        "name": "architect",
        "base_port": 8444,
        "role": "Capture strategy design, solution architecture, technical approach",
        "tier": "core",
    },
    {
        "name": "builder",
        "base_port": 8445,
        "role": "Plan generation, template creation, document assembly",
        "tier": "core",
    },
    {
        "name": "knowledge",
        "base_port": 8449,
        "role": "Pattern detection, competitive learning, win/loss analytics",
        "tier": "support",
    },
    {
        "name": "monitor",
        "base_port": 8450,
        "role": "Pipeline health monitoring, deadline alerts, engagement tracking",
        "tier": "support",
    },
]

CONDITIONAL_AGENTS: List[Dict[str, Any]] = [
    {
        "name": "compliance",
        "base_port": 8446,
        "role": "Compliance requirement extraction, flow-down verification, framework alignment",
        "tier": "domain",
        "condition": "compliance_frameworks",
    },
    {
        "name": "security",
        "base_port": 8447,
        "role": "SAST, dependency audit, secret detection for capture artifacts",
        "tier": "domain",
        "condition": "impact_level_gte_il4",
    },
]

# ============================================================
# DB table definitions (capture-specific)
# ============================================================

CAPTURE_DB_TABLES: List[Dict[str, Any]] = [
    {
        "name": "capture_opportunities",
        "description": "Master opportunity pipeline from all sources",
        "append_only": False,
        "columns": [
            "id TEXT PRIMARY KEY",
            "source TEXT NOT NULL",
            "title TEXT NOT NULL",
            "agency TEXT",
            "naics_code TEXT",
            "set_aside TEXT",
            "response_date TEXT",
            "estimated_value_low REAL",
            "estimated_value_high REAL",
            "status TEXT DEFAULT 'identified'",
            "pwin_current REAL",
            "pgo_current REAL",
            "shipley_phase TEXT DEFAULT 'phase_0'",
            "capture_manager TEXT",
            "created_at TEXT NOT NULL",
            "updated_at TEXT NOT NULL",
        ],
    },
    {
        "name": "capture_plans",
        "description": "Structured capture plans aligned to Shipley Phase 2",
        "append_only": False,
        "columns": [
            "id TEXT PRIMARY KEY",
            "opportunity_id TEXT NOT NULL",
            "version INTEGER DEFAULT 1",
            "status TEXT DEFAULT 'draft'",
            "win_strategy_summary TEXT",
            "team_summary TEXT",
            "customer_engagement_summary TEXT",
            "competitive_assessment_summary TEXT",
            "content_hash TEXT",
            "created_at TEXT NOT NULL",
            "updated_at TEXT NOT NULL",
        ],
    },
    {
        "name": "capture_milestones",
        "description": "Key milestones and deadlines per capture effort",
        "append_only": True,
        "columns": [
            "id TEXT PRIMARY KEY",
            "opportunity_id TEXT NOT NULL",
            "milestone_type TEXT NOT NULL",
            "title TEXT NOT NULL",
            "due_date TEXT",
            "completed_at TEXT",
            "status TEXT DEFAULT 'pending'",
            "created_at TEXT NOT NULL",
        ],
    },
    {
        "name": "competitor_profiles",
        "description": "Competitor intelligence per opportunity",
        "append_only": False,
        "columns": [
            "id TEXT PRIMARY KEY",
            "opportunity_id TEXT",
            "competitor_name TEXT NOT NULL",
            "incumbent INTEGER DEFAULT 0",
            "strengths TEXT",
            "weaknesses TEXT",
            "ghost_strategy TEXT",
            "fpds_win_count INTEGER DEFAULT 0",
            "estimated_pwin REAL",
            "created_at TEXT NOT NULL",
            "updated_at TEXT NOT NULL",
        ],
    },
    {
        "name": "pwin_assessments",
        "description": "Historical Pwin/Pgo assessments (append-only for trend)",
        "append_only": True,
        "columns": [
            "id TEXT PRIMARY KEY",
            "opportunity_id TEXT NOT NULL",
            "assessor TEXT",
            "pwin REAL NOT NULL",
            "pgo REAL NOT NULL",
            "confidence REAL DEFAULT 0.5",
            "factors_json TEXT",
            "assessment_date TEXT NOT NULL",
            "created_at TEXT NOT NULL",
        ],
    },
    {
        "name": "gate_decisions",
        "description": "Formal bid/no-bid gate decisions (append-only, NIST AU)",
        "append_only": True,
        "columns": [
            "id TEXT PRIMARY KEY",
            "opportunity_id TEXT NOT NULL",
            "gate_type TEXT NOT NULL",
            "decision TEXT NOT NULL",
            "pwin_at_decision REAL",
            "rationale TEXT",
            "approver TEXT",
            "approved_at TEXT",
            "created_at TEXT NOT NULL",
        ],
    },
    {
        "name": "customer_contacts",
        "description": "Customer and stakeholder contact registry",
        "append_only": False,
        "columns": [
            "id TEXT PRIMARY KEY",
            "name TEXT NOT NULL",
            "title TEXT",
            "organization TEXT",
            "agency TEXT",
            "email TEXT",
            "phone TEXT",
            "influence_level TEXT DEFAULT 'medium'",
            "relationship_score REAL DEFAULT 0.0",
            "created_at TEXT NOT NULL",
            "updated_at TEXT NOT NULL",
        ],
    },
    {
        "name": "customer_engagements",
        "description": "Engagement history with customers (append-only for audit)",
        "append_only": True,
        "columns": [
            "id TEXT PRIMARY KEY",
            "contact_id TEXT NOT NULL",
            "opportunity_id TEXT",
            "engagement_type TEXT NOT NULL",
            "summary TEXT",
            "outcome TEXT",
            "follow_up_date TEXT",
            "engagement_date TEXT NOT NULL",
            "created_at TEXT NOT NULL",
        ],
    },
    {
        "name": "shaping_activities",
        "description": "Pre-RFP shaping activities and influence campaigns",
        "append_only": True,
        "columns": [
            "id TEXT PRIMARY KEY",
            "opportunity_id TEXT NOT NULL",
            "activity_type TEXT NOT NULL",
            "description TEXT",
            "target_audience TEXT",
            "status TEXT DEFAULT 'planned'",
            "outcome TEXT",
            "activity_date TEXT",
            "created_at TEXT NOT NULL",
        ],
    },
    {
        "name": "pipeline_analytics",
        "description": "Aggregated pipeline metrics (append-only time-series)",
        "append_only": True,
        "columns": [
            "id TEXT PRIMARY KEY",
            "snapshot_date TEXT NOT NULL",
            "total_opportunities INTEGER",
            "total_pipeline_value REAL",
            "avg_pwin REAL",
            "opportunities_by_phase_json TEXT",
            "win_rate_trailing_12m REAL",
            "created_at TEXT NOT NULL",
        ],
    },
    {
        "name": "win_loss_analytics",
        "description": "Win/loss debrief records for Bayesian calibration",
        "append_only": True,
        "columns": [
            "id TEXT PRIMARY KEY",
            "opportunity_id TEXT NOT NULL",
            "outcome TEXT NOT NULL",
            "final_pwin REAL",
            "debrief_summary TEXT",
            "lessons_learned TEXT",
            "competitor_winner TEXT",
            "outcome_date TEXT NOT NULL",
            "created_at TEXT NOT NULL",
        ],
    },
]


# ============================================================
# Interop schema (A2A handoff)
# ============================================================

INTEROP_SCHEMA: Dict[str, Any] = {
    "schema_version": "1.0.0",
    "description": (
        "A2A JSON-RPC handoff schema for CaptureAI-to-Proposal Genesis interoperability. "
        "Transmitted at RFP release to transfer capture intelligence into proposal production."
    ),
    "transport": "A2A JSON-RPC 2.0 over mutual TLS",
    "events": {
        "rfp_released": {
            "direction": "CaptureAI -> Proposal Genesis",
            "description": "Triggered when RFP is released, initiating proposal phase",
            "payload": {
                "opportunity": {
                    "type": "object",
                    "required": True,
                    "fields": {
                        "id": {"type": "string", "required": True, "description": "CaptureAI opportunity ID"},
                        "title": {"type": "string", "required": True, "description": "Opportunity title"},
                        "agency": {"type": "string", "required": True, "description": "Sponsoring agency"},
                        "solicitation_number": {
                            "type": "string",
                            "required": True,
                            "description": "SAM.gov solicitation number",
                        },
                        "naics_code": {"type": "string", "required": False, "description": "Primary NAICS code"},
                        "set_aside": {
                            "type": "string",
                            "required": False,
                            "description": "Set-aside type (e.g., 8(a), SDVOSB)",
                        },
                        "response_deadline": {
                            "type": "string",
                            "required": True,
                            "description": "ISO 8601 proposal due date",
                        },
                        "estimated_value": {
                            "type": "number",
                            "required": False,
                            "description": "Estimated contract value in USD",
                        },
                        "period_of_performance": {
                            "type": "string",
                            "required": False,
                            "description": "Base + option years",
                        },
                    },
                },
                "win_strategy": {
                    "type": "object",
                    "required": True,
                    "fields": {
                        "win_themes": {"type": "array", "required": True, "description": "Ordered list of win themes"},
                        "discriminators": {
                            "type": "array",
                            "required": True,
                            "description": "Key discriminators vs competition",
                        },
                        "ghost_strategies": {
                            "type": "array",
                            "required": False,
                            "description": "Ghost team strategies per competitor",
                        },
                        "solution_approach_summary": {
                            "type": "string",
                            "required": True,
                            "description": "High-level solution narrative",
                        },
                    },
                },
                "team": {
                    "type": "object",
                    "required": True,
                    "fields": {
                        "prime_contractor": {
                            "type": "string",
                            "required": True,
                            "description": "Prime contractor name",
                        },
                        "subcontractors": {
                            "type": "array",
                            "required": False,
                            "description": "List of teaming partners with workshare",
                        },
                        "key_personnel": {
                            "type": "array",
                            "required": False,
                            "description": "Key personnel with roles and qualifications",
                        },
                        "oci_status": {
                            "type": "string",
                            "required": True,
                            "description": "OCI screening result: clear | mitigated | flagged",
                        },
                    },
                },
                "competitive_intel": {
                    "type": "object",
                    "required": True,
                    "fields": {
                        "competitors": {"type": "array", "required": True, "description": "Known competitor profiles"},
                        "incumbent": {
                            "type": "string",
                            "required": False,
                            "description": "Current incumbent name if recompete",
                        },
                        "competitive_landscape_summary": {
                            "type": "string",
                            "required": True,
                            "description": "Assessment narrative",
                        },
                    },
                },
                "customer_context": {
                    "type": "object",
                    "required": True,
                    "fields": {
                        "key_contacts": {
                            "type": "array",
                            "required": True,
                            "description": "Customer contacts with influence scores",
                        },
                        "engagement_history_summary": {
                            "type": "string",
                            "required": True,
                            "description": "Customer relationship summary",
                        },
                        "hot_buttons": {
                            "type": "array",
                            "required": False,
                            "description": "Customer priorities and concerns",
                        },
                        "evaluation_criteria_intel": {
                            "type": "array",
                            "required": False,
                            "description": "Known or inferred evaluation factors",
                        },
                    },
                },
                "pwin_assessment": {
                    "type": "object",
                    "required": True,
                    "fields": {
                        "pwin": {"type": "number", "required": True, "description": "Probability of win (0.0-1.0)"},
                        "pgo": {
                            "type": "number",
                            "required": True,
                            "description": "Probability of go decision (0.0-1.0)",
                        },
                        "confidence": {
                            "type": "number",
                            "required": True,
                            "description": "Assessment confidence (0.0-1.0)",
                        },
                        "factors": {
                            "type": "object",
                            "required": False,
                            "description": "Scored Pwin factors breakdown",
                        },
                        "gate_decision": {
                            "type": "string",
                            "required": True,
                            "description": "Latest gate decision: bid | no_bid | conditional",
                        },
                    },
                },
            },
        },
        "competitive_update": {
            "direction": "bidirectional",
            "description": "Mid-proposal competitive intelligence updates",
            "payload": {
                "opportunity_id": {"type": "string", "required": True},
                "update_type": {
                    "type": "string",
                    "required": True,
                    "description": "new_competitor | incumbent_change | teaming_shift | price_intel",
                },
                "details": {"type": "object", "required": True},
                "timestamp": {"type": "string", "required": True, "description": "ISO 8601 timestamp"},
            },
        },
        "post_award_feedback": {
            "direction": "Proposal Genesis -> CaptureAI",
            "description": "Win/loss outcome for Bayesian calibration and learning",
            "payload": {
                "opportunity_id": {"type": "string", "required": True},
                "outcome": {"type": "string", "required": True, "description": "win | loss | no_bid | cancelled"},
                "debrief_available": {"type": "boolean", "required": True},
                "debrief_summary": {"type": "string", "required": False},
                "competitor_winner": {"type": "string", "required": False},
                "lessons_learned": {"type": "array", "required": False},
            },
        },
    },
}


# ============================================================
# Utility helpers
# ============================================================


def _now() -> str:
    """Return current UTC timestamp as ISO 8601 string."""
    return datetime.now(timezone.utc).isoformat()


def _uuid() -> str:
    """Return a new UUID4 string."""
    return str(uuid.uuid4())


def _compute_hash(data: Dict[str, Any]) -> str:
    """Compute SHA-256 content hash for a blueprint dict.

    Excludes the ``blueprint_hash`` field itself to avoid circular hashing.
    """
    sanitised = {k: v for k, v in data.items() if k != "blueprint_hash"}
    raw = json.dumps(sanitised, sort_keys=True, default=str).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def _audit(event_type: str, action: str, details: Optional[Dict[str, Any]] = None) -> None:
    """Log an audit event (append-only, NIST AU)."""
    try:
        audit_log_event(
            event_type=event_type,
            actor="capture_ai_blueprint",
            action=action,
            details=json.dumps(details or {}),
        )
    except Exception:
        logger.debug("Audit log failed for %s: %s", event_type, action)


# ============================================================
# Core functions
# ============================================================


def generate_blueprint(
    deployment_mode: str = "integrated",
    team_size: int = 10,
    compliance_frameworks: Optional[List[str]] = None,
) -> Dict[str, Any]:
    """Generate CaptureAI child app blueprint.

    Produces a blueprint dict compatible with ``child_app_generator.py`` that
    fully describes the CaptureAI application architecture, agents, modules,
    DB tables, and interoperability schema.

    Args:
        deployment_mode: One of ``standalone``, ``integrated``, ``embedded``.
            - standalone: Full independent deployment with own DB and agents.
            - integrated: Shares ICDEV™ parent DB, adds capture-specific tables.
            - embedded: Runs within Proposal Genesis daemon (no separate agents).
        team_size: Expected capture team size (affects process formality).
        compliance_frameworks: Optional list of compliance frameworks to enable.

    Returns:
        Complete blueprint dict ready for ``child_app_generator.py`` consumption.

    Raises:
        ValueError: If ``deployment_mode`` is invalid or ``team_size`` < 1.
    """
    if deployment_mode not in DEPLOYMENT_MODES:
        raise ValueError(f"Invalid deployment_mode '{deployment_mode}'. Must be one of: {', '.join(DEPLOYMENT_MODES)}")
    if team_size < 1:
        raise ValueError(f"team_size must be >= 1, got {team_size}")

    frameworks = compliance_frameworks or []
    for fw in frameworks:
        if fw not in SUPPORTED_FRAMEWORKS:
            raise ValueError(f"Unsupported compliance framework '{fw}'. Supported: {', '.join(SUPPORTED_FRAMEWORKS)}")

    blueprint_id = _uuid()
    now = _now()

    # --- Resolve agents ---
    agents: List[Dict[str, Any]] = []
    for agent in CORE_AGENTS:
        agents.append(
            {
                "name": agent["name"],
                "port": agent["base_port"] + CHILD_PORT_OFFSET,
                "role": agent["role"],
                "tier": agent["tier"],
            }
        )

    for agent in CONDITIONAL_AGENTS:
        include = False
        if agent["condition"] == "compliance_frameworks" and frameworks:
            include = True
        elif agent["condition"] == "impact_level_gte_il4":
            # Include security agent when any compliance framework implies IL4+
            include = bool(frameworks)
        if include:
            agents.append(
                {
                    "name": agent["name"],
                    "port": agent["base_port"] + CHILD_PORT_OFFSET,
                    "role": agent["role"],
                    "tier": agent["tier"],
                }
            )

    # --- Resolve modules ---
    modules: List[Dict[str, Any]] = []
    for mod in CORE_MODULES:
        modules.append(
            {
                "name": mod["name"],
                "description": mod["description"],
                "enabled": True,
                "db_tables": mod["db_tables"],
                "dependencies": mod["dependencies"],
            }
        )
    for mod in OPTIONAL_MODULES:
        enabled = False
        if mod["name"] == "compliance_tracker" and frameworks:
            enabled = True
        elif mod["name"] == "cost_estimator" and deployment_mode != "embedded":
            enabled = True
        modules.append(
            {
                "name": mod["name"],
                "description": mod["description"],
                "enabled": enabled,
                "db_tables": mod.get("db_tables", []),
                "dependencies": mod.get("dependencies", []),
            }
        )

    # --- Resolve DB tables ---
    active_table_names: set = set()
    for mod in modules:
        if mod["enabled"]:
            for t in mod["db_tables"]:
                active_table_names.add(t)

    db_tables = [t for t in CAPTURE_DB_TABLES if t["name"] in active_table_names]

    # --- Process adaptation based on team size ---
    process_formality = "full"
    if team_size <= 3:
        process_formality = "minimal"
    elif team_size <= 8:
        process_formality = "lightweight"
    elif team_size <= 20:
        process_formality = "standard"
    else:
        process_formality = "full"

    # --- DB config ---
    if deployment_mode == "standalone":
        db_config = {
            "engine": "sqlite",
            "name": "captureai.db",
            "path": "data/captureai.db",
            "initial_tables": "capture_specific",
            "migration_supported": True,
        }
    elif deployment_mode == "integrated":
        db_config = {
            "engine": "shared",
            "name": "icdev.db",
            "path": "data/icdev.db",
            "initial_tables": "capture_additive",
            "migration_supported": True,
            "note": "Adds capture-prefixed tables to existing ICDEV™ database",
        }
    else:  # embedded
        db_config = {
            "engine": "shared",
            "name": "icdev.db",
            "path": "data/icdev.db",
            "initial_tables": "none",
            "migration_supported": False,
            "note": "Embedded mode reuses Proposal Genesis tables directly",
        }

    # --- Capabilities map (child_app_generator.py expects this) ---
    capabilities = {
        "core": True,
        "compliance": bool(frameworks),
        "security": bool(frameworks),
        "mbse": False,
        "cicd": deployment_mode != "embedded",
        "testing": True,
        "dashboard": deployment_mode != "embedded",
        "knowledge": True,
        "modernization": False,
        "infra": deployment_mode == "standalone",
        "db": True,
        "project": True,
        "memory": True,
        "ricoas": False,
        "supply_chain": False,
        "simulation": False,
        "devsecops_zta": bool(frameworks),
        "ai_security": False,
        "ai_governance": False,
        "observability": True,
        "code_intelligence": False,
        "govcon": False,
    }

    # --- ATLAS config ---
    atlas_config = {
        "fitness_step": False,
        "model_phase": False,
        "phases": ["architect", "trace", "link", "assemble", "stress_test"],
        "critique_enabled": False,
    }

    # --- Grandchild prevention (D52) ---
    grandchild_prevention = {
        "enabled": True,
        "config_flag": True,
        "scaffolder_strip": True,
        "claude_md_doc": True,
        "description": (
            "CaptureAI MUST NOT generate child applications. "
            "Agentic fitness assessor and app blueprint engine are stripped."
        ),
    }

    # --- Memory config ---
    memory_config = {
        "memory_md": True,
        "daily_logs": True,
        "sqlite_db": True,
        "semantic_search": deployment_mode != "embedded",
        "embeddings": deployment_mode != "embedded",
    }

    # --- CI/CD config ---
    cicd_config = {
        "github": deployment_mode != "embedded",
        "gitlab": deployment_mode != "embedded",
        "webhooks": deployment_mode != "embedded",
        "polling": deployment_mode != "embedded",
        "slash_commands": deployment_mode != "embedded",
        "bot_identifier": "[CAPTUREAI-BOT]",
    }

    # --- Interop config ---
    interop_config = {
        "proposal_genesis_handoff": True,
        "protocol": "A2A JSON-RPC 2.0",
        "events": list(INTEROP_SCHEMA["events"].keys()),
        "schema_version": INTEROP_SCHEMA["schema_version"],
    }

    # --- Build the blueprint ---
    blueprint: Dict[str, Any] = {
        "blueprint_id": blueprint_id,
        "app_name": "CaptureAI",
        "app_type": "capture_management",
        "description": ("AI-powered Capture Management for Government Contracting -- Shipley Phases 0-2"),
        "classification": "CUI",
        "impact_level": "IL4",
        "deployment_mode": deployment_mode,
        "team_size": team_size,
        "process_formality": process_formality,
        "compliance_frameworks": frameworks,
        "fitness_scorecard": {
            "component": "capture_management",
            "overall_score": 7.5,
            "scores": {
                "technical_complexity": 6,
                "compliance_sensitivity": 7 if frameworks else 4,
                "user_interaction": 8,
                "data_sensitivity": 7,
                "integration_needs": 8,
                "scalability_needs": 6,
            },
            "architecture": "multi_agent",
        },
        "capabilities": capabilities,
        "agents": agents,
        "modules": modules,
        "db_config": db_config,
        "db_tables": db_tables,
        "memory_config": memory_config,
        "cicd_config": cicd_config,
        "atlas_config": atlas_config,
        "grandchild_prevention": grandchild_prevention,
        "interop": interop_config,
        "cloud_provider": {
            "provider": "aws",
            "region": "us-gov-west-1",
            "govcloud": True,
            "mcp_servers": [],
            "knowledge_bases": [],
        },
        "parent_callback": {
            "enabled": deployment_mode == "integrated",
            "url": "",
            "auth": "bearer_token",
        },
        "file_manifest": [],
        "generated_at": now,
        "generated_by": "icdev/capture_ai_blueprint",
        "demo_mode": False,
        "blueprint_hash": "",
    }

    # Compute integrity hash
    blueprint["blueprint_hash"] = _compute_hash(blueprint)

    # Audit trail
    _audit(
        "blueprint.generated",
        f"Generated CaptureAI blueprint: {deployment_mode}, team_size={team_size}",
        {
            "blueprint_id": blueprint_id,
            "deployment_mode": deployment_mode,
            "team_size": team_size,
            "agent_count": len(agents),
            "module_count": sum(1 for m in modules if m["enabled"]),
            "compliance_frameworks": frameworks,
        },
    )

    logger.info(
        "CaptureAI blueprint %s generated: mode=%s, team=%d, agents=%d, modules=%d",
        blueprint_id[:12],
        deployment_mode,
        team_size,
        len(agents),
        sum(1 for m in modules if m["enabled"]),
    )

    return blueprint


def get_interop_schema() -> Dict[str, Any]:
    """Return the A2A handoff JSON schema for CaptureAI interoperability.

    Defines the data contract between CaptureAI and Proposal Genesis for
    three event types: rfp_released, competitive_update, post_award_feedback.

    Returns:
        Dict with ``schema``, ``version``, and ``description`` keys.
    """
    return {
        "schema": INTEROP_SCHEMA,
        "version": INTEROP_SCHEMA["schema_version"],
        "description": INTEROP_SCHEMA["description"],
    }


def estimate_resources(
    deployment_mode: str = "integrated",
    team_size: int = 10,
) -> Dict[str, Any]:
    """Estimate infrastructure requirements for CaptureAI.

    Provides CPU, memory, and storage estimates scaled by deployment mode
    and team size.

    Args:
        deployment_mode: One of ``standalone``, ``integrated``, ``embedded``.
        team_size: Expected team size (affects concurrent user scaling).

    Returns:
        Dict with ``deployment_mode``, ``resources``, and ``estimated_cost_monthly``.

    Raises:
        ValueError: If ``deployment_mode`` is invalid.
    """
    if deployment_mode not in DEPLOYMENT_MODES:
        raise ValueError(f"Invalid deployment_mode '{deployment_mode}'. Must be one of: {', '.join(DEPLOYMENT_MODES)}")

    # Base resources per mode
    base_resources = {
        "standalone": {"cpu_vcpu": 2.0, "memory_gb": 4.0, "storage_gb": 10.0},
        "integrated": {"cpu_vcpu": 1.0, "memory_gb": 2.0, "storage_gb": 5.0},
        "embedded": {"cpu_vcpu": 0.0, "memory_gb": 0.0, "storage_gb": 0.0},
    }

    resources = base_resources[deployment_mode].copy()

    # Scale for team size (logarithmic scaling beyond 10 users)
    if deployment_mode != "embedded" and team_size > 10:
        import math

        scale_factor = 1.0 + 0.3 * math.log2(team_size / 10)
        resources["cpu_vcpu"] = round(resources["cpu_vcpu"] * scale_factor, 1)
        resources["memory_gb"] = round(resources["memory_gb"] * scale_factor, 1)
        resources["storage_gb"] = round(resources["storage_gb"] * scale_factor, 1)

    # Rough cost estimate (AWS GovCloud pricing approximation)
    monthly_cost = 0.0
    if deployment_mode == "standalone":
        # t3.medium equivalent + EBS + data transfer
        monthly_cost = 85.0 + (resources["storage_gb"] * 0.10) + 15.0
    elif deployment_mode == "integrated":
        # Incremental cost on shared infrastructure
        monthly_cost = 35.0 + (resources["storage_gb"] * 0.10) + 5.0
    # embedded = 0.0 (no additional resources)

    return {
        "deployment_mode": deployment_mode,
        "team_size": team_size,
        "resources": {
            "cpu_vcpu": resources["cpu_vcpu"],
            "memory_gb": resources["memory_gb"],
            "storage_gb": resources["storage_gb"],
        },
        "estimated_cost_monthly_usd": round(monthly_cost, 2),
        "notes": {
            "standalone": "Full independent deployment: dedicated compute, DB, and agents",
            "integrated": "Shared ICDEV™ infrastructure: incremental compute for capture agents",
            "embedded": "Zero additional resources: runs within Proposal Genesis daemon process",
        }.get(deployment_mode, ""),
    }


def validate_blueprint(blueprint: Dict[str, Any]) -> Dict[str, Any]:
    """Validate a CaptureAI blueprint dict for correctness.

    Checks required fields, agent port conflicts with ICDEV™ parent,
    module dependency integrity, and structural completeness.

    Args:
        blueprint: Blueprint dict to validate.

    Returns:
        Dict with ``valid`` (bool), ``issues`` (list of blocking errors),
        and ``warnings`` (list of non-blocking advisories).
    """
    issues: List[str] = []
    warnings: List[str] = []

    # --- Required top-level fields ---
    required_fields = [
        "blueprint_id",
        "app_name",
        "app_type",
        "deployment_mode",
        "agents",
        "modules",
        "db_config",
        "capabilities",
        "generated_at",
        "blueprint_hash",
    ]
    for field in required_fields:
        if field not in blueprint:
            issues.append(f"Missing required field: {field}")

    # --- App type validation ---
    if blueprint.get("app_type") != "capture_management":
        warnings.append(f"Unexpected app_type '{blueprint.get('app_type')}', expected 'capture_management'")

    # --- Deployment mode ---
    mode = blueprint.get("deployment_mode")
    if mode and mode not in DEPLOYMENT_MODES:
        issues.append(f"Invalid deployment_mode '{mode}': must be one of {DEPLOYMENT_MODES}")

    # --- Agent port validation ---
    agents = blueprint.get("agents", [])
    used_ports: set = set()
    for agent in agents:
        port = agent.get("port", 0)
        if port in PARENT_PORT_RANGE:
            issues.append(
                f"Agent '{agent.get('name')}' port {port} conflicts with ICDEV™ parent range "
                f"({PARENT_PORT_RANGE.start}-{PARENT_PORT_RANGE.stop - 1})"
            )
        if port in used_ports:
            issues.append(f"Duplicate port {port} for agent '{agent.get('name')}'")
        used_ports.add(port)

    # --- Module dependency validation ---
    modules = blueprint.get("modules", [])
    enabled_module_names = {m["name"] for m in modules if m.get("enabled")}
    for mod in modules:
        if not mod.get("enabled"):
            continue
        for dep in mod.get("dependencies", []):
            if dep not in enabled_module_names:
                issues.append(f"Module '{mod['name']}' depends on '{dep}' which is not enabled")

    # --- Integrity hash check ---
    stored_hash = blueprint.get("blueprint_hash", "")
    if stored_hash:
        computed_hash = _compute_hash(blueprint)
        if computed_hash != stored_hash:
            issues.append(f"Blueprint hash mismatch: stored={stored_hash[:16]}... vs computed={computed_hash[:16]}...")

    # --- Grandchild prevention ---
    gcp = blueprint.get("grandchild_prevention", {})
    if not gcp.get("enabled"):
        warnings.append("Grandchild prevention is not enabled (D52 violation)")

    # --- At least one agent ---
    if not agents:
        issues.append("Blueprint has no agents defined")

    valid = len(issues) == 0

    return {
        "valid": valid,
        "issues": issues,
        "warnings": warnings,
        "checks_performed": len(required_fields) + 4,
    }


def export_blueprint(
    blueprint: Dict[str, Any],
    output_path: Optional[str] = None,
) -> Dict[str, Any]:
    """Export a blueprint as a JSON file.

    Saves the blueprint to the default proposal_genesis blueprints directory
    or a specified path.

    Args:
        blueprint: Blueprint dict to export.
        output_path: Optional file path override. Defaults to
            ``data/proposal_genesis/blueprints/capture-ai-blueprint.json``.

    Returns:
        Dict with ``path`` and ``size_bytes``.
    """
    if output_path:
        dest = Path(output_path)
    else:
        dest = BLUEPRINT_DIR / "capture-ai-blueprint.json"

    dest.parent.mkdir(parents=True, exist_ok=True)

    content = json.dumps(blueprint, indent=2, sort_keys=False, default=str)
    dest.write_text(content, encoding="utf-8", newline="")

    size = dest.stat().st_size

    _audit(
        "blueprint.exported",
        f"Exported CaptureAI blueprint to {dest}",
        {
            "blueprint_id": blueprint.get("blueprint_id", "unknown"),
            "path": str(dest),
            "size_bytes": size,
        },
    )

    logger.info("Blueprint exported to %s (%d bytes)", dest, size)

    return {
        "path": str(dest),
        "size_bytes": size,
    }


# ============================================================
# CLI
# ============================================================


def _build_parser() -> argparse.ArgumentParser:
    """Build argparse parser."""
    parser = argparse.ArgumentParser(
        description="CaptureAI Child App Blueprint Generator",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )

    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument(
        "--generate",
        action="store_true",
        help="Generate CaptureAI child app blueprint",
    )
    group.add_argument(
        "--interop-schema",
        action="store_true",
        help="Output A2A interoperability schema",
    )
    group.add_argument(
        "--estimate-resources",
        action="store_true",
        help="Estimate infrastructure requirements",
    )
    group.add_argument(
        "--validate",
        action="store_true",
        help="Validate an existing blueprint file",
    )
    group.add_argument(
        "--export",
        action="store_true",
        help="Generate and export blueprint to file",
    )

    parser.add_argument(
        "--deployment-mode",
        choices=DEPLOYMENT_MODES,
        default="integrated",
        help="Deployment mode (default: integrated)",
    )
    parser.add_argument(
        "--team-size",
        type=int,
        default=10,
        help="Expected capture team size (default: 10)",
    )
    parser.add_argument(
        "--compliance-frameworks",
        type=str,
        default=None,
        help="Comma-separated list of compliance frameworks (e.g., fedramp_moderate,cmmc_l2)",
    )
    parser.add_argument(
        "--blueprint-file",
        type=str,
        default=None,
        help="Path to blueprint JSON file (for --validate)",
    )
    parser.add_argument(
        "--output-path",
        type=str,
        default=None,
        help="Output path for --export",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Output as JSON",
    )

    return parser


def main() -> None:
    """CLI entry point."""
    parser = _build_parser()
    args = parser.parse_args()

    frameworks = None
    if args.compliance_frameworks:
        frameworks = [f.strip() for f in args.compliance_frameworks.split(",") if f.strip()]

    result: Dict[str, Any] = {}

    if args.generate:
        result = generate_blueprint(
            deployment_mode=args.deployment_mode,
            team_size=args.team_size,
            compliance_frameworks=frameworks,
        )

    elif args.interop_schema:
        result = get_interop_schema()

    elif args.estimate_resources:
        result = estimate_resources(
            deployment_mode=args.deployment_mode,
            team_size=args.team_size,
        )

    elif args.validate:
        if not args.blueprint_file:
            parser.error("--validate requires --blueprint-file")
        bp_path = Path(args.blueprint_file)
        if not bp_path.exists():
            result = {"valid": False, "issues": [f"File not found: {bp_path}"], "warnings": []}
        else:
            bp_data = json.loads(bp_path.read_text(encoding="utf-8"))
            result = validate_blueprint(bp_data)

    elif args.export:
        bp = generate_blueprint(
            deployment_mode=args.deployment_mode,
            team_size=args.team_size,
            compliance_frameworks=frameworks,
        )
        result = export_blueprint(bp, output_path=args.output_path)

    if args.json:
        print(json.dumps(result, indent=2, default=str))
    else:
        print(json.dumps(result, indent=2, default=str))


if __name__ == "__main__":
    main()

# CUI // SP-CTI
