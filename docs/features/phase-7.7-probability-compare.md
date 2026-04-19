# CUI // SP-CTI
# FathomDesk Phase 7.7 — Probability & Compare

**Shipped:** 2026-04-19. **Project:** `args/projects.yaml → fathomdesk-7-7`.
**Task prefix:** `ad77-` (15 tasks / 3 epics, all done).

## Why

Phase 7.6 gave the user a **what** (a concrete strategy proposal with
legs + payoff + rationale). 7.7 adds the **how likely** (POP + price
cone) and the **what else** (side-by-side alternates). Together they
close OptionStrat's two highest-value UX gaps on top of our existing
hybrid (LLM interpretation + deterministic selection) architecture.

## Probability of Profit + price cone (PROB epic)

### Model

Geometric-Brownian-Motion terminal distribution under the
risk-neutral measure:

```
S_T = S_0 · exp( (μ − σ²/2) · T  +  σ · √T · Z )    Z ∼ N(0, 1)
```

- σ = IV / 100 (pulled from the chosen legs; fallback
  `options_prob_config.yaml::default_iv_fallback_pct` = 40%)
- T = DTE / 365
- μ = annual drift, default 0 (risk-neutral)
- N = 10 000 sample paths

For each path we compute P&L using the same `_leg_pnl_at_expiry`
function the payoff chart already uses, so **the overlay is
mathematically consistent with the line it sits on**.

**POP = fraction of paths where P&L > 0 at expiry.**
**Price cone = percentile bands (p5 / p25 / p50 / p75 / p95) of S_T.**

### Determinism

Seed = `sha256(underlying | expiry | sorted(legs))`. Re-rendering
the same proposal always produces the same numbers, so users don't
see POP wiggle on a page refresh.

### Honesty labels

- **Badge says "POP: XX% (IV-implied)"** — the qualifier is
  load-bearing. IV-implied POP is not a real-world probability; fat
  tails, earnings jumps, skew, and drift are all ignored.
- **Intraday returns None** (DTE < 1). Sub-daily paths need a
  different model (realized vol, order-book microstructure) that
  we're not building here.

### Visualization

Chart.js payoff chart gets three shaded bands:
- **p5–p95 wide band** — very light goldenrod
- **p25–p75 narrow band** — denser goldenrod
- **Dashed median line** at p50

The P&L curve sits on top in blue, so the user sees at a glance:
*"Here's my P&L shape. Here's where price is most likely to land."*
Wins when both are positive. Loses when the cone's center is in the
red zone.

## Side-by-side Compare (COMPARE epic)

### Extension to `build_proposal`

Alternates were summary-only in 7.6 (strategy id + max profit + max
loss). 7.7 promotes them to full proposals: legs + payoff +
probability. Both `alternates` (full) and `alternates_compact`
(legacy shape) are returned so existing UI code keeps working.

### `build_for_strategy`

New orchestrator helper that builds a single proposal for a
specifically-named strategy (skipping `rank_strategies`). Used by the
compare endpoint when the UI asks for a specific set of strategies.

### Compare endpoint

`POST /api/options/ai-assist/compare` body:
```
{intent_text, underlying?, qty?, strategy_ids?, iv_percentile?}
```

- When `strategy_ids` is given → build one proposal per id.
- When omitted → return primary + all alternates from `build_proposal`.

Every proposal goes through server-side `run_preflight` before return.
No LLM — compare is a pure deterministic diff.

### UI — 3-column compare grid

Below the primary proposal card, a **"Compare alternates"** button
reveals a grid of alternates. Each column:
- Strategy id + expiry
- Preflight ✓ / ⚠️ / ❌ + POP %
- Max profit / max loss
- Mini payoff chart
- Collapsible legs table
- **"Use this one"** button → promotes that alternate to the primary
  proposal card; user then clicks Execute as normal

Disabled "Use this one" when that alternate's preflight has blocks.

## Out of scope (kept for later phases)

- **Greeks-at-time-T curves** — needs a Black-Scholes pricer layer.
- **Event-aware expiry** — earnings-date calendar integration.
- **Rolling calculator** — "close + reopen 30d out" preview.
- **Shareable trade URLs.**
- **Backtesting.**

## Worked example

```
Intent: "Bullish AAPL through earnings, limited risk"
  ↓
parse_intent → {direction:bullish, horizon:earnings, risk_cap:defined}
  ↓
build_proposal →
  primary:     bull_call_spread @ 150/155, 14 DTE   POP 49%
  alternate 1: long_call_butterfly @ 145/150/155     POP 52%
  alternate 2: long_call @ 150                        POP 39%
  ↓
User sees POP 49% on primary + golden price cone overlay
User clicks "Compare alternates" → sees all 3 side-by-side
User clicks "Use this one" on long_call_butterfly (higher POP) →
  primary card swaps to the butterfly
User clicks Execute → multileg order fills in sandbox
```

## Tests

- **`tests/test_options_probability.py`** — 13 pytest cases covering
  POP range, percentile ordering, monotone invariants (OTM < ATM,
  higher IV widens cone, longer DTE widens cone), deterministic
  seeding, guards (intraday/missing inputs), and short-vs-long
  premium POP asymmetry.
- **`tests/e2e_selenium/test_ad77_probability_compare.py`** —
  end-to-end: POP badge renders, cone shading present, compare grid
  shows 3 columns, "Use this one" promotes the alternate.
