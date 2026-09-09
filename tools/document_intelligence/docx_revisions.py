# CUI // SP-CTI
"""dwr-word-01 — a .docx whose tracked changes are REAL Word revisions.

THE DEFECT. ``exporter.render`` already writes a ``docx``, and what it writes
is flat prose: ``markdown_to_docx`` renders the assembled markdown and nothing
else. A reviewer who opens it sees the document as if every proposal had been
silently applied or silently dropped — the pending redlines and the review
comments, which are the entire point of a review artifact, do not travel. The
only place they exist is the canvas, so the answer to "send me the marked-up
copy" has been "log in and look at the page".

python-docx 1.2.0 (requirements.txt) exposes NO revision API — no ``w:ins``, no
``w:del``, no comment part. Every one of those is direct OOXML over the XML
tree python-docx does expose, plus two package parts it does not know about.
That is what this module does, and it is the only place in the tree that does
it.

WHAT WORD CALLS A REVISION IS AN *UNDECIDED* PROPOSAL, AND THAT IS THE WHOLE
MAPPING. A ``w:ins``/``w:del`` is something the reader may Accept or Reject.
So:

  pending   -> a real Word revision. Undecided here, undecided there.
  accepted  -> already spliced into ``dic_sections.content`` by
               ``suggestion_store.record_application``, so it is ALREADY in the
               body prose. Re-rendering it as a ``w:ins`` would offer a Word
               reviewer a Reject button over a decision a human has already
               taken and recorded on the append-only
               ``dic_suggestion_decisions`` chain — and rejecting it in Word
               would silently revert an accepted change with nothing written
               back. It is reported in the appendix as PROVENANCE instead.
  rejected / superseded
            -> likewise never a revision; reported, never dropped.

Both halves of "accepted-and-pending" therefore travel; they do not travel
IDENTICALLY, because they do not mean the same thing to Word.

ONLY A VERIFIED ANCHOR BECOMES A REVISION, AND NOTHING ELSE IS INVENTED. The
position of every item is ``review_rail``'s — the rail is the ONE reader that
already translates ``suggestion_store.verify_anchor`` and
``annotation_store.anchor_state`` onto one vocabulary, and its ``position`` is
``None`` unless the span verifies against the section's live content. This
module re-derives no anchor of its own. An item with no position is NOT drawn
at offset 0 and is NOT dropped: it goes to a labelled appendix carrying the
reason it could not be placed. Measured on the live PG board 2026-09-08, all
58 rows in ``dic_suggestions`` carry ``anchor_basis`` NULL, so on this
deployment the appendix is where the entire population lands — an exporter
that placed them would have put 58 fabricated revisions into a Word document
on day one.

FOUR MORE REFUSALS, each of which renders a change in the appendix rather than
approximately:

  anchor_spans_paragraphs  the span crosses a paragraph break. A ``w:ins``
                           lives inside one ``w:p``; splitting one revision
                           across two would need two revisions whose combined
                           meaning is not the proposal.
  anchor_overlap           two verified spans overlap. Rendering both nests one
                           revision inside another and Word's accept order
                           decides the outcome; rendering the first only is a
                           silent drop of the second.
  anchor_text_mismatch     the rail said verified and the slice does not hold.
                           Belt and braces over one computation trusted twice.
  diff_not_round_trip      ``word_diff.diff_words`` could not reassemble both
                           sides. Its own contract already returns
                           ``spans: None`` there rather than a best-effort
                           list; a revision built from a lossy diff would
                           delete characters nobody proposed deleting.

THE SECTION ROUND TRIP IS AN INVARIANT, NOT A HOPE. ``paragraph_spans``
partitions a section's content on ``\\n`` and records each paragraph's absolute
offsets, so ``"\\n".join(texts) == content`` for EVERY input — which is what
makes an anchor offset, which indexes the section content, mean the same thing
inside a paragraph. It is asserted at build time, and a section that fails it
is rendered with no revisions at all rather than with revisions at offsets that
have quietly moved.

Consequently this format does NOT re-render markdown. ``exporter.render``'s
``docx`` runs ``markdown_to_docx``, which reflows ``## Heading`` into a Word
heading and in doing so changes every character offset after it. Offset
fidelity and markdown reflow cannot both be had, and for a review artifact the
offsets are the load-bearing half: a heading rendered as literal ``##`` is
legible, a revision attached to the wrong clause is not. Section HEADINGS,
which are their own column and carry no anchors, are still real Word headings.

COUNTS ARE ``None`` AND NEVER ``0`` WHEN THE RAIL COULD NOT BE READ. A rail in
``unmeasurable`` means the document, the version or the sections were
unreadable; a docx reporting "0 revisions, 0 comments" for it is
indistinguishable from a document nobody has proposed anything about, which is
the reassurance this whole card series exists to refuse. The appendix says so
in words.

VERIFIED BY OPENING IT IN WORD. ``tests/document_intelligence/
test_docx_revisions.py`` asserts the OOXML; ``tools/document_intelligence/
docx_word_probe.py`` opens the artifact in Word itself over COM and reports
``Revisions.Count``, ``Comments.Count`` and which comments are replies. A
document Word SILENTLY REPAIRS passes every XML assertion — the first build of
this module was well-formed, schema-plausible and rejected outright by Word
("The file appears to be corrupted") because ``commentsExtended``'s ``w15``
namespace was ``…/office/2012/wordml`` rather than ``…/office/word/2012/
wordml``. No XML test in this repo would ever have found that.

Library only, no CLI. Reached through ``exporter.export_version(version_id,
"docx_tracked")``, so the placeholder -> citation -> WriteGuard gate still runs
in order, still fails closed, and still records one ``dic_artifacts`` row.
"""
from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any

from tools.logging.icdev_logger import get_logger

logger = get_logger(__name__)

try:  # pragma: no cover - exercised by the availability probe
    import docx  # noqa: F401
    from docx.opc.constants import RELATIONSHIP_TYPE as RT
    from docx.opc.packuri import PackURI
    from docx.opc.part import Part
    from docx.oxml import OxmlElement
    from docx.oxml.ns import qn

    DOCX_AVAILABLE = True
except ImportError as _exc:  # pragma: no cover
    # tsg-iso-03: an optional third-party import inside a swallowing handler is
    # indistinguishable from working code unless the handler SAYS it fired.
    logger.warning("python-docx is not installed (%s); the tracked-docx "
                   "exporter reports unavailable", _exc)
    DOCX_AVAILABLE = False

#: Namespaces. ``W15`` is the one that has to be exactly right and is the one
#: no XML test can check: with ``…/office/2012/wordml`` (the plausible spelling,
#: and the spelling this module shipped with for one build) the package is
#: well-formed and Word refuses to open it at all.
NS_W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
NS_W14 = "http://schemas.microsoft.com/office/word/2010/wordml"
NS_W15 = "http://schemas.microsoft.com/office/word/2012/wordml"

CT_COMMENTS = ("application/vnd.openxmlformats-officedocument."
               "wordprocessingml.comments+xml")
CT_COMMENTS_EXTENDED = ("application/vnd.openxmlformats-officedocument."
                        "wordprocessingml.commentsExtended+xml")
RT_COMMENTS_EXTENDED = ("http://schemas.microsoft.com/office/2011/"
                        "relationships/commentsExtended")

#: The one suggestion status that becomes an undecided Word revision. Anything
#: else is a decision already taken and is reported, never re-proposed.
TRACKED_STATUSES: tuple[str, ...] = ("pending",)

#: Every reason this module can refuse to PLACE an item. A refused item is
#: always reported in the appendix; none of these is ever a silent drop, and
#: none of them ever becomes a position.
DEFER_REASONS: tuple[str, ...] = (
    "no_verified_anchor",
    "not_pending",
    "anchor_spans_paragraphs",
    "anchor_overlap",
    "anchor_text_mismatch",
    "diff_not_round_trip",
    "section_round_trip_failed",
    "unplaced",
)

_FALLBACK_AUTHOR = "ICDEV"


class TrackedDocxError(RuntimeError):
    """The tracked-revision docx could not be built."""


# ── offsets ───────────────────────────────────────────────────────────────────

def paragraph_spans(content: str | None) -> list[dict]:
    """Partition ``content`` into paragraphs, each with its ABSOLUTE offsets.

    One entry per ``\\n``-delimited line, ``{"text", "start", "end"}``, where
    ``content[start:end] == text``. The newline itself belongs to no paragraph,
    which is what makes ``"\\n".join(texts) == content`` hold for every input
    including the empty string, a trailing newline and consecutive blank lines.

    That identity is the reason an anchor offset — which indexes the SECTION's
    content — can be turned into a position inside one paragraph without any
    arithmetic that could drift.
    """
    text = content or ""
    out: list[dict] = []
    pos = 0
    for line in text.split("\n"):
        out.append({"text": line, "start": pos, "end": pos + len(line)})
        pos += len(line) + 1
    return out


def spans_round_trip(content: str | None, spans: list[dict]) -> bool:
    """Do ``spans`` reassemble ``content`` exactly? Asserted per section."""
    return "\n".join(str(s.get("text") or "") for s in spans) == (content or "")


# ── small OOXML builders ──────────────────────────────────────────────────────

def _run(text: str, *, deleted: bool = False):
    """One ``w:r``. Inside a ``w:del`` the text element MUST be ``w:delText``;
    a ``w:t`` there is the classic silently-repaired document."""
    r = OxmlElement("w:r")
    t = OxmlElement("w:delText" if deleted else "w:t")
    t.set(qn("xml:space"), "preserve")
    t.text = text
    r.append(t)
    return r


def _revision(tag: str, rev_id: int, author: str, date: str, text: str):
    el = OxmlElement(f"w:{tag}")
    el.set(qn("w:id"), str(rev_id))
    el.set(qn("w:author"), author)
    el.set(qn("w:date"), date)
    el.append(_run(text, deleted=(tag == "del")))
    return el


def _comment_marker(tag: str, comment_id: int):
    el = OxmlElement(f"w:{tag}")
    el.set(qn("w:id"), str(comment_id))
    return el


def _comment_reference(comment_id: int):
    r = OxmlElement("w:r")
    rpr = OxmlElement("w:rPr")
    style = OxmlElement("w:rStyle")
    style.set(qn("w:val"), "CommentReference")
    rpr.append(style)
    r.append(rpr)
    ref = OxmlElement("w:commentReference")
    ref.set(qn("w:id"), str(comment_id))
    r.append(ref)
    return r


def word_date(value: Any) -> str:
    """``YYYY-MM-DDTHH:MM:SSZ``. An unparseable or absent stamp becomes NOW —
    a revision must carry a date, and inventing 1970 would date the proposal."""
    raw = (str(value) if value else "").strip()
    if raw:
        try:
            dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        except (TypeError, ValueError):
            pass
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def initials(author: str | None) -> str:
    parts = [p for p in re.split(r"[\s._@-]+", str(author or "")) if p]
    return ("".join(p[0] for p in parts[:3]).upper() or "IC")[:4]


def _xml_escape(value: Any) -> str:
    return (str(value if value is not None else "")
            .replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
            .replace('"', "&quot;"))


def _para_id(n: int) -> str:
    """8 upper-hex, never ``00000000`` — Word treats that as unset and the
    thread parentage in ``commentsExtended`` silently stops linking."""
    return f"{0x0A000001 + n:08X}"


# ── planning one paragraph ────────────────────────────────────────────────────

def _snap_comment_ranges(comments: list[dict], changes: list[dict]) -> None:
    """Widen a comment range that starts or ends INSIDE a change span out to
    that change's boundary, and say so on the item.

    A ``w:commentRangeStart`` cannot sit between the runs of one revision
    without splitting it, so the range is widened rather than the revision
    split. Only the highlighted RANGE moves; the comment's body, its author and
    its recorded ``anchor_text`` are untouched, and the widening is recorded as
    ``range_widened`` rather than applied quietly.
    """
    for c in comments:
        for ch in changes:
            if ch["start"] < c["start"] < ch["end"]:
                c["start"] = ch["start"]
                c["range_widened"] = True
            if ch["start"] < c["end"] < ch["end"]:
                c["end"] = ch["end"]
                c["range_widened"] = True


def plan_paragraph(text: str, base: int, changes: list[dict],
                   comments: list[dict]) -> list[dict]:
    """The ordered emit plan for ONE paragraph.

    Returns ``[{"op": ...}]`` where ``op`` is ``text`` (a plain run),
    ``change`` (a diff block) or ``comment_start`` / ``comment_end``. Pure: it
    reads nothing, writes nothing, and builds no XML, so the walk can be
    asserted directly.
    """
    changes = sorted(changes, key=lambda c: (c["start"], c["end"]))
    _snap_comment_ranges(comments, changes)
    cuts = {0, len(text)}
    for c in changes:
        cuts.add(c["start"] - base)
        cuts.add(c["end"] - base)
    for c in comments:
        cuts.add(c["start"] - base)
        cuts.add(c["end"] - base)
    ordered = sorted(x for x in cuts if 0 <= x <= len(text))

    starts: dict[int, list[dict]] = {}
    ends: dict[int, list[dict]] = {}
    for c in comments:
        starts.setdefault(c["start"] - base, []).append(c)
        ends.setdefault(c["end"] - base, []).append(c)
    change_at = {c["start"] - base: c for c in changes}

    plan: list[dict] = []
    cursor = 0
    while cursor <= len(text):
        for c in ends.get(cursor, []):
            plan.append({"op": "comment_end", "comment": c})
        for c in starts.get(cursor, []):
            plan.append({"op": "comment_start", "comment": c})
        if cursor == len(text):
            break
        change = change_at.get(cursor)
        if change is not None:
            plan.append({"op": "change", "change": change})
            cursor = change["end"] - base
            continue
        nxt = next((x for x in ordered if x > cursor), len(text))
        chunk = text[cursor:nxt]
        if chunk:
            plan.append({"op": "text", "text": chunk})
        cursor = nxt
    return plan


# ── selecting what can be placed ──────────────────────────────────────────────

def _defer(item: dict, reason: str, extra: dict | None = None) -> dict:
    out = {
        "kind": item.get("kind"),
        "id": item.get("id"),
        "section_id": item.get("section_id"),
        "status": item.get("status"),
        "reason": reason,
        "anchor_state": (item.get("anchor") or {}).get("state"),
        "anchor_reason": (item.get("anchor") or {}).get("reason"),
        "author": item.get("author"),
        "body": item.get("body") or "",
        "anchor_text": item.get("anchor_text") or "",
        "suggested_content": item.get("suggested_content") or "",
    }
    if extra:
        out.update(extra)
    return out


def select_section_items(section: dict, content: str | None,
                         paragraphs: list[dict]) -> tuple[list[dict], list[dict], list[dict]]:
    """Split one rail section's items into placeable changes, placeable
    comments and deferrals, WITHOUT building any XML.

    A change is placeable only when the rail gave it a position (which means
    ``verify_anchor`` said the span holds NOW), it is ``pending``, its span
    lies inside ONE paragraph, its slice really equals its anchor text, and its
    word diff round-trips. Every other outcome is a deferral carrying which of
    those it was.
    """
    from tools.document_intelligence import word_diff

    placeable_changes: list[dict] = []
    placeable_comments: list[dict] = []
    deferred: list[dict] = []
    text = content or ""

    items = list(section.get("positioned") or []) + list(section.get("unpositioned") or [])
    for item in items:
        pos = item.get("position")
        if pos is None:
            deferred.append(_defer(item, "no_verified_anchor"))
            continue
        start = pos
        anchor_text = item.get("anchor_text") or ""
        end = start + len(anchor_text)
        para = next((p for p in paragraphs if p["start"] <= start <= p["end"]), None)
        if para is None or end > para["end"]:
            deferred.append(_defer(item, "anchor_spans_paragraphs"))
            continue
        if text[start:end] != anchor_text:
            deferred.append(_defer(item, "anchor_text_mismatch",
                                   {"found_text": text[start:end]}))
            continue

        if item.get("kind") == "comment":
            placeable_comments.append({**item, "start": start, "end": end,
                                       "paragraph": para["start"]})
            continue

        if item.get("status") not in TRACKED_STATUSES:
            deferred.append(_defer(item, "not_pending"))
            continue
        diff = word_diff.diff_words(anchor_text, item.get("suggested_content") or "")
        if not diff.get("round_trip") or diff.get("spans") is None:
            deferred.append(_defer(item, "diff_not_round_trip"))
            continue
        placeable_changes.append({**item, "start": start, "end": end,
                                  "paragraph": para["start"], "spans": diff["spans"]})

    kept: list[dict] = []
    last_end = -1
    for ch in sorted(placeable_changes, key=lambda c: (c["start"], c["end"])):
        if ch["start"] < last_end:
            deferred.append(_defer(ch, "anchor_overlap"))
            continue
        kept.append(ch)
        last_end = ch["end"]
    return kept, placeable_comments, deferred


# ── the build ─────────────────────────────────────────────────────────────────

def _add_heading(document, text: str, level: int) -> None:
    if not text:
        return
    try:
        document.add_heading(text, level=level)
    except (KeyError, ValueError):  # a template without heading styles
        para = document.add_paragraph()
        para.add_run(text).bold = True


def build_tracked_docx(bundle: dict, rail: dict, out_path, *, title: str,
                       classification: str,
                       author_fallback: str = _FALLBACK_AUTHOR) -> dict:
    """Write a .docx carrying real Word revisions and comment threads.

    ``bundle`` is ``exporter.load_version``'s; ``rail`` is
    ``review_rail.build_rail``'s. Returns the render report — see the module
    docstring for why its counts are ``None`` and never ``0`` for an
    ``unmeasurable`` rail.
    """
    if not DOCX_AVAILABLE:
        raise TrackedDocxError("python-docx not installed")

    from docx import Document

    from tools.govcon.rfi_docx_exporter import _add_fouo_header_footer, _set_margins

    document = Document()
    _set_margins(document)
    # The classification LABEL is the marking, never the exporter's hard-coded
    # FOUO default — the same rule exporter.render already follows for `docx`.
    _add_fouo_header_footer(document, classification)

    rail_state = rail.get("state") if isinstance(rail, dict) else "unmeasurable"
    measurable = rail_state in ("items", "empty")
    by_section = {s.get("section_id"): s for s in (rail.get("sections") or [])} \
        if isinstance(rail, dict) else {}

    document.add_heading(title or "Document", level=0)
    version = bundle.get("version") or {}
    intro = document.add_paragraph()
    intro.add_run(
        f"Version {version.get('version_no')} · status: "
        f"{version.get('status') or 'unknown'} · tracked review copy"
    ).italic = True

    rev_id = 1000
    comment_id = 0
    comment_parts: list[str] = []
    extended_parts: list[str] = []
    deferred: list[dict] = []
    tracked_changes = 0
    revision_elements = 0
    comments_rendered = 0
    round_trip_failures: list[str] = []

    for sec in bundle.get("sections") or []:
        section_id = sec.get("section_id")
        content = sec.get("content") or ""
        _add_heading(document, (sec.get("heading") or "").strip(), 2)

        paragraphs = paragraph_spans(content)
        if not spans_round_trip(content, paragraphs):  # pragma: no cover - invariant
            round_trip_failures.append(section_id)
            for line in paragraphs:
                document.add_paragraph(line["text"])
            rail_sec = by_section.get(section_id) or {}
            for item in (list(rail_sec.get("positioned") or [])
                         + list(rail_sec.get("unpositioned") or [])):
                deferred.append(_defer(item, "section_round_trip_failed"))
            continue

        rail_sec = by_section.get(section_id) or {}
        changes, comments, sec_deferred = select_section_items(
            rail_sec, content, paragraphs)
        deferred.extend(sec_deferred)

        for para in paragraphs:
            p = document.add_paragraph()._p
            p_changes = [c for c in changes if c["paragraph"] == para["start"]]
            p_comments = [c for c in comments if c["paragraph"] == para["start"]]
            plan = plan_paragraph(para["text"], para["start"], p_changes, p_comments)
            for step in plan:
                op = step["op"]
                if op == "text":
                    p.append(_run(step["text"]))
                elif op == "comment_start":
                    step["comment"]["_wid"] = comment_id
                    p.append(_comment_marker("commentRangeStart", comment_id))
                    comment_id += 1
                elif op == "comment_end":
                    wid = step["comment"].get("_wid")
                    if wid is None:  # pragma: no cover - a snapped pair always opens first
                        continue
                    p.append(_comment_marker("commentRangeEnd", wid))
                    ids = _emit_comment_thread(step["comment"], wid,
                                               comment_parts, extended_parts,
                                               author_fallback)
                    for cid in ids:
                        p.append(_comment_reference(cid))
                    comment_id = max(comment_id, max(ids) + 1)
                    comments_rendered += len(ids)
                elif op == "change":
                    ch = step["change"]
                    author = str(ch.get("author") or author_fallback)
                    date = word_date(ch.get("created_at"))
                    for span in ch["spans"]:
                        tag = span.get("tag")
                        body = span.get("text") or ""
                        if not body:
                            continue
                        if tag == "equal":
                            p.append(_run(body))
                            continue
                        p.append(_revision("ins" if tag == "insert" else "del",
                                           rev_id, author, date, body))
                        rev_id += 1
                        revision_elements += 1
                    tracked_changes += 1

    for item in (rail.get("unplaced") or []) if isinstance(rail, dict) else []:
        deferred.append(_defer(item, "unplaced",
                               {"unplaced_reason": item.get("unplaced_reason")}))

    _write_appendix(document, deferred, rail_state, measurable)

    if comment_parts:
        _attach_comment_parts(document, comment_parts, extended_parts)

    document.save(str(out_path))
    return {
        "path": str(out_path),
        "rail_state": rail_state,
        # None, never 0: a rail nobody could read has an UNKNOWN number of
        # revisions, and 0 asserts it has none.
        "tracked_changes": tracked_changes if measurable else None,
        "revision_elements": revision_elements if measurable else None,
        "comments": comments_rendered if measurable else None,
        "deferred": deferred,
        "deferred_count": len(deferred) if measurable else None,
        "section_round_trip_failures": round_trip_failures,
    }


def _emit_comment_thread(comment: dict, root_id: int, comment_parts: list[str],
                         extended_parts: list[str], author_fallback: str) -> list[int]:
    """One ``w:comment`` for the root and one per reply, threaded.

    The reply parentage lives in ``commentsExtended.xml``: without it Word
    renders every reply as its own top-level comment, so the thread a reviewer
    had on the canvas arrives as a pile of unrelated remarks.
    """
    ids = [root_id]
    root_para = _para_id(root_id)
    comment_parts.append(_comment_xml(
        root_id, comment.get("author") or author_fallback,
        word_date(comment.get("created_at")), root_para,
        _comment_body(comment)))
    done = "1" if comment.get("status") == "resolved" else "0"
    extended_parts.append(
        f'<w15:commentEx w15:paraId="{root_para}" w15:done="{done}"/>')

    next_id = root_id + 1
    for reply in comment.get("replies") or []:
        para = _para_id(next_id)
        comment_parts.append(_comment_xml(
            next_id, reply.get("author") or author_fallback,
            word_date(reply.get("created_at")), para,
            str(reply.get("comment") or "")))
        extended_parts.append(
            f'<w15:commentEx w15:paraId="{para}" '
            f'w15:paraIdParent="{root_para}" w15:done="{done}"/>')
        ids.append(next_id)
        next_id += 1
    return ids


def _comment_body(comment: dict) -> str:
    body = str(comment.get("body") or "")
    category = comment.get("category")
    return f"[{category}] {body}" if category else body


def _comment_xml(comment_id: int, author: str, date: str, para_id: str,
                 body: str) -> str:
    return (
        f'<w:comment w:id="{comment_id}" w:author="{_xml_escape(author)}" '
        f'w:date="{date}" w:initials="{_xml_escape(initials(author))}">'
        f'<w:p w14:paraId="{para_id}"><w:r><w:t xml:space="preserve">'
        f'{_xml_escape(body)}</w:t></w:r></w:p></w:comment>'
    )


def _attach_comment_parts(document, comment_parts: list[str],
                          extended_parts: list[str]) -> None:
    """Add ``comments.xml`` and ``commentsExtended.xml`` as package parts.

    python-docx knows neither, so both are authored as raw blobs with their own
    namespace declarations and related from the document part. Relating them
    registers their content types in ``[Content_Types].xml`` automatically.
    """
    package = document.part.package
    comments_xml = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        f'<w:comments xmlns:w="{NS_W}" xmlns:w14="{NS_W14}">'
        + "".join(comment_parts) + "</w:comments>"
    ).encode("utf-8")
    document.part.relate_to(
        Part(PackURI("/word/comments.xml"), CT_COMMENTS, comments_xml, package),
        RT.COMMENTS)

    extended_xml = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        f'<w15:commentsEx xmlns:w15="{NS_W15}" xmlns:w14="{NS_W14}">'
        + "".join(extended_parts) + "</w15:commentsEx>"
    ).encode("utf-8")
    document.part.relate_to(
        Part(PackURI("/word/commentsExtended.xml"), CT_COMMENTS_EXTENDED,
             extended_xml, package),
        RT_COMMENTS_EXTENDED)


def _write_appendix(document, deferred: list[dict], rail_state: str,
                    measurable: bool) -> None:
    """Everything that did NOT become a revision, said in words.

    Deliberately plain paragraphs: these are a REPORT, not a proposal, so a
    Word reviewer cannot Accept or Reject something this exporter could not
    place — which would write a decision back to nothing.
    """
    document.add_page_break()
    document.add_heading("Review items not shown as tracked changes", level=1)
    if not measurable:
        para = document.add_paragraph()
        para.add_run(
            "The review rail could not be read for this version "
            f"(state: {rail_state}). This document therefore shows NO tracked "
            "changes and NO comments, and that is not a statement that there "
            "are none — nobody could measure them."
        ).bold = True
        return
    if not deferred:
        document.add_paragraph(
            "Every recorded change and comment on this version was placed as a "
            "tracked revision or a comment above.")
        return
    document.add_paragraph(
        f"{len(deferred)} item(s) are recorded on this version and are NOT "
        "shown inline. Each is listed with the reason it could not be placed; "
        "none has been dropped, and none has been attached to a position that "
        "was not measured.")
    for item in deferred:
        para = document.add_paragraph()
        para.add_run(
            f"{item.get('kind')} {item.get('id')} — {item.get('reason')}"
        ).bold = True
        if item.get("status"):
            document.add_paragraph(f"    status: {item['status']}")
        if item.get("anchor_state"):
            document.add_paragraph(
                f"    anchor: {item['anchor_state']}"
                f" ({item.get('anchor_reason') or 'no reason recorded'})")
        if item.get("anchor_text"):
            document.add_paragraph(f"    anchors to: {item['anchor_text']}")
        if item.get("suggested_content"):
            document.add_paragraph(f"    proposes: {item['suggested_content']}")
        if item.get("body"):
            document.add_paragraph(f"    says: {item['body']}")
