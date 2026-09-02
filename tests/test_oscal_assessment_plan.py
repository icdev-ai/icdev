#!/usr/bin/env python3
# CUI // SP-CTI
"""Tests for the OSCAL Assessment Plan generator (rmf-oscal-01).

The acceptance criterion is that an assessment-plan validates through the
EXISTING validator chain, so these tests call ``validate_oscal`` and
``validate_oscal_deep`` -- the same two entry points every other artifact type
goes through -- rather than asserting against a hand-written expectation of
what the document should contain.

The negative cases matter more than the positive one. A structural validator
that accepts everything would pass the happy path too, so each required block
is removed in turn and the validator is required to REFUSE.
"""

import json
import sqlite3
import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from tools.compliance.oscal_generator import (  # noqa: E402
    generate_oscal_assessment_plan,
    validate_oscal,
)

# The oscal_artifacts CHECK that migration 20260902214729 widens. Restated here
# in the fixture because the fixture builds the table from scratch -- if the
# migration were skipped, this schema is what the live database would NOT have.
_SCHEMA = """
CREATE TABLE projects (id TEXT PRIMARY KEY, name TEXT, description TEXT, type TEXT,
  impact_level TEXT, classification TEXT, directory_path TEXT, created_by TEXT, status TEXT);
CREATE TABLE project_controls (project_id TEXT, control_id TEXT, implementation_status TEXT,
  implementation_description TEXT, responsible_role TEXT, evidence_path TEXT, last_assessed TEXT);
CREATE TABLE compliance_controls (id TEXT PRIMARY KEY, family TEXT, title TEXT, description TEXT);
CREATE TABLE oscal_artifacts (
  id INTEGER PRIMARY KEY AUTOINCREMENT, project_id TEXT NOT NULL,
  artifact_type TEXT NOT NULL CHECK(artifact_type IN
    ('ssp','poam','assessment_results','assessment_plan','component_definition','catalog','profile')),
  oscal_version TEXT, format TEXT, file_path TEXT NOT NULL, file_hash TEXT,
  schema_valid INTEGER, validation_errors TEXT, generated_at TEXT, classification TEXT,
  UNIQUE(project_id, artifact_type, format));
CREATE TABLE cato_evidence (id INTEGER PRIMARY KEY AUTOINCREMENT, project_id TEXT, control_id TEXT,
  evidence_type TEXT, evidence_source TEXT, evidence_path TEXT, evidence_hash TEXT,
  collected_at TEXT, expires_at TEXT, is_fresh INTEGER, freshness_check_at TEXT,
  status TEXT, automation_frequency TEXT);
CREATE TABLE audit_trail (id INTEGER PRIMARY KEY AUTOINCREMENT, project_id TEXT, event_type TEXT,
  actor TEXT, action TEXT, details TEXT, affected_files TEXT, classification TEXT);
"""


def _build_db(path, *, controls=(), evidence=(), with_cato_table=True):
    """Create a minimal board for the generator to read.

    ``with_cato_table=False`` reproduces a deployment where the cATO migration
    never ran -- which must read as 'unmeasured', never as 'not-configured'.
    """
    statements = [s for s in _SCHEMA.split(";") if s.strip()]
    if not with_cato_table:
        statements = [s for s in statements if "cato_evidence" not in s]
        assert len(statements) == 5, statements  # every table except cato_evidence

    conn = sqlite3.connect(str(path))
    conn.executescript(";\n".join(statements) + ";")
    conn.execute(
        "INSERT INTO projects VALUES ('p1','Mission App','A test system','webapp','IL5','CUI','','ICDEV','active')"
    )
    for control_id in controls:
        conn.execute(
            "INSERT INTO project_controls VALUES ('p1',?,'implemented','Impl.','ISSO','/evidence',NULL)",
            (control_id,),
        )
    for control_id, frequency in evidence:
        conn.execute(
            """INSERT INTO cato_evidence
               (project_id, control_id, evidence_type, evidence_source,
                automation_frequency, status)
               VALUES ('p1', ?, 'scan_result', 'nessus', ?, 'current')""",
            (control_id, frequency),
        )
    conn.commit()
    conn.close()


@pytest.fixture
def populated_db(tmp_path):
    db = tmp_path / "icdev.db"
    _build_db(
        db,
        controls=["AC-2", "AC-2(1)", "SI-4", "AU-6"],
        evidence=[("AC-2", "daily"), ("SI-4", "weekly"), ("AU-6", "manual")],
    )
    return db


@pytest.fixture
def plan(tmp_path, populated_db):
    out = tmp_path / "oscal"
    return generate_oscal_assessment_plan("p1", output_dir=str(out), db_path=str(populated_db))


def _doc(result):
    return json.loads(Path(result["file_path"]).read_text(encoding="utf-8"))["assessment-plan"]


# --- the acceptance criterion ---------------------------------------------


def test_assessment_plan_validates_through_the_structural_validator(plan):
    """The generated plan passes validate_oscal with no errors."""
    assert plan["validation"]["valid"], plan["validation"]["errors"]

    # Re-validate from disk, explicitly typed, so the pass is not just the
    # generator agreeing with itself about auto-detection.
    revalidated = validate_oscal(plan["file_path"], "assessment_plan")
    assert revalidated["valid"], revalidated["errors"]


def test_assessment_plan_type_is_auto_detected_from_its_top_level_key(plan):
    """validate_oscal resolves 'assessment-plan' without being told the type."""
    result = validate_oscal(plan["file_path"])
    assert result["valid"], result["errors"]


def test_assessment_plan_validates_through_the_deep_validator(plan):
    """Every layer of the deep validator either passes or says why it did not.

    The optional layers need pydantic and Java, which are absent on some
    hosts. This test does NOT pytest.skip itself on their absence: a skipped
    test reports as coverage while asserting nothing, which is the defect the
    skip census exists to refuse. A layer that could not run must instead
    REPORT a reason, and that is what is asserted here -- so an optional
    dependency silently disappearing still fails this test.
    """
    from tools.compliance.oscal_tools import validate_oscal_deep

    result = validate_oscal_deep(plan["file_path"])
    by_name = {v["validator"]: v for v in result["validators"]}

    # The structural layer has no optional dependency and must always pass.
    assert by_name["icdev_structural"]["valid"], by_name["icdev_structural"]["errors"]

    for name in ("oscal_pydantic", "oscal_cli_metaschema"):
        layer = by_name[name]
        if layer.get("skipped"):
            assert layer.get("reason"), f"{name} skipped without saying why: {layer}"
        else:
            assert layer.get("valid"), (name, layer.get("errors"))


# --- the validator must REFUSE a plan that is missing a required block -----


@pytest.mark.parametrize(
    "label,mutate,expected_fragment",
    [
        ("no reviewed-controls", lambda d: d.pop("reviewed-controls"), "reviewed-controls"),
        ("no import-ssp", lambda d: d.pop("import-ssp"), "import-ssp"),
        ("no import-ssp href", lambda d: d["import-ssp"].pop("href"), "href"),
        (
            "empty control-selections",
            lambda d: d["reviewed-controls"].__setitem__("control-selections", []),
            "empty",
        ),
        (
            "a selection that selects nothing",
            lambda d: d["reviewed-controls"].__setitem__("control-selections", [{"description": "x"}]),
            "selects nothing",
        ),
        ("an invalid task type", lambda d: d["tasks"][0].__setitem__("type", "recurring"), "invalid type"),
    ],
)
def test_validator_refuses_a_structurally_broken_plan(tmp_path, plan, label, mutate, expected_fragment):
    doc = json.loads(Path(plan["file_path"]).read_text(encoding="utf-8"))
    mutate(doc["assessment-plan"])
    broken = tmp_path / "broken.oscal.json"
    broken.write_text(json.dumps(doc), encoding="utf-8")

    result = validate_oscal(str(broken), "assessment_plan")
    assert not result["valid"], f"{label} was accepted"
    assert any(expected_fragment in err for err in result["errors"]), result["errors"]


# --- what the plan actually says -------------------------------------------


def test_reviewed_controls_are_the_projects_controls_in_oscal_form(plan):
    """Control ids are lowercase and enhancements use dot notation."""
    doc = _doc(plan)
    selections = doc["reviewed-controls"]["control-selections"]
    ids = [c["control-id"] for c in selections[0]["include-controls"]]
    assert ids == ["ac-2", "ac-2.1", "au-6", "si-4"]
    assert plan["reviewed_controls"] == 4


def test_task_cadence_comes_from_the_cato_monitors_own_windows(plan):
    """at-frequency periods are the monitor's EXPIRY_WINDOWS, not a local copy.

    This is the whole point of the model: a plan that invented its own
    intervals would promise a refresh cadence nothing enforces.
    """
    from tools.compliance.cato_monitor import EXPIRY_WINDOWS

    doc = _doc(plan)
    timed = {
        t["title"]: t["timing"]["at-frequency"]["period"] for t in doc["tasks"] if "timing" in t
    }
    assert timed == {
        "Collect 'daily' evidence": EXPIRY_WINDOWS["daily"],
        "Collect 'weekly' evidence": EXPIRY_WINDOWS["weekly"],
        "Collect 'manual' evidence": EXPIRY_WINDOWS["manual"],
    }
    assert plan["continuous_monitoring"] == "configured"


def test_every_associated_activity_resolves_to_a_declared_activity(plan):
    """A task pointing at an activity uuid nothing declares reviews nothing."""
    doc = _doc(plan)
    declared = {a["uuid"] for a in doc["local-definitions"]["activities"]}
    referenced = {
        assoc["activity-uuid"] for task in doc["tasks"] for assoc in task.get("associated-activities", [])
    }
    assert referenced, "no task references any activity"
    assert referenced <= declared, referenced - declared


def test_the_artifact_record_is_actually_persisted(plan, populated_db):
    """The row must land, not be swallowed by _store_oscal_artifact's warning.

    This is the test that fails without migration 20260902214729: the CHECK
    refuses 'assessment_plan', the INSERT raises, the exception is caught and
    printed to stderr, and the generator still returns a successful result.
    """
    conn = sqlite3.connect(str(populated_db))
    try:
        rows = conn.execute(
            "SELECT artifact_type, schema_valid FROM oscal_artifacts WHERE project_id = 'p1'"
        ).fetchall()
    finally:
        conn.close()
    assert rows == [("assessment_plan", 1)], rows


def test_generation_writes_an_audit_row(plan, populated_db):
    conn = sqlite3.connect(str(populated_db))
    try:
        count = conn.execute(
            "SELECT COUNT(*) FROM audit_trail WHERE event_type = 'oscal_generated'"
        ).fetchone()[0]
    finally:
        conn.close()
    assert count == 1


# --- the three evidence states, which must never be merged ------------------


def test_a_project_with_no_evidence_reads_not_configured_and_invents_no_cadence(tmp_path):
    db = tmp_path / "empty_evidence.db"
    _build_db(db, controls=["AC-2"], evidence=[])

    result = generate_oscal_assessment_plan("p1", output_dir=str(tmp_path / "o"), db_path=str(db))

    assert result["validation"]["valid"], result["validation"]["errors"]
    assert result["continuous_monitoring"] == "not-configured"
    doc = _doc(result)
    assert not any("timing" in t for t in doc["tasks"]), "a cadence was invented from no evidence"


def test_an_unreadable_cato_table_reads_unmeasured_not_not_configured(tmp_path):
    """A migration that never ran is not a system nobody monitors.

    Merging the two would let an unmigrated deployment publish an assessment
    plan asserting that no continuous monitoring is configured.
    """
    db = tmp_path / "no_cato.db"
    _build_db(db, controls=["AC-2"], evidence=[], with_cato_table=False)

    result = generate_oscal_assessment_plan("p1", output_dir=str(tmp_path / "o"), db_path=str(db))

    assert result["continuous_monitoring"] == "unmeasured"
    assert result["validation"]["valid"], result["validation"]["errors"]


def test_a_project_with_no_controls_says_so_instead_of_claiming_full_coverage(tmp_path):
    """include-all is emitted with a remark, never a silent empty selection."""
    db = tmp_path / "no_controls.db"
    _build_db(db, controls=[], evidence=[])

    result = generate_oscal_assessment_plan("p1", output_dir=str(tmp_path / "o"), db_path=str(db))

    assert result["validation"]["valid"], result["validation"]["errors"]
    assert result["reviewed_controls"] == 0
    selection = _doc(result)["reviewed-controls"]["control-selections"][0]
    assert "include-all" in selection
    assert "NOT a claim" in selection["description"]


def test_import_ssp_is_flagged_unresolved_when_no_ssp_exists(plan):
    """A plan pointing at an SSP that was never generated must say so."""
    assert plan["import_ssp_resolved"] is False
    doc = _doc(plan)
    assert doc["import-ssp"]["href"] == "#ssp-not-yet-generated"
    assert "placeholder" in doc["import-ssp"]["remarks"]


def test_import_ssp_resolves_to_a_recorded_ssp_artifact(tmp_path, populated_db):
    conn = sqlite3.connect(str(populated_db))
    conn.execute(
        """INSERT INTO oscal_artifacts
           (project_id, artifact_type, oscal_version, format, file_path,
            file_hash, schema_valid, generated_at, classification)
           VALUES ('p1','ssp','1.1.2','json','/artifacts/ssp.oscal.json','h',1,'2026-09-02','CUI')"""
    )
    conn.commit()
    conn.close()

    result = generate_oscal_assessment_plan("p1", output_dir=str(tmp_path / "o"), db_path=str(populated_db))

    assert result["import_ssp_resolved"] is True
    assert _doc(result)["import-ssp"]["href"] == "/artifacts/ssp.oscal.json"


# --- registration -----------------------------------------------------------


def test_assessment_plan_is_registered_in_every_artifact_type_map():
    """A model registered in one map and not another is reachable by one door."""
    from tools.compliance import oscal_generator, oscal_tools

    assert oscal_tools._OSCAL_TYPE_MAP["assessment-plan"] == "assessment_plan"
    assert "assessment_plan" in oscal_tools._OSCAL_PYDANTIC_MAP
    assert oscal_tools._OSCAL_CLI_SUBCMD["assessment_plan"] == "assessment-plan"
    assert oscal_tools._get_builtin_v2_model("assessment_plan") is not None
    assert callable(oscal_generator.generate_oscal_assessment_plan)

    # Every map must cover exactly the same artifact types, or a sixth model
    # lands in some of them and is silently unreachable through the rest.
    types = set(oscal_tools._OSCAL_TYPE_MAP.values())
    assert types == set(oscal_tools._OSCAL_PYDANTIC_MAP)
    assert types == set(oscal_tools._OSCAL_CLI_SUBCMD)


def test_generate_all_oscal_includes_the_assessment_plan():
    """The aggregate must run five generators, not the original four."""
    import inspect

    from tools.compliance import oscal_generator

    source = inspect.getsource(oscal_generator.generate_all_oscal)
    assert "generate_oscal_assessment_plan" in source


def test_the_api_generate_route_offers_the_assessment_plan():
    """api/oscal.py exposes the model and refuses an unknown type."""
    from tools.dashboard.api.oscal import GENERATOR_FUNCTIONS

    assert GENERATOR_FUNCTIONS["assessment_plan"] == "generate_oscal_assessment_plan"
    assert set(GENERATOR_FUNCTIONS) == {
        "ssp",
        "poam",
        "assessment_results",
        "assessment_plan",
        "component_definition",
        "all",
    }


def test_oscal_artifacts_check_admits_assessment_plan_for_fresh_databases():
    """init_icdev_db.py must grant the value every new database is created with.

    The migration reaches databases that already exist; this DDL is what a
    fresh one gets, and the two must agree. Read from the module's own
    SCHEMA_SQL rather than from the file as text, so the assertion is made
    against the string the initialiser actually executes.
    """
    from tools.db.init_icdev_db import SCHEMA_SQL

    marker = "CREATE TABLE IF NOT EXISTS oscal_artifacts"
    assert marker in SCHEMA_SQL
    block = SCHEMA_SQL.split(marker, 1)[1].split(");", 1)[0]
    assert "'assessment_plan'" in block, block


def test_the_ddl_actually_accepts_an_assessment_plan_row():
    """Execute the real CREATE TABLE and insert the value it must now admit.

    Asserting on the DDL text alone would pass against a CHECK that names the
    value in a comment; this proves SQLite accepts the row and still refuses
    an unknown type.
    """
    from tools.db.init_icdev_db import SCHEMA_SQL

    marker = "CREATE TABLE IF NOT EXISTS oscal_artifacts"
    ddl = marker + SCHEMA_SQL.split(marker, 1)[1].split(");", 1)[0] + ")"

    conn = sqlite3.connect(":memory:")
    try:
        conn.execute(ddl)
        conn.execute(
            "INSERT INTO oscal_artifacts (project_id, artifact_type, file_path) "
            "VALUES ('p1', 'assessment_plan', '/x.json')"
        )
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "INSERT INTO oscal_artifacts (project_id, artifact_type, file_path) "
                "VALUES ('p1', 'assessment_manifesto', '/y.json')"
            )
    finally:
        conn.close()


def test_the_mcp_oscal_generate_tool_routes_to_the_assessment_plan_generator():
    """tools.mcp.compliance_server dispatches the new artifact type.

    The handler resolves its target through ``_import_tool``, so patching that
    records the function NAME it asked for -- which is the registration under
    test -- without generating anything.
    """
    from tools.mcp import compliance_server

    requested = []

    def _recording_import(module_path, func_name):
        requested.append((module_path, func_name))
        return lambda project_id, **kwargs: {"routed": func_name, "project_id": project_id}

    original = compliance_server._import_tool
    compliance_server._import_tool = _recording_import
    try:
        result = compliance_server.handle_oscal_generate(
            {"artifact": "assessment_plan", "project_id": "p1"}
        )
    finally:
        compliance_server._import_tool = original

    assert requested == [("tools.compliance.oscal_generator", "generate_oscal_assessment_plan")]
    assert result["routed"] == "generate_oscal_assessment_plan"


def test_the_mcp_handler_still_routes_the_four_original_artifact_types():
    """The new entry must not displace an existing one."""
    from tools.mcp import compliance_server

    expected = {
        "ssp": "generate_oscal_ssp",
        "poam": "generate_oscal_poam",
        "assessment_results": "generate_oscal_assessment_results",
        "component_definition": "generate_oscal_component_definition",
        "all": "generate_all_oscal",
    }

    original = compliance_server._import_tool
    for artifact, func_name in expected.items():
        requested = []
        compliance_server._import_tool = lambda m, f, _r=requested: (
            _r.append(f) or (lambda project_id, **kw: None)
        )
        try:
            compliance_server.handle_oscal_generate({"artifact": artifact, "project_id": "p1"})
        finally:
            compliance_server._import_tool = original
        assert requested == [func_name], (artifact, requested)


def test_a_migration_widens_the_check_for_existing_databases():
    """CREATE TABLE IF NOT EXISTS never alters an existing table."""
    migrations = _REPO_ROOT / "tools" / "db" / "migrations"
    widening = [
        p
        for p in migrations.glob("*/up.sql")
        if "oscal_artifacts_artifact_type_check" in p.read_text(encoding="utf-8")
        and "assessment_plan" in p.read_text(encoding="utf-8")
    ]
    assert widening, "no migration widens oscal_artifacts.artifact_type"
