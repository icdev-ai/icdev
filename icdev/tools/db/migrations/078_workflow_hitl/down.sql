-- Migration: 078_workflow_hitl DOWN
-- CUI // SP-CTI
--
-- Drop tables in reverse dependency order.

DROP TABLE IF EXISTS wf_external_steps;
DROP TABLE IF EXISTS wf_feedback_insights;
DROP TABLE IF EXISTS wf_feedback;
DROP TABLE IF EXISTS wf_team_assignments;
DROP TABLE IF EXISTS wf_team_members;
DROP TABLE IF EXISTS wf_teams;
DROP TABLE IF EXISTS wf_templates;
