# ICDEV Observability → CLI & Kanban Pipeline Gap Analysis
## Langfuse + OpenWorker Adaptation Assessment

**Date:** 2026-08-02  
**Scope:** Map existing observability infrastructure, analyze external resources, and produce prioritized wiring recommendations across three ICDEV surfaces: CLI, Kanban pipeline, and AADC (Agentic AI Canvas).

---

## 1. Current ICDEV Observability Architecture (Strengths)

### What's Built Today

ICDEV already has a **sophisticated three-tier observability stack** that rivals many production systems:

| Layer | Components | Storage |
|-------|-----------|---------|
| **Sources / Emitters** | `audit_logger.py`, `cross_agency_transfer_logger.py`, OpenTelemetry spans (`otel_spans`), W3C PROV provenance, SHAP XAI attributions, AI decisions (`canvas_ai_decisions`), hook events (`hook_events`), container metrics | SQLite (`data/icdev.db`) + PostgreSQL compat via `tools.db.storage` |
| **API / Query** | Flask Blueprints: `traces_api`, `metrics_api`, `events_bp`, `activity_api`, `xai_api`, `provenance_api`, `ai_observatory` | REST + SSE streams |
| **Surfaces** | Dashboard web UI, SSE live streams, HTTP polling, AI Observatory dashboard | Browser |

### Key Schemas in Production

| Table | Purpose | Rows |
|-------|---------|------|
| `audit_trail` | ~300 VALID_EVENT_TYPES covering project lifecycle, security, govcon, cross-agency, agent orchestration, chat, extensions, memory, observability, XAI | Primary audit log |
| `otel_spans` | Waterfall traces with `trace_id`, `span_id`, `duration_ms`, `status_code` | OpenTelemetry-compatible |
| `prov_entities/activities/relations` | W3C PROV lineage tracking | Exportable as PROV-JSON |
| `hook_events` | Real-time event ingest from tools → SSE broadcast | Live pipeline events |
| `cross_agency_transfers` | Dual-writes to `audit_trail` + transfer metadata | Zero-trust data flow |
| `shap_attributions` | XAI explainability per trace | Feature importance |
| `canvas_ai_decisions` | AI Observatory analytics: confidence, model used, decision type | Canvas runtime |
| `metric_snapshots`, `alerts`, `self_healing_events`, `container_metrics`, `failure_log` | SRE-style operational data | Health monitoring |

### The Critical Gap

**All of this data is dashboard-only.** The richest observability surface (`/api/activity/*` — a `UNION ALL` merged feed of `audit_trail` + `hook_events`) has **zero CLI consumer** and **zero Kanban consumer**.

---

## 2. External Resource Analysis

### 2.1 YouTube Video: Langfuse (V1khxZL-yVA)

**Core Thesis:** "Software with a model in the loop doesn't crash when it's wrong. It just carries on confidently."

#### What Langfuse Does (Relevant to ICDEV)

| Feature | ICDEV Analog | Gap |
|---------|------------|-----|
| **Trace-tree architecture** | `otel_spans` waterfall | ✅ ICDEV has spans, but no tree-view CLI |
| **Per-turn cost/latency tracking** | `metric_snapshots` + `canvas_ai_decisions` | ✅ Data exists, no CLI cost reporter |
| **Tool execution tracking** | `audit_trail` + `hook_events` | ✅ Events captured, no trace-tree CLI viewer |
| **Prompt management & versioning** | Hardprompts system | ✅ Similar capability exists |
| **Scores, feedback, evals** | AI Observatory + XAI SHAP | ✅ Rich evaluation layer exists |
| **Full-text search across sessions** | `audit_query.py` — basic query by actor/type | ⚠️ No full-text content search across traces |
| **Real-time SSE dashboard** | `events_bp` SSE stream | ✅ ICDEV already has SSE ingest |
| **Self-hosted (Postgres + ClickHouse + Redis)** | SQLite + PostgreSQL | ⚠️ ICDEV lacks ClickHouse for trace analytics at scale |

#### What ICDEV Can Adopt from Langfuse

1. **Trace-tree CLI viewer** — ICDEV has flat `otel_spans`. Langfuse shows nested model calls → tool calls → retries → reasoning. ICDEV needs a CLI waterfall renderer.
2. **Per-developer cost dashboards** — ICDEV has cost data in `canvas_ai_decisions` and agent telemetry. No CLI tool surfaces per-project or per-command cost.
3. **Session reconstruction** — Langfuse reconstructs full conversation from traces. ICDEV's `chat_manager` stores messages, but there's no unified "reconstruct this session from traces" tool.
4. **GitHub Copilot-style OTLP export** — Langfuse accepts OpenTelemetry natively. ICDEV's `otel_spans` could be exported to any OTLP collector with minimal work.

#### What's NOT Applicable

- Langfuse is an **external SaaS** — ICDEV already has richer in-house schemas (PROV lineage, cross-agency transfers, SHAP explainability).
- ICDEV's NIST 800-53 / zero-trust requirements mean **no external telemetry** (CoWorker's privacy model aligns here).

---

### 2.2 OpenWorker / CoWorker Repository (andrewyng/openworker)

**Architecture:** Python async agent runtime with TurnEngine, pluggable providers, multi-surface UI (TUI/GUI/WebSocket), SQLite memory, and durable audit log.

#### CoWorker Observability Patterns (Directly Adaptable)

| Pattern | CoWorker Implementation | ICDEV Adaptation |
|---------|------------------------|------------------|
| **Event streaming contract** | `EventType` enum + `Event` dataclass: `TURN_START`, `ASSISTANT_DELTA`, `TOOL_PROPOSED`, `TOOL_STARTED`, `TOOL_FINISHED`, `ERROR`, `INTERRUPTED` | ICDEV's `hook_events` is richer (severity, payload JSON) but lacks a structured Python enum contract. **Adopt:** Define `ICDEVEventType` enum aligned with audit_trail event types. |
| **Audit lifecycle hooks** | `TurnEngine` accepts `audit_sink: Callable[[dict], None]`. Every tool call emits structured events at: proposed → started → finished/denied/interrupted | ICDEV's `audit_logger.py` writes directly to DB. **Adopt:** Add `audit_sink` callback parameter to `ChatManager`, `CLI` dispatcher, and Kanban API so consumers can subscribe without DB polling. |
| **Interrupt hooks** | `interrupt_hooks: list[Callable[[], None]]` for external kill signals | ICDEV has no equivalent. **Adopt:** Add `before_interrupt` / `after_interrupt` hooks to long-running CLI commands (`setup`, `provision_db`, `scaffold`, batch runs). |
| **Background-thread telemetry** | `_emit_session_created()` fires on daemon thread so it never blocks session start | ICDEV's CLI runs synchronously. **Adopt:** Wrap telemetry emission in background threads for `icdev init`, `icdev setup`, CLI bridge prompts. |
| **Scheduler overlap guard** | `_running_ids: set[str]` prevents duplicate task execution during tick | ICDEV's Kanban scheduler (`genesis reflexes`) may have overlap. **Adopt:** Add `running_ids` guard to Kanban task reaper / batch executor. |
| **Durable inbox for HITL** | `InboxStore` + `asyncio.Event` waiters for pending approvals | ICDEV has `requirement_intake_hook.py` for chat HITL. **Adopt:** Extend to CLI long-running commands (e.g., `icdev setup --approval-required` pauses and resumes via inbox). |
| **Python logging discipline** | `logging.getLogger("coworker.automation")` with structured levels | ICDEV uses ad-hoc `print()` and JSON output. **Adopt:** Standardize structured logging with component-scoped loggers. |
| **SQLite audit store** | `AuditStore` with RLock + schema migration | ICDEV's `audit_logger.py` is similar. **Adopt:** Add `AuditStore` class abstraction so CLI/Kanban can query without raw SQL. |
| **Sanitization** | Audit payload truncates long values + scrubs secrets | ICDEV's `cross_agency_transfer_logger.py` already sanitizes. **Adopt:** Apply same sanitization to `hook_events` payload JSON. |

#### CoWorker Patterns NOT Applicable to ICDEV

- CoWorker is a **single-user desktop agent** — ICDEV is a multi-tenant SDLC platform with RLS, zero-trust, and cross-agency data flows.
- CoWorker's **cloud telemetry** (`emit_session_created`) sends hashed session IDs to a vendor endpoint. ICDEV's compliance model prohibits this.
- CoWorker has **no Kanban pipeline** — its "inbox" is individual approvals, not task workflow.

---

## 3. Detailed Gap Matrix: Observability Data → Surfaces

### 3.1 CLI (`tools/cli/`)

| Observability Data Exists In | Current CLI Access | Gap |
|------------------------------|-------------------|-----|
| `audit_trail` | `audit_logger.py` CLI writes; `audit_query.py` reads by `--project-id` | ❌ No `icdev audit tail --follow` or `icdev audit stream` command |
| `hook_events` | None | ❌ No CLI consumer for `/api/events/*` poll or SSE |
| `/api/activity/*` merged feed | None | ❌ No unified activity CLI viewer |
| `otel_spans` | None | ❌ No `icdev trace show <trace_id>` or waterfall viewer |
| `metric_snapshots` / `alerts` | None | ❌ No `icdev status` or `icdev health` command |
| `canvas_ai_decisions` | None | ❌ No CLI for AI Observatory stats |
| `cross_agency_transfers` | `cross_agency_transfer_logger.py --query --transfer-id` (requires known ID) | ❌ No list/search/filter CLI |
| `batch_runs` / `batch_run_steps` | None | ❌ No live batch progress CLI |
| Chat contexts (`/api/chat/*`) | `cli_bridge.py` connects | ✅ Already wired |

### 3.2 Kanban Pipeline (`dashboard/api/kanban.py`)

| Observability Data Exists In | Current Kanban Use | Gap |
|------------------------------|--------------------|-----|
| `audit_trail` + `hook_events` | None | ❌ No auto-task creation from audit events |
| `alerts` (`status='firing'`) | None | ❌ No task generation on alert firing |
| `self_healing_events` | None | ❌ No task linkage to auto-remediation patterns |
| `canvas_ai_decisions` (low confidence) | None | ❌ No task creation for confabulation flags |
| `otel_spans` (error spans) | None | ❌ No task creation from trace failures |
| `dispatch_source` | Only `chat:{context_id}` | ❌ No `audit:{id}`, `trace:{id}`, `alert:{id}` sources |
| `kanban_status_transitions` | Persisted but not reacted to | ⚠️ Stored for audit but no automation triggers |
| `kanban_executions` + `kanban_verifications` | Used for Guard-22 gate | ✅ Already wired for verification |

### 3.3 AADC / Visual (`tools/agentic_ai_canvas/`)

| Observability Data Exists In | Current AADC Use | Gap |
|------------------------------|------------------|-----|
| `otel_spans` | Validates `trace-collector` node presence | ❌ Doesn't verify actual DB emission |
| `hook_events` | None | ❌ No integration with canvas validation |
| `audit_trail` | None | ❌ No linkage between design assessments and lifecycle events |
| `cross_agency_transfers` | None | ❌ Not visualized |
| `container_metrics` / `failure_log` | None | ❌ Not surfaced in dashboards |
| `monitoring_engine.py` | In-memory drift scoring | ❌ Drift alerts not persisted to `alerts` or `audit_trail` |

---

## 4. Prioritized Integration Recommendations

### Priority 1: CLI Observability Wiring (Highest Impact)

#### 4.1.1 `icdev status` — Unified Health CLI
**What:** A single command that queries `metric_snapshots` + `alerts` + `otel_spans` (last error) + `audit_trail` (last event) and prints a structured status summary.

**Adapted from CoWorker:** Structured logging + `AuditStore` query abstraction.

**Implementation:**
```python
# tools/cli/status.py — NEW FILE
# Queries: metric_snapshots, alerts, otel_spans, audit_trail
# Output: Rich table (default) or JSON (--json) or live refresh (--watch)
# Exit code: 0 = all green, 1 = warnings, 2 = errors/alerts firing
```

**Files to modify:**
- `tools/cli/__main__.py` — add `status` subcommand dispatch
- `tools/cli/output_formatter.py` — add status-specific formatting

---

#### 4.1.2 `icdev audit tail` — Live Audit Stream CLI
**What:** `tail --follow` equivalent for `audit_trail` + `hook_events` merged feed.

**Adapted from CoWorker:** Event streaming contract (`EventType` enum) + SSE-like long-polling.

**Implementation:**
```python
# tools/cli/audit_tail.py — NEW FILE
# Modes:
#   --follow → poll /api/activity/poll every N seconds
#   --events → show only hook_events
#   --traces → show only otel_spans with errors
#   --project PROJECT_ID → filter
#   --severity {critical,high,medium,low} → filter
```

---

#### 4.1.3 `icdev trace show <trace_id>` — Waterfall CLI Viewer
**What:** Render an `otel_spans` trace as an indented waterfall (like Chrome DevTools Network tab or Langfuse trace tree).

**Adapted from Langfuse:** Tree-view trace rendering.

**Implementation:**
```python
# tools/cli/trace_viewer.py — NEW FILE
# Queries otel_spans by trace_id
# Renders:
#   [200ms] span: model_call → tool_call → retry
#   [ 45ms] span: db_query
#   [ERR  ] span: external_api_call (status=ERROR)
```

---

### Priority 2: Kanban Pipeline Observability Reactivity

#### 4.2.1 Event-Driven Task Creation
**What:** When `hook_events` or `alerts` fires with `severity >= HIGH`, auto-create a Kanban task.

**Adapted from CoWorker:** Event enum contract + `audit_sink` callback pattern.

**Implementation:**
```python
# tools/kanban/observability_reactor.py — NEW FILE
# Subscribes to hook_events SSE stream
# On match (severity filter, event_type filter):
#   → INSERT INTO kanban_tasks (title, description, dispatch_source='audit:{event_id}')
#   → INSERT INTO kanban_task_subscriptions (task_id, webhook_url)
```

**Schema additions:**
```sql
-- Extend kanban_tasks.dispatch_source enum:
ALTER TABLE kanban_tasks ADD COLUMN dispatch_source_type TEXT CHECK (
    dispatch_source_type IN ('chat', 'audit', 'trace', 'alert', 'batch', 'manual')
);
```

---

#### 4.2.2 Alert → Kanban Task Bridge
**What:** When `alerts.status` transitions to `'firing'`, create a Kanban task with `tag:alert`.

**Adapted from CoWorker:** Scheduler overlap guard + durable inbox pattern.

**Implementation:**
```python
# tools/kanban/alert_bridge.py — NEW FILE
# Polls alerts table every N seconds (or via trigger)
# Deduplicates via alert_id → task_id mapping table
# Auto-assigns to on-call rotation (if configured)
```

---

#### 4.2.3 Batch Execution Live Progress in Kanban
**What:** Link `batch_runs` / `batch_run_steps` to Kanban tasks so progress is visible on the board.

**Adapted from CoWorker:** Async task tracking + `_running_ids` overlap guard.

**Implementation:**
```python
# In tools/dashboard/api/batch.py:
# After each step completes:
#   → INSERT INTO kanban_status_transitions (task_id, from_status, to_status, reason='step_N_of_M_completed')
#   → sse_manager.broadcast('task_updated', ...)
```

---

### Priority 3: AADC / Visual Canvas Wiring

#### 4.3.1 Runtime Observability Validation
**What:** When AADC validates a canvas design, verify that `trace-collector` nodes actually emit to `otel_spans`.

**Adapted from CoWorker:** Audit lifecycle hooks (verify emission at each stage).

**Implementation:**
```python
# In tools/agentic_ai_canvas/observability_nodes.py:
# Add validation step:
#   → Query otel_spans WHERE project_id = ? AND span_name LIKE 'canvas_%'
#   → If no spans in last 24h, flag design as "observability stale"
```

---

#### 4.3.2 Persist Monitoring Engine Drift Alerts
**What:** `monitoring_engine.py` computes score drift (`CRITICAL/HIGH/MEDIUM/OK`) but keeps it in-memory. Persist to `alerts` table.

**Adapted from Langfuse:** Scores/evals framework (drift score = eval result).

**Implementation:**
```python
# In tools/agentic_ai_canvas/monitoring_engine.py:
# After drift scoring:
#   → INSERT INTO alerts (name='canvas_drift_{project_id}', severity=drift_level, status='firing')
#   → This then flows to Kanban via Priority 2.2 alert bridge
```

---

### Priority 4: Cross-Cutting Infrastructure (Foundation)

#### 4.4.1 `ICDEVEventType` Enum + Event Bus
**What:** Unify `audit_trail.event_type`, `hook_events.hook_type`, and ad-hoc event strings into a single Python enum.

**Adapted from CoWorker:** `EventType` enum + `Event` dataclass.

**Implementation:**
```python
# tools/events/__init__.py — NEW FILE
from enum import Enum

class ICDEVEventType(str, Enum):
    CLI_COMMAND_STARTED = "cli.command.started"
    CLI_COMMAND_FINISHED = "cli.command.finished"
    CLI_COMMAND_ERROR = "cli.command.error"
    KANBAN_TASK_CREATED = "kanban.task.created"
    KANBAN_STATUS_CHANGED = "kanban.status.changed"
    KANBAN_VERIFICATION_FAILED = "kanban.verification.failed"
    TRACE_SPAN_STARTED = "trace.span.started"
    TRACE_SPAN_FINISHED = "trace.span.finished"
    TRACE_ERROR = "trace.error"
    ALERT_FIRED = "alert.fired"
    ALERT_RESOLVED = "alert.resolved"
    # ... merge all 300+ VALID_EVENT_TYPES from audit_logger.py

@dataclass
class ICDEVEvent:
    type: ICDEVEventType
    project_id: str | None
    actor: str
    payload: dict[str, Any]
    timestamp: datetime
    severity: str = "info"
```

**Consumers:**
- `tools/cli/__main__.py` — emits `cli.command.*` events
- `tools/dashboard/api/kanban.py` — emits `kanban.*` events
- `tools/audit/audit_logger.py` — translates legacy events to new enum

---

#### 4.4.2 `AuditStore` Refactor
**What:** Extract query logic from `audit_logger.py` and `audit_query.py` into a reusable `AuditStore` class (like CoWorker's).

**Implementation:**
```python
# tools/audit/store.py — NEW FILE
class AuditStore:
    def __init__(self, db_path: str | None = None) -> None: ...
    def tail(self, n: int = 50, project_id: str | None = None, event_types: list[str] | None = None) -> list[dict]: ...
    def query(self, filters: AuditQuery) -> list[dict]: ...
    def subscribe(self, callback: Callable[[dict], None]) -> None: ...  # callback = audit_sink pattern
    def get_trace(self, trace_id: str) -> list[dict]: ...
```

---

#### 4.4.3 Background Thread Telemetry Wrapper
**What:** Wrap all CLI telemetry writes in daemon threads so commands never block.

**Adapted from CoWorker:** `_emit_session_created()` background dispatch.

**Implementation:**
```python
# tools/telemetry/background_emitter.py — NEW FILE
import threading

def emit_telemetry(payload: dict, blocking: bool = False) -> None:
    if blocking:
        _write_to_db(payload)
    else:
        threading.Thread(target=_write_to_db, args=(payload,), daemon=True).start()
```

**Files to modify:**
- `tools/cli/__main__.py` — wrap command dispatch in `emit_telemetry()`
- `tools/chat/chat_manager.py` — wrap message processing
- `tools/dashboard/api/batch.py` — wrap step completion

---

## 5. Implementation Order & Estimation

| Phase | Deliverable | Files | Complexity |
|-------|------------|-------|------------|
| **P1** | `ICDEVEventType` enum + Event dataclass | `tools/events/__init__.py` | Low |
| **P1** | `icdev status` CLI | `tools/cli/status.py` + `__main__.py` | Medium |
| **P1** | `icdev audit tail --follow` | `tools/cli/audit_tail.py` + `__main__.py` | Medium |
| **P1** | `AuditStore` refactor | `tools/audit/store.py` | Medium |
| **P2** | `icdev trace show <id>` | `tools/cli/trace_viewer.py` | Medium |
| **P2** | Kanban `dispatch_source_type` schema ext | SQL migration + `kanban.py` | Low |
| **P2** | Event-driven task creation reactor | `tools/kanban/observability_reactor.py` | High |
| **P3** | Alert → Kanban bridge | `tools/kanban/alert_bridge.py` | Medium |
| **P3** | Batch progress → Kanban SSE | Modify `batch.py` | Low |
| **P4** | AADC runtime validation | `observability_nodes.py` | Medium |
| **P4** | Persist drift alerts | `monitoring_engine.py` + `alerts` table | Low |
| **P4** | Background telemetry wrapper | `tools/telemetry/background_emitter.py` | Low |

---

## 6. Summary: What to Adopt vs. Build

| External Pattern | Source | ICDEV Action |
|------------------|--------|-------------|
| Trace-tree CLI viewer | Langfuse | **Build** — ICDEV's `otel_spans` schema is already richer than Langfuse's basic model |
| `audit_sink` callback parameter | CoWorker `TurnEngine` | **Adopt** — Add to `ChatManager`, CLI dispatcher, Kanban API for pluggable observability |
| `EventType` enum + `Event` dataclass | CoWorker `events.py` | **Adopt** — Unify ICDEV's scattered event strings into structured contract |
| Background-thread telemetry | CoWorker `cloud.py` | **Adopt** — Wrap all CLI/Kanban telemetry writes in daemon threads |
| Scheduler overlap guard | CoWorker `scheduler.py` | **Adopt** — Add `_running_ids` to Kanban batch executor and genesis reflexes |
| Durable HITL inbox | CoWorker `inbox.py` | **Adopt** — Extend to CLI long-running commands (setup, provision, scaffold) |
| OTLP export | Langfuse docs | **Build** — Export ICDEV's `otel_spans` to external OTLP collector for users who want it |
| Per-turn cost tracking | Langfuse | **Leverage existing** — `canvas_ai_decisions` already has cost data; just needs CLI surface |
| Scores/evals framework | Langfuse | **Leverage existing** — `monitoring_engine.py` + SHAP already provides evals; needs persistence |
| Self-hosted ClickHouse | Langfuse infra | **Defer** — SQLite + PostgreSQL sufficient until trace volume demands analytics DB |

---

## 7. Files Created / Modified

| Status | Path | Purpose |
|--------|------|---------|
| ✅ Created | `C:\ai\icdev\docs\observability_cli_kanban_integration_analysis.md` | This analysis document |
| 📋 To Create | `tools/events/__init__.py` | Unified event enum + dataclass |
| 📋 To Create | `tools/audit/store.py` | Reusable `AuditStore` query abstraction |
| 📋 To Create | `tools/cli/status.py` | `icdev status` command |
| 📋 To Create | `tools/cli/audit_tail.py` | `icdev audit tail --follow` command |
| 📋 To Create | `tools/cli/trace_viewer.py` | `icdev trace show <id>` command |
| 📋 To Create | `tools/kanban/observability_reactor.py` | Event-driven Kanban task creation |
| 📋 To Create | `tools/kanban/alert_bridge.py` | Alert → Kanban task bridge |
| 📋 To Create | `tools/telemetry/background_emitter.py` | Non-blocking telemetry wrapper |
| 📋 To Modify | `tools/cli/__main__.py` | Add subcommands + event emission hooks |
| 📋 To Modify | `tools/audit/audit_logger.py` | Use `AuditStore` + new event enum |
| 📋 To Modify | `tools/dashboard/api/kanban.py` | Add `dispatch_source_type` + SSE on status change |
| 📋 To Modify | `tools/dashboard/api/batch.py` | Emit Kanban SSE during step execution |
| 📋 To Modify | `tools/agentic_ai_canvas/observability_nodes.py` | Runtime validation against DB |
| 📋 To Modify | `tools/agentic_ai_canvas/monitoring_engine.py` | Persist drift to `alerts` table |

---

**Next Step:** If approved, I can begin implementing Priority 1 (CLI observability commands + event enum foundation) immediately, starting with `tools/events/__init__.py` and `tools/cli/status.py`.
