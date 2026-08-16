# CUI // SP-CTI
"""The approval gate is ARMED by configuration, not only by an env var (rem-cap-04).

`tests/test_agent_approval_gate.py` proves the gate behaves correctly *once it
exists*. This file proves the thing that was actually missing: that it exists at
all on the eleven call sites that pass `approval_gate=None`.

Before rem-cap-04, `_resolve_approval_gate` read `ICDEV_AGENT_APPROVAL_MODE`
straight out of `os.environ` and returned `None` — no gate whatsoever — whenever
it was unset. `args/agent_runtime.yaml` could therefore say `command_mode:
enforce`, `tools.agent_runtime.approval_gate.resolve_mode()` could agree, and
every default call site still ran ungated. It did: the gate had evaluated zero
tool calls against 3,214 dispatched autonomous builds
(`docs/security/approval-gate-reachability.md`, rem-cap-03).

So the property under test is a JOIN between two modules that were not talking:
what `resolve_mode()` answers must be what `_resolve_approval_gate(None)` does.
The tests point the loader at their own YAML in `tmp_path` (the convention in
`tests/agent_runtime/test_config.py`) so they pin the WIRING and do not break
when a shipped default is retuned — except `TestShippedDefault`, whose whole job
is to pin the shipped default.
"""
from __future__ import annotations

import builtins
from pathlib import Path

import pytest

import tools.agent_runtime.config as config_mod
from icdev.tools.llm.agent_loop import _resolve_approval_gate
from tools.agent_runtime.approval_gate import MODE_DRY_RUN, MODE_ENFORCE, MODE_OFF, resolve_mode

REPO_ROOT = Path(__file__).resolve().parents[1]
SHIPPED_CONFIG = REPO_ROOT / "args" / "agent_runtime.yaml"
PACKAGED_CONFIG = REPO_ROOT / "icdev" / "data" / "args" / "agent_runtime.yaml"

MODE_ENV = "ICDEV_AGENT_APPROVAL_MODE"


def _config_saying(tmp_path: Path, command_mode: str) -> Path:
    path = tmp_path / "agent_runtime.yaml"
    with path.open("w", encoding="utf-8", newline="") as fh:
        fh.write(f"subsystems:\n  approval:\n    command_mode: '{command_mode}'\n")
    return path


@pytest.fixture(autouse=True)
def _isolated_config(monkeypatch):
    """Start from an uncached loader with no approval env var in force."""
    monkeypatch.delenv(config_mod.ENV_CONFIG_PATH, raising=False)
    for name in config_mod.OVERRIDE_ENV_VARS:
        monkeypatch.delenv(name, raising=False)
    config_mod.reset_cache()
    yield
    config_mod.reset_cache()


def _point_at(monkeypatch, path: Path) -> None:
    monkeypatch.setenv(config_mod.ENV_CONFIG_PATH, str(path))
    config_mod.reset_cache()


# ---------------------------------------------------------------------------
# 1. The join: a configured mode arms the default call sites
# ---------------------------------------------------------------------------
class TestConfigArmsTheDefault:
    @pytest.mark.parametrize("mode", [MODE_ENFORCE, MODE_DRY_RUN])
    def test_a_configured_mode_builds_a_hook_with_no_env_var_set(
        self, monkeypatch, tmp_path, mode
    ):
        """THE regression. Unset env + config that says `enforce` used to mean no gate."""
        _point_at(monkeypatch, _config_saying(tmp_path, mode))
        assert MODE_ENV not in __import__("os").environ
        assert resolve_mode() == mode

        hook = _resolve_approval_gate(None)
        assert hook is not None, (
            f"config says command_mode={mode!r} but the default call site got no gate"
        )
        assert callable(hook)

    def test_the_resolver_and_the_loop_cannot_disagree(self, monkeypatch, tmp_path):
        """Whatever `resolve_mode()` answers is what the loop does — for every mode.

        Stated as one property rather than three cases, because the defect was
        precisely that these two could answer differently.
        """
        for mode in (MODE_ENFORCE, MODE_DRY_RUN, MODE_OFF):
            _point_at(monkeypatch, _config_saying(tmp_path, mode))
            gated = _resolve_approval_gate(None) is not None
            assert gated is (resolve_mode() != MODE_OFF), f"disagreement at mode={mode!r}"

    def test_config_off_leaves_the_default_ungated(self, monkeypatch, tmp_path):
        _point_at(monkeypatch, _config_saying(tmp_path, MODE_OFF))
        assert _resolve_approval_gate(None) is None


# ---------------------------------------------------------------------------
# 2. The operator's env var still wins — the escape hatch must not regress
# ---------------------------------------------------------------------------
class TestEnvStillWins:
    def test_env_off_beats_a_config_that_says_enforce(self, monkeypatch, tmp_path):
        """An operator who exported `off` did not ask the YAML for a second opinion."""
        _point_at(monkeypatch, _config_saying(tmp_path, MODE_ENFORCE))
        monkeypatch.setenv(MODE_ENV, "off")
        assert resolve_mode() == MODE_OFF
        assert _resolve_approval_gate(None) is None

    def test_env_enforce_beats_a_config_that_says_off(self, monkeypatch, tmp_path):
        _point_at(monkeypatch, _config_saying(tmp_path, MODE_OFF))
        monkeypatch.setenv(MODE_ENV, "enforce")
        assert _resolve_approval_gate(None) is not None


# ---------------------------------------------------------------------------
# 3. Explicit arguments keep their old meaning — no call site changed
# ---------------------------------------------------------------------------
class TestExplicitArgumentsUnchanged:
    def test_false_disables_even_when_the_config_enforces(self, monkeypatch, tmp_path):
        _point_at(monkeypatch, _config_saying(tmp_path, MODE_ENFORCE))
        assert _resolve_approval_gate(False) is None

    def test_a_callable_is_taken_as_is_even_when_the_config_is_off(
        self, monkeypatch, tmp_path
    ):
        _point_at(monkeypatch, _config_saying(tmp_path, MODE_OFF))
        sentinel = lambda n, i: None  # noqa: E731
        assert _resolve_approval_gate(sentinel) is sentinel

    def test_true_builds_a_hook_even_when_the_config_is_off(self, monkeypatch, tmp_path):
        """`True` is a caller demanding the gate; `off` is a default, not a veto."""
        _point_at(monkeypatch, _config_saying(tmp_path, MODE_OFF))
        assert callable(_resolve_approval_gate(True))


# ---------------------------------------------------------------------------
# 4. A broken config layer must not deny every default call site
# ---------------------------------------------------------------------------
class TestBrokenGateModule:
    @staticmethod
    def _break_the_import(monkeypatch):
        real_import = builtins.__import__

        def _fail(name, *args, **kwargs):
            if name == "tools.agent_runtime.approval_gate":
                raise ImportError("gate module is broken")
            return real_import(name, *args, **kwargs)

        monkeypatch.setattr(builtins, "__import__", _fail)

    def test_an_unaskable_mode_leaves_the_default_ungated(self, monkeypatch):
        """Nobody asked for a gate here, so a broken module is an outage, not a deny.

        The asymmetry is the point: `None` means "use the operator's default",
        and if the module that knows the default cannot be loaded there is no
        request to fail closed on. Refusing every tool call on all eleven
        default sites because a config layer broke is not fail-closed.
        """
        self._break_the_import(monkeypatch)
        hook = _resolve_approval_gate(None)
        monkeypatch.undo()
        assert hook is None

    def test_an_explicit_request_still_denies_everything(self, monkeypatch):
        """`True` IS a request, and a request that cannot be honoured fails closed."""
        self._break_the_import(monkeypatch)
        hook = _resolve_approval_gate(True)
        monkeypatch.undo()
        assert hook is not None
        assert "BLOCKED" in hook("anything_at_all", {})


# ---------------------------------------------------------------------------
# 5. The shipped default is `dry_run` — the survey, not the enforcement
# ---------------------------------------------------------------------------
class TestShippedDefault:
    """Pins the SHIPPED value, deliberately, unlike every test above.

    `enforce` is not survivable yet: 81% of the tools the SAG toolsets register
    classify `unknown`, the policy is fail-closed, and the default approver
    denies on EOF. `dry_run` still writes the `agent_approval_log` row and then
    allows, which is the fire-rate survey CLAUDE.md demands before arming a
    security check. Promotion is a later, per-bundle task.
    """

    @pytest.mark.parametrize("path", [SHIPPED_CONFIG, PACKAGED_CONFIG], ids=["args", "packaged"])
    def test_the_shipped_config_says_dry_run(self, monkeypatch, path):
        assert path.is_file(), f"{path} is missing"
        _point_at(monkeypatch, path)
        assert resolve_mode() == MODE_DRY_RUN
        assert _resolve_approval_gate(None) is not None

    def test_both_copies_agree(self):
        """A wheel resolves `icdev/data/args/`; a checkout resolves `args/`.

        If only one shipped `dry_run` the other would fall through to the
        posture, which supplies `enforce` — the 81% refusal, in exactly the
        deployment nobody tests locally.
        """
        assert SHIPPED_CONFIG.read_text(encoding="utf-8") == PACKAGED_CONFIG.read_text(
            encoding="utf-8"
        )

    def test_the_config_states_why_it_is_not_enforce(self):
        """A pinned security default with no stated reason regresses on the next edit."""
        text = SHIPPED_CONFIG.read_text(encoding="utf-8")
        assert "command_mode: dry_run" in text
        assert "81%" in text
        assert "approval-gate-reachability" in text
