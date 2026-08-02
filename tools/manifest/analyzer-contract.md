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

## Adding an analyzer
1. append an entry under `analyzers:` in `args/analyzer_contract.yaml`
2. every value in `accepts:` must already exist under `observable_types:`
   (each observable type names the module that consumes it — a type with no
   consumer is dead vocabulary)
3. `python tools/analyzers/contract.py --validate`

There is no step 4: no blueprint edit, no dispatch table, no base class.

## Why the vocabulary is closed
`register_citation` raised `ValueError` on an unknown `citation_type`, every
caller swallowed it, and two subsystems wrote zero provenance rows for their
entire existence while the gate reported `warn` (see
`tools/provenance/citation_types.py`). The same failure mode is why validation
here runs when the file is parsed, not when an observable is dispatched, and why
`load_contract()` raises rather than degrading to an empty contract.

## Scope
`anz-con-01` ships the declaration and its guard only. Dispatch
(`anz-disp-*`), porting existing analyzers (`anz-mig-*`) and rate-limit /
sandbox enforcement via the existing `sandbox_execute` MCP tool (`anz-rate-*`)
are separate tasks. `module`/`entrypoint` name the callable that already
implements the analysis; argument adaptation is `anz-disp-01`'s job.

## Related
- `args/analyzer_contract.yaml` — the contract data (single source; the copy at
  `icdev/data/args/` is the packaged one, gated for drift by
  `tests/test_analyzer_contract.py`)
- `tools/provenance/citation_types.py` — the closed-vocabulary precedent
- `docs/security/sandbox-coverage.md` — same four sandbox postures
