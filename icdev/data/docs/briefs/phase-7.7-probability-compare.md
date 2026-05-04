# CUI // SP-CTI
# FathomDesk Phase 7.7 — Probability & Compare

**Project:** `args/projects.yaml → fathomdesk-7-7`. **Prefix:** `ad77-`.
15 tasks across 3 epics.

## Why

Phase 7.6 shipped the proposal pipeline (intent → strategy → legs →
payoff). Users see **what** a trade looks like but not **how likely** it
is to win or **how it stacks up** against the alternates we already
rank. OptionStrat's two highest-value ideas fix both gaps.

## Scope — deterministic, no new LLM calls

### 1. Probability of Profit + price cone (PROB epic)

Monte Carlo on the IV-implied lognormal distribution:
`log-return ~ Normal(0, σ·√T)` where σ = IV / 100, T = DTE / 365.
Sample N paths → compute P&L at each terminal price → POP% = fraction of
paths with P&L > 0 at expiry. Also return p5/p25/p50/p75/p95 bands for
the price-cone overlay. Plots on top of the existing payoff chart.

### 2. Side-by-side compare (COMPARE epic)

`proposal_builder.build_proposal` already returns compact alternates
(slot 2 and 3 in the ranking). Extend those to full proposals with
payoffs + preflight + POP, expose via a new endpoint, render as a
3-column modal. Clicking any column promotes that alternate to the
primary proposal.

## Epics + tasks

### PROB (6 tasks)
- **ad77-prob-01** — `args/options_prob_config.yaml` (Monte Carlo params)
- **ad77-prob-02** — `tools/trading/options/probability.py` (`compute_pop`)
- **ad77-prob-03** — wire into `proposal_builder.build_proposal`
- **ad77-prob-04** — expose via `/api/options/ai-assist/propose`
- **ad77-prob-05** — frontend overlay (POP% badge + cone shading)
- **ad77-prob-gate**

### COMPARE (5 tasks)
- **ad77-compare-01** — extend `build_proposal` so alternates are full
  (payoffs + POP + preflight), not just summary
- **ad77-compare-02** — `POST /api/options/ai-assist/compare` endpoint
- **ad77-compare-03** — 3-column compare grid in the AI Assist tab
- **ad77-compare-04** — "promote alternate" flow (click a column → it
  becomes the primary proposal)
- **ad77-compare-gate**

### WRAP (4 tasks)
- **ad77-wrap-01** — manifest + coherence + companion
- **ad77-wrap-02** — feature doc
- **ad77-wrap-03** — Selenium E2E (POP badge + cone visible; compare
  modal + promotion click)
- **ad77-wrap-04** — backlog + memory

## Out of scope (intentionally)

- Greeks-at-time-T curves (idea #2 in the original list — deferred to
  its own phase since it needs a Black-Scholes pricer layer).
- Event-aware expiry (idea #6).
- Rolling calculator (idea #5).
- Shareable trade URLs (idea #7).
- Backtesting (idea #8).

## Assumptions

- **IV is the best distribution we have.** POP is not a guarantee; the
  UI labels it "IV-implied POP" to make the assumption explicit. Fat
  tails and earnings jumps are not modeled separately.
- **Monte Carlo default is 10 000 paths.** Deterministic seed per
  (underlying, expiry, intent) so re-renders are stable.
- **Cone bands shown at t=expiry only.** t-time cones would need a
  Black-Scholes pricer (deferred).
