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

import re
import subprocess
import threading
from pathlib import Path
from typing import Any, Callable, Dict, List, Tuple

# ``DONE`` terminates run_agent_loop when a handler returns it.
try:
    from tools.llm.agent_loop import DONE
except ImportError:  # packaged-only install
    from icdev.tools.llm.agent_loop import DONE

ToolHandler = Callable[[Dict[str, Any], "threading.Event | None"], str]

_GREP_DEFAULT_RESULTS = 50
_GREP_MAX_RESULTS = 200
_SEARCH_DEFAULT_RESULTS = 100
_SEARCH_MAX_RESULTS = 500
_COMMAND_DEFAULT_TIMEOUT = 600
_COMMAND_MAX_TIMEOUT = 1800
_MAX_OUTPUT_CHARS = 32_000

# Command allowlist for the build agent.
#
# The skill-runner default (`python tools/`, `python -m tools`, `python -c`)
# permits none of the gates this agent is GRADED BY: make_pipeline_grader runs
# ruff, the coherence checker and pytest, so an agent restricted to that list is
# judged on checks it cannot run itself and can only guess at.
#
# This list adds exactly those gates and nothing else. It stays python-only,
# which is what keeps it portable — no shell, no `&&`, and no POSIX-only binary
# such as grep/sed/find that would not exist on Windows. The agent is also
# confined to a disposable git worktree and every call still passes the safety
# gate, so the widening is bounded by construction.
_BUILD_ALLOWED_PREFIXES: Tuple[str, ...] = (
    "python tools/",
    "python -m tools",
    "python -c",
    "python -m pytest",
    "python -m ruff",
)

# Directories never worth walking for a code search. `.git` alone is tens of
# thousands of loose objects in a fresh worktree, so skipping it is the
# difference between a sub-second grep and a stalled turn.
_SKIP_DIRS = frozenset(
    {".git", "__pycache__", "node_modules", ".venv", "venv", ".tmp",
     ".pytest_cache", ".ruff_cache", ".mypy_cache", "dist", "build"}
)

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
            "name": "grep_files",
            "description": (
                "Search file CONTENTS for a regex under the worktree. Returns "
                "'path:line:text' matches. Use this to find a symbol before editing."
            ),
            "is_read_only": True,
            "parameters": {
                "type": "object",
                "properties": {
                    "pattern": {"type": "string", "description": "Regex (falls back to literal substring if invalid)."},
                    "path": {"type": "string", "description": "File or directory to search, relative to the worktree root (default '.')."},
                    "max_results": {"type": "integer", "description": f"Cap on matches (default {_GREP_DEFAULT_RESULTS}, max {_GREP_MAX_RESULTS})."},
                },
                "required": ["pattern"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "search_files",
            "description": "Find files by GLOB pattern (e.g. '**/*.py'). Returns paths, not contents.",
            "is_read_only": True,
            "parameters": {
                "type": "object",
                "properties": {
                    "pattern": {"type": "string", "description": "Glob pattern, e.g. '**/test_*.py'."},
                    "path": {"type": "string", "description": "Directory to search from, relative to the worktree root (default '.')."},
                    "max_results": {"type": "integer", "description": f"Cap on results (default {_SEARCH_DEFAULT_RESULTS}, max {_SEARCH_MAX_RESULTS})."},
                },
                "required": ["pattern"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "git_diff",
            "description": (
                "Show uncommitted changes in the worktree, as unified diff. Call this "
                "before 'done' to check what you actually changed."
            ),
            "is_read_only": True,
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "Limit the diff to this path (optional)."},
                    "stat": {"type": "boolean", "description": "Summary only (--stat) instead of full diff."},
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "run_command",
            "description": (
                "Run one allowlisted command in the worktree and return exit code + "
                "output. Only 'python tools/...', 'python -m tools...' and 'python -c' "
                "are permitted. Use it to run the tests you are graded on."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "command": {"type": "string", "description": "Full command line, e.g. 'python -m pytest tests/test_x.py -q'."},
                    "timeout": {"type": "integer", "description": f"Seconds before the command is killed (default {_COMMAND_DEFAULT_TIMEOUT})."},
                },
                "required": ["command"],
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


# Text I/O below deliberately goes through open(..., newline="") rather than
# Path.read_text/write_text.
#
# `newline=""` disables universal-newline translation in BOTH directions, so a
# file keeps the endings it already had and the agent sees exactly what is on
# disk. Without it, on Windows, `write_text` turns every "\n" into "\r\n": the
# agent patches one line and the ENTIRE file is rewritten, which shows up as a
# whole-file `git diff`, floods the reviewer, and makes the pipeline grader's
# changed-file signal meaningless. The same edit on Linux is a clean one-line
# diff — so the executor's output depended on the host OS. CI is ubuntu-only,
# so nothing caught it. See tests/genesis/test_rubric_build_tools.py.


def _read_text(path: Path) -> str:
    """Read text without translating line endings."""
    with open(path, encoding="utf-8", errors="replace", newline="") as fh:
        return fh.read()


def _write_text(path: Path, text: str) -> None:
    """Write text without translating line endings."""
    with open(path, "w", encoding="utf-8", newline="") as fh:
        fh.write(text)


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
            return _read_text(target)
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
            _write_text(target, content)
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
            text = _read_text(target)
            count = text.count(old)
            if count == 0:
                return f"error: old_string not found in {inp.get('path')}"
            if count > 1:
                return f"error: old_string appears {count} times — add surrounding context to make it unique"
            _write_text(target, text.replace(old, new, 1))
            return f"Patched {inp.get('path')}"
        except ValueError:
            return "error: path escapes the worktree root"
        except Exception as exc:  # noqa: BLE001
            return f"error: {exc}"

    def _walk(base: Path):
        """Yield files under *base*, pruning noise directories."""
        if base.is_file():
            yield base
            return
        stack = [base]
        while stack:
            current = stack.pop()
            try:
                entries = list(current.iterdir())
            except OSError:
                continue
            for entry in entries:
                if entry.is_dir():
                    if entry.name not in _SKIP_DIRS:
                        stack.append(entry)
                elif entry.is_file():
                    yield entry

    def _grep(inp: Dict[str, Any], stop: "threading.Event | None") -> str:
        pattern = str(inp.get("pattern") or "").strip()
        if not pattern:
            return "error: 'pattern' is required"
        try:
            max_results = min(
                int(inp.get("max_results") or _GREP_DEFAULT_RESULTS), _GREP_MAX_RESULTS
            )
        except (TypeError, ValueError):
            max_results = _GREP_DEFAULT_RESULTS
        try:
            base = _resolve(root, str(inp.get("path", ".")))
        except ValueError:
            return "error: path escapes the worktree root"
        if not base.exists():
            return f"error: path not found: {inp.get('path', '.')}"

        # Regex when it compiles, literal substring when it does not — an agent
        # searching for "foo(bar)" should get matches, not a regex error.
        try:
            rx: Any = re.compile(pattern)
        except re.error:
            rx = None

        results: List[str] = []
        for file_path in _walk(base):
            if stop is not None and stop.is_set():
                results.append("[search interrupted - stop event fired]")
                break
            try:
                text = _read_text(file_path)
            except OSError:
                continue
            if "\x00" in text[:1024]:  # binary
                continue
            try:
                rel = file_path.relative_to(root).as_posix()
            except ValueError:  # pragma: no cover - _resolve already guards
                continue
            for line_num, line in enumerate(text.splitlines(), 1):
                hit = rx.search(line) if rx is not None else (pattern in line)
                if not hit:
                    continue
                results.append(f"{rel}:{line_num}:{line.strip()[:200]}")
                if len(results) >= max_results:
                    results.append(
                        f"[truncated - {max_results} matches reached; "
                        "narrow the pattern or raise max_results]"
                    )
                    return "\n".join(results)
        return "\n".join(results) if results else "(no matches)"

    def _search(inp: Dict[str, Any], stop: "threading.Event | None") -> str:
        del stop
        pattern = str(inp.get("pattern") or "").strip()
        if not pattern:
            return "error: 'pattern' is required"
        try:
            max_results = min(
                int(inp.get("max_results") or _SEARCH_DEFAULT_RESULTS),
                _SEARCH_MAX_RESULTS,
            )
        except (TypeError, ValueError):
            max_results = _SEARCH_DEFAULT_RESULTS
        try:
            base = _resolve(root, str(inp.get("path", ".")))
        except ValueError:
            return "error: path escapes the worktree root"
        if not base.is_dir():
            return f"error: not a directory: {inp.get('path', '.')}"
        try:
            matches = sorted(
                p.relative_to(root).as_posix()
                for p in base.glob(pattern)
                if p.is_file()
                and not _SKIP_DIRS.intersection(p.relative_to(root).parts)
            )
        except (ValueError, OSError) as exc:
            return f"error: {exc}"
        if not matches:
            return "(no matches)"
        if len(matches) > max_results:
            shown = matches[:max_results]
            shown.append(f"[truncated - {len(matches)} files matched]")
            return "\n".join(shown)
        return "\n".join(matches)

    def _git_diff(inp: Dict[str, Any], stop: "threading.Event | None") -> str:
        del stop
        # List argv, shell=False: portable, and no quoting hazard from a path
        # the model chose.
        cmd = ["git", "diff"]
        if inp.get("stat"):
            cmd.append("--stat")
        rel = str(inp.get("path") or "").strip()
        if rel:
            try:
                _resolve(root, rel)
            except ValueError:
                return "error: path escapes the worktree root"
            cmd += ["--", rel]
        try:
            proc = subprocess.run(  # nosec B603,B607 - fixed argv, no shell
                cmd,
                cwd=str(root),
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=120,
            )
        except FileNotFoundError:
            return "error: git is not available"
        except subprocess.TimeoutExpired:
            return "error: git diff timed out"
        except Exception as exc:  # noqa: BLE001
            return f"error: {exc}"
        if proc.returncode != 0:
            return f"error: git diff exited {proc.returncode}: {(proc.stderr or '').strip()[:500]}"
        out = (proc.stdout or "").strip()
        if not out:
            return "(no uncommitted changes)"
        return out[:_MAX_OUTPUT_CHARS]

    def _run_command(inp: Dict[str, Any], stop: "threading.Event | None") -> str:
        del stop
        command = str(inp.get("command") or "").strip()
        if not command:
            return "error: 'command' is required"
        try:
            timeout = min(
                int(inp.get("timeout") or _COMMAND_DEFAULT_TIMEOUT), _COMMAND_MAX_TIMEOUT
            )
        except (TypeError, ValueError):
            timeout = _COMMAND_DEFAULT_TIMEOUT
        try:
            # Shares ONE allowlist with the headless skill runner
            # (python tools/ | python -m tools | python -c). cwd is the task's
            # worktree, not the shared checkout — without it the command reads
            # the wrong tree and reports on the wrong branch.
            from tools.skills.invoke import run_command as _invoke_run

            result = _invoke_run(
                command,
                [],
                timeout=timeout,
                cwd=str(root),
                allowed_prefixes=_BUILD_ALLOWED_PREFIXES,
            )
        except Exception as exc:  # noqa: BLE001
            return f"error running command: {exc}"
        if result.get("skipped"):
            return f"refused: {result.get('reason', 'command not allowlisted')}"
        rc = result.get("returncode")
        out = (result.get("stdout") or "").strip()
        err = (result.get("stderr") or "").strip()
        rendered = f"exit_code={rc}\n--- stdout ---\n{out}\n--- stderr ---\n{err}".rstrip()
        return rendered[:_MAX_OUTPUT_CHARS]

    def _done(inp: Dict[str, Any], stop: "threading.Event | None") -> str:
        return DONE

    handlers: Dict[str, ToolHandler] = {
        "read_file": _read,
        "list_files": _list,
        "write_file": _write,
        "patch_file": _patch,
        "grep_files": _grep,
        "search_files": _search,
        "git_diff": _git_diff,
        "run_command": _run_command,
        "done": _done,
    }
    return list(_SCHEMAS), handlers
