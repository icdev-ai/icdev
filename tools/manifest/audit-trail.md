# Audit Trail

> Shard of `tools/manifest.md`. See index at `tools/manifest.md`.

## Audit Trail
| Tool | File | Description | Input | Output |
|------|------|-------------|-------|--------|
| Audit Logger | tools/audit/audit_logger.py | Append-only audit trail writer (NIST AU) | --event, --actor, --action, --project | Entry ID |
| Audit Query | tools/audit/audit_query.py | Query audit trail (read-only) | --project, --type, --actor, --verify-completeness | Audit entries |
| Decision Recorder | tools/audit/decision_recorder.py | Record decisions with rationale | --project, --decision, --rationale | Entry ID |
| Cross-Agency Transfer Logger | tools/audit/cross_agency_transfer_logger.py | Append-only audit for cross-agency data transfers (NIST AU-2, AU-9) | --query, --transfer-id | Transfer audit events |
| GovCon Audit Write Verifier | tools/testing/verify_govcon_audit_writes.py | Probe every tools/govcon `_audit()` against the live backend and confirm the row lands; each `_audit` swallows its own exception, so a static check cannot prove the write is accepted | --commit, --json | Per-module pass/fail; exit 0 iff every module inserted |
| Audit Store | tools/audit/store.py | Read-only merged query layer over audit_trail + hook_events (library; backs `icdev audit tail`). Also `read_runtime_invocations()` — row-level reader for the `runtime_invocations` telemetry table (migration 341), which had a writer and a rollup but no way to read individual rows. That table is NOT folded into `tail()`: it is telemetry, not NIST AU evidence, and far higher volume | `tail(AuditFilter(project_id, event_types, actor, since, sources, limit))`; `read_runtime_invocations(limit, **{surface, name, status, session_id, project_id, parent_id, error_class, since, min_duration_ms})` | Normalized event dicts, newest-first |
| Audit Tail CLI | tools/cli/audit_tail.py | `icdev audit tail` — terminal reader for the audit feed, with --follow | --follow, --json, --project, --event-type, --since, --source, --list-types | Formatted or JSONL event stream |

