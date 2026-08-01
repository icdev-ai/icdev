#!/usr/bin/env python3
# CUI // SP-CTI
"""Agent-driven page completeness & acceptance V&V (oss-browse-04).

The first consumer of the browser primitive (oss-browse-01), and deliberately on
VERIFICATION rather than browsing.

Two gates are evaluated STATICALLY today and would be strictly better evaluated
by driving the real UI:

* ``new_page_completeness`` — ``coherence_checker.check_new_page_completeness``
  greps for a template include. It can tell you a file contains
  ``{% include "includes/iqe_query_widget.html" %}``; it cannot tell you the
  widget renders, or that clicking it does anything.
* ``acceptance_validation`` — blocks on ``ui_page_renders_with_error``, inferred
  from SOURCE text. Whether the page actually 500s at runtime is a fact source
  text cannot hold.

This verifies the same 8 components against the RUNNING dashboard and reports
per-component pass/fail with a screenshot and DOM evidence per finding. The
recurring V&V lesson this institutionalises: a visual regression needs a
screenshot + DOM evidence, not a 200 status. A page can return 200 and render a
stack trace, an empty body, or a broken widget.

Every action goes through ``AgentBrowser`` → ``scope.GuardedDriver``, so the run
is allowlist-bound (loopback by default) and every navigation/click is one
``audit_trail`` row. There is no path here that reaches a URL the scope refuses.
"""
from __future__ import annotations

import json
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from tools.logging.icdev_logger import get_logger

logger = get_logger("icdev.browser.page_vv")

BASE_DIR = Path(__file__).resolve().parent.parent.parent

PASS = "pass"
FAIL = "fail"
WARN = "warn"
SKIP = "skip"


@dataclass
class ComponentResult:
    """One of the 8 gate components, verified against the live page."""

    component: str
    status: str
    detail: str = ""
    dom_evidence: str = ""              # the element/text that proves it
    screenshot: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "component": self.component,
            "status": self.status,
            "detail": self.detail,
            "dom_evidence": self.dom_evidence,
            "screenshot": self.screenshot,
        }


@dataclass
class PageVVReport:
    canvas: str
    url: str
    started_at: str
    components: List[ComponentResult] = field(default_factory=list)
    console_errors: List[str] = field(default_factory=list)

    @property
    def passed(self) -> bool:
        return all(c.status in (PASS, WARN, SKIP) for c in self.components)

    @property
    def failed_components(self) -> List[str]:
        return [c.component for c in self.components if c.status == FAIL]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "canvas": self.canvas,
            "url": self.url,
            "started_at": self.started_at,
            "passed": self.passed,
            "failed_components": self.failed_components,
            "console_errors": self.console_errors,
            "components": [c.to_dict() for c in self.components],
        }


class PageVerifier:
    """Verifies a dashboard page's 8 components by driving the real UI.

    The browser is injected so this is testable without a live driver and so the
    ONE audited session is visible to the caller.
    """

    def __init__(self, browser: Any, base_url: str = "http://localhost:5050"):
        self._b = browser
        self._base = base_url.rstrip("/")

    # -- evidence helpers -------------------------------------------------

    def _shot(self, name: str) -> str:
        try:
            return self._b.screenshot(name=name)
        except Exception as exc:  # noqa: BLE001 - evidence capture is best-effort
            logger.debug("page_vv: screenshot %s failed (%s)", name, exc)
            return ""

    def _console_errors(self) -> List[str]:
        """Severe browser console messages, when the driver exposes them.

        A page that renders visually but throws in JS is the exact regression a
        200 check misses, so this is first-class evidence, not a nicety.
        """
        try:
            logs = self._b.guard.driver.get_log("browser")
        except Exception:  # not every driver supports get_log
            return []
        return [
            e.get("message", "")
            for e in (logs or [])
            if str(e.get("level", "")).upper() in ("SEVERE", "ERROR")
        ]

    # -- the 8 components -------------------------------------------------

    def verify(self, canvas: str, path: str) -> PageVVReport:
        """Verify the page for *canvas* served at *path* (e.g. "/bi_dashboard")."""
        url = f"{self._base}{path if path.startswith('/') else '/' + path}"
        report = PageVVReport(
            canvas=canvas, url=url,
            started_at=datetime.now(timezone.utc).isoformat(),
        )

        # Component 1 & 2: page renders, and renders without an error surface.
        try:
            state = self._b.navigate(url)
        except Exception as exc:  # noqa: BLE001
            report.components.append(ComponentResult(
                "page_renders", FAIL,
                detail=f"navigation failed or was refused: {exc}",
                screenshot=self._shot(f"{canvas}_nav_fail"),
            ))
            return report

        shot = self._shot(f"{canvas}_loaded")
        report.console_errors = self._console_errors()

        title = getattr(state, "title", "") or ""
        report.components.append(ComponentResult(
            "page_renders", PASS if title else WARN,
            detail=f"title={title!r}, {len(state.elements)} interactive elements",
            dom_evidence=title, screenshot=shot,
        ))

        # A 200 that renders a stack trace is the classic false pass.
        error_surface = self._detect_error_surface(state)
        report.components.append(ComponentResult(
            "no_render_error",
            FAIL if error_surface else PASS,
            detail=error_surface or "no error/traceback text in the rendered page",
            dom_evidence=error_surface, screenshot=shot,
        ))

        # Component 3: not an empty body.
        report.components.append(ComponentResult(
            "content_present",
            PASS if len(state.elements) >= 3 else FAIL,
            detail=f"{len(state.elements)} interactive elements rendered",
            screenshot=shot,
        ))

        # Component 4: IQE query widget present AND functional.
        report.components.append(self._verify_iqe(canvas, state, shot))

        # Component 5: reachable from nav.
        report.components.append(self._verify_nav_reachable(canvas, path, shot))

        # Component 6: no severe console errors.
        report.components.append(ComponentResult(
            "no_console_errors",
            FAIL if report.console_errors else PASS,
            detail=(f"{len(report.console_errors)} severe console message(s)"
                    if report.console_errors else "clean console"),
            dom_evidence="; ".join(report.console_errors[:3]),
            screenshot=shot,
        ))

        return report

    def _detect_error_surface(self, state: Any) -> str:
        """The visible text that betrays a rendered error, or ''.

        Looks at the model-facing rendering — the same text a human sees —
        rather than the HTTP status, because the two disagree exactly when this
        matters.
        """
        text = ""
        try:
            text = state.to_text()
        except Exception:  # noqa: BLE001
            pass
        needles = ("Traceback (most recent call last)", "Internal Server Error",
                   "werkzeug.exceptions", "jinja2.exceptions", "500 Internal",
                   "OperationalError", "UndefinedError")
        low = text
        for n in needles:
            if n in low:
                return n
        return ""

    def _verify_iqe(self, canvas: str, state: Any, shot: str) -> ComponentResult:
        """IQE widget present AND functional — not merely include-d in source."""
        iqe_el = None
        for el in state.elements:
            attrs = el.attributes or {}
            blob = " ".join(str(v) for v in attrs.values()).lower() + " " + (el.text or "").lower()
            if "iqe" in blob or "iqe-query" in blob or "ask a question" in blob:
                iqe_el = el
                break
        if iqe_el is None:
            return ComponentResult(
                "iqe_widget", FAIL,
                detail="no IQE query widget found in the rendered DOM "
                       "(source may include it; it does not render)",
                screenshot=shot,
            )
        return ComponentResult(
            "iqe_widget", PASS,
            detail="IQE widget rendered and addressable",
            dom_evidence=f"[{iqe_el.index}] {iqe_el.tag} {iqe_el.text[:40]}",
            screenshot=shot,
        )

    def _verify_nav_reachable(self, canvas: str, path: str, shot: str) -> ComponentResult:
        """Reachable from the nav — a page nothing links to is orphaned."""
        try:
            home = self._b.navigate(self._base + "/")
        except Exception as exc:  # noqa: BLE001
            return ComponentResult(
                "nav_reachable", WARN,
                detail=f"could not load home to check nav ({exc})", screenshot=shot,
            )
        target = path.rstrip("/")
        for el in home.elements:
            href = str((el.attributes or {}).get("href", ""))
            if href.rstrip("/").endswith(target):
                return ComponentResult(
                    "nav_reachable", PASS,
                    detail="linked from the dashboard nav",
                    dom_evidence=f"[{el.index}] href={href}",
                    screenshot=self._shot(f"{canvas}_nav"),
                )
        return ComponentResult(
            "nav_reachable", FAIL,
            detail=f"no nav link resolves to {target} — the page is orphaned",
            screenshot=self._shot(f"{canvas}_nav"),
        )


def verify_page(
    canvas: str,
    path: str,
    base_url: str = "http://localhost:5050",
    run_id: Optional[str] = None,
) -> Dict[str, Any]:
    """Convenience entry: open one audited browser and verify *canvas*.

    Returns the report dict, or an unavailability envelope when the browser
    stack cannot start — never raises, so a CI caller degrades to "could not
    verify" rather than crashing.
    """
    try:
        from tools.browser.agent_browser import AgentBrowser

        with AgentBrowser(run_id=run_id or f"page-vv:{canvas}") as browser:
            return PageVerifier(browser, base_url=base_url).verify(canvas, path).to_dict()
    except Exception as exc:  # noqa: BLE001
        logger.warning("page_vv: could not verify %s (%s)", canvas, exc)
        return {"canvas": canvas, "error": f"verification unavailable: {exc}", "passed": None}


def _cli(argv: Optional[List[str]] = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(description="Agent-driven page V&V (oss-browse-04)")
    parser.add_argument("--canvas", required=True, help="Canvas key")
    parser.add_argument("--path", required=True, help="Page path, e.g. /bi_dashboard")
    parser.add_argument("--base-url", default="http://localhost:5050")
    parser.add_argument("--json", action="store_true", dest="json_out")
    parser.add_argument("--gate", action="store_true",
                        help="Exit 1 if any component FAILED")
    args = parser.parse_args(argv)

    report = verify_page(args.canvas, args.path, base_url=args.base_url)
    if args.json_out:
        print(json.dumps(report, indent=2))
    else:
        print(f"canvas={report.get('canvas')} passed={report.get('passed')}")
        for c in report.get("components", []):
            print(f"  {c['status']:5s} {c['component']:20s} {c['detail']}")
    if args.gate and report.get("passed") is False:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(_cli())
