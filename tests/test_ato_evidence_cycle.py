# CUI // SP-CTI
"""rmf-inert-02 — the CALLER for the ATO evidence stack.

The eight tables an ATO package is assembled from held zero rows while every
generator and the rmf-cyc-01 stage recorder sat on main, invoked by nobody.
These tests pin the caller AND the refusals that keep it from fabricating.
"""
from __future__ import annotations

import ast
import sqlite3
from pathlib import Path

import pytest

from tools.genesis.reflexes import ato_evidence_cycle as reflex

REPO_ROOT = Path(__file__).resolve().parent.parent


# ---------------------------------------------------------------------------
# Registration — a reflex in only one of the two places has never run
# ---------------------------------------------------------------------------

def test_registered_in_daemon_reflex_names():
    from tools.genesis.daemon import REFLEX_NAMES

    assert "ato_evidence_cycle" in REFLEX_NAMES


def test_registered_in_genesis_config():
    import yaml

    with open(REPO_ROOT / "args" / "genesis_config.yaml", encoding="utf-8") as fh:
        cfg = yaml.safe_load(fh)
    block = cfg["reflexes"]["ato_evidence_cycle"]
    assert block["enabled"] is True
    assert block["interval_seconds"] == reflex.CADENCE_HOURS * 3600


# ---------------------------------------------------------------------------
# The declared control mapping must exist in the catalogue it claims to cite
# ---------------------------------------------------------------------------

def test_every_declared_control_is_in_the_nist_catalogue():
    """Evidence filed against a control the catalogue lacks is worse than none.

    It counts toward coverage and can never be reviewed.
    """
    catalogue = reflex.known_controls()
    assert catalogue, "NIST 800-53 catalogue must be readable"
    for kind, controls in reflex.ARTIFACT_CONTROLS.items():
        assert controls, f"{kind} declares no control"
        for control_id in controls:
            assert control_id in catalogue, f"{kind} -> {control_id} not in catalogue"


def test_every_declared_artifact_maps_to_a_known_rmf_stage():
    """The caller may not name an artifact kind the stage recorder cannot place."""
    from tools.compliance.rmf_stage_recorder import ARTIFACT_STAGE

    for kind in reflex.ARTIFACT_CONTROLS:
        assert kind in ARTIFACT_STAGE
    for kind in reflex.ARTIFACT_EVIDENCE_TYPE:
        assert kind in ARTIFACT_STAGE


def test_declared_evidence_types_are_in_the_producer_vocabulary():
    from tools.compliance.cato_monitor import EVIDENCE_TYPES

    for kind, evidence_type in reflex.ARTIFACT_EVIDENCE_TYPE.items():
        assert evidence_type in EVIDENCE_TYPES, kind


# ---------------------------------------------------------------------------
# The cwd trap — an assessment that measured nothing is not an assessment
# ---------------------------------------------------------------------------

def test_resolve_project_dir_accepts_a_directory_that_resolves(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "src").mkdir()
    verdict = reflex.resolve_project_dir("src")
    assert verdict["usable"] is True
    assert verdict["reason"] is None


def test_resolve_project_dir_refuses_and_names_the_cwd_case(tmp_path, monkeypatch):
    """The producer would have recorded a complete assessment measuring nothing.

    ``run_stig_check`` evaluates ``Path(directory_path).is_dir()`` and, when it
    is False, sets ``can_auto_check = False`` and records every finding as
    ``Not_Reviewed`` WITHOUT failing. ``tools`` resolves under the repo root but
    not from an unrelated cwd, so this is the live shape of that trap.
    """
    monkeypatch.chdir(tmp_path)
    verdict = reflex.resolve_project_dir("tools")
    assert verdict["usable"] is False
    assert verdict["reason"] == "project_directory_not_resolvable_from_cwd"
    # Both paths are reported: the repair is "start from the repo root", not
    # "the project is misconfigured", and only the second path says which.
    assert verdict["repo_path"] is not None


def test_resolve_project_dir_separates_absent_from_unresolvable(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    assert reflex.resolve_project_dir("no-such-dir")["reason"] == "project_directory_absent"
    assert reflex.resolve_project_dir("")["reason"] == "project_has_no_directory_path"
    assert reflex.resolve_project_dir(None)["reason"] == "project_has_no_directory_path"


# ---------------------------------------------------------------------------
# Fabrication guards, read from the source
# ---------------------------------------------------------------------------

def _reflex_source() -> str:
    return Path(reflex.__file__).read_text(encoding="utf-8")


def test_the_reflex_never_calls_a_synthetic_seeder():
    """A daemon fabricating its own evidence on a cadence is the whole prohibition.

    Read from the AST rather than by behaviour: a behavioural test over today's
    code passes on the day somebody adds the call.
    """
    tree = ast.parse(_reflex_source())
    called = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            fn = node.func
            name = fn.attr if isinstance(fn, ast.Attribute) else getattr(fn, "id", "")
            called.add(name)
    for banned in ("seed_synthetic_devices", "seed_synthetic", "SyntheticDataEngine"):
        assert banned not in called, f"{banned} must never be called from a reflex"


def test_deferred_producers_are_never_invoked():
    """ssp_generator / oscal_generator must not be called while their input is empty.

    An SSP over zero control implementations asserts that a security plan exists
    where none does, and stamps ``select`` in_progress on the way past.
    """
    source = _reflex_source()
    tree = ast.parse(source)
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            if "ssp_generator" in node.module or "oscal_generator" in node.module:
                imported.add(node.module)
        if isinstance(node, ast.Call):
            fn = node.func
            name = fn.attr if isinstance(fn, ast.Attribute) else getattr(fn, "id", "")
            assert name not in ("generate_ssp", "generate_oscal_ssp", "generate_oscal_poam")
    assert not imported, f"deferred producers must not be imported: {imported}"


def test_artifact_dir_is_not_the_producers_in_tree_default():
    """A 24h reflex writing into tools/compliance/ would auto-commit its reports."""
    out = reflex.artifact_dir(reflex._FALLBACK_CFG, "proj-1")
    assert "compliance" in out.parts
    assert "tools" not in out.parts
    # And it must be ignored by git, or the auto-commit hook sweeps it anyway.
    gitignore = (REPO_ROOT / ".gitignore").read_text(encoding="utf-8")
    assert "data/compliance/" in gitignore


def test_artifact_dir_honours_an_absolute_configured_path(tmp_path):
    out = reflex.artifact_dir({"artifact_dir": str(tmp_path)}, "proj-1")
    assert out == tmp_path / "proj-1"


# ---------------------------------------------------------------------------
# The verdict — a cycle that produced nothing is never `ok`
# ---------------------------------------------------------------------------

def test_a_cycle_that_produced_nothing_reports_unmeasured():
    result = reflex._finalise(
        {"status": "ok", "artifacts_produced": 0, "metric_value": 0.0}
    )
    assert result["status"] == "unmeasured"
    assert result["metric_value"] == 0.0


def test_a_cycle_that_produced_something_stays_ok():
    result = reflex._finalise(
        {"status": "ok", "artifacts_produced": 2, "metric_value": 0.0}
    )
    assert result["status"] == "ok"
    assert result["metric_value"] == 2.0


def test_an_error_cycle_is_not_relabelled_unmeasured():
    result = reflex._finalise(
        {"status": "error", "artifacts_produced": 0, "metric_value": 0.0}
    )
    assert result["status"] == "error"


def test_run_on_an_empty_board_is_unmeasured_and_still_a_healthy_cycle(tmp_path):
    """No project registered => nothing to assess. Not a failure, not `ok`.

    ``success`` must stay True: a reflex returning success=False is scored a
    failure on every cycle forever and lands in the circuit breaker, so a
    deployment that has simply not registered a project yet would disable it.
    """
    db = tmp_path / "empty.db"
    conn = sqlite3.connect(db)
    conn.execute("CREATE TABLE projects (id TEXT PRIMARY KEY, name TEXT, directory_path TEXT)")
    conn.commit()
    conn.close()

    result = reflex.run({"db_path": str(db)})
    assert result["success"] is True
    assert result["status"] == "unmeasured"
    assert result["artifacts_produced"] == 0
    assert any(r["reason"] == "no_projects_registered" for r in result["refusals"])


# ---------------------------------------------------------------------------
# Substrate probes: None (unreadable) is never 0 (readable, empty)
# ---------------------------------------------------------------------------

class _FakeConn:
    def __init__(self, rows=None, raises=False):
        self._rows = rows
        self._raises = raises

    def execute(self, sql, params=()):
        if self._raises:
            raise RuntimeError("boom")
        return self

    def fetchone(self):
        return {"n": self._rows}


def test_count_returns_none_for_an_absent_table(monkeypatch):
    monkeypatch.setattr("tools.db.storage.table_exists", lambda conn, t: False)
    assert reflex._count(_FakeConn(0), "nope") is None


def test_count_returns_a_measured_zero_for_a_present_empty_table(monkeypatch):
    monkeypatch.setattr("tools.db.storage.table_exists", lambda conn, t: True)
    assert reflex._count(_FakeConn(0), "present") == 0


def test_open_stig_findings_is_none_when_the_table_is_absent(monkeypatch):
    monkeypatch.setattr("tools.db.storage.table_exists", lambda conn, t: False)
    assert reflex.open_stig_findings(_FakeConn(0), "p") is None


def test_open_stig_findings_counts_only_open(monkeypatch):
    """The same predicate poam_generator uses.

    Counting every row would run the generator against an assessment whose
    findings were all Not_Reviewed and emit an empty POA&M, which reads
    downstream as "assessed, no weaknesses".
    """
    monkeypatch.setattr("tools.db.storage.table_exists", lambda conn, t: True)
    seen = {}

    class Conn(_FakeConn):
        def execute(self, sql, params=()):
            seen["sql"] = sql
            return self

    assert reflex.open_stig_findings(Conn(3), "p") == 3
    assert "status = 'Open'" in seen["sql"]


# ---------------------------------------------------------------------------
# Evidence collection refuses rather than filing an unverifiable pointer
# ---------------------------------------------------------------------------

def test_evidence_refused_when_the_artifact_file_is_absent(tmp_path):
    out = reflex._collect_artifact_evidence(
        "p", "poam", str(tmp_path / "gone.md"), db_path=None, catalogue={"CA-5"}
    )
    assert out["collected"] == []
    assert out["refusals"][0]["reason"] == "artifact_file_absent"


def test_evidence_refused_when_the_catalogue_is_unreadable(tmp_path):
    art = tmp_path / "poam.md"
    art.write_text("x", encoding="utf-8")
    out = reflex._collect_artifact_evidence(
        "p", "poam", str(art), db_path=None, catalogue=set()
    )
    assert out["collected"] == []
    assert out["refusals"][0]["reason"] == "nist_catalogue_unreadable"


def test_evidence_refused_for_a_control_the_catalogue_lacks(tmp_path):
    art = tmp_path / "poam.md"
    art.write_text("x", encoding="utf-8")
    out = reflex._collect_artifact_evidence(
        "p", "poam", str(art), db_path=None, catalogue={"AC-2"}
    )
    assert out["collected"] == []
    assert out["refusals"][0]["reason"] == "control_not_in_catalogue"
    assert out["refusals"][0]["detail"] == "CA-5"


# ---------------------------------------------------------------------------
# The end-to-end proof: 0 rows -> real rows, traceable to a real artifact
# ---------------------------------------------------------------------------

@pytest.fixture
def seeded_board(tmp_path):
    """A real board with the eight tables empty, and one real project."""
    from tools.db.storage import get_connection

    db = tmp_path / "board.db"
    src = tmp_path / "src"
    src.mkdir()
    (src / "app.py").write_text(
        "SECRET_KEY = 'x'\nDEBUG = True\n", encoding="utf-8"
    )

    import subprocess
    import sys

    subprocess.run(
        [sys.executable, str(REPO_ROOT / "tools" / "db" / "init_icdev_db.py"),
         "--db-path", str(db)],
        cwd=str(REPO_ROOT), check=True, capture_output=True,
    )
    conn = get_connection(db_path=str(db))
    conn.execute(
        "INSERT INTO projects (id, name, description, type, classification, status,"
        " directory_path, created_by, impact_level)"
        " VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)",
        ("p-real", "Real", "d", "webapp", "CUI", "active", str(src), "system", "IL5"),
    )
    conn.commit()
    conn.close()
    return {"db": str(db), "src": src, "out": tmp_path / "artifacts"}


def test_dry_run_probes_everything_and_writes_nothing(seeded_board):
    from tools.db.storage import get_connection

    result = reflex.run(
        {"db_path": seeded_board["db"], "dry_run": True,
         "artifact_dir": str(seeded_board["out"])}
    )
    assert result["dry_run"] is True
    assert result["status"] == "unmeasured"
    assert result["artifacts_produced"] == 0
    # It still PROBED: the deferred producers are named, on a dry run too.
    assert {s["producer"] for s in result["skipped_no_input"]} >= {
        "ssp_generator", "oscal_generator"
    }
    conn = get_connection(db_path=seeded_board["db"])
    for table in ("stig_findings", "poam_items", "rmf_workflow_stages", "cato_evidence"):
        row = conn.execute(f"SELECT COUNT(*) AS n FROM {table}").fetchone()
        assert dict(row)["n"] == 0, table
    conn.close()
    assert not seeded_board["out"].exists()


def test_the_cycle_turns_empty_tables_into_traceable_rows(seeded_board):
    """THE CARD'S SUCCESS CRITERION: a measured non-zero with its provenance."""
    from tools.db.storage import get_connection

    result = reflex.run(
        {"db_path": seeded_board["db"], "artifact_dir": str(seeded_board["out"])}
    )
    assert result["success"] is True
    assert result["status"] == "ok"
    assert result["artifacts_produced"] >= 1

    # Every one of these was 0 before the cycle.
    assert result["substrate_before"]["rmf_workflow_stages"] == 0
    assert result["substrate_before"]["stig_findings"] == 0
    assert result["substrate_after"]["rmf_workflow_stages"] >= 1
    assert result["substrate_after"]["stig_findings"] >= 1

    conn = get_connection(db_path=seeded_board["db"])
    stages = [
        dict(r)
        for r in conn.execute(
            "SELECT stage, status, started_at, actor, evidence_ref"
            " FROM rmf_workflow_stages"
        ).fetchall()
    ]
    conn.close()

    assert stages, "the stage table must hold rows after a producing cycle"
    for row in stages:
        assert row["started_at"], "a stage row with no clock is not a measurement"
        assert row["actor"], "a stage row must say who wrote it"
        # TRACEABLE: the evidence pointer resolves to something that exists.
        ref = row["evidence_ref"] or ""
        assert ref, "a stage row asserting progress must point at its evidence"
        if ref.startswith("file:"):
            assert Path(ref[len("file:"):]).is_file()

    # `assess` is a consequence of the STIG assessment, which really ran.
    assert "assess" in {r["stage"] for r in stages}


def test_findings_measured_excludes_not_reviewed(seeded_board):
    """An unprobed STIG check is UNMEASURED, not a pass (rmf-zt-01)."""
    result = reflex.run(
        {"db_path": seeded_board["db"], "artifact_dir": str(seeded_board["out"])}
    )
    stig = [
        a for p in result["projects"] for a in p["artifacts"]
        if a["artifact_kind"] == "stig_assessment"
    ]
    assert stig, "the STIG assessment must have run"
    art = stig[0]
    assert art["findings_measured"] < art["findings_assessed"], (
        "the template carries findings with no auto-check; they must not be "
        "counted as measured"
    )
    not_reviewed = sum(v["Not_Reviewed"] for v in art["summary"].values())
    assert art["findings_assessed"] - art["findings_measured"] == not_reviewed


def test_a_second_cycle_does_not_reset_the_started_at_clock(seeded_board):
    """rmf-cyc-01: started_at is stamped ONCE.

    A reflex on a 24h cadence re-runs the producers forever; if each run moved
    the clock, automation_time would measure the gap between the last two
    cycles rather than the age of the package.
    """
    from tools.db.storage import get_connection

    reflex.run({"db_path": seeded_board["db"], "artifact_dir": str(seeded_board["out"])})
    conn = get_connection(db_path=seeded_board["db"])
    first = {
        dict(r)["stage"]: dict(r)["started_at"]
        for r in conn.execute("SELECT stage, started_at FROM rmf_workflow_stages").fetchall()
    }
    conn.close()

    reflex.run({"db_path": seeded_board["db"], "artifact_dir": str(seeded_board["out"])})
    conn = get_connection(db_path=seeded_board["db"])
    second = {
        dict(r)["stage"]: dict(r)["started_at"]
        for r in conn.execute("SELECT stage, started_at FROM rmf_workflow_stages").fetchall()
    }
    conn.close()

    assert first, "the first cycle must have recorded a stage"
    for stage, started in first.items():
        assert second[stage] == started, f"{stage} clock moved on the second cycle"


def test_artifacts_are_not_written_into_the_tracked_source_tree(seeded_board):
    reflex.run(
        {"db_path": seeded_board["db"], "artifact_dir": str(seeded_board["out"])}
    )
    written = list(seeded_board["out"].rglob("*.md"))
    assert written, "the cycle must have written its artifacts"
    for path in written:
        assert seeded_board["out"] in path.parents or path.parent.parent == seeded_board["out"]


# ---------------------------------------------------------------------------
# The DAEMON'S dispatch conditions — a reflex proven only by a direct call has
# not been proven at all
# ---------------------------------------------------------------------------

def test_the_daemon_dispatches_run_with_a_trust_kernel_not_a_connection():
    """The second positional argument is the TrustKernel, never a DB handle.

    `_run_reflex_impl_inner` calls `module.run(config, trust)`. A reflex that
    treated arg 2 as a connection would raise on every cycle while a direct
    `run({})` in a test passed, so the signature is pinned against the
    daemon's own call site rather than against the docstring.
    """
    import inspect

    params = list(inspect.signature(reflex.run).parameters)
    assert params[:2] == ["ctx", "trust"], params
    # It must tolerate being handed the kernel positionally, as the daemon does.
    out = reflex.run({"dry_run": True, "db_path": ":memory:"}, object())
    assert out["success"] is True


def test_a_producing_cycle_persists_through_the_daemon_connection_scope(seeded_board):
    """THE CALLER MUST WORK WHERE IT IS ACTUALLY CALLED FROM.

    `tools/genesis/daemon.py::_run_reflex_impl_inner` wraps every reflex in
    `reflex_connection_scope()`, which ROLLS BACK and closes any connection
    opened inside the block and left open. A producer that wrote without
    committing would therefore report a healthy cycle whose rows silently
    vanished at scope exit -- the reflex would work by hand, forever, and
    write nothing on the 24h cadence, while the metric read green.

    Every other end-to-end test here calls `reflex.run` directly, which is
    exactly the shape that cannot see this. So this one enters the scope.
    """
    from tools.db.storage import get_connection, reflex_connection_scope

    with reflex_connection_scope():
        result = reflex.run(
            {"db_path": seeded_board["db"], "artifact_dir": str(seeded_board["out"])}
        )
        assert result["status"] == "ok", result["refusals"]
        assert result["artifacts_produced"] >= 1

    # Re-open AFTER the scope has exited and done its reclamation. Reading the
    # counts from `result` would prove nothing -- those were measured on the
    # inside, which is precisely the side a rollback does not affect.
    conn = get_connection(db_path=seeded_board["db"])
    persisted = {
        table: dict(conn.execute(f"SELECT COUNT(*) AS n FROM {table}").fetchone())["n"]
        for table in ("rmf_workflow_stages", "stig_findings", "cato_evidence")
    }
    conn.close()

    assert persisted["rmf_workflow_stages"] >= 1, (
        "the stage rows did not survive the daemon's connection scope"
    )
    assert persisted["stig_findings"] >= 1, persisted
    assert persisted["cato_evidence"] >= 1, persisted
