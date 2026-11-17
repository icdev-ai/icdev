# CUI // SP-CTI
"""Wave estimates and dates on whatever the platform database is (icx-mdz-05).

In the PG tier (ICDEV_PYTEST_PG=1, tests/pg_tier_allowlist.txt) that is PostgreSQL: the wave planner sums a plan's
``migration_tasks.estimated_hours`` (a ``real``) per wave over ``legacy_components``, and reads a person's
``decl-wft-mdz-capacity-review`` step from the append-only ``wf_decision_steps`` (written by ``declared.take_step``)
before it dates anything. In the normal suite it is the conftest SQLite database (``wf_db``), with the tables created
from their definitions of record (icx-mdz-04's ambient fixtures, reused).

No fixture redirects the backend on PG or opens a raw connection. The ledger is append-only and may be shared, so the
capacity is declared for an application id this file mints, and every other row it writes is removed afterwards.
"""
from __future__ import annotations

from tests.test_icx_mdz_04_ambient import app, platform_db  # noqa: F401 -- fixtures, reused


def _report(app_id: str, capacity):
    from tools.db.storage import get_connection
    from tools.modernization import app_wave_planner

    conn = get_connection()
    try:
        return app_wave_planner.report(app_id, conn, capacity=capacity)
    finally:
        conn.close()


def test_the_waves_are_dated_only_after_the_capacity_review_is_read_back(app, tmp_path):  # noqa: F811
    from tools.modernization import decisions, wave_schedule
    from tools.workflow_hitl import declared

    cap = tmp_path / "capacity.yaml"
    cap.write_text(f"applications:\n  {app}-shop:\n    hours_per_week: 40\n    start: \"2027-01-04\"\n"
                   "    source: \"mdz05 ambient (synthetic)\"\n", encoding="utf-8")
    rep = _report(app, cap)
    [w] = rep["waves"]                     # no dependency resolved: one wave, Cart (6.5 + 2.0 h) and Order (no task)
    assert w["estimated_hours"] == 8.5 and "starts" not in w
    assert rep["schedule"]["plan_id"] == f"{app}-plan" and rep["schedule"]["capacity"]["review"]["state"] == "unreviewed"
    digest = rep["schedule"]["capacity"]["digest"]
    step = declared.take_step(decisions.TEMPLATE_CAPACITY, surface="migration_canvas.api_modernisation_capacity_review",
                              decision="approve", subject=wave_schedule.subject(f"{app}-shop"),
                              reason="mdz05 ambient: the PMO plan", actor={"id": "u-mdz05", "role": "pm"},
                              payload={"application": f"{app}-shop", "digest": digest})
    assert step["affirmative"] is True
    rep = _report(app, cap)
    [w] = rep["waves"]
    assert (w["starts"], w["ends"]) == ("2027-01-04", "2027-01-05"), "8.5 h at 8 h a day: Monday and Tuesday"
    assert rep["schedule"]["dated"] is True and rep["schedule"]["capacity"]["review"]["by"] == "u-mdz05"
