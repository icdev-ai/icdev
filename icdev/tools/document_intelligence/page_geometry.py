# CUI // SP-CTI
"""Word geometry for a DIC document — the coordinate space a page view needs (dwr-fid-02).

THE DEFECT. ``extractors._extract_pdf_text`` ends every one of its four passes
in ``extract_text()``: a string, and a page COUNT. Nothing anywhere recorded
WHERE on the page a word sat, so the left pane of a review workspace could
render the document's PROSE and never the document. A positioned-text layer
needs a box per word, and no amount of re-reading the extracted string produces
one.

WHAT IS KEPT, AND THE TWO STORIES ARE NEVER MERGED
--------------------------------------------------
A PDF has pixel geometry; a DOCX does not have pages at all until something
renders it. Those are two different fidelity stories and they get two tables,
so a reader cannot mistake one for the other:

``dic_page_words``   PDF only. One row per word from pdfplumber's
                     ``extract_words()``: page, word_index, text, and the box
                     (``x0``/``x1``/``top``/``bottom``) in PDF points with
                     ``top`` measured from the page top — pdfplumber's own
                     convention, which is also CSS's, so a renderer positions
                     a word without converting anything.
``dic_doc_runs``     DOCX only. One row per RUN in document order: paragraph
                     index, run index, text, the paragraph's style name and the
                     run's bold/italic. Structure, order and styling — NOT
                     boxes, because python-docx cannot know them. There is no
                     ``page`` column here: pagination is the renderer's, and a
                     NULL page on a table called ``page_words`` would read as a
                     box we failed to measure rather than one that cannot exist.

``dic_document_geometry`` carries ONE row per document saying which story it
got and what that cost — see STATUS below.

pdfplumber, NEVER PyMuPDF. ``requirements.txt`` declares pdfplumber (:215) and
deliberately does NOT declare pymupdf/fitz (:218-220): it is dual-licensed
AGPL-3.0 / Artifex-commercial and ships as an optional accelerator only. A
fidelity feature that works only where an undeclared library happens to be
installed is not air-gap safe, and this host is exactly the trap — pymupdf IS
installed here, so a pymupdf implementation would have looked perfect locally
and produced nothing on a clean install. ``tests/document_intelligence/
test_page_geometry.py`` reads this module's AST and refuses an import of it.

CHAR OFFSETS MEAN OFFSETS INTO THE DOCUMENT'S OWN TEXT, OR THEY ARE NULL
------------------------------------------------------------------------
``char_start``/``char_end`` are only useful if they index text somebody has.
The trap is that the WORDS come from pdfplumber while the TEXT comes from
whichever of the four passes won — and MEASURED on the live board 2026-09-08,
that is never pdfplumber: of 13 PDFs, 10 were extracted by ``pymupdf`` and 3 by
``pypdf``. So the two sides always come from different libraries, and the
offsets pdfplumber would report about its own string are wrong about the
stored one.

:func:`align_char_offsets` therefore scans the STORED text: it locates each
page's segment by the ``--- Page N ---`` marker every text pass writes, then
walks that page's words in order, finding each one at or after the cursor
within :data:`ALIGN_LOOKAHEAD` characters. A word that cannot be placed gets
NULL — never 0, which would point every unplaceable word at the first character
of the document. The RATE is reported (``words_aligned`` / ``words_total``) and
is None — never 100.0 and never 0.0 — over an empty denominator
(args/perfect_score_gate.yaml) AND whenever alignment never ran at all, which
is a different thing from running and placing none: see
:data:`ALIGNMENT_ATTEMPTED_BASES`.

``char_basis`` says what the offsets index, and the values are not
interchangeable:

``document_text``   the text was PROVEN to be this document's — during ingest
                    it is the very string being stored; on a backfill the
                    re-extraction's sha256 equals the recorded
                    ``content_sha256``
``text_changed``    a backfill re-extracted and got DIFFERENT text (a different
                    pass won, or LLM OCR cleanup rewrote it). Offsets would
                    index a string nobody stored, so they are NULL. The BOXES
                    are still recorded and still correct — where a word sits on
                    the page does not depend on which library read it
``unaligned``       alignment ran against text and placed NOTHING
``not_attempted``   no text was supplied to align against

STATUS — AN EMPTY WORD LIST IS SEVEN DIFFERENT THINGS
------------------------------------------------------
``extracted``           the whole document is covered
``truncated``           a bound was hit. The rows present are real; the document
                        is PARTLY covered, and ``pages_extracted`` /
                        ``pages_total`` say by how much
``no_text_layer``       the file opened and yielded ZERO words. A scanned PDF.
                        A MEASURED zero — the only status here that is a
                        statement about the DOCUMENT
``unsupported_format``  not a PDF or a DOCX. Not a finding
``disabled_by_env``     switched off. Says nothing about the document
``library_unavailable`` pdfplumber / python-docx absent
``source_unreadable``   the file is gone — dwr-fid-01's finding, and why that
                        card had to land first: 9 of this board's 13 PDFs point
                        at temp files that no longer exist
``failed``              the library raised

Collapsing those into "no words" is how a document nobody could read becomes
indistinguishable from one with nothing on the page.

THE COST IS REAL, BOUNDED, AND THE BOUND IS REPORTED
-----------------------------------------------------
MEASURED on this host 2026-09-08, pdfplumber ``extract_words()`` over the live
corpus: constitution.pdf 19 pages / 9,178 words / 1.27s (483 words per page);
ArtOfWar.pdf 130 pages / 22,808 words / 6.24s (175 per page). So ~0.05s and
100-500 rows PER PAGE, and the 490 PDF pages on this board are ~100k rows.
Rows scale with PAGES, and a 2,000-page manual is one upload away.

``ICDEV_DIC_WORD_GEOMETRY=0``     off. Skippable, and REPORTED as
                                  ``disabled_by_env`` rather than silently
                                  reading like a document with no words
``ICDEV_DIC_GEOMETRY_MAX_PAGES``  default 50
``ICDEV_DIC_GEOMETRY_MAX_WORDS``  default 50,000
``ICDEV_DIC_GEOMETRY_MAX_RUNS``   default 20,000 (DOCX)

A bound that is hit is ``truncated`` with the numbers on the row, never a
quietly short list. Nothing here prunes; :func:`survey` reports the row counts
so the growth is measured.

Best-effort by construction: every entry point catches, and a geometry failure
can never fail an ingest. A document that had no geometry before still has none.

A library and a CLI::

    python -m tools.document_intelligence.page_geometry --survey [--json]
    python -m tools.document_intelligence.page_geometry --doc <doc_id> [--page 1]
    python -m tools.document_intelligence.page_geometry --backfill [--limit 5]
    python -m tools.document_intelligence.page_geometry --limits
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import pathlib
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from tools.logging.icdev_logger import get_logger

logger = get_logger(__name__)

WORDS_TABLE = "dic_page_words"
RUNS_TABLE = "dic_doc_runs"
GEOMETRY_TABLE = "dic_document_geometry"

#: Kill switch. Anything but "0"/"false"/"no"/"off" keeps geometry on.
ENABLED_ENV = "ICDEV_DIC_WORD_GEOMETRY"
MAX_PAGES_ENV = "ICDEV_DIC_GEOMETRY_MAX_PAGES"
MAX_WORDS_ENV = "ICDEV_DIC_GEOMETRY_MAX_WORDS"
MAX_RUNS_ENV = "ICDEV_DIC_GEOMETRY_MAX_RUNS"

DEFAULT_MAX_PAGES = 50
DEFAULT_MAX_WORDS = 50_000
DEFAULT_MAX_RUNS = 20_000

#: Ask pdfplumber for the CONTENT STREAM's order rather than its default visual
#: sort by (top, x0). This is the single decision that makes
#: ``char_start``/``char_end`` usable, and it was MEASURED, not reasoned.
#: Every readable PDF on the live board, 2026-09-08, same alignment code, words
#: placed in the document's own stored text:
#:
#:     constitution.pdf  19p   flow=False  1,334/9,178   14.5%
#:                             flow=True   9,178/9,178  100.0%
#:     SOP09-36.pdf      16p   flow=False  3,232/3,347   96.6%
#:                             flow=True   3,346/3,347   99.9%
#:     ArtOfWar.pdf      20p   flow=False  2,568/3,749   68.5%
#:                             flow=True   2,568/3,749   68.5%   (identical)
#:
#: Better on two, IDENTICAL on the third, worse on none. constitution.pdf is
#: two-column, so the default order walks each LINE across both columns while
#: every text extractor reads the stream column by column, and the two orders
#: share almost no runs. The word COUNT and every BOX are the same either way
#: (9,178 / 3,347 / 3,749 in both modes), so this costs nothing -- and it is
#: not a heuristic of ours: it is the order the PDF itself declares, which is
#: the order pymupdf and pypdf produce their text in.
#:
#: TWO CONSEQUENCES TO KNOW.
#: ``word_index`` is STREAM order, not visual reading order. A renderer does
#: not care (it positions by box), and a consumer that wants the page read
#: aloud should sort by (top, x0) itself rather than have this table guess at
#: columns.
#: And ArtOfWar.pdf's 68.5% is NOT this flag's doing and is not fixable here:
#: that PDF places characters with no space glyphs, so pdfplumber's word
#: segmentation glues whole lines into one "word" (mean word length 11.5
#: against 4.7 and 6.2 for the other two) in BOTH modes. Those runs do not
#: appear verbatim in the spaced text, so they cannot be placed. The BOXES stay
#: correct -- a line-wide box positions where the line is -- and the rate says
#: so. It is reported, not repaired, and never averaged away.
USE_TEXT_FLOW = True

#: How far past the cursor a word may be found in the stored text before the
#: match is judged a coincidence rather than the same word. Generous enough to
#: absorb whitespace, hyphenation and a dropped ligature; tight enough that a
#: common short word cannot bind to its next occurrence a paragraph later.
ALIGN_LOOKAHEAD = 200

#: The two fidelity stories. Never merged, never defaulted into each other.
KIND_PDF_WORD_BOX = "pdf_word_box"
KIND_DOCX_RUN = "docx_run"
GEOMETRY_KINDS: tuple[str, ...] = (KIND_PDF_WORD_BOX, KIND_DOCX_RUN)

STATUSES: tuple[str, ...] = (
    "extracted",
    "truncated",
    "no_text_layer",
    "unsupported_format",
    "disabled_by_env",
    "library_unavailable",
    "source_unreadable",
    "failed",
)

#: The statuses under which rows exist to render. Everything else renders
#: nothing, and each says a DIFFERENT thing about why.
RENDERABLE_STATUSES: frozenset[str] = frozenset({"extracted", "truncated"})

CHAR_BASES: tuple[str, ...] = (
    "document_text",
    "text_changed",
    "unaligned",
    "not_attempted",
)

#: The bases under which alignment actually RAN, and so under which a rate is a
#: measurement. Under the other two it was never attempted — no text was
#: supplied, or the text on offer was not this document's — and the rate is
#: None rather than 0.0.
#:
#: This is not a nicety. Caught on the live API 2026-09-08: a document whose
#: basis was ``text_changed`` reported ``align_rate_pct: 0.0`` beside 3,347
#: words, which reads as "every word was tried and none could be placed" — an
#: alarming claim about the extraction — when the truth was that alignment was
#: deliberately skipped and the boxes are perfect. Zero and "did not run" are
#: the two things this whole module exists to keep apart, and the first version
#: of the rate merged them.
ALIGNMENT_ATTEMPTED_BASES: frozenset[str] = frozenset({"document_text", "unaligned"})

#: The content types each story covers. A type in neither is
#: ``unsupported_format`` — a statement about the FORMAT, not the document.
PDF_TYPES: frozenset[str] = frozenset({"application/pdf"})
DOCX_TYPES: frozenset[str] = frozenset(
    {"application/vnd.openxmlformats-officedocument.wordprocessingml.document"}
)
PDF_SUFFIXES: frozenset[str] = frozenset({".pdf"})
DOCX_SUFFIXES: frozenset[str] = frozenset({".docx"})


# --------------------------------------------------------------------------- #
# Schema — the ONE copy. Migration 20260908091858 executes this same tuple.
# --------------------------------------------------------------------------- #
#: Plain TEXT/INTEGER/REAL so one string serves PostgreSQL and SQLite. Table
#: names are spelled LITERALLY (not through the constants above) so
#: tools/db/schema_ownership.py's CREATE TABLE scan can see them.
DDL: tuple[str, ...] = (
    """
CREATE TABLE IF NOT EXISTS dic_page_words (
    word_row_id     TEXT PRIMARY KEY,
    doc_id          TEXT NOT NULL,
    -- 1-based, the PDF's own page numbering.
    page            INTEGER NOT NULL,
    -- Reading order WITHIN the page, 0-based, as pdfplumber returned it.
    word_index      INTEGER NOT NULL,
    text            TEXT NOT NULL,
    -- The box, in PDF points. `top`/`bottom` are measured from the PAGE TOP
    -- (pdfplumber's convention, and CSS's), so a renderer positions a word
    -- without converting. Page DIMENSIONS are per page, not per document
    -- (measured: 612x792 and 595.3x841.9 in this corpus), and live on the
    -- document's geometry row under pages_json.
    x0              REAL NOT NULL,
    x1              REAL NOT NULL,
    top             REAL NOT NULL,
    bottom          REAL NOT NULL,
    -- Offsets into the DOCUMENT's stored text, or NULL. NULL is "this word
    -- could not be placed in that text" and is never 0 -- a 0 would point
    -- every unplaceable word at the first character of the document.
    char_start      INTEGER,
    char_end        INTEGER,
    created_at      TEXT NOT NULL,
    tenant_id       TEXT,
    classification  TEXT
)
""",
    "CREATE INDEX IF NOT EXISTS idx_dic_page_words_doc_page "
    "ON dic_page_words (doc_id, page, word_index)",
    """
CREATE TABLE IF NOT EXISTS dic_doc_runs (
    run_row_id      TEXT PRIMARY KEY,
    doc_id          TEXT NOT NULL,
    -- Document order. There is deliberately NO page column: python-docx knows
    -- paragraphs and runs, and pagination belongs to whatever renders the file.
    para_index      INTEGER NOT NULL,
    run_index       INTEGER NOT NULL,
    text            TEXT NOT NULL,
    -- The paragraph's style name ('Heading 1', 'Normal', ...) and the run's own
    -- emphasis. NULL bold/italic is python-docx's "inherit from the style",
    -- which is not the same answer as False.
    style           TEXT,
    bold            INTEGER,
    italic          INTEGER,
    char_start      INTEGER,
    char_end        INTEGER,
    created_at      TEXT NOT NULL,
    tenant_id       TEXT,
    classification  TEXT
)
""",
    "CREATE INDEX IF NOT EXISTS idx_dic_doc_runs_doc "
    "ON dic_doc_runs (doc_id, para_index, run_index)",
    """
CREATE TABLE IF NOT EXISTS dic_document_geometry (
    doc_id            TEXT PRIMARY KEY,
    -- WHICH fidelity story this document got: pdf_word_box | docx_run. NULL
    -- when none was attempted, and then `status` says why.
    kind              TEXT,
    -- page_geometry.STATUSES. An empty word list is seven different things and
    -- this column is which one.
    status            TEXT NOT NULL,
    -- Detail beside the status -- the missing library, the raised exception,
    -- the content type that is not covered.
    reason            TEXT,
    -- THE BOUND, REPORTED. pages_total is the file's page count as the library
    -- read it; pages_extracted is how many this run covered. Unequal means
    -- truncated, and the row says so rather than the list being quietly short.
    pages_total       INTEGER,
    pages_extracted   INTEGER,
    unit_count        INTEGER,
    truncated         INTEGER NOT NULL DEFAULT 0,
    truncated_reason  TEXT,
    max_pages         INTEGER,
    max_units         INTEGER,
    -- Per-PAGE dimensions: [{"page":1,"width":612.0,"height":792.0,"words":483}].
    -- JSON, read with json.loads in Python -- never queried with
    -- SQLite-dialect JSON SQL (CLAUDE.md: runtime SQL is authored for PostgreSQL).
    pages_json        TEXT,
    -- What char_start/char_end index: CHAR_BASES.
    char_basis        TEXT,
    words_aligned     INTEGER,
    words_total       INTEGER,
    -- The sha256 of the text the offsets were aligned against, so a later
    -- reader can prove it is still the document's text.
    text_sha256       TEXT,
    extracted_at      TEXT NOT NULL,
    duration_ms       INTEGER,
    tenant_id         TEXT,
    classification    TEXT
)
""",
)


# --------------------------------------------------------------------------- #
# Configuration
# --------------------------------------------------------------------------- #
def geometry_enabled() -> bool:
    raw = (os.environ.get(ENABLED_ENV) or "").strip().lower()
    return raw not in {"0", "false", "no", "off"}


def _int_env(name: str, default: int) -> int:
    raw = (os.environ.get(name) or "").strip()
    if not raw:
        return default
    try:
        value = int(raw)
    except ValueError:
        logger.warning(
            "dic.page_geometry: %s=%r is not an integer; using %d", name, raw, default
        )
        return default
    return value if value > 0 else default


def max_pages() -> int:
    return _int_env(MAX_PAGES_ENV, DEFAULT_MAX_PAGES)


def max_words() -> int:
    return _int_env(MAX_WORDS_ENV, DEFAULT_MAX_WORDS)


def max_runs() -> int:
    return _int_env(MAX_RUNS_ENV, DEFAULT_MAX_RUNS)


def limits() -> dict[str, Any]:
    """The bounds in force, as a caller would have to report them."""
    return {
        "enabled": geometry_enabled(),
        "max_pages": max_pages(),
        "max_words": max_words(),
        "max_runs": max_runs(),
        "align_lookahead": ALIGN_LOOKAHEAD,
    }


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _sha256_text(text: str) -> str:
    """Hash text EXACTLY the way ``ingest_orchestrator._sha256`` does.

    ``errors="replace"`` is not decoration: ``content_sha256`` is written with
    it, and a hash computed under a different rule would compare unequal for a
    document carrying one surrogate — reporting ``text_changed`` for text that
    had not changed. Two hashes are only comparable if one rule produced both.
    """
    return hashlib.sha256((text or "").encode("utf-8", errors="replace")).hexdigest()


def _align_rate(part: int | None, total: int | None, char_basis: str | None) -> float | None:
    """The alignment rate, or None when alignment never RAN.

    The ONE place this is decided, so the dataclass, the stored row and the API
    cannot disagree about whether a 0 means "tried and failed".
    """
    if char_basis not in ALIGNMENT_ATTEMPTED_BASES:
        return None
    return _rate(part, total)


def _rate(part: int | None, total: int | None) -> float | None:
    """The ONE place a percentage is computed here.

    None — never 0.0 and never 100.0 — over an empty denominator: a rate over
    nothing is the perfect score args/perfect_score_gate.yaml is ratcheted to
    zero over, and "nothing was aligned against" and "nothing aligned" justify
    opposite responses.

    And 100.0 is reserved for a rate that IS 100: 3,346 of 3,347 words rounds
    to 100.0 at one decimal place, and a display reading a perfect score for an
    imperfect one is the same defect one rounding away. It floors to 99.9.
    """
    if not total:
        return None
    part = part or 0
    if part >= total:
        return 100.0
    return min(round(part / total * 100, 1), 99.9)


# --------------------------------------------------------------------------- #
# Value objects
# --------------------------------------------------------------------------- #
@dataclass
class WordBox:
    """One word of a PDF page, with its box in PDF points."""

    page: int
    word_index: int
    text: str
    x0: float
    x1: float
    top: float
    bottom: float
    char_start: int | None = None
    char_end: int | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "page": self.page,
            "word_index": self.word_index,
            "text": self.text,
            "x0": self.x0,
            "x1": self.x1,
            "top": self.top,
            "bottom": self.bottom,
            "char_start": self.char_start,
            "char_end": self.char_end,
        }


@dataclass
class RunSpan:
    """One run of a DOCX paragraph — structure and styling, never a box."""

    para_index: int
    run_index: int
    text: str
    style: str | None = None
    bold: bool | None = None
    italic: bool | None = None
    char_start: int | None = None
    char_end: int | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "para_index": self.para_index,
            "run_index": self.run_index,
            "text": self.text,
            "style": self.style,
            "bold": self.bold,
            "italic": self.italic,
            "char_start": self.char_start,
            "char_end": self.char_end,
        }


@dataclass
class PageDim:
    """A page's own size. Per PAGE — pages within one PDF differ."""

    page: int
    width: float
    height: float
    words: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "page": self.page,
            "width": self.width,
            "height": self.height,
            "words": self.words,
        }


@dataclass
class GeometryResult:
    """What one capture produced — including every reason it produced nothing."""

    status: str
    kind: str | None = None
    reason: str = ""
    words: list[WordBox] = field(default_factory=list)
    runs: list[RunSpan] = field(default_factory=list)
    pages: list[PageDim] = field(default_factory=list)
    pages_total: int | None = None
    pages_extracted: int = 0
    truncated: bool = False
    truncated_reason: str = ""
    char_basis: str = "not_attempted"
    words_aligned: int = 0
    words_total: int = 0
    text_sha256: str | None = None
    duration_ms: int | None = None

    @property
    def unit_count(self) -> int:
        """Rows this result would write — words for a PDF, runs for a DOCX."""
        return len(self.words) if self.kind == KIND_PDF_WORD_BOX else len(self.runs)

    @property
    def align_rate(self) -> float | None:
        """Share of words placed in the document's own text, as a percentage."""
        return _align_rate(self.words_aligned, self.words_total, self.char_basis)

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "kind": self.kind,
            "reason": self.reason,
            "pages_total": self.pages_total,
            "pages_extracted": self.pages_extracted,
            "unit_count": self.unit_count,
            "truncated": self.truncated,
            "truncated_reason": self.truncated_reason,
            "char_basis": self.char_basis,
            "words_aligned": self.words_aligned,
            "words_total": self.words_total,
            "align_rate_pct": self.align_rate,
            "text_sha256": self.text_sha256,
            "duration_ms": self.duration_ms,
            "renderable": self.status in RENDERABLE_STATUSES,
            "pages": [p.to_dict() for p in self.pages],
        }


# --------------------------------------------------------------------------- #
# Format routing — which fidelity story does this file get?
# --------------------------------------------------------------------------- #
def geometry_kind_for(path: pathlib.Path, content_type: str | None = None) -> str | None:
    """``pdf_word_box`` / ``docx_run`` / None.

    The content type is authoritative when it is one we cover; otherwise the
    suffix decides, because an upload routed through a temp file arrives named
    ``tmpXXXX.pdf`` with whatever type the browser guessed.
    """
    ct = (content_type or "").split(";")[0].strip().lower()
    if ct in PDF_TYPES:
        return KIND_PDF_WORD_BOX
    if ct in DOCX_TYPES:
        return KIND_DOCX_RUN
    suffix = path.suffix.lower()
    if suffix in PDF_SUFFIXES:
        return KIND_PDF_WORD_BOX
    if suffix in DOCX_SUFFIXES:
        return KIND_DOCX_RUN
    return None


# --------------------------------------------------------------------------- #
# Extraction — pdfplumber for PDFs, python-docx for DOCX. Never pymupdf.
# --------------------------------------------------------------------------- #
def extract_pdf_words(
    path: pathlib.Path,
    *,
    page_budget: int | None = None,
    word_budget: int | None = None,
) -> GeometryResult:
    """Word boxes for a PDF, via pdfplumber's ``extract_words()``.

    Boxes are returned exactly as pdfplumber reports them — PDF points, ``top``
    from the page top — with no rounding or normalisation, because a renderer
    that scales by the page's own width/height needs the raw numbers and a
    consumer that wants points should not have to undo a transform.
    """
    page_budget = page_budget or max_pages()
    word_budget = word_budget or max_words()
    started = time.time()

    try:
        import pdfplumber  # noqa: PLC0415 - probed at call time, never at import
    except Exception as exc:  # noqa: BLE001
        return GeometryResult(
            status="library_unavailable",
            kind=KIND_PDF_WORD_BOX,
            reason=f"pdfplumber not importable: {exc}",
        )

    words: list[WordBox] = []
    pages: list[PageDim] = []
    pages_total: int | None = None
    truncated = False
    truncated_reason = ""

    try:
        with pdfplumber.open(str(path)) as pdf:
            pages_total = len(pdf.pages)
            for i, page in enumerate(pdf.pages):
                if i >= page_budget:
                    truncated = True
                    truncated_reason = (
                        f"page budget reached: {page_budget} of {pages_total} pages"
                    )
                    break
                if len(words) >= word_budget:
                    truncated = True
                    truncated_reason = (
                        f"word budget reached: {word_budget} words at page {i + 1} "
                        f"of {pages_total}"
                    )
                    break

                page_no = i + 1
                try:
                    raw = page.extract_words(use_text_flow=USE_TEXT_FLOW) or []
                except Exception as exc:  # noqa: BLE001 - one bad page is not the document
                    logger.warning(
                        "dic.page_geometry: extract_words failed on page %d of %s: %s",
                        page_no, path.name, exc,
                    )
                    raw = []

                on_page = 0
                for w in raw:
                    if len(words) >= word_budget:
                        truncated = True
                        truncated_reason = (
                            f"word budget reached: {word_budget} words on page "
                            f"{page_no} of {pages_total}"
                        )
                        break
                    text = (w.get("text") or "").strip()
                    if not text:
                        continue
                    try:
                        box = WordBox(
                            page=page_no,
                            word_index=on_page,
                            text=text,
                            x0=float(w["x0"]),
                            x1=float(w["x1"]),
                            top=float(w["top"]),
                            bottom=float(w["bottom"]),
                        )
                    except (KeyError, TypeError, ValueError):
                        # A word without a complete box is not a word we can
                        # position. Dropping it is honest; inventing a 0 box
                        # would put it in the page's top-left corner.
                        continue
                    words.append(box)
                    on_page += 1

                pages.append(
                    PageDim(
                        page=page_no,
                        width=float(page.width),
                        height=float(page.height),
                        words=on_page,
                    )
                )
                if truncated:
                    break
    except Exception as exc:  # noqa: BLE001
        return GeometryResult(
            status="failed",
            kind=KIND_PDF_WORD_BOX,
            reason=f"{type(exc).__name__}: {exc}",
            pages_total=pages_total,
            duration_ms=int((time.time() - started) * 1000),
        )

    duration_ms = int((time.time() - started) * 1000)

    if not words:
        # The file opened and yielded nothing. A MEASURED zero — a scanned page
        # image with no text layer — and the only status here that is a
        # statement about the document rather than about our tooling.
        return GeometryResult(
            status="no_text_layer",
            kind=KIND_PDF_WORD_BOX,
            reason="pdfplumber returned no words on any page read",
            pages=pages,
            pages_total=pages_total,
            pages_extracted=len(pages),
            truncated=truncated,
            truncated_reason=truncated_reason,
            duration_ms=duration_ms,
        )

    return GeometryResult(
        status="truncated" if truncated else "extracted",
        kind=KIND_PDF_WORD_BOX,
        words=words,
        pages=pages,
        pages_total=pages_total,
        pages_extracted=len(pages),
        truncated=truncated,
        truncated_reason=truncated_reason,
        duration_ms=duration_ms,
    )


def extract_docx_runs(
    path: pathlib.Path, *, run_budget: int | None = None
) -> GeometryResult:
    """Paragraph/run structure for a DOCX, via python-docx.

    Runs, styles and order — NOT boxes. python-docx reads the OOXML, and OOXML
    does not say where a word lands: pagination is the renderer's. Returning a
    box here would be a fabricated measurement, so the whole ``pdf_word_box``
    vocabulary is simply absent from this path.
    """
    run_budget = run_budget or max_runs()
    started = time.time()

    try:
        import docx  # noqa: PLC0415 - probed at call time
    except Exception as exc:  # noqa: BLE001
        return GeometryResult(
            status="library_unavailable",
            kind=KIND_DOCX_RUN,
            reason=f"python-docx not importable: {exc}",
        )

    runs: list[RunSpan] = []
    truncated = False
    truncated_reason = ""

    try:
        document = docx.Document(str(path))
        paragraphs = list(document.paragraphs)
        for pi, para in enumerate(paragraphs):
            if len(runs) >= run_budget:
                truncated = True
                truncated_reason = (
                    f"run budget reached: {run_budget} runs at paragraph {pi} "
                    f"of {len(paragraphs)}"
                )
                break
            try:
                style = para.style.name if para.style is not None else None
            except Exception:  # noqa: BLE001
                style = None
            for ri, run in enumerate(para.runs):
                if len(runs) >= run_budget:
                    truncated = True
                    truncated_reason = (
                        f"run budget reached: {run_budget} runs at paragraph {pi} "
                        f"of {len(paragraphs)}"
                    )
                    break
                text = run.text or ""
                if not text.strip():
                    continue
                runs.append(
                    RunSpan(
                        para_index=pi,
                        run_index=ri,
                        text=text,
                        style=style,
                        # None is python-docx's "inherit from the style", which
                        # is a different answer from False and is kept as one.
                        bold=run.bold,
                        italic=run.italic,
                    )
                )
    except Exception as exc:  # noqa: BLE001
        return GeometryResult(
            status="failed",
            kind=KIND_DOCX_RUN,
            reason=f"{type(exc).__name__}: {exc}",
            duration_ms=int((time.time() - started) * 1000),
        )

    duration_ms = int((time.time() - started) * 1000)

    if not runs:
        return GeometryResult(
            status="no_text_layer",
            kind=KIND_DOCX_RUN,
            reason="python-docx found no non-empty runs",
            duration_ms=duration_ms,
        )

    return GeometryResult(
        status="truncated" if truncated else "extracted",
        kind=KIND_DOCX_RUN,
        runs=runs,
        truncated=truncated,
        truncated_reason=truncated_reason,
        duration_ms=duration_ms,
    )


# --------------------------------------------------------------------------- #
# Char alignment — offsets into the DOCUMENT's text, or NULL
# --------------------------------------------------------------------------- #
#: Every text pass in ``extractors._extract_pdf_text`` writes this marker
#: before a page's text, and skips the marker entirely for a page whose text is
#: empty — so pages are located BY NUMBER from the marker, never by counting.
_PAGE_MARKER = "--- Page {n} ---"


def _page_segment(document_text: str, page: int) -> tuple[int, int] | None:
    """``(start, end)`` of one page's text inside the document's text."""
    marker = _PAGE_MARKER.format(n=page)
    at = document_text.find(marker)
    if at < 0:
        return None
    start = at + len(marker)
    nxt = document_text.find("\n--- Page ", start)
    return (start, nxt if nxt >= 0 else len(document_text))


def _align_run(units: list, text: str, start: int, end: int) -> int:
    """Place ``units`` in ``text[start:end]`` in order. Returns how many landed.

    A greedy forward scan: each unit is looked for at or after the cursor and
    no further than :data:`ALIGN_LOOKAHEAD` past it, so a short common word
    cannot bind to its next occurrence a paragraph later. A unit that is not
    found keeps NULL offsets and does NOT move the cursor — one library's
    dropped ligature must not desynchronise everything after it.
    """
    cursor = start
    landed = 0
    for unit in units:
        needle = unit.text
        if not needle:
            continue
        stop = min(end, cursor + ALIGN_LOOKAHEAD + len(needle))
        at = text.find(needle, cursor, stop)
        if at < 0:
            continue
        unit.char_start = at
        unit.char_end = at + len(needle)
        cursor = unit.char_end
        landed += 1
    return landed


def align_char_offsets(result: GeometryResult, document_text: str) -> int:
    """Set ``char_start``/``char_end`` on every unit that can be PLACED.

    Returns how many landed. Everything else keeps NULL, which is the honest
    answer and is never 0 — offsets of 0 would point every unplaceable word at
    the first character of the document.
    """
    if not document_text:
        return 0

    if result.kind == KIND_DOCX_RUN:
        # A DOCX has no page markers: python-docx's extractor joins paragraph
        # texts, so one segment and one cursor spans the whole document.
        return _align_run(result.runs, document_text, 0, len(document_text))

    by_page: dict[int, list[WordBox]] = {}
    for w in result.words:
        by_page.setdefault(w.page, []).append(w)

    # No markers at all (a provider that does not write them): fall back to one
    # segment with a cursor that carries across pages, since words are in
    # reading order document-wide.
    if "--- Page " not in document_text:
        ordered = [w for page in sorted(by_page) for w in by_page[page]]
        return _align_run(ordered, document_text, 0, len(document_text))

    landed = 0
    for page in sorted(by_page):
        seg = _page_segment(document_text, page)
        if seg is None:
            # The stored text has no segment for this page — the pass that won
            # skipped it as empty. Its words keep NULL rather than being
            # squeezed into a neighbouring page's characters.
            continue
        landed += _align_run(by_page[page], document_text, seg[0], seg[1])
    return landed


# --------------------------------------------------------------------------- #
# capture() — the one entry point an ingest calls
# --------------------------------------------------------------------------- #
def capture(
    path,
    *,
    content_type: str | None = None,
    document_text: str | None = None,
    expected_text_sha256: str | None = None,
) -> GeometryResult:
    """Extract geometry for one file and align it to the document's own text.

    ``document_text`` is the text the DOCUMENT is stored with. During an ingest
    that is the very string about to be written, so the offsets are the
    document's by construction. On a BACKFILL the caller re-extracts and passes
    ``expected_text_sha256`` (the recorded ``content_sha256``); when the two
    disagree the text is not the one the document was ingested from, so the
    offsets are withheld (``char_basis: text_changed``) and only the BOXES are
    kept — where a word sits on the page does not depend on which library read
    it.

    Never raises. Every failure is a status.
    """
    if not geometry_enabled():
        return GeometryResult(
            status="disabled_by_env",
            reason=f"{ENABLED_ENV} is off",
        )

    p = pathlib.Path(path)
    try:
        readable = p.is_file()
    except OSError as exc:
        return GeometryResult(status="source_unreadable", reason=str(exc))
    if not readable:
        return GeometryResult(
            status="source_unreadable",
            reason=f"no file at {p}",
        )

    kind = geometry_kind_for(p, content_type)
    if kind is None:
        return GeometryResult(
            status="unsupported_format",
            reason=(
                f"no geometry story for {content_type or p.suffix or 'unknown type'} — "
                "PDF gets word boxes, DOCX gets run structure"
            ),
        )

    try:
        if kind == KIND_PDF_WORD_BOX:
            result = extract_pdf_words(p)
        else:
            result = extract_docx_runs(p)
    except Exception as exc:  # noqa: BLE001 - a geometry failure never fails an ingest
        logger.warning("dic.page_geometry: capture failed for %s: %s", p, exc)
        return GeometryResult(status="failed", kind=kind, reason=f"{type(exc).__name__}: {exc}")

    units = result.words if kind == KIND_PDF_WORD_BOX else result.runs
    result.words_total = len(units)

    if document_text is None:
        result.char_basis = "not_attempted"
        return result

    result.text_sha256 = _sha256_text(document_text)
    if expected_text_sha256 and result.text_sha256 != expected_text_sha256:
        result.char_basis = "text_changed"
        return result

    if not units:
        result.char_basis = "not_attempted"
        return result

    result.words_aligned = align_char_offsets(result, document_text)
    result.char_basis = "document_text" if result.words_aligned else "unaligned"
    return result


# --------------------------------------------------------------------------- #
# Persistence
# --------------------------------------------------------------------------- #
def ensure_schema(conn) -> None:
    """Create the three tables if they are absent. Idempotent, best-effort."""
    try:
        for stmt in DDL:
            conn.execute(stmt)
    except Exception as exc:  # noqa: BLE001
        logger.warning("dic.page_geometry: schema ensure failed: %s", exc)


def persist(
    conn,
    doc_id: str,
    result: GeometryResult,
    *,
    tenant_id: str | None = None,
    classification: str | None = None,
) -> dict[str, Any]:
    """Write one document's geometry, replacing whatever it had.

    Re-ingesting a file must not leave the previous run's rows beside the new
    ones — a stale word box renders text that is no longer on the page, and a
    stale geometry row would report a bound nobody hit. Deleted and rewritten
    in the caller's transaction, the ``dic_sections`` precedent.

    The geometry ROW is written whatever the status, so a document with no
    words says WHY rather than being absent — which is indistinguishable from
    one nobody has looked at.
    """
    ensure_schema(conn)
    now = _now()
    cur = conn.cursor()

    cur.execute(f"DELETE FROM {WORDS_TABLE} WHERE doc_id = %s", (doc_id,))
    cur.execute(f"DELETE FROM {RUNS_TABLE} WHERE doc_id = %s", (doc_id,))
    cur.execute(f"DELETE FROM {GEOMETRY_TABLE} WHERE doc_id = %s", (doc_id,))

    if result.words:
        cur.executemany(
            f"INSERT INTO {WORDS_TABLE} "
            "(word_row_id, doc_id, page, word_index, text, x0, x1, top, bottom, "
            " char_start, char_end, created_at, tenant_id, classification) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
            [
                (
                    f"{doc_id}_p{w.page}_w{w.word_index}", doc_id, w.page, w.word_index,
                    w.text, w.x0, w.x1, w.top, w.bottom,
                    w.char_start, w.char_end, now, tenant_id, classification,
                )
                for w in result.words
            ],
        )

    if result.runs:
        cur.executemany(
            f"INSERT INTO {RUNS_TABLE} "
            "(run_row_id, doc_id, para_index, run_index, text, style, bold, italic, "
            " char_start, char_end, created_at, tenant_id, classification) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
            [
                (
                    f"{doc_id}_pa{r.para_index}_r{r.run_index}", doc_id,
                    r.para_index, r.run_index, r.text, r.style,
                    None if r.bold is None else int(r.bold),
                    None if r.italic is None else int(r.italic),
                    r.char_start, r.char_end, now, tenant_id, classification,
                )
                for r in result.runs
            ],
        )

    cur.execute(
        f"INSERT INTO {GEOMETRY_TABLE} "
        "(doc_id, kind, status, reason, pages_total, pages_extracted, unit_count, "
        " truncated, truncated_reason, max_pages, max_units, pages_json, char_basis, "
        " words_aligned, words_total, text_sha256, extracted_at, duration_ms, "
        " tenant_id, classification) "
        "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
        (
            doc_id, result.kind, result.status, result.reason or None,
            result.pages_total, result.pages_extracted, result.unit_count,
            1 if result.truncated else 0, result.truncated_reason or None,
            max_pages(),
            max_runs() if result.kind == KIND_DOCX_RUN else max_words(),
            json.dumps([p.to_dict() for p in result.pages]),
            result.char_basis, result.words_aligned, result.words_total,
            result.text_sha256, now, result.duration_ms, tenant_id, classification,
        ),
    )
    return result.to_dict()


def capture_and_persist(
    conn,
    doc_id: str,
    path,
    *,
    content_type: str | None = None,
    document_text: str | None = None,
    expected_text_sha256: str | None = None,
    tenant_id: str | None = None,
    classification: str | None = None,
) -> dict[str, Any]:
    """capture() then persist(), and NEVER raise — an ingest must not fail here.

    A geometry failure that took an ingest down would trade a feature nobody
    had yesterday for a document nobody has today.
    """
    try:
        result = capture(
            path,
            content_type=content_type,
            document_text=document_text,
            expected_text_sha256=expected_text_sha256,
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("dic.page_geometry: capture raised for %s: %s", doc_id, exc)
        return {"status": "failed", "reason": f"{type(exc).__name__}: {exc}", "persisted": False}

    try:
        out = persist(
            conn, doc_id, result, tenant_id=tenant_id, classification=classification
        )
        out["persisted"] = True
        return out
    except Exception as exc:  # noqa: BLE001
        logger.warning("dic.page_geometry: persist failed for %s: %s", doc_id, exc)
        out = result.to_dict()
        out["persisted"] = False
        out["persist_error"] = f"{type(exc).__name__}: {exc}"
        return out


# --------------------------------------------------------------------------- #
# Readers
# --------------------------------------------------------------------------- #
def _rows(conn, sql: str, params: tuple = ()) -> list[dict[str, Any]]:
    """Rows as dicts, or [] on a table this database has not got.

    A missing table is reported by the CALLER as ``unmeasured`` — never folded
    into an empty result, which reads as "we looked and there is nothing".
    """
    try:
        cur = conn.cursor()
        cur.execute(sql, params)
        return [dict(r) for r in (cur.fetchall() or [])]
    except Exception as exc:  # noqa: BLE001
        logger.debug("dic.page_geometry: query failed (%s): %s", sql.split()[0:4], exc)
        return []


def geometry_row(conn, doc_id: str) -> dict[str, Any] | None:
    """The document's geometry record, with ``pages`` parsed out of JSON."""
    rows = _rows(
        conn, f"SELECT * FROM {GEOMETRY_TABLE} WHERE doc_id = %s LIMIT 1", (doc_id,)
    )
    if not rows:
        return None
    row = rows[0]
    try:
        row["pages"] = json.loads(row.get("pages_json") or "[]")
    except (TypeError, ValueError):
        row["pages"] = []
    row.pop("pages_json", None)
    row["truncated"] = bool(row.get("truncated"))
    row["renderable"] = row.get("status") in RENDERABLE_STATUSES
    row["align_rate_pct"] = _align_rate(
        row.get("words_aligned"), row.get("words_total"), row.get("char_basis")
    )
    return row


def page_words(conn, doc_id: str, page: int) -> list[dict[str, Any]]:
    """One page's words, in reading order."""
    return _rows(
        conn,
        f"SELECT page, word_index, text, x0, x1, top, bottom, char_start, char_end "
        f"FROM {WORDS_TABLE} WHERE doc_id = %s AND page = %s ORDER BY word_index",
        (doc_id, page),
    )


def doc_runs(conn, doc_id: str, limit: int = 2000) -> list[dict[str, Any]]:
    """A DOCX's runs, in document order."""
    return _rows(
        conn,
        f"SELECT para_index, run_index, text, style, bold, italic, char_start, char_end "
        f"FROM {RUNS_TABLE} WHERE doc_id = %s ORDER BY para_index, run_index LIMIT {int(limit)}",
        (doc_id,),
    )


# --------------------------------------------------------------------------- #
# Survey
# --------------------------------------------------------------------------- #
def _table_present(conn, table: str) -> bool:
    try:
        conn.cursor().execute(f"SELECT 1 FROM {table} LIMIT 1")
        return True
    except Exception:  # noqa: BLE001
        return False


def survey(conn) -> dict[str, Any]:
    """Per-document geometry verdicts, counted, with what was MEASURED beside.

    A board whose tables do not exist reports ``unmeasured`` and says the
    migration has not run — never a wall of documents with "no geometry", which
    would read as a writer that failed rather than a schema that is absent.
    """
    present = _table_present(conn, GEOMETRY_TABLE)
    out: dict[str, Any] = {
        "state": "unmeasured",
        "tables_present": present,
        "limits": limits(),
        "documents": 0,
        "by_status": {},
        "by_kind": {},
        "rows": {"words": None, "runs": None},
        "documents_without_geometry": None,
    }
    if not present:
        out["reason"] = (
            f"{GEOMETRY_TABLE} is absent — run `python tools/db/migrate.py --up`"
        )
        return out

    docs = _rows(conn, "SELECT COUNT(*) AS n FROM dic_documents")
    total_docs = (docs[0].get("n") if docs else 0) or 0
    if not total_docs:
        out["reason"] = "no documents on this board"
        return out

    rows = _rows(
        conn,
        f"SELECT status, kind, COUNT(*) AS n FROM {GEOMETRY_TABLE} GROUP BY status, kind",
    )
    by_status: dict[str, int] = {}
    by_kind: dict[str, int] = {}
    recorded = 0
    for r in rows:
        n = int(r.get("n") or 0)
        recorded += n
        by_status[r.get("status") or "unknown"] = by_status.get(r.get("status") or "unknown", 0) + n
        key = r.get("kind") or "none"
        by_kind[key] = by_kind.get(key, 0) + n

    words = _rows(conn, f"SELECT COUNT(*) AS n FROM {WORDS_TABLE}")
    runs = _rows(conn, f"SELECT COUNT(*) AS n FROM {RUNS_TABLE}")

    out.update(
        {
            "state": "measured" if recorded else "no_geometry_recorded",
            "documents": total_docs,
            "documents_with_geometry": recorded,
            # NOT a finding on its own: a text/plain document has no geometry
            # story and never will. `unsupported_format` under by_status is the
            # measured version of that; this is simply what nothing has looked at.
            "documents_without_geometry": total_docs - recorded,
            "by_status": by_status,
            "by_kind": by_kind,
            "rows": {
                "words": (words[0].get("n") if words else 0) or 0,
                "runs": (runs[0].get("n") if runs else 0) or 0,
            },
        }
    )
    if not recorded:
        out["reason"] = "no document has been through geometry capture yet"
    return out


# --------------------------------------------------------------------------- #
# Backfill — the documents ingested before this landed
# --------------------------------------------------------------------------- #
def backfill(conn, *, limit: int = 5, doc_id: str | None = None) -> dict[str, Any]:
    """Capture geometry for already-ingested documents whose source is readable.

    The text is RE-EXTRACTED through the same chain and its sha256 checked
    against the document's recorded ``content_sha256``: equal proves it is the
    text the document was ingested from and the offsets are the document's;
    unequal is ``text_changed`` and the offsets are withheld while the boxes
    are kept.

    MEASURED 2026-09-08: 4 of this board's 13 PDFs still have a readable source
    (ArtOfWar 130p, constitution 19p x2, SOP09-36 16p); the other 9 point at
    deleted temp files. dwr-fid-01 retains the original from now on, so the
    reach of this grows — it does not fix the 9 that are already gone.
    """
    ensure_schema(conn)

    from tools.document_intelligence import originals as _originals

    # `filepath` plus the retention columns only when the LIVE table has them:
    # a SELECT naming a missing column is swallowed by _rows into an EMPTY
    # list, which would report a board with 55 documents as having none.
    extra, _columns_present = _originals.select_columns(conn)
    where = "WHERE doc_id = %s" if doc_id else ""
    params: tuple = (doc_id,) if doc_id else ()
    docs = _rows(
        conn,
        "SELECT doc_id, filename, content_type, content_sha256, tenant_id, classification"
        + extra
        + " FROM dic_documents " + where + " ORDER BY created_at DESC",
        params,
    )

    results: list[dict[str, Any]] = []
    done = 0
    for d in docs:
        if done >= limit:
            break
        # dwr-fid-01's ONE predicate for "is there a file to re-read", never a
        # second opinion: `retained` (the content-addressed original) and
        # `source_on_disk` (a CLI ingest whose own file persists) are readable;
        # `absent` / `no_source` / a missing or tampered store are not.
        verdict = _originals.original_verdict(d)
        source = (
            verdict.get("path")
            if verdict.get("status") in _originals.READABLE_VERDICTS
            else None
        )
        if source is None:
            results.append(
                {
                    "doc_id": d["doc_id"], "filename": d.get("filename"),
                    "status": "source_unreadable",
                    "reason": f"original_verdict: {verdict.get('status')}",
                }
            )
            continue

        text: str | None = None
        try:
            from tools.document_intelligence import extractors as _extractors

            text = (_extractors.extract_file(pathlib.Path(source)).text or "") or None
        except Exception as exc:  # noqa: BLE001
            logger.debug("dic.page_geometry: re-extract failed for %s: %s", d["doc_id"], exc)

        out = capture_and_persist(
            conn,
            d["doc_id"],
            source,
            content_type=d.get("content_type"),
            document_text=text,
            expected_text_sha256=d.get("content_sha256"),
            tenant_id=d.get("tenant_id"),
            classification=d.get("classification"),
        )
        out["doc_id"] = d["doc_id"]
        out["filename"] = d.get("filename")
        results.append(out)
        done += 1

    try:
        conn.commit()
    except Exception:  # noqa: BLE001
        pass
    return {"considered": len(docs), "captured": done, "limit": limit, "results": results}


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #
def _print_survey(report: dict[str, Any]) -> None:
    print(f"state: {report['state']}")
    if report.get("reason"):
        print(f"  reason: {report['reason']}")
    lim = report["limits"]
    print(
        f"  bounds: enabled={lim['enabled']} max_pages={lim['max_pages']} "
        f"max_words={lim['max_words']} max_runs={lim['max_runs']}"
    )
    print(f"  documents: {report['documents']}")
    if report.get("documents_with_geometry") is not None:
        print(f"  with geometry: {report['documents_with_geometry']}")
        print(f"  never captured: {report['documents_without_geometry']}")
    for status, n in sorted(report.get("by_status", {}).items(), key=lambda kv: -kv[1]):
        print(f"    {status:<20} {n}")
    for kind, n in sorted(report.get("by_kind", {}).items()):
        print(f"  kind {kind:<18} {n}")
    rows = report.get("rows") or {}
    print(f"  rows: words={rows.get('words')} runs={rows.get('runs')}")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description="DIC page geometry — word boxes (PDF) and run structure (DOCX)"
    )
    ap.add_argument("--survey", action="store_true", help="per-status counts across the board")
    ap.add_argument("--doc", help="one document's geometry record")
    ap.add_argument("--page", type=int, default=0, help="with --doc: show this page's words")
    ap.add_argument("--backfill", action="store_true", help="capture for existing documents")
    ap.add_argument("--limit", type=int, default=5, help="with --backfill: how many documents")
    ap.add_argument("--limits", action="store_true", help="print the bounds in force")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)

    if args.limits:
        print(json.dumps(limits(), indent=2))
        return 0

    from tools.db.storage import get_connection

    conn = get_connection()
    try:
        if args.backfill:
            report = backfill(conn, limit=args.limit, doc_id=args.doc)
            if args.json:
                print(json.dumps(report, indent=2, default=str))
            else:
                print(f"considered {report['considered']} document(s), captured {report['captured']}")
                for r in report["results"]:
                    print(
                        f"  {r.get('status'):<20} {r.get('unit_count', 0) or 0:>7} units  "
                        f"basis={r.get('char_basis', '-')}  {r.get('filename') or r.get('doc_id')}"
                    )
            return 0

        if args.doc:
            row = geometry_row(conn, args.doc)
            if row is None:
                print(f"no geometry recorded for {args.doc} — nothing has looked at it")
                return 1
            if args.page:
                words = page_words(conn, args.doc, args.page)
                payload = {"geometry": row, "page": args.page, "words": words}
            else:
                payload = {"geometry": row}
            print(json.dumps(payload, indent=2, default=str))
            return 0

        report = survey(conn)
        if args.json:
            print(json.dumps(report, indent=2, default=str))
        else:
            _print_survey(report)
        return 0
    finally:
        conn.close()


if __name__ == "__main__":  # pragma: no cover - CLI
    sys.exit(main())
