# CUI // SP-CTI
"""Migration 20260908091858 — word geometry for a DIC document (dwr-fid-02).

``extractors._extract_pdf_text`` ends every one of its four passes in
``extract_text()`` — a string and a page COUNT — so nothing recorded WHERE on
the page a word sat and a positioned-text layer had no coordinate space to
render into.

THREE tables, and the split is the design rather than normalisation:

``dic_page_words``        PDF only. One row per word, with its box in PDF
                          points (``top`` from the page top, pdfplumber's
                          convention and CSS's).
``dic_doc_runs``          DOCX only. Paragraph/run structure, styles and order
                          from python-docx — NOT boxes, which OOXML does not
                          have. No ``page`` column: pagination belongs to
                          whatever renders the file, and a NULL page on a table
                          called ``page_words`` would read as a box we failed
                          to measure rather than one that cannot exist.
``dic_document_geometry`` ONE row per document: which story it got, the STATUS
                          (an empty word list is seven different things), the
                          bound that was hit, and what ``char_start`` /
                          ``char_end`` index.

The DDL is IMPORTED from ``tools.document_intelligence.page_geometry.DDL``
rather than respelled here — the same call ``dic_author_assertions``
(20260908003920) made. ``ingest_orchestrator._ensure_schema`` executes the same
tuple for a database this migration has not reached, and two copies of a table
shape drift the first time a column is added.

Plain ``CREATE TABLE IF NOT EXISTS`` in TEXT/INTEGER/REAL, so one string serves
PostgreSQL and SQLite and re-running is a no-op. Nothing here alters an
existing table: all three tables are new in this card.

NOT append-only. These rows are a re-derivable projection of a file — a
re-ingest DELETEs and rewrites them, the ``dic_sections`` precedent — so they
are deliberately absent from ``APPEND_ONLY_TABLES``. The immutable record of a
document is its ``dic_versions`` row and its audit trail, not its geometry.
"""
from __future__ import annotations

import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[4]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from tools.db.storage import get_connection  # noqa: E402
from tools.document_intelligence.page_geometry import (  # noqa: E402
    DDL,
    GEOMETRY_TABLE,
    RUNS_TABLE,
    WORDS_TABLE,
)

TABLES = (WORDS_TABLE, RUNS_TABLE, GEOMETRY_TABLE)


def up(conn=None) -> None:
    owned = conn is None
    conn = conn or get_connection()
    try:
        for stmt in DDL:
            conn.execute(stmt)
        conn.commit()
        print(
            "[20260908091858_dic_page_geometry] up: "
            + ", ".join(TABLES)
            + " created (or already exist)"
        )
    finally:
        if owned:
            conn.close()


def down(conn=None) -> None:
    """Drop exactly the three tables this migration creates.

    Safe to invert, unlike the column-adding migrations beside it: every row in
    these tables is re-derivable from the document's file by re-running
    ``page_geometry.capture``, and no other table references them.
    """
    owned = conn is None
    conn = conn or get_connection()
    try:
        for table in reversed(TABLES):
            conn.execute(f"DROP TABLE IF EXISTS {table}")
        conn.commit()
    finally:
        if owned:
            conn.close()


if __name__ == "__main__":  # pragma: no cover - CLI
    up()
