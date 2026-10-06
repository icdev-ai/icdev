# CUI // SP-CTI
"""aicur-fun-03 -- FORGE Academy m13-benchmarks-evals is catalogued and GRADED.

Every step must be graded, not acknowledged: steps 1-2 by an item bank, step 3 by a
coding lab whose test is run server-side in the code_runner sandbox. The lab test
must reject the unmodified starter and accept a correct solution -- a grader that
passes both is decoration.
"""
from __future__ import annotations

import re

from apps.forge_academy import code_runner
from apps.forge_academy.assessment import validate_item_bank
from apps.forge_academy.content_loader import (
    BUILTIN_MISSIONS,
    CONTENT_ROOT,
    discover_steps,
    load_item_banks,
    load_starter_code,
    load_test_code,
)

SLUG = "m13-benchmarks-evals"

_REFERENCE_SOLUTION = '''
import math


def pass_at_k(n, c, k):
    if k < 1 or k > n:
        raise ValueError("k must be in 1..n")
    if n - c < k:
        return 1.0
    return 1.0 - math.comb(n - c, k) / math.comb(n, k)


def mean_pass_at_k(results, k):
    scores = [pass_at_k(len(s), sum(1 for x in s if x), k) for s in results.values()]
    return sum(scores) / len(scores)


def normalize(text):
    return " ".join(text.split()).lower()


def score_golden_set(rows):
    n = len(rows)
    correct = sum(1 for r in rows if normalize(r["output"]) == normalize(r["expected"]))
    total = sum(r["cost_usd"] for r in rows)
    return {
        "n": n,
        "correct": correct,
        "accuracy": correct / n,
        "mean_latency_ms": sum(r["latency_ms"] for r in rows) / n,
        "total_cost_usd": total,
        "cost_per_correct_usd": total / correct if correct else float("inf"),
    }
'''


def _steps():
    return discover_steps().get(SLUG, [])


def test_mission_is_catalogued_tier1_with_m01_prereq():
    entry = next((m for m in BUILTIN_MISSIONS if m["slug"] == SLUG), None)
    assert entry is not None, f"{SLUG} missing from BUILTIN_MISSIONS"
    assert entry["tier"] == 1
    assert entry["prereqs"] == ["m01-llm-fundamentals"]


def test_three_steps_are_discovered_with_the_lab_as_coding():
    steps = _steps()
    assert [s["step_num"] for s in steps] == [1, 2, 3]
    by_num = {s["step_num"]: s for s in steps}
    assert by_num[1]["step_type"] == "watch"
    assert by_num[2]["step_type"] == "watch"
    lab = by_num[3]
    assert lab["step_type"] == "coding"
    assert lab["starter_code_path"].endswith("step3_starter.py")
    assert lab["test_code_path"].endswith("step3_test.py")


def test_lesson_steps_have_valid_item_banks():
    by_step = load_item_banks().get(SLUG)
    assert by_step is not None, "m13 item bank not loaded"
    assert set(by_step) == {1, 2}
    for step_num, items in by_step.items():
        assert not validate_item_bank(items), f"step {step_num}: {validate_item_bank(items)}"
        assert len({i["correct_index"] for i in items}) > 1, "answer is always in one position"
        # The mission's habit: each bank asks what a score does NOT tell you.
        assert any("NOT tell you" in i["prompt"] for i in items), f"step {step_num} has no 'NOT tell you' item"


def test_lessons_date_their_facts():
    lesson = (CONTENT_ROOT / "tier1" / SLUG / "steps" / "step1_reading_benchmarks.md").read_text(encoding="utf-8")
    assert re.search(r"as of [A-Z][a-z]+ \d{4}", lesson), "benchmark facts must be dated"


def test_the_unmodified_starter_fails_the_grader():
    steps = {s["step_num"]: s for s in _steps()}
    result = code_runner.run_code(
        load_starter_code(steps[3]["starter_code_path"]),
        test_code=load_test_code(steps[3]["test_code_path"]),
    )
    assert result.get("passed") is False, result


def test_a_correct_solution_passes_the_grader_offline():
    steps = {s["step_num"]: s for s in _steps()}
    starter = load_starter_code(steps[3]["starter_code_path"])
    # Keep the starter's fixtures, replace its stubs with the reference solution.
    fixtures = starter.split("def pass_at_k", 1)[0]
    result = code_runner.run_code(
        fixtures + _REFERENCE_SOLUTION,
        test_code=load_test_code(steps[3]["test_code_path"]),
    )
    assert result.get("passed") is True, result
    assert "PASS" in result.get("stdout", "")


def test_an_off_by_one_pass_at_k_is_rejected():
    """pass@k computed as c/n for every k must not pass -- the grader checks k > 1."""
    steps = {s["step_num"]: s for s in _steps()}
    wrong = _REFERENCE_SOLUTION.replace(
        "return 1.0 - math.comb(n - c, k) / math.comb(n, k)", "return c / n"
    )
    result = code_runner.run_code(wrong, test_code=load_test_code(steps[3]["test_code_path"]))
    assert result.get("passed") is False, result
