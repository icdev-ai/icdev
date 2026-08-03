# Analyzer / Responder Contract (ANZ)

> Shard of `tools/manifest.md`. See index at `tools/manifest.md`.

The contract ICDEV's ~79 analyzer-shaped modules never had: each analyzer
declares the observable types it accepts, the taxonomy it emits, its rate limit
and its sandbox posture — **as data**, in `args/analyzer_contract.yaml`.
Adapted from TheHive Project's Cortex, whose value is the contract rather than
the 39 analyzers implementing it.

Deliberately **not** a base class. Declaring an analyzer changes no analyzer
code; the precedent is `args/component_registry.yaml` and
`args/mirror_parity.yaml`, where adding an entry needs no code change.

## Tools
| Tool | File | Description | Input | Output |
|------|------|-------------|-------|--------|
| Analyzer Contract | tools/analyzers/contract.py | Loads and validates `args/analyzer_contract.yaml`. Closed vocabularies (observable types, taxonomy namespaces/levels, sandbox postures) are enforced **at load** — an unknown observable type raises `UnknownObservableType` naming the offending analyzer and the legal values, never a swallowed warn at dispatch time. Renders the SQL CHECK clause for any column storing an observable type. | --validate, --json, --list, --observable TYPE, --check-sql [COLUMN], --path FILE | Validation verdict / machine-readable contract / CHECK clause |
| Observable Dispatch | tools/analyzers/dispatch.py | One entry point for observables (`anz-disp-01`). Fans an observable out to every analyzer that declared it accepts that type, concurrently over a shared bounded pool with a per-analyzer timeout, and returns taxonomy-tagged reports. Every matched declaration produces a report carrying a status (`ok`/`timeout`/`error`/`unavailable`/`misdeclared`/`skipped`/`rate_limited`/`sandbox_unavailable`) — a slow or throttled analyzer is reported, never omitted. Responders are opt-in. | --type TYPE --value VALUE [--context JSON] [--analyzer KEY] [--responders] [--timeout N] [--rate-limit-wait SECONDS] [--strict-sandbox] [--json], --observables | `DispatchResult` (reports + `partial` + `partial_reasons` + `excluded`); exit 2 when partial |
| Analyzer Rate Limit | tools/analyzers/rate_limit.py | Enforces each declaration's `rate_limit` (`anz-rate-01`). Sliding window keyed by analyzer, so a fixed-window boundary cannot spend two quotas in two seconds against a metered API. Exceeding a limit **queues** (bounded by `max_wait_seconds`) or is **reported** with `retry_after_seconds` — never dropped. Library; no CLI. | `get_limiter().acquire(key, max_calls, per_seconds, max_wait_seconds=…)` | `RateLimitDecision` (`allowed`, `remaining`, `retry_after_seconds`, `waited_seconds`) |
| Analyzer Sandbox Gate | tools/analyzers/sandbox.py | Turns each declaration's `sandbox:` posture into an execution mode and runs sandboxed analyzers through the platform `SandboxExecutor` behind the `sandbox_execute` MCP tool (D-SEC-10) — **no second isolation path**. Unknown posture and unavailable sandbox both fail closed; never downgraded to in-process. Library; no CLI. | `resolve_execution_mode(decl)`, `run_sandboxed(decl, kwargs, …)` | `'in_process'` / `'sandboxed'`; the entrypoint's return value |

## Adding an analyzer
1. append an entry under `analyzers:` in `args/analyzer_contract.yaml`
2. every value in `accepts:` must already exist under `observable_types:`
   (each observable type names the module that consumes it — a type with no
   consumer is dead vocabulary)
3. add a `binding:` block if the entrypoint's *first* parameter is not the
   observable (see below)
4. `python tools/analyzers/contract.py --validate`

There is no step 5: no blueprint edit, no dispatch table, no base class. The
new analyzer is dispatchable — and exposed over MCP through `analyzer_dispatch`
/ `analyzer_capabilities` — with no code change anywhere.

## Argument binding
The declared entrypoints share no signature, so the call is declared in data:

```yaml
binding:
  observable_arg: cve_id                    # `param.key` nests it in a dict
  context_args: {project_id: project_id}    # parameter -> dispatch-context key
  static_args:  {note: from-contract}       # constants
```

Omit the block when "first parameter" is already right. Declare it when it is
not: `trigger_rtbh(conn, prefix, ...)` opens with a live DB connection, and a
dispatcher that passed an IP address there would fail *inside* the analyzer —
indistinguishable from a clean run that found nothing. A parameter with no
source and no default is reported `misdeclared` **by name**; a context key the
caller omitted is reported `skipped` **by name**. Neither is ever guessed.

## Dispatch invariants
- **Nothing is dropped silently.** Every matched declaration yields a report;
  declarations held back before the fan-out (disabled, or a responder when only
  analyzers were asked for) are listed under `excluded` with a reason.
- **Partial is labelled.** `DispatchResult.partial` is true whenever any report
  is not `ok`, and `partial_reasons` names the offenders by status.
- **Responders do not run by default.** `kind: responder` acts (RTBH blackholes
  a prefix); submitting an IP for analysis must not trigger it.
- **The fan-out reuses the proven executor shape** from
  `tools/cortex/search_service.py::_run_backends`: a process-wide bounded pool
  that is never shut down per call, each future budgeted from a shared start,
  and a timed-out future abandoned rather than joined.
- **Taxonomy tags are validated, not trusted.** The namespace is stamped from
  the declaration; a predicate or level outside what the analyzer declared is
  dropped into `taxonomy_defects` and makes the result partial.
- **A rate limit queues or reports — it never drops.** An exhausted window
  yields a `rate_limited` report carrying `retry_after_seconds` (and
  `--rate-limit-wait` queues for a slot instead), so a throttled analyzer is
  never indistinguishable from one that ran and found nothing.
- **Quota is spent only by a call that happens.** The limiter is consulted
  *after* import, binding and posture resolution, so an analyzer reported
  `misdeclared`, `unavailable` or `sandbox_unavailable` burns none of the
  external API quota it never reached. The queue is additionally capped by
  what remains of the analyzer's own timeout budget — otherwise a call could
  acquire its slot after the fan-out had already abandoned it.
- **Sandbox posture is enforced, not merely declared.** `sandboxed` always
  routes through `SandboxExecutor`; `sandboxed_on_demand` is promoted by
  `ICDEV_STRICT_SANDBOX=1`; an unknown posture and an unavailable sandbox both
  fail closed as `sandbox_unavailable` rather than degrading to in-process.

## Why the vocabulary is closed
`register_citation` raised `ValueError` on an unknown `citation_type`, every
caller swallowed it, and two subsystems wrote zero provenance rows for their
entire existence while the gate reported `warn` (see
`tools/provenance/citation_types.py`). The same failure mode is why validation
here runs when the file is parsed, not when an observable is dispatched, and why
`load_contract()` raises rather than degrading to an empty contract.

## MCP surface
Exposed through the **existing** unified gateway as declarative metadata
(`module` + `handler` strings in `tools/mcp/tool_registry.py`, category
`analyzers`) — not as a new MCP server:

| Tool | Handler | Purpose |
|------|---------|---------|
| `analyzer_dispatch` | `tools.mcp.gap_handlers.handle_analyzer_dispatch` | Submit an observable, get every accepting analyzer's report |
| `analyzer_capabilities` | `tools.mcp.gap_handlers.handle_analyzer_capabilities` | The observable vocabulary and which analyzers accept each type |

Both read the contract at call time, so a newly declared analyzer is reachable
over MCP without touching the registry.

## Scope
`anz-con-01` shipped the declaration and its guard; `anz-disp-01` added the
dispatch path, argument binding and the MCP surface; `anz-rate-01` makes the
declared `rate_limit` and `sandbox` posture **enforced** rather than advisory,
and records one sandbox-coverage decision per ported analyzer (OPT-58, Gap 48).
Porting further analyzers (`anz-mig-*`) remains a separate task.

**Deployment prerequisite for `sandboxed` analyzers.** The sandbox driver runs
`importlib.import_module(<declared module>)` inside the container, so the image
must have ICDEV importable. The stock `python:3.12-slim` in
`args/sandbox_config.yaml` does not; an operator declaring an analyzer
`sandboxed` must point `sandbox.images.python` at an image carrying the
platform. It fails loudly rather than silently running untrusted content
in-process — that is the intended trade.

## Related
- `args/analyzer_contract.yaml` — the contract data (single source; the copy at
  `icdev/data/args/` is the packaged one, gated for drift by
  `tests/test_analyzer_contract.py`)
- `tools/cortex/search_service.py::_run_backends` — the fan-out shape reused here
- `tools/provenance/citation_types.py` — the closed-vocabulary precedent
- `docs/security/sandbox-coverage.md` — same four sandbox postures
