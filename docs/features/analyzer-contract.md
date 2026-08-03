# CUI // SP-CTI

# Analyzer / Responder Contract (ANZ) — anz-con-01

## Problem

ICDEV has ~79 analyzer-shaped modules across `tools/strategos/`,
`tools/security/`, `tools/supply_chain/` and `tools/dsoc_canvas/` and **no
shared base class among them** — measured 2026-08-02, the only `*Analyzer`
classes in the tree are `tools/analysis/code_analyzer.py`,
`tools/trading/news/pattern_analyzer.py` and `tools/win_loss/pattern_analyzer.py`,
three unrelated domains. Every feed, importer, scorer and triage path is
hand-wired: adding a source means touching a blueprint, and the outputs share no
vocabulary.

TheHive Project's Cortex (github.com/TheHive-Project/Cortex) is worth copying
for its **contract**, not its analyzers: each declares the observable types it
accepts, runs containerized under a per-analyzer rate limit, and emits a
taxonomy-tagged report. One API, N plugins, no bespoke wiring.

## What shipped

The contract, declared as **data**:

| Artifact | Role |
|---|---|
| `args/analyzer_contract.yaml` | The contract. Closed observable vocabulary, closed taxonomy levels/namespaces, closed sandbox postures, defaults, and the analyzer/responder declarations. |
| `tools/analyzers/contract.py` | Loader + validator. Raises on any defect; renders the SQL CHECK clause from the vocabulary. |
| `tests/test_analyzer_contract.py` | 26 tests, including the negative cases that hold the load-time guard. |
| `tools/manifest/analyzer-contract.md` | Manifest shard. |

### Deliberately not a base class

79 modules will not all be refactored, and a contract that requires rewriting a
module to adopt does not get adopted. Declaring an analyzer changes no analyzer
code. The precedent is `args/component_registry.yaml` and
`args/mirror_parity.yaml`: adding an entry needs no code change.

### Declaring an analyzer

```yaml
  - key: kev_check
    kind: analyzer                       # analyzer | responder
    display_name: CISA KEV Check
    module: tools.strategos.cisa_kev_importer
    entrypoint: run
    accepts: [cve]                       # every value must exist in observable_types
    taxonomy:
      namespace: STRATEGOS               # closed set
      predicates: [kev-listed]           # the analyzer's own vocabulary
      levels: [info, malicious]          # closed set
    sandbox: trusted_first_party         # closed set
    rate_limit: {max_calls: 240, per_seconds: 3600}
```

Then `python tools/analyzers/contract.py --validate`. There is no step 3.

An analyzer that omits `sandbox`, `rate_limit`, `timeout_seconds` or `enabled`
inherits `defaults:`, which is fail-safe by construction — the default posture
is `sandboxed`, the strictest one.

## Why the vocabulary is closed, and why it fails at load

This repo has already shipped an open-looking vocabulary that was actually
closed, twice. `tools/provenance/registry.py::register_citation` validates
`citation_type` against a closed list and raises `ValueError` on an unknown
value; every caller swallowed the raise. Measured 2026-08-02, two subsystems
(Cortex governance, GovChain asset tokenization) had therefore written **zero**
provenance rows since inception, while the gate reported `warn` — which reads as
degradation, not breakage.

So in this contract:

- validation runs when the file is **parsed**, not when an observable is
  dispatched;
- `load_contract()` raises `UnknownObservableType` naming the offending analyzer
  key, the offending value, and every legal value;
- nothing in `contract.py` catches those errors, and the docstring says so — a
  caller that wraps `load_contract()` in a bare `except Exception` has
  reintroduced the bug;
- `for_observable()` also raises on an unknown type, so a typo at a call site
  cannot read as "no analyzers apply";
- `check_constraint_sql()` / `sqlite_check_clause()` render the SQL CHECK clause
  from the same vocabulary, so a future table storing observable types cannot
  disagree with the declaration (repo guardrail: "SQL CHECK constraints: derive
  from Python constants, never hardcode").

Every observable type names the module that consumes it today, and a test
asserts those files exist. A type with no consumer is dead vocabulary.

## Single source, gated mirror

`args/analyzer_contract.yaml` is the source. `icdev/data/args/` holds the
packaged copy that ships in the wheel, and `tools/analyzers/` is mirrored to
`icdev/tools/analyzers/` (registered in `args/mirror_parity.yaml`).

`find_contract_path()` does two ordered passes — all parents for `args/`, then
all parents for `data/args/` — so a source checkout resolves to the repo-root
copy from **both** import namespaces (`tools.analyzers.contract` and
`icdev.tools.analyzers.contract`), and only a wheel install reads the packaged
one. `test_packaged_copies_have_not_drifted` is the gate that keeps them equal;
without it this is how `llm_config.yaml` ended up with three drifting copies.

## Scope boundary

`anz-con-01` is the interface, not the rewiring. Explicitly out of scope and
tracked separately:

- **anz-disp-01** — one dispatch path taking an observable and returning
  taxonomy-tagged reports. `module`/`entrypoint` name the callable that already
  implements the analysis; the contract does not change its signature, and
  argument adaptation belongs to dispatch.
- **anz-mig-\*** — porting the high-value analyzers (`dsoc_*`, `pvm_*`,
  `triage_cve`, the strategos importers) onto the contract without behavior
  change. The six seeded declarations prove the mechanism against real modules;
  they are not the port.
- **anz-rate-\*** — enforcing the declared rate limit and sandbox posture,
  reusing the existing `sandbox_execute` MCP tool (Docker, resource limits,
  network isolation, D-SEC-10) rather than inventing a second isolation path.

## Acceptance criteria

| Criterion | Where it is held |
|---|---|
| A new analyzer is declared entirely in config | `test_new_analyzer_needs_only_a_config_entry`, `test_defaults_apply_to_a_minimal_declaration` |
| Accepted observable types and output taxonomy are machine-readable | `test_contract_serializes_to_json`, `python tools/analyzers/contract.py --json` |
| An unknown observable type is rejected at load, not at run | `test_unknown_observable_type_rejected_at_load`, `test_unknown_observable_type_is_not_swallowed`, `test_lookup_of_unknown_observable_type_raises` |
