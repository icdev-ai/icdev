# CUI // SP-CTI
"""Tests for the done-artifact auditor (tools/kanban/done_artifact_auditor.py).

Post-process that verifies tasks flagged `done` actually produced the artifacts
their descriptions claim, on the working branch. Born from the ACE incident:
42/42 tasks "done" but most artifacts missing/divergent/unmerged.
"""
from __future__ import annotations

import importlib

import pytest

aud = importlib.import_module("tools.kanban.done_artifact_auditor")


# ---------------------------------------------------------------------------
# extract_artifact_paths
# ---------------------------------------------------------------------------


def test_extract_artifact_paths_basic():
    desc = "Create tools/foo/bar.py and args/x.yaml. See icdev/tools/ace/blueprint.py."
    paths = aud.extract_artifact_paths(desc)
    assert "tools/foo/bar.py" in paths
    assert "args/x.yaml" in paths
    assert "icdev/tools/ace/blueprint.py" in paths


def test_extract_dedups_preserving_order():
    desc = "Edit tests/a.py then re-run tests/a.py again."
    assert aud.extract_artifact_paths(desc) == ["tests/a.py"]


def test_extract_ignores_placeholders_and_module_imports():
    desc = (
        "Add entry to tools/manifest/<topic>.md. "
        "Then `from icdev.tools.ace.controller import ACEController`."
    )
    # <topic> placeholder is not a concrete file; dotted module path has no
    # source-dir-rooted slash path ending in an extension.
    assert aud.extract_artifact_paths(desc) == []


def test_extract_strips_trailing_punctuation():
    desc = "Create goals/ace_coworker.md, then stop."
    assert "goals/ace_coworker.md" in aud.extract_artifact_paths(desc)


def test_extract_only_known_source_dirs():
    desc = "Touch random/thing.py and /etc/passwd"
    assert aud.extract_artifact_paths(desc) == []


# ---------------------------------------------------------------------------
# extract_verify_commands
# ---------------------------------------------------------------------------


def test_extract_verify_commands():
    desc = 'Build it. Verify: python -c "import x; print(1)" Expected: 1.'
    cmds = aud.extract_verify_commands(desc)
    assert any("python -c" in c for c in cmds)


def test_extract_verify_commands_none():
    assert aud.extract_verify_commands("no verification mentioned") == []


# ---------------------------------------------------------------------------
# audit_task
# ---------------------------------------------------------------------------


def test_audit_task_missing(tmp_path):
    task = {"id": "t1", "status": "done", "description": "Create tools/missing_xyz.py"}
    result = aud.audit_task(task, tmp_path)
    assert result["verdict"] == "missing_artifacts"
    assert "tools/missing_xyz.py" in result["missing"]
    assert result["task_id"] == "t1"


def test_audit_task_ok(tmp_path):
    (tmp_path / "tools").mkdir()
    (tmp_path / "tools" / "exists.py").write_text("x = 1\n", encoding="utf-8")
    task = {"id": "t2", "status": "done", "description": "Create tools/exists.py"}
    result = aud.audit_task(task, tmp_path)
    assert result["verdict"] == "ok"
    assert result["missing"] == []


def test_audit_task_no_claims(tmp_path):
    task = {"id": "t3", "status": "done", "description": "Just refactor some logic."}
    result = aud.audit_task(task, tmp_path)
    assert result["verdict"] == "no_claims"
    assert result["missing"] == []


# ---------------------------------------------------------------------------
# audit_tasks (filtering + aggregation)
# ---------------------------------------------------------------------------


def test_audit_tasks_only_status_filter(tmp_path):
    tasks = [
        {"id": "d1", "status": "done", "description": "Create tools/gone.py"},
        {"id": "b1", "status": "backlog", "description": "Create tools/also_gone.py"},
    ]
    results = aud.audit_tasks(tasks, tmp_path, only_status="done")
    ids = {r["task_id"] for r in results}
    assert ids == {"d1"}


def test_audit_tasks_no_filter_audits_all(tmp_path):
    tasks = [
        {"id": "d1", "status": "done", "description": "Create tools/gone.py"},
        {"id": "b1", "status": "backlog", "description": "x"},
    ]
    results = aud.audit_tasks(tasks, tmp_path)
    assert len(results) == 2


def test_summarize_counts(tmp_path):
    (tmp_path / "tools").mkdir()
    (tmp_path / "tools" / "here.py").write_text("1", encoding="utf-8")
    tasks = [
        {"id": "ok1", "status": "done", "description": "Create tools/here.py"},
        {"id": "bad1", "status": "done", "description": "Create tools/nope.py"},
        {"id": "none1", "status": "done", "description": "refactor"},
    ]
    results = aud.audit_tasks(tasks, tmp_path)
    summary = aud.summarize(results)
    assert summary["ok"] == 1
    assert summary["missing_artifacts"] == 1
    assert summary["no_claims"] == 1
    assert summary["total"] == 3
