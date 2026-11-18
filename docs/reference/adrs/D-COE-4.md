---
id: D-COE-4
title: A new service starts conformant on a golden path whose declared standards are checked against the
  standards in force, its scaffold scored by the platform's own judges, and its versions published by
  a person who did not author them
version: '1'
status: proposed
section: 'Phase 83 — COE (Engineering Centre of Excellence: the standards register)'
format: structured
supersedes: []
superseded_by: ''
links:
  requirements:
  - PM-COE-16
  - PM-COE-17
  - PM-COE-18
  - PM-COE-19
  - PM-COE-20
  standards:
  - ENG-E2E-SPEC
  - ENG-FIVE-RULES
  - ENG-PAGE-COMPLETENESS
  - ENG-RED-FIRST
context: 'icx-coe-04, the fifth card of the Engineering CoE programme. icx-coe-01..03 gave ICDEV a standards
  register with a lifecycle, exemptions and ADR records, and scorecards per repo against the standards
  in force, but a new service was still hand-assembled: `icdev scaffold canvas` rendered 31 flavors none
  of which was conformant, and nothing noticed when a newly adopted standard was not met by the way services
  are made.'
consequences: Adopting a standard that applies to services makes every path behind until a new version
  declares it, and CI fails until then; a manual standard is declarable at once (needs_evidence), a machine
  one only when the scaffold actually passes it. The drift check adds ~15 s to the test-gates job for
  the two paths. A service scaffolded in place still needs routes.gen.js regenerated, its test file gated
  and its ADR accepted by a person.
recorded:
  by: agent:claude-icx-coe-04
  kind: agent
  step_id: ''
  note: drafted by the card's builder; a person who is not its author accepts it on /idp/adrs
---
(1) A new scaffold KIND, `icdev scaffold service --path <id>`, not a flag on `canvas`: what it promises is per path and checked in CI, so one entry point always produces the conformant thing and the canvas flavors keep their meaning. It reuses the shared template engine, the registry splice, and the existing SBOM, AI-BOM and ADR generators; the ADR it writes is PROPOSED, never accepted. (2) Each path in args/golden_paths.yaml declares the standards it satisfies by id and version; the standards that APPLY are derived, never listed: every standard in force whose id matches the path's repo declaration in args/coe_conformance.yaml. A path missing one, or at another version, is behind; `golden_paths.py --check` fails CI on the register's reviewed seeds, and the page and the twin judge by the live ledger. (3) The drift check scaffolds into a temporary tree laid over the checkout (symlinks for everything untouched) and judges the RESULT with the platform's own judges: the IDP scorecard through tools/iqe/adapters/idp.rooted (the same rules and fact collectors), feature_audit on the scaffold's own url_map and MCP registry, and the generated tests run red without the service and green with it. A machine-checkable standard must pass; a manual one is needs_evidence; a rule that measured nothing is unverified, never pass. (4) Versions are an append-only, hash-chained ledger (coe_golden_path_events): proposing and drift-checking decide nothing; a person who did not author a version publishes or retires it (decl-wft-coe-golden-path-publish / -retire), and publishing only when its newest drift check passed on the template as it is now and it is not behind. Rejected: a hard-coded list of standards per path (it goes stale silently, which is the defect); running the drift check on a full git worktree (seconds of checkout per run); a scaffold that registers into the live repo during the check.
