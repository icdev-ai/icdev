# Data Mesh (dm-*)

> Shard of `tools/manifest.md`. See index at `tools/manifest.md`.

## Data Mesh

**Package:** `tools/data_canvas/data_mesh/`
**Routes:** `/data/domains`, `/data/products`, `/data/contracts`, `/data/governance`, `/data/csp`, `/data/mesh`

| Module | Purpose | CLI |
|--------|---------|-----|
| domain_manager.py | Domain CRUD + maturity scoring | No |
| product_registry.py | Product registry + SLA + subscriptions + discoverability | No |
| contract_engine.py | ODCS contract lint + test (datacontract-cli or internal) | No |
| governance_engine.py | OPA client + local policy eval + audit log | No |
| lineage_emitter.py | OpenLineage event emission (openlineage-python or internal) | No |
| csp/__init__.py | CSP status + sync router | No |
| csp/aws_datazone.py | AWS DataZone sync (boto3 optional) | No |
| csp/azure_purview.py | Azure Purview sync (azure-purview-catalog optional) | No |
| csp/gcp_dataplex.py | GCP Dataplex sync (google-cloud-dataplex optional) | No |

## DB Tables

| Table | Description |
|-------|-------------|
| dm_domains | Data mesh domain registry with ownership and maturity score |
| dm_data_products | Data product catalog with SLA targets and discoverability metadata |
| dm_product_slas | SLA definitions per data product |
| dm_product_subscriptions | Consumer subscriptions to data products |
| dm_data_contracts | ODCS-compliant data contracts per product |
| dm_contract_test_runs | Contract lint/test run history (append-only) |
| dm_opa_policies | OPA governance policy store |
| dm_policy_audit_log | Append-only audit log of policy evaluation events (NIST AU) |
| dm_csp_sync_log | CSP sync operation history (DataZone / Purview / Dataplex) |

## Config

`args/data_mesh_config.yaml`

## Open Source Integrations

| Tool | Role |
|------|------|
| OpenMetadata | Data catalog (entity sync) |
| OpenLineage + Marquez | Lineage event emission and tracking |
| datacontract-cli / ODCS | Contract linting and testing |
| OPA (Open Policy Agent) | Governance policy evaluation |
| pyiceberg | Open table / storage format support |
| Redpanda | Streaming data product delivery |
| Dagster | Orchestration of data product pipelines |

## CSP Native Integrations

| CSP | Service | Module |
|-----|---------|--------|
| AWS | DataZone + Lake Formation | csp/aws_datazone.py |
| Azure | Purview | csp/azure_purview.py |
| GCP | Dataplex | csp/gcp_dataplex.py |
