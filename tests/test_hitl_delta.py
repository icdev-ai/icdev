# CUI // SP-CTI
"""HITL trust deltas — the delta is the reviewable unit (trust-hitl-01).

Five things have to be true, and each has a test class here:

  1. The diff is CLAIM-ANCHORED, not textual: every changed span carries offsets
     that index its own text and a verdict per side, and a verdict that was
     never computed reads ``unknown`` rather than ``supported``.
  2. ``trust_deltas`` is append-only EVIDENCE and ``approval_items`` is mutable
     STATE — settling a delta issues no UPDATE against ``trust_deltas``, and a
     correction appends a successor that leaves its predecessor byte-identical.
  3. A delta nobody answered reads as PENDING, including when its approval item
     failed to queue. A failed enqueue must never present as an approval.
  4. No artifact TEXT reaches ``approval_items``. Those rows are mirrored into
     Slack; the prose lives in ``trust_deltas`` behind the RLS predicate.
  5. ``trust_deltas`` is in ``APPEND_ONLY_TABLES`` and ``approval_items`` is
     still not, on purpose.

Both tables are built from their own migrations' DDL rather than a hand-written
schema, so a column added to one and not the other fails here instead of at
runtime inside a swallowed exception (CLAUDE.md: "every column in an INSERT must
exist in the LIVE schema").
"""
from __future__ import annotations

import ast
import importlib.util
import sqlite3
import sys
from pathlib import Path
from typing import Any

import pytest

from tests._sql_compat import translating
from tools.quality.citation_grounding import decompose_claims, ground_claims
from tools.quality.hitl_delta import (
    COLUMNS,
    OP_ADDED,
    OP_MODIFIED,
    OP_REMOVED,
    OP_UNCHANGED,
    STAGE_EXPORT,
    STAGE_PROMOTE,
    TABLE,
    VERDICT_UNKNOWN,
    Delta,
    HitlDeltaUnavailable,
    anchored_claims,
    compute_delta,
    correct_delta,
    list_deltas,
    pending_deltas,
    record_delta,
    render_delta_summary,
    revision_chain,
    settle_delta,
)

REPO_ROOT = Path(__file__).resolve().parents[1]

# Prose that could only have come from the artifact itself. If this string turns
# up in approval_items, the artifact text leaked into a chat-mirrored row.
ARTIFACT_SECRET = "Contract W91234-25-C-0007 was awarded to ACME Federal."

BEFORE = (
    "The system processed 4,200 requests in fiscal year 2024. [source: SRC-1] "
    "It was deployed to three regions. [source: SRC-1] "
    "Uptime met the service level objective. [source: SRC-1]"
)
AFTER = (
    "The system processed 4,200 requests in fiscal year 2024. [source: SRC-1] "
    "It was deployed to four regions. [source: SRC-1] "
    "Uptime met the service level objective. [source: SRC-1] "
    "A fourth region is planned. [source: SRC-1]"
)
SOURCES = {
    "SRC-1": (
        "The system processed 4,200 requests in fiscal year 2024. "
        "It was deployed to three regions. "
        "Uptime met the service level objective throughout."
    )
}


# ---------------------------------------------------------------------------
# Schema — from the migrations themselves
# ---------------------------------------------------------------------------
def _trust_deltas_ddl() -> str:
    path = (
        REPO_ROOT / "tools" / "db" / "migrations"
        / "20260815063941_trust_hitl_deltas" / "up.sql"
    )
    return path.read_text(encoding="utf-8")


def _approval_items_ddl() -> str:
    path = (
        REPO_ROOT / "tools" / "db" / "migrations"
        / "20260809203855_agov_approval_items" / "up.sql"
    )
    return path.read_text(encoding="utf-8")


def _approval_log_ddl() -> str:
    path = (
        REPO_ROOT / "tools" / "db" / "migrations"
        / "20260803002224_agent_approval_log" / "up.py"
    )
    spec = importlib.util.spec_from_file_location("_m_agent_approval_log", path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module._DDL


def _storage_module():
    """The module ``hitl_delta`` actually resolves ``get_connection`` from.

    ``tools.db.storage`` in ``sys.modules`` is the compat shim, and
    ``import tools.db.storage`` binds the canonical ``icdev.tools.db.storage``
    instead — two different objects. Both this module and ``approval_inbox``
    import the shim from inside their functions, so patching the canonical
    module (which is what monkeypatch's string form resolves to) would silently
    patch nothing and every test below would assert its own no-op.
    """
    return sys.modules["tools.db.storage"]


def _translating_conn(raw: sqlite3.Connection):
    """The connection handed to the code under test.

    ``unclosable``: the store closes its connection in a ``finally`` block, and
    the fixture's connection has to outlive that so the assertions can still
    read the rows.

    A named factory rather than an inline ``translating(raw, ...)`` because
    ``coherence_checker.check_test_db_isolation`` seeds its safe-name set from
    local factory FUNCTIONS — a name bound directly from the imported
    ``_sql_compat`` helper is not propagated, so the correctly-wrapped fixture
    reads to that gate as a raw sqlite3 handle.
    """
    return translating(raw, unclosable=True)


@pytest.fixture
def delta_db(monkeypatch, tmp_path):
    """All three real tables, in one DB, behind the production %s translation."""
    raw = sqlite3.connect(str(tmp_path / "deltas.db"))
    raw.executescript(_trust_deltas_ddl())
    raw.executescript(_approval_items_ddl())
    raw.executescript(_approval_log_ddl())
    conn = _translating_conn(raw)
    storage = _storage_module()
    monkeypatch.setattr(storage, "get_connection", lambda *a, **k: conn)
    monkeypatch.setattr(storage, "table_exists", lambda c, t: True)
    monkeypatch.setenv("ICDEV_APPROVAL_ACTOR", "test-operator")
    monkeypatch.setenv("ICDEV_SESSION_ID", "sess-hitl")
    yield raw
    raw.close()


def _rows(raw: sqlite3.Connection, table: str) -> list[dict[str, Any]]:
    cur = raw.execute(f"SELECT * FROM {table}")
    cols = [d[0] for d in cur.description]
    return [dict(zip(cols, r)) for r in cur.fetchall()]


def _record(**overrides) -> Delta:
    kwargs: dict[str, Any] = dict(
        artifact_id="draft-42",
        stage=STAGE_PROMOTE,
        before_text=BEFORE,
        after_text=AFTER,
        rationale="verified the region count against the deployment record",
        actor="alice",
        sources=SOURCES,
    )
    kwargs.update(overrides)
    return record_delta(**kwargs)


# ---------------------------------------------------------------------------
# 1. The diff is claim-anchored
# ---------------------------------------------------------------------------
class TestComputeDelta:
    def test_spans_anchor_to_claim_offsets_in_their_own_text(self):
        result = compute_delta(BEFORE, AFTER, sources=SOURCES)
        assert result["spans"], "a three-to-four-claim edit produced no spans"
        for span in result["spans"]:
            if "before_claim" in span:
                # The offsets index BEFORE, and slicing them back out returns
                # the claim. A raw character diff has no such property.
                assert (
                    BEFORE[span["before_start"]:span["before_end"]].strip()
                    == span["before_claim"]
                )
            if "after_claim" in span:
                assert (
                    AFTER[span["after_start"]:span["after_end"]].strip()
                    == span["after_claim"]
                )

    def test_a_reworded_claim_is_one_modified_span_not_a_delete_plus_insert(self):
        result = compute_delta(BEFORE, AFTER, sources=SOURCES)
        modified = [s for s in result["spans"] if s["op"] == OP_MODIFIED]
        assert len(modified) == 1
        assert "three regions" in modified[0]["before_claim"]
        assert "four regions" in modified[0]["after_claim"]
        # Both sides present is what makes a side-by-side panel possible.
        assert modified[0]["before_start"] >= 0 and modified[0]["after_start"] >= 0

    def test_an_appended_claim_is_added_and_the_rest_is_unchanged(self):
        result = compute_delta(BEFORE, AFTER, sources=SOURCES)
        added = [s for s in result["spans"] if s["op"] == OP_ADDED]
        assert [s["after_claim"] for s in added] == [
            "A fourth region is planned. [source: SRC-1]"
        ]
        assert result["counts"][OP_UNCHANGED] == 2
        assert result["counts"][OP_REMOVED] == 0
        assert result["changed"] == 2



    def test_an_unrelated_substitution_is_removed_plus_added_not_modified(self):
        # Below the pairing threshold: presenting two different assertions as a
        # single edit invites a reviewer to skim the second as a rewording.
        before = "The system processed 4,200 requests. [source: SRC-1]"
        after = "Nothing further is required at this time. [source: SRC-1]"
        result = compute_delta(before, after)
        ops = sorted(s["op"] for s in result["spans"])
        assert ops == [OP_ADDED, OP_REMOVED]

    def test_changing_only_the_citation_is_a_change(self):
        # The whole point of a TRUST delta: repointing a citation alters what the
        # sentence claims to rest on, even though the prose is identical.
        before = "Uptime met the objective. [source: SRC-1]"
        after = "Uptime met the objective. [source: SRC-9]"
        result = compute_delta(before, after)
        assert [s["op"] for s in result["spans"]] == [OP_MODIFIED]

    def test_an_unchanged_artifact_produces_no_changed_spans(self):
        result = compute_delta(BEFORE, BEFORE, sources=SOURCES)
        assert result["changed"] == 0
        assert result["before_hash"] == result["after_hash"]

    def test_verdicts_are_unknown_when_nothing_could_be_checked(self):
        # Absence of evidence is not evidence of support. A guard that reports
        # `supported` for a claim it never verified cannot fail.
        result = compute_delta(BEFORE, AFTER)
        verdicts = {
            v
            for s in result["spans"]
            for k, v in s.items()
            if k.endswith("_verdict")
        }
        assert verdicts == {VERDICT_UNKNOWN}
        assert result["findings_before"] == []
        assert result["findings_after"] == []

    def test_verdicts_are_real_when_sources_are_supplied(self):
        result = compute_delta(BEFORE, AFTER, sources=SOURCES)
        modified = [s for s in result["spans"] if s["op"] == OP_MODIFIED][0]
        # "three regions" is in the source; "four regions" is not, and the
        # anchor check is decisive regardless of lexical overlap.
        assert modified["before_verdict"] == "supported"
        assert modified["after_verdict"] == "unsupported"

    def test_the_edit_that_broke_grounding_shows_up_as_an_introduced_finding(self):
        result = compute_delta(BEFORE, AFTER, sources=SOURCES)
        record = Delta(
            delta_id="td-x",
            artifact_id="a",
            stage=STAGE_PROMOTE,
            findings_before=result["findings_before"],
            findings_after=result["findings_after"],
        )
        assert record.findings_introduced, (
            "changing three regions to four made a claim unsupported and the "
            "delta did not say so"
        )
        assert record.findings_resolved == []


# ---------------------------------------------------------------------------
# 2. Evidence is append-only; disposition is not stored here
class TestClaimAnchoring:
    """The sentence splitter and the citation convention disagree.

    ``decompose_claims`` breaks at the full stop and the convention puts
    ``[source: X]`` after it, so the raw offsets hand every tag to the FOLLOWING
    sentence and leave a bare tag standing as a claim of its own. These tests pin
    the upstream behaviour and the local compensation together: when
    ``citation_grounding`` is fixed, the first two fail and
    ``hitl_delta.anchored_claims`` can be deleted.
    """

    TEXT = (
        "The system processed 4,200 requests. [source: SRC-1] "
        "It was deployed to three regions. [source: SRC-1]"
    )
    SRC = {
        "SRC-1": (
            "The system processed 4,200 requests. It was deployed to three regions."
        )
    }

    def test_upstream_misattributes_the_tag_to_the_next_sentence(self):
        raw = [c for c, _s, _e in decompose_claims(self.TEXT)]
        assert raw[0] == "The system processed 4,200 requests."   # reads uncited
        assert raw[1].startswith("[source: SRC-1] It was deployed")
        assert raw[-1] == "[source: SRC-1]"                       # asserts nothing

    def test_upstream_scores_the_bare_tag_unsupported(self):
        # The visible cost: a document in which every sentence is correctly cited
        # against its own source scores half-grounded.
        report = ground_claims(self.TEXT, self.SRC)
        assert report["unsupported"] == 1
        assert report["supported_ratio"] < 1.0

    def test_anchored_claims_keeps_each_tag_on_its_own_sentence(self):
        claims = [c for c, _s, _e in anchored_claims(self.TEXT)]
        assert claims == [
            "The system processed 4,200 requests. [source: SRC-1]",
            "It was deployed to three regions. [source: SRC-1]",
        ]

    def test_anchored_offsets_still_index_the_original_text(self):
        for claim, start, end in anchored_claims(self.TEXT):
            assert self.TEXT[start:end].strip() == claim

    def test_the_phantom_finding_does_not_reach_the_delta(self):
        # Without re-anchoring, every delta over correctly-cited prose would ship
        # a fabricated unsupported_claim finding on both sides.
        result = compute_delta(self.TEXT, self.TEXT, sources=self.SRC)
        assert result["findings_before"] == []
        assert result["findings_after"] == []
        assert result["supported_ratio_before"] == 1.0

    def test_text_with_no_citations_is_unaffected(self):
        plain = "One sentence. Another sentence."
        assert anchored_claims(plain) == decompose_claims(plain)

    def test_empty_text_yields_no_claims(self):
        assert anchored_claims("") == []
        assert anchored_claims("   ") == []

    def test_a_leading_bare_tag_is_not_dropped(self):
        # Nothing precedes it, so there is no predecessor to absorb it and the
        # claim has to survive rather than vanish from the diff.
        assert [c for c, _s, _e in anchored_claims("[source: SRC-1]")] == [
            "[source: SRC-1]"
        ]
# ---------------------------------------------------------------------------
class TestSchema:
    def test_columns_match_the_migration(self, delta_db):
        live = [r[1] for r in delta_db.execute(f"PRAGMA table_info({TABLE})").fetchall()]
        assert list(COLUMNS) == live

    def test_insert_matches_the_live_schema(self, delta_db):
        record = _record()
        rows = _rows(delta_db, TABLE)
        assert len(rows) == 1
        row = rows[0]
        # Every column the store names is a column the table has — an INSERT
        # naming a phantom column would have raised out of record_delta.
        assert set(row) == set(COLUMNS)
        assert row["delta_id"] == record.delta_id
        assert row["artifact_id"] == "draft-42"
        assert row["stage"] == STAGE_PROMOTE
        assert row["actor"] == "alice"
        assert row["classification"] == "CUI"
        assert row["created_at"]

    def test_the_migration_is_the_conftest_schema(self):
        """The MINIMAL_ICDEV_SCHEMA copy must not drift from the migration."""
        conftest = (REPO_ROOT / "tests" / "conftest.py").read_text(encoding="utf-8")
        assert "CREATE TABLE IF NOT EXISTS trust_deltas" in conftest
        for column in COLUMNS:
            assert f"    {column} " in conftest, f"{column} missing from conftest schema"

    def test_an_unknown_stage_is_refused_before_the_insert(self, delta_db):
        with pytest.raises(ValueError, match="unknown stage"):
            _record(stage="whenever")
        assert _rows(delta_db, TABLE) == []

    def test_an_override_with_no_rationale_is_refused(self, delta_db):
        # The record this module replaces already said WHO overrode. Refusing an
        # empty reason is what makes force_reason enforceable at the call sites.
        with pytest.raises(ValueError, match="rationale is required"):
            _record(rationale="   ")
        assert _rows(delta_db, TABLE) == []

    def test_a_missing_table_raises_rather_than_dropping_the_evidence(
        self, monkeypatch, delta_db
    ):
        monkeypatch.setattr(_storage_module(), "table_exists", lambda c, t: False)
        with pytest.raises(HitlDeltaUnavailable):
            _record()


class TestAppendOnly:
    def test_settling_writes_no_update_against_trust_deltas(self, delta_db):
        record = _record()
        before = _rows(delta_db, TABLE)[0]

        result = settle_delta(record.delta_id, approved=True, actor="bob", reason="ok")
        assert result["settled"] is True
        assert result["resolution"] == "approved"

        # The evidence row is byte-identical. The disposition landed in the
        # mutable table next door.
        assert _rows(delta_db, TABLE) == [before]
        item = _rows(delta_db, "approval_items")[0]
        assert item["state"] == "resolved"
        assert item["resolution"] == "approved"
        assert item["item_id"] == record.approval_item_id

    def test_settling_writes_the_permanent_decision_row(self, delta_db):
        record = _record()
        settle_delta(record.delta_id, approved=False, actor="bob", reason="not verified")
        log = _rows(delta_db, "agent_approval_log")
        assert len(log) == 1, "the decision was not recorded in agent_approval_log"
        assert log[0]["tool_name"] == f"trust_delta:{record.stage}"

    def test_a_second_settlement_is_an_idempotent_no_op(self, delta_db):
        record = _record()
        assert settle_delta(record.delta_id, approved=True)["settled"] is True
        second = settle_delta(record.delta_id, approved=False)
        assert second["settled"] is False
        # No second, contradictory decision row.
        assert len(_rows(delta_db, "agent_approval_log")) == 1

    def test_a_correction_appends_and_leaves_its_predecessor_untouched(self, delta_db):
        original = _record()
        untouched = _rows(delta_db, TABLE)[0]

        corrected = correct_delta(
            original.delta_id,
            before_text=BEFORE,
            after_text=BEFORE,
            rationale="the region change was wrong; reverted to the source figure",
            actor="alice",
            sources=SOURCES,
        )
        rows = {r["delta_id"]: r for r in _rows(delta_db, TABLE)}
        assert len(rows) == 2
        # Not even a superseded flag on the predecessor: a reviewer may already
        # hold the document that row describes.
        assert rows[original.delta_id] == untouched
        assert rows[corrected.delta_id]["supersedes_delta_id"] == original.delta_id

    def test_the_chain_is_derived_at_read_time(self, delta_db):
        original = _record()
        corrected = correct_delta(
            original.delta_id,
            before_text=BEFORE,
            after_text=BEFORE,
            rationale="reverted",
            actor="alice",
        )
        chain = [d.delta_id for d in revision_chain(corrected.delta_id)]
        assert chain == [original.delta_id, corrected.delta_id]
        assert revision_chain(original.delta_id) == revision_chain(corrected.delta_id)

    def test_correcting_a_delta_that_does_not_exist_is_refused(self, delta_db):
        with pytest.raises(ValueError, match="no delta"):
            correct_delta(
                "td-nope", before_text="a.", after_text="b.", rationale="because"
            )
        assert _rows(delta_db, TABLE) == []

    def test_a_no_op_override_is_detectable_from_the_hashes_alone(self, delta_db):
        record = _record(after_text=BEFORE)
        assert record.is_no_op is True
        row = _rows(delta_db, TABLE)[0]
        assert row["before_hash"] == row["after_hash"]
        # And the reviewer is told, in the body they actually receive.
        _title, body = render_delta_summary(record)
        assert "NO-OP" in body


# ---------------------------------------------------------------------------
# 3. Unanswered means pending — including when the ask never queued
# ---------------------------------------------------------------------------
class TestPending:
    def test_a_recorded_delta_is_pending_until_it_is_settled(self, delta_db):
        record = _record()
        assert [d.delta_id for d in pending_deltas()] == [record.delta_id]
        settle_delta(record.delta_id, approved=True)
        assert pending_deltas() == []
        # Still listable as evidence — settling removes it from the queue, not
        # from the record.
        assert [d.delta_id for d in list_deltas()] == [record.delta_id]

    def test_a_delta_whose_ask_failed_to_queue_is_still_pending(
        self, monkeypatch, delta_db
    ):
        import tools.agent_runtime.approval_inbox as inbox

        def boom(**_kwargs):
            raise inbox.ApprovalInboxUnavailable("channel down")

        monkeypatch.setattr(inbox, "enqueue", boom)
        record = _record()

        # The evidence survived the failed enqueue...
        assert _rows(delta_db, TABLE)[0]["delta_id"] == record.delta_id
        assert _rows(delta_db, "approval_items") == []
        # ...and the delta reads as unanswered, not as approved. A failed
        # enqueue turning into a silent approval is the failure mode this
        # ordering exists to prevent.
        assert [d.delta_id for d in pending_deltas()] == [record.delta_id]

    def test_a_delta_recorded_with_no_ask_is_pending(self, delta_db):
        record = _record(raise_ask=False)
        assert record.approval_item_id == ""
        assert _rows(delta_db, "approval_items") == []
        assert [d.delta_id for d in pending_deltas()] == [record.delta_id]

    def test_a_superseded_delta_drops_out_of_the_queue(self, delta_db):
        original = _record()
        corrected = correct_delta(
            original.delta_id,
            before_text=BEFORE,
            after_text=BEFORE,
            rationale="reverted",
            actor="alice",
        )
        pending = [d.delta_id for d in pending_deltas()]
        assert original.delta_id not in pending
        assert corrected.delta_id in pending

    def test_settling_an_unknown_delta_reports_failure_rather_than_success(
        self, delta_db
    ):
        result = settle_delta("td-nope", approved=True)
        assert result["settled"] is False
        assert _rows(delta_db, "agent_approval_log") == []

    def test_pending_filters_by_artifact_and_stage(self, delta_db):
        a = _record(artifact_id="draft-1", stage=STAGE_PROMOTE)
        b = _record(artifact_id="draft-2", stage=STAGE_EXPORT)
        assert [d.delta_id for d in pending_deltas(artifact_id="draft-1")] == [a.delta_id]
        assert [d.delta_id for d in pending_deltas(stage=STAGE_EXPORT)] == [b.delta_id]


# ---------------------------------------------------------------------------
# 4. No artifact TEXT reaches the chat-mirrored table
# ---------------------------------------------------------------------------
class TestNoTextLeak:
    def test_the_approval_item_carries_no_line_of_the_artifact(self, delta_db):
        record = _record(
            before_text=f"{ARTIFACT_SECRET} [source: SRC-1]",
            after_text=f"{ARTIFACT_SECRET} It shipped in March. [source: SRC-1]",
            sources=None,
        )
        blob = repr(_rows(delta_db, "approval_items"))
        assert ARTIFACT_SECRET not in blob
        assert "ACME" not in blob
        # The text IS in the evidence table — that is the whole point of it.
        assert ARTIFACT_SECRET in _rows(delta_db, TABLE)[0]["before_text"]
        assert record.approval_item_id

    def test_the_rendered_summary_carries_counts_not_prose(self, delta_db):
        record = _record()
        title, body = render_delta_summary(record)
        assert "draft-42" in title
        assert "three regions" not in body
        assert "four regions" not in body
        assert "1 modified" in body
        assert "1 added" in body
        assert record.before_hash[:16] in body

    def test_to_dict_without_text_drops_the_prose_but_keeps_the_counts(self, delta_db):
        record = _record()
        payload = record.to_dict(with_text=False)
        assert payload["before_text"] == ""
        assert payload["after_text"] == ""
        assert all(
            "before_claim" not in s and "after_claim" not in s
            for s in payload["spans"]
        )
        assert payload["changed_span_count"] == 2
        assert payload["spans"], "dropping the prose must not drop the span structure"

    def test_a_settled_decision_row_carries_no_artifact_text(self, delta_db):
        record = _record(
            before_text=f"{ARTIFACT_SECRET} [source: SRC-1]",
            after_text=f"{ARTIFACT_SECRET} [source: SRC-2]",
            sources=None,
        )
        settle_delta(record.delta_id, approved=True, reason="checked the award notice")
        assert ARTIFACT_SECRET not in repr(_rows(delta_db, "agent_approval_log"))


# ---------------------------------------------------------------------------
# 5. The append-only registration itself
# ---------------------------------------------------------------------------
def _append_only_tables() -> set[str]:
    """Parse ``APPEND_ONLY_TABLES`` out of the pre_tool_use hook.

    Read from source rather than imported: the hook is a Claude Code entry
    point, not an importable module, and it is the file CLAUDE.md names as the
    canonical list.
    """
    src = (REPO_ROOT / ".claude" / "hooks" / "pre_tool_use.py").read_text(
        encoding="utf-8"
    )
    for node in ast.walk(ast.parse(src)):
        if isinstance(node, ast.Assign) and any(
            isinstance(t, ast.Name) and t.id == "APPEND_ONLY_TABLES"
            for t in node.targets
        ):
            return {
                el.value
                for el in node.value.elts  # type: ignore[attr-defined]
                if isinstance(el, ast.Constant) and isinstance(el.value, str)
            }
    raise AssertionError("APPEND_ONLY_TABLES not found in the hook")


class TestAppendOnlyRegistration:
    def test_trust_deltas_is_registered(self):
        assert TABLE in _append_only_tables()

    def test_approval_items_is_still_not_registered(self):
        # The split only works if the mutable half stays mutable. Adding it here
        # would make resolving an item a hook violation.
        assert "approval_items" not in _append_only_tables()

    def test_the_module_issues_no_update_or_delete_against_the_evidence_table(self):
        """Grep the source, not the behaviour.

        The tests above prove the two write paths this module HAS do not mutate
        evidence. This proves no third one was added later: an UPDATE naming
        ``trust_deltas`` anywhere in the module is the defect, whether or not a
        test happens to exercise it.
        """
        src = (REPO_ROOT / "tools" / "quality" / "hitl_delta.py").read_text(
            encoding="utf-8"
        )
        code = "\n".join(
            line for line in src.splitlines() if not line.lstrip().startswith("#")
        )
        lowered = code.lower()
        for forbidden in (f"update {TABLE}", f"delete from {TABLE}"):
            assert forbidden not in lowered, f"{forbidden!r} in hitl_delta.py"
