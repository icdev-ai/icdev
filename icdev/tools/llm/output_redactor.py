# CUI // SP-CTI
"""
Output redactor for agent loop results — IL4/IL5 compliance.

Scans final_content and stored text for PII and credential patterns before
they are persisted to ace_sessions, agent_evals, or returned to callers.
Unlike tool_result_sanitizer (which guards against prompt INJECTION on tool
INPUTS), this module guards against leaking sensitive data on OUTPUT.

Configuration: args/llm_config.yaml -> output_redactor:
  enabled: true
  mode: redact              # redact | warn | off
  patterns: []              # additional user-defined patterns
  placeholder: "[REDACTED]" # replacement text
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Literal

from tools.logging.icdev_logger import get_logger

logger = get_logger(__name__)

RedactorMode = Literal["redact", "warn", "off"]

_PLACEHOLDER = "[REDACTED]"

# ---------------------------------------------------------------------------
# PII and credential patterns
# ---------------------------------------------------------------------------
_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    # Credentials / secrets
    ("api_key_generic",    re.compile(r'\b(sk|pk|rk|ak)-[A-Za-z0-9]{20,}\b')),
    ("aws_access_key",     re.compile(r'\bAKIA[0-9A-Z]{16}\b')),
    ("aws_secret",         re.compile(r'(?i)aws.{0,20}secret.{0,20}["\']?[A-Za-z0-9/+=]{40}\b')),
    ("github_token",       re.compile(r'\bghp_[A-Za-z0-9]{36}\b|\bgh[os]_[A-Za-z0-9]{36}\b')),
    ("openai_key",         re.compile(r'\bsk-[A-Za-z0-9]{48}\b')),
    ("anthropic_key",      re.compile(r'\bsk-ant-[A-Za-z0-9\-_]{80,}\b')),
    ("jwt_token",          re.compile(r'\beyJ[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{10,}\b')),
    ("private_key_block",  re.compile(r'-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----')),
    ("password_in_url",    re.compile(r'(?i)://[^:@/\s]+:[^@/\s]{3,}@')),
    # PII
    ("ssn",                re.compile(r'\b\d{3}-\d{2}-\d{4}\b')),
    ("credit_card",        re.compile(r'\b(?:4[0-9]{12}(?:[0-9]{3})?|5[1-5][0-9]{14}|3[47][0-9]{13}|6(?:011|5[0-9]{2})[0-9]{12})\b')),
    ("email",              re.compile(r'\b[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}\b')),
    ("us_phone",           re.compile(r'\b(?:\+1[-.\s]?)?\(?\d{3}\)?[-.\s]\d{3}[-.\s]\d{4}\b')),
    ("ip_address_private", re.compile(r'\b(?:10\.\d{1,3}\.\d{1,3}\.\d{1,3}|172\.(?:1[6-9]|2\d|3[01])\.\d{1,3}\.\d{1,3}|192\.168\.\d{1,3}\.\d{1,3})\b')),
    # DoD-specific
    ("dodid",              re.compile(r'\b\d{10}\b')),
    ("cui_banner",         re.compile(r'CUI//(?:SP-)?[A-Z]{2,}', re.IGNORECASE)),
]


@dataclass
class RedactResult:
    original_text: str
    redacted_text: str
    flagged: bool
    pattern_hits: list[str] = field(default_factory=list)
    mode: RedactorMode = "redact"

    @property
    def changed(self) -> bool:
        return self.redacted_text != self.original_text


def redact(
    text: str,
    *,
    mode: RedactorMode = "redact",
    placeholder: str = _PLACEHOLDER,
    extra_patterns: list[tuple[str, re.Pattern[str]]] | None = None,
    skip_patterns: list[str] | None = None,
) -> RedactResult:
    """
    Scan and optionally redact sensitive content from text.

    Parameters
    ----------
    text : str
        Content to scan (final_content, stored message text, etc.).
    mode : 'redact' | 'warn' | 'off'
        - off    : return text unchanged, no scanning
        - warn   : log hits but return original text
        - redact : replace matched fragments with placeholder
    placeholder : str
        Replacement text in redact mode. Default "[REDACTED]".
    extra_patterns : list of (name, Pattern)
        Additional patterns to apply beyond the core set.
    skip_patterns : list of str
        Pattern names from the core set to skip.

    Returns
    -------
    RedactResult -- never raises.
    """
    try:
        if mode == "off" or not text:
            return RedactResult(
                original_text=text, redacted_text=text, flagged=False, mode=mode
            )

        skip_set = set(skip_patterns or [])
        patterns = [(n, p) for n, p in _PATTERNS if n not in skip_set]
        if extra_patterns:
            patterns += extra_patterns

        hits: list[str] = []
        result_text = text

        for name, pattern in patterns:
            if pattern.search(result_text):
                hits.append(name)
                if mode == "redact":
                    result_text = pattern.sub(placeholder, result_text)

        if hits:
            logger.warning(
                "output_redactor: %d pattern(s) matched (mode=%s): %s",
                len(hits), mode, ", ".join(hits),
            )

        return RedactResult(
            original_text=text,
            redacted_text=result_text,
            flagged=bool(hits),
            pattern_hits=hits,
            mode=mode,
        )

    except Exception:
        logger.debug("output_redactor: unexpected error (non-fatal)", exc_info=True)
        return RedactResult(original_text=text, redacted_text=text, flagged=False, mode=mode)


def load_config() -> dict:
    """Load redactor config from args/llm_config.yaml; return defaults on error."""
    defaults: dict = {"enabled": True, "mode": "redact", "placeholder": _PLACEHOLDER, "patterns": []}
    try:
        import yaml
        from pathlib import Path
        cfg_path = Path(__file__).resolve().parents[3] / "args" / "llm_config.yaml"
        if not cfg_path.exists():
            return defaults
        data = yaml.safe_load(cfg_path.read_text(encoding="utf-8")) or {}
        section = data.get("output_redactor", {})
        return {**defaults, **section}
    except Exception:
        return defaults
