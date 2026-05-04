"""Shared git helpers for ICDEV workflow tools."""
from __future__ import annotations

import subprocess
from typing import Optional

_default_branch_cache: Optional[str] = None


def default_branch(cwd: str | None = None) -> str:
    """Return the repository default branch name (main/master/trunk/dev).

    Probes in order:
    1. Symbolic ref of origin/HEAD (most reliable after a fetch)
    2. Common branch names: main, master, trunk, dev
    Caches result for the process lifetime.
    """
    global _default_branch_cache
    if _default_branch_cache:
        return _default_branch_cache

    kwargs: dict = {"capture_output": True, "text": True, "timeout": 10}
    if cwd:
        kwargs["cwd"] = cwd

    try:
        r = subprocess.run(
            ["git", "symbolic-ref", "refs/remotes/origin/HEAD"],
            **kwargs,
        )
        if r.returncode == 0:
            ref = r.stdout.strip()  # refs/remotes/origin/main
            branch = ref.split("/")[-1]
            if branch:
                _default_branch_cache = branch
                return branch
    except Exception:
        pass

    for candidate in ("main", "master", "trunk", "dev"):
        try:
            r = subprocess.run(
                ["git", "rev-parse", "--verify", candidate],
                **kwargs,
            )
            if r.returncode == 0:
                _default_branch_cache = candidate
                return candidate
        except Exception:
            pass

    _default_branch_cache = "main"
    return "main"
