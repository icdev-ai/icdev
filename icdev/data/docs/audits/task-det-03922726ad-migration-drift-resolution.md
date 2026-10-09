# task-det-03922726ad — migration_drift resolution record

**Detector:** `migration_drift` (autonomy-dep-01)
**Finding:** `03922726ad17f1c1` — subject `deployment`, first seen 2026-09-08T20:45:26Z, seen 1x
**Verdict at dispatch: ALREADY RESOLVED.** No code change was required and none was made.

## What the card reported

Three migrations were on `origin/main` and not applied to this deployment:

| version | name |
|---|---|
| 20260908071149 | `dic_sme_assertions` |
| 20260908071432 | `dic_suggestions_supersede_columns` |
| 20260908071433 | `dic_redraft_audit_event_type` |

Applying is a deployment act and is deliberately not automated. The card is
therefore satisfied by an apply having happened, not by an edit here.

## Re-derivation (2026-09-09, live PG board `icdev`)

```
python tools/db/migration_drift.py --json
```

```json
{"state": "current", "ref": "origin/main", "on_branch_count": 413,
 "applied_count": 445, "pending_count": 0, "pending": []}
```

`state` is `current` and `pending` is empty — the derivation no longer reports
`deployment`. The board it read is `{'backend': 'postgresql', 'database':
'icdev', 'measured': True}`, i.e. the live deployment and not a worktree's
throwaway SQLite (an empty database would have reported `applied_count: 0`,
which is the trap this record exists to rule out).

## The apply is RECORDED, and the SCHEMA is there

`schema_migrations` carries all three, applied 2026-09-08 at 21:56 UTC —
71 minutes after the finding was recorded and before this card was dispatched:

```
20260908071149  2026-09-08 21:56:12.670242+00
20260908071432  2026-09-08 21:56:13.680614+00
20260908071433  2026-09-08 21:56:14.271268+00
```

A recorded version is a claim that a migration ran, not proof that its objects
exist — a failed `up.py` that still stamped the ledger reads identically. So
each was verified INDEPENDENTLY against the live catalogue, one object per
migration:

- `20260908071149` — `to_regclass('public.dic_sme_assertions')` resolves
  (dwr-ev-02's attributed SME assertion store).
- `20260908071432` — `information_schema.columns` carries
  `dic_suggestions.successor_suggestion_id` (dwr-ev-03's redraft successor link).
- `20260908071433` — the `audit_trail` event-type CHECK admits `dic.redraft`.
  This one is load-bearing rather than cosmetic: `redraft_change` writes its
  `.intent` row fail-closed, so on a board that had not run this migration the
  CHECK refuses the type and EVERY redraft is refused.

## Detector state

`detector_findings` row for `03922726ad17f1c1` / `task-det-03922726ad`:

```
detector=migration_drift  subject=deployment  status=cleared  seen_count=1
cleared_at=2026-09-09 02:45:34.867294
```

Cleared by `detector_findings_reflex` on a MEASURABLE cycle that no longer
reported the subject — not by anything in this branch. `--list` shows it under
`cleared`, alongside three sibling `migration_drift` findings that resolved the
same way.

## What was NOT done, and why

The detector, its threshold and its window are untouched (an actuator never
edits what it verifies). `python tools/db/migrate.py --up` was NOT run from
this worktree: `deployment_freshness` reports this checkout 11 commits behind
`origin/main` and on a task branch, and the pending set was already empty —
re-running a migrator against a `current` board to make a card feel worked is
an unaudited write for no measurement.

Both acceptance criteria were met before this session opened. This record is
the evidence for that, so "the card closed" is separable from "the card was
never real".
