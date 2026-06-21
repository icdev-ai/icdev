-- 174_dashboard_users_clearance.sql
-- CUI // SP-CTI
-- Bell-LaPadula MAC readiness: add clearance_level + compartments to subject
-- and compartments to proposal/cpmp objects so no-read-up / compartment checks
-- can be enforced at both the row and route level.

ALTER TABLE dashboard_users ADD COLUMN clearance_level TEXT NOT NULL DEFAULT 'CUI';
ALTER TABLE dashboard_users ADD COLUMN compartments TEXT NOT NULL DEFAULT '[]';

-- Proposal opportunities: compartments column for SCI/COI/LAC tagging
ALTER TABLE proposal_opportunities ADD COLUMN compartments TEXT NOT NULL DEFAULT '[]';

-- CPMP contracts: compartments column for SCI/COI/LAC tagging
ALTER TABLE cpmp_contracts ADD COLUMN compartments TEXT NOT NULL DEFAULT '[]';
