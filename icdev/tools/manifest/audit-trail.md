# Audit Trail

> Shard of `tools/manifest.md`. See index at `tools/manifest.md`.

## Audit Trail
| Tool | File | Description | Input | Output |
|------|------|-------------|-------|--------|
| Audit Logger | tools/audit/audit_logger.py | Append-only audit trail writer (NIST AU) | --event, --actor, --action, --project | Entry ID |
| Audit Query | tools/audit/audit_query.py | Query audit trail (read-only) | --project, --type, --actor, --verify-completeness | Audit entries |
| Decision Recorder | tools/audit/decision_recorder.py | Record decisions with rationale | --project, --decision, --rationale | Entry ID |
| Cross-Agency Transfer Logger | tools/audit/cross_agency_transfer_logger.py | Append-only audit for cross-agency data transfers (NIST AU-2, AU-9) | --query, --transfer-id | Transfer audit events |

