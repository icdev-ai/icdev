# Daemon Infrastructure — Shared Base Classes

> Shard of `tools/manifest.md`. See index at `tools/manifest.md`.

## Daemon Infrastructure — Shared Base Classes

| Tool | Path | Purpose |
|------|------|---------|
| DaemonBase | `tools/daemon/base.py` | ABC for all ICDEV™ daemons: signal handling, config loading, main loop, schedule parsing, circuit breaker, audit logging, CLI (--once, --status, --reflex, --enable, --disable, --reset, --json) |
| ReflexStateBase | `tools/daemon/base.py` | Thread-safe DB-backed reflex state management parameterized by `state_table` class attribute |
| TrustKernelBase | `tools/daemon/base.py` | Risk tier enforcement (GREEN=auto, YELLOW=sandbox, ORANGE=human review) |

