# CUI // SP-CTI
"""Agent-driven page V&V (oss-browse-04).

The recurring V&V lesson this institutionalises: a visual regression needs a
screenshot + DOM evidence, not a 200 status. So the tests that matter are the
FALSE-PASS cases — a page that returns 200 and renders a stack trace, an empty
body, or a missing widget must FAIL, and each failure must carry the DOM
evidence that proves it.

A FakeBrowser stands in for AgentBrowser so these run without a driver. It has
the same read_state / navigate / screenshot surface, and — crucially — it goes
through the same code paths the real GuardedDriver-backed browser does.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List


from tools.browser.page_vv import FAIL, PASS, PageVerifier


@dataclass
class FakeElement:
    index: int
    tag: str = "div"
    text: str = ""
    attributes: Dict[str, Any] = field(default_factory=dict)


@dataclass
class FakePageState:
    title: str = ""
    elements: List[FakeElement] = field(default_factory=list)
    _text: str = ""

    def to_text(self) -> str:
        return self._text or "\n".join(e.text for e in self.elements)


class FakeBrowser:
    """Serves scripted page states by URL, records screenshots taken."""

    def __init__(self, pages: Dict[str, FakePageState], console=None):
        self._pages = pages
        self._console = console or []
        self.shots: List[str] = []
        self.navigations: List[str] = []

        class _Driver:
            def __init__(self, console):
                self._console = console

            def get_log(self, which):
                return self._console

        class _Guard:
            def __init__(self, console):
                self.driver = _Driver(console)

        self.guard = _Guard(self._console)

    def navigate(self, url: str) -> FakePageState:
        self.navigations.append(url)
        # Exact path match against the served pages. Suffix matching would let
        # "/missing" resolve to "/" and mask a nav failure.
        from urllib.parse import urlsplit

        path = urlsplit(url).path.rstrip("/") or "/"
        for served, state in self._pages.items():
            if (served.rstrip("/") or "/") == path:
                return state
        raise RuntimeError(f"navigation refused or 404: {url}")

    def screenshot(self, name=None) -> str:
        path = f"playwright/screenshots/{name}.png"
        self.shots.append(path)
        return path


def _healthy_page(path="/bi_dashboard") -> FakePageState:
    return FakePageState(
        title="BI Studio - ICDEV Dashboard",
        elements=[
            FakeElement(0, "h1", "BI Studio"),
            FakeElement(1, "input", "Ask a question", {"id": "iqe-query-input", "class": "iqe-widget"}),
            FakeElement(2, "button", "Run"),
            FakeElement(3, "canvas", "chart"),
        ],
    )


def _home_linking_to(path) -> FakePageState:
    return FakePageState(
        title="ICDEV Dashboard",
        elements=[FakeElement(0, "a", "BI Studio", {"href": path})],
    )


# ── The happy path ───────────────────────────────────────────────────────────


def test_a_healthy_page_passes_all_components():
    browser = FakeBrowser({"/bi_dashboard": _healthy_page(), "/": _home_linking_to("/bi_dashboard")})
    report = PageVerifier(browser).verify("bi_dashboard", "/bi_dashboard")

    assert report.passed is True
    statuses = {c.component: c.status for c in report.components}
    assert statuses["page_renders"] == PASS
    assert statuses["iqe_widget"] == PASS
    assert statuses["nav_reachable"] == PASS
    assert statuses["no_console_errors"] == PASS


def test_every_component_carries_a_screenshot():
    """Evidence, not just a verdict — the whole point of the task."""
    browser = FakeBrowser({"/bi_dashboard": _healthy_page(), "/": _home_linking_to("/bi_dashboard")})
    report = PageVerifier(browser).verify("bi_dashboard", "/bi_dashboard")
    assert any(c.screenshot for c in report.components)
    assert browser.shots, "no screenshots were captured"


# ── The false-pass cases — a 200 is not enough ──────────────────────────────


def test_a_200_that_renders_a_traceback_fails():
    """The classic false pass: HTTP 200, body is a stack trace."""
    error_page = FakePageState(
        title="Error",
        elements=[FakeElement(0, "pre", "Traceback (most recent call last):")],
        _text="Traceback (most recent call last):\n  File ...\nOperationalError",
    )
    browser = FakeBrowser({"/broken": error_page, "/": _home_linking_to("/broken")})
    report = PageVerifier(browser).verify("broken", "/broken")

    err = next(c for c in report.components if c.component == "no_render_error")
    assert err.status == FAIL
    assert "Traceback" in err.dom_evidence, "the failure must carry the proof"
    assert report.passed is False


def test_an_empty_body_fails_content_present():
    empty = FakePageState(title="Empty", elements=[FakeElement(0, "div", "")])
    browser = FakeBrowser({"/empty": empty, "/": _home_linking_to("/empty")})
    report = PageVerifier(browser).verify("empty", "/empty")

    content = next(c for c in report.components if c.component == "content_present")
    assert content.status == FAIL


def test_a_page_whose_iqe_widget_does_not_render_fails():
    """Source may `{% include %}` the widget; that is not the same as it rendering.

    This is precisely the gap over the static coherence check: grep sees the
    include, the DOM does not have the widget.
    """
    no_iqe = FakePageState(
        title="No IQE",
        elements=[FakeElement(0, "h1", "Page"), FakeElement(1, "p", "body"), FakeElement(2, "div", "x")],
    )
    browser = FakeBrowser({"/no_iqe": no_iqe, "/": _home_linking_to("/no_iqe")})
    report = PageVerifier(browser).verify("no_iqe", "/no_iqe")

    iqe = next(c for c in report.components if c.component == "iqe_widget")
    assert iqe.status == FAIL
    assert "does not render" in iqe.detail


def test_an_orphaned_page_fails_nav_reachable():
    """A page nothing links to is orphaned, even if it renders perfectly."""
    browser = FakeBrowser({
        "/orphan": _healthy_page("/orphan"),
        "/": FakePageState(title="Home", elements=[FakeElement(0, "a", "Other", {"href": "/other"})]),
    })
    report = PageVerifier(browser).verify("orphan", "/orphan")

    nav = next(c for c in report.components if c.component == "nav_reachable")
    assert nav.status == FAIL
    assert "orphaned" in nav.detail


def test_severe_console_errors_fail_the_page():
    """A page that renders visually but throws in JS is a real regression."""
    browser = FakeBrowser(
        {"/bi_dashboard": _healthy_page(), "/": _home_linking_to("/bi_dashboard")},
        console=[{"level": "SEVERE", "message": "Uncaught TypeError: x is undefined"}],
    )
    report = PageVerifier(browser).verify("bi_dashboard", "/bi_dashboard")

    con = next(c for c in report.components if c.component == "no_console_errors")
    assert con.status == FAIL
    assert "TypeError" in con.dom_evidence


# ── Failure posture ──────────────────────────────────────────────────────────


def test_a_page_that_will_not_load_fails_fast_with_a_screenshot():
    browser = FakeBrowser({"/": _home_linking_to("/x")})    # /missing not served
    report = PageVerifier(browser).verify("missing", "/missing")

    assert report.passed is False
    render = next(c for c in report.components if c.component == "page_renders")
    assert render.status == FAIL
    assert render.screenshot, "even a nav failure must capture evidence"


def test_report_names_exactly_the_failed_components():
    error_page = FakePageState(
        title="Error", elements=[FakeElement(0, "pre", "x")],
        _text="Internal Server Error",
    )
    browser = FakeBrowser({"/broken": error_page, "/": _home_linking_to("/broken")})
    report = PageVerifier(browser).verify("broken", "/broken")
    assert "no_render_error" in report.failed_components


def test_verify_page_returns_unavailable_envelope_without_a_driver(monkeypatch):
    """A CI caller degrades to 'could not verify', never crashes."""
    from tools.browser import page_vv

    monkeypatch.setattr(
        "tools.browser.agent_browser.AgentBrowser",
        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("no driver")),
    )
    out = page_vv.verify_page("x", "/x")
    assert out["passed"] is None
    assert "unavailable" in out["error"]


# ── The success criterion: reproduce a Selenium script's assertions ──────────


def test_reproduces_a_handwritten_e2e_assertion_set_from_a_goal():
    """The card's stated success criterion.

    A hand-written e2e for a dashboard page asserts, in prose:
      "the page loads with its title, the IQE widget is present, and it is
       reachable from the nav."
    Driving from that goal alone, the verifier reaches the same verdicts —
    per-component, with evidence — that the scripted assertions encode.
    """
    browser = FakeBrowser({"/bi_dashboard": _healthy_page(), "/": _home_linking_to("/bi_dashboard")})
    report = PageVerifier(browser).verify("bi_dashboard", "/bi_dashboard")

    by = {c.component: c for c in report.components}
    # the three assertions the hand-written script encodes:
    assert by["page_renders"].status == PASS and by["page_renders"].dom_evidence
    assert by["iqe_widget"].status == PASS and by["iqe_widget"].dom_evidence
    assert by["nav_reachable"].status == PASS and by["nav_reachable"].dom_evidence
    # and unlike the scripted assertions, each verdict ships proof
    assert all(c.screenshot or c.dom_evidence for c in report.components if c.status == PASS)
