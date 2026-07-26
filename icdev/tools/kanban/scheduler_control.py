# CUI // SP-CTI
"""Kanban scheduler pause control — two manual switches, one answer.

Either of these stops dispatch, and ``should_pause()`` reports which:

1. **Sentinel file** (``mode="manual"``) — ``data/kanban_scheduler.paused``,
   written by the dashboard "Pause Scheduler" button and :func:`pause`.
   Cross-process by virtue of being on disk. Auto-expires after
   ``KANBAN_PAUSE_MAX_MINUTES`` (default 120) so a crashed pipeline cannot wedge
   the scheduler forever.
2. **Runner-pause lease** (``mode="session"``) — the ``kanban:runner:global``
   resource, claimed by ``tools/kanban/cli.py --pause-runner`` for a session
   doing interactive work. Expires with its own lease TTL.

Both arms are explicit opt-ins. Auto-pause on *any* detected interactive session
was removed on 2026-05-30 because kanban merges use temporary git worktrees, so
the main working tree is never touched; ``KANBAN_AUTO_PAUSE`` remains only for
backward compatibility and is not consulted by :func:`should_pause`.

The lease arm was added on 2026-07-26 after ``--pause-runner`` was found to be a
no-op: it took the lease, printed "RUNNER PAUSED", returned 0, and the scheduler
— which read only the sentinel — kept dispatching. During one interactive
session that demoted two already-merged tasks from ``done`` to ``backlog`` and
auto-decomposed them into subtasks asking for shipped work to be rebuilt, while
manual board corrections were reverted minutes after being made. If you add a
third way to pause, wire it in here; a pause control that reports success
without pausing is worse than no pause control at all.

Scheduler integration: at the top of each cycle call ``should_pause()`` and skip
the cycle (sleep + continue) when it returns truthy.
"""
from __future__ import annotations

import json
import os
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

_ROOT = Path(__file__).resolve().parents[2]
_FLAG = Path(os.environ.get("KANBAN_PAUSE_FLAG", str(_ROOT / "data" / "kanban_scheduler.paused")))


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _stamp() -> str:
    return _now().strftime("%Y-%m-%dT%H:%M:%SZ")


def _max_minutes() -> int:
    try:
        return int(os.environ.get("KANBAN_PAUSE_MAX_MINUTES", "120"))
    except ValueError:
        return 120


def _auto_enabled() -> bool:
    return os.environ.get("KANBAN_AUTO_PAUSE", "true").lower() in ("1", "true", "yes")


# ── Manual flag ────────────────────────────────────────────────────────────────
def _flag_meta() -> dict | None:
    if not _FLAG.exists():
        return None
    try:
        return json.loads(_FLAG.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _flag_is_stale(meta: dict) -> bool:
    since = meta.get("since")
    if not since:
        return False
    try:
        ts = datetime.fromisoformat(since.replace("Z", "+00:00"))
    except Exception:
        return False
    return (_now() - ts).total_seconds() / 60.0 > _max_minutes()


def manual_paused() -> bool:
    """True when the manual sentinel flag is set and not stale."""
    meta = _flag_meta()
    if meta is None:
        return False
    if _flag_is_stale(meta):
        try:
            _FLAG.unlink()
        except FileNotFoundError:
            pass
        return False
    return True


def pause(actor: str = "dashboard", reason: str = "") -> dict:
    """Create the manual pause flag. Idempotent."""
    _FLAG.parent.mkdir(parents=True, exist_ok=True)
    meta = {"actor": actor, "reason": reason, "since": _stamp()}
    _FLAG.write_text(json.dumps(meta), encoding="utf-8")
    return {"paused": True, "mode": "manual", **meta}


def resume(actor: str = "dashboard") -> dict:
    """Remove the manual pause flag. Idempotent."""
    try:
        _FLAG.unlink()
    except FileNotFoundError:
        pass
    return {"paused": False, "resumed_by": actor}


# ── Automatic (pipeline-aware) pause ────────────────────────────────────────────
def active_interactive_sessions() -> list[dict]:
    """Active non-kanban agent sessions (interactive Claude/Cursor work).

    Returns [] if the coordination registry is unavailable.
    """
    try:
        from tools.coordination import session_registry as reg  # noqa: PLC0415
        return [s for s in reg.list_active()
                if (s.get("agent_type") or "").lower() not in ("kanban", "scheduler")]
    except Exception:
        return []


def auto_paused() -> bool:
    """True when auto-pause is enabled and an interactive session is active."""
    if not _auto_enabled():
        return False
    return len(active_interactive_sessions()) > 0


# ── CLI session pause (lease-backed) ──────────────────────────────────────────
#: Resource claimed by ``tools/kanban/cli.py --pause-runner``. Kept in sync with
#: ``cli.py::_RUNNER_PAUSE_RESOURCE`` by ``test_cli_pause_resource_matches``.
RUNNER_PAUSE_RESOURCE = "kanban:runner:global"


def session_paused() -> Optional[dict]:
    """Holder of the CLI runner-pause lease, or None.

    ``--pause-runner`` claims :data:`RUNNER_PAUSE_RESOURCE` for the session doing
    interactive work. The lease carries its own TTL, so an abandoned session
    releases the scheduler on expiry without anyone cleaning up.
    """
    try:
        from tools.coordination import leases  # noqa: PLC0415
        return leases.holder(RUNNER_PAUSE_RESOURCE)
    except Exception:
        # Coordination unavailable must not wedge the scheduler on.
        return None


# ── Combined ─────────────────────────────────────────────────────────────────
def should_pause() -> dict:
    """Single check for the scheduler loop.

    Returns ``{paused, mode, ...}`` so the scheduler can log *why* it skipped.

    Two independent manual switches, either of which stops dispatch:

    * ``manual`` — the sentinel file, written by the dashboard "Pause Scheduler"
      button and :func:`pause`. Expires after ``KANBAN_PAUSE_MAX_MINUTES``.
    * ``session`` — the ``kanban:runner:global`` lease, claimed by
      ``tools/kanban/cli.py --pause-runner`` for a session doing interactive
      work. Expires with the lease TTL.

    The lease arm exists because it previously did not: ``--pause-runner``
    printed "RUNNER PAUSED" and returned 0 while this function looked only at
    the sentinel, so dispatch continued. On 2026-07-26 that silently demoted two
    already-merged tasks from ``done`` to ``backlog`` and auto-decomposed them
    into subtasks asking for shipped work to be rebuilt. A pause control that
    reports success without pausing is worse than no pause control.

    Auto-pause on *any* active interactive session was removed separately
    (2026-05-30) because kanban merges now use temporary git worktrees; that is
    a different mechanism from the explicit opt-in lease read here.
    """
    if manual_paused():
        return {"paused": True, "mode": "manual", **(_flag_meta() or {})}
    held = session_paused()
    if held:
        return {
            "paused": True,
            "mode": "session",
            "holder_session": held.get("holder_session"),
            "intent": held.get("intent"),
            "reason": "kanban:runner:global lease held (cli --pause-runner)",
        }
    return {"paused": False, "mode": ""}


def is_paused() -> bool:
    """Convenience boolean for the scheduler loop."""
    return should_pause()["paused"]


def status() -> dict:
    """Full status for the dashboard (manual flag + auto state)."""
    sp = should_pause()
    return {
        "paused": sp["paused"],
        "mode": sp["mode"],
        "manual": manual_paused(),
        "session_lease": session_paused(),
        "auto_enabled": _auto_enabled(),
        "active_interactive_sessions": len(active_interactive_sessions()),
        "detail": sp,
    }


@contextmanager
def paused(reason: str = "pipeline", actor: str = "pipeline"):
    """Pause the scheduler around a pipeline step; resume on exit.

    Only resumes if *this* call created the pause (won't clobber a pre-existing
    manual pause set by someone else).

        with scheduler_control.paused("merging aiify-feature"):
            run_merge()
    """
    pre_existing = manual_paused()
    if not pre_existing:
        pause(actor=actor, reason=reason)
    try:
        yield
    finally:
        if not pre_existing:
            resume(actor=actor)


if __name__ == "__main__":
    import sys
    cmd = sys.argv[1] if len(sys.argv) > 1 else "status"
    fn = {"pause": pause, "resume": resume, "status": status}.get(cmd, status)
    print(json.dumps(fn(), indent=2))
