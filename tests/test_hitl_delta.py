# CUI // SP-CTI
"""Tests for the TRUST HITL delta store and the Delta Review panel
(trust-hitl-01 / trust-hitl-02).

These are written RED-FIRST against the properties that actually matter, not
against the shape of the code. Each one corresponds to a way this subsystem
would be worthless if it were wrong:

* a delta that is not claim-anchored cannot show a reviewer WHICH claim changed;
* a settlement that mutates its predecessor destroys the evidence it settles;
* a "pending" read that trusts the append-only row's own column re-queues every
  settled delta forever;
* an approval with no rationale is the unauditable artifact the whole epic
  exists to eliminate;
* a span that was reworded but still carries a finding is invisible to
  ``self_correct``'s monotone count, so if the panel cannot surface it the panel
  has no reason to exist.
"""
from __future__ import annotations

import json

import pytest

from tools.delta_review import review
from tools.quality import hitl_delta
from tools.quality.hitl_delta import (
    DISPOSITION_APPROVED,
    DISPOSITION_PENDING,
    SPAN_ADDED,
    SPAN_CHANGED,
    SPAN_REMOVED,
    SPAN_UNCHANGED,
    STAGE_SELF_CORRECTION,
    STAGE_SETTLEMENT,
    Delta,
    align_claims,
    compute_delta,
)

BEFORE = (
    "ICDEV supports 47 compliance frameworks [source: ssp-1]. "
    "The platform runs on PostgreSQL [source: arch-2]."
)
AFTER = (
    "ICDEV supports several compliance frameworks [source: ssp-1]. "
    "The platform runs on PostgreSQL [source: arch-2]."
)
UNSUPPORTED = {
    "guard": "claim_guard",
    "issue": "unsupported_claim",
    "severity": "block",
    "item_number": 1,
    "detail": "the evidence does not state 47",
}


# ── compute_delta / alignment ────────────────────────────────────────────────

def test_delta_is_claim_anchored_not_a_text_diff():
    """The changed span must resolve to the SENTENCE that changed, and the
    untouched sentence must be reported as untouched.

    A line/word diff would mark the whole paragraph dirty, which tells a
    reviewer nothing about which assertion moved.
    """
    delta = compute_delta(BEFORE, AFTER, artifact_id="a1", findings_before=[UNSUPPORTED])

    assert len(delta.spans) == 2
    changed = [s for s in delta.spans if s["kind"] == SPAN_CHANGED]
    unchanged = [s for s in delta.spans if s["kind"] == SPAN_UNCHANGED]
    assert len(changed) == 1 and len(unchanged) == 1
    assert "47" in changed[0]["before_claim"]
    assert "several" in changed[0]["after_claim"]
    assert "PostgreSQL" in unchanged[0]["before_claim"]


def test_finding_attaches_to_the_claim_it_was_reported_against():
    """``item_number`` is a 1-based index into ``decompose_claims`` — the
    vocabulary every TRUST guard reports in. If this join is wrong the panel
    shows the right diff with the wrong defect beside it."""
    delta = compute_delta(BEFORE, AFTER, artifact_id="a1", findings_before=[UNSUPPORTED])

    changed = next(s for s in delta.spans if s["kind"] == SPAN_CHANGED)
    unchanged = next(s for s in delta.spans if s["kind"] == SPAN_UNCHANGED)
    assert [f["issue"] for f in changed["findings_before"]] == ["unsupported_claim"]
    assert unchanged["findings_before"] == []


def test_document_level_findings_are_not_dropped():
    """``placeholder_guard`` / ``citation_guard`` report at document level.
    Dropping them renders a blocked draft with nothing wrong with it."""
    doc_finding = {"guard": "citation_guard", "issue": "missing_citations",
                   "severity": "block", "item_number": "document"}
    delta = compute_delta(
        BEFORE, AFTER, artifact_id="a1", findings_before=[UNSUPPORTED, doc_finding]
    )

    doc = hitl_delta.document_findings(delta.findings_before)
    assert [f["issue"] for f in doc] == ["missing_citations"]
    # ...and it is NOT also attached to a claim span.
    assert all(
        "missing_citations" not in [f["issue"] for f in s["findings_before"]]
        for s in delta.spans
    )


def test_alignment_reports_removed_and_added_claims():
    two = "Alpha claim one. Beta claim two."
    one = "Alpha claim one."
    assert [k for k, _b, _a in align_claims(two, one)] == [SPAN_UNCHANGED, SPAN_REMOVED]
    assert [k for k, _b, _a in align_claims(one, two)] == [SPAN_UNCHANGED, SPAN_ADDED]


def test_unknown_stage_is_refused():
    """A typo'd stage that persisted would make the row invisible to a panel
    filtering on the vocabulary — a silent disappearance, not an error."""
    with pytest.raises(ValueError, match="unknown stage"):
        compute_delta(BEFORE, AFTER, artifact_id="a1", stage="nope")


def test_noop_delta_is_identifiable():
    delta = compute_delta(BEFORE, BEFORE, artifact_id="a1")
    assert delta.is_noop is True


# ── span verdicts — the panel's central insight ──────────────────────────────

def test_reworded_but_still_flagged_span_is_surfaced_as_persisting():
    """THE case the finding COUNT cannot see.

    ``self_correct``'s monotone invariant only checks that the total dropped, so
    a claim that was reworded while keeping its defect hides whenever some other
    claim's finding cleared in the same round. If the panel cannot distinguish
    this from a fix, it is decoration.
    """
    still_bad = dict(UNSUPPORTED, item_number=1, detail="still unsupported")
    delta = compute_delta(
        BEFORE, AFTER, artifact_id="a1",
        findings_before=[UNSUPPORTED], findings_after=[still_bad],
    )
    span = review.resolve_span_findings(
        next(s for s in delta.spans if s["kind"] == SPAN_CHANGED)
    )
    assert span["finding_verdict"] == "persisting"


def test_cleared_span_is_resolved_and_new_finding_is_regressed():
    delta = compute_delta(
        BEFORE, AFTER, artifact_id="a1", findings_before=[UNSUPPORTED], findings_after=[]
    )
    resolved = review.resolve_span_findings(
        next(s for s in delta.spans if s["kind"] == SPAN_CHANGED)
    )
    assert resolved["finding_verdict"] == "resolved"

    regressed_delta = compute_delta(
        BEFORE, AFTER, artifact_id="a1", findings_before=[], findings_after=[UNSUPPORTED]
    )
    regressed = review.resolve_span_findings(
        next(s for s in regressed_delta.spans if s["kind"] == SPAN_CHANGED)
    )
    assert regressed["finding_verdict"] == "regressed"


def test_untouched_clean_span_gets_no_verdict_badge():
    delta = compute_delta(BEFORE, AFTER, artifact_id="a1", findings_before=[UNSUPPORTED])
    unchanged = review.resolve_span_findings(
        next(s for s in delta.spans if s["kind"] == SPAN_UNCHANGED)
    )
    assert unchanged["finding_verdict"] == "clean"
    assert unchanged["notable"] is False


# ── the store: append-only evidence + mutable state ──────────────────────────

@pytest.fixture()
def store(monkeypatch):
    """An in-memory delta store.

    Patches the module's own ``_connect`` rather than ``tools.db.storage``:
    patching the storage module by string form misses the ``icdev.`` module
    object entirely (they are distinct objects), and a test that silently falls
    through to the LIVE board is worse than no test.
    """
    import sqlite3

    conn = sqlite3.connect(":memory:")
    conn.execute(
        """
        CREATE TABLE trust_deltas (
            delta_id TEXT PRIMARY KEY, artifact_id TEXT NOT NULL, artifact_type TEXT,
            stage TEXT NOT NULL, gate TEXT, before_hash TEXT NOT NULL,
            after_hash TEXT NOT NULL, before_text TEXT, after_text TEXT,
            findings_before TEXT, findings_after TEXT, findings_before_n INTEGER,
            findings_after_n INTEGER, spans TEXT, actor TEXT, rationale TEXT,
            disposition TEXT NOT NULL, approval_item_id TEXT,
            supersedes_delta_id TEXT, session_id TEXT, tenant_id TEXT,
            classification TEXT, created_at TEXT
        )
        """
    )
    conn.commit()

    class _Wrapper:
        """Speaks the storage layer's ``%s`` dialect over raw sqlite3."""

        def execute(self, sql, params=()):
            return conn.execute(sql.replace("%s", "?"), params)

        def commit(self):
            conn.commit()

        def close(self):
            pass  # the fixture owns the connection

    monkeypatch.setattr(hitl_delta, "_connect", lambda: _Wrapper())
    # The inbox is exercised by tests/test_approval_inbox.py; here it must not
    # reach a real database, and a delta whose ask cannot be queued is a
    # supported state (empty approval_item_id), not a failure.
    monkeypatch.setattr(hitl_delta, "_enqueue_ask", lambda delta: "")
    yield conn
    conn.close()


def _record(**kwargs) -> Delta:
    delta = compute_delta(
        BEFORE, AFTER, artifact_id=kwargs.pop("artifact_id", "art-1"),
        findings_before=[UNSUPPORTED], findings_after=[], **kwargs,
    )
    return hitl_delta.record_delta(delta)


def test_record_then_read_round_trips_the_spans(store):
    recorded = _record()
    fetched = hitl_delta.get_delta(recorded.delta_id)

    assert fetched is not None
    assert fetched.artifact_id == "art-1"
    assert fetched.stage == STAGE_SELF_CORRECTION
    assert fetched.disposition == DISPOSITION_PENDING
    # The spans survive the JSON column round trip — a panel reading them back
    # as a string would render nothing and look like an empty diff.
    assert isinstance(fetched.spans, list) and len(fetched.spans) == 2
    assert fetched.findings_before_n == 1


def test_recording_a_noop_is_refused(store):
    """Asking a human to review nothing trains them to approve without looking."""
    delta = compute_delta(BEFORE, BEFORE, artifact_id="art-noop")
    with pytest.raises(ValueError, match="changes nothing"):
        hitl_delta.record_delta(delta)


def test_settlement_appends_and_never_touches_its_predecessor(store):
    """The append-only invariant, asserted on the ROW rather than on intent."""
    recorded = _record()
    before_row = store.execute(
        "SELECT * FROM trust_deltas WHERE delta_id = ?", (recorded.delta_id,)
    ).fetchone()

    settlement = hitl_delta.settle_delta(
        recorded.delta_id, approved=True, actor="reviewer",
        rationale="checked the revised figure against the cited SSP section",
    )

    assert settlement is not None
    assert settlement.stage == STAGE_SETTLEMENT
    assert settlement.supersedes_delta_id == recorded.delta_id
    assert settlement.disposition == DISPOSITION_APPROVED

    after_row = store.execute(
        "SELECT * FROM trust_deltas WHERE delta_id = ?", (recorded.delta_id,)
    ).fetchone()
    assert after_row == before_row, "the predecessor row was mutated"

    # There are now exactly two rows: the evidence and its successor.
    assert store.execute("SELECT COUNT(*) FROM trust_deltas").fetchone()[0] == 2


def test_settled_state_is_derived_from_the_successor_not_the_column(store):
    """The predecessor still SAYS pending — nothing updates it. Every read that
    answers "is this settled" must go through the successor, or a settled delta
    reappears in the queue forever."""
    recorded = _record()
    hitl_delta.settle_delta(
        recorded.delta_id, approved=True, actor="reviewer",
        rationale="verified against the source document",
    )

    assert hitl_delta.get_delta(recorded.delta_id).disposition == DISPOSITION_PENDING
    assert hitl_delta.get_settlement(recorded.delta_id) is not None
    assert hitl_delta.pending_deltas() == []


def test_empty_rationale_settles_nothing(store):
    """``trust_gate`` invariant 4. Not a warning — a refusal, and the delta is
    left exactly as pending as it was."""
    recorded = _record()

    assert hitl_delta.settle_delta(
        recorded.delta_id, approved=True, actor="reviewer", rationale="   "
    ) is None
    assert hitl_delta.get_settlement(recorded.delta_id) is None
    assert len(hitl_delta.pending_deltas()) == 1


def test_double_settle_is_refused(store):
    """A second settlement would give the panel two contradictory answers."""
    recorded = _record()
    first = hitl_delta.settle_delta(
        recorded.delta_id, approved=True, actor="a", rationale="first disposition, verified"
    )
    second = hitl_delta.settle_delta(
        recorded.delta_id, approved=False, actor="b", rationale="second disposition, verified"
    )

    assert first is not None
    assert second is None
    assert store.execute(
        "SELECT COUNT(*) FROM trust_deltas WHERE stage = ?", (STAGE_SETTLEMENT,)
    ).fetchone()[0] == 1


def test_delta_chain_excludes_settlements_and_marks_them_settled(store):
    recorded = _record(artifact_id="art-chain")
    hitl_delta.settle_delta(
        recorded.delta_id, approved=False, actor="reviewer",
        rationale="the revision still overstates the evidence",
    )

    chain = hitl_delta.delta_chain("art-chain")
    assert len(chain) == 1, "the settlement must not appear as its own reviewable entry"
    assert chain[0]["settled"] is True
    assert chain[0]["settlement"].disposition == "denied"


def test_summary_reports_unmeasurable_rather_than_a_confident_zero(monkeypatch):
    """A missing table is "nothing can be known", never "nothing is pending" —
    the capability_consumption discipline. A zero here would render as a clean
    review board on a database that has never had the migration run."""
    def _boom():
        raise hitl_delta.DeltaStoreUnavailable("trust_deltas is missing")

    monkeypatch.setattr(hitl_delta, "_connect", _boom)
    stats = hitl_delta.summary()
    assert stats["telemetry_available"] is False
    assert stats["pending"] == 0


# ── panel assembly ───────────────────────────────────────────────────────────

def test_panel_payload_offers_controls_only_while_unsettled(store):
    recorded = _record(artifact_id="art-panel")
    payload = review.delta_payload(hitl_delta.get_delta(recorded.delta_id))
    assert payload["can_settle"] is True
    assert payload["resolved_count"] == 1

    hitl_delta.settle_delta(
        recorded.delta_id, approved=True, actor="reviewer",
        rationale="evidence checked, wording now matches the source",
    )
    settled = review.delta_payload(hitl_delta.get_delta(recorded.delta_id))
    assert settled["can_settle"] is False
    assert settled["settled"] is True
    assert settled["disposition"] == DISPOSITION_APPROVED


def test_panel_context_reports_a_missing_delta_rather_than_silently_falling_back(store):
    _record(artifact_id="art-ctx")
    context = review.panel_context("td-does-not-exist")
    assert context["not_found"] == "td-does-not-exist"
    assert context["selected"] is None
    # A stale link must not quietly show a different delta.
    assert len(context["queue"]) == 1


# ── schema parity ────────────────────────────────────────────────────────────

def test_columns_match_the_migration_ddl():
    """``COLUMNS`` drives the INSERT by name, so a column that exists in one and
    not the other fails at runtime inside a swallowed exception — the
    CLAUDE.md INSERT/schema-parity failure mode."""
    from pathlib import Path

    ddl = (
        Path(__file__).resolve().parents[1]
        / "tools" / "db" / "migrations"
        / "20260815063956_trust_hitl_deltas" / "up.sql"
    ).read_text(encoding="utf-8")

    body = ddl.split("CREATE TABLE IF NOT EXISTS trust_deltas (", 1)[1].split(");", 1)[0]
    declared = [
        line.strip().split()[0]
        for line in body.splitlines()
        if line.strip() and not line.strip().startswith("--")
    ]
    assert declared == list(hitl_delta.COLUMNS)


def test_json_columns_serialise_to_valid_json(store):
    recorded = _record(artifact_id="art-json")
    row = store.execute(
        "SELECT findings_before, spans FROM trust_deltas WHERE delta_id = ?",
        (recorded.delta_id,),
    ).fetchone()
    assert isinstance(json.loads(row[0]), list)
    assert isinstance(json.loads(row[1]), list)
