# CUI // SP-CTI
"""Local Claude Code CLI provider for the LLM router.

Fronts LLM routing through the locally-installed `claude` CLI (Claude Code in
headless print mode) so ICDEV functions work with NO cloud API key — useful for
air-gapped / no-key deployments. Activated by prepending the `claude-cli` model
to every routing chain when ``ICDEV_CLI_BRIDGE`` is truthy (see router).

Implementation: shells out to ``claude -p`` with the prompt piped via stdin,
running in a temp working directory so it does not load the repo's project
context / MCP servers (a clean text-generation call, not an agent run).
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess  # nosec B404 — invoking the trusted local `claude` CLI
import tempfile
import time
from typing import List

from tools.llm.provider import LLMProvider, LLMRequest, LLMResponse
from tools.logging.icdev_logger import get_logger

logger = get_logger("icdev.llm.cli")

_ENV_RE = re.compile(r"\$\{([A-Za-z0-9_]+)(:-([^}]*))?\}")


def _expand_env(val: str) -> str:
    """Expand ${VAR} / ${VAR:-default} the same way llm_config.yaml expects.

    The router does not env-expand model_id, so the CLI provider must — otherwise
    the literal '${ICDEV_CLI_BRIDGE_MODEL:-claude-opus-4-8}' reaches `claude --model`.
    """
    if not isinstance(val, str):
        return val
    return _ENV_RE.sub(lambda m: os.environ.get(m.group(1), m.group(3) if m.group(3) is not None else ""), val)


def _flatten_content(content) -> str:
    """Reduce a message content (str or list-of-blocks) to plain text."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: List[str] = []
        for block in content:
            if isinstance(block, dict):
                if block.get("type") == "text":
                    parts.append(str(block.get("text", "")))
            elif isinstance(block, str):
                parts.append(block)
        return "\n".join(parts)
    return str(content or "")


class CLIProvider(LLMProvider):
    """LLM provider that delegates to the local `claude` CLI (print mode)."""

    def __init__(self, cli_binary: str = "claude", soft_wait_seconds: int = 60,
                 backend: str = "auto"):
        self._cli_binary = cli_binary or "claude"
        # Model calls can exceed the soft wait; allow a generous hard timeout.
        self._timeout = max(int(soft_wait_seconds or 60), 180)
        self._backend = backend

    @property
    def provider_name(self) -> str:
        return "cli"

    def _resolve_binary(self) -> str | None:
        return shutil.which(self._cli_binary) or (
            self._cli_binary if os.path.sep in self._cli_binary and os.path.exists(self._cli_binary) else None)

    def check_availability(self, model_id: str) -> bool:
        return self._resolve_binary() is not None

    def invoke(self, request: LLMRequest, model_id: str, model_config: dict) -> LLMResponse:
        binary = self._resolve_binary()
        if not binary:
            raise RuntimeError(
                f"Claude CLI not found on PATH ('{self._cli_binary}'). "
                "Install Claude Code or set CLAUDE_CLI_PATH."
            )

        # Combine system + user messages into a single prompt piped via stdin.
        segments: List[str] = []
        if request.system_prompt:
            segments.append(request.system_prompt.strip())
        for msg in request.messages or []:
            if msg.get("role") == "user":
                segments.append(_flatten_content(msg.get("content")))
        prompt = "\n\n".join(s for s in segments if s).strip() or "Hello"

        cmd = [binary, "-p", "--output-format", "text"]
        model_id = _expand_env(model_id or "")
        # Only pass --model when it resolved to a concrete id (skip unexpanded ${...}).
        if model_id and "${" not in model_id:
            cmd += ["--model", model_id]

        start = time.time()
        try:
            # Run in a temp cwd so the CLI does not load this repo's CLAUDE.md/MCP
            # — we want a clean text generation, not a project agent session.
            proc = subprocess.run(  # nosec B603 — fixed argv, trusted local binary
                cmd, input=prompt, capture_output=True, text=True,
                timeout=self._timeout, cwd=tempfile.gettempdir(),
                encoding="utf-8", errors="replace",
            )
        except subprocess.TimeoutExpired:
            raise TimeoutError(f"Claude CLI timed out after {self._timeout}s")
        except OSError as exc:
            raise RuntimeError(f"Claude CLI invocation failed: {exc}")

        if proc.returncode != 0:
            raise RuntimeError(
                f"Claude CLI exited {proc.returncode}: {(proc.stderr or '').strip()[:300]}"
            )

        text = (proc.stdout or "").strip()
        resp = LLMResponse(provider="cli", model_id=model_id or self._cli_binary, content=text)
        resp.duration_ms = int((time.time() - start) * 1000)
        # Best-effort structured output (matches other providers' behavior).
        if text[:1] in ("{", "["):
            try:
                resp.structured_output = json.loads(text)
            except (json.JSONDecodeError, ValueError):
                pass
        return resp
