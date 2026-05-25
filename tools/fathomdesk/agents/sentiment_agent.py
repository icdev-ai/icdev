# CUI // SP-CTI
"""SentimentAgent — news-driven sentiment analyst lens."""

from __future__ import annotations
from tools.logging.icdev_logger import get_logger

import json
import logging

from tools.fathomdesk.agents.base_analyst import BaseAnalystAgent
from tools.fathomdesk.data_gateway import FathomDeskDataGateway
from tools.llm.provider import LLMRequest

logger = get_logger(__name__)

_SYSTEM = (
    "You are a senior sentiment analyst specializing in news-driven market signals. "
    "Analyze the provided headlines and return ONLY valid JSON with keys: "
    "score (float 0-1, >0.5 = bullish), signals (list of strings), "
    "reasoning (string), confidence (float 0-1). No markdown, no prose outside JSON."
)

_MAX_HEADLINES = 20


class SentimentAgent(BaseAnalystAgent):
    """Analyst that reads recent news and scores market sentiment.

    If an INTaaS reasoner pre-computed sentiment score is available in the
    news payload (``intaas_sentiment`` field), it is used directly without
    re-running the LLM.
    """

    agent_name = "sentiment_agent"

    def analyze(self, **kwargs) -> dict:
        gw = FathomDeskDataGateway()
        try:
            news_items: list[dict] = gw.fetch_news(self.ticker, as_of_date=self.as_of_date)
        except Exception as exc:
            logger.warning("SentimentAgent: news fetch failed for %s: %s", self.ticker, exc)
            news_items = []

        # Fast path: use INTaaS pre-computed score if present on any item
        intaas_score = next(
            (float(n["intaas_sentiment"]) for n in news_items if "intaas_sentiment" in n),
            None,
        )
        if intaas_score is not None:
            signals = [n.get("headline", n.get("title", "")) for n in news_items[:5]]
            return {
                "score": max(0.0, min(1.0, intaas_score)),
                "signals": [s for s in signals if s],
                "reasoning": "INTaaS pre-computed sentiment score used directly.",
                "confidence": 0.8,
            }

        headlines = [
            n.get("headline", n.get("title", ""))
            for n in news_items[:_MAX_HEADLINES]
            if n.get("headline") or n.get("title")
        ]
        if not headlines:
            return {"score": 0.5, "signals": [], "reasoning": "No headlines available.", "confidence": 0.0}

        prompt = (
            f"Ticker: {self.ticker}\n"
            f"As-of date: {self.as_of_date}\n"
            f"Recent headlines ({len(headlines)}):\n"
            + "\n".join(f"- {h}" for h in headlines)
            + "\n\nEvaluate the sentiment outlook. Return JSON only."
        )
        try:
            resp = self._llm(LLMRequest(
                messages=[{"role": "user", "content": prompt}],
                system_prompt=_SYSTEM,
                max_tokens=512,
                temperature=0.3,
                agent_id="sentiment_agent",
            ))
            data = json.loads(resp.content)
            return {
                "score": float(data.get("score", 0.5)),
                "signals": list(data.get("signals", [])),
                "reasoning": str(data.get("reasoning", "")),
                "confidence": float(data.get("confidence", 0.5)),
            }
        except Exception as exc:
            logger.warning("SentimentAgent: LLM/parse failed for %s: %s", self.ticker, exc)
            return {"score": 0.5, "signals": [], "reasoning": str(exc), "confidence": 0.0}
