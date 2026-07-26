# CUI // SP-CTI
"""Tests for tools/browser/agent_browser.py — indexed-element page representation.

Two layers:

1. **Fake-driver tests** (always run) — cover the Python half: config merge, the
   URL gate, index staleness, key mapping, action results, the model-facing
   rendering, and the tool-schema registry. A ``FakeDriver`` stands in for
   Selenium so these need no browser.
2. **Real-browser test** (skipped when no driver resolves) — drives a real
   ``data:``-free local HTML fixture through ``get_driver()`` and asserts the
   indexed representation matches the page. This is the one that proves the JS
   extraction actually works; the fake-driver tests cannot.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from tools.browser.agent_browser import (
    INDEX_ATTR,
    ActionResult,
    AgentBrowser,
    AgentBrowserError,
    IndexedElement,
    NavigationBlockedError,
    PageState,
    StaleIndexError,
    load_config,
)
from tools.browser.agent_tools import DEFAULT_TOOLS, BrowserToolRegistry
from tools.browser.scope import (
    ActionBudgetExceeded,
    BrowserScopeConfig,
    NavigationDenied,
    ScopeViolation,
)


# ── Fakes ─────────────────────────────────────────────────────────────────────


class FakeElement:
    """Minimal WebElement stand-in that records what was done to it."""

    def __init__(self, tag: str = "button") -> None:
        self.tag_name = tag
        self.clicked = 0
        self.cleared = 0
        self.keys: list = []

    def click(self) -> None:
        self.clicked += 1

    def clear(self) -> None:
        self.cleared += 1

    def send_keys(self, value) -> None:
        self.keys.append(value)


class FakeSwitchTo:
    def __init__(self, element) -> None:
        self.active_element = element


class FakeDriver:
    """Drives the Python half without a browser.

    ``execute_script`` distinguishes the two scripts the module runs by looking
    for their marker text, so the tests stay honest about which one is called.
    """

    def __init__(self, extract_result=None, elements=None) -> None:
        self.current_url = "http://localhost:5050/"
        self.extract_result = extract_result or {
            "url": "http://localhost:5050/",
            "title": "ICDEV",
            "truncated": False,
            "elements": [],
        }
        self.elements = elements or {}
        self.visited: list = []
        self.screenshots: list = []
        self.quit_count = 0
        self.switch_to = FakeSwitchTo(FakeElement("body"))
        self.scrolled = 0

    def execute_script(self, script: str, *args):
        if "clearMarkers" in script:
            return self.extract_result
        if "scrollIntoView" in script:
            self.scrolled += 1
            return None
        if "function find(root)" in script:
            return self.elements.get(int(args[0]))
        if "arguments[0].click()" in script:
            args[0].clicked += 1
            return None
        return None

    def get(self, url: str) -> None:
        self.visited.append(url)
        self.current_url = url

    def save_screenshot(self, path: str) -> bool:
        self.screenshots.append(path)
        Path(path).write_bytes(b"\x89PNG\r\n\x1a\n")
        return True

    def set_page_load_timeout(self, value) -> None:
        pass

    def set_script_timeout(self, value) -> None:
        pass

    def quit(self) -> None:
        self.quit_count += 1


def _scope(**overrides) -> BrowserScopeConfig:
    """Scope policy for tests: loopback-only (the shipped default), audit off.

    Audit is disabled so the suite does not write ``audit_trail`` rows; the
    audit path itself is covered by ``tests/browser/test_scope.py``.
    """
    kwargs = {"audit_enabled": False, "require_broker_authorization": True}
    kwargs.update(overrides)
    return BrowserScopeConfig(**kwargs)


def _browser(driver=None, scope=None, **cfg_overrides) -> AgentBrowser:
    """AgentBrowser wired to a FakeDriver with settle disabled (tests must be fast)."""
    config = load_config()
    config["navigation"] = dict(config["navigation"])
    config["navigation"]["settle_ms"] = 0
    config.update(cfg_overrides)
    return AgentBrowser(
        driver=driver or FakeDriver(),
        config=config,
        scope_config=scope or _scope(),
    )


# ── Config ────────────────────────────────────────────────────────────────────


def test_config_loads_from_args_yaml():
    cfg = load_config()
    assert "id" in cfg["include_attributes"]
    assert cfg["max_elements"] > 0
    assert cfg["screenshot"]["dir"] == "playwright/screenshots"


def test_config_carries_no_navigation_policy():
    """Scheme/domain policy belongs to args/browser_scope.yaml, not here.

    A second copy of a security policy is the failure mode this asserts against:
    the enforcing copy wins and the shadow copy rots without anyone noticing.
    """
    cfg = load_config()
    for banned in ("allowed_domains", "blocked_domains", "allowed_schemes"):
        assert banned not in cfg["navigation"], (
            f"{banned} reappeared in args/agent_browser.yaml — it is owned by "
            "args/browser_scope.yaml and enforced by tools/browser/scope.py"
        )


def test_config_falls_back_when_file_missing(tmp_path):
    cfg = load_config(tmp_path / "nope.yaml")
    assert cfg["include_attributes"]
    assert cfg["navigation"]["settle_ms"] == 350


def test_config_merges_nested_blocks(tmp_path):
    args_file = tmp_path / "agent_browser.yaml"
    args_file.write_text(
        "agent_browser:\n"
        "  max_elements: 7\n"
        "  navigation:\n"
        "    settle_ms: 0\n",
        encoding="utf-8",
    )
    cfg = load_config(args_file)
    assert cfg["max_elements"] == 7
    assert cfg["navigation"]["settle_ms"] == 0
    # untouched nested keys survive the merge
    assert cfg["screenshot"]["dir"] == "playwright/screenshots"


def test_malformed_config_does_not_raise(tmp_path):
    args_file = tmp_path / "agent_browser.yaml"
    args_file.write_text("agent_browser: [this: is: not: a: mapping\n", encoding="utf-8")
    cfg = load_config(args_file)
    assert cfg["max_elements"] == 200


# ── Scope enforcement (delegated to tools/browser/scope.py) ───────────────────
#
# These do not re-test scope.py's decision table — tests/browser/test_scope.py
# owns that. What they assert is that AgentBrowser has no path around it.


def test_navigate_allows_loopback():
    driver = FakeDriver()
    browser = _browser(driver)
    browser.navigate("http://localhost:5050/kanban")
    assert driver.visited == ["http://localhost:5050/kanban"]


@pytest.mark.parametrize(
    "url",
    [
        "javascript:alert(1)",
        "data:text/html,<h1>x</h1>",
        "file:///C:/Windows/win.ini",
        "ftp://example.com/x",
    ],
)
def test_navigate_refuses_dangerous_schemes_without_touching_driver(url):
    driver = FakeDriver()
    browser = _browser(driver)
    with pytest.raises(NavigationDenied):
        browser.navigate(url)
    assert driver.visited == []


def test_navigate_is_default_deny_for_routable_hosts():
    """The shipped default is loopback-only, so a public host is refused.

    The superseded in-module gate treated an empty allowlist as "allow
    everything" and would have let this through.
    """
    driver = FakeDriver()
    browser = _browser(driver)
    with pytest.raises(NavigationDenied):
        browser.navigate("https://example.gov/a/b?c=d")
    assert driver.visited == []


def test_navigation_blocked_error_is_the_scope_exception():
    """The old module-local exception name still resolves, to one type."""
    assert NavigationBlockedError is NavigationDenied


def test_denied_domain_beats_allowed_domain():
    driver = FakeDriver()
    browser = _browser(
        driver,
        scope=_scope(
            allowed_domains=("evil.test",),
            denied_domains=("evil.test",),
            allow_non_local=True,
        ),
    )
    with pytest.raises(NavigationDenied):
        browser.navigate("https://sub.evil.test/x")
    assert driver.visited == []


def test_action_budget_caps_the_run():
    driver = FakeDriver(elements={0: FakeElement()})
    browser = _browser(driver, scope=_scope(max_actions_per_run=2))
    browser.navigate("http://localhost:5050/")   # action 1
    browser.click(0)                             # action 2
    with pytest.raises(ActionBudgetExceeded):
        browser.click(0)                         # refused
    assert browser.budget.remaining_actions == 0


def test_public_driver_property_refuses_bypass_attributes():
    """``browser.driver`` is the guard, not the raw session."""
    browser = _browser(FakeDriver())
    for attr in ("get", "execute_script", "set_page_load_timeout"):
        with pytest.raises(ScopeViolation):
            getattr(browser.driver, attr)


def test_observation_is_not_charged_to_the_budget():
    """read_state must not spend the budget the model needs for actions."""
    browser = _browser(FakeDriver(), scope=_scope(max_actions_per_run=2))
    for _ in range(5):
        browser.read_state()
    assert browser.budget.actions_used == 0


# ── read_state ────────────────────────────────────────────────────────────────


def _extract(elements, truncated=False):
    return {
        "url": "http://localhost:5050/kanban",
        "title": "Kanban — ICDEV",
        "truncated": truncated,
        "elements": elements,
    }


def test_read_state_indexes_elements():
    driver = FakeDriver(
        extract_result=_extract(
            [
                {
                    "index": 0, "tag": "a", "role": "link", "text": "Home",
                    "attributes": {"href": "/"}, "bounds": {"x": 0, "y": 0, "width": 40, "height": 20},
                    "in_viewport": True, "disabled": False,
                },
                {
                    "index": 1, "tag": "button", "role": "button", "text": "Save",
                    "attributes": {"id": "save"}, "bounds": {"x": 0, "y": 30, "width": 60, "height": 24},
                    "in_viewport": True, "disabled": True,
                },
            ]
        )
    )
    state = _browser(driver).read_state()
    assert state.url.endswith("/kanban")
    assert state.title == "Kanban — ICDEV"
    assert [e.index for e in state.elements] == [0, 1]
    assert state.elements[0].role == "link"
    assert state.elements[1].disabled is True
    assert state.generation == 1


def test_read_state_generation_increments():
    browser = _browser()
    assert browser.state is None
    assert browser.read_state().generation == 1
    assert browser.read_state().generation == 2
    assert browser.state.generation == 2


def test_read_state_passes_config_to_the_extractor():
    """The include_attributes allowlist must actually reach the page script."""
    captured = {}
    driver = FakeDriver()
    original = driver.execute_script

    def spy(script, *args):
        if "clearMarkers" in script:
            captured.update(args[0])
        return original(script, *args)

    driver.execute_script = spy
    _browser(driver).read_state()
    assert captured["include_attributes"] == load_config()["include_attributes"]
    assert captured["index_attr"] == INDEX_ATTR
    assert captured["max_elements"] == load_config()["max_elements"]


def test_read_state_reports_truncation():
    driver = FakeDriver(extract_result=_extract([], truncated=True))
    state = _browser(driver).read_state()
    assert state.truncated is True
    assert "truncated" in state.to_text()


def test_read_state_survives_empty_script_result():
    driver = FakeDriver(extract_result=None)
    driver.extract_result = {}
    state = _browser(driver).read_state()
    assert state.elements == []
    assert state.url == "http://localhost:5050/"


# ── Rendering ─────────────────────────────────────────────────────────────────


def test_element_line_is_index_first():
    el = IndexedElement(
        index=14, tag="button", role="button", text="Submit",
        attributes={"id": "save", "type": "submit"},
    )
    line = el.to_line()
    assert line.startswith("[14] <button>")
    assert "Submit" in line
    assert "id=save" in line and "type=submit" in line


def test_element_line_flags_disabled_and_offscreen():
    el = IndexedElement(index=2, tag="button", role="button", text="Go", disabled=True, in_viewport=False)
    line = el.to_line()
    assert "[disabled]" in line
    assert "[off-screen]" in line


def test_state_to_text_has_url_title_and_lines():
    state = PageState(
        url="http://localhost:5050/",
        title="ICDEV",
        elements=[IndexedElement(index=0, tag="button", role="button", text="Go")],
    )
    text = state.to_text()
    assert "URL: http://localhost:5050/" in text
    assert "Title: ICDEV" in text
    assert "[0] <button> Go" in text


def test_state_to_text_says_so_when_empty():
    assert "none found" in PageState(url="u", title="t").to_text()


def test_state_to_dict_is_json_serialisable():
    state = PageState(
        url="u", title="t",
        elements=[IndexedElement(index=0, tag="a", role="link", text="x")],
    )
    payload = json.dumps(state.to_dict())
    assert '"element_count": 1' in payload


def test_state_find_returns_element_or_none():
    state = PageState(url="u", title="t", elements=[IndexedElement(index=5, tag="a", role="link")])
    assert state.find(5).tag == "a"
    assert state.find(6) is None


# ── Actions ───────────────────────────────────────────────────────────────────


def test_click_uses_native_click():
    el = FakeElement("button")
    driver = FakeDriver(elements={3: el})
    result = _browser(driver).click(3)
    assert el.clicked == 1
    assert result.ok and result.action == "click" and result.index == 3
    assert result.url_changed is False


def test_click_falls_back_to_script_when_intercepted():
    from selenium.common.exceptions import ElementClickInterceptedException

    class Intercepted(FakeElement):
        def click(self):
            raise ElementClickInterceptedException("overlay")

    el = Intercepted("button")
    driver = FakeDriver(elements={1: el})
    result = _browser(driver).click(1)
    assert el.clicked == 1  # incremented by the scripted click path
    assert "script click" in result.detail


def test_click_reports_url_change():
    el = FakeElement("a")
    driver = FakeDriver(elements={0: el})

    def navigating_click():
        driver.current_url = "http://localhost:5050/next"

    el.click = navigating_click
    result = _browser(driver).click(0)
    assert result.url_changed is True
    assert "read_state" in result.to_text()


def test_type_text_clears_then_types():
    el = FakeElement("input")
    driver = FakeDriver(elements={2: el})
    result = _browser(driver).type_text(2, "kanban")
    assert el.cleared == 1
    assert el.keys == ["kanban"]
    assert "6 chars" in result.detail


def test_type_text_can_skip_clear_and_send_enter():
    el = FakeElement("input")
    driver = FakeDriver(elements={2: el})
    _browser(driver).type_text(2, "x", clear=False, enter=True)
    assert el.cleared == 0
    assert len(el.keys) == 2  # text, then Enter


def test_type_text_survives_unclearable_control():
    class NoClear(FakeElement):
        def clear(self):
            raise Exception("cannot clear")

    el = NoClear("input")
    driver = FakeDriver(elements={0: el})
    _browser(driver).type_text(0, "abc")
    assert el.keys == ["abc"]


def test_select_rejects_non_select_element():
    el = FakeElement("div")
    driver = FakeDriver(elements={4: el})
    with pytest.raises(AgentBrowserError, match="not a <select>"):
        _browser(driver).select(4, "x")


def test_press_named_key_goes_to_active_element():
    from selenium.webdriver.common.keys import Keys

    driver = FakeDriver()
    result = _browser(driver).press("Enter")
    assert driver.switch_to.active_element.keys == [Keys.ENTER]
    assert result.action == "press"


def test_press_accepts_aliases_and_single_chars():
    from selenium.webdriver.common.keys import Keys

    driver = FakeDriver()
    browser = _browser(driver)
    browser.press("ArrowDown")
    browser.press("esc")
    browser.press("a")
    assert driver.switch_to.active_element.keys == [Keys.ARROW_DOWN, Keys.ESCAPE, "a"]


def test_press_targets_an_index_when_given():
    from selenium.webdriver.common.keys import Keys

    el = FakeElement("input")
    driver = FakeDriver(elements={9: el})
    _browser(driver).press("Tab", index=9)
    assert el.keys == [Keys.TAB]
    assert driver.switch_to.active_element.keys == []


def test_press_rejects_unknown_key():
    with pytest.raises(AgentBrowserError, match="unknown key"):
        _browser().press("Frobnicate")


# ── Staleness ─────────────────────────────────────────────────────────────────


def test_stale_index_tells_the_model_to_re_read():
    driver = FakeDriver(elements={})  # locate returns None
    browser = _browser(driver)
    browser.read_state()
    with pytest.raises(StaleIndexError) as exc:
        browser.click(11)
    message = str(exc.value)
    assert "11" in message
    assert "read_state" in message


def test_non_integer_index_is_rejected():
    with pytest.raises(AgentBrowserError, match="must be an integer"):
        _browser().click("save-button")


def test_missing_index_is_rejected():
    with pytest.raises(AgentBrowserError):
        _browser().click(None)


# ── Screenshots ───────────────────────────────────────────────────────────────


def test_screenshot_lands_under_playwright_screenshots(tmp_path):
    driver = FakeDriver()
    browser = _browser(driver)
    browser.config["screenshot"] = {"dir": str(tmp_path), "default_name": "state"}
    path = browser.screenshot()
    assert path.endswith("state.png")
    assert Path(path).exists()


def test_screenshot_name_is_sanitised(tmp_path):
    driver = FakeDriver()
    browser = _browser(driver)
    browser.config["screenshot"] = {"dir": str(tmp_path), "default_name": "state"}
    path = browser.screenshot(name="../../etc/passwd.png")
    assert Path(path).parent == tmp_path
    assert ".." not in Path(path).name


def test_read_state_can_attach_a_screenshot(tmp_path):
    driver = FakeDriver()
    browser = _browser(driver)
    browser.config["screenshot"] = {"dir": str(tmp_path), "default_name": "state"}
    state = browser.read_state(screenshot=True)
    assert state.screenshot_path and Path(state.screenshot_path).exists()
    assert "Screenshot:" in state.to_text()


# ── Lifecycle ─────────────────────────────────────────────────────────────────


def test_injected_driver_is_not_quit():
    driver = FakeDriver()
    with _browser(driver):
        pass
    assert driver.quit_count == 0


def test_context_manager_returns_self():
    browser = _browser()
    with browser as b:
        assert b is browser


# ── ActionResult ──────────────────────────────────────────────────────────────


def test_action_result_to_dict_includes_url_changed():
    result = ActionResult(action="click", index=1, url_before="a", url_after="b")
    assert result.to_dict()["url_changed"] is True


def test_action_result_reports_failure():
    text = ActionResult(action="click", index=1, ok=False, detail="boom").to_text()
    assert "failed" in text and "boom" in text


# ── Tool registry ─────────────────────────────────────────────────────────────


def test_registry_builds_default_tools():
    tools, handlers = BrowserToolRegistry(_browser()).build()
    names = [t["function"]["name"] for t in tools]
    assert names == DEFAULT_TOOLS
    assert set(handlers) == set(DEFAULT_TOOLS)


def test_registry_skips_unknown_tools():
    tools, handlers = BrowserToolRegistry(_browser()).build(["browser_click", "browser_teleport"])
    assert list(handlers) == ["browser_click"]
    assert len(tools) == 1


def test_registry_schemas_are_valid_function_calling():
    tools, _ = BrowserToolRegistry(_browser()).build()
    for tool in tools:
        assert tool["type"] == "function"
        fn = tool["function"]
        assert fn["name"] and fn["description"]
        assert fn["parameters"]["type"] == "object"
        for required in fn["parameters"].get("required", []):
            assert required in fn["parameters"]["properties"]


def test_read_state_tool_is_marked_read_only():
    tools, _ = BrowserToolRegistry(_browser()).build(["browser_read_state", "browser_click"])
    by_name = {t["function"]["name"]: t for t in tools}
    assert by_name["browser_read_state"]["is_read_only"] is True
    assert "is_read_only" not in by_name["browser_click"]


def test_handlers_return_model_facing_strings():
    el = FakeElement("button")
    driver = FakeDriver(elements={0: el}, extract_result=_extract([]))
    browser = _browser(driver)
    _, handlers = BrowserToolRegistry(browser).build()

    state_text = handlers["browser_read_state"]({}, None)
    assert isinstance(state_text, str) and "URL:" in state_text

    click_text = handlers["browser_click"]({"index": 0}, None)
    assert isinstance(click_text, str) and "click(0)" in click_text
    assert el.clicked == 1


def test_navigate_handler_requires_a_url():
    _, handlers = BrowserToolRegistry(_browser()).build()
    assert "requires" in handlers["browser_navigate"]({}, None)


def test_type_handler_forwards_flags():
    el = FakeElement("input")
    driver = FakeDriver(elements={1: el})
    _, handlers = BrowserToolRegistry(_browser(driver)).build()
    handlers["browser_type"]({"index": 1, "text": "abc", "clear": False, "enter": True}, None)
    assert el.cleared == 0 and len(el.keys) == 2


# ── Real browser ──────────────────────────────────────────────────────────────

FIXTURE_HTML = """<!doctype html>
<html><head><meta charset="utf-8"><title>Agent Browser Fixture</title></head>
<body>
  <a href="/home" id="home-link">Home</a>
  <button id="save" type="submit" aria-label="Save">Save</button>
  <button id="ghost" style="display:none">Hidden</button>
  <button id="off" disabled>Disabled</button>
  <input id="q" name="query" type="text" placeholder="Search…">
  <select id="mode"><option value="a">Alpha</option><option value="b">Beta</option></select>
  <textarea id="notes"></textarea>
  <div role="button" tabindex="0" id="custom">Custom Action</div>
  <span>not interactive</span>
  <script>
    document.getElementById('save').addEventListener('click', function () {
      document.title = 'Saved';
    });
  </script>
</body></html>
"""


def _driver_available() -> bool:
    try:
        from tools.browser.driver_manager import DriverManager

        DriverManager.instance()
        import selenium  # noqa: F401
    except Exception:
        return False
    return True


@pytest.fixture
def fixture_server(tmp_path):
    """Serve the fixture over loopback HTTP and yield its base URL.

    Deliberately not ``file://``: the shipped policy allows http/https only, and
    a test that widens the scheme allowlist to pass would be testing a
    configuration nobody runs. Loopback is exactly what the default permits.
    """
    import threading
    from functools import partial
    from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer

    (tmp_path / "fixture.html").write_text(FIXTURE_HTML, encoding="utf-8")
    handler = partial(SimpleHTTPRequestHandler, directory=str(tmp_path))
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_address[1]}"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


@pytest.mark.skipif(not _driver_available(), reason="no selenium/webdriver available")
def test_real_browser_indexes_a_live_page(tmp_path, fixture_server):
    """The one test that exercises the JS extraction against a real DOM."""
    config = load_config()
    config["navigation"] = dict(config["navigation"])
    config["navigation"]["settle_ms"] = 100
    config["screenshot"] = {"dir": str(tmp_path), "default_name": "fixture"}

    browser = AgentBrowser(
        config=config, headless=True, scope_config=_scope(max_actions_per_run=100)
    )
    try:
        try:
            state = browser.navigate(f"{fixture_server}/fixture.html")
        except Exception as exc:  # noqa: BLE001 - no usable browser binary in this env
            pytest.skip(f"browser could not start: {exc}")

        assert state.title == "Agent Browser Fixture"
        by_id = {e.attributes.get("id"): e for e in state.elements}

        # Every real control is indexed…
        for element_id in ("home-link", "save", "q", "mode", "notes", "custom"):
            assert element_id in by_id, f"{element_id} missing from {list(by_id)}"
        # …the display:none one is not…
        assert "ghost" not in by_id
        # …and the disabled one is present but flagged.
        assert by_id["off"].disabled is True

        # Indices are contiguous and DOM-ordered.
        assert [e.index for e in state.elements] == list(range(len(state.elements)))
        assert by_id["home-link"].index < by_id["save"].index

        # Representation carries role + text + allowlisted attributes only.
        assert by_id["home-link"].role == "link"
        assert by_id["home-link"].text == "Home"
        assert by_id["custom"].role == "button"
        # An unlabelled input reads as its placeholder…
        assert by_id["q"].text == "Search…"
        assert by_id["q"].attributes.get("name") == "query"
        assert "style" not in by_id["save"].attributes  # not on the allowlist
        # …and duplicates of the visible text are dropped, not repeated.
        assert "placeholder" not in by_id["q"].attributes
        assert "aria-label" not in by_id["save"].attributes

        # Acting by index actually works.
        browser.click(by_id["save"].index)
        assert browser.guard.current_url and browser.guard.title == "Saved"

        browser.type_text(by_id["q"].index, "kanban")
        assert browser.guard.driver.execute_script(
            "return document.getElementById('q').value;"
        ) == "kanban"

        browser.select(by_id["mode"].index, "b")
        assert browser.guard.driver.execute_script(
            "return document.getElementById('mode').value;"
        ) == "b"

        # Selecting by visible text works too — that is what the model sees.
        browser.select(by_id["mode"].index, "Alpha")
        assert browser.guard.driver.execute_script(
            "return document.getElementById('mode').value;"
        ) == "a"

        # A fresh read re-indexes and the screenshot lands where configured.
        state2 = browser.read_state(screenshot=True, name="fixture")
        assert state2.generation > state.generation
        assert Path(state2.screenshot_path).exists()

        # An index the page never had is refused, not silently mis-clicked.
        with pytest.raises(StaleIndexError):
            browser.click(9999)
    finally:
        browser.close()
