"""Tests: RoleLoader supports both string steps and structured steps with condition."""

from __future__ import annotations

import sys
import tempfile
import textwrap
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from icdev.tools.ace.role_loader import RoleLoader, RoleNotFoundError, RoleStep


def _write_role(roles_dir: Path, content: str) -> None:
    import yaml
    data = yaml.safe_load(content)
    (roles_dir / f"{data['role_id']}.yaml").write_text(content, encoding="utf-8")


# ---------------------------------------------------------------------------
# String step backward compatibility
# ---------------------------------------------------------------------------

def test_string_steps_load_as_role_steps():
    with tempfile.TemporaryDirectory() as tmp:
        roles_dir = Path(tmp)
        _write_role(roles_dir, textwrap.dedent("""\
            role_id: simple
            trust_tier: yellow
            tool_permissions: [Read]
            steps:
              - step_a
              - step_b
        """))
        loader = RoleLoader(roles_dir=roles_dir, hot_reload=False)
        role = loader.get_role("simple")
        assert len(role.steps) == 2
        assert all(isinstance(s, RoleStep) for s in role.steps)
        assert role.steps[0].name == "step_a"
        assert role.steps[0].tool == ""
        assert role.steps[0].condition is None


# ---------------------------------------------------------------------------
# Structured step parsing
# ---------------------------------------------------------------------------

def test_structured_step_name_and_tool():
    with tempfile.TemporaryDirectory() as tmp:
        roles_dir = Path(tmp)
        _write_role(roles_dir, textwrap.dedent("""\
            role_id: structured
            trust_tier: yellow
            tool_permissions: [Read]
            steps:
              - name: do_thing
                tool: SomeTool.run
                params:
                  key: value
        """))
        loader = RoleLoader(roles_dir=roles_dir, hot_reload=False)
        role = loader.get_role("structured")
        step = role.steps[0]
        assert step.name == "do_thing"
        assert step.tool == "SomeTool.run"
        assert step.params == {"key": "value"}
        assert step.condition is None


def test_structured_step_condition_field():
    with tempfile.TemporaryDirectory() as tmp:
        roles_dir = Path(tmp)
        _write_role(roles_dir, textwrap.dedent("""\
            role_id: conditional
            trust_tier: yellow
            tool_permissions: [Read]
            steps:
              - name: maybe_run
                tool: Foo.bar
                condition: "$result.count > 0"
        """))
        loader = RoleLoader(roles_dir=roles_dir, hot_reload=False)
        role = loader.get_role("conditional")
        step = role.steps[0]
        assert step.condition == "$result.count > 0"


def test_structured_step_missing_name_raises():
    with pytest.raises(ValueError, match="missing 'name'"):
        RoleStep.from_raw({"tool": "Foo.bar"})


# ---------------------------------------------------------------------------
# Mixed steps in one role
# ---------------------------------------------------------------------------

def test_mixed_string_and_structured_steps():
    with tempfile.TemporaryDirectory() as tmp:
        roles_dir = Path(tmp)
        _write_role(roles_dir, textwrap.dedent("""\
            role_id: mixed
            trust_tier: yellow
            tool_permissions: [Read]
            steps:
              - plain_step
              - name: fancy_step
                tool: Bar.baz
                condition: "$x > 1"
        """))
        loader = RoleLoader(roles_dir=roles_dir, hot_reload=False)
        role = loader.get_role("mixed")
        assert role.steps[0].name == "plain_step"
        assert role.steps[0].tool == ""
        assert role.steps[1].name == "fancy_step"
        assert role.steps[1].condition == "$x > 1"


# ---------------------------------------------------------------------------
# RoleNotFoundError
# ---------------------------------------------------------------------------

def test_get_role_raises_for_unknown():
    with tempfile.TemporaryDirectory() as tmp:
        loader = RoleLoader(roles_dir=Path(tmp), hot_reload=False)
        with pytest.raises(RoleNotFoundError):
            loader.get_role("nonexistent")


# ---------------------------------------------------------------------------
# Reload
# ---------------------------------------------------------------------------

def test_reload_returns_count():
    with tempfile.TemporaryDirectory() as tmp:
        roles_dir = Path(tmp)
        _write_role(roles_dir, textwrap.dedent("""\
            role_id: r1
            trust_tier: yellow
            tool_permissions: [Read]
            steps: [s1]
        """))
        loader = RoleLoader(roles_dir=roles_dir, hot_reload=False)
        assert loader.reload() == 1
