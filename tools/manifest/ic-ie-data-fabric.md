# IC IE Data Fabric

> Shard of `tools/manifest.md`. See index at `tools/manifest.md`.

## IC IE Multi-Agency Data Sharing

| Tool | File | Description | Input | Output |
|------|------|-------------|-------|--------|
| DataFabricService | icdev/tools/ic_ie/data_fabric.py | Multi-agency data sharing via IC Information Environment data fabric with classification-aware policy enforcement and append-only audit trail | backend="sqlite", db_path=None | Registered asset dicts, share records, audit entries |

### Operations
| Method | Description | NIST Controls |
|--------|-------------|---------------|
| `register_asset(classification, owner_agency, metadata, asset_id)` | Register a data asset in the IC IE catalog | AC-3, SC-28 |
| `get_asset(asset_id)` | Retrieve an asset by fabric id | AC-3 |
| `share_asset(asset_id, target_agency, justification)` | Share asset with policy enforcement (NOFORN check) | AC-4, AU-2, AU-12 |
| `get_asset_for_agency(asset_id, agency)` | Retrieve asset if agency is authorized | AC-3, AC-4 |
| `query_shared_assets(agency)` | Return all assets accessible to an agency | AC-3 |
| `revoke_share(asset_id, target_agency)` | Revoke an active share | AC-4, AU-2, AU-12 |
| `get_audit_entries(event_type)` | Query append-only audit trail | AU-3, AU-12 |

### Policy Rules
- NOFORN assets (`//NOFORN` in classification string) cannot be shared with foreign partners (agencies starting with `FVEY_`, `NATO_`, or `FOREIGN_`).
- All share and revocation operations emit an immutable audit trail entry (NIST AU-2, AU-3, AU-12).
- Audit table rows are never updated or deleted (NIST AU-9 append-only requirement).
