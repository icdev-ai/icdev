# Observable Dispatch — One Entry Point (anz-disp-01)

> **Classification:** CUI // SP-CTI
> **Task:** `anz-disp-01` (ANZ — Unified Analyzer Contract, epic DISP)
> **Builds on:** [analyzer-contract.md](analyzer-contract.md) (`anz-con-01`)

## The problem

A caller had to know which module handled which indicator. A CVE went to
`tools.supply_chain.cve_triager`, an IP to
`tools.security_canvas.threat_intel_engine`, a vendor to
`tools.supply_chain.ndaa_889_screener`, a STIX bundle to
`tools.strategos.stix_importer`. Adding a threat source meant editing a
blueprint, and no caller could ask "what does this platform know about
`198.51.100.7`?" without first knowing the answer.

## What shipped

`tools/analyzers/dispatch.py` — one entry point:

```python
from tools.analyzers.dispatch import dispatch

result = dispatch("ip", "198.51.100.7")
result.partial                                  # did every analyzer finish?
[(r.analyzer, r.status) for r in result.reports]
result.taxonomy                                 # [{namespace, predicate, level, value}, ...]
```

Which analyzers run is read from `args/analyzer_contract.yaml` — every
declaration whose `accepts:` names that observable type. **Nothing in
`dispatch.py` enumerates analyzers, observable types, or modules**, and
`test_dispatch_module_hardcodes_no_analyzer_or_observable_names` asserts it
stays that way by scanning the executable lines of the module against every
shipped analyzer key.

## Design decisions

### Nothing is dropped silently

Every matched declaration produces a report, and every report carries a status
from a closed vocabulary:

| Status | Meaning | Fix |
|---|---|---|
| `ok` | the analyzer returned | — |
| `timeout` | it exceeded its declared `timeout_seconds` | raise the budget, or make the analyzer faster |
| `error` | it raised | fix the analyzer; the exception text is in `detail` |
| `unavailable` | its module or entrypoint could not be imported | fix `module`/`entrypoint` in the contract |
| `misdeclared` | the declaration and the callable's signature disagree | fix `binding:` — `detail` names the parameter |
| `skipped` | the caller's context lacks a key the declaration maps | pass the key — `detail` names it |

These are five separate statuses rather than one `failed` because each has a
different remedy. `DispatchResult.partial` is true whenever any report is not
`ok`, and `partial_reasons` names the offenders by status.

This is the requirement that drove the shape of the whole module: **a fan-out
that silently dropped a timed-out analyzer would read identically to one where
that analyzer found nothing.** Declarations held back *before* the fan-out —
disabled, not requested, or a responder when only analyzers were asked for —
are listed under `excluded` with a reason, for the same reason.

### Argument binding is declared, not guessed

The declared entrypoints share no signature:

```
match_observable(observable, context="")               # value first
triage_cve(project_id, cve_id, component, ...)         # value third, five more required
screen_item(item: dict)                                # value is one key of a dict
trigger_rtbh(conn, prefix, reason, ...)                # first argument is a DB connection
```

Any "just pass it first" rule hands an IP address to `conn`. The call then
fails *inside* the analyzer — which is exactly the failure mode this task
exists to eliminate, because from outside it is indistinguishable from a clean
run that found nothing.

So the call is declared in data, next to the analyzer it describes:

```yaml
binding:
  observable_arg: cve_id                    # `param.key` nests it in a dict argument
  context_args: {project_id: project_id}    # parameter -> dispatch-context key
  static_args:  {note: from-contract}       # constants
```

Omitted, the value goes to the callable's first parameter — right for
`match_observable`, `scan` and `parse_bundle`, so most declarations need no
block. A parameter left with no source and no default is reported
`misdeclared` **by name**; a mapped context key the caller omitted is reported
`skipped` **by name**. The dispatcher never guesses. Binding defects that are
knowable from the file alone (two sources for one parameter, a malformed
`observable_arg`) are rejected **at load** by `tools/analyzers/contract.py`,
consistent with `anz-con-01`.

### Responders do not run by default

The contract's `kind` splits analyzers (observe) from responders (act).
`dispatch()` runs analyzers only. `rtbh_blackhole` blackholes a prefix;
submitting an IP for analysis must not trigger it. Responders require an
explicit `kinds=("analyzer", "responder")` / `include_responders: true`, and
their exclusion is visible in `excluded` rather than implicit.

### The fan-out reuses a proven executor

`tools/cortex/search_service.py::_run_backends` already solved this: submit
onto a process-wide bounded pool that is **never shut down per call**, budget
each future from a shared start, and abandon — not join — a future that
overruns. A per-call executor leaks a worker thread for every timeout, because
the abandoned future keeps running and `shutdown()` waits for it. Dispatch
reuses that shape rather than becoming a third executor with a third copy of
the bug. Pool width: `ICDEV_ANALYZER_MAX_WORKERS` (default 8).

Imports happen **inside** the worker, so a module that hangs on import is
covered by the analyzer's timeout budget instead of stalling the whole fan-out.

### Taxonomy tags are validated, not trusted

An analyzer opts in by returning a mapping with a `taxonomy` key. The
`namespace` is stamped from the declaration — an analyzer cannot claim a
namespace it was not declared under — while `predicate` and `level` are checked
against what it declared it emits. A tag outside that vocabulary is dropped
into `taxonomy_defects` and makes the result partial: silently accepting an
undeclared predicate is how a closed vocabulary rots into a decorative one, and
silently dropping it is what the `citation_type` bug looked like from outside.

An analyzer that emits no taxonomy gets `taxonomy: []` and no defects. Nothing
is fabricated.

## MCP surface

Exposed through the **existing** unified gateway as declarative metadata
(`module` + `handler` strings in `tools/mcp/tool_registry.py`, category
`analyzers`) — **not** a new MCP server:

| Tool | Handler |
|---|---|
| `analyzer_dispatch` | `tools.mcp.gap_handlers.handle_analyzer_dispatch` |
| `analyzer_capabilities` | `tools.mcp.gap_handlers.handle_analyzer_capabilities` |

Both read the contract at call time, so a newly declared analyzer is reachable
over MCP with no registry edit. An unknown observable type comes back with the
legal values rather than an empty report set.

## CLI

```bash
python tools/analyzers/dispatch.py --observables
python tools/analyzers/dispatch.py --type ip --value 198.51.100.7
python tools/analyzers/dispatch.py --type vendor --value "Acme Corp" --json
python tools/analyzers/dispatch.py --type ip --value 1.2.3.4 --responders
```

Exit code is `2` when the result is partial — a caller scripting this must not
read "some analyzers never answered" as a clean run.

## Acceptance criteria

| Criterion | Where it is held |
|---|---|
| Submitting an observable returns reports from all analyzers declaring that type | `test_dispatch_runs_every_analyzer_declaring_the_type`, `test_reports_are_taxonomy_tagged_with_the_declared_namespace` |
| A timed-out analyzer is reported as timed-out, not omitted | `test_timed_out_analyzer_is_reported_not_omitted`, `test_a_timeout_makes_the_whole_result_partial`, `test_partial_results_from_the_analyzers_that_finished_are_still_returned` |
| Adding an analyzer requires no dispatch-code change | `test_declaring_a_new_analyzer_needs_no_dispatch_code_change`, `test_dispatch_module_hardcodes_no_analyzer_or_observable_names` |

`tests/test_analyzer_dispatch.py` (41 tests) is in the CI unit-test allowlist.

## Scope

Dispatch reads `rate_limit` and `sandbox` from each declaration and surfaces
them through `analyzer_capabilities`, but does **not** enforce them —
enforcement is `anz-rate-01`, reusing the existing `sandbox_execute` path.
Porting the remaining ~79 analyzer-shaped modules onto the contract is
`anz-mig-*`. The six seeded declarations prove the mechanism against real
modules; they are not the port.

## Related

- `tools/manifest/analyzer-contract.md` — tool manifest shard
- `docs/security/sandbox-coverage.md` — Gap 45, the dispatcher's trust decision
- `tools/cortex/search_service.py::_run_backends` — the fan-out shape reused here
