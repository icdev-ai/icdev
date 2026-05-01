-- Migration: 078_workflow_hitl DOWN
-- CUI // SP-CTI
--
-- Drop tables in reverse dependency order (assignments → members → teams → templates).

DROP TABLE IF EXISTS wf_team_assignments;
DROP TABLE IF EXISTS wf_team_members;
DROP TABLE IF EXISTS wf_teams;
DROP TABLE IF EXISTS wf_templates;
