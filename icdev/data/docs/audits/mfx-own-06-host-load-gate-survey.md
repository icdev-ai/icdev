# CUI // SP-CTI

# Would a host-load rung before `git worktree add` have been right to refuse? NO

**Date:** 2026-09-12
**Tool:** `python -m tools.kanban.host_io --survey --days 7 [--json]`
**Follow-up to:** `mfx-own-06` (PR #2236), which named this as the open half
**Verdict:** `do_not_arm` — shipped as a survey tool, wired into nothing

---

## The question

`git worktree add` runs under a 30 s budget that may not rise and may not be
retried — both forbidden by `kph-repark-kph-repark-mfx-ci-04`, correctly. An add
takes 5.1–25.3 s on a quiet host and 34.2–44.5 s on a busy one, so a dispatch
issued into a loaded host is killed and its task **parked** by
`worktree-isolation-guard`. Three tasks were parked that way on the evening of
2026-09-11 and one needed a human to unwedge.

`mfx-own-06` added a cross-process lock, which removes the parks caused by two
dispatchers **colliding**. It does nothing for a host that is merely busy: four
of the measured kills (35.3, 37.8, 41.7, 44.5 s) had no other add in flight.

The obvious next rung is *decline to **start** an add when the host is slow*.
That is **not** the retry the dispatch site forbids — a declined task is never
dispatched and stays `scheduled`, exactly as capacity and backpressure already
leave one, so there is no failure to hide.

## Why the signal is our own log, not a system counter

Recorded because it is the first thing a reader will want to change.

| candidate | why not |
|---|---|
| `Get-Counter '\PhysicalDisk(_Total)\Avg. Disk Queue Length'` | **2–6 s per sample**, measured on this host — unaffordable on a check that runs per candidate per cycle. Windows-only, against the standing OS-agnostic rule. |
| `os.getloadavg()` | POSIX-only, and measures **CPU**, which is not the constraint: during the slow window CPU was 15% while the disk queue was 25.8. |
| our own recorded add durations | free, already written, cross-platform, and measures the thing that actually matters. |

`_create_worktree` logs `Created worktree for <id> … in 12.2s (budget 30s)` and
`git worktree add for <id> exceeded its 30s budget after 35.9s`. The budget is
stated **in the line**, so the module never imports
`tools.genesis.reflexes.kanban` — that import would be circular and would drag
13,552 lines into a cheap predicate.

## The measurement

Population: **86 adds over 7 days**, of which **13 killed** — a base kill rate of
**15.12%**.

### Hypothesis 1 — a *slow* add (≥ 70% of budget) predicts the next

| fires | rate | prevented a park | wasted a cycle | precision |
|---|---|---|---|---|
| 5 | 5.81% | **0** | 5 | **0%** |

Every refusal would have been wrong.

### Hypothesis 2 — a *killed* add predicts the next

| cooldown | fires | rate | right | wrong | precision |
|---|---|---|---|---|---|
| 60 s | 3 | 3.49% | 0 | 3 | 0.0% |
| 180 s | 11 | 12.79% | 3 | 8 | 27.3% |
| 300 s | 11 | 12.79% | 3 | 8 | 27.3% |
| 600 s | 11 | 12.79% | 3 | 8 | 27.3% |
| 1800 s | 13 | 15.12% | 3 | 10 | 23.1% |

Knowing the previous add was killed moves the odds from a **15.12%** base rate to
about **25%**, while refusing eight good adds to catch three bad ones at a
**12.79%** fire rate. `CLAUDE.md` stands a check down at **1.63%** of routine
work.

Only **3 of 13** kills follow another kill. **The kills do not cluster.**

## Conclusion

**The host's state at the last add does not predict its state at the next one.**
No parameterisation tested changes that, and tuning the threshold until it looks
good is the move this repo's survey discipline exists to prevent. Both
hypotheses were replayed through the **shipped** predicate, never a second copy
of the rule, and `--survey` re-derives every number in this document.

The tool therefore ships **wired into nothing**. A test
(`tests/kanban/test_host_io_survey.py::test_nothing_imports_host_io_as_a_dispatch_gate`)
reads the source of `should_run.py`, `backpressure.py`, `dispatch_admission.py`
and `kanban.py` and fails if any of them imports it — a behavioural test cannot
see that edit, because the gate would still "work".

Precedent: `tools/kanban/landed_dispatch_survey.py` (`kpr-fix-03`), whose own
headline is *"Would that check have been RIGHT to refuse? Surveyed; answer is
NO"*, and which is likewise report-only with no `--gate`.

## What would actually help

Make the add **cheaper**, not better-timed. The tree is ~20,200 files / 275 MB
and a worker touches a fraction of it; `--no-checkout` plus a sparse-checkout of
the paths a task needs attacks the 34–44 s duration itself rather than guessing
when to avoid it. Untested, and it owes its own measurement and its own card.

Also still open from `mfx-own-06`: a timeout **parks** rather than leaving the
task `scheduled`, so a transient spike costs a park and, on the second inside 24
hours, a repark card and a human.
