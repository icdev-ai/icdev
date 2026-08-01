# CUI // SP-CTI
"""End-to-end: a fetched page becomes a citation with provenance behind it.

This is the TRUST invariant oss-cite-01 exists to satisfy — an inline
``[source: https://...]`` marker must resolve to a PERSISTED record of what was
actually served, not just to a URL string someone typed.
"""
from __future__ import annotations

import sqlite3

import pytest

from tools.provenance.citation_types import sqlite_check_clause

BODY = "<html><body>NIST SP 800-53 AC-2 account management</body></html>"


@pytest.fixture
def db(tmp_path, monkeypatch):
    """A throwaway SQLite database carrying both tables.

    The registry CHECK is rendered from the SAME constant the migration uses, so
    this fixture cannot accidentally be more permissive than production.
    """
    path = tmp_path / "cite.db"
    conn = sqlite3.connect(path)
    conn.executescript(
        f"""
        CREATE TABLE source_citation_registry (
            id TEXT PRIMARY KEY,
            citation_type TEXT NOT NULL {sqlite_check_clause()},
            source_table TEXT NOT NULL,
            source_record_id TEXT NOT NULL,
            source_doc TEXT,
            source_hash TEXT NOT NULL,
            anchor_hash TEXT,
            merkle_root TEXT,
            blockchain_tx_id TEXT,
            classification TEXT DEFAULT 'CUI',
            project_id TEXT,
            trust_score REAL DEFAULT 0.0,
            created_at TEXT
        );
        CREATE TABLE web_fetch_provenance (
            id TEXT PRIMARY KEY,
            citation_id TEXT,
            requested_url TEXT NOT NULL,
            final_url TEXT,
            http_status INTEGER,
            content_hash TEXT NOT NULL,
            content_type TEXT,
            content_length INTEGER,
            etag TEXT,
            last_modified TEXT,
            fetched_at TEXT NOT NULL,
            fetcher TEXT,
            classification TEXT DEFAULT 'CUI',
            project_id TEXT,
            tenant_id TEXT DEFAULT '',
            metadata TEXT DEFAULT '{{}}'
        );
        """
    )
    conn.commit()
    conn.close()
    monkeypatch.setenv("ICDEV_STORAGE_BACKEND", "sqlite")
    monkeypatch.setenv("ICDEV_DB_PATH", str(path))
    return path


def test_web_citation_writes_both_halves(db):
    from tools.provenance.registry import register_web_citation

    out = register_web_citation(
        requested_url="https://csrc.nist.gov/pubs/sp/800/53/r5",
        content=BODY,
        final_url="https://csrc.nist.gov/pubs/sp/800/53/r5/final",
        http_status=200,
        content_type="text/html",
        etag='W/"abc123"',
        last_modified="Tue, 01 Jul 2025 00:00:00 GMT",
        fetcher="tools.http.page_extract",
        db_path=db,
    )
    assert out["citation_id"].startswith("scr-")
    assert out["provenance_id"].startswith("wfp-")
    assert len(out["content_hash"]) == 64          # sha256 hex


def test_provenance_records_what_was_actually_served(db):
    from tools.provenance.registry import get_web_provenance, register_web_citation

    out = register_web_citation(
        requested_url="https://example.gov/a",
        content=BODY,
        final_url="https://example.gov/b",          # redirected
        http_status=200,
        etag='W/"v1"',
        db_path=db,
    )
    rows = get_web_provenance(out["citation_id"], db_path=db)
    assert len(rows) == 1
    row = rows[0]
    # The redirect is visible rather than inferred — the cited content is NOT at
    # the URL the citation names.
    assert row["requested_url"] == "https://example.gov/a"
    assert row["final_url"] == "https://example.gov/b"
    assert row["http_status"] == 200
    assert row["etag"] == 'W/"v1"'
    assert row["content_length"] == len(BODY)


def test_refetch_appends_rather_than_overwrites(db):
    """Append-only is what makes evidence drift visible."""
    from tools.provenance.registry import get_web_provenance, register_web_citation

    first = register_web_citation("https://example.gov/x", BODY, db_path=db)
    second = register_web_citation(
        "https://example.gov/x", BODY + "<p>changed</p>", db_path=db
    )
    assert first["content_hash"] != second["content_hash"], "hash must track content"

    # Same URL, two fetches, two citations, each with its own provenance row.
    assert len(get_web_provenance(first["citation_id"], db_path=db)) == 1
    assert len(get_web_provenance(second["citation_id"], db_path=db)) == 1


def test_identical_content_hashes_identically(db):
    """Byte-identical re-fetch is provable without re-reading the body."""
    from tools.provenance.registry import register_web_citation

    a = register_web_citation("https://example.gov/y", BODY, db_path=db)
    b = register_web_citation("https://example.gov/y", BODY, db_path=db)
    assert a["content_hash"] == b["content_hash"]


def test_registry_row_is_citeable_as_web(db):
    from tools.provenance.registry import register_web_citation

    out = register_web_citation("https://example.gov/z", BODY, db_path=db)
    conn = sqlite3.connect(db)
    try:
        row = conn.execute(
            "SELECT citation_type, source_doc, source_hash FROM "
            "source_citation_registry WHERE id = ?",
            (out["citation_id"],),
        ).fetchone()
    finally:
        conn.close()
    assert row is not None, "no registry row — the page is not citeable"
    assert row[0] == "web"
    assert row[1] == "https://example.gov/z"
    assert row[2] == out["content_hash"]
