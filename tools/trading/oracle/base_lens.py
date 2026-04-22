# CUI // SP-CTI
"""FathomDesk Trading Oracle — Base Lens.

Three-phase pipeline: analyze() -> score() -> propose().
Adapted from ICDEV Oracle BaseLens for trading domain.
"""

from __future__ import annotations

import json
import uuid
from abc import ABC, abstractmethod
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any


@dataclass
class TradingPrediction:
    """A single prediction produced by a Trading Oracle lens."""

    id: str = field(default_factory=lambda: f"tpred-{uuid.uuid4().hex[:10]}")
    lens: str = ""
    subject_type: str = ""  # ticker, sector, portfolio, macro, news_cluster
    subject_id: str = ""
    prediction_type: str = ""  # convergence, trajectory, stress, intelligence
    title: str = ""
    description: str = ""
    confidence: float = 0.0  # 0.0-1.0
    severity: str = "medium"  # info, medium, high, critical
    horizon_days: int = 7
    direction: str = ""  # bullish, bearish, neutral
    recommended_action: str = ""
    evidence: dict[str, Any] = field(default_factory=dict)
    created_at: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )

    def to_dict(self) -> dict:
        return asdict(self)

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), indent=2, default=str)


class BaseTradingLens(ABC):
    """Abstract base class for Trading Oracle lenses."""

    name: str = "base"
    description: str = "Base trading oracle lens"

    @abstractmethod
    def analyze(self) -> dict[str, Any]:
        """Phase 1: Gather data from trading infrastructure."""

    @abstractmethod
    def score(self, analysis: dict[str, Any]) -> list[TradingPrediction]:
        """Phase 2: Score analysis into predictions with confidence."""

    @abstractmethod
    def propose(self, predictions: list[TradingPrediction]) -> list[TradingPrediction]:
        """Phase 3: Enrich predictions with recommended actions."""

    def run(self) -> list[TradingPrediction]:
        """Execute the full lens pipeline: analyze -> score -> propose."""
        analysis = self.analyze()
        predictions = self.score(analysis)
        return self.propose(predictions)
