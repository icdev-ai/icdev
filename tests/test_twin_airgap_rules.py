# CUI // SP-CTI — Air-gap twin rules tests (twx-fed-01)
"""Negative-first tests for the shared air-gap validation rules.

Per the docs' 'query-as-compliance false-confidence' risk, every rule has a
known-bad fixture that MUST trip it — a silently-broken matcher cannot pass as
'compliant'. Plus allowlist + clean-design + adapter-wiring coverage.
"""
from __future__ import annotations

import importlib

from tools.twin_core import airgap_rules as ar
from tools.twin_core.registry import TwinRegistry


def _sev(v):
    return v["severity"]


def test_config_loads_and_enabled():
    cfg = ar.load_rules(force=True)
    assert cfg.get("enabled") is True
    ids = {r["id"] for r in cfg["rules"]}
    assert ids == {"airgap-internal-registry", "airgap-internal-package-mirror",
                   "airgap-no-external-api", "airgap-no-public-egress",
                   # flx-airgap-02. Unlike the four above it is NOT a
                   # deny-by-match string rule -- it derives the emulator's
                   # run-time image set and checks the local cache.
                   "airgap-emulator-runtime-images"}


# ── negative fixtures: each MUST trip its rule ────────────────────────────────

def test_public_registry_image_trips_registry_rule():
    graph = {"nodes": [{"id": "n1", "type": "container", "image": "docker.io/library/nginx:latest"}]}
    v = ar.evaluate_airgap(graph, source_canvas="idc", active=True)
    rids = {x["rule_id"] for x in v}
    assert "airgap-internal-registry" in rids
    assert all(_sev(x) == "blocker" for x in v)  # deployment_blocker -> blocker


def test_public_pip_index_trips_mirror_rule():
    plan = {"resource_changes": [{"after": {"pip_index_url": "https://pypi.org/simple"}}]}
    v = ar.evaluate_airgap(plan, active=True)
    assert "airgap-internal-package-mirror" in {x["rule_id"] for x in v}


def test_external_api_trips_api_rule():
    graph = {"nodes": [{"id": "svc", "api_url": "https://api.openai.com/v1/chat"}]}
    v = ar.evaluate_airgap(graph, active=True)
    assert "airgap-no-external-api" in {x["rule_id"] for x in v}


def test_public_egress_marker_trips_egress_rule():
    graph = {"nodes": [{"id": "rt", "type": "route", "destination": "0.0.0.0/0", "gateway": "igw-123"}]}
    v = ar.evaluate_airgap(graph, active=True)
    assert "airgap-no-public-egress" in {x["rule_id"] for x in v}


def test_ghcr_and_quay_both_trip():
    graph = {"nodes": [{"image": "ghcr.io/org/app:1"}, {"image": "quay.io/org/db:2"}]}
    v = ar.evaluate_airgap(graph, active=True)
    hits = [x for x in v if x["rule_id"] == "airgap-internal-registry"]
    assert {x["detail"] for x in hits} == {"ghcr.io", "quay.io"}


# ── allowlist + clean design must NOT trip ────────────────────────────────────

def test_internal_registry_allowlisted():
    graph = {"nodes": [{"image": "registry.internal/library/nginx:latest"}]}
    assert ar.evaluate_airgap(graph, active=True) == []


def test_clean_design_no_violations():
    graph = {"nodes": [{"id": "a", "type": "vm", "label": "app"},
                       {"id": "b", "image": "nexus.internal/app:1"}],
             "edges": [{"source": "a", "target": "b"}]}
    assert ar.evaluate_airgap(graph, active=True) == []


def test_disabled_when_inactive():
    graph = {"nodes": [{"image": "docker.io/nginx"}]}
    assert ar.evaluate_airgap(graph, active=False) == []


def test_disabled_config_returns_empty():
    graph = {"nodes": [{"image": "docker.io/nginx"}]}
    assert ar.evaluate_airgap(graph, config={"enabled": False, "rules": []}) == []


# ── adapter wiring: air-gap blockers escalate verdict to fail ─────────────────

def test_idc_adapter_airgap_escalates(monkeypatch):
    mod = importlib.import_module("tools.infra_canvas.preapply_gate")
    monkeypatch.setattr(mod, "run_gate", lambda plan: {"gate": "pass", "violations": [], "delta": {}})
    adapter = TwinRegistry.get("idc")
    # Plan references a public registry -> air-gap blocker -> verdict fail.
    plan = {"resource_changes": [{"after": {"image": "docker.io/library/redis"}}]}
    out = adapter.simulate_delta("proj", plan, airgap=True)
    assert out["verdict"] == "fail"
    assert any(v["rule_id"] == "airgap-internal-registry" for v in out["violations"])
    assert any(v["severity"] == "blocker" for v in out["violations"])


def test_ndc_adapter_airgap_optin(monkeypatch):
    mod = importlib.import_module("tools.network.twin")
    monkeypatch.setattr(mod, "simulate_delta", lambda *a, **k: {
        "id": "s", "verdict": "pass", "compliance_findings": [], "intent_results": []})
    adapter = TwinRegistry.get("ndc")
    # airgap not requested and env not air-gapped -> no air-gap escalation.
    out_off = adapter.simulate_delta("p", {"add_links": []})
    assert out_off["verdict"] == "pass"
    # airgap on + public egress marker -> fail.
    out_on = adapter.simulate_delta("p", {"add_devices": [{"id": "x", "destination": "0.0.0.0/0"}]}, airgap=True)
    assert out_on["verdict"] == "fail"
