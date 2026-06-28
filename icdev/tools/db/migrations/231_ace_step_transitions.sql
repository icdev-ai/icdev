-- CUI // SP-CTI
-- Migration 207: ACE Markov step sequencer — stores step-to-step transition history.
-- Enables the MarkovSequencer to learn role step orderings from execution history
-- and recommend improved step sequences via Markov transition probabilities.

CREATE TABLE IF NOT EXISTS ace_step_transitions (
    id          BIGSERIAL PRIMARY KEY,
    role_id     TEXT NOT NULL,
    from_step   TEXT NOT NULL,
    to_step     TEXT NOT NULL,
    success     BOOLEAN NOT NULL DEFAULT TRUE,
    session_id  TEXT,
    tenant_id   TEXT,
    created_at  TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_ace_step_trans_role
    ON ace_step_transitions (role_id, from_step);

CREATE INDEX IF NOT EXISTS idx_ace_step_trans_session
    ON ace_step_transitions (session_id);
