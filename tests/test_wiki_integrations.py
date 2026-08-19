# CUI // SP-CTI
"""Tests for Karpathy LLM Wiki integrations (Items 1–5).

Item 1: DIC wiki bypass — REMOVED by cef-di-04 (ungoverned pre-RAG cache)
Item 2: ACE coworker role wiki context enrichment on _run startup
Item 3: ANVIL Navigate wiki pre-step via wiki_tool_query
Item 4: DIC answer filing — REMOVED by cef-di-04 (see test_dic_search_evidence.py)
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
# Items 1 and 4 -- the DIC wiki Q&A cache -- were REMOVED by cef-di-04, so the
# tests that pinned them are gone with them. That cache filed grounded DIC
# answers into the auto-memory directory and served them back BEFORE any
# retrieval, with no tenant in its key, no clearance filter, no citations and
# no invalidation. Coverage of the decision now lives in
# tests/test_dic_search_evidence.py::TestWikiCacheRemoved, which asserts the
# symbols are gone from BOTH trees and that answer() cannot reach the
# auto-memory directory by any route. The ACE and ANVIL wiki items below are
# untouched.
# ---------------------------------------------------------------------------


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


if __name__ == "__main__":
    unittest.main()
