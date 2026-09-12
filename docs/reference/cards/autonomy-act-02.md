# Consume the detectors nobody runs — and file each finding ONCE, with its evidence (autonomy-act-02)

> Moved verbatim from CLAUDE.md on 2026-09-12 (xrv-docs-02). The prose is the card's own record; only the leading `#` comment markers were stripped and the command runs fenced.

```bash
python -m tools.kanban.detector_findings --json          # run status_churn + born_red_survey + recovery_summary, seed cards
python -m tools.kanban.detector_findings --dry-run       # run the detectors; write NOTHING (no rows, no cards)
python -m tools.kanban.detector_findings --list          # browse the projection (--detector, --status active|cleared)
python -m tools.kanban.detector_findings --stats         # per-detector denominator: never_ran | unmeasurable | clean | findings
python -m tools.kanban.detector_findings --records       # card vs RECORD, re-derived; runs no detector
python tools/genesis/daemon.py --reflex detector_findings_reflex   # the 6h reflex, once, through the daemon
```

THE DEFECT. status_churn (kpr-watch-11), born_red_survey (rem-hyg-14) and
recovery_summary (rem-hyg-16) were each built because a human found the defect
BY HAND, and each then sat imported by NOBODY on any runtime path — the
declared-but-unconsumed defect reaching the self-observation layer. This builds
NO detector; it runs the three that exist on the Genesis cadence.
A CARD CARRIES ITS DERIVATION: the detector's own row verbatim, the exact
command that re-derives it, and what "fixed" looks like. Never a bare alert.
DEDUPE ON THE FINDING, NOT THE RUN: one `detector_findings` row per
(detector, subject, fingerprint), upserted with `seen_count` — the cef-ui-02
projection shape. A card is seeded on FIRST sight and again only if the finding
RECURS after its card closed (`-r2`, `card_count`); `idempotency_key` on the
spec is the second lock inside create_tasks. Cards land in `suggested` (HITL
quarantine) by default — `seed_status` in args/genesis_config.yaml.
A RECORD IS NOT A DISPATCHABLE CARD (autonomy-act-04). summarize_recovery is
RIGHT that `escalate` outranks any later `merge` -- counting a post-escalation
merge as a recovery is the inflation rem-hyg-16 exists to refuse, and NOTHING
here changes that verdict, its threshold or its window (recovery_summary.py is
byte-identical). The objection is one layer up, about SEEDING: a
`needed_a_human` finding whose subject has since merged and gone terminal is a
true statement about the PAST, and a card for it dispatches a worker session
against a delivered subject -- a dispatch that CANNOT go RED, because there is
nothing left to change. THE RULE IS A CONJUNCTION of two pieces of primary
data, no elapsed time and no threshold: a `pr_watcher.merge` row NEWER than the
newest `pr_watcher.escalate` row for the subject, AND the subject task CLOSED
on the board (recovery_summary.CLOSED_STATUSES, IMPORTED, never respelled).
MEASURED over all 25 recorded `recovery` findings, live board 2026-09-05:
16 record / 9 card; every subject `done` (22) or `pr_opened` (3), NONE
abandoned or stuck; and the merge that answered the escalation was the
WATCHER'S OWN (`auto-merge ok`) for 12 of the 16 -- the escalation asked for a
human and no human came. An earlier reading the same afternoon read 15/10:
mfx-sib-02 merged between them, so BOTH are quoted -- one figure off a live
board is not a measurement.
THE FINDING IS STILL RECORDED. _upsert_finding, seen_count and _clear_missing
are untouched; only the CARD is withheld, and every record is SURFACED on the
run report (`records[]`, both stamps and the merge's own reason), as a RECORD
line in the human report, and through `--records`.
EVERY UNKNOWN KEEPS THE CARD: an unreadable order, a subject not on the board,
a subject still in flight, an unreadable board. `superseded` is None -- NEVER
False -- with no escalation to order against. A wasted dispatch and a silently
demoted escalation are not the same price.
NOT CAUGHT, ON PURPOSE and measured: 6 findings keep a card although their
subject is closed, because the subject carries NO `pr_watcher.merge` row at all
(it landed through a door the watcher does not record). "The subject is closed"
ALONE drops the ordering half, which is the only thing separating "the
escalation was answered" from "the board moved on". Widening it is a separate
card with its own survey.
NO MIGRATION: the disposition is re-derived from primary data every run and
never persisted -- a stored verdict about an ORDER goes stale the moment
either row's successor is written.
Survey: docs/audits/autonomy-act-04-record-not-card-survey.md
A CARD CLOSED EARLY IS NOT A RECURRENCE (task-f05d2bc8d1). A recovery finding
is a window over audit rows and `escalate` outranks any later merge, so it
CANNOT clear before last-attempt + window_hours whatever a human does; a
`done` card inside that window used to read as "did not hold" and draw a
DISPATCHED -r2 for a subject already delivered — 3 of 3 -r2 recovery cards on
the live board (2026-09-04). A Finding may now carry `earliest_clear_at`
(recovery only); a terminal card before it is HELD (finding stays active on
its task_id, seen_count rises, `held_closed_early` reported) and -rN is filed
only by a MEASURABLE run after that time, or when a `cleared` finding
reappears. born_red / status_churn carry no such time and keep the plain rule.
UNMEASURABLE CLEARS NOTHING: an idle board, an unmigrated baseline, an empty
audit window each report that they could not measure, and only a MEASURABLE
run that no longer reports a finding marks it `cleared`. `detector_runs` is
the denominator keeping never_ran / unmeasurable / clean apart.
Bounded per run (`max_cards_per_run`, default 6, worst-first) and the bound is
REPORTED as `cards_deferred`, never silent. Measured on the live board
2026-08-21: 0 oscillating, 3 born-red, 3 needed_a_human -> 6 cards.
Migration 20260821050135. Seeds through task_factory.create_tasks, never a raw INSERT.
