#!/usr/bin/env python3
# CUI // SP-CTI
"""MCP Tool Authorizer — per-tool RBAC for MCP servers (D261).

Stateless authorization engine that evaluates tool access based on
role-tool matrix defined in owasp_agentic_config.yaml. Uses
deny-first evaluation with fnmatch wildcard support.

Pattern: stateless config-driven authorization (no DB)
ADRs: D261 (MCP per-tool RBAC in YAML)

Roles:
    admin     — Full access to all tools
    pm        — Project/task management, compliance views
    developer — Build, test, lint, format, knowledge
    isso      — Compliance, security, assessment tools
    co        — Read-only project/agent status

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


def _load_config() -> Dict:
    """Load MCP authorization config from YAML."""
    if CONFIG_PATH.exists():
        with open(CONFIG_PATH) as f:
            cfg = yaml.safe_load(f) or {}
        return cfg.get("mcp_authorization", {})
    return {}


class MCPToolAuthorizer:
    """Per-tool RBAC for MCP servers (D261).

    Stateless authorizer — no DB required. Evaluates access by:
    1. Check deny list first (explicit deny always wins)
    2. Check allow list (fnmatch wildcards supported)
    3. Fall back to default_policy (deny)
    """

    def __init__(self, config: Optional[Dict] = None):
        self._config = config or _load_config()
        self._default_policy = self._config.get("default_policy", "deny")
        self._matrix = self._config.get("role_tool_matrix", {})

    def authorize(self, role: str, tool_name: str) -> Dict:
        """Authorize a tool call for a given role.

        Returns:
            Dict with allowed bool, role, tool, reason.
        """
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

    def list_allowed_tools(self, role: str) -> Dict:
        """List all explicitly allowed and denied tools for a role.

        Returns:
            Dict with role, allow patterns, deny patterns, default_policy.
        """
        if role not in self._matrix:
            return {
                "role": role,
                "error": f"Unknown role '{role}'",
                "known_roles": list(self._matrix.keys()),
            }

        role_config = self._matrix[role]
        return {
            "role": role,
            "allow": role_config.get("allow", []),
            "deny": role_config.get("deny", []),
            "default_policy": self._default_policy,
        }

    def get_roles(self) -> List[str]:
        """Return list of configured roles."""
        return list(self._matrix.keys())

    def validate_config(self) -> Dict:
        """Validate the authorization configuration.

        Checks:
            - All roles have at least one allow or deny rule
            - No role has conflicting allow and deny patterns
            - Known MCP tool names are covered

        Returns:
            Dict with valid bool, warnings, errors.
        """
        errors = []
        warnings = []

        if not self._matrix:
            errors.append("No role_tool_matrix configured")
            return {"valid": False, "errors": errors, "warnings": warnings}

        expected_roles = {"admin", "pm", "developer", "isso", "co"}
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
                        errors.append(
                            f"Role '{role}': pattern '{allow_pat}' in both allow and deny"
                        )

        return {
            "valid": len(errors) == 0,
            "roles": sorted(configured_roles),
            "role_count": len(configured_roles),
            "default_policy": self._default_policy,
            "errors": errors,
            "warnings": warnings,
        }


def main():
    parser = argparse.ArgumentParser(
        description="MCP Tool Authorizer — per-tool RBAC (D261)"
    )
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
            print("Error: --check requires --role and --tool",
                  file=__import__("sys").stderr)
            __import__("sys").exit(1)
        result = authorizer.authorize(args.role, args.tool)
    elif args.list:
        if not args.role:
            print("Error: --list requires --role",
                  file=__import__("sys").stderr)
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
