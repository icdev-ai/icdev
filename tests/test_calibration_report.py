# CUI // SP-CTI
"""The report about unearned certainty must not report unearned certainty.

Grounded in the live corpus. The organic join (oracle_predictions ->
kanban_verifications, which nobody had run) yields 49 verdicts, of which 27 are
'bypassed' and 22 are real labels:

    tool_not_in_manifest   claimed 0.95   17 passed / 2 failed   -> observed 0.89
    orphan_db_table        claimed 0.85    0 passed / 2 failed   -> 2 labels
    route_not_listed       claimed 0.90    1 passed / 0 failed   -> 1 label
    broken_test_reference  claimed 0.90    all 6 bypassed        -> 0 labels
    route_no_e2e           claimed 0.70    all 3 bypassed        -> 0 labels

Exactly one rule clears the evidence floor, and it is roughly right. The alarming
story this work started from — "the 0.9 band is right a third of the time" — was
an artifact of grouping unrelated rules into a band. Per band the same data reads
"0.9: 20 labels, observed 0.90", which is tool_not_in_manifest's 19 rows plus one
row of route_not_listed. That is what TestBandsMislead pins.
"""

import pytest

from tools.genesis.harness import calibration_report as cr
from tools.genesis.harness import eval_harness as eh


def _row(rule, conf, outcome):
    return {"decision": rule, "confidence": conf, "actual_outcome": outcome}


# The live organic corpus, as measured.
LIVE = (
    [_row("gap::tool_not_in_manifest", 0.95, "passed")] * 17
    + [_row("gap::tool_not_in_manifest", 0.95, "failed")] * 2
    + [_row("gap::tool_not_in_manifest", 0.95, "bypassed")] * 13
    + [_row("gap::orphan_db_table", 0.85, "failed")] * 2
    + [_row("gap::orphan_db_table", 0.85, "bypassed")] * 4
    + [_row("gap::route_not_listed", 0.90, "passed")] * 1
    + [_row("gap::route_not_listed", 0.90, "bypassed")] * 1
    + [_row("gap::broken_test_reference", 0.90, "bypassed")] * 6
    + [_row("gap::route_no_e2e", 0.70, "bypassed")] * 3
)


def _by_rule(rows=LIVE):
    return {g["rule"]: g for g in
            eh.calibration_by_rule(rows, success_outcomes=cr.VERIFICATION_SUCCESS)}


class TestReproducesTheLiveBaseline:
    def test_the_one_measurable_rule_is_roughly_right(self):
        """The honest headline. Not 'confidence is broken' — one rule has enough
        evidence to judge, and it claims 0.95 while delivering 0.89."""
        g = _by_rule()["gap::tool_not_in_manifest"]
        assert g["measured"] is True
        assert g["labelled"] == 19
        assert g["observed_accuracy"] == pytest.approx(0.8947, abs=1e-3)
        assert g["ci_low"] < 0.95 < g["ci_high"], "0.95 is inside the interval — consistent"

    def test_everything_else_is_unmeasured(self):
        by_rule = _by_rule()
        for rule in ("gap::orphan_db_table", "gap::route_not_listed",
                     "gap::broken_test_reference", "gap::route_no_e2e"):
            assert by_rule[rule]["measured"] is False, f"{rule} must not report a number"

    def test_a_rule_with_only_bypassed_rows_reports_no_accuracy(self):
        """broken_test_reference was promoted 6 times and verified zero times.
        That is a coverage hole, not a 0% accuracy."""
        g = _by_rule()["gap::broken_test_reference"]
        assert g["labelled"] == 0
        assert g["observed_accuracy"] is None
        assert g["non_evidence"] == 6


class TestBandsMislead:
    """Why this report is per rule. Same rows, two cuts, two different stories."""

    def test_the_band_number_is_really_one_rules_number(self):
        band = {g["band"]: g for g in
                eh.calibration_by_band(LIVE, success_outcomes=cr.VERIFICATION_SUCCESS)}[0.9]
        rule = _by_rule()["gap::tool_not_in_manifest"]
        assert band["labelled"] == 20
        assert rule["labelled"] == 19
        # The band looks like solid evidence; 19 of its 20 labels are one rule,
        # and 0.95 and 0.90 are different claims averaged into one bucket.
        assert abs(band["observed_accuracy"] - rule["observed_accuracy"]) < 0.02

    def test_a_band_hides_a_failing_rule_inside_a_passing_one(self):
        """The real cost, using the live collision: 0.95 and 0.90 share the 0.9
        band, which is why tool_not_in_manifest and broken_test_reference are
        averaged together today. A rule that never works disappears into a
        healthy neighbour's number."""
        rows = ([_row("healthy", 0.95, "passed")] * 18
                + [_row("broken", 0.90, "failed")] * 6)
        band = eh.calibration_by_band(rows, success_outcomes=cr.VERIFICATION_SUCCESS)
        assert len(band) == 1, "0.95 and 0.90 land in the same band"
        assert band[0]["observed_accuracy"] == pytest.approx(0.75)

        by_rule = {g["rule"]: g for g in
                   eh.calibration_by_rule(rows, success_outcomes=cr.VERIFICATION_SUCCESS)}
        assert by_rule["healthy"]["observed_accuracy"] == pytest.approx(1.0)
        assert by_rule["broken"]["observed_accuracy"] == pytest.approx(0.0)


class TestBypassedIsNotFailure:
    def test_bypassed_never_counts_against_a_rule(self):
        """27 of the 49 live verdicts are 'bypassed'. Scoring them as misses is
        the exact mistake made while first probing this, which reported every
        band as 0.00 accurate."""
        g = _by_rule()["gap::tool_not_in_manifest"]
        assert g["non_evidence"] == 13
        assert g["labelled"] == 19, "bypassed rows must not enter the denominator"

    def test_a_rule_that_is_only_unverified_is_not_scored_zero(self):
        rows = [_row("never_checked", 0.9, "bypassed")] * 20
        g = eh.calibration_by_rule(rows, success_outcomes=cr.VERIFICATION_SUCCESS)[0]
        assert g["observed_accuracy"] is None and g["measured"] is False


class TestProvenanceIsNeverPooled:
    def test_organic_and_sampled_are_reported_separately(self):
        class FakeConn:
            def execute(self, sql, params=None):
                s = " ".join(sql.split())
                if "count(*)" in s:
                    rows = [{"total": 1, "labelled": 1}]
                elif "kanban_verifications" in s:
                    rows = LIVE
                else:
                    rows = [_row("gap::route_no_e2e", 0.7, "failed")]

                class C:
                    def fetchall(self_inner):
                        return rows

                    def fetchone(self_inner):
                        return rows[0]
                return C()

        report = cr.build_report(FakeConn())
        assert "BIASED" in report["organic"]["provenance"]
        assert "UNBIASED" in report["sampled"]["provenance"]
        organic_rules = {g["rule"] for g in report["organic"]["rules"]}
        sampled_rules = {g["rule"] for g in report["sampled"]["rules"]}
        # The sampled route_no_e2e verdict must not merge into the organic rule
        # of the same name — one is unbiased, the other is not, and their average
        # would inherit the bias while looking like more evidence.
        assert "gap::route_no_e2e" in organic_rules and "gap::route_no_e2e" in sampled_rules
        assert report["organic"]["rules"] is not report["sampled"]["rules"]

    def test_an_empty_harness_eval_is_stated_not_silently_green(self):
        """compute_metrics and the ece gate read harness_eval, which holds one
        row. A report that stayed quiet about that would let an empty table read
        as a healthy one."""
        text = cr.render({
            "harness_eval": {"total": 1, "labelled": 1},
            "organic": {"provenance": "BIASED — x", "joined": 0, "rules": []},
            "sampled": {"provenance": "UNBIASED — y", "joined": 0, "rules": []},
            "min_samples": 5,
        })
        assert "1 row" in text and "empty" in text


class TestRenderNeverAssertsWhatItCannotShow:
    def test_an_unmeasured_rule_renders_no_number(self):
        groups = eh.calibration_by_rule(LIVE, success_outcomes=cr.VERIFICATION_SUCCESS)
        lines = "\n".join(cr._render_rules(groups))
        assert "UNMEASURED" in lines
        # orphan_db_table is 0-for-2. It must never render as "observed 0.00".
        for line in lines.splitlines():
            if "orphan_db_table" in line:
                assert "observed" not in line, line

    def test_a_rule_with_mixed_constants_is_flagged(self):
        """The tombstone bug: raise a rule's confidence in YAML and old rows keep
        the old value, so 'claimed' becomes an average of two different claims."""
        rows = ([_row("edited", 0.7, "passed")] * 3) + ([_row("edited", 0.9, "passed")] * 3)
        g = eh.calibration_by_rule(rows, success_outcomes=cr.VERIFICATION_SUCCESS)[0]
        assert g["claimed_values"] == [0.7, 0.9]
        assert "mixed constants" in "\n".join(cr._render_rules([g]))
