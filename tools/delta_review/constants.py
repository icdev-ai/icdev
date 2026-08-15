# CUI // SP-CTI
"""Delta Review canvas constants (trust-hitl-02).

Every vocabulary here is RE-EXPORTED, never redeclared. A canvas that keeps its
own copy of a status list is the drift bug CLAUDE.md names for CHECK
constraints, and it fails the same way: the store accepts a value the panel then
renders as blank. PR #1684 was closed for exactly that — it shipped its own
``STAGE_*`` / ``SPAN_*`` / ``DISPOSITION_*`` lists and none of them overlapped
the ones the store validates against.

Two vocabularies, from two modules, because a delta's *state* lives in two
places by design:

``tools.quality.hitl_delta``        what the delta IS — ``STAGES``, ``OPS``.
``tools.agent_runtime.approval_inbox``  what a human DID about it —
                                   ``STATE_*`` / ``RESOLUTION_*``.

There is no disposition column on ``trust_deltas`` and this module does not
invent one. ``trust_deltas`` is append-only evidence; the decision is mutable
state on the ``approval_items`` row reached through ``Delta.approval_item_id``.
"""
from __future__ import annotations

from tools.agent_runtime.approval_inbox import (
    RESOLUTION_APPROVED,
    RESOLUTION_DENIED,
    STATE_CANCELLED,
    STATE_EXPIRED,
    STATE_PENDING,
    STATE_RESOLVED,
)
from tools.quality.hitl_delta import (
    CHANGED_OPS,
    OP_ADDED,
    OP_MODIFIED,
    OP_REMOVED,
    OP_UNCHANGED,
    OPS,
    STAGE_CORRECTION,
    STAGE_DRAFT,
    STAGE_EXPORT,
    STAGE_PROMOTE,
    STAGE_REVIEW,
    STAGES,
)

__all__ = [
    "FEATURE_FLAG", "CANVAS_KEY", "URL_ROOT",
    "STAGES", "STAGE_DRAFT", "STAGE_REVIEW", "STAGE_PROMOTE", "STAGE_EXPORT",
    "STAGE_CORRECTION", "STAGE_LABELS",
    "OPS", "CHANGED_OPS", "OP_UNCHANGED", "OP_MODIFIED", "OP_ADDED", "OP_REMOVED",
    "OP_BADGES",
    "STATE_PENDING", "STATE_RESOLVED", "STATE_EXPIRED", "STATE_CANCELLED",
    "RESOLUTION_APPROVED", "RESOLUTION_DENIED",
    "REVIEW_PENDING", "REVIEW_APPROVED", "REVIEW_DENIED", "REVIEW_LAPSED",
    "REVIEW_SUPERSEDED", "REVIEW_BADGES",
    "VERDICT_RESOLVED", "VERDICT_PERSISTING", "VERDICT_REGRESSED", "VERDICT_CLEAN",
    "VERDICT_BADGES",
    "IQE_COLLECTIONS", "IQE_EXAMPLES",
    "DEFAULT_LIMIT", "MAX_LIMIT", "MIN_RATIONALE_CHARS",
]

FEATURE_FLAG = "ICDEV_DELTA_REVIEW_ENABLED"
CANVAS_KEY = "delta_review"
URL_ROOT = "/delta-review"

#: Page-list size; callers widen with ``?limit=``.
DEFAULT_LIMIT = 100
MAX_LIMIT = 500

#: A rationale shorter than this is refused by the settle route.
#:
#: This floor exists HERE and not in the store, and that is not an oversight.
#: ``hitl_delta.settle_delta`` accepts an empty ``reason`` and substitutes
#: ``"delta <id> approved"`` so a CLI or an expiry sweep still produces a
#: well-formed ``agent_approval_log`` row. That default is a *label*, not a
#: rationale: it restates the action and says nothing about the evidence. The
#: panel is the surface where a human is looking at the diff, so it is the one
#: surface that can insist. Not a style rule — an approval reading "ok" is the
#: same unauditable artifact as an empty one (``trust_gate`` invariant 4), and
#: the floor is deliberately low because this is a smell test for an empty
#: gesture, not an essay requirement.
MIN_RATIONALE_CHARS = 10

#: Span op → (label, CSS class). The panel renders both sides of EVERY span,
#: unchanged ones included — a diff that hides context makes a reviewer guess
#: whether a claim they cannot see was touched.
OP_BADGES: dict[str, tuple[str, str]] = {
    OP_UNCHANGED: ("unchanged", "dr-span--unchanged"),
    OP_MODIFIED: ("modified", "dr-span--modified"),
    OP_REMOVED: ("removed", "dr-span--removed"),
    OP_ADDED: ("added", "dr-span--added"),
}

#: Stage → human label. These are the five TRUST pipeline points a delta can be
#: recorded at, not a workflow status.
STAGE_LABELS: dict[str, str] = {
    STAGE_DRAFT: "Draft",
    STAGE_REVIEW: "Review",
    STAGE_PROMOTE: "Promote",
    STAGE_EXPORT: "Export",
    STAGE_CORRECTION: "Correction",
}

# --- Review state ----------------------------------------------------------
# DERIVED, never stored. These five keys are what the panel renders, and each
# one is computed at read time from the approval item plus the correction chain
# — see review.review_state. They are presentation keys for a state that lives
# in approval_items; they are deliberately NOT a disposition vocabulary for
# trust_deltas, which has no such column.
REVIEW_PENDING = "pending"
REVIEW_APPROVED = "approved"
REVIEW_DENIED = "denied"
#: Expired or cancelled. Named apart from `denied` because a lapsed ask is not a
#: refusal — nobody looked. Collapsing the two is how a timeout starts reading
#: as a decision.
REVIEW_LAPSED = "lapsed"
#: A later correction supersedes this delta, so it is no longer the thing to
#: review. Its evidence stands; its queue slot does not.
REVIEW_SUPERSEDED = "superseded"

REVIEW_BADGES: dict[str, tuple[str, str]] = {
    REVIEW_PENDING: ("PENDING", "badge-warning"),
    REVIEW_APPROVED: ("APPROVED", "badge-success"),
    REVIEW_DENIED: ("DENIED", "badge-danger"),
    REVIEW_LAPSED: ("LAPSED", "badge"),
    REVIEW_SUPERSEDED: ("SUPERSEDED", "badge"),
}

# --- Per-span finding verdict ----------------------------------------------
# What the revision did to ONE claim. The whole reason a reviewer is shown a
# diff rather than a pass/fail.
VERDICT_RESOLVED = "resolved"
VERDICT_PERSISTING = "persisting"
VERDICT_REGRESSED = "regressed"
VERDICT_CLEAN = "clean"

VERDICT_BADGES: dict[str, tuple[str, str]] = {
    VERDICT_RESOLVED: ("resolved", "badge-success"),
    VERDICT_PERSISTING: ("still flagged", "badge-warning"),
    VERDICT_REGRESSED: ("newly flagged", "badge-danger"),
}

#: IQE collections this canvas registers. Kept here so the blueprint's
#: ``/api/iqe-query`` route and ``args/component_registry.yaml`` cannot drift
#: from the adapter.
IQE_COLLECTIONS = (
    "delta_review.deltas",
    "delta_review.settlements",
    "delta_review.spans",
    "delta_review.decisions",
)

#: Quick-pick chips for the shared IQE widget.
IQE_EXAMPLES = (
    {"label": "Pending deltas", "query": "show deltas at the promote stage"},
    {"label": "Recorded without a rationale",
     "query": "show deltas where the rationale is empty"},
    {"label": "Still flagged after revision",
     "query": "show spans whose finding verdict is persisting"},
)
