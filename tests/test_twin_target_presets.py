# CUI // SP-CTI — Twin target-environment preset tests (twx-fed-02)
"""Tests for running twin simulations against target-environment presets:
service-availability parity, air-gap engagement, and the staleness guard."""
from __future__ import annotations

import importlib
import json

from tools.twin_core import target_presets as tp
from tools.twin_core.registry import TwinRegistry


def test_presets_load_and_public_only():
    presets = tp.list_presets()
    assert "aws_govcloud_west" in presets
    assert "azure_gov" in presets
    p = tp.get_preset("aws_high_side_airgap")
    assert p["network_constraints"]["airgap"] is True
    assert p["region_scope"] == "government"


def test_service_available_passes():
    # EKS is govcloud_available in the real catalog -> no parity violation.
    graph = {"nodes": [{"id": "n", "type": "compute", "label": "uses eks cluster"}]}
    v = tp.evaluate_target(graph, "aws_govcloud_west")
    parity = [x for x in v if x["rule_id"] == "service-not-available"]
    assert parity == []


def test_service_not_available_trips_parity(tmp_path, monkeypatch):
    # Synthetic catalog: 'fancy_ai' NOT available in government scope.
    cat = {
        "_metadata": {"last_updated": "2026-07-01T00:00:00Z"},
        "services": {"aws": {
            "fancy_ai": {"display_name": "Fancy AI", "category": "ai_ml",
                         "govcloud_available": False, "commercial_available": True,
                         "regions": {"government": [], "commercial": ["us-east-1"]}},
        }},
    }
    cat_path = tmp_path / "cat.json"
    cat_path.write_text(json.dumps(cat), encoding="utf-8")
    monkeypatch.setenv("ICDEV_CSP_CATALOG_PATH", str(cat_path))
    tp.load_service_catalog(force=True)  # refresh cache under the env override
    graph = {"nodes": [{"id": "n", "label": "deploy fancy_ai model"}]}
    v = tp.evaluate_target(graph, "aws_govcloud_west")
    parity = [x for x in v if x["rule_id"] == "service-not-available"]
    assert parity, "service unavailable in GovCloud must trip service_parity"
    assert parity[0]["category"] == "service_parity"
    assert parity[0]["severity"] == "blocker"       # deployment_blocker
    assert parity[0]["target_csp"] == "aws_govcloud"
    tp.load_service_catalog(force=True)  # reset cache for other tests


def test_staleness_guard_warns():
    # A synthetic preset reviewed long ago must warn.
    old_preset = {"csp": "aws", "region_scope": "government", "region": "us-gov-west-1",
                  "reviewed_at": "2020-01-01", "network_constraints": {"airgap": False}}
    v = tp.evaluate_target({"nodes": []}, old_preset)
    stale = [x for x in v if x["rule_id"] == "target-staleness"]
    assert stale and stale[0]["severity"] == "medium"


def test_airgap_preset_engages_fed01():
    # Air-gapped preset + public registry image -> air-gap deployment blocker.
    graph = {"nodes": [{"image": "docker.io/library/redis:7"}]}
    v = tp.evaluate_target(graph, "aws_high_side_airgap")
    assert any(x["rule_id"] == "airgap-internal-registry" for x in v)


def test_unknown_preset_returns_empty():
    assert tp.evaluate_target({"nodes": []}, "no_such_preset") == []


def test_idc_adapter_target_preset_escalates(monkeypatch, tmp_path):
    cat = {"_metadata": {"last_updated": "2026-07-01T00:00:00Z"},
           "services": {"aws": {"fancy_ai": {"display_name": "Fancy AI", "govcloud_available": False,
                                             "regions": {"government": []}}}}}
    cat_path = tmp_path / "cat.json"
    cat_path.write_text(json.dumps(cat), encoding="utf-8")
    monkeypatch.setenv("ICDEV_CSP_CATALOG_PATH", str(cat_path))
    tp.load_service_catalog(force=True)
    mod = importlib.import_module("tools.infra_canvas.preapply_gate")
    monkeypatch.setattr(mod, "run_gate", lambda plan: {"gate": "pass", "violations": [], "delta": {}})
    adapter = TwinRegistry.get("idc")
    plan = {"resource_changes": [{"after": {"model": "fancy_ai endpoint"}}]}
    out = adapter.simulate_delta("proj", plan, target_preset="aws_govcloud_west")
    assert out["verdict"] == "fail"
    assert any(v["rule_id"] == "service-not-available" for v in out["violations"])
    tp.load_service_catalog(force=True)
