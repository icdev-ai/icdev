# CUI // SP-CTI
"""Tests for the truthful LLM health check + registry db_migration path (obx-hyg-03).

Covers:
  (c) tools/observability/health_blueprint.py::_check_llm
      - default mode: reports under the truthful "llm_import" key and attempts
        NO network (socket calls are trapped to fail if used).
      - ICDEV_HEALTH_LLM_PING=true: a provider-reachability probe is attempted,
        and a failing/unreachable probe is reported as "degraded", never raised.
  (a) args/component_registry.yaml: the odc entry's db_migration path exists on
      disk (matches the boundary_canvas init_db.py convention adopted here).

Shim-aware: modules are resolved via importlib and attributes are patched with
setattr so tools.* and icdev.tools.* stay distinct.
"""
from __future__ import annotations

import importlib
import socket
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]

_HB = "tools.observability.health_blueprint"
_ROUTER = "tools.llm.router"


def _load_hb():
    return importlib.import_module(_HB)


# ---------------------------------------------------------------------------
# (c) default mode — truthful key, no network
# ---------------------------------------------------------------------------

def test_default_reports_llm_import_key_no_network(monkeypatch):
    """Default mode: checks payload uses the truthful 'llm_import' key and the
    check performs no network I/O."""
    hb = _load_hb()
    # Ensure the lazy `from tools.llm.router import LLMRouter` import is already
    # resolved (module import is not network I/O) so the guard below only traps
    # network attempts made by the check logic itself.
    importlib.import_module(_ROUTER)

    monkeypatch.delenv("ICDEV_HEALTH_LLM_PING", raising=False)

    def _boom(*_a, **_k):  # pragma: no cover - only fires on regression
        raise AssertionError("network attempted in default LLM health check")

    monkeypatch.setattr(socket.socket, "connect", _boom, raising=False)
    monkeypatch.setattr(socket, "create_connection", _boom, raising=False)

    assert hb._check_llm() == "ok"

    checks = hb._run_checks()
    assert "llm_import" in checks
    assert "llm" not in checks  # old, untruthful key is gone


def test_ping_disabled_variants_stay_ok(monkeypatch):
    """Only explicit truthy opt-in values enable the deep probe."""
    hb = _load_hb()
    router = importlib.import_module(_ROUTER)

    def _should_not_run(self):  # pragma: no cover - only on regression
        raise AssertionError("deep probe ran while ICDEV_HEALTH_LLM_PING disabled")

    monkeypatch.setattr(router.LLMRouter, "has_any_llm", _should_not_run, raising=False)

    for val in ("", "0", "false", "no", "off", "maybe"):
        monkeypatch.setenv("ICDEV_HEALTH_LLM_PING", val)
        assert hb._check_llm() == "ok"
        assert hb._llm_ping_enabled() is False


# ---------------------------------------------------------------------------
# (c) deep probe enabled — degrade, never raise
# ---------------------------------------------------------------------------

def test_ping_enabled_probe_failure_degrades(monkeypatch):
    """When the probe raises, the check degrades gracefully (no exception)."""
    hb = _load_hb()
    router = importlib.import_module(_ROUTER)
    monkeypatch.setenv("ICDEV_HEALTH_LLM_PING", "true")

    def _raise(self):
        raise RuntimeError("provider unreachable")

    monkeypatch.setattr(router.LLMRouter, "has_any_llm", _raise, raising=False)
    # Belt-and-suspenders: keep __init__ cheap so instantiation cannot touch net.
    monkeypatch.setattr(router.LLMRouter, "__init__", lambda self: None, raising=False)

    assert hb._check_llm() == "degraded"


def test_ping_enabled_unreachable_degrades(monkeypatch):
    """A reachable-but-negative probe (no provider up) reports degraded."""
    hb = _load_hb()
    router = importlib.import_module(_ROUTER)
    monkeypatch.setenv("ICDEV_HEALTH_LLM_PING", "true")

    monkeypatch.setattr(router.LLMRouter, "__init__", lambda self: None, raising=False)
    monkeypatch.setattr(router.LLMRouter, "has_any_llm", lambda self: False, raising=False)
    assert hb._check_llm() == "degraded"

    monkeypatch.setattr(router.LLMRouter, "has_any_llm", lambda self: True, raising=False)
    assert hb._check_llm() == "ok"


def test_ping_enabled_probe_does_not_invoke_completion(monkeypatch):
    """The deep probe must use has_any_llm reachability, never a completion."""
    hb = _load_hb()
    router = importlib.import_module(_ROUTER)
    monkeypatch.setenv("ICDEV_HEALTH_LLM_PING", "true")

    called = {"has_any_llm": False}

    def _has_any_llm(self):
        called["has_any_llm"] = True
        return True

    def _no_invoke(self, *a, **k):  # pragma: no cover - only on regression
        raise AssertionError("health probe invoked an LLM completion")

    monkeypatch.setattr(router.LLMRouter, "__init__", lambda self: None, raising=False)
    monkeypatch.setattr(router.LLMRouter, "has_any_llm", _has_any_llm, raising=False)
    if hasattr(router.LLMRouter, "invoke"):
        monkeypatch.setattr(router.LLMRouter, "invoke", _no_invoke, raising=False)

    assert hb._check_llm() == "ok"
    assert called["has_any_llm"] is True


# ---------------------------------------------------------------------------
# (a) registry db_migration path truthfulness
# ---------------------------------------------------------------------------

def _odc_entry(registry_path: Path) -> dict:
    data = yaml.safe_load(registry_path.read_text(encoding="utf-8"))
    components = data.get("components", data) if isinstance(data, dict) else data
    if isinstance(components, dict):
        components = components.get("components", [])
    for comp in components:
        if isinstance(comp, dict) and comp.get("key") == "odc":
            return comp
    raise AssertionError("odc entry not found in registry")


@pytest.mark.parametrize(
    "rel",
    ["args/component_registry.yaml", "icdev/data/args/component_registry.yaml"],
)
def test_odc_db_migration_path_exists(rel):
    """The odc db_migration declaration must point at a path that exists."""
    registry_path = REPO_ROOT / rel
    if not registry_path.exists():  # pragma: no cover - twin may be absent
        pytest.skip(f"{rel} not present")
    entry = _odc_entry(registry_path)
    migration = entry.get("completeness", {}).get("db_migration")
    assert migration, "odc entry is missing completeness.db_migration"
    assert (REPO_ROOT / migration).exists(), (
        f"odc db_migration path does not exist on disk: {migration}"
    )
