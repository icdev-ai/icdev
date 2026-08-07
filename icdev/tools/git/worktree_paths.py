# CUI // SP-CTI
"""Where a git worktree is allowed to live — one answer, for every actor.

Measured on this repo 2026-08-07: **150 registered worktrees across 22 distinct
parent directories**, including 33 dumped flat into ``%TEMP%\\claude``, 28 into
``C:\\AI\\.worktrees``, 5 directly into ``C:\\AI``, and 27 *nested inside another
worktree*. Five basenames collided across different parents — two different
``wt-wake2`` checkouts on two different branches existed simultaneously, which is
how one session's edits surfaced in another session's working tree.

The cause is not carelessness, it is the absence of an answer: every caller
derives its own base from its own ``__file__`` and picks its own name, so
``tools/genesis/reflexes/kanban.py``, ``tools/ci/modules/worktree.py`` and
``tools/workflow/failure_triage.py`` each invented a different root, and every
interactive session invented a different one again. Prose in CLAUDE.md has asked
for a convention since the beginning and produced 150 worktrees in 22 places, so
this module exists to make the answer computable and the violation detectable.

Two rules, and they are the whole design:

1. **Roots are disjoint per actor.** A kanban task and an interactive session
   cannot land on the same path because the actor name is a path component.
2. **Names carry their owner.** An interactive worktree is namespaced by session
   id, so two sessions that both pick the slug ``wt-fix`` still get separate
   directories.

Worktrees also live OUTSIDE the repository. A worktree under ``<repo>/.tmp`` is
walked by pytest collection, the coherence checker, and every ``git grep`` —
which is why 23 of the 150 are currently inside the checkout.
"""
from __future__ import annotations

import os
import subprocess  # nosec B404 -- git only, fixed argv, no shell
import tempfile
from pathlib import Path
from typing import Optional

__all__ = [
    "canonical_repo_root",
    "worktree_root",
    "actor_root",
    "worktree_path",
    "is_sanctioned",
    "describe_violation",
    "ACTORS",
]

#: Actors that may own a worktree. Each gets a disjoint subtree.
ACTORS = ("kanban", "cli", "verify", "autofix")

#: Where the runner has always put its worktrees. Grandfathered as sanctioned so
#: this module can ship without relocating in-flight tasks; see MIGRATION below.
_LEGACY_REPO_RELATIVE = (".tmp/worktrees", "trees", ".tmp/autofix")


def canonical_repo_root(start: Optional[Path] = None) -> Path:
    """The MAIN worktree's root, even when called from inside a linked one.

    ``git rev-parse --git-common-dir`` reports the main repository's ``.git``
    from anywhere in the worktree family. Deriving the root from ``__file__``
    instead is what produced the 27 worktrees nested under
    ``.wt-tsh-d4-audit5/.tmp/worktrees/`` — a dispatch running inside a worktree
    resolved its base to *that worktree* and created the next one within it.

    Falls back to the repo root inferred from this file when git is unavailable,
    which preserves behaviour rather than failing the caller.
    """
    origin = Path(start) if start else Path(__file__).resolve().parents[2]
    try:
        out = subprocess.run(  # nosec B603 B607 -- fixed argv, no shell
            ["git", "-C", str(origin), "rev-parse", "--git-common-dir"],
            capture_output=True, text=True, timeout=15,
        )
        if out.returncode == 0 and out.stdout.strip():
            common = Path(out.stdout.strip())
            if not common.is_absolute():
                common = (origin / common).resolve()
            return common.parent
    except Exception:  # noqa: BLE001 -- git absent, or not a repo
        pass
    return origin


def worktree_root() -> Path:
    """Base of every sanctioned worktree. Override with ``ICDEV_WORKTREE_ROOT``.

    Defaults outside the repository and outside any per-session scratchpad, so
    the location is stable across sessions and invisible to repo-walking tools.
    """
    env = os.environ.get("ICDEV_WORKTREE_ROOT", "").strip()
    if env:
        return Path(env).expanduser()
    return Path(tempfile.gettempdir()) / "icdev-worktrees"


def actor_root(actor: str) -> Path:
    """Subtree owned by *actor*. Disjoint by construction."""
    if actor not in ACTORS:
        raise ValueError(f"unknown worktree actor {actor!r}; expected one of {ACTORS}")
    return worktree_root() / actor


def _session_id() -> str:
    for var in ("CLAUDE_SESSION_ID", "ICDEV_SESSION_ID"):
        val = os.environ.get(var, "").strip()
        if val:
            # Session ids are UUIDs; keep them path-safe and short.
            return "".join(ch for ch in val if ch.isalnum() or ch in "-_")[:36]
    return "adhoc"


def worktree_path(actor: str, slug: str, session: Optional[str] = None) -> Path:
    """Absolute path a worktree for *slug* must use.

    ``cli`` worktrees are namespaced by session id, because interactive sessions
    reliably pick the same handful of slugs (``wt-fix``, ``wt-wake2``) and two of
    them landing on one directory is the collision this module prevents. Other
    actors key on the task id, which is already unique.
    """
    safe = "".join(ch if (ch.isalnum() or ch in "-_.") else "-" for ch in slug).strip("-")
    if not safe:
        raise ValueError(f"slug {slug!r} has no usable characters")
    if actor == "cli":
        return actor_root(actor) / (session or _session_id()) / safe
    return actor_root(actor) / safe


def _is_relative_to(path: Path, other: Path) -> bool:
    try:
        path.relative_to(other)
        return True
    except ValueError:
        return False


def is_sanctioned(path: Path | str, repo_root: Optional[Path] = None) -> bool:
    """True if a worktree at *path* is somewhere it is allowed to be.

    Sanctioned:
      * anywhere under :func:`worktree_root`
      * the legacy in-repo bases the runner still uses (grandfathered)
      * a Claude session scratchpad — already namespaced by session id, so it
        cannot collide, and CLAUDE.md directs sessions there
    """
    raw = Path(path).expanduser()
    # A relative path is refused rather than resolved. Resolving it against cwd
    # makes the answer depend on where the caller happens to be standing: a
    # session running inside its own scratchpad would see EVERY relative path
    # as sanctioned, because the scratchpad rule below would match the cwd it
    # was joined onto. Callers pass absolute paths; anything else is ambiguous.
    if not raw.is_absolute():
        return False
    try:
        p = raw.resolve()
    except OSError:
        p = raw.absolute()

    if _is_relative_to(p, worktree_root().resolve()):
        return True

    root = (repo_root or canonical_repo_root()).resolve()
    for rel in _LEGACY_REPO_RELATIVE:
        if _is_relative_to(p, root / rel):
            return True

    # Per-session scratchpad: .../claude/<project-slug>/<session-uuid>/scratchpad/...
    parts = [s.lower() for s in p.parts]
    if "scratchpad" in parts and "claude" in parts:
        return True
    return False


def describe_violation(path: Path | str) -> str:
    """Why *path* is refused, and what to use instead — for humans and hooks."""
    p = Path(path)
    suggestion = worktree_path("cli", p.name)
    return (
        f"worktree path is not sanctioned: {p}\n"
        f"  use: {suggestion}\n"
        f"  or:  python -m tools.git.worktree_paths --path cli {p.name}\n"
        f"  Worktrees must live under {worktree_root()} (override with "
        f"ICDEV_WORKTREE_ROOT) or your session scratchpad. Flat shared temp\n"
        f"  directories collide: two sessions picking the same slug get the same\n"
        f"  directory, and one session's edits appear in the other's working tree."
    )


# ---------------------------------------------------------------------------
# MIGRATION
#
# The runner's existing base (<repo>/.tmp/worktrees) is grandfathered rather than
# relocated, because tasks are in flight and repointing the base mid-run would
# orphan every worktree the runner is currently cleaning up. New callers should
# use worktree_path(); the runner's move is a separate, drainable change.
# ---------------------------------------------------------------------------


def main(argv=None) -> int:
    import argparse
    import json

    ap = argparse.ArgumentParser(
        prog="python -m tools.git.worktree_paths",
        description="Resolve and validate git worktree locations.",
    )
    ap.add_argument("--path", nargs=2, metavar=("ACTOR", "SLUG"),
                    help="print the sanctioned path for ACTOR/SLUG")
    ap.add_argument("--check", metavar="PATH", help="exit 1 if PATH is not sanctioned")
    ap.add_argument("--audit", action="store_true",
                    help="classify every registered worktree as sanctioned or stray")
    ap.add_argument("--check-cwd", action="store_true",
                    help="exit 1 if the CURRENT worktree is not sanctioned")
    ap.add_argument("--install-hooks", action="store_true",
                    help="point core.hooksPath at .githooks (enables the LLM-agnostic gate)")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)

    if args.install_hooks:
        root = canonical_repo_root()
        hooks_dir = root / ".githooks"
        if not hooks_dir.is_dir():
            print(f"no .githooks directory at {hooks_dir}")
            return 1
        prev = subprocess.run(  # nosec B603 B607 -- fixed argv, no shell
            ["git", "-C", str(root), "config", "--get", "core.hooksPath"],
            capture_output=True, text=True, timeout=30).stdout.strip()
        subprocess.run(  # nosec B603 B607 -- fixed argv, no shell
            ["git", "-C", str(root), "config", "core.hooksPath", ".githooks"],
            capture_output=True, text=True, timeout=30, check=True)
        # chmod is a no-op on Windows and required on POSIX.
        for hook in hooks_dir.iterdir():
            if hook.is_file():
                try:
                    hook.chmod(hook.stat().st_mode | 0o111)
                except OSError:
                    pass
        print(f"core.hooksPath: {prev or '(unset)'} -> .githooks")
        if prev and not (root / prev).exists() and prev != ".githooks":
            print(f"  note: previous value {prev!r} did not exist — hooks were disabled")
        return 0

    if args.check_cwd:
        here = Path.cwd()
        # The main checkout is not a worktree and is never a violation.
        if here.resolve() == canonical_repo_root().resolve():
            return 0
        if is_sanctioned(here):
            return 0
        print(describe_violation(here))
        return 1

    if args.path:
        actor, slug = args.path
        print(worktree_path(actor, slug))
        return 0

    if args.check:
        ok = is_sanctioned(args.check)
        if args.json:
            print(json.dumps({"path": args.check, "sanctioned": ok}))
        elif not ok:
            print(describe_violation(args.check))
        return 0 if ok else 1

    if args.audit:
        root = canonical_repo_root()
        out = subprocess.run(  # nosec B603 B607 -- fixed argv, no shell
            ["git", "-C", str(root), "worktree", "list", "--porcelain"],
            capture_output=True, text=True, timeout=60,
        )
        paths = [ln[len("worktree "):].strip()
                 for ln in out.stdout.splitlines() if ln.startswith("worktree ")]
        # The main checkout is not a stray.
        rows = [{"path": p, "sanctioned": is_sanctioned(p, root)}
                for p in paths if Path(p).resolve() != root.resolve()]
        stray = [r for r in rows if not r["sanctioned"]]
        if args.json:
            print(json.dumps({"total": len(rows), "stray": len(stray), "worktrees": rows},
                             indent=2))
        else:
            print(f"{len(rows)} worktrees, {len(stray)} stray")
            for r in stray:
                print(f"  STRAY {r['path']}")
        return 0

    ap.print_help()
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
