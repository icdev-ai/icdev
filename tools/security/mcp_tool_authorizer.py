#!/usr/bin/env python3
# CUI // SP-CTI
"""MCP Tool Authorizer — per-tool RBAC for MCP servers (D261).

Stateless authorization engine (no DB). It answers one question: *may this role
call this tool*.

Where the answer comes from — REGISTRY MODE (exa-policy-07)
------------------------------------------------------------
``min_il`` and ``required_roles`` are declared once per tool in
``tools/mcp/tool_registry.py`` and read by every surface that authorizes an MCP
call — the Studio ``mcp`` and ``agent`` gates and the SaaS MCP HTTP surface.
This module materialises the per-role VIEW of those declarations.

It used to read a hand-written ``role_tool_matrix`` in
``args/owasp_agentic_config.yaml``. That matrix had gone stale in the way
hand-written per-tool lists do — ``developer`` allowed 8 tools out of ~700 —
while the Studio gates read a different declaration entirely. Two RBACs that
disagree are worse than one that is switched off, so the matrix is retired and
the registry is the single source. See ``docs/security/mcp-tool-authorization.md``.

Evaluation, in order:

1. An unrecognised role holds no privileges — ``default_policy`` decides, which
   ships as ``deny``. Roles and their aliases are normalised by
   ``tool_registry.normalize_role``; anything outside that vocabulary
   (``viewer``, ``auditor``, ``""``) is unrecognised, never downgraded-to-safe.
2. ``admin`` is allowed everything. That is the same policy the retired matrix
   spelled ``allow: ["*"]``, kept explicit rather than emergent.
3. A tool with no declaration — no registry entry and no override — is refused.
   Restrictive by default is the whole point: a tool nobody classified must not
   be reachable by everybody.
4. A declaration with no ``required_roles`` is callable by any recognised role.
   That is the read-only tier, and it is a DECLARED value, not a fallback.
5. Otherwise the role must appear in ``required_roles``.

MATRIX MODE is retained for a caller that passes an explicit
``role_tool_matrix`` in ``config``: deny-first with ``fnmatch`` wildcards, the
pre-exa-policy-07 behaviour, unchanged. Nothing in the tree ships one; it exists
so a deployment can pin its own matrix and so the old contract stays testable.

Impact level is NOT evaluated here. The declaration carries ``min_il``, but the
surfaces this module serves authenticate a *role*, not an impact level; the
Studio gates are where ``min_il`` binds.

Pattern: stateless config-driven authorization (no DB)
ADRs: D261 (MCP per-tool RBAC)

Roles (``tool_registry.ROLES``):
    admin     — every tool
    pm        — project, portfolio and planning state
    developer — build, test, data and model state
    isso      — compliance, assurance and security posture state
    co        — read-only tiers only

CLI:
    python tools/security/mcp_tool_authorizer.py --check --role developer --tool scaffold --json
    python tools/security/mcp_tool_authorizer.py --list --role pm --json
    python tools/security/mcp_tool_authorizer.py --validate --json
"""

import argparse
import json
from fnmatch import fnmatch
from pathlib import Path
from typing import Dict, List, Optional

import yaml

BASE_DIR = Path(__file__).resolve().parent.parent.parent
CONFIG_PATH = BASE_DIR / "args" / "owasp_agentic_config.yaml"

#: Config value naming the registry as the declaration source. Present in the
#: shipped ``args/owasp_agentic_config.yaml`` so an operator reading that file
#: is told where the policy went, rather than finding an absence.
SOURCE_REGISTRY = "mcp_registry"


def _load_config() -> Dict:
    """Load MCP authorization config from YAML."""
    if CONFIG_PATH.exists():
        with open(CONFIG_PATH) as f:
            cfg = yaml.safe_load(f) or {}
        return cfg.get("mcp_authorization", {})
    return {}


def _registry():
    """The declaration module. Imported lazily so the CLI starts without it."""
    from tools.mcp import tool_registry

    return tool_registry


class MCPToolAuthorizer:
    """Per-tool RBAC for MCP servers (D261). See the module docstring."""

    def __init__(self, config: Optional[Dict] = None):
        self._config = config or _load_config()
        self._default_policy = self._config.get("default_policy", "deny")
        self._matrix = self._config.get("role_tool_matrix", {})
        # PRESENCE of the key selects the mode, not truthiness. A config that
        # declares `role_tool_matrix: {}` is a matrix that authorizes nobody --
        # a misconfiguration validate_config() must report -- and silently
        # rerouting it to the registry would turn that into a clean bill of
        # health for a policy the operator thought they had written.
        self._matrix_mode = "role_tool_matrix" in self._config

    @property
    def source(self) -> str:
        """``"role_tool_matrix"`` or :data:`SOURCE_REGISTRY`."""
        return "role_tool_matrix" if self._matrix_mode else SOURCE_REGISTRY

    # -- registry mode ------------------------------------------------------
    def _authorize_from_registry(self, role: str, tool_name: str) -> Dict:
        reg = _registry()
        canonical = reg.normalize_role(role)
        result = {"allowed": False, "role": role, "tool": tool_name}

        if not canonical:
            return dict(
                result,
                allowed=self._default_policy == "allow",
                reason=f"Unknown role '{role}' — default policy: {self._default_policy}",
            )
        if canonical == reg.ADMIN_ROLE:
            return dict(result, allowed=True, rbac_role=canonical,
                        reason=f"Role '{canonical}' is allowed every tool")

        auth = reg.tool_authorization(tool_name)
        result["rbac_role"] = canonical
        result["min_il"] = auth["min_il"]
        result["required_roles"] = list(auth["required_roles"])
        result["declaration"] = auth["source"]

        if auth["tier"] == reg.TIER_UNKNOWN:
            return dict(
                result,
                allowed=self._default_policy == "allow",
                reason=(
                    f"Tool '{tool_name}' has no declaration in the MCP registry — "
                    f"default policy: {self._default_policy}"
                ),
            )
        if not auth["required_roles"]:
            return dict(
                result,
                allowed=True,
                reason=f"Allowed: {auth['source']} declares no role limit",
            )
        if canonical in auth["required_roles"]:
            return dict(
                result,
                allowed=True,
                reason=f"Allowed: '{canonical}' is in required_roles ({auth['source']})",
            )
        return dict(
            result,
            reason=(
                f"Denied: '{canonical}' is not in required_roles "
                f"[{', '.join(auth['required_roles'])}] ({auth['source']})"
            ),
        )

    # -- matrix mode (retained; nothing in the tree ships a matrix) ---------
    def _authorize_from_matrix(self, role: str, tool_name: str) -> Dict:
        if role not in self._matrix:
            return {
                "allowed": self._default_policy == "allow",
                "role": role,
                "tool": tool_name,
                "reason": f"Unknown role '{role}' — default policy: {self._default_policy}",
            }

        role_config = self._matrix[role]
        deny_list = role_config.get("deny", [])
        allow_list = role_config.get("allow", [])

        # Step 1: Check deny list first (explicit deny wins)
        for pattern in deny_list:
            if fnmatch(tool_name.lower(), pattern.lower()):
                return {
                    "allowed": False,
                    "role": role,
                    "tool": tool_name,
                    "reason": f"Denied by explicit deny rule: {pattern}",
                }

        # Step 2: Check allow list
        for pattern in allow_list:
            if fnmatch(tool_name.lower(), pattern.lower()):
                return {
                    "allowed": True,
                    "role": role,
                    "tool": tool_name,
                    "reason": f"Allowed by rule: {pattern}",
                }

        # Step 3: Default policy
        return {
            "allowed": self._default_policy == "allow",
            "role": role,
            "tool": tool_name,
            "reason": f"No matching rule — default policy: {self._default_policy}",
        }

    def authorize(self, role: str, tool_name: str) -> Dict:
        """Authorize a tool call for a given role.

        Returns:
            Dict with allowed bool, role, tool, reason. In registry mode it also
            carries ``rbac_role``, ``min_il``, ``required_roles`` and
            ``declaration``, so a refusal names the declaration to go and edit.
        """
        if self._matrix_mode:
            return self._authorize_from_matrix(role, tool_name)
        return self._authorize_from_registry(role, tool_name)

    def list_allowed_tools(self, role: str) -> Dict:
        """The tools ``role`` may call.

        In registry mode this is the GENERATED view that replaced the matrix:
        ``allow`` is enumerated from the declarations rather than read from a
        hand-written pattern list, and ``deny`` names what was withheld.
        """
        if self._matrix_mode:
            if role not in self._matrix:
                return {
                    "role": role,
                    "error": f"Unknown role '{role}'",
                    "known_roles": list(self._matrix.keys()),
                }
            role_config = self._matrix[role]
            return {
                "role": role,
                "source": self.source,
                "allow": role_config.get("allow", []),
                "deny": role_config.get("deny", []),
                "default_policy": self._default_policy,
            }

        reg = _registry()
        canonical = reg.normalize_role(role)
        if not canonical:
            return {
                "role": role,
                "error": f"Unknown role '{role}'",
                "known_roles": list(reg.ROLES),
            }
        allow = reg.tools_for_role(canonical)
        declarations = reg.authorization_declarations()
        return {
            "role": role,
            "rbac_role": canonical,
            "source": self.source,
            "allow": allow,
            "deny": sorted(set(declarations) - set(allow)),
            "default_policy": self._default_policy,
        }

    def get_roles(self) -> List[str]:
        """Return the role vocabulary this authorizer decides against."""
        if self._matrix_mode:
            return list(self._matrix.keys())
        return list(_registry().ROLES)

    def validate_config(self) -> Dict:
        """Validate the authorization configuration.

        Matrix mode: every role has a rule, and no pattern is in both lists.

        Registry mode: the declarations must be usable as a policy — every role
        in the vocabulary resolves, ``admin`` reaches everything, and no
        registered tool fell through to the restrictive default (which would be
        a missing ``read_only`` declaration or an unmapped category, not a
        deliberate lockdown).
        """
        errors: List[str] = []
        warnings: List[str] = []
        expected_roles = {"admin", "pm", "developer", "isso", "co"}

        if not self._matrix_mode:
            reg = _registry()
            declarations = reg.authorization_declarations()
            if not declarations:
                errors.append("MCP registry declares no tools")
                return {"valid": False, "source": self.source,
                        "errors": errors, "warnings": warnings}

            configured_roles = set(reg.ROLES)
            missing = expected_roles - configured_roles
            if missing:
                warnings.append(f"Missing expected roles: {', '.join(sorted(missing))}")

            if len(reg.tools_for_role(reg.ADMIN_ROLE)) != len(declarations):
                errors.append("'admin' does not reach every declared tool")
            for role in sorted(configured_roles - {reg.ADMIN_ROLE}):
                if not reg.tools_for_role(role):
                    warnings.append(f"Role '{role}' can call no tool at all")

            fallen = reg.undeclared_authorizations()
            if fallen:
                warnings.append(
                    f"{len(fallen)} tool(s) fell through to the restrictive default "
                    f"(missing read_only declaration or unmapped category): "
                    f"{', '.join(fallen[:5])}"
                )

            return {
                "valid": not errors,
                "source": self.source,
                "roles": sorted(configured_roles),
                "role_count": len(configured_roles),
                "tool_count": len(declarations),
                "default_policy": self._default_policy,
                "errors": errors,
                "warnings": warnings,
            }

        if not self._matrix:
            errors.append("No role_tool_matrix configured")
            return {"valid": False, "source": self.source,
                    "errors": errors, "warnings": warnings}

        configured_roles = set(self._matrix.keys())
        missing = expected_roles - configured_roles
        if missing:
            warnings.append(f"Missing expected roles: {', '.join(sorted(missing))}")

        extra = configured_roles - expected_roles
        if extra:
            warnings.append(f"Extra roles configured: {', '.join(sorted(extra))}")

        for role, config in self._matrix.items():
            allow_list = config.get("allow", [])
            deny_list = config.get("deny", [])

            if not allow_list and not deny_list:
                warnings.append(f"Role '{role}' has no allow or deny rules")

            # Check for conflicts (same pattern in both allow and deny)
            for allow_pat in allow_list:
                for deny_pat in deny_list:
                    if allow_pat == deny_pat:
                        errors.append(f"Role '{role}': pattern '{allow_pat}' in both allow and deny")

        return {
            "valid": len(errors) == 0,
            "source": self.source,
            "roles": sorted(configured_roles),
            "role_count": len(configured_roles),
            "default_policy": self._default_policy,
            "errors": errors,
            "warnings": warnings,
        }


def main():
    parser = argparse.ArgumentParser(description="MCP Tool Authorizer — per-tool RBAC (D261)")
    parser.add_argument("--check", action="store_true", help="Check tool authorization")
    parser.add_argument("--list", action="store_true", help="List allowed tools for role")
    parser.add_argument("--validate", action="store_true", help="Validate configuration")
    parser.add_argument("--role", help="Role to check/list")
    parser.add_argument("--tool", help="Tool name to check")
    parser.add_argument("--json", action="store_true", help="Output as JSON")
    args = parser.parse_args()

    authorizer = MCPToolAuthorizer()

    if args.check:
        if not args.role or not args.tool:
            print("Error: --check requires --role and --tool", file=__import__("sys").stderr)
            __import__("sys").exit(1)
        result = authorizer.authorize(args.role, args.tool)
    elif args.list:
        if not args.role:
            print("Error: --list requires --role", file=__import__("sys").stderr)
            __import__("sys").exit(1)
        result = authorizer.list_allowed_tools(args.role)
    elif args.validate:
        result = authorizer.validate_config()
    else:
        parser.print_help()
        return

    if args.json:
        print(json.dumps(result, indent=2))
    else:
        if args.check:
            status = "ALLOWED" if result["allowed"] else "DENIED"
            print(f"Authorization: {status}")
            print(f"  Role: {result['role']}")
            print(f"  Tool: {result['tool']}")
            print(f"  Reason: {result['reason']}")
        elif args.list:
            print(f"Role: {result['role']}")
            print(f"  Allow: {', '.join(result.get('allow', []))}")
            print(f"  Deny: {', '.join(result.get('deny', []))}")
            print(f"  Default: {result.get('default_policy', 'deny')}")
        elif args.validate:
            status = "VALID" if result["valid"] else "INVALID"
            print(f"Config Validation: {status}")
            print(f"  Roles: {', '.join(result.get('roles', []))}")
            for e in result.get("errors", []):
                print(f"  [ERROR] {e}")
            for w in result.get("warnings", []):
                print(f"  [WARN] {w}")


if __name__ == "__main__":
    main()
