# CUI // SP-CTI

# Evidence integrity: file existence is not evidence of a control

**Task:** exa-policy-08 · **Card:** EXA — External Adoption
**Modules audited:** the five that evidenced MCP per-tool authorization

| Module | Check |
|---|---|
| `tools/compliance/fedramp_ksi_generator.py` | `EVIDENCE_COLLECTORS["mcp_tool_authorizer"]` |
| `tools/compliance/owasp_agentic_assessor.py` | `_check_mcp_rbac` (T06, T14) |
| `tools/compliance/owasp_asi_assessor.py` | ASI-02 (Tool Abuse) |
| `tools/security/red_team_registry.py` | `OutputSafetyPlugin` RT-OS-003 |
| `tools/testing/production_audit.py` | SEC-005 |

---

## 1. What was wrong

All five evidenced per-tool MCP authorization by checking that
`tools/security/mcp_tool_authorizer.py` **exists on disk**. That module shipped
with zero enforcement call sites, so every check passed while nothing
authorized anything. An assessor reading the generated artifact would conclude
that per-tool RBAC was in force.

Overstated evidence is a worse failure than a missing control: the missing
control is at least discoverable.

The measurable effect on `KSI-AC-01` (*Least Privilege Enforcement*, NIST
`AC-6`), whose three evidence sources are `rbac_config`, `mcp_tool_authorizer`
and `agent_trust_scores`:

| | evidence available | maturity reported |
|---|---|---|
| before (both config checks were file existence) | 2 / 3 | **intermediate** |
| after (authorization asserted behaviourally) | 1 / 3 | **basic** |

Nothing about the deployment changed. Only the accuracy of the claim did.

## 2. What replaced it

`tools/security/mcp_authz_evidence.py` — a shared probe that exercises the
control in three layers. All five call sites now delegate to it.

1. **The policy must decide.** `MCPToolAuthorizer` is run against the four
   branches of the D261 deny-first contract: an explicit deny, a default-policy
   deny, an unknown-role deny, and a positive allow. An empty or
   `default_policy: allow` matrix fails here even though the file exists.
2. **The policy must be wired to a surface that has a principal.** The probe
   calls the SaaS MCP surface's own decision function and requires it to refuse
   a call the matrix denies. With no call site there is nothing to call, and
   the verdict is `not_satisfied`.
3. **The verdict must bind.** A decision that is logged and then ignored
   (monitor mode) reports `partially_satisfied`, never `satisfied`.

```bash
python tools/security/mcp_authz_evidence.py --json
python tools/security/mcp_authz_evidence.py --gate           # exit 1 unless enforcing
python tools/security/mcp_authz_evidence.py --gate --allow-monitor
```

Pinned by `tests/test_exa_policy_08_mcp_authz_evidence.py` (25 tests). The
load-bearing one is negative: `mcp_tool_authorizer.py` is asserted present in
the checkout, and every generator must still refuse to call the control
satisfied while enforcement is disabled.

## 3. Scope of the claim

Per-tool RBAC is claimed **only on `tools/saas/mcp_http.py`**, the one MCP
surface with an authenticated principal — the gateway middleware establishes
tenant, user and role before the blueprint runs. It is not claimed
platform-wide. The scope statement is carried in the probe result and
persisted with the evidence (`scope_notes` on a KSI, `automation_result` on an
assessor row, `details` on a red-team or audit finding), so it reaches the
artifact an assessor actually reads.

### stdio is out of scope, deliberately

`tools/mcp/unified_server.py` and `tools/mcp/base_server.py` carry no caller
identity. A stdio MCP server is spawned by, and speaks only to, its parent
process. A role supplied in the tool arguments is self-asserted by the caller
being authorized, which is not authentication — gating on it would produce
authorization-shaped evidence with no authorization behind it, which is the
exact defect this work exists to remove.

Compensating controls, each probed rather than assumed (RT-OS-003b) — except the
first, which was probed for *behaviour* and never for *reachability*, and has
been withdrawn on that ground:

| Control | Bounds the surface by |
|---|---|
| ~~`tools/agent_runtime/approval_gate.py`~~ | ~~Classifies each call by reversibility; halts irreversible ones for approval.~~ **WITHDRAWN 2026-08-16 (rem-cap-03): measured, this control has evaluated ZERO tool calls.** It is not in the `claude_cli` adapter's path (a separate process), and on the in-process paths `_resolve_approval_gate` returns *no gate* because it reads `ICDEV_AGENT_APPROVAL_MODE` from the environment rather than consulting `resolve_mode()` — so `args/agent_runtime.yaml`'s shipped `enforce` arms nothing. 62 declared rules, 0 ever evaluated, on a board that has dispatched 3,214 autonomous builds. Listing it here was itself the file-existence-as-evidence defect. See [`approval-gate-reachability.md`](approval-gate-reachability.md); remediation is `rem-cap-04`. |
| `.claude/hooks/pre_tool_use.py` | Hard-blocks destructive commands and UPDATE/DELETE on append-only tables before the call runs. |
| `args/file_access_tiers.yaml` | Tiers filesystem reach so a tool cannot read or write outside its declared tier. |

Note that `tools/mcp/gap_handlers.py::_mcp_authz_gate` **does** call the
authorizer, for `dsoc_rtbh_trigger` and `dsoc_flowspec_activate`. It is not
counted as evidence here, and should not be: it reads the role from
`args["mcp_role"]` on the stdio surface, so the caller supplies the identity it
is authorized against. It is a useful speed bump against an ungated client
injecting routing actions; it is not access control.

---

## 4. Remaining file-existence-as-evidence in these modules

Reported, not fixed — outside this task's scope. The pattern repeats widely.

### 4.1 `fedramp_ksi_generator.py` — 33 of 76 evidence sources

`EVIDENCE_COLLECTORS` has 76 sources. 43 count DB rows (real evidence: something
happened). The other 33 do not:

**27 terminate on file existence** — `rbac_config`, `network_policies`,
`append_only_tables`, `pre_tool_use_hook`, `api_key_management`, `agent_config`,
`a2a_agent_cards`, `classification_config`, `security_gates`,
`code_pattern_config`, `icdev_yaml`, `cloud_config`, `resilience_config`,
`hpa_config`, `rate_limiter`, `k8s_manifests`, `attestation_config`, `ir_plan`,
`pipeline_config`, `test_results`, `bdd_results`, `e2e_results`, `sast_results`,
`dependency_audit`, `code_pattern_scan`, `claude_dir_validator`, `cato_monitor`.

**6 grep a config for a substring** — `session_config`, `hmac_config`,
`secrets_config`, `a2a_tls_config`, `encryption_config`, `behavioral_drift`. A
commented-out line satisfies these.

Three sub-patterns are worse than the rest:

- **Results claimed from a directory.** `test_results` is `tests/` exists.
  `bdd_results` is `features/` exists. `e2e_results` is `.claude/commands/e2e`
  exists. `sast_results` is `sast_runner.py` exists. These are named *results*
  and no run ever happened.
- **Aliased sources double-count one file.** `append_only_tables` and
  `pre_tool_use_hook` are the *same* `_file_exists(".claude/hooks/pre_tool_use.py")`
  call. `KSI-AU-03` cites both and has no other sources — so one file existing
  scores 2/2 and reports **advanced** maturity. `KSI-IA-02` has the identical
  shape with `agent_config` / `a2a_agent_cards`, both
  `_file_exists("args/agent_config.yaml")`. Two KSIs report the top maturity
  band for one file each.
- **Inert-module risk.** `attestation_config`, `ir_plan`, `rate_limiter`,
  `cato_monitor`, `dependency_audit`, `code_pattern_scan`,
  `claude_dir_validator` and `api_key_management` are all "a tool module is on
  disk" — the same shape as the `mcp_tool_authorizer` defect. Each is only
  sound while that module has real call sites; none of them checks.

### 4.2 `owasp_agentic_assessor.py` — 7 of 8 gap checks

Every check except the one fixed here is `module exists` plus a substring grep
of `args/owasp_agentic_config.yaml`. Because the 8 checks are fanned out across
17 threat IDs, each weak check carries 2–3 threats:

| Check | Threats | Evidence |
|---|---|---|
| `_check_behavioral_drift` | T01, T07, T13 | `ai_telemetry_logger.py` exists + `"drift"` in config |
| `_check_tool_chain` | T02, T11, T16 | `tool_chain_validator.py` exists + `"TC-001"` in config |
| `_check_output_safety` | T05, T12 | `agent_output_validator.py` exists + `"output_validation"` in config |
| `_check_threat_model` | T04, T15 | `goals/agentic_threat_model.md` exists + `"STRIDE"` in it |
| `_check_trust_scoring` | T03, T09 | `agent_trust_scorer.py` exists + `"trust_scoring"` in config |
| `_check_behavioral_red_team` | T10 | `atlas_red_team.py` exists + `"BRT-001"` in it |
| `_check_nist_crosswalk` | T08, T17 | catalog JSON parsed, ≥80% crosswalked — **this one is real** |

### 4.3 `owasp_asi_assessor.py` — ASI-05, ASI-07, ASI-08

- **ASI-05** (Code Execution): `args/code_pattern_config.yaml` exists →
  `satisfied`. The scanner is never run and no scan result is consulted.
- **ASI-07** (Comms Compromise): `"tls"`, `"mtls"` or `"hmac"` appears anywhere
  in `args/agent_config.yaml`.
- **ASI-08** (Cascading Failures): `"circuit_breaker"` or `"retry"` appears
  anywhere in `args/resilience_config.yaml`.

All three report `satisfied` — the top status — from a file test.

### 4.4 `red_team_registry.py` — 8 of 12 built-in checks

A red-team plugin whose checks are `Path.exists()` red-teams nothing. Beyond
the fixed RT-OS-003: RT-CB-001 (gates config exists), RT-CB-002 (hook exists,
then a substring), RT-CB-004 (`classification_manager.py` exists), RT-OS-001
(`agent_output_validator.py` exists), RT-OS-001b (substring), RT-OS-002
(`agent_trust_scorer.py` exists), RT-CC-001 (chaincode config exists), RT-CC-002
(blockchain requirements file exists), RT-CC-003 (`"FIPS"` substring).

### 4.5 `production_audit.py` — mostly sound, one check that cannot fail

Most of this module does real work: subprocess runs, DB row counts, HTTP page
probes, threshold comparisons. Existence tests are usually a *precondition*
before that work, which is fine. Three exceptions:

- **INT-005 (API Gateway) never fails.** It calls
  `importlib.util.spec_from_file_location(...)` and passes if the spec and its
  loader are truthy — but that function does not stat the path and never
  executes the module. Verified: it returns a spec with a loader for a file
  that does not exist, so INT-005 reports `pass` for a deleted API gateway.
  This is weaker than a file-existence check, which would at least fail.
- **CMP-009 (SLSA/SWFT)** `py_compile`s two generators. Parsing is not
  attestation; nothing checks an attestation was produced.
- **PRF-002 (DB Backup Config)** passes on the substring `"backup"` appearing
  anywhere in `args/db_config.yaml`.

---

## 5. The rule

An evidence check should answer *did the control act?* — not *is the file
there?* In order of strength:

1. **A record that the control acted** — an audit row, a decision log, a scan
   result. Strongest, and what the 43 DB-row collectors already do.
2. **Exercising the control** — call it and assert the outcome, as
   `mcp_authz_evidence.py` does. Use when there is no record to count.
3. **Parsing configuration** — weak, but bounded: assert structure and values,
   never a substring.
4. **File existence** — not evidence. If it is genuinely the best available,
   say so in the evidence description, and never let it report the top maturity
   or `satisfied` on its own.

And when a control only holds on part of the system, **scope the claim and
record the scope-out with its rationale and compensating controls**. A scoped,
accurate claim is defensible. An unscoped, inaccurate one is not.
