# Browser Automation (Selenium Driver Manager)

> Shard of `tools/manifest.md`. See index at `tools/manifest.md`.

## Browser Automation
| Tool | File | Description | Input | Output |
|------|------|-------------|-------|--------|
| Driver Manager | tools/browser/driver_manager.py | Singleton WebDriver factory — resolves vendored msedgedriver (Edge primary, Win11 pre-installed) or chromedriver fallback; no runtime downloads; `get_driver()` returns ready WebDriver | `--probe`, `--json`, `--smoke` | Resolved browser + driver path; Selenium WebDriver instance |
| Agent Browser | tools/browser/agent_browser.py | **Indexed-element page representation** — `read_state()` extracts every interactive element and assigns a stable integer index so a model acts via `click(14)` instead of inventing a CSS selector. Actions: `navigate`, `click`, `type_text`, `select`, `press`, `screenshot`. DOM verbosity governed by the `include_attributes` allowlist in `args/agent_browser.yaml`. Built on `get_driver()` — vendored Selenium, no runtime downloads, no Playwright | `--url`, `--text`, `--json`, `--screenshot`, `--name`, `--no-headless`, `--config` | `PageState` (url/title/indexed elements/truncated) or `ActionResult`; both render to model-facing text via `.to_text()` |
| Browser Agent Tools | tools/browser/agent_tools.py | `BrowserToolRegistry(browser).build()` → `(tools, tool_handlers)` for `icdev.tools.llm.agent_loop.run_agent_loop`. Exposes `browser_read_state`, `browser_navigate`, `browser_click`, `browser_type`, `browser_select`, `browser_press`, `browser_screenshot`. Same convention as `tools/ace/agent_tools.py` | list[str] tool names | OpenAI function-calling schemas + handlers returning model-facing strings |
| Browser Package | tools/browser/__init__.py | Package init — re-exports `get_driver`, `DriverManager`, `AgentBrowser`, `PageState`, `IndexedElement` | (import) | — |

### Driver resolution order
1. Vendored `vendor/drivers/msedgedriver/{major}/msedgedriver[.exe]` matching installed Edge major
2. `msedgedriver` on PATH (system-installed)
3. Vendored `vendor/drivers/chromedriver/{major}/chromedriver[.exe]`
4. `chromedriver` on PATH
5. Selenium Manager (bundled with selenium >= 4.6)

### Quick start

```python
from tools.browser.driver_manager import get_driver

driver = get_driver()          # headless Edge or Chrome
driver.get("http://localhost:5050")
driver.quit()
```

```bash
# Probe driver resolution
python tools/browser/driver_manager.py --probe

# Smoke test (launches browser, visits about:blank, quits)
python tools/browser/driver_manager.py --smoke
```

## Agent Browser — indexed-element page representation

The load-bearing idea is the **page representation**, not the loop. ICDEV already
has several agent loops; what it lacked was a way for an agent to *see* a page.
`read_state()` returns every interactive element with a stable integer index:

```
URL: http://localhost:5050/kanban
Title: Task Board - ICDEV™ Dashboard
Interactive elements (35):
[19] <select> All (Full View) (aria-label=Select dashboard view role, id=role-select)
[20] <input> role=text Filter tasks (id=kanban-filter-input, placeholder=Filter by title, id, priority..., type=text)
[24] <button> + Add Task
```

The model then acts by index — `click(24)` — instead of inventing a selector it
cannot verify.

```python
from tools.browser.agent_browser import AgentBrowser

with AgentBrowser() as b:
    state = b.navigate("http://localhost:5050/kanban")
    print(state.to_text())        # exactly what the model sees
    b.type_text(20, "oss-browse")
    b.click(24)
    state = b.read_state(screenshot=True)
```

```bash
# Index a page and print the model-facing rendering
python tools/browser/agent_browser.py --url http://localhost:5050/kanban --text

# JSON (elements carry index, role, text, allowlisted attributes, bounds)
python tools/browser/agent_browser.py --url http://localhost:5050 --json

# Capture a screenshot alongside the state (lands in playwright/screenshots/)
python tools/browser/agent_browser.py --url http://localhost:5050 --text --screenshot --name home

# Print the resolved config
python tools/browser/agent_browser.py --config --json
```

> **In a git worktree, invoke as `python -m tools.browser.agent_browser …`.**
> A direct script path puts `tools/browser/` on `sys.path` but not the repo root,
> so `tools.*` resolves to the installed/shared checkout — which may carry a
> different `vendor/drivers/` generation than the worktree you are testing.

### Element indices are per-observation

Indices are valid only for the `read_state()` that produced them. Acting on an
index the current DOM no longer carries raises `StaleIndexError`, whose message
tells the model to call `read_state` again — it never falls through to clicking
the wrong element. `ActionResult.to_text()` also reports when an action changed
the URL, which is the model's cue to re-read.

### DOM verbosity

`args/agent_browser.yaml` governs how much of the page reaches the model:

| Key | Effect |
|-----|--------|
| `include_attributes` | Allowlist — only these attributes are surfaced. The fastest way to blow up prompt size. |
| `drop_attrs_matching_text` | Drops an attribute whose value duplicates the visible text (`aria-label="Submit"` on a button reading "Submit"). |
| `max_elements` / `max_text_length` / `max_attr_length` | Hard caps; overflow sets `state.truncated`. |
| `viewport_only` / `occlusion_check` | Skip off-screen elements / elements covered by an overlay. |
| `navigation.allowed_schemes` / `allowed_domains` / `blocked_domains` | Navigation gate — blocks `javascript:` and `data:` outright. |

Open shadow roots are traversed. **Cross-origin iframes are not** — such a frame
appears as one element, not as its contents.

### Agent loop wiring

```python
from tools.browser.agent_browser import AgentBrowser
from tools.browser.agent_tools import BrowserToolRegistry

with AgentBrowser() as browser:
    tools, handlers = BrowserToolRegistry(browser).build()
    run_agent_loop(..., tools=tools, tool_handlers=handlers)
```

### Assertion half — reused, not rebuilt

`AgentBrowser.validate(assertion)` screenshots and delegates to
`tools/testing/screenshot_validator.py::validate_screenshot` — the same code path
behind the MCP tool `validate_screenshot`. No vision validation is reimplemented.

```python
with AgentBrowser() as b:
    b.navigate("http://localhost:5050")
    print(b.validate("The CUI banner is visible at the top of the page"))
```
