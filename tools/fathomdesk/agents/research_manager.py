# CUI // SP-CTI
"""ResearchManager — synthesizes 4 analyst reports into a coherent investment plan.

Sits between the analyst panel and the DebateEngine. Takes the structured
report text from FundamentalsAgent, TechnicalAgent, SentimentAgent, and MacroAgent
and produces a single narrative investment plan that the DebateEngine uses as
shared context for its bull/bear debate.

Usage::

    from tools.fathomdesk.agents.research_manager import run_synthesis

    plan = run_synthesis({
        "fundamentals": "P/E 22x, free cash flow positive ...",
        "technical":    "RSI 58, MACD crossover ...",
        "sentiment":    "Positive news tone, 0.72 score ...",
        "macro":        "Fed pause likely, yield curve flattening ...",
    })
    # plan is a plain-prose investment plan string
"""

from __future__ import annotations
from tools.logging.icdev_logger import get_logger


from tools.fathomdesk.llm_factory import get_llm
from tools.llm.provider import LLMRequest

logger = get_logger(__name__)

_SYSTEM_PROMPT = (
    "You are a senior portfolio strategist. Given analyst reports from four lenses "
    "(fundamentals, technical, sentiment, macro), synthesize them into a coherent "
    "investment plan. The plan must: (1) identify the dominant theme across all four "
    "lenses, (2) flag any conflicting signals, (3) state a clear directional bias "
    "(bullish / bearish / neutral) with supporting rationale, and (4) list 2-3 key "
    "risks. Write in plain prose, 200-350 words. No JSON, no markdown headers."
)


def run_synthesis(reports: dict[str, str]) -> str:
    """Synthesize 4 analyst reports into a single investment plan narrative.

    Args:
        reports: Mapping of lens name (e.g. ``"fundamentals"``) to its report text.

    Returns:
        Investment plan as a plain-prose string (200-350 words).
    """
    llm = get_llm("research_manager")

    report_block = "\n\n".join(
        f"[{lens.upper()}]\n{text}" for lens, text in reports.items()
    )
    prompt = (
        f"Analyst reports:\n\n{report_block}\n\n"
        "Synthesize the above into a coherent investment plan."
    )

    try:
        resp = llm(LLMRequest(
            messages=[{"role": "user", "content": prompt}],
            system_prompt=_SYSTEM_PROMPT,
            max_tokens=512,
            temperature=0.4,
            agent_id="research_manager",
        ))
        return resp.content.strip()
    except Exception as exc:
        logger.warning("ResearchManager: synthesis failed: %s", exc)
        return "Investment plan unavailable. Analyst reports: " + " | ".join(
            f"{k}: {v[:120]}" for k, v in reports.items()
        )
