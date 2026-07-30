-- CUI // SP-CTI
-- Migration 313: reconcile fa_mission_progress after the GET-write fix (aca-int-04).
--
-- blueprint.mission_runner called start_mission() on every GET of a mission page,
-- and that function did `SET status='in_progress', attempts=attempts+1`
-- unconditionally. So `attempts` counted page views and any visit re-opened a
-- completed mission. The live board showed 39 rows in_progress carrying 352
-- attempts (m01-llm-fundamentals alone: 26) while fa_step_progress was COMPLETELY
-- EMPTY â€” not one step had ever been submitted, so every recorded attempt was
-- somebody opening a page.
--
-- The code no longer writes on GET. This migration cleans up what it already
-- wrote, under one rule keyed on real evidence:
--
--   1. A non-completed row with NO fa_step_progress evidence for that mission was
--      never really started. Delete it.
--   2. Every surviving non-completed row has `attempts` reset to the number of
--      recorded step submissions for that mission â€” a truthful lower bound.
--      Page views are not attempts and cannot be recovered as such.
--
-- Completed missions are never touched by either statement: a completion is real
-- regardless of how its attempt counter got inflated, and deleting one would
-- withdraw a certificate gate. Rule 2 is also restricted to non-completed rows so
-- a mission completed before per-step progress was recorded does not get its
-- attempts zeroed.
--
-- Idempotent: re-running finds nothing to delete and recomputes the same counts.
-- Portable across PostgreSQL and the SQLite test/fallback backend â€” correlated
-- subqueries only, no dialect-specific JSON or UPDATE...FROM.

-- 1. Drop rows that only ever recorded page views.
DELETE FROM fa_mission_progress
WHERE status <> 'completed'
  AND completed_at IS NULL
  AND NOT EXISTS (
        SELECT 1
        FROM fa_step_progress sp
        JOIN fa_mission_steps s ON s.id = sp.step_id
        WHERE sp.user_id = fa_mission_progress.user_id
          AND s.mission_id = fa_mission_progress.mission_id
  );

-- 2. Re-base the surviving attempt counters on recorded submissions.
UPDATE fa_mission_progress
SET attempts = (
        SELECT COUNT(*)
        FROM fa_step_progress sp
        JOIN fa_mission_steps s ON s.id = sp.step_id
        WHERE sp.user_id = fa_mission_progress.user_id
          AND s.mission_id = fa_mission_progress.mission_id
    )
WHERE status <> 'completed';
