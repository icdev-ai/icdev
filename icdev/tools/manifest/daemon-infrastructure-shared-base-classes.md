# Daemon Infrastructure — Shared Base Classes

> Shard of `tools/manifest.md`. See index at `tools/manifest.md`.

## Daemon Infrastructure — Shared Base Classes

| Tool | Path | Purpose |
|------|------|---------|
| DaemonBase | `tools/daemon/base.py` | ABC for all ICDEV™ daemons: signal handling, config loading, main loop, schedule parsing, circuit breaker, audit logging, CLI (--once, --status, --reflex, --enable, --disable, --reset, --json) |
| ReflexStateBase | `tools/daemon/base.py` | Thread-safe DB-backed reflex state management parameterized by `state_table` class attribute |
| TrustKernelBase | `tools/daemon/base.py` | Risk tier enforcement (GREEN=auto, YELLOW=sandbox, ORANGE=human review) |
| circuit_probe_due | `tools/daemon/base.py` | Decide whether an OPEN reflex breaker has cooled down enough for one half-open probe run. Exponential backoff in failures past the trip threshold, capped at `max_cooldown_minutes`. Library function, no CLI |

### Circuit breaker semantics (xbm-wake-01)

`circuit_probe_due(state, cb_config)` reads `trust_kernel.circuit_breaker` from the
daemon config:

- `auto_reenable: false` — hard latch. A tripped breaker blocks the reflex until a
  human runs `daemon.py --reset <name>`.
- `auto_reenable: true` (the default, and genesis's posture) + `cooldown_minutes: N`
  — **half-open probe**. Once N minutes have elapsed the next cycle admits one run;
  `record_success()` closes the breaker, a failed probe re-trips it and doubles the
  wait, capped at `max_cooldown_minutes`.

Failure labelling: `run_reflex` only records `metric_threshold_not_met` when the
reflex actually SUCCEEDED and merely missed its threshold. A reflex that reports
failure without an explicit `details['error']` is recorded as
`reflex_reported_failure` — never as a threshold miss.

Background: [docs/ops/genesis-reflex-dormancy.md](../../docs/ops/genesis-reflex-dormancy.md).

