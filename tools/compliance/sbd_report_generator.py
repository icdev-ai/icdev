#!/usr/bin/env python3
# CUI // SP-CTI
"""Secure by Design (SbD) report generator.

Loads sbd_report_template.md, queries sbd_assessments table, builds domain scores
and CISA commitment status, generates a comprehensive SbD assessment report with
CUI markings."""

import argparse
import json
import re
import sqlite3
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent.parent
DB_PATH = BASE_DIR / "data" / "icdev.db"
SBD_TEMPLATE_PATH = BASE_DIR / "context" / "compliance" / "sbd_report_template.md"
SBD_REQUIREMENTS_PATH = BASE_DIR / "context" / "compliance" / "cisa_sbd_requirements.json"

# SbD domains as defined in the CISA requirements catalog
SBD_DOMAINS = [
    "Authentication",
    "Memory Safety",
    "Vulnerability Management",
    "Intrusion Evidence",
    "Cryptography",
    "Access Control",
    "Input Handling",
    "Error Handling",
    "Supply Chain",
    "Threat Modeling",
    "Defense in Depth",
    "Secure Defaults",
    "CUI Compliance",
    "DoD Software Assurance",
]

# CISA Secure by Design commitments (7 pledges)
CISA_COMMITMENTS = {
    1: "Multi-Factor Authentication",
    2: "Default Password Elimination",
    3: "Vulnerability Class Reduction",
    4: "Security Patch Deployment",
    5: "Vulnerability Disclosure Policy",
    6: "CVE Transparency",
    7: "Intrusion Evidence Collection",
}

# Priority ordering for remediation
PRIORITY_ORDER = ["critical", "high", "medium", "low"]


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
    """Load the SbD report template markdown.

    If the template file does not exist a minimal built-in template is
    returned so the generator can still produce a useful report.
    """
    path = template_path or SBD_TEMPLATE_PATH
    if path.exists():
        with open(path, "r", encoding="utf-8") as f:
            return f.read()

    # Fallback minimal template when file is missing
    return _builtin_template()


def _builtin_template():
    """Return a minimal built-in SbD report template."""
    return (
        "{{cui_banner_top}}\n\n"
        "# Secure by Design Assessment Report\n\n"
        "**Project:** {{project_name}}\n"
        "**Project ID:** {{project_id}}\n"
        "**Classification:** {{classification}}\n"
        "**Assessment Date:** {{assessment_date}}\n"
        "**Report Version:** {{version}}\n"
        "**Assessor:** {{assessor}}\n"
        "**Framework:** CISA Secure by Design + DoDI 5000.87 + NIST SP 800-218 SSDF\n\n"
        "---\n\n"
        "## 1. Executive Summary\n\n"
        "**Overall SbD Score:** {{overall_score}}%\n"
        "**Gate Result:** {{gate_result}}\n"
        "**Domains Assessed:** {{domains_assessed}} / 14\n"
        "**Critical Requirements Not Satisfied:** {{critical_not_satisfied}}\n\n"
        "{{executive_summary}}\n\n"
        "## 2. CISA Secure by Design Commitment Status\n\n"
        "The following table shows compliance with the 7 CISA Secure by Design commitments:\n\n"
        "{{cisa_commitment_table}}\n\n"
        "## 3. Domain Assessment Summary\n\n"
        "{{domain_scores_table}}\n\n"
        "## 4. Detailed Domain Assessments\n\n"
        "{{domain_details}}\n\n"
        "## 5. Auto-Check Results\n\n"
        "{{auto_check_results}}\n\n"
        "## 6. Manual Review Items\n\n"
        "The following requirements require manual verification:\n\n"
        "{{manual_review_items}}\n\n"
        "## 7. Findings and Remediation\n\n"
        "### Critical Findings\n"
        "{{critical_findings}}\n\n"
        "### Remediation Recommendations\n"
        "{{remediation_table}}\n\n"
        "## 8. Evidence Artifacts\n\n"
        "{{evidence_summary}}\n\n"
        "## 9. NIST 800-53 Control Mapping\n\n"
        "{{nist_control_mapping}}\n\n"
        "## 10. Assessment Methodology\n\n"
        "This assessment was conducted using the ICDEV SbD Assessor tool against the "
        "CISA Secure by Design requirements catalog (35 requirements across 14 domains). "
        "Automated checks were performed where possible; requirements marked as \"semi\" "
        "or \"manual\" are flagged for human review.\n\n"
        "**Scoring Formula:** Score = 100 x (satisfied + partially_satisfied x 0.5 + "
        "risk_accepted x 0.75) / assessable_count\n\n"
        "**Gate Logic:** PASS if 0 critical-priority requirements have status "
        "\"not_satisfied\"\n\n"
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


def _load_sbd_requirements():
    """Load the CISA SbD requirements catalog for reference data.

    Returns a dict keyed by requirement ID with full requirement metadata
    including domain, cisa_commitment, priority, nist_controls, etc.
    Falls back to an empty dict if the file is unavailable.
    """
    path = SBD_REQUIREMENTS_PATH
    if not path.exists():
        return {}

    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        requirements = {}
        for req in data.get("requirements", []):
            requirements[req["id"]] = req
        return requirements
    except (json.JSONDecodeError, KeyError, TypeError) as e:
        print(f"Warning: Could not load SbD requirements catalog: {e}", file=sys.stderr)
        return {}


# ---------------------------------------------------------------------------
# Data retrieval
# ---------------------------------------------------------------------------

def _get_sbd_assessments(conn, project_id):
    """Retrieve all SbD assessment results for a project."""
    rows = conn.execute(
        """SELECT * FROM sbd_assessments
           WHERE project_id = ?
           ORDER BY domain, requirement_id""",
        (project_id,),
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
    """Retrieve SBOM records for supply chain status cross-reference."""
    rows = conn.execute(
        """SELECT * FROM sbom_records
           WHERE project_id = ?
           ORDER BY generated_at DESC""",
        (project_id,),
    ).fetchall()
    return [dict(r) for r in rows]


# ---------------------------------------------------------------------------
# Score calculation
# ---------------------------------------------------------------------------

def _calculate_domain_scores(assessments):
    """Calculate a compliance score for each SbD domain.

    Score formula:
        score = 100 * (satisfied + partially_satisfied*0.5 + risk_accepted*0.75)
                / total (excluding not_applicable)

    Returns:
        dict mapping domain name to a dict with score, total, and
        per-status counts.
    """
    area_data = {domain: [] for domain in SBD_DOMAINS}
    for a in assessments:
        dom = a.get("domain")
        if dom in area_data:
            area_data[dom].append(a)

    results = {}
    for domain in SBD_DOMAINS:
        items = area_data[domain]
        total = len(items)
        if total == 0:
            results[domain] = {
                "score": 0.0,
                "total": 0,
                "satisfied": 0,
                "partially_satisfied": 0,
                "not_satisfied": 0,
                "not_applicable": 0,
                "not_assessed": 0,
                "risk_accepted": 0,
            }
            continue

        satisfied = sum(1 for i in items if i["status"] == "satisfied")
        partially = sum(1 for i in items if i["status"] == "partially_satisfied")
        not_satisfied = sum(1 for i in items if i["status"] == "not_satisfied")
        not_applicable = sum(1 for i in items if i["status"] == "not_applicable")
        not_assessed = sum(1 for i in items if i["status"] == "not_assessed")
        risk_accepted = sum(1 for i in items if i["status"] == "risk_accepted")

        # Denominator excludes not_applicable
        scoreable = total - not_applicable
        if scoreable > 0:
            score = 100.0 * (
                satisfied + partially * 0.5 + risk_accepted * 0.75
            ) / scoreable
        else:
            score = 100.0  # All N/A means fully compliant for this domain

        results[domain] = {
            "score": round(score, 1),
            "total": total,
            "satisfied": satisfied,
            "partially_satisfied": partially,
            "not_satisfied": not_satisfied,
            "not_applicable": not_applicable,
            "not_assessed": not_assessed,
            "risk_accepted": risk_accepted,
        }

    return results


def _calculate_cisa_commitment_status(assessments, requirements):
    """Map each of 7 CISA commitments to a compliance status.

    Uses the requirements catalog to determine which requirements map to
    each CISA commitment number. For each commitment, gathers assessments
    for the matching requirements and determines overall status.

    Status logic:
        - All satisfied -> "Compliant"
        - Any partially_satisfied (none not_satisfied) -> "Partially Compliant"
        - Any not_satisfied -> "Non-Compliant"
        - No assessments -> "Not Assessed"

    Returns:
        list of dicts with commitment_num, title, status, count,
        satisfied_count.
    """
    # Build mapping: commitment_num -> list of requirement IDs
    commitment_reqs = {num: [] for num in range(1, 8)}
    for req_id, req_data in requirements.items():
        cisa_num = req_data.get("cisa_commitment")
        if cisa_num and cisa_num in commitment_reqs:
            commitment_reqs[cisa_num].append(req_id)

    # Build mapping: requirement_id -> assessment
    assessment_map = {}
    for a in assessments:
        assessment_map[a.get("requirement_id")] = a

    results = []
    for num in range(1, 8):
        title = CISA_COMMITMENTS.get(num, f"Commitment {num}")
        req_ids = commitment_reqs[num]
        count = len(req_ids)

        if count == 0:
            results.append({
                "commitment_num": num,
                "title": title,
                "status": "Not Assessed",
                "count": 0,
                "satisfied_count": 0,
            })
            continue

        # Gather statuses for this commitment's requirements
        statuses = []
        satisfied_count = 0
        for req_id in req_ids:
            a = assessment_map.get(req_id)
            if a:
                st = a.get("status", "not_assessed")
                statuses.append(st)
                if st == "satisfied":
                    satisfied_count += 1
            else:
                statuses.append("not_assessed")

        # Determine commitment status
        if all(s == "satisfied" for s in statuses):
            status = "Compliant"
        elif all(s in ("satisfied", "risk_accepted") for s in statuses):
            status = "Compliant"
        elif any(s == "not_satisfied" for s in statuses):
            status = "Non-Compliant"
        elif any(s == "partially_satisfied" for s in statuses):
            status = "Partially Compliant"
        elif any(s == "not_assessed" for s in statuses):
            status = "Not Assessed"
        else:
            status = "Partially Compliant"

        results.append({
            "commitment_num": num,
            "title": title,
            "status": status,
            "count": count,
            "satisfied_count": satisfied_count,
        })

    return results


def _calculate_overall_status(domain_scores):
    """Determine overall status from domain scores.

    Returns:
        tuple of (overall_score, overall_status_label)
    """
    scoreable_domains = [v for v in domain_scores.values() if v["total"] > 0]
    if not scoreable_domains:
        return 0.0, "Non-Compliant"

    overall = sum(d["score"] for d in scoreable_domains) / len(scoreable_domains)
    overall = round(overall, 1)

    if overall >= 80:
        status = "Compliant"
    elif overall >= 50:
        status = "Partially Compliant"
    else:
        status = "Non-Compliant"

    return overall, status


# ---------------------------------------------------------------------------
# Section builder functions
# ---------------------------------------------------------------------------

def _build_domain_scores_table(domain_scores):
    """Build a markdown table summarising per-domain scores."""
    lines = [
        "| Domain | Score | Satisfied | Partial | Not Satisfied | Not Assessed | N/A | Risk Accepted |",
        "|--------|------:|----------:|--------:|--------------:|-------------:|----:|--------------:|",
    ]
    for domain in SBD_DOMAINS:
        s = domain_scores.get(domain, {})
        if s.get("total", 0) == 0:
            continue
        lines.append(
            f"| {domain} | {s.get('score', 0.0):.1f}% "
            f"| {s.get('satisfied', 0)} "
            f"| {s.get('partially_satisfied', 0)} "
            f"| {s.get('not_satisfied', 0)} "
            f"| {s.get('not_assessed', 0)} "
            f"| {s.get('not_applicable', 0)} "
            f"| {s.get('risk_accepted', 0)} |"
        )

    return "\n".join(lines)


def _build_cisa_commitment_table(cisa_status):
    """Build a markdown table of CISA commitment statuses."""
    lines = [
        "| # | Commitment | Status | Requirements | Satisfied |",
        "|---|-----------|--------|-------------:|----------:|",
    ]
    for c in cisa_status:
        num = c["commitment_num"]
        title = c["title"]
        status = c["status"]
        count = c["count"]
        satisfied = c["satisfied_count"]
        lines.append(
            f"| {num} | {title} | {status} | {count} | {satisfied} |"
        )

    return "\n".join(lines)


def _build_domain_details(assessments, domain_scores):
    """Build markdown detail sections for each assessed domain.

    Each domain gets a sub-heading and a table listing every requirement
    with its status, evidence description, and notes.
    """
    domain_data = {domain: [] for domain in SBD_DOMAINS}
    for a in assessments:
        dom = a.get("domain")
        if dom in domain_data:
            domain_data[dom].append(a)

    sections = []
    for domain in SBD_DOMAINS:
        items = domain_data[domain]
        s = domain_scores.get(domain, {})
        score = s.get("score", 0.0)

        # Skip domains with no assessments
        if not items and s.get("total", 0) == 0:
            continue

        sections.append(f"### {domain} ({score:.1f}%)")
        sections.append("")

        if not items:
            sections.append("*No assessments recorded for this domain.*")
            sections.append("")
            continue

        sections.append(
            "| Requirement ID | Status | Automation | Evidence | Notes |"
        )
        sections.append(
            "|----------------|--------|------------|----------|-------|"
        )
        for item in sorted(items, key=lambda x: x.get("requirement_id", "")):
            req_id = item.get("requirement_id", "N/A")
            status = item.get("status", "not_assessed")
            automation = item.get("automation_result", "N/A") or "N/A"
            evidence = (item.get("evidence_description") or "").replace("\n", " ").strip()
            notes = (item.get("notes") or "").replace("\n", " ").strip()
            # Truncate long fields for table readability
            if len(evidence) > 80:
                evidence = evidence[:77] + "..."
            if len(notes) > 80:
                notes = notes[:77] + "..."
            if len(automation) > 30:
                automation = automation[:27] + "..."
            sections.append(
                f"| {req_id} | {status} | {automation} | {evidence} | {notes} |"
            )
        sections.append("")

    return "\n".join(sections)


def _build_findings_table(assessments):
    """Build a table of not_satisfied requirements grouped by domain.

    Lists all findings that are not satisfied, ordered by domain then
    requirement ID.
    """
    findings = [
        a for a in assessments if a.get("status") == "not_satisfied"
    ]
    if not findings:
        return "*No findings requiring remediation.*"

    lines = [
        "| Domain | Requirement ID | Evidence | Notes |",
        "|--------|----------------|----------|-------|",
    ]
    for domain in SBD_DOMAINS:
        domain_findings = [f for f in findings if f.get("domain") == domain]
        for f in sorted(domain_findings, key=lambda x: x.get("requirement_id", "")):
            evidence = (f.get("evidence_description") or "").replace("\n", " ").strip()
            notes = (f.get("notes") or "").replace("\n", " ").strip()
            if len(evidence) > 60:
                evidence = evidence[:57] + "..."
            if len(notes) > 60:
                notes = notes[:57] + "..."
            lines.append(
                f"| {domain} | {f.get('requirement_id', 'N/A')} "
                f"| {evidence} | {notes} |"
            )

    return "\n".join(lines)


def _build_remediation_table(assessments):
    """Build table of findings needing remediation with priority.

    Priority is derived from the requirement priority in the catalog.
    Default remediation windows: critical=14 days, high=30 days,
    medium=60 days, low=90 days.
    """
    DEFAULT_WINDOWS = {
        "critical": 14,
        "high": 30,
        "medium": 60,
        "low": 90,
    }

    # Load requirements for priority data
    requirements = _load_sbd_requirements()

    needing_remediation = [
        a for a in assessments
        if a.get("status") in ("not_satisfied", "partially_satisfied")
    ]
    if not needing_remediation:
        return "*No items require remediation at this time.*"

    now = datetime.now(timezone.utc)
    lines = [
        "| Requirement ID | Domain | Current Status | Priority | Target Date | Remediation |",
        "|----------------|--------|----------------|----------|-------------|-------------|",
    ]

    for item in sorted(needing_remediation,
                       key=lambda x: (
                           PRIORITY_ORDER.index(
                               requirements.get(x.get("requirement_id", ""), {}).get("priority", "low")
                           ) if requirements.get(x.get("requirement_id", ""), {}).get("priority", "low") in PRIORITY_ORDER else 99,
                           x.get("domain", ""),
                           x.get("requirement_id", ""),
                       )):
        req_id = item.get("requirement_id", "N/A")
        domain = item.get("domain", "N/A")
        status = item.get("status", "N/A")

        # Get priority from requirements catalog
        req_data = requirements.get(req_id, {})
        priority = req_data.get("priority", "medium")
        title = req_data.get("title", "")

        # Determine target date based on priority
        window_days = DEFAULT_WINDOWS.get(priority, 60)
        target = (now + timedelta(days=window_days)).strftime("%Y-%m-%d")

        # Remediation suggestion
        if status == "not_satisfied":
            remediation = f"Implement {title}" if title else "Full implementation required"
        else:
            remediation = f"Complete {title}" if title else "Complete partial implementation"

        if len(remediation) > 50:
            remediation = remediation[:47] + "..."

        lines.append(
            f"| {req_id} | {domain} | {status} | {priority} | {target} | {remediation} |"
        )

    return "\n".join(lines)


def _build_evidence_summary(assessments):
    """Count evidence artifacts by domain."""
    domain_counts = {domain: {"with_evidence": 0, "without_evidence": 0, "total": 0}
                     for domain in SBD_DOMAINS}

    for a in assessments:
        dom = a.get("domain")
        if dom not in domain_counts:
            continue
        domain_counts[dom]["total"] += 1
        if a.get("evidence_path") or a.get("evidence_description"):
            domain_counts[dom]["with_evidence"] += 1
        else:
            domain_counts[dom]["without_evidence"] += 1

    lines = [
        "| Domain | Total Requirements | With Evidence | Without Evidence | Coverage |",
        "|--------|-------------------:|--------------:|-----------------:|---------:|",
    ]
    for domain in SBD_DOMAINS:
        c = domain_counts[domain]
        if c["total"] == 0:
            continue
        coverage = (
            f"{100.0 * c['with_evidence'] / c['total']:.0f}%"
            if c["total"] > 0 else "N/A"
        )
        lines.append(
            f"| {domain} | {c['total']} | {c['with_evidence']} "
            f"| {c['without_evidence']} | {coverage} |"
        )

    total_all = sum(c["total"] for c in domain_counts.values())
    total_with = sum(c["with_evidence"] for c in domain_counts.values())
    total_without = sum(c["without_evidence"] for c in domain_counts.values())
    total_cov = f"{100.0 * total_with / total_all:.0f}%" if total_all > 0 else "N/A"
    lines.append(
        f"| **Total** | **{total_all}** | **{total_with}** "
        f"| **{total_without}** | **{total_cov}** |"
    )

    return "\n".join(lines)


def _build_nist_mapping(assessments, requirements):
    """Build NIST 800-53 control mapping table.

    Maps each assessed requirement to its corresponding NIST controls
    from the requirements catalog.
    """
    if not requirements:
        return "*NIST control mapping unavailable (requirements catalog not loaded).*"

    # Collect unique requirement IDs from assessments
    assessed_reqs = set()
    assessment_map = {}
    for a in assessments:
        req_id = a.get("requirement_id")
        if req_id:
            assessed_reqs.add(req_id)
            assessment_map[req_id] = a

    if not assessed_reqs:
        return "*No assessed requirements to map.*"

    lines = [
        "| Requirement ID | Domain | NIST Controls | Status |",
        "|----------------|--------|---------------|--------|",
    ]

    for req_id in sorted(assessed_reqs):
        req_data = requirements.get(req_id, {})
        domain = req_data.get("domain", "N/A")
        nist_controls = req_data.get("nist_controls", [])
        nist_str = ", ".join(nist_controls) if nist_controls else "N/A"
        status = assessment_map.get(req_id, {}).get("status", "not_assessed")
        lines.append(
            f"| {req_id} | {domain} | {nist_str} | {status} |"
        )

    return "\n".join(lines)


def _build_auto_check_results(assessments, requirements):
    """Build table of automated check results.

    Filters for requirements with automation_level 'auto' and shows
    their automation_result field from the assessment.
    """
    # Identify which requirements are auto-checkable
    auto_req_ids = set()
    for req_id, req_data in requirements.items():
        if req_data.get("automation_level") == "auto":
            auto_req_ids.add(req_id)

    auto_assessments = [
        a for a in assessments
        if a.get("requirement_id") in auto_req_ids
    ]

    if not auto_assessments:
        return "*No automated check results available.*"

    lines = [
        "| Requirement ID | Domain | Status | Automation Result |",
        "|----------------|--------|--------|-------------------|",
    ]
    for a in sorted(auto_assessments, key=lambda x: x.get("requirement_id", "")):
        req_id = a.get("requirement_id", "N/A")
        domain = a.get("domain", "N/A")
        status = a.get("status", "not_assessed")
        result = (a.get("automation_result") or "N/A").replace("\n", " ").strip()
        if len(result) > 60:
            result = result[:57] + "..."
        lines.append(
            f"| {req_id} | {domain} | {status} | {result} |"
        )

    return "\n".join(lines)


def _build_manual_review_items(assessments, requirements):
    """Build table of requirements needing manual review.

    Filters for requirements with automation_level 'semi' or 'manual'.
    """
    # Identify which requirements need manual/semi review
    manual_req_ids = set()
    for req_id, req_data in requirements.items():
        if req_data.get("automation_level") in ("semi", "manual"):
            manual_req_ids.add(req_id)

    manual_assessments = [
        a for a in assessments
        if a.get("requirement_id") in manual_req_ids
    ]

    if not manual_assessments:
        return "*No manual review items.*"

    lines = [
        "| Requirement ID | Domain | Automation Level | Status | Notes |",
        "|----------------|--------|------------------|--------|-------|",
    ]
    for a in sorted(manual_assessments, key=lambda x: x.get("requirement_id", "")):
        req_id = a.get("requirement_id", "N/A")
        domain = a.get("domain", "N/A")
        status = a.get("status", "not_assessed")
        req_data = requirements.get(req_id, {})
        auto_level = req_data.get("automation_level", "manual")
        notes = (a.get("notes") or "").replace("\n", " ").strip()
        if len(notes) > 60:
            notes = notes[:57] + "..."
        lines.append(
            f"| {req_id} | {domain} | {auto_level} | {status} | {notes} |"
        )

    return "\n".join(lines)


def _build_executive_summary(overall_score, overall_status, gate_result,
                             domain_scores, cisa_status, assessments,
                             requirements):
    """Build the executive summary paragraph.

    Provides a high-level overview of the assessment results including
    key metrics, gate status, and notable findings.
    """
    total_assessed = len(assessments)
    total_satisfied = sum(1 for a in assessments if a.get("status") == "satisfied")
    total_not_satisfied = sum(1 for a in assessments if a.get("status") == "not_satisfied")
    total_partial = sum(1 for a in assessments if a.get("status") == "partially_satisfied")
    total_na = sum(1 for a in assessments if a.get("status") == "not_applicable")
    total_not_assessed = sum(1 for a in assessments if a.get("status") == "not_assessed")

    # Count domains with assessments
    domains_with_data = sum(
        1 for d in domain_scores.values() if d.get("total", 0) > 0
    )

    # Count critical not_satisfied
    critical_not_satisfied = 0
    for a in assessments:
        if a.get("status") == "not_satisfied":
            req_data = requirements.get(a.get("requirement_id", ""), {})
            if req_data.get("priority") == "critical":
                critical_not_satisfied += 1

    # Count CISA commitments by status
    cisa_compliant = sum(1 for c in cisa_status if c["status"] == "Compliant")
    cisa_total = len(cisa_status)

    # Identify weakest domain
    scored_domains = {
        d: s for d, s in domain_scores.items()
        if s.get("total", 0) > 0 and s.get("total", 0) != s.get("not_applicable", 0)
    }
    weakest_domain = ""
    weakest_score = 100.0
    for d, s in scored_domains.items():
        if s["score"] < weakest_score:
            weakest_score = s["score"]
            weakest_domain = d

    lines = []
    lines.append(
        f"This Secure by Design assessment evaluated {total_assessed} requirements "
        f"across {domains_with_data} domains. The overall score is **{overall_score:.1f}%** "
        f"with a gate result of **{gate_result}**."
    )
    lines.append("")
    lines.append(
        f"- **{total_satisfied}** requirements satisfied, "
        f"**{total_partial}** partially satisfied, "
        f"**{total_not_satisfied}** not satisfied, "
        f"**{total_not_assessed}** not assessed, "
        f"**{total_na}** not applicable."
    )
    lines.append(
        f"- **{cisa_compliant}/{cisa_total}** CISA Secure by Design commitments are compliant."
    )
    if critical_not_satisfied > 0:
        lines.append(
            f"- **{critical_not_satisfied} critical-priority requirement(s) not satisfied** "
            f"-- immediate remediation required."
        )
    if weakest_domain:
        lines.append(
            f"- Weakest domain: **{weakest_domain}** ({weakest_score:.1f}%)."
        )

    return "\n".join(lines), critical_not_satisfied


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
    """Log an audit trail event for SbD report generation."""
    try:
        conn.execute(
            """INSERT INTO audit_trail
               (project_id, event_type, actor, action, details,
                affected_files, classification)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (
                project_id,
                "sbd_report_generated",
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

def generate_sbd_report(project_id, output_path=None, db_path=None):
    """Generate a Secure by Design assessment report for a project.

    Args:
        project_id: The project identifier.
        output_path: Override output directory or file path.
        db_path: Override database path.

    Returns:
        dict with ``output_file`` path and metadata about the generated report.
    """
    conn = _get_connection(db_path)
    try:
        # 1. Load project data
        project = _get_project_data(conn, project_id)
        project_name = project.get("name", project_id)

        # 2. Load template (with fallback)
        template = _load_template()

        # 3. Query sbd_assessments
        assessments = _get_sbd_assessments(conn, project_id)

        # Cross-reference data for enrichment
        stig_findings = _get_stig_findings(conn, project_id)
        sbom_records = _get_sbom_records(conn, project_id)

        # 4. Load requirements catalog for CISA commitment mapping
        requirements = _load_sbd_requirements()

        # 5. Calculate domain scores, CISA status, overall status
        domain_scores = _calculate_domain_scores(assessments)
        overall_score, overall_status = _calculate_overall_status(domain_scores)
        cisa_status = _calculate_cisa_commitment_status(assessments, requirements)

        # Determine gate result: PASS if 0 critical-priority reqs are not_satisfied
        critical_not_sat = 0
        for a in assessments:
            if a.get("status") == "not_satisfied":
                req_data = requirements.get(a.get("requirement_id", ""), {})
                if req_data.get("priority") == "critical":
                    critical_not_sat += 1
        gate_result = "PASS" if critical_not_sat == 0 else "FAIL"

        # 6. Build all section content
        domain_scores_table = _build_domain_scores_table(domain_scores)
        cisa_commitment_table = _build_cisa_commitment_table(cisa_status)
        domain_details = _build_domain_details(assessments, domain_scores)
        findings_table = _build_findings_table(assessments)
        remediation_table = _build_remediation_table(assessments)
        evidence_summary = _build_evidence_summary(assessments)
        nist_mapping = _build_nist_mapping(assessments, requirements)
        auto_check_results = _build_auto_check_results(assessments, requirements)
        manual_review_items = _build_manual_review_items(assessments, requirements)
        executive_summary, critical_not_satisfied = _build_executive_summary(
            overall_score, overall_status, gate_result,
            domain_scores, cisa_status, assessments, requirements,
        )

        # Count domains with data
        domains_assessed = sum(
            1 for d in domain_scores.values() if d.get("total", 0) > 0
        )

        # Load CUI config for banner variables
        cui_config = _load_cui_config()

        # Determine version number by counting existing SbD audit events
        report_count_row = conn.execute(
            """SELECT COUNT(*) as cnt FROM audit_trail
               WHERE project_id = ? AND event_type = 'sbd_report_generated'""",
            (project_id,),
        ).fetchone()
        report_count = report_count_row["cnt"] if report_count_row else 0
        new_version = f"{report_count + 1}.0"

        now = datetime.now(timezone.utc)

        # Determine assessor from most recent assessment
        assessor = "icdev-compliance-engine"
        if assessments:
            assessor = assessments[0].get("assessor", assessor)

        # 7. Create substitution dict with all {{variables}}
        variables = {
            # Project info
            "project_name": project_name,
            "project_id": project_id,
            "classification": project.get("classification", "CUI"),

            # Report metadata
            "version": new_version,
            "report_version": new_version,
            "assessment_date": now.strftime("%Y-%m-%d"),
            "date_prepared": now.strftime("%Y-%m-%d"),
            "assessor": assessor,
            "generation_timestamp": now.strftime("%Y-%m-%d %H:%M UTC"),
            "icdev_version": "1.0",

            # Overall scores
            "overall_score": f"{overall_score:.1f}",
            "overall_status": overall_status,
            "gate_result": gate_result,
            "domains_assessed": str(domains_assessed),
            "critical_not_satisfied": str(critical_not_satisfied),

            # Executive summary
            "executive_summary": executive_summary,

            # CISA commitments
            "cisa_commitment_table": cisa_commitment_table,

            # Domain scores
            "domain_scores_table": domain_scores_table,

            # Domain details
            "domain_details": domain_details,

            # Auto-check and manual review
            "auto_check_results": auto_check_results,
            "manual_review_items": manual_review_items,

            # Findings and remediation
            "critical_findings": findings_table,
            "findings_table": findings_table,
            "remediation_table": remediation_table,

            # Evidence
            "evidence_summary": evidence_summary,

            # NIST mapping
            "nist_control_mapping": nist_mapping,

            # Assessment counts
            "total_assessments": str(len(assessments)),
            "assessments_satisfied": str(sum(
                1 for a in assessments if a.get("status") == "satisfied"
            )),
            "assessments_not_satisfied": str(sum(
                1 for a in assessments if a.get("status") == "not_satisfied"
            )),
            "assessments_partial": str(sum(
                1 for a in assessments if a.get("status") == "partially_satisfied"
            )),
            "assessments_na": str(sum(
                1 for a in assessments if a.get("status") == "not_applicable"
            )),
            "assessments_not_assessed": str(sum(
                1 for a in assessments if a.get("status") == "not_assessed"
            )),
            "assessments_risk_accepted": str(sum(
                1 for a in assessments if a.get("status") == "risk_accepted"
            )),

            # Cross-reference data
            "stig_findings_count": str(sum(r.get("cnt", 0) for r in stig_findings)),
            "sbom_records_count": str(len(sbom_records)),
            "sbom_latest_date": (
                sbom_records[0].get("generated_at", "N/A") if sbom_records else "N/A"
            ),

            # CUI banners
            "cui_banner_top": cui_config.get(
                "document_header", cui_config.get("banner_top", "CUI // SP-CTI")
            ),
            "cui_banner_bottom": cui_config.get(
                "document_footer", cui_config.get("banner_bottom", "CUI // SP-CTI")
            ),
        }

        # Per-domain score variables (e.g., authentication_score, etc.)
        for domain in SBD_DOMAINS:
            key_prefix = domain.lower().replace(" ", "_")
            s = domain_scores.get(domain, {})
            variables[f"{key_prefix}_score"] = f"{s.get('score', 0.0):.1f}"
            variables[f"{key_prefix}_total"] = str(s.get("total", 0))
            variables[f"{key_prefix}_satisfied"] = str(s.get("satisfied", 0))
            variables[f"{key_prefix}_not_satisfied"] = str(s.get("not_satisfied", 0))
            variables[f"{key_prefix}_partial"] = str(s.get("partially_satisfied", 0))
            variables[f"{key_prefix}_na"] = str(s.get("not_applicable", 0))

        # Per-CISA commitment variables
        for c in cisa_status:
            num = c["commitment_num"]
            variables[f"cisa_{num}_status"] = c["status"]
            variables[f"cisa_{num}_title"] = c["title"]
            variables[f"cisa_{num}_count"] = str(c["count"])
            variables[f"cisa_{num}_satisfied"] = str(c["satisfied_count"])

        # 8. Apply regex substitution
        report_content = _substitute_variables(template, variables)

        # 9. Apply CUI markings (header/footer banners)
        report_content = _apply_cui_markings(report_content, cui_config)

        # 10. Determine output path
        if output_path:
            out_path = Path(output_path)
            if out_path.is_dir() or str(output_path).endswith("/") or str(output_path).endswith("\\"):
                out_dir = out_path
                out_file = out_dir / f"sbd-report-v{new_version}.md"
            else:
                out_file = out_path
        else:
            dir_path = project.get("directory_path", "")
            if dir_path:
                out_dir = Path(dir_path) / "compliance"
            else:
                out_dir = BASE_DIR / "projects" / project_name / "compliance"
            out_file = out_dir / f"sbd-report-v{new_version}.md"

        out_file.parent.mkdir(parents=True, exist_ok=True)

        # 11. Write file
        with open(out_file, "w", encoding="utf-8") as f:
            f.write(report_content)

        # 12. Log audit event
        audit_details = {
            "version": new_version,
            "overall_score": overall_score,
            "overall_status": overall_status,
            "gate_result": gate_result,
            "domains_assessed": domains_assessed,
            "total_assessments": len(assessments),
            "critical_not_satisfied": critical_not_satisfied,
            "cisa_commitments_compliant": sum(
                1 for c in cisa_status if c["status"] == "Compliant"
            ),
            "stig_findings": sum(r.get("cnt", 0) for r in stig_findings),
            "sbom_records": len(sbom_records),
            "output_file": str(out_file),
        }
        _log_audit_event(
            conn, project_id,
            f"SbD report v{new_version} generated",
            audit_details,
            out_file,
        )

        # Print summary
        print("SbD assessment report generated successfully:")
        print(f"  File:                  {out_file}")
        print(f"  Version:               {new_version}")
        print(f"  Project:               {project_name}")
        print(f"  Overall Score:         {overall_score:.1f}%")
        print(f"  Overall Status:        {overall_status}")
        print(f"  Gate Result:           {gate_result}")
        print(f"  Domains Assessed:      {domains_assessed} / {len(SBD_DOMAINS)}")
        print(f"  Total Assessments:     {len(assessments)}")
        print(f"  Critical Not Satisfied:{critical_not_satisfied}")
        print(f"  CISA Commitments:      {sum(1 for c in cisa_status if c['status'] == 'Compliant')}/7 Compliant")

        # 13. Return output metadata
        return {
            "output_file": str(out_file),
            "version": new_version,
            "project_id": project_id,
            "project_name": project_name,
            "overall_score": overall_score,
            "overall_status": overall_status,
            "gate_result": gate_result,
            "domain_scores": {
                domain: domain_scores[domain]["score"] for domain in SBD_DOMAINS
                if domain_scores[domain]["total"] > 0
            },
            "cisa_status": [
                {
                    "commitment": c["commitment_num"],
                    "title": c["title"],
                    "status": c["status"],
                }
                for c in cisa_status
            ],
            "domains_assessed": domains_assessed,
            "total_assessments": len(assessments),
            "critical_not_satisfied": critical_not_satisfied,
            "generated_at": now.isoformat(),
        }

    finally:
        conn.close()


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Generate SbD assessment report"
    )
    parser.add_argument("--project-id", required=True, help="Project ID")
    parser.add_argument("--output-dir", help="Output directory")
    parser.add_argument(
        "--db-path", type=Path, default=DB_PATH, help="Database path"
    )
    parser.add_argument(
        "--format", choices=["text", "json"], default="text",
        help="Output format: text (default) or json"
    )
    parser.add_argument("--json", action="store_true", dest="json_output", help="JSON output")
    args = parser.parse_args()

    try:
        result = generate_sbd_report(
            args.project_id, args.output_dir, args.db_path
        )
        if args.format == "json":
            print(json.dumps(result, indent=2))
        else:
            print(f"\nSbD report generated: {result['output_file']}")
    except (FileNotFoundError, ValueError) as e:
        print(f"ERROR: {e}", file=sys.stderr)
        sys.exit(1)
