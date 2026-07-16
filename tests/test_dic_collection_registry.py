# CUI // SP-CTI
"""DIC collection registry — a document must never be invisible.

Regression cover for the orphaned-document bug: dic_documents.collection_id is
free-text with no FK, every ingest path took it from the caller without creating
the dic_collections row, and the Collections UI enumerates dic_collections. On
the live corpus that hid 49 of 53 documents.
"""

import pytest

from tools.document_intelligence.collection_registry import (
    ensure_collection,
    most_restrictive,
)


@pytest.fixture()
def conn(icdev_db, monkeypatch):
    """A real StorageConnection over the conftest SQLite DB.

    Deliberately NOT raw sqlite3: ensure_collection uses %s placeholders, and a
    raw connection bypasses storage.translate_sql — the test would then pass (or
    fail) for reasons unrelated to the code under test.
    """
    monkeypatch.setenv("ICDEV_DB_PATH", str(icdev_db))
    from tools.db.storage import get_connection

    conn = get_connection(db_path=str(icdev_db))
    cur = conn.cursor()
    cur.execute(
        """CREATE TABLE IF NOT EXISTS dic_collections (
               collection_id TEXT PRIMARY KEY,
               name TEXT NOT NULL,
               description TEXT DEFAULT '',
               owner_id TEXT DEFAULT '',
               retention_days INTEGER DEFAULT 90,
               classification TEXT DEFAULT 'CUI',
               tenant_id TEXT DEFAULT 'default',
               created_at TEXT,
               review_interval_days INTEGER DEFAULT 90
           )"""
    )
    cur.execute(
        """CREATE TABLE IF NOT EXISTS dic_documents (
               doc_id TEXT PRIMARY KEY,
               collection_id TEXT NOT NULL,
               title TEXT,
               tenant_id TEXT,
               classification TEXT
           )"""
    )
    conn.commit()
    yield conn
    conn.close()


def _collections(conn):
    cur = conn.cursor()
    cur.execute("SELECT collection_id FROM dic_collections")
    return {(list(r.values())[0] if isinstance(r, dict) else r[0]) for r in cur.fetchall()}


class TestEnsureCollection:
    def test_creates_missing_collection(self, conn):
        assert ensure_collection(conn, "isp-peering-demo") is True
        assert "isp-peering-demo" in _collections(conn)

    def test_is_idempotent(self, conn):
        ensure_collection(conn, "default")
        ensure_collection(conn, "default")
        cur = conn.cursor()
        cur.execute("SELECT COUNT(*) FROM dic_collections WHERE collection_id='default'")
        row = cur.fetchone()
        assert (list(row.values())[0] if isinstance(row, dict) else row[0]) == 1

    def test_does_not_clobber_an_existing_collection(self, conn):
        """A real collection created via the UI must keep its name."""
        conn.cursor().execute(
            "INSERT INTO dic_collections (collection_id, name) VALUES (%s, %s)",
            ("d92716e5c128623f0e9fd1b1", "Network Standards"),
        )
        ensure_collection(conn, "d92716e5c128623f0e9fd1b1", name="WRONG")
        cur = conn.cursor()
        cur.execute(
            "SELECT name FROM dic_collections WHERE collection_id=%s",
            ("d92716e5c128623f0e9fd1b1",),
        )
        row = cur.fetchone()
        assert (list(row.values())[0] if isinstance(row, dict) else row[0]) == "Network Standards"

    def test_names_it_after_the_id_when_no_name_given(self, conn):
        """The caller's own string, not a prettified guess — that's how they
        recognise their documents."""
        ensure_collection(conn, "idr-f160378d")
        cur = conn.cursor()
        cur.execute("SELECT name FROM dic_collections WHERE collection_id=%s", ("idr-f160378d",))
        row = cur.fetchone()
        assert (list(row.values())[0] if isinstance(row, dict) else row[0]) == "idr-f160378d"

    @pytest.mark.parametrize("empty", ["", "   ", None])
    def test_refuses_to_invent_an_id(self, conn, empty):
        """An empty collection_id is the caller's bug. Inventing one here would
        hide it; returning False surfaces it."""
        assert ensure_collection(conn, empty) is False
        assert _collections(conn) == set()

    def test_does_not_commit(self, conn):
        """The caller owns the transaction so the collection and its document
        land together or not at all."""
        ensure_collection(conn, "txn-test")
        conn.rollback()
        assert "txn-test" not in _collections(conn)


class TestClassification:
    """Ranking must be explicit — classification does not sort alphabetically."""

    def test_most_restrictive_wins(self):
        assert most_restrictive("CUI", "SECRET") == "SECRET"
        assert most_restrictive("SECRET", "TOP SECRET") == "TOP SECRET"

    def test_unclassified_never_outranks_secret(self):
        """MAX() over raw text would return 'UNCLASSIFIED' here (U > S) and
        under-mark a collection holding classified documents."""
        assert most_restrictive("SECRET", "UNCLASSIFIED") == "SECRET"
        assert most_restrictive("UNCLASSIFIED", "TOP SECRET") == "TOP SECRET"

    def test_unknown_marking_defaults_to_cui_not_unclassified(self):
        """An unrecognised marking is not evidence that content is releasable."""
        assert most_restrictive("bogus") == "CUI"
        assert most_restrictive(None) == "CUI"
        assert most_restrictive() == "CUI"

    def test_collection_takes_the_documents_classification(self, conn):
        ensure_collection(conn, "classified-coll", classification="SECRET")
        cur = conn.cursor()
        cur.execute(
            "SELECT classification FROM dic_collections WHERE collection_id=%s",
            ("classified-coll",),
        )
        row = cur.fetchone()
        assert (list(row.values())[0] if isinstance(row, dict) else row[0]) == "SECRET"


class TestOrphanRepair:
    """The bug itself: a document whose collection has no row is unreachable."""

    def _orphans(self, conn):
        cur = conn.cursor()
        cur.execute(
            """SELECT COUNT(*) FROM dic_documents d
               LEFT JOIN dic_collections c ON c.collection_id = d.collection_id
               WHERE c.collection_id IS NULL"""
        )
        row = cur.fetchone()
        return list(row.values())[0] if isinstance(row, dict) else row[0]

    def test_document_written_without_ensure_is_orphaned(self, conn):
        """Characterises the pre-fix behaviour this module exists to prevent."""
        conn.cursor().execute(
            "INSERT INTO dic_documents (doc_id, collection_id, title) VALUES (%s,%s,%s)",
            ("doc-1", "default", "Network Architecture"),
        )
        assert self._orphans(conn) == 1

    def test_ensure_before_insert_leaves_no_orphan(self, conn):
        ensure_collection(conn, "default")
        conn.cursor().execute(
            "INSERT INTO dic_documents (doc_id, collection_id, title) VALUES (%s,%s,%s)",
            ("doc-1", "default", "Network Architecture"),
        )
        assert self._orphans(conn) == 0
