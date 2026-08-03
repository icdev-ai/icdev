from __future__ import annotations
# CUI // SP-CTI
"""FORGE Academy DB layer — all fa_* tables via get_connection()."""

import json
import logging
import secrets
from datetime import datetime, timezone

from tools.db.storage import get_connection
from .constants import (
    ACHIEVEMENTS,
    MISSION_STATUS_COMPLETED,
    MISSION_STATUS_IN_PROGRESS,
    SKILL_NODES,
    STEP_STATUS_ATTEMPTED,
    STEP_STATUS_COMPLETED,
    STEP_STATUS_NOT_STARTED,
    xp_to_level,
)

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
    -- aca-trn-03: what the learner will be able to do afterwards. NULL where the
    -- authored content states none — never a synthesised stand-in, because this is
    -- the field a compliance audit reads. Extracted by
    -- content_loader.extract_learning_objective; migration 20260803005919 backfills.
    learning_objective TEXT,
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
    tenant_id TEXT,
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
    tenant_id TEXT,
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

-- aca-int-07: XP provenance. One append-only row per award, naming what earned it.
-- Declared here as well as in migration 315 so it exists from the child app's own
-- first-request DDL: a lookup against a missing table inside a caller's open
-- transaction ABORTS that transaction on PostgreSQL, and swallowing the error then
-- wedges every later statement on the same connection.
CREATE TABLE IF NOT EXISTS fa_xp_ledger (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL REFERENCES fa_users(id),
    xp_delta INTEGER NOT NULL,
    reason TEXT NOT NULL,
    source_type TEXT,
    source_id INTEGER,
    is_attendance INTEGER NOT NULL DEFAULT 0,
    verified INTEGER NOT NULL DEFAULT 1,
    note TEXT,
    created_at TEXT DEFAULT (datetime('now')),
    classification TEXT DEFAULT 'CUI',
    tenant_id TEXT
);

-- aca-int-07 part 2: what a certificate was issued against, snapshotted at issue
-- time. Declared here as well as in migration 317 for the same reason as
-- fa_xp_ledger: a query against a missing table inside an open transaction aborts
-- that transaction on PostgreSQL.
CREATE TABLE IF NOT EXISTS fa_certificate_evidence (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    cert_id INTEGER NOT NULL,
    user_id INTEGER NOT NULL REFERENCES fa_users(id),
    evidence_type TEXT NOT NULL,
    ref_id INTEGER,
    label TEXT NOT NULL,
    detail TEXT,
    demonstrated_at TEXT,
    score INTEGER,
    classification TEXT DEFAULT 'CUI',
    tenant_id TEXT,
    created_at TEXT DEFAULT (datetime('now'))
);

-- aca-trn-04: the instructor workflow. Declared here as well as in migration 323
-- for the same reason as fa_xp_ledger above — a query against a missing table
-- inside a caller's open transaction ABORTS that transaction on PostgreSQL.
CREATE TABLE IF NOT EXISTS fa_assignments (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    assignment_type TEXT NOT NULL DEFAULT 'mission',
    mission_id INTEGER REFERENCES fa_missions(id),
    track_key TEXT,
    target_type TEXT NOT NULL DEFAULT 'learner',
    target_user_id INTEGER REFERENCES fa_users(id),
    target_role TEXT,
    due_at TEXT,
    note TEXT,
    assigned_by TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'open',
    tenant_id TEXT,
    classification TEXT DEFAULT 'CUI',
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS fa_instructor_reviews (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL REFERENCES fa_users(id),
    mission_id INTEGER REFERENCES fa_missions(id),
    step_id INTEGER REFERENCES fa_mission_steps(id),
    assignment_id INTEGER REFERENCES fa_assignments(id),
    verdict TEXT NOT NULL,
    override_score INTEGER,
    prior_score INTEGER,
    comment TEXT,
    reviewer TEXT NOT NULL,
    tenant_id TEXT,
    classification TEXT DEFAULT 'CUI',
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

-- Append-only (registered in APPEND_ONLY_TABLES). A grade override that cannot
-- be attributed to a person is indistinguishable from a bug in the grader.
CREATE TABLE IF NOT EXISTS fa_instructor_audit (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    action TEXT NOT NULL,
    actor TEXT NOT NULL,
    actor_role TEXT,
    subject_type TEXT,
    subject_id TEXT,
    detail_json TEXT DEFAULT '{}',
    tenant_id TEXT,
    classification TEXT DEFAULT 'CUI',
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

-- aca-trn-01: the assessment model. Declared here as well as in migration 324 for
-- the same reason as fa_xp_ledger — a query against a missing table inside an open
-- transaction aborts that transaction on PostgreSQL, and classify_step runs on every
-- mission page render.
CREATE TABLE IF NOT EXISTS fa_assessment_items (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    step_id       INTEGER NOT NULL,
    item_key      TEXT NOT NULL,
    prompt        TEXT NOT NULL,
    options_json  TEXT NOT NULL DEFAULT '[]',
    correct_index INTEGER NOT NULL DEFAULT 0,
    explanation   TEXT,
    difficulty    TEXT DEFAULT 'core',
    is_active     INTEGER NOT NULL DEFAULT 1,
    classification TEXT DEFAULT 'CUI',
    tenant_id     TEXT,
    created_at    TEXT DEFAULT (datetime('now')),
    UNIQUE(step_id, item_key)
);

CREATE INDEX IF NOT EXISTS idx_fa_items_step ON fa_assessment_items(step_id);

-- Append-only (registered in APPEND_ONLY_TABLES). An attempt limit whose ledger can
-- be edited is not a limit, and fa_xp_ledger cites these rows.
CREATE TABLE IF NOT EXISTS fa_step_attempts (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id      INTEGER NOT NULL,
    step_id      INTEGER NOT NULL,
    kind         TEXT NOT NULL DEFAULT 'attempt',
    attempt_num  INTEGER NOT NULL DEFAULT 1,
    policy       TEXT NOT NULL DEFAULT 'practice',
    served_json  TEXT NOT NULL DEFAULT '[]',
    answers_json TEXT,
    score_pct    INTEGER,
    passed       INTEGER,
    closed_at    TEXT,
    reason       TEXT,
    actor        TEXT,
    classification TEXT DEFAULT 'CUI',
    tenant_id    TEXT,
    created_at   TEXT DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_fa_attempts_user_step ON fa_step_attempts(user_id, step_id);

CREATE TABLE IF NOT EXISTS fa_step_assessment_policy (
    id                 INTEGER PRIMARY KEY AUTOINCREMENT,
    step_id            INTEGER NOT NULL,
    policy             TEXT NOT NULL DEFAULT 'practice',
    items_per_attempt  INTEGER,
    pass_threshold_pct INTEGER,
    max_attempts       INTEGER,
    updated_at         TEXT,
    created_at         TEXT DEFAULT (datetime('now')),
    UNIQUE(step_id)
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
    competency_class TEXT,
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

-- kg_nodes / kg_edges are PLATFORM-OWNED, not Academy tables: on a provisioned
-- install they already hold thousands of rows from the rest of ICDEV, so these
-- CREATE ... IF NOT EXISTS statements are no-ops there and the column list below
-- must match the platform schema exactly.
--
-- aca-trn-02: it did not. This block declared `kg_edges.label`, but the platform
-- table names that column `relationship`. Every competency edge insert therefore
-- raised UndefinedColumn on PostgreSQL — swallowed by a bare `except: pass`,
-- which on PG leaves the transaction ABORTED, so the very next statement in
-- record_user_competency failed with InFailedSqlTransaction. The competency
-- chain could not have recorded a single row on the primary backend.
--
-- The platform declares both graph_id columns as REFERENCES kg_graphs(id), so a
-- node cannot be inserted into a graph that does not exist yet. kg_graphs is
-- declared here for the same reason the other two are: so the Academy can
-- guarantee its own graph row. See _ensure_kg_graph.
CREATE TABLE IF NOT EXISTS kg_graphs (
    id TEXT PRIMARY KEY,
    project_id TEXT,
    name TEXT NOT NULL,
    description TEXT,
    entity_count INTEGER DEFAULT 0,
    edge_count INTEGER DEFAULT 0,
    metadata TEXT DEFAULT '{}',
    created_at TEXT,
    updated_at TEXT
);

CREATE TABLE IF NOT EXISTS kg_nodes (
    id TEXT PRIMARY KEY,
    graph_id TEXT NOT NULL,
    label TEXT NOT NULL,
    entity_type TEXT NOT NULL,
    properties TEXT DEFAULT '{}',
    created_at TEXT
);

CREATE TABLE IF NOT EXISTS kg_edges (
    id TEXT PRIMARY KEY,
    graph_id TEXT NOT NULL,
    source_id TEXT NOT NULL,
    target_id TEXT NOT NULL,
    relationship TEXT NOT NULL,
    weight REAL DEFAULT 1.0,
    properties TEXT DEFAULT '{}',
    created_at TEXT
);
"""


def _set_lock_timeout(conn, value: str) -> None:
    """Best-effort SET lock_timeout (PostgreSQL only; no-op/ignored on SQLite)."""
    try:
        conn.execute(f"SET lock_timeout = '{value}'")
        conn.commit()
    except Exception:
        try:
            conn.rollback()
        except Exception:
            pass


def migrate():
    """Create all fa_* tables and seed static data.

    DDL runs with a short ``lock_timeout`` so a blocked ``CREATE``/``ALTER``
    (e.g. queued behind a leaked idle-in-transaction reader on a fa_* table)
    fails fast and is skipped instead of wedging the request thread. The fa_*
    tables already exist on provisioned PostgreSQL installs, so a skipped
    statement is safe. Without this guard, ``/academy/*`` pages hung
    indefinitely on first request whenever the table lock was contended.
    """
    conn = get_connection()
    _set_lock_timeout(conn, "3s")
    try:
        for stmt in _DDL.strip().split(";\n\n"):
            stmt = stmt.strip()
            if not stmt:
                continue
            try:
                conn.execute(stmt)
                conn.commit()
            except Exception as exc:
                try:
                    conn.rollback()
                except Exception:
                    pass
                _log.debug("FORGE Academy DDL skipped: %s", exc)
        # Safe column additions for existing installs
        for col_ddl in [
            "ALTER TABLE fa_missions ADD COLUMN status TEXT NOT NULL DEFAULT 'active'",
            "ALTER TABLE fa_missions ADD COLUMN updated_at TEXT",
            "ALTER TABLE fa_leaderboard_cache ADD COLUMN tenant_id TEXT",
            # aca-trn-04: guilds were the one cross-learner object with no tenant
            # column, so an invite code from one tenant was joinable from another.
            "ALTER TABLE fa_guilds ADD COLUMN tenant_id TEXT",
            # aca-trn-02 / migration 328 — steps carry a competency class too, so a
            # certificate can cite the specific submissions behind a claim.
            "ALTER TABLE fa_step_ontology ADD COLUMN competency_class TEXT",
        ]:
            try:
                conn.execute(col_ddl)
                conn.commit()
            except Exception:
                try:
                    conn.rollback()
                except Exception:
                    pass  # Column already exists
        _seed_achievements(conn)
        _seed_skill_nodes(conn)
        # NOTE: seed_mission_ontology_mappings() is deliberately NOT called here.
        # migrate() runs BEFORE seed_mission_catalog(), so on a fresh install
        # fa_missions is still empty at this point and every mapping lookup found
        # nothing. That ordering is why 35 of 124 missions and 122 of 212 steps
        # carried no ontology row in production (aca-trn-02). The caller seeds the
        # catalog first and then maps it — see blueprint._ensure_init.
    finally:
        _set_lock_timeout(conn, "0")  # restore PG default (no lock timeout)


def _seed_achievements(conn):
    for a in ACHIEVEMENTS:
        try:
            conn.execute(
                "INSERT OR IGNORE INTO fa_achievements "
                "(slug,title,description,icon,xp_bonus,rarity,criteria_json) "
                "VALUES (%s,%s,%s,%s,%s,%s,%s)",
                (a["slug"], a["title"], a["description"], a["icon"],
                 a["xp_bonus"], a["rarity"], a["criteria_json"]),
            )
        except Exception as exc:  # noqa: BLE001 - best-effort persistence; logged, never raised
            _log.warning("_seed_achievements: best-effort INSERT into fa_achievements failed (non-blocking): %s", exc)
    conn.commit()


def _seed_skill_nodes(conn):
    for n in SKILL_NODES:
        pos = n.get("pos", (0, 0))
        try:
            conn.execute(
                "INSERT OR IGNORE INTO fa_skill_nodes "
                "(slug,title,tier,role_filter,prereq_ids_json,pos_x,pos_y) "
                "VALUES (%s,%s,%s,%s,%s,%s,%s)",
                (n["slug"], n["title"], n["tier"], n["role_filter"],
                 json.dumps(n.get("prereqs", [])), pos[0], pos[1]),
            )
        except Exception as exc:  # noqa: BLE001 - best-effort persistence; logged, never raised
            _log.warning("_seed_skill_nodes: best-effort INSERT into fa_skill_nodes failed (non-blocking): %s", exc)
    conn.commit()


# ---------------------------------------------------------------------------
# User CRUD
# ---------------------------------------------------------------------------

def get_or_create_user(username: str, display_name: str = "", email: str = "", tenant_id: str | None = None) -> dict:
    conn = get_connection()
    if tenant_id:
        row = conn.execute(
            "SELECT * FROM fa_users WHERE username=%s AND tenant_id=%s", (username, tenant_id)
        ).fetchone()
    else:
        row = conn.execute(
            "SELECT * FROM fa_users WHERE username=%s AND (tenant_id IS NULL OR tenant_id='')", (username,)
        ).fetchone()
    if row:
        _touch_streak(conn, dict(row))
        if tenant_id:
            return dict(conn.execute("SELECT * FROM fa_users WHERE username=%s AND tenant_id=%s", (username, tenant_id)).fetchone())
        return dict(conn.execute("SELECT * FROM fa_users WHERE username=%s AND (tenant_id IS NULL OR tenant_id='')", (username,)).fetchone())
    conn.execute(
        "INSERT INTO fa_users (username, display_name, email, last_active, tenant_id) VALUES (%s,%s,%s,%s,%s)",
        (username, display_name or username, email,
         datetime.now(timezone.utc).isoformat(), tenant_id),
    )
    conn.commit()
    if tenant_id:
        return dict(conn.execute("SELECT * FROM fa_users WHERE username=%s AND tenant_id=%s", (username, tenant_id)).fetchone())
    return dict(conn.execute("SELECT * FROM fa_users WHERE username=%s AND (tenant_id IS NULL OR tenant_id='')", (username,)).fetchone())


def active_challenge_count() -> int:
    """How many challenges are currently running.

    fa_challenges has never had an INSERT anywhere in the repo — no seeder, no
    admin-create route — so the Arena has always rendered "No Active
    Challenges" and the entry API was unreachable (fga-fix-04). This lets the
    nav hide a feature that cannot work rather than advertising a dead end.
    Returns 0 on any error: a nav link is not worth an exception.
    """
    try:
        row = get_connection().execute(
            "SELECT COUNT(*) FROM fa_challenges WHERE ends_at > datetime('now')"
        ).fetchone()
        return int(row[0]) if row else 0
    except Exception as exc:  # noqa: BLE001
        _log.debug("active_challenge_count failed: %s", exc)
        return 0


def update_user_display_name(user_id: int, display_name: str) -> None:
    """Persist a display name for an EXISTING user.

    get_or_create_user() only sets display_name on INSERT, so a returning user
    who changed their name had it silently discarded by the setup route
    (fga-fix-03). Blank input is ignored rather than wiping the stored name.
    """
    display_name = (display_name or "").strip()
    if not display_name:
        return
    conn = get_connection()
    conn.execute(
        "UPDATE fa_users SET display_name=%s WHERE id=%s", (display_name, user_id)
    )
    conn.commit()


def update_user_role(user_id: int, role: str) -> None:
    from .constants import ROLES
    role_type = ROLES.get(role, {}).get("type", "guided")
    conn = get_connection()
    conn.execute(
        "UPDATE fa_users SET role=%s, role_type=%s WHERE id=%s",
        (role, role_type, user_id),
    )
    conn.commit()


# aca-int-07: every reason an award can exist. A value outside this set is a bug in
# the caller, not a new category — adding one means deciding whether it counts toward
# rank, which is exactly the decision this set exists to force.
XP_REASONS = frozenset({
    "step_pass", "mission_complete", "daily_login", "achievement",
    "certificate", "opening_balance", "adjustment",
})

# XP for showing up rather than for demonstrating anything. Kept in the ledger so the
# total still reconciles to fa_users.xp, but excluded from rank.
ATTENDANCE_REASONS = frozenset({"daily_login"})


def earned_xp(user_id: int, conn=None) -> int:
    """XP from demonstrated work — the basis for rank.

    Measured before this existed: the live learner held 1715 XP of which 1465 was
    attendance across 41 logins, so 85% of the rank was showing up and logging in for
    41 days outranked demonstrating anything.
    """
    own = conn is None
    if own:
        conn = get_connection()
    if own:
        # Only swallow on a connection we own. On PostgreSQL a failed statement
        # ABORTS the whole transaction, so catching the error on a CALLER's
        # connection leaves it poisoned: every later statement fails with
        # "current transaction is aborted" and an enclosing commit hangs on the
        # locks it is still holding. That is a deadlock introduced by an
        # error handler, and it is why the table is also declared in the DDL above.
        try:
            return _earned_xp(conn, user_id)
        except Exception:
            return 0
    return _earned_xp(conn, user_id)


def _earned_xp(conn, user_id: int) -> int:
    row = conn.execute(
        "SELECT COALESCE(SUM(xp_delta), 0) AS earned FROM fa_xp_ledger "
        "WHERE user_id=%s AND is_attendance=0",
        (user_id,),
    ).fetchone()
    if row is None:
        return 0
    earned = row["earned"] if not isinstance(row, tuple) else row[0]
    return int(earned or 0)


def record_xp(user_id: int, xp_delta: int, *, reason: str,
              source_type: str | None = None, source_id: int | None = None,
              note: str | None = None, conn=None) -> None:
    """Append one immutable row describing an award.

    Separate from update_user_xp so the ledger write and the balance update share a
    connection and therefore a transaction: a balance that moved without a ledger row
    is the exact failure this card exists to prevent.
    """
    if reason not in XP_REASONS:
        raise ValueError(
            f"unknown XP reason {reason!r}; add it to XP_REASONS and decide "
            f"whether it counts toward rank"
        )
    own = conn is None
    if own:
        conn = get_connection()
    conn.execute(
        "INSERT INTO fa_xp_ledger "
        "(user_id, xp_delta, reason, source_type, source_id, is_attendance, "
        " verified, note) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)",
        (user_id, int(xp_delta), reason, source_type, source_id,
         1 if reason in ATTENDANCE_REASONS else 0, 1, note),
    )
    if own:
        conn.commit()


def update_user_xp(user_id: int, xp_delta: int, conn=None, *,
                   reason: str,
                   source_type: str | None = None,
                   source_id: int | None = None,
                   note: str | None = None) -> dict:
    # aca-int-07: `reason` is keyword-only and has NO default on purpose. A default
    # would let a new award slip in unattributed and still look correct — which is
    # precisely how fa_users.xp accumulated 1715 points that no record explains.
    # Without one this is a TypeError at the call site, at import time of the test
    # suite, rather than a silent hole discovered months later.
    # penta-fix-03: accept an optional caller-supplied connection so an enclosing
    # transaction (e.g. issue_certificate's cert INSERT) can award XP on the SAME
    # connection instead of opening a second one. Opening a second connection while
    # the first transaction is still uncommitted self-deadlocks the SQLite backend
    # (the uncommitted writer holds the DB write lock). When conn is supplied the
    # caller owns the commit; otherwise we manage our own transaction as before.
    own_conn = conn is None
    if own_conn:
        conn = get_connection()
    conn.execute("UPDATE fa_users SET xp = xp + %s WHERE id=%s", (xp_delta, user_id))
    # Same connection, therefore the same transaction as the balance change. A
    # balance that moved without its ledger row is the exact failure this card
    # exists to prevent, so the two must not be able to diverge.
    record_xp(user_id, xp_delta, reason=reason, source_type=source_type,
              source_id=source_id, note=note, conn=conn)
    row = conn.execute("SELECT xp FROM fa_users WHERE id=%s", (user_id,)).fetchone()
    new_xp = row["xp"]
    # aca-int-07: rank is computed from EARNED XP, not the running total. Attendance
    # still accumulates and still shows on the profile — it just no longer buys a
    # rank. On the live learner this is a visible demotion, and it should be: 1465 of
    # their 1715 points were logins.
    earned = earned_xp(user_id, conn=conn)
    new_level = xp_to_level(earned)["slug"]
    conn.execute("UPDATE fa_users SET level=%s WHERE id=%s", (new_level, user_id))
    if own_conn:
        conn.commit()
    return {"xp": new_xp, "earned_xp": earned, "level": new_level}


def _touch_streak(conn, user: dict) -> None:
    """Advance or reset the login streak. One clock only.

    aca-hyg-04: `today` came from datetime.now(timezone.utc) while `yesterday` came
    from date.today(), which is LOCAL. Whenever the machine's local date differs from
    the UTC date — several hours of every day for most timezones — `last ==
    yesterday` was false and the streak reset to 1. It therefore never advanced past
    1 in production: the one learner had 41 daily logins over 41 distinct days with
    35 consecutive-day pairs, including the final six unbroken, and streak_days = 1.
    Every streak bonus ever paid was min(1,7)*10 instead of up to 70.

    last_active is stored in UTC, so both sides of the comparison are now UTC.
    """
    from datetime import timedelta

    now_utc = datetime.now(timezone.utc)
    today = now_utc.date().isoformat()
    last = (user.get("last_active") or "")[:10]
    if last == today:
        return
    streak = int(user.get("streak_days") or 0)
    yesterday = (now_utc.date() - timedelta(days=1)).isoformat()
    streak = (streak + 1) if last == yesterday else 1
    conn.execute(
        "UPDATE fa_users SET streak_days=%s, last_active=%s WHERE id=%s",
        (streak, datetime.now(timezone.utc).isoformat(), user["id"]),
    )
    conn.commit()


def get_user(user_id: int, tenant_id: str | None = None) -> dict | None:
    conn = get_connection()
    if tenant_id is not None:
        row = conn.execute(
            "SELECT * FROM fa_users WHERE id=%s AND tenant_id=%s", (user_id, tenant_id)
        ).fetchone()
    else:
        row = conn.execute(
            "SELECT * FROM fa_users WHERE id=%s", (user_id,)
        ).fetchone()
    return dict(row) if row else None


def get_user_by_username(username: str, tenant_id: str | None = None) -> dict | None:
    conn = get_connection()
    if tenant_id is not None:
        row = conn.execute(
            "SELECT * FROM fa_users WHERE username=%s AND tenant_id=%s", (username, tenant_id)
        ).fetchone()
    else:
        row = conn.execute(
            "SELECT * FROM fa_users WHERE username=%s", (username,)
        ).fetchone()
    return dict(row) if row else None


# ---------------------------------------------------------------------------
# Mission CRUD
# ---------------------------------------------------------------------------

def role_matches(role_filter: str | None, role: str) -> bool:
    """Whether a mission's ``role_filter`` covers ``role``, matching whole tokens.

    aca-hyg-02: fa_missions.role_filter is a comma-joined TEXT column ('swe,swe_arch',
    'secops_eng,isso,swe_arch', ...). check_cert_eligibility matched it with
    ``role_filter LIKE '%swe%'``, so 'swe' also matched every 'swe_arch' mission. On
    the live catalogue that inflated a plain SWE's Tier-2 certificate denominator from
    25 missions to 37 — twelve architect-only missions counted against them, so 100%
    required work aimed at a different role.

    'swe' -> 'swe_arch' is the only collision among the 17 role tokens in use, which
    is why it survived: it is invisible unless you enumerate them.

    list_missions already compared whole tokens in Python. This is that comparison,
    extracted so the two call sites cannot drift apart again — and it stays in Python
    rather than SQL per the repo rule preferring computed filters over dialect-specific
    string matching.
    """
    text = (role_filter or "").strip()
    if not text or text == "all":
        return True
    if not role:
        return False
    return role in {tok.strip() for tok in text.split(",") if tok.strip()}


def list_missions(tier: int = None, role: str = None,
                  mission_type: str = None, tenant_id: str | None = None) -> list[dict]:
    conn = get_connection()
    q = "SELECT * FROM fa_missions WHERE is_active=1"
    params = []
    if tier:
        q += " AND tier=%s"
        params.append(tier)
    if mission_type:
        q += " AND mission_type=%s"
        params.append(mission_type)
    if tenant_id is not None:
        q += " AND tenant_id=%s"
        params.append(tenant_id)
    q += " ORDER BY tier, order_idx"
    rows = conn.execute(q, params).fetchall()
    missions = [dict(r) for r in rows]

    # is_available: does this mission have any steps? A catalogue card leading
    # to "No steps found for this mission" is a dead end the student cannot
    # tell apart from a playable one until they click it (fga-wire-06). Ten
    # missions have no content on disk at all; they are catalogued deliberately,
    # so mark them rather than hide them.
    try:
        counts = {
            r[0]: r[1] for r in conn.execute(
                "SELECT mission_id, COUNT(*) FROM fa_mission_steps GROUP BY mission_id"
            ).fetchall()
        }
    except Exception as exc:  # noqa: BLE001 — a badge is not worth a 500
        _log.debug("list_missions: step counts unavailable: %s", exc)
        counts = {}
    for m in missions:
        m["step_count"] = int(counts.get(m.get("id"), 0))
        m["is_available"] = m["step_count"] > 0

    if role:
        # Whole-token match via the shared helper. This was already correct here and
        # wrong in check_cert_eligibility; one definition is what stops them drifting
        # apart again (aca-hyg-02). Note the helper also trims whitespace, which the
        # previous inline split did not.
        missions = [m for m in missions if role_matches(m.get("role_filter"), role)]
    return missions


def get_mission(slug: str) -> dict | None:
    row = get_connection().execute(
        "SELECT * FROM fa_missions WHERE slug=%s", (slug,)
    ).fetchone()
    return dict(row) if row else None


def get_mission_by_id(mission_id: int) -> dict | None:
    """Look a mission up by id.

    The submit route derives the mission from the step row rather than trusting a
    client-supplied slug (aca-int-01), so it needs an id-keyed lookup.
    """
    row = get_connection().execute(
        "SELECT * FROM fa_missions WHERE id=%s", (mission_id,)
    ).fetchone()
    return dict(row) if row else None


def get_mission_steps(mission_id: int) -> list[dict]:
    rows = get_connection().execute(
        "SELECT * FROM fa_mission_steps WHERE mission_id=%s ORDER BY step_num",
        (mission_id,),
    ).fetchall()
    return [dict(r) for r in rows]


def upsert_mission(data: dict) -> int:
    conn = get_connection()
    conn.execute(
        """INSERT INTO fa_missions
           (slug,title,tagline,tier,topic,role_filter,mission_type,xp_reward,
            prereq_slugs_json,order_idx,difficulty,estimated_minutes,source_credit,
            learning_objective)
           VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
           ON CONFLICT(slug) DO UPDATE SET
             title=excluded.title, tagline=excluded.tagline,
             xp_reward=excluded.xp_reward, order_idx=excluded.order_idx,
             learning_objective=COALESCE(excluded.learning_objective,
                                         fa_missions.learning_objective)""",
        (data["slug"], data["title"], data.get("tagline", ""),
         data.get("tier", 1), data.get("topic", ""), data.get("role_filter", "all"),
         data.get("mission_type", "coding"), data.get("xp_reward", 200),
         json.dumps(data.get("prereqs", [])), data.get("order_idx", 0),
         data.get("difficulty", "intermediate"), data.get("estimated_minutes", 30),
         data.get("source_credit", ""),
         # aca-trn-03: NULL, not "", so an unstated objective is one state.
         (data.get("learning_objective") or None)),
    )
    conn.commit()
    row = conn.execute("SELECT id FROM fa_missions WHERE slug=%s", (data["slug"],)).fetchone()
    return row["id"]


# ---------------------------------------------------------------------------
# Progress CRUD
# ---------------------------------------------------------------------------

def get_mission_progress(user_id: int, mission_id: int, tenant_id: str | None = None) -> dict:
    conn = get_connection()
    # Verify user belongs to tenant before returning progress
    if tenant_id is not None:
        user_row = conn.execute(
            "SELECT id FROM fa_users WHERE id=%s AND tenant_id=%s", (user_id, tenant_id)
        ).fetchone()
        if not user_row:
            return {"status": "not_started", "xp_earned": 0, "attempts": 0, "score": 0}
    row = conn.execute(
        "SELECT * FROM fa_mission_progress WHERE user_id=%s AND mission_id=%s",
        (user_id, mission_id),
    ).fetchone()
    if row:
        return dict(row)
    return {"status": "not_started", "xp_earned": 0, "attempts": 0, "score": 0}


def record_mission_attempt(user_id: int, mission_id: int) -> None:
    """Record that the learner submitted work against this mission.

    aca-int-04: this was `start_mission` and the mission GET handler called it on
    every page load, with `SET status='in_progress', attempts=attempts+1` applied
    unconditionally. Two consequences:

      * `attempts` counted page views. Production showed 39 rows in_progress with
        352 attempts while fa_step_progress was entirely empty — every one of
        those "attempts" was somebody opening a page.
      * revisiting a COMPLETED mission reverted it to in_progress, withdrawing a
        completion that check_cert_eligibility counts. Certificate eligibility
        oscillated with browsing.

    It is now called from the submit path only, so progress is a consequence of
    work, and a completed mission is never moved backwards — a learner must be
    able to reopen and tinker with something they have already passed.
    """
    conn = get_connection()
    now = datetime.now(timezone.utc).isoformat()
    existing = conn.execute(
        "SELECT id, status FROM fa_mission_progress WHERE user_id=%s AND mission_id=%s",
        (user_id, mission_id),
    ).fetchone()
    if existing:
        status = existing["status"] if hasattr(existing, "keys") else existing[1]
        if status == MISSION_STATUS_COMPLETED:
            # Count the attempt, keep the completion.
            conn.execute(
                "UPDATE fa_mission_progress SET attempts=attempts+1 "
                "WHERE user_id=%s AND mission_id=%s",
                (user_id, mission_id),
            )
        else:
            conn.execute(
                "UPDATE fa_mission_progress SET status=%s, attempts=attempts+1 "
                "WHERE user_id=%s AND mission_id=%s",
                (MISSION_STATUS_IN_PROGRESS, user_id, mission_id),
            )
    else:
        conn.execute(
            "INSERT INTO fa_mission_progress (user_id,mission_id,status,attempts,started_at) "
            "VALUES (%s,%s,%s,1,%s)",
            (user_id, mission_id, MISSION_STATUS_IN_PROGRESS, now),
        )
    conn.commit()


def complete_mission(user_id: int, mission_id: int, score: int = 100) -> None:
    conn = get_connection()
    now = datetime.now(timezone.utc).isoformat()
    existing = conn.execute(
        "SELECT id FROM fa_mission_progress WHERE user_id=%s AND mission_id=%s",
        (user_id, mission_id),
    ).fetchone()
    if existing:
        conn.execute(
            "UPDATE fa_mission_progress SET status='completed', score=%s, completed_at=%s "
            "WHERE user_id=%s AND mission_id=%s",
            (score, now, user_id, mission_id),
        )
    else:
        conn.execute(
            "INSERT INTO fa_mission_progress (user_id,mission_id,status,score,completed_at) "
            "VALUES (%s,%s,'completed',%s,%s)",
            (user_id, mission_id, score, now),
        )
    conn.commit()


def get_step_progress(user_id: int, step_id: int) -> dict:
    row = get_connection().execute(
        "SELECT * FROM fa_step_progress WHERE user_id=%s AND step_id=%s",
        (user_id, step_id),
    ).fetchone()
    return dict(row) if row else {"status": "not_started", "hints_used": 0, "score": 0}


def record_step_attempt(user_id: int, step_id: int, submission: str = "",
                        passed: bool = True, hints_used: int = 0,
                        score: int | None = None) -> str:
    """Record a submission against a step. Returns the resulting status.

    aca-int-05: this used to hardcode status='completed' in BOTH branches, so a
    FAILED submission was filed as a completed step (only `score` recorded the
    failure) and `steps_completed` counted it. A failure is now stored as
    STEP_STATUS_ATTEMPTED, and only a pass sets completed_at.

    aca-trn-01: `score` used to be `100 if passed else 0` unconditionally — a step
    could not express "2 of 3 items correct". An item-scored step now passes its real
    percentage; callers with nothing better to say leave it None and get the old
    binary, which is still correct for a coding step (the runner reports one boolean
    for the whole suite, see CODING_PASS_THRESHOLD_PCT).

    Mastery is never withdrawn: once a step is completed, a later failed
    experiment records the submission but does not downgrade the status or the
    score. Learners must be able to keep tinkering after they have passed.
    """
    conn = get_connection()
    now = datetime.now(timezone.utc).isoformat()
    status = STEP_STATUS_COMPLETED if passed else STEP_STATUS_ATTEMPTED
    score = (100 if passed else 0) if score is None else int(score)
    existing = conn.execute(
        "SELECT id, status, score FROM fa_step_progress WHERE user_id=%s AND step_id=%s",
        (user_id, step_id),
    ).fetchone()
    if existing:
        already_done = (existing["status"] if hasattr(existing, "keys") else existing[1])
        if already_done == STEP_STATUS_COMPLETED and not passed:
            # Keep the pass; still record what was tried.
            conn.execute(
                "UPDATE fa_step_progress SET submission=%s, hints_used=%s "
                "WHERE user_id=%s AND step_id=%s",
                (submission, hints_used, user_id, step_id),
            )
            conn.commit()
            return STEP_STATUS_COMPLETED
        if already_done == STEP_STATUS_COMPLETED:
            # aca-trn-01: a real percentage makes it possible to PASS a step you have
            # already passed with a lower score — 100% on the first attempt, then 70%
            # on a later practice run. Keeping the best score is the same
            # "mastery is never withdrawn" rule the branch above applies to status.
            prior = (existing["score"] if hasattr(existing, "keys") else existing[2]) or 0
            score = max(int(prior), score)
        conn.execute(
            "UPDATE fa_step_progress SET status=%s, submission=%s, score=%s, "
            "hints_used=%s, completed_at=%s WHERE user_id=%s AND step_id=%s",
            (status, submission, score, hints_used, now if passed else None,
             user_id, step_id),
        )
    else:
        conn.execute(
            "INSERT INTO fa_step_progress "
            "(user_id,step_id,status,submission,score,hints_used,completed_at) "
            "VALUES (%s,%s,%s,%s,%s,%s,%s)",
            (user_id, step_id, status, submission, score, hints_used,
             now if passed else None),
        )
    conn.commit()
    return status


# Back-compat alias: `complete_step` is the historical name and is still what the
# blueprint and the existing tests import. It no longer implies completion — the
# `passed` argument decides — so new call sites should prefer
# `record_step_attempt`, which says what it does.
complete_step = record_step_attempt


def tier_progress(user_id: int) -> dict:
    """Per-tier completion and unlock state for a learner.

    Returns ``{tier: {total, completable, completed, pct, unlocked, required_pct,
    gating_tier}}``.

    aca-ux-04: fa_users.tier_unlocked was set to 1 for everyone and enforced
    nowhere — all 104 Tier-2 missions were listed and openable while the hub showed
    a "TIER 1" tile implying a gate that did not exist. This computes the gate from
    recorded completions instead of trusting a stored column that nothing maintained.

    `completable` excludes zero-step missions. That is load-bearing: Tier 1 contains
    a mission with no steps by design, so counting it would put an impossible row in
    the denominator and lock Tier 2 forever. `total` is still reported so the UI can
    explain the difference.
    """
    from .constants import TIER_UNLOCK_PCT

    conn = get_connection()
    rows = conn.execute(
        "SELECT m.tier, "
        " COUNT(*) AS total, "
        " SUM(CASE WHEN (SELECT COUNT(*) FROM fa_mission_steps s "
        "               WHERE s.mission_id=m.id) > 0 THEN 1 ELSE 0 END) AS completable "
        "FROM fa_missions m WHERE m.is_active=1 GROUP BY m.tier"
    ).fetchall()

    done_rows = conn.execute(
        "SELECT m.tier, COUNT(DISTINCT mp.mission_id) "
        "FROM fa_mission_progress mp JOIN fa_missions m ON m.id=mp.mission_id "
        "WHERE mp.user_id=%s AND mp.status=%s AND m.is_active=1 "
        "  AND (SELECT COUNT(*) FROM fa_mission_steps s WHERE s.mission_id=m.id) > 0 "
        "GROUP BY m.tier",
        (user_id, MISSION_STATUS_COMPLETED),
    ).fetchall()
    done = {int(r[0]): int(r[1]) for r in done_rows}

    out: dict = {}
    for r in rows:
        tier = int(r["tier"] if hasattr(r, "keys") else r[0])
        total = int(r["total"] if hasattr(r, "keys") else r[1])
        completable = int((r["completable"] if hasattr(r, "keys") else r[2]) or 0)
        completed = done.get(tier, 0)
        pct = int(round(completed * 100 / completable)) if completable else 0
        out[tier] = {
            "total": total,
            "completable": completable,
            "completed": completed,
            "pct": pct,
            "required_pct": TIER_UNLOCK_PCT.get(tier),
            "gating_tier": tier - 1 if tier in TIER_UNLOCK_PCT else None,
            "unlocked": True,  # resolved below, once every tier's pct is known
        }

    for tier, info in out.items():
        required = info["required_pct"]
        if required is None:
            info["unlocked"] = True  # entry tier
            continue
        prior = out.get(tier - 1)
        if not prior or not prior["completable"]:
            # Nothing completable to gate on — an empty prior tier must not become a
            # permanent lock on everything after it.
            info["unlocked"] = True
        else:
            info["unlocked"] = prior["pct"] >= required
    return out


def is_tier_unlocked(user_id: int, tier: int) -> bool:
    """Whether `tier` is unlocked for this learner. Unknown tiers are open."""
    try:
        info = tier_progress(user_id).get(int(tier))
    except Exception:
        _log.exception("tier gate lookup failed for user %s tier %s", user_id, tier)
        return True  # never lock a learner out because of a query failure
    if not info:
        return True
    return bool(info["unlocked"])


def mission_step_progress(user_id: int, mission_ids) -> dict:
    """``{mission_id: {"done": n, "total": n}}`` for the given missions.

    aca-ux-03: the hub and the browser showed a Done/Active/Start badge and nothing
    else, so a learner could not see how far into a mission they were without
    opening it.
    """
    ids = [int(m) for m in (mission_ids or [])]
    if not ids:
        return {}
    conn = get_connection()
    placeholders = ",".join(["%s"] * len(ids))
    totals = {
        int(r[0]): int(r[1])
        for r in conn.execute(
            f"SELECT mission_id, COUNT(*) FROM fa_mission_steps "  # noqa: S608
            f"WHERE mission_id IN ({placeholders}) GROUP BY mission_id",
            ids,
        ).fetchall()
    }
    done = {
        int(r[0]): int(r[1])
        for r in conn.execute(
            f"SELECT s.mission_id, COUNT(*) FROM fa_step_progress sp "  # noqa: S608
            f"JOIN fa_mission_steps s ON s.id=sp.step_id "
            f"WHERE sp.user_id=%s AND sp.status=%s AND s.mission_id IN ({placeholders}) "
            f"GROUP BY s.mission_id",
            [user_id, STEP_STATUS_COMPLETED, *ids],
        ).fetchall()
    }
    return {
        mid: {"done": done.get(mid, 0), "total": totals.get(mid, 0)} for mid in ids
    }


def mission_prereq_state(user_id: int, missions) -> dict:
    """``{mission_id: {"prereqs": [...], "unmet": n, "ready": bool}}``.

    aca-ux-06: 86 of 122 active missions declare prereq_slugs_json and nothing
    rendered it, so a learner facing a 122-mission catalogue had no visible ordering
    — the card vocabulary was Done / Active / Start and nothing else.

    Each prerequisite carries its TITLE, because a slug is not something to show a
    learner, and falls back to the slug when the mission is unknown so a future typo
    degrades instead of raising. Two queries regardless of catalogue size; the browser
    renders every card on one page and this must not be N+1.
    """
    entries = list(missions or [])
    if not entries:
        return {}
    conn = get_connection()
    titles = {
        (r["slug"] if hasattr(r, "keys") else r[0]): (r["title"] if hasattr(r, "keys") else r[1])
        for r in conn.execute("SELECT slug, title FROM fa_missions").fetchall()
    }
    completed = {
        (r[0] if not hasattr(r, "keys") else r["slug"])
        for r in conn.execute(
            "SELECT m.slug FROM fa_mission_progress mp "
            "JOIN fa_missions m ON m.id = mp.mission_id "
            "WHERE mp.user_id=%s AND mp.status=%s",
            (user_id, MISSION_STATUS_COMPLETED),
        ).fetchall()
    }

    out: dict = {}
    for mission in entries:
        raw = mission.get("prereq_slugs_json") or "[]"
        try:
            slugs = json.loads(raw) if isinstance(raw, str) else list(raw or [])
        except (TypeError, ValueError):
            _log.warning(
                "mission %s has unparseable prereq_slugs_json", mission.get("slug")
            )
            slugs = []
        prereqs = [
            {
                "slug": s,
                "title": titles.get(s, s),
                "satisfied": s in completed,
            }
            for s in slugs
            if s
        ]
        unmet = sum(1 for p in prereqs if not p["satisfied"])
        out[mission["id"]] = {
            "prereqs": prereqs,
            "unmet": unmet,
            "ready": unmet == 0,
        }
    return out


def resume_target(user_id: int) -> dict | None:
    """The mission to offer as "continue where you left off", or None.

    Deliberately requires EVIDENCE of work, not just a progress row. Before
    aca-int-04, opening a mission page created an in_progress row — 39 of them, with
    352 attempts and not one step submission — so a resume control keyed on status
    alone would have pointed at missions the learner had merely glanced at. A mission
    with no fa_step_progress rows is therefore never offered.

    Recency is the learner's own last activity on that mission, falling back to when
    the mission was started so an attempt that has not completed anything still
    ranks.
    """
    conn = get_connection()
    row = conn.execute(
        "SELECT m.id, m.slug, m.title, m.tier, "
        "       MAX(COALESCE(sp.completed_at, sp.started_at, mp.started_at)) AS last_at "
        "FROM fa_mission_progress mp "
        "JOIN fa_missions m ON m.id = mp.mission_id "
        "JOIN fa_mission_steps s ON s.mission_id = m.id "
        "JOIN fa_step_progress sp ON sp.step_id = s.id AND sp.user_id = mp.user_id "
        "WHERE mp.user_id = %s AND mp.status <> %s AND m.is_active = 1 "
        "GROUP BY m.id, m.slug, m.title, m.tier "
        "ORDER BY last_at DESC, m.id DESC LIMIT 1",
        (user_id, MISSION_STATUS_COMPLETED),
    ).fetchone()
    if not row:
        return None
    mid = row["id"] if hasattr(row, "keys") else row[0]
    counts = mission_step_progress(user_id, [mid]).get(mid, {"done": 0, "total": 0})
    return {
        "id": mid,
        "slug": row["slug"] if hasattr(row, "keys") else row[1],
        "title": row["title"] if hasattr(row, "keys") else row[2],
        "tier": row["tier"] if hasattr(row, "keys") else row[3],
        "steps_done": counts["done"],
        "steps_total": counts["total"],
    }


def record_hint(user_id: int, step_id: int) -> int:
    """Count a hint against a step and return the new total.

    aca-int-06: `hintsUsed` used to live only in the browser, and goStep() reset it
    to 0 on every sidebar click - so three hints followed by a click away and back
    erased the penalty entirely, restoring both the 1.5x "perfect" mission
    multiplier and the no_hints_needed achievement. A counter the learner's own
    navigation can zero is not a counter.

    Creates the progress row if the learner has not submitted anything yet, and
    leaves status/score untouched so recording a hint can never disturb a step that
    is already completed.
    """
    conn = get_connection()
    existing = conn.execute(
        "SELECT id FROM fa_step_progress WHERE user_id=%s AND step_id=%s",
        (user_id, step_id),
    ).fetchone()
    if existing:
        conn.execute(
            "UPDATE fa_step_progress SET hints_used=hints_used+1 "
            "WHERE user_id=%s AND step_id=%s",
            (user_id, step_id),
        )
    else:
        conn.execute(
            "INSERT INTO fa_step_progress (user_id, step_id, status, hints_used) "
            "VALUES (%s,%s,%s,1)",
            (user_id, step_id, STEP_STATUS_NOT_STARTED),
        )
    conn.commit()
    row = conn.execute(
        "SELECT hints_used FROM fa_step_progress WHERE user_id=%s AND step_id=%s",
        (user_id, step_id),
    ).fetchone()
    if not row:
        return 0
    return int((row["hints_used"] if hasattr(row, "keys") else row[0]) or 0)


def user_progress_summary(user_id: int, tenant_id: str | None = None) -> dict:
    conn = get_connection()
    # Verify user belongs to tenant
    if tenant_id is not None:
        user_row = conn.execute(
            "SELECT id FROM fa_users WHERE id=%s AND tenant_id=%s", (user_id, tenant_id)
        ).fetchone()
        if not user_row:
            return {"total_missions": 0, "completed": 0, "steps_completed": 0, "in_progress": None}
    total = conn.execute("SELECT COUNT(*) FROM fa_missions WHERE is_active=1").fetchone()[0]
    done = conn.execute(
        "SELECT COUNT(*) FROM fa_mission_progress WHERE user_id=%s AND status='completed'",
        (user_id,),
    ).fetchone()[0]
    steps_done = conn.execute(
        "SELECT COUNT(*) FROM fa_step_progress WHERE user_id=%s AND status='completed'",
        (user_id,),
    ).fetchone()[0]
    in_prog = conn.execute(
        """SELECT m.slug, m.title, m.tier, mp.attempts
           FROM fa_mission_progress mp
           JOIN fa_missions m ON m.id=mp.mission_id
           WHERE mp.user_id=%s AND mp.status='in_progress'
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
           WHERE ua.user_id=%s
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
           WHERE us.user_id = %s""",
        (user_id,),
    ).fetchall()
    return {r["slug"] for r in rows}


def unlock_skill(user_id: int, skill_slug: str) -> bool:
    """Unlock a skill node for the user. Returns True if newly unlocked."""
    conn = get_connection()
    node = conn.execute(
        "SELECT id FROM fa_skill_nodes WHERE slug = %s", (skill_slug,)
    ).fetchone()
    if not node:
        return False
    try:
        conn.execute(
            "INSERT INTO fa_user_skills (user_id, skill_id) VALUES (%s, %s)",
            (user_id, node["id"]),
        )
        conn.commit()
        return True
    except Exception:
        return False


def grant_achievement(user_id: int, slug: str) -> dict | None:
    conn = get_connection()
    ach = conn.execute(
        "SELECT * FROM fa_achievements WHERE slug=%s", (slug,)
    ).fetchone()
    if not ach:
        return None
    try:
        conn.execute(
            "INSERT INTO fa_user_achievements (user_id,achievement_id) VALUES (%s,%s)",
            (user_id, ach["id"]),
        )
        conn.commit()
        return dict(ach)
    except Exception:
        return None  # already earned


# ---------------------------------------------------------------------------
# Guilds
# ---------------------------------------------------------------------------

def create_guild(
    name: str, description: str, created_by: int, invite_code: str | None = None,
    tenant_id: str | None = None,
) -> dict:
    """Create a guild and make ``created_by`` its leader.

    ``invite_code`` is accepted so the caller can mint the code it shows the
    user. The route already generated one and passed it, which raised TypeError
    on every request because this signature did not take it (fga-fix-01) — and
    had the signature matched, the route would have shown the user its own code
    while the row stored a different one, so the invite would never resolve.

    Codes are stored uppercased because ``join_guild`` uppercases before
    lookup; a lowercase code would be unjoinable.

    aca-trn-04: ``tenant_id`` is stamped on the guild so ``join_guild`` can
    refuse a cross-tenant join. Before this the invite code was the only key,
    and it is global — a code leaked out of one tenant admitted a learner from
    another straight into ``get_guild_stats``, which returns every member's name
    and XP.
    """
    conn = get_connection()
    code = (invite_code or secrets.token_urlsafe(6)).upper()
    conn.execute(
        "INSERT INTO fa_guilds (name,description,invite_code,created_by,tenant_id) "
        "VALUES (%s,%s,%s,%s,%s)",
        (name, description, code, created_by, tenant_id),
    )
    conn.commit()
    row = conn.execute(
        "SELECT * FROM fa_guilds WHERE invite_code=%s", (code,)
    ).fetchone()
    guild = dict(row)
    conn.execute(
        "INSERT INTO fa_guild_members (guild_id,user_id,role) VALUES (%s,%s,'leader')",
        (guild["id"], created_by),
    )
    conn.execute(
        "UPDATE fa_users SET guild_id=%s WHERE id=%s", (guild["id"], created_by)
    )
    conn.commit()
    return guild


def _same_tenant(left, right) -> bool:
    """Whether two tenant values name the same population.

    NULL and '' are both written for "no tenant" (the SaaS middleware returns
    None, refresh_leaderboard_cache stores ''), so comparing them raw would split
    one real tenant in two.
    """
    return (left or "") == (right or "")


def join_guild(invite_code: str, user_id: int, tenant_id: str | None = None) -> dict | None:
    """Join a guild by invite code, or None when the code does not resolve.

    aca-trn-04: returns None for a guild in a DIFFERENT tenant as well as for a
    missing code — deliberately the same answer, so a probe cannot use the
    response to confirm that someone else's invite code exists.
    """
    conn = get_connection()
    guild = conn.execute(
        "SELECT * FROM fa_guilds WHERE invite_code=%s", (invite_code.upper(),)
    ).fetchone()
    if not guild:
        return None
    guild_tenant = dict(guild).get("tenant_id")
    if not _same_tenant(guild_tenant, tenant_id):
        _log.warning("cross-tenant guild join refused: guild %s, user %s",
                     dict(guild).get("id"), user_id)
        return None
    try:
        conn.execute(
            "INSERT INTO fa_guild_members (guild_id,user_id) VALUES (%s,%s)",
            (guild["id"], user_id),
        )
        conn.execute(
            "UPDATE fa_users SET guild_id=%s WHERE id=%s", (guild["id"], user_id)
        )
        conn.commit()
    except Exception as exc:  # noqa: BLE001 - best-effort persistence; logged, never raised
        _log.warning("join_guild: best-effort INSERT into fa_guild_members failed (non-blocking): %s", exc)
    return dict(guild)


def get_guild_stats(guild_id: int, tenant_id: str | None = None) -> dict | None:
    """Members and total XP for a guild, or None when the guild does not exist.

    aca-hyg-03: this returned {"members": [], "total_xp": 0} either way, so a caller
    could not tell an empty guild from a missing one and the route 200'd on a bad id.
    An empty-but-real guild still returns a dict with an empty member list.

    aca-trn-04: a guild belonging to another tenant reads as missing — the check is
    unconditional, not opt-in, because ``tenant_id=None`` is itself a real tenant
    (the default one) and an opt-in check would leave the caller who most needs it
    unprotected. ``/api/academy/guild/<id>`` takes the id straight from the URL and
    had no authorisation of any kind, so with more than one tenant enrolled it was an
    id-enumeration read of every learner's display name and XP. Members are filtered
    by tenant too: guild rows predating this column carry NULL and would otherwise
    still list the cross-tenant members joined before the fix.
    """
    conn = get_connection()
    row = conn.execute(
        "SELECT tenant_id FROM fa_guilds WHERE id=%s", (guild_id,)
    ).fetchone()
    if not row:
        return None
    if not _same_tenant(dict(row).get("tenant_id"), tenant_id):
        return None
    if tenant_id:
        members = conn.execute(
            """SELECT u.display_name, u.xp, u.level, gm.role
               FROM fa_guild_members gm
               JOIN fa_users u ON u.id=gm.user_id
               WHERE gm.guild_id=%s AND u.tenant_id=%s
               ORDER BY u.xp DESC""",
            (guild_id, tenant_id),
        ).fetchall()
    else:
        members = conn.execute(
            """SELECT u.display_name, u.xp, u.level, gm.role
               FROM fa_guild_members gm
               JOIN fa_users u ON u.id=gm.user_id
               WHERE gm.guild_id=%s AND (u.tenant_id IS NULL OR u.tenant_id='')
               ORDER BY u.xp DESC""",
            (guild_id,),
        ).fetchall()
    total_xp = sum(m["xp"] for m in members)
    return {"members": [dict(m) for m in members], "total_xp": total_xp}


# ---------------------------------------------------------------------------
# Leaderboard
# ---------------------------------------------------------------------------

_LEADERBOARD_CACHE_TTL = 300  # seconds


def _leaderboard_cache_fresh(conn, period: str, tenant_id: str | None = None) -> bool:
    """Whether THIS TENANT's cache for ``period`` is inside the TTL.

    aca-trn-04: the freshness probe ignored tenant_id while every read and write
    around it was tenant-scoped. So the first tenant to refresh made the cache
    look fresh for all of them, and every other tenant's ``refresh_leaderboard_cache``
    was skipped forever — their rows were never written, the cache query returned
    nothing, and they silently fell through to the uncached fallback (which has no
    ``rank_pos``). Invisible with one learner in one tenant; permanent with two.
    """
    try:
        params: list = [period]
        q = "SELECT MAX(computed_at) FROM fa_leaderboard_cache WHERE period=%s"
        if tenant_id:
            q += " AND tenant_id=%s"
            params.append(tenant_id)
        else:
            q += " AND (tenant_id IS NULL OR tenant_id='')"
        row = conn.execute(q, params).fetchone()
        if not row or not row[0]:
            return False
        cached_at = datetime.fromisoformat(row[0].replace("Z", "+00:00"))
        age = (datetime.now(timezone.utc) - cached_at.astimezone(timezone.utc)).total_seconds()
        return age < _LEADERBOARD_CACHE_TTL
    except Exception:
        return False


def refresh_leaderboard_cache(period: str = "weekly", tenant_id: str | None = None) -> int:
    """Recompute XP rankings and persist them into fa_leaderboard_cache. Returns row count."""
    conn = get_connection()
    q = "SELECT id, xp FROM fa_users WHERE role != 'unset'"
    params: list = []
    if tenant_id:
        q += " AND tenant_id=%s"
        params.append(tenant_id)
    else:
        q += " AND (tenant_id IS NULL OR tenant_id='')"
    q += " ORDER BY xp DESC"
    users = conn.execute(q, params).fetchall()
    now = datetime.now(timezone.utc).isoformat()
    count = 0
    for rank, u in enumerate(users, 1):
        try:
            conn.execute(
                """INSERT OR REPLACE INTO fa_leaderboard_cache
                   (user_id, period, score, rank_pos, computed_at, tenant_id)
                   VALUES (%s, %s, %s, %s, %s, %s)""",
                (u["id"], period, u["xp"], rank, now, tenant_id or ""),
            )
            count += 1
        except Exception as exc:  # noqa: BLE001 - best-effort persistence; logged, never raised
            _log.warning(
                "refresh_leaderboard_cache: best-effort INSERT into fa_leaderboard_cache failed (non-blocking): %s",
                exc,
            )
    try:
        conn.commit()
    except Exception:
        pass
    return count


def get_leaderboard(period: str = "alltime", role: str = None, limit: int = 20, tenant_id: str | None = None) -> list[dict]:
    conn = get_connection()
    if not _leaderboard_cache_fresh(conn, period, tenant_id):
        try:
            refresh_leaderboard_cache(period=period, tenant_id=tenant_id)
        except Exception:
            pass
    # Cache-backed query: returns score + rank_pos from cache
    try:
        q = """SELECT u.display_name, u.role, u.level, u.xp, u.streak_days,
                      u.guild_id, g.name as guild_name,
                      lc.score, lc.rank_pos
               FROM fa_leaderboard_cache lc
               JOIN fa_users u ON u.id=lc.user_id
               LEFT JOIN fa_guilds g ON g.id=u.guild_id
               WHERE lc.period=%s AND u.role != 'unset'"""
        params: list = [period]
        if tenant_id:
            q += " AND u.tenant_id=%s"
            params.append(tenant_id)
        else:
            q += " AND (u.tenant_id IS NULL OR u.tenant_id='')"
        if role:
            q += " AND u.role=%s"
            params.append(role)
        q += " ORDER BY lc.rank_pos LIMIT %s"
        params.append(limit)
        rows = conn.execute(q, params).fetchall()
        if rows:
            return [dict(r) for r in rows]
    except Exception:
        pass
    # Fallback: direct query when cache is unavailable
    q = """SELECT u.display_name, u.role, u.level, u.xp, u.xp AS score, u.streak_days,
                  u.guild_id, g.name as guild_name
           FROM fa_users u
           LEFT JOIN fa_guilds g ON g.id=u.guild_id
           WHERE u.role != 'unset'"""
    params = []
    if tenant_id:
        q += " AND u.tenant_id=%s"
        params.append(tenant_id)
    else:
        q += " AND (u.tenant_id IS NULL OR u.tenant_id='')"
    if role:
        q += " AND u.role=%s"
        params.append(role)
    q += " ORDER BY u.xp DESC LIMIT %s"
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

    cert = CERT_BY_KEY.get(cert_key)
    if not cert:
        return {"eligible": False, "gates": [], "error": "unknown cert key"}

    conn = get_connection()
    user = get_user(user_id)
    if not user:
        return {"eligible": False, "gates": [], "error": "user not found"}

    reqs = cert.get("requirements", {})
    gates = []

    # Gate: Tier 1 complete
    if reqs.get("tier1_complete"):
        # aca-ux-04: this counted EVERY tier-1 mission, including the zero-step
        # "Coming soon" one that can never be completed (fga-wire-06) — so the
        # Foundation certificate was unobtainable by construction. Count only
        # completable missions, matching tier_progress().
        t1_missions = conn.execute(
            "SELECT COUNT(*) FROM fa_missions m WHERE m.tier=1 AND m.is_active=1 "
            "AND (SELECT COUNT(*) FROM fa_mission_steps s WHERE s.mission_id=m.id) > 0"
        ).fetchone()[0]
        t1_done = conn.execute(
            """SELECT COUNT(DISTINCT mp.mission_id)
               FROM fa_mission_progress mp
               JOIN fa_missions m ON m.id=mp.mission_id
               WHERE mp.user_id=%s AND mp.status='completed' AND m.tier=1
                 AND (SELECT COUNT(*) FROM fa_mission_steps s
                      WHERE s.mission_id=m.id) > 0""",
            (user_id,),
        ).fetchone()[0]
        met = t1_done >= t1_missions > 0
        gates.append({"name": "Tier 1 Complete", "met": met,
                      "detail": f"{t1_done}/{t1_missions} Tier 1 missions completed"})

    # Gate: Role Tier 2 complete (a percentage of the user's role missions)
    if reqs.get("role_tier2_pct"):
        role = user.get("role", "")
        if role and role != "unset":
            # aca-hyg-02: matched with LIKE '%role%', so 'swe' also matched every
            # 'swe_arch' mission — 37 missions in the denominator instead of 25.
            # Whole-token matching is done in Python via role_matches(), the same
            # comparison list_missions uses.
            #
            # Zero-step missions are excluded for the same reason as the
            # tier1_complete gate (aca-ux-04): nine Tier-2 missions have no steps,
            # and an uncompletable row in a percentage denominator makes 100%
            # unreachable by construction.
            t2_rows = conn.execute(
                "SELECT m.id, m.role_filter FROM fa_missions m "
                "WHERE m.tier=2 AND m.is_active=1 "
                "  AND (SELECT COUNT(*) FROM fa_mission_steps s "
                "       WHERE s.mission_id=m.id) > 0"
            ).fetchall()
            role_ids = {
                (r["id"] if hasattr(r, "keys") else r[0])
                for r in t2_rows
                if role_matches(r["role_filter"] if hasattr(r, "keys") else r[1], role)
            }
            done_ids = {
                (r[0] if not hasattr(r, "keys") else r["mission_id"])
                for r in conn.execute(
                    "SELECT DISTINCT mp.mission_id FROM fa_mission_progress mp "
                    "WHERE mp.user_id=%s AND mp.status=%s",
                    (user_id, MISSION_STATUS_COMPLETED),
                ).fetchall()
            }
            t2_role = len(role_ids)
            t2_done = len(role_ids & done_ids)
            pct = int((t2_done / t2_role * 100) if t2_role else 0)
            met = bool(t2_role) and pct >= reqs["role_tier2_pct"]
            gates.append({"name": f"Role Tier 2 ({role})", "met": met,
                          "detail": (
                              f"{t2_done}/{t2_role} role missions ({pct}%)"
                              if t2_role else
                              f"no Tier 2 missions are targeted at role '{role}'"
                          )})
        else:
            gates.append({"name": "Role Tier 2", "met": False,
                          "detail": "Set your role in profile first"})

    # Gate: aggregate assessment score (aca-trn-01)
    #
    # This requirement has been declared in CERT_TIERS since the certificates were
    # written — "Tier 1 complete + full role Tier 2 track + 20-question adaptive
    # assessment", assessment_score_min: 70 — and NOTHING read it. It fell off the end
    # of this if-chain, so the Foundation certificate attested to an assessment that
    # did not exist. It is enforced here now that there is a model behind it.
    if reqs.get("assessment_score_min"):
        from apps.forge_academy.assessment import certificate_assessment_score

        result = certificate_assessment_score(user_id)
        threshold = int(reqs["assessment_score_min"])
        met = bool(result["graded_steps"]) and result["score"] >= threshold
        gates.append({
            "name": f"Assessment Score >= {threshold}",
            "met": met,
            # collect_cert_evidence snapshots this string verbatim into
            # fa_certificate_evidence (aca-int-07), so it has to carry the figures.
            "detail": (
                f"Assessment score: {result['score']}% across "
                f"{result['graded_steps']} graded steps"
                if result["graded_steps"] else
                "No graded steps attempted yet — the score cannot be satisfied vacuously"
            ),
        })

    # Gate: Foundation cert required
    if reqs.get("foundation"):
        has_found = conn.execute(
            "SELECT COUNT(*) FROM fa_certificates WHERE user_id=%s AND cert_tier='foundation'",
            (user_id,),
        ).fetchone()[0] > 0
        gates.append({"name": "Foundation Cert", "met": has_found,
                      "detail": "FORGE AI Foundation certificate required"})

    # Gate: AADC design score >= threshold
    if reqs.get("aadc_score_min"):
        try:
            best = conn.execute(
                """SELECT MAX(CAST(JSON_EXTRACT(metadata_json,'$.aadc_score') AS REAL))
                   FROM fa_user_achievements WHERE user_id=%s""",
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
                """SELECT COUNT(*) FROM ttx_receipts WHERE player_id=%s AND status='submitted'""",
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
            "SELECT COUNT(*) FROM fa_certificates WHERE user_id=%s AND cert_tier='practitioner'",
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
               WHERE mp.user_id=%s AND mp.status='completed' AND m.tier=3""",
            (user_id,),
        ).fetchone()[0]
        met = t3_done >= t3_total > 0
        gates.append({"name": "Tier 3 Complete", "met": met,
                      "detail": f"{t3_done}/{t3_total} Tier 3 missions completed"})

    eligible = all(g["met"] for g in gates) and len(gates) > 0
    return {"eligible": eligible, "gates": gates}


def collect_cert_evidence(user_id: int, eligibility: dict, conn) -> list[dict]:
    """The work a certificate is about to be issued against.

    aca-int-07 part 2: check_cert_eligibility already computes exactly this and
    issue_certificate read only its boolean, throwing the rest away — so a
    certificate asserted competence and /academy/verify/<token> could do nothing but
    repeat the label back.

    Snapshotted at issue time rather than recomputed on the verify page. A
    certificate is a statement about a moment: recomputing lets the claim drift with
    the data underneath it, so retiring a mission or re-seeding a step would make a
    certificate issued last year quietly describe something else.
    """
    evidence: list[dict] = []

    # 1. The gates themselves, with the figures that satisfied them.
    for gate in eligibility.get("gates", []):
        evidence.append({
            "evidence_type": "gate",
            "ref_id": None,
            "label": gate.get("name", ""),
            "detail": gate.get("detail", ""),
            "demonstrated_at": None,
            "score": None,
        })

    # 2. The missions that counted, and 3. the verified steps underneath them.
    rows = conn.execute(
        """SELECT m.id AS mission_id, m.title AS mission_title, m.tier,
                  mp.completed_at, mp.score
             FROM fa_mission_progress mp
             JOIN fa_missions m ON m.id = mp.mission_id
            WHERE mp.user_id = %s AND mp.status = 'completed'
            ORDER BY m.tier, m.id""",
        (user_id,),
    ).fetchall()
    for r in rows:
        d = dict(r)
        evidence.append({
            "evidence_type": "mission",
            "ref_id": d["mission_id"],
            "label": d["mission_title"],
            "detail": f"Tier {d['tier']}",
            "demonstrated_at": d.get("completed_at"),
            "score": d.get("score"),
        })

    steps = conn.execute(
        """SELECT s.id AS step_id, s.title AS step_title, s.step_type,
                  m.title AS mission_title, sp.completed_at, sp.score, sp.hints_used
             FROM fa_step_progress sp
             JOIN fa_mission_steps s ON s.id = sp.step_id
             JOIN fa_missions m ON m.id = s.mission_id
            WHERE sp.user_id = %s AND sp.status = 'completed'
            ORDER BY s.mission_id, s.step_num""",
        (user_id,),
    ).fetchall()
    for r in steps:
        d = dict(r)
        hints = d.get("hints_used") or 0
        evidence.append({
            "evidence_type": "step",
            "ref_id": d["step_id"],
            "label": f"{d['mission_title']} — {d['step_title']}",
            "detail": f"{d.get('step_type') or 'step'}, {hints} hint(s)",
            "demonstrated_at": d.get("completed_at"),
            "score": d.get("score"),
        })

    # 4. The competencies those missions demonstrated (aca-trn-02).
    #
    # Missions and steps say what the learner DID. A competency says what that
    # work is evidence OF, in a vocabulary shared with the rest of the platform's
    # ontology — which is the part a reader outside the Academy can actually
    # act on. Snapshotted here with the rest, for the same reason: the claim must
    # not drift after issue.
    try:
        comps = conn.execute(
            """SELECT uc.competency_class, uc.demonstrated_at,
                      COUNT(*) AS mission_count,
                      MIN(uc.demonstrated_at) AS first_at
                 FROM fa_user_competencies uc
                WHERE uc.user_id = %s
                GROUP BY uc.competency_class, uc.demonstrated_at
                ORDER BY uc.competency_class""",
            (user_id,),
        ).fetchall()
    except Exception:
        comps = []
    rolled: dict[str, dict] = {}
    for r in comps:
        d = dict(r)
        cls = d["competency_class"]
        cur = rolled.setdefault(cls, {"n": 0, "first_at": d.get("first_at")})
        cur["n"] += int(d.get("mission_count") or 1)
        if (d.get("first_at") or "") < (cur["first_at"] or ""):
            cur["first_at"] = d.get("first_at")
    for cls, agg in sorted(rolled.items()):
        evidence.append({
            "evidence_type": "competency",
            "ref_id": None,
            "label": cls,
            "detail": f"demonstrated across {agg['n']} completed mission(s)",
            "demonstrated_at": agg["first_at"],
            "score": None,
        })
    return evidence


def get_cert_evidence(cert_id: int) -> list[dict]:
    """The evidence recorded when this certificate was issued."""
    conn = get_connection()
    try:
        rows = conn.execute(
            "SELECT * FROM fa_certificate_evidence WHERE cert_id=%s "
            "ORDER BY CASE evidence_type WHEN 'gate' THEN 0 WHEN 'competency' THEN 1 "
            "WHEN 'mission' THEN 2 ELSE 3 END, id",
            (cert_id,),
        ).fetchall()
    except Exception:
        # Own connection, so swallowing is safe here — see earned_xp for why this
        # would NOT be safe on a caller's connection under PostgreSQL.
        return []
    return [dict(r) for r in rows]


def issue_certificate(user_id: int, cert_key: str) -> dict | None:
    """Issue a certificate if eligible. Returns the cert record or None."""
    import secrets
    from datetime import datetime, timezone
    from apps.forge_academy.constants import CERT_BY_KEY

    eligibility = check_cert_eligibility(user_id, cert_key)
    if not eligibility.get("eligible"):
        return None

    cert_def = CERT_BY_KEY.get(cert_key, {})
    conn = get_connection()
    # Idempotent: return existing cert if already issued
    existing = conn.execute(
        "SELECT * FROM fa_certificates WHERE user_id=%s AND cert_tier=%s",
        (user_id, cert_key),
    ).fetchone()
    if existing:
        return dict(existing)

    token = secrets.token_urlsafe(32)
    now = datetime.now(timezone.utc).isoformat()
    # penta-fix-03: INSERT the certificate AND award its XP bonus on ONE connection
    # in a single transaction, then commit once. Previously the INSERT was left
    # uncommitted while update_user_xp opened a SECOND connection to UPDATE
    # fa_users — fine on PostgreSQL (MVCC) but a self-deadlock under the SQLite test
    # backend, where the uncommitted writer holds the DB write lock and the second
    # connection's UPDATE blocks on busy_timeout, then errors.
    conn.execute(
        """INSERT INTO fa_certificates
           (user_id, cert_tier, cert_label, token, issued_at)
           VALUES (%s,%s,%s,%s,%s)""",
        (user_id, cert_key, cert_def.get("label", cert_key), token, now),
    )
    # Award XP bonus on the SAME connection/transaction as the cert INSERT.
    xp_bonus = cert_def.get("xp_bonus", 0)
    if xp_bonus:
        update_user_xp(user_id, xp_bonus, conn=conn, reason="certificate",
                       source_type="certificate", note=cert_key)

    # aca-int-07 part 2: the evidence goes in on the SAME transaction. A certificate
    # that commits without it is precisely the artefact this card set out to remove —
    # a label asserting competence with nothing behind it — and a partial failure
    # here must take the certificate down with it rather than leave one standing.
    cert_row = conn.execute(
        "SELECT id FROM fa_certificates WHERE user_id=%s AND cert_tier=%s",
        (user_id, cert_key),
    ).fetchone()
    cert_id = (cert_row["id"] if not isinstance(cert_row, tuple) else cert_row[0]) \
        if cert_row else None
    if cert_id is not None:
        for item in collect_cert_evidence(user_id, eligibility, conn):
            conn.execute(
                "INSERT INTO fa_certificate_evidence "
                "(cert_id, user_id, evidence_type, ref_id, label, detail, "
                " demonstrated_at, score) VALUES (%s,%s,%s,%s,%s,%s,%s,%s)",
                (cert_id, user_id, item["evidence_type"], item["ref_id"],
                 item["label"], item["detail"], item["demonstrated_at"],
                 item["score"]),
            )
    conn.commit()
    return conn.execute(
        "SELECT * FROM fa_certificates WHERE user_id=%s AND cert_tier=%s",
        (user_id, cert_key),
    ).fetchone()


def get_user_certificates(user_id: int) -> list[dict]:
    rows = get_connection().execute(
        "SELECT * FROM fa_certificates WHERE user_id=%s ORDER BY issued_at DESC",
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
           WHERE c.token=%s""",
        (token,),
    ).fetchone()
    if not row:
        return None
    result = dict(row)
    # aca-int-07 part 2: a verifier needs to see what was demonstrated, not just be
    # told a label. Read back the snapshot taken at issue time — NOT a fresh
    # computation, which would let the claim drift with the data underneath it.
    result["evidence"] = get_cert_evidence(result["id"])
    return result


# ---------------------------------------------------------------------------
# Ontology mapping
# ---------------------------------------------------------------------------

def upsert_mission_ontology(mission_id: int, ontology_id: str, mission_class: str,
                             topic_class: str, competency_class: str,
                             prereq_paths: list[str] | None = None,
                             conn=None) -> None:
    """Map one mission to its ontology classes.

    `conn` lets the seeder pass its own connection so a whole catalog is mapped
    in one transaction with one commit, instead of opening and committing once
    per mission.
    """
    own = conn is None
    conn = conn or get_connection()
    conn.execute(
        """INSERT INTO fa_mission_ontology
           (mission_id, ontology_id, mission_class, topic_class, competency_class, prereq_ontology_paths_json)
           VALUES (%s, %s, %s, %s, %s, %s)
           ON CONFLICT(mission_id) DO UPDATE SET
             ontology_id=excluded.ontology_id,
             mission_class=excluded.mission_class,
             topic_class=excluded.topic_class,
             competency_class=excluded.competency_class,
             prereq_ontology_paths_json=excluded.prereq_ontology_paths_json""",
        (mission_id, ontology_id, mission_class, topic_class, competency_class,
         json.dumps(prereq_paths or [])),
    )
    if own:
        conn.commit()


def upsert_step_ontology(step_id: int, ontology_id: str, step_class: str,
                          competency_class: str | None = None, conn=None) -> None:
    own = conn is None
    conn = conn or get_connection()
    conn.execute(
        """INSERT INTO fa_step_ontology
           (step_id, ontology_id, step_class, competency_class)
           VALUES (%s, %s, %s, %s)
           ON CONFLICT(step_id) DO UPDATE SET
             ontology_id=excluded.ontology_id,
             step_class=excluded.step_class,
             competency_class=excluded.competency_class""",
        (step_id, ontology_id, step_class, competency_class),
    )
    if own:
        conn.commit()


# ---------------------------------------------------------------------------
# Competency tracking + KG edges
# ---------------------------------------------------------------------------

def _rollback_quietly(conn) -> None:
    """Return an errored connection to a usable state.

    Unconditional, because ``getattr(conn, "in_transaction", False)`` is always
    False on a StorageConnection — it forwards nothing extra — so there is no
    state to test first. On PostgreSQL a failed statement poisons the whole
    transaction: every later statement raises InFailedSqlTransaction until
    somebody rolls back. That is the mechanism by which one bad column name in
    the KG edge insert took down the SELECT that followed it.
    """
    try:
        conn.rollback()
    except Exception:
        pass


def record_user_competency(user_id: int, competency_class: str,
                            source_mission_id: int | None = None,
                            source_step_id: int | None = None,
                            evidence: dict | None = None) -> dict:
    """Record a demonstrated competency and link it into the knowledge graph.

    Raises on failure to record the competency itself — a training record that
    reports success when it wrote nothing is worse than no training record.

    The KG edge is a secondary index over data that now lives in
    fa_user_competencies, so a failure there is reported in the returned
    ``kg_edge`` field rather than thrown: it must not cost the learner the
    credit they earned. It is no longer swallowed silently.
    """
    conn = get_connection()
    now = datetime.now(timezone.utc).isoformat()
    conn.execute(
        """INSERT OR IGNORE INTO fa_user_competencies
           (user_id, competency_class, source_mission_id, source_step_id, demonstrated_at, evidence_json)
           VALUES (%s, %s, %s, %s, %s, %s)""",
        (user_id, competency_class, source_mission_id, source_step_id, now,
         json.dumps(evidence or {})),
    )
    conn.commit()

    kg_edge = "ok"
    try:
        _create_kg_competency_edge(conn, user_id, competency_class, source_mission_id, now)
    except Exception as exc:
        # Roll back BEFORE anything else touches this connection.
        _rollback_quietly(conn)
        kg_edge = f"failed: {exc}"
        _log.warning("competency KG edge failed for user %s / %s: %s",
                     user_id, competency_class, exc)

    row = conn.execute(
        "SELECT * FROM fa_user_competencies WHERE user_id=%s AND competency_class=%s "
        "AND source_mission_id=%s",
        (user_id, competency_class, source_mission_id),
    ).fetchone()
    result = dict(row) if row else {"user_id": user_id, "competency_class": competency_class}
    result["kg_edge"] = kg_edge
    return result


KG_GRAPH_ID = "icdev-core-ontology"


def _ensure_kg_graph(conn) -> None:
    """Guarantee the graph row the competency nodes and edges hang off.

    The platform's KG DDL (tools/knowledge_graph/ingester.py, federation.py,
    graph_rag.py) declares ``graph_id TEXT NOT NULL REFERENCES kg_graphs(id)``,
    and ``icdev-core-ontology`` is created by exactly one thing — the ontology
    federation pass, which runs only when somebody federates the ontology. On an
    install built from that DDL and never federated, the node insert fails on a
    foreign key, not on a column name.

    How much this bites depends on whether the constraint was materialised.
    Verified 2026-08-02: the current production PostgreSQL has the three tables
    but NOT the foreign key (only kg_nodes.source_chunk_id -> rag_chunks), so
    there the edge failed on the column name alone; a SQLite install with
    ``PRAGMA foreign_keys=ON``, or any PG built from the DDL above, fails on the
    key as well. Do not "disprove" this by checking production for a constraint
    that instance never had — seeding the row is what makes the chain correct on
    both, and a graph row is the right thing to have regardless of enforcement.

    Same graph id and same graph_type as the federation pass, so this seeds the
    row federation would later UPDATE rather than forking a parallel graph.
    """
    row = conn.execute("SELECT id FROM kg_graphs WHERE id=%s", (KG_GRAPH_ID,)).fetchone()
    if row:
        return
    now = datetime.now(timezone.utc).isoformat()
    conn.execute(
        """INSERT OR IGNORE INTO kg_graphs
           (id, project_id, name, description, metadata, created_at, updated_at)
           VALUES (%s, %s, %s, %s, %s, %s, %s)""",
        (KG_GRAPH_ID, None, "ICDEV Core Ontology",
         "Unified ontology graph; FORGE Academy links demonstrated competencies here.",
         json.dumps({"graph_type": "ontology", "source": "forge_academy"}), now, now),
    )


def _create_kg_competency_edge(conn, user_id: int, competency_class: str,
                                source_mission_id: int | None, demonstrated_at: str) -> None:
    """Insert a KG edge linking the user to the ontology competency class.

    kg_nodes/kg_edges are platform-owned. The edge's relation column is named
    ``relationship``; this wrote ``label`` and failed on every call against
    PostgreSQL (aca-trn-02). Keep the column list in step with the platform
    schema, not with the Academy's local CREATE IF NOT EXISTS.
    """
    _ensure_kg_graph(conn)
    user = conn.execute("SELECT username FROM fa_users WHERE id=%s", (user_id,)).fetchone()
    user_label = (user["username"] if user else None) or f"user_{user_id}"
    source_node = f"fa_user:{user_id}"
    target_node = f"ontology:{competency_class}"
    edge_id = f"{source_node}--demonstrates--{target_node}--{source_mission_id or 0}"

    conn.execute(
        """INSERT OR REPLACE INTO kg_nodes
           (id, graph_id, label, entity_type, properties, created_at)
           VALUES (%s, %s, %s, %s, %s, %s)""",
        (source_node, KG_GRAPH_ID, user_label, "fa_user",
         json.dumps({"user_id": user_id}), demonstrated_at),
    )
    conn.execute(
        """INSERT OR REPLACE INTO kg_nodes
           (id, graph_id, label, entity_type, properties, created_at)
           VALUES (%s, %s, %s, %s, %s, %s)""",
        (target_node, KG_GRAPH_ID, competency_class, "ontology_class",
         json.dumps({"canonical_id": competency_class}), demonstrated_at),
    )
    conn.execute(
        """INSERT OR REPLACE INTO kg_edges
           (id, graph_id, source_id, target_id, relationship, properties, created_at)
           VALUES (%s, %s, %s, %s, %s, %s, %s)""",
        (edge_id, KG_GRAPH_ID, source_node, target_node, "demonstrates",
         json.dumps({"source_mission_id": source_mission_id, "user_id": user_id}), demonstrated_at),
    )
    conn.commit()


def record_mission_competencies(user_id: int, mission_id: int, score: int = 100,
                                 hints_used: int = 0) -> dict:
    """Record every competency a verified mission completion demonstrates.

    This is the whole point of the chain: a mission the learner finished is
    converted into specific, cited claims — the tier it was at, the subject it
    was in, and the classes carried by the individual steps they actually
    passed. Each row carries the evidence it rests on, so a certificate can
    quote it and a verifier can check it.

    Returns a summary rather than raising, so the caller can put the outcome in
    the response the learner sees. ``recorded`` empty with ``classes`` non-empty
    means every class was already on file (the mission was re-completed);
    ``classes`` empty means the mission has no ontology mapping, which is a
    catalog defect and is reported as ``unmapped``.
    """
    from .ontology import build_mission_competency_classes

    conn = get_connection()
    result: dict = {"recorded": [], "classes": [], "errors": [], "unmapped": False,
                    "kg_edges": "ok"}

    try:
        onto = conn.execute(
            """SELECT o.topic_class, o.competency_class, m.slug, m.title, m.tier
                 FROM fa_missions m
                 LEFT JOIN fa_mission_ontology o ON o.mission_id = m.id
                WHERE m.id = %s""",
            (mission_id,),
        ).fetchone()
    except Exception as exc:
        _rollback_quietly(conn)
        result["errors"].append(str(exc))
        return result

    if not onto:
        result["errors"].append(f"mission {mission_id} not found")
        return result
    onto = dict(onto)

    if not onto.get("competency_class") and not onto.get("topic_class"):
        # The mission exists but was never mapped. Say so — silently recording
        # nothing here is what made 35 unmapped missions invisible.
        result["unmapped"] = True
        _log.warning("mission %s (%s) has no ontology mapping — no competency recorded",
                     mission_id, onto.get("slug"))
        return result

    classes = build_mission_competency_classes(
        onto.get("topic_class") or "", onto.get("tier") or 1)

    # The step classes the learner actually earned. A mission is completed by
    # passing its steps, so the steps are the evidence, and only steps that
    # carry a competency class (i.e. not passive `watch` steps) count.
    step_evidence: dict[str, list[int]] = {}
    try:
        rows = conn.execute(
            """SELECT so.competency_class, s.id AS step_id
                 FROM fa_step_progress sp
                 JOIN fa_mission_steps s ON s.id = sp.step_id
                 JOIN fa_step_ontology so ON so.step_id = s.id
                WHERE sp.user_id = %s AND s.mission_id = %s
                  AND sp.status = 'completed' AND so.competency_class IS NOT NULL""",
            (user_id, mission_id),
        ).fetchall()
        for r in rows:
            d = dict(r)
            step_evidence.setdefault(d["competency_class"], []).append(d["step_id"])
    except Exception as exc:
        # Step-level detail is an enrichment; the mission-level claim stands
        # without it. Record the error instead of discarding it.
        _rollback_quietly(conn)
        result["errors"].append(str(exc))

    for cls in step_evidence:
        if cls not in classes:
            classes.append(cls)
    result["classes"] = list(classes)

    for cls in classes:
        try:
            row = record_user_competency(
                user_id=user_id,
                competency_class=cls,
                source_mission_id=mission_id,
                source_step_id=(step_evidence.get(cls) or [None])[0],
                evidence={
                    "mission_slug": onto.get("slug"),
                    "mission_title": onto.get("title"),
                    "tier": onto.get("tier"),
                    "score": score,
                    "hints_used": hints_used,
                    "verified_step_ids": step_evidence.get(cls, []),
                },
            )
            if row.get("kg_edge", "ok") != "ok":
                result["kg_edges"] = row["kg_edge"]
            result["recorded"].append(cls)
        except Exception as exc:
            _rollback_quietly(conn)
            result["errors"].append(f"{cls}: {exc}")
            _log.exception("competency %s not recorded for user %s mission %s",
                           cls, user_id, mission_id)

    return result


def backfill_user_competencies() -> dict:
    """Record competencies for missions completed before this chain worked.

    Every completed mission on the board predates a working recorder, so
    without this the learners who did the work are the only ones with no
    training record. Idempotent — ``record_user_competency`` ignores conflicts —
    so it is safe to run on every init.
    """
    conn = get_connection()
    summary = {"users": 0, "missions": 0, "recorded": 0, "errors": []}
    try:
        rows = conn.execute(
            """SELECT mp.user_id, mp.mission_id, mp.score
                 FROM fa_mission_progress mp
                 JOIN fa_mission_ontology o ON o.mission_id = mp.mission_id
                WHERE mp.status = 'completed'
                  AND NOT EXISTS (
                      SELECT 1 FROM fa_user_competencies uc
                       WHERE uc.user_id = mp.user_id
                         AND uc.source_mission_id = mp.mission_id)"""
        ).fetchall()
    except Exception as exc:
        _rollback_quietly(conn)
        summary["errors"].append(str(exc))
        return summary

    seen_users = set()
    for r in rows:
        d = dict(r)
        out = record_mission_competencies(d["user_id"], d["mission_id"],
                                          score=d.get("score") or 100)
        summary["missions"] += 1
        summary["recorded"] += len(out["recorded"])
        summary["errors"].extend(out["errors"])
        seen_users.add(d["user_id"])
    summary["users"] = len(seen_users)
    if summary["recorded"]:
        _log.info("FORGE Academy: backfilled %d competency record(s) across %d mission(s)",
                  summary["recorded"], summary["missions"])
    return summary


def get_user_competencies(user_id: int) -> list[dict]:
    rows = get_connection().execute(
        """SELECT uc.*, m.slug as mission_slug, m.title as mission_title
           FROM fa_user_competencies uc
           LEFT JOIN fa_missions m ON m.id = uc.source_mission_id
           WHERE uc.user_id = %s
           ORDER BY uc.demonstrated_at DESC""",
        (user_id,),
    ).fetchall()
    return [dict(r) for r in rows]


def get_competency_profile(user_id: int) -> dict:
    """The learner's training record: what they can do, and what proves it.

    Grouped by competency class, because that is the unit a reader cares about
    ("has this person worked on boundaries?"), with every mission that
    demonstrated it listed underneath as the evidence. ``catalog_missions`` is
    the number of missions in the catalog carrying that class, so depth is
    readable — 1 of 9 is a different claim from 9 of 9 and the page should not
    present them identically.
    """
    conn = get_connection()
    profile: dict = {"competencies": [], "total_classes": 0, "total_records": 0,
                     "error": None}
    try:
        rows = conn.execute(
            """SELECT uc.competency_class, uc.demonstrated_at, uc.evidence_json,
                      uc.source_mission_id, m.slug AS mission_slug,
                      m.title AS mission_title, m.tier
                 FROM fa_user_competencies uc
                 LEFT JOIN fa_missions m ON m.id = uc.source_mission_id
                WHERE uc.user_id = %s
                ORDER BY uc.demonstrated_at DESC""",
            (user_id,),
        ).fetchall()
    except Exception as exc:
        _rollback_quietly(conn)
        profile["error"] = str(exc)
        return profile

    # Catalog breadth per class, computed once rather than per group.
    catalog: dict[str, int] = {}
    try:
        for r in conn.execute(
            """SELECT competency_class AS cls, COUNT(*) AS n FROM fa_mission_ontology
                GROUP BY competency_class"""
        ).fetchall():
            d = dict(r)
            if d["cls"]:
                catalog[d["cls"]] = catalog.get(d["cls"], 0) + int(d["n"])
        for r in conn.execute(
            """SELECT topic_class AS cls, COUNT(*) AS n FROM fa_mission_ontology
                GROUP BY topic_class"""
        ).fetchall():
            d = dict(r)
            if d["cls"]:
                catalog[d["cls"]] = catalog.get(d["cls"], 0) + int(d["n"])
    except Exception as exc:
        _rollback_quietly(conn)
        profile["error"] = str(exc)

    grouped: dict[str, dict] = {}
    for r in rows:
        d = dict(r)
        cls = d["competency_class"]
        try:
            ev = json.loads(d.get("evidence_json") or "{}")
        except (ValueError, TypeError):
            ev = {}
        entry = grouped.setdefault(cls, {
            "competency_class": cls,
            "namespace": cls.split(":")[0] if ":" in cls else "icdev",
            "label": cls.split(":")[-1],
            "first_demonstrated_at": d.get("demonstrated_at"),
            "last_demonstrated_at": d.get("demonstrated_at"),
            "catalog_missions": catalog.get(cls, 0),
            "evidence": [],
        })
        entry["evidence"].append({
            "mission_id": d.get("source_mission_id"),
            "mission_slug": d.get("mission_slug"),
            "mission_title": d.get("mission_title") or ev.get("mission_title") or "",
            "tier": d.get("tier") or ev.get("tier"),
            "score": ev.get("score"),
            "verified_step_ids": ev.get("verified_step_ids", []),
            "demonstrated_at": d.get("demonstrated_at"),
        })
        if (d.get("demonstrated_at") or "") < (entry["first_demonstrated_at"] or ""):
            entry["first_demonstrated_at"] = d.get("demonstrated_at")

    for entry in grouped.values():
        entry["mission_count"] = len(entry["evidence"])
    profile["competencies"] = sorted(
        grouped.values(), key=lambda e: (-e["mission_count"], e["competency_class"]))
    profile["total_classes"] = len(grouped)
    profile["total_records"] = len(rows)
    return profile


def seed_mission_ontology_mappings() -> dict:
    """Map every mission and step in the catalog to its ontology classes.

    Driven off the DATABASE, not off BUILTIN_MISSIONS. The previous version
    iterated the builtin list and bailed out entirely once
    ``COUNT(fa_mission_ontology) >= len(BUILTIN_MISSIONS)``, which failed twice
    over (aca-trn-02): missions seeded by any other source — the AADC and AIMC
    seed modules, a tenant's own content — were never in the list to be mapped,
    and the count guard then declared the job finished on their behalf. In
    production that left 35 of 124 missions and 122 of 212 steps with no
    ontology row, so completing them could not have demonstrated anything.

    Only the gaps are filled, so a fully-mapped catalog costs two SELECTs and no
    writes, and newly-added content is picked up on the next init without a
    manual reseed. Everything runs on one connection and commits once — the
    per-row commit storm is what the old guard was really protecting against.

    Returns a summary so a caller (init, a health probe, a test) can see what
    was actually mapped instead of inferring it from silence.
    """
    from .ontology import build_mission_ontology_id, build_step_ontology_id
    conn = get_connection()
    summary = {"missions_mapped": 0, "steps_mapped": 0, "errors": []}

    try:
        missions = conn.execute(
            """SELECT m.id, m.slug, m.mission_type, m.topic, m.title, m.tier
                 FROM fa_missions m
                 LEFT JOIN fa_mission_ontology o ON o.mission_id = m.id
                WHERE o.id IS NULL"""
        ).fetchall()
    except Exception as exc:
        # Tables may not exist yet on a partially-migrated install. Report it
        # rather than returning a summary that reads like a clean no-op.
        _log.warning("FORGE Academy: ontology mapping scan failed: %s", exc)
        _rollback_quietly(conn)
        summary["errors"].append(str(exc))
        return summary

    for m in missions:
        m = dict(m)
        onto = build_mission_ontology_id(
            slug=m["slug"],
            mission_type=m.get("mission_type") or "coding",
            topic=m.get("topic") or "",
            title=m.get("title") or "",
            tier=m.get("tier") or 1,
        )
        upsert_mission_ontology(
            mission_id=m["id"],
            ontology_id=onto["ontology_id"],
            mission_class=onto["mission_class"],
            topic_class=onto["topic_class"],
            competency_class=onto["competency_class"],
            prereq_paths=onto["prereq_ontology_paths"],
            conn=conn,
        )
        summary["missions_mapped"] += 1
    if summary["missions_mapped"]:
        conn.commit()

    # Steps are mapped after missions so a step can read the topic class its
    # mission was just given. A step with a row but a NULL competency_class is
    # re-mapped too: that is what every pre-aca-trn-02 row looks like.
    try:
        steps = conn.execute(
            """SELECT s.id, s.mission_id, s.step_num, s.step_type,
                      m.slug, m.tier, o.topic_class,
                      so.id AS mapping_id, so.ontology_id AS have_onto,
                      so.step_class AS have_step_class,
                      so.competency_class AS have_competency
                 FROM fa_mission_steps s
                 JOIN fa_missions m ON m.id = s.mission_id
                 LEFT JOIN fa_mission_ontology o ON o.mission_id = s.mission_id
                 LEFT JOIN fa_step_ontology so ON so.step_id = s.id
                WHERE so.id IS NULL OR so.competency_class IS NULL"""
        ).fetchall()
    except Exception as exc:
        _log.warning("FORGE Academy: step ontology scan failed: %s", exc)
        _rollback_quietly(conn)
        summary["errors"].append(str(exc))
        return summary

    for s in steps:
        s = dict(s)
        step_onto = build_step_ontology_id(
            s["slug"], s["step_num"], s.get("step_type") or "configure",
            topic_class=s.get("topic_class") or "",
            tier=s.get("tier") or 1,
        )
        # A NULL competency_class is ambiguous: it is either a row written before
        # the column existed, or a correctly-mapped PASSIVE step, for which NULL
        # is the right and permanent answer. Re-mapping on NULL alone would
        # rewrite every `watch` step on every init, forever, and never converge.
        # Compare against what is already stored and skip when it matches.
        if (s.get("mapping_id") is not None
                and s.get("have_onto") == step_onto["ontology_id"]
                and s.get("have_step_class") == step_onto["step_class"]
                and s.get("have_competency") == step_onto["competency_class"]):
            continue
        upsert_step_ontology(
            step_id=s["id"],
            ontology_id=step_onto["ontology_id"],
            step_class=step_onto["step_class"],
            competency_class=step_onto["competency_class"],
            conn=conn,
        )
        summary["steps_mapped"] += 1
    if summary["steps_mapped"]:
        conn.commit()

    if summary["missions_mapped"] or summary["steps_mapped"]:
        _log.info("FORGE Academy: mapped %d mission(s) and %d step(s) to ontology classes",
                  summary["missions_mapped"], summary["steps_mapped"])
    return summary


def competency_chain_status() -> dict:
    """How much of the catalog can actually produce a competency record.

    A silent competency chain is indistinguishable from one that works and has
    nothing to say yet, which is exactly how this shipped empty. These counters
    are served by /api/academy/health so the difference is visible without
    reading the database by hand.
    """
    conn = get_connection()
    status: dict = {"ok": True, "error": None}

    def _count(key: str, sql: str) -> None:
        try:
            row = conn.execute(sql).fetchone()
            status[key] = int(row[0]) if row else 0
        except Exception as exc:
            _rollback_quietly(conn)
            status["ok"] = False
            status["error"] = str(exc)
            status[key] = None

    _count("missions", "SELECT COUNT(*) FROM fa_missions")
    _count("missions_unmapped",
           "SELECT COUNT(*) FROM fa_missions m "
           "LEFT JOIN fa_mission_ontology o ON o.mission_id=m.id WHERE o.id IS NULL")
    _count("steps", "SELECT COUNT(*) FROM fa_mission_steps")
    # Unmapped means NO ontology row. A row with a NULL competency_class is a
    # correctly-mapped passive step (`watch`), which is a valid end state —
    # counting those as unmapped would leave this figure permanently non-zero
    # and so permanently unreadable.
    _count("steps_unmapped",
           "SELECT COUNT(*) FROM fa_mission_steps s "
           "LEFT JOIN fa_step_ontology o ON o.step_id=s.id "
           "WHERE o.id IS NULL")
    _count("steps_with_competency",
           "SELECT COUNT(*) FROM fa_step_ontology WHERE competency_class IS NOT NULL")
    _count("missions_completed",
           "SELECT COUNT(*) FROM fa_mission_progress WHERE status='completed'")
    _count("competencies_recorded", "SELECT COUNT(*) FROM fa_user_competencies")

    # The one combination that means something is broken rather than merely
    # unused: completed missions exist and not one produced a competency row.
    completed = status.get("missions_completed") or 0
    recorded = status.get("competencies_recorded") or 0
    status["stalled"] = bool(completed > 0 and recorded == 0)
    return status
