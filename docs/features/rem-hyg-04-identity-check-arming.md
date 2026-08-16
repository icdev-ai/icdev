# CUI // SP-CTI

# Arming the seed-time identity check (rem-hyg-04)

**Card:** REM — repository hygiene · **Task:** `rem-hyg-04` · **Date:** 2026-08-16
**Precursors:** [`rem-hyg-02`](../../tools/kanban/task_identity.py) (the check),
[`rem-hyg-03`](rem-hyg-03-identity-survey.md) (the survey)

## What this task did

`tools/kanban/task_identity.py` has answered, since rem-hyg-02, the question
nothing asked at seed time: *does a registered card own this id's prefix, and
does an epic key claim it?* It answered into a log. rem-hyg-04 gives that answer
the ability to refuse, behind a named switch — and, on the evidence, leaves the
switch at `report`.

| | |
|---|---|
| Switch | `KANBAN_IDENTITY_CHECK` = `enforce` \| `report` \| `off` |
| Default | **`report`** |
| Read by | `tools/kanban/task_identity.py::mode()` |
| Acted on by | `tools/kanban/task_factory.py::create_tasks`, before the first `INSERT` |
| Narrowed by | `tools/kanban/task_identity.py::is_enforceable()` |

## The decision, and the number behind it

The task's own precondition was that the survey must show the check would not
refuse routine seeding — and that if it would, the check gets **narrowed and
re-surveyed**, never armed with a widened exemption list to compensate.

Measured against the live PostgreSQL board on 2026-08-16 (3,244 rows, 164 cards):

```
claimed            2050   an epic counts the row — arming leaves these alone
gate sentinels       53   holds the card, never work, NEVER an orphan
no_epic              22   card owns the prefix, no epic claims it   ACTIONABLE
no_card            1119   no registered task_prefix owns it         ACTIONABLE
  |- card-shaped    330     a card is genuinely missing
  `- opaque         789     task-<hex> — NEVER card work
------------------------------------------------------------------
ACTIONABLE         1141   fire rate 35.17%   (refuse every unclaimed id)
NARROWED            352   fire rate 10.85%   (exempt opaque machine ids)

  WINDOW        ROWS   ACTIONABLE     RATE    NARROWED     RATE
  last   7d      190           33   17.37%           0    0.00%
  last  30d     1569          595   37.92%         248   15.81%
  last  90d     3191         1131   35.44%         352   11.03%
```

**The narrowing.** 789 of the 1,119 unclaimed ids are opaque machine ids —
`task-<hex>` is what the dashboard's own create-task API and
`awareness/suggested_card_writer` generate (`f"task-{uuid4().hex[:10]}"`). No card
was ever meant to count those rows. Refusing them would block routine work on
every seeding path, which is precisely how eight of the twelve PreToolUse checks
came to refuse 4.86% of 96,818 real tool calls. They are exempt from the
**refusal** and remain in the **report**, with `(REPORT ONLY — an opaque machine
id is never refused …)` appended to the finding a reader actually sees.

**Why `report` and not `enforce`.** Even narrowed, the rate over the window
arming would actually hit — 30 days — is **15.81%, or 248 seeds**. That is ten
times the rate CLAUDE.md already characterises as refusing routine work. The
findings are real (16 unregistered card prefixes, 330 card-shaped rows), which is
why they are still logged at the default; what is not defensible is converting
248 correct observations a month into 248 blocked seeds without an operator
choosing it. The task's instruction was explicit — *a wrong call here blocks ALL
seeding on every path, so err toward report* — and the survey does not support
`enforce`.

**Two further narrowings were considered and rejected**, because each would be
widening an exemption to improve the number rather than making the check right:

* dropping `no_card` would take the rate to ~1.4% and gut the HCX case the whole
  card exists for (25 live rows under a prefix no card had declared);
* exempting the 16 unregistered prefixes by name would exempt the defect itself.

## One predicate, two readers

`classify_shape`, `SHAPE_CARD` and `SHAPE_OPAQUE` were **moved** from
`identity_survey.py` into `task_identity.py`, and the survey now computes its
`NARROWED` column through `is_enforceable` — the same function `create_tasks`
calls. They remain importable from `identity_survey` for existing callers, but
there is deliberately only one copy.

This is the property that makes the survey worth having: the number in the report
is *exactly* the population `enforce` refuses. Two copies of "is this a finding?"
is how a measured rate and an enforced rate drift apart while both look measured
— which is the failure rem-hyg-03 exists to prevent. Re-running the survey after
the refactor produced byte-identical counts.

## The kill switch

An **environment variable**, matching `KANBAN_LANDED_CHECK` in spelling and
shape (`1/true/yes` → enforce, `0/false/no/none` → off, `warn` → report), so one
habit works for both. Never a shell operator: CLAUDE.md's `|| true` finding is
that a neutraliser inside a JSON string is unauditable and suppresses only the
*working* case. Never a config key the code does not read either — the inert
`hook_points:` block in `args/extension_config.yaml` is what that failure looks
like; `mode()` is the sole reader and every consumer calls it.

An **unrecognised** value resolves to the default **and logs that it did**.
`KANBAN_IDENTITY_CHECK=enforced` is one keystroke from `enforce`; falling back
silently would leave an operator believing the check was armed when it was not,
which is the same class of failure as a switch nothing reads.

## Refusal shape

Evaluated before any insert, so a refusal cannot half-land a batch — asserted by
proving the `ValueError` arrives *instead of* the first database call. Following
`_work_id_suggestion()`'s example, it ends in an edit rather than a puzzle: each
offending id, the id it should have carried (`<prefix><epic>-<N>`), both places
the fix can go, and the way to stand the check down.

```
refusing to seed 2 task id(s) no epic claims: rem-ghost-01 (no_epic — use
'rem-<epic>-01'); zzz-live-01 (no_card — use 'zzz-<epic>-01'). Every number on a
project card comes from <task_prefix><epic_key>-% patterns and never from
task_prefix alone, so these rows would be counted by nothing — and a card ALL of
whose rows are unclaimed vanishes from Home entirely, which looks exactly like a
project with no work. Fix it in one of two places: rename the id to an epic the
card already declares, or register the epic (or the whole card) in
args/projects.yaml. Stand this check down with KANBAN_IDENTITY_CHECK=report to
log instead of refusing, or KANBAN_IDENTITY_CHECK=off.
```

## Scope limit, carried forward

95 modules `INSERT INTO kanban_tasks` directly and never reach the `create_tasks`
seam. Arming that seam cannot see them. This was already recorded by rem-hyg-03
and is not fixed here; it bounds what `enforce` would achieve even if the rate
supported it.

## Fail-open behaviour

Unchanged and tested. An unreadable `args/projects.yaml` yields zero findings
rather than one per id — with no cards every id looks unowned, and that is 3,000
fabrications rather than a measurement. Any exception inside the check leaves
seeding exactly as it was.

## Tests

`tests/kanban/test_identity_check_arming.py` — 35 tests, <1s, registry injected
and every `get_connection` alias stubbed; no board, no network, no LLM. Gated in
`args/ci_test_files/core.txt` in the same PR, per the test-gating policy.

## Changing the default later

1. `python -m tools.kanban.identity_survey --env-file <path>/.env`
2. Read `enforcement.mode` (which posture the rate describes) and the 7/30-day
   `NARROWED` rates (the population arming would hit).
3. Change `DEFAULT_MODE` only if that rate is clean — and record the measurement
   next to it, as this task did.

Do not raise it by widening an exemption list.
