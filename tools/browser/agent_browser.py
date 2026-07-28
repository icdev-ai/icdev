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

Scope, budget, and audit are **not implemented here**. This class holds a
``scope.GuardedDriver`` (oss-browse-02), never a raw WebDriver, so the domain
allowlist, the scheme allowlist, the per-run action cap, the per-step timeout,
credential placeholder substitution, and the ``audit_trail`` row per action all
apply to every method below without this module restating any of them. There is
no unguarded path to the session short of the documented ``.guard.driver``
escape hatch. See ``args/browser_scope.yaml`` for the policy.

The assertion half is **reused, not rebuilt**: :meth:`AgentBrowser.validate`
delegates to ``tools/testing/screenshot_validator.py`` (also exposed as the MCP
tool ``validate_screenshot``).

Usage::

    from tools.browser.agent_browser import AgentBrowser

    with AgentBrowser(run_id="vv-001") as b:
        state = b.navigate("http://localhost:5050")   # allowlisted or refused
        print(state.to_text())          # what the model sees
        b.click(3)
        b.type_text(7, "kanban")
        b.type_text(9, "<secret>DASHBOARD_TOKEN</secret>")  # resolved at driver
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

import yaml

from tools.browser.scope import (
    ActionBudget,
    BrowserScopeConfig,
    GuardedDriver,
    NavigationDenied,
    load_scope_config,
)
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


#: Navigation refusals are raised by the scope layer, not by this module.
#:
#: ``tools/browser/scope.py`` (oss-browse-02) owns the domain allowlist, the
#: scheme allowlist, and the egress check, and it is *default-deny*: an empty
#: ``allowed_domains`` refuses every host. An earlier draft of this module
#: carried its own ``check_url`` whose empty allowlist meant "allow everything"
#: — strictly weaker, and a second policy surface to keep in sync. Re-exported
#: here so callers can catch one name.
NavigationBlockedError = NavigationDenied


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
    # Only the page-representation half lives here. Scheme/domain policy and
    # the page-load/script timeouts are owned by args/browser_scope.yaml and
    # applied by GuardedDriver — duplicating them would create a second policy
    # surface that can silently disagree with the enforcing one.
    "navigation": {"settle_ms": 350},
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


# ── AgentBrowser ──────────────────────────────────────────────────────────────


class AgentBrowser:
    """A browser an agent can actually drive, addressed by element index.

    The driver is created lazily on first use, so constructing an
    ``AgentBrowser`` is cheap and safe in environments without a browser.

    Args:
        driver: Pre-built Selenium WebDriver. When omitted, one is created via
            ``tools.browser.driver_manager.get_driver`` using the ``driver:``
            block of ``args/agent_browser.yaml``. Either way it is wrapped in a
            :class:`~tools.browser.scope.GuardedDriver` before anything touches it.
        config: Page-representation config override. Defaults to :func:`load_config`.
        headless: Overrides ``driver.headless`` when a driver is created here.
        run_id: Correlation id stamped on every audit row for this session.
        scope_config: Scope-policy override. Defaults to
            :func:`tools.browser.scope.load_scope_config` (``args/browser_scope.yaml``).
        budget: Pre-seeded :class:`~tools.browser.scope.ActionBudget`. Pass one to
            share a single per-run cap across several browser sessions.

    Ownership:
        When this class creates the driver it also quits it in :meth:`close`.
        An injected driver is left alone — the caller keeps ownership.
    """

    def __init__(
        self,
        driver: Any = None,
        config: Optional[Dict[str, Any]] = None,
        headless: Optional[bool] = None,
        run_id: Optional[str] = None,
        scope_config: Optional[BrowserScopeConfig] = None,
        budget: Optional[ActionBudget] = None,
    ) -> None:
        self.config = config if config is not None else load_config()
        self.scope_config = scope_config or load_scope_config()
        self.run_id = run_id
        self._raw_driver = driver
        self._guard: Optional[GuardedDriver] = None
        self._budget = budget
        self._owns_driver = driver is None
        self._headless = headless
        self._generation = 0
        self._state: Optional[PageState] = None

    # ── lifecycle ────────────────────────────────────────────────────────

    @property
    def guard(self) -> GuardedDriver:
        """Return the :class:`GuardedDriver`, creating the session on first access.

        Every action method goes through this. ``guard.driver`` is the single
        documented escape hatch to the raw session; nothing in this module uses it
        for a mutation.
        """
        if self._guard is None:
            raw = self._raw_driver
            if raw is None:
                from tools.browser.driver_manager import get_driver

                drv_cfg = self.config.get("driver", {})
                size = drv_cfg.get("window_size") or [1440, 900]
                headless = (
                    self._headless
                    if self._headless is not None
                    else bool(drv_cfg.get("headless", True))
                )
                raw = get_driver(
                    headless=headless, window_size=(int(size[0]), int(size[1]))
                )
                self._raw_driver = raw
            # GuardedDriver applies page-load/script timeouts from browser_scope.yaml.
            self._guard = GuardedDriver(
                raw,
                config=self.scope_config,
                run_id=self.run_id,
                budget=self._budget,
            )
        return self._guard

    @property
    def driver(self) -> Any:
        """The guarded session.

        Named ``driver`` for continuity with Selenium code, but this is a
        :class:`GuardedDriver`: ``get``, ``execute_script`` and the timeout
        setters raise ``ScopeViolation`` here. Read-only attributes
        (``current_url``, ``title``, ``switch_to``) pass through.
        """
        return self.guard

    @property
    def budget(self) -> ActionBudget:
        """The action budget for this run."""
        return self.guard.budget

    def status(self) -> Dict[str, Any]:
        """Budget plus active scope policy. Safe to log."""
        return self.guard.status()

    def close(self) -> None:
        """Quit the driver when this instance created it. Idempotent."""
        if self._raw_driver is not None and self._owns_driver:
            try:
                self._raw_driver.quit()
            except Exception as exc:  # noqa: BLE001 - teardown must not raise
                logger.debug("agent_browser: driver.quit() failed (%s)", exc)
        self._raw_driver = None
        self._guard = None
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
        raw = self._observe(_EXTRACT_JS, payload) or {}

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
            url=str(raw.get("url") or self.guard.current_url),
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

        The allowlist decision belongs to ``scope.check_navigation``; a refused
        host never reaches the driver.

        Raises:
            NavigationDenied: if the URL fails the scope gate.
            ActionBudgetExceeded: if the per-run action cap is spent.
        """
        logger.info("agent_browser: navigate %s", url)
        self.guard.navigate(url)          # gated + budgeted + audited
        self._settle()
        return self.read_state()

    def click(self, index: int) -> ActionResult:
        """Click the element carrying *index*.

        Falls back to a scripted click when the native click is intercepted
        (sticky headers and overlays are the usual cause). Both paths run inside
        one budgeted action — the fallback is the same click, not a second one.
        """
        from selenium.common.exceptions import (
            ElementClickInterceptedException,
            ElementNotInteractableException,
        )

        el = self._locate(index)
        url_before = self.guard.current_url
        self._scroll_into_view(el)
        detail = {"value": "native click"}

        def _do() -> None:
            try:
                el.click()
            except (
                ElementClickInterceptedException,
                ElementNotInteractableException,
            ) as exc:
                logger.debug(
                    "agent_browser: native click on %d failed (%s); using script click",
                    index, exc,
                )
                # guard.driver by design: this is the retry half of an action
                # already charged and audited by the enclosing run_action.
                self.guard.driver.execute_script("arguments[0].click();", el)
                detail["value"] = "script click (native was intercepted)"

        self.guard.run_action("click", _do, target=f"index={index}", detail={"index": index})
        self._settle()
        return ActionResult(
            action="click", index=index, ok=True, detail=detail["value"],
            url_before=url_before, url_after=self.guard.current_url,
        )

    def type_text(
        self,
        index: int,
        text: str,
        clear: bool = True,
        enter: bool = False,
    ) -> ActionResult:
        """Type *text* into the element carrying *index*.

        Credentials are written as ``<secret>NAME</secret>``. The placeholder is
        what the model emits and what the audit row keeps; the value is resolved
        at the driver by ``scope.SensitiveDataResolver`` (broker-authorised,
        env-sourced) and never appears in the prompt, transcript, or log.

        Args:
            index: Target element index from the latest ``read_state``.
            text: Text to type. May contain ``<secret>NAME</secret>`` placeholders.
            clear: Clear existing content first (default True).
            enter: Send Enter after typing — the common "search box" pattern.
        """
        el = self._locate(index)
        url_before = self.guard.current_url
        self._scroll_into_view(el)
        if clear:
            try:
                el.clear()
            except Exception as exc:  # noqa: BLE001 - clear() is unsupported on some controls
                logger.debug("agent_browser: clear() on %d failed (%s); typing over", index, exc)

        # Secret resolution, budget, timing, scope re-check and audit all live in
        # guard.type_text — the placeholders it returns are safe to report.
        placeholders = self.guard.type_text(el, text, clear=False)
        if enter:
            self.guard.run_action(
                "press",
                lambda: el.send_keys(_resolve_key("enter")),
                target="Enter",
                detail={"index": index},
            )
        self._settle()
        typed = "typed a credential" if placeholders else f"typed {len(text)} chars"
        return ActionResult(
            action="type", index=index, ok=True,
            detail=typed + (" + Enter" if enter else ""),
            url_before=url_before, url_after=self.guard.current_url,
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
        url_before = self.guard.current_url
        picker = Select(el)
        detail = {"value": ""}

        def _do() -> None:
            try:
                picker.select_by_value(value)
                detail["value"] = f"selected by value {value!r}"
            except Exception:  # noqa: BLE001 - fall through to visible-text match
                picker.select_by_visible_text(value)
                detail["value"] = f"selected by visible text {value!r}"

        self.guard.run_action(
            "select", _do, target=f"index={index}", detail={"index": index, "option": value}
        )
        self._settle()
        return ActionResult(
            action="select", index=index, ok=True, detail=detail["value"],
            url_before=url_before, url_after=self.guard.current_url,
        )

    def press(self, key: str, index: Optional[int] = None) -> ActionResult:
        """Send *key* to the element at *index*, or to the focused element.

        Accepts model-friendly names (``Enter``, ``Escape``, ``ArrowDown``, ``Tab``)
        and any single character.
        """
        resolved = _resolve_key(key)
        url_before = self.guard.current_url
        target = (
            self._locate(index)
            if index is not None
            else self.guard.switch_to.active_element
        )
        self.guard.run_action(
            "press",
            lambda: target.send_keys(resolved),
            target=key,
            detail={"index": index},
        )
        self._settle()
        return ActionResult(
            action="press", index=index, ok=True, detail=f"sent {key!r}",
            url_before=url_before, url_after=self.guard.current_url,
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
        self.guard.screenshot(str(path))   # budgeted + audited
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

    def _observe(self, script: str, *args: Any) -> Any:
        """Run a read-only extraction script against the current page.

        Deliberately **not** charged to the action budget: reading the page is
        how the model decides what to do, and a 50-action run that spent half its
        budget on looking would be useless. The scope check still applies — a
        redirect that walked the session off the allowlist is caught here before
        any of the page reaches the model, so observation cannot be used as a
        back door to a host ``navigate()`` would have refused.

        Uses ``guard.driver`` because ``execute_script`` is a blocked bypass
        attribute on the wrapper; this is the documented escape hatch, and it is
        read-only by construction (``_EXTRACT_JS`` and ``_LOCATE_JS`` are
        module constants, never caller-supplied).
        """
        self.guard.assert_in_scope("observe")
        return self.guard.driver.execute_script(script, *args)

    def _locate(self, index: int) -> Any:
        """Return the live WebElement for *index*, or raise :class:`StaleIndexError`."""
        if index is None:
            raise AgentBrowserError("an element index is required")
        try:
            idx = int(index)
        except (TypeError, ValueError) as exc:
            raise AgentBrowserError(f"element index must be an integer, got {index!r}") from exc

        element = self._observe(_LOCATE_JS, idx, INDEX_ATTR)
        if element is None:
            known = len(self._state.elements) if self._state else 0
            raise StaleIndexError(
                f"element index {idx} is not on the current page "
                f"(last state indexed {known} elements). "
                "The page changed — call read_state to get fresh indices."
            )
        return element

    def _scroll_into_view(self, element: Any) -> None:
        """Centre *element* in the viewport; failures are non-fatal.

        Positioning, not a mutation — uncharged for the same reason as
        :meth:`_observe`, and it always precedes an action that *is* charged.
        """
        try:
            self._observe(
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
    parser.add_argument("--run-id", help="Correlation id stamped on every audit row")
    args = parser.parse_args(argv)

    if args.config or not args.url:
        cfg = load_config()
        if args.json or args.config:
            # Both halves: page representation here, enforced policy from scope.py.
            print(json.dumps(
                {"agent_browser": cfg, "browser_scope": load_scope_config().to_dict()},
                indent=2,
            ))
        else:
            parser.print_help()
        return 0

    browser = AgentBrowser(headless=not args.no_headless, run_id=args.run_id)
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
