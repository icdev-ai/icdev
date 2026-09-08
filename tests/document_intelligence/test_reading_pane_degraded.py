# CUI // SP-CTI
"""A degraded render SAYS it is degraded, and says which absence caused it (dwr-fid-03).

THE DEFECT THESE GUARD. ``doc_detail`` rendered ``dic_sections`` and, when there
were none, one line: *"No sections yet. Generate a draft or instantiate a
template."* That single line was printed for three different documents — one
whose full text was in ``rag_chunks`` all along, one whose chunks had been
deleted, and one that was never chunked — and for a document whose uploaded
original is gone as readily as for one still on disk. A thin render was
indistinguishable from a complete one, which is the defect this card series
exists to refuse.

Measured on the live PG board 2026-09-08: 55 documents, 16 with sections; of the
39 without, 19 render from ``rag_chunks``, 9 name chunk links that ALL dangle,
and 11 were never chunked.

The fixture GUARANTEES the shape of every table it reads (dwr-anchor-02) rather
than assuming an ambient schema — a missing column would otherwise make each
read return nothing and every assertion below pass against a database that could
not answer the question.
"""
from __future__ import annotations

import ast
import pathlib
import re

import pytest

from tests._sql_compat import connect

# Spelled as literals, NOT imported, so this module still IMPORTS against a tree
# with no reading_pane -- which makes the red-first proof "the page cannot tell
# these apart" rather than a collection error proving only that a file was added.
BASIS_SECTIONS = "sections"
BASIS_CHUNKS = "chunks"
BASIS_CHUNKS_MISSING = "chunks_missing"
BASIS_NO_TEXT = "no_text"
BASIS_UNMEASURABLE = "unmeasurable"

_DDL = (
    """CREATE TABLE dic_documents (
        doc_id TEXT PRIMARY KEY, collection_id TEXT, filename TEXT, filepath TEXT,
        title TEXT, tenant_id TEXT, classification TEXT, created_at TEXT,
        original_path TEXT, original_sha256 TEXT, original_retained_at TEXT
    )""",
    """CREATE TABLE dic_sections (
        section_id TEXT PRIMARY KEY, version_id TEXT, doc_id TEXT, heading TEXT,
        content TEXT, status TEXT, origin TEXT
    )""",
    """CREATE TABLE dic_chunk_links (
        link_id TEXT PRIMARY KEY, doc_id TEXT, version_id TEXT, rag_chunk_id TEXT,
        collection_id TEXT, chunk_index INTEGER, page INTEGER, section TEXT
    )""",
    """CREATE TABLE rag_chunks (
        id TEXT PRIMARY KEY, content TEXT, source_type TEXT, source_id TEXT,
        source_table TEXT, chunk_index INTEGER
    )""",
)


@pytest.fixture()
def conn(tmp_path):
    c = connect(str(tmp_path / "pane.db"))
    for stmt in _DDL:
        c.execute(stmt)
    c.commit()
    yield c
    c.close()


def _doc(conn, doc_id: str, **cols) -> None:
    row = {"doc_id": doc_id, "collection_id": "default", "filename": f"{doc_id}.pdf",
           "filepath": None, "title": doc_id, "tenant_id": "t1", "classification": "CUI",
           "created_at": "2026-09-08T00:00:00Z", "original_path": None,
           "original_sha256": None, "original_retained_at": None}
    row.update(cols)
    keys = list(row)
    conn.execute(
        f"INSERT INTO dic_documents ({','.join(keys)}) VALUES ({','.join(['%s'] * len(keys))})",
        tuple(row[k] for k in keys),
    )
    conn.commit()


def _section(conn, doc_id: str, n: int = 1) -> None:
    for i in range(n):
        conn.execute(
            "INSERT INTO dic_sections (section_id, version_id, doc_id, heading, content, status, origin) "
            "VALUES (%s,%s,%s,%s,%s,%s,%s)",
            (f"{doc_id}_s{i}", f"{doc_id}_v1", doc_id, f"H{i}", "body", "approved", "human_authored"),
        )
    conn.commit()


def _chunk(conn, chunk_id: str, doc_id: str, idx: int, content: str = "chunk body") -> None:
    conn.execute(
        "INSERT INTO rag_chunks (id, content, source_type, source_id, source_table, chunk_index) "
        "VALUES (%s,%s,%s,%s,%s,%s)",
        (chunk_id, content, "dic_document", doc_id, "dic_documents", idx),
    )
    conn.commit()


def _link(conn, doc_id: str, chunk_id: str, idx: int, page=None, section=None) -> None:
    conn.execute(
        "INSERT INTO dic_chunk_links (link_id, doc_id, version_id, rag_chunk_id, collection_id, "
        "chunk_index, page, section) VALUES (%s,%s,%s,%s,%s,%s,%s,%s)",
        (f"{doc_id}-l{idx}", doc_id, f"{doc_id}_v1", chunk_id, "default", idx, page, section),
    )
    conn.commit()


def _pane(conn, doc_id: str):
    from tools.document_intelligence.reading_pane import build_pane

    return build_pane(conn, doc_id)


def _codes(pane) -> set[str]:
    return {lim.code for lim in pane.limits}


# ── the three absences the page could not tell apart ─────────────────────────

def test_a_document_with_chunks_renders_them(conn):
    _doc(conn, "d_chunks")
    _chunk(conn, "chunk-a", "d_chunks", 0, "First page of prose.")
    _chunk(conn, "chunk-b", "d_chunks", 1, "Second page of prose.")
    _link(conn, "d_chunks", "chunk-a", 0)
    _link(conn, "d_chunks", "chunk-b", 1)

    pane = _pane(conn, "d_chunks")
    assert pane.basis == BASIS_CHUNKS
    assert pane.renders_text
    assert [c.content for c in pane.chunks] == ["First page of prose.", "Second page of prose."]
    assert pane.text_source == "chunk_links"


def test_dangling_links_are_chunks_missing_never_no_text(conn):
    """9 documents on the live board are in exactly this state.

    Every link resolves to nothing: the document WAS chunked and the chunks have
    since been deleted. Reporting that as "never chunked" sends a reader to the
    wrong fix, and reporting it as an empty document is a claim about the
    document rather than about the store.
    """
    _doc(conn, "d_gone")
    _link(conn, "d_gone", "chunk-vanished", 0)
    _link(conn, "d_gone", "chunk-vanished-2", 1)

    pane = _pane(conn, "d_gone")
    assert pane.basis == BASIS_CHUNKS_MISSING
    assert pane.links_total == 2
    assert pane.links_resolved == 0
    assert not pane.renders_text
    assert "chunks_missing" in _codes(pane)
    assert "no_text" not in _codes(pane)


def test_never_chunked_is_no_text_never_chunks_missing(conn):
    _doc(conn, "d_bare")

    pane = _pane(conn, "d_bare")
    assert pane.basis == BASIS_NO_TEXT
    assert pane.links_total == 0
    assert "no_text" in _codes(pane)
    assert "chunks_missing" not in _codes(pane)


def test_the_three_empty_states_do_not_render_the_same_words(conn):
    """The whole card in one assertion: three documents, three panes, three
    different explanations. Before this module all three printed one line."""
    _doc(conn, "d1")
    _chunk(conn, "chunk-1", "d1", 0)
    _link(conn, "d1", "chunk-1", 0)
    _doc(conn, "d2")
    _link(conn, "d2", "chunk-missing", 0)
    _doc(conn, "d3")

    said = [tuple(sorted(_codes(_pane(conn, d)))) for d in ("d1", "d2", "d3")]
    assert len(set(said)) == 3, f"two documents render the same explanation: {said}"


# ── text recovered by source id, and the fact that it WAS ────────────────────

def test_text_is_recovered_by_source_id_when_links_dangle(conn):
    """19 of the 39 section-less documents on the live board are readable only
    this way — 17 of them have no usable link at all."""
    _doc(conn, "d_recovered")
    _link(conn, "d_recovered", "chunk-dead", 0)      # a link that resolves to nothing
    _chunk(conn, "chunk-live", "d_recovered", 0, "Recovered prose.")

    pane = _pane(conn, "d_recovered")
    assert pane.basis == BASIS_CHUNKS
    assert pane.text_source == "rag_source_id"
    assert [c.content for c in pane.chunks] == ["Recovered prose."]


def test_recovery_by_source_id_is_stated_not_silent(conn):
    """A silent fallback would hide that the link table has gone stale."""
    _doc(conn, "d_recovered")
    _link(conn, "d_recovered", "chunk-dead", 0)
    _chunk(conn, "chunk-live", "d_recovered", 0, "Recovered prose.")

    pane = _pane(conn, "d_recovered")
    detail = next(x.detail for x in pane.limits if x.code == "text_from_chunks")
    assert "source id" in detail
    assert "0 of 1" in detail, "the pane does not say how many of its own links resolved"


def test_a_chunk_of_another_document_is_never_served(conn):
    """`source_id` alone is not enough — `source_type` and `source_table` are
    asked for too, so a row of another kind can never become this document's prose."""
    _doc(conn, "d_x")
    conn.execute(
        "INSERT INTO rag_chunks (id, content, source_type, source_id, source_table, chunk_index) "
        "VALUES (%s,%s,%s,%s,%s,%s)",
        ("chunk-other", "Somebody else's text.", "compliance_reference", "d_x", "nist_controls", 0),
    )
    conn.commit()

    pane = _pane(conn, "d_x")
    assert pane.basis == BASIS_NO_TEXT
    assert pane.chunks == []


# ── anchors ──────────────────────────────────────────────────────────────────

def test_every_rendered_chunk_carries_a_unique_stable_anchor(conn):
    _doc(conn, "d_anchor")
    for i in range(4):
        _chunk(conn, f"chunk-{i}", "d_anchor", i)
        _link(conn, "d_anchor", f"chunk-{i}", i)

    first = _pane(conn, "d_anchor")
    again = _pane(conn, "d_anchor")
    ids = [c.anchor_id for c in first.chunks]
    assert len(set(ids)) == len(ids) == 4, "anchors collide"
    assert ids == [c.anchor_id for c in again.chunks], "anchors are not stable across renders"
    assert all(a.startswith("chunk-") and " " not in a and "#" not in a for a in ids)


def test_the_pane_says_anchored_review_is_unavailable(conn):
    """The pane's own anchors work; the REVIEW coordinate space does not,
    because comments and suggestions index offsets into a section's content and
    this document has none. Saying nothing would let a reviewer type into a box
    whose anchor could not land."""
    _doc(conn, "d_anchor")
    _chunk(conn, "chunk-0", "d_anchor", 0)
    _link(conn, "d_anchor", "chunk-0", 0)

    pane = _pane(conn, "d_anchor")
    detail = next(x.detail for x in pane.limits if x.code == "no_sections")
    assert "anchor" in detail.lower()


# ── which of the two is missing ──────────────────────────────────────────────

def test_a_document_with_sections_declines_the_degraded_pane(conn):
    _doc(conn, "d_full", original_path=None, filepath=None)
    _section(conn, "d_full", 3)

    pane = _pane(conn, "d_full")
    assert pane.basis == BASIS_SECTIONS
    assert not pane.degraded
    assert pane.chunks == []


def test_a_missing_original_is_stated_even_on_a_complete_document(conn, tmp_path):
    """Saying it only on the degraded page would hide it on exactly the
    documents that look complete."""
    _doc(conn, "d_full", filepath=str(tmp_path / "deleted-upload.pdf"))
    _section(conn, "d_full", 2)

    pane = _pane(conn, "d_full")
    assert pane.basis == BASIS_SECTIONS
    assert "original_absent" in _codes(pane)


def test_both_absences_are_named_separately(conn, tmp_path):
    """"which of the two is missing" — two absences, two entries, never one
    merged sentence."""
    _doc(conn, "d_both", filepath=str(tmp_path / "gone.pdf"))
    _chunk(conn, "chunk-0", "d_both", 0)

    codes = _codes(_pane(conn, "d_both"))
    assert "no_sections" in codes
    assert "original_absent" in codes


def test_a_retained_original_states_nothing(conn, tmp_path):
    """An empty limits list is a MEASUREMENT. A standing "no known limitations"
    banner would be a claim."""
    kept = tmp_path / "kept.pdf"
    kept.write_bytes(b"%PDF-1.4\n")
    _doc(conn, "d_ok", original_path=str(kept), original_sha256="x",
         original_retained_at="2026-09-08T00:00:00Z")
    _section(conn, "d_ok", 1)

    pane = _pane(conn, "d_ok")
    assert pane.limits == []


def test_an_in_canvas_document_is_no_source_not_a_finding(conn):
    """15 of the 55 were generated in the canvas — there was never an upload to
    retain. Filing them as findings buries the real ones."""
    _doc(conn, "d_gen", filepath=None)
    _section(conn, "d_gen", 1)

    pane = _pane(conn, "d_gen")
    assert "original_no_source" in _codes(pane)
    assert next(x.severity for x in pane.limits if x.code == "original_no_source") == "info"


def test_an_unmigrated_board_says_unmeasurable_not_absent(tmp_path):
    """The live PG board has NOT run dwr-fid-01's migration. Reporting 43
    documents as having lost their upload when the column does not exist would
    be a fabricated finding on the scale of the real one."""
    c = connect(str(tmp_path / "old.db"))
    c.execute("CREATE TABLE dic_documents (doc_id TEXT PRIMARY KEY, filepath TEXT)")
    c.execute("CREATE TABLE dic_sections (section_id TEXT, doc_id TEXT)")
    c.execute("CREATE TABLE dic_chunk_links (link_id TEXT, doc_id TEXT, rag_chunk_id TEXT, chunk_index INTEGER, page INTEGER, section TEXT)")
    c.execute("CREATE TABLE rag_chunks (id TEXT, content TEXT, source_type TEXT, source_id TEXT, source_table TEXT, chunk_index INTEGER)")
    c.execute("INSERT INTO dic_documents (doc_id, filepath) VALUES (%s,%s)", ("d_old", "/gone.pdf"))
    c.commit()
    try:
        pane = _pane(c, "d_old")
        assert "original_unmeasurable" in _codes(pane)
        assert "original_absent" not in _codes(pane)
        assert pane.original.get("columns_present") is False
    finally:
        c.close()


# ── unmeasurable is never a clean bill of health ─────────────────────────────

def test_an_unreadable_table_is_unmeasurable_never_no_text(tmp_path):
    """`_safe_rows` in blueprint.py turns a broken query into an EMPTY LIST,
    which is exactly how "the table is missing" reads as "this document has no
    chunks". This module reports its own failures instead."""
    c = connect(str(tmp_path / "broken.db"))
    c.execute("CREATE TABLE dic_documents (doc_id TEXT PRIMARY KEY, filepath TEXT)")
    c.execute("CREATE TABLE dic_sections (section_id TEXT, doc_id TEXT)")
    c.execute("INSERT INTO dic_documents (doc_id, filepath) VALUES (%s,%s)", ("d", None))
    c.commit()  # dic_chunk_links and rag_chunks do not exist at all
    try:
        pane = _pane(c, "d")
        assert pane.basis == BASIS_UNMEASURABLE
        assert pane.errors, "a failed read reported no error"
        assert "unmeasurable" in _codes(pane)
        assert "no_text" not in _codes(pane)
    finally:
        c.close()


def test_a_truncated_pane_says_it_truncated(conn, monkeypatch):
    from tools.document_intelligence import reading_pane as rp

    monkeypatch.setattr(rp, "MAX_PANE_CHUNKS", 2)
    _doc(conn, "d_big")
    for i in range(5):
        _chunk(conn, f"chunk-{i}", "d_big", i)

    pane = rp.build_pane(conn, "d_big")
    assert len(pane.chunks) == 2
    assert pane.chunks_truncated == 3
    assert "chunks_truncated" in _codes(pane)


# ── structural ───────────────────────────────────────────────────────────────

def test_the_original_verdict_is_imported_never_re_derived():
    """Two spellings of "is the upload still there" is how one surface starts
    disagreeing with another. A behavioural test would still pass for a future
    edit that re-implemented the verdict and happened to agree today."""
    src = pathlib.Path("tools/document_intelligence/reading_pane.py").read_text(encoding="utf-8")
    tree = ast.parse(src)
    assert "originals" in src and "original_verdict" in src
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name in {"original_verdict", "_original_verdict"}:
            raise AssertionError("reading_pane re-implements the retention verdict")
    # and it never reads a retention column itself. Asserted against the strings
    # actually handed to a QUERY, not against the file's prose: the docstring
    # NAMES those columns to explain the unmeasurable case, and a test that
    # failed on a reworded sentence is one people learn to weaken.
    def _query_strings(t):
        for n in ast.walk(t):
            if not isinstance(n, ast.Call):
                continue
            name = n.func.attr if isinstance(n.func, ast.Attribute) else getattr(n.func, "id", "")
            if name not in {"execute", "_rows", "executemany"}:
                continue
            for a in n.args:
                if isinstance(a, ast.Constant) and isinstance(a.value, str):
                    yield a.value
                elif isinstance(a, ast.JoinedStr):
                    yield "".join(
                        v.value for v in a.values
                        if isinstance(v, ast.Constant) and isinstance(v.value, str)
                    )

    for sql in _query_strings(tree):
        assert "original_" not in sql.lower(), \
            f"reading_pane queries a retention column itself instead of asking originals: {sql!r}"


def test_every_basis_is_declared():
    from tools.document_intelligence import reading_pane as rp

    assert set(rp.RENDER_BASES) == {
        BASIS_SECTIONS, BASIS_CHUNKS, BASIS_CHUNKS_MISSING, BASIS_NO_TEXT, BASIS_UNMEASURABLE
    }


def test_the_page_renders_the_pane_and_no_longer_prints_the_old_line():
    """Both spellings — the repo template and the packaged mirror the wheel
    reads — or a `pip install`ed dashboard keeps the old one-liner."""
    for base in ("tools", "icdev/tools"):
        page = pathlib.Path(f"{base}/dashboard/templates/document_intelligence/doc_detail.html")
        html = page.read_text(encoding="utf-8")
        assert "_reading_pane.html" in html, f"{page} does not include the reading pane"
        assert "No sections yet." not in html, f"{page} still prints the undifferentiated empty state"
        for partial in ("_reading_pane.html", "_reading_limits.html"):
            assert pathlib.Path(
                f"{base}/dashboard/templates/document_intelligence/{partial}"
            ).is_file(), f"{base} is missing {partial}"


def test_the_limits_band_has_no_standing_reassurance():
    """A band that printed "no known limitations" over an unmeasured document
    is the perfect-score-over-an-empty-denominator defect in prose."""
    html = pathlib.Path(
        "tools/dashboard/templates/document_intelligence/_reading_limits.html"
    ).read_text(encoding="utf-8")
    assert "{% if reading_pane and reading_pane.limits %}" in html
    # The comment blocks explain the rule and render nothing, so the assertion
    # is made against what a reader actually sees.
    rendered = re.sub(r"\{#.*?#\}", "", html, flags=re.S).lower()
    for phrase in ("no known limitation", "no issues", "all good", "complete document"):
        assert phrase not in rendered


def test_the_anchor_prefix_is_not_doubled(conn):
    """Live rag ids are already spelled `chunk-<hex>`, and `#chunk-chunk-6e63…`
    is a URL somebody has to paste."""
    _doc(conn, "d_pfx")
    _chunk(conn, "chunk-6e637937b060", "d_pfx", 0)
    _link(conn, "d_pfx", "chunk-6e637937b060", 0)

    assert _pane(conn, "d_pfx").chunks[0].anchor_id == "chunk-6e637937b060"
