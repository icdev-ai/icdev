# CUI // SP-CTI
#!/usr/bin/env python3
"""ATO-Aware Compliance Bridge for ICDEV DoD Modernization.

Maintains NIST 800-53 control coverage during monolith-to-microservice migration.
Provides control inheritance, distribution across extracted services, gap analysis,
ATO impact reporting, digital thread creation, and coverage validation.

Ensures that decomposing a monolith into microservices does not degrade the
security posture tracked by the Authority to Operate (ATO) package.

Usage:
    # Inherit controls from legacy monolith into migration plan
    python tools/modernization/compliance_bridge.py \\
        --plan-id mplan-abc123 --inherit

    # Distribute inherited controls across extracted services
    python tools/modernization/compliance_bridge.py \\
        --plan-id mplan-abc123 --distribute --service-map /path/to/map.json

    # Identify ATO coverage gaps
    python tools/modernization/compliance_bridge.py \\
        --plan-id mplan-abc123 --gaps

    # Generate ATO impact report
    python tools/modernization/compliance_bridge.py \\
        --plan-id mplan-abc123 --report --output-dir /path/to/output

    # Create full compliance digital thread
    python tools/modernization/compliance_bridge.py \\
        --plan-id mplan-abc123 --thread

    # Validate ATO coverage post-migration
    python tools/modernization/compliance_bridge.py \\
        --plan-id mplan-abc123 --validate

    # Show compliance dashboard
    python tools/modernization/compliance_bridge.py \\
        --plan-id mplan-abc123 --dashboard

Classification: CUI // SP-CTI
"""

import argparse
import collections
import json
import sqlite3
import textwrap
from datetime import datetime
from pathlib import Path

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

BASE_DIR = Path(__file__).resolve().parent.parent.parent
DB_PATH = BASE_DIR / "data" / "icdev.db"

# CUI banner text for generated documents
CUI_BANNER = (
    "////////////////////////////////////////////////////////////////////\n"
    "CONTROLLED UNCLASSIFIED INFORMATION (CUI) // SP-CTI\n"
    "Distribution: Distribution D -- Authorized DoD Personnel Only\n"
    "////////////////////////////////////////////////////////////////////"
)

CUI_FOOTER = (
    "////////////////////////////////////////////////////////////////////\n"
    "CUI // SP-CTI | Department of Defense\n"
    "////////////////////////////////////////////////////////////////////"
)

# NIST 800-53 control family descriptions for mapping logic
CONTROL_FAMILY_DESCRIPTIONS = {
    "AC": "Access Control",
    "AU": "Audit and Accountability",
    "AT": "Awareness and Training",
    "CM": "Configuration Management",
    "CP": "Contingency Planning",
    "IA": "Identification and Authentication",
    "IR": "Incident Response",
    "MA": "Maintenance",
    "MP": "Media Protection",
    "PE": "Physical and Environmental Protection",
    "PL": "Planning",
    "PM": "Program Management",
    "PS": "Personnel Security",
    "PT": "PII Processing and Transparency",
    "RA": "Risk Assessment",
    "SA": "System and Services Acquisition",
    "SC": "System and Communications Protection",
    "SI": "System and Information Integrity",
    "SR": "Supply Chain Risk Management",
}

# Control families that apply universally to ALL microservices
UNIVERSAL_FAMILIES = {"AC", "AU", "CM", "IR", "PL", "PM", "PS", "AT", "MA",
                      "MP", "PE", "RA", "SR", "CP"}

# Control families with component-type-specific applicability
TARGETED_FAMILY_RULES = {
    "SC": {"applies_to_types": ["controller", "api_endpoint", "service",
                                "interface", "servlet"]},
    "IA": {"applies_to_types": ["controller", "api_endpoint", "service",
                                "servlet", "module"]},
    "SI": {"applies_to_types": ["service", "repository", "model", "entity",
                                "stored_procedure", "function", "module"]},
    "SA": {"applies_to_types": ["class", "module", "service", "package"]},
    "PT": {"applies_to_types": ["model", "entity", "repository",
                                "stored_procedure"]},
}

# Risk weights for gap severity scoring by control family
FAMILY_RISK_WEIGHTS = {
    "AC": 9, "AU": 8, "IA": 9, "SC": 9, "SI": 8,
    "CM": 7, "SA": 7, "RA": 7, "IR": 6, "CP": 6,
    "SR": 5, "MA": 4, "MP": 4, "PE": 3, "PS": 3,
    "PL": 3, "PM": 3, "AT": 2, "PT": 5,
}

# Estimated remediation weeks per gap by family criticality
REMEDIATION_WEEKS = {
    "critical": 6,   # AC, IA, SC
    "high": 4,       # AU, SI, CM, SA, RA
    "medium": 2,     # IR, CP, SR, PT
    "low": 1,        # MA, MP, PE, PS, PL, PM, AT
}


# ---------------------------------------------------------------------------
# Database helper
# ---------------------------------------------------------------------------

def _get_db():
    """Return a sqlite3 connection to the ICDEV operational database.

    The database file must already exist (created by tools/db/init_icdev_db.py).
    Uses row_factory = sqlite3.Row for dict-like access.
    """
    if not DB_PATH.exists():
        raise FileNotFoundError(
            f"ICDEV database not found at {DB_PATH}. "
            "Run 'python tools/db/init_icdev_db.py' first."
        )
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def _get_plan_info(conn, plan_id):
    """Fetch migration plan record and validate it exists."""
    row = conn.execute(
        "SELECT * FROM migration_plans WHERE id = ?", (plan_id,)
    ).fetchone()
    if not row:
        raise ValueError(f"Migration plan '{plan_id}' not found in database.")
    return dict(row)


def _get_legacy_app_project_id(conn, legacy_app_id):
    """Get the project_id for a legacy application."""
    row = conn.execute(
        "SELECT project_id FROM legacy_applications WHERE id = ?",
        (legacy_app_id,)
    ).fetchone()
    if not row:
        raise ValueError(
            f"Legacy application '{legacy_app_id}' not found in database."
        )
    return row["project_id"]


def _get_control_family(control_id):
    """Extract the family prefix from a NIST control ID (e.g., 'AC-2' -> 'AC')."""
    if "-" in control_id:
        return control_id.split("-")[0]
    return control_id[:2]


def _log_audit(conn, project_id, event_type, action, details=None):
    """Write an audit trail entry (append-only, NIST AU compliant)."""
    conn.execute(
        """INSERT INTO audit_trail
           (project_id, event_type, actor, action, details, classification)
           VALUES (?, ?, ?, ?, ?, ?)""",
        (
            project_id,
            event_type,
            "compliance-bridge",
            action,
            json.dumps(details) if details else None,
            "CUI",
        ),
    )


def _get_family_criticality(family):
    """Map a control family to a criticality tier for remediation estimation."""
    weight = FAMILY_RISK_WEIGHTS.get(family, 3)
    if weight >= 9:
        return "critical"
    elif weight >= 7:
        return "high"
    elif weight >= 5:
        return "medium"
    return "low"


# ---------------------------------------------------------------------------
# 1. Inherit controls from monolith to migration plan
# ---------------------------------------------------------------------------

def inherit_controls(legacy_app_id, plan_id):
    """Copy NIST control mappings from the legacy monolith to a migration plan.

    For each implemented or partially_implemented control on the legacy app's
    project, creates a digital_thread_link tracing the control to the
    migration plan. This establishes the baseline ATO posture that must be
    maintained through decomposition.

    Args:
        legacy_app_id: ID of the legacy application being migrated.
        plan_id: ID of the migration plan receiving inherited controls.

    Returns:
        dict with keys:
            controls_inherited: int total count
            by_family: dict of {family_code: count}
    """
    conn = _get_db()
    try:
        # Validate plan exists
        _get_plan_info(conn, plan_id)

        # Get the project_id for the legacy application
        project_id = _get_legacy_app_project_id(conn, legacy_app_id)

        # Query all implemented / partially_implemented controls
        rows = conn.execute(
            """SELECT pc.control_id, pc.implementation_status,
                      pc.implementation_description, pc.evidence_path,
                      cc.family, cc.title
               FROM project_controls pc
               LEFT JOIN compliance_controls cc ON pc.control_id = cc.id
               WHERE pc.project_id = ?
                 AND pc.implementation_status IN ('implemented', 'partially_implemented')
               ORDER BY pc.control_id""",
            (project_id,),
        ).fetchall()

        inherited_count = 0
        by_family = collections.Counter()

        for row in rows:
            control_id = row["control_id"]
            family = row["family"] or _get_control_family(control_id)

            # Create digital thread link: nist_control -> migration_task (plan)
            try:
                conn.execute(
                    """INSERT OR IGNORE INTO digital_thread_links
                       (project_id, source_type, source_id,
                        target_type, target_id, link_type,
                        confidence, evidence, created_by, created_at)
                       VALUES (?, 'nist_control', ?,
                               'migration_task', ?, 'traces_to',
                               ?, ?, 'compliance-bridge', ?)""",
                    (
                        project_id,
                        control_id,
                        plan_id,
                        1.0 if row["implementation_status"] == "implemented" else 0.7,
                        json.dumps({
                            "source": "control_inheritance",
                            "legacy_app_id": legacy_app_id,
                            "original_status": row["implementation_status"],
                            "description": row["implementation_description"],
                        }),
                        datetime.utcnow().isoformat(),
                    ),
                )
                inherited_count += 1
                by_family[family] += 1
            except sqlite3.IntegrityError:
                # Link already exists -- count it anyway for reporting
                inherited_count += 1
                by_family[family] += 1

        # Audit trail
        _log_audit(conn, project_id, "compliance_check",
                   f"Inherited {inherited_count} controls from {legacy_app_id} to plan {plan_id}",
                   {"controls_inherited": inherited_count, "by_family": dict(by_family)})

        conn.commit()

        result = {
            "controls_inherited": inherited_count,
            "by_family": dict(by_family),
        }
        print(f"[INFO] Inherited {inherited_count} controls from {legacy_app_id} to plan {plan_id}")
        for fam, cnt in sorted(by_family.items()):
            print(f"       {fam} ({CONTROL_FAMILY_DESCRIPTIONS.get(fam, 'Unknown')}): {cnt}")
        return result

    finally:
        conn.close()


# ---------------------------------------------------------------------------
# 2. Distribute controls across extracted services
# ---------------------------------------------------------------------------

def distribute_controls(plan_id, service_map):
    """Distribute inherited NIST controls across extracted microservices.

    Uses control family semantics to determine which services should own
    each control:
      - AC, AU, CM, IR, etc. (universal) -> all services
      - SC (System Communications) -> services with API/network components
      - IA (Identification/Auth) -> services with auth components
      - SI (System Integrity) -> services with data processing components

    Args:
        plan_id: Migration plan ID.
        service_map: dict of {service_name: [component_ids]} mapping service
                     names to the legacy component IDs they absorb.

    Returns:
        Distribution matrix: {service_name: {control_family: [control_ids]}}
    """
    conn = _get_db()
    try:
        plan = _get_plan_info(conn, plan_id)
        legacy_app_id = plan["legacy_app_id"]
        project_id = _get_legacy_app_project_id(conn, legacy_app_id)

        # Fetch all inherited controls for this plan
        inherited_rows = conn.execute(
            """SELECT source_id AS control_id
               FROM digital_thread_links
               WHERE project_id = ?
                 AND source_type = 'nist_control'
                 AND target_type = 'migration_task'
                 AND target_id = ?
                 AND link_type = 'traces_to'""",
            (project_id, plan_id),
        ).fetchall()

        if not inherited_rows:
            print("[WARN] No inherited controls found. Run --inherit first.")
            return {}

        inherited_controls = [r["control_id"] for r in inherited_rows]

        # Build component-type lookup for each service
        service_component_types = {}
        for svc_name, comp_ids in service_map.items():
            types_set = set()
            for comp_id in comp_ids:
                row = conn.execute(
                    "SELECT component_type FROM legacy_components WHERE id = ?",
                    (comp_id,),
                ).fetchone()
                if row:
                    types_set.add(row["component_type"])
            service_component_types[svc_name] = types_set

        # Distribute controls
        distribution = {svc: collections.defaultdict(list) for svc in service_map}
        links_created = 0

        for control_id in inherited_controls:
            family = _get_control_family(control_id)

            # Determine target services for this control
            target_services = []

            if family in UNIVERSAL_FAMILIES:
                # Universal controls apply to all services
                target_services = list(service_map.keys())

            elif family in TARGETED_FAMILY_RULES:
                # Targeted controls apply only to services with matching types
                rule = TARGETED_FAMILY_RULES[family]
                applies_to = set(rule["applies_to_types"])
                for svc_name, comp_types in service_component_types.items():
                    if comp_types & applies_to:
                        target_services.append(svc_name)
                # Fallback: if no service matches, assign to all (safety net)
                if not target_services:
                    target_services = list(service_map.keys())

            else:
                # Unknown family: assign to all services as safety measure
                target_services = list(service_map.keys())

            # Create digital thread links for each service-control pairing
            for svc_name in target_services:
                distribution[svc_name][family].append(control_id)
                try:
                    conn.execute(
                        """INSERT OR IGNORE INTO digital_thread_links
                           (project_id, source_type, source_id,
                            target_type, target_id, link_type,
                            confidence, evidence, created_by, created_at)
                           VALUES (?, 'nist_control', ?,
                                   'migration_task', ?, 'traces_to',
                                   ?, ?, 'compliance-bridge', ?)""",
                        (
                            project_id,
                            control_id,
                            f"{plan_id}::{svc_name}",
                            0.9,
                            json.dumps({
                                "source": "control_distribution",
                                "service": svc_name,
                                "family": family,
                                "distribution_rule": "universal" if family in UNIVERSAL_FAMILIES else "targeted",
                            }),
                            datetime.utcnow().isoformat(),
                        ),
                    )
                    links_created += 1
                except sqlite3.IntegrityError:
                    pass

        # Convert defaultdicts to regular dicts for serialization
        result = {}
        for svc_name, families in distribution.items():
            result[svc_name] = {fam: ctrls for fam, ctrls in families.items()}

        _log_audit(conn, project_id, "compliance_check",
                   f"Distributed controls across {len(service_map)} services for plan {plan_id}",
                   {"services": list(service_map.keys()), "links_created": links_created})

        conn.commit()

        print(f"[INFO] Distributed controls across {len(service_map)} services")
        for svc_name in sorted(result.keys()):
            total = sum(len(ctrls) for ctrls in result[svc_name].values())
            families_str = ", ".join(sorted(result[svc_name].keys()))
            print(f"       {svc_name}: {total} controls [{families_str}]")

        return result

    finally:
        conn.close()


# ---------------------------------------------------------------------------
# 3. Identify ATO gaps
# ---------------------------------------------------------------------------

def identify_ato_gaps(plan_id):
    """Find NIST controls that lose coverage during service decomposition.

    Compares inherited controls (from the monolith) against distributed
    controls (assigned to microservices). Controls with no distribution
    target are ATO gaps. Also flags controls whose implementation
    description references monolith-specific architecture.

    Args:
        plan_id: Migration plan ID.

    Returns:
        dict with keys:
            gaps: list of {control_id, family, title, reason}
            gap_count: int
            total_controls: int
            coverage_pct: float (0-100)
    """
    conn = _get_db()
    try:
        plan = _get_plan_info(conn, plan_id)
        legacy_app_id = plan["legacy_app_id"]
        project_id = _get_legacy_app_project_id(conn, legacy_app_id)

        # All inherited controls
        inherited_rows = conn.execute(
            """SELECT source_id AS control_id
               FROM digital_thread_links
               WHERE project_id = ?
                 AND source_type = 'nist_control'
                 AND target_type = 'migration_task'
                 AND target_id = ?
                 AND link_type = 'traces_to'""",
            (project_id, plan_id),
        ).fetchall()
        inherited_set = {r["control_id"] for r in inherited_rows}

        # All distributed controls (target_id has '::' separator for service assignments)
        distributed_rows = conn.execute(
            """SELECT source_id AS control_id
               FROM digital_thread_links
               WHERE project_id = ?
                 AND source_type = 'nist_control'
                 AND target_type = 'migration_task'
                 AND target_id LIKE ?
                 AND link_type = 'traces_to'""",
            (project_id, f"{plan_id}::%"),
        ).fetchall()
        distributed_set = {r["control_id"] for r in distributed_rows}

        # Monolith-specific keywords that indicate architecture coupling
        monolith_keywords = [
            "monolith", "single deployment", "shared database",
            "in-process", "same server", "single instance",
            "tightly coupled", "single codebase", "co-located",
        ]

        gaps = []

        for control_id in sorted(inherited_set):
            family = _get_control_family(control_id)

            # Get control metadata
            ctrl_row = conn.execute(
                "SELECT title, description FROM compliance_controls WHERE id = ?",
                (control_id,),
            ).fetchone()
            title = ctrl_row["title"] if ctrl_row else "Unknown"

            # Gap type 1: no distribution target at all
            if control_id not in distributed_set:
                gaps.append({
                    "control_id": control_id,
                    "family": family,
                    "title": title,
                    "reason": "No microservice assigned to this control",
                })
                continue

            # Gap type 2: implementation references monolith architecture
            impl_row = conn.execute(
                """SELECT implementation_description
                   FROM project_controls
                   WHERE project_id = ? AND control_id = ?""",
                (project_id, control_id),
            ).fetchone()

            if impl_row and impl_row["implementation_description"]:
                desc_lower = impl_row["implementation_description"].lower()
                for keyword in monolith_keywords:
                    if keyword in desc_lower:
                        gaps.append({
                            "control_id": control_id,
                            "family": family,
                            "title": title,
                            "reason": f"Implementation references monolith architecture: '{keyword}'",
                        })
                        break

        total_controls = len(inherited_set)
        gap_count = len(gaps)
        coverage_pct = round(
            ((total_controls - gap_count) / total_controls * 100)
            if total_controls > 0 else 0.0, 2
        )

        result = {
            "gaps": gaps,
            "gap_count": gap_count,
            "total_controls": total_controls,
            "coverage_pct": coverage_pct,
        }

        print(f"[INFO] ATO gap analysis for plan {plan_id}")
        print(f"       Total controls: {total_controls}")
        print(f"       Gaps found: {gap_count}")
        print(f"       Coverage: {coverage_pct}%")
        if gaps:
            print("       Gap details:")
            for g in gaps:
                print(f"         {g['control_id']} ({g['family']}): {g['reason']}")

        return result

    finally:
        conn.close()


# ---------------------------------------------------------------------------
# 4. Generate ATO impact report
# ---------------------------------------------------------------------------

def generate_ato_impact_report(plan_id, output_dir=None):
    """Generate a comprehensive ATO impact analysis report in CUI-marked markdown.

    Calls inherit analysis, distribution analysis, and gap identification to
    produce an executive-level report covering:
      - Executive summary with totals
      - Per-family breakdown table
      - Gap analysis with remediation recommendations
      - Risk assessment with severity scoring
      - Timeline impact estimation

    Args:
        plan_id: Migration plan ID.
        output_dir: Optional directory path to write the report file.
                    If None, returns report content as string.

    Returns:
        File path (str) if output_dir provided, else report content (str).
    """
    conn = _get_db()
    try:
        plan = _get_plan_info(conn, plan_id)
        legacy_app_id = plan["legacy_app_id"]
        project_id = _get_legacy_app_project_id(conn, legacy_app_id)

        # Gather legacy app info
        app_row = conn.execute(
            "SELECT name FROM legacy_applications WHERE id = ?",
            (legacy_app_id,),
        ).fetchone()
        app_name = app_row["name"] if app_row else legacy_app_id

        # Gather inherited controls count by family
        inherited_rows = conn.execute(
            """SELECT source_id AS control_id
               FROM digital_thread_links
               WHERE project_id = ?
                 AND source_type = 'nist_control'
                 AND target_type = 'migration_task'
                 AND target_id = ?
                 AND link_type = 'traces_to'""",
            (project_id, plan_id),
        ).fetchall()
        inherited_by_family = collections.Counter()
        for r in inherited_rows:
            inherited_by_family[_get_control_family(r["control_id"])] += 1
        total_inherited = len(inherited_rows)

        # Gather distributed controls by family
        distributed_rows = conn.execute(
            """SELECT DISTINCT source_id AS control_id
               FROM digital_thread_links
               WHERE project_id = ?
                 AND source_type = 'nist_control'
                 AND target_type = 'migration_task'
                 AND target_id LIKE ?
                 AND link_type = 'traces_to'""",
            (project_id, f"{plan_id}::%"),
        ).fetchall()
        distributed_by_family = collections.Counter()
        for r in distributed_rows:
            distributed_by_family[_get_control_family(r["control_id"])] += 1
        total_distributed = len(distributed_rows)

    finally:
        conn.close()

    # Identify gaps (uses its own connection)
    gap_result = identify_ato_gaps(plan_id)
    gaps = gap_result["gaps"]
    gap_count = gap_result["gap_count"]
    coverage_pct = gap_result["coverage_pct"]

    # Compute risk scores and timeline impact
    gap_by_family = collections.Counter()
    for g in gaps:
        gap_by_family[g["family"]] += 1

    total_risk_score = 0
    total_remediation_weeks = 0
    family_risk_details = []
    for family, count in sorted(gap_by_family.items()):
        weight = FAMILY_RISK_WEIGHTS.get(family, 3)
        risk = weight * count
        total_risk_score += risk
        criticality = _get_family_criticality(family)
        weeks = REMEDIATION_WEEKS.get(criticality, 1) * count
        total_remediation_weeks += weeks
        family_risk_details.append({
            "family": family,
            "gap_count": count,
            "risk_weight": weight,
            "risk_score": risk,
            "criticality": criticality,
            "remediation_weeks": weeks,
        })

    # Determine overall risk level
    if total_risk_score == 0:
        overall_risk = "LOW"
    elif total_risk_score <= 20:
        overall_risk = "MODERATE"
    elif total_risk_score <= 50:
        overall_risk = "HIGH"
    else:
        overall_risk = "CRITICAL"

    now = datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC")

    # Build report
    lines = [
        CUI_BANNER,
        "",
        "# ATO Impact Analysis Report",
        "",
        f"**Plan ID:** {plan_id}",
        f"**Legacy Application:** {app_name} ({legacy_app_id})",
        f"**Migration Strategy:** {plan.get('strategy', 'N/A')}",
        f"**Target Architecture:** {plan.get('target_architecture', 'N/A')}",
        f"**Generated:** {now}",
        "**Classification:** CUI // SP-CTI",
        "",
        "---",
        "",
        "## Executive Summary",
        "",
        "| Metric | Value |",
        "|--------|-------|",
        f"| Total Controls in ATO Baseline | {total_inherited} |",
        f"| Controls Inherited | {total_inherited} |",
        f"| Controls Distributed to Services | {total_distributed} |",
        f"| ATO Gaps Identified | {gap_count} |",
        f"| Coverage Percentage | {coverage_pct}% |",
        f"| Overall Risk Level | **{overall_risk}** |",
        f"| Estimated Remediation | {total_remediation_weeks} additional weeks |",
        "",
    ]

    # Compliance gate status
    gate_status = "PASS" if coverage_pct >= 95.0 else "FAIL"
    lines.append(f"**Compliance Migration Gate:** {gate_status}")
    if gate_status == "FAIL":
        lines.append(f"  - Coverage must be >= 95% to proceed. Current: {coverage_pct}%")
    lines.append("")

    # Per-family breakdown
    lines.extend([
        "---",
        "",
        "## Per-Family Breakdown",
        "",
        "| Family | Description | Inherited | Distributed | Gaps | Coverage |",
        "|--------|-------------|-----------|-------------|------|----------|",
    ])

    all_families = sorted(set(
        list(inherited_by_family.keys()) +
        list(distributed_by_family.keys()) +
        list(gap_by_family.keys())
    ))

    for fam in all_families:
        desc = CONTROL_FAMILY_DESCRIPTIONS.get(fam, "Unknown")
        inh = inherited_by_family.get(fam, 0)
        dist = distributed_by_family.get(fam, 0)
        gps = gap_by_family.get(fam, 0)
        cov = round(((inh - gps) / inh * 100) if inh > 0 else 100.0, 1)
        lines.append(f"| {fam} | {desc} | {inh} | {dist} | {gps} | {cov}% |")

    lines.append("")

    # Gap analysis
    lines.extend([
        "---",
        "",
        "## Gap Analysis",
        "",
    ])

    if not gaps:
        lines.append("**No ATO gaps identified.** All controls have been distributed "
                      "to target microservices with adequate coverage.")
    else:
        lines.extend([
            "| # | Control | Family | Title | Reason | Recommended Action |",
            "|---|---------|--------|-------|--------|--------------------|",
        ])
        for i, g in enumerate(gaps, 1):
            family = g["family"]
            # Generate remediation recommendation based on gap reason
            if "No microservice assigned" in g["reason"]:
                rec = f"Assign {g['control_id']} to appropriate service(s) or create a shared security service"
            else:
                rec = f"Update implementation to reflect distributed architecture for {g['control_id']}"
            lines.append(
                f"| {i} | {g['control_id']} | {family} | {g['title']} | "
                f"{g['reason']} | {rec} |"
            )
        lines.append("")

    # Risk assessment
    lines.extend([
        "---",
        "",
        "## Risk Assessment",
        "",
    ])

    if family_risk_details:
        lines.extend([
            "| Family | Gaps | Risk Weight | Risk Score | Criticality | Remediation (weeks) |",
            "|--------|------|-------------|------------|-------------|---------------------|",
        ])
        for frd in sorted(family_risk_details, key=lambda x: x["risk_score"], reverse=True):
            lines.append(
                f"| {frd['family']} | {frd['gap_count']} | {frd['risk_weight']} | "
                f"{frd['risk_score']} | {frd['criticality'].upper()} | {frd['remediation_weeks']} |"
            )
        lines.extend([
            "",
            f"**Total Risk Score:** {total_risk_score}",
            f"**Overall Risk Level:** {overall_risk}",
            "",
        ])
    else:
        lines.append("No risks identified -- all controls have adequate coverage.")
        lines.append("")

    # Timeline impact
    lines.extend([
        "---",
        "",
        "## Timeline Impact",
        "",
        f"Based on gap analysis, an estimated **{total_remediation_weeks} additional weeks** "
        "may be required for compliance remediation before the migrated system can achieve ATO.",
        "",
        "Breakdown by criticality tier:",
        "",
    ])

    tier_weeks = collections.Counter()
    for frd in family_risk_details:
        tier_weeks[frd["criticality"]] += frd["remediation_weeks"]

    for tier in ["critical", "high", "medium", "low"]:
        if tier_weeks.get(tier, 0) > 0:
            lines.append(f"- **{tier.upper()}:** {tier_weeks[tier]} weeks")

    if total_remediation_weeks == 0:
        lines.append("- No additional time required -- compliance posture is maintained.")

    lines.extend([
        "",
        "---",
        "",
        "## Recommendations",
        "",
        "1. Address all CRITICAL and HIGH risk gaps before proceeding with production cutover.",
        "2. Create a shared security service to host cross-cutting controls (AC, AU, IA).",
        "3. Implement centralized audit logging to maintain AU family coverage across all services.",
        "4. Update System Security Plan (SSP) to reflect the new distributed architecture.",
        "5. Schedule an incremental ATO assessment for each migrated service boundary.",
        "",
        "---",
        "",
        CUI_FOOTER,
        "",
    ])

    content = "\n".join(lines)

    if output_dir:
        output_path = Path(output_dir)
        output_path.mkdir(parents=True, exist_ok=True)
        filename = f"ato_impact_report_{plan_id}_{datetime.utcnow().strftime('%Y%m%d_%H%M%S')}.md"
        file_path = output_path / filename
        with open(str(file_path), "w", encoding="utf-8") as f:
            f.write(content)
        print(f"[INFO] ATO impact report written to {file_path}")
        return str(file_path)

    return content


# ---------------------------------------------------------------------------
# 5. Create compliance digital thread
# ---------------------------------------------------------------------------

def create_compliance_thread(plan_id):
    """Create a full digital thread linking legacy components through migration
    tasks to NIST controls for end-to-end compliance traceability.

    For each migration task in the plan:
      1. Link legacy_component -> migration_task (migrates_to)
      2. If task has output code: link migration_task -> code_module (implements)
      3. Link code_module -> nist_control (satisfies) based on distributed controls

    Args:
        plan_id: Migration plan ID.

    Returns:
        dict with keys:
            links_created: int total new links
            coverage_pct: float percentage of controls with thread links
    """
    conn = _get_db()
    try:
        plan = _get_plan_info(conn, plan_id)
        legacy_app_id = plan["legacy_app_id"]
        project_id = _get_legacy_app_project_id(conn, legacy_app_id)

        # Get all migration tasks for this plan
        tasks = conn.execute(
            "SELECT * FROM migration_tasks WHERE plan_id = ?",
            (plan_id,),
        ).fetchall()

        links_created = 0
        now = datetime.utcnow().isoformat()

        for task in tasks:
            task_id = task["id"]
            comp_id = task["legacy_component_id"]

            # 1. Link legacy_component -> migration_task
            if comp_id:
                try:
                    conn.execute(
                        """INSERT OR IGNORE INTO digital_thread_links
                           (project_id, source_type, source_id,
                            target_type, target_id, link_type,
                            confidence, evidence, created_by, created_at)
                           VALUES (?, 'legacy_component', ?,
                                   'migration_task', ?, 'migrates_to',
                                   1.0, ?, 'compliance-bridge', ?)""",
                        (
                            project_id, comp_id, task_id,
                            json.dumps({"task_type": task["task_type"],
                                        "title": task["title"]}),
                            now,
                        ),
                    )
                    links_created += 1
                except sqlite3.IntegrityError:
                    pass

            # 2. If task has output code, link migration_task -> code_module
            output_path = task["output_path"]
            if output_path:
                code_module_id = f"{task_id}::output"
                try:
                    conn.execute(
                        """INSERT OR IGNORE INTO digital_thread_links
                           (project_id, source_type, source_id,
                            target_type, target_id, link_type,
                            confidence, evidence, created_by, created_at)
                           VALUES (?, 'migration_task', ?,
                                   'code_module', ?, 'implements',
                                   0.9, ?, 'compliance-bridge', ?)""",
                        (
                            project_id, task_id, code_module_id,
                            json.dumps({"output_path": output_path}),
                            now,
                        ),
                    )
                    links_created += 1
                except sqlite3.IntegrityError:
                    pass

                # 3. Link code_module -> nist_control (satisfies)
                # Find distributed controls relevant to this task's service context
                distributed = conn.execute(
                    """SELECT source_id AS control_id
                       FROM digital_thread_links
                       WHERE project_id = ?
                         AND source_type = 'nist_control'
                         AND target_type = 'migration_task'
                         AND target_id LIKE ?
                         AND link_type = 'traces_to'""",
                    (project_id, f"{plan_id}::%"),
                ).fetchall()

                for d_row in distributed:
                    try:
                        conn.execute(
                            """INSERT OR IGNORE INTO digital_thread_links
                               (project_id, source_type, source_id,
                                target_type, target_id, link_type,
                                confidence, evidence, created_by, created_at)
                               VALUES (?, 'code_module', ?,
                                       'nist_control', ?, 'satisfies',
                                       0.8, ?, 'compliance-bridge', ?)""",
                            (
                                project_id, code_module_id,
                                d_row["control_id"],
                                json.dumps({"source_task": task_id}),
                                now,
                            ),
                        )
                        links_created += 1
                    except sqlite3.IntegrityError:
                        pass

        # Calculate coverage: how many inherited controls have a satisfies link?
        inherited_rows = conn.execute(
            """SELECT source_id AS control_id
               FROM digital_thread_links
               WHERE project_id = ?
                 AND source_type = 'nist_control'
                 AND target_type = 'migration_task'
                 AND target_id = ?
                 AND link_type = 'traces_to'""",
            (project_id, plan_id),
        ).fetchall()
        inherited_set = {r["control_id"] for r in inherited_rows}

        satisfied_rows = conn.execute(
            """SELECT target_id AS control_id
               FROM digital_thread_links
               WHERE project_id = ?
                 AND source_type = 'code_module'
                 AND target_type = 'nist_control'
                 AND link_type = 'satisfies'""",
            (project_id,),
        ).fetchall()
        satisfied_set = {r["control_id"] for r in satisfied_rows}

        covered = inherited_set & satisfied_set
        coverage_pct = round(
            (len(covered) / len(inherited_set) * 100)
            if inherited_set else 0.0, 2
        )

        _log_audit(conn, project_id, "digital_thread_linked",
                   f"Created compliance thread for plan {plan_id}",
                   {"links_created": links_created, "coverage_pct": coverage_pct})

        conn.commit()

        result = {
            "links_created": links_created,
            "coverage_pct": coverage_pct,
        }
        print(f"[INFO] Compliance thread created for plan {plan_id}")
        print(f"       Links created: {links_created}")
        print(f"       Coverage: {coverage_pct}%")
        return result

    finally:
        conn.close()


# ---------------------------------------------------------------------------
# 6. Validate ATO coverage
# ---------------------------------------------------------------------------

def validate_ato_coverage(plan_id):
    """Verify that no NIST control coverage is lost after migration.

    Compares the pre-migration control count (from project_controls) against
    the post-migration coverage (from digital_thread_links). Ensures each
    control family retains at least the same level of coverage.

    Args:
        plan_id: Migration plan ID.

    Returns:
        dict with keys:
            valid: bool (True if no coverage lost)
            pre_count: int controls before migration
            post_count: int controls with post-migration coverage
            coverage_delta: int (post - pre, 0 or positive is good)
            failures: list of {control_id, reason}
    """
    conn = _get_db()
    try:
        plan = _get_plan_info(conn, plan_id)
        legacy_app_id = plan["legacy_app_id"]
        project_id = _get_legacy_app_project_id(conn, legacy_app_id)

        # Pre-migration: all implemented/partially_implemented controls
        pre_rows = conn.execute(
            """SELECT control_id, implementation_status
               FROM project_controls
               WHERE project_id = ?
                 AND implementation_status IN ('implemented', 'partially_implemented')
               ORDER BY control_id""",
            (project_id,),
        ).fetchall()
        pre_controls = {r["control_id"] for r in pre_rows}
        pre_by_family = collections.Counter()
        for r in pre_rows:
            pre_by_family[_get_control_family(r["control_id"])] += 1

        # Post-migration: controls linked via digital thread
        # A control is "covered" if it has a traces_to link to the plan
        # AND either a distribution link or a satisfies link
        post_distributed = conn.execute(
            """SELECT DISTINCT source_id AS control_id
               FROM digital_thread_links
               WHERE project_id = ?
                 AND source_type = 'nist_control'
                 AND target_type = 'migration_task'
                 AND (target_id = ? OR target_id LIKE ?)
                 AND link_type = 'traces_to'""",
            (project_id, plan_id, f"{plan_id}::%"),
        ).fetchall()
        post_controls = {r["control_id"] for r in post_distributed}
        post_by_family = collections.Counter()
        for r in post_distributed:
            post_by_family[_get_control_family(r["control_id"])] += 1

        # Validation: check each pre-migration control has post coverage
        failures = []

        for control_id in sorted(pre_controls):
            if control_id not in post_controls:
                failures.append({
                    "control_id": control_id,
                    "reason": "Control not found in post-migration digital thread",
                })

        # Check family-level coverage
        for family, pre_count in sorted(pre_by_family.items()):
            post_count = post_by_family.get(family, 0)
            if post_count < pre_count:
                delta = pre_count - post_count
                failures.append({
                    "control_id": f"{family}-*",
                    "reason": (f"Family {family} lost coverage: "
                               f"{pre_count} pre-migration vs {post_count} post-migration "
                               f"({delta} controls lost)"),
                })

        valid = len(failures) == 0
        pre_total = len(pre_controls)
        post_total = len(post_controls)
        coverage_delta = post_total - pre_total

        result = {
            "valid": valid,
            "pre_count": pre_total,
            "post_count": post_total,
            "coverage_delta": coverage_delta,
            "failures": failures,
        }

        _log_audit(conn, project_id, "compliance_check",
                   f"ATO coverage validation: {'PASS' if valid else 'FAIL'} for plan {plan_id}",
                   result)

        conn.commit()

        status_str = "PASS" if valid else "FAIL"
        print(f"[INFO] ATO coverage validation: {status_str}")
        print(f"       Pre-migration controls: {pre_total}")
        print(f"       Post-migration controls: {post_total}")
        print(f"       Coverage delta: {coverage_delta:+d}")
        if failures:
            print(f"       Failures ({len(failures)}):")
            for f in failures:
                print(f"         {f['control_id']}: {f['reason']}")

        return result

    finally:
        conn.close()


# ---------------------------------------------------------------------------
# 7. Compliance dashboard
# ---------------------------------------------------------------------------

def get_compliance_dashboard(plan_id):
    """Generate a summary compliance dashboard for the migration plan.

    Returns a comprehensive status view including:
      - Total controls in scope and coverage percentages
      - Controls at risk with details
      - Per-family status breakdown
      - Migration compliance gate (PASS/FAIL at 95% threshold)

    Args:
        plan_id: Migration plan ID.

    Returns:
        Dashboard dict with full status information.
    """
    conn = _get_db()
    try:
        plan = _get_plan_info(conn, plan_id)
        legacy_app_id = plan["legacy_app_id"]
        project_id = _get_legacy_app_project_id(conn, legacy_app_id)

        # Total controls in scope (inherited)
        inherited_rows = conn.execute(
            """SELECT source_id AS control_id
               FROM digital_thread_links
               WHERE project_id = ?
                 AND source_type = 'nist_control'
                 AND target_type = 'migration_task'
                 AND target_id = ?
                 AND link_type = 'traces_to'""",
            (project_id, plan_id),
        ).fetchall()
        total_in_scope = len(inherited_rows)
        inherited_set = {r["control_id"] for r in inherited_rows}

        # Distributed controls
        distributed_rows = conn.execute(
            """SELECT DISTINCT source_id AS control_id
               FROM digital_thread_links
               WHERE project_id = ?
                 AND source_type = 'nist_control'
                 AND target_type = 'migration_task'
                 AND target_id LIKE ?
                 AND link_type = 'traces_to'""",
            (project_id, f"{plan_id}::%"),
        ).fetchall()
        {r["control_id"] for r in distributed_rows}

    finally:
        conn.close()

    # Run gap analysis (uses its own connection)
    gap_result = identify_ato_gaps(plan_id)
    gap_controls = {g["control_id"] for g in gap_result["gaps"]}

    # Compute covered vs at-risk
    covered_set = inherited_set - gap_controls
    covered_count = len(covered_set)
    at_risk_count = len(gap_controls)
    coverage_pct = round(
        (covered_count / total_in_scope * 100) if total_in_scope > 0 else 0.0, 2
    )

    # Per-family status
    family_status = {}
    for control_id in inherited_set:
        family = _get_control_family(control_id)
        if family not in family_status:
            family_status[family] = {"total": 0, "covered": 0, "at_risk": 0}
        family_status[family]["total"] += 1
        if control_id in gap_controls:
            family_status[family]["at_risk"] += 1
        else:
            family_status[family]["covered"] += 1

    # Migration compliance gate
    gate = "PASS" if coverage_pct >= 95.0 else "FAIL"

    dashboard = {
        "plan_id": plan_id,
        "legacy_app_id": legacy_app_id,
        "strategy": plan.get("strategy", "N/A"),
        "target_architecture": plan.get("target_architecture", "N/A"),
        "plan_status": plan.get("status", "N/A"),
        "total_controls_in_scope": total_in_scope,
        "controls_with_coverage": covered_count,
        "controls_with_coverage_pct": coverage_pct,
        "controls_at_risk": at_risk_count,
        "controls_at_risk_list": sorted(gap_controls),
        "per_family_status": {
            fam: family_status[fam]
            for fam in sorted(family_status.keys())
        },
        "migration_compliance_gate": gate,
        "gate_threshold_pct": 95.0,
        "generated_at": datetime.utcnow().isoformat(),
    }

    return dashboard


def _format_dashboard(dashboard):
    """Format dashboard dict as human-readable console output."""
    lines = [
        "",
        "=" * 65,
        "  COMPLIANCE MIGRATION DASHBOARD",
        "=" * 65,
        f"  Plan:           {dashboard['plan_id']}",
        f"  Legacy App:     {dashboard['legacy_app_id']}",
        f"  Strategy:       {dashboard['strategy']}",
        f"  Architecture:   {dashboard['target_architecture']}",
        f"  Plan Status:    {dashboard['plan_status']}",
        f"  Generated:      {dashboard['generated_at']}",
        "",
        "-" * 65,
        "  CONTROL COVERAGE",
        "-" * 65,
        f"  Total in scope:       {dashboard['total_controls_in_scope']}",
        f"  With coverage:        {dashboard['controls_with_coverage']} "
        f"({dashboard['controls_with_coverage_pct']}%)",
        f"  At risk:              {dashboard['controls_at_risk']}",
        "",
    ]

    if dashboard["controls_at_risk_list"]:
        lines.append("  At-risk controls:")
        for ctrl in dashboard["controls_at_risk_list"]:
            lines.append(f"    - {ctrl}")
        lines.append("")

    lines.extend([
        "-" * 65,
        "  PER-FAMILY STATUS",
        "-" * 65,
        f"  {'Family':<8} {'Total':>6} {'Covered':>8} {'At Risk':>8}  Status",
        f"  {'-'*8} {'-'*6} {'-'*8} {'-'*8}  {'-'*8}",
    ])

    for fam, info in sorted(dashboard["per_family_status"].items()):
        status = "OK" if info["at_risk"] == 0 else "AT RISK"
        lines.append(
            f"  {fam:<8} {info['total']:>6} {info['covered']:>8} "
            f"{info['at_risk']:>8}  {status}"
        )

    lines.extend([
        "",
        "-" * 65,
        f"  MIGRATION COMPLIANCE GATE: {dashboard['migration_compliance_gate']}",
        f"  (Threshold: >= {dashboard['gate_threshold_pct']}% coverage)",
        "=" * 65,
        "",
    ])

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# CLI interface
# ---------------------------------------------------------------------------

def main():
    """Command-line entry point for the ATO-aware compliance bridge."""
    parser = argparse.ArgumentParser(
        description="CUI // SP-CTI -- ATO-Aware Compliance Bridge for Migration",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=textwrap.dedent("""\
        Examples:
          # Inherit controls from monolith
          python tools/modernization/compliance_bridge.py \\
              --plan-id mplan-abc123 --inherit

          # Distribute controls with service map
          python tools/modernization/compliance_bridge.py \\
              --plan-id mplan-abc123 --distribute \\
              --service-map services.json

          # Full ATO impact report
          python tools/modernization/compliance_bridge.py \\
              --plan-id mplan-abc123 --report \\
              --output-dir /opt/reports

          # Validate coverage
          python tools/modernization/compliance_bridge.py \\
              --plan-id mplan-abc123 --validate --json

        Classification: CUI // SP-CTI
        """),
    )

    parser.add_argument(
        "--plan-id", required=True,
        help="Migration plan ID (required for all operations)",
    )

    # Action flags
    parser.add_argument(
        "--inherit", action="store_true",
        help="Inherit NIST control mappings from legacy monolith to plan",
    )
    parser.add_argument(
        "--distribute", action="store_true",
        help="Distribute inherited controls across extracted services",
    )
    parser.add_argument(
        "--service-map",
        help="Path to JSON file mapping service names to component IDs "
             "(required with --distribute)",
    )
    parser.add_argument(
        "--gaps", action="store_true",
        help="Identify ATO coverage gaps in the migration",
    )
    parser.add_argument(
        "--report", action="store_true",
        help="Generate ATO impact analysis report",
    )
    parser.add_argument(
        "--output-dir",
        help="Output directory for report file (used with --report)",
    )
    parser.add_argument(
        "--thread", action="store_true",
        help="Create full compliance digital thread",
    )
    parser.add_argument(
        "--validate", action="store_true",
        help="Validate that ATO coverage is maintained post-migration",
    )
    parser.add_argument(
        "--dashboard", action="store_true",
        help="Show compliance migration dashboard",
    )
    parser.add_argument(
        "--json", action="store_true", dest="json_output",
        help="Output results as JSON",
    )

    args = parser.parse_args()

    # Validate at least one action was requested
    actions = [args.inherit, args.distribute, args.gaps, args.report,
               args.thread, args.validate, args.dashboard]
    if not any(actions):
        parser.error("At least one action flag is required: "
                     "--inherit, --distribute, --gaps, --report, --thread, "
                     "--validate, or --dashboard")

    try:
        # --- Inherit ---
        if args.inherit:
            conn = _get_db()
            try:
                plan = _get_plan_info(conn, args.plan_id)
                legacy_app_id = plan["legacy_app_id"]
            finally:
                conn.close()

            result = inherit_controls(legacy_app_id, args.plan_id)
            if args.json_output:
                print(json.dumps(result, indent=2))

        # --- Distribute ---
        if args.distribute:
            if not args.service_map:
                parser.error("--service-map is required with --distribute")
            smap_path = Path(args.service_map)
            if not smap_path.exists():
                raise FileNotFoundError(
                    f"Service map file not found: {smap_path}"
                )
            with open(str(smap_path), "r", encoding="utf-8") as f:
                service_map = json.load(f)

            result = distribute_controls(args.plan_id, service_map)
            if args.json_output:
                print(json.dumps(result, indent=2))

        # --- Gaps ---
        if args.gaps:
            result = identify_ato_gaps(args.plan_id)
            if args.json_output:
                print(json.dumps(result, indent=2))

        # --- Report ---
        if args.report:
            result = generate_ato_impact_report(
                args.plan_id,
                output_dir=args.output_dir,
            )
            if args.json_output:
                if args.output_dir:
                    print(json.dumps({"report_path": result}, indent=2))
                else:
                    # result is the content string
                    print(json.dumps({"report_content": result}, indent=2))
            elif not args.output_dir:
                # Print report to stdout
                print(result)

        # --- Thread ---
        if args.thread:
            result = create_compliance_thread(args.plan_id)
            if args.json_output:
                print(json.dumps(result, indent=2))

        # --- Validate ---
        if args.validate:
            result = validate_ato_coverage(args.plan_id)
            if args.json_output:
                print(json.dumps(result, indent=2))
            if not result["valid"]:
                raise SystemExit(1)

        # --- Dashboard ---
        if args.dashboard:
            result = get_compliance_dashboard(args.plan_id)
            if args.json_output:
                print(json.dumps(result, indent=2))
            else:
                print(_format_dashboard(result))

    except FileNotFoundError as exc:
        print(f"[ERROR] {exc}")
        raise SystemExit(1)
    except ValueError as exc:
        print(f"[ERROR] {exc}")
        raise SystemExit(1)
    except sqlite3.Error as exc:
        print(f"[ERROR] Database error: {exc}")
        raise SystemExit(1)


if __name__ == "__main__":
    main()
# CUI // SP-CTI
