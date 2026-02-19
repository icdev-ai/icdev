#!/usr/bin/env python3
# CUI // SP-CTI
"""Get detailed status for an ICDEV-managed project.

Reports on:
  - Project info (name, type, status, tech stack)
  - Compliance status (SSP version, POA&M open count, STIG summary, controls)
  - Security status (last scan date, open vulnerabilities, SBOM)
  - Deployment status (current version per environment)
  - Test status (last run, pass rate, coverage)

Usage:
    python tools/project/project_status.py --project PROJECT_ID --format detailed
    python tools/project/project_status.py --project PROJECT_ID --format brief
    python tools/project/project_status.py --project PROJECT_ID --format json
"""

import argparse
import json
import sqlite3
import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent.parent
DB_PATH = BASE_DIR / "data" / "icdev.db"


def get_project_status(project_id: str) -> dict:
    """Get comprehensive project status from the database.

    Args:
        project_id: UUID of the project.

    Returns:
        dict with project, compliance, security, deployments, and tests sections.

    Raises:
        ValueError: If project_id is not found.
    """
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    try:
        return _query_status(conn, project_id)
    finally:
        conn.close()


def _query_status(conn: sqlite3.Connection, project_id: str) -> dict:
    """Internal: run all status queries against the database."""

    # ---- Project info ----
    row = conn.execute("SELECT * FROM projects WHERE id = ?", (project_id,)).fetchone()
    if not row:
        raise ValueError(f"Project not found: {project_id}")

    project_info = {
        "id": row["id"],
        "name": row["name"],
        "description": row["description"],
        "type": row["type"],
        "classification": row["classification"],
        "status": row["status"],
        "tech_stack": {
            "backend": row["tech_stack_backend"] or "",
            "frontend": row["tech_stack_frontend"] or "",
            "database": row["tech_stack_database"] or "",
        },
        "directory_path": row["directory_path"],
        "created_by": row["created_by"],
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }

    # ---- Compliance status ----
    compliance = _query_compliance(conn, project_id)

    # ---- Security status ----
    security = _query_security(conn, project_id)

    # ---- Deployment status ----
    deployments = _query_deployments(conn, project_id)

    # ---- Test status ----
    tests = _query_tests(conn, project_id)

    return {
        "project": project_info,
        "compliance": compliance,
        "security": security,
        "deployments": deployments,
        "tests": tests,
    }


def _query_compliance(conn: sqlite3.Connection, project_id: str) -> dict:
    """Query compliance-related tables for a project."""

    # SSP (latest)
    ssp = conn.execute(
        """SELECT version, status, system_name, authorization_type,
                  approved_by, approved_at, created_at
           FROM ssp_documents
           WHERE project_id = ?
           ORDER BY created_at DESC LIMIT 1""",
        (project_id,),
    ).fetchone()

    ssp_info = {
        "version": ssp["version"] if ssp else None,
        "status": ssp["status"] if ssp else "not_generated",
        "system_name": ssp["system_name"] if ssp else None,
        "authorization_type": ssp["authorization_type"] if ssp else None,
        "approved_by": ssp["approved_by"] if ssp else None,
        "approved_at": ssp["approved_at"] if ssp else None,
        "generated_at": ssp["created_at"] if ssp else None,
    }

    # POA&M counts by status
    poam_rows = conn.execute(
        "SELECT status, COUNT(*) as cnt FROM poam_items WHERE project_id = ? GROUP BY status",
        (project_id,),
    ).fetchall()
    poam_summary = {r["status"]: r["cnt"] for r in poam_rows}
    poam_total = sum(poam_summary.values())

    # POA&M counts by severity (for open items)
    poam_severity = conn.execute(
        "SELECT severity, COUNT(*) as cnt FROM poam_items WHERE project_id = ? AND status = 'open' GROUP BY severity",
        (project_id,),
    ).fetchall()
    poam_by_severity = {r["severity"]: r["cnt"] for r in poam_severity}

    # STIG findings summary
    stig_rows = conn.execute(
        "SELECT severity, status, COUNT(*) as cnt FROM stig_findings WHERE project_id = ? GROUP BY severity, status",
        (project_id,),
    ).fetchall()
    stig_summary = {}
    for r in stig_rows:
        sev = r["severity"]
        if sev not in stig_summary:
            stig_summary[sev] = {}
        stig_summary[sev][r["status"]] = r["cnt"]

    stig_total = sum(r["cnt"] for r in stig_rows)
    stig_open = sum(
        r["cnt"] for r in stig_rows if r["status"] in ("Open", "open")
    )

    # Control implementation status
    control_rows = conn.execute(
        "SELECT implementation_status, COUNT(*) as cnt FROM project_controls WHERE project_id = ? GROUP BY implementation_status",
        (project_id,),
    ).fetchall()
    controls_summary = {r["implementation_status"]: r["cnt"] for r in control_rows}
    controls_total = sum(controls_summary.values())

    return {
        "ssp": ssp_info,
        "poam": {
            "by_status": poam_summary,
            "by_severity": poam_by_severity,
            "total": poam_total,
            "open_count": poam_summary.get("open", 0),
        },
        "stig": {
            "by_severity_status": stig_summary,
            "total_findings": stig_total,
            "open_findings": stig_open,
        },
        "controls": {
            "by_status": controls_summary,
            "total": controls_total,
            "implemented": controls_summary.get("implemented", 0),
            "planned": controls_summary.get("planned", 0),
        },
    }


def _query_security(conn: sqlite3.Connection, project_id: str) -> dict:
    """Query security-related data for a project."""

    # Last security scan (from audit trail)
    last_scan = conn.execute(
        """SELECT created_at, details
           FROM audit_trail
           WHERE project_id = ? AND event_type = 'security_scan'
           ORDER BY created_at DESC LIMIT 1""",
        (project_id,),
    ).fetchone()

    # Open vulnerabilities (found after the last resolved)
    open_vulns = conn.execute(
        """SELECT COUNT(*) as cnt FROM audit_trail
           WHERE project_id = ? AND event_type = 'vulnerability_found'
           AND created_at > COALESCE(
               (SELECT MAX(created_at) FROM audit_trail
                WHERE project_id = ? AND event_type = 'vulnerability_resolved'),
               '1970-01-01'
           )""",
        (project_id, project_id),
    ).fetchone()

    # Vulnerability counts by type (from details JSON)
    vuln_events = conn.execute(
        """SELECT details FROM audit_trail
           WHERE project_id = ? AND event_type = 'vulnerability_found'
           ORDER BY created_at DESC LIMIT 50""",
        (project_id,),
    ).fetchall()

    vuln_by_severity = {"critical": 0, "high": 0, "medium": 0, "low": 0}
    for ve in vuln_events:
        if ve["details"]:
            try:
                d = json.loads(ve["details"])
                sev = d.get("severity", "medium").lower()
                if sev in vuln_by_severity:
                    vuln_by_severity[sev] += 1
            except (json.JSONDecodeError, AttributeError):
                pass

    # Latest SBOM
    latest_sbom = conn.execute(
        """SELECT version, format, component_count, vulnerability_count, file_path, generated_at
           FROM sbom_records
           WHERE project_id = ?
           ORDER BY generated_at DESC LIMIT 1""",
        (project_id,),
    ).fetchone()

    sbom_info = {
        "version": latest_sbom["version"] if latest_sbom else None,
        "format": latest_sbom["format"] if latest_sbom else None,
        "component_count": latest_sbom["component_count"] if latest_sbom else 0,
        "vulnerability_count": latest_sbom["vulnerability_count"] if latest_sbom else 0,
        "file_path": latest_sbom["file_path"] if latest_sbom else None,
        "generated_at": latest_sbom["generated_at"] if latest_sbom else None,
    }

    # Code review gate status (latest)
    latest_review = conn.execute(
        """SELECT branch, status, security_gate_passed, compliance_gate_passed,
                  test_gate_passed, created_at
           FROM code_reviews
           WHERE project_id = ?
           ORDER BY created_at DESC LIMIT 1""",
        (project_id,),
    ).fetchone()

    review_info = None
    if latest_review:
        review_info = {
            "branch": latest_review["branch"],
            "status": latest_review["status"],
            "security_gate_passed": bool(latest_review["security_gate_passed"]),
            "compliance_gate_passed": bool(latest_review["compliance_gate_passed"]),
            "test_gate_passed": bool(latest_review["test_gate_passed"]),
            "date": latest_review["created_at"],
        }

    return {
        "last_scan_date": last_scan["created_at"] if last_scan else None,
        "last_scan_details": json.loads(last_scan["details"]) if (last_scan and last_scan["details"]) else None,
        "open_vulnerabilities": open_vulns["cnt"] if open_vulns else 0,
        "vulnerabilities_by_severity": vuln_by_severity,
        "sbom": sbom_info,
        "latest_code_review": review_info,
    }


def _query_deployments(conn: sqlite3.Connection, project_id: str) -> dict:
    """Query deployment data for a project (latest per environment)."""

    deployments = conn.execute(
        """SELECT environment, version, status, pipeline_id, deployed_by,
                  health_check_passed, created_at, completed_at
           FROM deployments
           WHERE project_id = ?
           AND id IN (
               SELECT MAX(id) FROM deployments
               WHERE project_id = ?
               GROUP BY environment
           )
           ORDER BY environment""",
        (project_id, project_id),
    ).fetchall()

    result = {}
    for dep in deployments:
        result[dep["environment"]] = {
            "version": dep["version"],
            "status": dep["status"],
            "pipeline_id": dep["pipeline_id"],
            "deployed_by": dep["deployed_by"],
            "health_check_passed": bool(dep["health_check_passed"]) if dep["health_check_passed"] is not None else None,
            "deployed_at": dep["created_at"],
            "completed_at": dep["completed_at"],
        }

    return result


def _query_tests(conn: sqlite3.Connection, project_id: str) -> dict:
    """Query test data for a project."""

    # Last test event
    last_test = conn.execute(
        """SELECT event_type, created_at, details
           FROM audit_trail
           WHERE project_id = ? AND event_type IN ('test_executed', 'test_passed', 'test_failed')
           ORDER BY created_at DESC LIMIT 1""",
        (project_id,),
    ).fetchone()

    # Test pass rate metric
    pass_rate = conn.execute(
        """SELECT metric_value, collected_at
           FROM metric_snapshots
           WHERE project_id = ? AND metric_name = 'test_pass_rate'
           ORDER BY collected_at DESC LIMIT 1""",
        (project_id,),
    ).fetchone()

    # Test coverage metric
    coverage = conn.execute(
        """SELECT metric_value, collected_at
           FROM metric_snapshots
           WHERE project_id = ? AND metric_name = 'test_coverage'
           ORDER BY collected_at DESC LIMIT 1""",
        (project_id,),
    ).fetchone()

    # Count test-related events
    test_counts = conn.execute(
        """SELECT event_type, COUNT(*) as cnt
           FROM audit_trail
           WHERE project_id = ? AND event_type IN ('test_executed', 'test_passed', 'test_failed', 'test_written')
           GROUP BY event_type""",
        (project_id,),
    ).fetchall()
    test_event_counts = {r["event_type"]: r["cnt"] for r in test_counts}

    last_test_details = None
    if last_test and last_test["details"]:
        try:
            last_test_details = json.loads(last_test["details"])
        except json.JSONDecodeError:
            pass

    return {
        "last_run": last_test["created_at"] if last_test else None,
        "last_result": last_test["event_type"] if last_test else None,
        "last_run_details": last_test_details,
        "pass_rate": pass_rate["metric_value"] if pass_rate else None,
        "pass_rate_as_of": pass_rate["collected_at"] if pass_rate else None,
        "coverage": coverage["metric_value"] if coverage else None,
        "coverage_as_of": coverage["collected_at"] if coverage else None,
        "event_counts": test_event_counts,
    }


def format_brief(data: dict) -> str:
    """Format project status as a compact summary."""
    lines = []
    p = data["project"]
    c = data["compliance"]
    s = data["security"]
    d = data["deployments"]
    t = data["tests"]

    # Project header
    lines.append(f"{'=' * 60}")
    lines.append(f"  Project: {p['name']} ({p['type']})")
    lines.append(f"  ID:      {p['id']}")
    lines.append(f"  Status:  {p['status']}    Classification: {p['classification']}")
    lines.append(f"{'=' * 60}")

    # Tech stack
    ts = p["tech_stack"]
    if ts["backend"] or ts["frontend"] or ts["database"]:
        lines.append("")
        lines.append("  Tech Stack:")
        if ts["backend"]:
            lines.append(f"    Backend:  {ts['backend']}")
        if ts["frontend"]:
            lines.append(f"    Frontend: {ts['frontend']}")
        if ts["database"]:
            lines.append(f"    Database: {ts['database']}")

    # Compliance
    lines.append("")
    lines.append(f"  {'--- Compliance ---':^56}")
    lines.append(f"    SSP:      {c['ssp']['status']}" + (f" (v{c['ssp']['version']})" if c['ssp']['version'] else ""))
    lines.append(f"    POA&M:    {c['poam']['open_count']} open / {c['poam']['total']} total")
    lines.append(f"    STIG:     {c['stig']['open_findings']} open / {c['stig']['total_findings']} total")
    lines.append(f"    Controls: {c['controls']['implemented']} implemented / {c['controls']['total']} total")

    # Security
    lines.append("")
    lines.append(f"  {'--- Security ---':^56}")
    lines.append(f"    Last scan:  {s['last_scan_date'] or 'never'}")
    lines.append(f"    Open vulns: {s['open_vulnerabilities']}")
    if s["sbom"]["version"]:
        lines.append(f"    SBOM:       v{s['sbom']['version']} ({s['sbom']['component_count']} components)")
    else:
        lines.append("    SBOM:       not generated")

    # Deployments
    lines.append("")
    lines.append(f"  {'--- Deployments ---':^56}")
    if d:
        for env, info in sorted(d.items()):
            health = ""
            if info["health_check_passed"] is not None:
                health = " [HEALTHY]" if info["health_check_passed"] else " [UNHEALTHY]"
            lines.append(f"    {env:12s} v{info['version']:10s} {info['status']}{health}")
    else:
        lines.append("    No deployments recorded")

    # Tests
    lines.append("")
    lines.append(f"  {'--- Tests ---':^56}")
    lines.append(f"    Last run:  {t['last_run'] or 'never'}")
    if t["pass_rate"] is not None:
        lines.append(f"    Pass rate: {t['pass_rate']:.1f}%")
    else:
        lines.append("    Pass rate: N/A")
    if t["coverage"] is not None:
        lines.append(f"    Coverage:  {t['coverage']:.1f}%")
    else:
        lines.append("    Coverage:  N/A")

    lines.append("")
    return "\n".join(lines)


def format_detailed(data: dict) -> str:
    """Format project status as pretty-printed JSON."""
    return json.dumps(data, indent=2, default=str)


def main():
    parser = argparse.ArgumentParser(
        description="Get detailed status for an ICDEV-managed project"
    )
    parser.add_argument(
        "--project", required=True,
        help="Project UUID"
    )
    parser.add_argument(
        "--format", choices=["brief", "detailed", "json"], default="brief",
        help="Output format (brief=summary, detailed/json=full JSON)"
    )
    args = parser.parse_args()

    try:
        data = get_project_status(args.project)
    except ValueError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        sys.exit(1)

    if args.format == "brief":
        print(format_brief(data))
    else:
        print(format_detailed(data))


if __name__ == "__main__":
    main()
