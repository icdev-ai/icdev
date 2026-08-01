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
