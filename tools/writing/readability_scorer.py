"""Readability scorer — thin wrapper re-exporting from analysis_engine."""

from tools.writing.analysis_engine import _readability_check


def score_readability(text: str) -> dict:
    result = _readability_check(text)
    # Convert 0-1 score to Flesch ease (0-100) for WriteGuard bridge compatibility
    raw = result.get("score", 0.5)
    flesch_ease = round(raw * 100, 1) if isinstance(raw, float) and raw <= 1.0 else raw
    return {
        **result,
        "score": flesch_ease,
        "flesch_ease": flesch_ease,
        "flesch_reading_ease": flesch_ease,
    }
