# CUI // SP-CTI
"""aca-trn-03 — a mission stated three costs and never what it taught.

``fa_missions`` advertised ``xp_reward``, ``estimated_minutes`` and ``difficulty``
— all price — and nothing about the outcome. For training used in a compliance
context that is backwards: the objective is the auditable unit, because "this
learner was trained on X" needs an X, and a tagline is marketing copy.

The change is an EXTRACTION pass, not an authoring one, and the tests below exist
mostly to hold that line. The failure mode worth guarding is not "no objective
shown" — that is a visible content gap someone can go fix. It is an objective
shown for a mission whose content never stated one: un-authored text on the field
an audit reads, indistinguishable from the real thing. So the assertions are
mostly about what the extractor REFUSES to return.

Pure-function tests against markdown strings — no database, no conftest fixture.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

APPS = Path(__file__).resolve().parents[1] / "apps"
if str(APPS) not in sys.path:
    sys.path.insert(0, str(APPS))

from forge_academy import content_loader as cl  # noqa: E402


def _step(raw: str) -> list[dict]:
    """One discovered step carrying whatever the markdown states."""
    return [{"step_num": 1, "learning_objective": cl.extract_learning_objective(raw)}]


# --------------------------------------------------------------------------
# What it extracts
# --------------------------------------------------------------------------

def test_frontmatter_objective_is_the_explicit_channel():
    """An author stating it outright wins over anything in the body."""
    raw = (
        "---\n"
        "title: Build a thing\n"
        "learning_objective: Configure a FIPS 199 categorisation for a new system.\n"
        "---\n"
        "## What You'll Build\n\n"
        "Something entirely different that should not be picked up here.\n"
    )
    assert cl.extract_learning_objective(raw) == (
        "Configure a FIPS 199 categorisation for a new system."
    )


def test_lead_paragraph_of_an_objective_section_is_extracted():
    raw = (
        "---\ntitle: t\n---\n"
        "## What You'll Build\n\n"
        "A TerraformGenerator that converts infrastructure specs into Terraform HCL.\n\n"
        "## Your Task\n\nSomething else.\n"
    )
    assert cl.extract_learning_objective(raw) == (
        "A TerraformGenerator that converts infrastructure specs into Terraform HCL."
    )


def test_markdown_is_flattened_but_identifiers_survive():
    """Links reduce to their label and emphasis is dropped — snake_case is not
    emphasis, and is usually the thing being taught."""
    raw = (
        "---\ntitle: t\n---\n"
        "## Mission Brief\n\n"
        "Wire **[the router](/docs/router.md)** so that `get_connection` and "
        "listen_topics resolve for every tenant in the estate.\n"
    )
    out = cl.extract_learning_objective(raw)
    assert "listen_topics" in out
    assert "get_connection" in out
    assert "the router" in out
    assert "**" not in out and "](" not in out and "`" not in out


def test_an_explicit_objective_heading_beats_a_build_section():
    raw = (
        "---\ntitle: t\n---\n"
        "## What You'll Build\n\nA build description long enough to clear the floor.\n\n"
        "## Learning Objective\n\nAssess a system against the CMMC Level 2 practices.\n"
    )
    assert cl.extract_learning_objective(raw).startswith("Assess a system")


# --------------------------------------------------------------------------
# What it refuses to invent — the point of the change
# --------------------------------------------------------------------------

def test_no_objective_section_yields_empty_not_a_guess():
    """A tagline is marketing copy. Returning it would be the failure this whole
    change exists to avoid."""
    raw = (
        "---\ntitle: t\ntagline: The difference between a chatbot and a weapon "
        "is the prompt.\n---\n"
        "## Setup\n\nInstall the dependencies and open the canvas.\n"
    )
    assert cl.extract_learning_objective(raw) == ""


def test_a_question_prompt_is_not_an_objective():
    """"Your Task" sections often pose the exercise rather than state the outcome.
    A quiz item rendered as the objective is un-authored text on an audited
    field, so the mission falls through to stating none."""
    raw = (
        "---\ntitle: t\n---\n"
        "## Your Task\n\n"
        "Read the ACE role YAML and identify: what listen_topics does it subscribe "
        "to? What steps does it execute?\n"
    )
    assert cl.extract_learning_objective(raw) == ""


def test_a_section_that_opens_on_a_list_borrows_nothing_from_the_next():
    """No prose of its own means no objective — not the following section's text."""
    raw = (
        "---\ntitle: t\n---\n"
        "## What You'll Build\n\n"
        "- a parser\n- a validator\n\n"
        "## Background\n\n"
        "This paragraph belongs to Background and must not be surfaced as the "
        "objective of the mission.\n"
    )
    assert cl.extract_learning_objective(raw) == ""


def test_a_section_that_opens_on_a_code_fence_yields_nothing():
    raw = (
        "---\ntitle: t\n---\n"
        "## Mission Brief\n\n"
        "```python\ndef build():\n    return 'a fenced block is not prose'\n```\n"
    )
    assert cl.extract_learning_objective(raw) == ""


def test_a_fragment_below_the_floor_is_dropped():
    """Better absent — and visibly so — than truncated into something unauditable."""
    raw = "---\ntitle: t\n---\n## Objective\n\nBuild it.\n"
    assert cl.extract_learning_objective(raw) == ""


def test_a_heading_inside_a_fence_is_not_a_heading():
    raw = (
        "---\ntitle: t\n---\n"
        "## Setup\n\n"
        "```markdown\n## Learning Objective\n\nThis is sample output, not the "
        "objective of this mission at all.\n```\n"
    )
    assert cl.extract_learning_objective(raw) == ""


# --------------------------------------------------------------------------
# Shape
# --------------------------------------------------------------------------

def test_over_long_prose_is_cut_back_to_a_sentence_boundary():
    long_tail = "It goes on well past what a mission card can hold. " * 12
    raw = (
        "---\ntitle: t\n---\n"
        "## Mission Brief\n\n"
        "Build a chaos engineering agent that designs fault injection experiments. "
        + long_tail
    )
    out = cl.extract_learning_objective(raw)
    assert len(out) <= cl._OBJECTIVE_MAX_CHARS
    assert out.endswith((".", "!", "?", "…"))


def test_a_trailing_colon_is_dropped():
    """A paragraph introducing a list still reads as an objective without it."""
    raw = (
        "---\ntitle: t\n---\n"
        "## What You'll Build\n\n"
        "A STIG marker script that handles the following control families:\n"
        "- authentication\n- audit logging\n"
    )
    assert cl.extract_learning_objective(raw).endswith("control families")


# --------------------------------------------------------------------------
# Mission-level rollup
# --------------------------------------------------------------------------

def test_only_the_first_step_speaks_for_the_mission():
    """Later steps state per-step tasks, not the mission's outcome."""
    steps = [
        {"step_num": 1, "learning_objective": ""},
        {"step_num": 2, "learning_objective": "Something step two happens to state."},
    ]
    assert cl.objective_for_mission(steps) == ""


def test_first_step_objective_is_the_mission_objective():
    steps = [
        {"step_num": 1, "learning_objective": "Generate an SSP from a control baseline."},
        {"step_num": 2, "learning_objective": "ignored"},
    ]
    assert cl.objective_for_mission(steps) == (
        "Generate an SSP from a control baseline."
    )


@pytest.mark.parametrize("steps", [None, [], [{"step_num": 1}]])
def test_a_mission_with_no_steps_or_no_objective_states_none(steps):
    assert cl.objective_for_mission(steps) == ""


# --------------------------------------------------------------------------
# Against the real catalogue
# --------------------------------------------------------------------------

def test_the_authored_catalogue_yields_objectives_without_inventing_them():
    """Guards both directions at once: the extractor must actually fire on real
    content, and must leave the missions that state nothing alone. Bounds rather
    than an exact count, so authoring a new mission does not fail the suite."""
    discovered = cl.discover_steps()
    if not discovered:
        pytest.skip("no authored content in this checkout")

    objectives = {
        slug: cl.objective_for_mission(steps)
        for slug, steps in discovered.items()
    }
    stated = {s: o for s, o in objectives.items() if o}

    assert stated, "the extractor fires on nothing in the real catalogue"
    # It is an extraction pass. If it ever claims an objective for every mission,
    # it has started synthesising them.
    assert len(stated) < len(objectives)
    for slug, objective in stated.items():
        assert len(objective) >= cl._OBJECTIVE_MIN_CHARS, slug
        assert len(objective) <= cl._OBJECTIVE_MAX_CHARS, slug
        assert objective == objective.strip(), slug
        assert "\n" not in objective, slug
