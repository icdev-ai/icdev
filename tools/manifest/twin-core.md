# Twin Core — Cross-Canvas Digital-Twin Unification (TWX)

> Shard of `tools/manifest.md`. See index at `tools/manifest.md`.
>
> `tools/twin_core/` is an **additive** layer that unifies the eight working canvas digital twins (NDC, PDC, BDC, SDC, DDC, ODC, IDC, Mission) behind one registry and one canonical verdict/violation schema — **without rewriting any of them**. Adapters translate each canvas's native twin output into the canonical schema, preserving `method`/provenance. Sequoia-Combine Pattern 4. Card: TWX (twx-core-01 = registry + schema + NDC/PDC reference adapters).

## Twin Core
| Tool | File | Description | Input | Output |
|------|------|-------------|-------|--------|
| Twin Canonical Schema | tools/twin_core/schema.py | Canonical verdict (`pass\|warn\|fail`, + `unknown` sentinel), severity (`blocker\|critical\|high\|medium\|low`), category (`service_parity\|iam\|network\|compliance\|cost\|security`) and `target_csp` enums. Normalizes the 3 verdict families (pass/warn/fail, green/amber/red, pass/fail gates) and severity families (bare `high`, STIG `CAT1-3`, cATO `high/moderate/low`) without loss. Never fabricates a verdict; unknown → `unknown`. | `normalize_verdict/severity/csp`, `canonical_violation(...)`, `worst_verdict`, `twin_verdict(...)` | canonical violation dicts / envelope |
| Twin Registry | tools/twin_core/registry.py | Data-driven registry of thin per-canvas `TwinAdapter`s. Adapters self-register via `@register_twin`; `TwinRegistry.discover()` imports every module in `adapters/` by filesystem scan (no hardcoded canvas list). Display names cross-checked against `args/component_registry.yaml`. Base adapter degrades gracefully (canvases lacking `list_snapshots` return `[]`). | `TwinRegistry.get(key)/discover()/describe_all()` | `TwinAdapter` instances |
| NDC Twin Adapter | tools/twin_core/adapters/ndc.py | Wraps `tools/network/twin.py`. `method='heuristic'`. Maps `compliance_findings` → `network` violations; reads `network_twin_snapshots` for the list surface NDC's native module lacks. | `simulate_delta(project_id, topology_delta)` | canonical envelope |
| PDC Twin Adapter | tools/twin_core/adapters/pdc.py | Wraps `tools/pipeline/twin.py`. `method='static-analysis'`. Antipatterns → `security`, compliance-rule failures → `compliance`. Passes PDC's sha256 snapshot dedup + retention (pdx-perf-01) straight through. `latest_status` surfaces the newest persisted `pdc_simulations` verdict. | `simulate_delta(pipeline_id, delta_graph)` | canonical envelope |

Remaining canvas adapters (BDC/SDC/DDC/ODC/IDC/Mission) + a cross-canvas observer land in **twx-core-02**. Event-bus wiring in **twx-bus-01**. Mirrored to `icdev/tools/twin_core/`.
