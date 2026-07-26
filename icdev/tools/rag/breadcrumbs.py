#!/usr/bin/env python3
# CUI // SP-CTI
"""Deterministic document-position breadcrumbs for chunks (oss-chunk-02).

A chunk that reads *"shall be documented in the System Security Plan"* is
positionally orphaned: nothing in ``rag_chunks`` says which document, which
section, or which page it came from. Retrieval cannot expand a hit to its
enclosing section, and a citation cannot say "p. 47, §3.4".

This module derives that position from the document's own heading structure and
renders it as a breadcrumb prepended to the **embedded** text.

Relationship to ``contextual_retrieval`` — both, not either
-----------------------------------------------------------
``tools/rag/contextual_retrieval.py`` already prepends an LLM-generated prefix
and ships ON (measured +0.0151 recall@5 / +0.0202 MRR). This does not duplicate
or replace it:

* the LLM prefix is *prose*; it can only help ranking
* a breadcrumb is *derived from structure*, so it is *deterministic*, free, and
  reproducible — and the same values land in real columns, which makes them
  **filterable** ("every chunk in section 3.4") and **citable** ("p. 47, §3.4")

Neither of those is possible with a prose prefix, which is the whole argument
for paying for both.

The breadcrumb is prepended to embedded text only. The stored/cited ``content``
is untouched — same discipline as ``contextual_retrieval``, and for the same
reason: a citation must quote what the document said, not what we added.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

#: Markdown ATX headings (`## Section`) and numbered clause headings
#: (`3.4 Access Control`) — the two shapes that actually appear in the corpora
#: this serves (OSCAL/STIG exports, SOPs, RFP sections, contracts).
_ATX_RE = re.compile(r"^(#{1,6})\s+(.+?)\s*#*$")
_NUMBERED_RE = re.compile(r"^(\d+(?:\.\d+){0,5})\.?\s+(\S.{0,120})$")

#: A heading longer than this is a paragraph that happens to start with a number.
MAX_HEADING_CHARS = 120

#: Breadcrumb depth. Deeper than this and the prefix costs more tokens than the
#: position is worth.
MAX_DEPTH = 4

SEPARATOR = " > "


@dataclass
class Heading:
    """One heading found in a document."""

    level: int
    text: str
    line: int
    number: str = ""

    def __str__(self) -> str:
        return f"{self.number} {self.text}".strip()


@dataclass
class ChunkPosition:
    """Where a chunk sits inside its document."""

    doc_id: str = ""
    section: str = ""
    page: Optional[int] = None
    trail: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "doc_id": self.doc_id,
            "section": self.section,
            "page": self.page,
            "trail": list(self.trail),
        }


def _number_depth(number: str) -> int:
    """Nesting depth of a clause number: ``3`` -> 1, ``3.4`` -> 2, ``3.4.1`` -> 3."""
    return number.count(".") + 1


def parse_headings(text: str) -> List[Heading]:
    """Find headings and their nesting level.

    Numbered headings carry their own depth (``3.4.1`` is level 3), which is more
    reliable than ATX depth in exported compliance documents where every heading
    is often ``##``.
    """
    found: List[Heading] = []
    for i, raw in enumerate(text.splitlines()):
        line = raw.strip()
        if not line or len(line) > MAX_HEADING_CHARS * 2:
            continue

        m = _ATX_RE.match(line)
        if m:
            title = m.group(2).strip()
            num = ""
            level = len(m.group(1))
            n = _NUMBERED_RE.match(title)
            if n:
                num, title = n.group(1), n.group(2).strip()
                # The number wins over the hash count. Exported compliance
                # documents routinely make EVERY heading `##`, and taking ATX
                # depth there collapses `3` / `3.4` / `3.4.1` to one level, so
                # each heading pops the previous and the trail degrades to a
                # single leaf — losing exactly the hierarchy this module exists
                # to recover.
                #
                # +1 leaves level 1 free for an un-numbered document title, so
                # `# SSP` then `## 3 Access Control` still nests instead of the
                # title being popped by a top-level numbered section.
                level = _number_depth(num) + 1
            found.append(Heading(level=level, text=title, line=i, number=num))
            continue

        n = _NUMBERED_RE.match(line)
        if n and len(n.group(2)) <= MAX_HEADING_CHARS:
            number = n.group(1)
            found.append(
                Heading(
                    level=_number_depth(number) + 1,
                    text=n.group(2).strip(),
                    line=i,
                    number=number,
                )
            )
    return found


def trail_at(headings: List[Heading], line: int) -> List[Heading]:
    """The heading trail in force at *line*.

    Walks headings that appear at or before the line, keeping the most recent at
    each level and dropping deeper ones when a shallower heading opens — which is
    what "the section this text is in" means.
    """
    stack: List[Heading] = []
    for h in headings:
        if h.line > line:
            break
        while stack and stack[-1].level >= h.level:
            stack.pop()
        stack.append(h)
    return stack


def render(trail: List[Heading], doc_title: str = "", max_depth: int = MAX_DEPTH) -> str:
    """Render a heading trail as ``doc > section > subsection``."""
    parts = [doc_title.strip()] if doc_title.strip() else []
    parts.extend(str(h) for h in trail[-max_depth:] if str(h))
    return SEPARATOR.join(p for p in parts if p)


def position_for(
    text: str,
    offset: int,
    doc_title: str = "",
    doc_id: str = "",
    page: Optional[int] = None,
) -> ChunkPosition:
    """Where the text at character *offset* sits in the document."""
    line = text.count("\n", 0, max(0, offset))
    trail = trail_at(parse_headings(text), line)
    return ChunkPosition(
        doc_id=doc_id,
        section=render(trail, max_depth=MAX_DEPTH),
        page=page,
        trail=[str(h) for h in trail],
    )


def breadcrumb_prefix(position: ChunkPosition, doc_title: str = "") -> str:
    """The line prepended to embedded text. Empty when position is unknown.

    Empty rather than a placeholder: "unknown > unknown" would be noise in every
    embedding and would make an un-positioned chunk look positioned.
    """
    parts = [doc_title.strip()] if doc_title.strip() else []
    if position.section:
        parts.append(position.section)
    if position.page is not None:
        parts.append(f"p. {position.page}")
    return f"[{SEPARATOR.join(p for p in parts if p)}]" if parts else ""


def apply_breadcrumb(content: str, position: ChunkPosition, doc_title: str = "") -> str:
    """Prepend the breadcrumb to text destined for the embedder.

    Never mutates stored/cited content — callers pass a copy for embedding, the
    same split ``contextual_retrieval`` maintains.
    """
    prefix = breadcrumb_prefix(position, doc_title=doc_title)
    return f"{prefix}\n{content}" if prefix else content


def page_breaks(text: str) -> List[Tuple[int, int]]:
    """``(page_number, character_offset)`` for each page marker.

    Recognises the ``--- Page N ---`` markers the PDF extractors emit, so page
    numbers survive from extraction into chunking rather than being re-derived.
    """
    out: List[Tuple[int, int]] = []
    for m in re.finditer(r"^-{2,}\s*Page\s+(\d+)\s*-{2,}$", text, re.MULTILINE):
        out.append((int(m.group(1)), m.start()))
    return out


def page_at(breaks: List[Tuple[int, int]], offset: int) -> Optional[int]:
    """The page in force at *offset*, or None when the document has no markers."""
    current = None
    for page, start in breaks:
        if start <= offset:
            current = page
        else:
            break
    return current
