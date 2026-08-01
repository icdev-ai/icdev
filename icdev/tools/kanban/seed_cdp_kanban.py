#!/usr/bin/env python3
# CUI // SP-CTI
"""Seed the CDP-00 air-gap browser automation project onto the kanban board.

Backs ``docs/spikes/cdp-00-browser-automation-airgap-adaptation.md``.

Every task is held behind ``cdp-gate-00``, which is seeded ``in_progress`` and
never completed by automation.  ``promote_backlog_to_scheduled`` refuses to
dispatch siblings while a ``*-gate-00`` task is open, so nothing here builds
until a human reviews the spike and releases the gate.

Usage::

    python tools/kanban/seed_cdp_kanban.py            # seed
    python tools/kanban/seed_cdp_kanban.py --json     # machine-readable report
    python tools/kanban/seed_cdp_kanban.py --dry-run  # print, insert nothing
"""

from __future__ import annotations

import argparse
import json
import sys

SPIKE = "docs/spikes/cdp-00-browser-automation-airgap-adaptation.md"

GATE_ID = "cdp-gate-00"


def _t(
    task_id: str,
    title: str,
    description: str,
    *,
    priority: str = "medium",
    task_type: str = "build",
    status: str = "backlog",
) -> dict:
    """Build a task spec.

    Every task except the gate itself declares ``depends_on_task_id = cdp-gate-00``.
    That FK is what actually blocks auto-dispatch — ``state_machine`` resolves
    dependants through it (``:566``, ``:574``, ``:615``). The ``-gate-00`` id
    suffix only protects the *sentinel* from being promoted/reaped
    (``tools/kanban/gates.py::is_manual_gate``); it does not gate siblings by
    prefix. Omitting the FK would leave every task below immediately
    dispatchable.
    """
    spec = {
        "id": task_id,
        "title": title,
        "description": description.strip(),
        "task_type": task_type,
        "priority": priority,
        "status": status,
        "dispatch_source": "cdp_spike_seed",
        "idempotency_key": f"cdp-00::{task_id}",
    }
    if task_id != GATE_ID:
        spec["depends_on_task_id"] = GATE_ID
    return spec


TASKS: list[dict] = [
    _t(
        GATE_ID,
        "MANUAL-MODE GATE — CDP-00 air-gap browser automation (held)",
        f"""
MANUAL GATE — do not complete via automation, do not open a PR for this task.

Holds every `cdp-*` task until a human has read {SPIKE} and approved the
approach. `promote_backlog_to_scheduled` will not dispatch sibling tasks while
this task is open.

BOTH earlier go/no-go questions have been ANSWERED by the product owner
(2026-07-26). Recorded here so nobody re-opens them:

  Q1 "enterprise policy" -> Policy-dependent behaviour is NOT available in
     air-gapped deployments (we cannot ask for a policy change); in commercial
     deployments it is fine. BOTH profiles must be supported.
  Q2 "scope" -> BOTH use cases must work: the agent browser AND the E2E Selenium
     estate. See the new `wd` epic.

KEY CORRECTION that follows from Q1 — do not plan around the old assumption:
Selenium is NOT a fallback for a policy-blocked CDP. `RemoteDebuggingAllowed`
(Edge >=93, Chrome equivalent) blocks BOTH --remote-debugging-port AND
--remote-debugging-pipe, and chromedriver/msedgedriver drive the browser over
CDP themselves — they launch it with --remote-debugging-port. The classic
"DevToolsActivePort file doesn't exist" ChromeDriver error IS this policy firing.
So CDP's preconditions are a strict SUBSET of Selenium's: anywhere Selenium
works, CDP works; anywhere policy blocks CDP, Selenium is dead too. The real
fallback is Tier 3 (browser-free HTTP verification), not Selenium.

Release procedure:

  1. Confirm the tier ladder in spike §4.7.2 is still the agreed shape:
       Tier 1 CDP (no binary)  ->  Tier 2 Selenium+driver (compatibility only)
       ->  Tier 3 browser-free HTTP (route_smoke / api_contract_tester /
           fathomdesk_smoke), which is what remains when policy forbids debugging.

  2. Confirm the epic `fix` tasks are still valid — they are independent of the
     adaptation and may already have been fixed by other work.

  3. Optional one-time empirical confirmation on the real air-gapped image (the
     registry preflight in cdp-port-06 makes this a confirmation, not a
     discovery):
       msedge.exe --headless=new --remote-debugging-port=0 \
                  --user-data-dir=%TEMP%\\cdpprobe about:blank
     then check whether %TEMP%\\cdpprobe\\DevToolsActivePort appears.

  4. Set this task done:
     python tools/kanban/cli.py --set-status {GATE_ID} done

Justification to keep in view: the case for this work is RECURRENCE, not today's
outage. Chrome/Edge ship a new major roughly every four weeks, so re-vendoring a
driver is a perpetual chore on every air-gapped install — not a one-time fix.
""",
        priority="high",
        task_type="chore",
        status="in_progress",
    ),
    # ── Epic: fix — standalone defects, independent of the adaptation ──────────
    _t(
        "cdp-fix-01",
        "Make the air-gap driver guarantee real (fail closed, no CDN fallthrough)",
        f"""
Defect D1 in {SPIKE} — severity High.

`tools/browser/driver_manager.py:507` documents that `create_driver()` raises
`AirgapDriverMissingError` when no driver resolves, and states "Never triggers a
CDN download." That exception does not exist anywhere in the codebase; the only
other occurrence of the name is a plan-text literal at
`tools/scripts/schedule_enterprise_frontend_plan.py:133`.

The real fall-through is `tools/browser/driver_manager.py:290`:
    return DriverResolution(browser="chrome", driver_path=None, source="selenium_manager")
`driver_path=None` hands off to Selenium Manager, which resolves drivers by
downloading them. So in an air-gapped environment the operator gets a network
timeout from a component documented as never reaching the network.

Do:
  - Define `AirgapDriverMissingError` in `tools/browser/driver_manager.py`.
  - Raise it from `create_driver()` when resolution yields no driver path AND
    air-gap is indicated (`tools.airgap.detector.is_airgap()`), with a message
    naming every path searched and the admin refresh command
    (`python tools/airgap/driver_vendor.py`).
  - Decide explicitly whether the Selenium Manager fall-through is permitted at
    all when NOT air-gapped; if kept, log it loudly at WARNING.
  - Correct the docstring so it describes what the code does.
  - Also fix D4 while here: the same docstring claims `--disable-dev-shm-usage`
    and `--disable-features=...` launch defaults that `create_driver()` never sets.

Acceptance: with `vendor/drivers/` emptied and `ICDEV_AIRGAP=true`, `get_driver()`
raises `AirgapDriverMissingError` naming the searched paths — no network call is
attempted. Regression test asserts the no-network property.
""",
        priority="high",
        task_type="fix",
    ),
    _t(
        "cdp-fix-02",
        "Vendored drivers are stale/absent vs installed browsers — refresh + staleness check",
        f"""
Defect D2 in {SPIKE} — severity High.

Measured on 2026-07-26:
  Chrome installed 150.0.7871.186  vs  vendor/drivers/chromedriver/147/ (147.0.7727.117)
  Edge   installed 150.0.4078.99   vs  vendor/drivers/msedgedriver/     (empty, .gitkeep only)

`python tools/browser/driver_manager.py --probe` reports the 147 driver as
"vendored" and resolved — it reports success at resolve time and fails at launch.
So the browser agent shipped by oss-browse-01 (108 tests) cannot start.

Note the failure SHAPE: `resolve_driver()` SUCCEEDS and returns the vendored 147
driver. The failure surfaces later inside `create_driver()` as chromedriver's own
"only supports Chrome version 147" error. So this is not a "no driver" path — it
is a silent version-mismatch path, which is why the error is opaque.

Do:
  - Fix defect D6 FIRST — it is a prerequisite. `_detect_edge_version()` works
    correctly (returns 150.0.4078.99 here from HKCU\\Software\\Microsoft\\Edge\\BLBeacon),
    but every Chrome branch of `resolve_driver()`
    (`tools/browser/driver_manager.py:258-290`) builds
    `DriverResolution(browser="chrome", ...)` WITHOUT passing `edge_version=`.
    That is why `--probe` reports `"resolved_edge_version": null` on a machine
    with Edge installed. One-line fix.
  - Add a staleness check: `--probe` must compare the resolved driver binary's
    major against the detected browser major and report `stale: true` rather than
    presenting a doomed resolution as healthy. Emit a WARNING from
    `create_driver()` on mismatch so the failure is self-explaining.
  - Repopulate `vendor/drivers/` for the installed browser majors on a connected
    admin host via `python tools/airgap/driver_vendor.py`.
  - Document the refresh cadence — this WILL recur on every browser major bump,
    which is the structural argument for the epic `port` work.

Acceptance: `--probe --json` reports `stale` and `resolved_edge_version`
truthfully; a stale resolution does not present as a successful one.
""",
        priority="high",
        task_type="fix",
    ),
    _t(
        "cdp-fix-03",
        "Declare selenium, or make its absence a loud failure",
        f"""
Defect D3 in {SPIKE} — severity Medium.

`selenium` is not in `requirements.txt` despite `tools/browser/` requiring it.
It exists only as a wheel under `vendor/wheels/`, which is git-ignored — a local
machine artifact, not a repo asset. On a clean `pip install -r requirements.txt`,
everything under `tools/browser/` fails at import with a bare ImportError.

This is the same silent optional-dependency cliff recorded as defect D8 in
docs/spikes/oss-00-ragflow-crawl4ai-browseruse-strix-adaptation.md.

Do: either declare it, or catch the ImportError at the driver factory and raise
an actionable error naming the extra to install. Prefer whichever matches the
outcome of the epic `port` work — if CDP becomes the default transport, selenium
should become genuinely optional rather than undeclared-but-required.

Also fix defect D7 while here: four tests hard-import selenium at FUNCTION scope
with no `skipif` — `tests/test_agent_browser.py:434`
(ElementClickInterceptedException), `:496`, `:505`, `:516` (Keys). On a clean
install where selenium is absent these ERROR rather than skip, so the
"fake-driver tier always runs without a browser" property does not actually hold.

Acceptance: a clean venv install either works or fails with a message naming the
fix. No bare ImportError. The fake-driver test tier runs green with selenium
absent.
""",
    ),
    _t(
        "cdp-fix-04",
        "Retire stale dashboard port 5000 references in favour of 5050",
        f"""
Defect D5 in {SPIKE} — severity Low.

Canonical port is 5050 (`tools/dashboard/config.py:113`). Still hard-coded to
5000: `tests/e2e_selenium/conftest.py` (BASE_URL default),
`.claude/commands/test_e2e.md` (application_url default),
`.claude/commands/e2e/dashboard_health.md` (prerequisites + steps), and the
`--port` help text in `tools/dashboard/app.py`.

Effect: Selenium e2e modules silently skip (the conftest autouse fixture TCP-probes
BASE_URL and skips the module when unreachable), so tests appear to pass by not
running.

Acceptance: no 5000 default remains; `tests/e2e_selenium/` actually executes
against a running dashboard rather than skipping.
""",
        priority="low",
        task_type="chore",
    ),
    # ── Epic: port — the CDP transport ────────────────────────────────────────
    _t(
        "cdp-port-01",
        "Minimal RFC 6455 WebSocket client for loopback CDP",
        f"""
§4.3 of {SPIKE}.

CDP over loopback strips almost everything hard out of RFC 6455: no TLS, no
proxy, no permessage-deflate (just don't offer it), server->client frames arrive
unmasked, client->server masking is a 4-byte XOR. ~250 lines on stdlib `socket`.

DECISION: stdlib-ONLY. Do NOT use the usual optional-accelerator-with-fallback
pattern here, despite `tools/compat/platform_utils.py:112` being the house idiom.
Reasons:
  - `websockets` 15.0.1 and `websocket-client` 1.9.0 are both importable on a
    developer machine and NEITHER is declared in requirements.txt. "Prefer
    third-party if importable" would take the fast path in dev and the UNTESTED
    fallback path on the air-gapped target — the exact inversion of where the
    testing effort should land.
  - The py3.9 floor makes the third-party branch MORE code: `websockets.sync.client`
    only exists from 12.0; below that it is coroutine-only and needs an event
    loop inside a synchronous codebase.

Constraint: ZERO new REQUIRED runtime dependencies. Neither `cdp-use` nor
`browser-use` may become a hard dependency — both require Python >=3.11 and
`pyproject.toml:10` declares `requires-python = ">=3.9"`.

Scope: handshake (Sec-WebSocket-Accept verification), text frames, masking, all
three length forms, continuation, ping/pong, close handshake. Bind loopback only.
No server role. Keep the codec entirely FREE of CDP knowledge — that is what
makes a later swap to --remote-debugging-pipe a single-file change.

Two details that must be covered by tests because they bite in practice:
  - A 1920x1080 PNG screenshot arrives base64-encoded at 1-3 MB, so the payload
    read MUST loop on partial recv and the 64-bit length path is exercised for
    real, not theoretically.
  - CDP responses interleave with unsolicited events, so request/response
    correlation belongs one layer UP and never inside the frame codec.

Acceptance: unit-tested against a stdlib socketserver on 127.0.0.1 speaking a
hand-written server-side handshake — no browser involved. Passes on py3.9.
""",
        priority="high",
    ),
    _t(
        "cdp-port-02",
        "Shared, network-free browser locator (Edge/Chrome/Chromium, Windows+Linux)",
        f"""
§4.4 of {SPIKE}.

Browser detection already exists but is stranded across two modules, one of which
is admin-only and network-requiring:
  - Edge via winreg  -> `tools/browser/driver_manager.py:93` (_detect_edge_version_windows)
  - Chrome via filesystem + PowerShell VersionInfo -> `tools/airgap/driver_vendor.py:413`
    (_detect_chrome_major) — this module downloads drivers and must not be
    imported on an air-gapped runtime path.

Do: extract one shared, network-free locator returning executable path + version,
trying Edge -> Chrome -> Chromium across Windows and Linux. The target estate has
both Edge and Chrome/Chromium, varying by site.

Must degrade LOUDLY when no browser is found: raise naming every path searched.
This is the opposite of the phantom `AirgapDriverMissingError` (cdp-fix-01).

Acceptance: locates the browser on a Windows workstation with only Edge, and on a
Linux host with only chromium. Zero network calls — assert this in test.
""",
        priority="high",
    ),
    _t(
        "cdp-port-03",
        "CDP driver — the ~10-operation surface plus launch lifecycle",
        f"""
§4.2 and §4.4 of {SPIKE}. Depends on cdp-port-01, cdp-port-02.

The Selenium surface actually used across tools/browser/*.py is ~14 operations:
  driver:  get(url), quit(), execute_script(script,*args), save_screenshot(path),
           get_screenshot_as_png(), current_url, title,
           switch_to.active_element (agent_browser.py:861),
           set_page_load_timeout / set_script_timeout (scope.py:679-687)
  element: click(), send_keys(text), clear(), tag_name (agent_browser.py:824,826)
  helpers: Select (agent_browser.py:821), Keys (:512), selenium.common.exceptions (:735)

Map to CDP: Page.navigate, Runtime.callFunctionOn, Page.captureScreenshot,
Target.getTargetInfo (url+title in one round trip), Input.dispatchMouseEvent,
Input.insertText, Input.dispatchKeyEvent, DOM.focus, DOM.scrollIntoViewIfNeeded.

THREE TRAPS — each will silently produce wrong behaviour if missed:

1. Runtime.callFunctionOn, NOT Runtime.evaluate. `_EXTRACT_JS` ends in a
   TOP-LEVEL `return` (agent_browser.py:464), a syntax error outside a function
   body. It MUST be wrapped as `function(){{ ...script... }}` — which is also what
   makes `arguments[0]` resolve to the config object, exactly as Selenium's own
   executeScript works. The wrapping is MANDATORY, not cosmetic. `_LOCATE_JS` and
   the scroll helper additionally pass live ELEMENT handles, which can only go
   through callFunctionOn with an objectId — they cannot be JSON-injected.
   VALIDATE THIS FIRST, with a throwaway script, before writing anything else.

2. PAGE vs VIEWPORT coordinates. `_EXTRACT_JS` returns PAGE coords
   (Math.round(rect.left + window.scrollX), :452). Input.dispatchMouseEvent wants
   VIEWPORT coords. Feeding IndexedElement.bounds straight to the dispatcher
   gives clicks that land correctly on unscrolled pages and silently land WRONG
   on scrolled ones. Recompute the point from a live getBoundingClientRect() at
   click time. The test fixture MUST be taller than the viewport or this cannot
   reproduce.

3. Select is not one call — internally it uses tag_name, get_attribute('multiple'),
   find_elements(By.TAG_NAME,'option'), option.get_attribute('value'), option.text,
   option.click(). Do NOT emulate it. Replace with a single JS-side select_option
   that sets value (or matches option text) then EXPLICITLY dispatches input +
   change with bubbles:true — assigning .value fires nothing, and framework
   handlers depend on that dispatch.

`_EXTRACT_JS` itself is transport-agnostic and MUST NOT be modified.

Prefer real Input.dispatchMouseEvent over scripted clicks: isTrusted:true events
are what fire native form submission, :active styling, focus rings, and any
library checking event.isTrusted. Keep the existing scripted-click fallback for
the intercepted case.

objectId LIFETIME: CDP objectIds die with the execution context and can point at
a detached node after a soft re-render — that is a WRONG CLICK, not a crash.
`agent_browser._locate()` already re-runs _LOCATE_JS on every action, so handles
stay fresh; verify that holds and never cache elements across actions. Use
objectGroup='icdev-agent' + Runtime.releaseObjectGroup per action or a 50-action
run leaks handles.

Launch lifecycle:
  - Chrome/Edge >=136 REFUSE --remote-debugging-port on the default profile.
    A non-default --user-data-dir is MANDATORY (also gives a clean profile per run).
  - Use --remote-debugging-port=0 and read the real port from DevToolsActivePort
    in the user-data-dir; do not assume 9222 is free.
  - Air-gap hygiene flags or the browser stalls on calls that cannot complete:
    --no-first-run --disable-background-networking --disable-component-update
    --disable-sync, alongside existing --headless=new --disable-gpu --no-sandbox.
  - Pre-existing browser processes can prevent flags being honoured — detect and
    report rather than hanging.
  - Temp-profile teardown on quit; do not leak processes or directories.

Follow-on (not this task): --remote-debugging-pipe opens no TCP listener at all
and is security-preferable; Windows handle-inheritance makes it more work.

Acceptance: drives the live dashboard end to end with `vendor/drivers/` empty.
""",
        priority="high",
    ),
    _t(
        "cdp-port-04",
        "Wire backend selection without touching scope.py or the 108 tests",
        f"""
§4.1 and §4.6 of {SPIKE}. Depends on cdp-port-03.

Recommended policy (CONFIRM AT GATE RELEASE): CDP default, Selenium selectable
via config where a vendored driver exists.

MUST remain unchanged: all of `tools/browser/scope.py` (GuardedDriver,
BrowserScopeConfig, SensitiveDataResolver, ActionBudget, audit_browser_action),
`tools/browser/agent_tools.py`, AgentBrowser's public API,
`args/browser_scope.yaml`, `args/agent_browser.yaml`, `_EXTRACT_JS`, and all 108
tests (tests/test_agent_browser.py + tests/browser/test_scope.py — 108 collected).

SECURITY CONSTRAINT (§4.5): `tools/browser/scope.py:85` lists `execute_cdp_cmd`
in `_BYPASS_ATTRS` so reaching it through the guarded driver raises
ScopeViolation. That intent MUST survive. CDP becomes an internal transport
detail BENEATH the guard, never a caller-reachable escape hatch. The
`_BYPASS_ATTRS` block on the wrapper stays exactly as it is.

Design choice to make and justify: a duck-typed driver object mimicking the ~10
Selenium calls (so scope.py needs zero changes) vs introducing an explicit
backend Protocol. Prefer whichever keeps the existing tests meaningful.

Acceptance: 108/108 tests green against BOTH backends; scope.py diff is empty.
""",
        priority="high",
    ),
    _t(
        "cdp-port-05",
        "Sandbox-coverage + attribution registry updates",
        f"""
§4.5 and §6 of {SPIKE}. Must land WITH the capability, not after.

Sandbox coverage (`docs/security/sandbox-coverage.md`):
  - Gap 36 (`tools/browser/scope.py`, bypass-documented) and Gap 38
    (`agent_browser.py` + `agent_tools.py`, sandboxed-via-renderer) both describe
    enforcement in terms a transport change touches. Revise both.
  - Gap 36's stated revisit trigger — page content reaching the planning prompt
    without passing prompt-injection controls — is unaffected by this work and
    still applies. Do not silently widen it.
  - RECORD THE NEW SURFACE, which neither Gap covers today: **CDP is
    unauthenticated.** Any local process that discovers the debugging port can
    drive the browser — that is precisely why Chrome 136 stopped allowing it on
    the default profile. Document the mitigations rather than inheriting the
    risk silently: listener bound to 127.0.0.1 only (never 0.0.0.0); ephemeral
    port via --remote-debugging-port=0; a FRESH temp profile per run carrying no
    cookies, credentials or session state, so there is nothing of value to steal
    through it; short session lifetime with deterministic teardown; and
    --remote-debugging-pipe (fd-based, no TCP listener at all) named as the
    hardening path. Note the temp profile does double duty — required by Chrome
    >=136 AND the control that makes an unauthenticated local port acceptable.
  - Enforced by `coherence_checker.py:check_sandbox_coverage`.

Attribution (`_ATTRIBUTION_REGISTRY` in `tools/workflow/coherence_checker.py`):
  - browser-use — https://github.com/browser-use/browser-use — MIT
  - cdp-use     — https://github.com/browser-use/cdp-use     — MIT
  - firecrawl   — https://github.com/firecrawl/firecrawl     — AGPL-3.0 (BLOCKING;
    referenced in the spike as a REJECT decision only, no code derived — record
    the clean-room note explicitly or check_attribution_claims fails the gate)
  Follow the wording precedent in `tools/agent_toolkit/__init__.py`: concept
  adopted, independent implementation, no runtime dependency.

Acceptance: `python tools/workflow/coherence_checker.py --all --gate` green.
""",
    ),
    _t(
        "cdp-port-06",
        "Policy/tier preflight — read RemoteDebuggingAllowed, pick and report the tier",
        f"""
§4.7.3 of {SPIKE}. Do this EARLY — it is cheap, deterministic, and it can shrink
the rest of the plan.

The enterprise policy that decides whether ANY browser automation is possible is
readable from the registry. No launch, no timeout, no trial and error:
  HKLM\\SOFTWARE\\Policies\\Microsoft\\Edge   -> RemoteDebuggingAllowed (REG_DWORD)
  HKLM\\SOFTWARE\\Policies\\Google\\Chrome    -> RemoteDebuggingAllowed
  (+ the HKCU equivalents)

UNSET MEANS PERMITTED — that is the documented default, and it is the state on
the dev machine (all four keys unset, verified 2026-07-26).

Implement a preflight that reads the policy, resolves the tier, and REPORTS it:
  Tier 1 CDP            — policy permits + a Chromium-family browser is present
  Tier 2 Selenium       — Tier 1 preconditions PLUS a version-matched driver
                          (compatibility/audit option, not the air-gap answer)
  Tier 3 browser-free   — policy forbids debugging; no browser transport is
                          possible at all, either flavour

Surface it in `DriverManager.probe()` (add `--probe-cdp`) and register a
capability in `tools/testing/health_check.py --json` so a degraded tier shows up
in the health check rather than at first click.

CRITICAL — do not encode the old assumption: Tier 2 is NOT a fallback for a
policy-blocked Tier 1. chromedriver/msedgedriver launch the browser with
--remote-debugging-port, so the same policy kills them. Selecting Selenium when
policy forbids debugging must be impossible by construction.

Acceptance: with RemoteDebuggingAllowed=0 set in the registry, preflight selects
Tier 3, says so explicitly, and neither browser transport is attempted. With the
key unset, Tier 1 is selected. Both assertions covered by tests with the registry
read monkeypatched.
""",
        priority="high",
    ),
    _t(
        "cdp-port-07",
        "Document the Tier-3 degradation — what is lost when policy forbids debugging",
        f"""
§4.7.2 of {SPIKE}. Honesty requirement, small.

When `RemoteDebuggingAllowed=0`, NO browser transport works — CDP and Selenium
alike. What survives is the browser-free HTTP tier, which already exists:
  - tools/testing/route_smoke.py        — authenticated sweep of every nav route
  - tools/testing/api_contract_tester.py — live requests replayed vs the OpenAPI spec
  - tools/testing/fathomdesk_smoke.py    — HTTP-only page/API/DB-schema checks

What is genuinely LOST at Tier 3 must be stated, not discovered:
  - tools/testing/a11y_sweep.py (injects axe-core into a live browser)
  - visual regression / screenshot validation
  - the agent browser itself

Do: a short ops section (airgap runbook + tools/browser/README.md) naming the
surviving commands and the lost capabilities, plus what a site should do if it
needs the lost ones (get the policy changed, which is a commercial-profile
option only).

Acceptance: an operator on a policy-locked image can read one page and know
exactly which verification commands still work.
""",
        priority="low",
        task_type="chore",
    ),
    # ── Epic: wd — WebDriver-compatible facade (the E2E estate) ───────────────
    _t(
        "cdp-wd-01",
        "WebDriver-compatible CDP facade — the ~22-operation E2E surface",
        f"""
§4.8 of {SPIKE}. LARGE. Depends on the `port` epic being proven first — this is
worthless until the transport works.

Answers the product-owner decision that BOTH use cases must work air-gapped: the
agent browser AND the E2E Selenium estate.

Measured surface across tests/e2e_selenium/ (30 modules), ~76 tests/e2e_*.py
scripts, tools/sharepoint/browser_fallback.py, tools/testing/e2e_*.py:
  find_element 531, .text 302, find_elements 240, click 119, execute_script 116,
  send_keys 100, is_displayed 98, quit 96, save_screenshot 69, close 46,
  title 44, page_source 44, clear 38, get_attribute 34, implicitly_wait 30,
  current_url 28, set_window_size 20, switch_to 6, refresh 2, is_selected 2,
  submit 1, get_cookies 1.

~22 distinct operations. CDP mappings:
  find_element/find_elements -> Runtime.evaluate querySelector/querySelectorAll,
                                returnByValue=False -> objectId handles
  .text / is_displayed / get_attribute / is_selected -> Runtime.callFunctionOn
  page_source                -> DOM.getOuterHTML (or documentElement.outerHTML)
  set_window_size            -> Browser.setWindowBounds / Emulation.setDeviceMetricsOverride
  implicitly_wait            -> polling loop with deadline
  get_cookies                -> Network.getCookies / Storage.getCookies
  refresh                    -> Page.reload

THE FACT THAT MAKES THIS CHEAP: the `selenium` PYTHON PACKAGE is pure Python and
pip-installable offline (already a vendored wheel here). What cannot be obtained
air-gapped is the DRIVER BINARY, not the library. So By, WebDriverWait and
expected_conditions — plain Python helpers that just call driver.find_element(...)
— keep working UNCHANGED against a duck-typed CDP driver. Do not reimplement them.

Design: same duck-typing decision as the agent-browser port. No inheritance from
selenium classes; match the method names and return shapes the estate already
calls. Element handles must expose the element-level operations above.

Acceptance: a representative sample of tests/e2e_selenium/ runs green with NO
driver binary present and NO test-body edits beyond driver construction.
""",
        priority="medium",
    ),
    _t(
        "cdp-wd-02",
        "Cut the E2E estate over to the facade and prove it driverless",
        f"""
§9 criterion 2 of {SPIKE}. Depends on cdp-wd-01.

Do:
  - Route `tests/e2e_selenium/conftest.py` driver construction through the tier
    selector so the whole suite picks CDP by default (this is also where
    cdp-fix-04's port 5000 -> 5050 fix must already have landed, or the suite
    silently SKIPS instead of running — see the conftest autouse TCP probe).
  - Cut `tools/sharepoint/browser_fallback.py` and the `tools/testing/e2e_*.py`
    scripts over.
  - Record which scripts could not be cut over and why (the e2e script inventory
    at docs/testing/e2e-script-inventory.md already tracks 76 total / 69
    importable / 7 broken-import).

Acceptance: with `vendor/drivers/` EMPTY, the sampled suite runs green. Report the
pass/skip/fail split before and after so the change is measured, not asserted.
Do NOT let previously-skipping tests count as newly passing.
""",
        priority="medium",
        task_type="test",
    ),
    # ── Epic: vv ──────────────────────────────────────────────────────────────
    _t(
        "cdp-vv-01",
        "V&V — reproduce an e2e script's assertions on a driverless machine",
        f"""
§9 of {SPIKE} — the success criterion for the whole effort.

On a machine with NO vendored driver and NO network access, the existing
AgentBrowser must reproduce one current hand-written e2e script's assertions
against the live dashboard (port 5050), with:
  - one `audit_trail` row per action,
  - zero navigation outside the `args/browser_scope.yaml` allowlist,
  - no change to scope.py, _EXTRACT_JS, or any of the 108 existing tests.

Procedure: empty `vendor/drivers/`, set the air-gap indicator, run the chosen
scenario, capture evidence. Screenshots go to `playwright/screenshots/`.

This is the task that proves the air-gap claim rather than asserting it. If it
cannot pass, the epic `port` work has not delivered its purpose.
""",
        priority="high",
        task_type="test",
    ),
]


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Seed CDP-00 air-gap browser automation tasks")
    ap.add_argument("--json", action="store_true", help="JSON report to stdout")
    ap.add_argument("--dry-run", action="store_true", help="Print, insert nothing")
    args = ap.parse_args(argv)

    if args.dry_run:
        report = {
            "dry_run": True,
            "count": len(TASKS),
            "tasks": [{"id": t["id"], "title": t["title"], "status": t["status"]} for t in TASKS],
        }
        print(json.dumps(report, indent=2))
        return 0

    from tools.kanban.task_factory import create_tasks

    created = create_tasks(TASKS)

    report = {
        "created": created,
        "created_count": len(created),
        "submitted_count": len(TASKS),
        "skipped_existing": [t["id"] for t in TASKS if t["id"] not in created],
        "gate": GATE_ID,
        "spike": SPIKE,
    }
    if args.json:
        print(json.dumps(report, indent=2))
    else:
        print(f"Seeded {len(created)}/{len(TASKS)} CDP-00 tasks (gate: {GATE_ID})")
        for tid in created:
            print(f"  + {tid}")
        if report["skipped_existing"]:
            print("  (already present: " + ", ".join(report["skipped_existing"]) + ")")
    return 0


if __name__ == "__main__":
    sys.exit(main())
