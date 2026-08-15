# CUI // SP-CTI
"""The Delta Review canvas — side-by-side HITL panel (trust-hitl-02).

Six things have to be true, and each has a test class here:

  1. A finding is attached to a SPAN by ``item_number - 1 == *_index``. That
     join is the whole reason the diff is claim-anchored, and a finding that
     anchors to nothing must surface at document level rather than vanish.
  2. A claim REWORDED BUT STILL FLAGGED reads ``persisting`` even while the
     total finding count falls. That is the case ``self_correct``'s monotone
     invariant structurally cannot see, and it is why this panel exists.
  3. Review state is DERIVED from the ``approval_items`` row, never from a
     column on ``trust_deltas`` — which has none. An ask that never queued, or
     whose item is gone, reads PENDING; a lapsed one is not a denial.
  4. The panel's own queue derivation agrees with the landed
     ``hitl_delta.pending_deltas`` over the same window. The panel reads the
     board in one pass so its counters and its table cannot disagree; this pins
     that local pass to the contract it reproduces.
  5. The settle route enforces a rationale, binds the actor to the
     authenticated user, and refuses a second settlement rather than
     overwriting the first.
  6. No artifact TEXT and no finding ``detail`` leaves the IQE seam — those rows
     travel into analyst answers and chat replies, and ``claim_gate`` puts 120
     characters of the offending claim into ``detail``.

Plus the two gate-gap tests trust-hitl-02 added: every seed query must PARSE and
name a collection this canvas actually registers, and one is EXECUTED end to end
— the 8-point completeness gate counts ``.iqe`` FILES and has never parsed one.

Schema comes from the real migrations, via the same fixture shape
``tests/test_hitl_delta.py`` uses, so a column added to one and not the other
fails here instead of at runtime inside a swallowed exception.
"""
from __future__ import annotations

import importlib.util
import json
import sqlite3
import sys
from pathlib import Path
from typing import Any

import pytest

from tests._sql_compat import translating
from tools.agent_runtime import approval_inbox
from tools.delta_review import constants, review
from tools.delta_review.blueprint import create_delta_review_blueprint
from tools.quality.hitl_delta import (
    OP_ADDED,
    OP_MODIFIED,
    OP_REMOVED,
    OP_UNCHANGED,
    STAGE_PROMOTE,
    Delta,
    pending_deltas,
    record_delta,
)

REPO_ROOT = Path(__file__).resolve().parents[1]
QUERY_DIR = REPO_ROOT / "context" / "iqe" / "queries" / "delta_review"

# A three-claim draft where the middle claim is invented. The revision rewords
# that claim without fixing it, and fixes nothing else — so the finding COUNT is
# unchanged and only a per-claim view shows what happened.
BEFORE = (
    "The system processed 4,200 requests in fiscal year 2024. [source: SRC-1] "
    "It was deployed to nine regions. [source: SRC-1] "
    "Uptime met the service level objective. [source: SRC-1]"
)
AFTER = (
    "The system processed 4,200 requests in fiscal year 2024. [source: SRC-1] "
    "It was rolled out across nine regions. [source: SRC-1] "
    "Uptime met the service level objective. [source: SRC-1]"
)
SOURCES = {
    "SRC-1": (
        "The system processed 4,200 requests in fiscal year 2024. "
        "It was deployed to three regions. "
        "Uptime met the service level objective throughout."
    )
}

# Prose that could only have come from the artifact. If it turns up in an IQE
# row, drafted CUI leaked into every downstream surface that renders one.
ARTIFACT_SECRET = "Contract W91234-25-C-0007 was awarded to ACME Federal."


# ---------------------------------------------------------------------------
# Schema — from the migrations themselves
# ---------------------------------------------------------------------------
def _sql_ddl(migration: str) -> str:
    return (REPO_ROOT / "tools" / "db" / "migrations" / migration / "up.sql").read_text(
        encoding="utf-8"
    )


def _approval_log_ddl() -> str:
    path = (
        REPO_ROOT / "tools" / "db" / "migrations"
        / "20260803002224_agent_approval_log" / "up.py"
    )
    spec = importlib.util.spec_from_file_location("_m_agent_approval_log_dr", path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module._DDL


def _storage_module():
    """The module the code under test actually resolves ``get_connection`` from.

    ``tools.db.storage`` in ``sys.modules`` is the compat shim, and
    ``import tools.db.storage`` binds the canonical ``icdev.tools.db.storage``
    instead — two different objects. Every module here imports the shim from
    inside its functions, so patching the canonical module (which is what
    monkeypatch's string form resolves to) would patch nothing and every test
    below would assert against the LIVE board.
    """
    return sys.modules["tools.db.storage"]


def _translating_conn(raw: sqlite3.Connection):
    """The connection handed to the code under test.

    ``unclosable``: the stores close their connection in a ``finally`` block and
    this fixture's connection has to outlive that. A named factory rather than
    an inline ``translating(...)`` because ``check_test_db_isolation`` seeds its
    safe-name set from local factory FUNCTIONS.
    """
    return translating(raw, unclosable=True)


@pytest.fixture
def delta_db(monkeypatch, tmp_path):
    """All three real tables in one DB, behind the production %s translation."""
    raw = sqlite3.connect(str(tmp_path / "delta_review.db"))
    raw.executescript(_sql_ddl("20260815063941_trust_hitl_deltas"))
    raw.executescript(_sql_ddl("20260809203855_agov_approval_items"))
    raw.executescript(_approval_log_ddl())
    conn = _translating_conn(raw)
    storage = _storage_module()
    monkeypatch.setattr(storage, "get_connection", lambda *a, **k: conn)
    monkeypatch.setattr(storage, "table_exists", lambda c, t: True)
    monkeypatch.setenv("ICDEV_APPROVAL_ACTOR", "test-operator")
    monkeypatch.setenv("ICDEV_SESSION_ID", "sess-delta-review")
    yield raw
    raw.close()


def _record(**overrides) -> Delta:
    kwargs: dict[str, Any] = dict(
        artifact_id="draft-42",
        stage=STAGE_PROMOTE,
        before_text=BEFORE,
        after_text=AFTER,
        rationale="checked the region count against the deployment record",
        actor="alice",
        sources=SOURCES,
    )
    kwargs.update(overrides)
    return record_delta(**kwargs)


def _span(op: str, *, before_index: int = -1, after_index: int = -1, **extra) -> dict:
    span: dict[str, Any] = {"op": op}
    if before_index >= 0:
        span.update({"before_index": before_index, "before_claim": f"b{before_index}"})
    if after_index >= 0:
        span.update({"after_index": after_index, "after_claim": f"a{after_index}"})
    span.update(extra)
    return span


def _finding(number: int, issue: str = "unsupported_claim") -> dict:
    return {"item_number": number, "issue": issue, "detail": ["9"]}


# ---------------------------------------------------------------------------
# 1. A finding is attached to a span by claim index
# ---------------------------------------------------------------------------
class TestFindingAnchoring:
    def test_item_number_is_one_based_over_the_index_spans_carry(self):
        delta = Delta(
            delta_id="d1", artifact_id="a", stage=STAGE_PROMOTE,
            spans=[_span(OP_UNCHANGED, before_index=0, after_index=0),
                   _span(OP_MODIFIED, before_index=1, after_index=1)],
            findings_before=[_finding(2)],
            findings_after=[_finding(2)],
        )
        spans, _doc_b, _doc_a = review.annotate_spans(delta)
        assert spans[0]["findings_before"] == [], "finding 2 landed on claim index 0"
        assert spans[1]["findings_before"] == [_finding(2)]
        assert spans[1]["findings_after"] == [_finding(2)]

    def test_an_unanchored_finding_surfaces_at_document_level(self):
        """``placeholder_guard`` reports with no usable item_number.

        Dropping it would render a blocked draft with nothing wrong with it,
        which is worse than the audit line this panel replaces.
        """
        delta = Delta(
            delta_id="d1", artifact_id="a", stage=STAGE_PROMOTE,
            spans=[_span(OP_UNCHANGED, before_index=0, after_index=0)],
            findings_before=[{"issue": "placeholder_detected", "detail": "TBD"}],
        )
        _spans, doc_before, _doc_after = review.annotate_spans(delta)
        assert doc_before == [{"issue": "placeholder_detected", "detail": "TBD"}]

    def test_a_finding_pointing_past_the_last_span_is_not_dropped(self):
        """The guard ran over a different revision than the delta stored.

        It anchors to no span, so it belongs with the document-level ones. The
        failure mode being guarded against is silence, not misplacement.
        """
        delta = Delta(
            delta_id="d1", artifact_id="a", stage=STAGE_PROMOTE,
            spans=[_span(OP_UNCHANGED, before_index=0, after_index=0)],
            findings_before=[_finding(7)],
        )
        spans, doc_before, _doc_after = review.annotate_spans(delta)
        assert all(not s["findings_before"] for s in spans)
        assert doc_before == [_finding(7)], "finding 7 anchored to nothing and vanished"

    def test_findings_by_claim_ignores_a_bool_item_number(self):
        """``True`` is an ``int`` in Python and would map to claim index 0."""
        assert review.findings_by_claim([{"item_number": True, "issue": "x"}]) == {}


# ---------------------------------------------------------------------------
# 2. The central case: reworded, still flagged
# ---------------------------------------------------------------------------
class TestSpanVerdicts:
    @pytest.mark.parametrize(
        "before,after,expected",
        [
            ([_finding(1)], [], constants.VERDICT_RESOLVED),
            ([], [_finding(1)], constants.VERDICT_REGRESSED),
            ([_finding(1)], [_finding(1)], constants.VERDICT_PERSISTING),
            ([], [], constants.VERDICT_CLEAN),
        ],
    )
    def test_four_verdicts(self, before, after, expected):
        row = review.resolve_span_findings(
            _span(OP_MODIFIED, before_index=0, after_index=0), before, after
        )
        assert row["finding_verdict"] == expected

    def test_a_reworded_claim_that_kept_its_finding_reads_persisting(self):
        """The case the monotone invariant cannot see.

        Two claims carried findings; the revision cleared one and merely
        reworded the other. The TOTAL count fell — ``self_correct`` accepts on
        ``strict_decrease`` and reports progress — while a specific defect
        survived. Only the per-claim view says so.
        """
        delta = Delta(
            delta_id="d1", artifact_id="a", stage=STAGE_PROMOTE,
            spans=[_span(OP_MODIFIED, before_index=0, after_index=0),
                   _span(OP_MODIFIED, before_index=1, after_index=1)],
            findings_before=[_finding(1), _finding(2)],
            findings_after=[_finding(2)],
        )
        spans, _b, _a = review.annotate_spans(delta)
        assert len(delta.findings_after) < len(delta.findings_before)
        assert spans[0]["finding_verdict"] == constants.VERDICT_RESOLVED
        assert spans[1]["finding_verdict"] == constants.VERDICT_PERSISTING

    def test_an_unchanged_span_carrying_a_finding_is_still_notable(self):
        row = review.resolve_span_findings(
            _span(OP_UNCHANGED, before_index=0, after_index=0),
            [_finding(1)], [_finding(1)],
        )
        assert row["notable"], "a flagged claim nobody touched must stay on screen"

    def test_a_clean_unchanged_span_is_not_notable(self):
        row = review.resolve_span_findings(
            _span(OP_UNCHANGED, before_index=0, after_index=0), [], []
        )
        assert not row["notable"]

    @pytest.mark.parametrize("op", [OP_MODIFIED, OP_ADDED, OP_REMOVED])
    def test_every_changed_op_is_notable_even_with_no_findings(self, op):
        row = review.resolve_span_findings(_span(op, before_index=0), [], [])
        assert row["notable"]


# ---------------------------------------------------------------------------
# 3. Review state is derived from the approval item
# ---------------------------------------------------------------------------
class TestReviewState:
    def test_a_delta_with_no_ask_reads_pending_and_cannot_be_settled(self):
        """A failed enqueue must never present as an approval."""
        state = review.review_state(Delta(delta_id="d", artifact_id="a", stage="promote"))
        assert state["key"] == constants.REVIEW_PENDING
        assert state["settled"] is False
        assert state["can_settle"] is False, "no ask means nothing to move"
        assert state["no_ask"] is True

    def test_an_absent_item_reads_pending_not_settled(self, delta_db):
        state = review.review_state(
            Delta(delta_id="d", artifact_id="a", stage="promote",
                  approval_item_id="item-that-never-existed")
        )
        assert state["key"] == constants.REVIEW_PENDING
        assert state["no_ask"] is True

    def test_superseded_wins_over_the_ask(self, delta_db):
        delta = _record()
        state = review.review_state(delta, superseded=True)
        assert state["key"] == constants.REVIEW_SUPERSEDED
        assert state["can_settle"] is False

    def test_a_pending_ask_can_be_settled(self, delta_db):
        delta = _record()
        state = review.review_state(delta)
        assert state["key"] == constants.REVIEW_PENDING
        assert state["can_settle"] is True

    @pytest.mark.parametrize(
        "approved,expected",
        [(True, constants.REVIEW_APPROVED), (False, constants.REVIEW_DENIED)],
    )
    def test_resolution_drives_the_key(self, delta_db, approved, expected):
        delta = _record()
        approval_inbox.resolve(
            delta.approval_item_id, approved=approved,
            resolved_by="bob", reason="looked at the diff",
        )
        state = review.review_state(delta)
        assert state["key"] == expected
        assert state["settled"] is True
        assert state["can_settle"] is False

    @pytest.mark.parametrize("mover", ["expire", "cancel"])
    def test_a_lapsed_ask_is_not_a_denial(self, delta_db, mover):
        """Nobody looked. Collapsing this into DENIED is how a timeout starts
        reading as a decision."""
        delta = _record()
        getattr(approval_inbox, mover)(delta.approval_item_id, reason="ttl")
        state = review.review_state(delta)
        assert state["key"] == constants.REVIEW_LAPSED
        assert state["key"] != constants.REVIEW_DENIED


# ---------------------------------------------------------------------------
# 4. The panel's queue agrees with the landed contract
# ---------------------------------------------------------------------------
class TestBoardAgreesWithPendingDeltas:
    def test_board_queue_matches_pending_deltas(self, delta_db):
        _record(artifact_id="draft-a")
        settled = _record(artifact_id="draft-b")
        _record(artifact_id="draft-c")
        approval_inbox.resolve(
            settled.approval_item_id, approved=True,
            resolved_by="bob", reason="checked against the source",
        )

        from_board = {row["delta_id"] for row in review.board(limit=100)["queue"]}
        from_contract = {d.delta_id for d in pending_deltas(limit=100)}
        assert from_board == from_contract
        assert settled.delta_id not in from_board

    def test_pending_queue_and_board_agree(self, delta_db):
        _record(artifact_id="draft-a")
        _record(artifact_id="draft-b")
        assert (
            {r["delta_id"] for r in review.pending_queue(limit=100)}
            == {r["delta_id"] for r in review.board(limit=100)["queue"]}
        )

    def test_the_header_counter_equals_the_table_length(self, delta_db):
        """One read, so a board reading '3 awaiting review' over four rows is
        not expressible."""
        _record(artifact_id="draft-a")
        _record(artifact_id="draft-b")
        data = review.board(limit=100)
        assert data["summary"][constants.REVIEW_PENDING] == len(data["queue"])


# ---------------------------------------------------------------------------
# 5. Projection derives, never persists
# ---------------------------------------------------------------------------
class TestDeltaPayload:
    def test_counts_are_derived_from_the_stored_finding_lists(self, delta_db):
        delta = _record()
        payload = review.delta_payload(delta)
        assert payload["findings_before_n"] == len(delta.findings_before)
        assert payload["findings_after_n"] == len(delta.findings_after)
        assert payload["net_findings"] == (
            len(delta.findings_after) - len(delta.findings_before)
        )

    def test_no_count_column_was_written_to_trust_deltas(self, delta_db):
        """The counts are derived, so the table must not have grown a column for
        them. PR #1684 persisted ``findings_before_n`` against a table that has
        no such column, and the INSERT failed into a swallowing caller."""
        _record()
        columns = {r[1] for r in delta_db.execute("PRAGMA table_info(trust_deltas)")}
        for derived in (
            "findings_before_n", "findings_after_n", "net_findings", "disposition",
        ):
            assert derived not in columns

    def test_the_timeline_is_list_deltas_not_the_revision_chain(self, delta_db):
        """Three independent deltas on one artifact are three chains of one.

        ``revision_chain`` on any of them returns a single row; the artifact's
        history is ``list_deltas``. Confusing the two silently empties the
        timeline.
        """
        for _ in range(3):
            _record(artifact_id="draft-shared")
        timeline = review.artifact_timeline("draft-shared")
        assert len(timeline) == 3
        assert len(review.correction_chain(timeline[0]["delta_id"])) == 1

    def test_the_timeline_reads_oldest_first(self, delta_db):
        first = _record(artifact_id="draft-ordered")
        _record(artifact_id="draft-ordered")
        assert review.artifact_timeline("draft-ordered")[0]["delta_id"] == first.delta_id

    def test_a_no_op_override_is_reported(self, delta_db):
        """The single most useful thing a reviewer can be told, and the one
        today's audit line cannot say."""
        delta = _record(before_text=BEFORE, after_text=BEFORE)
        assert review.delta_payload(delta)["is_no_op"] is True


# ---------------------------------------------------------------------------
# 6. The settle route
# ---------------------------------------------------------------------------
#: ``base.html`` mounts the whole dashboard chrome — nav, CSRF shim, the
#: registry-driven ``__ICDEV_PATH_CANVAS__`` — and needs app-level context this
#: canvas does not own. Stubbing it (and the shared IQE include) renders the
#: REAL ``delta_review/page.html`` against a minimal parent, so a failure here
#: is this page's, not the chrome's. The include is still asserted to be
#: present in the file by ``TestCanvasWiring``.
_BASE_STUB = "<html><body>{% block content %}{% endblock %}</body></html>"


@pytest.fixture
def client(delta_db, monkeypatch):
    from flask import Flask
    from jinja2 import ChoiceLoader, DictLoader, FileSystemLoader

    monkeypatch.setenv(constants.FEATURE_FLAG, "true")
    app = Flask(__name__)
    app.jinja_loader = ChoiceLoader([
        DictLoader({
            "base.html": _BASE_STUB,
            "includes/iqe_query_widget.html": "<div id='iqe-widget'></div>",
        }),
        FileSystemLoader(str(REPO_ROOT / "tools" / "dashboard" / "templates")),
    ])
    app.config["TESTING"] = True
    bp = create_delta_review_blueprint()
    assert bp is not None, "the canvas declares default_enabled: true"
    app.register_blueprint(bp)
    return app.test_client()


class TestSettleRoute:
    def test_missing_approved_is_a_400(self, client, delta_db):
        delta = _record()
        r = client.post(f"/api/delta-review/delta/{delta.delta_id}/settle",
                        json={"rationale": "a perfectly good rationale"})
        assert r.status_code == 400
        assert "approved" in r.get_json()["error"]

    def test_a_token_rationale_is_refused(self, client, delta_db):
        """An approval reading 'ok' is the same unauditable artifact as an
        empty one (``trust_gate`` invariant 4)."""
        delta = _record()
        r = client.post(f"/api/delta-review/delta/{delta.delta_id}/settle",
                        json={"approved": True, "rationale": "ok"})
        assert r.status_code == 400
        assert r.get_json()["min_chars"] == constants.MIN_RATIONALE_CHARS

    def test_an_unknown_delta_is_a_404(self, client, delta_db):
        r = client.post("/api/delta-review/delta/nope/settle",
                        json={"approved": True, "rationale": "a real rationale here"})
        assert r.status_code == 404

    def test_a_good_settle_resolves_the_approval_item(self, client, delta_db):
        delta = _record()
        r = client.post(f"/api/delta-review/delta/{delta.delta_id}/settle",
                        json={"approved": True,
                              "rationale": "checked the revised figure against SRC-1"})
        assert r.status_code == 200, r.get_data(as_text=True)
        body = r.get_json()
        assert body["resolution"] == constants.RESOLUTION_APPROVED
        item = approval_inbox.get(delta.approval_item_id)
        assert item.state == constants.STATE_RESOLVED

    def test_the_settle_writes_no_update_against_trust_deltas(self, client, delta_db):
        """``trust_deltas`` is append-only EVIDENCE. The decision moves the
        mutable ``approval_items`` row and appends to ``agent_approval_log`` —
        the delta row is byte-identical afterwards, and no successor delta is
        written either."""
        delta = _record()
        before = list(delta_db.execute("SELECT * FROM trust_deltas"))
        client.post(f"/api/delta-review/delta/{delta.delta_id}/settle",
                    json={"approved": True, "rationale": "verified against the source"})
        assert list(delta_db.execute("SELECT * FROM trust_deltas")) == before

    def test_the_rationale_reaches_the_permanent_decision_log(self, client, delta_db):
        rationale = "verified the region count against the deployment record"
        delta = _record()
        client.post(f"/api/delta-review/delta/{delta.delta_id}/settle",
                    json={"approved": True, "rationale": rationale})
        reasons = [
            r[0] for r in delta_db.execute(
                "SELECT reason FROM agent_approval_log WHERE rule = 'hitl_delta'"
            )
        ]
        assert rationale in reasons

    def test_a_body_supplied_actor_is_ignored(self, client, delta_db):
        """A caller must not be able to attribute a decision to someone else."""
        delta = _record()
        client.post(f"/api/delta-review/delta/{delta.delta_id}/settle",
                    json={"approved": True, "rationale": "checked against the source",
                          "actor": "someone-else"})
        actors = {
            r[0] for r in delta_db.execute(
                "SELECT actor FROM agent_approval_log WHERE rule = 'hitl_delta'"
            )
        }
        assert "someone-else" not in actors
        assert actors == {"dashboard"}

    def test_a_second_settle_is_a_409_not_an_overwrite(self, client, delta_db):
        """A second decision would give the panel two contradictory answers."""
        delta = _record()
        first = client.post(f"/api/delta-review/delta/{delta.delta_id}/settle",
                            json={"approved": True, "rationale": "approved on the evidence"})
        assert first.status_code == 200
        second = client.post(f"/api/delta-review/delta/{delta.delta_id}/settle",
                             json={"approved": False, "rationale": "changed my mind entirely"})
        assert second.status_code == 409
        assert approval_inbox.get(delta.approval_item_id).resolution == (
            constants.RESOLUTION_APPROVED
        )

    def test_a_delta_recorded_with_no_ask_is_a_409(self, client, delta_db):
        delta = _record()
        delta_db.execute(
            "UPDATE trust_deltas SET approval_item_id = '' WHERE delta_id = ?",
            (delta.delta_id,),
        )
        delta_db.commit()
        r = client.post(f"/api/delta-review/delta/{delta.delta_id}/settle",
                        json={"approved": True, "rationale": "a real rationale here"})
        assert r.status_code == 409


class TestPageAndReadRoutes:
    def test_the_page_renders_with_an_empty_board(self, client, delta_db):
        r = client.get("/delta-review")
        assert r.status_code == 200
        assert b"Delta Review" in r.data

    def test_the_page_renders_the_panel_for_a_delta(self, client, delta_db):
        delta = _record()
        r = client.get(f"/delta-review?delta_id={delta.delta_id}")
        assert r.status_code == 200
        body = r.get_data(as_text=True)
        assert delta.delta_id in body
        assert "Claim-by-claim diff" in body

    def test_a_stale_link_says_so_rather_than_showing_another_page(self, client, delta_db):
        r = client.get("/delta-review?delta_id=gone")
        assert r.status_code == 200
        assert "Delta not found" in r.get_data(as_text=True)

    def test_the_delta_api_404s_on_an_unknown_id(self, client, delta_db):
        assert client.get("/api/delta-review/delta/nope").status_code == 404

    def test_the_deltas_api_lists_the_queue(self, client, delta_db):
        delta = _record()
        body = client.get("/api/delta-review/deltas").get_json()
        assert body["count"] == 1
        assert body["deltas"][0]["delta_id"] == delta.delta_id

    def test_the_artifact_api_returns_the_timeline(self, client, delta_db):
        _record(artifact_id="draft-x")
        body = client.get("/api/delta-review/artifact/draft-x").get_json()
        assert body["count"] == 1

    def test_the_canvas_is_dark_when_toggled_off(self, monkeypatch):
        monkeypatch.setenv(constants.FEATURE_FLAG, "false")
        assert create_delta_review_blueprint() is None


# ---------------------------------------------------------------------------
# 7. No drafted text leaves the IQE seam
# ---------------------------------------------------------------------------
class TestIQEAdapters:
    @pytest.fixture(autouse=True)
    def _adapters(self):
        from tools.iqe.adapters import delta_review as adapters

        return adapters

    def test_the_collections_match_the_constants_and_the_registry(self):
        from tools.iqe.executor import list_collections

        from tools.iqe.adapters import delta_review as _  # noqa: F401
        registered = set(list_collections())
        for name in constants.IQE_COLLECTIONS:
            assert name in registered, f"{name} is declared but never registered"

    def test_no_artifact_text_or_finding_detail_reaches_a_row(self, delta_db, _adapters):
        """Those rows travel into analyst answers, AI briefs and chat replies,
        and ``claim_gate`` puts 120 characters of the offending claim into
        ``detail``."""
        _record(
            before_text=f"{ARTIFACT_SECRET} [source: SRC-1] {BEFORE}",
            after_text=f"{ARTIFACT_SECRET} [source: SRC-1] {AFTER}",
        )
        conn = _translating_conn(delta_db)
        emitted = json.dumps(
            _adapters.deltas_adapter(conn)
            + _adapters.settlements_adapter(conn)
            + _adapters.spans_adapter(conn)
            + _adapters.decisions_adapter(conn),
            default=str,
        )
        assert ARTIFACT_SECRET not in emitted
        assert "before_text" not in emitted
        assert "after_text" not in emitted
        assert "before_claim" not in emitted
        assert "detail" not in emitted

    def test_the_deltas_collection_derives_its_counts(self, delta_db, _adapters):
        delta = _record()
        row = _adapters.deltas_adapter(_translating_conn(delta_db))[0]
        assert row["findings_before_n"] == len(delta.findings_before)
        assert row["net_findings"] == (
            len(delta.findings_after) - len(delta.findings_before)
        )

    def test_settlements_reports_a_delta_with_no_ask_as_pending(self, delta_db, _adapters):
        """An inner join would drop it, and that is exactly the population an
        operator auditing HITL coverage is looking for."""
        delta = _record()
        delta_db.execute(
            "UPDATE trust_deltas SET approval_item_id = '' WHERE delta_id = ?",
            (delta.delta_id,),
        )
        delta_db.commit()
        row = _adapters.settlements_adapter(_translating_conn(delta_db))[0]
        assert row["review_state"] == constants.REVIEW_PENDING
        assert row["has_ask"] is False

    def test_settlements_agrees_with_the_panel_after_a_decision(self, delta_db, _adapters):
        delta = _record()
        approval_inbox.resolve(delta.approval_item_id, approved=True,
                               resolved_by="bob", reason="checked it")
        row = _adapters.settlements_adapter(_translating_conn(delta_db))[0]
        assert row["review_state"] == review.review_state(delta)["key"]

    def test_the_spans_collection_flattens_and_carries_the_verdict(self, delta_db, _adapters):
        _record()
        rows = _adapters.spans_adapter(_translating_conn(delta_db))
        assert rows, "a three-claim edit produced no span rows"
        assert {r["finding_verdict"] for r in rows} <= {
            constants.VERDICT_RESOLVED, constants.VERDICT_PERSISTING,
            constants.VERDICT_REGRESSED, constants.VERDICT_CLEAN,
        }

    def test_decisions_separates_a_default_reason_from_a_real_one(self, delta_db, _adapters):
        """``settle_delta`` substitutes ``delta <id> approved`` when a caller
        supplies none. It is well-formed and says nothing."""
        from tools.quality.hitl_delta import settle_delta

        delta = _record()
        settle_delta(delta.delta_id, approved=True, actor="bob")
        row = _adapters.decisions_adapter(_translating_conn(delta_db))[0]
        assert row["is_default_reason"] is True
        assert row["artifact_id"] == "draft-42"


# ---------------------------------------------------------------------------
# 8. The seed queries — the gap the completeness gate leaves open
# ---------------------------------------------------------------------------
class TestSeedQueries:
    """The 8-point gate counts ``.iqe`` FILES and has never parsed one.

    So a canvas can satisfy the standard with three files that raise on their
    first token — which is not hypothetical: the IQE lexer has no ``//`` comment
    form, and PR #1684 shipped three of four seed queries with ``//`` headers,
    visually indistinguishable from the working one. Generalising these two
    tests to every canvas is worth a task of its own; it is deliberately not
    done here.
    """

    def test_at_least_three_seed_queries_exist(self):
        assert len(list(QUERY_DIR.glob("*.iqe"))) >= 3

    @pytest.mark.parametrize(
        "path", sorted(QUERY_DIR.glob("*.iqe")), ids=lambda p: p.name
    )
    def test_every_seed_query_parses_and_names_a_registered_collection(self, path):
        from tools.iqe.parser import parse

        from tools.iqe.adapters import delta_review as _  # noqa: F401
        ast = parse(path.read_text(encoding="utf-8"))
        assert ".".join(ast.collection.parts) in constants.IQE_COLLECTIONS

    def test_one_seed_query_executes_end_to_end(self, delta_db):
        """Parsing is not running: a query that parses but selects a column no
        adapter emits is equally silent.

        The connection is passed EXPLICITLY — with ``conn=None`` the adapter
        opens its own via ``get_connection()`` and the assertion lands on the
        real database rather than this fixture.
        """
        from tools.iqe.executor import execute_query
        from tools.iqe.parser import parse

        from tools.iqe.adapters import delta_review as _  # noqa: F401
        delta = _record()
        ast = parse((QUERY_DIR / "01_pending_deltas.iqe").read_text(encoding="utf-8"))
        rows = execute_query(ast, conn=_translating_conn(delta_db))
        assert [r["delta_id"] for r in rows] == [delta.delta_id]


# ---------------------------------------------------------------------------
# 9. Registry wiring — the 8-point page-completeness contract
# ---------------------------------------------------------------------------
class TestCanvasWiring:
    @pytest.fixture(scope="class")
    def entry(self):
        import yaml

        data = yaml.safe_load(
            (REPO_ROOT / "args" / "component_registry.yaml").read_text(encoding="utf-8")
        )
        for component in data["components"]:
            if component.get("key") == constants.CANVAS_KEY:
                return component
        pytest.fail("delta_review is not registered in args/component_registry.yaml")

    def test_the_registry_declares_the_same_collections_as_the_constants(self, entry):
        assert tuple(entry["iqe"]["collections"]) == constants.IQE_COLLECTIONS

    def test_the_registry_points_at_the_real_blueprint_factory(self, entry):
        import importlib

        module = importlib.import_module(entry["module"])
        assert callable(getattr(module, entry["blueprint_attr"]))

    def test_the_path_is_mapped_for_the_iqe_mini_bar(self):
        import yaml

        data = yaml.safe_load(
            (REPO_ROOT / "args" / "component_registry.yaml").read_text(encoding="utf-8")
        )
        # Some entries are regex-keyed rather than path-keyed; this canvas
        # declares an exact path, which is what the mini-bar matches first.
        mapped = {
            row["path"]: row["canvas"]
            for row in data["iqe_path_canvas"]
            if isinstance(row, dict) and "path" in row
        }
        assert mapped.get(constants.URL_ROOT) == constants.CANVAS_KEY

    @pytest.mark.parametrize(
        "relative",
        [
            "tools/dashboard/templates/delta_review/page.html",
            "icdev/tools/dashboard/templates/delta_review/page.html",
            "tools/delta_review/blueprint.py",
            "icdev/tools/delta_review/blueprint.py",
            "tools/delta_review/constants.py",
            "tools/delta_review/review.py",
            "tools/iqe/adapters/delta_review.py",
            "icdev/tools/iqe/adapters/delta_review.py",
        ],
    )
    def test_every_required_component_exists(self, relative):
        assert (REPO_ROOT / relative).exists(), f"{relative} is missing"

    def test_the_template_includes_the_shared_iqe_widget(self):
        page = (
            REPO_ROOT / "tools" / "dashboard" / "templates" / "delta_review" / "page.html"
        ).read_text(encoding="utf-8")
        assert 'include "includes/iqe_query_widget.html"' in page

    def test_the_two_template_copies_are_identical(self):
        """The ``icdev/`` mirror is what a wheel ships. A copy that drifts means
        the packaged dashboard renders a different page from the repo's."""
        root = REPO_ROOT / "tools" / "dashboard" / "templates" / "delta_review" / "page.html"
        mirror = (
            REPO_ROOT / "icdev" / "tools" / "dashboard" / "templates"
            / "delta_review" / "page.html"
        )
        assert root.read_text(encoding="utf-8") == mirror.read_text(encoding="utf-8")
