#!/usr/bin/env python3
# [TEMPLATE: CUI // SP-CTI]
r"""A WARM worktree, pre-created while the host is quiet, claimed in ~1.3s (mfx-own-09).

THE DEFECT IS NOT THE ADD, IT IS WHEN IT RUNS. mfx-own-08 proved the caller
innocent -- a full dispatcher replica runs at 1.10x a hand add over 44
interleaved trials -- and proved the host is 3-20x slower for minutes at a
time, those minutes being self-hosted icdev_ft / icdev_rt CI jobs on this same
machine: 23.0s median and 15 of 37 adds KILLED with CI in flight, 8.1s median
and 0 of 54 killed without (Fisher exact p = 1.7e-7). `git worktree add` sits on
the CRITICAL PATH of a dispatch, so a slow minute becomes a
`worktree-isolation-guard` park -- and a second park inside 24h files a repark
card and demands a human. 96 such parks are on this board's record.

THE THREE OBVIOUS LEVERS WERE MEASURED AND REFUSED, and are not reopened here:
IO-throttling the CI containers (docker ACCEPTS `--device-write-bps` under
WSL2 and does not ENFORCE it -- 720 MB/s "limited"); fewer runners (the fleet's
IO work is fixed, so it lengthens each run and WIDENS the collision window);
and a "CI in flight -> decline to dispatch" gate (refused in writing by
docs/audits/mfx-own-08-...md s7: it fires on 40.7% of adds to prevent 15,
against the 1.63% CLAUDE.md calls grounds for standing a check down).

SO THE ADD MOVES OFF THE CRITICAL PATH RATHER THAN GETTING FASTER OR
BETTER-TIMED. A pool entry is a complete checkout on a THROWAWAY branch
(`kanban-pool/<hex>`) at a path that names no task. A dispatch CLAIMS one:
rename the branch to `kanban/<task-id>`, `git worktree move` it to the task's
path, `git reset --hard origin/<default>`. MEASURED on this host 2026-09-12,
same tree, same minute:

    git worktree add (quiet host)   3.62s     <- and 23.0s median / 15-of-37
                                                 killed with CI in flight
    ------------------------------------------
    git branch -m                   0.14s
    git worktree move               0.10s     <- a rename; the tree does not move
    git reset --hard origin/main    0.64s
    verify (list + status + HEAD)   0.19s
    ------------------------------------------
    CLAIM TOTAL                     ~1.07s

and the claim DOES NOT SCALE WITH THE TREE, which is the whole point. A pool
entry 12 hours stale (65 commits, 243 changed files) resets forward in 0.57s,
because `reset --hard` writes the DELTA while `worktree add` writes all 20,352
files. There is no duration here for a 30s budget to kill.

THE MECHANISM WAS PROVEN BEFORE IT WAS BUILT. `_create_worktree` already
RETURNS an existing worktree when the directory is present and registered (its
`if worktree_path.exists():` branch), so pre-creating the path is enough to make
a dispatch skip the add. That recipe was used BY HAND three times on
2026-09-11/12 (mfx-own-06, xrv-route-01, xrv-shield-02) and worked every time,
including at a disk queue of 13.2 where the runner's own add had just been
killed twice.

A POOL, NOT A JUST-IN-TIME PRE-CREATE, and both were measured as the card asked.
JIT (pre-create `.tmp/worktrees/<id>` for the tasks `_get_due_tasks` is about to
return) has the cheaper claim -- 0.07s, one `git worktree list --porcelain`,
because the existing exists-branch does the work -- and it was still rejected:
  * A JIT entry is BOUND TO ONE TASK ID. A pool entry is FUNGIBLE, so ONE warm
    checkout covers whichever card the next cycle picks; JIT needs one per
    candidate, and `_get_due_tasks` routinely returns more candidates than the
    cycle dispatches.
  * Everything that decides whether a due task is ACTUALLY dispatched -- the
    per-task lease, the landed check, sibling holds, the respawn guard -- runs
    AFTER `_get_due_tasks`. A JIT pre-create for a task the cycle then skips
    leaves a `kanban/<id>` branch and a 275 MB checkout for a card sitting in
    `scheduled`, which is precisely the shape `orphan_requeue` and the stranded
    audit read as a stuck build.
  * A pool entry's name and branch reference NO task, so nothing on the board
    can misread it.

HONESTY RAILS, each of them a way this must never become a new failure mode:
  * AN EMPTY POOL IS NOT AN ERROR. `claim` returns None for every doubt -- pool
    empty, lock busy, an unreadable `git worktree list`, an entry that fails its
    health check, a task branch that already exists -- and the caller falls
    through to today's inline add, unchanged. `claim` never raises into a
    dispatch, and it can never park a task.
  * A HALF-CLAIM IS TORN DOWN, NEVER LEFT. The dangerous state is a worktree at
    the TASK's path on the WRONG branch, because the dispatcher's exists-branch
    would then hand a worker a checkout of somebody else's branch. So the branch
    is renamed FIRST (a ref update, 0.14s, reversible) and the directory moved
    second, and any failure after the move tears the whole thing down -- worktree
    removed, branch deleted -- so the inline add that follows starts clean. The
    teardown is provably safe: a pool entry has never held work, which is
    verified (clean status) before anything is touched.
  * REFILL FAILURES ARE COUNTED AND REPORTED, never swallowed. `refill` returns
    every failure with its reason; `measure` reads them back out of the log.
  * THE POOL REAPS ITSELF. `max_size` is a hard ceiling, `entry_max_age_hours`
    retires an unclaimed entry, and a pool directory that has LOST its `.git`
    is removed by mfx-own-04's own rule -- `newest_mtime` and `_rmtree`
    IMPORTED from tools.kanban.worktree_husks, never re-derived. `sweep_husks`
    itself structurally CANNOT act here: its `board_row` precondition asks the
    board for the task id a directory is named after, and a pool entry is named
    after no task. That is stated rather than papered over. The 7-day
    `_sweep_old_worktrees` path IS extended to this root as a backstop.
  * A REFILL YIELDS TO REAL WORK. It takes the SAME cross-process
    `worktree_add_lock` a dispatch add takes, with a SHORT wait (5s): the lock
    being held means a dispatch is checking out right now, and the pool must
    never queue ahead of the board.

THE BUDGET IS THE DISPATCHER'S, AND IS NOT RAISED. A refill add runs under the
same 30s `WORKTREE_ADD_TIMEOUT_SECONDS` and the same `checkout.workers=0`, and
kills its whole process tree on expiry -- pinned to the dispatcher's literals by
tests/kanban/test_worktree_pool.py, which reads the reflex's AST rather than
importing 13,840 lines into a library. Giving the refill MORE patience would put
a 60s checkout on the very disk the dispatcher needs, making the pool the noisy
neighbour it exists to protect the board from.

WHEN "QUIET" IS UNMEASURABLE THE POOL STILL REFILLS, and that is a decision.
`tools.kanban.host_io` returns WAIT only when a slow or killed add sits inside
its cooldown -- the one measured signal -- and UNMEASURABLE whenever no add was
recorded in its window, which is the ordinary state of an IDLE board. Refusing
to refill on UNMEASURABLE would leave the pool structurally empty exactly when
refilling is cheapest and most valuable. host_io was surveyed and REFUSED as a
dispatch GATE because it cannot predict the next add; that verdict stands and is
not reopened. It is used here for a different question with a different cost of
being wrong: A BAD REFILL WASTES A CHECKOUT, A BAD GATE PARKS A TASK. Which
signal was seen is REPORTED as `quiet_basis` and never merged.

Config: args/worktree_pool.yaml. Kill switch: KANBAN_WORKTREE_POOL=0.

Headless:
    python -m tools.kanban.worktree_pool --status [--json]
    python -m tools.kanban.worktree_pool --refill [--json]
    python -m tools.kanban.worktree_pool --reap [--json]
    python -m tools.kanban.worktree_pool --measure [--window-hours 24] [--json]
"""
from __future__ import annotations

import json
import os
import secrets
import shutil
import subprocess  # nosec B404 - fixed argv, no shell, everywhere in this module
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

# sys.path BOOTSTRAP first, so `python tools/kanban/worktree_pool.py` reaches
# main() (kax-conflict-04); then the ONE root resolver.
_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from icdev.core.paths import repo_root  # noqa: E402
from tools.logging.icdev_logger import get_logger  # noqa: E402

logger = get_logger(__name__)

#: This checkout. `_REPO_ROOT` above is the IMPORT root and is used for nothing
#: else (xit-decl-03); every path below resolves through the ONE root resolver,
#: and the POOL's own root is canonicalised further -- see `canonical_root`.
BASE_DIR = repo_root(__file__)

#: Where the pool lives, RELATIVE TO THE MAIN CHECKOUT. Deliberately a sibling
#: of `.tmp/worktrees` rather than a child: every direct child of that directory
#: is read by `worktree_husks` as `<base>/<task-id>`, so a pool entry parked
#: there would be refused as `no_board_row` on every sweep and would pollute the
#: one survey whose whole job is to name real husks.
POOL_RELPATH = Path(".tmp") / "worktree-pool"

#: Pool branches. NOT `kanban/` -- that prefix is how `_worktree_task_id`,
#: `_branches_for_task` (mfx-own-05) and pr_watcher's unlinked sweep identify a
#: TASK's branch, and a pool entry belongs to no task. Pool branches are local
#: and are never pushed.
POOL_BRANCH_PREFIX = "kanban-pool/"

#: Directory name for an entry: `wt-<12 hex>`. The hex is the branch suffix, so
#: a directory and its branch are readable off each other with no state file.
ENTRY_PREFIX = "wt-"
_ENTRY_HEX_LEN = 12

#: The dispatcher's add budget and checkout config, restated here because
#: importing tools.genesis.reflexes.kanban would drag 13,840 lines (and the
#: board, and the LLM router) into a library. Pinned to the reflex's own
#: literals by an AST test -- a second SPELLING of a shared constant is how two
#: halves of one policy come to disagree about a number neither of them changed.
ADD_TIMEOUT_SECONDS = 30
ADD_GIT_CONFIG: Tuple[str, ...] = ("-c", "checkout.workers=0")

#: Seconds a claim waits for the pool bookkeeping lock. A claim is ~1.3s of git
#: plumbing, so this only ever queues behind another claim or a reap.
CLAIM_LOCK_WAIT_SECONDS = 10

# Verdict-ish reasons a claim can decline. Every one of them falls through to
# the inline add; none of them is an error.
MISS_DISABLED = "disabled"
MISS_EMPTY = "pool_empty"
MISS_LOCK = "lock_unavailable"
MISS_LISTING = "listing_unreadable"
MISS_NO_POOL = "no_pool_for_this_repo"
MISS_DEST_EXISTS = "destination_exists"
MISS_TASK_BRANCH_EXISTS = "task_branch_exists"
MISS_CONVERT_FAILED = "convert_failed"
MISS_ERROR = "error"


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
DEFAULT_CONFIG: Dict[str, Any] = {
    "enabled": True,
    "target_size": 2,
    "max_size": 3,
    "max_refills_per_run": 1,
    "entry_max_age_hours": 24,
    "refill_requires_quiet": True,
    "refill_lock_wait_seconds": 5,
    "refill_failure_cooldown_seconds": 600,
}

_CONFIG_RELPATH = Path("args") / "worktree_pool.yaml"


def load_config(path: Optional[Path] = None) -> Dict[str, Any]:
    """Declared config over the defaults. An unreadable file is the DEFAULTS.

    Never raises: a malformed YAML file must not stop a dispatch, and the pool
    degrading to its defaults is strictly safer than a dispatcher that cannot
    create a worktree at all.
    """
    cfg = dict(DEFAULT_CONFIG)
    p = path or (BASE_DIR / _CONFIG_RELPATH)
    try:
        import yaml  # noqa: PLC0415

        raw = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
        if isinstance(raw, dict):
            for k, v in raw.items():
                if k in cfg and v is not None:
                    cfg[k] = v
    except Exception as exc:  # noqa: BLE001 - defaults are a complete answer
        logger.debug("worktree pool: config unreadable (%s) -- using defaults", exc)
    return cfg


def enabled(cfg: Optional[Dict[str, Any]] = None) -> bool:
    """Is the pool on? The ENV kill switch wins over the config file."""
    env = os.environ.get("KANBAN_WORKTREE_POOL", "").strip().lower()
    if env in {"0", "false", "no", "off"}:
        return False
    if env in {"1", "true", "yes", "on"}:
        return True
    return bool((cfg or load_config()).get("enabled", True))


# ---------------------------------------------------------------------------
# Paths and git plumbing
# ---------------------------------------------------------------------------
def canonical_root(start: Optional[Path] = None) -> Path:
    """The MAIN checkout, even when this runs from inside a linked worktree.

    ONE spelling of the question, imported: `tools.git.worktree_paths
    .canonical_repo_root`. Deriving it from `__file__` is what nested 27
    worktrees under another worktree, and a pool rooted in a linked checkout
    would be invisible to the dispatcher that is supposed to drain it.
    """
    origin = Path(start) if start else BASE_DIR
    try:
        from tools.git.worktree_paths import canonical_repo_root  # noqa: PLC0415

        return Path(str(canonical_repo_root(origin)))
    except Exception as exc:  # noqa: BLE001
        logger.debug("worktree pool: canonical root unavailable (%s)", exc)
        return origin


def pool_root(root: Optional[Path] = None) -> Path:
    """Where entries live. ALWAYS canonicalised, whichever checkout is passed.

    The dispatcher hands its own `_repo_root` around, which is a LINKED worktree
    whenever a cycle runs from one -- and resolving the pool relative to that
    reads an empty directory and reports `pool_empty` for a pool that is full.
    Measured and fixed on this card's first live claim.
    """
    return canonical_root(Path(root) if root else None) / POOL_RELPATH


def _git(args: List[str], cwd: Path, timeout: int = 60) -> Tuple[int, str, str]:
    """(rc, stdout, stderr). A failure to even RUN git is rc 1, never a raise."""
    try:
        r = subprocess.run(  # nosec B603 B607 - fixed argv, no shell
            ["git", *args], cwd=str(cwd), capture_output=True, text=True,
            encoding="utf-8", errors="replace", timeout=timeout,
        )
        return r.returncode, (r.stdout or ""), (r.stderr or "")
    except Exception as exc:  # noqa: BLE001
        return 1, "", str(exc)


def _norm(p: Any) -> str:
    return str(p).replace("\\", "/").rstrip("/").lower()


def worktree_listing(root: Path) -> Optional[Dict[str, str]]:
    """{normalised path: branch ref} from `git worktree list --porcelain`.

    None on a FAILED listing -- never an empty dict. A git that fails, times out
    or runs against a repo mid-operation returns empty stdout, and an empty
    haystack contains no path, so reading that as "nothing is registered" makes
    every live worktree look like an orphan. That inversion is exactly the defect
    kpr-dup-10 cost three sessions their work over.
    """
    rc, out, _err = _git(["worktree", "list", "--porcelain"], root, timeout=30)
    if rc != 0:
        return None
    listing: Dict[str, str] = {}
    cur: Optional[str] = None
    for line in out.splitlines():
        if line.startswith("worktree "):
            cur = _norm(line[len("worktree "):])
            listing[cur] = ""
        elif line.startswith("branch ") and cur:
            listing[cur] = line[len("branch "):].strip()
    return listing


# ---------------------------------------------------------------------------
# Entries
# ---------------------------------------------------------------------------
def _entry_hex(path: Path) -> Optional[str]:
    name = path.name
    if not name.startswith(ENTRY_PREFIX):
        return None
    suffix = name[len(ENTRY_PREFIX):]
    if len(suffix) != _ENTRY_HEX_LEN or any(c not in "0123456789abcdef" for c in suffix):
        return None
    return suffix


def entry_branch(hex_id: str) -> str:
    return f"{POOL_BRANCH_PREFIX}{hex_id}"


def describe_entry(path: Path, listing: Optional[Dict[str, str]],
                   root: Path, now: Optional[datetime] = None) -> Dict[str, Any]:
    """One entry's state. `healthy` is True only when every question answered yes.

    `healthy` is False for an entry we could not fully check -- there is no
    third state here, because an unverifiable entry is one a dispatch must not
    be handed, and the fallback (an inline add) is always available.
    """
    now = now or datetime.now(timezone.utc)
    hex_id = _entry_hex(path)
    info: Dict[str, Any] = {
        "path": str(path), "hex": hex_id,
        "branch": entry_branch(hex_id) if hex_id else None,
        "has_git": (path / ".git").exists(),
        "registered": None, "clean": None, "age_hours": None,
        "healthy": False, "reason": "",
    }
    if hex_id is None:
        info["reason"] = "not_a_pool_entry"
        return info
    try:
        info["age_hours"] = round(
            (now.timestamp() - path.stat().st_mtime) / 3600.0, 2)
    except OSError:
        info["age_hours"] = None
    if not info["has_git"]:
        info["reason"] = "no_git_marker"
        return info
    if listing is None:
        info["reason"] = "listing_unreadable"
        return info
    ref = listing.get(_norm(path))
    info["registered"] = ref is not None
    if ref is None:
        info["reason"] = "unregistered"
        return info
    if ref != f"refs/heads/{info['branch']}":
        info["reason"] = f"unexpected_branch:{ref}"
        return info
    rc, out, _err = _git(["status", "--porcelain"], path, timeout=30)
    if rc != 0:
        info["reason"] = "status_unreadable"
        return info
    info["clean"] = not out.strip()
    if not info["clean"]:
        info["reason"] = "dirty"
        return info
    info["healthy"] = True
    return info


def list_entries(root: Optional[Path] = None,
                 listing: Optional[Dict[str, str]] = None,
                 now: Optional[datetime] = None) -> List[Dict[str, Any]]:
    """Every directory under the pool root, described. Newest first."""
    r = Path(root) if root else canonical_root()
    base = pool_root(r)
    if not base.is_dir():
        return []
    if listing is None:
        listing = worktree_listing(r)
    out: List[Dict[str, Any]] = []
    try:
        children = sorted(base.iterdir())
    except OSError:
        return []
    for c in children:
        if not c.is_dir():
            continue
        out.append(describe_entry(c, listing, r, now=now))
    out.sort(key=lambda e: (e["age_hours"] if e["age_hours"] is not None else 1e9))
    return out


def _pool_lock(timeout: float):
    from tools.coordination.gitlock import worktree_pool_lock  # noqa: PLC0415

    return worktree_pool_lock(timeout=timeout)


def _log_event(event: str, **fields: Any) -> None:
    """One structured record per pool event.

    `measure` reads these back, so the pool's own claim about itself is
    re-derivable from the log rather than from a counter nobody persists.
    """
    payload = {"event": event, **fields}
    try:
        logger.info("worktree pool: %s %s", event,
                    json.dumps(payload, default=str, sort_keys=True),
                    extra={"extra": payload})
    except Exception:  # noqa: BLE001 - a log must never break a dispatch
        pass


# ---------------------------------------------------------------------------
# Claim
# ---------------------------------------------------------------------------
def _teardown(dest: Path, branch: str, root: Path) -> None:
    """Undo a half-finished claim. Provably safe, and the proof is the ordering.

    Everything this removes was created by the pool and verified CLEAN before a
    single git command touched it, so there is no work here to lose -- the one
    thing kpr-dup-10 requires before any force-removal. Leaving the directory
    instead is the dangerous option: the dispatcher's exists-branch would return
    it and hand a worker a checkout in an unverified state.
    """
    _git(["worktree", "remove", "--force", str(dest)], root, timeout=120)
    if dest.exists():
        shutil.rmtree(dest, ignore_errors=True)
    _git(["worktree", "unlock", str(dest)], root, timeout=15)
    _git(["worktree", "prune"], root, timeout=30)
    _git(["branch", "-D", branch], root, timeout=15)


def _convert(entry: Dict[str, Any], task_id: str, dest: Path, root: Path,
             base: str, expect_manifest: bool) -> Optional[str]:
    """Turn one pool entry into the task's worktree, or leave nothing behind.

    ORDER IS THE SAFETY. The branch is renamed first because it is a ref update
    -- instant and reversible -- so the window in which a directory sits at the
    TASK's path under a POOL branch never exists. After the move, any failure
    tears the whole thing down rather than leaving a checkout the dispatcher
    would adopt.
    """
    src = Path(entry["path"])
    pool_branch = str(entry["branch"])
    task_branch = f"kanban/{task_id}"
    started = time.monotonic()

    rc, _out, err = _git(["branch", "-m", pool_branch, task_branch], root, timeout=30)
    if rc != 0:
        # The entry still carries its pool branch, so it is untouched and stays
        # in the pool. Nothing to undo.
        logger.warning("worktree pool: could not rename %s -> %s: %s",
                       pool_branch, task_branch, err.strip())
        return None

    dest.parent.mkdir(parents=True, exist_ok=True)
    rc, _out, err = _git(["worktree", "move", str(src), str(dest)], root, timeout=180)
    if rc != 0:
        rc_back, _o, err_back = _git(["branch", "-m", task_branch, pool_branch],
                                     root, timeout=30)
        if rc_back != 0:
            # The entry now carries a TASK branch at a POOL path. It can never be
            # claimed again and must not be left to confuse the next dispatch,
            # whose stale-branch cleanup would find a live worktree holding
            # `kanban/<id>` and REFUSE -- parking the task. Remove it outright:
            # it has never held work.
            logger.error(
                "worktree pool: move of %s failed (%s) AND the branch could not be "
                "renamed back (%s) -- removing the entry so %s can add cleanly",
                src, err.strip(), err_back.strip(), task_id,
            )
            _teardown(src, task_branch, root)
        else:
            logger.warning("worktree pool: move of %s to %s failed: %s",
                           src, dest, err.strip())
        return None

    rc, _out, err = _git(["reset", "--hard", base], dest, timeout=180)
    if rc != 0:
        logger.warning("worktree pool: reset of %s to %s failed (%s) -- tearing down",
                       dest, base, err.strip())
        _teardown(dest, task_branch, root)
        return None

    # CONFIRM BY RE-READING THE WORLD, never from the return codes above. A
    # claimed entry is about to receive an autonomous worker session; the four
    # questions below are the same ones `_create_worktree` asks of its own add.
    problems: List[str] = []
    if not (dest / ".git").exists():
        problems.append("no_git_marker")
    listing = worktree_listing(root)
    if listing is None:
        problems.append("listing_unreadable")
    elif listing.get(_norm(dest)) != f"refs/heads/{task_branch}":
        problems.append(f"registration_mismatch:{listing.get(_norm(dest))}")
    rc, head, _err = _git(["rev-parse", "--abbrev-ref", "HEAD"], dest, timeout=30)
    if rc != 0 or head.strip() != task_branch:
        problems.append(f"head_mismatch:{head.strip()}")
    if expect_manifest and not (dest / "tools" / "manifest.md").exists():
        problems.append("no_manifest")
    if problems:
        logger.warning("worktree pool: claimed worktree for %s failed verification "
                       "(%s) -- tearing down so the inline add starts clean",
                       task_id, ", ".join(problems))
        _teardown(dest, task_branch, root)
        return None

    _log_event("claim", task_id=task_id, path=str(dest), source=str(src),
               seconds=round(time.monotonic() - started, 3))
    return str(dest)


def claim(task_id: str, dest_path: Any, *, repo_root: Any = None,
          base: str = "origin/main", cfg: Optional[Dict[str, Any]] = None,
          expect_manifest: bool = True) -> Optional[str]:
    """Hand a warm worktree to `task_id`, or None -- and None is never an error.

    The caller falls through to its own `git worktree add` on None. This function
    is the optimisation; the add is the contract. It therefore swallows every
    exception: a pool defect must not become a dispatch failure.
    """
    cfg = cfg or load_config()
    dest = Path(dest_path)
    root = Path(repo_root) if repo_root else canonical_root()
    try:
        if not enabled(cfg):
            return None
        if not pool_root(root).is_dir():
            # THE POOL IS PER-REPOSITORY BY CONSTRUCTION, which is a stronger
            # statement than comparing two roots: `pool_root` canonicalises the
            # CALLER's repo root, so an EXTERNAL-repo task (compass / idea_lab)
            # asks its own repository for warm entries and finds a directory
            # nothing refills. One stat answers that without taking a lock or
            # spawning git on every external dispatch.
            _log_event("miss", task_id=task_id, reason=MISS_NO_POOL,
                       repo_root=str(root))
            return None
        if dest.exists():
            _log_event("miss", task_id=task_id, reason=MISS_DEST_EXISTS)
            return None

        with _pool_lock(CLAIM_LOCK_WAIT_SECONDS) as held:
            if not held:
                _log_event("miss", task_id=task_id, reason=MISS_LOCK)
                return None
            listing = worktree_listing(root)
            if listing is None:
                _log_event("miss", task_id=task_id, reason=MISS_LISTING)
                return None
            entries = list_entries(root, listing=listing)
            healthy = [e for e in entries if e["healthy"]]
            if not healthy:
                _log_event("miss", task_id=task_id, reason=MISS_EMPTY,
                           pool_depth=0, entries=len(entries))
                return None
            rc, out, _err = _git(
                ["rev-parse", "--verify", "--quiet", f"kanban/{task_id}"],
                root, timeout=30)
            if rc == 0 and out.strip():
                # The dispatcher's own stale-branch cleanup owns this case; a
                # claim on top of an existing task branch would rename over it.
                _log_event("miss", task_id=task_id, reason=MISS_TASK_BRANCH_EXISTS,
                           pool_depth=len(healthy))
                return None
            # NEWEST first: the freshest entry has the smallest reset.
            entry = healthy[0]
            got = _convert(entry, task_id, dest, root, base, expect_manifest)
            if got is None:
                _log_event("miss", task_id=task_id, reason=MISS_CONVERT_FAILED,
                           pool_depth=len(healthy))
                return None
            _log_event("pool_depth", pool_depth=len(healthy) - 1, after="claim")
            return got
    except Exception as exc:  # noqa: BLE001 - the pool may never break a dispatch
        logger.warning("worktree pool: claim for %s failed (%s) -- "
                       "falling back to an inline add", task_id, exc)
        _log_event("miss", task_id=task_id, reason=MISS_ERROR, error=str(exc))
        return None


# ---------------------------------------------------------------------------
# Refill
# ---------------------------------------------------------------------------
def _kill_tree(proc: Any) -> str:
    """Kill the add AND its checkout children. `proc.kill()` alone is the defect.

    On Windows it terminates one pid while the `git reset --hard` child keeps
    writing the tree (and keeps `communicate()` blocked) until it finishes --
    measured at 115s past a 30s budget by kph-repark-kph-repark-mfx-ci-04, over a
    checkout that had already completed.
    """
    import signal  # noqa: PLC0415

    pid = int(proc.pid)
    how = ""
    try:
        if os.name == "nt":
            done = subprocess.run(  # nosec B603 B607
                ["taskkill", "/PID", str(pid), "/T", "/F"],
                capture_output=True, text=True, timeout=30,
            )
            how = f"taskkill /T rc={done.returncode}"
        else:
            os.killpg(os.getpgid(pid), signal.SIGKILL)
            how = "killpg"
    except Exception as exc:  # noqa: BLE001
        how = f"tree kill failed: {exc}"
    try:
        proc.kill()
    except Exception:  # noqa: BLE001
        pass
    return how


def _remove_partial(path: Path, branch: str, root: Path) -> None:
    for _ in range(4):
        shutil.rmtree(path, ignore_errors=True)
        if not path.exists():
            break
        time.sleep(0.5)
    # A killed add leaves a LOCKED registration that `git worktree prune` skips
    # (measured on xrv-route-01, 2026-09-11), so unlock before pruning.
    _git(["worktree", "unlock", str(path)], root, timeout=15)
    _git(["worktree", "prune"], root, timeout=30)
    _git(["branch", "-D", branch], root, timeout=15)


def _create_entry(root: Path, base: str, expect_manifest: bool) -> Dict[str, Any]:
    """One `git worktree add` into the pool. Reports its own failure by name."""
    hex_id = secrets.token_hex(_ENTRY_HEX_LEN // 2)
    path = pool_root(root) / f"{ENTRY_PREFIX}{hex_id}"
    branch = entry_branch(hex_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    started = time.monotonic()
    try:
        proc = subprocess.Popen(  # nosec B603 B607 - fixed argv, no shell
            ["git", *ADD_GIT_CONFIG, "worktree", "add", "-b", branch,
             str(path), base],
            cwd=str(root), stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, start_new_session=True,
        )
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "reason": f"spawn_failed:{exc}", "hex": hex_id}
    try:
        _out, err = proc.communicate(timeout=ADD_TIMEOUT_SECONDS)
    except subprocess.TimeoutExpired:
        how = _kill_tree(proc)
        try:
            proc.communicate(timeout=10)
        except Exception:  # noqa: BLE001
            pass
        _remove_partial(path, branch, root)
        return {"ok": False, "reason": "budget_exceeded", "hex": hex_id,
                "seconds": round(time.monotonic() - started, 2), "kill": how}
    if proc.returncode != 0:
        _remove_partial(path, branch, root)
        return {"ok": False, "reason": f"rc={proc.returncode}",
                "detail": (err or "").strip()[-200:], "hex": hex_id}
    if not (path / ".git").exists() or (
            expect_manifest and not (path / "tools" / "manifest.md").exists()):
        _remove_partial(path, branch, root)
        return {"ok": False, "reason": "incomplete_checkout", "hex": hex_id}
    return {"ok": True, "hex": hex_id, "path": str(path),
            "seconds": round(time.monotonic() - started, 2)}


#: One line, one timestamp: when a refill add last FAILED. A file rather than an
#: in-process variable because two dispatchers refill against one disk, and a
#: cooldown only one of them can see is not a cooldown.
_FAILURE_STAMP = ".last-refill-failure"


def _record_refill_failure(root: Path) -> None:
    try:
        stamp = pool_root(root) / _FAILURE_STAMP
        stamp.parent.mkdir(parents=True, exist_ok=True)
        stamp.write_text(str(time.time()), encoding="utf-8")
    except OSError:  # a stamp that cannot be written must not break a refill
        logger.debug("worktree pool: could not record the refill failure stamp")


def refill_cooldown_remaining(root: Path, cfg: Dict[str, Any]) -> float:
    """Seconds left on the cooldown after a FAILED refill add. 0.0 when clear.

    WHY THIS EXISTS, and it is a cost this card would otherwise have ADDED. A
    refill add is killed at the same 30s budget a dispatch add is, and the host
    is slow for MINUTES at a time. On an idle board `host_io` reports
    UNMEASURABLE -- no dispatch add was recorded, because no dispatch happened --
    so the quiet check correctly allows a refill, and without this the pool would
    burn 30s of disk per cycle for the whole CI window, competing with the very
    runs that are slowing it down. One failure buys ten minutes of silence.

    NOT a retry budget and NOT a backoff ladder: the next attempt after the
    cooldown is an ordinary attempt. The dispatch path's "no retry" rule is about
    a task's add, and this is the pool declining to spend, not declining to work.
    """
    try:
        raw = (pool_root(root) / _FAILURE_STAMP).read_text(encoding="utf-8")
        last = float(raw.strip())
    except (OSError, ValueError):
        return 0.0  # no stamp, or an unreadable one: nothing is held back
    cooldown = float(cfg.get("refill_failure_cooldown_seconds", 600))
    remaining = (last + cooldown) - time.time()
    return max(0.0, remaining)


def quiet_check(cfg: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """May the pool spend a checkout right now? `basis` says on what evidence."""
    cfg = cfg or load_config()
    if not cfg.get("refill_requires_quiet", True):
        return {"refill": True, "basis": "not_required"}
    try:
        from tools.kanban import host_io  # noqa: PLC0415

        verdict = host_io.assess()
    except Exception as exc:  # noqa: BLE001
        return {"refill": True, "basis": "host_io_unavailable", "detail": str(exc)}
    v = verdict.get("verdict")
    # WAIT is the one MEASURED signal (a slow or killed add inside its
    # cooldown) and is honoured. UNMEASURABLE means no add was recorded in the
    # window -- the ordinary state of an idle board, and the state in which
    # refilling is both cheapest and most valuable.
    return {"refill": v != host_io.WAIT, "basis": v, "detail": verdict.get("reason")}


def refill(*, repo_root: Any = None, base: str = "origin/main",
           cfg: Optional[Dict[str, Any]] = None,
           expect_manifest: bool = True) -> Dict[str, Any]:
    """Top the pool up to `target_size`, at most `max_refills_per_run` per call.

    THE POOL LOCK IS HELD FOR THE DECISION ONLY, NEVER ACROSS THE ADD, and that
    ordering is load-bearing rather than tidy. Holding both locks through a 30s
    checkout would make a concurrent dispatch's `claim` spend its whole 10s wait
    on the pool lock, MISS, and then queue up to 90s behind the add lock for the
    inline add it fell back to -- strictly worse than today for that dispatch,
    produced by the very mechanism meant to speed it up. So the add runs under
    the ADD lock alone, which is the real serialisation, and the pool lock is
    retaken for nothing but the decision.

    The cost of that split is stated rather than hidden: two dispatchers can
    decide to refill at the same instant and create one entry more than
    `target_size`. `max_size` is re-checked against the world immediately before
    each add and `reap` enforces the ceiling regardless, so the overshoot is
    bounded by the number of concurrent refills and is transient.

    Returns a report. Failures are NAMED in `failures` -- a refill that silently
    does nothing is indistinguishable from a pool that is full.
    """
    cfg = cfg or load_config()
    root = Path(repo_root) if repo_root else canonical_root()
    report: Dict[str, Any] = {
        "enabled": enabled(cfg), "created": 0, "failures": [], "skipped": None,
        "depth_before": None, "depth_after": None, "quiet_basis": None,
    }
    if not report["enabled"]:
        report["skipped"] = MISS_DISABLED
        return report
    try:
        # ---- decide, under the pool lock, in milliseconds --------------------
        with _pool_lock(CLAIM_LOCK_WAIT_SECONDS) as held:
            if not held:
                report["skipped"] = MISS_LOCK
                return report
            entries = list_entries(root)
            healthy = [e for e in entries if e["healthy"]]
            report["depth_before"] = len(healthy)
            report["depth_after"] = len(healthy)
            want = min(
                int(cfg.get("target_size", 2)) - len(healthy),
                int(cfg.get("max_refills_per_run", 1)),
                int(cfg.get("max_size", 3)) - len(entries),
            )
            if want <= 0:
                report["skipped"] = "at_target"
                return report
            cooling = refill_cooldown_remaining(root, cfg)
            if cooling > 0:
                report["skipped"] = "failure_cooldown"
                report["cooldown_remaining_seconds"] = round(cooling, 1)
                return report
            quiet = quiet_check(cfg)
            report["quiet_basis"] = quiet.get("basis")
            if not quiet["refill"]:
                report["skipped"] = "host_busy"
                report["quiet_detail"] = quiet.get("detail")
                _log_event("refill_skipped", reason="host_busy",
                           pool_depth=len(healthy), quiet_basis=quiet.get("basis"))
                return report

        # ---- add, under the ADD lock ALONE -----------------------------------
        # THE REFILL YIELDS TO REAL WORK: a SHORT wait, because the lock being
        # held means a dispatch is checking out right now.
        from tools.coordination.gitlock import worktree_add_lock  # noqa: PLC0415

        wait = float(cfg.get("refill_lock_wait_seconds", 5))
        with worktree_add_lock(timeout=wait) as add_held:
            if not add_held:
                report["skipped"] = "add_lock_busy"
                _log_event("refill_skipped", reason="add_lock_busy",
                           pool_depth=len(healthy))
                return report
            for _ in range(want):
                # The CEILING re-checked against the world as it is NOW: another
                # dispatcher may have refilled while this one waited for the lock.
                live = len([e for e in list_entries(root) if e["hex"]])
                if live >= int(cfg.get("max_size", 3)):
                    report["skipped"] = "at_max_size"
                    break
                made = _create_entry(root, base, expect_manifest)
                if made.get("ok"):
                    report["created"] += 1
                    _log_event("refill", hex=made["hex"],
                               seconds=made.get("seconds"),
                               pool_depth=len(healthy) + report["created"])
                else:
                    report["failures"].append(made)
                    _record_refill_failure(root)
                    _log_event("refill_failed", reason=made.get("reason"),
                               hex=made.get("hex"), seconds=made.get("seconds"),
                               pool_depth=len(healthy) + report["created"])
                    break  # a failing add on a loaded disk will fail again

        report["depth_after"] = len(healthy) + report["created"]
        _log_event("pool_depth", pool_depth=report["depth_after"], after="refill")
        return report
    except Exception as exc:  # noqa: BLE001 - a refill may never break the cycle
        logger.warning("worktree pool: refill failed: %s", exc)
        report["failures"].append({"ok": False, "reason": f"error:{exc}"})
        return report


# ---------------------------------------------------------------------------
# Reap
# ---------------------------------------------------------------------------
def _husk_is_dead(path: Path, max_age_hours: float) -> Optional[bool]:
    """mfx-own-04's own rule, IMPORTED: is this .git-less directory provably dead?

    True | False | None, and None (cannot tell) never acts -- a partial walk
    OVER-estimates the age, which is the direction that deletes.
    """
    try:
        from tools.kanban.worktree_husks import newest_mtime  # noqa: PLC0415

        walk = newest_mtime(path, max_entries=40000, budget_seconds=20.0)
    except Exception as exc:  # noqa: BLE001
        logger.debug("worktree pool: husk age unmeasurable for %s (%s)", path, exc)
        return None
    # `measured: False` is the walk REFUSING -- an entry or time budget hit, and a
    # partial walk over-estimates the age, the direction that deletes.
    if not walk.get("measured") or walk.get("newest") is None:
        return None
    try:
        age_hours = (time.time() - float(walk["newest"])) / 3600.0
    except (TypeError, ValueError):
        return None
    return age_hours >= max_age_hours


def reap(*, repo_root: Any = None, cfg: Optional[Dict[str, Any]] = None,
         now: Optional[datetime] = None) -> Dict[str, Any]:
    """Remove aged, surplus and .git-less pool entries. Never touches anything else.

    A directory under the pool root whose name is not `wt-<12 hex>` is REPORTED
    and left alone -- the husk sweeper's instinct, and the reason its own
    `no_board_row` refusal exists.
    """
    cfg = cfg or load_config()
    root = Path(repo_root) if repo_root else canonical_root()
    out: Dict[str, Any] = {"removed": [], "kept": [], "foreign": [],
                           "unmeasurable": [], "errors": []}
    base = pool_root(root)
    if not base.is_dir():
        return out
    max_age = float(cfg.get("entry_max_age_hours", 24))
    max_size = int(cfg.get("max_size", 3))
    try:
        with _pool_lock(CLAIM_LOCK_WAIT_SECONDS) as held:
            if not held:
                out["errors"].append(MISS_LOCK)
                return out
            entries = list_entries(root, now=now)
            for e in entries:
                path = Path(e["path"])
                if e["hex"] is None:
                    out["foreign"].append(e["path"])
                    continue
                if not e["has_git"]:
                    dead = _husk_is_dead(path, max_age)
                    if dead is True:
                        shutil.rmtree(path, ignore_errors=True)
                        _git(["worktree", "prune"], root, timeout=30)
                        _git(["branch", "-D", str(e["branch"])], root, timeout=15)
                        out["removed"].append({"path": e["path"], "why": "husk"})
                    elif dead is None:
                        out["unmeasurable"].append(e["path"])
                    else:
                        out["kept"].append({"path": e["path"], "why": "husk_too_young"})
                    continue
                age = e["age_hours"]
                if age is not None and age >= max_age:
                    _teardown(path, str(e["branch"]), root)
                    out["removed"].append({"path": e["path"], "why": "aged",
                                           "age_hours": age})
                    continue
                out["kept"].append({"path": e["path"], "why": e["reason"] or "healthy"})
            # Ceiling, oldest first. `list_entries` sorts newest first, so the
            # surplus is taken from the tail.
            remaining = [e for e in list_entries(root, now=now) if e["hex"]]
            surplus = len(remaining) - max_size
            for e in reversed(remaining):
                if surplus <= 0:
                    break
                _teardown(Path(e["path"]), str(e["branch"]), root)
                out["removed"].append({"path": e["path"], "why": "over_max_size"})
                surplus -= 1
            if out["removed"]:
                _log_event("reap", removed=len(out["removed"]),
                           pool_depth=len([e for e in list_entries(root)
                                           if e["healthy"]]), after="reap")
    except Exception as exc:  # noqa: BLE001
        out["errors"].append(str(exc))
    return out


# ---------------------------------------------------------------------------
# Status and measurement
# ---------------------------------------------------------------------------
def status(*, repo_root: Any = None,
           cfg: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    cfg = cfg or load_config()
    root = Path(repo_root) if repo_root else canonical_root()
    entries = list_entries(root)
    return {
        "enabled": enabled(cfg),
        "pool_root": str(pool_root(root)),
        "target_size": cfg.get("target_size"),
        "max_size": cfg.get("max_size"),
        "depth": len([e for e in entries if e["healthy"]]),
        "entries": entries,
        "quiet": quiet_check(cfg),
    }


def log_file(root: Optional[Path] = None) -> Path:
    """The pool's own NDJSON, in the MAIN checkout.

    The dispatcher runs there, so that is where its pool events land; a linked
    worktree's `.logs` is empty and would read as "the pool did nothing", which
    is `unmeasurable` and must never be read as clean.
    """
    base = canonical_root(Path(root) if root else None)
    return base / ".logs" / "tools.kanban.worktree_pool.ndjson"


def _read_events(path: Path, cutoff: datetime) -> Optional[List[Dict[str, Any]]]:
    if not path.exists():
        return None
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return None
    events: List[Dict[str, Any]] = []
    for line in lines:
        try:
            rec = json.loads(line)
        except (ValueError, TypeError):
            continue  # a live append can tear the last line; that is normal
        extra = rec.get("extra") or {}
        if not isinstance(extra, dict) or "event" not in extra:
            continue
        try:
            ts = datetime.fromisoformat(str(rec.get("ts", "")).replace("Z", "+00:00"))
        except ValueError:
            continue
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
        if ts < cutoff:
            continue
        events.append({"at": ts, **extra})
    events.sort(key=lambda e: e["at"])
    return events


def _park_rows(cutoff: datetime, get_conn: Any = None) -> Optional[List[Dict[str, Any]]]:
    try:
        if get_conn is None:
            from tools.db.storage import get_connection as get_conn  # noqa: PLC0415
        conn = get_conn()
    except Exception as exc:  # noqa: BLE001
        logger.debug("worktree pool: board unreadable (%s)", exc)
        return None
    try:
        rows = conn.execute(
            "SELECT task_id, recorded_at, reason FROM kanban_status_transitions "
            "WHERE actor = 'worktree-isolation-guard' AND recorded_at >= %s "
            "ORDER BY recorded_at ASC",
            (cutoff.isoformat(),),
        ).fetchall()
        out = []
        for r in rows:
            d = dict(r)
            try:
                ts = datetime.fromisoformat(
                    str(d["recorded_at"]).replace("Z", "+00:00").replace(" ", "T"))
            except ValueError:
                continue
            if ts.tzinfo is None:
                ts = ts.replace(tzinfo=timezone.utc)
            out.append({"task_id": d["task_id"], "at": ts, "reason": d.get("reason")})
        return out
    except Exception as exc:  # noqa: BLE001
        logger.debug("worktree pool: park query failed (%s)", exc)
        return None
    finally:
        try:
            conn.close()
        except Exception:  # noqa: BLE001
            pass


def measure(window_hours: float = 24.0, *, repo_root: Any = None,
            get_conn: Any = None, now: Optional[datetime] = None) -> Dict[str, Any]:
    """The card's own acceptance question: did a park happen while the pool was warm?

    THE FINDING IS `parks_while_pool_nonempty`. A park while the pool was EMPTY
    is the sanctioned fallback -- the inline add ran and the host beat it -- and
    is counted apart. A park with NO pool observation before it is
    `unmeasurable`: the pool may not have been running, and reading that as a
    clean bill of health is the defect this whole file is written against.

    Counts are None -- never 0 -- when the pool has recorded nothing in the
    window, because "measured clean" and "never measured" justify opposite
    decisions.
    """
    now = now or datetime.now(timezone.utc)
    cutoff = now - timedelta(hours=window_hours)
    root = Path(repo_root) if repo_root else canonical_root()
    logp = log_file(root)
    events = _read_events(logp, cutoff)
    parks = _park_rows(cutoff, get_conn=get_conn)

    out: Dict[str, Any] = {
        "state": "unmeasurable",
        "window_hours": window_hours,
        "log_path": str(logp),
        "since": cutoff.isoformat(),
        "pool_events": None if events is None else len(events),
        "claims": None, "misses": None, "miss_reasons": {},
        "refills": None, "refill_failures": None, "refill_failure_reasons": {},
        "parks": None if parks is None else len(parks),
        "parks_while_pool_nonempty": None,
        "parks_while_pool_empty": None,
        "parks_unmeasurable": None,
        "park_detail": [],
        "reason": "",
    }
    if events is None or not events:
        out["reason"] = (f"no pool event recorded in {window_hours}h at {logp} -- "
                         "the pool may never have run; this is NOT a clean result")
        return out
    if parks is None:
        out["reason"] = "the board's transition log could not be read"
        return out

    out["claims"] = sum(1 for e in events if e["event"] == "claim")
    misses = [e for e in events if e["event"] == "miss"]
    out["misses"] = len(misses)
    for e in misses:
        k = str(e.get("reason") or "unknown")
        out["miss_reasons"][k] = out["miss_reasons"].get(k, 0) + 1
    out["refills"] = sum(1 for e in events if e["event"] == "refill")
    fails = [e for e in events if e["event"] == "refill_failed"]
    out["refill_failures"] = len(fails)
    for e in fails:
        k = str(e.get("reason") or "unknown")
        out["refill_failure_reasons"][k] = out["refill_failure_reasons"].get(k, 0) + 1

    depth_events = [e for e in events if e.get("pool_depth") is not None]
    nonempty = empty = unknown = 0
    for park in parks:
        prior = [e for e in depth_events if e["at"] <= park["at"]]
        if not prior:
            unknown += 1
            verdict = "unmeasurable"
            depth = None
        else:
            depth = int(prior[-1]["pool_depth"])
            if depth > 0:
                nonempty += 1
                verdict = "pool_nonempty"
            else:
                empty += 1
                verdict = "pool_empty"
        out["park_detail"].append({
            "task_id": park["task_id"], "at": park["at"].isoformat(),
            "pool_depth_at_park": depth, "verdict": verdict,
        })
    out["parks_while_pool_nonempty"] = nonempty
    out["parks_while_pool_empty"] = empty
    out["parks_unmeasurable"] = unknown
    out["state"] = "measured"
    return out


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def _render_status(s: Dict[str, Any]) -> str:
    lines = [
        f"worktree pool  {'ENABLED' if s['enabled'] else 'DISABLED'}   "
        f"depth {s['depth']} / target {s['target_size']} (max {s['max_size']})",
        f"  root: {s['pool_root']}",
        f"  refill allowed: {s['quiet']['refill']}  (basis: {s['quiet']['basis']})",
    ]
    if not s["entries"]:
        lines.append("  (no entries)")
    for e in s["entries"]:
        mark = "OK " if e["healthy"] else "-- "
        lines.append(f"  {mark}{Path(e['path']).name}  age "
                     f"{e['age_hours']}h  {e['reason'] or 'healthy'}")
    return "\n".join(lines)


def _render_measure(m: Dict[str, Any]) -> str:
    if m["state"] != "measured":
        return f"UNMEASURABLE: {m['reason']}"
    lines = [
        f"pool over the last {m['window_hours']}h "
        f"({m['pool_events']} pool events, {m['parks']} parks)",
        f"  claims {m['claims']}   misses {m['misses']} {m['miss_reasons'] or ''}",
        f"  refills {m['refills']}   refill FAILURES {m['refill_failures']} "
        f"{m['refill_failure_reasons'] or ''}",
        f"  parks while the pool was NON-EMPTY: {m['parks_while_pool_nonempty']}"
        "   <- the finding",
        f"  parks while the pool was empty:     {m['parks_while_pool_empty']}"
        "   (sanctioned fallback)",
        f"  parks with no prior observation:    {m['parks_unmeasurable']}"
        "   (not a clean result)",
    ]
    for p in m["park_detail"]:
        lines.append(f"    {p['at']}  {p['task_id']}  depth="
                     f"{p['pool_depth_at_park']}  {p['verdict']}")
    return "\n".join(lines)


def main(argv: Optional[List[str]] = None) -> int:
    import argparse  # noqa: PLC0415

    ap = argparse.ArgumentParser(
        description="Warm worktree pool: keep `git worktree add` off the "
                    "dispatch critical path (mfx-own-09)")
    ap.add_argument("--status", action="store_true")
    ap.add_argument("--refill", action="store_true")
    ap.add_argument("--reap", action="store_true")
    ap.add_argument("--measure", action="store_true")
    ap.add_argument("--window-hours", type=float, default=24.0)
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)

    if args.refill:
        out: Dict[str, Any] = refill()
        text = json.dumps(out, indent=2, default=str) if args.json else (
            f"created {out['created']}  depth {out['depth_before']} -> "
            f"{out['depth_after']}  skipped={out['skipped']}  "
            f"quiet={out['quiet_basis']}  failures={out['failures']}")
    elif args.reap:
        out = reap()
        text = json.dumps(out, indent=2, default=str) if args.json else (
            f"removed {len(out['removed'])}  kept {len(out['kept'])}  "
            f"foreign {len(out['foreign'])}  unmeasurable {len(out['unmeasurable'])}")
    elif args.measure:
        out = measure(args.window_hours)
        text = json.dumps(out, indent=2, default=str) if args.json else _render_measure(out)
    else:
        out = status()
        text = json.dumps(out, indent=2, default=str) if args.json else _render_status(out)
    print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
