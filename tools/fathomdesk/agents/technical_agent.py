# CUI // SP-CTI
"""TechnicalAgent — price-action and signal-based analyst lens."""

from __future__ import annotations
from tools.logging.icdev_logger import get_logger

import json
from typing import Any

from tools.fathomdesk.agents.base_analyst import BaseAnalystAgent
from tools.fathomdesk.factor_model import compute_factor_exposure
from tools.fathomdesk.signal_generator import load_thresholds, generate
from tools.llm.provider import LLMRequest

logger = get_logger(__name__)

_SYSTEM = (
    "You are a senior technical analyst. "
    "Analyze the filtered price-action signals and factor exposures, then return ONLY valid JSON with keys: "
    "score (float 0-1, >0.5 = bullish), signals (list of strings), "
    "reasoning (string), confidence (float 0-1), direction (BUY|SELL|HOLD). "
    "No markdown, no prose outside JSON."
)


class TechnicalAgent(BaseAnalystAgent):
    """Analyst that reads pre-filtered signals from signal_generator and scores them."""

    agent_name = "technical_agent"

    def analyze(self, **kwargs) -> dict:
        raw_signals: list[dict[str, Any]] = kwargs.get("signals", [])
        thresholds = load_thresholds()
        filtered = generate(raw_signals, thresholds)

        factor_ctx = ""
        try:
            exp = compute_factor_exposure(self.ticker)
            factor_ctx = (
                f"\nFactor exposures (FF3): beta_market={exp['beta_market']:.3f}, "
                f"beta_smb={exp['beta_smb']:.3f}, beta_hml={exp['beta_hml']:.3f}, "
                f"r_squared={exp['r_squared']:.3f}"
            )
        except Exception as exc:
            logger.debug("TechnicalAgent: factor exposure unavailable for %s: %s", self.ticker, exc)

        if not filtered:
            return {
                "score": 0.5,
                "signals": [],
                "reasoning": "No signals passed threshold gates.",
                "confidence": 0.0,
                "direction": "HOLD",
            }

        prompt = (
            f"Ticker: {self.ticker}\n"
            f"As-of date: {self.as_of_date}\n"
            f"Filtered technical signals ({len(filtered)}):\n"
            f"{json.dumps(filtered)}"
            f"{factor_ctx}\n\n"
            "Evaluate the technical outlook. Return JSON only."
        )
        try:
            resp = self._llm(LLMRequest(
                messages=[{"role": "user", "content": prompt}],
                system_prompt=_SYSTEM,
                max_tokens=512,
                temperature=0.3,
                agent_id="technical_agent",
            ))
            data = json.loads(resp.content)
            return {
                "score": float(data.get("score", 0.5)),
                "signals": list(data.get("signals", [])),
                "reasoning": str(data.get("reasoning", "")),
                "confidence": float(data.get("confidence", 0.5)),
                "direction": str(data.get("direction", "HOLD")).upper(),
            }
        except Exception as exc:
            logger.warning("TechnicalAgent: LLM/parse failed for %s: %s", self.ticker, exc)
            return {"score": 0.5, "signals": [], "reasoning": str(exc), "confidence": 0.0, "direction": "HOLD"}
