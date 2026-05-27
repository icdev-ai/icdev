# Supply Chain Intelligence (RICOAS Phase 2)

> Shard of `tools/manifest.md`. See index at `tools/manifest.md`.

## Supply Chain Intelligence (RICOAS Phase 2)
| Tool | File | Description | Input | Output |
|------|------|-------------|-------|--------|
| Dependency Graph | tools/supply_chain/dependency_graph.py | Build/query supply chain dependency graph with upstream/downstream impact propagation | --project-id, --build-graph, --upstream, --downstream, --impact, --json | Graph + blast radius |
| ISA Manager | tools/supply_chain/isa_manager.py | ISA/MOU lifecycle tracking — create, expiring, review due, renew, revoke | --project-id, --create, --expiring, --review-due, --json | ISA status |
| SCRM Assessor [DEPRECATED] | tools/supply_chain/scrm_assessor.py | NIST 800-161 supply chain risk assessment across 6 dimensions | --project-id, --vendor-id, --aggregate, --json | Risk score + tier |
| CVE Triager [DEPRECATED] | tools/supply_chain/cve_triager.py | CVE triage with upstream/downstream blast radius and SLA tracking | --project-id, --triage, --sla-check, --propagate, --json | Triage + blast radius |
| CVE Passive Watcher | tools/supply_chain/cve_passive_watcher.py | Passive ATO continuous monitoring — streams immutable audit_trail for CVE signals, auto-triages new discoveries, feeds dependency_graph blast-radius propagation (NIST SI-4, CA-7) | --project-id, --scan, --since-id, --status, --watch, --interval, --no-triage, --json | Scan results + triage IDs |
| MCP Supply Chain Server | tools/mcp/supply_chain_server.py | MCP server for boundary + supply chain tools (9 tools) | stdio | JSON-RPC responses |
| Rare Earth Cascade Analyzer | tools/supply_chain/rare_earth_cascade.py | Models REE embargo scenarios for US defense programs — calculates blast radius (affected program count) and time-to-depletion per system from stockpile data | --impact, --severity, --list-programs, --json | Cascade impact report + affected programs |

