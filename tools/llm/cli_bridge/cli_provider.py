# [TEMPLATE: CUI // SP-CTI]
"""CLILLMProvider — serve LLM requests via a locally authenticated Claude CLI.

Used when ICDEV™ runs without cloud API keys, or in an air-gapped environment
where the only reachable model is the operator's local Claude Code session.

Job-store backed flow
---------------------
:meth:`CLILLMProvider.invoke` no longer shells out inline. Instead it:

1. ``create_job(...)`` — writes a row to ``cli_llm_jobs`` (pending).
2. ``_dispatch(job_id, backend)`` — hands the job to the selected backend
   worker. The subprocess / mailbox workers are layered on by the
   ``uclb-job-04/05/06`` tasks; until one is wired this is a graceful no-op and
   the job stays pending.
3. ``wait_for_job(job_id, soft_wait_seconds)`` — blocks up to the soft-wait.

The terminal status then decides the outcome:

* ``done``    → return an :class:`~tools.llm.provider.LLMResponse` from the row.
* ``error``   → raise :class:`~tools.llm.router.LLMUnavailableError`.
* still running at the soft-wait → raise :class:`CLIJobDeferred`.

``CLIJobDeferred`` subclasses ``LLMUnavailableError`` on purpose (see its
docstring): chat callers catch it specifically and switch to background mode,
while every other caller transparently hits its existing rule-based fallback —
and the job keeps running, caching its result in ``cli_llm_jobs`` for reuse.
"""

import shutil
import time
from typing import Any, Callable, Dict, List, Optional

from tools.llm.provider import LLMProvider, LLMRequest, LLMResponse
from tools.llm.router import LLMUnavailableError
from tools.logging.icdev_logger import get_logger

logger = get_logger("icdev.llm.cli_bridge.cli_provider")

# Default logical model id reported when the caller does not supply one.
DEFAULT_MODEL_ID = "claude-cli"

# Default poll cadence for the soft-wait; small so a quick job returns promptly.
DEFAULT_POLL_INTERVAL = 0.25


class CLIJobDeferred(LLMUnavailableError):
    """Raised when a CLI job is still running once the soft-wait window closes.

    Subclasses :class:`~tools.llm.router.LLMUnavailableError` deliberately so a
    single ``raise`` serves both caller populations:

    - **Chat callers** catch ``CLIJobDeferred`` *specifically*, read ``job_id``,
      and switch the conversation to background mode — showing a "still working"
      placeholder and posting the answer when the job finishes (uclb-async-*).
    - **Non-chat callers** that only know ``LLMUnavailableError`` fall through to
      their existing rule-based / template fallback, exactly as they would for
      any unavailable provider. Meanwhile the job keeps running and its result is
      cached in ``cli_llm_jobs`` for later reuse.

    Attributes:
        job_id: Id of the deferred ``cli_llm_jobs`` row to poll for the result.
    """

    def __init__(self, message: str, *, job_id: str = "", chain=None, function: str = ""):
        super().__init__(message, function=function, chain=chain)
        self.job_id = job_id


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


def _resolve_backend_dispatcher() -> Optional[Callable[[str, str], None]]:
    """Return the backend dispatch entry point, or ``None`` if none is wired.

    The subprocess / mailbox workers (uclb-job-04/05/06) will expose
    ``tools.llm.cli_bridge.backends.dispatch(job_id, backend)``. Until that
    module lands this returns ``None`` and jobs are left pending — the soft-wait
    then elapses and the caller is deferred (chat) or falls back (non-chat).
    """
    try:
        from tools.llm.cli_bridge.backends import dispatch
    except Exception:
        return None
    return dispatch


class CLILLMProvider(LLMProvider):
    """LLM provider that defers requests to a locally authenticated Claude CLI."""

    def __init__(
        self,
        cli_binary: str = "claude",
        backend: str = "auto",
        soft_wait_seconds: int = 60,
        dispatcher: Optional[Callable[[str, str], None]] = None,
        poll_interval: float = DEFAULT_POLL_INTERVAL,
    ):
        self._cli_binary = cli_binary or "claude"
        self._backend = backend or "auto"
        try:
            self._soft_wait_seconds = int(soft_wait_seconds)
        except (TypeError, ValueError):
            self._soft_wait_seconds = 60
        # Injectable seam: tests and the uclb-job-04/05/06 workers supply a
        # dispatch callable; when None we resolve the registered backend lazily.
        self._dispatcher = dispatcher
        try:
            self._poll_interval = float(poll_interval)
        except (TypeError, ValueError):
            self._poll_interval = DEFAULT_POLL_INTERVAL

    @property
    def provider_name(self) -> str:
        return "cli"

    def _build_command(self, prompt: str) -> List[str]:
        """Build the Claude CLI argv for a one-shot, non-interactive prompt.

        Retained for the backend workers (uclb-job-04/05/06), which run the CLI
        and write the result back to the ``cli_llm_jobs`` row.
        """
        return [self._cli_binary, "-p", prompt, "--output-format", "text"]

    def _dispatch(self, job_id: str, backend: str) -> None:
        """Hand a freshly-created job to the selected backend worker (non-blocking).

        Never raises: a missing/failing backend just leaves the job pending so
        the soft-wait can elapse and the caller degrade gracefully.
        """
        dispatcher = self._dispatcher or _resolve_backend_dispatcher()
        if dispatcher is None:
            logger.debug(
                "no CLI backend worker wired; job %s left pending for soft-wait", job_id
            )
            return
        try:
            dispatcher(job_id, backend)
        except Exception as exc:  # pragma: no cover - defensive
            logger.warning("CLI backend dispatch failed for job %s: %s", job_id, exc)

    def _response_from_job(
        self, job: Dict[str, Any], model_id: str, start: float
    ) -> LLMResponse:
        """Build an LLMResponse from a completed ``cli_llm_jobs`` row."""
        response = LLMResponse(provider="cli")
        response.content = (job.get("result") or "").strip()
        response.model_id = job.get("model_id") or model_id or DEFAULT_MODEL_ID
        response.input_tokens = int(job.get("input_tokens") or 0)
        response.output_tokens = int(job.get("output_tokens") or 0)
        response.duration_ms = int((time.time() - start) * 1000)
        response.classification = job.get("classification") or "CUI"
        response.stop_reason = "stop"
        return response

    def invoke(self, request: LLMRequest, model_id: str, model_config: dict) -> LLMResponse:
        """Create a job, dispatch it to the backend, and wait up to the soft-wait.

        Returns an :class:`LLMResponse` when the job finishes in time. Raises
        :class:`~tools.llm.router.LLMUnavailableError` if the job fails (so the
        router falls through to the next provider), or :class:`CLIJobDeferred`
        (a subclass) when the job is still running at the soft-wait — letting
        chat callers switch to background mode while the work continues.
        """
        from tools.llm.cli_bridge import job_store

        start = time.time()
        prompt = _flatten_messages(request.messages, request.system_prompt)
        backend = self._backend
        classification = getattr(request, "classification", "CUI") or "CUI"
        function = getattr(request, "model", "") or model_id or DEFAULT_MODEL_ID

        job_id = job_store.create_job(
            function=function,
            prompt=prompt,
            system_prompt=getattr(request, "system_prompt", "") or "",
            model_id=model_id or DEFAULT_MODEL_ID,
            backend=backend,
            context_id=(getattr(request, "agent_id", "") or None),
            classification=classification,
        )

        # Kick off the backend worker, then block for the soft-wait window.
        self._dispatch(job_id, backend)
        job = job_store.wait_for_job(
            job_id,
            timeout=self._soft_wait_seconds,
            poll_interval=self._poll_interval,
        )

        if job is None:  # row vanished before completing — treat as unavailable
            raise LLMUnavailableError(
                f"CLI job {job_id} disappeared before completing",
                chain=[DEFAULT_MODEL_ID],
            )

        status = job.get("status")

        if status == "done":
            return self._response_from_job(job, model_id, start)

        if status == "error":
            err = job.get("error") or "unknown error"
            logger.warning("CLI job %s failed: %s", job_id, err)
            raise LLMUnavailableError(
                f"CLI job {job_id} failed: {err}",
                chain=[DEFAULT_MODEL_ID],
            )

        # Still pending/running when the soft-wait closed — defer to background.
        logger.info(
            "CLI job %s still running after %ss soft-wait; deferring",
            job_id,
            self._soft_wait_seconds,
        )
        raise CLIJobDeferred(
            f"CLI job {job_id} still running after {self._soft_wait_seconds}s soft-wait",
            job_id=job_id,
            chain=[DEFAULT_MODEL_ID],
        )

    def check_availability(self, model_id: str) -> bool:
        """True when the CLI binary is resolvable on PATH (or an absolute path)."""
        return shutil.which(self._cli_binary) is not None
