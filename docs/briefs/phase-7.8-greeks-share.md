# CUI // SP-CTI
# FathomDesk Phase 7.8 — Greeks Deep Dive + Quick Wins

**Project:** `args/projects.yaml → fathomdesk-7-8`. Prefix `ad78-`. 18 tasks / 4 epics.

## Why

Phase 7.7 shipped POP + compare on top of the 7.6 proposal flow. The
remaining OptionStrat parity items split naturally into:

1. **A deep unlock** — Black-Scholes pricer — which powers time-T
   payoff curves, theoretically-consistent rolling previews, and a
   future-Greeks column for the coach.
2. **Two single-session wins** — portfolio net Greeks (risk manager
   feature) and shareable trade URLs (distribution / docs feature).

Bundled together because they're orthogonal but all land cheaply in
the same session.

## Epics

### PRICING (6 tasks) — Black-Scholes + time-T payoff

- **ad78-pricing-01** — `tools/trading/options/pricing.py`: pure library.
  `bs_price(option_type, S, K, T, r, sigma, q=0)` + `bs_greeks(...)`.
  Closed-form via `math.erf` for the normal CDF.
- **ad78-pricing-02** — `tests/test_options_pricing.py`: put-call parity,
  ATM price symmetry, boundary (T→0 returns intrinsic), monotone
  invariants (call price increases with S; put decreases), sanity on
  known textbook values.
- **ad78-pricing-03** — `compute_payoff_at_time(legs, spot_range,
  dte_remaining, iv, r=0.04)` in `probability.py` (it already owns the
  per-leg math surface). Uses BS for interim time, falls through to
  expiry intrinsic when `dte_remaining == 0`.
- **ad78-pricing-04** — `proposal_builder` returns a `payoff_frames`
  list (e.g. [today, +7d, +14d, expiry]) alongside the existing
  `payoff` key. Compact — only send ≤5 frames.
- **ad78-pricing-05** — frontend "days from now" slider on the payoff
  chart. Re-render P&L curve from the current frame. Label shows
  DTE-remaining at that point.
- **ad78-pricing-gate**.

### PORTFOLIO (4 tasks) — Portfolio net Greeks

- **ad78-portfolio-01** — `portfolio_greeks.py`: query
  `ad_sandbox_option_positions`, sum Δ/Γ/Θ/ν per-user. Multiplied by
  100 × qty (signed by action). Fallback greeks are 0 when snapshot
  missing.
- **ad78-portfolio-02** — `GET /api/options/portfolio/greeks` returns
  `{net_delta, net_gamma, net_theta, net_vega, position_count, stale}`.
- **ad78-portfolio-03** — "📊 Portfolio Greeks" card on `/portfolio`
  showing the 4 numbers with severity coloring (big |delta| in yellow,
  highly-negative theta in red).
- **ad78-portfolio-gate**.

### SHARE (4 tasks) — Shareable trade URLs

- **ad78-share-01** — `options/share.py`: encode a proposal dict →
  base64(json(payload)) param. Decode reverses. Payload includes
  intent + legs + strategy_id + underlying. NO secrets, NO user_id.
- **ad78-share-02** — "🔗 Share" button on the AI Assist proposal card.
  Copies `location.origin/options?aiproposal=BASE64` to clipboard.
- **ad78-share-03** — on `/options` load, if `?aiproposal=...` param
  present, decode + pre-fill AI Assist tab + auto-submit so the
  recipient sees the same proposal.
- **ad78-share-gate**.

### WRAP (4 tasks)

- **ad78-wrap-01** — manifest entries (pricing, portfolio_greeks,
  share) + coherence + companion.
- **ad78-wrap-02** — feature doc `phase-7.8-greeks-share.md`.
- **ad78-wrap-03** — Selenium E2E: time-slider changes chart; portfolio
  greeks card populates; share URL round-trips.
- **ad78-wrap-04** — backlog + memory.

## Out of scope

- Event-aware expiry picker (earnings calendar) — still deferred.
- Rolling calculator UI — deferred. The BS pricer *enables* this, so
  it's on the backlog for the next phase.
- Backtesting harness — separate initiative.
- Greeks drift as a coach event type — event type already reserved in
  migration 020's CHECK constraint; emission rule deferred until the
  coach captures `opened_greeks` on entry.

## Assumptions

- Risk-free rate default **r = 4%** (close to current T-bill). Operator-tunable.
- Dividend yield **q = 0** (simplification; ignore for options on
  non-dividend stocks).
- Time-T slider renders ≤5 frames to keep payload small.
- Share-URL decoded payload is TRUSTED INPUT — we re-fetch the chain +
  rerun preflight server-side before anything executes.
