# kph-repark-fni-ana-01 — resolution

**Card:** `[REPARK] fni-ana-01: parked twice by repo-aware-guard within 24h`
**Filed by:** `mfx-own-03`, which correctly refused to requeue a recurring park a third time.
**Resolved:** 2026-09-05.

---

## Verdict

**`fni-ana-01` must NOT be requeued.** It is `done`, and its work is on
`origin/main` in ICDEV[FT]. The card's prescribed remedy —
`cli.py --requeue fni-ana-01 --requeue-status scheduled` — was written on
2026-09-05 03:17 state and is now wrong: it would re-dispatch a delivered card
to rebuild work that has already landed.

The card's *diagnostic* half was right, and the cause it asked for is real. It
is fixed here.

---

## What actually happened

`fni-ana-01` is an external-repo task. The ICDev scheduler dispatches it; the
worker it launches builds in ICDEV[FT]. **The two disagree about where the
worktree goes**, and that is the whole defect:

| actor | path | source |
|---|---|---|
| dispatcher | `<tmp>/icdev-kanban/<repo>/<task>` | `_task_worktree_path`, external branch |
| worker | `<tmp>/icdev-worktrees/kanban/<task>` | `tools.git.worktree_paths`, what CLAUDE.md mandates |

Re-derive the collision:

```bash
python -c "from tools.genesis.reflexes.kanban import _task_worktree_path as p; print(p('fni-ana-01'))"
#   C:\Users\schuo\AppData\Local\Temp\icdev-kanban\icdev_ft\fni-ana-01
python -m tools.git.worktree_paths --path kanban fni-ana-01
#   C:\Users\schuo\AppData\Local\Temp\icdev-worktrees\kanban\fni-ana-01   <- what git named
```

So on every retry the dispatcher looks in *its* place, finds nothing, and treats
the **live worker worktree's** branch as a stale leftover to clean up.

### Timeline (`kanban_status_transitions`, verbatim)

| when (UTC) | transition | actor |
|---|---|---|
| 09-04 13:19:33 | `scheduled -> in_progress` | scheduler |
| 09-04 13:35:38 | `in_progress -> token_exhausted` | scheduler |
| 09-04 13:41:27 | `token_exhausted -> validating` | **repo-aware-guard** (park 1) |
| 09-05 03:16:51 | `validating -> scheduled` | cli — *"transient 'git worktree add' failure"* |
| 09-05 03:17:02 | `scheduled -> validating` | **repo-aware-guard** (park 2, **11s later**) |
| 09-05 13:13:49 | `validating -> in_progress` | manual |
| 09-05 13:33:16 | `in_progress -> done` | manual — MERGED via CLI `--merge`, PR #337 |

A human diagnosed the park as **transient** and requeued it. It reparked
identically eleven seconds later. That misdiagnosis was **caused by the log**.

### The log asserted an act that did not happen

`_create_worktree` ran `git branch -D`, and on failure fell back to
`git update-ref -d` — then logged, on the next line, **unconditionally**:

```
Stale branch kanban/fni-ana-01 deleted via update-ref fallback
git worktree add failed for fni-ana-01 (rc=128): ...
fatal: 'kanban/fni-ana-01' is already used by worktree at '.../icdev-worktrees/kanban/fni-ana-01'
```

The return code was never checked. "Deleted" followed by "already used" reads
as a race — i.e. transient. It was neither.

### And the fallback was destroying work, not failing to

Measured on git 2.55.0.windows.2, real repo, real held branch:

```
git branch -D held        rc=1  "cannot delete branch 'held' used by worktree at '<path>'"
git update-ref -d ...     rc=0                                    <- bypasses the refusal
after update-ref, held =  GONE
git worktree add -b held  rc=128 "already used by worktree at '<path>'"
final held             =  <trunk>          <- the NAME recreated at the base commit
WORK COMMIT REACHABLE FROM held? False
```

`git branch -D` refuses a checked-out branch **by design**. `update-ref` is
plumbing and does not honour the checkout, so the "fallback" defeated the exact
safety that was protecting a running worker. On 2026-09-04 it ran against a
branch carrying **two commits and 1,179 lines of unpushed work**. That work
survived only because the worker session was still live and re-committed
afterwards, recreating the ref. Luck of timing, not design.

---

## The work landed

`kanban/fni-ana-01` @ `2bcdaef` was squash-merged as
[icdev_ft#337](https://github.com/icdev-ai/icdev_ft/pull/337). A squash-merge
leaves the branch reading "ahead", which is why `git cherry` still reports `+`
for both commits. The content check is the two-dot diff:

```bash
cd C:/ai/icdev_ft && git diff --stat origin/main kanban/fni-ana-01
#  14 files changed, 14 insertions(+), 2403 deletions(-)
```

The branch is **strictly behind** main: it lacks 2,403 lines main has
(`fni-ana-02`, `fni-api-01`, `fni-lens-01`), and its 14 remaining insertions are
older revisions of lines `fni-lens-01` (#339) superseded. Nothing on it is
outstanding. Merging it now would revert delivered work — the #1651 shape.

---

## What changed here

**`tools/genesis/reflexes/kanban.py::_create_worktree`** (mirrored to `icdev/`):

1. **New `_live_worktree_holding(repo_root, branch)`** — returns the path of an
   existing worktree that has the branch checked out, else `None`. "Live" means
   *the directory is still on disk*, which is the distinction the fallback was
   missing. It returns `None` on any git failure, matching
   `_worktree_is_disposable`: prove it is free, refuse when you cannot tell.
2. **The `update-ref -d` fallback is refused when a live worktree holds the
   branch.** It is still used for the case its own comment describes — a
   registration whose directory is *gone* and which `git worktree prune` did not
   clear — where it destroys nothing.
3. **The return code is checked.** The refusal names the holding worktree, quotes
   git's own stderr, and says in words that the condition is **not transient and
   requeuing will not clear it**.

Dispatch behaviour is otherwise unchanged: an undeletable branch still falls
through to `git worktree add`, which still fails, and the task is still parked by
`repo-aware-guard`. Only the claim is now true and the cause is now legible.

**Test:** `tests/kanban/test_stale_branch_deletion_honesty.py` (5 tests, gated via
`args/ci_test_files/core.d/kph-repark-fni-ana-01.txt`). It uses a **real** git
repo with a **real** held branch, because the defect is a return code from git
that a mock would have to be told to produce — and being told is precisely what
was missing. It also pins the no-regression case: a genuinely abandoned branch
that nothing holds is still deleted and the worktree still created.

**Litter removed:** `C:/AI/ICDev/.tmp/worktrees/fni-ana-01` — an empty leftover
from the 09-04 09:19 dispatch, before `fni:` routing sent the card to ICDEV[FT].
Proven disposable before removal (`git status --porcelain` empty, 0 commits ahead
of `origin/main`, HEAD an ancestor of it).

---

## Not done, and why

* **`fni-ana-01` was not requeued.** It is `done` and delivered. See Verdict.
* **The worktree-path mismatch is not fixed.** Making the dispatcher and the
  worker agree on one path for an external task is the *structural* cause, and it
  changes where every external task builds. That is its own card with its own
  survey, not a repark chore. Until then the guard's park is the backstop — and
  with this change it now says so honestly.
* **The 30s worktree timeout was not raised and no retry was added**, per the
  card's explicit instruction and the guard's own comment.
* **No branch was deleted.** `git branch -D` is blocked by the PreToolUse hook
  and was not worked around. `kanban/fni-ana-01` still exists in both repos:
  in ICDev at `ae89b91` (0 commits ahead of `origin/main`, harmless now that no
  worktree holds it) and in ICDEV[FT] at `2bcdaef` (landed, superseded).

---

## Re-derivation

```bash
python tools/kanban/cli.py --show fni-ana-01                 # done
python -m tools.kanban.stranded_audit --json                 # no longer orphan_validating
cd C:/ai/icdev_ft && git diff --stat origin/main kanban/fni-ana-01
python -m pytest tests/kanban/test_stale_branch_deletion_honesty.py -q
```
