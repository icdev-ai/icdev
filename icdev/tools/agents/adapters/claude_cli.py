# CUI // SP-CTI
"""OPT-71: Claude Code CLI adapter.

Wraps the Claude Code CLI (`claude` on PATH) as an AgentAdapter. Does
NOT re-implement the dispatch logic already in
tools/genesis/reflexes/kanban.py — the kanban path is still the primary
runtime. This adapter is for NEW consumers (pr_watcher OPT-70,
standalone orchestrators) that want a uniform interface.
"""
from __future__ import annotations

import json
import shutil
import subprocess
import time
from pathlib import Path
from typing import Any, Dict, Tuple

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


def _parse_cli_json(stdout: str) -> Tuple[str, Dict[str, Any]]:
    """Split the CLI's ``--output-format json`` envelope into (text, structured).

    The CLI is the only executor that knows what a session cost; without this the
    adapter reported a duration and nothing else, so any cost comparison against
    another adapter had one column permanently empty. Parsing is best-effort by
    design: an older CLI, or one that printed something else, degrades to
    treating stdout as plain text — the same contract callers had before.
    """
    raw = (stdout or "").strip()
    if not raw.startswith("{"):
        return stdout or "", {}
    try:
        payload = json.loads(raw)
    except (ValueError, TypeError):
        return stdout or "", {}
    if not isinstance(payload, dict):
        return stdout or "", {}

    usage = payload.get("usage") if isinstance(payload.get("usage"), dict) else {}
    structured: Dict[str, Any] = {
        "result_subtype": payload.get("subtype") or "",
        "is_error": bool(payload.get("is_error")),
        "turns": payload.get("num_turns") or 0,
        "session_id": payload.get("session_id") or "",
        "total_cost_usd": payload.get("total_cost_usd") or 0.0,
        "input_tokens": (usage.get("input_tokens") or 0)
        + (usage.get("cache_read_input_tokens") or 0)
        + (usage.get("cache_creation_input_tokens") or 0),
        "output_tokens": usage.get("output_tokens") or 0,
        "duration_api_ms": payload.get("duration_api_ms") or 0,
    }
    text = payload.get("result")
    if not isinstance(text, str):
        text = raw
    return text, structured


class ClaudeCliAdapter:
    name = "claude_cli"

    def available(self) -> bool:
        if shutil.which("claude") is not None:
            return True
        fallback = Path.home() / ".local" / "bin" / "claude"
        return fallback.exists()

    def _resolve(self) -> str:
        found = shutil.which("claude")
        if found:
            return found
        fallback = Path.home() / ".local" / "bin" / "claude"
        if fallback.exists():
            return str(fallback)
        raise NotInstalledError("claude CLI not on PATH")

    def prepare_prompt(self, session: AgentSession) -> str:
        if not session.system_prompt:
            return session.prompt
        return f"{session.system_prompt}\n\n{session.prompt}"

    def invoke(self, session: AgentSession) -> AgentResult:
        cli = self._resolve()
        prompt = self.prepare_prompt(session)

        t0 = time.time()
        try:
            proc = subprocess.run(
                [
                    cli,
                    "--dangerously-skip-permissions",
                    "--max-turns", str(session.max_turns),
                    # json, not text: the envelope carries cost, token usage and
                    # turn count. `output` below stays the assistant's text, so
                    # existing callers see no change.
                    "--output-format", "json",
                    "-p", prompt,
                ],
                cwd=session.working_dir or None,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=session.timeout_seconds,
            )
            dur = int((time.time() - t0) * 1000)
            text, structured = _parse_cli_json(proc.stdout or "")
            output = text + ("" if proc.returncode == 0
                             else "\n" + (proc.stderr or ""))
            return AgentResult(
                task_id=session.task_id,
                adapter_name=self.name,
                completed=(proc.returncode == 0
                           and not structured.get("is_error")
                           and self.detect_completion(text)),
                exit_code=proc.returncode,
                output=output,
                duration_ms=dur,
                structured=structured,
            )
        except subprocess.TimeoutExpired:
            dur = int((time.time() - t0) * 1000)
            return AgentResult(
                task_id=session.task_id,
                adapter_name=self.name,
                completed=False,
                exit_code=-1,
                output="",
                error=(
                    f"claude CLI timed out after "
                    f"{session.timeout_seconds}s"
                ),
                duration_ms=dur,
            )
        except FileNotFoundError as exc:
            raise NotInstalledError(f"claude CLI missing: {exc}") from exc

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
