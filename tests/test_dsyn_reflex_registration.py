# CUI // SP-CTI
"""Tests for dsyn-reflex-02: dic_integration registration in daemon + config."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest
import yaml

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

_GENESIS_CONFIG = Path(__file__).resolve().parents[1] / "args" / "genesis_config.yaml"
_REFLEX_NAME = "dic_integration"


# ── daemon.py REFLEX_NAMES ────────────────────────────────────────────────────

def test_dic_integration_in_tools_reflex_names():
    from tools.genesis.daemon import REFLEX_NAMES
    assert _REFLEX_NAME in REFLEX_NAMES, (
        f"'{_REFLEX_NAME}' missing from tools/genesis/daemon.py REFLEX_NAMES"
    )


def test_dic_integration_in_icdev_reflex_names():
    from icdev.tools.genesis.daemon import REFLEX_NAMES
    assert _REFLEX_NAME in REFLEX_NAMES, (
        f"'{_REFLEX_NAME}' missing from icdev/tools/genesis/daemon.py REFLEX_NAMES"
    )


# ── genesis_config.yaml ───────────────────────────────────────────────────────

@pytest.fixture(scope="module")
def genesis_config():
    with open(_GENESIS_CONFIG, encoding="utf-8") as fh:
        return yaml.safe_load(fh)


def test_config_yaml_parses(genesis_config):
    assert isinstance(genesis_config, dict)


def test_dic_integration_section_exists(genesis_config):
    reflexes = genesis_config.get("reflexes", {})
    assert _REFLEX_NAME in reflexes, (
        f"'{_REFLEX_NAME}' section missing from args/genesis_config.yaml reflexes:"
    )


def test_dic_integration_enabled(genesis_config):
    cfg = genesis_config["reflexes"][_REFLEX_NAME]
    assert cfg.get("enabled") is True


def test_dic_integration_interval_is_15_minutes(genesis_config):
    cfg = genesis_config["reflexes"][_REFLEX_NAME]
    assert cfg.get("interval_seconds") == 900, (
        f"Expected interval_seconds=900 (15 min), got {cfg.get('interval_seconds')}"
    )


def test_dic_integration_timeout_covers_budget(genesis_config):
    """timeout_seconds must be > 60 (the reflex's own 60s budget)."""
    cfg = genesis_config["reflexes"][_REFLEX_NAME]
    assert cfg.get("timeout_seconds", 0) > 60, (
        "timeout_seconds must exceed the 60s reflex budget"
    )


def test_dic_integration_risk_tier_is_green(genesis_config):
    cfg = genesis_config["reflexes"][_REFLEX_NAME]
    assert cfg.get("risk_tier") == "green"


def test_dic_integration_has_description(genesis_config):
    cfg = genesis_config["reflexes"][_REFLEX_NAME]
    desc = (cfg.get("description") or "").strip()
    assert len(desc) >= 20


def test_dic_integration_success_metric_exists(genesis_config):
    cfg = genesis_config["reflexes"][_REFLEX_NAME]
    assert "success_metric" in cfg
    sm = cfg["success_metric"]
    assert "name" in sm
    assert "threshold" in sm
    assert "operator" in sm


# ── Reflex module importable and has run() ────────────────────────────────────

def test_reflex_module_importable():
    import tools.genesis.reflexes.dic_integration as mod  # noqa: F401


def test_reflex_has_run_function():
    from tools.genesis.reflexes.dic_integration import run
    assert callable(run)


def test_reflex_has_cadence_minutes():
    from tools.genesis.reflexes.dic_integration import CADENCE_MINUTES
    assert CADENCE_MINUTES == 15


def test_reflex_has_implementation_status_full():
    from tools.genesis.reflexes.dic_integration import IMPLEMENTATION_STATUS
    assert IMPLEMENTATION_STATUS == "full"
