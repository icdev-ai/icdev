# CUI // SP-CTI
"""rmf-cyc-01 — the five producers, driven for real against a real database.

``tests/test_rmf_cycle_time.py`` exercises the recorder and the report directly.
This module asserts the thing that actually makes the table non-inert: that
running ``generate_ssp``, ``generate_poam``, ``run_stig_check``, an OSCAL
generator and ``collect_evidence`` LEAVES A ROW BEHIND, on the stage each
artifact is evidence of.

The database is built by ``tools/db/init_icdev_db.py``, not by a hand-written
fixture, because ``init_icdev_db.py`` is the population migration 20260902233931
deliberately does NOT reach — a fresh database gets its ``rmf_workflow_stages``
from there, and a fixture transcribed by hand could only ever agree with itself
while the shipped initializer carried the narrow shape.
"""
from __future__ import annotations

import shutil
import sys

import pytest


@pytest.fixture(scope="module")
def base_db(tmp_path_factory):
    """One initialized ICDEV database for the module; each test copies it."""
    import os

    os.environ["ICDEV_STORAGE_BACKEND"] = "sqlite"
    from tools.db.init_icdev_db import main as init_main

    db_path = tmp_path_factory.mktemp("rmfprod") / "base.db"
    argv = sys.argv
    sys.argv = ["init_icdev_db.py", "--db", str(db_path)]
    try:
        init_main()
    except SystemExit:
        pass
    finally:
        sys.argv = argv
    return db_path


@pytest.fixture()
def project(base_db, tmp_path, monkeypatch):
    """A copy of the initialized database holding one project."""
    monkeypatch.setenv("ICDEV_STORAGE_BACKEND", "sqlite")
    from tools.db.storage import get_connection

    db_path = tmp_path / "icdev.db"
    shutil.copy(base_db, db_path)
    out_dir = tmp_path / "proj"
    out_dir.mkdir()

    conn = get_connection(db_path=str(db_path))
    try:
        conn.execute(
            "INSERT INTO projects (id, name, type, classification, status, "
            "impact_level, directory_path) VALUES (%s, %s, %s, %s, %s, %s, %s)",
            ("prod-1", "Producer Test", "webapp", "CUI", "active", "IL5", str(out_dir)),
        )
        conn.commit()
    finally:
        conn.close()
    return str(db_path)


def _stages(db_path: str) -> dict[str, dict]:
    from tools.db.storage import get_connection

    conn = get_connection(db_path=db_path)
    try:
        rows = conn.execute("SELECT * FROM rmf_workflow_stages").fetchall()
    finally:
        conn.close()
    return {dict(r)["stage"]: dict(r) for r in rows}


# ---------------------------------------------------------------------------
# The fresh-database population
# ---------------------------------------------------------------------------

def test_a_fresh_database_carries_the_widened_stage_table(base_db):
    """init_icdev_db.py is the population the migration does not reach."""
    from tools.db.storage import get_connection

    conn = get_connection(db_path=str(base_db))
    try:
        cols = {
            dict(r)["name"]
            for r in conn.execute("PRAGMA table_info(rmf_workflow_stages)").fetchall()
        }
    finally:
        conn.close()
    assert {"started_at", "actor", "evidence_ref", "submitted_at"} <= cols


# ---------------------------------------------------------------------------
# The five producers
# ---------------------------------------------------------------------------

def test_ssp_generator_starts_the_select_stage(project):
    from tools.compliance.ssp_generator import generate_ssp

    generate_ssp("prod-1", db_path=project)

    stage = _stages(project)["select"]
    assert stage["actor"] == "ssp_generator"
    assert stage["status"] == "in_progress"
    assert stage["started_at"]
    # The pointer must reach the persisted record, not merely say "an SSP".
    assert stage["evidence_ref"].startswith("ssp_documents:prod-1@v")


def test_poam_generator_starts_the_assess_stage(project):
    from tools.compliance.poam_generator import generate_poam

    generate_poam("prod-1", db_path=project)

    stage = _stages(project)["assess"]
    assert stage["actor"] == "poam_generator"
    assert stage["evidence_ref"].startswith("file:")


def test_stig_checker_starts_the_assess_stage(project):
    from tools.compliance.stig_checker import run_stig_check

    run_stig_check("prod-1", stig_id="webapp", db_path=project)

    stage = _stages(project)["assess"]
    assert stage["actor"] == "stig_checker"
    assert stage["started_at"]


def test_cato_evidence_collection_starts_the_monitor_stage(project):
    from tools.compliance.cato_monitor import collect_evidence

    result = collect_evidence(
        "prod-1", "AC-2", "scan_result", "bandit_sast",
        automation_frequency="daily", db_path=project,
    )

    stage = _stages(project)["monitor"]
    assert stage["actor"] == "cato_monitor"
    assert stage["evidence_ref"] == f"cato_evidence:{result['evidence_id']}"


def test_oscal_artifacts_are_attributed_by_what_they_are(project):
    """An OSCAL SSP is still an SSP — `select`, not "wherever OSCAL goes"."""
    from tools.compliance.oscal_generator import (
        generate_oscal_component_definition,
        generate_oscal_ssp,
    )

    generate_oscal_ssp("prod-1", db_path=project)
    generate_oscal_component_definition("prod-1", db_path=project)

    stages = _stages(project)
    assert stages["select"]["evidence_ref"] == "oscal_artifacts:prod-1/ssp"
    assert stages["implement"]["evidence_ref"] == "oscal_artifacts:prod-1/component_definition"
    assert stages["select"]["actor"] == "oscal_generator"


def test_the_first_producer_owns_started_at_across_producers(project):
    """Two producers share `select`; the clock belongs to the earlier one.

    This is the cross-module half of the rule — a single-module test cannot see
    it, and it is what makes automation_time a duration rather than a gap
    between the last two artifacts.
    """
    from tools.compliance.oscal_generator import generate_oscal_ssp
    from tools.compliance.ssp_generator import generate_ssp

    generate_oscal_ssp("prod-1", db_path=project)
    first_started = _stages(project)["select"]["started_at"]

    generate_ssp("prod-1", db_path=project)
    after = _stages(project)["select"]

    assert after["started_at"] == first_started
    assert after["actor"] == "ssp_generator"          # latest writer
    assert after["evidence_ref"].startswith("ssp_documents:")


def test_no_producer_writes_the_categorize_or_authorize_stage(project):
    """Named absences. Neither has an automated producer wired by this card."""
    from tools.compliance.cato_monitor import collect_evidence
    from tools.compliance.poam_generator import generate_poam
    from tools.compliance.ssp_generator import generate_ssp

    generate_ssp("prod-1", db_path=project)
    generate_poam("prod-1", db_path=project)
    collect_evidence("prod-1", "AC-2", "scan_result", "s", db_path=project)

    stages = _stages(project)
    assert "categorize" not in stages
    assert "authorize" not in stages


# ---------------------------------------------------------------------------
# The pre-existing --db defect these producers all shared
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "module_name, func_name",
    [
        ("tools.compliance.ssp_generator", "_get_connection"),
        ("tools.compliance.poam_generator", "_get_connection"),
        ("tools.compliance.stig_checker", "_get_connection"),
    ],
)
def test_a_string_db_path_no_longer_raises_attributeerror(project, module_name, func_name):
    """`--db` is documented on all three CLIs and had never once worked.

    argparse hands `db_path` a STRING; the helper called `.exists()` on it
    directly, so every invocation with an explicit database died in
    `_get_connection` before touching the database.
    """
    import importlib

    module = importlib.import_module(module_name)
    conn = getattr(module, func_name)(project)
    try:
        assert conn.execute("SELECT 1 AS ok").fetchone() is not None
    finally:
        conn.close()


def test_a_genuinely_missing_database_still_raises(tmp_path, monkeypatch):
    """The fix widened the accepted TYPE, it did not remove the check."""
    monkeypatch.setenv("ICDEV_STORAGE_BACKEND", "sqlite")
    from tools.compliance.stig_checker import _get_connection

    with pytest.raises(FileNotFoundError):
        _get_connection(str(tmp_path / "does-not-exist.db"))


# ---------------------------------------------------------------------------
# The surface
# ---------------------------------------------------------------------------

def test_the_ato_dashboard_surfaces_the_clock_fields(project):
    from tools.ato_compliance.dashboard import get_rmf_stages
    from tools.compliance.ssp_generator import generate_ssp
    from tools.db.storage import get_connection

    generate_ssp("prod-1", db_path=project)

    conn = get_connection(db_path=project)
    try:
        stages = get_rmf_stages("prod-1", conn=conn)
    finally:
        conn.close()

    by_name = {s["stage"]: s for s in stages}
    assert by_name["select"]["actor"] == "ssp_generator"
    assert by_name["select"]["started_at"]
    # An unrecorded stage carries None for every clock field — never a
    # timestamp, and never a zero, which would render as "measured at once".
    assert by_name["authorize"]["started_at"] is None
    assert by_name["authorize"]["submitted_at"] is None
    assert by_name["authorize"]["actor"] is None


def test_the_dashboard_returns_six_stages_even_with_no_rows(project):
    from tools.ato_compliance.dashboard import get_rmf_stages
    from tools.db.storage import get_connection

    conn = get_connection(db_path=project)
    try:
        stages = get_rmf_stages("prod-1", conn=conn)
    finally:
        conn.close()

    assert len(stages) == 6
    assert all(s["status"] == "not_started" for s in stages)
    assert all(s["submitted_at"] is None for s in stages)
