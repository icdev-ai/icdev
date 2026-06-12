# Multi-Agent Orchestration (Opus 4.6 Multi-Agent — Phase B)

> Shard of `tools/manifest.md`. See index at `tools/manifest.md`.

## Multi-Agent Orchestration (Opus 4.6 Multi-Agent — Phase B)
| Tool | File | Description | Input | Output |
|------|------|-------------|-------|--------|
| Team Orchestrator | tools/agent/team_orchestrator.py | DAG-based workflow engine: LLM task decomposition, TopologicalSorter + ThreadPoolExecutor parallel execution | --decompose, --execute, --workflow-id, --json | Workflow result |
| Prompt Chain Executor | tools/agent/prompt_chain_executor.py | Declarative LLM-to-LLM sequential reasoning chains: YAML-driven prompt templates with $INPUT/$ORIGINAL/$STEP{x} variable substitution, per-step agent routing via LLMRouter (D-PC-1 through D-PC-3) | --chain, --input, --list, --dry-run, --history, --project-id, --json | Chain execution result |
| Skill Router | tools/agent/skill_router.py | Health-aware agent-skill routing: staleness check, least-loaded selection, dispatcher mode awareness (D-DISP-1) | --route-skill, --health, --routing-table, --project-id | Agent routing |
| Dispatcher Mode | tools/agent/dispatcher_mode.py | Dispatcher-only orchestrator mode: restricts orchestrator to delegation tools, blocks domain tool execution, per-project DB overrides, tool-to-agent redirect mapping (Phase 61, D-DISP-1) | --status, --enable, --disable, --check-tool, --project-id, --json, --human | Dispatcher status |

