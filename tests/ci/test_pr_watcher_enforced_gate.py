# CUI // SP-CTI
"""pr_watcher enforced done-gate: auto-merge waits on ICDEV verification.

Under KANBAN_PIPELINE_ENFORCE, a kanban PR may auto-merge only after the task's
ICDEV done-verification (conformance + gates) has PASSED — not on CI green alone.
"""
import pytest

from tools.ci.pr_watcher import _enforced_done_ok


class _FakeCursor:
    def __init__(self, row):
        self._row = row

    def execute(self, sql, params=None):
        pass

    def fetchone(self):
        return self._row


class _FakeConn:
    def __init__(self, row):
        self._row = row

    def cursor(self):
        return _FakeCursor(self._row)


def _conn_factory(row):
    return lambda: _FakeConn(row)


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    monkeypatch.delenv("KANBAN_PIPELINE_ENFORCE", raising=False)
    yield


def test_enforcement_off_is_always_ok(monkeypatch):
    # No enforcement -> no new blocker, even with a failed verification row.
    ok, reason = _enforced_done_ok(_conn_factory({"result": "failed", "review_passed": 0}), "t1")
    assert ok is True
    assert "off" in reason


def test_enforced_passed_result_merges(monkeypatch):
    monkeypatch.setenv("KANBAN_PIPELINE_ENFORCE", "1")
    for res in ("pass", "passed", "bypassed"):
        ok, _ = _enforced_done_ok(_conn_factory({"result": res, "review_passed": 1}), "t1")
        assert ok is True, res


def test_enforced_failed_result_holds(monkeypatch):
    monkeypatch.setenv("KANBAN_PIPELINE_ENFORCE", "true")
    ok, reason = _enforced_done_ok(_conn_factory({"result": "failed", "review_passed": 1}), "t1")
    assert ok is False
    assert "result=failed" in reason


def test_enforced_conformance_false_holds(monkeypatch):
    monkeypatch.setenv("KANBAN_PIPELINE_ENFORCE", "1")
    # result looks ok but conformance explicitly failed -> hold
    ok, reason = _enforced_done_ok(_conn_factory({"result": "passed", "review_passed": 0}), "t1")
    assert ok is False
    assert "review_passed" in reason


def test_enforced_conformance_none_does_not_block(monkeypatch):
    # review_passed NULL = "couldn't judge" -> must NOT block (matches grader semantics)
    monkeypatch.setenv("KANBAN_PIPELINE_ENFORCE", "1")
    ok, _ = _enforced_done_ok(_conn_factory({"result": "passed", "review_passed": None}), "t1")
    assert ok is True


def test_enforced_no_verification_row_holds(monkeypatch):
    monkeypatch.setenv("KANBAN_PIPELINE_ENFORCE", "1")
    ok, reason = _enforced_done_ok(_conn_factory(None), "t1")
    assert ok is False
    assert "awaiting" in reason


def test_enforced_pending_result_holds(monkeypatch):
    monkeypatch.setenv("KANBAN_PIPELINE_ENFORCE", "1")
    ok, reason = _enforced_done_ok(_conn_factory({"result": "pending", "review_passed": None}), "t1")
    assert ok is False
    assert "not yet passed" in reason


def test_enforced_unreadable_holds_fail_closed(monkeypatch):
    monkeypatch.setenv("KANBAN_PIPELINE_ENFORCE", "1")

    def _boom():
        raise RuntimeError("db down")

    ok, reason = _enforced_done_ok(_boom, "t1")
    assert ok is False
    assert "holding" in reason


def test_enforced_tuple_row_supported(monkeypatch):
    # plain-cursor tuple rows (result, review_passed) also handled
    monkeypatch.setenv("KANBAN_PIPELINE_ENFORCE", "1")
    ok, _ = _enforced_done_ok(_conn_factory(("passed", 1)), "t1")
    assert ok is True
    ok2, _ = _enforced_done_ok(_conn_factory(("failed", 1)), "t1")
    assert ok2 is False
