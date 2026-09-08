# CUI // SP-CTI
"""dwr-cmt-01 — review comments are threaded, anchored and resolvable.

``dic_section_annotations`` carried ``selected_text``, ``category``, ``author``
and an open/resolved lifecycle. Measured on the live PG board 2026-09-08: 14
columns, ZERO rows, and NO MIGRATION — the table was a runtime
``CREATE TABLE IF NOT EXISTS`` inside ``blueprint.py``, so a deployment where
that ``_ensure`` had run was indistinguishable from one where it had not.

WHAT IS PINNED HERE
  * migration 20260908065203 adds EXACTLY six columns, idempotently, against
    the three populations it faces, and leaves a pre-existing row NULL — not
    recorded, never a default;
  * the anchor rule is IMPORTED from ``suggestion_store`` and not re-derived,
    read out of this module's AST — a second copy of "does this span still say
    what it said" is how two surfaces start disagreeing about a moved document;
  * ``orphaned`` is a READ-TIME verdict and never a stored column: a section
    edited out from under a comment reports orphaned on the next read, keeps
    its stored offsets, and is NEVER silently re-pointed;
  * ``unverifiable`` (the section could not be read) is kept apart from
    ``verified`` and from ``orphaned``;
  * a reply carries no anchor and no lifecycle of its own, a reply to a reply
    is refused, and resolving writes the ROOT only;
  * the three acts the card asks for, end to end through the routes: reply to a
    comment, resolve the thread, and show an orphaned comment reporting itself.

The database is a throwaway SQLite file reached through ``ICDEV_DB_PATH``, so
the store's bare ``get_connection()`` lands there and never on the checkout's
``data/icdev.db``.
"""
from __future__ import annotations

import ast
import importlib.util
import sqlite3
import uuid
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
MIGRATION_DIR = (ROOT / "tools" / "db" / "migrations"
                 / "20260908065203_dic_section_annotations_threads_and_anchors")

SECTION_TEXT = (
    "The enclave shall use TLS 1.2 for transport. "
    "Legacy hosts still terminate on port 8443 behind the proxy."
)

# The shape the runtime CREATE TABLE IF NOT EXISTS in blueprint.py built — what
# a deployment that has served this canvas actually carries today.
OLD_SHAPE = """
CREATE TABLE dic_section_annotations (
    ann_id TEXT PRIMARY KEY,
    section_id TEXT NOT NULL,
    doc_id TEXT NOT NULL,
    selected_text TEXT NOT NULL DEFAULT '',
    category TEXT NOT NULL,
    comment TEXT NOT NULL,
    author TEXT NOT NULL DEFAULT 'reviewer',
    status TEXT NOT NULL DEFAULT 'open',
    resolution_note TEXT,
    resolved_by TEXT,
    resolved_at TEXT,
    classification TEXT DEFAULT 'CUI',
    created_at TEXT NOT NULL,
    updated_at TEXT
);
"""

SECTIONS_DDL = """
CREATE TABLE IF NOT EXISTS dic_sections (
    section_id TEXT PRIMARY KEY,
    version_id TEXT,
    doc_id TEXT,
    heading TEXT,
    content TEXT,
    status TEXT,
    origin TEXT,
    created_at TEXT,
    tenant_id TEXT,
    classification TEXT
);
"""


def _load_migration():
    spec = importlib.util.spec_from_file_location(
        "mig_dic_section_annotations_threads", MIGRATION_DIR / "up.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# ── Harness ───────────────────────────────────────────────────────────────────

@pytest.fixture()
def db(tmp_path, monkeypatch):
    """A throwaway SQLite file holding dic_sections and the migrated table."""
    from tools.db.storage import get_connection

    path = tmp_path / "dic.db"
    monkeypatch.setenv("ICDEV_STORAGE_BACKEND", "sqlite")
    monkeypatch.setenv("ICDEV_DB_PATH", str(path))
    raw = sqlite3.connect(path)
    raw.executescript(SECTIONS_DDL)
    raw.commit()
    raw.close()
    with get_connection(db_path=str(path)) as conn:
        _load_migration().up(conn)
    return path


@pytest.fixture()
def store(db):
    from tools.document_intelligence import annotation_store
    return annotation_store


def _seed_section(db_path, content=SECTION_TEXT, *, doc_id=None) -> tuple[str, str]:
    section_id = f"sec-{uuid.uuid4().hex[:8]}"
    doc_id = doc_id or f"doc-{uuid.uuid4().hex[:8]}"
    raw = sqlite3.connect(db_path)
    raw.execute(
        "INSERT INTO dic_sections (section_id, version_id, doc_id, heading, content, "
        "status, origin, created_at, tenant_id, classification) "
        "VALUES (?,?,?,?,?,?,?,?,?,?)",
        (section_id, "v1", doc_id, "Transport", content, "draft",
         "ai_generated", "2026-09-08T00:00:00Z", "acme", "CUI"),
    )
    raw.commit()
    raw.close()
    return section_id, doc_id


def _rewrite_section(db_path, section_id, content):
    raw = sqlite3.connect(db_path)
    raw.execute("UPDATE dic_sections SET content = ? WHERE section_id = ?",
                (content, section_id))
    raw.commit()
    raw.close()


def _span(text, needle):
    start = text.index(needle)
    return start, start + len(needle)


def _anchored(store, section_id, doc_id, *, needle="TLS 1.2", **kw):
    start, end = _span(SECTION_TEXT, needle)
    return store.create_annotation(
        section_id=section_id, doc_id=doc_id, category="compliance",
        comment=f"Is {needle} still the floor?", author="alice",
        anchor_start=start, anchor_end=end, anchor_text=needle,
        anchor_basis="exact", tenant_id="acme", **kw)


# ── The migration ─────────────────────────────────────────────────────────────

class TestMigration:
    def test_column_list_is_declared_once(self):
        from tools.document_intelligence import annotation_store as store
        mig = _load_migration()
        assert tuple(mig.NEW_COLUMNS) == tuple(store.NEW_COLUMNS)

    def test_it_adds_exactly_the_six_columns(self):
        mig = _load_migration()
        assert [n for n, _ in mig.NEW_COLUMNS] == [
            "parent_ann_id", "anchor_start", "anchor_end",
            "anchor_text", "anchor_basis", "tenant_id"]

    def test_existing_old_shape_table_gains_them_and_keeps_its_row(self, tmp_path):
        from tools.db.storage import get_connection
        path = tmp_path / "old.db"
        raw = sqlite3.connect(path)
        raw.executescript(OLD_SHAPE)
        raw.execute(
            "INSERT INTO dic_section_annotations "
            "(ann_id, section_id, doc_id, category, comment, created_at) "
            "VALUES ('ann_old','sec-1','doc-1','question','why?','t0')")
        raw.commit()
        raw.close()

        mig = _load_migration()
        with get_connection(db_path=str(path)) as conn:
            mig.up(conn)
            cols = mig.existing_columns(conn)
            assert {n for n, _ in mig.NEW_COLUMNS} <= cols
            # idempotent: a second run adds nothing and raises nothing
            mig.up(conn)
            assert mig.existing_columns(conn) == cols
            row = dict(conn.execute(
                "SELECT parent_ann_id, anchor_start, anchor_basis, tenant_id "
                "FROM dic_section_annotations WHERE ann_id = 'ann_old'").fetchone())
        # NOT RECORDED, never a default — an unanchored guess about an old row
        # would be indistinguishable from a writer that actually said so.
        assert row == {"parent_ann_id": None, "anchor_start": None,
                       "anchor_basis": None, "tenant_id": None}

    def test_absent_table_is_created_in_full(self, tmp_path):
        from tools.db.storage import get_connection
        mig = _load_migration()
        with get_connection(db_path=str(tmp_path / "fresh.db")) as conn:
            mig.up(conn)
            cols = mig.existing_columns(conn)
        assert {"ann_id", "section_id", "category", "comment", "created_at"} <= cols
        assert {n for n, _ in mig.NEW_COLUMNS} <= cols

    def test_down_drops_exactly_the_added_columns(self, tmp_path):
        from tools.db.storage import get_connection
        path = tmp_path / "down.db"
        raw = sqlite3.connect(path)
        raw.executescript(OLD_SHAPE)
        raw.commit()
        raw.close()
        mig = _load_migration()
        with get_connection(db_path=str(path)) as conn:
            before = mig.existing_columns(conn)
            mig.up(conn)
            mig.down(conn)
            assert mig.existing_columns(conn) == before

    def test_store_ddl_and_migration_ddl_agree(self):
        """A fresh table from either path has the same columns."""
        from tests._sql_compat import translating

        from tools.document_intelligence import annotation_store as store
        mig = _load_migration()
        a = sqlite3.connect(":memory:")
        a.execute(mig.CREATE_FULL)
        from_mig = {r[1] for r in a.execute("PRAGMA table_info(dic_section_annotations)")}

        b = sqlite3.connect(":memory:")
        store._ensure_tables(translating(b, unclosable=True))
        from_store = {r[1] for r in b.execute("PRAGMA table_info(dic_section_annotations)")}
        assert from_mig == from_store


# ── ONE anchor rule ───────────────────────────────────────────────────────────

class TestOneAnchorRule:
    """The anchor rule lives in suggestion_store (dwr-anchor-03). A local copy
    here would pass every behavioural test on the day it was written and drift
    the first time the rule changed on one side only — so it is refused
    STRUCTURALLY, from the module's AST."""

    def test_the_rule_is_imported_not_redefined(self):
        src = (ROOT / "tools" / "document_intelligence" / "annotation_store.py").read_text(
            encoding="utf-8")
        tree = ast.parse(src)
        imported = {
            alias.name
            for node in ast.walk(tree)
            if isinstance(node, ast.ImportFrom)
            and node.module == "tools.document_intelligence.suggestion_store"
            for alias in node.names
        }
        assert {"resolve_anchor", "validate_anchor"} <= imported, \
            "annotation_store must import the anchor rule from suggestion_store"
        defined = {n.name for n in ast.walk(tree)
                   if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))}
        assert not ({"resolve_anchor", "validate_anchor"} & defined), \
            "the anchor rule is re-derived here — there must be exactly one copy"

    def test_it_is_the_same_object(self):
        from tools.document_intelligence import annotation_store, suggestion_store
        assert annotation_store.resolve_anchor is suggestion_store.resolve_anchor
        assert annotation_store.validate_anchor is suggestion_store.validate_anchor


# ── The read-time verdict ─────────────────────────────────────────────────────

class TestAnchorState:
    def test_a_matching_slice_is_verified(self):
        from tools.document_intelligence import annotation_store as store
        s, e = _span(SECTION_TEXT, "port 8443")
        got = store.anchor_state(SECTION_TEXT, {
            "anchor_basis": "exact", "anchor_start": s, "anchor_end": e,
            "anchor_text": "port 8443"})
        assert got["state"] == "verified"
        assert got["relocation_candidate"] is None

    def test_a_moved_span_is_orphaned_and_offers_an_advisory_candidate(self):
        from tools.document_intelligence import annotation_store as store
        moved = "PREFIX. " + SECTION_TEXT
        s, e = _span(SECTION_TEXT, "port 8443")
        got = store.anchor_state(moved, {
            "anchor_basis": "exact", "anchor_start": s, "anchor_end": e,
            "anchor_text": "port 8443"})
        assert got["state"] == "orphaned"
        assert got["reason"] == "found_once"
        cand = got["relocation_candidate"]
        assert moved[cand["anchor_start"]:cand["anchor_end"]] == "port 8443"
        assert (cand["anchor_start"], cand["anchor_end"]) != (s, e)

    def test_a_deleted_span_is_orphaned_with_no_candidate(self):
        from tools.document_intelligence import annotation_store as store
        s, e = _span(SECTION_TEXT, "port 8443")
        got = store.anchor_state("The enclave shall use TLS 1.2.", {
            "anchor_basis": "exact", "anchor_start": s, "anchor_end": e,
            "anchor_text": "port 8443"})
        assert got["state"] == "orphaned"
        assert got["reason"] == "not_found"
        assert got["relocation_candidate"] is None

    def test_an_ambiguous_match_offers_nothing(self):
        """Picking one of several is the guess claim_extractor.anchor refuses."""
        from tools.document_intelligence import annotation_store as store
        text = "TLS 1.2 here. And TLS 1.2 there."
        got = store.anchor_state(text, {
            "anchor_basis": "exact", "anchor_start": 900, "anchor_end": 907,
            "anchor_text": "TLS 1.2"})
        assert got["state"] == "orphaned"
        assert got["reason"] == "ambiguous:2"
        assert got["relocation_candidate"] is None

    def test_a_section_level_comment_is_unanchored_not_orphaned(self):
        from tools.document_intelligence import annotation_store as store
        got = store.anchor_state(SECTION_TEXT, {
            "anchor_basis": "unanchored", "anchor_start": None,
            "anchor_end": None, "anchor_text": None})
        assert got["state"] == "unanchored"

    def test_an_unreadable_section_is_unverifiable_never_verified(self):
        from tools.document_intelligence import annotation_store as store
        s, e = _span(SECTION_TEXT, "TLS 1.2")
        got = store.anchor_state(None, {
            "anchor_basis": "exact", "anchor_start": s, "anchor_end": e,
            "anchor_text": "TLS 1.2"})
        assert got["state"] == "unverifiable"
        assert got["state"] not in ("verified", "orphaned")

    def test_a_pre_migration_row_says_no_anchor_recorded(self):
        from tools.document_intelligence import annotation_store as store
        got = store.anchor_state(SECTION_TEXT, {
            "anchor_basis": None, "anchor_start": None, "anchor_end": None,
            "anchor_text": None})
        assert got == {"state": "unanchored", "reason": "no_anchor_recorded",
                       "relocation_candidate": None}


# ── Writes ────────────────────────────────────────────────────────────────────

class TestCreate:
    def test_an_anchored_comment_round_trips_verified(self, store, db):
        section_id, doc_id = _seed_section(db)
        row = _anchored(store, section_id, doc_id)
        assert row["anchor_basis"] == "exact"
        assert row["anchor_text"] == "TLS 1.2"
        assert row["anchor"]["state"] == "verified"
        # display follows the verified slice, so the panel and the check cannot
        # render two different "selected texts"
        assert row["selected_text"] == "TLS 1.2"
        assert row["tenant_id"] == "acme"

    def test_a_non_verbatim_anchor_is_refused_and_writes_nothing(self, store, db):
        section_id, doc_id = _seed_section(db)
        with pytest.raises(store.AnnotationError):
            store.create_annotation(
                section_id=section_id, doc_id=doc_id, category="risk",
                comment="stale", anchor_start=0, anchor_end=7,
                anchor_text="TLS 1.3", anchor_basis="exact")
        assert store.list_annotations(section_id) == []

    def test_an_unanchored_comment_may_not_carry_offsets(self, store, db):
        section_id, doc_id = _seed_section(db)
        with pytest.raises(store.AnnotationError):
            store.create_annotation(
                section_id=section_id, doc_id=doc_id, category="question",
                comment="?", anchor_start=0, anchor_end=3,
                anchor_basis="unanchored")

    def test_an_anchor_against_an_unreadable_section_is_refused(self, store, db):
        with pytest.raises(store.AnnotationError) as exc:
            store.create_annotation(
                section_id="sec-missing", doc_id="d", category="question",
                comment="?", anchor_start=0, anchor_end=3, anchor_text="The",
                anchor_basis="exact")
        assert "content of record" in str(exc.value)

    def test_an_unknown_category_is_refused(self, store, db):
        section_id, doc_id = _seed_section(db)
        with pytest.raises(store.AnnotationError):
            store.create_annotation(section_id=section_id, doc_id=doc_id,
                                    category="vibes", comment="hm")

    def test_the_existing_categories_are_kept(self, store):
        assert set(store.CATEGORIES) == {
            "question", "improvement", "compliance", "strength",
            "weakness", "risk", "editorial"}


class TestThreads:
    def test_a_reply_attaches_and_inherits_the_documents_identity(self, store, db):
        section_id, doc_id = _seed_section(db)
        root = _anchored(store, section_id, doc_id)
        reply = store.create_annotation(
            section_id=section_id, category="question",
            comment="1.3 is the floor from Q4.", author="bob",
            parent_ann_id=root["ann_id"])
        assert reply["parent_ann_id"] == root["ann_id"]
        assert reply["doc_id"] == doc_id
        assert reply["tenant_id"] == "acme"
        assert reply["anchor"]["reason"] == "reply_inherits_thread"

        threads = store.list_threads(section_id)
        assert len(threads) == 1
        assert threads[0]["reply_count"] == 1
        assert threads[0]["replies"][0]["ann_id"] == reply["ann_id"]

    def test_a_reply_to_a_reply_is_refused(self, store, db):
        section_id, doc_id = _seed_section(db)
        root = _anchored(store, section_id, doc_id)
        reply = store.create_annotation(
            section_id=section_id, category="question", comment="a",
            parent_ann_id=root["ann_id"])
        with pytest.raises(store.AnnotationError) as exc:
            store.create_annotation(section_id=section_id, category="question",
                                    comment="b", parent_ann_id=reply["ann_id"])
        assert "flat" in str(exc.value)

    def test_a_reply_may_not_carry_its_own_anchor(self, store, db):
        section_id, doc_id = _seed_section(db)
        root = _anchored(store, section_id, doc_id)
        s, e = _span(SECTION_TEXT, "port 8443")
        with pytest.raises(store.AnnotationError) as exc:
            store.create_annotation(
                section_id=section_id, category="question", comment="a",
                parent_ann_id=root["ann_id"], anchor_start=s, anchor_end=e,
                anchor_text="port 8443", anchor_basis="exact")
        assert "no anchor of its own" in str(exc.value)

    def test_a_reply_across_sections_is_refused(self, store, db):
        section_a, doc_id = _seed_section(db)
        section_b, _ = _seed_section(db)
        root = _anchored(store, section_a, doc_id)
        with pytest.raises(store.AnnotationError):
            store.create_annotation(section_id=section_b, category="question",
                                    comment="a", parent_ann_id=root["ann_id"])

    def test_an_unknown_parent_is_refused(self, store, db):
        section_id, doc_id = _seed_section(db)
        with pytest.raises(store.AnnotationError):
            store.create_annotation(section_id=section_id, category="question",
                                    comment="a", parent_ann_id="ann_nope")

    def test_a_reply_whose_root_is_gone_is_surfaced_not_dropped(self, store, db):
        section_id, doc_id = _seed_section(db)
        root = _anchored(store, section_id, doc_id)
        reply = store.create_annotation(
            section_id=section_id, category="question", comment="a",
            parent_ann_id=root["ann_id"])
        # a hand-deleted root, the shape a legacy DELETE left behind
        raw = sqlite3.connect(db)
        raw.execute("DELETE FROM dic_section_annotations WHERE ann_id = ?",
                    (root["ann_id"],))
        raw.commit()
        raw.close()
        threads = store.list_threads(section_id)
        assert [t["ann_id"] for t in threads] == [reply["ann_id"]]
        assert threads[0]["thread_broken"] is True


class TestResolution:
    def test_resolving_writes_the_root_only(self, store, db):
        section_id, doc_id = _seed_section(db)
        root = _anchored(store, section_id, doc_id)
        reply = store.create_annotation(
            section_id=section_id, category="question", comment="a",
            parent_ann_id=root["ann_id"])
        out = store.resolve_thread(root["ann_id"], resolved_by="carol",
                                   note="fixed in v3")
        assert out["status"] == "resolved"
        assert out["resolved_by"] == "carol"
        assert out["resolved_at"]
        assert out["resolution_note"] == "fixed in v3"
        # the reply has no second lifecycle to keep in step
        assert store.get_annotation(reply["ann_id"])["status"] == "open"
        threads = store.list_threads(section_id)
        assert threads[0]["thread_status"] == "resolved"
        assert threads[0]["reply_count"] == 1

    def test_a_resolved_thread_comes_back_whole_when_filtered(self, store, db):
        section_id, doc_id = _seed_section(db)
        root = _anchored(store, section_id, doc_id)
        store.create_annotation(section_id=section_id, category="question",
                                comment="a", parent_ann_id=root["ann_id"])
        store.resolve_thread(root["ann_id"], resolved_by="carol")
        assert store.list_threads(section_id, status="open") == []
        resolved = store.list_threads(section_id, status="resolved")
        assert len(resolved) == 1 and resolved[0]["reply_count"] == 1

    def test_a_reply_cannot_be_resolved(self, store, db):
        section_id, doc_id = _seed_section(db)
        root = _anchored(store, section_id, doc_id)
        reply = store.create_annotation(
            section_id=section_id, category="question", comment="a",
            parent_ann_id=root["ann_id"])
        with pytest.raises(store.AnnotationError) as exc:
            store.resolve_thread(reply["ann_id"], resolved_by="carol")
        assert "resolve the thread" in str(exc.value)

    def test_reopening_clears_the_resolution_it_recorded(self, store, db):
        section_id, doc_id = _seed_section(db)
        root = _anchored(store, section_id, doc_id)
        store.resolve_thread(root["ann_id"], resolved_by="carol", note="n")
        out = store.reopen_thread(root["ann_id"])
        assert out["status"] == "open"
        assert (out["resolved_by"], out["resolved_at"], out["resolution_note"]) \
            == (None, None, None)

    def test_deleting_a_root_takes_its_replies(self, store, db):
        section_id, doc_id = _seed_section(db)
        root = _anchored(store, section_id, doc_id)
        store.create_annotation(section_id=section_id, category="question",
                                comment="a", parent_ann_id=root["ann_id"])
        out = store.delete_thread(root["ann_id"])
        assert out["deleted_replies"] == 1
        assert store.list_annotations(section_id) == []

    def test_deleting_a_reply_leaves_the_thread(self, store, db):
        section_id, doc_id = _seed_section(db)
        root = _anchored(store, section_id, doc_id)
        reply = store.create_annotation(section_id=section_id, category="question",
                                        comment="a", parent_ann_id=root["ann_id"])
        out = store.delete_thread(reply["ann_id"])
        assert out == {"deleted": reply["ann_id"], "deleted_replies": 0,
                       "was_reply": True}
        assert [a["ann_id"] for a in store.list_annotations(section_id)] == [root["ann_id"]]


class TestOrphanIsNeverPersistedOrRePointed:
    def test_an_edited_section_orphans_the_comment_on_the_next_read(self, store, db):
        section_id, doc_id = _seed_section(db)
        row = _anchored(store, section_id, doc_id, needle="port 8443")
        assert row["anchor"]["state"] == "verified"
        stored = (row["anchor_start"], row["anchor_end"])

        _rewrite_section(db, section_id, "REWRITTEN. " + SECTION_TEXT)
        after = store.get_annotation(row["ann_id"])
        assert after["anchor"]["state"] == "orphaned"
        # the stored offsets are UNTOUCHED — the candidate is advice, not a write
        assert (after["anchor_start"], after["anchor_end"]) == stored
        cand = after["anchor"]["relocation_candidate"]
        assert cand and (cand["anchor_start"], cand["anchor_end"]) != stored

    def test_the_verdict_is_not_a_column(self, store, db):
        """Nothing persists it: a stored verdict about whether a span still
        matches goes stale on the one event it exists to describe."""
        section_id, doc_id = _seed_section(db)
        _anchored(store, section_id, doc_id)
        raw = sqlite3.connect(db)
        cols = {r[1] for r in raw.execute("PRAGMA table_info(dic_section_annotations)")}
        raw.close()
        assert not (cols & {"orphaned", "anchor_state", "anchor_valid", "is_orphaned"})

    def test_a_deleted_section_reads_unverifiable_not_orphaned(self, store, db):
        section_id, doc_id = _seed_section(db)
        row = _anchored(store, section_id, doc_id)
        raw = sqlite3.connect(db)
        raw.execute("DELETE FROM dic_sections WHERE section_id = ?", (section_id,))
        raw.commit()
        raw.close()
        assert store.get_annotation(row["ann_id"])["anchor"]["state"] == "unverifiable"


class TestAnchorFromSelection:
    def test_an_unreadable_section_yields_a_comment_with_no_span(self, store, db):
        got = store.anchor_from_selection("sec-missing", "port 8443")
        assert got["anchor_basis"] == "unanchored"
        assert got["reason"] == "section_unreadable"
        assert got["section_content"] is None

    def test_verified_offsets_survive_as_exact(self, store, db):
        section_id, _ = _seed_section(db)
        s, e = _span(SECTION_TEXT, "TLS 1.2")
        got = store.anchor_from_selection(section_id, "TLS 1.2",
                                          anchor_start=s, anchor_end=e)
        assert got["anchor_basis"] == "exact"
        assert (got["anchor_start"], got["anchor_end"]) == (s, e)


class TestTenantScoping:
    def test_a_read_scoped_to_a_tenant_excludes_another(self, store, db):
        section_id, doc_id = _seed_section(db)
        _anchored(store, section_id, doc_id)  # tenant acme
        store.create_annotation(section_id=section_id, doc_id=doc_id,
                                category="question", comment="other tenant",
                                tenant_id="globex")
        assert len(store.list_annotations(section_id, tenant_id="acme")) == 1
        assert len(store.list_annotations(section_id, tenant_id="globex")) == 1
        assert len(store.list_annotations(section_id)) == 2

    def test_a_row_written_before_tenant_scoping_is_still_shown(self, store, db):
        """NULL is a pre-migration row. Dropping it would make an old comment
        vanish with no word — worse than showing it."""
        section_id, _ = _seed_section(db)
        raw = sqlite3.connect(db)
        raw.execute(
            "INSERT INTO dic_section_annotations "
            "(ann_id, section_id, doc_id, category, comment, created_at) "
            "VALUES ('ann_legacy',?,'d','question','pre-tenant','t0')", (section_id,))
        raw.commit()
        raw.close()
        got = store.list_annotations(section_id, tenant_id="acme")
        assert [a["ann_id"] for a in got] == ["ann_legacy"]
        assert got[0]["anchor"]["reason"] == "no_anchor_recorded"


# ── End to end, through the routes ────────────────────────────────────────────

@pytest.fixture()
def client(db):
    import flask

    from tools.document_intelligence.blueprint import dic_bp

    app = flask.Flask(__name__)
    app.register_blueprint(dic_bp, url_prefix="/document-intelligence")
    app.config["TESTING"] = True
    with app.test_client() as c:
        yield c


_BASE = "/document-intelligence/api"


class TestTheThreeActs:
    """The card's DONE list, each act through the HTTP surface the page calls."""

    def test_reply_to_a_comment(self, client, db):
        section_id, _ = _seed_section(db)
        s, e = _span(SECTION_TEXT, "TLS 1.2")
        root = client.post(f"{_BASE}/sections/{section_id}/annotations", json={
            "category": "compliance", "comment": "Still the floor?",
            "anchor_start": s, "anchor_end": e, "anchor_text": "TLS 1.2"})
        assert root.status_code == 201, root.get_json()
        root_id = root.get_json()["ann_id"]
        assert root.get_json()["anchor"]["state"] == "verified"

        reply = client.post(f"{_BASE}/sections/{section_id}/annotations", json={
            "category": "question", "comment": "1.3 from Q4.",
            "parent_ann_id": root_id})
        assert reply.status_code == 201, reply.get_json()

        page = client.get(f"{_BASE}/sections/{section_id}/annotations").get_json()
        assert page["counts"] == {
            "threads": 1, "comments": 2, "open": 1, "resolved": 0,
            "anchored": 1, "orphaned": 0, "unanchored": 0, "unverifiable": 0}
        assert page["threads"][0]["replies"][0]["comment"] == "1.3 from Q4."

    def test_resolve_the_thread(self, client, db):
        section_id, _ = _seed_section(db)
        root_id = client.post(f"{_BASE}/sections/{section_id}/annotations", json={
            "category": "question", "comment": "?"}).get_json()["ann_id"]
        reply_id = client.post(f"{_BASE}/sections/{section_id}/annotations", json={
            "category": "question", "comment": "answered",
            "parent_ann_id": root_id}).get_json()["ann_id"]

        done = client.put(f"{_BASE}/annotations/{root_id}", json={
            "status": "resolved", "resolved_by": "carol",
            "resolution_note": "fixed in v3"})
        assert done.status_code == 200
        assert done.get_json()["status"] == "resolved"

        page = client.get(f"{_BASE}/sections/{section_id}/annotations").get_json()
        assert page["counts"]["resolved"] == 1 and page["counts"]["open"] == 0

        refused = client.put(f"{_BASE}/annotations/{reply_id}",
                             json={"status": "resolved"})
        assert refused.status_code == 400
        assert "resolve the thread" in refused.get_json()["error"]

    def test_an_orphaned_comment_reports_itself(self, client, db):
        section_id, _ = _seed_section(db)
        s, e = _span(SECTION_TEXT, "port 8443")
        created = client.post(f"{_BASE}/sections/{section_id}/annotations", json={
            "category": "risk", "comment": "Is this still exposed?",
            "anchor_start": s, "anchor_end": e, "anchor_text": "port 8443"})
        ann_id = created.get_json()["ann_id"]

        _rewrite_section(db, section_id, "REWRITTEN. " + SECTION_TEXT)

        page = client.get(f"{_BASE}/sections/{section_id}/annotations").get_json()
        thread = page["threads"][0]
        assert thread["ann_id"] == ann_id
        assert thread["anchor"]["state"] == "orphaned"
        assert thread["anchor"]["reason"] == "found_once"
        assert thread["anchor"]["relocation_candidate"] is not None
        assert page["counts"]["orphaned"] == 1
        assert page["counts"]["anchored"] == 0

    def test_a_caller_claiming_exactness_wrongly_is_a_400(self, client, db):
        """An explicit basis is a CLAIM, and a wrong one is refused rather than
        quietly downgraded — that is what makes `exact` mean anything."""
        section_id, _ = _seed_section(db)
        r = client.post(f"{_BASE}/sections/{section_id}/annotations", json={
            "category": "risk", "comment": "x", "anchor_basis": "exact",
            "anchor_start": 0, "anchor_end": 7, "anchor_text": "TLS 1.3"})
        assert r.status_code == 400
        assert "verbatim" in r.get_json()["error"]

    def test_a_selection_the_page_sends_is_resolved_to_an_honest_basis(self, client, db):
        """doc_detail renders markdown, so it has the selected TEXT and not
        offsets into the content of record. Found once -> `relocated`, and the
        page said so; it did not claim `exact`."""
        section_id, _ = _seed_section(db)
        r = client.post(f"{_BASE}/sections/{section_id}/annotations", json={
            "category": "risk", "comment": "Still exposed?",
            "anchor_text": "port 8443"})
        assert r.status_code == 201
        row = r.get_json()
        assert row["anchor_basis"] == "relocated"
        assert row["anchor"]["state"] == "verified"
        assert SECTION_TEXT[row["anchor_start"]:row["anchor_end"]] == "port 8443"

    def test_an_ambiguous_selection_is_kept_as_a_comment_with_no_span(self, client, db):
        """Two occurrences: the remark is worth keeping, the span is not
        invented. `unanchored`, not a refusal and not a guess."""
        section_id, _ = _seed_section(db, content="TLS 1.2 here. And TLS 1.2 there.")
        r = client.post(f"{_BASE}/sections/{section_id}/annotations", json={
            "category": "question", "comment": "which one?",
            "anchor_text": "TLS 1.2"})
        assert r.status_code == 201
        row = r.get_json()
        assert row["anchor_basis"] == "unanchored"
        assert (row["anchor_start"], row["anchor_end"]) == (None, None)
        # what was highlighted is still shown
        assert row["selected_text"] == "TLS 1.2"
        page = client.get(f"{_BASE}/sections/{section_id}/annotations").get_json()
        assert page["counts"]["unanchored"] == 1
        assert page["counts"]["orphaned"] == 0

    def test_delete_reports_the_cascade(self, client, db):
        section_id, _ = _seed_section(db)
        root_id = client.post(f"{_BASE}/sections/{section_id}/annotations", json={
            "category": "question", "comment": "?"}).get_json()["ann_id"]
        client.post(f"{_BASE}/sections/{section_id}/annotations", json={
            "category": "question", "comment": "a", "parent_ann_id": root_id})
        out = client.delete(f"{_BASE}/annotations/{root_id}")
        assert out.status_code == 200
        assert out.get_json()["deleted_replies"] == 1
        assert client.get(
            f"{_BASE}/sections/{section_id}/annotations").get_json()["counts"]["comments"] == 0

    def test_a_missing_annotation_is_a_404(self, client, db):
        assert client.delete(f"{_BASE}/annotations/ann_nope").status_code == 404
        assert client.put(f"{_BASE}/annotations/ann_nope",
                          json={"comment": "x"}).status_code == 404
