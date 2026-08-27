# CUI // SP-CTI
"""An operator can PREFER the CLI bridge, not only fall back to it (cch-obs-06).

THE GAP. `activate.should_enable()` is `is_airgap() or not _has_cloud_key()`, so the bridge
was a FALLBACK for a keyless or air-gapped host and there was no way to prefer it. On a
machine holding any of the nine CLOUD_KEY_ENV_VARS it is off, and the only ways round that
were unsetting the key -- which kills that provider's routing -- or a context-scoped
per-request override. Neither survives a restart, so ICDEV's own Claude traffic could not be
routed through LLMRouter and therefore could not be MEASURED.

Measured on this deployment 2026-08-27: `OLLAMA_API_KEY` is set, `_has_cloud_key()` is True,
`should_enable()` is False, all of the last 90 router calls went to `ollama_cloud`,
`anthropic` has ZERO rows in 13,166 lifetime ai_telemetry rows, and `claude-cli` last appeared
2026-07-09.

`auto` is the default and must stay byte-identical to the previous behaviour -- a config key
that changes routing merely by existing would be worse than the gap it closes.
"""
from __future__ import annotations

import pathlib

import pytest
import yaml

from tools.llm.cli_bridge import activate

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]


@pytest.fixture
def cloud_key(monkeypatch):
    """The situation that makes the gap visible: a cloud key is present."""
    monkeypatch.setenv("OLLAMA_API_KEY", "stub")
    monkeypatch.setattr(activate, "_cli_bridge_override", activate.ContextVar("o", default=None))
    return True


# ---------------------------------------------------------------------------
# the default must not move
# ---------------------------------------------------------------------------


def test_the_shipped_default_is_auto():
    cfg = yaml.safe_load((REPO_ROOT / "args" / "llm_config.yaml").read_text(encoding="utf-8"))
    assert (cfg.get("cli_bridge") or {}).get("mode") == "auto", (
        "shipping `prefer` would silently redirect every deployment's LLM traffic to the "
        "Claude CLI on upgrade"
    )


def test_auto_defers_entirely_to_the_existing_detection(monkeypatch, cloud_key):
    """`auto` must equal should_enable(), in both directions."""
    monkeypatch.setattr(activate, "configured_mode", lambda: activate.MODE_AUTO)
    for detected in (True, False):
        monkeypatch.setattr(activate, "should_enable", lambda d=detected: d)
        assert activate.cli_bridge_enabled() is detected


def test_an_absent_config_key_reads_auto(monkeypatch, tmp_path):
    """A config without the key is the pre-change file; it must not change routing."""
    cfg = tmp_path / "llm_config.yaml"
    cfg.write_text("models: {}\n", encoding="utf-8")
    monkeypatch.setattr(
        "tools.llm.config_path.resolve_llm_config_path", lambda: cfg, raising=False
    )
    assert activate.configured_mode() == activate.MODE_AUTO


def test_an_unreadable_config_reads_auto(monkeypatch, tmp_path):
    """Routing must never break because a config read failed."""
    monkeypatch.setattr(
        "tools.llm.config_path.resolve_llm_config_path",
        lambda: tmp_path / "does_not_exist.yaml",
        raising=False,
    )
    assert activate.configured_mode() == activate.MODE_AUTO


def test_a_nonsense_mode_reads_auto(monkeypatch, tmp_path):
    """An unknown value must not be treated as `prefer`."""
    cfg = tmp_path / "llm_config.yaml"
    cfg.write_text("cli_bridge:\n  mode: aggressive\n", encoding="utf-8")
    monkeypatch.setattr(
        "tools.llm.config_path.resolve_llm_config_path", lambda: cfg, raising=False
    )
    assert activate.configured_mode() == activate.MODE_AUTO


# ---------------------------------------------------------------------------
# the modes
# ---------------------------------------------------------------------------


def test_prefer_wins_over_a_present_cloud_key(monkeypatch, cloud_key):
    """The whole point: a cloud key no longer forces the bridge off."""
    assert activate.should_enable() is False, "the auto-detection still says no"
    monkeypatch.setattr(activate, "configured_mode", lambda: activate.MODE_PREFER)
    assert activate.cli_bridge_enabled() is True


def test_never_wins_over_the_air_gap_detection(monkeypatch):
    """`never` has to beat the detection too, or it is not an off switch."""
    monkeypatch.setattr(activate, "should_enable", lambda: True)
    monkeypatch.setattr(activate, "configured_mode", lambda: activate.MODE_NEVER)
    assert activate.cli_bridge_enabled() is False


@pytest.mark.parametrize("mode", ["prefer", "never"])
def test_the_context_override_still_outranks_the_declared_mode(monkeypatch, mode):
    """Per-request beats per-deployment; a caller that pins a request must keep winning."""
    monkeypatch.setattr(activate, "configured_mode", lambda: mode)
    for pinned in (True, False):
        token = activate._cli_bridge_override.set(pinned)
        try:
            assert activate.cli_bridge_enabled() is pinned
        finally:
            activate._cli_bridge_override.reset(token)


# ---------------------------------------------------------------------------
# what it does NOT reinstate
# ---------------------------------------------------------------------------


def test_the_legacy_env_toggle_is_still_ignored(monkeypatch, cloud_key):
    """`ICDEV_CLI_BRIDGE` was removed because an env var deciding which VENDOR receives the
    prompt is invisible in review. A config key is reviewable; the env var stays dead."""
    monkeypatch.setenv("ICDEV_CLI_BRIDGE", "1")
    monkeypatch.setattr(activate, "configured_mode", lambda: activate.MODE_AUTO)
    assert activate.cli_bridge_enabled() is False

    source = pathlib.Path(activate.__file__).read_text(encoding="utf-8")
    assert 'environ.get("ICDEV_CLI_BRIDGE"' not in source
    assert "environ['ICDEV_CLI_BRIDGE'" not in source


def test_the_modes_are_a_closed_set():
    assert set(activate._MODES) == {"auto", "prefer", "never"}
