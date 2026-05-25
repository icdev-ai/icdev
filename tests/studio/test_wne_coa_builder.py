"""Unit tests for tools.studio.wne.coa_builder — no DB, no LLM, air-gap safe."""
from pathlib import Path


from tools.studio.wne.coa_builder import COABuilder
from tools.studio.wne.context_builder import WorkflowContext

YAML_PATH = Path(__file__).parents[2] / "args" / "workflow_templates" / "ai_ml_transformation.yaml"


def _minimal_ctx() -> WorkflowContext:
    return WorkflowContext(
        template_name="test",
        audience="",
        org_name="",
        program_name="Test",
        classification="CUI",
        purpose="",
        timeframe_months=18,
        parameters={},
        phases=[],
        decision_points=[],
        approval_gates=[],
    )


def test_parametric_fallback_returns_three_coas():
    result = COABuilder().build(_minimal_ctx())
    assert result.coa_a is not None
    assert result.coa_b is not None
    assert result.coa_c is not None


def test_coa_b_is_recommended():
    result = COABuilder().build(_minimal_ctx())
    assert result.coa_b.recommendation is True
    assert result.coa_a.recommendation is False
    assert result.coa_c.recommendation is False


def test_composite_coa_node_extraction_from_yaml():
    # ai_ml_transformation.yaml has direct coa_a/b/c step nodes; extraction
    # should use YAML step names rather than parametric defaults
    result = COABuilder().build_from_yaml(YAML_PATH)
    assert result.coa_a is not None
    assert result.coa_b is not None
    assert result.coa_c is not None
    assert result.coa_a.name == "COA-A: Organic Growth"
    assert result.coa_b.name == "COA-B: Hybrid Approach"
    assert result.coa_c.name == "COA-C: Sprint Acquisition"
