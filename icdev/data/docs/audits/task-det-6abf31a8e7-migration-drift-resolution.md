<!-- CUI // SP-CTI -->
# task-det-6abf31a8e7 — migration drift was applied by `icdev-migrate` two hours before the card was dispatched

- **Task:** task-det-6abf31a8e7 (filed by `detector_findings_reflex`, detector
  `migration_drift` / autonomy-dep-01, finding `6abf31a8e7ca6804`, `card_count` 1)
- **Subject:** `deployment` — 4 migrations on `origin/main`, not applied here
- **Date measured:** 2026-09-05 ~18:50Z, against the live PG board (`icdev`, user
  `icdev`, backend `postgresql`), `origin/main` at `f40935be5` after a fresh fetch

## Verdict

Nothing is left to apply. Both acceptance criteria were already satisfied when
the card reached a worker, and each was re-derived independently rather than
read off the projection:

| Criterion | Measured 2026-09-05 ~18:50Z |
|---|---|
| `python tools/db/migration_drift.py --json` reports `deployment` | **no** — `state: current`, `pending_count: 0`, `pending: []` |
| `origin/main` ref is fresh (not understating pending) | fetched to `f40935be5` before re-deriving; `on_branch_count` 400 |
| the 4 versions in `schema_migrations` | all 4 present, `applied_at` 2026-09-05 15:23:12–13Z |
| the 4 **tables** exist in the live schema | all 4 — a ledger row is a claim, the table is the fact |
| `detector_findings` row for this task | `status: cleared`, `cleared_at` 2026-09-05 18:24:22Z |
| latest `migration_drift` detector run | `clean`, 57 runs / 57 measurable |

The database measured is the real deployment, not a worktree throwaway: this
worktree carries no `.env`, so the identity was checked explicitly
(`current_database() = icdev`, 432 rows in `schema_migrations`) before any
verdict was drawn. A fresh-worktree SQLite reporting `Pending: 0` is the trap
the card's own text names, and it is not what happened here.

## Who applied it, and when

`audit_trail`, `event_type = config_changed`, actor `icdev-migrate`, all times
UTC 2026-09-05:

| version | name | applied | ms |
|---|---|---|---|
| 20260903194350 | dic_artifacts | 15:23:12.955 | 109 |
| 20260905070028 | floci_twin_snapshots | 15:23:13.072 | 50 |
| 20260905093010 | floci_az_twin_snapshots | 15:23:13.088 | 7 |
| 20260905104843 | floci_gcp_twin_snapshots | 15:23:13.125 | 31 |
| 20260905115811 | floci_oci_twin_snapshots | 15:23:13.171 | 40 |

One `migrate.py --up`, five migrations, 237 ms. Applying is a deployment act and
is deliberately not automated; an operator did it.

## The fifth migration, and why no second card was filed

The card's evidence names **four** pending migrations. **Five** were applied.
`20260905115811_floci_oci_twin_snapshots` landed on `origin/main` at 12:45:48Z —
21 minutes *after* this finding was fingerprinted at 12:24:18Z — which is the
`on_branch_count` 399 → 400 delta between the card's blob and today's run.

That fifth version never got its own card, and the reason is structural rather
than lucky. `migration_drift_findings` fingerprints on the sorted pending SET
(`"|".join(versions)`, detector_findings.py:481-484), so a drift that GROWS is a
*different* finding, not a `seen_count` bump on this one. The board's own history
shows the same detector and the same subject `deployment` under three distinct
fingerprints (`20260903100336`; `20260903194350`; and this card's four-version
set). No five-version row was ever written — the operator's apply landed before
the next cycle could file one.

This also explains `seen_count: 1` against a finding that stood for ~6 hours:
it could not accumulate, because the set it names stopped being the pending set
21 minutes after it was created.

## Merge → apply gap (how long each capability was inert)

| migration | landed on main | inert for |
|---|---|---|
| 20260903194350_dic_artifacts | `0a5c93e67` 2026-09-04T01:54:40Z | ~37.5 h |
| 20260905070028_floci_twin_snapshots | `8ec7c2b85` 2026-09-05T08:31:20Z | ~6.9 h |
| 20260905093010_floci_az_twin_snapshots | `2d2b2cc9a` 2026-09-05T10:22:33Z | ~5.0 h |
| 20260905104843_floci_gcp_twin_snapshots | `29fd944a6` 2026-09-05T11:36:34Z | ~3.8 h |
| 20260905115811_floci_oci_twin_snapshots | `3a8ce8cd7` 2026-09-05T12:45:48Z | ~2.6 h |

This is the condition autonomy-dep-01 exists to make visible: the code merged,
the schema did not, and every test stayed green for a day and a half.

## What the tables hold now — `empty`, not `absent`

`dic_artifacts` 0 rows, `floci_twin_snapshots` 0, `floci_az_twin_snapshots` 2,
`floci_gcp_twin_snapshots` 0. Those zeroes are the substrate distinction
`capability_consumption` keeps apart: **`empty`** (the table exists, a writer has
not run) and NOT **`absent`** (the migration never ran). The migration half is
closed by this card; whether a writer ever runs is a different question against a
different card, and is deliberately not folded in here.

## Not in scope, and left alone

`applied_not_on_branch_count: 32` — versions applied here that are not on
`origin/main`, including the legacy `173` / `186` and a 2026-08-2x cluster. The
detector reports `state: current` regardless of that number and does not fire on
it. It is the reverse condition, it has no finding, and this card is not the
place to act on it.

The detector, its threshold and its window were not touched.
