# Digital Program Twin Simulation (RICOAS Phase 3)

> Shard of `tools/manifest.md`. See index at `tools/manifest.md`.

## Digital Program Twin Simulation (RICOAS Phase 3)
| Tool | File | Description | Input | Output |
|------|------|-------------|-------|--------|
| Simulation Engine | tools/simulation/simulation_engine.py | 6-dimension what-if simulation (architecture, compliance, supply chain, schedule, cost, risk) | --project-id, --create-scenario, --run, --dimensions, --json | Simulation results |
| Monte Carlo | tools/simulation/monte_carlo.py | PERT/Monte Carlo schedule/cost/risk estimation (stdlib random, no numpy) | --scenario-id, --dimension, --iterations, --json | Percentiles + histogram |
| COA Generator | tools/simulation/coa_generator.py | Generate 3 COAs (Speed/Balanced/Comprehensive) + RED alternatives | --session-id, --generate-3-coas, --simulate, --compare, --json | COAs + comparison |
| Scenario Manager | tools/simulation/scenario_manager.py | Save, fork, compare, export, archive simulation scenarios | --scenario-id, --fork, --compare, --export, --json | Scenario operations |
| MCP Simulation Server | tools/mcp/simulation_server.py | MCP server for simulation tools (8 tools) | stdio | JSON-RPC responses |
| Diagram Style | tools/simulation/diagram_style.py | Canvas-agnostic color/style constants: AWS/Azure/BCAP/IDPS/BGP/Megaport/PrivateLink zones, microservice zones, EDA zones, flow palette F1-F8, edge semantics; get_node_style(zone, canvas_type), get_edge_style(semantic, canvas_type), get_flow_color(flow_id) | import | Style dicts |
| Mermaid Parser | tools/simulation/parsers/mermaid_parser.py | Parse Mermaid diagram source (flowchart, sequence, class, ER) into normalized graph_json; parse_mermaid(source: str) -> dict | diagram string | graph_json dict |
| draw.io Parser | tools/simulation/parsers/drawio_parser.py | Parse draw.io mxGraph XML (.drawio/.xml) into normalized graph_json; swimlane containers → zones, vertex labels/styles, edge connections/labels; network, microservice, EDA diagrams; parse_drawio(xml_str: str) -> dict | XML string | graph_json dict with nodes/edges/zones |

