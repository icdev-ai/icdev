<!-- CUI // SP-CTI -->
# autonomy-act-05 — the recovery row set: shipped vs legacy, replayed over history

- **Card:** autonomy-act-05 — "The recovery panel and the recovery DETECTOR read
  different audit rows"
- **Measured:** 2026-09-09, against the live PG board (`icdev`, measured via
  `tools.db.storage.active_database()`)
- **Re-derive:** `python -m tools.kanban.recovery_action_survey`
  (`--json`, `--at <instant>`, `--window-hours`, `--step-hours`)
- **Report only.** No `--gate` (kpr-fix-03): it measures the BOARD, not a diff.
  Exit 0 = a report was produced whatever it says; exit 2 = it could not be,
  which is never the same as a clean survey.

## The defect

Three readers answer "did `pr_watcher` recover this PR" from `audit_trail`, and
they did not fetch the same rows.

| reader | what it fetched | what it does |
|---|---|---|
| `tools/dashboard/app.py` (Home panel) | `recovery_summary.AUDIT_ACTIONS` — **8** | a human reads it |
| `tools/kanban/detector_findings.py::recovery_rows` | hand-written literal — **4** | **files and clears cards** |
| `tools/awareness/claims.py::_recovery_rows` | hand-written literal — **4** | the standing claim over the panel |

`AUDIT_ACTIONS` was exported *for exactly this*, and its own comment said so
("so the SQL in app.py and the classifier here cannot drift apart again. They
did"). One of three readers took it. So rmf-disc-01's widening — which taught
the classifier that `rebase_failed` and `ci_retrigger` are ATTEMPTS — reached
the surface a human READS and never reached the two readers that ACT.

## Method

Both arms are replayed through the **SHIPPED** reduction —
`recovery_summary.summarize_recovery` collapses the rows and
`detector_findings.recovery_findings` turns entries into findings. **Only the
row filter differs.** Re-deriving the collapse in the survey would measure the
survey instead of the detector.

`detector_runs` is a per-detector ROLLUP (one row carrying a `runs` counter),
not a run log, so there is no recorded per-run instant to anchor on. The reflex
is `every 6h` with `window_hours: 24` (`args/genesis_config.yaml`), so the
replay walks **6h boundaries** across the recorded corpus and asks each arm the
detector's own question at each one. Stated rather than implied: these are
REPLAY instants at the reflex's cadence, not the runs the rollup counts.

`task_status` is **not** replayed and does not need to be. It separates
`recovered` from `unresolved` only — `escalated` is tested FIRST and wins — and
`recovery_findings` emits `needed_a_human` and nothing else. A historical board
status is unknowable anyway; asserting one would be a fabrication.

The union of both action sets is read in **ONE** query and each arm filters it
in Python. Two SQL reads minutes apart on a live board describe two different
boards, and the delta between them would be attributed to the rule under test.

## The corpus

56,910 rows, `2026-07-31T09:33:12Z` .. `2026-09-09T03:35:07Z`; **157 replay
instants**, 6h step, 24h window.

```
    pr_watcher.escalate                44835
    pr_watcher.merge                   10198
    pr_watcher.resume                    901
  * pr_watcher.rebase_failed             649
    pr_watcher.rebase                    191
  * pr_watcher.rebase_refund             103
  * pr_watcher.ci_retrigger               23
  * pr_watcher.resume_refund               6      (* = shipped set only)
```

## The delta

| measure | value |
|---|---|
| findings **ADDED** by the shipped set | **19 subjects**, over 8 instants |
| findings **REMOVED** by the shipped set | **0**, over 0 instants |
| card **TITLES** changed | **102 subjects**, over 77 instants — 90 understated, **12 OVERSTATED**, 49 change `kind` |
| **`earliest_clear_at`** changed | **57 subjects**, over 71 instants — **all LATER, 0 earlier** |
| clear-time delta | min 0.014h, median **1.44h**, max **19.71h** |

Per-subject rows report the **first** instant at which the two arms differed
(`first_seen_at`); the min/median/max are taken over **every** occurrence across
all 157 instants, so 19.71h is the true extremum and not one snapshot's.

### Widening is not purely additive — three effects, three directions

1. **ADDS ATTEMPTS.** `rebase_failed` / `ci_retrigger` raise `attempts` and move
   `at` (the newest counted attempt) FORWARD, pushing `earliest_clear_at` later.
2. **SUBTRACTS.** `resume_refund` / `rebase_refund` DECREMENT `attempts`. The
   legacy set fetched neither, so it counted attempts the watcher's own
   accounting had already withdrawn — **12 subjects were OVERSTATED**, e.g.
   `cpmp-dcfe06a404` 14 → 13, `cch-obs-02` 13 → 12, `kpr-dup-03` 13 → 12.
3. **ADDS SUBJECTS.** A task attempted ONLY by `rebase_failed` / `ci_retrigger`
   is invisible to the legacy set. `summarize_recovery` drops a task with zero
   attempts, so it draws no finding *at all*.

### The third consequence, measured at its starkest

At `2026-08-09T09:33:12Z`, `sbx-fld-01`:

```
shipped {'pr_watcher.escalate': 177, 'pr_watcher.rebase_failed': 2}
legacy  {'pr_watcher.escalate': 177}
```

**177 escalations and no card.** The legacy reader saw zero attempts, so
`summarize_recovery` dropped the task and the detector never filed anything —
while the panel showed it. `sbx-cov-02` is identical (179 / 2). A reader cannot
see an attempt kind it does not FETCH.

### The card's own instant, reproduced exactly

`python -m tools.kanban.recovery_action_survey --at 2026-09-09T02:35:00Z`:

| subject | legacy title | shipped title | legacy clear | shipped clear | Δ |
|---|---|---|---|---|---|
| `dwr-fid-03` | 5 `resume` | **13 `rebase_failed`** | `09-09T09:52:02Z` | **`09-09T20:36:41Z`** | **+10.744h** |
| `dwr-anchor-04` | 3 `resume` | 6 `rebase_failed` | `09-09T03:01:52Z` | `09-09T20:36:23Z` | +17.575h |
| `dwr-ev-03` | 3 `resume` | 14 `rebase_failed` | `09-09T10:11:48Z` | `09-09T10:21:39Z` | +0.164h |
| `dwr-fid-02` | 2 `resume` | 9 `rebase_failed` | `09-09T10:08:13Z` | `09-09T10:20:12Z` | +0.200h |
| `dwr-collab-01` | 2 `rebase` | **1 `rebase`** | — | — | (refund) |

Entries at that instant: **shipped 10 tasks / 105 rows, legacy 9 / 68.** The
card recorded 10 / 103 and 9 / 66 forty minutes earlier; both readings are
quoted, because one figure off a live board is not a measurement. The card's
`dwr-fid-03` figures — 13 attempts, `20:36:41Z`, a 10h44m shortfall — reproduce
to the second.

## Verdict: ONE constant, and the shipped set is the one

The three options the card required be considered:

**Widen the two narrow readers to `AUDIT_ACTIONS` — TAKEN.** The survey supports
it on its own numbers:

- **0 findings removed.** Nothing that draws a card today stops drawing one, so
  widening cannot silence an existing finding. The whole risk of a widening is
  asymmetric here.
- **19 subjects were structurally invisible**, including a task escalated 177
  times. That is the hole the card names, and it is not marginal.
- **The refund correction makes the count MORE honest**, matching the watcher's
  own accounting on 12 subjects.
- **`earliest_clear_at` moves LATER, never earlier (0 of 57).** The #2057 hold
  exists to stop a spurious `-r2` for a card closed early; a longer hold can
  only reduce spurious filings, never cause one. The change is strictly in the
  safe direction.
- **Cost:** 19 extra subjects across 5.6 weeks ≈ 0.5/day, against
  `max_cards_per_run: 6` which already bounds and reports deferrals; and a
  median 1.44h longer life on a 24h window.

**Narrow the panel instead — REJECTED.** It would restore the 177-escalation
blind spot on the one surface that currently sees it, and it contradicts
rmf-disc-01's stated reason for the widening.

**Three distinct named constants — REJECTED for these three readers, ADOPTED for
a fourth.** The panel, the detector and the claim ask the *same* question, so
three names for it would be three chances to drift with extra ceremony. But
`detector_findings.watcher_outcome_rows` asks a genuinely different one — which
of two rows is NEWER — and an attempt row is neither an escalation nor a merge,
so it has no place in that ordering. It now reads a declared
`OUTCOME_ACTIONS` carrying that reason, rather than an inline SQL literal.

**No threshold and no window value was changed.** `window_hours: 24`,
`max_cards_per_run: 6`, `min_returns`, `RESUME_COOLDOWN_SECONDS`,
`max_resume_cycles_per_task` — all untouched. `summarize_recovery`'s rule that
`escalate` outranks a later `merge` is untouched. `recovery_summary.py`'s
classifier logic is untouched; only the exported names were made public.

## What changed

| file | change |
|---|---|
| `tools/dashboard/recovery_summary.py` | `_ATTEMPT_KINDS`/`_REFUND_KINDS` → public `ATTEMPT_KINDS`/`REFUND_KINDS` (private aliases kept); `AUDIT_ACTIONS` documented as THE one statement |
| `tools/kanban/detector_findings.py` | `recovery_rows` reads `AUDIT_ACTIONS`; new `OUTCOME_ACTIONS` for `watcher_outcome_rows` |
| `tools/awareness/claims.py` | `_recovery_rows` reads `AUDIT_ACTIONS`; `_derived_recoveries` reads the declared vocabulary, nets refunds, and still shares **no** reduction |
| `tools/kanban/recovery_action_survey.py` | new — this survey |
| `tests/kanban/test_recovery_action_vocabulary.py` | new — the fourth-reader rule |

### Why the claim's derived side may share the vocabulary

`claim_verifier`'s rule is that the two callables must not share the
**REDUCTION**. `_derived_recoveries` still re-implements the collapse itself —
a per-task counter plus set logic, no call into `summarize_recovery`, pinned by
an AST test. What it now shares is *which action is an attempt*, and that is a
fact about `pr_watcher`'s **writer**, not a reduction. A derivation over a
DIFFERENT population is not an independent check of the same claim; it is a
different claim, and it would report `disagrees` for a task the watcher only
ever `rebase_failed`. It also nets refunds now, for the same reason
`summarize_recovery` does: a task whose every attempt was withdrawn was never
attempted, and the two sides would otherwise disagree over an empty finding.

## The test, and why it reads the SOURCE

A behavioural test cannot hold this. Both spellings return audit rows and the
narrow one returns a **strict subset**, so every assertion over a fixture
carrying only `resume` rows passes for both. The drift is visible only in the
source, so the source is what is read.

Three discriminations in the scan, each measured rather than reasoned:

- **Substring, not equality.** The incident's actual shape was ONE string
  constant — `"WHERE action IN ('pr_watcher.rebase','pr_watcher.resume',"` —
  equal to no vocabulary member. The first version of this test used equality
  and **reported the incident clean**; the red-first replay against `origin/main`
  is what exposed it (the merge base failed on the import/derivation tests and
  *passed* `test_reader_holds_no_action_literal`). A rule that cannot see the
  incident it was written for is not a rule.
- **Prose is not a literal** (the `args/model_id_gate.yaml` precedent).
  `detector_findings` carries four reason strings like `"a pr_watcher.merge
  landed after the escalation"`; flagging them would refuse routine work on the
  first run.
- **A named module-level constant is not a literal either** — that is the card's
  second acceptable answer, and it is what `OUTCOME_ACTIONS` is.

**Surveyed before arming.** Over `tools/` and `icdev/tools/` (both trees), the
fourth-reader sweep fires on **zero** modules beyond the declarer
(`recovery_summary`), the writer (`pr_watcher`), the three readers and the three
declared exceptions — so it refuses no routine work today. The exceptions each
carry a written reason: `sibling_overlap.CONFLICT_ACTIONS` (the rows a real
merge conflict leaves), `protected_conflict_survey.LADDER_ACTIONS` (the rungs an
episode is made of), `recovery_action_survey.LEGACY_AUDIT_ACTIONS` (the
pre-autonomy-act-05 rule, copied verbatim so the survey can REPLAY it —
labelled history, never the shipped rule).

**Positive controls ship with it.** Three tests assert the scanner FIRES on the
incident's own inline SQL and on an inline collection, and stays silent on prose
and on a named constant. A test that only showed the block would also pass for a
scanner that had stopped scanning.

**Red-first.** Against `origin/main` (`c31218fcf683`) the suite fails **7 of 15**
with real assertions, on this tree 15 of 15 pass, and the seven name the defect
exactly:

```
test_reader_holds_no_action_literal[tools/kanban/detector_findings.py]
test_reader_holds_no_action_literal[tools/awareness/claims.py]
test_reader_imports_the_declaration[tools/kanban/detector_findings.py]
test_reader_imports_the_declaration[tools/awareness/claims.py]
test_derived_recovery_side_shares_the_vocabulary_not_the_reduction
test_audit_actions_is_derived_from_the_kind_tuples
test_declared_exception_uses_a_named_module_constant[recovery_action_survey.py]
```

The first two are the ones that matter, and they are exactly the two that
**passed** before the scan was widened from equality to substring. The RED was
recorded in two rounds, and the first round is the finding: an equality scan
proved only that a constant was new.

## Live confirmation, after the change

```
detector rows 117   claim rows 117   (identical)
actions: escalate, merge, rebase, rebase_failed, rebase_refund, resume
dwr-fid-03: 13 rebase_failed, earliest_clear_at 2026-09-09T20:36:41Z
```

Consequences 1 and 2 of the card are closed: the card title no longer understates
its subject, and the `#2057` hold is now as long as the panel's own evidence
supports.

## Not done, and named

- The **survey has no `--gate`**, deliberately. It measures the board.
- **`args/ci_test_backlog.txt` is untouched** — the new test file is gated in
  this PR via `args/ci_test_files/core.d/autonomy-act-05.txt`.
- The 19 added subjects are **historical**. They are not back-filled as findings:
  `detector_findings` projects from a 24h window and every one of them is long
  outside it. Nothing here rewrites a past run.
- `pr_watcher.wait`, `sibling_conflict_warn`, `protected_path_hold`,
  `behind_main_hold`, `union_refused`, `already_landed_warn` and `auto_ready`
  are **not** recovery evidence and were not added. `wait` alone is 62,925 rows
  and a PR the watcher merely LOOKED at is not one it attempted.
