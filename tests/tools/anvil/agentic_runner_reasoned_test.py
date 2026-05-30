# CUI // SP-CTI
"""Phase 2: reasoned-codegen wiring in the ANVIL agentic_runner.

Covers the --reasoned option + advisor resolution and that _llm_call routes
each turn through the reasoned_codegen wrapper. No real LLM calls.
"""

from unittest.mock import MagicMock, patch

from tools.anvil import agentic_runner as ar
from tools.llm.reasoned_codegen import MODE_OFF, MODE_COT, MODE_COD


def _router(section_on=True):
    router = MagicMock()
    router._config = {"reasoned_codegen": {"enabled": section_on}}
    return router


# ---------------------------------------------------------------------------
# Kill-switch
# ---------------------------------------------------------------------------
def test_section_killswitch_forces_off():
    router = _router(section_on=False)
    mode = ar._resolve_reasoned_mode(router, "on", "fix", "complex auth with crypto and nist audit")
    assert mode == MODE_OFF


# ---------------------------------------------------------------------------
# Option resolution
# ---------------------------------------------------------------------------
def test_reasoned_off_forces_off():
    assert ar._resolve_reasoned_mode(_router(), "off", "fix", "anything") == MODE_OFF


def test_reasoned_auto_uses_advisor():
    router = _router()
    with patch("tools.llm.reasoned_codegen_advisor.recommend",
               return_value={"mode": MODE_COD, "rationale": "risky"}):
        mode = ar._resolve_reasoned_mode(router, "auto", "feature", "secure auth subsystem")
    assert mode == MODE_COD


def test_reasoned_on_upgrades_advisor_off_to_cot():
    """--reasoned on must never resolve to off, even if advisor says off."""
    router = _router()
    with patch("tools.llm.reasoned_codegen_advisor.recommend",
               return_value={"mode": MODE_OFF, "rationale": "trivial"}):
        mode = ar._resolve_reasoned_mode(router, "on", "chore", "fix a typo")
    assert mode == MODE_COT


def test_advisor_failure_falls_back_by_option():
    router = _router()
    with patch("tools.llm.reasoned_codegen_advisor.recommend", side_effect=RuntimeError("down")):
        assert ar._resolve_reasoned_mode(router, "on", "fix", "x") == MODE_COT
        assert ar._resolve_reasoned_mode(router, "auto", "fix", "x") == MODE_OFF


# ---------------------------------------------------------------------------
# _llm_call routes through the wrapper
# ---------------------------------------------------------------------------
def test_llm_call_routes_through_wrapper():
    router = MagicMock()
    fake = MagicMock(code="{\"tool\": \"done\", \"args\": {}}")
    with patch("tools.llm.reasoned_codegen.generate_reasoned_code", return_value=fake) as gen:
        out = ar._llm_call(router, [{"role": "user", "content": "go"}], mode=MODE_COT)
    assert out == "{\"tool\": \"done\", \"args\": {}}"
    assert gen.call_args.kwargs["function"] == "code_generation"
    assert gen.call_args.kwargs["mode"] == MODE_COT
