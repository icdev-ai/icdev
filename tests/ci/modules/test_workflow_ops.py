# CUI // SP-CTI
"""Spec-conformance tests for tools/ci/modules/workflow_ops.py."""
from __future__ import annotations

import json
import logging
import pathlib
import sys
from unittest.mock import patch


ROOT = pathlib.Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.ci.modules import workflow_ops as wo  # noqa: E402


def _logger():
    return logging.getLogger("t")


class _Resp:
    def __init__(self, success=True, output=""):
        self.success = success
        self.output = output


# ────────────────────────────────────────────────────────────────────────────
# Constants
# ────────────────────────────────────────────────────────────────────────────


def test_agent_constants_exposed():
    assert wo.AGENT_PLANNER == "icdev_planner"
    assert wo.AGENT_IMPLEMENTOR == "icdev_implementor"
    assert wo.AGENT_CLASSIFIER == "issue_classifier"
    assert wo.AGENT_BRANCH_GENERATOR == "branch_generator"
    assert wo.AGENT_PR_CREATOR == "pr_creator"


def test_available_workflows_includes_core_set():
    for fn in (
        "icdev_plan", "icdev_build", "icdev_test", "icdev_review",
        "icdev_comply", "icdev_sdlc", "icdev_patch",
    ):
        assert fn in wo.AVAILABLE_ICDEV_WORKFLOWS


# ────────────────────────────────────────────────────────────────────────────
# format_issue_message
# ────────────────────────────────────────────────────────────────────────────


def test_format_issue_message_without_session():
    out = wo.format_issue_message("rid", "ops", "hello")
    assert "rid_ops:" in out
    assert "hello" in out


def test_format_issue_message_with_session():
    out = wo.format_issue_message("rid", "ops", "hi", session_id="sid")
    assert "rid_ops_sid:" in out


# ────────────────────────────────────────────────────────────────────────────
# extract_icdev_info
# ────────────────────────────────────────────────────────────────────────────


def test_extract_icdev_info_happy_path():
    payload = {"icdev_slash_command": "/icdev_plan", "run_id": "abc"}
    with patch.object(
        wo, "execute_template",
        return_value=_Resp(success=True, output=json.dumps(payload)),
    ):
        cmd, rid = wo.extract_icdev_info("any text", "tmp")
    assert cmd == "icdev_plan"
    assert rid == "abc"


def test_extract_icdev_info_unknown_workflow_returns_none():
    payload = {"icdev_slash_command": "/icdev_unknown", "run_id": "x"}
    with patch.object(
        wo, "execute_template",
        return_value=_Resp(success=True, output=json.dumps(payload)),
    ):
        cmd, rid = wo.extract_icdev_info("text", "tmp")
    assert cmd is None
    assert rid is None


def test_extract_icdev_info_swallows_agent_exception():
    def boom(req):
        raise RuntimeError("agent down")

    with patch.object(wo, "execute_template", side_effect=boom):
        cmd, rid = wo.extract_icdev_info("text", "tmp")
    assert cmd is None
    assert rid is None


def test_extract_icdev_info_swallows_bad_json():
    with patch.object(
        wo, "execute_template",
        return_value=_Resp(success=True, output="not json"),
    ):
        cmd, rid = wo.extract_icdev_info("text", "tmp")
    assert cmd is None
    assert rid is None


# ────────────────────────────────────────────────────────────────────────────
# classify_issue
# ────────────────────────────────────────────────────────────────────────────


def test_classify_issue_extracts_command():
    with patch.object(
        wo, "execute_template",
        return_value=_Resp(success=True, output="The answer is /feature here"),
    ):
        cmd, err = wo.classify_issue("{}", "rid", _logger())
    assert cmd == "/feature"
    assert err is None


def test_classify_issue_zero_returns_error():
    with patch.object(
        wo, "execute_template",
        return_value=_Resp(success=True, output="0"),
    ):
        cmd, err = wo.classify_issue("{}", "rid", _logger())
    assert cmd is None
    assert "No command" in err


def test_classify_issue_invalid_returns_error():
    with patch.object(
        wo, "execute_template",
        return_value=_Resp(success=True, output="garbage"),
    ):
        cmd, err = wo.classify_issue("{}", "rid", _logger())
    assert cmd is None
    assert "Invalid command" in err


def test_classify_issue_agent_failure_propagates():
    with patch.object(
        wo, "execute_template",
        return_value=_Resp(success=False, output="agent crashed"),
    ):
        cmd, err = wo.classify_issue("{}", "rid", _logger())
    assert cmd is None
    assert "agent crashed" in err


# ────────────────────────────────────────────────────────────────────────────
# generate_branch_name
# ────────────────────────────────────────────────────────────────────────────


def test_generate_branch_name_strips_output():
    with patch.object(
        wo, "execute_template",
        return_value=_Resp(success=True, output="  feat-1\n"),
    ):
        name, err = wo.generate_branch_name("{}", "/feature", "rid", _logger())
    assert name == "feat-1"
    assert err is None


def test_generate_branch_name_failure_returns_error():
    with patch.object(
        wo, "execute_template",
        return_value=_Resp(success=False, output="rate limited"),
    ):
        name, err = wo.generate_branch_name("{}", "/feature", "rid", _logger())
    assert name is None
    assert err == "rate limited"


# ────────────────────────────────────────────────────────────────────────────
# build_plan / implement_plan / create_commit / create_pull_request
# ────────────────────────────────────────────────────────────────────────────


def test_build_plan_passes_command_through():
    seen = []

    def fake(req):
        seen.append(req)
        return _Resp(success=True, output="plan.md")

    with patch.object(wo, "execute_template", side_effect=fake):
        wo.build_plan("{}", "/feature", "rid", _logger())
    assert seen
    assert seen[0].slash_command == "/feature"
    assert seen[0].agent_name == wo.AGENT_PLANNER


def test_implement_plan_uses_default_agent():
    seen = []

    def fake(req):
        seen.append(req)
        return _Resp(success=True, output="ok")

    with patch.object(wo, "execute_template", side_effect=fake):
        wo.implement_plan("plan.md", "rid", _logger())
    assert seen[0].agent_name == wo.AGENT_IMPLEMENTOR
    assert seen[0].slash_command == "/implement"


def test_implement_plan_accepts_custom_agent():
    seen = []

    def fake(req):
        seen.append(req)
        return _Resp(success=True)

    with patch.object(wo, "execute_template", side_effect=fake):
        wo.implement_plan("plan.md", "rid", _logger(), agent_name="patcher")
    assert seen[0].agent_name == "patcher"


def test_create_commit_strips_output():
    with patch.object(
        wo, "execute_template",
        return_value=_Resp(success=True, output="  fix: add docs  "),
    ):
        msg, err = wo.create_commit(
            "icdev_planner", "{}", "/feature", "rid", _logger(),
        )
    assert msg == "fix: add docs"
    assert err is None


def test_create_pull_request_uses_state_fields():
    class _State:
        def get(self, k, default=None):
            return {"plan_file": "p.md", "run_id": "rid"}.get(k, default)

    seen = []

    def fake(req):
        seen.append(req)
        return _Resp(success=True, output="https://github.com/o/r/pull/1")

    with patch.object(wo, "execute_template", side_effect=fake):
        url, err = wo.create_pull_request("feat-1", "{}", _State(), _logger())
    assert url == "https://github.com/o/r/pull/1"
    assert err is None
    assert seen[0].args == ["feat-1", "{}", "p.md", "rid"]


# ────────────────────────────────────────────────────────────────────────────
# ensure_run_id
# ────────────────────────────────────────────────────────────────────────────


def test_ensure_run_id_reuses_existing(monkeypatch, tmp_path):
    from tools.ci.modules import state as state_mod
    monkeypatch.setattr(state_mod, "_AGENTS_ROOT", tmp_path)

    # Pre-create the state file for run_id=R1
    s = state_mod.ICDevState("R1")
    s.update(run_id="R1", issue_number="9")
    s.save("seed")

    out = wo.ensure_run_id("9", run_id="R1")
    assert out == "R1"


def test_ensure_run_id_creates_when_id_missing(monkeypatch, tmp_path):
    from tools.ci.modules import state as state_mod
    monkeypatch.setattr(state_mod, "_AGENTS_ROOT", tmp_path)
    monkeypatch.setattr(
        "tools.testing.utils.make_run_id", lambda: "abcd1234",
    )
    out = wo.ensure_run_id("9", run_id=None)
    assert out == "abcd1234"
    assert (tmp_path / "abcd1234" / "icdev_state.json").exists()


# ────────────────────────────────────────────────────────────────────────────
# find_existing_branch_for_issue
# ────────────────────────────────────────────────────────────────────────────


def test_find_existing_branch_matches_issue():
    class _Proc:
        stdout = (
            "  main\n"
            "* feat-icdev-rid1-issue-9-add-thing\n"
            "  feat-icdev-rid2-issue-12-other\n"
        )
        stderr = ""
        returncode = 0

    with patch.object(wo.subprocess, "run", return_value=_Proc()):
        out = wo.find_existing_branch_for_issue("9")
    assert "issue-9" in (out or "")


def test_find_existing_branch_filters_by_run_id():
    class _Proc:
        stdout = (
            "  feat-icdev-rid1-issue-9-add-thing\n"
            "  feat-icdev-rid2-issue-9-other-attempt\n"
        )
        stderr = ""
        returncode = 0

    with patch.object(wo.subprocess, "run", return_value=_Proc()):
        out = wo.find_existing_branch_for_issue("9", run_id="rid2")
    assert "rid2" in (out or "")


def test_find_existing_branch_returns_none_on_failure():
    class _Proc:
        stdout = ""
        stderr = "fatal"
        returncode = 1

    with patch.object(wo.subprocess, "run", return_value=_Proc()):
        assert wo.find_existing_branch_for_issue("9") is None
