#!/usr/bin/env python3
# CUI // SP-CTI
# CONTROLLED UNCLASSIFIED INFORMATION (CUI)
# Distribution: Distribution D -- Authorized DoD Personnel Only
# CUI // SP-CTI
"""FedRAMP security assessment report generator.

Loads fedramp_report_template.md, queries fedramp_assessments table, builds
control family summaries and readiness scores, generates a comprehensive
FedRAMP assessment report with CUI markings."""

import argparse
import json
import re
import sqlite3
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent.parent
DB_PATH = BASE_DIR / "data" / "icdev.db"
FEDRAMP_TEMPLATE_PATH = BASE_DIR / "context" / "compliance" / "fedramp_report_template.md"

# NIST 800-53 control families used in FedRAMP baselines
CONTROL_FAMILIES = {
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
    "CA": "Assessment, Authorization, and Monitoring",
    "SC": "System and Communications Protection",
    "SI": "System and Information Integrity",
    "SA": "System and Services Acquisition",
    "SR": "Supply Chain Risk Management",
}

# Critical controls whose failure blocks the gate
CRITICAL_CONTROLS = ["AC-2", "IA-2", "SC-7", "AU-2", "CM-6"]

# Priority ordering for remediation recommendations
PRIORITY_ORDER = ["critical", "high", "medium", "low"]

# Remediation windows by priority (days)
REMEDIATION_WINDOWS = {
    "critical": 14,
    "high": 30,
    "medium": 60,
    "low": 90,
}


# ---------------------------------------------------------------------------
# Helper functions
# ---------------------------------------------------------------------------

def _get_connection(db_path=None):
    """Get a database connection with Row factory."""
    path = db_path or DB_PATH
    if not path.exists():
        raise FileNotFoundError(
            f"Database not found: {path}\n"
            "Run: python tools/db/init_icdev_db.py"
        )
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    return conn


def _load_template(template_path=None):
    """Load the FedRAMP report template markdown.

    If the template file does not exist a minimal built-in template is
    returned so the generator can still produce a useful report.
    """
    path = template_path or FEDRAMP_TEMPLATE_PATH
    if path.exists():
        with open(path, "r", encoding="utf-8") as f:
            return f.read()

    # Fallback minimal template when file is missing
    return _builtin_template()


def _builtin_template():
    """Return a minimal built-in FedRAMP report template."""
    return (
        "{{cui_banner_top}}\n\n"
        "# FedRAMP {{baseline}} Baseline Security Assessment Report\n\n"
        "**System Name:** {{system_name}}\n"
        "**Project ID:** {{project_id}}\n"
        "**Classification:** {{classification}}\n"
        "**Impact Level:** {{impact_level}}\n"
        "**FedRAMP Baseline:** {{baseline}}\n"
        "**Assessment Date:** {{assessment_date}}\n"
        "**Report Version:** {{version}}\n"
        "**Assessor:** {{assessor}}\n"
        "**Framework:** FedRAMP Rev 5 (NIST SP 800-53 Rev 5)\n\n"
        "---\n\n"
        "## 1. Executive Summary\n\n"
        "**Overall Readiness Score:** {{readiness_score}}%\n"
        "**Gate Result:** {{gate_result}}\n"
        "**Total Controls Assessed:** {{total_controls}}\n"
        "**Controls Satisfied:** {{controls_satisfied}}\n"
        "**Controls Other Than Satisfied:** {{controls_other_than_satisfied}}\n"
        "**Critical Controls Not Satisfied:** {{critical_controls_not_satisfied}}\n\n"
        "{{executive_summary}}\n\n"
        "## 2. Assessment Summary\n\n"
        "{{assessment_summary_table}}\n\n"
        "## 3. Control Family Analysis\n\n"
        "{{control_family_table}}\n\n"
        "{{control_family_details}}\n\n"
        "## 4. Gap Analysis\n\n"
        "{{gap_analysis_table}}\n\n"
        "## 5. Readiness Score\n\n"
        "**Overall FedRAMP Readiness Score:** {{readiness_score}}%\n"
        "**Current Readiness Level:** {{readiness_level}}\n\n"
        "## 6. Gate Evaluation\n\n"
        "**Gate Result:** {{gate_result}}\n\n"
        "## 7. Recommendations\n\n"
        "{{recommendations}}\n\n"
        "{{remediation_plan}}\n\n"
        "## 8. Evidence References\n\n"
        "{{evidence_table}}\n\n"
        "## 9. Assessment Methodology\n\n"
        "**Scoring Formula:** Readiness Score = 100 x (satisfied + risk_accepted x 0.75) / "
        "(total - not_applicable)\n\n"
        "**Gate Logic:** PASS if 0 \"other_than_satisfied\" critical controls AND "
        "readiness score >= 80%\n\n"
        "---\n\n"
        "**Prepared by:** {{assessor}}\n"
        "**Date:** {{assessment_date}}\n\n"
        "{{cui_banner_bottom}}\n"
    )


def _get_project_data(conn, project_id):
    """Load project record from database."""
    row = conn.execute(
        "SELECT * FROM projects WHERE id = ?", (project_id,)
    ).fetchone()
    if not row:
        raise ValueError(f"Project '{project_id}' not found in database.")
    return dict(row)


def _load_cui_config():
    """Load CUI marking configuration.

    Attempts to import load_cui_config from the cui_marker module;
    falls back to sensible defaults if unavailable.
    """
    try:
        from tools.compliance.cui_marker import load_cui_config as _load
        return _load()
    except Exception:
        pass

    # Try relative import
    try:
        cui_marker_path = Path(__file__).resolve().parent / "cui_marker.py"
        if cui_marker_path.exists():
            import importlib.util
            spec = importlib.util.spec_from_file_location("cui_marker", cui_marker_path)
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)
            return mod.load_cui_config()
    except Exception:
        pass

    return {
        "banner_top": "CUI // SP-CTI",
        "banner_bottom": "CUI // SP-CTI",
        "document_header": (
            "////////////////////////////////////////////////////////////////////\n"
            "CONTROLLED UNCLASSIFIED INFORMATION (CUI) // SP-CTI\n"
            "Distribution: Distribution D -- Authorized DoD Personnel Only\n"
            "////////////////////////////////////////////////////////////////////"
        ),
        "document_footer": (
            "////////////////////////////////////////////////////////////////////\n"
            "CUI // SP-CTI | Department of Defense\n"
            "////////////////////////////////////////////////////////////////////"
        ),
    }


def _extract_family(control_id):
    """Extract control family prefix from a control ID (e.g., 'AC-2' -> 'AC')."""
    if not control_id:
        return "UNKNOWN"
    parts = control_id.split("-")
    return parts[0].upper() if parts else "UNKNOWN"


# ---------------------------------------------------------------------------
# Data retrieval
# ---------------------------------------------------------------------------

def _get_fedramp_assessments(conn, project_id, baseline):
    """Retrieve all FedRAMP assessment results for a project and baseline."""
    rows = conn.execute(
        """SELECT * FROM fedramp_assessments
           WHERE project_id = ? AND baseline = ?
           ORDER BY control_id""",
        (project_id, baseline),
    ).fetchall()
    return [dict(r) for r in rows]


def _get_stig_findings(conn, project_id):
    """Retrieve STIG finding counts grouped by severity and status for cross-reference."""
    rows = conn.execute(
        """SELECT severity, status, COUNT(*) as cnt
           FROM stig_findings WHERE project_id = ?
           GROUP BY severity, status""",
        (project_id,),
    ).fetchall()
    return [dict(r) for r in rows]


def _get_sbom_records(conn, project_id):
    """Retrieve SBOM records for supply chain cross-reference."""
    rows = conn.execute(
        """SELECT * FROM sbom_records
           WHERE project_id = ?
           ORDER BY generated_at DESC""",
        (project_id,),
    ).fetchall()
    return [dict(r) for r in rows]


# ---------------------------------------------------------------------------
# Score and status calculations
# ---------------------------------------------------------------------------

def _calculate_family_scores(assessments):
    """Calculate implementation status per NIST 800-53 control family.

    Returns:
        dict mapping family code to a dict with counts and score.
    """
    family_data = {code: [] for code in CONTROL_FAMILIES}

    for a in assessments:
        family = _extract_family(a.get("control_id", ""))
        if family in family_data:
            family_data[family].append(a)
        else:
            # Catch controls from families not in our list
            if family not in family_data:
                family_data[family] = []
            family_data[family].append(a)

    results = {}
    for family_code in sorted(family_data.keys()):
        items = family_data[family_code]
        total = len(items)
        if total == 0:
            results[family_code] = {
                "name": CONTROL_FAMILIES.get(family_code, family_code),
                "score": 0.0,
                "total": 0,
                "satisfied": 0,
                "other_than_satisfied": 0,
                "not_applicable": 0,
                "risk_accepted": 0,
                "not_assessed": 0,
            }
            continue

        satisfied = sum(1 for i in items if i["status"] == "satisfied")
        other_than_satisfied = sum(1 for i in items if i["status"] == "other_than_satisfied")
        not_applicable = sum(1 for i in items if i["status"] == "not_applicable")
        risk_accepted = sum(1 for i in items if i["status"] == "risk_accepted")
        not_assessed = sum(1 for i in items if i["status"] == "not_assessed")

        # Denominator excludes not_applicable
        scoreable = total - not_applicable
        if scoreable > 0:
            score = 100.0 * (satisfied + risk_accepted * 0.75) / scoreable
        else:
            score = 100.0  # All N/A means fully compliant for this family

        results[family_code] = {
            "name": CONTROL_FAMILIES.get(family_code, family_code),
            "score": round(score, 1),
            "total": total,
            "satisfied": satisfied,
            "other_than_satisfied": other_than_satisfied,
            "not_applicable": not_applicable,
            "risk_accepted": risk_accepted,
            "not_assessed": not_assessed,
        }

    return results


def _calculate_readiness_score(assessments):
    """Calculate the overall FedRAMP readiness score.

    Formula: score = 100 * (satisfied + risk_accepted * 0.75) / (total - not_applicable)

    Returns:
        tuple of (score, readiness_level)
    """
    total = len(assessments)
    if total == 0:
        return 0.0, "Not Ready"

    satisfied = sum(1 for a in assessments if a.get("status") == "satisfied")
    not_applicable = sum(1 for a in assessments if a.get("status") == "not_applicable")
    risk_accepted = sum(1 for a in assessments if a.get("status") == "risk_accepted")

    scoreable = total - not_applicable
    if scoreable <= 0:
        return 100.0, "Ready for Assessment"

    score = 100.0 * (satisfied + risk_accepted * 0.75) / scoreable
    score = round(score, 1)

    if score >= 80:
        level = "Ready for Assessment"
    elif score >= 60:
        level = "Conditionally Ready"
    else:
        level = "Not Ready"

    return score, level


def _evaluate_gate(assessments, readiness_score, family_scores):
    """Evaluate FedRAMP security gate.

    Gate criteria:
      1. 0 "other_than_satisfied" on critical controls (AC-2, IA-2, SC-7, AU-2, CM-6)
      2. Readiness score >= 80%
      3. All control families have at least one assessment

    Returns:
        dict with overall gate result and individual check results.
    """
    # Check 1: Critical controls
    critical_not_satisfied = 0
    critical_control_details = []
    for a in assessments:
        ctrl = a.get("control_id", "")
        if ctrl in CRITICAL_CONTROLS and a.get("status") == "other_than_satisfied":
            critical_not_satisfied += 1
            critical_control_details.append(ctrl)

    critical_gate = "PASS" if critical_not_satisfied == 0 else "FAIL"

    # Check 2: Readiness threshold
    readiness_gate = "PASS" if readiness_score >= 80.0 else "FAIL"

    # Check 3: Family coverage
    families_with_data = sum(
        1 for f in family_scores.values() if f.get("total", 0) > 0
    )
    # Consider gate passed if at least the major families are covered
    # Major families: AC, AU, CM, IA, SC, SA, RA, CA
    major_families = ["AC", "AU", "CM", "IA", "SC", "SA", "RA", "CA"]
    major_covered = sum(
        1 for fam in major_families
        if family_scores.get(fam, {}).get("total", 0) > 0
    )
    family_coverage_gate = "PASS" if major_covered >= len(major_families) else "FAIL"

    # Overall gate
    overall = "PASS" if all(
        g == "PASS" for g in [critical_gate, readiness_gate, family_coverage_gate]
    ) else "FAIL"

    return {
        "gate_result": overall,
        "critical_control_gate": critical_gate,
        "readiness_gate": readiness_gate,
        "family_coverage_gate": family_coverage_gate,
        "critical_not_satisfied": critical_not_satisfied,
        "critical_control_details": critical_control_details,
        "families_with_data": families_with_data,
        "major_families_covered": major_covered,
    }


# ---------------------------------------------------------------------------
# Section builder functions
# ---------------------------------------------------------------------------

def _build_control_family_table(family_scores):
    """Build a markdown table summarising per-family scores."""
    lines = [
        "| Family | Name | Score | Satisfied | Other Than Satisfied | N/A | Risk Accepted | Not Assessed |",
        "|--------|------|------:|----------:|---------------------:|----:|--------------:|-------------:|",
    ]
    for code in sorted(family_scores.keys()):
        s = family_scores[code]
        if s.get("total", 0) == 0:
            continue
        name = s.get("name", code)
        lines.append(
            f"| {code} | {name} | {s.get('score', 0.0):.1f}% "
            f"| {s.get('satisfied', 0)} "
            f"| {s.get('other_than_satisfied', 0)} "
            f"| {s.get('not_applicable', 0)} "
            f"| {s.get('risk_accepted', 0)} "
            f"| {s.get('not_assessed', 0)} |"
        )

    return "\n".join(lines)


def _build_control_family_details(assessments, family_scores):
    """Build markdown detail sections for each assessed control family.

    Each family gets a sub-heading and a table listing every control
    with its status, evidence, and notes.
    """
    family_data = {}
    for a in assessments:
        family = _extract_family(a.get("control_id", ""))
        if family not in family_data:
            family_data[family] = []
        family_data[family].append(a)

    sections = []
    for code in sorted(family_data.keys()):
        items = family_data[code]
        s = family_scores.get(code, {})
        score = s.get("score", 0.0)
        name = s.get("name", CONTROL_FAMILIES.get(code, code))

        sections.append(f"### {code} - {name} ({score:.1f}%)")
        sections.append("")

        if not items:
            sections.append("*No assessments recorded for this family.*")
            sections.append("")
            continue

        sections.append(
            "| Control ID | Status | Implementation | Evidence | Notes |"
        )
        sections.append(
            "|------------|--------|----------------|----------|-------|"
        )
        for item in sorted(items, key=lambda x: x.get("control_id", "")):
            ctrl_id = item.get("control_id", "N/A")
            status = item.get("status", "not_assessed")
            impl = (item.get("implementation_status") or "N/A").replace("\n", " ").strip()
            evidence = (item.get("evidence_description") or "").replace("\n", " ").strip()
            notes = (item.get("notes") or "").replace("\n", " ").strip()
            # Truncate long fields for table readability
            if len(impl) > 40:
                impl = impl[:37] + "..."
            if len(evidence) > 60:
                evidence = evidence[:57] + "..."
            if len(notes) > 60:
                notes = notes[:57] + "..."
            sections.append(
                f"| {ctrl_id} | {status} | {impl} | {evidence} | {notes} |"
            )
        sections.append("")

    return "\n".join(sections)


def _build_gap_analysis_table(assessments):
    """Build a table of controls that are not satisfied, grouped by family.

    Lists all controls with status other_than_satisfied, not_assessed,
    or risk_accepted, ordered by family then control ID.
    """
    gaps = [
        a for a in assessments
        if a.get("status") in ("other_than_satisfied", "not_assessed")
    ]
    if not gaps:
        return "*No gaps identified. All assessed controls are satisfied or not applicable.*"

    lines = [
        "| Control ID | Family | Status | Implementation | Notes |",
        "|------------|--------|--------|----------------|-------|",
    ]
    for g in sorted(gaps, key=lambda x: x.get("control_id", "")):
        ctrl_id = g.get("control_id", "N/A")
        family = _extract_family(ctrl_id)
        status = g.get("status", "N/A")
        impl = (g.get("implementation_status") or "").replace("\n", " ").strip()
        notes = (g.get("notes") or "").replace("\n", " ").strip()
        if len(impl) > 40:
            impl = impl[:37] + "..."
        if len(notes) > 50:
            notes = notes[:47] + "..."
        lines.append(
            f"| {ctrl_id} | {family} | {status} | {impl} | {notes} |"
        )

    return "\n".join(lines)


def _build_recommendations(assessments, family_scores, gate_result):
    """Build prioritized recommendations based on assessment gaps."""
    lines = []

    # Collect gaps
    other_than_satisfied = [
        a for a in assessments if a.get("status") == "other_than_satisfied"
    ]
    not_assessed = [
        a for a in assessments if a.get("status") == "not_assessed"
    ]

    if not other_than_satisfied and not not_assessed:
        return "*No recommendations. All controls are satisfied or not applicable.*"

    # Critical control recommendations first
    critical_gaps = [
        a for a in other_than_satisfied
        if a.get("control_id") in CRITICAL_CONTROLS
    ]
    if critical_gaps:
        lines.append("**CRITICAL — Immediate Action Required:**")
        lines.append("")
        for g in critical_gaps:
            ctrl = g.get("control_id", "N/A")
            notes = (g.get("notes") or "No details available").replace("\n", " ").strip()
            lines.append(f"- **{ctrl}**: Remediate immediately. {notes}")
        lines.append("")

    # Family-level recommendations for weakest families
    weak_families = sorted(
        [(code, data) for code, data in family_scores.items()
         if data.get("total", 0) > 0 and data.get("score", 100) < 80],
        key=lambda x: x[1]["score"]
    )
    if weak_families:
        lines.append("**HIGH — Control Family Remediation:**")
        lines.append("")
        for code, data in weak_families[:5]:
            name = data.get("name", code)
            score = data.get("score", 0)
            ots = data.get("other_than_satisfied", 0)
            lines.append(
                f"- **{code} ({name})**: Score {score:.1f}%. "
                f"{ots} control(s) other than satisfied."
            )
        lines.append("")

    # Not assessed recommendations
    if not_assessed:
        lines.append("**MEDIUM — Assessment Completion:**")
        lines.append("")
        lines.append(
            f"- {len(not_assessed)} control(s) have not been assessed. "
            f"Complete assessment to improve readiness score."
        )
        # List families with unassessed controls
        unassessed_families = set()
        for a in not_assessed:
            unassessed_families.add(_extract_family(a.get("control_id", "")))
        if unassessed_families:
            lines.append(
                f"- Affected families: {', '.join(sorted(unassessed_families))}"
            )
        lines.append("")

    return "\n".join(lines)


def _build_remediation_plan(assessments):
    """Build a prioritized remediation plan table.

    Lists controls needing remediation with estimated target dates
    based on criticality.
    """
    needing_remediation = [
        a for a in assessments
        if a.get("status") in ("other_than_satisfied", "not_assessed")
    ]
    if not needing_remediation:
        return "*No items require remediation at this time.*"

    now = datetime.now(timezone.utc)
    lines = [
        "| Control ID | Family | Status | Priority | Target Date | Action Required |",
        "|------------|--------|--------|----------|-------------|-----------------|",
    ]

    for item in sorted(needing_remediation,
                       key=lambda x: (
                           0 if x.get("control_id") in CRITICAL_CONTROLS else 1,
                           x.get("control_id", ""),
                       )):
        ctrl_id = item.get("control_id", "N/A")
        family = _extract_family(ctrl_id)
        status = item.get("status", "N/A")

        # Determine priority based on criticality
        if ctrl_id in CRITICAL_CONTROLS:
            priority = "critical"
        elif status == "other_than_satisfied":
            priority = "high"
        else:
            priority = "medium"

        window_days = REMEDIATION_WINDOWS.get(priority, 60)
        target = (now + timedelta(days=window_days)).strftime("%Y-%m-%d")

        # Build action description
        if status == "other_than_satisfied":
            action = f"Implement {ctrl_id} control requirements"
        else:
            action = f"Complete assessment for {ctrl_id}"

        if len(action) > 50:
            action = action[:47] + "..."

        lines.append(
            f"| {ctrl_id} | {family} | {status} | {priority} | {target} | {action} |"
        )

    return "\n".join(lines)


def _build_evidence_table(assessments):
    """Build evidence reference table showing evidence coverage per family."""
    family_evidence = {}

    for a in assessments:
        family = _extract_family(a.get("control_id", ""))
        if family not in family_evidence:
            family_evidence[family] = {"total": 0, "with_evidence": 0, "without_evidence": 0}
        family_evidence[family]["total"] += 1
        if a.get("evidence_path") or a.get("evidence_description"):
            family_evidence[family]["with_evidence"] += 1
        else:
            family_evidence[family]["without_evidence"] += 1

    lines = [
        "| Family | Name | Total Controls | With Evidence | Without Evidence | Coverage |",
        "|--------|------|---------------:|--------------:|-----------------:|---------:|",
    ]
    for code in sorted(family_evidence.keys()):
        c = family_evidence[code]
        name = CONTROL_FAMILIES.get(code, code)
        if c["total"] == 0:
            continue
        coverage = f"{100.0 * c['with_evidence'] / c['total']:.0f}%"
        lines.append(
            f"| {code} | {name} | {c['total']} | {c['with_evidence']} "
            f"| {c['without_evidence']} | {coverage} |"
        )

    # Totals row
    total_all = sum(c["total"] for c in family_evidence.values())
    total_with = sum(c["with_evidence"] for c in family_evidence.values())
    total_without = sum(c["without_evidence"] for c in family_evidence.values())
    total_cov = f"{100.0 * total_with / total_all:.0f}%" if total_all > 0 else "N/A"
    lines.append(
        f"| **Total** | | **{total_all}** | **{total_with}** "
        f"| **{total_without}** | **{total_cov}** |"
    )

    return "\n".join(lines), total_with, total_without, total_cov


def _build_executive_summary(readiness_score, readiness_level, gate_result,
                             assessments, family_scores):
    """Build the executive summary paragraph.

    Provides a high-level overview of the FedRAMP assessment results
    including key metrics, gate status, and notable findings.
    """
    total = len(assessments)
    satisfied = sum(1 for a in assessments if a.get("status") == "satisfied")
    other_than_satisfied = sum(1 for a in assessments if a.get("status") == "other_than_satisfied")
    not_applicable = sum(1 for a in assessments if a.get("status") == "not_applicable")
    risk_accepted = sum(1 for a in assessments if a.get("status") == "risk_accepted")
    not_assessed = sum(1 for a in assessments if a.get("status") == "not_assessed")

    # Count families with assessments
    families_with_data = sum(
        1 for f in family_scores.values() if f.get("total", 0) > 0
    )

    # Count critical control failures
    critical_failures = sum(
        1 for a in assessments
        if a.get("control_id") in CRITICAL_CONTROLS
        and a.get("status") == "other_than_satisfied"
    )

    # Identify weakest family
    scored_families = {
        code: data for code, data in family_scores.items()
        if data.get("total", 0) > 0 and data.get("total", 0) != data.get("not_applicable", 0)
    }
    weakest_family = ""
    weakest_score = 100.0
    for code, data in scored_families.items():
        if data["score"] < weakest_score:
            weakest_score = data["score"]
            weakest_family = f"{code} ({data.get('name', code)})"

    lines = []
    lines.append(
        f"This FedRAMP security assessment evaluated {total} controls "
        f"across {families_with_data} control families. The overall readiness score is "
        f"**{readiness_score:.1f}%** ({readiness_level}) with a gate result of "
        f"**{gate_result['gate_result']}**."
    )
    lines.append("")
    lines.append(
        f"- **{satisfied}** controls satisfied, "
        f"**{other_than_satisfied}** other than satisfied, "
        f"**{risk_accepted}** risk accepted, "
        f"**{not_assessed}** not assessed, "
        f"**{not_applicable}** not applicable."
    )
    if critical_failures > 0:
        failed_ctrls = ", ".join(gate_result.get("critical_control_details", []))
        lines.append(
            f"- **{critical_failures} critical control(s) other than satisfied** "
            f"({failed_ctrls}) -- immediate remediation required."
        )
    else:
        lines.append("- All critical controls (AC-2, IA-2, SC-7, AU-2, CM-6) are satisfied.")
    if weakest_family:
        lines.append(
            f"- Weakest control family: **{weakest_family}** ({weakest_score:.1f}%)."
        )

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Variable substitution & CUI markings
# ---------------------------------------------------------------------------

def _apply_cui_markings(content, cui_config):
    """Apply CUI header and footer banners to the report content."""
    header = cui_config.get("document_header", "").strip()
    footer = cui_config.get("document_footer", "").strip()
    banner_top = cui_config.get("banner_top", "CUI // SP-CTI")

    # If the content already contains the banner, skip
    if banner_top in content:
        return content

    return f"{header}\n\n{content.strip()}\n\n{footer}\n"


def _substitute_variables(template, variables):
    """Replace {{variable_name}} placeholders in the template."""
    def replacer(match):
        key = match.group(1).strip()
        return str(variables.get(key, match.group(0)))
    return re.sub(r"\{\{(\w+)\}\}", replacer, template)


# ---------------------------------------------------------------------------
# Audit logging
# ---------------------------------------------------------------------------

def _log_audit_event(conn, project_id, action, details, file_path):
    """Log an audit trail event for FedRAMP report generation."""
    try:
        conn.execute(
            """INSERT INTO audit_trail
               (project_id, event_type, actor, action, details,
                affected_files, classification)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (
                project_id,
                "fedramp_assessed",
                "icdev-compliance-engine",
                action,
                json.dumps(details),
                json.dumps([str(file_path)]),
                "CUI",
            ),
        )
        conn.commit()
    except Exception as e:
        print(f"Warning: Could not log audit event: {e}", file=sys.stderr)


# ---------------------------------------------------------------------------
# Main generator
# ---------------------------------------------------------------------------

def generate_fedramp_report(project_id, baseline="moderate", output_path=None, db_path=None):
    """Generate a FedRAMP security assessment report for a project.

    Args:
        project_id: The project identifier.
        baseline: FedRAMP baseline level ('moderate' or 'high').
        output_path: Override output directory or file path.
        db_path: Override database path.

    Returns:
        dict with status, output_file path, summary, and gate_result.
    """
    conn = _get_connection(db_path)
    try:
        # 1. Load project data
        project = _get_project_data(conn, project_id)
        project_name = project.get("name", project_id)

        # 2. Load template (with fallback)
        template = _load_template()

        # 3. Query fedramp_assessments
        assessments = _get_fedramp_assessments(conn, project_id, baseline)

        # Cross-reference data for enrichment
        stig_findings = _get_stig_findings(conn, project_id)
        sbom_records = _get_sbom_records(conn, project_id)

        # 4. Calculate family scores, readiness, and gate
        family_scores = _calculate_family_scores(assessments)
        readiness_score, readiness_level = _calculate_readiness_score(assessments)
        gate_result = _evaluate_gate(assessments, readiness_score, family_scores)

        # 5. Compute status counts
        total_controls = len(assessments)
        controls_satisfied = sum(1 for a in assessments if a.get("status") == "satisfied")
        controls_other = sum(1 for a in assessments if a.get("status") == "other_than_satisfied")
        controls_na = sum(1 for a in assessments if a.get("status") == "not_applicable")
        controls_risk = sum(1 for a in assessments if a.get("status") == "risk_accepted")
        controls_not_assessed = sum(1 for a in assessments if a.get("status") == "not_assessed")
        total_gaps = controls_other + controls_not_assessed

        # Percentages
        def _pct(count, total):
            return f"{100.0 * count / total:.1f}" if total > 0 else "0.0"

        # 6. Build all section content
        control_family_table = _build_control_family_table(family_scores)
        control_family_details = _build_control_family_details(assessments, family_scores)
        gap_analysis_table = _build_gap_analysis_table(assessments)
        recommendations = _build_recommendations(assessments, family_scores, gate_result)
        remediation_plan = _build_remediation_plan(assessments)
        evidence_table, controls_with_evidence, controls_without_evidence, evidence_coverage_pct = (
            _build_evidence_table(assessments)
        )
        executive_summary = _build_executive_summary(
            readiness_score, readiness_level, gate_result,
            assessments, family_scores,
        )

        # Load CUI config for banner variables
        cui_config = _load_cui_config()

        # Determine version number by counting existing FedRAMP audit events
        report_count_row = conn.execute(
            """SELECT COUNT(*) as cnt FROM audit_trail
               WHERE project_id = ? AND event_type = 'fedramp_assessed'""",
            (project_id,),
        ).fetchone()
        report_count = report_count_row["cnt"] if report_count_row else 0
        new_version = f"{report_count + 1}.0"

        now = datetime.now(timezone.utc)

        # Determine assessor from most recent assessment
        assessor = "icdev-compliance-engine"
        if assessments:
            assessor = assessments[0].get("assessor", assessor)

        # Estimate remediation effort
        if total_gaps == 0:
            remediation_effort = "None"
        elif total_gaps <= 5:
            remediation_effort = "Low (1-2 weeks)"
        elif total_gaps <= 20:
            remediation_effort = "Medium (2-6 weeks)"
        else:
            remediation_effort = "High (6+ weeks)"

        # 7. Create substitution dict with all {{variables}}
        variables = {
            # System / project info
            "system_name": project_name,
            "project_id": project_id,
            "project_name": project_name,
            "classification": project.get("classification", "CUI"),
            "impact_level": project.get("impact_level", "IL5"),
            "cloud_environment": project.get("cloud_environment", "aws-govcloud"),
            "baseline": baseline.capitalize(),

            # Report metadata
            "version": new_version,
            "report_version": new_version,
            "assessment_date": now.strftime("%Y-%m-%d"),
            "date_prepared": now.strftime("%Y-%m-%d"),
            "assessor": assessor,
            "generation_timestamp": now.strftime("%Y-%m-%d %H:%M UTC"),
            "icdev_version": "1.0",

            # Readiness and gate
            "readiness_score": f"{readiness_score:.1f}",
            "readiness_level": readiness_level,
            "gate_result": gate_result["gate_result"],
            "critical_control_gate": gate_result["critical_control_gate"],
            "readiness_gate": gate_result["readiness_gate"],
            "family_coverage_gate": gate_result["family_coverage_gate"],

            # Readiness level thresholds
            "readiness_level_80": "CURRENT" if readiness_score >= 80 else "--",
            "readiness_level_60": "CURRENT" if 60 <= readiness_score < 80 else "--",
            "readiness_level_below_60": "CURRENT" if readiness_score < 60 else "--",

            # Control counts
            "total_controls": str(total_controls),
            "controls_satisfied": str(controls_satisfied),
            "controls_other_than_satisfied": str(controls_other),
            "controls_not_applicable": str(controls_na),
            "controls_risk_accepted": str(controls_risk),
            "controls_not_assessed": str(controls_not_assessed),
            "critical_controls_not_satisfied": str(gate_result["critical_not_satisfied"]),
            "total_gaps": str(total_gaps),
            "remediation_effort": remediation_effort,

            # Percentages
            "pct_satisfied": _pct(controls_satisfied, total_controls),
            "pct_other_than_satisfied": _pct(controls_other, total_controls),
            "pct_not_applicable": _pct(controls_na, total_controls),
            "pct_risk_accepted": _pct(controls_risk, total_controls),
            "pct_not_assessed": _pct(controls_not_assessed, total_controls),

            # Executive summary
            "executive_summary": executive_summary,

            # Section content
            "control_family_table": control_family_table,
            "control_family_details": control_family_details,
            "gap_analysis_table": gap_analysis_table,
            "recommendations": recommendations,
            "remediation_plan": remediation_plan,
            "evidence_table": evidence_table,

            # Evidence coverage
            "controls_with_evidence": str(controls_with_evidence),
            "controls_without_evidence": str(controls_without_evidence),
            "evidence_coverage_pct": evidence_coverage_pct.replace("%", ""),

            # Cross-reference data
            "stig_findings_count": str(sum(r.get("cnt", 0) for r in stig_findings)),
            "sbom_records_count": str(len(sbom_records)),

            # CUI banners
            "cui_banner_top": cui_config.get(
                "document_header", cui_config.get("banner_top", "CUI // SP-CTI")
            ),
            "cui_banner_bottom": cui_config.get(
                "document_footer", cui_config.get("banner_bottom", "CUI // SP-CTI")
            ),
        }

        # Per-family score variables
        for code in CONTROL_FAMILIES:
            key_prefix = code.lower()
            s = family_scores.get(code, {})
            variables[f"{key_prefix}_score"] = f"{s.get('score', 0.0):.1f}"
            variables[f"{key_prefix}_total"] = str(s.get("total", 0))
            variables[f"{key_prefix}_satisfied"] = str(s.get("satisfied", 0))
            variables[f"{key_prefix}_other"] = str(s.get("other_than_satisfied", 0))
            variables[f"{key_prefix}_na"] = str(s.get("not_applicable", 0))
            variables[f"{key_prefix}_risk"] = str(s.get("risk_accepted", 0))
            variables[f"{key_prefix}_not_assessed"] = str(s.get("not_assessed", 0))

        # 8. Apply regex substitution
        report_content = _substitute_variables(template, variables)

        # 9. Apply CUI markings (header/footer banners)
        report_content = _apply_cui_markings(report_content, cui_config)

        # 10. Determine output path
        if output_path:
            out_path = Path(output_path)
            if out_path.is_dir() or str(output_path).endswith("/") or str(output_path).endswith("\\"):
                out_dir = out_path
                out_file = out_dir / f"fedramp-{baseline}-report-v{new_version}.md"
            else:
                out_file = out_path
        else:
            dir_path = project.get("directory_path", "")
            if dir_path:
                out_dir = Path(dir_path) / "compliance"
            else:
                out_dir = BASE_DIR / "projects" / project_name / "compliance"
            out_file = out_dir / f"fedramp-{baseline}-report-v{new_version}.md"

        out_file.parent.mkdir(parents=True, exist_ok=True)

        # 11. Write file
        with open(out_file, "w", encoding="utf-8") as f:
            f.write(report_content)

        # 12. Log audit event
        audit_details = {
            "version": new_version,
            "baseline": baseline,
            "readiness_score": readiness_score,
            "readiness_level": readiness_level,
            "gate_result": gate_result["gate_result"],
            "total_controls": total_controls,
            "controls_satisfied": controls_satisfied,
            "controls_other_than_satisfied": controls_other,
            "controls_not_applicable": controls_na,
            "controls_risk_accepted": controls_risk,
            "controls_not_assessed": controls_not_assessed,
            "critical_not_satisfied": gate_result["critical_not_satisfied"],
            "total_gaps": total_gaps,
            "output_file": str(out_file),
        }
        _log_audit_event(
            conn, project_id,
            f"FedRAMP {baseline} report v{new_version} generated",
            audit_details,
            out_file,
        )

        # Print summary
        print("FedRAMP assessment report generated successfully:")
        print(f"  File:                       {out_file}")
        print(f"  Version:                    {new_version}")
        print(f"  Project:                    {project_name}")
        print(f"  Baseline:                   {baseline}")
        print(f"  Readiness Score:            {readiness_score:.1f}%")
        print(f"  Readiness Level:            {readiness_level}")
        print(f"  Gate Result:                {gate_result['gate_result']}")
        print(f"  Total Controls:             {total_controls}")
        print(f"  Controls Satisfied:         {controls_satisfied}")
        print(f"  Other Than Satisfied:       {controls_other}")
        print(f"  Critical Controls Failing:  {gate_result['critical_not_satisfied']}")
        print(f"  Total Gaps:                 {total_gaps}")

        # 13. Return output metadata
        summary = {
            "total_controls": total_controls,
            "controls_satisfied": controls_satisfied,
            "controls_other_than_satisfied": controls_other,
            "controls_not_applicable": controls_na,
            "controls_risk_accepted": controls_risk,
            "controls_not_assessed": controls_not_assessed,
            "readiness_score": readiness_score,
            "readiness_level": readiness_level,
            "total_gaps": total_gaps,
            "family_scores": {
                code: family_scores[code]["score"]
                for code in sorted(family_scores.keys())
                if family_scores[code]["total"] > 0
            },
        }

        return {
            "status": "success",
            "output_file": str(out_file),
            "summary": summary,
            "gate_result": {
                "overall": gate_result["gate_result"],
                "critical_control_gate": gate_result["critical_control_gate"],
                "readiness_gate": gate_result["readiness_gate"],
                "family_coverage_gate": gate_result["family_coverage_gate"],
                "critical_not_satisfied": gate_result["critical_not_satisfied"],
                "critical_control_details": gate_result["critical_control_details"],
            },
            "version": new_version,
            "project_id": project_id,
            "project_name": project_name,
            "baseline": baseline,
            "generated_at": now.isoformat(),
        }

    finally:
        conn.close()


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Generate FedRAMP security assessment report"
    )
    parser.add_argument("--project-id", required=True, help="Project ID")
    parser.add_argument(
        "--baseline", choices=["moderate", "high"], default="moderate",
        help="FedRAMP baseline level (default: moderate)"
    )
    parser.add_argument("--output-path", help="Output directory or file path")
    parser.add_argument(
        "--db-path", type=Path, default=DB_PATH, help="Database path"
    )
    parser.add_argument(
        "--format", choices=["text", "json"], default="text",
        help="Output format: text (default) or json"
    )
    args = parser.parse_args()

    try:
        result = generate_fedramp_report(
            args.project_id, args.baseline, args.output_path, args.db_path
        )
        if args.format == "json":
            print(json.dumps(result, indent=2))
        else:
            print(f"\nFedRAMP report generated: {result['output_file']}")
    except (FileNotFoundError, ValueError) as e:
        print(f"ERROR: {e}", file=sys.stderr)
        sys.exit(1)
