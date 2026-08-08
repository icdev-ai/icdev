# [TEMPLATE: CUI // SP-CTI]
# ICDEV™ Pre-Tool-Use Hook — Safety validation before tool execution
# Adapted from ADW pre_tool_use.py

"""Pre-tool-use hook that validates tool calls before execution.

This file is now a thin adapter. Every check lives in
``tools/hooks/shared_checks.py`` so that this hook and
``tools/airgap/hook_compat.py::run_pre_tool_check`` — the function every
non-Claude-Code orchestrator calls — enforce literally the same rules and
cannot drift apart (hgx-guard-01, hgx-guard-02).

Blocks:
    - Dangerous rm -rf commands
    - Access to .env files containing secrets
    - UPDATE/DELETE/DROP/TRUNCATE on every append-only table (D6, NIST AU)
    - Direct sqlite3.connect() that bypasses the storage layer
    - D-ORCH-8 tiered file access violations
    - Deletion of a remote branch that still holds unmerged work
    - `git worktree add` outside the sanctioned roots

To register a new append-only table, edit ``APPEND_ONLY_TABLES`` in
``tools/hooks/shared_checks.py`` — the literal used to live here, while the
headless path carried a 22-entry copy that had drifted ~340 tables behind it.

Exit codes:
    0 = allow tool call
    2 = block tool call (shows error to Claude)
"""

import importlib.util
import json
import sys
from pathlib import Path

HOOKS_DIR = Path(__file__).resolve().parent
# rls-bypass: repo root resolved from __file__, never os.getcwd() — required for
# hgx-guard-02 because this hook runs from whichever worktree invoked it, so cwd
# is the worktree root rather than the canonical repository root.
PROJECT_ROOT = HOOKS_DIR.parent.parent


def load_shared_checks():
    """Load ``shared_checks`` straight from disk. Returns None if unavailable.

    Deliberately NOT ``import tools.hooks.shared_checks``: the ``tools`` package
    shim eagerly constructs an ``LLMRouter`` (~80 ms), and this hook is a fresh
    process on the critical path of every single tool call. ``shared_checks`` is
    stdlib-only at import time precisely so it can be loaded this way.

    The two copies are byte-identical (enforced by
    ``tests/test_hgx_guard_parity.py``), so either resolves to the same rules.
    """
    for rel in ("tools/hooks/shared_checks.py", "icdev/tools/hooks/shared_checks.py"):
        path = PROJECT_ROOT / rel
        if not path.exists():
            continue
        try:
            spec = importlib.util.spec_from_file_location("icdev_shared_checks", path)
            module = importlib.util.module_from_spec(spec)
            # Register before exec: @dataclass resolves annotations through
            # sys.modules[cls.__module__] and raises AttributeError without it.
            sys.modules["icdev_shared_checks"] = module
            spec.loader.exec_module(module)
            return module
        except Exception:
            continue
    return None


_SHARED = None


def _shared():
    """Memoized :func:`load_shared_checks`, for the back-compat predicates."""
    global _SHARED
    if _SHARED is None:
        _SHARED = load_shared_checks()
    return _SHARED


# ── Back-compat predicates ─────────────────────────────────────────────────
#
# These used to be implemented here and are imported by name from several test
# modules. They now delegate to the shared registry so there is exactly one
# implementation of each rule.


def is_dangerous_rm_command(command: str) -> bool:
    """True if *command* is a destructive rm."""
    mod = _shared()
    return bool(mod and mod.check_dangerous_rm(mod.ToolCall(name="Bash", command=command)))


def is_env_file_access(tool_name: str, tool_input: dict) -> bool:
    """True if the call would read or write a secret-bearing .env file."""
    mod = _shared()
    return bool(mod and mod.check_env_file_access(mod.classify(tool_name, tool_input)))


def is_append_only_table_modification(tool_name: str, tool_input: dict) -> bool:
    """True if the call would UPDATE/DELETE/DROP/TRUNCATE an append-only table."""
    mod = _shared()
    return bool(mod and mod.check_append_only_tables(mod.classify(tool_name, tool_input)))


def is_direct_sqlite_usage(tool_name: str, tool_input: dict) -> bool:
    """True if the call introduces a raw sqlite3.connect() past the storage layer."""
    mod = _shared()
    return bool(mod and mod.check_direct_sqlite(mod.classify(tool_name, tool_input)))


def check_file_access_tiers(tool_name: str, tool_input: dict) -> str:
    """D-ORCH-8 tier violation message, or "" when allowed."""
    mod = _shared()
    return (mod.check_file_access_tiers(mod.classify(tool_name, tool_input)) if mod else "")


def check_worktree_path(tool_name: str, tool_input: dict) -> str:
    """Unsanctioned `git worktree add` message, or "" when allowed."""
    mod = _shared()
    return (mod.check_worktree_path(mod.classify(tool_name, tool_input)) if mod else "")


def check_branch_deletion(tool_name: str, tool_input: dict) -> str:
    """Unmerged remote-branch deletion message, or "" when allowed."""
    mod = _shared()
    return (mod.check_branch_deletion(mod.classify(tool_name, tool_input)) if mod else "")


def _remote_branch_delete_targets(command: str):
    """Remote branches *command* would delete. Empty when it deletes none."""
    mod = _shared()
    return mod._remote_branch_delete_targets(command) if mod else []


def _worktree_add_target(command: str, posix=None):
    """Target path of a ``git worktree add`` in *command*, or None."""
    mod = _shared()
    return mod._worktree_add_target(command, posix) if mod else None


def main():
    try:
        input_data = json.load(sys.stdin)
        tool_name = input_data.get("tool_name", "")
        tool_input = input_data.get("tool_input", {})

        checks = load_shared_checks()
        if checks is None:
            # Fail open: a guard that cannot load must never be the reason a
            # session cannot work. Loudly, so the gap is visible.
            print(
                "WARNING: pre_tool_use could not load tools/hooks/shared_checks.py "
                "— tool calls are running UNGUARDED.",
                file=sys.stderr,
            )
            sys.exit(0)

        outcome = checks.evaluate(
            tool_name, tool_input, path=checks.PATH_CLAUDE_CODE
        )

        for warning in outcome.warnings:
            print(
                f"WARNING: [{warning['check']}] {warning['reason']}",
                file=sys.stderr,
            )

        if not outcome.allowed:
            print(outcome.reason, file=sys.stderr)
            sys.exit(2)

        sys.exit(0)

    except SystemExit:
        raise
    except json.JSONDecodeError:
        sys.exit(0)
    except Exception:
        sys.exit(0)


if __name__ == "__main__":
    main()
