# CUI // SP-CTI
"""Unit tests for the reasoned_codegen wrapper.

Mock the LLM router and the anvil_critique engine so no real API calls or DB
access occur. Covers: passthrough identity, CoT generation, verify->repair
termination, budget abort, and critique veto short-circuit.
"""

from unittest.mock import MagicMock, patch

import pytest

from tools.llm.provider import LLMRequest, LLMResponse
from tools.llm import reasoned_codegen as rc
from tools.llm.reasoned_codegen import (
    VerificationResult,
    generate_reasoned_code,
    resolve_config,
)


# ---------------------------------------------------------------------------
# Helpers / fixtures
# ---------------------------------------------------------------------------
def _resp(content, inp=10, out=20, model_id="qwen3-local"):
    return LLMResponse(content=content, input_tokens=inp, output_tokens=out, model_id=model_id)


def _router(reasoned_cfg=None, routing=None):
    """MagicMock router with a configurable reasoned_codegen config block."""
    router = MagicMock()
    router._config = {
        "reasoned_codegen": reasoned_cfg if reasoned_cfg is not None else {},
        "routing": routing if routing is not None else {},
    }
    router.get_model_pricing.return_value = {"input_per_1k": 0.001, "output_per_1k": 0.002}
    router.invoke.return_value = _resp("plain code")
    router.invoke_chain_of_thought.return_value = _resp("cot code")
    router.invoke_chain_of_debate.return_value = _resp("cod code")
    return router


def _req():
    return LLMRequest(messages=[{"role": "user", "content": "translate this"}])


# ---------------------------------------------------------------------------
# resolve_config
# ---------------------------------------------------------------------------
def test_resolve_config_defaults_to_off():
    r = _router(reasoned_cfg={})
    cfg = resolve_config("anything", r)
    assert cfg["mode"] == rc.MODE_OFF
    assert cfg["critique"] is False


def test_resolve_config_per_function_override():
    r = _router(reasoned_cfg={
        "enabled": True,
        "default_mode": "off",
        "per_function": {"code_translation": {"enabled": True, "mode": "cot", "max_repair_rounds": 3}},
    })
    cfg = resolve_config("code_translation", r)
    assert cfg["mode"] == "cot"
    assert cfg["max_repair_rounds"] == 3


def test_resolve_config_globally_disabled_forces_off():
    r = _router(reasoned_cfg={
        "enabled": False,
        "per_function": {"code_translation": {"enabled": True, "mode": "cot"}},
    })
    cfg = resolve_config("code_translation", r)
    # Section-level disable wins.
    assert cfg["mode"] == rc.MODE_OFF
    assert cfg["critique"] is False


# ---------------------------------------------------------------------------
# Passthrough identity
# ---------------------------------------------------------------------------
def test_passthrough_identity_when_off():
    r = _router(reasoned_cfg={})  # everything OFF
    res = generate_reasoned_code(function="code_translation", request=_req(), router=r)
    assert res.code == "plain code"
    assert res.mode == rc.MODE_OFF
    assert res.stop_reason == rc.STOP_PASSTHROUGH
    assert res.rounds_used == 0
    # Generation went through router.invoke exactly once, no CoT/CoD/critique.
    r.invoke.assert_called_once()
    r.invoke_chain_of_thought.assert_not_called()
    r.invoke_chain_of_debate.assert_not_called()


def test_explicit_mode_override_beats_config():
    r = _router(reasoned_cfg={})  # config OFF
    res = generate_reasoned_code(function="code_translation", request=_req(), router=r, mode="cot")
    assert res.mode == "cot"
    assert res.code == "cot code"
    r.invoke_chain_of_thought.assert_called_once()


# ---------------------------------------------------------------------------
# CoT generation + verifier PASS
# ---------------------------------------------------------------------------
def test_cot_generation_with_passing_verifier():
    r = _router(reasoned_cfg={
        "enabled": True,
        "per_function": {"code_translation": {"enabled": True, "mode": "cot", "max_repair_rounds": 3}},
    })
    verifier = MagicMock(return_value=VerificationResult(passed=True, score=1.0, gate_result="pass"))
    res = generate_reasoned_code(
        function="code_translation", request=_req(), router=r, verifier=verifier
    )
    assert res.code == "cot code"
    assert res.passed is True
    assert res.stop_reason == rc.STOP_COMPLETED
    assert res.rounds_used == 1
    verifier.assert_called_once()
    r.invoke.assert_not_called()  # no repair needed


# ---------------------------------------------------------------------------
# Verify -> repair loop
# ---------------------------------------------------------------------------
def test_repair_loop_fixes_then_passes():
    r = _router(
        reasoned_cfg={"enabled": True, "per_function": {
            "code_translation": {"enabled": True, "mode": "cot", "max_repair_rounds": 3}}},
        routing={"code_translation_repair": {"chain": ["qwen3-local"]}},
    )
    r.invoke.return_value = _resp("repaired code")  # the repair call
    # Fail first verify, pass second.
    verifier = MagicMock(side_effect=[
        VerificationResult(passed=False, gate_result="fail", findings=["syntax error"]),
        VerificationResult(passed=True, gate_result="pass"),
    ])
    res = generate_reasoned_code(
        function="code_translation", request=_req(), router=r, verifier=verifier
    )
    assert res.code == "repaired code"
    assert res.passed is True
    assert res.stop_reason == rc.STOP_COMPLETED
    assert res.rounds_used == 2
    # Repair routed to the _repair function key.
    r.invoke.assert_called_once()
    assert r.invoke.call_args[0][0] == "code_translation_repair"


def test_repair_loop_exhausts_rounds_and_fails():
    r = _router(reasoned_cfg={"enabled": True, "per_function": {
        "code_translation": {"enabled": True, "mode": "cot", "max_repair_rounds": 2}}})
    r.invoke.return_value = _resp("still broken")
    verifier = MagicMock(return_value=VerificationResult(passed=False, gate_result="fail", findings=["nope"]))
    res = generate_reasoned_code(
        function="code_translation", request=_req(), router=r, verifier=verifier
    )
    assert res.passed is False
    assert res.stop_reason == rc.STOP_VERIFY_FAIL
    assert res.rounds_used == 2


def test_repair_function_falls_back_to_function_when_no_repair_route():
    r = _router(
        reasoned_cfg={"enabled": True, "per_function": {
            "code_generation": {"enabled": True, "mode": "cot", "max_repair_rounds": 2}}},
        routing={},  # no code_generation_repair key
    )
    r.invoke.return_value = _resp("repaired via same fn")
    verifier = MagicMock(side_effect=[
        VerificationResult(passed=False, gate_result="fail", findings=["x"]),
        VerificationResult(passed=True, gate_result="pass"),
    ])
    res = generate_reasoned_code(
        function="code_generation", request=_req(), router=r, verifier=verifier
    )
    assert r.invoke.call_args[0][0] == "code_generation"  # fell back to the function itself
    assert res.passed is True


# ---------------------------------------------------------------------------
# Budget abort
# ---------------------------------------------------------------------------
def test_budget_abort_on_token_cap():
    r = _router(reasoned_cfg={
        "enabled": True,
        "token_cap": 5,  # generation (10+20 tokens) already exceeds this
        "per_function": {"code_translation": {"enabled": True, "mode": "cot", "max_repair_rounds": 3}},
    })
    verifier = MagicMock(return_value=VerificationResult(passed=False, gate_result="fail", findings=["x"]))
    res = generate_reasoned_code(
        function="code_translation", request=_req(), router=r, verifier=verifier
    )
    assert res.stop_reason == rc.STOP_BUDGET
    r.invoke.assert_not_called()  # never spent a repair call


# ---------------------------------------------------------------------------
# Critique veto short-circuit
# ---------------------------------------------------------------------------
def test_critique_nogo_vetoes():
    r = _router(reasoned_cfg={
        "enabled": True,
        "per_function": {"code_generation": {"enabled": True, "mode": "cot", "critique": True}},
    })
    fake_critique = MagicMock()
    fake_critique.run_critique.return_value = {
        "consensus": "nogo", "session_id": "sess-1", "findings": [
            {"severity": "critical", "title": "secret leak", "suggested_fix": "remove key"}]}
    with patch("tools.agent.anvil_critique.AtlasCritique", return_value=fake_critique):
        res = generate_reasoned_code(function="code_generation", request=_req(), router=r)
    assert res.stop_reason == rc.STOP_VETO
    assert res.passed is False
    assert res.critique_consensus == "nogo"
    assert res.critique_session_id == "sess-1"


def test_critique_go_with_passing_verify_completes():
    r = _router(reasoned_cfg={
        "enabled": True,
        "per_function": {"code_generation": {"enabled": True, "mode": "cot", "critique": True}},
    })
    fake_critique = MagicMock()
    fake_critique.run_critique.return_value = {"consensus": "go", "session_id": "s2", "findings": []}
    verifier = MagicMock(return_value=VerificationResult(passed=True, gate_result="pass"))
    with patch("tools.agent.anvil_critique.AtlasCritique", return_value=fake_critique):
        res = generate_reasoned_code(
            function="code_generation", request=_req(), router=r, verifier=verifier)
    assert res.stop_reason == rc.STOP_COMPLETED
    assert res.passed is True
    assert res.critique_consensus == "go"


# ---------------------------------------------------------------------------
# Generation exceptions propagate (drop-in parity with router.invoke)
# ---------------------------------------------------------------------------
def test_generation_exception_propagates():
    r = _router(reasoned_cfg={"enabled": True, "per_function": {
        "code_translation": {"enabled": True, "mode": "cot"}}})
    r.invoke_chain_of_thought.side_effect = RuntimeError("no LLM")
    with pytest.raises(RuntimeError):
        generate_reasoned_code(function="code_translation", request=_req(), router=r)
