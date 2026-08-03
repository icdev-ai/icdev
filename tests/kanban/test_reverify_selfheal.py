"""Tests for the watcher's bounded re-verify self-heal (kpr-rvfy-03).

The dangerous shape this guards: re-verification writes `review_passed` NULL, and
`_enforced_done_ok` reads NULL as "not judged, allowed". So appending a row CLEARS
whatever the previous one said. If the self-heal ever fired on a conformance
failure it would silently merge a PR that failed review — turning a gate into a
formality. Most of these tests exist to prove it does not.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from tools.ci import pr_watcher as pw


# ── reverify_is_allowed — the safety rule ───────────────────────────────────

def test_refuses_to_launder_a_conformance_failure():
    ok, why = pw.reverify_is_allowed(
        {"result": "failed", "review_passed": 0}, allow_when_missing=True)
    assert ok is False
    assert "launder" in why


def test_refuses_when_never_verified_by_default():
    """No row = never judged. Inventing a pass would merge an unreviewed PR."""
    ok, why = pw.reverify_is_allowed(None, allow_when_missing=False)
    assert ok is False
    assert "never judged" in why


def test_allows_when_never_verified_only_if_opted_in():
    ok, _ = pw.reverify_is_allowed(None, allow_when_missing=True)
    assert ok is True


def test_refuses_when_already_passing():
    ok, why = pw.reverify_is_allowed(
        {"result": "passed", "review_passed": None}, allow_when_missing=False)
    assert ok is False
    assert "nothing to refresh" in why


@pytest.mark.parametrize("result", ["failed", "pending", ""])
def test_allows_refreshing_a_non_conformance_failure(result):
    """The real case: 'No git commits found' from a daemon restart mid-flight."""
    ok, why = pw.reverify_is_allowed(
        {"result": result, "review_passed": None}, allow_when_missing=False)
    assert ok is True
    assert "not a conformance failure" in why


def test_review_passed_none_is_not_treated_as_zero():
    """NULL means 'not judged', which is not the same as 'judged and failed'."""
    ok, _ = pw.reverify_is_allowed(
        {"result": "failed", "review_passed": None}, allow_when_missing=False)
    assert ok is True


# ── _maybe_reverify — the bound ─────────────────────────────────────────────

class _Cur:
    def __init__(self, row):
        self._row = row

    def execute(self, *a, **k):
        return self

    def fetchone(self):
        return self._row


def _conn_factory(row):
    return lambda: SimpleNamespace(cursor=lambda: _Cur(row))


def _watcher(monkeypatch, *, config=None, verdict=None, written=True):
    w = pw.PRWatcher(config=config if config is not None else {})
    calls = []

    import tools.kanban.reverify as rvmod

    def _fake(task_id, get_connection, **kw):
        calls.append(task_id)
        return verdict or {"result": "passed", "written": written, "reason": "r"}

    monkeypatch.setattr(rvmod, "reverify", _fake)
    w._calls = calls
    return w


STALE = {"result": "failed", "review_passed": None, "reason": "No git commits found"}


def test_reverifies_once_then_stops(monkeypatch):
    w = _watcher(monkeypatch)
    conn = _conn_factory(STALE)
    assert w._maybe_reverify(conn, "t1") is True
    assert w._maybe_reverify(conn, "t1") is False, "must not re-verify past the cap"
    assert w._calls == ["t1"]


def test_cap_is_per_task_not_global(monkeypatch):
    w = _watcher(monkeypatch)
    conn = _conn_factory(STALE)
    w._maybe_reverify(conn, "t1")
    assert w._maybe_reverify(conn, "t2") is True
    assert w._calls == ["t1", "t2"]


def test_cap_is_configurable(monkeypatch):
    w = _watcher(monkeypatch, config={"reverify_max_attempts_per_task": 2})
    conn = _conn_factory(STALE)
    assert w._maybe_reverify(conn, "t1") is True
    assert w._maybe_reverify(conn, "t1") is True
    assert w._maybe_reverify(conn, "t1") is False


def test_disabled_by_config(monkeypatch):
    w = _watcher(monkeypatch, config={"reverify_on_hold": False})
    assert w._maybe_reverify(_conn_factory(STALE), "t1") is False
    assert w._calls == []


def test_conformance_failure_is_never_reverified(monkeypatch):
    w = _watcher(monkeypatch)
    conn = _conn_factory({"result": "failed", "review_passed": 0})
    assert w._maybe_reverify(conn, "t1") is False
    assert w._calls == []


def test_missing_row_blocked_by_default(monkeypatch):
    w = _watcher(monkeypatch)
    assert w._maybe_reverify(_conn_factory(None), "t1") is False
    assert w._calls == []


def test_missing_row_allowed_when_opted_in(monkeypatch):
    w = _watcher(monkeypatch, config={"reverify_when_missing": True})
    assert w._maybe_reverify(_conn_factory(None), "t1") is True


def test_dry_run_does_not_write(monkeypatch):
    w = pw.PRWatcher(config={}, dry_run=True)
    import tools.kanban.reverify as rvmod
    calls = []
    monkeypatch.setattr(rvmod, "reverify",
                        lambda *a, **k: calls.append(1) or {"written": True})
    assert w._maybe_reverify(_conn_factory(STALE), "t1") is False
    assert calls == []


def test_reverify_exception_never_stops_the_poll(monkeypatch):
    w = pw.PRWatcher(config={})
    import tools.kanban.reverify as rvmod

    def _boom(*a, **k):
        raise RuntimeError("git exploded")

    monkeypatch.setattr(rvmod, "reverify", _boom)
    assert w._maybe_reverify(_conn_factory(STALE), "t1") is False


def test_returns_false_when_nothing_was_written(monkeypatch):
    """No new row means no point re-checking the gate."""
    w = _watcher(monkeypatch, verdict={"result": "failed", "written": False, "reason": "r"})
    assert w._maybe_reverify(_conn_factory(STALE), "t1") is False


def test_unreadable_verification_is_treated_as_missing(monkeypatch):
    """A DB error must not be mistaken for 'no conformance failure'."""
    def _bad():
        raise RuntimeError("db down")
    assert pw._latest_verification(_bad, "t1") is None
    w = _watcher(monkeypatch)
    assert w._maybe_reverify(_bad, "t1") is False   # missing => blocked by default
