#!/usr/bin/env python3
# CUI // SP-CTI
"""Compliance status dashboard data.
Aggregates SSP, POAM, STIG, SBOM, control mapping, CSSP, SbD, and IV&V status
into a comprehensive compliance overview for a project (8 components)."""

import argparse
import json
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent.parent
DB_PATH = BASE_DIR / "data" / "icdev.db"
CONTROLS_PATH = BASE_DIR / "context" / "compliance" / "nist_800_53.json"

REQUIRED_FAMILIES = ["AC", "AU", "CM", "IA", "SA", "SC", "RA", "CA"]


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


def _verify_project(conn, project_id):
    """Verify project exists."""
    row = conn.execute(
        "SELECT * FROM projects WHERE id = ?", (project_id,)
    ).fetchone()
    if not row:
        raise ValueError(f"Project '{project_id}' not found.")
    return dict(row)


def get_ssp_status(conn, project_id):
    """Get SSP document status for a project."""
    row = conn.execute(
        """SELECT id, version, system_name, status, file_path,
                  approved_by, approved_at, created_at
           FROM ssp_documents
           WHERE project_id = ?
           ORDER BY created_at DESC
           LIMIT 1""",
        (project_id,),
    ).fetchone()

    if not row:
        return {
            "exists": False,
            "latest_version": None,
            "status": "missing",
            "system_name": None,
            "file_path": None,
            "approved": False,
            "created_at": None,
        }

    return {
        "exists": True,
        "latest_version": row["version"],
        "status": row["status"],
        "system_name": row["system_name"],
        "file_path": row["file_path"],
        "approved": row["status"] == "approved",
        "approved_by": row["approved_by"],
        "approved_at": row["approved_at"],
        "created_at": row["created_at"],
    }


def get_poam_summary(conn, project_id):
    """Get POA&M summary with counts by severity and status."""
    rows = conn.execute(
        """SELECT severity, status, COUNT(*) as cnt
           FROM poam_items
           WHERE project_id = ?
           GROUP BY severity, status
           ORDER BY severity, status""",
        (project_id,),
    ).fetchall()

    summary = {
        "total": 0,
        "open": 0,
        "in_progress": 0,
        "completed": 0,
        "accepted_risk": 0,
        "by_severity": {},
        "overdue": 0,
    }

    for row in rows:
        sev = row["severity"]
        st = row["status"]
        cnt = row["cnt"]
        summary["total"] += cnt

        if st == "open":
            summary["open"] += cnt
        elif st == "in_progress":
            summary["in_progress"] += cnt
        elif st == "completed":
            summary["completed"] += cnt
        elif st == "accepted_risk":
            summary["accepted_risk"] += cnt

        if sev not in summary["by_severity"]:
            summary["by_severity"][sev] = {"open": 0, "in_progress": 0, "completed": 0, "accepted_risk": 0}
        summary["by_severity"][sev][st] = summary["by_severity"][sev].get(st, 0) + cnt

    # Check for overdue items
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    overdue = conn.execute(
        """SELECT COUNT(*) as cnt FROM poam_items
           WHERE project_id = ? AND status IN ('open', 'in_progress')
           AND milestone_date < ? AND milestone_date IS NOT NULL""",
        (project_id, today),
    ).fetchone()
    summary["overdue"] = overdue["cnt"] if overdue else 0

    return summary


def get_stig_summary(conn, project_id):
    """Get STIG findings summary with counts by severity and status."""
    rows = conn.execute(
        """SELECT severity, status, COUNT(*) as cnt
           FROM stig_findings
           WHERE project_id = ?
           GROUP BY severity, status
           ORDER BY severity, status""",
        (project_id,),
    ).fetchall()

    summary = {
        "total": 0,
        "by_severity": {
            "CAT1": {"Open": 0, "NotAFinding": 0, "Not_Applicable": 0, "Not_Reviewed": 0},
            "CAT2": {"Open": 0, "NotAFinding": 0, "Not_Applicable": 0, "Not_Reviewed": 0},
            "CAT3": {"Open": 0, "NotAFinding": 0, "Not_Applicable": 0, "Not_Reviewed": 0},
        },
        "cat1_open": 0,
        "cat2_open": 0,
        "cat3_open": 0,
        "gate_pass": True,  # 0 CAT1 Open = pass
    }

    for row in rows:
        sev = row["severity"]
        st = row["status"]
        cnt = row["cnt"]
        summary["total"] += cnt

        if sev in summary["by_severity"] and st in summary["by_severity"][sev]:
            summary["by_severity"][sev][st] += cnt

    summary["cat1_open"] = summary["by_severity"]["CAT1"]["Open"]
    summary["cat2_open"] = summary["by_severity"]["CAT2"]["Open"]
    summary["cat3_open"] = summary["by_severity"]["CAT3"]["Open"]
    summary["gate_pass"] = summary["cat1_open"] == 0

    # Get distinct STIGs assessed
    stig_ids = conn.execute(
        "SELECT DISTINCT stig_id FROM stig_findings WHERE project_id = ?",
        (project_id,),
    ).fetchall()
    summary["stigs_assessed"] = [r["stig_id"] for r in stig_ids]

    return summary


def get_sbom_info(conn, project_id):
    """Get latest SBOM information."""
    row = conn.execute(
        """SELECT version, format, file_path, component_count,
                  vulnerability_count, generated_at
           FROM sbom_records
           WHERE project_id = ?
           ORDER BY generated_at DESC
           LIMIT 1""",
        (project_id,),
    ).fetchone()

    if not row:
        return {
            "exists": False,
            "latest_version": None,
            "format": None,
            "component_count": 0,
            "vulnerability_count": 0,
            "generated_at": None,
        }

    return {
        "exists": True,
        "latest_version": row["version"],
        "format": row["format"],
        "file_path": row["file_path"],
        "component_count": row["component_count"] or 0,
        "vulnerability_count": row["vulnerability_count"] or 0,
        "generated_at": row["generated_at"],
    }


def get_control_mapping_status(conn, project_id):
    """Get control mapping completeness."""
    # Load reference controls
    total_required = 0
    if CONTROLS_PATH.exists():
        with open(CONTROLS_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
        all_controls = data.get("controls", [])
        required_control_ids = {
            c["id"] for c in all_controls
            if c["family"] in REQUIRED_FAMILIES
        }
        total_required = len(required_control_ids)
    else:
        required_control_ids = set()

    # Get mappings
    rows = conn.execute(
        """SELECT control_id, implementation_status
           FROM project_controls
           WHERE project_id = ?""",
        (project_id,),
    ).fetchall()

    mapped_ids = set()
    status_counts = {}
    for row in rows:
        mapped_ids.add(row["control_id"])
        st = row["implementation_status"]
        status_counts[st] = status_counts.get(st, 0) + 1

    mapped_required = mapped_ids & required_control_ids
    missing = required_control_ids - mapped_ids

    completeness = 0.0
    if total_required > 0:
        completeness = round(len(mapped_required) / total_required * 100, 1)

    return {
        "total_required": total_required,
        "total_mapped": len(rows),
        "mapped_required": len(mapped_required),
        "missing_count": len(missing),
        "missing_controls": sorted(list(missing)),
        "completeness_pct": completeness,
        "status_breakdown": status_counts,
        "complete": len(missing) == 0,
    }


def _get_cssp_status(conn, project_id):
    """Get CSSP assessment status for a project."""
    rows = conn.execute(
        "SELECT * FROM cssp_assessments WHERE project_id = ?",
        (project_id,),
    ).fetchall()

    if not rows:
        return {
            "assessed": False,
            "total": 0,
            "satisfied": 0,
            "partially_satisfied": 0,
            "not_satisfied": 0,
            "not_assessed": 0,
            "risk_accepted": 0,
            "score": 0,
            "critical_not_satisfied": 0,
            "ir_plan_exists": False,
            "siem_configured": False,
            "by_area": {},
            "xacta_sync": None,
        }

    total = len(rows)
    satisfied = sum(1 for r in rows if r["status"] == "satisfied")
    partial = sum(1 for r in rows if r["status"] == "partially_satisfied")
    not_sat = sum(1 for r in rows if r["status"] == "not_satisfied")
    not_assessed = sum(1 for r in rows if r["status"] == "not_assessed")
    risk_acc = sum(1 for r in rows if r["status"] == "risk_accepted")
    na = sum(1 for r in rows if r["status"] == "not_applicable")

    assessable = total - na if total > na else total
    score = (
        100 * (satisfied + partial * 0.5 + risk_acc * 0.75) / assessable
        if assessable > 0
        else 0
    )

    # Count critical requirements not satisfied
    critical_not_sat = sum(
        1 for r in rows
        if r["status"] == "not_satisfied"
        and r["requirement_id"] in (
            "ID-3", "ID-4", "ID-6", "PR-1", "PR-2", "PR-3", "PR-5",
            "DE-1", "DE-2", "DE-3", "DE-7", "RS-1", "RS-2", "RS-3",
            "SU-1", "SU-6",
        )
    )

    # Check IR plan and SIEM
    ir_plan = any(
        r["status"] in ("satisfied", "partially_satisfied")
        for r in rows if r["requirement_id"] == "RS-1"
    )
    siem = any(
        r["status"] in ("satisfied", "partially_satisfied")
        for r in rows if r["requirement_id"] == "DE-2"
    )

    # By functional area
    areas = {}
    for r in rows:
        area = r["functional_area"]
        if area not in areas:
            areas[area] = {"total": 0, "satisfied": 0, "partial": 0, "not_satisfied": 0}
        areas[area]["total"] += 1
        if r["status"] == "satisfied":
            areas[area]["satisfied"] += 1
        elif r["status"] == "partially_satisfied":
            areas[area]["partial"] += 1
        elif r["status"] == "not_satisfied":
            areas[area]["not_satisfied"] += 1

    # Xacta sync info
    cert_row = conn.execute(
        "SELECT last_xacta_sync, status FROM cssp_certifications WHERE project_id = ?",
        (project_id,),
    ).fetchone()
    xacta_sync = dict(cert_row) if cert_row else None

    return {
        "assessed": True,
        "total": total,
        "satisfied": satisfied,
        "partially_satisfied": partial,
        "not_satisfied": not_sat,
        "not_assessed": not_assessed,
        "risk_accepted": risk_acc,
        "not_applicable": na,
        "score": round(score, 1),
        "critical_not_satisfied": critical_not_sat,
        "ir_plan_exists": ir_plan,
        "siem_configured": siem,
        "by_area": areas,
        "xacta_sync": xacta_sync,
    }


def _get_sbd_status(conn, project_id):
    """Get Secure by Design (SbD) assessment status for a project."""
    try:
        rows = conn.execute(
            "SELECT * FROM sbd_assessments WHERE project_id = ?",
            (project_id,),
        ).fetchall()
    except Exception:
        return {"assessed": False, "total": 0, "score": 0, "critical_not_satisfied": 0, "by_domain": {}}

    if not rows:
        return {"assessed": False, "total": 0, "score": 0, "critical_not_satisfied": 0, "by_domain": {}}

    total = len(rows)
    satisfied = sum(1 for r in rows if r["status"] == "satisfied")
    partial = sum(1 for r in rows if r["status"] == "partially_satisfied")
    not_sat = sum(1 for r in rows if r["status"] == "not_satisfied")
    not_assessed = sum(1 for r in rows if r["status"] == "not_assessed")
    risk_acc = sum(1 for r in rows if r["status"] == "risk_accepted")
    na = sum(1 for r in rows if r["status"] == "not_applicable")

    assessable = total - na if total > na else total
    score = (
        100 * (satisfied + partial * 0.5 + risk_acc * 0.75) / assessable
        if assessable > 0 else 0
    )

    # Count critical not satisfied (requirements with cisa_commitment mapped to critical)
    critical_not_sat = sum(
        1 for r in rows
        if r["status"] == "not_satisfied"
        and r["requirement_id"] in (
            "SBD-01", "SBD-02", "SBD-08", "SBD-11", "SBD-14",
            "SBD-16", "SBD-22", "SBD-28", "SBD-31",
        )
    )

    # By domain
    domains = {}
    for r in rows:
        domain = r["domain"]
        if domain not in domains:
            domains[domain] = {"total": 0, "satisfied": 0, "partial": 0, "not_satisfied": 0}
        domains[domain]["total"] += 1
        if r["status"] == "satisfied":
            domains[domain]["satisfied"] += 1
        elif r["status"] == "partially_satisfied":
            domains[domain]["partial"] += 1
        elif r["status"] == "not_satisfied":
            domains[domain]["not_satisfied"] += 1

    return {
        "assessed": True,
        "total": total,
        "satisfied": satisfied,
        "partially_satisfied": partial,
        "not_satisfied": not_sat,
        "not_assessed": not_assessed,
        "risk_accepted": risk_acc,
        "not_applicable": na,
        "score": round(score, 1),
        "critical_not_satisfied": critical_not_sat,
        "by_domain": domains,
    }


def _get_ivv_status(conn, project_id):
    """Get IV&V assessment status for a project."""
    try:
        rows = conn.execute(
            "SELECT * FROM ivv_assessments WHERE project_id = ?",
            (project_id,),
        ).fetchall()
    except Exception:
        return {
            "assessed": False, "total": 0, "verification_score": 0,
            "validation_score": 0, "overall_score": 0,
            "critical_findings": 0, "certification_status": None,
        }

    if not rows:
        return {
            "assessed": False, "total": 0, "verification_score": 0,
            "validation_score": 0, "overall_score": 0,
            "critical_findings": 0, "certification_status": None,
        }

    total = len(rows)
    passed = sum(1 for r in rows if r["status"] == "pass")
    partial = sum(1 for r in rows if r["status"] == "partial")
    failed = sum(1 for r in rows if r["status"] == "fail")
    na = sum(1 for r in rows if r["status"] == "not_applicable")

    assessable = total - na if total > na else total
    score = (
        100 * (passed + partial * 0.5) / assessable
        if assessable > 0 else 0
    )

    # Critical findings
    try:
        critical_rows = conn.execute(
            "SELECT COUNT(*) as cnt FROM ivv_findings WHERE project_id = ? AND severity = 'critical' AND status = 'open'",
            (project_id,),
        ).fetchone()
        critical_findings = critical_rows["cnt"] if critical_rows else 0
    except Exception:
        critical_findings = 0

    # Certification status
    try:
        cert_row = conn.execute(
            "SELECT status, verification_score, validation_score, overall_score FROM ivv_certifications WHERE project_id = ?",
            (project_id,),
        ).fetchone()
        if cert_row:
            verification_score = cert_row["verification_score"] or score
            validation_score = cert_row["validation_score"] or score
            overall_ivv = cert_row["overall_score"] or score
            cert_status = cert_row["status"]
        else:
            verification_score = score
            validation_score = score
            overall_ivv = score
            cert_status = None
    except Exception:
        verification_score = score
        validation_score = score
        overall_ivv = score
        cert_status = None

    # By process area
    areas = {}
    for r in rows:
        area = r["process_area"]
        if area not in areas:
            areas[area] = {"total": 0, "pass": 0, "partial": 0, "fail": 0}
        areas[area]["total"] += 1
        if r["status"] == "pass":
            areas[area]["pass"] += 1
        elif r["status"] == "partial":
            areas[area]["partial"] += 1
        elif r["status"] == "fail":
            areas[area]["fail"] += 1

    return {
        "assessed": True,
        "total": total,
        "passed": passed,
        "partial": partial,
        "failed": failed,
        "verification_score": round(verification_score, 1),
        "validation_score": round(validation_score, 1),
        "overall_score": round(overall_ivv, 1),
        "critical_findings": critical_findings,
        "certification_status": cert_status,
        "by_area": areas,
    }


def get_compliance_status(project_id, db_path=None):
    """Get comprehensive compliance status for a project.

    Returns:
        Dict with ssp, poam, stig, sbom, controls, cssp, sbd, and ivv status (8 components).
    """
    conn = _get_connection(db_path)
    try:
        project = _verify_project(conn, project_id)

        ssp = get_ssp_status(conn, project_id)
        poam = get_poam_summary(conn, project_id)
        stig = get_stig_summary(conn, project_id)
        sbom = get_sbom_info(conn, project_id)
        controls = get_control_mapping_status(conn, project_id)
        cssp = _get_cssp_status(conn, project_id)
        sbd = _get_sbd_status(conn, project_id)
        ivv = _get_ivv_status(conn, project_id)

        # Overall compliance score (weighted — 8 components)
        scores = []

        # SSP: 12%
        if ssp["exists"]:
            ssp_score = 100 if ssp["approved"] else 50
        else:
            ssp_score = 0
        scores.append(("SSP", ssp_score, 0.12))

        # POAM: 10%
        if poam["total"] > 0:
            active = poam["open"] + poam["in_progress"]
            poam_score = max(0, 100 - (poam["overdue"] * 20) - (active * 5))
        else:
            poam_score = 100  # No findings = good
        scores.append(("POAM", poam_score, 0.10))

        # STIG: 15%
        if stig["total"] > 0:
            stig_score = 0 if stig["cat1_open"] > 0 else max(
                0, 100 - (stig["cat2_open"] * 5) - (stig["cat3_open"] * 2)
            )
        else:
            stig_score = 0  # No assessment done
        scores.append(("STIG", stig_score, 0.15))

        # SBOM: 6%
        sbom_score = 100 if sbom["exists"] else 0
        scores.append(("SBOM", sbom_score, 0.06))

        # Controls: 20%
        controls_score = min(100, controls["completeness_pct"])
        scores.append(("Controls", controls_score, 0.20))

        # CSSP: 15%
        cssp_score = cssp.get("score", 0)
        scores.append(("CSSP", cssp_score, 0.15))

        # SbD: 12%
        sbd_score = sbd.get("score", 0)
        scores.append(("SbD", sbd_score, 0.12))

        # IV&V: 10%
        ivv_score = ivv.get("overall_score", 0)
        scores.append(("IV&V", ivv_score, 0.10))

        overall_score = sum(s * w for _, s, w in scores)

        # Determine overall status
        if overall_score >= 80 and stig.get("gate_pass", True):
            overall_status = "compliant"
        elif overall_score >= 50:
            overall_status = "partially_compliant"
        else:
            overall_status = "non_compliant"

        # Blockers
        blockers = []
        if not ssp["exists"]:
            blockers.append("SSP document not generated")
        elif not ssp["approved"]:
            blockers.append("SSP not approved")
        if stig["cat1_open"] > 0:
            blockers.append(f"{stig['cat1_open']} CAT1 STIG finding(s) open")
        if poam["overdue"] > 0:
            blockers.append(f"{poam['overdue']} overdue POA&M item(s)")
        if not sbom["exists"]:
            blockers.append("SBOM not generated")
        if not controls["complete"]:
            blockers.append(f"{controls['missing_count']} required controls not mapped")
        if cssp.get("critical_not_satisfied", 0) > 0:
            blockers.append(f"{cssp['critical_not_satisfied']} critical CSSP requirement(s) not satisfied")
        if not cssp.get("ir_plan_exists", False):
            blockers.append("Incident Response Plan not generated")
        if not cssp.get("siem_configured", False):
            blockers.append("SIEM log forwarding not configured")
        if sbd.get("critical_not_satisfied", 0) > 0:
            blockers.append(f"{sbd['critical_not_satisfied']} critical SbD requirement(s) not satisfied")
        if not sbd.get("assessed", False):
            blockers.append("SbD assessment not completed")
        if ivv.get("critical_findings", 0) > 0:
            blockers.append(f"{ivv['critical_findings']} critical IV&V finding(s) open")
        if not ivv.get("assessed", False):
            blockers.append("IV&V assessment not completed")

        return {
            "project_id": project_id,
            "project_name": project.get("name", project_id),
            "classification": project.get("classification", "CUI"),
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "overall_status": overall_status,
            "overall_score": round(overall_score, 1),
            "component_scores": {name: score for name, score, _ in scores},
            "blockers": blockers,
            "ssp": ssp,
            "poam": poam,
            "stig": stig,
            "sbom": sbom,
            "controls": controls,
            "cssp": cssp,
            "sbd": sbd,
            "ivv": ivv,
        }

    finally:
        conn.close()


def _format_status_report(status):
    """Format compliance status as human-readable report."""
    lines = [
        "=" * 70,
        "  COMPLIANCE STATUS DASHBOARD",
        "=" * 70,
        f"  Project: {status['project_name']} ({status['project_id']})",
        f"  Classification: {status['classification']}",
        f"  Generated: {status['generated_at']}",
        "",
        f"  Overall Status: {status['overall_status'].upper()}",
        f"  Overall Score:  {status['overall_score']}/100",
        "",
    ]

    # Component scores
    lines.append("  Component Scores:")
    for name, score in status["component_scores"].items():
        bar_len = int(score / 5)
        bar = "#" * bar_len + "." * (20 - bar_len)
        lines.append(f"    {name:<12} [{bar}] {score:.0f}%")
    lines.append("")

    # Blockers
    if status["blockers"]:
        lines.append("  BLOCKERS:")
        for b in status["blockers"]:
            lines.append(f"    ! {b}")
        lines.append("")

    # SSP
    lines.append("  --- SSP ---")
    ssp = status["ssp"]
    if ssp["exists"]:
        lines.append(f"    Version: {ssp['latest_version']}")
        lines.append(f"    Status:  {ssp['status']}")
        lines.append(f"    System:  {ssp['system_name']}")
    else:
        lines.append("    NOT GENERATED")
    lines.append("")

    # POAM
    lines.append("  --- POA&M ---")
    poam = status["poam"]
    lines.append(f"    Total Items:  {poam['total']}")
    lines.append(f"    Open:         {poam['open']}")
    lines.append(f"    In Progress:  {poam['in_progress']}")
    lines.append(f"    Completed:    {poam['completed']}")
    lines.append(f"    Accepted Risk: {poam['accepted_risk']}")
    lines.append(f"    Overdue:      {poam['overdue']}")
    if poam["by_severity"]:
        lines.append("    By Severity:")
        for sev in ["critical", "high", "moderate", "low"]:
            if sev in poam["by_severity"]:
                d = poam["by_severity"][sev]
                lines.append(f"      {sev}: open={d.get('open',0)} ip={d.get('in_progress',0)} done={d.get('completed',0)}")
    lines.append("")

    # STIG
    lines.append("  --- STIG ---")
    stig = status["stig"]
    lines.append(f"    Findings Assessed: {stig['total']}")
    lines.append(f"    STIGs Assessed:    {', '.join(stig.get('stigs_assessed', [])) or 'None'}")
    lines.append(f"    Gate (0 CAT1):     {'PASS' if stig['gate_pass'] else 'FAIL'}")
    for cat in ["CAT1", "CAT2", "CAT3"]:
        s = stig["by_severity"].get(cat, {})
        open_c = s.get("Open", 0)
        naf = s.get("NotAFinding", 0)
        na = s.get("Not_Applicable", 0)
        nr = s.get("Not_Reviewed", 0)
        lines.append(f"    {cat}: Open={open_c} NAF={naf} NA={na} NR={nr}")
    lines.append("")

    # SBOM
    lines.append("  --- SBOM ---")
    sbom = status["sbom"]
    if sbom["exists"]:
        lines.append(f"    Version:      {sbom['latest_version']}")
        lines.append(f"    Format:       {sbom['format']}")
        lines.append(f"    Components:   {sbom['component_count']}")
        lines.append(f"    Vulnerabilities: {sbom['vulnerability_count']}")
        lines.append(f"    Generated:    {sbom['generated_at']}")
    else:
        lines.append("    NOT GENERATED")
    lines.append("")

    # Controls
    lines.append("  --- Control Mappings ---")
    ctrl = status["controls"]
    lines.append(f"    Required:      {ctrl['total_required']}")
    lines.append(f"    Mapped:        {ctrl['total_mapped']}")
    lines.append(f"    Completeness:  {ctrl['completeness_pct']}%")
    lines.append(f"    Missing:       {ctrl['missing_count']}")
    if ctrl["status_breakdown"]:
        lines.append("    Status:")
        for st, cnt in ctrl["status_breakdown"].items():
            lines.append(f"      {st}: {cnt}")
    if ctrl["missing_controls"]:
        missing_display = ctrl["missing_controls"][:10]
        lines.append(f"    Missing Controls: {', '.join(missing_display)}")
        if len(ctrl["missing_controls"]) > 10:
            lines.append(f"      ... and {len(ctrl['missing_controls']) - 10} more")
    lines.append("")

    # CSSP
    lines.append("  --- CSSP (DI 8530.01) ---")
    cssp = status.get("cssp", {})
    if cssp.get("assessed"):
        lines.append(f"    Score:             {cssp['score']}%")
        lines.append(f"    Requirements:      {cssp['total']}")
        lines.append(f"    Satisfied:         {cssp['satisfied']}")
        lines.append(f"    Partial:           {cssp['partially_satisfied']}")
        lines.append(f"    Not Satisfied:     {cssp['not_satisfied']}")
        lines.append(f"    Not Assessed:      {cssp['not_assessed']}")
        lines.append(f"    Risk Accepted:     {cssp['risk_accepted']}")
        lines.append(f"    IR Plan:           {'Yes' if cssp['ir_plan_exists'] else 'MISSING'}")
        lines.append(f"    SIEM Configured:   {'Yes' if cssp['siem_configured'] else 'MISSING'}")
        if cssp.get("by_area"):
            lines.append("    By Functional Area:")
            for area in ["Identify", "Protect", "Detect", "Respond", "Sustain"]:
                a = cssp["by_area"].get(area, {})
                if a:
                    lines.append(f"      {area}: {a.get('satisfied',0)}/{a.get('total',0)} satisfied")
        if cssp.get("xacta_sync"):
            lines.append(f"    Last Xacta Sync:   {cssp['xacta_sync'].get('last_xacta_sync', 'Never')}")
    else:
        lines.append("    NOT ASSESSED")

    lines.append("")
    lines.append("=" * 70)

    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(
        description="Compliance status dashboard"
    )
    parser.add_argument("--project-id", "--project", required=True, help="Project ID", dest="project_id")
    parser.add_argument("--db", help="Database path")
    parser.add_argument(
        "--json", action="store_true",
        help="Output as JSON"
    )
    parser.add_argument(
        "--section", choices=["ssp", "poam", "stig", "sbom", "controls", "cssp"],
        help="Show only a specific section"
    )
    args = parser.parse_args()

    try:
        status = get_compliance_status(
            project_id=args.project_id,
            db_path=Path(args.db) if args.db else None,
        )

        if args.section:
            section_data = status.get(args.section, {})
            if args.json:
                print(json.dumps(section_data, indent=2, default=str))
            else:
                print(json.dumps(section_data, indent=2, default=str))
        elif args.json:
            print(json.dumps(status, indent=2, default=str))
        else:
            print(_format_status_report(status))

    except (FileNotFoundError, ValueError) as e:
        print(f"ERROR: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
