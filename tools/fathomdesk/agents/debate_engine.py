# CUI // SP-CTI
"""DebateEngine — orchestrates the multi-agent analyst panel with Bull/Bear arbitration.

Pipeline:
  1. Run all 4 analyst agents concurrently (ThreadPoolExecutor).
  2. Synthesize their outputs into a bull_case and bear_case summary via LLM.
  3. Spawn two opposing advocate LLM calls (bull vs bear) concurrently using the
     Opus model (configured in args/fathomdesk_config.yaml as "debate_engine").
  4. Score each position against analyst report evidence.
  5. Return a structured DebateResult.

Usage::

    from tools.fathomdesk.agents.debate_engine import DebateEngine

    engine = DebateEngine(ticker="AAPL", as_of_date="2026-05-01")
    result = engine.run(signals=[...])   # signals optional
    print(result.verdict, result.confidence)
"""

from __future__ import annotations
from tools.logging.icdev_logger import get_logger

import json
import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from typing import Any

from tools.fathomdesk.llm_factory import get_llm
from tools.llm.provider import LLMRequest

logger = get_logger(__name__)

# ---------------------------------------------------------------------------
# Result dataclass
# ---------------------------------------------------------------------------

@dataclass
class DebateResult:
    """Structured output of a completed Bull/Bear debate."""

    bull_case: str
    bear_case: str
    bull_argument: str
    bear_argument: str
    bull_score: float            # 0.0–1.0
    bear_score: float            # 0.0–1.0
    verdict: str                 # "BUY" | "SELL" | "HOLD"
    confidence: float            # 0.0–1.0 overall confidence in verdict
    reasoning: str               # arbitration narrative
    analyst_reports: dict[str, dict] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "bull_case": self.bull_case,
            "bear_case": self.bear_case,
            "bull_argument": self.bull_argument,
            "bear_argument": self.bear_argument,
            "bull_score": self.bull_score,
            "bear_score": self.bear_score,
            "verdict": self.verdict,
            "confidence": self.confidence,
            "reasoning": self.reasoning,
            "analyst_reports": self.analyst_reports,
        }


# ---------------------------------------------------------------------------
# LLM system prompts
# ---------------------------------------------------------------------------

_SYNTHESIS_SYSTEM = (
    "You are a chief investment strategist synthesizing analyst reports. "
    "Given 4 analyst lenses (fundamentals, technical, sentiment, macro), extract the strongest "
    "bullish and bearish arguments. Return ONLY valid JSON with keys: "
    "bull_case (string, ≤200 words), bear_case (string, ≤200 words). No markdown outside JSON."
)

_BULL_SYSTEM = (
    "You are an aggressive bull advocate. Your job is to argue the strongest possible case for "
    "BUYING the stock. Draw on the provided analyst reports and bull case summary. "
    "Return ONLY valid JSON with keys: argument (string, ≤250 words), score (float 0-1 "
    "representing how convincing your case is given the evidence). No markdown outside JSON."
)

_BEAR_SYSTEM = (
    "You are a hard-nosed bear advocate. Your job is to argue the strongest possible case for "
    "SELLING (or avoiding) the stock. Draw on the analyst reports and bear case summary. "
    "Return ONLY valid JSON with keys: argument (string, ≤250 words), score (float 0-1 "
    "representing how convincing your case is given the evidence). No markdown outside JSON."
)

_ARBITER_SYSTEM = (
    "You are an objective arbitrator weighing a bull/bear investment debate. "
    "Given the two arguments and analyst evidence, determine the final verdict. "
    "Return ONLY valid JSON with keys: verdict (BUY|SELL|HOLD), confidence (float 0-1), "
    "reasoning (string, ≤150 words). No markdown outside JSON."
)


# ---------------------------------------------------------------------------
# Engine
# ---------------------------------------------------------------------------

class DebateEngine:
    """Orchestrates all 4 analyst agents and runs a Bull/Bear debate."""

    def __init__(self, ticker: str, as_of_date: str) -> None:
        self.ticker = ticker.upper().strip()
        self.as_of_date = as_of_date
        self._llm = get_llm("debate_engine")  # routes to Opus via fathomdesk_config.yaml

    # ------------------------------------------------------------------
    # Step 1: run analysts concurrently
    # ------------------------------------------------------------------

    def _run_analysts(self, signals: list[dict] | None = None) -> dict[str, dict]:
        from tools.fathomdesk.agents.fundamentals_agent import FundamentalsAgent
        from tools.fathomdesk.agents.technical_agent import TechnicalAgent
        from tools.fathomdesk.agents.sentiment_agent import SentimentAgent
        from tools.fathomdesk.agents.macro_agent import MacroAgent

        agents: dict[str, Any] = {
            "fundamentals": FundamentalsAgent(self.ticker, self.as_of_date),
            "technical": TechnicalAgent(self.ticker, self.as_of_date),
            "sentiment": SentimentAgent(self.ticker, self.as_of_date),
            "macro": MacroAgent(self.ticker, self.as_of_date),
        }

        reports: dict[str, dict] = {}
        with ThreadPoolExecutor(max_workers=4) as pool:
            futures: dict[Any, str] = {}
            for name, agent in agents.items():
                kw = {"signals": signals or []} if name == "technical" else {}
                futures[pool.submit(agent.analyze, **kw)] = name

            for fut in as_completed(futures):
                name = futures[fut]
                try:
                    reports[name] = fut.result()
                except Exception as exc:
                    logger.warning("DebateEngine: analyst '%s' raised: %s", name, exc)
                    reports[name] = {
                        "score": 0.5,
                        "signals": [],
                        "reasoning": f"Agent failed: {exc}",
                        "confidence": 0.0,
                    }

        return reports

    # ------------------------------------------------------------------
    # Step 2: synthesize bull/bear cases
    # ------------------------------------------------------------------

    def _synthesize(self, reports: dict[str, dict]) -> tuple[str, str]:
        report_text = "\n\n".join(
            f"[{lens.upper()}]\n"
            f"Score: {r.get('score', 0.5):.2f} | Confidence: {r.get('confidence', 0.0):.2f}\n"
            f"Signals: {'; '.join(r.get('signals', []))}\n"
            f"Reasoning: {r.get('reasoning', '')}"
            for lens, r in reports.items()
        )
        prompt = (
            f"Ticker: {self.ticker}  As-of: {self.as_of_date}\n\n"
            f"Analyst Reports:\n{report_text}\n\n"
            "Extract bull_case and bear_case. Return JSON only."
        )
        try:
            resp = self._llm(LLMRequest(
                messages=[{"role": "user", "content": prompt}],
                system_prompt=_SYNTHESIS_SYSTEM,
                max_tokens=512,
                temperature=0.5,
                agent_id="debate_synthesis",
            ))
            data = json.loads(resp.content)
            return str(data.get("bull_case", "")), str(data.get("bear_case", ""))
        except Exception as exc:
            logger.warning("DebateEngine: synthesis failed: %s", exc)
            avg_score = sum(r.get("score", 0.5) for r in reports.values()) / max(len(reports), 1)
            bull_case = "Bullish signals from analyst panel." if avg_score >= 0.5 else "Weak fundamental / technical momentum."
            bear_case = "Bearish risks from analyst panel." if avg_score < 0.5 else "Valuation headwinds and macro uncertainty."
            return bull_case, bear_case

    # ------------------------------------------------------------------
    # Step 3: advocate calls (Opus model via get_llm("debate_engine"))
    # ------------------------------------------------------------------

    def _advocate(
        self,
        side: str,
        bull_case: str,
        bear_case: str,
        reports: dict[str, dict],
    ) -> tuple[str, float]:
        system = _BULL_SYSTEM if side == "bull" else _BEAR_SYSTEM
        own_case = bull_case if side == "bull" else bear_case
        opp_case = bear_case if side == "bull" else bull_case

        report_summary = "; ".join(
            f"{k}: score={v.get('score', 0.5):.2f}"
            for k, v in reports.items()
        )
        prompt = (
            f"Ticker: {self.ticker}  As-of: {self.as_of_date}\n\n"
            f"Your case summary: {own_case}\n"
            f"Opposing case: {opp_case}\n"
            f"Analyst evidence: {report_summary}\n\n"
            f"Make your strongest {side.upper()} argument. Return JSON only."
        )
        try:
            resp = self._llm(LLMRequest(
                messages=[{"role": "user", "content": prompt}],
                system_prompt=system,
                max_tokens=512,
                temperature=0.7,
                agent_id=f"debate_{side}_advocate",
            ))
            data = json.loads(resp.content)
            return str(data.get("argument", "")), float(data.get("score", 0.5))
        except Exception as exc:
            logger.warning("DebateEngine: %s advocate failed: %s", side, exc)
            return f"{side.capitalize()} position (LLM unavailable).", 0.5

    # ------------------------------------------------------------------
    # Step 4: arbitrate
    # ------------------------------------------------------------------

    def _arbitrate(
        self,
        bull_argument: str,
        bear_argument: str,
        bull_score: float,
        bear_score: float,
        reports: dict[str, dict],
    ) -> tuple[str, float, str]:
        report_summary = "; ".join(
            f"{k}: score={v.get('score', 0.5):.2f}, conf={v.get('confidence', 0.0):.2f}"
            for k, v in reports.items()
        )
        prompt = (
            f"Ticker: {self.ticker}  As-of: {self.as_of_date}\n\n"
            f"BULL ARGUMENT (self-score {bull_score:.2f}):\n{bull_argument}\n\n"
            f"BEAR ARGUMENT (self-score {bear_score:.2f}):\n{bear_argument}\n\n"
            f"Analyst evidence: {report_summary}\n\n"
            "Arbitrate and return verdict JSON only."
        )
        try:
            resp = self._llm(LLMRequest(
                messages=[{"role": "user", "content": prompt}],
                system_prompt=_ARBITER_SYSTEM,
                max_tokens=256,
                temperature=0.3,
                agent_id="debate_arbiter",
            ))
            data = json.loads(resp.content)
            verdict = str(data.get("verdict", "HOLD")).upper()
            if verdict not in {"BUY", "SELL", "HOLD"}:
                verdict = "HOLD"
            confidence = float(data.get("confidence", 0.5))
            reasoning = str(data.get("reasoning", ""))
            return verdict, confidence, reasoning
        except Exception as exc:
            logger.warning("DebateEngine: arbitration failed: %s", exc)
            # Fallback: simple score comparison
            if bull_score > bear_score + 0.1:
                verdict, reasoning = "BUY", "Bull score exceeded bear score by margin."
            elif bear_score > bull_score + 0.1:
                verdict, reasoning = "SELL", "Bear score exceeded bull score by margin."
            else:
                verdict, reasoning = "HOLD", "Scores too close to call."
            confidence = abs(bull_score - bear_score) / 2.0
            return verdict, confidence, reasoning

    # ------------------------------------------------------------------
    # Public entry point
    # ------------------------------------------------------------------

    def run(self, signals: list[dict] | None = None) -> DebateResult:
        """Orchestrate the full debate pipeline.

        Args:
            signals: Optional pre-computed raw signals for TechnicalAgent.

        Returns:
            DebateResult with verdict, scores, arguments, and analyst reports.
        """
        # Step 1: gather analyst reports concurrently
        reports = self._run_analysts(signals)

        # Step 2: synthesize bull/bear cases
        bull_case, bear_case = self._synthesize(reports)

        # Step 3: run advocates concurrently (both use Opus via debate_engine override)
        with ThreadPoolExecutor(max_workers=2) as pool:
            bull_fut = pool.submit(self._advocate, "bull", bull_case, bear_case, reports)
            bear_fut = pool.submit(self._advocate, "bear", bull_case, bear_case, reports)
            bull_argument, bull_score = bull_fut.result()
            bear_argument, bear_score = bear_fut.result()

        # Step 4: arbitrate
        verdict, confidence, reasoning = self._arbitrate(
            bull_argument, bear_argument, bull_score, bear_score, reports
        )

        return DebateResult(
            bull_case=bull_case,
            bear_case=bear_case,
            bull_argument=bull_argument,
            bear_argument=bear_argument,
            bull_score=bull_score,
            bear_score=bear_score,
            verdict=verdict,
            confidence=confidence,
            reasoning=reasoning,
            analyst_reports=reports,
        )
