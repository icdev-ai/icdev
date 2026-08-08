# Phase SAG — Standalone Agent Runtime

**Status:** Complete (18/18 tasks). **Card:** `sag-` (`args/projects.yaml`).
**Governing decision:** compose existing primitives, don't rebuild (ADR **D384**).
**Source analysis:** `C:\AI\searches\icdev-standalone-agent-transformation-roadmap.md`,
`icdev-standalone-agent-findings.md` (several claimed gaps were stale — see
*Corrections* below).

## What it is

A persistent, interactive agent that runs from a plain shell — **no Claude Code,
no web session**. It is a thin orchestration shell over ICDEV's production
engines: the agent loop, chat persistence, the LLM provider abstraction, the
daemon/reflex scheduler, NOVA skill generation, and the Remote Command Gateway.
It introduces **no new LLM execution path and no new storage abstraction**.

Canonical package: `tools/agent_runtime/` (mirrored to `icdev/tools/agent_runtime/`).

## Surface

### Runtime & sessions (sag-rt-01…04, sag-mem-01, sag-del-01)
- `AgentRuntime.run_turn()` wraps `icdev.tools.llm.agent_loop.run_agent_loop`
  (native tool-use, budget caps, context compression, memory injection, resume).
- `RuntimeSession` couples `chat_manager` (`chat_contexts`/`chat_messages`) with
  `agent_loop_session` (`agent_loop_sessions`) — resume restores tool-use history.
- Per-user profile memory (`sag_user_profiles`, migration 287): durable
  facts/preferences injected at session start; `/memory` command.
- `icdev chat` (REPL / `-q` single-shot / `--resume` / `--stream`),
  `icdev sessions list|export|search`.
- Subagent delegation: isolated child runtimes with scoped toolsets.

### Project context at session start (hgx-sess-01)

The runtime never read the project's own instructions. Every `CLAUDE.md` hit in
`tools/agent_runtime/` used the name as a *repo-root sentinel* for a directory
walk — no file was ever opened and put in a prompt, and `AGENTS.md` /
`memory/MEMORY.md` were not handled at all. The agent got the six-line
`_DEFAULT_SYSTEM_PROMPT`, the operator profile, and a few memory hits.

- `tools/agent_runtime/project_context.py` loads `CLAUDE.md`, `AGENTS.md`,
  `memory/MEMORY.md`, and the project-state markdown from the **existing**
  `tools/project/session_context_builder.py` (via its new public
  `render_markdown()` — project detection is not re-implemented).
- **Budgeted against the real window, never a constant.** The block is capped at
  `WINDOW_SHARE` (25%) of `context_budget.available_input_tokens()`, which
  derives from `floor_window_for_function` — the MINIMUM window across the
  routed chain, because `two_tier` applies before chain resolution, RL
  re-ranking reorders it after, and the CLI bridge is prepended at invoke time.
- **Degrades, never swallows.** A 200k model gets the documents intact; a 32k
  chain gets a line-boundary-truncated block (measured: 5.9k tokens, 18% of a
  32k window) carrying `[... N of M lines omitted to fit the context budget —
  read <path> ...]`, so a partial rule set never reads as complete. Per-section
  shares (0.50/0.20/0.15/0.15) put pressure on derived state before the rules;
  unspent allowance rolls forward; a section under `MIN_SECTION_TOKENS` is
  dropped whole and named rather than shaved into a misleading stub.
- Cached per session on `AgentRuntime._project_context()`; `/new` clears it.
  `ICDEV_SAG_PROJECT_CONTEXT=0` disables the block, `ICDEV_SAG_PROJECT_STATE=0`
  keeps the instruction files but skips the DB-backed state summary.

**Three latent bugs fixed in `session_context_builder` on the way** — its
compliance/intake queries named columns that do not exist
(`framework_applicability.status` vs `confirmed`, `intake_sessions.status` vs
`session_status`, and `cato_evidence.readiness_score`/`assessed_at`, which have
never existed). Each raised, was swallowed by the surrounding `except`, and left
the field permanently empty; the tests passed because their fixture DDL declared
the invented columns. Fixture DDL now mirrors `init_icdev_db.py`, and
`cato_readiness` is left unset rather than read from a phantom column
(`cato_monitor.compute_cato_readiness` computes it but prints to stdout
unconditionally, so it cannot be called from a builder whose own output is
stdout markdown).

### Tool discovery, bundles, safety (sag-reg-01/02, sag-safe-01/02)
- Discovery derives agent-loop tool schemas from the MCP `TOOL_REGISTRY` + built-ins
  + `@tool`-decorated functions. Bundles are YAML data (`args/agent_toolsets.yaml`).
- `SafetyGate` composes `run_pre_tool_check` (destructive-git / append-only hard
  blocks) with an approval flow (manual / smart / off). Mutating built-ins
  (`write_file`, `run_command`) are repo-confined and allowlisted; **not MCP-registered**.
- Filesystem checkpoints + rollback back `/snapshot` and `/rollback`.

### User-facing cron (sag-cron-01)
- Durable job store `agent_cron_jobs` (mutable) + append-only `agent_cron_runs`
  (migration 289; `agent_cron_runs` in `APPEND_ONLY_TABLES`). Self-creating.
- Pure-Python interval + 5-field cron parsing (Vixie DOM/DOW). Retry with
  exponential backoff.
- Two exec modes: **agent** (single-shot `AgentRuntime.run_turn`) and **script**
  (allowlisted `python tools/…` via `tools/skills/invoke.py`).
- Delivery: `log` / `email` (SMTP adapter → on-file outbox fallback) /
  `gateway:<ch>:<chat>` (best-effort).
- Ticked by the 1-minute `agent_cron_reflex` Genesis reflex.
- CLI: `icdev cron create|list|pause|resume|remove|run|runs`.

### Profile isolation (sag-prof-01)
- Directory-based operator profiles `~/.icdev/profiles/<name>/` (`env` overlay +
  `skills/`); sticky active pointer (`~/.icdev/active_profile`, `ICDEV_SAG_PROFILE`).
- Session + memory isolation by **tenant namespacing** (`<tenant>::prof:<name>`) —
  no per-profile `.db` files; PostgreSQL stays the single primary. Default profile
  is a strict no-op.
- Durable `sag_profiles` registry + additive `sag_user_profiles.profile` tag
  (migration 290). Runtime resolves the active profile at startup.
- CLI: `icdev profile create|use|which|remove` (alongside existing `apply`/`list`/`show`).

### Skills lifecycle (sag-skl-01)
- Wires NOVA's `generate_skill_spec` into the runtime: novelty-gated post-session
  proposals (env-gated `ICDEV_SAG_SKILL_PROPOSALS`, or `/skill propose`) queue a
  `pending` proposal with provenance (session id + model).
- **HITL** approve/reject/edit; `approve_proposal()` is the sole writer to
  `.agents/skills/icdev-auto-<slug>/SKILL.md`, only on explicit approval, with
  provenance frontmatter (`trust: unverified-llm-generated`).
- Curator reflex `sag_skill_curator` (24h, dry-run default): tracks
  `use_count`/`last_activity` in `sag_skill_registry` (migration 291) and
  archives-never-deletes idle, unpinned auto-skills; pin support.

### Gateway integration (sag-gw-01/02, sag-mcp-01)
- Agent-mode: a bound user's free-text routes to the SAG runtime behind the
  unchanged 8-gate security chain (`remote_agent_sessions`, migration 288).
- **Email adapter** (`tools/gateway/adapters/email_channel.py`): stdlib
  `imaplib` poll + `smtplib` send, no new dependency, air-gap safe;
  `enabled:false` by default. Discord was evaluated and rejected.
- External-agent MCP access is the curated-toolset surface (`unified_server.py --toolset`).

## Trust & security posture
- LLM-generated skills carry provenance and stay HITL-gated (D389;
  sandbox-coverage Gap 33). Email inbound is parse-only and chain-gated
  (Gap 34). Mutating tools are allowlisted, repo-confined, and never MCP-exposed.
- Cron delivery and all subsystems degrade gracefully (missing DB / LLM / SMTP
  yields a recorded failure, never a daemon wedge).

## Corrections to the source analysis
The roadmap/findings docs claimed several greenfield gaps that were already
shipped — most traceable to reading a **stale `icdev/` mirror path** rather than
the live `tools/` tree:
- **Gateway** already existed (`tools/gateway/` — Flask, 8-gate chain, 7 adapters);
  `tools/gateway/` is not mirrored to `icdev/`, so `icdev/tools/gateway/` is absent
  and misled the analysis (D385).
- **MCP unified server + tool exposure** already existed; SAG deliberately does not
  register its mutating surface as MCP tools (D386).
- **Scheduling engine** already existed (`tools/daemon/base.py` + reflex registry);
  cron is a thin durable layer, not a new loop (D387).
- **Skill self-creation** already existed via NOVA behind `ICDEV_HARNESS_COLEARN`;
  sag-skl-01 wired it with HITL rather than rebuilding a generator (D389).

## Registration checklist (per CLAUDE.md 8-point)
1. Manifest — `tools/manifest/standalone-agent-runtime.md` (runtime, cron, profiles,
   skills lifecycle, reflexes) + `tools/manifest/remote-command-gateway.md` (Email adapter).
2. Commands reference — CLI surfaced via `icdev` dispatcher (`chat`/`sessions`/`cron`/`profile`).
3. `security_gates.yaml` — none required; mutations flow through the sag-safe-01
   `SafetyGate` + `invoke.py` allowlist (documented, D386).
4. MCP — intentionally not registered (D386); external access is the curated toolset.
5. `APPEND_ONLY_TABLES` — `agent_cron_runs` added; other SAG tables are mutable.
6. conftest — SAG tests are DB-independent (faked persistence); SAG tables self-create
   and are intentionally out of `MINIMAL_ICDEV_SCHEMA`.
7. Companion sync — run at close-out.
8. Coherence gate — run at close-out.
