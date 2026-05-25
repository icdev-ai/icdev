# CUI // SP-CTI
"""Spec-conformance tests for tools/ci/modules/agent.py."""
from __future__ import annotations

import json
import pathlib
import sys
from pathlib import Path


ROOT = pathlib.Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.ci.modules import agent as agent_mod  # noqa: E402
from tools.testing.data_types import (  # noqa: E402
    AgentPromptRequest,
    AgentPromptResponse,
    AgentTemplateRequest,
)


# ────────────────────────────────────────────────────────────────────────────
# Constants
# ────────────────────────────────────────────────────────────────────────────


def test_bot_identifier_value():
    assert agent_mod.BOT_IDENTIFIER == "[ICDEV\u2122-BOT]"


def test_slash_command_model_map_required_entries():
    m = agent_mod.SLASH_COMMAND_MODEL_MAP
    assert m["/classify_issue"] == "haiku"
    assert m["/classify_workflow"] == "haiku"
    assert m["/generate_branch_name"] == "haiku"
    assert m["/icdev-build"] == "opus"
    assert m["/icdev-review"] == "opus"
    assert m["/implement"] == "opus"
    assert m["/document"] == "sonnet"
    assert m["/commit"] == "haiku"


# ────────────────────────────────────────────────────────────────────────────
# _get_timeout
# ────────────────────────────────────────────────────────────────────────────


def test_get_timeout_default(monkeypatch, tmp_path):
    monkeypatch.setattr(agent_mod, "PROJECT_ROOT", tmp_path)
    assert agent_mod._get_timeout() == 300


def test_get_timeout_reads_default_seconds(monkeypatch, tmp_path):
    monkeypatch.setattr(agent_mod, "PROJECT_ROOT", tmp_path)
    cfg = tmp_path / "args" / "cicd_config.yaml"
    cfg.parent.mkdir(parents=True)
    cfg.write_text(
        "cicd:\n  executor:\n    default_timeout_seconds: 900\n",
        encoding="utf-8",
    )
    assert agent_mod._get_timeout() == 900


def test_get_timeout_honors_override(monkeypatch, tmp_path):
    monkeypatch.setattr(agent_mod, "PROJECT_ROOT", tmp_path)
    cfg = tmp_path / "args" / "cicd_config.yaml"
    cfg.parent.mkdir(parents=True)
    cfg.write_text(
        "cicd:\n  executor:\n    default_timeout_seconds: 300\n"
        "    timeout_overrides:\n      \"/icdev-build\": 1800\n",
        encoding="utf-8",
    )
    assert agent_mod._get_timeout("/icdev-build") == 1800
    assert agent_mod._get_timeout("/icdev-test") == 300


def test_get_timeout_swallows_parse_error(monkeypatch, tmp_path):
    monkeypatch.setattr(agent_mod, "PROJECT_ROOT", tmp_path)
    cfg = tmp_path / "args" / "cicd_config.yaml"
    cfg.parent.mkdir(parents=True)
    cfg.write_text("not yaml: [::]\n", encoding="utf-8")
    assert agent_mod._get_timeout() == 300


# ────────────────────────────────────────────────────────────────────────────
# _ensure_agent_dir
# ────────────────────────────────────────────────────────────────────────────


def test_ensure_agent_dir_creates_tree(monkeypatch, tmp_path):
    monkeypatch.setattr(agent_mod, "PROJECT_ROOT", tmp_path)
    out = agent_mod._ensure_agent_dir("rid-1", "planner")
    assert out == tmp_path / "agents" / "rid-1" / "planner"
    assert (out / "prompts").exists()


# ────────────────────────────────────────────────────────────────────────────
# _safe_filename
# ────────────────────────────────────────────────────────────────────────────


def test_safe_filename_strips_slash_and_dashes():
    assert agent_mod._safe_filename("/icdev-build") == "icdev_build"
    assert agent_mod._safe_filename("/classify_issue") == "classify_issue"
    assert agent_mod._safe_filename("") == "command"


# ────────────────────────────────────────────────────────────────────────────
# execute_template
# ────────────────────────────────────────────────────────────────────────────


def test_execute_template_writes_prompt_and_invokes(monkeypatch, tmp_path):
    monkeypatch.setattr(agent_mod, "PROJECT_ROOT", tmp_path)
    seen = []

    def fake_prompt_claude_code(req):
        seen.append(req)
        # Simulate writing a result line so the JSONL→JSON converter
        # has something to parse.
        Path(req.output_file).write_text(
            json.dumps({"type": "result", "result": "ok"}) + "\n",
            encoding="utf-8",
        )
        return AgentPromptResponse(output="ok", success=True)

    monkeypatch.setattr(
        agent_mod, "prompt_claude_code", fake_prompt_claude_code,
    )

    request = AgentTemplateRequest(
        agent_name="planner",
        slash_command="/icdev-build",
        args=["arg1", "arg2"],
        run_id="rid",
    )
    response = agent_mod.execute_template(request)
    assert response.success is True
    assert response.output == "ok"

    # Prompt file landed
    prompt_file = (
        tmp_path / "agents" / "rid" / "planner" / "prompts" / "icdev_build.txt"
    )
    assert prompt_file.exists()
    assert "arg1 arg2" in prompt_file.read_text(encoding="utf-8")

    # Inner request was built with the resolved model
    inner = seen[0]
    assert inner.model == "opus"
    assert inner.project_dir == "."

    # JSON sibling was written
    assert (tmp_path / "agents" / "rid" / "planner" / "raw_output.json").exists()


def test_execute_template_swallows_jsonl_conversion_error(monkeypatch, tmp_path):
    monkeypatch.setattr(agent_mod, "PROJECT_ROOT", tmp_path)

    def fake_prompt(req):
        # Don't write any output file → conversion is a no-op + must not raise
        return AgentPromptResponse(output="", success=True)

    monkeypatch.setattr(agent_mod, "prompt_claude_code", fake_prompt)
    response = agent_mod.execute_template(AgentTemplateRequest(
        agent_name="ops", slash_command="/commit", run_id="rid",
    ))
    assert response.success is True


# ────────────────────────────────────────────────────────────────────────────
# prompt_claude_code
# ────────────────────────────────────────────────────────────────────────────


def test_prompt_claude_code_uses_robust_executor_when_available(monkeypatch):
    seen = []

    class _Resp:
        output_text = "robust output"
        status = "completed"
        session_id = "sess"
        duration_ms = 12

    def fake_robust(req, timeout_seconds):
        seen.append((req, timeout_seconds))
        return AgentPromptResponse(
            output="robust output",
            success=True,
            session_id="sess",
            duration_ms=12,
        )

    monkeypatch.setattr(agent_mod, "_try_robust_executor", fake_robust)
    direct_called = []
    monkeypatch.setattr(
        agent_mod, "_direct_subprocess",
        lambda *a, **k: direct_called.append(True) or AgentPromptResponse(),
    )

    out = agent_mod.prompt_claude_code(AgentPromptRequest(prompt="hi"))
    assert out.output == "robust output"
    assert out.success is True
    assert direct_called == []  # robust path took it


def test_prompt_claude_code_falls_back_when_robust_missing(monkeypatch):
    monkeypatch.setattr(agent_mod, "_try_robust_executor", lambda *a, **k: None)
    monkeypatch.setattr(
        agent_mod, "_direct_subprocess",
        lambda *a, **k: AgentPromptResponse(output="direct", success=True),
    )
    out = agent_mod.prompt_claude_code(AgentPromptRequest(prompt="hi"))
    assert out.output == "direct"


def test_direct_subprocess_handles_file_not_found(monkeypatch, tmp_path):
    monkeypatch.setattr(agent_mod, "PROJECT_ROOT", tmp_path)
    monkeypatch.setenv("CLAUDE_CODE_PATH", "claude_made_up_xyz")

    def boom(*a, **k):
        raise FileNotFoundError("no such file")

    monkeypatch.setattr(agent_mod.subprocess, "run", boom)
    out = agent_mod._direct_subprocess(
        AgentPromptRequest(prompt="hi", model="haiku"), timeout_seconds=5,
    )
    assert out.success is False
    assert "not found" in out.output.lower()


def test_direct_subprocess_handles_timeout(monkeypatch, tmp_path):
    import subprocess as _sp
    monkeypatch.setattr(agent_mod, "PROJECT_ROOT", tmp_path)

    def boom(*a, **k):
        raise _sp.TimeoutExpired(cmd="claude", timeout=5)

    monkeypatch.setattr(agent_mod.subprocess, "run", boom)
    out = agent_mod._direct_subprocess(
        AgentPromptRequest(prompt="hi", model="haiku"), timeout_seconds=5,
    )
    assert out.success is False
    assert "timed out" in out.output.lower()


# ────────────────────────────────────────────────────────────────────────────
# _parse_jsonl_result
# ────────────────────────────────────────────────────────────────────────────


def test_parse_jsonl_result_extracts_result_record(tmp_path):
    p = tmp_path / "out.jsonl"
    p.write_text(
        json.dumps({"type": "system", "msg": "starting"}) + "\n"
        + json.dumps({"type": "result", "result": "hi", "session_id": "s1"})
        + "\n",
        encoding="utf-8",
    )
    text, session, is_error = agent_mod._parse_jsonl_result(p)
    assert text == "hi"
    assert session == "s1"
    assert is_error is False


def test_parse_jsonl_result_missing_file_returns_empty(tmp_path):
    text, session, is_error = agent_mod._parse_jsonl_result(
        tmp_path / "nope.jsonl"
    )
    assert text == ""
    assert session is None
    assert is_error is False


def test_parse_jsonl_result_skips_malformed_lines(tmp_path):
    p = tmp_path / "out.jsonl"
    p.write_text(
        "not json\n"
        + json.dumps({"type": "result", "result": "ok"}) + "\n",
        encoding="utf-8",
    )
    text, _session, _is_error = agent_mod._parse_jsonl_result(p)
    assert text == "ok"


