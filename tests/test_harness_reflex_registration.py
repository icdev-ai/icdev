# CUI // SP-CTI
"""Tests for hcx-rt-01: harness reflex registration in daemon + registry + config.

Verifies the Continuous Harness reflex (tools/genesis/reflexes/harness.py) is
actually dispatchable by the Genesis daemon:
  1. Listed in tools/genesis/daemon.py REFLEX_NAMES
  2. Listed in icdev/tools/genesis/daemon.py REFLEX_NAMES (mirror)
  3. Registered in reflex_registry.REGISTRY (both trees) at 6h CORE tier
  4. Has a `reflexes.harness` section in args/genesis_config.yaml with a schedule
     (without a schedule the daemon's run_due_reflexes() silently skips it)
  5. Module imports and exposes a callable run()
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest
import yaml

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

_GENESIS_CONFIG = Path(__file__).resolve().parents[1] / "args" / "genesis_config.yaml"
_REFLEX_NAME = "harness"


# ── daemon.py REFLEX_NAMES (both trees) ───────────────────────────────────────

def test_harness_in_tools_reflex_names():
    from tools.genesis.daemon import REFLEX_NAMES
    assert _REFLEX_NAME in REFLEX_NAMES, (
        f"'{_REFLEX_NAME}' missing from tools/genesis/daemon.py REFLEX_NAMES"
    )


def test_harness_in_icdev_reflex_names():
    from icdev.tools.genesis.daemon import REFLEX_NAMES
    assert _REFLEX_NAME in REFLEX_NAMES, (
        f"'{_REFLEX_NAME}' missing from icdev/tools/genesis/daemon.py REFLEX_NAMES"
    )


# ── reflex_registry.REGISTRY (both trees) ─────────────────────────────────────

def test_harness_in_tools_registry():
    from tools.genesis.reflex_registry import REGISTRY, get
    names = [e.name for e in REGISTRY]
    assert _REFLEX_NAME in names, "'harness' missing from tools reflex_registry.REGISTRY"
    entry = get(_REFLEX_NAME)
    assert entry.tier == "CORE"
    assert entry.interval_h == 6.0


def test_harness_in_icdev_registry():
    from icdev.tools.genesis.reflex_registry import REGISTRY, get
    names = [e.name for e in REGISTRY]
    assert _REFLEX_NAME in names, "'harness' missing from icdev reflex_registry.REGISTRY"
    entry = get(_REFLEX_NAME)
    assert entry.tier == "CORE"
    assert entry.interval_h == 6.0


# ── genesis_config.yaml reflexes.harness section ──────────────────────────────

@pytest.fixture(scope="module")
def genesis_config():
    with open(_GENESIS_CONFIG, encoding="utf-8") as fh:
        return yaml.safe_load(fh)


def test_config_yaml_parses(genesis_config):
    assert isinstance(genesis_config, dict)


def test_harness_reflex_section_exists(genesis_config):
    reflexes = genesis_config.get("reflexes", {})
    assert _REFLEX_NAME in reflexes, (
        "'harness' section missing from args/genesis_config.yaml reflexes: — "
        "without it run_due_reflexes() has no schedule and silently skips the reflex"
    )


def test_harness_enabled(genesis_config):
    cfg = genesis_config["reflexes"][_REFLEX_NAME]
    assert cfg.get("enabled") is True


def test_harness_has_schedule(genesis_config):
    """A schedule string is what populates daemon.schedules and makes the reflex due."""
    cfg = genesis_config["reflexes"][_REFLEX_NAME]
    assert cfg.get("schedule") == "every 6h"


def test_harness_interval_is_6h(genesis_config):
    cfg = genesis_config["reflexes"][_REFLEX_NAME]
    assert cfg.get("interval_seconds") == 21600


def test_harness_risk_tier_is_green(genesis_config):
    cfg = genesis_config["reflexes"][_REFLEX_NAME]
    assert cfg.get("risk_tier") == "green"


def test_harness_has_description(genesis_config):
    cfg = genesis_config["reflexes"][_REFLEX_NAME]
    desc = (cfg.get("description") or "").strip()
    assert len(desc) >= 20


def test_harness_success_metric_exists(genesis_config):
    cfg = genesis_config["reflexes"][_REFLEX_NAME]
    assert "success_metric" in cfg
    sm = cfg["success_metric"]
    assert "name" in sm
    assert "threshold" in sm
    assert "operator" in sm


# ── Reflex module importable and has run() ────────────────────────────────────

def test_reflex_module_importable():
    import tools.genesis.reflexes.harness as mod  # noqa: F401


def test_reflex_has_run_function():
    from tools.genesis.reflexes.harness import run
    assert callable(run)
