# CUI // SP-CTI
"""Second Brain — PII redaction at LLM egress (cnr-me-02).

The Second Brain assembles deeply personal context (names, work emails, org,
objectives, challenges, attendee names/emails) and sends it to an LLM to draft
briefings and profile summaries. This module masks that PII *before* it leaves
the process, routing prompts through the shared redaction layer
(``tools.redaction.anonymizer``) behind a toggle.

Toggle resolution (first hit wins):
  1. env ``ICDEV_SECOND_BRAIN_REDACT_EGRESS`` (1/true/yes/on | 0/false/no/off)
  2. ``args/redaction_config.yaml`` → ``second_brain.redact_llm_egress``
  3. default: True (IL4 canvas — mask by default)

Fail behavior aligns with the platform ``redaction.fail_closed`` convention: if
masking is required but the anonymizer errors, a fail-closed platform raises so
the caller degrades to its deterministic template (no raw PII egresses); a
fail-open platform logs and proceeds.
"""
from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path

from tools.logging.icdev_logger import get_logger

logger = get_logger(__name__)

_TOGGLE_ENV = "ICDEV_SECOND_BRAIN_REDACT_EGRESS"
_TRUE = {"1", "true", "yes", "on"}
_FALSE = {"0", "false", "no", "off"}


class EgressRedactionUnavailable(RuntimeError):
    """Raised (fail-closed only) when required egress masking cannot run."""


def _redaction_config_path() -> Path:
    """Locate args/redaction_config.yaml by walking upward from this file.

    Robust across both the canonical (icdev/tools/…) and legacy-mirror (tools/…)
    trees, whose depths from the repo root differ.
    """
    here = Path(__file__).resolve()
    for parent in here.parents:
        candidate = parent / "args" / "redaction_config.yaml"
        if candidate.exists():
            return candidate
    # Fallback to the canonical relative location (icdev/tools/second_brain → repo root)
    return here.parents[3] / "args" / "redaction_config.yaml"


@lru_cache(maxsize=1)
def _load_redaction_config() -> dict:
    try:
        import yaml
        with open(_redaction_config_path(), encoding="utf-8") as fh:
            return yaml.safe_load(fh) or {}
    except Exception as exc:  # pragma: no cover - config read best-effort
        logger.debug("[second_brain] redaction_config load failed: %s", exc)
        return {}


def egress_redaction_enabled() -> bool:
    """Return whether Second Brain LLM prompts should be PII-masked before egress."""
    raw = os.environ.get(_TOGGLE_ENV)
    if raw is not None:
        val = raw.strip().lower()
        if val in _TRUE:
            return True
        if val in _FALSE:
            return False
    cfg = _load_redaction_config()
    sb = cfg.get("second_brain") or {}
    return bool(sb.get("redact_llm_egress", True))


def _platform_fail_closed() -> bool:
    return bool(_load_redaction_config().get("fail_closed", False))


def redact_for_llm(text: str, impact_level: str = "IL4") -> str:
    """Mask PII in *text* before it is sent to an LLM.

    When the toggle is off, returns *text* unchanged. When on, runs the shared
    :class:`RedactionAnonymizer`. On anonymizer failure, honors the platform
    ``fail_closed`` flag (raise vs. log-and-passthrough).
    """
    if not text or not egress_redaction_enabled():
        return text
    try:
        from tools.redaction.anonymizer import RedactionAnonymizer
        result = RedactionAnonymizer().anonymize(text, impact_level=impact_level)
        return result.anonymized_text
    except Exception as exc:
        logger.warning("[second_brain] egress redaction unavailable: %s", exc)
        if _platform_fail_closed():
            raise EgressRedactionUnavailable(str(exc)) from exc
        return text
