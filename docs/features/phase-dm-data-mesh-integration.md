# CUI // SP-CTI
# Data Mesh Integration — Phase DM

**Status:** Complete  
**Date:** 2026-06-14  
**Task prefix:** `dm-`  
**Epics:** found · domain · prod · contract · gov · csp · portal · wire

---

## Summary

Six new pages integrated into the Data Design Canvas (DDC) at `/data/` implementing all four Data Mesh pillars using open-source tooling and CSP-native service bridges.

## Pages Delivered

| Route | Title | IQE Canvas | Status |
|-------|-------|-----------|--------|
| `/data/domains` | Domain Registry | `data_mesh_domains` | ✅ |
| `/data/products` | Data Products | `data_mesh_products` | ✅ |
| `/data/contracts` | Data Contracts | `data_mesh_contracts` | ✅ |
| `/data/governance` | Data Mesh Governance | `data_mesh_governance` | ✅ |
| `/data/csp` | Cloud Service Provider Sync | `data_mesh_csp` | ✅ |
| `/data/mesh` | Data Mesh Control Plane (Hub) | `data_mesh_hub` | ✅ |

## Architecture

### Python Modules (`tools/data_canvas/data_mesh/`)
- `domain_manager.py` — Domain CRUD + maturity scoring
- `product_registry.py` — Product registry + SLA + subscriptions + discoverability
- `contract_engine.py` — ODCS contract lint + test (internal or datacontract-cli)
- `governance_engine.py` — OPA REST client + local Rego eval fallback + audit log
- `lineage_emitter.py` — OpenLineage event emission (internal or openlineage-python)
- `csp/aws_datazone.py` — AWS DataZone dry-run/live sync adapter
- `csp/azure_purview.py` — Azure Purview dry-run/live sync adapter
- `csp/gcp_dataplex.py` — GCP Dataplex dry-run/live sync adapter

### Database Tables (PostgreSQL `icdev` db)
9 new `dm_*` tables: `dm_domains`, `dm_data_products`, `dm_contracts`, `dm_input_ports`, `dm_output_ports`, `dm_domain_maturity`, `dm_catalog_entries`, `dm_audit`, `dm_product_slas`, `dm_product_subscriptions`  
Plus pre-existing: `dm_governance_policies`, `dm_opa_policies`, `dm_csp_sync_log`, `dm_data_contracts`, `dm_contract_test_runs`, `dm_ports`

### IQE Integration
- Adapter: `tools/iqe/adapters/data_mesh.py` — registers 4 collections: `data_mesh.domains`, `data_mesh.products`, `data_mesh.contracts`, `data_mesh.governance_policies`
- Blueprint `/api/iqe-query` extended with `_DDC_CANVAS_COLLECTIONS` map covering all 6 DM canvas names
- Seed queries: `context/iqe/queries/data_mesh/` (4 files)
- IQE widget present on all 6 pages

### Configuration
- `args/data_mesh_config.yaml` — OPA URL, Marquez URL, governance gate, CSP dry-run default, contract test mode
- `requirements.txt` — optional deps commented: `datacontract-cli`, `openlineage-python`

## Bug Fixes Applied This Session
- `list_contracts()` used `ORDER BY name` — fixed to `ORDER BY title` (dm_contracts has `title` column)
- `/api/dm/summary` used `c.name AS title` — fixed to `c.title` in the recent-contracts query
- Missing DM tables in PG (`dm_domains`, `dm_data_products`, `dm_contracts`, etc.) — created via direct psycopg2 migration (init_db SCHEMA split on `;` silently skipped them due to SQLite trigger syntax in schema)
- `domains.html` IQE widget included without canvas variables — added `iqe_canvas`, `iqe_api_route`, `iqe_title`, `iqe_examples`
- `contracts.html`, `governance.html`, `csp.html` had no IQE widget — added full widget blocks

## Playwright Verification
All 6 pages verified rendering correctly:
- `playwright/screenshots/dm-domains.png`
- `playwright/screenshots/dm-products.png`
- `playwright/screenshots/dm-contracts.png`
- `playwright/screenshots/dm-governance.png`
- `playwright/screenshots/dm-csp.png`
- `playwright/screenshots/dm-mesh-fixed.png`

## Coherence Gate
`python tools/workflow/coherence_checker.py --all --fix --gate` — **PASSED** (exit 0)

## Companion Sync
`python tools/dx/companion.py --sync --write --json` — 10 platforms, 63 skills synced
