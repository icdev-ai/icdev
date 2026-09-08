# CUI // SP-CTI
"""Deriving dic_sections from an ingested document's own structure (dwr-sect-01).

Measured on the live PG board 2026-09-07: only 16 of 55 documents carry any
``dic_sections`` row, because ``ingest_file`` writes ``dic_documents``,
``dic_versions`` and ``dic_chunk_links`` and never a section. Sections are the
EDITING and ANCHORING coordinate space, so an ingested document had nowhere for
an anchored change to land.

The invariant every test here defends is the one anchoring rests on:

    text[section.char_start:section.char_end] == section.content

If that ever stops holding, every anchor derived downstream points at the wrong
prose, and it does so silently.
"""
from __future__ import annotations

import pytest

from tools.document_intelligence.section_deriver import (
    BASIS_EMPTY,
    BASIS_HEADINGS,
    BASIS_WHOLE_DOCUMENT,
    NO_HEADING_LABEL,
    derive_sections,
)
from tools.rag.breadcrumbs import parse_headings

ATX_DOC = """# Access Control Policy

Intro prose that sits before any section heading.

## 1 Purpose

This policy governs access.

## 2 Scope

It applies to every enclave.
"""

NUMBERED_DOC = """3 Access Control

Body of clause three.

3.4 Least Privilege

Body of clause three point four.
"""

NO_HEADING_DOC = (
    "Just a wall of prose with no headings at all.\n"
    "It runs to a second line and stops.\n"
)


# --------------------------------------------------------------- the invariant
@pytest.mark.parametrize(
    "text",
    [ATX_DOC, NUMBERED_DOC, NO_HEADING_DOC],
    ids=["atx", "numbered", "no-headings"],
)
def test_offsets_are_verbatim_slices_of_the_source(text):
    """content MUST equal the source slice at its own offsets.

    This is the whole reason the module exists. A section whose content is a
    cleaned-up, stripped or re-joined version of the source produces anchors
    that are off by however much was cleaned, and nothing downstream can tell.
    """
    outline = derive_sections(text)
    assert outline.sections, "expected at least one section"
    for s in outline.sections:
        assert text[s.char_start : s.char_end] == s.content, (
            f"section {s.heading!r} content is not the source slice at "
            f"[{s.char_start}:{s.char_end}]"
        )


def test_offsets_are_non_overlapping_and_ordered():
    outline = derive_sections(ATX_DOC)
    spans = [(s.char_start, s.char_end) for s in outline.sections]
    assert spans == sorted(spans), "sections must be in document order"
    for (_, prev_end), (next_start, _) in zip(spans, spans[1:]):
        assert next_start >= prev_end, "section spans must not overlap"


# ------------------------------------------------------- empty vs one section
def test_empty_text_derives_no_section_and_says_so():
    """An empty document gets NO fabricated section.

    Emitting a single empty section here would make "nothing to render" look
    identical to "a document with one short section".
    """
    outline = derive_sections("   \n\n  ")
    assert outline.basis == BASIS_EMPTY
    assert outline.sections == []
    assert outline.heading_count == 0


def test_no_headings_yields_one_whole_document_section():
    outline = derive_sections(NO_HEADING_DOC, title="Wall Of Prose")
    assert outline.basis == BASIS_WHOLE_DOCUMENT
    assert len(outline.sections) == 1
    assert outline.sections[0].content == NO_HEADING_DOC
    assert outline.sections[0].heading == "Wall Of Prose"


def test_one_section_from_a_heading_is_distinguishable_from_one_by_fallback():
    """The card's explicit requirement.

    Both documents below produce exactly ONE section. Without `basis`, a reader
    cannot tell "this document has one section" from "we could not find any
    headings and fell back to the whole document" — which are different facts
    about the document and lead to different fixes.
    """
    single_heading = derive_sections("## Only Section\n\nSome body prose.\n")
    fallback = derive_sections(NO_HEADING_DOC)

    assert len(single_heading.sections) == 1
    assert len(fallback.sections) == 1

    assert single_heading.basis == BASIS_HEADINGS
    assert fallback.basis == BASIS_WHOLE_DOCUMENT
    assert single_heading.heading_count == 1
    assert fallback.heading_count == 0


# ----------------------------------------------------------------- no data loss
@pytest.mark.parametrize(
    "text", [ATX_DOC, NUMBERED_DOC], ids=["atx", "numbered"]
)
def test_every_non_heading_character_lands_in_exactly_one_section(text):
    """The sections tile the document: no text dropped, none double-counted.

    Losing a paragraph to a splitting bug is silent — the document still
    renders, just shorter — so this asserts coverage over the character range
    rather than spot-checking that one known sentence survived.
    """
    outline = derive_sections(text)
    heading_lines = {h.line for h in parse_headings(text)}

    # Character offsets belonging to a heading line, which lives in the
    # `heading` column rather than in any section body.
    heading_chars: set[int] = set()
    offset = 0
    for line_no, line in enumerate(text.splitlines(keepends=True)):
        if line_no in heading_lines:
            heading_chars.update(range(offset, offset + len(line)))
        offset += len(line)

    covered: dict[int, int] = {}
    for s in outline.sections:
        for i in range(s.char_start, s.char_end):
            covered[i] = covered.get(i, 0) + 1

    for i in range(len(text)):
        if i in heading_chars:
            continue
        assert covered.get(i, 0) == 1, (
            f"character {i} ({text[i]!r}) is covered {covered.get(i, 0)} times; "
            "expected exactly once"
        )


def test_preamble_before_the_first_heading_is_not_dropped():
    """Text before the first heading is document content and must survive."""
    outline = derive_sections("Loose opening prose.\n\n## Real Heading\n\nBody.\n")
    joined = "".join(s.content for s in outline.sections)
    assert "Loose opening prose." in joined
    assert "Body." in joined


def test_an_unheaded_preamble_is_labelled_not_invented():
    """A preamble has no heading in the source, so we must not invent one.

    With no document title to fall back on, the label is a self-describing
    sentinel that cannot be mistaken for text lifted out of the document.
    """
    outline = derive_sections("Loose opening prose.\n\n## Real Heading\n\nBody.\n")
    assert outline.sections[0].heading == NO_HEADING_LABEL


def test_headings_are_captured_in_document_order():
    outline = derive_sections(ATX_DOC)
    assert [s.heading for s in outline.sections] == [
        "Access Control Policy",
        "1 Purpose",
        "2 Scope",
    ]


def test_numbered_clause_headings_are_recognised():
    """Reuses tools/rag/breadcrumbs.parse_headings, which already handles these."""
    outline = derive_sections(NUMBERED_DOC)
    assert outline.basis == BASIS_HEADINGS
    assert [s.heading for s in outline.sections] == [
        "3 Access Control",
        "3.4 Least Privilege",
    ]


def test_heading_text_is_not_repeated_inside_the_body():
    """heading and content are separate columns; the body must not re-state it."""
    outline = derive_sections("## Purpose\n\nThis policy governs access.\n")
    body = outline.sections[0].content
    assert "This policy governs access." in body
    assert not body.lstrip().startswith("## Purpose")
