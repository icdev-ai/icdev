# CUI // SP-CTI
"""m12-model-serving (aicur-fun-02): Tier-1 mission on inference serving and routing.

Pins that the mission is catalogued with its prerequisite, that discovery wires all
three steps (two lessons, one coding lab), that both lessons are GRADED by a valid
item bank rather than acknowledged, and that the routing lab's grader discriminates:
a correct resolver passes in the real sandbox, the untouched starter fails, and a
resolver that drops over-budget models instead of demoting them fails.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from apps.forge_academy.assessment import validate_item_bank
from apps.forge_academy.code_runner import run_code
from apps.forge_academy.content_loader import (
    BUILTIN_MISSIONS,
    CONTENT_ROOT,
    discover_steps,
    load_item_banks,
)

SLUG = "m12-model-serving"
STEPS_DIR = CONTENT_ROOT / "tier1" / SLUG / "steps"

# A reference solution to the step-3 lab. Kept here, not beside the content, so the
# answer is never served to the learner it grades.
_SOLUTION = '''
def resolve_chain(config, function, health, budget_exhausted):
    routing = config["routing"]
    route = routing.get(function) or routing["default"]
    models = config["models"]
    chain = [m for m in route["chain"] if m in models]
    if budget_exhausted:
        ceiling = config["cost_budget"]["downgrade"]["max_blended_per_1k"]
        def over(name):
            price = models[name].get("blended_per_1k")
            return price is None or price > ceiling
        chain = [m for m in chain if not over(m)] + [m for m in chain if over(m)]
    rank = {"healthy": 0, "recovering": 1, "degraded": 2}
    def state(name):
        return rank.get(health.get(models[name]["provider"], "healthy"), 0)
    return sorted(chain, key=state)


def pick_model(config, function, health, budget_exhausted):
    for name in resolve_chain(config, function, health, budget_exhausted):
        if health.get(config["models"][name]["provider"]) != "degraded":
            return name
    return None
'''


@pytest.fixture(scope="module")
def steps():
    return discover_steps().get(SLUG) or []


def _starter() -> str:
    return (STEPS_DIR / "step3_starter.py").read_text(encoding="utf-8")


def _grader() -> str:
    return (STEPS_DIR / "step3_test.py").read_text(encoding="utf-8")


def test_mission_is_catalogued_as_tier1_with_m01_prereq():
    entry = next((m for m in BUILTIN_MISSIONS if m["slug"] == SLUG), None)
    assert entry is not None, f"{SLUG} missing from BUILTIN_MISSIONS"
    assert entry["tier"] == 1
    assert entry["prereqs"] == ["m01-llm-fundamentals"]


def test_discovery_wires_two_lessons_and_a_coding_lab(steps):
    by_num = {s["step_num"]: s for s in steps}
    assert sorted(by_num) == [1, 2, 3]
    assert by_num[1]["step_type"] == "watch"
    assert by_num[2]["step_type"] == "watch"
    lab = by_num[3]
    assert lab["step_type"] == "coding"
    assert lab["test_code_path"].endswith("step3_test.py")
    assert lab["starter_code_path"].endswith("step3_starter.py")
    assert lab["title"] == "Lab: Multi-Model Routing"


def test_every_lesson_step_has_a_valid_item_bank():
    bank = load_item_banks().get(SLUG) or {}
    assert sorted(bank) == [1, 2], "steps 1 and 2 must each carry an item bank"
    for step_num, items in bank.items():
        assert validate_item_bank(items) == [], (step_num, validate_item_bank(items))


def test_lessons_date_their_facts():
    for name in ("step1_inference_server_anatomy.md", "step2_vllm_in_practice.md"):
        text = (STEPS_DIR / name).read_text(encoding="utf-8")
        assert "as of October 2026" in text, f"{name} must date its facts"


def test_reference_solution_passes_the_grader_in_the_sandbox():
    result = run_code(_starter() + "\n" + _SOLUTION, test_code=_grader())
    assert result.get("error") != "blocked", result
    assert result["passed"] is True, result["stderr"]
    assert "PASS" in result["stdout"]


def test_untouched_starter_fails_on_its_merits():
    result = run_code(_starter(), test_code=_grader())
    assert result.get("error") != "blocked", "must fail on its merits, not the gate"
    assert result["passed"] is False


def test_resolver_that_drops_over_budget_models_fails():
    """The lesson's rule is DEMOTE, never drop — a dropped model makes a cheap-tier
    outage a total outage. A grader that let this pass would not test the rule."""
    dropping = _SOLUTION.replace(
        "chain = [m for m in chain if not over(m)] + [m for m in chain if over(m)]",
        "chain = [m for m in chain if not over(m)]",
    )
    assert dropping != _SOLUTION
    result = run_code(_starter() + "\n" + dropping, test_code=_grader())
    assert result["passed"] is False


def test_lab_points_at_the_real_router_seams():
    text = (STEPS_DIR / "step3_multi_model_routing.md").read_text(encoding="utf-8")
    repo = Path(__file__).resolve().parent.parent
    for seam in ("tools/llm/router.py", "tools/llm/routing_policy.py",
                 "tools/llm/provider_health.py", "tools/llm/cost_budget.py"):
        assert seam in text, f"lab must name {seam}"
        assert (repo / seam).is_file(), f"{seam} named by the lab does not exist"
