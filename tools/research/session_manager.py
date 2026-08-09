#!/usr/bin/env python3
# CUI // SP-CTI
# Controlled by: Department of Defense
# CUI Category: CTI
# Distribution: D
# POC: ICDEV™ System Administrator
"""Research Engine Session Manager — lifecycle management for research sessions.

Manages the full session lifecycle from creation through archival:
    created -> scoping -> scanning -> synthesizing -> dossier_ready
    -> reviewed -> child_app_triggered -> archived

Pipeline stages advance through:
    SCOPE -> LANDSCAPE -> REGULATE -> COMMUNITY -> ACADEMIC
    -> BUILD_BUY -> SYNTHESIZE -> DOSSIER

Architecture:
    - Session lifecycle state machine with deterministic stage advancement (D-RES-1)
    - research_sessions table allows UPDATE for status transitions (D-RES-5)
    - Vertical validation via DB lookup before session creation
    - Signal/challenge counts refreshed from child tables on demand
    - All status transitions are audit-logged (D6 append-only audit trail)
    - Parameterized SQL throughout (no string interpolation)

Usage:
    python tools/research/session_manager.py --create --name "HealthIT Research" --vertical healthcare --json
    python tools/research/session_manager.py --get --session-id rsess-abc123 --json
    python tools/research/session_manager.py --list --json
    python tools/research/session_manager.py --list --status scanning --json
    python tools/research/session_manager.py --list --vertical healthcare --json
    python tools/research/session_manager.py --advance --session-id rsess-abc123 --json
    python tools/research/session_manager.py --status --session-id rsess-abc123 --json
    python tools/research/session_manager.py --human
"""

import argparse
import json
import os
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

# =========================================================================
# PATH SETUP
# =========================================================================
BASE_DIR = Path(__file__).resolve().parent.parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from tools.db.storage import get_connection  # noqa: E402

DB_PATH = Path(os.environ.get("ICDEV_DB_PATH", str(BASE_DIR / "data" / "icdev.db")))
CONFIG_PATH = BASE_DIR / "args" / "research_config.yaml"

# =========================================================================
# GRACEFUL IMPORTS
# =========================================================================
try:
    import yaml

    _HAS_YAML = True
except ImportError:
    _HAS_YAML = False

try:
    from tools.audit.audit_logger import log_event as audit_log_event

    _HAS_AUDIT = True
except ImportError:
    _HAS_AUDIT = False

    def audit_log_event(**kwargs):
        return -1


# =========================================================================
# CONSTANTS
# =========================================================================
PIPELINE_STAGES = [
    "SCOPE",
    "LANDSCAPE",
    "REGULATE",
    "COMMUNITY",
    "ACADEMIC",
    "BUILD_BUY",
    "SYNTHESIZE",
    "FORECAST",
    "DOSSIER",
]

STAGE_TO_STATUS = {
    "SCOPE": "scoping",
    "LANDSCAPE": "scanning",
    "REGULATE": "scanning",
    "COMMUNITY": "scanning",
    "ACADEMIC": "scanning",
    "BUILD_BUY": "scanning",
    "SYNTHESIZE": "synthesizing",
    "FORECAST": "synthesizing",
    "DOSSIER": "dossier_ready",
}

VALID_STATUSES = (
    "created",
    "scoping",
    "scanning",
    "synthesizing",
    "dossier_ready",
    "reviewed",
    "child_app_triggered",
    "archived",
)


# =========================================================================
# HELPERS
# =========================================================================
def _get_db(db_path=None):
    """Get database connection with dict-like row access."""
    path = db_path or DB_PATH
    if not Path(str(path)).exists():
        raise FileNotFoundError(f"Database not found: {path}")
    conn = get_connection(db_path=str(path))
    return conn


def _now():
    """ISO-8601 UTC timestamp."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _session_id():
    """Generate unique research session ID with rsess- prefix."""
    return f"rsess-{uuid.uuid4().hex[:12]}"


def _audit(event_type, action, details=None):
    """Write audit trail entry."""
    if _HAS_AUDIT:
        try:
            audit_log_event(
                event_type=event_type,
                actor="research-engine",
                action=action,
                details=json.dumps(details) if details else None,
                project_id="research-engine",
            )
        except Exception:
            pass


def _load_config():
    """Load research engine config from YAML."""
    if not _HAS_YAML or not CONFIG_PATH.exists():
        return {}
    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


# =========================================================================
# SESSION LIFECYCLE FUNCTIONS
# =========================================================================
def create_session(name, vertical_slug, description=None, focus_areas=None, db_path=None):
    """Create a new research session.

    Validates that the vertical exists in DB before creating the session.

    Args:
        name: Human-readable session name.
        vertical_slug: Slug of the target vertical (must exist in research_verticals).
        description: Optional session description.
        focus_areas: Optional list of focus area strings.
        db_path: Optional DB path override.

    Returns:
        Dict with session data or error.
    """
    conn = _get_db(db_path)
    try:
        # Validate vertical exists
        vert = conn.execute(
            "SELECT id, name FROM research_verticals WHERE slug = %s",
            (vertical_slug,),
        ).fetchone()
        if not vert:
            return {"error": f"Vertical not found: {vertical_slug}"}

        sid = _session_id()
        now = _now()
        focus = json.dumps(focus_areas) if focus_areas else "[]"

        conn.execute(
            """INSERT INTO research_sessions
               (id, name, vertical_id, vertical_name, description, focus_areas,
                status, pipeline_stage, signal_count, challenge_count,
                created_at, updated_at, classification)
               VALUES (%s, %s, %s, %s, %s, %s, 'created', 'SCOPE', 0, 0, %s, %s, 'CUI')""",
            (
                sid,
                name,
                vert["id"],
                vert["name"],
                description or "",
                focus,
                now,
                now,
            ),
        )
        conn.commit()

        # Fetch the created session
        row = conn.execute("SELECT * FROM research_sessions WHERE id = %s", (sid,)).fetchone()
        result = dict(row)

        _audit(
            "research.session.created",
            f"Created session '{name}' for vertical '{vertical_slug}'",
            {"session_id": sid, "vertical_slug": vertical_slug},
        )
        return result
    finally:
        conn.close()


def get_session(session_id, db_path=None):
    """Get a research session by ID.

    Args:
        session_id: The session ID (rsess-...).
        db_path: Optional DB path override.

    Returns:
        Dict with session data or error.
    """
    conn = _get_db(db_path)
    try:
        row = conn.execute("SELECT * FROM research_sessions WHERE id = %s", (session_id,)).fetchone()
        if not row:
            return {"error": f"Session not found: {session_id}"}
        return dict(row)
    finally:
        conn.close()


def list_sessions(status=None, vertical=None, db_path=None):
    """List research sessions with optional filters.

    Args:
        status: Optional status filter.
        vertical: Optional vertical slug filter.
        db_path: Optional DB path override.

    Returns:
        Dict with sessions list and total count.
    """
    conn = _get_db(db_path)
    try:
        query = "SELECT s.* FROM research_sessions s"
        params = []
        conditions = []

        if status:
            if status not in VALID_STATUSES:
                return {"error": f"Invalid status: {status}. Valid: {', '.join(VALID_STATUSES)}"}
            conditions.append("s.status = ?")
            params.append(status)

        if vertical:
            # Join to verticals to filter by slug
            query = """SELECT s.* FROM research_sessions s
                       JOIN research_verticals v ON s.vertical_id = v.id"""
            conditions.append("v.slug = ?")
            params.append(vertical)

        if conditions:
            query += " WHERE " + " AND ".join(conditions)

        query += " ORDER BY s.updated_at DESC"

        rows = conn.execute(query, params).fetchall()
        return {
            "sessions": [dict(r) for r in rows],
            "total": len(rows),
        }
    finally:
        conn.close()


def advance_stage(session_id, db_path=None):
    """Advance a session to the next pipeline stage.

    Uses PIPELINE_STAGES.index() to find the current stage and advance to the
    next one. Updates both pipeline_stage and status according to STAGE_TO_STATUS
    mapping.

    Args:
        session_id: The session ID to advance.
        db_path: Optional DB path override.

    Returns:
        Dict with updated session data or error.
    """
    conn = _get_db(db_path)
    try:
        row = conn.execute("SELECT * FROM research_sessions WHERE id = %s", (session_id,)).fetchone()
        if not row:
            return {"error": f"Session not found: {session_id}"}

        current_stage = row["pipeline_stage"]

        # Check if already at final stage
        if current_stage == "DOSSIER":
            return {"error": "Session already at DOSSIER stage — cannot advance further"}

        # Find current index and advance
        try:
            current_idx = PIPELINE_STAGES.index(current_stage)
        except ValueError:
            return {"error": f"Unknown pipeline stage: {current_stage}"}

        next_stage = PIPELINE_STAGES[current_idx + 1]
        next_status = STAGE_TO_STATUS[next_stage]
        now = _now()

        conn.execute(
            """UPDATE research_sessions
               SET pipeline_stage = %s, status = %s, updated_at = %s
               WHERE id = %s""",
            (next_stage, next_status, now, session_id),
        )
        conn.commit()

        # Fetch updated session
        updated = conn.execute("SELECT * FROM research_sessions WHERE id = %s", (session_id,)).fetchone()
        result = dict(updated)

        _audit(
            "research.session.stage_advanced",
            f"Advanced session {session_id} from {current_stage} to {next_stage}",
            {
                "session_id": session_id,
                "from_stage": current_stage,
                "to_stage": next_stage,
                "new_status": next_status,
            },
        )
        return result
    finally:
        conn.close()


def update_session_status(session_id, status, db_path=None):
    """Directly update session status.

    Used for status transitions that are not stage-driven:
    reviewed, child_app_triggered, archived.

    Args:
        session_id: The session ID to update.
        status: The new status value.
        db_path: Optional DB path override.

    Returns:
        Dict with updated session data or error.
    """
    if status not in VALID_STATUSES:
        return {"error": f"Invalid status: {status}. Valid: {', '.join(VALID_STATUSES)}"}

    conn = _get_db(db_path)
    try:
        row = conn.execute("SELECT * FROM research_sessions WHERE id = %s", (session_id,)).fetchone()
        if not row:
            return {"error": f"Session not found: {session_id}"}

        old_status = row["status"]
        now = _now()

        conn.execute(
            """UPDATE research_sessions
               SET status = %s, updated_at = %s
               WHERE id = %s""",
            (status, now, session_id),
        )
        conn.commit()

        updated = conn.execute("SELECT * FROM research_sessions WHERE id = %s", (session_id,)).fetchone()
        result = dict(updated)

        _audit(
            "research.session.status_updated",
            f"Updated session {session_id} status from {old_status} to {status}",
            {
                "session_id": session_id,
                "from_status": old_status,
                "to_status": status,
            },
        )
        return result
    finally:
        conn.close()


def update_session_counts(session_id, db_path=None):
    """Recount signals and challenges from DB and update session.

    Queries research_signals and research_challenges tables for the given
    session and updates the cached counts.

    Args:
        session_id: The session ID to update counts for.
        db_path: Optional DB path override.

    Returns:
        Dict with updated session data or error.
    """
    conn = _get_db(db_path)
    try:
        row = conn.execute("SELECT * FROM research_sessions WHERE id = %s", (session_id,)).fetchone()
        if not row:
            return {"error": f"Session not found: {session_id}"}

        signal_count = conn.execute(
            "SELECT COUNT(*) as cnt FROM research_signals WHERE session_id = %s",
            (session_id,),
        ).fetchone()["cnt"]

        challenge_count = conn.execute(
            "SELECT COUNT(*) as cnt FROM research_challenges WHERE session_id = %s",
            (session_id,),
        ).fetchone()["cnt"]

        now = _now()
        conn.execute(
            """UPDATE research_sessions
               SET signal_count = %s, challenge_count = %s, updated_at = %s
               WHERE id = %s""",
            (signal_count, challenge_count, now, session_id),
        )
        conn.commit()

        updated = conn.execute("SELECT * FROM research_sessions WHERE id = %s", (session_id,)).fetchone()
        result = dict(updated)

        _audit(
            "research.session.counts_updated",
            f"Updated counts for session {session_id}: {signal_count} signals, {challenge_count} challenges",
            {
                "session_id": session_id,
                "signal_count": signal_count,
                "challenge_count": challenge_count,
            },
        )
        return result
    finally:
        conn.close()


def get_session_status(session_id, db_path=None):
    """Get detailed session status including signal and challenge counts.

    Args:
        session_id: The session ID to check.
        db_path: Optional DB path override.

    Returns:
        Dict with detailed status info or error.
    """
    conn = _get_db(db_path)
    try:
        row = conn.execute("SELECT * FROM research_sessions WHERE id = %s", (session_id,)).fetchone()
        if not row:
            return {"error": f"Session not found: {session_id}"}

        session = dict(row)

        # Live counts from child tables
        signal_count = conn.execute(
            "SELECT COUNT(*) as cnt FROM research_signals WHERE session_id = %s",
            (session_id,),
        ).fetchone()["cnt"]

        challenge_count = conn.execute(
            "SELECT COUNT(*) as cnt FROM research_challenges WHERE session_id = %s",
            (session_id,),
        ).fetchone()["cnt"]

        # Current stage position
        try:
            stage_idx = PIPELINE_STAGES.index(session["pipeline_stage"])
        except ValueError:
            stage_idx = -1

        return {
            "session_id": session["id"],
            "name": session["name"],
            "vertical_id": session["vertical_id"],
            "vertical_name": session["vertical_name"],
            "status": session["status"],
            "pipeline_stage": session["pipeline_stage"],
            "stage_index": stage_idx,
            "total_stages": len(PIPELINE_STAGES),
            "stages_remaining": len(PIPELINE_STAGES) - stage_idx - 1 if stage_idx >= 0 else -1,
            "signal_count": signal_count,
            "challenge_count": challenge_count,
            "cached_signal_count": session["signal_count"],
            "cached_challenge_count": session["challenge_count"],
            "dossier_id": session.get("dossier_id"),
            "focus_areas": session.get("focus_areas", "[]"),
            "created_at": session["created_at"],
            "updated_at": session["updated_at"],
            "checked_at": _now(),
        }
    finally:
        conn.close()


# =========================================================================
# HUMAN OUTPUT
# =========================================================================
def _print_human(args, result):
    """Format output for human-readable terminal display."""
    print("=" * 70)
    print("  ICDEV™ Research Engine — Session Manager — CUI // SP-CTI")
    print("=" * 70)

    if isinstance(result, dict) and "error" in result:
        print(f"\n  ERROR: {result['error']}\n")
        print("=" * 70)
        return

    if args.create:
        print("\n  Session Created")
        print("-" * 70)
        print(f"  ID:       {result.get('id', '')}")
        print(f"  Name:     {result.get('name', '')}")
        print(f"  Vertical: {result.get('vertical_name', '')}")
        print(f"  Status:   {result.get('status', '')}")
        print(f"  Stage:    {result.get('pipeline_stage', '')}")

    elif args.get:
        print("\n  Session Detail")
        print("-" * 70)
        print(f"  ID:         {result.get('id', '')}")
        print(f"  Name:       {result.get('name', '')}")
        print(f"  Vertical:   {result.get('vertical_name', '')}")
        print(f"  Status:     {result.get('status', '')}")
        print(f"  Stage:      {result.get('pipeline_stage', '')}")
        print(f"  Signals:    {result.get('signal_count', 0)}")
        print(f"  Challenges: {result.get('challenge_count', 0)}")
        print(f"  Created:    {result.get('created_at', '')}")
        print(f"  Updated:    {result.get('updated_at', '')}")

    elif args.list:
        sessions = result.get("sessions", [])
        print(f"\n  Total Sessions: {result.get('total', 0)}\n")
        print(f"    {'ID':20s} {'Name':25s} {'Status':15s} {'Stage':12s} {'Signals':8s}")
        print(f"    {'-' * 20} {'-' * 25} {'-' * 15} {'-' * 12} {'-' * 8}")
        for s in sessions:
            print(
                f"    {s['id']:20s} {s['name'][:25]:25s} "
                f"{s['status']:15s} {s['pipeline_stage']:12s} "
                f"{s.get('signal_count', 0):<8d}"
            )

    elif args.advance:
        print("\n  Stage Advanced")
        print("-" * 70)
        print(f"  ID:        {result.get('id', '')}")
        print(f"  Name:      {result.get('name', '')}")
        print(f"  Status:    {result.get('status', '')}")
        print(f"  Stage:     {result.get('pipeline_stage', '')}")

    elif args.status:
        print("\n  Session Status")
        print("-" * 70)
        print(f"  ID:               {result.get('session_id', '')}")
        print(f"  Name:             {result.get('name', '')}")
        print(f"  Vertical:         {result.get('vertical_name', '')}")
        print(f"  Status:           {result.get('status', '')}")
        print(f"  Pipeline Stage:   {result.get('pipeline_stage', '')}")
        print(f"  Stage Progress:   {result.get('stage_index', 0) + 1}/{result.get('total_stages', 0)}")
        print(f"  Stages Remaining: {result.get('stages_remaining', 0)}")
        print(f"  Signals (live):   {result.get('signal_count', 0)}")
        print(f"  Challenges (live):{result.get('challenge_count', 0)}")
        if result.get("dossier_id"):
            print(f"  Dossier ID:       {result['dossier_id']}")
        print(f"  Created:          {result.get('created_at', '')}")
        print(f"  Updated:          {result.get('updated_at', '')}")
        print(f"  Checked:          {result.get('checked_at', '')}")

    print()
    print("=" * 70)


# =========================================================================
# CLI
# =========================================================================
def main():
    """CLI entry point."""
    parser = argparse.ArgumentParser(
        description="Research Engine Session Manager — CUI // SP-CTI",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="Examples:\n"
        "  %(prog)s --create --name 'HealthIT Research' --vertical healthcare --json\n"
        "  %(prog)s --get --session-id rsess-abc123 --json\n"
        "  %(prog)s --list --status scanning --json\n"
        "  %(prog)s --advance --session-id rsess-abc123 --json\n"
        "  %(prog)s --status --session-id rsess-abc123 --json\n",
    )

    # Actions
    actions = parser.add_mutually_exclusive_group(required=True)
    actions.add_argument("--create", action="store_true", help="Create a new research session")
    actions.add_argument("--get", action="store_true", help="Get a session by ID")
    actions.add_argument("--list", action="store_true", help="List sessions with optional filters")
    actions.add_argument("--advance", action="store_true", help="Advance session to next pipeline stage")
    actions.add_argument("--status", action="store_true", help="Get detailed session status")

    # Parameters
    parser.add_argument("--name", type=str, default=None, help="Session name (required for --create)")
    parser.add_argument(
        "--vertical", type=str, default=None, help="Vertical slug (required for --create, optional filter for --list)"
    )
    parser.add_argument(
        "--session-id", type=str, default=None, help="Session ID (required for --get, --advance, --status)"
    )
    parser.add_argument("--description", type=str, default=None, help="Session description (optional for --create)")
    parser.add_argument(
        "--focus-areas", type=str, default=None, help="Comma-separated focus areas (optional for --create)"
    )

    # Filter for --list
    list_status_help = f"Filter by status for --list. Valid: {', '.join(VALID_STATUSES)}"
    parser.add_argument("--filter-status", type=str, default=None, help=list_status_help)

    # Output
    parser.add_argument("--json", action="store_true", help="JSON output")
    parser.add_argument("--human", action="store_true", help="Human-readable output")
    parser.add_argument("--db-path", type=str, default=None, help="Override database path")

    args = parser.parse_args()
    db_path = Path(args.db_path) if args.db_path else None

    try:
        if args.create:
            if not args.name:
                result = {"error": "--name is required for --create"}
            elif not args.vertical:
                result = {"error": "--vertical is required for --create"}
            else:
                focus = [a.strip() for a in args.focus_areas.split(",")] if args.focus_areas else None
                result = create_session(
                    name=args.name,
                    vertical_slug=args.vertical,
                    description=args.description,
                    focus_areas=focus,
                    db_path=db_path,
                )
        elif args.get:
            if not args.session_id:
                result = {"error": "--session-id is required for --get"}
            else:
                result = get_session(args.session_id, db_path=db_path)
        elif args.list:
            result = list_sessions(
                status=args.filter_status,
                vertical=args.vertical,
                db_path=db_path,
            )
        elif args.advance:
            if not args.session_id:
                result = {"error": "--session-id is required for --advance"}
            else:
                result = advance_stage(args.session_id, db_path=db_path)
        elif args.status:
            if not args.session_id:
                result = {"error": "--session-id is required for --status"}
            else:
                result = get_session_status(args.session_id, db_path=db_path)
        else:
            result = {"error": "No action specified"}

    except FileNotFoundError as exc:
        result = {"error": str(exc), "hint": "Run: python tools/db/init_icdev_db.py"}
    except Exception as exc:
        result = {"error": f"Unexpected error: {exc}"}

    # Output
    if args.human:
        _print_human(args, result)
    else:
        print(json.dumps(result, indent=2, default=str))


if __name__ == "__main__":
    main()
