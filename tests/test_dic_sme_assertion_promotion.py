# CUI // SP-CTI
"""A comment is an INSTRUCTION until a human promotes it (dwr-ev-02).

The three things the card asks to be SHOWN, each asserted end to end against a
real database and the real ``entity_currency`` resolver:

1. a comment can be promoted, per-comment and deliberately;
2. the promoted comment is CITED as an attributed human source and is rendered
   DISTINCTLY from a document citation;
3. an UNPROMOTED comment cannot be cited at all.

Point 3 is the one that needs care. "It is not cited" is easy to assert
vacuously — against a database where nothing is cited, every comment passes. So
every test that asserts an absence does it beside a PRESENCE from the same
corpus in the same run: one comment promoted, one not, and the promoted one's
words reachable while the other's are not. An assertion that both are absent
would be the empty-denominator defect this repository has a census for.
"""
from __future__ import annotations

import ast
import importlib
import io
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]

sme_evidence = importlib.import_module("tools.document_intelligence.sme_evidence")
entity_currency = importlib.import_module("tools.currency.entity_currency")


# -- fixtures ----------------------------------------------------------------

#: Only the tables these tests touch, created from MINIMAL_ICDEV_SCHEMA — the
#: same discipline tests/currency/test_entity_currency.py uses, so the shapes
#: under test are the shipped ones rather than a fixture's paraphrase of them.
#: The COMMENT table is NOT among them: dwr-cmt-01 gave it a store that owns its
#: schema, so it is created by that store and there is one copy of the shape.
_SCHEMA_KEYS = ("entity_currency", "dic_sme_assertions")

annotation_store = importlib.import_module(
    "tools.document_intelligence.annotation_store")


@pytest.fixture()
def conn(tmp_path, monkeypatch):
    """An isolated SQLite database. NEVER a bare ``get_connection()``: one in a
    test writes the ambient checkout's data/icdev.db."""
    monkeypatch.setenv("ICDEV_DB_PATH", str(tmp_path / "sme.db"))
    from tests.conftest import MINIMAL_ICDEV_SCHEMA
    from tools.db.storage import get_connection

    c = get_connection()
    for stmt in MINIMAL_ICDEV_SCHEMA.split(";"):
        if "CREATE" in stmt and any(k in stmt for k in _SCHEMA_KEYS):
            c.execute(stmt)
    annotation_store._ensure_tables(c)
    c.commit()
    yield c
    c.close()


def _comment(conn, ann_id, comment, author, created_at, *, doc_id="doc-1",
             section_id="sec-1", category="compliance", parent_ann_id=None):
    """One comment, written by the SHIPPED comment writer (dwr-cmt-01).

    Not a raw INSERT: a promotion has to work against what the real comment path
    produces — that path now sets thread and anchor columns a hand-built row
    would omit, and a fixture that paraphrases the row proves only that a
    promotion can read its own paraphrase.

    Two fields are then set explicitly, and both are the test's subject rather
    than a shortcut: the id, so a test can name the comment it is talking about,
    and ``created_at``, because the SME's clock DEFAULTS to it and a test about
    two clocks cannot have `now` on both sides.
    """
    row = annotation_store.create_annotation(
        section_id=section_id, doc_id=doc_id, category=category, comment=comment,
        author=author, parent_ann_id=parent_ann_id, conn=conn,
    )
    conn.execute(
        "UPDATE dic_section_annotations SET ann_id = %s, created_at = %s "
        "WHERE ann_id = %s",
        (ann_id, created_at, row["ann_id"]),
    )
    conn.commit()
    return ann_id


TLS_CLAIM = {
    "entity_label": "TLS 1.1",
    "entity_type": "crypto_protocol",
    "status": "retired",
    "as_of": "2026-07-01",
}


# -- 1. a comment can be promoted --------------------------------------------

def test_promotion_records_who_said_it_and_who_decided(conn):
    _comment(conn, "ann-1", "We retired TLS 1.1 estate-wide last quarter.",
             "nia.okoro", "2026-08-14T09:00:00+00:00")

    out = sme_evidence.promote_comment(
        conn, ann_id="ann-1", claim=TLS_CLAIM, promoted_by="lead.reviewer",
    )
    conn.commit()
    row = out["assertion"]

    # WHO SAID IT is copied off the comment; the promoter supplies the claim and
    # never the name attached to it.
    assert row["asserted_by"] == "nia.okoro"
    assert row["promoted_by"] == "lead.reviewer"
    assert row["asserted_by"] != row["promoted_by"]
    # TWO CLOCKS, never merged.
    assert row["as_of"].startswith("2026-07-01")
    assert row["as_of_basis"] == "sme_stated"
    assert row["promoted_at"] > row["as_of"]
    # The prose is the QUOTATION, kept verbatim, and the claim is typed.
    assert row["comment"] == "We retired TLS 1.1 estate-wide last quarter."
    assert row["status"] == "retired"
    assert row["entity_key"] == entity_currency.normalize_key("TLS 1.1")


def test_an_unstated_date_defaults_to_the_comments_clock_and_says_so(conn):
    _comment(conn, "ann-2", "Catalyst 6500 is still fielded in the north site.",
             "sme.two", "2026-05-02T11:30:00+00:00")

    out = sme_evidence.promote_comment(
        conn, ann_id="ann-2", promoted_by="lead",
        claim={"entity_label": "Catalyst 6500", "entity_type": "hardware_model",
               "status": "fielded"},
    )
    row = out["assertion"]
    # The SME's clock defaults to when THEY wrote it -- never to the promotion,
    # which may be months later -- and the basis says the date was defaulted.
    assert row["as_of"].startswith("2026-05-02")
    assert row["as_of_basis"] == "comment_time"
    assert row["as_of_basis"] in sme_evidence.AS_OF_BASES


def test_promotion_is_per_comment_and_a_second_one_is_refused(conn):
    _comment(conn, "ann-3", "TLS 1.1 is gone.", "sme.one", "2026-08-01T00:00:00+00:00")
    sme_evidence.promote_comment(conn, ann_id="ann-3", claim=TLS_CLAIM, promoted_by="lead")
    conn.commit()

    with pytest.raises(sme_evidence.AlreadyPromoted):
        sme_evidence.promote_comment(
            conn, ann_id="ann-3", claim=dict(TLS_CLAIM, status="current"),
            promoted_by="someone.else",
        )
    # And what the first human decided still stands, unrewritten.
    assert sme_evidence.list_assertions(ann_id="ann-3", conn=conn)[0]["status"] == "retired"


@pytest.mark.parametrize("bad, exc", [
    ({"entity_type": "crypto_protocol", "status": "retired"}, sme_evidence.SMEPromotionError),
    ({"entity_label": "TLS 1.1", "status": "retired"}, sme_evidence.SMEPromotionError),
    ({"entity_label": "TLS 1.1", "entity_type": "crypto_protocol",
      "status": "probably gone"}, sme_evidence.SMEPromotionError),
])
def test_a_claim_outside_the_closed_vocabulary_is_refused(conn, bad, exc):
    _comment(conn, "ann-bad", "something", "sme", "2026-08-01T00:00:00+00:00")
    with pytest.raises(exc):
        sme_evidence.promote_comment(conn, ann_id="ann-bad", claim=bad, promoted_by="lead")


def test_an_unnamed_promoter_is_refused(conn):
    _comment(conn, "ann-4", "TLS 1.1 is gone.", "sme.one", "2026-08-01T00:00:00+00:00")
    with pytest.raises(sme_evidence.SMEPromotionError):
        sme_evidence.promote_comment(conn, ann_id="ann-4", claim=TLS_CLAIM, promoted_by="  ")


def test_promoting_a_comment_that_does_not_exist_is_refused(conn):
    with pytest.raises(sme_evidence.CommentNotFound):
        sme_evidence.promote_comment(conn, ann_id="nope", claim=TLS_CLAIM, promoted_by="lead")


# -- 2. the promoted comment IS cited, as an attributed human source ----------

def test_the_promoted_comment_reaches_the_store_and_is_resolvable(conn):
    _comment(conn, "ann-5", "We retired TLS 1.1 estate-wide.", "nia.okoro",
             "2026-08-14T09:00:00+00:00")
    sme_evidence.promote_comment(conn, ann_id="ann-5", claim=TLS_CLAIM, promoted_by="lead")
    conn.commit()

    view = entity_currency.resolve("TLS 1.1", entity_type="crypto_protocol", conn=conn)
    assert view is not None
    assert view["source"] == sme_evidence.SOURCE_ID
    assert view["source_kind"] == sme_evidence.SOURCE_KIND
    assert view["verdict"] == "end_of_life"          # 'retired' through the YAML value_map
    assert view["precedence"] == 0                   # declared, beside the author source
    # The ATTRIBUTION survives into the resolved view, or the citation below
    # cannot name the human and is not an attributed citation at all.
    assert view["provenance"]["fields"]["asserted_by"] == "nia.okoro"
    assert view["provenance"]["fields"]["promoted_by"] == "lead"


def test_the_citation_is_typed_sme_assertion_and_names_the_human(conn, monkeypatch):
    """The citation a drafter would carry -- shaped by the real Cortex rung."""
    search_service = importlib.import_module("tools.cortex.search_service")

    _comment(conn, "ann-6", "We retired TLS 1.1 estate-wide.", "nia.okoro",
             "2026-08-14T09:00:00+00:00")
    sme_evidence.promote_comment(conn, ann_id="ann-6", claim=TLS_CLAIM, promoted_by="lead")
    conn.commit()

    views = entity_currency.search("is TLS 1.1 still current", limit=5, conn=conn)
    assert views, "the promoted assertion must be findable by the currency lane"
    result = search_service._currency_assertion_result(
        views[0], search_service.CortexContext()
    )

    # DISTINCT FROM A DOCUMENT CITATION, structurally: its own source_type, and
    # its own one that a feed's row does not share either.
    assert result.citation.source_type == "sme_assertion"
    assert result.citation.source_type != search_service._ASSERTION_SOURCE_TYPE
    assert result.citation.source_table == "dic_sme_assertions"
    # ATTRIBUTED IN WORDS, not only in a badge -- the snippet is what a reader of
    # a drafted paragraph actually sees.
    assert "nia.okoro" in result.content
    assert "attributed human statement, not a document" in result.content
    assert result.metadata["source_kind"] == "sme_attributed"


def test_a_machine_feeds_citation_is_unchanged(conn):
    """The new type is for HUMAN sources only: nothing that shipped before moves."""
    search_service = importlib.import_module("tools.cortex.search_service")

    feed_view = {"source_kind": "external_feed", "provenance": {"fields": {}}}
    assert search_service._assertion_source_type(feed_view) == "currency_assertion"
    assert search_service._attribution_clause(feed_view) == ""
    curated = {"source_kind": "curated", "provenance": {"fields": {"asserted_by": "x"}}}
    assert search_service._assertion_source_type(curated) == "currency_assertion"


# -- 3. an UNPROMOTED comment cannot be cited at all --------------------------

def test_an_unpromoted_comment_is_cited_by_nothing_while_a_promoted_one_is(conn):
    """The card's third clause, asserted against a NON-EMPTY corpus.

    Two comments, same document, same section, same review. One promoted. If the
    unpromoted one were merely absent because nothing was cited at all, the
    promoted one would be absent too -- so the presence is what makes the
    absence mean something.
    """
    _comment(conn, "ann-promoted", "We retired TLS 1.1 estate-wide.", "nia.okoro",
             "2026-08-14T09:00:00+00:00")
    _comment(conn, "ann-instruction",
             "Shorten this paragraph and drop the Catalyst 6500 reference.",
             "ed.reviewer", "2026-08-14T09:05:00+00:00")
    sme_evidence.promote_comment(
        conn, ann_id="ann-promoted", claim=TLS_CLAIM, promoted_by="lead")
    conn.commit()

    # PRESENCE: the promoted comment is in the store, and its words are citable.
    promoted_view = entity_currency.resolve(
        "TLS 1.1", entity_type="crypto_protocol", conn=conn)
    assert promoted_view is not None
    assert promoted_view["source"] == sme_evidence.SOURCE_ID

    # ABSENCE: the instruction named an entity too, and it reached nothing.
    assert entity_currency.resolve(
        "Catalyst 6500", entity_type="hardware_model", conn=conn) is None
    every_row = entity_currency.query(
        entity_currency.normalize_key("Catalyst 6500"), conn=conn)
    assert every_row == []

    # And no citation anywhere carries the instruction's prose.
    for view in entity_currency.search("Catalyst 6500 paragraph", limit=10, conn=conn):
        assert "Shorten this paragraph" not in str(view)

    # The store knows exactly one promoted comment, not two.
    assert len(sme_evidence.list_assertions(doc_id="doc-1", conn=conn)) == 1


def test_promotions_for_reports_the_state_from_the_assertion_table_only(conn):
    _comment(conn, "ann-p", "TLS 1.1 is gone.", "sme.one", "2026-08-01T00:00:00+00:00")
    _comment(conn, "ann-i", "Tighten the wording.", "ed", "2026-08-01T00:01:00+00:00")
    sme_evidence.promote_comment(conn, ann_id="ann-p", claim=TLS_CLAIM, promoted_by="lead")
    conn.commit()

    state = sme_evidence.promotions_for(conn, ["ann-p", "ann-i"])
    assert set(state) == {"ann-p"}          # absence IS the answer for ann-i
    assert state["ann-p"]["asserted_by"] == "sme.one"


# -- disagreement is reported, never landed silently -------------------------

def test_two_smes_disagreeing_are_two_rows_and_the_conflict_is_reported(conn):
    _comment(conn, "ann-a", "TLS 1.1 is retired here.", "sme.one",
             "2026-06-01T00:00:00+00:00")
    _comment(conn, "ann-b", "TLS 1.1 is still in use on the legacy VLAN.", "sme.two",
             "2026-08-01T00:00:00+00:00")

    sme_evidence.promote_comment(
        conn, ann_id="ann-a", promoted_by="lead",
        claim=dict(TLS_CLAIM, as_of="2026-06-01"))
    second = sme_evidence.promote_comment(
        conn, ann_id="ann-b", promoted_by="lead",
        claim=dict(TLS_CLAIM, status="in_use", as_of="2026-08-01"))
    conn.commit()

    # REPORTED, not resolved away, at the moment the second promotion is made.
    assert [c["ann_id"] for c in second["contradicts"]] == ["ann-a"]
    # BOTH statements stand; nothing overwrote anything.
    rows = sme_evidence.list_assertions(entity_key="TLS 1.1", conn=conn)
    assert {r["asserted_by"] for r in rows} == {"sme.one", "sme.two"}
    # The store carries the LATER human clock, per the declared order_by.
    view = entity_currency.resolve("TLS 1.1", entity_type="crypto_protocol", conn=conn)
    assert view["verdict"] == "current"
    assert view["provenance"]["fields"]["asserted_by"] == "sme.two"


# -- structural: there is no other door --------------------------------------

def _declared_sources():
    cfg = entity_currency.load_config()
    return {str(s["id"]): s for s in (cfg.get("sources") or [])}


def test_the_comment_table_is_declared_as_a_source_nowhere():
    """The whole of "an unpromoted comment cannot be cited": the comment table
    is not evidence, so no source declaration may name it."""
    for sid, spec in _declared_sources().items():
        assert spec.get("table") != sme_evidence.COMMENT_TABLE, sid


def test_the_sme_source_is_declared_beside_the_author_source():
    sources = _declared_sources()
    sme = sources[sme_evidence.SOURCE_ID]
    author = sources["dic_author_assertions"]
    assert sme["kind"] == sme_evidence.SOURCE_KIND
    assert sme["table"] == sme_evidence.TABLE
    # BESIDE, not above: two humans about this estate tie, and recency decides.
    assert sme["precedence"] == author["precedence"] == 0
    assert sme["confidence"] == author["confidence"]
    # ONE vocabulary for both, or a status word means two things.
    author_statuses = importlib.import_module(
        "tools.document_intelligence.author_evidence").AUTHOR_STATUSES
    assert set(sme["verdict"]["map"]) == set(author_statuses)
    assert sme["verdict"]["map"] == author["verdict"]["map"]


def test_no_evidence_seam_reads_the_comment_table():
    """AST, over the modules a claim can travel through.

    A behavioural test cannot see a future edit that adds a SELECT on
    dic_section_annotations to an evidence module, and that single edit is the
    only way an unpromoted comment could ever become citable.
    """
    seams = [
        "tools/cortex/search_service.py",
        "tools/cortex/entity_resolution.py",
        "tools/currency/entity_currency.py",
        "tools/doc_modernization/evidence.py",
        "tools/document_intelligence/docgen_evidence.py",
        "tools/document_intelligence/search_evidence.py",
        "tools/document_intelligence/docdrift_evidence.py",
    ]
    for rel in seams:
        path = REPO_ROOT / rel
        if not path.exists():
            continue
        text = io.open(path, encoding="utf-8").read()
        assert "dic_section_annotations" not in text, (
            f"{rel} names the comment table; an unpromoted comment must reach "
            f"no evidence seam"
        )


def test_the_promoter_never_parses_the_comment_prose():
    """AST over sme_evidence: the CLAIM comes from the promoter, and the comment
    text is only ever stored. A module that grew a regex over ``comment`` would
    be extracting an assertion from prose -- a ``text_pattern`` claim, which can
    never reach a pack (TRUST rule 2)."""
    tree = ast.parse(io.open(
        REPO_ROOT / "tools/document_intelligence/sme_evidence.py", encoding="utf-8"
    ).read())
    promote = next(n for n in ast.walk(tree)
                   if isinstance(n, ast.FunctionDef) and n.name == "promote_comment")
    called = {
        n.func.attr for n in ast.walk(promote)
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
    }
    for parser in ("search", "findall", "match", "finditer", "split", "sub"):
        assert parser not in called, (
            f"promote_comment calls .{parser}(): nothing here may read the prose"
        )
    # And no LLM anywhere in the module.
    src = io.open(REPO_ROOT / "tools/document_intelligence/sme_evidence.py",
                  encoding="utf-8").read()
    for banned in ("LLMRouter", "router.invoke", "cortex.complete", "openai", "anthropic"):
        assert banned not in src, f"sme_evidence must not reach a model ({banned})"


def test_there_is_no_bulk_promote_route():
    """One comment per decision. A bulk endpoint is the shape that turns a
    deliberate act into a heuristic, so its absence is asserted rather than
    assumed."""
    text = io.open(REPO_ROOT / "tools/document_intelligence/blueprint.py",
                   encoding="utf-8").read()
    assert '/api/annotations/<ann_id>/promote' in text
    for bulk in ("promote-batch", "promote-all", "promote_batch", "promote_all"):
        assert bulk not in text, f"a bulk promotion door exists: {bulk}"


# -- the DOOR: the route, its statuses, and its audit leg --------------------

class TestPromotionRoute:
    """The route is the surface a human actually presses.

    The blueprint alone, the tests/docmod/test_hitl_decision_wiring.py shape:
    the subject is this door's behaviour, not the dashboard's login.
    """

    @pytest.fixture()
    def client(self, conn, monkeypatch):
        import flask

        import tools.document_intelligence.blueprint as bp_mod

        # Every route in this blueprint opens its OWN connection; point them all
        # at the fixture's isolated database.
        monkeypatch.setattr(bp_mod, "_conn", lambda: conn)
        # ... and keep the route from closing the connection the fixture owns.
        monkeypatch.setattr(conn, "close", lambda: None, raising=False)

        app = flask.Flask(__name__)
        app.register_blueprint(bp_mod.dic_bp, url_prefix="/document-intelligence")
        app.config["TESTING"] = True
        with app.test_client() as c:
            yield c

    @staticmethod
    def _audited(monkeypatch):
        """Capture the HITL decision rows the route writes."""
        import tools.document_intelligence.blueprint as bp_mod

        seen = []
        monkeypatch.setattr(
            bp_mod, "_record_hitl_decision",
            lambda surface, item_id, decision, reviewer, details=None: seen.append(
                (surface, item_id, decision, reviewer, details or {})),
        )
        return seen

    def test_promoting_through_the_route_records_the_decision_first(
            self, conn, client, monkeypatch):
        audited = self._audited(monkeypatch)
        _comment(conn, "ann-r1", "We retired TLS 1.1 estate-wide.", "nia.okoro",
                 "2026-08-14T09:00:00+00:00")

        resp = client.post("/document-intelligence/api/annotations/ann-r1/promote",
                           json={"promoted_by": "lead.reviewer", "claim": TLS_CLAIM})
        assert resp.status_code == 201, resp.get_data(as_text=True)
        body = resp.get_json()
        assert body["promoted"] is True
        assert body["sme_assertion"]["asserted_by"] == "nia.okoro"

        # AUDITED AS A DECISION, naming both people and carrying the claim.
        assert len(audited) == 1
        surface, item_id, decision, reviewer, details = audited[0]
        assert (surface, item_id, decision, reviewer) == (
            "dic_annotation", "ann-r1", "promoted_to_sme_assertion", "lead.reviewer")
        assert details["asserted_by"] == "nia.okoro"
        assert details["claim"] == TLS_CLAIM
        # A promotion adds a citable source; it applies nothing to a document.
        assert details["applied"] is False

    def test_an_unauditable_promotion_does_not_happen(self, conn, client, monkeypatch):
        """FAIL-CLOSED. The audit is written BEFORE the promotion, and raises, so
        a promotion whose decision could not be recorded leaves no assertion."""
        import tools.document_intelligence.blueprint as bp_mod

        def _boom(*a, **k):
            raise RuntimeError("audit_trail unreachable")

        monkeypatch.setattr(bp_mod, "_record_hitl_decision", _boom)
        _comment(conn, "ann-r2", "TLS 1.1 is gone.", "sme", "2026-08-01T00:00:00+00:00")

        resp = client.post("/document-intelligence/api/annotations/ann-r2/promote",
                           json={"promoted_by": "lead", "claim": TLS_CLAIM})
        assert resp.status_code == 500
        assert sme_evidence.promotions_for(conn, ["ann-r2"]) == {}

    def test_the_route_refuses_the_three_ways_a_promotion_can_be_wrong(
            self, conn, client, monkeypatch):
        self._audited(monkeypatch)
        _comment(conn, "ann-r3", "TLS 1.1 is gone.", "sme", "2026-08-01T00:00:00+00:00")

        # unknown comment -> 404, and NO audit row about a comment that does not exist
        missing = client.post("/document-intelligence/api/annotations/nope/promote",
                              json={"promoted_by": "lead", "claim": TLS_CLAIM})
        assert missing.status_code == 404

        # a claim outside the closed vocabulary -> 400
        bad = client.post("/document-intelligence/api/annotations/ann-r3/promote",
                          json={"promoted_by": "lead",
                                "claim": {"entity_label": "TLS 1.1",
                                          "entity_type": "crypto_protocol",
                                          "status": "probably gone"}})
        assert bad.status_code == 400

        # a second promotion of the same comment -> 409, never a silent rewrite
        first = client.post("/document-intelligence/api/annotations/ann-r3/promote",
                            json={"promoted_by": "lead", "claim": TLS_CLAIM})
        assert first.status_code == 201
        again = client.post("/document-intelligence/api/annotations/ann-r3/promote",
                            json={"promoted_by": "other",
                                  "claim": dict(TLS_CLAIM, status="current")})
        assert again.status_code == 409
        assert sme_evidence.list_assertions(
            ann_id="ann-r3", conn=conn)[0]["status"] == "retired"

    def test_the_comments_api_marks_the_promoted_one_and_only_it(
            self, conn, client, monkeypatch):
        """What the page renders from. `citable` is read from the assertion table
        every time, never from a flag on the comment that could go stale."""
        self._audited(monkeypatch)
        _comment(conn, "ann-r4", "We retired TLS 1.1 estate-wide.", "nia.okoro",
                 "2026-08-14T09:00:00+00:00")
        _comment(conn, "ann-r5", "Shorten this paragraph.", "ed",
                 "2026-08-14T09:05:00+00:00")
        client.post("/document-intelligence/api/annotations/ann-r4/promote",
                    json={"promoted_by": "lead", "claim": TLS_CLAIM})

        listing = client.get(
            "/document-intelligence/api/sections/sec-1/annotations").get_json()
        by_id = {a["ann_id"]: a for a in listing["annotations"]}
        assert by_id["ann-r4"]["citable"] is True
        assert by_id["ann-r4"]["sme_assertion"]["asserted_by"] == "nia.okoro"
        # The instruction is marked NOT citable, and carries no assertion at all.
        assert by_id["ann-r5"]["citable"] is False
        assert by_id["ann-r5"]["sme_assertion"] is None

    def test_the_read_route_separates_not_promoted_from_could_not_tell(
            self, conn, client, monkeypatch):
        self._audited(monkeypatch)
        _comment(conn, "ann-r6", "Tighten the wording.", "ed", "2026-08-01T00:00:00+00:00")

        body = client.get(
            "/document-intelligence/api/annotations/ann-r6/promote").get_json()
        # A MEASURED "nobody promoted this" ...
        assert body == {"ann_id": "ann-r6", "promoted": False,
                        "sme_assertion": None, "measured": True}

        # ... is never the same answer as "the store could not be read", which is
        # `promoted: None` and never False.
        import tools.document_intelligence.sme_evidence as mod

        def _unreadable(*a, **k):
            raise RuntimeError("store unreachable")

        monkeypatch.setattr(mod, "promotions_for", _unreadable)
        unmeasured = client.get(
            "/document-intelligence/api/annotations/ann-r6/promote").get_json()
        assert unmeasured["measured"] is False
        assert unmeasured["promoted"] is None


# -- rendered DISTINCTLY from a document citation ----------------------------

class TestRenderedDistinctly:
    """The card's second clause is about what a READER sees.

    The Jinja half is RENDERED and the two outputs compared, so "distinct" is a
    measured difference rather than a claim about a template. The JS half cannot
    be executed without a browser, so it is asserted STRUCTURALLY -- and the two
    halves are asserted to carry the same branch, because a surface whose server
    render and client render disagree about what a reader is looking at is worse
    than one that never distinguished them.
    """

    TEMPLATES = REPO_ROOT / "tools" / "dashboard" / "templates"
    MIRROR = REPO_ROOT / "icdev" / "tools" / "dashboard" / "templates"

    @staticmethod
    def _citation_block(source_type):
        """Render DocDrift's citation loop for one citation of a given type."""
        import re

        from jinja2 import Environment

        html = io.open(
            TestRenderedDistinctly.TEMPLATES / "document_intelligence" / "docdrift.html",
            encoding="utf-8",
        ).read()
        # The loop body, lifted verbatim out of the shipped page: rendering the
        # whole template would need the page's entire context, and a paraphrase
        # of the block would test the paraphrase.
        start = html.index('{% for c in r.citations %}')
        end = html.index('{% endfor %}', start) + len('{% endfor %}')
        body = html[start:end]
        rendered = Environment(autoescape=True).from_string(body).render(
            r={"citations": [{"source_id": "sme-abc", "source_table": "t",
                              "source_type": source_type, "snippet": "s"}]}
        )
        return re.sub(r"\s+", " ", rendered)

    def test_an_sme_assertion_does_not_render_like_a_document_citation(self):
        sme = self._citation_block("sme_assertion")
        doc = self._citation_block("dic_document")
        feed = self._citation_block("currency_assertion")

        # It SAYS what it is, in words a reader does not have to decode.
        assert "ATTRIBUTED HUMAN SOURCE" in sme
        assert "ATTRIBUTED HUMAN SOURCE" not in doc
        assert "ATTRIBUTED HUMAN SOURCE" not in feed
        # ... and it does not merely say it: the frame differs too, so the two
        # are distinguishable at a glance and not only by reading the chip.
        assert "#b45309" in sme and "#b45309" not in doc
        assert sme != doc != feed
        # A machine feed still renders exactly like every other non-pack
        # citation -- this card moved the HUMAN case and nothing else.
        assert feed.replace("currency_assertion", "dic_document") == doc

    def test_both_halves_of_both_surfaces_carry_the_distinction(self):
        """Jinja and JS, repo copy and packaged mirror -- four files."""
        for root in (self.TEMPLATES, self.MIRROR):
            drift = io.open(root / "document_intelligence" / "docdrift.html",
                            encoding="utf-8").read()
            # Jinja branch AND JS branch, in the one file that carries both.
            assert "{% elif c.source_type == 'sme_assertion' %}" in drift
            assert "c.source_type === 'sme_assertion'" in drift
            # The CHIP TEXT itself, once per half. Counted on the closing
            # marker so a reworded tooltip (which also carries the phrase)
            # cannot make this brittle.
            assert drift.count(">ATTRIBUTED HUMAN SOURCE<") == 2

            detail = io.open(root / "document_intelligence" / "doc_detail.html",
                             encoding="utf-8").read()
            # The comments panel: a promoted comment gets its own block, an
            # unpromoted one is labelled an instruction, and the promote button
            # exists only on the unpromoted branch.
            assert "_renderSmeAssertion" in detail
            assert "ATTRIBUTED HUMAN SOURCE" in detail
            assert "Instruction only" in detail
            assert "cited by nothing" in detail
            assert "Mark as SME assertion" in detail
            assert "promoteAnnotation" in detail
            # Both clocks and both people are on the card, or the render is not
            # an attribution.
            for field in ("asserted_by", "promoted_by", "as_of", "promoted_at",
                          "as_of_basis"):
                assert field in detail, field
