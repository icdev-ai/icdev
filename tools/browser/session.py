# CUI // SP-CTI
"""Session broker + seam-neutral browser operations (oss-browse-03).

oss-browse-01 shipped :class:`~tools.browser.agent_browser.AgentBrowser`, whose
element indices are **per-instance** state: ``browser_click(14)`` only means
something relative to the ``browser_read_state`` that produced index 14.

Three of the four agent-tool seams — the in-process toolkit, the MCP gateway, and
the SAG runtime — dispatch one *stateless* function call at a time and have
nowhere to keep that instance between calls. So this module keeps it for them: a
process-local registry of named sessions, plus one set of operations that every
seam calls.

Registering the capability four times but implementing it once is the whole point
of oss-browse-03. A divergent second implementation behind one of the seams — a
raw ``get_driver()`` that skips ``GuardedDriver``, say — is exactly the bug this
module exists to prevent, so **every** operation here goes through
``AgentBrowser`` and inherits its allowlist gate, action budget, and audit trail.

Sessions are per-process and never shared across processes: the MCP gateway, an
ACE co-worker thread, and an in-process toolkit call each see their own registry.
``ICDEV_BROWSER_MAX_SESSIONS`` (default 4) caps how many live drivers one process
may hold — that cap is what stops a looping agent from opening browsers until the
host runs out of memory.

Usage::

    from tools.browser.session import browser_navigate, browser_click, browser_close

    browser_navigate("http://localhost:5050")     # session "default"
    browser_click(14)
    browser_close()
"""
from __future__ import annotations

import os
import threading
from typing import Any, Dict, List, Optional

from tools.logging.icdev_logger import get_logger

logger = get_logger("icdev.browser.session")

#: Session used when a caller does not name one.
DEFAULT_SESSION = "default"

#: Default cap on concurrent live drivers in one process.
DEFAULT_MAX_SESSIONS = 4

_sessions: Dict[str, Any] = {}
_lock = threading.RLock()


class SessionLimitExceeded(RuntimeError):
    """Raised when opening a new session would exceed the per-process cap."""


def max_sessions() -> int:
    """Per-process live-driver cap from ``ICDEV_BROWSER_MAX_SESSIONS``."""
    raw = os.environ.get("ICDEV_BROWSER_MAX_SESSIONS", "").strip()
    try:
        value = int(raw) if raw else DEFAULT_MAX_SESSIONS
    except ValueError:
        logger.warning(
            "browser.session: ICDEV_BROWSER_MAX_SESSIONS=%r is not an integer; using %d",
            raw, DEFAULT_MAX_SESSIONS,
        )
        return DEFAULT_MAX_SESSIONS
    return max(1, value)


def _key(session: Optional[str]) -> str:
    name = (session or DEFAULT_SESSION).strip()
    return name or DEFAULT_SESSION


# ---------------------------------------------------------------------------
# Session registry
# ---------------------------------------------------------------------------
def get_session(session: Optional[str] = None, **kwargs: Any) -> Any:
    """Return the live :class:`AgentBrowser` for *session*, creating it if needed.

    The driver itself is created lazily by ``AgentBrowser`` on first use, so
    registering a session is cheap and never launches a browser by itself.

    Args:
        session: Session name. Defaults to :data:`DEFAULT_SESSION`.
        **kwargs: Forwarded to ``AgentBrowser`` on creation (``headless``,
            ``config``, ``scope_config``, ``budget``). Ignored for a session that
            already exists — the first call fixes the policy for the session.

    Raises:
        SessionLimitExceeded: if a new session would exceed :func:`max_sessions`.
    """
    from tools.browser.agent_browser import AgentBrowser

    name = _key(session)
    with _lock:
        existing = _sessions.get(name)
        if existing is not None:
            if kwargs:
                logger.debug(
                    "browser.session: session %r already open; ignoring %s",
                    name, sorted(kwargs),
                )
            return existing
        cap = max_sessions()
        if len(_sessions) >= cap:
            raise SessionLimitExceeded(
                f"cannot open browser session {name!r}: {len(_sessions)} of {cap} "
                "sessions already open. Close one with browser_close, or raise "
                "ICDEV_BROWSER_MAX_SESSIONS."
            )
        # run_id=name so every audit row written by GuardedDriver carries the
        # session that produced it.
        browser = AgentBrowser(run_id=name, **kwargs)
        _sessions[name] = browser
        logger.info("browser.session: opened %r (%d/%d)", name, len(_sessions), cap)
        return browser


def close_session(session: Optional[str] = None) -> bool:
    """Quit and forget *session*. Returns True if a session was open."""
    name = _key(session)
    with _lock:
        browser = _sessions.pop(name, None)
    if browser is None:
        return False
    try:
        browser.close()
    except Exception as exc:  # noqa: BLE001 — teardown must not raise
        logger.warning("browser.session: closing %r failed: %s", name, exc)
    logger.info("browser.session: closed %r", name)
    return True


def close_all() -> int:
    """Close every open session. Returns how many were closed."""
    with _lock:
        names = list(_sessions)
    return sum(1 for name in names if close_session(name))


def list_sessions() -> List[Dict[str, Any]]:
    """Summarise open sessions: name, budget/scope status, last-known URL."""
    with _lock:
        items = list(_sessions.items())
    out: List[Dict[str, Any]] = []
    for name, browser in items:
        entry: Dict[str, Any] = {"session": name}
        state = getattr(browser, "state", None)
        if state is not None:
            entry["url"] = state.url
            entry["generation"] = state.generation
            entry["element_count"] = len(state.elements)
        # status() forces the driver open; only report it once a session is live.
        if getattr(browser, "_guard", None) is not None:
            try:
                entry["status"] = browser.status()
            except Exception as exc:  # noqa: BLE001 — reporting must not raise
                entry["status_error"] = str(exc)
        out.append(entry)
    return out


# ---------------------------------------------------------------------------
# Seam-neutral operations
#
# One implementation, four seams. Each returns a JSON-serialisable dict carrying
# the session name so a caller juggling several sessions can tell them apart.
# Errors propagate: a StaleIndexError telling the model to re-read the page is a
# useful observation, and each seam renders it in its own idiom (the MCP handlers
# convert to {"error": ...}, the agent-loop registry to a tool_result string).
# ---------------------------------------------------------------------------
def _tag(payload: Dict[str, Any], session: Optional[str]) -> Dict[str, Any]:
    payload["session"] = _key(session)
    return payload


def browser_navigate(
    url: str, session: Optional[str] = None, **session_kwargs: Any
) -> Dict[str, Any]:
    """Open *url* and return the freshly-indexed page state.

    Args:
        url: Absolute http/https URL. Refused unless the host is on the
            ``args/browser_scope.yaml`` allowlist.
        session: Session name. Defaults to :data:`DEFAULT_SESSION`.

    Returns:
        ``PageState.to_dict()`` plus ``session``.
    """
    browser = get_session(session, **session_kwargs)
    return _tag(browser.navigate(str(url)).to_dict(), session)


def browser_read_state(
    session: Optional[str] = None,
    screenshot: bool = False,
    name: Optional[str] = None,
) -> Dict[str, Any]:
    """Re-index the current page and return its state.

    Args:
        session: Session name.
        screenshot: Also capture a PNG and report its path.
        name: Screenshot filename stem.
    """
    browser = get_session(session)
    return _tag(browser.read_state(screenshot=bool(screenshot), name=name).to_dict(), session)


def browser_click(index: int, session: Optional[str] = None) -> Dict[str, Any]:
    """Click the element carrying *index* from the latest ``browser_read_state``."""
    browser = get_session(session)
    return _tag(browser.click(index).to_dict(), session)


def browser_type(
    index: int,
    text: str,
    session: Optional[str] = None,
    clear: bool = True,
    enter: bool = False,
) -> Dict[str, Any]:
    """Type *text* into the element at *index*.

    Credentials are written as ``<secret>NAME</secret>``; the value is resolved at
    the driver and never appears in the prompt, transcript, or audit row.

    Args:
        index: Target element index.
        text: Text to type, optionally containing secret placeholders.
        session: Session name.
        clear: Clear existing content first.
        enter: Send Enter after typing.
    """
    browser = get_session(session)
    result = browser.type_text(index, str(text), clear=bool(clear), enter=bool(enter))
    return _tag(result.to_dict(), session)


def browser_select(index: int, value: str, session: Optional[str] = None) -> Dict[str, Any]:
    """Choose *value* in the ``<select>`` at *index* (by value, then visible text)."""
    browser = get_session(session)
    return _tag(browser.select(index, str(value)).to_dict(), session)


def browser_press(
    key: str, index: Optional[int] = None, session: Optional[str] = None
) -> Dict[str, Any]:
    """Send *key* to the element at *index*, or to the focused element."""
    browser = get_session(session)
    return _tag(browser.press(str(key), index=index).to_dict(), session)


def browser_screenshot(
    session: Optional[str] = None, name: Optional[str] = None
) -> Dict[str, Any]:
    """Capture a PNG under ``playwright/screenshots/`` and return its path."""
    browser = get_session(session)
    return _tag({"screenshot_path": browser.screenshot(name=name)}, session)


def browser_close(session: Optional[str] = None) -> Dict[str, Any]:
    """Quit the browser for *session*. Idempotent."""
    return _tag({"closed": close_session(session)}, session)


#: The capability's tool-name vocabulary — the single list every seam registers
#: against, so a rename can never leave one of the four seams behind.
BROWSER_TOOL_NAMES: List[str] = [
    "browser_navigate",
    "browser_read_state",
    "browser_click",
    "browser_type",
    "browser_select",
    "browser_press",
    "browser_screenshot",
    "browser_close",
]

__all__ = [
    "DEFAULT_SESSION",
    "DEFAULT_MAX_SESSIONS",
    "BROWSER_TOOL_NAMES",
    "SessionLimitExceeded",
    "max_sessions",
    "get_session",
    "close_session",
    "close_all",
    "list_sessions",
    *BROWSER_TOOL_NAMES,
]
