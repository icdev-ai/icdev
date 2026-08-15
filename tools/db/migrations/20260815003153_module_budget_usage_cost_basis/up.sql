-- Migration: 20260815003153_module_budget_usage_cost_basis
-- CUI // SP-CTI
--
-- Add module_budget_usage.cost_basis so a $0.00 cost is interpretable.
--
-- Measured on the live board 2026-08-15: module_budget_usage holds 1,391 rows
-- whose amount column sums to EXACTLY 0.00 - max(amount) is 0.0 and not one row
-- is above zero - including 557 calls on kimi-k2.6:cloud. LLMResponse.cost_usd
-- is documented "when provider computes it" and no provider computes it, so the
-- router recorded the 0.0 default on every call ever made. The consequence is
-- that generative_intelligence's $150 monthly USD cap sat at 0% and could never
-- fire, leaving the token cap as the only working control - and that token cap
-- then blocked work on FREE local inference (183,862 of 418,801 tokens were
-- ollama-local, genuinely $0).
--
-- The router now derives cost from the per-model pricing block. That alone is
-- not enough, because 30 of the 39 configured models carry
-- {input_per_1k: 0.0, output_per_1k: 0.0}: for the ollama-local ones 0.0 is the
-- truth, and for claude-opus it plainly is not. Without a basis those two are
-- the same number again and the reader is back to the original defect.
--
--     priced      computed from a real per-1k price
--     local_zero  local provider - $0 IS the cost
--     unpriced    non-local model with no price in the table - UNKNOWN
--
-- `unpriced` is deliberately still stored as amount = 0.0. Inventing a price to
-- avoid a zero would manufacture a spend figure, which is worse than an honest
-- unknown - but the label makes the gap COUNTABLE, so
--   SELECT COUNT(*) FROM module_budget_usage WHERE cost_basis = 'unpriced'
-- reports how much of the budget picture is guesswork instead of hiding it.
--
-- Nullable with no default backfill: rows written before this migration have no
-- recorded basis and must not be relabelled as though they did. NULL means
-- "written before the basis existed", which is a different fact from `unpriced`.

-- @pg-only
ALTER TABLE module_budget_usage
    ADD COLUMN IF NOT EXISTS cost_basis TEXT;

-- @pg-only
CREATE INDEX IF NOT EXISTS idx_mbu_cost_basis
    ON module_budget_usage (cost_basis) WHERE cost_basis IS NOT NULL;

-- @sqlite-only
-- SQLite cannot ADD COLUMN IF NOT EXISTS. init/_ensure_tables creates the table
-- fresh with the column, so this is a no-op there; an existing SQLite file
-- picks it up on the next table create. SQLite is an init-only fallback here.
SELECT 1;
