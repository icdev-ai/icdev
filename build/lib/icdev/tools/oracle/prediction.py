# CUI // SP-CTI
"""OraclePrediction — Canonical dataclass for Oracle lens output.

Imported by base_lens and all lenses via:
    from tools.oracle.base_lens import BaseLens, OraclePrediction
"""

from __future__ import annotations

import json
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any


@dataclass
class OraclePrediction:
    """A single prediction produced by an Oracle lens."""

    id: str = field(default_factory=lambda: f"pred-{uuid.uuid4().hex[:10]}")
    lens: str = ""
    title: str = ""
    description: str = ""
    confidence: float = 0.0  # 0.0–1.0
    severity: str = "info"  # info | warning | critical
    category: str = ""
    recommendations: list[str] = field(default_factory=list)
    data: dict[str, Any] = field(default_factory=dict)
    sentiment_weight: float = 0.5  # 0.0–1.0; >0.5 = bullish, <0.5 = bearish; default neutral
    created_at: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )

    def to_dict(self) -> dict:
        return asdict(self)

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), indent=2, default=str)
