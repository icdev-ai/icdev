# CUI // SP-CTI
"""Read-side assembly for the Delta Review panel (trust-hitl-02).

The panel needs three payloads and this module builds all three. It holds no
Flask import, no template knowledge and no write path, so it is testable with no
app context — the same separation ``integrity/blueprint.py`` keeps between its
queries and its routes.

The one piece of real logic here is :func:`resolve_span_findings`. Everything
else is projection.

## Why a settlement is derived, never read

``trust_deltas`` is append-only, so a pending delta's ``disposition`` column
says what was true when the row was WRITTEN and nothing ever updates it. Every
"is this settled" answer in this module therefore comes from the presence of a
successor row (``hitl_delta.get_settlement`` / ``delta_chain``). Reading the
column instead is how a settled delta reappears in the queue forever — and how
the panel would show an Approve button on a diff a colleague approved an hour
ago.
"""
from __future__ import annotations

from typing import Any, Optional

from tools.delta_review.constants import (
    DISPOSITION_BADGES,
    SPAN_BADGES,
    SPAN_UNCHANGED,
    STAGE_LABELS,
)
from tools.logging.icdev_logger import get_logger
from tools.quality import hitl_delta

logger = get_logger("icdev.delta_review.review")


def _stage_label(stage: str) -> str:
    return STAGE_LABELS.get(stage, stage.replace("_", " ").title())


def _disposition_badge(disposition: str) -> dict[str, str]:
    label, css = DISPOSITION_BADGES.get(disposition, (disposition.upper(), "badge"))
    return {"label": label, "css": css}


def resolve_span_findings(span: dict[str, Any]) -> dict[str, Any]:
    """Annotate one aligned span with what the revision actually did to it.

    Three verdicts, and the distinction between them is the whole reason a
    reviewer is being shown a diff rather than a pass/fail:

      ``resolved``    the span carried findings before and carries none now.
      ``persisting``  it still carries findings. A revision that changed the
                      wording without clearing the defect is the single most
                      important thing to surface, because the finding COUNT
                      dropping — which is all ``self_correct``'s monotone
                      invariant measures — can hide it when another span's
                      finding went away in the same round.
      ``regressed``   it carried none before and carries some now. The monotone
                      loop cannot produce this on its own, but an override or a
                      manual edit can, and it must not render as progress.

    A span that never carried a finding either side is ``clean`` and gets no
    badge — annotating untouched prose would bury the three verdicts above.
    """
    before = list(span.get("findings_before") or [])
    after = list(span.get("findings_after") or [])
    if before and not after:
        verdict = "resolved"
    elif after and not before:
        verdict = "regressed"
    elif after:
        verdict = "persisting"
    else:
        verdict = "clean"

    label, css = SPAN_BADGES.get(span.get("kind", ""), (span.get("kind", ""), "dr-span"))
    out = dict(span)
    out.update({
        "kind_label": label,
        "kind_css": css,
        "finding_verdict": verdict,
        "findings_before_n": len(before),
        "findings_after_n": len(after),
        # A reviewer scanning a long document needs the rows that matter to be
        # findable without reading every one. Anything changed, or anything
        # carrying a finding on either side, is worth stopping at.
        "notable": span.get("kind") != SPAN_UNCHANGED or bool(before or after),
    })
    return out


def delta_payload(delta: hitl_delta.Delta) -> dict[str, Any]:
    """One delta projected for the side-by-side panel.

    ``settled`` and ``settlement`` are DERIVED from the successor row — see the
    module docstring for why the delta's own ``disposition`` is not consulted
    for a pending row.
    """
    settlement = hitl_delta.get_settlement(delta.delta_id)
    spans = [resolve_span_findings(s) for s in delta.spans]
    doc_before = hitl_delta.document_findings(delta.findings_before)
    doc_after = hitl_delta.document_findings(delta.findings_after)

    effective = settlement.disposition if settlement else delta.disposition
    return {
        "delta": delta.to_dict(),
        "delta_id": delta.delta_id,
        "artifact_id": delta.artifact_id,
        "stage": delta.stage,
        "stage_label": _stage_label(delta.stage),
        "gate": delta.gate,
        "spans": spans,
        "notable_spans": [s for s in spans if s["notable"]],
        "changed_span_count": sum(1 for s in spans if s["kind"] != SPAN_UNCHANGED),
        # The three verdicts a reviewer scans for, counted once so the header can
        # state them without the template re-deriving anything.
        "resolved_count": sum(1 for s in spans if s["finding_verdict"] == "resolved"),
        "persisting_count": sum(1 for s in spans if s["finding_verdict"] == "persisting"),
        "regressed_count": sum(1 for s in spans if s["finding_verdict"] == "regressed"),
        # Findings that anchor to no claim. Dropping these would show a blocked
        # draft with nothing wrong with it — placeholder_guard and citation_guard
        # both report at document level.
        "document_findings_before": doc_before,
        "document_findings_after": doc_after,
        "findings_before_n": delta.findings_before_n,
        "findings_after_n": delta.findings_after_n,
        "net_findings": delta.net_findings,
        "settlement": settlement.to_dict() if settlement else None,
        "settled": settlement is not None,
        "disposition": effective,
        "disposition_badge": _disposition_badge(effective),
        # The Approve/Deny controls render only when this is true. A settled
        # delta keeps its diff — the evidence is the point — but loses its
        # buttons.
        "can_settle": settlement is None and delta.is_pending,
    }


def pending_queue(*, limit: int = 100) -> list[dict[str, Any]]:
    """The review queue: one compact row per delta still awaiting a human.

    Deliberately does NOT call :func:`delta_payload` per row — that would issue a
    settlement lookup for every item on every page load. ``pending_deltas``
    already excludes settled predecessors in one query.
    """
    return [
        {
            "delta_id": d.delta_id,
            "artifact_id": d.artifact_id,
            "artifact_type": d.artifact_type,
            "stage": d.stage,
            "stage_label": _stage_label(d.stage),
            "gate": d.gate,
            "findings_before_n": d.findings_before_n,
            "findings_after_n": d.findings_after_n,
            "net_findings": d.net_findings,
            "changed_span_count": len(d.changed_spans),
            "actor": d.actor,
            "created_at": d.created_at,
        }
        for d in hitl_delta.pending_deltas(limit=limit)
    ]


def artifact_history(artifact_id: str, *, limit: int = 100) -> list[dict[str, Any]]:
    """Every delta for one artifact, oldest first, with derived settlement state."""
    return [
        {
            "delta_id": entry["delta"].delta_id,
            "stage": entry["delta"].stage,
            "stage_label": _stage_label(entry["delta"].stage),
            "gate": entry["delta"].gate,
            "findings_before_n": entry["delta"].findings_before_n,
            "findings_after_n": entry["delta"].findings_after_n,
            "created_at": entry["delta"].created_at,
            "settled": entry["settled"],
            "disposition": (
                entry["settlement"].disposition if entry["settlement"]
                else entry["delta"].disposition
            ),
            "rationale": entry["settlement"].rationale if entry["settlement"] else "",
            "actor": entry["settlement"].actor if entry["settlement"] else entry["delta"].actor,
        }
        for entry in hitl_delta.delta_chain(artifact_id, limit=limit)
    ]


def panel_context(delta_id: Optional[str], *, limit: int = 100) -> dict[str, Any]:
    """Everything the page template needs, in one call.

    When ``delta_id`` is absent (or names a delta that is gone) the panel
    renders the queue alone. A missing delta is reported as ``not_found`` rather
    than silently falling back to the queue — a stale link that quietly shows a
    different page is how a reviewer settles the wrong diff.
    """
    stats = hitl_delta.summary()
    queue = pending_queue(limit=limit)
    selected: Optional[dict[str, Any]] = None
    not_found = ""

    if delta_id:
        delta = hitl_delta.get_delta(delta_id)
        if delta is None:
            not_found = delta_id
        else:
            selected = delta_payload(delta)
            selected["history"] = artifact_history(delta.artifact_id, limit=limit)

    return {
        "summary": stats,
        "queue": queue,
        "selected": selected,
        "not_found": not_found,
        # capability_consumption's discipline: an unmigrated database reports
        # "nothing can be known", never a confident zero. The template says so
        # instead of drawing an empty queue that looks like a clean board.
        "telemetry_available": bool(stats.get("telemetry_available")),
    }


def build_span_rows(delta: hitl_delta.Delta, *, only_notable: bool = False) -> list[dict]:
    """Span rows for a caller that wants them without the rest of the payload.

    Used by the IQE ``delta_review.spans`` collection, which flattens spans
    across deltas so a question like "which claims still carry a finding after
    revision" is answerable without opening each panel by hand.
    """
    rows = [resolve_span_findings(s) for s in delta.spans]
    if only_notable:
        rows = [r for r in rows if r["notable"]]
    for row in rows:
        row["delta_id"] = delta.delta_id
        row["artifact_id"] = delta.artifact_id
    return rows


__all__ = [
    "resolve_span_findings",
    "delta_payload",
    "pending_queue",
    "artifact_history",
    "panel_context",
    "build_span_rows",
]
