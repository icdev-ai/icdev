# CUI // SP-CTI
"""Tests for the Genesis ``foundry_cycle`` reflex (acf-reflex-01).

The reflex drives the Autonomous Capability Foundry on a schedule. Two core
behaviours per the task:

  * **flag off**  -> clean no-op (``status='skipped'``, ``success=True``); never
    imports / calls the engine, never trips the circuit breaker.
  * **flag on + seeded signals** -> delegates to ``tools.foundry.engine.run_cycle``
    and maps its roll-up onto the reflex-spec keys (``harvested`` /
    ``concepts_proposed`` / ``tasks_emitted``).

The engine is shipped by a sibling task, so we inject a fake
``tools.foundry.engine`` module (or monkeypatch a real one) for the flag-on path.
"""
import sys
import types

import pytest

from tools.genesis.reflexes import foundry_cycle


def _install_fake_engine(monkeypatch, run_cycle):
    """Register a fake ``tools.foundry.engine`` exposing ``run_cycle``."""
    mod = types.ModuleType("tools.foundry.engine")
    mod.run_cycle = run_cycle
    monkeypatch.setitem(sys.modules, "tools.foundry.engine", mod)


# --------------------------------------------------------------------------- #
# Contract
# --------------------------------------------------------------------------- #
def test_reflex_has_cadence_and_status_contract():
    # Follows the ndc_topology_drift / integrity_monitor contract.
    assert isinstance(foundry_cycle.CADENCE_HOURS, int)
    assert foundry_cycle.CADENCE_HOURS > 0
    assert foundry_cycle.IMPLEMENTATION_STATUS == "full"


# --------------------------------------------------------------------------- #
# Flag OFF -> clean no-op
# --------------------------------------------------------------------------- #
def test_flag_off_is_clean_noop(monkeypatch):
    monkeypatch.delenv(foundry_cycle.FEATURE_FLAG, raising=False)

    # If the reflex touched the engine while the flag is off, this would explode.
    def _boom(*a, **k):  # pragma: no cover - must never be called
        raise AssertionError("engine must not be called when flag is off")

    _install_fake_engine(monkeypatch, _boom)

    r = foundry_cycle.run({})
    assert r["status"] == "skipped"
    assert r["success"] is True
    assert r["harvested"] == 0
    assert r["concepts_proposed"] == 0
    assert r["tasks_emitted"] == 0
    assert r["metric_value"] == 0.0
    assert r["details"]["enabled"] is False
    assert foundry_cycle.FEATURE_FLAG in r["details"]["reason"]


@pytest.mark.parametrize("falsy", ["", "0", "false", "no", "off"])
def test_flag_falsy_values_are_noop(monkeypatch, falsy):
    monkeypatch.setenv(foundry_cycle.FEATURE_FLAG, falsy)
    r = foundry_cycle.run({})
    assert r["status"] == "skipped"
    assert r["success"] is True


# --------------------------------------------------------------------------- #
# Flag ON -> delegates to the engine and maps the roll-up
# --------------------------------------------------------------------------- #
def test_flag_on_runs_cycle_and_maps_rollup(monkeypatch):
    monkeypatch.setenv(foundry_cycle.FEATURE_FLAG, "true")

    calls = {}

    def _fake_run_cycle(dry_run=False, max_concepts=None):
        calls["dry_run"] = dry_run
        calls["max_concepts"] = max_concepts
        return {
            "run_id": "frun-abc123",
            "harvested": 7,
            "concepts_proposed": 3,
            "concepts_approved": 1,
            "tasks_emitted": 9,
            "status": "ok",
        }

    _install_fake_engine(monkeypatch, _fake_run_cycle)

    r = foundry_cycle.run({"max_concepts": 4})
    assert r["status"] == "ok"
    assert r["success"] is True
    assert r["harvested"] == 7
    assert r["concepts_proposed"] == 3
    assert r["tasks_emitted"] == 9
    assert r["metric_value"] == 9.0
    assert r["details"]["enabled"] is True
    assert r["details"]["run_id"] == "frun-abc123"
    assert r["details"]["concepts_approved"] == 1
    # Rate limit forwarded to the engine.
    assert calls["max_concepts"] == 4
    assert calls["dry_run"] is False


def test_flag_on_dry_run_forwarded(monkeypatch):
    monkeypatch.setenv(foundry_cycle.FEATURE_FLAG, "1")

    seen = {}

    def _fake_run_cycle(**kwargs):
        seen.update(kwargs)
        return {"harvested": 0, "concepts_proposed": 0, "tasks_emitted": 0, "status": "ok"}

    _install_fake_engine(monkeypatch, _fake_run_cycle)

    r = foundry_cycle.run({"dry_run": True})
    assert r["status"] == "ok"
    assert seen["dry_run"] is True
    assert r["details"]["dry_run"] is True


def test_engine_error_status_is_surfaced(monkeypatch):
    monkeypatch.setenv(foundry_cycle.FEATURE_FLAG, "on")

    def _fake_run_cycle(**kwargs):
        return {"harvested": 2, "concepts_proposed": 0, "tasks_emitted": 0, "status": "error"}

    _install_fake_engine(monkeypatch, _fake_run_cycle)

    r = foundry_cycle.run({})
    assert r["status"] == "error"
    assert r["success"] is False


def test_engine_exception_does_not_crash(monkeypatch):
    monkeypatch.setenv(foundry_cycle.FEATURE_FLAG, "yes")

    def _fake_run_cycle(**kwargs):
        raise RuntimeError("synthesizer blew up")

    _install_fake_engine(monkeypatch, _fake_run_cycle)

    r = foundry_cycle.run({})
    assert r["status"] == "error"
    assert r["success"] is False
    assert any("synthesizer blew up" in e for e in r["details"]["errors"])


def test_flag_on_engine_absent_skips_gracefully(monkeypatch):
    monkeypatch.setenv(foundry_cycle.FEATURE_FLAG, "true")
    # Ensure the engine import fails (no fake registered, real module absent).
    monkeypatch.setitem(sys.modules, "tools.foundry.engine", None)

    r = foundry_cycle.run({})
    assert r["status"] == "skipped"
    # success stays True so the missing engine never trips the circuit breaker.
    assert r["success"] is True
    assert r["details"]["enabled"] is True


# --------------------------------------------------------------------------- #
# Signature-aware engine invocation
# --------------------------------------------------------------------------- #
def test_call_run_cycle_filters_to_accepted_kwargs():
    # Engine that accepts neither kwarg -> called with none.
    def _no_args():
        return {"status": "ok"}

    assert foundry_cycle._call_run_cycle(_no_args, dry_run=True, max_concepts=3) == {"status": "ok"}

    # Engine that accepts only dry_run.
    captured = {}

    def _only_dry(dry_run=False):
        captured["dry_run"] = dry_run
        captured["had_max"] = False
        return {"status": "ok"}

    foundry_cycle._call_run_cycle(_only_dry, dry_run=True, max_concepts=3)
    assert captured == {"dry_run": True, "had_max": False}

    # Engine with **kwargs gets the canonical pair.
    seen = {}

    def _var_kw(**kw):
        seen.update(kw)
        return {"status": "ok"}

    foundry_cycle._call_run_cycle(_var_kw, dry_run=False, max_concepts=5)
    assert seen == {"dry_run": False, "max_concepts": 5}
