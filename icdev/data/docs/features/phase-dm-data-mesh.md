# Data Mesh — ODCS-Compliant Domain/Product/Contract Architecture
**Classification:** CUI // SP-CTI  
**Phase:** dm-* (8-epic track)  
**Date:** 2026-05-29  
**Status:** Complete (backend + routes); templates pre-existing

---

## What Was Built

A full Data Mesh control plane inside the Data Design Canvas (`/data/`), covering all four Data Mesh pillars: Domain Ownership, Data Products, Data Contracts, and Federated Governance.

---

## Architecture

### 8-Epic Track

| Epic | Task IDs | Description | Status |
|------|----------|-------------|--------|
| Foundation | dm-found-01/02 | DB schema + constants | ✅ |
| Domains | dm-domain-01/02 | `domain_manager.py` + blueprint routes | ✅ |
| Products | dm-prod-01/02 | `product_registry.py` + blueprint routes | ✅ |
| Contracts | dm-contract-01/02 | `contract_engine.py` + blueprint routes | ✅ |
| Governance | dm-gov-* | `governance_engine.py` (pre-existing) | ✅ |
| CSP | dm-csp-01 | AWS / Azure / GCP adapters + sync router | ✅ |
| Portal/Hub | dm-portal-* | `mesh.html` control plane + nav link | ✅ |
| Wiring | dm-wire-* | Config, manifest shard, feature doc | ✅ |

---

## New Backend Modules

### `tools/data_canvas/data_mesh/domain_manager.py`
CRUD + maturity scoring for `dm_domains`.

- `list_domains()` — all domains ordered by name
- `get_domain(id)` — single domain by ID
- `create_domain(data)` — insert with owner_team, owner_email, maturity_level, classification
- `update_domain(id, data)` — partial update of allowed fields
- `delete_domain(id)` — refuses if products reference the domain (409)
- `compute_domain_maturity(id)` — score = `products×0.4 + contracts×0.3 + policies×0.3`; labels: defined / managed / optimizing
- `list_domain_products(id)` — products scoped to a domain

### `tools/data_canvas/data_mesh/product_registry.py`
Product catalog with SLA tracking, subscriptions, and discoverability scoring.

- `list_products(domain_id, status)` — filtered listing
- `get_product(id)`, `create_product(data)`, `update_product(id, data)`, `delete_product(id)`
- `get_product_slas(product_id)` — SLA definitions from `dm_product_slas`
- `add_product_sla(product_id, data)` — add uptime/latency/freshness SLA
- `subscribe_to_product(product_id, data)` — consumer subscription (pending approval)
- `approve_subscription(sub_id)` — approve subscription
- `compute_discoverability_score(product_id)` — 5 dimensions × 20pts each:
  - has_description, has_slas, has_contract, has_lineage, has_quality
  - Labels: Undiscoverable (<40) / Emerging (40–59) / Discoverable (60–79) / Trusted (≥80)

### `tools/data_canvas/data_mesh/contract_engine.py`
ODCS v1.1+ compatible contract management with lint and test.

- `list_contracts(domain_id, product_id)` — from `dm_data_contracts`
- `get_contract(id)`, `create_contract(data)`, `update_contract(id, data)`, `delete_contract(id)`
- `validate_yaml_structure(yaml_text)` — checks required ODCS fields
- `lint_contract(yaml_text)` — full lint: score = 100 - 20×errors - 5×warnings
- `test_contract(id, conn_params)` — tries `datacontract-cli`; falls back to lint; stores run in `dm_contract_test_runs`

### `tools/data_canvas/data_mesh/csp/`
Three CSP adapters with graceful degradation (no hard SDK dependencies).

| Module | SDK | Provider |
|--------|-----|----------|
| `aws_datazone.py` | boto3 (optional) | AWS DataZone |
| `azure_purview.py` | azure-purview-catalog (optional) | Microsoft Purview |
| `gcp_dataplex.py` | google-cloud-dataplex (optional) | GCP Dataplex |

All operations default to `dry_run=True`. SDK availability checked via `importlib.util.find_spec()`. `csp/__init__.py` provides `get_csp_status()` and `run_sync(provider, domain_ids, dry_run)` router.

---

## New DB Tables

| Table | Description |
|-------|-------------|
| `dm_product_slas` | SLA definitions per data product (uptime, latency, freshness) |
| `dm_product_subscriptions` | Consumer team subscriptions (pending/approved) |
| `dm_data_contracts` | ODCS-compliant contracts with YAML payload + version |
| `dm_contract_test_runs` | Append-only lint/test run history (NIST AU) |

Pre-existing tables: `dm_domains`, `dm_data_products`, `dm_opa_policies`, `dm_policy_audit_log`, `dm_csp_sync_log`.

ALTER TABLE migrations added `output_port_type`, `sla_tier`, `owner_team` to `dm_data_products`.

---

## Blueprint API Routes

All routes under `/data/` (Data Canvas blueprint, mounted at `/data`):

### Domains
| Route | Methods |
|-------|---------|
| `/api/dm/domains` | GET, POST |
| `/api/dm/domains/<id>` | GET, PUT, DELETE |
| `/api/dm/domains/<id>/maturity` | GET |

### Products
| Route | Methods |
|-------|---------|
| `/api/dm/products` | GET, POST |
| `/api/dm/products/<id>` | GET, PUT, DELETE |
| `/api/dm/products/<id>/subscribe` | POST |
| `/api/dm/products/<id>/score` | GET |

### Contracts (ODCS)
| Route | Methods |
|-------|---------|
| `/api/dm/contracts` | GET, POST |
| `/api/dm/contracts/<id>` | GET, PUT, DELETE |
| `/api/dm/contracts/<id>/lint` | POST |
| `/api/dm/contracts/<id>/test` | POST |

### Governance + CSP
| Route | Methods |
|-------|---------|
| `/api/dm/policies` | GET, POST |
| `/api/dm/policies/<id>` | GET, PUT, DELETE |
| `/api/dm/governance/check` | POST |
| `/api/dm/governance/score` | GET |
| `/api/dm/csp/status` | GET |
| `/api/dm/csp/sync` | POST |
| `/api/dm/csp/history` | GET |
| `/api/dm/summary` | GET |

---

## Pages

| URL | Template | Description |
|-----|----------|-------------|
| `/data/domains` | `domains.html` | Domain registry with maturity badges |
| `/data/products` | `products.html` | Product catalog with discoverability scores |
| `/data/contracts` | `contracts.html` | ODCS contract browser |
| `/data/governance` | `governance.html` | OPA policy management |
| `/data/csp` | `csp.html` | CSP sync dashboard (AWS / Azure / GCP) |
| `/data/mesh` | `mesh.html` | 4-pillar control plane hub |

Nav link: **Canvases → Data Mesh** → `/data/mesh`

---

## Configuration

**`args/data_mesh_config.yaml`** — runtime settings:
- `opa.url` — OPA policy engine endpoint (blank = local eval only)
- `lineage.url` — OpenLineage/Marquez server
- `classification_default` — default marking for new artifacts
- `governance_score_gate` — compliance threshold (default: 0.6)
- `csp.dry_run_default` — safe default for all CSP sync operations
- `contract_test_mode` — `internal` (lint-only) or `cli` (datacontract-cli)

**Optional dependencies** (commented in `requirements.txt`):
- `datacontract-cli` — contract linting + testing
- `openlineage-python` — OpenLineage event emission
- `pyiceberg` — Apache Iceberg table support
- `boto3` — AWS DataZone bridge
- `azure-purview-catalog` — Azure Purview bridge
- `google-cloud-dataplex` — GCP Dataplex bridge

---

## Security

- All routes protected by `@dc_login_required` (session auth)
- `dm_contract_test_runs` and `dm_policy_audit_log` are append-only (NIST AU-12)
- Classification default: `CUI // SP-CTI` on all created artifacts
- CSP operations default to `dry_run=True` — no live cloud writes without explicit opt-in
- No hard SDK dependencies — all CSP adapters fail gracefully with `sdk_unavailable` status
