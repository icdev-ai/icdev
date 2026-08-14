-- CUI // SP-CTI
-- ungated_test_baseline — per-file pass/fail memory for the ungated_test_drift reflex.
--
-- The reflex reports TRANSITIONS (pass -> fail), not absolute failures, because
-- ~1,823 test modules are grandfathered out of CI and an unknown number are
-- already red. Re-reporting those every cycle would bury the one line that
-- matters. Detecting a transition requires remembering the previous verdict,
-- which is all this table is.
--
-- NOT append-only: one row per path, updated in place. The history that matters
-- is "what was it last time", and a growing log of 1,823 files x every 6h would
-- cost more than it tells anyone.
CREATE TABLE IF NOT EXISTS ungated_test_baseline (
    path           TEXT PRIMARY KEY,
    -- 'pass' | 'fail'. Never 'unknown': a file the runner could not spawn at all
    -- leaves the previous verdict alone rather than overwriting it with noise.
    status         TEXT NOT NULL,
    first_seen     TEXT,
    last_checked   TEXT,
    -- pytest's own summary line, so a card can say what broke without re-running.
    last_detail    TEXT,
    classification TEXT DEFAULT 'CUI'
);

-- The sampler orders by last_checked ASC to reach never-checked files first.
CREATE INDEX IF NOT EXISTS idx_ungated_test_baseline_last_checked
    ON ungated_test_baseline (last_checked);
