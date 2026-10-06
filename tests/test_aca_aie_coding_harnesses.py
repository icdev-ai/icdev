# CUI // SP-CTI
"""m-aie-01-coding-harnesses (aicur-eng-01): catalogued, discovered, GRADED.

Topic 2a — AI coding harnesses — had only passing mentions in m06 and m-ciso-01.
This pins the mission's wiring end to end:

  * it is in the hand-written catalogue and discovery finds its three steps;
  * both lessons carry a valid item bank (graded, not acknowledged), and every
    item is answerable from its OWN lesson (the key term appears in that file);
  * the lab grades for real in the code_runner sandbox, offline: the shipped
    starter FAILS, a correct solution PASSES, and a solution that hardcodes the
    shown project FAILS on the grader's hidden second project.
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

SLUG = "m-aie-01-coding-harnesses"
STEPS_DIR = CONTENT_ROOT / "tier1" / SLUG / "steps"


@pytest.fixture(scope="module")
def steps() -> list:
    return discover_steps().get(SLUG) or []


def _read(rel: str) -> str:
    return (CONTENT_ROOT / rel).read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# Catalogue + discovery
# ---------------------------------------------------------------------------

def test_mission_is_catalogued_in_tier1():
    by_slug = {m["slug"]: m for m in BUILTIN_MISSIONS}
    assert SLUG in by_slug
    mission = by_slug[SLUG]
    assert mission["tier"] == 1
    catalogued = {m["slug"] for m in BUILTIN_MISSIONS}
    assert set(mission["prereqs"]) <= catalogued, "a prereq names no catalogued mission"


def test_discovery_finds_two_lessons_and_a_lab(steps):
    assert [s["step_num"] for s in steps] == [1, 2, 3]
    assert [s["step_type"] for s in steps] == ["watch", "watch", "coding"]
    lab = steps[2]
    assert lab["starter_code_path"].endswith("step3_starter.py")
    assert lab["test_code_path"].endswith("step3_test.py")


def test_the_landscape_is_dated(steps):
    """Facts must be dated: the landscape lesson states when it was true."""
    body = _read(steps[0]["content_path"])
    assert "as of mid-2026" in body


def test_the_lab_references_the_platform_registry(steps):
    body = _read(steps[2]["content_path"])
    assert "tools/dx/ai_platforms.py" in body
    assert "tools/dx/instruction_generator.py" in body


# ---------------------------------------------------------------------------
# Item bank — every lesson step is graded
# ---------------------------------------------------------------------------

def test_every_lesson_step_has_a_valid_bank(steps):
    banks = load_item_banks().get(SLUG) or {}
    lessons = [s["step_num"] for s in steps if s["step_type"] != "coding"]
    assert lessons == [1, 2]
    for num in lessons:
        items = banks.get(num) or []
        assert not validate_item_bank(items), f"step {num}: {validate_item_bank(items)}"
        assert len({i["correct_index"] for i in items}) > 1, "answer is always the same option"


# A term from each item's correct answer that its own lesson must contain. If a
# lesson is edited and drops the fact, the item becomes unanswerable — fail here.
_ANSWER_TERMS = {
    1: {
        "hl-model-vs-harness": "Permissions",
        "hl-who-executes": "the harness *executes* it",
        "hl-autocomplete": "no agent loop",
        "hl-headless": "headless",
        "hl-file-mapping": "AGENTS.md",
        "hl-icdev-registry": "AI_PLATFORM_FILES",
        "hl-why-agree": "two different codebases",
    },
    2: {
        "dh-instruction-contents": "Boundaries",
        "dh-keep-short": "every session",
        "dh-hook-exit": "exit code 2",
        "dh-plan-mode": "read and propose only",
        "dh-subagent": "returns a conclusion",
        "dh-worktree": "git worktree add",
        "dh-context-hygiene": "Point, don't paste",
        "dh-mcp-least-privilege": "narrowest",
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
# Lab — graded server-side in the sandbox, offline
# ---------------------------------------------------------------------------

_SOLUTION_FN = '''
def render_body(project):
    out = ["## Project Overview", f"{project['name']}: {project['summary']}",
           "Stack: " + ", ".join(project["stack"]), "", "## Commands"]
    out += [f"- {k}: `{v}`" for k, v in project["commands"].items()]
    out += ["", "## Conventions"] + [f"- {c}" for c in project["conventions"]]
    out += ["", "## Boundaries"] + [f"- {b}" for b in project["boundaries"]]
    return "\\n".join(out) + "\\n"


def build_instruction_files(project):
    body = render_body(project)
    header = f"# {project['name']}\\n\\n"
    mdc = ("---\\n" f"description: Rules for {project['name']}\\n"
           "alwaysApply: true\\n---\\n\\n")
    return {"CLAUDE.md": header + body, "AGENTS.md": header + body,
            ".cursor/rules/project.mdc": mdc + header + body}
'''


def _starter() -> str:
    return (STEPS_DIR / "step3_starter.py").read_text(encoding="utf-8")


def _grader() -> str:
    return (STEPS_DIR / "step3_test.py").read_text(encoding="utf-8")


def _solution() -> str:
    """The shipped starter with its two TODO functions replaced by a working body."""
    src = _starter()
    head, rest = src.split("def render_body", 1)
    _, tail = rest.split("files = build_instruction_files(PROJECT)", 1)
    return head + _SOLUTION_FN + "\n\nfiles = build_instruction_files(PROJECT)" + tail


def test_the_shipped_starter_fails_the_grader():
    result = run_code(_starter(), _grader())
    assert result.get("error") != "blocked", result.get("stderr")
    assert not result["passed"]


def test_a_correct_solution_passes_the_grader():
    result = run_code(_solution(), _grader())
    assert result["passed"], result["stderr"]
    assert "PASS" in result["stdout"]


def test_hardcoding_the_shown_project_fails_on_the_hidden_one():
    hardcoded = _solution().replace(
        "def build_instruction_files(project):",
        "def build_instruction_files(project):\n    project = PROJECT",
    )
    result = run_code(hardcoded, _grader())
    assert not result["passed"]
    assert "fleet-telemetry" in result["stderr"] or "missing" in result["stderr"]


def test_cursor_file_without_frontmatter_fails():
    no_header = _solution().replace('"alwaysApply: true\\n---\\n\\n"', '"\\n"').replace(
        '("---\\n" f"description: Rules for {project[\'name\']}\\n"', '(""'
    )
    assert no_header != _solution()
    result = run_code(no_header, _grader())
    assert not result["passed"]
    assert "project.mdc" in result["stderr"]
