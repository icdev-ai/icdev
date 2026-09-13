# CUI // SP-CTI

# autonomy-act-08 — how late the merge ledger actually is

Measured 2026-09-12 against the live PostgreSQL board. Reproduce with:

```bash
python -m tools.idp.delivery_events --landing-latency --days 30 --json
```

## 0. The question, and the answer

autonomy-act-06 gave the record-not-card rule a SECOND door: the merge ledger
(`change landed on main: <task-id>` carrying `source: kanban_merge_ledger`),
which records a landing whatever merged it. autonomy-act-07 then measured that
all 11 recovery subjects with no `pr_watcher.merge` row are satisfied through
it, and named the residual: the ledger is written by a **6-hourly reflex
backfill**, not at the moment of merge.

The card asked for the number before the fix, and warned that six hours is the
reflex **cadence**, not a measured lag. It is not the lag.

| | |
|---|---:|
| landings measured (30 days to 2026-09-12) | 625 |
| p50 | **4.0 h** |
| p75 | 5.6 h |
| p90 | 6.9 h |
| p95 | **9.2 h** |
| max | **96.8 h** |
| written within a minute of the landing | 2 (0.3%) |

The tail is what the cadence figure hides: **9 landings waited more than a day**
and the worst waited four. A cycle that is skipped, circuit-broken or simply
started before the merge does not retry — it waits for the next one.

## 1. Why the row cannot time itself

`_emit_change` stamps `audit_trail.created_at` with the moment the change
**landed**, deliberately — the module docstring says "not at backfill time",
and that is what makes `deploy_frequency` countable. So the emission time is
nowhere in the row, and asking "is the ledger current?" by reading ledger
timestamps always answers yes.

`audit_trail` ids are assigned in insertion order, so a ledger row is bracketed
from above by the first **non-ledger** row with a higher id: whatever wrote that
row did so after this one was inserted. Ledger rows are grouped into emissions
by contiguous id, so one bracketing query serves a whole sweep. Both ledger
event types are excluded from the bracket — `deployment_failed` is written in
the same transaction and backdated the same way, so counting it would bracket an
emission with its own output.

**Every lag above is therefore an UPPER BOUND**, and the survey reports how
loose: median bracket 5.3 minutes, worst 3.1 hours (a quiet period with no other
writer). A row that cannot be bracketed at all is reported as `unbracketed`
rather than dropped — 0 of 625 here. This is `landing_latency` in
`tools/idp/delivery_events.py`.

## 2. Has a promotion ever fallen inside the window?

The card's own escape hatch: if the lag is small enough that no promotion ever
fell inside it, close this as not-worth-fixing. It is not, and one did.

Point-in-time over the 40 recovery-detector cards that reached `scheduled`,
using the bracketed emission time rather than the row's backdated `created_at`:

| | n |
|---|---:|
| promoted detector cards | 40 |
| whose subject's landing is visible ONLY through the ledger | 11 |
| **promoted while the subject had landed and the ledger row did not yet exist** | **1** |

The one is **`rmf-ui-13`**:

| time (UTC, 2026-09-03) | event |
|---|---|
| 18:43:57 | lands through `cli.py --set-status rmf-ui-13 done --merge` |
| 20:11:56 | its `[NEEDED-A-HUMAN]` card is promoted to `scheduled` |
| ≤ 22:40:12 | the 6-hourly sweep writes the ledger row |

At 20:11 the subject was delivered, and both doors said otherwise: no
`pr_watcher.merge` row (this door does not write one) and no ledger row (the
sweep had not run). Under autonomy-act-07's promotion gate that card reports
CARD and a worker session is spent on delivered work — which is exactly the
residual act-07 named and declined to fix.

The other ten are not counterexamples, they are the sampling: eight subjects
landed AFTER their card was promoted (carding them was correct), and two were
visible in time. One of those two, `artifact-fresh-ee3339893b`, missed by
**fifteen minutes** — it landed at 20:36:02, a sweep happened to run at ~21:19
and the card promoted at 21:34:14. A sweep 20 minutes later and it is the second
instance.

## 3. Which doors this reaches

Of the 11 ledger-only landings, by the transition row that recorded them:

| door | n |
|---|---:|
| `cli.py --set-status <id> done --merge` (land.py, mfx-mrg-04) | **7** |
| `--force-done --reason` (the work landed elsewhere; nothing merged here) | 4 |

`--merge` is the door this card wires, it carries 7 of the 11 including
`rmf-ui-13`, and it ran **79 times in the 30 days measured**.

Two doors are named and NOT wired, each for a stated reason:

* **The Actions auto-merge workflow (mfx-mrg-07)** runs `runs-on: ubuntu-latest`
  with a `gh` token and no database — it cannot write an `audit_trail` row, and
  it does not mark the task `done` either, so the landing reaches the board
  through some other door regardless.
* **`pr_watcher`'s own loop** is a merge door, and it is the biggest one (381
  landings in the same 30 days) — but it already writes a `pr_watcher.merge`
  row, so its landings are visible to the ordering rule *instantly* and none of
  them is in the 11 above. What it would buy is a fresher DORA input, not a
  withheld card, and the cost is specific: `tools/ci/pr_watcher.py` is a
  `protected_paths` entry, so a PR touching it cannot merge through the normal
  door and holds every later protected-path PR behind a human. Not this card's
  defect, and `emit_landing` takes any task id when someone decides it is worth
  that.
* **`--force-done`** is not a merge door. It records that work landed somewhere
  else, and widening it is a separate decision that deserves its own evidence
  (280 force-done transitions in the same 30 days — far too big a population to
  attach to this card's measurement).

## 4. What ships

`tools/idp/delivery_events.emit_landing` — ONE writer, called by the `--merge`
door in `tools/kanban/cli.py` after the `done` row is committed (the board row
is the evidence; there is nothing to derive before it exists).

* It is the **same row**, not a second shape: same `collect_changes` derivation
  narrowed by task id, same `_emit_change` writer, same action prefix and
  `source`. `tests/test_landing_at_merge_time.py` asserts acceptance through
  `detector_findings.ledger_landing` itself rather than by re-reading columns.
* **Idempotent in both directions through the existing dedupe.** It asks
  `emitted_task_ids` before writing and the sweep asks the same function, so
  neither writes a row the other has. One set, read from both sides.
* **Best-effort, after the transaction closed.** A ledger failure cannot
  un-write a confirmed merge, and the 6-hourly sweep remains the backstop for a
  row that fails here. Never silent: the outcome prints and is in `--json`.

Unchanged, deliberately (acceptance criteria 4 and 5): `OUTCOME_ACTIONS` is not
widened, nothing here writes a `pr_watcher.merge` row — that action name is the
watcher's own audit vocabulary and a second writer of it makes every pr_watcher
survey wrong — and the detector, its thresholds and its window are untouched.
This changes only WHEN a true landing becomes visible.

## 5. Re-measuring after

`at_merge_time` in the survey is the count of rows written within a minute of
the landing they describe. It was **2 of 625** before this card. Every `--merge`
landing after it should be in that bucket; the rest of the distribution is the
sweep still covering the doors that cannot write.
