# CUI // SP-CTI
"""Kanban scheduler pause control — manual switch only.

The scheduler can be paused via the dashboard "Pause Scheduler" button
(cross-process: dashboard writes a sentinel file under ``data/``, scheduler
reads it).

Auto-pause was removed on 2026-05-30 because kanban merges now use temporary
git worktrees — the main repository working tree is never touched, so there
is no risk of stash/checkout churn clobbering in-flight human work.
The ``KANBAN_AUTO_PAUSE`` env var still exists for backward compatibility
but is no longer consulted by ``should_pause()``.

Scheduler integration: at the top of each cycle call ``should_pause()`` and skip
the cycle (sleep + continue) when it returns truthy.

Auto-expiry: a manual pause older than ``KANBAN_PAUSE_MAX_MINUTES`` (default 120)
is treated as stale and ignored, so a crashed pipeline can't wedge the scheduler
forever.
"""
from __future__ import annotations

import json
import os
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

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


# ── Combined ─────────────────────────────────────────────────────────────────
def should_pause() -> dict:
    """Single check for the scheduler loop.

    Returns ``{paused, mode, ...}`` so the scheduler can log *why* it skipped.

    Auto-pause was removed because kanban merges now use temporary git
    worktrees — the main repository working tree is never touched, so
    there is no risk of stash/checkout churn clobbering in-flight work.
    Only the manual pause flag (dashboard button) can stop dispatch.
    """
    if manual_paused():
        return {"paused": True, "mode": "manual", **(_flag_meta() or {})}
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
