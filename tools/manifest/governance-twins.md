# Governance Twins (icx-cnf-03)

> Shard of `tools/manifest.md`. See index at `tools/manifest.md`.

## Observe, record, replay, thread

| Tool | File | Description | Input | Output |
|------|------|-------------|-------|--------|
| Governance Twins | tools/twin_core/governance.py | Governance twins for Cortex (TRUST-gate outcomes, `cortex_audit` health, conflicts awaiting a person, backend liveness), ACE (stuck/failed runs, checkpoints waiting on a person, aborts, NOVA trust drift), the Co-Workers roster sources and the feature-conformance audit. Each observation's evidence and verdict digest is recorded in the append-only `twin_governance_observations`; `--replay` re-judges it and exits 1 when it no longer matches. Every finding carries a `thread_id`; `--thread` follows it to its record and provenance. An unreadable source is unverified, never a pass. Thresholds: `args/twin_governance.yaml` | --observe [--canvas] [--no-record], --list, --replay ID, --thread ID, --json | JSON |
| Governance twin adapters | tools/twin_core/adapters/{cortex,ace,cwk,conformance}.py | `@register_twin` adapters (canvas keys `cortex`, `ace`, `cwk`, `twin_observatory`); `collect()` reads, `judge()` is pure, `replay()` re-judges a recorded observation | (library) | canonical twin envelope |
