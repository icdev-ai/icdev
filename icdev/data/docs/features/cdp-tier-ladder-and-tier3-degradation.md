# CUI // SP-CTI

# CDP capability ladder and the Tier-3 degradation (cdp-port-07)

**Card:** CDP Air-Gap Browser Automation (`cdp-`)
**Spike:** [docs/spikes/cdp-00-browser-automation-airgap-adaptation.md](../spikes/cdp-00-browser-automation-airgap-adaptation.md) §4.7
**Status:** honesty document — states what is *lost* when browser automation cannot run, rather than letting it be discovered at runtime.

---

## Why this document exists

Browser automation is not always available, and the platform must be honest about
that instead of stalling on a launch timeout. There is exactly one capability
ladder, chosen deterministically by preflight — and its bottom rung, Tier 3, is a
real, reduced mode with real gaps. Those gaps are enumerated here so an operator
reads them, rather than inferring them from a test that quietly stopped covering
the DOM.

## The ladder

| Tier | Transport | Requires | Air-gapped | Commercial |
|---|---|---|---|---|
| **1 — CDP** | Chrome DevTools Protocol over a loopback WebSocket | remote debugging permitted + any Chromium-family browser | ✅ zero downloads, zero binaries | ✅ default |
| **2 — Selenium** | WebDriver over a version-matched driver binary | everything Tier 1 needs, **plus** a version-matched `chromedriver`/`msedgedriver` | ⚠️ sanctioned transfer, recurring ~every 4 weeks | ✅ (driver is downloadable) |
| **3 — HTTP-only** | no browser | nothing | ✅ always available | ✅ |

Tier 1 is the default everywhere. Tier 2 is a **compatibility** option (a W3C-WebDriver-mandated audit, or an escape hatch if the CDP client misbehaves) — **never** the air-gap answer, and never auto-selected. Tier 3 is the declared degradation.

## The rung that surprises people

`RemoteDebuggingAllowed=0` (a Chromium enterprise policy, machine-wide) does **not**
demote CDP to Selenium. It kills **both**: the policy blocks
`--remote-debugging-port` *and* `--remote-debugging-pipe`, and `chromedriver` /
`msedgedriver` drive the browser over CDP themselves — the classic
`DevToolsActivePort file doesn't exist` failure is exactly this policy firing.

> **Anywhere Selenium works, CDP works. Anywhere policy blocks CDP, Selenium is dead too.**

So a restrictive policy is not an argument for keeping Selenium — it removes
Selenium as well, and the honest fallback is Tier 3.

## How the tier is chosen — and how to check it

Preflight reads the policy from the registry (Windows) or managed-policy JSON
(Linux) and picks the tier **without launching a browser**. Unset means *permitted*
(the documented Chromium default).

```bash
# What tier will this host use, and why?
python tools/browser/cdp/preflight.py --json

# Gate a pipeline step: exit non-zero if we are forced to Tier 3
python tools/browser/cdp/preflight.py --gate
```

The report names the tier, the policy value and where it was read, whether a
browser was found, and — at Tier 3 — exactly what is lost (below).

## What Tier 3 still gives you (the surviving verification)

Tier 3 is not "no testing." These already exist and run with no browser at all:

| Tool | What it verifies |
|---|---|
| `tools/testing/route_smoke.py` | Authenticated sweep of dashboard routes — every page returns without a server error under a real session. |
| `tools/testing/api_contract_tester.py` | Live requests replayed against the OpenAPI spec — the API contract holds. |
| `tools/testing/fathomdesk_smoke.py` | HTTP-only page/API/DB-schema checks for the FathomDesk surface. |

These cover "does the server respond correctly" end to end, over HTTP, with no
rendered DOM.

## What Tier 3 loses — stated, not discovered

Anything that needs a **rendered DOM** is unavailable at Tier 3:

- **The agent browser** (`tools/browser/agent_browser.py`) — no indexed-element page
  representation, so no LLM-driven interaction with a live page.
- **Accessibility sweeps** (`tools/testing/a11y_sweep.py`) — the accessibility tree
  is a browser artifact.
- **Visual regression / screenshot validation** — there is no page to screenshot.
- **Any e2e assertion that inspects rendered content** rather than an HTTP response.

A run that silently drops these reads as "everything passed" when it did not. At
Tier 3, preflight's report lists them under `decision.lost_at_this_tier` so the
reduction is visible in the run output, and the recurring V&V lesson still holds: a
rendered-DOM claim needs screenshot + DOM evidence, which Tier 3 cannot produce —
so those claims must be marked *not verified at this tier*, never *passed*.

## Restoring a higher tier

- **Tier 3 → Tier 1:** have the enterprise policy permit remote debugging
  (`RemoteDebuggingAllowed` unset or `1`) and ensure a Chromium-family browser is
  installed. No download, no driver binary.
- **Tier 3 → Tier 2** (only where Tier 1 is deliberately not used): additionally
  pre-stage a version-matched driver on a connected admin host and transfer it —
  `python tools/airgap/driver_vendor.py --fetch-edge` /
  `--fetch-chrome --major <installed-major>` — and watch for staleness with
  `python tools/browser/driver_manager.py --check-staleness`.

# CUI // SP-CTI
