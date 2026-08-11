-- CUI // SP-CTI
-- Give the stale-reaper a handle on the process it is reaping.
--
-- The reaper's only handle was the in-memory _running dict. When the scheduler
-- restarts, that dict is empty while the subprocesses it spawned are still
-- running, so a reap flips the task status and leaves the process tree alive.
-- The scheduler then re-dispatches, a second tree spawns, and the first wedges
-- forever holding its worktree and its port.
--
-- Measured on task-e2e-ebf5ab21 (2026-08-10): three stale-reaper -> backlog
-- transitions, failure_count 3, and an orphaned Playwright tree still listening
-- on 5090 whose launcher had already exited. Two more cycles would have
-- quarantined the task at fc>=5 and leaked two more trees.
--
-- started_at is stored alongside the pid because a pid alone is not safe to
-- kill: pids are reused, and killing the wrong process is far worse than
-- failing to kill the right one.
ALTER TABLE kanban_tasks ADD COLUMN dispatch_pid INTEGER;
ALTER TABLE kanban_tasks ADD COLUMN dispatch_pid_started_at TEXT;
