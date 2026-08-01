-- CUI // SP-CTI
-- Migration 284: genesis_audit health-trend indexes (crx-gen-02)
--
-- tools/genesis/reflex_health.py computes per-reflex failure-rate and duration
-- percentiles over rolling 7/30-day windows and scans recent failures per
-- reflex for critical-failure alerting. Those queries filter genesis_audit by
-- (reflex_name, created_at) and (event_type, created_at). The existing
-- single-column indexes force the planner to choose one predicate and filter
-- the rest; these composite indexes make both rollups index-only-friendly.
--
-- Additive, idempotent (CREATE INDEX IF NOT EXISTS). genesis_audit is an
-- append-only NIST AU table — indexes only, no schema/row changes.

CREATE INDEX IF NOT EXISTS idx_genesis_audit_reflex_created ON genesis_audit(reflex_name, created_at);
CREATE INDEX IF NOT EXISTS idx_genesis_audit_type_created ON genesis_audit(event_type, created_at);
