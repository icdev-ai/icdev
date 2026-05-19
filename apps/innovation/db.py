# CUI // SP-CTI
"""FORGE IGNITE DB layer — all innov_* tables via get_connection()."""

import json
from datetime import datetime, timezone, timedelta

from tools.db.storage import get_connection

# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------

_DDL = """
CREATE TABLE IF NOT EXISTS innov_ideas (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    title TEXT NOT NULL,
    submitter_name TEXT,
    submitter_email TEXT,
    submitter_role TEXT DEFAULT 'unknown',
    department TEXT,
    status TEXT NOT NULL DEFAULT 'spark',
    problem_statement TEXT,
    hypothesis TEXT,
    recommended_approach TEXT,
    risk_flags_json TEXT NOT NULL DEFAULT '[]',
    learning_path_json TEXT NOT NULL DEFAULT '[]',
    phase_tag TEXT NOT NULL DEFAULT 'deferred',
    score_total REAL NOT NULL DEFAULT 0,
    score_breakdown_json TEXT NOT NULL DEFAULT '{}',
    intake_answers_json TEXT NOT NULL DEFAULT '{}',
    opportunity_brief TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS innov_assessments (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    idea_id INTEGER NOT NULL REFERENCES innov_ideas(id),
    assessed_by TEXT,
    business_value_score INTEGER NOT NULL DEFAULT 5,
    strategic_alignment_score INTEGER NOT NULL DEFAULT 5,
    technical_feasibility_score INTEGER NOT NULL DEFAULT 5,
    data_readiness_score INTEGER NOT NULL DEFAULT 5,
    time_to_value_score INTEGER NOT NULL DEFAULT 5,
    risk_score INTEGER NOT NULL DEFAULT 5,
    feasibility_study_text TEXT,
    reviewer_notes TEXT,
    human_approved INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS innov_pilots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    idea_id INTEGER NOT NULL REFERENCES innov_ideas(id),
    pilot_name TEXT NOT NULL,
    hypothesis TEXT,
    success_criteria TEXT,
    kanban_epic_id TEXT,
    start_date TEXT,
    end_date TEXT,
    status TEXT NOT NULL DEFAULT 'planned',
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS innov_metrics (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    pilot_id INTEGER NOT NULL REFERENCES innov_pilots(id),
    metric_name TEXT NOT NULL,
    baseline_value REAL,
    measured_value REAL,
    unit TEXT,
    improvement_pct REAL,
    recorded_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS innov_lessons (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    idea_id INTEGER NOT NULL REFERENCES innov_ideas(id),
    outcome TEXT NOT NULL DEFAULT 'unknown',
    what_worked TEXT,
    what_didnt TEXT,
    recommendation TEXT,
    promoted_to_pattern INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS innov_comments (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    idea_id INTEGER NOT NULL REFERENCES innov_ideas(id),
    author_name TEXT,
    body TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
"""

VALID_STATUSES = {"spark", "assess", "score", "pilot", "measure", "scale", "archive"}


def migrate():
    with get_connection() as conn:
        for stmt in _DDL.strip().split(";"):
            stmt = stmt.strip()
            if stmt:
                conn.execute(stmt)
        conn.commit()


# ---------------------------------------------------------------------------
# Ideas CRUD
# ---------------------------------------------------------------------------

def _row(row) -> dict:
    if row is None:
        return {}
    d = dict(row)
    for key in ("risk_flags_json", "learning_path_json", "score_breakdown_json", "intake_answers_json"):
        if key in d and isinstance(d[key], str):
            try:
                d[key.replace("_json", "")] = json.loads(d[key])
            except Exception:
                d[key.replace("_json", "")] = [] if "flags" in key or "path" in key else {}
    return d


def create_idea(title: str, submitter_name: str = "", submitter_email: str = "",
                submitter_role: str = "unknown", department: str = "") -> dict:
    now = datetime.now(timezone.utc).isoformat()
    with get_connection() as conn:
        cur = conn.execute(
            """INSERT INTO innov_ideas
               (title, submitter_name, submitter_email, submitter_role, department,
                status, created_at, updated_at)
               VALUES (?,?,?,?,?,'spark',?,?)""",
            (title, submitter_name, submitter_email, submitter_role, department, now, now),
        )
        conn.commit()
        return get_idea(cur.lastrowid)


def get_idea(idea_id: int) -> dict:
    with get_connection() as conn:
        row = conn.execute("SELECT * FROM innov_ideas WHERE id=?", (idea_id,)).fetchone()
        return _row(row)


def list_ideas(status: str = None, limit: int = 200) -> list:
    with get_connection() as conn:
        if status:
            rows = conn.execute(
                "SELECT * FROM innov_ideas WHERE status=? ORDER BY updated_at DESC LIMIT ?",
                (status, limit),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM innov_ideas ORDER BY updated_at DESC LIMIT ?", (limit,)
            ).fetchall()
        return [_row(r) for r in rows]


def update_idea(idea_id: int, **fields) -> dict:
    allowed = {
        "title", "submitter_name", "submitter_email", "submitter_role", "department",
        "status", "problem_statement", "hypothesis", "recommended_approach",
        "risk_flags_json", "learning_path_json", "phase_tag",
        "score_total", "score_breakdown_json", "intake_answers_json", "opportunity_brief",
    }
    updates = {k: v for k, v in fields.items() if k in allowed}
    if not updates:
        return get_idea(idea_id)
    updates["updated_at"] = datetime.now(timezone.utc).isoformat()
    set_clause = ", ".join(f"{k}=?" for k in updates)
    values = list(updates.values()) + [idea_id]
    with get_connection() as conn:
        conn.execute(f"UPDATE innov_ideas SET {set_clause} WHERE id=?", values)
        conn.commit()
    return get_idea(idea_id)


def save_intake_answers(idea_id: int, answers: dict) -> dict:
    """Persist all intake answers and extract structured fields."""
    problem_statement = "\n".join(filter(None, [
        answers.get("q1_bottleneck", ""),
        answers.get("q3_impact", ""),
    ]))
    return update_idea(
        idea_id,
        intake_answers_json=json.dumps(answers),
        problem_statement=problem_statement,
        status="assess",
    )


# ---------------------------------------------------------------------------
# Assessments
# ---------------------------------------------------------------------------

def create_assessment(idea_id: int, scores: dict, study_text: str = "",
                      assessed_by: str = "") -> dict:
    with get_connection() as conn:
        cur = conn.execute(
            """INSERT INTO innov_assessments
               (idea_id, assessed_by, business_value_score, strategic_alignment_score,
                technical_feasibility_score, data_readiness_score, time_to_value_score,
                risk_score, feasibility_study_text)
               VALUES (?,?,?,?,?,?,?,?,?)""",
            (
                idea_id, assessed_by,
                scores.get("business_value", 5),
                scores.get("strategic_alignment", 5),
                scores.get("technical_feasibility", 5),
                scores.get("data_readiness", 5),
                scores.get("time_to_value", 5),
                scores.get("risk", 5),
                study_text,
            ),
        )
        conn.commit()
        return get_assessment(cur.lastrowid)


def get_assessment(assessment_id: int) -> dict:
    with get_connection() as conn:
        row = conn.execute(
            "SELECT * FROM innov_assessments WHERE id=?", (assessment_id,)
        ).fetchone()
        return dict(row) if row else {}


def get_latest_assessment(idea_id: int) -> dict:
    with get_connection() as conn:
        row = conn.execute(
            "SELECT * FROM innov_assessments WHERE idea_id=? ORDER BY created_at DESC LIMIT 1",
            (idea_id,),
        ).fetchone()
        return dict(row) if row else {}


def approve_assessment(assessment_id: int, reviewer_notes: str = "") -> None:
    with get_connection() as conn:
        conn.execute(
            "UPDATE innov_assessments SET human_approved=1, reviewer_notes=? WHERE id=?",
            (reviewer_notes, assessment_id),
        )
        conn.commit()


# ---------------------------------------------------------------------------
# Pilots
# ---------------------------------------------------------------------------

def create_pilot(idea_id: int, pilot_name: str, hypothesis: str = "",
                 success_criteria: str = "") -> dict:
    with get_connection() as conn:
        cur = conn.execute(
            """INSERT INTO innov_pilots (idea_id, pilot_name, hypothesis, success_criteria)
               VALUES (?,?,?,?)""",
            (idea_id, pilot_name, hypothesis, success_criteria),
        )
        conn.commit()
        row = conn.execute(
            "SELECT * FROM innov_pilots WHERE id=?", (cur.lastrowid,)
        ).fetchone()
        return dict(row) if row else {}


def list_pilots(idea_id: int) -> list:
    with get_connection() as conn:
        rows = conn.execute(
            "SELECT * FROM innov_pilots WHERE idea_id=? ORDER BY created_at DESC",
            (idea_id,),
        ).fetchall()
        return [dict(r) for r in rows]


# ---------------------------------------------------------------------------
# Comments
# ---------------------------------------------------------------------------

def add_comment(idea_id: int, author_name: str, body: str) -> dict:
    with get_connection() as conn:
        cur = conn.execute(
            "INSERT INTO innov_comments (idea_id, author_name, body) VALUES (?,?,?)",
            (idea_id, author_name, body),
        )
        conn.commit()
        row = conn.execute(
            "SELECT * FROM innov_comments WHERE id=?", (cur.lastrowid,)
        ).fetchone()
        return dict(row) if row else {}


def list_comments(idea_id: int) -> list:
    with get_connection() as conn:
        rows = conn.execute(
            "SELECT * FROM innov_comments WHERE idea_id=? ORDER BY created_at ASC",
            (idea_id,),
        ).fetchall()
        return [dict(r) for r in rows]


# ---------------------------------------------------------------------------
# Pipeline statistics (for dashboard)
# ---------------------------------------------------------------------------

def pipeline_stats() -> dict:
    with get_connection() as conn:
        stages = ["spark", "assess", "score", "pilot", "measure", "scale", "archive"]
        counts = {}
        for s in stages:
            row = conn.execute(
                "SELECT COUNT(*) FROM innov_ideas WHERE status=?", (s,)
            ).fetchone()
            counts[s] = row[0] if row else 0
        total = sum(counts.values())

        # Monthly velocity
        thirty_days_ago = (datetime.now(timezone.utc) - timedelta(days=30)).isoformat()
        row = conn.execute(
            "SELECT COUNT(*) FROM innov_ideas WHERE created_at >= ?",
            (thirty_days_ago,),
        ).fetchone()
        submitted_this_month = row[0] if row else 0

        row = conn.execute(
            """SELECT COUNT(*) FROM innov_ideas
               WHERE status IN ('pilot','measure','scale')
               AND updated_at >= ?""",
            (thirty_days_ago,),
        ).fetchone()
        pilots_this_month = row[0] if row else 0

        # Phase distribution
        phase_counts = {}
        for phase in ("phase1_quick_win", "phase2_augmentation", "phase3_transformation", "deferred"):
            tag = phase.split("_")[0] + ("_" + phase.split("_")[1] if "_" in phase[6:] else "")
            # Map to stored values
            stored_tag = {
                "phase1_quick_win": "phase1",
                "phase2_augmentation": "phase2",
                "phase3_transformation": "phase3",
                "deferred": "deferred",
            }.get(phase, phase)
            row = conn.execute(
                "SELECT COUNT(*) FROM innov_ideas WHERE phase_tag=?", (stored_tag,)
            ).fetchone()
            phase_counts[stored_tag] = row[0] if row else 0

        # Avg score of scored ideas
        row = conn.execute(
            "SELECT AVG(score_total) FROM innov_ideas WHERE status NOT IN ('spark','archive') AND score_total > 0"
        ).fetchone()
        avg_score = round(row[0] or 0, 1)

        # Top 5 highest scored ideas in queue
        top_ideas = conn.execute(
            """SELECT id, title, phase_tag, score_total, status, submitter_role, department
               FROM innov_ideas
               WHERE status IN ('score','pilot') AND score_total > 0
               ORDER BY score_total DESC LIMIT 5"""
        ).fetchall()

    return {
        "stage_counts": counts,
        "total": total,
        "submitted_this_month": submitted_this_month,
        "pilots_this_month": pilots_this_month,
        "phase_counts": phase_counts,
        "avg_score": avg_score,
        "top_ideas": [dict(r) for r in top_ideas],
    }


def recent_ideas(limit: int = 10) -> list:
    with get_connection() as conn:
        rows = conn.execute(
            """SELECT id, title, status, phase_tag, score_total, submitter_name,
                      submitter_role, department, created_at
               FROM innov_ideas ORDER BY created_at DESC LIMIT ?""",
            (limit,),
        ).fetchall()
        return [dict(r) for r in rows]
