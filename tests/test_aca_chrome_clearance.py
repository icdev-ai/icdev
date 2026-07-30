# CUI // SP-CTI
"""aca-ux-05 — docked dashboard chrome must not cover academy content.

The dashboard docks three fixed widgets the Academy does not own: the IQE mini-bar
across the bottom, the "CLI Prompt / BRIDGE" panel bottom-left, and the assistant
FAB bottom-right. Measured in a real browser at the live viewport on
/academy/missions, they covered four mission cards, and body carried
padding-bottom: 0 — so content at the foot of the page could not be scrolled clear
of them. On the mission runner the IQE bar sat over the primary
"Understood -> Continue" control, the forward action of every watch step.

Fixed academy-scoped rather than globally: those widgets belong to the dashboard
shell that every canvas shares, so a body-level rule would reach far outside this
change.
"""
from __future__ import annotations

from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
TPL = REPO_ROOT / "tools" / "dashboard" / "templates" / "forge_academy"
MIRROR = REPO_ROOT / "icdev" / "tools" / "dashboard" / "templates" / "forge_academy"
PARTIAL = TPL / "partials" / "_chrome_clearance.html"


def _pages():
    return sorted(p for p in TPL.glob("*.html"))


def test_the_clearance_partial_exists():
    assert PARTIAL.is_file()


def test_every_academy_page_includes_the_clearance():
    missing = [
        p.name for p in _pages()
        if "_chrome_clearance.html" not in p.read_text(encoding="utf-8")
    ]
    assert not missing, (
        f"these academy pages can still be covered by the docked bar: {missing}"
    )


def test_the_clearance_pads_the_scrolling_panes_not_just_the_page():
    """The mission runner's right pane scrolls on its own.

    Padding the page would do nothing there — the Run/Continue controls live inside
    .fa-main, which has its own overflow.
    """
    css = PARTIAL.read_text(encoding="utf-8")
    assert ".fa-main" in css, "the runner's scrolling pane needs its own clearance"
    assert ".fa-sidebar" in css, "the step list scrolls independently too"


def test_the_clearance_is_academy_scoped():
    """A global rule would change every other canvas sharing the dashboard shell."""
    css = PARTIAL.read_text(encoding="utf-8")
    for selector in ("body {", "body{", "html {", "* {"):
        assert selector not in css, (
            f"{selector!r} reaches outside the Academy; scope to .fa-* containers"
        )


def test_the_clearance_height_is_a_single_named_value():
    """One knob, so re-measuring the chrome does not mean editing many rules."""
    css = PARTIAL.read_text(encoding="utf-8")
    assert "--fa-docked-chrome" in css
    assert css.count("--fa-docked-chrome:") == 1, "define the height exactly once"


@pytest.mark.parametrize("page", [p.name for p in sorted(TPL.glob("*.html"))])
def test_the_mirror_matches(page):
    src = (TPL / page).read_text(encoding="utf-8")
    dst = MIRROR / page
    assert dst.is_file(), f"{page} is not mirrored to icdev/"
    assert dst.read_text(encoding="utf-8") == src, f"{page} differs from its mirror"


def test_templates_still_parse():
    """A malformed include would break every academy page at once."""
    from jinja2 import Environment, FileSystemLoader

    env = Environment(
        loader=FileSystemLoader(str(REPO_ROOT / "tools" / "dashboard" / "templates")),
        autoescape=True,
    )
    for p in _pages():
        env.parse(p.read_text(encoding="utf-8"), filename=p.name)
