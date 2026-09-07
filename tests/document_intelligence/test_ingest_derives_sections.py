# CUI // SP-CTI
"""ingest_file writes dic_sections for an ingested document (dwr-sect-01).

THE DEFECT THIS GUARDS. ``ingest_file`` wrote ``dic_documents``, ``dic_versions``
and ``dic_chunk_links`` and never a single ``dic_sections`` row. Sections are
only created by ``doc_generator``, import-from-docgen and template instantiate —
so an INGESTED document rendered with an empty Sections list and had no
coordinate space for an anchored change to land in. Measured on the live PG
board 2026-09-07: **16 of 55 documents carried any section row at all**, and the
39 that did not were exactly the ingested ones.

These run the real ``ingest_file`` against a temp SQLite database with every
LLM-touching stage switched off, so what is asserted is the persistence path
itself rather than a mocked stand-in for it.
"""
from __future__ import annotations

from tools.db.storage import get_connection
from tools.document_intelligence import ingest_orchestrator as io

# Spelled as literals, NOT imported from section_deriver, so this module still
# IMPORTS against a tree that has no such module -- which makes the red-first
# proof for these tests "ingest wrote no sections" (the actual defect) rather
# than a collection error that only proves a new file was added.
# test_basis_literals_match_the_module_constants keeps them from drifting.
BASIS_HEADINGS = "headings"
BASIS_WHOLE_DOCUMENT = "whole_document"

HEADED = """# Enclave Access Policy

Opening prose under the document title.

## 1 Purpose

This policy governs access to the enclave.

## 2 Scope

It applies to every system in the boundary.
"""

UNHEADED = (
    "A short note with no headings whatsoever.\n"
    "Two lines and it ends.\n"
)


def _ingest(tmp_path, name: str, body: str):
    """Run the real ingest with every LLM/vector stage off."""
    src = tmp_path / name
    src.write_text(body, encoding="utf-8")
    db = tmp_path / "dic.db"
    conn = get_connection(db_path=str(db))
    outcome = io.ingest_file(
        str(src),
        "default",
        tenant_id="t1",
        classification="CUI",
        created_by="tester",
        embed=False,
        bridge_kg=False,
        summarize=False,
        clean_ocr=False,
        extract_metadata=False,
        extract_identifiers=False,
        extract_correspondence=False,
        detect_anomalies=False,
        conn=conn,
    )
    return conn, outcome


def _sections(conn, version_id):
    rows = conn.execute(
        "SELECT section_id, heading, content, status, origin "
        "FROM dic_sections WHERE version_id = ? ORDER BY section_id",
        (version_id,),
    ).fetchall()
    return [dict(r) for r in rows]


# --------------------------------------------------------------- the core gap
def test_an_ingested_document_gets_sections(tmp_path):
    conn, outcome = _ingest(tmp_path, "policy.md", HEADED)
    try:
        assert outcome.doc_id
        secs = _sections(conn, f"{outcome.doc_id}_v1")
        assert secs, "ingest wrote no dic_sections — the defect dwr-sect-01 exists for"
        assert [s["heading"] for s in secs] == [
            "Enclave Access Policy",
            "1 Purpose",
            "2 Scope",
        ]
    finally:
        conn.close()


def test_ingested_sections_are_human_authored_and_approved(tmp_path):
    """An ingested document is the document of record, not an AI draft.

    The dic_sections column DEFAULTS are origin='ai_generated' status='draft'.
    Taking them would put real source prose into the AI-content population that
    citation_gate and the export gates are scoped to, and would mark an already
    approved document as awaiting review.
    """
    conn, outcome = _ingest(tmp_path, "policy.md", HEADED)
    try:
        for s in _sections(conn, f"{outcome.doc_id}_v1"):
            assert s["origin"] == "human_authored"
            assert s["status"] == "approved"
    finally:
        conn.close()


def test_section_content_is_a_verbatim_slice_of_the_document(tmp_path):
    """The anchoring invariant, asserted through the persistence path.

    Deriving the offsets correctly and then persisting normalised prose would
    leave every downstream anchor pointing at the wrong text, silently.
    """
    conn, outcome = _ingest(tmp_path, "policy.md", HEADED)
    try:
        for s in _sections(conn, f"{outcome.doc_id}_v1"):
            assert s["content"] in HEADED, (
                f"persisted content for {s['heading']!r} is not a slice of the source"
            )
    finally:
        conn.close()


# ------------------------------------------------------------------ the basis
def test_basis_literals_match_the_module_constants():
    """The literals above are a convenience for the red-first proof, not a fork."""
    from tools.document_intelligence import section_deriver as sd

    assert BASIS_HEADINGS == sd.BASIS_HEADINGS
    assert BASIS_WHOLE_DOCUMENT == sd.BASIS_WHOLE_DOCUMENT


def test_version_records_how_its_sections_were_derived(tmp_path):
    conn, outcome = _ingest(tmp_path, "policy.md", HEADED)
    try:
        row = conn.execute(
            "SELECT section_basis FROM dic_versions WHERE version_id = ?",
            (f"{outcome.doc_id}_v1",),
        ).fetchone()
        assert dict(row)["section_basis"] == BASIS_HEADINGS
    finally:
        conn.close()


def test_a_document_with_no_headings_says_so_rather_than_looking_structured(tmp_path):
    """One section by fallback must not read as a well-structured one-section doc."""
    conn, outcome = _ingest(tmp_path, "note.md", UNHEADED)
    try:
        vid = f"{outcome.doc_id}_v1"
        secs = _sections(conn, vid)
        assert len(secs) == 1
        row = conn.execute(
            "SELECT section_basis FROM dic_versions WHERE version_id = ?", (vid,)
        ).fetchone()
        assert dict(row)["section_basis"] == BASIS_WHOLE_DOCUMENT
    finally:
        conn.close()


# -------------------------------------------------------------------- re-ingest
def test_re_ingesting_replaces_sections_rather_than_duplicating_them(tmp_path):
    """Otherwise the document renders every heading twice and stale anchors resolve."""
    conn, outcome = _ingest(tmp_path, "policy.md", HEADED)
    try:
        vid = f"{outcome.doc_id}_v1"
        first = _sections(conn, vid)
        io.ingest_file(
            str(tmp_path / "policy.md"),
            "default",
            tenant_id="t1",
            classification="CUI",
            created_by="tester",
            embed=False,
            bridge_kg=False,
            summarize=False,
            clean_ocr=False,
            extract_metadata=False,
            extract_identifiers=False,
            extract_correspondence=False,
            detect_anomalies=False,
            conn=conn,
        )
        assert len(_sections(conn, vid)) == len(first)
    finally:
        conn.close()


# ------------------------------------------------------------ never break ingest
def test_a_failing_deriver_degrades_instead_of_losing_the_ingest(monkeypatch):
    """Section derivation is a convenience over an already-extracted document.

    A document that lands with no sections is degraded; a document that fails to
    land because the heading parser tripped over an unusual file is lost work.
    """
    monkeypatch.setattr(
        "tools.document_intelligence.section_deriver.outline_for_document",
        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")),
    )
    outline = io._derive_outline("# A\n\nbody\n", "T")
    assert outline.sections == []
    assert outline.basis == "empty"


def test_a_pathological_outline_degrades_whole_document_never_truncated():
    """Truncating would drop document text to stay under a limit."""
    text = "".join(f"{i} Clause\n\nbody {i}\n\n" for i in range(1, 60))
    from tools.document_intelligence.section_deriver import outline_for_document

    capped = outline_for_document(text, title="Big", max_sections=10)
    assert capped.basis == BASIS_WHOLE_DOCUMENT
    assert len(capped.sections) == 1
    assert capped.sections[0].content == text, "no document text may be dropped"
    # The heading count still reports what was actually found, so the cap is
    # visible rather than looking like a document with no structure.
    assert capped.heading_count > 10
