# CUI // SP-CTI
"""exa-bench-01: OpenAI Codex CLI adapter — a real harness behind the seam.

Until exa-bench-01 this module was a stub whose ``invoke()`` raised
``NotInstalledError``, and it was commented out of ``enabled_adapters``. The
EXA card's point is that ICDEV already HAS the many-harnesses-behind-one-seam
abstraction (``tools/agents/adapter_base.py``) and what it lacks is harnesses.
This is one.

``claude_cli.py`` is the reference for the three problems it already solved,
and the same three answers are used here:

* **PATHEXT-aware discovery** — ``shutil.which`` first (it resolves
  ``.exe``/``.cmd``/``.bat`` through PATHEXT, which is what an npm-style shim
  install needs), then a ``~/.local/bin`` secondary probe tried with each
  PATHEXT suffix, because the bare suffix-less name never exists on Windows.
  25 kanban tasks were quarantined as "no executor available" on 2026-08-01
  before that was traced on the claude side.
* **the instruction goes in over stdin from a temp file** — ``codex exec -``
  reads the prompt from stdin. A real task prompt on argv trips the Windows
  32767-char command-line limit (WinError 206).
* **the JSON envelope is parsed into ``structured``** — without it the adapter
  reports a duration and nothing else, so any cross-adapter comparison has
  every other column permanently empty.

Where this adapter deliberately differs from ``claude_cli``:

``structured`` omits what Codex does not report
    ``claude_cli`` fills ``total_cost_usd`` and ``duration_api_ms`` from the
    CLI envelope. Codex reports neither, so those keys are ABSENT rather than
    zero — a comparison must be able to tell "not reported" from "free". Same
    for token counts: present only when the CLI actually emitted them.

no ``spawn()``
    ``claude_cli.spawn()`` exists because the kanban runner owns its own
    poll/kill loop. Nothing dispatches Codex that way yet; adding a second
    execution mode with no consumer would be inventing a capability the
    exa-bench-03 probe is supposed to MEASURE.

LLM-agnostic
------------
No model id appears in this module. The model is the operator's choice, taken
from ``session.metadata['model_id']`` or ``$ICDEV_CODEX_MODEL``, and passed
through as ``--model``; with neither set the CLI uses its own configured
default. ``AgentLoopUnsupported`` is not caught here because it cannot be
raised on this path — it comes from ``icdev.tools.llm.agent_loop`` when a
resolved provider cannot serve native tool use, and this adapter runs a
subprocess rather than the loop. The analogous degradation for a shellout is
that ``invoke()`` never raises for a backend failure: an unknown flag, a
refused model, a non-zero exit or a timeout all come back as an ``AgentResult``
with ``completed=False`` and the CLI's own stderr in ``error``. Only a genuinely
absent CLI raises, which is the Protocol's contract.

OS-agnostic
-----------
``pathlib`` throughout, ``encoding='utf-8'`` and ``newline=''`` on every file
handle so a prompt's bytes survive unchanged on both line-ending conventions,
``shell=False``, and the one platform branch (PATHEXT) is two-sided.

Session metadata keys this adapter understands (all optional):

    model_id                 str  -> appended as ``--model``
    sandbox                  str  -> ``--sandbox`` mode; "" disables the flag
    skip_git_repo_check      bool -> appends ``--skip-git-repo-check``
    extra_args               list -> appended verbatim before the prompt arg
    dispatch_source          str  -> sets ICDEV_DISPATCH_SOURCE + _TASK_ID
    env                      dict -> extra environment variables
    temp_dir                 str  -> where the stdin temp file is written
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from tools.agents.adapter_base import (
    AgentResult,
    AgentSession,
    NotInstalledError,
)


# The Rust rewrite ships as ``codex``; ``openai-codex`` is kept as a secondary
# name for the older npm package. $ICDEV_CODEX_CLI overrides both.
_EXECUTABLE_NAMES = ("codex", "openai-codex")
_ENV_EXECUTABLE = "ICDEV_CODEX_CLI"
_ENV_MODEL = "ICDEV_CODEX_MODEL"
_ENV_SANDBOX = "ICDEV_CODEX_SANDBOX"

# An agent adapter exists to change files, so the default sandbox has to permit
# writes inside the working directory. Narrow it (``read-only``) or widen it
# (``danger-full-access``) per session; set it to "" to omit the flag entirely
# for a CLI build that predates it.
_DEFAULT_SANDBOX = "workspace-write"

_COMPLETION_MARKERS = (
    "[DONE]",
    "Task completed",
    "done.",
)

# Event ``type`` fragments. Codex has renamed its JSONL events across releases
# (``msg.agent_message`` -> ``item.completed``/``agent_message``), so matching
# is on a fragment rather than an exact name and every branch is optional.
_TOOL_CALL_FRAGMENTS = (
    "exec_command",
    "command_exec",
    "command_execution",
    "patch_apply",
    "file_change",
    "mcp_tool_call",
    "function_call",
    "tool_call",
    "web_search",
)
# Task/turn-level completion. Deliberately NOT a bare "complete" match: the
# newer stream wraps every item in ``item.completed``, so a substring test
# would call the run finished at the first assistant message.
_COMPLETION_PREFIXES = (
    "task_complet",
    "task_finish",
    "turn_complet",
    "thread_complet",
    "session_complet",
)
_MESSAGE_TEXT_KEYS = ("last_agent_message", "message", "text")
_DIFF_KEYS = ("unified_diff", "diff", "patch")


def resolve_codex_cli(is_windows: Optional[bool] = None) -> Optional[str]:
    """Absolute path to the Codex CLI, or None.

    ``$ICDEV_CODEX_CLI`` wins and may be either an explicit path or a bare
    name. Otherwise each known name is tried through ``shutil.which`` (PATHEXT
    on Windows), then as a ``~/.local/bin`` secondary probe for hosts where the
    binary is installed but not on PATH.

    ``is_windows`` defaults to this host. It is an explicit parameter so BOTH
    platform branches are reachable from either OS's test run — forcing the
    branch by patching ``os.name`` instead makes ``pathlib`` hand out a
    ``WindowsPath`` on Linux, which raises on construction.
    """
    override = (os.environ.get(_ENV_EXECUTABLE) or "").strip()
    names: List[str] = [override] if override else list(_EXECUTABLE_NAMES)

    if override:
        candidate = Path(override)
        if candidate.is_file():
            return str(candidate)

    for name in names:
        found = shutil.which(name)
        if found:
            return found

    for name in names:
        base = Path.home() / ".local" / "bin" / name
        for candidate in _pathext_candidates(base, is_windows):
            if candidate.is_file():
                return str(candidate)
    return None


def _pathext_candidates(
    base: Path, is_windows: Optional[bool] = None
) -> List[Path]:
    """``base`` itself, plus each PATHEXT suffix when targeting Windows.

    PATHEXT is a Windows-only variable and is always semicolon-delimited, so
    the split is on ``;`` rather than ``os.pathsep`` — the host running the
    check is not necessarily the platform being described.
    """
    if is_windows is None:
        is_windows = os.name == "nt"
    candidates = [base]
    if is_windows:
        for ext in os.environ.get("PATHEXT", ".EXE;.CMD;.BAT;.COM").split(";"):
            ext = ext.strip()
            if ext:
                candidates.append(base.with_name(base.name + ext.lower()))
    return candidates


def _unlink_quietly(path: str) -> None:
    try:
        os.unlink(path)
    except OSError:
        pass


def _event_body(event: Dict[str, Any]) -> Dict[str, Any]:
    """Unwrap the one-level envelope Codex puts around each event's payload."""
    for key in ("msg", "item", "payload"):
        inner = event.get(key)
        if isinstance(inner, dict):
            return inner
    return event


def _event_text(body: Dict[str, Any]) -> str:
    """Assistant text carried by one event, or ""."""
    for key in _MESSAGE_TEXT_KEYS:
        value = body.get(key)
        if isinstance(value, str) and value.strip():
            return value
    # Some releases carry the Responses-API content-block shape instead.
    content = body.get("content")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = [
            block.get("text", "")
            for block in content
            if isinstance(block, dict) and isinstance(block.get("text"), str)
        ]
        joined = "\n".join(p for p in parts if p.strip())
        if joined.strip():
            return joined
    return ""


def _item_kind(body: Dict[str, Any]) -> str:
    """The item-level kind ``item.completed`` carries one level down."""
    for key in ("item_type", "role"):
        value = body.get(key)
        if isinstance(value, str) and value:
            return value.replace(".", "_").lower()
    return ""


def _is_agent_message(blob: str) -> bool:
    return "agent_message" in blob or "assistant" in blob


def _usage_candidates(body: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Every place a Codex release has put token counts, outermost last.

    ``token_count`` events carry them on the body; ``turn.completed`` nests
    them under ``usage``; some builds use ``info.total_token_usage``. The list
    is ordered so a more specific container wins.
    """
    out: List[Dict[str, Any]] = [body]
    info = body.get("info")
    if isinstance(info, dict):
        out.append(info)
        nested = info.get("total_token_usage")
        if isinstance(nested, dict):
            out.append(nested)
    for key in ("total_token_usage", "usage"):
        value = body.get(key)
        if isinstance(value, dict):
            out.append(value)
    return out


def _parse_codex_output(stdout: str) -> Tuple[str, Dict[str, Any]]:
    """Split ``codex exec --json`` JSONL into (text, structured).

    Best-effort by design, exactly like ``claude_cli._parse_cli_json``: a CLI
    build that printed plain text, or one whose event names have moved on
    again, degrades to treating stdout as the answer rather than losing it.
    Keys the CLI did not report are OMITTED, never defaulted to zero — a zero
    cost and an unreported cost are different facts.
    """
    raw = stdout or ""
    events: List[Dict[str, Any]] = []
    for line in raw.splitlines():
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            parsed = json.loads(line)
        except (ValueError, TypeError):
            continue
        if isinstance(parsed, dict):
            events.append(parsed)

    if not events:
        return raw, {}

    messages: List[str] = []
    final_message = ""
    tool_calls = 0
    diffs: List[str] = []
    session_id = ""
    last_kind = ""
    is_error = False
    task_complete = False
    input_tokens: Optional[int] = None
    output_tokens: Optional[int] = None

    for event in events:
        body = _event_body(event)
        kind = str(body.get("type") or event.get("type") or "")
        norm = kind.replace(".", "_").lower()
        # Streaming deltas repeat text that arrives whole in the final event.
        if "delta" in norm:
            continue
        last_kind = kind or last_kind
        blob = f"{norm}|{_item_kind(body)}"

        for key in ("session_id", "conversation_id", "thread_id"):
            value = body.get(key) or event.get(key)
            if isinstance(value, str) and value:
                session_id = value

        if "error" in blob or body.get("is_error") is True:
            is_error = True

        if _is_agent_message(blob):
            text = _event_text(body)
            if text:
                messages.append(text)
        elif norm.startswith(_COMPLETION_PREFIXES):
            task_complete = True
            text = _event_text(body)
            if text:
                final_message = text
        elif not norm.endswith("_end") and any(
            frag in blob for frag in _TOOL_CALL_FRAGMENTS
        ):
            tool_calls += 1

        for key in _DIFF_KEYS:
            value = body.get(key)
            if isinstance(value, str) and value.strip():
                diffs.append(value)

        for usage in _usage_candidates(body):
            if isinstance(usage.get("input_tokens"), int):
                input_tokens = usage["input_tokens"]
            if isinstance(usage.get("output_tokens"), int):
                output_tokens = usage["output_tokens"]

    text = final_message or ("\n\n".join(messages) if messages else raw)

    structured: Dict[str, Any] = {
        "result_subtype": last_kind,
        "is_error": is_error,
        "task_complete": task_complete,
        "events": len(events),
        "turns": len(messages) + (1 if final_message else 0),
        "tool_calls": tool_calls,
    }
    if session_id:
        structured["session_id"] = session_id
    if input_tokens is not None:
        structured["input_tokens"] = input_tokens
    if output_tokens is not None:
        structured["output_tokens"] = output_tokens
    if diffs:
        structured["diff"] = "\n".join(diffs)
    return text, structured


class CodexCliAdapter:
    """AgentAdapter over the OpenAI Codex CLI's non-interactive ``exec`` mode."""

    name = "codex_cli"

    # ── discovery ────────────────────────────────────────────────────────────
    def available(self) -> bool:
        """True only when the CLI is actually resolvable on this host.

        Cheap and local — a filesystem/PATH probe, no subprocess and no network
        call, per the Protocol.
        """
        try:
            return resolve_codex_cli() is not None
        except OSError:  # e.g. an unreadable home directory
            return False

    def resolve(self) -> str:
        found = resolve_codex_cli()
        if not found:
            raise NotInstalledError(
                "codex CLI not found: tried "
                f"{', '.join(_EXECUTABLE_NAMES)} on PATH, ~/.local/bin, and "
                f"${_ENV_EXECUTABLE}"
            )
        return found

    # ── construction ─────────────────────────────────────────────────────────
    def prepare_prompt(self, session: AgentSession) -> str:
        """``codex exec`` takes one prompt, so a system prompt is prepended.

        The CLI has no ``--system-prompt`` equivalent (its persistent
        instructions live in ``AGENTS.md``), and inventing one would silently
        drop the caller's text.
        """
        if not session.system_prompt:
            return session.prompt
        return f"{session.system_prompt}\n\n{session.prompt}"

    def build_argv(self, session: AgentSession) -> List[str]:
        """The command line, ending in ``-`` so the prompt comes from stdin.

        Every optional flag is opt-out-able through metadata: the Codex CLI
        surface has moved across releases and an operator on an older build
        must be able to adapt without a code change.
        """
        meta = session.metadata or {}
        argv = [self.resolve(), "exec", "--json"]

        if meta.get("skip_git_repo_check"):
            argv.append("--skip-git-repo-check")

        sandbox = meta.get("sandbox")
        if sandbox is None:
            sandbox = os.environ.get(_ENV_SANDBOX, _DEFAULT_SANDBOX)
        if sandbox:
            argv += ["--sandbox", str(sandbox)]

        model_id = meta.get("model_id") or os.environ.get(_ENV_MODEL)
        if model_id:
            argv += ["--model", str(model_id)]

        argv += [str(arg) for arg in (meta.get("extra_args") or [])]
        argv.append("-")
        return argv

    def build_env(self, session: AgentSession) -> Dict[str, str]:
        """Inherited environment plus the dispatch tags.

        ``ICDEV_DISPATCH_SOURCE`` is what lets the stop hook attribute this
        session's commits to the scheduler instead of to an interactive user.
        Only set when the caller asked for it, so a review-only session stays
        untagged.
        """
        meta = session.metadata or {}
        env = dict(os.environ)
        source = meta.get("dispatch_source")
        if source:
            env["ICDEV_DISPATCH_SOURCE"] = str(source)
            env["ICDEV_DISPATCH_TASK_ID"] = str(session.task_id)
        for key, value in (meta.get("env") or {}).items():
            env[str(key)] = str(value)
        return env

    def _write_stdin(self, session: AgentSession) -> str:
        """Write the prompt to a temp file and return its path.

        Piped in over stdin rather than passed on argv: a real task prompt is
        far past the Windows 32767-char command-line limit. ``newline=""``
        keeps the bytes exactly as composed on every OS.
        """
        meta = session.metadata or {}
        temp_dir = meta.get("temp_dir")
        if temp_dir:
            dir_path = Path(temp_dir)
            dir_path.mkdir(parents=True, exist_ok=True)
            temp_dir = str(dir_path)
        handle = tempfile.NamedTemporaryFile(
            mode="w", suffix="_codex_instr.txt", delete=False,
            dir=temp_dir or None,
            encoding="utf-8", newline="", errors="replace",
        )
        try:
            handle.write(self.prepare_prompt(session))
        finally:
            handle.close()
        return handle.name

    # ── execution ────────────────────────────────────────────────────────────
    def invoke(self, session: AgentSession) -> AgentResult:
        """Run one Codex session to completion and return the result.

        Raises:
            NotInstalledError: the CLI is not present on this host. Every other
                failure — non-zero exit, timeout, refused model, unknown flag —
                is reported as ``completed=False`` with the CLI's own stderr.
        """
        argv = self.build_argv(session)
        env = self.build_env(session)
        instr_path = self._write_stdin(session)

        t0 = time.time()
        try:
            with open(instr_path, "r", encoding="utf-8",
                      newline="", errors="replace") as stdin_fh:
                proc = subprocess.run(
                    argv,
                    cwd=session.working_dir or None,
                    stdin=stdin_fh,
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    env=env,
                    timeout=session.timeout_seconds,
                    shell=False,
                )
            text, envelope = _parse_codex_output(proc.stdout or "")
            completed = (
                proc.returncode == 0
                and not envelope.get("is_error")
                and (bool(envelope.get("task_complete"))
                     or self.detect_completion(text))
            )
            return AgentResult(
                task_id=session.task_id,
                adapter_name=self.name,
                completed=completed,
                exit_code=proc.returncode,
                output=text,
                error=("" if proc.returncode == 0 else (proc.stderr or "")),
                duration_ms=int((time.time() - t0) * 1000),
                structured={**envelope, "stderr": proc.stderr or ""},
            )
        except subprocess.TimeoutExpired:
            return AgentResult(
                task_id=session.task_id,
                adapter_name=self.name,
                completed=False,
                exit_code=-1,
                output="",
                error=(f"codex CLI timed out after "
                       f"{session.timeout_seconds}s"),
                duration_ms=int((time.time() - t0) * 1000),
            )
        except FileNotFoundError as exc:
            # Resolved a moment ago and gone by exec time — still "not
            # installed" from the caller's point of view.
            raise NotInstalledError(f"codex CLI missing: {exc}") from exc
        finally:
            _unlink_quietly(instr_path)

    # ── protocol tail ────────────────────────────────────────────────────────
    def detect_completion(self, output: str) -> bool:
        """Pure-string heuristic over an agent's output text.

        A JSONL stream carrying a completion event is authoritative; anything
        else falls back to the same marker/length heuristic ``claude_cli``
        uses, so consumers holding only the text behave consistently across
        both shellout adapters.
        """
        if not output:
            return False
        _, envelope = _parse_codex_output(output)
        if envelope.get("task_complete"):
            return not envelope.get("is_error")
        tail = output[-500:]
        return (any(marker in tail for marker in _COMPLETION_MARKERS)
                or len(output.strip()) > 100)

    def parse_response(self, raw: str) -> Dict[str, Any]:
        """Extract content, tool-call count and any diff from a raw response.

        Accepts either the ``--json`` JSONL stream or plain text; plain text
        comes back as ``content`` with the other fields empty.
        """
        text, envelope = _parse_codex_output(raw or "")
        return {
            "content": text,
            "tool_calls": [],
            "tool_call_count": envelope.get("tool_calls", 0),
            "diff": envelope.get("diff", ""),
        }


ADAPTER = CodexCliAdapter()

__all__ = ["CodexCliAdapter", "ADAPTER", "resolve_codex_cli"]
