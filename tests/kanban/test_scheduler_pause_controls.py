# CUI // SP-CTI
"""Both scheduler pause switches must actually stop dispatch.

Regression guard for the 2026-07-26 defect: ``tools/kanban/cli.py --pause-runner``
acquired the ``kanban:runner:global`` lease, printed "RUNNER PAUSED" and returned
0, while ``scheduler_control.should_pause()`` consulted only the sentinel file.
The scheduler kept dispatching. Because the CLI reported success, the failure was
invisible until already-merged tasks started being demoted from ``done`` back to
``backlog`` and re-decomposed into subtasks for shipped work.

The asymmetry that made it possible — one module writes a lease, another reads a
file, nothing checks they agree — is what these tests pin down.
"""
from __future__ import annotations

import importlib

import pytest

from tools.kanban import scheduler_control as sc


@pytest.fixture
def isolated_flag(tmp_path, monkeypatch):
    """Point the sentinel at a temp path so tests never touch the real one.

    The module-level ``_FLAG`` constant became ``_flag_path()`` when the sentinel
    was re-anchored to the canonical repo root (a worktree resolved it to its own
    root and so could not see the pause). It is cached, so the cache is cleared
    around the override.
    """
    flag = tmp_path / "kanban_scheduler.paused"
    monkeypatch.setenv("KANBAN_PAUSE_FLAG", str(flag))
    sc._flag_path.cache_clear()
    yield flag
    sc._flag_path.cache_clear()


@pytest.fixture
def no_lease(monkeypatch):
    """Default the lease arm to 'not held' so sentinel tests are unambiguous."""
    monkeypatch.setattr(sc, "session_paused", lambda: None)


# ── The defect ────────────────────────────────────────────────────────────────


def test_cli_pause_resource_matches_the_resource_the_scheduler_reads():
    """The whole bug in one assertion: the two modules must name one resource.

    ``cli.py`` writes the lease and ``scheduler_control.py`` reads it. If these
    constants ever drift apart, ``--pause-runner`` silently stops working again.
    """
    cli = importlib.import_module("tools.kanban.cli")
    assert cli._RUNNER_PAUSE_RESOURCE == sc.RUNNER_PAUSE_RESOURCE


def test_runner_pause_lease_stops_dispatch(isolated_flag, monkeypatch):
    """A held lease must pause the scheduler even with no sentinel file."""
    assert not isolated_flag.exists()
    monkeypatch.setattr(
        sc, "session_paused",
        lambda: {"holder_session": "sess-abc", "intent": "cli-session"},
    )

    result = sc.should_pause()

    assert result["paused"] is True
    assert result["mode"] == "session"
    assert result["holder_session"] == "sess-abc"
    assert sc.is_paused() is True


def test_no_lease_and_no_sentinel_means_dispatch_proceeds(isolated_flag, no_lease):
    result = sc.should_pause()
    assert result["paused"] is False
    assert result["mode"] == ""
    assert sc.is_paused() is False


# ── The pre-existing arm must keep working ────────────────────────────────────


def test_sentinel_file_still_pauses(isolated_flag, no_lease):
    sc.pause(actor="dashboard", reason="button")
    result = sc.should_pause()
    assert result["paused"] is True
    assert result["mode"] == "manual"
    assert result["actor"] == "dashboard"


def test_sentinel_wins_when_both_are_set(isolated_flag, monkeypatch):
    """Reporting is deterministic when both switches are on."""
    monkeypatch.setattr(sc, "session_paused", lambda: {"holder_session": "sess-x"})
    sc.pause(actor="dashboard", reason="button")
    assert sc.should_pause()["mode"] == "manual"


def test_resume_clears_only_the_sentinel(isolated_flag, monkeypatch):
    """Releasing one arm must not release the other."""
    monkeypatch.setattr(sc, "session_paused", lambda: {"holder_session": "sess-x"})
    sc.pause(actor="dashboard")
    sc.resume(actor="dashboard")

    result = sc.should_pause()
    assert result["paused"] is True, "lease must still hold the scheduler"
    assert result["mode"] == "session"


def test_stale_sentinel_is_ignored(isolated_flag, no_lease, monkeypatch):
    """An abandoned pause must not wedge the scheduler forever."""
    monkeypatch.setenv("KANBAN_PAUSE_MAX_MINUTES", "0")
    sc.pause(actor="crashed-pipeline")
    assert sc.should_pause()["paused"] is False
    assert not isolated_flag.exists(), "stale flag should be cleaned up on read"


# ── Failure modes must fail open, not wedge ───────────────────────────────────


def test_coordination_failure_does_not_wedge_the_scheduler(isolated_flag, monkeypatch):
    """If the lease store is unreachable, dispatch continues rather than halting.

    A coordination outage that silently paused every scheduler forever would be a
    worse failure than the one this module guards against.

    Patched via ``importlib`` + ``setattr`` rather than a dotted string: the root
    ``tools`` package is a shim onto ``icdev.tools``, so a string target resolves
    to a different module object than the one ``session_paused`` imports.
    """
    leases = importlib.import_module("tools.coordination.leases")

    def _boom(resource):
        raise RuntimeError("lease store unavailable")

    monkeypatch.setattr(leases, "holder", _boom)

    assert sc.session_paused() is None, "a lease-store error must not look like a pause"
    assert sc.should_pause()["paused"] is False


def test_status_reports_both_arms(isolated_flag, no_lease):
    """The dashboard must be able to show *why* the scheduler is paused."""
    st = sc.status()
    assert "manual" in st
    assert "session_lease" in st
    assert st["detail"]["mode"] == ""
