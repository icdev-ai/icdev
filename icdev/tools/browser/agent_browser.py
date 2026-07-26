#!/usr/bin/env python3
# CUI // SP-CTI
"""Agent browser — indexed-element page representation on top of Selenium.

Adapted from browser-use's load-bearing idea (see
``docs/spikes/oss-00-ragflow-crawl4ai-browseruse-strix-adaptation.md``, A3):
the value is not another agent loop — ICDEV already has several — it is the
**page representation**. Interactive elements are extracted and handed stable
integer indexes, so a model acts via ``click(14)`` instead of inventing a CSS
selector it cannot verify.

Built on :func:`tools.browser.driver_manager.get_driver` (vendored
msedgedriver/chromedriver, no runtime downloads). No Playwright.

Surface::

    from tools.browser.agent_browser import AgentBrowser

    with AgentBrowser() as ab:
        state = ab.navigate("http://localhost:5050/kanban")
        for el in state["elements"]:
            print(el["index"], el["role"], el["text"])
        ab.type(3, "promote")
        ab.click(7)
        ab.screenshot("kanban_after_click")

``read_state()`` returns::

    {
      "url": "...", "title": "...",
      "element_count": 12,
      "elements": [
        {"index": 0, "tag": "button", "role": "button",
         "text": "Promote", "attributes": {"id": "promote-btn"}},
        ...
      ],
      "screenshot": "playwright/screenshots/state.png"   # only when requested
    }

DOM verbosity, the candidate selector, caps, and scope controls all live in
``args/agent_browser.yaml`` — not in this module.

Scope controls (v1): ``navigate()`` refuses any http(s) host outside
``agent_browser.allowed_domains`` (localhost/127.0.0.1 by default). Credential
brokering and egress-guard integration land with oss-browse-02.

CLI::

    python tools/browser/agent_browser.py --url http://localhost:5050 --json
    python tools/browser/agent_browser.py --url http://localhost:5050 --screenshot state
"""

from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any, Dict, List, Optional
from urllib.parse import urlparse

import yaml

from tools.logging.icdev_logger import get_logger

logger = get_logger("icdev.browser.agent_browser")

BASE_DIR = Path(__file__).resolve().parent.parent.parent
ARGS_FILE = BASE_DIR / "args" / "agent_browser.yaml"

# Fallback used when args/agent_browser.yaml is absent or partial. Every key the
# module reads must appear here so a missing config degrades to a working
# default rather than a KeyError.
DEFAULT_CONFIG: Dict[str, Any] = {
    "headless": True,
    "window_size": [1920, 1080],
    "page_load_timeout": 30,
    "implicit_wait": 0,
    "interactive_selector": (
        'a[href], button, input:not([type="hidden"]), select, textarea, summary, '
        '[role="button"], [role="link"], [role="checkbox"], [role="radio"], '
        '[role="tab"], [role="menuitem"], [role="switch"], [role="option"], '
        '[role="combobox"], [role="textbox"], [role="searchbox"], '
        '[onclick], [contenteditable="true"], [tabindex]:not([tabindex="-1"])'
    ),
    "include_attributes": [
        "id", "name", "type", "role", "href", "value", "placeholder",
        "title", "alt", "aria-label", "aria-expanded", "aria-checked",
        "aria-selected", "data-testid", "disabled", "checked", "required",
    ],
    "max_elements": 200,
    "max_text_length": 120,
    "skip_disabled": False,
    "screenshot_dir": "playwright/screenshots",
    "allowed_domains": ["localhost", "127.0.0.1", "::1"],
    "allowed_schemes": ["http", "https", "file", "about", "data"],
}


# ── Errors ────────────────────────────────────────────────────────────────────

class AgentBrowserError(RuntimeError):
    """Base class for agent-browser failures."""


class BrowserScopeError(AgentBrowserError):
    """Raised when a URL falls outside the configured navigation scope."""


class ElementIndexError(AgentBrowserError):
    """Raised when an index does not resolve to a live element.

    Either no ``read_state()`` has run since the last navigation, the index is
    out of range, or the node has since left the DOM. In all three cases the
    fix is the same: call ``read_state()`` again and re-read the indexes.
    """


# ── Config ────────────────────────────────────────────────────────────────────

def load_config(path: Optional[Path] = None) -> Dict[str, Any]:
    """Return the ``agent_browser:`` block merged over :data:`DEFAULT_CONFIG`.

    A missing file, an empty file, or a partial block all degrade to defaults
    for the keys they omit.
    """
    cfg = copy.deepcopy(DEFAULT_CONFIG)
    args_file = Path(path) if path else ARGS_FILE
    if not args_file.exists():
        logger.warning("agent_browser: %s not found; using built-in defaults", args_file)
        return cfg
    try:
        raw = yaml.safe_load(args_file.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError as exc:
        logger.warning("agent_browser: %s is not valid YAML (%s); using defaults", args_file, exc)
        return cfg
    block = raw.get("agent_browser") or {}
    if not isinstance(block, dict):
        logger.warning("agent_browser: 'agent_browser' key is not a mapping; using defaults")
        return cfg
    cfg.update({k: v for k, v in block.items() if v is not None})
    return cfg


# ── Page-representation JS ────────────────────────────────────────────────────
#
# One round trip: the script returns [{el: <node>, meta: {...}}, ...]. Selenium
# deserialises DOM nodes into WebElements anywhere in the returned structure,
# so we get descriptors and live handles from a single execute_script call.
_EXTRACT_JS = r"""
const cfg = arguments[0];
const allow = new Set(cfg.include_attributes || []);
const maxEl = cfg.max_elements;
const maxText = cfg.max_text_length;

function squash(s) {
  return (s || '').replace(/\s+/g, ' ').trim();
}

function visible(el) {
  if (el.hasAttribute('hidden')) return false;
  if (el.getAttribute('aria-hidden') === 'true') return false;
  if (el.getClientRects().length === 0) return false;
  const st = window.getComputedStyle(el);
  if (!st) return false;
  if (st.display === 'none' || st.visibility === 'hidden') return false;
  if (parseFloat(st.opacity || '1') === 0) return false;
  return true;
}

const INPUT_ROLES = {
  button: 'button', submit: 'button', reset: 'button', image: 'button',
  file: 'button', checkbox: 'checkbox', radio: 'radio', range: 'slider',
  number: 'spinbutton', search: 'searchbox'
};

function roleOf(el) {
  const explicit = squash(el.getAttribute('role'));
  if (explicit) return explicit.toLowerCase();
  const tag = el.tagName.toLowerCase();
  if (tag === 'a') return el.hasAttribute('href') ? 'link' : 'generic';
  if (tag === 'button' || tag === 'summary') return 'button';
  if (tag === 'select') return el.multiple ? 'listbox' : 'combobox';
  if (tag === 'textarea') return 'textbox';
  if (tag === 'input') {
    const t = squash(el.getAttribute('type')).toLowerCase() || 'text';
    return INPUT_ROLES[t] || 'textbox';
  }
  if (el.isContentEditable) return 'textbox';
  return 'generic';
}

function textOf(el) {
  const tag = el.tagName.toLowerCase();
  if (tag === 'select') {
    const opt = el.selectedOptions && el.selectedOptions[0];
    return squash(opt ? opt.text : '');
  }
  let t = squash(el.innerText || el.textContent);
  if (!t) {
    t = squash(el.getAttribute('aria-label'))
      || squash(el.value)
      || squash(el.getAttribute('placeholder'))
      || squash(el.getAttribute('title'))
      || squash(el.getAttribute('alt'))
      || '';
  }
  if (maxText > 0 && t.length > maxText) t = t.slice(0, maxText) + '…';
  return t;
}

const out = [];
const nodes = document.querySelectorAll(cfg.interactive_selector);
for (const el of nodes) {
  if (out.length >= maxEl) break;
  if (!visible(el)) continue;
  if (cfg.skip_disabled && (el.disabled === true || el.getAttribute('aria-disabled') === 'true')) continue;
  const attrs = {};
  for (const a of el.attributes) {
    if (allow.has(a.name)) attrs[a.name] = a.value;
  }
  out.push({
    el: el,
    meta: {
      index: out.length,
      tag: el.tagName.toLowerCase(),
      role: roleOf(el),
      text: textOf(el),
      attributes: attrs
    }
  });
}
return out;
"""


# ── Agent browser ─────────────────────────────────────────────────────────────

class AgentBrowser:
    """A Selenium session plus an integer index over its interactive elements.

    The index map is rebuilt by every :meth:`read_state` call and invalidated by
    :meth:`navigate`. Element handles survive ordinary actions, so
    ``type(2, ...)`` followed by ``click(5)`` works without an intervening
    re-read; if the page replaced the node, the action raises
    :class:`ElementIndexError` telling the caller to re-read.

    The caller owns the session and must call :meth:`close` (or use the context
    manager) — this mirrors ``get_driver()``'s cleanup contract.
    """

    def __init__(
        self,
        driver: Any = None,
        config: Optional[Dict[str, Any]] = None,
        config_path: Optional[Path] = None,
    ) -> None:
        self.config: Dict[str, Any] = config if config is not None else load_config(config_path)
        self._driver = driver
        self._owns_driver = driver is None
        self._elements: List[Any] = []
        self._meta: List[Dict[str, Any]] = []
        if driver is not None:
            self._apply_timeouts(driver)

    # ── Driver lifecycle ────────────────────────────────────────────────────

    def _apply_timeouts(self, driver: Any) -> None:
        try:
            driver.set_page_load_timeout(float(self.config["page_load_timeout"]))
            driver.implicitly_wait(float(self.config["implicit_wait"]))
        except Exception as exc:  # pragma: no cover - driver-specific
            logger.debug("agent_browser: could not apply timeouts: %s", exc)

    @property
    def driver(self) -> Any:
        """Return the WebDriver, launching one on first use."""
        if self._driver is None:
            from tools.browser.driver_manager import get_driver

            size = self.config["window_size"]
            self._driver = get_driver(
                headless=bool(self.config["headless"]),
                window_size=(int(size[0]), int(size[1])),
            )
            self._apply_timeouts(self._driver)
        return self._driver

    def close(self) -> None:
        """Quit the driver if this instance created it."""
        if self._driver is not None and self._owns_driver:
            try:
                self._driver.quit()
            except Exception as exc:  # pragma: no cover - best effort teardown
                logger.debug("agent_browser: driver.quit() failed: %s", exc)
        self._driver = None
        self._elements = []
        self._meta = []

    def __enter__(self) -> "AgentBrowser":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()

    # ── Scope control ───────────────────────────────────────────────────────

    def _check_scope(self, url: str) -> None:
        """Raise :class:`BrowserScopeError` if *url* is out of configured scope."""
        parsed = urlparse(url)
        scheme = (parsed.scheme or "").lower()
        allowed_schemes = [str(s).lower() for s in self.config["allowed_schemes"]]
        if scheme not in allowed_schemes:
            raise BrowserScopeError(
                f"scheme '{scheme or '(none)'}' is not in agent_browser.allowed_schemes "
                f"{allowed_schemes}"
            )
        if scheme not in ("http", "https"):
            return  # file/about/data carry no host to check
        host = (parsed.hostname or "").lower()
        allowed = [str(d).lower() for d in self.config["allowed_domains"]]
        if host not in allowed:
            raise BrowserScopeError(
                f"host '{host}' is not in agent_browser.allowed_domains {allowed}. "
                "Widen the allowlist in args/agent_browser.yaml to permit it."
            )

    # ── Page representation ─────────────────────────────────────────────────

    def read_state(self, include_screenshot: bool = False, name: str = "state") -> Dict[str, Any]:
        """Return the indexed representation of the current page.

        Parameters
        ----------
        include_screenshot:
            Also capture a PNG and return its path under ``screenshot``.
        name:
            Screenshot basename (only used when *include_screenshot* is true).

        Returns
        -------
        dict
            ``url``, ``title``, ``element_count``, ``elements`` (each with
            ``index``, ``tag``, ``role``, ``text``, ``attributes``), and
            optionally ``screenshot``.
        """
        driver = self.driver
        payload = {
            "interactive_selector": " ".join(str(self.config["interactive_selector"]).split()),
            "include_attributes": list(self.config["include_attributes"]),
            "max_elements": int(self.config["max_elements"]),
            "max_text_length": int(self.config["max_text_length"]),
            "skip_disabled": bool(self.config["skip_disabled"]),
        }
        raw = driver.execute_script(_EXTRACT_JS, payload) or []

        self._elements = [row["el"] for row in raw]
        self._meta = [row["meta"] for row in raw]

        state: Dict[str, Any] = {
            "url": driver.current_url,
            "title": driver.title,
            "element_count": len(self._meta),
            "elements": [dict(m) for m in self._meta],
        }
        if include_screenshot:
            state["screenshot"] = self.screenshot(name)
        return state

    # ── Actions ─────────────────────────────────────────────────────────────

    def navigate(self, url: str, read_state: bool = True) -> Dict[str, Any]:
        """Load *url* and return the resulting page state.

        Raises
        ------
        BrowserScopeError
            If *url* is outside ``agent_browser.allowed_domains`` / ``allowed_schemes``.
        """
        self._check_scope(url)
        self._elements = []
        self._meta = []
        self.driver.get(url)
        logger.info("agent_browser: navigated to %s", url)
        if not read_state:
            return {"url": self.driver.current_url, "title": self.driver.title}
        return self.read_state()

    def _element(self, index: int) -> Any:
        """Return the live WebElement for *index*, or raise ElementIndexError."""
        if not self._elements:
            raise ElementIndexError(
                "no indexed elements — call read_state() before acting on an index"
            )
        if not isinstance(index, int) or index < 0 or index >= len(self._elements):
            raise ElementIndexError(
                f"index {index} out of range (0..{len(self._elements) - 1}); "
                "call read_state() to refresh the index"
            )
        return self._elements[index]

    def _act(self, index: int, action: str, fn) -> Dict[str, Any]:
        """Run *fn* against the element at *index* and return a result dict."""
        from selenium.common.exceptions import (
            StaleElementReferenceException,
            WebDriverException,
        )

        element = self._element(index)
        try:
            fn(element)
        except StaleElementReferenceException as exc:
            raise ElementIndexError(
                f"element [{index}] left the DOM; call read_state() to refresh the index"
            ) from exc
        except WebDriverException as exc:
            raise AgentBrowserError(f"{action}({index}) failed: {exc.msg or exc}") from exc
        return {
            "ok": True,
            "action": action,
            "index": index,
            "element": dict(self._meta[index]),
            "url": self.driver.current_url,
        }

    def click(self, index: int) -> Dict[str, Any]:
        """Click the element at *index*."""
        return self._act(index, "click", lambda el: el.click())

    def type(self, index: int, text: str, clear: bool = True) -> Dict[str, Any]:
        """Type *text* into the element at *index* (clearing it first by default)."""

        def _do(el: Any) -> None:
            if clear:
                el.clear()
            el.send_keys(text)

        return self._act(index, "type", _do)

    def select(self, index: int, value: str) -> Dict[str, Any]:
        """Choose *value* in the ``<select>`` at *index*.

        Matches by option value first, then by visible text.
        """
        from selenium.webdriver.support.ui import Select

        def _do(el: Any) -> None:
            sel = Select(el)
            try:
                sel.select_by_value(value)
            except Exception:
                sel.select_by_visible_text(value)

        return self._act(index, "select", _do)

    def press(self, key: str, index: Optional[int] = None) -> Dict[str, Any]:
        """Send a named key (``ENTER``, ``TAB``, ``ESCAPE``, …).

        Sent to the element at *index* when given, otherwise to the active
        element (i.e. wherever focus currently is).
        """
        from selenium.webdriver.common.keys import Keys

        key_name = str(key).strip().upper()
        key_value = getattr(Keys, key_name, None)
        if key_value is None:
            raise AgentBrowserError(
                f"unknown key '{key}' — expected a selenium Keys name such as ENTER, TAB, ESCAPE"
            )
        if index is not None:
            return self._act(index, "press", lambda el: el.send_keys(key_value))
        self.driver.switch_to.active_element.send_keys(key_value)
        return {
            "ok": True,
            "action": "press",
            "key": key_name,
            "url": self.driver.current_url,
        }

    def screenshot(self, name: str = "state") -> str:
        """Save a PNG under the configured screenshot dir and return its path."""
        safe = "".join(c if c.isalnum() or c in "-_." else "_" for c in name) or "state"
        if not safe.endswith(".png"):
            safe += ".png"
        out_dir = Path(self.config["screenshot_dir"])
        if not out_dir.is_absolute():
            out_dir = BASE_DIR / out_dir
        out_dir.mkdir(parents=True, exist_ok=True)
        path = out_dir / safe
        self.driver.save_screenshot(str(path))
        logger.info("agent_browser: screenshot saved to %s", path)
        return str(path)


# ── CLI ───────────────────────────────────────────────────────────────────────

def main(argv: Optional[List[str]] = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(
        description="ICDEV™ agent browser — indexed page representation"
    )
    parser.add_argument("--url", required=True, help="URL to open")
    parser.add_argument("--screenshot", metavar="NAME", help="Also save a screenshot")
    parser.add_argument("--headed", action="store_true", help="Run with a visible window")
    parser.add_argument("--json", action="store_true", help="Emit JSON (always on; accepted for symmetry)")
    args = parser.parse_args(argv)

    config = load_config()
    if args.headed:
        config["headless"] = False

    browser = AgentBrowser(config=config)
    try:
        state = browser.navigate(args.url)
        if args.screenshot:
            state["screenshot"] = browser.screenshot(args.screenshot)
    except BrowserScopeError as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, indent=2))
        return 2
    finally:
        browser.close()

    print(json.dumps(state, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
