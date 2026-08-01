# CUI // SP-CTI
"""Tests for AADC model_layer — Embedder, ReRanker, SynthesisLLM,
AgenticResearchPipeline, and the /run-pipeline blueprint route.

All LLM and RAG provider calls are mocked so the suite runs offline.
"""

import json
import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

# Ensure repo root on sys.path (conftest also does this; belt-and-suspenders)
ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.agentic_ai_canvas.model_layer import (
    AgenticResearchPipeline,
    Embedder,
    EmbedResult,
    PipelineResult,
    RankedChunk,
    ReRanker,
    SynthesisLLM,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _fake_embed_provider(vector=None):
    p = MagicMock()
    p.embed.return_value = vector or [0.1, 0.2, 0.3]
    p.model_id = "nomic-embed-test"
    return p


def _fake_search_result(content, score=0.8):
    from tools.rag.vector_store_provider import SearchResult
    return SearchResult(content=content, score=score, source_id="test")


# ---------------------------------------------------------------------------
# Embedder tests
# ---------------------------------------------------------------------------

class TestEmbedder(unittest.TestCase):

    def test_embed_returns_vector(self):
        with patch("tools.llm.get_embedding_provider", return_value=_fake_embed_provider([0.5, 0.6])):
            emb = Embedder()
            result = emb.embed("FedRAMP AC-2")
        self.assertIsInstance(result, EmbedResult)
        self.assertEqual(result.query, "FedRAMP AC-2")
        self.assertEqual(result.vector, [0.5, 0.6])
        self.assertEqual(result.model, "nomic-embed-test")

    def test_embed_degrades_on_provider_failure(self):
        with patch("tools.llm.get_embedding_provider", side_effect=ImportError("no provider")):
            emb = Embedder()
            result = emb.embed("anything")
        self.assertEqual(result.vector, [])
        self.assertEqual(result.model, "none")

    def test_embed_degrades_on_embed_error(self):
        provider = MagicMock()
        provider.embed.side_effect = RuntimeError("timeout")
        with patch("tools.llm.get_embedding_provider", return_value=provider):
            emb = Embedder()
            result = emb.embed("query")
        self.assertEqual(result.vector, [])
        self.assertEqual(result.model, "error")


# ---------------------------------------------------------------------------
# ReRanker tests
# ---------------------------------------------------------------------------

class TestReRanker(unittest.TestCase):

    def test_rerank_returns_ranked_chunks(self):
        fake_results = [
            _fake_search_result("Chunk A", 0.9),
            _fake_search_result("Chunk B", 0.7),
            _fake_search_result("Chunk C", 0.5),
        ]
        # Simulate rerank_results returning the list sorted by rerank score
        for i, r in enumerate(fake_results):
            r.rerank_score = 1.0 - i * 0.1
            r.final_score = r.score

        with patch("tools.rag.reranker.rerank_results", return_value=fake_results[:2]):
            rr = ReRanker(top_k=2)
            ranked = rr.rerank("test query", ["Chunk A", "Chunk B", "Chunk C"])

        self.assertEqual(len(ranked), 2)
        self.assertIsInstance(ranked[0], RankedChunk)

    def test_rerank_empty_chunks(self):
        rr = ReRanker(top_k=5)
        ranked = rr.rerank("query", [])
        self.assertEqual(ranked, [])

    def test_rerank_degrades_on_error(self):
        with patch("tools.rag.reranker.rerank_results", side_effect=RuntimeError("offline")):
            rr = ReRanker(top_k=3)
            ranked = rr.rerank("query", ["A", "B", "C", "D"])
        # Falls back to top-k unranked
        self.assertLessEqual(len(ranked), 3)
        self.assertEqual(ranked[0].content, "A")

    def test_rerank_fewer_than_top_k(self):
        with patch("tools.rag.reranker.rerank_results", return_value=[_fake_search_result("only one")]):
            rr = ReRanker(top_k=5)
            ranked = rr.rerank("query", ["only one"])
        self.assertEqual(len(ranked), 1)


# ---------------------------------------------------------------------------
# SynthesisLLM tests
# ---------------------------------------------------------------------------

class TestSynthesisLLM(unittest.TestCase):

    def _make_chunks(self, n=3):
        return [RankedChunk(content=f"Context chunk {i}", score=1.0 / (i + 1)) for i in range(n)]

    def test_synthesize_success(self):
        mock_resp = MagicMock()
        mock_resp.content = "  The answer is 42.  "
        mock_resp.model = "claude-sonnet-4-6"
        mock_resp.confidence = 0.85

        mock_router = MagicMock()
        mock_router.invoke.return_value = mock_resp

        with patch("tools.llm.router.LLMRouter", return_value=mock_router):
            slm = SynthesisLLM()
            result = slm.synthesize("What is the answer?", self._make_chunks())

        self.assertEqual(result.answer, "The answer is 42.")
        self.assertEqual(result.model, "claude-sonnet-4-6")
        self.assertEqual(result.chunks_used, 3)
        self.assertAlmostEqual(result.confidence, 0.85)
        self.assertEqual(result.error, "")

    def test_synthesize_no_chunks(self):
        slm = SynthesisLLM()
        result = slm.synthesize("query", [])
        self.assertIn("No context", result.answer)
        self.assertEqual(result.chunks_used, 0)

    def test_synthesize_degrades_on_llm_error(self):
        mock_router = MagicMock()
        mock_router.invoke.side_effect = RuntimeError("LLM unavailable")

        with patch("tools.llm.router.LLMRouter", return_value=mock_router):
            slm = SynthesisLLM()
            result = slm.synthesize("query", self._make_chunks())

        self.assertEqual(result.answer, "")
        self.assertIn("LLM unavailable", result.error)

    def test_context_truncation(self):
        slm = SynthesisLLM(max_context_chars=50)
        chunks = [RankedChunk(content="x" * 30, score=1.0) for _ in range(5)]
        context = slm._build_context(chunks)
        self.assertLessEqual(len(context), 60)  # small budget


# ---------------------------------------------------------------------------
# AgenticResearchPipeline tests
# ---------------------------------------------------------------------------

class TestAgenticResearchPipeline(unittest.TestCase):

    def _mock_pipeline(self):
        """Return a pipeline with all three components mocked."""
        pipeline = AgenticResearchPipeline(top_k=3)

        pipeline.embedder.embed = MagicMock(
            return_value=EmbedResult(query="q", vector=[0.1], model="mock-embed")
        )
        pipeline.reranker.rerank = MagicMock(
            return_value=[RankedChunk(content="Relevant fact.", score=0.9)]
        )
        mock_synth = MagicMock()
        mock_synth.answer = "Synthesized answer."
        mock_synth.chunks_used = 1
        mock_synth.model = "mock-llm"
        mock_synth.confidence = 0.75
        mock_synth.error = ""
        pipeline.synthesis_llm.synthesize = MagicMock(return_value=mock_synth)
        return pipeline

    def test_run_full_pipeline(self):
        pipeline = self._mock_pipeline()
        result = pipeline.run("What is NIST RMF?", chunks=["Some context chunk"])
        self.assertIsInstance(result, PipelineResult)
        self.assertEqual(result.query, "What is NIST RMF?")
        self.assertEqual(result.answer, "Synthesized answer.")
        self.assertEqual(result.model, "mock-llm")
        self.assertEqual(result.embed_model, "mock-embed")
        self.assertEqual(len(result.top_chunks), 1)
        self.assertEqual(result.error, "")

    def test_run_empty_chunks(self):
        pipeline = self._mock_pipeline()
        pipeline.reranker.rerank = MagicMock(return_value=[])
        mock_synth_empty = MagicMock()
        mock_synth_empty.answer = "No context available."
        mock_synth_empty.chunks_used = 0
        mock_synth_empty.model = ""
        mock_synth_empty.confidence = 0.0
        mock_synth_empty.error = ""
        pipeline.synthesis_llm.synthesize = MagicMock(return_value=mock_synth_empty)

        result = pipeline.run("query", chunks=[])
        self.assertEqual(result.answer, "No context available.")
        self.assertEqual(result.chunks_used, 0)

    def test_embedder_called_with_query(self):
        pipeline = self._mock_pipeline()
        pipeline.run("test query", chunks=["c1"])
        pipeline.embedder.embed.assert_called_once_with("test query")

    def test_reranker_called_with_chunks(self):
        pipeline = self._mock_pipeline()
        pipeline.run("test query", chunks=["c1", "c2"])
        pipeline.reranker.rerank.assert_called_once_with("test query", ["c1", "c2"])

    def test_synthesis_called_with_ranked_chunks(self):
        pipeline = self._mock_pipeline()
        pipeline.run("q", chunks=["c1"])
        ranked = pipeline.reranker.rerank.return_value
        pipeline.synthesis_llm.synthesize.assert_called_once_with("q", ranked)


# ---------------------------------------------------------------------------
# Blueprint /run-pipeline route tests
# ---------------------------------------------------------------------------

class TestRunPipelineRoute(unittest.TestCase):

    def setUp(self):
        # Minimal Flask test client setup
        import flask
        self.app = flask.Flask(__name__)

        # Stand in for the platform auth hook. /run-pipeline is a POST, so
        # aadc_bp's _aadc_auth_guard (penta-aadc-01) requires an authenticated
        # user and 401s otherwise. Production resolves that user from
        # g.current_user, populated by tools/dashboard/auth.py's app-level
        # before_request; Flask runs app-level hooks ahead of blueprint ones,
        # so setting it here reproduces that ordering without a user DB.
        @self.app.before_request
        def _fake_platform_auth():
            flask.g.current_user = {"id": "test-admin", "status": "active"}

        # Register blueprint
        from tools.agentic_ai_canvas.blueprint import aadc_bp
        self.app.register_blueprint(aadc_bp)
        self.client = self.app.test_client()

        # Mock DB lookup so we don't need a real DB
        self._conn_patch = patch(
            "tools.agentic_ai_canvas.blueprint._conn"
        )
        self._mock_conn = self._conn_patch.start()
        mock_conn = MagicMock()
        mock_conn.__enter__ = MagicMock(return_value=mock_conn)
        mock_conn.__exit__ = MagicMock(return_value=False)
        mock_conn.close = MagicMock()

        # fetchone returns a design row
        mock_row = {"id": "d1", "name": "Test Design"}
        mock_conn.execute.return_value.fetchone.return_value = mock_row
        self._mock_conn.return_value = mock_conn

        # Mock init
        self._init_patch = patch("tools.agentic_ai_canvas.blueprint._ensure_init")
        self._init_patch.start()

    def tearDown(self):
        self._conn_patch.stop()
        self._init_patch.stop()

    def _mock_pipeline_result(self):
        from tools.agentic_ai_canvas.model_layer import PipelineResult, RankedChunk
        return PipelineResult(
            query="What is CMMC?",
            answer="CMMC is the Cybersecurity Maturity Model Certification.",
            chunks_used=2,
            model="claude-sonnet-4-6",
            confidence=0.8,
            embed_model="nomic-embed-text",
            top_chunks=[RankedChunk(content="CMMC overview...", score=0.9)],
            error="",
        )

    def test_run_pipeline_success(self):
        with patch(
            "tools.agentic_ai_canvas.model_layer.AgenticResearchPipeline.run",
            return_value=self._mock_pipeline_result(),
        ):
            resp = self.client.post(
                "/agentic-ai/api/designs/d1/run-pipeline",
                json={"query": "What is CMMC?", "chunks": ["CMMC overview..."]},
            )
        self.assertEqual(resp.status_code, 200)
        body = json.loads(resp.data)
        self.assertEqual(body["query"], "What is CMMC?")
        self.assertIn("answer", body)
        self.assertEqual(body["design_id"], "d1")

    def test_run_pipeline_missing_query(self):
        resp = self.client.post(
            "/agentic-ai/api/designs/d1/run-pipeline",
            json={"chunks": ["some text"]},
        )
        self.assertEqual(resp.status_code, 400)
        body = json.loads(resp.data)
        self.assertIn("error", body)

    def test_run_pipeline_design_not_found(self):
        self._mock_conn.return_value.execute.return_value.fetchone.return_value = None
        resp = self.client.post(
            "/agentic-ai/api/designs/nonexistent/run-pipeline",
            json={"query": "test"},
        )
        self.assertEqual(resp.status_code, 404)


if __name__ == "__main__":
    unittest.main()
