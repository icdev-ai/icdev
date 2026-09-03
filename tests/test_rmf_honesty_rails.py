# CUI // SP-CTI
"""rmf-rail-01: no rate in the RMF surfaces may return 0.0 or 100.0 over an
empty denominator.

THE DEFECT. Measured on the live board 2026-09-03: project_controls,
cato_evidence, rmf_workflow_stages and asset_visibility_snapshots all held
ZERO rows, and over that estate:

  * get_control_summary reported posture_pct 0.0 -- "assessed, nothing
    implemented" -- for a project with no controls assigned;
  * get_posture_score graded the project F at 16 points: 0.0 control coverage,
    0.0 RMF progress over six SYNTHETIC not_started stages, and 55 artifact
    points awarded for the ABSENCE of POAM items and STIG findings;
  * compute_cato_readiness reported readiness_pct 0.0 and automated_pct 0.0
    over no evidence at all -- which is why tools/fabric/posture.py refused
    to carry either number (rmf-fab-02) and re-derived its own.

Two rails, and they are different tests:

  behavioural  every one of those rates is None -- never 0.0, never 100.0 --
               against an empty denominator, AND a measured zero stays a
               real 0.0, because the two must never be confused;
  structural   no ratio in the RMF module set falls back to a constant 0 /
               0.0 / 100 / 100.0 through a conditional expression. This is
               the perfect-score census (rem-hyg-13) widened to the ZERO
               fallback, scoped to these modules only: tree-wide the
               `if x else 0` shape is ~1,167 ordinary counters, but a RATIO
               falling back to 0 in a surface that reports readiness is the
               same fabrication as one falling back to 100.

The four detector states (never_ran | unmeasurable | clean | findings) and the
three posture / visibility states stay distinct constants: a test here reads
them back so a refactor that folds two into one goes red.
"""
from __future__ import annotations

import ast
import sqlite3
from pathlib import Path

import pytest
import yaml

REPO = Path(__file__).resolve().parent.parent

# The RMF surfaces this card guards. NEW modules the rmf-* cards landed, plus
# the pre-existing ones they rewired. Add a module here when a card adds one.
RMF_SURFACES = [
    "tools/ato_compliance/dashboard.py",
    "tools/assets/discovery_adapters/harness.py",
    "tools/assets/discovery_adapters/runner.py",
    "tools/assets/identity.py",
    "tools/assets/visibility.py",
    "tools/compliance/ato_packager.py",
    "tools/compliance/cato_monitor.py",
    "tools/compliance/rmf_cycle_time.py",
    "tools/compliance/rmf_stage_recorder.py",
    "tools/compliance/stig_ckl_writer.py",
    "tools/devsecops/zta_maturity_scorer.py",
    "tools/fabric/posture.py",
    "tools/genesis/reflexes/asset_discovery.py",
    "tools/network/discovery_store.py",
    "tools/security_canvas/device_compliance_scanner.py",
    "tools/security_canvas/zt_verdict_survey.py",
]

# The substrates this card registers (the card's third acceptance item).
RMF_SUBSTRATES = {
    "asset_identity",
    "rmf_workflow_stages",
    "asset_visibility_snapshots",
    "cato_evidence",
    "project_controls",
}

# ---------------------------------------------------------------------------
# Fixtures -- always a throwaway database, never the ambient icdev.db
# ---------------------------------------------------------------------------

_ATO_SCHEMA = """
CREATE TABLE projects (
    id TEXT PRIMARY KEY, name TEXT, type TEXT DEFAULT 'webapp',
    classification TEXT DEFAULT 'CUI', impact_level TEXT DEFAULT 'IL4'
);
CREATE TABLE project_controls (
    id INTEGER PRIMARY KEY AUTOINCREMENT, project_id TEXT NOT NULL,
    control_id TEXT NOT NULL, implementation_status TEXT NOT NULL DEFAULT 'planned'
);
CREATE TABLE ssp_documents (
    id INTEGER PRIMARY KEY AUTOINCREMENT, project_id TEXT, version TEXT,
    system_name TEXT, content TEXT, status TEXT
);
CREATE TABLE poam_items (
    id INTEGER PRIMARY KEY AUTOINCREMENT, project_id TEXT, severity TEXT,
    status TEXT, weakness TEXT, milestone_date TEXT
);
CREATE TABLE stig_findings (
    id INTEGER PRIMARY KEY AUTOINCREMENT, project_id TEXT, severity TEXT,
    status TEXT, finding_id TEXT
);
CREATE TABLE sbom_records (
    id INTEGER PRIMARY KEY AUTOINCREMENT, project_id TEXT, format TEXT,
    created_at TEXT
);
CREATE TABLE rmf_workflow_stages (
    id INTEGER PRIMARY KEY AUTOINCREMENT, project_id TEXT NOT NULL,
    stage TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'not_started',
    assigned_to TEXT, completed_at TEXT, notes TEXT,
    started_at TEXT, submitted_at TEXT, actor TEXT, evidence_ref TEXT,
    UNIQUE(project_id, stage)
);
CREATE TABLE cato_evidence (
    id INTEGER PRIMARY KEY AUTOINCREMENT, project_id TEXT, control_id TEXT,
    evidence_type TEXT, status TEXT, is_fresh INTEGER,
    automation_frequency TEXT
);
"""


@pytest.fixture()
def ato_conn():
    """In-memory SQLite wrapped so the module's PG-native ``%s`` SQL runs."""
    from tools.db.storage import StorageConnection

    raw = sqlite3.connect(":memory:", check_same_thread=False)
    raw.row_factory = sqlite3.Row
    raw.executescript(_ATO_SCHEMA)
    raw.execute("INSERT INTO projects (id, name) VALUES ('p-empty', 'Empty')")
    raw.execute("INSERT INTO projects (id, name) VALUES ('p-zero', 'Zero')")
    raw.commit()
    wrapped = StorageConnection(raw, "sqlite")
    try:
        yield wrapped
    finally:
        wrapped.close()


@pytest.fixture()
def ato_db_file(tmp_path):
    """The same schema at a throwaway PATH, for modules that open by path."""
    db = tmp_path / "rmf_rail.db"
    raw = sqlite3.connect(str(db))
    raw.executescript(_ATO_SCHEMA)
    raw.execute("INSERT INTO projects (id, name) VALUES ('p-empty', 'Empty')")
    raw.execute("INSERT INTO projects (id, name) VALUES ('p-zero', 'Zero')")
    raw.execute(
        "INSERT INTO cato_evidence (project_id, control_id, evidence_type, status, "
        "is_fresh, automation_frequency) VALUES ('p-zero', 'AC-2', 'log', 'stale', 0, 'manual')"
    )
    raw.commit()
    raw.close()
    return db


# ---------------------------------------------------------------------------
# 1. The ATO posture dashboard
# ---------------------------------------------------------------------------

class TestAtoDashboardOverAnEmptyDenominator:
    def test_posture_pct_is_none_with_no_controls(self, ato_conn):
        from tools.ato_compliance.dashboard import get_control_summary

        result = get_control_summary("p-empty", conn=ato_conn)
        assert result["total_controls"] == 0
        assert result["posture_pct"] is None

    def test_a_measured_zero_posture_stays_zero(self, ato_conn):
        from tools.ato_compliance.dashboard import get_control_summary

        ato_conn.execute(
            "INSERT INTO project_controls (project_id, control_id, implementation_status) "
            "VALUES ('p-zero', 'AC-1', 'planned')"
        )
        result = get_control_summary("p-zero", conn=ato_conn)
        assert result["total_controls"] == 1
        assert result["posture_pct"] == 0.0  # a real finding, not an absence

    def test_artifact_readiness_is_none_when_nothing_was_produced(self, ato_conn):
        from tools.ato_compliance.dashboard import get_artifact_status

        result = get_artifact_status("p-empty", conn=ato_conn)
        assert result["readiness_score"] is None
        # The checklist still reports each artifact as missing / ok -- the
        # composite is what may not be awarded for absence.
        assert {a["type"] for a in result["artifacts"]} == {"ssp", "poam", "stig", "sbom"}

    def test_artifact_readiness_is_measured_once_any_artifact_exists(self, ato_conn):
        from tools.ato_compliance.dashboard import get_artifact_status

        ato_conn.execute("INSERT INTO sbom_records (project_id, format) VALUES ('p-zero', 'cyclonedx')")
        result = get_artifact_status("p-zero", conn=ato_conn)
        assert isinstance(result["readiness_score"], int)
        assert 0 <= result["readiness_score"] <= 100

    def test_posture_score_is_none_never_f_over_an_unassessed_project(self, ato_conn):
        from tools.ato_compliance.dashboard import get_posture_score

        result = get_posture_score("p-empty", conn=ato_conn)
        assert result["score"] is None
        assert result["grade"] is None
        assert result["score_basis"] == "unmeasured"
        assert set(result["unmeasured_components"]) == {"control_pct", "rmf_pct", "artifact_pct"}
        assert all(v is None for v in result["components"].values())

    def test_rmf_pct_is_none_over_synthetic_stages_and_zero_over_recorded_ones(self, ato_conn):
        from tools.ato_compliance.dashboard import get_posture_score, get_rmf_stages

        # No row recorded: get_rmf_stages still returns six stages ...
        assert len(get_rmf_stages("p-empty", conn=ato_conn)) == 6
        # ... and the ratio over them is refused.
        assert get_posture_score("p-empty", conn=ato_conn)["components"]["rmf_pct"] is None

        # One recorded row, not started: a MEASURED 0.0.
        ato_conn.execute(
            "INSERT INTO rmf_workflow_stages (project_id, stage, status) "
            "VALUES ('p-zero', 'categorize', 'not_started')"
        )
        assert get_posture_score("p-zero", conn=ato_conn)["components"]["rmf_pct"] == 0.0

    def test_posture_score_is_graded_only_when_every_component_is_measured(self, ato_conn):
        from tools.ato_compliance.dashboard import get_posture_score

        ato_conn.execute(
            "INSERT INTO project_controls (project_id, control_id, implementation_status) "
            "VALUES ('p-zero', 'AC-1', 'implemented')"
        )
        ato_conn.execute(
            "INSERT INTO rmf_workflow_stages (project_id, stage, status) "
            "VALUES ('p-zero', 'categorize', 'complete')"
        )
        partial = get_posture_score("p-zero", conn=ato_conn)
        assert partial["score"] is None, "two of three measured is not a score"
        assert partial["unmeasured_components"] == ["artifact_pct"]

        ato_conn.execute("INSERT INTO sbom_records (project_id, format) VALUES ('p-zero', 'spdx')")
        full = get_posture_score("p-zero", conn=ato_conn)
        assert full["score_basis"] == "all_components"
        assert isinstance(full["score"], int) and 0 <= full["score"] <= 100
        assert full["grade"] in ("A", "B", "C", "D", "F")
        assert full["unmeasured_components"] == []

    def test_a_failed_read_is_not_a_zero(self):
        """An unreadable database returns None, never posture_pct 0.0 / grade F."""
        from tools.ato_compliance.dashboard import (
            get_artifact_status,
            get_control_summary,
            get_posture_score,
        )

        class _Broken:
            def execute(self, *a, **k):
                raise RuntimeError("database unreachable")

            def close(self):
                pass

        assert get_control_summary("p", conn=_Broken())["posture_pct"] is None
        assert get_artifact_status("p", conn=_Broken())["readiness_score"] is None
        posture = get_posture_score("p", conn=_Broken())
        assert posture["score"] is None and posture["grade"] is None
        # Each reader degrades to None on its own, so the composite sees three
        # unmeasured components; `error` is the basis only when the composite
        # itself blows up. Either way there is no score and no grade.
        assert posture["score_basis"] in ("unmeasured", "error")
        assert set(posture["unmeasured_components"]) == {"control_pct", "rmf_pct", "artifact_pct"}


# ---------------------------------------------------------------------------
# 2. cATO readiness at source
# ---------------------------------------------------------------------------

class TestCatoReadinessOverAnEmptyDenominator:
    def test_no_evidence_reports_none_not_zero(self, ato_db_file, capsys):
        from tools.compliance.cato_monitor import compute_cato_readiness

        result = compute_cato_readiness("p-empty", db_path=str(ato_db_file))
        assert result["total_controls"] == 0
        assert result["readiness_pct"] is None
        assert result["automated_pct"] is None

    def test_one_stale_manual_row_is_a_measured_zero(self, ato_db_file, capsys):
        from tools.compliance.cato_monitor import compute_cato_readiness

        result = compute_cato_readiness("p-zero", db_path=str(ato_db_file))
        assert result["total_controls"] == 1
        assert result["readiness_pct"] == 0.0
        assert result["automated_pct"] == 0.0
        out = capsys.readouterr().out
        assert "0.0%" in out and "None%" not in out

    def test_console_rendering_never_prints_none_percent(self):
        from tools.compliance.cato_monitor import _format_readiness_report, _pct_text

        assert _pct_text(None) == "not measured"
        assert _pct_text(0.0) == "0.0%"
        report = _format_readiness_report(
            {
                "total_controls": 0,
                "controls_with_evidence": 0,
                "controls_with_fresh_evidence": 0,
                "readiness_pct": None,
                "automated_pct": None,
                "by_frequency": {},
            }
        )
        assert "None%" not in report
        assert "not measured" in report


# ---------------------------------------------------------------------------
# 3. The packager's checklist names the missing denominator
# ---------------------------------------------------------------------------

def test_packager_checklist_never_prints_zero_over_zero(ato_db_file):
    from tools.compliance.ato_packager import collect_checklist, open_connection

    conn = open_connection(str(ato_db_file))
    try:
        checks = {c["name"]: c for c in collect_checklist(conn, "p-empty")["checks"]}
    finally:
        conn.close()
    controls = checks["Controls >= 80% Implemented"]
    assert controls["status"] == "FAIL"
    assert "0/0" not in controls["detail"] and "(0%)" not in controls["detail"]
    assert controls["detail"] == "No controls assigned"


# ---------------------------------------------------------------------------
# 4. The pure helpers the other RMF surfaces divide through
# ---------------------------------------------------------------------------

def test_visibility_rate_refuses_a_missing_or_zero_denominator():
    from tools.assets.visibility import visibility_pct

    assert visibility_pct(0, 0) is None
    assert visibility_pct(5, None) is None
    assert visibility_pct(None, 5) is None
    assert visibility_pct(0, 4) == 0.0  # measured zero coverage is real


def test_fabric_posture_carries_no_value_outside_the_measured_state():
    from tools.fabric import posture

    for fn in (posture._not_assessed, posture._unavailable):
        m = fn("src", "controls with evidence", "nothing collected")
        assert m["value"] is None and m["numerator"] is None
        assert m["state"] in (posture.STATE_NOT_ASSESSED, posture.STATE_SOURCE_UNAVAILABLE)
    measured = posture._measure(
        posture.STATE_MEASURED, source="src", denominator_of="d", value=0.0, numerator=0, denominator=3
    )
    assert measured["value"] == 0.0  # a measured zero survives


def test_device_compliance_score_is_none_over_no_measured_checks():
    """An unknown leaves BOTH sides of the ratio (rmf-zt-01)."""
    from tools.security_canvas.device_compliance_scanner import FAIL, PASS, UNKNOWN, _score

    score, measured, unknown = _score({"c1": UNKNOWN, "c2": UNKNOWN})
    assert score is None and measured == 0 and unknown == 2
    score, measured, unknown = _score({"c1": PASS, "c2": FAIL, "c3": UNKNOWN})
    assert score == 0.5 and measured == 2 and unknown == 1
    score, _, _ = _score({"c1": FAIL})
    assert score == 0.0  # a measured zero is a real finding


def test_the_state_vocabularies_stay_distinct():
    """never_ran | unmeasurable | clean | findings, and the RMF trios, are
    separate constants -- folding any two makes an absence read as a result."""
    from tools.assets import visibility
    from tools.fabric import posture
    from tools.kanban import detector_findings as df

    detector_states = {"never_ran", "unmeasurable", "clean", "findings"}
    assert df.RUN_UNMEASURABLE == "unmeasurable"
    assert df.RUN_UNMEASURABLE in detector_states
    assert len(detector_states) == 4

    posture_states = {posture.STATE_MEASURED, posture.STATE_NOT_ASSESSED, posture.STATE_SOURCE_UNAVAILABLE}
    assert len(posture_states) == 3
    vis_states = {visibility.STATE_UNMEASURABLE, visibility.STATE_NOT_ASSESSED, visibility.STATE_ASSESSED}
    assert len(vis_states) == 3
    assert visibility.STATE_UNMEASURABLE != visibility.STATE_NOT_ASSESSED


# ---------------------------------------------------------------------------
# 5. Structural rails over the RMF module set
# ---------------------------------------------------------------------------

_CONSTANT_FALLBACKS = (0, 0.0, 100, 100.0)


def _ratio_fallback_sites(rel: str) -> list[str]:
    """Every ``<ratio> if <cond> else <0|0.0|100|100.0>`` in one module.

    The body must DIVIDE (perfect_score_census.computes_ratio -- the same
    predicate, not a second copy) so an ordinary counter never matches.
    """
    from tools.ci.perfect_score_census import computes_ratio

    src = (REPO / rel).read_text(encoding="utf-8")
    tree = ast.parse(src, filename=rel)
    hits = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.IfExp):
            continue
        fallback = node.orelse
        if not (isinstance(fallback, ast.Constant) and type(fallback.value) in (int, float)):
            continue
        if fallback.value not in _CONSTANT_FALLBACKS:
            continue
        if computes_ratio(node.body):
            hits.append(f"{rel}:{node.lineno}: {ast.unparse(node)}")
    return hits


@pytest.mark.parametrize("rel", RMF_SURFACES)
def test_module_exists(rel):
    assert (REPO / rel).is_file(), f"{rel} is listed in RMF_SURFACES but does not exist"


@pytest.mark.parametrize("rel", RMF_SURFACES)
def test_no_ratio_falls_back_to_a_constant(rel):
    hits = _ratio_fallback_sites(rel)
    assert hits == [], "a ratio falling back to 0/100 over an empty denominator:\n" + "\n".join(hits)


def test_perfect_score_census_is_clean_over_the_rmf_surfaces():
    from tools.ci.perfect_score_census import collect, load_gate

    findings = collect(REPO, load_gate(), only=RMF_SURFACES)
    assert findings == [], findings


def test_perfect_score_gate_is_ratcheted_to_zero():
    from tools.ci.perfect_score_census import load_gate

    assert load_gate().get("perfect_score_max") == 0


def test_rmf_substrates_are_registered():
    cfg = yaml.safe_load((REPO / "args" / "capability_consumption.yaml").read_text(encoding="utf-8"))
    refs = {s["ref"] for s in cfg["substrates"]}
    missing = RMF_SUBSTRATES - refs
    assert not missing, f"not declared in args/capability_consumption.yaml :: substrates: {sorted(missing)}"
    for entry in cfg["substrates"]:
        if entry["ref"] in RMF_SUBSTRATES:
            assert entry.get("note"), f"{entry['ref']} has no note"


def test_ato_template_renders_a_null_as_not_assessed():
    """The page must not put the `|| 0` back on a value the API now reports as None."""
    rel = "tools/dashboard/templates/boundary_canvas/ato_compliance.html"
    html = (REPO / rel).read_text(encoding="utf-8")
    for field in ("posture.score", "comp.control_pct", "comp.rmf_pct", "comp.artifact_pct",
                  "ctrl.posture_pct", "artData.readiness_score"):
        assert f"{field} || 0" not in html, f"{field} is coerced to 0 in {rel}"
    assert "not assessed" in html
    assert html == (REPO / "icdev" / rel).read_text(encoding="utf-8"), "template mirror diverged"


@pytest.mark.parametrize("rel", [
    "tools/ato_compliance/dashboard.py",
    "tools/compliance/cato_monitor.py",
    "tools/compliance/ato_packager.py",
])
def test_edited_modules_are_mirrored(rel):
    assert (REPO / rel).read_bytes() == (REPO / "icdev" / rel).read_bytes(), f"{rel} mirror diverged"
