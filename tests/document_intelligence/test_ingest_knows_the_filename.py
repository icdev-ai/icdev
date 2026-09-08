# CUI // SP-CTI
"""`ingest_file` is told the name, instead of being repaired afterwards.

Every upload path in this repo writes the request body to a NamedTemporaryFile
and hands the temp path to `ingest_file`, which had only that path to name the
document by. Measured on the live board 2026-09-08: 21 documents in
`dic_documents` carried a temp stem as their TITLE, and 20 of them carried it as
their FILENAME too -- so for those 20 the original name is not recoverable from
anywhere, which is what makes this a defect in the ingester rather than
something a repair statement can keep up with.

The dashboard upload was fixed at the row it wrote (PR #2191). This is the same
fix one level down, where the other three callers reach it too.
"""

from __future__ import annotations

import pytest

from tools.document_intelligence.ingest_orchestrator import client_basename


class TestTheNameACllientSentIsANameNotAPath:
    """`Path(name).name` splits only on the HOST separator. A Windows client
    posting to a POSIX server gets its whole path back as the 'filename', and
    that string lands in a column a UI renders."""

    def test_a_windows_path_reduces_to_its_basename(self):
        sep = chr(92)
        assert client_basename(f"C:{sep}Users{sep}x{sep}policy.pdf") == "policy.pdf"

    def test_a_posix_path_reduces_to_its_basename(self):
        assert client_basename("/var/tmp/a/policy.pdf") == "policy.pdf"

    def test_a_plain_name_is_returned_unchanged(self):
        assert client_basename("policy.pdf") == "policy.pdf"

    def test_traversal_cannot_survive(self):
        assert client_basename("../../etc/passwd") == "passwd"
        assert client_basename("..") == ""
        assert client_basename(".") == ""

    def test_a_bare_drive_letter_is_not_a_path_segment(self):
        assert client_basename("C:policy.pdf") == "policy.pdf"

    @pytest.mark.parametrize("empty", ["", "   ", None])
    def test_nothing_reduces_to_empty_so_the_caller_keeps_its_fallback(self, empty):
        """`""` rather than a guess: `ingest_file` then falls back to the temp
        name, which is wrong but honest, instead of storing a name nobody sent."""
        assert client_basename(empty) == ""

    def test_a_colon_inside_a_real_title_is_not_a_drive_letter(self):
        """canvas_push and the URL routes pass `<title>.txt`, and titles have
        colons in them."""
        assert client_basename("Q3 Report: Final.docx") == "Q3 Report: Final.docx"


class TestEveryCallerNowTellsTheIngesterTheName:
    """A source-level assertion, deliberately.

    The four call sites spool to a temp file, so a test that only drove one of
    them would leave the other three free to regress in exactly the way this
    card is about -- and each of the other three is a route (URL ingest,
    YouTube ingest, canvas push) whose wrongness is invisible in its own
    response, because all three RETURN a title they were not storing.
    """

    @staticmethod
    def _source(rel: str) -> str:
        from pathlib import Path
        import tools.document_intelligence as pkg
        return (Path(pkg.__file__).parent / rel).read_text(encoding="utf-8")

    def test_the_orchestrator_accepts_the_parameter(self):
        src = self._source("ingest_orchestrator.py")
        assert "original_filename: str | None = None," in src
        assert "display_name = client_basename(original_filename) or p.name" in src

    def test_the_temp_path_no_longer_names_the_stored_document(self):
        """The INSERT and the title fallback must both read the display name."""
        src = self._source("ingest_orchestrator.py")
        assert "doc_id, collection_id, source_id, display_name, str(p)," in src
        assert "extracted_title or ai_title or display_stem, p.stat().st_size" in src

    def test_the_near_duplicate_check_compares_real_titles(self):
        """Against a temp stem it could never match, so the whole check was
        dead in exactly the case it exists for."""
        src = self._source("ingest_orchestrator.py")
        assert "title_for_dup = extracted_title or ai_title or display_stem" in src

    @pytest.mark.parametrize("caller", [
        "original_filename=filename,",                     # dashboard upload
        'original_filename=f"{extraction.title or url}.txt",',  # url + youtube
    ])
    def test_the_blueprint_routes_pass_it(self, caller):
        assert caller in self._source("blueprint.py")

    def test_the_url_and_youtube_routes_both_pass_it(self):
        src = self._source("blueprint.py")
        assert src.count('original_filename=f"{extraction.title or url}.txt",') == 2

    def test_canvas_push_passes_the_title_it_was_already_returning(self):
        """`push_artifact` takes `title`, returns it in its result dict, and
        stored the temp stem -- so a caller reading that dict believed a title
        that was not in the database."""
        assert 'original_filename=f"{title}.txt",' in self._source("canvas_push.py")


class TestTheExtractedTitleStillWins:
    """This replaces an accident, not a real answer. A title the extractor
    genuinely derived -- PDF metadata, a leading heading -- is better than a
    filename stem and must survive."""

    def test_the_fallback_order_is_extraction_then_ai_then_the_name(self):
        src = TestEveryCallerNowTellsTheIngesterTheName._source(
            "ingest_orchestrator.py")
        # display_stem is LAST in every one of the three fallback chains
        for chain in (
            "extracted_title or ai_title or display_stem, p.stat().st_size",
            "title=extracted_title or ai_title or display_stem,",
            "_derive_outline(text, extracted_title or ai_title or display_stem)",
        ):
            assert chain in src, chain
        assert "display_stem or ai_title" not in src
        assert "display_stem or extraction.title" not in src


class TestTheFilenameLengthCheckCanFinallyFire:
    """`detect_consumer_file_anomaly` measured the length of the TEMP name,
    which is always short -- so `filename_too_long` could not fire on the one
    input that produces it."""

    def test_a_long_client_name_is_flagged(self, tmp_path):
        from tools.document_intelligence.ingest_orchestrator import (
            detect_consumer_file_anomaly,
        )
        f = tmp_path / "tmpabcd1234.txt"
        f.write_text("x" * 500, encoding="utf-8")
        long_name = ("a" * 300) + ".txt"
        result = detect_consumer_file_anomaly(f, display_name=long_name)
        assert result is not None
        assert any(s.startswith("filename_too_long") for s in result["signals"])
        assert result["filename"] == long_name

    def test_without_a_display_name_it_behaves_exactly_as_before(self, tmp_path):
        from tools.document_intelligence.ingest_orchestrator import (
            detect_consumer_file_anomaly,
        )
        f = tmp_path / "short.txt"
        f.write_text("x" * 500, encoding="utf-8")
        assert detect_consumer_file_anomaly(f) is None


# ══════════════════════════════════════════════════════════════════════════
# The real ingester, against a real database
#
# The assertions above are structural: they keep the four call sites honest.
# These drive the actual persistence path with every LLM stage off, so what is
# proven is the row that lands, not a stand-in for it.
# ══════════════════════════════════════════════════════════════════════════

NO_HEADING = "Just a paragraph of prose with nothing that reads as a title.\n"
WITH_HEADING = "# Enclave Access Policy\n\nOpening prose under the title.\n"


def _ingest(tmp_path, temp_name: str, body: str, original: str | None):
    """Run the real ingest from a file named like a TEMP file."""
    from tools.db.storage import get_connection
    from tools.document_intelligence import ingest_orchestrator as io

    src = tmp_path / temp_name
    src.write_text(body, encoding="utf-8")
    conn = get_connection(db_path=str(tmp_path / "dic.db"))
    outcome = io.ingest_file(
        str(src), "default",
        tenant_id="t1", classification="CUI", created_by="tester",
        embed=False, bridge_kg=False, summarize=False, clean_ocr=False,
        extract_metadata=False, extract_identifiers=False,
        extract_correspondence=False, detect_anomalies=False,
        original_filename=original,
        conn=conn,
    )
    row = dict(conn.execute(
        "SELECT filename, title, filepath FROM dic_documents WHERE doc_id = ?",
        (outcome.doc_id,),
    ).fetchone())
    return conn, outcome, row


def test_the_stored_document_carries_the_uploaded_name_not_the_temp_one(tmp_path):
    conn, _, row = _ingest(
        tmp_path, "tmpqsnpbru9.md", NO_HEADING, "peering-policy-update.md")
    try:
        assert row["filename"] == "peering-policy-update.md"
        assert row["title"] == "peering-policy-update"
        assert not row["title"].startswith("tmp")
        # the temp path is still recorded as WHERE the bytes were, which is
        # true and is a different column from what the document is CALLED
        assert "tmpqsnpbru9" in row["filepath"]
    finally:
        conn.close()


def test_without_the_parameter_nothing_changes_for_existing_callers(tmp_path):
    """The three callers not touched by this change keep their behaviour
    exactly. A default that altered them would be a silent migration."""
    conn, _, row = _ingest(tmp_path, "tmp9x41vmaz.md", NO_HEADING, None)
    try:
        assert row["filename"] == "tmp9x41vmaz.md"
        assert row["title"] == "tmp9x41vmaz"
    finally:
        conn.close()


def _ingest_with_extraction(tmp_path, temp_name, title, original):
    """Drive ingest with an extractor that returns a chosen title.

    MEASURED while writing this: no built-in extractor derives a title from a
    markdown heading -- `_select_extractor` falls back to the file's stem in
    three separate places, which is the whole reason `extracted_title` exists.
    So the fallback ORDER is proven at the seam rather than through a format
    that cannot produce a real title.
    """
    from tools.db.storage import get_connection
    from tools.document_intelligence import ingest_orchestrator as io

    src = tmp_path / temp_name
    src.write_text(NO_HEADING, encoding="utf-8")
    real = io._select_extractor

    def fake(path):
        got = real(path)
        return io.Extraction(
            text=got.text, provider=got.provider, content_type=got.content_type,
            page_count=got.page_count, title=title,
        )

    io._select_extractor = fake
    try:
        conn = get_connection(db_path=str(tmp_path / "dic.db"))
        outcome = io.ingest_file(
            str(src), "default",
            tenant_id="t1", classification="CUI", created_by="tester",
            embed=False, bridge_kg=False, summarize=False, clean_ocr=False,
            extract_metadata=False, extract_identifiers=False,
            extract_correspondence=False, detect_anomalies=False,
            original_filename=original, conn=conn,
        )
    finally:
        io._select_extractor = real
    row = dict(conn.execute(
        "SELECT filename, title FROM dic_documents WHERE doc_id = ?",
        (outcome.doc_id,),
    ).fetchone())
    return conn, row


def test_a_real_extracted_title_still_beats_the_uploaded_name(tmp_path):
    """This card replaces an accident, not a better answer. A title the
    extractor genuinely found -- PDF metadata, a leading heading -- is the
    better answer and must survive."""
    conn, row = _ingest_with_extraction(
        tmp_path, "tmp6dvlkkna.md", "Enclave Access Policy", "upload-2.md")
    try:
        assert row["title"] == "Enclave Access Policy"
        assert row["filename"] == "upload-2.md", "the filename is restored either way"
    finally:
        conn.close()


def test_an_extractor_that_merely_echoed_the_temp_stem_does_NOT_win(tmp_path):
    """The bug one layer down. An extractor that finds no title returns the
    file's own stem, so `extraction.title` was the TEMP NAME arriving by a
    second route -- non-empty, and therefore winning the fallback chain even
    after the caller had supplied the real name."""
    conn, row = _ingest_with_extraction(
        tmp_path, "tmpqsnpbru9.md", "tmpqsnpbru9", "peering-policy-update.md")
    try:
        assert row["title"] == "peering-policy-update"
        assert not row["title"].startswith("tmp")
    finally:
        conn.close()


def test_an_echoed_stem_is_KEPT_when_no_better_name_was_supplied(tmp_path):
    """Inert for every caller that passes nothing: there is no better answer
    then, and rewriting those titles would be a migration nobody asked for."""
    conn, row = _ingest_with_extraction(
        tmp_path, "genuine-report.md", "genuine-report", None)
    try:
        assert row["title"] == "genuine-report"
    finally:
        conn.close()


def test_a_client_supplied_path_is_reduced_to_its_name_before_storage(tmp_path):
    """A browser may post a full path. It must not become the filename."""
    sep = chr(92)
    conn, _, row = _ingest(
        tmp_path, "tmprr4p18oe.md", NO_HEADING,
        f"C:{sep}Users{sep}schuo{sep}Documents{sep}policy.md")
    try:
        assert row["filename"] == "policy.md"
        assert sep not in row["filename"] and "/" not in row["filename"]
    finally:
        conn.close()


def test_an_empty_original_falls_back_to_the_temp_name_rather_than_nothing(tmp_path):
    """Wrong but honest beats a blank name a UI renders as an empty row."""
    conn, _, row = _ingest(tmp_path, "tmpx56sbd7b.md", NO_HEADING, "   ")
    try:
        assert row["filename"] == "tmpx56sbd7b.md"
    finally:
        conn.close()
