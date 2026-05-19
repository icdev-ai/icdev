from __future__ import annotations
# CUI // SP-CTI
"""FORGE Academy DB layer — all fa_* tables via get_connection()."""

import json
import logging
import secrets
from datetime import datetime, timezone

from tools.db.storage import get_connection
from .constants import ACHIEVEMENTS, SKILL_NODES, xp_to_level

_log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Migration
# ---------------------------------------------------------------------------

_DDL = """
CREATE TABLE IF NOT EXISTS fa_users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    username TEXT NOT NULL,
    display_name TEXT NOT NULL,
    email TEXT,
    role TEXT NOT NULL DEFAULT 'unset',
    role_type TEXT NOT NULL DEFAULT 'guided',
    tier_unlocked INTEGER NOT NULL DEFAULT 1,
    xp INTEGER NOT NULL DEFAULT 0,
    level TEXT NOT NULL DEFAULT 'recruit',
    guild_id INTEGER,
    streak_days INTEGER NOT NULL DEFAULT 0,
    last_active TEXT,
    tenant_id TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE(username, tenant_id)
);

CREATE TABLE IF NOT EXISTS fa_missions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    slug TEXT NOT NULL UNIQUE,
    title TEXT NOT NULL,
    tagline TEXT,
    tier INTEGER NOT NULL DEFAULT 1,
    topic TEXT,
    role_filter TEXT DEFAULT 'all',
    mission_type TEXT NOT NULL DEFAULT 'coding',
    xp_reward INTEGER NOT NULL DEFAULT 200,
    prereq_slugs_json TEXT DEFAULT '[]',
    order_idx INTEGER NOT NULL DEFAULT 0,
    difficulty TEXT DEFAULT 'intermediate',
    estimated_minutes INTEGER DEFAULT 30,
    source_credit TEXT,
    is_active INTEGER NOT NULL DEFAULT 1,
    status TEXT NOT NULL DEFAULT 'active',
    updated_at TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS fa_mission_steps (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    mission_id INTEGER NOT NULL REFERENCES fa_missions(id),
    step_num INTEGER NOT NULL,
    title TEXT NOT NULL,
    step_type TEXT NOT NULL DEFAULT 'coding',
    content_path TEXT,
    starter_code_path TEXT,
    test_code_path TEXT,
    config_schema_json TEXT DEFAULT '{}',
    xp_partial INTEGER NOT NULL DEFAULT 50,
    skill_tag TEXT,
    hint_allowed INTEGER NOT NULL DEFAULT 1,
    estimated_seconds INTEGER DEFAULT 180
);

CREATE TABLE IF NOT EXISTS fa_mission_progress (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL REFERENCES fa_users(id),
    mission_id INTEGER NOT NULL REFERENCES fa_missions(id),
    status TEXT NOT NULL DEFAULT 'not_started',
    score INTEGER DEFAULT 0,
    xp_earned INTEGER DEFAULT 0,
    attempts INTEGER DEFAULT 0,
    started_at TEXT,
    completed_at TEXT,
    UNIQUE(user_id, mission_id)
);

CREATE TABLE IF NOT EXISTS fa_step_progress (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL REFERENCES fa_users(id),
    step_id INTEGER NOT NULL REFERENCES fa_mission_steps(id),
    status TEXT NOT NULL DEFAULT 'not_started',
    submission TEXT,
    score INTEGER DEFAULT 0,
    hints_used INTEGER DEFAULT 0,
    started_at TEXT,
    completed_at TEXT,
    UNIQUE(user_id, step_id)
);

CREATE TABLE IF NOT EXISTS fa_achievements (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    slug TEXT NOT NULL UNIQUE,
    title TEXT NOT NULL,
    description TEXT,
    icon TEXT DEFAULT 'trophy',
    xp_bonus INTEGER DEFAULT 100,
    rarity TEXT DEFAULT 'common',
    criteria_json TEXT DEFAULT '{}'
);

CREATE TABLE IF NOT EXISTS fa_user_achievements (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL REFERENCES fa_users(id),
    achievement_id INTEGER NOT NULL REFERENCES fa_achievements(id),
    earned_at TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE(user_id, achievement_id)
);

CREATE TABLE IF NOT EXISTS fa_skill_nodes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    slug TEXT NOT NULL UNIQUE,
    title TEXT NOT NULL,
    tier INTEGER NOT NULL DEFAULT 1,
    role_filter TEXT DEFAULT 'all',
    prereq_ids_json TEXT DEFAULT '[]',
    pos_x INTEGER DEFAULT 0,
    pos_y INTEGER DEFAULT 0
);

CREATE TABLE IF NOT EXISTS fa_user_skills (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL REFERENCES fa_users(id),
    skill_id INTEGER NOT NULL REFERENCES fa_skill_nodes(id),
    unlocked_at TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE(user_id, skill_id)
);

CREATE TABLE IF NOT EXISTS fa_guilds (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    description TEXT,
    invite_code TEXT NOT NULL UNIQUE,
    created_by INTEGER REFERENCES fa_users(id),
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS fa_guild_members (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    guild_id INTEGER NOT NULL REFERENCES fa_guilds(id),
    user_id INTEGER NOT NULL REFERENCES fa_users(id),
    role TEXT DEFAULT 'member',
    joined_at TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE(guild_id, user_id)
);

CREATE TABLE IF NOT EXISTS fa_leaderboard_cache (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL REFERENCES fa_users(id),
    period TEXT NOT NULL DEFAULT 'weekly',
    score INTEGER DEFAULT 0,
    rank_pos INTEGER DEFAULT 0,
    computed_at TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE(user_id, period)
);

CREATE TABLE IF NOT EXISTS fa_challenges (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    title TEXT NOT NULL,
    description TEXT,
    mission_id INTEGER REFERENCES fa_missions(id),
    starts_at TEXT,
    ends_at TEXT,
    xp_multiplier REAL DEFAULT 2.0,
    is_active INTEGER DEFAULT 1
);

CREATE TABLE IF NOT EXISTS fa_challenge_entries (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    challenge_id INTEGER NOT NULL REFERENCES fa_challenges(id),
    user_id INTEGER NOT NULL REFERENCES fa_users(id),
    submission TEXT,
    score INTEGER DEFAULT 0,
    submitted_at TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE(challenge_id, user_id)
);

CREATE TABLE IF NOT EXISTS fa_workflow_submissions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL REFERENCES fa_users(id),
    design_id TEXT,
    title TEXT NOT NULL,
    score INTEGER DEFAULT 0,
    ai_feedback TEXT,
    tier INTEGER DEFAULT 2,
    submitted_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS fa_daily_logins (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL REFERENCES fa_users(id),
    login_date TEXT NOT NULL,
    xp_awarded INTEGER DEFAULT 0,
    UNIQUE(user_id, login_date)
);

CREATE TABLE IF NOT EXISTS fa_oracle_predictions (
    id TEXT PRIMARY KEY,
    lens_id TEXT NOT NULL,
    lens_name TEXT NOT NULL,
    subject_type TEXT NOT NULL,
    subject_id TEXT NOT NULL,
    prediction_type TEXT NOT NULL,
    prediction_text TEXT NOT NULL,
    confidence REAL NOT NULL DEFAULT 0.0,
    severity TEXT NOT NULL DEFAULT 'info',
    horizon_days INTEGER NOT NULL DEFAULT 7,
    evidence_json TEXT DEFAULT '{}',
    outcome TEXT NOT NULL DEFAULT 'pending',
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_fa_oracle_subject ON fa_oracle_predictions(subject_type, subject_id);

CREATE INDEX IF NOT EXISTS idx_fa_oracle_lens ON fa_oracle_predictions(lens_id);

CREATE INDEX IF NOT EXISTS idx_fa_oracle_created ON fa_oracle_predictions(created_at);

CREATE TABLE IF NOT EXISTS fa_oracle_convergence_events (
    id TEXT PRIMARY KEY,
    subject_type TEXT NOT NULL,
    subject_id TEXT NOT NULL,
    lens_count INTEGER NOT NULL DEFAULT 2,
    consensus_score REAL NOT NULL DEFAULT 0.0,
    severity TEXT NOT NULL DEFAULT 'warning',
    summary TEXT NOT NULL,
    recommended_action TEXT,
    resolved_at TEXT,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS fa_competency_levels (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL REFERENCES fa_users(id),
    level_key TEXT NOT NULL,
    level_label TEXT NOT NULL,
    achieved_at TEXT NOT NULL DEFAULT (datetime('now')),
    xp_at_achievement INTEGER DEFAULT 0,
    UNIQUE(user_id, level_key)
);

CREATE TABLE IF NOT EXISTS fa_certificates (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL REFERENCES fa_users(id),
    cert_tier TEXT NOT NULL,
    cert_label TEXT NOT NULL,
    token TEXT NOT NULL UNIQUE,
    issued_at TEXT NOT NULL DEFAULT (datetime('now')),
    expires_at TEXT,
    metadata_json TEXT DEFAULT '{}'
);

CREATE INDEX IF NOT EXISTS idx_fa_certs_token ON fa_certificates(token);
CREATE INDEX IF NOT EXISTS idx_fa_certs_user ON fa_certificates(user_id);

CREATE TABLE IF NOT EXISTS fa_mission_ontology (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    mission_id INTEGER NOT NULL REFERENCES fa_missions(id),
    ontology_id TEXT NOT NULL,
    mission_class TEXT,
    topic_class TEXT,
    competency_class TEXT,
    prereq_ontology_paths_json TEXT DEFAULT '[]',
    UNIQUE(mission_id)
);

CREATE TABLE IF NOT EXISTS fa_step_ontology (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    step_id INTEGER NOT NULL REFERENCES fa_mission_steps(id),
    ontology_id TEXT NOT NULL,
    step_class TEXT,
    UNIQUE(step_id)
);

CREATE TABLE IF NOT EXISTS fa_user_competencies (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL REFERENCES fa_users(id),
    competency_class TEXT NOT NULL,
    source_mission_id INTEGER REFERENCES fa_missions(id),
    source_step_id INTEGER REFERENCES fa_mission_steps(id),
    demonstrated_at TEXT NOT NULL DEFAULT (datetime('now')),
    evidence_json TEXT DEFAULT '{}',
    UNIQUE(user_id, competency_class, source_mission_id)
);

CREATE INDEX IF NOT EXISTS idx_fa_user_competencies_user ON fa_user_competencies(user_id);
CREATE INDEX IF NOT EXISTS idx_fa_user_competencies_class ON fa_user_competencies(competency_class);

CREATE TABLE IF NOT EXISTS kg_nodes (
    id TEXT PRIMARY KEY,
    graph_id TEXT NOT NULL,
    label TEXT,
    entity_type TEXT,
    properties TEXT,
    created_at TEXT
);

CREATE TABLE IF NOT EXISTS kg_edges (
    id TEXT PRIMARY KEY,
    graph_id TEXT NOT NULL,
    source_id TEXT NOT NULL,
    target_id TEXT NOT NULL,
    label TEXT,
    properties TEXT,
    created_at TEXT
);
"""


def migrate():
    """Create all fa_* tables and seed static data."""
    conn = get_connection()
    for stmt in _DDL.strip().split(";\n\n"):
        stmt = stmt.strip()
        if stmt:
            conn.execute(stmt)
    conn.commit()
    # Safe column additions for existing installs
    for col_ddl in [
        "ALTER TABLE fa_missions ADD COLUMN status TEXT NOT NULL DEFAULT 'active'",
        "ALTER TABLE fa_missions ADD COLUMN updated_at TEXT",
    ]:
        try:
            conn.execute(col_ddl)
            conn.commit()
        except Exception:
            pass  # Column already exists
    _seed_achievements(conn)
    _seed_skill_nodes(conn)
    seed_mission_ontology_mappings()


def _seed_achievements(conn):
    for a in ACHIEVEMENTS:
        try:
            conn.execute(
                "INSERT OR IGNORE INTO fa_achievements "
                "(slug,title,description,icon,xp_bonus,rarity,criteria_json) "
                "VALUES (?,?,?,?,?,?,?)",
                (a["slug"], a["title"], a["description"], a["icon"],
                 a["xp_bonus"], a["rarity"], a["criteria_json"]),
            )
        except Exception:
            pass
    conn.commit()


def _seed_skill_nodes(conn):
    for n in SKILL_NODES:
        pos = n.get("pos", (0, 0))
        try:
            conn.execute(
                "INSERT OR IGNORE INTO fa_skill_nodes "
                "(slug,title,tier,role_filter,prereq_ids_json,pos_x,pos_y) "
                "VALUES (?,?,?,?,?,?,?)",
                (n["slug"], n["title"], n["tier"], n["role_filter"],
                 json.dumps(n.get("prereqs", [])), pos[0], pos[1]),
            )
        except Exception:
            pass
    conn.commit()


# ---------------------------------------------------------------------------
# User CRUD
# ---------------------------------------------------------------------------

def get_or_create_user(username: str, display_name: str = "", email: str = "", tenant_id: str | None = None) -> dict:
    conn = get_connection()
    if tenant_id:
        row = conn.execute(
            "SELECT * FROM fa_users WHERE username=? AND tenant_id=?", (username, tenant_id)
        ).fetchone()
    else:
        row = conn.execute(
            "SELECT * FROM fa_users WHERE username=? AND (tenant_id IS NULL OR tenant_id='')", (username,)
        ).fetchone()
    if row:
        _touch_streak(conn, dict(row))
        if tenant_id:
            return dict(conn.execute("SELECT * FROM fa_users WHERE username=? AND tenant_id=?", (username, tenant_id)).fetchone())
        return dict(conn.execute("SELECT * FROM fa_users WHERE username=? AND (tenant_id IS NULL OR tenant_id='')", (username,)).fetchone())
    conn.execute(
        "INSERT INTO fa_users (username, display_name, email, last_active, tenant_id) VALUES (?,?,?,?,?)",
        (username, display_name or username, email,
         datetime.now(timezone.utc).isoformat(), tenant_id),
    )
    conn.commit()
    if tenant_id:
        return dict(conn.execute("SELECT * FROM fa_users WHERE username=? AND tenant_id=?", (username, tenant_id)).fetchone())
    return dict(conn.execute("SELECT * FROM fa_users WHERE username=? AND (tenant_id IS NULL OR tenant_id='')", (username,)).fetchone())


def update_user_role(user_id: int, role: str) -> None:
    from .constants import ROLES
    role_type = ROLES.get(role, {}).get("type", "guided")
    conn = get_connection()
    conn.execute(
        "UPDATE fa_users SET role=?, role_type=? WHERE id=?",
        (role, role_type, user_id),
    )
    conn.commit()


def update_user_xp(user_id: int, xp_delta: int) -> dict:
    conn = get_connection()
    conn.execute("UPDATE fa_users SET xp = xp + ? WHERE id=?", (xp_delta, user_id))
    row = conn.execute("SELECT xp FROM fa_users WHERE id=?", (user_id,)).fetchone()
    new_xp = row["xp"]
    new_level = xp_to_level(new_xp)["slug"]
    conn.execute("UPDATE fa_users SET level=? WHERE id=?", (new_level, user_id))
    conn.commit()
    return {"xp": new_xp, "level": new_level}


def _touch_streak(conn, user: dict) -> None:
    today = datetime.now(timezone.utc).date().isoformat()
    last = (user.get("last_active") or "")[:10]
    if last == today:
        return
    streak = user.get("streak_days", 0)
    from datetime import date, timedelta
    yesterday = (date.today() - timedelta(days=1)).isoformat()
    streak = (streak + 1) if last == yesterday else 1
    conn.execute(
        "UPDATE fa_users SET streak_days=?, last_active=? WHERE id=?",
        (streak, datetime.now(timezone.utc).isoformat(), user["id"]),
    )
    conn.commit()


def get_user(user_id: int, tenant_id: str | None = None) -> dict | None:
    conn = get_connection()
    if tenant_id is not None:
        row = conn.execute(
            "SELECT * FROM fa_users WHERE id=? AND tenant_id=?", (user_id, tenant_id)
        ).fetchone()
    else:
        row = conn.execute(
            "SELECT * FROM fa_users WHERE id=?", (user_id,)
        ).fetchone()
    return dict(row) if row else None


def get_user_by_username(username: str, tenant_id: str | None = None) -> dict | None:
    conn = get_connection()
    if tenant_id is not None:
        row = conn.execute(
            "SELECT * FROM fa_users WHERE username=? AND tenant_id=?", (username, tenant_id)
        ).fetchone()
    else:
        row = conn.execute(
            "SELECT * FROM fa_users WHERE username=?", (username,)
        ).fetchone()
    return dict(row) if row else None


# ---------------------------------------------------------------------------
# Mission CRUD
# ---------------------------------------------------------------------------

def list_missions(tier: int = None, role: str = None,
                  mission_type: str = None, tenant_id: str | None = None) -> list[dict]:
    conn = get_connection()
    q = "SELECT * FROM fa_missions WHERE is_active=1"
    params = []
    if tier:
        q += " AND tier=?"
        params.append(tier)
    if mission_type:
        q += " AND mission_type=?"
        params.append(mission_type)
    if tenant_id is not None:
        q += " AND tenant_id=?"
        params.append(tenant_id)
    q += " ORDER BY tier, order_idx"
    rows = conn.execute(q, params).fetchall()
    missions = [dict(r) for r in rows]
    if role:
        missions = [
            m for m in missions
            if not m.get("role_filter") or m["role_filter"] == "all"
            or role in m["role_filter"].split(",")
        ]
    return missions


def get_mission(slug: str) -> dict | None:
    row = get_connection().execute(
        "SELECT * FROM fa_missions WHERE slug=?", (slug,)
    ).fetchone()
    return dict(row) if row else None


def get_mission_steps(mission_id: int) -> list[dict]:
    rows = get_connection().execute(
        "SELECT * FROM fa_mission_steps WHERE mission_id=? ORDER BY step_num",
        (mission_id,),
    ).fetchall()
    return [dict(r) for r in rows]


def upsert_mission(data: dict) -> int:
    conn = get_connection()
    conn.execute(
        """INSERT INTO fa_missions
           (slug,title,tagline,tier,topic,role_filter,mission_type,xp_reward,
            prereq_slugs_json,order_idx,difficulty,estimated_minutes,source_credit)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
           ON CONFLICT(slug) DO UPDATE SET
             title=excluded.title, tagline=excluded.tagline,
             xp_reward=excluded.xp_reward, order_idx=excluded.order_idx""",
        (data["slug"], data["title"], data.get("tagline", ""),
         data.get("tier", 1), data.get("topic", ""), data.get("role_filter", "all"),
         data.get("mission_type", "coding"), data.get("xp_reward", 200),
         json.dumps(data.get("prereqs", [])), data.get("order_idx", 0),
         data.get("difficulty", "intermediate"), data.get("estimated_minutes", 30),
         data.get("source_credit", "")),
    )
    conn.commit()
    row = conn.execute("SELECT id FROM fa_missions WHERE slug=?", (data["slug"],)).fetchone()
    return row["id"]


# ---------------------------------------------------------------------------
# Progress CRUD
# ---------------------------------------------------------------------------

def get_mission_progress(user_id: int, mission_id: int, tenant_id: str | None = None) -> dict:
    conn = get_connection()
    # Verify user belongs to tenant before returning progress
    if tenant_id is not None:
        user_row = conn.execute(
            "SELECT id FROM fa_users WHERE id=? AND tenant_id=?", (user_id, tenant_id)
        ).fetchone()
        if not user_row:
            return {"status": "not_started", "xp_earned": 0, "attempts": 0, "score": 0}
    row = conn.execute(
        "SELECT * FROM fa_mission_progress WHERE user_id=? AND mission_id=?",
        (user_id, mission_id),
    ).fetchone()
    if row:
        return dict(row)
    return {"status": "not_started", "xp_earned": 0, "attempts": 0, "score": 0}


def start_mission(user_id: int, mission_id: int) -> None:
    conn = get_connection()
    now = datetime.now(timezone.utc).isoformat()
    existing = conn.execute(
        "SELECT id FROM fa_mission_progress WHERE user_id=? AND mission_id=?",
        (user_id, mission_id),
    ).fetchone()
    if existing:
        conn.execute(
            "UPDATE fa_mission_progress SET status='in_progress', attempts=attempts+1 "
            "WHERE user_id=? AND mission_id=?",
            (user_id, mission_id),
        )
    else:
        conn.execute(
            "INSERT INTO fa_mission_progress (user_id,mission_id,status,attempts,started_at) "
            "VALUES (?,?,'in_progress',1,?)",
            (user_id, mission_id, now),
        )
    conn.commit()


def complete_mission(user_id: int, mission_id: int, score: int = 100) -> None:
    conn = get_connection()
    now = datetime.now(timezone.utc).isoformat()
    existing = conn.execute(
        "SELECT id FROM fa_mission_progress WHERE user_id=? AND mission_id=?",
        (user_id, mission_id),
    ).fetchone()
    if existing:
        conn.execute(
            "UPDATE fa_mission_progress SET status='completed', score=?, completed_at=? "
            "WHERE user_id=? AND mission_id=?",
            (score, now, user_id, mission_id),
        )
    else:
        conn.execute(
            "INSERT INTO fa_mission_progress (user_id,mission_id,status,score,completed_at) "
            "VALUES (?,?,'completed',?,?)",
            (user_id, mission_id, score, now),
        )
    conn.commit()


def get_step_progress(user_id: int, step_id: int) -> dict:
    row = get_connection().execute(
        "SELECT * FROM fa_step_progress WHERE user_id=? AND step_id=?",
        (user_id, step_id),
    ).fetchone()
    return dict(row) if row else {"status": "not_started", "hints_used": 0, "score": 0}


def complete_step(user_id: int, step_id: int, submission: str = "",
                  passed: bool = True, hints_used: int = 0) -> None:
    conn = get_connection()
    now = datetime.now(timezone.utc).isoformat()
    score = 100 if passed else 0
    existing = conn.execute(
        "SELECT id FROM fa_step_progress WHERE user_id=? AND step_id=?",
        (user_id, step_id),
    ).fetchone()
    if existing:
        conn.execute(
            "UPDATE fa_step_progress SET status='completed', submission=?, score=?, "
            "hints_used=?, completed_at=? WHERE user_id=? AND step_id=?",
            (submission, score, hints_used, now, user_id, step_id),
        )
    else:
        conn.execute(
            "INSERT INTO fa_step_progress "
            "(user_id,step_id,status,submission,score,hints_used,completed_at) "
            "VALUES (?,?,'completed',?,?,?,?)",
            (user_id, step_id, submission, score, hints_used, now),
        )
    conn.commit()


def user_progress_summary(user_id: int, tenant_id: str | None = None) -> dict:
    conn = get_connection()
    # Verify user belongs to tenant
    if tenant_id is not None:
        user_row = conn.execute(
            "SELECT id FROM fa_users WHERE id=? AND tenant_id=?", (user_id, tenant_id)
        ).fetchone()
        if not user_row:
            return {"total_missions": 0, "completed": 0, "steps_completed": 0, "in_progress": None}
    total = conn.execute("SELECT COUNT(*) FROM fa_missions WHERE is_active=1").fetchone()[0]
    done = conn.execute(
        "SELECT COUNT(*) FROM fa_mission_progress WHERE user_id=? AND status='completed'",
        (user_id,),
    ).fetchone()[0]
    steps_done = conn.execute(
        "SELECT COUNT(*) FROM fa_step_progress WHERE user_id=? AND status='completed'",
        (user_id,),
    ).fetchone()[0]
    in_prog = conn.execute(
        """SELECT m.slug, m.title, m.tier, mp.attempts
           FROM fa_mission_progress mp
           JOIN fa_missions m ON m.id=mp.mission_id
           WHERE mp.user_id=? AND mp.status='in_progress'
           ORDER BY mp.id DESC LIMIT 1""",
        (user_id,),
    ).fetchone()
    return {
        "total_missions": total,
        "completed": done,
        "steps_completed": steps_done,
        "in_progress": dict(in_prog) if in_prog else None,
    }


# ---------------------------------------------------------------------------
# Achievements
# ---------------------------------------------------------------------------

def get_user_achievements(user_id: int) -> list[dict]:
    # Qualify classification as a.classification to prevent RLS ambiguity
    # when both joined tables carry a classification column.
    conn = get_connection()
    conn.set_security_context(None)
    rows = conn.execute(
        """SELECT a.*, ua.earned_at
           FROM fa_user_achievements ua
           JOIN fa_achievements a ON a.id=ua.achievement_id
           WHERE ua.user_id=?
           ORDER BY ua.earned_at DESC""",
        (user_id,),
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_user_skills(user_id: int) -> set[str]:
    """Return set of skill slugs the user has unlocked."""
    rows = get_connection().execute(
        """SELECT sn.slug FROM fa_user_skills us
           JOIN fa_skill_nodes sn ON sn.id = us.skill_id
           WHERE us.user_id = ?""",
        (user_id,),
    ).fetchall()
    return {r["slug"] for r in rows}


def unlock_skill(user_id: int, skill_slug: str) -> bool:
    """Unlock a skill node for the user. Returns True if newly unlocked."""
    conn = get_connection()
    node = conn.execute(
        "SELECT id FROM fa_skill_nodes WHERE slug = ?", (skill_slug,)
    ).fetchone()
    if not node:
        return False
    try:
        conn.execute(
            "INSERT INTO fa_user_skills (user_id, skill_id) VALUES (?, ?)",
            (user_id, node["id"]),
        )
        conn.commit()
        return True
    except Exception:
        return False


def grant_achievement(user_id: int, slug: str) -> dict | None:
    conn = get_connection()
    ach = conn.execute(
        "SELECT * FROM fa_achievements WHERE slug=?", (slug,)
    ).fetchone()
    if not ach:
        return None
    try:
        conn.execute(
            "INSERT INTO fa_user_achievements (user_id,achievement_id) VALUES (?,?)",
            (user_id, ach["id"]),
        )
        conn.commit()
        return dict(ach)
    except Exception:
        return None  # already earned


# ---------------------------------------------------------------------------
# Guilds
# ---------------------------------------------------------------------------

def create_guild(name: str, description: str, created_by: int) -> dict:
    conn = get_connection()
    code = secrets.token_urlsafe(6).upper()
    conn.execute(
        "INSERT INTO fa_guilds (name,description,invite_code,created_by) VALUES (?,?,?,?)",
        (name, description, code, created_by),
    )
    conn.commit()
    row = conn.execute(
        "SELECT * FROM fa_guilds WHERE invite_code=?", (code,)
    ).fetchone()
    guild = dict(row)
    conn.execute(
        "INSERT INTO fa_guild_members (guild_id,user_id,role) VALUES (?,?,'leader')",
        (guild["id"], created_by),
    )
    conn.execute(
        "UPDATE fa_users SET guild_id=? WHERE id=?", (guild["id"], created_by)
    )
    conn.commit()
    return guild


def join_guild(invite_code: str, user_id: int) -> dict | None:
    conn = get_connection()
    guild = conn.execute(
        "SELECT * FROM fa_guilds WHERE invite_code=?", (invite_code.upper(),)
    ).fetchone()
    if not guild:
        return None
    try:
        conn.execute(
            "INSERT INTO fa_guild_members (guild_id,user_id) VALUES (?,?)",
            (guild["id"], user_id),
        )
        conn.execute(
            "UPDATE fa_users SET guild_id=? WHERE id=?", (guild["id"], user_id)
        )
        conn.commit()
    except Exception:
        pass
    return dict(guild)


def get_guild_stats(guild_id: int) -> dict:
    conn = get_connection()
    members = conn.execute(
        """SELECT u.display_name, u.xp, u.level, gm.role
           FROM fa_guild_members gm
           JOIN fa_users u ON u.id=gm.user_id
           WHERE gm.guild_id=?
           ORDER BY u.xp DESC""",
        (guild_id,),
    ).fetchall()
    total_xp = sum(m["xp"] for m in members)
    return {"members": [dict(m) for m in members], "total_xp": total_xp}


# ---------------------------------------------------------------------------
# Leaderboard
# ---------------------------------------------------------------------------

def get_leaderboard(period: str = "alltime", role: str = None, limit: int = 20, tenant_id: str | None = None) -> list[dict]:
    conn = get_connection()
    q = """SELECT u.display_name, u.role, u.level, u.xp, u.streak_days,
                  u.guild_id, g.name as guild_name
           FROM fa_users u
           LEFT JOIN fa_guilds g ON g.id=u.guild_id
           WHERE u.role != 'unset'"""
    params = []
    if tenant_id:
        q += " AND u.tenant_id=?"
        params.append(tenant_id)
    else:
        q += " AND (u.tenant_id IS NULL OR u.tenant_id='')"
    if role:
        q += " AND u.role=?"
        params.append(role)
    q += " ORDER BY u.xp DESC LIMIT ?"
    params.append(limit)
    rows = conn.execute(q, params).fetchall()
    return [dict(r) for r in rows]


# ---------------------------------------------------------------------------
# Certification system
# ---------------------------------------------------------------------------

def check_cert_eligibility(user_id: int, cert_key: str) -> dict:
    """Check whether a user meets the gates for a cert tier.

    Returns dict with keys: eligible (bool), gates (list of {name, met, detail}).
    """
    from apps.forge_academy.constants import CERT_BY_KEY
    from tools.db.storage import get_connection as _gc

    cert = CERT_BY_KEY.get(cert_key)
    if not cert:
        return {"eligible": False, "gates": [], "error": "unknown cert key"}

    conn = _gc()
    user = get_user(user_id)
    if not user:
        return {"eligible": False, "gates": [], "error": "user not found"}

    reqs = cert.get("requirements", {})
    gates = []

    # Gate: Tier 1 complete
    if reqs.get("tier1_complete"):
        t1_missions = conn.execute(
            """SELECT COUNT(*) FROM fa_missions WHERE tier=1"""
        ).fetchone()[0]
        t1_done = conn.execute(
            """SELECT COUNT(DISTINCT mp.mission_id)
               FROM fa_mission_progress mp
               JOIN fa_missions m ON m.id=mp.mission_id
               WHERE mp.user_id=? AND mp.status='completed' AND m.tier=1""",
            (user_id,),
        ).fetchone()[0]
        met = t1_done >= t1_missions > 0
        gates.append({"name": "Tier 1 Complete", "met": met,
                      "detail": f"{t1_done}/{t1_missions} Tier 1 missions completed"})

    # Gate: Role Tier 2 complete (100% of user's role missions)
    if reqs.get("role_tier2_pct"):
        role = user.get("role", "")
        if role and role != "unset":
            t2_role = conn.execute(
                """SELECT COUNT(*) FROM fa_missions
                   WHERE tier=2 AND (role_filter='all' OR role_filter LIKE ?)""",
                (f"%{role}%",),
            ).fetchone()[0]
            t2_done = conn.execute(
                """SELECT COUNT(DISTINCT mp.mission_id)
                   FROM fa_mission_progress mp
                   JOIN fa_missions m ON m.id=mp.mission_id
                   WHERE mp.user_id=? AND mp.status='completed' AND m.tier=2
                     AND (m.role_filter='all' OR m.role_filter LIKE ?)""",
                (user_id, f"%{role}%"),
            ).fetchone()[0]
            pct = int((t2_done / t2_role * 100) if t2_role else 0)
            met = pct >= reqs["role_tier2_pct"]
            gates.append({"name": f"Role Tier 2 ({role})", "met": met,
                          "detail": f"{t2_done}/{t2_role} role missions ({pct}%)"})
        else:
            gates.append({"name": "Role Tier 2", "met": False,
                          "detail": "Set your role in profile first"})

    # Gate: Foundation cert required
    if reqs.get("foundation"):
        has_found = conn.execute(
            "SELECT COUNT(*) FROM fa_certificates WHERE user_id=? AND cert_tier='foundation'",
            (user_id,),
        ).fetchone()[0] > 0
        gates.append({"name": "Foundation Cert", "met": has_found,
                      "detail": "FORGE AI Foundation certificate required"})

    # Gate: AADC design score >= threshold
    if reqs.get("aadc_score_min"):
        try:
            best = conn.execute(
                """SELECT MAX(CAST(JSON_EXTRACT(metadata_json,'$.aadc_score') AS REAL))
                   FROM fa_user_achievements WHERE user_id=?""",
                (user_id,),
            ).fetchone()[0] or 0
        except Exception:
            best = 0
        met = best >= reqs["aadc_score_min"]
        gates.append({"name": f"AADC Score >= {reqs['aadc_score_min']}", "met": met,
                      "detail": f"Best AADC assessment score: {best}"})

    # Gate: GameDay scenarios
    if reqs.get("gameday_scenarios_min"):
        try:
            gd = conn.execute(
                """SELECT COUNT(*) FROM ttx_receipts WHERE player_id=? AND status='submitted'""",
                (user_id,),
            ).fetchone()[0]
        except Exception:
            gd = 0
        met = gd >= reqs["gameday_scenarios_min"]
        gates.append({"name": f"GameDay >= {reqs['gameday_scenarios_min']} scenarios", "met": met,
                      "detail": f"GameDay scenarios completed: {gd}"})

    # Gate: Practitioner cert required
    if reqs.get("practitioner"):
        has_prac = conn.execute(
            "SELECT COUNT(*) FROM fa_certificates WHERE user_id=? AND cert_tier='practitioner'",
            (user_id,),
        ).fetchone()[0] > 0
        gates.append({"name": "Practitioner Cert", "met": has_prac,
                      "detail": "FORGE AI Practitioner certificate required"})

    # Gate: Tier 3 complete
    if reqs.get("tier3_complete"):
        t3_total = conn.execute("SELECT COUNT(*) FROM fa_missions WHERE tier=3").fetchone()[0]
        t3_done  = conn.execute(
            """SELECT COUNT(DISTINCT mp.mission_id)
               FROM fa_mission_progress mp
               JOIN fa_missions m ON m.id=mp.mission_id
               WHERE mp.user_id=? AND mp.status='completed' AND m.tier=3""",
            (user_id,),
        ).fetchone()[0]
        met = t3_done >= t3_total > 0
        gates.append({"name": "Tier 3 Complete", "met": met,
                      "detail": f"{t3_done}/{t3_total} Tier 3 missions completed"})

    eligible = all(g["met"] for g in gates) and len(gates) > 0
    return {"eligible": eligible, "gates": gates}


def issue_certificate(user_id: int, cert_key: str) -> dict | None:
    """Issue a certificate if eligible. Returns the cert record or None."""
    import secrets
    from datetime import datetime, timezone
    from tools.db.storage import get_connection as _gc
    from apps.forge_academy.constants import CERT_BY_KEY

    eligibility = check_cert_eligibility(user_id, cert_key)
    if not eligibility.get("eligible"):
        return None

    cert_def = CERT_BY_KEY.get(cert_key, {})
    conn = _gc()
    # Idempotent: return existing cert if already issued
    existing = conn.execute(
        "SELECT * FROM fa_certificates WHERE user_id=? AND cert_tier=?",
        (user_id, cert_key),
    ).fetchone()
    if existing:
        return dict(existing)

    token = secrets.token_urlsafe(32)
    now = datetime.now(timezone.utc).isoformat()
    conn.execute(
        """INSERT INTO fa_certificates
           (user_id, cert_tier, cert_label, token, issued_at)
           VALUES (?,?,?,?,?)""",
        (user_id, cert_key, cert_def.get("label", cert_key), token, now),
    )
    # Award XP bonus
    xp_bonus = cert_def.get("xp_bonus", 0)
    if xp_bonus:
        from apps.forge_academy.db import update_user_xp
        update_user_xp(user_id, xp_bonus)
    conn.commit()
    return conn.execute(
        "SELECT * FROM fa_certificates WHERE user_id=? AND cert_tier=?",
        (user_id, cert_key),
    ).fetchone()


def get_user_certificates(user_id: int) -> list[dict]:
    rows = get_connection().execute(
        "SELECT * FROM fa_certificates WHERE user_id=? ORDER BY issued_at DESC",
        (user_id,),
    ).fetchall()
    return [dict(r) for r in rows]


def verify_certificate_token(token: str) -> dict | None:
    """Verify a cert token. Returns cert+user info or None."""
    conn = get_connection()
    row = conn.execute(
        """SELECT c.*, u.display_name, u.role
           FROM fa_certificates c
           JOIN fa_users u ON u.id=c.user_id
           WHERE c.token=?""",
        (token,),
    ).fetchone()
    return dict(row) if row else None


# ---------------------------------------------------------------------------
# Ontology mapping
# ---------------------------------------------------------------------------

def upsert_mission_ontology(mission_id: int, ontology_id: str, mission_class: str,
                             topic_class: str, competency_class: str,
                             prereq_paths: list[str] | None = None) -> None:
    conn = get_connection()
    conn.execute(
        """INSERT INTO fa_mission_ontology
           (mission_id, ontology_id, mission_class, topic_class, competency_class, prereq_ontology_paths_json)
           VALUES (?, ?, ?, ?, ?, ?)
           ON CONFLICT(mission_id) DO UPDATE SET
             ontology_id=excluded.ontology_id,
             mission_class=excluded.mission_class,
             topic_class=excluded.topic_class,
             competency_class=excluded.competency_class,
             prereq_ontology_paths_json=excluded.prereq_ontology_paths_json""",
        (mission_id, ontology_id, mission_class, topic_class, competency_class,
         json.dumps(prereq_paths or [])),
    )
    conn.commit()


def upsert_step_ontology(step_id: int, ontology_id: str, step_class: str) -> None:
    conn = get_connection()
    conn.execute(
        """INSERT INTO fa_step_ontology
           (step_id, ontology_id, step_class)
           VALUES (?, ?, ?)
           ON CONFLICT(step_id) DO UPDATE SET
             ontology_id=excluded.ontology_id,
             step_class=excluded.step_class""",
        (step_id, ontology_id, step_class),
    )
    conn.commit()


# ---------------------------------------------------------------------------
# Competency tracking + KG edges
# ---------------------------------------------------------------------------

def record_user_competency(user_id: int, competency_class: str,
                            source_mission_id: int | None = None,
                            source_step_id: int | None = None,
                            evidence: dict | None = None) -> dict:
    """Record a demonstrated competency and create a KG edge."""
    conn = get_connection()
    now = datetime.now(timezone.utc).isoformat()
    conn.execute(
        """INSERT OR IGNORE INTO fa_user_competencies
           (user_id, competency_class, source_mission_id, source_step_id, demonstrated_at, evidence_json)
           VALUES (?, ?, ?, ?, ?, ?)""",
        (user_id, competency_class, source_mission_id, source_step_id, now,
         json.dumps(evidence or {})),
    )
    conn.commit()

    # Create KG edge: user -> demonstrates -> competency_class
    try:
        _create_kg_competency_edge(conn, user_id, competency_class, source_mission_id, now)
    except Exception:
        pass

    row = conn.execute(
        "SELECT * FROM fa_user_competencies WHERE user_id=? AND competency_class=? AND source_mission_id=?",
        (user_id, competency_class, source_mission_id),
    ).fetchone()
    return dict(row) if row else {"user_id": user_id, "competency_class": competency_class}


def _create_kg_competency_edge(conn, user_id: int, competency_class: str,
                                source_mission_id: int | None, demonstrated_at: str) -> None:
    """Insert a KG edge linking the user to the ontology competency class."""
    user = conn.execute("SELECT username FROM fa_users WHERE id=?", (user_id,)).fetchone()
    user_label = user["username"] if user else f"user_{user_id}"
    source_node = f"fa_user:{user_id}"
    target_node = f"ontology:{competency_class}"
    edge_id = f"{source_node}--demonstrates--{target_node}--{source_mission_id or 0}"

    conn.execute(
        """INSERT OR REPLACE INTO kg_nodes
           (id, graph_id, label, entity_type, properties, created_at)
           VALUES (?, ?, ?, ?, ?, ?)""",
        (source_node, "icdev-core-ontology", user_label, "fa_user",
         json.dumps({"user_id": user_id}), demonstrated_at),
    )
    conn.execute(
        """INSERT OR REPLACE INTO kg_nodes
           (id, graph_id, label, entity_type, properties, created_at)
           VALUES (?, ?, ?, ?, ?, ?)""",
        (target_node, "icdev-core-ontology", competency_class, "ontology_class",
         json.dumps({"canonical_id": competency_class}), demonstrated_at),
    )
    conn.execute(
        """INSERT OR REPLACE INTO kg_edges
           (id, graph_id, source_id, target_id, label, properties, created_at)
           VALUES (?, ?, ?, ?, ?, ?, ?)""",
        (edge_id, "icdev-core-ontology", source_node, target_node, "demonstrates",
         json.dumps({"source_mission_id": source_mission_id, "user_id": user_id}), demonstrated_at),
    )
    conn.commit()


def get_user_competencies(user_id: int) -> list[dict]:
    rows = get_connection().execute(
        """SELECT uc.*, m.slug as mission_slug, m.title as mission_title
           FROM fa_user_competencies uc
           LEFT JOIN fa_missions m ON m.id = uc.source_mission_id
           WHERE uc.user_id = ?
           ORDER BY uc.demonstrated_at DESC""",
        (user_id,),
    ).fetchall()
    return [dict(r) for r in rows]


def seed_mission_ontology_mappings() -> None:
    """Seed ontology mappings for all builtin missions."""
    from .ontology import build_mission_ontology_id, build_step_ontology_id
    from .content_loader import BUILTIN_MISSIONS, BUILTIN_STEPS
    conn = get_connection()
    for m in BUILTIN_MISSIONS:
        row = conn.execute("SELECT id FROM fa_missions WHERE slug=?", (m["slug"],)).fetchone()
        if not row:
            continue
        mission_id = row["id"]
        onto = build_mission_ontology_id(
            slug=m["slug"],
            mission_type=m.get("mission_type", "coding"),
            topic=m.get("topic", ""),
            title=m.get("title", ""),
            tier=m.get("tier", 1),
        )
        upsert_mission_ontology(
            mission_id=mission_id,
            ontology_id=onto["ontology_id"],
            mission_class=onto["mission_class"],
            topic_class=onto["topic_class"],
            competency_class=onto["competency_class"],
            prereq_paths=onto["prereq_ontology_paths"],
        )
        # Seed step ontologies
        steps = BUILTIN_STEPS.get(m["slug"], [])
        for step in steps:
            step_row = conn.execute(
                "SELECT id FROM fa_mission_steps WHERE mission_id=? AND step_num=?",
                (mission_id, step["step_num"]),
            ).fetchone()
            if not step_row:
                continue
            step_onto = build_step_ontology_id(m["slug"], step["step_num"], step.get("step_type", "configure"))
            upsert_step_ontology(
                step_id=step_row["id"],
                ontology_id=step_onto["ontology_id"],
                step_class=step_onto["step_class"],
            )
    _log.info("FORGE Academy: seeded ontology mappings")
