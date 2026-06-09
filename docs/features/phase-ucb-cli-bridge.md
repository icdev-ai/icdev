# UCB — UI CLI Bridge: per-page toggle, status pill, and prompt panel

CUI // SP-CTI

## Summary

Landed the whole-page Claude Code CLI bridge UX on top of the existing
`tools.llm.cli_bridge.*` provider. Three layered changes:

1. **Per-page router override** (`ucb-be-04`) — the dashboard now seeds the
   router's context-scoped `cli_bridge_override` ContextVar from a cookie /
   header on every request, so any canvas AI endpoint honors the user's
   in-page toggle even when `ICDEV_CLI_BRIDGE=1` is set globally. Resets in
   `teardown_request` so no state leaks between requests served by the same
   worker thread.
2. **Status pill** (`ucb-widget-01`) — a fixed bottom-left pill (`includes/cli_bridge_indicator.html`)
   that polls `GET /api/cli-bridge/status` for `enabled`, `available`, `state`
   (`active` / `missing` / `off`), the env override, and the last served
   provider/model from the `ai_telemetry` table.
3. **Interactive prompt panel** (`ucb-widget-02`) — a slide-out textarea
   (`includes/cli_bridge_panel.html`) that posts to `POST /api/cli-bridge/prompt`
   with the page-level `force_bridge` override; renders provider/model +
   duration. Cap of 8000 chars, cancel-on-Escape / outside-click.

## How it works

### Router override (`tools/llm/router.py` + `tools/llm/cli_bridge/activate.py`)

`cli_bridge_override(force: bool)` returns a token that wraps the
`_routing_chain` resolution in a `ContextVar`. While set, every
`LLMRouter.invoke()` call within that context will (or will not) prepend
the `claude-cli` provider in the chain, regardless of the global
`ICDEV_CLI_BRIDGE` env. The dashboard's `before_request` hook in
`tools/dashboard/api/cli_bridge_api.py` reads `X-ICDEV-CLI-Bridge` (header
wins over `icdev_cli_bridge` cookie) → `parse_toggle()` → seeds the
override. The teardown hook always resets.

The override only *prepends* / *strips* the `claude-cli` provider — the
rest of the chain (cloud → local fallbacks) stays intact, so a missing
CLI binary degrades gracefully to the next provider.

### Status endpoint (`/api/cli-bridge/status`)

`cli_bridge_status()` builds a payload that honors the per-request
override (so polling after the toggle flips returns the new effective
state), then probes `is_cli_headless_capable()` for the dot color:

- `active`  → bridge enabled AND CLI resolvable (green)
- `missing` → bridge enabled but CLI binary not on PATH (amber)
- `off`     → bridge disabled for this page (grey)

The most recent `ai_telemetry` row is read for `last_provider` /
`last_model` / `last_served_at`. Best-effort: any failure (missing
table, no DB, empty) returns all-None so the pill gracefully shows "no
last provider".

### Prompt endpoint (`/api/cli-bridge/prompt`)

`run_cli_bridge_prompt(payload)` accepts
`{prompt, function?, force_bridge?}`. `force_bridge` may be a bool or a
toggle string (`on` / `off` / `true` / `false` / `1` / `0` / `yes` / `no`);
anything unrecognized means "no override" (defer to env + auto-detect).
The override is set for the duration of `router.invoke()` then reset in
`finally`, so the chain is restored for the rest of the request. Default
routing function is `codebase_query`; unknown functions fall through to
the router's `default` chain so the path-derived hint is always safe.

Failure returns `{error, content: "", provider: "", model: "", duration_ms: 0}`
with a hint that the local CLI bridge may be missing.

## Files changed

### Code
- `tools/dashboard/api/cli_bridge_api.py` (new: middleware, status, prompt, parse_toggle, cli_bridge_status, _last_provider_served, run_cli_bridge_prompt)
- `tools/llm/cli_bridge/activate.py` (cli_bridge_override / reset_cli_bridge_override / cli_bridge_enabled; resolve_cli_bridge_override)
- `tools/llm/router.py` (consult override in _routing_chain resolution)
- `tools/dashboard/app.py` (calls `register_cli_bridge(app)` after correlation middleware)

### Templates
- `tools/dashboard/templates/base.html` (render the two new includes)
- `tools/dashboard/templates/includes/cli_bridge_indicator.html` (new: status pill)
- `tools/dashboard/templates/includes/cli_bridge_panel.html` (new: slide-out prompt)

### Mirrors
- `icdev/tools/dashboard/templates/base.html`
- `icdev/tools/dashboard/templates/includes/cli_bridge_indicator.html`
- `icdev/tools/dashboard/templates/includes/cli_bridge_panel.html`

### Tests
- `tests/dashboard/test_cli_bridge_api.py` (212 + 100 = 312 lines; covers parse_toggle, status, prompt, override, middleware teardown, telemetry)

### Manifest + docs
- `tools/manifest/dashboard-api.md` (CLI Bridge API row)
- `tools/manifest/llm-providers.md` (CLI Bridge Router Override row)
- `tools/manifest/dashboard.md` (indicator + panel include rows)
- `docs/reference/commands.md` (CLI Bridge section under LLM Provider)
- `docs/features/phase-ucb-cli-bridge.md` (this file)

## Verification

- `pytest tests/dashboard/test_cli_bridge_api.py -v` passes.
- `node --check` passes on `chat.js` (no syntax regressions).
- Manual: open any dashboard page, set the cookie
  `icdev_cli_bridge=on` → pill goes green; toggle to `off` → goes grey;
  open the prompt panel → submit → response shows the active provider.
- Coherence gate passes (see `python tools/workflow/coherence_checker.py --all --fix --gate`).
