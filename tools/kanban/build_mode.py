# CUI // SP-CTI
"""Manual Build mode — the board keeps its state machine; the CLI does the work.

Two ways to stop the autonomous runner, and they are NOT the same thing:

  * **Pause Scheduler** (tools/kanban/scheduler_control.py) stops the whole cycle.
    Nothing promotes, nothing dispatches, nothing is reaped. The board freezes.

  * **Manual Build** (this module) stops only the DISPATCH. Backlog tasks still
    promote to `scheduled`, project cards still track progress, the board still
    tells you exactly where the work is — but no executor is spawned. A CLI
    session does the building.

Manual Build exists for the case the pause button handles badly: you want to do the
work yourself, and you still want the board to be the record of it. Pausing the
scheduler gives you a frozen board and a build nobody is tracking; you find out what
happened by reading git log.

## Why this is the difference that matters on a crash

A manual build that dies halfway leaves the task sitting in `in_progress`, with its
worktree, its branch, and its status intact. A fresh session runs
``python -m tools.kanban.cli --list --status in_progress`` and picks up exactly where
the last one stopped. Nothing is lost, because the board never stopped being the record.

That only holds if the stale-reaper does not come along and reset a human's in-flight
task to backlog after 30–80 minutes — which is what it does to a dead scheduler
subprocess, and which a long manual build looks exactly like from the outside. So in
Manual Build mode the reaper leaves manually-dispatched tasks alone
(kanban.py::_reap_stale_in_progress). It still reaps genuinely dead scheduler
subprocesses, because those really are dead.

## It does not expire

The pause flag goes stale after KANBAN_PAUSE_MAX_MINUTES so a crashed pipeline cannot
wedge the scheduler forever. Manual Build is the opposite kind of thing: it is a
deliberate statement about who is doing the work, and having it silently flip back to
automatic mid-build — spawning an agent into the worktree a human is standing in — is
precisely the collision it exists to prevent.

You turn it on. You turn it off. Nothing else turns it off for you.

Cross-process by design (dashboard checkbox, CLI, and scheduler are three processes):
the state is a sentinel file under ``data/``, exactly like the pause flag.
"""
from __future__ import annotations

import json
import os
import subprocess
from datetime import datetime, timezone
from pathlib import Path

from icdev.core.paths import repo_root
from typing import Any, Dict

def _main_checkout() -> Path:
    """The MAIN checkout, even when this module is imported from a worktree.

    `Path(__file__).parents[2]` is a self-root (xit-decl-03): it answers "which
    copy of this file am I", and in a git WORKTREE that is the worktree, not the
    repository the scheduler runs in. Manual Build is one GLOBAL state -- the
    scheduler either dispatches or it does not -- so a worktree asking must get
    the same answer as the main checkout, or the flag means nothing.

    MEASURED 2026-08-30: `--build-mode status` reported MANUAL from C:/AI/ICDev
    and AUTOMATIC from a worktree of it, in the same minute, with dispatch
    genuinely paused. An operator standing in a worktree is told the runner is
    live when it is not -- and, worse, `--build-mode manual` there writes a flag
    the scheduler will never read, so the pause silently does nothing.

    `git --git-common-dir` is the canonical answer: for a worktree it returns the
    MAIN repository's .git, for a normal checkout its own. Falling back to
    `icdev.core.paths.repo_root` keeps this working with no git at all (a source
    tarball, a container without the binary), which is the same posture the rest
    of the module takes: never raise, prefer the answer that keeps the scheduler
    running.

    THE FALLBACK IS `repo_root`, NOT A SELF-ROOT, and the first version of this
    fix got that wrong: it fell back to `Path(__file__).resolve().parents[2]` --
    reintroducing, as the safety net, the exact defect being repaired. The
    self-root census caught it (tools/kanban/build_mode.py:77, NEW), which is
    the census earning its place: a reviewer reading a commit titled "stop
    computing the root from this file's location" is not primed to notice the
    one line that still does.
    """
    here = repo_root(__file__)
    try:
        r = subprocess.run(
            ["git", "rev-parse", "--path-format=absolute", "--git-common-dir"],
            cwd=str(here), capture_output=True, text=True, timeout=10,
        )
        if r.returncode == 0 and r.stdout.strip():
            return Path(r.stdout.strip()).resolve().parent
    except Exception:  # noqa: BLE001 -- no git, or not a repo: repo_root still answers
        pass
    return here


_ROOT = _main_checkout()
_FLAG = Path(
    os.environ.get("KANBAN_BUILD_MODE_FLAG", str(_ROOT / "data" / "kanban_manual_build.json"))
)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _read() -> Dict[str, Any] | None:
    if not _FLAG.exists():
        return None
    try:
        data = json.loads(_FLAG.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:  # noqa: BLE001 — a corrupt flag must not wedge the scheduler
        return {}


def is_manual() -> bool:
    """True when the runner must NOT dispatch. Never raises.

    Fail-safe direction: if we cannot read the flag, we return False (automatic). An
    unreadable flag file must never silently stop every build on the board — the
    failure that is hard to notice is the one where nothing happens.
    """
    try:
        return _read() is not None
    except Exception:  # noqa: BLE001
        return False


def status() -> Dict[str, Any]:
    """Current mode, and who set it."""
    meta = _read()
    if meta is None:
        return {"manual": False, "mode": "automatic"}
    return {
        "manual": True,
        "mode": "manual",
        "since": meta.get("since"),
        "actor": meta.get("actor"),
        "reason": meta.get("reason"),
    }


def enable(actor: str = "dashboard", reason: str = "") -> Dict[str, Any]:
    """Turn Manual Build ON. Promotion and tracking continue; dispatch stops."""
    _FLAG.parent.mkdir(parents=True, exist_ok=True)
    _FLAG.write_text(
        json.dumps(
            {"since": _now_iso(), "actor": actor,
             "reason": reason or "manual build (CLI session builds; board still tracks)"},
            indent=2,
        ),
        encoding="utf-8", newline="",
    )
    return status()


def disable(actor: str = "dashboard") -> Dict[str, Any]:
    """Turn Manual Build OFF — back to the default, which is automatic build."""
    try:
        _FLAG.unlink()
    except FileNotFoundError:
        pass
    return {"manual": False, "mode": "automatic", "disabled_by": actor}


def set_manual(manual: bool, actor: str = "dashboard", reason: str = "") -> Dict[str, Any]:
    return enable(actor=actor, reason=reason) if manual else disable(actor=actor)
