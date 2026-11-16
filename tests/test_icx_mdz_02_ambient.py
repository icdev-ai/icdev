# CUI // SP-CTI
"""The may-plan refusal record and its readers on whatever the process database is (icx-mdz-02).

In the PG tier (ICDEV_PYTEST_PG=1, tests/pg_tier_allowlist.txt) that is PostgreSQL: a refusal is written to the
append-only, hash-chained audit trail by ``plan_gate.require`` and read back by ``plan_gate.refusals`` (the /migration
page and the MCP tool) and by the modernisation twin, whose thread resolves to the row. In the normal suite it is the
conftest SQLite database (``wf_db``).

No fixture here redirects the backend on PG or opens a raw connection. The audit trail is append-only and may be
shared, so every assertion is scoped to an application id this file mints.
"""
from __future__ import annotations

import os
import uuid

import pytest


def _pg() -> bool:
    return os.environ.get("ICDEV_PYTEST_PG", "").lower() in ("1", "true", "yes")


@pytest.fixture(autouse=True)
def platform_db(request, monkeypatch):
    """The conftest SQLite platform DB, unless this is the PG tier (the ambient PG). No decisions export is set."""
    monkeypatch.delenv("ICDEV_ICOS_DECISIONS", raising=False)
    monkeypatch.delenv("ICDEV_ICOS_JWKS", raising=False)
    return None if _pg() else request.getfixturevalue("wf_db")


def test_a_refusal_is_audited_and_read_back():
    from tools.modernization import plan_gate

    app = f"mdz02-app-{uuid.uuid4().hex[:8]}"
    with pytest.raises(plan_gate.PlanRefused) as exc:
        plan_gate.require(app, "rehost", tool="icdev-it/monolith_decomposer", project_id="mdz02-proj")
    answer = exc.value.answer
    assert answer["status"] == "missing" and answer["allowed"] is False and answer["audit_id"]
    [row] = plan_gate.refusals([app])
    assert row["id"] == answer["audit_id"] and row["tool"] == "icdev-it/monolith_decomposer"
    assert row["status"] == "missing" and row["strategy_asked"] == "rehost" and "decisions export" in row["reason"]


def test_the_twin_reads_the_refusal_and_its_thread_resolves():
    from tools.modernization import plan_gate
    from tools.twin_core import governance
    from tools.twin_core.adapters.modernisation import ModernisationGovernanceTwin

    app = f"mdz02-app-{uuid.uuid4().hex[:8]}"
    with pytest.raises(plan_gate.PlanRefused) as exc:
        plan_gate.require(app, None, tool="icdev-it/cam_refactor_engine")
    ev = ModernisationGovernanceTwin().collect()
    assert "refusals" not in ev["errors"], ev["errors"]
    assert any(r["application"] == app for r in ev["refusals"])
    thread = governance.resolve_thread(f"audit_trail:{exc.value.answer['audit_id']}")
    assert thread["resolved"] is True and thread["record"]["action"] == f"{plan_gate.REFUSED}:{app}"
