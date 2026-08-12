# CUI // SP-CTI
"""exa-bench-02: Block Goose CLI adapter — the third real harness.

The EXA card's thesis is that ICDEV already HAS the many-harnesses-behind-one-
seam abstraction (``tools/agents/adapter_base.py``) and what it lacks is
harnesses. Goose is the one both omnigent and buzz integrate, and until now no
adapter for it existed anywhere in the tree.

This adapter was written against a Goose that is actually installed
(``goose 1.28.0``), so the parts a stub has to guess at are measured rather than
assumed. In particular the ``--output-format json`` envelope below is the shape
the CLI really emits, captured from a live ``goose run``:

.. code-block:: json

    {"messages": [{"id": null, "role": "user", "created": 1786535179,
                   "content": [{"type": "text", "text": "..."}],
                   "metadata": {"userVisible": true, "agentVisible": true}}],
     "metadata": {"total_tokens": 6, "status": "completed"}}

What Goose gives that the other shellouts do not
------------------------------------------------

* **a structured envelope without a flag gamble** — ``--output-format json`` is
  a stable, documented option, so ``turns``, ``total_tokens`` and a completion
  ``status`` are read rather than inferred from prose;
* **``--max-turns``** maps straight onto ``AgentSession.max_turns``, which
  ``codex_cli`` has no equivalent for;
* **``--no-session``**, the vendor's own "useful for automated runs" switch, so
  a dispatched task leaves no session file behind.

Three details are load-bearing and were confirmed by running the CLI:

1. **The banner is on stdout.** Goose prints its ASCII banner and a
   ``goose is ready`` line to stdout BEFORE the JSON, so a parser that assumes
   stdout starts with ``{`` gets nothing. ``--quiet`` suppresses it and is the
   default here, but the parser scans for the first balanced JSON object anyway
   — an operator who turns ``--quiet`` off must not lose the envelope.
2. **A misconfigured Goose panics rather than exiting cleanly.** With no model
   configured it aborts with a Rust panic on stderr, exit 101, and NOTHING on
   stdout. That path is a reported ``completed=False`` here, never a raise.
3. **``tool_calls`` can honestly be zero while tools ran.** With a provider that
   executes tools inside its own loop (``--provider claude-code``), Goose never
   sees a tool request, so none appear in ``messages``. The count is what the
   envelope reports, and the exa-bench-03 probe should read it as "tool calls
   Goose mediated", not "tool calls that happened".

Where this adapter deliberately differs from its siblings
---------------------------------------------------------

**The system prompt is prepended, not passed as ``--system``.** Goose does have
a real ``--system`` slot, which is more than ``codex_cli`` or ``claude_cli``
offer. It is not used because it puts the text on argv, and an ICDEV system
prompt plus a task prompt is exactly what blows the Windows 32767-char limit
this family of adapters keeps tripping over. Both go over stdin instead; an
operator who wants the native slot can pass it through ``extra_args``.

**No ``spawn()``.** ``claude_cli.spawn()`` exists because the kanban runner owns
its own poll/kill loop. Nothing dispatches Goose that way; a second execution
mode with no consumer would be inventing a capability the probe is meant to
MEASURE.

**Unreported metrics are absent, not zero.** Goose reports ``total_tokens`` but
not cost, and not an input/output split. Those keys are omitted so a
cross-adapter comparison can tell "not reported" from "free".

LLM-agnostic
------------
No model id and no provider name appear in this module. Both are the operator's
choice — ``session.metadata['model_id']`` / ``$ICDEV_GOOSE_MODEL`` and
``session.metadata['provider']`` / ``$ICDEV_GOOSE_PROVIDER`` — passed through as
``--model`` / ``--provider``; with neither set the CLI uses its own configured
``GOOSE_MODEL`` / ``GOOSE_PROVIDER``. ``invoke()`` never raises for a backend
failure: an unknown flag, an unconfigured model, a non-zero exit, a panic and a
timeout all come back as an ``AgentResult`` with ``completed=False`` and the
CLI's own stderr in ``error``. Only a genuinely absent CLI raises, which is the
Protocol's contract.

OS-agnostic
-----------
``pathlib`` throughout, ``encoding='utf-8'`` and ``newline=''`` on every file
handle, ``shell=False``, and the one platform branch is two-sided and
parameterised.

Session metadata keys this adapter understands (all optional):

    model_id              str      -> ``--model``
    provider              str      -> ``--provider``
    no_session            bool     -> ``--no-session``            (default on)
    quiet                 bool     -> ``--quiet``                 (default on)
    no_profile            bool     -> ``--no-profile``           (default off)
    with_builtin          str|list -> ``--with-builtin`` per entry
    max_tool_repetitions  int      -> ``--max-tool-repetitions``
    extra_args            list     -> appended before the stdin marker
    dispatch_source       str      -> sets ICDEV_DISPATCH_SOURCE + _TASK_ID
    env                   dict     -> extra environment variables
    temp_dir              str      -> where the stdin temp file is written
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

from tools.agents.adapter_base import (
    AgentResult,
    AgentSession,
    NotInstalledError,
)


_EXECUTABLE_NAMES = ("goose",)
_ENV_EXECUTABLE = "ICDEV_GOOSE_CLI"
_ENV_MODEL = "ICDEV_GOOSE_MODEL"
_ENV_PROVIDER = "ICDEV_GOOSE_PROVIDER"

# The one status string Goose emits for a clean run. Matched exactly rather
# than by substring, and every OTHER status is reported verbatim instead of
# being forced into a closed vocabulary this adapter would have invented.
_STATUS_COMPLETE = "completed"
_ERROR_STATUS_FRAGMENTS = ("error", "fail", "abort")

_COMPLETION_MARKERS = (
    "[DONE]",
    "Task completed",
    "done.",
)


def _resolve_platform(is_windows: Optional[bool]) -> bool:
    """The platform being described — this host unless the caller said otherwise.

    An explicit parameter rather than an ``os.name`` read at each call site so
    BOTH branches are reachable from either OS's test run. Forcing the branch by
    patching ``os.name`` instead makes ``pathlib`` hand out a ``WindowsPath`` on
    Linux, which raises on construction.
    """
    return (os.name == "nt") if is_windows is None else is_windows


def _pathext_candidates(
    base: Path, is_windows: Optional[bool] = None
) -> List[Path]:
    """``base`` itself, plus each PATHEXT suffix when targeting Windows.

    PATHEXT is a Windows-only variable and is always semicolon-delimited, so the
    split is on ``;`` rather than ``os.pathsep`` — the host running the check is
    not necessarily the platform being described.
    """
    candidates = [base]
    if _resolve_platform(is_windows):
        for ext in os.environ.get("PATHEXT", ".EXE;.CMD;.BAT;.COM").split(";"):
            ext = ext.strip()
            if ext:
                candidates.append(base.with_name(base.name + ext.lower()))
    return candidates


def resolve_goose_cli(is_windows: Optional[bool] = None) -> Optional[str]:
    """Absolute path to the Goose CLI, or None.

    ``$ICDEV_GOOSE_CLI`` wins and may be either an explicit path or a bare name.
    Otherwise the name is tried through ``shutil.which`` (PATHEXT on Windows),
    then as a ``~/.local/bin`` secondary probe — which is where Goose's own
    installer puts it, and where the suffix-less name never exists on Windows.
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


def _as_list(value: Any) -> List[str]:
    """Normalise a metadata knob that accepts one entry, many, or a CSV string."""
    if value is None or value == "":
        return []
    if isinstance(value, str):
        return [part.strip() for part in value.split(",") if part.strip()]
    if isinstance(value, Iterable):
        return [str(part).strip() for part in value if str(part).strip()]
    return [str(value)]


def _unlink_quietly(path: str) -> None:
    try:
        os.unlink(path)
    except OSError:
        pass


def _extract_json_object(raw: str) -> str:
    """The first balanced top-level JSON object in *raw*, or "".

    Goose prints its banner to stdout ahead of the envelope, so the object
    cannot be assumed to start at byte zero. Brace counting is string-aware:
    a ``}`` inside a message body is not the end of the document, and the
    assistant's text routinely contains braces.
    """
    start = raw.find("{")
    if start < 0:
        return ""
    depth = 0
    in_string = False
    escaped = False
    for index in range(start, len(raw)):
        char = raw[index]
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            continue
        if char == '"':
            in_string = True
        elif char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return raw[start:index + 1]
    return ""


def _parse_goose_output(stdout: str) -> Tuple[str, Dict[str, Any]]:
    """Split ``goose run --output-format json`` into (text, structured).

    Best-effort by design, exactly like ``claude_cli._parse_cli_json``: a run
    that printed plain text, panicked before emitting JSON, or whose schema has
    moved on degrades to treating stdout as the answer rather than losing it.
    Keys the CLI did not report are OMITTED, never defaulted to zero.
    """
    raw = stdout or ""
    blob = _extract_json_object(raw)
    if not blob:
        return raw, {}
    try:
        payload = json.loads(blob)
    except (ValueError, TypeError):
        return raw, {}
    if not isinstance(payload, dict) or not isinstance(
        payload.get("messages"), list
    ):
        return raw, {}

    messages: List[Any] = payload["messages"]
    assistant_texts: List[str] = []
    tool_calls = 0

    for message in messages:
        if not isinstance(message, dict):
            continue
        role = str(message.get("role") or "").lower()
        content = message.get("content")
        if not isinstance(content, list):
            continue
        for block in content:
            if not isinstance(block, dict):
                continue
            kind = str(block.get("type") or "").replace("_", "").lower()
            if "toolrequest" in kind:
                tool_calls += 1
            elif kind == "text" and role == "assistant":
                text = block.get("text")
                if isinstance(text, str) and text.strip():
                    assistant_texts.append(text)

    meta = payload.get("metadata")
    meta = meta if isinstance(meta, dict) else {}
    status = str(meta.get("status") or "")
    lowered = status.lower()

    structured: Dict[str, Any] = {
        "status": status,
        "task_complete": status == _STATUS_COMPLETE,
        "is_error": any(frag in lowered for frag in _ERROR_STATUS_FRAGMENTS),
        "messages": len(messages),
        "turns": len(assistant_texts),
        "tool_calls": tool_calls,
    }
    total_tokens = meta.get("total_tokens")
    if isinstance(total_tokens, int):
        structured["total_tokens"] = total_tokens
    session_id = payload.get("session_id") or meta.get("session_id")
    if isinstance(session_id, str) and session_id:
        structured["session_id"] = session_id
    if assistant_texts:
        structured["final_message"] = assistant_texts[-1]

    # The whole assistant transcript is the output: unlike claude_cli's
    # envelope there is no separate ``result`` field, and the last message
    # alone drops the work that led to it. ``final_message`` is carried in
    # ``structured`` for callers that only want the answer.
    text = "\n\n".join(assistant_texts) if assistant_texts else raw
    return text, structured


class GooseCliAdapter:
    """AgentAdapter over the Block Goose CLI's non-interactive ``run`` mode."""

    name = "goose_cli"

    # ── discovery ────────────────────────────────────────────────────────────
    def available(self) -> bool:
        """True only when the CLI is actually resolvable on this host.

        Cheap and local — a filesystem/PATH probe, no subprocess and no network
        call, per the Protocol.
        """
        try:
            return resolve_goose_cli() is not None
        except OSError:  # e.g. an unreadable home directory
            return False

    def resolve(self) -> str:
        found = resolve_goose_cli()
        if not found:
            raise NotInstalledError(
                "goose CLI not found: tried "
                f"{', '.join(_EXECUTABLE_NAMES)} on PATH, ~/.local/bin, and "
                f"${_ENV_EXECUTABLE}"
            )
        return found

    # ── construction ─────────────────────────────────────────────────────────
    def prepare_prompt(self, session: AgentSession) -> str:
        """System prompt prepended to the instructions that go over stdin.

        Goose's native ``--system`` slot is deliberately unused: it puts the
        text on argv, and system-plus-task is exactly the size that trips the
        Windows 32767-char command-line limit.
        """
        if not session.system_prompt:
            return session.prompt
        return f"{session.system_prompt}\n\n{session.prompt}"

    def build_argv(self, session: AgentSession) -> List[str]:
        """The command line, ending in ``-i -`` so instructions come from stdin.

        Every optional flag is opt-out-able through metadata: Goose ships fast
        and an operator on an older build must be able to adapt without a code
        change.
        """
        meta = session.metadata or {}
        argv = [self.resolve(), "run"]

        if meta.get("no_session", True):
            argv.append("--no-session")
        if meta.get("quiet", True):
            argv.append("--quiet")
        if meta.get("no_profile"):
            argv.append("--no-profile")
        for builtin in _as_list(meta.get("with_builtin")):
            argv += ["--with-builtin", builtin]

        argv += ["--output-format", "json"]

        if session.max_turns:
            argv += ["--max-turns", str(session.max_turns)]
        repetitions = meta.get("max_tool_repetitions")
        if repetitions:
            argv += ["--max-tool-repetitions", str(repetitions)]

        provider = meta.get("provider") or os.environ.get(_ENV_PROVIDER)
        if provider:
            argv += ["--provider", str(provider)]
        model_id = meta.get("model_id") or os.environ.get(_ENV_MODEL)
        if model_id:
            argv += ["--model", str(model_id)]

        argv += [str(arg) for arg in (meta.get("extra_args") or [])]
        argv += ["-i", "-"]
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
        """Write the instructions to a temp file and return its path.

        Piped in over stdin rather than passed with ``-t``: a real task prompt
        is far past the Windows 32767-char command-line limit. ``newline=""``
        keeps the bytes exactly as composed on every OS.
        """
        meta = session.metadata or {}
        temp_dir = meta.get("temp_dir")
        if temp_dir:
            dir_path = Path(temp_dir)
            dir_path.mkdir(parents=True, exist_ok=True)
            temp_dir = str(dir_path)
        handle = tempfile.NamedTemporaryFile(
            mode="w", suffix="_goose_instr.txt", delete=False,
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
        """Run one Goose session to completion and return the result.

        Raises:
            NotInstalledError: the CLI is not present on this host. Every other
                failure — non-zero exit, the exit-101 panic an unconfigured
                model produces, a timeout — is reported as ``completed=False``
                with the CLI's own stderr.
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
            text, envelope = _parse_goose_output(proc.stdout or "")
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
                error=(f"goose CLI timed out after "
                       f"{session.timeout_seconds}s"),
                duration_ms=int((time.time() - t0) * 1000),
            )
        except FileNotFoundError as exc:
            # Resolved a moment ago and gone by exec time — still "not
            # installed" from the caller's point of view.
            raise NotInstalledError(f"goose CLI missing: {exc}") from exc
        finally:
            _unlink_quietly(instr_path)

    # ── protocol tail ────────────────────────────────────────────────────────
    def detect_completion(self, output: str) -> bool:
        """Pure-string heuristic over an agent's output text.

        A JSON envelope carrying a ``status`` is authoritative; anything else
        falls back to the same marker/length heuristic ``claude_cli`` uses, so
        consumers holding only the text behave consistently across the shellout
        adapters.
        """
        if not output:
            return False
        _, envelope = _parse_goose_output(output)
        if envelope.get("status"):
            return bool(envelope.get("task_complete")) and not envelope.get(
                "is_error"
            )
        tail = output[-500:]
        return (any(marker in tail for marker in _COMPLETION_MARKERS)
                or len(output.strip()) > 100)

    def parse_response(self, raw: str) -> Dict[str, Any]:
        """Extract content and the mediated tool-call count from a raw response.

        Accepts either the ``--output-format json`` envelope or plain text;
        plain text comes back as ``content`` with the other fields empty. The
        envelope carries no diff, so ``diff`` is empty by fact.
        """
        text, envelope = _parse_goose_output(raw or "")
        return {
            "content": text,
            "tool_calls": [],
            "tool_call_count": envelope.get("tool_calls", 0),
            "diff": "",
        }


ADAPTER = GooseCliAdapter()

__all__ = ["GooseCliAdapter", "ADAPTER", "resolve_goose_cli"]
