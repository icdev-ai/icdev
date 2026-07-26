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


@pytest.fixture(autouse=True)
def isolated_db(tmp_path: Path, monkeypatch):
    """Give each test a private SQLite file, with the schema already created.

    These tests used to run against the shared ``data/icdev.db`` and were red for
    two reasons that are really one:

    * On a fresh checkout the tables did not exist yet. The cleanup blocks below
      ran BEFORE the first ``ingest_file`` call — which is what creates the schema
      via ``_ensure_schema`` — so the test died on ``no such table:
      dic_chunk_links`` and never reached the code it was meant to exercise.
    * On a used database the tables existed but with the WRONG shape: several
      ``tests/docmod/*`` files create their own ``dic_documents``, and
      ``CREATE TABLE IF NOT EXISTS`` will not add a missing column to an existing
      table. Whichever test ran first won, and this one failed on
      ``no such column: content_sha256``.

    So the result depended on execution order and on leftover state — the schema
    belonged to whoever got there first. Owning the database makes these tests
    deterministic, and stops them writing fixture rows ('test_collection',
    'cli_collection', tenant 'acme') into whatever database the developer happens
    to have configured.
    """
    monkeypatch.setenv("ICDEV_STORAGE_BACKEND", "sqlite")
    monkeypatch.setenv("ICDEV_DB_PATH", str(tmp_path / "icdev.db"))
    conn = get_connection()
    try:
        orch._ensure_schema(conn)
        conn.commit()
    finally:
        conn.close()


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
    # No cleanup block: isolated_db gives this test an empty database of its own,
    # so there is no prior-run residue to delete.
    outcome = ingest_file(
        str(sample_doc),
        "test_collection",
        tenant_id="acme",
        classification="CUI",
        created_by="alice",
        embed=False,
        bridge_kg=False,
        summarize=False,
        extract_metadata=False,
        extract_identifiers=False,
        extract_correspondence=False,
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


_NO_LLM = dict(summarize=False, extract_metadata=False, extract_identifiers=False, extract_correspondence=False)


def test_reingest_is_idempotent(sample_doc: Path):
    first = ingest_file(
        str(sample_doc), "test_collection2", embed=False, bridge_kg=False, **_NO_LLM
    )
    second = ingest_file(
        str(sample_doc), "test_collection2", embed=False, bridge_kg=False, **_NO_LLM
    )
    assert first.doc_id == second.doc_id
    assert first.version_id == second.version_id
    links = _rows("dic_chunk_links", second.version_id)
    assert len(links) == second.chunks  # not doubled


def test_context_defaults_when_unset(sample_doc: Path):
    outcome = ingest_file(
        str(sample_doc), "default_collection", embed=False, bridge_kg=False, **_NO_LLM
    )
    assert outcome.tenant_id == "default"
    assert outcome.classification == "UNCLASSIFIED"


def test_missing_file_raises():
    with pytest.raises(FileNotFoundError):
        ingest_file("does_not_exist_xyz.md", "c", embed=False, bridge_kg=False)


def test_cli_json(sample_doc: Path, capsys):
    # No cleanup block — see isolated_db.
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
            "--no-summarize",
            "--no-metadata",
            "--no-identifiers",
            "--no-correspondence",
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

def test_pdf_text_path_returns_first_available_provider(tmp_path: Path):
    """A plain-text PDF should extract via the first successful provider in the chain.

    The four-pass chain is pymupdf -> pdfplumber -> pypdf -> OCR. For the
    constitution PDF, pymupdf succeeds first, so the provider reflects that.
    """
    from tools.document_intelligence.extractors import _extract_pdf

    pdf_path = Path("C:/Users/schuo/Downloads/constitution.pdf")
    if not pdf_path.exists():
        pytest.skip("constitution.pdf not available")

    extraction = _extract_pdf(pdf_path)
    # A provider may augment itself ("pymupdf+tables" when table extraction also
    # ran). The chain position is what this pins, not the augmentation, so
    # compare the base provider.
    assert extraction.provider.split("+")[0] in ("pymupdf", "pdfplumber", "pypdf")
    assert len(extraction.text) > 1000
    assert extraction.page_count == 19


def test_pdf_all_failed_warning_when_no_ocr_available(tmp_path: Path, monkeypatch):
    """When all PDF extractors fail and no OCR engine is available, warn clearly."""
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
    assert extraction.provider == "pdf-all-failed"
    assert extraction.text == ""
    # The all-failed path warns that every pass failed and suggests Tesseract.
    warnings_text = " ".join(extraction.warnings)
    assert "All PDF extraction passes failed" in warnings_text
    assert "Tesseract" in warnings_text


def test_pypdfium2_rendering_produces_images(tmp_path: Path, monkeypatch):
    """pypdfium2 must render PDF pages to PIL images without error."""
    from tools.document_intelligence.extractors import _try_pdf_ocr
    from tools.document_intelligence import extractors as _ext

    pdf_path = Path("C:/Users/schuo/Downloads/constitution.pdf")
    if not pdf_path.exists():
        pytest.skip("constitution.pdf not available")

    # Mock all OCR paths so the test only exercises pypdfium2 page rendering.
    monkeypatch.setattr(_ext, "_ocr_image", lambda img: "")

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
