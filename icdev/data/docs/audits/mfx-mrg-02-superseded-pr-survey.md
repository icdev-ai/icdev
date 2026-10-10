# CUI // SP-CTI

# mfx-mrg-02 — superseded-PR survey

**Measured 2026-09-05 against `icdev-ai/icdev`.** Re-derive any line below with:

```bash
python -m tools.ci.pr_superseded --survey --json
python -m tools.ci.pr_superseded --survey --state closed --json
```

This survey exists because the check it arms **closes pull requests**. CLAUDE.md
requires a fire-rate measurement before a check is armed, and it names 1.63% as
the point at which a check is refusing routine work.

---

## The predicate

A PR is `superseded` when a **MERGED** PR **in the same task family** carries
**our head sha** in its commit list **and** every commit we can see is in that
list.

*Family* is one of three facts, never a similarity score:

| kind | test |
| --- | --- |
| `same_branch` | identical `headRefName` |
| `named_branch` | the merged PR's title/body contains our head branch |
| `named_pr` | the merged PR's title/body contains `#<our number>` |

The **head-sha requirement** is not redundant with the subset test. `gh` caps
the commits connection, so a truncated list of *ours* would make `ours ⊆ theirs`
trivially true. The head is the tip — the commit a merge would actually apply.

---

## Population 1 — open PRs (the false-positive risk)

| | |
| --- | --- |
| open PRs | 15 |
| fires | **8 (53.33%)** |
| unchecked | 0 |
| merged PRs considered | 40 |

Every fire was verified by hand against `gh pr view <n> --json headRefOid,commits`:

| open PR | branch | merged sibling | head sha | verdict |
| --- | --- | --- | --- | --- |
| #2096 | `kanban/flx-compose-02` | #2094 | `022ffa3f3e` — **identical** | duplicate |
| #2092 | `kanban/flx-airgap-01` | #2088 | `44f4f0b7d8` — **identical** | duplicate |
| #2090 | `kanban/flx-bridge-01` | #2089 | identical | duplicate |
| #2086 | `kanban/flx-sim-01` | #2083 | identical | duplicate |
| #2084 | `kanban/flx-compose-01` | #2078 | `eda1aaec69`, 6 of the sibling's 7 commits | duplicate |
| #2079 | `kanban/flx-studio-01` | #2077 | identical | duplicate |
| #2076 | `kanban/flx-seam-02` | #2075 | identical | duplicate |
| #2072 | `kanban/mfx-ci-01` | #2071 | `1ea11065ee` — **identical**, 9 of 9 commits | duplicate |

**53% is the measured size of the defect, not a threshold.** Seven of the eight
are the *same branch at the same head sha* under two PR numbers — the board was
mid-incident (mfx-mrg-01 addresses the cause; this card closes the residue).
The 1.63% rule is about a check refusing *routine work*, and no routine PR can
fire here: a legitimate branch always holds at least one commit no merged PR
has, and the subset rule cannot be satisfied.

**0 unchecked.** Every open PR could be evaluated; none had to be skipped.

---

## Population 2 — recently closed-unmerged PRs (recall)

| | |
| --- | --- |
| closed-unmerged PRs in the window | 2 |
| fires | **2 (100%)** |

| PR | branch | sibling | family | note |
| --- | --- | --- | --- | --- |
| #2056 | `kanban/kpr-stale-06` | #2053 | `same_branch` | the branch re-opened after #2053 squashed it |
| #2049 | `kanban/kpr-stale-05` | #2053 | `named_branch` | both its commits absorbed by #2053, whose body names the branch |

Both are named incidents from the card. The two older ones (#2015/#2014, 42s
apart; #1985/#1983, 82s) have aged out of the 40-PR window; they were re-checked
individually and both fire on `same_branch` with an identical head sha, and they
are pinned as fixtures in `tests/ci/test_pr_superseded.py`.

---

## The family requirement earns its place at zero cost

Re-running the open population **without** the family requirement adds **zero**
further fires. It is kept because it is what guarantees the close comment can
cite a PR number — a close with no named sibling is an unexplained close.

---

## Leg B (`superseded_revert_leg`) — measured contribution: 0

`git cherry origin/main origin/<branch>` marking every commit `-` (an equivalent
patch is already upstream) **and** a non-empty two-dot diff means the branch adds
nothing and merging it would *remove* what main has.

| open PR | leg A | leg B (`git cherry` all `-`) |
| --- | --- | --- |
| #2096 | fires | fires |
| #2090 | fires | fires |
| #2079 | fires | fires |
| #2092 #2086 #2084 #2072 | fires | **no** — sibling was squash-merged, so no patch id matches |
| #2095 #2091 #2082 #2070 #2066 #2064 #2059 | no | no |

Leg B fires on 3 of 15, a **strict subset** of leg A's 8, and on **nothing** leg
A misses. It ships **off** (`superseded_revert_leg: false`): a leg that has never
independently found anything must not be the thing that closes somebody's PR. It
is kept in the code because the squash case is exactly why it *cannot* be the
only leg, and the cherry-picked duplicate it is built for has simply not occurred
on this board yet.

---

## What the check does not do

* It **never merges** and **never un-drafts**. Un-drafting a superseded PR is one
  keystroke from a revert landing on main.
* It **never force-pushes** and **never deletes a branch**. Closing removes
  nothing; the comment says how to reopen.
* It **never completes a task** on the strength of the sibling alone.
  `landed_check` is asked separately, and an unchecked or negative report
  completes nothing and says so on the audit row. A sibling carrying our commits
  proves the *PR* is redundant; it does not prove the *task's deliverable*
  reached main — #2053's subject names kpr-stale-06 while it carried
  kpr-stale-05's commits.
* It is **fail-open** on every unreadable answer. `checked: false` is not a
  finding.

## Kill switches

| switch | effect |
| --- | --- |
| `superseded_close: false` | audit `superseded_warn`, close nothing |
| `superseded_check: false` | skip the merged listing entirely |
| `--dry-run` on the watcher | audit only |

Never a shell neutraliser.
