# CUI // SP-CTI
"""Phase 1 — delivery-pipeline assembler (visualization) tests.

Schema-independent: a FakeConn routes queries by SQL substring so the test does
not couple to the conftest DB schema (root PG vs dev SQLite drift).
"""
import pytest

from tools.kanban import pipeline


# --- fakes -----------------------------------------------------------------
class _Cur:
    def __init__(self, rows):
        self._rows = rows

    def fetchone(self):
        return self._rows[0] if self._rows else None

    def fetchall(self):
        return self._rows


class _FakeConn:
    def __init__(self, task=None, verification=None, transitions=None):
        self.task = task
        self.verification = verification
        self.transitions = transitions or []

    def execute(self, sql, params=None):
        s = sql.lower()
        if "from kanban_tasks" in s:
            return _Cur([self.task] if self.task else [])
        if "from kanban_verifications" in s:
            return _Cur([self.verification] if self.verification else [])
        if "from kanban_status_transitions" in s:
            return _Cur(list(self.transitions))
        return _Cur([])

    def close(self):
        pass


def _patch_conn(monkeypatch, conn):
    # Shim-aware: tools.db.storage may resolve through the icdev.* redirect, so
    # grab the actual module object via importlib and patch it there.
    import importlib
    storage = importlib.import_module("tools.db.storage")
    monkeypatch.setattr(storage, "get_connection", lambda *a, **k: conn)


# --- status_to_stage -------------------------------------------------------
def test_status_to_stage_all_lifecycle_values():
    cases = {
        "suggested": "implement", "backlog": "implement", "scheduled": "implement",
        "in_progress": "implement", "decomposed": "implement",
        "needs_decomposition": "implement", "validating": "code_quality",
        "pr_opened": "pr", "changes_requested": "pr", "merge_conflict": "pr",
        "ci_failed": "ci", "done": "merged", "failed": "implement",
        "token_exhausted": "implement",
    }
    for status, stage in cases.items():
        assert pipeline.status_to_stage(status) == stage, status
    assert pipeline.status_to_stage(None) == "implement"
    assert pipeline.status_to_stage("bogus") == "implement"


def test_stage_list_is_the_canonical_nine():
    keys = [s["key"] for s in pipeline.load_stages()]
    assert keys == ["implement", "code_quality", "coherence", "conformance",
                    "unit_tests", "e2e", "pr", "ci", "merged"]
    # every stage carries a tooltip
    assert all(s.get("tooltip") for s in pipeline.load_stages())


# --- assemble --------------------------------------------------------------
def _stage(res, key):
    return next(s for s in res["stages"] if s["key"] == key)


def test_assemble_done_task_all_completed(monkeypatch):
    conn = _FakeConn(
        task={"id": "t-done", "status": "done", "branch_name": "kanban/t-done",
              "commit_summary": "abc did it", "files_changed": 3},
        verification={"task_id": "t-done", "codelens_passed": 1, "coherence_passed": 1,
                      "e2e_ran": 1, "e2e_passed": 1},
        transitions=[],
    )
    _patch_conn(monkeypatch, conn)
    res = pipeline.assemble("t-done")
    assert res["current_stage"] == "merged"
    assert all(s["state"] == "completed" for s in res["stages"])
    assert res["meta"]["branch_name"] == "kanban/t-done"


def test_assemble_validating_codelens_fail(monkeypatch):
    conn = _FakeConn(
        task={"id": "t-v", "status": "validating"},
        verification={"task_id": "t-v", "codelens_passed": 0, "ruff_issues": 4},
    )
    _patch_conn(monkeypatch, conn)
    res = pipeline.assemble("t-v")
    assert res["current_stage"] == "code_quality"
    assert res["branch_state"] == "validating"
    assert _stage(res, "code_quality")["state"] == "failed"
    assert "ruff:4" in _stage(res, "code_quality")["detail"]
    # downstream gate that never ran → pending (after current)
    assert _stage(res, "coherence")["state"] == "pending"


def test_assemble_pr_opened_positions(monkeypatch):
    conn = _FakeConn(
        task={"id": "t-pr", "status": "pr_opened"},
        verification={"task_id": "t-pr", "codelens_passed": 1, "coherence_passed": 1,
                      "e2e_ran": 0},
    )
    _patch_conn(monkeypatch, conn)
    res = pipeline.assemble("t-pr")
    assert res["current_stage"] == "pr"
    assert _stage(res, "code_quality")["state"] == "completed"
    assert _stage(res, "coherence")["state"] == "completed"
    # e2e didn't run (backend-only) → not_run, not failed
    assert _stage(res, "e2e")["state"] == "not_run"
    assert _stage(res, "pr")["state"] == "current"
    assert _stage(res, "ci")["state"] == "pending"
    assert _stage(res, "merged")["state"] == "pending"


def test_assemble_not_run_gates_when_no_verification(monkeypatch):
    conn = _FakeConn(task={"id": "t-x", "status": "in_progress"}, verification=None)
    _patch_conn(monkeypatch, conn)
    res = pipeline.assemble("t-x")
    # conformance (review_passed absent) + unit_tests (pytest_passed absent) → not_run
    assert _stage(res, "conformance")["state"] in ("not_run", "pending")
    assert _stage(res, "unit_tests")["state"] in ("not_run", "pending")


def test_assemble_transitions_ordered_incl_refusal(monkeypatch):
    conn = _FakeConn(
        task={"id": "t-t", "status": "validating"},
        transitions=[
            {"from_status": "in_progress", "to_status": "validating", "actor": "scheduler",
             "reason": "work done", "recorded_at": "2026-07-11T01:00:00"},
            {"from_status": "validating", "to_status": "REFUSED_done_unmerged",
             "actor": "scheduler", "reason": "not on origin/main",
             "recorded_at": "2026-07-11T01:05:00"},
        ],
    )
    _patch_conn(monkeypatch, conn)
    res = pipeline.assemble("t-t")
    tos = [t["to"] for t in res["transitions"]]
    assert tos == ["validating", "REFUSED_done_unmerged"]


def test_assemble_task_not_found(monkeypatch):
    _patch_conn(monkeypatch, _FakeConn(task=None))
    res = pipeline.assemble("nope")
    assert res.get("error") == "task_not_found"


def test_assemble_db_error_is_pipeline_error_not_not_found(monkeypatch):
    """A DB failure must surface as pipeline_error, never masquerade as
    task_not_found (regression: _fetch_row used to swallow the exception)."""
    class _RaisingConn:
        def execute(self, *a, **k):
            raise RuntimeError("db down")

        def close(self):
            pass

    _patch_conn(monkeypatch, _RaisingConn())
    res = pipeline.assemble("any-task")
    assert res.get("error") == "pipeline_error"
    assert res.get("error") != "task_not_found"
    assert "detail" in res


# --- live PR / CI ----------------------------------------------------------
def test_resolve_pr_live_none_on_gh_failure(monkeypatch):
    import tools.ci.pr_watcher as pw

    def _boom(*a, **k):
        raise RuntimeError("gh not on PATH")

    monkeypatch.setattr(pw, "list_pr_tasks", _boom)
    assert pipeline.resolve_pr_live("t-any") is None


def test_resolve_pr_live_none_when_no_pr(monkeypatch):
    import tools.ci.pr_watcher as pw
    monkeypatch.setattr(pw, "list_pr_tasks", lambda *a, **k: [])
    assert pipeline.resolve_pr_live("t-any") is None


def test_summarize_ci():
    assert pipeline._summarize_ci([{"conclusion": "SUCCESS"}]) == "passing"
    assert pipeline._summarize_ci([{"conclusion": "FAILURE"}]) == "failing"
    assert pipeline._summarize_ci([{"status": "IN_PROGRESS"}]) == "pending"
    assert pipeline._summarize_ci([]) == "unknown"
    assert pipeline._summarize_ci(None) == "unknown"


@pytest.mark.parametrize("val,exp", [
    (1, "pass"), (True, "pass"), ("1", "pass"),
    (0, "fail"), (False, "fail"), ("0", "fail"),
    (None, "not_run"), ("", "not_run"),
])
def test_norm_gate(val, exp):
    assert pipeline._norm_gate(val) == exp
