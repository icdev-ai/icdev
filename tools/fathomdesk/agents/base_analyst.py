# CUI // SP-CTI
"""BaseAnalystAgent — abstract base class for all FathomDesk analyst agents."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Callable

from tools.fathomdesk.llm_factory import get_llm


class BaseAnalystAgent(ABC):
    """Abstract analyst agent.

    Subclasses set ``agent_name`` at class level, then call ``super().__init__``.
    ``self._llm`` is a ``(LLMRequest) -> LLMResponse`` callable routed through
    ``llm_factory.get_llm(agent_name)``.

    ``analyze(**kwargs) -> dict`` must return at minimum:
        score: float        0.0–1.0 bullish/bearish signal (>0.5 = bullish)
        signals: list[str]  human-readable evidence strings
        reasoning: str      narrative explanation
        confidence: float   0.0–1.0 model confidence in the score
    """

    agent_name: str = "base_analyst"

    def __init__(self, ticker: str, as_of_date: str) -> None:
        self.ticker = ticker.upper().strip()
        self.as_of_date = as_of_date
        self._llm: Callable = get_llm(self.agent_name)

    @abstractmethod
    def analyze(self, **kwargs) -> dict:
        """Run the analyst lens and return a structured report dict."""
