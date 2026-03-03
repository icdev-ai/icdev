#!/usr/bin/env python3
# CUI // SP-CTI
# Controlled Unclassified Information // Specified -- Controlled Technical Information
# Distribution: Distribution D -- Authorized DoD Personnel Only
# POC: ICDEV System Administrator
"""CMMC Level 2/3 assessment report generator.

Loads cmmc_report_template.md, queries cmmc_assessments table, builds domain scores
and practice status breakdowns, generates a comprehensive CMMC assessment report with
CUI markings, gap analysis, NIST 800-171 cross-reference, and gate evaluation.

Usage:
    python tools/compliance/cmmc_report_generator.py --project-id proj-123 --level 2
    python tools/compliance/cmmc_report_generator.py --project-id proj-123 --level 3 \\
        --output-path /path/to/output
    python tools/compliance/cmmc_report_generator.py --project-id proj-123 --level 2 --json

Databases:
    - data/icdev.db: cmmc_assessments, projects, audit_trail

See also:
    - tools/compliance/cmmc_assessor.py (assessment engine)
    - tools/compliance/crosswalk_engine.py (inherit NIST implementations)
    - context/compliance/cmmc_practices.json (practice catalog)
    - context/compliance/cmmc_report_template.md (report template)
"""

import argparse
import json
import re
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent.parent
DB_PATH = BASE_DIR / "data" / "icdev.db"
CMMC_TEMPLATE_PATH = BASE_DIR / "context" / "compliance" / "cmmc_report_template.md"
CMMC_PRACTICES_PATH = BASE_DIR / "context" / "compliance" / "cmmc_practices.json"

# 14 CMMC domains as defined in CMMC v2.0
CMMC_DOMAINS = [
    {"code": "AC", "name": "Access Control"},
    {"code": "AT", "name": "Awareness & Training"},
    {"code": "AU", "name": "Audit & Accountability"},
    {"code": "CM", "name": "Configuration Management"},
    {"code": "IA", "name": "Identification & Authentication"},
    {"code": "IR", "name": "Incident Response"},
    {"code": "MA", "name": "Maintenance"},
    {"code": "MP", "name": "Media Protection"},
    {"code": "PE", "name": "Physical Protection"},
    {"code": "PS", "name": "Personnel Security"},
    {"code": "RA", "name": "Risk Assessment"},
    {"code": "RE", "name": "Recovery"},
    {"code": "SC", "name": "System & Communications Protection"},
    {"code": "SI", "name": "System & Information Integrity"},
]

CMMC_DOMAIN_CODES = [d["code"] for d in CMMC_DOMAINS]
CMMC_DOMAIN_NAMES = {d["code"]: d["name"] for d in CMMC_DOMAINS}

# Valid practice statuses from the cmmc_assessments table
PRACTICE_STATUSES = ["met", "not_met", "partially_met", "not_applicable", "not_assessed"]


# ---------------------------------------------------------------------------
# Helper functions
# ---------------------------------------------------------------------------

def _get_connection(db_path=None):
    """Get a database connection with Row factory."""
    path = db_path or DB_PATH
    if not Path(path).exists():
        raise FileNotFoundError(
            f"Database not found: {path}\n"
            "Run: python tools/db/init_icdev_db.py"
        )
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    return conn


def _load_template(template_path=None):
    """Load the CMMC report template markdown.

    If the template file does not exist a minimal built-in template is
    returned so the generator can still produce a useful report.
    """
    path = template_path or CMMC_TEMPLATE_PATH
    if path.exists():
        with open(path, "r", encoding="utf-8") as f:
            return f.read()

    # Fallback minimal template when file is missing
    return _builtin_template()


def _builtin_template():
    """Return a minimal built-in CMMC report template."""
    return (
        "{{cui_banner_top}}\n\n"
        "# CMMC Level {{level}} Assessment Report\n\n"
        "**System Name:** {{system_name}}\n"
        "**Project ID:** {{project_id}}\n"
        "**Impact Level:** {{impact_level}}\n"
        "**CMMC Level:** {{cmmc_level}}\n"
        "**Assessment Date:** {{assessment_date}}\n"
        "**Report Version:** {{version}}\n"
        "**Assessor:** {{assessor}}\n\n"
        "---\n\n"
        "## 1. Executive Summary\n\n"
        "**Overall Readiness Score:** {{overall_score}}%\n"
        "**Gate Result:** {{gate_result}}\n"
        "**Domains Assessed:** {{domains_assessed}} / 14\n"
        "**Practices Not Met:** {{practices_not_met}}\n\n"
        "{{executive_summary}}\n\n"
        "## 2. Assessment Summary\n\n"
        "| Status | Count |\n"
        "|--------|------:|\n"
        "| Met | {{practices_met_count}} |\n"
        "| Not Met | {{practices_not_met_count}} |\n"
        "| Partially Met | {{practices_partially_met_count}} |\n"
        "| Not Applicable | {{practices_na_count}} |\n"
        "| Not Assessed | {{practices_not_assessed_count}} |\n\n"
        "## 3. Domain Analysis\n\n"
        "{{domain_scores_table}}\n\n"
        "## 4. Gap Analysis\n\n"
        "{{gap_analysis}}\n\n"
        "## 5. NIST 800-171 Alignment\n\n"
        "{{nist_171_mapping}}\n\n"
        "## 6. Readiness Score\n\n"
        "{{readiness_by_domain}}\n\n"
        "## 7. Gate Evaluation\n\n"
        "{{gate_details}}\n\n"
        "## 8. Evidence References\n\n"
        "{{evidence_references}}\n\n"
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


def _load_cmmc_practices():
    """Load the CMMC practices catalog for reference data.

    Returns a dict keyed by practice ID with full practice metadata
    including domain, level, nist_800_171_id, nist_800_53_controls, etc.
    Falls back to an empty dict if the file is unavailable.
    """
    path = CMMC_PRACTICES_PATH
    if not path.exists():
        return {}

    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        practices = {}
        for practice in data.get("practices", []):
            practices[practice["id"]] = practice
        return practices
    except (json.JSONDecodeError, KeyError, TypeError) as exc:
        print(f"Warning: Could not load CMMC practices catalog: {exc}", file=sys.stderr)
        return {}


# ---------------------------------------------------------------------------
# Data retrieval
# ---------------------------------------------------------------------------

def _get_cmmc_assessments(conn, project_id, level=2):
    """Retrieve all CMMC assessment results for a project filtered by level."""
    rows = conn.execute(
        """SELECT * FROM cmmc_assessments
           WHERE project_id = ? AND level <= ?
           ORDER BY domain, practice_id""",
        (project_id, level),
    ).fetchall()
    return [dict(r) for r in rows]


# ---------------------------------------------------------------------------
# Score calculation
# ---------------------------------------------------------------------------

def _calculate_domain_scores(assessments):
    """Calculate a compliance score for each CMMC domain.

    Score formula:
        score = 100 * (met + partially_met * 0.5) / assessable_count
        (assessable_count = total - not_applicable)

    Returns:
        dict mapping domain code to a dict with score, total, and
        per-status counts.
    """
    domain_data = {code: [] for code in CMMC_DOMAIN_CODES}
    for a in assessments:
        dom = a.get("domain")
        if dom in domain_data:
            domain_data[dom].append(a)

    results = {}
    for code in CMMC_DOMAIN_CODES:
        items = domain_data[code]
        total = len(items)
        if total == 0:
            results[code] = {
                "score": 0.0,
                "total": 0,
                "met": 0,
                "not_met": 0,
                "partially_met": 0,
                "not_applicable": 0,
                "not_assessed": 0,
            }
            continue

        met = sum(1 for i in items if i["status"] == "met")
        not_met = sum(1 for i in items if i["status"] == "not_met")
        partially_met = sum(1 for i in items if i["status"] == "partially_met")
        not_applicable = sum(1 for i in items if i["status"] == "not_applicable")
        not_assessed = sum(1 for i in items if i["status"] == "not_assessed")

        # Denominator excludes not_applicable
        assessable = total - not_applicable
        if assessable > 0:
            score = 100.0 * (met + partially_met * 0.5) / assessable
        else:
            score = 100.0  # All N/A means fully compliant for this domain

        results[code] = {
            "score": round(score, 1),
            "total": total,
            "met": met,
            "not_met": not_met,
            "partially_met": partially_met,
            "not_applicable": not_applicable,
            "not_assessed": not_assessed,
        }

    return results


def _calculate_overall_score(domain_scores):
    """Compute overall readiness score from domain scores.

    Returns:
        tuple of (overall_score, overall_status_label)
    """
    scoreable = [v for v in domain_scores.values() if v["total"] > 0]
    if not scoreable:
        return 0.0, "Not Ready"

    overall = sum(d["score"] for d in scoreable) / len(scoreable)
    overall = round(overall, 1)

    if overall >= 90:
        status = "Ready"
    elif overall >= 70:
        status = "Conditionally Ready"
    elif overall >= 50:
        status = "Partially Ready"
    else:
        status = "Not Ready"

    return overall, status


# ---------------------------------------------------------------------------
# Section builder functions
# ---------------------------------------------------------------------------

def _build_domain_scores_table(domain_scores):
    """Build a markdown table summarising per-domain scores."""
    lines = [
        "| Domain | Code | Score | Met | Not Met | Partial | N/A | Not Assessed |",
        "|--------|------|------:|----:|--------:|--------:|----:|-------------:|",
    ]
    for dom in CMMC_DOMAINS:
        code = dom["code"]
        name = dom["name"]
        s = domain_scores.get(code, {})
        if s.get("total", 0) == 0:
            continue
        lines.append(
            f"| {name} | {code} | {s.get('score', 0.0):.1f}% "
            f"| {s.get('met', 0)} "
            f"| {s.get('not_met', 0)} "
            f"| {s.get('partially_met', 0)} "
            f"| {s.get('not_applicable', 0)} "
            f"| {s.get('not_assessed', 0)} |"
        )

    return "\n".join(lines)


def _build_domain_details(assessments, domain_scores):
    """Build markdown detail sections for each assessed domain.

    Each domain gets a sub-heading and a table listing every practice
    with its status, evidence description, and notes.
    """
    domain_data = {code: [] for code in CMMC_DOMAIN_CODES}
    for a in assessments:
        dom = a.get("domain")
        if dom in domain_data:
            domain_data[dom].append(a)

    sections = []
    for dom in CMMC_DOMAINS:
        code = dom["code"]
        name = dom["name"]
        items = domain_data[code]
        s = domain_scores.get(code, {})
        score = s.get("score", 0.0)

        # Skip domains with no assessments
        if not items and s.get("total", 0) == 0:
            continue

        sections.append(f"#### {code} - {name} ({score:.1f}%)")
        sections.append("")

        if not items:
            sections.append("*No assessments recorded for this domain.*")
            sections.append("")
            continue

        sections.append(
            "| Practice ID | Status | NIST 171 | Evidence | Notes |"
        )
        sections.append(
            "|-------------|--------|----------|----------|-------|"
        )
        for item in sorted(items, key=lambda x: x.get("practice_id", "")):
            practice_id = item.get("practice_id", "N/A")
            status = item.get("status", "not_assessed")
            nist_id = item.get("nist_171_id", "") or "N/A"
            evidence = (item.get("evidence_description") or "").replace("\n", " ").strip()
            notes = (item.get("notes") or "").replace("\n", " ").strip()
            # Truncate long fields for table readability
            if len(evidence) > 80:
                evidence = evidence[:77] + "..."
            if len(notes) > 80:
                notes = notes[:77] + "..."
            sections.append(
                f"| {practice_id} | {status} | {nist_id} | {evidence} | {notes} |"
            )
        sections.append("")

    return "\n".join(sections)


def _build_gap_analysis(assessments, practices_catalog):
    """Build a table of not_met practices requiring remediation.

    Lists all practices with status not_met, ordered by domain then
    practice ID, with priority and evidence required from the catalog.
    """
    gaps = [a for a in assessments if a.get("status") == "not_met"]
    if not gaps:
        return "*No gaps identified. All assessed practices are met or not applicable.*"

    lines = [
        "| Practice ID | Domain | Title | Priority | Evidence Required |",
        "|-------------|--------|-------|----------|-------------------|",
    ]
    for code in CMMC_DOMAIN_CODES:
        domain_gaps = [g for g in gaps if g.get("domain") == code]
        for g in sorted(domain_gaps, key=lambda x: x.get("practice_id", "")):
            practice_id = g.get("practice_id", "N/A")
            domain_name = CMMC_DOMAIN_NAMES.get(code, code)
            catalog_entry = practices_catalog.get(practice_id, {})
            title = catalog_entry.get("title", "")
            priority = catalog_entry.get("priority", "medium")
            evidence_req = catalog_entry.get("evidence_required", "")
            if len(title) > 40:
                title = title[:37] + "..."
            if len(evidence_req) > 50:
                evidence_req = evidence_req[:47] + "..."
            lines.append(
                f"| {practice_id} | {domain_name} | {title} | {priority} | {evidence_req} |"
            )

    return "\n".join(lines)


def _build_nist_171_mapping(assessments, practices_catalog):
    """Build NIST 800-171 cross-reference table.

    Maps each assessed CMMC practice to its corresponding NIST 800-171 identifier
    and NIST 800-53 controls from the practices catalog.
    """
    if not practices_catalog:
        return "*NIST 800-171 mapping unavailable (practices catalog not loaded).*"

    assessed_ids = set()
    assessment_map = {}
    for a in assessments:
        pid = a.get("practice_id")
        if pid:
            assessed_ids.add(pid)
            assessment_map[pid] = a

    if not assessed_ids:
        return "*No assessed practices to map.*"

    lines = [
        "| Practice ID | Domain | NIST 800-171 | NIST 800-53 | Status |",
        "|-------------|--------|--------------|-------------|--------|",
    ]

    for pid in sorted(assessed_ids):
        catalog = practices_catalog.get(pid, {})
        domain_code = catalog.get("domain_code", "N/A")
        nist_171 = catalog.get("nist_800_171_id", "N/A") or "N/A"
        nist_53 = catalog.get("nist_800_53_controls", [])
        nist_53_str = ", ".join(nist_53) if nist_53 else "N/A"
        status = assessment_map.get(pid, {}).get("status", "not_assessed")
        lines.append(
            f"| {pid} | {domain_code} | {nist_171} | {nist_53_str} | {status} |"
        )

    return "\n".join(lines)


def _build_readiness_by_domain(domain_scores):
    """Build per-domain readiness score rows for the readiness table."""
    lines = []
    for dom in CMMC_DOMAINS:
        code = dom["code"]
        name = dom["name"]
        s = domain_scores.get(code, {})
        if s.get("total", 0) == 0:
            continue
        score = s.get("score", 0.0)
        if score >= 90:
            status = "Ready"
        elif score >= 70:
            status = "Conditionally Ready"
        elif score >= 50:
            status = "Partially Ready"
        else:
            status = "Not Ready"
        lines.append(f"| {name} ({code}) | {score:.1f}% | {status} |")

    return "\n".join(lines)


def _build_gate_details(assessments, level, gate_result):
    """Build gate evaluation detail paragraph."""
    not_met = [a for a in assessments if a.get("status") == "not_met"]
    partially_met = [a for a in assessments if a.get("status") == "partially_met"]
    not_assessed = [a for a in assessments if a.get("status") == "not_assessed"]

    lines = []
    if gate_result == "PASS":
        lines.append(
            f"All CMMC Level {level} practices are either met, partially met, "
            "not applicable, or not yet assessed. The system meets the minimum "
            "gate criteria for CMMC certification readiness."
        )
    else:
        lines.append(
            f"**{len(not_met)} practice(s) have a status of not_met.** "
            "These must be remediated before CMMC certification can proceed."
        )

    if partially_met:
        lines.append(
            f"\n**Note:** {len(partially_met)} practice(s) are partially met. "
            "While these do not block the gate, they should be fully implemented "
            "prior to formal C3PAO assessment."
        )

    if not_assessed:
        lines.append(
            f"\n**Note:** {len(not_assessed)} practice(s) have not been assessed. "
            "All practices must be evaluated before requesting formal assessment."
        )

    return "\n".join(lines)


def _build_recommendations(assessments, domain_scores, practices_catalog):
    """Build prioritized recommendations based on gaps and scores."""
    not_met = [a for a in assessments if a.get("status") == "not_met"]
    partially_met = [a for a in assessments if a.get("status") == "partially_met"]
    not_assessed = [a for a in assessments if a.get("status") == "not_assessed"]

    lines = []

    # Priority 1: Critical not_met practices
    critical_gaps = []
    for a in not_met:
        pid = a.get("practice_id", "")
        catalog = practices_catalog.get(pid, {})
        if catalog.get("priority") == "critical":
            critical_gaps.append(pid)

    if critical_gaps:
        lines.append("### Priority 1: Critical Practices Not Met")
        lines.append("")
        lines.append(
            "The following critical practices must be remediated immediately:"
        )
        for pid in sorted(critical_gaps):
            title = practices_catalog.get(pid, {}).get("title", "")
            lines.append(f"- **{pid}**: {title}")
        lines.append("")

    # Priority 2: Weakest domains
    weak_domains = []
    for dom in CMMC_DOMAINS:
        code = dom["code"]
        s = domain_scores.get(code, {})
        if s.get("total", 0) > 0 and s.get("score", 100.0) < 70.0:
            weak_domains.append((code, dom["name"], s["score"]))

    if weak_domains:
        lines.append("### Priority 2: Weak Domains")
        lines.append("")
        lines.append(
            "The following domains scored below 70% and need focused attention:"
        )
        for code, name, score in sorted(weak_domains, key=lambda x: x[2]):
            lines.append(f"- **{name} ({code})**: {score:.1f}%")
        lines.append("")

    # Priority 3: Partially met practices
    if partially_met:
        lines.append("### Priority 3: Complete Partial Implementations")
        lines.append("")
        lines.append(
            f"{len(partially_met)} practice(s) are partially met. "
            "Complete implementation to achieve full compliance."
        )
        lines.append("")

    # Priority 4: Not assessed practices
    if not_assessed:
        lines.append("### Priority 4: Complete Assessment")
        lines.append("")
        lines.append(
            f"{len(not_assessed)} practice(s) have not been assessed. "
            "All practices must be evaluated before requesting C3PAO assessment."
        )
        lines.append("")

    if not lines:
        return "*No recommendations at this time. All practices are met or not applicable.*"

    return "\n".join(lines)


def _build_evidence_references(assessments):
    """Build evidence reference table grouped by domain."""
    domain_counts = {code: {"with_evidence": 0, "without_evidence": 0, "total": 0}
                     for code in CMMC_DOMAIN_CODES}

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
        "| Domain | Total Practices | With Evidence | Without Evidence | Coverage |",
        "|--------|----------------:|--------------:|-----------------:|---------:|",
    ]
    for dom in CMMC_DOMAINS:
        code = dom["code"]
        name = dom["name"]
        c = domain_counts[code]
        if c["total"] == 0:
            continue
        coverage = (
            f"{100.0 * c['with_evidence'] / c['total']:.0f}%"
            if c["total"] > 0 else "N/A"
        )
        lines.append(
            f"| {name} ({code}) | {c['total']} | {c['with_evidence']} "
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


def _build_executive_summary(overall_score, overall_status, gate_result,
                             domain_scores, assessments, practices_catalog):
    """Build the executive summary paragraph."""
    total_assessed = len(assessments)
    total_met = sum(1 for a in assessments if a.get("status") == "met")
    total_not_met = sum(1 for a in assessments if a.get("status") == "not_met")
    total_partial = sum(1 for a in assessments if a.get("status") == "partially_met")
    total_na = sum(1 for a in assessments if a.get("status") == "not_applicable")
    total_not_assessed = sum(1 for a in assessments if a.get("status") == "not_assessed")

    # Count domains with assessments
    domains_with_data = sum(
        1 for d in domain_scores.values() if d.get("total", 0) > 0
    )

    # Count critical not_met
    critical_not_met = 0
    for a in assessments:
        if a.get("status") == "not_met":
            catalog = practices_catalog.get(a.get("practice_id", ""), {})
            if catalog.get("priority") == "critical":
                critical_not_met += 1

    # Identify weakest domain
    scored_domains = {
        code: s for code, s in domain_scores.items()
        if s.get("total", 0) > 0 and s.get("total", 0) != s.get("not_applicable", 0)
    }
    weakest_code = ""
    weakest_score = 100.0
    for code, s in scored_domains.items():
        if s["score"] < weakest_score:
            weakest_score = s["score"]
            weakest_code = code

    lines = []
    lines.append(
        f"This CMMC assessment evaluated {total_assessed} practices "
        f"across {domains_with_data} domains. The overall readiness score is "
        f"**{overall_score:.1f}%** with a gate result of **{gate_result}**."
    )
    lines.append("")
    lines.append(
        f"- **{total_met}** practices met, "
        f"**{total_partial}** partially met, "
        f"**{total_not_met}** not met, "
        f"**{total_not_assessed}** not assessed, "
        f"**{total_na}** not applicable."
    )
    if critical_not_met > 0:
        lines.append(
            f"- **{critical_not_met} critical-priority practice(s) not met** "
            "-- immediate remediation required."
        )
    if weakest_code:
        weakest_name = CMMC_DOMAIN_NAMES.get(weakest_code, weakest_code)
        lines.append(
            f"- Weakest domain: **{weakest_name} ({weakest_code})** ({weakest_score:.1f}%)."
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
    """Log an audit trail event for CMMC report generation."""
    try:
        conn.execute(
            """INSERT INTO audit_trail
               (project_id, event_type, actor, action, details,
                affected_files, classification)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (
                project_id,
                "cmmc_assessed",
                "icdev-compliance-engine",
                action,
                json.dumps(details),
                json.dumps([str(file_path)]),
                "CUI",
            ),
        )
        conn.commit()
    except Exception as exc:
        print(f"Warning: Could not log audit event: {exc}", file=sys.stderr)


# ---------------------------------------------------------------------------
# Main generator
# ---------------------------------------------------------------------------

def generate_cmmc_report(project_id, level=2, output_path=None, db_path=None):
    """Generate a CMMC assessment report for a project.

    Args:
        project_id: The project identifier.
        level: CMMC level (2 or 3). Defaults to 2.
        output_path: Override output directory or file path.
        db_path: Override database path.

    Returns:
        dict with keys: status, output_file, summary, gate_result.
    """
    conn = _get_connection(db_path)
    try:
        # 1. Load project data
        project = _get_project_data(conn, project_id)
        project_name = project.get("name", project_id)
        system_name = project.get("name", project_id)

        # 2. Load template (with fallback)
        template = _load_template()

        # 3. Query cmmc_assessments filtered by level
        assessments = _get_cmmc_assessments(conn, project_id, level)

        # 4. Load practices catalog for cross-reference
        practices_catalog = _load_cmmc_practices()

        # 5. Calculate domain scores and overall score
        domain_scores = _calculate_domain_scores(assessments)
        overall_score, overall_status = _calculate_overall_score(domain_scores)

        # 6. Gate evaluation: PASS if 0 "not_met" practices at this level
        total_not_met = sum(1 for a in assessments if a.get("status") == "not_met")
        gate_result = "PASS" if total_not_met == 0 else "FAIL"

        # Certification readiness label
        if gate_result == "PASS" and overall_score >= 90:
            certification_readiness = "Ready for C3PAO Assessment"
        elif gate_result == "PASS":
            certification_readiness = "Gate passed -- improve score before formal assessment"
        else:
            certification_readiness = "Not ready -- remediate not_met practices first"

        # 7. Build all section content
        domain_scores_table = _build_domain_scores_table(domain_scores)
        domain_details = _build_domain_details(assessments, domain_scores)
        gap_analysis = _build_gap_analysis(assessments, practices_catalog)
        nist_171_mapping = _build_nist_171_mapping(assessments, practices_catalog)
        readiness_by_domain = _build_readiness_by_domain(domain_scores)
        gate_details = _build_gate_details(assessments, level, gate_result)
        recommendations = _build_recommendations(
            assessments, domain_scores, practices_catalog
        )
        evidence_references = _build_evidence_references(assessments)
        executive_summary = _build_executive_summary(
            overall_score, overall_status, gate_result,
            domain_scores, assessments, practices_catalog,
        )

        # Count domains with data
        domains_assessed = sum(
            1 for d in domain_scores.values() if d.get("total", 0) > 0
        )

        # Practice status counts
        total_practices = len(assessments)
        practices_met = sum(1 for a in assessments if a.get("status") == "met")
        practices_not_met = total_not_met
        practices_partial = sum(1 for a in assessments if a.get("status") == "partially_met")
        practices_na = sum(1 for a in assessments if a.get("status") == "not_applicable")
        practices_not_assessed = sum(1 for a in assessments if a.get("status") == "not_assessed")

        # Percentages
        def _pct(count, total):
            return f"{100.0 * count / total:.1f}" if total > 0 else "0.0"

        # Load CUI config for banner variables
        cui_config = _load_cui_config()

        # Assessment type based on level
        assessment_type = "Third-Party (C3PAO)" if level == 2 else "Government-Led (DIBCAC)"

        # Determine version number by counting existing CMMC audit events
        report_count_row = conn.execute(
            """SELECT COUNT(*) as cnt FROM audit_trail
               WHERE project_id = ? AND event_type = 'cmmc_assessed'""",
            (project_id,),
        ).fetchone()
        report_count = report_count_row["cnt"] if report_count_row else 0
        new_version = f"{report_count + 1}.0"

        now = datetime.now(timezone.utc)

        # Determine assessor from most recent assessment
        assessor = "icdev-compliance-engine"
        if assessments:
            assessor = assessments[0].get("assessor", assessor)

        # 8. Create substitution dict with all {{variables}}
        variables = {
            # Project / system info
            "system_name": system_name,
            "project_name": project_name,
            "project_id": project_id,
            "impact_level": project.get("impact_level", "CUI"),
            "classification": project.get("classification", "CUI"),
            "cmmc_level": str(level),
            "level": str(level),
            "assessment_type": assessment_type,

            # Report metadata
            "version": new_version,
            "report_version": new_version,
            "assessment_date": now.strftime("%Y-%m-%d"),
            "date_prepared": now.strftime("%Y-%m-%d"),
            "assessor": assessor,
            "generation_timestamp": now.strftime("%Y-%m-%d %H:%M UTC"),

            # Overall scores
            "overall_score": f"{overall_score:.1f}",
            "overall_status": overall_status,
            "gate_result": gate_result,
            "domains_assessed": str(domains_assessed),
            "certification_readiness": certification_readiness,

            # Practice counts
            "total_practices": str(total_practices),
            "practices_met": str(practices_met),
            "practices_met_count": str(practices_met),
            "practices_met_pct": _pct(practices_met, total_practices),
            "practices_not_met": str(practices_not_met),
            "practices_not_met_count": str(practices_not_met),
            "practices_not_met_pct": _pct(practices_not_met, total_practices),
            "practices_partially_met": str(practices_partial),
            "practices_partially_met_count": str(practices_partial),
            "practices_partially_met_pct": _pct(practices_partial, total_practices),
            "practices_na": str(practices_na),
            "practices_na_count": str(practices_na),
            "practices_na_pct": _pct(practices_na, total_practices),
            "practices_not_assessed": str(practices_not_assessed),
            "practices_not_assessed_count": str(practices_not_assessed),
            "practices_not_assessed_pct": _pct(practices_not_assessed, total_practices),

            # Executive summary
            "executive_summary": executive_summary,

            # Section content
            "domain_scores_table": domain_scores_table,
            "domain_details": domain_details,
            "gap_analysis": gap_analysis,
            "nist_171_mapping": nist_171_mapping,
            "readiness_by_domain": readiness_by_domain,
            "gate_details": gate_details,
            "recommendations": recommendations,
            "evidence_references": evidence_references,

            # CUI banners
            "cui_banner_top": cui_config.get(
                "document_header", cui_config.get("banner_top", "CUI // SP-CTI")
            ),
            "cui_banner_bottom": cui_config.get(
                "document_footer", cui_config.get("banner_bottom", "CUI // SP-CTI")
            ),
        }

        # Per-domain score variables (e.g., ac_score, at_score, etc.)
        for dom in CMMC_DOMAINS:
            code = dom["code"]
            key_prefix = code.lower()
            s = domain_scores.get(code, {})
            variables[f"{key_prefix}_score"] = f"{s.get('score', 0.0):.1f}"
            variables[f"{key_prefix}_total"] = str(s.get("total", 0))
            variables[f"{key_prefix}_met"] = str(s.get("met", 0))
            variables[f"{key_prefix}_not_met"] = str(s.get("not_met", 0))
            variables[f"{key_prefix}_partial"] = str(s.get("partially_met", 0))
            variables[f"{key_prefix}_na"] = str(s.get("not_applicable", 0))

        # 9. Apply regex substitution
        report_content = _substitute_variables(template, variables)

        # 10. Apply CUI markings (header/footer banners)
        report_content = _apply_cui_markings(report_content, cui_config)

        # 11. Determine output path
        if output_path:
            out_path = Path(output_path)
            if out_path.is_dir() or str(output_path).endswith("/") or str(output_path).endswith("\\"):
                out_dir = out_path
                out_file = out_dir / f"cmmc-L{level}-report-v{new_version}.md"
            else:
                out_file = out_path
        else:
            dir_path = project.get("directory_path", "")
            if dir_path:
                out_dir = Path(dir_path) / "compliance"
            else:
                out_dir = BASE_DIR / "projects" / project_name / "compliance"
            out_file = out_dir / f"cmmc-L{level}-report-v{new_version}.md"

        out_file.parent.mkdir(parents=True, exist_ok=True)

        # 12. Write file
        with open(out_file, "w", encoding="utf-8") as f:
            f.write(report_content)

        # 13. Log audit event (event_type='cmmc_assessed')
        audit_details = {
            "version": new_version,
            "level": level,
            "overall_score": overall_score,
            "overall_status": overall_status,
            "gate_result": gate_result,
            "domains_assessed": domains_assessed,
            "total_practices": total_practices,
            "practices_met": practices_met,
            "practices_not_met": practices_not_met,
            "practices_partially_met": practices_partial,
            "practices_not_applicable": practices_na,
            "practices_not_assessed": practices_not_assessed,
            "certification_readiness": certification_readiness,
            "output_file": str(out_file),
        }
        _log_audit_event(
            conn, project_id,
            f"CMMC L{level} report v{new_version} generated",
            audit_details,
            out_file,
        )

        # Print summary
        print("CMMC assessment report generated successfully:")
        print(f"  File:                  {out_file}")
        print(f"  Version:               {new_version}")
        print(f"  Project:               {project_name}")
        print(f"  CMMC Level:            {level}")
        print(f"  Overall Score:         {overall_score:.1f}%")
        print(f"  Overall Status:        {overall_status}")
        print(f"  Gate Result:           {gate_result}")
        print(f"  Domains Assessed:      {domains_assessed} / {len(CMMC_DOMAINS)}")
        print(f"  Total Practices:       {total_practices}")
        print(f"  Practices Met:         {practices_met}")
        print(f"  Practices Not Met:     {practices_not_met}")
        print(f"  Certification Ready:   {certification_readiness}")

        # 14. Build summary dict for return value
        summary = {
            "version": new_version,
            "project_id": project_id,
            "project_name": project_name,
            "level": level,
            "overall_score": overall_score,
            "overall_status": overall_status,
            "domains_assessed": domains_assessed,
            "total_practices": total_practices,
            "practices_met": practices_met,
            "practices_not_met": practices_not_met,
            "practices_partially_met": practices_partial,
            "practices_not_applicable": practices_na,
            "practices_not_assessed": practices_not_assessed,
            "certification_readiness": certification_readiness,
            "domain_scores": {
                code: domain_scores[code]["score"]
                for code in CMMC_DOMAIN_CODES
                if domain_scores[code]["total"] > 0
            },
            "generated_at": now.isoformat(),
        }

        gate_result_dict = {
            "gate": "cmmc_certification",
            "level": level,
            "result": gate_result,
            "practices_not_met": practices_not_met,
            "certification_readiness": certification_readiness,
        }

        # 15. Return structured result
        return {
            "status": "success",
            "output_file": str(out_file),
            "summary": summary,
            "gate_result": gate_result_dict,
        }

    finally:
        conn.close()


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Generate CMMC assessment report"
    )
    parser.add_argument("--project-id", required=True, help="Project ID")
    parser.add_argument(
        "--level", type=int, choices=[2, 3], default=2,
        help="CMMC level (2 or 3, default: 2)"
    )
    parser.add_argument("--output-path", help="Output directory or file path")
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
        result = generate_cmmc_report(
            args.project_id,
            level=args.level,
            output_path=args.output_path,
            db_path=args.db_path,
        )
        if args.format == "json":
            print(json.dumps(result, indent=2))
        else:
            print(f"\nCMMC report generated: {result['output_file']}")
    except (FileNotFoundError, ValueError) as e:
        print(f"ERROR: {e}", file=sys.stderr)
        sys.exit(1)
