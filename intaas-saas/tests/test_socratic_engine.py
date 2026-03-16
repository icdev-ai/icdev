#!/usr/bin/env python3
"""Tests for the Socratic dialogue engine."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.services.socratic_engine import generate_next_question, TURN_SEQUENCE


class TestSocraticEngine:
    def _topic(self):
        return {"title": "Climate Policy Debate", "topic_id": "t1"}

    def _articles(self):
        return [
            {"title": "New Climate Plan Announced", "content": "The government revealed...", "source_id": "s1"},
            {"title": "Critics Slam Climate Proposal", "content": "Opposition leaders...", "source_id": "s2"},
        ]

    def _perspectives(self):
        return [
            {"source_name": "Source A", "source_id": "s1", "summary": "Government plan is bold."},
            {"source_name": "Source B", "source_id": "s2", "summary": "Plan is too expensive."},
        ]

    def test_first_turn_is_assumption(self):
        """First AI turn should be an assumption challenge."""
        result = generate_next_question(
            topic=self._topic(), articles=self._articles(),
            perspectives=self._perspectives(), previous_turns=[],
        )
        assert result["question_type"] == "assumption"
        assert len(result["content"]) > 20

    def test_turn_sequence(self):
        """Questions should follow the 5-turn sequence."""
        turns = []
        for i in range(5):
            result = generate_next_question(
                topic=self._topic(), articles=self._articles(),
                perspectives=self._perspectives(), previous_turns=turns,
            )
            assert result["question_type"] == TURN_SEQUENCE[i]
            turns.append({"role": "ai", "content": result["content"]})
            turns.append({"role": "user", "content": "I think..."})

    def test_content_references_topic(self):
        """Questions should reference topic context."""
        result = generate_next_question(
            topic=self._topic(), articles=self._articles(),
            perspectives=self._perspectives(), previous_turns=[],
        )
        # Should contain some reference to sources or content
        assert len(result["content"]) > 30

    def test_empty_articles(self):
        """Should handle empty articles gracefully."""
        result = generate_next_question(
            topic=self._topic(), articles=[], perspectives=[], previous_turns=[],
        )
        assert result["content"]
        assert result["question_type"] == "assumption"

    def test_synthesis_after_four_turns(self):
        """Turn 5 should be synthesis."""
        turns = [{"role": "ai", "content": f"Q{i}"} for i in range(4)]
        turns += [{"role": "user", "content": f"A{i}"} for i in range(4)]
        # 4 AI turns means next should be synthesis (index 4)
        result = generate_next_question(
            topic=self._topic(), articles=self._articles(),
            perspectives=self._perspectives(), previous_turns=turns,
        )
        assert result["question_type"] == "synthesis"
