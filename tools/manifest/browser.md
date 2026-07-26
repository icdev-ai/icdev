# Browser Automation (Selenium Driver Manager)

> Shard of `tools/manifest.md`. See index at `tools/manifest.md`.

## Browser Automation
| Tool | File | Description | Input | Output |
|------|------|-------------|-------|--------|
| Driver Manager | tools/browser/driver_manager.py | Singleton WebDriver factory — resolves vendored msedgedriver (Edge primary, Win11 pre-installed) or chromedriver fallback; no runtime downloads; `get_driver()` returns ready WebDriver | `--probe`, `--json`, `--smoke` | Resolved browser + driver path; Selenium WebDriver instance |
| Agent Scope Controls | tools/browser/scope.py | oss-browse-02. Mandatory guardrail seam between an LLM-driven agent and a live WebDriver. `check_navigation(url)` enforces a default-deny domain allowlist (loopback only) + scheme allowlist + `egress_guard` for routable hosts; `SensitiveDataResolver` substitutes `<secret>name</secret>` at the driver (broker-authorized, env-sourced, never in prompt/transcript/audit); `ActionBudget` caps actions/failures/step wall-clock; `GuardedDriver` binds all of it to a session and writes one `audit_trail` row per action. Config: `args/browser_scope.yaml` (`ICDEV_BROWSER_SCOPE_CONFIG` overrides). | `--show`, `--check-url <url>`, `--json` | `ScopeDecision` / policy JSON; exit 1 when a URL is denied |
| Agent Browser | tools/browser/agent_browser.py | oss-browse-01. **Indexed-element page representation** — `read_state()` extracts every interactive element and assigns a stable integer index so a model acts via `click(14)` instead of inventing a CSS selector. Actions: `navigate`, `click`, `type_text`, `select`, `press`, `screenshot`. DOM verbosity governed by the `include_attributes` allowlist in `args/agent_browser.yaml`. **Holds a `scope.GuardedDriver`, not a raw WebDriver** — every navigation is allowlist-gated and every action is budgeted and audited by `scope.py`; there is no unguarded path to the session. Built on `get_driver()` — vendored Selenium, no runtime downloads, no Playwright | `--url`, `--text`, `--json`, `--screenshot`, `--name`, `--no-headless`, `--config`, `--run-id` | `PageState` (url/title/indexed elements/truncated) or `ActionResult`; both render to model-facing text via `.to_text()` |
| Browser Agent Tools | tools/browser/agent_tools.py | `BrowserToolRegistry(browser).build()` → `(tools, tool_handlers)` for `icdev.tools.llm.agent_loop.run_agent_loop`. Exposes `browser_read_state`, `browser_navigate`, `browser_click`, `browser_type`, `browser_select`, `browser_press`, `browser_screenshot`, `browser_close`. Pass `session="name"` instead of a browser to bind to a `session.py` session resolved on first use. `browser_schemas()` is the public accessor other seams merge from, so a description fixed here is fixed everywhere. Same convention as `tools/ace/agent_tools.py` | list[str] tool names | OpenAI function-calling schemas + handlers returning model-facing strings |
| Browser Session Broker | tools/browser/session.py | oss-browse-03. **Library, no CLI.** Process-local registry of *named* browser sessions plus one set of seam-neutral operations (`browser_navigate`, `browser_read_state`, `browser_click`, `browser_type`, `browser_select`, `browser_press`, `browser_screenshot`, `browser_close`) returning JSON-serialisable dicts. Exists because element indices are per-instance state and three of the four seams dispatch one stateless call at a time with nowhere to keep the instance. Every operation goes through `AgentBrowser`, so all of `scope.py`'s allowlist gate / budget / audit applies — there is no second, unguarded path. `ICDEV_BROWSER_MAX_SESSIONS` (default 4) caps live drivers per process. `BROWSER_TOOL_NAMES` is the one vocabulary all four seams register against | `get_session`/`close_session`/`close_all`/`list_sessions`, or the 8 ops | `AgentBrowser`, or `PageState`/`ActionResult` dicts tagged with `session` |
| Browser Package | tools/browser/__init__.py | Package init — re-exports `get_driver`, `DriverManager`, `AgentBrowser`, `PageState`, `IndexedElement`, `GuardedDriver`, `check_navigation`, `load_scope_config` and the scope exception types | (import) | — |

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

## Where the browser is registered — four seams, one implementation

ICDEV has four parallel agent-tool registries. Registering in the wrong one
strands the capability *silently*: nothing errors, the tool simply is not there
for the agent that needed it. oss-browse-03 registers all four against a single
vocabulary, `tools.browser.session.BROWSER_TOOL_NAMES`.

| # | Seam | Where | How it is wired |
|---|------|-------|-----------------|
| 1 | In-process toolkit | `tools/agent_toolkit/__init__.py` (via `_browser.py`) | Re-exports the `session.py` ops alongside `read_file` / `execute_shell`. A re-export, not a reimplementation — the test asserts function *identity*. |
| 2 | MCP gateway | `TOOL_REGISTRY["browser_*"]` in `tools/mcp/tool_registry.py` → `handle_browser_*` in `tools/mcp/gap_handlers.py` | `tools/agent_runtime/discovery.py` derives the SAG `ToolSpec` from this entry automatically — no third place to update. |
| 3 | Standalone agent bundle | `bundles.browser` in `args/agent_toolsets.yaml` | `mutating: true` for the whole bundle. |
| 4 | ACE co-workers | `_SCHEMAS` + `_make_handler` in `tools/ace/agent_tools.py` | Merges `browser_schemas()` rather than restating it; each co-worker gets its own session keyed by instance id, so concurrent co-workers never share element indices. **Opt-in** — never in the default tool set. |

A fifth consumer, the oss-browse-01 agent-loop registry
(`BrowserToolRegistry`), uses the same names and the same handlers.

### Why the whole surface is mutating

`browser_read_state` and `browser_screenshot` do not change the page, but they
are how untrusted remote content enters the model's context, and discovery's
read-only heuristic cannot tell a browser read from a filesystem read. Marking
the surface read-only would skip `default_safety_gate` in
`tools/agent_runtime/dispatch.py` entirely. Gating it as one unit is the
fail-closed choice: approve the session, not each glance at it.

### Enforcement

| Layer | Control |
|-------|---------|
| SAG dispatch | `default_safety_gate` denies every browser tool unless `ICDEV_SAG_ALLOW_MUTATION` is set. |
| RBAC | `browser_*` is an **explicit deny** for `pm` / `developer` / `isso` / `co` in `args/owasp_agentic_config.yaml`. `MCPToolAuthorizer` evaluates deny before allow, so a future wildcard broadening cannot grant it by accident. `admin` is allowed. |
| Navigation | `args/browser_scope.yaml` — default-deny allowlist, loopback only out of the box (`scope.py`, oss-browse-02). |
| Budget + audit | `ActionBudget` caps actions/failures/wall-clock; one `audit_trail` row per action, carrying the session name as `run_id`. |
| Resource | `ICDEV_BROWSER_MAX_SESSIONS` (default 4) caps concurrent live drivers per process. |

Gate: `BROWSER-SEC-001` in `args/security_gates.yaml`.
Tests: `tests/browser/test_browser_tool_registration.py` (174 tests, driver-free —
every assertion is about registration, not behaviour).

### Assertion half — reused, not rebuilt

`AgentBrowser.validate(assertion)` screenshots and delegates to
`tools/testing/screenshot_validator.py::validate_screenshot` — the same code path
behind the MCP tool `validate_screenshot`. No vision validation is reimplemented.

```python
with AgentBrowser() as b:
    b.navigate("http://localhost:5050")
    print(b.validate("The CUI banner is visible at the top of the page"))
```
