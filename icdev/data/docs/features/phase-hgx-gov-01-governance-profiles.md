# CUI // SP-CTI

# Per-node governance profiles (hgx-gov-01)

**Card:** HGX — Harness Agent Parity and Graph Runtime
**Modules:** `tools/cortex/governance.py`, `tools/cortex/schemas.py`,
`tools/cortex/db/init_db.py`, `tools/cortex/__init__.py`,
`tools/studio/executors/agent_executor.py`, `tools/studio/workflow_runner.py`,
`tools/orchestration/workflow_composer.py`, `args/cortex_config.yaml`
(`governance.profiles`)
**Tests:** `tests/cortex/test_governance_profiles.py`,
`tests/studio/test_workflow_agent_node.py`
**NIST:** AU-2, AU-12 (the audit row stays mandatory), SC-28, SI-12.

---

## The gap

`GovernancePipeline` is the single enforced TRUST chain for every Cortex
invocation, and `GATE_ORDER` was a hardcoded seven-element tuple: pre-check,
input redaction, the operation, citation grounding, content grounding, output
redaction, provenance. The pipeline body was straight-line code against it.

The only per-call dials were `retrieval` (skip the two grounding gates),
`attach`, `ctx.fail_closed` and `ctx.trusted_content` (skip the two input
gates). None of them is a policy: they are structural facts about one call.

So a graph node doing internal diligence over first-party data — output feeding
the next node, no evidence set to attest against, input that never left the
tenant boundary — paid exactly the same seven gates as a node emitting a
customer-facing artifact. The chain was uniform because there was nowhere to say
otherwise, not because uniformity was the right answer.

## What shipped

A **governance profile**: a named subset of `GATE_ORDER`, declared as data.

```yaml
# args/cortex_config.yaml
governance:
  profiles:
    internal_diligence:
      gates: [input_redaction, operation, output_redaction, provenance]
```

```python
GovernancePipeline(operation="cortex.complete", profile="internal_diligence")
# or, per call:
pipeline.wrap(fn, ctx, prompt=p, profile="internal_diligence")
```

A gate the profile omits is recorded `"skip"` with the profile named as the
reason — a narrowed chain is exactly as observable in the `GovernanceReport` and
the `cortex_audit` row as a full one, and `report.profile` says which profile
ran. `GovernanceReport` gained a `profile` field; the audit blob gained a
`profile` key.

### The two gates no profile can drop

`MANDATORY_GATES = (operation, output_redaction, provenance)`.

`output_redaction` is the egress guarantee — the last thing between model output
and a caller, and the only gate applied to every result shape. `provenance` is
the NIST-AU append-only audit row. A profile able to drop either would turn a
latency optimisation into a compliance hole, so omitting one raises
`GovernanceProfileError` when the profile **loads**, not per call. `operation` is
listed beside them because it is the wrapped call itself: a profile that "skips"
it has skipped the work, not a gate.

The same error covers an unknown gate name, a malformed profile, an unknown
profile name at resolution time, and any attempt to redefine `default` — that
last one because redefining `default` would change the behaviour of every caller
that names no profile, which is the one thing profiles must not do.

### `default` is in code, not in YAML

`resolve_profile("")` returns `frozenset(GATE_ORDER)` without reading the config
at all. A missing, unreadable or truncated `args/cortex_config.yaml` therefore
cannot silently narrow governance, and no existing caller's behaviour depends on
a config file it never asked about.

### Studio agent nodes

A `node_type: agent` step may name one:

```yaml
- id: diligence
  node_type: agent
  prompt: Summarise what changed under tools/db/
  agent_tools: [worktree_read]
  governance_profile: internal_diligence
```

`workflow_runner` and `workflow_composer` forward it as
`--governance-profile` (both, in lockstep — a template must build the same
command headless as it does in the UI). The executor then runs the step's prompt
and the loop's final content through the pipeline under that profile: the prompt
is screened and input-redacted **before** the provider sees it, and the final
content is egress-redacted and provenance-recorded after. The published
`final_content` is the masked text, because `output_redaction` is not skippable
and publishing the raw string would bypass a gate that had just run.

A step naming **no** profile runs exactly as agent nodes always have: no
pipeline, no report, no audit row. Naming one is opting in — and the opt-in is
fail-closed. An undeclared profile, an unreadable governance config, or a gate
that blocks the prompt fails the step (`agent_step_unknown_governance_profile`,
`agent_step_governance_unavailable`, `agent_step_governance_blocked`) rather
than running it ungoverned. This is deliberately *unlike* the unsupported-provider
path, which degrades: an unavailable provider is an environment fact, a blocked
prompt is a refusal.

## Shipped profiles

| Profile | Drops | For |
|---|---|---|
| `default` (built-in) | nothing | everything that names no profile |
| `internal_diligence` | pre-check, both grounding gates | first-party input, no evidence set, output feeds another node |
| `screened_generation` | both grounding gates | untrusted input, free-form drafting |
| `trusted_ingest` | pre-check, input redaction | content already inside the tenant boundary (the `ctx.trusted_content` case, as a named profile) |

Every one of them keeps `output_redaction` and `provenance`. They cannot not.

## Acceptance

* A node naming a minimal profile skips only the permitted gates — and the
  skipped gates' seams are never called, so the skip is real rather than
  cosmetic (`test_a_minimal_profile_skips_only_the_gates_it_omits`).
* No profile can disable `output_redaction` or `provenance`; attempting it is a
  config error at load (`test_a_profile_cannot_drop_egress_or_the_audit_row`).
* Callers that name no profile run the whole chain, in order, with no skips
  (`test_naming_no_profile_runs_the_whole_chain`,
  `test_a_step_naming_no_profile_runs_ungoverned`).

## LLM- and OS-agnostic

No model id is named anywhere in this change; a profile names gates, and the
grounding gate's optional LLM judge continues to route by `llm_function`
through `LLMRouter`. Config is read through the existing
`load_cortex_config()` path (`pathlib`, `encoding="utf-8"`), the repo root
resolves from `__file__`, and the executor gains one argv flag — no shell.
