"""Unit tests for tools.studio.wne.roi_calculator — no DB, no LLM, air-gap safe."""


from tools.studio.wne.context_builder import WorkflowContext
from tools.studio.wne.roi_calculator import ROICalculator

_STANDARD_PARAMS = {
    "developers_targeted": 45,
    "training_cost_per_person_usd": 8500,
    "lab_standup_cost_usd": 250000,
    "workforce_size": 120,
    "avg_annual_salary_usd": 130000,
    "ai_productivity_gain_pct": 30,
}


def _ctx(params: dict, timeframe_months: int = 18) -> WorkflowContext:
    return WorkflowContext(
        template_name="test",
        audience="",
        org_name="",
        program_name="Test",
        classification="CUI",
        purpose="",
        timeframe_months=timeframe_months,
        parameters=params,
        phases=[],
        decision_points=[],
        approval_gates=[],
    )


def test_npv_8pct_payback_lt_timeframe():
    result = ROICalculator().calculate(_ctx(_STANDARD_PARAMS, timeframe_months=18))
    assert result.npv_usd is not None
    assert result.npv_usd > 0
    assert result.payback_months is not None
    assert result.payback_months < 18


def test_roi_pct_positive():
    result = ROICalculator().calculate(_ctx(_STANDARD_PARAMS, timeframe_months=18))
    assert result.roi_pct is not None
    assert result.roi_pct > 0


def test_sensitivity_table_five_rows():
    result = ROICalculator().calculate(_ctx(_STANDARD_PARAMS, timeframe_months=18))
    assert len(result.sensitivity_table) == 5


def test_degrade_gracefully_empty_parameters():
    result = ROICalculator().calculate(_ctx({}))
    assert result.roi_pct is None
    assert result.npv_usd is None
    assert result.note != ""
