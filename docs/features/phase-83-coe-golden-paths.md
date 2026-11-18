# CUI // SP-CTI
# Golden paths: a new service conformant by construction (icx-coe-04)

The fifth card of the Engineering CoE programme. Decisions: ADR D-COE-4. Requirements: PM-COE-16..20
(`args/platform_model/coe.yaml`).

## What a person does
- Scaffold a service: `icdev scaffold service ledger_review --display-name "Ledger Review" [--path model-service]`.
- See the paths on `/idp/golden-paths` (linked from `/idp/standards` and the nav): each path, the standards in
  force that apply to it, whether it declares them all, and its last drift check.
- Propose a new version there, run its drift check, and have a person who did not author it publish it
  (`decl-wft-coe-golden-path-publish`). Retire a version the same way (`decl-wft-coe-golden-path-retire`).

## What holds
- A path declares the standards it satisfies by id and version (`args/golden_paths.yaml`). Which standards
  apply is derived from the register and `args/coe_conformance.yaml`, never listed.
- A path that misses one is behind. CI fails: `python tools/idp/golden_paths.py --check`.
- The drift check scaffolds into a temporary tree laid over the checkout and runs the IDP scorecard and
  feature_audit on the result. A scorecard-rule standard must pass; a manual one shows needs evidence; a rule
  that measured nothing is unverified. The generated tests must be red without the service and green with
  it. CI: `python tools/idp/golden_paths.py --drift` (about 15 s for both paths).
- Publishing needs a passing drift check on the current template and a version that is not behind. A gate
  that cannot be evaluated blocks. The author never publishes or retires their own version. Agents draft only.
- The ledger `coe_golden_path_events` is append-only and hash-chained (migration 20261118070149).
- The idp governance twin observes `coe.golden_paths`; findings carry threads to ledger rows.

## What a scaffold contains
Contract (`contract.openapi.yaml`), SBOM (`bom/sbom.cdx.json`, from `tools/compliance/sbom_generator.py`),
AI-BOM for `model-service` (`bom/ai-bom.json`, from `tools/security/ai_bom_generator.py`), control map,
OTel spans (`telemetry.py`), a PROPOSED ADR record (`tools/idp/adrs.py`), a page and its icdev mirror, a
declared HITL template, a twin adapter with threads, a platform-model requirement traced to the generated
test, read and propose MCP tools, a feature_conformance entry, an IQE adapter with seed queries, pytest and
Playwright tests, and a scorecard declaration (`service.yaml` and the registry entry).

## Notes
- A service scaffolded in place still needs `python tools/dashboard/routes_gen.py`, its test file added to a
  `core.d` fragment, `python tools/idp/adrs.py --write`, and its ADR accepted by a person.
