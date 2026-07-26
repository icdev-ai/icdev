#!/usr/bin/env python3
# CUI // SP-CTI
"""Tests for single-toggle isolation in the RAG benchmark runner (oss-meas-01).

The point of the runner under test is attribution: a metric delta must be
traceable to ONE toggle. So these tests assert on the isolation itself — that
the control has all five off, that each variant has exactly one on, and that a
behaviour change shows up against the toggle that caused it and no other.

Uses injected fixture retrievers — never touches the live corpus or DB, so the
suite is deterministic and runs in a fresh (empty-DB) worktree.
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tools.rag.rag_benchmark import (  # noqa: E402
    MEASURED_REGRESSIONS,
    TOGGLES,
    RAGBenchmark,
    build_isolated_config,
    dry_run_plan,
    isolated_toggle_config,
    load_ground_truth,
    load_rag_config,
    run_toggle_matrix,
    toggle_names,
)
from tools.rag.vector_store_provider import SearchResult  # noqa: E402


def _res(chunk_id: str, content: str, source_id: str = "") -> SearchResult:
    return SearchResult(chunk_id=chunk_id, content=content, source_id=source_id)


def _read_path(cfg: dict, path) -> object:
    node = cfg
    for key in path:
        node = node[key]
    return node


# ---------------------------------------------------------------------------
# Toggle registry
# ---------------------------------------------------------------------------


class TestToggleRegistry:
    def test_exactly_five_toggles(self) -> None:
        assert len(TOGGLES) == 5
        assert len(toggle_names()) == 5

    def test_name_is_the_config_path(self) -> None:
        # The printed name and the key someone would edit must not diverge.
        for spec in TOGGLES:
            assert spec.name == ".".join(spec.path)

    def test_names_are_unique(self) -> None:
        assert len(set(toggle_names())) == 5

    def test_raptor_is_not_under_test(self) -> None:
        # Already measured as a regression; carried as prior art, not re-run.
        assert "rag.raptor.enabled" not in toggle_names()
        assert MEASURED_REGRESSIONS["rag.raptor.enabled"]["verdict"] == "DROP"

    def test_every_toggle_exists_in_the_shipped_config(self) -> None:
        # A toggle the runner flips that rag_config.yaml does not define would
        # make that row of the matrix silently measure nothing.
        cfg = load_rag_config()
        for spec in TOGGLES:
            node = cfg
            for key in spec.path:
                assert isinstance(node, dict) and key in node, f"missing: {spec.name}"
                node = node[key]
            assert isinstance(node, bool), f"{spec.name} is not a boolean"

    def test_all_five_ship_off(self) -> None:
        cfg = load_rag_config()
        for spec in TOGGLES:
            assert _read_path(cfg, spec.path) is False, f"{spec.name} is not OFF on disk"


# ---------------------------------------------------------------------------
# Isolation
# ---------------------------------------------------------------------------


class TestBuildIsolatedConfig:
    def test_control_forces_every_toggle_off(self) -> None:
        cfg = build_isolated_config(load_rag_config(), enable=None)
        for spec in TOGGLES:
            assert _read_path(cfg, spec.path) is False

    def test_exactly_one_toggle_on(self) -> None:
        target = "rag.rerank.enabled"
        cfg = build_isolated_config(load_rag_config(), enable=target)
        on = [s.name for s in TOGGLES if _read_path(cfg, s.path)]
        assert on == [target]

    def test_isolation_overrides_a_pre_enabled_toggle(self) -> None:
        # Every toggle is written explicitly, so a flipped on-disk default
        # cannot leak into a run and contaminate the attribution.
        base = {"rag": {"auto_indexer": {"enabled": True}}}
        cfg = build_isolated_config(base, enable="rag.rerank.enabled")
        assert cfg["rag"]["auto_indexer"]["enabled"] is False
        assert cfg["rag"]["rerank"]["enabled"] is True

    def test_does_not_mutate_the_base_config(self) -> None:
        base = {"rag": {"rerank": {"enabled": False}}}
        build_isolated_config(base, enable="rag.rerank.enabled")
        assert base["rag"]["rerank"]["enabled"] is False

    def test_preserves_unrelated_config(self) -> None:
        base = {"rag": {"retrieval": {"final_top_k": 7}}}
        cfg = build_isolated_config(base, enable=None)
        assert cfg["rag"]["retrieval"]["final_top_k"] == 7

    def test_creates_missing_intermediate_keys(self) -> None:
        cfg = build_isolated_config({}, enable="rag.quantization.binary_prefilter.enabled")
        assert cfg["rag"]["quantization"]["binary_prefilter"]["enabled"] is True

    def test_unknown_toggle_rejected(self) -> None:
        with pytest.raises(ValueError, match="Unknown toggle"):
            build_isolated_config({}, enable="rag.nope.enabled")


class TestIsolatedToggleConfig:
    def test_patches_and_restores_the_module_loader(self) -> None:
        import tools.rag.retriever as retriever_mod

        original = retriever_mod._load_rag_config
        marker = {"rag": {"marker": True}}
        with isolated_toggle_config(marker):
            assert retriever_mod._load_rag_config() == marker
        assert retriever_mod._load_rag_config is original

    def test_patches_the_quantization_loader(self) -> None:
        import tools.rag.sqlite_vector_store as svs

        original = svs._load_quantization_config
        cfg = {"rag": {"quantization": {"binary_prefilter": {"enabled": True}}}}
        with isolated_toggle_config(cfg):
            assert svs._load_quantization_config()["binary_prefilter"]["enabled"] is True
        assert svs._load_quantization_config is original

    def test_restores_on_exception(self) -> None:
        import tools.rag.retriever as retriever_mod

        original = retriever_mod._load_rag_config
        with pytest.raises(RuntimeError):
            with isolated_toggle_config({"rag": {}}):
                raise RuntimeError("boom")
        assert retriever_mod._load_rag_config is original


# ---------------------------------------------------------------------------
# Latency
# ---------------------------------------------------------------------------


class TestLatency:
    def test_run_reports_latency(self) -> None:
        data = {
            "top_k": 3,
            "queries": [{"id": "q1", "query": "a", "expect": {"substrings": ["x"]}}],
        }
        out = RAGBenchmark(golden_set=data).run(search_fn=lambda q, k: [_res("c1", "x")])
        assert out["latency"]["samples"] == 1
        assert out["latency"]["mean_ms"] >= 0
        assert out["results"][0]["latency_ms"] >= 0

    def test_no_samples_when_nothing_scored(self) -> None:
        data = {"top_k": 3, "queries": [{"id": "q1", "query": "a", "expect": {}}]}
        out = RAGBenchmark(golden_set=data).run(search_fn=lambda q, k: [])
        assert out["latency"]["samples"] == 0
        assert out["latency"]["mean_ms"] is None

    def test_errored_query_contributes_no_latency(self) -> None:
        def boom(query, k):
            raise RuntimeError("retriever down")

        data = {
            "top_k": 3,
            "queries": [{"id": "q1", "query": "a", "expect": {"substrings": ["x"]}}],
        }
        out = RAGBenchmark(golden_set=data).run(search_fn=boom)
        assert out["latency"]["samples"] == 0


# ---------------------------------------------------------------------------
# The matrix
# ---------------------------------------------------------------------------


_GOLDEN = {
    "top_k": 3,
    "queries": [
        {"id": "q1", "query": "alpha", "expect": {"substrings": ["hit"]}},
        {"id": "q2", "query": "beta", "expect": {"substrings": ["hit"]}},
    ],
}


@pytest.fixture
def fixed_golden_set(monkeypatch):
    """Pin RAGBenchmark to a 2-query fixture set so the matrix stays fast."""
    import tools.rag.rag_benchmark as rb

    real_init = rb.RAGBenchmark.__init__

    def patched(self, golden_set=None, golden_set_path=None, top_k=None):
        real_init(self, golden_set=_GOLDEN, top_k=top_k)

    monkeypatch.setattr(rb.RAGBenchmark, "__init__", patched)


def _recording_factory(seen):
    """Search factory that records each run's config.

    Retrieval "hits" only when rerank is on, so a delta must appear against
    exactly that toggle and nowhere else.
    """

    def factory(cfg):
        seen.append(cfg)
        on = cfg["rag"]["rerank"]["enabled"]
        return lambda q, k: [_res("c1", "hit" if on else "miss")]

    return factory


class TestToggleMatrix:
    def test_runs_control_plus_one_per_toggle(self, fixed_golden_set) -> None:
        seen = []
        out = run_toggle_matrix(
            rag_config={}, search_fn_factory=_recording_factory(seen), ground_truth={}
        )
        assert len(seen) == len(TOGGLES) + 1
        assert list(out["toggles"]) == toggle_names()
        assert out["toggles_tested"] == toggle_names()

    def test_control_has_every_toggle_off(self, fixed_golden_set) -> None:
        seen = []
        run_toggle_matrix(
            rag_config={}, search_fn_factory=_recording_factory(seen), ground_truth={}
        )
        for spec in TOGGLES:
            assert _read_path(seen[0], spec.path) is False

    def test_each_variant_isolates_exactly_one_toggle(self, fixed_golden_set) -> None:
        seen = []
        run_toggle_matrix(
            rag_config={}, search_fn_factory=_recording_factory(seen), ground_truth={}
        )
        for cfg, spec in zip(seen[1:], TOGGLES):
            enabled = [s.name for s in TOGGLES if _read_path(cfg, s.path)]
            assert enabled == [spec.name]

    def test_delta_attributed_to_the_toggle_that_changed_behaviour(
        self, fixed_golden_set
    ) -> None:
        out = run_toggle_matrix(
            rag_config={}, search_fn_factory=_recording_factory([]), ground_truth={}
        )
        assert out["control"]["aggregate"]["citation_hit_rate"] == 0.0
        rerank = out["toggles"]["rag.rerank.enabled"]
        assert rerank["aggregate"]["citation_hit_rate"] == 1.0
        assert rerank["delta_vs_control"]["citation_hit_rate"] == 1.0
        for name in toggle_names():
            if name != "rag.rerank.enabled":
                assert out["toggles"][name]["delta_vs_control"]["citation_hit_rate"] == 0.0

    def test_reports_all_acceptance_metrics_per_toggle(self, fixed_golden_set) -> None:
        out = run_toggle_matrix(
            rag_config={}, search_fn_factory=_recording_factory([]), ground_truth={}
        )
        entry = out["toggles"]["rag.rerank.enabled"]
        agg = entry["aggregate"]
        assert "recall_at_3" in agg and "mrr" in agg
        assert "ndcg_at_3" in agg and "citation_hit_rate" in agg
        assert entry["latency"]["samples"] == 2

    def test_carries_the_prior_raptor_measurement(self, fixed_golden_set) -> None:
        out = run_toggle_matrix(
            rag_config={}, search_fn_factory=_recording_factory([]), ground_truth={}
        )
        assert out["previously_measured"]["rag.raptor.enabled"]["verdict"] == "DROP"


# ---------------------------------------------------------------------------
# Dry run + ground truth
# ---------------------------------------------------------------------------


class TestDryRunPlan:
    def test_lists_five_toggles_without_retrieving(self) -> None:
        plan = dry_run_plan()
        assert plan["dry_run"] is True
        assert plan["toggle_count"] == 5
        assert plan["toggles_tested"] == toggle_names()
        assert plan["runs_planned"] == 6

    def test_reports_the_shipped_golden_set(self) -> None:
        plan = dry_run_plan()
        assert plan["queries_available"] > 0
        assert plan["top_k"] == 5

    def test_declares_every_acceptance_metric(self) -> None:
        metrics = dry_run_plan()["metrics"]
        for m in ("recall_at_k", "mrr", "ndcg_at_k", "citation_hit_rate", "latency_ms"):
            assert m in metrics

    def test_missing_golden_set_is_reported_not_raised(self, tmp_path) -> None:
        plan = dry_run_plan(golden_set_path=tmp_path / "absent.yaml")
        assert "golden_set_error" in plan
        assert plan["toggle_count"] == 5  # the toggle list still resolves


class TestGroundTruth:
    def test_loads_the_committed_compliance_baselines(self) -> None:
        gt = load_ground_truth()
        assert set(gt) == {"baseline_compliance", "contextual_compliance"}
        for label, entry in gt.items():
            assert "error" not in entry, f"{label}: {entry.get('error')}"
            assert entry["aggregate"]["recall_at_5"] > 0

    def test_missing_artifact_reported_not_raised(self, tmp_path) -> None:
        gt = load_ground_truth({"absent": tmp_path / "nope.json"})
        assert "not found" in gt["absent"]["error"]
