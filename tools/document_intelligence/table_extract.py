#!/usr/bin/env python3
# CUI // SP-CTI
"""Real table extraction from PDFs (oss-table-01).

DeepDoc's *goal*, not DeepDoc's stack. RAGFlow ships a deep-learning layout model
to recover tables; we get most of the value from pdfplumber's ruling-line and
whitespace detection, with no model weights, no GPU, and nothing to download at
runtime — which is the only version of this that survives an air-gap.

The gap it closes: `_extract_pdf` runs a four-pass chain that all ends in
`page.extract_text()`. A table comes out as whatever reading order the text
layer happened to have — cells run together, columns interleave, and a chunk
that says "AC-2  Implemented  2025-06-01" may just as easily read
"AC-2 AC-3 Implemented Planned 2025-06-01 2025-09-01". `extract_tables()` was
never called anywhere in the repo.

Tables are rendered as GitHub-flavoured markdown for two reasons: chunkers and
LLMs both already understand the pipe format, and `oss-chunk-01`'s `spreadsheet`
template can repeat a header row across chunk boundaries only if the header is
identifiable in the text.

**Optional dependency, probed not assumed.** `pdfplumber` is imported lazily and
:func:`table_support` reports what is actually available, following the
`layout_mode()` pattern in `extractors.py`. A prose claim in a docstring is not
a capability (oss-fix-03); a probe is.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

from tools.logging.icdev_logger import get_logger

logger = get_logger("icdev.dic.table_extract")

#: Cap on rendered cell text. A single runaway cell (a whole paragraph inside a
#: table) would otherwise dominate the chunk the table lands in.
MAX_CELL_CHARS = 200

#: Tables below this many rows/cols are almost always layout artefacts — a
#: two-cell "table" is usually a header box, not data.
MIN_ROWS = 2
MIN_COLS = 2


@dataclass
class ExtractedTable:
    """One table recovered from a page."""

    page: int
    index: int                       # nth table on that page, 0-based
    rows: List[List[str]] = field(default_factory=list)
    markdown: str = ""

    @property
    def shape(self) -> tuple:
        return (len(self.rows), max((len(r) for r in self.rows), default=0))

    def to_dict(self) -> Dict[str, Any]:
        return {
            "page": self.page,
            "index": self.index,
            "rows": self.rows,
            "markdown": self.markdown,
            "shape": list(self.shape),
        }


def table_support() -> Dict[str, Any]:
    """Report whether table extraction can actually run here.

    Returns ``{available, backend, reason}``. Callers branch on this instead of
    trusting a docstring — the whole point of oss-table-02 is that an optional
    dependency which is merely *mentioned* is not a capability.
    """
    try:
        import pdfplumber  # noqa: F401
    except ImportError as exc:
        return {
            "available": False,
            "backend": None,
            "reason": (
                f"pdfplumber not installed ({exc}). Tables will not be recovered; "
                "PDF text still extracts through the existing four-pass chain."
            ),
        }
    return {"available": True, "backend": "pdfplumber", "reason": ""}


def _clean(cell: Optional[str]) -> str:
    """Normalise one cell for markdown.

    Newlines inside a cell would break the row; pipes would break the column.
    Both are neutralised rather than dropped so the content survives.
    """
    if cell is None:
        return ""
    text = re.sub(r"\s+", " ", str(cell)).strip()
    text = text.replace("|", "\\|")
    if len(text) > MAX_CELL_CHARS:
        text = text[: MAX_CELL_CHARS - 1].rstrip() + "…"
    return text


def _is_meaningful(rows: List[List[str]]) -> bool:
    """Reject layout artefacts that pdfplumber reports as tables.

    Two filters, both earned from how ruling-line detection actually behaves:
    a single row or column is a header box or a sidebar, and a grid where every
    cell is empty is a page border.
    """
    if len(rows) < MIN_ROWS:
        return False
    if max((len(r) for r in rows), default=0) < MIN_COLS:
        return False
    return any(cell for row in rows for cell in row)


def to_markdown(rows: List[List[str]]) -> str:
    """Render cleaned rows as a GitHub-flavoured markdown table.

    The first row becomes the header. pdfplumber does not tell us whether a
    table has one, but treating row 0 as the header is right far more often than
    not, and a wrong guess still leaves every cell readable and correctly
    associated with its column — which is the property that matters for
    retrieval.
    """
    if not rows:
        return ""
    width = max(len(r) for r in rows)
    padded = [[_clean(c) for c in r] + [""] * (width - len(r)) for r in rows]

    header, body = padded[0], padded[1:]
    out = ["| " + " | ".join(header) + " |",
           "| " + " | ".join("---" for _ in header) + " |"]
    out.extend("| " + " | ".join(r) + " |" for r in body)
    return "\n".join(out)


def extract_tables(path: Path, max_pages: Optional[int] = None) -> List[ExtractedTable]:
    """Recover tables from a PDF.

    Degrades to ``[]`` — never raises — when pdfplumber is absent or a page is
    unparseable, because table recovery is an enhancement to text extraction and
    must not be able to fail the document.

    Args:
        path: PDF to read.
        max_pages: Stop after this many pages. Unbounded by default.

    Returns:
        Every table that passed :func:`_is_meaningful`, in page order.
    """
    support = table_support()
    if not support["available"]:
        logger.debug("table_extract: %s", support["reason"])
        return []

    import pdfplumber

    found: List[ExtractedTable] = []
    try:
        with pdfplumber.open(str(path)) as pdf:
            pages = pdf.pages[:max_pages] if max_pages else pdf.pages
            for page_no, page in enumerate(pages, start=1):
                try:
                    raw_tables = page.extract_tables() or []
                except Exception as exc:  # noqa: BLE001 - one bad page is not fatal
                    logger.debug("table_extract: page %d failed (%s)", page_no, exc)
                    continue
                for idx, raw in enumerate(raw_tables):
                    rows = [[_clean(c) for c in (row or [])] for row in (raw or [])]
                    if not _is_meaningful(rows):
                        continue
                    found.append(
                        ExtractedTable(
                            page=page_no,
                            index=idx,
                            rows=rows,
                            markdown=to_markdown(rows),
                        )
                    )
    except Exception as exc:  # noqa: BLE001
        logger.debug("table_extract: %s unreadable (%s)", path, exc)
        return []

    logger.info("table_extract: %d table(s) recovered from %s", len(found), path.name)
    return found


def tables_as_markdown(path: Path, max_pages: Optional[int] = None) -> str:
    """Every table in *path* as one markdown blob, page-labelled.

    Labelled because a table divorced from its page is hard to verify against
    the source — the same reason `oss-chunk-02` puts page/section on chunks.
    """
    blocks = []
    for t in extract_tables(path, max_pages=max_pages):
        blocks.append(f"\n--- Table (page {t.page}, #{t.index + 1}) ---\n{t.markdown}")
    return "\n".join(blocks)


def main() -> int:
    import argparse
    import json

    parser = argparse.ArgumentParser(description="PDF table extraction (oss-table-01)")
    parser.add_argument("pdf", nargs="?", help="PDF to read")
    parser.add_argument("--probe", action="store_true", help="Report backend availability")
    parser.add_argument("--max-pages", type=int)
    parser.add_argument("--json", action="store_true", dest="as_json")
    args = parser.parse_args()

    if args.probe or not args.pdf:
        print(json.dumps(table_support(), indent=2))
        return 0

    tables = extract_tables(Path(args.pdf), max_pages=args.max_pages)
    if args.as_json:
        print(json.dumps([t.to_dict() for t in tables], indent=2))
    else:
        for t in tables:
            print(f"\n--- page {t.page} table {t.index + 1} {t.shape} ---")
            print(t.markdown)
        print(f"\n{len(tables)} table(s)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
