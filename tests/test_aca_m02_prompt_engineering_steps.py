"""aicur-fun-04 — m02-prompt-engineering loads five graded steps.

Before this card the mission discovered only steps 1-2: step4 had a starter and a
test but no ``step4_*.md``, and discovery keys on markdown frontmatter, so the lab
was silently skipped. There was no step 3 or 5 at all.
"""
from __future__ import annotations

from apps.forge_academy import content_loader
from apps.forge_academy.assessment import validate_item_bank
from apps.forge_academy.code_runner import run_code

SLUG = "m02-prompt-engineering"
STEPS_DIR = content_loader.CONTENT_ROOT / "tier1" / SLUG / "steps"


def _m02_steps() -> dict:
    return {s["step_num"]: s for s in content_loader.discover_steps().get(SLUG, [])}


def test_m02_discovers_five_steps():
    assert sorted(_m02_steps()) == [1, 2, 3, 4, 5]


def test_step4_is_a_graded_coding_lab():
    step4 = _m02_steps()[4]
    assert step4["step_type"] == "coding"
    assert step4["test_code_path"].endswith("step4_test.py")
    assert step4["starter_code_path"].endswith("step4_starter.py")
    assert step4["title"] == "Structured Output Enforcement"


def test_new_lesson_steps_are_graded_by_a_valid_bank():
    by_step = content_loader.load_item_banks()[SLUG]
    # Steps 1, 2 and 4 are labs; the two prose lessons are graded by the bank.
    assert set(by_step) == {3, 5}
    for step_num, items in by_step.items():
        assert not validate_item_bank(items), f"step {step_num}: {validate_item_bank(items)}"
        assert len({i["correct_index"] for i in items}) > 1


def _solve_step4() -> str:
    starter = (STEPS_DIR / "step4_starter.py").read_text(encoding="utf-8")
    solution = '''
    prompt = (
        "Extract the contract award as JSON. Respond with ONLY a JSON object.\\n"
        'Schema: {"name": string, "naics": string, "value_usd": number, "status": string}\\n'
        'Example: {"name": "Example LLC", "naics": "541512", "value_usd": 1, "status": "award"}\\n'
        "<document>\\n" + raw_sam_text + "\\n</document>"
    )
    raw = simulate_llm_extract(prompt, raw_sam_text)
    print(raw)
    data = parse_llm_output(raw)
    print(data)
    return data
'''
    assert "    # YOUR CODE HERE\n    pass\n" in starter
    return starter.replace("    # YOUR CODE HERE\n    pass\n", solution, 1)


def test_step4_reference_solution_passes_the_grader_offline():
    test_code = (STEPS_DIR / "step4_test.py").read_text(encoding="utf-8")
    result = run_code(_solve_step4(), test_code=test_code)
    assert result["passed"], result
    assert "PASS: Structured output enforced" in result["stdout"]


def test_step4_unsolved_starter_fails_the_grader():
    test_code = (STEPS_DIR / "step4_test.py").read_text(encoding="utf-8")
    starter = (STEPS_DIR / "step4_starter.py").read_text(encoding="utf-8")
    assert not run_code(starter, test_code=test_code)["passed"]


def test_every_m02_lesson_is_dated():
    for name in ("step3_system_prompts.md", "step4_structured_output.md",
                 "step5_reasoning_and_tools.md"):
        assert "as of October 2026" in (STEPS_DIR / name).read_text(encoding="utf-8").replace("As of", "as of")
