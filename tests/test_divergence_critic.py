# CUI // SP-CTI
"""Unit tests for the divergence critic (dvg-critic-01).

Mock the router so no real LLM calls are made. Verify: pool parsing, the
categorical->Python composition + ordering (never asking the model to rank),
clean degradation on LLM/parse failure, and persistence keyed by trace_id.
"""
import json
from unittest.mock import MagicMock

from tools.quality.categorical_scoring import compose_divergence
from tools.quality.divergence_critic import (
    ScoredPool,
    parse_idea_pool,
    score_idea_pool,
)

POOL = """# Divergent Idea Pool (2 branch(es), frame set 'generative')

## Frame: The Adversary
1. Assume breach and design for it. Ship with a compromised-node kill switch.
2. Poison-pill honeytokens seeded through the pipeline.

## Frame: The Shoestring
1. One cron job and a flat file. No services.
"""


def _mock_router(scores):
    """Return a router whose invoke() replies with the given scores as JSON."""
    router = MagicMock()
    resp = MagicMock()
    resp.content = json.dumps({"scores": scores})
    resp.model_id = "mock-model"
    router.invoke.return_value = resp
    return router


class TestParsePool:
    def test_parses_frames_and_numbered_ideas(self):
        ideas = parse_idea_pool(POOL)
        assert len(ideas) == 3
        assert ideas[0]["frame"] == "The Adversary"
        assert ideas[2]["frame"] == "The Shoestring"
        assert "kill switch" in ideas[0]["idea"]

    def test_empty_pool_returns_empty(self):
        assert parse_idea_pool("") == []

    def test_unframed_prose_not_dropped(self):
        ideas = parse_idea_pool("just a single idea with no frame header")
        assert len(ideas) == 1


class TestScoring:
    def test_scores_and_orders_by_python_composite(self):
        # Adversary idea 0: strong; Adversary idea 1: weak; Shoestring: middling.
        scores = [
            {"index": 0, "novelty": "breakthrough", "viability": "viable", "fit": "on_target",
             "rationale": "strong all round"},
            {"index": 1, "novelty": "derivative", "viability": "unviable", "fit": "off_target",
             "rationale": "weak"},
            {"index": 2, "novelty": "incremental", "viability": "risky", "fit": "adjacent",
             "rationale": "ok"},
        ]
        pool = score_idea_pool(POOL, function="test_fn", router=_mock_router(scores), persist=False)
        assert isinstance(pool, ScoredPool)
        assert pool.stop_reason == "completed"
        assert len(pool.ordered) == 3
        # Ordering is composed in Python: the strong idea must rank first, weak last.
        assert pool.ordered[0].idea.startswith("Assume breach")
        assert pool.ordered[-1].novelty == "derivative"
        # composites strictly descending
        comps = [s.composite for s in pool.ordered]
        assert comps == sorted(comps, reverse=True)

    def test_composite_matches_categorical_scoring(self):
        scores = [{"index": 0, "novelty": "breakthrough", "viability": "viable", "fit": "on_target", "rationale": "x"},
                  {"index": 1, "novelty": "incremental", "viability": "risky", "fit": "adjacent", "rationale": "y"},
                  {"index": 2, "novelty": "derivative", "viability": "unviable", "fit": "off_target", "rationale": "z"}]
        pool = score_idea_pool(POOL, function="test_fn", router=_mock_router(scores), persist=False)
        top = pool.ordered[0]
        expected = compose_divergence("breakthrough", "viable", "on_target")["composite"]
        assert top.composite == expected

    def test_unknown_enum_degrades_to_midpoint(self):
        scores = [{"index": 0, "novelty": "wat", "viability": "huh", "fit": "???", "rationale": "x"},
                  {"index": 1, "novelty": "wat", "viability": "huh", "fit": "???", "rationale": "y"},
                  {"index": 2, "novelty": "wat", "viability": "huh", "fit": "???", "rationale": "z"}]
        pool = score_idea_pool(POOL, function="test_fn", router=_mock_router(scores), persist=False)
        # all unknown -> composite 0.5 for each (neutral midpoint), never a crash
        assert all(s.composite == 0.5 for s in pool.ordered)

    def test_empty_pool_short_circuits(self):
        router = MagicMock()
        pool = score_idea_pool("", function="test_fn", router=router, persist=False)
        assert pool.stop_reason == "empty_pool"
        router.invoke.assert_not_called()

    def test_llm_unavailable_degrades_cleanly(self):
        router = MagicMock()
        router.invoke.side_effect = RuntimeError("no provider")
        pool = score_idea_pool(POOL, function="test_fn", router=router, persist=False)
        assert pool.stop_reason == "critic_unavailable"
        assert pool.ordered == []

    def test_unparseable_output_degrades_cleanly(self):
        router = MagicMock()
        resp = MagicMock()
        resp.content = "I refuse to answer in JSON, here is prose instead."
        resp.model_id = "mock"
        router.invoke.return_value = resp
        pool = score_idea_pool(POOL, function="test_fn", router=router, persist=False)
        assert pool.stop_reason == "unparseable_critic_output"

    def test_json_in_code_fence_parsed(self):
        router = MagicMock()
        resp = MagicMock()
        resp.content = "```json\n" + json.dumps({"scores": [
            {"index": 0, "novelty": "viable", "viability": "viable", "fit": "on_target", "rationale": "x"},
            {"index": 1, "novelty": "incremental", "viability": "risky", "fit": "adjacent", "rationale": "y"},
            {"index": 2, "novelty": "derivative", "viability": "unviable", "fit": "off_target", "rationale": "z"},
        ]}) + "\n```"
        resp.model_id = "mock"
        router.invoke.return_value = resp
        pool = score_idea_pool(POOL, function="test_fn", router=router, persist=False)
        assert pool.stop_reason == "completed"
        assert len(pool.ordered) == 3


class TestPersistence:
    def test_persists_scores_keyed_by_trace_id(self, tmp_path, monkeypatch):
        import sqlite3

        db = tmp_path / "t.db"
        conn0 = sqlite3.connect(str(db))
        conn0.executescript(
            """
            CREATE TABLE divergence_idea_scores (
                id TEXT PRIMARY KEY, trace_id TEXT, function TEXT, idea_index INTEGER,
                frame TEXT, idea_text TEXT, novelty TEXT, viability TEXT, fit TEXT,
                composite REAL, rationale TEXT,
                trap_flag TEXT, trap_level REAL, is_trap INTEGER, trap_rationale TEXT,
                vocabulary_version TEXT,
                tenant_id TEXT, classification TEXT, created_at TEXT);
            """
        )
        conn0.commit()
        conn0.close()

        # Patch get_connection used inside _persist_scores to our tmp sqlite,
        # translating %s -> ? for the raw driver.
        class _Wrap:
            def __init__(self, path):
                self._c = sqlite3.connect(str(path))
            def execute(self, sql, params=()):
                return self._c.execute(sql.replace("%s", "?"), params)
            def commit(self):
                self._c.commit()
            def close(self):
                self._c.close()

        import importlib

        storage = importlib.import_module("tools.db.storage")
        monkeypatch.setattr(storage, "get_connection", lambda *a, **k: _Wrap(db))

        scores = [{"index": i, "novelty": "viable", "viability": "viable", "fit": "on_target", "rationale": "x"}
                  for i in range(3)]
        pool = score_idea_pool(POOL, function="test_fn", trace_id="trace-xyz",
                               router=_mock_router(scores), persist=True)
        assert pool.persisted is True

        conn = sqlite3.connect(str(db))
        rows = conn.execute(
            "SELECT trace_id, tenant_id, classification, composite FROM divergence_idea_scores WHERE trace_id=?",
            ("trace-xyz",),
        ).fetchall()
        conn.close()
        assert len(rows) == 3
        assert all(r[0] == "trace-xyz" and r[1] == "default" and r[2] == "CUI" for r in rows)

    def test_missing_table_tolerated(self):
        # persist=True with no patched DB -> best-effort, must not raise; persisted False on failure.
        scores = [{"index": i, "novelty": "viable", "viability": "viable", "fit": "on_target", "rationale": "x"}
                  for i in range(3)]
        pool = score_idea_pool(POOL, function="test_fn", router=_mock_router(scores), persist=True)
        assert pool.stop_reason == "completed"  # scoring still succeeds regardless of persistence


class TestTrapDetection:
    def _score_with_traps(self, trap_entries):
        return score_idea_pool(POOL, function="test_fn", router=_mock_router(trap_entries), persist=False)

    def test_actionable_trap_surfaces_with_rationale(self):
        entries = [
            {"index": 0, "novelty": "breakthrough", "viability": "viable", "fit": "on_target",
             "rationale": "strong", "trap": "trap",
             "trap_rationale": "Honeytokens leak the schema to the very adversary they target."},
            {"index": 1, "novelty": "incremental", "viability": "risky", "fit": "adjacent",
             "rationale": "ok", "trap": "clear", "trap_rationale": ""},
            {"index": 2, "novelty": "derivative", "viability": "unviable", "fit": "off_target",
             "rationale": "weak", "trap": "clear", "trap_rationale": ""},
        ]
        pool = self._score_with_traps(entries)
        warnings = pool.trap_warnings()
        assert len(warnings) == 1
        w = warnings[0]
        assert w["kind"] == "divergence_trap"
        assert w["severity"] == "warning"  # advisory only, never 'block'
        assert "Honeytokens" in w["rationale"]

    def test_unexplained_trap_flag_is_discarded(self):
        """The mandatory-explanation rule: a trap flag with NO rationale is not
        actionable and must not surface — an unexplained flag cannot be reviewed."""
        entries = [
            {"index": 0, "novelty": "breakthrough", "viability": "viable", "fit": "on_target",
             "rationale": "strong", "trap": "trap", "trap_rationale": ""},  # no why
            {"index": 1, "novelty": "incremental", "viability": "risky", "fit": "adjacent",
             "rationale": "ok", "trap": "clear", "trap_rationale": ""},
            {"index": 2, "novelty": "derivative", "viability": "unviable", "fit": "off_target",
             "rationale": "weak", "trap": "clear", "trap_rationale": ""},
        ]
        pool = self._score_with_traps(entries)
        # The idea still exists and is scored; it just carries no actionable trap.
        assert pool.trap_warnings() == []
        top = [s for s in pool.ordered if s.index == 0][0]
        assert top.trap_flag == "trap"
        assert top.is_trap is False  # demoted for lack of rationale

    def test_suspected_trap_with_rationale_is_actionable(self):
        entries = [
            {"index": 0, "novelty": "breakthrough", "viability": "viable", "fit": "on_target",
             "rationale": "s", "trap": "suspected_trap", "trap_rationale": "may not scale past pilot"},
            {"index": 1, "novelty": "incremental", "viability": "risky", "fit": "adjacent",
             "rationale": "o", "trap": "clear", "trap_rationale": ""},
            {"index": 2, "novelty": "derivative", "viability": "unviable", "fit": "off_target",
             "rationale": "w", "trap": "clear", "trap_rationale": ""},
        ]
        pool = self._score_with_traps(entries)
        assert len(pool.trap_warnings()) == 1

    def test_traps_are_advisory_never_block(self):
        entries = [
            {"index": i, "novelty": "viable", "viability": "viable", "fit": "on_target",
             "rationale": "x", "trap": "trap", "trap_rationale": "explained failure mode"}
            for i in range(3)
        ]
        pool = self._score_with_traps(entries)
        assert all(w["severity"] == "warning" for w in pool.trap_warnings())
        assert len(pool.trap_warnings()) == 3


class TestClusterAndDeepen:
    def _scored(self, entries):
        return score_idea_pool(POOL, function="test_fn", router=_mock_router(entries), persist=False)

    def test_cluster_pool_collapses_restatements(self):
        from tools.quality.divergence_critic import cluster_pool
        entries = [
            {"index": 0, "novelty": "breakthrough", "viability": "viable", "fit": "on_target",
             "rationale": "a", "cluster": "assume breach"},
            {"index": 1, "novelty": "incremental", "viability": "viable", "fit": "on_target",
             "rationale": "b", "cluster": "Assume Breach"},   # same approach, different casing
            {"index": 2, "novelty": "derivative", "viability": "risky", "fit": "adjacent",
             "rationale": "c", "cluster": "flat file"},
        ]
        pool = self._scored(entries)
        clusters = cluster_pool(pool)
        # 3 ideas but only 2 underlying approaches
        assert len(clusters) == 2
        # most-promising cluster first (best member composite)
        assert clusters[0].best_composite >= clusters[1].best_composite
        breach = [c for c in clusters if c.label.lower() == "assume breach"][0]
        assert len(breach.member_indices) == 2

    def test_cluster_falls_back_to_frame_without_label(self):
        from tools.quality.divergence_critic import cluster_pool
        entries = [
            {"index": i, "novelty": "viable", "viability": "viable", "fit": "on_target", "rationale": "x"}
            for i in range(3)
        ]  # no cluster labels
        pool = self._scored(entries)
        clusters = cluster_pool(pool)
        # POOL has 2 frames (The Adversary x2, The Shoestring x1) -> 2 clusters
        assert len(clusters) == 2

    def test_deepen_top_k_expands_only_survivors(self):
        from tools.quality.divergence_critic import cluster_and_deepen
        entries = [
            {"index": 0, "novelty": "breakthrough", "viability": "viable", "fit": "on_target",
             "rationale": "a", "cluster": "A"},
            {"index": 1, "novelty": "incremental", "viability": "risky", "fit": "adjacent",
             "rationale": "b", "cluster": "B"},
            {"index": 2, "novelty": "derivative", "viability": "unviable", "fit": "off_target",
             "rationale": "c", "cluster": "C"},
        ]
        pool = self._scored(entries)

        deepen_router = MagicMock()
        resp = MagicMock()
        resp.content = json.dumps({"clusters": [
            {"index": 0, "sketch": "do A", "risks": ["r1"], "next_steps": ["s1", "s2"]},
            {"index": 1, "sketch": "do B", "risks": [], "next_steps": ["s3"]},
        ]})
        deepen_router.invoke.return_value = resp

        out = cluster_and_deepen(pool, function="test_fn", router=deepen_router, k=2)
        assert out["k"] == 2
        assert out["cluster_count"] == 3
        clusters = out["clusters"]
        # top-2 deepened, 3rd not
        assert clusters[0]["deepened"] is True and clusters[0]["sketch"] == "do A"
        assert clusters[0]["risks"] == ["r1"] and clusters[0]["next_steps"] == ["s1", "s2"]
        assert clusters[2]["deepened"] is False and clusters[2]["sketch"] == ""

    def test_deepen_degrades_cleanly_without_llm(self):
        from tools.quality.divergence_critic import cluster_and_deepen
        entries = [
            {"index": i, "novelty": "viable", "viability": "viable", "fit": "on_target",
             "rationale": "x", "cluster": f"C{i}"}
            for i in range(3)
        ]
        pool = self._scored(entries)
        router = MagicMock()
        router.invoke.side_effect = RuntimeError("no provider")
        out = cluster_and_deepen(pool, function="test_fn", router=router, k=3)
        # clusters still returned; simply not deepened
        assert out["cluster_count"] == 3
        assert all(c["deepened"] is False for c in out["clusters"])

    def test_k_resolved_from_config_default(self):
        from tools.quality.divergence_critic import _resolve_deepen_k, DEFAULT_DEEPEN_TOP_K
        assert _resolve_deepen_k(None, None) == DEFAULT_DEEPEN_TOP_K
        router = MagicMock()
        router._config = {"chain_orchestration": {"divergence": {"deepen_top_k": 5}}}
        assert _resolve_deepen_k(router, None) == 5
        # explicit k wins over config
        assert _resolve_deepen_k(router, 2) == 2
