<!-- CUI // SP-CTI -->
# kpr-watch-12 — `_ci_never_fired`: replayed fire-rate survey of two narrowings

- **Predicate:** `pr_watcher._ci_never_fired` (`tools/ci/pr_watcher.py`)
- **Filed from:** `docs/audits/task-det-184086657c-needed-a-human-resolution.md`
- **Date measured:** 2026-09-05, against the live PG board and the GitHub API
- **Verdict:** both narrowings are **supported**. Together they withhold **2 of
  30** escalations and **19 of 23** close/reopen re-triggers, and **zero of the
  31** firings on a branch where no workflow run had ever fired.

## The question the survey had to answer

This repo's standing rule is that a check is measured before it is armed, and
the same rule applies to *narrowing* one. `_ci_never_fired` gates a **destructive
act** — a close/reopen of a live PR — and then a HITL escalation, so the cost of
loosening it is that a PR whose CI genuinely never fired waits silently for a
person. That is #1462, the defect the rung exists for.

So the survey asks two things, and the second is the one that decides:

1. how many recorded firings would each narrowing have withheld, and
2. **would either ever withhold a firing for a workflow that genuinely never
   fired?**

## Corpus

Every recorded firing of the predicate, from the board's own `audit_trail`:

| action | n | note |
|---|---|---|
| `pr_watcher.escalate` with reason "CI never fired; re-trigger exhausted" | 30 | across 4 PRs — #1483 (16), #1646 (6), #1651 (7), #2088 (1) |
| `pr_watcher.ci_retrigger` | 23 | the **destructive** consumer, across 23 PRs |
| **total** | **53** | |

The `ci_retrigger` half was not in the card's brief and is where the defect
actually lives. The predicate gates the re-trigger *before* it gates the
escalation, so every escalation is preceded by a spent close/reopen — and the
re-trigger corpus is both larger and spread over 23 distinct PRs rather than 4.

Forge facts (workflow runs per branch, PR timelines, commit dates) were read
live from the GitHub API at replay time.

## The two narrowings, as replayed

- **n1 — anchor the grace to the HEAD SHA.** Age the `ci_missing_grace_minutes`
  window from the head commit's `committedDate` instead of the PR's `createdAt`.
- **n2 — ask whether a workflow run EXISTS for that head sha**, created at or
  before the firing and *younger than the grace*. A run older than the grace with
  still no check run is STUCK, not queued, and must still escalate.

### How the head sha and its push time were recovered

A commit date is a **proxy** for a push time and the replay says so. It is a
lower bound (the push is at or after the commit), so the age it yields is an
**upper bound** — it overstates how long a sha has sat there, which biases every
verdict below toward **escalating**, the conservative direction.

Commits force-pushed away are absent from a PR's timeline. A firing earlier than
every surviving commit therefore sat on a vanished head, and is bounded instead
by the PR's own `createdAt`: a PR cannot exist before its first head was pushed.
Those rows print `<original, force-pushed away>` and their n1 age equals their
shipped age — the most generous possible anchor, and they still escalate.

## Result

```
escalate: n=30  shipped_fires=30
  n1: withholds 2/30 (6.67%)  PRs=['1483', '2088']
  n2: withholds 2/30 (6.67%)  PRs=['1483', '2088']
  n1 AND n2 (what ships): withholds 2/30 (6.67%)
  no workflow run had EVER fired on that branch: 28/30 -- withheld by n1: 0, by n2: 0

ci_retrigger: n=23  shipped_fires=23
  n1: withholds 19/23 (82.61%)
  n2: withholds 14/23 (60.87%)
  n1 AND n2 (what ships): withholds 19/23 (82.61%)
    CI for that head sha was ALREADY created: 14/19
    the rest -- seconds until a run for that sha appeared anyway: [1.9, None, 1.9, 0.2, 0.0]
  no workflow run had EVER fired on that branch: 3/23 -- withheld by n1: 0, by n2: 0
```

### Would it ever withhold for a workflow that genuinely never fired? No.

**31 of the 53 firings happened on a branch where no workflow run had EVER been
created at that instant.** The combined narrowing withholds **none** of them:

| PR | branch | workflow runs, lifetime | firings | still fires |
|---|---|---|---|---|
| #1646 | `kanban/ctx-perf-02` | **0** | 7 (6 escalate, 1 retrigger) | **all 7** |
| #1651 | `kanban/ctx-trust-02` | **0** | 8 (7 escalate, 1 retrigger) | **all 8** |
| #1483 | `kanban/agov-det-02` | 3, first at 2026-08-10T04:45:26Z | 16 of its 17 fired *before* that | **16 of 17** |

#1483 is the case the card names as the one not to break, and 16 of its 17
firings survive untouched — ages from 64.6 to 395.6 minutes past the most
generous possible anchor, with no run of any kind on the branch. The 17th
(2026-08-11 01:52:56.92) is withheld, correctly: a run for its exact head sha
`1205bf25d` had been created **0.9 seconds earlier**, concluded `success`, and
the PR merged 8m41s later.

### What it does withhold

Every withheld firing is a queued run misread as an absent one:

| PR | firing | head sha age | run for that sha |
|---|---|---|---|
| #2088 | `escalate` 05:07:37.5 | 1.2 min | created **22.5s earlier**; first check run appeared 9.5s *after* the escalation |
| #2088 | `ci_retrigger` 04:57:07.1 | 1.4 min | created **80.1s earlier**, alive and healthy — the close/reopen **cancelled it** |
| #1483 | `escalate` 01:52:56.9 | 2.8 min | created 0.9s earlier |
| 16 more | `ci_retrigger` | 0.2–2.7 min | 11 already created (0.0–2.7s earlier); 4 appeared 0.0–1.9s later with no help |

Of the 19 re-triggers withheld, **14 fired while a workflow run for that exact
head sha already existed** and 4 more had one within two seconds. The single
`None` is #1661: the sha it fired on (`7c9838e7b`) never got a run because the
branch was force-pushed to `a758250c0` and merged 16m35s later — the re-trigger
did not rescue that sha, it was abandoned.

### Withholding is a DELAY, not a cancellation

This is what makes the 82.61% figure safe rather than alarming. n1 does not
remove a firing; it moves it to `push time + grace`. A branch whose CI is truly
absent still reaches the re-trigger and still escalates — 15 minutes after its
own push instead of instantly after somebody else's. #1646, #1651 and #1664 (head
sha 880.3 minutes old, no run within grace) all still fire.

### Why both, and not one

They catch different subsets, and neither is a superset of the other in general:

- **n2 cannot see a run that is about to exist.** Five re-triggers (#1464, #1661,
  #1690, #1761, #2052) had no run for their sha *at* the firing and one 0.0–1.9s
  later. n1 withholds all five; n2 withholds none.
- **n1 cannot tell a queued run from an absent one once the sha is genuinely
  old.** A runner outage that leaves a run queued for 20 minutes clears n1's
  grace; only n2 can see the run.

Shipped order is cheapest-first — rollup, then the head sha anchor (no forge
call), then the probe — the same cost ordering `_stale_verdict` takes for the one
condition that can reach the forge.

### Why not simply raise the two knobs

The card forbids it and the data agrees. `ci_missing_grace_minutes` is also what
the **one lifetime** `max_ci_retriggers_per_pr` is spent against: #2088 spent its
single re-trigger at 04:57:07 on an 80-second-old healthy run, the close/reopen's
new run cancelled it (mfx-ci-02's `cancel-in-progress`), and the budget was empty
for the real episode ten minutes later. A longer wait does not fix a predicate
that cannot tell a queued run from an absent one, and a second re-trigger would
cancel a second healthy run.

## Method — the replay script IN FULL

Report only; writes nothing. Run it with the repo root on `sys.path` and a `.env`
that names the live board (the script is reproduced here rather than committed as
a tool because it is a one-off historical replay over a closed corpus, the
mfx-ci-01 precedent).

```python
"""Replay of `pr_watcher._ci_never_fired` over its own recorded firings (kpr-watch-12).

Corpus: every audit_trail row this predicate produced -- action
'pr_watcher.escalate' with reason "CI never fired; re-trigger exhausted", and
action 'pr_watcher.ci_retrigger'. Forge facts (workflow runs, PR timelines) are
read live from the GitHub API. Report only; writes nothing.
"""
import json, subprocess, sys
from datetime import datetime, timezone

from tools.db.storage import get_connection

REPO = "icdev-ai/icdev"
GRACE_MIN = 15


def gh(path, jq):
    out = subprocess.run(["gh", "api", path, "--jq", jq],
                         capture_output=True, text=True, encoding="utf-8")
    if out.returncode != 0:
        raise RuntimeError(out.stderr[:300])
    return out.stdout


def iso(s):
    return datetime.fromisoformat(s.replace("Z", "+00:00"))


def firings():
    """Every recorded firing of the predicate, from the board's own audit trail."""
    rows = []
    with get_connection() as c:
        cur = c.cursor()
        cur.execute(
            "SELECT created_at, action, details::text FROM audit_trail "
            "WHERE action IN ('pr_watcher.escalate','pr_watcher.ci_retrigger') "
            "ORDER BY created_at")
        for ts, action, det in cur.fetchall():
            d = json.loads(det)
            reason = d.get("reason") or ""
            if action == "pr_watcher.escalate" and "CI never fired" not in reason:
                continue
            when = ts if isinstance(ts, datetime) else iso(str(ts))
            rows.append({"at": when.replace(tzinfo=timezone.utc),
                         "kind": action.split(".")[-1],
                         "pr": (d.get("pr_url") or "").rsplit("/", 1)[-1],
                         "task": d.get("task_id")})
    return rows


def forge(pr):
    """PR metadata, its branch's workflow runs, and its commit/force-push timeline."""
    meta = json.loads(gh(f"repos/{REPO}/pulls/{pr}",
                         "{branch:.head.ref,created:.created_at}"))
    runs = [json.loads(x) for x in gh(
        f"repos/{REPO}/actions/runs?branch={meta['branch']}&per_page=100",
        '.workflow_runs[] | {sha:.head_sha,created:.created_at}').splitlines() if x.strip()]
    tl = [json.loads(x) for x in gh(
        f"repos/{REPO}/issues/{pr}/timeline?per_page=100",
        '.[] | select(.event=="committed" or .event=="head_ref_force_pushed") | '
        '{event:.event,at:(.created_at // .committer.date),sha:(.commit_id // .sha // "")}'
        ).splitlines() if x.strip()]
    return meta, runs, tl


def head_at(meta, tl, t):
    """(head sha, earliest-possible push time) in effect at instant `t`.

    A commit cannot be the head before it exists, and the PR cannot exist before
    its first head was pushed -- so `createdAt` bounds the ORIGINAL head. Commits
    force-pushed away are absent from the timeline; a `t` before every surviving
    commit therefore sits on a vanished head, bounded by the PR's own creation.
    Using a commit date where a push time is wanted OVERSTATES age, which biases
    every verdict below toward ESCALATING -- the conservative direction.
    """
    seen = [e for e in tl if iso(e["at"]) <= t]
    if not seen:
        return "<original, force-pushed away>", iso(meta["created"]), "pr_created_at (bound)"
    last = max(seen, key=lambda e: iso(e["at"]))
    return last["sha"], iso(last["at"]), "committedDate (proxy)"


def replay():
    cache, out = {}, []
    for f in firings():
        pr = f["pr"]
        if pr not in cache:
            cache[pr] = forge(pr)
        meta, runs, tl = cache[pr]
        t = f["at"]
        sha, pushed, basis = head_at(meta, tl, t)

        # SHIPPED predicate: grace aged from the PR's createdAt.
        age_pr = (t - iso(meta["created"])).total_seconds() / 60.0
        shipped = age_pr >= GRACE_MIN

        # Narrowing 1: grace aged from the HEAD SHA instead.
        age_head = (t - pushed).total_seconds() / 60.0
        n1_fires = age_head >= GRACE_MIN

        # Narrowing 2: does a workflow run EXIST for that head sha, created at or
        # before `t` and within the grace window? Runs for any other sha are not
        # this sha's CI. A run older than the grace is a STUCK run, not a queued
        # one, and must still escalate.
        hit = [r for r in runs
               if (sha.startswith(r["sha"][:9]) or r["sha"].startswith(sha[:9]))
               and iso(r["created"]) <= t
               and (t - iso(r["created"])).total_seconds() / 60.0 < GRACE_MIN]
        any_run_before = any(iso(r["created"]) <= t for r in runs)
        # Was CI for THIS sha already running when the predicate fired? And if
        # not, did a run for it appear anyway -- i.e. was the kick unnecessary?
        for_sha = [r for r in runs
                   if sha.startswith(r["sha"][:9]) or r["sha"].startswith(sha[:9])]
        before = [r for r in for_sha if iso(r["created"]) <= t]
        after = sorted((iso(r["created"]) - t).total_seconds()
                       for r in for_sha if iso(r["created"]) > t)
        n2_fires = not hit

        out.append({**f, "sha": sha[:9], "basis": basis,
                    "age_pr_min": round(age_pr, 1), "age_head_min": round(age_head, 1),
                    "shipped": shipped, "n1": n1_fires, "n2": n2_fires,
                    "any_run_on_branch_before": any_run_before,
                    "run_for_sha_before": bool(before),
                    "next_run_for_sha_s": round(after[0], 1) if after else None,
                    "run_age_s": round((t - iso(hit[0]["created"])).total_seconds(), 1) if hit else None})
    return out


rows = replay()
print(f"{'when':26} {'kind':10} {'PR':>5} {'head':10} {'age/PR':>8} {'age/head':>9} "
      f"{'ship':>5} {'n1':>4} {'n2':>4} {'run_age_s':>9}")
for r in rows:
    print(f"{r['at'].strftime('%Y-%m-%d %H:%M:%S.%f')[:26]:26} {r['kind']:10} "
          f"{r['pr']:>5} {r['sha']:10} {r['age_pr_min']:>8} {r['age_head_min']:>9} "
          f"{str(r['shipped']):>5} {str(r['n1']):>4} {str(r['n2']):>4} "
          f"{'' if r['run_age_s'] is None else r['run_age_s']:>9}")

for kind in ("escalate", "ci_retrigger"):
    sub = [r for r in rows if r["kind"] == kind]
    if not sub:
        continue
    n = len(sub)
    print(f"\n{kind}: n={n}  shipped_fires={sum(r['shipped'] for r in sub)}")
    for tag in ("n1", "n2"):
        held = [r for r in sub if r["shipped"] and not r[tag]]
        print(f"  {tag}: withholds {len(held)}/{n} ({100.0*len(held)/n:.2f}%)  "
              f"PRs={sorted({r['pr'] for r in held})}")
    both = [r for r in sub if r["shipped"] and not (r["n1"] and r["n2"])]
    print(f"  n1 AND n2 (what ships): withholds {len(both)}/{n} "
          f"({100.0*len(both)/n:.2f}%)")
    if kind == "ci_retrigger":
        already = [r for r in both if r["run_for_sha_before"]]
        print(f"    CI for that head sha was ALREADY created: {len(already)}/{len(both)}")
        print(f"    the rest -- seconds until a run for that sha appeared anyway: "
              f"{[r['next_run_for_sha_s'] for r in both if not r['run_for_sha_before']]}")
    never = [r for r in sub if not r["any_run_on_branch_before"]]
    print(f"  no workflow run had EVER fired on that branch: {len(never)}/{n} "
          f"-- withheld by n1: {sum(1 for r in never if not r['n1'])}, "
          f"by n2: {sum(1 for r in never if not r['n2'])}")
```

## Full replay table

`ship` is the shipped predicate; `n1`/`n2` are the narrowings; `True` means
"fires". `run_age_s` is how long a workflow run for that head sha had already
existed, blank when none had.

```
when                       kind          PR head         age/PR  age/head  ship   n1   n2 run_age_s
2026-08-09 23:04:39.580591 ci_retrigger  1483 <original      64.6      64.6  True True True
2026-08-09 23:33:22.294086 escalate      1483 <original      93.3      93.3  True True True
2026-08-09 23:46:55.451455 escalate      1483 <original     106.9     106.9  True True True
2026-08-09 23:59:50.599055 escalate      1483 <original     119.8     119.8  True True True
2026-08-10 00:16:06.810840 escalate      1483 <original     136.1     136.1  True True True
2026-08-10 00:18:01.922693 escalate      1483 <original     138.0     138.0  True True True
2026-08-10 00:35:40.190665 escalate      1483 <original     155.6     155.6  True True True
2026-08-10 00:41:16.505892 escalate      1483 <original     161.2     161.2  True True True
2026-08-10 01:07:21.667122 escalate      1483 <original     187.3     187.3  True True True
2026-08-10 01:21:09.063852 escalate      1483 <original     201.1     201.1  True True True
2026-08-10 01:41:46.788584 escalate      1483 <original     221.7     221.7  True True True
2026-08-10 01:52:28.297157 escalate      1483 <original     232.4     232.4  True True True
2026-08-10 02:12:27.524177 escalate      1483 <original     252.4     252.4  True True True
2026-08-10 03:24:44.692813 escalate      1483 <original     324.7     324.7  True True True
2026-08-10 03:30:51.647877 escalate      1483 <original     330.8     330.8  True True True
2026-08-10 04:35:36.962390 escalate      1483 <original     395.6     395.6  True True True
2026-08-11 01:05:01.242730 ci_retrigger  1479 bf0ee69e0    1667.8       2.7  True False False    0.2
2026-08-11 01:52:56.921852 escalate      1483 1205bf25d    1672.9       2.8  True False False    0.9
2026-08-11 03:11:03.129400 ci_retrigger  1464 180085d49    2046.3       1.9  True False  True
2026-08-13 02:40:14.490547 ci_retrigger  1597 b3a228850      39.9       0.2  True False False    1.5
2026-08-13 06:33:45.785651 ci_retrigger  1619 d573ff674      39.0       0.9  True False False    0.8
2026-08-14 19:18:05.228726 ci_retrigger  1646 9455d736c      58.5      59.4  True  True  True
2026-08-14 19:18:08.426341 ci_retrigger  1651 3198e9788      23.4      24.5  True  True  True
2026-08-14 19:22:12.542691 escalate      1646 9455d736c      62.6      63.5  True  True  True
2026-08-14 19:22:13.466296 escalate      1651 3198e9788      27.5      28.6  True  True  True
2026-08-14 19:29:44.256151 escalate      1651 3198e9788      35.0      36.1  True  True  True
2026-08-14 19:59:39.765750 escalate      1646 9455d736c     100.0     101.0  True  True  True
2026-08-14 19:59:41.907675 escalate      1651 3198e9788      65.0      66.1  True  True  True
2026-08-14 20:28:53.234660 escalate      1646 9455d736c     129.3     130.2  True  True  True
2026-08-14 20:28:55.558005 escalate      1651 3198e9788      94.2      95.3  True  True  True
2026-08-14 20:50:40.072787 escalate      1646 9455d736c     151.0     152.0  True  True  True
2026-08-14 20:50:40.664808 escalate      1651 3198e9788     115.9     117.1  True  True  True
2026-08-14 21:06:55.555987 ci_retrigger  1661 7c9838e7b      30.0       0.6  True False  True
2026-08-14 21:22:18.219914 ci_retrigger  1659 a4b1f12c1      53.8       0.7  True False False    2.2
2026-08-14 21:23:53.078417 escalate      1646 9455d736c     184.3     185.2  True  True  True
2026-08-14 21:23:53.729038 escalate      1651 3198e9788     149.2     150.3  True  True  True
2026-08-14 21:43:36.748059 escalate      1646 9455d736c     204.0     204.9  True  True  True
2026-08-14 21:43:37.408533 escalate      1651 3198e9788     168.9     170.0  True  True  True
2026-08-15 03:01:21.961186 ci_retrigger  1675 7e3dd716d     161.5       0.6  True False False    2.0
2026-08-15 11:54:57.136252 ci_retrigger  1690 dbb49b25d      98.4       0.7  True False  True
2026-08-15 12:25:55.016592 ci_retrigger  1685 4393a8809     289.4       0.7  True False False    2.0
2026-08-15 13:25:32.994647 ci_retrigger  1664 8350777be     963.0     880.3  True  True  True
2026-08-16 21:27:59.802081 ci_retrigger  1761 f77ae8940      19.9       0.7  True False  True
2026-08-17 10:14:40.848439 ci_retrigger  1783 fa94bd529      35.1       1.0  True False False    0.8
2026-08-17 23:49:56.239621 ci_retrigger  1786 68b5c9480      48.4       0.2  True False False    2.2
2026-08-18 14:08:38.112794 ci_retrigger  1798 a426159ac      51.5       0.7  True False False    2.1
2026-08-20 21:48:45.995242 ci_retrigger  1845 2885df0fa     750.9       1.8  True False False    1.0
2026-08-27 23:45:14.186322 ci_retrigger  1968 3dc82be91      30.2       0.8  True False False    1.2
2026-09-02 23:01:10.688320 ci_retrigger  2016 c6e1fcbba      20.2       0.2  True False False    2.7
2026-09-04 01:50:52.995210 ci_retrigger  2052 b55b2199c     138.0       0.2  True False  True
2026-09-05 04:57:07.067822 ci_retrigger  2088 00bb0b9d5      18.0       1.4  True False False   80.1
2026-09-05 05:07:37.500334 escalate      2088 44f4f0b7d      28.5       1.2  True False False   22.5
2026-09-05 07:49:01.041096 ci_retrigger  2103 36347f3d3      16.4       0.8  True False False    0.0
```

## Limits of this survey, stated

- **The head-sha timeline is reconstructed, not recorded.** Nothing persisted the
  PR head sha per poll before kpr-watch-02's `pr_merge_eligibility_events`, and
  that table postdates three of the four escalating PRs. Every reconstructed
  anchor is a bound in the escalating direction, so the withheld counts are an
  **upper bound on withholding** and the preserved counts a lower bound — the
  direction that cannot flatter the narrowing.
- **A commit date is not a push time.** A worker that commits and pushes twenty
  minutes later gets an overstated age and can still fire n1; n2 is the check for
  exactly that case.
- **Whether a withheld re-trigger would have been needed is counterfactual.**
  What is measured is that 14 of 19 fired on a sha whose CI had already been
  created and 4 more had one within two seconds. The remaining one (#1661) fired
  on a sha that was force-pushed away and never ran, and its PR merged 16m35s
  later.
- **`escalate` rows re-fire every poll while the condition stands**, so the 30
  rows are 4 PRs; the 23 `ci_retrigger` rows are 23 distinct PRs because the cap
  is one per PR. Neither count is a rate over polls, and neither is presented as
  one.
- **Report only.** No `--gate` (kpr-fix-03): this measures a closed historical
  corpus, not a diff.
