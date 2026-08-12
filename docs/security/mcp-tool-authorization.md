# MCP Per-Tool Authorization — Three Surfaces, Three Answers

CUI // SP-CTI

**Task:** exa-policy-05 · **Control:** AC-3 / AC-6 · **ADR:** D261

ICDEV™ exposes MCP three ways. "Enforce `MCPToolAuthorizer` at MCP dispatch"
is not a single change, because only one of the three surfaces has a principal
to authorize. This document records which is which and why, so the question
does not get re-litigated from scratch.

## 1. stdio — `tools/mcp/unified_server.py` — NOT ENFORCED (deliberate)

Claude Code and local agents. **No caller identity exists on this surface.**
Grep `unified_server.py` and `base_server.py` for `role`, `actor`, `principal`
or `auth` and the only hits are LLM message roles. A role supplied over stdio
would therefore be supplied *by the caller about itself* — an assertion, not an
authentication — and an authorization check reading it is theatre.

The caller is also, concretely, the developer at the keyboard, who already has
shell access to the whole repo. Refusing them a tool they can invoke directly
costs a turn and buys nothing.

The shipped D261 matrix confirms the fit is wrong here independently: `developer`
allows 8 tools out of roughly 700, and `admin` is a bare wildcard. A local
session would either deny essentially everything or run as admin. Both are
useless.

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
(`tools/security/mcp_tool_authorizer.py`) reading
`args/owasp_agentic_config.yaml::mcp_authorization`. `mcp_http.py` keeps **no**
role/tool matrix of its own.

### Role mapping

SaaS tenant roles (`tools/saas/models.py::UserRole`) are not the D261 role
vocabulary. `SAAS_ROLE_TO_RBAC_ROLE` maps them rather than forking a second
matrix:

| SaaS role | D261 role | Effect |
|---|---|---|
| `tenant_admin` | `admin` | wildcard — allowed everything |
| `developer` | `developer` | 0 of the 19 registry tools |
| `compliance_officer` | `isso` | ssp/poam/stig/sbom/nist_lookup |
| `viewer` | *(unmapped)* | unknown role → `default_policy` → deny |
| `auditor` | *(unmapped)* | unknown role → `default_policy` → deny |

`viewer` and `auditor` are left unmapped **on purpose**. They have no D261
equivalent, and deny is the safe direction; monitor mode is how we find out
whether real tenant traffic depends on them before that becomes binding.

## Monitor → enforce

```bash
# shipped default: log would-be denials, let the call through
ICDEV_SAAS_MCP_AUTHZ_MODE=monitor

# make denials binding
ICDEV_SAAS_MCP_AUTHZ_MODE=enforce
```

An unrecognised value falls back to `monitor` — a typo must not silently
disable the audit trail.

Monitor is the shipped default because the table above shows the D261 matrix
does not yet fit this surface: `developer` would lose every tool and
`viewer`/`auditor` would lose all access on the day enforcement flipped.

**Before flipping to `enforce`, read the evidence:**

```sql
SELECT details->>'role'  AS role,
       details->>'tool'  AS tool,
       details->>'surface' AS surface,
       count(*)
FROM audit_platform
WHERE event_type = 'mcp.authz' AND action = 'mcp.tool.would_deny'
GROUP BY 1, 2, 3
ORDER BY count(*) DESC;
```

Rows whose `surface` starts with `tests/` are test residue, not findings — the
audit trail is append-only, so the schema-parity test names itself there rather
than cleaning up after itself.

If that query returns tools real tenants depend on, the fix is to correct the
role declarations, **not** to widen the mapping here. exa-policy-07 moves role
and IL declarations into the MCP registry; when it lands,
`SAAS_ROLE_TO_RBAC_ROLE` is the one thing that goes away.

## Tests

`tests/test_exa_policy_05_saas_mcp_authz.py` — 39 tests, including a DENY case
for every SaaS role that can be denied. `tenant_admin` has none because `admin`
is a wildcard; `test_tenant_admin_is_allowed_by_wildcard` pins that as a
deliberate policy outcome rather than a gap in the table.

The file is also on `tests/pg_tier_allowlist.txt`: `audit_platform` is a
PostgreSQL-side table absent from the SQLite `icdev.db`, so the audit-write
schema-parity test only ever executes in the PG tier. `_write_authz_audit`
swallows its own exceptions by design — an audit outage must not 500 a call the
policy allowed — which means a column missing from the live schema would make
every monitor-mode row vanish silently while monitor mode reported a clean
board. The PG tier is the only place that failure mode is visible.
