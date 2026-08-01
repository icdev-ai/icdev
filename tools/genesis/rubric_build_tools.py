"""File-editing toolset for the rubric-gated kanban build loop.

The rubric-gated dispatch path (``_dispatch_via_rubric_loop`` in
``tools/genesis/reflexes/kanban.py``) runs an autonomous agent that must actually
EDIT files to build a task, then the delivery-pipeline gates grade the result.
:func:`run_agent_loop` needs the caller to supply the file-editing tools.

This module builds that toolset **bound to the task's isolated worktree**: every
path is resolved under ``work_dir`` with a strict traversal guard, so a build
task can only touch its own worktree — never the shared checkout or the wider
filesystem. Handlers never raise; they return an ``error: ...`` string the agent
can read and react to, which keeps one bad tool call from killing the loop.

Self-contained by design (own tool schemas, no dependency on ACE's registry) so
the kanban executor and ACE evolve independently.
"""

from __future__ import annotations

import threading
from pathlib import Path
from typing import Any, Callable, Dict, List, Tuple

# ``DONE`` terminates run_agent_loop when a handler returns it.
try:
    from tools.llm.agent_loop import DONE
except ImportError:  # packaged-only install
    from icdev.tools.llm.agent_loop import DONE

ToolHandler = Callable[[Dict[str, Any], "threading.Event | None"], str]

# OpenAI function-calling tool schemas. read_file/list_files are marked
# is_read_only so run_agent_loop can dispatch them concurrently.
_SCHEMAS: List[Dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": "read_file",
            "description": "Read a UTF-8 text file. Path is relative to the worktree root.",
            "is_read_only": True,
            "parameters": {
                "type": "object",
                "properties": {"path": {"type": "string", "description": "Path relative to the worktree root."}},
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_files",
            "description": "List entries in a directory (relative to the worktree root). Directories end with '/'.",
            "is_read_only": True,
            "parameters": {
                "type": "object",
                "properties": {"path": {"type": "string", "description": "Directory relative to the worktree root (default '.')."}},
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "write_file",
            "description": "Create or overwrite a file with the given content. Parent dirs are created.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "Path relative to the worktree root."},
                    "content": {"type": "string", "description": "Full new file content."},
                },
                "required": ["path", "content"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "patch_file",
            "description": "Replace the UNIQUE occurrence of old_string with new_string in a file. Safer than write_file for edits.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "Path relative to the worktree root."},
                    "old_string": {"type": "string", "description": "Exact text to replace (must occur exactly once)."},
                    "new_string": {"type": "string", "description": "Replacement text."},
                },
                "required": ["path", "old_string", "new_string"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "done",
            "description": "Call when the task is fully implemented and you are ready to be graded.",
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
]


def _resolve(root: Path, rel: str) -> Path:
    """Resolve *rel* under *root*, raising ValueError on traversal outside root."""
    target = (root / (rel or ".")).resolve()
    target.relative_to(root)  # raises ValueError if target escapes root
    return target


def build_worktree_toolset(work_dir: str) -> Tuple[List[Dict[str, Any]], Dict[str, ToolHandler]]:
    """Return ``(tools, tool_handlers)`` for an agent building inside *work_dir*.

    Every path is confined to ``work_dir`` (resolved + traversal-guarded). All
    handlers return strings (an ``error: ...`` string on any failure) and never
    raise, so a single bad call can't crash the agent loop.
    """
    root = Path(work_dir).resolve()

    def _read(inp: Dict[str, Any], stop: "threading.Event | None") -> str:
        try:
            target = _resolve(root, str(inp.get("path", "")))
            if not target.is_file():
                return f"error: file not found: {inp.get('path')}"
            return target.read_text(encoding="utf-8", errors="replace")
        except ValueError:
            return "error: path escapes the worktree root"
        except Exception as exc:  # noqa: BLE001 — surface to the agent, never crash the loop
            return f"error: {exc}"

    def _list(inp: Dict[str, Any], stop: "threading.Event | None") -> str:
        try:
            target = _resolve(root, str(inp.get("path", ".")))
            if not target.is_dir():
                return f"error: not a directory: {inp.get('path', '.')}"
            entries = sorted(x.name + ("/" if x.is_dir() else "") for x in target.iterdir())
            return "\n".join(entries) if entries else "(empty)"
        except ValueError:
            return "error: path escapes the worktree root"
        except Exception as exc:  # noqa: BLE001
            return f"error: {exc}"

    def _write(inp: Dict[str, Any], stop: "threading.Event | None") -> str:
        try:
            target = _resolve(root, str(inp.get("path", "")))
            content = inp.get("content", "")
            if not isinstance(content, str):
                return "error: 'content' must be a string"
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content, encoding="utf-8")
            return f"Wrote {len(content)} chars to {inp.get('path')}"
        except ValueError:
            return "error: path escapes the worktree root"
        except Exception as exc:  # noqa: BLE001
            return f"error: {exc}"

    def _patch(inp: Dict[str, Any], stop: "threading.Event | None") -> str:
        old = inp.get("old_string")
        new = inp.get("new_string")
        if old is None or new is None:
            return "error: 'old_string' and 'new_string' are required"
        try:
            target = _resolve(root, str(inp.get("path", "")))
            if not target.is_file():
                return f"error: file not found: {inp.get('path')}"
            text = target.read_text(encoding="utf-8")
            count = text.count(old)
            if count == 0:
                return f"error: old_string not found in {inp.get('path')}"
            if count > 1:
                return f"error: old_string appears {count} times — add surrounding context to make it unique"
            target.write_text(text.replace(old, new, 1), encoding="utf-8")
            return f"Patched {inp.get('path')}"
        except ValueError:
            return "error: path escapes the worktree root"
        except Exception as exc:  # noqa: BLE001
            return f"error: {exc}"

    def _done(inp: Dict[str, Any], stop: "threading.Event | None") -> str:
        return DONE

    handlers: Dict[str, ToolHandler] = {
        "read_file": _read,
        "list_files": _list,
        "write_file": _write,
        "patch_file": _patch,
        "done": _done,
    }
    return list(_SCHEMAS), handlers
