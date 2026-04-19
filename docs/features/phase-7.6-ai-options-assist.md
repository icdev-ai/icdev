# CUI // SP-CTI
# FathomDesk Phase 7.6 — AI-Assisted Options Strategy Creation

**Shipped:** 2026-04-19. **Project:** `args/projects.yaml → fathomdesk-7-6`.
**Task prefix:** `ad76-` (26 tasks across 5 epics, all done).

## Why

Phase 7.5 shipped the options execution layer (13 canonical strategies,
atomic multi-leg fills, payoff engine). It was powerful but cognitively
punishing for new traders — picking direction × strike × expiry × leg
count is a 4-dimensional search, and most users don't read Greek tables.
Phase 7.6 closes the usability gap so a trader can say
*"I'm bullish on AAPL through earnings, limited downside"* and get a
vetted proposal in one click.

## Design — Hybrid LLM + Deterministic

| Layer | Owner | Why |
|---|---|---|
| Intent parsing (`text → structured schema`) | **LLM** + rule fallback | Natural language is unbounded; fallback keeps air-gap deployments working |
| Strategy selection | **Deterministic rules** | Auditable (NIST AU), cheap, reproducible |
| Strike picking (delta-target) | **Deterministic** | Same reasons |
| Expiry bucketizing | **Deterministic** | Same reasons |
| Pre-flight risk gates | **Deterministic** | Hard limits must be enforceable |
| Pre-trade rationale paragraph | **LLM** (grounded) | Adds explanatory value without free-form numbers |
| Post-event coach recommendation | **LLM** (grounded) | Same |
| Order placement | **Existing multi-leg engine** | Phase 7.5 path with atomic rollback |
| Auto-close / auto-adjust | **Never** — user must click | Safety-critical |

**LLM fires twice per trade lifecycle:** once at intent parse, once per
coach event recommendation. Every other decision is a rule. This is the
only shape that's both magical and auditable.

## Components (by epic)

### intake (4 tasks)

- **`args/options_intent_schema.yaml`** — 4 enums, DTE buckets,
  keyword-hint corpus, defaults. Hot-reloadable.
- **`tools/trading/options/intent_parser.py`** — LLM-primary,
  rule-fallback. Never raises. Whole-phrase matching (regex word
  boundaries) so `"undefined risk"` can't substring-match `"defined"`.
- **`tests/test_options_intent_parser.py`** — 28 tests across 10 intent
  corpora + 6 bizarre-input guards.

### scorer (4 tasks + gate)

- **`strategy_selector.rank_strategies(intent)`** — `(direction, risk_cap)`
  → priority list → scored with IV/horizon overlays → top-3 w/ reasons.
- **`strike_picker.pick_strikes(strategy_id, chain, expiry, expiries?)`** —
  delta-target from `args/options_strike_targets.yaml` (short 0.30Δ,
  long 0.15Δ). Falls back to strike-ladder offset when delta missing.
  Multi-expiry via `expiries={'near','far'}`.
- **`strike_picker.pick_expiry(horizon, chain)`** — picks chain expiry
  nearest to bucket midpoint.
- **`proposal_builder.build_proposal(intent)`** — full pipeline end-to-end.

### confirm (5 tasks + gate)

- **`args/options_risk_gates.yaml`** — `max_loss_pct_of_equity: 2.0`,
  IV warn band, undefined-risk tier requirement, DTE rail.
- **`tools/trading/options/preflight.py`** — `run_preflight(proposal,
  ...)`. Never executes; returns `{allowed, warnings, blocks, meta}`.
- **`POST /api/options/ai-assist/propose`** — intent → proposal +
  preflight + LLM-grounded rationale. LLM prompt forbids new numbers.
- **`POST /api/options/ai-assist/execute`** — server **re-runs
  preflight** (never trusts client-cached) before dispatching to
  `sandbox_engine.place_multileg_order`.
- **/options "AI Assist" tab** — intent textarea → proposal modal
  (payoff chart, rationale, legs table, warnings/blocks, Execute
  button disabled when blocks exist).

### coach (5 tasks + gate)

- **Migration 020** — `ad_options_coach_events` (append-only NIST AU,
  mutable `recommendation` column only, in `APPEND_ONLY_TABLES`).
- **`coach_db`** — CRUD + dedupe guard.
- **`coach_engine`** — rule-only scanner over `ad_sandbox_option_positions`.
  Triggers: 50% profit target, 2× loss, DTE ≤ 7 warning, DTE 8–21 roll
  window. Dedupe per (position, event_type) within 24h.
- **`coach_llm`** — LLM writes recommendations (≤3 sentences, must end in
  close/adjust/hold, no new numbers). Rule template fallback.
- **Daemon reflex `options_coach`** — 10m cadence. `GET /api/options/coach/events`
  + /portfolio card (auto-hides when no events).

### wrap (5 tasks)

- Manifest entries in `tools/manifest/fathomdesk-trading-engine.md`.
- This feature doc.
- Coherence gate 17/17 at every phase-gate; companion sync 10 platforms.
- Selenium E2E in `tests/e2e_selenium/test_ad76_ai_options_assist.py`.
- Memory + backlog update.

## Safety boundaries (explicit)

- **Never auto-closes** a position. The coach emits events + writes
  recommendations; the trader clicks.
- **Never auto-adjusts** legs. Rolling, widening, or adding legs is
  always a new user action.
- **LLM cannot introduce numbers.** Both prompts (propose rationale,
  coach rec) explicitly forbid it. Rule-template fallback is fully
  auditable.
- **Server-side preflight on execute.** Client-cached preflight is
  ignored — we re-run with live equity + tier check.
- **Tier-gate enforces live options approval.** Paper mode may run
  below the required tier (warn only) to let users learn; live path
  hard-blocks.
- **Append-only audit trail.** Migration 020's table joins the 12
  other NIST AU tables watched by `pre_tool_use.py`.

## What's still out of scope

- **DoD CAC integration** (deferred).
- **Auto-close / auto-adjust** (never shipping — by design).
- **Futures options** (broker-blocked, memory
  `project_fathomdesk_futures_deferred.md`).
- **L2 book / WebSocket tick streams** (still infra-gated).
- **IV-crush trigger** (needs `opened_iv` column on
  `ad_sandbox_option_positions` — backlog task).

## Screenshots

See `playwright/screenshots/options_ai_*.png` (generated by the
Selenium E2E run during `wrap-04`).

## Worked example (rule-path; no LLM required)

```
Intent: "Bullish AAPL through earnings, limited risk"
 ↓
parse_intent → {direction:bullish, horizon:earnings, iv_view:neutral,
                risk_cap:defined, underlying:AAPL, source:rule}
 ↓
rank_strategies → [
  bull_call_spread   (score 105, "priority slot 1 for bullish/defined"),
  long_call_butterfly (score 52),
  long_call          (score 38),
]
 ↓
pick_expiry(earnings)  → nearest chain expiry inside [0,45] DTE
pick_strikes(bull_call_spread)  → buy ATM call, sell OTM call at 0.30Δ
 ↓
compute_payoff → {x:[...], y:[...], max_profit, max_loss, breakevens}
 ↓
preflight → max_loss check vs account equity, DTE rail, IV band
 ↓
Propose → user confirms → execute → place_multileg_order (atomic)
 ↓
Coach scans every 10 min. At 50% profit: emit event + LLM rec.
User decides: close / adjust / hold.
```
