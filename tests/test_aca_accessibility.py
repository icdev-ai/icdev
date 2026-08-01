# CUI // SP-CTI
"""aca-trn-06 — the mission runner has to be usable without a mouse or a screen.

Verified against the templates as authored, and separately against a live
accessibility snapshot (see the PR) rather than by eye, as the card asked.

What was actually wrong, after checking each claim on the card:

  * the step list was <li onclick> with no role, tabindex or key handling, so a
    keyboard user could not reach step 2 of any mission
  * the XP toast was injected already-populated with no live region, so completing a
    step announced nothing
  * the code editor, the design-id input and the configure form's select / textarea /
    text inputs had no accessible name
  * .sr-only was not defined anywhere the academy could reach it

One claim did NOT hold up: missions.html was said to convey status by colour alone,
but it already renders "checkmark Done / dot Active / circle Start" — text plus
glyph, with colour redundant. It is asserted here so it stays that way.
"""
from __future__ import annotations

import pathlib
import re

import pytest

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
TPL = REPO_ROOT / "tools" / "dashboard" / "templates" / "forge_academy"
MIRROR = REPO_ROOT / "icdev" / "tools" / "dashboard" / "templates" / "forge_academy"
RUNNER = TPL / "mission.html"
PARTIALS = TPL / "partials"


def _runner() -> str:
    return RUNNER.read_text(encoding="utf-8")


def _code_only(text: str) -> str:
    """Drop Jinja and JS comments.

    These assertions are about what the markup does, and the comments explaining a
    fix naturally quote the thing being fixed — `role="main"` and `display:none` both
    appear in prose right next to the code that avoids them.
    """
    text = re.sub(r"\{#.*?#\}", "", text, flags=re.S)
    text = re.sub(r"/\*.*?\*/", "", text, flags=re.S)
    return re.sub(r"^\s*//.*$", "", text, flags=re.M)


# --------------------------------------------------------------------------
# keyboard operability
# --------------------------------------------------------------------------

def test_step_list_items_are_focusable_and_have_a_role():
    html = _runner()
    nav = html[html.index('id="step-nav"'):html.index("</ul>", html.index('id="step-nav"'))]
    assert 'role="button"' in nav, "a div/li that responds to click needs a role"
    assert 'tabindex="0"' in nav, "the step list was unreachable by keyboard"


def test_step_list_responds_to_enter_and_space():
    html = _runner()
    assert "keydown" in html, "no key handler at all"
    for key in ("'Enter'", "' '"):
        assert key in html, f"{key} does not activate a step"


def test_arrow_keys_move_between_steps():
    html = _runner()
    for key in ("ArrowDown", "ArrowUp", "Home", "End"):
        assert key in html, f"{key} is not handled in the step list"


def test_the_focused_step_is_visible():
    """tabindex without a focus ring swaps one barrier for another."""
    css = (PARTIALS / "_a11y.html").read_text(encoding="utf-8")
    assert ":focus-visible" in css
    assert "outline" in css
    assert ".fa-step-nav li:focus" in css


def test_the_current_step_is_announced_not_just_coloured():
    html = _runner()
    assert 'aria-current="step"' in html, "the active step was a CSS class only"
    assert "removeAttribute('aria-current')" in html, (
        "aria-current must move with the selection, or every step claims to be current"
    )


def test_there_is_a_way_past_the_step_list():
    html = _runner()
    assert "fa-skip-link" in html
    assert 'id="step-main" tabindex="-1"' in html, (
        "a skip link to a non-focusable target scrolls but leaves focus behind"
    )


# --------------------------------------------------------------------------
# announcements
# --------------------------------------------------------------------------

def test_xp_feedback_reaches_a_live_region():
    html = _runner()
    assert "faAnnounce" in html
    assert 'aria-live' in html and 'role\', \'status\'' in html.replace('"', "'")
    assert "showXPToast" in html
    toast = html[html.index("function showXPToast"):]
    assert "faAnnounce" in toast[:400], "the toast fires without announcing anything"


def test_the_live_region_is_populated_after_it_is_attached():
    """A region that appears already-populated is frequently not announced.

    Screen readers generally announce mutations to a region they were already
    observing, so building the node with its text and appending it — which is what
    showXPToast did — can pass silently.
    """
    html = _runner()
    fn = html[html.index("function faLiveRegion"):html.index("function showXPToast")]
    assert "appendChild" in fn
    assert "setTimeout" in fn, "text must be written after the region is in the DOM"


def test_the_live_region_is_registered_at_load_not_on_first_use():
    """A region created inside the first announcement may miss that announcement.

    It has to be under observation before the mutation it should announce. Creating
    it lazily made the least reliable announcement a learner's FIRST completed step —
    the one that most needs to land. Probing the live page reported
    liveRegionExistedBefore: false.
    """
    html = _code_only(_runner())
    registered = ("DOMContentLoaded', faLiveRegion" in html
                  or 'DOMContentLoaded", faLiveRegion' in html)
    assert registered, "the live region must be created at page load, not on first use"


def test_the_visual_toast_is_not_announced_twice():
    html = _runner()
    toast = html[html.index("function showXPToast"):html.index("function faEnrolNotice")]
    assert "aria-hidden" in toast


def test_the_live_region_is_not_display_none():
    """display:none removes an element from the accessibility tree entirely."""
    html = _code_only(_runner())
    fn = html[html.index("function faLiveRegion"):html.index("function showXPToast")]
    assert "display:none" not in fn.replace(" ", "")
    assert "clip:rect" in fn.replace(" ", ""), "use clip-based visually-hidden"


# --------------------------------------------------------------------------
# form controls
# --------------------------------------------------------------------------

@pytest.mark.parametrize("partial,control", [
    ("_step_coding.html", "textarea"),
    ("_step_design.html", "input"),
])
def test_controls_have_an_accessible_name(partial, control):
    html = (PARTIALS / partial).read_text(encoding="utf-8")
    assert re.search(r"<label[^>]*\bfor=", html) or "aria-label" in html, (
        f"{partial}: the {control} reaches the a11y tree unnamed"
    )


def test_configure_form_labels_point_at_their_control():
    """The label sits beside its control rather than wrapping it, so `for` is required."""
    html = (PARTIALS / "_step_configure.html").read_text(encoding="utf-8")
    assert 'for="cfg-' in html
    for control in ("<select", "<textarea"):
        idx = html.index(control)
        assert 'id="cfg-' in html[idx:idx + 220], f"{control} has no id to be labelled by"


def test_the_codemirror_editor_keeps_a_name_after_it_replaces_the_textarea():
    """fromTextArea hides the labelled textarea and types into a private one."""
    html = _runner()
    assert "getInputField" in html, (
        "the <label for> stops reaching the control the learner actually focuses"
    )


def test_sr_only_is_defined_where_the_academy_can_use_it():
    """It previously existed only inside batch.js, so sr-only markup rendered visibly."""
    css = (PARTIALS / "_a11y.html").read_text(encoding="utf-8")
    assert ".sr-only" in css
    assert "clip:" in css.replace(" ", "") or "clip-path" in css


# --------------------------------------------------------------------------
# claims that were already satisfied — pinned so they stay that way
# --------------------------------------------------------------------------

def test_mission_status_is_not_conveyed_by_colour_alone():
    html = (TPL / "missions.html").read_text(encoding="utf-8")
    block = html[html.index('class="fa-status'):]
    for word in ("Done", "Active", "Start"):
        assert word in block[:400], f"status {word} would be colour-only"


def test_the_runner_does_not_add_a_second_main_landmark():
    """base.html already declares role="main"; two main landmarks is a violation."""
    html = _code_only(_runner())
    assert 'role="main"' not in html
    assert 'role="region"' in html


# --------------------------------------------------------------------------
# parity
# --------------------------------------------------------------------------

@pytest.mark.parametrize("name", ["mission.html", "missions.html"])
def test_mirrored(name):
    assert (MIRROR / name).read_text(encoding="utf-8") == (TPL / name).read_text(encoding="utf-8")


@pytest.mark.parametrize("name", ["_a11y.html", "_step_coding.html",
                                  "_step_design.html", "_step_configure.html"])
def test_partials_mirrored(name):
    src = (PARTIALS / name)
    dst = MIRROR / "partials" / name
    assert dst.is_file(), f"{name} is not mirrored"
    assert dst.read_text(encoding="utf-8") == src.read_text(encoding="utf-8")
