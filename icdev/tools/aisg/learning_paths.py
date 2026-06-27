# CUI // SP-CTI
"""AI Learning Paths — 3-track curriculum for non-AI teams.

DB-backed: reads track catalog from aisg_learning_tracks and progress
from aisg_track_progress.  Falls back to _TRACKS if the tables do not
exist yet (e.g. migration 085 not yet applied).
"""
from __future__ import annotations

# Display-only metadata not stored in the DB schema (icons, badges, modules).
_TRACK_META: dict[str, dict] = {
    "aisg-track-consumer": {
        "icon": "🌱",
        "badge": "AI Consumer Badge",
        "badge_color": "success",
        "duration_hours": 6,
        "modules": [
            "Introduction to AI in Government",
            "Using the AI Launchpad Wizard",
            "Deploying AI Patterns",
            "Reading AI Recommendations",
            "Compliance & Classification Basics",
            "Reviewing AI-Generated Reports",
            "Working with the Knowledge Handoff",
            "Capstone: Deploy a Pattern to Production",
        ],
    },
    "explorer": {
        "icon": "🔍",
        "badge": "AI Explorer Badge",
        "badge_color": "success",
        "duration_hours": 4,
        "modules": [
            "What is AI?",
            "Run Your First Workflow",
            "Deploy a Document Q&A Pattern",
            "Explore the Dashboard",
        ],
    },
    "aisg-track-configurator": {
        "icon": "⚡",
        "badge": "AI Configurator Badge",
        "badge_color": "info",
        "duration_hours": 14,
        "modules": [
            "FORGE Framework Deep Dive",
            "Configuring LLM Routing",
            "Compliance Control Customization",
            "Security Gate Tuning",
            "Kanban Workflow Configuration",
            "DB Migration Patterns",
            "Multi-Cloud Deployment Args",
            "SBOM & Dependency Management",
            "Fine-Tuning Evaluation",
            "Air-Gap Mode Setup",
            "Custom Pattern Creation",
            "Capstone: Configure a Full IL4 Stack",
        ],
    },
    "practitioner": {
        "icon": "🛠️",
        "badge": "AI Practitioner Badge",
        "badge_color": "info",
        "duration_hours": 10,
        "modules": [
            "Wire Real Data Sources",
            "Label Training Examples",
            "Integrate AI into Live Workflows",
            "Monitor & Tune Performance",
        ],
    },
    "aisg-track-builder": {
        "icon": "🚀",
        "badge": "AI Builder Badge",
        "badge_color": "warning",
        "duration_hours": 24,
        "modules": [
            "ANVIL Workflow Mastery",
            "Writing FORGE Goals",
            "Building Deterministic Tools",
            "Agent Architecture Design",
            "Canvas Development (7-component gate)",
            "Visual Agent Builder API",
            "Custom Compliance Crosswalk",
            "MCP Gateway Extension",
            "Multi-Agent Orchestration",
            "Self-Healing Reflex Design",
            "AI Security & Sandboxing",
            "Supply Chain Integration",
            "Performance Optimization",
            "Publishing to Marketplace",
            "Air-Gap Deployment Strategy",
            "Capstone: Ship a Production Canvas",
        ],
    },
    "architect": {
        "icon": "🏗️",
        "badge": "AI Architect Badge",
        "badge_color": "warning",
        "duration_hours": 20,
        "modules": [
            "Automate Fine-Tuning Pipelines",
            "Build Reusable FORGE Goals",
            "Mentor Teams Through Tracks",
            "Scale Multi-Agent Systems",
        ],
    },
}

# Legacy in-memory fallback used when migration 085 has not been applied.
_TRACKS = [
    {
        "id": "consumer",
        "name": "AI Consumer",
        "level": "Beginner",
        **_TRACK_META["aisg-track-consumer"],
        "description": (
            "Learn to use ICDEV™ AI tools confidently without writing code. "
            "Ideal for program managers, contracting officers, and leadership."
        ),
        "task_count": 8,
        "progress": 0,
    },
    {
        "id": "configurator",
        "name": "AI Configurator",
        "level": "Intermediate",
        **_TRACK_META["aisg-track-configurator"],
        "description": (
            "Configure and customize ICDEV™ agents, args, and compliance rules. "
            "Ideal for system administrators, DevSecOps engineers, and analysts."
        ),
        "task_count": 12,
        "progress": 0,
    },
    {
        "id": "builder",
        "name": "AI Builder",
        "level": "Advanced",
        **_TRACK_META["aisg-track-builder"],
        "description": (
            "Build new ICDEV™ tools, goals, agents, and canvases from scratch. "
            "Ideal for software engineers and architects extending the platform."
        ),
        "task_count": 16,
        "progress": 0,
    },
]

# Maps DB level values → display-friendly level labels.
_LEVEL_LABELS = {
    "consumer": "Beginner",
    "configurator": "Intermediate",
    "builder": "Advanced",
}


def _get_db_tracks() -> list[dict]:
    """Fetch all rows from aisg_learning_tracks. Returns [] on any error."""
    try:
        from tools.db.storage import get_connection
        conn = get_connection()
        rows = conn.execute(
            "SELECT id, name, level, description, task_count FROM aisg_learning_tracks"
            " ORDER BY task_count ASC"
        ).fetchall()
        return [dict(zip(["id", "name", "level", "description", "task_count"], r)) for r in rows]
    except Exception:
        return []


def _get_user_progress(user_email: str, track_id: str) -> int:
    """Return tasks_completed for (user_email, track_id), or 0 if no row."""
    try:
        from tools.db.storage import get_connection
        conn = get_connection()
        row = conn.execute(
            "SELECT tasks_completed FROM aisg_track_progress"
            " WHERE user_email = %s AND track_id = %s",
            (user_email, track_id),
        ).fetchone()
        return int(row[0]) if row else 0
    except Exception:
        return 0


def activate_track(user_email: str, track_id: str) -> dict:
    """Insert or update a progress row for the given user + track."""
    from tools.db.storage import get_connection
    conn = get_connection()
    pg = getattr(conn, "_backend", "sqlite") == "postgresql"
    if pg:
        conn.execute(
            "INSERT INTO aisg_track_progress (user_email, track_id, activated_at)"
            " VALUES (%s, %s, NOW())"
            " ON CONFLICT (user_email, track_id) DO NOTHING",
            (user_email, track_id),
        )
    else:
        conn.execute(
            "INSERT OR IGNORE INTO aisg_track_progress"
            " (user_email, track_id, activated_at) VALUES (%s, %s, datetime('now'))",
            (user_email, track_id),
        )
    conn.commit()
    return {"status": "ok", "user_email": user_email, "track_id": track_id}


def update_progress(user_email: str, track_id: str, tasks_completed: int) -> dict:
    """Increment progress on an existing aisg_track_progress row."""
    from tools.db.storage import get_connection
    conn = get_connection()
    conn.execute(
        "UPDATE aisg_track_progress SET tasks_completed = %s"
        " WHERE user_email = %s AND track_id = %s",
        (tasks_completed, user_email, track_id),
    )
    conn.commit()
    return {"status": "ok", "tasks_completed": tasks_completed}


_ID_TO_META = {
    "consumer": "aisg-track-consumer",
    "configurator": "aisg-track-configurator",
    "builder": "aisg-track-builder",
}


def get_tracks(user_email: str | None = None) -> list[dict]:
    """Return learning tracks with optional per-user progress.

    Reads the authoritative catalog from aisg_learning_tracks; if the table
    is unavailable (migration not yet applied) falls back to _TRACKS.
    """
    db_rows = _get_db_tracks()
    if not db_rows:
        return list(_TRACKS)

    tracks = []
    for row in db_rows:
        meta = _TRACK_META.get(_ID_TO_META.get(row["id"], row["id"]), {})
        progress = _get_user_progress(user_email, row["id"]) if user_email else 0
        tracks.append(
            {
                "id": row["id"],
                "name": row["name"],
                "level": _LEVEL_LABELS.get(row["level"], row["level"].capitalize()),
                "description": row["description"] or "",
                "task_count": row["task_count"],
                "progress": progress,
                **meta,
            }
        )
    return tracks
