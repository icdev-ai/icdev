#!/usr/bin/env python3
# CUI // SP-CTI
# Controlled Unclassified Information — SP-CTI
# Distribution: Distribution D — Authorized DoD Personnel Only
# CUI // SP-CTI
"""SAFe Program Increment (PI) compliance tracker.

Tracks compliance progress across SAFe Program Increments. Provides
PI-level snapshots of compliance posture, velocity metrics, burndown
projections, and generates PI compliance summary reports with CUI markings.

Usage:
    # Start a new PI
    python tools/compliance/pi_compliance_tracker.py \\
        --project-id proj-123 --start-pi PI-24.1 \\
        --start-date 2024-01-15 --end-date 2024-04-15

    # Record mid-PI progress
    python tools/compliance/pi_compliance_tracker.py \\
        --project-id proj-123 --record-progress --pi PI-24.1

    # Close a PI
    python tools/compliance/pi_compliance_tracker.py \\
        --project-id proj-123 --close-pi PI-24.1

    # View velocity metrics
    python tools/compliance/pi_compliance_tracker.py \\
        --project-id proj-123 --velocity

    # View compliance burndown
    python tools/compliance/pi_compliance_tracker.py \\
        --project-id proj-123 --burndown

    # Generate PI compliance report
    python tools/compliance/pi_compliance_tracker.py \\
        --project-id proj-123 --report --pi PI-24.1

    # List all PIs for a project
    python tools/compliance/pi_compliance_tracker.py \\
        --project-id proj-123 --list-pis

    # Get detailed PI metrics
    python tools/compliance/pi_compliance_tracker.py \\
        --project-id proj-123 --details --pi PI-24.1
"""

import argparse
import json
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent.parent
DB_PATH = BASE_DIR / "data" / "icdev.db"


# ------------------------------------------------------------------
# Database helpers
# ------------------------------------------------------------------

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
    """Verify project exists and return its data."""
    row = conn.execute(
        "SELECT * FROM projects WHERE id = ?", (project_id,)
    ).fetchone()
    if not row:
        raise ValueError(f"Project '{project_id}' not found.")
    return dict(row)


def _log_audit(conn, project_id, action, details):
    """Log an audit trail event for PI compliance tracking."""
    try:
        conn.execute(
            """INSERT INTO audit_trail
               (project_id, event_type, actor, action, details, classification)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (
                project_id,
                "pi_compliance_updated",
                "icdev-compliance-engine",
                action,
                json.dumps(details, default=str),
                "CUI",
            ),
        )
        conn.commit()
    except Exception as e:
        print(f"Warning: Could not log audit event: {e}", file=sys.stderr)


# ------------------------------------------------------------------
# Compliance snapshot helpers
# ------------------------------------------------------------------

def _count_implemented_controls(conn, project_id):
    """Count controls by implementation status for a project."""
    rows = conn.execute(
        """SELECT implementation_status, COUNT(*) as cnt
           FROM project_controls
           WHERE project_id = ?
           GROUP BY implementation_status""",
        (project_id,),
    ).fetchall()

    counts = {}
    for row in rows:
        counts[row["implementation_status"]] = row["cnt"]
    return counts


def _count_total_required_controls(conn, project_id):
    """Count total required controls for the project."""
    row = conn.execute(
        "SELECT COUNT(*) as cnt FROM project_controls WHERE project_id = ?",
        (project_id,),
    ).fetchone()
    return row["cnt"] if row else 0


def _count_open_poam_items(conn, project_id):
    """Count POAM items by status."""
    rows = conn.execute(
        """SELECT status, COUNT(*) as cnt
           FROM poam_items
           WHERE project_id = ?
           GROUP BY status""",
        (project_id,),
    ).fetchall()

    counts = {}
    for row in rows:
        counts[row["status"]] = row["cnt"]
    return counts


def _compute_compliance_score(conn, project_id):
    """Compute current compliance score (0-100).

    Uses the same weighted approach as compliance_status.py:
    SSP 12%, POAM 10%, STIG 15%, SBOM 6%, Controls 20%, CSSP 15%,
    SbD 12%, IV&V 10%.
    """
    scores = []

    # SSP: 12%
    ssp_row = conn.execute(
        """SELECT status FROM ssp_documents
           WHERE project_id = ? ORDER BY created_at DESC LIMIT 1""",
        (project_id,),
    ).fetchone()
    if ssp_row:
        ssp_score = 100 if ssp_row["status"] == "approved" else 50
    else:
        ssp_score = 0
    scores.append(("SSP", ssp_score, 0.12))

    # POAM: 10%
    poam_counts = _count_open_poam_items(conn, project_id)
    total_poam = sum(poam_counts.values())
    if total_poam > 0:
        active = poam_counts.get("open", 0) + poam_counts.get("in_progress", 0)
        today = datetime.utcnow().strftime("%Y-%m-%d")
        overdue_row = conn.execute(
            """SELECT COUNT(*) as cnt FROM poam_items
               WHERE project_id = ? AND status IN ('open', 'in_progress')
               AND milestone_date < ? AND milestone_date IS NOT NULL""",
            (project_id, today),
        ).fetchone()
        overdue = overdue_row["cnt"] if overdue_row else 0
        poam_score = max(0, 100 - (overdue * 20) - (active * 5))
    else:
        poam_score = 100
    scores.append(("POAM", poam_score, 0.10))

    # STIG: 15%
    stig_rows = conn.execute(
        """SELECT severity, status, COUNT(*) as cnt
           FROM stig_findings WHERE project_id = ?
           GROUP BY severity, status""",
        (project_id,),
    ).fetchall()
    cat1_open = sum(r["cnt"] for r in stig_rows if r["severity"] == "CAT1" and r["status"] == "Open")
    cat2_open = sum(r["cnt"] for r in stig_rows if r["severity"] == "CAT2" and r["status"] == "Open")
    cat3_open = sum(r["cnt"] for r in stig_rows if r["severity"] == "CAT3" and r["status"] == "Open")
    total_stig = sum(r["cnt"] for r in stig_rows)
    if total_stig > 0:
        stig_score = 0 if cat1_open > 0 else max(0, 100 - (cat2_open * 5) - (cat3_open * 2))
    else:
        stig_score = 0
    scores.append(("STIG", stig_score, 0.15))

    # SBOM: 6%
    sbom_row = conn.execute(
        "SELECT id FROM sbom_records WHERE project_id = ? LIMIT 1",
        (project_id,),
    ).fetchone()
    sbom_score = 100 if sbom_row else 0
    scores.append(("SBOM", sbom_score, 0.06))

    # Controls: 20%
    control_counts = _count_implemented_controls(conn, project_id)
    total_controls = sum(control_counts.values())
    implemented = control_counts.get("implemented", 0) + control_counts.get("compensating", 0)
    controls_score = (implemented / total_controls * 100) if total_controls > 0 else 0
    scores.append(("Controls", min(100, controls_score), 0.20))

    # CSSP: 15%
    try:
        cssp_rows = conn.execute(
            "SELECT status FROM cssp_assessments WHERE project_id = ?",
            (project_id,),
        ).fetchall()
        if cssp_rows:
            total_cssp = len(cssp_rows)
            satisfied = sum(1 for r in cssp_rows if r["status"] == "satisfied")
            partial = sum(1 for r in cssp_rows if r["status"] == "partially_satisfied")
            risk_acc = sum(1 for r in cssp_rows if r["status"] == "risk_accepted")
            na = sum(1 for r in cssp_rows if r["status"] == "not_applicable")
            assessable = total_cssp - na if total_cssp > na else total_cssp
            cssp_score = (100 * (satisfied + partial * 0.5 + risk_acc * 0.75) / assessable) if assessable > 0 else 0
        else:
            cssp_score = 0
    except Exception:
        cssp_score = 0
    scores.append(("CSSP", cssp_score, 0.15))

    # SbD: 12%
    try:
        sbd_rows = conn.execute(
            "SELECT status FROM sbd_assessments WHERE project_id = ?",
            (project_id,),
        ).fetchall()
        if sbd_rows:
            total_sbd = len(sbd_rows)
            satisfied = sum(1 for r in sbd_rows if r["status"] == "satisfied")
            partial = sum(1 for r in sbd_rows if r["status"] == "partially_satisfied")
            risk_acc = sum(1 for r in sbd_rows if r["status"] == "risk_accepted")
            na = sum(1 for r in sbd_rows if r["status"] == "not_applicable")
            assessable = total_sbd - na if total_sbd > na else total_sbd
            sbd_score = (100 * (satisfied + partial * 0.5 + risk_acc * 0.75) / assessable) if assessable > 0 else 0
        else:
            sbd_score = 0
    except Exception:
        sbd_score = 0
    scores.append(("SbD", sbd_score, 0.12))

    # IV&V: 10%
    try:
        ivv_cert = conn.execute(
            "SELECT overall_score FROM ivv_certifications WHERE project_id = ?",
            (project_id,),
        ).fetchone()
        ivv_score = ivv_cert["overall_score"] if ivv_cert and ivv_cert["overall_score"] else 0
    except Exception:
        ivv_score = 0
    scores.append(("IV&V", ivv_score, 0.10))

    overall = sum(s * w for _, s, w in scores)
    return round(overall, 1)


def _count_findings_remediated(conn, project_id):
    """Count STIG findings that are not open (i.e., remediated)."""
    row = conn.execute(
        """SELECT COUNT(*) as cnt FROM stig_findings
           WHERE project_id = ? AND status != 'Open'""",
        (project_id,),
    ).fetchone()
    return row["cnt"] if row else 0


def _list_artifacts_generated(conn, project_id, since_date=None):
    """List compliance artifacts generated for a project, optionally since a date."""
    artifacts = []

    # SSP documents
    query = "SELECT file_path, created_at FROM ssp_documents WHERE project_id = ?"
    params = [project_id]
    if since_date:
        query += " AND created_at >= ?"
        params.append(since_date)
    for row in conn.execute(query, params).fetchall():
        artifacts.append({"type": "SSP", "path": row["file_path"], "created_at": row["created_at"]})

    # SBOM records
    query = "SELECT file_path, generated_at FROM sbom_records WHERE project_id = ?"
    params = [project_id]
    if since_date:
        query += " AND generated_at >= ?"
        params.append(since_date)
    for row in conn.execute(query, params).fetchall():
        artifacts.append({"type": "SBOM", "path": row["file_path"], "created_at": row["generated_at"]})

    # OSCAL artifacts
    try:
        query = "SELECT artifact_type, file_path, generated_at FROM oscal_artifacts WHERE project_id = ?"
        params = [project_id]
        if since_date:
            query += " AND generated_at >= ?"
            params.append(since_date)
        for row in conn.execute(query, params).fetchall():
            artifacts.append({
                "type": f"OSCAL-{row['artifact_type']}",
                "path": row["file_path"],
                "created_at": row["generated_at"],
            })
    except Exception:
        pass

    return artifacts


# ------------------------------------------------------------------
# Core PI tracking functions
# ------------------------------------------------------------------

def start_pi(project_id, pi_number, start_date, end_date, db_path=None):
    """Initialize PI tracking with a baseline compliance snapshot.

    Args:
        project_id: The project identifier.
        pi_number: PI identifier (e.g. 'PI-24.1').
        start_date: PI start date (YYYY-MM-DD).
        end_date: PI end date (YYYY-MM-DD).
        db_path: Optional database path override.

    Returns:
        Dict with PI start snapshot data.
    """
    conn = _get_connection(db_path)
    try:
        _verify_project(conn, project_id)

        # Check for duplicate
        existing = conn.execute(
            "SELECT id FROM pi_compliance_tracking WHERE project_id = ? AND pi_number = ?",
            (project_id, pi_number),
        ).fetchone()
        if existing:
            raise ValueError(
                f"PI '{pi_number}' already exists for project '{project_id}'. "
                "Use record_pi_progress or close_pi instead."
            )

        # Take baseline snapshot
        control_counts = _count_implemented_controls(conn, project_id)
        implemented = control_counts.get("implemented", 0) + control_counts.get("compensating", 0)
        total_controls = sum(control_counts.values())
        remaining = total_controls - implemented

        poam_counts = _count_open_poam_items(conn, project_id)
        open_poam = poam_counts.get("open", 0) + poam_counts.get("in_progress", 0)

        compliance_score = _compute_compliance_score(conn, project_id)
        findings_remediated = _count_findings_remediated(conn, project_id)

        # Insert PI record
        conn.execute(
            """INSERT INTO pi_compliance_tracking
               (project_id, pi_number, pi_start_date, pi_end_date,
                compliance_score_start, controls_implemented, controls_remaining,
                poam_items_opened, findings_remediated, notes)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                project_id,
                pi_number,
                start_date,
                end_date,
                compliance_score,
                implemented,
                remaining,
                open_poam,
                findings_remediated,
                json.dumps({
                    "baseline_control_counts": control_counts,
                    "baseline_poam_counts": poam_counts,
                    "status": "active",
                }),
            ),
        )
        conn.commit()

        result = {
            "project_id": project_id,
            "pi_number": pi_number,
            "pi_start_date": start_date,
            "pi_end_date": end_date,
            "compliance_score_start": compliance_score,
            "controls_implemented": implemented,
            "controls_remaining": remaining,
            "open_poam_items": open_poam,
            "findings_remediated": findings_remediated,
            "status": "active",
        }

        _log_audit(conn, project_id, f"PI {pi_number} started", result)

        print(f"PI {pi_number} started for project {project_id}")
        print(f"  Start date:           {start_date}")
        print(f"  End date:             {end_date}")
        print(f"  Compliance score:     {compliance_score}")
        print(f"  Controls implemented: {implemented}")
        print(f"  Controls remaining:   {remaining}")
        print(f"  Open POAM items:      {open_poam}")
        print(f"  Findings remediated:  {findings_remediated}")

        return result

    finally:
        conn.close()


def record_pi_progress(project_id, pi_number, db_path=None):
    """Record current compliance metrics as a mid-PI snapshot.

    Args:
        project_id: The project identifier.
        pi_number: PI identifier (e.g. 'PI-24.1').
        db_path: Optional database path override.

    Returns:
        Dict with progress delta since PI start.
    """
    conn = _get_connection(db_path)
    try:
        _verify_project(conn, project_id)

        # Get existing PI record
        pi_row = conn.execute(
            """SELECT * FROM pi_compliance_tracking
               WHERE project_id = ? AND pi_number = ?""",
            (project_id, pi_number),
        ).fetchone()
        if not pi_row:
            raise ValueError(
                f"PI '{pi_number}' not found for project '{project_id}'. "
                "Use start_pi first."
            )
        pi_data = dict(pi_row)

        # Snapshot current state
        control_counts = _count_implemented_controls(conn, project_id)
        implemented_now = control_counts.get("implemented", 0) + control_counts.get("compensating", 0)
        total_controls = sum(control_counts.values())
        remaining_now = total_controls - implemented_now

        poam_counts = _count_open_poam_items(conn, project_id)
        open_poam_now = poam_counts.get("open", 0) + poam_counts.get("in_progress", 0)
        closed_poam = poam_counts.get("completed", 0) + poam_counts.get("accepted_risk", 0)

        findings_remediated_now = _count_findings_remediated(conn, project_id)
        current_score = _compute_compliance_score(conn, project_id)

        # Parse notes to get baseline
        notes = {}
        if pi_data["notes"]:
            try:
                notes = json.loads(pi_data["notes"])
            except (json.JSONDecodeError, TypeError):
                pass

        baseline_poam = notes.get("baseline_poam_counts", {})
        baseline_closed = baseline_poam.get("completed", 0) + baseline_poam.get("accepted_risk", 0)

        # Compute deltas
        controls_delta = implemented_now - pi_data["controls_implemented"]
        findings_delta = findings_remediated_now - pi_data["findings_remediated"]
        poam_closed_delta = closed_poam - baseline_closed
        score_delta = current_score - pi_data["compliance_score_start"]

        # Update notes with progress snapshot
        notes["last_progress_update"] = datetime.utcnow().isoformat()
        notes["current_score"] = current_score
        notes["current_controls_implemented"] = implemented_now
        notes["current_controls_remaining"] = remaining_now
        notes["current_open_poam"] = open_poam_now

        # Update the PI record
        conn.execute(
            """UPDATE pi_compliance_tracking SET
               controls_implemented = ?,
               controls_remaining = ?,
               poam_items_closed = ?,
               poam_items_opened = ?,
               findings_remediated = ?,
               notes = ?
               WHERE project_id = ? AND pi_number = ?""",
            (
                implemented_now,
                remaining_now,
                poam_closed_delta,
                open_poam_now,
                findings_remediated_now,
                json.dumps(notes, default=str),
                project_id,
                pi_number,
            ),
        )
        conn.commit()

        result = {
            "project_id": project_id,
            "pi_number": pi_number,
            "current_score": current_score,
            "score_delta": round(score_delta, 1),
            "controls_implemented": implemented_now,
            "controls_remaining": remaining_now,
            "controls_delta": controls_delta,
            "poam_items_closed": poam_closed_delta,
            "open_poam_items": open_poam_now,
            "findings_remediated": findings_remediated_now,
            "findings_delta": findings_delta,
            "updated_at": datetime.utcnow().isoformat(),
        }

        _log_audit(conn, project_id, f"PI {pi_number} progress recorded", result)

        print(f"PI {pi_number} progress recorded for project {project_id}")
        print(f"  Current score:        {current_score} (delta: {score_delta:+.1f})")
        print(f"  Controls implemented: {implemented_now} (delta: {controls_delta:+d})")
        print(f"  Controls remaining:   {remaining_now}")
        print(f"  POAM items closed:    {poam_closed_delta}")
        print(f"  Open POAM items:      {open_poam_now}")
        print(f"  Findings remediated:  {findings_remediated_now} (delta: {findings_delta:+d})")

        return result

    finally:
        conn.close()


def close_pi(project_id, pi_number, db_path=None):
    """Record final PI metrics, compute velocity, and close the PI.

    Args:
        project_id: The project identifier.
        pi_number: PI identifier (e.g. 'PI-24.1').
        db_path: Optional database path override.

    Returns:
        Dict with final PI metrics and velocity data.
    """
    conn = _get_connection(db_path)
    try:
        _verify_project(conn, project_id)

        # Get existing PI record
        pi_row = conn.execute(
            """SELECT * FROM pi_compliance_tracking
               WHERE project_id = ? AND pi_number = ?""",
            (project_id, pi_number),
        ).fetchone()
        if not pi_row:
            raise ValueError(
                f"PI '{pi_number}' not found for project '{project_id}'. "
                "Use start_pi first."
            )
        pi_data = dict(pi_row)

        # Final snapshot
        control_counts = _count_implemented_controls(conn, project_id)
        implemented_now = control_counts.get("implemented", 0) + control_counts.get("compensating", 0)
        total_controls = sum(control_counts.values())
        remaining_now = total_controls - implemented_now

        poam_counts = _count_open_poam_items(conn, project_id)
        open_poam_now = poam_counts.get("open", 0) + poam_counts.get("in_progress", 0)
        closed_poam = poam_counts.get("completed", 0) + poam_counts.get("accepted_risk", 0)

        findings_remediated_now = _count_findings_remediated(conn, project_id)
        final_score = _compute_compliance_score(conn, project_id)

        # Parse notes for baseline
        notes = {}
        if pi_data["notes"]:
            try:
                notes = json.loads(pi_data["notes"])
            except (json.JSONDecodeError, TypeError):
                pass

        baseline_poam = notes.get("baseline_poam_counts", {})
        baseline_closed = baseline_poam.get("completed", 0) + baseline_poam.get("accepted_risk", 0)

        # Compute PI velocity
        controls_delta = implemented_now - pi_data["controls_implemented"]
        findings_delta = findings_remediated_now - pi_data["findings_remediated"]
        poam_closed_delta = closed_poam - baseline_closed
        score_delta = final_score - pi_data["compliance_score_start"]

        # List artifacts generated during PI
        artifacts = _list_artifacts_generated(conn, project_id, since_date=pi_data["pi_start_date"])
        artifacts_json = json.dumps(artifacts, default=str)

        # Update notes with closure data
        notes["status"] = "closed"
        notes["closed_at"] = datetime.utcnow().isoformat()
        notes["velocity"] = {
            "controls_implemented": controls_delta,
            "findings_remediated": findings_delta,
            "poam_items_closed": poam_closed_delta,
            "score_delta": round(score_delta, 1),
        }

        # Update the record
        conn.execute(
            """UPDATE pi_compliance_tracking SET
               compliance_score_end = ?,
               controls_implemented = ?,
               controls_remaining = ?,
               poam_items_closed = ?,
               poam_items_opened = ?,
               findings_remediated = ?,
               artifacts_generated = ?,
               notes = ?
               WHERE project_id = ? AND pi_number = ?""",
            (
                final_score,
                implemented_now,
                remaining_now,
                poam_closed_delta,
                open_poam_now,
                findings_remediated_now,
                artifacts_json,
                json.dumps(notes, default=str),
                project_id,
                pi_number,
            ),
        )
        conn.commit()

        result = {
            "project_id": project_id,
            "pi_number": pi_number,
            "compliance_score_start": pi_data["compliance_score_start"],
            "compliance_score_end": final_score,
            "score_delta": round(score_delta, 1),
            "controls_implemented": implemented_now,
            "controls_remaining": remaining_now,
            "controls_delta": controls_delta,
            "poam_items_closed": poam_closed_delta,
            "open_poam_items": open_poam_now,
            "findings_remediated": findings_remediated_now,
            "findings_delta": findings_delta,
            "artifacts_generated": len(artifacts),
            "status": "closed",
        }

        _log_audit(conn, project_id, f"PI {pi_number} closed", result)

        print(f"PI {pi_number} closed for project {project_id}")
        print(f"  Score: {pi_data['compliance_score_start']} -> {final_score} (delta: {score_delta:+.1f})")
        print(f"  Controls implemented: {implemented_now} (delta: {controls_delta:+d})")
        print(f"  Controls remaining:   {remaining_now}")
        print(f"  POAM items closed:    {poam_closed_delta}")
        print(f"  Findings remediated:  {findings_remediated_now} (delta: {findings_delta:+d})")
        print(f"  Artifacts generated:  {len(artifacts)}")

        return result

    finally:
        conn.close()


def get_pi_velocity(project_id, db_path=None):
    """Compute velocity metrics across all PIs for a project.

    Returns controls implemented per PI, POAM items closed per PI,
    findings remediated per PI, and compliance score delta per PI.

    Args:
        project_id: The project identifier.
        db_path: Optional database path override.

    Returns:
        Dict with per-PI velocity data and averages.
    """
    conn = _get_connection(db_path)
    try:
        _verify_project(conn, project_id)

        rows = conn.execute(
            """SELECT * FROM pi_compliance_tracking
               WHERE project_id = ?
               ORDER BY pi_start_date ASC""",
            (project_id,),
        ).fetchall()

        if not rows:
            return {
                "project_id": project_id,
                "total_pis": 0,
                "pis": [],
                "averages": {},
                "message": "No PIs found for this project.",
            }

        pis = []
        total_controls = 0
        total_poam_closed = 0
        total_findings = 0
        total_score_delta = 0.0
        closed_count = 0

        for row in rows:
            pi = dict(row)
            notes = {}
            if pi["notes"]:
                try:
                    notes = json.loads(pi["notes"])
                except (json.JSONDecodeError, TypeError):
                    pass

            velocity = notes.get("velocity", {})
            is_closed = notes.get("status") == "closed"
            score_start = pi["compliance_score_start"] or 0
            score_end = pi["compliance_score_end"]
            score_delta = (score_end - score_start) if score_end is not None else None

            pi_velocity = {
                "pi_number": pi["pi_number"],
                "pi_start_date": pi["pi_start_date"],
                "pi_end_date": pi["pi_end_date"],
                "status": "closed" if is_closed else "active",
                "compliance_score_start": score_start,
                "compliance_score_end": score_end,
                "score_delta": round(score_delta, 1) if score_delta is not None else None,
                "controls_implemented": pi["controls_implemented"],
                "controls_remaining": pi["controls_remaining"],
                "poam_items_closed": pi["poam_items_closed"] or 0,
                "findings_remediated": pi["findings_remediated"] or 0,
            }
            pis.append(pi_velocity)

            if is_closed:
                closed_count += 1
                total_controls += velocity.get("controls_implemented", 0)
                total_poam_closed += velocity.get("poam_items_closed", 0)
                total_findings += velocity.get("findings_remediated", 0)
                total_score_delta += velocity.get("score_delta", 0)

        averages = {}
        if closed_count > 0:
            averages = {
                "avg_controls_per_pi": round(total_controls / closed_count, 1),
                "avg_poam_closed_per_pi": round(total_poam_closed / closed_count, 1),
                "avg_findings_per_pi": round(total_findings / closed_count, 1),
                "avg_score_delta_per_pi": round(total_score_delta / closed_count, 1),
                "closed_pis": closed_count,
            }

        result = {
            "project_id": project_id,
            "total_pis": len(rows),
            "pis": pis,
            "averages": averages,
        }

        print(f"PI Velocity for project {project_id}")
        print(f"  Total PIs: {len(rows)}  (Closed: {closed_count})")
        if averages:
            print(f"  Avg controls/PI:    {averages['avg_controls_per_pi']}")
            print(f"  Avg POAM closed/PI: {averages['avg_poam_closed_per_pi']}")
            print(f"  Avg findings/PI:    {averages['avg_findings_per_pi']}")
            print(f"  Avg score delta/PI: {averages['avg_score_delta_per_pi']:+.1f}")
        print()
        for pi in pis:
            status_tag = "[CLOSED]" if pi["status"] == "closed" else "[ACTIVE]"
            print(f"  {pi['pi_number']} {status_tag}")
            print(f"    Score: {pi['compliance_score_start']} -> {pi['compliance_score_end'] or '...'}")
            print(f"    Controls: {pi['controls_implemented']} impl, {pi['controls_remaining']} remaining")
            print(f"    POAM closed: {pi['poam_items_closed']}, Findings: {pi['findings_remediated']}")

        return result

    finally:
        conn.close()


def get_compliance_burndown(project_id, target_framework=None, db_path=None):
    """Compute remaining controls vs PI timeline with projected completion.

    Args:
        project_id: The project identifier.
        target_framework: Optional framework filter (not currently used,
            reserved for future multi-framework burndown).
        db_path: Optional database path override.

    Returns:
        Dict with burndown data including projected completion.
    """
    conn = _get_connection(db_path)
    try:
        _verify_project(conn, project_id)

        # Get current control state
        control_counts = _count_implemented_controls(conn, project_id)
        total_controls = sum(control_counts.values())
        implemented = control_counts.get("implemented", 0) + control_counts.get("compensating", 0)
        remaining = total_controls - implemented

        # Get velocity from closed PIs
        rows = conn.execute(
            """SELECT * FROM pi_compliance_tracking
               WHERE project_id = ?
               ORDER BY pi_start_date ASC""",
            (project_id,),
        ).fetchall()

        velocities = []
        pi_history = []
        for row in rows:
            pi = dict(row)
            notes = {}
            if pi["notes"]:
                try:
                    notes = json.loads(pi["notes"])
                except (json.JSONDecodeError, TypeError):
                    pass

            velocity = notes.get("velocity", {})
            is_closed = notes.get("status") == "closed"

            pi_history.append({
                "pi_number": pi["pi_number"],
                "controls_remaining": pi["controls_remaining"],
                "status": "closed" if is_closed else "active",
            })

            if is_closed:
                velocities.append(velocity.get("controls_implemented", 0))

        # Compute average velocity
        avg_velocity = round(sum(velocities) / len(velocities), 1) if velocities else 0

        # Project completion
        projected_pis_remaining = None
        projected_completion_pi = None
        projected_completion_date = None

        if avg_velocity > 0 and remaining > 0:
            import math
            projected_pis_remaining = math.ceil(remaining / avg_velocity)

            # Estimate completion PI number
            if rows:
                last_pi = dict(rows[-1])
                last_pi_number = last_pi["pi_number"]
                # Try to parse PI number to project forward
                try:
                    # Expecting format like PI-YY.N
                    parts = last_pi_number.replace("PI-", "").split(".")
                    year_part = int(parts[0])
                    increment_part = int(parts[1])
                    future_increment = increment_part + projected_pis_remaining
                    # Assume ~4 PIs per year
                    extra_years = (future_increment - 1) // 4
                    final_increment = ((future_increment - 1) % 4) + 1
                    projected_completion_pi = f"PI-{year_part + extra_years}.{final_increment}"
                except (ValueError, IndexError):
                    projected_completion_pi = f"{last_pi_number} + {projected_pis_remaining} PIs"

                # Estimate date (assume ~10 weeks per PI)
                try:
                    last_end = last_pi.get("pi_end_date")
                    if last_end:
                        from datetime import timedelta
                        last_date = datetime.strptime(last_end, "%Y-%m-%d")
                        projected_date = last_date + timedelta(weeks=10 * projected_pis_remaining)
                        projected_completion_date = projected_date.strftime("%Y-%m-%d")
                except (ValueError, TypeError):
                    pass
        elif remaining == 0:
            projected_pis_remaining = 0
            projected_completion_pi = "COMPLETE"

        result = {
            "project_id": project_id,
            "total_required": total_controls,
            "implemented": implemented,
            "remaining": remaining,
            "control_breakdown": control_counts,
            "avg_velocity": avg_velocity,
            "velocities_by_pi": velocities,
            "projected_pis_remaining": projected_pis_remaining,
            "projected_completion_pi": projected_completion_pi,
            "projected_completion_date": projected_completion_date,
            "pi_history": pi_history,
        }

        print(f"Compliance Burndown for project {project_id}")
        print(f"  Total required controls: {total_controls}")
        print(f"  Implemented:             {implemented}")
        print(f"  Remaining:               {remaining}")
        print(f"  Avg velocity:            {avg_velocity} controls/PI")
        if projected_pis_remaining is not None:
            if projected_pis_remaining == 0:
                print("  Status:                  ALL CONTROLS IMPLEMENTED")
            else:
                print(f"  Projected PIs remaining: {projected_pis_remaining}")
                if projected_completion_pi:
                    print(f"  Projected completion PI: {projected_completion_pi}")
                if projected_completion_date:
                    print(f"  Projected completion:    {projected_completion_date}")
        elif remaining > 0:
            print("  Projected completion:    INSUFFICIENT DATA (no closed PIs with velocity)")

        return result

    finally:
        conn.close()


def generate_pi_compliance_report(project_id, pi_number, output_path=None, db_path=None):
    """Generate a PI compliance summary report in markdown with CUI markings.

    Args:
        project_id: The project identifier.
        pi_number: PI identifier (e.g. 'PI-24.1').
        output_path: Optional output file path override.
        db_path: Optional database path override.

    Returns:
        Path to the generated report file.
    """
    conn = _get_connection(db_path)
    try:
        project = _verify_project(conn, project_id)

        # Get PI record
        pi_row = conn.execute(
            """SELECT * FROM pi_compliance_tracking
               WHERE project_id = ? AND pi_number = ?""",
            (project_id, pi_number),
        ).fetchone()
        if not pi_row:
            raise ValueError(f"PI '{pi_number}' not found for project '{project_id}'.")
        pi_data = dict(pi_row)

        # Parse notes
        notes = {}
        if pi_data["notes"]:
            try:
                notes = json.loads(pi_data["notes"])
            except (json.JSONDecodeError, TypeError):
                pass

        velocity = notes.get("velocity", {})
        is_closed = notes.get("status") == "closed"
        score_start = pi_data["compliance_score_start"] or 0
        score_end = pi_data["compliance_score_end"]

        # Parse artifacts
        artifacts = []
        if pi_data["artifacts_generated"]:
            try:
                artifacts = json.loads(pi_data["artifacts_generated"])
            except (json.JSONDecodeError, TypeError):
                pass

        # Current compliance score for active PIs
        if not is_closed:
            current_score = _compute_compliance_score(conn, project_id)
        else:
            current_score = score_end

        # Get velocity data for trend
        all_pis = conn.execute(
            """SELECT pi_number, compliance_score_start, compliance_score_end, notes
               FROM pi_compliance_tracking
               WHERE project_id = ?
               ORDER BY pi_start_date ASC""",
            (project_id,),
        ).fetchall()

        now = datetime.utcnow()

        # CUI markings
        cui_header = (
            "////////////////////////////////////////////////////////////////////\n"
            "CONTROLLED UNCLASSIFIED INFORMATION (CUI) // SP-CTI\n"
            "Distribution: Distribution D -- Authorized DoD Personnel Only\n"
            "////////////////////////////////////////////////////////////////////"
        )
        cui_footer = (
            "////////////////////////////////////////////////////////////////////\n"
            "CUI // SP-CTI | Department of Defense\n"
            "////////////////////////////////////////////////////////////////////"
        )

        # Build report
        lines = [
            cui_header,
            "",
            "# PI COMPLIANCE SUMMARY REPORT",
            "",
            f"**Project:** {project.get('name', project_id)} ({project_id})",
            f"**Program Increment:** {pi_number}",
            f"**PI Period:** {pi_data['pi_start_date']} to {pi_data['pi_end_date']}",
            f"**Status:** {'Closed' if is_closed else 'Active'}",
            f"**Report Generated:** {now.strftime('%Y-%m-%d %H:%M UTC')}",
            "**Classification:** CUI // SP-CTI",
            "",
            "---",
            "",
            "## 1. PI Metrics Summary",
            "",
            "| Metric | Start of PI | Current/End | Delta |",
            "|--------|-------------|-------------|-------|",
        ]

        score_display = score_end if is_closed else current_score
        score_delta = (score_display - score_start) if score_display is not None else "N/A"
        if isinstance(score_delta, (int, float)):
            score_delta_str = f"{score_delta:+.1f}"
        else:
            score_delta_str = str(score_delta)

        lines.append(
            f"| Compliance Score | {score_start} | {score_display} | {score_delta_str} |"
        )
        lines.append(
            f"| Controls Implemented | {pi_data['controls_implemented']} | "
            f"{pi_data['controls_implemented']} | "
            f"{velocity.get('controls_implemented', 'N/A')} |"
        )
        lines.append(
            f"| Controls Remaining | {pi_data['controls_remaining']} | "
            f"{pi_data['controls_remaining']} | -- |"
        )
        lines.append(
            f"| POAM Items Closed | -- | {pi_data['poam_items_closed'] or 0} | "
            f"{pi_data['poam_items_closed'] or 0} |"
        )
        lines.append(
            f"| Open POAM Items | {pi_data['poam_items_opened'] or 0} | "
            f"{pi_data['poam_items_opened'] or 0} | -- |"
        )
        lines.append(
            f"| Findings Remediated | -- | {pi_data['findings_remediated'] or 0} | "
            f"{velocity.get('findings_remediated', 'N/A')} |"
        )

        lines.extend(["", "---", ""])

        # Velocity chart data (text-based)
        lines.append("## 2. Compliance Score Trend")
        lines.append("")
        lines.append("| PI | Start Score | End Score | Delta |")
        lines.append("|----|-------------|-----------|-------|")
        for pi in all_pis:
            if pi["notes"]:
                try:
                    json.loads(pi["notes"])
                except (json.JSONDecodeError, TypeError):
                    pass
            s_start = pi["compliance_score_start"] or 0
            s_end = pi["compliance_score_end"]
            if s_end is not None:
                delta = f"{s_end - s_start:+.1f}"
            else:
                delta = "..."
            lines.append(
                f"| {pi['pi_number']} | {s_start} | {s_end or '...'} | {delta} |"
            )

        lines.extend(["", "---", ""])

        # Controls implemented this PI
        lines.append("## 3. Controls Implemented This PI")
        lines.append("")
        ctrl_delta = velocity.get("controls_implemented", 0)
        if ctrl_delta > 0:
            lines.append(f"**{ctrl_delta}** new controls implemented during {pi_number}.")
        elif is_closed:
            lines.append("No new controls were implemented during this PI.")
        else:
            lines.append("PI is still active. Run `record_pi_progress` for latest data.")

        lines.extend(["", "---", ""])

        # POAMs
        lines.append("## 4. POAM Activity")
        lines.append("")
        lines.append(f"- **Items Closed:** {pi_data['poam_items_closed'] or 0}")
        lines.append(f"- **Items Opened:** {pi_data['poam_items_opened'] or 0}")

        lines.extend(["", "---", ""])

        # Artifacts
        lines.append("## 5. Artifacts Generated")
        lines.append("")
        if artifacts:
            lines.append(f"**{len(artifacts)}** compliance artifacts generated during this PI:")
            lines.append("")
            for art in artifacts:
                lines.append(f"- **{art.get('type', 'Unknown')}:** {art.get('path', 'N/A')} ({art.get('created_at', '')})")
        else:
            if is_closed:
                lines.append("No artifacts recorded for this PI.")
            else:
                lines.append("PI is still active. Artifacts will be recorded on close.")

        lines.extend(["", "---", ""])

        # Recommendations
        lines.append("## 6. Recommendations for Next PI")
        lines.append("")
        recommendations = []
        if pi_data["controls_remaining"] and pi_data["controls_remaining"] > 0:
            recommendations.append(
                f"- **Controls:** {pi_data['controls_remaining']} controls remain. "
                f"Prioritize high-impact control families (AC, SC, IA)."
            )
        if pi_data["poam_items_opened"] and pi_data["poam_items_opened"] > 0:
            recommendations.append(
                f"- **POAM Remediation:** {pi_data['poam_items_opened']} open POAM items. "
                "Focus on overdue and critical-severity items."
            )
        if score_display is not None and score_display < 80:
            recommendations.append(
                "- **Compliance Score:** Score is below 80. Prioritize SSP approval, "
                "STIG remediation, and control implementation."
            )
        if not recommendations:
            recommendations.append("- Continue current pace. Compliance posture is on track.")
        lines.extend(recommendations)

        lines.extend([
            "",
            "---",
            "",
            cui_footer,
            "",
        ])

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
            out_file = out_dir / f"pi_compliance_report_{pi_number}_{timestamp}.md"

        out_file.parent.mkdir(parents=True, exist_ok=True)
        with open(out_file, "w", encoding="utf-8") as f:
            f.write(content)

        _log_audit(conn, project_id, f"PI {pi_number} compliance report generated", {
            "pi_number": pi_number,
            "output_file": str(out_file),
        })

        print(f"PI compliance report generated: {out_file}")
        return str(out_file)

    finally:
        conn.close()


def list_pis(project_id, db_path=None):
    """List all PIs for a project with status and scores.

    Args:
        project_id: The project identifier.
        db_path: Optional database path override.

    Returns:
        List of PI summary dicts.
    """
    conn = _get_connection(db_path)
    try:
        _verify_project(conn, project_id)

        rows = conn.execute(
            """SELECT * FROM pi_compliance_tracking
               WHERE project_id = ?
               ORDER BY pi_start_date ASC""",
            (project_id,),
        ).fetchall()

        pis = []
        for row in rows:
            pi = dict(row)
            notes = {}
            if pi["notes"]:
                try:
                    notes = json.loads(pi["notes"])
                except (json.JSONDecodeError, TypeError):
                    pass

            is_closed = notes.get("status") == "closed"

            pis.append({
                "pi_number": pi["pi_number"],
                "pi_start_date": pi["pi_start_date"],
                "pi_end_date": pi["pi_end_date"],
                "status": "closed" if is_closed else "active",
                "compliance_score_start": pi["compliance_score_start"],
                "compliance_score_end": pi["compliance_score_end"],
                "controls_implemented": pi["controls_implemented"],
                "controls_remaining": pi["controls_remaining"],
                "poam_items_closed": pi["poam_items_closed"] or 0,
                "findings_remediated": pi["findings_remediated"] or 0,
            })

        print(f"PIs for project {project_id}: ({len(pis)} total)")
        print()
        if pis:
            print(f"  {'PI':<12} {'Status':<10} {'Period':<25} {'Score Start':<14} {'Score End':<12}")
            print(f"  {'-'*12} {'-'*10} {'-'*25} {'-'*14} {'-'*12}")
            for pi in pis:
                period = f"{pi['pi_start_date']} - {pi['pi_end_date']}"
                score_end = pi["compliance_score_end"] if pi["compliance_score_end"] is not None else "..."
                print(f"  {pi['pi_number']:<12} {pi['status']:<10} {period:<25} {pi['compliance_score_start']:<14} {score_end:<12}")
        else:
            print("  No PIs found. Use --start-pi to create one.")

        return pis

    finally:
        conn.close()


def get_pi_details(project_id, pi_number, db_path=None):
    """Get detailed metrics for a specific PI.

    Args:
        project_id: The project identifier.
        pi_number: PI identifier (e.g. 'PI-24.1').
        db_path: Optional database path override.

    Returns:
        Dict with comprehensive PI details.
    """
    conn = _get_connection(db_path)
    try:
        _verify_project(conn, project_id)

        pi_row = conn.execute(
            """SELECT * FROM pi_compliance_tracking
               WHERE project_id = ? AND pi_number = ?""",
            (project_id, pi_number),
        ).fetchone()
        if not pi_row:
            raise ValueError(f"PI '{pi_number}' not found for project '{project_id}'.")

        pi_data = dict(pi_row)

        notes = {}
        if pi_data["notes"]:
            try:
                notes = json.loads(pi_data["notes"])
            except (json.JSONDecodeError, TypeError):
                pass

        velocity = notes.get("velocity", {})
        is_closed = notes.get("status") == "closed"

        artifacts = []
        if pi_data["artifacts_generated"]:
            try:
                artifacts = json.loads(pi_data["artifacts_generated"])
            except (json.JSONDecodeError, TypeError):
                pass

        result = {
            "project_id": project_id,
            "pi_number": pi_data["pi_number"],
            "pi_start_date": pi_data["pi_start_date"],
            "pi_end_date": pi_data["pi_end_date"],
            "status": "closed" if is_closed else "active",
            "compliance_score_start": pi_data["compliance_score_start"],
            "compliance_score_end": pi_data["compliance_score_end"],
            "controls_implemented": pi_data["controls_implemented"],
            "controls_remaining": pi_data["controls_remaining"],
            "poam_items_closed": pi_data["poam_items_closed"] or 0,
            "poam_items_opened": pi_data["poam_items_opened"] or 0,
            "findings_remediated": pi_data["findings_remediated"] or 0,
            "artifacts_generated": artifacts,
            "velocity": velocity,
            "baseline_control_counts": notes.get("baseline_control_counts", {}),
            "baseline_poam_counts": notes.get("baseline_poam_counts", {}),
            "last_progress_update": notes.get("last_progress_update"),
            "closed_at": notes.get("closed_at"),
            "created_at": pi_data["created_at"],
        }

        print(f"PI Details: {pi_number} for project {project_id}")
        print(f"  Status:               {'Closed' if is_closed else 'Active'}")
        print(f"  Period:               {pi_data['pi_start_date']} to {pi_data['pi_end_date']}")
        print(f"  Score (start):        {pi_data['compliance_score_start']}")
        print(f"  Score (end):          {pi_data['compliance_score_end'] or '...'}")
        print(f"  Controls implemented: {pi_data['controls_implemented']}")
        print(f"  Controls remaining:   {pi_data['controls_remaining']}")
        print(f"  POAM items closed:    {pi_data['poam_items_closed'] or 0}")
        print(f"  POAM items opened:    {pi_data['poam_items_opened'] or 0}")
        print(f"  Findings remediated:  {pi_data['findings_remediated'] or 0}")
        print(f"  Artifacts generated:  {len(artifacts)}")
        if velocity:
            print("  Velocity:")
            for k, v in velocity.items():
                print(f"    {k}: {v}")

        return result

    finally:
        conn.close()


# ------------------------------------------------------------------
# CLI
# ------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="SAFe PI Compliance Tracker — track compliance across Program Increments"
    )
    parser.add_argument("--project-id", required=True, help="Project identifier")
    parser.add_argument("--db", help="Database path override")

    # Actions
    parser.add_argument("--start-pi", metavar="PI_NUMBER", help="Start a new PI (e.g. PI-24.1)")
    parser.add_argument("--start-date", help="PI start date (YYYY-MM-DD)")
    parser.add_argument("--end-date", help="PI end date (YYYY-MM-DD)")

    parser.add_argument("--record-progress", action="store_true", help="Record mid-PI progress snapshot")
    parser.add_argument("--close-pi", metavar="PI_NUMBER", help="Close a PI with final metrics")

    parser.add_argument("--velocity", action="store_true", help="Show velocity metrics across PIs")
    parser.add_argument("--burndown", action="store_true", help="Show compliance burndown with projections")
    parser.add_argument("--target-framework", help="Target framework for burndown (optional)")

    parser.add_argument("--report", action="store_true", help="Generate PI compliance report")
    parser.add_argument("--output", help="Output file path for report")

    parser.add_argument("--list-pis", action="store_true", help="List all PIs for the project")
    parser.add_argument("--details", action="store_true", help="Get detailed PI metrics")

    parser.add_argument("--pi", metavar="PI_NUMBER", help="PI number (used with --record-progress, --report, --details)")
    parser.add_argument("--json", action="store_true", help="Output as JSON")

    args = parser.parse_args()
    db_path = Path(args.db) if args.db else None

    try:
        if args.start_pi:
            if not args.start_date or not args.end_date:
                parser.error("--start-pi requires --start-date and --end-date")
            result = start_pi(
                project_id=args.project_id,
                pi_number=args.start_pi,
                start_date=args.start_date,
                end_date=args.end_date,
                db_path=db_path,
            )
            if args.json:
                print(json.dumps(result, indent=2, default=str))

        elif args.record_progress:
            pi_num = args.pi
            if not pi_num:
                parser.error("--record-progress requires --pi PI_NUMBER")
            result = record_pi_progress(
                project_id=args.project_id,
                pi_number=pi_num,
                db_path=db_path,
            )
            if args.json:
                print(json.dumps(result, indent=2, default=str))

        elif args.close_pi:
            result = close_pi(
                project_id=args.project_id,
                pi_number=args.close_pi,
                db_path=db_path,
            )
            if args.json:
                print(json.dumps(result, indent=2, default=str))

        elif args.velocity:
            result = get_pi_velocity(
                project_id=args.project_id,
                db_path=db_path,
            )
            if args.json:
                print(json.dumps(result, indent=2, default=str))

        elif args.burndown:
            result = get_compliance_burndown(
                project_id=args.project_id,
                target_framework=args.target_framework,
                db_path=db_path,
            )
            if args.json:
                print(json.dumps(result, indent=2, default=str))

        elif args.report:
            pi_num = args.pi
            if not pi_num:
                parser.error("--report requires --pi PI_NUMBER")
            result = generate_pi_compliance_report(
                project_id=args.project_id,
                pi_number=pi_num,
                output_path=args.output,
                db_path=db_path,
            )

        elif args.list_pis:
            result = list_pis(
                project_id=args.project_id,
                db_path=db_path,
            )
            if args.json:
                print(json.dumps(result, indent=2, default=str))

        elif args.details:
            pi_num = args.pi
            if not pi_num:
                parser.error("--details requires --pi PI_NUMBER")
            result = get_pi_details(
                project_id=args.project_id,
                pi_number=pi_num,
                db_path=db_path,
            )
            if args.json:
                print(json.dumps(result, indent=2, default=str))

        else:
            parser.print_help()
            sys.exit(1)

    except (FileNotFoundError, ValueError) as e:
        print(f"ERROR: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
