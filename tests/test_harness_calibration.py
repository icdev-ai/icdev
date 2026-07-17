# CUI // SP-CTI
"""Calibration must not manufacture its own evidence.

TRUST gates on confidence; nothing checked whether confidence predicts
correctness. Measured on the live corpus (oracle_predictions joined to
kanban_verifications — a join nobody had run): claimed 1.0 -> observed 0.89,
claimed 0.9 -> observed 0.33. Non-monotonic, and 63% of verifications are
'bypassed' so most ground truth is absent.

_compute_ece had two defects that would corrupt any such measurement:
  * a NULL confidence became a fabricated 0.5
  * a NULL outcome scored as a WRONG prediction (`None == "resolved"` is False)
and the second only bit one of its two callers, so the same function reported
different calibration depending on who called it.
"""

import pytest

from tools.genesis.harness import eval_harness as eh


def _row(conf, outcome):
    return {"confidence": conf, "actual_outcome": outcome, "decision": "x"}


class TestNeverFabricatesInput:
    def test_null_confidence_is_dropped_not_scored_as_half(self):
        """It read `float(r["confidence"]) if not None else 0.5` — inventing a
        number inside the function whose job is checking whether the numbers are
        honest."""
        rows = [_row(None, "resolved") for _ in range(10)]
        assert eh._compute_ece(rows) is None, "all-NULL confidence must be unmeasurable, not 0.5"

    def test_null_confidence_does_not_pollute_a_real_sample(self):
        real = [_row(1.0, "resolved") for _ in range(6)]
        polluted = real + [_row(None, "resolved") for _ in range(6)]
        assert eh._compute_ece(real) == eh._compute_ece(polluted)


class TestNeverScoresUnknownAsWrong:
    def test_null_outcome_is_dropped_not_counted_as_a_miss(self):
        """`outcome = r["actual_outcome"] == "resolved"` made None -> False: a
        prediction nobody had judged counted as a failed one."""
        rows = [_row(0.9, None) for _ in range(10)]
        assert eh._compute_ece(rows) is None

    def test_bypassed_is_not_evidence(self):
        """1387 of 2201 live verifications are 'bypassed' — verification skipped.
        Counting that as failure manufactures miscalibration out of missing data.
        This is the exact mistake I made while probing."""
        rows = [_row(0.9, "bypassed") for _ in range(10)]
        assert eh._compute_ece(rows) is None

    def test_a_perfectly_calibrated_set_scores_zero_despite_unknowns(self):
        """The regression: unknowns used to drag a perfect predictor's ECE up."""
        perfect = [_row(1.0, "resolved") for _ in range(10)]
        assert eh._compute_ece(perfect) == pytest.approx(0.0, abs=1e-9)
        with_unknowns = perfect + [_row(1.0, None) for _ in range(10)] \
                                + [_row(1.0, "bypassed") for _ in range(10)]
        assert eh._compute_ece(with_unknowns) == pytest.approx(0.0, abs=1e-9), \
            "unlabelled rows must not make a perfect predictor look miscalibrated"

    def test_both_callers_now_agree(self):
        """compute_metrics pre-filtered outcomes; _snapshot_metrics did not, so
        the same rows produced different ECE depending on the caller. The filter
        lives in _compute_ece now, so neither can get it wrong."""
        rows = [_row(1.0, "resolved") for _ in range(6)] + [_row(1.0, None) for _ in range(6)]
        pre_filtered = [r for r in rows if r["confidence"] is not None and r["actual_outcome"] is not None]
        conf_only = [r for r in rows if r["confidence"] is not None]
        assert eh._compute_ece(pre_filtered) == eh._compute_ece(conf_only)


class TestOutcomeVocabularyIsParameterised:
    def test_default_is_the_harness_vocabulary(self):
        assert "resolved" in eh.SUCCESS_OUTCOMES

    def test_another_surface_can_bring_its_own_label(self):
        """A docmod finding is right when a human 'accepted' it; a sampled
        prediction when its task 'passed'. Hardcoding "resolved" scored every
        other surface 0% accurate — not wrong, just spelled differently."""
        rows = [_row(1.0, "accepted") for _ in range(10)]
        assert eh._compute_ece(rows) == pytest.approx(1.0, abs=1e-9), \
            "with the default vocabulary these all look wrong"
        assert eh._compute_ece(rows, success_outcomes={"accepted"}) == pytest.approx(0.0, abs=1e-9)


class TestSmallSamplesRenderAsUnmeasured:
    def test_below_the_floor_returns_none(self):
        assert eh._compute_ece([_row(0.9, "resolved") for _ in range(4)]) is None

    def test_band_with_three_labels_is_not_measured(self):
        """The live 0.9 band had 3 labels and 'observed 0.33'. Reporting that as
        a fact would be the very sin this report exists to expose."""
        rows = [_row(0.9, "resolved")] + [_row(0.9, "failed") for _ in range(2)]
        band = eh.calibration_by_band(rows)[0]
        assert band["labelled"] == 3
        assert band["measured"] is False
        assert band["ci_low"] is not None and band["ci_high"] is not None
        assert band["ci_high"] - band["ci_low"] > 0.5, "3 samples must yield a wide interval"


class TestCalibrationByBand:
    def test_reports_the_gap_per_band(self):
        rows = ([_row(0.9, "resolved")] * 3) + ([_row(0.9, "failed")] * 3)
        band = eh.calibration_by_band(rows)[0]
        assert band["claimed"] == 0.9
        assert band["observed_accuracy"] == pytest.approx(0.5)
        assert band["gap"] == pytest.approx(0.4)  # claimed 0.9, delivered 0.5

    def test_non_evidence_is_tracked_separately_not_scored(self):
        rows = ([_row(1.0, "resolved")] * 5) + ([_row(1.0, "bypassed")] * 7)
        band = eh.calibration_by_band(rows)[0]
        assert band["labelled"] == 5
        assert band["non_evidence"] == 7
        assert band["observed_accuracy"] == pytest.approx(1.0), "bypassed must not count as a miss"

    def test_surfaces_non_monotonic_confidence(self):
        """The finding the aggregate ECE hides: on live data the 1.0 band beat
        the 0.9 band, so higher claimed confidence predicted a worse outcome."""
        rows = ([_row(1.0, "resolved")] * 9) + ([_row(1.0, "failed")] * 1) \
             + ([_row(0.9, "resolved")] * 2) + ([_row(0.9, "failed")] * 8)
        by_band = {b["band"]: b for b in eh.calibration_by_band(rows)}
        assert by_band[1.0]["observed_accuracy"] > by_band[0.9]["observed_accuracy"]

    def test_empty_input_is_empty_not_zero(self):
        assert eh.calibration_by_band([]) == []


class TestGateScopeIsConfigurable:
    def test_default_preserves_existing_behaviour(self):
        assert eh._gated_reflexes({}) == ("oracle_triage", "heal")

    def test_config_can_add_a_surface(self):
        assert eh._gated_reflexes({"gated_reflexes": ["oracle_triage", "heal", "confidence_sampler"]}) \
            == ("oracle_triage", "heal", "confidence_sampler")

    def test_empty_config_falls_back_rather_than_silently_gating_nothing(self):
        """An empty list must not mean 'measure nothing' — that would disable the
        gate silently, which is worse than the hardcoded tuple it replaced."""
        assert eh._gated_reflexes({"gated_reflexes": []}) == ("oracle_triage", "heal")
        assert eh._gated_reflexes({"gated_reflexes": [" "]}) == ("oracle_triage", "heal")


class TestBandBoundariesAreExact:
    """Float division misfiles the band that matters most.

    `math.floor(0.7 / 0.1)` is 6, not 7 — 0.7/0.1 is 6.999999999999999 in binary
    floating point. 41 live oracle predictions sit at exactly 0.7, the promotion
    gate. Filed a band low, the gate's own band renders empty while the band
    beneath it inherits its rows: a calibration report that misfiles the very
    threshold it exists to evaluate.
    """

    @pytest.mark.parametrize("conf,expected", [
        (0.7, 0.7),   # the gate — floor(0.7/0.1) == 6 without integer math
        (0.3, 0.3),   # floor(0.3/0.1) == 2
        (0.9, 0.9),
        (1.0, 1.0),
        (0.0, 0.0),
        (0.733, 0.7),  # a real live value
        (0.699, 0.6),  # just below the gate stays below it
    ])
    def test_band_of(self, conf, expected):
        assert eh._band_of(conf, 0.1) == pytest.approx(expected)

    def test_the_gate_value_lands_in_the_gate_band(self):
        rows = [_row(0.7, "resolved") for _ in range(6)]
        bands = eh.calibration_by_band(rows)
        assert [b["band"] for b in bands] == [0.7], "0.7 must not be filed as 0.6"

    def test_zero_width_does_not_divide_by_zero(self):
        assert eh._band_of(0.7, 0.0) == 0.0


class TestWilsonInterval:
    def test_stays_inside_the_unit_interval_at_the_extremes(self):
        """Where the normal approximation reports nonsense like 1.0 +/- 0.3."""
        lo, hi = eh._wilson_interval(5, 5)
        assert 0.0 <= lo <= hi <= 1.0
        lo, hi = eh._wilson_interval(0, 5)
        assert 0.0 <= lo <= hi <= 1.0

    def test_does_not_collapse_to_zero_width_at_p_equals_one(self):
        lo, hi = eh._wilson_interval(5, 5)
        assert lo < 1.0, "5/5 is not proof of perfection"

    def test_narrows_as_evidence_accumulates(self):
        small = eh._wilson_interval(9, 10)
        large = eh._wilson_interval(900, 1000)
        assert (large[1] - large[0]) < (small[1] - small[0])

    def test_no_samples_is_maximal_uncertainty(self):
        assert eh._wilson_interval(0, 0) == (0.0, 1.0)
