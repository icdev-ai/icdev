# Phase 44 — Innovation Adaptation

**CUI // SP-CTI**

| Field | Value |
|-------|-------|
| Phase | 44 |
| Title | Innovation Adaptation — Agent Zero & InsForge Patterns |
| Status | Implemented |
| Priority | P2 |
| Dependencies | Phase 35 (Innovation Engine), Phase 39 (Observability & Operations), Phase 36 (Evolutionary Intelligence) |
| Author | ICDEV Architect Agent |
| Date | 2026-02-23 |

---

## 1. Problem Statement

ICDEV's Innovation Engine (Phase 35) discovers improvement opportunities through web scanning, signal scoring, and compliance triage. However, the internal agent execution model remained single-threaded and stateless — each agent interaction was independent, with no persistent conversation context, no mechanism for mid-stream course correction, and no way for external systems to extend agent behavior at runtime. These limitations prevented ICDEV from adopting proven patterns from cutting-edge agentic AI frameworks.

Agent Zero demonstrated that multi-stream parallel execution with persistent memory consolidation dramatically improves agent effectiveness. InsForge showed that active extension hooks — allowing external code to modify agent behavior at defined hook points — enable ecosystem-level customization without forking the core framework. Neither pattern was available in ICDEV.

Phase 44 adapts 10 capabilities from these frameworks into ICDEV's GOTCHA architecture: multi-stream parallel chat with thread-per-context execution, active extension hooks with behavioral and observational tiers, mid-stream intervention for atomic course correction, dirty-tracking state push for efficient real-time updates, 3-tier history compression for long-running conversations, shared schema enforcement via dataclasses, AI-driven memory consolidation, semantic layer MCP tools for context-aware agent guidance, dangerous pattern detection across 6 languages, and innovation signal registration for external pattern ingestion. Each capability is implemented as a deterministic tool consistent with the GOTCHA separation of concerns.

---

## 2. Goals

1. Enable multi-stream parallel chat with thread-per-context execution (max 5 concurrent per user) for simultaneous agent interactions
2. Provide 10 active extension hook points with behavioral (modify data) and observational (log only) tiers for runtime agent customization
3. Support atomic mid-stream intervention with 3-checkpoint verification for safe course correction during agent execution
4. Implement dirty-tracking state push with SSE debounced at 25ms and HTTP polling at 3s for efficient real-time client updates
5. Compress conversation history with a 3-tier budget model (current topic 50%, historical 30%, bulk 20%) to maintain context within token limits
6. Enforce shared schemas across agent outputs using stdlib dataclasses for air-gap-safe type enforcement
7. Consolidate duplicate and related memory entries using AI-driven similarity detection (Jaccard + LLM) with append-only consolidation logging
8. Index CLAUDE.md sections semantically and serve context-aware guidance to agents via MCP tools based on agent role

---

## 3. Architecture

```
Multi-Stream Chat Manager
  ┌─────────────┬──────────────┬──────────────┐
  │ Context A   │ Context B    │ Context C    │  (max 5/user)
  │ (thread)    │ (thread)     │ (thread)     │
  │ message_q   │ message_q    │ message_q    │
  │ extensions  │ extensions   │ extensions   │
  └──────┬──────┴──────┬───────┴──────┬───────┘
         │             │              │
    ┌────┴────┐   ┌────┴────┐   ┌────┴────┐
    │Extension│   │Extension│   │Extension│
    │ Hooks   │   │ Hooks   │   │ Hooks   │
    │(10 pts) │   │(10 pts) │   │(10 pts) │
    └────┬────┘   └────┬────┘   └────┬────┘
         │             │              │
    Intervention    State Push    History
    (3 checkpoints) (SSE 25ms)   Compression
         │             │         (3-tier)
         ↓             ↓              ↓
    ┌──────────────────────────────────────┐
    │  Shared Schema Enforcement           │
    │  (dataclasses + validate_output())   │
    └──────────────────────────────────────┘
         │
    ┌────┴─────────────────────────────────┐
    │  Memory Consolidation                │
    │  (Jaccard + LLM, append-only log)    │
    └──────────────────────────────────────┘
         │
    ┌────┴─────────────────────────────────┐
    │  Semantic Layer MCP Tools            │
    │  (CLAUDE.md indexing, role mapping)  │
    └──────────────────────────────────────┘
         │
    ┌────┴─────────────────────────────────┐
    │  Code Pattern Scanner + Signal Reg   │
    │  (6 languages, innovation pipeline)  │
    └──────────────────────────────────────┘
```

Chat contexts are scoped to `(user_id, tenant_id)` with a maximum of 5 concurrent contexts per user. Each context runs in its own thread with an independent message queue (`collections.deque`), extension hook chain, and history compression state. Extensions are loaded from numbered Python files following the Agent Zero pattern, with layered override (project > tenant > default) and exception isolation.

---

## 4. Requirements

### 4.1 Multi-Stream Parallel Chat

#### REQ-44-001: Thread-Per-Context Execution
The system SHALL support multiple concurrent chat contexts per user (max 5), each running in its own thread with an independent message queue.

#### REQ-44-002: Context Scoping
Chat contexts SHALL be scoped to `(user_id, tenant_id)` and stored in the `chat_contexts` and `chat_messages` database tables.

#### REQ-44-003: Context Independence
Each context SHALL be independent of intake sessions (Phase 13) and other agent execution channels.

### 4.2 Active Extension Hooks

#### REQ-44-004: 10 Extension Hook Points
The system SHALL provide 10 hook points at defined stages of agent execution (pre-LLM, post-LLM, pre-tool, post-tool, pre-output, post-output, pre-queue, post-queue, session-start, session-end).

#### REQ-44-005: Behavioral and Observational Tiers
Extensions SHALL be classified as behavioral (may modify data flowing through the hook) or observational (read-only logging/metrics), with behavioral extensions subject to stricter safety limits.

#### REQ-44-006: Layered Override
Extension resolution SHALL follow project > tenant > default precedence, with exception isolation ensuring one failing extension cannot crash the agent.

#### REQ-44-007: Safety Limits
Total handler execution time SHALL not exceed 30 seconds across all extensions at a single hook point.

### 4.3 Mid-Stream Intervention

#### REQ-44-008: Atomic 3-Checkpoint Intervention
The system SHALL support mid-stream intervention checked at 3 points per loop iteration: pre-LLM, post-LLM, and pre-queue-pop. Intervention messages SHALL be stored as `role='intervention'` in the message history.

### 4.4 State Management

#### REQ-44-009: Dirty-Tracking State Push
The system SHALL track per-client dirty/pushed version counters with SSE debounced at 25ms and HTTP polling at 3s. Clients SHALL send `?since_version=N` for incremental updates.

#### REQ-44-010: 3-Tier History Compression
The system SHALL compress conversation history using a 3-tier budget: current topic 50%, historical summaries 30%, bulk archive 20%. Topic boundaries SHALL be detected by time gap (>30 min) or keyword shift (>60%).

### 4.5 Schema and Memory

#### REQ-44-011: Shared Schema Enforcement
Agent outputs SHALL be validated against shared schemas using stdlib `dataclasses` with optional Pydantic support, backward compatible via `to_dict()` and `validate_output()` methods.

#### REQ-44-012: AI-Driven Memory Consolidation
The system SHALL optionally consolidate duplicate memory entries using hybrid search (Jaccard keyword fallback + optional LLM) with decisions logged to an append-only `memory_consolidation_log` table.

### 4.6 Semantic Layer and Pattern Detection

#### REQ-44-013: Semantic Layer MCP Tools
The MCP context server SHALL index CLAUDE.md sections by `##` headers, cache with configurable TTL, and serve role-appropriate sections to agents based on agent-role-to-section mapping.

#### REQ-44-014: Dangerous Pattern Detection
The code pattern scanner SHALL detect dangerous patterns (eval, exec, os.system, SQL injection, command injection) across 6 languages using declarative YAML-configured regex patterns.

#### REQ-44-015: Innovation Signal Registration
External patterns and framework analyses SHALL be registered as innovation signals with 5-dimension weighted scoring (novelty, feasibility, compliance_alignment, user_impact, effort).

---

## 5. Database Schema

### Tables

| Table | Purpose |
|-------|---------|
| `chat_contexts` | Chat context metadata — user_id, tenant_id, status, created_at, last_active, context_name |
| `chat_messages` | Chat message storage — context_id, role (user/assistant/intervention), content, timestamp, token_count |
| `chat_tasks` | Task tracking per chat context — context_id, task description, status, result |
| `extension_registry` | Registered extensions — name, hook_point, tier (behavioral/observational), file_path, priority, scope |
| `extension_execution_log` | Extension execution audit — extension_id, hook_point, duration_ms, success, error_message |
| `memory_consolidation_log` | Append-only log of consolidation decisions — entry_ids, action (MERGE/REPLACE/KEEP_SEPARATE/UPDATE/SKIP), rationale |

---

## 6. Tools

| Tool | Purpose |
|------|---------|
| `tools/agent/chat_manager.py` | Multi-stream parallel chat — context lifecycle, message routing, thread management |
| `tools/agent/extension_manager.py` | Active extension hook system — load, register, execute extensions with safety limits |
| `tools/agent/state_tracker.py` | Dirty-tracking state push — version counters, SSE dispatch, incremental updates |
| `tools/agent/history_compressor.py` | 3-tier history compression — topic detection, budget allocation, LLM/truncation fallback |
| `tools/agent/schemas.py` | Shared schema enforcement — dataclass definitions, validate_output(), strict/non-strict modes |
| `tools/memory/memory_consolidation.py` | AI-driven memory consolidation — similarity detection, LLM decision, append-only logging |
| `tools/mcp/context_server.py` | Semantic layer MCP tools — CLAUDE.md indexer, role-based section serving, cache management |
| `tools/security/code_pattern_scanner.py` | Dangerous pattern detection — 6-language regex scanner with declarative YAML config |
| `tools/innovation/register_external_patterns.py` | Innovation signal registration — external pattern ingestion into innovation pipeline |

---

## 7. Architecture Decisions

| ID | Decision | Rationale |
|----|----------|-----------|
| D257-D260 | Thread-per-context with collections.deque message queues | Independent execution per context, max 5/user, scoped to (user_id, tenant_id) |
| D261-D264 | Extensions loaded from numbered Python files (Agent Zero pattern) | Behavioral/observational tiers, layered override, exception isolation, 30s safety limit |
| D265-D267 | Atomic 3-checkpoint intervention | Checked at pre-LLM, post-LLM, pre-queue-pop; checkpoint preservation; role='intervention' messages |
| D268-D270 | Dirty-tracking with SSE 25ms debounce, HTTP 3s polling | Efficient incremental updates; clients send ?since_version=N |
| D271-D274 | 3-tier history compression with topic boundary detection | Budget: 50%/30%/20%; time gap >30min or keyword shift >60% for topic boundaries |
| D275 | Shared schemas via stdlib dataclasses | Air-gap safe, optional Pydantic, backward compatible to_dict()/validate_output() |
| D276 | AI-driven memory consolidation with Jaccard fallback | Optional --consolidate flag, LLM decides action, append-only consolidation log |
| D277 | CLAUDE.md section indexing via ## header parsing | Agent-role-to-section mapping, cache TTL, air-gap safe (stdlib only) |
| D278 | Dangerous pattern detection via declarative YAML | Unified scanner across 6 languages, callable from marketplace/translation/security |
| D279 | External patterns registered as innovation signals | 5-dimension scoring, feeds Phase 35 pipeline |

---

## 8. Security Gate

**Innovation Adaptation Gate:**
- Extension execution must not exceed 30 seconds total per hook point
- Behavioral extensions must be registered with explicit scope (project/tenant/default)
- Memory consolidation decisions must be logged to append-only audit trail
- Dangerous pattern scanner must detect eval/exec/os.system patterns with zero false negatives on known test cases
- Code pattern gate: max_critical=0, max_high=0, max_medium=10

---

## 9. Commands

```bash
# Code pattern scanning
python tools/security/code_pattern_scanner.py --project-dir /path --json
python tools/security/code_pattern_scanner.py --project-dir /path --gate --json

# Memory consolidation
python tools/memory/memory_consolidation.py --consolidate --json
python tools/memory/memory_consolidation.py --dry-run --json

# Innovation signal registration
python tools/innovation/register_external_patterns.py --source "Agent Zero" --patterns patterns.json --json

# Semantic layer context
# MCP server: icdev-context with tools: fetch_docs, list_sections, get_icdev_metadata, get_project_context, get_agent_context

# Configuration
# args/extension_config.yaml — 10 hook points, layered override, safety limits
# args/context_config.yaml — CLAUDE.md indexing, cache TTL, agent-role mapping
# args/code_pattern_config.yaml — Per-language patterns, scan settings, severity classification
```
