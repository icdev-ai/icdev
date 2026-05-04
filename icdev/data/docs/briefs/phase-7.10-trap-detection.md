# CUI // SP-CTI
# FathomDesk Phase 7.10 — Trap Detection

**Project:** `args/projects.yaml → fathomdesk-7-10`. Prefix `ad710-`.
12 tasks / 4 epics. **Blocked on 7.9** — uses S/R + swings + patterns.

## Why

Traps are the highest-signal reversal setups. They're also where the
difference between "price broke a level and came back" (naive) and
"price broke a real HVN with below-average volume then formed a lower
high" (correct) is the most valuable. With 7.9's primitives shipped,
this phase becomes mostly glue.

## Three kinds of "trap"

| Trap | Layer | Detection basis |
|---|---|---|
| Liquidity trap | Macro regime | Fed funds near zero-bound AND M2-velocity stalled AND flat/falling CPI despite stimulus |
| Bull trap | Technical, per-ticker | False breakout above S/R HVN, volume below Xth-percentile, closes back below within N bars, lower-high within M bars |
| Bear trap | Technical, per-ticker | Mirror: false breakdown below S/R HVN, reclaim with rising volume |

## Epics + tasks

### MACRO (3 tasks + gate) — liquidity trap

- **ad710-macro-01** — `tools/trading/ta/macro_liquidity.py`: ingestors
  for Fed funds rate, M2 (velocity derivable), headline CPI. Use yfinance
  or FRED wrapper if one exists; fallback to stubbed constants with a
  loud warning. Read via `macro_data.py` if plumbing is there.
- **ad710-macro-02** — `detect_liquidity_trap(data) → {active, evidence,
  confidence}`. Active if all three conditions hold. Writes to new
  append-only table `ad_macro_regimes` (migration 021). Idempotent daily
  (one row per date max).
- **ad710-macro-03** — Oracle `regime_lens` integration: attach active
  liquidity-trap regime as evidence. Preflight gate: when
  `liquidity_trap.active`, block premium-selling strategies (short
  condors, short flies, credit spreads) unless user explicitly acks via
  new risk-gate override.
- **ad710-macro-gate**.

### TECHNICAL (4 tasks + gate) — bull/bear trap

- **ad710-technical-01** — `tools/trading/ta/traps.py`: bull/bear trap
  detector built on 7.9's `sr.py` + `swings.py`. Thresholds in
  `args/ta_config.yaml::traps` (breakout_volume_ratio 0.7, max_reentry_bars
  3, confirmation_lookback_bars 5).
- **ad710-technical-02** — daemon reflex `trap_scanner` — scans user
  watchlists + positions every 15m during market hours. Writes to
  `ad_trap_events` (append-only, migration 022).
- **ad710-technical-03** — surface traps on `/signals` (new column / tag)
  and `/alerts` as virtual alert subjects `TRAP_BULL` + `TRAP_BEAR` on
  `WATCHLIST_ANY`.
- **ad710-technical-04** — Coach integration — new event type
  `trap_against_position` in coach_engine. Deduped per (position, trap).
  LLM rec explains: what S/R was broken, how volume confirmed, and
  whether to close/hedge/adjust.
- **ad710-technical-gate**.

### INTEGRATION (2 tasks + gate)

- **ad710-integration-01** — Genesis wiring — new Genesis reflex
  `fathomdesk_trap_scenarios.py` that on a high-severity trap event
  auto-spawns a matching scenario (e.g. "potential reversal cascade")
  and writes a daily digest to Pulse.
- **ad710-integration-02** — pytest: handcrafted bar fixtures producing
  known bull/bear trap geometries; assert detector fires with expected
  confidence.
- **ad710-integration-gate**.

### WRAP (3 tasks)

- **ad710-wrap-01** — manifest + coherence + companion.
- **ad710-wrap-02** — feature doc + Selenium E2E.
- **ad710-wrap-03** — backlog + memory.

## Safety boundaries

- **Trap coach never closes / adjusts positions** — emits events + LLM
  recommendation only. User must click.
- **Liquidity-trap preflight block is bypassable** with an explicit user
  override so institutional operators can take their own view.
- **Genesis scenario auto-spawn is gated** by a severity threshold
  (configurable; default 'critical') to prevent noise.
