#!/usr/bin/env python3
# CUI // SP-CTI
"""Tests for the Episodic Distiller Genesis Reflex (Phase B).

Tests cover: clustering logic, threshold-based skip, heuristic fallback,
mark-distilled DB update, genesis_config registration, and daemon wiring.
"""

import json
import sqlite3
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_DIR))

_SCHEMA = """
CREATE TABLE IF NOT EXISTS memory_entries (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    content TEXT NOT NULL,
    type TEXT DEFAULT 'event',
    importance INTEGER DEFAULT 5,
    embedding BLOB,
    created_at TEXT DEFAULT (datetime('now')),
    updated_at TEXT DEFAULT (datetime('now')),
    content_hash TEXT,
    user_id TEXT,
    tenant_id TEXT,
    source TEXT DEFAULT 'manual',
    decay_weight REAL DEFAULT 1.0,
    classification TEXT DEFAULT 'CUI',
    compartment TEXT DEFAULT '',
    tags TEXT,
    metadata TEXT,
    tier TEXT DEFAULT 'episodic',
    session_ref TEXT DEFAULT NULL,
    distilled INTEGER DEFAULT 0
);
"""


@pytest.fixture()
def mem_db(tmp_path):
    db_path = tmp_path / "memory.db"
    conn = sqlite3.connect(str(db_path))
    conn.executescript(_SCHEMA)
    conn.close()
    return db_path


def _seed_episodic(db_path, n: int, base_text: str = "episodic event about ICDEV agent loop") -> list[int]:
    import hashlib
    conn = sqlite3.connect(str(db_path))
    ids = []
    for i in range(n):
        text = f"{base_text} {i}"
        fp = hashlib.sha256(text.lower().strip().encode()).hexdigest()
        conn.execute(
            "INSERT OR IGNORE INTO memory_entries "
            "(content, type, importance, content_hash, tier, distilled) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (text, "event", 5, fp, "episodic", 0),
        )
    conn.commit()
    ids = [r[0] for r in conn.execute("SELECT id FROM memory_entries ORDER BY id").fetchall()]
    conn.close()
    return ids


# ---------------------------------------------------------------------------
# Unit: clustering helpers
# ---------------------------------------------------------------------------


class TestClusteringHelpers:
    def test_tokenize_returns_words_4_chars(self):
        from tools.genesis.reflexes.episodic_distiller import _tokenize
        tokens = _tokenize("ICDEV agent loop completed successfully")
        assert "icdev" in tokens
        assert "agent" in tokens
        assert "loop" in tokens

    def test_tokenize_ignores_short_words(self):
        from tools.genesis.reflexes.episodic_distiller import _tokenize
        tokens = _tokenize("the cat sat on a mat")
        assert "cat" not in tokens  # 3 chars
        assert "mat" not in tokens

    def test_cosine_identical_vectors(self):
        from tools.genesis.reflexes.episodic_distiller import _cosine
        v = [1.0, 0.5, 0.3]
        assert abs(_cosine(v, v) - 1.0) < 1e-6

    def test_cosine_orthogonal_vectors(self):
        from tools.genesis.reflexes.episodic_distiller import _cosine
        assert _cosine([1.0, 0.0], [0.0, 1.0]) == 0.0

    def test_cosine_zero_vector(self):
        from tools.genesis.reflexes.episodic_distiller import _cosine
        assert _cosine([0.0, 0.0], [1.0, 0.5]) == 0.0

    def test_cluster_entries_groups_similar(self):
        from tools.genesis.reflexes.episodic_distiller import _cluster_entries
        entries = [
            {"id": 1, "content": "ICDEV agent loop successfully completed the task"},
            {"id": 2, "content": "ICDEV agent loop finished executing the workflow"},
            {"id": 3, "content": "Database migration applied for new table schema"},
        ]
        clusters = _cluster_entries(entries, threshold=0.2)
        assert len(clusters) >= 1
        all_ids = [e["id"] for cl in clusters for e in cl]
        assert sorted(all_ids) == [1, 2, 3]

    def test_cluster_entries_empty(self):
        from tools.genesis.reflexes.episodic_distiller import _cluster_entries
        assert _cluster_entries([]) == []

    def test_cluster_entries_single(self):
        from tools.genesis.reflexes.episodic_distiller import _cluster_entries
        entry = {"id": 1, "content": "lone entry about something specific"}
        clusters = _cluster_entries([entry])
        assert len(clusters) == 1
        assert clusters[0][0]["id"] == 1


# ---------------------------------------------------------------------------
# Unit: heuristic fallback distillation
# ---------------------------------------------------------------------------


class TestHeuristicDistillation:
    def test_returns_top_importance_entries(self):
        from tools.genesis.reflexes.episodic_distiller import _distill_cluster_heuristic
        cluster = [
            {"id": 1, "content": "First event low importance", "importance": 3},
            {"id": 2, "content": "Second event high importance best fact", "importance": 9},
            {"id": 3, "content": "Third event medium importance stuff", "importance": 5},
        ]
        facts = _distill_cluster_heuristic(cluster, max_facts=2)
        assert len(facts) <= 2
        assert any("high importance" in f for f in facts)

    def test_skips_short_content(self):
        from tools.genesis.reflexes.episodic_distiller import _distill_cluster_heuristic
        cluster = [
            {"id": 1, "content": "short", "importance": 9},
            {"id": 2, "content": "This is a longer and more meaningful episodic fact", "importance": 5},
        ]
        facts = _distill_cluster_heuristic(cluster, max_facts=2)
        assert not any(f == "short" for f in facts)

    def test_caps_content_at_400_chars(self):
        from tools.genesis.reflexes.episodic_distiller import _distill_cluster_heuristic
        long_content = "A" * 500
        cluster = [{"id": 1, "content": long_content, "importance": 7}]
        facts = _distill_cluster_heuristic(cluster, max_facts=1)
        if facts:
            assert len(facts[0]) <= 400


# ---------------------------------------------------------------------------
# Unit: run() threshold skip
# ---------------------------------------------------------------------------


class TestRunThresholdSkip:
    def test_skips_when_below_trigger_count(self, mem_db):
        from tools.genesis.reflexes.episodic_distiller import run

        _seed_episodic(mem_db, 5)  # only 5 — below default 20

        with patch("tools.genesis.reflexes.episodic_distiller.get_connection") as mock_conn_fn:
            # Simulate 5 entries returned
            mock_conn = MagicMock()
            mock_conn_fn.return_value = mock_conn
            mock_cursor = MagicMock()
            mock_cursor.fetchall.return_value = []  # simulate empty (less than trigger)
            mock_conn.execute.return_value = mock_cursor
            mock_conn.close.return_value = None

            config = {"trigger_count": 20, "batch_size": 50, "use_llm": False}
            result = run(config, trust=None)

        assert result["success"] is True
        assert result["details"].get("skipped") is True

    def test_processes_when_at_trigger_count(self):
        """When entries >= trigger_count, run() attempts distillation."""
        from tools.genesis.reflexes.episodic_distiller import run

        fake_entries = [
            {"id": i, "content": f"episodic event number {i} about agent loop workflows", "importance": 5, "type": "event"}
            for i in range(25)
        ]

        with patch("tools.genesis.reflexes.episodic_distiller._fetch_undistilled_episodic", return_value=fake_entries):
            with patch("tools.genesis.reflexes.episodic_distiller._mark_distilled"):
                with patch("tools.genesis.reflexes.episodic_distiller._save_semantic_fact") as mock_save:
                    mock_save.return_value = {"id": 1, "status": "inserted", "fingerprint": "abc"}
                    config = {"trigger_count": 20, "batch_size": 50, "use_llm": False}
                    result = run(config, trust=None)

        assert result["success"] is True
        assert result["details"].get("skipped") is not True
        assert result["details"]["entries_processed"] == 25


# ---------------------------------------------------------------------------
# Unit: run() heuristic path (no LLM)
# ---------------------------------------------------------------------------


class TestRunHeuristicPath:
    def test_run_heuristic_writes_facts(self):
        from tools.genesis.reflexes.episodic_distiller import run

        fake_entries = [
            {"id": i, "content": f"The ICDEV system completed agent task {i} successfully", "importance": 6, "type": "event"}
            for i in range(20)
        ]
        saved_facts = []

        def fake_save(content, importance=6, source="distiller"):
            saved_facts.append(content)
            return {"id": len(saved_facts), "status": "inserted", "fingerprint": "x"}

        with patch("tools.genesis.reflexes.episodic_distiller._fetch_undistilled_episodic", return_value=fake_entries):
            with patch("tools.genesis.reflexes.episodic_distiller._mark_distilled"):
                with patch("tools.genesis.reflexes.episodic_distiller._save_semantic_fact", side_effect=fake_save):
                    config = {"trigger_count": 5, "batch_size": 50, "use_llm": False, "max_facts_per_cluster": 2}
                    result = run(config, trust=None)

        assert result["success"] is True
        assert result["metric_value"] >= 0
        assert len(saved_facts) >= 1

    def test_run_marks_source_entries_distilled(self):
        from tools.genesis.reflexes.episodic_distiller import run

        fake_entries = [
            {"id": i + 1, "content": f"Memory event about ICDEV system state {i}", "importance": 5, "type": "event"}
            for i in range(20)
        ]
        marked = []

        def fake_mark(ids):
            marked.extend(ids)

        with patch("tools.genesis.reflexes.episodic_distiller._fetch_undistilled_episodic", return_value=fake_entries):
            with patch("tools.genesis.reflexes.episodic_distiller._mark_distilled", side_effect=fake_mark):
                with patch("tools.genesis.reflexes.episodic_distiller._save_semantic_fact", return_value={"id": 1, "status": "inserted", "fingerprint": "x"}):
                    config = {"trigger_count": 5, "batch_size": 50, "use_llm": False}
                    run(config, trust=None)

        assert len(marked) == 20
        assert sorted(marked) == sorted(e["id"] for e in fake_entries)


# ---------------------------------------------------------------------------
# Unit: LLM distillation (mocked)
# ---------------------------------------------------------------------------


class TestLLMDistillation:
    def test_distill_cluster_with_llm_parses_json_array(self):
        from tools.genesis.reflexes.episodic_distiller import _distill_cluster_with_llm

        mock_response = MagicMock()
        mock_response.content = json.dumps([
            "ICDEV uses PostgreSQL as its primary backend.",
            "Agent loop sessions are persisted to agent_loop_sessions.",
        ])

        cluster = [
            {"id": 1, "content": "ICDEV uses PostgreSQL for all production data", "importance": 7},
            {"id": 2, "content": "The agent loop writes sessions to agent_loop_sessions table", "importance": 6},
        ]

        # LLMRouter is imported inside the function; patch at its source module
        with patch("tools.llm.router.LLMRouter") as MockRouter:
            with patch("tools.llm.router.LLMRequest", MagicMock()):
                instance = MockRouter.return_value
                instance.invoke.return_value = mock_response
                facts = _distill_cluster_with_llm(cluster, "summarization", max_facts=3)

        assert len(facts) == 2
        assert any("PostgreSQL" in f for f in facts)

    def test_distill_cluster_llm_error_returns_empty(self):
        from tools.genesis.reflexes.episodic_distiller import _distill_cluster_with_llm

        cluster = [{"id": 1, "content": "some content", "importance": 5}]
        # Make the import inside the function raise
        with patch.dict("sys.modules", {"tools.llm.router": None}):
            facts = _distill_cluster_with_llm(cluster, "summarization")
        assert facts == []

    def test_distill_cluster_llm_bad_json_returns_empty(self):
        from tools.genesis.reflexes.episodic_distiller import _distill_cluster_with_llm

        mock_response = MagicMock()
        mock_response.content = "not valid json at all"

        cluster = [{"id": 1, "content": "some content about systems", "importance": 5}]
        with patch("tools.llm.router.LLMRouter") as MockRouter:
            with patch("tools.llm.router.LLMRequest", MagicMock()):
                instance = MockRouter.return_value
                instance.invoke.return_value = mock_response
                facts = _distill_cluster_with_llm(cluster, "summarization")
        assert facts == []


# ---------------------------------------------------------------------------
# Integration: daemon and genesis_config registration
# ---------------------------------------------------------------------------


class TestRegistration:
    def test_episodic_distiller_in_daemon_reflex_names(self):
        from tools.genesis.daemon import REFLEX_NAMES
        assert "episodic_distiller" in REFLEX_NAMES

    def test_reflex_module_has_run_function(self):
        from tools.genesis.reflexes.episodic_distiller import run
        import inspect
        sig = inspect.signature(run)
        assert "config" in sig.parameters
        assert "trust" in sig.parameters

    def test_genesis_config_has_episodic_distiller(self):
        import yaml
        cfg_path = BASE_DIR / "args" / "genesis_config.yaml"
        with open(cfg_path, encoding="utf-8") as f:
            cfg = yaml.safe_load(f)
        reflexes = cfg.get("reflexes", {})
        assert "episodic_distiller" in reflexes, "genesis_config.yaml must have episodic_distiller reflex entry"
        distiller_cfg = reflexes["episodic_distiller"]
        assert "trigger_count" in distiller_cfg
        assert "use_llm" in distiller_cfg

    def test_reflex_file_exists(self):
        path = BASE_DIR / "tools" / "genesis" / "reflexes" / "episodic_distiller.py"
        assert path.exists()
