<!-- CUI // SP-CTI -->
# task-det-7a5948ff91 — migration drift was applied by `icdev-migrate` 17 hours before the card was dispatched

- **Task:** task-det-7a5948ff91 (filed by `detector_findings_reflex`, detector
  `migration_drift` / autonomy-dep-01, finding `7a5948ff916cff0b`, `seen_count` 1,
  `card_count` 1)
- **Subject:** `deployment` — 2 migrations on `origin/main`, not applied here
- **Date measured:** 2026-09-09 02:45–02:56Z, against the live PG board (`icdev`,
  user `icdev`, backend `postgresql`), `origin/main` at `00718bd52` after a fresh
  fetch

## Verdict

Nothing is left to apply. Both acceptance criteria were already satisfied when
the card reached a worker, and each was re-derived independently rather than
read off the projection:

| Criterion | Measured 2026-09-09 02:45–02:56Z |
|---|---|
| `python tools/db/migration_drift.py --json` reports `deployment` | **no** — `state: current`, `pending_count: 0`, `pending: []` |
| `origin/main` ref is fresh (not understating pending) | fetched to `00718bd52` before re-deriving; `on_branch_count` 413, `applied_count` 445 |
| the 2 versions in `schema_migrations` | both present, `applied_at` 2026-09-08 09:33:40.037996Z / 09:33:40.275180Z |
| the **schema** exists in the live catalogue | `dic_documents` carries `original_path`, `original_sha256`, `original_retained_at`; `to_regclass('public.dic_author_assertions')` resolves — a ledger row is a claim, the column is the fact |
| `detector_findings` row `7a5948ff916cff0b` | `status: cleared`, `cleared_at` 2026-09-08 14:21:33.706Z |
| latest `migration_drift` detector run | `clean`, `last_findings` 0, 66 runs / 66 measurable, `last_run_at` 2026-09-09 02:45:34Z |
| `detector_findings --stats` | `migration_drift clean active=0 cleared=6` |

The database measured is the real deployment, not a worktree throwaway: this
worktree carries no `.env`, so the identity was checked explicitly
(`current_database() = icdev`, `current_user = icdev`, 445 rows in
`schema_migrations`) before any verdict was drawn. A fresh-worktree SQLite
reporting `Pending: 0` is the trap the card's own text names, and it is not what
happened here.

## Who applied it, and when

`audit_trail`, actor `icdev-migrate`, all times UTC 2026-09-08. **One**
`migrate.py --up` run applied **three** migrations in 132 ms:

| version | name | applied | ms |
|---|---|---|---|
| 20260908003311 | dic_original_retention | 09:33:40.155993 | 26 |
| 20260908003920 | dic_author_assertions | 09:33:40.278081 | 44 |
| 20260908091858 | dic_page_geometry | 09:33:40.346973 | 62 |

Applying is a deployment act and is deliberately not automated; an operator did
it, 17.0 hours before this card's scheduled dispatch (2026-09-09 02:21:41Z).

## Merge → apply gap (how long each capability was inert)

| migration | landed on main | inert for |
|---|---|---|
| 20260908003311_dic_original_retention | `73086fb41` merge PR #2170 (dwr-fid-01) 2026-09-08T07:06:50Z | ~2.4 h |
| 20260908003920_dic_author_assertions | `fb0f1a551` squash PR #2173 (dwr-ev-01) 2026-09-08T07:02:08Z | ~2.5 h |

The detector fingerprinted the finding at 08:21:20Z — 74 and 79 minutes after
the two landed. This is the condition autonomy-dep-01 exists to make visible,
caught an order of magnitude faster than the 37.5 h recorded against
task-det-6abf31a8e7.

## The third migration, applied 11 hours *before* it merged

`20260908091858_dic_page_geometry` is not in the card's evidence and could not
have been: it landed on `origin/main` at 2026-09-08T20:33:53Z (`1fc9da025`,
merge PR #2186), **11h 00m after** it was applied here at 09:33:40Z.

`migrate.py` reads the **filesystem**, so an operator running `--up` applies
whatever that checkout holds, merged or not. The card's own text warns about
this property pointing the other way — a checkout *behind* the branch reports
`Pending: 0` and applies nothing. It is the same property, and it is the reason
`applied_not_on_branch_count` is a real number rather than noise.

The detector correctly does not fire on it. `migration_drift` asks whether
anything on the branch is missing **here**; it does not ask whether anything
here is missing from the branch, and folding the second question into the first
would make every pre-merge apply read as drift.

## What the schema holds now — not `empty`

Unlike task-det-6abf31a8e7, where every new table read `empty`, both capabilities
have **written rows** since the apply:

| substrate | now | first write |
|---|---|---|
| `dic_documents.original_*` | 57 documents, 2 with a retained original | 2026-09-08T21:59:56Z (12.4 h after the apply) |
| `dic_author_assertions` | 1 row — `SNMPv2c` / `protocol` / `retired` | 2026-09-08T22:00:07Z |

So the migration is not merely recorded in the ledger; the code it unblocked has
run against it. That is the strongest available evidence that the inertness this
card names is over.

## Second derivation, sharing no code with the detector

`python -m tools.document_intelligence.originals --survey --json` reports
`schema.original_columns_present: true`. It asks a different source (the live
`information_schema` catalogue, via the dwr-fid-01 survey) than
`migration_drift.py` does (`schema_migrations`), and the two agree. One
computation trusted twice would have proved only that the ledger is
self-consistent.

## Not a finding — a worktree artifact, recorded so it is not read as one

That same survey reports `retention.exists: false`, `files_retained: 0` and
`retained_missing: 2`. `ICDEV_DIC_ORIGINALS_DIR` defaults to the **relative**
path `data/document_intelligence/originals`, resolved against the current working
directory, and this worktree has no such directory. Run against the main
checkout both files are present:

```
C:/AI/ICDev/data/document_intelligence/originals/41/4189d383…fd3141.md
C:/AI/ICDev/data/document_intelligence/originals/8a/8add87ea…c21683.md
```

`retained_missing: 2` therefore describes **where the survey was run**, not the
deployment. It is recorded here rather than dropped, because a survey number
quoted without its cwd is exactly how a fabricated finding gets published.

## Not in scope, and left alone

`applied_not_on_branch_count: 32` — versions applied here that are not on
`origin/main`, including the legacy `173` / `186` and a 2026-08-2x cluster. The
detector reports `state: current` regardless of that number and does not fire on
it. It is the reverse condition, it has no finding, and this card is not the
place to act on it.

The detector, its threshold and its window were not touched.
