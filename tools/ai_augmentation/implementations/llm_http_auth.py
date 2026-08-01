# CUI // SP-CTI
"""LLM-Configured HTTP Digest Auth — AAC Opportunity aac-opp-469.

AI Opportunity: hardcoded_threshold in requests/auth.py (line 260)
  Original: `self._thread_local.num_401_calls < 2`
  Pattern:  hardcoded_threshold → llm_generation
  Phase:    P3 — Long-Horizon Investments

The `requests` library hard-codes a maximum of 1 retry attempt for HTTP
Digest authentication (the literal `2` in `num_401_calls < 2`). This module
provides an ICDEV wrapper that makes that threshold configurable at
construction time, and optionally delegates threshold selection to an LLM
that reasons about the target endpoint's security profile.

Public API:
    LLMConfiguredDigestAuth(username, password, max_401_retries=1)
    advise_retry_threshold(endpoint_url, scan_context=None) -> int
"""

from __future__ import annotations

import re
from typing import Any

try:
    from requests.auth import HTTPDigestAuth
except ImportError:  # pragma: no cover
    HTTPDigestAuth = object  # type: ignore[assignment,misc]


# ── Configurable Digest Auth wrapper ─────────────────────────────────────────

class LLMConfiguredDigestAuth(HTTPDigestAuth):
    """HTTPDigestAuth with a configurable 401-retry threshold.

    Replaces the hardcoded ``num_401_calls < 2`` guard in
    ``requests.auth.HTTPDigestAuth.handle_401`` with a caller-supplied
    ``max_401_retries`` limit.  The default of 1 preserves the original
    library behaviour; callers may raise it for endpoints that require
    multi-round challenge-response flows.

    Args:
        username: HTTP Digest username.
        password: HTTP Digest password.
        max_401_retries: Maximum number of 401 retry attempts before
            giving up.  Must be ≥ 1.  Default 1 matches the upstream
            requests behaviour.
    """

    def __init__(self, username: str, password: str, max_401_retries: int = 1) -> None:
        super().__init__(username, password)
        if max_401_retries < 1:
            raise ValueError(f"max_401_retries must be >= 1, got {max_401_retries}")
        self.max_401_retries = max_401_retries

    def handle_401(self, r: Any, **kwargs: Any) -> Any:
        """Retry Digest auth up to ``max_401_retries`` times.

        Overrides the parent's hard-coded ``num_401_calls < 2`` check with
        ``num_401_calls <= self.max_401_retries``, allowing the threshold to
        be driven by runtime configuration or LLM advice.
        """
        if not 400 <= r.status_code < 500:
            self._thread_local.num_401_calls = 1
            return r

        if self._thread_local.pos is not None:
            r.request.body.seek(self._thread_local.pos)

        s_auth = r.headers.get("www-authenticate", "")

        if "digest" in s_auth.lower() and self._thread_local.num_401_calls <= self.max_401_retries:
            self._thread_local.num_401_calls += 1
            pat = re.compile(r"digest ", flags=re.IGNORECASE)

            try:
                from requests.utils import parse_dict_header
                from requests.cookies import extract_cookies_to_jar
            except ImportError:  # pragma: no cover
                self._thread_local.num_401_calls = 1
                return r

            self._thread_local.chal = parse_dict_header(pat.sub("", s_auth, count=1))

            r.content
            r.close()
            prep = r.request.copy()
            extract_cookies_to_jar(prep._cookies, r.request, r.raw)
            prep.prepare_cookies(prep._cookies)
            prep.headers["Authorization"] = self.build_digest_header(prep.method, prep.url)
            _r = r.connection.send(prep, **kwargs)
            _r.history.append(r)
            _r.request = prep
            return _r

        self._thread_local.num_401_calls = 1
        return r

    @classmethod
    def from_llm(
        cls,
        username: str,
        password: str,
        endpoint_url: str,
        scan_context: dict[str, Any] | None = None,
    ) -> "LLMConfiguredDigestAuth":
        """Construct with an LLM-advised retry threshold.

        Calls ``advise_retry_threshold`` to let an LLM reason about the
        appropriate retry count for the given endpoint, then returns an
        instance pre-configured with that value.  Falls back to the default
        threshold (1) if the LLM is unavailable or returns an invalid value.

        Args:
            username: HTTP Digest username.
            password: HTTP Digest password.
            endpoint_url: The API endpoint URL being authenticated against.
            scan_context: Optional AAC scan context dict (il_level, etc.).

        Returns:
            LLMConfiguredDigestAuth instance.
        """
        threshold = advise_retry_threshold(endpoint_url, scan_context)
        return cls(username, password, max_401_retries=threshold)


# ── LLM threshold advisor ─────────────────────────────────────────────────────

_DEFAULT_THRESHOLD = 1
_MAX_ALLOWED_RETRIES = 5  # safety cap — never let LLM exceed this


def advise_retry_threshold(
    endpoint_url: str,
    scan_context: dict[str, Any] | None = None,
) -> int:
    """Use an LLM to recommend an HTTP Digest Auth retry threshold.

    Examines the endpoint URL and optional scan context (IL level, auth
    scheme hints) and asks the configured LLM how many 401-retry attempts
    are appropriate.  Returns a safe integer in [1, _MAX_ALLOWED_RETRIES].

    Falls back to ``_DEFAULT_THRESHOLD`` (1, matching upstream behaviour)
    when no LLM is available or the response cannot be parsed.

    Args:
        endpoint_url: Target API endpoint URL.
        scan_context: Optional dict with keys such as ``il_level`` (e.g.
            "il4"), ``auth_scheme``, or ``environment``.

    Returns:
        Recommended max_401_retries value (int, 1–5).
    """
    prompt = _build_prompt(endpoint_url, scan_context or {})

    try:
        from tools.llm.router import LLMRouter
        from tools.llm.provider import LLMRequest
    except ImportError:
        return _DEFAULT_THRESHOLD

    try:
        router = LLMRouter()
        request = LLMRequest(
            messages=[{"role": "user", "content": prompt}],
            max_tokens=128,
            temperature=0.0,
        )
        response = router.invoke("llm_generation", request)
        return _parse_threshold(response.content)
    except Exception:  # LLMUnavailableError or any provider error
        return _DEFAULT_THRESHOLD


def _build_prompt(endpoint_url: str, context: dict[str, Any]) -> str:
    ctx_lines = "\n".join(f"  {k}: {v}" for k, v in context.items()) or "  (none)"
    return (
        "You are an HTTP security configuration advisor.\n\n"
        "The Python `requests` library hard-codes a maximum of 1 retry attempt "
        "for HTTP Digest authentication (the literal `2` in `num_401_calls < 2`).\n\n"
        f"Endpoint URL: {endpoint_url}\n"
        f"Context:\n{ctx_lines}\n\n"
        "How many 401-retry attempts should be allowed for this endpoint?\n"
        "Consider: multi-round challenge-response requirements, security risk of "
        "repeated auth attempts, and the endpoint's sensitivity.\n\n"
        "Reply with ONLY a single integer between 1 and 5. No explanation."
    )


def _parse_threshold(text: str) -> int:
    """Extract and clamp an integer from the LLM response."""
    m = re.search(r"\b([1-5])\b", text.strip())
    if m:
        return int(m.group(1))
    return _DEFAULT_THRESHOLD
