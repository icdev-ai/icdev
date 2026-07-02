-- Migration 230: DIC Tech Writer Workspace columns
-- Adds template_type (doc kind) and writeguard_mode (active WG content mode)
-- to dic_documents so the tech writer workspace can filter and route analysis.
-- storage.py::translate_sql handles ADD COLUMN → ADD COLUMN IF NOT EXISTS for PG idempotency.

ALTER TABLE dic_documents ADD COLUMN template_type TEXT DEFAULT NULL
    CHECK (template_type IN (
        'STANDARD_GUIDE','SOP','RUNBOOK','ARCH_NETWORK','ARCH_APPLICATION','ARCH_SYSTEM'
    ));

ALTER TABLE dic_documents ADD COLUMN writeguard_mode TEXT DEFAULT 'default';
