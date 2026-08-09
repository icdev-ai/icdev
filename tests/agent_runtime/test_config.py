# CUI // SP-CTI
"""Unit tests for the SAG runtime config layer (hgx-cfg-01).

The one property that matters here is PRECEDENCE. ``args/agent_runtime.yaml``
was added *beneath* the existing environment variables, not above them: every
env var that worked before must still work and must still win. A config layer
that quietly overrode an exported flag would be a regression dressed as a
feature, so most of these tests set an env var and a conflicting config value
and assert the env var is what comes out.

The tests write their own YAML to ``tmp_path`` and point the loader at it with
``ICDEV_AGENT_RUNTIME_CONFIG`` rather than asserting against the repo's shipped
file — otherwise editing a default in ``args/agent_runtime.yaml`` would break
tests that are not about that default.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

import tools.agent_runtime.approval_gate as approval_gate
import tools.agent_runtime.config as config_mod
import tools.agent_runtime.delegation as delegation
import tools.agent_runtime.dispatch as dispatch_mod
import tools.agent_runtime.goal_context as goal_context
import tools.agent_runtime.project_context as project_context
import tools.agent_runtime.safety as safety
import tools.agent_runtime.skills_lifecycle as skills_lifecycle
import tools.agent_runtime.toolsets as toolsets

REPO_ROOT = Path(__file__).resolve().parents[2]

_FULL_CONFIG = """
enabled: false
runtime:
  llm_function: summarization
  max_iterations: 3
  max_total_tokens: 4096
  max_cost_usd: 1.5
subsystems:
  project_context:
    enabled: false
    include_project_state: false
  standing_goals:
    enabled: false
    limit: 2
  profile_memory:
    enabled: false
  skill_proposals:
    enabled: true
  approval:
    mode: 'off'
    risk_function: classification
    command_mode: 'off'
  mutation:
    allow: true
  delegation:
    child_can_delegate: true
  toolsets:
    bundle_path: /somewhere/bundles.yaml
"""


def _write(tmp_path: Path, text: str) -> Path:
    """Write a config file the way the loader reads it: utf-8, no translation."""
    path = tmp_path / "agent_runtime.yaml"
    with path.open("w", encoding="utf-8", newline="") as fh:
        fh.write(text)
    return path


@pytest.fixture(autouse=True)
def _clean_config_cache(monkeypatch):
    """Every test starts from an unpointed, uncached loader."""
    monkeypatch.delenv(config_mod.ENV_CONFIG_PATH, raising=False)
    for name in config_mod.OVERRIDE_ENV_VARS:
        monkeypatch.delenv(name, raising=False)
    config_mod.reset_cache()
    yield
    config_mod.reset_cache()


def _point_at(monkeypatch, path: Path) -> None:
    monkeypatch.setenv(config_mod.ENV_CONFIG_PATH, str(path))
    config_mod.reset_cache()


class _StubChatManager:
    """Minimal in-memory ChatManager so ``AgentRuntime()`` builds without a DB.

    Mirrors ``tests/agent_runtime/test_cli.py``: these tests are about config
    resolution, not persistence, and the shared conftest schema has no chat
    tables.
    """

    _counter = 0

    def __init__(self, user_id, tenant_id: str = "") -> None:
        self.user_id = user_id
        self.tenant_id = tenant_id

    def create_context(self, **_kwargs) -> str:
        _StubChatManager._counter += 1
        return f"ctx-{_StubChatManager._counter}"

    def add_message(self, *_args, **_kwargs) -> None:
        return None


@pytest.fixture
def stub_session(monkeypatch):
    """Patch out chat persistence for the AgentRuntime construction tests."""
    import tools.agent_runtime.sessions as sess_mod

    monkeypatch.setattr(sess_mod, "ChatManager", _StubChatManager)
    monkeypatch.setattr(sess_mod, "ensure_chat_tables", lambda: True)
    yield


# ---------------------------------------------------------------------------
# Loading
# ---------------------------------------------------------------------------
def test_shipped_config_loads_and_is_found_from_this_file():
    """The repo's own args/agent_runtime.yaml parses and is locatable.

    Located from ``__file__``, never ``os.getcwd()`` — a worktree or a service
    started from ``/`` must resolve the same file.
    """
    cfg = config_mod.load_config(refresh=True)
    assert cfg.path is not None
    assert cfg.path == REPO_ROOT / "args" / "agent_runtime.yaml"
    assert cfg.get("subsystems.standing_goals.limit") == goal_context.DEFAULT_LIMIT


def test_missing_config_degrades_to_builtin_defaults(monkeypatch, tmp_path):
    """A config file is optional: absent means built-in defaults, not a crash."""
    _point_at(monkeypatch, tmp_path / "does-not-exist.yaml")
    cfg = config_mod.load_config()
    assert cfg.path is None
    assert cfg.data == {}
    assert cfg.enabled is True
    assert cfg.llm_function == config_mod.DEFAULT_LLM_FUNCTION
    assert cfg.max_iterations == config_mod.DEFAULT_MAX_ITERATIONS


def test_malformed_config_degrades_rather_than_raising(monkeypatch, tmp_path):
    path = _write(tmp_path, "subsystems: [this is not: a mapping\n")
    _point_at(monkeypatch, path)
    cfg = config_mod.load_config()
    assert cfg.data == {}
    assert cfg.approval_mode == "manual"   # still fail-safe, not fail-open


def test_partial_config_only_overrides_what_it_declares(monkeypatch, tmp_path):
    path = _write(tmp_path, "subsystems:\n  standing_goals:\n    limit: 7\n")
    _point_at(monkeypatch, path)
    cfg = config_mod.load_config()
    assert cfg.get("subsystems.standing_goals.limit") == 7
    assert cfg.enabled is True                       # undeclared -> default
    assert cfg.approval_mode == "manual"             # undeclared -> default


def test_config_is_read_from_the_wheel_layout(monkeypatch, tmp_path):
    """``icdev/data/args/`` is probed as well as ``<root>/args/`` (pip install)."""
    pkg = tmp_path / "icdev" / "tools" / "agent_runtime"
    pkg.mkdir(parents=True)
    data_args = tmp_path / "icdev" / "data" / "args"
    data_args.mkdir(parents=True)
    with (data_args / config_mod.CONFIG_FILENAME).open(
        "w", encoding="utf-8", newline=""
    ) as fh:
        fh.write("enabled: false\n")
    monkeypatch.setattr(config_mod, "__file__", str(pkg / "config.py"))
    found = config_mod._find_config_path()
    assert found == data_args / config_mod.CONFIG_FILENAME


def test_bom_prefixed_config_still_parses(monkeypatch, tmp_path):
    """A Windows editor's BOM must not silently blank the whole config."""
    path = _write(tmp_path, "﻿subsystems:\n  standing_goals:\n    limit: 4\n")
    _point_at(monkeypatch, path)
    assert config_mod.load_config().get("subsystems.standing_goals.limit") == 4


# ---------------------------------------------------------------------------
# Precedence — env over config, everywhere
# ---------------------------------------------------------------------------
def test_config_supplies_values_when_no_env_var_is_set(monkeypatch, tmp_path):
    _point_at(monkeypatch, _write(tmp_path, _FULL_CONFIG))

    assert config_mod.load_config().enabled is False
    assert goal_context.goals_enabled() is False
    assert goal_context.goal_limit() == 2
    assert project_context.context_enabled() is False
    assert project_context.project_state_enabled() is False
    assert skills_lifecycle.proposals_enabled() is True
    assert safety.resolve_mode() == "off"
    assert safety.resolve_risk_function() == "classification"
    assert approval_gate.resolve_mode() == "off"
    assert dispatch_mod.mutation_allowed() is True
    assert delegation._child_can_delegate() is True
    assert toolsets._bundle_path() == Path("/somewhere/bundles.yaml")


@pytest.mark.parametrize(
    ("env_name", "env_value", "probe", "expected"),
    [
        ("ICDEV_SAG_ENABLED", "true", lambda: config_mod.load_config().enabled, True),
        ("ICDEV_SAG_GOALS", "1", goal_context.goals_enabled, True),
        ("ICDEV_SAG_GOAL_LIMIT", "9", goal_context.goal_limit, 9),
        ("ICDEV_SAG_PROJECT_CONTEXT", "1", project_context.context_enabled, True),
        ("ICDEV_SAG_PROJECT_STATE", "1", project_context.project_state_enabled, True),
        ("ICDEV_SAG_SKILL_PROPOSALS", "0", skills_lifecycle.proposals_enabled, False),
        ("ICDEV_SAG_APPROVAL_MODE", "manual", safety.resolve_mode, "manual"),
        (
            "ICDEV_SAG_RISK_FUNCTION",
            "summarization",
            safety.resolve_risk_function,
            "summarization",
        ),
        ("ICDEV_AGENT_APPROVAL_MODE", "enforce", approval_gate.resolve_mode, "enforce"),
        ("ICDEV_SAG_ALLOW_MUTATION", "0", dispatch_mod.mutation_allowed, False),
        (
            "ICDEV_SAG_MAX_ITERATIONS",
            "25",
            lambda: config_mod.load_config().max_iterations,
            25,
        ),
        (
            "ICDEV_SAG_LLM_FUNCTION",
            "code_generation",
            lambda: config_mod.load_config().llm_function,
            "code_generation",
        ),
    ],
)
def test_env_var_beats_the_config_file(
    monkeypatch, tmp_path, env_name, env_value, probe, expected
):
    """Every env var wins over a config file that says the opposite.

    ``_FULL_CONFIG`` sets each of these to the *other* value, so a passing
    assertion can only mean the environment was consulted first.
    """
    _point_at(monkeypatch, _write(tmp_path, _FULL_CONFIG))
    monkeypatch.setenv(env_name, env_value)
    assert probe() == expected


def test_unparseable_env_value_falls_through_to_the_config(monkeypatch, tmp_path):
    """A typo in an env var falls through a layer; it never zeroes the setting."""
    _point_at(monkeypatch, _write(tmp_path, _FULL_CONFIG))
    monkeypatch.setenv("ICDEV_SAG_GOAL_LIMIT", "not-a-number")
    assert goal_context.goal_limit() == 2          # the config's value, not 0
    monkeypatch.setenv("ICDEV_SAG_GOALS", "maybe")
    assert goal_context.goals_enabled() is False   # the config's value, not True


def test_a_typo_never_resolves_to_off(monkeypatch, tmp_path):
    """An unrecognised approval mode resolves to the strictest value, not `off`.

    Checked at both layers: a bad env var and a bad config value must each land
    on ``manual`` / ``enforce`` rather than silently disabling the gate.
    """
    path = _write(tmp_path, "subsystems:\n  approval:\n    mode: manual\n")
    _point_at(monkeypatch, path)
    monkeypatch.setenv("ICDEV_SAG_APPROVAL_MODE", "enfroce")
    assert safety.resolve_mode() == "manual"

    _point_at(
        monkeypatch,
        _write(
            tmp_path,
            "subsystems:\n  approval:\n    mode: disabled\n    command_mode: nope\n",
        ),
    )
    monkeypatch.delenv("ICDEV_SAG_APPROVAL_MODE", raising=False)
    assert safety.resolve_mode() == "manual"
    assert approval_gate.resolve_mode() == "enforce"


def test_explicit_argument_beats_both_layers(monkeypatch, tmp_path):
    _point_at(monkeypatch, _write(tmp_path, _FULL_CONFIG))
    monkeypatch.setenv("ICDEV_SAG_APPROVAL_MODE", "off")
    assert safety.resolve_mode("manual") == "manual"
    assert approval_gate.resolve_mode("enforce") == "enforce"


def test_env_changes_take_effect_without_reloading_the_file(monkeypatch, tmp_path):
    """The YAML is cached; ``os.environ`` is not. A /set must apply at once."""
    _point_at(monkeypatch, _write(tmp_path, _FULL_CONFIG))
    assert goal_context.goals_enabled() is False
    monkeypatch.setenv("ICDEV_SAG_GOALS", "1")
    assert goal_context.goals_enabled() is True
    monkeypatch.delenv("ICDEV_SAG_GOALS")
    assert goal_context.goals_enabled() is False


# ---------------------------------------------------------------------------
# Fail-safe defaults
# ---------------------------------------------------------------------------
def test_mutation_gate_stays_fail_closed_without_a_config(monkeypatch, tmp_path):
    """No config file must not mean "allow writes"."""
    _point_at(monkeypatch, tmp_path / "absent.yaml")
    assert dispatch_mod.mutation_allowed() is False
    allowed, reason = dispatch_mod.default_safety_gate("write_file", {}, False)
    assert allowed is False
    assert "ICDEV_SAG_ALLOW_MUTATION" in reason


def test_skill_proposals_stay_off_without_a_config(monkeypatch, tmp_path):
    _point_at(monkeypatch, tmp_path / "absent.yaml")
    assert skills_lifecycle.proposals_enabled() is False


def test_config_declares_no_model_id():
    """LLM-agnostic: routing functions only — a model id here pins a vendor."""
    text = (REPO_ROOT / "args" / "agent_runtime.yaml").read_text(encoding="utf-8")
    # Comments explain why there is no model key; only the DATA is checked.
    data_lines = [
        ln.lower()
        for ln in text.splitlines()
        if ln.strip() and not ln.lstrip().startswith("#")
    ]
    for token in ("claude", "gpt-", "llama", "anthropic", "model:", "model_id"):
        offenders = [ln for ln in data_lines if token in ln]
        assert not offenders, f"{token!r} must not appear in agent_runtime.yaml data"


# ---------------------------------------------------------------------------
# AgentRuntime reads the config
# ---------------------------------------------------------------------------
def test_agent_runtime_reads_the_config_at_construction(stub_session, monkeypatch, tmp_path):
    from tools.agent_runtime.runtime import AgentRuntime

    _point_at(monkeypatch, _write(tmp_path, _FULL_CONFIG))
    runtime = AgentRuntime()
    assert runtime.config is config_mod.load_config()
    assert runtime.llm_function == "summarization"
    assert runtime.max_iterations == 3
    assert runtime.max_total_tokens == 4096
    assert runtime.max_cost_usd == 1.5


def test_agent_runtime_lets_the_environment_win(stub_session, monkeypatch, tmp_path):
    from tools.agent_runtime.runtime import AgentRuntime

    _point_at(monkeypatch, _write(tmp_path, _FULL_CONFIG))
    monkeypatch.setenv("ICDEV_SAG_MAX_ITERATIONS", "7")
    monkeypatch.setenv("ICDEV_SAG_LLM_FUNCTION", "code_generation")
    runtime = AgentRuntime()
    assert runtime.max_iterations == 7
    assert runtime.llm_function == "code_generation"


def test_explicit_kwargs_beat_the_config(stub_session, monkeypatch, tmp_path):
    from tools.agent_runtime.runtime import AgentRuntime

    _point_at(monkeypatch, _write(tmp_path, _FULL_CONFIG))
    runtime = AgentRuntime(llm_function="chat", max_iterations=42, max_cost_usd=9.0)
    assert runtime.llm_function == "chat"
    assert runtime.max_iterations == 42
    assert runtime.max_cost_usd == 9.0


def test_agent_runtime_without_a_config_matches_the_historic_defaults(
    stub_session, monkeypatch, tmp_path
):
    """Deleting the YAML changes nothing about how the agent runs."""
    from tools.agent_runtime.runtime import AgentRuntime

    _point_at(monkeypatch, tmp_path / "absent.yaml")
    runtime = AgentRuntime()
    assert runtime.llm_function == "code_generation"
    assert runtime.max_iterations == 12
    assert runtime.max_total_tokens is None
    assert runtime.max_cost_usd is None


def test_profile_memory_toggle_is_honoured(stub_session, monkeypatch, tmp_path):
    """The toggle actually suppresses the injection, not just the config read."""
    from tools.agent_runtime.runtime import AgentRuntime

    _point_at(monkeypatch, _write(tmp_path, _FULL_CONFIG))
    runtime = AgentRuntime()
    assert runtime._profile_memory_enabled() is False

    called: list[tuple] = []

    def _boom(*args, **kwargs):  # pragma: no cover - must never run
        called.append((args, kwargs))
        return "PROFILE BLOCK"

    monkeypatch.setattr(
        "tools.agent_runtime.profile_memory.build_profile_context", _boom
    )
    assert "PROFILE BLOCK" not in runtime._effective_system_prompt("hello")
    assert called == []

    monkeypatch.setenv(config_mod.ENV_PROFILE_MEMORY, "1")
    runtime2 = AgentRuntime()
    assert runtime2._profile_memory_enabled() is True


# ---------------------------------------------------------------------------
# Registry + CLI
# ---------------------------------------------------------------------------
def test_sag_is_registered_as_a_core_extension():
    from tools.config.component_registry import get_registry

    comp = get_registry().get("sag")
    assert comp is not None
    assert comp.kind == "core_extension"
    assert comp.env_flag == config_mod.ENV_ENABLED
    # No blueprint: the dashboard's core-extension loop must skip it entirely.
    assert not comp.blueprint_attr


def test_sag_is_reachable_from_icdev_list_and_status():
    from tools.config.component_registry import get_registry

    registry = get_registry()
    assert "sag" in registry.get_cli_toggles()
    assert registry.get_cli_toggles()["sag"] == [config_mod.ENV_ENABLED]
    assert registry.get_cli_descriptions()["sag"] == "Standalone Agent Runtime"


def test_packaged_config_mirror_has_not_drifted():
    """``icdev/data/args/agent_runtime.yaml`` must match ``args/`` byte for byte.

    The loader probes ``<parent>/args/`` and ``<parent>/data/args/`` at every
    level walking up from its own file, so from the ``icdev/`` mirror the
    packaged copy is found BEFORE the repo one. A drifted mirror would therefore
    silently win for anything importing ``icdev.tools.agent_runtime.config`` —
    the same failure mode ``args/component_registry.yaml`` is gated against.
    """
    source = REPO_ROOT / "args" / "agent_runtime.yaml"
    mirror = REPO_ROOT / "icdev" / "data" / "args" / "agent_runtime.yaml"
    if not mirror.exists():
        pytest.skip(f"mirror not present in this checkout: {mirror}")
    assert source.read_bytes() == mirror.read_bytes(), (
        "icdev/data/args/agent_runtime.yaml has drifted from args/agent_runtime.yaml"
    )


def test_cli_reports_resolved_config_as_json():
    """``python -m tools.agent_runtime.config --json`` is the documented probe."""
    proc = subprocess.run(
        [sys.executable, "-m", "tools.agent_runtime.config", "--json"],
        cwd=str(REPO_ROOT),
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=180,
    )
    assert proc.returncode == 0, proc.stderr
    report = json.loads(proc.stdout)
    assert report["config_found"] is True
    assert report["resolved"]["runtime"]["llm_function"]
    assert "standing_goals" in report["resolved"]["subsystems"]
