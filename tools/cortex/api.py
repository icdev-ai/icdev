# CUI // SP-CTI
"""ICDEV Cortex public API facade.

Import Cortex capabilities from here (or from ``tools.cortex`` directly);
the per-capability modules (``analyst``, …) are implementation detail.
Later ctx-* epics add ``search()``, ``complete()``, etc. alongside ``ask()``.
"""
from .analyst import CortexAnalystError, CortexQueryBlocked, ask
from .schemas import (
    CORTEX_BACKENDS,
    Citation,
    CortexContext,
    CortexResult,
    CortexSearchResult,
    GovernanceReport,
)

__all__ = [
    "CORTEX_BACKENDS",
    "Citation",
    "CortexAnalystError",
    "CortexContext",
    "CortexQueryBlocked",
    "CortexResult",
    "CortexSearchResult",
    "GovernanceReport",
    "ask",
]
