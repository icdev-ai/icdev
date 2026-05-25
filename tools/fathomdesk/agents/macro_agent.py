# CUI // SP-CTI
"""MacroAgent — macro-regime analyst lens."""

from __future__ import annotations
from tools.logging.icdev_logger import get_logger

import json
import logging

from tools.fathomdesk.agents.base_analyst import BaseAnalystAgent
from tools.db.storage import get_connection
from tools.llm.provider import LLMRequest

logger = get_logger(__name__)

_SYSTEM = (
    "You are a senior macro economist and market strategist. "
    "Analyze the macro regime context and trap signals, then return ONLY valid JSON with keys: "
    "score (float 0-1, >0.5 = bullish), signals (list of strings), "
    "reasoning (string), confidence (float 0-1). No markdown, no prose outside JSON."
)

_TRAP_LIMIT = 10


def _fetch_trap_events(ticker: str) -> list[dict]:
    """Query recent ad_trap_events for *ticker*. Returns [] on any error."""
    try:
        conn = get_connection()
        ph = "%s" if getattr(conn, "_dialect", "sqlite") == "postgresql" else "?"
        try:
            rows = conn.execute(
                f"SELECT ticker, pattern, confidence, bar_age, timeframe, created_at "  # nosec B608
                f"FROM ad_trap_events WHERE ticker = {ph} "
                f"ORDER BY created_at DESC LIMIT {ph}",
                [ticker, _TRAP_LIMIT],
            ).fetchall()
            return [dict(r) for r in rows]
        finally:
            conn.close()
    except Exception as exc:
        logger.debug("MacroAgent: ad_trap_events query failed: %s", exc)
        return []


def _fetch_macro() -> dict:
    """Call fetch_macro_context(); return {} on import/runtime error."""
    try:
        from tools.trading.data.macro_data import fetch_macro_context
        return fetch_macro_context() or {}
    except Exception as exc:
        logger.debug("MacroAgent: fetch_macro_context failed: %s", exc)
        return {}


class MacroAgent(BaseAnalystAgent):
    """Analyst that reads macro regime signals and trap events."""

    agent_name = "macro_agent"

    def analyze(self, **kwargs) -> dict:
        trap_events = _fetch_trap_events(self.ticker)
        macro_ctx = _fetch_macro()

        context_summary = {
            "regime": macro_ctx.get("regime", "UNKNOWN"),
            "qeqt_phase": macro_ctx.get("qeqt_phase", "NEUTRAL"),
            "credit_impulse_label": macro_ctx.get("credit_impulse_label", "NEUTRAL"),
            "macro_score": macro_ctx.get("macro_score"),
            "summary": macro_ctx.get("summary", ""),
            "recent_trap_events": trap_events,
        }

        prompt = (
            f"Ticker: {self.ticker}\n"
            f"As-of date: {self.as_of_date}\n"
            f"Macro context:\n{json.dumps(context_summary, default=str)}\n\n"
            "Evaluate the macro outlook for this ticker. Return JSON only."
        )
        try:
            resp = self._llm(LLMRequest(
                messages=[{"role": "user", "content": prompt}],
                system_prompt=_SYSTEM,
                max_tokens=512,
                temperature=0.3,
                agent_id="macro_agent",
            ))
            data = json.loads(resp.content)
            return {
                "score": float(data.get("score", 0.5)),
                "signals": list(data.get("signals", [])),
                "reasoning": str(data.get("reasoning", "")),
                "confidence": float(data.get("confidence", 0.5)),
            }
        except Exception as exc:
            logger.warning("MacroAgent: LLM/parse failed for %s: %s", self.ticker, exc)
            return {"score": 0.5, "signals": [], "reasoning": str(exc), "confidence": 0.0}
