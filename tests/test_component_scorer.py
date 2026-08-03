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

import pytest

from tools.quality.component_scorer import (
    ASSESSED,
    DIMENSIONS,
    NOT_ASSESSED,
    DimensionScore,
    extract_coherence,
    extract_compliance,
    extract_completeness,
    extract_health,
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
