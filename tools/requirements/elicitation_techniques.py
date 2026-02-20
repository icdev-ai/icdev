#!/usr/bin/env python3
# CUI // SP-CTI
# Controlled by: Department of Defense
# CUI Category: CTI
# Distribution: D
# POC: ICDEV System Administrator
"""Elicitation techniques menu for requirements intake (BMAD pattern).

Provides named reasoning methods that the analyst can invoke during
conversation to probe deeper into specific requirement areas. Each
technique includes a system prompt injection and follow-up questions
that guide the LLM persona.

Techniques are drawn from BMAD's elicitation framework adapted for
government/defense requirements gathering.

Usage:
    from tools.requirements.elicitation_techniques import (
        list_techniques, get_technique, activate_technique,
    )
    techniques = list_techniques()
    result = activate_technique(session_id, "pre_mortem", db_path=DB_PATH)
"""

import json
import sqlite3
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent.parent
DB_PATH = BASE_DIR / "data" / "icdev.db"


# ---------------------------------------------------------------------------
# Technique Catalog
# ---------------------------------------------------------------------------

TECHNIQUES = {
    "pre_mortem": {
        "name": "Pre-Mortem Analysis",
        "icon": "skull",
        "short": "Imagine the project failed. What went wrong?",
        "description": (
            "Assume the project has already failed catastrophically. "
            "Ask the customer to describe what went wrong, what was missed, "
            "and what assumptions proved false. This surfaces hidden risks "
            "and missing requirements."
        ),
        "system_prompt": (
            "TECHNIQUE ACTIVE: Pre-Mortem Analysis\n"
            "Imagine the project has failed 6 months from now. Ask the customer:\n"
            "- What went wrong? What was the root cause of failure?\n"
            "- What requirements were missing or underspecified?\n"
            "- What assumptions turned out to be wrong?\n"
            "- What stakeholder needs were overlooked?\n"
            "- What compliance or security gap caused the failure?\n"
            "Frame questions as: 'Looking back at the failure, what do you wish "
            "we had addressed during requirements?'\n"
            "Extract each answer as a risk-mitigation requirement."
        ),
        "suggested_questions": [
            "If this project failed in 6 months, what's the most likely reason?",
            "What's the biggest assumption we're making that could be wrong?",
            "What stakeholder group are we not hearing from?",
            "What security or compliance requirement might we be missing?",
        ],
        "targets": ["risk_profile", "completeness"],
        "category": "risk",
    },
    "first_principles": {
        "name": "First Principles",
        "icon": "lightbulb",
        "short": "Break down to fundamentals. What must be true?",
        "description": (
            "Strip away assumptions and industry jargon. Decompose the "
            "problem to its fundamental truths. Rebuild requirements "
            "from basic principles rather than analogy to existing systems."
        ),
        "system_prompt": (
            "TECHNIQUE ACTIVE: First Principles Thinking\n"
            "Help the customer decompose their problem to fundamentals:\n"
            "- What is the core problem being solved? (not the solution)\n"
            "- Who are the actual end users and what do they need?\n"
            "- What are the immutable constraints (physics, regulations, budget)?\n"
            "- What are assumed constraints that could be challenged?\n"
            "- If you built this from scratch with no existing systems, what "
            "would the minimum viable solution look like?\n"
            "Challenge statements like 'we need X because that's how it's "
            "always been done.' Push for the underlying need."
        ),
        "suggested_questions": [
            "What's the core problem you're solving, ignoring how it's done today?",
            "If you had to explain the need to someone who knows nothing about your domain, what would you say?",
            "Which of your current constraints are truly immutable vs. assumed?",
            "What's the simplest possible solution that solves the core problem?",
        ],
        "targets": ["clarity", "feasibility"],
        "category": "analysis",
    },
    "red_team": {
        "name": "Red Team / Adversarial",
        "icon": "shield",
        "short": "How would an adversary attack this system?",
        "description": (
            "Adopt an adversary's mindset. Identify how the system could "
            "be attacked, misused, or circumvented. Surfaces security "
            "requirements, edge cases, and abuse scenarios."
        ),
        "system_prompt": (
            "TECHNIQUE ACTIVE: Red Team / Adversarial Analysis\n"
            "Think like an adversary trying to compromise, misuse, or "
            "circumvent this system. Ask the customer:\n"
            "- How could a malicious insider abuse this system?\n"
            "- What data could an attacker steal if they got in?\n"
            "- How could someone bypass authentication or authorization?\n"
            "- What would happen if a dependency was compromised?\n"
            "- How could the system be used in ways you didn't intend?\n"
            "- What happens during a denial-of-service attack?\n"
            "Convert each attack scenario into a security requirement or "
            "a negative test case (Given attacker does X, system prevents Y)."
        ),
        "suggested_questions": [
            "How could a malicious insider misuse this system?",
            "What's the most valuable data an attacker would target?",
            "What happens if an external API or dependency goes down?",
            "How could someone bypass the intended access controls?",
        ],
        "targets": ["compliance", "completeness"],
        "category": "security",
    },
    "socratic": {
        "name": "Socratic Questioning",
        "icon": "help-circle",
        "short": "Challenge assumptions through guided questions.",
        "description": (
            "Use Socratic method to challenge unstated assumptions, "
            "clarify vague terms, and drive toward measurable criteria. "
            "Particularly effective for improving clarity and testability."
        ),
        "system_prompt": (
            "TECHNIQUE ACTIVE: Socratic Questioning\n"
            "Challenge every vague or assumed statement with 'why?' and "
            "'how would you measure that?':\n"
            "- When the customer says 'fast' — ask 'How fast? What's "
            "the maximum acceptable response time?'\n"
            "- When they say 'secure' — ask 'Secure against what threat? "
            "What's the impact if it's breached?'\n"
            "- When they say 'user-friendly' — ask 'For which user? What "
            "task should take how many clicks/seconds?'\n"
            "- When they say 'scalable' — ask 'How many concurrent users? "
            "What growth rate? Over what time period?'\n"
            "Never accept qualitative terms. Push for quantitative criteria. "
            "Each clarification should yield a measurable requirement."
        ),
        "suggested_questions": [
            "When you say 'fast', what response time would be acceptable?",
            "How would you verify that this requirement is met?",
            "What happens if this requirement isn't implemented?",
            "Can you define 'secure' in terms of specific threats and impacts?",
        ],
        "targets": ["clarity", "testability"],
        "category": "analysis",
    },
    "stakeholder_map": {
        "name": "Stakeholder Mapping",
        "icon": "users",
        "short": "Who are all the affected users and roles?",
        "description": (
            "Systematically identify every stakeholder group: direct users, "
            "indirect users, administrators, auditors, adversaries, and "
            "affected bystanders. Each group reveals different requirements."
        ),
        "system_prompt": (
            "TECHNIQUE ACTIVE: Stakeholder Mapping\n"
            "Systematically identify all stakeholder groups:\n"
            "- Primary users: Who uses this daily? What are their top tasks?\n"
            "- Secondary users: Who uses it occasionally? For what?\n"
            "- Administrators: Who manages, configures, or monitors it?\n"
            "- Auditors/compliance: Who reviews it for security/compliance?\n"
            "- Upstream systems: What feeds data into this system?\n"
            "- Downstream consumers: Who depends on this system's output?\n"
            "- Adversaries: Who would want to attack or misuse it?\n"
            "- Affected parties: Who is impacted but doesn't directly use it?\n"
            "For each stakeholder group, ask: 'What does <group> need from "
            "this system that we haven't captured yet?'"
        ),
        "suggested_questions": [
            "Besides the primary users, who else interacts with this system?",
            "Who is responsible for system administration and monitoring?",
            "What other systems feed data into or consume data from this one?",
            "Who audits this system for compliance, and what do they need?",
        ],
        "targets": ["completeness", "feasibility"],
        "category": "scope",
    },
    "mission_thread": {
        "name": "Mission Thread Analysis",
        "icon": "target",
        "short": "Trace the end-to-end mission workflow.",
        "description": (
            "Trace the complete mission thread from trigger to outcome. "
            "Identify every step, handoff, decision point, and failure mode "
            "in the operational workflow. DoD-specific technique."
        ),
        "system_prompt": (
            "TECHNIQUE ACTIVE: Mission Thread Analysis\n"
            "Trace the complete operational workflow end-to-end:\n"
            "- What event triggers the workflow? (alert, schedule, request)\n"
            "- What is the first action taken? By whom?\n"
            "- What data flows between steps? In what format?\n"
            "- Where are the decision points? What criteria drive decisions?\n"
            "- What handoffs exist between roles or systems?\n"
            "- What are the failure modes at each step?\n"
            "- What is the expected outcome? How is success measured?\n"
            "- What is the fallback if the primary path fails?\n"
            "Each step and handoff should yield at least one requirement. "
            "Pay special attention to timing constraints and data freshness."
        ),
        "suggested_questions": [
            "Walk me through the complete workflow from trigger to outcome.",
            "At each step, what data is needed and where does it come from?",
            "What happens when a step fails or times out?",
            "What are the timing constraints — how fast must each step complete?",
        ],
        "targets": ["completeness", "testability"],
        "category": "operations",
    },
    "constraint_inversion": {
        "name": "Constraint Inversion",
        "icon": "rotate-ccw",
        "short": "What if we removed key constraints?",
        "description": (
            "Temporarily remove assumed constraints one by one. "
            "What would the ideal solution look like with no budget limit? "
            "No timeline? No legacy system? This reveals which constraints "
            "are truly fixed vs. negotiable."
        ),
        "system_prompt": (
            "TECHNIQUE ACTIVE: Constraint Inversion\n"
            "Temporarily remove one constraint at a time and ask what changes:\n"
            "- 'If budget were unlimited, what would you add?'\n"
            "- 'If timeline were unlimited, what would you do differently?'\n"
            "- 'If you didn't have to integrate with <legacy system>, what "
            "would you build?'\n"
            "- 'If there were no regulatory requirements, what would you "
            "simplify?'\n"
            "- 'If you could change the organizational process, what would "
            "you fix?'\n"
            "For each answer, determine: is this a MUST-have that the "
            "constraint is blocking (log as a gap), or a NICE-to-have "
            "that can be deferred?"
        ),
        "suggested_questions": [
            "If budget were unlimited, what capability would you add first?",
            "If you didn't have to integrate with existing systems, what would change?",
            "Which regulatory requirement constrains the design the most?",
            "What process or policy constraint could be changed to simplify this?",
        ],
        "targets": ["feasibility", "clarity"],
        "category": "analysis",
    },
    "day_in_life": {
        "name": "Day-in-the-Life",
        "icon": "clock",
        "short": "Walk through a user's typical workday.",
        "description": (
            "Have the customer describe a typical day for each user role. "
            "What tasks do they perform? What tools do they use? Where are "
            "the pain points? This surfaces usability and workflow requirements."
        ),
        "system_prompt": (
            "TECHNIQUE ACTIVE: Day-in-the-Life\n"
            "Walk through a typical day for each user role:\n"
            "- What is the first thing they do when they start work?\n"
            "- What tools and systems do they use throughout the day?\n"
            "- Where do they waste the most time? What's frustrating?\n"
            "- What repetitive tasks could be automated?\n"
            "- When do they need this system? For how long? How often?\n"
            "- What does a 'good day' vs 'bad day' look like?\n"
            "- What workarounds do they currently use?\n"
            "Each pain point and workaround should become a requirement. "
            "Focus on frequency, duration, and impact."
        ),
        "suggested_questions": [
            "Walk me through a typical day for the primary user of this system.",
            "Where do users currently waste the most time or get frustrated?",
            "What workarounds do people use because the current system doesn't support their needs?",
            "How many times per day would a user interact with this system?",
        ],
        "targets": ["completeness", "clarity"],
        "category": "scope",
    },
}


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def list_techniques(category=None):
    """List all available elicitation techniques.

    Args:
        category: Optional filter by category (risk, analysis, security,
                  scope, operations).

    Returns:
        List of technique summary dicts.
    """
    result = []
    for tech_id, tech in TECHNIQUES.items():
        if category and tech.get("category") != category:
            continue
        result.append({
            "id": tech_id,
            "name": tech["name"],
            "icon": tech["icon"],
            "short": tech["short"],
            "category": tech.get("category", "general"),
            "targets": tech.get("targets", []),
        })
    return result


def get_technique(technique_id):
    """Get full technique definition.

    Args:
        technique_id: Technique key (e.g. "pre_mortem").

    Returns:
        Full technique dict or None if not found.
    """
    tech = TECHNIQUES.get(technique_id)
    if not tech:
        return None
    return {
        "id": technique_id,
        **tech,
    }


def _get_connection(db_path=None):
    """Get database connection."""
    path = db_path or DB_PATH
    if not path.exists():
        raise FileNotFoundError(f"Database not found: {path}")
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    return conn


def activate_technique(session_id, technique_id, db_path=None):
    """Activate an elicitation technique for a session.

    Stores the technique in the session's context_summary so the LLM
    persona can incorporate it in subsequent responses.

    Args:
        session_id: Intake session ID.
        technique_id: Technique key from TECHNIQUES catalog.
        db_path: Optional database path override.

    Returns:
        Dict with status, technique info, and suggested first question.
    """
    tech = TECHNIQUES.get(technique_id)
    if not tech:
        return {"status": "error", "error": f"Unknown technique: {technique_id}"}

    conn = _get_connection(db_path)
    try:
        session = conn.execute(
            "SELECT * FROM intake_sessions WHERE id = ?", (session_id,)
        ).fetchone()
        if not session:
            return {"status": "error", "error": f"Session not found: {session_id}"}

        # Update context_summary with active technique
        context = {}
        try:
            context = json.loads(dict(session).get("context_summary") or "{}")
        except (ValueError, TypeError):
            pass

        # Record technique activation history
        history = context.get("technique_history", [])
        history.append(technique_id)
        context["technique_history"] = history
        context["active_technique"] = technique_id
        context["active_technique_prompt"] = tech["system_prompt"]

        conn.execute(
            "UPDATE intake_sessions SET context_summary = ? WHERE id = ?",
            (json.dumps(context), session_id),
        )
        conn.commit()

        return {
            "status": "ok",
            "session_id": session_id,
            "technique": {
                "id": technique_id,
                "name": tech["name"],
                "description": tech["description"],
                "targets": tech.get("targets", []),
            },
            "suggested_questions": tech.get("suggested_questions", []),
            "message": (
                f"Technique activated: {tech['name']}. "
                f"{tech['short']} Try one of the suggested questions."
            ),
        }
    except Exception as exc:
        return {"status": "error", "error": str(exc)}
    finally:
        conn.close()


def deactivate_technique(session_id, db_path=None):
    """Deactivate the current technique for a session.

    Args:
        session_id: Intake session ID.
        db_path: Optional database path override.

    Returns:
        Dict with status.
    """
    conn = _get_connection(db_path)
    try:
        session = conn.execute(
            "SELECT * FROM intake_sessions WHERE id = ?", (session_id,)
        ).fetchone()
        if not session:
            return {"status": "error", "error": f"Session not found: {session_id}"}

        context = {}
        try:
            context = json.loads(dict(session).get("context_summary") or "{}")
        except (ValueError, TypeError):
            pass

        context.pop("active_technique", None)
        context.pop("active_technique_prompt", None)

        conn.execute(
            "UPDATE intake_sessions SET context_summary = ? WHERE id = ?",
            (json.dumps(context), session_id),
        )
        conn.commit()

        return {"status": "ok", "session_id": session_id, "message": "Technique deactivated."}
    except Exception as exc:
        return {"status": "error", "error": str(exc)}
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    """CLI entry point."""
    import argparse

    parser = argparse.ArgumentParser(description="ICDEV Elicitation Techniques")
    parser.add_argument("--list", action="store_true", help="List all techniques")
    parser.add_argument("--category", help="Filter by category")
    parser.add_argument("--get", help="Get full technique by ID")
    parser.add_argument("--activate", help="Activate technique for session")
    parser.add_argument("--deactivate", action="store_true", help="Deactivate technique")
    parser.add_argument("--session-id", help="Session ID (for activate/deactivate)")
    parser.add_argument("--json", action="store_true", help="JSON output")
    args = parser.parse_args()

    if args.list:
        result = list_techniques(category=args.category)
        if args.json:
            print(json.dumps(result, indent=2))
        else:
            for t in result:
                print(f"  {t['id']:25s} {t['name']:30s} [{t['category']}]")
                print(f"  {'':25s} {t['short']}")
                print()
    elif args.get:
        tech = get_technique(args.get)
        if tech:
            if args.json:
                print(json.dumps(tech, indent=2))
            else:
                print(f"Name: {tech['name']}")
                print(f"Category: {tech.get('category', 'general')}")
                print(f"Description: {tech['description']}")
                print(f"Targets: {', '.join(tech.get('targets', []))}")
                print("\nSuggested questions:")
                for q in tech.get("suggested_questions", []):
                    print(f"  - {q}")
        else:
            print(f"Technique not found: {args.get}")
    elif args.activate and args.session_id:
        result = activate_technique(args.session_id, args.activate)
        if args.json:
            print(json.dumps(result, indent=2))
        else:
            print(result.get("message", result.get("error", "Unknown")))
    elif args.deactivate and args.session_id:
        result = deactivate_technique(args.session_id)
        if args.json:
            print(json.dumps(result, indent=2))
        else:
            print(result.get("message", result.get("error", "Unknown")))
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
