# Is this task id ALREADY on main? task -> main, not task -> PR (trust-disc-05)

> Moved verbatim from CLAUDE.md on 2026-09-12 (xrv-docs-02). The prose is the card's own record; only the leading `#` comment markers were stripped and the command runs fenced.

```bash
python -m tools.kanban.landed_check --task <task-id> --json
python -m tools.kanban.landed_check --all --json            # every non-terminal task
python -m tools.kanban.landed_check --task <task-id> --gate  # exit 1 if already on main
```

The board tracks task -> PR; nothing checked task -> main, so a task whose work had
already merged under a different PR number got dispatched again and opened a PR that
could only land as a REVERT (#1651: -38/+26 on rest_v1.py).
Evidence is tiered: merge_ref | subject BLOCK, body NEVER does (a body mention is a
citation as often as a landing). Boundary-matched, so ctx-perf-02 != ctx-perf-021 and a
parent id != its children. FAIL-OPEN — `checked: false` is never a clean answer.
Advisory by default; KANBAN_LANDED_CHECK=enforce refuses, =off disables. Survey first.
