"""pytest tests for Product Intelligence engine registry and universal orchestrator.

7 tests covering:
  1. EngineRegistry loads config and returns 8 engines
  2. registry.invoke() with mocked subprocess returns EngineResult(status='ok')
  3. registry.invoke() never raises on subprocess failure (returns status='failed')
  4. orchestrator.run_all() inserts one row to product_intel_runs
  5. orchestrator returns consolidated JSON with all required keys
  6. dry_run=True does NOT insert to product_intel_runs
  7. disabled engine in config is skipped (subprocess never called)
"""
from __future__ import annotations

import json
import sqlite3
from unittest.mock import MagicMock, patch


from tools.product_intel.engine_registry import EngineConfig, EngineRegistry, EngineResult
from tools.product_intel.orchestrator import ProductIntelOrchestrator


# ---------------------------------------------------------------------------
# 1. EngineRegistry loads config and returns 8 engines
# ---------------------------------------------------------------------------
def test_registry_loads_8_engines():
    registry = EngineRegistry()
    engines = registry.list_engines()
    assert len(engines) == 8, f"Expected 8 engines, got {len(engines)}"


# ---------------------------------------------------------------------------
# 2. registry.invoke() with mocked subprocess returns EngineResult(status='ok')
# ---------------------------------------------------------------------------
def test_registry_invoke_ok():
    registry = EngineRegistry()
    engine = registry.list_engines()[0]
    payload = json.dumps({"signals": [{"id": "sig-1"}, {"id": "sig-2"}]})
    mock_proc = MagicMock(returncode=0, stdout=payload, stderr="")

    with patch("subprocess.run", return_value=mock_proc):
        result = registry.invoke(engine)

    assert isinstance(result, EngineResult)
    assert result.status == "ok"
    assert result.signals_count == 2
    assert result.name == engine.name


# ---------------------------------------------------------------------------
# 3. registry.invoke() never raises on subprocess failure — returns status='failed'
# ---------------------------------------------------------------------------
def test_registry_invoke_never_raises_on_failure():
    registry = EngineRegistry()
    engine = registry.list_engines()[0]

    with patch("subprocess.run", side_effect=RuntimeError("engine crashed")):
        result = registry.invoke(engine)

    assert result.status == "failed"
    assert "exception" in result.output
    assert result.signals_count == 0


# ---------------------------------------------------------------------------
# 4. orchestrator.run_all() inserts one row to product_intel_runs
# ---------------------------------------------------------------------------
def test_orchestrator_run_all_inserts_row(icdev_db):
    mock_proc = MagicMock(returncode=0, stdout='{"signals": []}', stderr="")

    with patch("subprocess.run", return_value=mock_proc):
        orch = ProductIntelOrchestrator(db_path=str(icdev_db))
        orch.run_all()

    conn = sqlite3.connect(str(icdev_db))
    rows = conn.execute("SELECT id FROM product_intel_runs").fetchall()
    conn.close()
    assert len(rows) == 1


# ---------------------------------------------------------------------------
# 5. orchestrator returns consolidated JSON with all required keys
# ---------------------------------------------------------------------------
def test_orchestrator_consolidated_json():
    mock_proc = MagicMock(returncode=0, stdout='{"signals": [{"id": "s1"}]}', stderr="")
    required_keys = {"run_id", "status", "engines_run", "engines_failed", "total_signals", "results"}

    with patch("subprocess.run", return_value=mock_proc):
        orch = ProductIntelOrchestrator()
        result = orch.run_all(dry_run=True)

    for key in required_keys:
        assert key in result, f"Missing required key in consolidated result: {key}"

    assert isinstance(result["results"], list)
    assert isinstance(result["engines_run"], list)
    assert isinstance(result["engines_failed"], list)
    assert isinstance(result["total_signals"], int)


# ---------------------------------------------------------------------------
# 6. dry_run=True does NOT insert to product_intel_runs
# ---------------------------------------------------------------------------
def test_orchestrator_dry_run_no_insert(icdev_db):
    mock_proc = MagicMock(returncode=0, stdout='{"signals": []}', stderr="")

    with patch("subprocess.run", return_value=mock_proc):
        orch = ProductIntelOrchestrator(db_path=str(icdev_db))
        orch.run_all(dry_run=True)

    conn = sqlite3.connect(str(icdev_db))
    rows = conn.execute("SELECT id FROM product_intel_runs").fetchall()
    conn.close()
    assert len(rows) == 0, "dry_run=True must not insert any rows"


# ---------------------------------------------------------------------------
# 7. disabled engine in config is skipped — subprocess is never called
# ---------------------------------------------------------------------------
def test_disabled_engine_skipped():
    registry = EngineRegistry()
    disabled_engine = EngineConfig(
        name="disabled-test",
        module="tools.product_intel.engines.noop",
        cli_flags=["--json"],
        enabled=False,
        timeout_seconds=30,
        order=99,
    )

    with patch("subprocess.run") as mock_sp:
        result = registry.invoke(disabled_engine)

    mock_sp.assert_not_called()
    assert result.status == "skipped"
    assert result.signals_count == 0
