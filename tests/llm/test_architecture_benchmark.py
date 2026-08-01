"""Tests for the AGX cross-architecture benchmark suite (agx-bench-01).

Every test injects fake runner/judge seams so the suite NEVER touches a live
model — proving the harness builds, tests and merges air-gapped.
"""
from __future__ import annotations

import json
from types import SimpleNamespace

from tools.llm.architectures import benchmark as bench


# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------
def _fake_result(output="ok answer", cost=0.001, ms=120, degraded=False, stop="completed"):
    return SimpleNamespace(
        output=output,
        cost_usd=cost,
        duration_ms=ms,
        degraded=degraded,
        stop_reason=stop,
    )


def _fake_score(composite=0.8, correctness=0.9):
    return SimpleNamespace(
        composite=composite,
        correctness=correctness,
        procedure_following=0.7,
        conciseness=0.6,
    )


def _task(tid="t1", family="code_review"):
    return bench.BenchmarkTask(id=tid, family=family, prompt="p", expected_behavior="e")


# ---------------------------------------------------------------------------
# Task suite loading
# ---------------------------------------------------------------------------
def test_load_task_suite_from_shipped_yaml():
    tasks = bench.load_task_suite()
    assert len(tasks) >= 10, "shipped suite should have the real-work task families"
    families = {t.family for t in tasks}
    for expected in {
        "compliance_drafting",
        "requirement_decomposition",
        "cve_triage",
        "code_review",
        "retrieval_qa",
        "migration_planning",
    }:
        assert expected in families, f"missing task family {expected}"
    for t in tasks:
        assert t.id and t.prompt and t.expected_behavior
        assert t.function  # router function for cost attribution


def test_load_task_suite_missing_file_returns_empty(tmp_path):
    assert bench.load_task_suite(str(tmp_path / "nope.yaml")) == []


# ---------------------------------------------------------------------------
# Cell execution
# ---------------------------------------------------------------------------
def test_run_cell_measured():
    cell = bench.run_cell(
        _task(),
        "chain_of_thought",
        "ollama",
        "phi4",
        runner_fn=lambda *a, **k: _fake_result(),
        judge_fn=lambda *a, **k: _fake_score(),
    )
    assert cell.status == "measured"
    assert cell.composite == 0.8
    assert cell.correctness == 0.9
    assert cell.cost_usd == 0.001
    assert cell.duration_ms == 120


def test_run_cell_unmeasured_when_model_unreachable():
    def boom(*a, **k):
        raise ConnectionError("ollama down")

    cell = bench.run_cell(
        _task(), "react", "ollama", "phi4",
        runner_fn=boom, judge_fn=lambda *a, **k: _fake_score(),
    )
    assert cell.status == "unmeasured"
    assert "unreachable" in cell.note
    assert cell.composite is None  # never fabricates a score


def test_run_cell_unmeasured_on_empty_output():
    cell = bench.run_cell(
        _task(), "tree_of_thoughts", "anthropic", "m",
        runner_fn=lambda *a, **k: _fake_result(output="", degraded=True, stop="budget_exceeded"),
        judge_fn=lambda *a, **k: _fake_score(),
    )
    assert cell.status == "unmeasured"
    assert cell.degraded is True
    assert "budget_exceeded" in cell.note


def test_run_cell_error_when_judge_fails():
    def bad_judge(*a, **k):
        raise ValueError("judge blew up")

    cell = bench.run_cell(
        _task(), "council", "openai", "m",
        runner_fn=lambda *a, **k: _fake_result(),
        judge_fn=bad_judge,
    )
    assert cell.status == "error"
    assert "judge failed" in cell.note


# ---------------------------------------------------------------------------
# Aggregation (deterministic)
# ---------------------------------------------------------------------------
def test_aggregate_means_and_family_rows():
    cells = [
        bench.CellResult("cot", "ollama", "m", "t1", "code_review", status="measured",
                         composite=0.8, correctness=0.9, cost_usd=0.001, duration_ms=100),
        bench.CellResult("cot", "ollama", "m", "t2", "cve_triage", status="measured",
                         composite=0.6, correctness=0.7, cost_usd=0.003, duration_ms=200),
    ]
    rows = bench.aggregate(cells, min_samples=1)
    star = [r for r in rows if r.task_family == "*"]
    assert len(star) == 1
    r = star[0]
    assert r.status == "measured"
    assert r.n_samples == 2
    assert r.mean_composite == 0.7  # (0.8+0.6)/2
    assert r.mean_cost_usd == 0.002
    assert r.mean_duration_ms == 150
    # per-family task rows also present
    fams = {r.task_family for r in rows}
    assert {"code_review", "cve_triage", "*"} <= fams


def test_aggregate_marks_insufficient_samples_unmeasured():
    cells = [
        bench.CellResult("rare", "ollama", "m", "t1", "code_review", status="measured",
                         composite=0.9, cost_usd=0.001, duration_ms=100),
    ]
    rows = bench.aggregate(cells, min_samples=3)
    for r in rows:
        assert r.status == "unmeasured"
        assert r.mean_composite is None


def test_aggregate_ignores_non_measured_cells():
    cells = [
        bench.CellResult("cot", "ollama", "m", "t1", "code_review", status="unmeasured"),
        bench.CellResult("cot", "ollama", "m", "t2", "code_review", status="error"),
    ]
    assert bench.aggregate(cells, min_samples=1) == []


def test_aggregate_is_deterministic():
    cells = [
        bench.CellResult("b", "openai", "m", "t1", "x", status="measured", composite=0.5, cost_usd=0.0, duration_ms=1),
        bench.CellResult("a", "ollama", "m", "t2", "y", status="measured", composite=0.5, cost_usd=0.0, duration_ms=1),
    ]
    r1 = [r.to_dict() for r in bench.aggregate(cells)]
    r2 = [r.to_dict() for r in bench.aggregate(list(reversed(cells)))]
    assert r1 == r2  # order-independent, stable sort


# ---------------------------------------------------------------------------
# End-to-end run (injected seams)
# ---------------------------------------------------------------------------
def test_run_benchmark_measured_with_injected_seams():
    tasks = [_task("t1", "code_review"), _task("t2", "cve_triage")]
    report = bench.run_benchmark(
        architectures=["chain_of_thought", "react"],
        model_specs=[("ollama", "phi4"), ("anthropic", "claude")],
        tasks=tasks,
        runner_fn=lambda *a, **k: _fake_result(),
        judge_fn=lambda *a, **k: _fake_score(),
    )
    assert report.status == "measured"
    # 2 arch x 2 families x 2 tasks = 8 cells
    assert len(report.cells) == 8
    assert all(c.status == "measured" for c in report.cells)
    assert set(report.model_families) == {"ollama", "anthropic"}
    assert not report.dropped


def test_run_benchmark_airgap_no_models_never_raises():
    report = bench.run_benchmark(
        architectures=["chain_of_thought"],
        model_specs=[],  # nothing reachable
        tasks=[_task()],
        runner_fn=lambda *a, **k: _fake_result(),
        judge_fn=lambda *a, **k: _fake_score(),
    )
    assert report.status == "unmeasured"
    assert report.cells == []
    assert any(d["what"] == "model_families" for d in report.dropped)
    assert any(d["what"] == "cells" for d in report.dropped)


def test_run_benchmark_partial_family_outage():
    """One family (ollama) unreachable, the other measured — honest mixed result."""
    def runner(task, arch, model_id, family, router):
        if family == "ollama":
            raise ConnectionError("ollama not running")
        return _fake_result()

    report = bench.run_benchmark(
        architectures=["cot"],
        model_specs=[("ollama", "phi4"), ("anthropic", "claude")],
        tasks=[_task("t1"), _task("t2")],
        runner_fn=runner,
        judge_fn=lambda *a, **k: _fake_score(),
    )
    assert report.status == "measured"
    ollama_cells = [c for c in report.cells if c.model_family == "ollama"]
    anthropic_cells = [c for c in report.cells if c.model_family == "anthropic"]
    assert all(c.status == "unmeasured" for c in ollama_cells)
    assert all(c.status == "measured" for c in anthropic_cells)
    # no ollama aggregate row (unmeasured cells excluded)
    assert not any(r.model_family == "ollama" for r in report.aggregates)


# ---------------------------------------------------------------------------
# Persistence
# ---------------------------------------------------------------------------
def test_persist_and_load_roundtrip(tmp_path):
    report = bench.run_benchmark(
        architectures=["cot"],
        model_specs=[("ollama", "phi4")],
        tasks=[_task()],
        runner_fn=lambda *a, **k: _fake_result(),
        judge_fn=lambda *a, **k: _fake_score(),
        min_samples=1,
    )
    path = bench.persist_report(report, str(tmp_path))
    assert path.exists()
    loaded = bench.load_latest_report(str(tmp_path))
    assert loaded is not None
    assert loaded["status"] == report.status
    # deterministic sorted JSON
    assert json.loads(path.read_text(encoding="utf-8"))["architectures"] == ["cot"]


def test_load_latest_missing_returns_none(tmp_path):
    assert bench.load_latest_report(str(tmp_path)) is None


# ---------------------------------------------------------------------------
# LLM-agnostic guard: no vendor SDK imports / hardcoded models in the module
# ---------------------------------------------------------------------------
def test_module_has_no_vendor_sdk_imports():
    import inspect

    src = inspect.getsource(bench)
    for banned in ("import anthropic", "import openai", "langchain", "langgraph"):
        assert banned not in src, f"vendor/framework import leaked: {banned}"


def test_render_markdown_smoke():
    report = bench.run_benchmark(
        architectures=["cot"],
        model_specs=[("ollama", "phi4")],
        tasks=[_task()],
        runner_fn=lambda *a, **k: _fake_result(),
        judge_fn=lambda *a, **k: _fake_score(),
    )
    md = bench.render_markdown(report)
    assert "AGX Cross-Architecture Benchmark" in md
    assert "cot" in md
