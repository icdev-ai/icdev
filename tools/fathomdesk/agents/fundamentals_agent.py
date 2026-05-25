# CUI // SP-CTI
"""FundamentalsAgent — valuation-based analyst lens."""

from __future__ import annotations
from tools.logging.icdev_logger import get_logger

import json
import logging

from tools.fathomdesk.agents.base_analyst import BaseAnalystAgent
from tools.fathomdesk.data_gateway import FathomDeskDataGateway
from tools.llm.provider import LLMRequest

logger = get_logger(__name__)

_SYSTEM = (
    "You are a senior fundamental equity analyst. "
    "Analyze the provided fundamental data and return ONLY valid JSON with keys: "
    "score (float 0-1, >0.5 = bullish), signals (list of strings), "
    "reasoning (string), confidence (float 0-1). No markdown, no prose outside JSON."
)


class FundamentalsAgent(BaseAnalystAgent):
    """Analyst that fetches fundamental data and runs valuation LLM scoring."""

    agent_name = "fundamentals_agent"

    def analyze(self, **kwargs) -> dict:
        gw = FathomDeskDataGateway()
        try:
            fundamentals = gw.fundamentals(self.ticker, as_of_date=self.as_of_date)
        except Exception as exc:
            logger.warning("FundamentalsAgent: data fetch failed for %s: %s", self.ticker, exc)
            fundamentals = {}

        prompt = (
            f"Ticker: {self.ticker}\n"
            f"As-of date: {self.as_of_date}\n"
            f"Fundamental data:\n{json.dumps(fundamentals, default=str)}\n\n"
            "Evaluate the fundamental outlook. Return JSON only."
        )
        try:
            resp = self._llm(LLMRequest(
                messages=[{"role": "user", "content": prompt}],
                system_prompt=_SYSTEM,
                max_tokens=512,
                temperature=0.3,
                agent_id="fundamentals_agent",
            ))
            data = json.loads(resp.content)
            return {
                "score": float(data.get("score", 0.5)),
                "signals": list(data.get("signals", [])),
                "reasoning": str(data.get("reasoning", "")),
                "confidence": float(data.get("confidence", 0.5)),
            }
        except Exception as exc:
            logger.warning("FundamentalsAgent: LLM/parse failed for %s: %s", self.ticker, exc)
            return {"score": 0.5, "signals": [], "reasoning": str(exc), "confidence": 0.0}
