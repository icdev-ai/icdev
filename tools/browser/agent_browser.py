#!/usr/bin/env python3
# CUI // SP-CTI
"""Agent browser — indexed-element page representation over Selenium.

The load-bearing idea is the **page representation**, not the loop. ICDEV
already has several agent loops (``icdev.tools.llm.agent_loop``, ACE agent
mode, the harness). What no ICDEV agent had was a way to *see* a web page:
:func:`AgentBrowser.read_state` extracts every interactive element and assigns
each a stable integer index, so a model acts via ``click(14)`` instead of
inventing a CSS selector it cannot verify.

Built on :func:`tools.browser.driver_manager.get_driver` — Selenium with
vendored msedgedriver/chromedriver and **no runtime downloads**. This module
deliberately does not introduce Python Playwright or a chromium download; the
repo's ``@playwright/test`` setup is npm-based E2E tooling for our own
dashboard and is not the agent path.

Surface
-------
- :meth:`AgentBrowser.read_state` → :class:`PageState` (indexed elements + title/URL,
  optional screenshot)
- :meth:`AgentBrowser.navigate`
- :meth:`AgentBrowser.click`
- :meth:`AgentBrowser.type_text`
- :meth:`AgentBrowser.select`
- :meth:`AgentBrowser.press`
- :meth:`AgentBrowser.screenshot`

DOM verbosity is governed by the ``include_attributes`` allowlist in
``args/agent_browser.yaml`` — the single knob that decides how much of the page
reaches the model.

The assertion half is **reused, not rebuilt**: :meth:`AgentBrowser.validate`
delegates to ``tools/testing/screenshot_validator.py`` (also exposed as the MCP
tool ``validate_screenshot``).

Usage::

    from tools.browser.agent_browser import AgentBrowser

    with AgentBrowser() as b:
        state = b.navigate("http://localhost:5050")
        print(state.to_text())          # what the model sees
        b.click(3)
        b.type_text(7, "kanban")
        b.press("Enter")
        state = b.read_state(screenshot=True)

CLI::

    python tools/browser/agent_browser.py --url http://localhost:5050 --text
    python tools/browser/agent_browser.py --url http://localhost:5050 --json
    python tools/browser/agent_browser.py --config --json

Known limitations
-----------------
- **Cross-origin iframes are not traversed.** Same-document content and *open*
  shadow roots are indexed; a cross-origin ``<iframe>`` is surfaced as a single
  element (its frame box), not as its contents.
- Element indices are valid only for the state read that produced them. Any
  action against an index the current DOM no longer carries raises
  :class:`StaleIndexError`, whose message tells the model to re-read state.
"""

from __future__ import annotations

import json
import re
import sys
import time
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any, Dict, List, Optional
from urllib.parse import urlparse

import yaml

from tools.logging.icdev_logger import get_logger

logger = get_logger("icdev.browser.agent_browser")

BASE_DIR = Path(__file__).resolve().parent.parent.parent
_ARGS_FILE = BASE_DIR / "args" / "agent_browser.yaml"

#: Attribute stamped onto each indexed element so actions can re-locate it.
INDEX_ATTR = "data-icdev-agent-index"


# ── Errors ────────────────────────────────────────────────────────────────────


class AgentBrowserError(RuntimeError):
    """Base class for agent-browser failures."""


class StaleIndexError(AgentBrowserError):
    """Raised when an element index is not present in the current DOM.

    The message is written for a model to act on: it says to call
    ``read_state`` again rather than describing internals.
    """


class NavigationBlockedError(AgentBrowserError):
    """Raised when a URL fails the scheme / domain gate in args/agent_browser.yaml."""


# ── Config ────────────────────────────────────────────────────────────────────

_DEFAULT_CONFIG: Dict[str, Any] = {
    "include_attributes": [
        "id", "name", "type", "placeholder", "aria-label", "title",
        "alt", "value", "href", "role", "data-testid",
    ],
    "drop_attrs_matching_text": True,
    "max_elements": 200,
    "max_text_length": 120,
    "max_attr_length": 120,
    "viewport_only": False,
    "occlusion_check": True,
    "include_disabled": True,
    "interactive_selectors": [
        "a[href]", "button", "input", "select", "textarea", "summary",
        "[contenteditable='true']", "[onclick]", "[tabindex]:not([tabindex='-1'])",
        "[role='button']", "[role='link']", "[role='checkbox']", "[role='radio']",
        "[role='switch']", "[role='tab']", "[role='menuitem']", "[role='option']",
        "[role='combobox']", "[role='searchbox']", "[role='textbox']",
    ],
    "navigation": {
        "page_load_timeout": 30,
        "script_timeout": 30,
        "settle_ms": 350,
        "allowed_schemes": ["http", "https", "about"],
        "allowed_domains": [],
        "blocked_domains": [],
    },
    "driver": {"headless": True, "window_size": [1440, 900]},
    "screenshot": {"dir": "playwright/screenshots", "default_name": "agent_browser_state"},
}


def load_config(path: Optional[Path] = None) -> Dict[str, Any]:
    """Return the ``agent_browser:`` block from ``args/agent_browser.yaml``.

    Missing file or missing keys fall back to :data:`_DEFAULT_CONFIG` so the
    module stays importable in a stripped checkout. Nested dicts are merged one
    level deep (``navigation``, ``driver``, ``screenshot``).
    """
    cfg = json.loads(json.dumps(_DEFAULT_CONFIG))  # deep copy, stdlib only
    args_file = path or _ARGS_FILE
    if not args_file.exists():
        logger.warning("agent_browser: %s not found; using built-in defaults", args_file)
        return cfg

    try:
        raw = yaml.safe_load(args_file.read_text(encoding="utf-8")) or {}
    except Exception as exc:  # noqa: BLE001 - malformed config must not break import
        logger.warning("agent_browser: failed to parse %s (%s); using defaults", args_file, exc)
        return cfg

    block = raw.get("agent_browser") or {}
    for key, value in block.items():
        if isinstance(value, dict) and isinstance(cfg.get(key), dict):
            cfg[key].update(value)
        else:
            cfg[key] = value
    return cfg


# ── Data model ────────────────────────────────────────────────────────────────


@dataclass
class IndexedElement:
    """One interactive element, addressable by :attr:`index`.

    Attributes:
        index: Stable integer for this state read. Pass it to click/type/select.
        tag: Lowercase tag name (``button``, ``input``, …).
        role: Explicit ``role`` attribute when present, else derived from the tag.
        text: Visible text, whitespace-collapsed and truncated.
        attributes: Allowlisted attributes only (``include_attributes``).
        bounds: ``{x, y, width, height}`` in CSS pixels, page coordinates.
        in_viewport: Whether the element's box intersects the current viewport.
        disabled: Whether the control is disabled / ``aria-disabled``.
    """

    index: int
    tag: str
    role: str
    text: str = ""
    attributes: Dict[str, str] = field(default_factory=dict)
    bounds: Dict[str, float] = field(default_factory=dict)
    in_viewport: bool = True
    disabled: bool = False

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    def to_line(self) -> str:
        """Render as one prompt line: ``[14] <button> Submit (id=save, type=submit)``."""
        parts = [f"[{self.index}]", f"<{self.tag}>"]
        if self.role and self.role != self.tag:
            parts.append(f"role={self.role}")
        if self.text:
            parts.append(self.text)
        if self.attributes:
            attrs = ", ".join(f"{k}={v}" for k, v in self.attributes.items())
            parts.append(f"({attrs})")
        if self.disabled:
            parts.append("[disabled]")
        if not self.in_viewport:
            parts.append("[off-screen]")
        return " ".join(parts)


@dataclass
class PageState:
    """A single observation of the page: what is there and what can be acted on."""

    url: str
    title: str
    elements: List[IndexedElement] = field(default_factory=list)
    generation: int = 0
    truncated: bool = False
    screenshot_path: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "url": self.url,
            "title": self.title,
            "generation": self.generation,
            "truncated": self.truncated,
            "screenshot_path": self.screenshot_path,
            "element_count": len(self.elements),
            "elements": [e.to_dict() for e in self.elements],
        }

    def to_text(self) -> str:
        """Render the state the way a model should see it — one line per element."""
        header = [f"URL: {self.url}", f"Title: {self.title}"]
        if self.screenshot_path:
            header.append(f"Screenshot: {self.screenshot_path}")
        header.append(f"Interactive elements ({len(self.elements)}):")
        lines = [e.to_line() for e in self.elements]
        if self.truncated:
            lines.append("… element list truncated (max_elements reached)")
        if not self.elements:
            lines.append("(none found — the page may still be loading or have no controls)")
        return "\n".join(header + lines)

    def find(self, index: int) -> Optional[IndexedElement]:
        """Return the element carrying *index*, or None."""
        for el in self.elements:
            if el.index == index:
                return el
        return None


@dataclass
class ActionResult:
    """Outcome of a single action, shaped for feeding straight back to a model."""

    action: str
    index: Optional[int] = None
    ok: bool = True
    detail: str = ""
    url_before: str = ""
    url_after: str = ""

    @property
    def url_changed(self) -> bool:
        return self.url_before != self.url_after

    def to_dict(self) -> Dict[str, Any]:
        data = asdict(self)
        data["url_changed"] = self.url_changed
        return data

    def to_text(self) -> str:
        head = f"{self.action}" + (f"({self.index})" if self.index is not None else "")
        status = "ok" if self.ok else "failed"
        out = f"{head}: {status}"
        if self.detail:
            out += f" — {self.detail}"
        if self.url_changed:
            out += f"\nURL changed: {self.url_before} -> {self.url_after}. Call read_state to re-index."
        return out


# ── JavaScript ────────────────────────────────────────────────────────────────

# Extraction runs as ONE round trip. Everything the representation needs —
# geometry, computed visibility, occlusion, allowlisted attributes — is decided
# in the page and returned as plain JSON. Python never walks the DOM node by
# node (that would be one round trip per element and unusably slow).
_EXTRACT_JS = r"""
const cfg = arguments[0];
const SEL = cfg.selectors.join(',');
const INDEX_ATTR = cfg.index_attr;

// Clear markers from any previous read so indices never overlap generations.
function clearMarkers(root) {
  root.querySelectorAll('[' + INDEX_ATTR + ']').forEach(function (el) {
    el.removeAttribute(INDEX_ATTR);
  });
  root.querySelectorAll('*').forEach(function (el) {
    if (el.shadowRoot) { clearMarkers(el.shadowRoot); }
  });
}
clearMarkers(document);

// Collect the document plus every OPEN shadow root, in discovery order.
function collectRoots(root, out) {
  out.push(root);
  root.querySelectorAll('*').forEach(function (el) {
    if (el.shadowRoot) { collectRoots(el.shadowRoot, out); }
  });
}
const roots = [];
collectRoots(document, roots);

const vw = window.innerWidth || document.documentElement.clientWidth;
const vh = window.innerHeight || document.documentElement.clientHeight;

function collapse(s) { return (s || '').replace(/\s+/g, ' ').trim(); }
function truncate(s, n) { return s.length > n ? s.slice(0, n - 1) + '…' : s; }

function isRendered(el, rect) {
  if (rect.width <= 0 || rect.height <= 0) { return false; }
  const st = window.getComputedStyle(el);
  if (st.display === 'none' || st.visibility === 'hidden') { return false; }
  if (parseFloat(st.opacity || '1') < 0.01) { return false; }
  return true;
}

function roleOf(el) {
  const explicit = el.getAttribute('role');
  if (explicit) { return explicit.toLowerCase(); }
  const tag = el.tagName.toLowerCase();
  if (tag === 'a') { return 'link'; }
  if (tag === 'input') { return (el.getAttribute('type') || 'text').toLowerCase(); }
  if (tag === 'textarea') { return 'textbox'; }
  if (el.isContentEditable) { return 'textbox'; }
  return tag;
}

function visibleText(el) {
  const tag = el.tagName.toLowerCase();
  if (tag === 'input') {
    const type = (el.getAttribute('type') || 'text').toLowerCase();
    if (type === 'submit' || type === 'button' || type === 'reset') {
      return collapse(el.value || el.getAttribute('aria-label') || '');
    }
    return collapse(el.getAttribute('aria-label') || el.getAttribute('placeholder') || '');
  }
  if (tag === 'select') {
    const opt = el.options && el.options[el.selectedIndex];
    return collapse((opt && opt.text) || el.getAttribute('aria-label') || '');
  }
  const own = collapse(el.innerText || el.textContent || '');
  if (own) { return own; }
  return collapse(el.getAttribute('aria-label') || el.getAttribute('title') || '');
}

function isDisabled(el) {
  if (el.disabled === true) { return true; }
  return (el.getAttribute('aria-disabled') || '').toLowerCase() === 'true';
}

// Only outermost interactive nodes are indexed, EXCEPT real form controls —
// a <button> inside an <a> is still its own target, a <span role=button>
// inside an <a> is not.
const CONTROL_TAGS = { input: 1, select: 1, textarea: 1, button: 1, a: 1 };
function hasIndexedAncestor(el) {
  let p = el.parentElement;
  while (p) {
    if (p.matches && p.matches(SEL)) { return true; }
    p = p.parentElement;
  }
  return false;
}

const out = [];
let truncated = false;

for (const root of roots) {
  const found = root.querySelectorAll(SEL);
  for (const el of found) {
    if (out.length >= cfg.max_elements) { truncated = true; break; }

    const tag = el.tagName.toLowerCase();
    if (!CONTROL_TAGS[tag] && hasIndexedAncestor(el)) { continue; }

    const disabled = isDisabled(el);
    if (disabled && !cfg.include_disabled) { continue; }

    const rect = el.getBoundingClientRect();
    if (!isRendered(el, rect)) { continue; }

    const inViewport = rect.bottom > 0 && rect.right > 0 && rect.top < vh && rect.left < vw;
    if (cfg.viewport_only && !inViewport) { continue; }

    // Occlusion: only meaningful for a point actually inside the viewport.
    if (cfg.occlusion_check && inViewport) {
      const cx = Math.min(Math.max(rect.left + rect.width / 2, 1), vw - 1);
      const cy = Math.min(Math.max(rect.top + rect.height / 2, 1), vh - 1);
      const top = document.elementFromPoint(cx, cy);
      if (top && top !== el && !el.contains(top) && !top.contains(el)) { continue; }
    }

    const text = truncate(visibleText(el), cfg.max_text_length);

    const attrs = {};
    for (const name of cfg.include_attributes) {
      let val = el.getAttribute(name);
      if (val === null || val === undefined) { continue; }
      val = collapse(String(val));
      if (!val) { continue; }
      if (cfg.drop_attrs_matching_text && val === text) { continue; }
      attrs[name] = truncate(val, cfg.max_attr_length);
    }

    const index = out.length;
    el.setAttribute(INDEX_ATTR, String(index));
    out.push({
      index: index,
      tag: tag,
      role: roleOf(el),
      text: text,
      attributes: attrs,
      bounds: {
        x: Math.round(rect.left + window.scrollX),
        y: Math.round(rect.top + window.scrollY),
        width: Math.round(rect.width),
        height: Math.round(rect.height)
      },
      in_viewport: inViewport,
      disabled: disabled
    });
  }
  if (truncated) { break; }
}

return {
  url: window.location.href,
  title: document.title || '',
  truncated: truncated,
  elements: out
};
"""

# Re-locate by marker attribute. Written as a JS walk rather than a Python-side
# CSS lookup because find_element cannot pierce shadow roots.
_LOCATE_JS = r"""
const target = String(arguments[0]);
const attr = arguments[1];
function find(root) {
  const direct = root.querySelector('[' + attr + '="' + target + '"]');
  if (direct) { return direct; }
  const all = root.querySelectorAll('*');
  for (const el of all) {
    if (el.shadowRoot) {
      const hit = find(el.shadowRoot);
      if (hit) { return hit; }
    }
  }
  return null;
}
return find(document);
"""


# ── Key mapping ───────────────────────────────────────────────────────────────

#: Model-friendly key names → selenium ``Keys`` attribute names.
KEY_ALIASES: Dict[str, str] = {
    "enter": "ENTER", "return": "ENTER", "tab": "TAB", "escape": "ESCAPE",
    "esc": "ESCAPE", "backspace": "BACK_SPACE", "delete": "DELETE",
    "space": "SPACE", "up": "ARROW_UP", "down": "ARROW_DOWN",
    "left": "ARROW_LEFT", "right": "ARROW_RIGHT", "arrowup": "ARROW_UP",
    "arrowdown": "ARROW_DOWN", "arrowleft": "ARROW_LEFT",
    "arrowright": "ARROW_RIGHT", "pageup": "PAGE_UP", "pagedown": "PAGE_DOWN",
    "home": "HOME", "end": "END",
}


def _resolve_key(name: str) -> str:
    """Map a key name to a selenium ``Keys`` value.

    Single characters pass through unchanged so ``press('a')`` works.
    """
    from selenium.webdriver.common.keys import Keys

    raw = (name or "").strip()
    if len(raw) == 1:
        return raw
    attr = KEY_ALIASES.get(raw.lower().replace("_", "").replace("-", ""))
    if attr is None:
        raise AgentBrowserError(
            f"unknown key {name!r}. Known: {', '.join(sorted(KEY_ALIASES))}, "
            "or any single character."
        )
    return getattr(Keys, attr)


# ── URL gate ──────────────────────────────────────────────────────────────────


def check_url(url: str, nav_cfg: Dict[str, Any]) -> None:
    """Raise :class:`NavigationBlockedError` if *url* fails the configured gate.

    Enforces the scheme allowlist (blocking ``javascript:`` and ``data:``
    outright), then the blocked/allowed domain lists. ``allowed_domains``
    entries match the host exactly or as a ``*.host`` suffix.
    """
    parsed = urlparse(url)
    scheme = (parsed.scheme or "").lower()
    allowed_schemes = [s.lower() for s in nav_cfg.get("allowed_schemes", [])]
    if scheme not in allowed_schemes:
        raise NavigationBlockedError(
            f"scheme {scheme or '(none)'!r} is not allowed. "
            f"Permitted: {', '.join(allowed_schemes)} "
            "(args/agent_browser.yaml → navigation.allowed_schemes)"
        )

    host = (parsed.hostname or "").lower()

    def _matches(host_value: str, pattern: str) -> bool:
        pattern = pattern.lower().lstrip(".")
        return host_value == pattern or host_value.endswith("." + pattern)

    for pattern in nav_cfg.get("blocked_domains", []) or []:
        if host and _matches(host, pattern):
            raise NavigationBlockedError(f"host {host!r} is on navigation.blocked_domains")

    allowed = nav_cfg.get("allowed_domains", []) or []
    if allowed and host and not any(_matches(host, p) for p in allowed):
        raise NavigationBlockedError(
            f"host {host!r} is not on navigation.allowed_domains "
            f"({', '.join(allowed)})"
        )


# ── AgentBrowser ──────────────────────────────────────────────────────────────


class AgentBrowser:
    """A browser an agent can actually drive, addressed by element index.

    The driver is created lazily on first use, so constructing an
    ``AgentBrowser`` is cheap and safe in environments without a browser.

    Args:
        driver: Pre-built Selenium WebDriver. When omitted, one is created via
            ``tools.browser.driver_manager.get_driver`` using the ``driver:``
            block of ``args/agent_browser.yaml``.
        config: Config override. Defaults to :func:`load_config`.
        headless: Overrides ``driver.headless`` when a driver is created here.

    Ownership:
        When this class creates the driver it also quits it in :meth:`close`.
        An injected driver is left alone — the caller keeps ownership.
    """

    def __init__(
        self,
        driver: Any = None,
        config: Optional[Dict[str, Any]] = None,
        headless: Optional[bool] = None,
    ) -> None:
        self.config = config if config is not None else load_config()
        self._driver = driver
        self._owns_driver = driver is None
        self._headless = headless
        self._generation = 0
        self._state: Optional[PageState] = None

    # ── lifecycle ────────────────────────────────────────────────────────

    @property
    def driver(self) -> Any:
        """Return the WebDriver, creating it on first access."""
        if self._driver is None:
            from tools.browser.driver_manager import get_driver

            drv_cfg = self.config.get("driver", {})
            size = drv_cfg.get("window_size") or [1440, 900]
            headless = self._headless if self._headless is not None else bool(drv_cfg.get("headless", True))
            self._driver = get_driver(headless=headless, window_size=(int(size[0]), int(size[1])))
            nav = self.config.get("navigation", {})
            try:
                self._driver.set_page_load_timeout(int(nav.get("page_load_timeout", 30)))
                self._driver.set_script_timeout(int(nav.get("script_timeout", 30)))
            except Exception as exc:  # noqa: BLE001 - timeouts are best-effort
                logger.debug("agent_browser: could not set timeouts (%s)", exc)
        return self._driver

    def close(self) -> None:
        """Quit the driver when this instance created it. Idempotent."""
        if self._driver is not None and self._owns_driver:
            try:
                self._driver.quit()
            except Exception as exc:  # noqa: BLE001 - teardown must not raise
                logger.debug("agent_browser: driver.quit() failed (%s)", exc)
        self._driver = None
        self._state = None

    def __enter__(self) -> "AgentBrowser":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()

    # ── observation ──────────────────────────────────────────────────────

    def read_state(self, screenshot: bool = False, name: Optional[str] = None) -> PageState:
        """Extract the indexed page representation.

        Every interactive, rendered element gets an integer index in DOM order.
        Indices are only valid until the next ``read_state`` — acting on a stale
        one raises :class:`StaleIndexError` rather than clicking the wrong thing.

        Args:
            screenshot: Also capture a PNG and record its path on the state.
            name: Screenshot filename stem (defaults to ``screenshot.default_name``).

        Returns:
            PageState with ``url``, ``title``, ``elements``, and ``truncated``.
        """
        payload = {
            "selectors": self.config.get("interactive_selectors", []),
            "include_attributes": self.config.get("include_attributes", []),
            "drop_attrs_matching_text": bool(self.config.get("drop_attrs_matching_text", True)),
            "max_elements": int(self.config.get("max_elements", 200)),
            "max_text_length": int(self.config.get("max_text_length", 120)),
            "max_attr_length": int(self.config.get("max_attr_length", 120)),
            "viewport_only": bool(self.config.get("viewport_only", False)),
            "occlusion_check": bool(self.config.get("occlusion_check", True)),
            "include_disabled": bool(self.config.get("include_disabled", True)),
            "index_attr": INDEX_ATTR,
        }
        raw = self.driver.execute_script(_EXTRACT_JS, payload) or {}

        elements = [
            IndexedElement(
                index=int(item.get("index", i)),
                tag=str(item.get("tag", "")),
                role=str(item.get("role", "")),
                text=str(item.get("text", "")),
                attributes=dict(item.get("attributes") or {}),
                bounds=dict(item.get("bounds") or {}),
                in_viewport=bool(item.get("in_viewport", True)),
                disabled=bool(item.get("disabled", False)),
            )
            for i, item in enumerate(raw.get("elements") or [])
        ]

        self._generation += 1
        state = PageState(
            url=str(raw.get("url") or self.driver.current_url),
            title=str(raw.get("title") or ""),
            elements=elements,
            generation=self._generation,
            truncated=bool(raw.get("truncated", False)),
        )
        if screenshot:
            state.screenshot_path = self.screenshot(name=name)
        self._state = state
        logger.info(
            "agent_browser: read_state gen=%d url=%s elements=%d truncated=%s",
            state.generation, state.url, len(state.elements), state.truncated,
        )
        return state

    @property
    def state(self) -> Optional[PageState]:
        """The most recent :class:`PageState`, or None before the first read."""
        return self._state

    # ── actions ──────────────────────────────────────────────────────────

    def navigate(self, url: str) -> PageState:
        """Load *url* and return the freshly-indexed page state.

        Raises:
            NavigationBlockedError: if the URL fails the scheme/domain gate.
        """
        nav = self.config.get("navigation", {})
        check_url(url, nav)
        logger.info("agent_browser: navigate %s", url)
        self.driver.get(url)
        self._settle()
        return self.read_state()

    def click(self, index: int) -> ActionResult:
        """Click the element carrying *index*.

        Falls back to a scripted click when the native click is intercepted
        (sticky headers and overlays are the usual cause).
        """
        from selenium.common.exceptions import (
            ElementClickInterceptedException,
            ElementNotInteractableException,
        )

        el = self._locate(index)
        url_before = self.driver.current_url
        self._scroll_into_view(el)
        detail = "native click"
        try:
            el.click()
        except (ElementClickInterceptedException, ElementNotInteractableException) as exc:
            logger.debug("agent_browser: native click on %d failed (%s); using script click", index, exc)
            self.driver.execute_script("arguments[0].click();", el)
            detail = "script click (native was intercepted)"
        self._settle()
        return ActionResult(
            action="click", index=index, ok=True, detail=detail,
            url_before=url_before, url_after=self.driver.current_url,
        )

    def type_text(
        self,
        index: int,
        text: str,
        clear: bool = True,
        enter: bool = False,
    ) -> ActionResult:
        """Type *text* into the element carrying *index*.

        Args:
            index: Target element index from the latest ``read_state``.
            text: Text to type.
            clear: Clear existing content first (default True).
            enter: Send Enter after typing — the common "search box" pattern.
        """
        el = self._locate(index)
        url_before = self.driver.current_url
        self._scroll_into_view(el)
        if clear:
            try:
                el.clear()
            except Exception as exc:  # noqa: BLE001 - clear() is unsupported on some controls
                logger.debug("agent_browser: clear() on %d failed (%s); typing over", index, exc)
        el.send_keys(text)
        if enter:
            el.send_keys(_resolve_key("enter"))
        self._settle()
        return ActionResult(
            action="type", index=index, ok=True,
            detail=f"typed {len(text)} chars" + (" + Enter" if enter else ""),
            url_before=url_before, url_after=self.driver.current_url,
        )

    def select(self, index: int, value: str) -> ActionResult:
        """Choose *value* in the ``<select>`` at *index*.

        Matches by option value first, then by visible text — a model reading
        the state sees the visible text, so it must work as a selector.
        """
        from selenium.webdriver.support.ui import Select

        el = self._locate(index)
        if el.tag_name.lower() != "select":
            raise AgentBrowserError(
                f"element {index} is a <{el.tag_name.lower()}>, not a <select>. "
                "Use click() for custom dropdowns, then click the option's index."
            )
        url_before = self.driver.current_url
        picker = Select(el)
        try:
            picker.select_by_value(value)
            detail = f"selected by value {value!r}"
        except Exception:  # noqa: BLE001 - fall through to visible-text match
            picker.select_by_visible_text(value)
            detail = f"selected by visible text {value!r}"
        self._settle()
        return ActionResult(
            action="select", index=index, ok=True, detail=detail,
            url_before=url_before, url_after=self.driver.current_url,
        )

    def press(self, key: str, index: Optional[int] = None) -> ActionResult:
        """Send *key* to the element at *index*, or to the focused element.

        Accepts model-friendly names (``Enter``, ``Escape``, ``ArrowDown``, ``Tab``)
        and any single character.
        """
        resolved = _resolve_key(key)
        url_before = self.driver.current_url
        target = self._locate(index) if index is not None else self.driver.switch_to.active_element
        target.send_keys(resolved)
        self._settle()
        return ActionResult(
            action="press", index=index, ok=True, detail=f"sent {key!r}",
            url_before=url_before, url_after=self.driver.current_url,
        )

    def screenshot(self, name: Optional[str] = None) -> str:
        """Save a PNG under ``playwright/screenshots/`` and return its path.

        The directory is fixed by repo guardrail so the assertion half
        (``screenshot_validator.py`` / MCP ``validate_screenshot``) can find it.
        """
        shot_cfg = self.config.get("screenshot", {})
        stem = name or shot_cfg.get("default_name", "agent_browser_state")
        stem = re.sub(r"[^A-Za-z0-9_.-]", "_", str(stem))
        if stem.lower().endswith(".png"):
            stem = stem[:-4]
        stem = stem.replace("..", "_") or shot_cfg.get("default_name", "agent_browser_state")
        out_dir = BASE_DIR / shot_cfg.get("dir", "playwright/screenshots")
        out_dir.mkdir(parents=True, exist_ok=True)
        path = out_dir / f"{stem}.png"
        self.driver.save_screenshot(str(path))
        logger.info("agent_browser: screenshot -> %s", path)
        return str(path)

    # ── assertion half (reused, not rebuilt) ─────────────────────────────

    def validate(self, assertion: str, name: Optional[str] = None) -> Dict[str, Any]:
        """Capture a screenshot and check *assertion* with the existing vision validator.

        Delegates to ``tools/testing/screenshot_validator.py::validate_screenshot``
        — the same code path behind the MCP tool ``validate_screenshot``. Nothing
        about vision validation is reimplemented here.

        Returns:
            The validator's result dict (``passed`` is None when no vision model
            is configured).
        """
        from tools.testing.screenshot_validator import validate_screenshot

        path = self.screenshot(name=name)
        return validate_screenshot(path, assertion).to_dict()

    # ── internals ────────────────────────────────────────────────────────

    def _locate(self, index: int) -> Any:
        """Return the live WebElement for *index*, or raise :class:`StaleIndexError`."""
        if index is None:
            raise AgentBrowserError("an element index is required")
        try:
            idx = int(index)
        except (TypeError, ValueError) as exc:
            raise AgentBrowserError(f"element index must be an integer, got {index!r}") from exc

        element = self.driver.execute_script(_LOCATE_JS, idx, INDEX_ATTR)
        if element is None:
            known = len(self._state.elements) if self._state else 0
            raise StaleIndexError(
                f"element index {idx} is not on the current page "
                f"(last state indexed {known} elements). "
                "The page changed — call read_state to get fresh indices."
            )
        return element

    def _scroll_into_view(self, element: Any) -> None:
        """Centre *element* in the viewport; failures are non-fatal."""
        try:
            self.driver.execute_script(
                "arguments[0].scrollIntoView({block: 'center', inline: 'center'});", element
            )
        except Exception as exc:  # noqa: BLE001 - scrolling is a convenience
            logger.debug("agent_browser: scrollIntoView failed (%s)", exc)

    def _settle(self) -> None:
        """Pause for ``navigation.settle_ms`` so async re-renders land before the next read."""
        settle_ms = int(self.config.get("navigation", {}).get("settle_ms", 350))
        if settle_ms > 0:
            time.sleep(settle_ms / 1000.0)


# ── CLI ───────────────────────────────────────────────────────────────────────


def _cli(argv: Optional[List[str]] = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(
        description="ICDEV™ agent browser — indexed-element page representation",
    )
    parser.add_argument("--url", help="URL to open and index")
    parser.add_argument("--text", action="store_true", help="Print the model-facing rendering")
    parser.add_argument("--json", action="store_true", help="Print JSON")
    parser.add_argument("--screenshot", action="store_true", help="Also capture a screenshot")
    parser.add_argument("--name", help="Screenshot filename stem")
    parser.add_argument("--no-headless", action="store_true", help="Show the browser window")
    parser.add_argument("--config", action="store_true", help="Print the resolved config and exit")
    args = parser.parse_args(argv)

    if args.config or not args.url:
        cfg = load_config()
        if args.json or args.config:
            print(json.dumps(cfg, indent=2))
        else:
            parser.print_help()
        return 0

    browser = AgentBrowser(headless=not args.no_headless)
    try:
        state = browser.navigate(args.url)
        if args.screenshot:
            state.screenshot_path = browser.screenshot(name=args.name)
        if args.json:
            print(json.dumps(state.to_dict(), indent=2))
        else:
            print(state.to_text())
    except Exception as exc:  # noqa: BLE001 - CLI reports, does not traceback
        if args.json:
            print(json.dumps({"error": str(exc), "type": type(exc).__name__}))
        else:
            print(f"error: {exc}", file=sys.stderr)
        return 1
    finally:
        browser.close()
    return 0


if __name__ == "__main__":
    sys.exit(_cli())
