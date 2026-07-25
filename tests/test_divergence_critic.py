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
                composite REAL, rationale TEXT, vocabulary_version TEXT,
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
