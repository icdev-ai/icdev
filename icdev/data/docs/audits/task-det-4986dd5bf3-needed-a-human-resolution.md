<!-- CUI // SP-CTI -->
# task-det-4986dd5bf3 — `needed_a_human` finding for rmf-ui-10, resolved

- **Task:** task-det-4986dd5bf3 (filed by `detector_findings_reflex`, detector
  `recovery` / rem-hyg-16, finding `4986dd5bf38b3dcb`)
- **Subject:** rmf-ui-10 — PR #2048, rebase-conflicted 6x, resumed 5x, escalated
- **Date measured:** 2026-09-05, against the live PG board (3,935 tasks)

## Verdict

Nothing is left to land. Both acceptance criteria were already true on the live
board **11h27m before this card dispatched**:

| Criterion | Measured 2026-09-05 |
|---|---|
| the derivation no longer reports `rmf-ui-10` | `[]` — the window held 88–89 rows across two readings 20 min apart (`resume` 29, `merge` 50, `escalate` 6, `rebase` 3); **none for this subject** |
| `detector_findings.4986dd5bf38b3dcb` reads `cleared` | `status=cleared`, `cleared_at=2026-09-05 06:03:51`; card dispatched `17:30:48` |


> The row count is a **rolling** 24h window over exactly four actions
> (`_recovery_rows` fetches `resume`/`rebase`/`escalate`/`merge` only) and moves
> between readings; the stable fact is that **none of them name `rmf-ui-10`**.

The deliverable is on the default branch — verified against `origin/main`, not
taken from the board:

| Evidence | On `origin/main` |
|---|---|
| merge commit | `3c7efe5af` (`subject` tier — BLOCKING evidence in `landed_check`) |
| the route | `@bp.route("/mosa")` → `bdc_mosa_page()` at `tools/boundary_canvas/blueprint.py:1769` **and** the `icdev/` mirror |
| the template | `tools/dashboard/templates/boundary_canvas/mosa.html` (moved, both trees) |
| the test | `tests/test_bdc_mosa_page.py`, 273 lines, gated via `args/ci_test_files/core.d/rmf-ui-10.txt` |

## The actual cause: an epic-wide shared-file conflict train

The escalation reason was `resume cap reached (5/5)`, and every underlying
failure was the same real git conflict:

```
rebase onto origin/main hit conflicts: Could not apply 75be06b40...
  # feat(compliance): migrate /mosa onto the Boundary canvas (rmf-ui-10)
```

This is **not** a union-only or phantom conflict — `git` itself refused to apply
the commit, so no resume refund was due and none was issued. The branch had no
defect in it either. The cause is that `main` moved underneath it, repeatedly,
because **sibling cards from the same `rmf-ui-*` epic were landing edits to the
identical file set**:

| Shared file | Touched by |
|---|---|
| `tools/dashboard/app.py` + `icdev/` mirror | #2044, #2045, #2046, #2048, #2054 |
| `tools/dashboard/templates/base.html` + mirror | #2044, #2045, #2046, #2048 |
| `tools/boundary_canvas/blueprint.py` + mirror | #2044, #2045, #2046, #2048 |
| `tools/dashboard/templates/compliance.html` + mirror | #2045, #2046, #2048 |
| `.claude/commands/start.md` (the `Pages:` line) | #2044, #2045, #2046, #2048 |
| `tests/e2e/nav_intelligence_compliance.spec.ts` | #2044, #2045, #2046, #2048 |
| `docs/features/rmf-ui-compliance-route-migration.md` | #2044, #2046, #2048 |

The watcher's own `sibling_conflict_warn` row at 00:59:21Z named **five** open
sibling PRs (#2047, #2050, #2051, #2052, #2054) sharing source files with #2048.

### Timeline — every rebase failure trails a sibling landing

All times UTC. Commit times converted from the recorded `-04:00` offset.

| Time | Event | Δ since sibling landed |
|---|---|---|
| 22:55:14 | `75be06b40` authored (the commit that would not rebase) | |
| 22:58–23:04 | `wait` × 8 — CI running | |
| **23:04:47** | **`96a8c4056` rmf-ui-07 (#2044) lands on main** | |
| 23:06:55 | `rebase_failed` → `resume` 1 | **+2m08s** |
| 23:09:18 | `rebase_failed` (immediate retry) | |
| 23:20:03 | `resume` 2 | |
| **23:39:20** | **`319809d81` rmf-ui-06 (#2045) lands on main** | |
| 23:41:53 | `rebase_failed` → `resume` 3 | **+2m33s** |
| 23:44:44 | `rebase_failed` (immediate retry) | |
| 23:51:58 | `resume` 4 | |
| 00:02:45 | `resume` 5 | |
| **00:03:45** | **`escalate` — "resume cap reached (5/5) — manual intervention required"** | |
| **00:27:35** | **`f5347f173` rmf-ui-09 (#2046) lands on main** | |
| 00:27:55 | `rebase_failed` | **+20s** |
| 00:28:17 | `d7830f50a` kpr-stale-06 (#2053) lands on main | |
| 00:29:45 | `rebase_failed` | **+1m28s** |
| 00:31–00:58 | `wait` × 23 — a rebase finally succeeded, CI running | |
| 00:59:21 | `sibling_conflict_warn` — 5 open siblings named | |
| **00:59:26** | **`merge` — "auto-merge ok"** → `3c7efe5af` | |
| 01:01:00 | `merge` — "PR already merged"; board → `done` | |

Four of the six `rebase_failed` events fire within **2m33s** of a sibling
landing on `main`; the other two are immediate retries of the preceding failure.
That is the mechanism, measured rather than asserted.

## Who fixed it: nobody

**No human ever intervened.** The escalation asked for one at 00:03:45Z and the
watcher merged the PR itself **56 minutes later** with reason `auto-merge ok`.
The conflict train simply paused long enough for one rebase to succeed.

The merge is correctly **not** counted as a recovery: it followed the
escalation, so `summarize_recovery` keeps the outcome `needed_a_human`. That is
the rem-hyg-16 rule working exactly as designed, not a defect — counting a
post-escalation merge as a recovery is the inflation that detector exists to
refuse.

Equally, the escalation was **correct by the watcher's own rules**: a real
conflict, five genuine attempts, a per-PR budget with refunds reserved for
phantom conflicts (`_resume_cycle`, `_refund_resume_budget`). What the watcher
could not know is that the cause was *transient and epic-wide* — the LLM resume
was structurally incapable of helping, because each resolution it could have
authored was invalidated by the next sibling landing seconds later.

> Note on the attempt count: this card's title says "escalated after 5
> attempt(s)" while the `detector_findings` row now reads "1 attempt(s)"
> (`evidence_json.attempts: 1`). Both are right. The finding is a **window** over
> `audit_trail`, re-derived each 6h cycle (`seen_count` 3); four of the five
> `resume` rows had aged out of the 24h window by the last cycle. Not a defect.

## Survey — is a retroactive `needed_a_human` card the norm?

The precedent record `task-det-c3cf418aed-needed-a-human-resolution.md`
(2026-08-23) proposed a candidate rule and noted it would need a survey. Here it
is, over **all 24 `recovery` findings ever recorded**:

| Measure | Count |
|---|---|
| findings lifetime | 24 |
| subjects now `done` | 19 |
| subjects still `pr_opened` | 5 |
| **subjects abandoned or stuck** | **0** |
| merge recorded *after* the escalation | 14 |
| …of which the **watcher merged itself** (reason begins `auto-merge ok`) | **10** |
| …merged elsewhere (reason `PR already merged`) | 4 |
| detector cards filed | 24 |
| `-r2` recurrence cards (the pre-#2057 window artifact) | 3 |

Eight of the 24 are the `rmf-ui-*` epic alone — rmf-ui-03, 07, 08, 10, 11, 12,
13, 16 — every one `done`, with post-escalation gaps of 56m to 663m. One
finding (`task-det-f3abb63607`) has another *recovery card* as its subject:
task-det-cd1d099fff's own resolution PR escalated and generated a new card.

> **Correction.** The first draft of this record said 13 and 9. Re-derived
> exactly, the numbers are **14** and **10**: one subject (`flx-airgap-01`) was
> missed in the hand count, and `rmf-ui-08`'s merge reason is
> `auto-merge ok; ignored non-required failing check(s): E2E ...` — still the
> watcher merging itself, but an exact-string match dropped it. The distinction
> now reported is `startswith("auto-merge ok")` (the watcher) versus
> `PR already merged` (some other door), because collapsing the two is the same
> conflation this record objects to elsewhere.

### The cost, measured

Of the 9 recovery cards sitting `scheduled` and **due now** (all stamped
`2026-09-05 17:30:48`, the same batch as this card):

| Bucket | Count | Cards |
|---|---|---|
| **moot** — finding `cleared` **and** subject `done` | **3** | task-det-8a4ca3352d (rmf-ui-08), task-det-6ca8c2dd3b (rmf-ui-11), task-det-5631a471c7 (rmf-ui-16) |
| live — finding `active` or subject in flight | 6 | flx-airgap-01, mfx-boot-01, mfx-mrg-01, mfx-sib-02, mfx-sib-03, task-det-cd1d099fff |

Each moot card dispatches a worker session against a subject that is already
delivered and a finding that is already `cleared`. Such a dispatch **cannot go
RED** — there is nothing left to change.

### What the survey does and does not support

The finding is **not** wrong, and `summarize_recovery` is **not** wrong. The
escalation happened; refusing to call the later merge a recovery is correct. The
narrow, evidenced observation is about *card seeding* in
`tools/kanban/detector_findings.py`, not about the detector's verdict:

> A `needed_a_human` finding whose subject carries a `pr_watcher.merge` row
> newer than its `pr_watcher.escalate` row, **and** whose subject task is
> terminal, is a true statement about the past with no remaining work. It should
> be filed as a **record** and not as a **dispatchable card**.

This record deliberately changes **no** detector, threshold or window — an
actuator never edits what it verifies. The observation is carried to its own
card, with this survey as its evidence.

## Re-derive

```
python - <<'EOF'
from tools.awareness.claims import _recovery_rows
from tools.dashboard.recovery_summary import summarize_recovery
print([e for e in summarize_recovery(_recovery_rows(), limit=10_000) if e['task_id'] == 'rmf-ui-10'])
EOF
python -m tools.kanban.detector_findings --list --status cleared
python -m tools.kanban.landed_check --task rmf-ui-10 --json
git show origin/main:tools/boundary_canvas/blueprint.py | grep -n 'bp.route("/mosa")'
```
