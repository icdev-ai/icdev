# CUI // SP-CTI
"""The detector-consuming reflex (autonomy-act-02).

Three detectors were imported by nobody on any runtime path. This is the
consumer: it runs them, projects each finding into ``detector_findings`` ONCE
(``seen_count`` on recurrence), seeds one evidence-carrying card per NEW
finding through ``task_factory.create_tasks``, and clears a finding only on a
MEASURABLE run that no longer reports it.

Hermetic: a per-test SQLite database from ``icdev_db``, injected detector
runners, and ``create_tasks`` patched on the task_factory module — the same
seam ``stranded_audit`` is tested through.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pytest

from tools.db.storage import get_connection
from tools.kanban import detector_findings as df
from tools.kanban import task_factory as tf

# --------------------------------------------------------------------------
# fixtures: the detectors' REAL output shapes, captured on the live board
# --------------------------------------------------------------------------
CHURN_REPORT = {
    "measurable": True, "window_hours": 24, "min_returns": 10,
    "transitions_scanned": 591, "tasks_with_any_return": 3,
    "oscillating": 2, "contested": 1,
    "tasks": [
        {"task_id": "cef-ui-03", "returns": 95, "cycle": "done -> backlog -> done",
         "actors": ["pr_watcher", "scheduler"], "contested": True,
         "first_seen": "2026-08-19T01:00:00+00:00", "last_seen": "2026-08-19T06:30:00+00:00"},
        {"task_id": "prop-vv-02", "returns": 12,
         "cycle": "in_progress -> token_exhausted -> in_progress",
         "actors": ["scheduler"], "contested": False,
         "first_seen": "2026-08-19T02:00:00+00:00", "last_seen": "2026-08-19T05:00:00+00:00"},
    ],
}

BORN_RED_REPORT = {
    "ran": True, "state": "findings", "backlog_total": 1701, "observed": 247,
    "born_red_count": 2, "broke_after_birth_count": 1,
    "findings": [
        {"path": "tests/govcon/test_past_performance_suggester.py", "state": "born_red",
         "detail": "1 failed, 8 errors in 0.84s", "observed_red_days": 1.2,
         "landed_at": "2026-07-25T02:01:38-04:00", "landed_commit": "82954f6",
         "file_age_days": 27.0, "red_days": 27.0, "red_days_basis": "file_age_upper_bound"},
        {"path": "tests/dashboard/test_home_tile_gating.py", "state": "born_red",
         "detail": "1 failed, 6 passed in 0.69s", "observed_red_days": 3.0,
         "landed_at": "2026-08-02T09:11:44-04:00", "landed_commit": "faafbd6",
         "file_age_days": 18.7, "red_days": 18.7, "red_days_basis": "file_age_upper_bound"},
        {"path": "tests/airgap/test_hook_compat_git_blocklist.py", "state": "broke_after_birth",
         "detail": "1 failed, 19 passed", "red_days": 3.5, "red_days_basis": "refuted_at_birth"},
    ],
}

RECOVERY_ENTRIES = [
    {"task_id": "qa-fail-e2e-baseurl-01", "attempts": 5, "kind": "resume",
     "reason": "CI failed: test", "at": datetime(2026, 8, 20, 10, tzinfo=timezone.utc),
     "escalated": True, "merged": False, "outcome": "needed_a_human"},
    {"task_id": "cef-ci-01", "attempts": 1, "kind": "resume", "reason": "CI failed",
     "at": "2026-08-20T09:00:00+00:00", "escalated": False, "merged": True,
     "outcome": "recovered"},
    {"task_id": "cef-ui-01", "attempts": 1, "kind": "resume", "reason": "",
     "at": "2026-08-20T08:00:00+00:00", "escalated": False, "merged": False,
     "outcome": "unresolved"},
]


def _finding(detector, subject, fingerprint="fp", priority="medium"):
    return df.Finding(detector, subject, fingerprint, title=f"{subject} finding",
                      priority=priority, task_type="fix",
                      evidence={"subject": subject, "n": 1},
                      derivation=f"python -m tools.x --subject {subject}",
                      advice="do the thing")


def _runners(**per_detector):
    """Injected runners: each value is a (state, [Finding]) or a callable."""
    def _mk(value):
        if callable(value):
            return value
        state, findings = value
        return lambda conn, cfg: df._result(state, findings)
    return {name: _mk(v) for name, v in per_detector.items()}


@pytest.fixture
def conn(icdev_db):
    c = get_connection(str(icdev_db))
    yield c
    c.close()


@pytest.fixture
def seeded(monkeypatch):
    """Capture what reaches the canonical seeder; return the ids it 'inserted'."""
    calls: list = []

    def _fake_create_tasks(specs, **_kw):
        calls.append([dict(s) for s in specs])
        return [s["id"] for s in specs]

    monkeypatch.setattr(tf, "create_tasks", _fake_create_tasks)
    return calls


def _rows(conn, sql, *params):
    return [dict(r) for r in conn.execute(sql, params).fetchall()]


# --------------------------------------------------------------------------
# adapters: detector output -> findings (pure)
# --------------------------------------------------------------------------
def test_churn_findings_carry_the_row_and_separate_contested():
    found = df.churn_findings(CHURN_REPORT)
    assert [f["subject"] for f in found] == ["cef-ui-03", "prop-vv-02"]
    contested, single = found
    assert contested["priority"] == "high" and single["priority"] == "medium"
    assert "CONTESTED" in contested["advice"] and "RETRY LOOP" in single["advice"]
    assert contested["evidence"]["returns"] == 95
    assert "--window-hours 24 --min-returns 10" in contested["derivation"]
    # the same task on a DIFFERENT cycle is a different finding
    other = df.Finding(df.DETECTOR_STATUS_CHURN, "cef-ui-03", "scheduled -> backlog -> scheduled|contested",
                       title="", priority="high", task_type="fix", evidence={}, derivation="", advice="")
    assert other["finding_id"] != contested["finding_id"]


def test_born_red_findings_skip_the_drift_reflexs_half():
    found = df.born_red_findings(BORN_RED_REPORT)
    assert sorted(f["subject"] for f in found) == [
        "tests/dashboard/test_home_tile_gating.py",
        "tests/govcon/test_past_performance_suggester.py",
    ]
    f = next(x for x in found if "govcon" in x["subject"])
    assert f["evidence"]["red_days_basis"] == "file_age_upper_bound"
    assert "upper bound" in f["advice"] and "--confirm 1" in f["advice"]
    assert f["task_type"] == "fix"


def test_recovery_findings_are_only_the_watchers_own_verdict():
    found = df.recovery_findings(RECOVERY_ENTRIES, window_hours=24)
    assert [f["subject"] for f in found] == ["qa-fail-e2e-baseurl-01"]
    f = found[0]
    assert f["priority"] == "high" and f["task_type"] == "chore"
    # datetimes from a PG row are serialised, not left to break json.dumps later
    assert isinstance(f["evidence"]["at"], str)
    json.dumps(f["evidence"])
    assert "qa-fail-e2e-baseurl-01" in f["derivation"]


def test_card_ids_are_opaque_machine_ids_never_card_shaped():
    from tools.kanban.task_identity import SHAPE_OPAQUE, classify_shape

    for i in range(200):
        fid = df.finding_ident("d", f"subject-{i}", "fp")
        assert classify_shape(df.card_id_for(fid)) == SHAPE_OPAQUE
        assert classify_shape(df.card_id_for(fid, 2)) == SHAPE_OPAQUE
    # a digest window that is all digits is walked past, so `-<10 digits>` can
    # never be parsed as a card's <N> and invent a project card
    assert not df.opaque_token("1234567890abcdef").isdigit()
    assert not df.opaque_token("1234567890123456").isdigit()


# --------------------------------------------------------------------------
# the run: projection, dedupe, seeding
# --------------------------------------------------------------------------
def test_first_sight_seeds_one_card_carrying_its_evidence(conn, seeded):
    f = _finding(df.DETECTOR_STATUS_CHURN, "cef-ui-03", priority="high")
    report = df.consume({}, conn=conn, runners=_runners(status_churn=("findings", [f])))

    assert report["state"] == "ok"
    assert report["findings_seen"] == 1 and report["findings_new"] == 1
    assert len(seeded) == 1 and len(seeded[0]) == 1
    spec = seeded[0][0]
    assert spec["id"] == df.card_id_for(f["finding_id"])
    assert spec["status"] == "suggested"
    assert spec["dispatch_source"] == "detector_findings_reflex"
    assert spec["idempotency_key"] == f"detector-finding-{f['finding_id']}-r1"
    # the card carries the derivation, the verbatim evidence and the finding id
    assert f["derivation"] in spec["description"]
    assert '"subject": "cef-ui-03"' in spec["description"]
    assert f["finding_id"] in spec["description"]
    assert "Do NOT" in spec["description"]
    assert spec["acceptance_criteria"]

    row = _rows(conn, f"SELECT * FROM {df.FINDINGS_TABLE}")[0]
    assert row["status"] == "active" and row["seen_count"] == 1
    assert row["task_id"] == spec["id"] and row["card_count"] == 1
    assert json.loads(row["evidence_json"])["subject"] == "cef-ui-03"
    run = _rows(conn, f"SELECT * FROM {df.RUNS_TABLE} WHERE detector = ?", "status_churn")[0]
    assert run["runs"] == 1 and run["measurable_runs"] == 1
    assert run["last_state"] == "findings" and run["last_findings"] == 1


def test_second_sight_bumps_seen_count_and_seeds_nothing(conn, seeded):
    f = _finding(df.DETECTOR_STATUS_CHURN, "cef-ui-03")
    runners = _runners(status_churn=("findings", [f]))
    df.consume({}, conn=conn, runners=runners)
    second = df.consume({}, conn=conn, runners=runners)

    assert len(seeded) == 1, "the same finding seen twice is ONE card"
    assert second["findings_new"] == 0 and second["findings_recurring"] == 0
    assert second["cards_seeded"] == []
    row = _rows(conn, f"SELECT seen_count, card_count, status FROM {df.FINDINGS_TABLE}")[0]
    assert row == {"seen_count": 2, "card_count": 1, "status": "active"}


def test_unmeasurable_run_clears_nothing(conn, seeded):
    f = _finding(df.DETECTOR_STATUS_CHURN, "cef-ui-03")
    df.consume({}, conn=conn, runners=_runners(status_churn=("findings", [f])))

    def _idle(conn_, cfg):
        return df._result("unmeasurable", reason="no status transitions recorded in the last 24h")

    report = df.consume({}, conn=conn, runners={"status_churn": _idle})
    d = report["detectors"]["status_churn"]
    assert d["state"] == "unmeasurable" and d["findings"] is None and d["cleared"] == 0
    row = _rows(conn, f"SELECT status, cleared_at FROM {df.FINDINGS_TABLE}")[0]
    assert row["status"] == "active" and row["cleared_at"] is None
    run = _rows(conn, f"SELECT * FROM {df.RUNS_TABLE}")[0]
    assert run["runs"] == 2 and run["measurable_runs"] == 1
    assert run["last_state"] == "unmeasurable" and run["last_findings"] is None
    assert run["last_measurable_at"] is not None, "the last MEASURABLE time survives an idle run"


def test_runner_exception_is_isolated_and_recorded(conn, seeded):
    def _boom(conn_, cfg):
        raise RuntimeError("kaboom")

    f = _finding(df.DETECTOR_RECOVERY, "t-1")
    report = df.consume({}, conn=conn, runners={
        "born_red": _boom, **_runners(recovery=("findings", [f]))})
    assert report["state"] == "partial"
    assert report["detectors"]["born_red"]["state"] == "error"
    assert any("kaboom" in e for e in report["errors"])
    assert report["cards_seeded"] == [df.card_id_for(f["finding_id"])]
    run = _rows(conn, f"SELECT last_state, measurable_runs FROM {df.RUNS_TABLE} WHERE detector = ?",
                "born_red")[0]
    assert run == {"last_state": "error", "measurable_runs": 0}


def test_measurable_clean_run_clears_then_recurrence_gets_a_second_card(conn, seeded):
    f = _finding(df.DETECTOR_BORN_RED, "tests/x_test.py")
    df.consume({}, conn=conn, runners=_runners(born_red=("findings", [f])))
    cleared = df.consume({}, conn=conn, runners=_runners(born_red=("clean", [])))
    assert cleared["findings_cleared"] == 1
    row = _rows(conn, f"SELECT status, cleared_at FROM {df.FINDINGS_TABLE}")[0]
    assert row["status"] == "cleared" and row["cleared_at"]

    back = df.consume({}, conn=conn, runners=_runners(born_red=("findings", [f])))
    assert back["findings_recurring"] == 1 and back["findings_new"] == 0
    assert back["cards_seeded"] == [df.card_id_for(f["finding_id"], 2)]
    spec = seeded[-1][0]
    assert spec["id"].endswith("-r2") and "RECURRENCE" in spec["description"]
    assert spec["idempotency_key"].endswith("-r2")
    row = _rows(conn, f"SELECT status, seen_count, card_count, task_id FROM {df.FINDINGS_TABLE}")[0]
    assert row == {"status": "active", "seen_count": 2, "card_count": 2, "task_id": spec["id"]}


def test_finding_still_seen_after_its_card_closed_is_a_recurrence(conn, seeded):
    f = _finding(df.DETECTOR_RECOVERY, "t-9")
    runners = _runners(recovery=("findings", [f]))
    first = df.consume({}, conn=conn, runners=runners)
    card = first["cards_seeded"][0]
    conn.execute("INSERT INTO kanban_tasks (id, title, status) VALUES (?, ?, ?)",
                 (card, "closed card", "done"))
    conn.commit()

    again = df.consume({}, conn=conn, runners=runners)
    assert again["findings_recurring"] == 1
    assert again["cards_seeded"] == [df.card_id_for(f["finding_id"], 2)]
    # an OPEN card, by contrast, absorbs further observations
    conn.execute("INSERT INTO kanban_tasks (id, title, status) VALUES (?, ?, ?)",
                 (again["cards_seeded"][0], "open card", "suggested"))
    conn.commit()
    third = df.consume({}, conn=conn, runners=runners)
    assert third["cards_seeded"] == [] and third["findings_recurring"] == 0


def test_cap_defers_worst_last_and_says_so(conn, seeded):
    findings = [_finding(df.DETECTOR_STATUS_CHURN, f"t-{i}", priority="medium") for i in range(5)]
    findings.append(_finding(df.DETECTOR_STATUS_CHURN, "t-hot", priority="high"))
    runners = _runners(status_churn=("findings", findings))

    first = df.consume({"max_cards_per_run": 2}, conn=conn, runners=runners)
    assert len(first["cards_seeded"]) == 2 and first["cards_deferred"] == 4
    assert first["cards_seeded"][0] == df.card_id_for(findings[-1]["finding_id"]), "high first"
    # every finding is PROJECTED even when its card is deferred
    assert len(_rows(conn, f"SELECT 1 FROM {df.FINDINGS_TABLE}")) == 6
    assert len(_rows(conn, f"SELECT 1 FROM {df.FINDINGS_TABLE} WHERE task_id IS NULL")) == 4

    second = df.consume({"max_cards_per_run": 10}, conn=conn, runners=runners)
    assert len(second["cards_seeded"]) == 4 and second["cards_deferred"] == 0
    assert second["findings_new"] == 4 and second["findings_recurring"] == 0
    assert all(s["id"] == df.card_id_for(
        next(f for f in findings if df.card_id_for(f["finding_id"]) == s["id"])["finding_id"])
        for s in seeded[-1]), "a deferred finding's first card is -r1, not a recurrence"
    assert not _rows(conn, f"SELECT 1 FROM {df.FINDINGS_TABLE} WHERE task_id IS NULL")


def test_dry_run_writes_nothing(conn, seeded):
    f = _finding(df.DETECTOR_STATUS_CHURN, "cef-ui-03")
    report = df.consume({}, conn=conn, seed=False,
                        runners=_runners(status_churn=("findings", [f])))
    assert report["dry_run"] is True
    assert report["cards_planned"] == [df.card_id_for(f["finding_id"])]
    assert report["cards_seeded"] == [] and seeded == []
    assert not _rows(conn, f"SELECT 1 FROM {df.FINDINGS_TABLE}")
    assert not _rows(conn, f"SELECT 1 FROM {df.RUNS_TABLE}")


def test_unmigrated_database_reports_and_writes_nothing(conn, seeded):
    conn.execute(f"DROP TABLE {df.FINDINGS_TABLE}")
    conn.commit()
    report = df.consume({}, conn=conn, runners=_runners(
        status_churn=("findings", [_finding(df.DETECTOR_STATUS_CHURN, "x")])))
    assert report["state"] == "unmigrated"
    assert df.MIGRATION in report["errors"][0]
    assert seeded == [] and report["detectors"] == {}
    assert df.stats(conn)["state"] == "unmigrated"
    assert df.list_findings(conn) == []


def test_seed_failure_is_reported_and_leaves_the_finding_owed(conn, monkeypatch):
    def _refuse(specs, **_kw):
        raise ValueError("refusing to seed")

    monkeypatch.setattr(tf, "create_tasks", _refuse)
    f = _finding(df.DETECTOR_STATUS_CHURN, "cef-ui-03")
    report = df.consume({}, conn=conn, runners=_runners(status_churn=("findings", [f])))
    assert report["state"] == "partial" and any("refusing" in e for e in report["errors"])
    row = _rows(conn, f"SELECT task_id, card_count FROM {df.FINDINGS_TABLE}")[0]
    assert row == {"task_id": None, "card_count": 0}


def test_stats_and_list_keep_never_ran_apart_from_clean(conn, seeded):
    s = df.stats(conn)
    assert s["state"] == "never_ran"
    assert all(v["state"] == "never_ran" and v["active"] is None for v in s["detectors"].values())

    f = _finding(df.DETECTOR_RECOVERY, "t-1")
    df.consume({}, conn=conn, runners={
        **_runners(recovery=("findings", [f]), status_churn=("clean", [])),
        "born_red": lambda c, cfg: df._result("unmeasurable", reason="baseline absent"),
    })
    s = df.stats(conn)
    assert s["detectors"]["recovery"]["state"] == "findings"
    assert s["detectors"]["recovery"]["active"] == 1
    assert s["detectors"]["status_churn"]["state"] == "clean"
    assert s["detectors"]["status_churn"]["active"] == 0
    assert s["detectors"]["born_red"]["state"] == "unmeasurable"
    assert s["detectors"]["born_red"]["run"]["last_findings"] is None

    rows = df.list_findings(conn, detector=df.DETECTOR_RECOVERY, status="active")
    assert len(rows) == 1 and rows[0]["evidence"]["subject"] == "t-1"
    assert df.list_findings(conn, status="cleared") == []


# --------------------------------------------------------------------------
# the live seams
# --------------------------------------------------------------------------
def test_run_recovery_reads_the_panels_rows_and_is_unmeasurable_when_empty(conn):
    assert df.run_recovery(conn, {"window_hours": 24})["state"] == "unmeasurable"
    now = datetime.now(timezone.utc)
    rows = [
        ("pr_watcher.resume", {"task_id": "t-esc", "reason": "CI failed"}),
        ("pr_watcher.resume", {"task_id": "t-esc", "reason": "CI failed again"}),
        ("pr_watcher.escalate", {"task_id": "t-esc"}),
        ("pr_watcher.merge", {"task_id": "t-esc"}),       # the human's merge
        ("pr_watcher.resume", {"task_id": "t-ok", "reason": "conflict"}),
        ("pr_watcher.merge", {"task_id": "t-ok"}),
    ]
    for i, (action, d) in enumerate(rows):
        conn.execute(
            "INSERT INTO audit_trail (event_type, actor, action, details, created_at) "
            "VALUES (?, ?, ?, ?, ?)",
            ("pr_watcher", "pr_watcher", action, json.dumps(d),
             (now - timedelta(minutes=60 - i)).isoformat()))
    conn.commit()
    res = df.run_recovery(conn, {"window_hours": 24})
    assert res["state"] == "findings"
    assert [f["subject"] for f in res["findings"]] == ["t-esc"]
    assert res["summary"]["outcomes"] == {"needed_a_human": 1, "recovered": 1}


def test_run_status_churn_is_unmeasurable_on_an_idle_board(conn):
    res = df.run_status_churn(conn, {})
    assert res["state"] == "unmeasurable" and "no status transitions" in res["reason"]


def test_run_born_red_is_unmeasurable_without_the_baseline_table(conn):
    res = df.run_born_red(conn, {})
    assert res["state"] == "unmeasurable" and "ungated_test_baseline" in res["reason"]


# --------------------------------------------------------------------------
# the reflex is wired, both halves
# --------------------------------------------------------------------------
def test_reflex_is_registered_enabled_and_scheduled():
    import yaml

    from tools.daemon.base import parse_schedule
    from tools.genesis.daemon import REFLEX_NAMES
    from tools.genesis.reflexes import detector_findings_reflex as reflex

    assert "detector_findings_reflex" in REFLEX_NAMES
    cfg = yaml.safe_load(open("args/genesis_config.yaml", encoding="utf-8"))
    block = cfg["reflexes"]["detector_findings_reflex"]
    assert block["enabled"] is True and parse_schedule(block["schedule"])
    assert block["seed_status"] == "suggested"
    assert set(block["detectors"]) == set(df.DETECTORS)
    assert callable(reflex.run) and reflex.IMPLEMENTATION_STATUS == "full"


def test_reflex_reports_success_from_the_consumer(monkeypatch):
    from tools.genesis.reflexes import detector_findings_reflex as reflex
    from tools.kanban import detector_findings as lib

    monkeypatch.setattr(lib, "consume", lambda cfg: {
        "state": "ok", "findings_seen": 3, "errors": []})
    out = reflex.run({"max_cards_per_run": 1}, None)
    assert out["success"] is True and out["metric_value"] == 3.0

    monkeypatch.setattr(lib, "consume", lambda cfg: {
        "state": "unmigrated", "findings_seen": 0, "errors": ["tables absent"]})
    out = reflex.run({}, None)
    assert out["success"] is False and "tables absent" in out["error"]


def test_reflex_seeds_only_through_the_canonical_seeder():
    import ast
    import inspect

    src = inspect.getsource(df)
    tree = ast.parse(src)
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            assert "INSERT INTO KANBAN_TASKS" not in " ".join(node.value.upper().split())
    assert "from tools.kanban.task_factory import create_tasks" in src
