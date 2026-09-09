# CUI // SP-CTI

# task-det-d4cb07d1e1 — [NEEDED-A-HUMAN] `dwr-ev-03`: resolution record

**Detector:** `recovery` (rem-hyg-16, `summarize_recovery`) — finding
`d4cb07d1e12d8a16`, subject `dwr-ev-03`, first seen 2026-09-08T14:21:33Z.
**Verdict: the escalation was CORRECT and a human answered it.** `escalate`
outranking the later `merge` is right here, so nothing about the detector is
wrong and nothing in it was touched. Unlike the twelve records before it this
one is **NOT moot at dispatch** — the derivation still reports the subject, by
construction, and the section "Why the acceptance criterion cannot be met at
dispatch" says exactly why, and when it will be.

## The derivation, re-run before acting

```
python - <<'EOF'
from tools.awareness.claims import _recovery_rows
from tools.dashboard.recovery_summary import summarize_recovery
print([e for e in summarize_recovery(_recovery_rows(), limit=10_000) if e['task_id'] == 'dwr-ev-03'])
EOF
```

At 2026-09-09T02:35:13Z (68 rows in the 24h window) it returns **one entry**:

```
{'task_id': 'dwr-ev-03', 'attempts': 3, 'kind': 'resume',
 'reason': 'resume enqueued; prior injections: undelivered (2 pr_watcher message(s) still unread in the queue)',
 'at': datetime(2026, 9, 8, 10, 11, 48, 715969), 'escalated': True,
 'merged': True, 'board_status': None, 'outcome': 'needed_a_human'}
```

`merged` has flipped `False` → `True` since the card was filed — the merge
landed at 20:33:55Z, six hours after it. The verdict is unchanged, and must be:
a merge after an escalation is the human the escalation asked for.

Corroborating state: board `dwr-ev-03` = `done` (2026-09-08T20:33:55Z);
`restore_acts --plan` lists **no** `reap_dead_lease` candidate for `dwr-ev-03`
and `.tmp/coordination/{leases,claims}/` hold nothing under that name — there
was no claim left to release.

## What actually happened (UTC)

PR [#2179](https://github.com/icdev-ai/icdev/pull/2179), head `kanban/dwr-ev-03`,
squashed onto `main` as `238d58375`. Ledger: 261 `wait`, 115
`sibling_conflict_warn`, 12 `union_refused`, 12 `rebase_failed`, 3 `resume`,
1 `rebase_refund`, 1 `escalate`, 1 `merge`.

| time | event |
|---|---|
| 07:44:15 | worker commits `acad8b4d1` |
| 07:48:33 | `union_refused` + `rebase_failed`; **resume #1** ("first injection — nothing prior to judge") |
| 07:49:51 | `union_refused` + `rebase_failed` again |
| 07:54:12 | worker **rebases by hand** → `26c59984d` |
| **07:57:53** | worker **supersedes the pre-rebase commit** — `234cea39c`, a two-parent `merge -s ours`. From here the branch head is a **merge commit** |
| 09:58:20 | `rebase_refund` — "forge reported CONFLICTING but the merge is clean" (a phantom, correctly refunded) |
| 09:58:35 – 10:21:39 | **ten** further `union_refused` + `rebase_failed` pairs |
| 10:00:45 | **resume #2** ("undelivered — 1 message still unread") |
| 10:11:48 | **resume #3** ("undelivered — 2 messages still unread") |
| 10:22:27 | **`escalate` at cycle 3, not 5** — see below |
| **20:03:13** | **a human merges `origin/main` into the branch** — `d8d574a0d`, two parents, resolving `CLAUDE.md` and `doc_detail.html` in both trees |
| 20:32:34 | #2179 squash-merged |
| 20:33:55 | watcher logs `merge` — *"PR already merged"* |

The escalation asked for a human at 10:22:27Z and a human came 9h40m46s later.
The repair was theirs, not the watcher's, and the record says so.

## Three causes, and only one of them has a card

### 1. All three resumes were never read (kpr-watch-13, reproduced)

```
$ python -m tools.ci.resume_delivery --task dwr-ev-03 --json
{"verdict": {"verdict": "undelivered",
             "detail": "3 pr_watcher message(s) still unread in the queue",
             "pending": 3, "receipted": 0}}
```

Three attempts, **zero delivered**. Nothing consumed the queue then and nothing
has since. Not a new finding; kpr-watch-13's, still true.

### 2. `CLAUDE.md` is undeclared, and it forfeited two DECLARED files with it

Every one of the twelve refusals names an undeclared file. Replaying each
refused set through the **shipped**
`tools/kanban/union_resolver.py::match_declaration` against the live
`union_resolver.files`:

| refusal window | files in the set | declared | undeclared |
|---|---|---|---|
| 07:48, 07:49 | `{icdev/,}tools/document_intelligence/suggestion_store.py` | 0 | 2 |
| 09:58 | 8 files (`CLAUDE.md`, the bootstrap copy, `redraft.py` ×2, `suggestion_store.py` ×2, the new migration, `tests/test_dwr_redraft.py`) | 0 | 8 |
| 10:00 – 10:14 | `CLAUDE.md`, `icdev/data/claude_bootstrap/CLAUDE.md` | 0 | 2 |
| **10:18, 10:20, 10:21** | the two above **plus** `{icdev/,}tools/dashboard/templates/document_intelligence/doc_detail.html` | **2** | 2 |

The last three refusals — the three immediately before the escalation — refused
a **declared** pair. `tools/dashboard/templates/**/*.html` is declared with
rules `['adjacent_edits', 'keep_both_blocks']`, and the `icdev/` mirror matches
it too (`match_declaration` strips the prefix, exactly as
`task-det-d6615e5bd4-needed-a-human-resolution.md` established). The whole plan
was abandoned because `CLAUDE.md` shared the set.

This is a **second live instance** of the mixed case that record measured at
15.9% of 63 rows and reported as "all one task". It is now two tasks. Already
filed as **kpr-watch-14** (`scheduled`); nothing about it is fixed here, and
`args/pr_watcher_config.yaml` is a `protected_path`, so folding it in would
stall this card behind the mfx-mrg-04 door.

What the human's merge commit did is what the declared rules are named after —
"both appended … Both kept, main's first because it already landed"
(`keep_both_blocks`) for `CLAUDE.md`, and "Both intents kept — main's
word-level renderer AND this branch's button" (`adjacent_edits`) for
`doc_detail.html`. That is suggestive, **not** a claim that declaring
`CLAUDE.md` would have rescued this rebase: cause 3 is independent of the union
rung and would have failed the rebase anyway. It also carries mfx-ci-04's
constraint — the bootstrap copy is **regenerated**, never unioned — which is
kpr-watch-14's work, with its own survey.

### 3. A `-s ours` supersede merge makes the branch unrebasable — NO CARD EXISTS

From 07:57:53Z the branch head was `234cea39c`, a two-parent `merge -s ours`
whose entire purpose is "keep the rebased tree `26c59984d`, discard the
pre-rebase `acad8b4d1`". `git rebase` does **not** preserve merges. Asked what a
default `git rebase origin/main` would replay from that head onto the `main` of
the moment:

```
$ git rev-list --no-merges --reverse 39d6655e6..234cea39c
acad8b4d1  feat(dic): redraft with my comments … (dwr-ev-03)
26c59984d  feat(dic): redraft with my comments … (dwr-ev-03)
```

**Two commits, where the branch intends one.** The supersede is undone by the
replay: the discarded commit is resurrected and its own replacement then lands
on top of it. Every `rebase_failed` after 07:57:53 names `26c59984d` — the
*second* of the two — which is what "the first applied, the second collided"
looks like. The squash on `main` carries the commit body **twice**, for the same
reason.

The mechanism is named in prose in the `dwr-ev-01` record ("the head is now a
merge commit, so `rebase origin/main` replays the original commit"). Nothing
ever measured it and no card exists, so:

#### Survey — all 649 lifetime `pr_watcher.rebase_failed` rows

Per **row**, was the branch carrying a merge commit **at that instant**?
(`git log --merges origin/main..<head>`, each merge's committer date compared
against the row's stamp — a *rescue* merge added afterwards must not be counted
as the cause.)

| bucket | rows | share |
|---|---|---|
| **branch demonstrably carried a merge commit at the time** | **52** | **8.0%** |
| branch was linear at the time | 484 | 74.6% |
| **UNMEASURABLE** — branch deleted, cannot be asked | 113 | 17.4% |

12 tasks, and `dwr-ev-03` contributes 10 of the 52 — joint largest with
`rmf-ui-11` (11) and `rmf-ui-03` (10), then `rmf-ui-10` (6), `rmf-ui-07` /
`rmf-ui-09` (3), `hcx-live-02` / `rmf-ui-06` / `mfx-sib-02` (2), `rmf-ui-05` /
`rmf-ui-08` / `dwr-ev-01` (1).

**8.0% is not the majority cause and this record does not claim it is** — the
ordinary shared-file conflict train is, and kpr-watch-14 is where that lives.
The 17.4% `UNMEASURABLE` is reported rather than folded into "linear": a deleted
branch cannot answer, and reading it as clean would understate the finding. A
coarser test (does the branch carry a merge commit *today*) reports 26 of 63
measurable tasks — that number is an **upper bound** and is deliberately not
used, because the human's own rescue merge is itself a merge commit.

Filed as **kpr-watch-15** (unclaimed) with this survey.

## The early escalation is #2184 working, and this is its second firing

`72765eec1` ("stop spending the resume budget on a queue nobody is draining")
landed on `main` at **10:10:28Z**. `dwr-ev-03` escalated at **10:22:27Z**,
**11m59s later**, at cycle **3 of 5**:

> resume undelivered after 3 attempt(s) — 3 pr_watcher message(s) still unread
> in the queue. Nothing is consuming this task's queue, so further injections
> cannot be read; escalating now rather than spending the remaining 2
> attempt(s) on it. The repair is DELIVERY, not the branch.

Four such escalations exist lifetime, all on 2026-09-08 and all after that
commit: `dwr-fid-02` 10:18:38 (cycle 2), **`dwr-ev-03` 10:22:27 (cycle 3)**,
`dwr-ws-02` 11:09:19 (cycle 1), `dwr-collab-01` 23:56:57 (cycle 1). Every
escalation before 10:10:28Z reads "resume cap reached (5/5)".

What it bought here is **two injections not made and an alert raised sooner** —
not a faster repair. The human arrived 9h40m after the escalation either way.

## The conflict train, measured 12 of 12

Each `union_refused` against `git log origin/main --first-parent` in the same
window:

| refusal | gap | preceding landing |
|---|---|---|
| 07:48:33, 07:49:51 | +430s, +508s | `13c0c1f4b` #2178 `dwr-cmt-01` |
| 09:58:35 | +76s | `56780545b` #2182 `dwr-anchor-06` |
| 10:00:45, 10:02:34, 10:03:46 | +104s, +213s, +285s | `583875149` #2180 `dwr-ev-02` |
| 10:11:48, 10:13:33, 10:14:26 | +80s, +185s, +238s | `72765eec1` #2184 |
| 10:18:55, 10:20:31, 10:21:39 | +95s, +191s, +259s | `39d6655e6` #2181 `dwr-ws-01` |

Twelve of twelve fire 76–508s after a distinct sibling landing, and the file set
**grows when a new sibling touches a new file** — `doc_detail.html` enters the
set 95s after `dwr-ws-01` merged, and `dwr-ws-01` is precisely the change the
human's merge commit had to reconcile ("main (dwr-ws-01) REPLACED the
hand-rolled inline diff with `renderWordDiff(s)`"). Nine sibling `dwr-*` cards
landed on `main` between 07:02 and 10:17 that morning.

## Why the acceptance criterion cannot be met at dispatch

The card asks that the derivation stop reporting `dwr-ev-03` and that the
finding read `cleared`. **Neither is reachable by any act available here**, and
the only way to force either is to edit the detector, its threshold or its
window — which the card itself forbids and which nothing in this change does.

`_recovery_rows` selects rows `created_at >= now - 24h`. The newest counted
attempt is resume #3 at **2026-09-08T10:11:48.715969Z**, so the entry is inside
the window until **2026-09-09T10:11:48Z** — ~7h36m after this card dispatched at
02:03:58Z. `escalate` outranks the later merge, correctly, so no repair, merge,
board move or lease release can remove it sooner. The finding then clears on the
first `detector_findings_reflex` cycle (6h cadence) after that time.

## Why closing this card is safe — two independent grounds

1. **It is a RECORD, not a card (autonomy-act-04).** `detector_findings
   --records` at 02:38Z:

   > `record  dwr-ev-03  active  a pr_watcher.merge at 2026-09-08T20:33:55.559946+00:00`
   > `landed AFTER the escalation at 2026-09-08T10:22:27.855378+00:00, and the subject`
   > `is 'done': nothing is left to land`

   `_disposition` returns `RECORD` **before** any recurrence branch runs, so no
   `-rN` can be filed at all.
2. **`earliest_clear_at` holds it anyway (task-f05d2bc8d1).** 10:11:48Z + 24h =
   2026-09-09T10:11:48Z; a terminal card before that is `held_closed_early`, not
   re-filed.

Both `fb989f6ad` (#2057) and `b235fa410` (#2134) are on `origin/main`, and this
worktree's `tools/kanban/detector_findings.py` is byte-identical to `main`'s, so
the `--records` output above is the shipped code's. Landed as an ordinary PR —
no `hold` label, no `scheduled_at` deferral.

The detector, its threshold and its window were not touched.

## Precedent

Thirteenth instance of the class, and the **first that is not moot at dispatch**
— the twelve before it re-derived clean because their window had already closed.
Fifth positive control (a real human repair inside the squash) after rmf-ui-08,
fni-api-01, rmf-ui-13 and dwr-ev-01. See
`docs/audits/task-det-{d6615e5bd4,aaf476c383,cd1d099fff}-needed-a-human-resolution.md`.
