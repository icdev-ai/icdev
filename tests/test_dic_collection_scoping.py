#!/usr/bin/env python3
"""Collection-scoped DIC search must not silently return nothing. CUI // SP-CTI.

`DICSearchEngine.search()` scopes twice, against two different columns:

  * `_rag_search` passes `project_id=collection_id` to the retriever — this is
    correct and does restrict the candidate set; and
  * the loop in `search()` then re-checks `_chunk_meta(...)["collection_id"]`
    and drops anything that does not match.

The second check used to read `dic_chunk_links` ALONE. That table is written
only by `ingest_orchestrator.ingest_file`, so chunks ingested by any other path
— or before linking existed — resolved to `""` and were dropped by
`if collection_id and col_id != collection_id`.

Measured on the live corpus when this was found: 168 of 559 chunks linked. A
scoped query against a 236-chunk collection returned **zero results** while the
retriever had correctly returned that collection's chunks. Nothing errored; it
reads exactly like poor recall, which is why it survived.

These are unit tests over the resolution logic with a stub connection: the
defect was in which column is authoritative, not in SQL execution, and pinning
it without a live corpus is what keeps it pinned.
"""
from __future__ import annotations

import pytest

from tools.document_intelligence import search_engine as se


class _Row(dict):
    """psycopg2 DictRow stand-in — `hasattr(row, "keys")` is the branch tested."""


class _StubConn:
    """Serves one canned row per table, keyed off the SQL text.

    `_chunk_meta` issues exactly two SELECTs (rag_chunks, then dic_chunk_links)
    and wraps each in its own bare `except`, so a stub that raises for one table
    also exercises the not-found path.
    """

    def __init__(self, chunk_row=None, link_row=None):
        self._chunk_row = chunk_row
        self._link_row = link_row

    def execute(self, sql, params=None):
        row = self._link_row if "dic_chunk_links" in sql else self._chunk_row
        return _StubCursor(row)


class _StubCursor:
    def __init__(self, row):
        self._row = row

    def fetchone(self):
        return self._row


def _chunk(project_id="", content="body text", content_hash="abc"):
    return _Row(content=content, content_hash=content_hash, project_id=project_id)


def _link(collection_id="", page=3, section="II", doc_id="doc-1"):
    return _Row(page=page, section=section, doc_id=doc_id, collection_id=collection_id)


# --------------------------------------------------------------------------- #
# The regression itself
# --------------------------------------------------------------------------- #


def test_unlinked_chunk_resolves_its_collection_from_project_id():
    """The bug. No link row, so the old code returned "" and the caller dropped it.

    `rag_chunks.project_id` is the column the retriever ALREADY filtered on, so
    using it here makes the post-filter agree with the query that produced the
    candidate rather than contradicting it.
    """
    meta = se._chunk_meta(_StubConn(chunk_row=_chunk(project_id="coll-a")), "c1")
    assert meta["collection_id"] == "coll-a"


def test_scoped_filter_keeps_an_unlinked_in_collection_chunk():
    """Same defect stated as the caller sees it — the drop condition itself."""
    meta = se._chunk_meta(_StubConn(chunk_row=_chunk(project_id="coll-a")), "c1")
    collection_id = "coll-a"
    dropped = bool(collection_id) and meta["collection_id"] != collection_id
    assert not dropped, "in-collection chunk dropped by the post-truncation filter"


def test_out_of_collection_chunk_is_still_dropped():
    """The negative case — the fix must not turn scoping into a no-op.

    Without this, "scoped search returns results again" could be satisfied by
    simply not filtering, which would leak other tenants' collections into a
    scoped answer.
    """
    meta = se._chunk_meta(_StubConn(chunk_row=_chunk(project_id="coll-b")), "c1")
    assert meta["collection_id"] != "coll-a"


# --------------------------------------------------------------------------- #
# Precedence between the two sources
# --------------------------------------------------------------------------- #


def test_link_row_wins_when_it_names_a_collection():
    """`dic_chunk_links` stays authoritative where it is populated.

    It is the DIC-specific mapping and can legitimately differ from the RAG
    project id; the project_id read is a fallback, not a replacement.
    """
    conn = _StubConn(chunk_row=_chunk(project_id="proj"), link_row=_link(collection_id="coll-link"))
    assert se._chunk_meta(conn, "c1")["collection_id"] == "coll-link"


@pytest.mark.parametrize("empty", ["", None])
def test_empty_link_collection_does_not_erase_project_id(empty):
    """A link row with no collection must not re-create the drop-everything bug.

    `result.update(...)` previously wrote `row2["collection_id"] or ""`
    unconditionally, so a half-populated link was worse than no link at all.
    """
    conn = _StubConn(chunk_row=_chunk(project_id="proj"), link_row=_link(collection_id=empty))
    assert se._chunk_meta(conn, "c1")["collection_id"] == "proj"


def test_link_row_still_supplies_page_and_section():
    """Guard the fields the citation UI needs — this edit touched that update()."""
    conn = _StubConn(chunk_row=_chunk(project_id="proj"), link_row=_link(collection_id="c"))
    meta = se._chunk_meta(conn, "c1")
    assert meta["page"] == 3 and meta["section"] == "II" and meta["doc_id"] == "doc-1"


def test_no_rows_at_all_is_safe():
    meta = se._chunk_meta(_StubConn(), "c1")
    assert meta["collection_id"] == "" and meta["page"] == 0


# --------------------------------------------------------------------------- #
# The BM25 fallback could not execute on the primary backend
# --------------------------------------------------------------------------- #


def test_bm25_fallback_uses_one_placeholder_style():
    """`content LIKE ?` mixed with `%s` for LIMIT raised on psycopg2 every time.

    The outer `except` then returned `[]`, so the keyword safety net was dead on
    PostgreSQL — the primary backend — for every query that reached it, without
    ever surfacing an error.
    """
    import inspect

    src = inspect.getsource(se.DICSearchEngine._bm25_fallback)
    assert "LIKE ?" not in src, "bare ? placeholder cannot execute on psycopg2"
    assert "LIKE %s" in src
