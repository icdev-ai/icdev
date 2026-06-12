#!/usr/bin/env python3
"""Seed Kanban tasks for LLM Response Cache (cache-*).

Decomposed from plan: bright-launching-crescent.md
PostgreSQL-native cache engine for token cost reduction.

Run: python tools/kanban/seed_llm_cache.py
"""

from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path

_BASE = Path(__file__).resolve().parents[2]
if str(_BASE) not in sys.path:
    sys.path.insert(0, str(_BASE))

from tools.db.storage import get_connection  # noqa: E402

_NOW = datetime.now(timezone.utc).isoformat()


def _conn():
    return get_connection()


# Project prefix: cache
TASKS = [
    # ═══════════════════════════════════════════════════════════════════════
    # EP1: FOUNDATION — PG Cache Engine + Schema
    # ═══════════════════════════════════════════════════════════════════════
    {
        "id": "cache-fnd-01",
        "title": "CACHE: Create tools/llm/response_cache.py (PG-native UNLOGGED)",
        "description": (
            "Create the core cache engine at tools/llm/response_cache.py.\n\n"
            "Requirements:\n"
            "  - PostgreSQL-native UNLOGGED table (no WAL overhead).\n"
            "  - JSONB columns for tool_calls and structured_output.\n"
            "  - BRIN index on expires_at; partial B-tree on function.\n"
            "  - Class LLMResponseCache with get/set/invalidate/stats API.\n"
            "  - SHA-256 cache key from canonical JSON of function + model_id + messages + system_prompt + temperature + max_tokens + tools + output_schema.\n"
            "  - Lazy TTL eviction + eager LRU sweep when row count exceeds max_entries.\n"
            "  - Timing jitter (0-50ms) on cache hit for InputSnatch mitigation.\n"
            "  - Use get_connection() for dual-backend (PG primary, SQLite fallback).\n"
            "  - Air-gap safe — 100% local.\n\n"
            "Do NOT write router integration yet — that is cache-rt-01."
        ),
        "task_type": "build",
        "priority": "critical",
        "status": "scheduled",
        "scheduled_at": _NOW,
        "depends_on_task_id": None,
    },
    {
        "id": "cache-fnd-02",
        "title": "CACHE: Add llm_response_cache schema to init_icdev_db.py + migration",
        "description": (
            "Add the UNLOGGED table schema to tools/db/init_icdev_db.py.\n\n"
            "Schema:\n"
            "  CREATE UNLOGGED TABLE IF NOT EXISTS llm_response_cache (\n"
            "    cache_key TEXT PRIMARY KEY,\n"
            "    function TEXT NOT NULL,\n"
            "    model_id TEXT NOT NULL,\n"
            "    content TEXT NOT NULL,\n"
            "    tool_calls_json JSONB,\n"
            "    structured_output_json JSONB,\n"
            "    provider TEXT,\n"
            "    input_tokens INTEGER DEFAULT 0,\n"
            "    output_tokens INTEGER DEFAULT 0,\n"
            "    thinking_tokens INTEGER DEFAULT 0,\n"
            "    duration_ms INTEGER DEFAULT 0,\n"
            "    stop_reason TEXT,\n"
            "    hit_count INTEGER DEFAULT 1,\n"
            "    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),\n"
            "    expires_at TIMESTAMPTZ NOT NULL\n"
            "  );\n"
            "  CREATE INDEX idx_llm_cache_expires ON llm_response_cache USING BRIN (expires_at);\n"
            "  CREATE INDEX idx_llm_cache_function ON llm_response_cache (function) WHERE function IS NOT NULL;\n\n"
            "Also create a numbered migration in tools/db/migrations/ so existing PG instances get the table.\n"
            "Add table to tests/conftest.py MINIMAL_ICDEV_SCHEMA."
        ),
        "task_type": "build",
        "priority": "critical",
        "status": "backlog",
        "scheduled_at": None,
        "depends_on_task_id": "cache-fnd-01",
    },
    {
        "id": "cache-fnd-03",
        "title": "CACHE: Add response_cache config block to args/llm_config.yaml",
        "description": (
            "Add top-level response_cache section to args/llm_config.yaml:\n\n"
            "  response_cache:\n"
            "    enabled: true\n"
            "    backend: postgresql\n"
            "    ttl_seconds: 3600\n"
            "    max_entries: 100000\n"
            "    match_strategy: exact\n"
            "    excluded_functions:\n"
            "      - pulse_generation\n"
            "      - news_oracle\n"
            "      - market_scan\n"
            "      - fathomdesk_trap\n"
            "    per_function:\n"
            "      code_generation:\n"
            "        ttl_seconds: 7200\n"
            "      narrative_generation:\n"
            "        ttl_seconds: 1800\n"
            "      nlq_sql:\n"
            "        ttl_seconds: 600\n\n"
            "Ensure _expand_env works if any values use ${VAR} syntax."
        ),
        "task_type": "build",
        "priority": "high",
        "status": "backlog",
        "scheduled_at": None,
        "depends_on_task_id": None,
    },
    # ═══════════════════════════════════════════════════════════════════════
    # EP2: ROUTER — Integration hooks
    # ═══════════════════════════════════════════════════════════════════════
    {
        "id": "cache-rt-01",
        "title": "CACHE: Integrate cache lookup + store into tools/llm/router.py",
        "description": (
            "Modify tools/llm/router.py invoke() method.\n\n"
            "1. After _pre_invoke_redaction() and _rag_augment(), add _cache_lookup(function, request):\n"
            "   - Compute cache key via response_cache.canonical_key().\n"
            "   - If hit: increment hit_count, log telemetry with cached=True, apply timing jitter (0-50ms), return response.\n"
            "   - Skip cache for functions in excluded_functions.\n"
            "\n"
            "2. After successful provider.invoke() (~line 1324), add _cache_store(function, request, response):\n"
            "   - Store if function not excluded and stop_reason != 'error'.\n"
            "   - Use per_function TTL if configured, else default.\n"
            "\n"
            "3. invoke_streaming(): skip caching (streaming responses are ephemeral).\n\n"
            "Do NOT modify provider files — that is cache-prov-01."
        ),
        "task_type": "build",
        "priority": "critical",
        "status": "backlog",
        "scheduled_at": None,
        "depends_on_task_id": "cache-fnd-01",
    },
    {
        "id": "cache-rt-02",
        "title": "CACHE: Update cost_intelligence.py to auto-enable cache",
        "description": (
            "Modify tools/llm/cost_intelligence.py recommend_optimizations().\n\n"
            "When a cache_responses recommendation has confidence >= 0.9:\n"
            "  - Read args/llm_config.yaml.\n"
            "  - If response_cache.enabled is false, flip it to true.\n"
            "  - Write back idempotently (preserve comments/formatting if possible; otherwise use yaml.safe_dump).\n"
            "  - Update the recommendation row status to 'implemented'.\n"
            "  - Log an event to llm_cost_recommendations with note 'auto-enabled by cost_intelligence'.\n\n"
            "Ensure the write does not corrupt YAML structure."
        ),
        "task_type": "build",
        "priority": "medium",
        "status": "backlog",
        "scheduled_at": None,
        "depends_on_task_id": "cache-fnd-03",
    },
    # ═══════════════════════════════════════════════════════════════════════
    # EP3: PROVIDERS — Native cache hints
    # ═══════════════════════════════════════════════════════════════════════
    {
        "id": "cache-prov-01",
        "title": "CACHE: Add provider-level cache hints (Anthropic + OpenAI)",
        "description": (
            "Modify provider files to pass native cache_control hints when request._route_config.cache_control == 'ephemeral'.\n\n"
            "tools/llm/anthropic_provider.py:\n"
            "  - On the last user message block, add cache_control={'type': 'ephemeral'} if hint is set.\n"
            "  - Only for messages list format (not string format).\n\n"
            "tools/llm/openai_provider.py:\n"
            "  - If the API supports prompt caching (check API version), set the message cache_control flag.\n"
            "  - Graceful fallback if unsupported (no error).\n\n"
            "Ollama/DeepSeek: no-op (local inference has no token cost).\n"
            "Add unit tests in existing provider test files or in response_cache_test.py."
        ),
        "task_type": "build",
        "priority": "medium",
        "status": "backlog",
        "scheduled_at": None,
        "depends_on_task_id": "cache-rt-01",
    },
    # ═══════════════════════════════════════════════════════════════════════
    # EP4: FORGE ARTIFACTS
    # ═══════════════════════════════════════════════════════════════════════
    {
        "id": "cache-forge-01",
        "title": "CACHE: Create FORGE goal goals/enable_llm_cache.md",
        "description": (
            "Create goals/enable_llm_cache.md per FORGE framework.\n\n"
            "Content:\n"
            "  - Problem: repeated prompts waste tokens.\n"
            "  - Solution: response cache with PG UNLOGGED table.\n"
            "  - Workflow: detect repeat prompts -> enable cache -> verify hit rate -> report savings.\n"
            "  - Tools used: response_cache.py, cost_intelligence.py, router.py.\n"
            "  - Expected outputs: cache hit rate dashboard, reduced token spend.\n"
            "  - Success criteria: hit rate > 20% for code_generation within 7 days of enablement."
        ),
        "task_type": "build",
        "priority": "medium",
        "status": "backlog",
        "scheduled_at": None,
        "depends_on_task_id": None,
    },
    {
        "id": "cache-forge-02",
        "title": "CACHE: Create context/llm_cache_policy.md",
        "description": (
            "Create context/llm_cache_policy.md.\n\n"
            "Sections:\n"
            "  - Cache design (UNLOGGED, BRIN, JSONB).\n"
            "  - Eviction rules (TTL + LRU).\n"
            "  - PII handling: cache stores post-redaction responses; de-anonymization happens after cache lookup.\n"
            "  - Security mitigations (InputSnatch timing jitter, excluded_functions).\n"
            "  - Operational runbook (clear cache, warm cache, toggle).\n"
            "  - Classification: CUI // SP-CTI."
        ),
        "task_type": "build",
        "priority": "medium",
        "status": "backlog",
        "scheduled_at": None,
        "depends_on_task_id": None,
    },
    {
        "id": "cache-forge-03",
        "title": "CACHE: Create hardprompts/cache_routing.md",
        "description": (
            "Create hardprompts/cache_routing.md — prompt engineering guide for cache-friendly routing.\n\n"
            "Guidelines:\n"
            "  - Use deterministic language (avoid 'now', 'today', 'recent').\n"
            "  - Stable message ordering.\n"
            "  - Separate time-varying context from stable instructions.\n"
            "  - When to use system_prompt vs user message for cache key stability.\n"
            "  - Examples: before/after prompt refactor for better cache hit rate."
        ),
        "task_type": "build",
        "priority": "low",
        "status": "backlog",
        "scheduled_at": None,
        "depends_on_task_id": None,
    },
    # ═══════════════════════════════════════════════════════════════════════
    # EP5: ANVIL + REGISTRATION
    # ═══════════════════════════════════════════════════════════════════════
    {
        "id": "cache-anvil-01",
        "title": "CACHE: Create ANVIL workflow .claude/commands/cache.md",
        "description": (
            "Create .claude/commands/cache.md per ANVIL workflow spec.\n\n"
            "Sections:\n"
            "  ## Stats: python tools/llm/response_cache.py --stats --json\n"
            "  ## Clear: python tools/llm/response_cache.py --clear --function <name> --json\n"
            "  ## Warm: python tools/llm/response_cache.py --warm --function <name> --limit 50 --json\n"
            "  ## Toggle: python tools/llm/response_cache.py --enable / --disable\n\n"
            "Ensure all commands are allowlisted (python tools/... prefix)."
        ),
        "task_type": "build",
        "priority": "medium",
        "status": "backlog",
        "scheduled_at": None,
        "depends_on_task_id": None,
    },
    {
        "id": "cache-reg-01",
        "title": "CACHE: Register in manifest + update CLAUDE.md reference",
        "description": (
            "1. Add entry to tools/manifest/llm-system.md for response_cache.py.\n"
            "2. Add CLI commands to CLAUDE.md docs/reference/commands.md section:\n"
            "   python tools/llm/response_cache.py --stats --json\n"
            "   python tools/llm/response_cache.py --clear --function code_generation --json\n"
            "   python tools/llm/response_cache.py --warm --function narrative_generation --limit 50 --json\n"
            "3. Add args/llm_config.yaml response_cache block to docs/reference/configuration.md if it exists."
        ),
        "task_type": "chore",
        "priority": "medium",
        "status": "backlog",
        "scheduled_at": None,
        "depends_on_task_id": "cache-anvil-01",
    },
    # ═══════════════════════════════════════════════════════════════════════
    # EP6: TESTING + V&V
    # ═══════════════════════════════════════════════════════════════════════
    {
        "id": "cache-test-01",
        "title": "CACHE: Write unit tests tools/llm/response_cache_test.py",
        "description": (
            "Create comprehensive unit tests.\n\n"
            "Test cases:\n"
            "  - Cache hit returns identical LLMResponse.\n"
            "  - TTL expiry evicts entry (insert with backdated expires_at, verify miss).\n"
            "  - LRU eviction when max_entries exceeded (insert N+1 items, verify oldest gone).\n"
            "  - excluded_functions are never cached.\n"
            "  - Timing jitter is applied on cache hit (mock time.sleep, assert called).\n"
            "  - JSONB round-trip for tool_calls and structured_output.\n"
            "  - BRIN index is used for TTL scan (EXPLAIN if PG available).\n"
            "  - get_connection() works for both SQLite and PG backends.\n\n"
            "Use pytest. Mock PG if unavailable."
        ),
        "task_type": "test",
        "priority": "high",
        "status": "backlog",
        "scheduled_at": None,
        "depends_on_task_id": "cache-fnd-02",
    },
    {
        "id": "cache-vv-01",
        "title": "CACHE: Run health_check + coherence_checker + companion sync",
        "description": (
            "Post-implementation validation sequence:\n\n"
            "1. python tools/testing/health_check.py --json\n"
            "   - Verify no import errors after router.py changes.\n"
            "2. python tools/workflow/coherence_checker.py --all --fix --gate\n"
            "   - Verify manifest registration, CLAUDE.md sync, schema consistency.\n"
            "3. python tools/dx/companion.py --sync --write --json\n"
            "   - Sync to icdev/ package mirror and all AI platforms.\n"
            "4. python tools/llm/cost_intelligence.py --recommend --json\n"
            "   - Verify recommendations still parse correctly.\n\n"
            "Report pass/fail for each gate."
        ),
        "task_type": "test",
        "priority": "critical",
        "status": "backlog",
        "scheduled_at": None,
        "depends_on_task_id": "cache-test-01",
    },
    {
        "id": "cache-vv-02",
        "title": "CACHE: End-to-end verification (live cache hit)",
        "description": (
            "Manual E2E verification:\n\n"
            "1. Disable cache: set response_cache.enabled=false in args/llm_config.yaml.\n"
            "2. Run a function twice via python -c \"from tools.llm.router import LLMRouter; ...\".\n"
            "3. Enable cache, run same function twice again.\n"
            "4. Assert second call shows duration_ms ~ jitter range and hit_count > 0.\n"
            "5. Run response_cache.py --stats and confirm non-zero hit rate.\n"
            "6. Verify child_app_generator.py DIRECTORY_TREE includes tools/llm/response_cache.py.\n\n"
            "Capture output and attach to this task."
        ),
        "task_type": "test",
        "priority": "critical",
        "status": "backlog",
        "scheduled_at": None,
        "depends_on_task_id": "cache-vv-01",
    },
]


def seed():
    conn = _conn()
    cur = conn.cursor()

    inserted = 0
    skipped = 0

    for task in TASKS:
        # Check if task already exists
        row = cur.execute(
            "SELECT id FROM kanban_tasks WHERE id = ?", (task["id"],)
        ).fetchone()
        if row:
            skipped += 1
            continue

        cur.execute(
            """
            INSERT INTO kanban_tasks
                (id, title, description, task_type, priority, status,
                 scheduled_at, created_at, updated_at, depends_on_task_id)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                task["id"],
                task["title"],
                task["description"],
                task.get("task_type", "build"),
                task.get("priority", "high"),
                task.get("status", "backlog"),
                task.get("scheduled_at"),
                _NOW,
                _NOW,
                task.get("depends_on_task_id"),
            ),
        )
        inserted += 1

    conn.commit()
    conn.close()
    print(f"Inserted {inserted} tasks, skipped {skipped} existing.")
    return {"inserted": inserted, "skipped": skipped}


if __name__ == "__main__":
    seed()
