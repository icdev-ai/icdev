# Lesson Learned Engine

**Classification:** CUI // SP-CTI  
**Feature ID:** feat-lesson-learned  
**Status:** SHIPPED (main, 2026-05-30)  
**SaFe Alignment:** Inspect & Adapt ceremony → automated post-task closure ritual

---

## Purpose

Every kanban task that reaches `done`, `token_exhausted`, `suggested` (quarantined), or is auto-decomposed / auto-fixed should teach the pipeline something. The Lesson Learned Engine captures these insights, classifies them into a deterministic taxonomy, detects recurrence, writes structured lessons to memory, and spawns suggested remediation cards when a pattern is systemic.

---

## Architecture

```
┌─────────────────┐     ┌────────────────────┐     ┌─────────────────┐
│ kanban.py hooks │────▶│ lesson_learned.py  │────▶│ memory_entries  │
│ (7 hooks)       │     │ analyze_task()     │     │ type=lesson_learned
└─────────────────┘     │ write_lesson()     │     └─────────────────┘
                        │ maybe_create_      │              │
                        │   remediation_card()│              ▼
                        └────────────────────┘     ┌─────────────────┐
                                                   │ oracle_predictions│
                                                   │ prediction_type=  │
                                                   │   lesson_learned  │
                                                   └─────────────────┘
                                                            │
                                                            ▼
                        ┌────────────────────┐     ┌─────────────────┐
                        │ inspect_adapt.py   │◄────│ suggested_card_ │
                        │ (weekly reflex)    │     │   writer.py       │
                        └────────────────────┘     └─────────────────┘
```

---

## Components

| File | Role |
|------|------|
| `tools/workflow/lesson_learned.py` | Core engine: classify, recurrence, recommendation, write to memory |
| `tools/workflow/lesson_learned_remediation.py` | Bridge: creates `oracle_predictions` rows for systemic lessons |
| `tools/workflow/inspect_adapt.py` | Weekly reflex: aggregates lessons, writes retrospective markdown |
| `tools/kanban/metrics.py` | Metrics: success rate, retry rate, top lesson categories, recurrence |
| `args/lesson_learned_config.yaml` | Feature toggle + thresholds |
| `tools/genesis/reflexes/kanban.py` | **7 hooks** dispatch `analyze_task` at lifecycle points |
| `tools/workflow/self_debug.py` | **1 hook** after quarantine |
| `tools/dashboard/api/kanban.py` | `/api/kanban/lessons` endpoint |
| `tools/awareness/suggested_card_writer.py` | Promotes `lesson_learned` predictions to kanban cards |

---

## Lifecycle Hooks

1. **Verified success path** — after `_clear_timeout_count(task_id)`
2. **Unverified failure path** — after `_move_task(task_id, new_status)` (failure)
3. **Stale cleanup failure** — after stale-cleanup `conn.commit()`
4. **Batch auto-decompose** — after `conn.commit()` in batch block
5. **Phase-exit gate auto-decompose** — after `conn.commit()` in phase-exit block
6. **Auto-decompose (stalled)** — after `_auto_decompose_stalled_tasks()` print
7. **Pre-dispatch complexity gate** — after successful `_decompose_one_task(task)`
8. **Self-debug quarantine** — after `_quarantine_task()` in `self_debug.py`

---

## Pattern Taxonomy (`LessonPattern`)

| Pattern | Trigger |
|---------|---------|
| `success_first_try` | `failure_count=0`, ≤2 transitions |
| `success_after_retry` | `failure_count>0`, eventual success |
| `success_after_decomposition` | >2 transitions, no failures |
| `token_exhaustion` | "token", "rate limit", "session limit" in reason |
| `timeout_quarantine` | "timeout" + `failure_count>=3` |
| `phantom_completion` | "phantom", "no commits", "unverified" |
| `permission_blocked` | "permission" in reason |
| `self_debug_quarantined` | Quarantined by self_debug |
| `auto_decomposed` | Pre-dispatch complexity decompose |
| `stale_cleanup` | Stale subprocess cleanup |
| `autofixed` | Auto-remediation applied |
| `auto_remediated` | Pre-backlog auto-remediation |
| `verification_fail` | Generic failure fallback |

---

## Config (`args/lesson_learned_config.yaml`)

```yaml
enabled: true
recurrence_threshold: 0.30
systemic_threshold: 0.50
always_remediate:
  - token_exhaustion
  - phantom_completion
  - permission_blocked
never_remediate:
  - success_first_try
```

---

## API Endpoint

```
GET /api/kanban/lessons?pattern=token_exhaustion&systemic=1&days=30&limit=200
```

Returns lessons from `memory_entries` (type=`lesson_learned`) parsed from JSON content.

---

## Inspect & Adapt Reflex

`inspect_adapt.py` runs weekly as a Genesis reflex (registered in `daemon.py`). It:
1. Aggregates lessons from the last 7 days
2. Detects trending patterns (recurrence_score ≥ threshold)
3. Writes a markdown retrospective to `memory/logs/YYYY-MM-DD-retrospective.md`
4. Creates synthetic remediation predictions for trending categories

---

## Dashboard

No dedicated UI page yet; the `/api/kanban/lessons` endpoint feeds the existing Kanban board and can be queried by the IQE system.

---

## Rollback

- Set `enabled: false` in `args/lesson_learned_config.yaml`
- Hooks in `kanban.py` degrade gracefully (wrapped in `try/except`)
- No schema changes — purely additive

---

## References

- Plan: `.claude/plans/kanban-lessons-learned-saFe-retrospective.md`
- ADRs: D331–D337
