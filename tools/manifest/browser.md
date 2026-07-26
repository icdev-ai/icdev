# Browser Automation (Selenium Driver Manager)

> Shard of `tools/manifest.md`. See index at `tools/manifest.md`.

## Browser Automation
| Tool | File | Description | Input | Output |
|------|------|-------------|-------|--------|
| Driver Manager | tools/browser/driver_manager.py | Singleton WebDriver factory — resolves vendored msedgedriver (Edge primary, Win11 pre-installed) or chromedriver fallback; no runtime downloads; `get_driver()` returns ready WebDriver | `--probe`, `--json`, `--smoke` | Resolved browser + driver path; Selenium WebDriver instance |
| Agent Scope Controls | tools/browser/scope.py | oss-browse-02. Mandatory guardrail seam between an LLM-driven agent and a live WebDriver. `check_navigation(url)` enforces a default-deny domain allowlist (loopback only) + scheme allowlist + `egress_guard` for routable hosts; `SensitiveDataResolver` substitutes `<secret>name</secret>` at the driver (broker-authorized, env-sourced, never in prompt/transcript/audit); `ActionBudget` caps actions/failures/step wall-clock; `GuardedDriver` binds all of it to a session and writes one `audit_trail` row per action. Config: `args/browser_scope.yaml` (`ICDEV_BROWSER_SCOPE_CONFIG` overrides). | `--show`, `--check-url <url>`, `--json` | `ScopeDecision` / policy JSON; exit 1 when a URL is denied |
| Agent Browser | tools/browser/agent_browser.py | oss-browse-01. Indexed-element page representation for agents (browser-use adaptation) — `read_state()`, `navigate()`, `click()`, `type()`, `select()`, `press()`, `screenshot()`; DOM verbosity + attribute allowlist in `args/agent_browser.yaml`; every http(s) navigation is additionally cleared by `scope.check_navigation` | `--url`, `--screenshot NAME`, `--headed` | JSON page state: url, title, element_count, indexed elements (index/tag/role/text/allowlisted attributes) |
| Browser Package | tools/browser/__init__.py | Package init — re-exports `get_driver`, `DriverManager`, `AgentBrowser`, `GuardedDriver`, `check_navigation`, `load_scope_config` and the scope exception types | (import) | — |

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

### Agent browser — indexed page representation

Adapted from browser-use (see `docs/spikes/oss-00-ragflow-crawl4ai-browseruse-strix-adaptation.md`,
A3): interactive elements get stable integer indexes so a model acts via
`click(14)` rather than inventing a selector it cannot verify.

```python
from tools.browser.agent_browser import AgentBrowser

with AgentBrowser() as ab:                      # caller owns the session
    state = ab.navigate("http://localhost:5050/kanban")
    for el in state["elements"]:
        print(el["index"], el["role"], el["text"])
    ab.type(3, "promote")
    ab.click(7)
    ab.screenshot("kanban_after_click")         # playwright/screenshots/<name>.png
```

```bash
# Print the indexed state of a page as JSON
python tools/browser/agent_browser.py --url http://localhost:5050 --json

# …and save a screenshot alongside it
python tools/browser/agent_browser.py --url http://localhost:5050 --screenshot home
```

Behaviour lives in `args/agent_browser.yaml`, not in Python:

| Key | Meaning |
|-----|---------|
| `interactive_selector` | CSS selector deciding which nodes are index candidates |
| `include_attributes` | DOM-attribute allowlist copied into each descriptor (verbosity control) |
| `max_elements`, `max_text_length` | Caps on index size and per-element visible text |
| `skip_disabled` | Drop disabled controls from the index |
| `allowed_domains`, `allowed_schemes` | Navigation scope — **localhost/127.0.0.1 only by default**; `navigate()` raises `BrowserScopeError` otherwise |
| `headless`, `window_size`, `page_load_timeout` | Driver launch settings passed to `get_driver()` |

Indexes are rebuilt by every `read_state()` and invalidated by `navigate()`.
Acting on an index that no longer resolves raises `ElementIndexError` — the fix
is always to call `read_state()` again.
