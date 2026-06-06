# CUI // SP-CTI — Seed UCLB (UI→CLI LLM Bridge) Kanban tasks
"""Seed kanban_tasks for the UI->CLI LLM Bridge project (uclb-*).

Bridges the ICDEV dashboard's LLM calls to a local Claude CLI when the
environment is air-gapped OR has no usable cloud API key (env + BYOK).
A router-level ``cli`` provider routes every LLM call through the CLI via a
unified job store with two auto-selected backends (subprocess / mailbox),
no request timeouts, and live progress in the browser.

Tasks are seeded to BACKLOG in a single linear depends_on_task_id chain so
the dispatcher implements them in dependency order. Plan reference:
~/.claude/plans/in-air-gap-environment-they-effervescent-wreath.md
"""


import sys
from datetime import datetime, timezone
from pathlib import Path

_BASE = Path(__file__).resolve().parents[2]
if str(_BASE) not in sys.path:
    sys.path.insert(0, str(_BASE))

from tools.db.storage import get_connection  # noqa: E402
from tools.kanban.task_factory import create_tasks  # noqa: E402


NOW = datetime.now(timezone.utc).isoformat()


def seed():
    conn = get_connection()
    try:
        conn.execute("PRAGMA journal_mode=WAL")
    except Exception:
        pass  # PG backend — PRAGMA is a no-op / unsupported

    tasks = [
        # ── Epic prov: CLI provider + router integration ───────────────────
        {
            "id": "uclb-prov-01",
            "title": "UCLB: Add cli_bridge provider + claude-cli model to args/llm_config.yaml",
            "description": (
                "Config only — no Python.\n"
                "providers: add\n"
                "  cli_bridge:\n"
                "    type: cli\n"
                "    cli_binary: ${CLAUDE_CLI_PATH:-claude}\n"
                "    backend: ${ICDEV_CLI_BRIDGE_BACKEND:-auto}   # auto|subprocess|mailbox\n"
                "    soft_wait_seconds: 60\n"
                "models: add\n"
                "  claude-cli:\n"
                "    provider: cli_bridge\n"
                "    model_id: ${ICDEV_CLI_BRIDGE_MODEL:-claude-opus-4-8}\n"
                "    max_output_tokens: 64000\n"
                "Do NOT yet edit routing chains (auto-prepend handled in uclb-prov-05)."
            ),
            "task_type": "chore",
            "priority": "critical",
            "depends_on_task_id": None,
        },
        {
            "id": "uclb-prov-02",
            "title": "UCLB: Create tools/llm/cli_bridge/cli_provider.py (CLILLMProvider)",
            "description": (
                "New package tools/llm/cli_bridge/ with __init__.py.\n"
                "cli_provider.py: class CLILLMProvider(LLMProvider) implementing the ABC in\n"
                "tools/llm/provider.py — provider_name='cli', invoke(request, model_id, model_config),\n"
                "check_availability(model_id).\n"
                "invoke(): flatten LLMRequest.system_prompt + messages into a single prompt string,\n"
                "shell out to `claude -p <prompt> --output-format json` via subprocess, parse stdout\n"
                "to LLMResponse (content from result text; input/output_tokens best-effort from the\n"
                "JSON usage block; provider='cli'; model_id passthrough). Honor request.classification.\n"
                "check_availability(): True if the cli_binary is on PATH. Never raise on subprocess\n"
                "failure — surface via LLMUnavailableError so existing fallbacks engage.\n"
                "Keep it simple/synchronous here; job-store wiring is uclb-job-03."
            ),
            "task_type": "build",
            "priority": "critical",
            "depends_on_task_id": "uclb-prov-01",
        },
        {
            "id": "uclb-prov-03",
            "title": "UCLB: Register ptype=='cli' branch in router._get_provider",
            "description": (
                "In tools/llm/router.py _get_provider() (the if/elif on ptype, ~line 424-529),\n"
                "add a new branch before the else:\n"
                "  elif ptype == 'cli':\n"
                "      from tools.llm.cli_bridge.cli_provider import CLILLMProvider\n"
                "      instance = CLILLMProvider(\n"
                "          cli_binary=_expand_env(provider_cfg.get('cli_binary', 'claude')),\n"
                "          backend=provider_cfg.get('backend', 'auto'),\n"
                "          soft_wait_seconds=int(provider_cfg.get('soft_wait_seconds', 60)),\n"
                "      )\n"
                "Match the existing import-guarded pattern (ImportError/Exception -> return None)."
            ),
            "task_type": "build",
            "priority": "critical",
            "depends_on_task_id": "uclb-prov-02",
        },
        {
            "id": "uclb-prov-04",
            "title": "UCLB: Add tools/llm/cli_bridge/capability.py (cached headless-claude probe)",
            "description": (
                "Pure helper. is_cli_headless_capable(use_cache=True) -> bool:\n"
                "  1. reuse tools/airgap/detector.py::_probe_claude_code_cli to confirm `claude` on PATH\n"
                "  2. functional probe: run `claude -p 'ping' --output-format json` with a short bound\n"
                "     (e.g. 8s); True only if it exits 0 with parseable output.\n"
                "Cache result in module-level var for the process (5-min TTL via a passed-in clock-free\n"
                "counter or simple cached flag). Used by uclb-job-06 to pick subprocess vs mailbox.\n"
                "Never raise — return False on any error/timeout."
            ),
            "task_type": "build",
            "priority": "high",
            "depends_on_task_id": "uclb-prov-03",
        },
        {
            "id": "uclb-prov-05",
            "title": "UCLB: Add tools/llm/cli_bridge/activate.py + hook auto-enable in router init",
            "description": (
                "activate.py:\n"
                "  should_enable() -> bool = is_airgap() OR (not _has_cloud_key())\n"
                "    _has_cloud_key(): True if any of ANTHROPIC_API_KEY/OPENAI_API_KEY/GOOGLE_API_KEY/\n"
                "    AZURE_OPENAI_API_KEY/IBM_CLOUD_API_KEY in env is non-empty, OR\n"
                "    tools/dashboard/byok.py::resolve_api_key returns a key for any provider.\n"
                "    (import byok lazily; treat ImportError as no BYOK key.)\n"
                "  prepend_cli_to_chains(config) -> mutates a copy of routing chains so 'claude-cli' is\n"
                "    first in every chain (mirror tools/airgap/config_patcher.py::_rewrite_routing_chains).\n"
                "Hook: in LLMRouter.__init__ (after config load), if should_enable(): prepend_cli_to_chains.\n"
                "Gate behind env ICDEV_CLI_BRIDGE (default on when should_enable; allow ICDEV_CLI_BRIDGE=false\n"
                "to hard-disable). Idempotent — don't double-prepend if claude-cli already first."
            ),
            "task_type": "build",
            "priority": "critical",
            "depends_on_task_id": "uclb-prov-04",
        },
        {
            "id": "uclb-prov-06",
            "title": "UCLB: Unit tests — provider invoke + auto-enable chain prepend",
            "description": (
                "tests/llm/test_cli_provider.py and tests/llm/test_cli_activate.py.\n"
                "Mock subprocess.run/claude (shim-aware monkeypatch: importlib.import_module + setattr,\n"
                "NOT pytest string form). Assert:\n"
                "  - invoke() returns LLMResponse with content from fake claude stdout\n"
                "  - subprocess failure -> LLMUnavailableError (not a crash)\n"
                "  - should_enable() True when no key + not air-gap monkeypatched, False when key present\n"
                "  - prepend_cli_to_chains puts claude-cli first and is idempotent."
            ),
            "task_type": "test",
            "priority": "high",
            "depends_on_task_id": "uclb-prov-05",
        },
        # ── Epic job: job store + backends ─────────────────────────────────
        {
            "id": "uclb-job-01",
            "title": "UCLB: Add cli_llm_jobs table (migration + conftest schema)",
            "description": (
                "New mutable table cli_llm_jobs:\n"
                "  id TEXT PK, function TEXT, prompt TEXT, system_prompt TEXT, model_id TEXT,\n"
                "  backend TEXT, status TEXT ('pending'|'running'|'done'|'error'),\n"
                "  result TEXT, error TEXT, context_id TEXT, input_tokens INT, output_tokens INT,\n"
                "  tenant_id TEXT, classification TEXT DEFAULT 'CUI // SP-CTI',\n"
                "  created_at TEXT, updated_at TEXT, claimed_at TEXT, completed_at TEXT.\n"
                "Include tenant_id + classification from the start (RLS rule). Add indexes on\n"
                "(status) and (context_id). Add a PG migration under tools/db/migrations/ AND add the\n"
                "CREATE TABLE to tests/conftest.py MINIMAL_ICDEV_SCHEMA. NOT append-only — do NOT add\n"
                "to pre_tool_use.py APPEND_ONLY_TABLES (status mutates)."
            ),
            "task_type": "build",
            "priority": "critical",
            "depends_on_task_id": "uclb-prov-06",
        },
        {
            "id": "uclb-job-02",
            "title": "UCLB: Create tools/llm/cli_bridge/job_store.py (CRUD + wait_for_job)",
            "description": (
                "Pure DB module over cli_llm_jobs using tools.db.storage.get_connection().\n"
                "create_job(function, prompt, system_prompt, model_id, backend, context_id, tenant_id,\n"
                "           classification) -> job_id (status='pending')\n"
                "claim_job(backend) -> job dict|None (atomically flip oldest pending->running)\n"
                "complete_job(job_id, result, input_tokens, output_tokens)\n"
                "fail_job(job_id, error)\n"
                "get_job(job_id) -> dict|None\n"
                "wait_for_job(job_id, timeout) -> job dict (polls every ~0.5s until terminal or timeout;\n"
                "  returns the row even if still running at timeout — caller decides).\n"
                "All RLS-aware via get_connection; never raise on benign races."
            ),
            "task_type": "build",
            "priority": "critical",
            "depends_on_task_id": "uclb-job-01",
        },
        {
            "id": "uclb-job-03",
            "title": "UCLB: Wire CLILLMProvider.invoke -> job store + soft-wait + CLIJobDeferred",
            "description": (
                "Refactor cli_provider.invoke() to: create_job(...), dispatch to the selected backend,\n"
                "then wait_for_job(job_id, soft_wait_seconds). If terminal 'done' -> return LLMResponse.\n"
                "If 'error' -> raise LLMUnavailableError. If still running at soft-wait:\n"
                "  - raise CLIJobDeferred(job_id=...) so chat callers can switch to background mode\n"
                "  - define CLIJobDeferred in tools/llm/cli_bridge/cli_provider.py (or a small errors.py),\n"
                "    subclassing LLMUnavailableError so NON-chat callers transparently hit their existing\n"
                "    rule-based fallback while the job keeps running and its result is cached for reuse."
            ),
            "task_type": "build",
            "priority": "critical",
            "depends_on_task_id": "uclb-job-02",
        },
        {
            "id": "uclb-job-04",
            "title": "UCLB: Subprocess backend worker thread (no hard kill) + emit_progress",
            "description": (
                "tools/llm/cli_bridge/subprocess_backend.py. Reuse the background-thread + rate-limit\n"
                "pattern from tools/dashboard/api/batch.py (~330-468). On dispatch(job): spawn a daemon\n"
                "thread that runs `claude -p <prompt> --output-format json` (no hard timeout; ceiling\n"
                "ICDEV_CLI_BRIDGE_MAX_SECONDS default 900), then complete_job/fail_job. Emit phase\n"
                "progress via tools/dashboard/sse_manager.py::emit_progress (operation_type='cli_synthesis',\n"
                "phases queued->running->done) keyed by job_id. Bounded concurrency (e.g. 3)."
            ),
            "task_type": "build",
            "priority": "critical",
            "depends_on_task_id": "uclb-job-03",
        },
        {
            "id": "uclb-job-05",
            "title": "UCLB: Mailbox backend + standalone worker.py",
            "description": (
                "tools/llm/cli_bridge/mailbox_backend.py: dispatch(job) is a no-op beyond leaving the\n"
                "row 'pending' (an external authed CLI session drains it).\n"
                "tools/llm/cli_bridge/worker.py: standalone CLI the user runs in their authenticated\n"
                "shell — loop: claim_job('mailbox'), run `claude -p`, complete_job/fail_job; --once and\n"
                "--interval flags; --json status. Concept mirrors tools/chat/cli_bridge.py but reversed.\n"
                "Add a heartbeat (e.g. touch a row/file) so capability/health can tell a worker is live."
            ),
            "task_type": "build",
            "priority": "high",
            "depends_on_task_id": "uclb-job-04",
        },
        {
            "id": "uclb-job-06",
            "title": "UCLB: Dynamic backend selection (subprocess else mailbox)",
            "description": (
                "In cli_provider, resolve backend at dispatch time: if config backend=='auto', use\n"
                "subprocess when capability.is_cli_headless_capable() else mailbox; honor explicit\n"
                "'subprocess'/'mailbox' override and ICDEV_CLI_BRIDGE_BACKEND. Record chosen backend on\n"
                "the job row. If mailbox selected but no live worker heartbeat, still enqueue and let the\n"
                "soft-wait/deferred path handle it."
            ),
            "task_type": "build",
            "priority": "high",
            "depends_on_task_id": "uclb-job-05",
        },
        {
            "id": "uclb-job-07",
            "title": "UCLB: Tests — job store + both backends",
            "description": (
                "tests/llm/test_cli_job_store.py + test_cli_backends.py. Assert create/claim/complete/\n"
                "fail/get/wait_for_job semantics, soft-wait timeout returns running row, subprocess\n"
                "backend completes a mocked claude job and emits progress, mailbox claim/complete via a\n"
                "simulated worker, and dynamic selection picks subprocess when capable else mailbox\n"
                "(monkeypatch capability)."
            ),
            "task_type": "test",
            "priority": "high",
            "depends_on_task_id": "uclb-job-06",
        },
        # ── Epic async: chat no-timeout ────────────────────────────────────
        {
            "id": "uclb-async-01",
            "title": "UCLB: Job-aware chat_manager.send_message (pending placeholder, no block)",
            "description": (
                "In tools/dashboard/chat_manager.py send_message()/_process_message() (~1259): wrap the\n"
                "router.invoke call; on CLIJobDeferred, persist a PENDING assistant placeholder message\n"
                "carrying job_id in metadata and return {status:'pending', job_id, message:'Working… "
                "running in background; I'll post the result here.'} instead of blocking the request.\n"
                "Normal (fast) completions are unchanged. Keep the existing no-LLM echo as the final\n"
                "fallback only when the bridge is disabled."
            ),
            "task_type": "build",
            "priority": "critical",
            "depends_on_task_id": "uclb-job-07",
        },
        {
            "id": "uclb-async-02",
            "title": "UCLB: Completion callback posts assistant message into context",
            "description": (
                "When a deferred job finishes (subprocess worker thread, or mailbox worker via a\n"
                "dashboard-side reconciler), replace the pending placeholder with the real assistant\n"
                "message in the chat context (RAG attribution preserved) and bump the context version so\n"
                "the existing /<id>/messages poll + sse_manager deliver it. Idempotent on job_id; handle\n"
                "error jobs by posting a graceful failure note."
            ),
            "task_type": "build",
            "priority": "critical",
            "depends_on_task_id": "uclb-async-01",
        },
        {
            "id": "uclb-async-03",
            "title": "UCLB: Tests — async chat path",
            "description": (
                "tests/dashboard/test_chat_cli_async.py. Simulate a slow CLI job: assert send_message\n"
                "returns status='pending' + job_id quickly, a pending placeholder exists, and after the\n"
                "job completes the placeholder is replaced by the real assistant message via\n"
                "get_messages. Cover the error-job path too."
            ),
            "task_type": "test",
            "priority": "high",
            "depends_on_task_id": "uclb-async-02",
        },
        # ── Epic ui: progress UI ───────────────────────────────────────────
        {
            "id": "uclb-ui-01",
            "title": "UCLB: Chat UI spinner + 'running in background' notice + poll for late message",
            "description": (
                "In the chat template (tools/dashboard/templates/.../chat*.html + its icdev/ mirror):\n"
                "show a spinner while a send is in flight; if the send response is {status:'pending'},\n"
                "render an inline 'running in background — result will be posted' notice and keep polling\n"
                "/<id>/messages until the assistant message arrives, then render it in place. No\n"
                "request-level timeout. Mirror template to icdev/ per the dashboard completeness gate."
            ),
            "task_type": "build",
            "priority": "high",
            "depends_on_task_id": "uclb-async-03",
        },
        {
            "id": "uclb-ui-02",
            "title": "UCLB: Subscribe chat UI to emit_progress cli_synthesis events",
            "description": (
                "Wire the chat page to /api/events/progress (or /api/events/poll per D103) and update the\n"
                "spinner/phase label from emit_progress events whose operation_id matches the active\n"
                "job_id (queued -> calling CLI -> synthesizing -> done). Falls back gracefully to plain\n"
                "message polling if the SSE/poll stream is unavailable."
            ),
            "task_type": "build",
            "priority": "medium",
            "depends_on_task_id": "uclb-ui-01",
        },
        {
            "id": "uclb-ui-03",
            "title": "UCLB: Playwright E2E — spinner -> background -> posted result",
            "description": (
                "Add an E2E (playwright/screenshots/<name>.png for any shots). With a fake slow `claude`\n"
                "stub on PATH and the bridge force-enabled, drive the chat UI: send a message, assert the\n"
                "spinner + 'running in background' notice appear immediately, then assert the real\n"
                "assistant message is posted after the job completes. Reset DB/dashboard per\n"
                ".claude/commands/prepare_app.md first."
            ),
            "task_type": "test",
            "priority": "high",
            "depends_on_task_id": "uclb-ui-02",
        },
        # ── Epic test: V&V + registration ──────────────────────────────────
        {
            "id": "uclb-test-01",
            "title": "UCLB: Manifest + commands.md + feature doc + companion sync",
            "description": (
                "1. Add CLI-bridge tool entries to tools/manifest/llm-providers.md (cli_provider,\n"
                "   job_store, subprocess_backend, mailbox_backend, worker, capability, activate).\n"
                "2. Document worker.py + env vars (ICDEV_CLI_BRIDGE, ICDEV_CLI_BRIDGE_BACKEND,\n"
                "   ICDEV_CLI_BRIDGE_MODEL, CLAUDE_CLI_PATH, ICDEV_CLI_BRIDGE_MAX_SECONDS) in\n"
                "   docs/reference/commands.md.\n"
                "3. Create docs/features/phase-uclb-ui-cli-bridge.md.\n"
                "4. python tools/dx/companion.py --sync --write --json (FOREGROUND; commit first)."
            ),
            "task_type": "chore",
            "priority": "medium",
            "depends_on_task_id": "uclb-ui-03",
        },
        {
            "id": "uclb-vv-01",
            "title": "UCLB V&V: full pytest + ruff + coherence gate",
            "description": (
                "1. ruff check . (use the exact CI Ruff command; a fresh F401 in the lint job is the\n"
                "   real blocker).\n"
                "2. pytest tests/ -v --tb=short (SQLite forced by conftest) — all green.\n"
                "3. python tools/workflow/coherence_checker.py --all --fix --gate — green.\n"
                "4. Sanity: with no API key + bridge enabled, a RAG/chat query yields real CLI synthesis\n"
                "   (not the rule-based echo). FathomDesk smoke unaffected."
            ),
            "task_type": "test",
            "priority": "critical",
            "depends_on_task_id": "uclb-test-01",
        },
    ]

    report = create_tasks(
        "uclb", tasks, conn=conn, strict=False, register_project=False
    )
    print(report.summary())

    conn.close()
    print(f"\nDone. {len(report.seeded)} tasks seeded to BACKLOG for project uclb-*")


if __name__ == "__main__":
    seed()
