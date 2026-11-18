---
id: D-COE-1
title: The engineering standards register is a NEW file, args/engineering_standards.yaml, beside args/standards_registry.yaml,
  not an extension of it
version: '1'
status: accepted
section: 'Phase 83 — COE (Engineering Centre of Excellence: the standards register)'
format: legacy
supersedes: []
superseded_by: ''
links:
  requirements: []
  standards: []
context: ''
consequences: ''
recorded:
  by: bootstrap:icx-coe-02-adr-migration
  kind: bootstrap
  step_id: ''
  note: migrated from docs/reference/adrs.md at bbd0b5df6
---
**The engineering standards register is a NEW file, `args/engineering_standards.yaml`, beside `args/standards_registry.yaml`, not an extension of it.** `standards_registry.yaml` is the WG 22 citation catalog: external standards keyed by name, each mapping control or item ids to titles, read by `tools/writing/standards_validator.py` to check citations in prose (its only reader, plus its manifest row). An engineering standard is a different thing: a rule ICDEV holds itself to, with an owner role, a version, a scope, a check-as-code and a lifecycle. Growing the catalog would put lifecycle entries in a file whose `standards:` keys the validator treats as citable names, and would put STATUS in a reviewed YAML file where any edit could change it. So: (1) the catalog is untouched, and an engineering standard may cite one of its keys as a source (`{catalog: <key>}`, checked to exist), so the two are linked without a copy; (2) the new file holds only the reviewed SEEDS, and status (proposed, reviewed, adopted, refused, returned, deprecated, superseded, with dates) is the replay of the append-only, hash-chained ledger `coe_standard_events`, written only by the declared steps and the seeder; (3) the check-as-code is an existing IDP scorecard rule (`args/scorecards/`) or a named manual evidence step whose producing file must exist, and adoption refuses an entry with neither ("a standard without a check is a wish"); (4) the lifecycle is three declared templates (`decl-wft-coe-standard-review`, `-adopt`, `-deprecate`), one manual stage each because a declared template has exactly one, all `separate_from: builders` so the author never reviews, signs or retires their own version; (5) seeds that ICDEV already enforces enter `adopted` only when their check resolves and are recorded by a labelled bootstrap identity, never as a person's signature, and the twin reports each as `coe.in_force.bootstrap_only` until a person signs one; ICDEV[OS]'s interfaces and controls registers are referenced at a pinned commit with their sha256 and enter as proposals, because adopting them is the owner's decision. Rejected: a `lifecycle:` block inside `standards_registry.yaml` (couples the citation validator to governance state); status as a YAML field (an edit is not a decision); a single multi-stage template (the declared-step machinery holds one manual stage per template, and three steps with three reasons are easier to audit). ICDEV has no cryptographic signature for a person's step: "signed" here means the person's authenticated declared step, with a reason, bound to the entry digest in the append-only `wf_decision_steps` ledger.
