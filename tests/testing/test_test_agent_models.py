# CUI // SP-CTI
"""Spec-conformance tests for tools/testing/test_agent_models.py."""
from __future__ import annotations

import pathlib
import sys
from unittest.mock import patch


ROOT = pathlib.Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.testing import test_agent_models as tam  # noqa: E402
from tools.testing.data_types import AgentPromptResponse  # noqa: E402


# ────────────────────────────────────────────────────────────────────────────
# Constants
# ────────────────────────────────────────────────────────────────────────────


def test_models_constant():
    assert tam.MODELS == ["opus", "sonnet", "haiku"]


def test_test_prompt_contains_required_phrases():
    assert "Test successful!" in tam.TEST_PROMPT
    assert "Confirm you received" in tam.TEST_PROMPT


# ────────────────────────────────────────────────────────────────────────────
# test_model
# ────────────────────────────────────────────────────────────────────────────


def test_model_returns_success_tuple_on_happy_response(monkeypatch, tmp_path):
    monkeypatch.setattr(tam, "PROJECT_ROOT", tmp_path)
    fake = AgentPromptResponse(output="hi I am opus", success=True,
                               session_id="s1")
    with patch.object(tam, "prompt_claude_code", return_value=fake):
        ok, message = tam.test_model("opus", "rid-1")
    assert ok is True
    assert "opus" in message
    assert "Success" in message
    # Output dir was created
    assert (tmp_path / "agents" / "rid-1").exists()


def test_model_returns_failure_tuple_on_unsuccessful_response(monkeypatch, tmp_path):
    monkeypatch.setattr(tam, "PROJECT_ROOT", tmp_path)
    fake = AgentPromptResponse(output="rate limit", success=False)
    with patch.object(tam, "prompt_claude_code", return_value=fake):
        ok, message = tam.test_model("haiku", "rid-2")
    assert ok is False
    assert "haiku" in message
    assert "rate limit" in message


def test_model_swallows_prompt_claude_code_exception(monkeypatch, tmp_path):
    monkeypatch.setattr(tam, "PROJECT_ROOT", tmp_path)

    def boom(req):
        raise RuntimeError("network down")

    with patch.object(tam, "prompt_claude_code", side_effect=boom):
        ok, message = tam.test_model("sonnet", "rid-3")
    assert ok is False
    assert "Exception" in message
    assert "network down" in message


# ────────────────────────────────────────────────────────────────────────────
# main()
# ────────────────────────────────────────────────────────────────────────────


def test_main_zero_when_all_pass(monkeypatch, capsys):
    monkeypatch.setattr(tam, "make_run_id", lambda: "rid-x")
    monkeypatch.setattr(
        tam, "_run_in_parallel",
        lambda models, run_id: {m: (True, f"{m}: Success") for m in models},
    )
    rc = tam.main([])
    assert rc == 0
    out = capsys.readouterr().out
    assert "All tests passed" in out


def test_main_one_when_any_fail(monkeypatch, capsys):
    monkeypatch.setattr(tam, "make_run_id", lambda: "rid-y")
    monkeypatch.setattr(
        tam, "_run_in_parallel",
        lambda models, run_id: {
            "opus": (True, "opus: Success"),
            "sonnet": (False, "sonnet: rate limit"),
            "haiku": (True, "haiku: Success"),
        },
    )
    rc = tam.main([])
    assert rc == 1
    out = capsys.readouterr().out
    assert "Some tests failed" in out


def test_main_reports_missing_model_as_fail(monkeypatch, capsys):
    monkeypatch.setattr(tam, "make_run_id", lambda: "rid-z")
    monkeypatch.setattr(
        tam, "_run_in_parallel",
        lambda models, run_id: {"opus": (True, "opus: Success")},
    )
    rc = tam.main([])
    assert rc == 1
    out = capsys.readouterr().out
    assert "sonnet: not run" in out
    assert "haiku: not run" in out
