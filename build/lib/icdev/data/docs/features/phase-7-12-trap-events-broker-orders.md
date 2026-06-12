# Phase 7.12 — Trap Events & Broker Orders

**Status:** Shipped  
**Date:** 2026-04-25  
**Classification:** CUI // SP-CTI

---

## Summary

Phase 7.12 delivers the `ad_trap_events` write path and coach integration for FathomDesk's bull/bear trap detection subsystem. The phase closes the gap between trap detection signals (emitted by the pattern analyzer) and downstream broker order gating + coach event persistence.

---

## Features Shipped

### 1. Trap Event Persistence (`ad_trap_events`)

- Trap detector (`tools/alphadesk/ta_traps.py`) now writes detected bull/bear trap signals to `ad_trap_events` via `get_connection()`.
- Table is append-only (immutable audit trail per NIST AU).
- Schema: `id`, `ticker`, `pattern` (`bull_trap` | `bear_trap`), `broken_level`, `reentry_bar`, `lower_high_bar`, `confidence`, `detected_at`.

### 2. Coach Event Integration (`scan_traps_against_positions`)

- `scan_traps_against_positions()` correlates live trap events against open positions.
- Fires coach events to `ad_options_coach_events` when a trap threatens a user's open position (LONG on bull trap, SHORT on bear trap).
- Deduplicates: one coach event per `(position_id, trap_event_id)` pair.
- Summary format: `"{TICKER} {human-readable pattern} detected at level {price} — {direction} position at risk (confidence {pct}%)"`.

### 3. Liquidity Trap Preflight Gate

- `check_liquidity_trap_preflight()` blocks short-premium strategies (short condor, iron condor, naked put/call) when macro liquidity trap regime is active.
- Regime condition: `fed_funds_rate < 1%`, `m2_velocity < 2.0`, `cpi < 2.5%`.
- Hard block (not a warning). Bypassed with `bypass_liquidity_check=True` or `ICDEV_BYPASS_LIQUIDITY_CHECK=true` env var (test/audit use only).

---

## Test Coverage

### Unit Tests — `tests/test_ta_traps.py` (30 tests)
  - `TestBullTrapDetection` — pattern detection, level extraction, reentry timing, lower-high presence
  - `TestBearTrapDetection` — symmetric bear-side coverage
  - `TestCleanBreakout` — true breakouts don't trigger false positives
  - `TestLiquidityTrapPreflight` — active/inactive/partial regime detection, strategy blocking, bypass paths
  - `TestCoachEventOnTrap` — insert, summary content, recommendation presence, dedup, ticker/direction filtering

### E2E Integration Tests — `tests/e2e_fathomdesk_trap.py` (23 tests)
  - `TestTrapEventWritePath` — direct `_insert_trap_event` DB write path; 5 scenarios
  - `TestTrapSweepReflex` — Genesis reflex signal-flip detection, dedup, cooldown; 6 scenarios
  - `TestBrokerAdapterPayload` — Alpaca limit/stop payload construction, paper detection, missing-key guard; 5 scenarios
  - `TestTrapsAPIRoute` — Flask `/fathomdesk/api/traps` filtering, limit, empty result, field presence; 7 scenarios

---

## V&V Gate Results (2026-04-25)

| Gate | Result |
|------|--------|
| Coherence checker (`--all --fix --gate`) | ✅ 17/17 checks pass |
| Unit tests (`tests/test_ta_traps.py`) | ✅ 30/30 passed |
| E2E tests (`tests/e2e_fathomdesk_trap.py`) | ✅ 23/23 passed |
| Companion sync | ✅ 10 platforms, 63 skills synced |

---

## Architecture Notes

- All DB writes use `get_connection()` — PostgreSQL-compatible via `storage.py` translation layer.
- `ad_trap_events` registered in `APPEND_ONLY_TABLES` (pre_tool_use hook).
- Trap detection runs as a Genesis reflex on 4h cooldown to prevent signal flooding.
- Coach events are read by the FathomDesk Coach canvas (`/coach`) for display.
