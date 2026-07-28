"""MCP Tool Executor — generic Studio workflow step.

Dispatches any tool declared in ``tools/mcp/tool_registry.py::TOOL_REGISTRY``
so a workflow step can reach every registered tool without a hand-written
executor per integration.

Dispatch is **in-process**: the registry entry's ``module`` is imported with
``importlib`` and its ``handler`` called directly. No MCP server is started and
no stdio transport is opened — this is the same lazy-import path
``mcp/unified_server.py::_resolve_handler`` uses, minus the protocol layer.

Usage::

    python tools/studio/executors/mcp_executor.py --tool health_check --params '{}'

Contract (matches the runner's expectations, see workflow_runner._exec_step):
  stdout  = single-line JSON object
  exit 0  = handler ran and returned
  exit 1  = refused by the gate, unknown tool, invalid params, or handler raised

One deliberate divergence from the MCP protocol layer: unified_server catches a
raising handler and returns ``{"error": ...}`` as a *successful* tool call. Here
that exits 1, because a step whose handler blew up must fail the run rather than
pass a success record with an error buried in the payload.

Authorization (dwo-mcp-02)
--------------------------
Every dispatch passes the ``mcp_workflow_tools`` allowlist in
``args/security_gates.yaml`` (gate MCP-WF-001) before the registry is touched,
so a refused tool is never imported and its handler never loaded. The policy is
**default-deny**: a tool runs only if it is named in ``allowed``. Anything else
raises :class:`MCPWorkflowGateError`.

The gate is fail-closed — a missing, unparseable, or non-default-deny policy
refuses every tool rather than dispatching unchecked. There is deliberately no
bypass argument: ``run()`` is the only dispatch path and it always gates.

IL and RBAC limits (dwo-mcp-02-d3)
----------------------------------
An allowlisted tool is then checked against the caller: the caller's impact
level must meet the tool's ``min_il``, and the caller must hold a role the tool
requires. Limits are **not** restated in the gates file — they come from
``args/component_registry.yaml`` (``min_il`` / ``default_roles``) via the
component that owns the tool's handler module, so the workflow surface and the
HTTP canvas gate enforce one policy. Role checks fall back to an explicit
``canvas_access`` grant before refusing. A tool no component owns runs at the
platform baseline (IL4, no role limit).

Scope of the RBAC half today: none of the 29 tools currently on the allowlist
live inside a canvas package (they are all ``tools.mcp.*`` servers), so no role
limit applies to them yet — the check goes live the moment a canvas-owned tool
is allowlisted. The IL half is live now: a run whose caller context declares
IL2 cannot dispatch an IL4 platform tool.

Still to land on top of this: the human-approval path that makes
``requires_approval`` tools reachable (d4), and append-only audit of every
attempt (d5). Until d4, a ``requires_approval`` tool is refused with its own
reason rather than silently treated as unknown.
"""
from __future__ import annotations

import argparse
import difflib
import importlib
import json
import os
import sys
import time
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[3]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

# Reserved key prefix for step results in run memory (dwo-mem-01).
MEMORY_KEY_PREFIX = "step:"

_MAX_SUGGESTIONS = 5

# ── Authorization gate (dwo-mcp-02, gate MCP-WF-001) ───────────────────────

GATES_FILENAME = "security_gates.yaml"

#: Top-level key holding the workflow allowlist inside the gates file.
GATE_POLICY_KEY = "mcp_workflow_tools"

#: Parsed policies, keyed by the path they came from. Cleared by ``refresh=True``.
_POLICY_CACHE: dict[str, dict] = {}


class MCPWorkflowGateError(RuntimeError):
    """A tool was refused by the MCP workflow allowlist, or the policy is unusable.

    ``reason`` carries the MCP-WF-001 block condition so the CLI can report it
    as ``error_type`` and d5 can audit it without re-parsing the message.
    """

    def __init__(self, message: str, *, tool: str = "", reason: str = ""):
        super().__init__(message)
        self.tool = tool
        self.reason = reason


def _candidate_gate_paths() -> list[Path]:
    """Gate-file locations to probe, nearest ancestor first.

    Both ``<root>/args/`` and ``<root>/data/args/`` are probed at every level so
    this resolves from the repo checkout, from the ``icdev/`` package mirror, and
    from a pip-installed wheel where the file ships as package data. Mirrors the
    strategy in ``tools/config/component_registry.py::_find_repo_root``.
    """
    here = Path(__file__).resolve()
    paths: list[Path] = []
    for parent in here.parents:
        for rel in (("args",), ("data", "args")):
            candidate = parent.joinpath(*rel, GATES_FILENAME)
            if candidate not in paths:
                paths.append(candidate)
    return paths


def _parse_policy(path: Path) -> dict | None:
    """Return the policy section of ``path``, or None if absent/unreadable.

    None means "keep looking" — several gate files exist in a checkout and only
    the authoritative one declares this section. Never returns a policy that is
    not default-deny: an edited ``default`` raises rather than being ignored,
    because silently enforcing a stricter rule than the file states hides the
    edit from whoever made it.
    """
    try:
        import yaml  # noqa: PLC0415
    except ImportError:
        return None
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except (OSError, ValueError, yaml.YAMLError):
        return None

    policy = data.get(GATE_POLICY_KEY) if isinstance(data, dict) else None
    if not isinstance(policy, dict):
        return None

    default = str(policy.get("default", "")).strip().lower()
    if default != "deny":
        raise MCPWorkflowGateError(
            f"'{GATE_POLICY_KEY}.default' is {default or '(unset)'!r} in {path}, "
            f"expected 'deny'. This executor implements a default-deny allowlist "
            f"only and will not guess what the edited policy permits.",
            reason="gate_policy_unavailable",
        )
    return policy


def load_gate_policy(path: str | Path | None = None, *, refresh: bool = False) -> dict:
    """Return the ``mcp_workflow_tools`` policy.

    Args:
        path: Read this gate file instead of probing for one.
        refresh: Bypass the cache and re-read from disk.

    Raises:
        MCPWorkflowGateError: if no readable default-deny policy is found. The
            gate is fail-closed: without a policy nothing dispatches.
    """
    candidates = [Path(path)] if path else _candidate_gate_paths()
    cache_key = str(candidates[0]) if path else GATE_POLICY_KEY
    if not refresh and cache_key in _POLICY_CACHE:
        return _POLICY_CACHE[cache_key]

    for candidate in candidates:
        policy = _parse_policy(candidate)
        if policy is not None:
            policy = {**policy, "_source": str(candidate)}
            _POLICY_CACHE[cache_key] = policy
            return policy

    raise MCPWorkflowGateError(
        f"Cannot enforce the MCP workflow allowlist: no '{GATE_POLICY_KEY}' "
        f"section found in any {GATES_FILENAME} (looked in "
        f"{', '.join(str(p.parent) for p in candidates[:4])}), or PyYAML is not "
        f"installed. Refusing to dispatch — the gate is fail-closed.",
        reason="gate_policy_unavailable",
    )


def _tool_set(policy: dict, key: str) -> frozenset[str]:
    """Return one of the policy's tool lists as a set, tolerating null/absent."""
    return frozenset(str(t) for t in (policy.get(key) or []))


def allowed_tools(policy: dict | None = None) -> frozenset[str]:
    """Tools dispatchable from a workflow step with no human gate."""
    return _tool_set(policy if policy is not None else load_gate_policy(), "allowed")


def approval_tools(policy: dict | None = None) -> frozenset[str]:
    """Tools that need an approved human gate before dispatch (reachable in d4)."""
    return _tool_set(
        policy if policy is not None else load_gate_policy(), "requires_approval"
    )


def check_tool_allowed(tool: str, policy: dict | None = None) -> None:
    """Refuse ``tool`` unless the allowlist names it. Returns None when allowed.

    Raises:
        MCPWorkflowGateError: always names the tool, so the refusal is
            actionable from the step's stdout alone.
    """
    policy = policy if policy is not None else load_gate_policy()

    if tool in allowed_tools(policy):
        return

    if tool in approval_tools(policy):
        raise MCPWorkflowGateError(
            f"MCP tool '{tool}' is state-changing and requires an approved "
            f"human gate in the same run before it can be dispatched "
            f"({GATE_POLICY_KEY}.requires_approval). Workflow approval gates "
            f"are not wired yet (dwo-mcp-02-d4), so it is refused.",
            tool=tool,
            reason="mcp_tool_awaiting_human_approval",
        )

    # Suggest from the allowlist, not the registry: a typo of an allowlisted
    # tool is the common case, and naming it costs no registry import.
    close = _closest(tool, sorted(allowed_tools(policy)))
    hint = f" Closest allowlisted tools: {', '.join(close)}." if close else ""
    raise MCPWorkflowGateError(
        f"MCP tool '{tool}' is not allowlisted for workflow steps. The "
        f"{GATE_POLICY_KEY} policy is default-deny: add '{tool}' to its "
        f"'allowed' list in {GATES_FILENAME} (read-only tools only) or to "
        f"'requires_approval' (state-changing tools) to make it dispatchable."
        + hint,
        tool=tool,
        reason="mcp_tool_not_allowlisted",
    )


# ── Registry lookup ────────────────────────────────────────────────────────

def resolve_entry(tool: str) -> dict:
    """Return the TOOL_REGISTRY entry for ``tool``.

    Raises LookupError with the closest matching names when unknown.
    """
    from tools.mcp.tool_registry import RESOURCE_REGISTRY, TOOL_REGISTRY

    entry = TOOL_REGISTRY.get(tool)
    if entry:
        return entry

    if tool in RESOURCE_REGISTRY:
        raise LookupError(
            f"'{tool}' is an MCP resource, not a tool — this executor dispatches "
            f"TOOL_REGISTRY entries only"
        )

    raise LookupError(_unknown_tool_message(tool, list(TOOL_REGISTRY)))


def _closest(tool: str, names: list[str]) -> list[str]:
    """Return the names most likely meant by ``tool``, best first."""
    close = difflib.get_close_matches(tool, names, n=_MAX_SUGGESTIONS, cutoff=0.6)
    if not close:
        lowered = tool.lower()
        close = [n for n in names if lowered in n.lower()][:_MAX_SUGGESTIONS]
    return close


def _unknown_tool_message(tool: str, names: list[str]) -> str:
    """Build an unknown-tool error listing the closest registry names."""
    close = _closest(tool, names)
    msg = f"Unknown MCP tool '{tool}' ({len(names)} tools registered)"
    if close:
        msg += ". Closest matches: " + ", ".join(close)
    return msg


# ── Param validation ───────────────────────────────────────────────────────

def parse_params(raw: str) -> dict:
    """Parse the --params JSON string into a dict."""
    raw = (raw or "").strip()
    if not raw:
        return {}
    try:
        value = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError(f"--params is not valid JSON: {exc}") from exc
    if not isinstance(value, dict):
        raise ValueError(
            f"--params must be a JSON object, got {type(value).__name__}"
        )
    return value


def validate_params(params: dict, schema: dict) -> list[str]:
    """Return a list of human-readable schema violations (empty == valid)."""
    if not schema:
        return []
    try:
        import jsonschema  # noqa: PLC0415
    except ImportError:
        return []  # validation is best-effort; dispatch still guarded by the handler

    validator = jsonschema.Draft7Validator(schema)
    errors = []
    for err in sorted(validator.iter_errors(params), key=lambda e: list(e.path)):
        field = ".".join(str(p) for p in err.path) or "(root)"
        errors.append(f"{field}: {err.message}")
    return errors


# ── Caller IL and RBAC limits (dwo-mcp-02-d3) ──────────────────────────────

#: Run-memory key holding the run's principal (see ``resolve_caller``).
CALLER_KEY = "caller"

#: Impact level assumed for a run that declares no caller. Matches the default
#: in ``canvas_access._has_sufficient_il``: an undeclared principal operates at
#: the deployment's own level, not at a privileged one.
DEFAULT_CALLER_IL = "IL4"

#: Impact level required by a tool that no registry component owns. ICDEV's
#: platform baseline is CUI/IL4, so a platform tool is treated as IL4 rather
#: than as unclassified.
DEFAULT_TOOL_MIN_IL = "IL4"

#: Environment fallbacks for the caller's IL, first match wins.
CALLER_IL_ENV = ("ICDEV_MCP_CALLER_IL", "ICDEV_IMPACT_LEVEL")

#: Environment fallback for the caller's roles (comma-separated).
CALLER_ROLES_ENV = "ICDEV_MCP_CALLER_ROLES"


def _il_order() -> dict:
    """Return the platform's impact-level ordering.

    Imported from ``canvas_access`` rather than restated: one ordering for the
    HTTP canvas gate and the workflow gate, so raising a canvas to IL5 cannot
    leave the workflow surface enforcing the old order.
    """
    try:
        from tools.security.canvas_access import _IL_ORDER  # noqa: PLC0415
    except ImportError as exc:
        raise MCPWorkflowGateError(
            f"Cannot evaluate impact-level limits: tools.security.canvas_access "
            f"is unimportable ({exc}). Refusing to dispatch — the gate is "
            f"fail-closed.",
            reason="gate_policy_unavailable",
        ) from exc
    return _IL_ORDER


def _normalize_roles(value) -> tuple[str, ...]:
    """Coerce a roles value (list, tuple, or comma-separated string) to a tuple."""
    if not value:
        return ()
    if isinstance(value, str):
        parts = value.split(",")
    else:
        parts = list(value)
    return tuple(str(p).strip() for p in parts if str(p).strip())


def read_caller_context(run_id: str) -> dict:
    """Return the run's declared caller from run memory, or ``{}``.

    Soft dependency, like :func:`write_run_memory`: a run whose trigger surface
    never wrote a ``caller`` key falls through to the environment defaults
    rather than failing. Absence is not treated as an error because no trigger
    surface writes this key yet.
    """
    if not run_id:
        return {}
    try:
        from tools.studio import run_memory  # noqa: PLC0415
    except ImportError:
        return {}
    try:
        value = run_memory.get(run_id, CALLER_KEY, default=None)
    except Exception:  # noqa: BLE001 — an unreadable memory must not fail open loudly
        return {}
    return value if isinstance(value, dict) else {}


def resolve_caller(run_id: str = "", overrides: dict | None = None) -> dict:
    """Resolve the principal a dispatch runs as.

    Resolution order, most specific first:

    1. ``overrides`` — the executor's ``--caller-*`` flags.
    2. Run memory's ``caller`` key — the workflow context (dwo-mem-01).
    3. ``ICDEV_MCP_CALLER_IL`` / ``ICDEV_IMPACT_LEVEL`` and
       ``ICDEV_MCP_CALLER_ROLES`` from the environment.
    4. :data:`DEFAULT_CALLER_IL` with no roles.

    Fields are resolved independently, so a run may declare an IL in memory and
    have its roles come from the environment.

    Returns:
        ``{"principal_id", "tenant_id", "impact_level", "roles", "source"}``.
        ``source`` names where the impact level came from, so a refusal can say
        which layer decided it.
    """
    overrides = {k: v for k, v in (overrides or {}).items() if v}
    context = read_caller_context(run_id)

    impact_level, source = "", ""
    for candidate, origin in (
        (overrides.get("impact_level"), "argument"),
        (context.get("impact_level"), f"run memory '{CALLER_KEY}'"),
    ):
        if candidate:
            impact_level, source = str(candidate), origin
            break
    if not impact_level:
        for name in CALLER_IL_ENV:
            if os.environ.get(name):
                impact_level, source = os.environ[name], f"${name}"
                break
    if not impact_level:
        impact_level, source = DEFAULT_CALLER_IL, "default (no caller declared)"

    roles = (
        _normalize_roles(overrides.get("roles"))
        or _normalize_roles(context.get("roles"))
        or _normalize_roles(os.environ.get(CALLER_ROLES_ENV))
    )

    return {
        "principal_id": str(
            overrides.get("principal_id") or context.get("principal_id") or ""
        ),
        "tenant_id": str(overrides.get("tenant_id") or context.get("tenant_id") or ""),
        "impact_level": impact_level.strip().upper(),
        "roles": roles,
        "source": source,
    }


def _owning_component(module_path: str, registry=None):
    """Return the registry component whose package contains ``module_path``.

    Ownership is by module package — ``tools.infra_canvas.foo`` is owned by the
    component whose ``module`` is ``tools.infra_canvas.blueprint``. The tool's
    ``category`` is deliberately *not* consulted: category names collide with
    component ``cli_name``s by coincidence (category ``infra`` vs. the
    Infrastructure canvas), and authorizing on a coincidental string match
    would deny tools for reasons nobody declared.

    Where several components share a package, the strictest (highest ``min_il``)
    wins, so an ambiguous mapping cannot resolve to the weaker of two policies.
    """
    if not module_path:
        return None
    if registry is None:
        from tools.config.component_registry import get_registry  # noqa: PLC0415

        registry = get_registry()

    order = _il_order()
    best, best_len, best_il = None, -1, -1
    for component in registry:
        module = component.module or ""
        if "." not in module:
            continue
        package = module.rsplit(".", 1)[0]
        if module_path != package and not module_path.startswith(package + "."):
            continue
        il = order.get((component.min_il or "").upper(), -1)
        if len(package) > best_len or (len(package) == best_len and il > best_il):
            best, best_len, best_il = component, len(package), il
    return best


def tool_requirements(tool: str, entry: dict | None = None, registry=None) -> dict:
    """Return the IL and role limits ``tool`` is dispatched under.

    Limits come from ``args/component_registry.yaml`` — the same ``min_il`` and
    ``default_roles`` the HTTP canvas gate enforces — resolved through the
    component that owns the tool's handler module. A tool no component owns runs
    at the platform baseline (:data:`DEFAULT_TOOL_MIN_IL`) with no role limit.

    Returns:
        ``{"min_il", "required_roles", "component", "component_name"}``.
    """
    entry = entry if entry is not None else resolve_entry(tool)
    component = _owning_component(str(entry.get("module", "") or ""), registry)
    if component is None:
        return {
            "min_il": DEFAULT_TOOL_MIN_IL,
            "required_roles": (),
            "component": "",
            "component_name": "",
        }
    return {
        "min_il": (component.min_il or DEFAULT_TOOL_MIN_IL).strip().upper(),
        "required_roles": tuple(component.default_roles or ()),
        "component": component.key,
        "component_name": component.display_name or component.key,
    }


def _has_canvas_grant(caller: dict, canvas_name: str) -> bool:
    """Return True if the caller holds an explicit grant on ``canvas_name``.

    Consulted only after the caller's declared roles fail to match, and only
    when the caller has an identity to look up: a direct or group grant is a
    legitimate way to reach a canvas without holding its default role, but it
    costs a DB round trip that an anonymous run cannot benefit from.
    """
    if not (caller.get("principal_id") and caller.get("tenant_id")):
        return False
    try:
        from tools.security.canvas_access import check_access  # noqa: PLC0415

        return bool(
            check_access(
                caller["principal_id"],
                caller["tenant_id"],
                canvas_name,
                required_level="read",
                user_role=(caller.get("roles") or ("",))[0],
            )
        )
    except Exception:  # noqa: BLE001 — an unreachable grant store denies, never crashes
        return False


def check_caller_authorized(
    tool: str,
    caller: dict | None = None,
    entry: dict | None = None,
    registry=None,
) -> dict:
    """Refuse ``tool`` unless the caller clears its IL and role limits.

    Args:
        tool: Registry tool name, already past the allowlist.
        caller: Resolved caller (see :func:`resolve_caller`). Defaults to a
            caller resolved with no run context.
        entry: The tool's registry entry, when already resolved.
        registry: Component registry to read limits from. Injectable for tests.

    Returns:
        The requirements the caller cleared, for the step payload and d5 audit.

    Raises:
        MCPWorkflowGateError: ``mcp_tool_exceeds_caller_il`` when the caller's
            impact level is below the tool's minimum (or is not a level this
            platform knows), ``mcp_tool_missing_required_role`` when the tool's
            owning component requires a role the caller neither holds nor has
            been granted.
    """
    caller = caller if caller is not None else resolve_caller()
    requirements = tool_requirements(tool, entry=entry, registry=registry)

    order = _il_order()
    required_il = str(requirements["min_il"]).upper()
    caller_il = str(caller.get("impact_level") or "").upper()
    required_rank = order.get(required_il)
    caller_rank = order.get(caller_il)

    if required_rank is None:
        raise MCPWorkflowGateError(
            f"MCP tool '{tool}' is owned by component "
            f"'{requirements['component'] or '(none)'}', whose min_il "
            f"{required_il!r} is not a known impact level "
            f"({', '.join(sorted(order))}). Refusing to dispatch — the gate "
            f"will not guess what an unrecognized level permits.",
            tool=tool,
            reason="mcp_tool_exceeds_caller_il",
        )
    if caller_rank is None or caller_rank < required_rank:
        owner = (
            f" (owned by {requirements['component_name']})"
            if requirements["component"]
            else " (platform baseline — no component owns it)"
        )
        detail = (
            f"caller impact level {caller_il!r} is not a known level "
            f"({', '.join(sorted(order))})"
            if caller_rank is None
            else f"caller is {caller_il}, tool requires {required_il}"
        )
        raise MCPWorkflowGateError(
            f"MCP tool '{tool}' requires impact level {required_il}{owner}, "
            f"but the caller cannot meet it: {detail}. Caller IL resolved from "
            f"{caller.get('source') or 'unknown'}. Raise the run's caller "
            f"context or dispatch this tool from an {required_il} run.",
            tool=tool,
            reason="mcp_tool_exceeds_caller_il",
        )

    required_roles = requirements["required_roles"]
    if required_roles:
        held = set(caller.get("roles") or ())
        if not held & set(required_roles) and not _has_canvas_grant(
            caller, requirements["component"]
        ):
            raise MCPWorkflowGateError(
                f"MCP tool '{tool}' is owned by "
                f"{requirements['component_name']} ({requirements['component']}), "
                f"which requires one of these roles: "
                f"{', '.join(sorted(required_roles))}. The caller holds "
                f"{', '.join(sorted(held)) or '(no roles)'} and has no explicit "
                f"canvas_access grant. Grant the principal access to "
                f"'{requirements['component']}' or run the step as a principal "
                f"that holds one of those roles.",
                tool=tool,
                reason="mcp_tool_missing_required_role",
            )

    return requirements


# ── Run memory (dwo-mem-01) ────────────────────────────────────────────────

def write_run_memory(run_id: str, step_id: str, value: dict) -> tuple[bool, str]:
    """Persist a step result to run-scoped memory under ``step:<step_id>``.

    Soft dependency: run_memory is delivered by dwo-mem-01. Until it lands this
    is a no-op that reports why, rather than a second state store.
    """
    if not run_id:
        return False, "no --run-id supplied"
    try:
        from tools.studio import run_memory  # noqa: PLC0415
    except ImportError:
        return False, "tools.studio.run_memory not available (dwo-mem-01)"
    try:
        run_memory.set(run_id, f"{MEMORY_KEY_PREFIX}{step_id}", value)
        return True, ""
    except Exception as exc:  # noqa: BLE001 — memory must never fail the step
        return False, str(exc)


# ── Dispatch ───────────────────────────────────────────────────────────────

def _jsonable(value):
    """Coerce a handler return value into something json.dumps can emit."""
    try:
        json.dumps(value)
        return value
    except (TypeError, ValueError):
        return {"repr": repr(value)[:4000]}


def run(
    tool: str,
    params: dict,
    run_id: str = "",
    step_id: str = "",
    caller: dict | None = None,
) -> dict:
    """Authorize, look up, validate, and dispatch a registry tool.

    Args:
        caller: Principal to dispatch as. Resolved from the run's workflow
            context when omitted (see :func:`resolve_caller`).

    Returns the step result payload.

    Raises:
        MCPWorkflowGateError: ``tool`` is not on the workflow allowlist, or the
            caller does not clear its IL / role limits. The allowlist is checked
            first, so a refused tool is never resolved, imported, or called;
            IL and RBAC are checked after lookup (they read the tool's owning
            component from its module) but before params and dispatch, so a
            refused caller never reaches the handler either.
    """
    check_tool_allowed(tool)

    entry = resolve_entry(tool)

    caller = caller if caller is not None else resolve_caller(run_id)
    requirements = check_caller_authorized(tool, caller, entry=entry)

    violations = validate_params(params, entry.get("input_schema") or {})
    if violations:
        raise ValueError(
            f"Invalid params for '{tool}' — "
            + "; ".join(violations[:10])
            + (f" (+{len(violations) - 10} more)" if len(violations) > 10 else "")
        )

    module_path, handler_name = entry["module"], entry["handler"]
    try:
        mod = importlib.import_module(module_path)
        handler = getattr(mod, handler_name)
    except (ImportError, AttributeError) as exc:
        raise RuntimeError(
            f"Cannot load handler {module_path}.{handler_name} for '{tool}': {exc}"
        ) from exc

    start = time.monotonic()
    result = handler(params)
    duration_ms = int((time.monotonic() - start) * 1000)

    step_id = step_id or f"mcp-{tool}"
    payload = {
        "tool": tool,
        "category": entry.get("category", ""),
        "handler": f"{module_path}.{handler_name}",
        "duration_ms": duration_ms,
        # What the dispatch was authorized under — the record d5 audits.
        "caller_il": caller.get("impact_level", ""),
        "required_il": requirements["min_il"],
        "component": requirements["component"],
        "result": _jsonable(result),
    }
    written, reason = write_run_memory(run_id, step_id, payload)
    payload["step_id"] = step_id
    payload["memory_key"] = f"{MEMORY_KEY_PREFIX}{step_id}"
    payload["memory_written"] = written
    if not written:
        payload["memory_skipped"] = reason
    return payload


def main():
    parser = argparse.ArgumentParser(description="MCP Tool Executor (shared)")
    parser.add_argument("--tool", required=True, help="TOOL_REGISTRY tool name")
    parser.add_argument("--params", default="{}", help="Tool arguments as a JSON object")
    parser.add_argument("--run-id", default="")
    parser.add_argument("--step-id", default="", help="Run-memory key suffix")
    parser.add_argument("--project-id", default="default")
    parser.add_argument("--json", action="store_true", help="Accepted for runner parity")
    parser.add_argument("--caller-il", default="",
                        help="Caller impact level (IL2|IL4|IL5|IL6); overrides run context")
    parser.add_argument("--caller-roles", default="",
                        help="Comma-separated caller roles; overrides run context")
    parser.add_argument("--caller-id", default="", help="Caller principal id")
    parser.add_argument("--tenant-id", default="", help="Caller tenant id")
    args = parser.parse_args()

    try:
        params = parse_params(args.params)
        caller = resolve_caller(args.run_id, {
            "impact_level": args.caller_il,
            "roles": args.caller_roles,
            "principal_id": args.caller_id,
            "tenant_id": args.tenant_id,
        })
        payload = run(args.tool, params, args.run_id, args.step_id, caller)
        print(json.dumps({"status": "success", **payload}))
        sys.exit(0)
    except MCPWorkflowGateError as exc:
        # Before LookupError/Exception: this is a RuntimeError and must not be
        # reported as a generic dispatch failure — the step was refused, not run.
        print(json.dumps({"status": "failed",
                          "error_type": exc.reason or "mcp_tool_not_allowlisted",
                          "tool": args.tool, "error": str(exc)}))
        sys.exit(1)
    except LookupError as exc:
        print(json.dumps({"status": "failed", "error_type": "unknown_tool",
                          "tool": args.tool, "error": str(exc)}))
        sys.exit(1)
    except ValueError as exc:
        print(json.dumps({"status": "failed", "error_type": "invalid_params",
                          "tool": args.tool, "error": str(exc)}))
        sys.exit(1)
    except Exception as exc:  # noqa: BLE001
        print(json.dumps({"status": "failed", "error_type": "dispatch_error",
                          "tool": args.tool,
                          "error": f"{type(exc).__name__}: {exc}"}))
        sys.exit(1)


if __name__ == "__main__":
    main()
