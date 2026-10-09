# SAG Runtime Config Surface and Component Registration (hgx-cfg-01)

**Card:** HGX — Harness Agent Parity and Graph Runtime
**Status:** shipped

## Problem

Two gaps, both about *visibility* rather than capability.

1. **No configuration surface.** The standalone agent runtime's behaviour lived
   in roughly a dozen environment variables spread across nine modules
   (`ICDEV_SAG_GOALS`, `ICDEV_SAG_PROJECT_STATE`, `ICDEV_SAG_APPROVAL_MODE`,
   `ICDEV_SAG_ALLOW_MUTATION`, …). There was nowhere to see them together, no
   way to ship a configured default with a deployment, and no way to answer
   "what is this agent actually doing?" without grepping for `os.environ`.

2. **SAG was absent from `args/component_registry.yaml`.** The registry is what
   makes a component reachable from `icdev enable` / `disable` / `status` /
   `list`, the `icdev setup` TUI, the generated `.env`, RBAC metadata and the
   coherence checks that read the registry. SAG was reachable from none of them.

## What shipped

### 1. `args/agent_runtime.yaml` + `tools/agent_runtime/config.py`

A declarative config file and an `AgentRuntimeConfig` loader, read at
`AgentRuntime` construction.

The governing decision is **precedence**:

```
explicit argument  >  environment variable  >  args/agent_runtime.yaml  >  built-in default
```

The config layer was inserted *beneath* the environment, never above it. Every
env var that worked before still works and still wins. An operator who exports a
flag in a shell, a systemd unit or a CI job must not have it silently reversed by
a file they did not know to look at — so each accessor takes the env name it
defers to and reads it **first**; the YAML is consulted only when the env var is
absent.

Design properties worth stating because each is load-bearing:

- **Optional.** A missing, unreadable or malformed file degrades to the built-in
  defaults with a log line. Deleting `args/agent_runtime.yaml` changes nothing
  about how the agent runs — configuration is not a hard dependency of starting
  an agent.
- **Fail-safe, not fail-open.** An unparseable env value falls through a layer
  rather than resolving to zero/false, and an out-of-vocabulary approval mode
  resolves to the strictest value (`manual` / `enforce`) at every layer — a typo
  can never disable the mutation gate.
- **YAML cached, environment not.** The file is parsed once (it is consulted at
  every turn boundary); `os.environ` is read at each access, so a `/set` or a
  test's `monkeypatch` applies immediately without a reload.
- **No inert keys.** Every key in the file names the function that reads it. A
  key nothing consumes reads as a supported setting and is worse than no key.

Binding acceptance criteria for the card:

- **LLM-agnostic** — the schema has no `model:` key at all, only `llm_function`,
  a routing function resolved by `LLMRouter` against `args/llm_config.yaml`. A
  model id in YAML pins a vendor exactly as hard as one in Python. A test asserts
  no model token appears in the file's data.
- **OS-agnostic** — the file is read `encoding="utf-8", newline=""` with a
  leading BOM stripped; the path is resolved from `__file__` (never
  `os.getcwd()`), so a worktree or a service started from `/` resolves the same
  file; both the checkout layout (`<root>/args/`) and the wheel layout
  (`icdev/data/args/`) are probed; `pathlib` throughout; no shell.

Subsystems wired (each keeps its env var as the winning layer):

| Key | Env var (wins) | Read by |
|-----|----------------|---------|
| `enabled` | `ICDEV_SAG_ENABLED` | `config.AgentRuntimeConfig.enabled` |
| `runtime.llm_function` | `ICDEV_SAG_LLM_FUNCTION` | `AgentRuntime.__init__` |
| `runtime.max_iterations` | `ICDEV_SAG_MAX_ITERATIONS` | `AgentRuntime.__init__` |
| `runtime.max_total_tokens` | `ICDEV_SAG_MAX_TOTAL_TOKENS` | `AgentRuntime.__init__` |
| `runtime.max_cost_usd` | `ICDEV_SAG_MAX_COST_USD` | `AgentRuntime.__init__` |
| `subsystems.project_context.enabled` | `ICDEV_SAG_PROJECT_CONTEXT` | `project_context.context_enabled()` |
| `subsystems.project_context.include_project_state` | `ICDEV_SAG_PROJECT_STATE` | `project_context.project_state_enabled()` |
| `subsystems.standing_goals.enabled` | `ICDEV_SAG_GOALS` | `goal_context.goals_enabled()` |
| `subsystems.standing_goals.limit` | `ICDEV_SAG_GOAL_LIMIT` | `goal_context.goal_limit()` |
| `subsystems.profile_memory.enabled` | `ICDEV_SAG_PROFILE_MEMORY` | `AgentRuntime._profile_memory_enabled()` |
| `subsystems.skill_proposals.enabled` | `ICDEV_SAG_SKILL_PROPOSALS` | `skills_lifecycle.proposals_enabled()` |
| `subsystems.approval.mode` | `ICDEV_SAG_APPROVAL_MODE` | `safety.resolve_mode()` |
| `subsystems.approval.risk_function` | `ICDEV_SAG_RISK_FUNCTION` | `safety.resolve_risk_function()` |
| `subsystems.approval.command_mode` | `ICDEV_AGENT_APPROVAL_MODE` | `approval_gate.resolve_mode()` |
| `subsystems.mutation.allow` | `ICDEV_SAG_ALLOW_MUTATION` | `dispatch.mutation_allowed()` |
| `subsystems.delegation.child_can_delegate` | `ICDEV_SAG_CAN_DELEGATE` | `delegation._child_can_delegate()` |
| `subsystems.toolsets.bundle_path` | `ICDEV_AGENT_TOOLSETS` | `toolsets._bundle_path()` |

Probe the resolved state — including which env vars are currently beating the
file, because "the config says X but the agent does Y" is otherwise a long hunt:

```bash
python -m tools.agent_runtime.config --json
```

### 2. SAG registered in `args/component_registry.yaml`

```yaml
- key: sag
  kind: core_extension
  cli_name: sag
  display_name: Standalone Agent Runtime
  env_flag: ICDEV_SAG_ENABLED
  default_enabled: true
```

`ICDEV_SAG_ENABLED` is the *same* switch as `enabled:` in
`args/agent_runtime.yaml` — the config layer reads that env var first — so the
CLI toggle and the config file cannot disagree. `icdev chat` now refuses to start
when the runtime is disabled, naming both the flag and the file, rather than
starting a runtime the operator turned off.

No `module` / `blueprint_attr` is declared: SAG is a CLI subsystem, and the
dashboard's core-extension loop registers a blueprint only when `blueprint_attr`
is set, so nothing is mounted on a route. Because all three component surfaces
(`icdev enable/disable`, the `icdev setup` TUI, `env_generator`) are
registry-driven, the single entry makes SAG reachable from all of them at once —
which is what `check_component_cli_reachability` verifies.

## Verification

```bash
python -m tools.agent_runtime.config --json
icdev list | grep sag
pytest tests/agent_runtime/ tests/test_component_registry.py -q
python tools/workflow/coherence_checker.py --all --gate
python tools/dx/mirror_parity.py --paths agent_runtime --gate
```

`tests/agent_runtime/test_config.py` (34 tests) covers loading (missing,
malformed, partial, BOM-prefixed, wheel layout), precedence (a parametrised case
per env var, each asserted against a config file that says the opposite),
fail-safe defaults, `AgentRuntime` construction, and the registry/CLI surfaces.

## Files

- `args/agent_runtime.yaml` (new)
- `tools/agent_runtime/config.py` (new, mirrored to `icdev/tools/`)
- `tools/agent_runtime/{runtime,cli,goal_context,project_context,safety,approval_gate,dispatch,delegation,toolsets,skills_lifecycle}.py`
- `args/component_registry.yaml`
- `tests/agent_runtime/test_config.py` (new)
- `tools/manifest/standalone-agent-runtime.md`, `docs/reference/commands.md`
