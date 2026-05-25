# CUI // SP-CTI
"""Trader agent — translates an investment plan into a concrete transaction proposal.

Called after the Bull/Bear investment debate. Produces a structured proposal that
feeds the Risk debate separately from the investment debate.

Config (args/fathomdesk_config.yaml)::

    agent_llm_overrides:
      trader: claude-opus-4-7   # deep-think Opus for high-stakes execution decisions
"""

from __future__ import annotations
from tools.logging.icdev_logger import get_logger

import json
import logging

from tools.fathomdesk.llm_factory import get_llm
from tools.llm.provider import LLMRequest

logger = get_logger(__name__)

_SYSTEM = (
    "You are an institutional execution trader. Given an investment plan and supporting "
    "analyst reports, translate the thesis into a precise transaction proposal. "
    "Return ONLY valid JSON with exactly these fields: "
    '{"direction": "BUY"|"SELL"|"HOLD", '
    '"size_modifier": <float 0.25–2.0>, '
    '"rationale": "<string ≤120 words>"}. '
    "size_modifier: 0.25 = quarter-size (high uncertainty), 1.0 = normal, 2.0 = double (high conviction). "
    "rationale must be concise and actionable. No markdown fencing, no prose outside JSON."
)

_USER_TMPL = (
    "Ticker: {ticker}\n\n"
    "Investment Plan:\n{investment_plan}\n\n"
    "Analyst Reports:\n{analyst_reports_json}\n\n"
    "Translate the above into a transaction proposal."
)


def propose(investment_plan: str, ticker: str, analyst_reports: dict) -> dict:
    """Translate an investment plan into a concrete transaction proposal.

    Args:
        investment_plan: Narrative from the investment debate (e.g. DebateResult.reasoning).
        ticker: Equity ticker symbol (e.g. "AAPL").
        analyst_reports: Raw analyst outputs keyed by lens name.

    Returns:
        dict with keys:
            direction (str): "BUY" | "SELL" | "HOLD"
            size_modifier (float): 0.25–2.0 position-sizing multiplier vs default
            rationale (str): ≤120-word entry logic and key risk summary
    """
    llm = get_llm("trader")

    user_content = _USER_TMPL.format(
        ticker=ticker,
        investment_plan=investment_plan,
        analyst_reports_json=json.dumps(analyst_reports, indent=2, default=str),
    )

    try:
        resp = llm(LLMRequest(
            messages=[{"role": "user", "content": user_content}],
            system_prompt=_SYSTEM,
            max_tokens=256,
            temperature=0.1,  # near-deterministic for execution decisions
            agent_id="trader",
        ))
        raw = resp.content.strip()
        if raw.startswith("```"):
            parts = raw.split("```")
            raw = parts[1].lstrip("json").strip() if len(parts) > 1 else raw
        proposal = json.loads(raw)
    except Exception as exc:
        logger.warning("Trader: LLM/parse failed for %s: %s", ticker, exc)
        return {
            "direction": "HOLD",
            "size_modifier": 0.25,
            "rationale": f"Trader response unavailable; defaulting to reduced-size HOLD. ({exc})",
        }

    direction = str(proposal.get("direction", "HOLD")).upper()
    if direction not in {"BUY", "SELL", "HOLD"}:
        direction = "HOLD"

    try:
        modifier = float(proposal.get("size_modifier", 1.0))
        modifier = max(0.25, min(2.0, modifier))
    except (TypeError, ValueError):
        modifier = 1.0

    return {
        "direction": direction,
        "size_modifier": modifier,
        "rationale": str(proposal.get("rationale", "")),
    }
