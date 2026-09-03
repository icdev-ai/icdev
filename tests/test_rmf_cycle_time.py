# CUI // SP-CTI
"""rmf-cyc-01 — the two clocks, and every way they are forbidden to merge.

The database under test is built by RUNNING migration 20260902233931's own
``up.py``, never by transcribing its DDL here. A hand-copied schema can only
ever agree with itself: it would pass while the shipped migration added the
wrong columns, or none.
"""
from __future__ import annotations

import importlib.util
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
MIGRATION_DIR = (
    REPO_ROOT / "tools" / "db" / "migrations" / "20260902233931_rmf_workflow_stage_clocks"
)

BASE = datetime(2026, 9, 1, 8, 0, 0, tzinfo=timezone.utc)


def _iso(hours: float) -> str:
    return (BASE + timedelta(hours=hours)).isoformat()


def _load_migration(name: str):
    spec = importlib.util.spec_from_file_location(
        f"rmf_cyc_migration_{name}", str(MIGRATION_DIR / f"{name}.py")
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture()
def db(tmp_path, monkeypatch):
    """A real SQLite database whose rmf_workflow_stages came from the migration.

    A bare get_connection() here would poison the ambient checkout database, so
    the path is explicit and lives under tmp_path.
    """
    monkeypatch.setenv("ICDEV_STORAGE_BACKEND", "sqlite")
    from tools.db.storage import get_connection

    db_path = tmp_path / "rmf.db"
    # Touch the file through sqlite3 so the storage layer opens an existing db.
    sqlite3.connect(db_path).close()

    conn = get_connection(db_path=str(db_path))
    _load_migration("up").up(conn)
    conn.close()
    return str(db_path)


def _conn(db_path):
    from tools.db.storage import get_connection

    return get_connection(db_path=db_path)


# ---------------------------------------------------------------------------
# The migration
# ---------------------------------------------------------------------------

def test_migration_adds_all_four_clock_columns(db):
    conn = _conn(db)
    try:
        cols = {dict(r)["name"] for r in conn.execute("PRAGMA table_info(rmf_workflow_stages)").fetchall()}
    finally:
        conn.close()
    assert {"started_at", "actor", "evidence_ref", "submitted_at"} <= cols


def test_migration_is_idempotent(db):
    """Re-running up() over the widened table must not raise or duplicate."""
    conn = _conn(db)
    try:
        _load_migration("up").up(conn)
        _load_migration("up").up(conn)
        cols = [dict(r)["name"] for r in conn.execute("PRAGMA table_info(rmf_workflow_stages)").fetchall()]
    finally:
        conn.close()
    assert cols.count("started_at") == 1


def test_migration_widens_a_pre_existing_narrow_table(tmp_path, monkeypatch):
    """The live PostgreSQL population: table present, four columns absent.

    Creating the table wholesale would have been a silent no-op there, leaving a
    recorder that writes columns the database does not have.
    """
    monkeypatch.setenv("ICDEV_STORAGE_BACKEND", "sqlite")
    from tools.db.storage import get_connection

    db_path = tmp_path / "narrow.db"
    raw = sqlite3.connect(db_path)
    raw.execute(
        """CREATE TABLE rmf_workflow_stages (
               id INTEGER PRIMARY KEY AUTOINCREMENT,
               project_id TEXT NOT NULL,
               stage TEXT NOT NULL,
               status TEXT NOT NULL DEFAULT 'not_started',
               assigned_to TEXT, completed_at TEXT, notes TEXT,
               created_at TEXT, updated_at TEXT, classification VARCHAR(50),
               UNIQUE(project_id, stage))"""
    )
    raw.execute(
        "INSERT INTO rmf_workflow_stages (project_id, stage, status) VALUES ('p1','assess','complete')"
    )
    raw.commit()
    raw.close()

    conn = get_connection(db_path=str(db_path))
    try:
        _load_migration("up").up(conn)
        cols = {dict(r)["name"] for r in conn.execute("PRAGMA table_info(rmf_workflow_stages)").fetchall()}
        survivors = conn.execute("SELECT COUNT(*) AS c FROM rmf_workflow_stages").fetchone()
    finally:
        conn.close()
    assert {"started_at", "actor", "evidence_ref", "submitted_at"} <= cols
    # The pre-existing row must survive the widening.
    assert dict(survivors)["c"] == 1


# ---------------------------------------------------------------------------
# The recorder
# ---------------------------------------------------------------------------

def test_record_artifact_stamps_started_at_and_maps_the_stage(db):
    from tools.compliance import rmf_stage_recorder as rec

    result = rec.record_artifact(
        "p1", "ssp", actor="ssp_generator", evidence="ssp_documents:p1@v1.0",
        db_path=db, now=_iso(0),
    )
    assert result["recorded"] is True
    assert result["stage"] == "select"

    conn = _conn(db)
    try:
        row = dict(conn.execute("SELECT * FROM rmf_workflow_stages").fetchone())
    finally:
        conn.close()
    assert row["started_at"] == _iso(0)
    assert row["actor"] == "ssp_generator"
    assert row["evidence_ref"] == "ssp_documents:p1@v1.0"
    assert row["status"] == "in_progress"


def test_regenerating_an_artifact_never_moves_started_at(db):
    """The clock started when the FIRST artifact appeared, not the latest one."""
    from tools.compliance import rmf_stage_recorder as rec

    rec.record_artifact("p1", "ssp", actor="ssp_generator", evidence="a", db_path=db, now=_iso(0))
    rec.record_artifact("p1", "ssp", actor="ssp_generator", evidence="b", db_path=db, now=_iso(30))

    conn = _conn(db)
    try:
        row = dict(conn.execute("SELECT * FROM rmf_workflow_stages").fetchone())
    finally:
        conn.close()
    assert row["started_at"] == _iso(0)
    assert row["updated_at"] == _iso(30)
    assert row["evidence_ref"] == "b"


def test_an_unknown_artifact_kind_is_refused_not_guessed(db):
    from tools.compliance import rmf_stage_recorder as rec

    result = rec.record_artifact("p1", "nonsense", actor="ssp_generator", evidence="x", db_path=db)
    assert result["recorded"] is False
    assert result["reason"] == "unknown_artifact_kind"


def test_schema_not_ready_is_refused_and_reported(tmp_path, monkeypatch):
    """A database predating the migration must not record a clockless stage."""
    monkeypatch.setenv("ICDEV_STORAGE_BACKEND", "sqlite")
    db_path = tmp_path / "narrow.db"
    raw = sqlite3.connect(db_path)
    raw.execute(
        "CREATE TABLE rmf_workflow_stages (id INTEGER PRIMARY KEY, project_id TEXT, "
        "stage TEXT, status TEXT, completed_at TEXT)"
    )
    raw.commit()
    raw.close()

    from tools.compliance import rmf_stage_recorder as rec

    result = rec.record_artifact(
        "p1", "ssp", actor="ssp_generator", evidence="x", db_path=str(db_path)
    )
    assert result["recorded"] is False
    assert result["reason"] == "schema_not_ready"


def test_actor_kind_has_three_values_not_two():
    """An unrecognised actor is neither automated nor manual."""
    from tools.compliance.rmf_stage_recorder import actor_kind

    assert actor_kind("ssp_generator") == "automated"
    assert actor_kind("human:ao-office") == "human"
    assert actor_kind("some_script") == "unknown"
    assert actor_kind(None) == "unknown"


def test_resubmission_clears_the_recorded_decision(db):
    """A returned-and-resubmitted package has a NEW decision pending.

    Carrying the first submission forward would fold our own rework into the
    Authorizing Official's number.
    """
    from tools.compliance import rmf_stage_recorder as rec

    rec.record_submission("p1", actor="human:pm", evidence="pkg:1", db_path=db, now=_iso(10))
    rec.record_decision("p1", "denied", actor="human:ao", db_path=db, now=_iso(60))
    rec.record_submission("p1", actor="human:pm", evidence="pkg:2", db_path=db, now=_iso(100))

    conn = _conn(db)
    try:
        row = dict(
            conn.execute(
                "SELECT * FROM rmf_workflow_stages WHERE stage = 'authorize'"
            ).fetchone()
        )
    finally:
        conn.close()
    assert row["submitted_at"] == _iso(100)
    assert row["completed_at"] is None


def test_a_denied_decision_is_blocked_not_complete(db):
    from tools.compliance import rmf_stage_recorder as rec

    rec.record_submission("p1", actor="human:pm", evidence="pkg:1", db_path=db, now=_iso(0))
    rec.record_decision("p1", "denied", actor="human:ao", db_path=db, now=_iso(24))

    conn = _conn(db)
    try:
        row = dict(conn.execute("SELECT * FROM rmf_workflow_stages").fetchone())
    finally:
        conn.close()
    assert row["status"] == "blocked"


# ---------------------------------------------------------------------------
# The report
# ---------------------------------------------------------------------------

def _seed_measured_package(db, project="p1", *, submit_at=48.0, decide_at=300.0):
    from tools.compliance import rmf_stage_recorder as rec

    rec.record_artifact(project, "ssp", actor="ssp_generator", evidence="a", db_path=db, now=_iso(0))
    rec.record_artifact(project, "stig_assessment", actor="stig_checker", evidence="b", db_path=db, now=_iso(20))
    rec.record_submission(project, actor="human:pm", evidence="pkg", db_path=db, now=_iso(submit_at))
    if decide_at is not None:
        rec.record_decision(project, "authorized", actor="human:ao", db_path=db, now=_iso(decide_at))


def test_empty_board_is_never_recorded_never_zero(db):
    from tools.compliance.rmf_cycle_time import collect_report

    report = collect_report(db_path=db)
    assert report["state"] == "never_recorded"
    # None, not a zero-valued section. A 0.0 here reads as "instant", which is
    # the most flattering possible reading of a clock nobody ever started.
    assert report["automation_time"] is None
    assert report["decision_latency"] is None
    assert report["baseline_source"] is None
    assert "not a clean bill of health" in report["note"].lower()


def test_absent_substrate_is_not_a_clean_bill_of_health(tmp_path, monkeypatch):
    monkeypatch.setenv("ICDEV_STORAGE_BACKEND", "sqlite")
    db_path = tmp_path / "bare.db"
    sqlite3.connect(db_path).close()

    from tools.compliance.rmf_cycle_time import collect_report

    report = collect_report(db_path=str(db_path))
    assert report["state"] == "substrate_absent"
    assert report["automation_time"] is None
    assert report["decision_latency"] is None


def test_both_clocks_measured_with_their_own_denominators(db):
    from tools.compliance.rmf_cycle_time import collect_report

    _seed_measured_package(db)
    report = collect_report(db_path=db)

    assert report["state"] == "measured"
    auto = report["automation_time"]
    dec = report["decision_latency"]

    # automation_time: first artifact (h0) -> submission (h48).
    assert auto["count"] == 1
    assert auto["median_hours"] == pytest.approx(48.0)
    assert auto["end_basis_counts"]["submitted"] == 1
    # decision_latency: submission (h48) -> decision (h300).
    assert dec["count"] == 1
    assert dec["median_hours"] == pytest.approx(252.0)


def test_no_blended_figure_is_emitted_anywhere(db):
    """The load-bearing assertion.

    A structural check on key names would pass a payload that spelled the blend
    under an innocent name, so this walks EVERY numeric value in the report and
    asserts none of them equals automation + decision. The fixture is chosen so
    that sum (300.0) collides with no legitimate quantity in the payload.
    """
    from tools.compliance.rmf_cycle_time import collect_report

    _seed_measured_package(db, submit_at=48.0, decide_at=300.0)
    report = collect_report(db_path=db)

    auto = report["automation_time"]["median_hours"]
    dec = report["decision_latency"]["median_hours"]
    assert auto and dec
    blended = auto + dec

    found: list[str] = []

    def walk(node, path="report"):
        if isinstance(node, dict):
            for key, value in node.items():
                walk(value, f"{path}.{key}")
        elif isinstance(node, list):
            for i, value in enumerate(node):
                walk(value, f"{path}[{i}]")
        elif isinstance(node, (int, float)) and not isinstance(node, bool):
            if abs(float(node) - blended) < 1e-6:
                found.append(path)

    walk(report)
    assert not found, f"a blended automation+decision figure is emitted at: {found}"


def test_an_unsubmitted_package_reports_its_end_basis_as_a_lower_bound(db):
    from tools.compliance import rmf_stage_recorder as rec
    from tools.compliance.rmf_cycle_time import collect_report

    rec.record_artifact("p1", "ssp", actor="ssp_generator", evidence="a", db_path=db, now=_iso(0))
    rec.record_artifact("p1", "poam", actor="poam_generator", evidence="b", db_path=db, now=_iso(12))

    report = collect_report(db_path=db)
    auto = report["automation_time"]
    assert auto["end_basis_counts"]["last_artifact"] == 1
    assert auto["end_basis_counts"]["submitted"] == 0
    assert auto["is_lower_bound"] is True
    # A floor can only grow, so it can never establish "under the target".
    assert auto["submitted_only"]["count"] == 0
    assert auto["meets_target"] is None
    # The AO clock has no denominator at all here — and says so rather than 0.
    assert report["decision_latency"]["median_hours"] is None
    assert report["decision_latency"]["unmeasurable"]["no_authorize_stage"] == 1
    assert report["state"] == "partial"


def test_a_decision_with_no_recorded_submission_is_unmeasurable_not_instant(db):
    from tools.compliance import rmf_stage_recorder as rec
    from tools.compliance.rmf_cycle_time import collect_report

    rec.record_artifact("p1", "ssp", actor="ssp_generator", evidence="a", db_path=db, now=_iso(0))
    rec.record_decision("p1", "authorized", actor="human:ao", db_path=db, now=_iso(200))

    report = collect_report(db_path=db)
    dec = report["decision_latency"]
    assert dec["count"] == 0
    assert dec["median_hours"] is None
    assert dec["unmeasurable"]["decided_without_submission"] == 1


def test_awaiting_a_decision_is_its_own_state(db):
    from tools.compliance.rmf_cycle_time import collect_report

    _seed_measured_package(db, decide_at=None)
    report = collect_report(db_path=db)
    assert report["decision_latency"]["awaiting_decision"] == 1
    assert report["decision_latency"]["median_hours"] is None


def test_meets_target_is_none_when_unmeasured_never_false(db):
    """"missed the target" and "was never measured" are opposite findings."""
    from tools.compliance import rmf_stage_recorder as rec
    from tools.compliance.rmf_cycle_time import collect_report

    # A package that genuinely blew the 72h target reports False.
    _seed_measured_package(db, project="slow", submit_at=1000.0, decide_at=1100.0)
    slow = collect_report(db_path=db, project_id="slow")
    assert slow["automation_time"]["median_hours"] == pytest.approx(1000.0)
    assert slow["automation_time"]["meets_target"] is False

    # A project whose only stage row carries an actor nothing recognises has NO
    # automation figure. It must read None — never False, which would report a
    # missed target for work nobody measured.
    rec.record_stage_event(
        "opaque", "assess", actor="some_cron_job", evidence="a", db_path=db, now=_iso(0)
    )
    opaque = collect_report(db_path=db, project_id="opaque")
    assert opaque["automation_time"]["median_hours"] is None
    assert opaque["automation_time"]["meets_target"] is None


def test_stages_without_a_producer_are_named_on_every_run(db):
    from tools.compliance.rmf_cycle_time import collect_report

    report = collect_report(db_path=db)
    assert "categorize" in report["stages_without_producer"]
    assert "authorize" in report["stages_without_producer"]
    assert "select" not in report["stages_without_producer"]


def test_a_negative_span_is_unmeasurable_not_a_small_number(db):
    """A decision dated before its own submission is a data defect, not 0.1h."""
    from tools.compliance import rmf_stage_recorder as rec
    from tools.compliance.rmf_cycle_time import collect_report

    rec.record_artifact("p1", "ssp", actor="ssp_generator", evidence="a", db_path=db, now=_iso(0))
    rec.record_submission("p1", actor="human:pm", evidence="pkg", db_path=db, now=_iso(100))
    rec.record_decision("p1", "authorized", actor="human:ao", db_path=db, now=_iso(50))

    report = collect_report(db_path=db)
    assert report["decision_latency"]["median_hours"] is None
    assert report["decision_latency"]["unmeasurable"]["negative_span"] == 1


def test_an_unknown_actor_is_not_counted_as_automation(db):
    from tools.compliance import rmf_stage_recorder as rec
    from tools.compliance.rmf_cycle_time import collect_report

    rec.record_stage_event(
        "p1", "assess", actor="some_cron_job", evidence="a", db_path=db, now=_iso(0)
    )
    report = collect_report(db_path=db)
    assert report["automation_time"]["count"] == 0
    assert report["automation_time"]["unmeasurable"]["unknown_actor"] == 1


# ---------------------------------------------------------------------------
# The baseline
# ---------------------------------------------------------------------------

def test_shipped_baseline_refuses_the_comparison_and_says_why(db):
    """"months -> 72h" has no denominator yet, and the report must say so."""
    from tools.compliance.rmf_cycle_time import collect_report

    _seed_measured_package(db)
    baseline = collect_report(db_path=db)["baseline_source"]

    assert baseline["comparison"] is None
    refused = baseline["comparison_refused"]
    assert "baseline_unquantified" in refused
    assert "baseline_includes_decision_latency" in refused
    assert baseline["declared"]["kind"] == "claimed"
    assert baseline["declared"]["verified"] is False


def test_a_baseline_that_includes_the_ao_queue_is_refused_even_when_quantified(db, tmp_path):
    """The refusal that IS the card.

    A wall-clock ATO duration contains the AO's queue; automation_time does not.
    Dividing one by the other is the blend, wearing a percentage.
    """
    import yaml

    from tools.compliance.rmf_cycle_time import collect_report

    cfg = tmp_path / "baseline.yaml"
    cfg.write_text(
        yaml.safe_dump(
            {
                "target": {"automation_hours": 72, "kind": "claimed", "verified": False},
                "baseline": {
                    "id": "anecdote",
                    "kind": "claimed",
                    "value_hours": 4380,
                    "includes_decision_latency": True,
                    "verified": False,
                },
                "comparison": {
                    "require_quantified_baseline": True,
                    "refuse_when_baseline_includes_decision_latency": True,
                },
                "measured_here": {"min_projects": 2},
            }
        ),
        encoding="utf-8",
    )

    _seed_measured_package(db)
    baseline = collect_report(db_path=db, config_path=cfg)["baseline_source"]
    assert baseline["comparison"] is None
    assert baseline["comparison_refused"] == ["baseline_includes_decision_latency"]


def test_a_compatible_quantified_baseline_does_produce_a_comparison(db, tmp_path):
    """The rails are refusals, not a refusal to ever answer."""
    import yaml

    from tools.compliance.rmf_cycle_time import collect_report

    cfg = tmp_path / "baseline.yaml"
    cfg.write_text(
        yaml.safe_dump(
            {
                "target": {"automation_hours": 72},
                "baseline": {
                    "id": "measured_manual_assembly",
                    "kind": "cited_external",
                    "value_hours": 480,
                    "includes_decision_latency": False,
                    "verified": True,
                },
                "comparison": {
                    "require_quantified_baseline": True,
                    "refuse_when_baseline_includes_decision_latency": True,
                },
                "measured_here": {"min_projects": 2},
            }
        ),
        encoding="utf-8",
    )

    _seed_measured_package(db)
    baseline = collect_report(db_path=db, config_path=cfg)["baseline_source"]
    assert baseline["comparison"] is not None
    assert baseline["comparison"]["reduction_factor"] == pytest.approx(10.0)


def test_measured_here_baseline_withholds_its_median_below_the_floor(db):
    """One project is an anecdote wearing a statistic's name."""
    from tools.compliance import rmf_stage_recorder as rec
    from tools.compliance.rmf_cycle_time import collect_report

    rec.record_stage_event("p9", "assess", actor="human:analyst", evidence="a", db_path=db, now=_iso(0))
    rec.record_stage_event("p9", "select", actor="human:analyst", evidence="b", db_path=db, now=_iso(500))

    measured = collect_report(db_path=db)["baseline_source"]["measured_here"]
    assert measured["count"] == 1
    assert measured["median_hours"] is None
    assert measured["state"] == "below_min_projects"


def test_measured_here_is_a_separate_derivation_from_the_declared_baseline(db):
    from tools.compliance.rmf_cycle_time import collect_report

    _seed_measured_package(db)
    baseline = collect_report(db_path=db)["baseline_source"]
    assert set(baseline) >= {"declared", "measured_here", "comparison", "comparison_refused"}
    # They are never folded into one number.
    assert baseline["declared"]["value_hours"] is None
    assert baseline["measured_here"]["state"] in ("no_manual_history", "below_min_projects", "measured")


def test_unreadable_baseline_config_is_reported_not_defaulted(db, tmp_path):
    from tools.compliance.rmf_cycle_time import collect_report

    _seed_measured_package(db)
    missing = tmp_path / "nope.yaml"
    baseline = collect_report(db_path=db, config_path=missing)["baseline_source"]
    assert baseline["declared"]["config_unreadable"]
    assert baseline["comparison"] is None


def test_a_package_assembled_in_one_second_cannot_claim_the_72h_target(db):
    """The defect this rule exists for, found by running the real generators.

    Two artifacts produced sixteen milliseconds apart give a lower bound of 0.0
    hours. Scored naively against a 72-hour target that returns True — a perfect
    result for a package nobody has finished assembling, which is the
    empty-denominator defect wearing a duration.
    """
    from tools.compliance import rmf_stage_recorder as rec
    from tools.compliance.rmf_cycle_time import collect_report

    rec.record_artifact("fast", "ssp", actor="ssp_generator", evidence="a", db_path=db, now=_iso(0))
    rec.record_artifact("fast", "poam", actor="poam_generator", evidence="b", db_path=db, now=_iso(0))

    auto = collect_report(db_path=db, project_id="fast")["automation_time"]
    assert auto["median_hours"] == 0.0          # the floor is real and is reported
    assert auto["is_lower_bound"] is True
    assert auto["meets_target"] is None         # and it establishes nothing


def test_a_submitted_package_under_the_target_does_report_met(db):
    """The rail is a refusal to claim, not a refusal to ever answer."""
    from tools.compliance.rmf_cycle_time import collect_report

    _seed_measured_package(db, project="quick", submit_at=40.0, decide_at=None)
    auto = collect_report(db_path=db, project_id="quick")["automation_time"]
    assert auto["submitted_only"]["count"] == 1
    assert auto["meets_target"] is True
