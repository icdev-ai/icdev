"""Tone profiler — thin wrapper re-exporting from analysis_engine."""

from tools.writing.analysis_engine import _tone_check


def profile_tone(text: str, **kwargs) -> dict:
    """Profile tone. Accepts and ignores extra kwargs (e.g. use_llm)."""
    result = _tone_check(text)
    # Convert 0-1 score to 0-100 for WriteGuard bridge compatibility
    raw = result.get("score", 0.7)
    score_100 = round(raw * 100, 1) if isinstance(raw, float) and raw <= 1.0 else raw
    return {**result, "score": score_100, "findings": result.get("issues", [])}
