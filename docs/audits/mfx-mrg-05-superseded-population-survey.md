# CUI // SP-CTI

# The superseded guard is ARMED, CORRECT and was never ASKED — a population survey

**Card:** mfx-mrg-05 · **Measured:** 2026-09-06 (live board, live forge)
**Subject:** the fifteen duplicate PRs an operator closed by hand at 2026-09-06T00:06Z
**Verdict:** none of (a), (b) or (c) as the card framed them. A **fourth** cause holds, and it
is the generalisation of (b): the guard runs on a **population that structurally excludes
every one of the fifteen**. Report only, no `--gate` (kpr-fix-03).

---

## 0. The one-line answer

`decide_superseded` would have fired on **14 of the 15** — but `pr_watcher`'s poll iterates
`list_pr_tasks()`, which selects only tasks in
`('in_progress','scheduled','pr_opened','ci_failed','merge_conflict','changes_requested')`.
**All fifteen tasks were already `done` when their duplicate PR was created** — by 12 to 67
seconds — and recorded **zero** further status transitions for the whole 3.4–26.0 hours each
duplicate sat open. The guard's population never contained a single one of them.

Corroborated three ways, each independent of the others:

1. **The board.** 15/15 `-> done` transitions precede the duplicate's `createdAt`; 15/15 have
   no transition of any kind afterwards (§5.1).
2. **The audit trail.** `close_superseded` / `superseded_warn` / `superseded_close_failed`:
   **zero rows, lifetime**, on a board carrying 7,327 `pr_watcher.wait` rows since 09-05 (§3).
3. **The forge.** Replaying the SHIPPED predicate over the fifteen fires on fourteen (§4).

---

## 1. The population

| dup PR | task | merged sibling | task `done` at | dup created at | gap | open for | after guard | replay |
|---|---|---|---|---|---|---|---|---|
| #2072 | mfx-ci-01 | #2071 | 09-04 22:07:29 | 09-04 22:08:19 | +49s | 26.0 h | no | FIRES |
| #2076 | flx-seam-02 | #2075 | 09-05 02:47:03 | 09-05 02:47:20 | +16s | 21.3 h | no | FIRES |
| #2079 | flx-studio-01 | #2077 | 09-05 03:46:21 | 09-05 03:47:12 | +50s | 20.3 h | no | FIRES |
| #2082 | mfx-sib-01 | #2080 | 09-05 04:06:42 | 09-05 04:07:45 | +62s | 20.0 h | no | **MISS** |
| #2084 | flx-compose-01 | #2078 | 09-05 04:13:38 | 09-05 04:14:01 | +22s | 19.9 h | no | FIRES |
| #2086 | flx-sim-01 | #2083 | 09-05 04:34:03 | 09-05 04:34:57 | +53s | 19.5 h | no | FIRES |
| #2090 | flx-bridge-01 | #2089 | 09-05 05:10:40 | 09-05 05:10:53 | +12s | 18.9 h | no | FIRES |
| #2092 | flx-airgap-01 | #2088 | 09-05 05:25:02 | 09-05 05:26:10 | +67s | 18.7 h | no | FIRES |
| #2106 | flx-test-01 | #2105 | 09-05 09:13:25 | 09-05 09:13:49 | +23s | 14.9 h | no | FIRES |
| #2108 | flx-az-01 | #2107 | 09-05 10:23:39 | 09-05 10:24:21 | +41s | 13.7 h | no | FIRES |
| #2111 | flx-oci-01 | #2110 | 09-05 12:46:53 | 09-05 12:47:38 | +44s | 11.3 h | **YES** | FIRES |
| #2116 | task-det-0b52c01989 | #2113 | 09-05 17:51:02 | 09-05 17:51:39 | +36s | 6.2 h | **YES** | FIRES |
| #2119 | task-det-6ca8c2dd3b | #2115 | 09-05 17:59:27 | 09-05 17:59:59 | +31s | 6.1 h | **YES** | FIRES |
| #2132 | rmf-inert-02 | #2129 | 09-05 20:27:00 | 09-05 20:27:56 | +55s | 3.6 h | **YES** | FIRES |
| #2133 | mfx-ci-03 | #2131 | 09-05 20:41:19 | 09-05 20:41:43 | +23s | 3.4 h | **YES** | FIRES |

gap `done` → duplicate: min 12s, **median 41s**, max 67s.
open for: min 3.4 h, median 18.7 h, max 26.0 h — **223.9 PR-hours** in total.

**The shape is uniform.** Fourteen of the fifteen duplicates carry the **identical head sha**
of an already-merged PR **on the same branch**, merged 60–110 seconds earlier. This is the
"a squash-merge leaves the branch *ahead* and a worker opens a duplicate PR" producer, once
per card.

---

## 2. (a) is FALSE — the guard was live for five of them, for 30.6 PR-hours

`#2102` (mfx-mrg-02, the guard) merged **2026-09-05T12:05:36Z**. Five duplicates — #2111,
#2116, #2119, #2132, #2133 — were created **after** that and sat open for 11.3, 6.2, 6.1, 3.6
and 3.4 hours respectively. Not one drew a superseded audit row of any kind.

Whether the *running* watcher process carried the new code across that window is
**UNMEASURABLE** here and deliberately not claimed: `agent_sessions` keeps one row per service
name (claim-verif-33c9f4cd11's process-local ownership), so the only recoverable start time is
the current one (`pr-watcher-36452`, started 2026-09-06T12:03:20Z). It does not matter to the
verdict, and that is the point of §5: **the population filter refuses all fifteen whatever code
version is running.** A fresh watcher started this minute would still not see them.

---

## 3. (b) is TRUE and is NOT the cause

The card's note is accurate: `_supersede_stale_prs` (`tools/genesis/reflexes/kanban.py:2606`)
closes a task's **other OPEN PRs on OTHER branches** (`_open_prs_for_task(..., exclude_branch=
keep_branch)`). Every sibling here is **MERGED**, on the **SAME** branch. The hook had nothing
to act on and could never have acted.

But the open-time hook is not the guard this card is about. The guard is the **poll-time**
check at `pr_watcher.py:3132`, and its refusal is absent from every audit row because it never
ran on these PRs:

```
SUPERSEDED-CLASS AUDIT ROWS (lifetime, live PG board 2026-09-06):
   (NONE)
pr_watcher rows since 2026-09-05: pr_watcher.wait 7327, sibling_conflict_warn 918,
   rebase_failed 127, merge 92, behind_main_hold 47, resume 30, protected_path_hold 22, ...
```

The watcher was working continuously. It never once asked the superseded question about a
duplicate, because no duplicate was ever put in front of it.

---

## 4. (c) is TRUE for EXACTLY ONE of the fifteen

Replaying the **shipped** `tools.ci.pr_superseded.decide_superseded` (imported, never
re-implemented — a second copy would prove only that two functions agree) against the merged
page `fetch_merged_prs` would have returned at each duplicate's own creation instant:

```
population=15  fires=14  unchecked=0  rate=93.33%
```

Zero `unchecked`, so the fail-open posture is not hiding anything here.

The one miss is **#2082 (mfx-sib-01)**, and it is a real finding:

* its head `558202ad9658` is **not** in merged #2080's commit list (`4fa3e1a035e8`) — the two
  commits have **different parents** (`ae89b914` vs `b677b427`): the worker committed the same
  work twice, off two different bases. Leg A (`shared_commits`) is structurally blind.
* leg B (`pure_revert`, `git cherry` by patch id) is blind too, and this is the interesting
  half. The diffstats are **byte-identical** — `9 files changed, 1758 insertions(+), 2
  deletions(-)` on both `558202ad9658` and main's squash `db03f80a5`, and
  `git diff 558202ad9658 db03f80a5 -- tools/kanban/sibling_overlap.py` is **empty** — yet
  `git patch-id --stable` gives `0f85ee6e2089…` against `f5eedbd9ee4c…`. **`patch-id` hashes
  context lines, so a squash onto a moved base does not match the branch commit it came from.**
  Measured live: `revert_evidence("kanban/mfx-sib-01")` returns
  `all_patches_upstream: false, would_revert: false, ahead: 1`.
  Leg B ships OFF (`superseded_revert_leg: false`) in any case — but this is evidence that
  arming it would **not** have caught the one case leg A misses, which is precisely the case
  its own docstring claims it exists for ("the cherry-picked / rebased duplicate, whose shas
  therefore differ"). That claim does not survive this measurement.

**1 of 15 = 6.67%.** Named, not fixed here.

---

## 5. THE OPERATIVE CAUSE — the population, and the fail-closed accident that saved it

### 5.1 The linked path cannot see a terminal task

`tools/ci/pr_watcher.py:567` —

```sql
SELECT id, title, description, status, executor_url FROM kanban_tasks WHERE status IN
 ('in_progress', 'scheduled', 'pr_opened', 'ci_failed', 'merge_conflict', 'changes_requested')
```

The poll loop is `for task in tasks:` over exactly that, one recorded `pr_url` each. Every
guard on that path — superseded, protected-path, behind-main, sibling-conflict, the resume
ladder — inherits the filter. All 15 tasks were `done` before their duplicate existed and never
left `done` (measured: 0 transitions after the duplicate's `createdAt`, 15/15).

The board even records the duplicate: `executor_url` on all fifteen points at the **duplicate**
PR number, written while the task was already `done`. The link was made; the status was not
moved; the watcher does not read `executor_url` for a terminal task.

### 5.2 The unlinked sweep DOES see them — and has no superseded check at all

`_sweep_unlinked_prs` (`pr_watcher.py:4234`) lists every open PR, but computes its `linked` set
from **the same `list_pr_tasks`**. A terminal-task PR is therefore classified **UNLINKED**, and
the sweep runs `classify_merge_readiness` and **merges on `ready`**. There is no
`_superseded_verdict` call anywhere on that path.

Measured — the only audit rows any of these duplicates ever drew:

```
#2072  2026-09-05 14:21:21  {"task_id": "", "action": "protected_path_hold",
        "reason": "changed files could not be determined and 5 path(s) are protected
                   -- refusing rather than guessing"}
#2076  2026-09-05 14:21:21  (identical)
#2111  2026-09-05 18:14:00  (identical)
#2133  no audit rows at all
```

`task_id` is the **empty string** — that is the sweep, and only the sweep (it audits once per
PR, kpr-watch-10). So the single thing standing between these duplicates and an automatic merge
was a **fail-closed protected-path refusal that fired only because the changed-file listing
could not be read**. Not the superseded guard, which was never consulted; not the staleness
rung, which never got a verdict to reach.

### 5.3 It has already merged duplicates — five of them, as empty commits

The same shape reaches the **linked** path when the scheduler's post-dispatch confirmation
re-opens the task (`_pr_flow_outcome` → `done -> pr_opened`, `reflexes/kanban.py:10042`).
Measured over 194 kanban PRs opened 2026-08-21..2026-09-06, **26 (13.40%)** were opened while
their task was already terminal — and **five of those MERGED**:

| PR | branch | sibling | merged | first-parent diff on main |
|---|---|---|---|---|
| #1905 | qa-fail-49655511c721a165 | #1904 | 08-22 02:32 | **EMPTY** |
| #1926 | task-det-b1d12d0f70 | #1925 | 08-25 04:49 | **EMPTY** |
| #1950 | qa-fail-602a6fa061cee852 | #1947 | 08-26 04:40 | **EMPTY** |
| #2096 | flx-compose-02 | #2094 | 09-05 06:17 | **EMPTY** |
| #2101 | flx-airgap-03 | #2098 | 09-05 07:04 | **EMPTY** |

(`git diff --shortstat <merge>^1 <merge>` is empty for all five; the same command on their
siblings #2094 and #2098 gives `4 files changed, 243 insertions(+)` and `14 files changed,
2232 insertions(+)`.) Three of the five drew a `pr_watcher.already_landed_warn` **seconds
before the merge** — `landed_check_on_poll` in `warn` mode said the work was already on main,
and the merge went ahead 4 seconds later. Each cost a full ~19-job CI run and left a commit on
main whose subject claims a feature it did not deliver.

---

## 6. What a widened population would refuse

Replaying the shipped predicate over **all 26** terminal-born kanban PRs in the window (the
exact set a population widening would admit), each against the merged page as of its own
creation:

```
population 26 = 21 closed-unmerged + 5 MERGED
  fires                    : 24 / 26  (92.31%)
  FALSE POSITIVES          :  0 / 26
  correct non-fire         :  1  (#1937 — its sibling #1936 was CLOSED UNMERGED, so there is
                                  no merged sibling and the rule correctly declines)
  miss                     :  1  (#2082, §4)
  unchecked                :  0
```

**Zero false positives across the whole set.** Every one of the 24 fires was verified by hand
against the forge: same head branch, and a head sha that is IN the merged sibling's commit
list (identical head sha in all 24 — #1905/#1904, #1926/#1925, #1949/#1945, #1950/#1947,
#1985/#1983, #2015/#2014, #2056/#2053, #2096/#2094, #2100/#2097, #2101/#2098 and the fourteen
of §1). Of the 26, 21 ended closed-unmerged — fifteen of them in one operator batch at
2026-09-06T00:06Z — and 5 merged as empty commits (§5.3). The only PR in the population that
is NOT a duplicate of merged work is #1937, and the predicate correctly declines it. There is
no legitimate PR here that the predicate would have refused — which is what makes this a
*population* problem and not a *predicate* problem.

That 92.31% is the **measured size of the defect on an enriched population**, not a fire rate
against routine work: the denominator is "PRs opened after their own task went terminal", which
is 13.40% of kanban PRs and is itself the anomaly. It must not be quoted as a refusal rate
against ordinary PRs — the guard's own survey (mfx-mrg-02) measured that at 8/15 open PRs
mid-incident, and `--survey` on today's clean board reports `population=2, fires=0`.

**The board is clean of the shape right now** (2026-09-06T14:00Z): both open PRs — #2143
(`mfx-mrg-04`, `in_progress`) and #2137 (`qa-fail-6a87916931be3793`, `pr_opened`) — have
pollable tasks. The gap is structural, not currently manifest.

---

## 7. What this survey does NOT establish

* **Which code version the watcher was running on 2026-09-05.** UNMEASURABLE (§2). Not needed.
* **Whether the sweep would have merged the fifteen had the file listing been readable.** The
  protected-path refusal short-circuits before the staleness rung, so no verdict exists to
  replay and none is invented here. The card records each was 20–54 commits behind main, which
  `refuse_merge_when_behind` (`max_behind_commits: 10`) would very likely have caught — *likely*
  is not measured, and it is a second fail-closed accident either way, not a check that asked
  the right question.
* **Why some terminal tasks are re-opened by the scheduler and some are not.** The two
  sub-shapes are documented (§5.2 vs §5.3); the producer is `_pr_flow_outcome`'s post-dispatch
  confirmation racing the worker's own `gh pr create`. Diagnosing the producer is a different
  card from measuring the consumer, and widening the population is the repair that holds
  whichever way that race falls.
* **Anything about the FT board.** Measured on ICDEV[IT] only.

---

## 8. Follow-up

**ONE card: `mfx-mrg-06`** — ask the superseded question about a PR whose task has gone
terminal. The repair is to the **population**, not to the predicate, and not by auto-merging or
auto-closing on "the task is terminal" alone:

* the sweep already enumerates every open PR; give the **superseded verdict** a rung there,
  before `classify_merge_readiness` decides anything;
* keep the close gated on the **predicate** (24/26 fire, 0 false positives) and never on the
  terminal-task condition alone — 5 of the 26 were merged by the pipeline itself, so "terminal
  task" is not by itself a close-worthy fact, it is only what makes the PR *reachable*;
* survey the fire rate over the whole open-PR population before arming anything that closes.

Deliberately NOT recommended: arming `superseded_revert_leg`. §4 measures it blind to the one
case leg A misses.

---

## 9. Re-derive it

The predicate is imported, never copied. The merged page is rewound with
`gh pr list --state merged --search 'merged:<=T'`, which is the same ordering (`mergedAt` desc)
and the same `--limit 40` the shipped `fetch_merged_prs` uses.

```python
# replay.py — the fifteen, each against the merged page as of its own createdAt
import json, subprocess
from tools.ci.pr_superseded import decide_superseded, GH_FIELDS, DEFAULT_MERGED_LIMIT

DUPES = [2072, 2076, 2079, 2082, 2084, 2086, 2090, 2092, 2106, 2108, 2111,
         2116, 2119, 2132, 2133]


def gh_json(args):
    p = subprocess.run(["gh"] + args, capture_output=True, text=True,
                       encoding="utf-8", errors="replace", timeout=300)
    return (json.loads(p.stdout or "null"), "") if p.returncode == 0 else (None, p.stderr[:200])


def pr_record(n):
    d, err = gh_json(["pr", "view", str(n), "--json",
                      GH_FIELDS + ",createdAt,closedAt,state"])
    if d is None:
        raise SystemExit("gh pr view %d failed: %s" % (n, err))
    return d


def merged_page_as_of(iso):
    d, _ = gh_json(["pr", "list", "--state", "merged", "--json", GH_FIELDS,
                    "--limit", str(DEFAULT_MERGED_LIMIT),
                    "--search", "merged:<=%s" % iso])
    return d


rows = []
for n in DUPES:
    pr = pr_record(n)
    v = decide_superseded(pr, merged_page_as_of(pr["createdAt"]))
    rows.append(v.to_dict())
    print("#%-6s %-34s checked=%-5s fires=%-5s %-14s -> #%s"
          % (n, v.head_ref, v.checked, v.superseded, v.basis or "-", v.sibling_number))

fires = sum(r["superseded"] for r in rows)
print("population=%d fires=%d unchecked=%d rate=%.2f%%"
      % (len(rows), fires, sum(not r["checked"] for r in rows), 100.0 * fires / len(rows)))
```

Swap `DUPES` for the 26 terminal-born numbers to reproduce §6:
`1905 1926 1937 1949 1950 1985 2015 2056 2072 2076 2079 2082 2084 2086 2090 2092 2096 2100
2101 2106 2108 2111 2116 2119 2132 2133`.

The terminal-born set itself is re-derived by joining `gh pr list --state all --limit 250`
against `kanban_status_transitions`, taking each PR's last `to_status` at or before its
`createdAt` and keeping the ones in `('done','cancelled','decomposed','superseded')` —
`tools/ci/pr_watcher.py::_TERMINAL_TASK_STATUSES`, never a respelled list.

The board-side facts:

```bash
python -m tools.ci.pr_superseded --survey            # today's open population
python -m tools.kanban.landed_check --task flx-oci-01 --json
gh pr list --state all --head kanban/<task-id> --json number,state,mergedAt,headRefOid
git diff --shortstat <merge-sha>^1 <merge-sha>       # the five empty merges of §5.3
```
