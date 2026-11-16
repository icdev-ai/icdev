# CUI // SP-CTI
"""Modernisation's new reads and steps on whatever the process database is (icx-mdz-01).

In the PG tier (ICDEV_PYTEST_PG=1, tests/pg_tier_allowlist.txt) that is PostgreSQL:
the ledger read every modernisation page uses (``decisions.decided``), the observed
drafts listing, and the twin's collection of both. In the normal suite it is the
conftest SQLite database (``wf_db``).

No fixture here redirects the backend on PG or opens a raw connection. The ledger is
append-only and may be shared, so every assertion is scoped to ids this file mints.
"""
from __future__ import annotations

import os
import uuid

import pytest

PM = {"id": "mdz01-u-pm", "role": "pm"}


def _pg() -> bool:
    return os.environ.get("ICDEV_PYTEST_PG", "").lower() in ("1", "true", "yes")


@pytest.fixture(autouse=True)
def platform_db(request):
    """The conftest SQLite platform DB, unless this is the PG tier (the ambient PG)."""
    return None if _pg() else request.getfixturevalue("wf_db")


def test_a_recorded_strategy_reads_back_from_the_ledger():
    from tools.modernization import decisions
    from tools.workflow_hitl import declared

    subject = f"mdz01-ma-{uuid.uuid4().hex[:8]}"
    payload = {"assessment_id": subject, "legacy_app_id": "mdz01-app", "recommended_strategy": "rehost",
               "strategy": "replatform"}
    step = declared.take_step(decisions.TEMPLATE_7R, surface="migration_api_legacy.record_strategy",
                              decision="record", subject=subject, reason="ambient", payload=payload, actor=PM)
    got = decisions.decided(decisions.TEMPLATE_7R, [subject])
    assert list(got) == [subject] and got[subject]["step_id"] == step["step_id"]
    rec = decisions.strategy_record(got[subject])
    assert rec["strategy"] == "replatform" and rec["recommended"] == "rehost" and rec["binding"] is False
    assert decisions.decided(decisions.TEMPLATE_CAM, [subject]) == {}


def test_an_observed_draft_is_listed_and_reviewed_once(monkeypatch):
    from tools.workflow_canvas import observed_processify as op

    monkeypatch.setattr(op, "_audit", lambda *a, **k: None)
    name = f"mdz01 ambient draft {uuid.uuid4().hex[:8]}"
    wid = op.save_draft(op.build_draft({
        "status": "ok", "kind": "process", "scenario": {"executable": "/opt/app/bin/app"},
        "record": {"evidence_id": "mdz01-ev", "normalized_result": {"steps": [{"label": "open"}]}}},
        name=name))
    pending = {d["workflow_id"] for d in op.list_drafts(state=op.REVIEW_PENDING)}
    assert wid in pending
    op.review_draft(wid, "approve", reviewer=PM["id"])
    approved = [d for d in op.list_drafts(state=op.REVIEW_APPROVED) if d["workflow_id"] == wid]
    assert approved and approved[0]["review"]["reviewer"] == PM["id"]
    with pytest.raises(ValueError):
        op.review_draft(wid, "reject", reviewer=PM["id"])


def test_the_twin_reads_the_ledger_and_the_drafts():
    from tools.twin_core.adapters.modernisation import ModernisationGovernanceTwin

    ev = ModernisationGovernanceTwin().collect()
    assert "ledger" not in ev["errors"], ev["errors"]
    assert "drafts" not in ev["errors"], ev["errors"]
    assert isinstance(ev["strategy_records"], list) and isinstance(ev["drafts"], list)
