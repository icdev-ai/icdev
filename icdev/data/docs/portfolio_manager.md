# Portfolio Manager — Base+Option Periods & Obligation Tracking

**CUI // SP-CTI**

Reference for `tools/govcon/portfolio_manager.py` and `tools/govcon/contract_periods_manager.py`
(Phase 60 CPMP, D-CPMP-8/9/10). See [phase-60-cpmp.md](features/phase-60-cpmp.md) for the full
CPMP architecture.

---

## 1. Base + Option Period Structure

A contract's period of performance (PoP) is broken into a **base period** plus up to five
**option periods**, tracked one row per period in `cpmp_contract_periods`
(migration `173_cpmp_obligation_periods.py`).

**`period_type`** — one of:

| Value | Meaning |
|-------|---------|
| `base` | The initial period of performance. Always created first; a contract has exactly one. |
| `option_1` … `option_5` | Option years, in order. `option_number` (0–5) is derived automatically from `period_type`. |

**`status`** — one of:

| Value | Meaning |
|-------|---------|
| `active` | Assigned automatically to the `base` period on creation. |
| `unexercised` | Default status for a newly created option period — not yet obligated. |
| `exercised` | Set when the government exercises the option via `exercise_option()`; obligated_value becomes authoritative and the contract rolls up its totals. |
| `expired` | Option period whose window has passed without being exercised. |

Rules enforced by `contract_periods_manager.py`:

- Only one row per `(contract_id, period_type)` — creating a duplicate period_type for the same contract is rejected.
- Creating a `base` period also backfills the parent contract's `period_type`, `option_number`, `pop_start`, and `pop_end` (`pop_start`/`pop_end` only if not already set).
- `exercise_option()` rejects: exercising the `base` period, and re-exercising a period already `exercised`.
- When an option is exercised, the contract's `period_type`/`option_number` are updated to reflect the newly active period, and `cpmp_contracts.obligated_value` is recomputed as the sum of `obligated_value` across all periods with status `active` or `exercised` (i.e., base + every exercised option to date).

### `cpmp_contracts` columns added for period tracking

| Column | Type | Meaning |
|--------|------|---------|
| `period_type` | TEXT | Current period of the contract (`base`, `option_1`, …); defaults to `base`. |
| `option_number` | INTEGER | 0 for base, 1–5 for the corresponding option year. |
| `obligated_value` | REAL | Roll-up of obligated value across active/exercised periods (see above). Falls back to `funded_value` where no periods exist yet. |

### `cpmp_contract_periods` columns

| Column | Meaning |
|--------|---------|
| `period_type`, `option_number` | See above. |
| `pop_start`, `pop_end` | Period-of-performance dates for this specific period. |
| `obligated_value` | Amount formally obligated for this period. |
| `funded_value` | Amount funded (may lag `obligated_value` for incrementally funded contracts). |
| `ceiling_value` | Not-to-exceed ceiling for the period (relevant for IDIQ/T&M option years priced later). |
| `status` | `active` \| `unexercised` \| `exercised` \| `expired`. |
| `exercised_at`, `exercised_by` | Set only when the option is exercised. |

---

## 2. Funding & Obligation Reporting Logic

### Per-contract obligation summary — `get_obligation_summary(contract_id)`

Computed fields (also rendered on `/cpmp/<id>`, Overview panel and Periods tab):

| Field | Formula |
|-------|---------|
| `total_obligated` | Sum of `obligated_value` across periods with status `active` or `exercised`. If no periods exist yet, falls back to `cpmp_contracts.obligated_value`, then `funded_value`. |
| `total_billed` | `SUM(cpmp_clins.billed_value)` for the contract. |
| `remaining_obligation` | `total_obligated - total_billed`. |
| `burn_rate_pct` | `total_billed / total_obligated * 100` (0 if `total_obligated` is 0). |
| `by_period` | Per-period breakdown: `period_type`, `option_number`, `pop_start`, `pop_end`, `obligated_value`, `funded_value`, `ceiling_value`, `status`. |

`list_periods(contract_id)` additionally annotates each period row with:

- `remaining_obligation` = `obligated_value - total_billed` (same billed figure used contract-wide, not split per period)
- `burn_rate_pct` = `total_billed / obligated_value * 100` (0 if the period isn't obligated yet)

### Portfolio-level roll-up — `get_portfolio_summary()`

Aggregates across all `active`/`option_pending` contracts:

| Field | Formula |
|-------|---------|
| `obligated_value` | `SUM(cpmp_contracts.obligated_value)`, falling back to `SUM(funded_value)` when obligated totals are unset. |
| `billed_value` | `SUM(cpmp_clins.billed_value)` joined through active/option_pending contracts. |
| `remaining_obligation` | `obligated_value - billed_value`. |
| `burn_rate_pct` | `billed_value / max(obligated_value, 1) * 100`. |

These four fields appear in the `/cpmp` portfolio stat grid alongside `total_value`, `funded_value`, contract counts, and health distribution.

### Effect on contract health scoring

`compute_contract_health()` (D-CPMP-8) weights a **funding** dimension (default weight `0.10`,
configurable in `args/govcon_config.yaml` under `cpmp.health_weights`):

```
funding_score = (funded_value / total_value) * 0.6
              + max(0, 1 - billed_value / max(funded_value, 1)) * 0.4
```

A contract that is well-funded relative to `total_value` and not over-burned relative to
`funded_value` scores near 1.0; heavy billing against a small funded amount drags the score
down, which in turn can move the contract's overall `health` from green → yellow/red and
surface a "Review contract funding status and obligation rate" recommendation.

Burn-rate color coding used throughout the CPMP dashboard (`Obligation & Burn Rate` panel and
the Periods tab gauges):

| Burn Rate | Color |
|-----------|-------|
| `>= 90%` | Red |
| `>= 70%` | Yellow |
| `< 70%` | Green |

This is a display convention only — it does not feed back into `compute_contract_health()`.

---

## 3. API Endpoints

| Method | Route | Purpose |
|--------|-------|---------|
| `GET` | `/api/cpmp/contracts/<id>/periods` | List all periods for a contract (base + options), each with computed `remaining_obligation`/`burn_rate_pct`. |
| `POST` | `/api/cpmp/contracts/<id>/periods` | Create a period (`period_type` required; `pop_start`, `pop_end`, `obligated_value`, `funded_value`, `ceiling_value`, `notes` optional). Requires role `admin`, `co`, or `contract_mgr`. |
| `PUT` | `/api/cpmp/periods/<id>/exercise` | Exercise an option period (`obligated_value` required). Rolls up `cpmp_contracts.obligated_value`. Requires role `admin`, `co`, or `contract_mgr`. |
| `GET` | `/api/cpmp/contracts/<id>/obligation-summary` | Burn-rate vs. obligation summary for a single contract (see fields above). |
| `GET` | `/api/cpmp/portfolio` | Portfolio-wide summary, including the aggregate obligation fields described above. |

---

## 4. Dashboard

`/cpmp/<id>` (contract detail) renders `periods` and `obligation_summary` via
`tools/dashboard/app.py::cpmp_detail_page`, which calls `list_periods()` and
`get_obligation_summary()` from `contract_periods_manager.py`:

- **Overview tab** — "Obligation & Burn Rate" card: total obligated, billed, remaining
  obligation, burn rate, a funding gauge, and the contract's current period label
  (e.g. "Option 1").
- **Periods tab** — obligation summary stat grid plus a full base/option periods table
  (period, POP dates, obligated/funded/ceiling values, remaining obligation, burn rate,
  status) and a per-period utilization gauge.

---

## 5. CLI

```bash
python tools/govcon/contract_periods_manager.py --list --contract-id <id> --json
python tools/govcon/contract_periods_manager.py --summary --contract-id <id> --json
python tools/govcon/contract_periods_manager.py --create --contract-id <id> --period-type base \
    --obligated-value 2000000 --pop-start 2026-01-01 --pop-end 2026-12-31 --json
python tools/govcon/contract_periods_manager.py --exercise --period-id <period_id> \
    --obligated-value 1500000 --exercised-by <user> --json
```
