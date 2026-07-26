# CUI // SP-CTI

# Agent Browser — Four-Seam Agent-Tool Registration (oss-browse-03)

**Status:** shipped
**Task:** `oss-browse-03`
**Modules:** `tools/browser/session.py` (new), `tools/agent_toolkit/_browser.py` (new),
`tools/agent_toolkit/__init__.py`, `tools/mcp/tool_registry.py`, `tools/mcp/gap_handlers.py`,
`tools/ace/agent_tools.py`, `tools/browser/agent_tools.py`
**Config:** `args/agent_toolsets.yaml` (`bundles.browser`), `args/owasp_agentic_config.yaml`
(`browser_*` denies), `args/security_gates.yaml` (`BROWSER-SEC-001`)
**Tests:** `tests/browser/test_browser_tool_registration.py` (174 tests, driver-free)

---

## The gap this closes

oss-browse-01 shipped the capability. It shipped it into exactly **one** consumer:
`BrowserToolRegistry`, wired by hand into an agent loop by a caller who already
owned an `AgentBrowser` instance.

ICDEV has **four parallel agent-tool registries**, and registering in the wrong
one strands the capability *silently* — nothing errors, no test fails, the tool
simply is not there for the agent that needed it:

| # | Seam | Consumer |
|---|------|----------|
| 1 | `tools/agent_toolkit/__init__.py` | in-process callers, alongside `read_file` / `execute_shell` |
| 2 | `TOOL_REGISTRY` + `gap_handlers.py` | the MCP gateway (and, derived from it, the SAG `ToolSpec`) |
| 3 | `args/agent_toolsets.yaml` | the standalone agent's bundles |
| 4 | `tools/ace/agent_tools.py` | ACE co-workers |

## The design problem: indices are per-instance state

`browser_click(14)` only means something relative to the `browser_read_state`
that produced index 14. But three of the four seams dispatch one **stateless**
function call at a time and have nowhere to keep an `AgentBrowser` between calls.

`tools/browser/session.py` is that missing piece: a process-local registry of
*named* sessions, plus one set of seam-neutral operations that every seam calls.
It is a **library, no CLI**.

```python
from tools.agent_toolkit import browser_navigate, browser_click, browser_close

try:
    state = browser_navigate("http://localhost:5050/kanban")   # session "default"
    browser_click(24)                                          # index from state
finally:
    browser_close()
```

## One implementation, four registrations

Registering four times but implementing **once** is the whole point of the task.
The failure this guards against is not a missing registration — it is a
*divergent* one: a second, unguarded path to a driver behind one seam (a raw
`get_driver()` that skips `GuardedDriver`) would silently void the allowlist, the
action budget, and the audit trail while all four registrations still looked
correct.

So every seam resolves to the same `session.py` operations, and every operation
goes through `AgentBrowser` → `scope.GuardedDriver`. The test asserts function
**identity**, not equivalent behaviour:

```python
assert getattr(toolkit, name) is getattr(session, name)
```

Two supporting moves keep the seams from drifting apart:

- `tools.browser.session.BROWSER_TOOL_NAMES` is the single tool-name vocabulary
  all four seams register against. A rename that misses a seam fails
  `test_all_four_seams_expose_the_same_tool_names`, not production.
- `browser_schemas()` in `tools/browser/agent_tools.py` is the public accessor
  ACE *merges* rather than restating, so a description fixed in one place is
  fixed everywhere.

`browser_close` was added to the vocabulary here (oss-browse-01 had seven tools):
once a broker holds sessions open across stateless calls, an agent needs a way to
release the real process it is holding.

## The oss-fix-01 lesson, re-asserted

A `TOOL_REGISTRY` entry whose handler does not exist is **silently replaced by an
error stub** in `unified_server.py`. The tool is advertised, answers plausibly,
and never works.

The suite resolves every handler for real — and then goes one step further,
exercising the production resolver rather than just `importlib`:

```python
server = UnifiedMCPServer()
handler = server._resolve_handler(name, TOOL_REGISTRY[name])
assert getattr(handler, "__name__", "") != "_stub"
```

## Enforcement

A browser is a general-purpose egress channel that can reach any allowlisted host
and type broker-resolved credentials into it, so it is gated like a
state-changing tool rather than a read-only query.

| Layer | Control |
|-------|---------|
| SAG dispatch | `default_safety_gate` denies every browser tool unless `ICDEV_SAG_ALLOW_MUTATION` is set. |
| RBAC | `browser_*` is an **explicit deny** for `pm` / `developer` / `isso` / `co`. `MCPToolAuthorizer` evaluates deny before allow, so a future wildcard broadening cannot grant it by accident. `admin` is allowed. |
| Navigation | `args/browser_scope.yaml` — default-deny allowlist, loopback only out of the box. |
| Budget + audit | `ActionBudget` caps actions/failures/wall-clock; one `audit_trail` row per action, carrying the session name as `run_id`. |
| Resource | `ICDEV_BROWSER_MAX_SESSIONS` (default 4) caps concurrent live drivers per process; `SessionLimitExceeded` stops a looping agent exhausting host memory. |

Two enforcement details are load-bearing and easy to get wrong:

**The RBAC gate had to be wired, not merely declared.** `_mcp_authz_gate` is
opt-in *per handler* in `gap_handlers.py`. Declaring `browser_*` denies in
`args/owasp_agentic_config.yaml` without calling it would have left the policy
inert while reading as enforced — the tool governed on paper, reachable by any
role in practice. Both halves are pinned by tests. The gate also runs **before**
argument validation, so a denied caller cannot use error messages to enumerate
the tool surface.

**The whole surface is mutating, including `browser_read_state` and
`browser_screenshot`.** Neither changes the page, but both are how untrusted
remote content enters the model's context, and `discovery.py`'s read-only
heuristic cannot tell a browser read from a filesystem read. Marking either
`read_only` would skip `default_safety_gate` entirely. Gating the bundle as one
unit is the fail-closed choice: approve the session, not each glance at it.

## Why the tests are driver-free

Every assertion is about *registration* — resolution, schema shape, gating — so
the suite runs on a host with no browser driver at all. Argument validation
happens before any driver is created, which is both a security property (see
above) and what keeps the suite hermetic.

## Deliberate non-adoptions

- **No new sandbox.** Recorded as **bypass-documented** in
  `docs/security/sandbox-coverage.md` Gap 39, which also discharges the explicit
  revisit trigger Gap 38 left for exactly this change ("*`AgentBrowser` is exposed
  through … an MCP tool*"). `session.py` is a dict lookup plus eight thin
  delegations — it never parses, renders, or executes page content. The control
  for four-seam divergence is single-implementation plus cross-seam tests, not a
  container.
- **The browser is opt-in everywhere.** Absent from ACE's default tool set and
  from every other bundle. A co-worker gets it only by naming it in
  `icdev_tools`; a standalone agent only by requesting the `browser` bundle *and*
  setting `ICDEV_SAG_ALLOW_MUTATION`.
- **Sessions are process-local and never shared across processes.** The MCP
  gateway, an ACE co-worker thread, and an in-process toolkit call each see their
  own registry. ACE keys sessions by co-worker instance id, so two concurrent
  co-workers can never address each other's element indices.

## Residual risk

Unchanged from Gap 38: indirect prompt injection via page text. Broadened reach
does not deepen that risk, but it does widen *who* can incur it — which is what
the deny-first RBAC and the mutating-by-default gate are there to bound. Note
that `ICDEV_MCP_AUTHZ_BYPASS=1` disables the RBAC gate wholesale; it is a
dev/test opt-out and must never be set in a deployed configuration.

## Verification

```bash
python -m pytest tests/browser/test_browser_tool_registration.py -q   # 174 passed
python tools/workflow/coherence_checker.py --check sandbox_coverage --json
python tools/workflow/coherence_checker.py --check doc_command_paths --json
```

Gate: `BROWSER-SEC-001` in `args/security_gates.yaml`.
