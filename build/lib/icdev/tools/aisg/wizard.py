"""
AISG Setup Wizard backend — pure rule-based mapping, no LLM calls.

Maps 5 wizard answers to recommended_goals, recommended_skills, and a
phased sprint_plan_tasks list; persists generated args overrides to DB.
"""

from __future__ import annotations

import json
from typing import Any

from tools.db.storage import get_connection, sql_placeholder

# ---------------------------------------------------------------------------
# Rule tables
# ---------------------------------------------------------------------------

_COMPLIANCE_GOALS: dict[str, list[str]] = {
    "IL2": ["compliance_workflow.md", "security_categorization.md"],
    "IL4": ["compliance_workflow.md", "ato_acceleration.md", "zero_trust_architecture.md"],
    "IL5": [
        "compliance_workflow.md",
        "ato_acceleration.md",
        "zero_trust_architecture.md",
        "devsecops_workflow.md",
    ],
    "FedRAMP": ["compliance_workflow.md", "ato_acceleration.md", "nlq_compliance.md"],
    "CMMC": ["compliance_workflow.md", "boundary_supply_chain.md", "devsecops_workflow.md"],
}

_COMPLIANCE_SKILLS: dict[str, list[str]] = {
    "IL2": ["icdev-comply", "icdev-secure"],
    "IL4": ["icdev-comply", "icdev-secure", "icdev-boundary"],
    "IL5": ["icdev-comply", "icdev-secure", "icdev-boundary", "icdev-deploy"],
    "FedRAMP": ["icdev-comply", "icdev-secure", "icdev-monitor"],
    "CMMC": ["icdev-comply", "icdev-secure", "icdev-boundary"],
}

_USE_CASE_GOALS: dict[str, list[str]] = {
    "web_app": ["build_app.md", "dashboard.md", "tdd_workflow.md"],
    "dashboard": ["dashboard.md", "build_app.md", "monitoring.md"],
    "compliance": ["compliance_workflow.md", "ato_acceleration.md", "nlq_compliance.md"],
    "ato": ["ato_acceleration.md", "compliance_workflow.md", "security_scan.md"],
    "security": ["security_scan.md", "devsecops_workflow.md", "owasp_agentic_security.md"],
    "devops": ["cicd_integration.md", "deploy_workflow.md", "parallel_cicd.md"],
    "research": ["industry_research.md", "autoresearch.md", "govcon_intelligence.md"],
    "modernize": ["modernization_workflow.md", "cross_language_translation.md", "code_intelligence.md"],
    "monitor": ["monitoring.md", "observability.md", "observability_traceability_xai.md"],
    "saas": ["saas_multi_tenancy.md", "marketplace.md", "build_app.md"],
    "ai": ["multi_agent_orchestration.md", "innovation_engine.md", "rag_subsystem.md"],
    "govcon": ["govcon_intelligence.md", "cpmp_workflow.md", "requirements_intake.md"],
}

_USE_CASE_SKILLS: dict[str, list[str]] = {
    "web_app": ["icdev-build", "icdev-test", "icdev-review"],
    "dashboard": ["icdev-build", "icdev-test"],
    "compliance": ["icdev-comply", "icdev-secure", "icdev-boundary"],
    "ato": ["icdev-comply", "icdev-secure"],
    "security": ["icdev-secure", "icdev-boundary", "icdev-review"],
    "devops": ["icdev-deploy", "icdev-monitor"],
    "research": ["icdev-knowledge", "icdev-innovate"],
    "modernize": ["icdev-modernize", "icdev-build"],
    "monitor": ["icdev-monitor", "icdev-maintain"],
    "saas": ["icdev-build", "icdev-deploy", "icdev-monitor"],
    "ai": ["icdev-innovate", "icdev-build", "icdev-knowledge"],
    "govcon": ["icdev-intake", "icdev-comply"],
}

_MATURITY_GOALS: dict[str, list[str]] = {
    "none": ["init_project.md", "requirements_intake.md", "framework_planning.md"],
    "pilot": ["tdd_workflow.md", "code_review.md", "integration_testing.md"],
    "scaling": [
        "multi_agent_orchestration.md",
        "evolutionary_intelligence.md",
        "continuous_harmonization.md",
    ],
}

_MATURITY_SKILLS: dict[str, list[str]] = {
    "none": ["icdev-init", "icdev-intake"],
    "pilot": ["icdev-build", "icdev-test", "icdev-review"],
    "scaling": ["icdev-monitor", "icdev-maintain", "icdev-deploy"],
}

_CLOUD_GOALS: dict[str, list[str]] = {
    "aws": ["deploy_workflow.md", "cloud_agnostic.md"],
    "aws_govcloud": ["deploy_workflow.md", "cloud_agnostic.md", "zero_trust_architecture.md"],
    "azure": ["deploy_workflow.md", "cloud_agnostic.md"],
    "azure_government": ["deploy_workflow.md", "cloud_agnostic.md", "zero_trust_architecture.md"],
    "gcp": ["deploy_workflow.md", "cloud_agnostic.md"],
    "local": ["cicd_integration.md"],
    "on_prem": ["cicd_integration.md", "zero_trust_architecture.md"],
}

_CLOUD_SKILLS: dict[str, list[str]] = {
    "aws": ["icdev-deploy"],
    "aws_govcloud": ["icdev-deploy", "icdev-secure"],
    "azure": ["icdev-deploy"],
    "azure_government": ["icdev-deploy", "icdev-secure"],
    "gcp": ["icdev-deploy"],
    "local": ["icdev-deploy"],
    "on_prem": ["icdev-deploy", "icdev-secure"],
}

# Estimated compliance setup hours by level
_COMPLIANCE_HOURS: dict[str, int] = {
    "IL2": 4,
    "IL4": 8,
    "IL5": 12,
    "FedRAMP": 16,
    "CMMC": 12,
}

# Default fallback goals / skills when use_case key is not in table
_DEFAULT_USE_CASE_GOALS = ["build_app.md", "tdd_workflow.md"]
_DEFAULT_USE_CASE_SKILLS = ["icdev-build", "icdev-test"]


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _normalise_key(value: str) -> str:
    return value.lower().replace(" ", "_").replace("-", "_")


def _deduplicated(items: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for item in items:
        if item not in seen:
            seen.add(item)
            out.append(item)
    return out


def _infer_language(tech_stack: str) -> str:
    ts = tech_stack.lower()
    if "typescript" in ts or ts == "ts":
        return "typescript"
    if "javascript" in ts or ts == "js":
        return "javascript"
    if "java" in ts and "script" not in ts:
        return "java"
    if ts in ("go", "golang"):
        return "go"
    if "rust" in ts:
        return "rust"
    if "csharp" in ts or "c#" in ts or ".net" in ts:
        return "csharp"
    return "python"


def _build_args_overrides(answers: dict[str, str]) -> dict[str, Any]:
    compliance_level = answers.get("compliance_level", "IL2")
    cloud_provider = answers.get("cloud_provider", "local")
    ai_maturity = answers.get("ai_maturity", "none")
    tech_stack = answers.get("tech_stack", "python")

    # Derive numeric IL level for cross-framework checks
    if compliance_level.startswith("IL") and compliance_level[2:].isdigit():
        il_level = int(compliance_level[2:])
    else:
        il_level = 4  # FedRAMP / CMMC map to IL4 equivalent

    return {
        "compliance": {
            "level": compliance_level,
            "il_level": il_level,
            "fedramp_enabled": compliance_level == "FedRAMP",
            "cmmc_enabled": compliance_level == "CMMC",
        },
        "cloud": {
            "provider": _normalise_key(cloud_provider),
            "govcloud": "gov" in cloud_provider.lower() or "government" in cloud_provider.lower(),
        },
        "ai": {
            "maturity": ai_maturity,
            "self_healing_enabled": ai_maturity == "scaling",
            "multi_agent_enabled": ai_maturity in ("pilot", "scaling"),
        },
        "tech_stack": {
            "primary_language": _infer_language(tech_stack),
            "stack": tech_stack,
        },
    }


def _build_sprint_tasks(
    use_case: str,
    compliance_level: str,
    tech_stack: str,
    ai_maturity: str,
    cloud_provider: str,
) -> list[dict[str, Any]]:
    tasks: list[dict[str, Any]] = []
    phase = 1

    # Phase 1 — Foundation (only for teams starting from scratch)
    if ai_maturity == "none":
        tasks.append(
            {
                "phase": phase,
                "id": f"wiz-p{phase}-init",
                "title": "Initialize ICDEV™ project structure",
                "type": "chore",
                "priority": "high",
                "estimated_hours": 2,
                "skill": "icdev-init",
            }
        )
        tasks.append(
            {
                "phase": phase,
                "id": f"wiz-p{phase}-intake",
                "title": "Run AI-driven requirements intake",
                "type": "research",
                "priority": "high",
                "estimated_hours": 4,
                "skill": "icdev-intake",
            }
        )

    phase += 1

    # Phase 2 — Compliance setup
    compliance_hours = _COMPLIANCE_HOURS.get(compliance_level, 8)
    tasks.append(
        {
            "phase": phase,
            "id": f"wiz-p{phase}-comply",
            "title": f"Configure {compliance_level} compliance controls",
            "type": "compliance",
            "priority": "high",
            "estimated_hours": compliance_hours,
            "skill": "icdev-comply",
        }
    )
    if compliance_level in ("IL4", "IL5", "FedRAMP", "CMMC"):
        tasks.append(
            {
                "phase": phase,
                "id": f"wiz-p{phase}-boundary",
                "title": "Define system boundary and ATO boundary assessment",
                "type": "compliance",
                "priority": "high",
                "estimated_hours": 6,
                "skill": "icdev-boundary",
            }
        )

    phase += 1

    # Phase 3 — Core build
    tasks.append(
        {
            "phase": phase,
            "id": f"wiz-p{phase}-build",
            "title": f"Build core {use_case} application ({tech_stack})",
            "type": "build",
            "priority": "high",
            "estimated_hours": 16,
            "skill": "icdev-build",
        }
    )
    tasks.append(
        {
            "phase": phase,
            "id": f"wiz-p{phase}-test",
            "title": "Implement TDD/BDD test suite",
            "type": "test",
            "priority": "high",
            "estimated_hours": 8,
            "skill": "icdev-test",
        }
    )

    phase += 1

    # Phase 4 — Security gate
    tasks.append(
        {
            "phase": phase,
            "id": f"wiz-p{phase}-secure",
            "title": "Run SAST / dependency security scan",
            "type": "security",
            "priority": "high",
            "estimated_hours": 4,
            "skill": "icdev-secure",
        }
    )
    tasks.append(
        {
            "phase": phase,
            "id": f"wiz-p{phase}-review",
            "title": "Code review gate (security + quality)",
            "type": "review",
            "priority": "medium",
            "estimated_hours": 3,
            "skill": "icdev-review",
        }
    )

    phase += 1

    # Phase 5 — Deploy
    tasks.append(
        {
            "phase": phase,
            "id": f"wiz-p{phase}-deploy",
            "title": f"Deploy to {cloud_provider} ({compliance_level})",
            "type": "deploy",
            "priority": "high",
            "estimated_hours": 6,
            "skill": "icdev-deploy",
        }
    )

    phase += 1

    # Phase 6 — Operations (pilot/scaling only)
    if ai_maturity in ("pilot", "scaling"):
        tasks.append(
            {
                "phase": phase,
                "id": f"wiz-p{phase}-monitor",
                "title": "Configure production monitoring and alerting",
                "type": "monitor",
                "priority": "medium",
                "estimated_hours": 4,
                "skill": "icdev-monitor",
            }
        )

    if ai_maturity == "scaling":
        tasks.append(
            {
                "phase": phase,
                "id": f"wiz-p{phase}-maintain",
                "title": "Enable self-healing and autonomous maintenance",
                "type": "chore",
                "priority": "medium",
                "estimated_hours": 6,
                "skill": "icdev-maintain",
            }
        )

    return tasks


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


class WizardEngine:
    """Maps 5 wizard answers to goals, skills, and a sprint plan.

    No LLM calls — pure deterministic rule-based mapping.
    """

    def process(self, session_token: str, answers: dict[str, str]) -> dict[str, Any]:
        """Process wizard answers and persist generated args to DB.

        Args:
            session_token: Unique identifier for this wizard session.
            answers: Dict with keys use_case, compliance_level, tech_stack,
                     ai_maturity, cloud_provider.

        Returns:
            Dict with recommended_goals (list[str]), recommended_skills (list[str]),
            and sprint_plan_tasks (list[dict]).
        """
        use_case = answers.get("use_case", "web_app")
        compliance_level = answers.get("compliance_level", "IL2")
        tech_stack = answers.get("tech_stack", "python")
        ai_maturity = answers.get("ai_maturity", "none")
        cloud_provider = answers.get("cloud_provider", "local")

        use_case_key = _normalise_key(use_case)
        cloud_key = _normalise_key(cloud_provider)

        goals = _deduplicated(
            _USE_CASE_GOALS.get(use_case_key, _DEFAULT_USE_CASE_GOALS)
            + _COMPLIANCE_GOALS.get(compliance_level, [])
            + _MATURITY_GOALS.get(ai_maturity, [])
            + _CLOUD_GOALS.get(cloud_key, [])
        )

        skills = _deduplicated(
            _USE_CASE_SKILLS.get(use_case_key, _DEFAULT_USE_CASE_SKILLS)
            + _COMPLIANCE_SKILLS.get(compliance_level, [])
            + _MATURITY_SKILLS.get(ai_maturity, [])
            + _CLOUD_SKILLS.get(cloud_key, [])
        )

        sprint_tasks = _build_sprint_tasks(
            use_case=use_case,
            compliance_level=compliance_level,
            tech_stack=tech_stack,
            ai_maturity=ai_maturity,
            cloud_provider=cloud_provider,
        )

        args_overrides = _build_args_overrides(answers)
        generated_args_json = json.dumps(args_overrides)

        conn = get_connection()
        try:
            ph = sql_placeholder(conn)
            conn.execute(
                f"""INSERT OR REPLACE INTO aisg_wizard_sessions
                    (session_token, use_case, compliance_level, tech_stack,
                     ai_maturity, cloud_provider, generated_args_json)
                VALUES ({ph}, {ph}, {ph}, {ph}, {ph}, {ph}, {ph})""",
                (
                    session_token,
                    use_case,
                    compliance_level,
                    tech_stack,
                    ai_maturity,
                    cloud_provider,
                    generated_args_json,
                ),
            )
            conn.commit()
        finally:
            conn.close()

        return {
            "recommended_goals": goals,
            "recommended_skills": skills,
            "sprint_plan_tasks": sprint_tasks,
        }
