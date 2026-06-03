# CUI // SP-CTI
"""Tests for AADC agent_layer — WebSearcher, TextChunker, ResearchAgent,
and the /run-agent blueprint route.

All network and RAG calls are mocked so the suite runs offline.
"""

import json
import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.agentic_ai_canvas.agent_layer import (
    ResearchAgent,
    ResearchResult,
    SearchHit,
    TextChunker,
    WebSearcher,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _hit(title="Title", url="https://example.com", snippet="Some text.", score=0.9):
    return SearchHit(title=title, url=url, snippet=snippet, score=score)


# ---------------------------------------------------------------------------
# WebSearcher tests
# ---------------------------------------------------------------------------

class TestWebSearcher(unittest.TestCase):

    def test_tavily_success(self):
        fake_resp = MagicMock()
        fake_resp.json.return_value = {
            "results": [
                {"title": "Doc A", "url": "https://a.com", "content": "FedRAMP details", "score": 0.95},
                {"title": "Doc B", "url": "https://b.com", "content": "AC-2 overview",  "score": 0.80},
            ]
        }
        fake_resp.raise_for_status = MagicMock()

        with patch.dict("os.environ", {"TAVILY_API_KEY": "test-key"}):
            with patch("requests.post", return_value=fake_resp):
                ws = WebSearcher(max_results=5)
                hits = ws.search("FedRAMP AC-2")

        self.assertEqual(len(hits), 2)
        self.assertEqual(hits[0].title, "Doc A")
        self.assertAlmostEqual(hits[0].score, 0.95)

    def test_tavily_not_called_without_key(self):
        with patch.dict("os.environ", {}, clear=True):
            with patch("requests.post") as mock_post:
                ws = WebSearcher()
                ws._try_tavily("query")
        mock_post.assert_not_called()

    def test_serpapi_success(self):
        fake_resp = MagicMock()
        fake_resp.json.return_value = {
            "organic_results": [
                {"title": "Result 1", "link": "https://r1.com", "snippet": "Snippet 1"},
            ]
        }
        fake_resp.raise_for_status = MagicMock()

        with patch.dict("os.environ", {"SERP_API_KEY": "serp-key", "TAVILY_API_KEY": ""}):
            with patch("requests.get", return_value=fake_resp):
                ws = WebSearcher()
                hits = ws._try_serpapi("CMMC level 2")

        self.assertEqual(len(hits), 1)
        self.assertEqual(hits[0].url, "https://r1.com")

    def test_search_degrades_when_all_backends_fail(self):
        ws = WebSearcher()
        ws._try_tavily = MagicMock(return_value=None)
        ws._try_serpapi = MagicMock(return_value=None)
        ws._try_duckduckgo = MagicMock(return_value=None)
        hits = ws.search("anything")
        self.assertEqual(hits, [])

    def test_search_respects_max_results(self):
        many_hits = [_hit(title=f"H{i}") for i in range(20)]
        ws = WebSearcher(max_results=3)
        ws._try_tavily = MagicMock(return_value=many_hits)
        hits = ws.search("query")
        self.assertEqual(len(hits), 3)

    def test_tavily_network_error_returns_none(self):
        with patch.dict("os.environ", {"TAVILY_API_KEY": "key"}):
            with patch("requests.post", side_effect=ConnectionError("offline")):
                ws = WebSearcher()
                result = ws._try_tavily("query")
        self.assertIsNone(result)


# ---------------------------------------------------------------------------
# TextChunker tests
# ---------------------------------------------------------------------------

class TestTextChunker(unittest.TestCase):

    def test_chunk_uses_rag_chunker(self):
        fake_chunk = MagicMock()
        fake_chunk.content = "RAG chunk content"
        with patch("tools.rag.chunker.chunk_content", return_value=[fake_chunk]):
            tc = TextChunker()
            chunks = tc.chunk([_hit(snippet="Some text here")])
        self.assertIn("RAG chunk content", chunks)

    def test_chunk_fallback_on_import_error(self):
        with patch("tools.rag.chunker.chunk_content", side_effect=ImportError("no rag")):
            tc = TextChunker(max_chunk_size=50, overlap=5)
            chunks = tc.chunk([_hit(snippet="Line one.\nLine two.\nLine three.")])
        self.assertGreater(len(chunks), 0)
        self.assertTrue(all(isinstance(c, str) for c in chunks))

    def test_chunk_empty_hits(self):
        tc = TextChunker()
        self.assertEqual(tc.chunk([]), [])

    def test_chunk_strips_empty_snippets(self):
        tc = TextChunker()
        with patch("tools.rag.chunker.chunk_content", side_effect=ImportError):
            result = tc.chunk([_hit(snippet="   "), _hit(snippet="real content")])
        self.assertTrue(all(c.strip() for c in result))

    def test_simple_chunk_overlap(self):
        tc = TextChunker(max_chunk_size=6, overlap=2)
        # Each word is 5 chars; combined they exceed max_chunk_size forcing splits
        text = "AAAAA\nBBBBB\nCCCCC\nDDDDD"
        chunks = tc._simple_chunk(text)
        self.assertGreater(len(chunks), 1)


# ---------------------------------------------------------------------------
# ResearchAgent tests
# ---------------------------------------------------------------------------

class TestResearchAgent(unittest.TestCase):

    def _mock_agent(self, hits=None, chunks=None):
        agent = ResearchAgent(max_results=5)
        agent.searcher.search = MagicMock(
            return_value=hits if hits is not None else [_hit()]
        )
        agent.chunker.chunk = MagicMock(
            return_value=chunks if chunks is not None else ["chunk one", "chunk two"]
        )
        return agent

    def test_search_success(self):
        agent = self._mock_agent()
        result = agent.search("What is NIST RMF?")
        self.assertIsInstance(result, ResearchResult)
        self.assertEqual(result.query, "What is NIST RMF?")
        self.assertEqual(result.chunks, ["chunk one", "chunk two"])
        self.assertEqual(len(result.sources), 1)
        self.assertEqual(result.error, "")
        self.assertGreaterEqual(result.duration_ms, 0)

    def test_search_empty_hits(self):
        agent = self._mock_agent(hits=[], chunks=[])
        result = agent.search("obscure query")
        self.assertEqual(result.chunks, [])
        self.assertEqual(result.sources, [])
        self.assertEqual(result.error, "")

    def test_search_degrades_on_exception(self):
        agent = ResearchAgent()
        agent.searcher.search = MagicMock(side_effect=RuntimeError("network error"))
        result = agent.search("query")
        self.assertEqual(result.chunks, [])
        self.assertIn("network error", result.error)

    def test_searcher_called_with_query(self):
        agent = self._mock_agent()
        agent.search("test query")
        agent.searcher.search.assert_called_once_with("test query")

    def test_chunker_called_with_hits(self):
        hits = [_hit(title="H1"), _hit(title="H2")]
        agent = self._mock_agent(hits=hits)
        agent.search("test")
        agent.chunker.chunk.assert_called_once_with(hits)


# ---------------------------------------------------------------------------
# Blueprint /run-agent route tests
# ---------------------------------------------------------------------------

class TestRunAgentRoute(unittest.TestCase):

    def setUp(self):
        import flask
        self.app = flask.Flask(__name__)
        from tools.agentic_ai_canvas.blueprint import aadc_bp
        self.app.register_blueprint(aadc_bp)
        self.client = self.app.test_client()

        # Mock DB lookup
        self._conn_patch = patch("tools.agentic_ai_canvas.blueprint._conn")
        self._mock_conn = self._conn_patch.start()
        mock_conn = MagicMock()
        mock_conn.close = MagicMock()
        mock_conn.execute.return_value.fetchone.return_value = {"id": "d1", "name": "Research Pipeline"}
        self._mock_conn.return_value = mock_conn

        self._init_patch = patch("tools.agentic_ai_canvas.blueprint._ensure_init")
        self._init_patch.start()

    def tearDown(self):
        self._conn_patch.stop()
        self._init_patch.stop()

    def _fake_research_result(self):
        return ResearchResult(
            query="What is FedRAMP?",
            chunks=["FedRAMP overview chunk", "Authorization process chunk"],
            sources=[_hit(title="FedRAMP Docs", url="https://fedramp.gov", snippet="FedRAMP overview")],
            duration_ms=123.4,
        )

    def _fake_pipeline_result(self):
        from tools.agentic_ai_canvas.model_layer import PipelineResult, RankedChunk
        return PipelineResult(
            query="What is FedRAMP?",
            answer="FedRAMP is a government-wide program.",
            chunks_used=2,
            model="claude-sonnet-4-6",
            confidence=0.88,
            embed_model="nomic-embed-text",
            top_chunks=[RankedChunk(content="FedRAMP overview chunk", score=0.9)],
            error="",
        )

    def test_run_agent_success(self):
        with patch("tools.agentic_ai_canvas.agent_layer.ResearchAgent.search",
                   return_value=self._fake_research_result()):
            with patch("tools.agentic_ai_canvas.model_layer.AgenticResearchPipeline.run",
                       return_value=self._fake_pipeline_result()):
                resp = self.client.post(
                    "/agentic-ai/api/designs/d1/run-agent",
                    json={"query": "What is FedRAMP?"},
                )
        self.assertEqual(resp.status_code, 200)
        body = json.loads(resp.data)
        self.assertEqual(body["query"], "What is FedRAMP?")
        self.assertIn("answer", body)
        self.assertIn("sources", body)
        self.assertIn("agent_duration_ms", body)
        self.assertEqual(body["design_id"], "d1")

    def test_run_agent_missing_query(self):
        resp = self.client.post(
            "/agentic-ai/api/designs/d1/run-agent",
            json={},
        )
        self.assertEqual(resp.status_code, 400)
        self.assertIn("error", json.loads(resp.data))

    def test_run_agent_design_not_found(self):
        self._mock_conn.return_value.execute.return_value.fetchone.return_value = None
        resp = self.client.post(
            "/agentic-ai/api/designs/missing/run-agent",
            json={"query": "test"},
        )
        self.assertEqual(resp.status_code, 404)

    def test_run_agent_sources_in_response(self):
        with patch("tools.agentic_ai_canvas.agent_layer.ResearchAgent.search",
                   return_value=self._fake_research_result()):
            with patch("tools.agentic_ai_canvas.model_layer.AgenticResearchPipeline.run",
                       return_value=self._fake_pipeline_result()):
                resp = self.client.post(
                    "/agentic-ai/api/designs/d1/run-agent",
                    json={"query": "What is FedRAMP?"},
                )
        body = json.loads(resp.data)
        self.assertEqual(len(body["sources"]), 1)
        self.assertEqual(body["sources"][0]["title"], "FedRAMP Docs")


if __name__ == "__main__":
    unittest.main()
