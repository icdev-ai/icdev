# Audit Trail

> Shard of `tools/manifest.md`. See index at `tools/manifest.md`.

## Audit Trail
| Tool | File | Description | Input | Output |
|------|------|-------------|-------|--------|
| Audit Logger | tools/audit/audit_logger.py | Append-only audit trail writer (NIST AU) | --event, --actor, --action, --project | Entry ID |
| Audit Query | tools/audit/audit_query.py | Query audit trail (read-only) | --project, --type, --actor, --verify-completeness | Audit entries |
| Decision Recorder | tools/audit/decision_recorder.py | Record decisions with rationale | --project, --decision, --rationale | Entry ID |
| Cross-Agency Transfer Logger | tools/audit/cross_agency_transfer_logger.py | Append-only audit for cross-agency data transfers (NIST AU-2, AU-9) | --query, --transfer-id | Transfer audit events |
| Audit Store | tools/audit/store.py | Read-only merged query layer over audit_trail + hook_events (library; backs `icdev audit tail`) | AuditFilter(project_id, event_types, actor, since, sources, limit) | Normalized event dicts, newest-first |
| Audit Tail CLI | tools/cli/audit_tail.py | `icdev audit tail` — terminal reader for the audit feed, with --follow | --follow, --json, --project, --event-type, --since, --source, --list-types | Formatted or JSONL event stream |

