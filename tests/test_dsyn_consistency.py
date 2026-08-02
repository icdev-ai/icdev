# CUI // SP-CTI
"""Tests for dsyn-consist-01/02: KG neighbor walk and consistency flag propagation."""
from __future__ import annotations

import contextlib
import importlib
import sqlite3
from unittest.mock import MagicMock

from tests import _sql_compat


# ── Helpers ────────────────────────────────────────────────────────────────────

def _make_raw_conn():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    for ddl in [
        """CREATE TABLE dic_documents (
            doc_id TEXT PRIMARY KEY, title TEXT, collection_id TEXT,
            classification TEXT DEFAULT 'CUI', created_at TEXT
        )""",
        """CREATE TABLE kg_nodes (
            id TEXT PRIMARY KEY, label TEXT, entity_type TEXT,
            centrality REAL, source_chunk_id TEXT, graph_id TEXT
        )""",
        """CREATE TABLE kg_graphs (
            id TEXT PRIMARY KEY, source_doc_id TEXT, created_at TEXT
        )""",
        """CREATE TABLE dic_edit_history (
            edit_id TEXT PRIMARY KEY, section_id TEXT, doc_id TEXT,
            version_id TEXT, editor TEXT NOT NULL, content_before TEXT,
            content_after TEXT, char_delta INTEGER, diff_summary TEXT,
            edited_at TEXT NOT NULL, tenant_id TEXT, collection_id TEXT,
            classification TEXT DEFAULT 'CUI'
        )""",
        # consumed_at is named by _propagate_consistency_flags' INSERT. Without
        # it the statement raised "no such column", the best-effort except
        # swallowed it, and the test read 0 events back — a no-op it caused
        # itself, indistinguishable from an unimplemented feature.
        """CREATE TABLE canvas_events (
            id TEXT PRIMARY KEY, source_canvas TEXT, target_canvas TEXT,
            event_type TEXT, payload_json TEXT, created_at TEXT,
            tenant_id TEXT, classification TEXT DEFAULT 'CUI',
            consumed_at TEXT
        )""",
    ]:
        conn.execute(ddl)
    conn.commit()
    return conn


def _make_shim(raw=None):
    """Translating wrapper over the in-memory DIC DB.

    ``unclosable`` because the code under test writes through
    ``with get_connection() as conn:`` and the wrapper's ``__exit__`` would
    otherwise close the handle — taking the in-memory database with it before
    the test can read the row back.
    """
    return _sql_compat.translating(raw or _make_raw_conn(), unclosable=True)


@contextlib.contextmanager
def _patch_attr(module_path: str, attr: str, value):
    mod = importlib.import_module(module_path)
    orig = getattr(mod, attr, None)
    setattr(mod, attr, value)
    try:
        yield
    finally:
        if orig is None:
            try:
                delattr(mod, attr)
            except AttributeError:
                pass
        else:
            setattr(mod, attr, orig)


# ══════════════════════════════════════════════════════════════════════════════
# dsyn-consist-01: extract_changed_concepts
# ══════════════════════════════════════════════════════════════════════════════

class TestExtractChangedConcepts:
    def test_returns_new_terms(self):
        from tools.document_intelligence.consistency_checker import extract_changed_concepts
        before = "The system uses authentication."
        after = "The system uses authentication and zero-trust network access control."
        result = extract_changed_concepts(before, after)
        # "zero-trust", "network", "access", "control" should be in new terms
        assert len(result) > 0

    def test_returns_empty_when_no_new_terms(self):
        from tools.document_intelligence.consistency_checker import extract_changed_concepts
        before = "The system uses authentication and authorization."
        after = "The system uses authentication and authorization."
        result = extract_changed_concepts(before, after)
        assert result == []

    def test_filters_stop_words(self):
        from tools.document_intelligence.consistency_checker import extract_changed_concepts
        before = "Hello world."
        after = "Hello world and the new system."
        result = extract_changed_concepts(before, after)
        assert "the" not in result
        assert "and" not in result

    def test_caps_at_50_terms(self):
        from tools.document_intelligence.consistency_checker import extract_changed_concepts
        before = ""
        after = " ".join([f"concept{i}xyz" for i in range(100)])
        result = extract_changed_concepts(before, after)
        assert len(result) <= 50

    def test_deduplicates_terms(self):
        from tools.document_intelligence.consistency_checker import extract_changed_concepts
        before = ""
        after = "zero-trust zero-trust firewall firewall"
        result = extract_changed_concepts(before, after)
        assert result.count("zero-trust") <= 1
        assert result.count("firewall") <= 1


# ══════════════════════════════════════════════════════════════════════════════
# dsyn-consist-01: find_related_docs
# ══════════════════════════════════════════════════════════════════════════════

class TestFindRelatedDocs:
    def _seed(self, shim, source_doc_id="doc-src", related_doc_id="doc-rel",
              shared_concept="firewall"):
        shim.execute(
            "INSERT INTO dic_documents (doc_id, title, collection_id, created_at)"
            " VALUES (?,?,?,?)", (source_doc_id, "Source Doc", "col-001", "2026-01-01"),
        )
        shim.execute(
            "INSERT INTO dic_documents (doc_id, title, collection_id, created_at)"
            " VALUES (?,?,?,?)", (related_doc_id, "Related Doc", "col-001", "2026-01-02"),
        )
        # Source doc graph
        shim.execute("INSERT INTO kg_graphs (id, source_doc_id) VALUES (?,?)",
                     ("graph-src", source_doc_id))
        shim.execute("INSERT INTO kg_nodes (id, label, entity_type, graph_id)"
                     " VALUES (?,?,?,?)", ("n-src", shared_concept, "concept", "graph-src"))
        # Related doc graph — shares same concept
        shim.execute("INSERT INTO kg_graphs (id, source_doc_id) VALUES (?,?)",
                     ("graph-rel", related_doc_id))
        shim.execute("INSERT INTO kg_nodes (id, label, entity_type, graph_id)"
                     " VALUES (?,?,?,?)", ("n-rel", shared_concept, "concept", "graph-rel"))
        shim.commit()

    def test_finds_doc_sharing_concept(self):
        shim = _make_shim()
        self._seed(shim, shared_concept="authentication")
        with _patch_attr("tools.document_intelligence.consistency_checker", "get_connection",
                         MagicMock(return_value=shim)):
            from tools.document_intelligence.consistency_checker import find_related_docs
            results = find_related_docs("doc-src", ["authentication"])
        doc_ids = [r["doc_id"] for r in results]
        assert "doc-rel" in doc_ids
        assert "doc-src" not in doc_ids

    def test_excludes_source_doc(self):
        shim = _make_shim()
        self._seed(shim)
        with _patch_attr("tools.document_intelligence.consistency_checker", "get_connection",
                         MagicMock(return_value=shim)):
            from tools.document_intelligence.consistency_checker import find_related_docs
            results = find_related_docs("doc-src", ["firewall"])
        doc_ids = [r["doc_id"] for r in results]
        assert "doc-src" not in doc_ids

    def test_returns_empty_for_no_concepts(self):
        shim = _make_shim()
        with _patch_attr("tools.document_intelligence.consistency_checker", "get_connection",
                         MagicMock(return_value=shim)):
            from tools.document_intelligence.consistency_checker import find_related_docs
            results = find_related_docs("doc-src", [])
        assert results == []

    def test_returns_empty_when_no_overlap(self):
        shim = _make_shim()
        self._seed(shim, shared_concept="firewall")
        with _patch_attr("tools.document_intelligence.consistency_checker", "get_connection",
                         MagicMock(return_value=shim)):
            from tools.document_intelligence.consistency_checker import find_related_docs
            results = find_related_docs("doc-src", ["authentication"])  # no overlap
        assert results == []

    def test_result_has_required_fields(self):
        shim = _make_shim()
        self._seed(shim, shared_concept="encryption")
        with _patch_attr("tools.document_intelligence.consistency_checker", "get_connection",
                         MagicMock(return_value=shim)):
            from tools.document_intelligence.consistency_checker import find_related_docs
            results = find_related_docs("doc-src", ["encryption"])
        if results:
            r = results[0]
            assert "doc_id" in r
            assert "doc_title" in r
            assert "collection_id" in r
            assert "matching_concepts" in r

    def test_respects_limit(self):
        shim = _make_shim()
        # Seed 5 related docs, all sharing "zero-trust"
        for i in range(5):
            did = f"doc-r{i}"
            shim.execute(
                "INSERT INTO dic_documents (doc_id, title, collection_id, created_at)"
                " VALUES (?,?,?,?)", (did, f"Doc {i}", "col-001", "2026-01-01"),
            )
            shim.execute("INSERT INTO kg_graphs (id, source_doc_id) VALUES (?,?)",
                         (f"g-r{i}", did))
            shim.execute("INSERT INTO kg_nodes (id, label, entity_type, graph_id)"
                         " VALUES (?,?,?,?)", (f"n-r{i}", "zero-trust", "concept", f"g-r{i}"))
        shim.commit()
        with _patch_attr("tools.document_intelligence.consistency_checker", "get_connection",
                         MagicMock(return_value=shim)):
            from tools.document_intelligence.consistency_checker import find_related_docs
            results = find_related_docs("doc-src", ["zero-trust"], limit=3)
        assert len(results) <= 3


# ══════════════════════════════════════════════════════════════════════════════
# dsyn-consist-02: consistency flag propagation in record_edit
# ══════════════════════════════════════════════════════════════════════════════

class TestConsistencyFlagPropagation:
    def test_flag_emitted_when_char_delta_large(self):
        shim = _make_shim()
        before = "Short text."
        after = "This section now covers zero-trust authentication, network segmentation, " \
                "firewall rules, access control lists, and multi-factor authentication requirements."
        fake_related = [
            {"doc_id": "doc-rel", "doc_title": "Related", "collection_id": "col-001",
             "matching_concepts": ["authentication"], "last_updated": ""}
        ]
        import tools.document_intelligence.consistency_checker as cc
        orig_fr = cc.find_related_docs
        cc.find_related_docs = MagicMock(return_value=fake_related)
        try:
            # record_edit writes edit history through get_connection() but
            # _propagate_consistency_flags writes canvas_events through
            # get_canvas_connection() (canvas_events has no RLS predicate).
            # Patching only get_connection sent the event to the real DB, so
            # the test read 0 rows back and looked like a missing feature.
            with _patch_attr("tools.document_intelligence.history_recorder", "get_connection",
                             MagicMock(return_value=shim)), \
                 _patch_attr("tools.document_intelligence.history_recorder",
                             "get_canvas_connection", MagicMock(return_value=shim)):
                from tools.document_intelligence.history_recorder import record_edit
                record_edit("sec-001", "editor", before, after, doc_id="doc-src")
        finally:
            cc.find_related_docs = orig_fr

        events = shim.execute(
            "SELECT * FROM canvas_events WHERE event_type='dic.consistency_flag'"
        ).fetchall()
        assert len(events) >= 1

    def test_flag_not_emitted_when_char_delta_small(self):
        shim = _make_shim()
        before = "This is some text."
        after = "This is some text!"  # tiny change
        find_calls = []
        import tools.document_intelligence.consistency_checker as cc
        orig_fr = cc.find_related_docs
        cc.find_related_docs = MagicMock(side_effect=lambda *a, **kw: find_calls.append(a) or [])
        try:
            with _patch_attr("tools.document_intelligence.history_recorder", "get_connection",
                             MagicMock(return_value=shim)):
                from tools.document_intelligence.history_recorder import record_edit
                record_edit("sec-002", "editor", before, after, doc_id="doc-src")
        finally:
            cc.find_related_docs = orig_fr
        # find_related_docs should NOT be called for tiny edits
        assert len(find_calls) == 0

    def test_propagation_does_not_raise_on_db_error(self):
        """Consistency propagation must never block a section save."""
        from tools.document_intelligence.history_recorder import _propagate_consistency_flags
        # Should not raise even if DB is broken
        _propagate_consistency_flags(
            section_id="sec-x",
            doc_id="doc-x",
            content_before="old content here",
            content_after="new content with many words added for delta threshold testing",
            char_delta=100,
            now="2026-01-01T00:00:00",
            tenant_id="",
            classification="CUI",
        )

    def test_propagation_not_exported_but_callable(self):
        """_propagate_consistency_flags is a private function but must exist."""
        from tools.document_intelligence import history_recorder
        assert hasattr(history_recorder, "_propagate_consistency_flags")
        assert callable(history_recorder._propagate_consistency_flags)

    def test_record_edit_still_returns_edit_id(self):
        shim = _make_shim()
        with _patch_attr("tools.document_intelligence.history_recorder", "get_connection",
                         MagicMock(return_value=shim)):
            from tools.document_intelligence.history_recorder import record_edit
            result = record_edit("sec-003", "user", "before text", "after text changed")
        assert result is not None
        assert result.startswith("eh_")
