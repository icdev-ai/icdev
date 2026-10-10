# CUI // SP-CTI
"""omx-adapt-02: Pi coding agent (``pi``) CLI adapter — a fully supported OSS peer.

Pi (``@earendil-works/pi-coding-agent``) ships with Omarchy and is a peer of
``opencode_cli``, not a fallback. omx-spike-01 measured it
(``docs/research/omx-spike-01/findings.md``, section "Pi") and every flag and
every field this module relies on comes from that spike.

What the spike measured, and how this adapter uses it
-----------------------------------------------------
* **Invocation** — ``pi --mode json [--offline] [--model <provider>/<id>]
  [-e <extension>]... "<prompt>"`` with stdin closed. ``--mode json`` is
  non-interactive and exits when the prompt settles. The prompt is the final
  positional argument.
* **The stream is strict JSONL.** Pi's docs warn that U+2028/U+2029 are NOT
  record boundaries, so records are split on ``\\n`` only — never
  ``str.splitlines()``, which would cut a record carrying either character.
  Records used here:

      session                header: ``id``, ``cwd``
      message_end            ``message.{role, content[], stopReason, usage,
                             errorMessage}``; ``stopReason`` is
                             ``stop`` | ``toolUse`` | ``error``
      tool_execution_start   ``toolCallId``, ``toolName``, ``args``
      tool_execution_end     ``toolCallId``, ``toolName``, ``isError``
      agent_settled          ``aborted`` — the completion record

* **Pi exits 0 when the provider rejects the model** (measured: a provider 404
  is ``stopReason: "error"`` on the last assistant message and exit 0). So
  completion is exit 0 AND ``agent_settled`` with ``aborted: false`` AND the
  last assistant ``stopReason == "stop"``. A ``returncode == 0`` check alone
  would report a provider outage as success.
* **An unknown provider is CLI-side**: exit 1, plain stderr, no JSON. stderr
  is kept in ``error``.
* **A tool call the guard refused is not a run failure** — it is a
  ``tool_execution_end`` with ``isError: true`` and the run still settles. It
  is counted in ``tool_errors`` and never flips ``completed``.
* **Token usage** is per assistant ``message_end.message.usage``
  (``input, output, cacheRead, cacheWrite, reasoning, totalTokens,
  cost.total``); the adapter SUMS it over the run.
* **``--no-extensions`` does not remove an explicit ``-e``** (measured: the
  victim survived), so unlike opencode's ``--pure`` there is no flag to refuse;
  a guard is always passed as ``-e <path>`` rather than left to discovery.

What this adapter deliberately does NOT do
------------------------------------------
* No guard wiring. Shipping the ``tool_call`` extension that calls
  ``tools.airgap.hook_compat.run_pre_tool_check`` is the guard task; until it
  lands ``guard_wired`` is declared ``false`` and ``verify_guard()`` says so.
  Pi's Windows ``powershell`` tool is also not covered by the POSIX rm check
  (spike gap 7) — that check belongs in ``shared_checks.py``, written once.
* No ``spawn()``, sandbox or turn/token budget flag: ``pi --mode json`` has
  none, and an execution mode with no consumer would invent a capability the
  capability matrix is supposed to MEASURE.

LLM-agnostic
------------
No model id appears in this module. The model is ``session.metadata['model_id']``
or ``$ICDEV_PI_MODEL`` (``.env``), in Pi's own ``provider/id`` form, where the
provider is declared in ``$PI_CODING_AGENT_DIR/models.json`` — the
OpenAI-compatible shape a vLLM endpoint exposes. With neither set, Pi uses its
own configured default.

OS-agnostic
-----------
``shell=False``, ``encoding='utf-8'`` on every stream, ``pathlib`` throughout,
and the one platform branch (PATHEXT discovery — npm installs a ``.cmd`` shim)
is shared with ``codex_cli`` and two-sided. A prompt past the Windows
command-line limit raises ``OSError`` at spawn time and is REPORTED as a failed
result, never swallowed.

Session metadata keys this adapter understands (all optional):

    model_id         str  -> ``--model``
    llm_function     str  -> with neither model set and ICDEV_HARNESS_LLM=vllm,
                            the router's vLLM choice (omx-vllm-04)
    offline          bool -> default True: ``--offline`` (no start-up network)
    approve          bool -> ``--approve`` (trust the project: .pi/mcp.json)
    extensions       list -> one ``-e <path>`` each
    agent_dir        str  -> ``PI_CODING_AGENT_DIR`` (default ``~/.pi/agent``)
    extra_args       list -> appended before the prompt
    dispatch_source  str  -> sets ICDEV_DISPATCH_SOURCE + _TASK_ID
    env              dict -> extra environment variables
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
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


_EXECUTABLE_NAME = "pi"
_ENV_EXECUTABLE = "ICDEV_PI_CLI"
_ENV_MODEL = "ICDEV_PI_MODEL"

_COMPLETION_MARKERS = ("[DONE]", "Task completed", "done.")

_USAGE_KEYS = {
    "input": "input",
    "output": "output",
    "cacheRead": "cache_read",
    "cacheWrite": "cache_write",
    "reasoning": "reasoning",
    "totalTokens": "total",
}


def resolve_pi_cli(is_windows: Optional[bool] = None) -> Optional[str]:
    """Absolute path to the Pi CLI, or None.

    ``$ICDEV_PI_CLI`` wins and may be an explicit path or a bare name.
    Otherwise ``shutil.which`` (PATHEXT on Windows), then ``~/.local/bin``,
    which is where Omarchy's mise stubs live.
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
    """JSONL records split on ``\\n`` ONLY — U+2028/2029 are not boundaries."""
    out: List[Dict[str, Any]] = []
    for line in (stdout or "").split("\n"):
        line = line.strip("\r \t")
        if not line.startswith("{"):
            continue
        try:
            parsed = json.loads(line)
        except (ValueError, TypeError):
            continue
        if isinstance(parsed, dict):
            out.append(parsed)
    return out


def _message_text(message: Dict[str, Any]) -> str:
    content = message.get("content")
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return ""
    return "".join(
        str(part.get("text") or "")
        for part in content
        if isinstance(part, dict) and part.get("type") == "text"
    )


def _is_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _parse_pi_output(stdout: str) -> Tuple[str, Dict[str, Any]]:
    """Split ``pi --mode json`` JSONL into (final text, structured).

    Keys Pi did not report are OMITTED, never defaulted to zero: a run whose
    stream carried no ``usage`` reported no tokens, which is not the same fact
    as a run that used none. Plain-text stdout degrades to being the answer.
    """
    raw = stdout or ""
    events = _events(raw)
    if not events:
        return raw, {}

    final_text = ""
    turns = 0
    stop_reason = ""
    errors: List[str] = []
    session_id = ""
    settled: Optional[bool] = None
    aborted = False
    calls: Dict[str, Dict[str, Any]] = {}
    order: List[str] = []
    tokens: Dict[str, int] = {}
    cost: Optional[float] = None

    for event in events:
        kind = str(event.get("type") or "")
        if kind == "session":
            sid = event.get("id")
            if isinstance(sid, str) and sid:
                session_id = sid
        elif kind == "message_end":
            message = event.get("message")
            if not isinstance(message, dict) or message.get("role") != "assistant":
                continue
            turns += 1
            text = _message_text(message)
            if text.strip():
                final_text = text
            stop_reason = str(message.get("stopReason") or "")
            if stop_reason == "error":
                errors.append(str(message.get("errorMessage") or
                                  "pi reported stopReason 'error'"))
            usage = message.get("usage")
            if isinstance(usage, dict):
                for src, dst in _USAGE_KEYS.items():
                    if _is_int(usage.get(src)):
                        tokens[dst] = tokens.get(dst, 0) + usage[src]
                spent = usage.get("cost")
                total = spent.get("total") if isinstance(spent, dict) else None
                if isinstance(total, (int, float)) and not isinstance(total, bool):
                    cost = (cost or 0.0) + float(total)
        elif kind in ("tool_execution_start", "tool_execution_end"):
            call_id = str(event.get("toolCallId") or f"_anon{len(order)}")
            call = calls.get(call_id)
            if call is None:
                call = {"name": str(event.get("toolName") or ""),
                        "call_id": call_id, "status": "started", "input": {}}
                calls[call_id] = call
                order.append(call_id)
            if kind == "tool_execution_start":
                args = event.get("args")
                call["input"] = args if isinstance(args, dict) else {}
            else:
                call["status"] = "error" if event.get("isError") else "completed"
        elif kind == "agent_settled":
            settled = True
            aborted = bool(event.get("aborted"))

    tool_calls = [calls[cid] for cid in order]
    structured: Dict[str, Any] = {
        "is_error": bool(errors) or aborted,
        "task_complete": (bool(settled) and not aborted and not errors
                          and stop_reason == "stop"),
        "events": len(events),
        "turns": turns,
        "settled": bool(settled),
        "aborted": aborted,
        "tool_calls": len(tool_calls),
        "tool_errors": sum(1 for c in tool_calls if c["status"] == "error"),
        "tool_call_list": tool_calls,
    }
    if stop_reason:
        structured["stop_reason"] = stop_reason
    if session_id:
        structured["session_id"] = session_id
    if errors:
        structured["error"] = "; ".join(errors)
    elif aborted:
        structured["error"] = "pi run was aborted"
    if "input" in tokens:
        structured["input_tokens"] = tokens["input"]
    if "output" in tokens:
        structured["output_tokens"] = tokens["output"]
    if tokens:
        structured["tokens"] = tokens
    if cost is not None:
        structured["total_cost_usd"] = cost

    return final_text, structured


class PiCliAdapter:
    """AgentAdapter over ``pi --mode json``."""

    name = "pi_cli"

    # ── discovery ────────────────────────────────────────────────────────────
    def available(self) -> bool:
        """True only when the CLI resolves on this host — no subprocess."""
        try:
            return resolve_pi_cli() is not None
        except OSError:
            return False

    def resolve(self) -> str:
        found = resolve_pi_cli()
        if not found:
            raise NotInstalledError(
                f"Pi CLI not found: tried {_EXECUTABLE_NAME} on PATH, "
                f"~/.local/bin, and ${_ENV_EXECUTABLE}"
            )
        return found

    # ── construction ─────────────────────────────────────────────────────────
    def prepare_prompt(self, session: AgentSession) -> str:
        """``pi --mode json`` takes one message, so a system prompt is prepended.

        Persistent instructions live in ``AGENTS.md`` (measured: loaded into
        ``project_context`` with ``--approve``).
        """
        if not session.system_prompt:
            return session.prompt
        return f"{session.system_prompt}\n\n{session.prompt}"

    def build_argv(self, session: AgentSession) -> List[str]:
        """The command line, ending in the prompt."""
        meta = session.metadata or {}
        argv = [self.resolve(), "--mode", "json"]
        if meta.get("offline", True):
            argv.append("--offline")
        model_id = meta.get("model_id") or os.environ.get(_ENV_MODEL)
        if model_id:
            argv += ["--model", str(model_id)]
        else:
            # omx-vllm-04: the router's vLLM choice for the task's llm_function.
            argv += routed_model_argv("pi", meta)
        if meta.get("approve"):
            argv.append("--approve")
        for extension in meta.get("extensions") or []:
            argv += ["-e", str(Path(extension))]
        argv += [str(arg) for arg in (meta.get("extra_args") or [])]
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
        if meta.get("agent_dir"):
            env["PI_CODING_AGENT_DIR"] = str(Path(meta["agent_dir"]))
        for key, value in (meta.get("env") or {}).items():
            env[str(key)] = str(value)
        return env

    # ── execution ────────────────────────────────────────────────────────────
    def invoke(self, session: AgentSession) -> AgentResult:
        """Run one Pi session to completion and return the result.

        Raises:
            NotInstalledError: the CLI is not present on this host. Every other
                failure — non-zero exit, a provider error behind exit 0, an
                aborted run, a timeout, an argv the OS refused — is
                ``completed=False`` with a reason.
        """
        argv = self.build_argv(session)
        env = self.build_env(session)

        t0 = time.time()

        def _failed(exit_code: int, error: str) -> AgentResult:
            return AgentResult(
                task_id=session.task_id,
                adapter_name=self.name,
                completed=False,
                exit_code=exit_code,
                output="",
                error=error,
                duration_ms=int((time.time() - t0) * 1000),
                structured={},
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
            return _failed(-1, f"Pi CLI timed out after {session.timeout_seconds}s")
        except FileNotFoundError as exc:
            raise NotInstalledError(f"Pi CLI missing: {exc}") from exc
        except OSError as exc:
            # e.g. WinError 206 — the prompt is past the command-line limit.
            return _failed(-1, f"Pi CLI could not be started: {exc}")

        text, envelope = _parse_pi_output(proc.stdout or "")
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
                error = (f"pi exited {proc.returncode} without settling on a "
                         "final assistant stopReason 'stop'")
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
        """Whether ICDEV's guard sees this adapter's tool calls.

        Not yet: the ``tool_call`` extension passed with ``-e`` is the guard
        task. This returns the honest answer rather than being absent so the
        capability matrix measures ``absent`` instead of ``unconfirmed``.
        """
        return {
            "wired": False,
            "reason": "no ICDEV guard extension is passed by this adapter "
                      "(Pi guard task)",
        }

    # ── protocol tail ────────────────────────────────────────────────────────
    def detect_completion(self, output: str) -> bool:
        """A JSONL stream is authoritative; plain text falls back to markers."""
        if not output:
            return False
        if _events(output):
            _, envelope = _parse_pi_output(output)
            return bool(envelope.get("task_complete"))
        tail = output[-500:]
        return any(marker in tail for marker in _COMPLETION_MARKERS)

    def parse_response(self, raw: str) -> Dict[str, Any]:
        """Content, tool calls, usage and error from a raw ``--mode json`` run."""
        text, envelope = _parse_pi_output(raw or "")
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


ADAPTER = PiCliAdapter()

__all__ = ["PiCliAdapter", "ADAPTER", "resolve_pi_cli"]
