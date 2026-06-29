#!/usr/bin/env python3
# CUI // SP-CTI
"""Tests for regression detection in quality_monitor (D3)."""
from __future__ import annotations


from icdev.tools.finetune.quality_monitor import detect_regression


# ── Higher-is-better metrics (bleu, rouge_l, accuracy, f1, pass_rate) ──


class TestDetectRegressionHigherIsBetter:
    def test_bleu_drops_more_than_threshold_is_regression(self):
        # bleu 0.35 → 0.30: delta = (0.30 - 0.35)/0.35 ≈ -0.143 < -0.05 threshold
        result = detect_regression({"bleu": 0.30}, {"bleu": 0.35}, threshold_pct=0.05)
        assert result["has_regression"] is True
        assert len(result["regressions"]) == 1
        assert result["regressions"][0]["metric"] == "bleu"
        assert result["regressions"][0]["direction"] == "worse"

    def test_rouge_l_drops_is_regression(self):
        result = detect_regression({"rouge_l": 0.40}, {"rouge_l": 0.50})
        assert result["has_regression"] is True
        assert result["regressions"][0]["metric"] == "rouge_l"

    def test_bleu_within_threshold_not_regression(self):
        # bleu 0.30 → 0.29: delta ≈ -0.033 (within 5% threshold)
        result = detect_regression({"bleu": 0.29}, {"bleu": 0.30}, threshold_pct=0.05)
        assert result["has_regression"] is False
        assert result["regressions"] == []

    def test_bleu_improves_is_improvement(self):
        # bleu 0.30 → 0.35: delta ≈ +0.167 > 5% threshold
        result = detect_regression({"bleu": 0.35}, {"bleu": 0.30})
        assert result["has_regression"] is False
        assert len(result["improvements"]) == 1
        assert result["improvements"][0]["metric"] == "bleu"
        assert result["improvements"][0]["direction"] == "better"

    def test_accuracy_regression(self):
        result = detect_regression({"accuracy": 0.70}, {"accuracy": 0.80})
        assert result["has_regression"] is True

    def test_f1_regression(self):
        result = detect_regression({"f1": 0.60}, {"f1": 0.70})
        assert result["has_regression"] is True

    def test_pass_rate_regression(self):
        result = detect_regression({"pass_rate": 0.50}, {"pass_rate": 0.65})
        assert result["has_regression"] is True


# ── Lower-is-better metrics (perplexity, loss) ───────────────────────


class TestDetectRegressionLowerIsBetter:
    def test_perplexity_increases_is_regression(self):
        # perplexity 10.0 → 12.0: delta = (12 - 10)/10 = 0.20 > 5% threshold
        result = detect_regression({"perplexity": 12.0}, {"perplexity": 10.0}, threshold_pct=0.05)
        assert result["has_regression"] is True
        assert result["regressions"][0]["metric"] == "perplexity"
        assert result["regressions"][0]["direction"] == "worse"

    def test_loss_increases_is_regression(self):
        result = detect_regression({"loss": 2.5}, {"loss": 2.0})
        assert result["has_regression"] is True
        assert result["regressions"][0]["metric"] == "loss"

    def test_perplexity_decreases_is_improvement(self):
        result = detect_regression({"perplexity": 8.0}, {"perplexity": 10.0})
        assert result["has_regression"] is False
        assert len(result["improvements"]) == 1
        assert result["improvements"][0]["metric"] == "perplexity"
        assert result["improvements"][0]["direction"] == "better"

    def test_perplexity_within_threshold_not_regression(self):
        # perplexity 10.0 → 10.4: delta = 0.04 < 5% threshold
        result = detect_regression({"perplexity": 10.4}, {"perplexity": 10.0}, threshold_pct=0.05)
        assert result["has_regression"] is False


# ── Multiple metrics ──────────────────────────────────────────────────


class TestMultipleMetrics:
    def test_mixed_regression_and_improvement(self):
        # bleu down (regression), perplexity down (improvement)
        result = detect_regression(
            {"bleu": 0.25, "perplexity": 8.0},
            {"bleu": 0.35, "perplexity": 10.0},
        )
        assert result["has_regression"] is True
        assert len(result["regressions"]) == 1
        assert result["regressions"][0]["metric"] == "bleu"
        assert len(result["improvements"]) == 1
        assert result["improvements"][0]["metric"] == "perplexity"

    def test_all_metrics_stable(self):
        result = detect_regression(
            {"bleu": 0.300, "rouge_l": 0.400, "perplexity": 10.0},
            {"bleu": 0.302, "rouge_l": 0.401, "perplexity": 10.1},
        )
        assert result["has_regression"] is False
        assert result["improvements"] == []

    def test_missing_metric_in_current_skipped(self):
        result = detect_regression(
            {"bleu": 0.30},
            {"bleu": 0.35, "rouge_l": 0.40},
        )
        # rouge_l not in current → skipped
        assert result["has_regression"] is True  # bleu regression still detected
        reg_metrics = [r["metric"] for r in result["regressions"]]
        assert "rouge_l" not in reg_metrics

    def test_zero_baseline_skipped_no_crash(self):
        result = detect_regression({"bleu": 0.30}, {"bleu": 0.0})
        assert result["has_regression"] is False  # division by zero skipped


# ── Return structure ──────────────────────────────────────────────────


class TestReturnStructure:
    def test_all_keys_present(self):
        result = detect_regression({}, {})
        assert "has_regression" in result
        assert "regressions" in result
        assert "improvements" in result
        assert "summary" in result

    def test_summary_mentions_regressions(self):
        result = detect_regression({"bleu": 0.20}, {"bleu": 0.40})
        assert "bleu" in result["summary"]

    def test_summary_no_changes(self):
        result = detect_regression({"bleu": 0.30}, {"bleu": 0.30})
        assert "No significant" in result["summary"]

    def test_delta_pct_sign_correct_for_improvement(self):
        # bleu goes up: delta_pct should be positive
        result = detect_regression({"bleu": 0.40}, {"bleu": 0.30})
        assert result["improvements"][0]["delta_pct"] > 0

    def test_delta_pct_sign_correct_for_regression(self):
        # bleu goes down: delta_pct should be negative
        result = detect_regression({"bleu": 0.20}, {"bleu": 0.30})
        assert result["regressions"][0]["delta_pct"] < 0

    def test_custom_threshold(self):
        # 1% threshold — even small drops count
        result = detect_regression({"bleu": 0.296}, {"bleu": 0.300}, threshold_pct=0.01)
        assert result["has_regression"] is True

    def test_ndcg_is_higher_is_better(self):
        # ndcg goes down → regression
        result = detect_regression({"ndcg": 0.40}, {"ndcg": 0.50})
        assert result["has_regression"] is True

    def test_mrr_is_higher_is_better(self):
        result = detect_regression({"mrr": 0.30}, {"mrr": 0.40})
        assert result["has_regression"] is True
