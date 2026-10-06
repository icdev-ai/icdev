# CUI // SP-CTI
"""aca-step-polish: what the Tier 2/3 walkthrough found broken on EVERY mission.

Three defects that no single lesson edit could fix, because each one lived in the
shared machinery rather than in the content:

  1. Lessons indent sub-lists by two spaces; Python-Markdown wants four, so every
     nested list rendered merged into its parent's numbering.
  2. A step's title is seeded once and never refreshed, so a corrected lesson kept
     its stale claim in the sidebar ("6 ML Predictors"), and m11 showed a
     classification banner ("CUI // SP-CTI") as each step's title.
  3. Configure and reflect steps discovered from lesson files carry no field schema,
     so their panel offered nothing to fill in — a configure step promised to
     "execute this action with default settings" and recorded {}.
"""

from __future__ import annotations

from pathlib import Path

import jinja2

from apps.forge_academy.content_loader import _indent_nested_lists, display_step_title

_ROOT = Path(__file__).resolve().parent.parent
PARTIALS = _ROOT / "tools" / "dashboard" / "templates" / "forge_academy" / "partials"


def _render_step(partial: str, schema: dict) -> str:
    """Render a step partial as mission.html's step loop does, as its first step."""
    env = jinja2.Environment(loader=jinja2.FileSystemLoader(str(PARTIALS)), autoescape=True)
    return env.get_template(partial).render(
        step={"content_md": "<p>lesson</p>", "config_schema": schema},
        loop={"index0": 0},
    )


# --- 1. nested lists ---------------------------------------------------------
# Tested on the pre-render pass itself: the markdown package is optional (the gated
# CI job does not install it, and _md_to_html then falls back to <pre>).

def test_a_two_space_sub_list_is_widened_to_four():
    out = _indent_nested_lists("1. Parent\n  - child a\n  - child b\n2. Next\n")
    assert out == "1. Parent\n    - child a\n    - child b\n2. Next\n"


def test_two_space_authoring_nests_every_level():
    out = _indent_nested_lists("- a\n  - b\n    - c\n- d\n")
    assert out == "- a\n    - b\n        - c\n- d\n"


def test_mixed_authoring_ranks_indents_by_depth():
    """4-then-6 spaces is one level under b, not three levels deep."""
    out = _indent_nested_lists("- a\n    - b\n      - c\n")
    assert out == "- a\n    - b\n        - c\n"


def test_indented_text_outside_a_list_is_left_alone():
    text = "Para\n  - not a list item\n"
    assert _indent_nested_lists(text) == text


def test_code_fences_keep_their_indentation():
    text = "```\n  - not a list\n```\n"
    assert _indent_nested_lists(text) == text


# --- 2. step titles ----------------------------------------------------------

def test_a_lesson_frontmatter_title_replaces_the_stale_seeded_one():
    content = {"frontmatter": {"title": '"Predictive Network Analytics: 6 Scorers"'},
               "title": "Predictive Network Analytics"}
    assert display_step_title("Predictive Network Analytics: 6 ML Predictors", content) == \
        "Predictive Network Analytics: 6 Scorers"


def test_a_classification_banner_is_not_a_title():
    content = {"frontmatter": {}, "title": "Build a Document Classifier"}
    assert display_step_title("CUI // SP-CTI", content) == "Build a Document Classifier"


def test_an_authored_step_title_is_kept_over_the_lesson_heading():
    """Coding steps carry specific titles; the lesson's H1 is often mission-level."""
    content = {"frontmatter": {}, "title": "ICDEV Cortex — The Unified AI Layer"}
    stored = "Route a request through the Cortex facade"
    assert display_step_title(stored, content) == stored


# --- 3. steps with no field schema -------------------------------------------

def test_a_configure_step_with_no_fields_asks_for_an_answer():
    html = _render_step("_step_configure.html", {})
    assert 'name="answer"' in html
    assert "execute this action with default settings" not in html


def test_a_configure_step_with_an_action_keeps_its_default_prompt():
    html = _render_step("_step_configure.html", {"action": "stig_triage"})
    assert 'name="answer"' not in html
    assert "execute this action with default settings" in html


def test_a_configure_step_with_fields_renders_them_not_the_answer_box():
    html = _render_step("_step_configure.html",
                        {"fields": [{"id": "target", "label": "Target", "type": "text"}]})
    assert 'name="target"' in html
    assert 'name="answer"' not in html


def test_a_reflect_step_with_no_questions_offers_a_notes_box():
    html = _render_step("_step_reflect.html", {})
    assert 'id="reflect-form-0"' in html
    assert 'name="notes"' in html
