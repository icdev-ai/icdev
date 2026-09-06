<!-- CUI // SP-CTI -->
# kpr-watch-13 — the resume-delivery verdict, surveyed before it was wired

- **Card:** `kpr-watch-13` — *pr_watcher resumes are appended to a file nobody
  reads*
- **Measured:** 2026-09-06, 02:47–03:01 UTC, against the live checkout
  (`C:\AI\ICDev\.tmp\kanban\messages`) and the live PG board
- **Predecessor survey:** `docs/audits/task-det-1f22df3838-needed-a-human-resolution.md`
  (the incident that found this; landed under `task-det-1f22df3838`)
- **Verdict:** the finding fires on **100.00%** of the resume corpus, which is
  the *measurement*, not a threshold. Nothing that refuses, dispatches or
  deletes was armed, and no threshold was moved.

---

## 0. What was armed, and therefore what needed surveying

This repo's standing rule is that a check or actuator gets a fire-rate survey
before it is turned on. Three things changed, and only one of them can *fire*:

| Change | Kind | Can it refuse / dispatch / delete? |
|---|---|---|
| `tools/ci/resume_delivery.py` — the verdict + the board survey | measurement | **no** |
| `hook_compat.check_message_queue` — appends a drain receipt | recorder | **no** |
| `pr_watcher` — probes before each resume, states the split on escalation | reporting | **no** |

There is deliberately **no delivery actuator**. The card names one — a path that
re-dispatches a worker when a resume is enqueued — and explicitly says it is a
dispatch-rate change owing its own survey. It is not in this change. Building
the actuator before the measurement would have shipped a second capability
nobody could prove was consumed, which is this platform's signature defect.

What *is* surveyed below is therefore (A) the rate at which the new verdict
returns `undelivered`, (B) the cost the probe and the receipt add to the hot
paths, and (C) the measured cost of the decision recorded at the escalate
branch.

---

## A. The headline measurement — is a resume ever read?

Re-derive, from the checkout `pr_watcher` runs in:

```bash
python -m tools.ci.resume_delivery --survey
python -m tools.ci.resume_delivery --survey --json
# and, independently, straight off the disk:
ls .tmp/kanban/messages/*.jsonl | wc -l
grep -ho '"sender": "pr_watcher"' .tmp/kanban/messages/*.jsonl | wc -l
```

Three readings, each with its instant — one figure off a live board is not a
measurement, and both sides of this inequality move while the watcher polls:

| Reading (UTC) | queue files | files w/ pending | undrained `pr_watcher` msgs | lifetime `pr_watcher.resume` rows |
|---|---:|---:|---:|---:|
| 2026-09-06 ~20:4x (the incident survey) | 186 | 185 | 849 | 847 |
| 2026-09-06 02:47 | 187 | 186 | 851 | 849 |
| 2026-09-06 02:59 | 187 | 186 | **852** | **850** |

**A drain DELETES the file**, so a drain is traceless and cannot be counted
directly. It does not need to be: a board holding at least as many undrained
`pr_watcher` messages as it has ever recorded resumes cannot have drained one of
them. The *inequality* carries the argument, and every new resume widens it.

`receipt_files: 0` / `receipted_messages: 0` is reported beside it and is
explicitly **not** evidence — receipts begin with this change, so a zero there
says nothing about drains that predate it. The survey prints that note itself.

Worst offenders on the 02:59 reading (`--top`, oldest message shown):

| task | undrained | oldest |
|---|---:|---|
| `kpr-dup-03` | 15 | 2026-08-16T17:54:20Z |
| `sbx-sig-02` | 11 | 2026-08-08T05:51:28Z |
| `cch-obs-02` | 10 | 2026-08-16T20:20:31Z |
| `cef-fnd-04` | 10 | 2026-08-17T01:43:49Z |
| `mfx-sib-03` | 10 | 2026-09-04T16:58:29Z |

`kpr-dup-03` is the PR whose memory note already records *"`resume cap reached
(5/5)` = five attempts never made"*. Its fifteen messages are still on disk.

---

## B. Fire rate of the new verdict — replayed over the whole resume corpus

Every lifetime `pr_watcher.resume` audit row was replayed through the **shipped**
predicate (`resume_delivery.probe_prior_delivery`, imported — never a second
copy), with `had_prior_injection` set from that row's position in its own task's
sequence, exactly as `poll_once` sets it from `cycle > 0`.

```
corpus                 850 resume rows (2026-08-01 -> 2026-09-06 02:59 UTC)

undelivered            850   100.00%
delivered                0     0.00%
unmeasured               0     0.00%
```

**100.00% is a measurement, not a threshold.** It is high because the finding is
true of the entire corpus: every one of those messages is still on disk. There
is nothing to narrow — a rate this high would be grounds for standing a
*refusing* check down (this repo calls 1.63% "refusing routine work"), and that
is precisely why nothing here refuses. The verdict is written onto an audit row.

Note the replay cannot produce `unmeasured` from a **first** injection, because
`probe_prior_delivery` ranks the primary evidence first: a task whose queue file
still holds an earlier era's messages reads `undelivered` on cycle 1 too, and
correctly so — an unread line is an unread line whatever counter it sits under.
The `unmeasured` branches are exercised by
`tests/ci/test_resume_delivery.py::test_first_injection_has_nothing_prior_to_judge`
and `::test_an_empty_queue_with_no_receipt_is_never_delivered`, which is where
the anti-fabrication rule lives.

### Cost added to the hot paths

Measured in the same run, on this host:

| Path | Added work | Measured |
|---|---|---|
| per resume injection | one `exists()` + one read of a small JSONL file, twice (queue + receipts) | **0.112 ms** mean over 850 calls (0.095 s total) |
| per queue drain | one `mkdir(exist_ok)` + one append | **0.367 ms** mean over 200 writes |
| per escalation | one more read of the same two files | same order as the probe |

Against a ~45 s poll and a forge round-trip measured in seconds, both are noise.
Neither can fail the caller: `record_drain` is best-effort by construction and
`_probe_prior_delivery` catches everything and returns `unmeasured`. A receipt
store that is down is asserted not to break the drain
(`::test_a_receipt_failure_never_breaks_the_drain`) — a running agent must still
get its message.

---

## C. The fifth attempt's one-poll grace — the decision and its measured cost

`resume_cooldown_seconds` (600 s) spaces injections 1 → N. It does **not**
protect the last one: the escalate branch fires on the first poll where
`cycle >= max_cycles`, one poll after the final injection.

Re-derived here over every `resume cap reached` escalation in `audit_trail`,
de-duplicated per `(task_id, pr_url)` and paired with the newest preceding
`pr_watcher.resume` row for the same pair:

```
cap escalations                     153
with a preceding resume row         150   (3 have none — unmeasurable, not 0)

final resume -> escalate
  min 32.0s   p50 40.9s   p90 60.0s   max 715.1s
  within 180s   148/150 (98.7%)
  within 600s   149/150 (99.3%)
```

So the last of five "attempts" is universally declared spent **inside the very
interval the cooldown constant was written to rule out**. (The incident survey
read `146` with a preceding row against 153; the board has since taken four more
escalations. Same shape, same conclusion.)

**How often that changes an outcome is BOUNDED, NOT PROVEN:**

```
cap escalations with a later pr_watcher.merge   109/153 (71.2%)
  median time from escalation to merge          238.2 min
  merged within 60 min                          24 (15.7% of 153)
  merged within 30 min                          4  (2.6%)
```

Most are not a fifth resume finishing late. A minority might be.

### The decision: the grace is KEPT, and recorded

Holding the escalation for a full cooldown would delay **every** HITL alert by
~10 minutes to rescue a fifth attempt that, on this deployment, was never
delivered at all (§B: 100% `undelivered`). The repair is *delivery*, not a
longer wait for a message nobody reads — and moving `RESUME_COOLDOWN_SECONDS` or
`max_resume_cycles_per_task` to quieten a symptom is what the card explicitly
forbids. **More undelivered messages is not more attempts.**

What changes is that the grace stops being an accident of branch order. It is
now a documented decision at the branch, and its cost is **measured on every
escalation** rather than argued about once: `final_attempt_grace_seconds` is
written onto each `pr_watcher.escalate` row, so a future card can re-derive this
distribution from the live series instead of replaying history. If delivery is
ever wired and this number starts mattering, the evidence will already exist.

---

## D. What the audit row says now

| | before | after |
|---|---|---|
| `resume` reason | `injected resume context` | `resume enqueued; prior injections: undelivered (N pr_watcher message(s) still unread in the queue)` |
| `resume` fields | — | `delivery`, `delivery_detail` |
| `escalate` reason | `resume cap reached (5/5) — manual intervention required` | `… — manual intervention required; NONE of the 5 injection(s) were ever read (5 still unread in the queue)` |
| `escalate` fields | — | `delivery`, `delivery_detail`, `final_attempt_grace_seconds` |

The HITL alert keeps the `resume cap reached (n/m) after <cause>.` prefix that
`tools/kanban/hitl_alert_view.py` parses; the delivery clause is appended after
it, so every existing reader extracts exactly what it did before.

`delivered` is returned **only** on positive evidence — a receipt naming a real
drain. An empty queue with no receipt is `unmeasured`, because a `.tmp` sweep, a
pre-receipt reader, and a survey run from a worktree with its own empty `.tmp`
all produce that same shortfall. Reading it as `delivered` would be this card's
own defect: a reduction asserting more than its data supports.

---

## E. Not done here, and why

* **No delivery actuator.** Naming it precisely: something that, on an
  `undelivered` verdict, re-dispatches the task's worker so the queue is
  drained. That is a dispatch-rate change and needs its own fire-rate survey —
  and, on this board, would fire on 100% of resumes on day one.
* **`_emit_wake_events` is still unmeasured.** It is a second, independent
  channel (`emit_pr_state` promotes a registered wake `pending -> due`) and its
  own docstring says *"an empty `promoted` is the normal case — most events have
  no listener"*. This survey measures the **message queue**, which is the
  channel `_send_resume` writes and the one the `resume` audit row is emitted
  for. Do not count wake promotion as the answer without measuring `promoted`.
* **The 852 messages already on disk are not deleted.** They are the evidence.
  Nothing in `resume_delivery.py` may delete anything, asserted by AST
  (`::test_the_module_has_no_actuator`).

## F. Re-derivation

```bash
python -m tools.ci.resume_delivery --survey --json      # §A, live
python -m tools.ci.resume_delivery --task kpr-dup-03    # one task's verdict
python -m pytest tests/ci/test_resume_delivery.py -q    # the rules, pinned
```

§B and §C were produced by replaying `audit_trail` through the shipped
predicate; the driver is reproduced inline above in prose and is a scratch
script under `.tmp/` — disposable, and deliberately not cited as the way to
re-derive a published number. The two SQL corpora are
`action = 'pr_watcher.resume'` and `action = 'pr_watcher.escalate'` filtered to
reasons containing `resume cap reached`, de-duplicated per `(task_id, pr_url)`.
