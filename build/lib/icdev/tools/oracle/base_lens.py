# CUI // SP-CTI
"""Oracle Base Lens — Abstract base class for all Oracle prediction lenses.

Each lens follows a three-phase pipeline:
  1. analyze()  — Gather data from internal sources (DB, tools, git)
  2. score()    — Compute prediction confidence scores
  3. propose()  — Generate actionable recommendations

Lenses produce OraclePrediction instances with confidence scores.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

# OraclePrediction is the canonical dataclass — re-exported here so all
# existing lens imports (from tools.oracle.base_lens import OraclePrediction)
# continue to work without modification.
from tools.oracle.prediction import OraclePrediction  # noqa: F401


class BaseLens(ABC):
    """Abstract base class for Oracle lenses."""

    name: str = "base"
    description: str = "Base Oracle lens"

    @abstractmethod
    def analyze(self) -> dict[str, Any]:
        """Phase 1: Gather and analyze data. Returns raw analysis dict."""

    @abstractmethod
    def score(self, analysis: dict[str, Any]) -> list[OraclePrediction]:
        """Phase 2: Score analysis data into predictions with confidence."""

    @abstractmethod
    def propose(self, predictions: list[OraclePrediction]) -> list[OraclePrediction]:
        """Phase 3: Enrich predictions with actionable recommendations."""

    def run(self) -> list[OraclePrediction]:
        """Execute the full lens pipeline: analyze → score → propose."""
        analysis = self.analyze()
        predictions = self.score(analysis)
        return self.propose(predictions)
