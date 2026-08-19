-- Migration: 20260819011454_pr_merge_eligibility_events
-- CUI // SP-CTI
--
-- kpr-watch-02: when did this PR become merge-ELIGIBLE, and is it still open?
--
-- THE STATE WORTH PAGING ON. A PR the merge-eligibility ladder
-- (tools/ci/merge_readiness.py::classify_merge_readiness) calls `ready` — green,
-- MERGEABLE, not a draft, no hold label, correct base, not behind — that is STILL
-- open on the next poll means the actor should have merged it and did not. That is
-- not a PR problem, it is an automation-liveness problem, and nothing reported it.
--
-- Answering it needs ONE fact nobody was keeping: WHEN the PR became eligible. The
-- forge does not record it (`updatedAt` moves on a comment or a label), and the
-- watcher's audit rows record ACTIONS, not the moment a refusal stopped applying.
-- Without it "still open" has no age, and an alarm with no age is either silent or
-- constant.
--
-- WHY A TABLE AND NOT A DERIVED ESTIMATE. `max(statusCheckRollup[].completedAt)` is
-- a usable PROXY for the moment a PR went green, and it is what the arming survey
-- for this card was measured with. It is not the same fact: eligibility can arrive
-- LATER than green (a hold label removed, a changes-requested review dismissed, a
-- rebase that cleared `behind_main`), so the proxy reads such a PR as instantly
-- hours old and would alarm on its first sight of it. The estimate is kept as a
-- labelled FALLBACK (`ready_since_source: ci_estimate`) for a PR with no recorded
-- history; it is never silently substituted for a recorded observation.
--
-- APPEND-ONLY. One row per OBSERVED TRANSITION, never per poll: the recorder writes
-- only when (state, head_sha) differs from this PR's newest row. So a PR sitting
-- `ready` for an hour has exactly ONE row, whose observed_at IS its first-seen-ready
-- — no scan, no aggregation, no window function. A 30s poll over ~10 open PRs
-- therefore costs a handful of rows a day rather than ~29,000.
--
-- The head sha is part of the transition key on purpose. A force-push to a `ready`
-- PR is a NEW merge opportunity even if the state string is unchanged, and its clock
-- must restart; keying on state alone would carry the old branch's age onto a branch
-- that no longer exists.
--
-- NEVER UPDATE OR DELETE A ROW HERE. It is registered in APPEND_ONLY_TABLES in
-- .claude/hooks/pre_tool_use.py. An observation that a PR was eligible at 03:14 does
-- not stop being true when it merges at 03:15.

CREATE TABLE IF NOT EXISTS pr_merge_eligibility_events (
    -- AUTOINCREMENT is load-bearing, not decorative. storage.py::translate_sql
    -- rewrites 'INTEGER PRIMARY KEY AUTOINCREMENT' to 'SERIAL PRIMARY KEY' for
    -- PostgreSQL and rewrites nothing without it, so a bare INTEGER PRIMARY KEY
    -- creates a column with NO sequence on the primary backend and every INSERT
    -- that omits 'id' raises a not-null violation there while passing on SQLite.
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    pr_url          TEXT NOT NULL,
    pr_number       INTEGER,
    head_sha        TEXT,               -- NULL when the forge did not report one
    head_ref        TEXT,
    -- The `merge_readiness` state as classified with the `linked` rung SKIPPED, so a
    -- task-linked PR is judged on the same merits as an unlinked one. `linked` is a
    -- statement about WHICH ACTOR OWNS the merge, not about whether the PR is
    -- finished, and collapsing the two hides every stall on the task path.
    state           TEXT NOT NULL,
    -- Denormalised `state = 'ready'`. Stored rather than derived because the ladder
    -- may gain states, and an event row must keep meaning what it meant when written.
    eligible        INTEGER NOT NULL DEFAULT 0,
    -- Which merge path owns this PR: 'linked' (the task path) or 'unlinked' (the
    -- sweep). The repair differs by door, so the alarm must not lose it.
    door            TEXT,
    reason          TEXT,
    observed_at     TIMESTAMP NOT NULL,
    recorded_by     TEXT,               -- 'pr_watcher' | 'cli' | a caller's own tag
    tenant_id       TEXT,
    classification  TEXT DEFAULT 'CUI'  -- the LABEL, never a banner: it feeds the RLS predicate
);

-- The hot query is "the newest row for this url", run once per open PR per report.
CREATE INDEX IF NOT EXISTS idx_pr_merge_elig_url_observed
    ON pr_merge_eligibility_events(pr_url, observed_at DESC);
-- The stall report scans recent eligible rows across all PRs.
CREATE INDEX IF NOT EXISTS idx_pr_merge_elig_eligible
    ON pr_merge_eligibility_events(eligible, observed_at DESC);
