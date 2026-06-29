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

from icdev.tools.llm.agent_loop import DONE
from tools.logging.icdev_logger import get_logger

logger = get_logger("icdev.ace.agent_tools")

ToolHandler = Callable[[dict[str, Any], "threading.Event | None"], str]


# ---------------------------------------------------------------------------
# Tool schemas (OpenAI function-calling format)
# ---------------------------------------------------------------------------

_SCHEMAS: dict[str, dict[str, Any]] = {
    "read_file": {
        "type": "function",
        "function": {
            "name": "read_file",
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
        "function": {
            "name": "list_files",
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
        if name == "run_tool":
            return self._run_tool
        if name == "done":
            return self._done
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
