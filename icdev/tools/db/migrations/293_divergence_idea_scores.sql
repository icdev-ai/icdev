-- CUI // SP-CTI
-- Migration 293: divergence_idea_scores — persisted output of the divergence
-- critic (dvg-critic-01, tools/quality/divergence_critic.py).
--
-- The Focus half of divergent ideation scores each candidate idea from an
-- invoke_divergence run on novelty / viability / fit. The LLM emits categorical
-- enums; Python composes the composite + ordering. Rows are keyed by the run
-- trace_id so a scored pool is auditable after the fact.
--
-- Mutable score table (a re-score of the same pool inserts new rows under the
-- same trace_id; readers take the latest run), NOT append-only audit — so it is
-- intentionally NOT in APPEND_ONLY_TABLES. RLS: carries tenant_id (NOT NULL) +
-- classification so it filters through the standard global get_connection
-- predicate. TEXT-only, dialect-neutral (mirrors migrations 289-292); the
-- runtime tolerates a missing table (persistence is best-effort).

CREATE TABLE IF NOT EXISTS divergence_idea_scores (
    id                 TEXT PRIMARY KEY,
    trace_id           TEXT NOT NULL,
    function           TEXT NOT NULL,
    idea_index         INTEGER NOT NULL,
    frame              TEXT DEFAULT '',
    idea_text          TEXT NOT NULL,
    novelty            TEXT DEFAULT 'unknown',
    viability          TEXT DEFAULT 'unknown',
    fit                TEXT DEFAULT 'unknown',
    composite          REAL DEFAULT 0.0,
    rationale          TEXT DEFAULT '',
    vocabulary_version TEXT DEFAULT '',
    tenant_id          TEXT NOT NULL DEFAULT 'default',
    classification     TEXT NOT NULL DEFAULT 'CUI'
        CHECK (classification IN ('PUBLIC', 'CUI', 'ECI', 'SECRET', 'TOP SECRET', 'TOP SECRET//SCI')),
    created_at         TEXT
);

CREATE INDEX IF NOT EXISTS idx_divergence_scores_trace ON divergence_idea_scores (trace_id);
CREATE INDEX IF NOT EXISTS idx_divergence_scores_tenant ON divergence_idea_scores (tenant_id);
