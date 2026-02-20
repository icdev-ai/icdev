# CUI // SP-CTI
"""PRD (Product Requirements Document) generator.

Assembles all intake pipeline data (session, requirements, readiness,
conversation, decomposition, COAs, gaps) into a single formatted PRD
document in Markdown.

Usage:
    python tools/requirements/prd_generator.py --session-id <id> [--json]
"""
from __future__ import annotations

import argparse
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent.parent
DB_PATH = BASE_DIR / "data" / "icdev.db"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _get_connection(db_path=None):
    p = str(db_path or DB_PATH)
    conn = sqlite3.connect(p)
    conn.row_factory = sqlite3.Row
    return conn


def _safe_json(text: str | None) -> dict | list | None:
    """Parse a JSON column, returning None on failure."""
    if not text:
        return None
    try:
        return json.loads(text)
    except (json.JSONDecodeError, TypeError):
        return None


def _pct(value: float | None) -> str:
    if value is None:
        return "N/A"
    return f"{value * 100:.0f}%"


# ---------------------------------------------------------------------------
# Data loaders
# ---------------------------------------------------------------------------

def _load_session(conn, session_id: str) -> dict | None:
    row = conn.execute(
        "SELECT * FROM intake_sessions WHERE id = ?", (session_id,)
    ).fetchone()
    return dict(row) if row else None


def _load_requirements(conn, session_id: str) -> list[dict]:
    rows = conn.execute(
        "SELECT * FROM intake_requirements WHERE session_id = ? ORDER BY priority, created_at",
        (session_id,),
    ).fetchall()
    return [dict(r) for r in rows]


def _load_conversation(conn, session_id: str) -> list[dict]:
    rows = conn.execute(
        "SELECT * FROM intake_conversation WHERE session_id = ? ORDER BY turn_number",
        (session_id,),
    ).fetchall()
    return [dict(r) for r in rows]


def _load_readiness(conn, session_id: str) -> dict | None:
    row = conn.execute(
        "SELECT * FROM readiness_scores WHERE session_id = ? ORDER BY scored_at DESC LIMIT 1",
        (session_id,),
    ).fetchone()
    return dict(row) if row else None


def _load_decomposition(conn, session_id: str) -> list[dict]:
    rows = conn.execute(
        "SELECT * FROM safe_decomposition WHERE session_id = ? ORDER BY level, wsjf_score DESC",
        (session_id,),
    ).fetchall()
    return [dict(r) for r in rows]


def _load_selected_coa(conn, session_id: str) -> dict | None:
    row = conn.execute(
        "SELECT * FROM coa_definitions WHERE session_id = ? AND status = 'selected' LIMIT 1",
        (session_id,),
    ).fetchone()
    if not row:
        # Fall back to recommended (balanced)
        row = conn.execute(
            "SELECT * FROM coa_definitions WHERE session_id = ? ORDER BY "
            "CASE coa_type WHEN 'balanced' THEN 0 WHEN 'comprehensive' THEN 1 "
            "WHEN 'speed' THEN 2 ELSE 3 END LIMIT 1",
            (session_id,),
        ).fetchone()
    return dict(row) if row else None


def _load_all_coas(conn, session_id: str) -> list[dict]:
    rows = conn.execute(
        "SELECT * FROM coa_definitions WHERE session_id = ? ORDER BY "
        "CASE coa_type WHEN 'speed' THEN 0 WHEN 'balanced' THEN 1 "
        "WHEN 'comprehensive' THEN 2 ELSE 3 END, created_at DESC",
        (session_id,),
    ).fetchall()
    # Deduplicate: keep latest per coa_type
    seen: dict[str, dict] = {}
    for r in rows:
        d = dict(r)
        ctype = d.get("coa_type", "")
        if ctype not in seen:
            seen[ctype] = d
    return list(seen.values())


def _load_documents(conn, session_id: str) -> list[dict]:
    rows = conn.execute(
        "SELECT * FROM intake_documents WHERE session_id = ? ORDER BY uploaded_at",
        (session_id,),
    ).fetchall()
    return [dict(r) for r in rows]


# ---------------------------------------------------------------------------
# Section renderers
# ---------------------------------------------------------------------------

_PRIORITY_ORDER = {"critical": 0, "high": 1, "medium": 2, "low": 3}


def _render_cover(session: dict, readiness: dict | None, req_count: int) -> str:
    ctx = _safe_json(session.get("context_summary")) or {}
    classification = session.get("classification", "CUI")
    impact = session.get("impact_level", "IL5")
    customer = session.get("customer_name", "Unknown")
    org = session.get("customer_org", "")
    date = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    score = readiness["overall_score"] if readiness else session.get("readiness_score", 0)

    lines = [
        f"# CUI // SP-CTI",
        f"# Product Requirements Document (PRD)",
        "",
        f"| Field | Value |",
        f"|-------|-------|",
        f"| **Session** | `{session['id']}` |",
        f"| **Customer** | {customer}{(' — ' + org) if org else ''} |",
        f"| **Classification** | {classification} |",
        f"| **Impact Level** | {impact} |",
        f"| **Total Requirements** | {req_count} |",
        f"| **Readiness Score** | {_pct(score)} |",
        f"| **Date** | {date} |",
        f"| **Status** | {session.get('session_status', 'active').title()} |",
    ]

    frameworks = ctx.get("selected_frameworks") or []
    if frameworks:
        fw_str = ", ".join(f.replace("_", " ").title() for f in frameworks)
        lines.append(f"| **Compliance Frameworks** | {fw_str} |")

    lines.append("")
    return "\n".join(lines)


def _render_executive_summary(
    session: dict, reqs: list[dict], readiness: dict | None, conversation: list[dict],
) -> str:
    ctx = _safe_json(session.get("context_summary")) or {}
    goal = ctx.get("goal", "build")
    role = ctx.get("role", "developer")

    # Extract problem statement from first customer messages
    problem_turns = [
        t["content"] for t in conversation
        if t["role"] == "customer" and t["turn_number"] <= 3
    ]
    problem_statement = " ".join(problem_turns)[:500] if problem_turns else "Not captured."

    type_counts = {}
    for r in reqs:
        t = r.get("requirement_type", "functional")
        type_counts[t] = type_counts.get(t, 0) + 1

    priority_counts = {}
    for r in reqs:
        p = r.get("priority", "medium")
        priority_counts[p] = priority_counts.get(p, 0) + 1

    score = readiness["overall_score"] if readiness else session.get("readiness_score", 0)

    lines = [
        "## 1. Executive Summary",
        "",
        f"**Project Goal:** {goal.replace('_', ' ').title()}",
        f"**Primary Stakeholder Role:** {role.replace('_', ' ').title()}",
        "",
        "### Problem Statement",
        "",
        f"> {problem_statement}",
        "",
        "### Requirements Overview",
        "",
        f"| Priority | Count |",
        f"|----------|-------|",
    ]
    for p in ["critical", "high", "medium", "low"]:
        if priority_counts.get(p, 0) > 0:
            lines.append(f"| {p.title()} | {priority_counts[p]} |")
    lines.append("")

    lines.append(f"| Type | Count |")
    lines.append(f"|------|-------|")
    for t, c in sorted(type_counts.items(), key=lambda x: -x[1]):
        lines.append(f"| {t.replace('_', ' ').title()} | {c} |")
    lines.append("")

    lines.append(f"**Readiness Score:** {_pct(score)}")
    if readiness:
        lines.append("")
        lines.append(f"| Dimension | Score |")
        lines.append(f"|-----------|-------|")
        for dim in ["completeness", "clarity", "feasibility", "compliance", "testability"]:
            lines.append(f"| {dim.title()} | {_pct(readiness.get(dim, 0))} |")
    lines.append("")
    return "\n".join(lines)


def _render_requirements(reqs: list[dict]) -> str:
    """Render requirements grouped by type."""
    lines = ["## 2. Requirements", ""]

    grouped: dict[str, list[dict]] = {}
    for r in reqs:
        t = r.get("requirement_type", "functional")
        grouped.setdefault(t, []).append(r)

    # Render in a logical order
    type_order = [
        "functional", "security", "performance", "interface",
        "data", "compliance", "non_functional", "constraint",
        "operational", "transitional",
    ]
    section_num = 1
    for req_type in type_order:
        type_reqs = grouped.get(req_type)
        if not type_reqs:
            continue
        label = req_type.replace("_", " ").title()
        lines.append(f"### 2.{section_num} {label} Requirements")
        lines.append("")
        lines.append(f"| ID | Requirement | Priority | Status |")
        lines.append(f"|----|-------------|----------|--------|")
        for r in sorted(type_reqs, key=lambda x: _PRIORITY_ORDER.get(x.get("priority", "medium"), 2)):
            rid = r.get("id", "—")
            text = (r.get("refined_text") or r.get("raw_text", ""))[:120]
            pri = r.get("priority", "medium").title()
            status = r.get("status", "draft").title()
            lines.append(f"| `{rid}` | {text} | {pri} | {status} |")
        lines.append("")
        section_num += 1

    return "\n".join(lines)


def _render_user_journeys(
    session: dict, reqs: list[dict], conversation: list[dict],
) -> str:
    """Synthesize user journeys from conversation context and requirements.

    Derives journey narratives by grouping requirements by type and mapping
    the customer's stated workflow from conversation turns.
    """
    ctx = _safe_json(session.get("context_summary")) or {}
    role = ctx.get("role", "user").replace("_", " ").title()
    custom_role = ctx.get("custom_role_name", "")
    if custom_role:
        role = custom_role

    # Extract workflow descriptions from customer turns
    workflow_turns = []
    for t in conversation:
        if t["role"] == "customer":
            text = t.get("content", "")
            # Look for workflow/process language
            if any(kw in text.lower() for kw in [
                "workflow", "process", "step", "then", "after", "before",
                "first", "next", "finally", "need to", "want to", "will",
                "login", "log in", "upload", "submit", "review", "approve",
                "search", "create", "delete", "view", "edit", "report",
            ]):
                workflow_turns.append(text)

    if not reqs and not workflow_turns:
        return ""

    lines = [
        "## 3. User Journeys",
        "",
        f"*Derived from {len(conversation)} conversation turns and "
        f"{len(reqs)} requirements.*",
        "",
    ]

    # Journey 1: Primary user workflow (from conversation)
    if workflow_turns:
        lines.append(f"### Journey 1: {role} — Primary Workflow")
        lines.append("")
        lines.append(f"**Persona:** {role}")
        lines.append(f"**Goal:** {ctx.get('goal', 'build').replace('_', ' ').title()}")
        lines.append("")
        # Reconstruct the workflow narrative from customer turns
        for i, turn in enumerate(workflow_turns[:5], 1):
            # Truncate long turns
            snippet = turn[:200].replace("\n", " ").strip()
            if len(turn) > 200:
                snippet += "..."
            lines.append(f"{i}. {snippet}")
        lines.append("")

    # Journey 2+: Derive from requirement types (functional groupings)
    type_groups: dict[str, list[dict]] = {}
    for r in reqs:
        rtype = r.get("requirement_type", "functional")
        type_groups.setdefault(rtype, []).append(r)

    journey_num = 2 if workflow_turns else 1
    journey_map = {
        "security": ("Security Administrator", "Secure the system",
                     "configure authentication, manage access controls, and monitor security events"),
        "performance": ("Operations Engineer", "Monitor system health",
                        "track SLAs, review performance metrics, and respond to alerts"),
        "interface": ("Integration Specialist", "Connect external systems",
                      "configure integrations, map data fields, and verify data flow"),
        "data": ("Data Steward", "Manage data lifecycle",
                 "classify data, enforce retention policies, and ensure data quality"),
        "compliance": ("ISSO / Compliance Officer", "Maintain compliance posture",
                       "review controls, generate compliance reports, and track remediation"),
    }

    for rtype, (persona, goal, narrative) in journey_map.items():
        type_reqs = type_groups.get(rtype, [])
        if not type_reqs:
            continue
        lines.append(f"### Journey {journey_num}: {persona} — {goal}")
        lines.append("")
        lines.append(f"**Persona:** {persona}")
        lines.append(f"**Goal:** {goal}")
        lines.append(f"**Narrative:** The {persona.lower()} needs to {narrative}.")
        lines.append("")
        lines.append("**Key Requirements:**")
        for r in type_reqs[:5]:
            text = (r.get("raw_text", ""))[:100]
            lines.append(f"- {text}")
        lines.append("")
        journey_num += 1

    if journey_num <= 1:
        return ""  # Nothing to show

    return "\n".join(lines)


def _render_acceptance_criteria(reqs: list[dict]) -> str:
    """Render BDD acceptance criteria for requirements that have them."""
    reqs_with_bdd = [r for r in reqs if r.get("acceptance_criteria")]
    if not reqs_with_bdd:
        return ""

    lines = [
        "## 4. Acceptance Criteria (BDD)",
        "",
        f"{len(reqs_with_bdd)} of {len(reqs)} requirements have BDD scenarios.",
        "",
    ]
    for r in reqs_with_bdd:
        rid = r.get("id", "—")
        text = (r.get("raw_text", ""))[:80]
        lines.append(f"#### {rid}: {text}")
        lines.append("")
        lines.append("```gherkin")
        lines.append(r["acceptance_criteria"].strip())
        lines.append("```")
        lines.append("")

    return "\n".join(lines)


def _render_user_stories(decomp: list[dict]) -> str:
    """Render SAFe decomposition hierarchy."""
    if not decomp:
        return ""

    lines = ["## 5. User Stories & Decomposition (SAFe)", ""]

    level_order = ["epic", "capability", "feature", "story", "enabler"]
    by_level: dict[str, list[dict]] = {}
    for d in decomp:
        lv = d.get("level", "story")
        by_level.setdefault(lv, []).append(d)

    for lv in level_order:
        items = by_level.get(lv)
        if not items:
            continue
        lines.append(f"### {lv.title()}s ({len(items)})")
        lines.append("")
        lines.append(f"| ID | Title | Size | WSJF | ATO Tier | Status |")
        lines.append(f"|----|-------|------|------|----------|--------|")
        for d in items:
            did = d.get("id", "—")
            title = (d.get("title", ""))[:80]
            size = d.get("t_shirt_size", "—")
            wsjf = f"{d['wsjf_score']:.1f}" if d.get("wsjf_score") else "—"
            tier = d.get("ato_impact_tier", "—")
            status = d.get("status", "draft").title()
            lines.append(f"| `{did}` | {title} | {size} | {wsjf} | {tier} | {status} |")
        lines.append("")

    return "\n".join(lines)


def _render_architecture(coa: dict | None) -> str:
    """Render architecture from the selected COA."""
    if not coa:
        return ""

    arch = _safe_json(coa.get("architecture_summary"))
    if not arch:
        return ""

    lines = [
        "## 6. Architecture & Technical Approach",
        "",
        f"*Source: {coa.get('coa_name', 'Selected COA')} ({coa.get('coa_type', '').title()})*",
        "",
    ]

    if arch.get("pattern"):
        lines.append(f"**Architecture Pattern:** {arch['pattern']}")
    if arch.get("layers"):
        lines.append(f"**Layers:** {arch['layers']}")

    components = arch.get("components", {})
    if components:
        lines.append("")
        lines.append("### Components")
        lines.append("")
        lines.append("| Component | Count |")
        lines.append("|-----------|-------|")
        for k, v in components.items():
            lines.append(f"| {k.replace('_', ' ').title()} | {v} |")
        lines.append("")

    infra = arch.get("infrastructure", {})
    if infra:
        lines.append("### Infrastructure")
        lines.append("")
        for k, v in infra.items():
            lines.append(f"- **{k.replace('_', ' ').title()}:** {v}")
        lines.append("")

    security = arch.get("security", {})
    if security:
        lines.append("### Security Architecture")
        lines.append("")
        for k, v in security.items():
            lines.append(f"- **{k.replace('_', ' ').title()}:** {v}")
        lines.append("")

    return "\n".join(lines)


def _render_timeline(coa: dict | None) -> str:
    if not coa:
        return ""
    timeline = _safe_json(coa.get("timeline"))
    if not timeline:
        return ""

    lines = [
        "## 7. Timeline & Milestones",
        "",
        f"*Source: {coa.get('coa_name', 'Selected COA')}*",
        "",
    ]

    if timeline.get("timeline_pis"):
        lines.append(f"**Program Increments (PIs):** {timeline['timeline_pis']}")
    if timeline.get("sprints"):
        lines.append(f"**Sprints:** {timeline['sprints']}")
    if timeline.get("start_date"):
        lines.append(f"**Estimated Start:** {timeline['start_date']}")
    if timeline.get("end_date"):
        lines.append(f"**Estimated End:** {timeline['end_date']}")

    roadmap = timeline.get("pi_roadmap", [])
    if roadmap:
        lines.append("")
        lines.append("### PI Roadmap")
        lines.append("")
        for pi in roadmap:
            pi_name = pi.get("pi", "PI")
            lines.append(f"**{pi_name}**")
            items = pi.get("items") or pi.get("scope", [])
            if isinstance(items, list):
                for item in items:
                    lines.append(f"  - {item}")
            milestone = pi.get("milestone")
            if milestone:
                lines.append(f"  - *Milestone: {milestone}*")
            lines.append("")

    return "\n".join(lines)


def _render_cost(coa: dict | None) -> str:
    if not coa:
        return ""
    cost = _safe_json(coa.get("cost_estimate"))
    if not cost:
        return ""

    lines = [
        "## 8. Cost Estimate",
        "",
        f"*Source: {coa.get('coa_name', 'Selected COA')}*",
        "",
    ]

    if cost.get("total_hours"):
        lines.append(f"**Total Estimated Hours:** {cost['total_hours']:,}")
    if cost.get("low") and cost.get("high"):
        lines.append(f"**Cost Range:** ${cost['low']:,.0f} — ${cost['high']:,.0f}")

    breakdown = cost.get("breakdown") or cost.get("t_shirt_breakdown", {})
    if breakdown:
        lines.append("")
        lines.append("| Category | Hours |")
        lines.append("|----------|-------|")
        for k, v in breakdown.items():
            lines.append(f"| {k.replace('_', ' ').title()} | {v} |")
        lines.append("")

    return "\n".join(lines)


def _render_risk(coa: dict | None) -> str:
    if not coa:
        return ""
    risk = _safe_json(coa.get("risk_profile"))
    if not risk:
        return ""

    lines = [
        "## 9. Risk Assessment",
        "",
        f"**Overall Risk Level:** {risk.get('overall_risk', risk.get('risk_level', 'Unknown')).title()}",
        "",
    ]

    top_risks = risk.get("top_risks") or risk.get("risks", [])
    if top_risks:
        lines.append("| # | Risk | Probability | Impact | Mitigation |")
        lines.append("|---|------|-------------|--------|------------|")
        for i, r in enumerate(top_risks[:10], 1):
            desc = r.get("description", r.get("risk", ""))[:80]
            prob = r.get("probability", "—")
            impact = r.get("impact", "—")
            mit = r.get("mitigation", "")[:80]
            lines.append(f"| {i} | {desc} | {prob} | {impact} | {mit} |")
        lines.append("")

    advantages = risk.get("advantages", [])
    if advantages:
        lines.append("### Advantages")
        lines.append("")
        for a in advantages:
            lines.append(f"- {a}")
        lines.append("")

    disadvantages = risk.get("disadvantages", [])
    if disadvantages:
        lines.append("### Disadvantages")
        lines.append("")
        for d in disadvantages:
            lines.append(f"- {d}")
        lines.append("")

    return "\n".join(lines)


def _render_compliance(session: dict, coa: dict | None) -> str:
    ctx = _safe_json(session.get("context_summary")) or {}
    frameworks = ctx.get("selected_frameworks", [])
    impact = session.get("impact_level", "IL5")
    classification = session.get("classification", "CUI")

    lines = [
        "## 10. Compliance & Regulatory",
        "",
        f"**Classification:** {classification}",
        f"**Impact Level:** {impact}",
        "",
    ]

    if frameworks:
        lines.append("### Applicable Frameworks")
        lines.append("")
        for fw in frameworks:
            lines.append(f"- {fw.replace('_', ' ').title()}")
        lines.append("")

    if coa:
        comp = _safe_json(coa.get("compliance_impact"))
        if comp:
            lines.append("### Compliance Impact (from selected COA)")
            lines.append("")
            if comp.get("coverage_pct"):
                lines.append(f"**Coverage:** {comp['coverage_pct']}%")
            if comp.get("ssp_update_required") is not None:
                lines.append(f"**SSP Update Required:** {'Yes' if comp['ssp_update_required'] else 'No'}")
            controls = comp.get("affected_controls", [])
            if controls:
                lines.append(f"**NIST 800-53 Controls:** {', '.join(controls[:20])}")
            lines.append("")

        sc = _safe_json(coa.get("supply_chain_impact"))
        if sc:
            lines.append("### Supply Chain Impact")
            lines.append("")
            for k, v in sc.items():
                lines.append(f"- **{k.replace('_', ' ').title()}:** {v}")
            lines.append("")

        if coa.get("boundary_tier"):
            lines.append(f"**ATO Boundary Tier:** {coa['boundary_tier']}")
            lines.append("")

    return "\n".join(lines)


def _render_coa_comparison(coas: list[dict]) -> str:
    if len(coas) < 2:
        return ""

    lines = [
        "## 11. Courses of Action Comparison",
        "",
        "| Attribute | " + " | ".join(c.get("coa_name", c.get("coa_type", "COA")) for c in coas) + " |",
        "|-----------|" + "|".join(["--------" for _ in coas]) + "|",
    ]

    # Type
    lines.append("| **Type** | " + " | ".join(
        c.get("coa_type", "—").title() for c in coas) + " |")
    # Status
    lines.append("| **Status** | " + " | ".join(
        c.get("status", "draft").title() for c in coas) + " |")
    # Boundary tier
    lines.append("| **Boundary Tier** | " + " | ".join(
        c.get("boundary_tier", "—") for c in coas) + " |")

    # Timeline PIs
    def _get_pis(c):
        t = _safe_json(c.get("timeline")) or {}
        return str(t.get("timeline_pis", "—"))
    lines.append("| **PIs** | " + " | ".join(_get_pis(c) for c in coas) + " |")

    # Cost range
    def _get_cost(c):
        cost = _safe_json(c.get("cost_estimate")) or {}
        if cost.get("low") and cost.get("high"):
            return f"${cost['low']:,.0f}–${cost['high']:,.0f}"
        return "—"
    lines.append("| **Cost Range** | " + " | ".join(_get_cost(c) for c in coas) + " |")

    # Risk level
    def _get_risk(c):
        r = _safe_json(c.get("risk_profile")) or {}
        return r.get("overall_risk", r.get("risk_level", "—")).title()
    lines.append("| **Risk Level** | " + " | ".join(_get_risk(c) for c in coas) + " |")

    # Mission fit
    lines.append("| **Mission Fit** | " + " | ".join(
        f"{c['mission_fit_pct']:.0f}%" if c.get("mission_fit_pct") else "—" for c in coas) + " |")

    lines.append("")

    selected = [c for c in coas if c.get("status") == "selected"]
    if selected:
        s = selected[0]
        lines.append(f"**Selected:** {s.get('coa_name', '—')}")
        if s.get("selected_by"):
            lines.append(f"**Selected By:** {s['selected_by']}")
        if s.get("selection_rationale"):
            lines.append(f"**Rationale:** {s['selection_rationale']}")
        lines.append("")

    return "\n".join(lines)


def _render_documents(docs: list[dict]) -> str:
    if not docs:
        return ""
    lines = [
        "## 12. Source Documents",
        "",
        "| File | Type | Requirements Extracted |",
        "|------|------|-----------------------|",
    ]
    for d in docs:
        name = d.get("file_name", "—")
        dtype = d.get("document_type", "—").upper()
        count = d.get("extracted_requirements_count", 0)
        lines.append(f"| {name} | {dtype} | {count} |")
    lines.append("")
    return "\n".join(lines)


def _render_gaps(session: dict) -> str:
    gap_count = session.get("gap_count", 0)
    ambiguity_count = session.get("ambiguity_count", 0)
    if gap_count == 0 and ambiguity_count == 0:
        return ""

    lines = [
        "## 13. Known Gaps & Open Questions",
        "",
        f"- **Unresolved Gaps:** {gap_count}",
        f"- **Ambiguities:** {ambiguity_count}",
        "",
        "*Run gap detection (`/icdev-intake --session-id <id> --gaps`) for detailed gap analysis.*",
        "",
    ]
    return "\n".join(lines)


def _render_footer() -> str:
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    return "\n".join([
        "---",
        "",
        f"*Generated by ICDEV PRD Generator on {ts}*",
        "",
        "# CUI // SP-CTI",
        "",
    ])


# ---------------------------------------------------------------------------
# Main generator
# ---------------------------------------------------------------------------

def generate_prd(session_id: str, db_path=None) -> dict:
    """Generate a PRD document for an intake session.

    Returns ``{"status": "ok", "session_id": ..., "prd_markdown": ...}``.
    """
    conn = _get_connection(db_path)
    try:
        session = _load_session(conn, session_id)
        if not session:
            return {"status": "error", "error": f"Session '{session_id}' not found."}

        reqs = _load_requirements(conn, session_id)
        conversation = _load_conversation(conn, session_id)
        readiness = _load_readiness(conn, session_id)
        decomp = _load_decomposition(conn, session_id)
        selected_coa = _load_selected_coa(conn, session_id)
        all_coas = _load_all_coas(conn, session_id)
        docs = _load_documents(conn, session_id)
    finally:
        conn.close()

    # Assemble sections
    sections = [
        _render_cover(session, readiness, len(reqs)),
        _render_executive_summary(session, reqs, readiness, conversation),
        _render_requirements(reqs),
        _render_user_journeys(session, reqs, conversation),
        _render_acceptance_criteria(reqs),
        _render_user_stories(decomp),
        _render_architecture(selected_coa),
        _render_timeline(selected_coa),
        _render_cost(selected_coa),
        _render_risk(selected_coa),
        _render_compliance(session, selected_coa),
        _render_coa_comparison(all_coas),
        _render_documents(docs),
        _render_gaps(session),
        _render_footer(),
    ]

    prd_md = "\n".join(s for s in sections if s)

    return {
        "status": "ok",
        "session_id": session_id,
        "project_id": session.get("project_id"),
        "total_requirements": len(reqs),
        "has_coa": selected_coa is not None,
        "has_decomposition": len(decomp) > 0,
        "readiness_score": readiness["overall_score"] if readiness else session.get("readiness_score", 0),
        "prd_markdown": prd_md,
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="Generate PRD from intake session")
    parser.add_argument("--session-id", required=True, help="Intake session ID")
    parser.add_argument("--output", help="Write PRD to file (markdown)")
    parser.add_argument("--json", action="store_true", help="Output full JSON envelope")
    args = parser.parse_args()

    result = generate_prd(args.session_id)

    if result["status"] != "ok":
        print(json.dumps(result, indent=2))
        raise SystemExit(1)

    if args.output:
        Path(args.output).write_text(result["prd_markdown"], encoding="utf-8")
        print(f"PRD written to {args.output}")
    elif args.json:
        print(json.dumps(result, indent=2))
    else:
        print(result["prd_markdown"])


if __name__ == "__main__":
    main()
