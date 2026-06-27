# CUI // SP-CTI
"""ACE agent tool registry — binds existing ACE built-ins to LLM tool schemas.

Used by :meth:`CoWorkerThread._run_agent_loop` to expose a set of tools to the
LLM agent loop (:mod:`icdev.tools.llm.agent_loop`). Each tool maps an OpenAI
function-calling schema to a handler that wraps an **existing** ACE built-in —
no new execution paths are introduced:

- ``read_file``  → :class:`icdev.tools.ace.file_access_broker.FileAccessBroker.read`
- ``write_file`` → :class:`FileAccessBroker.write`
- ``run_tool``   → :class:`icdev.tools.ace.tool_runner.ToolRunner.run`
- ``done``       → sentinel that terminates the agent loop
- ``list_files`` → scoped directory listing (read-only)

Handlers receive ``(input_dict, stop_event)`` and return a string result, matching
the :data:`icdev.tools.llm.agent_loop.ToolHandler` contract. Exceptions are left
to propagate — the agent loop catches them and surfaces the error back to the LLM
as a ``tool_result`` so the agent can adapt (e.g. a ``ScopeViolationError`` on a
path outside ``folder_access`` tells the model to pick a different path).

Trust gating reuses the existing built-ins unchanged: ``run_tool`` routes through
``ToolRunner``, which raises ``TrustKernelDeniedError`` for non-green tiers. In
agent mode v1 that surfaces to the LLM as a tool error (the agent learns the
constraint) rather than triggering a HITL pause mid-turn; the pre-loop confidence
gate (``trust_score < 0.6``) in :class:`CoWorkerThread` still applies.
"""
from __future__ import annotations

import threading
from typing import Any, Callable

from icdev.tools.llm.agent_loop import DONE, AgentLoopUnsupported, run_agent_loop
from tools.logging.icdev_logger import get_logger

# Module-level LLMRouter reference so _spawn_agent can be monkeypatched in tests.
try:
    from tools.llm.router import LLMRouter
except Exception:  # noqa: BLE001
    LLMRouter = None  # type: ignore[assignment,misc]

logger = get_logger("icdev.ace.agent_tools")

ToolHandler = Callable[[dict[str, Any], "threading.Event | None"], str]


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
            "description": "Read a UTF-8 text file within the role's declared folder_access scopes. Returns the file contents.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "Path relative to the repository root."},
                },
                "required": ["path"],
            },
        },
    },
    "write_file": {
        "type": "function",
        "function": {
            "name": "write_file",
            "description": "Write UTF-8 text to a file within the role's declared rw folder_access scopes. Overwrites existing content. Creates parent directories.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "Path relative to the repository root."},
                    "content": {"type": "string", "description": "Full file content to write."},
                },
                "required": ["path", "content"],
            },
        },
    },
    "list_files": {
        "type": "function",
        "is_read_only": True,
        "function": {
            "name": "list_files",
            "is_read_only": True,
            "description": "List files in a directory within the role's declared folder_access scopes. Returns one path per line.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "Directory path relative to the repository root."},
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
                "Find files matching a glob pattern within the role's declared folder_access "
                "scopes. Returns one matching path per line. Use '**/*.py' for recursive "
                "search, '*.yaml' for a flat directory search. The 'path' argument sets the "
                "search root (defaults to '.' — repo root). Runs in parallel with other "
                "read-only tools."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "pattern": {
                        "type": "string",
                        "description": "Glob pattern, e.g. '**/*.py', '*.yaml', 'test_*.py'.",
                    },
                    "path": {
                        "type": "string",
                        "description": (
                            "Directory to search in (relative to repository root). "
                            "Defaults to '.' (repo root)."
                        ),
                    },
                    "max_results": {
                        "type": "integer",
                        "description": "Maximum number of paths to return (default 100, max 500).",
                    },
                },
                "required": ["pattern"],
            },
        },
    },
    "grep_files": {
        "type": "function",
        "is_read_only": True,
        "function": {
            "name": "grep_files",
            "is_read_only": True,
            "description": (
                "Search file contents for a text substring or regex pattern within the "
                "role's declared folder_access scopes. Returns matches in "
                "'filepath:line_num:content' format. Searches recursively when 'path' is a "
                "directory; searches a single file when 'path' is a file. Runs in parallel "
                "with other read-only tools."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "pattern": {
                        "type": "string",
                        "description": "Text substring or Python regex to search for.",
                    },
                    "path": {
                        "type": "string",
                        "description": (
                            "File or directory to search (relative to repository root). "
                            "Defaults to '.' (repo root)."
                        ),
                    },
                    "max_results": {
                        "type": "integer",
                        "description": "Maximum number of matching lines to return (default 50, max 200).",
                    },
                },
                "required": ["pattern"],
            },
        },
    },
    "run_tool": {
        "type": "function",
        "function": {
            "name": "run_tool",
            "description": (
                "Run an allowlisted ICDEV Python tool (subprocess). The command "
                "must exactly match an entry in the role's icdev_tools list and "
                "start with 'python tools/', 'python -m tools.', 'python icdev/', "
                "or 'python -m icdev.'. Non-green trust tiers are blocked. "
                "Returns stdout, stderr, and the exit code."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "command": {"type": "string", "description": "The exact allowlisted command string."},
                },
                "required": ["command"],
            },
        },
    },
    "done": {
        "type": "function",
        "function": {
            "name": "done",
            "description": "Signal that the task is complete. Call this once the objective is satisfied and verified.",
            "parameters": {
                "type": "object",
                "properties": {
                    "summary": {"type": "string", "description": "Concise summary of what was accomplished."},
                    "changed_files": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "List of file paths created or modified.",
                    },
                },
                "required": ["summary"],
            },
        },
    },
    "spawn_agent": {
        "type": "function",
        "function": {
            "name": "spawn_agent",
            "description": (
                "Spawn a sub-agent to handle an isolated subtask. The sub-agent runs "
                "its own agent loop with a scoped toolset and returns a summary. Only "
                "the final answer is returned — the sub-agent's conversation history "
                "does not accumulate in the parent's context. Use this to delegate "
                "independent subtasks (e.g. 'read all files in dir X and summarise', "
                "'run tool Y and return parsed output')."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "task": {
                        "type": "string",
                        "description": "Full task description for the sub-agent.",
                    },
                    "role": {
                        "type": "string",
                        "description": (
                            "Optional system prompt / persona for the sub-agent. "
                            "Defaults to a generic helpful sub-agent role."
                        ),
                    },
                    "tools": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": (
                            "Tool names to give the sub-agent. Must be a subset of "
                            "the parent's available tools (read_file, list_files, "
                            "write_file, run_tool, done). Defaults to read-only tools."
                        ),
                    },
                    "max_iterations": {
                        "type": "integer",
                        "description": "Max LLM turns for the sub-agent (default 6, max 20).",
                    },
                },
                "required": ["task"],
            },
        },
    },
}


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------


# Common aliases LLMs emit for the file-path parameter even when the schema
# declares `path`. Accept the canonical name first, then the usual synonyms so
# a `file_path`/`filename` call doesn't silently resolve to "" and fail.
_PATH_ALIASES = ("path", "file_path", "filepath", "filename", "file")


def _path_arg(inp: dict[str, Any]) -> str:
    for key in _PATH_ALIASES:
        val = inp.get(key)
        if isinstance(val, str) and val.strip():
            return val.strip()
    return ""


class AgentToolRegistry:
    """Builds ``(tools, tool_handlers)`` for an ACE agent-mode co-worker.

    Args:
        spec:        CoWorkerSpec (carries coworker_id, trust_tier, folder_access,
                     icdev_tools, llm_function).
        instance_id: ACE instance ID for audit trails.
        stop_event:  Optional stop event forwarded to handlers.
    """

    def __init__(
        self,
        spec: Any,
        instance_id: str,
        stop_event: threading.Event | None = None,
    ) -> None:
        self.spec = spec
        self.instance_id = instance_id
        self.stop_event = stop_event
        self._coworker_id = getattr(spec, "coworker_id", getattr(spec, "name", "unknown"))
        self._trust_tier = getattr(spec, "trust_tier", "green")
        self._folder_access = list(getattr(spec, "folder_access", []) or [])
        self._icdev_tools = list(getattr(spec, "icdev_tools", []) or [])

    def build(self, tool_names: list[str]) -> tuple[list[dict[str, Any]], dict[str, ToolHandler]]:
        """Return ``(tools, tool_handlers)`` filtered to *tool_names*.

        Unknown names are logged and skipped. ``tool_names`` defaults to the full
        core set when empty.
        """
        names = list(tool_names or [])
        if not names:
            names = ["read_file", "write_file", "run_tool", "done"]

        tools: list[dict[str, Any]] = []
        handlers: dict[str, ToolHandler] = {}
        for name in names:
            schema = _SCHEMAS.get(name)
            if schema is None:
                logger.warning("agent_tools: unknown tool %r requested — skipping", name)
                continue
            handler = self._make_handler(name)
            if handler is None:
                continue
            tools.append(schema)
            handlers[name] = handler
        return tools, handlers

    # ------------------------------------------------------------------
    # Handler factory
    # ------------------------------------------------------------------

    def _make_handler(self, name: str) -> ToolHandler | None:
        if name == "read_file":
            return self._read_file
        if name == "write_file":
            return self._write_file
        if name == "list_files":
            return self._list_files
        if name == "search_files":
            return self._search_files
        if name == "grep_files":
            return self._grep_files
        if name == "run_tool":
            return self._run_tool
        if name == "done":
            return self._done
        if name == "spawn_agent":
            return self._spawn_agent
        return None

    # ------------------------------------------------------------------
    # Handlers
    # ------------------------------------------------------------------

    def _read_file(self, inp: dict[str, Any], stop: threading.Event | None) -> str:
        from icdev.tools.ace.file_access_broker import FileAccessBroker

        path = _path_arg(inp)
        broker = FileAccessBroker(self._folder_access)
        return broker.read(path, coworker_id=self._coworker_id, instance_id=self.instance_id)

    def _write_file(self, inp: dict[str, Any], stop: threading.Event | None) -> str:
        from icdev.tools.ace.file_access_broker import FileAccessBroker

        path = _path_arg(inp)
        content = inp.get("content", "")
        broker = FileAccessBroker(self._folder_access)
        n = broker.write(path, content, coworker_id=self._coworker_id, instance_id=self.instance_id)
        return f"Wrote {n} bytes to {path}"

    def _list_files(self, inp: dict[str, Any], stop: threading.Event | None) -> str:
        from icdev.tools.ace.file_access_broker import FileAccessBroker, ScopeViolationError

        path = _path_arg(inp)
        broker = FileAccessBroker(self._folder_access)
        # Reuse the broker's scope resolver by attempting a read of the directory.
        try:
            resolved = broker._resolve(path, need_write=False)  # noqa: SLF001 — scoped listing
        except ScopeViolationError:
            raise
        if not resolved.exists():
            raise FileNotFoundError(f"Directory not found: {resolved}")
        if not resolved.is_dir():
            return str(resolved)
        entries = sorted(p.name for p in resolved.iterdir())
        return "\n".join(entries) if entries else "(empty)"

    def _search_files(self, inp: dict[str, Any], stop: threading.Event | None) -> str:
        import re as _re
        from icdev.tools.ace.file_access_broker import FileAccessBroker, ScopeViolationError

        pattern = (inp.get("pattern") or "").strip()
        if not pattern:
            return "error: 'pattern' is required"
        path = _path_arg(inp) or "."
        max_results = min(int(inp.get("max_results") or 100), 500)

        broker = FileAccessBroker(self._folder_access)
        try:
            resolved = broker._resolve(path, need_write=False)  # noqa: SLF001
        except ScopeViolationError:
            raise
        if not resolved.is_dir():
            return f"error: '{path}' is not a directory"

        try:
            matches = sorted(
                str(p.relative_to(resolved))
                for p in resolved.glob(pattern)
                if p.is_file()
            )
        except Exception as exc:
            return f"error: glob failed — {exc}"

        truncated = len(matches) > max_results
        matches = matches[:max_results]
        out = "\n".join(matches) if matches else "(no matches)"
        if truncated:
            out += f"\n[truncated — showing first {max_results}; narrow the pattern or reduce max_results]"
        elif matches:
            out = f"Found {len(matches)} file(s):\n{out}"
        return out

    def _grep_files(self, inp: dict[str, Any], stop: threading.Event | None) -> str:
        import re as _re
        from icdev.tools.ace.file_access_broker import FileAccessBroker, ScopeViolationError

        pattern = (inp.get("pattern") or "").strip()
        if not pattern:
            return "error: 'pattern' is required"
        path = _path_arg(inp) or "."
        max_results = min(int(inp.get("max_results") or 50), 200)

        broker = FileAccessBroker(self._folder_access)
        try:
            resolved = broker._resolve(path, need_write=False)  # noqa: SLF001
        except ScopeViolationError:
            raise

        # Compile pattern as regex; fall back to literal substring search on error.
        try:
            rx = _re.compile(pattern)
        except _re.error:
            rx = None

        def _matches(line: str) -> bool:
            return bool(rx.search(line)) if rx is not None else (pattern in line)

        results: list[str] = []
        search_files_iter = (
            resolved.rglob("*") if resolved.is_dir() else [resolved]
        )
        for file_path in search_files_iter:
            if stop is not None and stop.is_set():
                results.append("[search interrupted — stop event fired]")
                break
            if not file_path.is_file():
                continue
            try:
                text = file_path.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            rel = str(file_path.relative_to(resolved)) if resolved.is_dir() else file_path.name
            for line_num, line in enumerate(text.splitlines(), 1):
                if _matches(line):
                    results.append(f"{rel}:{line_num}:{line.strip()[:120]}")
                    if len(results) >= max_results:
                        results.append(
                            f"[truncated — {max_results} matches reached; "
                            "narrow the pattern or reduce max_results]"
                        )
                        return "\n".join(results)

        return "\n".join(results) if results else "(no matches)"

    def _run_tool(self, inp: dict[str, Any], stop: threading.Event | None) -> str:
        from icdev.tools.ace.tool_runner import ToolRunner

        command = inp.get("command", "")
        runner = ToolRunner(self._icdev_tools)
        result = runner.run(
            command,
            coworker_id=self._coworker_id,
            instance_id=self.instance_id,
            trust_tier=self._trust_tier,
        )
        rc = result.get("returncode")
        out = result.get("stdout", "")
        err = result.get("stderr", "")
        return f"exit_code={rc}\n--- stdout ---\n{out}\n--- stderr ---\n{err}".rstrip()

    def _done(self, inp: dict[str, Any], stop: threading.Event | None) -> str:
        summary = inp.get("summary", "")
        changed = inp.get("changed_files", []) or []
        logger.info(
            "agent_tools: done called by %s — summary=%r changed_files=%d",
            self._coworker_id,
            summary[:200],
            len(changed),
        )
        # Stash the summary on the spec-bound registry so CoWorkerThread can
        # audit/broadcast it after the loop returns.
        self.done_summary = summary  # type: ignore[attr-defined]
        self.done_changed_files = list(changed)  # type: ignore[attr-defined]
        return DONE

    def _spawn_agent(self, inp: dict[str, Any], stop: threading.Event | None) -> str:
        """Spawn a child agent loop for an isolated subtask.

        The child runs :func:`run_agent_loop` with a scoped toolset and the
        same file-access and trust-tier constraints as the parent. Only the
        child's ``final_content`` is returned to the parent — the full child
        conversation history is discarded to keep the parent's context small.
        """
        task = (inp.get("task") or "").strip()
        if not task:
            return "spawn_agent error: 'task' is required."

        role_prompt = (inp.get("role") or "").strip() or (
            "You are a helpful sub-agent. Complete the task using the available "
            "tools, then call done with a summary of what you accomplished."
        )
        sub_tool_names = list(inp.get("tools") or ["read_file", "list_files", "done"])
        max_iter = min(int(inp.get("max_iterations") or 6), 20)

        sub_tools, sub_handlers = self.build(sub_tool_names)
        if not sub_tools:
            return "spawn_agent error: no valid tools resolved for sub-agent."

        try:
            router = LLMRouter()
        except Exception as exc:
            return f"spawn_agent error: could not create router — {exc}"

        logger.info(
            "agent_tools: spawning sub-agent for %s — task=%r tools=%s max_iter=%d",
            self._coworker_id,
            task[:120],
            list(sub_handlers.keys()),
            max_iter,
        )

        try:
            # Use the module-level run_agent_loop (patchable in tests).
            child = run_agent_loop(
                router,
                system_prompt=role_prompt,
                user_prompt=task,
                tools=sub_tools,
                tool_handlers=sub_handlers,
                max_iterations=max_iter,
                stop_event=stop,
            )
        except AgentLoopUnsupported as exc:
            return f"spawn_agent error: provider does not support tool use — {exc}"
        except Exception as exc:
            return f"spawn_agent error: {type(exc).__name__}: {exc}"

        status = "done" if child.done else f"truncated ({child.result_subtype})"
        header = (
            f"[sub-agent: {status} | turns={child.turns} | "
            f"tools_called={len(child.tool_call_log)} | "
            f"session={child.session_id}]"
        )
        body = child.final_content or "(no output)"
        logger.info(
            "agent_tools: sub-agent completed for %s — %s",
            self._coworker_id,
            status,
        )
        return f"{header}\n{body}"