# ATO Boundary Impact (RICOAS Phase 2)

> Shard of `tools/manifest.md`. See index at `tools/manifest.md`.

## ATO Boundary Impact (RICOAS Phase 2)
| Tool | File | Description | Input | Output |
|------|------|-------------|-------|--------|
| [DEPRECATED] Boundary Analyzer | tools/requirements/boundary_analyzer.py | 4-tier ATO boundary impact assessment (GREEN/YELLOW/ORANGE/RED) with RED alternative COA generation | --project-id, --system-id, --requirement-id, --generate-alternatives, --json | Impact tier + alternatives |

## BDC cATO Twin (Phase 1) — CUI // SP-CTI
| Tool | File | Description | Input | Output |
|------|------|-------------|-------|--------|
| cATO Twin Snapshot Writer | tools/boundary_canvas/cato_twin/snapshot_writer.py | Freeze cross-framework compliance state (control × project × framework × timestamp → status + evidence_ref) into compliance_twin_snapshots | --project-id, --framework, --controls-json, --triggered-by, --json | snapshot_id |
| cATO Twin IQE Query Surface | tools/iqe/adapters/compliance.py | Execute IQE DSL queries against compliance_twin_* via the maintained IQE executor/adapters (`compliance.twin_snapshots`/`.twin_violations`/`.twin_runs`); `run_query` enforces a fail-closed field whitelist + project scoping. bdt-iqe-1 retired the Phase-1 regex engine `cato_twin/query_engine.py`. | IQE query string, project_id | list[dict] rows |
| cATO Twin POA&M Auto-Generator | tools/boundary_canvas/cato_twin/poam_auto_generator.py | Generate POA&M items from compliance_twin_violations for a snapshot; idempotent dedup; links back to violations table | --snapshot-id, --project-id, --json | {new_items, skipped_items} |
| cATO Twin CLI | tools/boundary_canvas/cato_twin/cli.py | Unified CLI: snapshot / query / poam / status / migrate sub-commands | sub-command + args, --json | varies |
| cATO Twin Genesis Reflex | tools/genesis/reflexes/cato_twin.py | 6h continuous monitoring reflex; per-project snapshot + IQE violation scan + auto POA&M | ctx dict, conn (optional) | {snapshots_written, violations_found, poam_items_created} |
| DB Migration 027 | tools/db/migrations/027_compliance_twin_schema/up.py | Create compliance_twin_snapshots, compliance_twin_violations, compliance_twin_runs tables | sqlite3.Connection | {status, actions} |
| IQE Seed Queries | context/iqe/queries/boundary/*.iqe | 20 seed queries across FedRAMP Moderate (frm-001…010) and FedRAMP High (frh-001…010) | — | IQE DSL files |

## ISA lifecycle — two deliberately separate stores
Interconnection Security Agreements (ISAs) live in **two independent stores**;
this split is intentional. Consolidating them is a **rejected non-goal for now**
— the two surfaces serve different consumers and are wired together by a
nav-link only (PR #374), not by a shared table.

| Store | Scope | Table (DB) | Code | Surface |
|-------|-------|-----------|------|---------|
| Design-scoped ISA tracker | Per boundary **design**; feeds cATO readiness ISA-expiry scoring and boundary risk | `bd_isa_tracker` (**canvas DB**) | `tools/boundary_canvas/boundary_engine.py::compute_isa_status`; `tools/boundary_canvas/isa_expiry.py` (`ensure_isa_expiry_column`, `check_isa_expiry`) | Page `GET /boundary/isa-tracker` (`bdc_isa_tracker_page`); API `GET/POST /boundary/api/designs/<design_id>/isa-tracker`; IQE `bdc.isas` |
| Supply-chain ISA/MOU manager | Project-level ISA/MOU **agreements** lifecycle (create, list, expiring, review-due) | `isa_agreements` (**main DB**) | `tools/supply_chain/isa_manager.py` (`create_isa`, `list_isas`, `get_expiring`, `get_review_due`) | MCP tool `manage_isa` (`tools/mcp/supply_chain_server.py::handle_manage_isa`, actions: list / create / expiring / review_due) |

- The two stores are **not** joined: the boundary canvas reads only
  `bd_isa_tracker`; the supply-chain manager reads only `isa_agreements`.
- Integration between them is a **nav link only** (PR #374) — deliberately loose
  coupling. A future consolidation card may revisit this, but it is out of scope
  today.

