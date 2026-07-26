# CUI // SP-CTI
"""tests/browser/test_agent_browser.py — oss-browse-01-d1 acceptance.

Single acceptance criterion: ``read_state()`` returns the correct indexed
element list on a simple HTML test page.

The browser-backed tests skip when selenium or a driver binary is unavailable
(air-gapped hosts without a vendored driver); the config tests always run.
"""

from __future__ import annotations

import textwrap
from pathlib import Path

import pytest

from tools.browser.agent_browser import (
    DEFAULT_CONFIG,
    AgentBrowser,
    BrowserScopeError,
    ElementIndexError,
    load_config,
)

# ── Fixture page ──────────────────────────────────────────────────────────────

PAGE_HTML = textwrap.dedent(
    """\
    <!doctype html>
    <html><head><title>Agent Browser Fixture</title></head>
    <body>
      <h1>Heading is not interactive</h1>
      <p>Body copy is not interactive either.</p>
      <a href="/kanban" id="board-link">Task Board</a>
      <button id="promote-btn" data-testid="promote">Promote</button>
      <input id="query" name="q" type="text" placeholder="Search tasks">
      <select id="status-select" name="status">
        <option value="backlog">Backlog</option>
        <option value="done" selected>Done</option>
      </select>
      <textarea id="notes" placeholder="Notes"></textarea>
      <input type="hidden" id="csrf" name="csrf" value="secret">
      <button id="hidden-btn" style="display:none">Never Indexed</button>
      <div id="fake-btn" role="button">Custom Role Button</div>
      <a id="anchor-no-href">Not a link</a>
    </body></html>
    """
)

# Elements the page representation must contain, in document order.
EXPECTED = [
    ("a", "link", "Task Board"),
    ("button", "button", "Promote"),
    ("input", "textbox", "Search tasks"),
    ("select", "combobox", "Done"),
    ("textarea", "textbox", "Notes"),
    ("div", "button", "Custom Role Button"),
]


@pytest.fixture(scope="module")
def page_url(tmp_path_factory: pytest.TempPathFactory) -> str:
    path: Path = tmp_path_factory.mktemp("agent_browser") / "fixture.html"
    path.write_text(PAGE_HTML, encoding="utf-8")
    return path.as_uri()


@pytest.fixture(scope="module")
def browser():
    pytest.importorskip("selenium", reason="selenium not installed")
    ab = AgentBrowser()
    try:
        ab.driver  # noqa: B018 - force driver launch so we can skip on failure
    except Exception as exc:  # pragma: no cover - environment-dependent
        ab.close()
        pytest.skip(f"no usable WebDriver in this environment: {exc}")
    try:
        yield ab
    finally:
        ab.close()


# ── Acceptance ────────────────────────────────────────────────────────────────

def test_read_state_returns_indexed_element_list(browser, page_url):
    """read_state() indexes exactly the visible interactive elements, in order."""
    state = browser.navigate(page_url)

    assert state["title"] == "Agent Browser Fixture"
    assert state["url"].startswith("file:")
    assert state["element_count"] == len(state["elements"]) == len(EXPECTED)

    for position, (element, (tag, role, text)) in enumerate(zip(state["elements"], EXPECTED)):
        assert element["index"] == position, "indexes must be dense and document-ordered"
        assert element["tag"] == tag
        assert element["role"] == role
        assert element["text"] == text


def test_read_state_excludes_hidden_and_non_interactive(browser, page_url):
    """Hidden inputs, display:none nodes, and plain copy never get an index."""
    state = browser.navigate(page_url)
    ids = {el["attributes"].get("id") for el in state["elements"]}

    assert "csrf" not in ids, "hidden inputs must not be indexed"
    assert "hidden-btn" not in ids, "display:none elements must not be indexed"
    assert "anchor-no-href" not in ids, "an <a> without href is not interactive"
    assert "board-link" in ids


def test_read_state_attributes_are_allowlisted(browser, page_url):
    """Only attributes named in include_attributes reach the descriptor."""
    state = browser.navigate(page_url)
    allowed = set(browser.config["include_attributes"])

    promote = next(el for el in state["elements"] if el["attributes"].get("id") == "promote-btn")
    assert promote["attributes"]["data-testid"] == "promote"
    assert "style" not in promote["attributes"]
    for element in state["elements"]:
        assert set(element["attributes"]).issubset(allowed)


def test_read_state_honours_narrowed_attribute_allowlist(page_url):
    """Narrowing include_attributes narrows the descriptor — config is load-bearing."""
    pytest.importorskip("selenium", reason="selenium not installed")
    config = dict(DEFAULT_CONFIG, include_attributes=["id"])
    ab = AgentBrowser(config=config)
    try:
        try:
            ab.driver  # noqa: B018
        except Exception as exc:  # pragma: no cover - environment-dependent
            pytest.skip(f"no usable WebDriver in this environment: {exc}")
        state = ab.navigate(page_url)
        for element in state["elements"]:
            assert set(element["attributes"]).issubset({"id"})
    finally:
        ab.close()


def test_actions_resolve_by_index(browser, page_url):
    """type()/select() act on the element the index points at."""
    browser.navigate(page_url)
    state = browser.read_state()

    query_index = next(
        el["index"] for el in state["elements"] if el["attributes"].get("id") == "query"
    )
    result = browser.type(query_index, "promote")
    assert result["ok"] and result["action"] == "type"
    assert browser._element(query_index).get_attribute("value") == "promote"

    select_index = next(
        el["index"] for el in state["elements"] if el["attributes"].get("id") == "status-select"
    )
    browser.select(select_index, "backlog")
    assert browser.read_state()["elements"][select_index]["text"] == "Backlog"


def test_out_of_range_index_raises(browser, page_url):
    browser.navigate(page_url)
    with pytest.raises(ElementIndexError):
        browser.click(999)


def test_screenshot_is_written(browser, page_url, tmp_path):
    browser.navigate(page_url)
    config_backup = browser.config["screenshot_dir"]
    browser.config["screenshot_dir"] = str(tmp_path)
    try:
        path = Path(browser.screenshot("agent_browser_fixture"))
        assert path.exists() and path.stat().st_size > 0
        assert path.name == "agent_browser_fixture.png"
    finally:
        browser.config["screenshot_dir"] = config_backup


# ── Config / scope (no browser required) ──────────────────────────────────────

def test_load_config_reads_args_file():
    cfg = load_config()
    assert cfg["allowed_domains"], "allowed_domains must never be empty"
    assert "id" in cfg["include_attributes"]
    assert cfg["max_elements"] > 0


def test_load_config_falls_back_when_file_missing(tmp_path):
    cfg = load_config(tmp_path / "nope.yaml")
    assert cfg == DEFAULT_CONFIG


def test_load_config_merges_partial_block(tmp_path):
    path = tmp_path / "agent_browser.yaml"
    path.write_text("agent_browser:\n  max_elements: 7\n", encoding="utf-8")
    cfg = load_config(path)
    assert cfg["max_elements"] == 7
    assert cfg["include_attributes"] == DEFAULT_CONFIG["include_attributes"]


def test_navigate_refuses_out_of_scope_host():
    ab = AgentBrowser(driver=object(), config=dict(DEFAULT_CONFIG))
    with pytest.raises(BrowserScopeError):
        ab.navigate("https://example.com/")


def test_navigate_refuses_unlisted_scheme():
    ab = AgentBrowser(driver=object(), config=dict(DEFAULT_CONFIG, allowed_schemes=["http"]))
    with pytest.raises(BrowserScopeError):
        ab.navigate("file:///etc/passwd")


def test_navigate_allows_localhost_by_default():
    """Scope check passes for localhost — proven without touching the network."""
    ab = AgentBrowser(driver=object(), config=dict(DEFAULT_CONFIG))
    ab._check_scope("http://localhost:5050/kanban")
    ab._check_scope("http://127.0.0.1:5050/")
