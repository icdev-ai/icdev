# CUI // SP-CTI
"""Error taxonomy and structured tool results for the SAG runtime (arr-tax-01, arr-res-01).

``dispatch.make_handler`` used to collapse every tool failure to
``f"error executing {name}: {exc}"``. A missing library, a network blip, a
permission denial and a genuine code bug all arrived at the LLM as the same
shape of prose, so the loop could not tell "try that again" from "you cannot do
this here" from "ask a human". This module supplies the missing distinction.

**It classifies; it does not install.** ICDEV targets air-gapped IL4-IL6.
``args/security_gates.yaml`` enforces ``sbom_max_age_days``, ``min_slsa_level``
and attestation presence, and ``tools/airgap/wheel_vendor.py`` exists so
packages arrive as vetted, vendored wheels. An agent that repaired itself by
running ``pip install`` would invalidate the SBOM and defeat the air-gap story,
so a missing capability is *declared and degraded around* — never installed.
``tools/airgap/pdf_fallback.py`` is the shape to copy: when a PDF library is
absent it announces a local fallback chain rather than acquiring anything.
Full rationale: ``docs/spikes/ahx-00-agent-audit-docs-disposition.md``.

Composition (ADR D384 — compose, don't rebuild):

* the environmental-vs-code-bug split is **borrowed from**
  ``tools.workflow.self_debug``, which already curates those two exception sets
  for post-hoc build RCA. Two lists would drift apart and disagree about the
  same exception.
* ``tools.resilience.errors.ICDevError`` already carries an explicit
  ``retryable`` flag. When a raiser has stated its intent, that beats guessing
  from the class name.
* backoff comes from ``tools.resilience.retry.backoff_delay``.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from tools.logging.icdev_logger import get_logger

logger = get_logger("icdev.agent_runtime.error_recovery")

# --- Dispositions ----------------------------------------------------------
#: The failure looks transient. Retry the identical call ONCE, then give up.
RETRY_SAFE = "retry_safe"
#: A capability is genuinely absent. Announce it and work around it. Never install.
DEGRADE = "degrade"
#: A human must decide. File a card carrying the context.
ESCALATE = "escalate"
#: A code-level bug the model can reason about. Hand it back and let it adapt.
TERMINAL = "terminal"

DISPOSITIONS = (RETRY_SAFE, DEGRADE, ESCALATE, TERMINAL)

# Exceptions that mean "the environment misbehaved", not "the code is wrong".
# Mirrors self_debug._QUARANTINE_EXCEPTIONS; used only if that import fails.
_FALLBACK_ENVIRONMENTAL = frozenset({
    "TimeoutError", "ConnectionError", "ConnectionRefusedError",
    "ConnectionResetError", "BrokenPipeError", "OSError", "IOError",
    "MemoryError", "socket.timeout", "concurrent.futures.TimeoutError",
})

# Environmental failures that retrying cannot fix — a human has to act.
_ENVIRONMENTAL_BUT_HOPELESS = frozenset({"MemoryError"})

# Always a human decision, regardless of which set they fall in.
_ALWAYS_ESCALATE = frozenset({"PermissionError"})

_MISSING_MODULE_RE = re.compile(r"No module named ['\"]([\w.]+)['\"]")

_environmental_cache: frozenset[str] | None = None


def _environmental_exceptions() -> frozenset[str]:
    """The shared environmental-exception set, borrowed from self_debug.

    Imported lazily: self_debug pulls in git_utils/subprocess and costs ~0.3s,
    which does not belong in the import path of every agent turn.
    """
    global _environmental_cache
    if _environmental_cache is not None:
        return _environmental_cache
    try:
        from tools.workflow.self_debug import _QUARANTINE_EXCEPTIONS

        _environmental_cache = frozenset(_QUARANTINE_EXCEPTIONS)
    except Exception as exc:  # noqa: BLE001
        logger.debug("error_recovery: self_debug unavailable, using fallback set: %s", exc)
        _environmental_cache = _FALLBACK_ENVIRONMENTAL
    return _environmental_cache


@dataclass
class Classification:
    """What kind of failure this is, and what may be done about it."""

    disposition: str
    error_type: str
    remediation_hint: str = ""
    #: Import name that was missing — set only when disposition is DEGRADE.
    missing_capability: str = ""

    @property
    def retry_safe(self) -> bool:
        return self.disposition == RETRY_SAFE


@dataclass
class ToolResult:
    """A tool outcome the loop can reason about instead of parsing prose.

    ``render()`` is what the model sees. It stays human-readable — the model is
    still the consumer — but leads with the machine-checkable fields so a caller
    can branch on ``disposition`` without regex over an error message.
    """

    success: bool
    output: str = ""
    error_type: str = ""
    disposition: str = ""
    remediation_hint: str = ""
    missing_capability: str = ""
    retried: bool = False
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "success": self.success,
            "output": self.output,
            "error_type": self.error_type,
            "disposition": self.disposition,
            "remediation_hint": self.remediation_hint,
            "missing_capability": self.missing_capability,
            "retried": self.retried,
            **({"metadata": self.metadata} if self.metadata else {}),
        }

    def render(self) -> str:
        """Format for the agent loop's tool_result channel."""
        if self.success:
            return self.output
        parts = [f"error [{self.disposition or 'terminal'}]"]
        if self.error_type:
            parts.append(f"type={self.error_type}")
        if self.missing_capability:
            parts.append(f"missing={self.missing_capability}")
        if self.retried:
            parts.append("retried=1")
        head = " ".join(parts)
        body = self.output.strip()
        hint = self.remediation_hint.strip()
        out = f"{head}: {body}" if body else head
        return f"{out}\n{hint}" if hint else out


def _degrade_hint(module_name: str) -> str:
    return (
        f"The capability provided by {module_name!r} is not installed in this "
        f"environment, and this agent will NOT install it: ICDEV runs air-gapped "
        f"at IL4-IL6, where packages must arrive as vetted vendored wheels "
        f"(tools/airgap/wheel_vendor.py) so the SBOM and SLSA attestation stay "
        f"valid. Do not retry this call. Either complete the task another way "
        f"(see tools/airgap/pdf_fallback.py for the declared-fallback pattern), "
        f"or tell the operator to vendor {module_name!r} and re-run."
    )


def classify(exc: BaseException) -> Classification:
    """Map an exception to a disposition. Pure — never raises, never acts."""
    error_type = type(exc).__name__
    try:
        message = str(exc)
    except Exception:  # noqa: BLE001
        # An exception whose __str__ raises is pathological, but classification
        # runs on the failure path — it must not add a second failure.
        message = ""

    # 1. An explicit contract beats inference. tools/resilience/errors.py raisers
    #    already declare whether the caller should retry.
    try:
        from tools.resilience.errors import ICDevError

        if isinstance(exc, ICDevError):
            if getattr(exc, "retryable", False):
                return Classification(
                    RETRY_SAFE,
                    error_type,
                    f"{error_type} declared itself retryable; retrying once.",
                )
            return Classification(
                ESCALATE,
                error_type,
                f"{error_type} declared itself non-retryable — a human should look.",
            )
    except Exception:  # noqa: BLE001 — classification must never fail
        pass

    # 2. A missing import is a capability gap, not an error to retry.
    if isinstance(exc, ImportError):
        match = _MISSING_MODULE_RE.search(message)
        missing = match.group(1) if match else (getattr(exc, "name", "") or "")
        return Classification(
            DEGRADE,
            error_type,
            _degrade_hint(missing or "the required module"),
            missing_capability=missing,
        )

    if error_type in _ALWAYS_ESCALATE:
        return Classification(
            ESCALATE,
            error_type,
            "Permission was refused. The agent must not attempt to widen its own "
            "access — raise this with an operator.",
        )

    if error_type in _ENVIRONMENTAL_BUT_HOPELESS:
        return Classification(
            ESCALATE,
            error_type,
            f"{error_type} is a host-resource failure that retrying cannot fix.",
        )

    if error_type in _environmental_exceptions():
        return Classification(
            RETRY_SAFE,
            error_type,
            f"{error_type} looks transient (network/IO). Retrying the same call once.",
        )

    return Classification(
        TERMINAL,
        error_type,
        f"{error_type} looks like a code-level fault rather than an environment "
        f"problem. Re-reading the inputs and adjusting the call is more likely to "
        f"help than repeating it.",
    )


def retry_delay_seconds(attempt: int = 1) -> float:
    """Backoff before the single retry, reusing the shared resilience helper."""
    try:
        from tools.resilience.retry import backoff_delay

        return float(backoff_delay(attempt, 0.25, 2.0))
    except Exception:  # noqa: BLE001
        return 0.25


# ---------------------------------------------------------------------------
# Escalation (arr-esc-01) — the only side-effecting function in this module
# ---------------------------------------------------------------------------
#: Escalations per tool per process. A tool failing in a loop must not paper the
#: board with duplicates; the first card carries the evidence, the rest are noise.
_MAX_ESCALATIONS_PER_TOOL = 1
_escalated: dict[str, int] = {}


def _escalation_id(tool_name: str, error_type: str) -> str:
    """Stable id so re-running the same failure updates nothing and inserts once."""
    import hashlib

    digest = hashlib.sha256(f"{tool_name}:{error_type}".encode()).hexdigest()[:10]
    return f"arr-esc-{digest}"


def file_escalation_card(
    *,
    tool_name: str,
    classification: Classification,
    error_message: str,
    tool_input: dict[str, Any] | None = None,
    task_id: str | None = None,
) -> str | None:
    """File a HITL card for a failure the agent must not resolve on its own.

    Routed through ``tools.kanban.task_factory.create_tasks`` — the canonical
    seeder — rather than a raw INSERT, and given a deterministic id so repeated
    occurrences of the same failure deduplicate instead of accumulating.

    Best-effort: returns the task id, or ``None`` if a card could not be filed.
    Never raises. An agent that crashed while trying to report a crash would be
    strictly worse than one that simply reported the original failure.
    """
    key = f"{tool_name}:{classification.error_type}"
    if _escalated.get(key, 0) >= _MAX_ESCALATIONS_PER_TOOL:
        logger.debug("error_recovery: escalation already filed this process for %s", key)
        return None

    card_id = _escalation_id(tool_name, classification.error_type)
    try:
        from tools.kanban.task_factory import create_tasks

        redacted_input = ", ".join(sorted((tool_input or {}).keys())) or "(none)"
        description = (
            f"The SAG runtime stopped on a failure it must not resolve itself.\n\n"
            f"Tool:        {tool_name}\n"
            f"Exception:   {classification.error_type}\n"
            f"Message:     {error_message[:500]}\n"
            f"Input keys:  {redacted_input}\n"
            f"Originating task: {task_id or '(none)'}\n\n"
            f"Why it escalated: {classification.remediation_hint}\n\n"
            f"Only argument NAMES are recorded, not values — a tool input can "
            f"carry CUI, and this card is board-visible.\n\n"
            f"Filed by tools/agent_runtime/error_recovery.py (arr-esc-01)."
        )
        created = create_tasks([{
            "id": card_id,
            "title": f"SAG escalation: {tool_name} raised {classification.error_type}",
            "description": description,
            "task_type": "chore",
            "priority": "high",
            "status": "suggested",
        }])
        _escalated[key] = _escalated.get(key, 0) + 1
        if created:
            logger.info("error_recovery: filed escalation card %s for %s", card_id, key)
            return card_id
        logger.debug("error_recovery: escalation card %s already on the board", card_id)
        return card_id
    except Exception as exc:  # noqa: BLE001
        logger.warning("error_recovery: could not file escalation card for %s: %s", key, exc)
        return None
