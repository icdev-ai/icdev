# CUI // SP-CTI

# Agent Browser — Indexed-Element Page Representation (oss-browse-01)

**Status:** shipped
**Task:** `oss-browse-01`
**Modules:** `tools/browser/agent_browser.py`, `tools/browser/agent_tools.py`
**Config:** `args/agent_browser.yaml`
**Tests:** `tests/test_agent_browser.py` (52 tests, incl. one real-browser DOM test)

---

## The gap this closes

No ICDEV agent could use a browser. `get_driver()` had eight consumers — the
driver manager and its `__init__`, the air-gap vendoring tool, one scheduling
script, `tools/sharepoint/browser_fallback.py`, and three `tools/testing/e2e_*`
modules. Every one of them is *our* automation driving *our* dashboard with
selectors a human wrote. ACE agent mode declared 12 tools, none browser-shaped;
the canonical agent loop grepped clean for `navigate|click|screenshot|selenium|
playwright|webdriver`.

## What was actually adopted

browser-use's load-bearing idea is **not** its agent loop — ICDEV already has
several (`icdev.tools.llm.agent_loop`, ACE agent mode, the harness). It is the
**page representation**: extract every interactive element and assign it a stable
integer index, so a model acts via `click(14)` instead of inventing a CSS
selector it has no way to verify.

That single idea is what this ships. The loop side is left alone.

```
URL: http://localhost:5050/kanban
Title: Task Board - ICDEV™ Dashboard
Interactive elements (35):
[19] <select> All (Full View) (aria-label=Select dashboard view role, id=role-select)
[20] <input> role=text Filter tasks (id=kanban-filter-input, type=text)
[24] <button> + Add Task
```

## Deliberate non-adoptions

- **No Python Playwright, no chromium download.** Built on the existing
  `tools/browser/driver_manager.py::get_driver` — Selenium with vendored
  `msedgedriver`/`chromedriver` and no runtime downloads. The repo's
  `@playwright/test` setup is npm-based E2E tooling for our own dashboard; it is
  not the agent path and was not extended into one.
- **The assertion half was reused, not rebuilt.** `AgentBrowser.validate()`
  screenshots and delegates to `tools/testing/screenshot_validator.py::
  validate_screenshot` — the same code path behind the MCP tool
  `validate_screenshot` (`tools/mcp/gap_handlers.py`). No vision validation is
  reimplemented here.

## Surface

| Method | Returns | Notes |
|---|---|---|
| `read_state(screenshot=False)` | `PageState` | indexed elements + title/URL + optional screenshot |
| `navigate(url)` | `PageState` | scheme/domain gate first; driver untouched if refused |
| `click(index)` | `ActionResult` | falls back to a scripted click when intercepted |
| `type_text(index, text, clear, enter)` | `ActionResult` | `enter=True` is the search-box pattern |
| `select(index, value)` | `ActionResult` | option value first, then visible text |
| `press(key, index=None)` | `ActionResult` | model-friendly names or any single char |
| `screenshot(name=None)` | `str` | always under `playwright/screenshots/` |
| `validate(assertion)` | `dict` | delegates to the existing screenshot validator |

`PageState.to_text()` and `ActionResult.to_text()` are the model-facing
renderings — every `agent_tools.py` handler returns one of them, never a repr.

## Design decisions worth knowing

**Extraction is one round trip.** Geometry, computed visibility, occlusion, and
attribute filtering are all decided *in the page* by `_EXTRACT_JS`, which returns
plain JSON. Python never walks the DOM node by node — that would be one WebDriver
round trip per element and unusably slow on a real page.

**Indices are per-observation.** They are valid only for the `read_state()` that
produced them. Acting on an index the current DOM no longer carries raises
`StaleIndexError`, whose message tells the model to re-read — it never falls
through to clicking whatever now occupies that position. `ActionResult.to_text()`
also reports when an action changed the URL, which is the cue to re-read.

**Only outermost interactive nodes are indexed, except real form controls.** A
`<button>` inside an `<a>` is its own target; a `<span role="button">` inside an
`<a>` is not. Without this a nav bar produces three indices per link.

**DOM verbosity is one knob.** `include_attributes` in `args/agent_browser.yaml`
is an allowlist — anything not listed never reaches the model. Combined with
`drop_attrs_matching_text` (which kills the ubiquitous
`aria-label="Submit"` on a button reading "Submit") this is the single fastest
lever on prompt size.

## Known limitations

- **Cross-origin iframes are not traversed.** Same-document content and *open*
  shadow roots are indexed; a cross-origin `<iframe>` surfaces as one element
  (its frame box), not as its contents.
- **Indirect prompt injection via page text is a real, accepted residual risk.**
  The allowlist and length caps shrink the channel; they do not close it. See
  Gap 36 in [docs/security/sandbox-coverage.md](../security/sandbox-coverage.md).

## Security

Documented as **Gap 36 — sandboxed** in
[docs/security/sandbox-coverage.md](../security/sandbox-coverage.md): the
isolation boundary is the browser's own renderer sandbox, which is the
purpose-built tool for rendering hostile web content, rather than wrapping the
vendored driver in `SandboxExecutor`. The navigation gate (`check_url`) refuses
`javascript:`, `data:`, and `file:` before the driver is touched, and
`navigation.allowed_domains` turns the gate into an allowlist for SSRF
containment in a deployed agent.

## Verification

```
$ python -m pytest tests/test_agent_browser.py -q
52 passed
```

The suite is fake-driver based except for
`test_real_browser_indexes_a_live_page`, which launches the actual vendored
driver against an HTML fixture and proves the whole chain end to end: the
`display:none` button is absent, the `disabled` button is present and flagged,
indices are contiguous and DOM-ordered, `aria-label`/`placeholder` duplicates of
the visible text are dropped, `click(index)` fires the page's own handler,
`type_text` lands in the input, `select` matches by both value and visible text,
and `click(9999)` raises `StaleIndexError` instead of mis-clicking. It skips
cleanly when no driver is available.

## Wiring it into an agent loop

```python
from tools.browser.agent_browser import AgentBrowser
from tools.browser.agent_tools import BrowserToolRegistry

with AgentBrowser() as browser:
    tools, handlers = BrowserToolRegistry(browser).build()
    run_agent_loop(..., tools=tools, tool_handlers=handlers)
```

Seven tools: `browser_read_state`, `browser_navigate`, `browser_click`,
`browser_type`, `browser_select`, `browser_press`, `browser_screenshot` — same
`(tools, tool_handlers)` convention as `tools/ace/agent_tools.py`.
