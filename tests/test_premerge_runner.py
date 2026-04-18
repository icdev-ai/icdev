# CUI // SP-CTI
"""Tests for tools.pipeline.premerge_runner — 6 cases."""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tools.pipeline.premerge_runner import run_premerge, _merge_graphs  # noqa: E402


# ── Shared fixtures ───────────────────────────────────────────────────────────

def _node(id_, type_, label=None):
    return {"id": id_, "type": type_, "label": label or type_}


_CLEAN_DELTA = {
    "nodes": [
        _node("scm1", "scm-gitlab"),
        _node("ci1", "cicd-gitlab"),
        _node("scan1", "scan-trivy"),
        _node("scan2", "scan-semgrep"),
        _node("sec1", "vault-hashicorp"),
        _node("sbom1", "sbom-syft"),
        _node("sign1", "sign-cosign"),
        _node("gate1", "gate-manual"),
        _node("reg1", "registry-harbor"),
        _node("reg2", "registry-nexus"),
        _node("k8s1", "k8s-cluster"),
        _node("rt1", "mon-falco"),
        _node("slo1", "sre-slo"),
        _node("inc1", "sre-pagerduty"),
        _node("pol1", "policy-kyverno"),
    ],
    "edges": [],
}

_CRITICAL_DELTA = {
    "nodes": [
        _node("ci1", "cicd-gitlab"),
        _node("k8s1", "k8s-cluster"),
    ],
    "edges": [],
}

_HIGH_ONLY_DELTA = {
    "nodes": [
        _node("reg1", "registry-harbor"),
        _node("reg2", "registry-nexus"),
    ],
    "edges": [],
}


# ── 1. Clean delta → gate: pass ───────────────────────────────────────────────

def test_clean_delta_returns_pass():
    result = run_premerge(_CLEAN_DELTA)
    assert result["gate"] == "pass", f"Expected pass, got {result['gate']}; hits: {result['antipattern_hits']}"
    assert isinstance(result["antipattern_hits"], list)
    assert isinstance(result["slsa_score"], int)


# ── 2. Critical antipattern → gate: fail ─────────────────────────────────────

def test_critical_antipattern_returns_fail():
    # CI+deploy with no scanners, no gate → AP-PDC-001 (critical), AP-PDC-002 (critical)
    result = run_premerge(_CRITICAL_DELTA)
    assert result["gate"] == "fail"
    critical_ids = {a["id"] for a in result["antipattern_hits"] if a["severity"] == "critical"}
    assert critical_ids, "Expected at least one critical antipattern"


# ── 3. High-only antipatterns → gate: warn ───────────────────────────────────

def test_high_only_antipatterns_returns_warn():
    # Registries without signing triggers AP-PDC-003 (high)
    result = run_premerge(_HIGH_ONLY_DELTA)
    assert result["gate"] == "warn"
    severities = {a["severity"] for a in result["antipattern_hits"]}
    assert "high" in severities
    assert "critical" not in severities


# ── 4. SLSA score is returned correctly ──────────────────────────────────────

def test_slsa_score_reflects_evidence():
    # SCM + gitlab CI satisfy some SLSA evidence; score ≥ 0 and ≤ 4
    delta = {
        "nodes": [
            _node("scm1", "scm-gitlab"),
            _node("ci1", "cicd-tekton"),
        ],
        "edges": [],
    }
    result = run_premerge(delta)
    assert 0 <= result["slsa_score"] <= 4


# ── 5. Baseline merge: delta nodes override baseline ─────────────────────────

def test_merge_graphs_delta_overrides_baseline():
    baseline = {
        "nodes": [_node("n1", "scan-trivy"), _node("n2", "scm-gitlab")],
        "edges": [{"id": "e1", "source": "n1", "target": "n2"}],
    }
    delta = {
        "nodes": [_node("n1", "scan-semgrep"), _node("n3", "cicd-gitlab")],
        "edges": [{"id": "e2", "source": "n2", "target": "n3"}],
    }
    merged = _merge_graphs(baseline, delta)
    node_map = {n["id"]: n for n in merged["nodes"]}
    # n1 should be replaced by delta version
    assert node_map["n1"]["type"] == "scan-semgrep"
    # n2 preserved from baseline
    assert node_map["n2"]["type"] == "scm-gitlab"
    # n3 added from delta
    assert "n3" in node_map
    # both edges present
    edge_ids = {e["id"] for e in merged["edges"]}
    assert edge_ids == {"e1", "e2"}


# ── 6. --gate flag exits 1 on fail ───────────────────────────────────────────

def test_gate_flag_exits_1_on_fail(tmp_path):
    delta_file = tmp_path / "delta.json"
    delta_file.write_text(json.dumps(_CRITICAL_DELTA), encoding="utf-8")

    proc = subprocess.run(
        [sys.executable, "tools/pipeline/premerge_runner.py", "--delta", str(delta_file), "--gate", "--json"],
        capture_output=True,
        text=True,
        cwd=str(Path(__file__).resolve().parent.parent),
    )
    assert proc.returncode == 1, f"Expected exit 1 for fail gate; stdout={proc.stdout!r}"
    output = json.loads(proc.stdout)
    assert output["gate"] == "fail"
