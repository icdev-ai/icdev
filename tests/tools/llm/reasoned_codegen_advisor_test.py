# CUI // SP-CTI
"""Unit tests for the reasoned_codegen advisor.

No real LLM calls — the LLM-refine path is mocked. Heuristic recommendations
are deterministic and air-gap safe.
"""

from unittest.mock import MagicMock

from tools.llm.provider import LLMResponse
from tools.llm import reasoned_codegen_advisor as adv
from tools.llm.reasoned_codegen import MODE_OFF, MODE_COT, MODE_COD


# ---------------------------------------------------------------------------
# Heuristic path (use_llm=False → zero-cost, deterministic)
# ---------------------------------------------------------------------------
def test_trivial_task_recommends_off():
    r = adv.recommend("code_generation", "Fix a typo in a docstring comment", use_llm=False)
    assert r["recommended"] is False
    assert r["mode"] == MODE_OFF
    assert r["source"] == "heuristic"


def test_security_compliance_task_escalates_with_critique():
    spec = (
        "Implement password authentication with token-based sessions. Encrypt "
        "secrets at rest. Must satisfy NIST 800-53 AC controls and emit a CUI "
        "audit trail. Add the auth endpoint, the session schema migration, and "
        "RBAC checks across multiple modules.\n"
        "- validate credentials\n- rotate tokens\n- log to audit\n- enforce rbac\n"
    )
    r = adv.recommend("code_generation", spec, context={"file_count": 6}, use_llm=False)
    assert r["recommended"] is True
    assert r["critique"] is True
    assert r["mode"] in (MODE_COT, MODE_COD)
    assert r["signals"]["security_hits"]
    assert r["signals"]["compliance_hits"]


def test_high_complexity_reaches_debate():
    spec = (
        "Build a distributed transaction coordinator with a state machine, async "
        "concurrency control, a custom protocol parser, and a recursion-based "
        "algorithm. Handle race conditions and optimize the schema and api. "
        "Add migration and endpoint and table changes.\n"
        + "\n".join(f"- requirement {i}" for i in range(12))
    )
    r = adv.recommend("code_generation", spec, context={"file_count": 12, "past_failures": 2}, use_llm=False)
    assert r["recommended"] is True
    assert r["mode"] == MODE_COD
    assert r["signals"]["score"] >= 0.65


def test_no_llm_mode_falls_back_to_heuristic():
    """When the router is in no-LLM mode, refine is skipped and heuristic stands."""
    router = MagicMock()
    router.is_no_llm_mode.return_value = True
    r = adv.recommend("code_generation", "complex schema migration with api endpoints and tables",
                      context={"file_count": 4}, router=router, use_llm=True)
    assert r["source"] == "heuristic"
    router.invoke.assert_not_called()


# ---------------------------------------------------------------------------
# LLM refine path (mocked)
# ---------------------------------------------------------------------------
def test_llm_refine_overrides_when_available():
    router = MagicMock()
    router.is_no_llm_mode.return_value = False
    router.invoke.return_value = LLMResponse(
        content="",
        structured_output={"recommended": True, "mode": MODE_COD, "critique": True,
                            "confidence": 0.9, "rationale": "LLM says debate it"},
    )
    r = adv.recommend("code_generation", "moderately complex api work with a schema",
                      context={"file_count": 3}, router=router, use_llm=True)
    assert r["source"] == "llm"
    assert r["mode"] == MODE_COD
    assert r["rationale"] == "LLM says debate it"
    router.invoke.assert_called_once()


def test_llm_refine_failure_keeps_heuristic():
    router = MagicMock()
    router.is_no_llm_mode.return_value = False
    router.invoke.side_effect = RuntimeError("provider down")
    r = adv.recommend("code_generation", "fix a typo", router=router, use_llm=True)
    assert r["source"] == "heuristic"
    assert r["mode"] == MODE_OFF


def test_llm_invalid_mode_rejected():
    router = MagicMock()
    router.is_no_llm_mode.return_value = False
    router.invoke.return_value = LLMResponse(
        content="", structured_output={"recommended": True, "mode": "bogus", "critique": True,
                                        "rationale": "x"})
    r = adv.recommend("code_generation", "complex distributed algorithm with concurrency",
                      context={"file_count": 8}, router=router, use_llm=True)
    # Invalid LLM mode → discard, heuristic baseline stands.
    assert r["source"] == "heuristic"


def test_llm_parses_json_from_content_when_no_structured_output():
    router = MagicMock()
    router.is_no_llm_mode.return_value = False
    router.invoke.return_value = LLMResponse(
        content='```json\n{"recommended": true, "mode": "cot", "critique": false, '
                '"confidence": 0.7, "rationale": "parsed from text"}\n```',
        structured_output=None,
    )
    r = adv.recommend("code_generation", "add an api endpoint with a schema change",
                      context={"file_count": 2}, router=router, use_llm=True)
    assert r["source"] == "llm"
    assert r["mode"] == MODE_COT
    assert r["rationale"] == "parsed from text"
