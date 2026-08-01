"""Regression tests for nav-comp-01 and nav-comp-04.

nav-comp-01 — `_calculate_readiness_score` returned 100.0 / "Ready for
Assessment" when every control was marked not_applicable (scoreable == 0),
presenting a system with ZERO scoreable controls as fully ready for FedRAMP
assessment. The fix returns 0.0 / "Insufficient Scope" and surfaces the state
in the report.

nav-comp-04 — the same trap in the PER-FAMILY table: `_calculate_family_scores`
scored a family whose controls are ALL not_applicable as 100.0 (reads as fully
satisfied). The fix marks such families not_scoreable with score None so the
table/detail render "N/A" and rollups exclude them.

These are pure-function unit tests (no DB) so they run in any worktree.
"""

import importlib

import pytest

# Import via the canonical namespace; also exercise the legacy shim path below.
frg = importlib.import_module("tools.compliance.fedramp_report_generator")


def _mk(status, control_id="AC-1"):
    return {"control_id": control_id, "status": status}


class TestReadinessScoreScope:
    def test_all_not_applicable_is_insufficient_scope(self):
        """Every control N/A -> 0.0 / Insufficient Scope (was 100.0 / Ready)."""
        assessments = [
            _mk("not_applicable", "AC-1"),
            _mk("not_applicable", "AC-2"),
            _mk("not_applicable", "IA-2"),
        ]
        score, level = frg._calculate_readiness_score(assessments)
        assert score == 0.0
        assert level == "Insufficient Scope"

    def test_empty_assessments_still_not_ready(self):
        """No controls at all remains the distinct 'Not Ready' early return."""
        score, level = frg._calculate_readiness_score([])
        assert score == 0.0
        assert level == "Not Ready"

    def test_mixed_set_math_unchanged(self):
        """Regression: hand-computed score for a mixed control set.

        total=10, not_applicable=2 -> scoreable=8
        satisfied=5, risk_accepted=1
        score = 100 * (5 + 1*0.75) / 8 = 71.875 -> 71.9 -> Conditionally Ready
        """
        assessments = (
            [_mk("satisfied", f"AC-{i}") for i in range(5)]
            + [_mk("other_than_satisfied", f"AU-{i}") for i in range(2)]
            + [_mk("risk_accepted", "SC-7")]
            + [_mk("not_applicable", f"CM-{i}") for i in range(2)]
        )
        score, level = frg._calculate_readiness_score(assessments)
        assert score == 71.9
        assert level == "Conditionally Ready"

    def test_all_satisfied_is_ready(self):
        """Sanity: fully satisfied scoreable set is still Ready for Assessment."""
        assessments = [_mk("satisfied", f"AC-{i}") for i in range(5)]
        score, level = frg._calculate_readiness_score(assessments)
        assert score == 100.0
        assert level == "Ready for Assessment"


class TestConsumersRenderInsufficientScope:
    def test_gate_fails_on_zero_scope(self):
        """Zero-scope readiness (0.0) must FAIL the readiness gate."""
        assessments = [_mk("not_applicable", "AC-1"), _mk("not_applicable", "AC-2")]
        family_scores = frg._calculate_family_scores(assessments)
        score, _level = frg._calculate_readiness_score(assessments)
        gate = frg._evaluate_gate(assessments, score, family_scores)
        assert gate["readiness_gate"] == "FAIL"
        assert gate["gate_result"] == "FAIL"

    def test_executive_summary_explains_insufficient_scope(self):
        """The report artifact must explain why readiness can't be assessed."""
        assessments = [
            _mk("not_applicable", "AC-1"),
            _mk("not_applicable", "AC-2"),
            _mk("not_applicable", "IA-2"),
        ]
        family_scores = frg._calculate_family_scores(assessments)
        score, level = frg._calculate_readiness_score(assessments)
        gate = frg._evaluate_gate(assessments, score, family_scores)
        summary = frg._build_executive_summary(score, level, gate, assessments, family_scores)
        assert isinstance(summary, str) and summary
        assert "Insufficient Scope" in summary
        assert "no scoreable controls" in summary.lower()
        # Must NOT claim readiness in the zero-scope case.
        assert "Ready for Assessment" not in summary

    def test_executive_summary_normal_case_unchanged(self):
        """A normal mixed set renders without the insufficient-scope note."""
        assessments = (
            [_mk("satisfied", f"AC-{i}") for i in range(5)]
            + [_mk("other_than_satisfied", "AU-2")]
        )
        family_scores = frg._calculate_family_scores(assessments)
        score, level = frg._calculate_readiness_score(assessments)
        gate = frg._evaluate_gate(assessments, score, family_scores)
        summary = frg._build_executive_summary(score, level, gate, assessments, family_scores)
        assert "no scoreable controls" not in summary.lower()


class TestFamilyScoreScope:
    """nav-comp-04: a family whose controls are ALL not_applicable must not
    render as 100% (fully satisfied) in the per-family table. It is N/A."""

    def test_all_na_family_is_not_scoreable(self):
        """All-N/A family -> score None + not_scoreable flag (was 100.0)."""
        assessments = [
            _mk("not_applicable", "AC-1"),
            _mk("not_applicable", "AC-2"),
            _mk("satisfied", "AU-2"),
            _mk("satisfied", "AU-3"),
        ]
        fs = frg._calculate_family_scores(assessments)
        # The all-N/A family is flagged and carries no numeric score.
        assert fs["AC"]["score"] is None
        assert fs["AC"]["not_scoreable"] is True
        assert fs["AC"]["total"] == 2
        assert fs["AC"]["not_applicable"] == 2
        # A genuinely satisfied family is unaffected.
        assert fs["AU"]["score"] == 100.0
        assert fs["AU"]["not_scoreable"] is False

    def test_all_na_family_renders_na_in_table(self):
        """The per-family markdown table shows N/A, never 100.0% for all-N/A."""
        assessments = [
            _mk("not_applicable", "AC-1"),
            _mk("not_applicable", "AC-2"),
            _mk("satisfied", "AU-2"),
        ]
        fs = frg._calculate_family_scores(assessments)
        table = frg._build_control_family_table(fs)
        ac_rows = [ln for ln in table.splitlines() if ln.startswith("| AC ")]
        assert len(ac_rows) == 1
        assert "N/A" in ac_rows[0]
        assert "100.0%" not in ac_rows[0]

    def test_all_na_family_renders_na_in_details(self):
        """Detail section heading for an all-N/A family shows (N/A)."""
        assessments = [
            _mk("not_applicable", "AC-1"),
            _mk("not_applicable", "AC-2"),
        ]
        fs = frg._calculate_family_scores(assessments)
        details = frg._build_control_family_details(assessments, fs)
        ac_heading = [ln for ln in details.splitlines() if ln.startswith("### AC")]
        assert len(ac_heading) == 1
        assert "(N/A)" in ac_heading[0]
        assert "100.0%" not in ac_heading[0]

    def test_mixed_family_math_unchanged(self):
        """Regression: a mixed family still computes the documented score.

        AC: satisfied=2, other_than_satisfied=1, not_applicable=1
        scoreable = 4 - 1 = 3 -> 100 * 2 / 3 = 66.67 -> 66.7
        """
        assessments = [
            _mk("satisfied", "AC-1"),
            _mk("satisfied", "AC-2"),
            _mk("other_than_satisfied", "AC-3"),
            _mk("not_applicable", "AC-4"),
        ]
        fs = frg._calculate_family_scores(assessments)
        assert fs["AC"]["score"] == 66.7
        assert fs["AC"]["not_scoreable"] is False

    def test_rollups_exclude_na_family(self):
        """Recommendations and executive summary exclude N/A families rather
        than treating them as 0 or 100 (and must not crash on score=None)."""
        assessments = [
            _mk("not_applicable", "AC-1"),
            _mk("not_applicable", "AC-2"),
            _mk("other_than_satisfied", "AU-2"),
            _mk("satisfied", "AU-3"),
        ]
        fs = frg._calculate_family_scores(assessments)
        score, level = frg._calculate_readiness_score(assessments)
        gate = frg._evaluate_gate(assessments, score, fs)

        recs = frg._build_recommendations(assessments, fs, gate)
        # The scored-but-weak family surfaces; the N/A family never does.
        assert "**AU (" in recs
        assert "**AC (" not in recs

        summary = frg._build_executive_summary(score, level, gate, assessments, fs)
        # Weakest family is the scoreable AU (50%), never the all-N/A AC.
        assert "Weakest control family: **AU" in summary
        assert "**AC (" not in summary


def test_icdev_twin_matches_behaviour():
    """The icdev/ twin must return the same fixed values."""
    twin = importlib.import_module("icdev.tools.compliance.fedramp_report_generator")
    assessments = [_mk("not_applicable", "AC-1"), _mk("not_applicable", "AC-2")]
    assert twin._calculate_readiness_score(assessments) == (0.0, "Insufficient Scope")
    # nav-comp-04: the twin must also mark all-N/A families not_scoreable.
    fam = twin._calculate_family_scores(assessments)
    assert fam["AC"]["score"] is None
    assert fam["AC"]["not_scoreable"] is True


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v"]))
