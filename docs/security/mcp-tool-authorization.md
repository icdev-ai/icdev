# MCP Per-Tool Authorization — One Declaration, Three Surfaces

CUI // SP-CTI

**Tasks:** exa-policy-05, exa-policy-07 · **Control:** AC-3 / AC-6 · **ADR:** D261

ICDEV™ exposes MCP three ways. "Enforce `MCPToolAuthorizer` at MCP dispatch"
is not a single change, because only one of the three surfaces has a principal
to authorize. This document records which is which and why, so the question
does not get re-litigated from scratch.

## 0. Where the policy lives (exa-policy-07)

`min_il` and `required_roles` are declared **once per tool**, in
`tools/mcp/tool_registry.py`, and every surface below reads that one
declaration.

Before exa-policy-07 there were two, and they disagreed.
`agent_tool_gate.tool_limits` already had the inheritance path — it asks the
registry what a tool's limits are and lets the stricter of the two win — but the
registry declared *neither* field, so every tool fell through to
`default_min_il` (the CUI/IL4 platform baseline) with no role limit at all.
Meanwhile `args/owasp_agentic_config.yaml` carried a hand-written
`role_tool_matrix` that had gone stale in the opposite direction: `developer`
allowed 8 tools out of roughly 700. Two RBACs that disagree are worse than one
that is switched off, so the matrix is retired and the registry is the source.

### How a tool's declaration is decided

Resolution order, first match wins (`tool_registry.tool_authorization`):

| # | Condition | `min_il` | `required_roles` |
|---|---|---|---|
| 1 | named in `AUTHZ_OVERRIDES` | as declared | as declared |
| 2 | not a registered tool | IL5 | `admin` |
| 3 | no `read_only` declaration | IL5 | `admin` |
| 4 | `read_only: True`, in no mutating bundle | IL4 | *(none)* |
| 5 | otherwise | IL4 | `CATEGORY_WRITE_ROLES[category]` |

Three declarations that already exist feed the derivation, so nobody hand-wrote
roles for ~520 tools:

- **`READ_ONLY_DECLARATIONS`** — does the handler mutate state.
- **`mutating: true` bundle membership** in `args/agent_toolsets.yaml` — an
  independent second claim. It is not redundant: `browser_read_state` is
  declared read-only (it only reads the page) yet sits in the mutating `browser`
  bundle, because reading through a *driven* browser is not a pure read. The
  stricter signal wins.
- **`category`** — which domain the tool acts in, and therefore which role owns
  mutating it. `CATEGORY_WRITE_ROLES` is ~70 rows keyed by a field the generator
  already writes, instead of ~520 rows keyed by tool name. A tool added to an
  existing category inherits its roles automatically, which is exactly what the
  per-tool matrix could not do.

**Restrictive by default.** An unmapped category, a missing `read_only`
declaration, or no registry entry at all resolves to IL5 / admin-only. A
too-strict new tool is a refusal somebody reports; a too-loose one is silent.
`mcp_tool_authorizer.py --validate` warns when any tool has fallen through, and
`tests/test_exa_policy_07_registry_authorization.py` fails when a category
exists in the registry and not in `CATEGORY_WRITE_ROLES`.

### What this tightened

Two behaviour changes worth knowing about, both deliberate:

- A Studio `mcp` step resolves a caller with **no roles** unless the run or
  `ICDEV_MCP_CALLER_ROLES` declares them. Registry-declared roles therefore bind
  on that surface too. The 17 read-only tools in
  `mcp_workflow_tools.allowed` carry no role limit and are unaffected; the three
  report-only *writers* in that list (`stig_check`, `code_analyze`,
  `scan_dependencies`) are declared `required_roles: ()` in `AUTHZ_OVERRIDES`
  for exactly this reason. The `requires_approval` tier does now require a role
  — those calls already needed a human gate, and the declaration now says *who*.
- The infrastructure, credential and marketplace tools moved from IL4 to
  **IL5**. `terraform_apply`, `k8s_deploy`, `ansible_run`, `rollback`,
  `sandbox_execute`, `send_command`, `self_heal`, `install_asset`,
  `proxy_key_issue`, `studio_run_start` and the rest of `AUTHZ_OVERRIDES` are
  refused to an IL4 run. No shipped workflow template dispatches them through an
  `mcp` node (they run as `node_type: tool`), so the blast radius is a
  deployment that had wired one up itself.

### Changing the policy

```bash
python tools/security/mcp_tool_authorizer.py --validate --json
python tools/security/mcp_tool_authorizer.py --list --role developer --json
python tools/security/mcp_tool_authorizer.py --check --role developer --tool terraform_apply --json
```

Edit `CATEGORY_WRITE_ROLES` for a whole domain, `AUTHZ_OVERRIDES` for one tool.
Every override carries a `why`; the test suite fails an override without one.
Do **not** reintroduce `role_tool_matrix` in `args/owasp_agentic_config.yaml` —
setting that key puts `MCPToolAuthorizer` back into matrix mode and the two
sources diverge again.

## 1. stdio — `tools/mcp/unified_server.py` — NOT ENFORCED (deliberate)

Claude Code and local agents. **No caller identity exists on this surface.**
Grep `unified_server.py` and `base_server.py` for `role`, `actor`, `principal`
or `auth` and the only hits are LLM message roles. A role supplied over stdio
would therefore be supplied *by the caller about itself* — an assertion, not an
authentication — and an authorization check reading it is theatre.

The caller is also, concretely, the developer at the keyboard, who already has
shell access to the whole repo. Refusing them a tool they can invoke directly
costs a turn and buys nothing.

The registry declarations confirm the fit is wrong here independently: `admin`
reaches everything and every other role is bounded by domain, so a local session
would either self-assert `admin` (and the check buys nothing) or self-assert
something narrower (and the check buys nothing, because it could have said
`admin`).

What actually bounds this surface, and does work:

| Control | File |
|---|---|
| Reversibility classifier | `tools/agent_runtime/approval_gate.py` |
| Hard blocks | `.claude/hooks/pre_tool_use.py` |
| Path tiers | `args/file_access_tiers.yaml` |

**Do not add a role check here without first adding a way to authenticate the
caller.**

## 2. Studio `agent` / `mcp` nodes — ALREADY ENFORCED

`tools/studio/executors/agent_tool_gate.py`, gate AGENT-WF-001: default-deny,
checked at offer time *and* call time, per-tool `min_il` and `required_roles`,
every decision written to the append-only `studio_mcp_dispatch_audit`.

Those per-tool limits are read from the registry declarations in §0 via
`mcp_executor.tool_requirements`, combined with the owning component's
`min_il` / `default_roles` in `args/component_registry.yaml`. The stricter
impact level of the two wins; roles do **not** merge — a component's
`default_roles` replaces the registry declaration, because "hold any one of
these" gets weaker as the set grows, and the component is the more specific
claim (it names a canvas a principal can also be granted access to).

**No second gate is added beside it.** `tests/test_exa_policy_05_saas_mcp_authz.py
::test_no_second_gate_beside_agent_tool_gate` pins that.

## 3. `tools/saas/mcp_http.py` — ENFORCED HERE

MCP over HTTP for tenants, and the one surface with a real authenticated
principal: the gateway auth middleware sets `g.tenant_id`, `g.user_id` and
`g.user_role` before the blueprint runs.

Two checkpoints, mirroring the agent_tool_gate rationale:

- **offer time** — `tools/list` and `GET /mcp/v1/tools` do not advertise a tool
  the caller may not call. The convenience endpoint is filtered too; otherwise
  it just routes around the `tools/list` filter.
- **call time** — `_dispatch_tool` refuses before importing the tool module.
  The check lives in `_dispatch_tool` rather than at its call site because that
  is the single chokepoint through which a tool actually executes.

A refusal is a JSON-RPC error (`-32003`), not an `isError: true` content blob:
"you may not call this at all" is not a tool result.

The decision itself is delegated to `MCPToolAuthorizer`
(`tools/security/mcp_tool_authorizer.py`) reading the registry declarations in
§0. `mcp_http.py` keeps **no** role/tool policy of its own.

Impact level is **not** evaluated on this surface. The declaration carries
`min_il`, but the gateway middleware authenticates a *role*, not an impact
level; §2 is where `min_il` binds.

### Role mapping

SaaS tenant roles (`tools/saas/models.py::UserRole`) are folded into the
canonical vocabulary by `tool_registry.normalize_role`, whose `ROLE_ALIASES`
table is the only copy. `mcp_http.py` used to carry a local
`SAAS_ROLE_TO_RBAC_ROLE`; exa-policy-07 removed it, and `mcp_http.py` now passes
the role through verbatim so a role outside the vocabulary stays unrecognised
rather than being silently upgraded to one that is in it.

| SaaS role | Canonical role | Effect on the 19 tools this surface exposes |
|---|---|---|
| `tenant_admin` | `admin` | all 19 |
| `developer` | `developer` | read tiers + `sast_scan`, `dependency_audit` |
| `compliance_officer` | `isso` | read tiers + ssp/poam/stig/sbom/cards/fairness/GAO |
| `viewer` | *(unmapped)* | unrecognised role → `default_policy` → deny |
| `auditor` | *(unmapped)* | unrecognised role → `default_policy` → deny |

`viewer` and `auditor` are left unmapped **on purpose**. They have no D261
equivalent, and deny is the safe direction; monitor mode is how we find out
whether real tenant traffic depends on them before that becomes binding.

Three of the 19 tools this surface exposes (`sast_scan`, `dependency_audit`,
`gao_evidence_build`) are surface-local names with no `TOOL_REGISTRY` entry.
Without a declaration they would resolve to the restrictive default and a tenant
`developer` would silently lose them, so they are declared in `AUTHZ_OVERRIDES`
— in the registry, not here, so there is still exactly one place to read.

## Monitor → enforce

```bash
# shipped default: log would-be denials, let the call through
ICDEV_SAAS_MCP_AUTHZ_MODE=monitor

# make denials binding
ICDEV_SAAS_MCP_AUTHZ_MODE=enforce
```

An unrecognised value falls back to `monitor` — a typo must not silently
disable the audit trail.

Monitor is still the shipped default. exa-policy-07 fixed the worst of what the
old matrix got wrong here — `developer` no longer loses every tool — but
`viewer` and `auditor` still lose all access on the day enforcement flips, and
that is a decision to make against real traffic, not against this table.

**Before flipping to `enforce`, read the evidence.** All rows carry
`event_type = 'mcp.authz'`; the `action` distinguishes the surface and the mode:

| `action` | Meaning |
|---|---|
| `mcp.tool.would_deny` | monitor: a `tools/call` that enforce would refuse |
| `mcp.tool.denied` | enforce: a `tools/call` that was refused |
| `mcp.tools_list.would_filter` | monitor: tools enforce would have hidden |
| `mcp.tools_list.filtered` | enforce: tools that were hidden |

The `tools/call` denials are the ones that matter — they are calls a real
client actually tried to make:

```sql
SELECT details->>'role'    AS role,
       details->>'tool'    AS tool,
       count(*)
FROM audit_platform
WHERE event_type = 'mcp.authz' AND action = 'mcp.tool.would_deny'
GROUP BY 1, 2
ORDER BY count(*) DESC;
```

A list filter is recorded as **one row naming every withheld tool**, not one
row per tool. A deny-all role listing this registry would otherwise emit 19
rows and 19 DB round-trips on every read, burying the call denials above.

```sql
SELECT details->>'role' AS role,
       details->>'surface' AS surface,
       max((details->>'withheld_count')::int) AS withheld,
       count(*) AS list_calls
FROM audit_platform
WHERE event_type = 'mcp.authz' AND action LIKE 'mcp.tools_list.%'
GROUP BY 1, 2;
```

Rows whose `surface` starts with `tests/` are test residue, not findings — the
audit trail is append-only, so the schema-parity test names itself there rather
than cleaning up after itself.

If that query returns tools real tenants depend on, the fix is to correct the
role declarations in `tools/mcp/tool_registry.py`, **not** to widen the alias
table. Widening the alias table hands a role every privilege the role it aliases
to has, across all three surfaces; correcting a declaration changes one tool.

## Tests

`tests/test_exa_policy_07_registry_authorization.py` — the declarations
themselves: every tool resolves, an undeclared tool is restrictive, every
override states a reason, every registry category is mapped, and the three
surfaces agree on one answer.

`tests/test_exa_policy_05_saas_mcp_authz.py` — 41 tests, including a DENY case
for every SaaS role that can be denied. `tenant_admin` has none because `admin`
reaches every tool; `test_tenant_admin_is_allowed_by_wildcard` pins that as a
deliberate policy outcome rather than a gap in the table.

`tests/studio/test_mcp_executor_rbac.py` — what component ownership contributes
on top of the registry declaration, and which of the two wins.

Listed in `args/ci_test_files/core.txt` so the required `Test` job runs it —
the PG tier is not a required check, so registering there alone would have let
an authorization regression merge green.

The file is also on `tests/pg_tier_allowlist.txt`: `audit_platform` is a
PostgreSQL-side table absent from the SQLite `icdev.db`, so the audit-write
schema-parity test only ever executes in the PG tier. `_write_authz_audit`
swallows its own exceptions by design — an audit outage must not 500 a call the
policy allowed — which means a column missing from the live schema would make
every monitor-mode row vanish silently while monitor mode reported a clean
board. The PG tier is the only place that failure mode is visible.
