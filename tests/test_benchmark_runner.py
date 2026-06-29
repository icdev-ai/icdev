from __future__ import annotations

"""Tests for icdev/tools/llm/benchmark_runner.py and _compute_auroc in eval_harness.py."""

from unittest.mock import MagicMock

import pytest


# ---------------------------------------------------------------------------
# Extractor tests
# ---------------------------------------------------------------------------

def test_extract_last_number_integer():
    from icdev.tools.llm.benchmark_runner import extract_last_number
    assert extract_last_number("The answer is 42") == "42"


def test_extract_last_number_with_junk():
    from icdev.tools.llm.benchmark_runner import extract_last_number
    assert extract_last_number("Step 1 gives 10, final answer is 99") == "99"


def test_extract_last_number_float():
    from icdev.tools.llm.benchmark_runner import extract_last_number
    assert extract_last_number("result: 3.14") == "3.14"


def test_extract_last_number_no_number():
    from icdev.tools.llm.benchmark_runner import extract_last_number
    assert extract_last_number("no digits here") == ""


def test_extract_boxed_simple():
    from icdev.tools.llm.benchmark_runner import extract_boxed
    assert extract_boxed(r"The answer is \boxed{x^2+1}") == "x^2+1"


def test_extract_boxed_missing():
    from icdev.tools.llm.benchmark_runner import extract_boxed
    assert extract_boxed("no boxed here") == ""


def test_extract_boxed_multiple_takes_last():
    from icdev.tools.llm.benchmark_runner import extract_boxed
    assert extract_boxed(r"\boxed{3} and \boxed{7}") == "7"


def test_extract_code_block_python():
    from icdev.tools.llm.benchmark_runner import extract_code_block
    text = "```python\nprint('hi')\n```"
    assert extract_code_block(text) == "print('hi')"


def test_extract_code_block_missing():
    from icdev.tools.llm.benchmark_runner import extract_code_block
    assert extract_code_block("no code here") == ""


def test_extract_raw_passthrough():
    from icdev.tools.llm.benchmark_runner import extract_raw
    assert extract_raw("  hello world  ") == "  hello world  "


# ---------------------------------------------------------------------------
# BenchmarkRunner.score
# ---------------------------------------------------------------------------

def _make_result(correct: bool, benchmark_name: str = "gsm8k") -> object:
    from icdev.tools.llm.benchmark_runner import BenchmarkResult
    return BenchmarkResult(
        benchmark_name=benchmark_name,
        sample_id="s1",
        prompt="q",
        response="r",
        predicted_answer="42",
        reference_answer="42",
        correct=correct,
        latency_ms=100.0,
        model_id="test-model",
        timestamp="2026-01-01T00:00:00+00:00",
    )


def test_score_five_correct_out_of_ten():
    from icdev.tools.llm.benchmark_runner import BenchmarkRunner
    runner = BenchmarkRunner.__new__(BenchmarkRunner)
    results = [_make_result(i < 5) for i in range(10)]
    score = runner.score(results)
    assert score["accuracy"] == pytest.approx(0.5)
    assert score["correct"] == 5
    assert score["total"] == 10


def test_score_all_correct():
    from icdev.tools.llm.benchmark_runner import BenchmarkRunner
    runner = BenchmarkRunner.__new__(BenchmarkRunner)
    results = [_make_result(True) for _ in range(4)]
    score = runner.score(results)
    assert score["accuracy"] == pytest.approx(1.0)


def test_score_empty():
    from icdev.tools.llm.benchmark_runner import BenchmarkRunner
    runner = BenchmarkRunner.__new__(BenchmarkRunner)
    score = runner.score([])
    assert score["total"] == 0
    assert score["accuracy"] == 0.0


# ---------------------------------------------------------------------------
# BenchmarkRunner.run_sample (mocked LLMRouter)
# ---------------------------------------------------------------------------

def _make_runner_with_mock(response_text: str, model_id: str = "mock-model"):
    from icdev.tools.llm.benchmark_runner import BenchmarkRunner
    from icdev.tools.llm.provider import LLMResponse
    runner = BenchmarkRunner.__new__(BenchmarkRunner)
    mock_router = MagicMock()
    mock_response = LLMResponse(content=response_text, model_id=model_id)
    mock_router.invoke.return_value = mock_response
    runner._router = mock_router
    runner._llm_function = "benchmark_eval"
    return runner


def test_run_sample_correct_gsm8k():
    from icdev.tools.llm.benchmark_runner import BenchmarkSample
    runner = _make_runner_with_mock("The answer is #### 42")
    sample = BenchmarkSample(id="s1", prompt="What is 6*7?", reference_answer="42")
    result = runner.run_sample(sample, "gsm8k")
    assert result.predicted_answer == "42"
    assert result.correct is True
    assert result.model_id == "mock-model"


def test_run_sample_wrong_answer():
    from icdev.tools.llm.benchmark_runner import BenchmarkSample
    runner = _make_runner_with_mock("The answer is 99")
    sample = BenchmarkSample(id="s1", prompt="q", reference_answer="42")
    result = runner.run_sample(sample, "gsm8k")
    assert result.correct is False


def test_run_sample_humaneval_code_block():
    from icdev.tools.llm.benchmark_runner import BenchmarkSample
    runner = _make_runner_with_mock("```python\nreturn x + 1\n```")
    sample = BenchmarkSample(id="he1", prompt="Write a function", reference_answer="return x + 1")
    result = runner.run_sample(sample, "humaneval")
    assert result.predicted_answer == "return x + 1"
    assert result.correct is True


# ---------------------------------------------------------------------------
# _compute_auroc (eval_harness)
# ---------------------------------------------------------------------------

def _row(confidence: float, outcome: str) -> dict:
    return {"confidence": confidence, "actual_outcome": outcome}


def test_compute_auroc_perfect_classifier():
    from icdev.tools.genesis.harness.eval_harness import _compute_auroc
    rows = (
        [_row(0.9, "resolved")] * 5 +
        [_row(0.1, "false_positive")] * 5
    )
    result = _compute_auroc(rows)
    assert result is not None
    assert result == pytest.approx(1.0, abs=1e-9)


def test_compute_auroc_random_classifier():
    from icdev.tools.genesis.harness.eval_harness import _compute_auroc
    # Alternating — should be close to 0.5
    rows = [_row(0.5, "resolved"), _row(0.5, "false_positive")] * 10
    result = _compute_auroc(rows)
    assert result is not None
    assert 0.4 <= result <= 0.6


def test_compute_auroc_inverse_classifier():
    from icdev.tools.genesis.harness.eval_harness import _compute_auroc
    rows = (
        [_row(0.1, "resolved")] * 5 +
        [_row(0.9, "false_positive")] * 5
    )
    result = _compute_auroc(rows)
    assert result is not None
    assert result == pytest.approx(0.0, abs=1e-9)


def test_compute_auroc_too_few_rows():
    from icdev.tools.genesis.harness.eval_harness import _compute_auroc
    rows = [_row(0.8, "resolved")] * 3
    assert _compute_auroc(rows) is None


def test_compute_auroc_single_class():
    from icdev.tools.genesis.harness.eval_harness import _compute_auroc
    rows = [_row(0.8, "resolved")] * 10
    assert _compute_auroc(rows) is None


# ---------------------------------------------------------------------------
# BenchmarkDataNotFoundError
# ---------------------------------------------------------------------------

def test_benchmark_data_not_found_error_message():
    from icdev.tools.llm.benchmark_runner import BenchmarkDataNotFoundError
    exc = BenchmarkDataNotFoundError("gsm8k")
    assert "gsm8k" in str(exc)
    assert "DeepSpec" in str(exc)
    assert "--data-path" in str(exc)


# ---------------------------------------------------------------------------
# Registry sanity
# ---------------------------------------------------------------------------

def test_registry_contains_expected_benchmarks():
    from icdev.tools.llm.benchmark_runner import _REGISTRY
    expected = {"gsm8k", "math500", "humaneval", "mbpp", "aime24", "aime25", "mt-bench", "alpaca"}
    assert expected.issubset(set(_REGISTRY.keys()))


def test_registry_extractor_types_valid():
    from icdev.tools.llm.benchmark_runner import _REGISTRY, _EXTRACTORS
    for cfg in _REGISTRY.values():
        assert cfg.answer_extractor_type in _EXTRACTORS, (
            f"{cfg.name} has unknown extractor {cfg.answer_extractor_type}"
        )
