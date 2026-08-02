# CUI // SP-CTI
"""A manual pause that lapses must say so.

The sentinel auto-expires after KANBAN_PAUSE_MAX_MINUTES (default 120) so a
crashed pipeline cannot wedge the scheduler forever. That is the right design.
What was wrong is that it happened in silence: manual_paused() unlinked the
stale flag and returned False, so dispatch resumed and nothing recorded it.

An overnight run was reported as paused after it had already resumed and
dispatched; the operator found tasks in flight and had no way to reconcile that
with the report. The module docstring already warns that "a pause control that
reports success without pausing is worse than no pause control at all" — this is
the temporal version of the same failure.

Every test redirects the sentinel to a scratch path via KANBAN_PAUSE_FLAG. None
of them touch the live board.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta, timezone

import pytest

from tools.kanban import scheduler_control as sc


@pytest.fixture
def flag(tmp_path, monkeypatch):
    """Point the sentinel at a scratch file, never the real one.

    The module-level ``_FLAG`` constant became the cached ``_flag_path()`` when
    the sentinel was re-anchored to the canonical repo root. Setting the removed
    attribute raised at fixture setup, so every test here errored — and had the
    monkeypatch been written with ``raising=False`` it would have passed the
    redirect straight through and paused the LIVE board instead. Override the
    documented ``KANBAN_PAUSE_FLAG`` escape hatch and clear the cache around it,
    matching ``tests/kanban/test_scheduler_pause_controls.py::isolated_flag``.
    """
    path = tmp_path / "kanban_scheduler.paused"
    monkeypatch.setenv("KANBAN_PAUSE_FLAG", str(path))
    sc._flag_path.cache_clear()
    yield path
    sc._flag_path.cache_clear()


def _age_flag(path, minutes: int) -> None:
    """Rewind the sentinel as if it had been written *minutes* ago.

    Both stamps move. ``expires_at`` was added when the deadline stopped being
    recomputed from the reader's own KANBAN_PAUSE_MAX_MINUTES (a worktree has no
    .env, so it expired pauses taken under a longer ceiling). Aging only
    ``since`` therefore aged nothing the module still reads: the pause stayed
    live no matter how far back it was pushed, and the expiry tests below could
    not fail.
    """
    meta = json.loads(path.read_text(encoding="utf-8"))
    delta = timedelta(minutes=minutes)
    meta["since"] = (datetime.now(timezone.utc) - delta).isoformat()
    if meta.get("expires_at"):
        deadline = datetime.fromisoformat(str(meta["expires_at"]).replace("Z", "+00:00"))
        meta["expires_at"] = (deadline - delta).strftime("%Y-%m-%dT%H:%M:%SZ")
    path.write_text(json.dumps(meta), encoding="utf-8")


def _capture_warnings():
    records: list = []

    class _Cap(logging.Handler):
        def emit(self, record):
            records.append(record.getMessage())

    handler = _Cap(level=logging.WARNING)
    sc.logger.addHandler(handler)
    return records, handler


# ---------------------------------------------------------------------------
# The silence
# ---------------------------------------------------------------------------

def test_expiry_is_announced(flag):
    """The regression. A lapse used to clear the flag and say nothing."""
    sc.pause(actor="overnight-session", reason="manual takeover")
    _age_flag(flag, sc._max_minutes() + 5)

    records, handler = _capture_warnings()
    try:
        assert sc.manual_paused() is False
    finally:
        sc.logger.removeHandler(handler)

    assert records, "a pause lapsing must not be silent"
    msg = " ".join(records)
    assert "EXPIRED" in msg
    assert "overnight-session" in msg, "say WHO set the pause that just lapsed"
    assert "manual takeover" in msg, "and why it was set"


def test_expiry_still_clears_the_flag(flag):
    """Announcing must not stop it expiring — that was the point of the ceiling."""
    sc.pause(actor="x")
    _age_flag(flag, sc._max_minutes() + 1)
    assert sc.manual_paused() is False
    assert not flag.exists(), "a crashed pipeline must not wedge the scheduler"


def test_a_live_pause_is_not_announced_or_cleared(flag):
    sc.pause(actor="x")
    records, handler = _capture_warnings()
    try:
        assert sc.manual_paused() is True
    finally:
        sc.logger.removeHandler(handler)
    assert not records, "a pause that is holding must not warn"
    assert flag.exists()


# ---------------------------------------------------------------------------
# Reporting remaining time
# ---------------------------------------------------------------------------

def test_status_reports_how_long_the_pause_has_left(flag):
    """Without this a caller cannot tell a pause that will hold from one about
    to lapse — which is exactly how the overnight report went wrong."""
    sc.pause(actor="x")
    st = sc.status()
    assert st["paused"] is True
    assert st["manual_expires_in_minutes"] == pytest.approx(sc._max_minutes(), abs=1)
    assert st["manual_max_minutes"] == sc._max_minutes()


def test_remaining_time_decreases_as_the_pause_ages(flag):
    sc.pause(actor="x")
    _age_flag(flag, 30)
    remaining = sc.status()["manual_expires_in_minutes"]
    assert remaining == pytest.approx(sc._max_minutes() - 30, abs=1)


def test_remaining_time_is_none_when_nothing_is_paused(flag):
    assert not flag.exists()
    assert sc.status()["manual_expires_in_minutes"] is None


def test_remaining_time_never_goes_negative(flag):
    sc.pause(actor="x")
    _age_flag(flag, sc._max_minutes() * 3)
    assert sc._minutes_remaining(sc._flag_meta() or {}) == 0.0


def test_malformed_since_does_not_break_status(flag):
    """A corrupt flag must degrade, not raise into a dashboard render."""
    sc.pause(actor="x")
    flag.write_text(json.dumps({"since": "not-a-timestamp", "actor": "x"}), encoding="utf-8")
    assert sc._minutes_remaining(sc._flag_meta()) is None
    sc.status()  # must not raise


def test_resume_clears_everything(flag):
    sc.pause(actor="x")
    sc.resume(actor="x")
    assert sc.manual_paused() is False
    assert sc.status()["manual_expires_in_minutes"] is None
