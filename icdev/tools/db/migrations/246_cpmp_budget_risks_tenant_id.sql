-- Migration 246: tenant_id backfill for tables missed by migration 245
-- (cpmp_risks, cpmp_budget_* were created after 245's table list was authored)

ALTER TABLE cpmp_risks ADD COLUMN IF NOT EXISTS tenant_id TEXT;
ALTER TABLE cpmp_budget_allocations ADD COLUMN IF NOT EXISTS tenant_id TEXT;
ALTER TABLE cpmp_budget_obligations ADD COLUMN IF NOT EXISTS tenant_id TEXT;
ALTER TABLE cpmp_budget_tier_history ADD COLUMN IF NOT EXISTS tenant_id TEXT;
