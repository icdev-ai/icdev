"""Worktrees holding work NOBODY IS COMING BACK FOR.

THE INCIDENT. On 2026-08-25 a dispatch of `ftl-chain-01` ran 4,603s against a 4,540s budget,
TIMED OUT, and left 1,178 lines uncommitted and unpushed in its worktree while the card fell
back to `backlog`. Nothing detected it. It was found only because `git worktree add` refused a
branch name that already existed -- which is to say, by accident, an hour later, and only
because somebody happened to reach for the same name.

That work was good: a complete option-chain seam with tests, recovered and merged as #214.
Had the name differed it would have sat there until the disk was cleaned.

WHAT `worktree_paths --audit` ALREADY ANSWERS, and this does not: whether a worktree sits in a
SANCTIONED ROOT. That is a placement question. This is a CONTENTS question -- 220 worktrees on
this machine are all correctly placed and some of them hold uncommitted work whose session is
gone.

THE SIGNATURE, and every clause is load-bearing:
    changes that ADD content (M / A / ??)      there is something to LOSE
    ZERO commits ahead of the base             nothing was preserved in git
    no local remote-tracking ref               nothing was pushed
    idle longer than `stale_minutes`           no session is mid-edit

Drop any one and the detector starts reporting live work. A branch with commits is recoverable
from git; one with a remote ref is on the forge; one modified two minutes ago belongs to
somebody who is still typing.

DELETIONS ARE NOT WORK, and the first version of this module got that wrong. `git status
--porcelain` reports `D` for every tracked file when a worktree's CONTENTS have been removed
from disk, so an emptied husk presents as ~19,000 "uncommitted files" -- and the first scan
duly reported three of them as orphaned, alongside nothing of value. There is nothing to lose
in a deletion: git holds those files. `emptied` is therefore its own verdict, useful (the
record is prunable) and never confused with abandoned work. The incident this exists for
looked nothing like it: MODIFIED files plus UNTRACKED new modules.

REPORT ONLY, AND DELIBERATELY SO. This module removes nothing, cleans nothing and touches no
worktree. The whole value is a NAMED LIST plus the command that recovers each one, because the
failure mode being fixed is "nobody knew it was there" -- and a tool that also deleted things
would turn a detection problem into a data-loss problem. `restore_acts.py` is where sanctioned
automatic repairs live, and this is not one of them: a human decides whether abandoned work is
worth keeping.

CONSERVATIVE BY CONSTRUCTION. Anything the scan cannot establish reads `unknown`, never
`orphaned`. A worktree whose git commands fail is a worktree we know nothing about, and
reporting it as abandoned would invite exactly the deletion this module refuses to do itself.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

#: Idle minutes before a worktree with uncommitted work is considered abandoned. 60 is chosen
#: against the incident: that dispatch had a 4,540s (76 min) budget, so a threshold below the
#: dispatch budget would flag every long-running session mid-flight.
DEFAULT_STALE_MINUTES = 90

#: Per-git-call ceiling. A hung git on one worktree must not stall a scan of 220.
GIT_TIMEOUT_S = 20


def _git(args: list[str], cwd: Path | None = None) -> tuple[int, str]:
    try:
        p = subprocess.run(["git", *args], cwd=str(cwd) if cwd else None,
                           capture_output=True, text=True, timeout=GIT_TIMEOUT_S)
        return p.returncode, (p.stdout or "").strip()
    except (subprocess.TimeoutExpired, OSError) as exc:
        return 1, f"__error__ {type(exc).__name__}: {exc}"


@dataclass
class Worktree:
    path: Path
    branch: str | None = None
    dirty_files: int = 0          # changes that ADD content: M / A / ??
    deleted_files: int = 0        # pure deletions -- git still holds these
    commits_ahead: int | None = None
    has_remote_ref: bool | None = None
    idle_minutes: float | None = None
    verdict: str = "unknown"       # orphaned | active | clean | unknown
    reason: str = ""
    sample: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {"path": str(self.path), "branch": self.branch,
                "dirty_files": self.dirty_files, "deleted_files": self.deleted_files,
                "commits_ahead": self.commits_ahead,
                "has_remote_ref": self.has_remote_ref,
                "idle_minutes": (None if self.idle_minutes is None
                                 else round(self.idle_minutes, 1)),
                "verdict": self.verdict, "reason": self.reason, "sample": self.sample}

    @property
    def recover_cmd(self) -> str:
        """What a human would run to look at it. Never executed by this module."""
        return f'cd "{self.path}" && git status --short && git diff --stat'


def list_worktrees(repo: Path) -> list[Worktree]:
    code, out = _git(["worktree", "list", "--porcelain"], repo)
    if code != 0:
        return []
    trees, cur = [], None
    for line in out.splitlines():
        if line.startswith("worktree "):
            cur = Worktree(path=Path(line[len("worktree "):].strip()))
            trees.append(cur)
        elif line.startswith("branch ") and cur is not None:
            cur.branch = line[len("branch "):].strip().replace("refs/heads/", "")
    return trees


def _idle_minutes(path: Path) -> float | None:
    """Minutes since the newest tracked-looking file changed.

    Bounded: it walks at most a few thousand entries and skips .git, node_modules and
    __pycache__ -- a full walk of a large checkout would cost more than the scan is worth,
    and build detritus is not evidence that a human is working.
    """
    newest, seen = 0.0, 0
    skip = {".git", "node_modules", "__pycache__", ".venv", "dist", "build", ".tmp"}
    try:
        for p in path.rglob("*"):
            if seen > 4000:
                break
            if any(part in skip for part in p.parts):
                continue
            try:
                if p.is_file():
                    seen += 1
                    newest = max(newest, p.stat().st_mtime)
            except OSError:
                continue
    except OSError:
        return None
    if not newest:
        return None
    return (time.time() - newest) / 60.0


def assess(wt: Worktree, *, base: str, stale_minutes: int) -> Worktree:
    """Fill in the verdict. Anything unestablished stays `unknown`, never `orphaned`."""
    if not wt.path.exists():
        wt.verdict, wt.reason = "unknown", "path does not exist (prunable worktree record)"
        return wt

    code, out = _git(["status", "--porcelain"], wt.path)
    if code != 0 or out.startswith("__error__"):
        wt.verdict, wt.reason = "unknown", f"git status failed: {out[:80]}"
        return wt
    lines = [ln for ln in out.splitlines() if ln.strip()]
    if not lines:
        wt.verdict, wt.reason = "clean", "nothing uncommitted"
        return wt

    # ONLY CHANGES THAT ADD CONTENT COUNT. A porcelain line's first two columns are the index
    # and worktree status; a pure `D` means the file is GONE from disk and git still has it,
    # so there is nothing to preserve. Counting deletions is what made an emptied husk read as
    # ~19,000 files of abandoned work.
    keep = [ln for ln in lines if not set(ln[:2].strip()) <= {"D"}]
    wt.dirty_files = len(keep)
    wt.deleted_files = len(lines) - len(keep)
    wt.sample = [ln.strip()[:60] for ln in keep[:5]]
    # DELETIONS DOMINATING ADDITIONS MEANS NOTHING NET-NEW EXISTS. Two real states produce it
    # and neither is abandoned work: a husk whose contents were removed from disk, and a tree
    # whose INDEX was cleared while the files stayed (an uncache sweep or a bad reset), which
    # presents as thousands of staged deletions PLUS the whole repo showing up untracked.
    # Measured on this machine: 18,882 deletions against 96 untracked -- and those 96 were
    # `.gitignore`, `.github/`, `.claude/` ... the repository itself, not somebody's work. Git
    # holds every one of those files; a reset restores them.
    if wt.deleted_files >= wt.dirty_files:
        wt.verdict = "emptied"
        wt.reason = (f"{wt.deleted_files} deletion(s) against {wt.dirty_files} addition(s) "
                     "-- nothing net-new; git already holds this. Prune or reset, never "
                     "treat as lost work")
        return wt

    if wt.branch:
        code, out = _git(["rev-list", "--count", f"{base}..{wt.branch}"], wt.path)
        wt.commits_ahead = int(out) if code == 0 and out.isdigit() else None
        code, _ = _git(["rev-parse", "--verify", "--quiet",
                        f"refs/remotes/origin/{wt.branch}"], wt.path)
        wt.has_remote_ref = code == 0
    wt.idle_minutes = _idle_minutes(wt.path)

    # ACTIVE beats ORPHANED on every axis: a branch with commits is recoverable from git, one
    # with a remote ref is on the forge, and recent edits mean somebody is still working.
    if wt.commits_ahead is None:
        wt.verdict, wt.reason = "unknown", "could not count commits against the base"
        return wt
    if wt.commits_ahead > 0:
        wt.verdict = "active"
        wt.reason = f"{wt.commits_ahead} commit(s) ahead -- the work is in git"
        return wt
    if wt.has_remote_ref:
        wt.verdict, wt.reason = "active", "branch is pushed -- the work is on the forge"
        return wt
    if wt.idle_minutes is None:
        wt.verdict, wt.reason = "unknown", "could not determine how long it has been idle"
        return wt
    if wt.idle_minutes < stale_minutes:
        wt.verdict = "active"
        wt.reason = f"edited {wt.idle_minutes:.0f} min ago -- a session may still hold it"
        return wt
    wt.verdict = "orphaned"
    wt.reason = (f"{wt.dirty_files} added/modified file(s), 0 commits, never pushed, idle "
                 f"{wt.idle_minutes:.0f} min -- nothing preserved anywhere")
    return wt


def scan(repo: Path, *, base: str = "origin/main",
         stale_minutes: int = DEFAULT_STALE_MINUTES,
         limit: int | None = None) -> dict:
    """Scan every worktree. `orphaned_count` is None when nothing could be assessed."""
    trees = list_worktrees(repo)
    if limit:
        trees = trees[:limit]
    assessed = [assess(w, base=base, stale_minutes=stale_minutes) for w in trees]
    counts = {k: sum(1 for w in assessed if w.verdict == k)
              for k in ("orphaned", "emptied", "active", "clean", "unknown")}
    measurable = sum(counts[k] for k in ("orphaned", "emptied", "active", "clean"))
    return {
        "repo": str(repo), "base": base, "stale_minutes": stale_minutes,
        "worktrees": len(assessed), "counts": counts,
        # None, never 0, when NOTHING could be assessed: a scan that established nothing is
        # not a scan that found nothing, and the difference decides whether to trust it.
        "orphaned_count": counts["orphaned"] if measurable else None,
        "orphaned": [w.to_dict() for w in assessed if w.verdict == "orphaned"],
        "unknown": [w.to_dict() for w in assessed if w.verdict == "unknown"],
    }


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--repo", default=".", help="repository whose worktrees to scan")
    ap.add_argument("--base", default="origin/main")
    ap.add_argument("--stale-minutes", type=int, default=DEFAULT_STALE_MINUTES)
    ap.add_argument("--limit", type=int, default=None, help="assess at most N worktrees")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)

    res = scan(Path(a.repo).resolve(), base=a.base, stale_minutes=a.stale_minutes,
               limit=a.limit)
    if a.json:
        print(json.dumps(res, indent=1))
        return 0

    c = res["counts"]
    print(f"{res['worktrees']} worktrees under {res['repo']}  "
          f"(base {res['base']}, stale after {res['stale_minutes']} min)")
    print(f"  orphaned {c['orphaned']}   emptied {c['emptied']}   active {c['active']}   "
          f"clean {c['clean']}   unknown {c['unknown']}")
    if res["orphaned_count"] is None:
        print("\n  NOTHING COULD BE ASSESSED -- this is not a clean bill of health.")
        return 0
    for w in res["orphaned"]:
        print(f"\n  ORPHANED  {w['path']}")
        print(f"            branch {w['branch']}  {w['reason']}")
        for s in w["sample"]:
            print(f"              {s}")
        print(f'            look:  cd "{w["path"]}" && git status --short && git diff --stat')
    if not res["orphaned"]:
        print("\n  No abandoned work found. (Report only -- this tool never deletes.)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
