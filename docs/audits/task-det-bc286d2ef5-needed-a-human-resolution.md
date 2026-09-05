<!-- CUI // SP-CTI -->
# task-det-bc286d2ef5 — `needed_a_human` finding for mfx-sib-03, resolved

- **Task:** task-det-bc286d2ef5 (filed by `detector_findings_reflex`, detector
  `recovery` / rem-hyg-16, finding `bc286d2ef5868050`, fingerprint
  `needed_a_human`)
- **Subject:** mfx-sib-03 — PR #2070, 10 resumes, 15 failed rebases, escalated
- **Date measured:** 2026-09-05, against the live PG board and the forge

## Verdict

**This one was NOT moot.** The five previous records in this series
(`0b52c01989`, `4986dd5bf3`, `5631a471c7`, `cd1d099fff`, `2d74ec6cdc`) each found
the work already landed and nobody at fault. Here the conflict was **live and
still reproducing while the card was being worked**: the last `rebase_failed` row
fired at 18:05:06Z, seven minutes after the derivation was first run. It was
repaired by hand and the branch went `CONFLICTING` -> `MERGEABLE`.

| Criterion | Before | After |
|---|---|---|
| PR #2070 `mergeable` | `CONFLICTING` / `DIRTY` | `MERGEABLE` |
| derivation `outcome` for `mfx-sib-03` | `needed_a_human` | `unresolved`, then cleared |
| `detector_findings` `bc286d2ef5868050` | `active`, `seen_count=4` | `cleared` |

## The actual cause: a MIRROR BACKFILL COLLISION, not a plain sibling append

`args/pr_watcher_config.yaml` and its packaged twin
`icdev/data/args/pr_watcher_config.yaml` had **drifted apart on main by 135
lines** before either card existed — the mirror was missing commentary the
canonical copy had gained. Measured at `39986a0bf^`:

| | `args/…` | `icdev/data/args/…` |
|---|---|---|
| lines on main before either card | 306 | 171 |

Two cards then landed the same reconciliation independently:

| Commit | added to `args/…` | added to `icdev/data/args/…` |
|---|---|---|
| `40a9eb48d` mfx-sib-03 (the union rung) | +73 | +208 |
| `39986a0bf` mfx-mrg-02 (#2102, superseded-PR guard) | +86 | +221 |

Both wrote **138 identical lines** into the mirror (the backfill) plus their own
block (70 card-only lines). Re-derived, not inferred:

```bash
git show 40a9eb48d:icdev/data/args/pr_watcher_config.yaml > card.yaml
git show 39986a0bf:icdev/data/args/pr_watcher_config.yaml > main.yaml
git show 39986a0bf^:icdev/data/args/pr_watcher_config.yaml > base.yaml
comm -12 <(diff base.yaml card.yaml | grep '^>' | sort) \
         <(diff base.yaml main.yaml | grep '^>' | sort) | wc -l   # 138 shared
comm -23 <(diff base.yaml card.yaml | grep '^>' | sort) \
         <(diff base.yaml main.yaml | grep '^>' | sort) | wc -l   # 70 card-only
```

**That asymmetry is the whole defect.** `args/pr_watcher_config.yaml` merged
CLEAN — each card appended one block at a different anchor in a file whose
context both sides agreed on. The mirror did not: both sides rewrote the same
135-line region, so the two new blocks anchored into freshly-rewritten text and
git could not place them. It is a *sibling append* conflict only in the mirror,
and only because the mirror was stale to begin with.

Both conflict hunks had **one empty side**, so the union is not a judgement call:

| Hunk | our side | main's side | resolution |
|---|---|---|---|
| line 155 | empty | mfx-mrg-02's `superseded_*` block (88 lines) | take main's |
| line 396 | mfx-sib-03's `union_resolver:` block (75 lines) | empty | take ours |

## Why the automation could never have finished this — TWO independent reasons

### 1. The retries were per-BASE-ERA, and the base era advanced on unrelated commits

15 `rebase_failed` rows across **6 distinct base shas**, every one 61-278s
(median 127s) after a landing on main:

| main landing (UTC) | commit | `rebase_failed` at | delta |
|---|---|---|---|
| 12:05:36 | `39986a0bf` mfx-mrg-02 | 12:06:37 / 12:08:30 / 12:10:14 | +61s |
| 12:45:48 | `3a8ce8cd7` flx-oci-01 | 12:47:37 / 12:49:34 / 12:51:18 | +109s |
| 15:48:38 | `28c22c52e` INSERT ratchet | 15:50:16 / 15:52:15 / 15:54:03 | +98s |
| 17:50:07 | `9192f7c45` task-det-0b52c01989 | 17:52:22 / 17:54:47 / 17:56:54 | +135s |
| 17:58:10 | `df1dae7fb` rmf-ui-11 record | 18:00:09 | +119s |
| 18:00:39 | `0056318b3` task-det-4986dd5bf3 record | 18:02:46 / 18:05:06 | +127s |

**Only the FIRST of those six landings touched the conflicted file.** The other
five are floci, a CI ratchet and three audit records — none of them go near
`pr_watcher_config.yaml`. `max_rebase_attempts_per_task` is budgeted *per base
era*, so every unrelated commit on a busy board refunds the budget and the
watcher retries a conflict whose cause has not moved. This is not a conflict
train of six collisions; it is **one unresolvable collision retried six times**,
and in the audit log it is indistinguishable from the real train.

### 2. PR #2070 touches TWO `protected_paths` — the watcher is forbidden to merge it

```yaml
protected_paths:
  - tools/ci/pr_watcher.py        # PR #2070 changes it
  - args/pr_watcher_config.yaml   # PR #2070 changes it
```

`tools/kanban/gates.py`: *"A gate that cannot protect itself is not a control."*
So even a perfectly clean rebase ends at a refusal. **The entire ladder — 10
resumes, 15 rebases, one escalation — was spent on a PR the watcher was
structurally forbidden to merge from the moment it opened.** The escalation was
correct; it was simply ~10 resumes late, because the protected-path rung sits
*after* the rebase rung and nothing asks the cheap question first.

## The gap this leaves, named and NOT closed here

mfx-sib-03 *is* the union rung — the change that resolves exactly this hunk shape
automatically. It could not have resolved **its own** conflict: its declared
table (`union_resolver.files`) lists canvas blueprints, `app.py`, `base.html`,
Playwright specs, `start.md` and `docs/features/*.md`, and **no `args/*.yaml`
entry**. An undeclared conflicted file REFUSES, by design — rules are chosen by
file, never guessed from content, which is the property that keeps the rung safe.
So the rung would have declined and aborted, correctly.

Adding `args/*.yaml` to that table is a real candidate (the file is append-shaped
and both hunks here had an empty side), but it is **not done in this record**:
widening the declared table is a change to a `protected_path` config that governs
an automatic force-push, and it belongs in its own card with its own survey.
Recorded here as the evidence for that card, not applied.

## What was done

1. Worktree off `origin/kanban/mfx-sib-03`; `git merge origin/main` (never
   rebase — the push would be a force-push, which the hook refuses).
2. Resolved both hunks as the union, empty side yielding to the other.
3. Verified before pushing: `yaml.safe_load` on both copies and
   `args == icdev/data/args` **deep-equal**; `ast.parse` + `ruff` on all seven
   changed `.py` files (clean); `mirror_parity --gate` clean on all three
   mirrored modules; `git diff --check` clean; no conflict markers.
4. Confirmed BOTH features survived the merge — main's `superseded` guard (34
   references in `tools/ci/pr_watcher.py`) and the card's union rung
   (`union_resolved` / `union_refused` audit rows, `_union_resolve` in
   `rebase_recovery.py`).
5. Tests: `tests/kanban/test_union_resolver.py` 27 passed;
   `test_pr_watcher_superseded` + `test_pr_watcher_union_conflict` +
   `test_pr_watcher_rebase` + `test_pr_watcher` + `test_pr_superseded`
   -> **104 passed, 1 skipped**.
6. Pushed as a fast-forward (`40a9eb48d..1cb466935`). No force-push, no rebase,
   no branch deleted.
7. Landed through the governed door (`cli.py --set-status mfx-sib-03 done
   --merge`), because the protected-path rung means the watcher cannot.

No detector, threshold or window was touched. No budget, timeout or census
ceiling was raised.

## Closing this card is safe — measured, not assumed

`earliest_clear_at` for this finding is the newest counted attempt + 24h. The
card's own evidence row is `2026-09-04T17:40:10`, so the clear time was
**2026-09-05 17:40:10Z**, which had already passed when the card was worked
(18:0xZ). The `escalate` row aged out of the 24h window at 17:40:52Z and the
derivation downgraded to `unresolved` — i.e. **already not a finding**. Both the
`held_closed_early` guard (`fb989f6ad`, on main) and the plain clear rule agree,
so no `-r2` can be filed. Landed as an ordinary PR; no `hold` label, no
`scheduled_at` deferral.
