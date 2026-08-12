#!/usr/bin/env python3
# CUI // SP-CTI
"""MCP per-tool authorization *evidence* probe (exa-policy-08).

Four compliance generators used to evidence per-tool MCP authorization by
checking that ``tools/security/mcp_tool_authorizer.py`` **exists on disk**.
That is not evidence of a control.  A module with zero call sites satisfies a
file-existence check perfectly, and an assessor reading the generated artifact
would conclude that per-tool RBAC is in force when nothing is authorizing
anything.  Overstated evidence is worse than a missing control: the missing
control is at least discoverable.

This module replaces that check with one that **exercises the control**:

    1. The policy must actually decide.  ``MCPToolAuthorizer`` is run against
       fixed probe cases drawn from the deny-first contract in D261 -- an
       explicit deny, a default-policy deny, an unknown-role deny, and a
       positive allow.  An empty or ``default_policy: allow`` matrix fails
       here even though the file and the class both exist.

    2. The policy must be *wired to a surface that has a principal*.  The
       probe calls the SaaS MCP surface's own decision function and requires
       it to refuse a call the matrix denies.  With no call site there is
       nothing to call and the probe reports ``not_satisfied``.

    3. The verdict must *bind*.  A decision that is logged and then ignored
       (monitor mode) is real evidence of monitoring, not of enforcement, so
       it reports ``partially_satisfied`` -- never ``satisfied``.

Scope is deliberately narrow, and the narrowing is itself recorded in the
result (see ``STDIO_SCOPE_OUT``).  A scoped, accurate claim is defensible; an
unscoped, inaccurate one is not.

Surfaces:

    saas_mcp_http   IN SCOPE.  ``tools/saas/mcp_http.py`` is the only MCP
                    surface with an authenticated principal -- the gateway
                    middleware establishes tenant/user/role before the
                    blueprint runs -- so "may this caller use this tool" is a
                    question that can be answered there.

    stdio           OUT OF SCOPE, with reason.  ``tools/mcp/unified_server.py``
                    and ``tools/mcp/base_server.py`` carry no caller identity.
                    Any role would be supplied by the caller being authorized,
                    which is not authentication.  What bounds that surface
                    instead is recorded in ``STDIO_SCOPE_OUT``.

CLI:
    python tools/security/mcp_authz_evidence.py --json
    python tools/security/mcp_authz_evidence.py --gate
"""

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

BASE_DIR = Path(__file__).resolve().parent.parent.parent

STATUS_SATISFIED = "satisfied"
STATUS_PARTIAL = "partially_satisfied"
STATUS_NOT_SATISFIED = "not_satisfied"

#: The surface this control is claimed on.  Named so the claim in a generated
#: artifact is scoped rather than a bare "MCP is authorized".
IN_SCOPE_SURFACE = "saas_mcp_http"

#: Behavioural probe cases for the D261 matrix, one per branch of the
#: deny-first contract.  ``(role, tool, must_be_allowed, what_it_proves)``.
#: These are policy *outcomes*, not the matrix itself -- a rewrite of the
#: matrix that keeps the contract keeps passing, and one that breaks the
#: contract fails regardless of how the file is shaped.
POLICY_PROBES = (
    ("developer", "terraform_apply", False, "explicit deny rule fires"),
    ("developer", "run_tests", True, "allow rule fires"),
    ("developer", "no_such_tool_exa_policy_08", False, "default policy denies an unlisted tool"),
    ("no_such_role_exa_policy_08", "run_tests", False, "unknown role gets default policy, not admin"),
)

#: A tool/role pair the surface must refuse if it is consulting the policy at
#: all.  Uses the SaaS role vocabulary, which is what a real caller presents.
SURFACE_DENY_PROBE = ("terraform_apply", "developer")

#: Why the stdio surface is not claimed, and what bounds it instead.  Recorded
#: in every result so the scope-out travels with the evidence rather than
#: living only in a design doc nobody re-reads at assessment time.
STDIO_SCOPE_OUT: Dict[str, Any] = {
    "surface": "stdio",
    "in_scope": False,
    "modules": [
        "tools/mcp/unified_server.py",
        "tools/mcp/base_server.py",
    ],
    "reason": (
        "No caller identity exists on this surface. A stdio MCP server is spawned by, "
        "and speaks only to, its parent process; there is no authenticated principal to "
        "authorize. A role supplied in the tool arguments is self-asserted by the caller "
        "being authorized, which is not authentication -- gating on it would produce "
        "authorization-shaped evidence with no authorization behind it, which is the "
        "exact defect this probe exists to prevent."
    ),
    "compensating_controls": [
        {
            "id": "reversibility-gate",
            "control": "tools/agent_runtime/approval_gate.py",
            "bounds": "Classifies each tool call by reversibility and halts irreversible ones for approval.",
        },
        {
            "id": "pre-tool-use-hard-blocks",
            "control": ".claude/hooks/pre_tool_use.py",
            "bounds": "Hard-blocks destructive commands and UPDATE/DELETE against append-only tables before the call runs.",
        },
        {
            "id": "file-access-tiers",
            "control": "args/file_access_tiers.yaml",
            "bounds": "Tiers filesystem reach so a tool cannot read or write outside its declared tier.",
        },
    ],
}


# ---------------------------------------------------------------------------
# Layer 1: does the policy actually decide?
# ---------------------------------------------------------------------------
def probe_policy(authorizer: Optional[Any] = None) -> Dict[str, Any]:
    """Run the D261 matrix against the deny-first probe cases.

    Args:
        authorizer: An object with ``authorize(role, tool) -> dict``. Defaults
            to a live ``MCPToolAuthorizer`` reading the shipped config.

    Returns:
        ``{ok, checks, reason}``. ``ok`` is False if any probe case came back
        with the wrong verdict, or if the authorizer could not be built.
    """
    if authorizer is None:
        try:
            from tools.security.mcp_tool_authorizer import MCPToolAuthorizer

            authorizer = MCPToolAuthorizer()
        except Exception as exc:  # noqa: BLE001 - unavailable policy is not-satisfied, not a crash
            return {
                "ok": False,
                "checks": [],
                "reason": f"MCPToolAuthorizer unavailable: {exc}",
            }

    checks: List[Dict[str, Any]] = []
    for role, tool, must_allow, proves in POLICY_PROBES:
        try:
            verdict = authorizer.authorize(role, tool)
            actual = bool(verdict.get("allowed"))
            detail = verdict.get("reason", "")
        except Exception as exc:  # noqa: BLE001
            actual, detail = (not must_allow), f"authorize() raised: {exc}"
        checks.append(
            {
                "role": role,
                "tool": tool,
                "expected_allowed": must_allow,
                "actual_allowed": actual,
                "passed": actual == must_allow,
                "proves": proves,
                "detail": detail,
            }
        )

    failed = [c for c in checks if not c["passed"]]
    return {
        "ok": not failed,
        "checks": checks,
        "reason": (
            "policy decides per the deny-first contract"
            if not failed
            else "policy did not decide as configured: "
            + "; ".join(f"{c['role']}/{c['tool']}" for c in failed)
        ),
    }


# ---------------------------------------------------------------------------
# Layer 2: is the policy wired to a surface that has a principal?
# ---------------------------------------------------------------------------
def _load_surface() -> Any:
    """Import the in-scope MCP surface module, or raise."""
    from tools.saas import mcp_http

    return mcp_http


def probe_surface(surface: Optional[Any] = None) -> Dict[str, Any]:
    """Exercise the in-scope surface's own authorization decision.

    This is the check that a file-existence test cannot make: an inert
    ``mcp_tool_authorizer.py`` passes ``Path.exists()`` and fails here,
    because there is no ``authorize_tool`` on the surface to call.

    Args:
        surface: Module-like object exposing ``authorize_tool(tool, role)``.
            Defaults to ``tools.saas.mcp_http``.

    Returns:
        ``{ok, wired, enforced, mode, denied_for_role, registry_size, reason}``.
    """
    if surface is None:
        try:
            surface = _load_surface()
        except Exception as exc:  # noqa: BLE001
            return {
                "ok": False,
                "wired": False,
                "enforced": False,
                "mode": None,
                "reason": f"{IN_SCOPE_SURFACE} could not be imported: {exc}",
            }

    authorize_tool: Optional[Callable] = getattr(surface, "authorize_tool", None)
    if not callable(authorize_tool):
        return {
            "ok": False,
            "wired": False,
            "enforced": False,
            "mode": None,
            "reason": (
                f"{IN_SCOPE_SURFACE} exposes no authorize_tool() -- the policy module exists "
                "but nothing on a principal-bearing surface consults it"
            ),
        }

    tool, role = SURFACE_DENY_PROBE
    try:
        decision = authorize_tool(tool, role)
    except Exception as exc:  # noqa: BLE001
        return {
            "ok": False,
            "wired": True,
            "enforced": False,
            "mode": None,
            "reason": f"{IN_SCOPE_SURFACE}.authorize_tool() raised: {exc}",
        }

    if decision.get("allowed"):
        return {
            "ok": False,
            "wired": True,
            "enforced": bool(decision.get("enforced")),
            "mode": decision.get("mode"),
            "reason": (
                f"{IN_SCOPE_SURFACE} allowed {role}/{tool}, which the D261 matrix denies -- "
                "the surface is not consulting the policy"
            ),
        }

    # An empty-role caller must never be upgraded to admin.
    anon = authorize_tool(tool, None)
    if anon.get("allowed"):
        return {
            "ok": False,
            "wired": True,
            "enforced": bool(decision.get("enforced")),
            "mode": decision.get("mode"),
            "reason": f"{IN_SCOPE_SURFACE} allowed {tool} with no role presented",
        }

    # Offer-time reach: how much of the registry this role is actually refused.
    # Computed with the surface's own pure decision function so generating
    # evidence writes no audit rows of its own.
    registry = getattr(surface, "TOOL_REGISTRY", []) or []
    denied = 0
    for entry in registry:
        name = entry.get("name") if isinstance(entry, dict) else None
        if name and not authorize_tool(name, role).get("allowed"):
            denied += 1

    return {
        "ok": True,
        "wired": True,
        "enforced": bool(decision.get("enforced")),
        "mode": decision.get("mode"),
        "denied_for_role": {"role": role, "denied": denied, "of": len(registry)},
        "registry_size": len(registry),
        "reason": decision.get("reason", ""),
    }


# ---------------------------------------------------------------------------
# Layer 3: do the stdio compensating controls behave?
# ---------------------------------------------------------------------------
def probe_compensating_controls() -> List[Dict[str, Any]]:
    """Check the controls named in the stdio scope-out actually do something.

    Deliberately behavioural for the same reason as everything else here: the
    scope-out is only defensible if the controls it points at are not
    themselves evidenced by ``Path.exists()``.
    """
    results: List[Dict[str, Any]] = []

    # Reversibility gate: an irreversible call must not classify as reversible.
    try:
        from tools.agent_runtime.approval_gate import classify

        verdict = classify("Bash", {"command": "rm -rf /srv/data"})
        results.append(
            {
                "id": "reversibility-gate",
                "passed": bool(verdict.requires_approval),
                "detail": f"classify(Bash, 'rm -rf ...') -> {verdict.tier}, "
                f"requires_approval={verdict.requires_approval}",
            }
        )
    except Exception as exc:  # noqa: BLE001
        results.append({"id": "reversibility-gate", "passed": False, "detail": f"unavailable: {exc}"})

    # pre_tool_use hard blocks: the append-only table list must be populated.
    # Parsed rather than imported -- the hook is a fresh-interpreter entry
    # point, not a library, and importing it here would be a side effect.
    try:
        import ast

        hook_src = (BASE_DIR / ".claude" / "hooks" / "pre_tool_use.py").read_text(
            encoding="utf-8", errors="ignore"
        )
        tables: List[str] = []
        for node in ast.walk(ast.parse(hook_src)):
            if isinstance(node, ast.Assign) and any(
                isinstance(t, ast.Name) and t.id == "APPEND_ONLY_TABLES" for t in node.targets
            ):
                if isinstance(node.value, (ast.List, ast.Tuple, ast.Set)):
                    tables = [e.value for e in node.value.elts if isinstance(e, ast.Constant)]
        results.append(
            {
                "id": "pre-tool-use-hard-blocks",
                "passed": len(tables) > 0,
                "detail": f"{len(tables)} append-only tables protected",
            }
        )
    except Exception as exc:  # noqa: BLE001
        results.append({"id": "pre-tool-use-hard-blocks", "passed": False, "detail": f"unavailable: {exc}"})

    # File access tiers: the config must declare at least one tier.
    try:
        import yaml

        tiers = (
            yaml.safe_load((BASE_DIR / "args" / "file_access_tiers.yaml").read_text(encoding="utf-8")) or {}
        ).get("file_access_tiers") or {}
        results.append(
            {
                "id": "file-access-tiers",
                "passed": len(tiers) > 0,
                "detail": f"{len(tiers)} tiers declared",
            }
        )
    except Exception as exc:  # noqa: BLE001
        results.append({"id": "file-access-tiers", "passed": False, "detail": f"unavailable: {exc}"})

    return results


# ---------------------------------------------------------------------------
# Composite verdict
# ---------------------------------------------------------------------------
def probe_mcp_authorization(
    *,
    authorizer: Optional[Any] = None,
    surface: Optional[Any] = None,
    include_compensating: bool = True,
) -> Dict[str, Any]:
    """Full evidence verdict for per-tool MCP authorization.

    Returns:
        Dict with ``status`` (satisfied / partially_satisfied / not_satisfied),
        ``enforced``, ``scope`` and the underlying probe results. ``status`` is
        never ``satisfied`` unless a deny genuinely binds on a surface with an
        authenticated principal.
    """
    policy = probe_policy(authorizer)
    surf = probe_surface(surface)

    if not policy["ok"]:
        status, reason = STATUS_NOT_SATISFIED, policy["reason"]
    elif not surf["ok"]:
        status, reason = STATUS_NOT_SATISFIED, surf["reason"]
    elif not surf["enforced"]:
        status, reason = (
            STATUS_PARTIAL,
            f"authorization is evaluated and audited on {IN_SCOPE_SURFACE} but does not bind "
            f"(mode={surf.get('mode')}): a denied call is logged and then proceeds",
        )
    else:
        status, reason = (
            STATUS_SATISFIED,
            f"per-tool authorization binds on {IN_SCOPE_SURFACE} (mode={surf.get('mode')})",
        )

    result: Dict[str, Any] = {
        "control": "mcp_per_tool_authorization",
        "adr": "D261",
        "status": status,
        "enforced": bool(surf.get("enforced")) and status == STATUS_SATISFIED,
        "reason": reason,
        "scope": {
            "in_scope_surfaces": [IN_SCOPE_SURFACE],
            "claim": (
                "Per-tool RBAC is claimed ONLY on the MCP surface that has an authenticated "
                "principal. It is not claimed platform-wide."
            ),
            "scoped_out": [STDIO_SCOPE_OUT],
        },
        "policy": policy,
        "surface": surf,
    }
    if include_compensating:
        result["scope"]["scoped_out"] = [
            dict(STDIO_SCOPE_OUT, compensating_control_probes=probe_compensating_controls())
        ]
    return result


_CACHED_DEFAULT: Optional[Dict[str, Any]] = None


def cached_probe() -> Dict[str, Any]:
    """``probe_mcp_authorization()`` with no overrides, memoized per process.

    A generator asks the same question once per requirement; importing the
    SaaS surface for each of 61 KSIs is pure waste. Only the no-override path
    is cached -- a caller that injects an authorizer or surface always gets a
    fresh evaluation, so tests are never served a stale verdict.
    """
    global _CACHED_DEFAULT
    if _CACHED_DEFAULT is None:
        _CACHED_DEFAULT = probe_mcp_authorization()
    return _CACHED_DEFAULT


def reset_cache() -> None:
    """Drop the memoized default verdict (tests, long-lived daemons)."""
    global _CACHED_DEFAULT
    _CACHED_DEFAULT = None


def scope_note() -> str:
    """Compact one-line scope statement for a persisted evidence field."""
    scoped = STDIO_SCOPE_OUT
    controls = ", ".join(c["control"] for c in scoped["compensating_controls"])
    return (
        f"Scope: claimed on {IN_SCOPE_SURFACE} only. "
        f"The {scoped['surface']} surface is OUT OF SCOPE — no authenticated caller identity exists "
        f"there, so a role would be self-asserted by the caller being authorized. "
        f"Compensating controls: {controls}."
    )


def evidence_count(**kwargs: Any) -> int:
    """1 when a deny binds on a principal-bearing surface, else 0.

    Deliberately 0 in monitor mode. A FedRAMP KSI counting this source is
    claiming the control is in force; "we log what we would have denied" is
    not that claim, and rounding it up is how the original file-existence
    check overstated in the first place.
    """
    return 1 if probe_mcp_authorization(**kwargs)["status"] == STATUS_SATISFIED else 0


def assessment_status(**kwargs: Any) -> str:
    """Assessor-vocabulary status for the same probe."""
    return probe_mcp_authorization(**kwargs)["status"]


def summary_line(result: Optional[Dict[str, Any]] = None) -> str:
    """One-line human summary, for red-team / audit finding messages."""
    result = result or probe_mcp_authorization()
    return f"MCP per-tool authorization [{IN_SCOPE_SURFACE}]: {result['status']} — {result['reason']}"


def main() -> int:
    parser = argparse.ArgumentParser(description="MCP per-tool authorization evidence probe (exa-policy-08)")
    parser.add_argument("--json", action="store_true", help="Output as JSON")
    parser.add_argument("--gate", action="store_true", help="Exit 1 unless status is satisfied")
    parser.add_argument(
        "--allow-monitor",
        action="store_true",
        help="With --gate, treat partially_satisfied (monitor mode) as passing",
    )
    args = parser.parse_args()

    result = probe_mcp_authorization()

    if args.json:
        print(json.dumps(result, indent=2))
    else:
        print(summary_line(result))
        for check in result["policy"]["checks"]:
            mark = "PASS" if check["passed"] else "FAIL"
            print(f"  [{mark}] {check['role']}/{check['tool']} — {check['proves']}")
        scoped = result["scope"]["scoped_out"][0]
        print(f"  [SCOPE-OUT] {scoped['surface']}: {scoped['reason'][:110]}...")
        for control in scoped["compensating_controls"]:
            print(f"      compensating: {control['id']} — {control['control']}")

    if args.gate:
        passing = {STATUS_SATISFIED} | ({STATUS_PARTIAL} if args.allow_monitor else set())
        return 0 if result["status"] in passing else 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
