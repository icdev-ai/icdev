# Manifest Gap Fill (2026-04-12)

> Shard of `tools/manifest.md`. See index at `tools/manifest.md`.

## Manifest Gap Fill (2026-04-12)
| Tool | File | Description | Input | Output |
|------|------|-------------|-------|--------|
| Network Topology Merge | tools/network/topology_merge.py | Network Design Canvas — merge multiple topology graphs (from separate team diagrams) into a single canonical graph. Collapses duplicate-label nodes, rewrites edges to canonical IDs, auto-discovers cross-graph edges via label matching, and records per-node/edge provenance. Core utility for stitching site-ops, cloud, DC, and security diagram slices. | `(library) merge_topologies(graphs, match_on="label")` | `{nodes, edges, _sources, _stitched_hosts, _stats}` |
| Network Config Import | tools/network/config_import.py | Network Design Canvas — parse Cisco IOS/NX-OS/Junos config files into a topology graph. Auto-detects vendor; infers adjacency from LLDP/CDP tables, shared subnets, and interface descriptions. Emits `{nodes, edges}` compatible with `export_import.to_drawio`, `visio_export.export_vsdx`, and `layout.auto_layout`. | `--file <cfg>` \| `--dir <dir>` `[--output <file>]` `[--auto-layout]` `[--json]` | Topology graph JSON `{nodes, edges, _devices, _edges_by_method}` |
| Network Backup | tools/network/backup.py | Network Design Canvas — backup/restore CLI for device configs and topology | --backup, --list, --restore, --notes | Backup metadata + JSON |
| Network Compliance | tools/network/compliance.py | Network Design Canvas — compliance audit engine (STIG/CIS/NIST alignment) | --audit, --device, --json | Findings + compliance report |
| Network Config Generator | tools/network/config_generator.py | Network Design Canvas — device configuration generator (vendor-agnostic) | --device, --template, --json | Rendered config |
| Network Simulation | tools/network/simulation.py | Network topology simulation (extracted from network-canvas app.py) | (library) | Simulation funcs |
| Notification Gateway | tools/notifications/gateway.py | Multi-platform notification gateway (Slack, Teams, Telegram, email) — Phase 72 | --send, --channel, --severity | Delivery status |
| Pipeline Export | tools/pipeline/export.py | Pipeline Design Canvas — export engine (YAML/JSON/HCL) | --pipeline-id, --format, --json | Exported pipeline artifact |
| Pipeline Remediation | tools/pipeline/remediation.py | Pipeline Design Canvas — remediation engine for policy/security violations | --pipeline-id, --apply, --json | Remediation actions |
| Pipeline Runbooks | tools/pipeline/runbooks.py | Pipeline Design Canvas — incident response runbooks | --incident, --list, --json | Runbook steps |
| Playground App | tools/playground/app.py | ICDEV™ Playground — read-only demo application | (server) | Flask app |
| Proposal Genesis Security | tools/proposal_genesis/security.py | NemoClaw credential broker wrapper for Proposal Genesis | (library) | Credential broker API |
| Pulse CLI | tools/pulse/cli.py | Pulse AI Blog Engine — command-line interface | --publish, --list, --status, --json | Pulse operation results |
| Pulse DB | tools/pulse/db.py | Pulse database layer — uses ICDEV™ storage abstraction (D-DB-20) | (library) | Connection + CRUD helpers |
| Pulse WriteGuard Bridge | tools/pulse/writeguard.py | WriteGuard integration bridge for Pulse (5-dimension quality check) | (library) | run_full_quality_check() |
| QDC Agent | tools/qdc_canvas/agent.py | Quality Design Canvas — cross-canvas agent hooks | (library) | Canvas agent API |
| QDC Blueprint | tools/qdc_canvas/blueprint.py | Quality Design Canvas — Flask Blueprint with all routes and API endpoints | (library) | Flask Blueprint |
| RAG Evaluator | tools/rag/evaluator.py | RAGAS-style RAG evaluation framework (D-RAG-22) | --evaluate, --dataset, --json | Evaluation metrics |
| Review Board Compliance Bridge | tools/review_board/compliance_bridge.py | Ties Review Board findings into ICDEV™ audit, evidence, and NIST controls | (library) | Control mappings |
| Review Board Correlator | tools/review_board/correlator.py | Cross-Reflex Correlator — dedup related findings across personas (D-RB-15) | --run, --json | Correlated findings |
| Review Board Escalation | tools/review_board/escalation.py | Escalation workflow — auto-create GitHub/GitLab issues for escalated findings (D-RB-14) | --escalate, --finding-id, --json | Issue URL + status |
| IDC Runbooks | tools/infra_canvas/runbooks.py | Infrastructure Design Canvas — operational runbooks for common infra incidents (server provisioning failure, capacity threshold breach, cloud drift, patch rollback). CRUD + seed for idc_runbooks table; no LLM dependency. | (library) get_all_runbooks(category, severity) / get_runbook_by_id(id) / create_runbook(data) / record_execution(id) / seed_runbooks() | Runbook dict / list |
| ODC Runbooks | tools/observability_canvas/runbooks.py | Observability Design Canvas — operational runbooks for common observability incidents (alert storm triage, log pipeline failure, SIEM gap detected, metric collection outage). CRUD + seed for odc_runbooks table; no LLM dependency. | (library) get_all_runbooks(category, severity) / get_runbook_by_id(id) / create_runbook(data) / record_execution(id) / seed_runbooks() | Runbook dict / list |
| SDC Agent | tools/security_canvas/agent.py | Security Design Canvas — cross-canvas agent hooks for STRIDE/MITRE/NIST workflows. | (library) | Canvas agent API |
| IDC SOPs | tools/infra_canvas/sops.py | Infrastructure Design Canvas — Standard Operating Procedures (provisioning, scale-out, drift remediation, patching). CRUD + seed for idc_sops table; no LLM dependency. | (library) | SOP dict / list |
| FathomDesk Snapshot Builder | tools/trading/market_intel/snapshot_builder.py | FathomDesk — materializes `ad_market_snapshot` and `ad_ticker_performance` tables so `/api/market/latest` and `/api/market/performance` read from cache instead of recomputing. Refreshed by the `snapshot_refresher` reflex. | `--build-snapshot`, `--build-performance`, `--json` | Row counts + timing |
| Network PDF Import | tools/network/pdf_import.py | Network Design Canvas — PDF import for network diagrams. Two-tier extraction: vector path (pdfplumber) for Visio/drawio/Lucidchart exports, vision-LLM fallback for raster PDFs. Returns topology graph compatible with `export_import.to_drawio` / `visio_export.export_vsdx`. | `--file <pdf>` `[--vision-fallback]` `[--output <file>]` `[--json]` | Topology graph JSON `{nodes, edges, _source, _method}` |


