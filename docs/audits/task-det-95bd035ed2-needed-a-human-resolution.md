<!-- CUI // SP-CTI -->
# task-det-95bd035ed2 — `needed_a_human` for dwr-anchor-04: the escalation was RIGHT, the five attempts were never made, and a human answered 17 hours later

- **Task:** task-det-95bd035ed2 (filed by `detector_findings_reflex`, detector
  `recovery` / rem-hyg-16, finding `95bd035ed24b91e1`)
- **Subject:** dwr-anchor-04 — icdev#2175, 5 `pr_watcher.resume`, escalated at the cap
- **Date measured:** 2026-09-09 02:2x UTC, against the live PG board, `origin/main`
  and the `icdev-ai/icdev` forge

## Verdict

**Nothing is left to land on the subject.** #2175 merged at 2026-09-08T23:11:50Z
(squash `731e9bc12`, merged by `app/github-actions`), `kanban_tasks.dwr-anchor-04`
reads `done` (23:11:58.739412Z), and `restore_acts --apply reap_dead_lease
--target dwr-anchor-04 --dry-run` answers *"no live lease — nothing to reap"*.

| | |
|---|---|
| derivation (`summarize_recovery` over `_recovery_rows()`, subject `dwr-anchor-04`) at 02:21Z | still reports — `attempts=4`, `escalated=true`, `merged=true`, `outcome=needed_a_human` |
| newest COUNTED attempt row (`resume` 5/5) | `2026-09-08 03:01:52.646873` |
| **clear-by** (attempt + `window_hours=24`) | **`2026-09-09 03:01:52Z`** |
| `detector_findings.95bd035ed24b91e1` | `status=active`, `seen_count=3`, `card_count=1`, `last_seen_at=2026-09-08 20:45:26` |
| `resume_delivery --task dwr-anchor-04` | `undelivered — 5 pr_watcher message(s) still unread in the queue` |

The card dispatched at **02:03:58Z, 58 minutes BEFORE its own clear-by** — tighter
even than the twelfth instance's 12m19s margin, and on the wrong side of it. So
unlike instances 4/5/6/8/11 the derivation is *not* already empty at dispatch; it
drops when the fifth resume ages out at 03:01:52Z, and the `detector_findings` row
clears at the first `detector_findings_reflex` cycle after that. Landing this as an
ordinary PR is nonetheless safe on #2057's ground (`fb989f6ad`, confirmed an
ancestor of `origin/main`): a terminal card inside the window is HELD
(`held_closed_early`), not re-filed as `-r2`.

Neither `rebase_failed` (3 rows, 20:33–20:36Z) nor `union_refused` extends the
clear-by — both `claims._recovery_rows` and `detector_findings.recovery_rows`
fetch only `pr_watcher.{rebase,resume,escalate,merge}`.

## The escalation was CORRECT — a real test failure, in the branch's own new test

This is **not** one of the moot instances. `Test Shard 2 of 4` failed on the
original head `2d5b1a88c` at 02:08:02Z, ten minutes before the first resume, and
the job log's last lines name a real defect in the branch's own new file:

```
FAILED tests/docmod/test_redline_passage_anchor.py::TestResolvePassage::
  test_section_fallback_offsets_verify_and_are_exact
  - sqlite3.IntegrityError: CHECK constraint failed:
    currency_verdict IN ('current','deprecated','eol','retired','divergent','unknown')
1 failed, 3251 passed, 1 skipped, 1 warning in 330.56s (0:05:30)
```

Not an artifact-upload timeout (ninth instance), not a stale branch, not a
host-dependent path comparison. A fixture wrote a `currency_verdict` the schema
forbids. `escalate` outranking the later `merge` is the right verdict here, and
the repair is the very first of the human's five fix commits.

## Why nobody acted for 17 hours: the five attempts were never made

`resume_delivery --task dwr-anchor-04` reads **5 pending / 0 receipted**, and the
escalation says so itself (kpr-watch-13's clause is live):

> `resume cap reached (5/5) — manual intervention required; NONE of the 5
> injection(s) were ever read (5 still unread in the queue)`

The cap escalation fired **52 s** after the fifth resume — again inside the
`RESUME_COOLDOWN_SECONDS = 600` that constant exists to rule out.

And even had one been read, the context names the CHECK and never the TEST:

```
Current state: ci_failed
Failing checks: Test Shard 2 of 4, Test
```

One `grep FAILED` over the job log is the whole answer; the resume carries neither
the file, the test id, nor the constraint. Cross-reference:
`pr-watcher-resume-names-the-check-never-the-test`.

## A GENUINE sibling conflict train on the `dwr-*` epic — and the file is Python

The three `rebase_failed` rows are not merely the documented post-repair artifact
(the branch head *was* a merge commit by then). A sibling landed on the exact file
pair:

| UTC | Event | Δ |
|---|---|---|
| 20:07:50 | `770216b78` — human merges `origin/main` into the branch (first pass) | |
| **20:32:34** | **dwr-ev-03 (#2179, `238d58375`) lands on main — touching `tools/doc_modernization/redline_drafter.py` AND its `icdev/` mirror** | |
| 20:33:45.9 | `union_refused` + `rebase_failed` on exactly that pair | **+71.9 s** |
| **20:33:53** | dwr-fid-02 (#2186, `1fc9da025`) lands on main | |
| 20:34:57.1 | `union_refused` + `rebase_failed` | **+64 s** |
| 20:36:23.9 | `union_refused` + `rebase_failed` | +150 s |
| 20:54:41 | `cf3032f5d` — human merges `origin/main` (second pass) — resolves it | |

`git log origin/main -- .../redline_drafter.py` since 2026-09-08 returns exactly
two commits: #2179 and this PR's own squash. The 33 s–279 s signature holds
(71.9 s / 64 s / 150 s), and the collision set is the same-epic shape already
measured on `rmf-ui-*` and on `dwr-*` in the twelfth instance.

### The `union_refused` here is CORRECT and must stay refused

```
refused: files=['icdev/tools/doc_modernization/redline_drafter.py',
                'tools/doc_modernization/redline_drafter.py']
rules=[] verifiers=[] -- undeclared: icdev/tools/doc_modernization/redline_drafter.py
matches no union_resolver.files entry
```

Every prior instance's `union_refused` was about a line-oriented append surface
(`CLAUDE.md` at 55.6 % of all refusals, `docs/features/*.md`, `start.md`). **This
one is an ordinary Python module**, where two conflicting hunks unioned produce
code nobody wrote — the hazard CLAUDE.md names explicitly ("never extend it to
YAML/JSON/Python"). `union_resolver.files` already declares `tools/*/blueprint.py`
and `tools/dashboard/app.py`, so the boundary is not "no Python": it is
*append-only registration surfaces with verifiers*, and `redline_drafter.py` is
not one.

**So kpr-watch-14 (declare `CLAUDE.md`) must not be widened to source modules on
the strength of this row.** A refusal here is the resolver working. The repair for
this shape is the one the human performed — merge `origin/main` in and resolve by
hand — not a declaration.

## What the human actually did

Two `origin/main` merges and five real fixes, all after the 03:02:44Z escalation:

| UTC | Commit | |
|---|---|---|
| 20:07:50 | `770216b78` | `Merge remote-tracking branch 'origin/main'` |
| 20:34:46 | `3bd6e7bc3` | the anchor fixture wrote a `currency_verdict` the schema forbids — **the escalation's own cause** |
| 20:54:41 | `cf3032f5d` | `Merge origin/main` (second pass) — clears the #2179 conflict |
| 21:21:18 | `06cfeadf9` | make anchoring and redraft work together |
| 21:37:20 | `a37734afe` | seed the scan run the anchor fixture's findings point at |
| 22:02:06 | `fd7c9deb0` | the chunk-link fixture omitted a NOT NULL `doc_id` |
| 22:50:34 | `901a811d9` | mirror every NOT NULL of `dic_chunk_links`, not one at a time |
| 23:11:50 | `731e9bc12` | merged by `app/github-actions` |

Positive control again, after rmf-ui-08, fni-api-01 and rmf-ui-13. Per the
standing rule: **check the branch's own history for a merge commit before
concluding nobody intervened** — `gh pr view --json commits` shows both merges
even though the PR squash-merged to a single parent.

## Ledger

363 `pr_watcher` rows for this task: 350 `wait`, 5 `resume`, 3 `union_refused`,
3 `rebase_failed`, 1 `escalate`, 1 `merge` (`"PR already merged"`). Every resume
classified `ci_failed`; every conflict row `merge_conflict`. Note the two distinct
causes in one ledger — a real CI failure drove the escalation, a sibling train
drove the conflict rows 17 hours later, and neither is the other.

## Nothing changed outside this file

No detector, threshold or window was touched — an actuator never edits what it
verifies. `tools/ci/pr_watcher.py` and `args/pr_watcher_config.yaml` are
`protected_paths`; the two observations above (kpr-watch-13 is already live;
kpr-watch-14 must not be widened to source modules) are recorded here rather than
folded in.
