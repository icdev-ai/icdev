# CUI // SP-CTI
"""Read-side assembly for the Delta Review panel (trust-hitl-02).

The panel needs a queue, a board header and one delta's full projection, and
this module builds all three. It holds no Flask import, no template knowledge
and no write path, so it is testable with no app context — the separation
``integrity/blueprint.py`` keeps between its queries and its routes.

Everything here is projection except one function, :func:`resolve_span_findings`,
and one derivation, :func:`review_state`.

## How a finding gets attached to a span

``compute_delta`` emits spans carrying ``before_index`` / ``after_index``:
0-based positions in ``hitl_delta.anchored_claims(before_text)`` and
``anchored_claims(after_text)``. Every guard in the TRUST spine numbers its
findings ``item_number`` **1-based over exactly that decomposition** — that is
the property ``self_correct.target_findings`` already relies on, and
``claim_gate`` is written against it directly.

So ``item_number - 1 == before_index`` is the join, and it is why the diff is
claim-anchored rather than a line diff: a reviewer sees not that a paragraph
moved but that the unsupported claim inside it is why it was blocked.

Nothing derived here is persisted. ``findings_before`` and ``findings_after``
are the stored evidence; the counts, the net change, the per-span verdicts and
the review state are all computed at read time, every time.

## Where a decision lives

``trust_deltas`` is append-only EVIDENCE and owns no disposition column. The
human's answer is mutable state on the ``approval_items`` row reached through
``Delta.approval_item_id``. :func:`review_state` reads it there. Storing a
settlement as a successor delta instead — which is what PR #1684 did — gives
two tables an opinion about the same fact and no rule for which one wins.
"""
from __future__ import annotations

from typing import Any, Optional

from tools.delta_review.constants import (
    CHANGED_OPS,
    OP_BADGES,
    RESOLUTION_APPROVED,
    RESOLUTION_DENIED,
    REVIEW_APPROVED,
    REVIEW_BADGES,
    REVIEW_DENIED,
    REVIEW_LAPSED,
    REVIEW_PENDING,
    REVIEW_SUPERSEDED,
    STAGE_LABELS,
    STATE_PENDING,
    STATE_RESOLVED,
    VERDICT_BADGES,
    VERDICT_CLEAN,
    VERDICT_PERSISTING,
    VERDICT_REGRESSED,
    VERDICT_RESOLVED,
)
from tools.logging.icdev_logger import get_logger
from tools.quality import hitl_delta

logger = get_logger("icdev.delta_review.review")


def _stage_label(stage: str) -> str:
    return STAGE_LABELS.get(stage, (stage or "").replace("_", " ").title() or "—")


def _review_badge(key: str) -> dict[str, str]:
    label, css = REVIEW_BADGES.get(key, (key.upper(), "badge"))
    return {"key": key, "label": label, "css": css}


# ---------------------------------------------------------------------------
# Claim-anchored findings
# ---------------------------------------------------------------------------
def findings_by_claim(findings: Any) -> dict[int, list[dict]]:
    """Group findings by the 0-based claim index each one anchors to.

    ``item_number`` is 1-based over the same decomposition the spans index, so
    the conversion is ``item_number - 1``. A finding with no usable
    ``item_number`` is dropped from this map and picked up by
    :func:`document_findings` instead — never silently discarded, because a
    blocked draft whose findings all vanished from the panel reads as a draft
    with nothing wrong with it.
    """
    out: dict[int, list[dict]] = {}
    for finding in findings or []:
        if not isinstance(finding, dict):
            continue
        number = finding.get("item_number")
        if isinstance(number, bool) or not isinstance(number, int) or number < 1:
            continue
        out.setdefault(number - 1, []).append(finding)
    return out


def document_findings(findings: Any, claimed: set[int]) -> list[dict]:
    """Findings that anchor to no span in this delta.

    Two sources. ``placeholder_guard`` and ``citation_guard`` report at document
    level and carry no meaningful ``item_number`` at all. And a claim-anchored
    finding can point past the end of the decomposition when the guard ran over
    a different revision of the text than the delta stored.

    Both belong on screen. Dropping them shows a blocked draft with nothing
    wrong with it, which is worse than the audit line this panel replaces.
    """
    out: list[dict] = []
    for finding in findings or []:
        if not isinstance(finding, dict):
            out.append({"issue": str(finding), "detail": ""})
            continue
        number = finding.get("item_number")
        anchored = (
            not isinstance(number, bool)
            and isinstance(number, int)
            and number >= 1
            and (number - 1) in claimed
        )
        if not anchored:
            out.append(finding)
    return out


def resolve_span_findings(
    span: dict[str, Any],
    findings_before: Optional[list[dict]] = None,
    findings_after: Optional[list[dict]] = None,
) -> dict[str, Any]:
    """Annotate one aligned span with what the revision actually did to it.

    Four verdicts, and the distinction between the middle two is the whole
    reason a reviewer is being shown a diff rather than a pass/fail:

      ``resolved``    carried findings before, carries none now.
      ``persisting``  still carries a finding. **The central case.** A revision
                      that changed the wording without clearing the defect is
                      invisible to ``self_correct``'s monotone invariant, which
                      only compares the TOTAL finding count — so it hides
                      whenever some other span's finding cleared in the same
                      round.
      ``regressed``   carried none before, carries some now. The monotone loop
                      cannot produce this on its own, but an override or a
                      manual edit can, and it must not render as progress.
      ``clean``       carried none either side. Gets no badge; annotating
                      untouched prose would bury the three above.
    """
    before = list(findings_before or [])
    after = list(findings_after or [])
    if before and not after:
        verdict = VERDICT_RESOLVED
    elif after and not before:
        verdict = VERDICT_REGRESSED
    elif after:
        verdict = VERDICT_PERSISTING
    else:
        verdict = VERDICT_CLEAN

    op = span.get("op", "")
    label, css = OP_BADGES.get(op, (op or "span", "dr-span"))
    verdict_label, verdict_css = VERDICT_BADGES.get(verdict, ("", ""))

    out = dict(span)
    out.update({
        "op_label": label,
        "op_css": css,
        "finding_verdict": verdict,
        "verdict_label": verdict_label,
        "verdict_css": verdict_css,
        "findings_before": before,
        "findings_after": after,
        "findings_before_n": len(before),
        "findings_after_n": len(after),
        # A reviewer scanning a long document needs the rows that matter to be
        # findable without reading every one. Anything changed, or anything
        # carrying a finding on either side, is worth stopping at.
        "notable": op in CHANGED_OPS or bool(before or after),
    })
    return out


def annotate_spans(delta: hitl_delta.Delta) -> tuple[list[dict], list[dict], list[dict]]:
    """``(spans, document_findings_before, document_findings_after)``.

    One pass. Returns the document-level leftovers alongside the spans because
    the two are complementary halves of one partition — computing them in
    separate calls is how the same finding ends up rendered twice, or nowhere.
    """
    before_map = findings_by_claim(delta.findings_before)
    after_map = findings_by_claim(delta.findings_after)

    rows: list[dict] = []
    claimed_before: set[int] = set()
    claimed_after: set[int] = set()
    for span in delta.spans or []:
        if not isinstance(span, dict):
            continue
        b_index = span.get("before_index", -1)
        a_index = span.get("after_index", -1)
        b_index = b_index if isinstance(b_index, int) else -1
        a_index = a_index if isinstance(a_index, int) else -1
        b_findings = before_map.get(b_index, []) if b_index >= 0 else []
        a_findings = after_map.get(a_index, []) if a_index >= 0 else []
        if b_index >= 0:
            claimed_before.add(b_index)
        if a_index >= 0:
            claimed_after.add(a_index)
        rows.append(resolve_span_findings(span, b_findings, a_findings))

    return (
        rows,
        document_findings(delta.findings_before, claimed_before),
        document_findings(delta.findings_after, claimed_after),
    )


# ---------------------------------------------------------------------------
# Review state — derived from the approval item, never from the delta
# ---------------------------------------------------------------------------
def review_state(delta: hitl_delta.Delta, *, superseded: bool = False) -> dict[str, Any]:
    """What a human has done about this delta.

    Resolution order, and each step is deliberate:

    1. **Superseded** wins over everything. A delta a later correction replaced
       is no longer the thing to review, whatever its ask says.
    2. **No ``approval_item_id``** is PENDING, not settled. The ask failed to
       queue, and treating a failed enqueue as an answer is the same shape of
       bug as an expiry auto-approving. Same call ``pending_deltas`` makes.
    3. **An absent item** is PENDING for the same reason — the mutable row was
       pruned or is on another tenant; nobody answered it either way.
    4. ``resolved`` splits on ``resolution``; ``expired`` and ``cancelled``
       collapse to LAPSED, which is deliberately not DENIED. Nobody looked.
    """
    if superseded:
        return {
            "key": REVIEW_SUPERSEDED,
            "badge": _review_badge(REVIEW_SUPERSEDED),
            "settled": True,
            "can_settle": False,
            "item": None,
        }

    item = None
    if delta.approval_item_id:
        try:
            from tools.agent_runtime.approval_inbox import get as get_item

            item = get_item(delta.approval_item_id)
        except Exception as exc:  # noqa: BLE001 — an inbox outage is not a decision
            logger.warning(
                "delta_review: approval item %s unreadable (%s) — %s reports pending",
                delta.approval_item_id, exc, delta.delta_id,
            )
            item = None

    if item is None or item.state == STATE_PENDING:
        return {
            "key": REVIEW_PENDING,
            "badge": _review_badge(REVIEW_PENDING),
            "settled": False,
            # An ask that never queued cannot be answered through the inbox, so
            # the panel must not offer a button that would 500 on click.
            "can_settle": bool(delta.approval_item_id) and item is not None,
            "no_ask": not delta.approval_item_id or item is None,
            "item": item.to_dict() if item is not None else None,
        }

    if item.state == STATE_RESOLVED:
        key = REVIEW_APPROVED if item.resolution == RESOLUTION_APPROVED else (
            REVIEW_DENIED if item.resolution == RESOLUTION_DENIED else REVIEW_LAPSED
        )
    else:
        key = REVIEW_LAPSED

    return {
        "key": key,
        "badge": _review_badge(key),
        "settled": True,
        "can_settle": False,
        "item": item.to_dict(),
    }


# ---------------------------------------------------------------------------
# Projections
# ---------------------------------------------------------------------------
def _counts(delta: hitl_delta.Delta) -> dict[str, int]:
    """Finding counts, DERIVED. Never stored — that is the contract."""
    before = len(delta.findings_before or [])
    after = len(delta.findings_after or [])
    return {
        "findings_before_n": before,
        "findings_after_n": after,
        "net_findings": after - before,
        "resolved_findings_n": len(delta.findings_resolved),
        "introduced_findings_n": len(delta.findings_introduced),
    }


def queue_row(delta: hitl_delta.Delta, state: dict[str, Any]) -> dict[str, Any]:
    """One compact row for the queue table. Carries no artifact text."""
    return {
        "delta_id": delta.delta_id,
        "artifact_id": delta.artifact_id,
        "stage": delta.stage,
        "stage_label": _stage_label(delta.stage),
        "actor": delta.actor,
        "rationale": delta.rationale,
        "is_no_op": delta.is_no_op,
        "changed_span_count": len(delta.changed_spans),
        "review": state,
        "created_at": delta.created_at,
        **_counts(delta),
    }


def delta_payload(delta: hitl_delta.Delta, *, superseded: Optional[bool] = None) -> dict[str, Any]:
    """One delta projected for the side-by-side panel.

    ``superseded`` is computed here when the caller has not already established
    it, from the artifact's own deltas rather than from a capped board window —
    a panel that says PENDING because the correction fell outside the last 100
    rows would invite a reviewer to settle a diff that no longer applies.
    """
    if superseded is None:
        superseded = any(
            other.supersedes_delta_id == delta.delta_id
            for other in hitl_delta.list_deltas(artifact_id=delta.artifact_id, limit=500)
        )

    spans, doc_before, doc_after = annotate_spans(delta)
    state = review_state(delta, superseded=superseded)

    return {
        "delta": delta.to_dict(),
        "delta_id": delta.delta_id,
        "artifact_id": delta.artifact_id,
        "stage": delta.stage,
        "stage_label": _stage_label(delta.stage),
        "actor": delta.actor,
        "rationale": delta.rationale,
        "is_no_op": delta.is_no_op,
        "before_hash": delta.before_hash,
        "after_hash": delta.after_hash,
        "supersedes_delta_id": delta.supersedes_delta_id,
        "spans": spans,
        "notable_spans": [s for s in spans if s["notable"]],
        "changed_span_count": sum(1 for s in spans if s["op"] in CHANGED_OPS),
        # The three verdicts a reviewer scans for, counted once so the header
        # can state them without the template re-deriving anything.
        "resolved_count": sum(1 for s in spans if s["finding_verdict"] == VERDICT_RESOLVED),
        "persisting_count": sum(1 for s in spans if s["finding_verdict"] == VERDICT_PERSISTING),
        "regressed_count": sum(1 for s in spans if s["finding_verdict"] == VERDICT_REGRESSED),
        "document_findings_before": doc_before,
        "document_findings_after": doc_after,
        "findings_resolved": delta.findings_resolved,
        "findings_introduced": delta.findings_introduced,
        "review": state,
        "settled": state["settled"],
        # The Approve/Deny controls render only when this is true. A settled
        # delta KEEPS its diff — the evidence is the point — and loses only its
        # buttons.
        "can_settle": state["can_settle"],
        **_counts(delta),
    }


def board(*, limit: int = 100) -> dict[str, Any]:
    """The queue and the header counters, from ONE window of deltas.

    Deliberately one read. ``hitl_delta.pending_deltas`` is the sanctioned queue
    query and :func:`pending_queue` calls it, but deriving the table from that
    call and the counters above it from a second, independently-capped call lets
    the header disagree with the table it sits on top of — a board reading
    "3 awaiting review" over four rows. ``tests/test_delta_review.py`` pins this
    window's pending set to ``pending_deltas``' own answer so the local
    derivation cannot drift from the landed contract it reproduces.
    """
    deltas = hitl_delta.list_deltas(limit=limit)
    superseded = {d.supersedes_delta_id for d in deltas if d.supersedes_delta_id}

    rows: list[dict[str, Any]] = []
    summary = {
        "total": len(deltas),
        REVIEW_PENDING: 0,
        REVIEW_APPROVED: 0,
        REVIEW_DENIED: 0,
        REVIEW_LAPSED: 0,
        REVIEW_SUPERSEDED: 0,
        "no_op": 0,
        "regressions": 0,
        "by_stage": {},
    }
    for delta in deltas:
        state = review_state(delta, superseded=delta.delta_id in superseded)
        summary[state["key"]] = summary.get(state["key"], 0) + 1
        summary["by_stage"][delta.stage] = summary["by_stage"].get(delta.stage, 0) + 1
        if delta.is_no_op:
            summary["no_op"] += 1
        if delta.findings_introduced:
            summary["regressions"] += 1
        if state["key"] == REVIEW_PENDING:
            rows.append(queue_row(delta, state))

    return {"queue": rows, "summary": summary}


def pending_queue(*, limit: int = 100) -> list[dict[str, Any]]:
    """The review queue via the landed ``pending_deltas``.

    Kept as the public read for anything that wants the queue alone. The panel
    uses :func:`board` instead so its counters and its table come from one
    window; both agree by construction and a test holds them to it.
    """
    deltas = hitl_delta.pending_deltas(limit=limit)
    superseded = {d.supersedes_delta_id for d in deltas if d.supersedes_delta_id}
    return [
        queue_row(d, review_state(d, superseded=d.delta_id in superseded))
        for d in deltas
    ]


def artifact_timeline(artifact_id: str, *, limit: int = 100) -> list[dict[str, Any]]:
    """Every delta recorded against one artifact, oldest first.

    ``list_deltas``, NOT ``revision_chain``. The two answer different questions
    and confusing them is easy: ``revision_chain`` walks ONE delta's correction
    chain through ``supersedes_delta_id``, so an artifact whose deltas were
    recorded independently at draft, promote and export has three chains of one
    and ``revision_chain`` on any of them returns a single row. The timeline is
    the artifact's history; the chain is one delta's corrections.
    """
    deltas = hitl_delta.list_deltas(artifact_id=artifact_id, limit=limit)
    superseded = {d.supersedes_delta_id for d in deltas if d.supersedes_delta_id}
    rows = [
        {
            "delta_id": d.delta_id,
            "stage": d.stage,
            "stage_label": _stage_label(d.stage),
            "actor": d.actor,
            "rationale": d.rationale,
            "is_no_op": d.is_no_op,
            "supersedes_delta_id": d.supersedes_delta_id,
            "review": review_state(d, superseded=d.delta_id in superseded),
            "created_at": d.created_at,
            **_counts(d),
        }
        for d in deltas
    ]
    rows.reverse()  # list_deltas is newest-first; a history reads forwards.
    return rows


def correction_chain(delta_id: str) -> list[dict[str, Any]]:
    """One delta's correction chain, oldest first, via ``revision_chain``."""
    return [
        {
            "delta_id": d.delta_id,
            "stage": d.stage,
            "stage_label": _stage_label(d.stage),
            "actor": d.actor,
            "rationale": d.rationale,
            "supersedes_delta_id": d.supersedes_delta_id,
            "created_at": d.created_at,
            **_counts(d),
        }
        for d in hitl_delta.revision_chain(delta_id)
    ]


def telemetry_available() -> bool:
    """Is ``trust_deltas`` actually readable on this database?

    ``capability_consumption``'s discipline: an unmigrated database reports
    "nothing can be known", never a confident zero. Without this the panel draws
    an empty queue on a database that has never run the migration, and an
    unmeasured board is indistinguishable from a clean one.
    """
    try:
        from tools.db.storage import get_connection, table_exists

        conn = get_connection()
    except Exception as exc:  # noqa: BLE001
        logger.debug("delta_review: storage unavailable: %s", exc)
        return False
    try:
        return bool(table_exists(conn, hitl_delta.TABLE))
    except Exception as exc:  # noqa: BLE001
        logger.debug("delta_review: table_exists failed: %s", exc)
        return False
    finally:
        try:
            conn.close()
        except Exception:  # noqa: BLE001
            pass


def panel_context(delta_id: Optional[str], *, limit: int = 100) -> dict[str, Any]:
    """Everything the page template needs, in one call.

    A ``delta_id`` naming a delta that is gone is reported as ``not_found``
    rather than silently falling back to the queue — a stale link that quietly
    shows a different page is how a reviewer settles the wrong diff.
    """
    data = board(limit=limit)
    selected: Optional[dict[str, Any]] = None
    not_found = ""

    if delta_id:
        delta = hitl_delta.get(delta_id)
        if delta is None:
            not_found = delta_id
        else:
            selected = delta_payload(delta)
            selected["timeline"] = artifact_timeline(delta.artifact_id, limit=limit)
            selected["chain"] = correction_chain(delta.delta_id)

    return {
        "summary": data["summary"],
        "queue": data["queue"],
        "selected": selected,
        "not_found": not_found,
        "telemetry_available": telemetry_available(),
    }


def build_span_rows(delta: hitl_delta.Delta, *, only_notable: bool = False) -> list[dict]:
    """Span rows for a caller that wants them without the rest of the payload.

    Used by the IQE ``delta_review.spans`` collection, which flattens spans
    across deltas so "which claims still carry a finding after revision" is
    answerable in bulk rather than by opening every panel by hand.
    """
    rows, _doc_before, _doc_after = annotate_spans(delta)
    if only_notable:
        rows = [r for r in rows if r["notable"]]
    for row in rows:
        row["delta_id"] = delta.delta_id
        row["artifact_id"] = delta.artifact_id
    return rows


__all__ = [
    "findings_by_claim",
    "document_findings",
    "resolve_span_findings",
    "annotate_spans",
    "review_state",
    "queue_row",
    "delta_payload",
    "board",
    "pending_queue",
    "artifact_timeline",
    "correction_chain",
    "telemetry_available",
    "panel_context",
    "build_span_rows",
]
