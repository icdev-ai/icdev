# CUI // SP-CTI
"""dwr-anchor-06 — re-draft the suggestions written against a token.

The 58 pending ``dic_suggestions`` on the live board carry an entity label as
their "before" text and an empty ``section_id``. They cannot be repaired in
place, so each is superseded and its finding re-drafted from a real passage.

What these tests hold down, in the order the module's safety depends on it:

  * the RUN-LEVEL refusal. A drafter with no ``resolve_passage`` predates
    dwr-anchor-04 and would write another unanchored row, so nothing is
    superseded at all -- proven by counting the decision chain, not by reading
    a verdict string.
  * every refusal REACHED BY NAME from primary data: no origin, a non-open
    origin, a finding with no span, a document with no sections.
  * the dry run writes nothing -- no status change, no decision row, no draft.
  * apply: supersede -> re-draft -> CONFIRM, and the confirmation is a re-read
    of the stored row, not the drafter's own claim.
  * the DONE criterion, end to end: the before/after census by ``anchor_basis``,
    and NO re-drafted suggestion carrying an empty ``section_id``.
  * the loop is closed BY CONSTRUCTION -- an anchored row is not a target, so a
    second run has nothing to do.
  * structurally, this module is not a second writer: it never UPDATEs or
    INSERTs ``dic_suggestions`` itself and goes through
    ``supersede_suggestion`` (dwr-anchor-05), read from the AST.

The database is the per-module SQLite file tests/docmod/conftest.py points
``tools.db.storage`` at.
"""
from __future__ import annotations

import ast
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from tools.document_intelligence import suggestion_redraft as rd  # noqa: E402
from tools.document_intelligence import suggestion_store as store  # noqa: E402

SECTION = (
    "The enclave shall use TLS 1.1 for transport between the bastion and the "
    "management plane. Operators must rotate credentials quarterly."
)
PASSAGE = "The enclave shall use TLS 1.1 for transport between the bastion and the management plane."


# -- Fixtures -----------------------------------------------------------------

def _conn():
    from tools.db.storage import get_connection
    return get_connection()


FINDING_COLUMNS = (
    "finding_id TEXT PRIMARY KEY", "run_id TEXT", "doc_id TEXT", "version_id TEXT",
    "chunk_link_id TEXT", "section_heading TEXT", "page INTEGER", "pack_id TEXT",
    "entity_label TEXT", "entity_type TEXT", "finding_type TEXT",
    "currency_verdict TEXT", "severity TEXT", "rationale TEXT", "evidence_json TEXT",
    "recommended_replacement TEXT", "replacement_evidence_json TEXT",
    "confidence REAL", "state TEXT", "supersedes_id TEXT",
    "redline_suggestion_id TEXT", "dedupe_key TEXT", "created_at TEXT",
    "tenant_id TEXT", "classification TEXT", "anchor_start INTEGER",
    "anchor_end INTEGER", "anchor_text TEXT",
)


@pytest.fixture(autouse=True)
def _schema():
    """GUARANTEE the shape this file needs, never assume it (dwr-anchor-02).

    ``docmod_findings`` and ``dic_sections`` are created by other modules with
    other shapes; ``CREATE TABLE IF NOT EXISTS`` would silently keep whichever
    ran first. Dropping first is what makes the fixture a guarantee.
    """
    with _conn() as conn:
        store._ensure_tables(conn)
        for table in ("docmod_findings", "dic_sections"):
            conn.execute(f"DROP TABLE IF EXISTS {table}")
        conn.execute(f"CREATE TABLE docmod_findings ({', '.join(FINDING_COLUMNS)})")
        conn.execute(
            "CREATE TABLE dic_sections (section_id TEXT PRIMARY KEY, version_id TEXT, "
            "doc_id TEXT, heading TEXT, content TEXT)"
        )
        conn.execute("DELETE FROM dic_suggestions")
        conn.execute("DELETE FROM dic_suggestion_decisions")
        conn.commit()
    yield


def _seed_finding(finding_id="fnd-origin", *, state="open", doc_id="doc-1",
                  span=(0, 7), heading="Transport", version_id="v1"):
    start, end = (None, None) if span is None else span
    with _conn() as conn:
        conn.execute(
            "INSERT INTO docmod_findings (finding_id, run_id, doc_id, version_id, "
            "section_heading, pack_id, entity_label, finding_type, currency_verdict, "
            "state, dedupe_key, created_at, anchor_start, anchor_end, anchor_text) "
            "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
            (finding_id, "run-1", doc_id, version_id, heading, "crypto_protocols",
             "TLS 1.1", "deprecated", "deprecated", state, f"dk-{finding_id}",
             "2026-09-08T00:00:00+00:00", start, end,
             None if start is None else SECTION[start:end]),
        )
        conn.commit()
    return finding_id


def _seed_section(doc_id="doc-1", version_id="v1", heading="Transport"):
    with _conn() as conn:
        conn.execute(
            "INSERT INTO dic_sections (section_id, version_id, doc_id, heading, content) "
            "VALUES (%s,%s,%s,%s,%s)",
            ("sec-1", version_id, doc_id, heading, SECTION),
        )
        conn.commit()
    return "sec-1"


def _seed_unanchored_suggestion(finding_id="fnd-origin", *, doc_id="doc-1",
                                link=True, rationale=None):
    """The shape the broken drafter wrote: entity label as the before text,
    empty section_id, no basis."""
    sid = store.create_suggestion(
        doc_id=doc_id, section_id="", collection_id="",
        canvas_source=rd.CANVAS_SOURCE,
        suggested_content="TLS 1.2 or higher [source: rule:crypto-tls-02]",
        current_content="TLS 1.1",
        rationale=(f"[docmod:{finding_id}] TLS 1.1 is deprecated."
                   if rationale is None else rationale),
        anchor_basis="unanchored", anchor_text="TLS 1.1",
    )
    if link:
        with _conn() as conn:
            conn.execute(
                "INSERT INTO docmod_findings (finding_id, run_id, doc_id, version_id, "
                "pack_id, entity_label, finding_type, currency_verdict, state, "
                "supersedes_id, redline_suggestion_id, dedupe_key, created_at) "
                "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,'redline_drafted',%s,%s,%s,%s)",
                (f"fnd-succ-{sid[-6:]}", "run-1", doc_id, "v1", "crypto_protocols",
                 "TLS 1.1", "deprecated", "deprecated", finding_id, sid,
                 f"dk-{finding_id}", "2026-09-08T00:01:00+00:00"),
            )
            conn.commit()
    return sid


def _decisions(suggestion_id):
    return store.get_decisions_for_suggestion(suggestion_id)


class _Result:
    """The ``RedlineResult`` shape ``redraft`` consumes -- only ``to_dict``."""

    def __init__(self, **kw):
        self._d = {"finding_id": "", "status": "drafted", "suggestion_id": None,
                   "reason": "", **kw}

    def to_dict(self):
        return dict(self._d)


@pytest.fixture
def anchoring_drafter(monkeypatch):
    """A drafter that ANCHORS, standing in for dwr-anchor-04.

    ``resolve_passage`` only has to EXIST -- ``drafter_anchors`` asks for the
    symbol, which is what tells a pre-04 tree from a post-04 one. ``draft_redline``
    writes a real anchored row through the real ``create_suggestion``, so the
    confirm step is exercised against the store's own validation rather than a
    stub's promise.
    """
    from tools.doc_modernization import redline_drafter

    monkeypatch.setattr(redline_drafter, "resolve_passage",
                        lambda conn, finding: None, raising=False)

    def _draft(finding_id, conn=None):
        section_id = "sec-1"
        start = SECTION.index(PASSAGE)
        new_id = store.create_suggestion(
            doc_id="doc-1", section_id=section_id, collection_id="",
            canvas_source=rd.CANVAS_SOURCE,
            suggested_content="The enclave shall use TLS 1.2 or higher "
                              "[source: rule:crypto-tls-02]",
            current_content=PASSAGE,
            rationale=f"[docmod:{finding_id}] re-drafted from the passage",
            anchor_section_id=section_id, anchor_start=start,
            anchor_end=start + len(PASSAGE), anchor_text=PASSAGE,
            anchor_basis="exact", origin_kind="docmod_redline",
        )
        return _Result(finding_id=finding_id, status="drafted", suggestion_id=new_id)

    monkeypatch.setattr(redline_drafter, "draft_redline", _draft)
    return redline_drafter


# -- The target predicate -----------------------------------------------------

class TestTargetPredicate:
    def test_unanchored_pending_docmod_row_is_a_target(self):
        assert rd.is_target({"status": "pending", "canvas_source": rd.CANVAS_SOURCE,
                             "anchor_basis": None, "section_id": ""})

    def test_an_anchored_row_is_not_a_target(self):
        assert not rd.is_target({"status": "pending", "canvas_source": rd.CANVAS_SOURCE,
                                 "anchor_basis": "exact", "anchor_section_id": "sec-1"})

    @pytest.mark.parametrize("basis,section", [
        ("exact", ""),          # a basis with nowhere to apply it
        (None, "sec-1"),        # a section with no proven basis
        ("unanchored", "sec-1"),
    ])
    def test_either_half_missing_is_still_unappliable(self, basis, section):
        assert rd.is_target({"status": "pending", "canvas_source": rd.CANVAS_SOURCE,
                             "anchor_basis": basis, "anchor_section_id": section})

    def test_another_canvas_is_never_a_target(self):
        assert not rd.is_target({"status": "pending", "canvas_source": "crowdsource",
                                 "anchor_basis": None, "section_id": ""})

    def test_a_decided_row_is_never_a_target(self):
        for status in ("accepted", "rejected", "superseded"):
            assert not rd.is_target({"status": status, "canvas_source": rd.CANVAS_SOURCE,
                                     "anchor_basis": None, "section_id": ""})


# -- The drafter's own capability ---------------------------------------------

class TestDrafterCapability:
    def test_a_drafter_without_resolve_passage_does_not_anchor(self, monkeypatch):
        from tools.doc_modernization import redline_drafter
        monkeypatch.delattr(redline_drafter, "resolve_passage", raising=False)
        ok, why = rd.drafter_anchors()
        assert ok is False
        assert "resolve_passage" in why

    def test_a_drafter_with_resolve_passage_anchors(self, monkeypatch):
        from tools.doc_modernization import redline_drafter
        monkeypatch.setattr(redline_drafter, "resolve_passage",
                            lambda conn, finding: None, raising=False)
        ok, _ = rd.drafter_anchors()
        assert ok is True


# -- Resolving the origin finding ---------------------------------------------

class TestOriginFinding:
    def test_the_structured_link_is_asked_first(self):
        _seed_finding("fnd-origin")
        sid = _seed_unanchored_suggestion("fnd-origin")
        with _conn() as conn:
            sug = store.get_suggestion(sid)
            finding, route, _ = rd.origin_finding(conn, sug)
        assert route == "link"
        assert finding["finding_id"] == "fnd-origin"

    def test_the_rationale_prefix_is_the_fallback(self):
        _seed_finding("fnd-origin")
        sid = _seed_unanchored_suggestion("fnd-origin", link=False)
        with _conn() as conn:
            finding, route, _ = rd.origin_finding(conn, store.get_suggestion(sid))
        assert route == "rationale"
        assert finding["finding_id"] == "fnd-origin"

    def test_two_routes_that_disagree_resolve_to_nothing(self):
        _seed_finding("fnd-origin")
        _seed_finding("fnd-other")
        sid = _seed_unanchored_suggestion("fnd-origin",
                                          rationale="[docmod:fnd-other] mismatched")
        with _conn() as conn:
            finding, route, reason = rd.origin_finding(conn, store.get_suggestion(sid))
        assert finding is None and route == "conflict"
        assert "fnd-origin" in reason and "fnd-other" in reason

    def test_no_link_and_no_prefix_resolves_to_nothing(self):
        sid = _seed_unanchored_suggestion(link=False, rationale="no marker here")
        with _conn() as conn:
            finding, route, reason = rd.origin_finding(conn, store.get_suggestion(sid))
        assert finding is None and route == "none"
        assert "rationale prefix" in reason


# -- Probe: every refusal reached by name -------------------------------------

class TestProbe:
    def _probe(self, sid, anchors=True):
        with _conn() as conn:
            return rd.probe(conn, store.get_suggestion(sid), anchors=anchors)

    def test_a_pre_04_drafter_refuses_before_reading_anything(self):
        sid = _seed_unanchored_suggestion()
        p = self._probe(sid, anchors=False)
        assert p["verdict"] == "drafter_does_not_anchor"
        assert p["finding_id"] is None      # nothing was even looked up

    def test_an_unresolvable_origin_is_named(self):
        sid = _seed_unanchored_suggestion(link=False, rationale="nothing")
        assert self._probe(sid)["verdict"] == "origin_unresolved"

    def test_a_finding_that_is_not_open_is_named(self):
        _seed_finding("fnd-origin", state="redline_drafted")
        sid = _seed_unanchored_suggestion("fnd-origin")
        p = self._probe(sid)
        assert p["verdict"] == "origin_not_open"
        assert "redline_drafted" in p["reason"]

    def test_a_finding_with_no_span_is_named_and_sends_you_to_a_rescan(self):
        _seed_finding("fnd-origin", span=None)
        _seed_section()
        sid = _seed_unanchored_suggestion("fnd-origin")
        p = self._probe(sid)
        assert p["verdict"] == "finding_has_no_span"
        assert p["finding_span"] is None
        assert "Re-scan" in p["reason"]

    def test_a_document_with_no_sections_is_named(self):
        _seed_finding("fnd-origin")           # has a span; no dic_sections row
        sid = _seed_unanchored_suggestion("fnd-origin")
        p = self._probe(sid)
        assert p["verdict"] == "doc_has_no_sections"
        assert p["doc_sections"] == 0

    def test_span_plus_sections_plus_open_is_redraftable(self):
        _seed_finding("fnd-origin")
        _seed_section()
        sid = _seed_unanchored_suggestion("fnd-origin")
        p = self._probe(sid)
        assert p["verdict"] == rd.REDRAFTABLE
        assert p["finding_span"] == [0, 7]
        assert p["doc_sections"] == 1

    def test_every_refusal_is_a_declared_name(self):
        # A verdict outside the enumeration would be invisible to a reader of
        # REFUSALS; the module's own list is the contract.
        assert set(rd.REFUSALS) == {
            "drafter_does_not_anchor", "origin_unresolved", "origin_not_open",
            "finding_has_no_span", "doc_has_no_sections",
        }


# -- The dry run writes nothing -----------------------------------------------

class TestDryRunWritesNothing:
    def test_plan_changes_no_status_and_records_no_decision(self, anchoring_drafter):
        _seed_finding("fnd-origin")
        _seed_section()
        sid = _seed_unanchored_suggestion("fnd-origin")

        report = rd.redraft(apply=False)

        assert report["applied"] is False
        assert report["redraftable"] == 1
        assert report["by_outcome"] == {"would_redraft": 1}
        assert store.get_suggestion(sid)["status"] == "pending"
        assert _decisions(sid) == []
        assert report["census_after"] is None

    def test_plan_alone_acts_on_nothing(self, anchoring_drafter):
        _seed_finding("fnd-origin")
        _seed_section()
        sid = _seed_unanchored_suggestion("fnd-origin")
        rd.plan()
        assert store.get_suggestion(sid)["status"] == "pending"
        assert _decisions(sid) == []


# -- The run-level refusal ----------------------------------------------------

class TestRunLevelRefusal:
    def test_a_pre_04_drafter_supersedes_nothing_even_under_apply(self, monkeypatch):
        from tools.doc_modernization import redline_drafter
        monkeypatch.delattr(redline_drafter, "resolve_passage", raising=False)
        _seed_finding("fnd-origin")
        _seed_section()
        sid = _seed_unanchored_suggestion("fnd-origin")

        report = rd.redraft(apply=True)

        assert report["drafter_anchors"] is False
        assert report["redraftable"] == 0
        assert report["by_verdict"] == {"drafter_does_not_anchor": 1}
        # The claim that matters is the ABSENCE of a write, not the verdict text.
        assert store.get_suggestion(sid)["status"] == "pending"
        assert _decisions(sid) == []


# -- Apply: supersede -> re-draft -> confirm ----------------------------------

class TestApply:
    def test_the_old_row_is_superseded_on_the_append_only_chain(self, anchoring_drafter):
        _seed_finding("fnd-origin")
        _seed_section()
        sid = _seed_unanchored_suggestion("fnd-origin")

        rd.redraft(apply=True)

        assert store.get_suggestion(sid)["status"] == "superseded"
        rows = _decisions(sid)
        assert len(rows) == 1
        assert rows[0]["decision"] == store.SUPERSEDED_DECISION
        # The mechanism, never a person -- a reader must not read this as a verdict.
        assert rows[0]["decided_by"] == rd.REDRAFT_ACTOR
        assert rd.SUPERSEDE_REASON in rows[0]["note"]

    def test_the_replacement_is_anchored_and_confirmed_by_re_reading_it(
            self, anchoring_drafter):
        _seed_finding("fnd-origin")
        _seed_section()
        _seed_unanchored_suggestion("fnd-origin")

        report = rd.redraft(apply=True)

        assert report["by_outcome"] == {"redrafted": 1}
        entry = report["outcomes"][0]
        fresh = store.get_suggestion(entry["new_suggestion_id"])
        assert fresh["anchor_basis"] in store.APPLIABLE_BASES
        assert fresh["anchor_section_id"] == "sec-1"
        # The passage, not the token -- what the model was shown IS what is pinned.
        assert fresh["current_content"] == PASSAGE
        assert fresh["anchor_text"] == PASSAGE

    def test_the_done_criterion_no_redrafted_row_has_an_empty_section_id(
            self, anchoring_drafter):
        for i in range(3):
            _seed_finding(f"fnd-{i}")
            _seed_unanchored_suggestion(f"fnd-{i}")
        _seed_section()

        report = rd.redraft(apply=True)

        before = report["census_before"]["by_status"]["pending"]
        after = report["census_after"]["by_status"]
        assert before["total"] == 3 and before["anchored"] == 0
        assert before["empty_section_id"] == 3
        assert after["pending"]["total"] == 3
        assert after["pending"]["anchored"] == 3
        assert after["pending"]["empty_section_id"] == 0
        assert after["pending"]["by_basis"] == {"exact": 3}
        assert after["superseded"]["total"] == 3

    def test_a_draft_that_abstains_leaves_the_finding_open_and_says_so(
            self, anchoring_drafter, monkeypatch):
        monkeypatch.setattr(
            anchoring_drafter, "draft_redline",
            lambda fid, conn=None: _Result(finding_id=fid, status="abstained",
                                           reason="confidence below abstain threshold"))
        _seed_finding("fnd-origin")
        _seed_section()
        sid = _seed_unanchored_suggestion("fnd-origin")

        report = rd.redraft(apply=True)

        assert report["by_outcome"] == {"abstained": 1}
        assert report["outcomes"][0]["new_suggestion_id"] is None
        assert "abstain" in report["outcomes"][0]["reason"]
        # The unappliable row is gone; nothing appliable was lost, and the
        # finding is still open for the next sweep.
        assert store.get_suggestion(sid)["status"] == "superseded"

    def test_a_drafter_that_raises_is_an_error_not_a_success(
            self, anchoring_drafter, monkeypatch):
        def _boom(fid, conn=None):
            raise RuntimeError("router unavailable")
        monkeypatch.setattr(anchoring_drafter, "draft_redline", _boom)
        _seed_finding("fnd-origin")
        _seed_section()
        _seed_unanchored_suggestion("fnd-origin")

        report = rd.redraft(apply=True)

        assert report["by_outcome"] == {"error": 1}
        assert "router unavailable" in report["outcomes"][0]["reason"]

    def test_an_unanchored_replacement_is_reported_never_counted_as_redrafted(
            self, anchoring_drafter, monkeypatch):
        def _unanchored(fid, conn=None):
            new_id = store.create_suggestion(
                doc_id="doc-1", section_id="", canvas_source=rd.CANVAS_SOURCE,
                suggested_content="x [source: s]", current_content="TLS 1.1",
                rationale=f"[docmod:{fid}] still broken", anchor_basis="unanchored")
            return _Result(finding_id=fid, status="drafted", suggestion_id=new_id)
        monkeypatch.setattr(anchoring_drafter, "draft_redline", _unanchored)
        _seed_finding("fnd-origin")
        _seed_section()
        _seed_unanchored_suggestion("fnd-origin")

        report = rd.redraft(apply=True)

        assert report["by_outcome"] == {"redrafted_unanchored": 1}


# -- The bound, and the loop --------------------------------------------------

class TestBoundIsReported:
    def test_deferred_items_come_back_named(self, anchoring_drafter):
        for i in range(4):
            _seed_finding(f"fnd-{i}")
            _seed_unanchored_suggestion(f"fnd-{i}")
        _seed_section()

        report = rd.redraft(limit=2, apply=True)

        assert report["max_per_run"] == 2
        assert report["by_outcome"] == {"redrafted": 2}
        assert len(report["deferred"]) == 2
        # NAMED, never a count: a reader can go and look at each one.
        assert all(d.startswith("sug_") for d in report["deferred"])

    def test_a_zero_budget_acts_on_nothing_and_defers_everything(self, anchoring_drafter):
        _seed_finding("fnd-origin")
        _seed_section()
        sid = _seed_unanchored_suggestion("fnd-origin")

        report = rd.redraft(limit=0, apply=True)

        assert report["redraftable"] == 1
        assert report["deferred"] == [sid]
        assert store.get_suggestion(sid)["status"] == "pending"


class TestLoopClosure:
    def test_a_second_run_has_nothing_left_to_do(self, anchoring_drafter):
        _seed_finding("fnd-origin")
        _seed_section()
        _seed_unanchored_suggestion("fnd-origin")

        first = rd.redraft(apply=True)
        second = rd.redraft(apply=True)

        assert first["by_outcome"] == {"redrafted": 1}
        # The replacement is anchored, so it is not a target -- the loop is closed
        # by the anchor invariant, not by remembering what was visited.
        assert second["targets"] == 0
        assert second["by_outcome"] == {}

    def test_the_store_refuses_to_write_an_anchored_basis_with_no_section(self):
        # The invariant the loop-closure argument rests on. If this ever stops
        # holding, a re-drafted row could be unanchored and re-enter the set.
        with pytest.raises(ValueError):
            store.create_suggestion(
                suggested_content="x", current_content=SECTION,
                anchor_basis="exact", anchor_section_id=None,
                anchor_start=0, anchor_end=7, anchor_text=SECTION[:7])


# -- Structural: not a second writer ------------------------------------------

class TestNotASecondWriter:
    """The module must not develop its own opinion about how a suggestion is
    retired. A behavioural test would still pass for a future edit that adds a
    private UPDATE beside the seam, so this reads the source."""

    SOURCE = (ROOT / "tools" / "document_intelligence" / "suggestion_redraft.py").read_text(
        encoding="utf-8")

    def test_it_never_writes_dic_suggestions_itself(self):
        tree = ast.parse(self.SOURCE)
        for node in ast.walk(tree):
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                sql = node.value.upper()
                if "DIC_SUGGESTION" in sql:
                    assert not any(verb in sql for verb in
                                   ("UPDATE ", "INSERT ", "DELETE ")), \
                        f"suggestion_redraft writes dic_suggestions directly: {node.value!r}"

    def test_it_retires_a_row_through_the_dwr_anchor_05_seam(self):
        tree = ast.parse(self.SOURCE)
        imported = {
            alias.name
            for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)
            and node.module == "tools.document_intelligence.suggestion_store"
            for alias in node.names
        }
        assert "supersede_suggestion" in imported

    def test_it_calls_the_drafter_rather_than_re_deriving_a_draft(self):
        tree = ast.parse(self.SOURCE)
        called = {
            node.func.id for node in ast.walk(tree)
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
        }
        assert "draft_redline" in called
