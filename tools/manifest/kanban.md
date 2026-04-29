# Kanban System

> Shard of `tools/manifest.md`. See index at `tools/manifest.md`.

## Kanban Tools

| Tool | File | Description | Input | Output |
|------|------|-------------|-------|--------|
| DES Audit Logger | tools/kanban/des_audit_logger.py | Append-only DES execution audit trail (NIST AU). Inserts dispatch, completion, verification, and gate_override events into `des_execution_events` table — never UPDATE/DELETE. Class `DESAuditLogger` exposes: `log_dispatch(task_id, skill, inputs, executor, source)`, `log_completion(task_id, status, outputs, duration_ms)`, `log_verification(task_id, signals)`, `log_gate_override(task_id, reason, operator)`. Table: `des_execution_events` (migration 065). | `--query --task-id <id> [--json]` | Event ID string / JSON list of events |
| State Machine | tools/kanban/state_machine.py | Kanban task state transition engine — enforces valid status flows (scheduled→in_progress→done/failed) with guard-22 verification gate | Task ID, target status | Transition result |
| Source Stats | tools/kanban/source_stats.py | Kanban task source statistics aggregator — counts tasks by source/type for dashboard metrics | --json | JSON stats dict |
| Kanban Verify | tools/kanban_verify.py | Runs coherence gate and records detailed failure reason in `kanban_verifications`. Parses coherence_checker.py JSON output, extracts the first failing rule/file/line, and stores it as the `reason` field instead of the opaque "unknown". | `--task-id <id> [--json] [--dry-run] [--changed-files f1,f2]` | JSON verification result |
