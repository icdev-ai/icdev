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
| Analyzer Binding (anz-mig-01) | tools/analyzers/binding.py | Hands a declared analyzer an observable without touching the analyzer: resolves `module:entrypoint`, assembles keyword arguments from the declaration's `input_binding`, manages the declared connection (open / commit only if declared / close), and returns the callable's result **untouched** — no envelope, no coercion, no swallowed exception. `--verify` checks every binding against the real signature of its callable, so a binding naming a parameter the callable does not have fails there rather than at dispatch. | --verify, --describe KEY, --json | Verification report / declaration + live signature |
| Analyzer Parity (anz-mig-01) | tools/analyzers/parity.py | Proves a port changed nothing, against the fixed input set in `args/analyzer_parity_cases.yaml`. Checks input adaptation (assembled kwargs vs the hand-written call, side-effect free) and, for cases declared live-safe, output transparency (both calls executed in one process against one DB, outcomes diffed). Cases not executed live are reported with their reason rather than dropped. | --live, --analyzer KEY, --cases FILE, --json | Per-case parity report |

## Adding an analyzer
1. append an entry under `analyzers:` in `args/analyzer_contract.yaml`
2. every value in `accepts:` must already exist under `observable_types:`
   (each observable type names the module that consumes it — a type with no
   consumer is dead vocabulary)
3. `python tools/analyzers/contract.py --validate`

There is no step 4: no blueprint edit, no dispatch table, no base class.

## Making it dispatchable (`input_binding`)
Steps 1–3 make an analyzer *described*. To make it *runnable*, add an
`input_binding` block naming the parameter the observable lands in, the DB-handle
factory if the callable takes one, and the remaining parameters the caller
supplies. Then:

4. `python tools/analyzers/binding.py --verify` — binding vs the real signature
5. add a case to `args/analyzer_parity_cases.yaml` and
   `python tools/analyzers/parity.py --live` — behaviour diff

Still no adapter function, and still no analyzer code changed.
`tests/test_analyzer_binding_parity.py` fails if a bound analyzer has no parity
case, so "bound" cannot quietly mean "unproven".

## Why the vocabulary is closed
`register_citation` raised `ValueError` on an unknown `citation_type`, every
caller swallowed it, and two subsystems wrote zero provenance rows for their
entire existence while the gate reported `warn` (see
`tools/provenance/citation_types.py`). The same failure mode is why validation
here runs when the file is parsed, not when an observable is dispatched, and why
`load_contract()` raises rather than degrading to an empty contract.

## Scope
`anz-con-01` shipped the declaration and its guard. `anz-mig-01` added
`input_binding` plus the binding and parity layers, and ported the DSOC
(`rtbh_blackhole`, `bgp_prefix_hijack`, `bgp_route_leak`) and PVM
(`pvm_risk_prediction`, `pvm_triage_scoring`, `pvm_attack_surface`) families
without modifying a single analyzer module. Taxonomy tagging and the REST
surface (`anz-disp-*`) sit downstream of `binding.invoke()`; rate-limit and
sandbox enforcement via the existing `sandbox_execute` MCP tool (`anz-rate-*`)
sit upstream. Both are separate tasks — `invoke()` deliberately returns the raw
result, because wrapping it would have made the port a behavioural change.

**Not everything fits, by design.** Canvas aggregators (`get_dsoc_overview`,
`get_active_hijacks`) accept no observable; the strategos feed importers are
`source → N records`, not `observable → report`; the passive CVE watcher and
`darkweb_monitor` are sweeps and daemons. Reasons per family are in
[docs/features/anz-mig-01-analyzer-contract-migration.md](../../docs/features/anz-mig-01-analyzer-contract-migration.md).

## Related
- `args/analyzer_contract.yaml` — the contract data (single source; the copy at
  `icdev/data/args/` is the packaged one, gated for drift by
  `tests/test_analyzer_contract.py`)
- `args/analyzer_parity_cases.yaml` — the fixed input set the ports are proved
  against
- `tools/provenance/citation_types.py` — the closed-vocabulary precedent
- `docs/security/sandbox-coverage.md` — same four sandbox postures
