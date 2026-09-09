#!/usr/bin/env python3
# CUI // SP-CTI
"""dwr-word-02 — read a reviewer's Word revisions back in, and RECONCILE them.

dwr-word-01 sends a review copy OUT: the version's pending redlines as real
``w:ins``/``w:del`` revisions and its comment threads as native Word comments.
Nothing read one back. A reviewer who opened it in Word, struck a clause and
typed a replacement had produced exactly the artifact this canvas exists to
adjudicate, and the only way it re-entered ICDEV was somebody retyping it.

THE HARD PART IS NOT PARSING, IT IS RECONCILIATION, AND THE REASON IS THAT
BOTH SIDES MOVE. The reviewer had the document as it was when it was exported.
While they were reading it, a docmod sweep could draft a redline over the same
sentence, a human could accept one, ``supersede_suggestion`` could retire one
whose anchor drifted, and a section could be regenerated. So a revision that
arrives back is not a proposal about the document — it is a proposal about a
document that may no longer exist. Every verdict here is about THAT.

THREE VERDICTS OVER A REVISION, AND NO FOURTH:

  matched      the revision's anchor located to exactly ONE span of exactly one
               section, and nothing on the ICDEV side contests that span.
               ``origin`` says WHICH thing it is — ``own_proposal`` (one of our
               own exported redlines, come back undecided) or ``reviewer_edit``
               (the reviewer's own new writing). Both are matched; they are not
               the same thing and are never merged.
  conflicting  it located, AND the ICDEV side also changed that span. Surfaced
               for adjudication with BOTH sides carried whole. Never merged,
               never won by one side, and nothing here writes either.
  unmatched    it could not be located. REPORTED BY NAME with the reason, never
               dropped. A dropped revision is a reviewer's edit that silently
               ceased to exist, which is worse than one nobody could place.

NOTHING HERE DECIDES, AND NOTHING HERE WRITES. This module executes no INSERT,
UPDATE or DELETE and calls no writer in ``suggestion_store`` or
``annotation_store`` — pinned by an AST test, because the failure mode is a
later edit threading a "just apply the matched ones" flag through, and a
behavioural test over today's callers would still pass the day it happens.
A ``matched`` verdict says WHERE a revision goes, never that it may go there:
the accept door (cef-ui-03) is the only writer of ``dic_sections.content``, a
human decides at it, and turning a reviewer's Word edit into an applied change
without one is the "never auto-apply" prohibition wearing an import's name.

ABSENCE IS NOT A DECISION, and this is the one inference the module refuses to
make. If an exported redline does NOT come back as a revision, that is
consistent with the reviewer having ACCEPTED it (Word then writes the text
plain), REJECTED it (the text is simply gone), never having reached it, having
deleted the whole paragraph, or with the upload being a different document
altogether. One observation, five causes, and four of them are not decisions.
So an ICDEV item with no counterpart in the upload is reported under
``absent_from_upload`` — a labelled ABSENCE, never an accept and never a
reject. Reading a decision out of it would write a human's verdict that no
human gave, onto the append-only ``dic_suggestion_decisions`` chain.

LOCATION IS MEASURED AGAINST THE PARAGRAPH THE REVIEWER RECEIVED. A revision's
coordinate space is its paragraph's ``before`` text — equal runs plus deleted
runs, i.e. the paragraph with every revision REJECTED, which is exactly what
was in the .docx when it left here. That text is looked for in the version's
sections through ``docx_revisions.paragraph_spans`` — dwr-word-01's partition,
IMPORTED and not re-derived, because an offset that means one thing on the way
out and another on the way back is the whole class of defect this series is
about. Exactly one match locates. Zero or several do not, and an ambiguous
match is never resolved by picking one (``resolve_anchor``'s rule).

A REVISION THAT LOCATES BY SPAN BUT NOT BY PARAGRAPH IS A CONFLICT, NOT A
MATCH. If the paragraph the reviewer edited no longer exists in the version but
their deleted text still occurs exactly once, then BOTH SIDES CHANGED that
paragraph — theirs in Word, ours in the database — and reporting it as a clean
match would hand a human a merge nobody measured. ``base_paragraph_changed``.

A COMMENT IS NOT AN EDIT, so a comment is never ``conflicting``. It locates or
it does not. A comment anchored to a span some pending redline proposes to
replace is the NORMAL case — it is a reviewer asking about a proposal — and
calling that a conflict would flag every useful comment on the board. The
contested span is reported as CONTEXT on the comment (``contested_by``) and
never as its verdict.

COUNTS ARE ``None`` AND NEVER ``0`` WHEN NOTHING WAS MEASURED. An unreadable
.docx, an unknown version and an ``unmeasurable`` review rail each report
themselves; a reconciliation reporting "0 conflicts" for a rail nobody could
read is indistinguishable from a clean round trip, which is the reassurance
this whole card series refuses. The RAIL being unmeasurable makes the whole
report unmeasurable and not merely the contest half: ``matched`` asserts
"nothing contests this span", and that is a claim about the rail.

PARSING IS MEASURABLE INDEPENDENTLY OF THE BOARD, and is reported separately.
What is in the file is a fact about the file; ``--parse-only`` answers it with
no database at all, which is also what an operator holding a .docx from a
customer has before they know which version it belongs to.

THE FILE IS UNTRUSTED INPUT AND IS BOUNDED BEFORE IT IS PARSED. A .docx is a
zip of XML: a declared DOCTYPE is REFUSED outright (entity expansion), each
part is refused above ``ICDEV_DOCX_IMPORT_MAX_PART_BYTES`` on its DECLARED
uncompressed size before a byte is decompressed, and only the three parts this
module names are ever read. A bound that is hit is REPORTED (``truncated``,
``limits``), never a quietly short list. See docs/security/sandbox-coverage.md
Gap 70.

    python -m tools.document_intelligence.docx_review_import \
        --file review.docx --version <version_id> [--json]
    python -m tools.document_intelligence.docx_review_import \
        --file review.docx --parse-only [--json]

Exit 0 = a report was produced (whatever it says). Exit 2 = it could not be.
Report only, deliberately no ``--gate`` (kpr-fix-03).
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import zipfile
from xml.etree import ElementTree as ET

from tools.document_intelligence.docx_revisions import (
    NS_W,
    NS_W14,
    NS_W15,
    paragraph_spans,
)
from tools.logging.icdev_logger import get_logger

logger = get_logger(__name__)

W = f"{{{NS_W}}}"
W14 = f"{{{NS_W14}}}"
W15 = f"{{{NS_W15}}}"

#: The only parts read. Never a glob, never a walk of the archive.
PART_DOCUMENT = "word/document.xml"
PART_COMMENTS = "word/comments.xml"
PART_COMMENTS_EXTENDED = "word/commentsExtended.xml"
READ_PARTS = (PART_DOCUMENT, PART_COMMENTS, PART_COMMENTS_EXTENDED)

#: What a TEXT token of the run stream contributes to (the stream also carries
#: ``comment_start`` / ``comment_end`` / ``comment_reference`` markers, which
#: carry no text). ``ins_del`` is text that was inserted and then deleted again
#: -- Word nests ``w:del`` inside ``w:ins`` -- so it was never in the document
#: the reviewer received and is not in the document they propose, and it
#: belongs to NEITHER side.
TOKEN_KINDS = ("equal", "insert", "delete", "ins_del")

#: A revision's verdict. Three, and no fourth.
VERDICTS = ("matched", "conflicting", "unmatched")

#: Which THING a matched item is. ``matched`` says where it goes; this says
#: what it is, and the two are never collapsed.
ORIGINS = ("own_proposal", "reviewer_edit", "own_comment", "reviewer_comment")

#: Every reason this module can refuse to locate a revision. None of them is a
#: silent drop and none of them ever becomes a position.
UNMATCHED_REASONS = (
    "paragraph_not_found",
    "paragraph_ambiguous",
    "empty_paragraph_text",
    "anchor_ambiguous",
    "insertion_point_unlocatable",
    "paragraph_mark_revision",
    "heading_not_anchorable",
    "comment_range_spans_paragraphs",
    "comment_range_unlocatable",
    "comment_body_absent",
)

#: Every way the ICDEV side can contest a span the reviewer edited.
CONFLICT_REASONS = (
    "base_paragraph_changed",
    "competing_proposal",
    "decided_since_export",
)

#: A suggestion status that is a DECISION already taken. A reviewer's Word edit
#: over one of these arrived after somebody had adjudicated it.
DECIDED_STATUSES = ("accepted", "rejected", "superseded", "applied")

REPORT_STATES = ("items", "empty", "unmeasurable")

MAX_PART_BYTES_ENV = "ICDEV_DOCX_IMPORT_MAX_PART_BYTES"
MAX_REVISIONS_ENV = "ICDEV_DOCX_IMPORT_MAX_REVISIONS"
DEFAULT_MAX_PART_BYTES = 64 * 1024 * 1024
DEFAULT_MAX_REVISIONS = 5000


class DocxReadError(RuntimeError):
    """The .docx could not be read. Never confused with "it holds nothing"."""


def limits() -> dict:
    """The bounds this parse applies, so a caller can report them."""
    return {
        "max_part_bytes": _int_env(MAX_PART_BYTES_ENV, DEFAULT_MAX_PART_BYTES),
        "max_revisions": _int_env(MAX_REVISIONS_ENV, DEFAULT_MAX_REVISIONS),
    }


def _int_env(name: str, default: int) -> int:
    try:
        value = int(str(os.environ.get(name, "")).strip())
    except (TypeError, ValueError):
        return default
    return value if value > 0 else default


# ── reading the package ───────────────────────────────────────────────────────

def read_part(path, part: str) -> bytes | None:
    """One named part of the .docx, bounded, or ``None`` when it is absent.

    ``comments.xml`` is legitimately absent from a document nobody commented
    on, so absence is ``None`` and not an error. A DECLARED uncompressed size
    over the cap is refused BEFORE decompression — checking afterwards would
    already have paid the cost the cap exists to refuse.
    """
    cap = limits()["max_part_bytes"]
    try:
        with zipfile.ZipFile(path) as archive:
            try:
                info = archive.getinfo(part)
            except KeyError:
                return None
            if info.file_size > cap:
                raise DocxReadError(
                    f"{part} declares {info.file_size} uncompressed bytes, over "
                    f"the {cap}-byte cap ({MAX_PART_BYTES_ENV})")
            data = archive.read(part)
    except DocxReadError:
        raise
    except (OSError, zipfile.BadZipFile, RuntimeError) as exc:
        raise DocxReadError(f"cannot read {part}: {exc}") from exc
    if len(data) > cap:  # pragma: no cover - a zip that lied about file_size
        raise DocxReadError(f"{part} decompressed to {len(data)} bytes, over the cap")
    return data


def parse_xml(data: bytes, part: str):
    """Parse one part, refusing a declared DOCTYPE.

    ``xml.etree`` does not resolve EXTERNAL entities, but it does expand
    INTERNAL ones, which is the billion-laughs shape. No legitimate OOXML part
    carries a DTD, so refusing the declaration outright costs nothing real and
    removes the expansion surface entirely — a bound on untrusted input, not a
    guess about which entities are safe.
    """
    if b"<!DOCTYPE" in data[:2048] or b"<!doctype" in data[:2048]:
        raise DocxReadError(f"{part} declares a DOCTYPE; refused unparsed")
    try:
        return ET.fromstring(data)
    except ET.ParseError as exc:
        raise DocxReadError(f"{part} is not well-formed XML: {exc}") from exc


# ── the run stream ────────────────────────────────────────────────────────────

def _run_text(run) -> str:
    """The text of one ``w:r``.

    ``w:delText`` is where Word puts deleted text and is as load-bearing on the
    way in as it was on the way out. ``w:tab`` and ``w:br`` carry a character
    the section content also carries, so dropping them would shift every offset
    after them.
    """
    out: list[str] = []
    for child in run:
        tag = child.tag
        if tag in (f"{W}t", f"{W}delText"):
            out.append(child.text or "")
        elif tag == f"{W}tab":
            out.append("\t")
        elif tag in (f"{W}br", f"{W}cr"):
            out.append("\n")
        elif tag == f"{W}noBreakHyphen":
            out.append("-")
    return "".join(out)


def _revision_attrs(element) -> dict:
    return {"author": element.get(f"{W}author") or "",
            "date": element.get(f"{W}date") or ""}


def token_stream(paragraph) -> list[dict]:
    """Every run of one ``w:p``, in document order, tagged by what it is.

    ``[{"kind", "text", "author", "date"}]`` for text, plus
    ``{"kind": "comment_start"|"comment_end", "comment_id"}`` markers. Pure: it
    builds no offsets and consults nothing, so the walk can be asserted alone.

    ``w:ins`` and ``w:del`` NEST — Word writes text that was inserted and then
    struck again as a ``w:del`` inside a ``w:ins`` — so this is a recursive
    descent carrying both flags rather than a scan of the paragraph's direct
    children, which would read the inner deletion as an ordinary one and put
    text into ``before`` that was never there.
    """
    tokens: list[dict] = []

    def walk(node, in_ins: bool, in_del: bool, attrs: dict) -> None:
        for child in node:
            tag = child.tag
            if tag == f"{W}pPr":
                continue
            if tag == f"{W}ins":
                walk(child, True, in_del, _revision_attrs(child))
            elif tag == f"{W}del":
                walk(child, in_ins, True, _revision_attrs(child))
            elif tag == f"{W}r":
                text = _run_text(child)
                reference = child.find(f"{W}commentReference")
                if reference is not None:
                    # A REPLY has its own reference run and, in dwr-word-01's
                    # export, no range of its own: the thread is highlighted
                    # once. Recording the reference is what lets a reply be
                    # anchored to its root's range rather than dropped.
                    tokens.append({"kind": "comment_reference",
                                   "comment_id": reference.get(f"{W}id")})
                if not text:
                    continue
                if in_ins and in_del:
                    kind = "ins_del"
                elif in_ins:
                    kind = "insert"
                elif in_del:
                    kind = "delete"
                else:
                    kind = "equal"
                tokens.append({"kind": kind, "text": text,
                               "author": attrs.get("author") or "",
                               "date": attrs.get("date") or ""})
            elif tag == f"{W}commentRangeStart":
                tokens.append({"kind": "comment_start",
                               "comment_id": child.get(f"{W}id")})
            elif tag == f"{W}commentRangeEnd":
                tokens.append({"kind": "comment_end",
                               "comment_id": child.get(f"{W}id")})
            elif tag in (f"{W}hyperlink", f"{W}smartTag", f"{W}sdt",
                         f"{W}sdtContent"):
                walk(child, in_ins, in_del, attrs)
    walk(paragraph, False, False, {})
    return tokens


def paragraph_mark_revision(paragraph) -> dict | None:
    """Is the paragraph MARK itself inserted or deleted?

    A deleted paragraph mark merges this paragraph into the next and an
    inserted one splits it — a structural edit with no span in ICDEV's model,
    which anchors offsets INSIDE one section's content. It is reported rather
    than ignored: silently dropping it loses a reviewer's edit.
    """
    ppr = paragraph.find(f"{W}pPr")
    if ppr is None:
        return None
    rpr = ppr.find(f"{W}rPr")
    if rpr is None:
        return None
    for tag, kind in ((f"{W}ins", "insert"), (f"{W}del", "delete")):
        element = rpr.find(tag)
        if element is not None:
            return {"kind": kind, **_revision_attrs(element)}
    return None


def fold_tokens(tokens: list[dict]) -> dict:
    """Turn one paragraph's token stream into both sides and its revisions.

    ``before`` is equal + delete — the paragraph with every revision REJECTED,
    which is the text that was in the .docx when it left here and therefore the
    ONLY coordinate space in which an ICDEV anchor means anything. ``after`` is
    equal + insert. ``ins_del`` text is in neither, by definition.

    A GROUP is a maximal run of adjacent non-equal tokens: Word writes one
    replacement as a ``w:del`` immediately followed by a ``w:ins``, and
    reporting those as two unrelated revisions would ask a human to adjudicate
    half a sentence twice.
    """
    before_parts: list[str] = []
    after_parts: list[str] = []
    before_len = 0
    groups: list[dict] = []
    open_ranges: dict[str, dict] = {}
    comment_ranges: list[dict] = []
    referenced: list[str] = []
    current: dict | None = None

    def close_group() -> None:
        nonlocal current
        if current is not None:
            current["before_end"] = before_len
            groups.append(current)
            current = None

    for token in tokens:
        kind = token["kind"]
        if kind == "comment_start":
            open_ranges[token["comment_id"]] = {
                "comment_id": token["comment_id"], "before_start": before_len}
            continue
        if kind == "comment_end":
            opened = open_ranges.pop(token["comment_id"], None)
            if opened is None:
                # A range that ends without starting in THIS paragraph. Its
                # extent here is unknown; reported, never guessed at as 0.
                comment_ranges.append({"comment_id": token["comment_id"],
                                       "before_start": None,
                                       "before_end": before_len,
                                       "spans_paragraphs": True})
                continue
            opened["before_end"] = before_len
            opened["spans_paragraphs"] = False
            comment_ranges.append(opened)
            continue
        if kind == "comment_reference":
            if token["comment_id"] not in referenced:
                referenced.append(token["comment_id"])
            continue

        text = token["text"]
        if kind == "equal":
            close_group()
            before_parts.append(text)
            after_parts.append(text)
            before_len += len(text)
            continue

        if current is None:
            current = {"before_start": before_len, "before_text": "",
                       "after_text": "", "authors": [], "dates": [],
                       "kinds": []}
        author = token.get("author") or ""
        date = token.get("date") or ""
        if author and author not in current["authors"]:
            current["authors"].append(author)
        if date and date not in current["dates"]:
            current["dates"].append(date)
        if kind not in current["kinds"]:
            current["kinds"].append(kind)
        if kind == "delete":
            before_parts.append(text)
            current["before_text"] += text
            before_len += len(text)
        elif kind == "insert":
            after_parts.append(text)
            current["after_text"] += text
        # ins_del contributes to NEITHER side, and is recorded only as a kind.
    close_group()

    for still_open in open_ranges.values():
        still_open["before_end"] = None
        still_open["spans_paragraphs"] = True
        comment_ranges.append(still_open)

    for rng in comment_ranges:
        if rng["comment_id"] not in referenced:
            referenced.append(rng["comment_id"])
    return {"before": "".join(before_parts), "after": "".join(after_parts),
            "groups": groups, "comment_ranges": comment_ranges,
            "comment_ids": referenced}


# ── comments.xml ──────────────────────────────────────────────────────────────

def _element_text(element) -> str:
    """Every ``w:t`` under an element, joined — Word splits one sentence across
    runs freely, so reading the first run only truncates a comment."""
    return "".join(node.text or "" for node in element.iter(f"{W}t"))


def parse_comments(comments_xml: bytes | None,
                   extended_xml: bytes | None) -> dict:
    """``{comment_id: {...}}`` with thread parentage attached where declared.

    Parentage lives in ``commentsExtended.xml``, keyed by the ``w14:paraId`` of
    the comment's first paragraph — NOT by comment id — so the two parts are
    joined on that, exactly the way dwr-word-01 emits them. A reply whose
    parent paragraph id names no comment we read keeps ``parent_id: None`` and
    carries ``parent_para_id``: an unresolvable parent is reported, never
    silently promoted to a root remark.
    """
    if not comments_xml:
        return {}
    root = parse_xml(comments_xml, PART_COMMENTS)
    by_para: dict[str, str] = {}
    comments: dict[str, dict] = {}
    for element in root.findall(f"{W}comment"):
        cid = element.get(f"{W}id")
        if cid is None:
            continue
        first = element.find(f"{W}p")
        para_id = first.get(f"{W14}paraId") if first is not None else None
        if para_id:
            by_para[para_id] = cid
        comments[cid] = {
            "comment_id": cid,
            "author": element.get(f"{W}author") or "",
            "date": element.get(f"{W}date") or "",
            "initials": element.get(f"{W}initials") or "",
            "body": _element_text(element),
            "para_id": para_id,
            "parent_para_id": None,
            "parent_id": None,
            "done": None,
        }

    if extended_xml:
        extended = parse_xml(extended_xml, PART_COMMENTS_EXTENDED)
        for element in extended.findall(f"{W15}commentEx"):
            cid = by_para.get(element.get(f"{W15}paraId") or "")
            if cid is None:
                continue
            parent_para = element.get(f"{W15}paraIdParent")
            done = element.get(f"{W15}done")
            comments[cid]["parent_para_id"] = parent_para
            comments[cid]["parent_id"] = by_para.get(parent_para or "")
            if done is not None:
                comments[cid]["done"] = done in ("1", "true")
    return comments


# ── the parse ─────────────────────────────────────────────────────────────────

def read_revisions(path) -> dict:
    """Everything the .docx says, and nothing about the board.

    ``state`` is ``parsed`` or ``unreadable``; on ``unreadable`` every count is
    ``None`` and ``reason`` names what failed. A document with no revisions and
    no comments is ``parsed`` with MEASURED zeroes — a real answer, and never
    spelled the same way as a file nobody could open.
    """
    out: dict = {"path": str(path), "state": "unreadable", "reason": None,
                 "paragraphs": [], "comments": {}, "truncated": False,
                 "limits": limits(),
                 "counts": {"revisions": None, "comments": None,
                            "paragraph_marks": None, "paragraphs": None}}
    try:
        document = read_part(path, PART_DOCUMENT)
        if document is None:
            out["reason"] = f"{PART_DOCUMENT} absent; not a Word document"
            return out
        root = parse_xml(document, PART_DOCUMENT)
        comments = parse_comments(read_part(path, PART_COMMENTS),
                                  read_part(path, PART_COMMENTS_EXTENDED))
    except DocxReadError as exc:
        out["reason"] = str(exc)
        return out

    cap = limits()["max_revisions"]
    revisions = 0
    marks = 0
    paragraphs: list[dict] = []
    for index, paragraph in enumerate(root.iter(f"{W}p")):
        folded = fold_tokens(token_stream(paragraph))
        mark = paragraph_mark_revision(paragraph)
        if not folded["groups"] and not folded["comment_ids"] and mark is None:
            # Nothing to reconcile. The title, the intro, the appendix and
            # every untouched paragraph land here, which is why an untouched
            # round trip reports no findings rather than hundreds.
            continue
        revisions += len(folded["groups"])
        marks += 1 if mark else 0
        paragraphs.append({"index": index, "paragraph_mark": mark, **folded})
        if revisions >= cap:
            out["truncated"] = True
            break

    out.update({
        "state": "parsed", "paragraphs": paragraphs, "comments": comments,
        "counts": {"revisions": revisions, "comments": len(comments),
                   "paragraph_marks": marks, "paragraphs": len(paragraphs)},
    })
    return out


# ── locating a span in the version ────────────────────────────────────────────

def section_paragraphs(bundle: dict) -> list[dict]:
    """Every paragraph of every section, with its section and ABSOLUTE offsets.

    ``paragraph_spans`` is dwr-word-01's, imported. Two partitions of one
    section — one used to write the .docx and one to read it — is how an offset
    comes to mean two different things, which is the entire class of defect
    this card series exists to close.
    """
    out: list[dict] = []
    for section in bundle.get("sections") or []:
        content = section.get("content") or ""
        for span in paragraph_spans(content):
            out.append({"section_id": section.get("section_id"),
                        "heading": section.get("heading") or "",
                        "content": content, **span})
    return out


def locate_paragraph(before: str, paragraphs: list[dict], bundle: dict) -> dict:
    """Where the paragraph the reviewer received sits in the version NOW.

    ``{"state", "reason", "paragraph"|None, "section_id"|None}``:

      located     exactly one section paragraph is that text
      ambiguous   several are. A guess is not a location (``resolve_anchor``'s
                  rule: an ambiguous match is never resolved by picking one).
      heading     the text is a section's HEADING, which is its own column and
                  carries no anchors — a different repair from "not found".
      not_found   no paragraph and no heading holds it.

    An EMPTY ``before`` matches every blank line in the document, so it is
    ``ambiguous`` rather than bound to whichever blank line sorts first.
    """
    if not before:
        return {"state": "ambiguous", "reason": "empty_paragraph_text",
                "paragraph": None, "section_id": None}
    matches = [p for p in paragraphs if p["text"] == before]
    if len(matches) == 1:
        return {"state": "located", "reason": "paragraph_exact",
                "paragraph": matches[0], "section_id": matches[0]["section_id"]}
    if len(matches) > 1:
        return {"state": "ambiguous",
                "reason": f"paragraph_ambiguous:{len(matches)}",
                "paragraph": None, "section_id": None}
    for section in bundle.get("sections") or []:
        if (section.get("heading") or "").strip() == before.strip():
            return {"state": "heading", "reason": "heading_not_anchorable",
                    "paragraph": None, "section_id": section.get("section_id")}
    return {"state": "not_found", "reason": "paragraph_not_found",
            "paragraph": None, "section_id": None}


def locate_span(anchor_text: str, bundle: dict) -> dict:
    """Where a piece of DELETED text still sits, when its paragraph does not.

    Only ever consulted after :func:`locate_paragraph` has failed, and a hit
    here is a CONFLICT and not a match: the paragraph changed under the
    reviewer while they were editing inside it. Exactly one occurrence across
    exactly one section, or nothing.
    """
    if not anchor_text:
        return {"state": "not_found", "section_id": None, "start": None,
                "end": None}
    hits: list[dict] = []
    for section in bundle.get("sections") or []:
        content = section.get("content") or ""
        count = content.count(anchor_text)
        if count > 1:
            return {"state": "ambiguous", "section_id": None, "start": None,
                    "end": None}
        if count == 1:
            index = content.find(anchor_text)
            hits.append({"section_id": section.get("section_id"),
                         "start": index, "end": index + len(anchor_text)})
    if len(hits) == 1:
        return {"state": "located", **hits[0]}
    if len(hits) > 1:
        return {"state": "ambiguous", "section_id": None, "start": None,
                "end": None}
    return {"state": "not_found", "section_id": None, "start": None, "end": None}


# ── what the ICDEV side says about a span ─────────────────────────────────────

def rail_spans(rail: dict) -> list[dict]:
    """Every rail item that HAS a verified position, as a span.

    Only a positioned item can contest a span: an item with no verified anchor
    has no span to contest WITH, and inventing one for it here would be the
    fabrication ``review_rail._position`` exists to refuse, committed one
    module downstream.
    """
    out: list[dict] = []
    for section in (rail.get("sections") or []):
        for item in section.get("positioned") or []:
            start = item.get("position")
            if not isinstance(start, int) or isinstance(start, bool):
                continue  # pragma: no cover - the rail's own invariant
            anchor = item.get("anchor_text") or ""
            out.append({"section_id": section.get("section_id"),
                        "start": start, "end": start + len(anchor),
                        "item": item})
    return out


def spans_overlap(a_start: int, a_end: int, b_start: int, b_end: int) -> bool:
    """Do two spans share a character, or does a zero-length one sit INSIDE?

    A pure insertion has a zero-length span, and half-open interval overlap is
    False for every zero-length interval — which would make an insertion point
    in the middle of a proposed replacement report as uncontested. Touching
    endpoints are NOT an overlap: an insertion exactly at a span's boundary
    belongs to neither side of it.
    """
    if a_start == a_end:
        return b_start < a_start < b_end
    if b_start == b_end:
        return a_start < b_start < a_end
    return a_start < b_end and b_start < a_end


def contesters(section_id: str, start: int, end: int,
               spans: list[dict]) -> list[dict]:
    """Every rail item whose verified span overlaps ``[start, end)``."""
    return [s for s in spans
            if s["section_id"] == section_id
            and spans_overlap(start, end, s["start"], s["end"])]


def item_summary(item: dict) -> dict:
    """One rail item as it is CARRIED onto a finding — both sides whole."""
    return {"kind": item.get("kind"), "id": item.get("id"),
            "status": item.get("status"), "author": item.get("author"),
            "anchor_text": item.get("anchor_text") or "",
            "suggested_content": item.get("suggested_content") or "",
            "body": item.get("body") or ""}


def expected_revision_groups(item: dict) -> list[dict] | None:
    """The revision groups dwr-word-01 WOULD have written for this rail item.

    THE EXPORTER EMITS A WORD-LEVEL DIFF, and this function exists because of
    it. A suggestion replacing ``FIPS 140-2`` with ``FIPS 140-3`` leaves here
    as a deletion of ``2`` and an insertion of ``3`` — not as the clause — so
    comparing a returned revision against the suggestion's OWN ``anchor_text``
    and ``suggested_content`` columns matches NOTHING, and every one of our own
    exported redlines comes back reported as a rival proposal against itself.
    Measured on the first live round trip of this card: three of three.

    So the expectation is re-derived through ``word_diff.diff_words``, the
    exporter's own function, IMPORTED. A second spelling of "what does a
    suggestion look like as revisions" is how the two halves of a round trip
    come to disagree about a document neither of them changed.

    ``None`` when the diff does not round-trip: ``docx_revisions`` DEFERS those
    (``diff_not_round_trip``) rather than rendering them, so there is nothing
    it could have emitted and nothing to expect back.
    """
    from tools.document_intelligence import word_diff

    position = item.get("position")
    if not isinstance(position, int) or isinstance(position, bool):
        return None
    diff = word_diff.diff_words(item.get("anchor_text") or "",
                                item.get("suggested_content") or "")
    if not diff.get("round_trip") or diff.get("spans") is None:
        return None

    groups: list[dict] = []
    offset = position
    current: dict | None = None
    for span in diff["spans"]:
        text = str(span.get("text") or "")
        if not text:
            continue
        if span.get("tag") == word_diff.EQUAL:
            if current is not None:
                current["end"] = offset
                groups.append(current)
                current = None
            offset += len(text)
            continue
        if current is None:
            current = {"start": offset, "before_text": "", "after_text": ""}
        if span.get("tag") == word_diff.DELETE:
            current["before_text"] += text
            offset += len(text)
        else:
            current["after_text"] += text
    if current is not None:
        current["end"] = offset
        groups.append(current)
    return groups


def _finding(verdict: str, reason: str, **fields) -> dict:
    return {"verdict": verdict, "reason": reason, **fields}


# ── reconciling one revision ──────────────────────────────────────────────────

def reconcile_group(group: dict, para: dict, bundle: dict,
                    paragraphs: list[dict], spans: list[dict]) -> dict:
    """The verdict for ONE revision group, and the whole decision in one place.

    The order is load-bearing. Location is asked FIRST, because a revision that
    cannot be placed cannot be contested and calling it clean would be the
    stronger claim of the two. Then the base: a span that located only because
    the deleted text survived a paragraph that did not is a conflict whatever
    the rail says. Only then the rail.
    """
    base = {
        "kind": "revision",
        "before_text": group.get("before_text") or "",
        "after_text": group.get("after_text") or "",
        "authors": list(group.get("authors") or []),
        "dates": list(group.get("dates") or []),
        "revision_kinds": list(group.get("kinds") or []),
        "paragraph_index": para.get("index"),
        "paragraph_before": para.get("before") or "",
        "section_id": None, "anchor_start": None, "anchor_end": None,
        "origin": None, "icdev_item_id": None, "contested_by": [],
    }

    located = locate_paragraph(para.get("before") or "", paragraphs, bundle)
    if located["state"] == "located":
        paragraph = located["paragraph"]
        start = paragraph["start"] + int(group["before_start"])
        end = paragraph["start"] + int(group["before_end"])
        base.update({"section_id": located["section_id"],
                     "anchor_start": start, "anchor_end": end})
        return _contest(base, start, end, spans, base_changed=False)

    if located["state"] == "heading":
        return _finding("unmatched", "heading_not_anchorable",
                        **{**base, "section_id": located["section_id"],
                           "detail": "a section heading is its own column and "
                                     "carries no anchors; edit the heading, "
                                     "not a span"})

    # The paragraph is gone. The span may not be — and if it is not, BOTH
    # sides changed it, which is a conflict and never a clean match.
    if not (group.get("before_text") or ""):
        return _finding("unmatched", "insertion_point_unlocatable",
                        **{**base,
                           "detail": "a pure insertion has no text to look "
                                     "for, and its paragraph no longer exists "
                                     "in this version"})
    hit = locate_span(group["before_text"], bundle)
    if hit["state"] == "located":
        base.update({"section_id": hit["section_id"],
                     "anchor_start": hit["start"], "anchor_end": hit["end"]})
        return _contest(base, hit["start"], hit["end"], spans,
                        base_changed=True)
    if hit["state"] == "ambiguous":
        return _finding("unmatched", "anchor_ambiguous", **base)
    return _finding("unmatched", _locate_failure(located), **base)


def _locate_failure(located: dict) -> str:
    """The CLOSED reason a location failed. ``locate_paragraph`` reports how
    many paragraphs matched (``paragraph_ambiguous:3``); a finding carries the
    reason from the closed vocabulary and the count belongs in the detail, so
    a consumer grouping by reason cannot end up with one bucket per count."""
    if located["state"] != "ambiguous":
        return "paragraph_not_found"
    if located["reason"] == "empty_paragraph_text":
        return "empty_paragraph_text"
    return "paragraph_ambiguous"


def is_own_proposal(item: dict, start: int, end: int, before_text: str,
                    after_text: str) -> bool:
    """Is this returned revision one dwr-word-01 wrote for ``item``?

    Exact on all four of span, deleted text and inserted text, against what
    :func:`expected_revision_groups` says the exporter emitted. Anything less
    is a DIFFERENT proposal over the same span, which is the conflict and not
    a near-match to be rounded off — a reviewer typing ``Annex D`` where ICDEV
    proposes ``Annex B`` must never be recorded as agreement.
    """
    for group in expected_revision_groups(item) or []:
        if (group["start"] == start and group["end"] == end
                and group["before_text"] == before_text
                and group["after_text"] == after_text):
            return True
    return False


def _contest(base: dict, start: int, end: int, spans: list[dict],
             *, base_changed: bool) -> dict:
    """Ask the rail about a LOCATED span, and name what it says.

    ``contested_by`` never carries the item the finding IS: an own proposal
    listed as its own contester reads as a document at war with itself.
    """
    overlapping = contesters(base["section_id"], start, end, spans)
    changes = [s for s in overlapping if s["item"].get("kind") == "change"]

    if base_changed:
        return _finding("conflicting", "base_paragraph_changed",
                        **{**base, "contested_by": _summaries(overlapping),
                           "detail": "the paragraph the reviewer edited no "
                                     "longer exists in this version, and their "
                                     "deleted text was found elsewhere; both "
                                     "sides moved"})

    own = [s for s in changes
           if s["item"].get("status") == "pending"
           and is_own_proposal(s["item"], start, end, base["before_text"],
                               base["after_text"])]
    if own:
        item = own[0]["item"]
        return _finding("matched", "own_proposal_unchanged",
                        **{**base, "origin": "own_proposal",
                           "icdev_item_id": item.get("id"),
                           "contested_by": _summaries(overlapping, exclude=item)})

    decided = [s for s in changes if s["item"].get("status") in DECIDED_STATUSES]
    if decided:
        item = decided[0]["item"]
        return _finding("conflicting", "decided_since_export",
                        **{**base, "icdev_item_id": item.get("id"),
                           "contested_by": _summaries(overlapping, exclude=item),
                           "detail": "somebody adjudicated this span while the "
                                     "reviewer was editing it"})
    competing = [s for s in changes if s["item"].get("status") == "pending"]
    if competing:
        item = competing[0]["item"]
        return _finding("conflicting", "competing_proposal",
                        **{**base, "icdev_item_id": item.get("id"),
                           "contested_by": _summaries(overlapping, exclude=item),
                           "detail": "a pending ICDEV redline proposes "
                                     "something different for this span"})
    return _finding("matched", "reviewer_edit",
                    **{**base, "origin": "reviewer_edit",
                       "contested_by": _summaries(overlapping)})


def _summaries(overlapping: list[dict], *, exclude: dict | None = None) -> list[dict]:
    excluded = exclude.get("id") if exclude else None
    return [item_summary(s["item"]) for s in overlapping
            if s["item"].get("id") != excluded]


# ── reconciling one comment ───────────────────────────────────────────────────

def thread_range(comment_id: str, by_id: dict, comments: dict) -> dict | None:
    """The highlighted range a comment is anchored by, or ``None``.

    A REPLY OFTEN HAS NO RANGE OF ITS OWN. dwr-word-01 highlights a thread ONCE
    and emits one ``w:commentReference`` per reply inside it, so a reader that
    only walked ranges saw the root and dropped every reply — measured on this
    card's own export. Word 16.0, saving the same document, writes a range per
    comment; both shapes are legal and both must read. So the range is the
    comment's own where it has one, and otherwise the nearest ancestor's in
    THIS paragraph. A cycle in the parentage (which no writer here produces and
    a hand-built file could) terminates rather than spinning.
    """
    seen: set[str] = set()
    current = comment_id
    while current is not None and current not in seen:
        seen.add(current)
        if current in by_id:
            return by_id[current]
        current = (comments.get(current) or {}).get("parent_id")
    return None


def reconcile_comment(rng: dict, para: dict, comment: dict, bundle: dict,
                      paragraphs: list[dict], spans: list[dict]) -> dict:
    """The verdict for ONE comment range.

    A comment is never ``conflicting``: it changes no text, so there is nothing
    for the ICDEV side to disagree with. Where its span IS contested that is
    reported as ``contested_by`` context — a reviewer asking about a proposed
    redline is the normal case and flagging it would bury the real findings.
    """
    body = str(comment.get("body") or "")
    base = {
        "kind": "comment",
        "comment_id": comment.get("comment_id"),
        "author": comment.get("author") or "",
        "date": comment.get("date") or "",
        "body": body,
        "is_reply": bool(comment.get("parent_id") or comment.get("parent_para_id")),
        "parent_comment_id": comment.get("parent_id"),
        "resolved": comment.get("done"),
        "paragraph_index": para.get("index"),
        "section_id": None, "anchor_start": None, "anchor_end": None,
        "anchor_text": "", "origin": None, "icdev_item_id": None,
        "contested_by": [],
    }
    if rng.get("spans_paragraphs") or rng.get("before_start") is None \
            or rng.get("before_end") is None:
        return _finding("unmatched", "comment_range_spans_paragraphs",
                        **{**base,
                           "detail": "the highlighted range opens or closes in "
                                     "another paragraph; a DIC comment anchors "
                                     "inside one section's content"})

    located = locate_paragraph(para.get("before") or "", paragraphs, bundle)
    if located["state"] != "located":
        reason = ("heading_not_anchorable" if located["state"] == "heading"
                  else _locate_failure(located))
        return _finding("unmatched", reason,
                        **{**base, "section_id": located.get("section_id")})

    paragraph = located["paragraph"]
    start = paragraph["start"] + int(rng["before_start"])
    end = paragraph["start"] + int(rng["before_end"])
    base.update({"section_id": located["section_id"], "anchor_start": start,
                 "anchor_end": end,
                 "anchor_text": (paragraph["content"] or "")[start:end]})

    overlapping = contesters(located["section_id"], start, end, spans)
    # A comment overlapping a comment is not a CONTEST — two people may ask
    # about one sentence. Only a CHANGE contests the span a comment is about,
    # by proposing to alter the very text the remark refers to.
    base["contested_by"] = [item_summary(s["item"]) for s in overlapping
                            if s["item"].get("kind") == "change"]

    own = _matching_annotation(overlapping, body, start, end)
    if own:
        return _finding("matched", "own_comment_unchanged",
                        **{**base, "origin": "own_comment",
                           "icdev_item_id": own})
    return _finding("matched", "reviewer_comment",
                    **{**base, "origin": "reviewer_comment"})


def _matching_annotation(overlapping: list[dict], body: str, start: int,
                         end: int) -> str | None:
    """The ``ann_id`` this Word comment was EXPORTED from, or ``None``.

    THREADS ARE SEARCHED WHOLE — root AND replies. dwr-word-01 writes a thread
    as one root ``w:comment`` plus one per reply, all over ONE range, so a
    reply that only ever compared against roots comes back as a brand-new
    reviewer remark carrying our own author's words. Measured on the first
    live round trip: the seeded reply ``Yes — see the ISSO memo.`` did exactly
    that.

    The body is compared because the SPAN alone cannot separate a reviewer's
    genuinely new remark about a sentence from our own comment come back — and
    that confusion, between a proposal and its answer, is what this card is
    about. dwr-word-01 renders a root's body as ``[category] body`` when a
    category is set and a reply's verbatim, so the comparison undoes exactly
    that and nothing more.
    """
    for span in overlapping:
        item = span["item"]
        if item.get("kind") != "comment":
            continue
        if item.get("position") != start:
            continue
        if start + len(item.get("anchor_text") or "") != end:
            continue
        root = str(item.get("body") or "")
        category = item.get("category")
        if body in (root, f"[{category}] {root}" if category else root):
            return item.get("id")
        for reply in item.get("replies") or []:
            if body == str(reply.get("comment") or ""):
                return reply.get("ann_id")
    return None


# ── absence ───────────────────────────────────────────────────────────────────

def absent_items(rail: dict, findings: list[dict]) -> list[dict]:
    """Every POSITIONED rail item with no counterpart in the upload.

    A labelled ABSENCE and never a decision — see the module docstring for the
    five causes one absence is consistent with, four of which are not
    decisions. Only positioned items are considered: an item the rail could not
    place has no span, so "did it come back" is not a question that can be
    asked of it, and counting it here would report every unanchored proposal on
    the board as missing from every upload.
    """
    # ACCOUNTED FOR, not merely MATCHED. An item named as the CONTESTER of a
    # revision was plainly seen in the upload — it is contested, not absent —
    # and listing it under both headings tells a reader the same proposal both
    # came back and did not. Measured on the first live round trip: `sug-eol`
    # was the contester of a `base_paragraph_changed` finding, whose reason
    # deliberately records no `icdev_item_id`, and so appeared in both.
    seen = {f.get("icdev_item_id") for f in findings if f.get("icdev_item_id")}
    seen |= {other.get("id") for f in findings
             for other in (f.get("contested_by") or [])}
    out: list[dict] = []
    for span in rail_spans(rail):
        item = span["item"]
        if item.get("id") in seen:
            continue
        out.append({**item_summary(item), "section_id": span["section_id"],
                    "anchor_start": span["start"], "anchor_end": span["end"],
                    "note": "absent from the upload. NOT an accept and NOT a "
                            "reject — one absence, five causes."})
    return out


# ── the report ────────────────────────────────────────────────────────────────

def _unmeasurable(version_id, doc_id, reason, parsed=None) -> dict:
    return {"version_id": version_id, "doc_id": doc_id, "state": "unmeasurable",
            "reason": reason, "revisions": [], "comments": [],
            "absent_from_upload": [], "parse": parsed,
            "counts": {k: None for k in
                       ("revisions", "comments", "matched", "conflicting",
                        "unmatched", "absent_from_upload")}}


def reconcile(path, version_id: str, *, tenant_id: str | None = None,
              parsed: dict | None = None) -> dict:
    """Reconcile an uploaded .docx against ONE version of the ICDEV change set.

    ``version_id`` is the caller's DECLARATION of which version this file is a
    review of, and it is deliberately not guessed: nothing in a .docx carries
    an ICDEV identifier, and recovering one by matching prose would attribute a
    reviewer's edits to whichever version happened to look closest. A wrong
    declaration is measurable rather than silent — its revisions land in
    ``unmatched`` with ``paragraph_not_found``, which is what a mismatched
    document looks like.

    Writes nothing.
    """
    from tools.document_intelligence.exporter import load_version
    from tools.document_intelligence.review_rail import build_rail

    parsed = parsed if parsed is not None else read_revisions(path)
    if parsed.get("state") != "parsed":
        return _unmeasurable(version_id, None,
                             f"docx_unreadable: {parsed.get('reason')}", parsed)
    try:
        bundle = load_version(version_id)
    except Exception as exc:  # noqa: BLE001
        logger.warning("review import: version %s unreadable: %s", version_id, exc)
        return _unmeasurable(version_id, None, f"version_unreadable: {exc}", parsed)
    if bundle is None:
        return _unmeasurable(version_id, None, "version_not_found", parsed)

    version = bundle.get("version") or {}
    doc_id = version.get("doc_id")
    rail = build_rail(doc_id or "", version_id=version_id,
                      tenant_id=tenant_id or version.get("tenant_id"))
    if rail.get("state") == "unmeasurable":
        # The contest half is unmeasurable, and `matched` is a claim ABOUT the
        # rail ("nothing contests this span"), so the whole report is.
        return _unmeasurable(version_id, doc_id,
                             f"rail_unmeasurable: {rail.get('reason')}", parsed)

    paragraphs = section_paragraphs(bundle)
    spans = rail_spans(rail)
    revisions: list[dict] = []
    comments: list[dict] = []

    for para in parsed.get("paragraphs") or []:
        mark = para.get("paragraph_mark")
        if mark:
            revisions.append(_finding(
                "unmatched", "paragraph_mark_revision",
                kind="paragraph_mark", before_text="", after_text="",
                authors=[mark.get("author") or ""], dates=[mark.get("date") or ""],
                revision_kinds=[mark.get("kind")],
                paragraph_index=para.get("index"),
                paragraph_before=para.get("before") or "",
                section_id=locate_paragraph(para.get("before") or "",
                                            paragraphs, bundle).get("section_id"),
                anchor_start=None, anchor_end=None, origin=None,
                icdev_item_id=None, contested_by=[],
                detail="a paragraph mark was inserted or deleted; ICDEV anchors "
                       "spans INSIDE a section's content and has no "
                       "representation for a paragraph split or merge"))
        for group in para.get("groups") or []:
            revisions.append(reconcile_group(group, para, bundle, paragraphs,
                                             spans))
        by_id = {r["comment_id"]: r for r in para.get("comment_ranges") or []}
        for comment_id in para.get("comment_ids") or []:
            comment = (parsed.get("comments") or {}).get(comment_id)
            if comment is None:
                comments.append(_finding(
                    "unmatched", "comment_body_absent",
                    kind="comment", comment_id=comment_id,
                    author="", date="", body="", is_reply=False,
                    parent_comment_id=None, resolved=None,
                    paragraph_index=para.get("index"), section_id=None,
                    anchor_start=None, anchor_end=None, anchor_text="",
                    origin=None, icdev_item_id=None, contested_by=[],
                    detail="the document references a comment whose body is "
                           "not in comments.xml"))
                continue
            rng = thread_range(comment_id, by_id, parsed.get("comments") or {})
            if rng is None:
                comments.append(_finding(
                    "unmatched", "comment_range_unlocatable",
                    kind="comment", comment_id=comment_id,
                    author=comment.get("author") or "",
                    date=comment.get("date") or "",
                    body=str(comment.get("body") or ""),
                    is_reply=bool(comment.get("parent_id")
                                  or comment.get("parent_para_id")),
                    parent_comment_id=comment.get("parent_id"),
                    resolved=comment.get("done"),
                    paragraph_index=para.get("index"), section_id=None,
                    anchor_start=None, anchor_end=None, anchor_text="",
                    origin=None, icdev_item_id=None, contested_by=[],
                    detail="the comment is referenced here and neither it nor "
                           "any thread ancestor highlights a range in this "
                           "paragraph"))
                continue
            comments.append(reconcile_comment(rng, para, comment, bundle,
                                              paragraphs, spans))

    findings = revisions + comments
    absent = absent_items(rail, findings)
    counts = {
        "revisions": len(revisions), "comments": len(comments),
        "absent_from_upload": len(absent),
        **{v: sum(1 for f in findings if f["verdict"] == v) for v in VERDICTS},
    }
    return {"version_id": version_id, "doc_id": doc_id,
            "state": "items" if findings else "empty", "reason": None,
            "revisions": revisions, "comments": comments,
            "absent_from_upload": absent, "parse": parsed, "counts": counts}


# ── CLI ───────────────────────────────────────────────────────────────────────

def _print_parse(parsed: dict) -> None:
    print(f"file:      {parsed['path']}")
    print(f"state:     {parsed['state']}")
    if parsed.get("reason"):
        print(f"reason:    {parsed['reason']}")
    counts = parsed["counts"]
    print(f"revisions: {counts['revisions']}   comments: {counts['comments']}"
          f"   paragraph marks: {counts['paragraph_marks']}")
    if parsed.get("truncated"):
        print(f"TRUNCATED at {parsed['limits']['max_revisions']} revisions "
              f"({MAX_REVISIONS_ENV}) — this list is short, and says so")
    for para in parsed.get("paragraphs") or []:
        for group in para["groups"]:
            print(f"  p{para['index']} [{'+'.join(group['kinds'])}] "
                  f"{group['before_text']!r} -> {group['after_text']!r} "
                  f"by {', '.join(group['authors']) or '(no author)'}")


def _print_report(report: dict) -> None:
    print(f"version:   {report['version_id']}  (doc {report['doc_id']})")
    print(f"state:     {report['state']}")
    if report.get("reason"):
        print(f"reason:    {report['reason']}")
    counts = report["counts"]
    print(f"matched {counts['matched']} · conflicting {counts['conflicting']} "
          f"· unmatched {counts['unmatched']}   "
          f"(revisions {counts['revisions']}, comments {counts['comments']}, "
          f"absent from upload {counts['absent_from_upload']})")
    if report["state"] == "unmeasurable":
        print("  NOT a clean round trip — nobody could measure it.")
        return
    for verdict in VERDICTS:
        rows = [f for f in report["revisions"] + report["comments"]
                if f["verdict"] == verdict]
        if not rows:
            continue
        print(f"\n{verdict.upper()} ({len(rows)})")
        for row in rows:
            where = (f"{row.get('section_id')}"
                     f"[{row.get('anchor_start')}:{row.get('anchor_end')}]"
                     if row.get("section_id") else "(unplaced)")
            print(f"  {row['kind']} {row['reason']} @ {where}")
            if row["kind"] == "comment":
                print(f"      {row.get('author')}: {row.get('body')!r}")
            else:
                print(f"      {row.get('before_text')!r} -> "
                      f"{row.get('after_text')!r} "
                      f"by {', '.join(row.get('authors') or []) or '(no author)'}")
            if row.get("icdev_item_id"):
                print(f"      icdev item: {row['icdev_item_id']}")
            for other in row.get("contested_by") or []:
                print(f"      contested by {other['kind']} {other['id']} "
                      f"({other['status']}): {other['anchor_text']!r} -> "
                      f"{other['suggested_content']!r}")
            if row.get("detail"):
                print(f"      {row['detail']}")
    if report["absent_from_upload"]:
        print(f"\nABSENT FROM THE UPLOAD ({len(report['absent_from_upload'])}) "
              "— NOT a decision")
        for row in report["absent_from_upload"]:
            print(f"  {row['kind']} {row['id']} ({row['status']}) "
                  f"@ {row['section_id']}[{row['anchor_start']}:"
                  f"{row['anchor_end']}]: {row['anchor_text']!r}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Reconcile a reviewer's Word revisions against a DIC version")
    parser.add_argument("--file", required=True, help="the reviewed .docx")
    parser.add_argument("--version", help="the dic_versions.version_id it reviews")
    parser.add_argument("--parse-only", action="store_true",
                        help="what is IN the file; touches no database")
    parser.add_argument("--tenant-id")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    parsed = read_revisions(args.file)
    if args.parse_only:
        if args.json:
            print(json.dumps(parsed, indent=2, default=str))
        else:
            _print_parse(parsed)
        return 0 if parsed["state"] == "parsed" else 2

    if not args.version:
        parser.error("--version is required unless --parse-only is given")
    report = reconcile(args.file, args.version, tenant_id=args.tenant_id,
                       parsed=parsed)
    if args.json:
        print(json.dumps(report, indent=2, default=str))
    else:
        _print_report(report)
    # 0 = a report was produced, whatever it says. 2 = it could not be, which
    # is never the same as a clean round trip.
    return 0 if report["state"] in ("items", "empty") else 2


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
