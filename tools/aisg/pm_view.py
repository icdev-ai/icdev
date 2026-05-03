# CUI // SP-CTI
"""PM view data — sprint tasks, velocity, test coverage, learning milestones."""
from __future__ import annotations

from tools.db.storage import get_connection


def get_pm_data() -> dict:
    """Return sprint tasks, velocity metrics, and learning milestones."""
    sprint_tasks: list[dict] = []

    try:
        conn = get_connection()
        try:
            c = conn.cursor()
            c.execute(
                "SELECT id, title, status, priority, task_type, created_at "
                "FROM kanban_tasks "
                "WHERE dispatch_source = 'aisg_wizard' "
                "ORDER BY created_at DESC LIMIT 20"
            )
            sprint_tasks = [dict(r) for r in c.fetchall()]
        finally:
            conn.close()
    except Exception:
        pass

    # Fallback demo sprint tasks when DB table has no aisg tasks yet
    if not sprint_tasks:
        sprint_tasks = [
            {"id": "aisg-sp-01", "title": "Initialize ICDEV™ project structure", "status": "done", "priority": "high", "task_type": "chore"},
            {"id": "aisg-sp-02", "title": "Run AI-driven requirements intake", "status": "done", "priority": "high", "task_type": "research"},
            {"id": "aisg-sp-03", "title": "Configure IL4 compliance controls", "status": "in_progress", "priority": "high", "task_type": "compliance"},
            {"id": "aisg-sp-04", "title": "Define system boundary and ATO assessment", "status": "in_progress", "priority": "high", "task_type": "compliance"},
            {"id": "aisg-sp-05", "title": "Build core web_app application (python)", "status": "todo", "priority": "high", "task_type": "build"},
            {"id": "aisg-sp-06", "title": "Implement TDD/BDD test suite", "status": "todo", "priority": "high", "task_type": "test"},
            {"id": "aisg-sp-07", "title": "Run SAST / dependency security scan", "status": "todo", "priority": "high", "task_type": "security"},
            {"id": "aisg-sp-08", "title": "Code review gate (security + quality)", "status": "todo", "priority": "medium", "task_type": "review"},
        ]

    done = sum(1 for t in sprint_tasks if t.get("status") in ("done", "completed"))
    in_progress = sum(1 for t in sprint_tasks if t.get("status") == "in_progress")
    todo = sum(1 for t in sprint_tasks if t.get("status") in ("todo", "pending", "scheduled"))

    velocity_data = [
        {"sprint": "S1", "planned": 8, "completed": 7},
        {"sprint": "S2", "planned": 10, "completed": 9},
        {"sprint": "S3", "planned": 12, "completed": 11},
        {"sprint": "S4", "planned": 10, "completed": 8},
        {"sprint": "S5", "planned": 11, "completed": 10},
        {"sprint": "S6", "planned": 13, "completed": 12},
    ]

    learning_milestones = [
        {"user": "Alice M.", "track": "AI Builder", "milestone": "Completed Visual Agent Builder module", "date": "2026-05-02"},
        {"user": "Bob K.", "track": "AI Consumer", "milestone": "Deployed first pattern (Document Q&A)", "date": "2026-05-01"},
        {"user": "Carol T.", "track": "AI Configurator", "milestone": "Configured IL4 compliance controls", "date": "2026-04-30"},
    ]

    return {
        "sprint_tasks": sprint_tasks,
        "sprint_name": "Sprint 6 — AISG Phase A",
        "sprint_start": "2026-04-28",
        "sprint_end": "2026-05-09",
        "done": done,
        "in_progress": in_progress,
        "todo": todo,
        "total": len(sprint_tasks),
        "velocity_data": velocity_data,
        "test_coverage": 84,
        "build_pass_rate": 96,
        "learning_milestones": learning_milestones,
    }
