# CUI // SP-CTI

# task-det-507624da9f — migration drift cleared: `20261011222631` applied

**Detector:** `migration_drift` (autonomy-dep-01) · **Finding:** `507624da9f057437`
· **Resolved:** 2026-10-13

## What the card asked

One migration on `origin/main` and not applied to this deployment:

```
20261011222631_audit_event_types_re_analysis
```

## What was actually true when re-derived

The migration is applied. The ledger row on the live database:

```
schema_migrations  version 20261011222631  name audit_event_types_re_analysis
                   applied_at 2026-10-13 21:24:58+00  applied_by icdev-migrate
                   execution_time_ms 903  checksum 712537a84dcc3b54
                   rolled_back_at NULL
```

It was applied by `migrate.py --up` (`applied_by icdev-migrate`) about two minutes
after this card was scheduled (21:23:08Z). Attempt #1 of this card ran then, and it
committed nothing, so the board had no record that the deployment act had happened.
This file is that record.

## Acceptance criteria — both hold

```
python tools/db/migration_drift.py --json   ->  state: current, pending_count: 0,
                                                applied_count: 450 (was 449)
python tools/db/migration_drift.py --gate   ->  exit 0
detector_findings 507624da9f057437          ->  status cleared,
                                                cleared_at 2026-10-13T21:25:45,
                                                seen_count 8
```

The finding cleared on the first measurable reflex cycle after the apply, and it
cleared because the schema changed. The finding's fingerprint is the set
`{20261011222631}`, and that set is now empty.

**The detector was not touched.** Its threshold, window and fingerprint are the same
as before.

## Not done here

- The 32 versions applied here but absent from `origin/main` are context, not a
  finding. They are unchanged, as in task-det-12d839d263.
