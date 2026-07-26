# CUI // SP-CTI

# CDP-00 — Air-gap browser automation: browser-use transport adaptation, firecrawl adoption analysis

**Date:** 2026-07-26
**Sources:**
- browser-use — https://github.com/browser-use/browser-use (MIT), v0.13.6
- cdp-use — https://github.com/browser-use/cdp-use (MIT), v1.4.5
- firecrawl — https://github.com/firecrawl/firecrawl (**AGPL-3.0**; SDKs/UI MIT)

**Prior art this doc extends:** `docs/spikes/oss-00-ragflow-crawl4ai-browseruse-strix-adaptation.md`
(2026-07-25) already evaluated browser-use once and adopted exactly one idea from it. This document
does **not** re-litigate that decision. It addresses a constraint that analysis did not weigh: the
adaptation was built on a transport that cannot be obtained in an air-gapped environment.

**Method:** every ICDEV claim below was verified against the tree at `C:\ai\icdev` on 2026-07-26
(file:line where cited), including live probes of the installed browsers and the driver resolver.
Upstream claims come from the projects' own manifests and docs, fetched the same day.

---

## 1. Headline verdict

| Project | Verdict | The one idea worth taking |
|---|---|---|
| **browser-use** | **Reject the runtime (again); adopt the *transport decision*** | Drive the browser over the Chrome DevTools Protocol. No driver binary, no download, no version pinning. |
| **firecrawl** | **Reject wholesale** | Nothing that is not already shipped here. See §5. |

The oss-00 spike took browser-use's page representation — *"index the interactive elements so a model
can act on a page at all"* — and shipped it as oss-browse-01/02. That was the right call and it
stands. But it was built on **Selenium with vendored drivers**, and that reintroduces precisely the
artifact an air-gapped site cannot obtain: a driver binary whose major version matches a browser
that silently auto-updates underneath it.

Upstream browser-use hit this same wall and solved it by **deleting the driver layer entirely**.
That is the part worth taking now.

---

## 2. The problem, stated precisely

### 2.1 What air-gap actually forbids

Two distinct downloads, both blocked:

1. **Playwright** — `playwright install chromium` fetches a browser bundle (~150 MB) from a CDN.
   This is why oss-00 listed Playwright as a standing non-goal.
2. **WebDriver** — Selenium requires `chromedriver` / `msedgedriver`, a *separate binary* that must
   match the installed browser's major version, fetched from
   `chromedriver.storage.googleapis.com` / `msedgewebdriverstorage.blob.core.windows.net`.

ICDEV's answer to (2) is pre-staging: `tools/airgap/driver_vendor.py` downloads drivers into
`vendor/drivers/{name}/{major}/` with SHA256 verification, on a connected admin host, and
`tools/browser/driver_manager.py` resolves from that store at runtime without touching the network.

That design is sound in principle. In practice it has a maintenance coupling that air-gap makes
unserviceable: **the browser updates, the vendored driver does not.**

### 2.2 It is already broken — measured, not predicted

Live state of this checkout on 2026-07-26:

| | installed | vendored |
|---|---|---|
| Google Chrome | **150.0.7871.186** | `vendor/drivers/chromedriver/147/` → binary self-reports **147.0.7727.117** |
| Microsoft Edge | **150.0.4078.99** | `vendor/drivers/msedgedriver/` → **empty** (`.gitkeep` only) |

`python tools/browser/driver_manager.py --probe` returns:

```json
{
  "resolved_browser": "chrome",
  "resolved_driver_path": ".../vendor/drivers/chromedriver/147/chromedriver.exe",
  "resolved_source": "vendored",
  "resolved_edge_version": null
}
```

It reports success at *resolve* and would fail at *launch* — chromedriver refuses a browser three
majors ahead. Route 1 (vendored msedgedriver for Edge 150) has nothing to match; routes 2 and 4
(drivers on PATH) are empty on a clean workstation.

So `tools/browser/agent_browser.py` — the browser agent oss-browse-01 shipped, with 108 tests — cannot
start here today. On a connected host you fix that with a download. In the target environment you
cannot fix it at all.

### 2.3 The stated air-gap guarantee is not implemented

This is the more serious finding. `get_driver()`'s docstring
(`tools/browser/driver_manager.py:507`) promises:

> If no driver binary is resolvable (no vendored binary, none on PATH), `create_driver()` raises
> `AirgapDriverMissingError` with the admin refresh command. **Never triggers a CDN download.**

`AirgapDriverMissingError` **does not exist anywhere in the codebase** — the string appears only in
that docstring and in one plan-text literal (`tools/scripts/schedule_enterprise_frontend_plan.py:133`).
The actual fall-through is `tools/browser/driver_manager.py:290`:

```python
return DriverResolution(browser="chrome", driver_path=None, source="selenium_manager")
```

`driver_path=None` hands control to **Selenium Manager**, which resolves drivers *by downloading
them*. So the documented promise is inverted: in an air-gapped environment the operator gets a
confusing network timeout from a component they were told would never reach the network, instead of
an actionable "no vendored driver — run the admin refresh."

The same docstring also advertises `--disable-dev-shm-usage` and `--disable-features=…` launch
defaults that `create_driver()` never sets.

### 2.4 Why CDP removes the class of failure

The Chrome DevTools Protocol needs **no separate binary**. The browser serves it itself over a
WebSocket when launched with `--remote-debugging-port`. There is nothing to version-match, nothing to
pre-stage, and nothing to re-vendor when the browser updates. Microsoft Edge is preinstalled on every
Windows workstation and implements CDP with APIs identical to Chrome's.

This is exactly the move upstream made. **browser-use v0.13.6 has no `playwright` and no `patchright`
dependency.** It drives the browser through `cdp-use`, whose complete dependency list is:

```
httpx>=0.28.1
typing-extensions>=4.12.2
websockets>=15.0.1
```

Pure Python, no compiled extensions, MIT. And browser-use's own session layer *discovers* an
installed browser rather than downloading one — its error path is literally *"Chrome not found.
Please install Chrome or use Browser() with explicit executable_path."*

---

## 3. What ICDEV already has (do NOT rebuild)

| Upstream capability | ICDEV equivalent | Assessment |
|---|---|---|
| browser-use: indexed interactive elements | `tools/browser/agent_browser.py` — `_EXTRACT_JS` (:316+), `IndexedElement`, `PageState`, `DOMSelectorMap` equivalent | **Already built** (oss-browse-01). Detection covers native tags, ARIA roles, shadow DOM, occlusion via `elementFromPoint`, viewport filtering, outermost-interactive-only with a form-control exception. |
| browser-use: `include_attributes`, verbosity control | `args/agent_browser.yaml` — allowlist, `max_elements: 200`, `max_text_length: 120` | Already built. |
| browser-use: `sensitive_data` placeholders | `tools/browser/scope.py` — `SensitiveDataResolver`, `<secret>name</secret>` resolved at the driver via `tools/security/credential_broker.py` | Already built; ours is fail-closed. |
| browser-use: step/failure caps | `tools/browser/scope.py` — `ActionBudget` (50 actions, 3 failures, 15 s/step) | Already built. |
| browser-use: domain restriction | `tools/browser/scope.py` — `BrowserScopeConfig`, default-deny (`localhost`, `127.0.0.1`, `::1`), scheme allowlist, per-hop redirect re-check | Already built, and stricter than upstream. |
| browser-use: agent loop, tool registry | `icdev/tools/llm/agent_loop.py::run_agent_loop`; `tools/browser/agent_tools.py::BrowserToolRegistry` | **Already built.** oss-00 already ruled: do not import a second agent framework. |
| browser-use: vision judgement of a page | `tools/testing/screenshot_validator.py`, MCP `validate_screenshot` | Already built. |
| firecrawl: `/scrape` → LLM-ready markdown | `tools/http/page_extract.py` (density pruning + BM25, stdlib `html.parser`) | **Already built** (oss-00 A1). |
| firecrawl: safe fetch of untrusted pages | `tools/http/fetch_extract.py` — `FetchedPage`, mandatory injection scan, 2 MiB streaming cap | **Already built** (oss-00 A1b). Sandbox Gap 37/39. |
| firecrawl: `/interact` (AI click/scroll/type) | `AgentBrowser.click/type_text/select/press` | **Already built.** |

**The gap is one layer down from all of this:** the transport underneath `AgentBrowser`.

---

## 4. Recommended adaptation — a CDP transport under the existing agent

### 4.1 What stays exactly as it is

This is the point of the design. Everything valuable is above the transport line:

- **`_EXTRACT_JS`** (`tools/browser/agent_browser.py:316+`). The entire interactive-element detection
  and indexing engine is one self-contained JavaScript string, executed in a **single round trip**,
  returning plain JSON. Python never walks the DOM node-by-node. It is already transport-agnostic —
  CDP runs the identical string via `Runtime.evaluate`. This is the expensive, subtle part and it is
  not touched.
- **All of `tools/browser/scope.py`** — `GuardedDriver`, `BrowserScopeConfig`, `SensitiveDataResolver`,
  `ActionBudget`, `audit_browser_action`, `ScopeViolation`.
- **`tools/browser/agent_tools.py`**, `AgentBrowser`'s public API, `args/browser_scope.yaml`,
  `args/agent_browser.yaml`, and all 108 tests (`tests/test_agent_browser.py`, `tests/browser/test_scope.py`).

### 4.2 The port surface is small — but not as small as it first looks

Every Selenium call across `tools/browser/*.py`, enumerated:

| Level | Operations used |
|---|---|
| driver | `get(url)`, `quit()`, `execute_script(script, *args)` (3 sites), `save_screenshot(path)`, `get_screenshot_as_png()`, `current_url` / `title` via `GuardedDriver.__getattr__`, `switch_to.active_element` (`agent_browser.py:861`), `set_page_load_timeout` / `set_script_timeout` (`scope.py:679-687`) |
| element | `click()`, `send_keys(text)`, `clear()`, `tag_name` (`agent_browser.py:824,826`) |
| helpers | `selenium.webdriver.support.ui.Select` (:821), `selenium.webdriver.common.keys.Keys` (:512), `selenium.common.exceptions` (:735) |

Call it ~14 operations, not ten. Two of them deserve care:

- **`Select` is not one call.** Internally it uses `tag_name`, `get_attribute('multiple')`,
  `find_elements(By.TAG_NAME, 'option')`, `option.get_attribute('value')`, `option.text`, and
  `option.click()`. Emulating it against a CDP element would require implementing `find_elements`
  and `get_attribute`, roughly doubling the element surface. **Don't** — replace the `Select` usage
  with a single JS-side `select_option` that sets the value (or matches option text) and then
  explicitly dispatches `input` + `change` with `bubbles:true`. Assigning `.value` fires no events
  on its own, and framework handlers depend on that dispatch.
- **Timeout setters have no CDP equivalent.** Store them on the driver and use them as WebSocket
  receive deadlines. `scope.py` already calls them inside a `try/except` that tolerates drivers
  which do not support them, so this is not a behaviour change.

**`Runtime.callFunctionOn`, not `Runtime.evaluate`.** `_EXTRACT_JS` ends in a **top-level `return`**
(`agent_browser.py:464`), which is a syntax error outside a function body. It must be wrapped as
`function(){ …script… }` and invoked via `Runtime.callFunctionOn` — which is exactly how Selenium's
own `executeScript` works, and is what makes the script's `arguments[0]` resolve to the config
object. The wrapping is **mandatory, not cosmetic**; a future "simplification" to `Runtime.evaluate`
would break it. `_LOCATE_JS` and the scroll helper additionally pass live *element* handles, which
can only be done through `callFunctionOn` with an `objectId` — they cannot be JSON-injected.

**Coordinate trap — do not reuse the indexed geometry for clicks.** `_EXTRACT_JS` returns
**page** coordinates (`Math.round(rect.left + window.scrollX)`, `:452`). `Input.dispatchMouseEvent`
expects **viewport** coordinates. Feeding `IndexedElement.bounds` straight to the input dispatcher
produces clicks that land correctly on unscrolled pages and silently land wrong on scrolled ones.
The click point must be recomputed from a live `getBoundingClientRect()` at click time. Any test
fixture for this must be **taller than the viewport**, or the bug cannot reproduce.

With that caveat, real `Input.dispatchMouseEvent` clicks are still the right primary path —
`isTrusted: true` events are what fire native form submission, `:active` styling, focus rings, and
any library that checks `event.isTrusted`, all of which a JS `.click()` silently fails to trigger.
Keep the existing scripted-click fallback for the intercepted case.

### 4.3 Dependency posture — zero new *required* dependencies

CDP over loopback strips almost everything hard out of RFC 6455: no TLS, no proxy, no
`permessage-deflate` (simply don't offer it), server→client frames arrive unmasked, and
client→server masking is a 4-byte XOR. A stdlib `socket` client is roughly 250 lines.

**Recommendation: stdlib-only — *not* the usual optional-accelerator-with-fallback pattern.** The
repo's `pid_exists` precedent (`tools/compat/platform_utils.py:112` → psutil, else ctypes, else
`os.kill`) is the normal idiom here, and it is the wrong fit for this case:

- `websockets` 15.0.1 and `websocket-client` 1.9.0 are both importable on a *developer* machine and
  neither is declared in `requirements.txt`. A "prefer third-party if importable" design would
  therefore take the fast path in development and the **untested** fallback path on the actual
  air-gapped target — the exact inversion of where testing effort should land.
- The py3.9 floor makes the third-party branch *more* code, not less: `websockets.sync.client`
  only exists from 12.0, and below that it is coroutine-only, requiring an event loop inside a
  synchronous codebase. A version-conditional shim across that split is larger than the whole
  stdlib client.

Two implementation details that must be covered by tests, because they bite in practice: a
1920×1080 PNG screenshot arrives base64-encoded at 1–3 MB, so the payload read must loop on partial
`recv` and the 64-bit length path is exercised for real; and CDP responses interleave with
unsolicited events, so request/response correlation belongs one layer up and never inside the frame
codec. Keeping the codec free of CDP knowledge is also what makes a later swap to
`--remote-debugging-pipe` a single-file change.

Neither `cdp-use` nor `browser-use` can be a hard dependency regardless: both require Python ≥3.11,
and `pyproject.toml:10` declares `requires-python = ">=3.9"`.

### 4.4 Launch and discovery constraints to design around

- **Chrome/Edge ≥136 refuse `--remote-debugging-port` on the default profile** (a deliberate
  hardening against cookie theft). A non-default `--user-data-dir` is **mandatory**. This is good
  hygiene for testing anyway — a clean profile per run.
- Prefer `--remote-debugging-port=0` and read the actual port from the `DevToolsActivePort` file in
  the user-data-dir, rather than assuming 9222 is free.
- Air-gap hygiene flags matter or the browser stalls on calls that cannot complete:
  `--no-first-run`, `--disable-background-networking`, `--disable-component-update`, `--disable-sync`,
  alongside the existing `--headless=new`, `--disable-gpu`, `--no-sandbox`.
- Pre-existing browser processes can prevent a new instance honouring the debugging flags.
- `--remote-debugging-pipe` (fd-based, no TCP listener at all) is security-preferable and worth
  evaluating as a follow-on; Windows handle-inheritance makes it more work.
- **Discovery must cover Edge → Chrome → Chromium on both Windows and Linux** — the target estate has
  both, varying by site. The detection logic already exists but is stranded: Edge-via-`winreg` in
  `tools/browser/driver_manager.py:93`, Chrome-via-filesystem+PowerShell in
  `tools/airgap/driver_vendor.py:413` — the latter being an admin-only, network-requiring module.
  Extract into one shared, network-free locator.

### 4.5 Security posture must not regress

`tools/browser/scope.py:85` deliberately lists `execute_cdp_cmd` in `_BYPASS_ATTRS`, so reaching it
through the guarded driver raises `ScopeViolation`. That intent must survive: **CDP becomes an
internal transport detail *beneath* the guard, never a caller-reachable escape hatch.** The guard
wraps the caller-facing driver object; the backend's use of CDP underneath is not a bypass, and the
`_BYPASS_ATTRS` block on the wrapper stays exactly as it is.

`docs/security/sandbox-coverage.md` Gap 36 (`scope.py`, bypass-documented) and Gap 38
(`agent_browser.py` + `agent_tools.py`, sandboxed-via-renderer) both describe enforcement in terms
that a transport change touches. Both entries must be revised in the same change. Gap 36's stated
revisit trigger — page content reaching the planning prompt without passing prompt-injection
controls — is unaffected by this work and still applies.

**A genuinely new attack surface, which neither Gap covers today: CDP is unauthenticated.** Any
local process that discovers the debugging port can drive the browser — that is exactly why Chrome
136 stopped allowing it on the default profile. This must be recorded, with its mitigations, rather
than inherited silently:

- bind the listener to `127.0.0.1` only, never `0.0.0.0`;
- ephemeral port (`--remote-debugging-port=0`), so it is neither predictable nor long-lived;
- a **fresh temp profile per run** carrying no cookies, no saved credentials, no session state — so
  there is nothing of value for a local attacker to steal through it;
- short session lifetime with deterministic teardown;
- `--remote-debugging-pipe` (fd-based, no TCP listener at all) documented as the hardening path.

The temp profile is therefore doing double duty: it is required by Chrome ≥136 *and* it is the
control that makes an unauthenticated local port acceptable.

### 4.6 Recommended backend policy

**CDP default; Selenium selectable via config; browser-free HTTP verification as the declared
degradation.** This is the ladder in §4.7.2, expressed as configuration. It keeps the 108 existing
tests meaningful and makes the air-gap path the one that works by default.

Note the escape-hatch wording has changed from an earlier draft: Selenium is **not** the fallback
for "a CDP-hostile browser policy," because such a policy disables Selenium too (§4.7.1). Selenium
is retained for W3C-WebDriver-mandated audits and as a bug-escape hatch. The fallback for a
policy-blocked host is Tier 3.

Resolution must be a **declared order, never a silent try/except cascade**: `auto` resolves
CDP → Selenium → Tier 3, logging which tier was chosen and why; an explicit `cdp` or `selenium`
setting never degrades and raises instead.

Whatever the choice, degradation must be **loud**: when no browser can be located, raise a specific,
actionable error naming the search paths tried — the opposite of today's phantom
`AirgapDriverMissingError`.

### 4.7 Two deployment profiles, one capability ladder

The platform must serve two profiles with different freedoms:

- **Commercial** — enterprise policy is ours to set, and the network is available.
- **Air-gapped (Gov/DoD)** — **policy is not ours to change**, and nothing can be downloaded.

The naive reading is "CDP for one, Selenium for the other." That is wrong, and the reason matters.

#### 4.7.1 Selenium is not a fallback for a policy-blocked CDP

`RemoteDebuggingAllowed` is a Chromium enterprise policy (Edge ≥93; Chrome equivalent). Microsoft's
own documentation is explicit:

> If you enable or don't configure this policy, users can use remote debugging by specifying
> `--remote-debug-port` and `--remote-debugging-pipe` command line switches. If you disable this
> policy, users aren't allowed to use remote debugging.

It blocks **both** switches, is machine-wide (not per-profile), and requires a browser restart.

The consequence people miss: **`chromedriver` and `msedgedriver` drive the browser over CDP
themselves** — they launch it with `--remote-debugging-port` and speak DevTools to it. The classic
ChromeDriver failure `DevToolsActivePort file doesn't exist` is exactly this policy firing.

So the preconditions are nested, not parallel:

> **Anywhere Selenium works, CDP works. Anywhere policy blocks CDP, Selenium is dead too.**

CDP's requirements are a strict *subset* of Selenium's — same policy, same browser, minus the
version-matched binary. That inverts the earlier framing in this document: a restrictive policy is
not an argument for keeping Selenium, because it takes Selenium out as well.

#### 4.7.2 The ladder

| Tier | Requires | Air-gapped | Commercial |
|---|---|---|---|
| **1 — CDP** | remote debugging permitted + any Chromium-family browser | ✅ **zero downloads, zero binaries** | ✅ default |
| **2 — Selenium + WebDriver binary** | everything Tier 1 needs, **plus** a version-matched driver | ⚠️ sanctioned transfer, recurring every ~4 weeks | ✅ (driver is downloadable) |
| **3 — browser-free HTTP verification** | nothing | ✅ always available | ✅ |

Tier 3 is not hypothetical — it already exists and is the honest answer when policy forbids
debugging: `tools/testing/route_smoke.py` (authenticated route sweep), `api_contract_tester.py`
(live requests replayed against the OpenAPI spec), `fathomdesk_smoke.py` (HTTP-only page/API/schema
checks). What is genuinely lost at Tier 3 is anything needing a rendered DOM — `a11y_sweep.py`,
visual regression, and the agent browser itself. That loss should be **stated**, not discovered.

Tier 2 therefore survives as a *compatibility* option — for a site that mandates W3C WebDriver for
audit reasons, or as an escape hatch if the CDP client misbehaves — **not** as the air-gap answer.

#### 4.7.3 Detect the profile, don't guess it

The policy is readable from the registry, so profile selection is deterministic — no launch, no
timeout, no trial and error:

- `HKLM\SOFTWARE\Policies\Microsoft\Edge` → `RemoteDebuggingAllowed` (REG_DWORD)
- `HKLM\SOFTWARE\Policies\Google\Chrome` → `RemoteDebuggingAllowed`
- (and the `HKCU` equivalents)

**Unset means permitted** — that is the documented default, and it is the state on this development
machine (all four keys unset, verified 2026-07-26). Preflight reads the key, picks the tier, and
reports which one it chose and why. A one-line empirical confirmation on the real image is still
worth doing once, but it is a confirmation, not a discovery.

#### 4.7.4 Both use cases, one implementation

The two profiles do **not** need two codebases. Tier 1 is the default everywhere; Tier 2 stays
selectable; Tier 3 is the declared degradation when preflight says debugging is forbidden. The only
profile-specific behaviour is *which tier preflight selects and what it tells the operator*.

**The durable justification is recurrence, not today's outage.** Chrome and Edge ship a new major
roughly every four weeks. Even where a sanctioned transfer channel exists and vendoring a driver is a
ten-minute admin task, that task recurs perpetually across every air-gapped install. CDP removes the
chore permanently. Today's 147-vs-150 breakage is the symptom that surfaced it, not the case for it.

### 4.8 Scope: the agent browser *and* the E2E estate

Both must work air-gapped, so the backend contract has to be wider than `AgentBrowser`'s ~14
operations. Measured across `tests/e2e_selenium/` (30 modules), the ~76 `tests/e2e_*.py` scripts,
`tools/sharepoint/browser_fallback.py`, and `tools/testing/e2e_*.py`:

| Operation | Uses | | Operation | Uses |
|---|---|---|---|---|
| `find_element` | 531 | | `close` | 46 |
| `.text` | 302 | | `title` | 44 |
| `find_elements` | 240 | | `page_source` | 44 |
| `click` | 119 | | `clear` | 38 |
| `execute_script` | 116 | | `get_attribute` | 34 |
| `send_keys` | 100 | | `implicitly_wait` | 30 |
| `is_displayed` | 98 | | `current_url` | 28 |
| `quit` | 96 | | `set_window_size` | 20 |
| `save_screenshot` | 69 | | `switch_to`, `refresh`, `is_selected`, `submit`, `get_cookies` | ≤6 each |

That is **~22 distinct operations** — larger than the agent-browser subset, but bounded and entirely
mappable to CDP (`querySelector`/`querySelectorAll` → `objectId`, `Runtime.callFunctionOn` for
`.text` / `is_displayed` / `get_attribute`, `DOM.getOuterHTML` for `page_source`,
`Browser.setWindowBounds` for `set_window_size`, a polling loop for `implicitly_wait`).

**The load-bearing fact that makes this cheap:** the `selenium` Python package is **pure Python and
pip-installable offline** — it is already a vendored wheel in this repo. What cannot be obtained
air-gapped is the *driver binary*, not the library. So `By`, `WebDriverWait`, and
`expected_conditions` — which are plain Python helpers that simply call `driver.find_element(...)` —
keep working **unchanged** against a duck-typed CDP driver.

A WebDriver-compatible CDP facade therefore lets the existing E2E estate run air-gapped with **no
driver binary and near-zero test edits**. That is a materially larger build than the agent-browser
port, and it is why the `wd` epic below is separated from `port` — `port` ships the transport and
proves it on `AgentBrowser`; `wd` widens the same transport to the full estate.

---

## 5. firecrawl — reject

### 5.1 The license is blocking

The main repository is **AGPL-3.0** (SDKs and some UI components are MIT). `AGPL-3.0` is in the
copyleft blocklist at `tools/workflow/coherence_checker.py:1785`; a citation of a blocking-license
upstream without a recorded clean-room audit **fails `check_attribution_claims`**. This is not a
formality — the check exists because a prior phase mis-recorded an upstream's license.

### 5.2 The runtime is the shape already rejected

Self-hosting firecrawl requires Docker Compose orchestrating **Redis** (queueing/rate limiting),
**PostgreSQL**, and a **Playwright microservice** (`PLAYWRIGHT_MICROSERVICE_URL`) for JS rendering.
Docker-required deployment and Playwright are both standing oss-00 non-goals, and a Playwright
microservice reintroduces the exact browser download this document exists to eliminate. The hosted
API requires an `fc-` key, which is a non-starter for CUI egress.

### 5.3 Its value is already shipped here

- `/scrape` → LLM-ready markdown is `tools/http/page_extract.py` + `tools/http/fetch_extract.py`,
  built as oss-00 adaptations A1/A1b with sandbox decisions Gap 37/39. Ours additionally runs a
  mandatory prompt-injection scan on fetched bytes, which firecrawl does not.
- `/interact` overlaps `AgentBrowser` (§3).
- `/search` overlaps the research and OSINT scanners.

### 5.4 The one non-duplicated idea, and why it stays deferred

`/map` — instant sitemap + link URL discovery — is genuinely absent here. That is oss-00's **Gap C2**:
no `robots.txt` handling, no sitemap parsing, no frontier/visited set, no HTTP-level cache. oss-00
deliberately marked it *defer / do not build absent a real requirement*, and recorded that building
it ad hoc would produce "an impolite, uncached, unbounded crawler with no per-domain state."

Nothing has changed since. **Recorded as still-deferred**, not re-opened. If a concrete crawl
requirement appears, build it on `tools/http/client.py` per oss-00's C2/C3 guidance — do not vendor a
crawler.

---

## 6. Cost, risk, governance

- **Dependency budget: zero new required runtime dependencies.** stdlib WebSocket with optional
  acceleration; the browser is already installed on the target.
- **Attribution:** browser-use (MIT), cdp-use (MIT), and firecrawl (AGPL-3.0) each need an
  `_ATTRIBUTION_REGISTRY` entry in `tools/workflow/coherence_checker.py` before any file cites them,
  with clean-room notes. Follow the wording precedent in `tools/agent_toolkit/__init__.py` — concept
  adopted, independent implementation, no runtime dependency.
- **Blast radius:** an LLM that can click inside a platform managing ATO artifacts. The scope
  controls already exist and ship with the capability; they must not regress, and §4.5 is the
  binding constraint.
- **Air-gap:** the entire point. Must degrade loudly when no browser is present.
- **Public repo:** this document names file paths, version numbers, and design intent only.
- **Delivery mechanics:** worktree-first branches; project card in `args/projects.yaml` plus seeded
  kanban tasks; manifest/companion/coherence close-out.

**Non-goals:** Playwright; the browser-use package itself; `cdp-use` as a hard dependency; Docker
required paths; a general-purpose web crawler; npm; any cloud scraping API.

---

## 7. Defects found while mapping (fix these regardless of what is adapted)

None of these depend on adopting anything.

| # | Defect | Location | Severity |
|---|---|---|---|
| D1 | **The air-gap guarantee is not implemented.** `get_driver()` documents raising `AirgapDriverMissingError` and "Never triggers a CDN download"; that exception does not exist, and the real fall-through hands off to Selenium Manager, which downloads. Air-gapped operators get a network timeout instead of an actionable error. | `tools/browser/driver_manager.py:507` vs `:290` | **High** |
| D2 | **Vendored drivers are stale/absent** vs installed browsers — chromedriver 147 against Chrome 150; msedgedriver empty against Edge 150. `get_driver()` cannot produce a working session on this checkout. | `vendor/drivers/` | High |
| D3 | **`selenium` is not declared in `requirements.txt`** despite `tools/browser/` requiring it — the same silent optional-dependency cliff recorded as oss-00 defect D8. It is present only as a wheel in `vendor/wheels/`, which is git-ignored, i.e. a local machine artifact rather than a repo asset. | `requirements.txt` vs `tools/browser/` | Medium |
| D4 | **Docstring claims launch defaults the code does not set** (`--disable-dev-shm-usage`, `--disable-features=…`). | `tools/browser/driver_manager.py` | Low (docs) |
| D5 | **Port drift.** The canonical dashboard port is 5050 (`tools/dashboard/config.py:113`), but 5000 is still hard-coded in `tests/e2e_selenium/conftest.py`, `.claude/commands/test_e2e.md`, `.claude/commands/e2e/dashboard_health.md`, and the `--port` help text in `tools/dashboard/app.py`. | multiple | Low |
| D6 | **`resolve_driver()` discards the detected Edge version.** `_detect_edge_version()` works correctly (returns `150.0.4078.99` here from `HKCU\Software\Microsoft\Edge\BLBeacon`), but every Chrome branch of `resolve_driver()` constructs `DriverResolution(browser="chrome", …)` without passing `edge_version=`. That is why `--probe` reports `"resolved_edge_version": null` on a machine with Edge installed. One-line fix, and it is a prerequisite for any staleness check. | `tools/browser/driver_manager.py:258-290` | Low |
| D7 | **Four tests hard-import selenium at function scope with no `skipif`** (`:434` `ElementClickInterceptedException`; `:496`, `:505`, `:516` `Keys`). On a clean install where selenium is absent these **error** rather than skip, so the "fake-driver tier always runs" property does not hold. | `tests/test_agent_browser.py` | Low |

**Landscape note (context, not a defect):** three browser stacks coexist — vendored Selenium
(Python, the only air-gap-capable path, and the broken one), Playwright/TypeScript via `npx` (the CI
gate, 61 specs; CI runs `npx playwright install chromium --with-deps`, so that path is not
air-gappable either), and Playwright-MCP driven by a `claude -p` subprocess from
`tools/testing/e2e_runner.py`. Consolidation is out of scope here, but any CDP backend should be
understood as strengthening the first of the three, not adding a fourth.

---

## 8. Priority ranking

| # | Item | Size | Why this order |
|---|---|---|---|
| 1 | **D1 + D2 + D3 + D6** — make the existing Selenium path honest | S | Independent of any adaptation. D1 is a false assurance claim on exactly the constraint that motivated this review; it should not wait behind a new backend. |
| 2 | **Policy/tier preflight** — read `RemoteDebuggingAllowed`, pick and report the tier | S | Cheap, deterministic, and it tells you whether the rest of the plan is even applicable on a given image. Do it early precisely because it can shrink the plan. |
| 3 | **Stdlib WebSocket client** | S | Foundation, testable in isolation against a loopback socket server. |
| 4 | **Shared network-free browser locator** | S | Consolidates detection stranded across two modules; also feeds D2's staleness check. |
| 5 | **CDP driver + launch lifecycle** | M | The substance. |
| 6 | **Backend/tier selection wiring** | S | Keeps `scope.py` untouched and the 108 tests green. |
| 7 | **Sandbox-coverage + attribution updates** | S | Gate requirement; must land with the capability, not after. |
| 8 | **V&V on a driverless machine** (agent browser) | S | First proof. |
| 9 | **WebDriver-compatible facade** (`wd` epic) — the ~22-operation surface | L | Extends the same transport to the 30 E2E modules + ~76 scripts. Largest item; deliberately last because it is worthless until 3–6 are proven. |
| 10 | **Tier-3 degradation documented** — what is lost when policy forbids debugging | S | Honesty requirement. Names `route_smoke` / `api_contract_tester` / `fathomdesk_smoke` as the surviving tier and states that rendered-DOM checks are lost. |

---

## 9. Success criteria

Both deployment profiles and both consumers must be satisfied.

1. **Agent browser, driverless.** On a machine with `vendor/drivers/` empty and no network access,
   `AgentBrowser` reproduces one current hand-written e2e script's assertions against the live
   dashboard — one `audit_trail` row per action, zero navigation outside the allowlist, and no
   change to `scope.py`, `_EXTRACT_JS`, or any of the 108 existing tests.
2. **E2E estate, driverless.** A representative sample of `tests/e2e_selenium/` runs green against
   the CDP facade with no driver binary present and no test-body edits beyond driver construction.
3. **Profile honesty.** With `RemoteDebuggingAllowed=0` set in the registry, preflight selects
   Tier 3, says so explicitly, and no code path silently attempts either browser transport.
4. **Commercial parity.** With policy unset and the network available, Tier 1 is selected by default
   and Tier 2 remains selectable without code changes.

# CUI // SP-CTI
