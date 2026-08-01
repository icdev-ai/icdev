# CUI // SP-CTI
"""Tests for pdx-ops-01: operationalize PDC pipeline-staleness reflex + enablement.

Covers:
  - pdc_pipeline_stale registered in daemon REFLEX_NAMES (tools + icdev mirror)
  - genesis_config.yaml has a coherent pdc_pipeline_stale block
  - the reflex module is importable and exposes the expected contract
  - the pdc.pipeline.stale event has a consumer in tools/pipeline/bus_subscriber.py
  - core profiles government + saas-il4 enable the pdc canvas
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest
import yaml

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

_BASE = Path(__file__).resolve().parents[1]
_GENESIS_CONFIG = _BASE / "args" / "genesis_config.yaml"
_CORE_PROFILES = _BASE / "args" / "core_profiles.yaml"
_REFLEX_NAME = "pdc_pipeline_stale"


# ── daemon.py REFLEX_NAMES ────────────────────────────────────────────────────

def test_pdc_pipeline_stale_in_tools_reflex_names():
    from tools.genesis.daemon import REFLEX_NAMES
    assert _REFLEX_NAME in REFLEX_NAMES, (
        f"'{_REFLEX_NAME}' missing from tools/genesis/daemon.py REFLEX_NAMES"
    )


def test_pdc_pipeline_stale_in_icdev_reflex_names():
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


def test_pdc_pipeline_stale_section_exists(genesis_config):
    reflexes = genesis_config.get("reflexes", {})
    assert _REFLEX_NAME in reflexes, (
        f"'{_REFLEX_NAME}' section missing from args/genesis_config.yaml reflexes:"
    )


def test_pdc_pipeline_stale_enabled(genesis_config):
    cfg = genesis_config["reflexes"][_REFLEX_NAME]
    assert cfg.get("enabled") is True


def test_pdc_pipeline_stale_interval_matches_cadence(genesis_config):
    """interval_seconds must match the module's CADENCE_HOURS = 6 (21600s)."""
    cfg = genesis_config["reflexes"][_REFLEX_NAME]
    assert cfg.get("interval_seconds") == 21600, (
        f"Expected interval_seconds=21600 (6h), got {cfg.get('interval_seconds')}"
    )


def test_pdc_pipeline_stale_risk_tier_is_green(genesis_config):
    cfg = genesis_config["reflexes"][_REFLEX_NAME]
    assert cfg.get("risk_tier") == "green"


def test_pdc_pipeline_stale_has_description(genesis_config):
    cfg = genesis_config["reflexes"][_REFLEX_NAME]
    desc = (cfg.get("description") or "").strip()
    assert len(desc) >= 20


def test_pdc_pipeline_stale_success_metric_exists(genesis_config):
    cfg = genesis_config["reflexes"][_REFLEX_NAME]
    assert "success_metric" in cfg
    sm = cfg["success_metric"]
    assert "name" in sm
    assert "threshold" in sm
    assert "operator" in sm


# ── Reflex module contract ────────────────────────────────────────────────────

def test_reflex_module_importable():
    import tools.genesis.reflexes.pdc_pipeline_stale as mod  # noqa: F401


def test_reflex_has_run_function():
    from tools.genesis.reflexes.pdc_pipeline_stale import run
    assert callable(run)


def test_reflex_cadence_matches_config():
    from tools.genesis.reflexes.pdc_pipeline_stale import CADENCE_HOURS
    assert CADENCE_HOURS == 6


def test_reflex_implementation_status_full():
    from tools.genesis.reflexes.pdc_pipeline_stale import IMPLEMENTATION_STATUS
    assert IMPLEMENTATION_STATUS == "full"


# ── Event consumer wiring (pdc.pipeline.stale had no subscriber before) ───────

def test_bus_subscriber_has_stale_handler():
    from tools.pipeline import bus_subscriber
    assert hasattr(bus_subscriber, "_on_pdc_pipeline_stale")
    assert callable(bus_subscriber._on_pdc_pipeline_stale)


def test_bus_subscriber_subscribes_to_stale_event():
    src = (_BASE / "tools" / "pipeline" / "bus_subscriber.py").read_text(encoding="utf-8")
    assert 'subscribe("pdc", "pdc.pipeline.stale"' in src, (
        "bus_subscriber.register() must subscribe to pdc.pipeline.stale"
    )


# ── Core-profile enablement path ──────────────────────────────────────────────

@pytest.fixture(scope="module")
def core_profiles():
    with open(_CORE_PROFILES, encoding="utf-8") as fh:
        return yaml.safe_load(fh)


@pytest.mark.parametrize("profile_name", ["government", "saas-il4"])
def test_profile_enables_pdc(core_profiles, profile_name):
    profile = core_profiles["profiles"][profile_name]
    components = profile.get("default_enabled_components", [])
    assert "pdc" in components, (
        f"core profile '{profile_name}' must enable the pdc canvas"
    )


@pytest.mark.parametrize("profile_name", ["community", "commercial", "il6-secret"])
def test_minimal_profiles_do_not_enable_pdc(core_profiles, profile_name):
    """pdc (min_il IL4) stays out of minimal / commercial / SIPR-minimal profiles."""
    profile = core_profiles["profiles"][profile_name]
    components = profile.get("default_enabled_components", [])
    assert "pdc" not in components
