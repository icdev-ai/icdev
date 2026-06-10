# UI CLI Bridge (UCB)

**Classification:** CUI // SP-CTI  
**Feature Code:** `ucb`  
**Kanban Prefix:** `ucb-`  
**Phase:** 1  
**Ship Date:** 2026-06-10  
**Related:** [UCB CLI bridge needs claude-cli model](../../memory/ucb-cli-bridge-needs-claude-cli-model.md)

---

## 1. Overview

The **UI CLI Bridge** lets the ICDEV™ dashboard route LLM requests through a locally authenticated **Claude Code CLI** (`claude`) instead of cloud API keys. It is the primary air-gap survival mechanism: when no cloud credentials are configured, the bridge automatically front-loads the local CLI into every routing chain so that AI-driven canvases, chat, and co-worker engines continue to work.

The bridge is **non-blocking** and **degradable**: if the CLI cannot answer within a configurable soft-wait window (default 60 s), the job is deferred to a background worker so the HTTP request returns immediately. Chat callers switch to a “still working” placeholder; non-chat callers fall through to the next provider in the chain.

### 1.1 Goals

| # | Goal |
|---|------|
| G1 | Operate without cloud API keys when a local Claude CLI is present. |
| G2 | Degrade gracefully — missing CLI, slow CLI, or disabled bridge all fall back to cloud/local providers. |
| G3 | Never block an HTTP request waiting for a CLI subprocess to finish. |
| G4 | Give the operator a per-page toggle in the dashboard UI. |
| G5 | Provide an interactive prompt panel for ad-hoc CLI queries from any page. |

---

## 2. Architecture

### 2.1 Component Map

```
┌─────────────────────────────────────────────────────────────┐
│                      ICDEV Dashboard                         │
│  ┌──────────────┐      ┌──────────────┐                     │
│  │ Navbar pill  │      │ Prompt panel │                     │
│  │ (indicator)  │      │ (slide-out)  │                     │
│  └──────┬───────┘      └──────┬───────┘                     │
│         │                     │                            │
│         └─────────┬───────────┘                            │
│                   │                                          │
│         ┌─────────▼──────────┐                              │
│         │ /api/cli-bridge/*  │                              │
│         │  · status          │                              │
│         │  · prompt          │                              │
│         └─────────┬──────────┘                              │
│                   │                                          │
│         ┌─────────▼──────────┐                              │
│         │ cli_bridge_api.py  │                              │
│         │ (middleware + API) │                              │
│         └─────────┬──────────┘                              │
│                   │                                          │
│         ┌─────────▼──────────┐                              │
│         │ LLMRouter          │◄────── context-scoped       │
│         │                    │        override (ContextVar)  │
│         └─────────┬──────────┘                              │
│                   │                                          │
│         ┌─────────▼──────────┐                              │
│         │ CLILLMProvider     │                              │
│         │ (tools.llm.cli_   │                              │
│         │  bridge.cli_      │                              │
│         │  provider)         │                              │
│         └─────────┬──────────┘                              │
│                   │                                          │
│         ┌─────────▼──────────┐      ┌──────────────┐        │
│         │ job_store.py       │◄────►│ Dashboard DB │        │
│         │ cli_llm_jobs       │      │ (SQLite/PG)  │        │
│         └─────────┬──────────┘      └──────────────┘        │
│                   │                                          │
│    ┌──────────────┼──────────────┐                         │
│    │              │              │                         │
│ ┌──▼───┐    ┌────▼────┐   ┌────▼────┐                     │
│ │subpro│    │mailbox  │   │chat     │                     │
│ │cess  │    │(external│   │deferred │                     │
│ │backend│   │ worker) │   │  mode   │                     │
│ └──┬───┘    └────┬────┘   └────┬────┘                     │
│    │             │             │                          │
│ ┌──▼───┐    ┌────▼────┐   ┌────▼────┐                     │
│ │claude│    │claude   │   │SSE      │                     │
│ │-p … │    │(interactive│  │progress │                     │
│ └──────┘    │ session) │   │widget   │                     │
│             └─────────┘   └─────────┘                     │
└─────────────────────────────────────────────────────────────┘
```

### 2.2 File Inventory

| Path | Role |
|------|------|
| `tools/llm/cli_bridge/__init__.py` | Package docstring + export contract. |
| `tools/llm/cli_bridge/activate.py` | Auto-enable logic, routing-chain rewrite, context-scoped override (`ContextVar`). |
| `tools/llm/cli_bridge/capability.py` | Host capability probes: `is_cli_headless_capable()`, `mailbox_worker_alive()`. |
| `tools/llm/cli_bridge/cli_provider.py` | `CLILLMProvider` — the LLM provider implementation; job creation, dispatch, soft-wait, deferral. |
| `tools/llm/cli_bridge/job_store.py` | Pure persistence layer over `cli_llm_jobs` — CRUD + claim + stale reaper. |
| `tools/llm/cli_bridge/subprocess_backend.py` | Daemon-thread worker that runs `claude -p … --output-format json` and writes results back. |
| `tools/dashboard/api/cli_bridge_api.py` | Flask middleware (`before_request`/`teardown_request`) + `/api/cli-bridge/status` + `/api/cli-bridge/prompt`. |
| `tools/dashboard/templates/includes/cli_bridge_indicator.html` | Navbar pill with popover toggle (ucb-widget-01). |
| `tools/dashboard/templates/includes/cli_bridge_panel.html` | Interactive slide-out prompt panel (ucb-widget-02). |
| `tests/llm/test_cli_backends.py` | End-to-end backend lifecycle + dynamic selection + env-read allowlist. |
| `tests/llm/test_cli_provider.py` | Provider unit tests. |
| `tests/llm/test_cli_subprocess_backend.py` | Subprocess worker isolated tests. |
| `tests/llm/test_cli_capability.py` | Capability probe tests. |
| `tests/llm/test_cli_job_store.py` | Job store persistence tests. |
| `tests/llm/test_cli_activate.py` | Activation / routing rewrite tests. |
| `tests/dashboard/test_cli_bridge_api.py` | Dashboard API tests. |
| `tools/db/migrations/183_cli_llm_jobs.sql` | Schema for `cli_llm_jobs` (SQLite + PostgreSQL). |

---

## 3. Design Decisions

### D-1 — Job-store backed flow (not inline subprocess)

**Decision:** `CLILLMProvider.invoke` no longer shells out directly. It writes a `cli_llm_jobs` row and lets a backend worker complete it asynchronously.

**Rationale:** An inline `subprocess.run` would block the HTTP thread for the full duration of a CLI synthesis (potentially minutes). The job-store pattern decouples creation from execution so the HTTP response returns in milliseconds, and a daemon thread (or external worker) finishes the job later.

**Trade-off:** Adds a database write + poll per invoke. Mitigated by the soft-wait window: fast CLI answers (< 60 s) still return synchronously to the caller because the worker often finishes before the poll loop elapses.

### D-2 — Soft-wait + deferral (not hard timeout)

**Decision:** The provider waits up to `soft_wait_seconds` (default 60). If the job is still running, it raises `CLIJobDeferred` — a subclass of `LLMUnavailableError`.

**Rationale:**
- Chat callers catch `CLIJobDeferred` specifically and switch the conversation to background mode (SSE progress widget).
- Non-chat callers fall through to the next provider in the routing chain, exactly as they would for any unavailable cloud model.
- The job keeps running; its result is cached in `cli_llm_jobs` for reuse.

### D-3 — Two backend types: subprocess vs mailbox

**Decision:** The provider supports two concrete backends:

| Backend | When selected | Worker location |
|---------|---------------|-----------------|
| `subprocess` | Host is headless-capable (`claude` on PATH) | In-process daemon thread (`subprocess_backend.py`) |
| `mailbox` | Host is not headless-capable, or explicit config | External worker (not yet shipped — uclb-job-05) |

`auto` resolves at dispatch time via `capability.is_cli_headless_capable()`.

### D-4 — ContextVar override (not thread-local)

**Decision:** The per-page toggle uses a `contextvars.ContextVar`, not `threading.local()`.

**Rationale:** ICDEV™ uses both threaded (Flask dev server) and async (eventlet/gunicorn) execution models. `ContextVar` isolates state per asyncio task *and* per thread, and supports nested override/reset via tokens — critical because Flask re-uses worker threads across requests.

### D-5 — RLS-aware mutable table

**Decision:** `cli_llm_jobs` is mutable (status transitions) and carries `tenant_id`/`classification` so it is RLS-aware through `get_connection()`.

**Rationale:** Background workers (which have no Flask request context) see the full table. Dashboard/API requests see only their tenant/classification slice. The stale reaper can therefore safely reap orphaned rows without accidentally touching another tenant's jobs.

---

## 4. Configuration

### 4.1 `args/llm_config.yaml`

The CLI provider must be registered as a provider and referenced in routing chains:

```yaml
providers:
  # … other providers …
  claude-cli:
    type: cli
    cli_binary: claude        # name or absolute path
    backend: auto             # auto | subprocess | mailbox
    soft_wait_seconds: 60

routing:
  codebase_query:
    chain: [claude-cli, anthropic, ollama]
  # … other functions …
```

`type: cli` is the discriminator the router uses to instantiate `CLILLMProvider`.

### 4.2 Environment Variables

| Variable | Default | Scope | Description |
|----------|---------|-------|-------------|
| `ICDEV_CLI_BRIDGE_BACKEND` | — | runtime | Override backend selection (`auto`/`subprocess`/`mailbox`). |
| `ICDEV_CLI_BRIDGE_BINARY` | `claude` | subprocess | Binary name/path for the subprocess backend. |
| `ICDEV_CLI_BRIDGE_MAX_SECONDS` | `900` | subprocess | Hard ceiling for a single CLI invocation (bounds hung processes). |
| `ICDEV_CLI_BRIDGE_MAX_CONCURRENT` | `3` | subprocess | Max concurrent CLI subprocesses. |
| `ICDEV_CLI_BRIDGE_STALE_GRACE_SECONDS` | `300` | subprocess | Grace added to max_seconds before a `running` job is reaped as orphaned. |
| `ICDEV_CLI_HEADLESS` | — | capability | Truthy/falsey override for headless capability probe. |
| `ICDEV_CLI_MAILBOX_HEARTBEAT` | — | capability | ISO-8601 UTC timestamp refreshed by an external mailbox worker. |

> **Note:** None of the `ICDEV_CLI_*` variables are secrets. SIPA's `env_secret` sweep has previously mis-flagged them; each module contains an auditable scope block documenting this separation.

### 4.3 Cookie / Header Toggle

The dashboard front-end sets a session cookie:

- **Cookie:** `icdev_cli_bridge` = `on` / `off`
- **Header:** `X-ICDEV-CLI-Bridge` = `on` / `off` (takes precedence over cookie)

Accepted values (case-insensitive): `on`, `true`, `1`, `yes` → force-enable; `off`, `false`, `0`, `no` → force-disable.

---

## 5. API Surface

### 5.1 Dashboard Routes

| Method | Route | Description |
|--------|-------|-------------|
| `GET` | `/api/cli-bridge/status` | Returns `{enabled, available, state, cookie_name, last_provider, last_model, last_served_at}`. Polled by the navbar indicator every 30 s. |
| `POST` | `/api/cli-bridge/prompt` | Accepts `{prompt, function?, force_bridge?}`; runs the prompt through `LLMRouter.invoke()` with the per-page override applied; returns `{content, provider, model, duration_ms, function}` or `{error, content: ""}`. |

### 5.2 Job Store (Python)

```python
from tools.llm.cli_bridge import job_store

job_id = job_store.create_job(
    function="codebase_query",
    prompt="Summarize the network canvas",
    backend="subprocess",
    classification="CUI // SP-CTI",
)

# Worker side
job = job_store.claim_job("subprocess")   # atomic; returns None if nothing to do
job_store.complete_job(job["id"], "The network canvas …", input_tokens=12, output_tokens=45)
job_store.fail_job(job["id"], "Binary not found")
```

### 5.3 Activation (Python)

```python
from tools.llm.cli_bridge.activate import (
    cli_bridge_override,
    reset_cli_bridge_override,
    maybe_activate,
)

# Router construction time — prepend claude-cli to all chains when enabled
config = maybe_activate(config)

# Request time — force-enable for this context only
token = cli_bridge_override(True)
try:
    router.invoke("codebase_query", request)
finally:
    reset_cli_bridge_override(token)
```

---

## 6. Data Model

### 6.1 `cli_llm_jobs`

| Column | Type | Notes |
|--------|------|-------|
| `id` | `TEXT PRIMARY KEY` | UUIDv4 hex |
| `function` | `TEXT NOT NULL` | Routing function name |
| `prompt` | `TEXT NOT NULL` | Flattened prompt string |
| `system_prompt` | `TEXT` | System prompt (if any) |
| `model_id` | `TEXT` | Logical model id |
| `backend` | `TEXT` | `auto` / `subprocess` / `mailbox` |
| `status` | `TEXT` | `pending` → `running` → `done` / `error` |
| `result` | `TEXT` | Answer text (terminal) |
| `error` | `TEXT` | Failure message (terminal) |
| `context_id` | `TEXT` | Agent / conversation id |
| `input_tokens` | `INTEGER` | Input token count |
| `output_tokens` | `INTEGER` | Output token count |
| `tenant_id` | `TEXT` | RLS tenant |
| `classification` | `TEXT` | RLS classification |
| `created_at` | `TEXT` | ISO-8601 UTC |
| `updated_at` | `TEXT` | ISO-8601 UTC |
| `claimed_at` | `TEXT` | ISO-8601 UTC (worker claim) |
| `completed_at` | `TEXT` | ISO-8601 UTC (terminal) |

**Indexes:**
- `idx_cli_llm_jobs_claim` — `(status, backend, created_at)` for worker polling.
- `idx_cli_llm_jobs_context` — `(context_id)` for conversation-scoped listing.

---

## 7. Operational Behavior

### 7.1 Happy Path (fast answer)

1. Caller → `CLILLMProvider.invoke()`
2. Provider → `job_store.create_job()` → row in `pending`
3. Provider → `_dispatch()` → `subprocess_backend.dispatch()` spawns daemon thread
4. Thread → `_run_job()` → acquires semaphore slot → runs `claude -p … --output-format json`
5. CLI finishes in 15 s → `job_store.complete_job()` → row `done`
6. Provider's `wait_for_job()` loop sees `done` on next poll → returns `LLMResponse`

### 7.2 Slow Path (deferral)

1. Steps 1–3 identical.
2. CLI still running at 60 s soft-wait.
3. `wait_for_job()` returns the still-`running` row.
4. Provider raises `CLIJobDeferred`.
5. **Chat caller** catches it, shows "still working", subscribes to SSE `cli_synthesis` progress events; when the thread eventually completes, the chat manager polls the job row and posts the answer.
6. **Non-chat caller** catches `LLMUnavailableError` (parent class), router falls through to `anthropic` / `ollama` / next in chain.

### 7.3 Stale Reaper

If the host process dies while a job is `running`, the daemon thread dies with it and the row is stranded. Before every `invoke()`, the provider calls `job_store.reap_stale_jobs()`, which transitions any `running` row older than `max_seconds + stale_grace` (default 1200 s) to `error`. This prevents indefinite deferral loops.

### 7.4 Duplicate Dispatch Guard

`subprocess_backend.dispatch()` tracks in-flight job ids in a module-level `set` protected by a `threading.Lock`. Calling dispatch twice for the same id is a silent no-op.

---

## 8. UI Widgets

### 8.1 Navbar Indicator (`cli_bridge_indicator.html`)

- **Pill** on the far right of the dashboard navbar.
- **Dot color:**
  - 🟢 **green** — bridge enabled AND `claude` binary resolvable (`active`)
  - 🟡 **amber** — bridge enabled but binary missing (`missing`)
  - ⚫ **grey** — bridge disabled for this page (`off`)
- **Click** opens a popover with:
  - Status sentence
  - Checkbox: *"Use local CLI for this page"*
  - Last provider served label
- **Cookie:** toggling the checkbox sets `icdev_cli_bridge=on/off` (session cookie, root path, `SameSite=Lax`).

### 8.2 Prompt Panel (`cli_bridge_panel.html`)

- **Slide-out** fixed to bottom-left of every dashboard page.
- Collapsible header with BRIDGE badge.
- Textarea with placeholder hint; `Ctrl/Cmd+Enter` to submit.
- **Run** button POSTs to `/api/cli-bridge/prompt` with:
  - `prompt` — user text (capped at 8000 chars)
  - `function` — derived from current path via `window.ROUTE_MODULE_MAP` (falls back to `codebase_query`)
  - `force_bridge` — read from the `icdev_cli_bridge` cookie (tri-state)
- Result rendered as a pre-formatted answer block; footer shows `served by {provider}/{model} in {duration_ms}ms`.

---

## 9. Security & Compliance

### 9.1 Classification

All `cli_llm_jobs` rows carry `classification` (default `CUI // SP-CTI`). The job store uses `get_connection()`, which injects RLS predicates automatically inside Flask requests. Background workers have no request context and therefore see the full table — this is required so a single worker process can serve all tenants.

### 9.2 Secret Handling

- No API keys are stored in `cli_llm_jobs`.
- The CLI relies on the operator's local Claude Code authentication (browser OAuth or existing session), not on ICDEV-managed secrets.
- The `subprocess_backend` reads only three env vars, all documented as routing overrides, not credentials (see §4.2).

### 9.3 Bounded Execution

- **Concurrency cap:** `BoundedSemaphore(3)` prevents a burst of requests from spawning unlimited CLI processes.
- **Hard ceiling:** `subprocess.run(timeout=900)` bounds a genuinely hung CLI so the thread cannot leak forever.
- **Stale reaper:** orphaned `running` rows are automatically failed after 1200 s.

---

## 10. Testing

### 10.1 Test Matrix

| Module | Tests | Focus |
|--------|-------|-------|
| `test_cli_backends.py` | 22 | End-to-end lifecycle (subprocess + mailbox), dynamic backend selection, env-read allowlist |
| `test_cli_provider.py` | — | Invoke flow, soft-wait, deferral, response building, availability check |
| `test_cli_subprocess_backend.py` | — | Worker thread, JSON parsing, progress emission, error paths, bounded concurrency |
| `test_cli_capability.py` | — | Headless probe, mailbox heartbeat, env overrides |
| `test_cli_job_store.py` | — | CRUD, claim race safety, stale reaper, list filtering |
| `test_cli_activate.py` | — | Enable logic, chain prepend, context override apply/reset |
| `test_cli_bridge_api.py` | — | Dashboard middleware cookie/header parsing, status payload, prompt endpoint |

### 10.2 Running

```bash
pytest tests/llm/test_cli_backends.py -v
pytest tests/llm/test_cli_provider.py -v
pytest tests/dashboard/test_cli_bridge_api.py -v
```

---

## 11. Known Limitations & Future Work

| ID | Limitation | Planned Resolution |
|----|------------|-------------------|
| UCLB-JOB-05 | Mailbox backend worker not yet implemented. Needed for hosts where `claude` is not on PATH but an interactive Claude Code session is available elsewhere. | External worker that claims jobs via HTTP or file-system mailbox. |
| UCLB-ASYNC-01 | Chat deferred-mode answer posting not yet wired. When `CLIJobDeferred` is raised, the chat UI shows "still working" but does not automatically post the result when the job finishes. | SSE subscription + callback in `chat_manager.py`. |
| UCLB-WIDGET-03 | Prompt panel function derivation depends on `ROUTE_MODULE_MAP`. If a canvas page does not register itself in the map, the fallback `codebase_query` may be suboptimal. | Auto-registration in canvas blueprints. |

---

## 12. Acceptance Criteria

- [x] `claude-cli` provider registered in `args/llm_config.yaml` with `type: cli`.
- [x] `CLILLMProvider` implements `LLMProvider.invoke()` and returns `LLMResponse`.
- [x] `CLIJobDeferred` subclasses `LLMUnavailableError` and carries `job_id`.
- [x] Job store (`cli_llm_jobs`) created via migration 183 with both SQLite and PostgreSQL DDL.
- [x] Subprocess backend runs `claude -p … --output-format json` in a daemon thread, bounded by semaphore + timeout.
- [x] Dashboard navbar indicator polls `/api/cli-bridge/status` and shows green/amber/grey dot.
- [x] Per-page toggle cookie (`icdev_cli_bridge`) seeds `ContextVar` override via middleware.
- [x] Interactive prompt panel slide-out on every page; `Ctrl+Enter` submits; result rendered with provider footer.
- [x] Stale reaper transitions orphaned `running` jobs to `error`.
- [x] All 22 backend tests pass (`pytest tests/llm/test_cli_backends.py`).
- [x] SIPA env-read allowlist test guards unauthorized `os.environ.get` additions.

---

## 13. Changelog

| Date | Change | Commit |
|------|--------|--------|
| 2026-06-10 | Feature documentation completed | `ucb-vv-01-d5` |
| 2026-06-09 | Subprocess backend shipped with bounded concurrency + progress emission | `uclb-job-04` |
| 2026-06-08 | Provider invoke rewritten to job-store pattern + soft-wait deferral | `uclb-job-03` |
| 2026-06-07 | `cli_llm_jobs` table + migration 183 landed | `uclb-job-02` |
| 2026-06-06 | Auto-enable (`activate.py`) + routing-chain rewrite shipped | `uclb-job-01` |
| 2026-06-05 | Dashboard navbar indicator + prompt panel templates | `ucb-widget-01/02` |

---

*End of document — UI CLI Bridge (UCB) Feature Documentation*
