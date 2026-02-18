#!/usr/bin/env python3
# CUI // SP-CTI
# Controlled by: Department of Defense
# CUI Category: CTI
# Distribution: D
# POC: ICDEV System Administrator
"""COA (Course of Action) Generator for the ICDEV RICOAS Digital Program Twin.

Generates 3 standard Courses of Action (Speed / Balanced / Comprehensive) plus
RED-tier alternative COAs.  Each COA includes architecture summary, PI roadmap,
risk register, compliance impact, resource plan, cost estimate, supply-chain
impact, and boundary tier.

Usage:
    # Generate the 3 standard COAs for an intake session
    python tools/simulation/coa_generator.py --session-id <id> --generate-3-coas --json

    # Generate with automatic simulation runs
    python tools/simulation/coa_generator.py --session-id <id> --generate-3-coas --simulate --json

    # Generate alternative COAs for a RED-tier requirement
    python tools/simulation/coa_generator.py --session-id <id> --generate-alternative \\
        --requirement-id <id> --json

    # Compare all COAs for a session
    python tools/simulation/coa_generator.py --session-id <id> --compare --json

    # Select a COA
    python tools/simulation/coa_generator.py --coa-id <id> --select \\
        --selected-by "Jane Smith" --rationale "Best scope/risk balance" --json

    # List COAs for a session
    python tools/simulation/coa_generator.py --session-id <id> --list --json

Databases:
    - data/icdev.db: intake_requirements, safe_decomposition,
      boundary_impact_assessments, coa_definitions, coa_comparisons,
      simulation_scenarios, simulation_results
"""

import argparse
import json
import os
import sqlite3
import sys
import uuid
from datetime import datetime
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent.parent
DB_PATH = Path(os.environ.get("ICDEV_DB_PATH", str(BASE_DIR / "data" / "icdev.db")))

RED_ALT_PATTERNS_PATH = BASE_DIR / "context" / "requirements" / "red_alternative_patterns.json"

# Graceful import of audit logger
try:
    from tools.audit.audit_logger import log_event
    _HAS_AUDIT = True
except ImportError:
    _HAS_AUDIT = False
    def log_event(**kwargs) -> int:  # type: ignore[misc]
        return -1


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# T-shirt size to hours mapping (used for cost estimation)
_TSHIRT_HOURS = {
    "XS": 8,
    "S": 24,
    "M": 80,
    "L": 200,
    "XL": 480,
    "XXL": 960,
}

# Blended hourly rate for cost range estimates (low / high)
_RATE_LOW = 125   # USD/hr
_RATE_HIGH = 200  # USD/hr

# Tier rank for comparisons (lower is better from risk perspective)
_TIER_RANK = {"GREEN": 1, "YELLOW": 2, "ORANGE": 3, "RED": 4}
_RANK_TIER = {v: k for k, v in _TIER_RANK.items()}


# ---------------------------------------------------------------------------
# Database helpers
# ---------------------------------------------------------------------------

def _get_connection(db_path=None):
    """Get database connection with dict-like row access."""
    path = db_path or DB_PATH
    if not path.exists():
        raise FileNotFoundError(
            f"Database not found: {path}\nRun: python tools/db/init_icdev_db.py"
        )
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    return conn


def _generate_id(prefix="coa"):
    """Generate a unique ID with prefix."""
    return f"{prefix}-{uuid.uuid4().hex[:12]}"


def _now_iso():
    """Return current UTC timestamp in ISO format."""
    return datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")


# ---------------------------------------------------------------------------
# Data loaders
# ---------------------------------------------------------------------------

def _load_requirements(conn, session_id):
    """Load intake requirements for a session, grouped by priority."""
    rows = conn.execute(
        "SELECT * FROM intake_requirements WHERE session_id = ? ORDER BY priority",
        (session_id,),
    ).fetchall()
    reqs = [dict(r) for r in rows]
    by_priority = {"critical": [], "high": [], "medium": [], "low": []}
    for r in reqs:
        by_priority.setdefault(r.get("priority", "medium"), []).append(r)
    return reqs, by_priority


def _load_decomposition(conn, session_id):
    """Load SAFe decomposition items for a session."""
    rows = conn.execute(
        "SELECT * FROM safe_decomposition WHERE session_id = ? ORDER BY level, title",
        (session_id,),
    ).fetchall()
    return [dict(r) for r in rows]


def _load_boundary_assessments(conn, session_id):
    """Load boundary impact assessments for a session."""
    rows = conn.execute(
        "SELECT * FROM boundary_impact_assessments WHERE session_id = ?",
        (session_id,),
    ).fetchall()
    return [dict(r) for r in rows]


def _get_session(conn, session_id):
    """Load the intake session record."""
    row = conn.execute(
        "SELECT * FROM intake_sessions WHERE id = ?", (session_id,)
    ).fetchone()
    if not row:
        raise ValueError(f"Intake session not found: {session_id}")
    return dict(row)


def _load_red_alternative_patterns():
    """Load RED alternative patterns from context file."""
    if not RED_ALT_PATTERNS_PATH.exists():
        return {"alternative_patterns": [], "selection_criteria": {}}
    with open(RED_ALT_PATTERNS_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


# ---------------------------------------------------------------------------
# Cost estimation helpers
# ---------------------------------------------------------------------------

def _sum_tshirt_hours(items):
    """Sum estimated hours from T-shirt sizes of decomposition items."""
    total = 0
    breakdown = {}
    for item in items:
        size = item.get("t_shirt_size") or "M"
        hours = _TSHIRT_HOURS.get(size, 80)
        total += hours
        breakdown[size] = breakdown.get(size, 0) + 1
    return total, breakdown


def _cost_estimate(hours, breakdown):
    """Build a cost estimate dict from total hours."""
    return {
        "hours": hours,
        "cost_range_low": hours * _RATE_LOW,
        "cost_range_high": hours * _RATE_HIGH,
        "t_shirt_breakdown": breakdown,
    }


# ---------------------------------------------------------------------------
# Boundary tier aggregation
# ---------------------------------------------------------------------------

def _best_tier(assessments):
    """Return the best (lowest-impact) tier from assessments."""
    if not assessments:
        return "GREEN"
    ranks = [_TIER_RANK.get(a.get("impact_tier", "GREEN"), 1) for a in assessments]
    return _RANK_TIER.get(min(ranks), "GREEN")


def _worst_tier(assessments):
    """Return the worst (highest-impact) tier from assessments."""
    if not assessments:
        return "GREEN"
    ranks = [_TIER_RANK.get(a.get("impact_tier", "GREEN"), 1) for a in assessments]
    return _RANK_TIER.get(max(ranks), "GREEN")


def _average_tier(assessments):
    """Return the average (rounded) tier from assessments."""
    if not assessments:
        return "GREEN"
    ranks = [_TIER_RANK.get(a.get("impact_tier", "GREEN"), 1) for a in assessments]
    avg = sum(ranks) / len(ranks)
    rounded = round(avg)
    return _RANK_TIER.get(rounded, "YELLOW")


# ---------------------------------------------------------------------------
# Risk register generator
# ---------------------------------------------------------------------------

def _generate_risk_register(coa_type, reqs, assessments):
    """Generate top-5 risks for a COA type."""
    base_risks = {
        "speed": [
            {"id": "R-01", "description": "Insufficient test coverage due to accelerated timeline",
             "probability": "high", "impact": "high",
             "mitigation": "Automated test generation, prioritize critical path tests"},
            {"id": "R-02", "description": "Technical debt accumulation from shortcuts",
             "probability": "high", "impact": "medium",
             "mitigation": "Schedule refactoring sprint in next PI"},
            {"id": "R-03", "description": "Incomplete compliance artifacts",
             "probability": "medium", "impact": "high",
             "mitigation": "Parallel compliance artifact generation"},
            {"id": "R-04", "description": "Integration defects from limited scope testing",
             "probability": "medium", "impact": "medium",
             "mitigation": "API contract testing at integration points"},
            {"id": "R-05", "description": "Rework required when adding deferred features",
             "probability": "high", "impact": "medium",
             "mitigation": "Design for extensibility in core architecture"},
        ],
        "balanced": [
            {"id": "R-01", "description": "Schedule pressure on P2 requirements",
             "probability": "medium", "impact": "medium",
             "mitigation": "WSJF prioritization, buffer sprints between PIs"},
            {"id": "R-02", "description": "Resource contention across work streams",
             "probability": "medium", "impact": "medium",
             "mitigation": "SAFe capacity allocation, clear team assignments"},
            {"id": "R-03", "description": "Boundary impact from YELLOW-tier items",
             "probability": "low", "impact": "high",
             "mitigation": "Early boundary assessment, incremental SSP updates"},
            {"id": "R-04", "description": "Dependency on external system availability",
             "probability": "low", "impact": "medium",
             "mitigation": "Mock services for development, ISA tracking"},
            {"id": "R-05", "description": "Compliance gap in deferred P3 items",
             "probability": "low", "impact": "low",
             "mitigation": "Compliance coverage tracking per PI"},
        ],
        "comprehensive": [
            {"id": "R-01", "description": "Feature creep and scope growth beyond estimates",
             "probability": "high", "impact": "medium",
             "mitigation": "Strict change control board, PI commitment gates"},
            {"id": "R-02", "description": "Extended timeline increases cost overrun risk",
             "probability": "medium", "impact": "high",
             "mitigation": "Earned value management, monthly burn-rate reviews"},
            {"id": "R-03", "description": "Team fatigue on long-duration project",
             "probability": "medium", "impact": "medium",
             "mitigation": "Sprint rotation, innovation sprints between PIs"},
            {"id": "R-04", "description": "Technology obsolescence during long build",
             "probability": "low", "impact": "medium",
             "mitigation": "Architecture Decision Records, modular design"},
            {"id": "R-05", "description": "Stakeholder engagement decline over extended period",
             "probability": "medium", "impact": "medium",
             "mitigation": "PI demos, monthly stakeholder briefings"},
        ],
    }
    risks = base_risks.get(coa_type, base_risks["balanced"])

    # Add boundary-specific risk if RED-tier assessments exist
    red_count = sum(1 for a in assessments if a.get("impact_tier") == "RED")
    if red_count > 0 and len(risks) < 6:
        risks.append({
            "id": f"R-{len(risks)+1:02d}",
            "description": f"{red_count} requirement(s) with RED boundary impact may invalidate ATO",
            "probability": "high",
            "impact": "critical",
            "mitigation": "Generate alternative COAs, engage AO early",
        })

    return risks[:5]


# ---------------------------------------------------------------------------
# Architecture summary generator
# ---------------------------------------------------------------------------

def _generate_architecture(coa_type, reqs, decomposition):
    """Generate architecture summary for a COA type."""
    component_counts = {
        "speed": {"services": 2, "databases": 1, "queues": 0, "caches": 0},
        "balanced": {"services": 4, "databases": 1, "queues": 1, "caches": 1},
        "comprehensive": {"services": 8, "databases": 2, "queues": 2, "caches": 2},
    }
    base = component_counts.get(coa_type, component_counts["balanced"])

    # Scale based on decomposition size
    epic_count = sum(1 for d in decomposition if d.get("level") == "epic")
    if epic_count > 3:
        scale = min(epic_count / 3.0, 2.0)
        for key in base:
            base[key] = max(1, int(base[key] * scale))

    patterns = {
        "speed": "Monolithic with modular boundaries",
        "balanced": "Modular monolith with service extraction points",
        "comprehensive": "Microservices with event-driven integration",
    }

    return {
        "pattern": patterns.get(coa_type, patterns["balanced"]),
        "components": base,
        "infrastructure": {
            "compute": "AWS GovCloud ECS" if coa_type == "speed" else "AWS GovCloud EKS",
            "database": "RDS PostgreSQL",
            "monitoring": "ELK + Prometheus/Grafana",
            "ci_cd": "GitLab CI/CD",
        },
        "security": {
            "auth": "CAC/PKI + OAuth 2.0",
            "encryption": "FIPS 140-2 (TLS 1.3, AES-256)",
            "network": "VPC with private subnets, NACLs",
        },
    }


# ---------------------------------------------------------------------------
# PI roadmap generator
# ---------------------------------------------------------------------------

def _generate_pi_roadmap(coa_type, reqs_by_priority, decomposition):
    """Generate PI roadmap for a COA type."""
    # Determine how many PIs
    pi_counts = {"speed": 2, "balanced": 3, "comprehensive": 5}
    num_pis = pi_counts.get(coa_type, 3)

    # Gather items by PI target from decomposition
    items_by_pi = {}
    for item in decomposition:
        pi = item.get("pi_target") or "PI-1"
        items_by_pi.setdefault(pi, []).append(item.get("title", "Untitled"))

    roadmap = []
    for pi_num in range(1, num_pis + 1):
        pi_key = f"PI-{pi_num}"
        pi_items = items_by_pi.get(pi_key, [])

        # If no items mapped to this PI, assign based on COA type
        if not pi_items:
            if coa_type == "speed" and pi_num == 1:
                pi_items = [r.get("refined_text", r.get("raw_text", "Requirement"))[:80]
                            for r in reqs_by_priority.get("critical", [])
                            + reqs_by_priority.get("high", [])]
            elif coa_type == "balanced":
                if pi_num <= 2:
                    pool = reqs_by_priority.get("critical", []) + reqs_by_priority.get("high", [])
                else:
                    pool = reqs_by_priority.get("medium", [])
                pi_items = [r.get("refined_text", r.get("raw_text", "Requirement"))[:80]
                            for r in pool[:5]]
            elif coa_type == "comprehensive":
                all_reqs = []
                for p in ("critical", "high", "medium", "low"):
                    all_reqs.extend(reqs_by_priority.get(p, []))
                chunk = len(all_reqs) // num_pis if num_pis else 1
                chunk = max(chunk, 1)
                start = (pi_num - 1) * chunk
                pi_items = [r.get("refined_text", r.get("raw_text", "Requirement"))[:80]
                            for r in all_reqs[start:start + chunk]]

        milestones = []
        if pi_num == 1:
            milestones.append("Architecture baseline approved")
            milestones.append("Initial ATO artifacts generated")
        if pi_num == num_pis:
            milestones.append("Full system integration test")
            milestones.append("ATO package submission")
        if 1 < pi_num < num_pis:
            milestones.append(f"PI-{pi_num} integration review")

        roadmap.append({
            "pi": pi_key,
            "items": pi_items[:10],  # Cap at 10 items per PI
            "milestones": milestones,
        })

    return roadmap


# ---------------------------------------------------------------------------
# Compliance impact generator
# ---------------------------------------------------------------------------

def _generate_compliance_impact(coa_type, assessments):
    """Generate compliance impact summary for a COA type."""
    coverage_map = {"speed": 70.0, "balanced": 85.0, "comprehensive": 95.0}
    coverage = coverage_map.get(coa_type, 85.0)

    affected_controls = set()
    for a in assessments:
        ctrls = a.get("affected_controls")
        if ctrls:
            try:
                parsed = json.loads(ctrls) if isinstance(ctrls, str) else ctrls
                if isinstance(parsed, list):
                    affected_controls.update(parsed)
            except (json.JSONDecodeError, TypeError):
                pass

    return {
        "coverage_pct": coverage,
        "affected_controls": sorted(affected_controls)[:20],
        "ssp_update_required": any(
            a.get("impact_tier") in ("ORANGE", "RED") for a in assessments
        ),
        "poam_items_expected": max(0, int((100 - coverage) / 5)),
        "frameworks": ["NIST 800-53", "FedRAMP Moderate", "CMMC Level 2"],
    }


# ---------------------------------------------------------------------------
# Supply chain impact generator
# ---------------------------------------------------------------------------

def _generate_supply_chain_impact(coa_type, reqs):
    """Generate supply chain impact summary."""
    vendor_count = {"speed": 2, "balanced": 4, "comprehensive": 6}
    return {
        "estimated_vendor_count": vendor_count.get(coa_type, 4),
        "scrm_assessment_required": coa_type != "speed",
        "section_889_review": True,
        "isa_agreements_needed": 1 if coa_type == "speed" else (
            2 if coa_type == "balanced" else 3
        ),
        "cots_components": max(1, len(reqs) // 3),
    }


# ---------------------------------------------------------------------------
# Simulation helper (creates scenario + runs basic simulation)
# ---------------------------------------------------------------------------

def _create_simulation_for_coa(conn, coa_id, session_id, project_id, coa_type, coa_data):
    """Create a simulation scenario and basic results for a COA."""
    scenario_id = _generate_id("sim")
    now = _now_iso()

    base_state = {
        "coa_id": coa_id,
        "coa_type": coa_type,
        "requirements_count": len(coa_data.get("requirements_included", [])),
        "timeline_pis": coa_data.get("timeline_pis", 3),
        "cost_estimate": coa_data.get("cost_estimate", {}),
    }
    modifications = {
        "scenario_purpose": f"Simulate {coa_type} COA outcome",
        "variables_tested": ["schedule", "cost", "risk", "compliance"],
    }

    conn.execute(
        """INSERT INTO simulation_scenarios
           (id, project_id, session_id, scenario_name, scenario_type,
            base_state, modifications, status, classification, created_by, created_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            scenario_id, project_id, session_id,
            f"{coa_type.title()} COA Simulation",
            "coa_comparison",
            json.dumps(base_state), json.dumps(modifications),
            "completed", "CUI", "icdev-simulation-engine", now,
        ),
    )

    # Generate simulation results across dimensions
    timeline_pis = coa_data.get("timeline_pis", 3)
    hours = coa_data.get("cost_estimate", {}).get("hours", 200)
    compliance_pct = coa_data.get("compliance_impact", {}).get("coverage_pct", 85.0)

    dimensions = [
        {
            "dimension": "schedule",
            "metric_name": "timeline_sprints",
            "baseline_value": 20.0,
            "simulated_value": float(timeline_pis * 5),
        },
        {
            "dimension": "cost",
            "metric_name": "total_hours",
            "baseline_value": 500.0,
            "simulated_value": float(hours),
        },
        {
            "dimension": "compliance",
            "metric_name": "coverage_pct",
            "baseline_value": 80.0,
            "simulated_value": compliance_pct,
        },
        {
            "dimension": "risk",
            "metric_name": "risk_score",
            "baseline_value": 0.5,
            "simulated_value": {"speed": 0.7, "balanced": 0.4, "comprehensive": 0.2}.get(
                coa_type, 0.5
            ),
        },
    ]

    for dim in dimensions:
        delta = dim["simulated_value"] - dim["baseline_value"]
        delta_pct = (delta / dim["baseline_value"] * 100.0) if dim["baseline_value"] else 0.0
        # Determine impact tier
        abs_pct = abs(delta_pct)
        if abs_pct < 10:
            tier = "GREEN"
        elif abs_pct < 25:
            tier = "YELLOW"
        elif abs_pct < 50:
            tier = "ORANGE"
        else:
            tier = "RED"

        conn.execute(
            """INSERT INTO simulation_results
               (scenario_id, dimension, metric_name, baseline_value,
                simulated_value, delta, delta_pct, confidence, impact_tier,
                details, calculated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                scenario_id, dim["dimension"], dim["metric_name"],
                dim["baseline_value"], dim["simulated_value"],
                round(delta, 2), round(delta_pct, 2),
                0.8, tier,
                json.dumps({"coa_type": coa_type}), now,
            ),
        )

    # Update scenario status
    conn.execute(
        "UPDATE simulation_scenarios SET status = 'completed', completed_at = ? WHERE id = ?",
        (now, scenario_id),
    )

    return scenario_id


# ---------------------------------------------------------------------------
# Core functions
# ---------------------------------------------------------------------------

def generate_3_coas(session_id, project_id=None, simulate=False, db_path=None):
    """Generate 3 Courses of Action (Speed / Balanced / Comprehensive).

    Args:
        session_id: The intake session ID.
        project_id: Optional project ID override (read from session if None).
        simulate: If True, create simulation scenarios and run them.
        db_path: Optional database path override.

    Returns:
        dict with session_id, coas list, and recommendation.
    """
    conn = _get_connection(db_path)
    try:
        session = _get_session(conn, session_id)
        project_id = project_id or session.get("project_id")
        if not project_id:
            raise ValueError("project_id is required (not found in session)")

        reqs, reqs_by_priority = _load_requirements(conn, session_id)
        decomposition = _load_decomposition(conn, session_id)
        assessments = _load_boundary_assessments(conn, session_id)

        now = _now_iso()

        # Classify requirements by priority groups
        p1_reqs = reqs_by_priority.get("critical", []) + reqs_by_priority.get("high", [])
        p2_reqs = reqs_by_priority.get("medium", [])
        p3_reqs = reqs_by_priority.get("low", [])

        p1_ids = [r["id"] for r in p1_reqs]
        p2_ids = [r["id"] for r in p2_reqs]
        p3_ids = [r["id"] for r in p3_reqs]

        # Classify decomposition items by associated requirements
        def _items_for_req_ids(req_ids):
            """Filter decomposition items whose source requirements intersect."""
            matched = []
            req_id_set = set(req_ids)
            for item in decomposition:
                src = item.get("source_requirement_ids")
                if src:
                    try:
                        parsed = json.loads(src) if isinstance(src, str) else src
                        if isinstance(parsed, list) and req_id_set.intersection(parsed):
                            matched.append(item)
                            continue
                    except (json.JSONDecodeError, TypeError):
                        pass
                # Include items not linked to any requirement in P1
                if not src and req_ids is p1_ids:
                    matched.append(item)
            return matched

        p1_items = _items_for_req_ids(p1_ids)
        p2_items = _items_for_req_ids(p2_ids)
        # For comprehensive, use all decomposition items
        all_items = decomposition if decomposition else p1_items + p2_items

        # --- Speed COA ---
        speed_hours, speed_breakdown = _sum_tshirt_hours(p1_items)
        speed_cost = _cost_estimate(speed_hours, speed_breakdown)
        speed_compliance = _generate_compliance_impact("speed", assessments)
        speed_arch = _generate_architecture("speed", p1_reqs, p1_items)
        speed_roadmap = _generate_pi_roadmap("speed", reqs_by_priority, p1_items)
        speed_risks = _generate_risk_register("speed", p1_reqs, assessments)
        speed_supply = _generate_supply_chain_impact("speed", p1_reqs)

        speed_data = {
            "coa_type": "speed",
            "coa_name": "Speed: Minimum Viable Delivery",
            "scope_description": "P1 (critical + high priority) requirements only",
            "requirements_included": p1_ids,
            "architecture_summary": speed_arch,
            "pi_roadmap": speed_roadmap,
            "risk_register": speed_risks,
            "compliance_impact": speed_compliance,
            "cost_estimate": speed_cost,
            "supply_chain_impact": speed_supply,
            "boundary_tier": _best_tier(assessments),
            "timeline_sprints": 10,
            "timeline_pis": min(2, max(1, len(speed_roadmap))),
            "risk_level": "high",
            "recommended": False,
            "advantages": [
                "Fastest delivery",
                "Lowest initial cost",
                "Quick feedback loop",
                "Early capability delivery",
            ],
            "disadvantages": [
                "Technical debt accumulation",
                "Limited scope — P2/P3 deferred",
                "Higher integration risk",
                "May require significant rework later",
            ],
        }

        # --- Balanced COA ---
        balanced_items = p1_items + p2_items
        balanced_hours, balanced_breakdown = _sum_tshirt_hours(balanced_items)
        balanced_cost = _cost_estimate(balanced_hours, balanced_breakdown)
        balanced_compliance = _generate_compliance_impact("balanced", assessments)
        balanced_arch = _generate_architecture("balanced", p1_reqs + p2_reqs, balanced_items)
        balanced_roadmap = _generate_pi_roadmap("balanced", reqs_by_priority, balanced_items)
        balanced_risks = _generate_risk_register("balanced", p1_reqs + p2_reqs, assessments)
        balanced_supply = _generate_supply_chain_impact("balanced", p1_reqs + p2_reqs)

        balanced_data = {
            "coa_type": "balanced",
            "coa_name": "Balanced: Optimal Scope-Risk Tradeoff",
            "scope_description": "P1 + P2 (critical, high, and medium priority) requirements",
            "requirements_included": p1_ids + p2_ids,
            "architecture_summary": balanced_arch,
            "pi_roadmap": balanced_roadmap,
            "risk_register": balanced_risks,
            "compliance_impact": balanced_compliance,
            "cost_estimate": balanced_cost,
            "supply_chain_impact": balanced_supply,
            "boundary_tier": _average_tier(assessments),
            "timeline_sprints": 15,
            "timeline_pis": min(3, max(2, len(balanced_roadmap))),
            "risk_level": "moderate",
            "recommended": True,
            "advantages": [
                "Good scope/risk balance",
                "Reasonable timeline",
                "Adequate compliance coverage",
                "Sustainable development pace",
            ],
            "disadvantages": [
                "Compromises on low-priority items",
                "Moderate complexity",
            ],
        }

        # --- Comprehensive COA ---
        comp_hours, comp_breakdown = _sum_tshirt_hours(all_items)
        comp_cost = _cost_estimate(comp_hours, comp_breakdown)
        comp_compliance = _generate_compliance_impact("comprehensive", assessments)
        comp_arch = _generate_architecture("comprehensive", reqs, all_items)
        comp_roadmap = _generate_pi_roadmap("comprehensive", reqs_by_priority, all_items)
        comp_risks = _generate_risk_register("comprehensive", reqs, assessments)
        comp_supply = _generate_supply_chain_impact("comprehensive", reqs)

        comp_data = {
            "coa_type": "comprehensive",
            "coa_name": "Comprehensive: Full Scope Delivery",
            "scope_description": "All requirements (P1 + P2 + P3 — all priorities)",
            "requirements_included": p1_ids + p2_ids + p3_ids,
            "architecture_summary": comp_arch,
            "pi_roadmap": comp_roadmap,
            "risk_register": comp_risks,
            "compliance_impact": comp_compliance,
            "cost_estimate": comp_cost,
            "supply_chain_impact": comp_supply,
            "boundary_tier": _worst_tier(assessments),
            "timeline_sprints": 25,
            "timeline_pis": min(5, max(3, len(comp_roadmap))),
            "risk_level": "low",
            "recommended": False,
            "advantages": [
                "Complete scope coverage",
                "Lowest residual risk",
                "Full compliance coverage",
                "Future-proof architecture",
            ],
            "disadvantages": [
                "Longest timeline",
                "Highest cost",
                "Feature creep risk",
                "Stakeholder patience required",
            ],
        }

        # Insert all three COAs into the database
        coas = []
        for coa_data in (speed_data, balanced_data, comp_data):
            coa_id = _generate_id("coa")
            coa_data["id"] = coa_id

            sim_scenario_id = None
            if simulate:
                sim_scenario_id = _create_simulation_for_coa(
                    conn, coa_id, session_id, project_id,
                    coa_data["coa_type"], coa_data,
                )
                coa_data["simulation_scenario_id"] = sim_scenario_id

            conn.execute(
                """INSERT INTO coa_definitions
                   (id, session_id, project_id, coa_type, coa_name, description,
                    architecture_summary, cost_estimate, risk_profile, timeline,
                    compliance_impact, supply_chain_impact, boundary_tier,
                    simulation_scenario_id, status, classification, created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    coa_id, session_id, project_id,
                    coa_data["coa_type"],
                    coa_data["coa_name"],
                    coa_data["scope_description"],
                    json.dumps(coa_data["architecture_summary"]),
                    json.dumps(coa_data["cost_estimate"]),
                    json.dumps({
                        "risk_level": coa_data["risk_level"],
                        "risk_register": coa_data["risk_register"],
                        "advantages": coa_data["advantages"],
                        "disadvantages": coa_data["disadvantages"],
                    }),
                    json.dumps({
                        "timeline_sprints": coa_data["timeline_sprints"],
                        "timeline_pis": coa_data["timeline_pis"],
                        "pi_roadmap": coa_data["pi_roadmap"],
                        "requirements_included": coa_data["requirements_included"],
                    }),
                    json.dumps(coa_data["compliance_impact"]),
                    json.dumps(coa_data["supply_chain_impact"]),
                    coa_data["boundary_tier"],
                    sim_scenario_id,
                    "simulated" if simulate else "draft",
                    "CUI", now, now,
                ),
            )
            coas.append(coa_data)

        conn.commit()

        # Audit
        if _HAS_AUDIT:
            log_event(
                event_type="coa_generated",
                actor="icdev-simulation-engine",
                action=f"Generated 3 COAs for session {session_id}",
                project_id=project_id,
                details=json.dumps({
                    "session_id": session_id,
                    "coa_ids": [c["id"] for c in coas],
                    "simulated": simulate,
                }),
            )

        return {
            "session_id": session_id,
            "project_id": project_id,
            "coas": coas,
            "recommendation": "balanced",
        }

    finally:
        conn.close()


def generate_alternative_coa(session_id, requirement_id, project_id=None, db_path=None):
    """Generate alternative COAs for a RED-tier requirement.

    Reads boundary assessment and RED alternative patterns, then creates
    COA variants that achieve the same intent within ATO boundaries.

    Args:
        session_id: The intake session ID.
        requirement_id: The requirement with RED-tier impact.
        project_id: Optional project ID override.
        db_path: Optional database path override.

    Returns:
        dict with requirement_id, original_intent, and alternatives list.
    """
    conn = _get_connection(db_path)
    try:
        session = _get_session(conn, session_id)
        project_id = project_id or session.get("project_id")
        if not project_id:
            raise ValueError("project_id is required (not found in session)")

        # Load the requirement
        req_row = conn.execute(
            "SELECT * FROM intake_requirements WHERE id = ?",
            (requirement_id,),
        ).fetchone()
        if not req_row:
            raise ValueError(f"Requirement not found: {requirement_id}")
        req = dict(req_row)
        original_intent = req.get("refined_text") or req.get("raw_text", "")

        # Load boundary assessment for this requirement
        assessment_rows = conn.execute(
            "SELECT * FROM boundary_impact_assessments WHERE requirement_id = ?",
            (requirement_id,),
        ).fetchall()
        assessments = [dict(r) for r in assessment_rows]

        if not assessments:
            return {
                "requirement_id": requirement_id,
                "original_intent": original_intent,
                "alternatives": [],
                "message": "No boundary assessment found for this requirement",
            }

        # Determine applicable categories from assessments
        applicable_categories = set()
        for a in assessments:
            cat = a.get("impact_category", "")
            if cat:
                applicable_categories.add(cat)

        # Map boundary impact categories to RED alternative pattern triggers
        category_to_trigger = {
            "data_type_change": ["classification_change", "data_sensitivity"],
            "boundary_change": ["boundary_expansion", "scope_increase"],
            "new_interconnection": ["new_interconnection"],
            "architecture": ["boundary_expansion", "scope_increase"],
            "data_flow": ["data_sensitivity", "cross_network"],
            "authentication": ["prohibited_technology"],
            "authorization": ["scope_increase"],
            "network": ["new_interconnection", "cross_network"],
            "encryption": ["classification_change"],
            "logging": ["scope_increase"],
            "component_addition": ["boundary_expansion"],
        }

        triggers = set()
        for cat in applicable_categories:
            triggers.update(category_to_trigger.get(cat, ["boundary_expansion"]))

        # Load patterns
        patterns_data = _load_red_alternative_patterns()
        all_patterns = patterns_data.get("alternative_patterns", [])
        selection_criteria = patterns_data.get("selection_criteria", {})

        # Filter applicable patterns
        applicable_patterns = []
        for pattern in all_patterns:
            pattern_when = set(pattern.get("applicable_when", []))
            if pattern_when.intersection(triggers):
                applicable_patterns.append(pattern)

        # If no patterns match, include the most generic ones
        if not applicable_patterns:
            applicable_patterns = [
                p for p in all_patterns
                if p.get("id") in ("ALT-PHASE", "ALT-SCOPE-REDUCE")
            ]

        now = _now_iso()
        alternatives = []

        # Score and sort patterns
        tier_scores = selection_criteria.get("tier_scores", {
            "GREEN": 1.0, "YELLOW": 0.75, "ORANGE": 0.5, "RED": 0.0,
        })
        cost_scores = selection_criteria.get("cost_scores", {
            "low": 1.0, "medium": 0.6, "high": 0.3,
        })
        weights = selection_criteria.get("scoring_weights", {
            "feasibility": 0.35, "resulting_tier_score": 0.25,
            "timeline_score": 0.20, "cost_score": 0.20,
        })

        for pattern in applicable_patterns:
            # Compute composite score
            feasibility = pattern.get("feasibility", 0.5)
            resulting_tier = pattern.get("resulting_tier", "YELLOW")
            cost_impact = pattern.get("cost_impact", "medium")
            timeline_days = pattern.get("estimated_timeline_days", 60)

            tier_s = tier_scores.get(resulting_tier, 0.5)
            cost_s = cost_scores.get(cost_impact, 0.5)
            # Normalize timeline: 0-30 days = 1.0, 120+ days = 0.2
            timeline_s = max(0.2, 1.0 - (timeline_days / 150.0))

            score = (
                weights.get("feasibility", 0.35) * feasibility
                + weights.get("resulting_tier_score", 0.25) * tier_s
                + weights.get("timeline_score", 0.20) * timeline_s
                + weights.get("cost_score", 0.20) * cost_s
            )

            coa_id = _generate_id("coa")
            alt_data = {
                "id": coa_id,
                "pattern_id": pattern.get("id"),
                "pattern_name": pattern.get("name"),
                "description": pattern.get("description"),
                "resulting_tier": resulting_tier,
                "feasibility": feasibility,
                "tradeoffs": pattern.get("tradeoffs", []),
                "implementation_steps": pattern.get("implementation_steps", []),
                "estimated_timeline_days": timeline_days,
                "cost_impact": cost_impact,
                "composite_score": round(score, 3),
            }

            # Insert alternative COA into database
            conn.execute(
                """INSERT INTO coa_definitions
                   (id, session_id, project_id, coa_type, coa_name, description,
                    architecture_summary, cost_estimate, risk_profile, timeline,
                    compliance_impact, supply_chain_impact, boundary_tier,
                    status, classification, created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    coa_id, session_id, project_id,
                    "alternative",
                    f"Alternative: {pattern.get('name', 'Unknown')}",
                    pattern.get("description", ""),
                    json.dumps({
                        "pattern_id": pattern.get("id"),
                        "implementation_steps": pattern.get("implementation_steps", []),
                    }),
                    json.dumps({
                        "cost_impact": cost_impact,
                        "estimated_timeline_days": timeline_days,
                    }),
                    json.dumps({
                        "risk_level": "varies",
                        "feasibility": feasibility,
                        "tradeoffs": pattern.get("tradeoffs", []),
                    }),
                    json.dumps({
                        "estimated_timeline_days": timeline_days,
                        "requirement_id": requirement_id,
                    }),
                    json.dumps({
                        "original_requirement": requirement_id,
                        "original_tier": "RED",
                        "resulting_tier": resulting_tier,
                    }),
                    json.dumps({}),
                    resulting_tier,
                    "draft", "CUI", now, now,
                ),
            )
            alternatives.append(alt_data)

        # Sort by composite score descending
        alternatives.sort(key=lambda x: x.get("composite_score", 0), reverse=True)

        conn.commit()

        # Audit
        if _HAS_AUDIT:
            log_event(
                event_type="coa_alternative_generated",
                actor="icdev-simulation-engine",
                action=f"Generated {len(alternatives)} alternative COAs for RED-tier requirement {requirement_id}",
                project_id=project_id,
                details=json.dumps({
                    "session_id": session_id,
                    "requirement_id": requirement_id,
                    "alternative_count": len(alternatives),
                    "pattern_ids": [a.get("pattern_id") for a in alternatives],
                }),
            )

        return {
            "requirement_id": requirement_id,
            "original_intent": original_intent,
            "alternatives": alternatives,
        }

    finally:
        conn.close()


def compare_coas(session_id, db_path=None):
    """Compare all COAs for a session across multiple dimensions.

    Inserts pairwise comparison records into coa_comparisons.

    Args:
        session_id: The intake session ID.
        db_path: Optional database path override.

    Returns:
        dict with session_id, comparison_matrix, and recommendation.
    """
    conn = _get_connection(db_path)
    try:
        # Load all COAs for session
        rows = conn.execute(
            """SELECT * FROM coa_definitions
               WHERE session_id = ? AND coa_type IN ('speed', 'balanced', 'comprehensive')
               ORDER BY coa_type""",
            (session_id,),
        ).fetchall()
        coas = [dict(r) for r in rows]

        if len(coas) < 2:
            return {
                "session_id": session_id,
                "comparison_matrix": [],
                "recommendation": None,
                "message": "Need at least 2 COAs to compare",
            }

        now = _now_iso()
        dimensions = ["architecture", "compliance", "supply_chain", "schedule", "cost", "risk", "overall"]
        comparisons = []

        # Score each COA across dimensions
        coa_scores = {}
        for coa in coas:
            cid = coa["id"]
            ctype = coa["coa_type"]
            scores = {}

            # Architecture: comprehensive > balanced > speed
            scores["architecture"] = {"speed": 3.0, "balanced": 7.0, "comprehensive": 9.0}.get(ctype, 5.0)

            # Compliance: higher coverage = better
            compliance = {}
            if coa.get("compliance_impact"):
                try:
                    compliance = json.loads(coa["compliance_impact"]) if isinstance(coa["compliance_impact"], str) else coa["compliance_impact"]
                except (json.JSONDecodeError, TypeError):
                    pass
            scores["compliance"] = compliance.get("coverage_pct", 80.0) / 10.0

            # Supply chain: fewer vendors = less risk = higher score
            supply = {}
            if coa.get("supply_chain_impact"):
                try:
                    supply = json.loads(coa["supply_chain_impact"]) if isinstance(coa["supply_chain_impact"], str) else coa["supply_chain_impact"]
                except (json.JSONDecodeError, TypeError):
                    pass
            vendor_count = supply.get("estimated_vendor_count", 4)
            scores["supply_chain"] = max(1.0, 10.0 - vendor_count)

            # Schedule: fewer PIs = faster = higher score
            timeline = {}
            if coa.get("timeline"):
                try:
                    timeline = json.loads(coa["timeline"]) if isinstance(coa["timeline"], str) else coa["timeline"]
                except (json.JSONDecodeError, TypeError):
                    pass
            pis = timeline.get("timeline_pis", 3)
            scores["schedule"] = max(1.0, 10.0 - pis * 1.5)

            # Cost: lower cost = higher score
            cost = {}
            if coa.get("cost_estimate"):
                try:
                    cost = json.loads(coa["cost_estimate"]) if isinstance(coa["cost_estimate"], str) else coa["cost_estimate"]
                except (json.JSONDecodeError, TypeError):
                    pass
            hours = cost.get("hours", 200)
            scores["cost"] = max(1.0, 10.0 - (hours / 200.0))

            # Risk: lower risk = higher score
            risk = {}
            if coa.get("risk_profile"):
                try:
                    risk = json.loads(coa["risk_profile"]) if isinstance(coa["risk_profile"], str) else coa["risk_profile"]
                except (json.JSONDecodeError, TypeError):
                    pass
            risk_level = risk.get("risk_level", "moderate")
            scores["risk"] = {"low": 9.0, "moderate": 6.0, "high": 3.0}.get(risk_level, 5.0)

            # Overall: weighted average
            overall_weights = {
                "architecture": 0.15,
                "compliance": 0.20,
                "supply_chain": 0.10,
                "schedule": 0.20,
                "cost": 0.20,
                "risk": 0.15,
            }
            scores["overall"] = sum(
                scores.get(d, 5.0) * overall_weights.get(d, 0.15)
                for d in overall_weights
            )

            coa_scores[cid] = {"scores": scores, "coa": coa}

        # Generate pairwise comparisons
        coa_ids = [c["id"] for c in coas]
        for i in range(len(coa_ids)):
            for j in range(i + 1, len(coa_ids)):
                cid_a = coa_ids[i]
                cid_b = coa_ids[j]
                scores_a = coa_scores[cid_a]["scores"]
                scores_b = coa_scores[cid_b]["scores"]
                coa_a = coa_scores[cid_a]["coa"]
                coa_b = coa_scores[cid_b]["coa"]

                for dim in dimensions:
                    sa = round(scores_a.get(dim, 5.0), 2)
                    sb = round(scores_b.get(dim, 5.0), 2)

                    if sa > sb:
                        winner = "coa_a"
                    elif sb > sa:
                        winner = "coa_b"
                    else:
                        winner = "tie"

                    comp_id = _generate_id("comp")

                    conn.execute(
                        """INSERT INTO coa_comparisons
                           (session_id, coa_a_id, coa_b_id, dimension,
                            coa_a_score, coa_b_score, winner, rationale, created_at)
                           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                        (
                            session_id, cid_a, cid_b, dim,
                            sa, sb, winner,
                            f"{coa_a['coa_type']}={sa} vs {coa_b['coa_type']}={sb}",
                            now,
                        ),
                    )

                    comparisons.append({
                        "coa_a_id": cid_a,
                        "coa_a_type": coa_a["coa_type"],
                        "coa_b_id": cid_b,
                        "coa_b_type": coa_b["coa_type"],
                        "dimension": dim,
                        "coa_a_score": sa,
                        "coa_b_score": sb,
                        "winner": winner,
                    })

        conn.commit()

        # Determine overall recommendation
        overall_scores = {
            cid: coa_scores[cid]["scores"].get("overall", 0)
            for cid in coa_ids
        }
        best_cid = max(overall_scores, key=overall_scores.get)
        recommendation = coa_scores[best_cid]["coa"]["coa_type"]

        # Audit
        if _HAS_AUDIT:
            log_event(
                event_type="coa_compared",
                actor="icdev-simulation-engine",
                action=f"Compared {len(coas)} COAs across {len(dimensions)} dimensions",
                details=json.dumps({
                    "session_id": session_id,
                    "coa_count": len(coas),
                    "comparison_count": len(comparisons),
                    "recommendation": recommendation,
                }),
            )

        return {
            "session_id": session_id,
            "comparison_matrix": comparisons,
            "coa_scores": {
                coa_scores[cid]["coa"]["coa_type"]: round(coa_scores[cid]["scores"]["overall"], 2)
                for cid in coa_ids
            },
            "recommendation": recommendation,
        }

    finally:
        conn.close()


def select_coa(coa_id, selected_by, rationale, db_path=None):
    """Mark a COA as selected and reject all others in the same session.

    Args:
        coa_id: The COA ID to select.
        selected_by: Name of the person selecting.
        rationale: Reason for selection.
        db_path: Optional database path override.

    Returns:
        dict with coa_id, coa_type, and selection_status.
    """
    conn = _get_connection(db_path)
    try:
        now = _now_iso()

        # Load the COA
        row = conn.execute(
            "SELECT * FROM coa_definitions WHERE id = ?", (coa_id,)
        ).fetchone()
        if not row:
            raise ValueError(f"COA not found: {coa_id}")
        coa = dict(row)
        session_id = coa["session_id"]
        project_id = coa["project_id"]

        # Reject all other COAs in same session
        conn.execute(
            """UPDATE coa_definitions
               SET status = 'rejected', updated_at = ?
               WHERE session_id = ? AND id != ? AND status NOT IN ('rejected', 'archived')""",
            (now, session_id, coa_id),
        )

        # Select this COA
        conn.execute(
            """UPDATE coa_definitions
               SET status = 'selected', selected_by = ?, selected_at = ?,
                   selection_rationale = ?, updated_at = ?
               WHERE id = ?""",
            (selected_by, now, rationale, now, coa_id),
        )

        conn.commit()

        # Audit
        if _HAS_AUDIT:
            log_event(
                event_type="coa_selected",
                actor=selected_by,
                action=f"Selected COA {coa_id} ({coa['coa_type']})",
                project_id=project_id,
                details=json.dumps({
                    "coa_id": coa_id,
                    "coa_type": coa["coa_type"],
                    "session_id": session_id,
                    "rationale": rationale,
                }),
            )

        return {
            "coa_id": coa_id,
            "coa_type": coa["coa_type"],
            "coa_name": coa["coa_name"],
            "selection_status": "selected",
            "selected_by": selected_by,
            "rationale": rationale,
        }

    finally:
        conn.close()


def get_coa(coa_id, db_path=None):
    """Load a single COA by ID.

    Args:
        coa_id: The COA ID to retrieve.
        db_path: Optional database path override.

    Returns:
        dict with all COA fields (JSON fields parsed).
    """
    conn = _get_connection(db_path)
    try:
        row = conn.execute(
            "SELECT * FROM coa_definitions WHERE id = ?", (coa_id,)
        ).fetchone()
        if not row:
            raise ValueError(f"COA not found: {coa_id}")
        coa = dict(row)

        # Parse JSON fields
        for field in ("architecture_summary", "cost_estimate", "risk_profile",
                      "timeline", "compliance_impact", "supply_chain_impact"):
            val = coa.get(field)
            if val and isinstance(val, str):
                try:
                    coa[field] = json.loads(val)
                except json.JSONDecodeError:
                    pass

        return coa

    finally:
        conn.close()


def list_coas(session_id, db_path=None):
    """List all COAs for a session.

    Args:
        session_id: The intake session ID.
        db_path: Optional database path override.

    Returns:
        dict with session_id and coas list.
    """
    conn = _get_connection(db_path)
    try:
        rows = conn.execute(
            """SELECT id, session_id, project_id, coa_type, coa_name,
                      description, boundary_tier, status,
                      selected_by, selected_at, selection_rationale,
                      mission_fit_pct, created_at, updated_at
               FROM coa_definitions
               WHERE session_id = ?
               ORDER BY
                   CASE coa_type
                       WHEN 'speed' THEN 1
                       WHEN 'balanced' THEN 2
                       WHEN 'comprehensive' THEN 3
                       WHEN 'alternative' THEN 4
                   END,
                   created_at""",
            (session_id,),
        ).fetchall()
        coas = [dict(r) for r in rows]

        return {
            "session_id": session_id,
            "count": len(coas),
            "coas": coas,
        }

    finally:
        conn.close()


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="RICOAS COA Generator — Generate and manage Courses of Action"
    )
    parser.add_argument("--session-id", help="Intake session ID")
    parser.add_argument("--project-id", help="Project ID (optional, read from session)")
    parser.add_argument("--coa-id", help="COA ID (for get/select)")
    parser.add_argument("--requirement-id", help="Requirement ID (for alternative COA)")
    parser.add_argument("--db", help="Database path override")

    # Actions
    parser.add_argument("--generate-3-coas", action="store_true",
                        help="Generate Speed/Balanced/Comprehensive COAs")
    parser.add_argument("--simulate", action="store_true",
                        help="Also create and run simulations for each COA")
    parser.add_argument("--generate-alternative", action="store_true",
                        help="Generate alternative COAs for RED-tier requirement")
    parser.add_argument("--compare", action="store_true",
                        help="Compare all COAs for a session")
    parser.add_argument("--select", action="store_true",
                        help="Select a COA")
    parser.add_argument("--selected-by", help="Name of person selecting COA")
    parser.add_argument("--rationale", help="Selection rationale")
    parser.add_argument("--list", action="store_true",
                        help="List all COAs for a session")
    parser.add_argument("--get", action="store_true",
                        help="Get a single COA by ID")

    # Output format
    parser.add_argument("--json", action="store_true",
                        help="Output as JSON")

    args = parser.parse_args()
    db_path = Path(args.db) if args.db else None

    try:
        if args.generate_3_coas:
            if not args.session_id:
                parser.error("--session-id is required for --generate-3-coas")
            result = generate_3_coas(
                session_id=args.session_id,
                project_id=args.project_id,
                simulate=args.simulate,
                db_path=db_path,
            )
            if args.json:
                print(json.dumps(result, indent=2, default=str))
            else:
                print(f"Generated 3 COAs for session: {args.session_id}")
                print(f"  Recommendation: {result['recommendation']}")
                for coa in result["coas"]:
                    print(f"\n  [{coa['coa_type'].upper()}] {coa['coa_name']}")
                    print(f"    ID: {coa['id']}")
                    print(f"    Scope: {coa['scope_description']}")
                    print(f"    Requirements: {len(coa['requirements_included'])}")
                    print(f"    Timeline: {coa['timeline_pis']} PIs ({coa['timeline_sprints']} sprints)")
                    ce = coa["cost_estimate"]
                    print(f"    Cost: ${ce['cost_range_low']:,.0f} - ${ce['cost_range_high']:,.0f}")
                    print(f"    Risk Level: {coa['risk_level']}")
                    print(f"    Boundary Tier: {coa['boundary_tier']}")
                    print(f"    Recommended: {coa['recommended']}")

        elif args.generate_alternative:
            if not args.session_id:
                parser.error("--session-id is required for --generate-alternative")
            if not args.requirement_id:
                parser.error("--requirement-id is required for --generate-alternative")
            result = generate_alternative_coa(
                session_id=args.session_id,
                requirement_id=args.requirement_id,
                project_id=args.project_id,
                db_path=db_path,
            )
            if args.json:
                print(json.dumps(result, indent=2, default=str))
            else:
                print(f"Alternative COAs for requirement: {args.requirement_id}")
                print(f"  Original intent: {result['original_intent'][:100]}...")
                for alt in result["alternatives"]:
                    print(f"\n  [{alt['pattern_id']}] {alt['pattern_name']}")
                    print(f"    ID: {alt['id']}")
                    print(f"    Resulting Tier: {alt['resulting_tier']}")
                    print(f"    Feasibility: {alt['feasibility']:.0%}")
                    print(f"    Score: {alt['composite_score']:.3f}")
                    print(f"    Timeline: {alt['estimated_timeline_days']} days")
                    print(f"    Cost Impact: {alt['cost_impact']}")

        elif args.compare:
            if not args.session_id:
                parser.error("--session-id is required for --compare")
            result = compare_coas(
                session_id=args.session_id,
                db_path=db_path,
            )
            if args.json:
                print(json.dumps(result, indent=2, default=str))
            else:
                print(f"COA Comparison for session: {args.session_id}")
                print(f"  Recommendation: {result['recommendation']}")
                if result.get("coa_scores"):
                    print("\n  Overall Scores:")
                    for ctype, score in result["coa_scores"].items():
                        marker = " <-- RECOMMENDED" if ctype == result["recommendation"] else ""
                        print(f"    {ctype}: {score:.2f}{marker}")

        elif args.select:
            if not args.coa_id:
                parser.error("--coa-id is required for --select")
            if not args.selected_by:
                parser.error("--selected-by is required for --select")
            if not args.rationale:
                parser.error("--rationale is required for --select")
            result = select_coa(
                coa_id=args.coa_id,
                selected_by=args.selected_by,
                rationale=args.rationale,
                db_path=db_path,
            )
            if args.json:
                print(json.dumps(result, indent=2, default=str))
            else:
                print(f"COA Selected: {result['coa_id']}")
                print(f"  Type: {result['coa_type']}")
                print(f"  Name: {result['coa_name']}")
                print(f"  Selected By: {result['selected_by']}")
                print(f"  Rationale: {result['rationale']}")

        elif args.list:
            if not args.session_id:
                parser.error("--session-id is required for --list")
            result = list_coas(
                session_id=args.session_id,
                db_path=db_path,
            )
            if args.json:
                print(json.dumps(result, indent=2, default=str))
            else:
                print(f"COAs for session: {args.session_id} ({result['count']} total)")
                for coa in result["coas"]:
                    status = coa.get("status", "draft")
                    marker = " ***" if status == "selected" else ""
                    print(f"\n  [{coa['coa_type'].upper()}] {coa['coa_name']}{marker}")
                    print(f"    ID: {coa['id']}")
                    print(f"    Status: {status}")
                    print(f"    Boundary Tier: {coa.get('boundary_tier', 'N/A')}")

        elif args.get or args.coa_id:
            if not args.coa_id:
                parser.error("--coa-id is required for --get")
            result = get_coa(
                coa_id=args.coa_id,
                db_path=db_path,
            )
            if args.json:
                print(json.dumps(result, indent=2, default=str))
            else:
                print(f"COA: {result['id']}")
                print(f"  Type: {result['coa_type']}")
                print(f"  Name: {result['coa_name']}")
                print(f"  Status: {result['status']}")
                print(f"  Boundary Tier: {result.get('boundary_tier', 'N/A')}")

        else:
            parser.print_help()
            sys.exit(1)

    except (FileNotFoundError, ValueError) as e:
        print(f"ERROR: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
# CUI // SP-CTI
