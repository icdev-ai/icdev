# CUI // SP-CTI
"""ICDEV Cortex — unified AI facade over the platform's retrieval backends."""
from .api import (
    CORTEX_CLASSIFY_FUNCTION,
    CORTEX_COMPLETE_FUNCTION,
    CORTEX_EXTRACT_FUNCTION,
    classify,
    complete,
    extract,
)
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
    "CORTEX_CLASSIFY_FUNCTION",
    "CORTEX_COMPLETE_FUNCTION",
    "CORTEX_EXTRACT_FUNCTION",
    "Citation",
    "CortexContext",
    "CortexResult",
    "CortexSearchResult",
    "GovernanceReport",
    "classify",
    "complete",
    "extract",
]
