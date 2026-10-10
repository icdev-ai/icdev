#!/usr/bin/env python3
"""omx-spike-01 SCRATCH bridge: harness tool call -> ICDEV run_pre_tool_check.

Not production code. Reads one JSON object on stdin:

    {"harness": "opencode"|"pi", "tool": "<harness tool name>", "args": {...}}

maps it to ICDEV's Claude-Code-shaped ``tool_name`` / ``tool_input``, calls
``tools.airgap.hook_compat.run_pre_tool_check`` (the ONE copy of the guard, in
tools/hooks/shared_checks.py) and prints one JSON verdict on stdout:

    {"allowed": bool, "reason": str, "tool_name": str, "tool_input": {...}}

Exit code is always 0 when a verdict was printed; the caller treats ANY other
outcome (non-zero exit, unparsable stdout, timeout) as DENY -- fail closed.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

# Repo root: ICDEV_ROOT when the harness runs elsewhere, else this file's repo.
ROOT = Path(os.environ.get("ICDEV_ROOT") or Path(__file__).resolve().parents[3])
sys.path.insert(0, str(ROOT))

# harness tool name -> ICDEV (Claude Code) tool name. The checks in
# shared_checks.py key on the Claude Code spelling ("Bash", "Write", ...):
# check_dangerous_rm returns None for tool_name != "Bash", so an UNMAPPED
# lowercase "bash" would sail through. The mapping is load-bearing.
TOOL_NAMES = {
    "bash": "Bash",
    # Pi ships a separate `powershell` tool on Windows (same {command} input).
    # The rm check parses POSIX syntax only -- Remove-Item is NOT caught.
    "powershell": "Bash",
    "write": "Write",
    "edit": "Edit",
    "multiedit": "Edit",
    "read": "Read",
    "grep": "Grep",
    "glob": "Glob",
    "find": "Glob",
    "ls": "LS",
    "list": "LS",
    "webfetch": "WebFetch",
}

# harness arg key -> ICDEV arg key (opencode is camelCase, Pi uses `path`).
ARG_KEYS = {
    "filePath": "file_path",
    "path": "file_path",
    "oldString": "old_string",
    "newString": "new_string",
    "oldText": "old_string",
    "newText": "new_string",
    "replaceAll": "replace_all",
}


def to_icdev(tool: str, args: dict) -> tuple[str, dict]:
    name = TOOL_NAMES.get(tool.lower(), tool)
    mapped = {ARG_KEYS.get(k, k): v for k, v in (args or {}).items()}
    # Pi's edit carries a list of {oldText,newText}; flatten the first for the
    # content-sensitive checks and keep the raw list alongside.
    edits = mapped.get("edits")
    if isinstance(edits, list) and edits and isinstance(edits[0], dict):
        mapped.setdefault("old_string", edits[0].get("oldText", ""))
        mapped.setdefault("new_string", edits[0].get("newText", ""))
    return name, mapped


def main() -> int:
    req = json.loads(sys.stdin.read() or "{}")
    tool_name, tool_input = to_icdev(req.get("tool", ""), req.get("args") or {})
    from tools.airgap.hook_compat import run_pre_tool_check

    verdict = run_pre_tool_check(tool_name, tool_input)
    print(json.dumps({**verdict, "tool_name": tool_name, "tool_input": tool_input}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
