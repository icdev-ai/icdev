-- Migration 243: CLIN-level obligation tracking (prop-ctr-02)
--
-- Obligations are tracked at the CLIN level in DoD contracting practice, then
-- roll up to the contract. tools/iqe/adapters/cpmp.py::clins_adapter already
-- queries cpmp_clins.obligated_value (silently swallowed by a broad except,
-- so the CLINs IQE collection has always returned empty) -- this column
-- never existed until now.

ALTER TABLE cpmp_clins ADD COLUMN IF NOT EXISTS obligated_value REAL DEFAULT 0.0;
