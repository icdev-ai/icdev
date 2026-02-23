#!/usr/bin/env python3
# CUI // SP-CTI
"""3-Tier history compression for chat contexts (Phase 44 — D271-D274).

Opt-in per context. Budget: Current Topic 50%, Historical Topics 30%, Bulk 20%.
Topic boundary detection via time gap >30min OR keyword shift >60%.
Summarization via LLMRouter (falls back to truncation).
Originals preserved in DB (is_compressed=0).

Usage:
    from tools.memory.history_compressor import HistoryCompressor

    compressor = HistoryCompressor()
    compressed = compressor.compress(messages, budget_tokens=4000)
"""

import logging
import re
import string
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger("icdev.history_compressor")

# Stopwords for keyword extraction (small set, air-gap safe)
_STOPWORDS = frozenset({
    "the", "a", "an", "is", "are", "was", "were", "be", "been", "being",
    "have", "has", "had", "do", "does", "did", "will", "would", "could",
    "should", "may", "might", "can", "shall", "must", "need", "this",
    "that", "these", "those", "it", "its", "i", "you", "he", "she", "we",
    "they", "me", "him", "her", "us", "them", "my", "your", "his", "our",
    "their", "what", "which", "who", "whom", "when", "where", "why", "how",
    "not", "no", "but", "or", "and", "if", "then", "else", "for", "in",
    "on", "at", "to", "of", "by", "with", "from", "as", "into", "about",
    "so", "up", "out", "just", "also", "very", "too", "all", "any", "each",
    "some", "more", "most", "other", "than", "only", "own", "same", "such",
})

# Default budget allocation
DEFAULT_BUDGET = {
    "current_topic": 0.50,
    "historical_topics": 0.30,
    "bulk": 0.20,
}

# Topic boundary thresholds
DEFAULT_TIME_GAP_MINUTES = 30
DEFAULT_KEYWORD_SHIFT_THRESHOLD = 0.60


@dataclass
class TopicBoundary:
    """Represents a detected topic segment in conversation history."""

    start_turn: int
    end_turn: int
    keywords: List[str] = field(default_factory=list)
    time_span_minutes: float = 0.0
    message_count: int = 0
    summary: str = ""


class HistoryCompressor:
    """Compress conversation history using 3-tier proportional budget.

    Tiers:
    1. Current Topic (50%) — recent messages in the active topic
    2. Historical Topics (30%) — summaries of prior topic segments
    3. Bulk (20%) — merged mega-summary of oldest topics
    """

    def __init__(
        self,
        budget_allocation: Optional[Dict[str, float]] = None,
        time_gap_minutes: int = DEFAULT_TIME_GAP_MINUTES,
        keyword_shift_threshold: float = DEFAULT_KEYWORD_SHIFT_THRESHOLD,
    ):
        self._budget = budget_allocation or dict(DEFAULT_BUDGET)
        self._time_gap_minutes = time_gap_minutes
        self._keyword_shift_threshold = keyword_shift_threshold

    def compress(
        self,
        messages: List[dict],
        budget_tokens: int = 4000,
    ) -> List[dict]:
        """Compress messages to fit within budget_tokens.

        Args:
            messages: List of message dicts with 'role', 'content', 'turn_number', 'created_at'.
            budget_tokens: Maximum token budget for compressed output.

        Returns:
            List of (possibly summarized) message dicts.
        """
        if not messages:
            return []

        total_tokens = sum(self._estimate_tokens(m.get("content", "")) for m in messages)
        if total_tokens <= budget_tokens:
            return messages  # Already within budget

        # Detect topic boundaries
        boundaries = self.detect_topic_boundaries(messages)

        if not boundaries:
            # No boundaries detected — truncate to budget
            return self._truncate_to_budget(messages, budget_tokens)

        # Allocate budget across tiers
        current_budget = int(budget_tokens * self._budget["current_topic"])
        historical_budget = int(budget_tokens * self._budget["historical_topics"])
        bulk_budget = int(budget_tokens * self._budget["bulk"])

        result = []

        # Tier 3: Bulk — merge oldest topics into mega-summary
        if len(boundaries) > 2:
            bulk_topics = boundaries[:-2]
            bulk_messages = []
            for b in bulk_topics:
                bulk_messages.extend(
                    m for m in messages
                    if b.start_turn <= m.get("turn_number", 0) <= b.end_turn
                )
            if bulk_messages:
                bulk_summary = self._merge_summaries(
                    [self._summarize_topic(
                        [m for m in messages if b.start_turn <= m.get("turn_number", 0) <= b.end_turn],
                        max_tokens=bulk_budget // max(len(bulk_topics), 1),
                    ) for b in bulk_topics],
                    max_tokens=bulk_budget,
                )
                if bulk_summary:
                    result.append({
                        "role": "system",
                        "content": f"[Compressed history — {len(bulk_topics)} topics]\n{bulk_summary}",
                        "content_type": "summary",
                        "is_compressed": True,
                        "compression_tier": "bulk",
                    })

        # Tier 2: Historical — summaries of middle topics
        if len(boundaries) > 1:
            hist_topics = boundaries[-2:-1] if len(boundaries) > 2 else boundaries[:-1]
            per_topic_budget = historical_budget // max(len(hist_topics), 1)
            for b in hist_topics:
                topic_msgs = [
                    m for m in messages
                    if b.start_turn <= m.get("turn_number", 0) <= b.end_turn
                ]
                summary = self._summarize_topic(topic_msgs, max_tokens=per_topic_budget)
                if summary:
                    result.append({
                        "role": "system",
                        "content": f"[Topic summary — turns {b.start_turn}-{b.end_turn}]\n{summary}",
                        "content_type": "summary",
                        "is_compressed": True,
                        "compression_tier": "historical",
                    })

        # Tier 1: Current — keep most recent topic messages within budget
        current_boundary = boundaries[-1]
        current_msgs = [
            m for m in messages
            if m.get("turn_number", 0) >= current_boundary.start_turn
        ]
        result.extend(self._truncate_to_budget(current_msgs, current_budget))

        return result

    def detect_topic_boundaries(
        self,
        messages: List[dict],
    ) -> List[TopicBoundary]:
        """Detect topic boundaries via time gap and keyword shift heuristics.

        Air-gap safe — no LLM required.
        """
        if not messages:
            return []

        boundaries = []
        current_start = 0

        for i in range(1, len(messages)):
            prev = messages[i - 1]
            curr = messages[i]

            is_boundary = False

            # Check time gap
            time_gap = self._time_gap_minutes_between(prev, curr)
            if time_gap >= self._time_gap_minutes:
                is_boundary = True

            # Check keyword shift
            if not is_boundary and i >= 3:
                # Compare keywords of last 3 messages vs current 3 messages
                prev_window = messages[max(0, i - 3):i]
                curr_window = messages[i:min(len(messages), i + 3)]
                prev_kw = self._extract_keywords_from_messages(prev_window)
                curr_kw = self._extract_keywords_from_messages(curr_window)

                if prev_kw and curr_kw:
                    overlap = len(prev_kw & curr_kw)
                    total = len(prev_kw | curr_kw)
                    similarity = overlap / total if total > 0 else 1.0
                    if (1.0 - similarity) >= self._keyword_shift_threshold:
                        is_boundary = True

            if is_boundary:
                segment_msgs = messages[current_start:i]
                keywords = list(self._extract_keywords_from_messages(segment_msgs))[:10]
                boundaries.append(TopicBoundary(
                    start_turn=messages[current_start].get("turn_number", current_start),
                    end_turn=messages[i - 1].get("turn_number", i - 1),
                    keywords=keywords,
                    message_count=len(segment_msgs),
                ))
                current_start = i

        # Final segment
        final_msgs = messages[current_start:]
        if final_msgs:
            keywords = list(self._extract_keywords_from_messages(final_msgs))[:10]
            boundaries.append(TopicBoundary(
                start_turn=messages[current_start].get("turn_number", current_start),
                end_turn=messages[-1].get("turn_number", len(messages) - 1),
                keywords=keywords,
                message_count=len(final_msgs),
            ))

        return boundaries

    def _summarize_topic(
        self,
        messages: List[dict],
        max_tokens: int = 200,
    ) -> str:
        """Summarize a topic segment. Uses LLM if available, falls back to truncation."""
        if not messages:
            return ""

        combined = "\n".join(
            f"{m.get('role', 'user')}: {m.get('content', '')}"
            for m in messages
        )

        # Try LLM summarization
        try:
            from tools.llm.router import LLMRouter
            router = LLMRouter()
            response = router.generate(
                function_name="history_summarize",
                messages=[{
                    "role": "user",
                    "content": f"Summarize this conversation segment in {max_tokens // 4} words or fewer:\n\n{combined[:3000]}",
                }],
            )
            summary = response.get("content", str(response)) if isinstance(response, dict) else str(response)
            return summary[:max_tokens * 4]  # Rough token-to-char ratio
        except (ImportError, Exception) as exc:
            logger.debug("LLM summarization unavailable: %s — using truncation", exc)

        # Fallback: truncation
        max_chars = max_tokens * 4
        if len(combined) <= max_chars:
            return combined
        return combined[:max_chars - 3] + "..."

    def _merge_summaries(
        self,
        summaries: List[str],
        max_tokens: int = 500,
    ) -> str:
        """Merge multiple topic summaries into a bulk summary."""
        combined = "\n---\n".join(s for s in summaries if s)
        max_chars = max_tokens * 4
        if len(combined) <= max_chars:
            return combined
        return combined[:max_chars - 3] + "..."

    def _truncate_to_budget(
        self,
        messages: List[dict],
        budget_tokens: int,
    ) -> List[dict]:
        """Keep most recent messages that fit within budget."""
        result = []
        used = 0
        for msg in reversed(messages):
            tokens = self._estimate_tokens(msg.get("content", ""))
            if used + tokens > budget_tokens:
                break
            result.insert(0, msg)
            used += tokens
        return result

    @staticmethod
    def _estimate_tokens(text: str) -> int:
        """Estimate token count. Rough: len(text) // 4."""
        return max(1, len(text) // 4)

    @staticmethod
    def _extract_keywords(text: str) -> set:
        """Extract keywords from text using stdlib word extraction + stopword filter."""
        words = re.findall(r'\b[a-zA-Z]{3,}\b', text.lower())
        return {w for w in words if w not in _STOPWORDS}

    def _extract_keywords_from_messages(self, messages: List[dict]) -> set:
        """Extract keywords from a list of messages."""
        keywords = set()
        for m in messages:
            keywords |= self._extract_keywords(m.get("content", ""))
        return keywords

    @staticmethod
    def _time_gap_minutes_between(msg1: dict, msg2: dict) -> float:
        """Calculate time gap in minutes between two messages."""
        try:
            t1 = msg1.get("created_at", "")
            t2 = msg2.get("created_at", "")
            if not t1 or not t2:
                return 0.0

            # Parse ISO timestamps
            dt1 = datetime.fromisoformat(t1.replace("Z", "+00:00"))
            dt2 = datetime.fromisoformat(t2.replace("Z", "+00:00"))
            delta = abs((dt2 - dt1).total_seconds())
            return delta / 60.0
        except (ValueError, TypeError):
            return 0.0
