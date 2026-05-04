# CUI // SP-CTI
"""Input Sanitizer — validates and cleans task specs before the agent pipeline."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any


_BLOCKED_PATTERNS = [
    r"(?i)(rm\s+-rf|del\s+/[qsf]|format\s+[a-z]:)",  # destructive shell
    r"(?i)(curl|wget|fetch)\s+https?://",               # network exfil
    r"(?i)(os\.system|subprocess\.call)\s*\(",          # raw shell in prompts
    r"(?i)base64\s*\.b64decode",                         # encoded payloads
    r"(?i)(exec|eval)\s*\(",                             # code injection
]

_MAX_PROMPT_LEN = 8_000
_MAX_FILE_LINES = 500


@dataclass
class SanitizeResult:
    ok: bool
    prompt: str = ""
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)


class InputSanitizer:
    """Validates coding task prompts before they reach agents."""

    def sanitize(self, raw_prompt: str, context: dict[str, Any] | None = None) -> SanitizeResult:
        errors: list[str] = []
        warnings: list[str] = []

        if not isinstance(raw_prompt, str) or not raw_prompt.strip():
            return SanitizeResult(ok=False, errors=["Prompt must be a non-empty string"])

        prompt = raw_prompt.strip()

        if len(prompt) > _MAX_PROMPT_LEN:
            warnings.append(f"Prompt truncated from {len(prompt)} to {_MAX_PROMPT_LEN} chars")
            prompt = prompt[:_MAX_PROMPT_LEN]

        for pat in _BLOCKED_PATTERNS:
            if re.search(pat, prompt):
                errors.append(f"Blocked pattern detected: {pat}")

        if context:
            file_lines = context.get("file_lines", 0)
            if file_lines > _MAX_FILE_LINES:
                warnings.append(f"Context has {file_lines} lines; consider narrowing scope")

        if errors:
            return SanitizeResult(ok=False, errors=errors, warnings=warnings)

        return SanitizeResult(
            ok=True,
            prompt=prompt,
            warnings=warnings,
            metadata={"length": len(prompt), "context_keys": list((context or {}).keys())},
        )
