# [TEMPLATE: CUI // SP-CTI]
"""CLILLMProvider — serve LLM requests via a locally authenticated Claude CLI.

Shells out to the Claude Code CLI (``claude -p <prompt> --output-format text``)
and wraps its stdout in an :class:`~tools.llm.provider.LLMResponse`. Used when
ICDEV™ runs without cloud API keys, or in an air-gapped environment where the
only reachable model is the operator's local Claude Code session.

This is the *synchronous* front-end: it blocks for up to ``soft_wait_seconds``
and returns the CLI output directly. Job-store backed deferral (soft-wait +
``CLIJobDeferred`` + background worker) is layered on later (see the
``uclb-job-*`` tasks); here a subprocess failure surfaces as
:class:`~tools.llm.router.LLMUnavailableError` so the router cleanly falls
through to the next link in the routing chain.

``LLMUnavailableError`` is imported lazily inside :meth:`CLILLMProvider.invoke`
because :mod:`tools.llm.router` imports this module to build the provider — a
module-level import here would be circular.
"""

import shutil
import subprocess
import time
from typing import Any, Dict, List

from tools.llm.provider import LLMProvider, LLMRequest, LLMResponse
from tools.logging.icdev_logger import get_logger

logger = get_logger("icdev.llm.cli_bridge.cli_provider")

# Default logical model id reported when the caller does not supply one.
DEFAULT_MODEL_ID = "claude-cli"


def _flatten_messages(messages: List[Dict[str, Any]], system_prompt: str = "") -> str:
    """Collapse universal messages + system prompt into a single prompt string.

    The Claude CLI takes a single prompt argument, so multimodal/structured
    content blocks are reduced to their text. Non-text blocks (images, tool
    results other than text) are dropped — the CLI bridge is a text fallback.
    """
    parts: List[str] = []
    if system_prompt:
        parts.append(system_prompt)

    for msg in messages or []:
        content = msg.get("content", "")
        if isinstance(content, str):
            text = content
        elif isinstance(content, list):
            chunks: List[str] = []
            for block in content:
                if isinstance(block, dict) and block.get("type") == "text":
                    chunks.append(block.get("text", ""))
                elif isinstance(block, dict) and block.get("type") == "tool_result":
                    for inner in block.get("content", []):
                        if isinstance(inner, dict) and inner.get("type") == "text":
                            chunks.append(inner.get("text", ""))
            text = "\n".join(c for c in chunks if c)
        else:
            text = str(content)
        if text:
            parts.append(text)

    return "\n\n".join(parts)


class CLILLMProvider(LLMProvider):
    """LLM provider that shells out to a locally authenticated Claude CLI."""

    def __init__(
        self,
        cli_binary: str = "claude",
        backend: str = "auto",
        soft_wait_seconds: int = 60,
    ):
        self._cli_binary = cli_binary or "claude"
        self._backend = backend or "auto"
        try:
            self._soft_wait_seconds = int(soft_wait_seconds)
        except (TypeError, ValueError):
            self._soft_wait_seconds = 60

    @property
    def provider_name(self) -> str:
        return "cli"

    def _build_command(self, prompt: str) -> List[str]:
        """Build the Claude CLI argv for a one-shot, non-interactive prompt."""
        return [self._cli_binary, "-p", prompt, "--output-format", "text"]

    def invoke(self, request: LLMRequest, model_id: str, model_config: dict) -> LLMResponse:
        """Run the CLI once and return its stdout as an LLMResponse.

        Raises :class:`~tools.llm.router.LLMUnavailableError` on any subprocess
        failure (binary missing, non-zero exit, timeout) so the router can fall
        through to the next provider in the chain rather than crashing.
        """
        # Lazy import — router imports this module, so a top-level import is circular.
        from tools.llm.router import LLMUnavailableError

        start = time.time()
        prompt = _flatten_messages(request.messages, request.system_prompt)
        cmd = self._build_command(prompt)

        try:
            proc = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=self._soft_wait_seconds,
            )
        except FileNotFoundError as exc:
            logger.warning("Claude CLI binary not found: %s", self._cli_binary)
            raise LLMUnavailableError(
                f"Claude CLI binary not found: {self._cli_binary}",
                chain=[DEFAULT_MODEL_ID],
            ) from exc
        except subprocess.TimeoutExpired as exc:
            logger.warning("Claude CLI timed out after %ds", self._soft_wait_seconds)
            raise LLMUnavailableError(
                f"Claude CLI timed out after {self._soft_wait_seconds}s",
                chain=[DEFAULT_MODEL_ID],
            ) from exc
        except Exception as exc:  # pragma: no cover - defensive
            logger.warning("Claude CLI invocation failed: %s", exc)
            raise LLMUnavailableError(
                f"Claude CLI invocation failed: {exc}",
                chain=[DEFAULT_MODEL_ID],
            ) from exc

        if proc.returncode != 0:
            stderr = (proc.stderr or "").strip()
            logger.warning("Claude CLI exited %s: %s", proc.returncode, stderr)
            raise LLMUnavailableError(
                f"Claude CLI exited {proc.returncode}: {stderr}",
                chain=[DEFAULT_MODEL_ID],
            )

        response = LLMResponse(provider="cli")
        response.content = (proc.stdout or "").strip()
        response.model_id = model_id or DEFAULT_MODEL_ID
        response.duration_ms = int((time.time() - start) * 1000)
        response.classification = getattr(request, "classification", "CUI")
        response.stop_reason = "stop"
        return response

    def check_availability(self, model_id: str) -> bool:
        """True when the CLI binary is resolvable on PATH (or an absolute path)."""
        return shutil.which(self._cli_binary) is not None
