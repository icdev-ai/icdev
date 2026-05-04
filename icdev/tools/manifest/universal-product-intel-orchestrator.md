# Universal Product Intelligence Orchestrator

## Tools

### orchestrator.py
- **Path:** `tools/product_intel/orchestrator.py`
- **Class:** `ProductIntelOrchestrator`
- **Purpose:** Universal orchestrator that invokes all 8 product-intelligence sub-engines in registration order, captures per-engine results (status, duration_ms, signals_count), and persists one consolidated run record to `product_intel_runs`. Never raises — engine failures are captured in `engines_failed`.
- **CLI flags:** `--dry-run` (skip DB write, return consolidated dict only)
- **DB tables written:** `product_intel_runs`
- **Key method:** `run_all(dry_run=False) -> dict` — returns `{run_id, status, started_at, completed_at, engines_run, engines_failed, total_signals, results[]}`

### engine_registry.py
- **Path:** `tools/product_intel/engine_registry.py`
- **Class:** `EngineRegistry`
- **Purpose:** Loads engine definitions from `args/product_intel_config.yaml`, sorts by `order`, and invokes each via `subprocess` with the configured CLI flags. Returns `EngineResult(name, status, duration_ms, signals_count, output)`.
- **Config:** `args/product_intel_config.yaml` — `engines[]` list with `name`, `module`, `cli_flags`, `enabled`, `timeout_seconds`, `order`
- **Key methods:**
  - `list_engines() -> list[EngineConfig]` — returns all engines sorted by order
  - `get_engine(name) -> EngineConfig` — raises `KeyError` if not found
  - `invoke(engine_config, extra_args?) -> EngineResult` — subprocess call; non-zero exit or non-JSON stdout → `status="failed"`

## 8 Registered Engines (execution order)

| Order | Name | Module |
|-------|------|--------|
| 1 | `innovation` | `tools.innovation.engine` |
| 2 | `regulatory_foresight` | `tools.regfore.foresight_engine` |
| 3 | `creative` | `tools.creative.engine` |
| 4 | `usage` | `tools.usage.engine` |
| 5 | `win_loss` | `tools.win_loss.engine` |
| 6 | `voc` | `tools.voc.engine` |
| 7 | `tech_radar` | `tools.tech_radar.engine` |
| 8 | `research` | `tools.research.engine` |

## DB Tables

| Table | Migration | Purpose |
|-------|-----------|---------|
| `product_intel_runs` | 071 | One row per orchestrator run — stores run_id, started/completed timestamps, engines_run/failed (JSON arrays), total_signals, result_json, status |
| `regulatory_foresight_signals` | 066 | Signals from the regulatory_foresight engine |
| `usage_events` | 067 | Usage signals from the usage engine |
| `voc_signals` | 069 | Voice-of-customer signals from the voc engine |
| `tech_radar_entries` | 070 | Technology radar entries from the tech_radar engine |

## Federation Router Integration

After all engines complete, the orchestrator optionally triggers federation via `args/product_intel_config.yaml`:
- `federation.run_after_engines: true` — federation runs post-engine sweep
- `federation.min_signal_score: 0.70` — signals below this threshold are filtered before federation routing

## Scheduling

- Default cadence: every 24 hours (`scheduling.run_interval_hours: 24`)
- Quiet hours: 22:00–06:00 UTC suppresses automatic runs

## Classification

CUI // SP-CTI
