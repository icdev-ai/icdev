-- Migration 225: Add session/grader model tracking to agent_evals
-- Enables cross-grader enforcement — tracks which model ran the session
-- and which model graded it. These must be different per ACE policy.

ALTER TABLE agent_evals ADD COLUMN IF NOT EXISTS session_model_id TEXT DEFAULT '';
ALTER TABLE agent_evals ADD COLUMN IF NOT EXISTS grader_model_id  TEXT DEFAULT '';

COMMENT ON COLUMN agent_evals.session_model_id IS 'model_id of the LLM that ran the agent session';
COMMENT ON COLUMN agent_evals.grader_model_id  IS 'model_id of the LLM that graded the output — must differ from session_model_id';
