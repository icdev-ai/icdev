# CUI // SP-CTI
"""Derive ``dic_sections`` from an ingested document's own structure (dwr-sect-01).

WHY THIS EXISTS. ``ingest_file`` writes ``dic_documents``, ``dic_versions`` and
``dic_chunk_links`` and has never written a section. Sections are only created
by ``doc_generator``, import-from-docgen and template instantiate — so an
*ingested* document renders with an empty Sections list and has no coordinate
space for an anchored change to land in. Measured on the live PG board
2026-09-07: **16 of 55 documents carry any ``dic_sections`` row at all.**

CHUNKS ARE NOT SECTIONS, and this module does not re-chunk. ``dic_chunk_links``
stays the RETRIEVAL unit — sized for embedding, overlapping, and deliberately
indifferent to where a sentence begins. A section is the EDITING and ANCHORING
unit: it follows the document's own headings, does not overlap, and covers the
text exactly once. Both point at the same version; neither is derived from the
other.

THE ONE INVARIANT
-----------------
::

    text[section.char_start:section.char_end] == section.content

Everything anchored rests on it. A section whose content is a stripped,
re-joined or otherwise tidied version of the source yields anchors that are off
by however much was tidied, and *nothing downstream can detect it* — the offsets
still look like integers and the prose still looks like prose. So the content is
a verbatim slice, always, and the tests assert the slice rather than the shape.

HEADINGS ARE NOT RE-PARSED HERE. ``tools/rag/breadcrumbs.parse_headings``
already finds ATX (``## Section``) and numbered clause (``3.4 Access Control``)
headings, already prefers a clause number's depth over its hash count for
exported compliance documents, and is already the parser the retrieval side
trusts for chunk position. A second heading parser in this package would be a
second opinion about what a heading is, and the two would drift.

THREE BASES, AND THE THIRD IS WHY THIS IS NOT A ONE-LINER
---------------------------------------------------------
``basis`` records HOW the outline was obtained, because a document with one
heading and a document with none both produce exactly one section:

``headings``        the document's own headings were used
``whole_document``  no heading was found; the whole text is one section
``empty``           there is no text. **No section is fabricated.**

Without ``basis`` those first two are indistinguishable on screen, and they are
different facts about the document leading to different fixes — one is a short
document, the other is an extraction that found no structure. Emitting a single
empty section for the third would make "nothing to render" look identical to "a
document with one short section", which is the same conflation one layer down.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional

from tools.rag.breadcrumbs import parse_headings

#: The outline came from the document's own headings.
BASIS_HEADINGS = "headings"
#: No heading was found; the whole document is one section.
BASIS_WHOLE_DOCUMENT = "whole_document"
#: There is no text at all. No section is emitted.
BASIS_EMPTY = "empty"

#: Every basis this module can return. The ``dic_versions.section_basis`` CHECK
#: is derived from this tuple by migration — never respelled in SQL.
SECTION_BASES = (BASIS_HEADINGS, BASIS_WHOLE_DOCUMENT, BASIS_EMPTY)

#: Label for content that precedes the first heading. ``dic_sections.heading`` is
#: NOT NULL, so a preamble needs *something* — and that something must not look
#: like text lifted out of the document. This sentinel is self-describing: a
#: reader can tell at a glance that the document did not supply a heading here,
#: which an invented "Introduction" would actively hide.
NO_HEADING_LABEL = "(no heading)"


@dataclass
class DerivedSection:
    """One section of a document, addressable by character offset.

    ``content`` is a VERBATIM slice of the source at ``[char_start:char_end]``.
    ``heading`` is stored separately and is NOT repeated at the head of
    ``content`` — ``dic_sections`` has both columns, and ``assemble_markdown``
    re-renders ``## heading`` from the heading column when it rebuilds the
    document.
    """

    heading: str
    content: str
    char_start: int
    char_end: int
    level: int = 1

    def to_dict(self) -> dict:
        return {
            "heading": self.heading,
            "content": self.content,
            "char_start": self.char_start,
            "char_end": self.char_end,
            "level": self.level,
        }


@dataclass
class DerivedOutline:
    """The sections of one document, and how they were arrived at."""

    sections: List[DerivedSection] = field(default_factory=list)
    basis: str = BASIS_EMPTY
    #: Headings actually found. ``0`` with a ``whole_document`` basis is the
    #: fallback; ``1`` with a ``headings`` basis is a one-section document.
    heading_count: int = 0

    def to_dict(self) -> dict:
        return {
            "basis": self.basis,
            "heading_count": self.heading_count,
            "section_count": len(self.sections),
            "sections": [s.to_dict() for s in self.sections],
        }


def _line_offsets(text: str) -> List[int]:
    """Character offset at which each line starts.

    ``splitlines()`` is not usable here: it drops the line terminators, so the
    offsets rebuilt from it would drift by one per line on ``\\r\\n`` input and
    every anchor in a Windows-authored document would land short.
    """
    offsets = [0]
    for i, ch in enumerate(text):
        if ch == "\n":
            offsets.append(i + 1)
    return offsets


def _body_start(text: str, heading_line_start: int) -> int:
    """Offset just past the heading's own line."""
    nl = text.find("\n", heading_line_start)
    return len(text) if nl == -1 else nl + 1


def derive_sections(text: str, *, title: str = "") -> DerivedOutline:
    """Split *text* into anchorable sections along its own headings.

    ``title`` labels a preamble or a whole-document fallback when the document
    supplies no heading of its own. It is the DOCUMENT's title, not an invented
    one; with no title available the preamble is labelled
    :data:`NO_HEADING_LABEL`.

    Returns a :class:`DerivedOutline`. Never raises on malformed input — the
    worst case is a single ``whole_document`` section, which is always a true
    statement about the text.
    """
    if not text or not text.strip():
        return DerivedOutline(sections=[], basis=BASIS_EMPTY, heading_count=0)

    headings = parse_headings(text)
    if not headings:
        return DerivedOutline(
            sections=[
                DerivedSection(
                    heading=title.strip() or NO_HEADING_LABEL,
                    content=text,
                    char_start=0,
                    char_end=len(text),
                    level=1,
                )
            ],
            basis=BASIS_WHOLE_DOCUMENT,
            heading_count=0,
        )

    line_starts = _line_offsets(text)

    def _start_of(line_no: int) -> int:
        # A heading line beyond the recorded offsets cannot happen for text we
        # just scanned, but clamping is cheaper than trusting two parsers to
        # agree about how many lines a string has.
        if line_no < 0:
            return 0
        if line_no >= len(line_starts):
            return len(text)
        return line_starts[line_no]

    sections: List[DerivedSection] = []

    # Content before the first heading is document text and must not be dropped.
    first_heading_start = _start_of(headings[0].line)
    preamble = text[0:first_heading_start]
    if preamble.strip():
        sections.append(
            DerivedSection(
                heading=title.strip() or NO_HEADING_LABEL,
                content=preamble,
                char_start=0,
                char_end=first_heading_start,
                level=1,
            )
        )

    for idx, h in enumerate(headings):
        heading_start = _start_of(h.line)
        body_start = _body_start(text, heading_start)
        if idx + 1 < len(headings):
            body_end = _start_of(headings[idx + 1].line)
        else:
            body_end = len(text)
        # A heading on the document's last line has no body. Clamp rather than
        # emit a negative span.
        if body_end < body_start:
            body_end = body_start
        sections.append(
            DerivedSection(
                heading=str(h).strip() or NO_HEADING_LABEL,
                content=text[body_start:body_end],
                char_start=body_start,
                char_end=body_end,
                level=h.level,
            )
        )

    return DerivedOutline(
        sections=sections, basis=BASIS_HEADINGS, heading_count=len(headings)
    )


def outline_for_document(
    text: str, *, title: str = "", max_sections: Optional[int] = None
) -> DerivedOutline:
    """:func:`derive_sections` with a declared upper bound on section count.

    A pathological document — a glossary where every line parses as a numbered
    clause — would otherwise write thousands of one-line ``dic_sections`` rows
    per ingest. When the bound is exceeded the outline degrades to a single
    ``whole_document`` section rather than silently truncating: a truncated
    outline drops document text, and dropping text to stay under a limit is the
    one failure this module must never have.
    """
    outline = derive_sections(text, title=title)
    if (
        max_sections is not None
        and max_sections > 0
        and len(outline.sections) > max_sections
    ):
        return DerivedOutline(
            sections=[
                DerivedSection(
                    heading=title.strip() or NO_HEADING_LABEL,
                    content=text,
                    char_start=0,
                    char_end=len(text),
                    level=1,
                )
            ],
            basis=BASIS_WHOLE_DOCUMENT,
            heading_count=outline.heading_count,
        )
    return outline
