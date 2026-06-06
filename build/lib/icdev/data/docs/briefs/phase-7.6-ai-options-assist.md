# CUI // SP-CTI
# FathomDesk Phase 7.6 — AI-Assisted Options Strategy Creation

**Status:** Scoped 2026-04-19. Task registry: `args/projects.yaml` → `fathomdesk-7-6`. Prefix: `ad76-`.

## Why

Complex option strategies (verticals, condors, butterflies, calendars) are punishing for new traders:
picking direction + strike + expiry + leg count is 4-dimensional, and payoff math isn't intuitive.
Phase 7.5 shipped the execution layer (13 strategies, multi-leg atomic fills, payoff engine).
7.6 closes the usability gap so a trader can say *"I'm bullish on AAPL through earnings, limited
downside"* and get a vetted proposal in one click.

## Design — hybrid LLM + deterministic

| Layer | Owner | Why |
|---|---|---|
| Intent parsing (`text → structured schema`) | **LLM** + rule fallback | Natural-language is unbounded; fallback keeps us air-gap-safe |
| Strategy selection + strike picking + expiry | **Deterministic rules** | Auditable (NIST AU requires it), cheap, reproducible |
| Payoff + pre-flight + risk gates | **Deterministic** | Hard limits enforceable |
| Rationale text (pre-trade + post-event) | **LLM** grounded on computed outputs | Adds explanatory value without free-form numbers |
| Order placement | **Existing multi-leg engine** | Phase 7.5 already atomic with rollback |
| Auto-close / adjustment | **Never** — user must click | Safety-critical; coach notifies, never acts |

**LLM fires twice per flow:** once at intent parse, once per coach event rationale. Every other
decision is a rule. This is the only shape that's both magical and auditable.

## Epics (5)

### 1. `intake` — Intent → Structured Schema (4 tasks)

- **ad76-intake-01** — `args/options_intent_schema.yaml`: enums for
  `direction ∈ {bullish, bearish, neutral, volatile}`, `horizon ∈ {intraday, short, earnings, medium, long}`,
  `iv_view ∈ {high, low, neutral}`, `risk_cap ∈ {defined, undefined}`.
- **ad76-intake-02** — `tools/trading/options/intent_parser.py`: `parse_intent(text, underlying) → dict`.
  Primary: LLM (Ollama-first). Fallback: keyword matcher so air-gap still works.
- **ad76-intake-03** — 10 canned intents in a smoke test; assert each produces a valid schema row.
- **ad76-intake-gate** — codelens + coherence + regression + companion.

### 2. `scorer` — Strategy + Strike + Expiry Picker (5 tasks)

- **ad76-scorer-01** — `tools/trading/options/strategy_selector.py`: ranks the 13 strategies in
  `args/options_strategies.yaml` against intent. Rule table, not LLM. Returns top-3 with reasons.
- **ad76-scorer-02** — `tools/trading/options/strike_picker.py`: delta-target picker. Defaults:
  short leg 0.30Δ, long leg 0.15Δ. Tunable in yaml. Uses `options/chain.fetch_chain()`.
- **ad76-scorer-03** — `pick_expiry(horizon)` bucketizer: `intraday→0-3 DTE`, `short→7-21`,
  `earnings→next earnings window`, `medium→30-60`, `long→60-120`. Picks nearest available expiry.
- **ad76-scorer-04** — `build_proposal(intent, underlying)` orchestrator returning
  `{strategy, legs, payoff, rationale_stub, warnings}`.
- **ad76-scorer-gate**.

### 3. `confirm` — Pre-flight Gates + Execute Modal (6 tasks)

- **ad76-confirm-01** — `args/options_risk_gates.yaml`: `max_loss_pct_of_equity` (default 2.0),
  `min_iv_percentile` (20), `max_iv_percentile` (80), `undefined_risk_required_tier` (`L3`).
- **ad76-confirm-02** — `tools/trading/options/preflight.py`: runs a proposal through the gates;
  returns `{allowed, warnings, blocks}`. Hard-blocks on tier mismatch + max-loss breach.
- **ad76-confirm-03** — `POST /api/options/ai-assist/propose` — accepts `{intent, underlying}`,
  returns full proposal + preflight. LLM generates the rationale paragraph grounded in
  `compute_payoff()` output.
- **ad76-confirm-04** — `POST /api/options/ai-assist/execute` — accepts a vetted proposal,
  runs the existing `sandbox_engine.place_multileg_order()` (paper) or live path (gated on
  graduation + options approval tier per Phase 7.5 follow-up C).
- **ad76-confirm-05** — Frontend: "AI Strategy Builder" tab in `/options` — intent textarea →
  proposal modal (payoff chart, rationale, warnings, blocks, single *Execute* button).
- **ad76-confirm-gate**.

### 4. `coach` — Position Coach (6 tasks)

- **ad76-coach-01** — Migration `017_options_coach_events`: table `ad_options_coach_events`
  (append-only NIST AU). Columns: `id, position_id, user_id, tenant_id, event_type, severity,
  summary, recommendation, position_snapshot_json, created_at`. Also appends to
  `APPEND_ONLY_TABLES` in `pre_tool_use.py` + `MINIMAL_ICDEV_SCHEMA` in `conftest.py`.
- **ad76-coach-02** — `tools/trading/options/coach_db.py`: CRUD helpers (`insert_event`,
  `list_events(user_id, limit)`, `latest_event_per_position`).
- **ad76-coach-03** — `tools/trading/options/coach_engine.py`: rule layer. Triggers on 50% max
  profit, 2× credit loss, 7 DTE warning, 21 DTE roll-window, 20% IV crush. No LLM.
- **ad76-coach-04** — `tools/trading/options/coach_llm.py`: `explain_event(event, snapshot) → str`
  grounded in P&L + Greeks + underlying. Writes to `event.recommendation`.
- **ad76-coach-05** — Register `options_coach` reflex in `market_intel/daemon.py` — every 10m.
  Frontend card on `/portfolio` + `/options` showing recent coach events with click-to-expand.
- **ad76-coach-gate**.

### 5. `wrap` — Registration + Docs (5 tasks)

- **ad76-wrap-01** — Update `tools/manifest/fathomdesk-trading-engine.md` with all new modules.
  Walk CLAUDE.md 8-point registration checklist.
- **ad76-wrap-02** — `docs/features/phase-7.6-ai-options-assist.md` feature doc.
  Screenshots to `playwright/screenshots/options_ai_*.png`.
- **ad76-wrap-03** — Coherence gate 17/17 + companion sync all 10 platforms.
- **ad76-wrap-04** — Selenium E2E `tests/e2e/fathomdesk/test_options_ai_assist.py`: submit intent,
  assert proposal modal, assert execute creates a sandbox multileg order.
- **ad76-wrap-05** — Append to `docs/fathomdesk-backlog.md`; update memory index
  (`project_fathomdesk_phase7plus.md`) with the shipped-checkmark.

## Dependency shape

Linear per epic; phase gates bridge. Exit gates are the sign-off — phantom completions are a known
risk (memory `feedback_kanban_phantom.md`) so the gate runs the mandatory 5-step validation every
time, even if the epic looks done.

## Explicit out-of-scope

- DoD CAC integration (deferred).
- Auto-close or auto-adjust positions — coach only notifies.
- Futures options — broker-blocked (memory `project_fathomdesk_futures_deferred.md`).
- L2 / WebSocket tick streams — still infra-gated.
- "Substantially identical" wash-sale detection beyond exact ticker — out of scope.
