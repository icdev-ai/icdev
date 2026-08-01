# CUI // SP-CTI
"""ML Pattern Engine — extract escalation signals from unstructured conflict text.

Uses a two-tier approach:
  1. Rule-based scoring (deterministic, always available)
  2. LLMRouter NLP assessment (optional enhancement; degrades gracefully if unavailable)

Signal extraction:
  - violence_score   : keyword density in text (0–1)
  - actor_count      : distinct actor entity count
  - geographic_spread: distinct location count / 10, capped at 1.0
  - tone             : negative tone score (GDELT GoldsteinScale or estimated)
  - llm_assessment   : optional LLM summary string

Usage:
    from tools.conflict_mesh.ml_pattern_engine import MLPatternEngine
    engine = MLPatternEngine()
    signals = engine.extract_signals("Artillery attack kills soldiers", {"tone": -7.5})
    print(signals.violence_score, signals.geographic_spread)
"""

from __future__ import annotations

import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Optional

BASE_DIR = Path(__file__).resolve().parents[2]
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from tools.logging.icdev_logger import get_logger
log = get_logger("conflict_mesh.ml_engine")

VIOLENCE_KEYWORDS = frozenset({
    "kills", "killed", "attack", "attacked", "offensive", "siege",
    "assault", "bombardment", "airstrike", "artillery", "missile",
    "invasion", "occupation", "shelling", "sniper", "ambush",
    "casualties", "dead", "wounded", "explosion", "rocket", "mortar",
    "combat", "frontline", "war", "fighting", "clashes", "troops",
})

ACTOR_PATTERNS = [
    r"\b(?:military|forces|rebels|militia|troops|soldiers|insurgents|separatists|"
    r"government|regime|army|navy|air\s+force|paramilitary|jihadists|coalition)\b",
]

LOCATION_PATTERNS = [
    r"\b[A-Z][a-z]{2,}(?:\s+[A-Z][a-z]{2,})*\b",  # capitalized words as proxy for locations
]


@dataclass
class EscalationSignals:
    violence_score: float = 0.0
    actor_count: int = 0
    geographic_spread: float = 0.0
    tone: float = 0.0
    llm_assessment: Optional[str] = None


class MLPatternEngine:
    """Extracts escalation signals from unstructured conflict text."""

    def __init__(self, llm_router: Optional[Any] = None) -> None:
        self._llm = llm_router
        self._actor_re = re.compile(
            "|".join(ACTOR_PATTERNS), re.IGNORECASE
        )

    def extract_signals(
        self, text: str, metadata: Dict[str, Any]
    ) -> EscalationSignals:
        """Extract escalation signals from text and metadata.

        Args:
            text: Unstructured narrative text (headline, body, summary).
            metadata: Structured metadata dict (may contain tone, locations, etc.)

        Returns:
            EscalationSignals dataclass with all signal fields populated.
        """
        violence = self._violence_score(text)
        actors = self._actor_count(text, metadata)
        spread = self._geographic_spread(text, metadata)
        tone = self._extract_tone(metadata)
        llm = self._llm_assess(text) if self._llm is not None else None

        return EscalationSignals(
            violence_score=violence,
            actor_count=actors,
            geographic_spread=spread,
            tone=tone,
            llm_assessment=llm,
        )

    def _violence_score(self, text: str) -> float:
        if not text:
            return 0.0
        words = re.findall(r"\w+", text.lower())
        if not words:
            return 0.0
        hits = sum(1 for w in words if w in VIOLENCE_KEYWORDS)
        # Density: hits / total words, capped at 1.0, scaled by sensitivity factor
        density = hits / max(len(words), 1)
        return min(density * 5.0, 1.0)  # 20%+ keyword density → max score

    def _actor_count(self, text: str, metadata: Dict[str, Any]) -> int:
        actors_from_meta = len(
            [v for k, v in metadata.items() if k in ("actor1", "actor2") and v]
        )
        matches_in_text = len(set(re.findall(self._actor_re, text.lower())))
        return max(actors_from_meta, matches_in_text)

    def _geographic_spread(self, text: str, metadata: Dict[str, Any]) -> float:
        # Prefer explicit location list from metadata if available
        locations = metadata.get("locations")
        if isinstance(locations, list):
            count = len(locations)
        else:
            # Crude heuristic: count comma-separated geo_hint components
            geo_hint = metadata.get("geo_hint", "")
            count = len([p for p in re.split(r"[,—]", geo_hint) if p.strip()])
        return min(count / 10.0, 1.0)

    def _extract_tone(self, metadata: Dict[str, Any]) -> float:
        tone = metadata.get("tone") or metadata.get("GoldsteinScale") or 0.0
        try:
            return float(tone)
        except (TypeError, ValueError):
            return 0.0

    def _llm_assess(self, text: str) -> Optional[str]:
        try:
            from tools.llm.provider import LLMRequest
            if self._llm is None:
                return None
            req = LLMRequest(
                messages=[{
                    "role": "user",
                    "content": (
                        "You are a conflict analyst. In one sentence, assess whether the "
                        f"following text indicates conflict escalation: {text[:500]}"
                    ),
                }],
                max_tokens=80,
                temperature=0.0,
            )
            resp = self._llm.invoke("summarization", req)
            return resp.content.strip() if resp and resp.content else None
        except Exception as exc:
            log.debug("LLM assessment skipped: %s", exc)
            return None
