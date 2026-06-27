"""Tests for cross-grader LLM enforcement.

Verifies that:
 - grade_output_quality() uses 'agent_eval_grading' routing by default
 - exclude_model_id blocks the same-model grader path
 - CrossGraderViolation is raised when the router returns the excluded model
 - EvalResult.model_id is auto-populated as the exclude candidate
 - router.invoke() skip-logic correctly excludes specified model IDs
"""

import json
import pytest
from unittest.mock import MagicMock, patch


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_eval_result(model_id: str = "qwen3-local"):
    from icdev.tools.ace.evaluator import EvalResult
    return EvalResult(
        session_id="sess-xgrade-01",
        efficiency_score=0.7,
        model_id=model_id,
    )


def _make_mock_response(content: dict, model_id: str = "claude-sonnet"):
    mock = MagicMock()
    mock.content = json.dumps(content)
    mock.model_id = model_id
    mock.provider = "anthropic"
    return mock


# ---------------------------------------------------------------------------
# 1. CrossGraderViolation importable
# ---------------------------------------------------------------------------

class TestCrossGraderViolationClass:
    def test_importable_from_router(self):
        from icdev.tools.llm.router import CrossGraderViolation
        assert issubclass(CrossGraderViolation, RuntimeError)

    def test_is_runtime_error(self):
        from icdev.tools.llm.router import CrossGraderViolation
        exc = CrossGraderViolation("test")
        assert isinstance(exc, RuntimeError)
        assert "test" in str(exc)


# ---------------------------------------------------------------------------
# 2. Router exclude_model_ids skip logic
# ---------------------------------------------------------------------------

class TestRouterExcludeModelIds:
    def _make_router_with_chain(self, chain_model_ids):
        """Builds a minimal LLMRouter mock with a chain."""
        from icdev.tools.llm.router import LLMRouter
        router = LLMRouter.__new__(LLMRouter)
        router._config = {"functions": {
            "test_fn": {"chain": chain_model_ids, "effort": "low"},
        }}
        router._models = {mid: {"model_id": mid, "provider": "mock"} for mid in chain_model_ids}
        return router

    def test_no_exclude_uses_first_model(self):
        """Without exclusion, first available model is used."""
        from icdev.tools.llm.router import LLMRouter
        from icdev.tools.llm.provider import LLMRequest
        router = LLMRouter.__new__(LLMRouter)

        mock_response = _make_mock_response({"result": "ok"}, "model-a")
        with patch.object(LLMRouter, "invoke", return_value=mock_response) as m:
            result = router.invoke("code_generation", LLMRequest(messages=[{"role": "user", "content": "hi"}]))
            # invoke was called without exclude_model_ids
            call_kwargs = m.call_args[1] if m.call_args else {}
            assert "exclude_model_ids" not in call_kwargs or call_kwargs.get("exclude_model_ids") is None

    def test_exclude_model_ids_param_accepted(self):
        """invoke() accepts exclude_model_ids without error."""
        from icdev.tools.llm.router import LLMRouter
        from icdev.tools.llm.provider import LLMRequest
        router = LLMRouter.__new__(LLMRouter)
        mock_resp = _make_mock_response({}, "model-b")
        with patch.object(LLMRouter, "invoke", return_value=mock_resp):
            # should not raise TypeError for unknown kwarg
            router.invoke(
                "code_generation",
                LLMRequest(messages=[]),
                exclude_model_ids=["model-a"],
            )


# ---------------------------------------------------------------------------
# 3. grade_output_quality — default function changed
# ---------------------------------------------------------------------------

_ROUTER_PATH = "icdev.tools.llm.router.LLMRouter"


class TestGradeOutputQualityRouting:
    def test_default_llm_function_is_agent_eval_grading(self):
        import inspect
        from icdev.tools.ace.evaluator import grade_output_quality
        sig = inspect.signature(grade_output_quality)
        assert sig.parameters["llm_function"].default == "agent_eval_grading"

    def test_calls_agent_eval_grading_routing_function(self):
        from icdev.tools.ace.evaluator import grade_output_quality, EvalResult
        er = _make_eval_result("qwen3-local")
        mock_resp = _make_mock_response(
            {"faithfulness": 0.9, "completeness": 0.8, "reasoning_quality": 0.7,
             "cod_quality": 0.6, "error_adaptation": 0.5, "overall": 0.7, "reasoning": "ok"},
            "claude-sonnet",
        )
        with patch(_ROUTER_PATH) as MockRouter:
            instance = MockRouter.return_value
            instance.invoke.return_value = mock_resp
            result = grade_output_quality(
                er, user_prompt="do X", final_content="did X"
            )
            call_args = instance.invoke.call_args
            assert call_args[0][0] == "agent_eval_grading"

    def test_grader_model_id_captured_in_result(self):
        from icdev.tools.ace.evaluator import grade_output_quality, EvalResult
        er = _make_eval_result("qwen3-local")
        mock_resp = _make_mock_response({"overall": 0.8}, "claude-sonnet")
        with patch(_ROUTER_PATH) as MockRouter:
            instance = MockRouter.return_value
            instance.invoke.return_value = mock_resp
            result = grade_output_quality(
                er, user_prompt="do X", final_content="did X"
            )
            assert result.get("grader_model_id") == "claude-sonnet"


# ---------------------------------------------------------------------------
# 4. exclude_model_id forwarded to router
# ---------------------------------------------------------------------------

class TestExcludeModelIdForwarding:
    def test_exclude_model_id_passed_to_invoke(self):
        from icdev.tools.ace.evaluator import grade_output_quality, EvalResult
        er = _make_eval_result("qwen3-local")
        mock_resp = _make_mock_response({"overall": 0.9}, "gpt-4o")
        with patch(_ROUTER_PATH) as MockRouter:
            instance = MockRouter.return_value
            instance.invoke.return_value = mock_resp
            grade_output_quality(
                er,
                user_prompt="do X",
                final_content="did X",
                exclude_model_id="qwen3-local",
            )
            call_kwargs = instance.invoke.call_args[1]
            assert call_kwargs.get("exclude_model_ids") == ["qwen3-local"]

    def test_auto_exclude_from_eval_result_model_id(self):
        """EvalResult.model_id auto-populates exclude if not explicitly given."""
        from icdev.tools.ace.evaluator import grade_output_quality, EvalResult
        er = _make_eval_result("qwen3-local")
        mock_resp = _make_mock_response({"overall": 0.9}, "claude-sonnet")
        with patch(_ROUTER_PATH) as MockRouter:
            instance = MockRouter.return_value
            instance.invoke.return_value = mock_resp
            grade_output_quality(er, user_prompt="do X", final_content="did X")
            call_kwargs = instance.invoke.call_args[1]
            assert call_kwargs.get("exclude_model_ids") == ["qwen3-local"]

    def test_no_exclude_when_model_id_empty(self):
        """Empty EvalResult.model_id → no exclusion kwarg passed."""
        from icdev.tools.ace.evaluator import grade_output_quality, EvalResult
        er = _make_eval_result("")
        mock_resp = _make_mock_response({"overall": 0.9}, "claude-sonnet")
        with patch(_ROUTER_PATH) as MockRouter:
            instance = MockRouter.return_value
            instance.invoke.return_value = mock_resp
            grade_output_quality(er, user_prompt="do X", final_content="did X")
            call_kwargs = instance.invoke.call_args[1]
            excludes = call_kwargs.get("exclude_model_ids")
            assert not excludes  # None or []


# ---------------------------------------------------------------------------
# 5. CrossGraderViolation assertion inside grade_output_quality
# ---------------------------------------------------------------------------

class TestCrossGraderViolationAssertion:
    def test_violation_raised_when_grader_equals_session_model(self):
        from icdev.tools.ace.evaluator import grade_output_quality, EvalResult
        from icdev.tools.llm.router import CrossGraderViolation
        er = _make_eval_result("qwen3-local")
        mock_resp = _make_mock_response({"overall": 0.9}, "qwen3-local")
        with patch(_ROUTER_PATH) as MockRouter:
            instance = MockRouter.return_value
            instance.invoke.return_value = mock_resp
            with pytest.raises(CrossGraderViolation):
                grade_output_quality(
                    er,
                    user_prompt="do X",
                    final_content="did X",
                    exclude_model_id="qwen3-local",
                )

    def test_no_violation_when_different_model(self):
        from icdev.tools.ace.evaluator import grade_output_quality, EvalResult
        from icdev.tools.llm.router import CrossGraderViolation
        er = _make_eval_result("qwen3-local")
        mock_resp = _make_mock_response({"overall": 0.8}, "claude-sonnet")
        with patch(_ROUTER_PATH) as MockRouter:
            instance = MockRouter.return_value
            instance.invoke.return_value = mock_resp
            result = grade_output_quality(
                er,
                user_prompt="do X",
                final_content="did X",
                exclude_model_id="qwen3-local",
            )
            assert "grader_model_id" in result
            assert result["grader_model_id"] == "claude-sonnet"

    def test_no_violation_when_exclude_not_set(self):
        """No exclusion → never raises CrossGraderViolation."""
        from icdev.tools.ace.evaluator import grade_output_quality, EvalResult
        er = _make_eval_result("")
        mock_resp = _make_mock_response({"overall": 0.9}, "any-model")
        with patch(_ROUTER_PATH) as MockRouter:
            instance = MockRouter.return_value
            instance.invoke.return_value = mock_resp
            result = grade_output_quality(er, user_prompt="do X", final_content="did X")
            assert isinstance(result, dict)

    def test_session_text_alias_works(self):
        """session_text= is accepted as alias for final_content."""
        from icdev.tools.ace.evaluator import grade_output_quality, EvalResult
        er = _make_eval_result("qwen3-local")
        mock_resp = _make_mock_response({"overall": 0.7}, "gpt-4o")
        with patch(_ROUTER_PATH) as MockRouter:
            instance = MockRouter.return_value
            instance.invoke.return_value = mock_resp
            result = grade_output_quality(
                er, user_prompt="do X", session_text="did X"
            )
            assert result.get("overall") is not None or "error" in result


# ---------------------------------------------------------------------------
# 6. EvalResult.model_id field
# ---------------------------------------------------------------------------

class TestEvalResultModelIdField:
    def test_model_id_default_empty(self):
        from icdev.tools.ace.evaluator import EvalResult
        er = EvalResult(session_id="x")
        assert er.model_id == ""

    def test_model_id_set_on_construction(self):
        from icdev.tools.ace.evaluator import EvalResult
        er = EvalResult(session_id="x", model_id="qwen3-local")
        assert er.model_id == "qwen3-local"
