"""CoWorkerRAG tests (cwk-rag-01)."""
import os
import sqlite3
from pathlib import Path

import pytest

from icdev.tools.coworkers.rag import CoWorkerRAG


@pytest.fixture
def rag_db(tmp_path, monkeypatch):
    """Temp SQLite DB with a known table for generic RAG tests."""
    db_path = tmp_path / "icdev.db"
    monkeypatch.setenv("ICDEV_DB_PATH", str(db_path))
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS demo_docs (
            id INTEGER PRIMARY KEY,
            title TEXT NOT NULL,
            description TEXT,
            category TEXT
        );
        INSERT INTO demo_docs (id, title, description, category) VALUES
            (1, 'Alpha Strategy', 'A comprehensive plan for alpha operations in sector 7.', 'military'),
            (2, 'Beta Logistics', 'Supply chain details for beta theater deployment.', 'logistics'),
            (3, 'Gamma Intel', 'Intelligence briefing on gamma faction movements.', 'intel');
    """)
    conn.commit()
    conn.close()
    yield db_path


class TestCoWorkerRAGGeneric:
    def test_retrieve_returns_cited_rows(self, rag_db):
        rag = CoWorkerRAG(tables=["demo_docs"])
        results = rag.retrieve("alpha operations sector", top_k=2)
        assert len(results) >= 1
        # First result should be the alpha row
        assert any("Alpha Strategy" in r["content"] for r in results)
        assert all("source_type" in r for r in results)
        assert all(r["source_type"] == "demo_docs" for r in results)

    def test_retrieve_score_present(self, rag_db):
        rag = CoWorkerRAG(tables=["demo_docs"])
        results = rag.retrieve("logistics supply chain", top_k=1)
        assert results
        assert "score" in results[0]
        assert results[0]["score"] == 0.6

    def test_retrieve_empty_terms(self, rag_db):
        rag = CoWorkerRAG(tables=["demo_docs"])
        results = rag.retrieve("a", top_k=5)
        assert results == []

    def test_retrieve_no_matching_table(self, rag_db):
        rag = CoWorkerRAG(tables=["nonexistent_table"])
        results = rag.retrieve("alpha", top_k=5)
        # Should gracefully return empty because table doesn't exist
        assert results == []

    def test_retrieve_no_text_columns(self, rag_db):
        conn = sqlite3.connect(str(rag_db))
        conn.executescript("""
            CREATE TABLE numeric_only (
                id INTEGER PRIMARY KEY,
                val REAL,
                count INTEGER
            );
            INSERT INTO numeric_only VALUES (1, 3.14, 42);
        """)
        conn.commit()
        conn.close()
        rag = CoWorkerRAG(tables=["numeric_only"])
        results = rag.retrieve("alpha", top_k=5)
        assert results == []


class TestCoWorkerRAGBespoke:
    def test_bespoke_mode_delegates_to_strategos(self, monkeypatch):
        """When mode=bespoke + coworker_id=strategos, delegate retrieve is called."""
        class FakeRAG:
            def retrieve(self, query, top_k):
                return [{"content": "delegated", "source_type": "strategos", "score": 0.99}]

        # Patch the delegate registry so we don't need the real StrategosRAG
        import icdev.tools.coworkers.rag as _rag_mod
        original = _rag_mod._BESPOKE_DELEGATES.get("strategos")
        _rag_mod._BESPOKE_DELEGATES["strategos"] = "tests.test_coworkers_rag.FakeStrategosRAG"

        try:
            # We need FakeStrategosRAG importable; inject it into sys.modules
            import sys
            sys.modules["tests.test_coworkers_rag"] = sys.modules[__name__]

            rag = CoWorkerRAG(mode="bespoke", coworker_id="strategos")
            results = rag.retrieve("any query", top_k=5)
            assert len(results) == 1
            assert results[0]["content"] == "delegated"
            assert results[0]["source_type"] == "strategos"
        finally:
            if original:
                _rag_mod._BESPOKE_DELEGATES["strategos"] = original
            else:
                del _rag_mod._BESPOKE_DELEGATES["strategos"]

    def test_bespoke_missing_delegate_falls_back_to_generic(self, rag_db):
        rag = CoWorkerRAG(tables=["demo_docs"], mode="bespoke", coworker_id="unknown")
        results = rag.retrieve("alpha operations", top_k=2)
        assert any("Alpha Strategy" in r["content"] for r in results)

    def test_generic_mode_ignores_delegate(self, rag_db):
        rag = CoWorkerRAG(tables=["demo_docs"], mode="generic", coworker_id="strategos")
        results = rag.retrieve("alpha", top_k=2)
        assert any("Alpha Strategy" in r["content"] for r in results)


class FakeStrategosRAG:
    def retrieve(self, query, top_k):
        return [{"content": "delegated", "source_type": "strategos", "score": 0.99}]
