# CUI // SP-CTI
"""Single-toggle isolation and reachability probing (oss-meas-01-d2).

The defect this guards against is subtle: a retrieval toggle that is **not wired
to anything** benchmarks identically to one that is wired and useless — both
produce a zero delta. ``oss-meas-01`` asks for a KEEP/DROP decision per toggle,
so a harness that cannot tell those apart would record "DROP — no measurable
benefit" against modules that were simply never connected.

Three of the five toggles named in that task are in that state today
(``reflective_rerank``, ``adaptive_routing``, ``auto_indexer``), which is why
the refusal path below is tested as hard as the measurement path.
"""
from __future__ import annotations

import os

import pytest
import yaml

from tools.rag import toggle_harness as th
from tools.rag.config_path import CONFIG_ENV_VAR, rag_config_path


# ── Config path resolution ────────────────────────────────────────────────────


def test_config_path_defaults_to_the_committed_file(monkeypatch):
    monkeypatch.delenv(CONFIG_ENV_VAR, raising=False)
    assert rag_config_path().name == "rag_config.yaml"
    assert rag_config_path().parent.name == "args"


def test_config_path_honours_the_override(monkeypatch, tmp_path):
    override = tmp_path / "alt.yaml"
    monkeypatch.setenv(CONFIG_ENV_VAR, str(override))
    assert rag_config_path() == override


def test_missing_override_is_not_silently_ignored(monkeypatch, tmp_path):
    """A typo'd override must not fall back to the committed config.

    Falling back would make a benchmark report a zero delta for a toggle it
    never flipped — measuring the wrong thing while looking successful.
    """
    ghost = tmp_path / "does-not-exist.yaml"
    monkeypatch.setenv(CONFIG_ENV_VAR, str(ghost))
    assert rag_config_path() == ghost
    assert not rag_config_path().exists()


# ── Reachability ──────────────────────────────────────────────────────────────


def test_probe_reports_wired_and_not_wired():
    results = {r.toggle: r for r in th.probe_all()}
    assert set(results) == set(th.TOGGLES)
    # rerank is called inline by retriever.search()
    assert results["rerank"].reachable is True
    assert results["rerank"].verdict == "WIRED"


@pytest.mark.parametrize("name", ["reflective_rerank", "adaptive_routing"])
def test_unwired_toggles_are_reported_not_wired(name):
    """Documents the state of the tree, and fails loudly when it changes.

    If someone wires one of these into the retrieval path, this test breaks —
    which is the correct signal to go re-run the benchmark and give it a real
    KEEP/DROP decision instead of a NOT-WIRED note.
    """
    probe = th.probe_reachability(name)
    assert probe.reachable is False
    assert probe.verdict == "NOT-WIRED"
    assert probe.importers == []
    assert "not connected" in probe.reason


def test_ingest_side_toggle_is_wired_but_unmeasurable():
    """auto_indexer changes what is indexed, not how a query is served."""
    probe = th.probe_reachability("auto_indexer")
    assert probe.retrieval_side is False
    assert probe.verdict in ("NOT-WIRED", "WIRED-INGEST-ONLY")


def test_closure_follows_deferred_imports():
    """Function-level imports must count.

    ``retriever.search()`` does ``from tools.rag.reranker import rerank_results``
    inside the branch that uses it. A top-level-only scan would call every
    toggle dead and the harness would refuse to measure anything.
    """
    closure = th.import_closure()
    assert "tools.rag.reranker" in closure
    assert "tools.rag.retriever" in closure["tools.rag.reranker"]


# ── Isolation ─────────────────────────────────────────────────────────────────


def test_isolated_config_sets_env_and_restores_it(monkeypatch):
    monkeypatch.delenv(CONFIG_ENV_VAR, raising=False)
    with th.isolated_config("rerank", True) as path:
        assert os.environ[CONFIG_ENV_VAR] == str(path)
        assert path.exists()
    assert CONFIG_ENV_VAR not in os.environ
    assert not path.exists(), "temp config must be cleaned up"


def test_isolated_config_preserves_a_pre_existing_override(monkeypatch, tmp_path):
    sentinel = str(tmp_path / "outer.yaml")
    monkeypatch.setenv(CONFIG_ENV_VAR, sentinel)
    with th.isolated_config("rerank", True):
        pass
    assert os.environ[CONFIG_ENV_VAR] == sentinel


def test_isolated_config_flips_exactly_one_toggle():
    base = th.load_base_config()
    with th.isolated_config("rerank", True) as path:
        written = yaml.safe_load(path.read_text(encoding="utf-8"))

    assert th.get_path(written, "rag.rerank.enabled") is True
    for name, spec in th.TOGGLES.items():
        if name == "rerank":
            continue
        assert th.get_path(written, spec.path) == th.get_path(base, spec.path), (
            f"{name} changed while isolating rerank"
        )


def test_none_yields_an_unmodified_baseline():
    """The control arm runs through the same temp-config path as the variants."""
    base = th.load_base_config()
    with th.isolated_config(None) as path:
        written = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert written == base


def test_committed_config_is_never_written():
    committed = rag_config_path()
    before = committed.read_bytes()
    with th.isolated_config("rerank", True):
        pass
    assert committed.read_bytes() == before


def test_env_restored_even_when_the_block_raises(monkeypatch):
    monkeypatch.delenv(CONFIG_ENV_VAR, raising=False)
    with pytest.raises(RuntimeError):
        with th.isolated_config("rerank", True):
            raise RuntimeError("boom")
    assert CONFIG_ENV_VAR not in os.environ


@pytest.mark.parametrize("name", ["rerank", "binary_prefilter", "raptor"])
def test_verify_isolation_proves_the_override_reaches_the_loader(name):
    """End-to-end: the loader the retriever actually calls sees the change."""
    result = th.verify_isolation(name)
    assert result["override_applied"] is True, f"{name} override never reached _load_rag_config"
    assert result["restored"] is True
    assert result["isolated"] is True, f"{name} bled into {result['bled_into']}"


# ── Benchmark refusal path ────────────────────────────────────────────────────


def test_sweep_refuses_to_benchmark_unwired_toggles(monkeypatch):
    """The whole point: no number is emitted for dead code."""
    from tools.rag import rag_benchmark

    class _StubBench:
        def __init__(self, **kwargs):
            pass

        def run(self):
            return {"aggregate": {"recall_at_k": 0.5, "mrr": 0.4}, "queries_scored": 3}

    monkeypatch.setattr(rag_benchmark, "RAGBenchmark", _StubBench)
    sweep = rag_benchmark.run_toggle_sweep(only=["rerank", "reflective_rerank", "auto_indexer"])

    by_name = {a["toggle"]: a for a in sweep["arms"]}
    assert by_name["rerank"]["benchmarked"] is True
    assert "deltas" in by_name["rerank"]

    for dead in ("reflective_rerank", "auto_indexer"):
        assert by_name[dead]["benchmarked"] is False
        assert "deltas" not in by_name[dead], "an unwired toggle must not carry a delta"
        assert "wire it or delete" in by_name[dead]["recommendation"]

    assert "reflective_rerank" in sweep["summary"]["not_wired"]
