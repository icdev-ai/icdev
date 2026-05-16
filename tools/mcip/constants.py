# CUI // SP-CTI
"""MCIP DAT constants — source types, DTI weights, tension bands."""
from __future__ import annotations

DAT_SOURCE_TYPES: list[str] = [
    "cable_traffic",
    "unsc_schedule",
    "backchannel_metadata",
]

# Contribution weight per source type; must sum to 1.0
DTI_WEIGHTS: dict[str, float] = {
    "cable_traffic": 0.45,
    "unsc_schedule": 0.30,
    "backchannel_metadata": 0.25,
}

# Inclusive [low, high] score ranges for tension band labels
DTI_BANDS: dict[str, tuple[int, int]] = {
    "low": (0, 33),
    "moderate": (34, 66),
    "high": (67, 100),
}

DTI_UPDATE_INTERVAL_HOURS: int = 6

SOURCE_LABELS: dict[str, str] = {
    "cable_traffic": "Cable Traffic",
    "unsc_schedule": "UNSC Schedule",
    "backchannel_metadata": "Back-Channel Metadata",
}

SOURCE_COLORS: dict[str, str] = {
    "cable_traffic": "#e94560",
    "unsc_schedule": "#f59e0b",
    "backchannel_metadata": "#a78bfa",
}

BAND_COLORS: dict[str, str] = {
    "low": "#10b981",
    "moderate": "#f59e0b",
    "high": "#e94560",
}


def classify_dti(score: float) -> str:
    """Return the tension band label for *score*."""
    s = int(score)
    for band, (lo, hi) in DTI_BANDS.items():
        if lo <= s <= hi:
            return band
    return "high"
