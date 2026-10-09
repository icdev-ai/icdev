# CUI // SP-CTI

# autonomy-act-07 — the record-not-card rule, re-derived at PROMOTION

Measured 2026-09-12 against the live PostgreSQL board. Reproduce with:

```bash
python -m tools.kanban.promotion_gate --survey --json
python -m tools.kanban.promotion_gate --records --json
python -m tools.kanban.detector_findings --records          # the detector's own view
```

## 1. The incident, end to end

The card was RIGHT when it was seeded, which is what separates this from
autonomy-act-04's defect.

| time (UTC) | event |
|---|---|
| 16:58–17:01 | three artifact-freshness PRs are genuinely CONFLICTING; `pr_watcher` escalates each |
| 20:27:35 | `detector_findings` projects three `recovery` findings and seeds three `[NEEDED-A-HUMAN]` cards. CORRECT. |
| 20:36:02 | `artifact-fresh-ee3339893b` lands (merge-ledger row; no `pr_watcher.merge`) |
| 20:57:37 | `artifact-fresh-da63da118f` merges — `pr_watcher.merge` written |
| 21:22:01 | `artifact-fresh-a7486018c7` merges — `pr_watcher.merge` written |
| 21:34:14 | all three cards are promoted to `scheduled` |
| 21:34:34–50 | all three are DISPATCHED — one worker session each |
| 21:35:28–29 | all three are force-closed by hand: *"Nothing left to build — a dispatch here cannot go RED."* |

At 21:34 every one of the three satisfied the record-not-card conjunction in
full. The rule was evaluated once, at 20:27, and never again.

## 2. The replay (acceptance criterion 5)

Point-in-time over every `recovery` finding that drew a card and reached
`scheduled` — 40 of 41 (one card has no `scheduled_at` and was never promoted;
it is excluded from the denominator rather than counted as a pass). Each is
evaluated with only the watcher rows, ledger rows and subject board status that
existed at that card's own `scheduled_at`.

| seeding verdict → promotion verdict | n | what this gate changes |
|---|---:|---|
| `card` → `record` | **18** | **WITHHELD — the incremental effect of this card** |
| `record` → `record` | 13 | already a record at seeding; act-04/-06 never card these today |
| `card` → `card` | 9 | untouched |
| | **40** | total promoted |

Total withholds at promotion: **31 of 40 (77.5%)**. The 18 in the first row are
what this change actually buys; the 13 in the second are the seeding-time rule
already working and are counted separately so the two are never conflated.

### The control — how many would have been withheld WRONGLY

Independent of the rule under test: `landed_check.check_landed_bulk` asks git
whether the subject id is on `origin/<default>`, sharing no input with the two
audit rows `merge_after_escalation` orders.

| | |
|---|---:|
| incremental withholds (`card` → `record`) | 18 |
| subjects independently confirmed on `origin/main` | **18 / 18** |
| **withheld wrongly (subject not delivered)** | **0** |

Across all 31 withholds, two subjects are unconfirmed by git —
`qa-fail-84f92cebcf4fe498` and `qa-fail-b2537204d4a9b6dd`. Both are `done` on
the board, and a `qa-fail-*` id is not named in the commit that fixes it, so
`landed_check` reporting `landed: false` there is the control's own known limit
(it is fail-open by design), not evidence of a wrong withhold. Both are in the
`record → record` bucket — already records at seeding, and not this gate's
doing.

### Against the 1.63% this repo calls refusing routine work

Two denominators, because they answer different questions:

* **0.28% of all dispatches** — 18 withholds against the 6,528 recorded
  scheduler dispatches `dispatch_admission` surveys. Well under 1.63%.
* **45% of the population it is scoped to** — 18 of the 40 promoted detector
  cards. High, and correctly so: a detector card exists *because* the watcher
  gave up on a PR, and the most common thing that happens to a stuck PR is that
  it gets unstuck.

A 100% correct control over the incremental population is why this ships
ENFORCING rather than advisory (`dispatch_admission` ships advisory at 88.2%
correct). `ICDEV_PROMOTION_GATE=report` derives the verdict and withholds
nothing; `=off` skips the read.

## 3. The SECOND half — a `land.py` subject writes no `pr_watcher.merge`
(acceptance criterion 4)

**Measured, and the answer is not what the card assumed.**

Over all 45 recorded `recovery` findings:

| | n | % |
|---|---:|---:|
| landing seen through `pr_watcher.merge` | 34 | 75.6% |
| landing seen ONLY through the merge ledger | **11** | 24.4% |
| **satisfiable by NEITHER door** | **0** | 0% |

The 11 with no post-escalation `pr_watcher.merge` row: `xrv-cost-05`,
`artifact-fresh-ee3339893b` (the one the card names), `qa-fail-5cacee65f1d03c8c`,
`fni-api-01`, `mfx-mrg-01`, `mfx-sib-03`, `kpr-stale-05`, `rmf-ui-13`,
`qa-fail-84f92cebcf4fe498`, `qa-fail-b2537204d4a9b6dd`, `task-det-920b4f1072`.

Every one of them is satisfied through the SECOND door that **autonomy-act-06
already built** — the merge ledger (`change landed on main: <task-id>` carrying
`source: kanban_merge_ledger`). So the ordering half is *not* permanently
unsatisfiable for a `land.py` subject today; it is unsatisfiable **through the
watcher door**, and act-06 closed it.

### What is left, named

`pr_watcher.merge` rows are written by `pr_watcher`'s own loop and by nothing
else. `tools/kanban/land.py` (the `--set-status <id> done --merge` door,
mfx-mrg-04) reuses `PRWatcher._auto_merge` — the `gh` invocation — but writes no
`WatcherAction`, so its landings are invisible to `merge_after_escalation`. The
same is true of the Actions auto-merge workflow (mfx-mrg-07) and a hand
`gh pr merge`.

The merge ledger covers them, but it is **a 6-hourly reflex backfill**
(`idp_delivery_events` → `tools/idp/delivery_events.sync_delivery_events`), not
a write at the moment of merge. So a subject that lands through `land.py` is
invisible to the ordering rule for **up to 6 hours**, during which this
promotion gate correctly reports CARD and a worker can still be spent. That is
the residual, and it is a latency, not a blind spot.

**Not fixed here, deliberately** — it is a change to what `land.py` writes,
which is act-04's input data, and the card says the two halves are not the same
fix. Carded separately as `autonomy-act-08`: have `land.py` (and the Actions
auto-merge workflow) emit the landing row at merge time rather than waiting for
the 6-hourly ledger sweep.

## 4. What this change does NOT touch (acceptance criterion 6)

The detector, its thresholds and its window; `summarize_recovery`'s verdict;
`_upsert_finding`; `seen_count`; `_clear_missing`; the projection row itself. A
withheld card's finding stays ACTIVE exactly as it was, and
`python -m tools.kanban.detector_findings --records` still lists it with its
reason. `tests/kanban/test_promotion_gate.py::test_the_gate_writes_nothing`
pins that: the three writer functions are rebound to raise, and the projection
row the gate was handed is asserted byte-identical afterwards.
