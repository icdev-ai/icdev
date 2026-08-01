#!/usr/bin/env python3
# CUI // SP-CTI
# Controlled by: Department of Defense
# CUI Category: CTI
# Distribution: D
# POC: ICDEV™ System Administrator
"""IDIQ Task Order Factory — standing teams, TO templates, compressed timelines.

Manages standing teams registered against contract vehicles (OASIS+, Polaris,
CIO-SP4, GSA MAS, SEWP), generates task order response templates with
vehicle-specific compliance requirements, estimates realistic timelines,
and tracks team capacity across active task orders.

Deterministic, air-gap safe.

DB tables (read/write): pg_standing_teams, pg_task_orders
DB tables (write):      audit_trail

Usage:
    python tools/govcon/idiq_factory.py \\
        --create-team --vehicle "OASIS+" \\
        --members '[{"name":"J. Smith","role":"PM","labor_category":"Project Manager","clearance":"Secret","available_fte":1.0}]' --json
    python tools/govcon/idiq_factory.py --list-teams --json
    python tools/govcon/idiq_factory.py --list-teams --vehicle "OASIS+" --json
    python tools/govcon/idiq_factory.py --generate-template --vehicle "OASIS+" --json
    python tools/govcon/idiq_factory.py \\
        --estimate-timeline --page-limit 25 \\
        --eval-factors 4 --team-size 6 --json
    python tools/govcon/idiq_factory.py --track-capacity --vehicle "OASIS+" --json
"""

import argparse
import json
import math
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from tools.db.storage import get_connection  # noqa: E402
from tools.common.helpers import row_to_dict  # noqa: E402
from tools.logging.icdev_logger import get_logger

logger = get_logger("icdev.govcon.idiq_factory")


# ── Constants ─────────────────────────────────────────────────────────

KNOWN_VEHICLES = {
    "OASIS+": {
        "full_name": "One Acquisition Solution for Integrated Services Plus",
        "agency": "GSA",
        "contract_type_default": "t_and_m",
        "domains": [
            "Management and Advisory",
            "Technical and Engineering",
            "Scientific",
            "Logistics",
            "Environmental",
        ],
        "compliance_notes": [
            "FAR 52.232-18 (Availability of Funds)",
            "OASIS+ ordering procedures apply",
            "Fair Opportunity per FAR 16.505",
        ],
    },
    "Polaris": {
        "full_name": "Polaris (GSA GWAC)",
        "agency": "GSA",
        "contract_type_default": "t_and_m",
        "domains": ["IT Services"],
        "compliance_notes": [
            "Small business set-asides (HUBZone, SDVOSB, WOSB pools)",
            "FAR 52.232-18 (Availability of Funds)",
        ],
    },
    "CIO-SP4": {
        "full_name": "Chief Information Officer Solutions and Partners 4",
        "agency": "NIH NITAAC",
        "contract_type_default": "t_and_m",
        "domains": [
            "IT Services",
            "Cybersecurity",
            "Cloud Solutions",
            "Health IT",
        ],
        "compliance_notes": [
            "NITAAC ordering guide procedures",
            "Section 508 accessibility required",
            "FISMA compliance required",
        ],
    },
    "GSA MAS": {
        "full_name": "GSA Multiple Award Schedule",
        "agency": "GSA",
        "contract_type_default": "ffp",
        "domains": [
            "IT Professional Services",
            "Software",
            "Cybersecurity",
        ],
        "compliance_notes": [
            "GSA MAS ordering procedures (BPA or RFQ)",
            "Price reductions clause",
            "Industrial Funding Fee (IFF) applies",
        ],
    },
    "SEWP": {
        "full_name": "Solutions for Enterprise-Wide Procurement V",
        "agency": "NASA",
        "contract_type_default": "ffp",
        "domains": [
            "IT Products",
            "IT Product-Based Services",
            "Cloud Solutions",
        ],
        "compliance_notes": [
            "SEWP quotation process via SEWP portal",
            "Delivery within 20 business days (products)",
            "FISMA compliance for cloud",
        ],
    },
}

CONTRACT_TYPES = ("ffp", "t_and_m", "cpff", "cpaf", "idiq")

TO_STATUSES = ("draft", "in_progress", "submitted", "awarded", "completed", "cancelled")


# ── Helpers ───────────────────────────────────────────────────────────


def _now():
    return datetime.now(timezone.utc).isoformat()


def _gen_id(prefix="st"):
    return f"{prefix}-{uuid.uuid4().hex[:12]}"


def _get_db():
    return get_connection()


def _audit(conn, event_type, action, details):
    det = json.dumps(details) if isinstance(details, dict) else str(details)
    try:
        conn.execute(
            "INSERT INTO audit_trail "
            "(created_at, event_type, actor, action, details, project_id, session_id) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s)",
            (_now(), event_type, "idiq_factory", action, det, None, None),
        )
    except Exception:
        try:
            conn.execute(
                "INSERT INTO audit_trail "
                "(project_id, event_type, actor, action, details, session_id) "
                "VALUES (%s, %s, %s, %s, %s, %s)",
                (None, event_type, "idiq_factory", action, det, None),
            )
        except Exception as exc:  # noqa: BLE001 - best-effort persistence; logged, never raised
            # audit is best-effort
            logger.warning("_audit: best-effort INSERT into audit_trail failed (non-blocking): %s", exc)


def _ensure_tables(conn):
    """Create IDIQ factory tables if they don't exist."""
    conn.execute("""
        CREATE TABLE IF NOT EXISTS pg_standing_teams (
            id TEXT PRIMARY KEY,
            vehicle_name TEXT NOT NULL,
            team_name TEXT,
            members TEXT NOT NULL,
            total_fte REAL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS pg_task_orders (
            id TEXT PRIMARY KEY,
            vehicle_name TEXT NOT NULL,
            standing_team_id TEXT,
            to_number TEXT,
            title TEXT,
            contract_type TEXT DEFAULT 't_and_m',
            timeline_days INTEGER,
            page_limit INTEGER,
            status TEXT DEFAULT 'draft',
            committed_fte REAL DEFAULT 0,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
    """)
    conn.commit()


# ── Core Functions ────────────────────────────────────────────────────


def create_standing_team(vehicle_name, team_members, team_name=None):
    """Register a standing team for a contract vehicle.

    Args:
        vehicle_name: Contract vehicle (e.g., OASIS+, Polaris, CIO-SP4)
        team_members: List of dicts with name, role, labor_category,
                      clearance, available_fte
        team_name: Optional descriptive name for the team
    """
    if isinstance(team_members, str):
        try:
            team_members = json.loads(team_members)
        except json.JSONDecodeError:
            return {"status": "error", "message": "Invalid JSON for team_members"}

    if not isinstance(team_members, list) or not team_members:
        return {"status": "error", "message": "team_members must be a non-empty list"}

    total_fte = sum(float(m.get("available_fte", 0)) for m in team_members)
    team_id = _gen_id("st")
    now = _now()

    conn = _get_db()
    _ensure_tables(conn)

    conn.execute(
        "INSERT INTO pg_standing_teams "
        "(id, vehicle_name, team_name, members, total_fte, created_at, updated_at) "
        "VALUES (%s, %s, %s, %s, %s, %s, %s)",
        (team_id, vehicle_name, team_name or f"{vehicle_name} Team", json.dumps(team_members), total_fte, now, now),
    )

    _audit(
        conn,
        "idiq.team_created",
        "create_standing_team",
        {
            "team_id": team_id,
            "vehicle": vehicle_name,
            "members_count": len(team_members),
            "total_fte": total_fte,
        },
    )
    conn.commit()

    return {
        "status": "ok",
        "team_id": team_id,
        "vehicle": vehicle_name,
        "team_name": team_name or f"{vehicle_name} Team",
        "members_count": len(team_members),
        "total_fte": total_fte,
    }


def list_standing_teams(vehicle_name=None):
    """List registered standing teams, optionally filtered by vehicle."""
    conn = _get_db()
    _ensure_tables(conn)

    if vehicle_name:
        rows = conn.execute(
            "SELECT * FROM pg_standing_teams WHERE vehicle_name = %s ORDER BY created_at DESC",
            (vehicle_name,),
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT * FROM pg_standing_teams ORDER BY vehicle_name, created_at DESC",
        ).fetchall()

    teams = []
    for row in rows:
        d = row_to_dict(row)
        # Parse members JSON for summary
        try:
            members = json.loads(d.get("members", "[]"))
        except (json.JSONDecodeError, TypeError):
            members = []
        d["members_count"] = len(members)
        d["members_summary"] = [{"name": m.get("name", ""), "role": m.get("role", "")} for m in members[:5]]
        teams.append(d)

    return {
        "status": "ok",
        "teams": teams,
        "total": len(teams),
    }


def generate_task_order_template(vehicle_name, contract_type="t_and_m"):
    """Generate a TO response template with vehicle-specific requirements."""
    vehicle_info = KNOWN_VEHICLES.get(vehicle_name)
    if not vehicle_info:
        # Allow unknown vehicles with generic template
        vehicle_info = {
            "full_name": vehicle_name,
            "agency": "Unknown",
            "contract_type_default": contract_type,
            "domains": [],
            "compliance_notes": [],
        }

    if contract_type not in CONTRACT_TYPES:
        return {"status": "error", "message": f"Invalid contract_type. Must be one of: {CONTRACT_TYPES}"}

    # Determine timeline based on complexity
    if contract_type == "ffp":
        timeline_days = 15  # Standard
    elif contract_type in ("cpff", "cpaf"):
        timeline_days = 30  # Complex
    else:
        timeline_days = 15  # Standard T&M

    contract_label = {
        "ffp": "Firm Fixed Price",
        "t_and_m": "Time and Materials",
        "cpff": "Cost Plus Fixed Fee",
        "cpaf": "Cost Plus Award Fee",
        "idiq": "Indefinite Delivery / Indefinite Quantity",
    }.get(contract_type, contract_type.upper())

    lines = [
        f"# Task Order Response — {vehicle_name}",
        "",
        f"**Vehicle:** {vehicle_info['full_name']}",
        f"**Ordering Agency:** {vehicle_info['agency']}",
        f"**Contract Type:** {contract_label}",
        f"**Response Timeline:** {timeline_days} calendar days",
        "",
        "---",
        "",
        "## Volume I: Technical Approach",
        "",
        "### 1. Understanding of Requirements",
        "",
        "*[Demonstrate understanding of the task order requirements. Reference specific PWS/SOW paragraphs.]*",
        "",
        "### 2. Technical Solution",
        "",
        "*[Describe the proposed technical approach, tools, methodologies, and technologies.]*",
        "",
        "### 3. Quality Assurance",
        "",
        "*[Describe QA/QC processes, deliverable review procedures, and continuous improvement.]*",
        "",
        "---",
        "",
        "## Volume II: Management Approach",
        "",
        "### 1. Program Management",
        "",
        "*[Describe project management methodology, communication plan, "
        "and risk management. Reference the post-award management portal "
        "for tracking requirements and CDRLs.]*",
        "",
        "### 2. Staffing Plan",
        "",
        "*[Map labor categories to requirements. Include key personnel qualifications and availability.]*",
        "",
        "### 3. Transition Plan",
        "",
        "*[Describe phase-in/phase-out approach with knowledge transfer.]*",
        "",
        "---",
        "",
        "## Volume III: Past Performance",
        "",
        "*[Provide 3-5 relevant past performance references. Include "
        "contract number, agency, period, value, and relevance.]*",
        "",
        "---",
        "",
    ]

    # Pricing volume varies by contract type
    lines.append("## Volume IV: Pricing")
    lines.append("")

    if contract_type == "t_and_m":
        lines.extend(
            [
                "### Labor Rate Table",
                "",
                "| Labor Category | Direct Rate | Wrap Rate | Ceiling Rate |",
                "|---------------|------------|-----------|--------------|",
                "| *[Category]* | *[$X.XX]* | *[$X.XX]* | *[$X.XX]* |",
                "",
                "### Level of Effort",
                "",
                "| Task | Labor Category | Hours | Cost |",
                "|------|---------------|-------|------|",
                "| *[Task]* | *[Category]* | *[XXX]* | *[$X,XXX]* |",
                "",
            ]
        )
    elif contract_type == "ffp":
        lines.extend(
            [
                "### Fixed Price Schedule",
                "",
                "| CLIN | Description | Quantity | Unit Price | Total |",
                "|------|------------|----------|------------|-------|",
                "| *[0001]* | *[Description]* | *[1]* | *[$X,XXX]* | *[$X,XXX]* |",
                "",
            ]
        )
    else:
        lines.extend(
            [
                "### Cost Breakdown",
                "",
                "| Element | Amount |",
                "|---------|--------|",
                "| Direct Labor | *[$X,XXX]* |",
                "| Fringe | *[$X,XXX]* |",
                "| Overhead | *[$X,XXX]* |",
                "| G&A | *[$X,XXX]* |",
                "| Fee | *[$X,XXX]* |",
                "| **Total** | **$X,XXX** |",
                "",
            ]
        )

    # Vehicle-specific compliance section
    if vehicle_info["compliance_notes"]:
        lines.append("---")
        lines.append("")
        lines.append("## Vehicle-Specific Compliance Requirements")
        lines.append("")
        for note in vehicle_info["compliance_notes"]:
            lines.append(f"- {note}")
        lines.append("")

    sections = [
        "Volume I: Technical Approach",
        "Volume II: Management Approach",
        "Volume III: Past Performance",
        "Volume IV: Pricing",
    ]

    markdown = "\n".join(lines)
    return {
        "status": "ok",
        "template_markdown": markdown,
        "sections": sections,
        "vehicle": vehicle_name,
        "contract_type": contract_type,
        "timeline_days": timeline_days,
    }


def estimate_to_timeline(page_limit, evaluation_factors_count, team_size):
    """Estimate realistic task order response timeline.

    Based on page limit, evaluation complexity, and team capacity.
    """
    if page_limit <= 0 or team_size <= 0:
        return {"status": "error", "message": "page_limit and team_size must be positive"}

    # Base drafting: ~3-5 pages per person per day for quality content
    pages_per_person_per_day = 4.0
    drafting_days = math.ceil(page_limit / (pages_per_person_per_day * max(team_size * 0.6, 1)))
    drafting_days = max(drafting_days, 2)

    # Review cycles scale with evaluation factors
    review_cycles = min(evaluation_factors_count, 4)
    review_days_per_cycle = max(1, math.ceil(page_limit / 30))
    review_days = review_cycles * review_days_per_cycle

    # Pricing always takes at least 2 days
    pricing_days = max(2, math.ceil(evaluation_factors_count * 0.5))

    # Final production: 1-2 days
    production_days = 1 if page_limit <= 30 else 2

    # Kickoff and planning
    planning_days = 1 if page_limit <= 20 else 2

    total_days = planning_days + drafting_days + review_days + pricing_days + production_days

    # Critical path (some phases can be parallel)
    # Pricing can run parallel with later review cycles
    parallel_savings = min(pricing_days - 1, review_days // 2)
    critical_path_days = total_days - max(parallel_savings, 0)

    breakdown = [
        {"phase": "Planning & Kickoff", "days": planning_days, "parallel": False},
        {"phase": "Drafting", "days": drafting_days, "parallel": False},
        {"phase": "Review Cycles", "days": review_days, "parallel": False},
        {"phase": "Pricing", "days": pricing_days, "parallel": True},
        {"phase": "Final Production", "days": production_days, "parallel": False},
    ]

    # Urgency classification
    if critical_path_days <= 10:
        urgency = "urgent"
    elif critical_path_days <= 15:
        urgency = "standard"
    else:
        urgency = "complex"

    return {
        "status": "ok",
        "total_days": total_days,
        "critical_path_days": critical_path_days,
        "urgency": urgency,
        "breakdown": breakdown,
        "assumptions": {
            "pages_per_person_per_day": pages_per_person_per_day,
            "writers": max(int(team_size * 0.6), 1),
            "review_cycles": review_cycles,
            "page_limit": page_limit,
            "evaluation_factors": evaluation_factors_count,
            "team_size": team_size,
        },
    }


def track_to_capacity(vehicle_name):
    """Track team capacity across active task orders for a vehicle."""
    conn = _get_db()
    _ensure_tables(conn)

    # Get standing teams for this vehicle
    teams = conn.execute(
        "SELECT * FROM pg_standing_teams WHERE vehicle_name = %s",
        (vehicle_name,),
    ).fetchall()

    if not teams:
        return {
            "status": "ok",
            "vehicle": vehicle_name,
            "total_available_fte": 0,
            "committed_fte": 0,
            "remaining_fte": 0,
            "utilization_pct": 0,
            "at_risk": False,
            "message": f"No standing teams registered for {vehicle_name}",
        }

    total_available = sum(float(row_to_dict(t).get("total_fte", 0)) for t in teams)

    # Get active task orders for this vehicle
    active_tos = conn.execute(
        "SELECT * FROM pg_task_orders WHERE vehicle_name = %s AND status IN (%s, %s)",
        (vehicle_name, "in_progress", "awarded"),
    ).fetchall()

    committed = sum(float(row_to_dict(to).get("committed_fte", 0)) for to in active_tos)

    remaining = total_available - committed
    utilization_pct = round((committed / total_available * 100) if total_available > 0 else 0, 1)
    at_risk = utilization_pct > 85

    active_to_list = []
    for to_row in active_tos:
        d = row_to_dict(to_row)
        active_to_list.append(
            {
                "id": d.get("id"),
                "title": d.get("title"),
                "to_number": d.get("to_number"),
                "committed_fte": d.get("committed_fte", 0),
                "status": d.get("status"),
            }
        )

    return {
        "status": "ok",
        "vehicle": vehicle_name,
        "standing_teams": len(teams),
        "total_available_fte": total_available,
        "committed_fte": committed,
        "remaining_fte": round(remaining, 2),
        "utilization_pct": utilization_pct,
        "at_risk": at_risk,
        "active_task_orders": active_to_list,
        "active_to_count": len(active_to_list),
    }


# ── CLI ───────────────────────────────────────────────────────────────


def main():
    parser = argparse.ArgumentParser(
        description="IDIQ Task Order Factory — standing teams, TO templates, capacity tracking"
    )

    group = parser.add_mutually_exclusive_group()
    group.add_argument("--create-team", action="store_true", help="Register a standing team for a contract vehicle")
    group.add_argument("--list-teams", action="store_true", help="List registered standing teams")
    group.add_argument("--generate-template", action="store_true", help="Generate task order response template")
    group.add_argument("--estimate-timeline", action="store_true", help="Estimate task order response timeline")
    group.add_argument("--track-capacity", action="store_true", help="Track team capacity across active TOs")

    parser.add_argument("--vehicle", help="Contract vehicle name (OASIS+, Polaris, CIO-SP4, GSA MAS, SEWP)")
    parser.add_argument("--members", help="JSON array of team members for --create-team")
    parser.add_argument("--team-name", help="Optional team name for --create-team")
    parser.add_argument("--contract-type", default="t_and_m", help="Contract type: ffp, t_and_m, cpff, cpaf, idiq")
    parser.add_argument("--page-limit", type=int, help="Page limit for --estimate-timeline")
    parser.add_argument("--eval-factors", type=int, help="Number of evaluation factors")
    parser.add_argument("--team-size", type=int, help="Team size for --estimate-timeline")
    parser.add_argument("--json", action="store_true", help="JSON output")
    parser.add_argument("--human", action="store_true", help="Human-readable output")
    args = parser.parse_args()

    result = {}

    if args.create_team:
        if not args.vehicle or not args.members:
            result = {"status": "error", "message": "Provide --vehicle and --members (JSON)"}
        else:
            result = create_standing_team(args.vehicle, args.members, args.team_name)

    elif args.list_teams:
        result = list_standing_teams(args.vehicle)

    elif args.generate_template:
        if not args.vehicle:
            result = {"status": "error", "message": "Provide --vehicle"}
        else:
            result = generate_task_order_template(args.vehicle, args.contract_type)

    elif args.estimate_timeline:
        if not all([args.page_limit, args.eval_factors, args.team_size]):
            result = {"status": "error", "message": "Provide --page-limit, --eval-factors, --team-size"}
        else:
            result = estimate_to_timeline(args.page_limit, args.eval_factors, args.team_size)

    elif args.track_capacity:
        if not args.vehicle:
            result = {"status": "error", "message": "Provide --vehicle"}
        else:
            result = track_to_capacity(args.vehicle)

    else:
        parser.print_help()
        return

    print(json.dumps(result, indent=2, default=str))


if __name__ == "__main__":
    main()

# CUI // SP-CTI
