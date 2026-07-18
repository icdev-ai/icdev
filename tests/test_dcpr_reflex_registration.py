# CUI // SP-CTI
"""dcpr-fix-06: verify the data-canvas freshness reflex is registered.

The freshness_guardian reflex module exists under tools/genesis/reflexes/ but
was previously registered in NEITHER daemon.REFLEX_NAMES NOR
reflex_registry.REGISTRY. Per tools/genesis/CONTEXT.md, an unregistered reflex
is never dispatched by the Genesis daemon. These tests guard that registration.
"""
from __future__ import annotations

import importlib

REFLEX = "freshness_guardian"


def test_freshness_guardian_in_daemon_reflex_names():
    daemon = importlib.import_module("tools.genesis.daemon")
    assert REFLEX in daemon.REFLEX_NAMES


def test_freshness_guardian_in_registry():
    registry = importlib.import_module("tools.genesis.reflex_registry")
    entry = registry.get(REFLEX)
    assert entry.name == REFLEX
    # Cadence mirrors CADENCE_HOURS=1 in the reflex module.
    assert entry.interval_h == 1.0
    assert REFLEX in {e.name for e in registry.list_reflexes()}


def test_freshness_guardian_module_resolves_with_run():
    # dcpr-fix-07: the `from __future__` import is now first after the docstring,
    # so the module imports cleanly (previously a SyntaxError blocked dispatch).
    module = importlib.import_module(f"tools.genesis.reflexes.{REFLEX}")
    assert module.IMPLEMENTATION_STATUS == "full"
    assert hasattr(module, "run"), "daemon dispatch requires a module-level run()"
