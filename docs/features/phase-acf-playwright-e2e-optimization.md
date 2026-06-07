<!-- CUI // SP-CTI -->
# Playwright E2E Task Optimization (acf-vv-04 hang fix)

**Date:** 2026-06-07
**Trigger:** `acf-vv-04` ("Playwright E2E — run a cycle … + screenshots") hung and
timed out repeatedly (attempt 26). Goal: stop the hang and optimize **all current
and future** Playwright E2E kanban tasks; confirm CodeLens + Coherence run in
parallel.

## Root cause

Playwright E2E kanban tasks were dispatched to the Claude CLI agent, which drove
the Playwright **MCP browser** ad-hoc against the running dashboard. There was:

1. **No deterministic native spec** for `/foundry` — verification depended on the
   LLM agent issuing `browser_*` calls, which block indefinitely on a missing
   selector / unready page.
2. **No bounded budget** — because the description contains "playwright"/"e2e",
   the task inherited the **40-min pytest budget** (`MAX_EXECUTION_SECONDS_PYTEST
   = 2400`). A wedged browser session burned the full 40 min on **every** retry.

Net effect: ~17 hours of churn across 26 attempts with no progress.

## Changes

### 1. Bounded fail-fast budget for Playwright/browser E2E — `tools/genesis/reflexes/kanban.py`
- New constant `MAX_EXECUTION_SECONDS_PLAYWRIGHT` (default **900s / 15 min**,
  override `KANBAN_MAX_EXECUTION_SECONDS_PLAYWRIGHT`).
- New `_is_playwright_e2e(desc, task_type)` detector (matches `playwright`,
  `browser_navigate`/`browser_click`, or a `test` task that drives the UI for
  `e2e` + `screenshot`).
- `_get_task_timeout()` checks Playwright E2E **first**, before the pytest branch,
  and returns the bounded budget with **no adaptive inflation** — a hung session
  is reaped ~2.5× faster instead of falling into the 40-min ceiling.

### 2. Fail-fast E2E playbook preamble — `tools/genesis/reflexes/kanban.py`
`_build_instruction()` now prepends `_PLAYWRIGHT_E2E_PLAYBOOK` to any detected
Playwright E2E task. It steers the agent to:
- **Prefer the deterministic native spec** (`npx playwright test tests/e2e/<slug>.spec.ts`
  or `python tools/testing/e2e_runner.py --mode native`) over ad-hoc MCP driving;
  create the spec if missing.
- **Preflight** the route with a 5s `curl` and stop if not `200`.
- **Prefer API assertions** over flaky UI waits; one screenshot per state.
- If MCP `browser_*` is unavoidable: explicit short timeouts, retry a selector at
  most twice, **always `browser_close`**.
- Save screenshots only to `playwright/screenshots/`, and stop cleanly within the
  bounded budget.

### 3. Deterministic native spec — `tests/e2e/foundry.spec.ts` (new)
Drives the cycle through the JSON API (`POST /api/foundry/run`, `GET
/api/foundry/concepts`, `GET /api/foundry/concept/<id>`, `POST
/foundry/api/iqe-query`) and asserts on responses rather than waiting on
selectors — so it **cannot hang**. Covers board render + CUI banner + Run Cycle
control, the `proposed → scored → approved|rejected` envelope, emitted kanban
tasks on concept detail, and the IQE widget. Screenshots →
`playwright/screenshots/foundry-*.png`.

**Result:** all 4 tests pass in ~1 min (vs. the prior 40-min hang). Discovered by
`tools/testing/e2e_runner.py` (50 native specs) and the shared
`playwright.config.ts` (per-action 10s / navigation 30s timeouts,
`reuseExistingServer`).

### 4. CodeLens + Coherence parallelism — confirmed
Already parallel in the post-task verification suite:
`tools/workflow/validated_commit.py:665-669` runs `_run_codelens` and
`_run_coherence` concurrently via `ThreadPoolExecutor(max_workers=2)`; wall-clock
is `max(cl, co)` instead of `cl + co`. The E2E gate runs after (it depends on a
clean tree). No sequential CodeLens→Coherence path remains.

## Verification
- `ruff check tools/genesis/reflexes/kanban.py` → clean; `py_compile` → OK.
- `_is_playwright_e2e` true for acf-vv-04, false for plain chores / pytest tasks.
- Playbook injected into the acf-vv-04 instruction; budget routes to 900s.
- `npx playwright test tests/e2e/foundry.spec.ts` → **4 passed (~1.0m)**, 3
  screenshots written.

## Operational note
`kanban.py` is imported by the running Genesis scheduler daemon; the new budget +
playbook take effect on the **next daemon restart**. The native spec is effective
immediately (it is run by the agent / e2e_runner, not the daemon).
<!-- CUI // SP-CTI -->
