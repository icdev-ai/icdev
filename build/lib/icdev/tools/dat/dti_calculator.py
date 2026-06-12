# CUI // SP-CTI
"""Diplomatic Tension Index (DTI) Calculator.

Pure-function computation of a DTI score in [0.0, 1.0] from a unified data
manifest dict. No database dependency — suitable for pipelines and unit tests.

Unified manifest format::

    {
        "cables": [
            {
                "tension_level": "high",       # low | medium | high | critical
                "received_at": "2026-05-16T00:00:00+00:00",  # ISO-8601
            },
            ...
        ],
        "schedules": [                          # UNSC events
            {
                "emergency": True,
                "veto_cast": False,
                "walkout": False,
            },
            ...
        ],
        "metadata": [                           # back-channel communications
            {
                "escalation_flag": True,
                "communication_breakdown": False,
                "frequency_delta": -0.5,        # negative = reduced contact
            },
            ...
        ],
    }

DTI scoring weights (mirrors tools.strategos.dat):
    Cable tension signals   40 %
    UNSC meeting stress     30 %
    Back-channel patterns   30 %
"""
from __future__ import annotations

import math
from datetime import datetime, timezone

CABLE_WEIGHT = 0.40
UNSC_WEIGHT = 0.30
BACKCHANNEL_WEIGHT = 0.30

_TENSION_WEIGHTS: dict[str, float] = {
    "low": 0.1,
    "medium": 0.4,
    "high": 0.75,
    "critical": 1.0,
}


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _clamp(v: float, lo: float = 0.0, hi: float = 1.0) -> float:
    return max(lo, min(hi, v))


def _score_cables(cables: list[dict]) -> float:
    """Decay-weighted average tension level, normalised to [0.0, 1.0]."""
    if not cables:
        return 0.0

    now = _now()
    total_weight = 0.0
    weighted_tension = 0.0

    for cable in cables:
        tension_level = str(cable.get("tension_level", "low")).lower()
        received_at = cable.get("received_at")
        try:
            ts = datetime.fromisoformat(str(received_at).replace("Z", "+00:00"))
            if ts.tzinfo is None:
                ts = ts.replace(tzinfo=timezone.utc)
            age_days = max(0.0, (now - ts).total_seconds() / 86400)
            decay = math.exp(-0.05 * age_days)
        except Exception:
            decay = 0.5

        w = _TENSION_WEIGHTS.get(tension_level, 0.1)
        weighted_tension += w * decay
        total_weight += decay

    raw = weighted_tension / total_weight if total_weight > 0 else 0.0
    return _clamp(raw)


def _score_schedules(schedules: list[dict]) -> float:
    """UNSC meeting stress score normalised to [0.0, 1.0].

    Scoring per event: emergency +25 pts, veto +20 pts, walkout +15 pts.
    A density bonus of up to 10 pts rewards sustained meeting pressure.
    Maximum possible raw score is 60 pts/event + 10 density = 70 pts → capped at 1.0.
    """
    if not schedules:
        return 0.0

    n = len(schedules)
    emergency_count = sum(1 for s in schedules if s.get("emergency"))
    veto_count = sum(1 for s in schedules if s.get("veto_cast"))
    walkout_count = sum(1 for s in schedules if s.get("walkout"))

    raw_pts = (emergency_count * 25 + veto_count * 20 + walkout_count * 15) / n
    density_bonus = min(10.0, n * 0.5)
    return _clamp((raw_pts + density_bonus) / 100.0)


def _score_metadata(metadata: list[dict]) -> float:
    """Back-channel communication tension score normalised to [0.0, 1.0].

    Escalation rate  × 0.50
    Breakdown rate   × 0.35
    Neg-freq rate    × 0.15
    """
    if not metadata:
        return 0.0

    total = len(metadata)
    escalations = sum(1 for m in metadata if m.get("escalation_flag"))
    breakdowns = sum(1 for m in metadata if m.get("communication_breakdown"))
    neg_freq = sum(1 for m in metadata if float(m.get("frequency_delta", 0.0) or 0.0) < -0.2)

    esc_rate = escalations / total
    brk_rate = breakdowns / total
    freq_rate = neg_freq / total

    raw = esc_rate * 0.50 + brk_rate * 0.35 + freq_rate * 0.15
    return _clamp(raw)


def compute_dti_from_manifest(manifest: dict) -> float:
    """Compute a Diplomatic Tension Index (DTI) score from a unified data manifest.

    Args:
        manifest: dict with keys ``cables``, ``schedules``, and ``metadata``
                  (each a list of signal dicts — see module docstring for schema).

    Returns:
        float in [0.0, 1.0].  0.0 = no tension; 1.0 = maximum tension.
    """
    cables = manifest.get("cables") or []
    schedules = manifest.get("schedules") or []
    metadata = manifest.get("metadata") or []

    cable_score = _score_cables(cables)
    schedule_score = _score_schedules(schedules)
    metadata_score = _score_metadata(metadata)

    dti = _clamp(
        cable_score * CABLE_WEIGHT
        + schedule_score * UNSC_WEIGHT
        + metadata_score * BACKCHANNEL_WEIGHT
    )
    return round(dti, 4)
