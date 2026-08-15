# CUI // SP-CTI
"""Delta Review canvas constants (trust-hitl-02).

Vocabularies are RE-EXPORTED from :mod:`tools.quality.hitl_delta`, never
redeclared. A canvas that keeps its own copy of a status list is the drift bug
CLAUDE.md names for CHECK constraints, and it fails the same way: the store
accepts a value the panel then renders as blank.
"""
from __future__ import annotations

from tools.quality.hitl_delta import (
    DELTA_STAGES,
    DISPOSITION_APPROVED,
    DISPOSITION_DENIED,
    DISPOSITION_PENDING,
    DISPOSITIONS,
    SPAN_ADDED,
    SPAN_CHANGED,
    SPAN_KINDS,
    SPAN_REMOVED,
    SPAN_UNCHANGED,
    STAGE_MANUAL_EDIT,
    STAGE_OVERRIDE,
    STAGE_SELF_CORRECTION,
    STAGE_SETTLEMENT,
)

__all__ = [
    "FEATURE_FLAG", "CANVAS_KEY", "URL_ROOT",
    "DELTA_STAGES", "DISPOSITIONS",
    "DISPOSITION_PENDING", "DISPOSITION_APPROVED", "DISPOSITION_DENIED",
    "STAGE_SELF_CORRECTION", "STAGE_OVERRIDE", "STAGE_MANUAL_EDIT",
    "STAGE_SETTLEMENT",
    "SPAN_KINDS", "SPAN_UNCHANGED", "SPAN_CHANGED", "SPAN_REMOVED", "SPAN_ADDED",
    "SPAN_BADGES", "STAGE_LABELS", "DISPOSITION_BADGES",
    "IQE_COLLECTIONS", "IQE_EXAMPLES",
    "DEFAULT_LIMIT", "MAX_LIMIT", "MIN_RATIONALE_CHARS",
]

FEATURE_FLAG = "ICDEV_DELTA_REVIEW_ENABLED"
CANVAS_KEY = "delta_review"
URL_ROOT = "/delta-review"

#: Page-list size; callers widen with ``?limit=``.
DEFAULT_LIMIT = 100
MAX_LIMIT = 500

#: A rationale shorter than this is refused by the API. Not a style rule — an
#: approval reading "ok" is the same unauditable artifact as an empty one, and
#: ``trust_gate`` invariant 4 exists precisely because that is what an override
#: degenerates into when nothing enforces it. The floor is deliberately low:
#: this is a smell test for an empty gesture, not an essay requirement.
MIN_RATIONALE_CHARS = 10

#: Span kind → (label, CSS class). The panel renders both sides of every span,
#: so an UNCHANGED row is drawn too — a diff that hides context makes a reviewer
#: guess whether a claim they cannot see was touched.
SPAN_BADGES: dict[str, tuple[str, str]] = {
    SPAN_UNCHANGED: ("unchanged", "dr-span--unchanged"),
    SPAN_CHANGED: ("changed", "dr-span--changed"),
    SPAN_REMOVED: ("removed", "dr-span--removed"),
    SPAN_ADDED: ("added", "dr-span--added"),
}

STAGE_LABELS: dict[str, str] = {
    STAGE_SELF_CORRECTION: "Self-correction",
    STAGE_OVERRIDE: "HITL override",
    STAGE_MANUAL_EDIT: "Manual edit",
    STAGE_SETTLEMENT: "Settlement",
}

DISPOSITION_BADGES: dict[str, tuple[str, str]] = {
    DISPOSITION_PENDING: ("PENDING", "badge-warning"),
    DISPOSITION_APPROVED: ("APPROVED", "badge-success"),
    DISPOSITION_DENIED: ("DENIED", "badge-danger"),
}

#: IQE collections this canvas registers. Kept here so the blueprint's
#: ``/api/iqe-query`` route and ``args/component_registry.yaml`` cannot drift
#: from the adapter.
IQE_COLLECTIONS = (
    "delta_review.deltas",
    "delta_review.settlements",
    "delta_review.spans",
)

#: Quick-pick chips for the shared IQE widget.
IQE_EXAMPLES = (
    {"label": "Pending deltas", "query": "show all pending deltas"},
    {"label": "Approved without rationale",
     "query": "show settlements where the rationale is empty"},
    {"label": "Blocked by claim_guard",
     "query": "show deltas whose blocking gate was claim_guard"},
)
