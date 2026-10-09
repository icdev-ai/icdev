<!-- CUI // SP-CTI -->
# autonomy-act-04 — a `needed_a_human` finding whose subject already merged is a RECORD

- **Card:** autonomy-act-04, filed from `docs/audits/task-det-4986dd5bf3-needed-a-human-resolution.md`
- **Measured:** 2026-09-05, live PG board, 54,914 lifetime `pr_watcher.escalate` /
  `pr_watcher.merge` audit rows
- **Re-derive:** `python -m tools.kanban.detector_findings --records [--json]`

## The detector is not wrong

`summarize_recovery` (rem-hyg-16) gives `escalate` priority over any later
`merge`, and that is **correct**: counting a post-escalation merge as a recovery
is exactly the inflation it exists to refuse. Nothing in this change touches
that verdict, its threshold or its window — `tools/dashboard/recovery_summary.py`
is byte-identical.

The objection is one layer up, in `tools/kanban/detector_findings.py`: a
`needed_a_human` finding is filed as a **dispatchable card** even when it is a
true statement about the past with no remaining work. A moot card dispatches a
worker session against a delivered subject and a cleared finding, and **such a
dispatch cannot go RED** — there is nothing left to change.

## The rule, as shipped

A **conjunction** of two pieces of primary data. No elapsed time, no threshold:

> a `pr_watcher.merge` row **newer than** the newest `pr_watcher.escalate` row
> for the subject, **AND** the subject task closed on the board

`merge_after_escalation()` answers the first half from the audit rows the
detector itself read; `card_disposition()` combines it with one `kanban_tasks`
status read. "Closed" is `recovery_summary.CLOSED_STATUSES`, **imported**, never
respelled — a second copy of "what counts as closed" is the defect the project
cards carried until 2026-08-28.

**The finding is still RECORDED.** `_upsert_finding` runs unchanged, `seen_count`
still rises, `_clear_missing` still clears on a measurable run that no longer
reports it. Only the **card** is withheld, and every record is surfaced on the
run report (`records[]`, with both stamps and the merge's own reason), in the
human report as a `RECORD` line, and through `--records`.

**Every unknown keeps the card.** An unreadable order, a subject that is not on
the board, a subject still in flight, an unreadable board, a non-recovery
detector: all return `card`. A card filed against delivered work costs a wasted
dispatch; an escalation silently demoted to a record costs the one signal the
watcher has for "I gave up". Those are not the same price.

`merge_after_escalation` returns `superseded: None`, **never `False`**, when
there is no readable escalation to order against. "I cannot tell" and "the
escalation still stands" send a caller to opposite places.

## The survey — all 25 recorded `recovery` findings

Re-derived with the **shipped** predicate (`dispositions()` calls
`merge_after_escalation` and `card_disposition` themselves, so there is no second
copy of the rule to drift). `consume` orders against its own 24h window; the
survey orders against pr_watcher rows **lifetime**, which is what a finding
projected two weeks ago needs. Same functions either way.

**16 record / 9 card of 25 (64.0%).** Subject statuses: 22 `done`, 3 `pr_opened`
— **none abandoned or stuck**.

| subject | finding | subject | disposition | why |
|---|---|---|---|---|
| `autonomy-lrn-01` | cleared | `done` | **record** | merge 2026-08-21T12:13:07 > escalate 2026-08-21T07:39:16 |
| `flx-airgap-01` | active | `done` | **record** | merge 2026-09-05T05:24:01 > escalate 2026-09-05T05:07:37 |
| `fni-api-01` | active | `done` | card | the escalation is the newer of the two rows |
| `kpr-stale-05` | cleared | `done` | card | the escalation is the newer of the two rows |
| `mfx-boot-01` | cleared | `pr_opened` | card | the escalation is the newer of the two rows |
| `mfx-mrg-01` | cleared | `pr_opened` | card | the escalation is the newer of the two rows |
| `mfx-sib-02` | active | `done` | **record** | merge 2026-09-05T20:26:16 > escalate 2026-09-05T06:17:55 |
| `mfx-sib-03` | cleared | `pr_opened` | card | the escalation is the newer of the two rows |
| `qa-fail-5f7cf03a0b0a4351` | cleared | `done` | **record** | merge 2026-08-23T02:10:27 > escalate 2026-08-22T02:15:07 |
| `qa-fail-84f92cebcf4fe498` | cleared | `done` | card | the escalation is the newer of the two rows |
| `qa-fail-b2537204d4a9b6dd` | cleared | `done` | card | the escalation is the newer of the two rows |
| `rem-hyg-17` | cleared | `done` | **record** | merge 2026-08-21T01:12:20 > escalate 2026-08-21T01:11:38 |
| `rmf-ui-03` | cleared | `done` | **record** | merge 2026-09-04T01:21:09 > escalate 2026-09-03T23:53:56 |
| `rmf-ui-07` | cleared | `done` | **record** | merge 2026-09-03T23:04:46 > escalate 2026-09-03T21:02:21 |
| `rmf-ui-08` | cleared | `done` | **record** | merge 2026-09-04T11:45:37 > escalate 2026-09-04T02:02:40 |
| `rmf-ui-10` | cleared | `done` | **record** | merge 2026-09-04T00:59:26 > escalate 2026-09-04T00:03:45 |
| `rmf-ui-11` | cleared | `done` | **record** | merge 2026-09-04T01:46:42 > escalate 2026-09-04T00:13:10 |
| `rmf-ui-12` | cleared | `done` | **record** | merge 2026-09-03T18:08:20 > escalate 2026-09-03T12:22:44 |
| `rmf-ui-13` | cleared | `done` | card | the escalation is the newer of the two rows |
| `rmf-ui-16` | cleared | `done` | **record** | merge 2026-09-04T11:28:35 > escalate 2026-09-04T00:25:25 |
| `task-42a17b8956` | cleared | `done` | **record** | merge 2026-08-25T04:10:37 > escalate 2026-08-23T03:16:10 |
| `task-c49fb2727d` | cleared | `done` | **record** | merge 2026-08-20T22:19:42 > escalate 2026-08-20T10:02:14 |
| `task-det-920b4f1072` | cleared | `done` | card | the escalation is the newer of the two rows |
| `task-det-cd1d099fff` | cleared | `done` | **record** | merge 2026-09-05T18:33:59 > escalate 2026-09-04T12:34:43 |
| `xit-decl-03` | cleared | `done` | **record** | merge 2026-08-21T15:39:28 > escalate 2026-08-21T06:45:14 |

**The merge that answered the escalation was the watcher's OWN in 12 of the 16
records** — `auto-merge ok` (11) and one `auto-merge ok; ignored non-required
failing check(s): E2E …`. The other 4 read `PR already merged`, i.e. it landed
through some other door. The escalation asked for a human and, in three cases
out of four, no human came.

### THE BOARD MOVED DURING THE SURVEY, and it is stated rather than smoothed

An earlier reading the same afternoon gave **15 record / 10 card of 25**:
`mfx-sib-02` was `pr_opened` at 20:26 UTC and `done` by 20:36. The rule reacted
correctly — the merge row landed, the subject closed, the finding became a
record. A single figure quoted from one reading of a live board is not a
measurement; both are given.

### The three cards the source audit calls moot

All three resolve to **record** under the shipped predicate:
`task-det-8a4ca3352d` (rmf-ui-08), `task-det-6ca8c2dd3b` (rmf-ui-11),
`task-det-5631a471c7` (rmf-ui-16).

### The live ones still seed

Every subject in flight keeps its card: `mfx-boot-01`, `mfx-mrg-01`,
`mfx-sib-03` are all `pr_opened` and all `card`. Verified end to end with a true
dry run of `consume` (recovery runner only, `seed=False`) against the live board
on 2026-09-05: 4 findings in the window, **2 filed as records, 1 new card
planned**, nothing written.

## What this deliberately does NOT catch, measured

**Six findings keep a card although their subject is closed**, because there is
no `pr_watcher.merge` row for them at all: `fni-api-01`, `kpr-stale-05`,
`rmf-ui-13`, `qa-fail-84f92cebcf4fe498`, `qa-fail-b2537204d4a9b6dd`,
`task-det-920b4f1072`. Spot-checked against the raw audit rows — `rmf-ui-13`,
`kpr-stale-05` and `fni-api-01` each carry `escalate`, `resume`, `rebase_failed`
and `wait` rows and **zero** `merge` rows. Those subjects landed through a door
the watcher does not record.

This is the stated rule working as specified, not a gap that was overlooked. The
alternative — "the subject is closed" **alone** — drops the ordering half of the
conjunction, which is the only thing that distinguishes "the escalation was
answered" from "the board moved on for unrelated reasons". Widening it is a
separate card with its own survey; it is **not** done here by quietly relaxing
the predicate.

## Cost

Zero extra queries in the reflex path: `consume` orders against the rows
`recovery_rows()` already fetched, and reads one `kanban_tasks` row per recovery
finding (typically ≤ 5 per cycle). The `--records` surface and this survey read
the two ordering actions lifetime — 54,914 rows, **0.18 s** measured on this
host.

## Not changed

`tools/dashboard/recovery_summary.py`, the 24h window, the `escalate`-outranks-
`merge` verdict, `earliest_clear_at` / `held_closed_early` (task-f05d2bc8d1),
`_clear_missing`, the card cap, and the `suggested` seed status. No migration:
the disposition is **re-derived every run from primary data** and never
persisted — a stored verdict about an order goes stale the moment either row's
successor is written.
