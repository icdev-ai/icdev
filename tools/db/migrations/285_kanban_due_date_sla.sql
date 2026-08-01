-- CUI // SP-CTI
-- Migration 285: kanban_tasks due_date + sla_hours (crx-kan-01)
--
-- Adds SLA / due-date tracking so the board can badge overdue and at-risk
-- tasks (tools/kanban/metrics.py::sla_snapshot). Both columns are nullable and
-- opt-in per task — existing rows and the runner are unaffected. Cycle-time
-- metrics come from the existing append-only kanban_status_transitions timeline,
-- so no new history table is needed.
--
-- ADD COLUMN IF NOT EXISTS is idempotent on PostgreSQL and avoids the
-- ACCESS EXCLUSIVE re-lock that an unconditional ADD COLUMN would queue behind
-- the active runner's readers (kanban_tasks lock-storm guard). The SQLite init
-- fallback backfills these via the conditional-ALTER path in kanban/init_db.py.

ALTER TABLE kanban_tasks ADD COLUMN IF NOT EXISTS due_date  TEXT;
ALTER TABLE kanban_tasks ADD COLUMN IF NOT EXISTS sla_hours INTEGER;
