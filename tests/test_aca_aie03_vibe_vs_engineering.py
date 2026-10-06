# CUI // SP-CTI
"""m-aie-03-vibe-vs-engineering (aicur-eng-03): catalogued, discovered, and GRADED.

The failure this guards against is the quiet one the mission itself teaches: a
mission that is listed and openable but whose steps silently fall back to
`acknowledged` because a bank was malformed, keyed to the wrong step, or its
snippet references point at nothing.
"""
from __future__ import annotations

import re

from apps.forge_academy import content_loader
from apps.forge_academy.assessment import validate_item_bank

SLUG = "m-aie-03-vibe-vs-engineering"


def test_the_mission_is_catalogued_in_tier_1():
    entry = next(m for m in content_loader.BUILTIN_MISSIONS if m["slug"] == SLUG)
    assert entry["tier"] == 1
    catalogued = {m["slug"] for m in content_loader.BUILTIN_MISSIONS}
    assert set(entry["prereqs"]) <= catalogued


def test_discovery_finds_two_lessons_and_a_reflect_step():
    steps = content_loader.steps_for(SLUG)
    assert [(s["step_num"], s["step_type"]) for s in steps] == [
        (1, "watch"), (2, "watch"), (3, "reflect"),
    ]
    assert steps[0]["learning_objective"]
    assert content_loader.mission_type_from_steps(steps) == "watch"


def test_every_step_has_a_valid_bank():
    by_step = content_loader.load_item_banks()[SLUG]
    assert set(by_step) == {1, 2, 3}
    for step_num, items in by_step.items():
        assert not validate_item_bank(items), (step_num, validate_item_bank(items))
        assert len({i["correct_index"] for i in items}) > 1, step_num


def test_every_snippet_question_names_a_snippet_the_lesson_prints():
    steps = content_loader.steps_for(SLUG)
    lesson = (content_loader.CONTENT_ROOT / steps[2]["content_path"]).read_text(
        encoding="utf-8")
    printed = set(re.findall(r"^## Snippet ([A-Z])$", lesson, re.MULTILINE))
    asked = set()
    for item in content_loader.load_item_banks()[SLUG][3]:
        m = re.match(r"Snippet ([A-Z])\b", item["prompt"])
        assert m, item["item_key"]
        asked.add(m.group(1))
    assert asked == printed


def test_lessons_are_dated():
    for step in content_loader.steps_for(SLUG)[:2]:
        text = (content_loader.CONTENT_ROOT / step["content_path"]).read_text(
            encoding="utf-8")
        assert "as of October 2026" in text, step["content_path"]
