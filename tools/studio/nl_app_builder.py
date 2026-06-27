"""ICDEV™ Studio — Natural Language App Builder.

"Describe what you want → get a working app" (Base44-style UX).

Pipeline:
  1. User describes app in plain English
  2. NL parser extracts: purpose, capabilities, classification, users
  3. Deterministic mapper → blueprint capabilities dict
  4. Preview shown to user (visual capability map)
  5. User confirms/adjusts → child_app_generator runs
  6. Compliance artifacts auto-generated

Uses two-tier LLM (D366): Ollama draft → Claude refine (optional).
Falls back to deterministic keyword extraction when LLM unavailable.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from tools.db.storage import get_connection  # noqa: E402


# ── Keyword-to-capability mapping (deterministic, no LLM needed) ─────

CAPABILITY_KEYWORDS: dict[str, list[str]] = {
    "compliance": [
        "compliance",
        "ato",
        "fedramp",
        "cmmc",
        "nist",
        "stig",
        "poam",
        "ssp",
        "rmf",
        "authority to operate",
        "il4",
        "il5",
        "il6",
        "accreditation",
        "authorization",
        "cui",
        "controlled unclassified",
    ],
    "security": [
        "security",
        "vulnerability",
        "cve",
        "sast",
        "sbom",
        "scanning",
        "penetration",
        "zero trust",
        "zta",
        "encryption",
        "secrets",
    ],
    "mbse": [
        "mbse",
        "sysml",
        "model-based",
        "digital thread",
        "reqif",
        "doors",
        "systems engineering",
        "cameo",
        "magicdraw",
    ],
    "ricoas": [
        "requirements",
        "intake",
        "elicitation",
        "user stories",
        "decomposition",
        "traceability",
        "gap analysis",
        "readiness",
    ],
    "supply_chain": [
        "supply chain",
        "scrm",
        "sbom",
        "dependency",
        "cve",
        "vendor",
        "third-party",
        "open source risk",
    ],
    "simulation": [
        "simulation",
        "monte carlo",
        "scenario",
        "coa",
        "digital twin",
        "what-if",
        "forecast",
    ],
    "devsecops_zta": [
        "devsecops",
        "zero trust",
        "ci/cd",
        "pipeline",
        "devops",
        "continuous",
        "automated deploy",
    ],
    "ai_security": [
        "ai security",
        "prompt injection",
        "model security",
        "ai bom",
        "adversarial",
        "red team",
    ],
    "ai_governance": [
        "ai governance",
        "eu ai act",
        "ai transparency",
        "model card",
        "fairness",
        "bias",
        "accountability",
        "ai inventory",
    ],
    "observability": [
        "observability",
        "tracing",
        "monitoring",
        "metrics",
        "alerting",
        "logging",
        "otel",
        "opentelemetry",
    ],
    "rag": [
        "rag",
        "retrieval",
        "knowledge base",
        "semantic search",
        "vector",
        "embeddings",
        "knowledge search",
    ],
    "fine_tuning": [
        "fine-tune",
        "finetune",
        "fine tuning",
        "training",
        "dataset",
        "model training",
        "qlora",
        "lora",
    ],
    "knowledge_graph": [
        "knowledge graph",
        "graph",
        "neo4j",
        "entity",
        "relationship",
        "ontology",
        "triple",
    ],
    "databridge": [
        "data bridge",
        "databridge",
        "integration",
        "connector",
        "sync",
        "etl",
        "data pipeline",
    ],
    "dashboard": [
        "dashboard",
        "ui",
        "web interface",
        "portal",
        "visualization",
        "reporting",
        "charts",
    ],
}

CLASSIFICATION_KEYWORDS: dict[str, list[str]] = {
    "IL6": ["secret", "classified", "il6", "sipr", "top secret"],
    "IL5": ["il5", "mission-critical", "dod dedicated"],
    "IL4": ["il4", "cui", "controlled unclassified", "govcloud"],
    "IL2": ["public", "il2", "non-sensitive", "open"],
}

PURPOSE_PATTERNS: list[tuple[str, str]] = [
    (r"track\w*\s+(cdrl|deliverable|contract|milestone)", "contract_management"),
    (r"(compliance|ato|accreditation)\s+(track|manage|automate)", "compliance_automation"),
    (r"(monitor|alert|observe)\s+(system|service|infra)", "monitoring"),
    (r"(scan|detect|check)\s+(vulnerability|cve|security)", "security_scanning"),
    (r"(proposal|rfp|bid|capture)\s+(manage|track|respond)", "proposal_management"),
    (r"(build|create|develop)\s+(app|application|tool|system)", "app_development"),
    (r"(migrate|modernize|refactor)\s+(legacy|monolith|app)", "modernization"),
    (r"(train|fine.?tune|label)\s+(model|ai|llm)", "ml_training"),
    (r"(research|analyze|investigate)\s+(market|industry|competitor)", "research"),
    (r"(manage|track)\s+(case|incident|ticket|issue)", "case_management"),
]


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


# ── NL Extraction (deterministic) ────────────────────────────────────


def extract_from_description(description: str) -> dict:
    """Extract capabilities, classification, and purpose from NL description.

    100% deterministic — no LLM required.
    """
    desc_lower = description.lower()

    # Extract capabilities
    capabilities: dict[str, bool] = {
        "core": True,
        "testing": True,
        "cicd": True,
        "knowledge": True,
        "memory": True,
        "infrastructure": True,
        "maintenance": True,
        "orchestration": True,
    }

    for cap_name, keywords in CAPABILITY_KEYWORDS.items():
        matched = any(kw in desc_lower for kw in keywords)
        capabilities[cap_name] = matched

    # Always include compliance if classification >= IL4
    classification = "IL4"  # default
    for level, keywords in CLASSIFICATION_KEYWORDS.items():
        if any(kw in desc_lower for kw in keywords):
            classification = level
            break

    if classification in ("IL4", "IL5", "IL6"):
        capabilities["compliance"] = True
        capabilities["security"] = True

    # Detect purpose
    purpose = "general"
    for pattern, purpose_name in PURPOSE_PATTERNS:
        if re.search(pattern, desc_lower):
            purpose = purpose_name
            break

    # Extract app name hint
    name_match = re.search(
        r"(?:called?|named?|titled?)\s+[\"']?([a-zA-Z][a-zA-Z0-9_ -]{1,30})[\"']?",
        description,
        re.IGNORECASE,
    )
    suggested_name = name_match.group(1).strip() if name_match else None

    # Count enabled capabilities
    enabled = [k for k, v in capabilities.items() if v]

    return {
        "capabilities": capabilities,
        "classification": classification,
        "purpose": purpose,
        "suggested_name": suggested_name,
        "enabled_count": len(enabled),
        "enabled_capabilities": enabled,
        "description_length": len(description),
    }


# ── Blueprint Generation ─────────────────────────────────────────────


def build_blueprint_from_extraction(
    extraction: dict,
    app_name: str,
    *,
    port_offset: int = 1000,
    cloud_provider: str = "aws",
    govcloud: bool = False,
) -> dict:
    """Convert extraction result into a child app blueprint.

    This is a simplified blueprint that can feed into
    tools.builder.child_app_generator.generate_child_app().
    """
    impact_level = extraction["classification"]
    caps = extraction["capabilities"]

    # Determine classification marking
    classification_map = {
        "IL6": "SECRET",
        "IL5": "CUI",
        "IL4": "CUI",
        "IL2": "PUBLIC",
    }

    # Core agents (always)
    agents = [
        {
            "name": "orchestrator",
            "base_port": 8443,
            "port_offset": port_offset,
            "actual_port": 8443 + port_offset,
            "role": "Task routing, workflow management",
            "required": True,
        },
        {
            "name": "architect",
            "base_port": 8444,
            "port_offset": port_offset,
            "actual_port": 8444 + port_offset,
            "role": "Design, architecture decisions",
            "required": True,
        },
        {
            "name": "builder",
            "base_port": 8445,
            "port_offset": port_offset,
            "actual_port": 8445 + port_offset,
            "role": "Code generation, implementation",
            "required": True,
        },
    ]

    # Conditional agents
    if caps.get("compliance"):
        agents.append(
            {
                "name": "compliance",
                "base_port": 8446,
                "port_offset": port_offset,
                "actual_port": 8446 + port_offset,
                "role": "Compliance assessment, ATO artifacts",
            }
        )
    if caps.get("security"):
        agents.append(
            {
                "name": "security",
                "base_port": 8447,
                "port_offset": port_offset,
                "actual_port": 8447 + port_offset,
                "role": "Security scanning, vulnerability management",
            }
        )

    blueprint = {
        "blueprint_id": str(uuid.uuid4()),
        "app_name": app_name,
        "classification": classification_map.get(impact_level, "CUI"),
        "impact_level": impact_level,
        "capabilities": caps,
        "agents": agents,
        "cloud_provider": {
            "provider": cloud_provider,
            "govcloud": govcloud,
            "region": "us-gov-west-1" if govcloud else "us-east-1",
        },
        "db_config": {
            "engine": "sqlite",
            "name": f"{app_name}.db",
            "path": f"data/{app_name}.db",
        },
        "purpose": extraction["purpose"],
        "source_description": None,  # Set by caller
        "generated_at": _now_iso(),
        "generated_by": "icdev/studio/nl_app_builder",
    }

    return blueprint


# ── Session Management ────────────────────────────────────────────────


def create_builder_session(description: str, *, app_name: str | None = None) -> dict:
    """Start an NL app builder session.

    Returns extraction results + preview blueprint for user confirmation.
    """
    session_id = f"nlb-{uuid.uuid4().hex[:12]}"

    # Extract from description
    extraction = extract_from_description(description)

    # Generate suggested name if not provided
    if not app_name:
        app_name = extraction.get("suggested_name")
    if not app_name:
        # Generate from purpose
        purpose_names = {
            "contract_management": "contract-tracker",
            "compliance_automation": "compliance-hub",
            "monitoring": "monitor-app",
            "security_scanning": "security-scanner",
            "proposal_management": "proposal-manager",
            "app_development": "new-app",
            "modernization": "modernized-app",
            "ml_training": "ml-trainer",
            "research": "research-engine",
            "case_management": "case-tracker",
        }
        app_name = purpose_names.get(extraction["purpose"], "my-app")

    # Build preview blueprint
    blueprint = build_blueprint_from_extraction(extraction, app_name)
    blueprint["source_description"] = description

    # Store session
    conn = get_connection()
    try:
        conn.execute(
            """INSERT INTO studio_workflows
               (workflow_id, name, description, template_yaml, category,
                created_by, created_at, updated_at)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s)""",
            (
                session_id,
                f"App Builder: {app_name}",
                description[:200],
                json.dumps(blueprint, default=str),
                "app_builder",
                "studio",
                _now_iso(),
                _now_iso(),
            ),
        )
        conn.commit()
    finally:
        conn.close()

    return {
        "status": "ok",
        "session_id": session_id,
        "app_name": app_name,
        "extraction": extraction,
        "blueprint_preview": blueprint,
        "message": _build_confirmation_message(app_name, extraction, blueprint),
    }


def _build_confirmation_message(app_name: str, extraction: dict, blueprint: dict) -> str:
    """Build a human-friendly confirmation message."""
    caps = extraction["enabled_capabilities"]
    il = extraction["classification"]

    lines = [
        f"I'll build **{app_name}** with the following configuration:",
        "",
        f"- **Classification:** {il} ({blueprint['classification']})",
        f"- **Purpose:** {extraction['purpose'].replace('_', ' ').title()}",
        f"- **Capabilities ({len(caps)}):** {', '.join(caps)}",
        f"- **Agents:** {len(blueprint['agents'])} configured",
        "",
        "Review the capability map below and adjust if needed.",
        "Click **Build App** when ready.",
    ]
    return "\n".join(lines)


def refine_session(
    session_id: str,
    *,
    app_name: str | None = None,
    toggle_capabilities: dict[str, bool] | None = None,
    classification: str | None = None,
) -> dict:
    """Refine an existing builder session."""
    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT * FROM studio_workflows WHERE workflow_id = %s",
            (session_id,),
        ).fetchone()
        if not row:
            return {"status": "error", "error": "Session not found"}

        blueprint = json.loads(dict(row)["template_yaml"])

        if app_name:
            blueprint["app_name"] = app_name
        if classification:
            blueprint["impact_level"] = classification
            cl_map = {"IL6": "SECRET", "IL5": "CUI", "IL4": "CUI", "IL2": "PUBLIC"}
            blueprint["classification"] = cl_map.get(classification, "CUI")
        if toggle_capabilities:
            for cap, enabled in toggle_capabilities.items():
                blueprint["capabilities"][cap] = enabled

        conn.execute(
            "UPDATE studio_workflows SET template_yaml = %s, updated_at = %s WHERE workflow_id = %s",
            (json.dumps(blueprint, default=str), _now_iso(), session_id),
        )
        conn.commit()

        return {
            "status": "ok",
            "session_id": session_id,
            "blueprint_preview": blueprint,
        }
    finally:
        conn.close()


def execute_build(session_id: str, *, output_dir: str | None = None) -> dict:
    """Execute the build from a confirmed session.

    Calls child_app_generator.generate_child_app() with the session's blueprint.
    """
    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT * FROM studio_workflows WHERE workflow_id = %s",
            (session_id,),
        ).fetchone()
        if not row:
            return {"status": "error", "error": "Session not found"}

        blueprint = json.loads(dict(row)["template_yaml"])
    finally:
        conn.close()

    app_name = blueprint.get("app_name", "my-app")
    project_path = output_dir or str(_ROOT.parent)

    try:
        from tools.builder.child_app_generator import generate_child_app

        result = generate_child_app(
            blueprint=blueprint,
            project_path=project_path,
            name=app_name,
        )
        return {
            "status": "ok",
            "session_id": session_id,
            "build_result": result,
        }
    except Exception as exc:
        return {
            "status": "error",
            "session_id": session_id,
            "error": str(exc),
        }


# ── CLI ───────────────────────────────────────────────────────────────


def main() -> None:
    parser = argparse.ArgumentParser(description="ICDEV™ Studio NL App Builder")
    parser.add_argument("--json", action="store_true")
    sub = parser.add_subparsers(dest="command")

    p_extract = sub.add_parser("extract", help="Extract capabilities from description")
    p_extract.add_argument("description", help="Natural language app description")

    p_create = sub.add_parser("create", help="Create builder session")
    p_create.add_argument("description", help="Natural language app description")
    p_create.add_argument("--name", help="App name override")

    p_refine = sub.add_parser("refine", help="Refine session")
    p_refine.add_argument("session_id")
    p_refine.add_argument("--name", help="New app name")
    p_refine.add_argument("--classification", help="IL2|IL4|IL5|IL6")

    args = parser.parse_args()
    result: Any = None

    if args.command == "extract":
        result = extract_from_description(args.description)
    elif args.command == "create":
        result = create_builder_session(args.description, app_name=args.name)
    elif args.command == "refine":
        result = refine_session(
            args.session_id,
            app_name=getattr(args, "name", None),
            classification=getattr(args, "classification", None),
        )
    else:
        parser.print_help()
        return

    if args.json:
        print(json.dumps(result, indent=2, default=str))
    else:
        print(json.dumps(result, indent=2, default=str))


if __name__ == "__main__":
    main()
