<!-- CUI // SP-CTI -->
# task-det-a02733a6dc — the migration was applied, and the finding had already been marked `cleared` 71 minutes before that happened

- **Task:** task-det-a02733a6dc (filed by `detector_findings_reflex`, detector
  `migration_drift` / autonomy-dep-01, finding `a02733a6dc9aa2e6`, `seen_count` 1)
- **Subject:** `deployment` — 1 migration on `origin/main`, not applied here
- **Date measured:** 2026-09-09 ~03:00Z against the live PG board (`icdev`,
  backend `postgresql`, `measured: true`), `origin/main` at `c31218fcf` after a
  fresh fetch

## Verdict — nothing left to apply

Both acceptance criteria are satisfied, each re-derived independently rather
than read off the projection:

| Criterion | Measured 2026-09-09 ~03:00Z |
|---|---|
| `python tools/db/migration_drift.py --json` reports `deployment` | **no** — `state: current`, `pending_count: 0`, `pending: []` |
| `origin/main` fresh (not understating pending) | fetched to `c31218fcf`; `on_branch_count` 413, `applied_count` 445 |
| `20260908071149` in `schema_migrations` | present, `applied_at` 2026-09-08 21:56:12.670242Z |
| the **table** exists in the live schema | `dic_sme_assertions`, 24 columns — a ledger row is a claim, the table is the fact |
| `detector_findings` row `a02733a6dc9aa2e6` | `status: cleared`, `cleared_at` 2026-09-08 20:45:26.810428 |
| latest `migration_drift` detector run | `clean`, 66 runs / 66 measurable, last run 2026-09-09 02:45:34 |

The database measured is the real deployment, not a worktree throwaway: this
worktree carries no `.env`, so the identity was checked explicitly
(`active_database()` → `{'backend': 'postgresql', 'database': 'icdev',
'measured': True}`) before any verdict was drawn. A fresh-worktree SQLite
reporting `Pending: 0` is the trap the card's own text names, and it is not what
happened here. The worktree also **contains** the migration directory
(`tools/db/migrations/20260908071149_dic_sme_assertions`, 466 entries), so the
filesystem side could not have understated it either.

## Who applied it, and when

`audit_trail`, actor `icdev-migrate`, all times UTC 2026-09-08:

| version | name | applied | ms |
|---|---|---|---|
| 20260908071149 | dic_sme_assertions | 21:56:12.838 | 322 |
| 20260908071432 | dic_suggestions_supersede_columns | 21:56:13.683 | 527 |
| 20260908071433 | dic_redraft_audit_event_type | 21:56:14.328 | 617 |

One `migrate.py --up`, three migrations, ~1.5 s. Applying is a deployment act and
is deliberately not automated.

**Merge → apply gap.** `20260908071149_dic_sme_assertions` landed on `origin/main`
in `023a60f7d` at 2026-09-08T07:53:02Z and was applied at 21:56:12Z — **inert for
~14.1 h**. That is the condition autonomy-dep-01 exists to make visible: the code
merged, the schema did not, and every test stayed green throughout.

`dic_sme_assertions` holds **0 rows**. That zero is the substrate distinction
`capability_consumption` keeps apart: **`empty`** (the table exists, a writer has
not run — dwr-ev-02's promotion route has not been exercised on this deployment)
and NOT **`absent`** (the migration never ran). The migration half is closed by
this card; whether a writer ever runs is a different question against a different
card, and is deliberately not folded in here.

## THE FINDING — `cleared` here means "this pending SET is no longer reported", not "the drift is gone"

This card's own acceptance criterion — *"`detector_findings` row
`a02733a6dc9aa2e6` reads status=cleared"* — **was satisfied at 2026-09-08
20:45:26, one hour and eleven minutes before the migration it names was
applied.** It later became honestly true at 21:56:12. Both readings are `cleared`
and nothing in the row distinguishes them.

**Mechanism.** `migration_drift_findings` fingerprints on the sorted pending set
(`fingerprint="|".join(versions)`, `tools/kanban/detector_findings.py:671-674`).
`_clear_missing` (`:907-920`) then marks `cleared` every active finding whose
`finding_id` is absent from the current run's list. For a **set-valued**
fingerprint those two rules compose into a false clear: when the pending set
*changes* — because another migration merged, not because anything was applied —
the `finding_id` changes, the old row is not in `still_active`, and it is written
`cleared` with its migrations still pending.

**Proof, and it needs no timezone reasoning** — the clear and the successor are
written in the same run, under one `now_iso` (`:1075`), so both timestamps below
are the same instant by construction:

| instant | finding marked `cleared` | its pending set | successor filed at that same instant | successor's pending set |
|---|---|---|---|---|
| 2026-09-05 12:24:18.059041 | `12a243722f` | `[20260903194350_dic_artifacts]` | `6abf31a8e7` | **contains `20260903194350_dic_artifacts`** + 3 floci |
| 2026-09-08 20:45:26.810428 | `a02733a6dc` *(this card)* | `[20260908071149_dic_sme_assertions]` | `03922726ad` | **contains `20260908071149_dic_sme_assertions`** + 2 |

In both rows the detector, in the very act of clearing the finding, reported the
same migration as still pending. Corroborated against `schema_migrations`:
`20260903194350` was applied 2026-09-05 15:23:12Z, ~2h59m **after** the finding
naming it was cleared; `20260908071149` at 2026-09-08 21:56:12Z, ~1h11m after.

**Why the precedent missed it.** `docs/audits/task-det-6abf31a8e7-migration-drift-resolution.md`
identified the same set-fingerprint behaviour and drew the correct conclusion for
*seeding* — a grown drift is a new finding, not a `seen_count` bump, which is why
these rows all carry `seen_count: 1` despite standing for hours. It did not carry
that one step further to the *clearing* rule, which is where it matters, because
`cleared` is what a card's acceptance criterion reads.

**Scope of the defect.** It is specific to set-valued fingerprints.
`born_red` (`fingerprint="born_red"`) and `recovery` (`"needed_a_human"`) use
constants and cannot exhibit it. `stranded` (`:747`,
`f"{reason}|{'|'.join(conflicts)}"`) is the same shape and is **named here, not
surveyed** — asserting it without measuring would be the defect this file is
about.

**Not fixed here, on purpose.** The card's own instructions are explicit: *"Do
NOT edit the detector, its threshold or its window so the finding goes away… If
the detector is wrong, that is a separate card against the detector, with the
survey that proves it."* This file is that survey; the card is **autonomy-dep-05**.
Editing `_clear_missing` from inside a card whose acceptance criterion that
function writes would be an actuator editing what it verifies.

## The recurrence, measured

Six `migration_drift` findings in six days, every one subject `deployment`:

| filed | finding | pending set | cleared |
|---|---|---|---|
| 2026-09-03 15:17:42 | `3a2f0f2aa1` | 1 (dic_documents_template_type_whitepaper) | 17:56:21 |
| 2026-09-04 11:58:57 | `12a243722f` | 1 (dic_artifacts) | 2026-09-05 12:24:18 *(false)* |
| 2026-09-05 12:24:18 | `6abf31a8e7` | 4 (dic_artifacts + 3 floci) | 18:24:22 |
| 2026-09-08 08:21:20 | `7a5948ff91` | 2 (dic_original_retention, dic_author_assertions) | 14:21:33 |
| 2026-09-08 14:21:33 | `a02733a6dc` **(this card)** | 1 (dic_sme_assertions) | 20:45:26 *(false)* |
| 2026-09-08 20:45:26 | `03922726ad` | 3 (dic_sme_assertions + 2) | 2026-09-09 02:45:34 |

Each `cleared_at` is the next `first_seen_at` exactly — the chain is one
continuous drift re-fingerprinted six times, not six separate incidents. Nothing
on any runtime path applies a migration: `grep` over `tools/genesis/` and
`tools/daemon/` for `migrate.py --up` / `MigrationRunner` returns nothing, which
is the detector docstring's own statement ("wired to NO startup path and NO
reflex") still holding. Whether that *should* change is autonomy-dep-02's
settled question — its answer was no — and is not reopened here.

## Not in scope, and left alone

`applied_not_on_branch_count: 32` — versions applied here that are not on
`origin/main`, including the legacy `173` / `186` and a 2026-08-2x cluster. The
detector reports `state: current` regardless of that number and does not fire on
it. It is the reverse condition, it has no finding, and this card is not the
place to act on it.

The detector, its threshold and its window were not touched.
