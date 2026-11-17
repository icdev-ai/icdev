# CUI // SP-CTI
"""Wave estimates and dates from a declared, reviewed team capacity (icx-mdz-05).

Covers:
  * no capacity declared: no wave is dated, and the planner, the page, the MCP tool and the bundle say why
    (unmeasured); nothing is made up;
  * a capacity declared but not approved by a person at its digest (or rejected, or changed since) dates nothing;
  * an approved capacity gives exact dates: one wave at a time, in dependency order (not the order the waves are
    listed in), at hours_per_week / 5 a working day, Monday to Friday, from the declared start;
  * a wave's estimate is the sum of its tasks' own estimates, and a wave with none is undated, as is every wave after it;
  * the capacity file refuses an entry with another field, no source, hours that are not a positive number, or a start
    that is not a date; it ships empty;
  * the bundle carries the dates as the contract's ``starts`` / ``ends`` and says why, or from which capacity digest,
    in fields the pinned 1.189.0 contract already has; it still validates;
  * reviewing the capacity is a person's declared step bound to its digest; the page shows estimates, dates and the
    review; the twin flags an unreviewed capacity and a plan with undated waves, threaded, and replays; the MCP read
    tool is read-only.

Run: pytest tests/test_icx_mdz_05_wave_dates.py -v
"""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

import tests.test_icx_mdz_03_bundle_features as mdz03
from tests.test_icx_mdz_03_bundle_features import COR, _bundle_evidence, _client, _conn, _report
from tests.test_icx_mdz_04_bundle_fields import _plan

shop, canvas = mdz03.shop, mdz03.canvas      # icx-mdz-03's fixtures (a synthetic shop, the migration canvas), reused

ROOT = Path(__file__).resolve().parents[1]
AT = "2026-11-16T00:00:00Z"
PAGE = "tools/dashboard/templates/migration_canvas/modernisation.html"


def _waves(*rows):
    """Planner-shaped waves: (number, depends_on)."""
    return [{"id": f"w{n}", "name": f"Wave {n}", "wave_number": n, "status": "planned", "depends_on": list(d)}
            for n, d in rows]


def _entry(hours=40, start="2027-01-04", source="PMO staffing plan FY27 (synthetic)"):
    from tools.modernization import wave_schedule as ws

    e = {"hours_per_week": float(hours), "start": start, "source": source}
    return {**e, "digest": ws.digest("Shop", e)}


def _capacity_file(tmp_path, monkeypatch, body: str) -> Path:
    from tools.modernization import wave_schedule as ws

    f = tmp_path / "capacity.yaml"
    f.write_text(body, encoding="utf-8")
    monkeypatch.setattr(ws, "capacity_path", lambda path=None: Path(path) if path else f)
    return f


CAPACITY = "version: 1\napplications:\n  Shop:\n    hours_per_week: 40\n    start: 2027-01-04\n" \
           "    source: \"PMO staffing plan FY27, team Alpha (synthetic)\"\n"


# ── the schedule, pure ───────────────────────────────────────────────────────


def test_no_capacity_declared_dates_nothing_and_says_why():
    from tools.modernization import wave_schedule as ws

    out = ws.schedule(_waves((1, ()), (2, (1,))), {"w1": 8.0, "w2": 4.0}, key=None, entry=None, review=None, app="Shop")
    assert out["dated"] is False and out["capacity"] is None
    assert "no team capacity is declared for Shop" in out["reason"] and "unmeasured" in out["reason"]
    assert all("starts" not in w and "ends" not in w and w["undated"] == out["reason"] for w in out["waves"].values())
    assert {k: w["estimated_hours"] for k, w in out["waves"].items()} == {"w1": 8.0, "w2": 4.0}, "the estimate stands"


@pytest.mark.parametrize("review, why", [
    (None, "no person has reviewed it"), ({"state": "unreviewed"}, "no person has reviewed it"),
    ({"state": "rejected"}, "a person rejected it"), ({"state": "stale"}, "it changed since a person reviewed it"),
    ({"state": "unreadable", "why": "no ledger"}, "the review ledger could not be read")])
def test_a_capacity_no_person_approved_at_its_digest_dates_nothing(review, why):
    from tools.modernization import wave_schedule as ws

    out = ws.schedule(_waves((1, ())), {"w1": 8.0}, key="Shop", entry=_entry(), review=review, app="Shop")
    assert out["dated"] is False and why in out["reason"] and "decl-wft-mdz-capacity-review" in out["reason"]
    assert "starts" not in out["waves"]["w1"] and out["capacity"]["digest"] == _entry()["digest"]


def test_a_reviewed_capacity_gives_exact_dates():
    from tools.modernization import wave_schedule as ws

    # 40 h/week = 8 h a working day from Monday 2027-01-04. w1: 12 h -> Mon (8) and Tue (4); w2 starts Tue with the
    # 4 h left that day, 20 h -> Tue, Wed, Thu (to hour 32 = end of Thu); w3: 10 h -> Fri and Mon after the weekend.
    out = ws.schedule(_waves((1, ()), (2, (1,)), (3, (2,))), {"w1": 12.0, "w2": 20.0, "w3": 10.0}, key="Shop",
                      entry=_entry(), review={"state": "reviewed", "step_id": "step-1"}, app="Shop")
    got = {k: (w["starts"], w["ends"]) for k, w in out["waves"].items()}
    assert got == {"w1": ("2027-01-04", "2027-01-05"), "w2": ("2027-01-05", "2027-01-07"),
                   "w3": ("2027-01-08", "2027-01-11")}
    assert out["dated"] is True and _entry()["digest"][:12] in out["reason"] and "step-1" in out["reason"]
    # a start on a Saturday begins the next Monday; 20 h/week is 4 h a day
    sat = ws.schedule(_waves((1, ())), {"w1": 6.0}, key="Shop", entry=_entry(hours=20, start="2027-01-09"),
                      review={"state": "reviewed"}, app="Shop")
    assert (sat["waves"]["w1"]["starts"], sat["waves"]["w1"]["ends"]) == ("2027-01-11", "2027-01-12")


def test_dependency_order_is_respected_whatever_the_listing_order():
    from tools.modernization import wave_schedule as ws

    # listed 1, 2, 3 but wave 1 depends on wave 3, and wave 3 on wave 2: the team works 2, 3, 1
    waves = _waves((1, (3,)), (2, ()), (3, (2,)))
    out = ws.schedule(waves, {"w1": 8.0, "w2": 8.0, "w3": 8.0}, key="Shop", entry=_entry(),
                      review={"state": "reviewed"}, app="Shop")
    w = out["waves"]
    assert (w["w2"]["starts"], w["w3"]["starts"], w["w1"]["starts"]) == ("2027-01-04", "2027-01-05", "2027-01-06")
    for n, deps in ((1, (3,)), (3, (2,))):
        for d in deps:
            assert w[f"w{n}"]["starts"] >= w[f"w{d}"]["ends"], f"wave {n} starts only after wave {d} ends"
    cyc = ws.schedule(_waves((1, (2,)), (2, (1,))), {"w1": 1.0, "w2": 1.0}, key="Shop", entry=_entry(),
                      review={"state": "reviewed"}, app="Shop")
    assert cyc["dated"] is False and "cycle" in cyc["reason"]
    ghost = ws.schedule(_waves((1, (9,))), {"w1": 1.0}, key="Shop", entry=_entry(), review={"state": "reviewed"},
                        app="Shop")
    assert ghost["dated"] is False and "not waves of this plan" in ghost["reason"]


def test_a_wave_with_no_estimate_is_undated_and_so_is_every_wave_after_it():
    from tools.modernization import wave_schedule as ws

    out = ws.schedule(_waves((1, ()), (2, (1,)), (3, (2,))), {"w1": 8.0, "w2": None, "w3": 8.0}, key="Shop",
                      entry=_entry(), review={"state": "reviewed"}, app="Shop")
    w = out["waves"]
    assert (w["w1"]["starts"], w["w1"]["ends"]) == ("2027-01-04", "2027-01-04")
    assert "starts" not in w["w2"] and "wave 2 has no estimate" in w["w2"]["undated"]
    assert "starts" not in w["w3"] and "after wave(s) that are not dated" in w["w3"]["undated"]
    assert "estimated_hours" not in w["w2"], "none, never 0"
    assert out["dated"] is False and "1 of 3 wave(s) dated" in out["reason"]


# ── the capacity file ────────────────────────────────────────────────────────


def test_the_capacity_file_ships_empty_and_refuses_what_it_cannot_read(tmp_path):
    import yaml

    from tools.modernization import wave_schedule as ws

    shipped = yaml.safe_load((ROOT / ws.CAPACITY_REL).read_text(encoding="utf-8"))
    assert shipped == {"version": 1, "applications": {}}, "real team capacity is the owner's to declare"
    f = tmp_path / "c.yaml"
    for body, msg in (
            ("applications:\n  Shop:\n    hours_per_week: 40\n    start: 2027-01-04\n", "names no source"),
            ("applications:\n  Shop:\n    hours_per_week: 40\n    start: 2027-01-04\n    source: x\n    team: a\n",
             "names ['team']"),
            ("applications:\n  Shop:\n    hours_per_week: yes\n    start: 2027-01-04\n    source: x\n", "positive number"),
            ("applications:\n  Shop:\n    hours_per_week: 0\n    start: 2027-01-04\n    source: x\n", "positive number"),
            ("applications:\n  Shop:\n    hours_per_week: 40\n    start: next monday\n    source: x\n", "not an ISO date")):
        f.write_text(body, encoding="utf-8")
        with pytest.raises(ws.CapacityError, match=msg.replace("[", r"\[").replace("]", r"\]")):
            ws.declared(f)
    f.write_text(CAPACITY, encoding="utf-8")
    got = ws.declared(f)["Shop"]
    assert (got["hours_per_week"], got["start"]) == (40.0, "2027-01-04") and len(got["digest"]) == 64
    assert ws.declared(f)["Shop"]["digest"] == got["digest"], "the same entry, the same digest"
    f.write_text(CAPACITY.replace("40", "32"), encoding="utf-8")
    assert ws.declared(f)["Shop"]["digest"] != got["digest"], "a changed entry is a new one"


def test_the_e2e_capacity_file_is_honoured_only_under_tests(tmp_path, monkeypatch):
    from tools.modernization import wave_schedule as ws

    monkeypatch.setenv(ws.E2E_ENV, str(tmp_path / "c.yaml"))
    assert ws.capacity_path() == ROOT / ws.CAPACITY_REL, "outside tests/: refused, the reviewed args file stands"
    inside = ROOT / "tests" / "e2e" / "fixtures" / ".mdz05_capacity.yaml"
    monkeypatch.setenv(ws.E2E_ENV, str(inside))
    assert ws.capacity_path() == inside.resolve()


# ── the planner and the bundle over a planned shop ───────────────────────────


TASKS = [("c-cart", 6.0), ("c-cart", 2.5), ("c-catalog", 4.0), ("c-trade", 12.0), ("c-svc", 3.5), (None, 8.0)]


def _planned(shop):
    """The shop planned: a task estimate on every component (the plan-level task belongs to no wave)."""
    _plan(shop, TASKS)
    with sqlite3.connect(str(shop)) as c:
        rows = c.execute("SELECT id FROM legacy_components WHERE legacy_app_id = 'app-1'").fetchall()
        for (cid,) in rows:
            if cid not in {t[0] for t in TASKS}:
                c.execute("INSERT INTO migration_tasks (id, plan_id, legacy_component_id, task_type, title, "
                          "estimated_hours) VALUES (?, 'mplan-1', ?, 'deploy', 'x', 1.0)", (f"t-{cid}", cid))


def _waves_report(**kw):
    from tools.modernization import app_wave_planner

    conn = _conn()
    try:
        return app_wave_planner.report("app-1", conn, **kw)
    finally:
        conn.close()


def test_estimated_hours_is_the_sum_of_the_plans_own_task_estimates(shop):
    _planned(shop)
    rep = _waves_report()
    with sqlite3.connect(str(shop)) as c:
        names = dict(c.execute("SELECT id, COALESCE(qualified_name, name) FROM legacy_components").fetchall())
        tasks = c.execute("SELECT legacy_component_id, estimated_hours FROM migration_tasks").fetchall()
    for w in rep["waves"]:
        want = sum(h for comp, h in tasks if comp is not None and names[comp] in w["components"])
        assert w["estimated_hours"] == round(want, 2), w["id"]
    assert sum(w["estimated_hours"] for w in rep["waves"]) == sum(h for comp, h in tasks if comp), \
        "every component's tasks are in exactly one wave; the plan-level task in none"
    assert rep["schedule"]["plan_id"] == "mplan-1" and rep["schedule"]["dated"] is False
    assert "no team capacity is declared for Shop" in rep["schedule"]["reason"]


def test_before_a_plan_no_estimate_and_no_date_and_the_report_says_why(shop):
    rep = _waves_report()
    assert rep["schedule"]["plan_id"] is None and "no migration plan yet" in rep["schedule"]["reason"]
    assert all("estimated_hours" not in w and "starts" not in w for w in rep["waves"])


def _review(key: str, digest: str, decision: str = "approve"):
    from tools.modernization import decisions, wave_schedule as ws
    from tools.workflow_hitl import declared

    return declared.take_step(decisions.TEMPLATE_CAPACITY, surface="migration_canvas.api_modernisation_capacity_review",
                              decision=decision, subject=ws.subject(key), reason="mdz05 test: the PMO plan",
                              actor={"id": "u-pm", "role": "pm"}, payload={"application": key, "digest": digest})


def test_the_planner_dates_the_waves_only_once_the_capacity_is_approved_and_the_bundle_carries_them(shop, tmp_path,
                                                                                                   monkeypatch):
    from tools.modernization import apptwin_export as ax
    from tools.modernization import wave_schedule as ws

    _planned(shop)
    f = _capacity_file(tmp_path, monkeypatch, CAPACITY)
    rep = _waves_report()
    cap = rep["schedule"]["capacity"]
    assert cap["application"] == "Shop" and cap["review"] == {"state": "unreviewed"} and not rep["schedule"]["dated"]
    _review("Shop", cap["digest"])
    rep = _waves_report()
    assert rep["schedule"]["dated"] is True and rep["schedule"]["capacity"]["review"]["state"] == "reviewed"
    assert cap["digest"][:12] in rep["schedule"]["reason"]
    order = sorted(rep["waves"], key=lambda w: w["wave_number"])
    assert order[0]["starts"] == "2027-01-04" and all(a["ends"] <= b["starts"] for a, b in zip(order, order[1:]))
    conn = _conn()
    try:
        b = ax.build("app-1", conn, at=AT, extract=_report(), waves=rep)
    finally:
        conn.close()
    assert ax.validate(b) == [], "the dates ride in the contract's own fields"
    carried = {w["id"]: (w.get("starts"), w.get("ends"), w.get("estimated_hours")) for w in b["migration"]["waves"]}
    assert carried == {w["id"]: (w["starts"], w["ends"], w["estimated_hours"]) for w in rep["waves"]}
    st = b["sections"]["migration.waves"]
    assert st["state"] == "present" and "dated from" in st["reason"] and cap["digest"][:12] in st["reason"]
    # the entry changes: the review is stale, and nothing is dated
    f.write_text(CAPACITY.replace("40", "30"), encoding="utf-8")
    rep = _waves_report()
    assert rep["schedule"]["capacity"]["review"]["state"] == "stale" and rep["schedule"]["dated"] is False
    assert all("starts" not in w for w in rep["waves"])
    assert ws.declared()["Shop"]["hours_per_week"] == 30.0


def test_without_a_capacity_the_bundle_says_why_and_still_validates(shop):
    from tools.modernization import apptwin_export as ax

    _planned(shop)
    rep = _waves_report()
    conn = _conn()
    try:
        b = ax.build("app-1", conn, at=AT, extract=_report(), waves=rep)
    finally:
        conn.close()
    assert ax.validate(b) == []
    assert all("starts" not in w and "ends" not in w for w in b["migration"]["waves"])
    assert "no team capacity is declared" in b["sections"]["migration.waves"]["reason"]
    _drop_plan(shop)
    conn = _conn()
    try:
        derived = ax.build("app-1", conn, at=AT, extract=_report(), waves=_waves_report())
    finally:
        conn.close()
    assert "no migration plan yet" in derived["derived_waves"]["basis"], "derived waves say why they are not dated"
    assert ax.validate(derived) == []


def _drop_plan(db):
    with sqlite3.connect(str(db)) as c:
        c.execute("DELETE FROM migration_tasks")
        c.execute("DELETE FROM migration_plans")


# ── the person's review, the page, the twin, MCP ─────────────────────────────


def test_reviewing_the_capacity_is_a_persons_step_bound_to_its_digest(canvas, shop, tmp_path, monkeypatch):
    _planned(shop)
    url = "/migration-canvas/api/modernisation/apps/app-1/capacity/review"
    assert _client(canvas).post(url, json={"decision": "approve", "reason": "x", "digest": "0"}).status_code == 404, \
        "nothing declared: nothing to review"
    f = _capacity_file(tmp_path, monkeypatch, CAPACITY)
    waves = _client(canvas).get("/migration-canvas/api/modernisation/apps/app-1/waves").get_json()
    digest = waves["schedule"]["capacity"]["digest"]
    body = {"decision": "approve", "reason": "the PMO plan for FY27", "digest": digest}
    assert _client(canvas).post(url, json={**body, "reason": ""}).status_code == 400
    assert _client(canvas, COR).post(url, json=body).status_code == 403
    assert _client(canvas).post(url, json={**body, "digest": "0" * 64}).status_code == 409
    assert _client(canvas).post("/migration-canvas/api/modernisation/apps/nope/capacity/review",
                                json=body).status_code == 404
    resp = _client(canvas).post(url, json=body)
    assert resp.status_code == 200, resp.get_data(as_text=True)
    out = resp.get_json()
    assert out["review"] == {"state": "reviewed", "decision": "approve", "by": "u-pm", "digest": digest}
    assert out["decision_step"]["template_id"] == "decl-wft-mdz-capacity-review"
    assert _client(canvas).post(url, json=body).status_code == 409, "reviewed once at a digest"
    after = _client(canvas).get("/migration-canvas/api/modernisation/apps/app-1/waves").get_json()
    assert after["schedule"]["dated"] is True and all(w["starts"] and w["ends"] for w in after["waves"])
    f.write_text("applications: [1]\n", encoding="utf-8")
    assert _client(canvas).post(url, json=body).status_code == 409, "a refused file reviews nothing"


def test_the_page_shows_estimates_dates_why_and_the_capacity_review():
    text = (ROOT / PAGE).read_text(encoding="utf-8")
    for needle in ("mdz-schedule-reason", "mdz-capacity", "data-approve-capacity", "data-reject-capacity",
                   "migration_canvas.api_modernisation_capacity_review", "mdz-wave-hours", "mdz-wave-starts",
                   "mdz-wave-ends", "not dated", "args/modernisation_capacity.yaml", "decl-wft-mdz-capacity-review"):
        assert needle in text, needle
    assert "/api/" not in text, "routes by endpoint id"
    assert (ROOT / "icdev" / PAGE).read_text(encoding="utf-8") == text, "not mirrored to icdev/"


def test_the_twin_flags_an_unreviewed_capacity_and_undated_waves():
    from tools.twin_core.adapters.modernisation import ModernisationGovernanceTwin

    def bundle(app, schedule):
        return {"app": app, "name": app.title(), "valid": True, "error": None, "features": 1, "processes": [],
                "missing": {}, "schedule": schedule}

    ev = _bundle_evidence(bundles=[
        bundle("app-1", {"planned_waves": 3, "dated": False, "reason": "the declared capacity is not reviewed",
                         "capacity": {"application": "Shop", "digest": "a" * 64, "review": "unreviewed"}}),
        bundle("app-2", {"planned_waves": 2, "dated": False, "reason": "no team capacity is declared",
                         "capacity": None}),
        bundle("app-3", {"planned_waves": 2, "dated": True, "reason": "dated from 40 h/week",
                         "capacity": {"application": "Depot", "digest": "b" * 64, "review": "reviewed"}}),
        bundle("app-4", {"planned_waves": 0, "dated": False, "reason": "no migration plan yet", "capacity": None})])
    out = ModernisationGovernanceTwin().judge(ev)
    found = {(f["rule_id"], f["thread_id"]) for f in out["findings"] if f["rule_id"].startswith("mdz.waves.")}
    assert found == {("mdz.waves.capacity_unreviewed", "legacy_app:app-1"), ("mdz.waves.undated", "legacy_app:app-1"),
                     ("mdz.waves.undated", "legacy_app:app-2")}, "dated, or not planned yet: nothing to say"
    undated = next(f for f in out["findings"] if f["thread_id"] == "legacy_app:app-2")
    assert "no team capacity is declared" in undated["detail"]
    assert out["summary"]["plans_with_undated_waves"] == 2


def test_the_twin_observes_undated_waves_with_a_thread_and_replays(canvas, shop):
    from tools.twin_core import governance

    _planned(shop)
    adapter = governance.adapter_for("mdc")
    env = governance.observe(adapter)
    threads = {(v["rule_id"], v["thread_id"]) for v in env["violations"]}
    assert ("mdz.waves.undated", "legacy_app:app-1") in threads
    replayed = governance.replay(adapter, env["extra"]["observation_id"])
    assert replayed["match"] is True, replayed["reason"]


def test_the_mcp_wave_schedule_tool_is_read_only_and_says_why(shop):
    from tools.mcp import gap_handlers
    from tools.mcp.tool_registry import READ_ONLY_DECLARATIONS, list_tools, tool_authorization

    assert "modernisation_wave_schedule" in set(list_tools())
    assert READ_ONLY_DECLARATIONS["modernisation_wave_schedule"] is True
    assert tool_authorization("modernisation_wave_schedule")["tier"] == "read"
    assert gap_handlers.handle_modernisation_wave_schedule({}) == {"error": "app_id is required"}
    _planned(shop)
    out = gap_handlers.handle_modernisation_wave_schedule({"app_id": "app-1"})
    assert out["schedule"]["dated"] is False and "no team capacity" in out["schedule"]["reason"]
    assert all(w["estimated_hours"] > 0 and "starts" not in w and w["undated"] for w in out["waves"])
    assert json.dumps(out)
