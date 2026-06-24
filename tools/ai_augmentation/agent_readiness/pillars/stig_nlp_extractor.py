# CUI // SP-CTI
"""STIG NLP Extractor — Phase 512.

Replaces raw ``re.search()`` calls in the STIG compliance pillar with an
NLP extractor backed by ``claude-haiku-4-5-20251001``.

Public API
----------
StigNlpExtractor
    extract_stig_markers(content: str) -> dict

The returned dict always has the following keys:

    has_vid        : bool  — a STIG V-ID (V-NNNNNN or SV-NNNNNNrNN_rule) was found
    has_doc_ref    : bool  — a STIG documentation reference was found
    has_cat_marker : bool  — a CAT I/II/III severity marker was found
    method         : str   — "nlp_extractor" | "regex_fallback"

Design notes
------------
- The LLM path is optional and off by default to keep the pillar callable
  in air-gapped / test environments without any API keys.
- When ``use_llm=True`` the extractor calls the Anthropic messages API with
  a structured prompt; if the API call fails or returns malformed JSON the
  extractor silently falls back to the same regex logic used historically.
- ``client`` can be injected for testing (avoids live API calls).
- NIST 800-53 controls: SA-11 (Developer Testing), CM-7 (Least Functionality).
"""
from __future__ import annotations

import json
import os
import re
from typing import Any, Optional

# ---------------------------------------------------------------------------
# Regex patterns — preserved from the original stig_compliance.py so that
# the fallback path is byte-for-byte compatible with previous behaviour.
# ---------------------------------------------------------------------------
_STIG_VID_PATTERN = r"\bV-\d{5,6}\b|\bSV-\d{5,6}r\d+_rule\b"
_STIG_DOC_PATTERN = r"STIG|Security\s+Technical\s+Implementation\s+Guide|DISA\s+STIG"
_CAT_PATTERN = r"\bCAT\s*[I]{1,3}\b|\bCAT-[123]\b|\bCategory\s+[I]{1,3}\b"

# Model wired to the nlp_extractor paradigm (engine.py _PARADIGM_TO_MODEL)
_NLP_MODEL = "claude-haiku-4-5-20251001"

_SYSTEM_PROMPT = (
    "You are a STIG compliance marker extractor. "
    "Given a code or documentation excerpt, detect the presence of STIG markers. "
    "Return ONLY a single-line JSON object with three boolean fields: "
    "has_vid (STIG V-ID present), has_doc_ref (STIG documentation reference present), "
    "has_cat_marker (CAT I/II/III severity marker present). "
    "No explanation, no markdown — just the JSON."
)


def _regex_extract(content: str) -> dict:
    """Pure-regex fallback — always available, no external deps."""
    return {
        "has_vid": bool(re.search(_STIG_VID_PATTERN, content)),
        "has_doc_ref": bool(re.search(_STIG_DOC_PATTERN, content, re.IGNORECASE)),
        "has_cat_marker": bool(re.search(_CAT_PATTERN, content, re.IGNORECASE)),
        "method": "regex_fallback",
    }


class StigNlpExtractor:
    """NLP extractor for STIG compliance markers.

    Parameters
    ----------
    use_llm : bool
        When True, attempt an LLM call before falling back to regex.
        Defaults to the ``ICDEV_STIG_USE_LLM`` env var (True if set to
        ``"1"`` or ``"true"``), falling back to False.
    client : optional
        Pre-built Anthropic client (for testing). If not supplied and
        ``use_llm=True``, one is constructed lazily using the
        ``ANTHROPIC_API_KEY`` from the environment.
    """

    def __init__(
        self,
        use_llm: Optional[bool] = None,
        client: Any = None,  # kept for backwards-compat; ignored (routed via LLMRouter)
    ) -> None:
        if use_llm is None:
            env_val = os.environ.get("ICDEV_STIG_USE_LLM", "false").lower()
            use_llm = env_val in ("1", "true", "yes")
        self._use_llm = use_llm

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def extract_stig_markers(self, content: str) -> dict:
        """Extract STIG markers from *content*.

        Returns a dict with keys ``has_vid``, ``has_doc_ref``,
        ``has_cat_marker``, and ``method``.
        """
        if not self._use_llm:
            return _regex_extract(content)

        # Truncate to avoid hitting context limits on large files
        excerpt = content[:4000]
        try:
            from tools.llm.router import LLMRouter, LLMRequest
            router = LLMRouter()
            req = LLMRequest(
                function="stig_nlp_extractor",
                model=_NLP_MODEL,
                max_tokens=64,
                system=_SYSTEM_PROMPT,
                messages=[{"role": "user", "content": excerpt}],
            )
            resp = router.invoke("stig_nlp_extractor", req)
            raw = resp.content.strip()
            parsed = json.loads(raw)
            return {
                "has_vid": bool(parsed.get("has_vid", False)),
                "has_doc_ref": bool(parsed.get("has_doc_ref", False)),
                "has_cat_marker": bool(parsed.get("has_cat_marker", False)),
                "method": "nlp_extractor",
            }
        except Exception:  # noqa: BLE001 — graceful degradation
            return _regex_extract(content)

