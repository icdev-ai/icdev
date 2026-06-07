# Phase: UCLB — CLI LLM Bridge

**Task ID:** uclb-test-01  
**Type:** chore  
**Status:** done  
**Branch:** irad/feature  

## Summary
Register the CLI LLM Bridge (UCLB) subsystem in canonical project indexes: manifest shard, command reference, and feature documentation, then sync to all AI platforms via the companion.

## What was shipped

### 1. Manifest shard updated
**File:** `tools/manifest/llm-providers.md`

Added 5 CLI-bridge tool entries:
- `activate` — auto-enable + routing-chain rewrite
- `capability` — headless / mailbox worker probes
- `cli_provider` — `CLILLMProvider` (job-store-backed deferral)
- `job_store` — CRUD + claim + wait on `cli_llm_jobs`
- `subprocess_backend` — daemon-thread worker that shells out to `claude-cli`

### 2. Command reference updated
**File:** `docs/reference/commands.md`

New top-level section `## CLI LLM Bridge Commands (UCLB)` with:
- Capability probe one-liners
- Auto-enable invocation
- Direct provider invocation example
- Job store CLI snippets (list, claim, complete)
- Subprocess backend dispatch snippet
- Environment variable reference table (6 vars)

### 3. Feature documentation created
**File:** `docs/features/phase-uclb-ui-cli-bridge.md` (this file)

### 4. Companion sync
Ran `python tools/dx/companion.py --sync --write --json` in the foreground and committed the result.

## Design context

The CLI LLM Bridge lets ICDEV™ serve LLM requests through a locally authenticated Claude Code CLI when no cloud API keys are available (air-gap, BYOK exhaustion, or operator preference). It is **not** an inline shell-out — the provider writes a row to `cli_llm_jobs`, hands the job to a backend worker (subprocess or mailbox), and soft-waits. If the job completes within the soft-wait window the result is returned synchronously; otherwise `CLIJobDeferred` is raised and chat callers switch to background-poll mode (progress bubbles via SSE).

Key modules:
- `tools/llm/cli_bridge/activate.py` — decides whether to prepend `claude-cli` to every routing chain
- `tools/llm/cli_bridge/capability.py` — probes `PATH` for the `claude` binary
- `tools/llm/cli_bridge/cli_provider.py` — `CLILLMProvider` implementing the job-store deferral pattern
- `tools/llm/cli_bridge/job_store.py` — pure persistence layer over `cli_llm_jobs`
- `tools/llm/cli_bridge/subprocess_backend.py` — daemon-thread subprocess worker with bounded concurrency

## Acceptance criteria
- [x] `tools/manifest/llm-providers.md` contains all 5 CLI bridge entries
- [x] `docs/reference/commands.md` contains UCLB section + env var table
- [x] `docs/features/phase-uclb-ui-cli-bridge.md` exists and documents the phase
- [x] Companion sync ran successfully in the foreground
- [x] All changes committed

## Related
- `docs/features/uclb-ui-01-chat-pending-notice.md`
- `docs/features/uclb-ui-02-chat-progress-subscription.md`
- `docs/features/uclb-async-01-job-aware-chat-send.md`
- `docs/features/uclb-job-02-job-store.md`
- `docs/features/uclb-job-03-cli-provider.md`
- `docs/features/uclb-job-04-subprocess-backend.md`
- `docs/features/uclb-job-06-dynamic-backend-selection.md`
- `docs/features/uclb-prov-05-auto-enable.md`
