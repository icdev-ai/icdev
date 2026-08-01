# CUI // SP-CTI
"""Tests for the component reachability coherence check (pkg-reg-02).

pkg-reg-01 closed a 21-flag enable/disable gap; this check is the durable guard
that stops the 22nd: every component in args/component_registry.yaml must be
reachable from all three surfaces a fresh install offers — `icdev enable`/
`disable`, the `icdev setup` TUI, and the generated .env. A failure must NAME
the specific component keys + env flags, not report a bare count.

Run: pytest tests/test_pkg_reg02_reachability.py -v --tb=short
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tools.workflow.coherence_checker import (
    CHECK_REGISTRY,
    _FIX_REGISTRY,
    check_component_cli_reachability,
)


def test_registered_for_all_and_gate():
    assert CHECK_REGISTRY.get("component_cli_reachability") is (
        check_component_cli_reachability
    )
    # A surface gap needs wiring, so no mechanical auto-fix.
    assert _FIX_REGISTRY.get("component_cli_reachability") == "skip"


def test_passes_on_current_registry():
    """All shipped components are reachable from all three surfaces today."""
    result = check_component_cli_reachability()
    assert result.status == "pass", result.message
    assert "reachable" in result.message


def test_checks_all_three_surfaces():
    result = check_component_cli_reachability()
    joined = " ".join(result.expected).lower()
    assert "enable/disable" in joined
    assert "setup tui" in joined
    assert ".env" in joined


def test_failure_names_components_not_bare_count(monkeypatch):
    """If a surface drops components, the finding must name keys + env flags."""
    import tools.cli.env_generator as eg

    # Simulate the generated .env omitting every component flag.
    monkeypatch.setattr(eg, "render_component_section", lambda registry: "")

    result = check_component_cli_reachability()
    assert result.status == "fail"
    # Every missing entry names a component key and its ICDEV_ env flag.
    assert result.missing, "failure must list the offending components"
    assert all(".env:" in m for m in result.missing)
    assert any("ICDEV_" in m for m in result.missing)
    # The message points the reader to the named list, not just a number.
    assert "missing[]" in result.message


def test_failure_when_enable_surface_empty(monkeypatch):
    """Dropping the CLI toggles surfaces every component as enable-unreachable."""
    import importlib

    # Shim-aware: patch the exact module object the check imports from.
    cr = importlib.import_module("tools.config.component_registry")
    real = cr.get_registry()

    class _Reg:
        def __getattr__(self, name):
            return getattr(real, name)

        def get_cli_toggles(self):
            return {}

    monkeypatch.setattr(cr, "get_registry", lambda: _Reg())

    result = check_component_cli_reachability()
    assert result.status == "fail"
    assert any(m.startswith("enable/disable:") for m in result.missing)
