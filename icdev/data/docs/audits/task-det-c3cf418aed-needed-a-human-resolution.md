<!-- CUI // SP-CTI -->
# task-det-c3cf418aed — `needed_a_human` finding for task-c49fb2727d, resolved

- **Task:** task-det-c3cf418aed (filed by `detector_findings_reflex`, detector
  `recovery` / rem-hyg-16, finding `c3cf418aed081545`)
- **Subject:** task-c49fb2727d — PR #1845, resumed 5x by pr_watcher, escalated
- **Date measured:** 2026-08-23, against the live PG board

## Verdict

Nothing is left to land. Both acceptance criteria were already true on the
live board when this card was dispatched:

| Criterion | Measured 2026-08-23 |
|---|---|
| the derivation no longer reports `task-c49fb2727d` | `[]` (15 `pr_watcher.*` rows in the 24h window, none for this subject) |
| `detector_findings.c3cf418aed081545` reads `cleared` | `status=cleared`, `cleared_at=2026-08-21 11:36:37` (the reflex's next measurable cycle) |

## The actual cause, and who fixed it

The card's own instruction — *find the actual cause, land it by hand, and
release the claim* — was carried out by a human **before the card existed**:

| When (UTC) | Event |
|---|---|
| 2026-08-20 09:20 → 10:01 | `pr_watcher.resume` x5 on PR #1845, `classification` moving `ci_failed` → `merge_conflict` |
| 2026-08-20 10:02 | `pr_watcher.escalate` — "resume cap reached (5/5) — manual intervention required" |
| 2026-08-20 22:19 | PR #1845 merged (`fix(integrity): authorize the DIC governed-evidence seam's dynamic import`); `pr_watcher.merge` recorded, task → `done` |
| 2026-08-21 05:36 | `detector_findings` row first seen; this card filed |
| 2026-08-21 11:36 | finding `cleared` by the reflex |

The real causes were the two the `recovery_summary` module docstring names: a
16-commit-stale branch and a host-dependent `as_posix()` path comparison in the
SIPA scanner's suppression predicate. #1845 landed the
`known_safe_dynamic_import_modules` entry in `args/integrity_config.yaml`
(mirrored to `icdev/data/args/`) and tests that drive
`_is_safe_dynamic_import` with the exact backslash form assessment 300
recorded — red-first proven (5 failed at the merge base, 22 pass after). An
LLM resume could never have reached either cause, which is the class this
detector exists to separate from `recovered`.

The merge at 22:19 is correctly **not** counted as a recovery: it followed the
escalation, so `summarize_recovery` keeps the outcome `needed_a_human`. That is
the rem-hyg-16 rule working, not a defect.

## Survey — is a retroactive `needed_a_human` card a one-off?

All six `recovery`/`needed_a_human` findings ever recorded, joined to the
subject's own escalate/merge audit rows:

| finding | subject | subject now | filed | merged | merged before filing? | detector card |
|---|---|---|---|---|---|---|
| c3cf418aed081545 | task-c49fb2727d | done | 08-21 05:36 | 08-20 22:19 | **yes** | in_progress (this card) |
| 295a9bb95e1bdb1a | rem-hyg-17 | done | 08-21 05:36 | 08-21 01:12 | **yes** | pr_opened |
| fd5008803225ce22 | autonomy-lrn-01 | done | 08-21 11:36 | 08-21 12:13 | no | in_progress |
| 5db26004f98661ef | xit-decl-03 | done | 08-21 11:36 | 08-21 15:39 | no | done |
| bbc0fa01eae45989 | qa-fail-5f7cf03a0b0a4351 | done | 08-22 02:19 | 08-23 02:10 | no | suggested |
| b1d12d0f70694dc2 | task-42a17b8956 | pr_opened | 08-23 03:50 | — | no | suggested |

- 5 of 6 findings are `cleared`; the one `active` finding is the only subject
  still open.
- 2 of 6 cards were filed for a subject a human had **already merged** — the
  detector's 24h window still held the escalation row, so the finding was
  true as a statement about the past and moot as a work item.
- 4 detector cards remain non-terminal (`in_progress` x2, `pr_opened`,
  `suggested`) for subjects that are already `done`. Each is a retroactive
  card; dispatching one to the runner cannot go RED because there is nothing
  left to change.

This is deliberately **not** a change to the detector, its threshold or its
window — an actuator never edits what it verifies. It is the survey a separate
card against the detector would need: the candidate rule is that a
`needed_a_human` finding whose subject carries a `pr_watcher.merge` row newer
than its `pr_watcher.escalate` row, and whose task is `done`, is filed as a
record and never as a dispatchable card.

## Re-derive

```
python - <<'EOF'
from tools.awareness.claims import _recovery_rows
from tools.dashboard.recovery_summary import summarize_recovery
print([e for e in summarize_recovery(_recovery_rows(), limit=10_000) if e['task_id'] == 'task-c49fb2727d'])
EOF
python -m tools.kanban.detector_findings --list --status cleared --detector recovery
```
