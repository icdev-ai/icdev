#!/usr/bin/env python3
# CUI // SP-CTI
"""Project PI Timeline Loader

Loads the default 18-month PI timeline template from args/project_timelines.yaml
and overlays per-project status from the project_pi_timeline table.

Usage:
    from tools.project.timeline_loader import load_default_timeline, get_project_timeline
    default = load_default_timeline()
    project_timeline = get_project_timeline("proj-123")
"""

import json
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent.parent
TIMELINE_REGISTRY = BASE_DIR / "args" / "project_timelines.yaml"


def _load_yaml(filepath: Path) -> dict:
    """Load a YAML file. Uses PyYAML if available, otherwise returns empty."""
    if not filepath.exists():
        return {}
    try:
        import yaml

        with open(filepath, "r", encoding="utf-8") as fh:
            return yaml.safe_load(fh) or {}
    except ImportError:
        return {}


def load_default_timeline(registry_path: Path = None) -> dict:
    """Load the default 18-month PI timeline template.

    Returns:
        Dict with name, description, total_months, phase_groups, and timeline.
    """
    path = registry_path or TIMELINE_REGISTRY
    data = _load_yaml(path)
    return {
        "name": data.get("name", "18-Month Contract Timeline"),
        "description": data.get("description", ""),
        "total_months": data.get("total_months", 18),
        "months_per_pi": data.get("months_per_pi", 2),
        "phase_groups": data.get("phase_groups", {}),
        "timeline": data.get("timeline", []),
    }


def _get_connection():
    from tools.db.storage import get_connection

    return get_connection()


def get_project_timeline(project_id: str, db_conn=None) -> dict:
    """Get the full timeline for a project, overlaying stored status.

    If no stored entries exist for the project, returns the default template
    with all PIs marked as 'planned'.

    Args:
        project_id: The project identifier.
        db_conn: Optional existing DB connection.

    Returns:
        Dict with project_id, phase_groups, and timeline (with status overlay).
    """
    default = load_default_timeline()
    phase_groups = default.get("phase_groups", {})
    timeline = default.get("timeline", [])

    conn = db_conn or _get_connection()
    close_conn = db_conn is None

    try:
        # Check for stored timeline entries
        rows = conn.execute(
            """SELECT pi_number, status, progress_pct, start_date, end_date,
                      milestones, notes
               FROM project_pi_timeline
               WHERE project_id = ?
               ORDER BY pi_number""",
            (project_id,),
        ).fetchall()

        status_map = {}
        for row in rows:
            status_map[row["pi_number"]] = {
                "status": row["status"],
                "progress_pct": row["progress_pct"],
                "start_date": row["start_date"],
                "end_date": row["end_date"],
                "milestones": row["milestones"],
                "notes": row["notes"],
            }

        # Overlay status onto default timeline
        enriched_timeline = []
        for pi in timeline:
            pi_copy = dict(pi)
            stored = status_map.get(pi["pi_number"], {})
            if stored:
                pi_copy["status"] = stored["status"]
                pi_copy["progress_pct"] = stored["progress_pct"]
                if stored.get("start_date"):
                    pi_copy["start_date"] = stored["start_date"]
                if stored.get("end_date"):
                    pi_copy["end_date"] = stored["end_date"]
                if stored.get("milestones"):
                    try:
                        pi_copy["milestones"] = json.loads(stored["milestones"])
                    except (json.JSONDecodeError, TypeError):
                        pass
                if stored.get("notes"):
                    pi_copy["notes"] = stored["notes"]
            else:
                pi_copy["status"] = "planned"
                pi_copy["progress_pct"] = 0

            enriched_timeline.append(pi_copy)

        # Compute summary stats
        total_pis = len(enriched_timeline)
        completed = sum(1 for p in enriched_timeline if p["status"] == "completed")
        active = sum(1 for p in enriched_timeline if p["status"] == "active")
        delayed = sum(1 for p in enriched_timeline if p["status"] == "delayed")
        overall_progress = round(
            sum(p.get("progress_pct", 0) for p in enriched_timeline) / max(total_pis, 1)
        )

        return {
            "project_id": project_id,
            "name": default.get("name"),
            "description": default.get("description"),
            "total_months": default.get("total_months"),
            "phase_groups": phase_groups,
            "timeline": enriched_timeline,
            "summary": {
                "total_pis": total_pis,
                "completed": completed,
                "active": active,
                "delayed": delayed,
                "planned": total_pis - completed - active - delayed,
                "overall_progress_pct": overall_progress,
            },
        }

    finally:
        if close_conn:
            conn.close()


def seed_project_timeline(project_id: str, contract_award_date: str = None, db_conn=None) -> dict:
    """Seed the default 18-month PI timeline for a project.

    Args:
        project_id: The project identifier.
        contract_award_date: Optional contract award date (YYYY-MM-DD).
                             If provided, PI start/end dates are computed.
        db_conn: Optional existing DB connection.

    Returns:
        Dict with project_id and count of seeded PIs.
    """
    from datetime import datetime, timedelta

    default = load_default_timeline()
    timeline = default.get("timeline", [])

    conn = db_conn or _get_connection()
    close_conn = db_conn is None

    try:
        # Parse award date if provided
        award_date = None
        if contract_award_date:
            try:
                award_date = datetime.strptime(contract_award_date, "%Y-%m-%d")
            except ValueError:
                award_date = None

        count = 0
        for pi in timeline:
            # Compute dates from month offsets
            start_date = None
            end_date = None
            if award_date:
                month_start = pi.get("month_start", 1)
                month_end = pi.get("month_end", 2)
                start_date = (award_date + timedelta(days=(month_start - 1) * 30)).strftime("%Y-%m-%d")
                end_date = (award_date + timedelta(days=month_end * 30 - 1)).strftime("%Y-%m-%d")

            milestones_json = json.dumps(pi.get("milestones", []), default=str)

            conn.execute(
                """INSERT OR IGNORE INTO project_pi_timeline
                   (project_id, pi_number, pi_name, pi_theme, pi_phase_group,
                    start_date, end_date, status, milestones, progress_pct, notes)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    project_id,
                    pi["pi_number"],
                    pi.get("pi_name"),
                    pi.get("pi_theme"),
                    pi.get("phase_group", "discovery_planning"),
                    start_date,
                    end_date,
                    "planned",
                    milestones_json,
                    0,
                    None,
                ),
            )
            count += 1

        conn.commit()

        return {
            "project_id": project_id,
            "seeded": count,
            "contract_award_date": contract_award_date,
        }

    finally:
        if close_conn:
            conn.close()


def update_pi_status(
    project_id: str,
    pi_number: str,
    status: str,
    progress_pct: int = None,
    notes: str = None,
    db_conn=None,
) -> dict:
    """Update the status of a single PI for a project.

    Args:
        project_id: The project identifier.
        pi_number: PI number (e.g. 'PI-1').
        status: One of 'planned', 'active', 'completed', 'delayed'.
        progress_pct: Optional progress percentage (0-100).
        notes: Optional notes.
        db_conn: Optional existing DB connection.

    Returns:
        Dict with project_id, pi_number, and updated fields.
    """
    conn = db_conn or _get_connection()
    close_conn = db_conn is None

    try:
        # Ensure the PI exists (seed if not)
        existing = conn.execute(
            "SELECT id FROM project_pi_timeline WHERE project_id = ? AND pi_number = ?",
            (project_id, pi_number),
        ).fetchone()

        if not existing:
            # Seed the full timeline first
            seed_project_timeline(project_id, db_conn=conn)

        # Build update
        fields = ["status = ?"]
        params = [status]

        if progress_pct is not None:
            fields.append("progress_pct = ?")
            params.append(max(0, min(100, progress_pct)))
        if notes is not None:
            fields.append("notes = ?")
            params.append(notes)

        params.extend([project_id, pi_number])

        conn.execute(
            f"UPDATE project_pi_timeline SET {', '.join(fields)} WHERE project_id = ? AND pi_number = ?",
            tuple(params),
        )
        conn.commit()

        return {
            "project_id": project_id,
            "pi_number": pi_number,
            "status": status,
            "progress_pct": progress_pct,
            "notes": notes,
        }

    finally:
        if close_conn:
            conn.close()
