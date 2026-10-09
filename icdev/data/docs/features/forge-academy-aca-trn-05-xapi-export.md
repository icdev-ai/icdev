# CUI // SP-CTI

# FORGE Academy — xAPI Export (aca-trn-05)

## The problem

Completions and certificates lived only in `fa_*` tables. Nothing left the platform.
If Academy results are ever meant to count as **training of record** — the thing a
program office points at to say a person is qualified — they have to be readable by
the system that already holds that record, and today that is an LMS or an LRS.

## Why this waited on aca-int-01..07

This card carried a hard dependency, and the dependency was the point. Before the INT
epic:

- the browser sent its own `passed` flag, defaulting to `True` when omitted;
- XP was a running total in `fa_users.xp` mutated from eleven call sites with no
  record linking any award to the work that earned it;
- a certificate was a label, a token and a timestamp, with every detail of what
  satisfied it discarded at issue time.

Exporting that would have published unverified completions into a system of record —
laundering an unaudited number into an authoritative one by putting it in a standard
envelope. The INT epic made grading server-authoritative (`grading.grade_step`, the
graded party never supplies the grader), added the append-only `fa_xp_ledger`
(migration 315) and snapshotted certificate evidence (migration 317). Those three
things are what this export reads. Without them there is nothing here worth exporting.

## Why xAPI and not SCORM

SCORM's unit of record is a course launch with a single rolled-up completion and
score. The Academy's unit of record is a **verified step** — one submission graded
server-side against a stored test, with its own score, duration, hint count and
provenance row. Flattening that to one `cmi.core.score` per mission discards exactly
the granularity that makes the record worth exporting in the first place.

xAPI carries a statement per step, per mission and per certificate, each with its own
actor / verb / object / result. That matches the data.

**SCORM is deliberately not implemented.** It is a packaging format for a *specific*
target LMS; building it before a target exists means guessing at the manifest, the
launch sequence and the rollup rules. When a named LMS demands SCORM, wrap these
statements — the data model is the hard part and it is done.

## What is exported

| Record | Verb | Activity type | Provenance required |
|--------|------|---------------|---------------------|
| Verified step completion | `.../verbs/passed` | `.../activities/assessment` | `fa_xp_ledger` `reason='step_pass'`, `source_type='step'` |
| Mission completion | `.../verbs/completed` | `.../activities/course` | `fa_xp_ledger` `reason='mission_complete'`, `source_type='mission'` |
| Certificate | `openbadges/.../earned` | `.../activitytype/certificate` | ≥1 row in `fa_certificate_evidence` |

A step statement carries the real percentage from the assessment model (2 of 3 correct
is 67, not 100), the measured `started_at → completed_at` duration when both ends
parse, the mission as its `contextActivities.parent`, and a `registration` UUID shared
with that mission's own statement so an LRS groups one learner's run together.

## What is refused

The interesting behaviour of an export into a system of record is what it will not
emit.

- **No provenance row → withheld.** A completion with no matching ledger entry, or one
  the migration-315 backfill flagged `verified=0` because it could only reconstruct the
  amount, is not exported by default.
- **Withheld is counted, not hidden.** The result carries an `excluded` block
  (`unverified_step`, `unverified_mission`, `unverified_certificate`,
  `unidentifiable_actor`, `missing_timestamp`) so a caller never mistakes a filtered
  export for a complete one.
- **`include_unverified=True` flags rather than launders.** The statement is emitted
  with a provenance extension carrying `verified: false`, so a consumer can filter on
  the statement itself after it leaves the platform. There is no mode in which an
  unverifiable completion is presented as a verified one.
- **A certificate with no evidence is not a certificate.** Zero rows in
  `fa_certificate_evidence` is precisely the state migration 317 exists to end; such a
  certificate is unverified and withheld.
- **No fabricated identity.** A learner with an email gets an `mbox`; one without gets
  an `account` scoped to this platform's homePage, which is honest about being a local
  identity. A learner with neither is excluded and counted — never anonymised into a
  synthetic identifier that an LMS would then match against a real person.
- **No fabricated duration.** Emitted only when both timestamps parse and the interval
  is non-negative; an LMS reports duration as time on task.

## Idempotence

Statement IDs are UUIDv5 over `activity | actor | verb | timestamp`, so re-running the
export and re-POSTing to the same LRS updates nothing rather than duplicating the
learner's history. The UUID namespace is fixed forever — changing it would re-issue
every statement ID and an LRS would accept the whole history a second time.

## Interfaces

```bash
python -m apps.forge_academy.xapi --json                                     # envelope
python -m apps.forge_academy.xapi --statements-only --out academy_feed.json  # LRS POST body
python -m apps.forge_academy.xapi --user-id 1 --since 2026-01-01T00:00:00Z   # incremental
```

`GET /api/academy/export/xapi` — `@require_org_intel`, the same admin/pm/isso tier as
Oracle and Org Readiness. Feeding an external LMS is an administrative act over every
learner's record, not a per-learner one; `?user_id=` narrows it for a single
transfer-of-record request.

`ICDEV_XAPI_ACTIVITY_BASE` (default `https://icdev.ai/xapi/forge-academy`) is
configurable because an LRS keys activities by IRI: two deployments feeding one LRS
must not both claim `.../mission/m-t1-01-prompting`.

## Success criteria (all covered by `tests/test_academy_xapi_export.py`)

1. A ledger-backed step becomes one `passed` statement with the right activity IRI,
   actor, score, duration and parent mission.
2. A `verified=0` step is withheld and counted in `excluded`.
3. `include_unverified=True` emits it stamped `verified: false`.
4. A certificate with no evidence rows is withheld; one with evidence is emitted with
   the row count.
5. A mission completion is a `completed` statement against a `course` activity.
6. Statement IDs are stable across runs and unique within one export.
7. Step and mission statements share one `registration` per mission.
8. `since` filters by completion time.
9. A learner without an email gets an `account`, never a fabricated `mbox`.
10. A learner with no identifier at all is excluded, not anonymised.
