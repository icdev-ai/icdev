# CUI // SP-CTI
"""Conformance Review gate — degrades gracefully, never raises, never blocks itself."""
import importlib

cr = importlib.import_module("tools.testing.conformance_reviewer")


def _task(monkeypatch, ac="AC-1: must add a foo endpoint"):
    monkeypatch.setattr(cr, "_load_task", lambda tid: {
        "title": "Add foo", "description": "Add a foo endpoint", "acceptance_criteria": ac,
    })
    # avoid real git — force the changed-files fallback
    monkeypatch.setattr(cr, "_branch_diff", lambda tid: "")


def test_pass_verdict(monkeypatch):
    _task(monkeypatch)
    res = cr.review_conformance(
        "t1", changed_files=["tools/foo.py"],
        llm_caller=lambda p, t: '{"pass": true, "gap_findings": []}',
    )
    assert res["status"] == "pass"
    assert res["review_passed"] is True


def test_fail_verdict_with_findings(monkeypatch):
    _task(monkeypatch)
    res = cr.review_conformance(
        "t1", changed_files=["tools/bar.py"],
        llm_caller=lambda p, t: '{"pass": false, "gap_findings": [{"criterion": "AC-1", "met": false, "note": "no endpoint"}]}',
    )
    assert res["status"] == "fail"
    assert res["review_passed"] is False
    assert len(res["findings"]) == 1 and res["findings"][0]["met"] is False


def test_no_acceptance_criteria_is_not_run(monkeypatch):
    _task(monkeypatch, ac="")
    res = cr.review_conformance("t1", changed_files=["x.py"], llm_caller=lambda p, t: "should not be called")
    assert res["status"] == "not_run"
    assert res["review_passed"] is None
    assert "acceptance criteria" in res["reason"]


def test_no_diff_no_files_is_not_run(monkeypatch):
    _task(monkeypatch)
    res = cr.review_conformance("t1", changed_files=None, llm_caller=lambda p, t: "x")
    assert res["status"] == "not_run"


def test_llm_raises_is_not_run_never_propagates(monkeypatch):
    _task(monkeypatch)

    def _boom(p, t):
        raise RuntimeError("llm down")

    res = cr.review_conformance("t1", changed_files=["a.py"], llm_caller=_boom)
    assert res["status"] == "not_run"
    assert res["review_passed"] is None


def test_unparseable_output_is_not_run(monkeypatch):
    _task(monkeypatch)
    res = cr.review_conformance("t1", changed_files=["a.py"], llm_caller=lambda p, t: "not json at all")
    assert res["status"] == "not_run"


def test_task_not_found_is_not_run(monkeypatch):
    monkeypatch.setattr(cr, "_load_task", lambda tid: None)
    res = cr.review_conformance("nope", changed_files=["a.py"], llm_caller=lambda p, t: "x")
    assert res["status"] == "not_run"
    assert "not found" in res["reason"]
