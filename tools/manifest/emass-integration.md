# eMASS Integration

> Shard of `tools/manifest.md`. See index at `tools/manifest.md`.

## eMASS Integration
| Tool | File | Description | Input | Output |
|------|------|-------------|-------|--------|
| eMASS Client | tools/compliance/emass/emass_client.py | REST API client for eMASS (PKI auth) | — | — |
| eMASS Export | tools/compliance/emass/emass_export.py | Export controls, POA&M, artifacts in eMASS format | --project-id, --type | Export file paths |
| eMASS Sync [DEPRECATED] | tools/compliance/emass/emass_sync.py | Sync orchestrator (API/export/hybrid) for eMASS | --project-id, --mode | Sync results |

