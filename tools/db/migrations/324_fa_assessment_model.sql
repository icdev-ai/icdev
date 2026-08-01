-- CUI // SP-CTI
-- Migration 324: the FORGE Academy assessment model (aca-trn-01).
--
-- The INT epic made grading server-authoritative. It could not make it MEAN
-- anything, because there was nothing to grade: all 32 reflect steps in
-- BUILTIN_STEPS are free text, none carries a question/options/correct key, and so
-- _grade_reflect returned assessed=False for every one of them. A step was 100 or 0
-- (grading._verdict and db.record_step_attempt both hardcode it), attempts were
-- unbounded and uncounted, and constants.CERT_TIERS declared an
-- assessment_score_min of 70 that check_cert_eligibility never read.
--
-- These three tables are the model. See
-- docs/features/forge-academy-aca-trn-01-assessment-model.md for the spec.
--
-- Portable across PostgreSQL and the SQLite test/fallback backend:
--   * CREATE TABLE IF NOT EXISTS only. Deliberately NO ALTER TABLE on
--     fa_mission_steps for the per-step policy — "ADD COLUMN IF NOT EXISTS" is
--     PostgreSQL-only and a bare "ADD COLUMN" raises on the second run against
--     SQLite, so the policy gets its own table instead.
--   * No semicolon appears inside any string literal: statement splitters that are
--     not string-aware cut a statement in half at the first one.

-- ---------------------------------------------------------------------------
-- The item bank.
-- ---------------------------------------------------------------------------
-- correct_index is SERVER-ONLY. It must never be selected into a client payload:
-- the whole point of aca-int-03 was that the answer key stopped being readable in
-- page source, and an item bank that re-leaks it would undo that. The browser
-- receives option TEXT in a per-attempt permutation and nothing else.
CREATE TABLE IF NOT EXISTS fa_assessment_items (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    step_id       INTEGER NOT NULL,
    -- Stable per-step identifier, authored in the YAML bank. Answers are posted
    -- keyed on this rather than on a position, so a reordered bank does not
    -- silently re-point a recorded answer at a different question.
    item_key      TEXT    NOT NULL,
    prompt        TEXT    NOT NULL,
    -- JSON array of option strings, in AUTHORED order. The order a learner sees is
    -- a per-attempt permutation recorded in fa_step_attempts.served_json.
    options_json  TEXT    NOT NULL DEFAULT '[]',
    correct_index INTEGER NOT NULL DEFAULT 0,
    explanation   TEXT,
    -- Authored, recorded, and not yet used to select: adaptive selection needs item
    -- statistics, which need attempt volume, which needs this table to exist first.
    difficulty    TEXT    DEFAULT 'core',
    is_active     INTEGER NOT NULL DEFAULT 1,
    classification TEXT   DEFAULT 'CUI',
    tenant_id     TEXT,
    created_at    TEXT    DEFAULT (datetime('now')),
    UNIQUE(step_id, item_key)
);

CREATE INDEX IF NOT EXISTS idx_fa_items_step ON fa_assessment_items(step_id);

-- ---------------------------------------------------------------------------
-- The attempt ledger. APPEND-ONLY (registered in APPEND_ONLY_TABLES).
-- ---------------------------------------------------------------------------
-- An attempt limit whose ledger can be UPDATEd away is not a limit, and fa_xp_ledger
-- cites these rows as the provenance of an award. Corrections are new rows: an
-- instructor reset is a kind='reset' row, never a DELETE of the attempts it forgives.
--
-- served_json is the load-bearing column. It records WHICH items were drawn and, for
-- each, the permutation that maps the displayed option positions back to the
-- authored ones. Without it the indices the client posts are meaningless — which is
-- exactly why reading the DOM buys an attacker nothing.
CREATE TABLE IF NOT EXISTS fa_step_attempts (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id      INTEGER NOT NULL,
    step_id      INTEGER NOT NULL,
    -- 'attempt' — a served/graded attempt.  'reset' — an instructor forgiving the
    -- attempts recorded before it (aca-trn-04 owns the UI, the audit row is here).
    kind         TEXT    NOT NULL DEFAULT 'attempt',
    attempt_num  INTEGER NOT NULL DEFAULT 1,
    policy       TEXT    NOT NULL DEFAULT 'practice',
    served_json  TEXT    NOT NULL DEFAULT '[]',
    answers_json TEXT,
    score_pct    INTEGER,
    passed       INTEGER,
    -- NULL until graded. An open attempt is what a page refresh returns to, so a
    -- refresh cannot reroll the draw or consume a second attempt.
    closed_at    TEXT,
    reason       TEXT,
    actor        TEXT,
    classification TEXT  DEFAULT 'CUI',
    tenant_id    TEXT,
    created_at   TEXT    DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_fa_attempts_user_step ON fa_step_attempts(user_id, step_id);
CREATE INDEX IF NOT EXISTS idx_fa_attempts_step ON fa_step_attempts(step_id);

-- ---------------------------------------------------------------------------
-- Per-step assessment policy. Absent row = practice defaults.
-- ---------------------------------------------------------------------------
-- pass_threshold_pct and items_per_attempt are NULLable on purpose: NULL means "use
-- the constant", so raising STEP_PASS_THRESHOLD_PCT moves every step that never
-- asked for something different, instead of leaving 200 stale copies of an old
-- default behind in the database.
CREATE TABLE IF NOT EXISTS fa_step_assessment_policy (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    step_id           INTEGER NOT NULL,
    policy            TEXT    NOT NULL DEFAULT 'practice',
    items_per_attempt INTEGER,
    pass_threshold_pct INTEGER,
    max_attempts      INTEGER,
    updated_at        TEXT,
    created_at        TEXT    DEFAULT (datetime('now')),
    UNIQUE(step_id)
);
