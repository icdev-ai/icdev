# CUI // SP-CTI
"""Structural table recovery from PDFs (oss-table-01/02).

The defect: `_extract_pdf`'s four-pass chain all ends in `extract_text()`, so a
table arrives as whatever reading order the text layer happened to have. Cells
run together and columns interleave, which means a chunk can read
"AC-2 AC-3 Implemented Planned" with no way to tell which status belongs to
which control. `extract_tables()` was never called anywhere in the repo.

These tests pin the two properties that make the fix safe to ship: the
rendering is correct, and the enhancement can never cost us text we already had.
"""
from __future__ import annotations

import pytest

from tools.document_intelligence import table_extract as te


# ── Capability probe (oss-table-02) ──────────────────────────────────────────


def test_support_probe_reports_a_backend_not_a_promise():
    """A docstring claiming a capability is not a capability (oss-fix-03)."""
    support = te.table_support()
    assert set(support) == {"available", "backend", "reason"}
    assert isinstance(support["available"], bool)
    if support["available"]:
        assert support["backend"] == "pdfplumber"
    else:
        assert support["reason"], "unavailability must explain itself"


def test_pdfplumber_is_declared_in_requirements():
    """oss-table-02: it was imported by four modules and in no requirements file.

    A clean install therefore failed at the call site rather than at install
    time — the silent optional-dependency cliff.
    """
    import pathlib

    req = pathlib.Path(__file__).resolve().parents[2] / "requirements.txt"
    text = req.read_text(encoding="utf-8")
    assert "pdfplumber" in text, "pdfplumber imported at runtime but not declared"
    assert "beautifulsoup4" in text or "bs4" in text, "bs4 imported but not declared"


# ── Markdown rendering ───────────────────────────────────────────────────────


def test_first_row_becomes_the_header():
    md = te.to_markdown([["Control", "Status"], ["AC-2", "Implemented"]])
    lines = md.splitlines()
    assert lines[0] == "| Control | Status |"
    assert lines[1] == "| --- | --- |"
    assert lines[2] == "| AC-2 | Implemented |"


def test_cells_keep_their_column_association():
    """The whole point: a status must stay attached to its control."""
    md = te.to_markdown(
        [["Control", "Status"], ["AC-2", "Implemented"], ["AC-3", "Planned"]]
    )
    rows = [ln for ln in md.splitlines() if ln.startswith("|")][2:]
    assert "| AC-2 | Implemented |" in rows
    assert "| AC-3 | Planned |" in rows
    # the flat-text failure mode this replaces
    assert "AC-2 AC-3 Implemented Planned" not in md


def test_ragged_rows_are_padded_not_dropped():
    md = te.to_markdown([["a", "b", "c"], ["1"], ["2", "3"]])
    body = [ln for ln in md.splitlines() if ln.startswith("|")][2:]
    assert body[0] == "| 1 |  |  |"
    assert body[1] == "| 2 | 3 |  |"


def test_pipes_are_escaped_so_a_cell_cannot_forge_a_column():
    md = te.to_markdown([["h"], ["a|b"]])
    assert "a\\|b" in md


def test_newlines_inside_a_cell_do_not_break_the_row():
    md = te.to_markdown([["h"], ["line1\nline2"]])
    assert "line1 line2" in md
    assert len([ln for ln in md.splitlines() if ln.startswith("|")]) == 3


def test_runaway_cell_is_truncated():
    md = te.to_markdown([["h"], ["x" * 500]])
    assert len(md) < 400
    assert "…" in md


def test_none_cells_render_empty():
    assert te.to_markdown([["a", None], [None, "b"]]).count("|") > 0
    assert "None" not in te.to_markdown([["a", None]])


def test_empty_input_renders_nothing():
    assert te.to_markdown([]) == ""


# ── Artefact rejection ───────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "rows,why",
    [
        ([["only one row"]], "single row is a header box, not data"),
        ([["a"], ["b"], ["c"]], "single column is a sidebar"),
        ([["", ""], ["", ""]], "all-empty grid is a page border"),
        ([], "nothing at all"),
    ],
)
def test_layout_artefacts_are_rejected(rows, why):
    assert not te._is_meaningful(rows), why


def test_a_real_two_by_two_is_accepted():
    assert te._is_meaningful([["Control", "Status"], ["AC-2", "Implemented"]])


# ── Failure posture ──────────────────────────────────────────────────────────


def test_missing_file_returns_empty_not_raises(tmp_path):
    """Table recovery is an enhancement; it must never fail the document."""
    assert te.extract_tables(tmp_path / "nope.pdf") == []


def test_non_pdf_returns_empty_not_raises(tmp_path):
    junk = tmp_path / "not.pdf"
    junk.write_bytes(b"this is not a pdf")
    assert te.extract_tables(junk) == []


def test_absent_backend_degrades_silently(tmp_path, monkeypatch):
    monkeypatch.setattr(
        te, "table_support",
        lambda: {"available": False, "backend": None, "reason": "not installed"},
    )
    assert te.extract_tables(tmp_path / "x.pdf") == []


def test_tables_as_markdown_labels_the_page(monkeypatch):
    """A table divorced from its page is hard to verify against the source."""
    monkeypatch.setattr(
        te, "extract_tables",
        lambda path, max_pages=None: [
            te.ExtractedTable(page=7, index=0, rows=[["a", "b"]], markdown="| a | b |")
        ],
    )
    out = te.tables_as_markdown("x.pdf")
    assert "page 7" in out
    assert "| a | b |" in out


# ── Integration with the extractor ───────────────────────────────────────────


def test_pdf_extraction_appends_tables_and_never_substitutes(monkeypatch, tmp_path):
    """Additive by construction: a table-detection regression cannot cost text."""
    from tools.document_intelligence import extractors

    original = extractors.Extraction(
        text="body prose", provider="pymupdf",
        content_type="application/pdf", page_count=1, title="t",
    )
    monkeypatch.setattr(extractors, "_extract_pdf_text", lambda p: original)
    monkeypatch.setattr(
        "tools.document_intelligence.table_extract.tables_as_markdown",
        lambda p, max_pages=None: "| a | b |",
    )

    out = extractors._extract_pdf(tmp_path / "x.pdf")
    assert "body prose" in out.text, "existing text must survive"
    assert "| a | b |" in out.text
    assert out.provider == "pymupdf+tables"


def test_pdf_extraction_is_unchanged_when_no_tables_found(monkeypatch, tmp_path):
    from tools.document_intelligence import extractors

    original = extractors.Extraction(
        text="body prose", provider="pymupdf",
        content_type="application/pdf", page_count=1, title="t",
    )
    monkeypatch.setattr(extractors, "_extract_pdf_text", lambda p: original)
    monkeypatch.setattr(
        "tools.document_intelligence.table_extract.tables_as_markdown",
        lambda p, max_pages=None: "",
    )

    out = extractors._extract_pdf(tmp_path / "x.pdf")
    assert out.text == "body prose"
    assert out.provider == "pymupdf", "provider must not be tagged when nothing was added"


def test_table_failure_does_not_fail_the_document(monkeypatch, tmp_path):
    from tools.document_intelligence import extractors

    original = extractors.Extraction(
        text="body prose", provider="pypdf",
        content_type="application/pdf", page_count=1, title="t",
    )
    monkeypatch.setattr(extractors, "_extract_pdf_text", lambda p: original)

    def _boom(path, max_pages=None):
        raise RuntimeError("table engine exploded")

    monkeypatch.setattr(
        "tools.document_intelligence.table_extract.tables_as_markdown", _boom
    )

    out = extractors._extract_pdf(tmp_path / "x.pdf")
    assert out.text == "body prose", "a table crash must not lose the extracted text"
