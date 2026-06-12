# Canvas Auto-Remediation

> Shard of `tools/manifest.md`. See index at `tools/manifest.md`.

## Canvas Auto-Remediation
| Tool | File | Description | Input | Output |
|------|------|-------------|-------|--------|
| Canvas Auto-Remediator | tools\canvas\auto_remediator.py | POA&M auto-remediation CLI — applies vendor-neutral design-completeness fixes to approved/pending findings across all 9 canvases (security, observability, boundary, infra, data, network, pipeline, QDC, migration). Pipeline per finding: backup canvas DB → mutate graph_json with per-rule handler → re-run assessment to verify fix → mark finding_approvals.decision='remediated' → append audit_trail row (event_type='vulnerability_resolved'). Supports --dry-run, --list-handlers, --canvas filter. | --finding-hash \<hash\>, --all-pending, --all-approved, --canvas \<name\>, --list-handlers, --dry-run, --gate, --json | JSON remediation report (status, findings processed, remediated count, skipped, errors) |
| Collaboration Manager | tools\canvas\collaboration.py | Session-based multi-user canvas collaboration (join/leave/push/poll); SQLite-backed, air-gap safe, no WebSocket required | canvas_key, db_path, collab_table | dict |


