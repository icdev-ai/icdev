<!-- CUI // SP-CTI -->
# autonomy-dep-05 — a `migration_drift` finding was cleared by a change of pending set, not by application

- **Card:** autonomy-dep-05 (`fix`), from the survey filed by task-det-a02733a6dc
  (`docs/audits/task-det-a02733a6dc-migration-drift-resolution.md`, on branch
  `kanban/task-det-a02733a6dc`).
- **Measured:** 2026-09-09 against the live PG board (`active_database()` →
  `{'backend': 'postgresql', 'database': 'icdev', 'measured': True}` — checked
  explicitly before any verdict, because a worktree with no `.env` reads a
  throwaway SQLite and every count would be a fabricated zero).
- **Population:** every `migration_drift` row in `detector_findings` since the
  detector was wired (2026-09-03 → 2026-09-09) — 6 findings, 6 clears, joined
  against `schema_migrations.applied_at`.

## The defect, in two rules that are each correct alone

`migration_drift_findings` fingerprints on the sorted pending **set**
(`fingerprint = "|".join(versions)`), so a finding's identity changes whenever
the set does. `_clear_missing` marks `cleared` every active finding whose
`finding_id` the current run did not report.

Composed over a set-valued fingerprint they produce a **false clear**: when the
pending set changes because another migration **merged** — not because anything
was **applied** — the `finding_id` changes, the old row is not in
`still_active`, and it is written `cleared` with its migrations still pending.

It matters because `cleared` is what a detector card's acceptance criterion
reads. `build_spec` emits *"detector_findings row `<id>` reads status=cleared"*,
so task-det-a02733a6dc's own criterion was satisfiable **71 minutes before the
migration it names was applied**. A card can be verified complete by a clear it
did not cause.

## The whole recorded population, re-derived

Each row: the finding's pending set, when it was written `cleared`, and when the
**newest** migration it names was actually applied. `delta > 0` means the clear
came first.

| finding | pending set | cleared at | newest apply | delta | verdict |
|---|---|---|---|---|---|
| `3a2f0f2aa1b33c3e` | 1 (`20260903100336`) | 2026-09-03 17:56:21 | 2026-09-03 17:55:41 | −0.011 h | honest |
| `12a243722f5eed9a` | 1 (`20260903194350`) | 2026-09-05 12:24:18 | 2026-09-05 15:23:12 | **+2.982 h** | **FALSE** |
| `6abf31a8e7ca6804` | 4 (`…194350` + 3 floci) | 2026-09-05 18:24:22 | 2026-09-05 15:23:13 | −3.019 h | honest |
| `7a5948ff916cff0b` | 2 (`20260908003311`, `…003920`) | 2026-09-08 14:21:33 | 2026-09-08 09:33:40 | −4.798 h | honest |
| `a02733a6dc9aa2e6` | 1 (`20260908071149`) | 2026-09-08 20:45:26 | 2026-09-08 21:56:12 | **+1.179 h** | **FALSE** |
| `03922726ad17f1c1` | 3 (`…071149`, `…071432`, `…071433`) | 2026-09-09 02:45:34 | 2026-09-08 21:56:14 | −4.822 h | honest |

**Two of six clears (33.3%) were false**, and neither needs timezone reasoning:
the clear and its successor are written in one run under one `now_iso`, so the
successor filed at that same instant reported as pending the very migration the
cleared finding names. Each `cleared_at` above is the next row's `first_seen_at`
exactly — one continuous drift re-fingerprinted six times, not six incidents.

## The rule that ships

A detector **declares** that its fingerprint is a set
(`SET_VALUED_FINGERPRINT_DETECTORS`, today exactly `migration_drift`). For a
declared detector, a finding the run no longer reports **by finding_id** is
cleared only when **none** of the members it names is still reported; otherwise
it is **held ACTIVE** and the surviving members are named on the run report
(`held_still_true`) and in the log.

The current member set is read from **this run's own findings**
(`reported_members`) — the same output `still_active` is built from — so the
survival question is answered from the detector's own report and never from a
second derivation of "what is pending".

Replayed through the shipped predicate, the table above flips **exactly** the
two false clears and leaves all four honest clears untouched. The control that
makes that non-trivial is `7a5948ff916cff0b`: its set changed *because* both its
migrations had been applied, and a successor **was** filed at the same instant —
a naive "never clear when a successor appears" rule would have wrongly held it.
Membership is what tells the two apart.

A partly-applied finding is **not** cleared: naming two migrations of which one
is now applied still names one that is not, and "some of what this says is still
true" is not a clear.

## Three decisions, and why

**No third status.** A held finding stays `active`. The card that asked for this
fix says in terms that an *unclearable* finding is worse than a false clear, and
every already-filed detector card's acceptance criterion reads `status=cleared`
verbatim — a `superseded` terminal state would make each of them unclosable.
Folding a predecessor into its successor (the card's one suggested option) has
the same consequence and was declined for the same reason; `cleared` still means
what it always meant and is still written on the first measurable run that stops
reporting the condition.

**`deployment_freshness` is named, not declared.** Its fingerprint
(`f"{reason}|{'|'.join(conflicts)}"`) is the same shape, and the card says in
terms: *measure it before asserting it*. Its first member is a **reason**, not a
conflicting file, so membership would have to be defined over the tail only —
a real design decision. The live population is one row, one distinct fingerprint
(`local changes would be lost|args/projects.yaml`), one clear: not a population
a rule can be justified against. Declaring it is a separate card with its own
survey. `status_churn`'s `<cycle>|contested` contains the separator and is *not*
a set — a cycle and a flag; `born_red` and `recovery` carry constant
fingerprints (1 distinct each over 8 and 37 rows) and structurally cannot
exhibit the defect. All four are asserted unchanged by test.

**No actuator.** Nothing here applies a migration, edits a threshold or rewrites
a criterion. autonomy-dep-02 settled whether applying should be automatic (no)
and is not reopened.

## Red-first proof

`tests/kanban/test_detector_set_valued_clearing.py` replays `LIVE_CLEARS` — the
six rows above, verbatim — through the shipped `consume()`.

```
python tools/ci/red_first_gate.py --files tests/kanban/test_detector_set_valued_clearing.py
Red-first proof vs origin/main (2a6fd162d0af): 1 changed test file(s) —
  1 discriminating, 0 not, 0 indecisive, 0 exempt, 0 not applicable.
  merge-base: failed (exit 1) 19 failed, 2 passed
  this tree:  passed (exit 0) 21 passed
```

The discriminating assertion is behavioural, not an `AttributeError` on a new
symbol — run alone against the merge-base module both parameters fail on the
finding's own status:

```
assert row["status"] == df.FINDING_ACTIVE, f"FALSE CLEAR reinstated: {note}"
E  AssertionError: FALSE CLEAR reinstated: cleared 2026-09-05 12:24:18; applied 15:23:12 -- 2h58m54s LATER
E  assert 'cleared' == 'active'
E  AssertionError: FALSE CLEAR reinstated: cleared 2026-09-08 20:45:26; applied 21:56:12 -- 1h10m46s LATER
E  assert 'cleared' == 'active'
```

## Not fixed here, and named rather than implied

- **The existing six rows are left exactly as they are.** They are the evidence,
  and two of them read `cleared` for a condition that was still true at the time.
  Rewriting a historical projection to match a rule that did not exist when it
  was written is the actuator-edits-its-own-verifier move this file argues
  against; the migrations they name have all since been applied, so the rows are
  true now for a reason that has nothing to do with why they were written.
- **One repair is still queued once per pending set.** The chain above produced
  six cards for one continuous drift, and the hold does not change that — a
  successor still files its own card, exactly as before. Suppressing the
  successor's card while a held predecessor's card is open is a *seeding* change
  with its own fire-rate question, and this card is about clearing.
- **A held finding accumulates while the drift persists.** Three merged
  migrations arriving one at a time leave three active rows for one condition
  until the apply clears all three at once. That is honest — each names a
  migration that is still pending — and it is visible in `--list --status active`
  and in `--stats`, which is where a reader should see it rather than in a count
  that quietly went to one.
