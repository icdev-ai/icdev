# CUI // SP-CTI
"""Unit tests for permission postures (hcx-post-01).

A posture names a COMBINATION of the safety knobs — sandbox confinement,
approval mode, command-approval mode, mutation gate — so a run can say which
posture it was under. Two properties are load-bearing and most of this file is
about them:

1. **A posture never overrules a higher layer.** It is a default-setter sitting
   at the bottom of ``argument > env > agent_runtime.yaml > posture > default``.
   An operator who exported ``ICDEV_SAG_APPROVAL_MODE`` stated an intent that a
   posture name must not reverse — the same reasoning that put
   ``agent_runtime.yaml`` below the environment in hgx-cfg-01.

2. **A file cannot select the dangerous posture.** ``danger-full-access`` is
   reachable only from an explicit call argument or ``ICDEV_PERMISSION_POSTURE``
   — an act somebody performed — never from the shipped file's ``default:`` key,
   and not by re-declaring the posture without its flag.

The third theme is inertness. A posture key nothing reads would be the
declared-but-never-consumed defect in miniature, so
:func:`test_every_posture_knob_actually_moves_a_resolved_value` proves each of
the four keys changes something, and
:func:`test_the_shipped_config_pins_only_the_posture_governed_knobs_it_argues_for`
proves the shipped ``agent_runtime.yaml`` does not sit on top of them — except
for the pins enumerated, with their reasons, in :data:`SANCTIONED_PINS`. It is
the ONE place the shipped defaults are asserted, so every other posture test can
point the loader at its own config and stay about the mechanism.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

import tools.agent_runtime.approval_gate as approval_gate
import tools.agent_runtime.config as config_mod
import tools.agent_runtime.dispatch as dispatch_mod
import tools.agent_runtime.safety as safety
from tools.agents.adapter_base import AgentSession
from tools.agents.adapters import codex_cli

REPO_ROOT = Path(__file__).resolve().parents[2]

#: Every key the shipped posture file is allowed to declare. Four knobs, plus
#: two pieces of metadata. A key outside this set is a key with no reader.
KNOWN_POSTURE_KEYS = {
    "description",
    "requires_explicit_selection",
    "sandbox",
    "approval_mode",
    "command_approval_mode",
    "allow_mutation",
}


def _write(tmp_path: Path, name: str, text: str) -> Path:
    path = tmp_path / name
    with path.open("w", encoding="utf-8", newline="") as fh:
        fh.write(text)
    return path


@pytest.fixture(autouse=True)
def _clean(monkeypatch, tmp_path):
    """Start every test unpointed, uncached, and with an EMPTY agent_runtime.yaml.

    Pointing the config layer at an empty file is deliberate: these tests are
    about the posture layer, and inheriting whatever the repo's shipped
    ``agent_runtime.yaml`` happens to declare would make them fail for reasons
    that are not about postures. The one test that DOES care about the shipped
    file says so in its name.
    """
    for name in config_mod.OVERRIDE_ENV_VARS:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.delenv("ICDEV_CODEX_SANDBOX", raising=False)
    monkeypatch.setenv(
        config_mod.ENV_CONFIG_PATH, str(_write(tmp_path, "agent_runtime.yaml", "{}\n"))
    )
    config_mod.reset_cache()
    yield
    config_mod.reset_cache()


def _point_at_postures(monkeypatch, path: Path) -> None:
    monkeypatch.setenv(config_mod.ENV_POSTURES_PATH, str(path))
    config_mod.reset_cache()


# ---------------------------------------------------------------------------
# The shipped file
# ---------------------------------------------------------------------------
def test_the_shipped_posture_file_loads_and_is_found_from_this_file():
    """Located from ``__file__``, never ``os.getcwd()`` — worktree-safe."""
    postures = config_mod.load_postures(refresh=True)
    assert postures.path == REPO_ROOT / "args" / "permission_postures.yaml"
    assert set(postures.postures) >= {"workspace-write", "danger-full-access"}


def test_the_shipped_file_declares_no_key_without_a_reader():
    """An inert posture key would claim a reach the file does not have."""
    declared = config_mod.load_postures(refresh=True).data["postures"]
    for name, values in declared.items():
        unknown = set(values) - KNOWN_POSTURE_KEYS
        assert not unknown, f"posture {name!r} declares unread key(s): {sorted(unknown)}"


def test_the_default_posture_reproduces_the_pre_posture_behaviour(monkeypatch):
    """Adopting this layer must not change what an unconfigured run does."""
    cfg = config_mod.load_config()
    assert cfg.posture_name == config_mod.DEFAULT_POSTURE == "workspace-write"
    assert cfg.approval_mode == "manual"
    assert cfg.command_approval_mode == "enforce"
    assert cfg.sandbox_mode == "workspace-write"
    assert dispatch_mod.mutation_allowed() is False


#: Posture-governed knobs the shipped ``agent_runtime.yaml`` is allowed to pin,
#: each with the reason it earns the exception. Enumerated by name rather than
#: counted, and never widened to "some pins are fine": a pin makes the posture
#: partly inert for that knob, so each one has to be argued.
SANCTIONED_PINS = {
    # rem-cap-04. Both built-in postures would otherwise supply a mode that does
    # not work: `workspace-write` gives `enforce`, and the gate's policy is
    # fail-closed while 81% of the tools the SAG toolsets register are
    # unenumerated — four refusals in five, unattended, with `console_approver`
    # denying on EOF. `dry_run` records the decision and allows, which is the
    # fire-rate survey CLAUDE.md requires before arming a security check.
    # The inertness this costs is small and was checked, not assumed: `off`
    # (danger-full-access) and `dry_run` BOTH write the row and BOTH allow, so
    # the pin changes the recorded reason for that posture and not its
    # behaviour. Promotion to `enforce` is a later, per-bundle task; when it
    # lands the pin should move to the posture, not widen this set.
    # Finding: docs/security/approval-gate-reachability.md
    "subsystems.approval.command_mode": "dry_run",
}


def test_the_shipped_config_pins_only_the_posture_governed_knobs_it_argues_for():
    """``agent_runtime.yaml`` sits ABOVE the posture, so a value there is a pin.

    The posture-governed keys ship commented out for exactly this reason. If one
    is uncommented, selecting a posture stops moving that knob and the posture
    becomes partly inert — silently, because both files still parse. So a pin is
    allowed only when it is listed in :data:`SANCTIONED_PINS` with a written
    reason, and the exact value is asserted too: an unreviewed edit from
    ``dry_run`` to ``enforce`` is the failure mode this guards, and it would slip
    past a mere "is it pinned?" check.
    """
    import yaml

    shipped = yaml.safe_load(
        (REPO_ROOT / "args" / "agent_runtime.yaml").read_text(encoding="utf-8")
    )
    subsystems = shipped.get("subsystems") or {}
    approval = subsystems.get("approval") or {}
    found = {
        "subsystems.approval.mode": approval.get("mode"),
        "subsystems.approval.command_mode": approval.get("command_mode"),
        "subsystems.sandbox.mode": (subsystems.get("sandbox") or {}).get("mode"),
        "subsystems.mutation.allow": (subsystems.get("mutation") or {}).get("allow"),
    }
    for dotted, value in found.items():
        if value is None:
            assert dotted not in SANCTIONED_PINS, (
                f"{dotted} is sanctioned as a pin but is no longer set; drop it "
                "from SANCTIONED_PINS so the posture is documented as governing it"
            )
            continue
        assert dotted in SANCTIONED_PINS, (
            f"{dotted}={value!r} pins a posture-governed knob. Selecting a posture "
            "will no longer move it. Argue the pin in SANCTIONED_PINS or remove it."
        )
        assert value == SANCTIONED_PINS[dotted], (
            f"{dotted} is pinned to {value!r}, but the sanctioned value is "
            f"{SANCTIONED_PINS[dotted]!r}. Re-argue the pin before changing it."
        )


def test_the_packaged_config_pins_exactly_what_the_checkout_pins():
    """A wheel resolves ``icdev/data/args/``; a checkout resolves ``args/``.

    `_find_config_path` probes both layouts, so a pin present in one and absent
    from the other is a knob that resolves differently depending on how ICDEV
    was installed — in the deployment nobody tests locally.
    """
    checkout = (REPO_ROOT / "args" / "agent_runtime.yaml").read_text(encoding="utf-8")
    packaged = (
        REPO_ROOT / "icdev" / "data" / "args" / "agent_runtime.yaml"
    ).read_text(encoding="utf-8")
    assert checkout == packaged


def test_every_posture_knob_actually_moves_a_resolved_value(monkeypatch):
    """Each of the four keys must change something, or it is a dead key."""
    def resolved() -> dict:
        config_mod.reset_cache()
        cfg = config_mod.load_config()
        return {
            "sandbox": cfg.sandbox_mode,
            "approval_mode": cfg.approval_mode,
            "command_approval_mode": cfg.command_approval_mode,
            "allow_mutation": dispatch_mod.mutation_allowed(),
        }

    safe = resolved()
    monkeypatch.setenv(config_mod.ENV_POSTURE, "danger-full-access")
    dangerous = resolved()

    for key in ("sandbox", "approval_mode", "command_approval_mode", "allow_mutation"):
        assert safe[key] != dangerous[key], f"posture key {key!r} changes nothing"


def test_the_knobs_reach_the_modules_that_own_them(monkeypatch):
    """Not just the config object: the consuming modules must see the posture."""
    monkeypatch.setenv(config_mod.ENV_POSTURE, "danger-full-access")
    config_mod.reset_cache()
    assert safety.resolve_mode() == "off"
    assert approval_gate.resolve_mode() == "off"
    assert dispatch_mod.mutation_allowed() is True
    allowed, _reason = dispatch_mod.default_safety_gate("write_file", {}, False)
    assert allowed is True


# ---------------------------------------------------------------------------
# Precedence — a posture never overrules a higher layer
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    "env_name,env_value,read",
    [
        ("ICDEV_SAG_APPROVAL_MODE", "manual", lambda: config_mod.load_config().approval_mode),
        (
            "ICDEV_AGENT_APPROVAL_MODE",
            "enforce",
            lambda: config_mod.load_config().command_approval_mode,
        ),
        ("ICDEV_SAG_ALLOW_MUTATION", "0", lambda: dispatch_mod.mutation_allowed()),
        ("ICDEV_SAG_SANDBOX_MODE", "read-only", lambda: config_mod.load_config().sandbox_mode),
    ],
)
def test_an_exported_env_var_beats_the_posture(monkeypatch, env_name, env_value, read):
    """The whole point: naming a posture cannot reverse an exported flag."""
    monkeypatch.setenv(config_mod.ENV_POSTURE, "danger-full-access")
    monkeypatch.setenv(env_name, env_value)
    config_mod.reset_cache()
    resolved = read()
    assert str(resolved).lower() in (env_value.lower(), "false")


def test_an_explicit_config_key_beats_the_posture(monkeypatch, tmp_path):
    """``agent_runtime.yaml`` is more specific than a named bundle, and wins."""
    monkeypatch.setenv(
        config_mod.ENV_CONFIG_PATH,
        str(
            _write(
                tmp_path,
                "agent_runtime.yaml",
                "subsystems:\n"
                "  approval:\n"
                "    mode: manual\n"
                "  mutation:\n"
                "    allow: false\n",
            )
        ),
    )
    monkeypatch.setenv(config_mod.ENV_POSTURE, "danger-full-access")
    config_mod.reset_cache()
    assert config_mod.load_config().approval_mode == "manual"
    assert dispatch_mod.mutation_allowed() is False
    # ...and a knob the file does NOT pin still moves with the posture.
    assert config_mod.load_config().sandbox_mode == "danger-full-access"


def test_a_posture_cannot_smuggle_in_a_mode_outside_the_choices(monkeypatch, tmp_path):
    """``choices`` filtering applies to the posture layer too."""
    _point_at_postures(
        monkeypatch,
        _write(
            tmp_path,
            "permission_postures.yaml",
            "default: odd\npostures:\n  odd:\n    approval_mode: banana\n",
        ),
    )
    assert config_mod.load_config().approval_mode == "manual"


# ---------------------------------------------------------------------------
# Selection — danger-full-access takes an explicit human act
# ---------------------------------------------------------------------------
def test_the_files_default_key_cannot_select_a_dangerous_posture(monkeypatch, tmp_path):
    _point_at_postures(
        monkeypatch,
        _write(tmp_path, "permission_postures.yaml", "default: danger-full-access\n"),
    )
    name, source = config_mod.load_postures().resolve_name()
    assert (name, source) == ("workspace-write", config_mod.POSTURE_SOURCE_BUILTIN)
    assert dispatch_mod.mutation_allowed() is False


def test_a_file_cannot_relax_the_flag_on_a_builtin_posture(monkeypatch, tmp_path):
    """Otherwise the guard is one config line away from being switched off."""
    _point_at_postures(
        monkeypatch,
        _write(
            tmp_path,
            "permission_postures.yaml",
            "default: danger-full-access\n"
            "postures:\n"
            "  danger-full-access:\n"
            "    requires_explicit_selection: false\n",
        ),
    )
    assert config_mod.load_postures().requires_explicit_selection("danger-full-access")
    assert config_mod.load_postures().resolve_name()[0] == "workspace-write"


def test_the_environment_may_select_a_dangerous_posture(monkeypatch):
    """Exporting the variable IS the explicit act — it must still work."""
    monkeypatch.setenv(config_mod.ENV_POSTURE, "danger-full-access")
    config_mod.reset_cache()
    assert config_mod.load_postures().resolve_name() == (
        "danger-full-access",
        config_mod.POSTURE_SOURCE_ENV,
    )


def test_an_explicit_argument_may_select_a_dangerous_posture():
    assert config_mod.load_postures().resolve_name("danger-full-access") == (
        "danger-full-access",
        config_mod.POSTURE_SOURCE_ARGUMENT,
    )


def test_an_unknown_posture_name_falls_through_rather_than_resolving_to_nothing(
    monkeypatch,
):
    """A typo must land on the safe posture, not on an empty one."""
    monkeypatch.setenv(config_mod.ENV_POSTURE, "workspce-write")
    config_mod.reset_cache()
    assert config_mod.load_postures().resolve_name()[0] == "workspace-write"
    assert dispatch_mod.mutation_allowed() is False


# ---------------------------------------------------------------------------
# Degradation — the file is documentation, not a dependency
# ---------------------------------------------------------------------------
def test_a_missing_posture_file_leaves_both_postures_available(monkeypatch, tmp_path):
    _point_at_postures(monkeypatch, tmp_path / "absent.yaml")
    postures = config_mod.load_postures()
    assert postures.path is None
    assert set(postures.postures) == set(config_mod.BUILTIN_POSTURES)
    assert postures.resolve_name()[0] == "workspace-write"
    assert dispatch_mod.mutation_allowed() is False


def test_a_malformed_posture_file_degrades_rather_than_raising(monkeypatch, tmp_path):
    _point_at_postures(
        monkeypatch,
        _write(tmp_path, "permission_postures.yaml", "postures: [not: a mapping\n"),
    )
    assert config_mod.load_postures().resolve_name()[0] == "workspace-write"
    assert config_mod.load_config().approval_mode == "manual"


def test_a_partially_declared_posture_keeps_the_builtin_keys(monkeypatch, tmp_path):
    """A half-written posture must not blank a safety knob into "unset"."""
    _point_at_postures(
        monkeypatch,
        _write(
            tmp_path,
            "permission_postures.yaml",
            "postures:\n  workspace-write:\n    sandbox: read-only\n",
        ),
    )
    cfg = config_mod.load_config()
    assert cfg.sandbox_mode == "read-only"     # the file's value
    assert cfg.approval_mode == "manual"       # the built-in's, not lost
    assert dispatch_mod.mutation_allowed() is False


def test_a_non_mapping_posture_entry_is_ignored(monkeypatch, tmp_path):
    _point_at_postures(
        monkeypatch,
        _write(tmp_path, "permission_postures.yaml", "postures:\n  broken: 'a string'\n"),
    )
    assert config_mod.load_config().approval_mode == "manual"


# ---------------------------------------------------------------------------
# The adapter — the reader for the `sandbox` key
# ---------------------------------------------------------------------------
def _sandbox_argv(metadata: dict) -> str:
    adapter = codex_cli.CodexCliAdapter()
    adapter.resolve = lambda: "<codex>"          # type: ignore[method-assign]
    argv = adapter.build_argv(
        AgentSession(task_id="t", prompt="p", working_dir=".", metadata=metadata)
    )
    return argv[argv.index("--sandbox") + 1] if "--sandbox" in argv else ""


def test_the_adapter_default_is_unchanged_by_the_posture_layer():
    assert _sandbox_argv({}) == "workspace-write"


def test_the_posture_moves_the_adapter_sandbox(monkeypatch):
    monkeypatch.setenv(config_mod.ENV_POSTURE, "danger-full-access")
    config_mod.reset_cache()
    assert _sandbox_argv({}) == "danger-full-access"


def test_the_adapter_env_var_beats_the_posture(monkeypatch):
    monkeypatch.setenv(config_mod.ENV_POSTURE, "danger-full-access")
    monkeypatch.setenv("ICDEV_CODEX_SANDBOX", "read-only")
    config_mod.reset_cache()
    assert _sandbox_argv({}) == "read-only"


def test_session_metadata_beats_everything(monkeypatch):
    monkeypatch.setenv(config_mod.ENV_POSTURE, "danger-full-access")
    monkeypatch.setenv("ICDEV_CODEX_SANDBOX", "read-only")
    config_mod.reset_cache()
    assert _sandbox_argv({"sandbox": "workspace-write"}) == "workspace-write"


@pytest.mark.parametrize("where", ["env", "metadata"])
def test_an_empty_string_still_omits_the_flag(monkeypatch, where):
    """"" is "omit --sandbox for an older CLI" — an answer, not an absent value."""
    metadata: dict = {}
    if where == "env":
        monkeypatch.setenv("ICDEV_CODEX_SANDBOX", "")
    else:
        metadata["sandbox"] = ""
    config_mod.reset_cache()
    assert _sandbox_argv(metadata) == ""


# ---------------------------------------------------------------------------
# Reporting and packaging
# ---------------------------------------------------------------------------
def test_describe_reports_the_posture_and_how_it_was_chosen(monkeypatch):
    monkeypatch.setenv(config_mod.ENV_POSTURE, "danger-full-access")
    config_mod.reset_cache()
    report = config_mod.load_config().describe()
    posture = report["resolved"]["posture"]
    assert posture["name"] == "danger-full-access"
    assert posture["source"] == config_mod.POSTURE_SOURCE_ENV
    assert posture["values"]["allow_mutation"] is True
    assert report["env_overrides"][config_mod.ENV_POSTURE] == "danger-full-access"


def test_the_cli_reports_the_posture():
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
    assert report["postures_path"]
    assert report["resolved"]["posture"]["name"] == "workspace-write"


def test_the_packaged_posture_mirror_has_not_drifted():
    """The loader finds ``icdev/data/args/`` first from the packaged copy.

    A drifted mirror would therefore decide the posture for anything importing
    ``icdev.tools.agent_runtime.config`` — the failure mode
    ``agent_runtime.yaml`` is already gated against.

    Asserted, never skipped: this file is CI-gated, and a mirror check that
    skips itself when the mirror is missing reports the absence as a pass, which
    is the one outcome it exists to catch.
    """
    source = REPO_ROOT / "args" / "permission_postures.yaml"
    mirror = REPO_ROOT / "icdev" / "data" / "args" / "permission_postures.yaml"
    assert mirror.is_file(), f"packaged posture mirror is missing: {mirror}"
    assert source.read_bytes() == mirror.read_bytes()
