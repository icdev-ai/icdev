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
| cATO Twin IQE Query Engine | tools/boundary_canvas/cato_twin/query_engine.py | Execute IQE DSL queries against compliance_twin_snapshots; foreach/where/select over framework controls, violations, or runs | IQE query string, --json | list[dict] rows |
| cATO Twin POA&M Auto-Generator | tools/boundary_canvas/cato_twin/poam_auto_generator.py | Generate POA&M items from compliance_twin_violations for a snapshot; idempotent dedup; links back to violations table | --snapshot-id, --project-id, --json | {new_items, skipped_items} |
| cATO Twin CLI | tools/boundary_canvas/cato_twin/cli.py | Unified CLI: snapshot / query / poam / status / migrate sub-commands | sub-command + args, --json | varies |
| cATO Twin Genesis Reflex | tools/genesis/reflexes/cato_twin.py | 6h continuous monitoring reflex; per-project snapshot + IQE violation scan + auto POA&M | ctx dict, conn (optional) | {snapshots_written, violations_found, poam_items_created} |
| DB Migration 027 | tools/db/migrations/027_compliance_twin_schema/up.py | Create compliance_twin_snapshots, compliance_twin_violations, compliance_twin_runs tables | sqlite3.Connection | {status, actions} |
| IQE Seed Queries | context/iqe/queries/boundary/*.iqe | 20 seed queries across FedRAMP Moderate (frm-001…010) and FedRAMP High (frh-001…010) | — | IQE DSL files |

