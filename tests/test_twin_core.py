# CUI // SP-CTI — Twin Core unit tests (twx-core-01)
"""Unit tests for the additive cross-canvas twin_core layer.

Covers the canonical schema normalization (the #1 project risk per the research
docs), the data-driven TwinRegistry, and the NDC/PDC reference adapters. Schema
and registry tests are pure (no DB); adapter tests stub the underlying twin so
they run on the shared conftest schema without canvas databases.
"""
from __future__ import annotations

import importlib

import pytest

from tools.twin_core import schema
from tools.twin_core.registry import TwinAdapter, TwinRegistry, register_twin


# ── schema: verdict normalization ─────────────────────────────────────────────

@pytest.mark.parametrize(
    "raw,expected",
    [
        ("pass", "pass"), ("PASS", "pass"), ("green", "pass"), ("ok", "pass"),
        ("satisfied", "pass"),
        ("warn", "warn"), ("warning", "warn"), ("amber", "warn"), ("partial", "warn"),
        ("fail", "fail"), ("red", "fail"), ("error", "fail"), ("failed", "fail"),
        ("blocked", "fail"),
        ("unknown", "unknown"), ("", "unknown"), (None, "unknown"),
        ("not_assessed", "unknown"), ("gibberish", "unknown"),
    ],
)
def test_normalize_verdict(raw, expected):
    assert schema.normalize_verdict(raw) == expected


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("blocker", "blocker"),
        ("critical", "critical"), ("CAT1", "critical"), ("cat_1", "critical"),
        ("high", "high"), ("CAT2", "high"),
        ("medium", "medium"), ("moderate", "medium"), ("CAT3", "medium"), ("warning", "medium"),
        ("low", "low"), ("info", "low"),
        ("mystery", "medium"), (None, "medium"),
    ],
)
def test_normalize_severity(raw, expected):
    assert schema.normalize_severity(raw) == expected


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("aws", "aws_govcloud"), ("AWS-GovCloud", "aws_govcloud"),
        ("azure", "azure_gov"), ("gcp", "gcp"), ("oracle", "oci"),
        ("localstack", "local"), (None, None), ("nope", None),
    ],
)
def test_normalize_csp(raw, expected):
    assert schema.normalize_csp(raw) == expected


def test_worst_verdict_ordering():
    assert schema.worst_verdict(["pass", "warn", "fail"]) == "fail"
    assert schema.worst_verdict(["pass", "warn"]) == "warn"
    assert schema.worst_verdict(["pass", "pass"]) == "pass"
    assert schema.worst_verdict(["unknown", "unknown"]) == "unknown"
    assert schema.worst_verdict(["unknown", "pass"]) == "pass"
    assert schema.worst_verdict([]) == "pass"


def test_worst_severity():
    viols = [{"severity": "low"}, {"severity": "critical"}, {"severity": "high"}]
    assert schema.worst_severity(viols) == "critical"
    assert schema.worst_severity([]) is None


def test_derive_verdict_from_violations():
    assert schema.derive_verdict_from_violations([{"severity": "critical"}]) == "fail"
    assert schema.derive_verdict_from_violations([{"severity": "blocker"}]) == "fail"
    assert schema.derive_verdict_from_violations([{"severity": "high"}]) == "warn"
    assert schema.derive_verdict_from_violations([{"severity": "medium"}]) == "warn"
    assert schema.derive_verdict_from_violations([{"severity": "low"}]) == "pass"
    assert schema.derive_verdict_from_violations([]) == "pass"


# ── schema: canonical violation factory ───────────────────────────────────────

def test_canonical_violation_shape():
    v = schema.canonical_violation(
        "CAT1", "network", "Add a firewall",
        target_csp="aws", auto_fixable=True, title="No FW", rule_id="no-direct-internet",
        source_canvas="ndc", method="heuristic",
    )
    # Sequoia Pattern 4 required fields all present.
    for field in ("severity", "category", "target_csp", "recommendation", "auto_fixable"):
        assert field in v
    assert v["severity"] == "critical"           # CAT1 normalized
    assert v["category"] == "network"
    assert v["target_csp"] == "aws_govcloud"     # aws normalized
    assert v["auto_fixable"] is True
    assert v["method"] == "heuristic"            # provenance preserved
    assert v["source_canvas"] == "ndc"


def test_canonical_violation_unknown_category_coerced():
    v = schema.canonical_violation("high", "bogus_cat", "fix it")
    assert v["category"] == "compliance"          # safe catch-all, not dropped


def test_summarize_violations_zero_filled():
    counts = schema.summarize_violations([{"severity": "high"}, {"severity": "high"}, {"severity": "low"}])
    assert counts["high"] == 2
    assert counts["low"] == 1
    assert counts["blocker"] == 0
    assert counts["total"] == 3


def test_twin_verdict_envelope():
    env = schema.twin_verdict(
        "ndc", "proj-1", "pass",
        [{"severity": "high", "category": "network", "recommendation": "x"}],
        method="heuristic", simulation_id="sim-1",
    )
    assert env["canvas"] == "ndc"
    assert env["verdict"] == "pass"
    assert env["method"] == "heuristic"
    assert env["simulation_id"] == "sim-1"
    assert env["counts"]["high"] == 1
    assert env["violations"][0]["category"] == "network"
    assert "generated_at" in env


# ── registry ──────────────────────────────────────────────────────────────────

def test_registry_discovers_reference_adapters():
    keys = TwinRegistry.discover(force=True)
    assert "ndc" in keys
    assert "pdc" in keys
    ndc = TwinRegistry.get("ndc")
    assert ndc is not None
    assert ndc.canvas_key == "ndc"
    assert ndc.method == "heuristic"
    pdc = TwinRegistry.get("pdc")
    assert pdc.method == "static-analysis"


def test_registry_describe_all():
    TwinRegistry.discover(force=True)
    described = {d["canvas"]: d for d in TwinRegistry.describe_all()}
    assert "ndc" in described and "pdc" in described
    assert described["ndc"]["supports_simulation"] is True


def test_registry_register_and_reset():
    class _StubAdapter(TwinAdapter):
        canvas_key = "_stub"
        method = "test"

    TwinRegistry.register(_StubAdapter())
    assert TwinRegistry.is_registered("_stub")
    # Base list_snapshots + latest_status degrade gracefully.
    stub = TwinRegistry.get("_stub")
    assert stub.list_snapshots("x") == []
    status = stub.latest_status("x")
    assert status["verdict"] == "unknown"
    assert status["snapshot_count"] == 0


def test_register_twin_decorator_requires_key():
    with pytest.raises(ValueError):
        class _NoKey(TwinAdapter):
            canvas_key = ""

        register_twin(_NoKey)


# ── adapters (stubbed underlying twin — no canvas DB needed) ───────────────────

def test_ndc_adapter_canonicalizes(monkeypatch):
    ndc_mod = importlib.import_module("tools.network.twin")
    fake = {
        "id": "sim-ndc-1",
        "verdict": "warn",
        "compliance_findings": [
            {"severity": "high", "id": "no-unencrypted", "title": "Plaintext link",
             "recommendation": "Use TLS"},
        ],
        "intent_results": [{"rule_id": "no-unencrypted", "passed": False}],
    }
    monkeypatch.setattr(ndc_mod, "simulate_delta", lambda *a, **k: fake)
    adapter = TwinRegistry.get("ndc")
    out = adapter.simulate_delta("proj-1", {"add_links": []})
    assert out["canvas"] == "ndc"
    assert out["verdict"] == "warn"
    assert out["violations"][0]["category"] == "network"
    assert out["violations"][0]["severity"] == "high"
    assert out["violations"][0]["method"] == "heuristic"
    assert out["extra"]["intent_results"]


def test_pdc_adapter_canonicalizes(monkeypatch):
    pdc_mod = importlib.import_module("tools.pipeline.twin")
    fake = {
        "id": "sim-pdc-1",
        "baseline_snap_id": "snap-1",
        "verdict": "fail",
        "antipatterns": [{"severity": "critical", "id": "no-gate", "title": "Missing gate",
                          "recommendation": "Add approval gate"}],
        "compliance": {"failures": [{"severity": "medium", "id": "slsa-1", "title": "No provenance"}]},
        "slsa": {"level": 1},
        "diff": {},
        "critical_count": 1,
        "high_count": 0,
    }
    monkeypatch.setattr(pdc_mod, "simulate_delta", lambda *a, **k: fake)
    adapter = TwinRegistry.get("pdc")
    out = adapter.simulate_delta("pipe-1", {"nodes": [], "edges": []})
    assert out["verdict"] == "fail"
    cats = {v["category"] for v in out["violations"]}
    assert cats == {"security", "compliance"}
    assert out["method"] == "static-analysis"
    # antipattern critical + compliance medium both carried
    sevs = {v["severity"] for v in out["violations"]}
    assert "critical" in sevs and "medium" in sevs
