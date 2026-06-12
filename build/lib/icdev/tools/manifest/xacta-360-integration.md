# Xacta 360 Integration

> Shard of `tools/manifest.md`. See index at `tools/manifest.md`.

## Xacta 360 Integration
| Tool | File | Description | Input | Output |
|------|------|-------------|-------|--------|
| Xacta Client | tools/compliance/xacta/xacta_client.py | REST API client for Xacta 360 (PKI auth) | — | — |
| Xacta Export | tools/compliance/xacta/xacta_export.py | OSCAL JSON + CSV export for Xacta import | --project-id, --format | Export file paths |
| Xacta Sync | tools/compliance/xacta/xacta_sync.py | Sync orchestrator (API/export/hybrid) | --project-id, --mode | Sync results |

