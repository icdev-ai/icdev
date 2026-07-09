# CUI // SP-CTI
"""Tests: price-to-win figures are withheld from roles denied them at rest.

`column_policies` deny `ptw_estimate_low/high` and `calc_benchmark_median` on
`pg_cost_volumes` to `reviewer` and `co`. But `rate_benchmarker.ptw_analysis()`
recomputes those same figures live from raw `pg_competitor_awards.award_amount`,
so those roles could recover them via the PTW endpoint / opportunity page —
the column-masking policy was bypassed by the live computation.

`award_amount` is deliberately NOT masked at the DB layer: `ptw_analysis`
aggregates it server-side, and NULLing it would not hide the answer — it would
make the analysis silently return "no award amounts" with a bogus
`recommendation: competitive, confidence: 0.1`. The derived payload is masked
instead. The last test locks that distinction in.
"""

import importlib

cs = importlib.import_module("tools.security.column_security")


def _ptw_result() -> dict:
    return {
        "status": "ok",
        "opportunity_id": "opp-1",
        "competitor_count": 6,
        "award_amounts_analyzed": 6,
        "ptw_range": {"low": 1000.0, "median": 2000.0, "high": 3000.0},
        "recommendation": "competitive",
        "confidence": 0.3,
        "strategies": {
            "aggressive": {"target": 950.0, "risk": "high"},
            "competitive": {"target": 2000.0, "risk": "moderate"},
            "premium": {"target": 3150.0, "risk": "low"},
        },
    }


class TestPtwPayloadMasking:
    def test_reviewer_cannot_see_recomputed_ptw_range(self):
        out = cs.mask_ptw_payload(_ptw_result(), "reviewer")
        assert out["ptw_range_masked"] is True
        assert all(v is None for v in out["ptw_range"].values())

    def test_co_cannot_see_recomputed_strategy_targets(self):
        out = cs.mask_ptw_payload(_ptw_result(), "co")
        assert out["strategies_masked"] is True
        assert all(s["target"] is None for s in out["strategies"].values())
        # non-price metadata is preserved
        assert out["strategies"]["aggressive"]["risk"] == "high"

    def test_capture_mgr_sees_full_figures(self):
        out = cs.mask_ptw_payload(_ptw_result(), "capture_mgr")
        assert out["ptw_range"]["median"] == 2000.0
        assert out["strategies"]["competitive"]["target"] == 2000.0
        assert "ptw_range_masked" not in out

    def test_masking_preserves_key_shape_for_clients(self):
        src = _ptw_result()
        out = cs.mask_ptw_payload(src, "reviewer")
        assert set(out["ptw_range"]) == set(src["ptw_range"])
        assert set(out["strategies"]) == set(src["strategies"])
        assert out["competitor_count"] == 6  # non-price context still available

    def test_original_payload_not_mutated(self):
        src = _ptw_result()
        cs.mask_ptw_payload(src, "reviewer")
        assert src["ptw_range"]["median"] == 2000.0

    def test_non_ok_status_passes_through(self):
        err = {"status": "error", "message": "boom"}
        assert cs.mask_ptw_payload(err, "reviewer") is err

    def test_empty_role_is_not_denied(self):
        out = cs.mask_ptw_payload(_ptw_result(), "")
        assert out["ptw_range"]["median"] == 2000.0


class TestCompetitorAwardsColumnPolicy:
    def test_labor_categories_masked_for_reviewer_and_co(self):
        for role in ("reviewer", "co"):
            pol = cs.get_column_policies_for_role("pg_competitor_awards", role)
            assert pol.get("labor_categories") == "null", role

    def test_award_amount_NOT_masked_at_db_layer(self):
        """Regression guard: masking it would silently corrupt ptw_analysis().

        ptw_analysis filters falsy award_amount values, so a NULLed column makes
        it return "no award amounts" + recommendation "competitive" rather than
        an honest refusal. Protection belongs at the payload layer.
        """
        for role in ("reviewer", "co"):
            pol = cs.get_column_policies_for_role("pg_competitor_awards", role)
            assert "award_amount" not in pol, role

    def test_capture_mgr_has_no_competitor_award_policy(self):
        assert cs.get_column_policies_for_role("pg_competitor_awards", "capture_mgr") == {}
