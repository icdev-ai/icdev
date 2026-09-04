-- CUI // SP-CTI
-- Migration 323: the FORGE Academy instructor and cohort workflow (aca-trn-04).
--
-- The Academy could measure a training programme but not run one. Org Readiness
-- (/academy/org-readiness) reported cohort counts, skill gaps and a composite score
-- to the admin/pm/isso tier; nothing anywhere could assign a mission, set a due
-- date, look at what a learner submitted, or record a human verdict on it. Every
-- other route was self-service and per-learner.
--
-- fa_assignments      — one row per assignment. Target is one learner or a cohort
--                       (a role token, or 'all'). Cohort membership is resolved at
--                       READ time, not frozen here, so a learner who enrols into the
--                       role afterwards inherits the assignment. Scope is one
--                       mission or a track (every active mission whose role_filter
--                       covers a role token).
-- fa_instructor_reviews — one row per human verdict, optionally carrying a score
--                       override plus the prior value it replaced.
-- fa_instructor_audit — APPEND-ONLY, registered in APPEND_ONLY_TABLES. Every
--                       assign / cancel / review lands here with its actor.
--
-- An override writes fa_mission_progress.score and NEVER moves XP. Those are
-- separate columns, and every XP point in this schema has a provenance row in
-- fa_xp_ledger (aca-int-07). An instructor-mintable XP path would reopen exactly
-- the hole that card closed — a rank bought rather than demonstrated.
--
-- Also: fa_guilds.tenant_id. Guilds were the one cross-learner object with no
-- tenant column, so an invite code (global, and the only key join_guild checked)
-- admitted a learner from another tenant straight into get_guild_stats, which
-- returns every member's display name and XP. Invisible while exactly one learner
-- was enrolled; a cross-tenant read the moment a second tenant exists. Existing
-- rows keep NULL, which reads as the default tenant — correct for every
-- single-tenant install, which is every install that has one today. That column is
-- added by apps/forge_academy/db.py::migrate()'s safe-column loop rather than by an
-- ALTER here — the same mechanism that added fa_leaderboard_cache.tenant_id. A bare
-- ALTER in this file would abort the whole executescript on SQLite, where the
-- child app's own DDL has already created fa_guilds WITH the column: PostgreSQL
-- reports "column already exists" (which the runner tolerates) but SQLite reports
-- "duplicate column name" (which it does not).
--
-- Portable across PostgreSQL and the SQLite test/fallback backend. No semicolon
-- appears inside any string literal: statement splitters that are not string-aware
-- cut a statement in half at the first one.

CREATE TABLE IF NOT EXISTS fa_assignments (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    -- 'mission' (one fa_missions row) or 'track' (every active mission whose
    -- role_filter covers track_key)
    assignment_type TEXT    NOT NULL DEFAULT 'mission',
    mission_id      INTEGER,
    track_key       TEXT,
    -- 'learner' (target_user_id) or 'cohort' (target_role, or 'all')
    target_type     TEXT    NOT NULL DEFAULT 'learner',
    target_user_id  INTEGER,
    target_role     TEXT,
    -- ISO-8601 UTC. NULL means no deadline, which is why the writer refuses an
    -- unparseable date rather than storing NULL: a deadline that silently does
    -- not exist never goes overdue and nobody finds out.
    due_at          TEXT,
    note            TEXT,
    assigned_by     TEXT    NOT NULL,
    status          TEXT    NOT NULL DEFAULT 'open',
    tenant_id       TEXT,
    classification  TEXT    DEFAULT 'CUI',
    created_at      TEXT    NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_fa_assignments_tenant ON fa_assignments(tenant_id);

CREATE INDEX IF NOT EXISTS idx_fa_assignments_target ON fa_assignments(target_type, target_user_id);

CREATE TABLE IF NOT EXISTS fa_instructor_reviews (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id         INTEGER NOT NULL,
    mission_id      INTEGER,
    step_id         INTEGER,
    assignment_id   INTEGER,
    -- approved | needs_rework | rejected
    verdict         TEXT    NOT NULL,
    -- NULL when the review is a comment only. When set, fa_mission_progress.score
    -- was written to this value and prior_score records what it replaced, so the
    -- change is reversible by inspection rather than by guesswork.
    override_score  INTEGER,
    prior_score     INTEGER,
    comment         TEXT,
    reviewer        TEXT    NOT NULL,
    tenant_id       TEXT,
    classification  TEXT    DEFAULT 'CUI',
    created_at      TEXT    NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_fa_instructor_reviews_user ON fa_instructor_reviews(user_id);

CREATE TABLE IF NOT EXISTS fa_instructor_audit (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    -- assignment.create | assignment.cancel | review.record
    action          TEXT    NOT NULL,
    actor           TEXT    NOT NULL,
    actor_role      TEXT,
    subject_type    TEXT,
    subject_id      TEXT,
    detail_json     TEXT    DEFAULT '{}',
    tenant_id       TEXT,
    classification  TEXT    DEFAULT 'CUI',
    created_at      TEXT    NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_fa_instructor_audit_tenant ON fa_instructor_audit(tenant_id, id);
