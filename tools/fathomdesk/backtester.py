# CUI // SP-CTI
"""FathomDesk backtester — signal scoring (precision / recall / F1).

score(signals) treats 'long' as the positive class and uses p1y > 0
as the ground-truth outcome to compute classification metrics.
"""
from __future__ import annotations

from typing import Any


def score(signals: list[dict[str, Any]]) -> dict[str, float]:
    """Return precision, recall, and F1 for a list of directional signals.

    Each signal must have:
      direction  — 'long' or 'short'
      p1y        — actual 1-year return (float; > 0 means price rose)
      confidence — float 0-1 (informational; not used in classification)

    Positive class: direction == 'long'.
    Ground truth positive: p1y > 0.
    """
    tp = fp = fn = 0
    for sig in signals:
        predicted_long = str(sig.get("direction", "")).lower() == "long"
        actual_up = float(sig.get("p1y", 0.0)) > 0.0
        if predicted_long and actual_up:
            tp += 1
        elif predicted_long and not actual_up:
            fp += 1
        elif not predicted_long and actual_up:
            fn += 1

    precision = tp / (tp + fp) if (tp + fp) > 0 else 0.0
    recall = tp / (tp + fn) if (tp + fn) > 0 else 0.0
    f1 = (
        2 * precision * recall / (precision + recall)
        if (precision + recall) > 0
        else 0.0
    )

    return {"precision": precision, "recall": recall, "f1": f1}
