# CUI // SP-CTI
# FathomDesk Phase 7.8 — Greeks Deep Dive + Quick Wins

**Shipped:** 2026-04-19. **Project:** `args/projects.yaml → fathomdesk-7-8`.
Prefix `ad78-` (18 tasks, 4 epics — all done).

## Why

Phase 7.7 gave us IV-implied POP + a compare grid. The remaining
OptionStrat-parity items split into one deep unlock (Black-Scholes
pricer, which enables everything time-aware) and two single-session
wins (portfolio greeks, shareable URLs).

## Three epics shipped

### 1. Black-Scholes pricer + time-T payoff (PRICING)

- **`tools/trading/options/pricing.py`** — closed-form BSM via
  `math.erf`. Pure Python, no numpy.
  - `bs_price(option_type, S, K, T, r, sigma, q=0)` — per-share dollars
  - `bs_greeks(...)` → Δ (per $1), Γ (Δ-rate per $1), Θ (per-day, from
    annual/365), ν (per 1% IV, /100), ρ (per 1%-rate, /100)
  - Edge cases never raise: T≤0 returns intrinsic; σ≤0 returns
    discounted forward intrinsic; negative spot returns 0.
- **`probability.compute_payoff_at_time(legs, spot_range, dte_rem, iv)`**
  — same `{x, y, max_profit, max_loss, breakevens}` shape as the
  expiry-intrinsic `compute_payoff`, so the frontend swaps frames
  without re-rendering the chart.
- **`proposal_builder` returns 5 time frames** — `+1d`, 25%, 50%, 75%,
  and expiry DTE. Alternates keep expiry-only payoffs (payload size).
- **Time-T slider** on the AI Assist payoff chart — drag to see how
  the P&L curve reshapes as theta eats extrinsic. Labels show DTE
  remaining at each tick.

### 2. Portfolio Net Greeks (PORTFOLIO)

- **`portfolio_greeks.compute_portfolio_greeks(user_id)`** — reads
  `ad_sandbox_option_positions`, sums Δ/Γ/Θ/ν per user multiplied by
  100 × signed qty. Positions without cached `last_greeks_json`
  contribute zero and surface in `stale_count`.
- **`GET /api/options/portfolio/greeks`** — returns the aggregate dict.
- **"📊 Portfolio Greeks" card on /portfolio** — 4 big numbers with
  severity coloring (|Δ| > 500 = yellow, > 2000 = red; Θ < −50 =
  yellow, < −200 = red). Auto-refreshes every 30s. Hides when
  `position_count == 0`.

### 3. Shareable trade URLs (SHARE)

- **`share.encode_proposal(proposal, intent)`** — URL-safe base64 token.
  What's encoded: intent, underlying, strategy_id, leg list (action,
  type, strike, expiry, qty). 2 kB cap.
  What's NOT: user_id, tenant, API keys, probability/payoff (recipient
  recomputes), rationale text, any identifying metadata.
- **`POST /api/options/ai-assist/share`** — returns
  `{token, url: "https://host/options?aiproposal=<token>"}`.
- **"🔗 Share" button** on the AI Assist proposal card → copies URL
  to clipboard (falls back to `prompt()` when clipboard API blocked).
- **Auto-load** on `/options?aiproposal=...` — browser decodes, fills
  AI Assist textarea + underlying, auto-submits. The SERVER re-runs
  `parse_intent → build_proposal → preflight` on fresh chain data.
  Nothing the URL claims is trusted.

## Assumptions

- **Risk-free rate r = 4%** (close to current T-bill). Tunable in
  `compute_payoff_at_time(r_annual=...)` and pricer call sites.
- **Dividend yield q = 0** (simplification for non-dividend equities).
- **Time-T slider frames = 5** max to keep payload under a few KB.
- **IV used per-frame is the proposal's avg leg IV** (same as the POP
  Monte Carlo uses). Real implementations would apply a per-leg term
  structure; we don't.

## Worked example

```
Proposal: bull_call_spread 150C / 155C, 14 DTE, premium debit $2.40
  payoff_frames:
    +1d  (DTE-rem 13)  curve is smooth S-shape, max_profit ~498
    +4d  (DTE-rem 10)  slightly more angular, more time value decayed
    +7d  (DTE-rem  7)  noticeably kinked around strikes
    +11d (DTE-rem  3)  nearly the expiry kink
    +14d (DTE-rem  0)  classic straight-line vertical spread payoff

Drag slider from expiry back to +1d → watch the curve "unfold" as
theta hasn't yet done its work.
```

```
/portfolio:
  📊 Portfolio Greeks   3 positions · 0 stale · as of 14:32:08
  NET DELTA      245.21    share-equivalents
  NET GAMMA        0.12    Δ change per $1
  NET THETA      -18.44    $ per day               ← theta is a daily cost
  NET VEGA        92.11    $ per 1% IV
```

```
Share:
  User 1 clicks 🔗 Share → clipboard gets
  https://fathomdesk.example.com/options?aiproposal=eyJ2IjoxLCJpbn...

  User 2 opens the URL → AI Assist tab auto-opens → intent textarea
  pre-filled with "Bullish AAPL through earnings, limited risk" →
  Propose fires → User 2 sees the same strategy selected against
  THEIR current chain + THEIR preflight (may differ if equity /
  options tier differ).
```

## Tests

- `tests/test_options_pricing.py` — 24 pytest cases: put-call parity,
  ATM symmetry, T→0 intrinsic, monotone-in-S, monotone-in-σ, known
  reference value, greek range invariants, long-option theta
  negativity, zero-input graceful handling, invalid-type raises.
- `tests/e2e_selenium/test_ad78_greeks_share.py` — time-slider renders
  + updates chart on drag; portfolio greeks card conditionally visible;
  share URL round-trip.

## Out of scope (next-phase candidates)

- **Event-aware expiry** (earnings / ex-div calendar) — still deferred.
- **Rolling calculator UI** — the BS pricer enables this; backlog item.
- **Backtesting harness.**
- **Greeks-drift coach event** — event type reserved in migration 020;
  emission rule deferred until coach captures `opened_greeks` on entry.
