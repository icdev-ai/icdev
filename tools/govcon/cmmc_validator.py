# CUI // SP-CTI
# ICDEV™ Proposal Genesis — CMMC Supply Chain Validator (§3.14)
# Verifies CMMC/SPRS status for all proposed team members.

"""
CMMC Supply Chain Validator — validate CMMC and SPRS compliance for proposal team.

Manages:
    - Team member CMMC/SPRS registration and tracking (pg_cmmc_supply_chain)
    - Compliance gap detection across all team members
    - DFARS 252.204-7021 flow-down text generation
    - Pass/warn/fail gate evaluation for proposal readiness

Tables used:
    - pg_cmmc_supply_chain (CRUD — team member compliance data)
    - audit_trail (write — NIST AU-2)

Gate criteria:
    - PASS: All team members compliant or poam_status='closed'
    - WARN: Any members with poam_status='open' (remediation in progress)
    - FAIL: Any members non_compliant, unknown, or expired cert

Usage:
    python tools/govcon/cmmc_validator.py --opportunity-id "opp-xxx" --validate --json
    python tools/govcon/cmmc_validator.py --opportunity-id "opp-xxx" --add-member --name "ACME Corp" --role sub_tier1 --required-level 2 --json
    python tools/govcon/cmmc_validator.py --opportunity-id "opp-xxx" --update-member --member-id "cm-xxx" --actual-level 2 --sprs-score 110 --json
    python tools/govcon/cmmc_validator.py --opportunity-id "opp-xxx" --flow-down --json
    python tools/govcon/cmmc_validator.py --opportunity-id "opp-xxx" --status --json
    python tools/govcon/cmmc_validator.py --opportunity-id "opp-xxx" --gate --json
"""

import argparse
import json
import os
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from tools.db.storage import get_connection  # noqa: E402

_DB_PATH = Path(os.environ.get("ICDEV_DB_PATH", str(_ROOT / "data" / "icdev.db")))

# Valid team member roles
MEMBER_ROLES = (
    "prime",
    "sub_tier1",
    "sub_tier2",
    "sub_tier3",
    "jv_partner",
    "teaming_partner",
    "consultant",
)

# Valid compliance statuses
COMPLIANCE_STATUSES = ("compliant", "non_compliant", "in_progress", "unknown", "expired")

# Valid POAM statuses
POAM_STATUSES = ("none", "open", "closed")

# Valid assessment types per CMMC rule
ASSESSMENT_TYPES = ("self", "c3pao", "dibcac", "jsvap")

# DFARS flow-down template (deterministic, no LLM)
FLOW_DOWN_TEMPLATE = """DFARS 252.204-7021 — Contractor Compliance with the Cybersecurity Maturity Model Certification (CMMC) Program

(a) Scope. The Contractor shall have a current CMMC certificate at the CMMC level required by this contract and maintain it for the duration of the contract.

(b) Requirements. In accordance with DFARS 252.204-7021:

{member_sections}

(c) Subcontract Flow-Down. The Contractor shall include the substance of this clause, including this paragraph (c), in all subcontracts and other contractual instruments, including subcontracts for the acquisition of commercial products or commercial services, excluding commercially available off-the-shelf items.

(d) Compliance Timeline. All team members must achieve the required CMMC level prior to contract award or within the remediation period specified in their Plan of Action and Milestones (POA&M), not to exceed 180 days from the date of the conditional CMMC certificate.

Generated: {generated_at}
"""

MEMBER_SECTION_TEMPLATE = """    {role_label}: {name}
    - Required CMMC Level: {required_level}
    - Assessment Type: {assessment_type}
    - CAGE Code: {cage}
    - Current Status: {status}
    - SPRS Score: {sprs_score}
    - POA&M Status: {poam_status}
    - Certification Expiry: {cert_expiry}
"""


# ── Helpers ──────────────────────────────────────────────────────────


def _get_db():
    conn = get_connection()
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def _now():
    return datetime.now(timezone.utc).isoformat()


def _uuid():
    return f"cm-{uuid.uuid4().hex[:12]}"


def _audit(conn, action, details="", actor="cmmc_validator"):
    try:
        conn.execute(
            "INSERT INTO audit_trail (created_at, event_type, actor, action, details, session_id) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s)",
            (str(uuid.uuid4()), _now(), "govcon.cmmc_supply_chain", actor, action, details, "proposal_genesis"),
        )
    except Exception:
        pass


def _role_label(role):
    """Convert role code to human-readable label."""
    labels = {
        "prime": "Prime Contractor",
        "sub_tier1": "Tier 1 Subcontractor",
        "sub_tier2": "Tier 2 Subcontractor",
        "sub_tier3": "Tier 3 Subcontractor",
        "jv_partner": "Joint Venture Partner",
        "teaming_partner": "Teaming Partner",
        "consultant": "Consultant",
    }
    return labels.get(role, role)


def _determine_status(member):
    """Determine the effective compliance status for a team member.

    Logic:
        - If cert_expiry is set and in the past → 'expired'
        - If actual_cmmc_level >= required_cmmc_level → 'compliant'
        - If actual_cmmc_level < required or actual_cmmc_level is None → check poam
        - If poam_status='closed' and actual_cmmc_level >= required → 'compliant'
        - If poam_status='open' → 'in_progress'
        - Otherwise → 'non_compliant' or 'unknown'
    """
    required = member.get("required_cmmc_level") or 0
    actual = member.get("actual_cmmc_level") or 0
    poam = member.get("poam_status", "none")
    cert_expiry = member.get("cert_expiry")

    # Check expiry
    if cert_expiry:
        try:
            expiry_dt = datetime.fromisoformat(cert_expiry.replace("Z", "+00:00"))
            if expiry_dt < datetime.now(timezone.utc):
                return "expired"
        except (ValueError, TypeError):
            pass

    # Check level match
    if actual >= required and actual > 0:
        return "compliant"

    # Check POAM
    if poam == "closed" and actual >= required:
        return "compliant"
    if poam == "open":
        return "in_progress"

    # Unknown if no data at all
    if actual == 0 and poam == "none":
        return "unknown"

    return "non_compliant"


# ── Team Member CRUD ─────────────────────────────────────────────────


def add_team_member(
    opportunity_id,
    name,
    role,
    required_cmmc_level,
    cage=None,
    actual_cmmc_level=None,
    sprs_score=None,
    assessment_type=None,
    poam_status="none",
    cert_expiry=None,
):
    """Add a team member with CMMC compliance data.

    Args:
        opportunity_id: Parent opportunity UUID.
        name: Organization name.
        role: Team role (prime, sub_tier1, etc.).
        required_cmmc_level: Required CMMC level (1, 2, or 3).
        cage: Optional CAGE code.
        actual_cmmc_level: Current actual CMMC level.
        sprs_score: SPRS score (-203 to 110).
        assessment_type: Assessment type (self, c3pao, dibcac, jsvap).
        poam_status: POA&M status (none, open, closed).
        cert_expiry: Certification expiry date (ISO 8601).

    Returns:
        Dict with status and member_id.
    """
    if role not in MEMBER_ROLES:
        return {
            "status": "error",
            "message": f"Invalid role '{role}'. Must be one of: {MEMBER_ROLES}",
        }

    if required_cmmc_level not in (1, 2, 3):
        return {
            "status": "error",
            "message": f"Invalid required_cmmc_level '{required_cmmc_level}'. Must be 1, 2, or 3.",
        }

    if poam_status not in POAM_STATUSES:
        return {
            "status": "error",
            "message": f"Invalid poam_status '{poam_status}'. Must be one of: {POAM_STATUSES}",
        }

    if assessment_type and assessment_type not in ASSESSMENT_TYPES:
        return {
            "status": "error",
            "message": f"Invalid assessment_type '{assessment_type}'. Must be one of: {ASSESSMENT_TYPES}",
        }

    conn = _get_db()
    member_id = _uuid()
    now = _now()

    # Compute effective status
    member_data = {
        "required_cmmc_level": required_cmmc_level,
        "actual_cmmc_level": actual_cmmc_level,
        "poam_status": poam_status,
        "cert_expiry": cert_expiry,
    }
    effective_status = _determine_status(member_data)

    try:
        conn.execute(
            "INSERT INTO pg_cmmc_supply_chain "
            "(id, opportunity_id, team_member_name, role, required_cmmc_level, actual_cmmc_level, "
            "sprs_score, assessment_type, poam_status, cert_expiry, cage_code, "
            "compliance_status, created_at, updated_at) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
            (
                member_id,
                opportunity_id,
                name,
                role,
                required_cmmc_level,
                actual_cmmc_level,
                sprs_score,
                assessment_type,
                poam_status,
                cert_expiry,
                cage,
                effective_status,
                now,
                now,
            ),
        )
        _audit(
            conn,
            "add_team_member",
            f"Added {name} ({role}) to {opportunity_id}: required L{required_cmmc_level}, status={effective_status}",
        )
        conn.commit()
    except Exception as exc:
        conn.close()
        return {"status": "error", "message": str(exc)}

    conn.close()
    return {
        "status": "ok",
        "member_id": member_id,
        "team_member_name": name,
        "role": role,
        "compliance_status": effective_status,
    }


def update_member(member_id, **kwargs):
    """Update team member compliance data.

    Args:
        member_id: Member UUID.
        **kwargs: Fields to update.

    Returns:
        Dict with status and updated_fields.
    """
    conn = _get_db()
    row = conn.execute("SELECT * FROM pg_cmmc_supply_chain WHERE id = %s", (member_id,)).fetchone()
    if not row:
        conn.close()
        return {"status": "error", "message": f"Member {member_id} not found"}

    updatable = [
        "team_member_name",
        "role",
        "required_cmmc_level",
        "actual_cmmc_level",
        "sprs_score",
        "assessment_type",
        "poam_status",
        "cert_expiry",
        "cage_code",
    ]
    sets = []
    params = []
    for field in updatable:
        if field in kwargs and kwargs[field] is not None:
            sets.append(f"{field} = ?")
            params.append(kwargs[field])

    if not sets:
        conn.close()
        return {"status": "error", "message": "No updatable fields provided"}

    # Re-compute effective status with merged data
    merged = dict(row)
    merged.update({k: v for k, v in kwargs.items() if v is not None})
    effective_status = _determine_status(merged)
    sets.append("compliance_status = ?")
    params.append(effective_status)

    sets.append("updated_at = ?")
    params.append(_now())
    params.append(member_id)

    conn.execute(
        f"UPDATE pg_cmmc_supply_chain SET {', '.join(sets)} WHERE id = %s",
        params,  # nosec B608 -- table/column names are internal constants, not user input
    )
    _audit(conn, "update_member", f"Updated member {member_id}: {list(kwargs.keys())}, status={effective_status}")
    conn.commit()
    conn.close()

    return {
        "status": "ok",
        "member_id": member_id,
        "updated_fields": list(kwargs.keys()),
        "compliance_status": effective_status,
    }


# ── Validation & Compliance ──────────────────────────────────────────


def validate_team(opportunity_id):
    """Validate all team members for CMMC compliance.

    Recomputes effective status for each member and returns detailed results.

    Args:
        opportunity_id: Parent opportunity UUID.

    Returns:
        Dict with per-member validation results.
    """
    conn = _get_db()
    members = conn.execute(
        "SELECT * FROM pg_cmmc_supply_chain WHERE opportunity_id = %s ORDER BY role ASC, team_member_name ASC",
        (opportunity_id,),
    ).fetchall()

    if not members:
        conn.close()
        return {
            "status": "ok",
            "opportunity_id": opportunity_id,
            "message": "No team members registered",
            "members": [],
            "summary": {"total": 0, "compliant": 0, "non_compliant": 0, "in_progress": 0, "unknown": 0, "expired": 0},
        }

    results = []
    status_counts = {"compliant": 0, "non_compliant": 0, "in_progress": 0, "unknown": 0, "expired": 0}

    for m in members:
        member = dict(m)
        effective_status = _determine_status(member)

        # Update DB if status changed
        if effective_status != member.get("compliance_status"):
            conn.execute(
                "UPDATE pg_cmmc_supply_chain SET compliance_status = %s, updated_at = %s WHERE id = %s",
                (effective_status, _now(), member["id"]),
            )

        status_counts[effective_status] = status_counts.get(effective_status, 0) + 1

        # Build issue list
        issues = []
        if effective_status == "expired":
            issues.append(f"CMMC certification expired ({member.get('cert_expiry', 'unknown')})")
        if effective_status == "non_compliant":
            req = member.get("required_cmmc_level", "?")
            act = member.get("actual_cmmc_level", "N/A")
            issues.append(f"Required Level {req}, actual Level {act}")
        if effective_status == "unknown":
            issues.append("No CMMC assessment data on file")
        if member.get("poam_status") == "open":
            issues.append("POA&M open — remediation in progress")
        if member.get("sprs_score") is not None and member["sprs_score"] < 110:
            issues.append(f"SPRS score {member['sprs_score']} (below maximum 110)")

        results.append(
            {
                "member_id": member["id"],
                "team_member_name": member["team_member_name"],
                "role": member["role"],
                "role_label": _role_label(member["role"]),
                "required_level": member["required_cmmc_level"],
                "actual_cmmc_level": member.get("actual_cmmc_level"),
                "sprs_score": member.get("sprs_score"),
                "assessment_type": member.get("assessment_type"),
                "poam_status": member.get("poam_status", "none"),
                "cert_expiry": member.get("cert_expiry"),
                "compliance_status": effective_status,
                "issues": issues,
            }
        )

    _audit(
        conn,
        "validate_team",
        f"Validated {len(members)} members for {opportunity_id}: "
        f"{status_counts['compliant']} compliant, {status_counts['non_compliant']} non-compliant",
    )
    conn.commit()
    conn.close()

    return {
        "status": "ok",
        "opportunity_id": opportunity_id,
        "members": results,
        "summary": {
            "total": len(members),
            **status_counts,
        },
    }


def check_compliance(opportunity_id):
    """Overall compliance status summary.

    Args:
        opportunity_id: Parent opportunity UUID.

    Returns:
        Dict with overall compliance determination.
    """
    validation = validate_team(opportunity_id)
    if validation.get("status") != "ok":
        return validation

    summary = validation["summary"]
    total = summary["total"]

    if total == 0:
        return {
            "status": "ok",
            "opportunity_id": opportunity_id,
            "overall_compliance": "no_data",
            "message": "No team members registered for CMMC validation",
        }

    if summary["non_compliant"] > 0 or summary["unknown"] > 0 or summary["expired"] > 0:
        overall = "non_compliant"
        message = (
            f"{summary['non_compliant']} non-compliant, "
            f"{summary['unknown']} unknown, "
            f"{summary['expired']} expired out of {total} members"
        )
    elif summary["in_progress"] > 0:
        overall = "conditional"
        message = f"{summary['in_progress']} members have open POA&Ms — conditional compliance"
    else:
        overall = "compliant"
        message = f"All {total} team members are CMMC compliant"

    return {
        "status": "ok",
        "opportunity_id": opportunity_id,
        "overall_compliance": overall,
        "message": message,
        "summary": summary,
    }


def generate_flow_down(opportunity_id):
    """Generate DFARS 252.204-7021 flow-down text for all team members.

    Args:
        opportunity_id: Parent opportunity UUID.

    Returns:
        Dict with generated flow-down text.
    """
    conn = _get_db()
    members = conn.execute(
        "SELECT * FROM pg_cmmc_supply_chain WHERE opportunity_id = %s ORDER BY role ASC, team_member_name ASC",
        (opportunity_id,),
    ).fetchall()
    conn.close()

    if not members:
        return {
            "status": "ok",
            "opportunity_id": opportunity_id,
            "flow_down_text": "",
            "message": "No team members — flow-down text not generated",
        }

    member_sections = []
    for m in members:
        section = MEMBER_SECTION_TEMPLATE.format(
            role_label=_role_label(m["role"]),
            name=m["team_member_name"],
            required_level=m.get("required_cmmc_level", "TBD"),
            assessment_type=m.get("assessment_type", "TBD"),
            cage=m.get("cage_code", "N/A"),
            status=m.get("compliance_status", "unknown"),
            sprs_score=m.get("sprs_score", "N/A"),
            poam_status=m.get("poam_status", "none"),
            cert_expiry=m.get("cert_expiry", "N/A"),
        )
        member_sections.append(section)

    flow_down_text = FLOW_DOWN_TEMPLATE.format(
        member_sections="\n".join(member_sections),
        generated_at=_now(),
    )

    return {
        "status": "ok",
        "opportunity_id": opportunity_id,
        "flow_down_text": flow_down_text,
        "member_count": len(members),
    }


def get_supply_chain_status(opportunity_id):
    """Dashboard-ready supply chain status.

    Args:
        opportunity_id: Parent opportunity UUID.

    Returns:
        Dict with status grid data.
    """
    validation = validate_team(opportunity_id)
    if validation.get("status") != "ok":
        return validation

    compliance = check_compliance(opportunity_id)

    # Build status grid
    members_by_role = {}
    for m in validation.get("members", []):
        role = m["role"]
        if role not in members_by_role:
            members_by_role[role] = []
        members_by_role[role].append(
            {
                "name": m["team_member_name"],
                "level": m["actual_cmmc_level"],
                "required": m["required_level"],
                "status": m["compliance_status"],
                "sprs": m.get("sprs_score"),
            }
        )

    return {
        "status": "ok",
        "opportunity_id": opportunity_id,
        "overall_compliance": compliance.get("overall_compliance", "unknown"),
        "message": compliance.get("message", ""),
        "summary": validation.get("summary", {}),
        "by_role": members_by_role,
    }


def gate_evaluate(opportunity_id):
    """Pass/fail gate evaluation for proposal CMMC readiness.

    Gate criteria:
        - PASS: All team members compliant or poam_status='closed'
        - WARN: Any members with poam_status='open'
        - FAIL: Any members non_compliant, unknown, or expired

    Args:
        opportunity_id: Parent opportunity UUID.

    Returns:
        Dict with gate result and details.
    """
    compliance = check_compliance(opportunity_id)
    if compliance.get("status") != "ok":
        return {"status": "error", "gate": "fail", "message": compliance.get("message", "Error")}

    overall = compliance.get("overall_compliance", "unknown")
    summary = compliance.get("summary", {})

    if overall == "compliant":
        gate = "pass"
        blocking = []
    elif overall == "conditional":
        gate = "warn"
        blocking = [f"{summary.get('in_progress', 0)} team member(s) with open POA&M"]
    elif overall == "no_data":
        gate = "fail"
        blocking = ["No team members registered for CMMC validation"]
    else:
        gate = "fail"
        blocking = []
        if summary.get("non_compliant", 0) > 0:
            blocking.append(f"{summary['non_compliant']} non-compliant member(s)")
        if summary.get("unknown", 0) > 0:
            blocking.append(f"{summary['unknown']} member(s) with unknown status")
        if summary.get("expired", 0) > 0:
            blocking.append(f"{summary['expired']} member(s) with expired certification")

    result = {
        "status": "ok",
        "gate": gate,
        "opportunity_id": opportunity_id,
        "overall_compliance": overall,
        "blocking_issues": blocking,
        "summary": summary,
    }

    if gate == "fail":
        result["recommendation"] = (
            "Resolve all blocking issues before proposal submission. "
            "Ensure all team members have current CMMC certification or an approved POA&M."
        )

    return result


# ── CLI ──────────────────────────────────────────────────────────────


def main():
    parser = argparse.ArgumentParser(description="ICDEV™ Proposal Genesis — CMMC Supply Chain Validator (§3.14)")

    # Actions
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--validate", action="store_true", help="Validate all team members")
    group.add_argument("--add-member", action="store_true", help="Add a team member")
    group.add_argument("--update-member", action="store_true", help="Update a team member")
    group.add_argument("--flow-down", action="store_true", help="Generate DFARS flow-down text")
    group.add_argument("--status", action="store_true", help="Dashboard-ready status")
    group.add_argument("--gate", action="store_true", help="Pass/fail gate evaluation")

    # Identifiers
    parser.add_argument("--opportunity-id", help="Opportunity UUID")
    parser.add_argument("--member-id", help="Team member UUID (for --update-member)")

    # Member fields
    parser.add_argument("--name", help="Organization name")
    parser.add_argument("--role", choices=MEMBER_ROLES, help="Team role")
    parser.add_argument("--required-level", type=int, choices=[1, 2, 3], help="Required CMMC level")
    parser.add_argument("--actual-level", type=int, help="Current actual CMMC level")
    parser.add_argument("--cage", help="CAGE code")
    parser.add_argument("--sprs-score", type=int, help="SPRS score (-203 to 110)")
    parser.add_argument("--assessment-type", choices=ASSESSMENT_TYPES, help="Assessment type")
    parser.add_argument("--poam-status", choices=POAM_STATUSES, default="none", help="POA&M status")
    parser.add_argument("--cert-expiry", help="Certification expiry (ISO 8601)")

    # Output format
    parser.add_argument("--json", action="store_true", help="JSON output")
    parser.add_argument("--human", action="store_true", help="Human-readable output")

    args = parser.parse_args()

    result = None

    if args.add_member:
        if not args.opportunity_id or not args.name or not args.role or not args.required_level:
            print("Error: --opportunity-id, --name, --role, and --required-level required", file=sys.stderr)
            sys.exit(1)
        if args.sprs_score is not None and not -203 <= args.sprs_score <= 110:
            print("Error: --sprs-score must be between -203 and 110", file=sys.stderr)
            sys.exit(1)
        result = add_team_member(
            opportunity_id=args.opportunity_id,
            name=args.name,
            role=args.role,
            required_cmmc_level=args.required_level,
            cage=args.cage,
            actual_cmmc_level=args.actual_cmmc_level,
            sprs_score=args.sprs_score,
            assessment_type=args.assessment_type,
            poam_status=args.poam_status,
            cert_expiry=args.cert_expiry,
        )

    elif args.update_member:
        if not args.member_id:
            print("Error: --member-id required for --update-member", file=sys.stderr)
            sys.exit(1)
        kwargs = {}
        if args.name:
            kwargs["team_member_name"] = args.name
        if args.role:
            kwargs["role"] = args.role
        if args.required_level:
            kwargs["required_cmmc_level"] = args.required_level
        if args.actual_cmmc_level is not None:
            kwargs["actual_cmmc_level"] = args.actual_cmmc_level
        if args.sprs_score is not None:
            kwargs["sprs_score"] = args.sprs_score
        if args.assessment_type:
            kwargs["assessment_type"] = args.assessment_type
        if args.poam_status:
            kwargs["poam_status"] = args.poam_status
        if args.cert_expiry:
            kwargs["cert_expiry"] = args.cert_expiry
        if args.cage:
            kwargs["cage_code"] = args.cage
        result = update_member(args.member_id, **kwargs)

    elif args.validate:
        if not args.opportunity_id:
            print("Error: --opportunity-id required", file=sys.stderr)
            sys.exit(1)
        result = validate_team(args.opportunity_id)

    elif args.flow_down:
        if not args.opportunity_id:
            print("Error: --opportunity-id required", file=sys.stderr)
            sys.exit(1)
        result = generate_flow_down(args.opportunity_id)

    elif args.status:
        if not args.opportunity_id:
            print("Error: --opportunity-id required", file=sys.stderr)
            sys.exit(1)
        result = get_supply_chain_status(args.opportunity_id)

    elif args.gate:
        if not args.opportunity_id:
            print("Error: --opportunity-id required", file=sys.stderr)
            sys.exit(1)
        result = gate_evaluate(args.opportunity_id)
        # Exit with non-zero for CI/CD gate integration
        if result.get("gate") == "fail":
            print(json.dumps(result, indent=2, default=str))
            sys.exit(1)

    # Output
    if args.human:
        print(f"\n{'=' * 60}")
        print(f"  CMMC Supply Chain Validator — {args.opportunity_id or args.member_id or '?'}")
        print(f"{'=' * 60}")
        if "members" in result:
            for m in result["members"]:
                status_icon = {
                    "compliant": "[PASS]",
                    "in_progress": "[WARN]",
                    "non_compliant": "[FAIL]",
                    "unknown": "[????]",
                    "expired": "[EXPD]",
                }.get(m["compliance_status"], "[????]")
                print(
                    f"  {status_icon} {m['team_member_name']:30s} L{m.get('required_level', '?')} ({m['role_label']})"
                )
                for issue in m.get("issues", []):
                    print(f"         -> {issue}")
        if "gate" in result:
            gate_icon = {"pass": "[PASS]", "warn": "[WARN]", "fail": "[FAIL]"}.get(result["gate"], "?")
            print(f"\n  Gate: {gate_icon} {result['gate'].upper()}")
            for b in result.get("blocking_issues", []):
                print(f"    - {b}")
        if "flow_down_text" in result and result["flow_down_text"]:
            print(f"\n{result['flow_down_text']}")
        if "summary" in result:
            s = result["summary"]
            print(
                f"\n  Total: {s.get('total', 0)} | "
                f"Compliant: {s.get('compliant', 0)} | "
                f"Non-Compliant: {s.get('non_compliant', 0)} | "
                f"In Progress: {s.get('in_progress', 0)} | "
                f"Unknown: {s.get('unknown', 0)} | "
                f"Expired: {s.get('expired', 0)}"
            )
        print()
    else:
        print(json.dumps(result, indent=2, default=str))


if __name__ == "__main__":
    main()
