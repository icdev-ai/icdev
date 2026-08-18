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

import base64
import os
import shutil
import time
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from tools.llm.provider import (
    PREFIX_CACHE_NONE,
    LLMProvider,
    LLMRequest,
    LLMResponse,
    PrefixCacheCapability,
)
from tools.llm.router import LLMUnavailableError
from tools.logging.icdev_logger import get_logger

logger = get_logger("icdev.llm.cli_bridge.cli_provider")

# Default logical model id reported when the caller does not supply one.
DEFAULT_MODEL_ID = "claude-cli"

# Default poll cadence for the soft-wait; small so a quick job returns promptly.
DEFAULT_POLL_INTERVAL = 0.25

# Backend labels. ``auto`` is resolved at dispatch time to one of the concrete
# backends; the concrete labels are what get recorded on the job row so the
# backend worker's ``claim_job(backend)`` filter matches.
BACKEND_AUTO = "auto"
BACKEND_SUBPROCESS = "subprocess"
BACKEND_MAILBOX = "mailbox"
CONCRETE_BACKENDS = (BACKEND_SUBPROCESS, BACKEND_MAILBOX)

# Env override for the backend selection (highest precedence). Accepts
# ``auto`` / ``subprocess`` / ``mailbox``; anything else is ignored.
BACKEND_ENV = "ICDEV_CLI_BRIDGE_BACKEND"


def resolve_backend(configured: str) -> str:
    """Resolve a configured backend to a concrete ``subprocess``/``mailbox`` label.

    Precedence:

    1. ``ICDEV_CLI_BRIDGE_BACKEND`` env override (``subprocess``/``mailbox``/``auto``).
    2. The ``configured`` value passed in (the provider's ``backend`` setting).
    3. If the effective value is ``auto``, pick ``subprocess`` when the host is
       headless-capable (:func:`capability.is_cli_headless_capable`), else
       ``mailbox``.

    Any unrecognized value degrades to ``auto`` resolution rather than being
    written verbatim to the job row.
    """
    env = (os.environ.get(BACKEND_ENV) or "").strip().lower()
    if env in CONCRETE_BACKENDS:
        return env
    if env == BACKEND_AUTO:
        effective = BACKEND_AUTO
    else:
        effective = (configured or BACKEND_AUTO).strip().lower()

    if effective in CONCRETE_BACKENDS:
        return effective

    # auto (or anything unrecognized) → capability-driven choice
    try:
        from tools.llm.cli_bridge import capability

        return BACKEND_SUBPROCESS if capability.is_cli_headless_capable() else BACKEND_MAILBOX
    except Exception as exc:  # pragma: no cover - defensive; default to subprocess
        logger.debug("backend capability probe failed (%s); defaulting to subprocess", exc)
        return BACKEND_SUBPROCESS


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


_CLI_TMP_DIR = Path("data/ndc_uploads/cli_tmp")


def _save_image_block(block: Dict[str, Any], idx: int) -> Optional[str]:
    """Save a base64 image_url block to a temp PNG file. Returns the path or None."""
    try:
        url = (block.get("image_url") or {}).get("url", "")
        if not url.startswith("data:"):
            return None
        # data:<mime>;base64,<data>
        header, data = url.split(",", 1)
        ext = "png"
        if "jpeg" in header or "jpg" in header:
            ext = "jpg"
        _CLI_TMP_DIR.mkdir(parents=True, exist_ok=True)
        img_bytes = base64.b64decode(data)
        tmp_file = _CLI_TMP_DIR / f"cli_img_{os.getpid()}_{idx}.{ext}"
        tmp_file.write_bytes(img_bytes)
        return str(tmp_file.resolve())
    except Exception:
        return None


def _flatten_messages(messages: List[Dict[str, Any]], system_prompt: str = "") -> str:
    """Collapse universal messages + system prompt into a single prompt string.

    Images are saved as temp files and referenced by path so Claude Code can
    read them via its Read tool (using --dangerously-skip-permissions).
    """
    parts: List[str] = []
    if system_prompt:
        parts.append(system_prompt)

    img_paths: List[str] = []
    img_idx = 0
    for msg in messages or []:
        content = msg.get("content", "")
        if isinstance(content, str):
            text = content
        elif isinstance(content, list):
            chunks: List[str] = []
            for block in content:
                if isinstance(block, dict) and block.get("type") == "text":
                    chunks.append(block.get("text", ""))
                elif isinstance(block, dict) and block.get("type") == "image_url":
                    saved = _save_image_block(block, img_idx)
                    if saved:
                        img_paths.append(saved)
                        img_idx += 1
                elif isinstance(block, dict) and block.get("type") == "tool_result":
                    for inner in block.get("content", []):
                        if isinstance(inner, dict) and inner.get("type") == "text":
                            chunks.append(inner.get("text", ""))
            text = "\n".join(c for c in chunks if c)
        else:
            text = str(content)
        if text:
            parts.append(text)

    if img_paths:
        img_note = (
            "\n\n[IMAGES FOR ANALYSIS]\n"
            + "\n".join(f"Image {i+1}: {p}" for i, p in enumerate(img_paths))
            + "\n\nPlease use your Read tool to view each image file listed above and include it in your analysis."
        )
        parts.append(img_note)

    return "\n\n".join(parts)


def _resolve_backend_dispatcher(backend: str) -> Optional[Callable[[str, str], None]]:
    """Return the ``dispatch`` entry point for ``backend``, or ``None`` if unwired.

    Each concrete backend exposes ``dispatch(job_id, backend)`` in its own module
    (``subprocess_backend`` from uclb-job-04, ``mailbox_backend`` from
    uclb-job-05). When the selected backend's module is absent — e.g. the mailbox
    worker has not landed yet — this returns ``None`` and the job is left pending:
    the soft-wait elapses and the caller is deferred (chat) or falls back
    (non-chat), while the row stays enqueued for a worker to claim later.

    Uses a static ``import`` per concrete backend (rather than
    ``importlib.import_module``) so the SIPA capability extractor does not flag
    this dispatch seam as ``dynamic_code``; each backend module is referenced
    verbatim by name, and the soft failure on a missing module preserves the
    original semantics.
    """
    if backend == BACKEND_SUBPROCESS:
        try:
            from tools.llm.cli_bridge import subprocess_backend as module  # noqa: F401
        except Exception:
            return None
        return getattr(module, "dispatch", None)
    if backend == BACKEND_MAILBOX:
        try:
            from tools.llm.cli_bridge import mailbox_backend as module  # noqa: F401
        except Exception:
            return None
        return getattr(module, "dispatch", None)
    return None


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

    @property
    def prefix_cache_capability(self) -> PrefixCacheCapability:
        """None AT THIS SEAM: the vendor CLI owns whatever caching happens."""
        return PrefixCacheCapability(
            support=PREFIX_CACHE_NONE,
            reason=(
                "Requests are handed to a separate Claude CLI process as prompt "
                "text. That subprocess decides its own caching and returns no token "
                "accounting, so ICDEV can neither request a breakpoint nor read a "
                "cached-token count. Anthropic caching may well be happening — it is "
                "simply not observable or controllable here."
            ),
        )

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
        dispatcher = self._dispatcher or _resolve_backend_dispatcher(backend)
        if dispatcher is None:
            logger.debug(
                "no '%s' CLI backend worker wired; job %s left pending for soft-wait",
                backend,
                job_id,
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

        # Opportunistically clear jobs orphaned in 'running' by a previously dead
        # worker host. Without this, wait_for_job() below could block behind a row
        # that can never reach a terminal status, deferring this caller forever
        # ("premature stale status"). Best-effort: never let reaping block invoke.
        try:
            job_store.reap_stale_jobs()
        except Exception as exc:  # pragma: no cover - defensive; reaping is advisory
            logger.debug("stale-job reap before invoke failed: %s", exc)

        start = time.time()
        prompt = _flatten_messages(request.messages, request.system_prompt)
        # Resolve 'auto' to a concrete backend now, at dispatch time, so the
        # chosen value is recorded on the row (claim_job filters by backend).
        backend = resolve_backend(self._backend)
        classification = getattr(request, "classification", "CUI") or "CUI"
        function = getattr(request, "model", "") or model_id or DEFAULT_MODEL_ID

        # Mailbox jobs are served by an external worker; if none is heartbeating
        # we still enqueue and let the soft-wait/deferred path carry the request.
        if backend == BACKEND_MAILBOX:
            try:
                from tools.llm.cli_bridge import capability

                if not capability.mailbox_worker_alive():
                    logger.info(
                        "mailbox backend selected but no live worker heartbeat; "
                        "enqueueing job for an external worker and deferring"
                    )
            except Exception:  # pragma: no cover - advisory log only
                pass

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
