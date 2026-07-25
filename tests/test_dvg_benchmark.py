# CUI // SP-CTI
"""Tests for the divergence-vs-single-shot benchmark harness (dvg-bench-01).

Every seam is injected so no live model is touched: the harness must produce a
deterministic report from fake generators/judges, honestly mark unmeasured cells
on air-gap, hold the model-fixed flag correctly, and NEVER flip a default in its
recommendation.
"""
from __future__ import annotations

import importlib

bench = importlib.import_module("tools.creative.divergence_benchmark")
DivergenceTask = bench.DivergenceTask


# --------------------------------------------------------------- fakes
class _FakeIdea:
    def __init__(self, novelty=0.5, is_trap=False):
        self.dimension_floats = {"novelty": novelty}
        self.is_trap = is_trap


class _FakeScored:
    """Stand-in for ScoredPool with an ordered list; cluster_pool is monkeypatched."""

    def __init__(self, ideas, stop_reason="completed"):
        self.ordered = ideas
        self.stop_reason = stop_reason


def _tasks(n=2):
    return [DivergenceTask(id=f"t{i}", family="fam", prompt="p", function="creative_ideation") for i in range(n)]


def _make_generate(single_pool="1. a", div_pool="## Frame: x\n1. a\n2. b",
                   single_model="claude-sonnet", div_model="claude-haiku",
                   single_cost=0.001, div_cost=0.004, single_tok=100, div_tok=600):
    def gen(method, task, function, router, orchestrator):
        if method == "single_shot":
            return single_pool, single_cost, single_tok, single_model, ""
        return div_pool, div_cost, div_tok, div_model, "trace-1"
    return gen


def _make_judge(single_ideas, div_ideas):
    def judge(pool_text, function, trace_id, router):
        # divergence pool carries a frame header; single-shot does not.
        return _FakeScored(div_ideas if "Frame" in pool_text else single_ideas)
    return judge


def _patch_cluster(monkeypatch, n_single, n_div):
    critic = importlib.import_module("tools.quality.divergence_critic")
    # cluster count == distinct approaches; fake it from the pool size heuristic.
    monkeypatch.setattr(
        critic, "cluster_pool",
        lambda scored: [object()] * (n_div if len(scored.ordered) >= n_div else n_single),
    )


# --------------------------------------------------------------- metrics
def test_metrics_from_scored(monkeypatch):
    critic = importlib.import_module("tools.quality.divergence_critic")
    monkeypatch.setattr(critic, "cluster_pool", lambda scored: [object(), object()])
    scored = _FakeScored([_FakeIdea(0.8, False), _FakeIdea(0.4, True)])
    m = bench.metrics_from_scored(scored)
    assert m["breadth_ideas"] == 2
    assert m["breadth_clusters"] == 2
    assert m["trap_count"] == 1
    assert abs(m["mean_novelty"] - 0.6) < 1e-6


# --------------------------------------------------------------- measured run
def test_run_benchmark_measured_and_recommendation(monkeypatch):
    # Divergence yields 3 clusters / higher novelty / a trap; single-shot 1 cluster.
    single_ideas = [_FakeIdea(0.4, False)]
    div_ideas = [_FakeIdea(0.8, False), _FakeIdea(0.7, True), _FakeIdea(0.6, False)]
    _patch_cluster(monkeypatch, n_single=1, n_div=3)

    report = bench.run_benchmark(
        tasks=_tasks(2),
        generate_fn=_make_generate(single_model="claude-sonnet", div_model="claude-sonnet",
                                   single_cost=0.001, div_cost=0.003),
        judge_fn=_make_judge(single_ideas, div_ideas),
    )
    assert report.status == "measured"
    agg = report.aggregate
    assert agg["n_measured"] == 2
    assert agg["n_model_fixed"] == 2                 # same family both sides
    assert agg["mean_breadth_ratio"] == 3.0          # 3 clusters vs 1
    assert agg["mean_cost_ratio"] == 3.0             # 0.003 / 0.001
    assert agg["mean_trap_delta"] == 1.0
    # quality gain (breadth 3x) at acceptable cost (3x) -> candidate, but default stays OFF.
    rec = report.recommendation
    assert rec["verdict"] == "candidate_opt_in"
    assert rec["enable_divergence_default"] is False
    assert rec["promote_trap_to_gating"] is False


def test_model_not_fixed_is_flagged_inconclusive(monkeypatch):
    single_ideas = [_FakeIdea(0.4, False)]
    div_ideas = [_FakeIdea(0.9, False), _FakeIdea(0.9, False)]
    _patch_cluster(monkeypatch, n_single=1, n_div=2)
    report = bench.run_benchmark(
        tasks=_tasks(1),
        generate_fn=_make_generate(single_model="claude-sonnet", div_model="gpt-4o"),
        judge_fn=_make_judge(single_ideas, div_ideas),
    )
    assert report.status == "measured"
    assert report.aggregate["n_model_fixed"] == 0
    assert report.recommendation["verdict"] == "inconclusive"
    assert report.recommendation["enable_divergence_default"] is False


# --------------------------------------------------------------- air-gap
def test_air_gap_yields_unmeasured_never_raises():
    def dead_gen(method, task, function, router, orchestrator):
        raise RuntimeError("no reachable model")

    report = bench.run_benchmark(tasks=_tasks(2), generate_fn=dead_gen, judge_fn=lambda *a, **k: None)
    assert report.status == "unmeasured"
    assert report.aggregate["n_measured"] == 0
    assert report.recommendation["verdict"] == "unmeasured"
    assert report.recommendation["enable_divergence_default"] is False
    assert any(d["what"] == "comparisons" for d in report.dropped)


def test_empty_pool_is_unmeasured(monkeypatch):
    def empty_gen(method, task, function, router, orchestrator):
        return "", 0.0, 0, "claude-sonnet", ""

    report = bench.run_benchmark(tasks=_tasks(1), generate_fn=empty_gen, judge_fn=lambda *a, **k: None)
    assert report.status == "unmeasured"
    assert report.comparisons[0].status == "unmeasured"


# --------------------------------------------------------------- suite + persistence
def test_real_task_suite_loads():
    tasks = bench.load_task_suite()
    assert len(tasks) >= 5
    assert all(t.function == "creative_ideation" for t in tasks)
    assert {t.family for t in tasks} >= {"creative_pain_point", "innovation_signal"}


def test_persist_and_reload(tmp_path):
    report = bench.run_benchmark(tasks=[], generate_fn=lambda *a, **k: ("", 0, 0, "", ""), judge_fn=lambda *a, **k: None)
    path = bench.persist_report(report, out_dir=str(tmp_path))
    assert path.exists()
    reloaded = bench.load_latest_report(out_dir=str(tmp_path))
    assert reloaded is not None and reloaded["status"] == "unmeasured"


def test_ratio_and_same_family_helpers():
    assert bench._ratio(6, 2) == 3.0
    assert bench._ratio(1, 0) is None
    assert bench._same_family("claude-sonnet", "claude-haiku") is True
    assert bench._same_family("claude-sonnet", "gpt-4o") is False
