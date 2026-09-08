# CUI // SP-CTI
r"""The anchored change set — one word-level diff per proposal (dwr-ws-01).

:mod:`word_diff` answers "what did this change do to the sentence". This module
answers "which proposals are there, what is each one's before/after, and what
does the system already KNOW about it" — and hands both to one endpoint.

## Where the before side comes from, and why that is two cases and not one

A proposal has a before-text in one of two very different senses, and merging
them is the defect this module exists to avoid:

``anchor_text``      THE ADDRESSABLE SPAN. ``anchor_basis`` is ``exact`` or
                     ``relocated``, so the store has verified
                     ``section.content[start:end] == anchor_text`` and the
                     accept path will splice EXACTLY this span. What the
                     reviewer sees is what would be written.
``current_content``  the drafter's own before-text on an UNANCHORED row — the
                     old string it was replacing, with no verified location in
                     any section. It is a genuine preview of the rewrite and it
                     is NOT appliable: ``api_suggestion_accept`` refuses an
                     unanchored proposal with a 409 rather than guessing where
                     it goes (dwr-anchor-05).

Both are diffed and both are rendered, because MEASURED ON THE LIVE PG BOARD
2026-09-08 all 58 ``dic_suggestions`` rows carry ``anchor_basis`` NULL — every
one written by ``doc_modernization.redline_drafter``, which honestly declares
``unanchored`` because it is handed an entity label and not a span. A change
set restricted to anchored proposals would therefore return an EMPTY list on
this deployment, and an empty change set reads as "nothing proposed" when the
truth is "58 proposals, none of them addressable". So every row is returned,
each carrying ``before_source`` and ``appliable``, and the run-level ``state``
says which case the board is actually in.

## What is READ and what is derived

Nothing here re-derives a verdict. Each field names the row it came from:

  rationale         ``dic_suggestions.rationale``
  currency_verdict  ``docmod_findings.currency_verdict`` — the SCAN's verdict,
                    the one that motivated this redline
  confidence        ``docmod_findings.confidence``; the BAND is
                    ``citation_grounding.classify_confidence`` applied to that
                    stored score — the same function the drafter's TRUST gate
                    used, imported rather than respelled, so there is one
                    statement of where the thresholds are
  citations         the ``[source: ...]`` ids parsed out of the proposed text
                    with ``citation_grounding.parse_citations`` — the same
                    parser the hallucinated-citation gate ran. Reading the tags
                    that are in the draft, not scoring them again.
  evidence_health   ``dic_docdrift_resolutions`` for the finding's entity, via
                    ``docdrift_evidence.latest_resolutions``
  anchor_basis      ``dic_suggestions.anchor_basis``

THE DOCDRIFT VERDICT IS KEPT APART FROM THE FINDING'S. They are two
derivations of one question — is this entity still current — and this module
carries both under their own names (``currency_verdict`` from the scan,
``evidence_verdict`` from the resolution) rather than picking a winner. A
change set is not the place to adjudicate a disagreement between two stores.

AN ENTITY WITH NO RESOLUTION ROW READS ``unmeasured``, NEVER ``ok``. Measured
on the same board: of the 10 distinct entity labels carrying a drafted
redline, six have a stored resolution (all ``degraded``) and four —
``SNMPv2c``, ``telnet``, ``Windows Server 2012 R2``, ``CentOS 7`` — have none
at all. Rendering those four as healthy would be the fabrication
``docdrift_evidence`` was written to refuse; the vocabulary is that module's
own (``unresolved_view``), not a second one.

## The four run states

  unmeasured     the suggestion store could not be read. NOT a clean board.
  no_suggestions measured, and there are none for this filter.
  none_anchored  suggestions exist and NOT ONE carries an appliable anchor —
                 the live board today. Every one is still returned and
                 diffable as a preview; none of them can be applied.
  changes        at least one addressable, appliable change.

Reads. Writes nothing, decides nothing, applies nothing.
"""
from __future__ import annotations

from tools.document_intelligence import word_diff
from tools.document_intelligence.suggestion_store import (
    APPLIABLE_BASES,
    verify_anchor,
)

#: Where a change's before-text came from. ``None`` when there is none.
BEFORE_ANCHOR = "anchor_text"
BEFORE_CURRENT = "current_content"

#: Where the after-text came from. ``applied_text`` wins when it is recorded:
#: on edit-then-accept THAT is what shipped, and the AI draft did not.
AFTER_SUGGESTED = "suggested_content"
AFTER_APPLIED = "applied_text"

STATE_UNMEASURED = "unmeasured"
STATE_NONE = "no_suggestions"
STATE_NONE_ANCHORED = "none_anchored"
STATE_CHANGES = "changes"

#: docdrift_evidence's own word for "nobody has asked". Never ``ok``.
HEALTH_UNMEASURED = "unmeasured"

_DEFAULT_LIMIT = 200


# ── One change ────────────────────────────────────────────────────────────────

def before_side(suggestion: dict) -> tuple[str | None, str | None]:
    """``(before_text, before_source)`` for one suggestion.

    An APPLIABLE basis takes ``anchor_text`` — including the empty string,
    which is a legitimate insertion-point anchor (``0:0`` in a section).
    Otherwise ``current_content`` is used when it holds anything, and an empty
    or absent one means the writer recorded no before-text at all, which is
    ``(None, None)`` — no diff, and a reason, never a silently empty one.
    """
    basis = suggestion.get("anchor_basis")
    if basis in APPLIABLE_BASES:
        text = suggestion.get("anchor_text")
        if isinstance(text, str):
            return text, BEFORE_ANCHOR
    current = suggestion.get("current_content")
    if isinstance(current, str) and current != "":
        return current, BEFORE_CURRENT
    return None, None


def after_side(suggestion: dict) -> tuple[str | None, str | None]:
    """``(after_text, after_source)``. ``applied_text`` outranks the draft."""
    applied = suggestion.get("applied_text")
    if isinstance(applied, str) and applied != "":
        return applied, AFTER_APPLIED
    suggested = suggestion.get("suggested_content")
    if isinstance(suggested, str):
        return suggested, AFTER_SUGGESTED
    return None, None


def change_view(
    suggestion: dict,
    *,
    finding: dict | None = None,
    resolution: object | None = None,
    section_content: str | None = None,
    section_read: bool = False,
) -> dict:
    """One suggestion -> one rendered change. Pure: no database, no LLM.

    ``finding`` is the ``docmod_findings`` row linked by
    ``redline_suggestion_id``, or ``None``. ``resolution`` is a
    ``docdrift_evidence.FindingView`` for the finding's entity, or ``None``.
    ``section_read`` distinguishes "the section was looked up and holds this"
    from "nobody looked" — without it a ``None`` content would make an
    unchecked anchor indistinguishable from a missing section.
    """
    from tools.quality.citation_grounding import classify_confidence, parse_citations

    before, before_source = before_side(suggestion)
    after, after_source = after_side(suggestion)

    if before_source is None:
        diff = {"spans": None, "round_trip": None,
                "added_words": None, "removed_words": None, "changed": None}
        spans_reason = "no_text" if after_source is None else "no_before_text"
    else:
        diff = word_diff.diff_words(before, after)
        spans_reason = None if diff["spans"] is not None else "round_trip_failed"

    basis = suggestion.get("anchor_basis")
    appliable = basis in APPLIABLE_BASES
    verified: bool | None = None
    verify_reason: str | None = None
    if section_read:
        verdict = verify_anchor(suggestion, section_content)
        verified = bool(verdict["ok"])
        verify_reason = verdict["reason"]

    confidence = _float_or_none(finding.get("confidence") if finding else None)
    citations = parse_citations(after or "") if after else []

    return {
        "suggestion_id": suggestion.get("suggestion_id"),
        "doc_id": suggestion.get("doc_id") or None,
        "collection_id": suggestion.get("collection_id") or None,
        "section_id": suggestion.get("section_id") or None,
        "canvas_source": suggestion.get("canvas_source") or None,
        "origin_kind": suggestion.get("origin_kind"),
        "status": suggestion.get("status"),
        "created_at": suggestion.get("created_at"),

        # ── the anchor ───────────────────────────────────────────────────────
        "anchor_basis": basis,
        "anchor_section_id": suggestion.get("anchor_section_id"),
        "anchor_start": suggestion.get("anchor_start"),
        "anchor_end": suggestion.get("anchor_end"),
        "appliable": appliable,
        "anchor_verified": verified,
        "anchor_verify_reason": verify_reason,

        # ── the diff ─────────────────────────────────────────────────────────
        "before": before,
        "before_source": before_source,
        "after": after,
        "after_source": after_source,
        "spans": diff["spans"],
        "spans_reason": spans_reason,
        "round_trip": diff["round_trip"],
        "added_words": diff["added_words"],
        "removed_words": diff["removed_words"],
        "changed": diff["changed"],

        # ── what the system already knows, each field naming its row ─────────
        "rationale": suggestion.get("rationale") or None,
        "citations": citations,
        "citations_source": "inline_tags" if citations else None,
        "finding_id": (finding or {}).get("finding_id"),
        "entity_label": (finding or {}).get("entity_label"),
        "currency_verdict": (finding or {}).get("currency_verdict"),
        "currency_verdict_source": "docmod_findings" if finding else None,
        "severity": (finding or {}).get("severity"),
        "confidence": confidence,
        "confidence_band": classify_confidence(confidence) if confidence is not None else None,
        "confidence_source": "docmod_findings" if confidence is not None else None,
        "evidence_health": (getattr(resolution, "evidence_health", None)
                            if resolution is not None else HEALTH_UNMEASURED),
        "evidence_state": (getattr(resolution, "state", None)
                           if resolution is not None else "not_resolved"),
        "evidence_verdict": (getattr(resolution, "verdict", None)
                             if resolution is not None else None),
        "evidence_citation_count": (getattr(resolution, "citation_count", None)
                                    if resolution is not None else None),
        "evidence_resolved_at": (getattr(resolution, "resolved_at", None)
                                 if resolution is not None else None),
        "evidence_source": "dic_docdrift_resolutions" if resolution is not None else None,
    }


def _float_or_none(value) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


# ── The run ───────────────────────────────────────────────────────────────────

def run_state(read_ok: bool, changes: list[dict]) -> str:
    """Which of the four states this run is in. Never guesses at ``changes``."""
    if not read_ok:
        return STATE_UNMEASURED
    if not changes:
        return STATE_NONE
    if any(c.get("appliable") for c in changes):
        return STATE_CHANGES
    return STATE_NONE_ANCHORED


def build_change_set(
    *,
    doc_id: str | None = None,
    collection_id: str | None = None,
    canvas_source: str | None = None,
    status: str = "pending",
    limit: int = _DEFAULT_LIMIT,
    verify_anchors: bool = True,
) -> dict:
    """Assemble the change set for one filter.

    ``verify_anchors`` re-reads each APPLIABLE proposal's section and asks
    ``verify_anchor`` whether the span still holds — the same question the
    accept path asks, through the same function, so the panel cannot promise an
    apply the accept route would refuse. Bounded by construction: only rows
    with an appliable basis are looked up, and each section is read once.
    """
    from tools.logging.icdev_logger import get_logger

    logger = get_logger(__name__)
    read_ok = True
    rows: list[dict] = []
    try:
        from tools.document_intelligence.suggestion_store import get_pending_suggestions
        rows = get_pending_suggestions(collection_id=collection_id,
                                       canvas_source=canvas_source, status=status)
    except Exception as exc:  # noqa: BLE001 — an unreadable store is `unmeasured`
        logger.warning("change_set: suggestion store unreadable: %s", exc)
        read_ok = False
        rows = []

    if doc_id:
        rows = [r for r in rows if (r.get("doc_id") or "") == doc_id]
    truncated = len(rows) > max(limit, 0)
    rows = rows[:max(limit, 0)]

    findings = _findings_for([r.get("suggestion_id") for r in rows])
    resolutions = _resolutions_for(
        [f.get("entity_label") for f in findings.values() if f.get("entity_label")]
    )
    sections = _sections_for(rows) if verify_anchors else {}

    changes = []
    for row in rows:
        finding = findings.get(row.get("suggestion_id"))
        entity = (finding or {}).get("entity_label") or ""
        resolution = resolutions.get(_entity_key(entity)) if entity else None
        section_id = row.get("anchor_section_id") or ""
        read = section_id in sections
        changes.append(change_view(
            row, finding=finding, resolution=resolution,
            section_content=sections.get(section_id),
            section_read=read,
        ))

    anchored = sum(1 for c in changes if c["appliable"])
    diffable = sum(1 for c in changes if c["spans"] is not None)
    return {
        "state": run_state(read_ok, changes),
        "doc_id": doc_id,
        "collection_id": collection_id,
        "status": status,
        "changes": changes,
        "counts": {
            "total": len(changes) if read_ok else None,
            "appliable": anchored if read_ok else None,
            "unanchored": (len(changes) - anchored) if read_ok else None,
            "diffable": diffable if read_ok else None,
            "not_diffable": (len(changes) - diffable) if read_ok else None,
        },
        "truncated": truncated,
        "limit": limit,
    }


def _entity_key(entity: str) -> str:
    from tools.document_intelligence.docdrift_evidence import _entity_key as key
    return key(entity)


def _findings_for(suggestion_ids: list) -> dict:
    """``{suggestion_id: docmod_findings row}`` — the NEWEST row per suggestion.

    Degrades to ``{}`` on any read failure: a change with no finding reports
    every finding-sourced field as ``None`` with a ``None`` source, which is
    what "we could not read it" means. It never reports a default verdict.
    """
    ids = [s for s in suggestion_ids if s]
    if not ids:
        return {}
    from tools.db.storage import get_connection
    from tools.logging.icdev_logger import get_logger
    placeholders = ",".join(["%s"] * len(ids))
    try:
        with get_connection() as conn:
            rows = conn.execute(
                "SELECT finding_id, redline_suggestion_id, entity_label, entity_type, "
                "       finding_type, currency_verdict, severity, confidence, state, created_at "
                "FROM docmod_findings WHERE redline_suggestion_id IN "
                f"({placeholders}) ORDER BY created_at",
                ids,
            ).fetchall()
    except Exception as exc:  # noqa: BLE001
        get_logger(__name__).warning("change_set: docmod_findings unreadable: %s", exc)
        return {}
    out: dict = {}
    for raw in rows:
        row = dict(raw) if not isinstance(raw, dict) else raw
        key = row.get("redline_suggestion_id")
        if key:
            out[key] = row  # ORDER BY created_at — the last write wins
    return out


def _resolutions_for(entities: list) -> dict:
    """``{entity_key: FindingView}``. ``{}`` on any failure, which renders every
    entity ``unmeasured`` — ``latest_resolutions``' own documented degradation."""
    wanted = [e for e in entities if e]
    if not wanted:
        return {}
    from tools.document_intelligence.docdrift_evidence import latest_resolutions
    try:
        return latest_resolutions(wanted)
    except Exception:  # noqa: BLE001 — already logged there; unmeasured is honest
        return {}


def _sections_for(rows: list[dict]) -> dict:
    """``{section_id: content}`` for the APPLIABLE rows only.

    A section that does not exist is absent from the map, so ``change_view``
    reports ``section_read=False`` for it and ``anchor_verified`` stays
    ``None`` — "we could not look" rather than "the anchor failed".
    """
    ids = sorted({(r.get("anchor_section_id") or "") for r in rows
                  if r.get("anchor_basis") in APPLIABLE_BASES and r.get("anchor_section_id")})
    if not ids:
        return {}
    from tools.db.storage import get_connection
    from tools.logging.icdev_logger import get_logger
    placeholders = ",".join(["%s"] * len(ids))
    try:
        with get_connection() as conn:
            rows_out = conn.execute(
                f"SELECT section_id, content FROM dic_sections WHERE section_id IN ({placeholders})",
                ids,
            ).fetchall()
    except Exception as exc:  # noqa: BLE001
        get_logger(__name__).warning("change_set: dic_sections unreadable: %s", exc)
        return {}
    out = {}
    for raw in rows_out:
        row = dict(raw) if not isinstance(raw, dict) else raw
        out[row.get("section_id")] = row.get("content") or ""
    return out
