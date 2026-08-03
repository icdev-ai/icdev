# CUI // SP-CTI
"""Tests for the component dimension extractors (idp-score-01).

The acceptance criterion for this module is a single invariant, and it is the
reason the module exists: **every extractor returns NOT_ASSESSED when its
source data is empty or missing** — never a score of 0.

That distinction is load-bearing rather than cosmetic. Both upstream sources
hand back something shaped exactly like a measured failure when they mean "no
data": ``compute_canvas_posture`` scores a canvas with an empty assessment
table as ``0.0``, and ``validate_canvas_completeness`` returns ``passed=False``
for a key that is not a canvas at all. A scorer that believed either one would
publish a confident zero for something nothing ever measured.

Every test here injects its source data directly, so none of them touch the
database, the registry or the filesystem.
"""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

from tools.quality.component_scorer import (
    ASSESSED,
    DIMENSIONS,
    NOT_ASSESSED,
    DimensionScore,
    ScorecardPersistError,
    aggregate_score,
    extract_coherence,
    extract_compliance,
    extract_completeness,
    extract_health,
    letter_grade,
    persist_scorecard,
    score_component,
)

CANVAS = {
    "key": "network",
    "display_name": "Network",
    "kind": "canvas",
    "url_prefix": "/network",
    "module": "tools.network.blueprint",
}


# ---------------------------------------------------------------------------
# The acceptance criterion: empty/missing source -> NOT_ASSESSED, never 0
# ---------------------------------------------------------------------------


def _empty_source_cases():
    """One (name, callable) per extractor, each given a deliberately empty source."""
    return [
        # No probe rows and no canvas-health row.
        ("health", lambda: extract_health(CANVAS, evidence=[], canvas_health=[])),
        # Posture engine returned no rows at all.
        ("compliance", lambda: extract_compliance(CANVAS, posture=[])),
        # A report with no checks in it.
        ("coherence", lambda: extract_coherence(CANVAS, report={"checks": []})),
        # A completeness report carrying no items.
        ("completeness", lambda: extract_completeness(CANVAS, report={"items": []})),
    ]


def _missing_source_cases():
    """One (name, callable) per extractor, each with the source entirely absent."""
    return [
        ("health", lambda: extract_health({"key": "nope"}, evidence=[], canvas_health=[])),
        # No connection and no injected rows -> the engine was never consulted.
        ("compliance", lambda: extract_compliance(CANVAS, conn=None, posture=None)),
        ("coherence", lambda: extract_coherence(CANVAS, report=None)),
        ("completeness", lambda: extract_completeness(CANVAS, report={})),
    ]


@pytest.mark.parametrize("name,call", _empty_source_cases(), ids=lambda v: getattr(v, "__name__", v))
def test_extractor_returns_not_assessed_when_source_is_empty(name, call):
    """ACCEPTANCE: an empty source yields NOT_ASSESSED with score None."""
    result = call()
    assert result.dimension == name
    assert result.status == NOT_ASSESSED, f"{name}: empty source must not be assessed"
    assert result.score is None, f"{name}: unassessed must have no score, got {result.score}"
    assert result.score != 0, f"{name}: an unassessed dimension must never read as a measured 0"
    assert result.evidence_count == 0
    assert result.assessed is False
    assert result.reason, f"{name}: must say why it is unassessed"


@pytest.mark.parametrize("name,call", _missing_source_cases(), ids=lambda v: getattr(v, "__name__", v))
def test_extractor_returns_not_assessed_when_source_is_missing(name, call):
    """ACCEPTANCE: a missing source yields NOT_ASSESSED with score None."""
    result = call()
    assert result.dimension == name
    assert result.status == NOT_ASSESSED
    assert result.score is None
    assert result.evidence_count == 0
    assert result.reason


def test_every_declared_dimension_is_covered_by_the_acceptance_test():
    """The parametrized cases must cover all four dimensions, not three of them."""
    covered = {name for name, _ in _empty_source_cases()}
    assert covered == set(DIMENSIONS)
    assert {name for name, _ in _missing_source_cases()} == set(DIMENSIONS)


# ---------------------------------------------------------------------------
# The type-level invariant
# ---------------------------------------------------------------------------


def test_measured_with_zero_evidence_downgrades_to_not_assessed():
    """A score with no evidence behind it cannot be constructed at all."""
    result = DimensionScore.measured("health", 0.0, evidence_count=0)
    assert result.status == NOT_ASSESSED
    assert result.score is None


def test_measured_with_evidence_is_assessed():
    result = DimensionScore.measured("health", 87.5, evidence_count=4)
    assert result.status == ASSESSED
    assert result.assessed is True
    assert result.score == 87.5
    assert result.evidence_count == 4


def test_a_real_measured_zero_is_preserved():
    """0% with evidence behind it is a genuine failure and must survive."""
    result = DimensionScore.measured("health", 0.0, evidence_count=3)
    assert result.status == ASSESSED
    assert result.score == 0.0


# ---------------------------------------------------------------------------
# The two disguised-absence traps
# ---------------------------------------------------------------------------


def test_posture_row_scoring_zero_with_no_findings_is_not_assessed():
    """compute_canvas_posture scores an empty assessment table as 0.0.

    That row means "never assessed", and the posture engine itself excludes it
    from its own average (``if score > 0``). Scoring it as 0% here would invent
    a compliance failure out of a missing table.
    """
    result = extract_compliance(
        CANVAS,
        posture=[{"name": "Network", "score": 0.0, "open_findings": 0, "closed_findings": 0}],
    )
    assert result.status == NOT_ASSESSED
    assert result.score is None


def test_posture_row_scoring_zero_with_findings_is_a_real_failure():
    """The same 0 *with* findings is measured, and must be scored as 0."""
    result = extract_compliance(
        CANVAS,
        posture=[{"name": "Network", "score": 0.0, "open_findings": 7, "closed_findings": 0}],
    )
    assert result.status == ASSESSED
    assert result.score == 0.0
    assert result.evidence_count == 7


def test_posture_matches_a_canvas_whose_key_looks_nothing_like_the_row_name():
    """The registry calls it 'ndc'; the posture engine calls it 'Network'.

    Measured against the live registry: every canvas key (ndc, sdc, idc, …) is
    an acronym the posture row name never contains, so name matching alone
    matched nothing at all and the compliance dimension was unassessed for
    every component in the platform. The module package both sides agree on is
    the join.
    """
    ndc = {
        "key": "ndc",
        "display_name": "Network Design Canvas",
        "kind": "canvas",
        "url_prefix": "/network",
        "module": "tools.network.blueprint",
    }
    result = extract_compliance(
        ndc,
        posture=[
            {"name": "Security", "score": 10.0, "open_findings": 1, "closed_findings": 0},
            {"name": "Network", "score": 91.0, "open_findings": 1, "closed_findings": 9},
        ],
    )
    assert result.status == ASSESSED
    assert result.score == 91.0
    assert result.detail["matched_name"] == "Network"


def test_posture_flags_a_score_no_finding_backs():
    """A subtraction-style canvas with an empty table scores a vacuous 100.

    Measured 2026-08-03: mc_assessments held 0 rows and compute_canvas_posture
    reported Migration at 100.0. This extractor cannot tell that apart from a
    genuinely clean canvas, so it must not hide the ambiguity.
    """
    result = extract_compliance(
        {"key": "mdc", "display_name": "Migration", "module": "tools.migration_canvas.blueprint"},
        posture=[{"name": "Migration", "score": 100.0, "open_findings": 0, "closed_findings": 0}],
    )
    assert result.status == ASSESSED
    assert result.detail["findings_backed"] is False


def test_posture_marks_a_score_findings_do_back():
    result = extract_compliance(
        CANVAS,
        posture=[{"name": "Network", "score": 90.0, "open_findings": 1, "closed_findings": 9}],
    )
    assert result.detail["findings_backed"] is True


def test_posture_falls_back_to_name_for_rows_outside_the_module_map():
    """'GovLift' / 'Zero Trust' / 'AI-ify' have no _CANVAS_MODULES entry."""
    result = extract_compliance(
        {"key": "govlift", "display_name": "GovLift"},
        posture=[{"name": "GovLift", "score": 75.0, "open_findings": 5, "closed_findings": 15}],
    )
    assert result.status == ASSESSED
    assert result.score == 75.0


def test_normalized_name_drops_design_canvas_noise():
    from tools.quality.component_scorer import _normalize_name

    assert _normalize_name("Network Design Canvas") == "network"
    assert _normalize_name("security_canvas") == "security"
    assert _normalize_name("AI/ML") == "ai ml"


def test_not_a_canvas_sentinel_is_not_assessed_rather_than_zero():
    """validate_canvas_completeness reports non-canvases as passed=False.

    That is absence of applicability, not a failed gate — a child app is not a
    canvas scoring 0 on the canvas gate.
    """
    report = {
        "key": "some_child_app",
        "passed": False,
        "items": [
            {
                "point": "registered",
                "required": True,
                "present": False,
                "path": None,
                "message": "Component 'some_child_app' is kind=child_app, not canvas",
            }
        ],
    }
    result = extract_completeness({"key": "some_child_app"}, report=report)
    assert result.status == NOT_ASSESSED
    assert result.score is None
    assert "not canvas" in result.reason


def test_coherence_does_not_credit_a_component_no_check_examined():
    """A sweep that never named the component says nothing about it.

    Crediting "no check mentioned it" as 100% is the vacuous-truth failure that
    handed 30 of 67 components a top ladder rung on the scorecard side.
    """
    report = {
        "checks": [
            {
                "check_id": "karpathy_sync",
                "status": "pass",
                "expected": ["tools/other/thing.py"],
                "actual": ["tools/other/thing.py"],
                "missing": [],
                "extra": [],
            }
        ]
    }
    result = extract_coherence(CANVAS, report=report)
    assert result.status == NOT_ASSESSED
    assert result.score is None


# ---------------------------------------------------------------------------
# Positive paths — the extractors must still measure when data exists
# ---------------------------------------------------------------------------


def test_health_scores_probe_rows_with_warn_at_half_credit():
    result = extract_health(
        CANVAS,
        evidence=[
            {"route": "/network", "status": "pass"},
            {"route": "/network/a", "status": "warn"},
            {"route": "/network/b", "status": "fail"},
            {"route": "/network/c", "status": "pass"},
        ],
    )
    assert result.status == ASSESSED
    assert result.evidence_count == 4
    # (1.0 + 0.5 + 0.0 + 1.0) / 4 = 62.5%
    assert result.score == 62.5


def test_health_falls_back_to_canvas_health_when_nothing_probed():
    result = extract_health(
        CANVAS,
        evidence=[],
        canvas_health=[{"key": "network", "status": "amber", "issues": ["no_e2e"]}],
    )
    assert result.status == ASSESSED
    assert result.score == 60.0
    assert result.source == "canvas_health.health_data"


def test_health_ignores_an_unknown_probe_status_rather_than_crediting_it():
    """An unrecognised status must not become free credit."""
    result = extract_health(CANVAS, evidence=[{"route": "/network", "status": "banana"}])
    assert result.status == NOT_ASSESSED


def test_coherence_scores_only_the_checks_naming_the_component():
    report = {
        "checks": [
            {
                "check_id": "relevant_pass",
                "status": "pass",
                "expected": ["tools/network/blueprint.py"],
                "actual": [],
                "missing": [],
                "extra": [],
            },
            {
                "check_id": "relevant_fail",
                "status": "fail",
                "expected": [],
                "actual": [],
                "missing": ["tools/network/db/init_db.py"],
                "extra": [],
            },
            {
                "check_id": "unrelated",
                "status": "fail",
                "expected": [],
                "actual": [],
                "missing": ["tools/metadata/other.py"],
                "extra": [],
            },
        ]
    }
    result = extract_coherence(CANVAS, report=report)
    assert result.status == ASSESSED
    assert result.evidence_count == 2, "the unrelated check must not be counted"
    assert result.score == 50.0
    assert "unrelated" not in result.detail["relevant_checks"]


def test_coherence_matching_is_segment_wise_not_substring():
    """Key 'data' must not match 'tools/metadata/...'."""
    report = {
        "checks": [
            {
                "check_id": "metadata_check",
                "status": "fail",
                "expected": ["tools/metadata/loader.py"],
                "actual": [],
                "missing": [],
                "extra": [],
            }
        ]
    }
    result = extract_coherence({"key": "data"}, report=report)
    assert result.status == NOT_ASSESSED


def test_completeness_scores_required_points_only():
    report = {
        "items": [
            {"point": "template", "required": True, "present": True},
            {"point": "blueprint", "required": True, "present": True},
            {"point": "constants", "required": True, "present": False},
            {"point": "migration", "required": True, "present": False},
            {"point": "nice_to_have", "required": False, "present": False},
        ]
    }
    result = extract_completeness(CANVAS, report=report)
    assert result.status == ASSESSED
    assert result.evidence_count == 4, "only required points count"
    assert result.score == 50.0
    assert sorted(result.detail["missing_points"]) == ["constants", "migration"]


# ---------------------------------------------------------------------------
# Aggregation
# ---------------------------------------------------------------------------


def test_score_component_reports_overall_none_when_nothing_assessed():
    """A component nothing measured has no overall score — not a zero."""
    result = score_component(
        dict(CANVAS),
        conn=None,
        posture=[],
        canvas_health=[],
        evidence=[],
        coherence_report={"checks": []},
        repo_root="/nonexistent-repo-root",
    )
    assert result["overall"] is None
    assert result["assessed"] is False
    assert result["assessed_dimensions"] == 0
    # Unassessed dimensions are still reported, with their reasons.
    assert set(result["dimensions"]) == set(DIMENSIONS)
    for name, dim in result["dimensions"].items():
        assert dim["status"] == NOT_ASSESSED, name
        assert dim["score"] is None, name
        assert dim["reason"], name


def test_score_component_averages_only_assessed_dimensions():
    """An unassessed dimension must not drag the average down as a zero."""
    result = score_component(
        dict(CANVAS),
        conn=None,
        posture=[{"name": "Network", "score": 80.0, "open_findings": 2, "closed_findings": 8}],
        canvas_health=[],
        evidence=[{"route": "/network", "status": "pass"}],
        coherence_report={"checks": []},
        repo_root="/nonexistent-repo-root",
    )
    # health = 100, compliance = 80, coherence + completeness unassessed.
    assert result["assessed_dimensions"] == 2
    assert result["overall"] == 90.0


# ---------------------------------------------------------------------------
# Aggregation — the honesty rule (idp-score-01-d3)
# ---------------------------------------------------------------------------


def _measured(name, score, evidence=3):
    return DimensionScore.measured(name, score, evidence, source=f"src.{name}")


def _all_assessed(health=100.0, compliance=80.0, coherence=90.0, completeness=70.0):
    """Four measured dimensions — the only shape that earns an overall score."""
    return {
        "health": _measured("health", health, 4),
        "compliance": _measured("compliance", compliance, 10),
        "coherence": _measured("coherence", coherence, 6),
        "completeness": _measured("completeness", completeness, 8),
    }


def test_aggregate_score_grades_a_fully_assessed_component():
    """All four measured: weighted mean, banded to a letter."""
    result = aggregate_score(_all_assessed())
    # Uniform default weights: (100 + 80 + 90 + 70) / 4 = 85.0
    assert result["overall_score"] == 85.0
    assert result["letter_grade"] == "B"
    assert result["status"] == ASSESSED
    assert result["unassessed_dimensions"] == []
    assert result["assessed_dimensions"] == 4
    # Evidence counts survive into the details, per dimension.
    assert result["evidence_count"] == 4 + 10 + 6 + 8
    assert result["dimension_details"]["compliance"]["evidence_count"] == 10
    assert result["dimension_details"]["compliance"]["source"] == "src.compliance"


@pytest.mark.parametrize(
    ("score", "grade"),
    [(95.0, "A"), (90.0, "A"), (85.0, "B"), (75.0, "C"), (65.0, "D"), (10.0, "F"), (0.0, "F")],
)
def test_letter_grade_bands_match_the_check_constraint(score, grade):
    """Only A-F — the letters developer_scorecards.letter_grade admits."""
    assert letter_grade(score) == grade


def test_letter_grade_of_nothing_is_nothing():
    """None in, None out. An "F" here would turn unmeasured into failed."""
    assert letter_grade(None) is None


@pytest.mark.parametrize("missing", list(DIMENSIONS))
def test_any_unassessed_dimension_caps_the_overall_score(missing):
    """THE honesty rule: one unassessed dimension and there is no score.

    Parametrized over all four so no dimension is quietly exempt — the
    temptation is always to let "just this one" be optional.
    """
    dimensions = _all_assessed()
    dimensions[missing] = DimensionScore.unassessed(missing, reason="nothing measured it")

    result = aggregate_score(dimensions)

    assert result["overall_score"] is None
    assert result["letter_grade"] is None
    assert result["status"] == NOT_ASSESSED
    assert result["unassessed_dimensions"] == [missing]
    assert missing in result["reason"]
    # The other three are still reported with their evidence — the score is
    # withheld, the measurements are not.
    assert result["assessed_dimensions"] == 3
    for name in DIMENSIONS:
        if name != missing:
            assert result["dimension_details"][name]["assessed"] is True


def test_a_dimension_absent_from_the_dict_is_unassessed_not_ignored():
    """A key that was never computed must not be silently dropped."""
    dimensions = _all_assessed()
    del dimensions["coherence"]

    result = aggregate_score(dimensions)

    assert result["overall_score"] is None
    assert result["unassessed_dimensions"] == ["coherence"]
    assert result["dimension_details"]["coherence"]["status"] == NOT_ASSESSED


def test_aggregate_score_honours_custom_weights():
    """Weights are normalized, so a caller need not do the arithmetic."""
    result = aggregate_score(
        _all_assessed(health=100.0, compliance=0.0, coherence=0.0, completeness=0.0),
        # Unnormalized on purpose: 3 + 1 + 1 + 1 = 6, so health is 0.5.
        weights={"health": 3, "compliance": 1, "coherence": 1, "completeness": 1},
    )
    assert result["overall_score"] == 50.0
    assert result["weights"]["health"] == 0.5


def test_aggregate_score_accepts_serialized_dimensions():
    """A round-tripped result aggregates the same as the live objects."""
    live = _all_assessed()
    serialized = {name: dim.to_dict() for name, dim in live.items()}
    assert aggregate_score(serialized) == aggregate_score(live)


def test_zero_weights_fall_back_to_uniform_rather_than_dividing_by_zero():
    result = aggregate_score(_all_assessed(), weights=dict.fromkeys(DIMENSIONS, 0))
    assert result["overall_score"] == 85.0


def test_score_component_exposes_the_aggregate():
    """The end-to-end path: nothing assessed, so no score and no grade."""
    result = score_component(
        dict(CANVAS),
        conn=None,
        posture=[],
        canvas_health=[],
        evidence=[],
        coherence_report=None,
        repo_root="/nonexistent-repo-root",
    )
    assert result["aggregate"]["overall_score"] is None
    assert result["aggregate"]["letter_grade"] is None
    assert sorted(result["aggregate"]["unassessed_dimensions"]) == sorted(DIMENSIONS)


# ---------------------------------------------------------------------------
# Persistence — developer_scorecards (idp-score-01-d3)
# ---------------------------------------------------------------------------

# The table exactly as tools/db/init_icdev_db.py creates it — i.e. the shape
# every deployed database had BEFORE migration 20260802145147. The migration is
# then applied on top, so these tests exercise the real upgrade path rather than
# a convenient hand-built table. That matters more than usual here: the bug this
# task is most exposed to is an INSERT naming a column the live schema lacks,
# which raises, gets swallowed by a caller, and reports success while writing
# nothing.
#
# ``projects`` comes along because ``developer_scorecards`` references it and
# ``tools/db/storage.py`` opens SQLite with ``PRAGMA foreign_keys=ON``. Without
# it the migration's SQLite table rebuild fails on "no such table:
# main.projects" — which is a property of the test database, not of the
# migration, since every real database has the table.
_PROJECTS_DDL = """
CREATE TABLE IF NOT EXISTS projects (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    directory_path TEXT NOT NULL
)
"""

_PRE_MIGRATION_DDL = """
CREATE TABLE IF NOT EXISTS developer_scorecards (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL REFERENCES projects(id),
    actor TEXT,
    overall_score REAL NOT NULL,
    letter_grade TEXT NOT NULL CHECK(letter_grade IN ('A','B','C','D','F')),
    code_quality_score REAL,
    security_score REAL,
    compliance_score REAL,
    test_coverage_score REAL,
    velocity_score REAL,
    dimension_details TEXT,
    classification TEXT DEFAULT 'CUI // SP-CTI',
    created_at TEXT DEFAULT (datetime('now'))
)
"""

MIGRATION_DIR = (
    Path(__file__).resolve().parent.parent
    / "tools"
    / "db"
    / "migrations"
    / "20260802145147_scorecard_component_id"
)


def _load_migration(name: str):
    """Import the shipped migration by path (its dir name is not an identifier)."""
    spec = importlib.util.spec_from_file_location(
        f"_mig_scorecard_{name}", MIGRATION_DIR / f"{name}.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def conn(tmp_path, monkeypatch):
    """Storage connection over a temp SQLite DB, migrated to the component grain.

    ``get_connection`` rather than raw ``sqlite3``: the module writes ``%s``
    placeholders and only the storage wrapper translates them to ``?``. A raw
    connection here would make these tests assert their own no-op.
    """
    monkeypatch.setenv("ICDEV_STORAGE_BACKEND", "sqlite")
    from tools.db.storage import get_connection

    connection = get_connection(db_path=str(tmp_path / "scorecards.db"))
    connection.execute(_PROJECTS_DDL)
    connection.execute(_PRE_MIGRATION_DDL)
    connection.commit()
    _load_migration("up").up(connection)
    yield connection
    try:
        connection.close()
    except Exception:  # noqa: BLE001
        pass


def _mock_component(key="mock-canvas", **overrides):
    """A scored result for a component that need not exist in the registry."""
    result = {
        "key": key,
        "display_name": "Mock Canvas",
        "kind": "canvas",
        "route": "/mock",
        "dimensions": {name: dim.to_dict() for name, dim in _all_assessed().items()},
        "aggregate": aggregate_score(_all_assessed()),
    }
    result.update(overrides)
    return result


def _select_all(conn, component_id):
    """SELECT * — the acceptance criterion in its own words."""
    cursor = conn.execute(
        "SELECT * FROM developer_scorecards WHERE component_id = %s", (component_id,)
    )
    columns = [d[0] for d in cursor.description]
    return [dict(zip(columns, row)) for row in cursor.fetchall()]


def test_persist_scorecard_writes_a_row_with_the_right_grade(conn):
    """THE acceptance criterion, end to end.

    Insert a scorecard for a mock component; SELECT * returns the correct
    letter grade and a dimension_details payload carrying per-source evidence
    counts.
    """
    outcome = persist_scorecard(_mock_component(), conn=conn)
    assert outcome["action"] == "insert"

    rows = _select_all(conn, "mock-canvas")
    assert len(rows) == 1
    row = rows[0]

    # (100 + 80 + 90 + 70) / 4 = 85.0 -> B
    assert row["overall_score"] == 85.0
    assert row["letter_grade"] == "B"
    assert row["component_id"] == "mock-canvas"
    assert row["evaluated_at"]
    assert row["classification"] == "CUI // SP-CTI"

    details = json.loads(row["dimension_details"])
    assert set(details["dimensions"]) == set(DIMENSIONS)
    assert details["dimensions"]["compliance"]["evidence_count"] == 10
    assert details["dimensions"]["health"]["evidence_count"] == 4
    assert details["dimensions"]["coherence"]["source"] == "src.coherence"
    assert details["evidence_count"] == 28
    assert details["component"]["key"] == "mock-canvas"

    # The one legacy column that genuinely means the same thing is populated;
    # the four that do not are left NULL rather than filled with a lookalike.
    assert row["compliance_score"] == 80.0
    for column in (
        "code_quality_score",
        "security_score",
        "test_coverage_score",
        "velocity_score",
    ):
        assert row[column] is None, column


def test_persist_scorecard_upserts_rather_than_duplicating(conn):
    """Re-scoring a component updates its row: one component, one standing."""
    persist_scorecard(_mock_component(), conn=conn)

    improved = _mock_component(
        aggregate=aggregate_score(
            _all_assessed(health=100.0, compliance=95.0, coherence=95.0, completeness=90.0)
        )
    )
    outcome = persist_scorecard(improved, conn=conn)

    assert outcome["action"] == "update"
    rows = _select_all(conn, "mock-canvas")
    assert len(rows) == 1
    assert rows[0]["overall_score"] == 95.0
    assert rows[0]["letter_grade"] == "A"


def test_an_unassessed_component_persists_as_null_not_as_f(conn):
    """The row that says "we looked and could not measure it".

    NULL score and NULL grade — not 0.0 and not "F", which are findings.
    """
    dimensions = _all_assessed()
    dimensions["coherence"] = DimensionScore.unassessed("coherence", reason="no check names it")
    result = _mock_component(key="unmeasured", aggregate=aggregate_score(dimensions))

    outcome = persist_scorecard(result, conn=conn)

    assert outcome["overall_score"] is None
    row = _select_all(conn, "unmeasured")[0]
    assert row["overall_score"] is None
    assert row["letter_grade"] is None
    # Why it is unassessed rides on the row, so the gap is actionable.
    details = json.loads(row["dimension_details"])
    assert details["unassessed_dimensions"] == ["coherence"]
    assert "coherence" in details["reason"]


def test_persist_scorecard_refuses_a_schema_without_the_component_grain(tmp_path, monkeypatch):
    """A pre-migration table must raise, not swallow and report success."""
    monkeypatch.setenv("ICDEV_STORAGE_BACKEND", "sqlite")
    from tools.db.storage import get_connection

    connection = get_connection(db_path=str(tmp_path / "premigration.db"))
    connection.execute(_PROJECTS_DDL)
    connection.execute(_PRE_MIGRATION_DDL)
    connection.commit()
    try:
        with pytest.raises(ScorecardPersistError, match="component_id"):
            persist_scorecard(_mock_component(), conn=connection)
        # And nothing was written under the illusion that it had worked.
        count = connection.execute("SELECT COUNT(*) FROM developer_scorecards").fetchone()[0]
        assert count == 0
    finally:
        connection.close()


def test_persist_scorecard_refuses_a_result_with_no_component_key(conn):
    with pytest.raises(ScorecardPersistError, match="no component key"):
        persist_scorecard({"key": "", "dimensions": {}}, conn=conn)


def test_persist_all_records_every_component_and_survives_one_failure(conn, monkeypatch):
    """A sweep persists each key; a component that raises is reported, not fatal."""
    import tools.quality.component_scorer as scorer

    def fake_score(key, **_kwargs):
        if key == "explodes":
            raise RuntimeError("canvas module will not import")
        return _mock_component(key=key)

    monkeypatch.setattr(scorer, "score_component", fake_score)

    summary = scorer.persist_all(conn=conn, keys=["alpha", "explodes", "bravo"])

    assert [r["component_id"] for r in summary["persisted"]] == ["alpha", "bravo"]
    assert summary["errors"][0]["component_id"] == "explodes"
    assert summary["assessed"] == 2
    # One shared timestamp, so a sweep's rows sort together on the index.
    stamps = {_select_all(conn, k)[0]["evaluated_at"] for k in ("alpha", "bravo")}
    assert stamps == {summary["evaluated_at"]}


def test_the_whole_path_from_raw_sources_to_a_persisted_grade(conn):
    """Real extractors -> aggregate -> row, with nothing hand-built in between.

    Every other persistence test above starts from a constructed ``dimensions``
    dict, which proves the aggregation and the write but not the join between
    them: an extractor could change the shape it returns and those tests would
    still pass. This one feeds the four raw sources in and reads the graded row
    back out, so the whole chain is under test.

    All four sources are injected — including ``completeness_report``, which is
    why that parameter exists. Without it a fully-assessed component could not
    be produced without the filesystem and the registry, and the end-to-end
    case would be untestable.

      health        1 passing probe                       -> 100.0
      compliance    posture 80.0 backed by 2 + 8 findings ->  80.0
      coherence     one passing + one failing check       ->  50.0
      completeness  4 required points, all present        -> 100.0

    Uniform weights: (100 + 80 + 50 + 100) / 4 = 82.5 -> B.
    """
    result = score_component(
        {
            "key": "mockc",
            "display_name": "Mock",
            "kind": "canvas",
            "url_prefix": "/mockc",
            "module": "tools.mockc.blueprint",
        },
        conn=None,
        evidence=[{"route": "/mockc", "status": "pass"}],
        posture=[
            {"name": "Mock", "score": 80.0, "open_findings": 2, "closed_findings": 8}
        ],
        coherence_report={
            "checks": [
                {
                    "check_id": "mirror_parity",
                    "status": "pass",
                    "expected": ["tools/mockc/blueprint.py"],
                },
                {
                    "check_id": "completeness_gate",
                    "status": "fail",
                    "missing": ["tools/mockc/constants.py"],
                },
            ]
        },
        completeness_report={
            "items": [
                {"point": "template", "required": True, "present": True},
                {"point": "route", "required": True, "present": True},
                {"point": "module", "required": True, "present": True},
                {"point": "iqe", "required": True, "present": True},
            ]
        },
    )

    persist_scorecard(result, conn=conn)

    row = _select_all(conn, "mockc")[0]
    assert row["overall_score"] == 82.5
    assert row["letter_grade"] == "B"
    assert row["compliance_score"] == 80.0
    assert row["project_id"] is None, "a component scorecard has no project"

    # The evidence counts are the extractors' own, not a fixture's: one probe,
    # ten findings, two coherence checks, four required completeness points.
    details = json.loads(row["dimension_details"])
    assert {
        name: dim["evidence_count"] for name, dim in details["dimensions"].items()
    } == {"health": 1, "compliance": 10, "coherence": 2, "completeness": 4}
    # And each names the subsystem it came from, so a reader can go check.
    assert details["dimensions"]["health"]["source"] == "awareness_component_health"
    assert details["dimensions"]["compliance"]["source"] == "canvas_compliance.posture"
