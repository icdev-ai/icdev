# CUI // SP-CTI
"""The fine-tune extractor's ingestion-log row must actually LAND (mfx-ci-03).

``extract_document`` returns ``{"success": True, ...}`` after writing one
``rag_ingestion_log`` row, and that write is wrapped in
``except Exception: logger.warning(... non-blocking ...)``.  So the return value
says nothing at all about whether the row exists — and it did not.  Measured on
the live PostgreSQL board 2026-09-05: ``rag_ingestion_log`` holds 2,004 rows,
every one of them ``source_type='compliance_reference'`` from
``rag_compliance_corpus``, and ZERO from this writer.  Two independent reasons,
both of which the ``except`` swallowed and neither of which the caller could
see:

* the INSERT named ``chunk_count``; the column is ``chunks_created``;
* it did not name ``source_type`` at all, which is ``NOT NULL`` with no
  default, so the statement raises even once the first defect is fixed.

The second one is why every assertion below reads the ROW BACK.  A test that
asserts ``result["success"]`` passes against both defects, and an
INSERT/schema gate that checks only the columns a statement NAMES cannot see
the one it OMITS.  Those are different claims and only the row proves the
stronger one.
"""
from __future__ import annotations

import pathlib
import sqlite3
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.finetune import doc_extractor  # noqa: E402


#: The LIVE shape of ``rag_ingestion_log``, read from
#: ``information_schema.columns`` on the board this module writes to
#: (2026-09-05, 17 columns).  It is deliberately NOT the DDL in
#: ``init_icdev_db.py``: that one predates the migration adding
#: status/started_at/completed_at, and building the fixture from it would make
#: the test fail for a reason production does not have.  ``source_type`` keeps
#: its NOT NULL with no default, because that constraint IS the second defect.
_LIVE_SHAPE = """
CREATE TABLE rag_ingestion_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source_type TEXT NOT NULL,
    source_id TEXT NOT NULL DEFAULT '',
    source_table TEXT NOT NULL DEFAULT '',
    chunks_created INTEGER NOT NULL DEFAULT 0,
    chunks_skipped INTEGER NOT NULL DEFAULT 0,
    content_hash TEXT DEFAULT '',
    ingestion_mode TEXT DEFAULT 'batch',
    tenant_id TEXT DEFAULT '',
    project_id TEXT DEFAULT '',
    agent_id TEXT DEFAULT '',
    correlation_id TEXT DEFAULT '',
    classification TEXT NOT NULL DEFAULT 'CUI',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    status TEXT,
    started_at TEXT,
    completed_at TEXT
)
"""


@pytest.fixture
def board(tmp_path):
    """A throwaway SQLite database carrying the live ingestion-log shape."""
    db_path = tmp_path / "extractor.db"
    conn = sqlite3.connect(db_path)
    conn.execute(_LIVE_SHAPE)
    conn.commit()
    conn.close()
    return db_path


@pytest.fixture
def document(tmp_path):
    path = tmp_path / "runbook.txt"
    path.write_text(
        "Zero trust segmentation runbook. " * 200,
        encoding="utf-8",
    )
    return path


def _rows(db_path):
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        return [dict(r) for r in conn.execute("SELECT * FROM rag_ingestion_log")]
    finally:
        conn.close()


def test_extraction_writes_an_ingestion_log_row(board, document, monkeypatch):
    """The row EXISTS — not merely that the call reported success."""
    monkeypatch.setenv("ICDEV_STORAGE_BACKEND", "sqlite")

    result = doc_extractor.extract_document(
        str(document), dataset_id="ds-mfx-ci-03", db_path=board
    )
    assert result["success"] is True, result

    rows = _rows(board)
    assert len(rows) == 1, (
        "extract_document returned success and persisted nothing. That is the "
        "defect: the INSERT raises and the except-clause downgrades it to a "
        f"warning. rows={rows}"
    )


def test_the_row_records_the_chunk_count_it_produced(board, document, monkeypatch):
    """chunks_created is the live column; chunk_count has never existed here."""
    monkeypatch.setenv("ICDEV_STORAGE_BACKEND", "sqlite")

    result = doc_extractor.extract_document(
        str(document), dataset_id="ds-mfx-ci-03", db_path=board
    )
    row = _rows(board)[0]

    assert row["chunks_created"] == result["chunk_count"] == len(result["chunks"])
    assert row["source_id"] == result["document_id"]
    assert row["source_table"] == "ft_document:ds-mfx-ci-03"
    assert row["status"] == "completed"


def test_the_row_names_its_source_type(board, document, monkeypatch):
    """source_type is NOT NULL with no default — an unnamed one raises.

    Separate from the count assertion on purpose: this is the defect a
    column-existence gate structurally cannot see, because it is about a column
    the statement does NOT name.
    """
    monkeypatch.setenv("ICDEV_STORAGE_BACKEND", "sqlite")

    doc_extractor.extract_document(
        str(document), dataset_id="ds-mfx-ci-03", db_path=board
    )
    row = _rows(board)[0]

    assert row["source_type"], "source_type is NOT NULL; the INSERT must supply it"
    assert row["source_type"] == "document_extraction"


def test_get_db_passes_a_missing_path_through_as_none():
    """``str(db_path)`` on a None turns the default into the literal 'None'.

    Asserted structurally rather than by calling ``_get_db(None)``: on the
    SQLite backend that call resolves to the ambient database and would write
    into whatever checkout the suite runs in.
    """
    seen = {}

    def _fake_get_connection(db_path=None):
        seen["db_path"] = db_path
        raise RuntimeError("connection not needed")

    original = doc_extractor.get_connection
    doc_extractor.get_connection = _fake_get_connection
    try:
        with pytest.raises(RuntimeError):
            doc_extractor._get_db(None)
    finally:
        doc_extractor.get_connection = original

    assert seen["db_path"] is None, (
        "_get_db(None) asked for a database literally named 'None' — "
        f"got {seen['db_path']!r}"
    )
