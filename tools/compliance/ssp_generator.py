#!/usr/bin/env python3
# CUI // SP-CTI
"""Generate a System Security Plan (SSP) from template and project data.
Fills {{variables}} from project data in icdev.db, pulls control implementations
from project_controls table, applies CUI markings, saves to project compliance
directory, records in ssp_documents table, and logs an audit event."""

import argparse
import json
import re
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent.parent
DB_PATH = BASE_DIR / "data" / "icdev.db"

try:
    from tools.compat.db_utils import get_db_connection
except ImportError:
    get_db_connection = None
SSP_TEMPLATE_PATH = BASE_DIR / "context" / "compliance" / "ssp_template.md"
CONTROLS_PATH = BASE_DIR / "context" / "compliance" / "nist_800_53.json"

# All 17 FIPS 200 families tracked in the SSP
CONTROL_FAMILIES = {
    "AC": "Access Control",
    "AT": "Awareness and Training",
    "AU": "Audit and Accountability",
    "CA": "Assessment, Authorization, and Monitoring",
    "CM": "Configuration Management",
    "CP": "Contingency Planning",
    "IA": "Identification and Authentication",
    "IR": "Incident Response",
    "MA": "Maintenance",
    "MP": "Media Protection",
    "PE": "Physical and Environmental Protection",
    "PL": "Planning",
    "PS": "Personnel Security",
    "RA": "Risk Assessment",
    "SA": "System and Services Acquisition",
    "SC": "System and Communications Protection",
    "SI": "System and Information Integrity",
}

# Impact level to baseline mapping
_IL_BASELINE_MAP = {"IL2": "Low", "IL4": "Moderate", "IL5": "High", "IL6": "High"}


def _get_connection(db_path=None):
    """Get a database connection."""
    if get_db_connection:
        return get_db_connection(db_path or DB_PATH, validate=True)
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
    """Load the SSP template markdown."""
    path = template_path or SSP_TEMPLATE_PATH
    if not path.exists():
        raise FileNotFoundError(f"SSP template not found: {path}")
    with open(path, "r", encoding="utf-8") as f:
        return f.read()


def _load_nist_controls():
    """Load NIST controls reference."""
    if not CONTROLS_PATH.exists():
        return {}
    with open(CONTROLS_PATH, "r", encoding="utf-8") as f:
        data = json.load(f)
    return {c["id"]: c for c in data.get("controls", [])}


def _get_project_data(conn, project_id):
    """Load project record from database."""
    row = conn.execute(
        "SELECT * FROM projects WHERE id = ?", (project_id,)
    ).fetchone()
    if not row:
        raise ValueError(f"Project '{project_id}' not found in database.")
    return dict(row)


def _get_control_implementations(conn, project_id):
    """Get all control mappings for a project."""
    rows = conn.execute(
        "SELECT * FROM project_controls WHERE project_id = ? ORDER BY control_id",
        (project_id,),
    ).fetchall()
    return [dict(r) for r in rows]


def _get_stig_summary(conn, project_id):
    """Get STIG findings summary."""
    rows = conn.execute(
        """SELECT severity, status, COUNT(*) as cnt
           FROM stig_findings WHERE project_id = ?
           GROUP BY severity, status""",
        (project_id,),
    ).fetchall()
    return [dict(r) for r in rows]


def _get_poam_summary(conn, project_id):
    """Get POAM items summary."""
    rows = conn.execute(
        """SELECT severity, status, COUNT(*) as cnt
           FROM poam_items WHERE project_id = ?
           GROUP BY severity, status""",
        (project_id,),
    ).fetchall()
    return [dict(r) for r in rows]


def _get_fips199_categorization(conn, project_id):
    """Get the current FIPS 199 categorization for dynamic SSP baseline.
    Looks for approved first, then draft. Returns None if no categorization."""
    if not conn or not project_id:
        return None
    try:
        row = conn.execute(
            """SELECT confidentiality_impact, integrity_impact, availability_impact,
                      overall_categorization, baseline_selected, status
               FROM fips199_categorizations
               WHERE project_id = ? AND status IN ('approved', 'draft')
               ORDER BY CASE status WHEN 'approved' THEN 1 ELSE 2 END,
                        categorization_date DESC
               LIMIT 1""",
            (project_id,),
        ).fetchone()
        return dict(row) if row else None
    except Exception:
        return None  # Table may not exist yet


def _build_system_info(project, system_name, system_info=None, conn=None):
    """Build the variable substitution dictionary from project data and overrides.
    Uses FIPS 199 categorization from DB for dynamic baseline selection."""
    now = datetime.now(timezone.utc)

    # Dynamic FIPS 199 categorization lookup
    cat = _get_fips199_categorization(conn, project.get("id"))
    if cat:
        conf_impact = cat["confidentiality_impact"]
        int_impact = cat["integrity_impact"]
        avail_impact = cat["availability_impact"]
        overall = cat["overall_categorization"]
        baseline = cat.get("baseline_selected") or overall
    else:
        # Fallback: try project-level columns, then IL mapping
        conf_impact = project.get("fips199_confidentiality") or "Moderate"
        int_impact = project.get("fips199_integrity") or "Moderate"
        avail_impact = project.get("fips199_availability") or "Low"
        overall = project.get("fips199_overall")
        if not overall:
            il = project.get("impact_level", "IL5")
            overall = _IL_BASELINE_MAP.get(il, "Moderate")
        baseline = overall

    info = {
        # Section 1
        "system_name": system_name or project.get("name", "UNNAMED SYSTEM"),
        "system_abbreviation": "",
        "system_id": project.get("id", ""),

        # Section 2 — dynamic from FIPS 199
        "confidentiality_impact": conf_impact,
        "integrity_impact": int_impact,
        "availability_impact": avail_impact,
        "overall_categorization": overall,
        "cui_category": "CTI",
        "cui_designation": "CUI // SP-CTI",

        # Section 3
        "system_owner_name": "{{system_owner_name}}",
        "system_owner_title": "{{system_owner_title}}",
        "system_owner_organization": "{{system_owner_organization}}",
        "system_owner_address": "{{system_owner_address}}",
        "system_owner_email": "{{system_owner_email}}",
        "system_owner_phone": "{{system_owner_phone}}",

        # Section 4
        "authorizing_official_name": "{{authorizing_official_name}}",
        "authorizing_official_title": "{{authorizing_official_title}}",
        "authorizing_official_organization": "{{authorizing_official_organization}}",
        "authorizing_official_email": "{{authorizing_official_email}}",
        "authorizing_official_phone": "{{authorizing_official_phone}}",

        # Section 7
        "operational_status": "Under Development",
        "operational_date": "TBD",
        "authorization_date": "TBD",
        "authorization_termination_date": "TBD",

        # Section 8
        "system_type": project.get("type", "webapp"),
        "cloud_service_model": "PaaS",
        "cloud_deployment_model": "Government Community Cloud",

        # Section 9
        "system_purpose": project.get("description", "{{system_purpose}}"),

        # Section 12
        "fisma_applicability": "Applicable",
        "nist_800_53_applicability": f"Applicable — Rev 5 {baseline} Baseline",
        "nist_800_171_applicability": "Applicable",
        "dfars_applicability": "Applicable — DFARS 252.204-7012",
        "cmmc_applicability": "Level 2 Required",
        "dod_cui_applicability": "Applicable",
        "fedramp_applicability": "Aligned",

        # Section 13 — dynamic from FIPS 199
        "control_baseline": baseline,
        "impact_level": baseline,

        # Section 14
        "date_prepared": now.strftime("%Y-%m-%d"),
        "document_version": "1.0",
        "plan_prepared_by": "ICDEV Compliance Engine",
        "next_review_date": f"{now.year + 1}-{now.strftime('%m-%d')}",
        "version_1": "1.0",
        "version_1_date": now.strftime("%Y-%m-%d"),
        "version_1_author": "ICDEV Compliance Engine",
        "version_1_changes": "Initial SSP generation",

        # Classification
        "classification": "CUI // SP-CTI",
        "icdev_version": "1.0",
        "generation_date": now.strftime("%Y-%m-%d %H:%M UTC"),
    }

    # Override with any user-provided system_info
    if system_info and isinstance(system_info, dict):
        info.update(system_info)

    return info


def _load_pre_generated_narratives(conn, project_id):
    """Load pre-generated control narratives from the control_narratives table.

    Returns a dict of {control_id: narrative_text} or empty dict if table
    doesn't exist or no narratives are found.
    """
    narratives = {}
    if not conn or not project_id:
        return narratives
    try:
        rows = conn.execute(
            "SELECT control_id, narrative_text FROM control_narratives "
            "WHERE project_id = ? ORDER BY control_id",
            (project_id,),
        ).fetchall()
        for row in rows:
            narratives[row["control_id"] if hasattr(row, "keys") else row[0]] = (
                row["narrative_text"] if hasattr(row, "keys") else row[1]
            )
    except Exception:
        pass  # Table may not exist on older DBs
    return narratives


def _build_control_section(implementations, nist_controls, conn=None, project_id=None):
    """Build the Section 15 control implementation narratives.

    Checks the control_narratives table first for pre-generated narratives
    (from narrative_generator.py). Falls back to implementation descriptions.
    """
    if not implementations:
        return (
            "*No control implementations have been mapped for this project yet. "
            "Use `python tools/compliance/control_mapper.py` to create mappings.*"
        )

    # Load pre-generated narratives (Phase 3 — Enhancement #10)
    pre_narratives = _load_pre_generated_narratives(conn, project_id)

    lines = []
    # Group by family
    by_family = {}
    for impl in implementations:
        fam = impl["control_id"].split("-")[0] if "-" in impl["control_id"] else "Other"
        by_family.setdefault(fam, []).append(impl)

    for fam in sorted(by_family.keys()):
        fam_name = CONTROL_FAMILIES.get(fam, fam)
        lines.append(f"### {fam}: {fam_name}")
        lines.append("")

        for impl in sorted(by_family[fam], key=lambda x: x["control_id"]):
            ctrl_ref = nist_controls.get(impl["control_id"], {})
            title = ctrl_ref.get("title", "Unknown Control")

            lines.append(f"#### {impl['control_id']}: {title}")
            lines.append("")
            lines.append(f"**Implementation Status:** {impl['implementation_status']}")
            lines.append("")
            lines.append(f"**Responsible Role:** {impl.get('responsible_role') or 'TBD'}")
            lines.append("")

            # Use pre-generated narrative if available, otherwise fall back
            pre_narr = pre_narratives.get(impl["control_id"])
            if pre_narr:
                lines.append("**Implementation Narrative:**")
                lines.append("")
                lines.append(pre_narr)
            else:
                lines.append("**Implementation Description:**")
                lines.append("")
                desc = impl.get("implementation_description") or "*To be documented.*"
                lines.append(desc)
            lines.append("")

            evidence = impl.get("evidence_path")
            if evidence:
                lines.append(f"**Evidence / Artifacts:** {evidence}")
            else:
                lines.append("**Evidence / Artifacts:** *To be collected.*")
            lines.append("")
            lines.append("---")
            lines.append("")

    return "\n".join(lines)


def _build_family_summary(implementations, nist_controls):
    """Build the Section 13 family summary counts."""
    counts = {}
    for fam_code in CONTROL_FAMILIES:
        total_in_nist = sum(1 for cid in nist_controls if cid.startswith(f"{fam_code}-"))
        family_impls = [
            i for i in implementations if i["control_id"].startswith(f"{fam_code}-")
        ]
        implemented = sum(
            1 for i in family_impls if i["implementation_status"] == "implemented"
        )
        planned = sum(
            1 for i in family_impls
            if i["implementation_status"] in ("planned", "partially_implemented")
        )
        na = sum(
            1 for i in family_impls if i["implementation_status"] == "not_applicable"
        )

        prefix = fam_code.lower()
        counts[f"{prefix}_total"] = str(total_in_nist)
        counts[f"{prefix}_implemented"] = str(implemented)
        counts[f"{prefix}_planned"] = str(planned)
        counts[f"{prefix}_na"] = str(na)

    # Overall totals
    total_required = sum(
        int(counts.get(f"{f.lower()}_total", "0")) for f in CONTROL_FAMILIES
    )
    total_implemented = sum(
        int(counts.get(f"{f.lower()}_implemented", "0")) for f in CONTROL_FAMILIES
    )
    total_planned = sum(
        int(counts.get(f"{f.lower()}_planned", "0")) for f in CONTROL_FAMILIES
    )
    total_na = sum(
        int(counts.get(f"{f.lower()}_na", "0")) for f in CONTROL_FAMILIES
    )

    counts["total_controls_required"] = str(total_required)
    counts["controls_implemented"] = str(total_implemented)
    counts["controls_planned"] = str(total_planned)
    counts["controls_not_applicable"] = str(total_na)

    return counts


def _substitute_variables(template, variables):
    """Replace {{variable_name}} placeholders in the template with actual values."""
    def replacer(match):
        key = match.group(1).strip()
        return str(variables.get(key, match.group(0)))
    return re.sub(r"\{\{(\w+)\}\}", replacer, template)


def _log_audit_event(conn, project_id, action, details, file_path):
    """Log an audit trail event for SSP generation."""
    try:
        conn.execute(
            """INSERT INTO audit_trail
               (project_id, event_type, actor, action, details,
                affected_files, classification)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (
                project_id,
                "ssp_generated",
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


def generate_ssp(
    project_id,
    system_name=None,
    system_info=None,
    template_path=None,
    output_path=None,
    db_path=None,
):
    """Generate a complete System Security Plan for a project.

    Args:
        project_id: The project identifier
        system_name: Override system name (defaults to project name)
        system_info: Dict of additional variable overrides
        template_path: Path to SSP template (default: context/compliance/ssp_template.md)
        output_path: Override output file path
        db_path: Override database path

    Returns:
        Path to the generated SSP document
    """
    conn = _get_connection(db_path)
    try:
        # Load project data
        project = _get_project_data(conn, project_id)

        # Load template
        template = _load_template(template_path)

        # Load NIST controls reference
        nist_controls = _load_nist_controls()

        # Load control implementations
        implementations = _get_control_implementations(conn, project_id)

        # Build variables dict (conn passed for dynamic FIPS 199 baseline)
        variables = _build_system_info(
            project, system_name or project.get("name"), system_info, conn=conn
        )

        # Build control family summary counts
        family_counts = _build_family_summary(implementations, nist_controls)
        variables.update(family_counts)

        # Build Section 15 control implementation narratives
        # Checks control_narratives table for pre-generated narratives first
        control_section = _build_control_section(
            implementations, nist_controls, conn=conn, project_id=project_id
        )
        variables["control_implementations"] = control_section

        # Substitute all variables in template
        ssp_content = _substitute_variables(template, variables)

        # Determine output path
        if output_path:
            out_file = Path(output_path)
        else:
            dir_path = project.get("directory_path", "")
            if dir_path:
                out_dir = Path(dir_path) / "compliance"
            else:
                out_dir = BASE_DIR / ".tmp" / "compliance" / project_id
            out_dir.mkdir(parents=True, exist_ok=True)
            timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
            out_file = out_dir / f"ssp_{project_id}_{timestamp}.md"

        out_file.parent.mkdir(parents=True, exist_ok=True)

        with open(out_file, "w", encoding="utf-8") as f:
            f.write(ssp_content)

        # Determine version number
        existing = conn.execute(
            """SELECT MAX(CAST(version AS REAL)) as max_ver
               FROM ssp_documents WHERE project_id = ?""",
            (project_id,),
        ).fetchone()
        max_ver = existing["max_ver"] if existing and existing["max_ver"] else 0.0
        new_version = f"{max_ver + 1.0:.1f}"

        # Record in ssp_documents table
        conn.execute(
            """INSERT INTO ssp_documents
               (project_id, version, system_name, content, file_path,
                classification, status)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (
                project_id,
                new_version,
                variables["system_name"],
                ssp_content,
                str(out_file),
                "CUI",
                "draft",
            ),
        )
        conn.commit()

        # Log audit event
        _log_audit_event(conn, project_id, f"SSP v{new_version} generated", {
            "version": new_version,
            "system_name": variables["system_name"],
            "controls_mapped": len(implementations),
            "output_file": str(out_file),
        }, out_file)

        print("SSP generated successfully:")
        print(f"  File: {out_file}")
        print(f"  Version: {new_version}")
        print(f"  System: {variables['system_name']}")
        print(f"  Controls mapped: {len(implementations)}")

        return str(out_file)

    finally:
        conn.close()


def main():
    parser = argparse.ArgumentParser(
        description="Generate a System Security Plan (SSP)"
    )
    parser.add_argument("--project-id", "--project", required=True, help="Project ID", dest="project_id")
    parser.add_argument("--system-name", help="System name (overrides project name)")
    parser.add_argument("--template", help="Path to SSP template")
    parser.add_argument("--output", help="Output file path")
    parser.add_argument("--db", help="Database path")
    parser.add_argument(
        "--system-info", help="JSON string of additional variable overrides"
    )
    parser.add_argument("--json", action="store_true", dest="json_output", help="JSON output")
    args = parser.parse_args()

    system_info = None
    if args.system_info:
        try:
            system_info = json.loads(args.system_info)
        except json.JSONDecodeError as e:
            print(f"ERROR: Invalid JSON in --system-info: {e}", file=sys.stderr)
            sys.exit(1)

    try:
        path = generate_ssp(
            project_id=args.project_id,
            system_name=args.system_name,
            system_info=system_info,
            template_path=Path(args.template) if args.template else None,
            output_path=args.output,
            db_path=Path(args.db) if args.db else None,
        )
        print(f"\nSSP document path: {path}")
    except (FileNotFoundError, ValueError) as e:
        print(f"ERROR: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
