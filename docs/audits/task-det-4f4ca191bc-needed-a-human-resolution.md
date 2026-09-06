<!-- CUI // SP-CTI -->
# task-det-4f4ca191bc — `needed_a_human` finding for qa-fail-6a87916931be3793, resolved

- **Task:** task-det-4f4ca191bc (filed by `detector_findings_reflex`, detector
  `recovery` / rem-hyg-16, finding `4f4ca191bc7c8b30`, seen 2x)
- **Subject:** qa-fail-6a87916931be3793 — PR #2137, five `resume` cycles, escalated
- **Date measured:** 2026-09-06, against the live PG board and the forge (REST)

## Verdict

**The escalation was CORRECT, the PR was genuinely stuck when the card dispatched,
and the repair is a three-file commit — not a merge button and not a re-run.**
This is the fourth instance of this card class where real work was outstanding
(after mfx-sib-03, mfx-mrg-01 and task-det-9a62ee81a7). Re-derived first, as the
card demands: the derivation still reported the subject (`attempts: 5`, `kind:
resume`, `escalated: true`, `merged: false`, `at: 03:37:42Z`), the task read
`pr_opened` with `executor_url` #2137, the lease was already free
(`restore_acts.py --apply reap_dead_lease --dry-run` → "no live lease"), and the
forge reported the PR **open, non-draft, `mergeable: true`, `mergeable_state:
blocked`** at head `0acc4f7b9`.

The card's quoted `reason` (`injected resume context`) is the last ATTEMPT's, not
the escalation's. The escalate row's own reason is a true resume cap: `resume cap
reached (5/5) — manual intervention required`, classification `ci_failed`.

## The ledger

`pr_watcher` rows naming the subject, lifetime: **288 `wait`, 5 `resume`,
1 `escalate`, 8 `rebase_failed`, 2 `union_refused`** — and no `rebase`, no
`merge`. Two distinct phases, and the escalation belongs to the FIRST:

| # | at (UTC) | action | classification | reason |
|---|---|---|---|---|
| 1 | 02:36:56 | `resume` | ci_failed | cycle 1 — `Failing checks: Test Shard 3 of 4, Test Shard 4 of 4, Test` |
| 2 | 02:47:17 | `resume` | ci_failed | cycle 2 |
| 3 | 02:57:23 | `resume` | ci_failed | cycle 3 |
| 4 | 03:27:16 | `resume` | ci_failed | cycle 4 |
| 5 | 03:37:42 | `resume` | ci_failed | cycle 5 |
| 6 | 03:38:36 | `escalate` | ci_failed | **resume cap reached (5/5) — manual intervention required** |
| 7 | 09:41:25 | `rebase_failed` | merge_conflict | `Could not apply 4b1978dfa`, base `c3c4b26e1` |
| 8 | 09:42:36 | `rebase_failed` | merge_conflict | base `c3c4b26e1` |
| 9 | 11:03:36 | `rebase_failed` | merge_conflict | base `aadeed4fa` |
| 10 | 11:04:39 | `rebase_failed` | merge_conflict | base `aadeed4fa` |
| 11 | 12:01:50 | `rebase_failed` | merge_conflict | base `eaa8d41dc` |
| 12 | 12:03:01 | `rebase_failed` | merge_conflict | base `eaa8d41dc` |
| 13 | 12:31:34 | `union_refused` | merge_conflict | `icdev/tools/security/row_security.py matches no union_resolver.files entry` |
| 14 | 12:31:34 | `rebase_failed` | merge_conflict | base `47101cb53` |
| 15 | 12:33:56 | `union_refused` | merge_conflict | same refusal |
| 16 | 12:33:57 | `rebase_failed` | merge_conflict | base `47101cb53` |

The fifth resume fired at 03:37:42 and the cap escalation 54 seconds later —
inside the 600-second cooldown, the `final_attempt_grace_seconds` shape
kpr-watch-13 measured at p50 41s.

## The five resumes produced nothing, and could not have

```
python -m tools.ci.resume_delivery --task qa-fail-6a87916931be3793 --json
  verdict: undelivered — 5 pr_watcher message(s) still unread in the queue
  receipted: 0
```

All five injections sit unread in `.tmp/kanban/messages/qa-fail-6a87916931be3793.jsonl`
on the checkout `pr_watcher` runs in. The branch head did not move between the
first resume (02:36) and the escalation (03:38): `4b1978dfa` throughout. That is
the kpr-watch-13 finding restated on one more subject — "five attempts" that were
never made.

## Phase 1: why CI was red at escalation (02:25–03:38Z)

The original commit `4b1978dfa` (02:22Z, 13 files) fixed the card's defect AND a
defect it found on the way: inside a request, `row_security.inject_row_predicate`
rewrote the health probe `SELECT 1` into `SELECT 1 WHERE (classification IS NULL
OR …)`, which raises `UndefinedColumn`, so `/api/health` answered `degraded` on
every request. The branch added `_is_tableless` to `row_security.py` and taught
`/api/health` to publish a MEASURED `database` / `database_measured` pair, which
its new `globalSetup.ts` isolation assert reads to learn which database the
server is actually on.

Shards 3 and 4 were red on that head. The transition log names the mechanism from
the board's side: the worker's run ended `VALIDATION FAILED: BUDGET EXHAUSTED
after CodeLens+Coherence (300s)` and the scheduler demoted the task `in_progress
→ backlog` at 02:32:12 while the PR opened at 02:25:27 — so no session was
attached to the branch when the watcher started resuming it. (The exact red tests
of that head are not re-derived here; the head was superseded twice before this
card dispatched, and the failures on the CURRENT head are measured below.)

## Phase 2: the conflict train, and a sibling that fixed the same defect

Every `rebase_failed` row fires shortly after a distinct landing on
`origin/main`, exactly two per landing (one per rebase attempt in that base era):

| main landing (first-parent) | at (UTC) | rebase_failed at | Δ |
|---|---|---|---|
| `c3c4b26e1` #2138 qa-fail-de9fc92555a3c906 | 09:40:31 | 09:41:25, 09:42:36 | 54s, 125s |
| `aadeed4fa` #2064 mfx-mrg-01 | 11:02:52 | 11:03:36, 11:04:39 | 44s, 107s |
| `eaa8d41dc` #2070 mfx-sib-03 | 12:00:42 | 12:01:50, 12:03:01 | 68s, 139s |
| `47101cb53` #2123 task-det-9a62ee81a7 | 12:31:00 | 12:31:34, 12:33:56 | 34s, 176s |

The same signature as the rmf-ui-* train (12 of 12 within 33–279s), but the
collision is NOT a sibling append. **Two QA-sweep cards fixed the same defect.**
`c3c4b26e1` (qa-fail-de9fc92555a3c906, #2138, "RLS must not rewrite a table-less
SELECT — /api/health reported db:false on every request") landed
`_is_tableless_select` in `row_security.py` and a LOGGING health probe seven
hours after this branch had committed `_is_tableless` and a MEASURING one. Four
files conflicted — `row_security.py` and `dashboard/app.py`, both trees — and
once main carried the sibling, every later landing re-triggered a rebase attempt
that hit the identical conflict. The union rung correctly refused
(`row_security.py` matches no `union_resolver.files` entry, and a duplicate
function is not a union anyway).

## The 12:36Z hand-merge resolved the right file the wrong way

A CLI session merged `origin/main` into the branch at 12:36:21Z (`0acc4f7b9`,
two parents, "take main's landed RLS fix, and regenerate the packaged bootstrap
CLAUDE.md"). Taking main's `row_security.py` was right: the sibling's spelling
landed first and both do the same thing. Taking main's `app.py` was wrong, and
the merge commit's own message records the choice ("Both conflicts resolved to
main"): main's `/api/health` answers `{status, db}` and nothing else. That one
resolution explains **every red check on the new head**, measured from the job
logs (never from the rollup names):

| check on `0acc4f7b9` | log says |
|---|---|
| `Test Shard 4 of 4` FAILURE | `1 failed, 4421 passed` — `test_health_route_reports_the_measured_database_not_the_env: /api/health must MEASURE the database` |
| `Test Gates` FAILURE | red-first gate: `tests/test_e2e_database_isolation.py` "does not pass against THIS tree either — merge-base 14 failed, this tree 1 failed" — the same test |
| `E2E Shard 1-4 of 4` FAILURE (2 min each) | `globalSetup.ts:1034` **E2E DATABASE ISOLATION FAILED — refusing to run. requested: icdev (via ICDEV_PG_DATABASE) / measured: <not measured> / verdict: unmeasured / detail: /api/health did not report a measured database** |
| `Test Shard 2 of 4` FAILURE | `1 failed, 4366 passed` — `tests/core/test_domain_declaration.py::test_it_declaration_exists_and_reproduces_todays_constants: assert ('icdev', 'icdev_e2e') == ('icdev',)` |

The E2E refusal is the card's own design working: an unconfirmed isolation is
refused, never waved through. It just refused because the merge had removed the
field it measures with.

The shard-2 failure is independent of the merge: the branch declares `icdev_e2e`
in `icdev_domain.yaml` (so a local `npx playwright test` no longer has to stand
the identity guard down), and an existing test pins the declaration to exactly
`("icdev",)`. Locally a second test in that file also fails —
`test_builtin_default_matches_the_checked_in_file` requires the checked-in `db`
block to EQUAL `icdev-core`'s `BUILTIN_DEFAULT`, which lives in the installed
distribution (0.2.0) and cannot be edited from this repo.

## The repair (`5ab798a99`, pushed 17:26Z)

Three files, on the subject branch, in its own worktree:

1. `tools/dashboard/app.py` + `icdev/tools/dashboard/app.py` — `/api/health`
   carries BOTH halves: main's logging handler (a probe whose failure is
   invisible cannot tell an outage from its own bug) AND the measured
   `backend` / `database` / `database_measured` fields from
   `storage.active_database()`. `database: null` with `database_measured: false`
   still means NOT MEASURED, never confirmed-clean.
2. `tests/core/test_domain_declaration.py` — the pin gains `icdev_e2e`; the
   builtin comparison checks every `db` field except the database list, and
   requires instead that the file never loses or renames the builtin's
   canonical name and keeps it first. A scaffolded project has no Playwright
   suite and no fixture writes to isolate, so that list is the one field the
   checked-in file may extend.

Verified before the push, in the worktree:

```
pytest tests/test_e2e_database_isolation.py tests/core/test_domain_declaration.py tests/test_rls_integration.py
  78 passed
python tools/ci/red_first_gate.py --files tests/core/test_domain_declaration.py tests/test_e2e_database_isolation.py --gate
  2 discriminating — merge-base 1 failed / 14 failed; this tree 27 / 22 passed
python tools/dx/mirror_parity.py --files tools/dashboard/app.py --gate      clean: true
ruff check <3 files>                                                        All checks passed
python tools/workflow/coherence_checker.py --tier fast --gate --changed-files ...   pass
/api/health via the Flask test client (SQLite):
  {'backend': 'sqlite', 'database': '...\\data\\icdev.db', 'database_measured': True, 'db': True, 'status': 'ok'}
```

The pre-commit hook ran the skip census, domain-leak gate, mirror parity,
undeclared-import census, nav-path derivation, blueprint imports and the route
smoke, all OK. CI on `5ab798a99` was queued at 17:26Z; its outcome is recorded
in the section below.

## What the automation could not have done

- **The resume loop could not fix a conflict that did not yet exist.** Phase 1
  was a red-CI escalation at 03:38Z; the conflict arrived at 09:40Z. Five
  undelivered injections aside, a resume asked to fix "Test Shard 3 of 4, Test
  Shard 4 of 4, Test" names the CHECK, never the failing test.
- **The union resolver was RIGHT to refuse.** Two spellings of the same function
  in `row_security.py` is a choice, not a union; a rule that kept both would
  have shipped a duplicate definition.
- **The hand-merge got the choice half right.** Nothing on the branch or on main
  says "the E2E assert reads `database_measured`" in a place a conflict
  resolver sees — `globalSetup.ts` did not conflict, so its dependency on the
  route's shape was invisible while resolving `app.py`. The branch's own test
  said it, and CI said it 90 seconds later; the resolution was pushed before
  either was asked.
- **The `earliest_clear_at` hold (#2057) makes an ordinary PR safe for this
  record**: the newest counted attempt row is 03:37:42Z, so the finding cannot
  clear before 09-07 03:37:42Z plus the next 6-hour reflex cycle, and a terminal
  card before that is held, not re-filed.

## What is NOT changed here, on purpose

- `summarize_recovery`, its window and its threshold — the verdict was correct.
- `tools/ci/pr_watcher.py` — a `protected_path`; the resume-delivery gap is
  kpr-watch-13's, and the "CI red → LLM resume with no failing-test name" path is
  the same card's follow-on.
- The `union_resolver` file table — `row_security.py` should NOT be added; a
  duplicate-fix collision is a human choice.
- The subject's second duplicate-fix root cause: two QA-sweep cards
  (`qa-fail-de9fc92555a3c906`, `qa-fail-6a87916931be3793`) fixing one defect is
  a sweep-dedupe question for the QA seeder, not this card.

## Done when

- [x] Derivation re-run and the subject confirmed still reported at dispatch.
- [x] Real cause found: a hand-merge dropped the measured health fields; an
      existing pin refused the new declaration.
- [x] Landed by hand on the subject branch (`5ab798a99`), through the ordinary
      PR door — the watcher merges #2137 once required checks are green.
- [x] No lease to release (holder pid dead, TTL spent).
- [ ] The derivation stops reporting the subject once the 03:37:42Z row ages
      out (≥ 2026-09-07 03:37:42Z), and `detector_findings` row
      `4f4ca191bc7c8b30` reads `cleared` at the first `detector_findings_reflex`
      cycle after that. Nothing here can, or should, make that happen sooner.
