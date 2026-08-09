# CUI // SP-CTI
"""MCP Wrapper Generator — scans ICDEV™ tools and produces MCP-compatible wrappers.

Discovers CLI tools that support ``--json`` output and generates thin MCP
tool handlers that invoke them via subprocess, enabling any MCP-compatible
client to call ICDEV™ tools without custom integration code.

Architecture decision: deterministic scanning + template-based generation
(no LLM in the critical path).
"""

from __future__ import annotations

import json
import pathlib
import re
import sys
from datetime import datetime, timezone
from typing import Any

from tools.common.helpers import now_iso


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

_TOOLS_ROOT = pathlib.Path(__file__).resolve().parent.parent  # tools/


def _has_json_flag(path: pathlib.Path) -> bool:
    """Heuristic: file contains ``--json`` argument registration."""
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
        return bool(re.search(r"""['"]\-\-json['"]""", text))
    except OSError:
        return False


def _is_cli_tool(path: pathlib.Path) -> bool:
    """Heuristic: file has an ``if __name__`` guard."""
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
        return "__name__" in text and "__main__" in text
    except OSError:
        return False


# ---------------------------------------------------------------------------
# Public API — consumed by tools/dashboard/app.py
# ---------------------------------------------------------------------------


def scan_tools(tools_dir: str | None = None) -> dict:
    """Discover ICDEV™ CLI tools eligible for MCP wrapping.

    Returns
    -------
    dict
        ``{status, discovered: [{path, name, has_json}], total, with_json_flag}``
    """
    root = pathlib.Path(tools_dir) if tools_dir else _TOOLS_ROOT
    discovered: list[dict[str, Any]] = []

    if not root.is_dir():
        return {
            "status": "error",
            "error": f"Tools directory not found: {root}",
            "discovered": [],
            "total": 0,
            "with_json_flag": 0,
        }

    for py_file in sorted(root.rglob("*.py")):
        # Skip tests, __pycache__, __init__, etc.
        if "__pycache__" in str(py_file) or py_file.name.startswith("__") or "test" in py_file.name.lower():
            continue
        if not _is_cli_tool(py_file):
            continue

        has_json = _has_json_flag(py_file)
        discovered.append(
            {
                "path": str(py_file),
                "name": py_file.stem,
                "has_json": has_json,
            }
        )

    return {
        "status": "ok",
        "discovered": discovered,
        "total": len(discovered),
        "with_json_flag": sum(1 for d in discovered if d["has_json"]),
    }


def list_wrapped(wrappers_dir: str | None = None) -> dict:
    """List already-generated MCP wrapper files.

    Returns
    -------
    dict
        ``{wrappers: [{path, name, generated_at}], count}``
    """
    root = pathlib.Path(wrappers_dir) if wrappers_dir else _TOOLS_ROOT / "mcp" / "wrappers"
    wrappers: list[dict[str, Any]] = []

    if root.is_dir():
        for f in sorted(root.glob("*.py")):
            if f.name.startswith("__"):
                continue
            wrappers.append(
                {
                    "path": str(f),
                    "name": f.stem,
                    "generated_at": datetime.fromtimestamp(f.stat().st_mtime, tz=timezone.utc).strftime(
                        "%Y-%m-%dT%H:%M:%SZ"
                    ),
                }
            )

    return {
        "wrappers": wrappers,
        "count": len(wrappers),
    }


def wrap_tool(tool_path: str, dry_run: bool = False) -> dict:
    """Generate an MCP wrapper for a single CLI tool.

    Parameters
    ----------
    tool_path : str
        Path to the Python CLI tool to wrap.
    dry_run : bool
        If True, return what *would* be generated without writing.

    Returns
    -------
    dict
        ``{status, tool_path, wrapper_path, name, dry_run, generated_at}``
    """
    source = pathlib.Path(tool_path)
    if not source.exists():
        return {"status": "error", "error": f"Tool not found: {tool_path}"}

    wrapper_name = f"mcp_{source.stem}"
    wrapper_dir = _TOOLS_ROOT / "mcp" / "wrappers"
    wrapper_path = wrapper_dir / f"{wrapper_name}.py"

    if dry_run:
        return {
            "status": "dry_run",
            "tool_path": str(source),
            "wrapper_path": str(wrapper_path),
            "name": wrapper_name,
            "dry_run": True,
            "generated_at": now_iso(),
        }

    wrapper_dir.mkdir(parents=True, exist_ok=True)
    wrapper_path.write_text(
        _render_wrapper(wrapper_name, str(source)),
        encoding="utf-8", newline="",
    )

    return {
        "status": "ok",
        "tool_path": str(source),
        "wrapper_path": str(wrapper_path),
        "name": wrapper_name,
        "dry_run": False,
        "generated_at": now_iso(),
    }


def wrap_all(dry_run: bool = False, limit: int = 20) -> dict:
    """Generate MCP wrappers for all discovered tools with ``--json`` support.

    Parameters
    ----------
    dry_run : bool
        Preview without writing files.
    limit : int
        Maximum number of tools to wrap in one invocation.

    Returns
    -------
    dict
        ``{status, wrapped: [...], total, skipped, dry_run, generated_at}``
    """
    scan = scan_tools()
    eligible = [d for d in scan.get("discovered", []) if d.get("has_json")]
    to_wrap = eligible[:limit]

    results: list[dict[str, Any]] = []
    for tool_info in to_wrap:
        result = wrap_tool(tool_info["path"], dry_run=dry_run)
        results.append(result)

    return {
        "status": "ok",
        "wrapped": results,
        "total": len(results),
        "skipped": len(eligible) - len(to_wrap),
        "dry_run": dry_run,
        "generated_at": now_iso(),
    }


# ---------------------------------------------------------------------------
# Template
# ---------------------------------------------------------------------------


def _render_wrapper(name: str, tool_path: str) -> str:
    """Render a minimal MCP tool handler that shells out to the CLI tool."""
    return f'''\
# CUI // SP-CTI
# Auto-generated MCP wrapper for {name}
# Source tool: {tool_path}
# Generated: {now_iso()}

"""MCP tool handler — delegates to {name} via subprocess."""

from __future__ import annotations

import json
import subprocess
import sys


TOOL_PATH = r"{tool_path}"


def handle(params: dict | None = None) -> dict:
    """MCP tool handler entry-point."""
    cmd = [sys.executable, TOOL_PATH, "--json"]
    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=120,
            stdin=subprocess.DEVNULL,
        )
        if proc.returncode == 0:
            return json.loads(proc.stdout)
        return {{"status": "error", "error": proc.stderr.strip()}}
    except subprocess.TimeoutExpired:
        return {{"status": "error", "error": "Tool timed out after 120s"}}
    except Exception as exc:
        return {{"status": "error", "error": str(exc)}}
'''


# ---------------------------------------------------------------------------
# CLI entry-point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="MCP Wrapper Generator")
    sub = parser.add_subparsers(dest="command")

    sub.add_parser("scan", help="Discover eligible tools")
    sub.add_parser("list", help="List generated wrappers")

    wrap_p = sub.add_parser("wrap", help="Wrap a single tool")
    wrap_p.add_argument("--tool-path", required=True)
    wrap_p.add_argument("--dry-run", action="store_true")

    all_p = sub.add_parser("wrap-all", help="Wrap all eligible tools")
    all_p.add_argument("--dry-run", action="store_true")
    all_p.add_argument("--limit", type=int, default=20)

    parser.add_argument("--json", action="store_true", help="JSON output")
    args = parser.parse_args()

    if args.command == "scan":
        result = scan_tools()
    elif args.command == "list":
        result = list_wrapped()
    elif args.command == "wrap":
        result = wrap_tool(args.tool_path, dry_run=args.dry_run)
    elif args.command == "wrap-all":
        result = wrap_all(dry_run=args.dry_run, limit=args.limit)
    else:
        parser.print_help()
        sys.exit(0)

    print(json.dumps(result, indent=2))
