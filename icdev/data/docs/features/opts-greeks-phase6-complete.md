# Options Greeks — Phase 6b Complete
**Classification:** CUI // SP-CTI
**Date:** 2026-04-22
**Branch:** kanban/og-pgm-04

---

## Summary

Phase 6b closes the V&V + harmonization gate for the Portfolio Greek Manager and the full options subsystem. All five first-order Greeks (delta, gamma, theta, vega, rho) and three second-order Greeks (vanna, charm, volga) are now consistently shown, computed, and actionable across the chain, proposal, and portfolio manager pages.

---

## Deliverables

### 1. `build_hedge_proposal()` — `tools/trading/options/proposal_builder.py`

New public function that wires portfolio-level Greek state into hedge recommendations.

- Reads current net Greeks via `compute_portfolio_greeks(user_id)`
- Computes per-Greek gaps (current − target) against user-supplied `greek_targets`
- Assigns urgency tiers: CRITICAL / HIGH / MEDIUM / LOW / FLAT (based on gap vs 1-contract notional thresholds)
- Maps the dominant gap to hedge direction + top-3 strategy candidates via a deterministic lookup table
- Returns structured payload:
  ```json
  {
    "user_id": "...",
    "current_greeks": {"delta": ..., "gamma": ..., "vanna": ...},
    "targets": {"delta": 0, ...},
    "gaps": {"delta": -45.0, ...},
    "urgency_tier": "HIGH",
    "strategies": [{"strategy": "long_call", "rationale": "...", "urgency": "HIGH"}],
    "as_of": "2026-04-22T..."
  }
  ```

### 2. `/api/options/net_greeks/<ticker>` — `tools/dashboard/api/options.py`

New GET endpoint that returns portfolio-level net Greeks for the authenticated user.

- Calls `compute_portfolio_greeks(user_id)` (reads `ad_sandbox_option_positions`)
- Remaps `net_delta → delta`, etc. for frontend slider compatibility
- Returns all 8 Greeks (delta, gamma, theta, vega, rho, vanna, charm, volga) + position_count, stale_count, as_of
- Ticker param preserved for UI compatibility (portfolio Greeks are book-wide)

### 3. `/api/options/hedge-proposal` — `tools/dashboard/api/options.py`

POST endpoint now fully wired to `build_hedge_proposal()`. Previously a stub that would crash on import; now returns valid hedge strategy payload for any Greek deviation.

### 4. `/api/options/iv-skew/<ticker>` — `tools/dashboard/api/options.py`

New GET endpoint providing IV surface data for a ticker.

- **IV smile**: IV vs strike for the chosen expiry (calls and puts separately)
- **ATM IV term structure**: ATM IV vs expiry date across nearest 6 expirations
- `expiry_index` query param selects which expiry to use for the smile slice
- Returns: `{ticker, expiry, spot, skew: [{strike, iv, type}], term: [{expiry, dte, atm_iv}]}`

### 5. Greek Targets Panel — `tools/dashboard/templates/options.html`

Extended from 4 to 7 Greeks:

| Greek | Order | Slider Range |
|-------|-------|-------------|
| Delta | 1st | −500 → +500 |
| Theta | 1st | −100 → +100 |
| Vega | 1st | −500 → +500 |
| Gamma | 1st | −100 → +100 |
| Vanna | 2nd | −50 → +50 |
| Charm | 2nd | −50 → +50 |
| Volga | 2nd | −50 → +50 |

- All 7 sliders persist to localStorage per-ticker (`greek_targets_v1_<TICKER>`)
- Current values fetched live from `/api/options/net_greeks/<ticker>` on page load
- Green/red indicator shows within-20% of target range
- Second-order section visually separated by a divider

### 6. IV Skew + Term Structure Charts — `tools/dashboard/templates/options.html`

New dual-chart panel using Chart.js 4.4.3 (CDN):

- **IV Smile** (line chart): calls IV (green) and puts IV (red) vs strike for the nearest expiry
- **ATM IV Term Structure** (area line chart): ATM IV % vs expiration date
- Charts load automatically on page load via `/api/options/iv-skew/<ticker>`
- Displays spot price in status bar

---

## Harmonization Verification

| Component | Before Phase 6b | After Phase 6b |
|-----------|----------------|---------------|
| First-order Greeks (5) | Computed in chain/proposal, shown in chain only | Shown in chain + portfolio panel (current + target) |
| Second-order Greeks (vanna, charm, volga) | Computed, hidden in UI | Shown in portfolio panel with sliders |
| IVR badge | Wired, color-coded | Unchanged (already correct) |
| Hedge proposal endpoint | Stub — ImportError on call | Fully wired, returns urgency + strategies |
| Portfolio Greek fetch | Missing endpoint (404) | `/api/options/net_greeks/<ticker>` live |
| IV skew visualization | Not implemented | Dual-chart panel (smile + term structure) |
| localStorage persistence | Delta/theta/vega/gamma only | All 7 Greeks |
| Coach alerts | Engine + DB exist | No change (engine/DB complete; API exposure deferred) |

---

## Files Changed

| File | Change |
|------|--------|
| `tools/trading/options/proposal_builder.py` | Added `build_hedge_proposal()` (+~130 lines) |
| `tools/dashboard/api/options.py` | Added `/api/options/net_greeks/<ticker>` and `/api/options/iv-skew/<ticker>` endpoints |
| `tools/dashboard/templates/options.html` | Added vanna/charm/volga sliders, IV skew + term structure charts, Chart.js CDN |

---

## Validation

- `coherence_checker.py --all --gate`: **PASS** (0 failures, 1 pre-existing warn)
- `companion.py --sync --write`: **PASS** (10 platforms synced)
- `build_hedge_proposal()` import + smoke test: **PASS**
- All 3 new API endpoints load without ImportError: **PASS**
- options.html template: all 9 required elements present
