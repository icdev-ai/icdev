# CUI // SP-CTI
"""FathomDesk analyst_panel — top-level orchestrator for the multi-agent panel.

Checks the ``analyst_panel_enabled`` flag in args/fathomdesk_config.yaml
(overridable via ANALYST_PANEL_ENABLED env var). When disabled, falls back to
heuristic signal scoring without touching DebateEngine or RiskManager.

Usage::

    from tools.fathomdesk.analyst_panel import run

    rec = run(ticker="AAPL", as_of_date="2026-05-01", signals=[...])
    # rec: {"direction": "BUY"|"SELL"|"HOLD", "confidence": float,
    #       "size_modifier": float, "reasoning": str, "source": str}
"""
from __future__ import annotations
from tools.logging.icdev_logger import get_logger

import os
from pathlib import Path
from typing import Any

import yaml

logger = get_logger(__name__)

_CONFIG_PATH = Path(__file__).resolve().parents[2] / "args" / "fathomdesk_config.yaml"


def _panel_enabled() -> bool:
    """Return True if analyst panel is enabled (default True).

    Env var ANALYST_PANEL_ENABLED='0'/'false' takes precedence over config file.
    """
    env = os.environ.get("ANALYST_PANEL_ENABLED", "").strip().lower()
    if env in {"0", "false", "no"}:
        return False
    if env in {"1", "true", "yes"}:
        return True
    try:
        with _CONFIG_PATH.open(encoding="utf-8") as fh:
            cfg = yaml.safe_load(fh) or {}
        return bool(cfg.get("analyst_panel_enabled", True))
    except Exception:
        return True


def run(
    ticker: str,
    as_of_date: str,
    signals: list[dict] | None = None,
) -> dict[str, Any]:
    """Run the analyst panel or fall back to heuristic scoring.

    Args:
        ticker: Equity ticker symbol (e.g. "AAPL").
        as_of_date: ISO date string (e.g. "2026-05-01").
        signals: Optional pre-computed raw signals for TechnicalAgent.

    Returns:
        dict with keys: direction, confidence, size_modifier, reasoning, source.
    """
    if not _panel_enabled():
        logger.info("analyst_panel: disabled — using heuristic fallback for %s", ticker)
        return _heuristic_fallback(ticker, signals or [])

    from tools.fathomdesk.agents.debate_engine import DebateEngine
    from tools.fathomdesk.agents.risk_manager import RiskManager

    debate = DebateEngine(ticker=ticker, as_of_date=as_of_date)
    result = debate.run(signals=signals)

    rm = RiskManager(ticker=ticker, as_of_date=as_of_date)
    rec = rm.arbitrate(result)
    rec["source"] = "analyst_panel"
    return rec


def _heuristic_fallback(ticker: str, signals: list[dict]) -> dict[str, Any]:
    """Score signals using static threshold rules (no LLM required)."""
    from tools.fathomdesk.signal_generator import generate, load_thresholds

    thresholds = load_thresholds()
    filtered = generate(signals, thresholds)

    if not filtered:
        return {
            "direction": "HOLD",
            "confidence": 0.0,
            "size_modifier": 0.0,
            "reasoning": "Heuristic fallback: no signals passed threshold gates.",
            "source": "heuristic",
        }

    avg_conf = sum(float(s.get("confidence", 0.0)) for s in filtered) / len(filtered)
    avg_score = sum(float(s.get("score", 0.5)) for s in filtered) / len(filtered)

    if avg_score >= 0.6:
        direction = "BUY"
    elif avg_score <= 0.4:
        direction = "SELL"
    else:
        direction = "HOLD"

    return {
        "direction": direction,
        "confidence": round(avg_conf, 4),
        "size_modifier": round(min(avg_conf, 1.0), 4),
        "reasoning": (
            f"Heuristic fallback: {len(filtered)} signal(s) passed gates, "
            f"avg_score={avg_score:.2f}, avg_conf={avg_conf:.2f}."
        ),
        "source": "heuristic",
    }
