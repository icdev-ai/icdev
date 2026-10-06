---
ontology_id: icdev:mission:m-swe-01-multi-agent-dag:step:1
step_class: icdev:Lesson
---

# Multi-Agent DAG — Directed Acyclic Graph Orchestration

Real multi-agent systems don't run agents in a flat list — they run them in a dependency graph. Task B can only start after Task A completes. Task C depends on both A and B. This is a **DAG** (Directed Acyclic Graph).

## What you'll build

```
DAG definition:
  task_a → task_b → task_d
  task_a → task_c → task_d

DAGRunner executes:
  1. Find tasks with no unmet dependencies → run them in parallel
  2. When a task completes → check if any new tasks are now unblocked
  3. Repeat until all tasks complete or timeout
```

## The architecture

```python
# The whole graph is one dict: task name -> {"fn": callable(context) -> dict, "deps": [...]}
COMPLIANCE_PIPELINE = {
    "stig_scan":         {"fn": task_stig_scan,         "deps": []},
    "risk_score":        {"fn": task_risk_score,        "deps": ["stig_scan"]},
    "poam_draft":        {"fn": task_poam_draft,        "deps": ["stig_scan"]},  # parallel with risk_score
    "executive_summary": {"fn": task_executive_summary, "deps": ["risk_score", "poam_draft"]},
}

runner = DAGRunner(COMPLIANCE_PIPELINE)
runner._build_order()   # -> [["stig_scan"], ["risk_score", "poam_draft"], ["executive_summary"]]
results = runner.run()  # -> {"stig_scan": {...}, "risk_score": {...}, "poam_draft": {...}, "executive_summary": {...}}
```

## Key concepts

**Topological ordering** — Tasks must run in an order that respects all dependencies. Use a BFS/Kahn's algorithm approach:
1. Find all tasks with `in_degree == 0` (no dependencies) → ready queue
2. Run all ready tasks, collecting results
3. For each completed task, reduce `in_degree` of its dependents by 1
4. Any dependent that reaches `in_degree == 0` joins the ready queue
5. Repeat until done

**Result propagation** — Each task function receives the results of its dependencies as a `context` dict, keyed by task name.

## The compliance pipeline

Your DAG runs a 4-stage compliance pipeline:
1. `stig_scan` — simulate scanning 3 systems, return findings
2. `risk_score` — calculate risk score from scan results (depends on stig_scan)
3. `poam_draft` — draft POA&M entries (depends on stig_scan)
4. `executive_summary` — combine risk score + POA&M into a summary (depends on both)

## Success criteria

- DAGRunner correctly resolves execution order (stig_scan before risk_score)
- Tasks with no shared dependencies run in the same batch
- `executive_summary` receives context from both `risk_score` and `poam_draft`
- The final result includes all 4 task outputs
