# CUI // SP-CTI
"""OPT-71: Claude Code CLI adapter.

Wraps the Claude Code CLI (`claude` on PATH) as an AgentAdapter. Does
NOT re-implement the dispatch logic already in
tools/genesis/reflexes/kanban.py — the kanban path is still the primary
runtime. This adapter is for NEW consumers (pr_watcher OPT-70,
standalone orchestrators) that want a uniform interface.
"""
from __future__ import annotations

import shutil
import subprocess
import time
from pathlib import Path
from typing import Any, Dict

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
                    "--output-format", "text",
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
            output = (proc.stdout or "") + ("" if proc.returncode == 0
                                            else "\n" + (proc.stderr or ""))
            return AgentResult(
                task_id=session.task_id,
                adapter_name=self.name,
                completed=(proc.returncode == 0
                           and self.detect_completion(proc.stdout or "")),
                exit_code=proc.returncode,
                output=output,
                duration_ms=dur,
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
