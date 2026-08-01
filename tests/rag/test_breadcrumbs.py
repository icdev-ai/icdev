# CUI // SP-CTI
"""Deterministic document-position breadcrumbs (oss-chunk-02).

A chunk reading "shall be documented in the System Security Plan" is
positionally orphaned — nothing says which document, section, or page. These
tests pin the derivation, and the two properties that make it worth having
alongside the LLM prefix in contextual_retrieval: it is DETERMINISTIC, and the
values land somewhere filterable and citable.
"""
from __future__ import annotations


from tools.rag import breadcrumbs as bc

SSP = """--- Page 46 ---
# System Security Plan

## 3 Access Control

### 3.4 Account Management

The organization shall document account types in the SSP.

--- Page 47 ---
### 3.5 Least Privilege

Employ the principle of least privilege.
"""


# ── Heading parsing ──────────────────────────────────────────────────────────


def test_atx_and_numbered_headings_are_both_found():
    hs = bc.parse_headings(SSP)
    titles = [h.text for h in hs]
    assert "System Security Plan" in titles
    assert "Account Management" in titles
    assert "Least Privilege" in titles


def test_numbered_heading_depth_comes_from_its_number():
    """`3.4.1` is level 3 regardless of how many hashes precede it.

    Exported compliance documents frequently make every heading `##`, so ATX
    depth alone would flatten the whole hierarchy.
    """
    hs = {h.text: h for h in bc.parse_headings("## 3 A\n## 3.4 B\n## 3.4.1 C\n")}
    # +1 so level 1 stays free for an un-numbered document title, which would
    # otherwise be popped by a top-level numbered section.
    assert hs["A"].level == 2
    assert hs["B"].level == 3
    assert hs["C"].level == 4


def test_flat_atx_document_keeps_its_hierarchy():
    """The failure this exists to prevent.

    Exported compliance documents make every heading `##`. Taking ATX depth
    there collapses 3 / 3.4 / 3.4.1 to one level, each popping the last, and the
    trail degrades to a single leaf — losing exactly the hierarchy this module
    is for. Before the fix this returned just "3.4.1 Types".
    """
    flat = "## 3 Access Control\n## 3.4 Account Management\n## 3.4.1 Types\nbody\n"
    section = bc.position_for(flat, flat.index("body")).section
    assert "3 Access Control" in section
    assert "3.4 Account Management" in section
    assert "3.4.1 Types" in section


def test_a_long_line_starting_with_a_number_is_not_a_heading():
    """Prose beginning "1. The organization shall ..." is a list item."""
    text = "1. " + ("The organization shall implement controls. " * 8)
    assert bc.parse_headings(text) == []


def test_prose_without_headings_yields_none():
    assert bc.parse_headings("just a paragraph\nand another\n") == []


# ── Trail resolution ─────────────────────────────────────────────────────────


def test_trail_reflects_the_section_the_text_sits_in():
    off = SSP.index("The organization shall document")
    pos = bc.position_for(SSP, off)
    assert pos.section == (
        "System Security Plan > 3 Access Control > 3.4 Account Management"
    )


def test_a_sibling_heading_replaces_rather_than_nests():
    """3.5 must not appear underneath 3.4."""
    off = SSP.index("Employ the principle")
    pos = bc.position_for(SSP, off)
    assert "3.5 Least Privilege" in pos.section
    assert "3.4 Account Management" not in pos.section


def test_text_before_any_heading_has_an_empty_section():
    pos = bc.position_for("intro prose\n\n# Later\nbody\n", 3)
    assert pos.section == ""


def test_trail_is_depth_capped():
    doc = "".join(f"{'#' * (i + 1)} L{i}\n" for i in range(1, 7)) + "body\n"
    pos = bc.position_for(doc, doc.index("body"))
    assert pos.section.count(bc.SEPARATOR) < bc.MAX_DEPTH


# ── Page tracking ────────────────────────────────────────────────────────────


def test_page_markers_from_the_pdf_extractors_are_recognised():
    """Page numbers survive extraction rather than being re-derived."""
    assert bc.page_breaks(SSP) == [(46, 0), (47, SSP.index("--- Page 47 ---"))]


def test_page_tracks_across_a_break():
    breaks = bc.page_breaks(SSP)
    assert bc.page_at(breaks, SSP.index("The organization shall")) == 46
    assert bc.page_at(breaks, SSP.index("Employ the principle")) == 47


def test_document_without_page_markers_reports_none():
    assert bc.page_at(bc.page_breaks("no markers here"), 5) is None


# ── Prefix rendering ─────────────────────────────────────────────────────────


def test_prefix_carries_document_section_and_page():
    off = SSP.index("The organization shall document")
    pos = bc.position_for(SSP, off, page=bc.page_at(bc.page_breaks(SSP), off))
    prefix = bc.breadcrumb_prefix(pos, doc_title="SSP")
    assert prefix.startswith("[") and prefix.endswith("]")
    assert "SSP" in prefix
    assert "3.4 Account Management" in prefix
    assert "p. 46" in prefix


def test_unknown_position_yields_no_prefix_rather_than_a_placeholder():
    """"unknown > unknown" would be noise in every embedding, and would make an
    un-positioned chunk look positioned."""
    assert bc.breadcrumb_prefix(bc.ChunkPosition()) == ""
    assert bc.apply_breadcrumb("body", bc.ChunkPosition()) == "body"


def test_apply_prepends_without_touching_the_content():
    """Stored/cited content must stay exactly what the document said."""
    body = "The organization shall document account types."
    pos = bc.ChunkPosition(section="3.4 Account Management", page=46)
    out = bc.apply_breadcrumb(body, pos, doc_title="SSP")
    assert out.endswith(body), "original content must survive verbatim"
    assert out.count(body) == 1


def test_breadcrumbs_are_deterministic():
    """The property that distinguishes this from the LLM prefix."""
    off = SSP.index("The organization shall document")
    runs = {bc.breadcrumb_prefix(bc.position_for(SSP, off), "SSP") for _ in range(5)}
    assert len(runs) == 1


# ── The columns exist to be filtered and cited ───────────────────────────────


def test_migration_adds_all_three_columns_and_the_expansion_index():
    import pathlib

    sql = (
        pathlib.Path(__file__).resolve().parents[2]
        / "tools" / "db" / "migrations" / "296_rag_chunks_position_columns.sql"
    ).read_text(encoding="utf-8")

    for col in ("page", "section", "doc_id"):
        assert f"ADD COLUMN IF NOT EXISTS {col}" in sql, f"{col} not added"
    # section-level expansion = "given a hit, fetch its siblings" = doc_id+section
    assert "idx_rag_chunks_doc_section" in sql


def test_columns_are_nullable_so_existing_rows_stay_valid():
    """Back-filling would mean re-parsing every source document, and a WRONG
    page number is worse than an absent one on a provenance surface."""
    import pathlib

    sql = (
        pathlib.Path(__file__).resolve().parents[2]
        / "tools" / "db" / "migrations" / "296_rag_chunks_position_columns.sql"
    ).read_text(encoding="utf-8")
    for line in sql.splitlines():
        if "ADD COLUMN IF NOT EXISTS" in line:
            assert "NOT NULL" not in line, f"non-nullable column would break existing rows: {line}"
