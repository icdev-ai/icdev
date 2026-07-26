# CUI // SP-CTI
"""Browser primitive for the agent toolkit (oss-browse-03, seam 1 of 4).

Sits alongside :func:`tools.agent_toolkit.execute_shell` so an in-process agent
reaches a browser the same way it reaches a shell: one call, a plain dict back,
audited by default.

**No policy is implemented here.** Everything delegates to
:class:`tools.browser.agent_browser.AgentBrowser`, which holds a
``scope.GuardedDriver`` — so the default-deny domain allowlist, the scheme
allowlist, the per-run action cap, the per-step timeout, credential placeholder
substitution and the ``audit_trail`` row per action all apply without this module
restating any of them. Re-implementing any of that here would create a second
policy surface that can disagree with the enforcing one, which is exactly the
defect oss-browse-01 had to unwind before it could merge.

Session model: each call opens and closes its own browser unless the caller
passes an existing ``AgentBrowser``. That is deliberately conservative — a
long-lived driver held across agent turns is a resource leak and a scope-drift
risk (the allowlist is re-checked per action, but a parked session on a
now-denied page is a worse default). Callers that need a session pass one::

    from tools.agent_toolkit import browser_session, browser_read_state

    with browser_session(run_id="vv-001") as b:
        browser_navigate("http://localhost:5050/kanban", browser=b)
        state = browser_read_state(browser=b)
"""
from __future__ import annotations

import contextlib
from typing import Any, Dict, Iterator, Optional

from tools.logging.icdev_logger import get_logger

logger = get_logger("icdev.agent_toolkit.browser")

#: Returned instead of raising when the browser stack is unavailable, so an
#: agent loop degrades to "this tool did not work" rather than dying. Mirrors
#: execute_shell's structured-result contract.
_UNAVAILABLE = "browser unavailable"


@contextlib.contextmanager
def browser_session(
    run_id: Optional[str] = None,
    headless: bool = True,
    **kwargs: Any,
) -> Iterator[Any]:
    """Open one audited browser session and close it on exit.

    Args:
        run_id: Correlation id stamped on every audit row for this session.
        headless: Run without a visible window. Default True.
        **kwargs: Forwarded to :class:`AgentBrowser` (``scope_config``, ``budget``).

    Yields:
        The live ``AgentBrowser``, or None when the stack is unavailable.
    """
    browser = None
    try:
        from tools.browser.agent_browser import AgentBrowser

        browser = AgentBrowser(run_id=run_id, headless=headless, **kwargs)
        yield browser
    except Exception as exc:  # noqa: BLE001 - unavailability must not kill the loop
        logger.warning("browser_session unavailable: %s", exc)
        yield None
    finally:
        if browser is not None:
            with contextlib.suppress(Exception):
                browser.close()


def _result(ok: bool, **fields: Any) -> Dict[str, Any]:
    out: Dict[str, Any] = {"ok": ok}
    out.update(fields)
    return out


def _run(fn, browser: Optional[Any], run_id: Optional[str], headless: bool) -> Dict[str, Any]:
    """Apply *fn* to a browser — the caller's, or a throwaway session.

    Denials are returned, not raised: a scope refusal is information the agent
    should see and reason about, not a crash. The distinction is preserved in
    the result (``denied`` vs ``error``) so a caller can tell "policy said no"
    from "the browser broke".
    """
    if browser is not None:
        return _apply(fn, browser)
    with browser_session(run_id=run_id, headless=headless) as b:
        if b is None:
            return _result(False, error=_UNAVAILABLE)
        return _apply(fn, b)


def _apply(fn, browser: Any) -> Dict[str, Any]:
    from tools.browser.scope import ScopeViolation

    try:
        return _result(True, **fn(browser))
    except ScopeViolation as exc:
        # Policy refusal — expected, and actionable by the model.
        logger.info("browser action denied: %s", exc)
        return _result(False, denied=True, reason=str(exc))
    except Exception as exc:  # noqa: BLE001
        logger.warning("browser action failed: %s", exc)
        return _result(False, error=f"{type(exc).__name__}: {exc}")


def browser_navigate(
    url: str,
    browser: Optional[Any] = None,
    run_id: Optional[str] = None,
    headless: bool = True,
) -> Dict[str, Any]:
    """Navigate to *url* and return the indexed page state.

    Refused unless the URL clears the allowlist in ``args/browser_scope.yaml``;
    a denied host never reaches the driver.
    """
    return _run(lambda b: {"state": b.navigate(url).to_dict()}, browser, run_id, headless)


def browser_read_state(
    browser: Optional[Any] = None,
    screenshot: bool = False,
    run_id: Optional[str] = None,
    headless: bool = True,
) -> Dict[str, Any]:
    """Return the current page as indexed interactive elements.

    Observation is not charged to the action budget, but the scope check still
    runs — a redirect off the allowlist is caught before any page content is
    handed back.
    """
    return _run(
        lambda b: {"state": b.read_state(screenshot=screenshot).to_dict()},
        browser, run_id, headless,
    )


def browser_click(
    index: int,
    browser: Optional[Any] = None,
    run_id: Optional[str] = None,
    headless: bool = True,
) -> Dict[str, Any]:
    """Click the element carrying *index* from the latest ``read_state``."""
    return _run(lambda b: {"result": b.click(index).to_dict()}, browser, run_id, headless)


def browser_type(
    index: int,
    text: str,
    clear: bool = True,
    enter: bool = False,
    browser: Optional[Any] = None,
    run_id: Optional[str] = None,
    headless: bool = True,
) -> Dict[str, Any]:
    """Type *text* into the element at *index*.

    Credentials are written as ``<secret>NAME</secret>``; the value is resolved
    at the driver and never appears in the prompt, transcript, or audit row.
    """
    return _run(
        lambda b: {"result": b.type_text(index, text, clear=clear, enter=enter).to_dict()},
        browser, run_id, headless,
    )


def browser_select(
    index: int,
    value: str,
    browser: Optional[Any] = None,
    run_id: Optional[str] = None,
    headless: bool = True,
) -> Dict[str, Any]:
    """Choose *value* in the ``<select>`` at *index* (by value, then visible text)."""
    return _run(lambda b: {"result": b.select(index, value).to_dict()}, browser, run_id, headless)


def browser_press(
    key: str,
    index: Optional[int] = None,
    browser: Optional[Any] = None,
    run_id: Optional[str] = None,
    headless: bool = True,
) -> Dict[str, Any]:
    """Send *key* to the element at *index*, or to the focused element."""
    return _run(lambda b: {"result": b.press(key, index=index).to_dict()}, browser, run_id, headless)


def browser_screenshot(
    name: Optional[str] = None,
    browser: Optional[Any] = None,
    run_id: Optional[str] = None,
    headless: bool = True,
) -> Dict[str, Any]:
    """Capture a PNG under ``playwright/screenshots/`` and return its path."""
    return _run(lambda b: {"path": b.screenshot(name=name)}, browser, run_id, headless)
