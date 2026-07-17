# CUI // SP-CTI
"""sharepoint_documents.content_hash must move when the document moves.

It was hashing only the server-relative PATH, which is stable across edits — so
the "content hash" never changed when a file changed, defeating any change
detection built on the column. This is a metadata list crawl (no content is
downloaded), so the token folds in SharePoint's Modified timestamp and size.
"""
from __future__ import annotations

from tools.sharepoint.ingest import _document_change_hash


PATH = "/sites/x/Shared Documents/report.docx"


class TestChangeHashMovesWithTheDocument:
    def test_modified_timestamp_change_changes_the_hash(self):
        """The bug: an edited file keeps its path, so the old path-only hash was
        identical before and after the edit."""
        before = _document_change_hash(PATH, 1000, "2026-01-01T00:00:00Z")
        after = _document_change_hash(PATH, 1000, "2026-06-01T00:00:00Z")
        assert before != after, "edit (new Modified) must change the token"

    def test_size_change_changes_the_hash(self):
        a = _document_change_hash(PATH, 1000, "2026-01-01T00:00:00Z")
        b = _document_change_hash(PATH, 2048, "2026-01-01T00:00:00Z")
        assert a != b

    def test_path_alone_no_longer_determines_the_hash(self):
        """Guard against regressing to the path-only hash."""
        import hashlib
        path_only = hashlib.sha256(PATH.encode("utf-8")).hexdigest()[:16]
        assert _document_change_hash(PATH, 1000, "2026-01-01T00:00:00Z") != path_only


class TestChangeHashIsStableWhenNothingChanged:
    def test_same_inputs_same_hash(self):
        """An unchanged document must produce the same token across crawls, or
        every crawl looks like a change."""
        args = (PATH, 1234, "2026-03-03T12:00:00Z")
        assert _document_change_hash(*args) == _document_change_hash(*args)

    def test_missing_modified_is_tolerated(self):
        """SharePoint may omit Modified; the hash must still be deterministic and
        not raise."""
        assert _document_change_hash(PATH, 0, None) == _document_change_hash(PATH, 0, None)


class TestDistinctDocumentsDiffer:
    def test_different_paths_differ(self):
        a = _document_change_hash("/a/x.docx", 10, "t")
        b = _document_change_hash("/b/x.docx", 10, "t")
        assert a != b
