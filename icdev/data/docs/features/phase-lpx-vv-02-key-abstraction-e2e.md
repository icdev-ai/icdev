# LPX vv-02 — E2E: an academy session proves the key abstraction

**Classification:** CUI // SP-CTI
**Status:** shipped
**Task:** `lpx-vv-02`

## Goal

Prove end to end that an academy (`apps/forge_academy`) / gameday
(`apps/ai_gameday`) session performs an LLM-backed action **successfully while the
session never holds a real provider key**.

## What is verified

`tests/e2e/test_lpx_vv02_key_abstraction_e2e.py` (4 tests, runnable headless):

1. **Per-guild key issuance (keys-01/02).** A `scope_type='guild'` virtual key is
   issued and resolves for enforcement; the plaintext key is returned once and
   **never persisted** (only a SHA-256 hash is stored), so it cannot leak from the
   database.
2. **Key abstraction for cloud providers.** With the proxy on and a virtual key
   set, every cloud provider the academy could route to resolves to the proxy
   `base_url` and presents the **virtual** key — the real provider-key env var is
   never selected, even with a real key present in the environment.
3. **The action succeeds with no real key held.** With no real provider key in the
   session environment, `ai_coach.get_hint` still returns a non-empty hint to the
   learner — the user-facing action completes with zero real credentials in the
   process.

## UI reachability (Playwright)

The `/academy/guild` and `/gameday` pages were driven with Playwright MCP against
a running dashboard (localhost:5050) and render real content (guild create/join
forms; gameday hub). Screenshots were captured to the house location
`playwright/screenshots/lpx_vv_02_academy_guild.png` and
`…_gameday.png`. That directory is gitignored, so the screenshots are V&V
evidence for this run, not committed binaries.

## Honest scope note (headless limitation)

A fully live **LLM-through-proxy browser** flow is deliberately not executed in
CI/headless. It requires a running LiteLLM proxy with upstream connectivity, and
— by design — the CUI egress gate (lpx-egress-02) blocks default-CUI traffic from
traversing the proxy. The LLM dimension of the proof is therefore established
deterministically at the session boundary (the key abstraction above), while the
UI dimension is established by the Playwright page renders. Together they show the
academy session completes its action without ever holding a real provider key.
