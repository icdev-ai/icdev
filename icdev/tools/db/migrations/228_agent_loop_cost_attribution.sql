-- CUI // SP-CTI
-- Migration 228: cost attribution columns for agent loop sessions and evals
-- Adds tenant_id and user_id to agent_loop_checkpoints and agent_evals so that
-- per-tenant / per-user cost reporting can be produced without joining external tables.

ALTER TABLE agent_loop_checkpoints
  ADD COLUMN IF NOT EXISTS tenant_id TEXT NOT NULL DEFAULT '';

ALTER TABLE agent_loop_checkpoints
  ADD COLUMN IF NOT EXISTS user_id TEXT NOT NULL DEFAULT '';

CREATE INDEX IF NOT EXISTS idx_alc_tenant_id
    ON agent_loop_checkpoints (tenant_id)
    WHERE tenant_id != '';

CREATE INDEX IF NOT EXISTS idx_alc_user_id
    ON agent_loop_checkpoints (user_id)
    WHERE user_id != '';

-- agent_evals cost attribution (grading runs are billable per tenant too)
ALTER TABLE agent_evals
  ADD COLUMN IF NOT EXISTS tenant_id TEXT NOT NULL DEFAULT '';

ALTER TABLE agent_evals
  ADD COLUMN IF NOT EXISTS user_id TEXT NOT NULL DEFAULT '';

CREATE INDEX IF NOT EXISTS idx_ae_tenant_id
    ON agent_evals (tenant_id)
    WHERE tenant_id != '';

CREATE INDEX IF NOT EXISTS idx_ae_user_id
    ON agent_evals (user_id)
    WHERE user_id != '';
