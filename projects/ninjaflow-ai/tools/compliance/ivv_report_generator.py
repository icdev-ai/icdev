#!/usr/bin/env python3
# CUI // SP-CTI
"""IV&V certification report generator per IEEE 1012.

Loads ivv_report_template.md, queries ivv_assessments, ivv_findings, and
ivv_certifications tables, generates a comprehensive IV&V certification report
with verification/validation scores and certification recommendation."""

import argparse
import json
import re
import sqlite3
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent.parent
DB_PATH = BASE_DIR / "data" / "icdev.db"
IVV_TEMPLATE_PATH = BASE_DIR / "context" / "compliance" / "ivv_report_template.md"
IVV_REQUIREMENTS_PATH = BASE_DIR / "context" / "compliance" / "ivv_requirements.json"

# Process areas per IEEE 1012 as defined in ivv_requirements.json
PROCESS_AREAS = [
    "Requirements Verification",
    "Design Verification",
    "Code Verification",
    "Test Verification",
    "Integration Verification",
    "Traceability Analysis",
    "Security Verification",
    "Build/Deploy Verification",
    "Process Compliance",
]

# Which process areas contribute to the Verification score
VERIFICATION_AREAS = [
    "Requirements Verification",
    "Design Verification",
    "Code Verification",
    "Traceability Analysis",
    "Security Verification",
    "Build/Deploy Verification",
    "Process Compliance",
]

# Which process areas contribute to the Validation score
VALIDATION_AREAS = [
    "Test Verification",
    "Integration Verification",
]

# Status weighting for score calculation
IVV_STATUS_WEIGHTS = {
    "pass": 1.0,
    "partial": 0.5,
    "fail": 0.0,
    "deferred": 0.0,
    "not_assessed": 0.0,
}

# Severity ordering for consistent output
SEVERITY_ORDER = ["critical", "high", "moderate", "low"]

# Finding statuses for summary
FINDING_STATUSES = ["open", "in_progress", "resolved", "accepted_risk", "deferred"]


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
    """Load the IV&V report template markdown.

    If the template file does not exist a minimal built-in template is
    returned so the generator can still produce a useful report.
    """
    path = template_path or IVV_TEMPLATE_PATH
    if path.exists():
        with open(path, "r", encoding="utf-8") as f:
            return f.read()

    # Fallback minimal template when file is missing
    return _builtin_template()


def _builtin_template():
    """Return a minimal built-in IV&V report template."""
    return (
        "{{cui_banner_top}}\n\n"
        "# Independent Verification & Validation (IV&V) Certification Report\n\n"
        "**Project:** {{project_name}}  \n"
        "**Project ID:** {{project_id}}  \n"
        "**Classification:** {{classification}}  \n"
        "**Assessment Date:** {{assessment_date}}  \n"
        "**Report Version:** {{version}}  \n"
        "**IV&V Authority:** {{ivv_authority}}  \n"
        "**Framework:** IEEE 1012-2016, DoDI 5000.87  \n\n"
        "---\n\n"
        "## 1. Executive Summary\n\n"
        "**Verification Score:** {{verification_score}}%  \n"
        "**Validation Score:** {{validation_score}}%  \n"
        "**Overall IV&V Score:** {{overall_score}}%  \n"
        "**Gate Result:** {{gate_result}}  \n"
        "**Certification Recommendation:** {{certification_recommendation}}  \n\n"
        "{{executive_summary}}\n\n"
        "---\n\n"
        "## 2. Independence Declaration\n\n"
        "{{independence_declaration}}\n\n"
        "---\n\n"
        "## 3. Verification Results\n\n"
        "### 3.1 Process Area Scores\n\n"
        "{{process_area_scores_table}}\n\n"
        "### 3.2 Process Area Details\n\n"
        "{{process_area_details}}\n\n"
        "---\n\n"
        "## 4. Validation Results\n\n"
        "### 4.1 Test Verification Results\n\n"
        "{{test_verification_results}}\n\n"
        "### 4.2 Integration Verification Results\n\n"
        "{{integration_verification_results}}\n\n"
        "---\n\n"
        "## 5. Requirements Traceability Matrix Summary\n\n"
        "{{rtm_summary}}\n\n"
        "**RTM Coverage:** {{rtm_coverage}}%  \n"
        "**Requirements with Full Trace:** {{rtm_full_trace_count}}  \n"
        "**Requirements with Gaps:** {{rtm_gap_count}}  \n"
        "**Orphan Tests:** {{rtm_orphan_tests}}  \n\n"
        "---\n\n"
        "## 6. IV&V Findings\n\n"
        "### 6.1 Critical Findings\n\n"
        "{{critical_findings}}\n\n"
        "### 6.2 High Findings\n\n"
        "{{high_findings}}\n\n"
        "### 6.3 Moderate Findings\n\n"
        "{{moderate_findings}}\n\n"
        "### 6.4 Low Findings\n\n"
        "{{low_findings}}\n\n"
        "### 6.5 Findings Summary\n\n"
        "| Severity | Open | Resolved | Accepted Risk | Deferred | Total |\n"
        "|----------|------|----------|---------------|----------|-------|\n"
        "{{findings_summary_table}}\n\n"
        "---\n\n"
        "## 7. Certification Recommendation\n\n"
        "**Recommendation:** {{certification_recommendation}}  \n\n"
        "### Criteria Applied:\n"
        "- **CERTIFY:** Overall score >= 80%, 0 critical findings, all process areas >= 60%\n"
        "- **CONDITIONAL:** Overall score >= 60%, 0 critical findings, conditions listed\n"
        "- **DENY:** Overall score < 60% OR critical findings unresolved\n\n"
        "### Conditions (if applicable):\n\n"
        "{{conditions}}\n\n"
        "---\n\n"
        "## 8. Evidence Index\n\n"
        "{{evidence_index}}\n\n"
        "---\n\n"
        "## 9. Assessment Methodology\n\n"
        "This assessment was conducted using the ICDEV IV&V Assessor tool against "
        "the IEEE 1012 requirements catalog (30 requirements across 9 process areas).\n\n"
        "**Scoring Formula:**\n"
        "- Verification Score = average of process area pass rates\n"
        "- Validation Score = average of Test + Integration area pass rates\n"
        "- Overall Score = 0.6 x Verification + 0.4 x Validation\n\n"
        "**Gate Logic:** PASS if 0 critical findings remain open\n\n"
        "---\n\n"
        "**Prepared by:** {{ivv_authority}}  \n"
        "**Date:** {{assessment_date}}  \n"
        "**Next Review:** {{next_review_date}}  \n\n"
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

    # Try relative import via file location
    try:
        cui_marker_path = Path(__file__).resolve().parent / "cui_marker.py"
        if cui_marker_path.exists():
            import importlib.util
            spec = importlib.util.spec_from_file_location(
                "cui_marker", cui_marker_path
            )
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


def _load_ivv_requirements():
    """Load IV&V requirements catalog from JSON.

    Returns the full catalog dict with 'metadata' and 'requirements' keys.
    Falls back to an empty catalog if the file is missing.
    """
    if not IVV_REQUIREMENTS_PATH.exists():
        return {"metadata": {}, "requirements": []}

    with open(IVV_REQUIREMENTS_PATH, "r", encoding="utf-8") as f:
        data = json.load(f)
    return data


# ---------------------------------------------------------------------------
# Data retrieval
# ---------------------------------------------------------------------------

def _get_ivv_assessments(conn, project_id):
    """Retrieve all IV&V assessment results for a project."""
    rows = conn.execute(
        """SELECT * FROM ivv_assessments
           WHERE project_id = ?
           ORDER BY process_area, requirement_id""",
        (project_id,),
    ).fetchall()
    return [dict(r) for r in rows]


def _get_ivv_findings(conn, project_id):
    """Retrieve all IV&V findings for a project."""
    rows = conn.execute(
        """SELECT * FROM ivv_findings
           WHERE project_id = ?
           ORDER BY severity, finding_id""",
        (project_id,),
    ).fetchall()
    return [dict(r) for r in rows]


def _get_ivv_certification(conn, project_id):
    """Retrieve IV&V certification status for a project."""
    row = conn.execute(
        "SELECT * FROM ivv_certifications WHERE project_id = ?",
        (project_id,),
    ).fetchone()
    return dict(row) if row else {}


# ---------------------------------------------------------------------------
# Score calculation
# ---------------------------------------------------------------------------

def _calculate_process_area_scores(assessments):
    """Calculate a pass-rate score for each IV&V process area.

    Score formula per area:
        score = 100 * (pass_count + partial_count * 0.5) / total_scoreable

    ``not_applicable`` assessments are excluded from the denominator.

    Returns:
        dict mapping process area name to a dict with ``score``, per-status
        counts, and ``total`` / ``scoreable`` tallies.
    """
    area_data = {area: [] for area in PROCESS_AREAS}
    for a in assessments:
        pa = a.get("process_area")
        if pa in area_data:
            area_data[pa].append(a)

    results = {}
    for area in PROCESS_AREAS:
        items = area_data[area]
        total = len(items)

        if total == 0:
            results[area] = {
                "score": 0.0,
                "total": 0,
                "scoreable": 0,
                "pass": 0,
                "partial": 0,
                "fail": 0,
                "deferred": 0,
                "not_assessed": 0,
                "not_applicable": 0,
            }
            continue

        pass_count = sum(
            1 for i in items if i.get("status") == "pass"
        )
        partial_count = sum(
            1 for i in items if i.get("status") == "partial"
        )
        fail_count = sum(
            1 for i in items if i.get("status") == "fail"
        )
        deferred_count = sum(
            1 for i in items if i.get("status") == "deferred"
        )
        not_assessed_count = sum(
            1 for i in items if i.get("status") == "not_assessed"
        )
        not_applicable_count = sum(
            1 for i in items if i.get("status") == "not_applicable"
        )

        # Denominator excludes not_applicable
        scoreable = total - not_applicable_count
        if scoreable > 0:
            score = 100.0 * (
                pass_count * IVV_STATUS_WEIGHTS["pass"]
                + partial_count * IVV_STATUS_WEIGHTS["partial"]
            ) / scoreable
        else:
            # All items are N/A — treat as fully compliant
            score = 100.0

        results[area] = {
            "score": round(score, 1),
            "total": total,
            "scoreable": scoreable,
            "pass": pass_count,
            "partial": partial_count,
            "fail": fail_count,
            "deferred": deferred_count,
            "not_assessed": not_assessed_count,
            "not_applicable": not_applicable_count,
        }

    return results


def _calculate_verification_score(area_scores):
    """Calculate the aggregate Verification score.

    Average of the scores for the 7 verification process areas.  Areas with
    zero scoreable items are excluded from the average.
    """
    scores = []
    for area in VERIFICATION_AREAS:
        info = area_scores.get(area, {})
        if info.get("scoreable", 0) > 0 or info.get("total", 0) > 0:
            scores.append(info.get("score", 0.0))

    if not scores:
        return 0.0
    return round(sum(scores) / len(scores), 1)


def _calculate_validation_score(area_scores):
    """Calculate the aggregate Validation score.

    Average of the scores for Test Verification and Integration Verification.
    Areas with zero scoreable items are excluded from the average.
    """
    scores = []
    for area in VALIDATION_AREAS:
        info = area_scores.get(area, {})
        if info.get("scoreable", 0) > 0 or info.get("total", 0) > 0:
            scores.append(info.get("score", 0.0))

    if not scores:
        return 0.0
    return round(sum(scores) / len(scores), 1)


def _calculate_overall_score(verification_score, validation_score):
    """Calculate the weighted overall IV&V score.

    Overall = 0.6 * Verification + 0.4 * Validation
    """
    overall = 0.6 * verification_score + 0.4 * validation_score
    return round(overall, 1)


def _determine_certification_recommendation(overall_score, area_scores, findings):
    """Determine the IV&V certification recommendation.

    Rules:
        CERTIFY:     overall >= 80, 0 critical open findings, all areas >= 60
        CONDITIONAL: overall >= 60, 0 critical open findings
        DENY:        overall < 60 OR any critical findings are open

    Returns:
        tuple of (recommendation_str, reason_str)
    """
    # Count critical open findings
    critical_open = sum(
        1 for f in findings
        if f.get("severity") == "critical"
        and f.get("status") in ("open", "in_progress")
    )

    # Check if all areas meet the 60% minimum
    all_areas_above_60 = True
    areas_below_60 = []
    for area in PROCESS_AREAS:
        info = area_scores.get(area, {})
        # Only evaluate areas that have assessments
        if info.get("total", 0) > 0 and info.get("score", 0.0) < 60.0:
            all_areas_above_60 = False
            areas_below_60.append(area)

    # Decision logic
    if critical_open > 0:
        reason = (
            f"DENY: {critical_open} critical finding(s) remain open. "
            "All critical findings must be resolved before certification."
        )
        return "DENY", reason

    if overall_score < 60.0:
        reason = (
            f"DENY: Overall score ({overall_score:.1f}%) is below the 60% "
            "minimum threshold required for certification."
        )
        return "DENY", reason

    if overall_score >= 80.0 and all_areas_above_60:
        reason = (
            f"CERTIFY: Overall score ({overall_score:.1f}%) meets the 80% "
            "threshold, zero critical open findings, and all process areas "
            "meet the 60% minimum."
        )
        return "CERTIFY", reason

    # Conditional case: overall >= 60 but either < 80 or some areas below 60
    condition_parts = []
    if overall_score < 80.0:
        condition_parts.append(
            f"Overall score ({overall_score:.1f}%) is below the 80% full "
            "certification threshold"
        )
    if not all_areas_above_60:
        area_list = ", ".join(areas_below_60)
        condition_parts.append(
            f"The following process areas are below 60%: {area_list}"
        )

    reason = "CONDITIONAL: " + "; ".join(condition_parts) + "."
    return "CONDITIONAL", reason


# ---------------------------------------------------------------------------
# Section builders
# ---------------------------------------------------------------------------

def _build_process_area_scores_table(area_scores):
    """Build a markdown table summarizing per-area IV&V scores."""
    lines = [
        "| Process Area | Score | Total | Pass | Partial | Fail | Deferred | N/A | Not Assessed |",
        "|--------------|------:|------:|-----:|--------:|-----:|---------:|----:|-------------:|",
    ]
    for area in PROCESS_AREAS:
        s = area_scores.get(area, {})
        lines.append(
            f"| {area} "
            f"| {s.get('score', 0.0):.1f}% "
            f"| {s.get('total', 0)} "
            f"| {s.get('pass', 0)} "
            f"| {s.get('partial', 0)} "
            f"| {s.get('fail', 0)} "
            f"| {s.get('deferred', 0)} "
            f"| {s.get('not_applicable', 0)} "
            f"| {s.get('not_assessed', 0)} |"
        )

    # Totals row
    totals = {
        "total": sum(s.get("total", 0) for s in area_scores.values()),
        "pass": sum(s.get("pass", 0) for s in area_scores.values()),
        "partial": sum(s.get("partial", 0) for s in area_scores.values()),
        "fail": sum(s.get("fail", 0) for s in area_scores.values()),
        "deferred": sum(s.get("deferred", 0) for s in area_scores.values()),
        "not_applicable": sum(
            s.get("not_applicable", 0) for s in area_scores.values()
        ),
        "not_assessed": sum(
            s.get("not_assessed", 0) for s in area_scores.values()
        ),
    }
    lines.append(
        f"| **Total** | -- "
        f"| **{totals['total']}** "
        f"| **{totals['pass']}** "
        f"| **{totals['partial']}** "
        f"| **{totals['fail']}** "
        f"| **{totals['deferred']}** "
        f"| **{totals['not_applicable']}** "
        f"| **{totals['not_assessed']}** |"
    )
    return "\n".join(lines)


def _build_process_area_details(assessments, area_scores):
    """Build markdown detail sections for each process area.

    Each area gets a sub-heading and a table listing every requirement
    with its status, evidence description, and notes.
    """
    area_data = {area: [] for area in PROCESS_AREAS}
    for a in assessments:
        pa = a.get("process_area")
        if pa in area_data:
            area_data[pa].append(a)

    sections = []
    for area in PROCESS_AREAS:
        items = area_data[area]
        s = area_scores.get(area, {})
        score = s.get("score", 0.0)
        v_type = "Verification" if area in VERIFICATION_AREAS else "Validation"

        sections.append(f"#### {area} ({score:.1f}%) — {v_type}")
        sections.append("")

        if not items:
            sections.append(
                "*No assessments recorded for this process area.*"
            )
            sections.append("")
            continue

        sections.append(
            "| Req ID | Title | Status | Evidence | Notes |"
        )
        sections.append(
            "|--------|-------|--------|----------|-------|"
        )
        for item in sorted(items, key=lambda x: x.get("requirement_id", "")):
            req_id = item.get("requirement_id", "N/A")
            # Attempt to get the title from the automation_result field
            # which may contain structured data
            title = ""
            auto_result = item.get("automation_result", "")
            if auto_result:
                try:
                    auto_data = json.loads(auto_result)
                    title = auto_data.get("title", "")
                except (json.JSONDecodeError, TypeError):
                    title = ""
            if not title:
                title = req_id  # Fallback to the requirement ID

            status = item.get("status", "not_assessed")
            evidence = (
                (item.get("evidence_description") or "")
                .replace("\n", " ")
                .strip()
            )
            notes = (
                (item.get("notes") or "").replace("\n", " ").strip()
            )

            # Truncate long fields for table readability
            if len(title) > 50:
                title = title[:47] + "..."
            if len(evidence) > 60:
                evidence = evidence[:57] + "..."
            if len(notes) > 60:
                notes = notes[:57] + "..."

            # Status badge for readability
            status_badge = _status_badge(status)

            sections.append(
                f"| {req_id} | {title} | {status_badge} "
                f"| {evidence} | {notes} |"
            )
        sections.append("")

    return "\n".join(sections)


def _status_badge(status):
    """Return a markdown-friendly status indicator."""
    badges = {
        "pass": "PASS",
        "partial": "PARTIAL",
        "fail": "**FAIL**",
        "deferred": "DEFERRED",
        "not_assessed": "NOT ASSESSED",
        "not_applicable": "N/A",
    }
    return badges.get(status, status.upper() if status else "UNKNOWN")


def _build_findings_by_severity(findings):
    """Build per-severity sections of IV&V findings.

    Returns a dict mapping severity to a markdown string.
    """
    grouped = {sev: [] for sev in SEVERITY_ORDER}
    for f in findings:
        sev = f.get("severity", "low")
        if sev in grouped:
            grouped[sev].append(f)

    result = {}
    for sev in SEVERITY_ORDER:
        items = grouped[sev]
        if not items:
            result[sev] = f"*No {sev} findings.*"
            continue

        lines = [
            "| Finding ID | Process Area | Title | Status | Recommendation |",
            "|------------|-------------|-------|--------|----------------|",
        ]
        for f in sorted(items, key=lambda x: x.get("finding_id", "")):
            fid = f.get("finding_id", "N/A")
            pa = f.get("process_area", "N/A")
            title = (f.get("title") or "").replace("\n", " ").strip()
            status = f.get("status", "open")
            rec = (
                (f.get("recommendation") or "").replace("\n", " ").strip()
            )

            if len(title) > 50:
                title = title[:47] + "..."
            if len(rec) > 60:
                rec = rec[:57] + "..."

            lines.append(
                f"| {fid} | {pa} | {title} | {status} | {rec} |"
            )

        result[sev] = "\n".join(lines)

    return result


def _build_findings_summary_table(findings):
    """Build a summary table of findings by severity and status.

    Returns the markdown rows (without header — the template provides the
    header already).
    """
    # Initialize counts grid
    counts = {
        sev: {st: 0 for st in FINDING_STATUSES}
        for sev in SEVERITY_ORDER
    }

    for f in findings:
        sev = f.get("severity", "low")
        st = f.get("status", "open")
        if sev in counts and st in counts[sev]:
            counts[sev][st] += 1

    lines = []
    grand_total = 0
    for sev in SEVERITY_ORDER:
        c = counts[sev]
        total = sum(c.values())
        grand_total += total
        lines.append(
            f"| {sev.capitalize()} "
            f"| {c.get('open', 0)} "
            f"| {c.get('resolved', 0)} "
            f"| {c.get('accepted_risk', 0)} "
            f"| {c.get('deferred', 0)} "
            f"| {total} |"
        )

    # Grand total row
    total_open = sum(counts[s]["open"] for s in SEVERITY_ORDER)
    total_resolved = sum(counts[s]["resolved"] for s in SEVERITY_ORDER)
    total_accepted = sum(
        counts[s]["accepted_risk"] for s in SEVERITY_ORDER
    )
    total_deferred = sum(counts[s]["deferred"] for s in SEVERITY_ORDER)
    lines.append(
        f"| **Total** "
        f"| **{total_open}** "
        f"| **{total_resolved}** "
        f"| **{total_accepted}** "
        f"| **{total_deferred}** "
        f"| **{grand_total}** |"
    )

    return "\n".join(lines)


def _build_rtm_summary(conn, project_id):
    """Build an RTM summary section by looking for RTM data.

    Attempts to find RTM JSON output from a previous traceability_matrix.py
    run.  Falls back to a placeholder if no data is found.
    """
    # Try to find RTM JSON in the project directory
    try:
        project = _get_project_data(conn, project_id)
        project_dir = project.get("directory_path", "")
        if project_dir:
            rtm_json_path = (
                Path(project_dir) / "compliance" / "rtm" / "rtm-data.json"
            )
            if rtm_json_path.exists():
                with open(rtm_json_path, "r", encoding="utf-8") as f:
                    rtm_data = json.load(f)

                coverage = rtm_data.get("coverage", 0.0)
                traced = rtm_data.get("traced", 0)
                total = rtm_data.get("total_requirements", 0)
                gaps = rtm_data.get("gaps", {})
                gap_count = gaps.get("gap_count", 0)
                orphan_count = len(gaps.get("orphan_tests", []))

                lines = [
                    f"RTM data loaded from: `{rtm_json_path}`",
                    "",
                    f"- **Total Requirements:** {total}",
                    f"- **Fully Traced:** {traced}",
                    f"- **Coverage:** {coverage:.1f}%",
                    f"- **Gap Count:** {gap_count}",
                    f"- **Orphan Tests:** {orphan_count}",
                ]
                return (
                    "\n".join(lines),
                    coverage,
                    traced,
                    gap_count,
                    orphan_count,
                )
    except Exception:
        pass

    # Fallback — no RTM data found
    placeholder = (
        "*No Requirements Traceability Matrix data found. "
        "Run `python tools/compliance/traceability_matrix.py "
        f"--project-id {project_id}` to generate RTM.*"
    )
    return placeholder, 0.0, 0, 0, 0


def _build_independence_declaration():
    """Return the standard IEEE 1012 independence statement.

    This is the boilerplate independence declaration required by IEEE 1012
    for any IV&V assessment to be considered independent.
    """
    return (
        "This Independent Verification and Validation assessment was "
        "conducted separately from the development team per IEEE 1012 and "
        "DoD requirements. The IV&V engine operates with:\n\n"
        "- **Organizational Independence:** Separate assessment authority "
        "from development\n"
        "- **Technical Independence:** Independent analysis tools and "
        "criteria\n"
        "- **Financial Independence:** Assessment budget separate from "
        "development\n"
        "- **Authority:** Gate authority to block releases based on findings\n"
        "\n"
        "The IV&V assessor has no reporting relationship to the development "
        "organization and maintains independent access to all project "
        "artifacts, source code, test results, and configuration data. "
        "Assessment criteria are derived from IEEE 1012-2016, DoDI 5000.87, "
        "and NIST 800-53 Rev 5 security controls."
    )


def _build_conditions(recommendation, area_scores, findings):
    """Build conditions text for CONDITIONAL recommendations.

    Returns a markdown string describing what must be remediated for full
    certification.
    """
    if recommendation == "CERTIFY":
        return "*No conditions — full certification recommended.*"

    if recommendation == "DENY":
        # List the blocking issues
        lines = ["**Blocking Issues (must be resolved before resubmission):**", ""]
        critical_open = [
            f for f in findings
            if f.get("severity") == "critical"
            and f.get("status") in ("open", "in_progress")
        ]
        if critical_open:
            lines.append(
                f"1. **{len(critical_open)} critical finding(s) "
                "must be resolved:**"
            )
            for f in critical_open:
                fid = f.get("finding_id", "N/A")
                title = f.get("title", "N/A")
                lines.append(f"   - {fid}: {title}")
            lines.append("")

        areas_below_60 = [
            area for area in PROCESS_AREAS
            if area_scores.get(area, {}).get("total", 0) > 0
            and area_scores.get(area, {}).get("score", 0.0) < 60.0
        ]
        if areas_below_60:
            lines.append(
                "2. **Process areas below 60% minimum:**"
            )
            for area in areas_below_60:
                score = area_scores[area]["score"]
                lines.append(f"   - {area}: {score:.1f}%")
            lines.append("")

        return "\n".join(lines)

    # CONDITIONAL — list what needs improvement
    lines = [
        "**Conditions for Full Certification:**",
        "",
        "The following conditions must be met within 90 days for the "
        "conditional certification to be elevated to full certification:",
        "",
    ]

    condition_num = 1

    # Areas below 60%
    areas_below_60 = [
        area for area in PROCESS_AREAS
        if area_scores.get(area, {}).get("total", 0) > 0
        and area_scores.get(area, {}).get("score", 0.0) < 60.0
    ]
    if areas_below_60:
        for area in areas_below_60:
            score = area_scores[area]["score"]
            lines.append(
                f"{condition_num}. Raise **{area}** score from "
                f"{score:.1f}% to at least 60%."
            )
            condition_num += 1

    # Areas between 60% and 80% (advisory)
    areas_below_80 = [
        area for area in PROCESS_AREAS
        if area_scores.get(area, {}).get("total", 0) > 0
        and 60.0 <= area_scores.get(area, {}).get("score", 0.0) < 80.0
    ]
    if areas_below_80:
        for area in areas_below_80:
            score = area_scores[area]["score"]
            lines.append(
                f"{condition_num}. Improve **{area}** score from "
                f"{score:.1f}% toward 80% target."
            )
            condition_num += 1

    # Open high findings
    high_open = [
        f for f in findings
        if f.get("severity") == "high"
        and f.get("status") in ("open", "in_progress")
    ]
    if high_open:
        lines.append(
            f"{condition_num}. Resolve {len(high_open)} open high-severity "
            "finding(s)."
        )
        condition_num += 1

    # Open moderate findings (advisory)
    moderate_open = [
        f for f in findings
        if f.get("severity") == "moderate"
        and f.get("status") in ("open", "in_progress")
    ]
    if moderate_open:
        lines.append(
            f"{condition_num}. Address {len(moderate_open)} open "
            "moderate-severity finding(s)."
        )
        condition_num += 1

    if condition_num == 1:
        lines.append(
            "1. Raise overall IV&V score to 80% or above for full "
            "certification."
        )

    lines.append("")
    lines.append(
        "**Review Date:** A follow-up review will be scheduled within "
        "90 calendar days to verify condition completion."
    )

    return "\n".join(lines)


def _build_evidence_index(assessments):
    """Build an evidence index table from assessment evidence paths.

    Lists all assessments that have an evidence_path recorded.
    """
    with_evidence = [
        a for a in assessments if a.get("evidence_path")
    ]

    if not with_evidence:
        return "*No evidence artifacts recorded in assessments.*"

    lines = [
        "| Req ID | Process Area | Evidence Path |",
        "|--------|-------------|---------------|",
    ]
    for a in sorted(with_evidence, key=lambda x: x.get("requirement_id", "")):
        req_id = a.get("requirement_id", "N/A")
        pa = a.get("process_area", "N/A")
        path = a.get("evidence_path", "N/A")
        lines.append(f"| {req_id} | {pa} | `{path}` |")

    # Summary
    total = len(assessments)
    with_count = len(with_evidence)
    without_count = total - with_count
    coverage = (
        f"{100.0 * with_count / total:.0f}%"
        if total > 0
        else "N/A"
    )
    lines.append("")
    lines.append(
        f"**Evidence Coverage:** {with_count}/{total} assessments "
        f"have evidence artifacts ({coverage})"
    )
    if without_count > 0:
        missing = [
            a for a in assessments if not a.get("evidence_path")
        ]
        missing_ids = [
            a.get("requirement_id", "?") for a in missing
        ]
        if len(missing_ids) <= 10:
            lines.append(
                f"**Missing Evidence:** {', '.join(missing_ids)}"
            )
        else:
            lines.append(
                f"**Missing Evidence:** {', '.join(missing_ids[:10])} "
                f"(and {len(missing_ids) - 10} more)"
            )

    return "\n".join(lines)


def _build_executive_summary(
    verification_score,
    validation_score,
    overall_score,
    recommendation,
    reason,
    area_scores,
    findings,
    assessments,
):
    """Build the executive summary paragraph."""
    total_assessments = len(assessments)
    total_findings = len(findings)
    critical_open = sum(
        1 for f in findings
        if f.get("severity") == "critical"
        and f.get("status") in ("open", "in_progress")
    )
    high_open = sum(
        1 for f in findings
        if f.get("severity") == "high"
        and f.get("status") in ("open", "in_progress")
    )

    # Count assessments by status
    sum(
        1 for a in assessments if a.get("status") == "pass"
    )
    sum(
        1 for a in assessments if a.get("status") == "fail"
    )

    lines = []
    lines.append(
        f"This IV&V assessment evaluated {total_assessments} requirements "
        f"across {len(PROCESS_AREAS)} process areas per IEEE 1012-2016 and "
        f"DoDI 5000.87. The verification score is **{verification_score:.1f}%** "
        f"and the validation score is **{validation_score:.1f}%**, yielding an "
        f"overall weighted score of **{overall_score:.1f}%**."
    )
    lines.append("")

    if total_findings > 0:
        lines.append(
            f"The assessment identified **{total_findings} finding(s)**: "
            f"{critical_open} critical open, {high_open} high open. "
        )
    else:
        lines.append("No findings were identified during this assessment.")

    lines.append("")
    lines.append(
        f"**Certification Recommendation: {recommendation}** — {reason}"
    )

    # Highlight strongest and weakest areas
    scored_areas = [
        (area, area_scores[area]["score"])
        for area in PROCESS_AREAS
        if area_scores.get(area, {}).get("total", 0) > 0
    ]
    if scored_areas:
        scored_areas.sort(key=lambda x: x[1], reverse=True)
        strongest = scored_areas[0]
        weakest = scored_areas[-1]
        if strongest[0] != weakest[0]:
            lines.append("")
            lines.append(
                f"**Strongest Area:** {strongest[0]} ({strongest[1]:.1f}%)  \n"
                f"**Weakest Area:** {weakest[0]} ({weakest[1]:.1f}%)"
            )

    return "\n".join(lines)


def _build_area_subset_details(assessments, area_scores, area_list, label):
    """Build detail tables for a subset of process areas (used for
    validation area breakouts in sections 4.1 and 4.2)."""
    area_data = {area: [] for area in area_list}
    for a in assessments:
        pa = a.get("process_area")
        if pa in area_data:
            area_data[pa].append(a)

    sections = []
    for area in area_list:
        items = area_data[area]
        s = area_scores.get(area, {})
        score = s.get("score", 0.0)

        sections.append(f"**{area}** — Score: {score:.1f}%")
        sections.append("")

        if not items:
            sections.append(
                "*No assessments recorded for this area.*"
            )
            sections.append("")
            continue

        sections.append("| Req ID | Status | Evidence | Notes |")
        sections.append("|--------|--------|----------|-------|")
        for item in sorted(items, key=lambda x: x.get("requirement_id", "")):
            req_id = item.get("requirement_id", "N/A")
            status = _status_badge(item.get("status", "not_assessed"))
            evidence = (
                (item.get("evidence_description") or "")
                .replace("\n", " ")
                .strip()
            )
            notes = (
                (item.get("notes") or "").replace("\n", " ").strip()
            )
            if len(evidence) > 60:
                evidence = evidence[:57] + "..."
            if len(notes) > 60:
                notes = notes[:57] + "..."
            sections.append(
                f"| {req_id} | {status} | {evidence} | {notes} |"
            )
        sections.append("")

    return "\n".join(sections)


def _determine_gate_result(findings):
    """Determine the IV&V gate result.

    PASS if zero critical findings are open; FAIL otherwise.
    """
    critical_open = sum(
        1 for f in findings
        if f.get("severity") == "critical"
        and f.get("status") in ("open", "in_progress")
    )
    if critical_open > 0:
        return "FAIL", critical_open
    return "PASS", 0


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
    """Log an audit trail event for IV&V report generation.

    Uses 'compliance_check' as the event_type since IV&V report generation
    falls under compliance verification activities.
    """
    try:
        conn.execute(
            """INSERT INTO audit_trail
               (project_id, event_type, actor, action, details,
                affected_files, classification)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (
                project_id,
                "compliance_check",
                "icdev-ivv-engine",
                action,
                json.dumps(details),
                json.dumps([str(file_path)]),
                "CUI",
            ),
        )
        conn.commit()
    except Exception as e:
        print(
            f"Warning: Could not log audit event: {e}", file=sys.stderr
        )


# ---------------------------------------------------------------------------
# Main generator
# ---------------------------------------------------------------------------

def generate_ivv_report(project_id, output_path=None, db_path=None):
    """Generate an IV&V certification report for a project.

    Workflow:
        1. Connect, load project, template, requirements
        2. Query all 3 IV&V tables
        3. Calculate scores (verification, validation, overall)
        4. Determine certification recommendation
        5. Build all sections
        6. Apply {{variable}} substitution
        7. Apply CUI markings
        8. Write to: {project_dir}/compliance/ivv-report-v{version}.md
        9. Update ivv_certifications table with scores and recommendation
       10. Audit: "compliance_check" (IV&V report generated)
       11. Return result dict

    Args:
        project_id: The project identifier.
        output_path: Override output directory or file path.
        db_path: Override database path.

    Returns:
        dict with ``file_path`` and metadata about the generated report.
    """
    conn = _get_connection(db_path)
    try:
        # 1. Load project data
        project = _get_project_data(conn, project_id)
        project_name = project.get("name", project_id)

        # 2. Load template
        template = _load_template()

        # 3. Load IV&V requirements catalog
        ivv_catalog = _load_ivv_requirements()
        _requirements_list = ivv_catalog.get("requirements", [])

        # 4. Query all IV&V tables
        assessments = _get_ivv_assessments(conn, project_id)
        findings = _get_ivv_findings(conn, project_id)
        certification = _get_ivv_certification(conn, project_id)

        # 5. Calculate scores
        area_scores = _calculate_process_area_scores(assessments)
        verification_score = _calculate_verification_score(area_scores)
        validation_score = _calculate_validation_score(area_scores)
        overall_score = _calculate_overall_score(
            verification_score, validation_score
        )

        # 6. Determine certification recommendation
        recommendation, reason = _determine_certification_recommendation(
            overall_score, area_scores, findings
        )

        # 7. Determine gate result
        gate_result, critical_open_count = _determine_gate_result(findings)

        # 8. Build all report sections
        process_area_scores_table = _build_process_area_scores_table(
            area_scores
        )
        process_area_details = _build_process_area_details(
            assessments, area_scores
        )
        findings_by_severity = _build_findings_by_severity(findings)
        findings_summary_table = _build_findings_summary_table(findings)

        rtm_summary, rtm_coverage, rtm_traced, rtm_gaps, rtm_orphans = (
            _build_rtm_summary(conn, project_id)
        )

        independence_declaration = _build_independence_declaration()
        conditions = _build_conditions(
            recommendation, area_scores, findings
        )
        evidence_index = _build_evidence_index(assessments)

        # Build validation area breakouts for sections 4.1 and 4.2
        test_verification_results = _build_area_subset_details(
            assessments, area_scores, ["Test Verification"],
            "Test Verification"
        )
        integration_verification_results = _build_area_subset_details(
            assessments, area_scores, ["Integration Verification"],
            "Integration Verification"
        )

        # Build executive summary
        executive_summary = _build_executive_summary(
            verification_score,
            validation_score,
            overall_score,
            recommendation,
            reason,
            area_scores,
            findings,
            assessments,
        )

        # Load CUI config for banner variables
        cui_config = _load_cui_config()

        # Determine version number from prior audit events
        report_count_row = conn.execute(
            """SELECT COUNT(*) as cnt FROM audit_trail
               WHERE project_id = ? AND event_type = 'compliance_check'
               AND action LIKE '%IV&V report%'""",
            (project_id,),
        ).fetchone()
        report_count = report_count_row["cnt"] if report_count_row else 0
        new_version = f"{report_count + 1}.0"

        now = datetime.now(timezone.utc)

        # 9. Build the complete variable substitution dict
        variables = {
            # Project info
            "project_name": project_name,
            "project_id": project_id,
            "classification": project.get("classification", "CUI"),
            "system_type": project.get("type", "webapp"),

            # Report metadata
            "version": new_version,
            "report_version": new_version,
            "assessment_date": now.strftime("%Y-%m-%d"),
            "date_prepared": now.strftime("%Y-%m-%d"),
            "generation_timestamp": now.strftime("%Y-%m-%d %H:%M UTC"),
            "icdev_version": "1.0",
            "ivv_authority": certification.get(
                "ivv_authority", "ICDEV IV&V Engine"
            ),

            # Scores
            "verification_score": f"{verification_score:.1f}",
            "validation_score": f"{validation_score:.1f}",
            "overall_score": f"{overall_score:.1f}",

            # Gate result
            "gate_result": gate_result,

            # Certification recommendation
            "certification_recommendation": recommendation,
            "certification_reason": reason,

            # Executive summary
            "executive_summary": executive_summary,

            # Independence declaration
            "independence_declaration": independence_declaration,

            # Process area tables
            "process_area_scores_table": process_area_scores_table,
            "process_area_details": process_area_details,

            # Validation breakouts
            "test_verification_results": test_verification_results,
            "integration_verification_results": (
                integration_verification_results
            ),

            # RTM summary
            "rtm_summary": rtm_summary,
            "rtm_coverage": f"{rtm_coverage:.1f}" if rtm_coverage else "0.0",
            "rtm_full_trace_count": str(rtm_traced),
            "rtm_gap_count": str(rtm_gaps),
            "rtm_orphan_tests": str(rtm_orphans),

            # Findings by severity
            "critical_findings": findings_by_severity.get(
                "critical", "*No critical findings.*"
            ),
            "high_findings": findings_by_severity.get(
                "high", "*No high findings.*"
            ),
            "moderate_findings": findings_by_severity.get(
                "moderate", "*No moderate findings.*"
            ),
            "low_findings": findings_by_severity.get(
                "low", "*No low findings.*"
            ),
            "findings_summary_table": findings_summary_table,

            # Conditions
            "conditions": conditions,

            # Evidence index
            "evidence_index": evidence_index,

            # Next review date (90 days from now if not set)
            "next_review_date": certification.get(
                "next_review_date",
                (now + timedelta(days=90)).strftime("%Y-%m-%d"),
            ),

            # Certification info from existing record
            "certification_status": certification.get(
                "status", "in_progress"
            ),
            "certified_date": certification.get("certified_date", "N/A"),
            "expiration_date": certification.get("expiration_date", "N/A"),
            "open_findings_count": str(
                sum(
                    1 for f in findings
                    if f.get("status") in ("open", "in_progress")
                )
            ),
            "critical_findings_count": str(critical_open_count),

            # Assessment totals
            "total_assessments": str(len(assessments)),
            "total_findings": str(len(findings)),
            "assessments_pass": str(
                sum(1 for a in assessments if a.get("status") == "pass")
            ),
            "assessments_fail": str(
                sum(1 for a in assessments if a.get("status") == "fail")
            ),

            # CUI banners
            "cui_banner_top": cui_config.get(
                "document_header",
                cui_config.get("banner_top", "CUI // SP-CTI"),
            ),
            "cui_banner_bottom": cui_config.get(
                "document_footer",
                cui_config.get("banner_bottom", "CUI // SP-CTI"),
            ),
        }

        # Per-area score variables (e.g., requirements_verification_score)
        for area in PROCESS_AREAS:
            key_prefix = area.lower().replace(" ", "_").replace("/", "_")
            s = area_scores.get(area, {})
            variables[f"{key_prefix}_score"] = f"{s.get('score', 0.0):.1f}"
            variables[f"{key_prefix}_total"] = str(s.get("total", 0))
            variables[f"{key_prefix}_pass"] = str(s.get("pass", 0))
            variables[f"{key_prefix}_fail"] = str(s.get("fail", 0))

        # 10. Substitute variables in template
        report_content = _substitute_variables(template, variables)

        # 11. Apply CUI markings
        report_content = _apply_cui_markings(report_content, cui_config)

        # 12. Determine output file path
        if output_path:
            out_path = Path(output_path)
            if (
                out_path.is_dir()
                or str(output_path).endswith("/")
                or str(output_path).endswith("\\")
            ):
                out_dir = out_path
                out_file = out_dir / f"ivv-report-v{new_version}.md"
            else:
                out_file = out_path
        else:
            dir_path = project.get("directory_path", "")
            if dir_path:
                out_dir = Path(dir_path) / "compliance"
            else:
                out_dir = (
                    BASE_DIR / "projects" / project_name / "compliance"
                )
            out_file = out_dir / f"ivv-report-v{new_version}.md"

        out_file.parent.mkdir(parents=True, exist_ok=True)

        with open(out_file, "w", encoding="utf-8") as f:
            f.write(report_content)

        # 13. Update ivv_certifications table with scores and recommendation
        try:
            # Map recommendation to DB status
            status_map = {
                "CERTIFY": "certified",
                "CONDITIONAL": "conditional",
                "DENY": "denied",
            }
            cert_status = status_map.get(recommendation, "in_progress")

            # Count open and critical findings
            open_count = sum(
                1 for f in findings
                if f.get("status") in ("open", "in_progress")
            )

            conn.execute(
                """INSERT OR REPLACE INTO ivv_certifications
                   (project_id, certification_type, status,
                    verification_score, validation_score, overall_score,
                    ivv_authority, independence_declaration,
                    conditions, open_findings_count,
                    critical_findings_count, next_review_date,
                    updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    project_id,
                    "IV&V",
                    cert_status,
                    verification_score,
                    validation_score,
                    overall_score,
                    certification.get(
                        "ivv_authority", "ICDEV IV&V Engine"
                    ),
                    "IEEE 1012 Independent Assessment",
                    conditions if recommendation == "CONDITIONAL" else None,
                    open_count,
                    critical_open_count,
                    (now + timedelta(days=90)).strftime("%Y-%m-%d"),
                    now.strftime("%Y-%m-%d %H:%M:%S"),
                ),
            )
            conn.commit()
        except Exception as e:
            print(
                f"Warning: Could not update ivv_certifications: {e}",
                file=sys.stderr,
            )

        # 14. Log audit event
        audit_details = {
            "report_type": "IV&V Certification Report",
            "version": new_version,
            "verification_score": verification_score,
            "validation_score": validation_score,
            "overall_score": overall_score,
            "gate_result": gate_result,
            "recommendation": recommendation,
            "total_assessments": len(assessments),
            "total_findings": len(findings),
            "critical_open": critical_open_count,
            "output_file": str(out_file),
        }
        _log_audit_event(
            conn,
            project_id,
            f"IV&V report v{new_version} generated — {recommendation}",
            audit_details,
            out_file,
        )

        # 15. Print summary
        print("IV&V certification report generated successfully:")
        print(f"  File:              {out_file}")
        print(f"  Version:           {new_version}")
        print(f"  Project:           {project_name}")
        print(f"  Verification:      {verification_score:.1f}%")
        print(f"  Validation:        {validation_score:.1f}%")
        print(f"  Overall Score:     {overall_score:.1f}%")
        print(f"  Gate Result:       {gate_result}")
        print(f"  Recommendation:    {recommendation}")
        print(f"  Assessments:       {len(assessments)}")
        print(f"  Findings:          {len(findings)}")
        print(f"  Critical Open:     {critical_open_count}")

        # 16. Return result dict
        return {
            "file_path": str(out_file),
            "version": new_version,
            "project_id": project_id,
            "project_name": project_name,
            "verification_score": verification_score,
            "validation_score": validation_score,
            "overall_score": overall_score,
            "gate_result": gate_result,
            "recommendation": recommendation,
            "reason": reason,
            "process_area_scores": {
                area: area_scores[area]["score"]
                for area in PROCESS_AREAS
            },
            "total_assessments": len(assessments),
            "total_findings": len(findings),
            "critical_open_findings": critical_open_count,
            "rtm_coverage": rtm_coverage,
            "generated_at": now.isoformat(),
        }

    finally:
        conn.close()


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

def _format_json_output(result):
    """Format result as JSON for machine-readable output."""
    return json.dumps(result, indent=2, default=str)


def _format_text_output(result):
    """Format result as human-readable text."""
    lines = [
        "=" * 60,
        "IV&V CERTIFICATION REPORT SUMMARY",
        "=" * 60,
        "",
        f"  File:              {result['file_path']}",
        f"  Version:           {result['version']}",
        f"  Project:           {result['project_name']} ({result['project_id']})",
        "",
        "  SCORES:",
        f"    Verification:    {result['verification_score']:.1f}%",
        f"    Validation:      {result['validation_score']:.1f}%",
        f"    Overall:         {result['overall_score']:.1f}%",
        "",
        f"  Gate Result:       {result['gate_result']}",
        f"  Recommendation:    {result['recommendation']}",
        "",
        "  PROCESS AREA SCORES:",
    ]
    for area, score in result.get("process_area_scores", {}).items():
        lines.append(f"    {area:30s} {score:.1f}%")
    lines.extend([
        "",
        f"  Total Assessments: {result['total_assessments']}",
        f"  Total Findings:    {result['total_findings']}",
        f"  Critical Open:     {result['critical_open_findings']}",
        f"  RTM Coverage:      {result['rtm_coverage']:.1f}%",
        f"  Generated:         {result['generated_at']}",
        "",
        "=" * 60,
    ])
    return "\n".join(lines)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Generate IV&V certification report per IEEE 1012"
    )
    parser.add_argument(
        "--project-id",
        required=True,
        help="Project ID to generate IV&V report for",
    )
    parser.add_argument(
        "--output-dir",
        help="Output directory (default: {project_dir}/compliance/)",
    )
    parser.add_argument(
        "--db-path",
        type=Path,
        default=DB_PATH,
        help="Database path (default: data/icdev.db)",
    )
    parser.add_argument(
        "--format",
        choices=["text", "json"],
        default="text",
        help="Output format for CLI summary (default: text)",
    )

    parser.add_argument("--json", action="store_true", dest="json_output", help="JSON output")
    args = parser.parse_args()

    try:
        result = generate_ivv_report(
            args.project_id, args.output_dir, args.db_path
        )
        if args.format == "json":
            print(_format_json_output(result))
        else:
            print(_format_text_output(result))
    except FileNotFoundError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        sys.exit(1)
    except ValueError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        sys.exit(1)
