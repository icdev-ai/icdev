# CUI // SP-CTI
"""Browser tool schemas for agent loops — binds :class:`AgentBrowser` to LLM tools.

Mirrors the convention in :mod:`tools.ace.agent_tools`: a registry that returns
``(tools, tool_handlers)`` for :func:`icdev.tools.llm.agent_loop.run_agent_loop`.
Handlers take ``(input_dict, stop_event)`` and return a **string** — the model
reads that string as its observation, so every return value here is the
model-facing rendering (``PageState.to_text`` / ``ActionResult.to_text``), never
a repr.

No new execution path is introduced: every handler is a thin call onto the
:class:`AgentBrowser` surface, which is itself built on the existing vendored
Selenium driver.

Usage::

    from tools.browser.agent_browser import AgentBrowser
    from tools.browser.agent_tools import BrowserToolRegistry

    with AgentBrowser() as browser:
        tools, handlers = BrowserToolRegistry(browser).build()
        run_agent_loop(..., tools=tools, tool_handlers=handlers)

Exceptions are left to propagate. The agent loop turns them into a
``tool_result`` the model can act on — a :class:`StaleIndexError` telling it to
re-read state is a *useful* observation, not a failure to hide.
"""

from __future__ import annotations

import threading
from typing import Any, Callable, Dict, List, Optional, Tuple

from tools.logging.icdev_logger import get_logger

logger = get_logger("icdev.browser.agent_tools")

ToolHandler = Callable[[Dict[str, Any], "threading.Event | None"], str]

#: Tool names exposed when ``build()`` is called with no explicit list.
DEFAULT_TOOLS: List[str] = [
    "browser_read_state",
    "browser_navigate",
    "browser_click",
    "browser_type",
    "browser_select",
    "browser_press",
    "browser_screenshot",
]

_INDEX_DESC = (
    "Element index from the most recent browser_read_state. Indices are only "
    "valid until the page changes — if the tool reports a stale index, call "
    "browser_read_state again."
)

_SCHEMAS: Dict[str, Dict[str, Any]] = {
    "browser_read_state": {
        "type": "function",
        "is_read_only": True,
        "function": {
            "name": "browser_read_state",
            "is_read_only": True,
            "description": (
                "Look at the current page. Returns the URL, title, and every "
                "interactive element with a stable integer index, e.g. "
                "'[14] <button> Submit (id=save)'. Use those indices with "
                "browser_click / browser_type / browser_select. Call this after "
                "any action that changes the page."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "screenshot": {
                        "type": "boolean",
                        "description": "Also capture a PNG and report its path.",
                    },
                },
            },
        },
    },
    "browser_navigate": {
        "type": "function",
        "function": {
            "name": "browser_navigate",
            "description": (
                "Open a URL and return the indexed page state. Only http/https "
                "URLs on the configured allowlist are permitted."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "url": {"type": "string", "description": "Absolute URL to open."},
                },
                "required": ["url"],
            },
        },
    },
    "browser_click": {
        "type": "function",
        "function": {
            "name": "browser_click",
            "description": "Click the element with the given index.",
            "parameters": {
                "type": "object",
                "properties": {"index": {"type": "integer", "description": _INDEX_DESC}},
                "required": ["index"],
            },
        },
    },
    "browser_type": {
        "type": "function",
        "function": {
            "name": "browser_type",
            "description": "Type text into the input/textarea with the given index.",
            "parameters": {
                "type": "object",
                "properties": {
                    "index": {"type": "integer", "description": _INDEX_DESC},
                    "text": {"type": "string", "description": "Text to type."},
                    "clear": {
                        "type": "boolean",
                        "description": "Clear the field first. Default true.",
                    },
                    "enter": {
                        "type": "boolean",
                        "description": "Press Enter after typing (submits most search boxes).",
                    },
                },
                "required": ["index", "text"],
            },
        },
    },
    "browser_select": {
        "type": "function",
        "function": {
            "name": "browser_select",
            "description": (
                "Choose an option in a <select> dropdown. Matches the option "
                "value first, then its visible text."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "index": {"type": "integer", "description": _INDEX_DESC},
                    "value": {"type": "string", "description": "Option value or visible text."},
                },
                "required": ["index", "value"],
            },
        },
    },
    "browser_press": {
        "type": "function",
        "function": {
            "name": "browser_press",
            "description": (
                "Send a key to the focused element, or to a given index. "
                "Names: Enter, Tab, Escape, Backspace, Delete, ArrowUp/Down/Left/Right, "
                "PageUp, PageDown, Home, End — or any single character."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "key": {"type": "string", "description": "Key name or single character."},
                    "index": {"type": "integer", "description": "Optional target index."},
                },
                "required": ["key"],
            },
        },
    },
    "browser_screenshot": {
        "type": "function",
        "is_read_only": True,
        "function": {
            "name": "browser_screenshot",
            "is_read_only": True,
            "description": (
                "Capture a PNG of the current page and return its path "
                "(under playwright/screenshots/)."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "name": {"type": "string", "description": "Filename stem."},
                },
            },
        },
    },
}


class BrowserToolRegistry:
    """Builds ``(tools, tool_handlers)`` bound to one :class:`AgentBrowser`.

    Args:
        browser: The :class:`~tools.browser.agent_browser.AgentBrowser` every
            handler drives. One browser per registry — indices are per-browser
            state, so sharing a registry across browsers would mis-address.
    """

    def __init__(self, browser: Any) -> None:
        self.browser = browser

    def build(
        self, tool_names: Optional[List[str]] = None
    ) -> Tuple[List[Dict[str, Any]], Dict[str, ToolHandler]]:
        """Return ``(tools, handlers)`` for *tool_names* (defaults to :data:`DEFAULT_TOOLS`).

        Unknown names are logged and skipped, matching ACE's registry behaviour.
        """
        names = list(tool_names or DEFAULT_TOOLS)
        tools: List[Dict[str, Any]] = []
        handlers: Dict[str, ToolHandler] = {}
        for name in names:
            schema = _SCHEMAS.get(name)
            handler = self._make_handler(name)
            if schema is None or handler is None:
                logger.warning("browser agent_tools: unknown tool %r requested — skipping", name)
                continue
            tools.append(schema)
            handlers[name] = handler
        return tools, handlers

    # ── handler factory ──────────────────────────────────────────────────

    def _make_handler(self, name: str) -> Optional[ToolHandler]:
        return {
            "browser_read_state": self._read_state,
            "browser_navigate": self._navigate,
            "browser_click": self._click,
            "browser_type": self._type,
            "browser_select": self._select,
            "browser_press": self._press,
            "browser_screenshot": self._screenshot,
        }.get(name)

    # ── handlers ─────────────────────────────────────────────────────────

    def _read_state(self, inp: Dict[str, Any], stop: "threading.Event | None") -> str:
        state = self.browser.read_state(screenshot=bool(inp.get("screenshot")))
        return state.to_text()

    def _navigate(self, inp: Dict[str, Any], stop: "threading.Event | None") -> str:
        url = str(inp.get("url") or "").strip()
        if not url:
            return "browser_navigate requires a 'url'."
        return self.browser.navigate(url).to_text()

    def _click(self, inp: Dict[str, Any], stop: "threading.Event | None") -> str:
        return self.browser.click(inp.get("index")).to_text()

    def _type(self, inp: Dict[str, Any], stop: "threading.Event | None") -> str:
        result = self.browser.type_text(
            inp.get("index"),
            str(inp.get("text", "")),
            clear=bool(inp.get("clear", True)),
            enter=bool(inp.get("enter", False)),
        )
        return result.to_text()

    def _select(self, inp: Dict[str, Any], stop: "threading.Event | None") -> str:
        return self.browser.select(inp.get("index"), str(inp.get("value", ""))).to_text()

    def _press(self, inp: Dict[str, Any], stop: "threading.Event | None") -> str:
        index = inp.get("index")
        return self.browser.press(str(inp.get("key", "")), index=index).to_text()

    def _screenshot(self, inp: Dict[str, Any], stop: "threading.Event | None") -> str:
        path = self.browser.screenshot(name=inp.get("name"))
        return f"screenshot saved: {path}"
