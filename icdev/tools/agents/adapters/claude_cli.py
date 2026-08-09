# CUI // SP-CTI
"""OPT-71 / hgx-exec-03: Claude Code CLI adapter — THE claude-CLI shellout.

Until hgx-exec-03 this module held a SECOND, thinner implementation of the same
``claude --dangerously-skip-permissions --max-turns`` shellout that
``tools/genesis/reflexes/kanban.py`` had been running in production for months.
Two implementations, and only the kanban one carried the hardening that turned
out to matter:

* **env tagging** — ``ICDEV_DISPATCH_SOURCE`` / ``ICDEV_DISPATCH_TASK_ID`` so
  the stop hook attributes the session's commits to the scheduler rather than
  to an interactive user;
* **the instruction goes in over stdin from a temp file** — a real task prompt
  on argv trips the Windows 32767-char command-line limit (WinError 206), and
  the CLI auto-detects a non-TTY stdout and enters non-interactive mode anyway;
* **the operator's model override** passed through as ``--model``;
* **PATHEXT-aware discovery** — ``~/.local/bin/claude`` with no suffix never
  exists on Windows (the installed binary is ``claude.EXE``), so resolution
  depended entirely on PATH and a service started with a thinner environment
  found nothing. 25 tasks were quarantined as "no executor available" on
  2026-08-01 before that was traced.

The hardened version now lives here and kanban calls it, so exactly one place in
the tree knows how to invoke the CLI. Two execution modes share that single
construction (``_build_argv`` / ``_build_env`` / ``_write_stdin``):

``spawn(session)``
    Returns a live ``subprocess.Popen``. Non-blocking — this is what the kanban
    runner needs so its poll/kill/timeout loop keeps working unchanged.

``invoke(session)``
    Blocking; returns an ``AgentResult``. The AgentAdapter protocol method,
    used by callers that just want the output (the adversarial verifier,
    pr_watcher, standalone orchestrators).

Session metadata keys this adapter understands (all optional):

    model_id                 str  -> appended as ``--model``
    dispatch_source          str  -> sets ICDEV_DISPATCH_SOURCE + _TASK_ID
    env                      dict -> extra environment variables
    temp_dir                 str  -> where the stdin temp file is written
    cleanup_delay_seconds    num  -> delay before the temp file is unlinked
"""
from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
import threading
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from tools.agents.adapter_base import (
    AgentResult,
    AgentSession,
    NotInstalledError,
)


_COMPLETION_MARKERS = (
    "[DONE]",
    "Task completed",
    "done.",
)

# The subprocess has read its stdin long before this elapses; the delay only
# exists so a slow start does not race the unlink.
_DEFAULT_CLEANUP_DELAY = 300.0


def resolve_claude_cli() -> Optional[str]:
    """Absolute path to the ``claude`` CLI, or None.

    ``shutil.which`` is the primary probe: on Windows it resolves
    ``.exe``/``.cmd``/``.bat`` through PATHEXT, which is what an npm-style shim
    install needs. ``~/.local/bin/claude`` is a POSIX-flavoured SECONDARY probe
    for hosts where the binary is installed but not on PATH — and it is tried
    with each PATHEXT suffix too, because the bare name never exists on Windows.
    """
    found = shutil.which("claude")
    if found:
        return found

    base = Path.home() / ".local" / "bin" / "claude"
    suffixes: List[str] = [""]
    if os.name == "nt":
        suffixes += [
            e.lower()
            for e in os.environ.get(
                "PATHEXT", ".EXE;.CMD;.BAT;.COM"
            ).split(os.pathsep)
            if e.strip()
        ]
    for suffix in suffixes:
        candidate = base.with_name(base.name + suffix) if suffix else base
        if candidate.exists():
            return str(candidate)
    return None


def _unlink_quietly(path: str) -> None:
    try:
        os.unlink(path)
    except OSError:
        pass


def _schedule_cleanup(path: str, delay: float) -> None:
    """Unlink *path* after *delay* seconds on a daemon thread.

    Threads rather than asyncio (D36), and daemon so a scheduler shutdown is
    never held open by a pending unlink.
    """
    def _cleanup() -> None:
        time.sleep(delay)
        _unlink_quietly(path)

    threading.Thread(target=_cleanup, daemon=True,
                     name="claude-cli-instr-cleanup").start()


class ClaudeCliAdapter:
    name = "claude_cli"

    # ── discovery ────────────────────────────────────────────────────────────
    def available(self) -> bool:
        return resolve_claude_cli() is not None

    def resolve(self) -> str:
        found = resolve_claude_cli()
        if not found:
            raise NotInstalledError(
                "claude CLI not found on PATH nor at ~/.local/bin/claude"
            )
        return found

    # ── one construction, shared by spawn() and invoke() ─────────────────────
    def prepare_prompt(self, session: AgentSession) -> str:
        if not session.system_prompt:
            return session.prompt
        return f"{session.system_prompt}\n\n{session.prompt}"

    def build_argv(self, session: AgentSession) -> List[str]:
        """The command line. ``--model`` only when the caller supplied one.

        The caller owns the decision of WHICH model — a model the CLI cannot
        serve must never reach here (kanban drops claude_cli from the executor
        chain in that case rather than quietly ignoring the operator's choice).
        """
        argv = [
            self.resolve(),
            "--dangerously-skip-permissions",
            "--max-turns",
            str(session.max_turns),
            "--output-format",
            "text",
        ]
        model_id = (session.metadata or {}).get("model_id")
        if model_id:
            argv += ["--model", str(model_id)]
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
            mode="w", suffix="_instr.txt", delete=False,
            dir=temp_dir or None,
            encoding="utf-8", newline="", errors="replace",
        )
        try:
            handle.write(self.prepare_prompt(session))
        finally:
            handle.close()
        return handle.name

    # ── execution mode 1: non-blocking (the kanban runner) ───────────────────
    def spawn(self, session: AgentSession, stdout: Any = None,
              stderr: Any = None) -> subprocess.Popen:
        """Launch the CLI and return the live handle.

        The caller owns the returned process: polling it, killing it on
        timeout, and reaping it. ``stdout``/``stderr`` are passed straight
        through, so a caller can hand over an open log file.
        """
        argv = self.build_argv(session)
        env = self.build_env(session)
        instr_path = self._write_stdin(session)

        stdin_fh = open(instr_path, "r", encoding="utf-8",
                        newline="", errors="replace")
        try:
            proc = subprocess.Popen(
                argv,
                cwd=session.working_dir or None,
                stdin=stdin_fh,
                stdout=stdout,
                stderr=stderr,
                env=env,
                shell=False,
            )
        except BaseException:
            stdin_fh.close()
            _unlink_quietly(instr_path)
            raise
        finally:
            # The child inherited the fd; this handle is ours to drop.
            if not stdin_fh.closed:
                stdin_fh.close()

        delay = float((session.metadata or {}).get(
            "cleanup_delay_seconds", _DEFAULT_CLEANUP_DELAY))
        _schedule_cleanup(instr_path, delay)
        return proc

    # ── execution mode 2: blocking (the AgentAdapter protocol) ───────────────
    def invoke(self, session: AgentSession) -> AgentResult:
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
            stdout = proc.stdout or ""
            return AgentResult(
                task_id=session.task_id,
                adapter_name=self.name,
                completed=(proc.returncode == 0
                           and self.detect_completion(stdout)),
                exit_code=proc.returncode,
                output=stdout,
                error=("" if proc.returncode == 0 else (proc.stderr or "")),
                duration_ms=int((time.time() - t0) * 1000),
                structured={"stderr": proc.stderr or ""},
            )
        except subprocess.TimeoutExpired:
            return AgentResult(
                task_id=session.task_id,
                adapter_name=self.name,
                completed=False,
                exit_code=-1,
                output="",
                error=(f"claude CLI timed out after "
                       f"{session.timeout_seconds}s"),
                duration_ms=int((time.time() - t0) * 1000),
            )
        except FileNotFoundError as exc:
            raise NotInstalledError(f"claude CLI missing: {exc}") from exc
        finally:
            _unlink_quietly(instr_path)

    # ── protocol tail ────────────────────────────────────────────────────────
    def detect_completion(self, output: str) -> bool:
        if not output:
            return False
        tail = output[-500:]
        return any(marker in tail for marker in _COMPLETION_MARKERS) or len(
            output.strip()
        ) > 100

    def parse_response(self, raw: str) -> Dict[str, Any]:
        return {
            "content": raw or "",
            "tool_calls": [],
            "diff": "",
        }


ADAPTER = ClaudeCliAdapter()
