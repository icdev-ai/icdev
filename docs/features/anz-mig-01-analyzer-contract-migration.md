# CUI // SP-CTI

# ANZ-MIG-01 — Porting the highest-value analyzers onto the contract

**Status:** shipped
**Date:** 2026-08-02
**Card:** ANZ — Unified Analyzer Contract (`args/projects.yaml`, epic `mig`)
**Depends on:** anz-con-01 (the contract, PR #1217)

---

## What shipped

Six analyzers across two families now dispatch through
`args/analyzer_contract.yaml` — the DSOC network-defence family and the PVM
predictive-vulnerability family — with their behaviour proved unchanged against
a fixed input set.

| Analyzer key | Family | Callable | Observable |
|---|---|---|---|
| `rtbh_blackhole` | DSOC | `tools.dsoc_canvas.rtbh_manager:trigger_rtbh` | `ip`, `cidr` |
| `bgp_prefix_hijack` | DSOC | `tools.dsoc_canvas.bgp_hijack_detector:detect_prefix_hijack` | `bgp_prefix` |
| `bgp_route_leak` | DSOC | `tools.dsoc_canvas.bgp_hijack_detector:detect_route_leak` | `bgp_prefix` |
| `pvm_risk_prediction` | PVM | `tools.network.vuln_predictor:predict_advisory_risk` | `advisory` |
| `pvm_triage_scoring` | PVM | `tools.network.vuln_triage_engine:score_advisories` | `advisory` |
| `pvm_attack_surface` | PVM | `tools.network.attack_surface_mapper:map_attack_surface` | `network` |

No analyzer module was modified. Not one line.

### New pieces

- **`input_binding`** — an optional block on a declaration saying how an
  observable reaches an existing callable's parameters: which parameter receives
  it, whether it is passed bare or wrapped in a list, where a DB handle comes
  from, and which remaining parameters the caller must supply. Declared as data,
  for the same reason the rest of the contract is: one adapter function per
  analyzer would rebuild exactly the bespoke wiring this card exists to delete.
- **`tools/analyzers/binding.py`** — assembles the call and returns the
  callable's result **untouched**. Also `--verify`, which checks every binding
  against the real signature of its callable.
- **`tools/analyzers/parity.py`** + **`args/analyzer_parity_cases.yaml`** — the
  before/after diff.

```bash
python -m tools.analyzers.contract --list        # 11 declared, 6 bound
python -m tools.analyzers.binding --verify       # bindings vs real signatures
python -m tools.analyzers.parity --live          # behaviour diff
```

---

## How "behaviour must not change" was actually proved

A port can silently change behaviour two ways, and each is checked separately.

**1. Input adaptation — the binding could put a value in the wrong parameter.**
Every case in `args/analyzer_parity_cases.yaml` declares `direct_kwargs`: the
call a hand-written call site makes, written out from the existing call sites
(`tools/mcp/gap_handlers.py`, the DSOC blueprint) rather than generated from the
binding. The harness asserts the contract assembles byte-identical kwargs. This
executes nothing, so it is deterministic and side-effect free — it holds on an
empty worktree DB and in CI alike.

**2. Output transparency — the binding could wrap, coerce or swallow.**
`binding.invoke()` returns the callable's result untouched and lets its
exceptions propagate. Cases marked `live` are executed **both ways in the same
process against the same database** and their outcomes diffed: same return
value, or the same exception type and message.

The diff is direct-vs-bound in one environment, **not** against a golden file
captured elsewhere. A golden file would bake in whatever rows the capture host
happened to hold and would go stale the first time one changed.

### Why exceptions are not caught

Every existing call site for these analyzers is a `gap_handlers.py` function
wrapping the call in `except Exception: return {"error": str(exc)}`. If the
binding layer also caught, a port would convert a raise into an error dict and
the handler's own except clause would go dead — the output shape would change on
every failure path. The boundary that swallows stays exactly where it already
is.

### Coverage, stated honestly

| Case | Live? | Why |
|---|---|---|
| `pvm_risk_prediction` / single-advisory | **executed** | read-then-raise; no writes |
| `pvm_triage_scoring` / batch-of-one | **executed** | read-then-raise; no writes |
| `rtbh_blackhole` | call-shape only | commits; a second execution is a second blackhole |
| `bgp_prefix_hijack` | call-shape only | records a hijack event and commits |
| `bgp_route_leak` | call-shape only | records a leak event and commits |
| `pvm_attack_surface` | call-shape only | issues Forward Networks NQE queries over the network |

Four of six are not executed live. That is a real hole in the proof and the
harness prints it rather than reporting a clean 6/6 — a test that reaches green
by not running the hard half is the failure mode this card was written against.
`tests/test_analyzer_binding_parity.py::test_skipped_live_cases_state_a_reason`
enforces that a skipped case names its reason. The success path is instead
exercised end-to-end against a fixture module: connection opened, committed only
when declared, closed even when the analyzer raises, result identity preserved.

The harness can also go red. `test_parity_harness_detects_a_swapped_observable`
rewrites `bgp_prefix_hijack` to bind the observable to the prefix we *own*
rather than the one we *observed* — the inversion that would actually happen
when porting a hijack detector — and asserts the harness fails.

---

## What does NOT fit the contract

A contract everything fits after enough special-casing is not a contract. These
were surveyed for anz-mig-01 and rejected. Each is a family, not a one-off.

### 1. Canvas aggregators — they accept no observable

`dsoc_threat_ingest` → `tools.dsoc_canvas.dsoc_aggregator:get_dsoc_overview(conn)`
`dsoc_hijack_report` → `tools.dsoc_canvas.bgp_hijack_detector:get_active_hijacks(conn)`

Both were named in the card as top migration candidates. Both take **only a
connection**. They answer "what is currently happening across this canvas", not
"what do you make of this thing" — there is no observable to bind, and no
verdict to tag. Making them fit would mean inventing a null observable type,
which is how a closed vocabulary starts drifting toward meaningless.

They are **queries**, and they belong to whatever query/reporting surface
anz-disp-01 exposes alongside dispatch — not to the analyzer contract.
`tools.network.vuln_predictor:get_top_risks`,
`attack_surface_mapper:get_surface_summary` and
`bgp_hijack_detector:get_hijack_summary` are the same shape.

### 2. Feed importers — source in, N records out

`cisa_kev_importer:run(file=None, sync=False, as_json=False)`
`acled_importer:write_events(events, dry_run=False)`
`adsb_importer:fetch_opensky(bbox, timeout=20)`
`ground_vehicle_importer:fetch_gdelt_events(...)`, plus `tle_importer`,
`uas_importer`, `oryx_importer`, `gdelt_importer`, `economic_importer`,
`eo_importer`, `frontline_importer`, `osm_importer`, `ais_importer`.

The card listed the strategos importers as migration candidates. **None of them
fit, and the mismatch is structural rather than cosmetic.** An analyzer is
`observable → report`. An importer is `source → many records`: it pulls a whole
feed, or a bounding box, or a file, and writes rows. There is no single
observable in, and no verdict out. Note the module surfaces — most expose only
`main()` and `run()`, i.e. they are CLI scripts, not callables anything was
meant to invoke programmatically.

Forcing an importer in would require an observable type meaning "the entire KEV
catalogue" and a taxonomy verdict about a bulk load. Importers want a *different*
contract — source, schedule, cursor, idempotency key, records-written — and
inventing that is not this card.

`stix_ingest` (declared by anz-con-01) is the one real exception, and it is not
an importer: `parse_bundle(bundle: dict)` takes one bundle and returns what is
in it. Its observable is the bundle.

### 3. Daemon watchers — a loop, not a call

`tools.supply_chain.cve_passive_watcher` — the card named `watch_passive_cve`.
**That function does not exist.** The module exposes `watch_scan(project_id,
since_id=None, ...)` and `watch_continuous(...)`. `watch_scan` takes a *project*
and sweeps the audit trail for CVE events; it never receives a CVE. Despite the
name, it is a scheduled sweep whose input is a scope and whose output is a count.
`watch_continuous` is a daemon loop and does not terminate.

`tools.strategos.darkweb_monitor` is the same: `get_signals(status_filter=None)`
is a listing, `tor_status()` is a health probe. Neither takes an observable.

### 4. Whole-corpus sweeps — the batch half of an otherwise-fitting analyzer

`vuln_predictor:predict_all_open_advisories()` and
`vuln_triage_engine:score_advisories(advisory_ids=None)` sweep every open
advisory. The single-observable half of each was ported; the sweep half has no
observable and stays out. This is worth calling out because it is the seductive
case: `score_advisories` is declared with `observable_form: list`, so a
one-observable dispatch produces `advisory_ids=[id]` — but passing `None` to
sweep the whole platform is a scheduling operation wearing an analyzer's clothes.
The contract does not express it and should not.

### 5. Already-declared but still unbound

`cve_triage`, `section_889_screen`, `threat_intel_match`, `secret_scan` and
`stix_ingest` were declared by anz-con-01 and remain declaration-only. They are
outside this card's named scope, and `binding.py --verify` lists them explicitly
as `declared` rather than letting "declared" pass for "wired".

One of them should be looked at before anyone binds it:
**`tools/supply_chain/cve_triager.py` carries `DEPRECATED: unused as of
2026-05-09. Remove after 2026-08-01`** — a date that has now passed. The
contract's flagship declaration points at a module scheduled for deletion. That
is a decision for the ANZ card owner, not something to quietly build on, so
`cve_triage` was deliberately left unbound despite `triage_cve` being a named
candidate.

---

## Vocabulary added

Two observable types, both with real consumers:

- **`advisory`** — `nc_advisories.id`. The PVM family does **not** accept a CVE
  string. It accepts the platform's own advisory record, which already carries
  the CVSS, publication date and asset linkage the scorers read. Declaring
  `cve` would have been a lie that typechecks.
- **`network`** — the identifier scoping NQE device-inventory queries.

One taxonomy namespace: **`NETWORK`** for `tools/network/`. The existing
namespaces are directory-scoped and none covered NDC.

---

## What this does not do

- **No rate limiting, no sandbox enforcement.** Declared, not enforced — that is
  anz-rate-01, upstream of the binding layer.
- **No taxonomy tagging, no report envelope, no REST surface.** `invoke()`
  returns the analyzer's raw result on purpose; wrapping it is anz-disp-01's job,
  downstream of the binding layer. Adding an envelope here would have made the
  port a behavioural change, which is the one thing this card forbade.
- **No change to `tools/mcp/gap_handlers.py`.** The hand-written handlers still
  work exactly as before. A caller going through the contract no longer needs
  one; that is the whole benefit, and removing them is a separate decision.

## Files

| File | Change |
|---|---|
| `args/analyzer_contract.yaml` | `input_binding` schema docs, 2 observable types, `NETWORK` namespace, 5 new declarations, binding on `rtbh_blackhole` |
| `args/analyzer_parity_cases.yaml` | new — the fixed input set |
| `tools/analyzers/contract.py` | `InputBinding` / `ConnectionBinding` parsing and validation |
| `tools/analyzers/binding.py` | new — resolution, invocation, `--verify` |
| `tools/analyzers/parity.py` | new — the before/after diff harness |
| `tests/test_analyzer_binding_parity.py` | new — 29 tests |
| `tests/test_analyzer_contract.py` | drift gate extended to the new mirrored files |
| `icdev/tools/analyzers/`, `icdev/data/args/` | mirrored copies |
| `.github/workflows/icdev-ci.yml` | new test file in the unit tier |
