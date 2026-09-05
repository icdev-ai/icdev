<!-- CUI // SP-CTI -->
# task-det-5631a471c7 — `needed_a_human` finding for rmf-ui-16, resolved

- **Task:** task-det-5631a471c7 (filed by `detector_findings_reflex`, detector
  `recovery` / rem-hyg-16, finding `5631a471c7c28089`)
- **Subject:** rmf-ui-16 — PR #2052, resumed 5x by pr_watcher, escalated
- **Date measured:** 2026-09-05, against the live PG board

## Verdict

Nothing is left to land. Both acceptance criteria were already true on the live
board **11h27m before this card dispatched**:

| Criterion | Measured 2026-09-05 |
|---|---|
| the derivation no longer reports `rmf-ui-16` | `[]` (86 `pr_watcher.*` rows in the 24h window, none for this subject) |
| `detector_findings` row reads `cleared` | `status=cleared`, `cleared_at=2026-09-05 06:03:51`; card dispatched `17:30:48` |

`rmf-ui-16` is `done` (2026-09-04 11:28:35) and PR #2052 merged as
`e96bdc09a4045df62c62a4c02f91ec6965d5d1b8`.

## Who fixed it: NOBODY

The card's instruction — *find the actual cause, land it by hand* — had no human
to name. This is the second confirmed instance (after rmf-ui-10 / #2114) of the
survey's finding that **the escalation is typically PREMATURE rather than wrong**:

| When (UTC) | Event |
|---|---|
| 2026-09-03 23:33 → 23:39 | 7x `wait` — "CI still running" on PR #2052 |
| 2026-09-03 23:40:53 → 2026-09-04 00:24:19 | 5x `resume` ("injected resume context"), interleaved with `rebase_failed` |
| 2026-09-04 00:25:25 | `escalate` — "resume cap reached (5/5) — manual intervention required" |
| 2026-09-04 00:28:00 → 01:49:59 | 3x `sibling_conflict_warn`, 2x `wait` ("a lower-numbered sibling goes first"), 10 further `rebase_failed` |
| 2026-09-04 01:50:52 | `ci_retrigger` — closed and reopened to re-fire the workflows |
| 2026-09-04 09:56:27 | **PR #2052 merged by `app/github-actions`** — the `.github/workflows/pr-watcher.yml` job, not a person |
| 2026-09-04 11:28:35 | local watcher records `merge` ("PR already merged"); task → `done` |
| 2026-09-04 11:58:57 | `detector_findings` row first seen; **this card filed, 2h02m AFTER the merge** |
| 2026-09-05 06:03:51 | finding `cleared` by the reflex |
| 2026-09-05 17:30:48 | card dispatched |

The merge at 09:56 is correctly **not** counted as a recovery — it follows the
escalation, so `summarize_recovery` keeps the outcome `needed_a_human`. That is
the rem-hyg-16 rule working as designed, not a defect.

## The actual cause, measured: a shared-file conflict train

The cause class is the one the epic's earlier cards established, and this
instance measures it more sharply than any before it. **Every one of the twelve
`pr_watcher.rebase_failed` rows fires within minutes of a sibling landing on
`origin/main`** — six distinct landings, each drawing exactly two failures:

| `rebase_failed` (UTC) | Δ after landing | Landing on origin/main |
|---|---|---|
| 23:40:53 / 23:43:59 | +94s / +279s | `319809d81` #2045 rmf-ui-06 |
| 00:30:23 / 00:32:21 | +126s / +245s | `d7830f50a` #2053 kpr-stale-06 |
| 00:48:41 / 00:50:47 | +114s / +240s | `39c6b6a6b` #2055 mc-reflex |
| 01:03:17 / 01:05:34 | +111s / +248s | `a82491632` #2054 rmf-ui-17 |
| 01:21:43 / 01:23:47 | +33s / +158s | `193bf37a2` #2047 rmf-ui-03 |
| 01:48:11 / 01:49:59 | +89s / +196s | `86e5f65a9` #2050 rmf-ui-11 |

12 of 12 within 4m39s; 10 of 12 within 2m28s; median 142s. Every failure names
the same commit, `7a54940e3`. The prior board-wide survey recorded *four of six*
`rebase_failed` rows inside 2m33s of a sibling landing; this run is 12 of 12.

The watcher's own `sibling_conflict_warn` payload names the collision set — six
open sibling PRs (#2054, #2051, #2050, #2048, #2047, #2046) sharing:

```
tools/dashboard/app.py
icdev/tools/dashboard/app.py
tools/dashboard/templates/compliance.html
icdev/tools/dashboard/templates/compliance.html
docs/features/rmf-ui-compliance-route-migration.md
tests/e2e/nav_intelligence_compliance.spec.ts
tests/e2e/key_pages_smoke.spec.ts
tests/e2e_ui_full_coverage.py
```

These are REAL git conflicts, so no resume refund is due (`_resume_cycle`
refunds phantoms only) and an LLM resume cannot help: each resolution it could
author is invalidated by the next sibling landing ~2 minutes later. The five
resumes were spent against a moving base, and the PR merged unaided once the
train drained.

## Note on the downstream consequence, already addressed

The #2052 squash landed `tests/e2e/key_pages_smoke.spec.ts` unparseable and all
four E2E shards on `main` failed at collection — Playwright loads every spec
before running one. That is fixed independently: the required `Lint` job now
runs `npx playwright test --list` (mfx-ci-01), which parses all 65 specs with no
browser and no dashboard. Recorded here only so the two events are not
re-diagnosed as one.

## Not a change to the detector

No detector, threshold or window was touched — an actuator never edits what it
verifies. The candidate rule (a `needed_a_human` finding whose subject carries a
`pr_watcher.merge` row newer than its `escalate` row, and whose task is `done`,
should be filed as a record and never dispatched as a card) is already FILED as
**autonomy-act-04**, seeded unclaimed, with the board-wide survey as evidence.
This record is a fifth data point for it, and the second where the merge was the
watcher's own.

## Re-derive

```
python - <<'EOF'
from tools.awareness.claims import _recovery_rows
from tools.dashboard.recovery_summary import summarize_recovery
print([e for e in summarize_recovery(_recovery_rows(), limit=10_000) if e['task_id'] == 'rmf-ui-16'])
EOF
python -m tools.kanban.detector_findings --list --status cleared --detector recovery
```
