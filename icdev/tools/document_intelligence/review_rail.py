# CUI // SP-CTI
"""dwr-cmt-02 — ONE review rail: the AI's proposal and the colleague's question,
against the same sentence.

dwr-cmt-01 gave a comment a span. dwr-anchor-03/05 gave a suggestion a span.
Nothing put the two in one ordered stream, so a reviewer read AI change cards
in a panel at the top of the page and human comments inside a per-section
accordion, and no surface ever said that both were about the same clause. This
module builds that stream, and it is a READER: it writes nothing, decides
nothing, and holds no anchor logic of its own.

WHERE AN ITEM SITS IS A MEASUREMENT, NEVER A DEFAULT
    ``position`` is the anchor's ``anchor_start`` and it is ``None`` unless the
    span VERIFIES against the section's live content. Sorting an item with no
    verified span to offset 0 is the defect this whole card series exists to
    refuse: it would render every unanchored proposal as a remark about the
    first sentence. Measured on the live PG board 2026-09-08, all 58 rows in
    ``dic_suggestions`` carry ``anchor_basis`` NULL — a rail that defaulted
    would have put 58 fabricated positions on screen on day one.

THREE STATES, AND COLLAPSING ANY TWO IS A FABRICATION
    placed + positioned   a verified span in a section of this version. Sorted
                          by offset, interleaved with the other kind.
    placed, unpositioned  it names a section, but no span in it holds — no
                          anchor was ever recorded, or the section moved under
                          it. Ordered by age, in a LABELLED group under that
                          section. Never given a number.
    unplaced              it names NO section at all. Document-level, and on
                          this board that is 58 of 58 suggestions: they carry a
                          real ``doc_id``, ``section_id = ''`` and
                          ``anchor_section_id`` NULL. Dropping them shows an
                          empty rail for a document with 47 pending proposals;
                          folding them into the first section attributes 47
                          proposals to a section that has never seen one.

ONE ANCHOR VOCABULARY, TRANSLATED AND NOT RE-DERIVED
    A comment's verdict is ``annotation_store.anchor_state`` — already attached
    by ``list_threads`` on every read, so this module never re-slices a section
    for a comment. A change's verdict is ``suggestion_store.verify_anchor``,
    the SAME function the accept door asks, translated onto the comment's four
    states through ``_STATE_BY_VERIFY_REASON``. The rail therefore cannot show
    "appliable" for a proposal accept would refuse. The map is pinned against
    ``suggestion_store.VERIFY_REASONS`` by test, and an UNKNOWN reason degrades
    to ``unverifiable`` — never to ``verified``, which is the only direction
    that would put a fabricated position on screen.

``empty`` IS A MEASUREMENT AND ``unmeasurable`` IS NOT
    ``state`` is ``items`` | ``empty`` | ``unmeasurable``. ``empty`` means the
    version's sections were READ and nobody has proposed or asked anything.
    ``unmeasurable`` means the document, the version or the sections could not
    be read, and it is never folded into ``empty`` — an empty rail that cannot
    tell those apart reads as a clean bill of health for a document nobody has
    reviewed. Counts are ``None``, never ``0``, when nothing was measured.
"""
from __future__ import annotations

from tools.db.storage import get_connection
from tools.document_intelligence.annotation_store import (
    ANCHOR_STATES,
    list_threads,
)
from tools.document_intelligence.suggestion_store import (
    APPLIABLE_BASES,
    resolve_anchor,
    section_of_record,
    verify_anchor,
)
from tools.logging.icdev_logger import get_logger

logger = get_logger(__name__)

#: The two things a reviewer reads in one column.
ITEM_KINDS = ("comment", "change")

#: ``empty`` is a measured zero. ``unmeasurable`` is not a zero at all.
RAIL_STATES = ("items", "empty", "unmeasurable")

#: ``verify_anchor``'s refusal reasons, translated onto the comment vocabulary
#: (``annotation_store.ANCHOR_STATES``). Keys are pinned against
#: ``suggestion_store.VERIFY_REASONS`` by test.
#:
#: ``no_offsets`` is ``orphaned`` and not ``unanchored`` on purpose: the row
#: CLAIMS an appliable basis and carries no offsets, which is a malformed row
#: the accept door supersedes. "Nobody recorded a span" and "the recorded span
#: is unusable" send a reader to different repairs.
_STATE_BY_VERIFY_REASON = {
    "section_missing": ("unverifiable", "section_unreadable"),
    "unanchored": ("unanchored", "no_anchor_recorded"),
    "no_offsets": ("orphaned", "no_offsets"),
    "anchor_stale": ("orphaned", "anchor_stale"),
}


# ── The change card's anchor verdict ──────────────────────────────────────────

def unplaced_anchor_state(suggestion: dict) -> dict:
    """The anchor verdict for a suggestion that names NO section of record.

    ``verify_anchor`` cannot answer this one honestly: handed ``None`` it
    short-circuits at ``section_missing``, which this module maps to
    ``unverifiable``/``section_unreadable`` — and "the section could not be
    read" is a DIFFERENT fact from "there is no section", pointing at a
    different repair (a database that will not answer, versus a writer that
    never named one). Merging them is what this rail refuses everywhere else.

    So the verdict is taken from the ROW ALONE, which needs no section:

      basis not appliable  ``unanchored`` — nobody ever recorded a span. This
                           is all 58 rows on the live board (2026-09-08).
      basis appliable      ``unverifiable`` / ``no_section_of_record`` — it
                           claims a span, and there is nothing to check it
                           against. Never ``verified``.

    ``APPLIABLE_BASES`` is the suggestion store's own declaration, read and not
    respelled; nothing here re-derives whether a span holds.
    """
    if suggestion.get("anchor_basis") not in APPLIABLE_BASES:
        return {"state": "unanchored", "reason": "no_anchor_recorded",
                "relocation_candidate": None}
    return {"state": "unverifiable", "reason": "no_section_of_record",
            "relocation_candidate": None}


def change_anchor_state(section_text: str | None, suggestion: dict) -> dict:
    """Re-derive whether a suggestion's span still holds, in the SAME four-state
    vocabulary a comment reports in.

    Asks ``suggestion_store.verify_anchor`` — the accept door's own question —
    and translates its answer. It never inspects ``anchor_start`` itself: a
    second opinion about whether a span holds is how the rail would come to
    show a proposal as placeable that accept refuses.

    ``relocation_candidate`` is ADVISORY and is computed exactly the way
    ``annotation_store.anchor_state`` computes a comment's, through the shared
    ``resolve_anchor``: where the anchor text is still findable EXACTLY ONCE
    elsewhere, that position is reported for a human. Nothing is re-pointed,
    and an ambiguous match yields no candidate at all.

    Pure: reads no database, writes nothing.
    """
    verdict = verify_anchor(suggestion, section_text)
    if verdict["ok"]:
        return {"state": "verified", "reason": "offsets_verified",
                "relocation_candidate": None}

    reason = verdict.get("reason")
    state, mapped = _STATE_BY_VERIFY_REASON.get(
        reason, ("unverifiable", f"unmapped:{reason}"))

    candidate = None
    if state == "orphaned" and section_text is not None:
        found = resolve_anchor(section_text, suggestion.get("anchor_text") or "")
        if found["anchor_basis"] == "relocated":
            candidate = {"anchor_start": found["anchor_start"],
                         "anchor_end": found["anchor_end"]}
    return {"state": state, "reason": mapped, "relocation_candidate": candidate}


# ── Items ─────────────────────────────────────────────────────────────────────

def _position(anchor: dict, row: dict) -> int | None:
    """The offset an item is drawn at, or ``None``.

    ONLY a ``verified`` anchor yields a number. Everything else has no position
    — not offset 0, not the section's start, not the item's age. This is the
    one place a position is decided, so no caller can invent one.
    """
    if anchor.get("state") != "verified":
        return None
    start = row.get("anchor_start")
    return start if isinstance(start, int) and not isinstance(start, bool) else None


def _comment_item(thread: dict) -> dict:
    anchor = dict(thread.get("anchor") or
                  {"state": "unverifiable", "reason": "no_verdict",
                   "relocation_candidate": None})
    return {
        "kind": "comment",
        "id": thread.get("ann_id"),
        "section_id": thread.get("section_id"),
        "position": _position(anchor, thread),
        "anchor": anchor,
        "anchor_text": thread.get("anchor_text") or thread.get("selected_text") or "",
        "author": thread.get("author"),
        "created_at": thread.get("created_at"),
        "status": thread.get("status"),
        "category": thread.get("category"),
        "body": thread.get("comment"),
        "replies": thread.get("replies") or [],
        "reply_count": thread.get("reply_count", len(thread.get("replies") or [])),
        "thread_broken": bool(thread.get("thread_broken")),
        "resolved_by": thread.get("resolved_by"),
        "resolution_note": thread.get("resolution_note"),
    }


def _change_item(suggestion: dict, section_text: str | None, *,
                 placed: bool = True) -> dict:
    anchor = (change_anchor_state(section_text, suggestion) if placed
              else unplaced_anchor_state(suggestion))
    return {
        "kind": "change",
        "id": suggestion.get("suggestion_id"),
        "section_id": section_of_record(suggestion),
        "position": _position(anchor, suggestion),
        "anchor": anchor,
        "anchor_text": suggestion.get("anchor_text") or "",
        "author": suggestion.get("canvas_source") or "ai",
        "created_at": suggestion.get("created_at"),
        "status": suggestion.get("status"),
        "origin_kind": suggestion.get("origin_kind"),
        "body": suggestion.get("rationale") or "",
        "suggested_content": suggestion.get("suggested_content") or "",
        "current_content": suggestion.get("current_content") or "",
        "replies": [],
        "reply_count": 0,
    }


def _order(items: list[dict]) -> tuple[list[dict], list[dict]]:
    """Split one section's items into the positioned stream and the group that
    has no position, and order each on its own terms.

    A positioned item sorts by offset — that is the interleave the card asks
    for, and the ONLY thing that puts a comment and a change card side by side.
    An unpositioned item sorts by age, because age is the only fact about it
    that is measured.
    """
    placed = [i for i in items if i["position"] is not None]
    loose = [i for i in items if i["position"] is None]
    placed.sort(key=lambda i: (i["position"], i.get("created_at") or "", i["id"] or ""))
    loose.sort(key=lambda i: (i.get("created_at") or "", i["id"] or ""))
    return placed, loose


def _counts(items: list[dict]) -> dict:
    states = [i["anchor"]["state"] for i in items]
    return {
        "items": len(items),
        "comments": sum(1 for i in items if i["kind"] == "comment"),
        "changes": sum(1 for i in items if i["kind"] == "change"),
        "positioned": sum(1 for i in items if i["position"] is not None),
        "unpositioned": sum(1 for i in items if i["position"] is None),
        # Kept apart, all four: `unverifiable` is a section nobody could read,
        # and folding it into `unanchored` would let an unreadable section
        # report as a set of harmless section-level remarks.
        **{s: sum(1 for x in states if x == s) for s in ANCHOR_STATES},
        "open_comments": sum(1 for i in items
                             if i["kind"] == "comment" and i.get("status") != "resolved"),
        "pending_changes": sum(1 for i in items
                               if i["kind"] == "change" and i.get("status") == "pending"),
    }


def _empty_counts() -> dict:
    """Every count ``None`` — NEVER 0. An unmeasurable rail that reports zeroes
    is indistinguishable from a document nobody has anything to say about."""
    keys = ("items", "comments", "changes", "positioned", "unpositioned",
            "open_comments", "pending_changes", *ANCHOR_STATES)
    return {k: None for k in keys}


# ── Reads ─────────────────────────────────────────────────────────────────────

def _sections(conn, version_id: str) -> list[dict] | None:
    """The version's sections in DOCUMENT ORDER, or ``None`` when unreadable.

    ``ORDER BY section_id`` is the page's own ordering rule (``doc_detail``
    reads sections exactly this way, and ``section_id`` carries a sortable
    index). Ordering the rail any other way would put the rail and the page in
    a different order about the same document.
    """
    try:
        cur = conn.execute(
            "SELECT section_id, heading, content FROM dic_sections "
            "WHERE version_id = %s ORDER BY section_id",
            (version_id,),
        )
        cols = [d[0] for d in cur.description]
        return [dict(zip(cols, r)) for r in cur.fetchall()]
    except Exception as exc:
        logger.warning("review rail: sections unreadable for %s: %s", version_id, exc)
        return None


def _suggestions(conn, doc_id: str, tenant_id: str | None) -> list[dict] | None:
    """Every suggestion on the document, or ``None`` when unreadable.

    Read by ``doc_id`` and grouped in PYTHON through ``section_of_record`` —
    never by a WHERE clause that respells which of ``anchor_section_id`` and
    ``section_id`` wins. A NULL ``tenant_id`` is kept for the same reason
    ``annotation_store._tenant_clause`` keeps one: a row written before tenant
    scoping must not vanish without a word.
    """
    sql = "SELECT * FROM dic_suggestions WHERE doc_id = %s"
    params: list = [doc_id]
    if tenant_id:
        sql += " AND (tenant_id = %s OR tenant_id IS NULL)"
        params.append(tenant_id)
    sql += " ORDER BY created_at ASC, suggestion_id ASC"
    try:
        cur = conn.execute(sql, params)
        cols = [d[0] for d in cur.description]
        return [dict(zip(cols, r)) for r in cur.fetchall()]
    except Exception as exc:
        logger.warning("review rail: suggestions unreadable for %s: %s", doc_id, exc)
        return None


def _unmeasurable(doc_id: str, version_id: str | None, reason: str) -> dict:
    return {
        "doc_id": doc_id,
        "version_id": version_id,
        "state": "unmeasurable",
        "reason": reason,
        "sections": [],
        "unplaced": [],
        "counts": _empty_counts(),
        "unplaced_count": None,
    }


def build_rail(doc_id: str, *, version_id: str | None,
               tenant_id: str | None = None, conn=None) -> dict:
    """The document's review rail: comment threads and change cards in one
    ordered stream, per section, by anchor position.

    ``version_id`` is REQUIRED and is not resolved here — which version a page
    is showing is the page's question, and answering it a second time in this
    module is how the rail would describe a version the reader is not looking
    at. ``None`` is ``unmeasurable`` with reason ``no_version``, never an
    empty rail.

    Writes nothing.
    """
    if not doc_id:
        return _unmeasurable(doc_id, version_id, "no_doc_id")
    if not version_id:
        return _unmeasurable(doc_id, version_id, "no_version")

    own = conn is None
    conn = conn or get_connection()
    try:
        sections = _sections(conn, version_id)
        if sections is None:
            return _unmeasurable(doc_id, version_id, "sections_unreadable")
        suggestions = _suggestions(conn, doc_id, tenant_id)
        if suggestions is None:
            return _unmeasurable(doc_id, version_id, "suggestions_unreadable")

        by_section: dict[str, list[dict]] = {}
        unplaced: list[dict] = []
        known = {s["section_id"] for s in sections}
        text_of = {s["section_id"]: s.get("content") for s in sections}
        for sug in suggestions:
            sid = section_of_record(sug)
            if sid and sid in known:
                by_section.setdefault(sid, []).append(
                    _change_item(sug, text_of.get(sid)))
            else:
                # Names no section, or names one that is not in this version.
                # Reported whole at document level; NEVER attached to a section
                # it does not name, and never dropped.
                item = _change_item(sug, None, placed=False)
                item["unplaced_reason"] = ("no_section_named" if not sid
                                           else "section_not_in_version")
                unplaced.append(item)

        out_sections: list[dict] = []
        for s in sections:
            sid = s["section_id"]
            try:
                threads = list_threads(sid, tenant_id=tenant_id, conn=conn)
            except Exception as exc:
                logger.warning("review rail: comments unreadable for %s: %s", sid, exc)
                threads = []
            items = [_comment_item(t) for t in threads] + by_section.get(sid, [])
            placed, loose = _order(items)
            out_sections.append({
                "section_id": sid,
                "heading": s.get("heading") or "",
                "positioned": placed,
                "unpositioned": loose,
                "counts": _counts(items),
            })
    finally:
        if own:
            conn.close()

    unplaced.sort(key=lambda i: (i.get("created_at") or "", i["id"] or ""))
    every = [i for sec in out_sections
             for i in sec["positioned"] + sec["unpositioned"]] + unplaced
    return {
        "doc_id": doc_id,
        "version_id": version_id,
        "state": "items" if every else "empty",
        "reason": None,
        "sections": out_sections,
        "unplaced": unplaced,
        "counts": _counts(every),
        "unplaced_count": len(unplaced),
    }
