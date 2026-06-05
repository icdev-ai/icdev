"""Tests for the DIC ingest orchestrator (dic-ingest-03).

[TEMPLATE: CUI // SP-CTI]

Embedding and KG bridging are disabled so the suite runs headless without an
embedding provider or vector store; the focus is provider routing + DIC row
writes (dic_documents / dic_versions / dic_chunk_links) with tenant/
classification stamps.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from tools.db.storage import get_connection
from tools.document_intelligence import ingest_orchestrator as orch
from tools.document_intelligence.ingest_orchestrator import ingest_file


@pytest.fixture
def sample_doc(tmp_path: Path) -> Path:
    p = tmp_path / "policy.md"
    p.write_text(
        "# Acme Security Policy\n\n"
        + ("All access requires multi-factor authentication. " * 80),
        encoding="utf-8",
    )
    return p


def _rows(table: str, version_id: str):
    conn = get_connection()
    try:
        cur = conn.cursor()
        cur.execute(f"SELECT * FROM {table} WHERE version_id = ?", (version_id,))
        cols = [d[0] for d in cur.description]
        return [dict(zip(cols, r)) for r in cur.fetchall()]
    finally:
        conn.close()


def test_provider_routing_picks_builtin_text(sample_doc: Path):
    extraction = orch._select_extractor(sample_doc)
    assert extraction.provider in ("builtin-text", "builtin-fallback")
    assert "multi-factor" in extraction.text


def test_html_extractor_strips_tags(tmp_path: Path):
    p = tmp_path / "page.html"
    p.write_text("<html><body><h1>Title</h1><p>Hello world</p></body></html>", "utf-8")
    extraction = orch._select_extractor(p)
    assert extraction.content_type == "text/html"
    assert "<" not in extraction.text
    assert "Hello world" in extraction.text


def test_ingest_writes_dic_rows_with_stamps(sample_doc: Path):
    # Clean up any prior test run residue in the shared DB.
    conn = get_connection()
    try:
        cur = conn.cursor()
        cur.execute("DELETE FROM dic_chunk_links WHERE doc_id LIKE 'dic_doc_%'")
        cur.execute("DELETE FROM dic_versions WHERE doc_id LIKE 'dic_doc_%'")
        cur.execute("DELETE FROM dic_documents WHERE collection_id = ?", ("test_collection",))
        conn.commit()
    finally:
        conn.close()

    outcome = ingest_file(
        str(sample_doc),
        "test_collection",
        tenant_id="acme",
        classification="CUI",
        created_by="alice",
        embed=False,
        bridge_kg=False,
    )

    assert outcome.chunks >= 1
    assert outcome.tenant_id == "acme"
    assert outcome.classification == "CUI"
    assert outcome.doc_id.startswith("dic_doc_")

    # dic_documents row.
    conn = get_connection()
    try:
        cur = conn.cursor()
        cur.execute(
            "SELECT collection_id, tenant_id, classification, provider, source_id "
            "FROM dic_documents WHERE doc_id = ?",
            (outcome.doc_id,),
        )
        doc = cur.fetchone()
    finally:
        conn.close()
    assert doc is not None
    assert doc[0] == "test_collection"
    assert doc[1] == "acme"
    assert doc[2] == "CUI"
    assert doc[4] == outcome.source_id

    # dic_versions: initial version is human_authored / approved.
    versions = _rows("dic_versions", outcome.version_id)
    assert len(versions) == 1
    assert versions[0]["origin"] == "human_authored"
    assert versions[0]["status"] == "approved"
    assert versions[0]["tenant_id"] == "acme"
    assert versions[0]["classification"] == "CUI"

    # dic_chunk_links: one per chunk, mapping rag chunk id back to the doc.
    links = _rows("dic_chunk_links", outcome.version_id)
    assert len(links) == outcome.chunks
    for link in links:
        assert link["doc_id"] == outcome.doc_id
        assert link["rag_chunk_id"]  # non-empty rag chunk id
        assert link["tenant_id"] == "acme"
        assert link["classification"] == "CUI"


def test_reingest_is_idempotent(sample_doc: Path):
    first = ingest_file(
        str(sample_doc), "test_collection2", embed=False, bridge_kg=False
    )
    second = ingest_file(
        str(sample_doc), "test_collection2", embed=False, bridge_kg=False
    )
    assert first.doc_id == second.doc_id
    assert first.version_id == second.version_id
    links = _rows("dic_chunk_links", second.version_id)
    assert len(links) == second.chunks  # not doubled


def test_context_defaults_when_unset(sample_doc: Path):
    outcome = ingest_file(
        str(sample_doc), "default_collection", embed=False, bridge_kg=False
    )
    assert outcome.tenant_id == "default"
    assert outcome.classification == "UNCLASSIFIED"


def test_missing_file_raises():
    with pytest.raises(FileNotFoundError):
        ingest_file("does_not_exist_xyz.md", "c", embed=False, bridge_kg=False)


def test_cli_json(sample_doc: Path, capsys):
    # Clean up any prior test run residue in the shared DB.
    conn = get_connection()
    try:
        cur = conn.cursor()
        cur.execute("DELETE FROM dic_chunk_links WHERE doc_id LIKE 'dic_doc_%'")
        cur.execute("DELETE FROM dic_versions WHERE doc_id LIKE 'dic_doc_%'")
        cur.execute("DELETE FROM dic_documents WHERE collection_id = ?", ("cli_collection",))
        conn.commit()
    finally:
        conn.close()

    from tools.document_intelligence.__main__ import main

    rc = main(
        [
            "--ingest",
            str(sample_doc),
            "--collection",
            "cli_collection",
            "--tenant",
            "cli_tenant",
            "--classification",
            "CUI",
            "--no-embed",
            "--no-kg",
            "--json",
        ]
    )
    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is True
    assert payload["collection_id"] == "cli_collection"
    assert payload["tenant_id"] == "cli_tenant"
    assert payload["version_id"].endswith("_v1")


# ── PDF OCR fallback tests ──────────────────────────────────────────────────

def test_pdf_text_path_returns_pypdf_provider(tmp_path: Path):
    """A plain-text PDF should extract via pypdf without touching OCR."""
    from tools.document_intelligence.extractors import _extract_pdf

    # Create a minimal text-based PDF using pypdf
    try:
        from pypdf import PdfWriter
    except ImportError:
        pytest.skip("pypdf not installed")

    writer = PdfWriter()
    writer.add_blank_page(width=612, height=792)
    # pypdf doesn't support adding text directly to blank pages easily,
    # so we use a real file we know works.
    pdf_path = Path("C:/Users/schuo/Downloads/constitution.pdf")
    if not pdf_path.exists():
        pytest.skip("constitution.pdf not available")

    extraction = _extract_pdf(pdf_path)
    assert extraction.provider == "pypdf"
    assert len(extraction.text) > 1000
    assert extraction.page_count == 19


def test_pdf_ocr_fallback_warning_when_no_ocr_available(tmp_path: Path, monkeypatch):
    """When pypdf returns empty text and no OCR engine is available, warn clearly."""
    from tools.document_intelligence.extractors import _extract_pdf

    # Bypass easyocr model download and vision LLM probes so the test is fast.
    monkeypatch.setattr(
        "tools.document_intelligence.extractors._get_easyocr_reader",
        lambda: None,
        raising=False,
    )
    monkeypatch.setattr(
        "tools.document_intelligence.extractors._vision_ocr",
        lambda img: "",
        raising=False,
    )

    # Create a dummy PDF that pypdf can open but which has no extractable text
    try:
        from pypdf import PdfWriter
    except ImportError:
        pytest.skip("pypdf not installed")

    writer = PdfWriter()
    writer.add_blank_page(width=612, height=792)
    pdf_path = tmp_path / "blank.pdf"
    with open(pdf_path, "wb") as f:
        writer.write(f)

    extraction = _extract_pdf(pdf_path)
    assert extraction.provider == "pypdf"  # falls through because OCR also fails
    assert extraction.text == ""
    assert any("scanned/image PDF" in w for w in extraction.warnings)
    assert any("easyocr" in w for w in extraction.warnings)


def test_pypdfium2_rendering_produces_images(tmp_path: Path):
    """pypdfium2 must render PDF pages to PIL images without error."""
    from tools.document_intelligence.extractors import _try_pdf_ocr
    from pathlib import Path

    pdf_path = Path("C:/Users/schuo/Downloads/constitution.pdf")
    if not pdf_path.exists():
        pytest.skip("constitution.pdf not available")

    # _try_pdf_ocr returns empty string when no OCR engine is available,
    # but it should NOT raise — rendering must succeed silently.
    result = _try_pdf_ocr(pdf_path, 19, max_pages=2)
    assert isinstance(result, str)
    # If no OCR engine is installed, result is empty; the important thing is no exception.


def test_easyocr_reader_is_cached(monkeypatch):
    """_get_easyocr_reader should cache the reader to avoid re-initializing."""
    from tools.document_intelligence.extractors import _get_easyocr_reader

    # Force re-initialization
    monkeypatch.setattr(
        "tools.document_intelligence.extractors._EASYOCR_READER",
        None,
        raising=False,
    )
    monkeypatch.setattr(
        "tools.document_intelligence.extractors._EASYOCR_TRIED",
        False,
        raising=False,
    )

    reader = _get_easyocr_reader()
    # Second call must return the SAME cached object (identity check).
    reader2 = _get_easyocr_reader()
    assert reader2 is reader
