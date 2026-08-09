# CUI // SP-CTI
"""Built-in starter toolset for the standalone agent runtime (SAG).

A deliberately small, read-mostly toolset so the runtime is useful on its own
before dynamic tool auto-discovery lands in sag-reg-01:

- ``read_file``    — read a UTF-8 text file under the repo root (read-only).
- ``search_files`` — ripgrep-style content search under the repo root (read-only).
- ``health_check`` — run the platform health check (read-only).
- ``list_skills``  — name + description of every indexed skill (read-only).
- ``load_skill``   — one named skill's full instructions (read-only).
- ``sleep_for`` / ``sleep_until`` / ``wake_on`` / ``wake_on_event`` — suspend the
  session until a time, a job, or an event (agov-wake-02).

The two skill tools come from ``tools.agent_runtime.skill_tools`` and are folded
in here rather than defined here, because ACE's own registry offers the same pair
and one implementation should back both. They are a matched pair on purpose: the
listing is cheap and body-size independent, so it can always be offered, and a
body is only paid for once the agent has picked one — progressive disclosure.

The four wake tools come from ``tools.agent_runtime.wake_tools`` and are folded
in the same way. They are the only members of this toolset that write anything,
and they are here rather than in a capability bundle because they are runtime
CONTROL, not capability: what they do is end the turn. They are the counterpart
to the natural ``end_turn`` below — the exit an agent takes when it is not done,
just not able to make progress yet. Without them the only way to wait for
something is to keep the session open and poll it.

The loop terminates naturally on ``end_turn`` (the model answers with no further
tool calls), which is the correct completion path for an interactive Q&A/coding
agent — no explicit ``done`` sentinel tool is needed here. Every handler matches
the :data:`icdev.tools.llm.agent_loop.ToolHandler`
contract ``handler(input_dict, stop_event) -> str`` and is defensive: it returns
an ``error: ...`` string rather than raising, so the agent can adapt. All file
access is confined to the repository root; ``..`` escapes are rejected.
"""
from __future__ import annotations

import io
import os
import re
import threading
from pathlib import Path
from typing import Any, Callable

from tools.agent_runtime import skill_tools, wake_tools

ToolHandler = Callable[[dict[str, Any], "threading.Event | None"], str]

# Resolve the repository root from this file's location (never from cwd — the
# runtime may be launched from a worktree or a subdirectory). This module is
# mirrored to BOTH <repo>/tools/agent_runtime/ and <repo>/icdev/tools/agent_runtime/,
# which sit at different depths, so we walk upward to a repo sentinel rather than
# counting parents.
def _find_repo_root(start: Path) -> Path:
    sentinels = ("pyproject.toml", ".git", "CLAUDE.md")
    for parent in [start, *start.parents]:
        if any((parent / s).exists() for s in sentinels):
            return parent
    # Fallback: two levels up from tools/agent_runtime/ (best effort).
    return start.parents[1] if len(start.parents) > 1 else start


_REPO_ROOT = _find_repo_root(Path(__file__).resolve().parent)

_MAX_READ_BYTES = 200_000
_MAX_SEARCH_MATCHES = 100


def _repo_root() -> Path:
    return _REPO_ROOT


def _resolve_in_repo(rel: str) -> Path | None:
    """Resolve ``rel`` under the repo root, or return None if it escapes."""
    if not rel:
        return None
    root = _repo_root()
    candidate = (root / rel).resolve()
    try:
        candidate.relative_to(root)
    except ValueError:
        return None
    return candidate


# ---------------------------------------------------------------------------
# Tool schemas (OpenAI function-calling format)
# ---------------------------------------------------------------------------

_SCHEMAS: dict[str, dict[str, Any]] = {
    "read_file": {
        "type": "function",
        "is_read_only": True,
        "function": {
            "name": "read_file",
            "is_read_only": True,
            "description": (
                "Read a UTF-8 text file within the repository. Returns the file "
                "contents (truncated at 200 KB). Path is relative to the repo root."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "Path relative to the repository root.",
                    },
                },
                "required": ["path"],
            },
        },
    },
    "search_files": {
        "type": "function",
        "is_read_only": True,
        "function": {
            "name": "search_files",
            "is_read_only": True,
            "description": (
                "Search file contents under the repository for a regular "
                "expression. Returns up to 100 matching lines as "
                "'path:lineno: line'. Optionally restrict to a subdirectory or a "
                "filename glob."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "pattern": {
                        "type": "string",
                        "description": "Python regular expression to match.",
                    },
                    "path": {
                        "type": "string",
                        "description": "Optional subdirectory (relative to repo root) to search under.",
                    },
                    "glob": {
                        "type": "string",
                        "description": "Optional filename glob filter, e.g. '*.py'.",
                    },
                },
                "required": ["pattern"],
            },
        },
    },
    "health_check": {
        "type": "function",
        "is_read_only": True,
        "function": {
            "name": "health_check",
            "is_read_only": True,
            "description": (
                "Run the ICDEV platform health check (environment, database, "
                "dependencies) and return a JSON summary."
            ),
            "parameters": {"type": "object", "properties": {}},
        },
    },
    **skill_tools.SCHEMAS,
    **wake_tools.SCHEMAS,
}


# ---------------------------------------------------------------------------
# Handlers
# ---------------------------------------------------------------------------


def _handle_read_file(inp: dict[str, Any], _stop: threading.Event | None) -> str:
    path = str(inp.get("path", "")).strip()
    if not path:
        return "error: 'path' is required"
    resolved = _resolve_in_repo(path)
    if resolved is None:
        return f"error: path escapes repository root: {path!r}"
    if not resolved.exists():
        return f"error: file not found: {path}"
    if resolved.is_dir():
        return f"error: '{path}' is a directory, not a file"
    try:
        data = resolved.read_bytes()[:_MAX_READ_BYTES]
        text = data.decode("utf-8", errors="replace")
    except Exception as exc:  # noqa: BLE001
        return f"error reading {path}: {exc}"
    return text


def _handle_search_files(inp: dict[str, Any], stop: threading.Event | None) -> str:
    pattern = str(inp.get("pattern", "")).strip()
    if not pattern:
        return "error: 'pattern' is required"
    try:
        rx = re.compile(pattern)
    except re.error as exc:
        return f"error: invalid regex: {exc}"

    sub = str(inp.get("path", "")).strip()
    base = _resolve_in_repo(sub) if sub else _repo_root()
    if base is None:
        return f"error: path escapes repository root: {sub!r}"
    if not base.exists():
        return f"error: path not found: {sub}"

    glob_pat = str(inp.get("glob", "")).strip() or "*"
    _skip_dirs = {".git", "__pycache__", "node_modules", ".venv", "venv", ".tmp"}
    matches: list[str] = []
    root = _repo_root()
    for dirpath, dirnames, filenames in os.walk(base):
        if stop is not None and stop.is_set():
            break
        dirnames[:] = [d for d in dirnames if d not in _skip_dirs]
        for fn in filenames:
            if not Path(fn).match(glob_pat):
                continue
            fp = Path(dirpath) / fn
            try:
                with io.open(fp, "r", encoding="utf-8", errors="ignore") as fh:
                    for lineno, line in enumerate(fh, start=1):
                        if rx.search(line):
                            rel = fp.resolve().relative_to(root).as_posix()
                            matches.append(f"{rel}:{lineno}: {line.rstrip()[:300]}")
                            if len(matches) >= _MAX_SEARCH_MATCHES:
                                return "\n".join(matches) + "\n... (truncated)"
            except Exception:  # noqa: BLE001 — skip unreadable/binary files
                continue
    return "\n".join(matches) if matches else "(no matches)"


def _handle_health_check(_inp: dict[str, Any], _stop: threading.Event | None) -> str:
    try:
        from tools.testing.health_check import run_health_check  # type: ignore
    except Exception:  # noqa: BLE001
        # Fall back to a minimal probe if the helper is unavailable.
        try:
            from tools.db.storage import get_connection

            conn = get_connection()
            conn.close()
            return '{"status": "ok", "db": "reachable"}'
        except Exception as exc:  # noqa: BLE001
            return f'{{"status": "degraded", "error": {exc!r}}}'
    try:
        import json as _json

        result = run_health_check()  # type: ignore[call-arg]
        return _json.dumps(result, default=str)[:_MAX_READ_BYTES]
    except Exception as exc:  # noqa: BLE001
        return f"error running health_check: {exc}"


_HANDLERS: dict[str, ToolHandler] = {
    "read_file": _handle_read_file,
    "search_files": _handle_search_files,
    "health_check": _handle_health_check,
    **skill_tools.HANDLERS,
    **wake_tools.HANDLERS,
}


def build_builtin_toolset() -> tuple[list[dict[str, Any]], dict[str, ToolHandler]]:
    """Return ``(tools, handlers)`` for the built-in starter toolset.

    ``tools`` is the OpenAI function-calling schema list passed to
    :func:`run_agent_loop`; ``handlers`` maps each tool name to its handler.
    """
    tools = [_SCHEMAS[name] for name in _HANDLERS]
    handlers = dict(_HANDLERS)
    return tools, handlers


def builtin_tool_names() -> list[str]:
    """Return the sorted names of the built-in tools (used by ``/tools``)."""
    return sorted(_HANDLERS)
