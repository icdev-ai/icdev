# CUI // SP-CTI
"""bdr-vv-2: dispatch-contract smoke test for the three BDC Genesis reflexes.

The Genesis daemon scores every reflex run via ``tools/daemon/base.py::run_reflex``:

    success       = result.get("success", False)
    metric_value  = result.get("metric_value", 0.0)
    metric_passed = evaluate_metric(success_metric, metric_value) if success else False
    -> if not (success and metric_passed): state.record_failure(...)   # trips breaker

A reflex that returns only a flat ``{"status": "ok", ...}`` dict (no ``success``
key) is therefore recorded as a FAILURE on every run and, repeated, trips its
circuit breaker and stops being dispatched. ``cato_twin`` and ``bdc_isa_expiry``
did exactly that; this test locks in the dispatch contract so a regression fails
CI. It also pins ``cato_twin`` to the enabled state bdr-vv-2 turned on, and
proves the PG-transaction rollback guard added to ``_pull_framework_controls``.

Externals (Telegram, DB) are mocked — the test is hermetic and backend-agnostic.
"""
from __future__ import annotations

import importlib
from pathlib import Path

import pytest
import yaml

from tools.daemon.base import evaluate_metric

_CFG_PATH = Path(__file__).resolve().parents[1] / "args" / "genesis_config.yaml"


def _reflex_block(name: str) -> dict:
    with open(_CFG_PATH, "r", encoding="utf-8") as fh:
        cfg = yaml.safe_load(fh)
    return cfg["reflexes"][name]


def _assert_dispatch_contract(result: dict, metric_name: str) -> None:
    """A reflex return must carry the keys the daemon reads, and be a clean success."""
    assert isinstance(result, dict), "reflex must return a dict"
    assert "success" in result, "reflex return missing 'success' — daemon scores it as FAILURE"
    assert result["success"] is True, f"expected clean success, got: {result.get('errors') or result}"
    assert isinstance(result.get("metric_value"), (int, float)), "metric_value must be numeric"
    assert isinstance(result.get("details"), dict), "details must be a dict for audit logging"


# ---------------------------------------------------------------------------
# Config-enabled state (bdr-vv-2 flipped cato_twin on)
# ---------------------------------------------------------------------------
def test_cato_twin_enabled_in_config():
    assert _reflex_block("cato_twin")["enabled"] is True, (
        "cato_twin must be enabled:true (bdr-vv-2) — the daemon skips disabled reflexes"
    )


# ---------------------------------------------------------------------------
# cato_twin — dispatch contract on the no-projects (healthy no-op) path
# ---------------------------------------------------------------------------
def test_cato_twin_dispatch_contract(monkeypatch):
    mod = importlib.import_module("tools.genesis.reflexes.cato_twin")

    class _FakeConn:
        def execute(self, *a, **k):
            raise AssertionError("no query expected once _get_active_projects is stubbed")

        def close(self):
            pass

    monkeypatch.setattr(mod, "get_connection", lambda: _FakeConn())
    monkeypatch.setattr(mod, "_get_active_projects", lambda conn: [])

    result = mod.run({})
    _assert_dispatch_contract(result, "snapshots_written")
    assert result["status"] == "no_projects"
    # Daemon would score this a success (metric snapshots_written gte 0)
    metric_passed = evaluate_metric(_reflex_block("cato_twin")["success_metric"], result["metric_value"])
    assert result["success"] and metric_passed


def test_cato_twin_rollback_guards_transaction(monkeypatch):
    """A failing controls query must trigger conn.rollback() (PG txn de-poison)."""
    mod = importlib.import_module("tools.genesis.reflexes.cato_twin")
    calls = {"rollback": 0}

    class _PoisonConn:
        def execute(self, sql, params=None):
            low = sql.lower()
            if "sqlite_master" in low or "information_schema" in low:
                class _R:
                    def fetchone(self_inner):
                        return {"1": 1}  # table "exists"
                return _R()
            raise RuntimeError('column "evidence_ref" does not exist')

        def rollback(self):
            calls["rollback"] += 1

    rows = mod._pull_framework_controls(_PoisonConn(), "proj-x", "FedRAMP High")
    assert rows == [], "a failed controls query must degrade to an empty list"
    assert calls["rollback"] == 1, (
        "controls-query failure must roll back — otherwise the PG transaction stays "
        "aborted and every later query in the cycle cascade-fails"
    )


# ---------------------------------------------------------------------------
# bdc_isa_expiry — dispatch contract with check_isa_expiry + Telegram mocked
# ---------------------------------------------------------------------------
def test_bdc_isa_expiry_dispatch_contract(monkeypatch):
    mod = importlib.import_module("tools.genesis.reflexes.bdc_isa_expiry")
    isa_mod = importlib.import_module("tools.boundary_canvas.isa_expiry")

    monkeypatch.setattr(
        isa_mod,
        "check_isa_expiry",
        lambda *, dry_run=False: {
            "isas_checked": 3,
            "events_published": 2,
            "notifications_sent": 0,  # Telegram not reached in this fixture
            "dry_run": dry_run,
        },
    )

    result = mod.run({})
    _assert_dispatch_contract(result, "isas_checked")
    assert result["metric_value"] == 3.0
    assert result["details"]["events_published"] == 2


def test_bdc_isa_expiry_error_is_not_success(monkeypatch):
    """When the delegate raises, the reflex must report success=False (not swallow silently)."""
    mod = importlib.import_module("tools.genesis.reflexes.bdc_isa_expiry")
    isa_mod = importlib.import_module("tools.boundary_canvas.isa_expiry")

    def _boom(*, dry_run=False):
        raise RuntimeError("bd_isa_tracker unavailable")

    monkeypatch.setattr(isa_mod, "check_isa_expiry", _boom)
    result = mod.run({})
    assert result["success"] is False
    assert result["status"] == "error"
    assert result["errors"], "the delegate error must be surfaced, not swallowed"


# ---------------------------------------------------------------------------
# cato_monitor — already returns the contract; lock it in
# ---------------------------------------------------------------------------
def test_cato_monitor_dispatch_contract(monkeypatch):
    mod = importlib.import_module("tools.genesis.reflexes.cato_monitor")
    # With no IQE files the reflex opens a connection but runs no query, so it is
    # backend-agnostic under the SQLite conftest — no DB mock needed.
    monkeypatch.setattr(mod, "_collect_iqe_files", lambda: [])

    result = mod.run(_reflex_block("cato_monitor"), trust=None)
    _assert_dispatch_contract(result, "violations")
    assert result["scanned"] == 0


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v"]))
