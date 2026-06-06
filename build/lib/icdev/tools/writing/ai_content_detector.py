"""AI content detector — thin wrapper re-exporting from analysis_engine."""

from tools.writing.analysis_engine import _ai_detection


def detect_ai_content(text: str) -> dict:
    result = _ai_detection(text)
    # ai_score 0-1 (1.0 = definitely AI). Invert for quality: 100 = human, 0 = AI
    ai_score = result.get("score", 0.5)
    quality_score = round((1.0 - ai_score) * 100, 1) if isinstance(ai_score, float) and ai_score <= 1.0 else ai_score
    return {**result, "score": quality_score, "ai_score": ai_score}
