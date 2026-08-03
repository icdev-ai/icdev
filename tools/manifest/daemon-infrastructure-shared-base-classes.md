# Daemon Infrastructure — Shared Base Classes

> Shard of `tools/manifest.md`. See index at `tools/manifest.md`.

## Daemon Infrastructure — Shared Base Classes

| Tool | Path | Purpose |
|------|------|---------|
| DaemonBase | `tools/daemon/base.py` | ABC for all ICDEV™ daemons: signal handling, config loading, main loop, schedule parsing, circuit breaker, audit logging, CLI (--once, --status, --reflex, --enable, --disable, --reset, --json) |
| ReflexStateBase | `tools/daemon/base.py` | Thread-safe DB-backed reflex state management parameterized by `state_table` class attribute |
| TrustKernelBase | `tools/daemon/base.py` | Risk tier enforcement (GREEN=auto, YELLOW=sandbox, ORANGE=human review) |
| classify_failure | `tools/daemon/base.py` | Label a failed reflex run: explicit `details['error']` → `reflex_reported_failure` → `metric_threshold_not_met` (with the comparison that failed). Library function, no CLI |

### Circuit breaker semantics (xbm-wake-01)

`ReflexStateBase.is_circuit_open(cb_config)` reads
`trust_kernel.circuit_breaker` from the daemon config:

- `auto_reenable: false` — hard latch. A tripped breaker blocks the reflex until a
  human runs `--reset <name>`. This is `proposal_genesis`'s posture.
- `auto_reenable: true` + `cooldown_minutes: N` — **half-open probe**. After N minutes
  the next scheduled run is allowed through; `record_success()` closes the breaker, a
  failed probe re-trips it and restarts the window. This is `genesis`'s posture.

A reflex skipped for an open breaker is reported via `_warn_reflex_dormant()` (stdout
warning + `<prefix>.reflex.dormant` audit event, throttled hourly per reflex) — it is
never skipped silently. Background: [docs/features/xbm-wake-01-scout-dormancy-rca.md](../../docs/features/xbm-wake-01-scout-dormancy-rca.md).

