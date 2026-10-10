# CUI // SP-CTI
"""omx-adapt-01: opencode (sst/opencode, MIT) CLI adapter — the OSS default harness.

opencode is the open-source harness the OMX card names as ICDEV's default on
Omarchy. omx-spike-01 measured it (``docs/research/omx-spike-01/findings.md``)
and every flag and every field this module relies on comes from that spike —
nothing here is taken from opencode's brochure.

What the spike measured, and how this adapter uses it
-----------------------------------------------------
* **Invocation** — ``opencode run --format json [--model <provider/model>]
  --dir <cwd> "<prompt>"``. The prompt is the final positional argument.
* **stdin MUST be closed.** With an inherited open stdin ``opencode run``
  printed nothing and hung for over nine minutes. ``invoke()`` always spawns
  with ``stdin=DEVNULL``. That is also why the prompt goes on argv rather than
  over stdin the way ``codex_cli`` does it: opencode does not read it there.
* **The stream is JSONL**, one object per line, each with ``type``,
  ``timestamp``, ``sessionID`` and ``part``:

      step_start   an LLM step began
      text         assistant text in ``part.text``
      tool_use     ``part.tool`` + ``part.state.{status, input, output|error}``
      step_finish  ``part.reason`` (``tool-calls`` | ``stop``), ``part.tokens``,
                   ``part.cost``
      error        ``error.{name, data.message}`` — fatal

* **Completion** is exit 0 AND the last ``step_finish.reason == "stop"``; there
  is no separate end-of-session event. The final message is the last ``text``
  part; token usage is the SUM of ``step_finish.part.tokens`` over the run.
* **A tool call the guard refused is not a run failure** — it is a ``tool_use``
  whose ``state.status == "error"`` and the run still exits 0. It is counted in
  ``tool_errors`` so a consumer can see it, and it never flips ``completed``.
* **opencode's fatal JSON error is opaque** (``UnknownError`` plus a ``ref``),
  so ``--print-logs --log-level WARN`` is passed by default and stderr is kept:
  that is where the usable reason is.
* **``--pure`` silently removes the guard plugin** (measured: the victim file was
  deleted). ICDEV owns this argv and refuses to emit it — ``build_argv`` raises
  on a caller-supplied ``--pure`` rather than dropping it quietly.

* **The guard is wired (omx-guard-01).** ``invoke()`` installs ICDEV's
  ``tool.execute.before`` plugin into the run's project directory
  (``.opencode/plugin/icdev-guard.ts``, idempotent) and refuses to run if it
  cannot; the plugin calls ``tools.hooks.harness_guard`` ->
  ``tools.airgap.hook_compat.run_pre_tool_check``. ``verify_guard()`` proves it
  live: it runs that bridge on a known-bad call and requires a refusal.

What this adapter deliberately does NOT do
------------------------------------------
* No ``spawn()``, no sandbox flag, no turn/token budget flag: opencode's ``run``
  has none of them, and adding an execution mode with no consumer would be
  inventing a capability the capability matrix is supposed to MEASURE.

LLM-agnostic
------------
No model id appears in this module. The model is ``session.metadata['model_id']``
or ``$ICDEV_OPENCODE_MODEL`` (``.env``), in opencode's own ``provider/model``
form, where the provider is declared in ``opencode.json`` — that is the vLLM /
OpenAI-compatible shape the spike used. With neither set, opencode uses its own
configured default.

OS-agnostic
-----------
``shell=False``, ``encoding='utf-8'`` on every stream, ``pathlib`` throughout,
and the one platform branch (PATHEXT discovery) is shared with ``codex_cli`` and
two-sided. A prompt past the Windows command-line limit cannot be passed on
argv; that raises ``OSError`` at spawn time and is REPORTED as a failed result,
never swallowed.

Session metadata keys this adapter understands (all optional):

    model_id         str  -> ``--model``
    llm_function     str  -> with neither model set and ICDEV_HARNESS_LLM=vllm,
                            the router's vLLM choice (omx-vllm-04)
    auto_approve     bool -> ``--auto`` (auto-approve permissions not denied)
    print_logs       bool -> default True; False drops ``--print-logs``
    extra_args       list -> appended before the prompt (``--pure`` refused)
    dispatch_source  str  -> sets ICDEV_DISPATCH_SOURCE + _TASK_ID
    env              dict -> extra environment variables
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from tools.agents.adapter_base import (
    AgentResult,
    AgentSession,
    NotInstalledError,
)
from tools.agents.adapters.codex_cli import _pathext_candidates
from tools.llm.harness_llm_config import routed_model_argv
from tools.hooks.harness_guard import BASE_DIR as GUARD_ROOT  # repo_root()
from tools.hooks.harness_guard import guard_module, install_guard, render_plugin


_EXECUTABLE_NAME = "opencode"
_ENV_EXECUTABLE = "ICDEV_OPENCODE_CLI"
_ENV_MODEL = "ICDEV_OPENCODE_MODEL"

# Flags that remove ICDEV's guard from the run. Measured in omx-spike-01 for
# ``--pure``; refused rather than stripped, so a caller learns their argv was
# never going to run as written.
_GUARD_DISABLING_FLAGS = ("--pure",)

# The guard self-test's known-bad call (verify_guard). Spelled in two pieces so
# no scanner reading this source mistakes it for a command.
_KNOWN_BAD_COMMAND = "rm -rf " + "/"

_COMPLETION_MARKERS = ("[DONE]", "Task completed", "done.")


def resolve_opencode_cli(is_windows: Optional[bool] = None) -> Optional[str]:
    """Absolute path to the opencode CLI, or None.

    ``$ICDEV_OPENCODE_CLI`` wins and may be an explicit path or a bare name.
    Otherwise ``shutil.which`` (PATHEXT on Windows — npm installs a ``.cmd``
    shim), then ``~/.local/bin``, which is where Omarchy's mise stubs live.
    """
    override = (os.environ.get(_ENV_EXECUTABLE) or "").strip()
    name = override or _EXECUTABLE_NAME

    if override and Path(override).is_file():
        return str(Path(override))

    found = shutil.which(name)
    if found:
        return found

    base = Path.home() / ".local" / "bin" / name
    for candidate in _pathext_candidates(base, is_windows):
        if candidate.is_file():
            return str(candidate)
    return None


def _events(stdout: str) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    for line in (stdout or "").splitlines():
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            parsed = json.loads(line)
        except (ValueError, TypeError):
            continue
        if isinstance(parsed, dict):
            out.append(parsed)
    return out


def _error_message(event: Dict[str, Any]) -> str:
    err = event.get("error")
    if not isinstance(err, dict):
        return str(err or "")
    data = err.get("data") if isinstance(err.get("data"), dict) else {}
    message = str(data.get("message") or "")
    name = str(err.get("name") or "")
    ref = str(data.get("ref") or "")
    text = ": ".join(p for p in (name, message) if p)
    return f"{text} (ref {ref})" if ref else text


def _parse_opencode_output(stdout: str) -> Tuple[str, Dict[str, Any]]:
    """Split ``opencode run --format json`` JSONL into (text, structured).

    Keys opencode did not report are OMITTED, never defaulted to zero: a run
    whose stream carried no ``step_finish`` reported no tokens, which is not
    the same fact as a run that used none. Plain-text stdout degrades to being
    the answer rather than being lost.
    """
    raw = stdout or ""
    events = _events(raw)
    if not events:
        return raw, {}

    texts: List[str] = []
    final_text = ""
    tool_calls: List[Dict[str, Any]] = []
    tool_errors = 0
    session_id = ""
    errors: List[str] = []
    last_reason = ""
    steps = 0
    tokens: Dict[str, int] = {}
    cost: Optional[float] = None

    for event in events:
        kind = str(event.get("type") or "")
        part = event.get("part") if isinstance(event.get("part"), dict) else {}
        sid = event.get("sessionID") or part.get("sessionID")
        if isinstance(sid, str) and sid:
            session_id = sid

        if kind == "text":
            text = part.get("text")
            if isinstance(text, str) and text.strip():
                texts.append(text)
                final_text = text
        elif kind == "tool_use":
            state = part.get("state") if isinstance(part.get("state"), dict) else {}
            status = str(state.get("status") or "")
            if status == "error":
                tool_errors += 1
            tool_calls.append({
                "name": str(part.get("tool") or ""),
                "call_id": str(part.get("callID") or ""),
                "status": status,
                "input": state.get("input") if isinstance(state.get("input"), dict) else {},
            })
        elif kind == "step_finish":
            steps += 1
            last_reason = str(part.get("reason") or "")
            used = part.get("tokens")
            if isinstance(used, dict):
                for key in ("total", "input", "output", "reasoning"):
                    value = used.get(key)
                    if isinstance(value, int) and not isinstance(value, bool):
                        tokens[key] = tokens.get(key, 0) + value
                cache = used.get("cache")
                if isinstance(cache, dict):
                    for key in ("read", "write"):
                        value = cache.get(key)
                        if isinstance(value, int) and not isinstance(value, bool):
                            name = f"cache_{key}"
                            tokens[name] = tokens.get(name, 0) + value
            if isinstance(part.get("cost"), (int, float)) and not isinstance(
                part.get("cost"), bool
            ):
                cost = (cost or 0.0) + float(part["cost"])
        elif kind == "error":
            errors.append(_error_message(event) or "opencode reported an error")

    structured: Dict[str, Any] = {
        "is_error": bool(errors),
        "task_complete": last_reason == "stop" and not errors,
        "events": len(events),
        "steps": steps,
        "turns": len(texts),
        "tool_calls": len(tool_calls),
        "tool_errors": tool_errors,
        "tool_call_list": tool_calls,
    }
    if last_reason:
        structured["stop_reason"] = last_reason
    if session_id:
        structured["session_id"] = session_id
    if errors:
        structured["error"] = "; ".join(errors)
    if "input" in tokens:
        structured["input_tokens"] = tokens["input"]
    if "output" in tokens:
        structured["output_tokens"] = tokens["output"]
    if tokens:
        structured["tokens"] = tokens
    if cost is not None:
        structured["total_cost_usd"] = cost

    return final_text, structured


class OpencodeCliAdapter:
    """AgentAdapter over ``opencode run --format json``."""

    name = "opencode_cli"

    # ── discovery ────────────────────────────────────────────────────────────
    def available(self) -> bool:
        """True only when the CLI resolves on this host — no subprocess."""
        try:
            return resolve_opencode_cli() is not None
        except OSError:
            return False

    def resolve(self) -> str:
        found = resolve_opencode_cli()
        if not found:
            raise NotInstalledError(
                f"opencode CLI not found: tried {_EXECUTABLE_NAME} on PATH, "
                f"~/.local/bin, and ${_ENV_EXECUTABLE}"
            )
        return found

    # ── construction ─────────────────────────────────────────────────────────
    def prepare_prompt(self, session: AgentSession) -> str:
        """``opencode run`` takes one message, so a system prompt is prepended.

        Its persistent instructions live in ``AGENTS.md`` (measured: loaded);
        there is no per-run system-prompt flag to map onto.
        """
        if not session.system_prompt:
            return session.prompt
        return f"{session.system_prompt}\n\n{session.prompt}"

    def build_argv(self, session: AgentSession) -> List[str]:
        """The command line, ending in the prompt.

        Raises:
            ValueError: ``extra_args`` carries a flag that disables the guard.
        """
        meta = session.metadata or {}
        extra = [str(arg) for arg in (meta.get("extra_args") or [])]
        refused = [a for a in extra if a in _GUARD_DISABLING_FLAGS]
        if refused:
            raise ValueError(
                f"opencode_cli refuses {refused}: it removes ICDEV's guard "
                "plugin from the run (measured in omx-spike-01)"
            )

        argv = [self.resolve(), "run", "--format", "json"]
        model_id = meta.get("model_id") or os.environ.get(_ENV_MODEL)
        if model_id:
            argv += ["--model", str(model_id)]
        else:
            # omx-vllm-04: the router's vLLM choice for the task's llm_function.
            argv += routed_model_argv("opencode", meta)
        if session.working_dir:
            argv += ["--dir", str(session.working_dir)]
        if meta.get("auto_approve"):
            argv.append("--auto")
        if meta.get("print_logs", True):
            argv += ["--print-logs", "--log-level", "WARN"]
        argv += extra
        argv.append(self.prepare_prompt(session))
        return argv

    def build_env(self, session: AgentSession) -> Dict[str, str]:
        """Inherited environment plus the dispatch tags (same as codex_cli)."""
        meta = session.metadata or {}
        env = dict(os.environ)
        source = meta.get("dispatch_source")
        if source:
            env["ICDEV_DISPATCH_SOURCE"] = str(source)
            env["ICDEV_DISPATCH_TASK_ID"] = str(session.task_id)
        for key, value in (meta.get("env") or {}).items():
            env[str(key)] = str(value)
        return env

    # ── execution ────────────────────────────────────────────────────────────
    def invoke(self, session: AgentSession) -> AgentResult:
        """Run one opencode session to completion and return the result.

        Raises:
            NotInstalledError: the CLI is not present on this host. Every other
                failure — non-zero exit, a fatal stream ``error``, a timeout, an
                argv the OS refused — is ``completed=False`` with a reason.
        """
        argv = self.build_argv(session)
        env = self.build_env(session)

        t0 = time.time()
        try:
            install_guard("opencode", project=Path(session.working_dir or Path.cwd()))
        except Exception as exc:  # noqa: BLE001 -- refuse, never run unguarded
            return AgentResult(
                task_id=session.task_id, adapter_name=self.name, completed=False,
                exit_code=-1, output="",
                error=f"opencode_cli refuses to run unguarded: ICDEV guard plugin "
                      f"could not be installed: {type(exc).__name__}: {exc}",
                duration_ms=int((time.time() - t0) * 1000), structured={},
            )

        def _failed(exit_code: int, error: str, **structured: Any) -> AgentResult:
            return AgentResult(
                task_id=session.task_id,
                adapter_name=self.name,
                completed=False,
                exit_code=exit_code,
                output="",
                error=error,
                duration_ms=int((time.time() - t0) * 1000),
                structured=structured,
            )

        try:
            proc = subprocess.run(
                argv,
                cwd=session.working_dir or None,
                stdin=subprocess.DEVNULL,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                env=env,
                timeout=session.timeout_seconds,
                shell=False,
            )
        except subprocess.TimeoutExpired:
            return _failed(
                -1, f"opencode CLI timed out after {session.timeout_seconds}s"
            )
        except FileNotFoundError as exc:
            raise NotInstalledError(f"opencode CLI missing: {exc}") from exc
        except OSError as exc:
            # e.g. WinError 206 — the prompt is past the command-line limit.
            return _failed(-1, f"opencode CLI could not be started: {exc}")

        text, envelope = _parse_opencode_output(proc.stdout or "")
        completed = (
            proc.returncode == 0
            and not envelope.get("is_error")
            and bool(envelope.get("task_complete"))
        )
        stderr = proc.stderr or ""
        error = ""
        if not completed:
            error = envelope.get("error") or ""
            if stderr.strip():
                error = f"{error}\n{stderr}".strip()
            if not error:
                error = (f"opencode exited {proc.returncode} without a final "
                         "step_finish reason 'stop'")
        return AgentResult(
            task_id=session.task_id,
            adapter_name=self.name,
            completed=completed,
            exit_code=proc.returncode,
            output=text,
            error=error,
            duration_ms=int((time.time() - t0) * 1000),
            structured={**envelope, "stderr": stderr},
        )

    # ── guard self-report (consumed by capability_matrix: guard_wired) ───────
    def verify_guard(self) -> Dict[str, Any]:
        """Whether ICDEV's guard sees this adapter's tool calls -- measured LIVE.

        Runs the exact process the installed plugin spawns
        (``python -m tools.hooks.harness_guard --harness opencode``) on a
        known-bad call in opencode's own spelling and requires a refusal, and
        checks the packaged plugin source renders. ``wired`` is never inferred
        from a file existing (omx-spike-01: ``--pure`` drops a plugin that is
        right there on disk; ``build_argv`` refuses it).
        """
        try:
            render_plugin("opencode")
        except Exception as exc:  # noqa: BLE001
            return {"wired": False,
                    "reason": f"guard plugin source unavailable: {exc}"}
        request = {"tool": "bash", "args": {"command": _KNOWN_BAD_COMMAND}}
        try:
            proc = subprocess.run(
                [sys.executable, "-m", guard_module(), "--harness", "opencode"],
                input=json.dumps(request), capture_output=True, text=True,
                encoding="utf-8", errors="replace", cwd=str(GUARD_ROOT),
                timeout=120, shell=False,
            )
            verdict = json.loads((proc.stdout or "").strip().splitlines()[-1])
        except Exception as exc:  # noqa: BLE001
            return {"wired": False,
                    "reason": f"guard bridge did not answer: {type(exc).__name__}: {exc}"}
        if verdict.get("allowed") is False:
            return {"wired": True,
                    "reason": f"bridge refused a known-bad bash call: {verdict.get('reason')}"}
        return {"wired": False,
                "reason": f"bridge ALLOWED a known-bad bash call: {verdict.get('reason')}"}

    # ── protocol tail ────────────────────────────────────────────────────────
    def detect_completion(self, output: str) -> bool:
        """A JSONL stream is authoritative; plain text falls back to markers."""
        if not output:
            return False
        events = _events(output)
        if events:
            _, envelope = _parse_opencode_output(output)
            return bool(envelope.get("task_complete"))
        tail = output[-500:]
        return any(marker in tail for marker in _COMPLETION_MARKERS)

    def parse_response(self, raw: str) -> Dict[str, Any]:
        """Content, tool calls, usage and error from a raw ``--format json`` run."""
        text, envelope = _parse_opencode_output(raw or "")
        out: Dict[str, Any] = {
            "content": text,
            "tool_calls": envelope.get("tool_call_list", []),
            "tool_call_count": envelope.get("tool_calls", 0),
            "diff": "",
        }
        for key in ("input_tokens", "output_tokens", "tokens", "total_cost_usd",
                    "session_id", "stop_reason", "error"):
            if key in envelope:
                out[key] = envelope[key]
        if envelope:
            out["is_error"] = envelope.get("is_error", False)
            out["task_complete"] = envelope.get("task_complete", False)
        return out


ADAPTER = OpencodeCliAdapter()

__all__ = ["OpencodeCliAdapter", "ADAPTER", "resolve_opencode_cli"]
