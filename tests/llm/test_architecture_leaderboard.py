"""Tests for the AGX leaderboard + evidence-based recommendation (agx-bench-02).

Includes the SAFETY-GOVERNOR regression guard: the SHIPPED args/llm_config.yaml
`architectures:` block must be a no-op, so this task changed no runtime selection.
"""
from __future__ import annotations

from pathlib import Path

import yaml

from tools.llm.architectures import leaderboard as lb


def _row(arch, mf, tf, comp, cost=0.001, ms=100, n=3, status="measured"):
    return {
        "architecture": arch,
        "model_family": mf,
        "task_family": tf,
        "status": status,
        "n_samples": n,
        "mean_composite": comp,
        "mean_correctness": comp,
        "mean_cost_usd": cost,
        "mean_duration_ms": ms,
    }


def _report(rows, status="measured", families=None):
    return {
        "status": status,
        "model_families": families or sorted({r["model_family"] for r in rows}),
        "aggregates": rows,
    }


# ---------------------------------------------------------------------------
# Leaderboard
# ---------------------------------------------------------------------------
def test_build_leaderboard_ranks_and_deltas():
    rows = [
        _row("baseline", "ollama", "code_review", 0.60),
        _row("chain_of_thought", "ollama", "code_review", 0.75),
        _row("react", "ollama", "code_review", 0.65),
    ]
    entries = lb.build_leaderboard(_report(rows), min_samples=3)
    ranked = {e.architecture: e for e in entries}
    assert ranked["chain_of_thought"].rank == 1
    assert ranked["react"].rank == 2
    assert ranked["baseline"].rank == 3
    assert ranked["chain_of_thought"].composite_delta_vs_baseline == 0.15
    assert ranked["chain_of_thought"].beats_baseline is True
    assert ranked["baseline"].composite_delta_vs_baseline == 0.0


def test_build_leaderboard_marks_low_sample_unmeasured():
    rows = [_row("baseline", "ollama", "cve_triage", 0.6, n=1)]
    entries = lb.build_leaderboard(_report(rows), min_samples=3)
    assert entries[0].status == "unmeasured"
    assert entries[0].rank is None


# ---------------------------------------------------------------------------
# Recommendation
# ---------------------------------------------------------------------------
def test_recommend_change_when_arch_beats_baseline_on_all_families():
    rows = [
        _row("baseline", "ollama", "code_review", 0.60),
        _row("chain_of_thought", "ollama", "code_review", 0.72),
        _row("baseline", "anthropic", "code_review", 0.65),
        _row("chain_of_thought", "anthropic", "code_review", 0.80),
    ]
    recs = {r.task_family: r for r in lb.recommend_defaults(_report(rows), min_margin=0.05)}
    r = recs["code_review"]
    assert r.decision == "recommend_change"
    assert r.recommended_architecture == "chain_of_thought"
    assert r.apply_hint  # human-apply hint present
    assert any("beats baseline" in e for e in r.evidence)


def test_recommend_rejects_frontier_only_win():
    """Wins on anthropic but LOSES on ollama -> must NOT be recommended (LLM-agnostic)."""
    rows = [
        _row("baseline", "ollama", "code_review", 0.60),
        _row("tree_of_thoughts", "ollama", "code_review", 0.55),   # worse locally
        _row("baseline", "anthropic", "code_review", 0.60),
        _row("tree_of_thoughts", "anthropic", "code_review", 0.90),  # much better on frontier
    ]
    r = {x.task_family: x for x in lb.recommend_defaults(_report(rows), min_margin=0.05)}["code_review"]
    assert r.decision == "keep_current"
    assert r.recommended_architecture is None


def test_recommend_keep_current_is_reported_not_buried():
    rows = [
        _row("baseline", "ollama", "cve_triage", 0.70),
        _row("react", "ollama", "cve_triage", 0.71),  # within margin, not a real win
    ]
    r = {x.task_family: x for x in lb.recommend_defaults(_report(rows), min_margin=0.05)}["cve_triage"]
    assert r.decision == "keep_current"
    assert any("No architecture beat the baseline" in e for e in r.evidence)


def test_recommend_rejects_cost_prohibitive_winner():
    rows = [
        _row("baseline", "ollama", "migration_planning", 0.60, cost=0.001),
        _row("council", "ollama", "migration_planning", 0.80, cost=0.010),  # 10x cost
    ]
    r = {x.task_family: x for x in lb.recommend_defaults(_report(rows), min_margin=0.05, max_cost_ratio=2.0)}["migration_planning"]
    assert r.decision == "keep_current"


def test_recommend_insufficient_evidence_when_no_baseline():
    rows = [_row("chain_of_thought", "ollama", "retrieval_qa", 0.80)]  # no baseline row
    r = {x.task_family: x for x in lb.recommend_defaults(_report(rows))}["retrieval_qa"]
    assert r.decision == "insufficient_evidence"
    assert r.recommended_architecture is None


def test_unmeasured_report_yields_no_recommendations_change():
    report = {"status": "unmeasured", "model_families": [], "aggregates": []}
    recs = lb.recommend_defaults(report)
    assert all(r.decision != "recommend_change" for r in recs)
    assert lb.build_leaderboard(report) == []


# ---------------------------------------------------------------------------
# Regression guard
# ---------------------------------------------------------------------------
def test_is_config_noop_true_for_shipped_shape():
    assert lb.is_config_noop({"default": None, "functions": {}, "roles": {}, "log_selections": True})
    assert lb.is_config_noop({})
    assert lb.is_config_noop(None)


def test_is_config_noop_false_when_routing_set():
    assert not lb.is_config_noop({"default": "chain_of_thought", "functions": {}, "roles": {}})
    assert not lb.is_config_noop({"default": None, "functions": {"code_review": "council"}, "roles": {}})
    assert not lb.is_config_noop({"default": None, "functions": {}, "roles": {"cot_reasoner": "react"}})


def test_check_no_degradation_empty_for_noop_config():
    rows = [_row("baseline", "ollama", "code_review", 0.6), _row("react", "ollama", "code_review", 0.4)]
    assert lb.check_no_degradation(_report(rows), {"default": None, "functions": {}, "roles": {}}) == []


def test_check_no_degradation_flags_configured_regression():
    rows = [
        _row("baseline", "ollama", "code_review", 0.70),
        _row("react", "ollama", "code_review", 0.55),  # worse than baseline but configured
    ]
    findings = lb.check_no_degradation(_report(rows), {"default": None, "functions": {"code_review": "react"}, "roles": {}})
    assert len(findings) == 1
    assert findings[0]["architecture"] == "react"
    assert findings[0]["regression"] < 0


# ---------------------------------------------------------------------------
# SAFETY GOVERNOR: the SHIPPED config must change no runtime selection
# ---------------------------------------------------------------------------
def test_shipped_llm_config_architectures_block_is_noop():
    from tools.llm.config_path import resolve_llm_config_path

    cfg = yaml.safe_load(Path(resolve_llm_config_path()).read_text(encoding="utf-8")) or {}
    block = cfg.get("architectures") or {}
    assert lb.is_config_noop(block), (
        "SHIPPED architectures block must be a no-op — agx-bench-02 must not flip "
        f"platform-wide routing defaults. Got: {block}"
    )


def test_shipped_config_resolves_to_current_behavior_for_real_functions():
    from tools.llm.architectures.selection import resolve_architecture
    from tools.llm.config_path import resolve_llm_config_path

    cfg = yaml.safe_load(Path(resolve_llm_config_path()).read_text(encoding="utf-8")) or {}
    for fn in ("narrative_generation", "code_review", "requirements_generation",
               "recommendation", "chat_response", "code_generation"):
        assert resolve_architecture(function=fn, config=cfg) is None, (
            f"function '{fn}' must resolve to None (current behavior) under shipped config"
        )
    for role in ("cot_reasoner", "cod_debater_pool"):
        assert resolve_architecture(role=role, config=cfg) is None


# ---------------------------------------------------------------------------
# baseline architecture is registered
# ---------------------------------------------------------------------------
def test_baseline_architecture_registered():
    from tools.llm.architectures import is_registered, list_architectures

    assert is_registered("baseline")
    assert "baseline" in list_architectures()


def test_leaderboard_module_no_vendor_imports():
    import inspect

    src = inspect.getsource(lb)
    for banned in ("import anthropic", "import openai", "langchain", "langgraph"):
        assert banned not in src
