# CUI // SP-CTI

# task-det-12d839d263 — migration drift cleared, and the applier that hid it

**Detector:** `migration_drift` (autonomy-dep-01) · **Finding:** `12d839d263a51a7e`
· **Resolved:** 2026-09-12

## What the card asked, and what was actually true

The card named one migration as merged-and-unapplied:

```
20260911220746_hook_events_session_start_type
```

Re-derived at the start of this attempt, that migration was **already applied** —
`applied_at 2026-09-12 12:35:18+00`. The finding's fingerprint is the SET
`{20260911220746}`, and its own clearing rule is "cleared only on a measurable
run that reports NONE of the members it names", so the reflex had already
marked it:

```
finding_id 12d839d263a51a7e   status cleared   cleared_at 2026-09-12T13:11:36
```

But the derivation still reported subject `deployment`, because a **different**
migration had merged in the meantime — a new member, therefore a new
finding_id, exactly as the card predicts:

```
20260912122759_add_experiment_candidate_lane
```

## The deployment act

`migrate.py` reads the FILESYSTEM, and this worktree was 7 commits behind
`origin/main`, so it did not contain the migration. `deployment_freshness.py`
confirmed the lag before anything was applied. `git merge origin/main` brought
the file in; the target database was then confirmed by identity, not by
assumption, because applying is irreversible:

```
current_database  icdev
server            172.18.0.2:5432
kanban_tasks      4073 rows          (the live board, not a throwaway)
lane column       absent
```

Applied with `python tools/db/migrate.py --up`:

```
[postgresql (ICDEV_DATABASE_URL)]
  [20260912122759] add_experiment_candidate_lane — OK 37ms
```

Verified after the fact:

```
lane column   'incubator'::text default, nullable
distribution  incubator: 9
```

Nine pre-existing candidates backfilled to `incubator`, which is what the
migration's own docstring says should happen: every one of them was decided
against an identity baseline, so no real measurement exists for any of them.

Both acceptance criteria:

```
python tools/db/migration_drift.py --json   ->  state: current, pending_count: 0
python tools/db/migration_drift.py --gate   ->  exit 0
detector_findings 12d839d263a51a7e          ->  status cleared
```

## The defect found on the way, and why it is the same defect one layer up

The card's instructions warn in PROSE that "a checkout behind the branch reports
`Pending: 0` and applies nothing". That warning exists because the tools
disagree, and nothing but prose was saying so. Measured here against the SAME
live PostgreSQL, in the same minute, from the behind checkout:

| command | reads | verdict |
|---|---|---|
| `python tools/db/migration_drift.py --json` | git `origin/main` | `pending`, naming `20260912122759` |
| `python tools/db/migrate.py --status` | this filesystem | `Applied: 446 | Pending: 0` |
| `python tools/db/migrate.py --up --dry-run` | this filesystem | `No pending migrations.` |

The tool an operator ACTS on is the one that reads as "nothing to do". That
alone is confusing. The consumer made it a **certification**:

- `production_audit.check_migration_status` (PRF-001) reads that `pending_count`
  and returned `status="pass"`, `message="All migrations applied"`.
- `production_remediate` then auto-fixes PRF-001 (confidence 0.75, tier
  `auto_fix`) by running `migrate.py --up` — which applies nothing and exits 0.

So the audit that exists to catch an unmigrated deployment certified one, and
the remediator reported success for having done nothing. That is autonomy-dep-01's
own defect — code merged, schema did not, everything green — reproduced one
layer up, inside the tooling prescribed to fix it. It is also why this card
class recurs: seven `migration_drift` findings were filed in the nine days to
2026-09-12.

### What changed

`migrate.py` now derives its own checkout drift and `--status` always carries it:

```
python tools/db/migrate.py --status        # warns when this checkout is behind
python tools/db/migrate.py --up            # the "No pending migrations." line
                                           # can no longer stand alone
```

Three states, and only one is silence — `unmeasurable` is never folded into
`current`, for the reason `migration_drift` already states: a check that could
not run is not a check that found nothing.

| state | meaning |
|---|---|
| `current` | the branch holds nothing this checkout lacks |
| `behind` | named versions are on the branch and absent here |
| `unmeasurable` | the ref could not be read (no remote, shallow clone) |

PRF-001 consumes it: `behind` and `unmeasurable` both return `warn`, never
`pass`. Wiring the consumer is deliberate — a new field nobody reads would be
this platform's signature bug (a declared capability that is never consumed)
rather than a fix for it.

**The detector was not touched.** It was right. The threshold, window and
fingerprint are unchanged, and the finding cleared because the schema changed,
not because anything was edited to stop reporting it.

### One distinction the comparison refuses to blur

The branch set is compared against **directory names**, not against
`discover_migrations()`. That method silently drops a directory carrying neither
`up.sql` nor `up.py` (17 exist), so comparing against the RUNNABLE set would
report a present-but-unrunnable migration as "your checkout is behind" — a
different defect with a different fix. The same `_VERSION_DIR_RE` the runner
parses names with is used here, so "what counts as a migration" cannot drift
between applying and auditing. Pinned by
`test_present_but_unrunnable_migration_is_not_reported_behind`.

## Evidence

- Red-first: `1 discriminating` — merge-base `11 failed, 3 passed`, this tree
  `14 passed`.
- `tests/db/test_migrate_checkout_drift.py`, gated via
  `args/ci_test_files/core.d/task-det-12d839d263.txt`.
- Live proof of both render paths against a real `git ls-tree origin/main` read,
  no stubbing: this checkout `current`; a directory holding one migration
  `behind`, naming 414.

## Not done here

- The 32 versions applied here but absent from `origin/main` are reported for
  context and are deliberately not a finding — they say nothing about whether
  this deployment is missing something.
- `migrate.py --status` surfaces 8 pre-existing `checksum_mismatch` issues and
  one `migration_files_missing` (343). Untouched: out of scope for this card,
  and each needs its own survey.
- Exit codes are unchanged. `production_audit` requires `rc == 0` to parse the
  JSON at all, and `production_remediate` reads a non-zero exit as a failed
  fix, so making `--status` exit non-zero on drift would have discarded the very
  data this change adds.
