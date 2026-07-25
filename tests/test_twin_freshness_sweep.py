# CUI // SP-CTI — twin_freshness_sweep reflex tests (twx-cov-02)
"""Tests for the cross-canvas twin-freshness sweep reflex and its registration.

Genesis gotcha guard: a reflex module that isn't in REFLEX_NAMES is never
dispatched, and one missing from genesis_config.yaml is disabled by default.
These tests assert all three registration points are wired.
"""
from __future__ import annotations

from pathlib import Path

import yaml

from tools.genesis.reflexes import twin_freshness_sweep as sweep

_NAME = "twin_freshness_sweep"


def test_reflex_run_dry_run_shape():
    out = sweep.run({"dry_run": True})
    assert out["status"] == "ok"
    assert "twins_checked" in out
    assert isinstance(out["stale_twins"], list)
    assert out["events_published"] == 0  # dry-run never publishes


def test_registered_in_reflex_names():
    from tools.genesis.daemon import REFLEX_NAMES

    assert _NAME in REFLEX_NAMES, "reflex missing from daemon REFLEX_NAMES — will never dispatch"


def test_registered_in_reflex_registry():
    from tools.genesis import reflex_registry

    entry = reflex_registry.get(_NAME)
    assert entry is not None
    assert entry.name == _NAME


def test_registered_in_genesis_config():
    cfg_path = Path(__file__).resolve().parents[1] / "args" / "genesis_config.yaml"
    cfg = yaml.safe_load(cfg_path.read_text(encoding="utf-8"))
    reflexes = cfg.get("reflexes", {})
    assert _NAME in reflexes, "reflex missing from genesis_config.yaml — disabled by default"
    entry = reflexes[_NAME]
    assert entry.get("enabled") is True
    assert entry.get("risk_tier") == "green"


def test_publishes_on_stale(monkeypatch):
    # Stub observer to report one stale twin; capture the published event.
    import tools.genesis.reflexes.twin_freshness_sweep as mod

    fake_report = {
        "twin_count": 2,
        "twins": [
            {"canvas": "aadc", "snapshot_count": 0, "latest_snapshot_at": None,
             "latest_snapshot_age_seconds": None},
            {"canvas": "pdc", "snapshot_count": 5, "latest_snapshot_at": "2026-07-25T00:00:00"},
        ],
        "summary": {"stale_twins": ["aadc"]},
    }
    import importlib

    observer_mod = importlib.import_module("tools.twin_core.observer")
    monkeypatch.setattr(observer_mod, "observe", lambda **k: fake_report)

    published = []
    bus = importlib.import_module("tools.canvas.event_bus")
    monkeypatch.setattr(bus, "publish", lambda *a, **k: published.append((a, k)) or "evt")

    out = mod.run({})
    assert out["stale_twins"] == ["aadc"]
    assert out["events_published"] == 1
    (args, kwargs) = published[0]
    assert args[0] == "twin_core"
    assert args[1] == "twin.snapshot.stale"
    assert kwargs.get("target_canvas") == "aadc"
