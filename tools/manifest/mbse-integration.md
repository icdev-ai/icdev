# MBSE Integration (Phase 18)

> Shard of `tools/manifest.md`. See index at `tools/manifest.md`.

## MBSE Integration (Phase 18)
| Tool | File | Description | Input | Output |
|------|------|-------------|-------|--------|
| XMI Parser | tools/mbse/xmi_parser.py | Parse Cameo SysML v1.6 XMI exports into sysml_elements + relationships | --project-id, --file, --validate-only, --json | Import summary |
| ReqIF Parser | tools/mbse/reqif_parser.py | Parse DOORS NG ReqIF 1.2 exports into doors_requirements | --project-id, --file, --diff, --export, --json | Import summary |
| Digital Thread | tools/mbse/digital_thread.py | End-to-end traceability engine (req→model→code→test→control) | --project-id, subcommands (auto-link, coverage, orphans, gaps, report) | Coverage + trace |
| Model-to-Code Generator | tools/mbse/model_code_generator.py | Generate code scaffolding from SysML models (blocks→classes, activities→functions) | --project-id, --language, --output, --json | Generated files |
| Sync Engine | tools/mbse/sync_engine.py | Bidirectional model-code sync with SHA-256 drift detection | --project-id, detect-drift, sync-model-to-code, --json | Sync status |
| DES Assessor [DEPRECATED] | tools/mbse/des_assessor.py | DoDI 5000.87 Digital Engineering Strategy compliance assessment (10 auto-checks) | --project-id, --project-dir, --json | DES score + gate |
| DES Report Generator | tools/mbse/des_report_generator.py | CUI-marked DES compliance report generation | --project-id, --output-dir | Report path |
| Model-NIST Mapper | tools/mbse/model_control_mapper.py | Map SysML elements to NIST 800-53 controls by keyword analysis | --project-id, --map-all, --json | Control mappings |
| PI Model Tracker | tools/mbse/pi_model_tracker.py | SAFe PI-cadenced model snapshots, velocity, burndown, comparison | --project-id, --pi, --snapshot, --compare, --json | PI metrics |
| MCP MBSE Server | tools/mcp/mbse_server.py | MCP server for MBSE tools (10 tools: import, trace, generate, sync, assess) | stdio | JSON-RPC responses |

