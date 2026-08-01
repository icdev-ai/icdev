-- CUI // SP-CTI
-- Migration 315: an append-only XP ledger, and the reconstruction of what can be
-- reconstructed (aca-int-07).
--
-- Today a rank asserts competence with nothing behind it. XP lives in exactly one
-- place, fa_users.xp, as a running total mutated by `SET xp = xp + ?` from eleven
-- call sites. There is no record anywhere linking an award to the work that earned
-- it, so a duplicated, negative or fabricated award is indistinguishable from a
-- correct one after the fact.
--
-- Measured on the live board before this migration:
--
--   fa_users.xp                       1715
--   sum(fa_daily_logins.xp_awarded)   1465   across 41 logins
--   earned from graded work            250   2 completed steps
--
-- So 85% of the learner's rank is attendance. Rank is computed from the total, which
-- means logging in for 41 days outranks demonstrating anything.
--
-- WHAT THIS MIGRATION DOES NOT DO: invent provenance. The backfill reconstructs only
-- awards that have a surviving source row — daily logins and completed steps. Any
-- residual is written as a single `opening_balance` row flagged unverified, rather
-- than distributed across plausible-looking reasons. A ledger that launders unknown
-- history into confident entries is worse than no ledger, because it reads as
-- evidence.
--
-- Append-only: registered in APPEND_ONLY_TABLES in .claude/hooks/pre_tool_use.py.
-- Corrections are new compensating rows, never UPDATE or DELETE.
--
-- Idempotent: the backfill inserts are guarded by NOT EXISTS on (user_id, reason,
-- source_type, source_id), so re-running adds nothing.
--
-- Portable across PostgreSQL and the SQLite test/fallback backend: no dialect JSON,
-- no UPDATE...FROM, correlated subqueries only.

CREATE TABLE IF NOT EXISTS fa_xp_ledger (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id        INTEGER NOT NULL,
    xp_delta       INTEGER NOT NULL,
    -- step_pass | mission_complete | daily_login | achievement | certificate
    -- | opening_balance | adjustment
    reason         TEXT    NOT NULL,
    -- What earned it. NULL only for opening_balance, which is the point of it.
    source_type    TEXT,
    source_id      INTEGER,
    -- Attendance is XP for showing up. Kept in the same ledger so the total still
    -- reconciles to fa_users.xp, but flagged so rank can exclude it.
    is_attendance  INTEGER NOT NULL DEFAULT 0,
    -- 0 when the row was reconstructed rather than observed, so a consumer can tell
    -- a real award from an accounting artefact.
    verified       INTEGER NOT NULL DEFAULT 1,
    note           TEXT,
    created_at     TEXT    DEFAULT (datetime('now')),
    classification TEXT    DEFAULT 'CUI',
    tenant_id      TEXT
);

CREATE INDEX IF NOT EXISTS idx_fa_xp_ledger_user    ON fa_xp_ledger(user_id);
CREATE INDEX IF NOT EXISTS idx_fa_xp_ledger_reason  ON fa_xp_ledger(user_id, reason);
CREATE INDEX IF NOT EXISTS idx_fa_xp_ledger_earned  ON fa_xp_ledger(user_id, is_attendance);

-- ── Backfill 1: attendance, from the surviving daily-login rows ──────────────
INSERT INTO fa_xp_ledger
    (user_id, xp_delta, reason, source_type, source_id, is_attendance, verified, note)
SELECT dl.user_id, dl.xp_awarded, 'daily_login', 'daily_login', dl.id, 1, 1,
       'backfilled from fa_daily_logins'
  FROM fa_daily_logins dl
 WHERE dl.xp_awarded IS NOT NULL
   AND dl.xp_awarded <> 0
   AND NOT EXISTS (
        SELECT 1 FROM fa_xp_ledger l
         WHERE l.user_id = dl.user_id
           AND l.reason = 'daily_login'
           AND l.source_type = 'daily_login'
           AND l.source_id = dl.id);

-- ── Backfill 2: earned XP, from steps that were actually completed ───────────
-- xp_partial is the step's face value. The multipliers applied at award time
-- (speed bonus, hint penalty) are NOT recoverable from stored state, so these rows
-- are marked unverified: the reason and the source are true, the amount is a
-- reconstruction. The opening_balance row below absorbs the difference.
INSERT INTO fa_xp_ledger
    (user_id, xp_delta, reason, source_type, source_id, is_attendance, verified, note)
SELECT sp.user_id,
       COALESCE((SELECT s.xp_partial FROM fa_mission_steps s WHERE s.id = sp.step_id), 0),
       'step_pass', 'step', sp.step_id, 0, 0,
       -- No semicolon in this literal: statement splitters that are not
       -- string-aware would cut the INSERT in half here.
       'backfilled from fa_step_progress, face value, multipliers not recoverable'
  FROM fa_step_progress sp
 WHERE sp.status = 'completed'
   AND NOT EXISTS (
        SELECT 1 FROM fa_xp_ledger l
         WHERE l.user_id = sp.user_id
           AND l.reason = 'step_pass'
           AND l.source_type = 'step'
           AND l.source_id = sp.step_id);

-- ── Backfill 3: name the remainder instead of hiding it ──────────────────────
-- Whatever fa_users.xp still exceeds the sum of reconstructed rows has no surviving
-- source. One row per user, flagged unverified, carrying no source. This is the
-- honest residue of a system that never recorded provenance, and it shrinks to
-- nothing for every learner enrolled after this migration.
INSERT INTO fa_xp_ledger
    (user_id, xp_delta, reason, source_type, source_id, is_attendance, verified, note)
SELECT u.id,
       u.xp - COALESCE((SELECT SUM(l.xp_delta) FROM fa_xp_ledger l WHERE l.user_id = u.id), 0),
       'opening_balance', NULL, NULL, 0, 0,
       'pre-ledger XP with no surviving source row, not attributable to any work'
  FROM fa_users u
 WHERE u.xp - COALESCE((SELECT SUM(l.xp_delta) FROM fa_xp_ledger l WHERE l.user_id = u.id), 0) <> 0
   AND NOT EXISTS (
        SELECT 1 FROM fa_xp_ledger l2
         WHERE l2.user_id = u.id AND l2.reason = 'opening_balance');
