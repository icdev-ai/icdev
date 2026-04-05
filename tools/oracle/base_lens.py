# CUI // SP-CTI
"""Oracle Base Lens — Abstract base class for all Oracle prediction lenses.

Each lens follows a three-phase pipeline:
  1. analyze()  — Gather data from internal sources (DB, tools, git)
  2. score()    — Compute prediction confidence scores
  3. propose()  — Generate actionable recommendations

Lenses produce OraclePrediction instances with confidence scores.
"""

from __future__ import annotations

import json
import uuid
from abc import ABC, abstractmethod
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any


@dataclass
class OraclePrediction:
    """A single prediction from an Oracle lens."""

    id: str = field(default_factory=lambda: f"pred-{uuid.uuid4().hex[:10]}")
    lens: str = ""
    title: str = ""
    description: str = ""
    confidence: float = 0.0  # 0.0 - 1.0
    severity: str = "info"  # info, warning, critical
    category: str = ""
    recommendations: list[str] = field(default_factory=list)
    data: dict[str, Any] = field(default_factory=dict)
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    def to_dict(self) -> dict:
        return asdict(self)

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), indent=2, default=str)


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
