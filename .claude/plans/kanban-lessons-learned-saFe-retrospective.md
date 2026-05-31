# Kanban Lessons Learned — Automated SaFe Inspect & Adapt Pipeline

## Context

ICDEV's kanban workflow auto-decomposes, auto-remediates, auto-triages, and auto-fixes tasks. Thousands of tasks have moved through `done`, `suggested`, `backlog`, and `token_exhausted`. Yet there is **no closure ritual** that captures what succeeded, what failed, and why. The experiential knowledge of each task evaporates into `last_failure_reason` (overwritten per failure) and `failure_count` (a scalar with no history).

Existing systems capture *some* failure knowledge:
- `self_debug.py` writes an `insight` to `memory_entries` only after 3 identical signatures
- `failure_triage.py` writes autofix audits to `.tmp/kanban/autofix-audit/` JSON files
- `auto_remediate.py` fixes idempotent issues but logs nothing to memory
- `goal_learner.py` learns at the **domain/goal** level, not the individual task level

There is **no SaFe-style Inspect & Adapt (I&A)** reflex that runs when a kanban task completes or fails. There is no bridge from `kanban_tasks` outcomes → `memory_entries` → suggested remediation cards. There is no recurrence tracking.

## Problem

1. **Knowledge loss:** A task that auto-decomposed, timed out, got auto-fixed, or succeeded on retry carries actionable intelligence about the pipeline itself. That intelligence is lost.
2. **Repeated failures:** The same systemic issues (e.g., "session limit regex gap") recur because no automated mechanism promotes the fix insight to a suggested kanban card.
3. **No process metrics:** ICDEV cannot answer "What % of tasks succeed on first dispatch?" or "Which task categories have the highest retry rate?" without ad-hoc SQL.
4. **Missing SaFe I&A:** The framework's continuous improvement principle is present in docs but absent in automation.

## Goal

Create an automated **Lessons Learned Engine** (`tools/workflow/lesson_learned.py`) that:
1. Runs a lightweight closure analysis on every kanban task that reaches `done` or is quarantined to `suggested`
2. Classifies the outcome into a pattern taxonomy (success_first_try, success_after_decomposition, success_after_retry, timeout_quarantine, token_exhaustion, phantom_completion, permission_blocked, verification_fail, auto_remediated, autofixed, self_debug_quarantined)
3. Writes a structured lesson to `memory_entries` (type=`lesson_learned`) with SHA-256 dedup
4. Detects **recurrence** — if a similar pattern has been seen before on tasks matching the same prefix/type, escalate
5. If the lesson indicates a **systemic fix** (config change, tool change, prompt change, process change), creates a `SUGGESTED` kanban card via the existing `oracle_predictions` → `suggested_card_writer` path
6. Provides a dashboard endpoint (`/api/kanban/lessons`) and a periodic reflex (`tools/genesis/reflexes/inspect_adapt.py`) for batch retrospectives

## Architecture Decisions

| ID | Decision | Rationale |
|----|----------|-----------|
| D331 | Reuse `memory_entries` table (type=`lesson_learned`) | No new DB schema required; existing `memory_write.py` provides SHA-256 dedup, search, and classification tags |
| D332 | Heuristic classification first, LLM analysis only for novel/escalated patterns | 90%+ of tasks can be classified deterministically from `status`, `failure_count`, `last_failure_reason`, `kanban_status_transitions`, and `self_debug` signatures. LLM is reserved for recurrence analysis and root-cause synthesis |
| D333 | Hook into existing kanban.py lifecycle, not a separate daemon | `lesson_learned.analyze_task()` is called synchronously from `_check_completed()`, `_record_failure_and_maybe_flag()`, `_decompose_batch_tasks()`, and `self_debug.py`. This ensures every task is analyzed exactly once |
| D334 | Use `oracle_predictions` as the bridge to suggested cards | `suggested_card_writer.py` already polls `oracle_predictions` with `prediction_type='gap::*'`. Adding `prediction_type='lesson_learned'` lets the existing promotion pipeline create cards without new UI code |
| D335 | Batch retrospective reflex runs weekly, not per-task | Per-task analysis is immediate; the `inspect_adapt` reflex aggregates the week's lessons into a summary, trend report, and PI-level recommendations. Matches SaFe Program Increment cadence |
| D336 | Lessons linked to task_id via `memory_entries.source_id` | Reverse-lookup: `SELECT * FROM memory_entries WHERE type='lesson_learned' AND source_id = 'task-xxx'` gives the full history |
| D337 | No modification to `kanban_tasks` schema | All lesson data lives in `memory_entries` and `oracle_predictions`. Keeps the change bounded and avoids migration risk |

## Implementation

### Step 1: Core Engine

**Create:** `tools/workflow/lesson_learned.py` (~250 LOC)

Exports:
- `analyze_task(task_id: str, outcome: str) -> Lesson` — collects task metadata, classifies pattern, computes recurrence score
- `write_lesson(lesson: Lesson) -> str` — writes to `memory_entries` via `memory_write.py`
- `maybe_create_remediation_card(lesson: Lesson) -> Optional[str]` — creates `oracle_predictions` row if lesson indicates systemic fix
- `get_recurrence(pattern: LessonPattern) -> RecurrenceReport` — queries `memory_entries` for similar past lessons

Internal helpers:
- `_classify_outcome(task_id, transitions, failure_count, last_reason, self_debug_sig) -> LessonPattern`
- `_compute_recurrence_score(pattern, prefix, task_type) -> float` — 0.0-1.0 based on match count / total count
- `_is_systemic(pattern, recurrence_score) -> bool` — true if recurrence_score > 0.3 or pattern is novel tool/config gap

### Step 2: Lifecycle Hooks

**Edit:** `tools/genesis/reflexes/kanban.py`

Four injection points:
1. After `_check_completed()` — when `verified=True` and task moves to `done`:
   ```python
   if verified:
       try:
           from tools.workflow.lesson_learned import analyze_task, write_lesson
           lesson = analyze_task(task_id, outcome="success")
           write_lesson(lesson)
       except Exception:
           pass  # best-effort
   ```

2. After `_record_failure_and_maybe_flag()` — when task moves to `needs_decomposition` or `suggested`:
   ```python
   new_status = _record_failure_and_maybe_flag(task_id, reason)
   # ... existing _move_task ...
   try:
       from tools.workflow.lesson_learned import analyze_task, write_lesson
       lesson = analyze_task(task_id, outcome="failure")
       write_lesson(lesson)
   except Exception:
       pass
   ```

3. After auto-decomposition in `_decompose_batch_tasks()` and `_decompose_phase_exit_gates()`:
   ```python
   # After parent marked 'decomposed' and children created
   try:
       lesson = analyze_task(parent_id, outcome="auto_decomposed")
       write_lesson(lesson)
   except Exception:
       pass
   ```

4. After stale-cleanup path (line ~6313) — when a task is cleaned up due to stale subprocess:
   ```python
   # After _record_failure_and_maybe_flag and _move_task
   try:
       lesson = analyze_task(tid, outcome="stale_cleanup")
       write_lesson(lesson)
   except Exception:
       pass
   ```

**Edit:** `tools/workflow/self_debug.py`

After `check_and_diagnose()` quarantines a task to `suggested`:
```python
try:
    from tools.workflow.lesson_learned import analyze_task, write_lesson
    lesson = analyze_task(task_id, outcome="self_debug_quarantined")
    write_lesson(lesson)
except Exception:
    pass
```

### Step 3: Remediation Card Creation

**Create:** `tools/workflow/lesson_learned_remediation.py` (~80 LOC)

Thin wrapper around `tools.awareness.suggested_card_writer` internals. Called by `maybe_create_remediation_card()`.

Inserts an `oracle_predictions` row with:
- `prediction_type = 'lesson_learned'`
- `prediction_text` = markdown summary of lesson + recommended fix
- `confidence` = recurrence_score * 100
- `severity` = 'medium' if recurrence_score < 0.5 else 'high'
- `lens_name` = `lesson.pattern.category` (e.g. 'timeout_quarantine', 'phantom_completion')

`suggested_card_writer.py` already polls `oracle_predictions` with `outcome='pending'`. The new `prediction_type` will create a suggested card with title `[LESSON-LEARNED] <category>: <brief>` and description containing the full lesson + recommendation.

### Step 4: Batch Retrospective Reflex

**Create:** `tools/genesis/reflexes/inspect_adapt.py` (~150 LOC)

Weekly reflex (configurable cadence) that:
1. Queries `memory_entries` for `lesson_learned` rows from the past 7 days
2. Aggregates by pattern category → count, avg recurrence_score, top task prefixes
3. Identifies "trending" categories (count >= 2 and increasing vs prior week)
4. Generates a markdown retrospective report saved to `memory/logs/YYYY-MM-DD-retrospective.md`
5. If a trending category has no open remediation task, creates one via `maybe_create_remediation_card()`
6. Returns summary dict for genesis audit

**Edit:** `tools/genesis/daemon.py`

Add `"inspect_adapt"` to `REFLEX_NAMES` (if not present) and configure in `args/genesis_config.yaml`:
```yaml
reflexes:
  inspect_adapt:
    enabled: true
    schedule: "0 9 * * 1"  # Mondays at 9am
    risk_tier: green
```

### Step 5: Dashboard Endpoint

**Edit:** `tools/dashboard/api/kanban.py`

Add route:
```python
@kanban_api.route("/lessons", methods=["GET"])
def list_lessons():
    """Return recent lessons learned for the kanban board."""
```

Queries `memory_entries` for `type='lesson_learned'` with optional `?days=7&category=timeout` filters. Returns JSON suitable for a new "Inspect & Adapt" panel on the kanban dashboard.

### Step 6: Metrics & Analytics

**Create:** `tools/kanban/metrics.py` (~100 LOC) — or extend existing `tools/kanban/source_stats.py`

Functions:
- `dispatch_success_rate(days=7) -> float` — % of tasks that reached `done` on first dispatch
- `retry_rate_by_prefix(prefix: str) -> dict` — avg failure_count per task in prefix family
- `top_lesson_categories(days=30) -> list` — most frequent lesson patterns
- `recurring_patterns(days=30) -> list` — patterns with recurrence_score > 0.3

### Step 7: Configuration

**Create:** `args/lesson_learned_config.yaml`

```yaml
enabled: true
# Heuristic classification thresholds
recurrence_threshold: 0.30  # min score to flag as recurring
systemic_threshold: 0.50   # min score to auto-create remediation card
# LLM analysis (optional, for novel patterns)
llm_analysis:
  enabled: true
  model: claude-sonnet-4-6
  max_tokens: 2048
# Categories that always create a remediation card (whitelist)
always_remediate:
  - token_exhaustion
  - phantom_completion
  - permission_blocked
# Categories that never create cards (blacklist)
never_remediate:
  - success_first_try
```

### Step 8: Documentation & Memory

**Create:** `docs/features/lesson-learned-engine.md`

SaFe alignment, taxonomy reference, API docs, and operator runbook.

**Create memory entry:** `memory/lesson-learned-engine.md` — one-line pointer in `MEMORY.md`.

## Success Criteria

1. Every task reaching `done` or `suggested` has exactly 1 `lesson_learned` row in `memory_entries` within 24 hours
2. A task that auto-decomposes produces a lesson linking parent → children
3. Recurring patterns (same category on 2+ tasks in 7 days) are detectable via `/api/kanban/lessons`
4. A systemic lesson creates a suggested kanban card within 1 scheduler cycle
5. The weekly `inspect_adapt` reflex runs without error and produces a markdown retrospective
6. `dispatch_success_rate()` returns a meaningful float for the last 7 days

## Files Modified / Created

| Action | File | Lines |
|--------|------|-------|
| Create | `tools/workflow/lesson_learned.py` | ~250 |
| Create | `tools/workflow/lesson_learned_remediation.py` | ~80 |
| Create | `tools/genesis/reflexes/inspect_adapt.py` | ~150 |
| Create | `args/lesson_learned_config.yaml` | ~30 |
| Create | `docs/features/lesson-learned-engine.md` | ~100 |
| Create | `tools/kanban/metrics.py` | ~100 |
| Edit | `tools/genesis/reflexes/kanban.py` | +40 (4 hook blocks) |
| Edit | `tools/workflow/self_debug.py` | +8 (1 hook block) |
| Edit | `tools/genesis/daemon.py` | +1 (add to REFLEX_NAMES) |
| Edit | `tools/dashboard/api/kanban.py` | +40 (new route) |
| Edit | `tools/awareness/suggested_card_writer.py` | +10 (handle lesson_learned type) |
| Edit | `args/genesis_config.yaml` | +5 (inspect_adapt config) |
| Edit | `memory/MEMORY.md` | +1 (pointer) |

## Rollback Plan

1. Set `args/lesson_learned_config.yaml::enabled: false` — all hooks become no-ops
2. Delete `oracle_predictions` rows with `prediction_type='lesson_learned'` to remove suggested cards
3. Existing `memory_entries` rows remain for historical reference; they are non-destructive

## Out of Scope

- Manual retrospective UI (all automation; human reviews suggested cards)
- Integration with ANVIL `workflow_reconciliations.lessons_learned` (separate system)
- Integration with `ai_incident_log` (only for AI-specific incidents)
- New DB tables (intentionally avoided per D331/D337)
