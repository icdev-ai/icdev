# CUI // SP-CTI
"""dwr-cmt-02 — comments and change cards in ONE rail, ordered by anchor position.

WHAT IS PINNED HERE

  * POSITION IS A MEASUREMENT. Only a ``verified`` anchor yields a number;
    everything else has ``position is None`` and lands in the labelled group.
    Sorting an unanchored item to offset 0 would draw it as a remark about the
    first sentence — and measured on the live PG board 2026-09-08 that is 58 of
    58 rows in ``dic_suggestions`` (``anchor_basis`` NULL).

  * THE INTERLEAVE IS BY POSITION, NOT BY AGE. The fixture creates the comment
    FIRST and the change card SECOND while anchoring the change EARLIER in the
    section, so a rail that ordered by ``created_at`` would pass a weaker test
    and fail this one.

  * THREE STATES ARE NEVER COLLAPSED: positioned, placed-but-unpositioned, and
    UNPLACED (names no section at all — again, 58 of 58 live rows, which carry
    a real ``doc_id`` and ``section_id = ''``). An unplaced item is neither
    dropped nor attached to a section it does not name.

  * THE ANCHOR RULE IS IMPORTED, NEVER RE-DERIVED. A change card's verdict comes
    from ``suggestion_store.verify_anchor`` — the accept door's own question —
    translated through a map pinned against ``suggestion_store.VERIFY_REASONS``,
    and read out of this module's AST so a future local copy fails.

  * ``empty`` (measured: the sections were read, nobody has said anything) is
    never ``unmeasurable`` (nothing could be read), and an unmeasurable rail
    reports ``None`` counts rather than zeroes.

  * IDENTITY. ``g.security_context`` is a ``SecurityContext`` DATACLASS with no
    ``.get``, so every ``.get`` in blueprint.py's identity helpers raised inside
    a bare ``except`` and an AUTHENTICATED account read as the anonymous
    sentinel — which ``_user_role`` grants ADMIN. Both halves are pinned: the
    read now works for both shapes, and a signed-in account no longer inherits
    the sentinel's admin.

The database is a throwaway SQLite file reached through ``ICDEV_DB_PATH``, so
the stores' bare ``get_connection()`` lands there and never on the checkout's
``data/icdev.db``.
"""
from __future__ import annotations

import ast
import sqlite3
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]

# "TLS 1.2" starts at 22; "hardware token" starts at 159 — the change card is
# anchored EARLIER in the section than the comment, and is created LATER.
SECTION_A = (
    "The enclave shall use TLS 1.2 for transport security. "
    "Legacy hosts still terminate on port 8443 behind the reverse proxy. "
    "All administrative access requires a hardware token."
)
SECTION_B = (
    "Backups run nightly to the regional vault. "
    "Restoration is exercised quarterly by the operations team."
)

DDL = """
CREATE TABLE IF NOT EXISTS dic_sections (
    section_id TEXT PRIMARY KEY, version_id TEXT, doc_id TEXT, heading TEXT,
    content TEXT, status TEXT, origin TEXT, created_at TEXT, tenant_id TEXT,
    classification TEXT
);
CREATE TABLE IF NOT EXISTS dic_versions (
    version_id TEXT PRIMARY KEY, doc_id TEXT, version_no INTEGER, origin TEXT,
    status TEXT, created_at TEXT
);
"""


# ── Harness ───────────────────────────────────────────────────────────────────

@pytest.fixture()
def db(tmp_path, monkeypatch):
    monkeypatch.setenv("ICDEV_STORAGE_BACKEND", "sqlite")
    monkeypatch.setenv("ICDEV_DB_PATH", str(tmp_path / "rail.db"))
    path = tmp_path / "rail.db"
    raw = sqlite3.connect(path)
    raw.executescript(DDL)
    raw.execute("INSERT INTO dic_versions VALUES ('ver-1','doc-1',1,'human','pending_review','t0')")
    for sid, heading, content in (("sec-01", "Transport", SECTION_A),
                                  ("sec-02", "Backups", SECTION_B)):
        raw.execute(
            "INSERT INTO dic_sections (section_id, version_id, doc_id, heading, content, "
            "status, origin, created_at, tenant_id, classification) VALUES (?,?,?,?,?,?,?,?,?,?)",
            (sid, "ver-1", "doc-1", heading, content, "draft", "human", "t0", "default", "CUI"))
    raw.commit()
    raw.close()
    from tools.db.storage import get_connection
    from tools.document_intelligence import annotation_store, suggestion_store
    with get_connection(db_path=str(path)) as conn:
        annotation_store._ensure_tables(conn)
        suggestion_store._ensure_tables(conn)
    return path


@pytest.fixture()
def rail_mod(db):
    from tools.document_intelligence import review_rail
    return review_rail


def _span(text, needle):
    i = text.index(needle)
    return i, i + len(needle)


def _comment(*, section_id="sec-01", needle=None, section=SECTION_A, author="u-alice",
             comment="why?", category="question", **kw):
    from tools.document_intelligence import annotation_store as anns
    fields = {"anchor_basis": "unanchored"}
    if needle:
        s, e = _span(section, needle)
        fields = {"anchor_start": s, "anchor_end": e, "anchor_text": needle,
                  "anchor_basis": "exact"}
    return anns.create_annotation(section_id=section_id, doc_id="doc-1",
                                  category=category, comment=comment, author=author,
                                  tenant_id="default", **fields, **kw)


def _change(*, section_id="sec-01", needle=None, section=SECTION_A,
            suggested="TLS 1.3", rationale="superseded", anchor_section_id=None):
    from tools.document_intelligence import suggestion_store as sug
    fields = {"anchor_basis": "unanchored"}
    if needle:
        s, e = _span(section, needle)
        fields = {"anchor_section_id": anchor_section_id or section_id,
                  "anchor_start": s, "anchor_end": e, "anchor_text": needle,
                  "anchor_basis": "exact"}
    return sug.create_suggestion(section_id=section_id, doc_id="doc-1",
                                 collection_id="coll-1", canvas_source="doc_modernization",
                                 suggested_content=suggested, current_content=section,
                                 rationale=rationale, tenant_id="default", **fields)


def _rewrite(db_path, section_id, content):
    raw = sqlite3.connect(db_path)
    raw.execute("UPDATE dic_sections SET content = ? WHERE section_id = ?", (content, section_id))
    raw.commit()
    raw.close()


def _build(rail_mod, **kw):
    return rail_mod.build_rail("doc-1", version_id="ver-1", tenant_id="default", **kw)


# ── The interleave, which is the card ─────────────────────────────────────────

class TestInterleave:
    def test_a_change_and_a_comment_sort_by_position_not_by_age(self, rail_mod, db):
        # The comment is created FIRST and anchored LATER in the section; the
        # change is created SECOND and anchored EARLIER. Ordering by age would
        # put them the other way round.
        _comment(needle="hardware token", comment="still required?")
        _change(needle="TLS 1.2")
        sec = _build(rail_mod)["sections"][0]
        assert [(i["kind"], i["position"]) for i in sec["positioned"]] == [
            ("change", 22), ("comment", 159)]
        assert sec["unpositioned"] == []

    def test_both_kinds_carry_one_item_shape(self, rail_mod, db):
        _comment(needle="hardware token")
        _change(needle="TLS 1.2")
        items = _build(rail_mod)["sections"][0]["positioned"]
        shared = {"kind", "id", "section_id", "position", "anchor", "anchor_text",
                  "author", "created_at", "status", "body", "replies", "reply_count"}
        for item in items:
            assert shared <= set(item), item["kind"]

    def test_sections_are_in_the_pages_own_document_order(self, rail_mod, db):
        _comment(section_id="sec-02", needle="quarterly", section=SECTION_B)
        _comment(section_id="sec-01", needle="TLS 1.2")
        rail = _build(rail_mod)
        assert [s["section_id"] for s in rail["sections"]] == ["sec-01", "sec-02"]


# ── Position is a measurement, never a default ────────────────────────────────

class TestPositionIsMeasured:
    def test_an_unanchored_item_has_no_position_and_is_never_offset_zero(self, rail_mod, db):
        _comment(comment="the whole section reads as a draft")
        _change()  # anchor_basis unanchored — the live board's shape
        sec = _build(rail_mod)["sections"][0]
        assert sec["positioned"] == []
        assert len(sec["unpositioned"]) == 2
        assert all(i["position"] is None for i in sec["unpositioned"])

    def test_an_orphaned_comment_loses_its_position_and_keeps_its_offsets(self, rail_mod, db):
        ann = _comment(section_id="sec-02", needle="quarterly", section=SECTION_B)
        _rewrite(db, "sec-02", SECTION_B.replace("quarterly", "monthly"))
        sec = [s for s in _build(rail_mod)["sections"] if s["section_id"] == "sec-02"][0]
        item = sec["unpositioned"][0]
        assert item["position"] is None
        assert item["anchor"]["state"] == "orphaned"
        # The row is NOT re-pointed: the store still holds the offsets it was
        # written with. dwr-cmt-01's rule, unchanged by rendering it here.
        from tools.document_intelligence import annotation_store as anns
        assert anns.get_annotation(ann["ann_id"])["anchor_start"] == SECTION_B.index("quarterly")

    def test_an_orphaned_change_loses_its_position_too(self, rail_mod, db):
        _change(needle="TLS 1.2")
        _rewrite(db, "sec-01", SECTION_A.replace("TLS 1.2", "TLS 1.3"))
        sec = _build(rail_mod)["sections"][0]
        assert sec["positioned"] == []
        assert sec["unpositioned"][0]["anchor"]["state"] == "orphaned"
        assert sec["unpositioned"][0]["anchor"]["reason"] == "anchor_stale"

    def test_position_comes_only_from_a_verified_anchor(self, rail_mod):
        # The one place a position is decided. A row carrying offsets but no
        # verified verdict must not leak a number through.
        row = {"anchor_start": 12}
        assert rail_mod._position({"state": "verified"}, row) == 12
        for state in ("orphaned", "unanchored", "unverifiable"):
            assert rail_mod._position({"state": state}, row) is None


# ── unplaced: names no section at all ─────────────────────────────────────────

class TestUnplaced:
    def test_a_change_naming_no_section_is_reported_whole_not_dropped(self, rail_mod, db):
        _change(section_id="", suggested="Add a retention clause.")
        rail = _build(rail_mod)
        assert rail["unplaced_count"] == 1
        assert rail["unplaced"][0]["unplaced_reason"] == "no_section_named"
        # ... and never attached to a section it does not name.
        assert all(not s["positioned"] and not s["unpositioned"] for s in rail["sections"])

    def test_a_change_naming_a_section_outside_this_version_is_unplaced(self, rail_mod, db):
        _change(section_id="sec-gone")
        rail = _build(rail_mod)
        assert rail["unplaced"][0]["unplaced_reason"] == "section_not_in_version"

    def test_no_section_is_not_the_same_finding_as_an_unreadable_section(self, rail_mod):
        # `verify_anchor(row, None)` short-circuits at `section_missing`, which
        # this module maps to unverifiable/section_unreadable. For a row that
        # names NO section that answer is wrong: the truth is that nobody ever
        # recorded a span, and the two send a reader to different repairs.
        assert rail_mod.unplaced_anchor_state({"anchor_basis": None}) == {
            "state": "unanchored", "reason": "no_anchor_recorded",
            "relocation_candidate": None}
        assert rail_mod.unplaced_anchor_state({"anchor_basis": "exact"})["reason"] == (
            "no_section_of_record")
        assert rail_mod.change_anchor_state(None, {"anchor_basis": "exact"})["reason"] == (
            "section_unreadable")


# ── The anchor rule is imported, not re-derived ───────────────────────────────

class TestOneAnchorRule:
    def test_every_verify_reason_is_mapped(self, rail_mod):
        from tools.document_intelligence import suggestion_store
        assert set(rail_mod._STATE_BY_VERIFY_REASON) == set(suggestion_store.VERIFY_REASONS)

    def test_every_mapped_state_is_a_declared_anchor_state(self, rail_mod):
        from tools.document_intelligence.annotation_store import ANCHOR_STATES
        for state, _ in rail_mod._STATE_BY_VERIFY_REASON.values():
            assert state in ANCHOR_STATES

    def test_an_unmapped_reason_degrades_to_unverifiable_never_to_verified(self, rail_mod, monkeypatch):
        monkeypatch.setattr(rail_mod, "verify_anchor",
                            lambda s, t: {"ok": False, "reason": "brand_new_reason"})
        out = rail_mod.change_anchor_state(SECTION_A, {"anchor_basis": "exact"})
        assert out["state"] == "unverifiable"
        assert out["reason"] == "unmapped:brand_new_reason"

    def test_it_does_not_carry_its_own_copy_of_the_anchor_logic(self):
        """A behavioural test passes on the day somebody writes a local copy —
        the two agree until a document moves. Read the source instead."""
        src = (ROOT / "tools" / "document_intelligence" / "review_rail.py").read_text(encoding="utf-8")
        tree = ast.parse(src)
        imported = {a.name for n in ast.walk(tree) if isinstance(n, ast.ImportFrom)
                    for a in n.names}
        assert {"verify_anchor", "resolve_anchor", "section_of_record"} <= imported
        defined = {n.name for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)}
        assert not ({"verify_anchor", "resolve_anchor", "section_of_record",
                     "anchor_state"} & defined)

    def test_the_section_of_record_rule_has_one_statement(self):
        """The accept door and the rail must agree about which section a
        proposal belongs to, or the rail draws it beside one section while
        accept splices it into another."""
        from tools.document_intelligence.suggestion_store import section_of_record
        assert section_of_record({"anchor_section_id": "a", "section_id": "b"}) == "a"
        assert section_of_record({"section_id": "b"}) == "b"
        assert section_of_record({"section_id": ""}) == ""
        blueprint = (ROOT / "tools" / "document_intelligence" / "blueprint.py").read_text(encoding="utf-8")
        assert "section_of_record" in blueprint


# ── empty is measured; unmeasurable is not ────────────────────────────────────

class TestMeasuredVersusUnmeasurable:
    def test_a_document_nobody_has_touched_is_empty_with_real_zeroes(self, rail_mod, db):
        rail = _build(rail_mod)
        assert rail["state"] == "empty"
        assert rail["counts"]["items"] == 0
        assert rail["unplaced_count"] == 0

    def test_no_version_is_unmeasurable_with_none_counts_never_empty(self, rail_mod, db):
        rail = rail_mod.build_rail("doc-1", version_id=None)
        assert rail["state"] == "unmeasurable"
        assert rail["reason"] == "no_version"
        assert all(v is None for v in rail["counts"].values())
        assert rail["unplaced_count"] is None

    def test_an_unreadable_store_is_unmeasurable_not_empty(self, rail_mod, db, monkeypatch):
        monkeypatch.setattr(rail_mod, "_suggestions", lambda *a, **k: None)
        rail = _build(rail_mod)
        assert rail["state"] == "unmeasurable"
        assert rail["reason"] == "suggestions_unreadable"
        assert rail["counts"]["changes"] is None

    def test_positioned_and_unpositioned_are_two_numbers(self, rail_mod, db):
        _comment(needle="hardware token")
        _comment(comment="section-level")
        counts = _build(rail_mod)["counts"]
        assert counts["positioned"] == 1
        assert counts["unpositioned"] == 1
        assert counts["items"] == 2

    def test_unverifiable_is_counted_apart_from_unanchored(self, rail_mod, db):
        _comment(comment="section-level")            # unanchored
        _change(section_id="", suggested="x")        # unplaced, unanchored
        _change(section_id="sec-gone", needle="TLS 1.2", anchor_section_id="sec-gone")
        counts = _build(rail_mod)["counts"]
        assert counts["unanchored"] == 2
        assert counts["unverifiable"] == 1


# ── Threads survive the trip through the rail ─────────────────────────────────

class TestThreads:
    def test_a_reply_rides_on_its_root_and_never_becomes_its_own_item(self, rail_mod, db):
        root = _comment(needle="hardware token")
        from tools.document_intelligence import annotation_store as anns
        anns.create_annotation(section_id="sec-01", doc_id="doc-1", category="question",
                               comment="yes", author="u-bob", tenant_id="default",
                               parent_ann_id=root["ann_id"], anchor_basis="unanchored")
        sec = _build(rail_mod)["sections"][0]
        assert len(sec["positioned"]) + len(sec["unpositioned"]) == 1
        assert sec["positioned"][0]["reply_count"] == 1
        assert _build(rail_mod)["counts"]["comments"] == 1

    def test_a_resolved_thread_stays_on_the_rail_and_leaves_the_open_count(self, rail_mod, db):
        root = _comment(needle="hardware token")
        from tools.document_intelligence import annotation_store as anns
        anns.resolve_thread(root["ann_id"], resolved_by="u-alice")
        rail = _build(rail_mod)
        assert rail["counts"]["comments"] == 1
        assert rail["counts"]["open_comments"] == 0
        assert rail["sections"][0]["positioned"][0]["status"] == "resolved"


# ── Identity: the reviewer is the authenticated account ───────────────────────

@pytest.fixture()
def app():
    import flask
    return flask.Flask(__name__)


def _ctx(**kw):
    from tools.security.security_context import SecurityContext
    return SecurityContext(**kw)


class TestIdentity:
    def test_a_security_context_dataclass_has_no_get(self):
        """The defect in one line: every reader in blueprint.py called `.get` on
        this object inside a bare `except`, so an AUTHENTICATED account read as
        the anonymous sentinel."""
        assert not hasattr(_ctx(user_id="u"), "get")

    def test_the_authenticated_account_is_the_author(self, app, monkeypatch):
        from flask import g
        from tools.document_intelligence import blueprint as bp
        monkeypatch.delenv("ICDEV_DIC_IDENTITY", raising=False)
        with app.test_request_context("/"):
            g.current_user = {"id": "u-alice", "role": "reviewer"}
            g.security_context = _ctx(user_id="u-alice", role="reviewer", tenant_id="acme")
            assert bp._current_user() == "u-alice"
            assert bp._security_context() == ("acme", "CUI")

    def test_a_dict_context_still_works(self, app, monkeypatch):
        """The Cortex service-key branch of auth stores a plain dict. Both
        shapes are real, and neither may fall through to the sentinel."""
        from flask import g
        from tools.document_intelligence import blueprint as bp
        monkeypatch.delenv("ICDEV_DIC_IDENTITY", raising=False)
        with app.test_request_context("/"):
            g.security_context = {"user_id": "cortex-svc:x", "tenant_id": "t1",
                                  "classification": "CUI"}
            assert bp._current_user() == "cortex-svc:x"
            assert bp._security_context() == ("t1", "CUI")

    def test_nobody_signed_in_is_still_the_anonymous_sentinel(self, app, monkeypatch):
        from tools.document_intelligence import blueprint as bp
        monkeypatch.delenv("ICDEV_DIC_IDENTITY", raising=False)
        with app.test_request_context("/"):
            assert bp._current_user() == bp.ANONYMOUS_USER

    def test_a_signed_in_account_no_longer_inherits_the_sentinels_admin(self, app, db, monkeypatch):
        """The serious half. `_user_role` grants the sentinel ADMIN so a
        single-user dashboard is usable — and because the identity read failed,
        every one of the 13 real accounts on the live board reached that branch
        (measured 2026-09-08)."""
        from flask import g
        from tools.document_intelligence import blueprint as bp
        monkeypatch.delenv("ICDEV_DIC_IDENTITY", raising=False)
        with app.test_request_context("/"):
            g.current_user = {"id": "u-pm", "role": "pm"}
            g.security_context = _ctx(user_id="u-pm", role="pm")
            assert bp._user_role("coll-1", bp._current_user()) == "viewer"

    def test_the_platform_role_is_the_grant_a_signed_in_account_holds(self, app, db, monkeypatch):
        """`dic_team_access` holds demo string ids while `dashboard_users.id` is
        a UUID, so NOT ONE real account can match a grant row — falling straight
        through to `viewer` would lock every account out of its own canvas."""
        from flask import g
        from tools.document_intelligence import blueprint as bp
        monkeypatch.delenv("ICDEV_DIC_IDENTITY", raising=False)
        for platform, expected in (("admin", "admin"), ("reviewer", "reviewer"),
                                   ("bd", "viewer"), ("capture_mgr", "viewer")):
            with app.test_request_context("/"):
                g.current_user = {"id": "u-x", "role": platform}
                g.security_context = _ctx(user_id="u-x", role=platform)
                assert bp._user_role("coll-1", bp._current_user()) == expected, platform

    def test_an_explicit_grant_outranks_the_platform_role(self, app, db, monkeypatch):
        from flask import g
        from tools.db.storage import get_connection
        from tools.document_intelligence import blueprint as bp
        monkeypatch.delenv("ICDEV_DIC_IDENTITY", raising=False)
        with get_connection(db_path=str(db)) as conn:
            # `classification` and `tenant_id` are NOT decoration: get_connection
            # injects the row-security predicate inside a request, and a table
            # without them raises `no such column: classification` — which
            # `_user_role` swallows, so a narrow fixture reads as "no grant".
            conn.execute("CREATE TABLE IF NOT EXISTS dic_team_access "
                         "(collection_id TEXT, user_id TEXT, role TEXT, "
                         "tenant_id TEXT, classification TEXT)")
            conn.execute("INSERT INTO dic_team_access (collection_id, user_id, role) "
                         "VALUES ('coll-1','u-bd','admin')")
            conn.commit()
        with app.test_request_context("/"):
            g.current_user = {"id": "u-bd", "role": "bd"}
            g.security_context = _ctx(user_id="u-bd", role="bd")
            assert bp._user_role("coll-1", bp._current_user()) == "admin"

    def test_the_kill_switch_restores_the_previous_behaviour_exactly(self, app, db, monkeypatch):
        from flask import g
        from tools.document_intelligence import blueprint as bp
        monkeypatch.setenv("ICDEV_DIC_IDENTITY", "0")
        with app.test_request_context("/"):
            g.current_user = {"id": "u-pm", "role": "pm"}
            g.security_context = _ctx(user_id="u-pm", role="pm", tenant_id="acme")
            assert bp._current_user() == bp.ANONYMOUS_USER
            assert bp._security_context() == ("default", "CUI")
            assert bp._user_role("coll-1", bp._current_user()) == "admin"

    def test_an_unresolvable_author_renders_as_its_id_never_as_a_person(self, app, db, monkeypatch):
        """`dashboard_users` carries no `classification` column, so the
        row-security predicate rewrites every in-request read of it into a
        statement naming a column that does not exist. The id is then rendered
        as stored — stated, not dressed up."""
        from tools.document_intelligence import blueprint as bp
        monkeypatch.delenv("ICDEV_DIC_IDENTITY", raising=False)
        with app.test_request_context("/"):
            assert bp._display_name("u-nobody") == "u-nobody"

    def test_the_viewers_own_name_comes_from_memory_not_a_query(self, app, db, monkeypatch):
        from flask import g
        from tools.document_intelligence import blueprint as bp
        monkeypatch.delenv("ICDEV_DIC_IDENTITY", raising=False)

        def _refuse(*a, **k):  # any query here is the bug
            raise AssertionError("the viewer's name must not need a database read")

        monkeypatch.setattr("tools.dashboard.auth.get_user_by_id", _refuse)
        with app.test_request_context("/"):
            g.current_user = {"id": "u-alice", "display_name": "Alice Nakamura"}
            g.security_context = _ctx(user_id="u-alice")
            assert bp._display_name("u-alice") == "Alice Nakamura"


# ── The version rule has one statement ────────────────────────────────────────

class TestActiveVersion:
    def test_the_page_and_the_rail_ask_the_same_function(self):
        src = (ROOT / "tools" / "document_intelligence" / "blueprint.py").read_text(encoding="utf-8")
        # doc_detail and the rail route, and nothing that re-derives the rule.
        assert src.count("_active_version_id(") >= 3
        assert src.count("_OPEN_VERSION_STATUSES") == 2

    def test_the_newest_open_version_wins_then_the_newest(self):
        from tools.document_intelligence.blueprint import _active_version_id
        assert _active_version_id([{"version_id": "v3", "status": "approved"},
                                   {"version_id": "v2", "status": "draft"}]) == "v2"
        assert _active_version_id([{"version_id": "v3", "status": "approved"}]) == "v3"
        assert _active_version_id([]) == ""


# ── The route ─────────────────────────────────────────────────────────────────

_BASE = "/document-intelligence/api"


@pytest.fixture()
def client(db):
    import flask

    from tools.document_intelligence.blueprint import dic_bp
    app = flask.Flask(__name__)
    app.register_blueprint(dic_bp, url_prefix="/document-intelligence")
    with app.test_client() as c:
        yield c


class TestRoute:
    def test_it_returns_the_rail_and_names_the_viewer(self, client, db):
        _comment(needle="hardware token")
        _change(needle="TLS 1.2")
        out = client.get(f"{_BASE}/documents/doc-1/review-rail").get_json()
        assert out["state"] == "items"
        assert [i["kind"] for i in out["sections"][0]["positioned"]] == ["change", "comment"]
        assert out["viewer"]["authenticated"] is False   # nobody signed in here

    def test_an_unknown_document_is_unmeasurable_never_an_empty_rail(self, client, db):
        out = client.get(f"{_BASE}/documents/nope/review-rail").get_json()
        assert out["state"] == "unmeasurable"
        assert out["counts"]["items"] is None

    def test_it_is_read_only_and_has_no_post_sibling(self, client, db):
        assert client.post(f"{_BASE}/documents/doc-1/review-rail").status_code == 405

    def test_no_write_happens_through_it(self, client, db):
        """A rail that could write would be a fourth door onto the same rows.
        Every act it offers POSTs to a route that already owns it."""
        src = (ROOT / "tools" / "document_intelligence" / "review_rail.py").read_text(encoding="utf-8")
        tree = ast.parse(src)
        sql = [n.value for n in ast.walk(tree) if isinstance(n, ast.Constant)
               and isinstance(n.value, str)]
        for verb in ("INSERT ", "UPDATE ", "DELETE ", "commit("):
            assert not any(verb in s for s in sql), verb

    def test_the_annotations_route_attributes_to_the_server_not_the_caller(self, client, db):
        """The page no longer sends an `author`. A caller that supplies one is a
        separate question; what is pinned is that omitting it still attributes."""
        created = client.post(f"{_BASE}/sections/sec-01/annotations", json={
            "category": "question", "comment": "who owns this?",
            "anchor_text": "hardware token"}).get_json()
        assert created["author"]                       # never blank
        assert created["anchor"]["state"] == "verified"

    def test_the_page_sends_no_author_of_its_own(self):
        page = (ROOT / "tools" / "dashboard" / "templates" / "document_intelligence"
                / "doc_detail.html").read_text(encoding="utf-8")
        assert "author: _ANN_CURRENT_USER" not in page
        assert "resolved_by: _ANN_CURRENT_USER" not in page
