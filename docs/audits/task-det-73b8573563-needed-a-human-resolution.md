<!-- CUI // SP-CTI -->
# task-det-73b8573563 — `needed_a_human` finding for dwr-fid-03, resolved

- **Task:** task-det-73b8573563 (filed by `detector_findings_reflex`, detector
  `recovery` / rem-hyg-16; finding `73b8573563ae33c5`, seen 2x, `card_count` 1)
- **Subject:** dwr-fid-03 — PR #2183, five `resume` cycles, escalated
- **Date measured:** 2026-09-09, against the live PG board (`icdev`, measured)
  and the forge

## Verdict

**The escalation was CORRECT and a human ANSWERED it.** This is a POSITIVE
CONTROL, and an unusually clean one: the branch had TWO causes standing between
it and `main`, and *both* are named verbatim in the card's own advice text and in
rem-hyg-16's module docstring — a **host-dependent path comparison** and a
**conflict no rebase could clear**. Neither is reachable by an LLM resume, and on
this task the resumes were never delivered at all, so nothing ever read one.

PR #2183 is **MERGED** (2026-09-08T21:07:29Z, merge commit `28f5fcdeb`, a real
two-parent merge — the watcher merges `--merge`), the subject is `done`, its
deliverables are on `origin/main`, and the lease is free. Nothing is outstanding.

## The derivation still reports, and that is a CLOCK, not a defect

Re-derived first, as the card demands, at **2026-09-09T02:24:13Z**:

```
python - <<'EOF'
from tools.awareness.claims import _recovery_rows
from tools.dashboard.recovery_summary import summarize_recovery
print([e for e in summarize_recovery(_recovery_rows(), limit=10_000) if e['task_id'] == 'dwr-fid-03'])
EOF
# -> [{'task_id': 'dwr-fid-03', 'attempts': 5, 'kind': 'resume',
#      'at': datetime.datetime(2026, 9, 8, 9, 52, 2, 993835),
#      'escalated': True, 'merged': True, 'outcome': 'needed_a_human', ...}]
```

Note `merged: True`. `summarize_recovery` gives `escalate` priority over any
later `merge` — deliberately, because that merge is the human the escalation
asked for — so the entry leaves ONLY when its newest counted ATTEMPT row falls
out of the 24h window. That row is the fifth resume at `09:52:02.993835`, so:

| fact | value |
|---|---|
| `earliest_clear_at` (newest counted attempt + `detectors.recovery.window_hours` = 24) | **`2026-09-09T09:52:02.99Z`** |
| card scheduled | `2026-09-09T02:03:58.847Z` — **7h48m BEFORE** the clear time |

This card therefore **cannot** satisfy its own literal acceptance criterion at
dispatch, and closing it now is safe for the reason #2057 (`fb989f6ad`,
`earliest_clear_at`, task-f05d2bc8d1) exists and is on `main`: a terminal card
before that instant is **HELD** (`held_closed_early` — the finding stays active
on this task_id and `seen_count` rises), not re-filed as `-r2`. The first
MEASURABLE `detector_findings_reflex` cycle after 09:52:02Z will find no entry
for `dwr-fid-03` and mark the finding `cleared`. It cannot be re-attempted in
between: the subject is terminal, so `pr_watcher` can no longer see it and can
write no further attempt row.

Nothing about the detector, its threshold or its window was touched.

## The ledger

`pr_watcher` rows naming the subject, lifetime: **170 `wait`, 8 `rebase_failed`,
8 `union_refused`, 5 `resume`, 2 `merge`, 1 `escalate`** — and no `rebase`, no
`ci_retrigger`, no refunds.

| at (UTC) | action | cls | reason |
|---|---|---|---|
| 08:43:28 → 09:02:58 | `wait` x21 | `pr_opened` | CI still running |
| 09:03:54 | `resume` | `ci_failed` | cycle 1 — prior injections: **unmeasured** (first injection) |
| 09:13:57 | `resume` | `ci_failed` | cycle 2 — **undelivered** (1 unread) |
| 09:24:47 | `resume` | `ci_failed` | cycle 3 — **undelivered** (2 unread) |
| 09:32:38 → 09:41:10 | `wait` x16 | `error` | `gh pr view` failed: GraphQL API rate limit already exceeded |
| 09:41:59 | `resume` | `ci_failed` | cycle 4 — **undelivered** (3 unread) |
| 09:52:02 | `resume` | `ci_failed` | cycle 5 — **undelivered** (4 unread) |
| 09:53:01 | `escalate` | `ci_failed` | **resume cap reached (5/5) — manual intervention required; NONE of the 5 injection(s) were ever read (5 still unread in the queue)** |
| 09:59:21 → 10:19:13 | `union_refused` + `rebase_failed` x6 | `merge_conflict` | `CLAUDE.md`, `icdev/data/claude_bootstrap/CLAUDE.md` |
| 20:35:24, 20:36:41 | `union_refused` + `rebase_failed` x2 | `merge_conflict` | same two files |
| 21:07:29 | `merge` | `done` | **auto-merge ok** |
| 21:08:19 | `merge` | `done` | PR already merged |

Every resume ran on `ci_failed` — the branch had a red check, not a stale base.
The escalation fired **58.02s** after the fifth resume, carried on the row itself
as `final_attempt_grace_seconds` — the kpr-watch-13 shape (p50 40.9s, 98.7%
within 180s), well inside the 600s `RESUME_COOLDOWN_SECONDS`.

The card's quoted `reason` is the last ATTEMPT's, not the escalation's. The
escalate row carries `delivery: undelivered` and
`delivery_detail: 0 delivered, 5 still unread, 0 unaccounted of 5 injection(s)`.

## The five resumes were never read

```
python -m tools.ci.resume_delivery --task dwr-fid-03
  dwr-fid-03: undelivered -- 5 pr_watcher message(s) still unread in the queue
```

Re-derived from the filesystem, sharing no code with `queue_message`. So
`resume cap reached (5/5)` is again five attempts **that were never made** — the
kpr-watch-13 mechanism, reproduced exactly. Whatever a resume might have been
able to do about the red check, it was never given the chance.

## Cause 1 — a host-dependent path comparison (the red check)

Fixed by hand on the branch at **20:40:18Z**, `310db5295`
*"fix(dic): the client filename's suffix must not depend on which OS is serving"*.
Its own commit body is the evidence, and it states the class outright:

> Test Shard 4 and the red-first gate both failed on
> `test_a_client_filename_cannot_put_a_path_component_on_disk[x.\..\y]`, and it
> **passed locally — which IS the defect, not an inconvenience.**
>
> `pathlib.Path(name).suffix` only treats a separator as a boundary if the HOST
> uses it: `PurePosixPath(r"x.\..\y").suffix == ".\y"` (backslash survives on
> Linux), `PureWindowsPath("a.pdf/../evil").suffix == ""` (Windows splits on
> both). So each platform lets the OTHER platform's separator through, and that
> value is handed straight to `tempfile.NamedTemporaryFile(suffix=...)` and to
> the retained original's name. The runner and the containers are Linux; the
> assertion was written on Windows.

`ingest_guard.safe_suffix` now takes the last component under BOTH conventions
and requires the result to look like an extension, wired at both sites that put a
client-supplied suffix on disk (`blueprint.py`'s temp file, `originals.py`'s
retained copy). 7 files, +92/-14, mirror twins updated.

This is **verbatim** the example rem-hyg-16's docstring gives for why an LLM
resume cannot address this class ("a 16-commit-stale branch and a host-dependent
`as_posix()` path comparison, neither of which an LLM resume can address, because
the branch it is asked to repair looks fine locally"). A worker resumed into this
worktree would have run the suite on Windows and seen green.

## Cause 2 — a CLAUDE.md sibling train, and then a rebase that could not hold

All 8 `union_refused` rows are identical:

```
refused: files=['CLAUDE.md', 'icdev/data/claude_bootstrap/CLAUDE.md']
rules=[] verifiers=[] -- undeclared: CLAUDE.md matches no union_resolver.files entry
```

That refusal is **correct**: `CLAUDE.md` is prose, not a line-oriented table, and
union-merging it would be wrong. The pair is the mfx-ci-04 bootstrap-parity twin
— every card that edits `CLAUDE.md` must regenerate the packaged copy, so both
files move together and both collide together.

`origin/main` took **13 commits touching `CLAUDE.md` on 2026-09-08**, five of
them inside this task's escalation window:

| landing (UTC) | commit | what |
|---|---|---|
| 09:53:43 | `00c385c01` | dwr-fid-02 — **42s after the escalation** |
| 09:57:19 | `56780545b` | dwr-anchor-06 (#2182) |
| 09:59:01 | `583875149` | dwr-ev-02 (#2180) |
| *09:59:21* | — | *first `rebase_failed`, **20s later*** |
| 20:32:34 | `238d58375` | dwr-ev-03 (#2179) |
| 20:33:53 | `1fc9da025` | dwr-fid-02 (#2186) |
| *20:35:24* | — | *seventh `rebase_failed`, **91s later*** |

20s and 91s: the shared-file conflict train signature (33s-279s, median 142s)
measured 12 of 12 on the `rmf-ui-*` epic, now reproduced on `dwr-*`.

**The two blocks of `rebase_failed` have DIFFERENT causes and must not be read as
one loop.** The first six (09:59-10:19) are the plain sibling conflict on a
still-linear branch. The last two (20:35, 20:36) came *after* a human had already
resolved it — and failed anyway, because `git rebase` replays the FEATURE commit
and flattens the merge commit that carried the resolution away:

```
rebase onto origin/main hit conflicts: Could not apply a205f29c0...
  # feat(dic): a degraded render says so, and the ingest gets its sandbox decision (dwr-fid-03)
```

A branch whose conflict was settled with a MERGE COMMIT can never be rebased by
the watcher — the resolution is discarded on every attempt and the same conflict
returns. The only exit is to merge `main` IN again, which is exactly what the
operator then did.

## Who repaired it

Three hand-authored commits on `kanban/dwr-fid-03`, all AFTER the 09:53:01Z
escalation:

| commit | at (UTC) | what | second parent |
|---|---|---|---|
| `40d283a3c` | 20:04:44 | Merge `origin/main` into the branch | `39d6655e6` (#2181 dwr-ws-01, 10:17:20Z) |
| `310db5295` | 20:40:18 | the host-dependent suffix fix | — |
| `29dac7848` | 20:51:42 | Merge `origin/main` in, **second pass** | `1fc9da025` (#2186 dwr-fid-02, 20:33:53Z) |

The first merge was already stale when it landed — its second parent is an
`origin/main` from 10:17:20Z, ~9h47m old — and #2179/#2186 then landed on
`CLAUDE.md` 28 minutes after it, which is what the 20:35/20:36 `rebase_failed`
rows were reacting to. The second pass at 20:51:42Z picked up #2186 and held.
`pr_watcher` merged **15m47s later** at 21:07:29Z (`auto-merge ok`), and marked
the subject `done` 50s after that.

Note the operator merged the SIBLING's branch too, 66 seconds after this one
(`981844beb`, dwr-fid-02, 20:05:50Z) — one human clearing a train, not one
branch. Do not read this class as "nobody did anything" without reading the
branch's own history: `28f5fcdeb` has two parents, so all three commits are on
`main`.

## Found on the way: the panel and the detector read DIFFERENT rows (autonomy-act-05)

Reported, **not fixed here** — the card forbids touching the detector, and says a
detector defect is a separate card with the survey that proves it. Seeded as
`autonomy-act-05`; this section is that survey.

`recovery_summary.AUDIT_ACTIONS` exists precisely to stop this. Its own comment
says so:

> The `audit_trail` `action` values the panel's query must fetch — **exported so
> the SQL in app.py and the classifier here cannot drift apart again.** They did:
> the query fetched four action names and the classifier knew two of them.

It is 8 values (`resume`, `rebase`, `rebase_failed`, `ci_retrigger`,
`resume_refund`, `rebase_refund`, `escalate`, `merge`). **Exactly one caller
reads it** — `tools/dashboard/app.py:10254`, the Home panel. The two readers that
FILE AND CLEAR CARDS each hard-code the pre-rmf-disc-01 four-value literal
instead:

- `tools/kanban/detector_findings.py:592` — `recovery_rows`
- `tools/awareness/claims.py:181` — `_recovery_rows` (the card's own derivation)

So the widening that taught the classifier about `rebase_failed` and
`ci_retrigger` reached the panel and never reached the detector. Measured on the
live board 2026-09-09T02:35Z, same instant, same window:

| reader | actions | rows | tasks | dwr-fid-03 `attempts` | `kind` | `earliest_clear_at` |
|---|---|---|---|---|---|---|
| Home panel (`AUDIT_ACTIONS`) | 8 | 103 | **10** | **13** | `rebase_failed` | 2026-09-09**T20:36:41Z** |
| detector / claim (literal) | 4 | 66 | **9** | **5** | `resume` | 2026-09-09**T09:52:02Z** |

Three consequences, all live today:

1. **This card's own title understates its subject by 8 attempts** — "escalated
   after 5 attempt(s)" against the 13 a human reads on the panel.
2. **`earliest_clear_at` moves by 10h44m**, so the hold that keeps a `-r2` from
   being filed is 10 hours shorter than the panel's evidence supports.
3. **One task is visible to the panel and invisible to the detector** — 10 vs 9 —
   i.e. a task attempted only by `rebase_failed`/`ci_retrigger` rows can be
   escalated and never draw a card at all.

Which reading is CORRECT is that card's to settle, not this record's: a
`rebase_failed` IS an attempt by the classifier's declared vocabulary, but the
detector's window semantics were surveyed against the narrow set. The finding
here is only that **two readers of one measurement disagree**, which is the exact
defect `AUDIT_ACTIONS` was exported to prevent. It does not disturb this card's
verdict — dwr-fid-03 escalated, needed a human, and got one, on either row set.

## State at close

| fact | value |
|---|---|
| subject board status | `done` (2026-09-08T21:08:19.795Z, 50s after the merge) |
| PR #2183 | **MERGED** 21:07:29Z, merge commit `28f5fcdeb` (two parents) |
| deliverables on `origin/main` | `tools/document_intelligence/reading_pane.py`, `.../ingest_guard.py` — both PRESENT |
| lease | free — `restore_acts.py --apply reap_dead_lease --target dwr-fid-03 --dry-run` -> *"no live lease — nothing to reap"* |
| derivation | **still reports** — clears at `2026-09-09T09:52:02.99Z`, 7h48m after this card was scheduled |
| `detector_findings` row `73b8573563ae33c5` | `active`, seen 2, first seen 2026-09-08T14:21:33, last seen 2026-09-08T20:45:26 — HELD at close (`held_closed_early`), clears on the first MEASURABLE cycle after 09:52:02Z |
| `--records` disposition | **`record`** — *"a pr_watcher.merge at 2026-09-08T21:07:29 landed AFTER the escalation at 2026-09-08T09:53:01, and the subject is `done`: nothing is left to land"* |

The autonomy-act-04 disposition is worth quoting on its own: this finding reads
`record`, not `card`, **today**. It read `card` when it was filed at 14:21:33Z,
because the merge did not happen until 21:07:29Z — 6h46m later. The rule worked
exactly as written; the card was correct at dispatch and moot by the time it ran.

Nothing was outstanding, nothing was fixed here, and nothing needed to be. This
record is the artifact.

## Running tally for this card class

**22 landed records** including this one (`ls docs/audits/task-det-*-needed-a-human-resolution.md`,
counted on `origin/main` at 2026-09-09T02:40Z). Eight sibling recovery findings
are `active` alongside this one and several are being worked concurrently, so
treat any split of that total into moot/outstanding as a reading at an instant,
not a standing figure — re-derive with
`python -m tools.kanban.detector_findings --records`, which states each subject's
disposition and the two timestamps it ordered.

What this instance adds to the class: the first recorded case where the
escalation's cause was a **red check from a host-dependent path comparison** —
green in the worktree, red on the Linux runner — which is the one shape a resumed
worker is structurally guaranteed to miss even if the resume HAD been delivered.
Re-derive, read the branch's own history, and read the failing check's platform,
before concluding the escalation was wrong.
