# Design Canvases (7-Canvas Suite)

> Shard of `tools/manifest.md`. See index at `tools/manifest.md`.

## Design Canvases (7-Canvas Suite)
All canvases share: separate SQLite DB, Flask Blueprint, YAML config in `args/`, feature flag env var, NIST 800-53 compliance assessment, and cross-canvas integration via `tools/canvas/orchestrator.py`.

| Canvas | Blueprint | Engine | Config | DB | Route | Feature Flag | Description |
|--------|-----------|--------|--------|----|-------|--------------|-------------|
| **IDC** Infrastructure | tools\infra_canvas\blueprint.py | tools\infra_canvas\infra_engine.py | args\infra_canvas_config.yaml | infra_canvas.db | /infra | ICDEV_INFRA_ENABLED | Multi-CSP infrastructure design (compute, storage, containers, serverless) with NIST/FedRAMP/CMMC assessment and CSP equivalence mapping |
| **NDC** Network | tools\network\blueprint.py | tools\network\routes\ | — | network_canvas.db | /network | ICDEV_NETWORK_ENABLED | Network topology design with intent validation, ACAS/Nessus overlay, NL query, change request markup, and NetBox sync |
| **SDC** Security | tools\security_canvas\blueprint.py | tools\security_canvas\security_engine.py | args\security_canvas_config.yaml | security_canvas.db | /security | ICDEV_SECURITY_ENABLED | STRIDE threat modeling, NIST/FedRAMP/CMMC control mapping, MITRE ATT&CK coverage, compliance KG, remediation, LLM agent, NL query |
| **BDC** Boundary | tools\boundary_canvas\blueprint.py | tools\boundary_canvas\boundary_engine.py | args\boundary_canvas_config.yaml | boundary_canvas.db | /boundary | ICDEV_BOUNDARY_ENABLED | ATO/FedRAMP/SCIF authorization boundary design, ISA lifecycle (expiry warning 60d / critical 30d), PPS matrix generation, boundary gap detection |
| **PDC** Pipeline | tools\pipeline\blueprint.py | — | — | pipeline_canvas.db | /devops | ICDEV_PIPELINE_ENABLED | DevSecOps pipeline design, CI/CD stage modeling, security gate placement, GitLab/GitHub Actions export |
| **ODC** Observability | tools\observability_canvas\blueprint.py | tools\observability_canvas\observability_engine.py | args\observability_canvas_config.yaml | observability_canvas.db | /observability | ICDEV_OBSERVABILITY_ENABLED | SIEM/SOAR/log stack design, MITRE ATT&CK detection coverage, source type weighting, NIST AU/SI control assessment, log retention policy |
| **DDC** Data | tools\data_canvas\blueprint.py | tools\data_canvas\data_engine.py | args\data_canvas_config.yaml | data_canvas.db | /data | ICDEV_DATA_CANVAS_ENABLED | Data model design with PII/PHI/CUI/SECRET classification, retention policy enforcement, Privacy Act/HIPAA/GDPR assessment, ER diagram export |

## DDC Sub-Tools
| Tool | File | Description | Input | Output |
|------|------|-------------|-------|--------|
| DDC ← Collibra Import | tools/data_canvas/sync/collibra_import.py | Import table/column-level lineage from Collibra Data Intelligence Cloud (REST API v2) into DDC as dd_lineage records. Applies a CUI classification overlay to every edge; runs generate_contract_assertions() after import to enforce 'SECRET data may not flow into IL4-accessible datasets'. External Collibra assets become DDC graph nodes with IDs `ext:collibra:<assetId>`. Supports live API pull and offline JSON file import. | `--design-id <id>` or `--create-design <name>`, `--file <json>` (offline), `--classification CUI\|SECRET`, `--dry-run`, `--gate`, `--json` | JSON: `{status, nodes_imported, lineage_edges_imported, cat1_violations, violation_details[]}` |
| DDC ← OpenMetadata Import | tools/data_canvas/sync/openmetadata_import.py | Import table/column-level lineage FROM OpenMetadata into DDC (reverse of openmetadata_sync.py). Fetches `GET /api/v1/lineage/{type}/name/{fqn}` with configurable upstream/downstream depth. Maps OM column lineage details to DDC lineage_type (col-passthrough, col-aggregate, col-derive, etc.). Applies CUI classification overlay and runs contract assertions after import. | `--design-id <id>` or `--create-design <name>`, `--entity <fqn>` or `--all`, `--entity-type table`, `--upstream-depth 3`, `--downstream-depth 3`, `--dry-run`, `--gate`, `--json` | JSON: `{status, tables_processed, nodes_imported, lineage_edges_imported, cat1_violations, violation_details[]}` |

## IDC Sub-Tools
| Tool | File | Description | Input | Output |
|------|------|-------------|-------|--------|
| IDC SOPs | tools/infra_canvas/sops.py | Infrastructure Design Canvas — Standard Operating Procedures CRUD + draft→pending_review→approved approval workflow. Seeds change management, capacity planning, and cloud account onboarding SOPs into `idc_sops` table. No LLM dependency. | (library) `get_all_sops`, `get_sop_by_id`, `create_sop`, `update_sop`, `transition_status`, `delete_sop`, `seed_sops` | SOP dict / list |
| IDC Runbooks | tools/infra_canvas/runbooks.py | Infrastructure Design Canvas — operational runbooks for common infrastructure incidents (provisioning failure, capacity breach, cloud drift, patch rollback). CRUD + seed for `idc_runbooks` table; no LLM dependency. | (library) | Runbook dict / list |

