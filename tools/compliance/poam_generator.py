#!/usr/bin/env python3
# CUI // SP-CTI
"""Generate Plan of Action & Milestones (POA&M) from findings.
Pulls open findings from stig_findings and vulnerability scans,
formats into POAM template, applies CUI markings, saves to project
compliance directory, inserts into poam_items table, and logs audit event."""

import argparse
import json
import sqlite3
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent.parent
DB_PATH = BASE_DIR / "data" / "icdev.db"
POAM_TEMPLATE_PATH = BASE_DIR / "context" / "compliance" / "poam_template.md"

# Remediation timelines by severity (days)
REMEDIATION_TIMELINES = {
    "critical": 15,
    "high": 30,
    "moderate": 90,
    "low": 180,
    "CAT1": 15,
    "CAT2": 90,
    "CAT3": 180,
}


def _get_connection(db_path=None):
    """Get a database connection."""
    path = db_path or DB_PATH
    if not path.exists():
        raise FileNotFoundError(
            f"Database not found: {path}\n"
            "Run: python tools/db/init_icdev_db.py"
        )
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    return conn


def _get_project(conn, project_id):
    """Load project data."""
    row = conn.execute(
        "SELECT * FROM projects WHERE id = ?", (project_id,)
    ).fetchone()
    if not row:
        raise ValueError(f"Project '{project_id}' not found.")
    return dict(row)


def _get_stig_findings(conn, project_id):
    """Get all open STIG findings for a project."""
    rows = conn.execute(
        """SELECT * FROM stig_findings
           WHERE project_id = ? AND status = 'Open'
           ORDER BY
             CASE severity
               WHEN 'CAT1' THEN 1
               WHEN 'CAT2' THEN 2
               WHEN 'CAT3' THEN 3
             END,
             finding_id""",
        (project_id,),
    ).fetchall()
    return [dict(r) for r in rows]


def _get_existing_poam_items(conn, project_id):
    """Get existing POAM items for deduplication."""
    rows = conn.execute(
        "SELECT weakness_id FROM poam_items WHERE project_id = ?",
        (project_id,),
    ).fetchall()
    return {r["weakness_id"] for r in rows}


def _stig_severity_to_poam(stig_severity):
    """Map STIG severity (CAT1/CAT2/CAT3) to POAM severity."""
    mapping = {"CAT1": "critical", "CAT2": "moderate", "CAT3": "low"}
    return mapping.get(stig_severity, "moderate")


def _get_milestone_date(severity):
    """Calculate milestone date based on severity."""
    days = REMEDIATION_TIMELINES.get(severity, 90)
    return (datetime.now(timezone.utc) + timedelta(days=days)).strftime("%Y-%m-%d")


def _load_cui_config():
    """Load CUI marking configuration."""
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


def _log_audit_event(conn, project_id, action, details, file_path):
    """Log an audit trail event."""
    try:
        conn.execute(
            """INSERT INTO audit_trail
               (project_id, event_type, actor, action, details,
                affected_files, classification)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (
                project_id,
                "poam_generated",
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


def _build_poam_table_row(item_num, item):
    """Build a single POAM table row."""
    return (
        f"| POAM-{item_num:04d} "
        f"| {item['weakness']} "
        f"| {item['severity']} "
        f"| {item.get('control_id', 'N/A')} "
        f"| {item['description'][:60]}... "
        f"| {item['corrective_action'][:50]}... "
        f"| {item.get('resources', 'Staff time')} "
        f"| {item['milestone_date']} "
        f"| {item['status']} "
        f"| {item.get('completion_date', '')} "
        f"| {item.get('responsible', 'TBD')} |"
    )


def _build_poam_detail(item_num, item):
    """Build a detailed POAM item section."""
    lines = [
        f"### POAM-{item_num:04d}: {item['weakness']}",
        "",
        f"**Severity:** {item['severity']}",
        "",
        f"**Related NIST 800-53 Control:** {item.get('control_id', 'N/A')}",
        "",
        f"**Source:** {item.get('source', 'STIG Assessment')}",
        "",
        f"**Date Identified:** {item.get('date_identified', datetime.now(timezone.utc).strftime('%Y-%m-%d'))}",
        "",
        "**Deficiency Description:**",
        "",
        item["description"],
        "",
        "**Corrective Action Plan:**",
        "",
        item["corrective_action"],
        "",
        f"**Resources Required:** {item.get('resources', 'Staff time, testing environment')}",
        "",
        "**Milestones:**",
        "",
        "| Milestone | Target Date | Status | Actual Date |",
        "|-----------|-------------|--------|-------------|",
        f"| Remediation plan developed | {item['milestone_date']} | open | |",
        f"| Fix implemented | {item['milestone_date']} | open | |",
        f"| Verification testing | {item['milestone_date']} | open | |",
        "",
        f"**Status:** {item['status']}",
        "",
        f"**Responsible Party:** {item.get('responsible', 'TBD')}",
        "",
        "---",
        "",
    ]
    return "\n".join(lines)


def generate_poam(project_id, output_path=None, db_path=None):
    """Generate a POA&M document from findings.

    Args:
        project_id: The project identifier
        output_path: Override output file path
        db_path: Override database path

    Returns:
        Path to the generated POAM document
    """
    conn = _get_connection(db_path)
    try:
        project = _get_project(conn, project_id)

        # Collect findings
        stig_findings = _get_stig_findings(conn, project_id)
        existing_ids = _get_existing_poam_items(conn, project_id)

        # Build POAM items from STIG findings
        poam_items = []
        for finding in stig_findings:
            weakness_id = f"STIG-{finding['finding_id']}"
            poam_severity = _stig_severity_to_poam(finding["severity"])

            poam_items.append({
                "weakness_id": weakness_id,
                "weakness": finding["title"],
                "severity": poam_severity,
                "control_id": "",  # Will be populated if control mapping exists
                "description": finding.get("description", finding["title"]),
                "corrective_action": finding.get("fix_text", "Apply remediation per STIG guidance."),
                "resources": "Staff time, testing environment",
                "milestone_date": _get_milestone_date(poam_severity),
                "status": "open",
                "completion_date": "",
                "responsible": "Security Engineering Team",
                "source": f"STIG Assessment ({finding['stig_id']})",
                "date_identified": finding.get("created_at", datetime.now(timezone.utc).strftime("%Y-%m-%d")),
                "stig_severity": finding["severity"],
            })

        # Build severity summary
        severity_counts = {"critical": 0, "high": 0, "moderate": 0, "low": 0}
        for item in poam_items:
            sev = item["severity"]
            severity_counts[sev] = severity_counts.get(sev, 0) + 1

        # Build CUI document
        cui_config = _load_cui_config()
        header = cui_config.get("document_header", "CUI // SP-CTI").strip()
        footer = cui_config.get("document_footer", "CUI // SP-CTI").strip()
        now = datetime.now(timezone.utc)

        lines = [
            header,
            "",
            "# PLAN OF ACTION AND MILESTONES (POA&M)",
            "",
            f"**System Name:** {project.get('name', project_id)}",
            "",
            f"**System Identifier:** {project_id}",
            "",
            f"**Project ID:** {project_id}",
            "",
            "**POA&M Version:** 1.0",
            "",
            f"**Date Generated:** {now.strftime('%Y-%m-%d %H:%M UTC')}",
            "",
            "**Prepared By:** ICDEV Compliance Engine",
            "",
            "**Classification:** CUI // SP-CTI",
            "",
            "---",
            "",
            "## Summary",
            "",
            "| Severity | Open | In Progress | Completed | Accepted Risk | Total |",
            "|----------|------|-------------|-----------|---------------|-------|",
        ]

        for sev in ["critical", "high", "moderate", "low"]:
            cnt = severity_counts.get(sev, 0)
            lines.append(f"| {sev.capitalize()} | {cnt} | 0 | 0 | 0 | {cnt} |")

        total = sum(severity_counts.values())
        lines.append(f"| **Total** | **{total}** | **0** | **0** | **0** | **{total}** |")
        lines.extend(["", "---", ""])

        # POAM table
        lines.append("## POA&M Items")
        lines.append("")
        lines.append("| ID | Weakness | Severity | Related Control | Deficiency Description | Corrective Action | Resources Required | Milestone Date | Status | Completion Date | Responsible Party |")
        lines.append("|----|----------|----------|-----------------|----------------------|-------------------|--------------------|----------------|--------|-----------------|-------------------|")

        for i, item in enumerate(poam_items, 1):
            lines.append(_build_poam_table_row(i, item))

        lines.extend(["", "---", ""])

        # Detailed items
        lines.append("## Item Detail")
        lines.append("")
        for i, item in enumerate(poam_items, 1):
            lines.append(_build_poam_detail(i, item))

        lines.extend(["---", "", footer, ""])

        content = "\n".join(lines)

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
            timestamp = now.strftime("%Y%m%d_%H%M%S")
            out_file = out_dir / f"poam_{project_id}_{timestamp}.md"

        out_file.parent.mkdir(parents=True, exist_ok=True)

        with open(out_file, "w", encoding="utf-8") as f:
            f.write(content)

        # Insert new items into poam_items table
        new_count = 0
        for item in poam_items:
            if item["weakness_id"] not in existing_ids:
                conn.execute(
                    """INSERT INTO poam_items
                       (project_id, weakness_id, weakness_description, severity,
                        source, control_id, status, corrective_action,
                        milestone_date, responsible_party, resources_required)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        project_id,
                        item["weakness_id"],
                        item["description"],
                        item["severity"],
                        item["source"],
                        item["control_id"] or None,
                        item["status"],
                        item["corrective_action"],
                        item["milestone_date"],
                        item["responsible"],
                        item["resources"],
                    ),
                )
                new_count += 1

        conn.commit()

        # Log audit event
        _log_audit_event(conn, project_id, "POA&M generated", {
            "total_items": len(poam_items),
            "new_items": new_count,
            "severity_counts": severity_counts,
            "output_file": str(out_file),
        }, out_file)

        print("POA&M generated successfully:")
        print(f"  File: {out_file}")
        print(f"  Total items: {len(poam_items)}")
        print(f"  New items added to DB: {new_count}")
        print("  Severity breakdown:")
        for sev, cnt in severity_counts.items():
            if cnt > 0:
                print(f"    {sev}: {cnt}")

        return str(out_file)

    finally:
        conn.close()


def main():
    parser = argparse.ArgumentParser(
        description="Generate Plan of Action & Milestones (POA&M)"
    )
    parser.add_argument("--project", required=True, help="Project ID")
    parser.add_argument("--output", help="Output file path")
    parser.add_argument("--db", help="Database path")
    args = parser.parse_args()

    try:
        path = generate_poam(
            project_id=args.project,
            output_path=args.output,
            db_path=Path(args.db) if args.db else None,
        )
        print(f"\nPOA&M document path: {path}")
    except (FileNotFoundError, ValueError) as e:
        print(f"ERROR: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
