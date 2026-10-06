# CUI // SP-CTI
"""m-aie-02-verification-harnesses (aicur-eng-02): catalogued, discovered, GRADED.

Topic 2b — verification harnesses — was partial (m-readiness-02's CI gate step,
m-secops-01's Bandit) and taught no TDD. This pins the mission end to end:

  * it is in the hand-written catalogue after m-aie-01, and discovery finds a
    lesson, a lab and a lesson in that order;
  * both lessons carry a valid item bank, and every item is answerable from its
    OWN lesson (the key term appears in that file);
  * the lab is a red-first check run for real in the code_runner sandbox, offline:
    the shipped starter (the agent's always-green test) FAILS, a discriminating
    test PASSES, and a test that is always red FAILS too.
"""
from __future__ import annotations

import pytest

from apps.forge_academy.assessment import validate_item_bank
from apps.forge_academy.code_runner import run_code
from apps.forge_academy.content_loader import (
    BUILTIN_MISSIONS,
    CONTENT_ROOT,
    discover_steps,
    load_item_banks,
)

SLUG = "m-aie-02-verification-harnesses"
STEPS_DIR = CONTENT_ROOT / "tier1" / SLUG / "steps"


@pytest.fixture(scope="module")
def steps() -> list:
    return discover_steps().get(SLUG) or []


def _read(rel: str) -> str:
    return (CONTENT_ROOT / rel).read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# Catalogue + discovery
# ---------------------------------------------------------------------------

def test_mission_is_catalogued_in_tier1_after_m_aie_01():
    by_slug = {m["slug"]: m for m in BUILTIN_MISSIONS}
    assert SLUG in by_slug
    mission = by_slug[SLUG]
    assert mission["tier"] == 1
    assert mission["prereqs"] == ["m-aie-01-coding-harnesses"]
    assert set(mission["prereqs"]) <= set(by_slug), "a prereq names no catalogued mission"


def test_discovery_finds_lesson_lab_lesson(steps):
    assert [s["step_num"] for s in steps] == [1, 2, 3]
    assert [s["step_type"] for s in steps] == ["watch", "coding", "watch"]
    lab = steps[1]
    assert lab["starter_code_path"].endswith("step2_starter.py")
    assert lab["test_code_path"].endswith("step2_test.py")


def test_the_lessons_are_dated(steps):
    assert "as of October 2026" in _read(steps[0]["content_path"])
    assert "as of October 2026" in _read(steps[2]["content_path"])


def test_lessons_cite_the_platforms_own_gates(steps):
    first, lab, last = (_read(s["content_path"]) for s in steps)
    assert "tools/ci/red_first_gate.py" in first
    assert "tools/ci/red_first_gate.py" in lab
    assert "tools/ci/skip_census.py" in last
    for rel in ("tools/ci/red_first_gate.py", "tools/ci/skip_census.py",
                "apps/forge_academy/code_runner.py"):
        assert (CONTENT_ROOT.parents[2] / rel).is_file(), f"lesson cites missing {rel}"


# ---------------------------------------------------------------------------
# Item bank — every lesson step is graded
# ---------------------------------------------------------------------------

def test_every_lesson_step_has_a_valid_bank(steps):
    banks = load_item_banks().get(SLUG) or {}
    lessons = [s["step_num"] for s in steps if s["step_type"] != "coding"]
    assert lessons == [1, 3]
    for num in lessons:
        items = banks.get(num) or []
        assert not validate_item_bank(items), f"step {num}: {validate_item_bank(items)}"
        assert len({i["correct_index"] for i in items}) > 1, "answer is always the same option"


# A term from each item's correct answer that its own lesson must contain. If a
# lesson is edited and drops the fact, the item becomes unanswerable — fail here.
_ANSWER_TERMS = {
    1: {
        "vh-done-is-a-claim": "generated text",
        "vh-tests-as-spec": "**specification**",
        "vh-red-step": "can detect the missing behaviour",
        "vh-not-a-test": "could never have gone red",
        "vh-red-first-gate": "**merge base**",
        "vh-static-tools": "SAST",
        "vh-hook-bypass": "--no-verify",
        "vh-sandbox": "**sandboxed**",
    },
    3: {
        "wl-exact-command": "**exact** command",
        "wl-edit-the-test": "change the expected value",
        "wl-skip-unmeasured": "unmeasured, not passing",
        "wl-or-true": "pytest -q || true",
        "wl-swallowed-exception": "persisting nothing",
        "wl-cannot-run": "exits **2**",
        "wl-disable-deliberately": "auditable switch",
        "wl-test-the-gate": "confirm it actually blocks",
    },
}


def test_every_item_is_answerable_from_its_own_lesson(steps):
    banks = load_item_banks()[SLUG]
    for num, terms in _ANSWER_TERMS.items():
        body = _read(steps[num - 1]["content_path"])
        assert {i["item_key"] for i in banks[num]} == set(terms), f"step {num} keys drifted"
        for key, term in terms.items():
            assert term in body, f"{key}: lesson {num} no longer states {term!r}"


# ---------------------------------------------------------------------------
# Lab — a red-first check graded server-side in the sandbox, offline
# ---------------------------------------------------------------------------

_AGENT_TEST = '''def test_bulk_price():
    assert bulk_price(20, 1.0) == 18.0
'''

_DISCRIMINATING = '''def test_below_threshold_pays_full_price():
    assert bulk_price(9, 2.0) == 18.0


def test_exactly_ten_gets_the_discount():
    assert bulk_price(10, 2.0) == 18.0


def test_above_threshold_gets_the_discount():
    assert bulk_price(20, 1.0) == 18.0


def test_zero_quantity_raises():
    try:
        bulk_price(0, 1.0)
    except ValueError:
        return
    raise AssertionError("expected ValueError")
'''


def _starter() -> str:
    return (STEPS_DIR / "step2_starter.py").read_text(encoding="utf-8")


def _grader() -> str:
    return (STEPS_DIR / "step2_test.py").read_text(encoding="utf-8")


def _with_tests(tests: str) -> str:
    src = _starter()
    assert _AGENT_TEST in src, "the starter's agent test drifted"
    return src.replace(_AGENT_TEST, tests)


def test_the_shipped_starter_runs_green_on_its_own():
    """The agent's test passes as shipped — that is the trap the lab sets."""
    result = run_code(_starter())
    assert result["passed"], result["stderr"]
    assert "PASS test_bulk_price" in result["stdout"]


def test_the_shipped_starter_fails_the_grader():
    result = run_code(_starter(), _grader())
    assert result.get("error") != "blocked", result.get("stderr")
    assert not result["passed"]
    assert "not a test" in result["stderr"]


def test_a_discriminating_test_passes_the_grader():
    result = run_code(_with_tests(_DISCRIMINATING), _grader())
    assert result["passed"], result["stderr"]
    assert "PASS" in result["stdout"]


def test_an_always_red_test_fails_the_grader():
    result = run_code(_with_tests("def test_always_red():\n    assert False\n"), _grader())
    assert not result["passed"]
    assert "correct bulk_price" in result["stderr"]


def test_no_tests_fails_the_grader():
    result = run_code(_with_tests(""), _grader())
    assert not result["passed"]
    assert "No tests found" in result["stderr"]


def test_a_test_with_its_own_copy_of_the_function_cannot_pass():
    """Pinning a private copy of the buggy function means the swap never reaches it."""
    private = _DISCRIMINATING.replace(
        "def test_exactly_ten_gets_the_discount():\n    assert bulk_price(10, 2.0) == 18.0",
        "def test_exactly_ten_gets_the_discount(f=bulk_price):\n    assert f(10, 2.0) == 18.0",
    )
    assert private != _DISCRIMINATING
    result = run_code(_with_tests(private), _grader())
    assert not result["passed"]
