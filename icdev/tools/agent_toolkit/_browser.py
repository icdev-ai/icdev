# CUI // SP-CTI
"""Browser primitive for the agent toolkit (oss-browse-03).

Seam 1 of 4. Makes the oss-browse-01 agent browser available in-process
alongside ``read_file`` / ``execute_shell``, under the *same tool names* the MCP
gateway, the SAG ``browser`` bundle, and the ACE tool registry use — one
vocabulary across all four seams, so a rename can never strand the capability
behind one of them.

Deliberately a re-export, not a reimplementation: the operations live in
:mod:`tools.browser.session`, which owns the per-process session registry and
drives everything through ``AgentBrowser``. Nothing here may reach a raw
WebDriver — that would bypass the allowlist gate, the action budget, and the
audit trail that ``scope.GuardedDriver`` enforces.

Unlike the other toolkit primitives these operations are **stateful**: element
indices belong to a named session, and a session holds a live browser until
``browser_close`` (or process exit). Prefer::

    from tools.agent_toolkit import browser_navigate, browser_click, browser_close

    try:
        state = browser_navigate("http://localhost:5050")
        browser_click(14)
    finally:
        browser_close()
"""
from __future__ import annotations

from tools.browser.session import (
    BROWSER_TOOL_NAMES,
    browser_click,
    browser_close,
    browser_navigate,
    browser_press,
    browser_read_state,
    browser_screenshot,
    browser_select,
    browser_type,
)

__all__ = [
    "BROWSER_TOOL_NAMES",
    "browser_navigate",
    "browser_read_state",
    "browser_click",
    "browser_type",
    "browser_select",
    "browser_press",
    "browser_screenshot",
    "browser_close",
]
