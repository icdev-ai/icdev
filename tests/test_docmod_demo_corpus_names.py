"""A declared collection is matched by NAME as well as by id (cdh-seed-01 follow-up).

WHAT THE FIRST FIX MISSED, found by measuring the live board rather than by
re-reading the tests that passed. `dic_documents.collection_id` holds an opaque
id; the human-readable label lives in `dic_collections.name`:

    801d27077c1444ddd4864757  ->  'Politics'   (4 docs: constitution.pdf, ArtOfWar.pdf,
                                                sop-refresh-template.md, and
                                                'The First Amendment to the United
                                                States Constitution')
    d92716e5c128623f0e9fd1b1  ->  'test'       (1 doc: tmp9x41vmaz)

`args/docmod/demo_collections.yaml` declared two entries by NAME (`Politics`,
`test`) and two whose id happens to BE the name (`col1`, `isp-peering-demo`).
So the gate skipped 19 findings and let 108 through -- including the two
documents the card was written about. Measured on the live board 2026-09-01,
after the first fix merged.

THE UNIT TESTS COULD NOT HAVE CAUGHT IT. They passed a collection id in and
asserted the set membership, which is exactly the mechanism working; the defect
was in what the DECLARATION means, not in how it is applied. That is the shape
this repo keeps re-learning -- one computation trusted twice proves nothing.

A LOOKUP, NOT A HEURISTIC. Resolving a declared string through
`dic_collections` is asking the database which collection somebody named; it is
not pattern-matching a name for the substring 'test'. A declaration that names
a collection should work whichever of that collection's two identifiers the
author had to hand -- requiring the hex id would make the file unwritable by a
person, and requiring the name would break `col1`, which has no separate one.

FAILS OPEN, like everything else on this path: an unreadable `dic_collections`
resolves nothing extra and the id-only matching still stands.
"""
from __future__ import annotations

import sqlite3

import pytest


@pytest.fixture()
def conn():
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    c.execute("CREATE TABLE dic_collections (collection_id TEXT PRIMARY KEY, name TEXT)")
    c.executemany(
        "INSERT INTO dic_collections (collection_id, name) VALUES (?, ?)",
        [
            ("801d27077c1444ddd4864757", "Politics"),
            ("d92716e5c128623f0e9fd1b1", "test"),
            ("col1", "col1"),
            ("9f0000000000000000000000", "Client Estate 2026"),
            ("aa0000000000000000000000", "citation-test-coll"),
            ("bb0000000000000000000000", "citation-test-coll"),
        ],
    )
    yield c
    c.close()


def _resolve(conn, declared):
    from tools.doc_modernization import card_bridge

    return card_bridge.resolve_demo_collection_ids(conn, declared=frozenset(declared))


# --------------------------------------------------------------------------- #
# the defect
# --------------------------------------------------------------------------- #
def test_a_declared_NAME_resolves_to_the_collection_id(conn):
    """The whole finding: 'Politics' is a name, and the documents carry the id."""
    resolved = _resolve(conn, {"Politics"})
    assert "801d27077c1444ddd4864757" in resolved


def test_the_two_documents_the_card_was_written_about_are_now_covered(conn):
    resolved = _resolve(conn, {"Politics", "test"})
    assert "801d27077c1444ddd4864757" in resolved, "the First Amendment document"
    assert "d92716e5c128623f0e9fd1b1" in resolved, "tmp9x41vmaz"


def test_a_declared_ID_still_matches(conn):
    """col1 and isp-peering-demo are declared by an id that IS the name; the
    name path must not cost the id path."""
    resolved = _resolve(conn, {"col1", "isp-peering-demo"})
    assert "col1" in resolved
    assert "isp-peering-demo" in resolved, "an id with no dic_collections row still counts"


def test_an_UNDECLARED_collection_resolves_to_NOTHING(conn):
    """The half a one-sided test misses: a resolver that returned every id would
    pass every test above and silence the entire board."""
    resolved = _resolve(conn, {"Politics"})
    assert "9f0000000000000000000000" not in resolved
    assert "col1" not in resolved


def test_a_name_shared_by_two_collections_resolves_to_BOTH(conn):
    """Six 'citation-test-coll' rows exist on the live board. A denylist naming
    that collection means all of it -- picking one id would half-apply the
    declaration, which is worse than either answer."""
    resolved = _resolve(conn, {"citation-test-coll"})
    assert {"aa0000000000000000000000", "bb0000000000000000000000"} <= resolved


def test_a_name_is_matched_EXACTLY_never_as_a_substring(conn):
    """'test' must not drag in 'citation-test-coll'. Substring matching is the
    heuristic this card refused in the first place."""
    resolved = _resolve(conn, {"test"})
    assert "aa0000000000000000000000" not in resolved
    assert resolved == {"test", "d92716e5c128623f0e9fd1b1"}


# --------------------------------------------------------------------------- #
# degradation
# --------------------------------------------------------------------------- #
def test_an_absent_dic_collections_still_matches_by_id():
    """Fails open, and the id-only behaviour is exactly what shipped first."""
    from tools.doc_modernization import card_bridge

    c = sqlite3.connect(":memory:")
    try:
        resolved = card_bridge.resolve_demo_collection_ids(c, declared=frozenset({"col1", "Politics"}))
    finally:
        c.close()
    assert resolved == {"col1", "Politics"}, "declared strings survive; nothing extra invented"


def test_an_empty_declaration_resolves_to_nothing(conn):
    assert _resolve(conn, set()) == frozenset()


def test_the_seeder_RESOLVES_before_it_filters():
    """Pins the wiring: resolving and then never consulting the result would
    leave the 108 findings exactly where they are."""
    import inspect

    from tools.doc_modernization import card_bridge

    source = inspect.getsource(card_bridge.emit_rollups)
    assert "resolve_demo_collection_ids" in source
    assert "_is_demo_document" in source


def test_is_demo_document_accepts_the_RESOLVED_set(conn):
    from tools.doc_modernization import card_bridge

    resolved = _resolve(conn, {"Politics"})
    assert card_bridge._is_demo_document("801d27077c1444ddd4864757", resolved) is True
    assert card_bridge._is_demo_document("9f0000000000000000000000", resolved) is False
    assert card_bridge._is_demo_document(None, resolved) is False
