# [TEMPLATE: CUI // SP-CTI]
#!/usr/bin/env python3
"""Migration Report Generator for ICDEV DoD Modernization.

Generates CUI-marked reports for migration assessments, progress tracking,
ATO impact analysis, and executive summaries.  Reads legacy application data,
7R assessment scores, migration plans, tasks, and progress snapshots from
icdev.db and produces structured Markdown documents with CUI // SP-CTI
banners and Distribution D statements.

All generated reports include:
  - CUI // SP-CTI banners at top and bottom
  - Distribution D: Authorized DoD Personnel Only
  - ISO-formatted generation timestamps
  - ICDEV report engine attribution

Usage:
    python tools/modernization/migration_report_generator.py --app-id A-001 --type assessment
    python tools/modernization/migration_report_generator.py --plan-id MP-001 --type progress --pi PI-3
    python tools/modernization/migration_report_generator.py --plan-id MP-001 --type ato-impact
    python tools/modernization/migration_report_generator.py --app-id A-001 --type executive
    python tools/modernization/migration_report_generator.py --app-id A-001 --plan-id MP-001 --type all
    python tools/modernization/migration_report_generator.py --app-id A-001 --type assessment --json

Classification: CUI // SP-CTI
Environment:    AWS GovCloud (us-gov-west-1)
Compliance:     NIST 800-53 Rev 5 / RMF
"""

import argparse
import json
import sqlite3
import sys
import textwrap
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
BASE_DIR = Path(__file__).resolve().parent.parent.parent
DB_PATH = BASE_DIR / "data" / "icdev.db"
TEMPLATE_PATH = BASE_DIR / "context" / "modernization" / "migration_report_template.md"

# ---------------------------------------------------------------------------
# CUI marking constants
# ---------------------------------------------------------------------------
CUI_BANNER_TEXT = "CUI // SP-CTI"
DISTRIBUTION_STMT = "Distribution D: Authorized DoD Personnel Only"

# ATO impact descriptions by level
ATO_IMPACT_DESCRIPTIONS = {
    "none": "No impact to existing ATO boundary. Existing authorization remains valid.",
    "low": "Minor infrastructure changes. Update System Security Plan (SSP) appendices. "
           "No new ATO required; submit a Significant Change Request (SCR).",
    "medium": "Version and framework changes affect the technology baseline. "
              "Update SSP, SAR, and POAM. ATO addendum or reassessment likely required.",
    "high": "New architecture introduces new authorization boundary. Full ATO "
            "reassessment required including updated SSP, SAR, POAM, and STIG review.",
    "critical": "Complete system replacement. New ATO package required from scratch. "
                "Coordinate with ISSM/AO for timeline and interim ATO.",
}

# NIST 800-53 control families commonly affected by migration strategies
STRATEGY_CONTROL_FAMILIES = {
    "rehost": ["CM-2", "CM-3", "CM-8", "SA-10"],
    "replatform": ["CM-2", "CM-3", "CM-8", "SA-10", "SC-7", "SI-2"],
    "refactor": ["CM-2", "CM-3", "CM-8", "SA-10", "SA-11", "SI-2", "SI-7"],
    "rearchitect": [
        "AC-2", "AC-3", "AU-2", "AU-3", "CM-2", "CM-3", "CM-8",
        "IA-2", "SA-10", "SA-11", "SC-7", "SC-8", "SI-2", "SI-7",
    ],
    "repurchase": [
        "AC-2", "AC-3", "AU-2", "AU-3", "CA-2", "CM-2", "CM-3", "CM-8",
        "IA-2", "IA-5", "SA-4", "SA-10", "SA-11", "SC-7", "SC-8",
        "SI-2", "SI-7",
    ],
    "retire": ["CM-8", "MP-6", "SI-12"],
    "retain": ["CM-2", "SI-2"],
}

# Risk level labels
RISK_LABELS = {
    (0.0, 0.2): "LOW",
    (0.2, 0.4): "MODERATE",
    (0.4, 0.6): "SIGNIFICANT",
    (0.6, 0.8): "HIGH",
    (0.8, 1.01): "CRITICAL",
}

# Strategy-to-ATO compliance weeks overhead
STRATEGY_ATO_WEEKS = {
    "rehost": 0,
    "replatform": 2,
    "refactor": 4,
    "rearchitect": 8,
    "repurchase": 12,
    "retire": 1,
    "retain": 0,
}


# ============================================================================
# Database helper
# ============================================================================

def _get_db(db_path=None):
    """Return a sqlite3 connection with Row factory for dict-like access.

    Args:
        db_path: Optional override path to the SQLite database.

    Returns:
        sqlite3.Connection with row_factory set to sqlite3.Row.

    Raises:
        FileNotFoundError: If the database file does not exist.
    """
    path = Path(db_path) if db_path else DB_PATH
    if not path.exists():
        raise FileNotFoundError(
            f"Database not found: {path}\n"
            "Run: python tools/db/init_icdev_db.py"
        )
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    return conn


# ============================================================================
# CUI banner helper
# ============================================================================

def _cui_banner():
    """Return the standard CUI banner string with distribution statement.

    Returns:
        str: Multi-line CUI banner for inclusion in reports.
    """
    return f"{CUI_BANNER_TEXT}\n{DISTRIBUTION_STMT}"


# ============================================================================
# Template loader
# ============================================================================

def _load_template():
    """Load the migration report Markdown template from the context directory.

    If the template file is not found, returns a minimal built-in default
    template string that can be used for simple variable substitution.

    Returns:
        str: The template content.
    """
    if TEMPLATE_PATH.exists():
        with open(TEMPLATE_PATH, "r", encoding="utf-8") as fh:
            return fh.read()
    # Built-in fallback template
    return textwrap.dedent("""\
        CUI // SP-CTI
        Distribution D: Authorized DoD Personnel Only

        # Migration Assessment Report: {{ app_name }}

        **Report Date:** {{ report_date }}
        **Classification:** CUI // SP-CTI

        ---

        ## Executive Summary

        Application **{{ app_name }}** recommended strategy: **{{ recommended_strategy }}**.
        Estimated effort: **{{ estimated_hours }} hours**. Risk level: **{{ risk_level }}**.

        ---

        *Generated by ICDEV Migration Report Engine*

        CUI // SP-CTI
        Distribution D: Authorized DoD Personnel Only
    """)


# ============================================================================
# Utility helpers
# ============================================================================

def _now_iso():
    """Return the current UTC datetime as an ISO-formatted string."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _ensure_dir(output_dir):
    """Ensure the output directory exists, creating it if necessary.

    Args:
        output_dir: Path string or Path object for the output directory.

    Returns:
        Path object for the directory.
    """
    path = Path(output_dir)
    path.mkdir(parents=True, exist_ok=True)
    return path


def _risk_label(score):
    """Convert a numeric risk score (0.0-1.0) to a human-readable label.

    Args:
        score: Float risk score.

    Returns:
        str: Risk label such as 'LOW', 'MODERATE', 'HIGH', etc.
    """
    for (lo, hi), label in RISK_LABELS.items():
        if lo <= score < hi:
            return label
    return "UNKNOWN"


def _format_number(value):
    """Format a number with comma separators, handling None gracefully.

    Args:
        value: Numeric value or None.

    Returns:
        str: Formatted number string, or 'N/A' if value is None.
    """
    if value is None:
        return "N/A"
    if isinstance(value, float):
        return f"{value:,.1f}"
    return f"{value:,}"


def _write_report(filepath, content):
    """Write a CUI-marked report to the given filepath.

    The report is wrapped with CUI banners at top and bottom, plus the
    Distribution D statement.

    Args:
        filepath: Path to write the file.
        content: The markdown content body.

    Returns:
        str: Absolute path to the written file.
    """
    path = Path(filepath)
    banner = _cui_banner()
    full_content = f"{banner}\n\n{content}\n\n---\n\n*Generated by ICDEV Migration Report Engine — {_now_iso()}*\n\n{banner}\n"
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(full_content)
    return str(path.resolve())


def _safe_json_loads(raw, default=None):
    """Safely parse a JSON string, returning default on failure.

    Args:
        raw: A JSON string or already-parsed object.
        default: Value to return if parsing fails.

    Returns:
        Parsed object or default.
    """
    if raw is None:
        return default if default is not None else {}
    if isinstance(raw, (dict, list)):
        return raw
    try:
        return json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return default if default is not None else {}


# ============================================================================
# 1. Assessment Report
# ============================================================================

def generate_assessment_report(app_id, output_dir=None, db_path=None):
    """Generate a CUI-marked 7R assessment report for a legacy application.

    Queries legacy_applications, migration_assessments, legacy_components,
    legacy_apis, and legacy_db_schemas to build a comprehensive assessment
    report covering all 7 migration strategies with scoring, risk analysis,
    ATO impact, and resource estimates.

    Args:
        app_id:     Legacy application ID.
        output_dir: Directory to write the report (default: current dir).
        db_path:    Optional database path override.

    Returns:
        str: Absolute path to the generated assessment report file.

    Raises:
        ValueError: If the application or assessment is not found.
        FileNotFoundError: If the database does not exist.
    """
    out_dir = _ensure_dir(output_dir or ".")
    conn = _get_db(db_path)

    try:
        # --- Fetch application ---
        app_row = conn.execute(
            "SELECT * FROM legacy_applications WHERE id = ?", (app_id,)
        ).fetchone()
        if app_row is None:
            raise ValueError(f"Application '{app_id}' not found in legacy_applications.")
        app = dict(app_row)

        # --- Fetch latest assessment ---
        assess_row = conn.execute(
            "SELECT * FROM migration_assessments WHERE legacy_app_id = ? "
            "ORDER BY rowid DESC LIMIT 1", (app_id,)
        ).fetchone()
        if assess_row is None:
            raise ValueError(
                f"No assessment found for application '{app_id}'. "
                "Run: python tools/modernization/seven_r_assessor.py --app-id <ID>"
            )
        assessment = dict(assess_row)

        # --- Fetch components (top 20 by complexity) ---
        comp_rows = conn.execute(
            "SELECT * FROM legacy_components WHERE legacy_app_id = ? "
            "ORDER BY cyclomatic_complexity DESC LIMIT 20", (app_id,)
        ).fetchall()
        components = [dict(r) for r in comp_rows]

        total_components_row = conn.execute(
            "SELECT COUNT(*) AS cnt FROM legacy_components WHERE legacy_app_id = ?",
            (app_id,),
        ).fetchone()
        total_components = total_components_row["cnt"] if total_components_row else 0

        # --- Fetch APIs ---
        api_rows = conn.execute(
            "SELECT * FROM legacy_apis WHERE legacy_app_id = ? ORDER BY path, method",
            (app_id,),
        ).fetchall()
        apis = [dict(r) for r in api_rows]

        # --- Fetch DB schemas (grouped by table) ---
        schema_rows = conn.execute(
            "SELECT * FROM legacy_db_schemas WHERE legacy_app_id = ? "
            "ORDER BY table_name, column_name", (app_id,)
        ).fetchall()
        db_schemas = [dict(r) for r in schema_rows]

    finally:
        conn.close()

    # --- Derived values ---
    risk_score = assessment.get("risk_score", 0.0) or 0.0
    risk_level = _risk_label(risk_score)
    recommended = assessment.get("recommended_strategy", "N/A")
    cost_hours = assessment.get("cost_estimate_hours", 0) or 0
    timeline_weeks = assessment.get("timeline_weeks", 0) or 0
    ato_impact = assessment.get("ato_impact", "N/A")
    evidence = _safe_json_loads(assessment.get("evidence"))

    # Group DB schemas by table for column counts
    table_columns = defaultdict(int)
    table_db_type = {}
    for col in db_schemas:
        tbl = col.get("table_name", "unknown")
        table_columns[tbl] += 1
        if tbl not in table_db_type:
            table_db_type[tbl] = col.get("db_type", "unknown")

    # --- Build report sections ---
    lines = []

    # Title
    lines.append(f"# Migration Assessment Report: {app.get('name', app_id)}")
    lines.append("")
    lines.append(f"**Report Date:** {_now_iso()}")
    lines.append(f"**Classification:** {CUI_BANNER_TEXT}")
    lines.append(f"**Application ID:** {app_id}")
    lines.append(f"**Project ID:** {app.get('project_id', 'N/A')}")
    lines.append("")
    lines.append("---")
    lines.append("")

    # --- Section 1: Executive Summary ---
    lines.append("## 1. Executive Summary")
    lines.append("")
    lines.append(
        f"Application **{app.get('name', app_id)}** has been assessed for modernization "
        f"using the 7R framework. The recommended migration strategy is "
        f"**{recommended.upper()}** based on analysis of **{total_components}** components, "
        f"**{len(apis)}** API endpoints, and **{len(table_columns)}** database tables."
    )
    lines.append("")
    lines.append(f"- **Recommended Strategy:** {recommended.upper()}")
    lines.append(f"- **Estimated Effort:** {_format_number(cost_hours)} hours")
    lines.append(f"- **Timeline:** {timeline_weeks} weeks")
    lines.append(f"- **Risk Level:** {risk_level} ({risk_score:.2f})")
    lines.append(f"- **ATO Impact:** {ato_impact.upper()}")
    lines.append(f"- **Total Components:** {total_components}")
    lines.append("")
    lines.append("---")
    lines.append("")

    # --- Section 2: Legacy Application Profile ---
    lines.append("## 2. Legacy Application Profile")
    lines.append("")
    lines.append("| Attribute         | Value                                       |")
    lines.append("|-------------------|---------------------------------------------|")
    lines.append(f"| Language          | {app.get('primary_language', 'N/A')} {app.get('language_version', '')} |")
    lines.append(f"| Framework         | {app.get('framework', 'N/A')} {app.get('framework_version', '')} |")
    lines.append(f"| Application Type  | {app.get('app_type', 'N/A')} |")
    lines.append(f"| Lines of Code     | {_format_number(app.get('loc_total'))} |")
    lines.append(f"| Code Lines        | {_format_number(app.get('loc_code'))} |")
    lines.append(f"| File Count        | {_format_number(app.get('file_count'))} |")
    lines.append(f"| Complexity Score  | {_format_number(app.get('complexity_score'))} |")
    lines.append(f"| Tech Debt         | {_format_number(app.get('tech_debt_hours'))} hours |")
    lines.append(f"| Maintainability   | {_format_number(app.get('maintainability_index'))} |")
    lines.append("")
    lines.append("---")
    lines.append("")

    # --- Section 3: Component Analysis ---
    lines.append("## 3. Component Analysis")
    lines.append("")
    lines.append(f"Showing top {len(components)} components by cyclomatic complexity "
                 f"(out of {total_components} total).")
    lines.append("")
    lines.append("| Component | Type | LOC | Complexity | Coupling | Cohesion |")
    lines.append("|-----------|------|-----|------------|----------|----------|")
    for comp in components:
        cname = comp.get("name", "N/A")
        ctype = comp.get("component_type", "N/A")
        cloc = comp.get("loc", 0) or 0
        ccx = comp.get("cyclomatic_complexity", 0) or 0
        ccoup = comp.get("coupling_score", 0) or 0
        ccoh = comp.get("cohesion_score", 0) or 0
        lines.append(f"| {cname} | {ctype} | {cloc:,} | {ccx:.1f} | {ccoup:.2f} | {ccoh:.2f} |")
    lines.append("")
    lines.append(f"**Total Components:** {total_components}")
    lines.append("")
    lines.append("---")
    lines.append("")

    # --- Section 4: API Inventory ---
    lines.append("## 4. API Inventory")
    lines.append("")
    if apis:
        lines.append("| Method | Path | Handler | Auth Required |")
        lines.append("|--------|------|---------|---------------|")
        for api in apis:
            method = api.get("method", "N/A")
            path = api.get("path", "N/A")
            handler = api.get("handler_function", "N/A")
            auth = "Yes" if api.get("auth_required") else "No"
            lines.append(f"| {method} | {path} | {handler} | {auth} |")
        lines.append("")
        lines.append(f"**Total Endpoints:** {len(apis)}")
    else:
        lines.append("*No API endpoints discovered for this application.*")
    lines.append("")
    lines.append("---")
    lines.append("")

    # --- Section 5: Database Schema ---
    lines.append("## 5. Database Schema")
    lines.append("")
    if table_columns:
        lines.append("| Table | DB Type | Column Count |")
        lines.append("|-------|---------|--------------|")
        for tbl_name in sorted(table_columns.keys()):
            col_count = table_columns[tbl_name]
            db_type = table_db_type.get(tbl_name, "unknown")
            lines.append(f"| {tbl_name} | {db_type} | {col_count} |")
        lines.append("")
        lines.append(f"**Total Tables:** {len(table_columns)}")
        lines.append(f"**Total Columns:** {len(db_schemas)}")
    else:
        lines.append("*No database schemas discovered for this application.*")
    lines.append("")
    lines.append("---")
    lines.append("")

    # --- Section 6: 7R Scoring Matrix ---
    lines.append("## 6. 7R Scoring Matrix")
    lines.append("")
    strategy_names = [
        ("rehost", "Rehost"),
        ("replatform", "Replatform"),
        ("refactor", "Refactor"),
        ("rearchitect", "Rearchitect"),
        ("repurchase", "Repurchase"),
        ("retire", "Retire"),
        ("retain", "Retain"),
    ]
    lines.append("| Strategy     | Score  | Recommended |")
    lines.append("|--------------|--------|-------------|")
    for sid, sname in strategy_names:
        score_key = f"{sid}_score"
        score_val = assessment.get(score_key, 0.0) or 0.0
        marker = ">> YES <<" if sid == recommended else ""
        lines.append(f"| {sname:<12} | {score_val:.4f} | {marker} |")
    lines.append("")
    lines.append(f"**Recommended Strategy:** **{recommended.upper()}** "
                 f"(Score: {assessment.get(recommended + '_score', 0.0):.4f})")
    lines.append("")
    lines.append("---")
    lines.append("")

    # --- Section 7: Risk Assessment ---
    lines.append("## 7. Risk Assessment")
    lines.append("")
    lines.append(f"**Overall Risk Score:** {risk_score:.4f} ({risk_level})")
    lines.append("")
    lines.append("Risk score combines strategy inherent risk (40%), application "
                 "health (30%), ATO impact (20%), and dependency complexity (10%).")
    lines.append("")
    # Risk factor breakdown from evidence if available
    profile_summary = evidence.get("profile_summary", {})
    maint = profile_summary.get("maintainability_index", app.get("maintainability_index", 0))
    if maint is not None:
        health_risk = max(0.0, min(1.0, 1.0 - (float(maint or 0) / 100.0)))
        lines.append(f"- **Application Health Risk:** {health_risk:.2f} "
                     f"(maintainability index: {_format_number(maint)})")
    lines.append(f"- **ATO Impact Risk:** {ato_impact}")
    lines.append(f"- **Component Count:** {total_components}")
    lines.append(f"- **Tech Debt:** {_format_number(app.get('tech_debt_hours'))} hours")
    lines.append("")
    lines.append("---")
    lines.append("")

    # --- Section 8: ATO Impact Analysis ---
    lines.append("## 8. ATO Impact Analysis")
    lines.append("")
    ato_desc = ATO_IMPACT_DESCRIPTIONS.get(ato_impact, "Impact level not determined.")
    ato_weeks = STRATEGY_ATO_WEEKS.get(recommended, 0)
    controls = STRATEGY_CONTROL_FAMILIES.get(recommended, [])
    lines.append("| Attribute            | Value                        |")
    lines.append("|----------------------|------------------------------|")
    lines.append(f"| Impact Level         | {ato_impact.upper()} |")
    lines.append(f"| Controls Affected    | {len(controls)} control families |")
    lines.append(f"| Estimated ATO Delay  | {ato_weeks} weeks |")
    lines.append("")
    lines.append(f"**Assessment:** {ato_desc}")
    lines.append("")
    if controls:
        lines.append("**Affected Control Families:**")
        for ctrl in controls:
            lines.append(f"- {ctrl}")
    lines.append("")
    lines.append("---")
    lines.append("")

    # --- Section 9: Timeline & Resources ---
    lines.append("## 9. Timeline & Resources")
    lines.append("")
    fte_estimate = max(1, round(cost_hours / max(timeline_weeks * 40, 1)))
    lines.append("| Attribute        | Value                          |")
    lines.append("|------------------|--------------------------------|")
    lines.append(f"| Estimated Hours  | {_format_number(cost_hours)} |")
    lines.append(f"| Timeline         | {timeline_weeks} weeks |")
    lines.append(f"| Estimated FTEs   | {fte_estimate} |")
    lines.append(f"| ATO Overhead     | +{ato_weeks} weeks |")
    lines.append(f"| Total Timeline   | {timeline_weeks + ato_weeks} weeks |")
    lines.append("")

    content = "\n".join(lines)
    filepath = out_dir / f"assessment_report_{app_id}.md"
    return _write_report(filepath, content)


# ============================================================================
# 2. Progress Report
# ============================================================================

def generate_progress_report(plan_id, pi_number=None, output_dir=None, db_path=None):
    """Generate a migration progress report for a specific plan and PI.

    Queries migration_plans, migration_tasks, and migration_progress to
    build a report showing task status, velocity, hours tracking, blockers,
    and component migration progress.

    Args:
        plan_id:    Migration plan ID.
        pi_number:  Optional PI number for focused snapshot.
        output_dir: Directory to write the report (default: current dir).
        db_path:    Optional database path override.

    Returns:
        str: Absolute path to the generated progress report file.

    Raises:
        ValueError: If the migration plan is not found.
        FileNotFoundError: If the database does not exist.
    """
    out_dir = _ensure_dir(output_dir or ".")
    conn = _get_db(db_path)

    try:
        # --- Fetch plan ---
        plan_row = conn.execute(
            "SELECT * FROM migration_plans WHERE id = ?", (plan_id,)
        ).fetchone()
        if plan_row is None:
            raise ValueError(f"Migration plan '{plan_id}' not found in migration_plans.")
        plan = dict(plan_row)

        # --- Fetch all tasks for this plan ---
        task_rows = conn.execute(
            "SELECT * FROM migration_tasks WHERE plan_id = ? ORDER BY priority, title",
            (plan_id,),
        ).fetchall()
        tasks = [dict(r) for r in task_rows]

        # --- Fetch progress snapshots ---
        if pi_number:
            progress_rows = conn.execute(
                "SELECT * FROM migration_progress WHERE plan_id = ? AND pi_number = ? "
                "ORDER BY rowid DESC",
                (plan_id, pi_number),
            ).fetchall()
        else:
            progress_rows = conn.execute(
                "SELECT * FROM migration_progress WHERE plan_id = ? ORDER BY pi_number",
                (plan_id,),
            ).fetchall()
        progress = [dict(r) for r in progress_rows]

    finally:
        conn.close()

    # --- Compute task status counts ---
    status_counts = defaultdict(int)
    for task in tasks:
        st = (task.get("status") or "pending").lower()
        status_counts[st] += 1

    total_tasks = len(tasks)
    completed = status_counts.get("completed", 0) + status_counts.get("done", 0)
    in_progress = status_counts.get("in_progress", 0) + status_counts.get("in-progress", 0)
    blocked = status_counts.get("blocked", 0)
    pending = total_tasks - completed - in_progress - blocked

    # Filter blocked and high-priority pending tasks
    blocked_tasks = [t for t in tasks if (t.get("status") or "").lower() == "blocked"]
    high_priority_pending = [
        t for t in tasks
        if (t.get("status") or "").lower() in ("pending", "todo", "backlog")
        and (t.get("priority") or "").lower() in ("high", "critical", "1", "2")
    ]

    # --- Build report ---
    lines = []

    # Title
    plan_name = plan.get("plan_name", plan_id)
    lines.append(f"# Migration Progress Report: {plan_name}")
    lines.append("")
    lines.append(f"**Report Date:** {_now_iso()}")
    lines.append(f"**Classification:** {CUI_BANNER_TEXT}")
    lines.append(f"**Plan ID:** {plan_id}")
    if pi_number:
        lines.append(f"**Program Increment:** {pi_number}")
    lines.append("")
    lines.append("---")
    lines.append("")

    # --- Plan Summary ---
    lines.append("## 1. Plan Summary")
    lines.append("")
    lines.append("| Attribute          | Value                                          |")
    lines.append("|--------------------|------------------------------------------------|")
    lines.append(f"| Strategy           | {plan.get('strategy', 'N/A')} |")
    lines.append(f"| Migration Approach | {plan.get('migration_approach', 'N/A')} |")
    lines.append(f"| Target Language    | {plan.get('target_language', 'N/A')} |")
    lines.append(f"| Target Framework   | {plan.get('target_framework', 'N/A')} |")
    lines.append(f"| Target Architecture| {plan.get('target_architecture', 'N/A')} |")
    lines.append(f"| Status             | {plan.get('status', 'N/A')} |")
    lines.append(f"| Estimated Hours    | {_format_number(plan.get('estimated_hours'))} |")
    lines.append(f"| Actual Hours       | {_format_number(plan.get('actual_hours'))} |")
    lines.append("")
    lines.append("---")
    lines.append("")

    # --- Task Status ---
    lines.append("## 2. Task Status")
    lines.append("")
    completion_pct = (completed / total_tasks * 100) if total_tasks > 0 else 0
    lines.append(f"**Overall Completion:** {completion_pct:.1f}% "
                 f"({completed}/{total_tasks} tasks)")
    lines.append("")
    lines.append("| Status      | Count | Percentage |")
    lines.append("|-------------|-------|------------|")
    for label, count in [("Completed", completed), ("In Progress", in_progress),
                         ("Blocked", blocked), ("Pending", pending)]:
        pct = (count / total_tasks * 100) if total_tasks > 0 else 0
        lines.append(f"| {label} | {count} | {pct:.1f}% |")
    lines.append(f"| **Total** | **{total_tasks}** | **100%** |")
    lines.append("")
    lines.append("---")
    lines.append("")

    # --- PI Progress (if pi_number specified) ---
    if pi_number and progress:
        lines.append(f"## 3. PI Progress: {pi_number}")
        lines.append("")
        snapshot = progress[0]  # Most recent snapshot for this PI
        snap_tasks_total = snapshot.get("tasks_total", 0) or 0
        snap_completed = snapshot.get("tasks_completed", 0) or 0
        snap_in_progress = snapshot.get("tasks_in_progress", 0) or 0
        snap_blocked = snapshot.get("tasks_blocked", 0) or 0
        snap_comps_migrated = snapshot.get("components_migrated", 0) or 0
        snap_comps_remaining = snapshot.get("components_remaining", 0) or 0
        snap_coverage = snapshot.get("test_coverage", 0) or 0
        snap_compliance = snapshot.get("compliance_score", 0) or 0
        snap_hours = snapshot.get("hours_spent", 0) or 0

        lines.append("| Metric                | Value       |")
        lines.append("|-----------------------|-------------|")
        lines.append(f"| PI Tasks Total        | {snap_tasks_total} |")
        lines.append(f"| PI Tasks Completed    | {snap_completed} |")
        lines.append(f"| PI Tasks In Progress  | {snap_in_progress} |")
        lines.append(f"| PI Tasks Blocked      | {snap_blocked} |")
        lines.append(f"| Components Migrated   | {snap_comps_migrated} |")
        lines.append(f"| Components Remaining  | {snap_comps_remaining} |")
        lines.append(f"| Test Coverage         | {snap_coverage:.1f}% |")
        lines.append(f"| Compliance Score      | {snap_compliance:.1f}% |")
        lines.append(f"| Hours Spent (PI)      | {_format_number(snap_hours)} |")
        lines.append("")

        # Velocity: tasks completed per PI
        if snap_tasks_total > 0:
            velocity = snap_completed
            lines.append(f"**PI Velocity:** {velocity} tasks completed")
            remaining_tasks = total_tasks - completed
            if velocity > 0:
                estimated_pis = remaining_tasks / velocity
                lines.append(f"**Estimated PIs Remaining:** {estimated_pis:.1f}")
        lines.append("")
        lines.append("---")
        lines.append("")
    elif progress:
        # Show all PI snapshots as trend
        lines.append("## 3. PI Progress Trend")
        lines.append("")
        lines.append("| PI | Tasks Total | Completed | In Progress | Blocked | "
                     "Components Migrated | Test Coverage | Hours |")
        lines.append("|-----|-------------|-----------|-------------|---------|"
                     "--------------------|---------------|-------|")
        for snap in progress:
            pi = snap.get("pi_number", "N/A")
            lines.append(
                f"| {pi} "
                f"| {snap.get('tasks_total', 0)} "
                f"| {snap.get('tasks_completed', 0)} "
                f"| {snap.get('tasks_in_progress', 0)} "
                f"| {snap.get('tasks_blocked', 0)} "
                f"| {snap.get('components_migrated', 0)} "
                f"| {(snap.get('test_coverage', 0) or 0):.1f}% "
                f"| {_format_number(snap.get('hours_spent', 0))} |"
            )
        lines.append("")
        lines.append("---")
        lines.append("")

    # --- Hours Tracking ---
    lines.append("## 4. Hours Tracking")
    lines.append("")
    estimated_hours = plan.get("estimated_hours", 0) or 0
    actual_hours = plan.get("actual_hours", 0) or 0
    variance = actual_hours - estimated_hours
    burn_rate = (actual_hours / estimated_hours * 100) if estimated_hours > 0 else 0

    lines.append("| Metric          | Value              |")
    lines.append("|-----------------|-------------------|")
    lines.append(f"| Estimated Hours | {_format_number(estimated_hours)} |")
    lines.append(f"| Actual Hours    | {_format_number(actual_hours)} |")
    lines.append(f"| Variance        | {_format_number(variance)} |")
    lines.append(f"| Burn Rate       | {burn_rate:.1f}% |")
    lines.append("")
    if burn_rate > 100:
        lines.append(f"> **WARNING:** Actual hours exceed estimate by "
                     f"{burn_rate - 100:.1f}%. Review scope and resource allocation.")
    elif burn_rate > 80 and completion_pct < 80:
        lines.append(f"> **CAUTION:** {burn_rate:.0f}% of hours consumed but only "
                     f"{completion_pct:.0f}% tasks complete. Monitor burn rate closely.")
    lines.append("")
    lines.append("---")
    lines.append("")

    # --- Blockers ---
    lines.append("## 5. Blockers")
    lines.append("")
    if blocked_tasks:
        lines.append("| Task | Type | Priority | PI | Est. Hours |")
        lines.append("|------|------|----------|----|------------|")
        for bt in blocked_tasks:
            lines.append(
                f"| {bt.get('title', 'N/A')} "
                f"| {bt.get('task_type', 'N/A')} "
                f"| {bt.get('priority', 'N/A')} "
                f"| {bt.get('pi_number', 'N/A')} "
                f"| {_format_number(bt.get('estimated_hours'))} |"
            )
        lines.append("")
        lines.append(f"**Total Blocked Tasks:** {len(blocked_tasks)}")
    else:
        lines.append("*No blocked tasks at this time.*")
    lines.append("")
    lines.append("---")
    lines.append("")

    # --- Next Steps ---
    lines.append("## 6. Next Steps")
    lines.append("")
    if high_priority_pending:
        lines.append("**High-priority pending tasks:**")
        lines.append("")
        for idx, task in enumerate(high_priority_pending[:10], 1):
            lines.append(f"{idx}. **{task.get('title', 'N/A')}** "
                         f"(Type: {task.get('task_type', 'N/A')}, "
                         f"Priority: {task.get('priority', 'N/A')}, "
                         f"Est: {_format_number(task.get('estimated_hours'))} hrs)")
        if len(high_priority_pending) > 10:
            lines.append(f"\n*...and {len(high_priority_pending) - 10} more high-priority tasks.*")
    else:
        lines.append("*No high-priority pending tasks identified.*")
    lines.append("")

    content = "\n".join(lines)
    filepath = out_dir / f"progress_report_{plan_id}.md"
    return _write_report(filepath, content)


# ============================================================================
# 3. ATO Impact Report
# ============================================================================

def generate_ato_impact_report(plan_id, output_dir=None, db_path=None):
    """Generate a compliance and ATO impact analysis report for a migration plan.

    Analyzes the migration strategy's impact on the existing ATO boundary,
    identifies affected NIST 800-53 control families, evaluates compliance
    coverage, and lists remediation actions required.

    Args:
        plan_id:    Migration plan ID.
        output_dir: Directory to write the report (default: current dir).
        db_path:    Optional database path override.

    Returns:
        str: Absolute path to the generated ATO impact report file.

    Raises:
        ValueError: If the migration plan is not found.
        FileNotFoundError: If the database does not exist.
    """
    out_dir = _ensure_dir(output_dir or ".")
    conn = _get_db(db_path)

    try:
        # --- Fetch plan ---
        plan_row = conn.execute(
            "SELECT * FROM migration_plans WHERE id = ?", (plan_id,)
        ).fetchone()
        if plan_row is None:
            raise ValueError(f"Migration plan '{plan_id}' not found.")
        plan = dict(plan_row)

        # --- Fetch assessment for the app ---
        app_id = plan.get("legacy_app_id")
        assessment = None
        if app_id:
            assess_row = conn.execute(
                "SELECT * FROM migration_assessments WHERE legacy_app_id = ? "
                "ORDER BY rowid DESC LIMIT 1", (app_id,)
            ).fetchone()
            if assess_row:
                assessment = dict(assess_row)

        # --- Attempt to fetch digital thread links for compliance coverage ---
        compliance_links = []
        try:
            link_rows = conn.execute(
                "SELECT * FROM digital_thread_links WHERE project_id = ? "
                "AND link_type LIKE '%compliance%'",
                (plan.get("legacy_app_id", ""),),
            ).fetchall()
            compliance_links = [dict(r) for r in link_rows]
        except sqlite3.OperationalError:
            # Table may not exist; proceed without compliance links
            pass

    finally:
        conn.close()

    # --- Determine strategy and impact ---
    strategy = (plan.get("strategy") or "unknown").lower()
    ato_impact = "medium"  # default
    if assessment:
        ato_impact = assessment.get("ato_impact", "medium") or "medium"
    else:
        # Derive from strategy directly
        impact_map = {
            "rehost": "none", "replatform": "low", "refactor": "medium",
            "rearchitect": "high", "repurchase": "critical",
            "retire": "none", "retain": "none",
        }
        ato_impact = impact_map.get(strategy, "medium")

    controls = STRATEGY_CONTROL_FAMILIES.get(strategy, [])
    ato_desc = ATO_IMPACT_DESCRIPTIONS.get(ato_impact, "Impact assessment pending.")
    ato_weeks = STRATEGY_ATO_WEEKS.get(strategy, 0)

    # --- Build report ---
    lines = []

    plan_name = plan.get("plan_name", plan_id)
    lines.append(f"# ATO Impact Report: {plan_name}")
    lines.append("")
    lines.append(f"**Report Date:** {_now_iso()}")
    lines.append(f"**Classification:** {CUI_BANNER_TEXT}")
    lines.append(f"**Plan ID:** {plan_id}")
    lines.append(f"**Migration Strategy:** {strategy.upper()}")
    lines.append("")
    lines.append("---")
    lines.append("")

    # --- Section 1: Impact Level ---
    lines.append("## 1. Impact Level")
    lines.append("")
    lines.append(f"**ATO Impact Level:** {ato_impact.upper()}")
    lines.append("")
    lines.append(f"{ato_desc}")
    lines.append("")

    # Impact level guidance table
    lines.append("### Impact Level Reference")
    lines.append("")
    lines.append("| Level    | Description | Action Required |")
    lines.append("|----------|-------------|-----------------|")
    lines.append("| NONE     | No boundary change | No action |")
    lines.append("| LOW      | Minor infra changes | SCR submission |")
    lines.append("| MEDIUM   | Tech baseline change | ATO addendum |")
    lines.append("| HIGH     | New boundary | Full reassessment |")
    lines.append("| CRITICAL | System replacement | New ATO package |")
    lines.append("")
    lines.append("---")
    lines.append("")

    # --- Section 2: Controls Affected ---
    lines.append("## 2. Controls Affected")
    lines.append("")
    if controls:
        lines.append(f"The **{strategy.upper()}** strategy impacts **{len(controls)}** "
                     "NIST 800-53 control families:")
        lines.append("")
        lines.append("| Control ID | Family Description |")
        lines.append("|------------|-------------------|")
        control_descriptions = {
            "AC-2": "Account Management",
            "AC-3": "Access Enforcement",
            "AU-2": "Event Logging",
            "AU-3": "Content of Audit Records",
            "CA-2": "Control Assessments",
            "CM-2": "Baseline Configuration",
            "CM-3": "Configuration Change Control",
            "CM-8": "System Component Inventory",
            "IA-2": "Identification and Authentication",
            "IA-5": "Authenticator Management",
            "MP-6": "Media Sanitization",
            "SA-4": "Acquisition Process",
            "SA-10": "Developer Configuration Management",
            "SA-11": "Developer Testing and Evaluation",
            "SC-7": "Boundary Protection",
            "SC-8": "Transmission Confidentiality and Integrity",
            "SI-2": "Flaw Remediation",
            "SI-7": "Software, Firmware, and Information Integrity",
            "SI-12": "Information Management and Retention",
        }
        for ctrl in controls:
            desc = control_descriptions.get(ctrl, "See NIST 800-53 Rev 5")
            lines.append(f"| {ctrl} | {desc} |")
    else:
        lines.append("*No specific control families identified as impacted.*")
    lines.append("")
    lines.append("---")
    lines.append("")

    # --- Section 3: Coverage Analysis ---
    lines.append("## 3. Coverage Analysis")
    lines.append("")
    if compliance_links:
        covered_controls = set()
        for link in compliance_links:
            ctrl = link.get("target_id", "")
            if ctrl:
                covered_controls.add(ctrl)
        pre_coverage = len(covered_controls)
        affected_set = set(controls)
        gap_controls = affected_set - covered_controls
        pre_coverage - len(affected_set & covered_controls)

        lines.append(f"- **Pre-Migration Controls Documented:** {pre_coverage}")
        lines.append(f"- **Controls Requiring Update:** {len(affected_set)}")
        lines.append(f"- **Coverage Gaps Identified:** {len(gap_controls)}")
        lines.append("")
        if gap_controls:
            lines.append("**Control Gaps:**")
            for gap in sorted(gap_controls):
                desc = control_descriptions.get(gap, "")
                lines.append(f"- {gap}: {desc}")
            lines.append("")
    else:
        lines.append("*No existing compliance mapping data available. "
                     "Manual compliance assessment recommended.*")
        lines.append("")
        lines.append(f"Based on the **{strategy.upper()}** strategy, the following "
                     f"{len(controls)} control families will need documentation:")
        lines.append("")
        for ctrl in controls:
            lines.append(f"- {ctrl}")
    lines.append("")
    lines.append("---")
    lines.append("")

    # --- Section 4: Remediation Required ---
    lines.append("## 4. Remediation Required")
    lines.append("")

    remediation_actions = {
        "none": [
            "No compliance remediation required.",
        ],
        "low": [
            "Update SSP Appendix A (System Boundary Diagram) with new infrastructure.",
            "Submit Significant Change Request (SCR) to ISSM.",
            "Update CM-8 system component inventory.",
            "Verify SI-2 patch management covers new platform.",
        ],
        "medium": [
            "Update SSP Sections 1-3 (System Identification and Description).",
            "Revise SSP Section 13 (System and Communications Protection).",
            "Update SAR (Security Assessment Report) with new technology baseline.",
            "Review and update POAM for any new findings.",
            "Conduct developer testing per SA-11.",
            "Update STIG checklists for new framework version.",
            "Submit ATO addendum package to AO.",
        ],
        "high": [
            "Draft new SSP or major SSP revision for new architecture.",
            "Conduct full Security Control Assessment (SCA).",
            "Update all STIG benchmarks for new technology stack.",
            "Revise SAR with comprehensive findings.",
            "Create/update POAM with all open findings.",
            "Update network diagrams and data flow diagrams.",
            "Conduct penetration testing on new architecture.",
            "Submit full ATO reassessment package to AO.",
            "Plan for Interim ATO (IATO) during transition.",
        ],
        "critical": [
            "Develop complete new SSP for replacement system.",
            "Conduct full SCA against all applicable NIST controls.",
            "Complete STIG evaluation for all new system components.",
            "Develop new SAR documenting all assessment results.",
            "Create comprehensive POAM for any findings.",
            "Generate SBOM for all new system components.",
            "Conduct thorough penetration testing.",
            "Develop data migration security plan.",
            "Plan decommission security procedures for legacy system.",
            "Coordinate with ISSM/AO for new ATO timeline.",
            "Apply for IATO to cover migration transition period.",
            "Conduct independent verification and validation (IV&V).",
        ],
    }

    actions = remediation_actions.get(ato_impact, remediation_actions["medium"])
    for idx, action in enumerate(actions, 1):
        lines.append(f"{idx}. {action}")
    lines.append("")
    lines.append("---")
    lines.append("")

    # --- Section 5: Timeline Impact ---
    lines.append("## 5. Timeline Impact")
    lines.append("")
    lines.append(f"The **{strategy.upper()}** migration strategy adds an estimated "
                 f"**{ato_weeks} weeks** to the project timeline for compliance activities.")
    lines.append("")
    plan_timeline = plan.get("estimated_hours", 0) or 0
    if plan_timeline:
        plan_weeks = max(1, round(plan_timeline / 40))
        total_weeks = plan_weeks + ato_weeks
        lines.append("| Phase               | Duration (weeks) |")
        lines.append("|---------------------|-----------------|")
        lines.append(f"| Migration Execution | {plan_weeks} |")
        lines.append(f"| Compliance Work     | {ato_weeks} |")
        lines.append(f"| **Total**           | **{total_weeks}** |")
    else:
        lines.append(f"Additional compliance overhead: **{ato_weeks} weeks**")
    lines.append("")

    content = "\n".join(lines)
    filepath = out_dir / f"ato_impact_report_{plan_id}.md"
    return _write_report(filepath, content)


# ============================================================================
# 4. Executive Summary
# ============================================================================

def generate_executive_summary(app_id, output_dir=None, db_path=None):
    """Generate a one-page executive summary for leadership review.

    Provides a high-level overview of the migration assessment including
    the recommended strategy, cost, timeline, risk, ATO impact, a compact
    decision matrix, and key findings.

    Args:
        app_id:     Legacy application ID.
        output_dir: Directory to write the report (default: current dir).
        db_path:    Optional database path override.

    Returns:
        str: Absolute path to the generated executive summary file.

    Raises:
        ValueError: If the application or assessment is not found.
        FileNotFoundError: If the database does not exist.
    """
    out_dir = _ensure_dir(output_dir or ".")
    conn = _get_db(db_path)

    try:
        # --- Fetch application ---
        app_row = conn.execute(
            "SELECT * FROM legacy_applications WHERE id = ?", (app_id,)
        ).fetchone()
        if app_row is None:
            raise ValueError(f"Application '{app_id}' not found.")
        app = dict(app_row)

        # --- Fetch latest assessment ---
        assess_row = conn.execute(
            "SELECT * FROM migration_assessments WHERE legacy_app_id = ? "
            "ORDER BY rowid DESC LIMIT 1", (app_id,)
        ).fetchone()
        if assess_row is None:
            raise ValueError(
                f"No assessment found for '{app_id}'. Run seven_r_assessor.py first."
            )
        assessment = dict(assess_row)

        # --- Component count ---
        comp_count_row = conn.execute(
            "SELECT COUNT(*) AS cnt FROM legacy_components WHERE legacy_app_id = ?",
            (app_id,),
        ).fetchone()
        comp_count = comp_count_row["cnt"] if comp_count_row else 0

    finally:
        conn.close()

    # --- Derived values ---
    recommended = assessment.get("recommended_strategy", "N/A")
    risk_score = assessment.get("risk_score", 0.0) or 0.0
    risk_level = _risk_label(risk_score)
    cost_hours = assessment.get("cost_estimate_hours", 0) or 0
    timeline_weeks = assessment.get("timeline_weeks", 0) or 0
    ato_impact = assessment.get("ato_impact", "N/A")
    ato_weeks = STRATEGY_ATO_WEEKS.get(recommended, 0)

    evidence = _safe_json_loads(assessment.get("evidence"))
    strategy_scores = evidence.get("strategy_scores", {})
    fitness_results = evidence.get("fitness_results", {})

    # --- Build report ---
    lines = []

    lines.append(f"# Executive Summary: {app.get('name', app_id)}")
    lines.append("")
    lines.append(f"**Report Date:** {_now_iso()}")
    lines.append(f"**Classification:** {CUI_BANNER_TEXT}")
    lines.append(f"**Application ID:** {app_id}")
    lines.append("")
    lines.append("---")
    lines.append("")

    # --- High-Level Overview ---
    lines.append("## Overview")
    lines.append("")
    lines.append(
        f"**{app.get('name', app_id)}** is a {app.get('app_type', 'N/A')} application "
        f"built with {app.get('primary_language', 'N/A')} "
        f"{app.get('language_version', '')} / {app.get('framework', 'N/A')} "
        f"{app.get('framework_version', '')}. "
        f"It comprises {_format_number(app.get('loc_total'))} lines of code across "
        f"{_format_number(app.get('file_count'))} files with {comp_count} components."
    )
    lines.append("")
    lines.append("| Metric              | Value              |")
    lines.append("|---------------------|--------------------|")
    lines.append(f"| Recommended Strategy| **{recommended.upper()}** |")
    lines.append(f"| Estimated Cost      | {_format_number(cost_hours)} hours |")
    lines.append(f"| Timeline            | {timeline_weeks} weeks (+{ato_weeks} ATO) |")
    lines.append(f"| Risk Level          | {risk_level} ({risk_score:.2f}) |")
    lines.append(f"| ATO Impact          | {ato_impact.upper()} |")
    lines.append(f"| Maintainability     | {_format_number(app.get('maintainability_index'))} |")
    lines.append(f"| Tech Debt           | {_format_number(app.get('tech_debt_hours'))} hours |")
    lines.append("")
    lines.append("---")
    lines.append("")

    # --- Decision Matrix (compact) ---
    lines.append("## Decision Matrix")
    lines.append("")
    lines.append("| Strategy     | Score  | Cost (hrs) | Time (wks) | Risk    |")
    lines.append("|--------------|--------|------------|------------|---------|")

    strategy_names = [
        ("rehost", "Rehost"), ("replatform", "Replatform"),
        ("refactor", "Refactor"), ("rearchitect", "Rearchitect"),
        ("repurchase", "Repurchase"), ("retire", "Retire"),
        ("retain", "Retain"),
    ]
    for sid, sname in strategy_names:
        score_key = f"{sid}_score"
        score_val = assessment.get(score_key, 0.0) or 0.0
        # Approximate cost scaling for each strategy relative to recommended
        if sid == recommended:
            s_cost = cost_hours
            s_time = timeline_weeks
        else:
            ratio = (score_val / max(assessment.get(f"{recommended}_score", 0.0) or 1.0, 0.01))
            s_cost = int(cost_hours * max(0.5, 2.0 - ratio))
            s_time = max(2, int(timeline_weeks * max(0.5, 2.0 - ratio)))
        s_risk = _risk_label(risk_score * max(0.5, 2.0 - score_val))
        marker = " **" if sid == recommended else ""
        end_marker = "**" if sid == recommended else ""
        lines.append(
            f"| {marker}{sname}{end_marker} | {score_val:.4f} | "
            f"{_format_number(s_cost)} | {s_time} | {s_risk} |"
        )
    lines.append("")
    lines.append("---")
    lines.append("")

    # --- Key Findings ---
    lines.append("## Key Findings")
    lines.append("")

    findings = []

    # Finding 1: Strategy recommendation
    findings.append(
        f"The **{recommended.upper()}** strategy scored highest at "
        f"{assessment.get(recommended + '_score', 0.0):.4f}, "
        f"indicating the best fit for this application's profile and constraints."
    )

    # Finding 2: Complexity / maintainability
    maint = app.get("maintainability_index", 0) or 0
    app.get("complexity_score", 0) or 0
    if maint < 30:
        findings.append(
            f"Maintainability index is **{maint:.1f}** (poor). The codebase presents "
            "significant technical debt that will increase migration effort and risk."
        )
    elif maint > 60:
        findings.append(
            f"Maintainability index is **{maint:.1f}** (good). The codebase is "
            "well-structured, which reduces migration risk and effort."
        )
    else:
        findings.append(
            f"Maintainability index is **{maint:.1f}** (moderate). Some refactoring "
            "of high-complexity components is advisable before or during migration."
        )

    # Finding 3: ATO impact
    if ato_impact in ("high", "critical"):
        findings.append(
            f"ATO impact is **{ato_impact.upper()}**, adding approximately "
            f"{ato_weeks} weeks for compliance work. Coordinate with ISSM early."
        )
    elif ato_impact == "none":
        findings.append(
            "ATO impact is **NONE**. The existing authorization boundary "
            "is unaffected by this migration strategy."
        )

    # Finding 4: Tech debt
    tech_debt = app.get("tech_debt_hours", 0) or 0
    if tech_debt > 500:
        findings.append(
            f"Technical debt is estimated at **{tech_debt:.0f} hours**. "
            "Consider allocating dedicated sprints for debt reduction."
        )

    # Finding 5: Close scores
    if strategy_scores:
        sorted_scores = sorted(strategy_scores.items(), key=lambda x: x[1], reverse=True)
        if len(sorted_scores) >= 2:
            top_score = sorted_scores[0][1]
            second_score = sorted_scores[1][1]
            delta = top_score - second_score
            if delta < 0.05:
                findings.append(
                    f"The margin between top strategies is narrow ({delta:.4f}). "
                    "Manual review of business context and team capacity is recommended "
                    "before finalizing the strategy."
                )

    for idx, finding in enumerate(findings[:5], 1):
        lines.append(f"{idx}. {finding}")
    lines.append("")
    lines.append("---")
    lines.append("")

    # --- Recommendation ---
    lines.append("## Recommendation")
    lines.append("")

    rec_fitness = fitness_results.get(recommended, {})
    strengths = [k for k, v in rec_fitness.items() if v >= 0.8]
    weaknesses = [k for k, v in rec_fitness.items() if v <= 0.2]

    lines.append(
        f"We recommend proceeding with the **{recommended.upper()}** strategy for "
        f"**{app.get('name', app_id)}**. This strategy provides the optimal balance "
        f"of cost ({_format_number(cost_hours)} hours), timeline ({timeline_weeks} weeks), "
        f"and risk ({risk_level}) given the application's technical profile."
    )
    lines.append("")
    if strengths:
        lines.append(
            f"The application demonstrates strong fitness in: "
            f"{', '.join(s.replace('_', ' ') for s in strengths)}."
        )
    if weaknesses:
        lines.append(
            f"Areas requiring attention: "
            f"{', '.join(w.replace('_', ' ') for w in weaknesses)}."
        )
    lines.append("")

    content = "\n".join(lines)
    filepath = out_dir / f"executive_summary_{app_id}.md"
    return _write_report(filepath, content)


# ============================================================================
# 5. Generate All Reports
# ============================================================================

def generate_all_reports(app_id, plan_id=None, pi_number=None,
                         output_dir=".", db_path=None):
    """Generate all applicable migration reports.

    Always generates: assessment report, executive summary.
    If plan_id is provided: also generates progress report and ATO impact report.
    Finally generates a report_index.md linking all generated reports.

    Args:
        app_id:     Legacy application ID.
        plan_id:    Optional migration plan ID.
        pi_number:  Optional PI number for progress report.
        output_dir: Directory to write reports (default: current dir).
        db_path:    Optional database path override.

    Returns:
        dict: Mapping of report type to absolute file path, plus 'index' key.
    """
    out_dir = _ensure_dir(output_dir)
    results = {}
    errors = {}

    # --- Always generate assessment and executive summary ---
    try:
        results["assessment"] = generate_assessment_report(
            app_id, output_dir=str(out_dir), db_path=db_path
        )
    except Exception as exc:
        errors["assessment"] = str(exc)

    try:
        results["executive"] = generate_executive_summary(
            app_id, output_dir=str(out_dir), db_path=db_path
        )
    except Exception as exc:
        errors["executive"] = str(exc)

    # --- Conditionally generate plan-based reports ---
    if plan_id:
        try:
            results["progress"] = generate_progress_report(
                plan_id, pi_number=pi_number, output_dir=str(out_dir),
                db_path=db_path
            )
        except Exception as exc:
            errors["progress"] = str(exc)

        try:
            results["ato_impact"] = generate_ato_impact_report(
                plan_id, output_dir=str(out_dir), db_path=db_path
            )
        except Exception as exc:
            errors["ato_impact"] = str(exc)

    # --- Generate report index ---
    index_lines = []
    index_lines.append("# Migration Report Index")
    index_lines.append("")
    index_lines.append(f"**Application ID:** {app_id}")
    if plan_id:
        index_lines.append(f"**Plan ID:** {plan_id}")
    if pi_number:
        index_lines.append(f"**Program Increment:** {pi_number}")
    index_lines.append(f"**Generated:** {_now_iso()}")
    index_lines.append(f"**Classification:** {CUI_BANNER_TEXT}")
    index_lines.append("")
    index_lines.append("---")
    index_lines.append("")
    index_lines.append("## Reports")
    index_lines.append("")

    report_labels = {
        "assessment": "7R Assessment Report",
        "executive": "Executive Summary",
        "progress": "Migration Progress Report",
        "ato_impact": "ATO Impact Report",
    }

    for key in ["assessment", "executive", "progress", "ato_impact"]:
        label = report_labels.get(key, key)
        if key in results:
            filename = Path(results[key]).name
            index_lines.append(f"- [{label}]({filename})")
        elif key in errors:
            index_lines.append(f"- {label} -- **FAILED:** {errors[key]}")

    index_lines.append("")

    if errors:
        index_lines.append("## Errors")
        index_lines.append("")
        for key, err in errors.items():
            index_lines.append(f"- **{report_labels.get(key, key)}:** {err}")
        index_lines.append("")

    index_content = "\n".join(index_lines)
    index_path = out_dir / "report_index.md"
    results["index"] = _write_report(index_path, index_content)

    return results


# ============================================================================
# CLI entry point
# ============================================================================

def main():
    """CLI entry point for the migration report generator.

    Supports generation of individual report types or all reports at once.
    Outputs file paths on success; optional --json flag for machine-readable
    output.
    """
    parser = argparse.ArgumentParser(
        description=(
            "Migration Report Generator -- Produces CUI-marked reports for "
            "7R assessments, migration progress, ATO impact, and executive "
            "summaries within the ICDEV DoD modernization system."
        ),
        epilog=textwrap.dedent("""\
            Examples:
              %(prog)s --app-id A-001 --type assessment
              %(prog)s --plan-id MP-001 --type progress --pi PI-3
              %(prog)s --plan-id MP-001 --type ato-impact
              %(prog)s --app-id A-001 --type executive
              %(prog)s --app-id A-001 --plan-id MP-001 --type all --output-dir ./reports

            Classification: CUI // SP-CTI
        """),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--app-id",
        default=None,
        help="Legacy application ID (required for assessment, executive, all).",
    )
    parser.add_argument(
        "--plan-id",
        default=None,
        help="Migration plan ID (required for progress, ato-impact).",
    )
    parser.add_argument(
        "--pi",
        default=None,
        dest="pi_number",
        help="Program Increment number for progress reports (optional).",
    )
    parser.add_argument(
        "--output-dir",
        default=".",
        help="Directory to write generated report files (default: current dir).",
    )
    parser.add_argument(
        "--type",
        choices=["assessment", "progress", "ato-impact", "executive", "all"],
        default="all",
        dest="report_type",
        help="Type of report to generate (default: all).",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        dest="json_output",
        help="Output result as JSON (file paths and metadata).",
    )
    parser.add_argument(
        "--db-path",
        default=None,
        help="Override path to icdev.db database.",
    )

    args = parser.parse_args()

    # --- Validate required arguments per report type ---
    if args.report_type in ("assessment", "executive"):
        if not args.app_id:
            parser.error(f"--app-id is required for --type {args.report_type}")

    if args.report_type in ("progress", "ato-impact"):
        if not args.plan_id:
            parser.error(f"--plan-id is required for --type {args.report_type}")

    if args.report_type == "all":
        if not args.app_id:
            parser.error("--app-id is required for --type all")

    # --- Generate requested report(s) ---
    try:
        if args.report_type == "assessment":
            filepath = generate_assessment_report(
                args.app_id, output_dir=args.output_dir, db_path=args.db_path
            )
            if args.json_output:
                print(json.dumps({
                    "type": "assessment",
                    "app_id": args.app_id,
                    "file": filepath,
                    "generated_at": _now_iso(),
                }, indent=2))
            else:
                print(f"Assessment report generated: {filepath}")

        elif args.report_type == "progress":
            filepath = generate_progress_report(
                args.plan_id, pi_number=args.pi_number,
                output_dir=args.output_dir, db_path=args.db_path
            )
            if args.json_output:
                print(json.dumps({
                    "type": "progress",
                    "plan_id": args.plan_id,
                    "pi_number": args.pi_number,
                    "file": filepath,
                    "generated_at": _now_iso(),
                }, indent=2))
            else:
                print(f"Progress report generated: {filepath}")

        elif args.report_type == "ato-impact":
            filepath = generate_ato_impact_report(
                args.plan_id, output_dir=args.output_dir, db_path=args.db_path
            )
            if args.json_output:
                print(json.dumps({
                    "type": "ato-impact",
                    "plan_id": args.plan_id,
                    "file": filepath,
                    "generated_at": _now_iso(),
                }, indent=2))
            else:
                print(f"ATO impact report generated: {filepath}")

        elif args.report_type == "executive":
            filepath = generate_executive_summary(
                args.app_id, output_dir=args.output_dir, db_path=args.db_path
            )
            if args.json_output:
                print(json.dumps({
                    "type": "executive",
                    "app_id": args.app_id,
                    "file": filepath,
                    "generated_at": _now_iso(),
                }, indent=2))
            else:
                print(f"Executive summary generated: {filepath}")

        elif args.report_type == "all":
            results = generate_all_reports(
                args.app_id, plan_id=args.plan_id, pi_number=args.pi_number,
                output_dir=args.output_dir, db_path=args.db_path
            )
            if args.json_output:
                print(json.dumps({
                    "type": "all",
                    "app_id": args.app_id,
                    "plan_id": args.plan_id,
                    "pi_number": args.pi_number,
                    "reports": results,
                    "report_count": len(results),
                    "generated_at": _now_iso(),
                }, indent=2))
            else:
                print(f"Migration reports generated for application: {args.app_id}")
                print(f"Output directory: {args.output_dir}")
                print(f"Reports generated: {len(results)}")
                for rtype, rpath in results.items():
                    print(f"  {rtype}: {rpath}")

    except FileNotFoundError as exc:
        if args.json_output:
            print(json.dumps({"error": str(exc)}, indent=2))
        else:
            print(f"Error: {exc}", file=sys.stderr)
        sys.exit(1)
    except ValueError as exc:
        if args.json_output:
            print(json.dumps({"error": str(exc)}, indent=2))
        else:
            print(f"Error: {exc}", file=sys.stderr)
        sys.exit(1)
    except Exception as exc:
        if args.json_output:
            print(json.dumps({"error": str(exc)}, indent=2))
        else:
            print(f"Unexpected error: {exc}", file=sys.stderr)
        sys.exit(2)


if __name__ == "__main__":
    main()
# [TEMPLATE: CUI // SP-CTI]
