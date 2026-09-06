# ICDEV™ Agent Runtime Implementation Plan

## Standalone Agent Runtime (SAG) — Full Build-Out

**Target:** Promote ICDEV from a build-pipeline platform into a true agent runtime on par with Hermes Agent, Claude Code, and Codex.

**Scope:** All identified runtime primitives — standing goals, hierarchical planning, kanban coordination, gateway expansion, curator UX, and session search — mapped to existing ICDEV surfaces (chat, AADC, build pipeline).

**Est. Duration:** 10 weeks (5 phases × 2 weeks each)

**Dependencies:** Existing SAG (`tools/agent_runtime/`), ACE Controller, ChatManager, AADC, LLMRouter, DB layer (`tools/db/storage.py`)

---

## Table of Contents

1. [Phase 0: Foundation (Weeks 1–2)](#phase-0-foundation-weeks-12)
2. [Phase 1: Standing Goals (`/goal`) (Weeks 3–4)](#phase-1-standing-goals-goal-weeks-34)
3. [Phase 2: Hierarchical Planning Engine (Weeks 5–6)](#phase-2-hierarchical-planning-engine-weeks-56)
4. [Phase 3: Kanban Work Queue (Weeks 7–8)](#phase-3-kanban-work-queue-weeks-78)
5. [Phase 4: Gateway Expansion (Weeks 9–10)](#phase-4-gateway-expansion-weeks-910)
6. [Phase 5: UX Polish — Curator, Search, Dashboard (Weeks 11–12)](#phase-5-ux-polish--curator-search-dashboard-weeks-1112)
7. [Cross-Cutting Concerns](#cross-cutting-concerns)
8. [Success Metrics](#success-metrics)

---

## Phase 0: Foundation (Weeks 1–2)

### 0.1 Audit Current Runtime Inventory

**Goal:** Exhaustively map every SAG module, its capabilities, and its extension seams.

**Tasks:**
- [ ] Run `grep -r "sag-" tools/agent_runtime/` to extract all module IDs and their documented purposes.
- [ ] Map each module to its Hermes equivalent:

| ICDEV Module | Hermes Equivalent | Status |
|--------------|-------------------|--------|
| `runtime.py` (sag-rt-01) | `run_conversation()` | ✅ Parity |
| `commands.py` (sag-rt-02) | Slash command registry | ✅ Parity |
| `cli.py` (sag-rt-02) | `hermes chat` | ✅ Parity |
| `delegation.py` (sag-del-01) | `delegate_task` | ✅ Parity |
| `sessions.py` | Session persistence | ✅ Parity |
| `profile_memory.py` (sag-mem-01) | Memory provider | ✅ Parity |
| `skills_lifecycle.py` (sag-skl-01) | Skills + Curator | ✅ Parity |
| `cron.py` (sag-cron-01) | Cron scheduler | ✅ Parity |
| `checkpoints.py` (sag-safe-02) | `/rollback` | ✅ Parity |
| `approval_gate.py` (sag-safe-01) | Command approval | ✅ Parity |
| `safety.py` | Safety patterns | ✅ Parity |
| `error_recovery.py` | Error recovery | ✅ Parity |
| `discovery.py` (sag-reg-01) | Tool discovery | ✅ Parity |
| `toolsets.py` (sag-reg-02) | Toolset bundles | ✅ Parity |
| `dispatch.py` | Tool dispatch | ✅ Parity |
| `loop_context.py` (clx-fb-01) | Loop feedback / golden patterns | ✅ Parity |
| **Standing goals** | `/goal` | ❌ Missing |
| **Hierarchical planning** | Plan trees | ❌ Missing |
| **Kanban** | `kanban_*` toolset | ❌ Missing |
| **Gateway** | Multi-platform gateway | ⚠️ Partial |

- [ ] Document every DB migration that created SAG tables (287, 289, 291) — they are the foundation for new tables.

**Deliverable:** `docs/internal/sag-inventory-audit.md`

### 0.2 Define Runtime Extension Architecture

**Goal:** Establish a clean pattern for adding new runtime subsystems without bloating `runtime.py`.

**Design:**
- Each new subsystem lives in its own module under `tools/agent_runtime/`.
- Each subsystem exposes a `mount(runtime: AgentRuntime)` function that registers hooks/callbacks on the runtime instance.
- `runtime.py` calls `mount()` for each enabled subsystem at construction time (config-driven via `args/agent_runtime.yaml`).
- This mirrors how Hermes loads toolsets and plugins.

**New directory structure:**
```
tools/agent_runtime/
├── __init__.py
├── __main__.py
├── runtime.py              # Core loop (sag-rt-01) — MINIMAL CHANGES
├── cli.py                  # CLI surface (sag-rt-02)
├── commands.py             # Slash commands (sag-rt-02)
├── sessions.py             # Session persistence
├── builtin_tools.py        # Read-only built-ins
├── mutating_tools.py       # Mutating built-ins
├── discovery.py            # Tool auto-discovery (sag-reg-01)
├── toolsets.py             # Bundle resolution (sag-reg-02)
├── dispatch.py             # Safety dispatch
├── approval_gate.py        # Approval gate (sag-safe-01)
├── safety.py               # Safety patterns
├── checkpoints.py          # Checkpoint / rollback (sag-safe-02)
├── error_recovery.py       # Error recovery
├── delegation.py           # Subprocess delegation (sag-del-01)
├── profile_memory.py       # Per-user memory (sag-mem-01)
├── skills_lifecycle.py     # Skills + curator (sag-skl-01)
├── cron.py                 # Cron scheduler (sag-cron-01)
├── loop_context.py         # Feedback + golden patterns (clx-fb-01)
├── profiles.py             # Profile isolation (sag-prof-01)
│
├── standing_goals.py       # NEW: Standing goal subsystem (sag-goal-01)
├── plan_tree.py            # NEW: Hierarchical plan engine (sag-plan-01)
├── plan_replan.py          # NEW: Local replanning (sag-plan-02)
├── plan_visual.py          # NEW: ASCII / Mermaid export (sag-plan-03)
├── kanban.py               # NEW: Work queue board (sag-kan-01)
├── gateway_adapters/       # NEW: Platform adapters (sag-gw-02)
│   ├── base.py
│   ├── telegram.py         # (existing, move here)
│   ├── discord.py
│   ├── slack.py
│   └── email.py
└── dashboard.py            # NEW: Status dashboard (sag-dash-01)
```

**Deliverable:** `docs/architecture/agent-runtime-extension-model.md`

### 0.3 Create Configuration Surface

**Goal:** Add `args/agent_runtime.yaml` to control which subsystems are active.

**Schema:**
```yaml
agent_runtime:
  enabled: true
  subsystems:
    standing_goals:
      enabled: true
      max_concurrent: 5
      auto_inject: true
    hierarchical_planning:
      enabled: true
      max_depth: 4          # Mission → Phase → Slice → Task
      auto_advance: true
    kanban:
      enabled: true
      default_board: "default"
    gateway:
      enabled: true
      platforms:
        - telegram
        - discord
        - slack
        - email
    curator:
      enabled: true
      auto_run: true
      interval_hours: 24
    dashboard:
      enabled: true
```

**Tasks:**
- [ ] Create `args/agent_runtime.yaml` with defaults.
- [ ] Add `AgentRuntimeConfig` dataclass in `tools/agent_runtime/config.py`.
- [ ] Load config at `AgentRuntime` construction time.

**Deliverable:** `args/agent_runtime.yaml`, `tools/agent_runtime/config.py`

---

## Phase 1: Standing Goals (`/goal`) (Weeks 3–4)

### 1.1 Database Schema

**Goal:** Persist standing goals across sessions with full lifecycle tracking.

**Migration (292):**
```sql
CREATE TABLE IF NOT EXISTS sag_standing_goals (
    goal_id          TEXT PRIMARY KEY,           -- UUID v4
    context_id       TEXT NOT NULL,              -- FK to chat_contexts (nullable for global)
    user_id          TEXT NOT NULL,
    tenant_id        TEXT DEFAULT '',
    title            TEXT NOT NULL,
    description      TEXT DEFAULT '',
    status           TEXT DEFAULT 'pending',     -- pending, active, paused, blocked, completed, cancelled
    priority         INTEGER DEFAULT 0,            -- 0 = default, higher = more urgent
    created_at       TEXT,
    updated_at       TEXT,
    activated_at     TEXT,                       -- when status became active
    completed_at     TEXT,
    blocked_reason   TEXT DEFAULT '',
    progress_json    TEXT DEFAULT '{}',            -- {current_step: N, total_steps: M, pct: 0.75}
    metadata_json    TEXT DEFAULT '{}'             -- {source: "user", tags: [...]}
);

CREATE INDEX IF NOT EXISTS idx_sag_goals_user ON sag_standing_goals(user_id, tenant_id, status);
CREATE INDEX IF NOT EXISTS idx_sag_goals_ctx  ON sag_standing_goals(context_id);
```

**Tasks:**
- [ ] Write migration script in `tools/db/migrations/292_standing_goals.sql`
- [ ] Add migration runner hook to `tools/db/init_icdev_db.py`
- [ ] Add rollback script (`292_standing_goals_rollback.sql`)

**Deliverable:** Migration + rollback scripts, updated init hook.

### 1.2 Core Module: `standing_goals.py`

**Goal:** CRUD + lifecycle + status tracking for standing goals.

**API:**
```python
# tools/agent_runtime/standing_goals.py

class StandingGoal:
    goal_id: str
    context_id: str | None
    title: str
    description: str
    status: str
    priority: int
    progress: dict[str, Any]
    ...

class GoalManager:
    def create(self, title: str, description: str = "", priority: int = 0, context_id: str | None = None) -> StandingGoal
    def get(self, goal_id: str) -> StandingGoal | None
    def list_active(self, user_id: str, tenant_id: str = "") -> list[StandingGoal]
    def list_for_context(self, context_id: str) -> list[StandingGoal]
    def activate(self, goal_id: str) -> StandingGoal
    def pause(self, goal_id: str, reason: str = "") -> StandingGoal
    def complete(self, goal_id: str) -> StandingGoal
    def block(self, goal_id: str, reason: str) -> StandingGoal
    def cancel(self, goal_id: str) -> StandingGoal
    def update_progress(self, goal_id: str, progress: dict) -> StandingGoal
    def delete(self, goal_id: str) -> bool
```

**Tasks:**
- [ ] Implement `StandingGoal` dataclass.
- [ ] Implement `GoalManager` with full CRUD.
- [ ] Add `GoalStatus` enum: `PENDING`, `ACTIVE`, `PAUSED`, `BLOCKED`, `COMPLETED`, `CANCELLED`.
- [ ] All DB access via `get_connection()` (RLS-aware).
- [ ] Degrade gracefully on missing table.

**Deliverable:** `tools/agent_runtime/standing_goals.py` (~400 lines)

### 1.3 Slash Commands

**Goal:** User-facing `/goal` commands in the REPL and gateway.

**Additions to `commands.py`:**
```python
# /goal "Build secure microservice" — create and activate
# /goal status — show active goals with progress
# /goal list — show all goals (all statuses)
# /goal pause 3 — pause goal #3
# /goal resume 3 — resume paused goal
# /goal complete 3 — mark completed
# /goal block 3 "waiting for API keys" — mark blocked with reason
# /goal cancel 3 — cancel and archive
# /goal clear — remove all completed/cancelled from active view
# /goal inject — manually inject goal context into next turn
```

**Tasks:**
- [ ] Add `_cmd_goal()` handler with subcommand dispatch.
- [ ] Add `_cmd_goal_status()` for pretty-printed status table.
- [ ] Update `/help` text to include goal commands.
- [ ] Ensure commands work in both CLI and gateway modes.

**Deliverable:** Updated `tools/agent_runtime/commands.py`

### 1.4 System Prompt Injection

**Goal:** Every turn knows about active standing goals.

**Integration into `runtime.py`:**
```python
# In _effective_system_prompt():
from tools.agent_runtime.standing_goals import GoalManager

goals = GoalManager().list_active(self.user_id, self.tenant_id)
if goals:
    goal_block = "## Active Standing Goals\n"
    for g in goals:
        goal_block += f"- [{g.status}] {g.title}"
        if g.progress.get("pct"):
            goal_block += f" ({g.progress['pct']*100:.0f}%)"
        goal_block += "\n"
    # Prepend to system prompt
```

**Tasks:**
- [ ] Modify `_effective_system_prompt()` to query `GoalManager`.
- [ ] Inject active goals in a compact, non-token-heavy format.
- [ ] Add `max_goals_in_prompt: int = 5` to config to avoid flooding.
- [ ] Cache goal list and invalidate on `/goal` mutation commands.

**Deliverable:** Updated `tools/agent_runtime/runtime.py` (~30 lines added)

### 1.5 Chat Status Integration

**Goal:** ChatManager shows standing goal status alongside conversation status.

**Integration into `tools/chat/chat_manager.py`:**
```python
def get_context_status(self, context_id: str) -> dict:
    status = self._get_base_status(context_id)
    # NEW: append standing goal info
    from tools.agent_runtime.standing_goals import GoalManager
    goals = GoalManager().list_for_context(context_id)
    if goals:
        status["standing_goals"] = [
            {"title": g.title, "status": g.status, "progress": g.progress}
            for g in goals
        ]
    return status
```

**Tasks:**
- [ ] Add standing goal status to chat context payload.
- [ ] Update frontend (`chat-ui`) to render goal badges (pending/active/blocked).
- [ ] Add goal progress bar component to chat sidebar.

**Deliverable:** Updated `tools/chat/chat_manager.py`, frontend component

### 1.6 ACE Controller Integration

**Goal:** Standing goals are first-class entities in the multi-agent system.

**Integration:**
- [ ] Add `StandingGoal` event type to ACE Controller event bus (`tools/ace/event_bus.py`).
- [ ] On goal creation/activation/completion/block, emit events:
  - `GoalCreated`, `GoalActivated`, `GoalProgressUpdated`, `GoalCompleted`, `GoalBlocked`
- [ ] Builder agent subscribes to `GoalActivated` to auto-trigger build pipelines.
- [ ] Status Manager subscribes to `GoalProgressUpdated` to update chat dashboard.

**Deliverable:** Updated ACE event types, agent subscriptions

### 1.7 Testing

- [ ] Unit tests: `tests/agent_runtime/test_standing_goals.py` — CRUD, lifecycle, status transitions.
- [ ] Integration tests: `/goal` commands in CLI REPL, goal persistence across `--resume`.
- [ ] Mock DB tests using temp SQLite.

---

## Phase 2: Hierarchical Planning Engine (Weeks 5–6)

### 2.1 Plan Tree Data Model

**Goal:** Represent plans as persistent, versioned, navigable trees.

**Database Schema (Migration 293):**
```sql
CREATE TABLE IF NOT EXISTS sag_plan_nodes (
    node_id          TEXT PRIMARY KEY,           -- "plan-uuid::phase-1::slice-2::task-3"
    plan_id          TEXT NOT NULL,              -- FK to sag_plans
    parent_id        TEXT DEFAULT '',            -- '' for root
    node_type        TEXT NOT NULL,              -- mission, phase, slice, task, tool_call
    title            TEXT NOT NULL,
    description      TEXT DEFAULT '',
    status           TEXT DEFAULT 'pending',     -- pending, running, completed, failed, skipped, blocked
    priority         INTEGER DEFAULT 0,
    dependencies     TEXT DEFAULT '[]',            -- JSON list of node_ids that must complete first
    tool_schema      TEXT DEFAULT '',            -- JSON tool schema (for task/tool_call nodes)
    result_json      TEXT DEFAULT '{}',           -- {output: "...", duration_ms: 1234, error: ""}
    checkpoint_id    TEXT DEFAULT '',            -- FK to checkpoints (sag-safe-02)
    created_at       TEXT,
    updated_at       TEXT,
    started_at       TEXT,
    completed_at     TEXT,
    assigned_to      TEXT DEFAULT ''             -- agent_id or user_id
);

CREATE TABLE IF NOT EXISTS sag_plans (
    plan_id          TEXT PRIMARY KEY,
    goal_id          TEXT,                       -- FK to sag_standing_goals (optional)
    context_id       TEXT,                       -- FK to chat_contexts
    user_id          TEXT NOT NULL,
    tenant_id        TEXT DEFAULT '',
    title            TEXT NOT NULL,
    status           TEXT DEFAULT 'draft',       -- draft, active, paused, completed, failed
    root_node_id     TEXT,
    version          INTEGER DEFAULT 1,
    created_at       TEXT,
    updated_at       TEXT,
    metadata_json    TEXT DEFAULT '{}'             -- {source: "user", model: "claude", tags: [...]}
);

CREATE INDEX IF NOT EXISTS idx_plan_nodes_plan ON sag_plan_nodes(plan_id);
CREATE INDEX IF NOT EXISTS idx_plan_nodes_parent ON sag_plan_nodes(parent_id);
CREATE INDEX IF NOT EXISTS idx_plan_nodes_status ON sag_plan_nodes(status);
CREATE INDEX IF NOT EXISTS idx_plans_goal ON sag_plans(goal_id);
CREATE INDEX IF NOT EXISTS idx_plans_ctx ON sag_plans(context_id);
```

**Tasks:**
- [ ] Write migration 293.
- [ ] Add `PlanNode` and `Plan` dataclasses.
- [ ] Implement tree traversal: `get_children()`, `get_ancestors()`, `get_leaves()`.

**Deliverable:** Migration + `tools/agent_runtime/plan_tree.py` core data model

### 2.2 Plan Tree Engine

**Goal:** Full CRUD + execution semantics for plan trees.

**API:**
```python
class PlanTree:
    def __init__(self, plan: Plan, nodes: dict[str, PlanNode]):
        ...

    # --- Navigation ---
    def root(self) -> PlanNode
    def children(self, node_id: str) -> list[PlanNode]
    def descendants(self, node_id: str) -> list[PlanNode]
    def leaves(self) -> list[PlanNode]                # All nodes with no children
    def active_path(self) -> list[PlanNode]            # Root → current running node
    def ready_to_run(self) -> list[PlanNode]           # All nodes whose deps are satisfied

    # --- Lifecycle ---
    def create_node(self, parent_id: str, node_type: str, title: str, **kwargs) -> PlanNode
    def start(self, node_id: str)                     # Mark running, set started_at
    def complete(self, node_id: str, result: dict)   # Mark completed, propagate up
    def fail(self, node_id: str, error: str)          # Mark failed, trigger replan
    def skip(self, node_id: str)                      # Mark skipped, continue siblings
    def block(self, node_id: str, reason: str)        # Mark blocked, notify

    # --- Validation ---
    def validate(self) -> list[str]                   # Check for cycles, orphan nodes
    def is_complete(self) -> bool                     # All leaves completed/skipped

    # --- Persistence ---
    def save(self) -> None
    @classmethod
    def load(cls, plan_id: str) -> PlanTree
    @classmethod
    def from_goal(cls, goal: StandingGoal) -> PlanTree  # Auto-generate from goal text
```

**Tasks:**
- [ ] Implement tree navigation with adjacency list (DB) + in-memory cache.
- [ ] Implement lifecycle transitions with validation (can't complete a non-running node).
- [ ] Implement `from_goal()` using LLM decomposition (BedrockClient with structured output).
- [ ] Reuse existing `graphlib.TopologicalSorter` for dependency resolution.

**Deliverable:** `tools/agent_runtime/plan_tree.py` (~600 lines)

### 2.3 Hierarchical Decomposer

**Goal:** Automatically decompose a standing goal or user request into a plan tree.

**API:**
```python
# tools/agent_runtime/plan_decomposer.py

def decompose_goal(
    goal_text: str,
    context: str = "",
    max_depth: int = 4,
    model: str | None = None,
) -> PlanTree:
    """Use LLM to decompose goal into hierarchical plan.

    Returns a PlanTree with Mission→Phase→Slice→Task structure.
    Falls back to rule-based decomposition if LLM fails.
    """
```

**Prompt design:**
```
You are a hierarchical planning engine. Decompose the following goal into a
structured plan tree with up to 4 levels of abstraction:

Level 0: Mission — the overall objective
Level 1: Phase — major stages (e.g., Design → Build → Test → Deploy)
Level 2: Slice — vertical tracer bullets within each phase
Level 3: Task — concrete actionable steps

For each node, provide:
- title (concise, <10 words)
- description (1–2 sentences)
- dependencies (list of node IDs that must complete first)
- node_type (mission, phase, slice, task)

Goal: {goal_text}
Context: {context}

Respond in valid JSON matching this schema:
{schema}
```

**Tasks:**
- [ ] Design structured output schema (JSON).
- [ ] Implement `decompose_goal()` with BedrockClient.
- [ ] Add fallback: rule-based decomposition using `args/design_twice_default.yaml` constraints.
- [ ] Cache decomposed plans in `data/plan_templates/` for reuse.

**Deliverable:** `tools/agent_runtime/plan_decomposer.py` (~300 lines)

### 2.4 Local Replanning Engine

**Goal:** When a subtree fails, replan only that subtree — preserve the rest.

**API:**
```python
# tools/agent_runtime/plan_replan.py

class ReplanEngine:
    def __init__(self, tree: PlanTree):
        ...

    def replan_subtree(
        self,
        failed_node_id: str,
        error: str,
        strategy: str = "regenerate",   # regenerate, retry, skip, escalate
    ) -> PlanTree:
        """Replace the subtree rooted at failed_node_id with a regenerated plan.

        - Regenerate: ask LLM to produce a new subtree for the same objective.
        - Retry: mark node pending and try again (for transient failures).
        - Skip: mark node skipped and continue (for optional steps).
        - Escalate: promote failure to parent node and replan parent.
        """

    def propagate_status(self, node_id: str) -> None:
        """Update parent statuses based on children (e.g., all children done → parent done)."""
```

**Replanning prompt:**
```
A task in the plan has failed. Here is the failed node:
- Title: {node.title}
- Description: {node.description}
- Error: {error}

Here is the surrounding context (parent and sibling nodes):
{context}

Please produce a revised plan for achieving the same objective. The new plan
should avoid the failure mode described above. Respond as a JSON plan tree.
```

**Tasks:**
- [ ] Implement `replan_subtree()` with all four strategies.
- [ ] Implement `propagate_status()` for bottom-up status rollup.
- [ ] Add max_replan_attempts config (default 3) to prevent infinite loops.
- [ ] Add replan history to plan metadata (audit trail).

**Deliverable:** `tools/agent_runtime/plan_replan.py` (~400 lines)

### 2.5 Plan Execution Hook

**Goal:** Hook plan tree advancement into the agent turn loop.

**Integration into `runtime.py`:**
```python
# In run_turn(), after tool execution:

# 1. Detect if any tool call was part of a plan task
# 2. If task completed, mark task as completed in plan tree
# 3. Check if parent phase/slice can now advance
# 4. If task failed, trigger local replanning
# 5. Inject plan status into system prompt for next turn

def _maybe_advance_plan(self, tool_calls: list[dict], results: list[dict]) -> None:
    """Called after tool execution to update plan tree state."""
    ...

def _plan_context_for_prompt(self) -> str:
    """Return compact plan status for system prompt injection."""
    ...
```

**Tasks:**
- [ ] Tag tool calls with `plan_node_id` metadata when they originate from a plan task.
- [ ] Implement `_maybe_advance_plan()` to auto-advance task statuses.
- [ ] Implement `_plan_context_for_prompt()` to show active tasks and next steps.
- [ ] Ensure plan advancement is atomic (DB transaction).

**Deliverable:** Updated `tools/agent_runtime/runtime.py` (~80 lines added)

### 2.6 Slash Commands

**Additions to `commands.py`:**
```python
# /plan create "Build secure microservice" — auto-decompose goal into plan
# /plan tree — ASCII tree visualization of active plan
# /plan show — current active task
# /plan status — summary of plan progress (% complete, blocked tasks)
# /plan advance <task-id> — manually mark task completed
# /plan fail <task-id> <reason> — manually mark task failed, trigger replan
# /plan skip <task-id> — skip a task
# /plan replan <task-id> — force replan of a subtree
# /plan export — export plan as Mermaid diagram or JSON
# /plan import <json> — load plan from JSON
# /plan rollback — restore plan to last checkpoint
```

**Tasks:**
- [ ] Implement all `/plan` handlers.
- [ ] Add ASCII tree rendering (`plan_visual.py`).
- [ ] Add Mermaid export for documentation.
- [ ] Update `/help`.

**Deliverable:** Updated `commands.py`, new `plan_visual.py`

### 2.7 AADC Integration

**Goal:** Visual plan tree in the Agentic AI Canvas.

**Integration:**
- [ ] Add `PlanTreeNode` component to AADC (`tools/agentic_ai_canvas/components/`):
  - Collapsible/expandable tree nodes
  - Color-coded status badges
  - Click-to-replan on failed nodes
  - Drag-and-drop reordering of sibling tasks
- [ ] Add plan tree to canvas data model (`canvas_state.py`).
- [ ] Emit WebSocket events on plan status changes for live UI updates.

**Deliverable:** AADC plan tree component

### 2.8 Build Pipeline Integration

**Goal:** Child App Generator and AppForge use hierarchical plans instead of flat pipelines.

**Integration:**
- [ ] Modify `tools/builder/child_app_generator.py`:
  - On build request, create a standing goal + plan tree.
  - Each of the 16 steps becomes a task node under phases.
  - Auto-advance as steps complete.
  - On step failure, trigger local replan (retry with different template, or skip if optional).
- [ ] Modify `tools/builder/app_forge.py`:
  - Accept plan tree as input instead of flat step list.
  - Execute tasks in dependency order using `PlanTree.ready_to_run()`.

**Deliverable:** Updated builder modules with plan-tree execution

### 2.9 Testing

- [ ] Unit tests: `tests/agent_runtime/test_plan_tree.py` — tree CRUD, traversal, lifecycle.
- [ ] Unit tests: `tests/agent_runtime/test_plan_replan.py` — all four replan strategies.
- [ ] Integration tests: CLI `/plan` commands, full goal→plan→execute→replan cycle.
- [ ] Property tests: validate no cycles, all orphan nodes rejected.

---

## Phase 3: Kanban Work Queue (Weeks 7–8)

### 3.1 Database Schema

**Goal:** Durable SQLite board for multi-agent task coordination.

**Migration (294):**
```sql
CREATE TABLE IF NOT EXISTS sag_kanban_boards (
    board_id         TEXT PRIMARY KEY,
    tenant_id        TEXT DEFAULT '',
    title            TEXT NOT NULL,
    description      TEXT DEFAULT '',
    columns_json     TEXT DEFAULT '["backlog","ready","in_progress","review","done"]',  -- ordered JSON array
    created_at       TEXT,
    updated_at       TEXT
);

CREATE TABLE IF NOT EXISTS sag_kanban_tasks (
    task_id          TEXT PRIMARY KEY,
    board_id         TEXT NOT NULL,
    tenant_id        TEXT DEFAULT '',
    title            TEXT NOT NULL,
    description      TEXT DEFAULT '',
    status           TEXT DEFAULT 'backlog',
    priority         INTEGER DEFAULT 0,
    assignee         TEXT DEFAULT '',            -- agent_id or user_id
    blocked_by       TEXT DEFAULT '[]',            -- JSON list of task_ids
    tags_json        TEXT DEFAULT '[]',
    metadata_json    TEXT DEFAULT '{}',             -- {source: "ace", goal_id: "...", plan_node_id: "..."}
    created_at       TEXT,
    updated_at       TEXT,
    started_at       TEXT,
    completed_at     TEXT,
    claim_expires_at TEXT                          -- for auto-reclaim
);

CREATE INDEX IF NOT EXISTS idx_kanban_tasks_board ON sag_kanban_tasks(board_id);
CREATE INDEX IF NOT EXISTS idx_kanban_tasks_status ON sag_kanban_tasks(status);
CREATE INDEX IF NOT EXISTS idx_kanban_tasks_assignee ON sag_kanban_tasks(assignee);
```

**Deliverable:** Migration 294

### 3.2 Core Module: `kanban.py`

**Goal:** Full Kanban board CRUD with agent-oriented features.

**API:**
```python
class KanbanBoard:
    def __init__(self, board_id: str, columns: list[str]):
        ...

    def create_task(self, title: str, description: str = "", priority: int = 0, **kwargs) -> KanbanTask
    def move_task(self, task_id: str, new_status: str) -> KanbanTask
    def assign_task(self, task_id: str, assignee: str) -> KanbanTask
    def block_task(self, task_id: str, blocked_by_task_id: str) -> KanbanTask
    def unblock_task(self, task_id: str) -> KanbanTask
    def claim_next_ready(self, assignee: str) -> KanbanTask | None  # Atomic claim
    def list_tasks(self, status: str | None = None, assignee: str | None = None) -> list[KanbanTask]
    def get_stats(self) -> dict  # {backlog: N, ready: M, in_progress: P, ...}
    def archive_completed(self, older_than_days: int = 30) -> int
    def auto_reclaim_stale(self, stale_minutes: int = 30) -> list[KanbanTask]
```

**Deliverable:** `tools/agent_runtime/kanban.py` (~500 lines)

### 3.3 CLI Surface

**Add `icdev kanban` subcommand:**
```bash
icdev kanban init <board-name>           # Create a new board
icdev kanban list [--board <id>]         # List tasks
icdev kanban create "Title" [--board <id>] [--priority N] [--assignee <agent>]
icdev kanban move <task-id> <status>
icdev kanban assign <task-id> <agent-id>
icdev kanban block <task-id> <reason>
icdev kanban unblock <task-id>
icdev kanban claim [--assignee <agent-id>] [--board <id>]  # Atomic claim next ready
icdev kanban complete <task-id> [result-json]
icdev kanban archive                     # Archive completed >30 days
icdev kanban stats [--board <id>]        # Show board statistics
```

**Tasks:**
- [ ] Add `kanban_main()` to `tools/agent_runtime/cli.py`.
- [ ] Wire into `icdev` dispatcher.

**Deliverable:** Updated `cli.py`, `__main__.py`

### 3.4 Runtime Integration

**Goal:** Agents can claim and complete kanban tasks as part of their turn loop.

**Integration into `runtime.py`:**
```python
class AgentRuntime:
    ...
    def kanban_claim_next(self, board_id: str | None = None) -> KanbanTask | None:
        """Claim the next ready task atomically."""
        ...

    def kanban_complete_task(self, task_id: str, result: dict) -> KanbanTask:
        """Mark a task completed and advance the plan if linked."""
        ...
```

**Integration into `delegation.py`:**
- When a child agent completes, check if it was working on a kanban task.
- If so, auto-complete the task with the child's summary.

**Deliverable:** Updated `runtime.py`, `delegation.py`

### 3.5 ACE Controller Integration

**Goal:** Orchestrator agent manages kanban board as its primary coordination mechanism.

**Integration:**
- [ ] Orchestrator creates tasks on kanban board instead of sending A2A messages directly.
- [ ] Domain agents `claim_next_ready()` instead of waiting for task assignments.
- [ ] On task completion, agent posts result as kanban task comment (new `comments_json` column).
- [ ] Support agents monitor kanban board for blocked tasks and auto-escalate.

**Deliverable:** Updated `tools/agent/team_orchestrator.py`

### 3.6 AADC Integration

**Goal:** Visual Kanban board in the canvas.

**Integration:**
- [ ] Add `KanbanBoardComponent` to AADC:
  - Drag-and-drop columns
  - Task cards with status badges
  - Agent assignment avatars
  - Blocked task highlighting
- [ ] Real-time sync via WebSocket.

**Deliverable:** AADC Kanban component

### 3.7 Testing

- [ ] Unit tests: `tests/agent_runtime/test_kanban.py` — CRUD, atomic claim, reclaim.
- [ ] Integration tests: Multi-agent claim race conditions.
- [ ] Stress tests: 100+ tasks, concurrent claims.

---

## Phase 4: Gateway Expansion (Weeks 9–10)

### 4.1 Adapter Architecture

**Goal:** Clean abstraction for multi-platform messaging.

**New directory:** `tools/agent_runtime/gateway_adapters/`

```python
# tools/agent_runtime/gateway_adapters/base.py

from abc import ABC, abstractmethod

class GatewayAdapter(ABC):
    @abstractmethod
    async def send(self, channel: str, chat_id: str, text: str, *, thread_id: str | None = None) -> bool:
        ...

    @abstractmethod
    async def receive(self) -> AsyncIterator[IncomingMessage]:
        ...

    @abstractmethod
    def health_check(self) -> bool:
        ...

@dataclass
class IncomingMessage:
    platform: str
    channel: str
    chat_id: str
    user_id: str
    text: str
    thread_id: str | None = None
    attachments: list[Attachment] = field(default_factory=list)
```

**Deliverable:** `gateway_adapters/base.py`

### 4.2 Platform Adapters

**Goal:** Implement Discord, Slack, and Email adapters.

| Platform | Library | Auth |
|----------|---------|------|
| Discord | `discord.py` | Bot token |
| Slack | `slack-sdk` | Bot token |
| Email | `smtplib` + `imaplib` | SMTP/IMAP credentials |

**Tasks:**
- [ ] `gateway_adapters/discord.py` — Bot adapter with slash command proxy.
- [ ] `gateway_adapters/slack.py` — Bolt-style adapter with event subscriptions.
- [ ] `gateway_adapters/email.py` — IMAP polling + SMTP sending.
- [ ] `gateway_adapters/telegram.py` — Move existing Telegram bot here.
- [ ] Each adapter implements `send()`, `receive()`, `health_check()`.

**Deliverable:** 4 adapter modules

### 4.3 Gateway Runtime

**Goal:** Long-running process that hosts all platform adapters.

**New module:** `tools/agent_runtime/gateway.py`

```python
class GatewayRuntime:
    def __init__(self, adapters: list[GatewayAdapter]):
        ...

    async def run(self) -> None:
        """Start all adapters and route incoming messages to AgentRuntime."""
        ...

    async def route(self, msg: IncomingMessage) -> None:
        """Find or create a session for the chat, then run a turn."""
        ...

    async def broadcast(self, text: str, *, platforms: list[str] | None = None) -> None:
        """Send a message to all connected channels (or filtered by platform)."""
        ...
```

**Tasks:**
- [ ] Implement asyncio-based gateway runtime.
- [ ] Session affinity: `chat_id` + `platform` → `context_id` mapping.
- [ ] Rate limiting per platform.
- [ ] Approval gate integration: high-risk commands require user confirmation via messaging.

**Deliverable:** `tools/agent_runtime/gateway.py`

### 4.4 CLI Surface

**Add `icdev gateway` subcommand:**
```bash
icdev gateway run              # Start gateway foreground
icdev gateway install          # Install as background service (systemd / Windows service)
icdev gateway start/stop       # Control service
icdev gateway status           # Show adapter health
icdev gateway setup            # Interactive platform configuration
icdev gateway test <platform>  # Send test message
```

**Deliverable:** Updated `cli.py`

### 4.5 Cron Delivery Integration

**Goal:** Cron jobs can deliver results to any platform channel.

**Already partially implemented** in `cron.py` (`gateway:<ch>:<chat>`). Complete it:
- [ ] Wire `_deliver_gateway()` to use new adapter architecture.
- [ ] Support thread_id for threaded channels (Slack, Discord).

**Deliverable:** Updated `cron.py` delivery path

### 4.6 Testing

- [ ] Unit tests: Mock adapter, message routing.
- [ ] Integration tests: Send/receive on each real platform (use test channels).
- [ ] Health check tests: adapter failure detection and restart.

---

## Phase 5: UX Polish — Curator, Search, Dashboard (Weeks 11–12)

### 5.1 Curator Slash Commands

**Additions to `commands.py`:**
```python
# /curator status    — Show skill usage stats (use_count, last_activity, state)
# /curator run       — Trigger background curation manually
# /curator pin <skill>    — Pin a skill (exempt from auto-archive)
# /curator unpin <skill>  — Unpin a skill
# /curator archive <skill> — Manually archive a skill
# /curator restore <skill> — Restore an archived skill
# /curator prune     — Remove archived skills older than N days
```

**Tasks:**
- [ ] Reuse existing `skills_lifecycle.py` functions.
- [ ] Add `_cmd_curator()` handler with subcommand dispatch.
- [ ] Pretty-printed tables for status output.

**Deliverable:** Updated `commands.py`

### 5.2 Session Search CLI

**Additions to `commands.py`:**
```python
# /history [N]       — Show last N messages in current session
# /search <query>    — Full-text search across all sessions
# /sessions list     — List recent sessions (already in cli.py)
# /sessions browse   — Interactive picker
```

**Tasks:**
- [ ] Reuse existing `search_sessions()` from `sessions.py`.
- [ ] Add `_cmd_history()` and `_cmd_search()` handlers.
- [ ] Pretty-print search results with context snippets.

**Deliverable:** Updated `commands.py`

### 5.3 Status Dashboard

**Goal:** Real-time overview of runtime health, goals, plans, and kanban.

**New module:** `tools/agent_runtime/dashboard.py`

**CLI:**
```bash
icdev dashboard           # Launch interactive TUI dashboard
icdev dashboard --json    # Emit JSON snapshot for external monitoring
```

**TUI Components (using `rich` or `textual`):**
- **Runtime Health:** Active sessions, memory usage, LLM provider status.
- **Standing Goals:** Active goals with progress bars.
- **Plan Tree:** ASCII tree of current plan with status colors.
- **Kanban Board:** Compact board view (5 columns).
- **Recent Events:** Last 10 ACE events / cron runs / delegations.

**Tasks:**
- [ ] Design TUI layout.
- [ ] Implement data aggregation from all subsystems.
- [ ] Add auto-refresh (every 5s).
- [ ] Support `--json` mode for CI/external dashboards.

**Deliverable:** `tools/agent_runtime/dashboard.py`

### 5.4 Unified `/status` Command

**Goal:** One command to see everything.

**Addition to `commands.py`:**
```python
# /status            — Compact status summary (goals, plan, kanban, health)
```

**Output:**
```
Runtime Status
==============
Session: ctx-abc-123 | User: default | Tenant: default
Uptime: 2h 14m | Provider: anthropic/claude-sonnet-4

Standing Goals (1 active)
  [ACTIVE] Build secure microservice (75%)

Plan Tree
  [Mission] Build secure microservice
    [Phase] Scaffold ✓
    [Phase] Code (active)
      [Slice] API routes (running)
        [Task] Generate /users endpoint (completed)
        [Task] Generate /auth endpoint (running)

Kanban (default board)
  backlog: 3 | ready: 2 | in_progress: 1 | review: 0 | done: 12

Health: OK
```

**Deliverable:** Updated `commands.py`

---

## Cross-Cutting Concerns

### C.1 Configuration Management

All new subsystems must respect `args/agent_runtime.yaml`:
- [ ] `AgentRuntimeConfig` dataclass in `tools/agent_runtime/config.py`
- [ ] Per-subsystem enable/disable toggles
- [ ] Environment variable overrides (`ICDEV_SAG_STANDING_GOALS=0`)

### C.2 Backward Compatibility

- [ ] All new DB migrations are optional — degrade gracefully on missing tables.
- [ ] Existing `AgentRuntime()` constructor signature unchanged.
- [ ] Existing chat workflows unaffected when new subsystems disabled.

### C.3 Security & Compliance

- [ ] RLS on all new tables (`tenant_id` + `user_id`)
- [ ] Classification tagging for plan trees (`CUI` default)
- [ ] Audit logging for goal/plan/kanban mutations
- [ ] Approval gate for destructive plan operations (replan, rollback, delete)

### C.4 Performance

- [ ] Plan tree queries use indexed lookups (no full table scans).
- [ ] Kanban claim uses `SELECT ... FOR UPDATE` or SQLite equivalent.
- [ ] Dashboard aggregates are cached (5s TTL) to avoid hammering DB.

### C.5 Observability

- [ ] Structured logging via `icdev_logger` for all subsystems.
- [ ] Prometheus metrics (optional): `sag_goals_active`, `sag_plan_nodes_total`, `sag_kanban_tasks_completed`.
- [ ] OpenTelemetry spans for gateway message routing.

### C.6 Documentation

- [ ] Update `AGENTS.md` with new subsystems and extension model.
- [ ] Update `CLAUDE.md` with new `/goal`, `/plan`, `/kanban` commands.
- [ ] Write `docs/features/phase-8x-agent-runtime.md` (user-facing).
- [ ] Write `docs/developer/agent-runtime-extension.md` (dev-facing).

---

## Success Metrics

| Metric | Baseline (Current) | Target (After Build) | Measurement |
|--------|-------------------|----------------------|-------------|
| Standing goals supported | 0 | `/goal` with full lifecycle | Feature test |
| Hierarchical plan depth | 1 (flat) | 4 (Mission→Phase→Slice→Task) | Unit test |
| Local replan on failure | None | <2s subtree replan | Benchmark |
| Multi-agent coordination | A2A messages | Kanban board + atomic claim | Integration test |
| Gateway platforms | 1 (Telegram) | 4 (Telegram, Discord, Slack, Email) | Health check |
| Session search | Internal API only | `/search` + `/history` CLI | Feature test |
| Runtime dashboard | None | Interactive TUI + JSON export | Feature test |
| Workflow completion (>30min) | ~40% | ~75% | Academy mission runs |
| Error recovery time | ~8 min | ~2 min | Benchmark |

---

## Rollout Strategy

### Internal Alpha (Week 13)
- Deploy behind feature flag (`agent_runtime.alpha=1`)
- Core team dogfoods standing goals + plan tree on daily tasks
- Gather feedback on `/goal` UX

### Forge Academy Beta (Week 14)
- Enable for Academy missions >10 steps
- Measure completion rate vs. flat pipeline
- A/B test hierarchical planning on child app generation

### General Availability (Week 15+)
- Remove feature flags
- Update onboarding docs
- Announce in release notes

---

## Risk Register

| Risk | Likelihood | Impact | Mitigation |
|------|-----------|--------|------------|
| Plan tree DB queries too slow | Medium | High | Indexed schema, pagination, caching |
| LLM decomposition hallucinates plans | High | Medium | Structured output + validation + fallback |
| Kanban claim races in multi-agent | Medium | High | Atomic claim with timeout, auto-reclaim |
| Gateway adapters increase attack surface | Low | High | Input sanitization, rate limiting, approval gates |
| Feature bloat in runtime.py | Medium | Medium | Extension model — each subsystem isolated |
| Migration conflicts with existing DB | Low | High | Backward-compatible migrations, rollback scripts |

---

*Plan Version: 1.0*
*Author: Hermes Agent*
*Date: 2026-08-08*
