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
    # -- Browser (oss-browse-03 seam 4; supersedes oss-browse-01-d2) --------
    # Opt-in per role via the role's tool list, NOT in the default set: a
    # co-worker that can click inside a platform managing ATO artifacts is a
    # deliberate grant. Scope, budget and audit are enforced by
    # tools/browser/scope.py; nothing is re-implemented here.
    "browser_navigate": {
        "type": "function",
        "is_read_only": False,
        "function": {
            "name": "browser_navigate",
            "is_read_only": False,
            "description": (
                "Open a URL in an audited, scope-limited browser and return the page as "
                "indexed interactive elements. Only hosts on the allowlist in "
                "args/browser_scope.yaml are reachable (loopback only by default) - a "
                "refused host never reaches the driver."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "url": {"type": "string", "description": "URL to open."},
                },
                "required": ["url"],
            },
        },
    },
    "browser_read_state": {
        "type": "function",
        "is_read_only": True,
        "function": {
            "name": "browser_read_state",
            "is_read_only": True,
            "description": (
                "Return the current page as a numbered list of interactive elements. Act "
                "on them by index (click 14), never by inventing a CSS selector. Indices "
                "are valid only until the next read."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "screenshot": {"type": "boolean", "description": "Also capture a PNG."},
                },
            },
        },
    },
    "browser_click": {
        "type": "function",
        "is_read_only": False,
        "function": {
            "name": "browser_click",
            "is_read_only": False,
            "description": "Click the element carrying this index from the latest browser_read_state.",
            "parameters": {
                "type": "object",
                "properties": {
                    "index": {"type": "integer", "description": "Element index from the latest read."},
                },
                "required": ["index"],
            },
        },
    },
    "browser_type": {
        "type": "function",
        "is_read_only": False,
        "function": {
            "name": "browser_type",
            "is_read_only": False,
            "description": (
                "Type text into the element at this index. For credentials write "
                "<secret>NAME</secret> - the value is resolved at the driver and never "
                "enters your context, the transcript, or the audit row."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "index": {"type": "integer"},
                    "text": {"type": "string"},
                    "enter": {"type": "boolean", "description": "Send Enter after typing."},
                },
                "required": ["index", "text"],
            },
        },
    },
    "browser_screenshot": {
        "type": "function",
        "is_read_only": True,
        "function": {
            "name": "browser_screenshot",
            "is_read_only": True,
            "description": "Capture a screenshot of the current page and return its path.",
            "parameters": {
                "type": "object",
                "properties": {
                    "name": {"type": "string", "description": "Filename stem."},
                },
            },
        },
    },
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
    "patch_file": {
        "type": "function",
        "is_read_only": False,
        "function": {
            "name": "patch_file",
            "is_read_only": False,
            "description": (
                "Apply a targeted string replacement to a file within the role's "
                "declared folder_access scopes. Finds old_string exactly once in the "
                "file and replaces it with new_string. Fails if old_string appears "
                "zero or more than once (provide more context to make it unique). "
                "Safer than write_file for partial edits."
            ),
            "parameters": {
                "type": "object",
                "required": ["path", "old_string", "new_string"],
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "Path to the file to patch (within folder_access scope).",
                    },
                    "old_string": {
                        "type": "string",
                        "description": "Exact string to find in the file (must appear exactly once).",
                    },
                    "new_string": {
                        "type": "string",
                        "description": "Replacement string.",
                    },
                },
                "additionalProperties": False,
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
    "post_result": {
        "type": "function",
        "is_read_only": False,
        "function": {
            "name": "post_result",
            "is_read_only": False,
            "description": (
                "Share a typed artifact with sibling agents via the coordination namespace. "
                "The value is stored under the given key and can be read by any agent "
                "in the same coordination namespace."
            ),
            "parameters": {
                "type": "object",
                "required": ["key", "value"],
                "properties": {
                    "key": {
                        "type": "string",
                        "description": "Artifact key (e.g. 'analysis_result', 'file_list').",
                    },
                    "value": {
                        "description": "Value to store. Must be JSON-serializable.",
                    },
                },
                "additionalProperties": False,
            },
        },
    },
    "read_result": {
        "type": "function",
        "is_read_only": True,
        "function": {
            "name": "read_result",
            "is_read_only": True,
            "description": (
                "Read a shared artifact posted by a sibling agent. "
                "Returns the stored value or '(not found)' if the key doesn't exist."
            ),
            "parameters": {
                "type": "object",
                "required": ["key"],
                "properties": {
                    "key": {
                        "type": "string",
                        "description": "Artifact key to read.",
                    },
                },
                "additionalProperties": False,
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
                    "coordination_namespace": {
                        "type": "string",
                        "description": (
                            "Optional coordination namespace for the spawned agent. "
                            "Defaults to the parent's namespace. Set an explicit value "
                            "to create an isolated or shared artifact space."
                        ),
                    },
                },
                "required": ["task"],
            },
        },
    },
    "parallel_agents": {
        "type": "function",
        "is_read_only": True,
        "function": {
            "name": "parallel_agents",
            "description": (
                "Fan out multiple independent sub-agent tasks in parallel. "
                "Each task runs its own agent loop concurrently. "
                "Results are stored in the coordination bus under the task key "
                "and returned as a combined summary. "
                "Hard limits: ≤8 tasks per call, ≤8 concurrent workers."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "tasks": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "key": {
                                    "type": "string",
                                    "description": "Unique identifier for this task's result (used to read it back via read_result).",
                                },
                                "task": {
                                    "type": "string",
                                    "description": "The user prompt / task description for this sub-agent.",
                                },
                                "role": {
                                    "type": "string",
                                    "description": "Optional system prompt / role for the sub-agent.",
                                },
                                "tools": {
                                    "type": "array",
                                    "items": {"type": "string"},
                                    "description": "Tool names available to this sub-agent. Defaults to ['read_file', 'list_files', 'done'].",
                                },
                                "max_iterations": {
                                    "type": "integer",
                                    "description": "Max turns for this sub-agent (default 6, max 20).",
                                },
                            },
                            "required": ["key", "task"],
                        },
                        "description": "List of sub-agent tasks to run concurrently (max 8).",
                    },
                    "max_parallel": {
                        "type": "integer",
                        "description": "Maximum concurrent sub-agent workers (default 4, max 8).",
                    },
                },
                "required": ["tasks"],
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
        self._coordination_namespace = getattr(spec, "coordination_namespace", None) or instance_id

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
        if name.startswith("browser_"):
            return self._make_browser_handler(name)
        if name == "read_file":
            return self._read_file
        if name == "write_file":
            return self._write_file
        if name == "patch_file":
            return self._patch_file
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
        if name == "parallel_agents":
            return self._parallel_agents
        if name == "post_result":
            return self._post_result
        if name == "read_result":
            return self._read_result
        return None

    # ------------------------------------------------------------------
    # Handlers
    # ------------------------------------------------------------------

    # ------------------------------------------------------------------
    # Browser (oss-browse-03)
    # ------------------------------------------------------------------

    def _make_browser_handler(self, name: str):
        """Bind one browser tool to this co-worker's audited session.

        One AgentBrowser per co-worker instance, created lazily and reused, so
        the per-run action budget in args/browser_scope.yaml bounds the instance
        rather than resetting on every call. run_id carries the instance id,
        which is what ties an audit_trail row back to a co-worker.
        """
        from tools.agent_toolkit import (
            browser_click,
            browser_navigate,
            browser_read_state,
            browser_screenshot,
            browser_type,
        )

        fns = {
            "browser_navigate": lambda b, inp: browser_navigate(inp.get("url", ""), browser=b),
            "browser_read_state": lambda b, inp: browser_read_state(
                browser=b, screenshot=bool(inp.get("screenshot", False))),
            "browser_click": lambda b, inp: browser_click(int(inp.get("index", -1)), browser=b),
            "browser_type": lambda b, inp: browser_type(
                int(inp.get("index", -1)), str(inp.get("text", "")),
                enter=bool(inp.get("enter", False)), browser=b),
            "browser_screenshot": lambda b, inp: browser_screenshot(
                name=inp.get("name"), browser=b),
        }
        fn = fns.get(name)
        if fn is None:
            return None

        def _handler(inp: "dict[str, Any]", stop: "threading.Event | None") -> str:
            browser = self._get_browser()
            if browser is None:
                return "Browser unavailable in this environment."
            result = fn(browser, inp)
            if result.get("denied"):
                # Surfaced as text, not an exception: a scope refusal is
                # something the model should read and route around.
                return "Refused by browser scope policy: " + str(result.get("reason", ""))
            if not result.get("ok"):
                return "Browser action failed: " + str(result.get("error", "unknown error"))
            state = result.get("state")
            if state is not None:
                lines = [
                    "URL: " + str(state.get("url", "")),
                    "Title: " + str(state.get("title", "")),
                ]
                for el in state.get("elements", [])[:60]:
                    lines.append(
                        "[%s] <%s> %s"
                        % (el.get("index"), el.get("tag", ""), (el.get("text") or "")[:80])
                    )
                if state.get("truncated"):
                    lines.append("... (element list truncated)")
                return "\n".join(lines)
            if "path" in result:
                return "Screenshot saved to " + str(result["path"])
            res = result.get("result", {})
            return str(res.get("detail") or res)

        return _handler

    def _get_browser(self):
        """Lazily create one audited browser session for this instance."""
        existing = getattr(self, "_browser", None)
        if existing is not None:
            return existing
        try:
            from tools.browser.agent_browser import AgentBrowser

            self._browser = AgentBrowser(run_id="ace:" + str(self.instance_id))
        except Exception as exc:  # noqa: BLE001
            logger.warning("agent_tools: browser unavailable (%s)", exc)
            self._browser = None
        return self._browser

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

    def _patch_file(self, inp: dict[str, Any], stop: threading.Event | None) -> str:
        from icdev.tools.ace.file_access_broker import FileAccessBroker, ScopeViolationError

        path = _path_arg(inp)
        old_string = inp.get("old_string")
        new_string = inp.get("new_string")

        if not path:
            return "error: 'path' is required"
        if old_string is None:
            return "error: 'old_string' is required"
        if new_string is None:
            return "error: 'new_string' is required"

        broker = FileAccessBroker(self._folder_access)
        try:
            resolved = broker._resolve(path, need_write=True)  # noqa: SLF001
        except ScopeViolationError:
            raise

        if not resolved.exists():
            return f"error: file not found: {path}"
        if resolved.is_dir():
            return f"error: '{path}' is a directory, not a file"

        try:
            content = resolved.read_text(encoding="utf-8")
        except Exception as exc:
            return f"error reading {path}: {exc}"

        count = content.count(old_string)
        if count == 0:
            return f"error: old_string not found in {path}"
        if count > 1:
            return (
                f"error: old_string appears {count} times in {path} — "
                "provide more surrounding context to make it unique"
            )

        new_content = content.replace(old_string, new_string, 1)
        try:
            resolved.write_text(new_content, encoding="utf-8", newline="")
        except Exception as exc:
            return f"error writing {path}: {exc}"

        net_lines = new_string.count("\n") - old_string.count("\n")
        return (
            f"Patched {path}: replaced {len(old_string)} chars → {len(new_string)} chars "
            f"(net {net_lines:+d} lines)"
        )

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

        # Allow explicit coordination_namespace override for the child.
        # Without an override, the child uses self._coordination_namespace via
        # the bound methods from self.build() — inheritance is automatic.
        explicit_ns = (inp.get("coordination_namespace") or "").strip()
        if explicit_ns and explicit_ns != self._coordination_namespace:
            from icdev.tools.ace.agent_coordination import (
                post_result as _post_fn,
                _NOT_FOUND,
            )
            if "post_result" in sub_handlers:
                def _patched_post(inp_, stop_, _ns=explicit_ns, _inst=self.instance_id):
                    key = (inp_.get("key") or "").strip()
                    if not key:
                        return "error: 'key' is required"
                    try:
                        return _post_fn(_ns, key, inp_.get("value"), posted_by=_inst)
                    except Exception as exc:
                        return f"error posting result: {exc}"
                sub_handlers["post_result"] = _patched_post
            if "read_result" in sub_handlers:
                def _patched_read(inp_, stop_, _ns=explicit_ns):
                    key = (inp_.get("key") or "").strip()
                    if not key:
                        return "error: 'key' is required"
                    try:
                        import icdev.tools.ace.agent_coordination as _m
                        val = _m.read_result(_ns, key)
                    except Exception as exc:
                        return f"error reading result: {exc}"
                    if val is _NOT_FOUND:
                        return f"(not found: key={key!r} in namespace={_ns!r})"
                    import json as _json
                    return _json.dumps(val, indent=2)
                sub_handlers["read_result"] = _patched_read

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

    def _parallel_agents(self, inp: dict[str, Any], stop: threading.Event | None) -> str:
        """Fan out N sub-agent tasks concurrently using ThreadPoolExecutor.

        Results are posted to the coordination bus under each task's key
        and returned as a merged summary string.
        """
        import concurrent.futures as _cf

        raw_tasks = inp.get("tasks") or []
        if not raw_tasks:
            return "error: 'tasks' list is required and must be non-empty"
        if len(raw_tasks) > 8:
            return f"error: max 8 tasks per parallel_agents call (got {len(raw_tasks)})"

        max_parallel = min(int(inp.get("max_parallel") or 4), 8)

        def _run_one(task_spec: dict[str, Any], idx: int) -> dict[str, Any]:
            key = (task_spec.get("key") or "").strip()
            task_text = (task_spec.get("task") or "").strip()
            if not key:
                return {"key": f"task_{idx}", "error": "task 'key' is required"}
            if not task_text:
                return {"key": key, "error": "task 'task' text is required"}
            try:
                sub_inp = {
                    "task": task_text,
                    "role": task_spec.get("role") or "",
                    "tools": task_spec.get("tools") or ["read_file", "list_files", "done"],
                    "max_iterations": task_spec.get("max_iterations") or 6,
                }
                result_text = self._spawn_agent(sub_inp, stop)
                try:
                    self._post_result({"key": key, "value": result_text}, stop)
                except Exception:  # noqa: BLE001
                    pass
                return {"key": key, "result": result_text}
            except Exception as exc:  # noqa: BLE001
                return {"key": key, "error": str(exc)}

        results: list[dict[str, Any]] = []
        with _cf.ThreadPoolExecutor(max_workers=max_parallel) as pool:
            future_map = {pool.submit(_run_one, t, i): i for i, t in enumerate(raw_tasks)}
            for fut in _cf.as_completed(future_map, timeout=600):
                try:
                    results.append(fut.result())
                except Exception as exc:  # noqa: BLE001
                    idx = future_map[fut]
                    key = (raw_tasks[idx].get("key") or f"task_{idx}") if idx < len(raw_tasks) else f"task_{idx}"
                    results.append({"key": key, "error": str(exc)})

        key_order = {(t.get("key") or f"task_{i}"): i for i, t in enumerate(raw_tasks)}
        results.sort(key=lambda r: key_order.get(r.get("key", ""), 999))

        n_ok = sum(1 for r in results if "result" in r)
        n_err = sum(1 for r in results if "error" in r)
        lines = [f"parallel_agents: {len(raw_tasks)} tasks dispatched — {n_ok} succeeded, {n_err} failed.\n"]
        for r in results:
            key = r.get("key", "?")
            if "error" in r:
                lines.append(f"[{key}] ERROR: {r['error']}")
            else:
                preview = str(r.get("result", ""))
                if len(preview) > 800:
                    preview = preview[:800] + "…"
                lines.append(f"[{key}]:\n{preview}")

        return "\n\n".join(lines)

    def _post_result(self, inp: dict[str, Any], stop: threading.Event | None) -> str:
        from icdev.tools.ace.agent_coordination import post_result as _post

        key = (inp.get("key") or "").strip()
        if not key:
            return "error: 'key' is required"
        value = inp.get("value")
        try:
            return _post(self._coordination_namespace, key, value, posted_by=self.instance_id)
        except Exception as exc:
            return f"error posting result: {exc}"

    def _read_result(self, inp: dict[str, Any], stop: threading.Event | None) -> str:
        import json as _json
        from icdev.tools.ace.agent_coordination import read_result as _read, _NOT_FOUND

        key = (inp.get("key") or "").strip()
        if not key:
            return "error: 'key' is required"
        try:
            val = _read(self._coordination_namespace, key)
        except Exception as exc:
            return f"error reading result: {exc}"
        if val is _NOT_FOUND:
            return f"(not found: key={key!r} in namespace={self._coordination_namespace!r})"
        return _json.dumps(val, indent=2)
