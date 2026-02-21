# CUI // SP-CTI
#!/usr/bin/env python3
"""DES compliance report generator.

Queries des_compliance table, groups results by category, loads the report
template from context/mbse/des_report_template.md, performs variable
substitution, applies CUI markings, and writes the output report.

Follows the same pattern as tools/compliance/sbd_report_generator.py.
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
DES_TEMPLATE_PATH = BASE_DIR / "context" / "mbse" / "des_report_template.md"
DES_REQUIREMENTS_PATH = BASE_DIR / "context" / "mbse" / "des_requirements.json"

# Category display names and ordering
CATEGORY_ORDER = [
    ("model_authority", "Model Authority"),
    ("data_management", "Data Management"),
    ("infrastructure", "Infrastructure"),
    ("workforce", "Workforce"),
    ("policy", "Policy"),
    ("lifecycle", "Lifecycle"),
]


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


def _get_project_data(conn, project_id):
    """Load project record from database."""
    row = conn.execute(
        "SELECT * FROM projects WHERE id = ?", (project_id,)
    ).fetchone()
    if not row:
        raise ValueError(f"Project '{project_id}' not found in database.")
    return dict(row)


def _load_template(template_path=None):
    """Load the DES report template markdown.

    Falls back to a built-in minimal template if the file does not exist.
    """
    path = template_path or DES_TEMPLATE_PATH
    if path.exists():
        with open(path, "r", encoding="utf-8") as f:
            return f.read()
    return _builtin_template()


def _builtin_template():
    """Return a minimal built-in DES report template."""
    return (
        "{{cui_banner_top}}\n\n"
        "# Digital Engineering Strategy (DES) Compliance Report\n\n"
        "**Project:** {{project_name}}\n"
        "**Project ID:** {{project_id}}\n"
        "**Classification:** {{classification}}\n"
        "**Assessment Date:** {{assessment_date}}\n"
        "**Report Version:** {{version}}\n"
        "**Assessor:** {{assessor}}\n"
        "**Framework:** DoD Digital Engineering Strategy + DoDI 5000.87\n\n"
        "---\n\n"
        "## 1. Executive Summary\n\n"
        "**Overall DES Score:** {{overall_score}}%\n"
        "**Gate Status:** {{gate_status}}\n"
        "**Categories Assessed:** {{categories_assessed}} / 6\n"
        "**Requirements Compliant:** {{requirements_compliant}} / {{requirements_total}}\n"
        "**Partial Compliance:** {{requirements_partial}}\n"
        "**Non-Compliant:** {{requirements_non_compliant}}\n\n"
        "{{executive_summary}}\n\n"
        "---\n\n"
        "## 2. Assessment Summary\n\n"
        "{{assessment_summary_table}}\n\n"
        "---\n\n"
        "## 3. Category Breakdown\n\n"
        "{{category_details}}\n\n"
        "---\n\n"
        "## 4. Gap Analysis\n\n"
        "{{gap_analysis}}\n\n"
        "---\n\n"
        "## 5. Gate Evaluation\n\n"
        "{{gate_evaluation}}\n\n"
        "---\n\n"
        "## 6. Appendix: Assessment Methodology\n\n"
        "This assessment was conducted using the ICDEV DES Assessment Engine against the "
        "DoDI 5000.87 Digital Engineering Strategy requirements catalog. Requirements span "
        "six categories: model_authority, data_management, infrastructure, workforce, policy, "
        "and lifecycle.\n\n"
        "**Scoring Formula:** Score = 100 x (compliant + partial x 0.5) / (total - not_applicable)\n\n"
        "**Gate Logic:** PASS if 0 non-compliant on critical-priority requirements. "
        "WARN if any partially_compliant on critical. FAIL otherwise.\n\n"
        "---\n\n"
        "**Prepared by:** {{assessor}}\n"
        "**Date:** {{assessment_date}}\n\n"
        "{{cui_banner_bottom}}\n"
    )


def _load_cui_config():
    """Load CUI marking configuration with fallback."""
    try:
        sys.path.insert(0, str(BASE_DIR / "tools" / "compliance"))
        from cui_marker import load_cui_config
        return load_cui_config()
    except ImportError:
        return {
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


def _load_des_requirements():
    """Load the DES requirements catalog for reference data.

    Returns a dict keyed by requirement ID with full metadata.
    Falls back to an empty dict if the file is unavailable.
    """
    path = DES_REQUIREMENTS_PATH
    if not path.exists():
        return {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return {req["id"]: req for req in data.get("requirements", [])}
    except (json.JSONDecodeError, KeyError, TypeError) as e:
        print(f"Warning: Could not load DES requirements catalog: {e}", file=sys.stderr)
        return {}


def _substitute_variables(template, variables):
    """Replace {{variable_name}} placeholders in the template.

    Uses simple regex substitution -- no Jinja2 dependency required.
    Also handles {%...%} Jinja-style blocks by replacing them with
    pre-rendered content from variables.
    """
    def replacer(match):
        key = match.group(1).strip()
        return str(variables.get(key, match.group(0)))

    # First pass: remove Jinja-style block tags that we handle via variables
    # Replace {% for ... %} ... {% endfor %} blocks with variable references
    # These are handled by pre-rendering into the variables dict
    cleaned = re.sub(
        r"\{%\s*for\s+\w+\s+in\s+(\w+)\s*%\}.*?\{%\s*endfor\s*%\}",
        lambda m: variables.get(m.group(1) + "_rendered", ""),
        template,
        flags=re.DOTALL,
    )

    # Second pass: substitute {{variable}} placeholders
    return re.sub(r"\{\{(\w+)\}\}", replacer, cleaned)


def _log_audit_event(conn, project_id, action, details, file_path):
    """Log an audit trail event for DES report generation."""
    try:
        conn.execute(
            """INSERT INTO audit_trail
               (project_id, event_type, actor, action, details,
                affected_files, classification)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (
                project_id,
                "des_report_generated",
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
# Data retrieval
# ---------------------------------------------------------------------------

def _get_des_assessments(conn, project_id):
    """Retrieve all DES compliance results for a project."""
    rows = conn.execute(
        """SELECT * FROM des_compliance
           WHERE project_id = ?
           ORDER BY category, requirement_id""",
        (project_id,),
    ).fetchall()
    return [dict(r) for r in rows]


# ---------------------------------------------------------------------------
# Score calculation
# ---------------------------------------------------------------------------

def _calculate_category_scores(assessments):
    """Calculate compliance score for each DES category.

    Score = 100 * (compliant + partially_compliant * 0.5) / (total - not_applicable)

    Returns:
        dict mapping category key to score dict.
    """
    results = {}
    for cat_key, cat_name in CATEGORY_ORDER:
        items = [a for a in assessments if a.get("category") == cat_key]
        total = len(items)
        compliant = sum(1 for i in items if i["status"] == "compliant")
        partial = sum(1 for i in items if i["status"] == "partially_compliant")
        non_compliant = sum(1 for i in items if i["status"] == "non_compliant")
        not_applicable = sum(1 for i in items if i["status"] == "not_applicable")
        not_assessed = sum(1 for i in items if i["status"] == "not_assessed")

        scoreable = total - not_applicable
        if scoreable > 0:
            score = round(100.0 * (compliant + partial * 0.5) / scoreable, 1)
        else:
            score = 100.0 if total > 0 else 0.0

        results[cat_key] = {
            "name": cat_name,
            "score": score,
            "total": total,
            "compliant": compliant,
            "partially_compliant": partial,
            "non_compliant": non_compliant,
            "not_applicable": not_applicable,
            "not_assessed": not_assessed,
        }
    return results


def _calculate_overall(category_scores):
    """Calculate overall DES score from category scores.

    Returns:
        tuple of (overall_score, overall_status_label)
    """
    scored = [v for v in category_scores.values() if v["total"] > 0]
    if not scored:
        return 0.0, "Non-Compliant"

    overall = sum(d["score"] for d in scored) / len(scored)
    overall = round(overall, 1)

    if overall >= 80:
        status = "Compliant"
    elif overall >= 50:
        status = "Partially Compliant"
    else:
        status = "Non-Compliant"

    return overall, status


# ---------------------------------------------------------------------------
# Section builders
# ---------------------------------------------------------------------------

def _build_assessment_summary_table(assessments):
    """Build a markdown summary table of status counts."""
    total = len(assessments)
    compliant = sum(1 for a in assessments if a["status"] == "compliant")
    partial = sum(1 for a in assessments if a["status"] == "partially_compliant")
    non_compliant = sum(1 for a in assessments if a["status"] == "non_compliant")
    not_applicable = sum(1 for a in assessments if a["status"] == "not_applicable")
    not_assessed = sum(1 for a in assessments if a["status"] == "not_assessed")

    def pct(n):
        return f"{100.0 * n / total:.1f}" if total > 0 else "0.0"

    lines = [
        "| Status | Count | Percentage |",
        "|--------|------:|----------:|",
        f"| Compliant | {compliant} | {pct(compliant)}% |",
        f"| Partially Compliant | {partial} | {pct(partial)}% |",
        f"| Non-Compliant | {non_compliant} | {pct(non_compliant)}% |",
        f"| Not Applicable | {not_applicable} | {pct(not_applicable)}% |",
        f"| Not Assessed | {not_assessed} | {pct(not_assessed)}% |",
        f"| **Total** | **{total}** | **100%** |",
    ]
    return "\n".join(lines)


def _build_category_details(assessments, category_scores, requirements):
    """Build detailed markdown sections for each category."""
    sections = []
    cat_num = 1

    for cat_key, cat_name in CATEGORY_ORDER:
        cs = category_scores.get(cat_key, {})
        if cs.get("total", 0) == 0:
            cat_num += 1
            continue

        sections.append(f"### 3.{cat_num} {cat_name}")
        sections.append("")
        sections.append("| Metric | Value |")
        sections.append("|--------|------:|")
        sections.append(f"| Requirements Assessed | {cs['total']} |")
        sections.append(f"| Compliant | {cs['compliant']} |")
        sections.append(f"| Partially Compliant | {cs['partially_compliant']} |")
        sections.append(f"| Non-Compliant | {cs['non_compliant']} |")
        sections.append(f"| Not Assessed | {cs['not_assessed']} |")
        sections.append(f"| Category Score | {cs['score']}% |")
        sections.append("")
        sections.append("#### Requirement Findings")
        sections.append("")

        cat_items = [a for a in assessments if a.get("category") == cat_key]
        for item in sorted(cat_items, key=lambda x: x.get("requirement_id", "")):
            req_id = item.get("requirement_id", "N/A")
            title = item.get("requirement_title", "")
            status = item.get("status", "not_assessed")
            evidence = (item.get("evidence") or "").replace("\n", " ").strip()
            notes = (item.get("notes") or "").replace("\n", " ").strip()

            if len(evidence) > 120:
                evidence = evidence[:117] + "..."
            if len(notes) > 120:
                notes = notes[:117] + "..."

            sections.append(f"**{req_id}: {title}**")
            sections.append("")
            sections.append(f"- **Status:** {status}")
            sections.append(f"- **Evidence:** {evidence}")
            if notes:
                sections.append(f"- **Notes:** {notes}")
            sections.append("")

        cat_num += 1

    return "\n".join(sections)


def _build_gap_analysis(assessments, requirements):
    """Build gap analysis section for non-compliant and partial items."""
    gaps = [
        a for a in assessments
        if a.get("status") in ("non_compliant", "partially_compliant")
    ]

    if not gaps:
        return "*No gaps identified. All assessed requirements are compliant.*"

    lines = [
        "The following requirements have not achieved full compliance and require remediation:",
        "",
        "| Req ID | Category | Title | Status | Priority |",
        "|--------|----------|-------|--------|----------|",
    ]

    for g in sorted(gaps, key=lambda x: (x.get("category", ""), x.get("requirement_id", ""))):
        req_id = g.get("requirement_id", "N/A")
        category = g.get("category", "N/A")
        title = g.get("requirement_title", "")
        status = g.get("status", "N/A")
        req_data = requirements.get(req_id, {})
        priority = req_data.get("priority", "medium")
        if len(title) > 50:
            title = title[:47] + "..."
        lines.append(f"| {req_id} | {category} | {title} | {status} | {priority} |")

    lines.append("")
    non_compliant = sum(1 for g in gaps if g["status"] == "non_compliant")
    partial = sum(1 for g in gaps if g["status"] == "partially_compliant")
    lines.append(f"- **Total Gaps Identified:** {len(gaps)}")
    lines.append(f"- **Non-Compliant:** {non_compliant}")
    lines.append(f"- **Partially Compliant:** {partial}")

    return "\n".join(lines)


def _build_gate_evaluation(assessments, requirements, overall_score, gate_status):
    """Build gate evaluation section."""
    # Compute individual gate checks
    critical_nc = sum(
        1 for a in assessments
        if a.get("status") == "non_compliant"
        and requirements.get(a.get("requirement_id", ""), {}).get("priority") == "critical"
    )

    model_auth_nc = sum(
        1 for a in assessments
        if a.get("category") == "model_authority" and a.get("status") == "non_compliant"
    )

    # Digital thread coverage check
    thread_items = [a for a in assessments if a.get("requirement_id") in ("DES-2.4", "DES-6.4")]
    thread_pass = all(a.get("status") == "compliant" for a in thread_items) if thread_items else False

    model_authority_gate = "PASS" if model_auth_nc == 0 else "FAIL"
    score_gate = "PASS" if overall_score >= 70.0 else "FAIL"
    thread_gate = "PASS" if thread_pass else "FAIL"

    lines = [
        f"**Gate Status:** {gate_status}",
        "",
        "**Gate Criteria:**",
        f"- 0 non-compliant on critical-priority requirements = {'PASS' if critical_nc == 0 else 'FAIL'}",
        f"- 0 non-compliant in model_authority category = {model_authority_gate}",
        f"- Overall DES score >= 70% = {score_gate}",
        f"- Digital thread coverage check = {thread_gate}",
        "",
        "| Gate Check | Requirement | Result |",
        "|------------|-------------|--------|",
        f"| Critical Priority | 0 non-compliant on critical | {'PASS' if critical_nc == 0 else 'FAIL'} |",
        f"| Model Authority | 0 non-compliant in model_authority | {model_authority_gate} |",
        f"| Score Threshold | Overall score >= 70% | {score_gate} |",
        f"| Digital Thread | Thread coverage checks pass | {thread_gate} |",
        f"| **Overall Gate** | **All critical checks must PASS** | **{gate_status}** |",
    ]
    return "\n".join(lines)


def _build_executive_summary(overall_score, gate_status, assessments,
                             category_scores, requirements):
    """Build executive summary paragraph."""
    total = len(assessments)
    compliant = sum(1 for a in assessments if a["status"] == "compliant")
    partial = sum(1 for a in assessments if a["status"] == "partially_compliant")
    non_compliant = sum(1 for a in assessments if a["status"] == "non_compliant")
    not_assessed = sum(1 for a in assessments if a["status"] == "not_assessed")
    categories_with_data = sum(
        1 for v in category_scores.values() if v.get("total", 0) > 0
    )

    # Find weakest category
    scored_cats = {
        k: v for k, v in category_scores.items()
        if v.get("total", 0) > 0
        and v.get("total", 0) != v.get("not_applicable", 0)
    }
    weakest_cat = ""
    weakest_score = 100.0
    for k, v in scored_cats.items():
        if v["score"] < weakest_score:
            weakest_score = v["score"]
            weakest_cat = v.get("name", k)

    critical_nc = sum(
        1 for a in assessments
        if a.get("status") == "non_compliant"
        and requirements.get(a.get("requirement_id", ""), {}).get("priority") == "critical"
    )

    lines = [
        (
            f"This DES assessment evaluated {total} requirements across "
            f"{categories_with_data} categories derived from DoDI 5000.87 and the "
            f"DoD Digital Engineering Strategy. The overall score is **{overall_score:.1f}%** "
            f"with a gate status of **{gate_status}**."
        ),
        "",
        (
            f"- **{compliant}** requirements compliant, "
            f"**{partial}** partially compliant, "
            f"**{non_compliant}** non-compliant, "
            f"**{not_assessed}** not assessed."
        ),
    ]
    if critical_nc > 0:
        lines.append(
            f"- **{critical_nc} critical-priority requirement(s) non-compliant** "
            f"-- immediate remediation required."
        )
    if weakest_cat:
        lines.append(
            f"- Weakest category: **{weakest_cat}** ({weakest_score:.1f}%)."
        )

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Main generator
# ---------------------------------------------------------------------------

def generate_des_report(project_id, output_path=None, db_path=None):
    """Generate CUI-marked DES compliance report.

    Steps:
        1. Query des_compliance table for all results
        2. Group by category
        3. Load template from context/mbse/des_report_template.md
        4. Render with simple string replacement
        5. Save to output_path or .tmp/reports/

    Args:
        project_id: The project identifier.
        output_path: Override output file or directory path.
        db_path: Override database path.

    Returns:
        dict with file_path, overall_score, and gate_status.
    """
    conn = _get_connection(db_path)
    try:
        # 1. Load project data
        project = _get_project_data(conn, project_id)
        project_name = project.get("name", project_id)

        # 2. Load template
        template = _load_template()

        # 3. Query des_compliance
        assessments = _get_des_assessments(conn, project_id)

        # 4. Load requirements catalog for enrichment
        requirements = _load_des_requirements()

        # 5. Calculate scores
        category_scores = _calculate_category_scores(assessments)
        overall_score, overall_status = _calculate_overall(category_scores)

        # Gate status
        critical_nc = sum(
            1 for a in assessments
            if a.get("status") == "non_compliant"
            and requirements.get(a.get("requirement_id", ""), {}).get("priority") == "critical"
        )
        critical_partial = sum(
            1 for a in assessments
            if a.get("status") == "partially_compliant"
            and requirements.get(a.get("requirement_id", ""), {}).get("priority") == "critical"
        )

        if critical_nc == 0 and critical_partial == 0:
            gate_status = "PASS"
        elif critical_nc == 0 and critical_partial > 0:
            gate_status = "WARN"
        else:
            gate_status = "FAIL"

        # Counts
        total = len(assessments)
        compliant = sum(1 for a in assessments if a["status"] == "compliant")
        partial = sum(1 for a in assessments if a["status"] == "partially_compliant")
        non_compliant = sum(1 for a in assessments if a["status"] == "non_compliant")
        not_applicable = sum(1 for a in assessments if a["status"] == "not_applicable")
        not_assessed = sum(1 for a in assessments if a["status"] == "not_assessed")
        categories_assessed = sum(
            1 for v in category_scores.values() if v.get("total", 0) > 0
        )

        def pct(n):
            return f"{100.0 * n / total:.1f}" if total > 0 else "0.0"

        # 6. Build section content
        assessment_summary_table = _build_assessment_summary_table(assessments)
        category_details = _build_category_details(assessments, category_scores, requirements)
        gap_analysis = _build_gap_analysis(assessments, requirements)
        gate_evaluation = _build_gate_evaluation(
            assessments, requirements, overall_score, gate_status
        )
        executive_summary = _build_executive_summary(
            overall_score, gate_status, assessments, category_scores, requirements
        )

        # CUI config
        cui_config = _load_cui_config()

        # Determine version number
        try:
            report_count_row = conn.execute(
                """SELECT COUNT(*) as cnt FROM audit_trail
                   WHERE project_id = ? AND event_type = 'des_report_generated'""",
                (project_id,),
            ).fetchone()
            report_count = report_count_row["cnt"] if report_count_row else 0
        except Exception:
            report_count = 0
        new_version = f"{report_count + 1}.0"

        now = datetime.now(timezone.utc)

        # 7. Build substitution dict
        variables = {
            "project_name": project_name,
            "project_id": project_id,
            "classification": project.get("classification", "CUI"),
            "version": new_version,
            "assessment_date": now.strftime("%Y-%m-%d"),
            "assessor": "icdev-compliance-engine",

            "overall_score": f"{overall_score:.1f}",
            "gate_status": gate_status,
            "categories_assessed": str(categories_assessed),

            "requirements_total": str(total),
            "requirements_compliant": str(compliant),
            "requirements_partial": str(partial),
            "requirements_non_compliant": str(non_compliant),
            "requirements_na": str(not_applicable),
            "requirements_not_assessed": str(not_assessed),

            "pct_compliant": pct(compliant),
            "pct_partial": pct(partial),
            "pct_non_compliant": pct(non_compliant),
            "pct_na": pct(not_applicable),

            "executive_summary": executive_summary,
            "assessment_summary_table": assessment_summary_table,
            "category_details": category_details,
            "gap_analysis": gap_analysis,
            "gate_evaluation": gate_evaluation,
            "total_gaps": str(non_compliant + partial),
            "remediation_effort": "See gap analysis above",

            # Digital thread coverage variables for template
            "models_registered": str(total),
            "models_with_traceability": str(compliant + partial),
            "digital_thread_coverage": f"{overall_score:.1f}",
            "data_exchange_standards_met": str(compliant),
            "data_exchange_standards_total": str(total),
            "authoritative_source_defined": "Yes" if any(
                a.get("requirement_id") == "DES-1.1" and a.get("status") == "compliant"
                for a in assessments
            ) else "No",

            # Gate sub-checks
            "model_authority_gate": "PASS" if not any(
                a.get("category") == "model_authority" and a.get("status") == "non_compliant"
                for a in assessments
            ) else "FAIL",
            "score_gate": "PASS" if overall_score >= 70.0 else "FAIL",
            "thread_gate": "PASS" if any(
                a.get("requirement_id") in ("DES-2.4", "DES-6.4")
                and a.get("status") == "compliant"
                for a in assessments
            ) else "FAIL",

            # CUI banners
            "cui_banner_top": cui_config.get(
                "document_header",
                "CUI // SP-CTI"
            ),
            "cui_banner_bottom": cui_config.get(
                "document_footer",
                "CUI // SP-CTI"
            ),

            # Pre-rendered Jinja-style blocks
            "categories_rendered": category_details,
            "gaps_rendered": gap_analysis,
            "recommendations_rendered": (
                "See gap analysis and gate evaluation sections for "
                "specific remediation recommendations."
            ),
        }

        # Per-category score variables
        for cat_key, cat_name in CATEGORY_ORDER:
            cs = category_scores.get(cat_key, {})
            prefix = cat_key
            variables[f"{prefix}_score"] = f"{cs.get('score', 0.0):.1f}"
            variables[f"{prefix}_total"] = str(cs.get("total", 0))
            variables[f"{prefix}_compliant"] = str(cs.get("compliant", 0))
            variables[f"{prefix}_partial"] = str(cs.get("partially_compliant", 0))
            variables[f"{prefix}_non_compliant"] = str(cs.get("non_compliant", 0))

        # 8. Apply template substitution
        report_content = _substitute_variables(template, variables)

        # 9. Determine output path
        if output_path:
            out_path = Path(output_path)
            if out_path.is_dir() or str(output_path).endswith("/") or str(output_path).endswith("\\"):
                out_dir = out_path
                out_file = out_dir / f"des-report-v{new_version}.md"
            else:
                out_file = out_path
        else:
            dir_path = project.get("directory_path", "")
            if dir_path:
                out_dir = Path(dir_path) / "compliance"
            else:
                out_dir = BASE_DIR / ".tmp" / "reports" / project_id
            out_file = out_dir / f"des-report-v{new_version}.md"

        out_file.parent.mkdir(parents=True, exist_ok=True)

        # 10. Write file
        with open(out_file, "w", encoding="utf-8") as f:
            f.write(report_content)

        # 11. Log audit event
        audit_details = {
            "version": new_version,
            "overall_score": overall_score,
            "gate_status": gate_status,
            "categories_assessed": categories_assessed,
            "total_assessments": total,
            "compliant": compliant,
            "non_compliant": non_compliant,
            "output_file": str(out_file),
        }
        _log_audit_event(
            conn, project_id,
            f"DES report v{new_version} generated",
            audit_details,
            out_file,
        )

        # Console output
        print("DES compliance report generated successfully:")
        print(f"  File:              {out_file}")
        print(f"  Version:           {new_version}")
        print(f"  Project:           {project_name}")
        print(f"  Overall Score:     {overall_score:.1f}%")
        print(f"  Gate Status:       {gate_status}")
        print(f"  Categories:        {categories_assessed} / 6")
        print(f"  Total Reqs:        {total}")
        print(f"  Compliant:         {compliant}")
        print(f"  Non-Compliant:     {non_compliant}")

        return {
            "file_path": str(out_file),
            "overall_score": overall_score,
            "gate_status": gate_status,
        }

    finally:
        conn.close()


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Generate DES compliance report"
    )
    parser.add_argument(
        "--project-id", required=True, help="Project ID"
    )
    parser.add_argument(
        "--output", help="Output file path"
    )
    parser.add_argument(
        "--json", action="store_true",
        help="Output result metadata as JSON"
    )
    parser.add_argument(
        "--db-path", type=Path, default=DB_PATH,
        help="Override database path"
    )
    args = parser.parse_args()

    try:
        result = generate_des_report(
            project_id=args.project_id,
            output_path=args.output,
            db_path=args.db_path,
        )
        if args.json:
            print(json.dumps(result, indent=2))
        else:
            print(f"\nDES report generated: {result['file_path']}")
    except (FileNotFoundError, ValueError) as e:
        print(f"ERROR: {e}", file=sys.stderr)
        sys.exit(1)
# CUI // SP-CTI
