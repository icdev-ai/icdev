"""Unit tests for the ACE _run_inner listen_topics defensive guard.

Covers _filter_listen_topics() and check_ace_yaml_listen_topics() coherence check.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from icdev.tools.ace.coworker_thread import _BOOTSTRAP_TOPICS, _filter_listen_topics


# ---------------------------------------------------------------------------
# _filter_listen_topics tests
# ---------------------------------------------------------------------------


def test_bootstrap_only_list_unchanged() -> None:
    """A list containing only bootstrap topics is returned unchanged."""
    result = _filter_listen_topics(["task.assigned"], "cw-test")
    assert result == ["task.assigned"]


def test_reactive_only_list_unchanged() -> None:
    """Without task.assigned, all topics are kept — no filtering applied."""
    topics = ["doc.draft_ready", "doc.review_feedback"]
    result = _filter_listen_topics(topics, "cw-test")
    assert result == ["doc.draft_ready", "doc.review_feedback"]


def test_mixed_list_drops_non_bootstrap_and_warns() -> None:
    """When task.assigned is present, non-bootstrap topics are dropped with a WARNING."""
    topics = ["task.assigned", "doc.review_feedback", "doc.draft_ready"]
    mock_logger = MagicMock()
    with patch("icdev.tools.ace.coworker_thread.logger", mock_logger):
        result = _filter_listen_topics(topics, "cw-docrev")

    assert result == ["task.assigned"]
    assert mock_logger.warning.call_count == 2
    warning_calls = str(mock_logger.warning.call_args_list)
    assert "doc.review_feedback" in warning_calls
    assert "doc.draft_ready" in warning_calls


def test_single_task_assigned_returns_it() -> None:
    """Single-element list [task.assigned] is returned as-is."""
    result = _filter_listen_topics(["task.assigned"], "cw-solo")
    assert result == ["task.assigned"]


def test_empty_list_returns_empty() -> None:
    """Empty listen_topics list is handled without error."""
    result = _filter_listen_topics([], "cw-empty")
    assert result == []


def test_only_one_reactive_topic_dropped() -> None:
    """Exactly one non-bootstrap topic is dropped when task.assigned is present."""
    topics = ["task.assigned", "build.artifact_ready"]
    mock_logger = MagicMock()
    with patch("icdev.tools.ace.coworker_thread.logger", mock_logger):
        result = _filter_listen_topics(topics, "cw-builder")

    assert result == ["task.assigned"]
    assert mock_logger.warning.call_count == 1
    assert "build.artifact_ready" in str(mock_logger.warning.call_args_list)


def test_bootstrap_topics_set_contains_task_assigned() -> None:
    """_BOOTSTRAP_TOPICS must include the canonical gateway trigger."""
    assert "task.assigned" in _BOOTSTRAP_TOPICS


def test_no_warning_when_only_bootstrap_topics() -> None:
    """No warnings are emitted when all topics are bootstrap topics."""
    mock_logger = MagicMock()
    with patch("icdev.tools.ace.coworker_thread.logger", mock_logger):
        _filter_listen_topics(["task.assigned"], "cw-noop")
    mock_logger.warning.assert_not_called()


# ---------------------------------------------------------------------------
# check_ace_yaml_listen_topics coherence check tests
# ---------------------------------------------------------------------------


def _make_yaml(listen_topics: list[str], tmpdir: Path, role_id: str = "test_role") -> Path:
    """Write a minimal role YAML with the given listen_topics."""
    topics_block = "\n".join(f"    - {t}" for t in listen_topics)
    content = f"""role_id: {role_id}
display_name: Test Role
description: Test
version: "1.0"
trust_tier: yellow
default_count: 1
max_instances: 1
steps:
  - do_something
communication:
  protocol: a2a
  listen_topics:
{topics_block}
  emit_topics: []
llm_function: code_generation
tool_permissions:
  - Read
"""
    path = tmpdir / f"{role_id}.yaml"
    path.write_text(content, encoding="utf-8")
    return path


def test_coherence_check_clean_yaml_passes(tmp_path: Path) -> None:
    """A role with only task.assigned in listen_topics passes the check."""
    from icdev.tools.workflow.coherence_checker import check_ace_yaml_listen_topics

    _make_yaml(["task.assigned"], tmp_path)
    result = check_ace_yaml_listen_topics(changed_files=list(tmp_path.glob("*.yaml")))
    assert result.status == "pass"


def test_coherence_check_mixed_yaml_warns(tmp_path: Path) -> None:
    """A role mixing task.assigned with a reactive topic raises a warning."""
    from icdev.tools.workflow.coherence_checker import check_ace_yaml_listen_topics

    _make_yaml(["task.assigned", "doc.review_feedback"], tmp_path)
    result = check_ace_yaml_listen_topics(changed_files=list(tmp_path.glob("*.yaml")))
    assert result.status == "warn"
    assert any("doc.review_feedback" in v for v in result.extra)


def test_coherence_check_reactive_only_passes(tmp_path: Path) -> None:
    """A role with only reactive topics (no task.assigned) is not flagged."""
    from icdev.tools.workflow.coherence_checker import check_ace_yaml_listen_topics

    _make_yaml(["doc.draft_ready", "build.done"], tmp_path)
    result = check_ace_yaml_listen_topics(changed_files=list(tmp_path.glob("*.yaml")))
    assert result.status == "pass"


def test_coherence_check_empty_topics_passes(tmp_path: Path) -> None:
    """A role with an empty listen_topics list passes the check."""
    content = """role_id: minimal_role
display_name: Minimal
description: Test
version: "1.0"
trust_tier: yellow
default_count: 1
max_instances: 1
steps:
  - do_something
communication:
  protocol: a2a
  listen_topics: []
  emit_topics: []
llm_function: code_generation
tool_permissions:
  - Read
"""
    path = tmp_path / "minimal_role.yaml"
    path.write_text(content, encoding="utf-8")

    from icdev.tools.workflow.coherence_checker import check_ace_yaml_listen_topics

    result = check_ace_yaml_listen_topics(changed_files=[path])
    assert result.status == "pass"


def test_coherence_check_multiple_violations(tmp_path: Path) -> None:
    """Multiple reactive topics alongside task.assigned all appear in violations."""
    from icdev.tools.workflow.coherence_checker import check_ace_yaml_listen_topics

    _make_yaml(
        ["task.assigned", "doc.review_feedback", "doc.draft_ready", "build.done"],
        tmp_path,
    )
    result = check_ace_yaml_listen_topics(changed_files=list(tmp_path.glob("*.yaml")))
    assert result.status == "warn"
    assert len(result.extra) == 3  # one violation per non-bootstrap topic


def test_coherence_check_no_roles_dir_warns(monkeypatch: pytest.MonkeyPatch) -> None:
    """Missing args/ace/roles/ directory produces a warn (not a crash)."""
    from icdev.tools.workflow import coherence_checker

    original = coherence_checker.PROJECT_ROOT
    monkeypatch.setattr(coherence_checker, "PROJECT_ROOT", Path("/nonexistent/path"))
    result = coherence_checker.check_ace_yaml_listen_topics()
    assert result.status == "warn"
    monkeypatch.setattr(coherence_checker, "PROJECT_ROOT", original)


def test_coherence_check_registered_in_registry() -> None:
    """check_ace_yaml_listen_topics must be in CHECK_REGISTRY."""
    from icdev.tools.workflow.coherence_checker import CHECK_REGISTRY

    assert "ace_yaml_listen_topics" in CHECK_REGISTRY
