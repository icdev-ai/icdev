# Is a task's status OSCILLATING — two writers taking turns? (kpr-watch-11)

> Moved verbatim from CLAUDE.md on 2026-09-12 (xrv-docs-02). The prose is the card's own record; only the leading `#` comment markers were stripped and the command runs fenced.

```bash
python -m tools.kanban.status_churn --json
python -m tools.kanban.status_churn --window-hours 6
python -m tools.kanban.status_churn --min-returns 5
```

On 2026-08-19 cef-ui-03 flipped done<->backlog 95 times in 5.5 hours:
pr_watcher completed it (its PR had merged), the scheduler demoted it (its run
had run out of budget). EVERY INDIVIDUAL TRANSITION WAS LEGITIMATE, so no
per-move guard could see it — the board read `scheduled` throughout and the
scheduler reported `idle`. kpr-dup-09 fixed that mechanism; this detects the
SHAPE, so the next pair of writers that disagree is visible in minutes.
A RETURN is a PAIR (`A -> B` then `B -> A`), NOT "changed status a lot" — a
task progressing backlog->scheduled->in_progress->pr_opened->done produces
none. CONTESTED (2+ writers) is separated from single-writer churn and sorted
FIRST: a fight needs a rule about who owns the row, a retry loop needs a
budget. Measured live: 34 oscillating, only 3 contested — and one of those
(prop-vv-02, scheduler vs stale-reaper) nobody knew about.
SURVEYED BEFORE THRESHOLDING over 15,879 transitions: >=1 return is ROUTINE
(11.9% of tasks), >=10 is 1.09% — below the 1.63% this file already calls
refusing routine work — and still catches the 316-return cases.
UNMEASURABLE, never a clean zero, on a board with no transitions in the
window. Report only; it measures the BOARD, not a diff, so a --gate would
fail commits for a condition the committer did not cause.
