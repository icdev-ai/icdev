# CUI // SP-CTI — Twin Core observer + full adapter-set tests (twx-core-02)
"""Tests for the remaining canvas adapters (BDC/SDC/DDC/ODC/IDC/Mission) and the
cross-canvas observer. Underlying twins are stubbed so tests run on the shared
conftest schema without any canvas database.
"""
from __future__ import annotations

import importlib

import pytest

from tools.twin_core import observer
from tools.twin_core.registry import TwinRegistry


ALL_CANVASES = {"ndc", "pdc", "bdc", "sdc", "ddc", "odc", "idc", "mission_canvas"}


def test_all_adapters_registered():
    keys = set(TwinRegistry.discover(force=True))
    assert ALL_CANVASES.issubset(keys), f"missing: {ALL_CANVASES - keys}"


@pytest.mark.parametrize("key", sorted(ALL_CANVASES))
def test_adapter_describe(key):
    TwinRegistry.discover(force=True)
    a = TwinRegistry.get(key)
    d = a.describe()
    assert d["canvas"] == key
    assert d["method"]  # provenance label present


def test_bdc_adapter_normalizes_rating(monkeypatch):
    mod = importlib.import_module("tools.boundary_canvas.twin")
    fake = {
        "simulation_id": "s1", "rating": "amber", "verdict": "amber", "score": 0.6,
        "cod_method": "heuristic",
        "violations": [{"severity": "high", "id": "AC-2", "title": "AC-2 not satisfied",
                        "recommendation": "Implement"}],
        "compliance_delta": {"resolved": 1, "new_gaps": 1, "total": 2},
    }
    monkeypatch.setattr(mod, "simulate_delta", lambda *a, **k: fake)
    out = TwinRegistry.get("bdc").simulate_delta("proj", [{"implementation_status": "not_satisfied"}])
    assert out["verdict"] == "warn"          # amber -> warn
    assert out["violations"][0]["category"] == "compliance"
    assert out["violations"][0]["method"] == "heuristic"   # cod_method carried


def test_bdc_unknown_not_greenwashed(monkeypatch):
    mod = importlib.import_module("tools.boundary_canvas.twin")
    monkeypatch.setattr(mod, "simulate_delta", lambda *a, **k: {"rating": "unknown", "verdict": "unknown", "violations": []})
    out = TwinRegistry.get("bdc").simulate_delta("proj", [])
    assert out["verdict"] == "unknown"       # honest, never coerced to pass


def test_sdc_adapter_attack_paths(monkeypatch):
    mod = importlib.import_module("tools.security_canvas.twin")
    fake = {"simulation_id": "s", "verdict": "fail", "risk_score": 0.8,
            "attack_paths": [{"path_id": "path-1", "severity": "critical",
                              "path": ["internet", "web", "db"], "description": "via web"}]}
    monkeypatch.setattr(mod, "simulate_delta", lambda *a, **k: fake)
    out = TwinRegistry.get("sdc").simulate_delta("d", {"nodes": [], "edges": []})
    assert out["verdict"] == "fail"
    assert out["violations"][0]["category"] == "security"
    assert out["violations"][0]["severity"] == "critical"


def test_ddc_adapter_combines_gate(monkeypatch):
    mod = importlib.import_module("tools.data_canvas.twin")
    monkeypatch.setattr(mod, "simulate_delta", lambda *a, **k: {"simulation_id": "s", "verdict": "warn",
                                                                "coverage_score": 0.9, "orphan_count": 1})
    monkeypatch.setattr(mod, "quality_gate", lambda *a, **k: {"gate": "fail", "violations": [
        {"severity": "high", "type": "referential_integrity", "id": "t.c", "title": "downstream",
         "recommendation": "repoint"}]})
    out = TwinRegistry.get("ddc").simulate_delta("d", [{"change": "remove_column"}])
    assert out["verdict"] == "warn"
    assert out["violations"][0]["category"] == "compliance"
    assert out["violations"][0]["rule_id"] == "referential_integrity"


def test_odc_adapter_gaps(monkeypatch):
    mod = importlib.import_module("tools.observability_canvas.twin")
    fake = {"simulation_id": "s", "verdict": "warn", "estimate": True, "basis": "x",
            "projected_coverage_pct": 80.0,
            "gaps": [{"severity": "high", "id": "exp-1", "title": "Exporter removed",
                      "recommendation": "add replacement"}]}
    monkeypatch.setattr(mod, "simulate_delta", lambda *a, **k: fake)
    out = TwinRegistry.get("odc").simulate_delta("d", {"remove_exporters": ["exp-1"]})
    assert out["verdict"] == "warn"
    assert out["violations"][0]["category"] == "security"
    assert out["extra"]["estimate"] is True


def test_idc_adapter_gate_cat_severity(monkeypatch):
    mod = importlib.import_module("tools.infra_canvas.preapply_gate")
    fake = {"gate": "fail", "delta": {"add": 1, "modify": 0, "delete": 0},
            "violations": [{"source": "iqe", "check": "no-public-s3", "severity": "CAT1",
                            "detail": "bucket public", "affected": ["r1"]}]}
    monkeypatch.setattr(mod, "run_gate", lambda plan: fake)
    out = TwinRegistry.get("idc").simulate_delta("proj", {"resource_changes": []})
    assert out["verdict"] == "fail"
    assert out["violations"][0]["severity"] == "critical"   # CAT1 -> critical
    assert out["violations"][0]["category"] == "compliance"


def test_idc_take_snapshot_requires_graph():
    with pytest.raises(ValueError):
        TwinRegistry.get("idc").take_snapshot("proj")


def test_mission_adapter_delegates(monkeypatch):
    mod = importlib.import_module("tools.mission_canvas.twin")
    monkeypatch.setattr(mod, "simulate_delta", lambda *a, **k: {"verdict": "pass", "coverage_score": 1.0,
                                                                "downstream_impacts": []})
    out = TwinRegistry.get("mission_canvas").simulate_delta("m", [])
    assert out["verdict"] == "pass"
    assert out["canvas"] == "mission_canvas"


# ── observer ──────────────────────────────────────────────────────────────────

def test_observe_shape_and_inventory():
    report = observer.observe()
    assert "generated_at" in report
    assert report["twin_count"] >= 8
    canvases = {t["canvas"] for t in report["twins"]}
    assert ALL_CANVASES.issubset(canvases)
    summ = report["summary"]
    assert set(summ["verdict_distribution"]).issuperset({"pass", "warn", "fail", "unknown"})
    assert "stale_twins" in summ and "overdue_reflexes" in summ


def test_observe_fleet_health_degrades_gracefully(monkeypatch):
    # Force every adapter's fleet_health to raise; observer must still produce a report.
    from tools.twin_core.registry import TwinAdapter

    def boom(self, window_hours=24):
        raise RuntimeError("no db")

    monkeypatch.setattr(TwinAdapter, "fleet_health", boom)
    report = observer.observe()
    assert report["twin_count"] >= 8
    # All twins report fleet health unavailable rather than crashing the observer.
    assert all(t["fleet_health_available"] is False for t in report["twins"])


def test_observer_reflex_adherence_unknown(monkeypatch):
    # No genesis_reflex_state row -> IDC twin reflex flagged overdue (expected but unseen).
    monkeypatch.setattr(observer, "_reflex_state", lambda name: None)
    from datetime import datetime, timezone

    info = observer._reflex_adherence("idc", datetime.now(timezone.utc))
    assert info["scheduled"] is True
    assert info["overdue"] is True
    assert observer._reflex_adherence("ndc", datetime.now(timezone.utc)) is None  # unscheduled
