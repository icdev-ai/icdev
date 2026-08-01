# CUI // SP-CTI
"""DIC chat feeds GraphRAG community summaries into global/thematic questions.

A question about the corpus as a whole ("what are the main themes across these
documents") is answered by no single chunk — the answer lives in the KG's
community structure. The chat must recognise such a question and put the
community summaries in front of the model, while a direct lookup must NOT pull
them in (and the whole thing must degrade to grounded RAG if the engine is
empty or unavailable).
"""
from __future__ import annotations

from unittest.mock import patch

import pytest

from tools.document_intelligence import blueprint as bp


class TestIsGlobalQuery:
    @pytest.mark.parametrize("q", [
        "what are the main themes across these documents",
        "give me an overview of the corpus",
        "what topics do these documents cover",
        "the overall high-level structure",
        "what are the recurring themes",
    ])
    def test_recognises_thematic_questions(self, q):
        assert bp._is_global_query(q)

    @pytest.mark.parametrize("q", [
        "what is the retention period for AC-2",
        "who approved version 3",
        "define zero trust",
        "when was this document uploaded",
    ])
    def test_ignores_direct_lookups(self, q):
        assert not bp._is_global_query(q)


class _Citation:
    def to_dict(self):
        return {}


class _Result:
    def __init__(self, i):
        self.doc_id = f"d{i}"
        self.page = i
        self.section = ""
        self.content = f"passage {i} about networking"
        self.doc_title = f"Doc {i}"
        self.citation = _Citation()


class TestSynthesisIncludesCommunitySummaries:
    def test_global_summaries_reach_the_prompt(self):
        captured = {}

        class _CX:
            text = "Synthesised themal answer [1]."

        def _fake_complete(prompt, **kwargs):
            captured["prompt"] = prompt
            return _CX()

        with patch("tools.cortex.api.complete", side_effect=_fake_complete):
            out = bp._llm_synthesize(
                "what are the main themes",
                [_Result(1), _Result(2)],
                community_summaries=["Theme A: peering economics", "Theme B: network performance"],
            )
        assert out == "Synthesised themal answer [1]."
        assert "thematic overview" in captured["prompt"].lower()
        assert "Theme A: peering economics" in captured["prompt"]

    def test_no_summaries_means_no_overview_block(self):
        captured = {}

        class _CX:
            text = "answer"

        def _fake_complete(prompt, **kwargs):
            captured["prompt"] = prompt
            return _CX()

        with patch("tools.cortex.api.complete", side_effect=_fake_complete):
            bp._llm_synthesize("summarize doc", [_Result(1)], community_summaries=None)
        assert "thematic overview" not in captured["prompt"].lower()


class TestCommunityContextIsGraceful:
    def test_returns_empty_when_engine_raises(self):
        with patch("tools.knowledge_graph.community_engine.search_communities",
                   side_effect=RuntimeError("no table")):
            assert bp._community_context("themes", "default") == []

    def test_maps_summary_rows_to_texts(self):
        rows = [{"summary_text": "Theme one"}, {"summary_text": "Theme two"}, {"summary_text": ""}]
        with patch("tools.knowledge_graph.community_engine.search_communities", return_value=rows), \
             patch.object(bp, "_conn", return_value=_FakeConn()):
            out = bp._community_context("themes", "default")
        assert out == ["Theme one", "Theme two"]  # blank dropped


class _FakeConn:
    def close(self):
        pass
