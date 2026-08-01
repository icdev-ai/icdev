# CUI // SP-CTI
"""HITL review decisions on SSP fragments must leave an audit record.

dic_ssp_fragments is a mutable workflow table holding only the CURRENT status,
so before this change an approve/reject left no evidence of who decided what,
when. Those fragments become SSP content, so that record is the cATO audit
trail. These tests pin the behaviour, including the fail-closed contract.

conftest forces ICDEV_STORAGE_BACKEND=sqlite; no network, no LLM.
"""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import patch

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))


@pytest.fixture
def acoic_db(tmp_path, monkeypatch):
    """Point acoic at a temp DB via the real StorageConnection (%s translation)."""
    from tools.db.storage import get_connection as _real

    db_path = str(tmp_path / "acoic_audit.db")

    def _factory(*a, **k):
        return _real(db_path=db_path)

    import tools.document_intelligence.acoic as acoic_mod
    monkeypatch.setattr(acoic_mod, "get_connection", _factory)
    return _factory


def _seed_fragment(factory, fragment_id="frag-1"):
    from tools.document_intelligence import acoic

    conn = factory()
    try:
        acoic._ensure_schema(conn)
        conn.cursor().execute(
            "INSERT INTO dic_ssp_fragments (fragment_id, control_id, document_id, "
            "regen_item_id, status, created_at) VALUES (%s, %s, %s, %s, %s, %s)",
            (fragment_id, "AC-2", "doc-9", None, "pending_review", "2026-01-01T00:00:00Z"),
        )
        conn.commit()
    finally:
        conn.close()


class TestReviewIsAudited:
    def test_approval_writes_an_audit_record(self, acoic_db):
        from tools.document_intelligence import acoic

        _seed_fragment(acoic_db)
        with patch("tools.audit.audit_logger.log_event") as log_event:
            log_event.return_value = 1
            acoic.approve_fragment("frag-1", reviewed_by="isso@example.mil")

        assert log_event.called, "an SSP fragment approval must be audited"
        kw = log_event.call_args.kwargs
        assert kw["event_type"] == "dic.ssp_fragment.review"
        assert kw["action"] == "ssp_fragment.approved"
        assert kw["actor"] == "isso@example.mil"
        # The auditor needs to know WHICH control and document were affected.
        assert kw["details"]["fragment_id"] == "frag-1"
        assert kw["details"]["control_id"] == "AC-2"
        assert kw["details"]["document_id"] == "doc-9"

    @pytest.mark.parametrize(
        "fn,expected",
        [
            ("approve_fragment", "ssp_fragment.approved"),
            ("reject_fragment", "ssp_fragment.rejected"),
            ("request_revision", "ssp_fragment.needs_revision"),
        ],
    )
    def test_every_human_decision_is_audited(self, acoic_db, fn, expected):
        from tools.document_intelligence import acoic

        _seed_fragment(acoic_db)
        with patch("tools.audit.audit_logger.log_event") as log_event:
            log_event.return_value = 1
            getattr(acoic, fn)("frag-1", reviewed_by="reviewer")
        assert log_event.call_args.kwargs["action"] == expected

    def test_audit_is_fail_closed(self, acoic_db):
        """An approval that cannot be audited must not silently stand (AU-5)."""
        from tools.document_intelligence import acoic

        _seed_fragment(acoic_db)
        with patch("tools.audit.audit_logger.log_event", side_effect=RuntimeError("audit down")):
            with pytest.raises(RuntimeError):
                acoic.approve_fragment("frag-1", reviewed_by="isso")

    def test_raise_on_error_is_requested(self, acoic_db):
        from tools.document_intelligence import acoic

        _seed_fragment(acoic_db)
        with patch("tools.audit.audit_logger.log_event") as log_event:
            log_event.return_value = 1
            acoic.approve_fragment("frag-1", reviewed_by="isso")
        assert log_event.call_args.kwargs["raise_on_error"] is True

    def test_no_audit_when_nothing_changed(self, acoic_db):
        """A review of a non-existent fragment is not a decision — don't audit it."""
        from tools.document_intelligence import acoic

        _seed_fragment(acoic_db)
        with patch("tools.audit.audit_logger.log_event") as log_event:
            acoic.approve_fragment("frag-does-not-exist", reviewed_by="isso")
        assert not log_event.called


class TestQueueAdvanceStillWorks:
    def test_linked_queue_item_still_advances(self, acoic_db):
        """The audit SELECT widened to 3 columns — the positional fallback for
        regen_item_id must not silently read control_id instead."""
        from tools.document_intelligence import acoic

        conn = acoic_db()
        try:
            acoic._ensure_schema(conn)
            conn.cursor().execute(
                "INSERT INTO dic_ssp_fragments (fragment_id, control_id, document_id, "
                "regen_item_id, status, created_at) VALUES (%s, %s, %s, %s, %s, %s)",
                ("frag-2", "AC-3", "doc-1", "item-1", "pending_review", "2026-01-01T00:00:00Z"),
            )
            conn.commit()
        finally:
            conn.close()

        acoic.enqueue_regen("doc-1", severity="high", dedup_key="k-audit")
        with patch("tools.audit.audit_logger.log_event") as log_event:
            log_event.return_value = 1
            with patch.object(acoic, "_set_queue_state") as set_state:
                acoic.approve_fragment("frag-2", reviewed_by="isso")
        set_state.assert_called_once_with("item-1", "approved")


class TestAppendOnlyDeclarationIsTrue:
    def test_mutable_workflow_tables_are_not_declared_append_only(self):
        """These are UPDATEd by design (regen state machine, drift processed
        flag). Declaring them append-only would break HITL the moment the
        constant is wired up."""
        from tools.document_intelligence.constants import APPEND_ONLY_TABLES

        assert "dic_acoic_regen_queue" not in APPEND_ONLY_TABLES
        assert "dic_drift_events" not in APPEND_ONLY_TABLES
        assert "dic_versions" in APPEND_ONLY_TABLES
