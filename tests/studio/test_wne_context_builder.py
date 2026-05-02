"""Unit tests for tools.studio.wne.context_builder — no DB, no LLM, air-gap safe."""
from pathlib import Path


from tools.studio.wne.context_builder import WorkflowContextBuilder

YAML_PATH = Path(__file__).parents[2] / "args" / "workflow_templates" / "ai_ml_transformation.yaml"


def test_build_phases_gte_five():
    ctx = WorkflowContextBuilder().build(YAML_PATH)
    assert len(ctx.phases) >= 5


def test_decision_points_extracted():
    ctx = WorkflowContextBuilder().build(YAML_PATH)
    assert len(ctx.decision_points) > 0
    dp_ids = {dp.node_id for dp in ctx.decision_points}
    assert "leadership_brief" in dp_ids


def test_approval_gates_extracted():
    ctx = WorkflowContextBuilder().build(YAML_PATH)
    assert len(ctx.approval_gates) > 0
    gate_ids = {ag.node_id for ag in ctx.approval_gates}
    assert "funding_approval" in gate_ids


def test_degrade_gracefully_missing_narrative_context():
    minimal = {
        "steps": [
            {"id": "step_a", "name": "Step A", "node_type": "tool"},
        ]
    }
    ctx = WorkflowContextBuilder().build(minimal)
    assert ctx.template_name == "unnamed"
    assert ctx.audience == ""
    assert ctx.org_name == ""
    assert ctx.parameters == {}
    assert len(ctx.phases) >= 1


def test_topological_sort_respects_depends_on():
    chain = {
        "steps": [
            {"id": "c", "name": "C", "node_type": "tool", "depends_on": ["b"]},
            {"id": "b", "name": "B", "node_type": "tool", "depends_on": ["a"]},
            {"id": "a", "name": "A", "node_type": "tool"},
        ]
    }
    ctx = WorkflowContextBuilder().build(chain)
    ordered = [node for phase in ctx.phases for node in phase.nodes]
    assert ordered.index("a") < ordered.index("b")
    assert ordered.index("b") < ordered.index("c")
