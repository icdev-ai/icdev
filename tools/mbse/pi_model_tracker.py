# CUI // SP-CTI
#!/usr/bin/env python3
"""SAFe Program Increment (PI) model state tracker.

Tracks model state per SAFe Program Increment: creates snapshots of SysML
elements, DOORS requirements, relationships, and digital thread links at
PI boundaries. Compares snapshots across PIs, computes model evolution
velocity, generates requirement burndown, and produces CUI-marked PI
model status reports.

Usage:
    # Create a PI snapshot
    python tools/mbse/pi_model_tracker.py \\
        --project-id proj-123 --pi PI-25.1 --snapshot --snapshot-type pi_start

    # Compare two PIs
    python tools/mbse/pi_model_tracker.py \\
        --project-id proj-123 --pi PI-25.1 --compare PI-24.4

    # Show model velocity across PIs
    python tools/mbse/pi_model_tracker.py \\
        --project-id proj-123 --velocity

    # Show requirement burndown
    python tools/mbse/pi_model_tracker.py \\
        --project-id proj-123 --burndown

    # Generate PI model report
    python tools/mbse/pi_model_tracker.py \\
        --project-id proj-123 --pi PI-25.1 --report

    # List all snapshots
    python tools/mbse/pi_model_tracker.py \\
        --project-id proj-123 --list

    # JSON output
    python tools/mbse/pi_model_tracker.py \\
        --project-id proj-123 --velocity --json
"""

import argparse
import hashlib
import json
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent.parent
DB_PATH = BASE_DIR / "data" / "icdev.db"

# Optional audit logger import
try:
    from tools.audit.audit_logger import log_event as audit_log_event
except ImportError:
    audit_log_event = None


# ------------------------------------------------------------------
# Database helpers
# ------------------------------------------------------------------

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


def _log_audit(conn, project_id, action, details):
    """Log an audit trail event for model snapshot operations."""
    try:
        conn.execute(
            """INSERT INTO audit_trail
               (project_id, event_type, actor, action, details, classification)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (
                project_id,
                "model_snapshot",
                "icdev-mbse-engine",
                action,
                json.dumps(details, default=str),
                "CUI",
            ),
        )
        conn.commit()
    except Exception as e:
        print(f"Warning: Could not log audit event: {e}", file=sys.stderr)


# ------------------------------------------------------------------
# Internal helpers
# ------------------------------------------------------------------

def _compute_content_hash(project_id: str, conn) -> str:
    """Compute SHA-256 of current model state.

    Concatenates sorted element IDs, relationship IDs, and requirement IDs
    to produce a deterministic hash representing the model's content at
    this point in time.
    """
    parts = []

    # Element IDs
    rows = conn.execute(
        "SELECT id FROM sysml_elements WHERE project_id = ? ORDER BY id",
        (project_id,),
    ).fetchall()
    parts.extend(row["id"] for row in rows)

    # Relationship IDs (integer, cast to str)
    rows = conn.execute(
        "SELECT id FROM sysml_relationships WHERE project_id = ? ORDER BY id",
        (project_id,),
    ).fetchall()
    parts.extend(str(row["id"]) for row in rows)

    # Requirement IDs
    rows = conn.execute(
        "SELECT id FROM doors_requirements WHERE project_id = ? ORDER BY id",
        (project_id,),
    ).fetchall()
    parts.extend(row["id"] for row in rows)

    content = "|".join(parts)
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def _get_snapshot_data(project_id: str, conn) -> dict:
    """Get detailed breakdown for the snapshot_data JSON field.

    Includes element type distribution, relationship type distribution,
    requirement type/status breakdown, thread link coverage, and
    model-code sync status summary.
    """
    data = {}

    # Element type breakdown
    rows = conn.execute(
        """SELECT element_type, COUNT(*) as cnt
           FROM sysml_elements WHERE project_id = ?
           GROUP BY element_type ORDER BY cnt DESC""",
        (project_id,),
    ).fetchall()
    data["element_types"] = {row["element_type"]: row["cnt"] for row in rows}

    # Relationship type breakdown
    rows = conn.execute(
        """SELECT relationship_type, COUNT(*) as cnt
           FROM sysml_relationships WHERE project_id = ?
           GROUP BY relationship_type ORDER BY cnt DESC""",
        (project_id,),
    ).fetchall()
    data["relationship_types"] = {row["relationship_type"]: row["cnt"] for row in rows}

    # Requirement type breakdown
    rows = conn.execute(
        """SELECT requirement_type, COUNT(*) as cnt
           FROM doors_requirements WHERE project_id = ?
           GROUP BY requirement_type ORDER BY cnt DESC""",
        (project_id,),
    ).fetchall()
    data["requirement_types"] = {row["requirement_type"]: row["cnt"] for row in rows}

    # Requirement status breakdown
    rows = conn.execute(
        """SELECT status, COUNT(*) as cnt
           FROM doors_requirements WHERE project_id = ?
           GROUP BY status ORDER BY cnt DESC""",
        (project_id,),
    ).fetchall()
    data["requirement_statuses"] = {row["status"]: row["cnt"] for row in rows}

    # Thread link type breakdown
    rows = conn.execute(
        """SELECT link_type, COUNT(*) as cnt
           FROM digital_thread_links WHERE project_id = ?
           GROUP BY link_type ORDER BY cnt DESC""",
        (project_id,),
    ).fetchall()
    data["thread_link_types"] = {row["link_type"]: row["cnt"] for row in rows}

    # Coverage: requirements linked via digital thread
    total_reqs = conn.execute(
        "SELECT COUNT(*) as cnt FROM doors_requirements WHERE project_id = ?",
        (project_id,),
    ).fetchone()["cnt"]
    linked_reqs = conn.execute(
        """SELECT COUNT(DISTINCT source_id) as cnt FROM digital_thread_links
           WHERE project_id = ? AND source_type = 'doors_requirement'""",
        (project_id,),
    ).fetchone()["cnt"]
    data["coverage"] = {
        "total_requirements": total_reqs,
        "linked_requirements": linked_reqs,
        "unlinked_requirements": total_reqs - linked_reqs,
        "coverage_pct": round((linked_reqs / total_reqs * 100), 1) if total_reqs > 0 else 0.0,
    }

    # Model-code sync status summary
    try:
        rows = conn.execute(
            """SELECT sync_status, COUNT(*) as cnt
               FROM model_code_mappings WHERE project_id = ?
               GROUP BY sync_status""",
            (project_id,),
        ).fetchall()
        data["model_code_sync"] = {row["sync_status"]: row["cnt"] for row in rows}
    except Exception:
        data["model_code_sync"] = {}

    return data


# ------------------------------------------------------------------
# Core functions
# ------------------------------------------------------------------

def create_pi_snapshot(project_id: str, pi_number: str,
                       snapshot_type: str = "manual", notes: str = None,
                       db_path=None) -> dict:
    """Create a snapshot of current model state for a PI.

    Counts all sysml_elements, sysml_relationships, doors_requirements,
    and digital_thread_links for the project. Computes a SHA-256 content
    hash of sorted element IDs. Stores detailed breakdown in snapshot_data.

    Args:
        project_id: The project identifier.
        pi_number: PI identifier (e.g. 'PI-25.1').
        snapshot_type: One of pi_start, pi_end, baseline, milestone, manual.
        notes: Optional notes for the snapshot.
        db_path: Optional database path override.

    Returns:
        Dict with snapshot_id, pi_number, counts, and content_hash.
    """
    conn = _get_connection(db_path)
    try:
        # Count elements
        element_count = conn.execute(
            "SELECT COUNT(*) as cnt FROM sysml_elements WHERE project_id = ?",
            (project_id,),
        ).fetchone()["cnt"]

        relationship_count = conn.execute(
            "SELECT COUNT(*) as cnt FROM sysml_relationships WHERE project_id = ?",
            (project_id,),
        ).fetchone()["cnt"]

        requirement_count = conn.execute(
            "SELECT COUNT(*) as cnt FROM doors_requirements WHERE project_id = ?",
            (project_id,),
        ).fetchone()["cnt"]

        thread_link_count = conn.execute(
            "SELECT COUNT(*) as cnt FROM digital_thread_links WHERE project_id = ?",
            (project_id,),
        ).fetchone()["cnt"]

        # Compute content hash and detailed snapshot data
        content_hash = _compute_content_hash(project_id, conn)
        snapshot_data = _get_snapshot_data(project_id, conn)

        # Insert snapshot
        cursor = conn.execute(
            """INSERT INTO model_snapshots
               (project_id, pi_number, snapshot_type, element_count,
                relationship_count, requirement_count, thread_link_count,
                content_hash, snapshot_data, notes)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                project_id,
                pi_number,
                snapshot_type,
                element_count,
                relationship_count,
                requirement_count,
                thread_link_count,
                content_hash,
                json.dumps(snapshot_data, default=str),
                notes,
            ),
        )
        conn.commit()
        snapshot_id = cursor.lastrowid

        result = {
            "snapshot_id": snapshot_id,
            "pi_number": pi_number,
            "snapshot_type": snapshot_type,
            "element_count": element_count,
            "relationship_count": relationship_count,
            "requirement_count": requirement_count,
            "thread_link_count": thread_link_count,
            "content_hash": content_hash,
        }

        _log_audit(conn, project_id, f"PI {pi_number} model snapshot created ({snapshot_type})", result)

        print(f"Model snapshot created for {pi_number} ({snapshot_type})")
        print(f"  Snapshot ID:    {snapshot_id}")
        print(f"  Elements:       {element_count}")
        print(f"  Relationships:  {relationship_count}")
        print(f"  Requirements:   {requirement_count}")
        print(f"  Thread links:   {thread_link_count}")
        print(f"  Content hash:   {content_hash[:16]}...")

        return result

    finally:
        conn.close()


def compare_pi_snapshots(project_id: str, pi_from: str, pi_to: str,
                         db_path=None) -> dict:
    """Diff between two PI snapshots.

    Retrieves the most recent snapshot for each PI and computes deltas
    across element counts, relationship counts, requirement counts,
    thread link counts, and content hash changes.

    Args:
        project_id: The project identifier.
        pi_from: Source PI number (e.g. 'PI-24.4').
        pi_to: Target PI number (e.g. 'PI-25.1').
        db_path: Optional database path override.

    Returns:
        Dict with deltas and comparison details.
    """
    conn = _get_connection(db_path)
    try:
        snap_from = conn.execute(
            """SELECT * FROM model_snapshots
               WHERE project_id = ? AND pi_number = ?
               ORDER BY created_at DESC LIMIT 1""",
            (project_id, pi_from),
        ).fetchone()
        if not snap_from:
            raise ValueError(f"No snapshot found for PI '{pi_from}' in project '{project_id}'.")

        snap_to = conn.execute(
            """SELECT * FROM model_snapshots
               WHERE project_id = ? AND pi_number = ?
               ORDER BY created_at DESC LIMIT 1""",
            (project_id, pi_to),
        ).fetchone()
        if not snap_to:
            raise ValueError(f"No snapshot found for PI '{pi_to}' in project '{project_id}'.")

        snap_from = dict(snap_from)
        snap_to = dict(snap_to)

        elements_delta = snap_to["element_count"] - snap_from["element_count"]
        relationships_delta = snap_to["relationship_count"] - snap_from["relationship_count"]
        requirements_delta = snap_to["requirement_count"] - snap_from["requirement_count"]
        thread_links_delta = snap_to["thread_link_count"] - snap_from["thread_link_count"]
        hash_changed = snap_from["content_hash"] != snap_to["content_hash"]

        # Parse snapshot_data for detailed comparison
        data_from = {}
        data_to = {}
        try:
            data_from = json.loads(snap_from["snapshot_data"]) if snap_from["snapshot_data"] else {}
        except (json.JSONDecodeError, TypeError):
            pass
        try:
            data_to = json.loads(snap_to["snapshot_data"]) if snap_to["snapshot_data"] else {}
        except (json.JSONDecodeError, TypeError):
            pass

        # Element type deltas
        elem_types_from = data_from.get("element_types", {})
        elem_types_to = data_to.get("element_types", {})
        all_elem_types = set(elem_types_from.keys()) | set(elem_types_to.keys())
        element_type_deltas = {}
        for et in sorted(all_elem_types):
            delta = elem_types_to.get(et, 0) - elem_types_from.get(et, 0)
            if delta != 0:
                element_type_deltas[et] = delta

        # Coverage deltas
        cov_from = data_from.get("coverage", {})
        cov_to = data_to.get("coverage", {})
        coverage_delta = {
            "coverage_pct_from": cov_from.get("coverage_pct", 0.0),
            "coverage_pct_to": cov_to.get("coverage_pct", 0.0),
            "coverage_pct_delta": round(
                cov_to.get("coverage_pct", 0.0) - cov_from.get("coverage_pct", 0.0), 1
            ),
        }

        result = {
            "pi_from": pi_from,
            "pi_to": pi_to,
            "elements_delta": elements_delta,
            "relationships_delta": relationships_delta,
            "requirements_delta": requirements_delta,
            "thread_links_delta": thread_links_delta,
            "hash_changed": hash_changed,
            "details": {
                "element_type_deltas": element_type_deltas,
                "coverage_delta": coverage_delta,
                "from_snapshot": {
                    "snapshot_type": snap_from["snapshot_type"],
                    "created_at": snap_from["created_at"],
                    "content_hash": snap_from["content_hash"],
                },
                "to_snapshot": {
                    "snapshot_type": snap_to["snapshot_type"],
                    "created_at": snap_to["created_at"],
                    "content_hash": snap_to["content_hash"],
                },
            },
        }

        print(f"PI Comparison: {pi_from} -> {pi_to}")
        print(f"  Elements:       {elements_delta:+d}")
        print(f"  Relationships:  {relationships_delta:+d}")
        print(f"  Requirements:   {requirements_delta:+d}")
        print(f"  Thread links:   {thread_links_delta:+d}")
        print(f"  Hash changed:   {hash_changed}")
        if element_type_deltas:
            print("  Element type changes:")
            for et, delta in element_type_deltas.items():
                print(f"    {et}: {delta:+d}")
        if coverage_delta["coverage_pct_delta"] != 0:
            print(f"  Coverage: {coverage_delta['coverage_pct_from']}% -> "
                  f"{coverage_delta['coverage_pct_to']}% "
                  f"({coverage_delta['coverage_pct_delta']:+.1f}%)")

        return result

    finally:
        conn.close()


def get_model_velocity(project_id: str, db_path=None) -> dict:
    """Compute model evolution velocity across PIs.

    Analyzes the rate of change in model elements, requirements, and
    thread links across successive PI snapshots. Determines whether the
    trend is improving, stable, or declining.

    Args:
        project_id: The project identifier.
        db_path: Optional database path override.

    Returns:
        Dict with pi_count, averages, trend, and per-PI breakdown.
    """
    conn = _get_connection(db_path)
    try:
        # Get all snapshots ordered by created_at
        rows = conn.execute(
            """SELECT pi_number, snapshot_type, element_count, relationship_count,
                      requirement_count, thread_link_count, created_at
               FROM model_snapshots
               WHERE project_id = ?
               ORDER BY created_at ASC""",
            (project_id,),
        ).fetchall()

        if not rows:
            return {
                "pi_count": 0,
                "avg_elements_per_pi": 0.0,
                "avg_requirements_per_pi": 0.0,
                "avg_thread_links_per_pi": 0.0,
                "trend": "stable",
                "per_pi": [],
                "message": "No snapshots found for this project.",
            }

        # Group by PI, take the latest snapshot per PI
        pi_snapshots = {}
        for row in rows:
            pi = row["pi_number"]
            pi_snapshots[pi] = dict(row)

        pis_ordered = list(pi_snapshots.keys())
        per_pi = []
        elements_added_list = []
        requirements_added_list = []
        thread_links_added_list = []

        prev_snap = None
        for pi in pis_ordered:
            snap = pi_snapshots[pi]
            entry = {
                "pi": pi,
                "elements": snap["element_count"],
                "relationships": snap["relationship_count"],
                "requirements": snap["requirement_count"],
                "thread_links": snap["thread_link_count"],
            }

            if prev_snap is not None:
                entry["elements_added"] = snap["element_count"] - prev_snap["element_count"]
                entry["requirements_added"] = snap["requirement_count"] - prev_snap["requirement_count"]
                entry["thread_links_added"] = snap["thread_link_count"] - prev_snap["thread_link_count"]
                elements_added_list.append(entry["elements_added"])
                requirements_added_list.append(entry["requirements_added"])
                thread_links_added_list.append(entry["thread_links_added"])
            else:
                entry["elements_added"] = snap["element_count"]
                entry["requirements_added"] = snap["requirement_count"]
                entry["thread_links_added"] = snap["thread_link_count"]

            per_pi.append(entry)
            prev_snap = snap

        pi_count = len(pis_ordered)
        divisor = max(len(elements_added_list), 1)
        avg_elements = round(sum(elements_added_list) / divisor, 1) if elements_added_list else 0.0
        avg_requirements = round(sum(requirements_added_list) / divisor, 1) if requirements_added_list else 0.0
        avg_thread_links = round(sum(thread_links_added_list) / divisor, 1) if thread_links_added_list else 0.0

        # Determine trend from last 3 PIs (or all if fewer)
        trend = "stable"
        if len(elements_added_list) >= 2:
            recent = elements_added_list[-min(3, len(elements_added_list)):]
            if len(recent) >= 2:
                if recent[-1] > recent[0] * 1.1:
                    trend = "improving"
                elif recent[-1] < recent[0] * 0.9:
                    trend = "declining"

        result = {
            "pi_count": pi_count,
            "avg_elements_per_pi": avg_elements,
            "avg_requirements_per_pi": avg_requirements,
            "avg_thread_links_per_pi": avg_thread_links,
            "trend": trend,
            "per_pi": per_pi,
        }

        print(f"Model Velocity for project {project_id}")
        print(f"  PI count:             {pi_count}")
        print(f"  Avg elements/PI:      {avg_elements}")
        print(f"  Avg requirements/PI:  {avg_requirements}")
        print(f"  Avg thread links/PI:  {avg_thread_links}")
        print(f"  Trend:                {trend}")
        print()
        for entry in per_pi:
            print(f"  {entry['pi']}: elements={entry['elements']} "
                  f"(+{entry.get('elements_added', 0)}), "
                  f"reqs={entry['requirements']} "
                  f"(+{entry.get('requirements_added', 0)}), "
                  f"links={entry['thread_links']} "
                  f"(+{entry.get('thread_links_added', 0)})")

        return result

    finally:
        conn.close()


def get_model_burndown(project_id: str, db_path=None) -> dict:
    """Compute burndown: unlinked requirements remaining vs PI timeline.

    Tracks the number of requirements that are not yet linked via the
    digital thread across successive PI snapshots, providing a burndown
    view toward full traceability.

    Args:
        project_id: The project identifier.
        db_path: Optional database path override.

    Returns:
        Dict with total, linked, unlinked counts, burndown percentage,
        and per-PI breakdown.
    """
    conn = _get_connection(db_path)
    try:
        # Current state
        total_requirements = conn.execute(
            "SELECT COUNT(*) as cnt FROM doors_requirements WHERE project_id = ?",
            (project_id,),
        ).fetchone()["cnt"]

        linked_requirements = conn.execute(
            """SELECT COUNT(DISTINCT source_id) as cnt FROM digital_thread_links
               WHERE project_id = ? AND source_type = 'doors_requirement'""",
            (project_id,),
        ).fetchone()["cnt"]

        unlinked_remaining = total_requirements - linked_requirements
        burndown_pct = round(
            (linked_requirements / total_requirements * 100), 1
        ) if total_requirements > 0 else 0.0

        # Per-PI burndown from snapshot data
        rows = conn.execute(
            """SELECT pi_number, snapshot_data, created_at
               FROM model_snapshots
               WHERE project_id = ?
               ORDER BY created_at ASC""",
            (project_id,),
        ).fetchall()

        # Deduplicate by PI (take latest per PI)
        pi_data_map = {}
        for row in rows:
            pi_data_map[row["pi_number"]] = row

        per_pi = []
        for pi_num, row in pi_data_map.items():
            snap_data = {}
            try:
                snap_data = json.loads(row["snapshot_data"]) if row["snapshot_data"] else {}
            except (json.JSONDecodeError, TypeError):
                pass
            coverage = snap_data.get("coverage", {})
            per_pi.append({
                "pi": pi_num,
                "total_requirements": coverage.get("total_requirements", 0),
                "linked_requirements": coverage.get("linked_requirements", 0),
                "unlinked_remaining": coverage.get("unlinked_requirements", 0),
                "coverage_pct": coverage.get("coverage_pct", 0.0),
                "snapshot_date": row["created_at"],
            })

        result = {
            "total_requirements": total_requirements,
            "linked_requirements": linked_requirements,
            "unlinked_remaining": unlinked_remaining,
            "burndown_pct": burndown_pct,
            "per_pi": per_pi,
        }

        print(f"Model Burndown for project {project_id}")
        print(f"  Total requirements:  {total_requirements}")
        print(f"  Linked:              {linked_requirements}")
        print(f"  Unlinked remaining:  {unlinked_remaining}")
        print(f"  Burndown progress:   {burndown_pct}%")
        if per_pi:
            print()
            for entry in per_pi:
                print(f"  {entry['pi']}: "
                      f"{entry['linked_requirements']}/{entry['total_requirements']} linked "
                      f"({entry['coverage_pct']}%), "
                      f"{entry['unlinked_remaining']} remaining")

        return result

    finally:
        conn.close()


def generate_pi_model_report(project_id: str, pi_number: str,
                              db_path=None) -> str:
    """Generate a CUI-marked PI model status report.

    Includes snapshot summary, comparison with the previous PI,
    model velocity, requirement burndown, and recommendations for
    the next PI.

    Args:
        project_id: The project identifier.
        pi_number: PI identifier (e.g. 'PI-25.1').
        db_path: Optional database path override.

    Returns:
        The report content as a CUI-marked markdown string.
    """
    conn = _get_connection(db_path)
    try:
        # Get the target snapshot
        snap_row = conn.execute(
            """SELECT * FROM model_snapshots
               WHERE project_id = ? AND pi_number = ?
               ORDER BY created_at DESC LIMIT 1""",
            (project_id, pi_number),
        ).fetchone()
        if not snap_row:
            raise ValueError(
                f"No snapshot found for PI '{pi_number}' in project '{project_id}'. "
                "Create one with --snapshot first."
            )
        snap = dict(snap_row)

        snap_data = {}
        try:
            snap_data = json.loads(snap["snapshot_data"]) if snap["snapshot_data"] else {}
        except (json.JSONDecodeError, TypeError):
            pass

        # Find previous PI snapshot for comparison
        prev_snap = conn.execute(
            """SELECT * FROM model_snapshots
               WHERE project_id = ? AND pi_number != ?
               AND created_at < ?
               ORDER BY created_at DESC LIMIT 1""",
            (project_id, pi_number, snap["created_at"]),
        ).fetchone()

        prev_pi = None
        comparison = None
        if prev_snap:
            prev_pi = prev_snap["pi_number"]

        # PI compliance integration
        pi_compliance = conn.execute(
            """SELECT * FROM pi_compliance_tracking
               WHERE project_id = ? AND pi_number = ?""",
            (project_id, pi_number),
        ).fetchone()

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

        lines = [
            cui_header,
            "",
            "# PI MODEL STATUS REPORT",
            "",
            f"**Project:** {project_id}",
            f"**Program Increment:** {pi_number}",
            f"**Snapshot Type:** {snap['snapshot_type']}",
            f"**Report Generated:** {now.strftime('%Y-%m-%d %H:%M UTC')}",
            "**Classification:** CUI // SP-CTI",
            "",
            "---",
            "",
            "## 1. Model Snapshot Summary",
            "",
            "| Metric | Count |",
            "|--------|-------|",
            f"| SysML Elements | {snap['element_count']} |",
            f"| Relationships | {snap['relationship_count']} |",
            f"| DOORS Requirements | {snap['requirement_count']} |",
            f"| Digital Thread Links | {snap['thread_link_count']} |",
            f"| Content Hash | `{snap['content_hash'][:16]}...` |",
            "",
        ]

        # Element type breakdown
        elem_types = snap_data.get("element_types", {})
        if elem_types:
            lines.append("### Element Type Distribution")
            lines.append("")
            lines.append("| Type | Count |")
            lines.append("|------|-------|")
            for et, cnt in sorted(elem_types.items(), key=lambda x: -x[1]):
                lines.append(f"| {et} | {cnt} |")
            lines.append("")

        # Coverage
        coverage = snap_data.get("coverage", {})
        if coverage:
            lines.append("### Traceability Coverage")
            lines.append("")
            lines.append(f"- **Total Requirements:** {coverage.get('total_requirements', 0)}")
            lines.append(f"- **Linked:** {coverage.get('linked_requirements', 0)}")
            lines.append(f"- **Unlinked:** {coverage.get('unlinked_requirements', 0)}")
            lines.append(f"- **Coverage:** {coverage.get('coverage_pct', 0.0)}%")
            lines.append("")

        lines.extend(["---", ""])

        # Comparison with previous PI
        lines.append("## 2. Comparison with Previous PI")
        lines.append("")
        if prev_pi:
            prev_snap_dict = dict(prev_snap)
            elem_d = snap["element_count"] - prev_snap_dict["element_count"]
            rel_d = snap["relationship_count"] - prev_snap_dict["relationship_count"]
            req_d = snap["requirement_count"] - prev_snap_dict["requirement_count"]
            link_d = snap["thread_link_count"] - prev_snap_dict["thread_link_count"]
            hash_diff = snap["content_hash"] != prev_snap_dict["content_hash"]

            lines.append(f"Compared against: **{prev_pi}**")
            lines.append("")
            lines.append("| Metric | Previous | Current | Delta |")
            lines.append("|--------|----------|---------|-------|")
            lines.append(f"| Elements | {prev_snap_dict['element_count']} | {snap['element_count']} | {elem_d:+d} |")
            lines.append(f"| Relationships | {prev_snap_dict['relationship_count']} | {snap['relationship_count']} | {rel_d:+d} |")
            lines.append(f"| Requirements | {prev_snap_dict['requirement_count']} | {snap['requirement_count']} | {req_d:+d} |")
            lines.append(f"| Thread Links | {prev_snap_dict['thread_link_count']} | {snap['thread_link_count']} | {link_d:+d} |")
            lines.append(f"| Hash Changed | -- | -- | {'Yes' if hash_diff else 'No'} |")
        else:
            lines.append("No previous PI snapshot available for comparison.")
        lines.append("")

        lines.extend(["---", ""])

        # Velocity (inline summary)
        lines.append("## 3. Model Velocity")
        lines.append("")
        all_snaps = conn.execute(
            """SELECT pi_number, element_count, requirement_count, thread_link_count
               FROM model_snapshots WHERE project_id = ?
               ORDER BY created_at ASC""",
            (project_id,),
        ).fetchall()
        pi_snap_map = {}
        for r in all_snaps:
            pi_snap_map[r["pi_number"]] = dict(r)
        pi_list = list(pi_snap_map.keys())

        if len(pi_list) >= 2:
            elem_deltas = []
            for i in range(1, len(pi_list)):
                prev = pi_snap_map[pi_list[i - 1]]
                curr = pi_snap_map[pi_list[i]]
                elem_deltas.append(curr["element_count"] - prev["element_count"])
            avg_vel = round(sum(elem_deltas) / len(elem_deltas), 1)
            lines.append(f"- **Average elements added per PI:** {avg_vel}")
            lines.append(f"- **PIs tracked:** {len(pi_list)}")
        else:
            lines.append("Insufficient data for velocity calculation (need >= 2 snapshots).")
        lines.append("")

        lines.extend(["---", ""])

        # Burndown
        lines.append("## 4. Requirement Burndown")
        lines.append("")
        if coverage:
            total_r = coverage.get("total_requirements", 0)
            linked_r = coverage.get("linked_requirements", 0)
            unlinked_r = coverage.get("unlinked_requirements", 0)
            cov_pct = coverage.get("coverage_pct", 0.0)
            lines.append(f"- **Total requirements:** {total_r}")
            lines.append(f"- **Linked (traced):** {linked_r}")
            lines.append(f"- **Unlinked remaining:** {unlinked_r}")
            lines.append(f"- **Traceability coverage:** {cov_pct}%")
            if cov_pct >= 100.0:
                lines.append("- **Status:** FULL TRACEABILITY ACHIEVED")
            elif cov_pct >= 80.0:
                lines.append("- **Status:** On track")
            else:
                lines.append("- **Status:** Below target (80%)")
        else:
            lines.append("No coverage data available.")
        lines.append("")

        lines.extend(["---", ""])

        # PI compliance integration
        lines.append("## 5. PI Compliance Integration")
        lines.append("")
        if pi_compliance:
            pi_c = dict(pi_compliance)
            lines.append(f"- **Compliance score (start):** {pi_c.get('compliance_score_start', 'N/A')}")
            lines.append(f"- **Compliance score (end):** {pi_c.get('compliance_score_end', 'N/A')}")
            lines.append(f"- **Controls implemented:** {pi_c.get('controls_implemented', 0)}")
            lines.append(f"- **Controls remaining:** {pi_c.get('controls_remaining', 0)}")
            lines.append(f"- **POAM items closed:** {pi_c.get('poam_items_closed', 0)}")
        else:
            lines.append(f"No PI compliance tracking record found for {pi_number}.")
        lines.append("")

        lines.extend(["---", ""])

        # Recommendations
        lines.append("## 6. Recommendations")
        lines.append("")
        recommendations = []
        if coverage:
            cov_pct = coverage.get("coverage_pct", 0.0)
            unlinked_r = coverage.get("unlinked_requirements", 0)
            if cov_pct < 80.0:
                recommendations.append(
                    f"- **Traceability Gap:** {unlinked_r} requirements lack digital thread "
                    f"links. Target 80% coverage by next PI."
                )
            if cov_pct >= 80.0 and cov_pct < 100.0:
                recommendations.append(
                    f"- **Near Complete:** {unlinked_r} requirements remaining. "
                    f"Prioritize linking to close traceability gaps."
                )

        sync_status = snap_data.get("model_code_sync", {})
        conflicts = sync_status.get("conflict", 0)
        model_ahead = sync_status.get("model_ahead", 0)
        code_ahead = sync_status.get("code_ahead", 0)
        if conflicts > 0:
            recommendations.append(
                f"- **Sync Conflicts:** {conflicts} model-code conflicts detected. "
                f"Resolve before PI end."
            )
        if model_ahead > 0 or code_ahead > 0:
            recommendations.append(
                f"- **Sync Drift:** {model_ahead} model-ahead, {code_ahead} code-ahead. "
                f"Synchronize model and code."
            )

        if not recommendations:
            recommendations.append("- Model state is healthy. Continue current pace.")

        lines.extend(recommendations)
        lines.extend([
            "",
            "---",
            "",
            cui_footer,
            "",
        ])

        report_content = "\n".join(lines)

        _log_audit(conn, project_id, f"PI {pi_number} model report generated", {
            "pi_number": pi_number,
        })

        print(f"PI model report generated for {pi_number}")
        return report_content

    finally:
        conn.close()


def list_snapshots(project_id: str, db_path=None) -> list:
    """List all snapshots for a project, ordered by created_at.

    Args:
        project_id: The project identifier.
        db_path: Optional database path override.

    Returns:
        List of snapshot summary dicts.
    """
    conn = _get_connection(db_path)
    try:
        rows = conn.execute(
            """SELECT id, pi_number, snapshot_type, element_count,
                      relationship_count, requirement_count, thread_link_count,
                      content_hash, notes, created_at
               FROM model_snapshots
               WHERE project_id = ?
               ORDER BY created_at ASC""",
            (project_id,),
        ).fetchall()

        snapshots = []
        for row in rows:
            snapshots.append({
                "id": row["id"],
                "pi_number": row["pi_number"],
                "snapshot_type": row["snapshot_type"],
                "element_count": row["element_count"],
                "relationship_count": row["relationship_count"],
                "requirement_count": row["requirement_count"],
                "thread_link_count": row["thread_link_count"],
                "content_hash": row["content_hash"],
                "notes": row["notes"],
                "created_at": row["created_at"],
            })

        print(f"Snapshots for project {project_id}: ({len(snapshots)} total)")
        if snapshots:
            print()
            print(f"  {'ID':<6} {'PI':<12} {'Type':<12} {'Elems':<8} {'Rels':<8} {'Reqs':<8} {'Links':<8} {'Created'}")
            print(f"  {'-'*6} {'-'*12} {'-'*12} {'-'*8} {'-'*8} {'-'*8} {'-'*8} {'-'*20}")
            for s in snapshots:
                print(f"  {s['id']:<6} {s['pi_number']:<12} {s['snapshot_type']:<12} "
                      f"{s['element_count']:<8} {s['relationship_count']:<8} "
                      f"{s['requirement_count']:<8} {s['thread_link_count']:<8} "
                      f"{s['created_at']}")
        else:
            print("  No snapshots found. Use --snapshot to create one.")

        return snapshots

    finally:
        conn.close()


# ------------------------------------------------------------------
# CLI
# ------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="SAFe PI Model Tracking -- snapshots, velocity, burndown"
    )
    parser.add_argument("--project-id", required=True, help="Project identifier")
    parser.add_argument("--pi", help="PI number (e.g., PI-25.1)")
    parser.add_argument("--snapshot", action="store_true", help="Create PI snapshot")
    parser.add_argument(
        "--snapshot-type", default="manual",
        choices=["pi_start", "pi_end", "baseline", "milestone", "manual"],
        help="Snapshot type (default: manual)",
    )
    parser.add_argument("--compare", help="Compare with another PI (e.g., PI-24.4)")
    parser.add_argument("--velocity", action="store_true", help="Show model velocity across PIs")
    parser.add_argument("--burndown", action="store_true", help="Show requirement burndown")
    parser.add_argument("--report", action="store_true", help="Generate PI model report")
    parser.add_argument("--list", action="store_true", help="List all snapshots")
    parser.add_argument("--json", action="store_true", help="Output as JSON")
    parser.add_argument("--db-path", type=Path, help="Database path override")
    parser.add_argument("--notes", help="Notes for snapshot")

    args = parser.parse_args()
    db_path = args.db_path if args.db_path else None

    try:
        if args.snapshot:
            if not args.pi:
                parser.error("--snapshot requires --pi PI_NUMBER")
            result = create_pi_snapshot(
                project_id=args.project_id,
                pi_number=args.pi,
                snapshot_type=args.snapshot_type,
                notes=args.notes,
                db_path=db_path,
            )
            if args.json:
                print(json.dumps(result, indent=2, default=str))

        elif args.compare:
            if not args.pi:
                parser.error("--compare requires --pi PI_NUMBER (the target PI)")
            result = compare_pi_snapshots(
                project_id=args.project_id,
                pi_from=args.compare,
                pi_to=args.pi,
                db_path=db_path,
            )
            if args.json:
                print(json.dumps(result, indent=2, default=str))

        elif args.velocity:
            result = get_model_velocity(
                project_id=args.project_id,
                db_path=db_path,
            )
            if args.json:
                print(json.dumps(result, indent=2, default=str))

        elif args.burndown:
            result = get_model_burndown(
                project_id=args.project_id,
                db_path=db_path,
            )
            if args.json:
                print(json.dumps(result, indent=2, default=str))

        elif args.report:
            if not args.pi:
                parser.error("--report requires --pi PI_NUMBER")
            result = generate_pi_model_report(
                project_id=args.project_id,
                pi_number=args.pi,
                db_path=db_path,
            )
            if args.json:
                print(json.dumps({"report": result}, indent=2))
            else:
                print()
                print(result)

        elif args.list:
            result = list_snapshots(
                project_id=args.project_id,
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
# CUI // SP-CTI
