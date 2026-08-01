# CUI // SP-CTI
"""wire-04: divergence_invoke MCP tool + skill registration.

Follows the handle_cod_invoke / handle_council_query contract: build the request,
call the orchestrator, return a flat payload, and catch-and-return {'error': ...}
rather than raising.
"""
from unittest.mock import MagicMock, patch


def test_divergence_invoke_registered():
    import tools.mcp.tool_registry as tr

    # Lives alongside cod_invoke/cot_invoke in the llmops chain-orchestration registry.
    reg = next(
        d for name in dir(tr)
        if isinstance(d := getattr(tr, name), dict) and "cod_invoke" in d
    )
    assert "divergence_invoke" in reg
    entry = reg["divergence_invoke"]
    assert entry["handler"] == "handle_divergence_invoke"
    assert entry["module"] == "tools.mcp.gap_handlers"
    assert "function" in entry["input_schema"]["required"]
    assert "prompt" in entry["input_schema"]["required"]


def test_handler_returns_flat_payload_on_success():
    from tools.mcp.gap_handlers import handle_divergence_invoke

    fake_result = MagicMock()
    fake_result.content = "# Divergent Idea Pool\n## Frame: X\n1. idea"
    fake_result.chain_mode = "divergence"
    fake_result.models_used = ["m1", "m2"]
    fake_result.total_cost_usd = 0.01
    fake_result.total_input_tokens = 100
    fake_result.total_output_tokens = 50
    fake_result.total_duration_ms = 1234
    fake_result.stop_reason = "completed"
    fake_result.trace_id = "trace-1"
    fake_result.rounds = [{"step": "branch:X"}]

    with patch("tools.llm.chain_orchestrator.ChainOrchestrator") as Orch:
        Orch.return_value.invoke_divergence.return_value = fake_result
        out = handle_divergence_invoke({"function": "f", "prompt": "widen this"})

    assert "error" not in out
    assert out["chain_mode"] == "divergence"
    assert out["trace_id"] == "trace-1"
    assert out["models_used"] == ["m1", "m2"]
    assert out["stop_reason"] == "completed"


def test_handler_returns_error_dict_never_raises():
    from tools.mcp.gap_handlers import handle_divergence_invoke

    with patch("tools.llm.chain_orchestrator.ChainOrchestrator") as Orch:
        Orch.return_value.invoke_divergence.side_effect = RuntimeError("Divergence is disabled in config")
        out = handle_divergence_invoke({"function": "f", "prompt": "x"})

    assert "error" in out
    assert "disabled" in out["error"]


def test_score_opt_in_runs_critic():
    from tools.mcp.gap_handlers import handle_divergence_invoke

    fake_result = MagicMock()
    fake_result.content = "# Divergent Idea Pool\n## Frame: X\n1. idea"
    fake_result.chain_mode = "divergence"
    fake_result.models_used = []
    fake_result.total_cost_usd = 0.0
    fake_result.total_input_tokens = 0
    fake_result.total_output_tokens = 0
    fake_result.total_duration_ms = 0
    fake_result.stop_reason = "completed"
    fake_result.trace_id = "t"
    fake_result.rounds = []

    fake_scored = MagicMock()
    fake_scored.as_dict.return_value = {"ideas": []}
    fake_scored.trap_warnings.return_value = [{"kind": "divergence_trap"}]

    with patch("tools.llm.chain_orchestrator.ChainOrchestrator") as Orch, \
         patch("tools.quality.divergence_critic.score_idea_pool", return_value=fake_scored):
        Orch.return_value.invoke_divergence.return_value = fake_result
        out = handle_divergence_invoke({"function": "f", "prompt": "x", "score": True})

    assert out.get("scored") == {"ideas": []}
    assert out.get("trap_warnings") == [{"kind": "divergence_trap"}]


def test_skill_registered():
    from tools.skills.registry import build_registry

    reg = build_registry()
    assert "icdev-divergence" in reg.get("skills", {})
    skill = reg["skills"]["icdev-divergence"]
    # The skill documents the two allowlisted python commands (generate + score).
    joined = " ".join(skill.get("commands", []))
    assert "chain_orchestrator.py --divergence" in joined
    assert "divergence_critic.py" in joined
