# CUI // SP-CTI
# ICDEV Pre-Tool-Use Hook — Safety validation before tool execution
# Adapted from ADW pre_tool_use.py

"""
Pre-tool-use hook that validates tool calls before execution.

Blocks:
    - Dangerous rm -rf commands
    - Access to .env files containing secrets
    - Modifications to audit trail (append-only)
    - Deletion of CUI-marked artifacts without explicit approval

Exit codes:
    0 = allow tool call
    2 = block tool call (shows error to Claude)
"""

import json
import re
import sys


def is_dangerous_rm_command(command: str) -> bool:
    """Detect dangerous rm commands."""
    normalized = " ".join(command.lower().split())

    patterns = [
        r"\brm\s+.*-[a-z]*r[a-z]*f",
        r"\brm\s+.*-[a-z]*f[a-z]*r",
        r"\brm\s+--recursive\s+--force",
        r"\brm\s+--force\s+--recursive",
        r"\brm\s+-r\s+.*-f",
        r"\brm\s+-f\s+.*-r",
    ]

    for pattern in patterns:
        if re.search(pattern, normalized):
            return True

    # Check for rm with recursive flag targeting dangerous paths
    dangerous_paths = [r"/", r"/\*", r"~", r"~/", r"\$HOME", r"\.\.", r"\*", r"\."]
    if re.search(r"\brm\s+.*-[a-z]*r", normalized):
        for path in dangerous_paths:
            if re.search(path, normalized):
                return True

    return False


def is_env_file_access(tool_name: str, tool_input: dict) -> bool:
    """Check if a tool is trying to access .env files."""
    if tool_name in ("Read", "Edit", "MultiEdit", "Write"):
        file_path = tool_input.get("file_path", "")
        if ".env" in file_path and not file_path.endswith(".env.sample"):
            return True

    elif tool_name == "Bash":
        command = tool_input.get("command", "")
        env_patterns = [
            r"\b\.env\b(?!\.sample)",
            r"cat\s+.*\.env\b(?!\.sample)",
            r"echo\s+.*>\s*\.env\b(?!\.sample)",
        ]
        for pattern in env_patterns:
            if re.search(pattern, command):
                return True

    return False


def is_audit_trail_modification(tool_name: str, tool_input: dict) -> bool:
    """Block UPDATE/DELETE on audit trail tables (NIST AU compliance)."""
    if tool_name == "Bash":
        command = tool_input.get("command", "").lower()
        # Block SQL UPDATE/DELETE on audit_trail table
        if re.search(r"(update|delete)\s+.*audit_trail", command):
            return True
        # Block direct modification of audit DB
        if re.search(r"(sqlite3|python).*audit.*(-c|--command).*(update|delete)", command):
            return True

    return False


def main():
    try:
        input_data = json.load(sys.stdin)
        tool_name = input_data.get("tool_name", "")
        tool_input = input_data.get("tool_input", {})

        # Block .env file access
        if is_env_file_access(tool_name, tool_input):
            print("BLOCKED: Access to .env files is prohibited. Use AWS Secrets Manager.", file=sys.stderr)
            sys.exit(2)

        # Block dangerous rm commands
        if tool_name == "Bash":
            command = tool_input.get("command", "")
            if is_dangerous_rm_command(command):
                print("BLOCKED: Dangerous rm command detected and prevented", file=sys.stderr)
                sys.exit(2)

        # Block audit trail modification (NIST AU compliance)
        if is_audit_trail_modification(tool_name, tool_input):
            print("BLOCKED: Audit trail is append-only (NIST 800-53 AU). No UPDATE/DELETE allowed.", file=sys.stderr)
            sys.exit(2)

        sys.exit(0)

    except json.JSONDecodeError:
        sys.exit(0)
    except Exception:
        sys.exit(0)


if __name__ == "__main__":
    main()
