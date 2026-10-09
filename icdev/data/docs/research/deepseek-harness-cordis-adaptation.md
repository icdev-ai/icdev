# CUI // SP-CTI

# DeepSeek Harness / Cordis — Analysis and ICDEV Adaptation

**Date:** 2026-08-16
**Subject:** https://deepseek.com/harness/en/ — repo, developer docs, the Cordis paper, community plugins
**Verdict:** Do not adopt. Adopt three of its design commitments, in Python.
**Card:** `hcx` (see `args/projects.yaml`)

---

## 1. What DeepSeek Harness actually is

`dsh` is an agent harness open-sourced by DeepSeek on **2026-08-13** under MIT, at version
**`0.1.0-rc.5`**, explicitly a developer preview ("THERE WILL BE COMPATIBILITY-BREAKING CHANGES").
It is TypeScript on **Node 22.19+ / pnpm 11.7.0**, ~48 packages, with a Web UI, a headless runner,
and a Python client.

Its thesis is *"everything is a plugin, every run is traceable."* Both halves are real.

### The plugin contract (Cordis)

A plugin is a module exporting `name`, an optional `inject: string[]`, and `apply(ctx, config)`.
`ctx.plugin(x)` returns a **fiber** — a handle with the state machine
`PENDING → LOADING → ACTIVE → UNLOADING → DISPOSED` (plus `FAILED`). `fiber.dispose()` resolves only
after all cleanup finishes and recursively unloads children.

The load-bearing design decision is that **every registration returns a disposer**:

```ts
ctx.effect(() => {
  const timer = setInterval(() => console.log('tick'), 200)
  return () => clearInterval(timer)          // the inverse, mandatory, at the registration site
})
```

`ctx.on(...)`, `ctx.plugin(...)`, service registrations and registry calls such as
`ctx.tools.register(...)` all attach their disposer to the calling plugin automatically. That is what
the Cordis paper calls **temporal composability** — a component's side effects can be completely
reverted on removal. **Spatial composability** is the `inject` / `provide` half: a plugin declares the
services it needs, waits until they exist, and unloads before its provider does ("providers must
outlive their consumers"). Config is YAML rows in `cordis.yml`, and **list position guarantees
nothing** — ordering comes from `inject`, not from the file.

### The session log

The stated runtime invariant is **"model-visible means logged"**: anything reaching a model request
must be reconstructable from the log, and this is asserted at runtime. Each `SessionEvent` is
`{type, seq (= log.length), time, data}`, losslessly JSON-serializable. LLM messages are a
*projection* of that log, so `fork(source, boundary)`, resume, replay and full-text search all
operate on one stream. Events include `turn/start`, `step/start`, `user/message`,
`assistant/message`, `tool/call`, `tool/result`, `request/context`, `request/header`.

### Runtime modes

Not build variants — **agent presets**, i.e. pure YAML compositions in
`apps/cli/config/agent-presets/{standard,code,minimal,cordis}`. The entire delta between `code` and
`standard` is one config row. `minimal` is a two-tool agent (persistent bash + `str_replace_editor`)
used as the benchmarking rig. `cordis` ("Creator") adds a self-referential toolset whose own docs
warn: *"Treat a session on this preset as shell access."*

---

## 2. Why we are not adopting it

| Blocker | Detail |
|---|---|
| **No Node runtime in ICDEV** | The 72 `.ts` files in the tree are Playwright specs. Root `package.json` has two deps: `playwright`, `mermaid`. Adopting DSH means adopting Node 22.19+, pnpm, a build chain, and a bundler |
| **Pre-release** | `0.1.0-rc.5`, with an explicit breaking-changes warning in the README |
| **Sandbox is filesystem-only** | `read-only` / `workspace-write` / `danger-full-access` confine the filesystem. **Network and process isolation are explicitly excluded.** Unacceptable for IL4+ |
| **Install-time code execution** | A plugin dependency's npm `prepare` script executes at install time, outside the agent sandbox. That is an RCE surface in an ATO boundary |
| **Python SDK is Linux/macOS only** | It spawns the Node runtime and speaks newline-delimited JSON-RPC over stdio — which would otherwise slot cleanly into ICDEV's `AgentAdapter` seam — but ships no Windows support |
| **The paper has no evaluation** | *A Programming Paradigm for Spatiotemporal Composability* is a self-published preprint (GitHub repo, 2 commits, no arXiv ID, no DOI, no venue, "under active revision"). Its author also wrote Cordis **and** Koishi, the sole case study. There are **no benchmarks, no comparison against OSGi / DI / Erlang, no mechanized proofs** |
| **Forked lineage** | DeepSeek published `@deepseek-ai/cordis@4.0.1` rather than consuming upstream `cordis@4.0.0-rc.8`. The "four years in production" provenance belongs to upstream, not the shipped artifact |
| **Ecosystem is thinner than it looks** | The `dsh-plugin` topic shows ~4,900 repos, but it is being topic-squatted by pre-existing projects (`reactive-resume`, `yao`, `awesome-gpt-image-2`). Curated lists put the real count at **~300–550**, and the largest categories are dashboards, themes and pets. Security & Governance has 11 entries; Agents & Orchestration has 5 |

The most substantive independent review tried to construct a scenario where the in-process imperative
plugin model decisively beats a file-based declarative one and **could not find one**, except
agent-loop customization. Cordis's own Discussion section lists dependency cycles ("leaves both
parties permanently inactive"), interface drift, key collision, and total in-memory state loss on
reload as **unsolved**.

Conclusion: watch closely, pilot in isolation if ever. Not a dependency.

---

## 3. What is worth taking, and why each is a real ICDEV gap

The valuable finding is not the plugin model. It is that Cordis's central rule — *"there is no
privileged core to patch"* — names the defect ICDEV ships most often: **a descriptive registry
beside an imperative hardcoded list, where the descriptive one silently does nothing.**

Instances verified in the tree on 2026-08-16:

- `tools/genesis/reflex_registry.py` opens with a warning that *"This file schedules nothing. No
  dispatcher imports it."* — dispatch requires `REFLEX_NAMES` in `tools/genesis/daemon.py`.
- `tools/agents/capability_matrix.py` (38 KB of careful three-state capability probing) exists to
  serve `pick_default(require=[...])`, which has **zero production call sites**.
- `tools/extensions/extension_manager.py` declares **ten** `ExtensionPoint` members. Exactly one
  production site calls `dispatch()` (`tools/dashboard/chat_manager.py:79`). `TOOL_EXECUTE_BEFORE`
  — the *gating* point — has no production dispatcher at all.
- New agent adapters require editing a hardcoded import tuple in `tools/agents/registry.py`; new
  Studio node types require editing an if-chain in `workflow_runner.py`; `tools/studio/executors/__init__.py`
  is a one-line comment with no registry behind it.

### 3.1 "Model-visible means logged" — an append-only agent event log

This is the highest-value adaptation, and it lands on a genuine hole.

- `icdev/tools/llm/agent_loop_session.py::save_session` writes the whole transcript as a single
  `messages_json` blob, **UPSERT-overwritten every turn**. Resume works. Fork does not exist. Replay
  of an agent run does not exist.
- **No context injection is recorded anywhere.** `tools/agent_runtime/project_context.py`,
  `goal_context.py` and `profile_memory.py` all inject into the prompt; a tree-wide grep for
  `context_injection|injected_context|prompt_snapshot|rendered_prompt` returns three unrelated files.
- `llm_gateway_audit` (`tools/llm/gateway.py`) stores **hashes only** — deliberately, for privacy —
  and is imported only by `tools/cortex/*` and `tools/ops_hub/llmops_engine.py`. **It is not on the
  agent-runtime path**, so SAG's LLM calls are currently unaudited.
- `tools/agent_case/session_timeline.py` already joins `hook_events` + `audit_trail` +
  `agent_findings` into an ordered timeline, and its own `limits` block names `agent_executions`,
  `ai_telemetry` and `ace_audit_log` as uncorrelatable **because none has a `session_id` column**.

The seam to write through already exists — `run_agent_loop` exposes `on_turn`, `on_pre_tool_use`,
`on_post_tool_use` and `on_stop`, composed at `icdev/tools/llm/agent_loop.py:1395`. No new hook
machinery is required.

**Substrate probe (2026-08-16, live PostgreSQL board).** The nearest existing thing is
`harness_eval`, written from the agent loop and the `claude_cli` executor by the phase-1 tasks
`hcx-rt-03` / `hcx-rt-08`. It is **populated and live** — 1,993 rows, most recent
`2026-08-16T04:00Z` — but it is a **per-dispatch outcome table keyed by `task_id`**
(`reflex`, `decision`, `confidence`, `actual_outcome`, `resolved_at`). It has **no `session_id`, no
`seq`, no messages, no tool calls and no context injections**, so it records *that the codegen reflex
decided X and the dispatch ended Z*, never *what the model saw*. Different substrate, different
grain. Build alongside it; do not widen it into a message log.

Payoff is both engineering (fork, replay, per-injection provenance) and compliance (NIST AU; it
extends the existing `chain_sweep.py` / `case_bundler.py` evidence path to cover agent runs).

### 3.2 Named permission postures

DSH bundles sandbox mode + approval policy into **one named selector** and appends a log-only event
recording the operator's *intent*, separately from the knobs it sets.

ICDEV's equivalent posture is spread across `ICDEV_SAG_APPROVAL_MODE`, `ICDEV_AGENT_APPROVAL_MODE`,
`ICDEV_SAG_ALLOW_MUTATION`, `ICDEV_PRETOOLUSE_ENFORCE`, per-check `ICDEV_<CHECK>_GUARD`,
`args/file_access_tiers.yaml` and `args/sandbox_config.yaml`. Nothing records what posture a given
run was under, or who chose it.

### 3.3 Registration is the only path to execution

Bounded to making the dead declarations honest — wire a point to a real dispatcher **or delete it**,
per ICDEV's own capability-liveness rule. `dispatch()` additionally swallows every handler exception
and counts nothing, so a broken handler is invisible and `capability_consumption.py` cannot measure
the seam.

---

## 4. Deliberately rejected

| Rejected | Why |
|---|---|
| DSH as a vendored harness / `AgentAdapter` backend | Node dependency; `rc.5`; no Windows in the Python SDK; install-time code execution |
| Hot mount/unmount of live components | Cordis's own paper lists cycles, version drift and state loss as unsolved. ICDEV restarts are cheap; `ICDEV_LAZY_CANVASES` already gives lazy mount |
| A third-party *code* plugin marketplace | ICDEV's marketplace installs skills and compliance extensions as file assets. Executable third-party plugins are an install-time RCE surface |
| Waterfall / `next()` around-middleware events | Strictly more expressive than the current priority chain, but nothing in ICDEV needs wrapping today. YAGNI |
| Cordis's "the telemetry seam ships no rules of its own" | Correct for them, wrong for CUI. ICDEV's fail-closed redaction default stays |

**Validation rather than adoption:** DSH's tool pipeline uses monotonic guards that *"can only deny,
never force-allow."* `_compose_pre_tool_hooks` in `agent_loop.py` already composes the approval gate
ahead of `on_pre_tool_use` with "the gate's block wins." That design is already right, and this note
exists so nobody re-derives it.

---

## 5. Sources

Primary (github.com/deepseek-ai, raw.githubusercontent.com, deepseek-harness.github.io, deepseek.com):

- Product page — https://deepseek.com/harness/en/
- Repo — https://github.com/deepseek-ai/deepseek-harness
- `docs/architecture.md`, `docs/cordis-primer.md`, `docs/development.md`, `BENCHMARK.md`
- `docs/cordis-tutorial/01`–`07`; `docs/subsystems/{session,core,tools,subagent,sandbox,filesystem,storage,skills,approval,permission-presets,session-telemetry,session-query,llm-streaming,code-runtime,scope}.md`
- `packages/preset/agent-presets/README.md`; `apps/cli/config/agent-presets/{minimal,code,cordis}/agent.cordis.yml`
- `examples/jsonrpc-agent/minimal.cordis.yml`; `docs/user/guide/python-sdk.md`; `docs/user/develop/basic/publish.md`

Cordis / ecosystem:

- Paper — https://github.com/cordiverse/paper · Org — https://github.com/cordiverse · Koishi — https://github.com/koishijs/koishi
- npm — https://registry.npmjs.org/-/v1/search?text=cordis and `?text=dsh-plugin`
- Topic — https://github.com/topics/dsh-plugin · Curated — https://github.com/0xsline/awesome-deepseek-harness

Independent commentary:

- The Register (2026-08-14) — https://www.theregister.com/ai-and-ml/2026/08/14/deepseeks-innovative-harness-treats-everything-as-a-plug-in/5288095 and its forum thread
- The New Stack — https://thenewstack.io/deepseek-harness-open-source-plugins/
- AgentsPulse — https://agentspulse.github.io/tutorials/deepseek-harness-and-cordis-why-everything-is-a-plugin/
- yage.ai deep analysis — https://yage.ai/share/dsh-deep-analysis-en-20260813.html

### Not verified

- Exact `Agent` interface and `ctx.agents.setFactory()` TypeScript signatures (docs returned summarized).
- The `standard` preset file itself (inferred from the `code` and `cordis` headers, which state
  "Everything in `standard` is here unchanged").
- `packages/{native,e2b,acp,mcp,hooks,identity,guard,spill,typert}` contents.
- Star counts, which ranged 33k → 125k across sources within three days. Treat as volatile.
