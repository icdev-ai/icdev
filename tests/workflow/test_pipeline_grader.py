"""``make_pipeline_grader`` — the delivery-pipeline gates as a rubric grader.

Collaborators (``validate_working_tree``, ``review_conformance``) are patched
in the grader's module namespace, so no real gates, git, or LLM run here.
"""

import pytest

from icdev.tools.workflow import pipeline_grader as pg
from icdev.tools.llm.agent_loop import RubricVerdict


@pytest.fixture
def patch_collab(monkeypatch):
    """Patch the two collaborators; return setters for their return values."""
    state = {
        "vwt": (True, "all gates passed", {}),
        "conf": {"status": "pass", "review_passed": True, "findings": [], "reason": "ok"},
        "vwt_calls": [],
        "conf_calls": [],
    }

    # Signature tracks validated_commit.validate_working_tree. A stub that drops a
    # real parameter turns every call into a TypeError the grader swallows as
    # ``grader_error`` — which is how ``budget_sec`` went untested after it was
    # added upstream. Record it too, so the next addition fails loudly instead.
    def _vwt(cwd, modified_files=None, compare_to_main=True, run_e2e=True,
             run_companion=True, budget_sec=None):
        state["vwt_calls"].append(
            {"cwd": cwd, "files": modified_files, "run_e2e": run_e2e,
             "companion": run_companion, "budget_sec": budget_sec}
        )
        if isinstance(state["vwt"], Exception):
            raise state["vwt"]
        return state["vwt"]

    def _conf(task_id, changed_files=None, **kw):
        state["conf_calls"].append({"task_id": task_id, "files": changed_files})
        return state["conf"]

    monkeypatch.setattr(pg, "validate_working_tree", _vwt)
    monkeypatch.setattr(pg, "review_conformance", _conf)
    return state


def test_all_pass_is_satisfied(patch_collab):
    grader = pg.make_pipeline_grader("/wt", "t-1", ["a.py"])
    grade = grader(None)
    assert grade.verdict == RubricVerdict.satisfied
    assert "passed" in grade.feedback.lower()
    # e2e off by default; companion never run inside grading
    assert patch_collab["vwt_calls"][0]["run_e2e"] is False
    assert patch_collab["vwt_calls"][0]["companion"] is False


def test_gate_failure_is_needs_revision_with_detail(patch_collab):
    patch_collab["vwt"] = (
        False,
        "coherence gate failed",
        {"codelens_passed": True, "coherence_passed": False, "coherence_violations": 3},
    )
    grader = pg.make_pipeline_grader("/wt", "t-1", ["a.py"])
    grade = grader(None)
    assert grade.verdict == RubricVerdict.needs_revision
    assert "coherence gate failed" in grade.feedback
    assert "3 violation" in grade.feedback


def test_conformance_false_is_needs_revision_with_unmet(patch_collab):
    patch_collab["conf"] = {
        "status": "fail",
        "review_passed": False,
        "reason": "2 unmet/drifted criteria",
        "findings": [
            {"criterion": "has docstring", "met": True},
            {"criterion": "handles negatives", "met": False, "note": "no test"},
        ],
    }
    grader = pg.make_pipeline_grader("/wt", "t-1", ["a.py"])
    grade = grader(None)
    assert grade.verdict == RubricVerdict.needs_revision
    assert "Conformance review failed" in grade.feedback
    assert "handles negatives" in grade.feedback
    assert "no test" in grade.feedback


def test_conformance_none_does_not_block(patch_collab):
    # reviewer couldn't judge (no criteria / no diff / LLM down) -> None, not False
    patch_collab["conf"] = {"status": "not_run", "review_passed": None, "findings": [], "reason": "x"}
    grader = pg.make_pipeline_grader("/wt", "t-1", ["a.py"])
    grade = grader(None)
    assert grade.verdict == RubricVerdict.satisfied
    assert "not evaluated" in grade.feedback


def test_run_conformance_false_skips_reviewer(patch_collab):
    grader = pg.make_pipeline_grader("/wt", "t-1", ["a.py"], run_conformance=False)
    grade = grader(None)
    assert grade.verdict == RubricVerdict.satisfied
    assert patch_collab["conf_calls"] == []


def test_modified_files_callable_resolved_at_grade_time(patch_collab):
    resolved = []

    def _files():
        resolved.append(1)
        return ["late.py"]

    grader = pg.make_pipeline_grader("/wt", "t-1", _files)
    grader(None)
    assert resolved == [1]
    assert patch_collab["vwt_calls"][0]["files"] == ["late.py"]
    assert patch_collab["conf_calls"][0]["files"] == ["late.py"]


def test_gate_suite_exception_is_grader_error(patch_collab):
    patch_collab["vwt"] = RuntimeError("worktree vanished")
    grader = pg.make_pipeline_grader("/wt", "t-1", ["a.py"])
    grade = grader(None)
    assert grade.verdict == RubricVerdict.grader_error
    assert "worktree vanished" in grade.feedback


def test_run_e2e_passthrough(patch_collab):
    grader = pg.make_pipeline_grader("/wt", "t-1", ["a.py"], run_e2e=True)
    grader(None)
    assert patch_collab["vwt_calls"][0]["run_e2e"] is True


def test_budget_sec_passthrough(patch_collab):
    """The kanban runner derives this from the task's dispatch budget; if it stops
    reaching the gate suite, validation can outspend the build it is judging."""
    grader = pg.make_pipeline_grader("/wt", "t-1", ["a.py"], budget_sec=42.0)
    grader(None)
    assert patch_collab["vwt_calls"][0]["budget_sec"] == 42.0


def test_budget_sec_defaults_to_none(patch_collab):
    grader = pg.make_pipeline_grader("/wt", "t-1", ["a.py"])
    grader(None)
    assert patch_collab["vwt_calls"][0]["budget_sec"] is None
