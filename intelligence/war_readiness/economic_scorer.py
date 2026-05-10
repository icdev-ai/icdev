# CUI // SP-CTI
"""War Readiness Economic Scorer — DIB procurement velocity signals.

Computes CUSUM-based anomaly scores on defense industrial base (DIB)
procurement data relative to neutral-country baselines. Scores feed
war readiness assessment pipelines on a normalized 0-10 scale.
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger("icdev.intelligence.war_readiness.economic_scorer")

# CUSUM tuning parameters
_CUSUM_SLACK_MULTIPLIER = 0.5   # k = slack * sigma (half-sigma slack)
_CUSUM_RESET_FLOOR = 0.0        # lower CUSUM clipped to 0 (one-sided, upward)
_SCORE_SATURATION_SIGMAS = 4.0  # raw CUSUM value at which score saturates to 10


def calculate_dib_velocity_cusum(procurement_data: dict[str, Any]) -> float:
    """Compute CUSUM anomaly score on DIB import-volume velocity changes.

    Uses a one-sided upper CUSUM against the mean/std derived from the
    provided neutral-country baseline series.  The resulting cumulative
    sum is mapped linearly to [0, 10], saturating at ``_SCORE_SATURATION_SIGMAS``
    standard deviations above baseline.

    Parameters
    ----------
    procurement_data:
        Dictionary with the following optional keys:

        ``baseline`` — list[float]
            Historical volume observations from neutral reference countries.
            Minimum 2 elements required to compute variance.  If absent or
            too short, a default mid-range score (5.0) is returned.

        ``current_series`` — list[float]
            Observed import-volume changes for the adversary/target country
            in chronological order.  If absent or empty, returns 0.0.

        ``slack_multiplier`` — float (optional)
            Override ``_CUSUM_SLACK_MULTIPLIER`` (default 0.5).

    Returns
    -------
    float
        Normalized score in [0.0, 10.0].
    """
    if not procurement_data:
        logger.debug("calculate_dib_velocity_cusum: empty input, returning 0.0")
        return 0.0

    baseline: list[float] = procurement_data.get("baseline") or []
    current_series: list[float] = procurement_data.get("current_series") or []
    slack_k: float = float(procurement_data.get("slack_multiplier", _CUSUM_SLACK_MULTIPLIER))

    if len(current_series) == 0:
        logger.debug("calculate_dib_velocity_cusum: no current_series, returning 0.0")
        return 0.0

    if len(baseline) < 2:
        # Cannot establish a reliable baseline variance — return neutral mid-point
        logger.warning("calculate_dib_velocity_cusum: insufficient baseline data, returning 5.0")
        return 5.0

    mu = sum(baseline) / len(baseline)
    variance = sum((x - mu) ** 2 for x in baseline) / (len(baseline) - 1)
    sigma = variance ** 0.5

    if sigma == 0.0:
        # Zero variance baseline: any deviation is anomalous; score by magnitude
        max_deviation = max(abs(v - mu) for v in current_series)
        score = min(10.0, max_deviation)
        logger.debug("calculate_dib_velocity_cusum: zero-variance baseline, score=%.3f", score)
        return round(score, 4)

    k = slack_k * sigma  # allowable slack

    cusum_pos = 0.0
    for x in current_series:
        cusum_pos = max(_CUSUM_RESET_FLOOR, cusum_pos + (x - mu) - k)

    # Normalise: cusum_pos / (saturation_threshold) → [0, 1] → scale to 10
    saturation = _SCORE_SATURATION_SIGMAS * sigma
    raw_score = min(1.0, cusum_pos / saturation) * 10.0

    score = round(raw_score, 4)
    logger.debug(
        "calculate_dib_velocity_cusum: mu=%.3f sigma=%.3f cusum_pos=%.3f score=%.3f",
        mu, sigma, cusum_pos, score,
    )
    return score
