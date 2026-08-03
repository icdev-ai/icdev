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
| Observable Dispatch | tools/analyzers/dispatch.py | One entry point for observables (`anz-disp-01`). Fans an observable out to every analyzer that declared it accepts that type, concurrently over a shared bounded pool with a per-analyzer timeout, and returns taxonomy-tagged reports. Every matched declaration produces a report carrying a status (`ok`/`timeout`/`error`/`unavailable`/`misdeclared`/`skipped`) — a slow analyzer is reported as timed-out, never omitted. Responders are opt-in. | --type TYPE --value VALUE [--context JSON] [--analyzer KEY] [--responders] [--timeout N] [--json], --observables | `DispatchResult` (reports + `partial` + `partial_reasons` + `excluded`); exit 2 when partial |

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
`anz-con-01` shipped the declaration and its guard; `anz-disp-01` adds the
dispatch path, argument binding and the MCP surface. Porting existing analyzers
(`anz-mig-*`) and rate-limit / sandbox enforcement via the existing
`sandbox_execute` MCP tool (`anz-rate-*`) remain separate tasks — dispatch reads
`rate_limit` and `sandbox` from the declaration but does not yet enforce them.

## Related
- `args/analyzer_contract.yaml` — the contract data (single source; the copy at
  `icdev/data/args/` is the packaged one, gated for drift by
  `tests/test_analyzer_contract.py`)
- `tools/cortex/search_service.py::_run_backends` — the fan-out shape reused here
- `tools/provenance/citation_types.py` — the closed-vocabulary precedent
- `docs/security/sandbox-coverage.md` — same four sandbox postures
