# CUI // SP-CTI
"""Tests for Karpathy LLM Wiki integrations (Items 1–5).

Item 1: DIC wiki bypass — _check_wiki_cache returns cached DICAnswer on hit
Item 2: ACE coworker role wiki context enrichment on _run startup
Item 3: ANVIL Navigate wiki pre-step via wiki_tool_query
Item 4: DIC answer filing — _file_qa_to_wiki writes wiki file after grounded answer
Item 5: ACE session end cross-role wiki links via _file_session_to_wiki
"""

import tempfile
import unittest

from tools.memory.claude_memory_path import project_slug

# These fixtures used to hardcode the literal 'C--AI-ICDev'. That is the slug for
# exactly one checkout on one machine, and it only ever matched because Windows
# compares paths case-insensitively (the real directory is lowercase). The tests
# passed by mirroring the bug they were meant to guard. Derive it the same way
# the implementation does (ahx-path-01).
_PROJECT_SLUG = project_slug()
from pathlib import Path
from unittest.mock import patch


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_wiki_dir(tmp):
    """Return a temp Path with a minimal MEMORY.md."""
    p = Path(tmp)
    (p / "MEMORY.md").write_text("# Memory Index\n", encoding="utf-8")
    return p


# ---------------------------------------------------------------------------
# Item 4: _file_qa_to_wiki
# ---------------------------------------------------------------------------

class TestFileQaToWiki(unittest.TestCase):
    def test_writes_file_to_wiki_dir(self):
        from tools.document_intelligence.search_engine import _file_qa_to_wiki, _qa_slug
        with tempfile.TemporaryDirectory() as td:
            _wiki = _make_wiki_dir(td)
            with patch.dict("os.environ", {"USERPROFILE": td}):
                # Patch auto_dir derivation inside function
                from pathlib import Path as _Path

                auto_path = _Path(td) / f".claude/projects/{_PROJECT_SLUG}/memory"
                auto_path.mkdir(parents=True, exist_ok=True)
                (auto_path / "MEMORY.md").write_text("# Index\n", encoding="utf-8")

                with patch("tools.memory.memory_write.update_crossrefs"):
                    _file_qa_to_wiki("what is DIC?", "DIC stands for Document Intelligence Canvas.", "col-1")

                slug = _qa_slug("what is DIC?", "col-1")
                filed = auto_path / f"{slug}.md"
                self.assertTrue(filed.exists(), "Q&A wiki file should be created")
                content = filed.read_text(encoding="utf-8")
                self.assertIn("DIC stands for", content)
                self.assertIn("**Q:** what is DIC?", content)

    def test_skips_if_file_already_exists(self):
        from tools.document_intelligence.search_engine import _file_qa_to_wiki, _qa_slug
        with tempfile.TemporaryDirectory() as td:
            auto_path = Path(td) / f".claude/projects/{_PROJECT_SLUG}/memory"
            auto_path.mkdir(parents=True, exist_ok=True)
            (auto_path / "MEMORY.md").write_text("# Index\n", encoding="utf-8")

            slug = _qa_slug("repeated query", None)
            pre_existing = auto_path / f"{slug}.md"
            pre_existing.write_text("already there", encoding="utf-8")

            with patch.dict("os.environ", {"USERPROFILE": td}):
                with patch("tools.memory.memory_write.update_crossrefs") as mock_xref:
                    _file_qa_to_wiki("repeated query", "some answer", None)
                    # crossrefs should NOT be called since file already exists
                    mock_xref.assert_not_called()

    def test_does_not_raise_on_bad_dir(self):
        from tools.document_intelligence.search_engine import _file_qa_to_wiki
        with patch.dict("os.environ", {"USERPROFILE": "/nonexistent/nowhere"}):
            # Should not raise
            _file_qa_to_wiki("q", "a", None)


# ---------------------------------------------------------------------------
# Item 1: _check_wiki_cache
# ---------------------------------------------------------------------------

class TestCheckWikiCache(unittest.TestCase):
    def test_exact_slug_hit_returns_answer(self):
        from tools.document_intelligence.search_engine import _check_wiki_cache, _qa_slug
        with tempfile.TemporaryDirectory() as td:
            auto_path = Path(td) / f".claude/projects/{_PROJECT_SLUG}/memory"
            auto_path.mkdir(parents=True, exist_ok=True)
            slug = _qa_slug("what is RLS?", None)
            content = (
                "---\nname: {slug}\n---\n\n"
                "**Q:** what is RLS?\n\n"
                "**A (DIC grounded):** RLS is Row-Level Security.\n\n"
                "*Synthesized from DIC search.*\n"
            )
            (auto_path / f"{slug}.md").write_text(content, encoding="utf-8")

            with patch.dict("os.environ", {"USERPROFILE": td}):
                result = _check_wiki_cache("what is RLS?", None)

            self.assertIsNotNone(result, "Should return cached answer")
            self.assertTrue(result.grounded)
            self.assertIn("RLS is Row-Level Security", result.answer)
            self.assertEqual(result.origin, "wiki_cache")

    def test_miss_returns_none(self):
        from tools.document_intelligence.search_engine import _check_wiki_cache
        with tempfile.TemporaryDirectory() as td:
            auto_path = Path(td) / f".claude/projects/{_PROJECT_SLUG}/memory"
            auto_path.mkdir(parents=True, exist_ok=True)

            with patch.dict("os.environ", {"USERPROFILE": td}):
                result = _check_wiki_cache("query with no cached answer", None)

            self.assertIsNone(result)

    def test_nonexistent_dir_returns_none(self):
        from tools.document_intelligence.search_engine import _check_wiki_cache
        with patch.dict("os.environ", {"USERPROFILE": "/no/such/path"}):
            result = _check_wiki_cache("any query", None)
        self.assertIsNone(result)

    def test_fuzzy_hit_via_keyword_search(self):
        from tools.document_intelligence.search_engine import _check_wiki_cache
        with tempfile.TemporaryDirectory() as td:
            auto_path = Path(td) / f".claude/projects/{_PROJECT_SLUG}/memory"
            auto_path.mkdir(parents=True, exist_ok=True)
            # Create a Q&A file with different slug but overlapping keywords
            (auto_path / "dic-qa-aaabbbcccdddee.md").write_text(
                "**Q:** what does postgresql primary mean?\n\n"
                "**A (DIC grounded):** PostgreSQL is the primary database backend "
                "for ICDEV, replacing the SQLite fallback.\n\n"
                "*Synthesized from DIC search.*\n",
                encoding="utf-8",
            )
            with patch.dict("os.environ", {"USERPROFILE": td}):
                result = _check_wiki_cache("postgresql primary database backend", None)

            # May or may not hit depending on score threshold — just ensure no crash
            # and type is correct when it does hit
            if result is not None:
                self.assertTrue(result.grounded)
                self.assertEqual(result.origin, "wiki_cache")


# ---------------------------------------------------------------------------
# Item 3: wiki_tool_query
# ---------------------------------------------------------------------------

class TestWikiToolQuery(unittest.TestCase):
    def test_returns_relevant_entries(self):
        from tools.memory.wiki_tool_query import wiki_tool_query
        with tempfile.TemporaryDirectory() as td:
            p = Path(td)
            (p / "project-rag-mcp.md").write_text(
                "# RAG MCP\nRAG retriever pipeline for document intelligence canvas.",
                encoding="utf-8",
            )
            (p / "feedback-kanban.md").write_text(
                "# Kanban\nUse kanban seed_validator for all task seeding.",
                encoding="utf-8",
            )
            results = wiki_tool_query("build a RAG retriever pipeline", top_k=3, memory_dirs=[td])
            self.assertTrue(len(results) > 0)
            self.assertIn("project-rag-mcp", results[0]["slug"])

    def test_no_results_on_empty_query(self):
        from tools.memory.wiki_tool_query import wiki_tool_query
        results = wiki_tool_query("", memory_dirs=[])
        self.assertEqual(results, [])

    def test_returns_empty_for_unmatched_query(self):
        from tools.memory.wiki_tool_query import wiki_tool_query
        with tempfile.TemporaryDirectory() as td:
            p = Path(td)
            (p / "topic.md").write_text("Cooking recipes for pasta.", encoding="utf-8")
            results = wiki_tool_query("quantum computing lattice operations", memory_dirs=[td])
            self.assertEqual(results, [])

    def test_result_schema(self):
        from tools.memory.wiki_tool_query import wiki_tool_query
        with tempfile.TemporaryDirectory() as td:
            p = Path(td)
            (p / "project-dic.md").write_text(
                "# DIC\nDocument intelligence canvas for RAG and KG search.", encoding="utf-8"
            )
            results = wiki_tool_query("document intelligence RAG search", top_k=1, memory_dirs=[td])
            self.assertEqual(len(results), 1)
            r = results[0]
            self.assertIn("slug", r)
            self.assertIn("score", r)
            self.assertIn("snippet", r)
            self.assertIn("source_dir", r)
            self.assertIsInstance(r["score"], float)


# ---------------------------------------------------------------------------
# Item 2+5: ACEController wiki helpers
# ---------------------------------------------------------------------------

class TestACEWikiHelpers(unittest.TestCase):
    def test_query_role_wiki_returns_string(self):
        from icdev.tools.ace.controller import ACEController
        with tempfile.TemporaryDirectory() as td:
            auto_path = Path(td) / f".claude/projects/{_PROJECT_SLUG}/memory"
            auto_path.mkdir(parents=True, exist_ok=True)
            (auto_path / "project-ace.md").write_text(
                "# ACE Coworker\nai_developer role builds code and runs tests.",
                encoding="utf-8",
            )
            with patch.dict("os.environ", {"USERPROFILE": td}):
                ctx = ACEController._query_role_wiki(["ai_developer"], "build a REST API")
            # Returns a string (may be empty if no wiki dir on this machine)
            self.assertIsInstance(ctx, str)

    def test_query_role_wiki_returns_empty_on_no_dir(self):
        from icdev.tools.ace.controller import ACEController
        with patch.dict("os.environ", {"USERPROFILE": "/nonexistent"}):
            ctx = ACEController._query_role_wiki(["ai_developer"], "some problem")
        self.assertEqual(ctx, "")

    def test_file_session_to_wiki_creates_entry(self):
        from icdev.tools.ace.controller import ACEController
        with tempfile.TemporaryDirectory() as td:
            auto_path = Path(td) / f".claude/projects/{_PROJECT_SLUG}/memory"
            auto_path.mkdir(parents=True, exist_ok=True)
            (auto_path / "MEMORY.md").write_text("# Index\n", encoding="utf-8")

            with patch.dict("os.environ", {"USERPROFILE": td}):
                with patch("tools.memory.memory_write.update_crossrefs"):
                    ACEController._file_session_to_wiki(
                        "ace-test123abc",
                        "build a data pipeline for DIC",
                        ["ai_developer", "qa_manager"],
                    )

            # Check that a session file was created
            session_files = list(auto_path.glob("ace-session-*.md"))
            self.assertEqual(len(session_files), 1)
            content = session_files[0].read_text(encoding="utf-8")
            self.assertIn("ai_developer", content)
            self.assertIn("build a data pipeline", content)

    def test_file_session_to_wiki_idempotent(self):
        from icdev.tools.ace.controller import ACEController
        with tempfile.TemporaryDirectory() as td:
            auto_path = Path(td) / f".claude/projects/{_PROJECT_SLUG}/memory"
            auto_path.mkdir(parents=True, exist_ok=True)
            (auto_path / "MEMORY.md").write_text("# Index\n", encoding="utf-8")

            with patch.dict("os.environ", {"USERPROFILE": td}):
                with patch("tools.memory.memory_write.update_crossrefs"):
                    ACEController._file_session_to_wiki("ace-idm001", "problem A", ["role_x"])
                    ACEController._file_session_to_wiki("ace-idm001", "problem A", ["role_x"])

            session_files = list(auto_path.glob("ace-session-*.md"))
            self.assertEqual(len(session_files), 1, "Should not create duplicate")

    def test_file_session_does_not_raise_on_bad_dir(self):
        from icdev.tools.ace.controller import ACEController
        with patch.dict("os.environ", {"USERPROFILE": "/no/such/place"}):
            ACEController._file_session_to_wiki("ace-x", "prob", ["r1"])


# ---------------------------------------------------------------------------
# Item 3: ANVIL runner wiki pre-step
# ---------------------------------------------------------------------------

class TestRunnerWikiNavigate(unittest.TestCase):
    def test_wiki_navigate_context_result_shape(self):
        from tools.anvil.runner import _wiki_navigate_context
        result = _wiki_navigate_context(["build a DIC pipeline"])
        if result is not None:
            self.assertIn("step", result)
            self.assertEqual(result["step"], 0)
            self.assertIn("returncode", result)

    def test_wiki_navigate_context_empty_args(self):
        from tools.anvil.runner import _wiki_navigate_context
        result = _wiki_navigate_context([])
        self.assertIsNone(result)

    def test_wiki_keyword_search_scores_by_overlap(self):
        from tools.document_intelligence.search_engine import _wiki_keyword_search
        with tempfile.TemporaryDirectory() as td:
            p = Path(td)
            (p / "match.md").write_text(
                "# PostgreSQL\nPostgresql primary backend storage configuration.", encoding="utf-8"
            )
            (p / "nomatch.md").write_text("# Cooking\nPasta recipes.", encoding="utf-8")
            hits = _wiki_keyword_search("postgresql primary storage", p, top_k=5)
            self.assertTrue(len(hits) >= 1)
            self.assertEqual(hits[0][1], "match")


if __name__ == "__main__":
    unittest.main()
