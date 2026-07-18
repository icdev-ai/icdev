# CUI // SP-CTI
"""penta-aimc-01 — AIMC scanner/checker integrity + RLS-safe reads.

Proves:
  (a) the canvas (RLS-disabled) connection path returns REAL rows when present;
  (b) an empty DB yields an explicit ``no-data`` status, NOT fabricated defaults;
  (c) the fabricated ``_DEFAULT_INVENTORY`` / ``_DEFAULT_CHECKS`` constants are gone.

Reads/writes go through the storage translate layer (get_canvas_connection), never
raw sqlite3 — so ``%s`` placeholders are exercised the same way the runtime does.
"""
import importlib
import uuid

import pytest


@pytest.fixture
def aimc_db(tmp_path, monkeypatch):
    """Point the storage layer at a fresh temp SQLite DB with AIMC tables created."""
    db = tmp_path / "icdev.db"
    monkeypatch.setenv("ICDEV_STORAGE_BACKEND", "sqlite")
    monkeypatch.setenv("ICDEV_DB_PATH", str(db))
    # AIMC_DB_URL unset -> get_canvas_connection() falls through to ICDEV_DB_PATH.
    monkeypatch.delenv("AIMC_DB_URL", raising=False)
    from tools.aimc.db.init_db import init_db
    init_db()  # idempotent; creates aimc_models + aimc_deployment in the temp DB
    return db


def _insert_models(rows, project_id):
    from tools.db.storage import get_canvas_connection
    with get_canvas_connection("AIMC_DB_URL") as conn:
        cur = conn.cursor()
        for key, val in rows:
            cur.execute(
                "INSERT INTO aimc_models (id, project_id, metric_key, metric_value) "
                "VALUES (%s, %s, %s, %s)",
                (uuid.uuid4().hex, project_id, key, str(val)),
            )
        conn.commit()


def _insert_deployment(rows, project_id):
    from tools.db.storage import get_canvas_connection
    with get_canvas_connection("AIMC_DB_URL") as conn:
        cur = conn.cursor()
        for key, val in rows:
            cur.execute(
                "INSERT INTO aimc_deployment (id, project_id, check_key, check_value) "
                "VALUES (%s, %s, %s, %s)",
                (uuid.uuid4().hex, project_id, key, str(val)),
            )
        conn.commit()


# ── model_scanner ─────────────────────────────────────────────────────────────

def test_scan_models_returns_real_rows(aimc_db):
    from tools.aimc import model_scanner
    _insert_models(
        [
            ("model_count", 7),
            ("model_registry_present", "true"),
            ("monitoring_enabled", "true"),
            ("frameworks", "TensorFlow"),
        ],
        "proj-real",
    )
    result = model_scanner.scan_models("proj-real")

    assert result["status"] == "success"
    assert result["inventory"]["model_count"] == 7
    assert result["inventory"]["model_registry_present"] is True
    assert result["inventory"]["monitoring_enabled"] is True
    # controls not recorded are honestly reported as absent (not fabricated present)
    assert result["inventory"]["ab_testing_enabled"] is False
    assert result["frameworks"] == ["TensorFlow"]
    # governance score computed from real rows: 2 of 7 controls present
    assert result["governance_score"] == pytest.approx(round((2 / 7) * 100, 1))


def test_scan_models_empty_is_no_data(aimc_db):
    from tools.aimc import model_scanner
    result = model_scanner.scan_models("proj-empty")

    assert result["status"] == "no-data"
    assert result["reason"] == "empty-inventory"
    # explicitly NO fabricated inventory / score keys
    assert "inventory" not in result
    assert "governance_score" not in result


def test_scan_models_never_serves_pytorch_default(aimc_db):
    """Regression: the old _DEFAULT_INVENTORY (count 4, PyTorch/sklearn) is gone."""
    from tools.aimc import model_scanner
    result = model_scanner.scan_models("proj-empty")
    assert result.get("frameworks", []) != ["PyTorch", "sklearn"]
    assert result.get("status") == "no-data"


# ── deployment_checker ──────────────────────────────────────────────────────────

def test_deployment_returns_real_rows_pass(aimc_db):
    from tools.aimc import deployment_checker
    _insert_deployment(
        [
            ("model_card_present", "true"),
            ("bias_testing_done", "true"),
            ("performance_benchmarks_met", "true"),
            ("p90_latency_ms", "100"),
            ("latency_sla_ms", "500"),
        ],
        "proj-ready",
    )
    result = deployment_checker.run_deployment_checks("proj-ready")

    assert result["status"] == "success"
    assert result["gate"] == "PASS"
    assert result["checks"]["model_card_present"] is True
    assert result["checks"]["p90_latency_ms"] == 100.0
    assert not any(f["severity"] == "fail" for f in result["findings"])


def test_deployment_returns_real_rows_fail_on_latency(aimc_db):
    from tools.aimc import deployment_checker
    _insert_deployment(
        [
            ("model_card_present", "true"),
            ("bias_testing_done", "true"),
            ("performance_benchmarks_met", "true"),
            ("p90_latency_ms", "900"),
            ("latency_sla_ms", "500"),
        ],
        "proj-slow",
    )
    result = deployment_checker.run_deployment_checks("proj-slow")

    assert result["status"] == "success"
    assert result["gate"] == "FAIL"
    assert any(f["check"] == "latency_sla" for f in result["findings"])


def test_deployment_empty_is_no_data(aimc_db):
    from tools.aimc import deployment_checker
    result = deployment_checker.run_deployment_checks("proj-empty")

    assert result["status"] == "no-data"
    assert result["reason"] == "empty-checks"
    # explicitly NO fabricated checks / gate keys
    assert "checks" not in result
    assert "gate" not in result


# ── no-data reports never contain fabricated values ─────────────────────────────

def test_scan_no_data_report_has_no_fabricated_numbers(aimc_db):
    from tools.aimc import model_scanner
    result = model_scanner.scan_models("proj-empty")
    report = model_scanner.build_report(result)
    assert "No Data" in report
    assert "PyTorch" not in report
    assert "sklearn" not in report


def test_deployment_no_data_report_has_no_gate_decision(aimc_db):
    from tools.aimc import deployment_checker
    result = deployment_checker.run_deployment_checks("proj-empty")
    report = deployment_checker.build_report(result)
    assert "No Data" in report
    # no PASS/FAIL gate decision fabricated from absent data
    assert "**Deployment Gate:** PASS" not in report
    assert "**Deployment Gate:** FAIL" not in report


# ── fabricated default constants are removed ────────────────────────────────────

def test_default_constants_removed():
    ms = importlib.import_module("tools.aimc.model_scanner")
    dc = importlib.import_module("tools.aimc.deployment_checker")
    assert not hasattr(ms, "_DEFAULT_INVENTORY")
    assert not hasattr(dc, "_DEFAULT_CHECKS")


def test_default_constants_removed_in_mirror():
    ms = importlib.import_module("icdev.tools.aimc.model_scanner")
    dc = importlib.import_module("icdev.tools.aimc.deployment_checker")
    assert not hasattr(ms, "_DEFAULT_INVENTORY")
    assert not hasattr(dc, "_DEFAULT_CHECKS")
