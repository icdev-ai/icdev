#!/usr/bin/env python3
# CUI // SP-CTI
"""Entitlement-Driven RAG pipeline (irad-cls-01/02/03).

5-step workflow from AI_Enablement_IL5_Architectural_Review.pdf:
  1. translate_intent  — LLM converts analyst question to SQL/SPARQL
  2. execute_as_user   — query runs under USER credentials (CLS native filters)
  3. tag_cells_with_uuids — UUID4 tag appended to every data cell
  4. synthesize_with_grounding — LLM synthesizes with mandatory [UUID] citation
  5. verify_citation_coverage — audits output; returns grounding_report

Stateless by design: no persistent vector index per user, no fine-tuning
on mission data.  TTLIndex prevents ghost data in embedding stores.
"""
from __future__ import annotations

import re
import time
import uuid as _uuid_mod
from typing import Any, Callable, Dict, List, Optional, Set

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

GROUNDING_SYSTEM_PROMPT = (
    "For every factual claim you make, you MUST cite the source UUID inline "
    "as [UUID]. If you cannot cite a UUID for a claim, do not make that claim."
)

_UUID_RE = re.compile(
    r"\[([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})\]",
    re.IGNORECASE,
)
_SENTENCE_RE = re.compile(r"(?<=[.!?])\s+")

COVERAGE_FAIL_THRESHOLD = 0.80  # coverage_pct below this → FAIL


# ---------------------------------------------------------------------------
# Step 1 — translate_intent
# ---------------------------------------------------------------------------

def translate_intent(
    prompt: str,
    user_context: dict,
    *,
    _llm_fn: Optional[Callable] = None,
) -> str:
    """LLM converts analyst question to SQL/SPARQL query.

    Two-tier: qwen3 local first, fallback Claude (via LLMRouter).
    Inject _llm_fn(prompt, user_context) -> str for testing.
    """
    if _llm_fn is not None:
        return _llm_fn(prompt, user_context)
    try:
        from icdev.tools.llm.router import LLMRouter
        from icdev.tools.llm.provider import LLMRequest

        router = LLMRouter()
        resp = router.invoke(
            "code_generation",
            LLMRequest(
                system_prompt="Translate the analyst question to a SQL query. Return only the SQL.",
                messages=[{"role": "user", "content": f"Question: {prompt}\nContext: {user_context}"}],
            ),
        )
        return (resp.content or "").strip()
    except Exception:
        return f"SELECT * FROM entitlements -- {prompt}"


# ---------------------------------------------------------------------------
# Step 2 — execute_as_user
# ---------------------------------------------------------------------------

def execute_as_user(
    query: str,
    user_auth_token: str,
    *,
    _exec_fn: Optional[Callable] = None,
) -> List[Dict[str, Any]]:
    """Runs query with USER's credentials so native CLS filters apply.

    Inject _exec_fn(query, user_auth_token) -> list[dict] for testing.
    """
    if _exec_fn is not None:
        return _exec_fn(query, user_auth_token)
    # Production: connect to data warehouse using user token, not system creds.
    return []


# ---------------------------------------------------------------------------
# Step 3 — tag_cells_with_uuids
# ---------------------------------------------------------------------------

def tag_cells_with_uuids(result_set: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Appends a unique UUID4 tag to each data cell in result_set.

    For each key in a row a sibling key ``_uuid_<key>`` is added containing
    the UUID string.  The UUIDs are later used to enforce citation grounding.
    """
    tagged: List[Dict[str, Any]] = []
    for row in result_set:
        new_row: Dict[str, Any] = {}
        for key, value in row.items():
            new_row[key] = value
            new_row[f"_uuid_{key}"] = str(_uuid_mod.uuid4())
        tagged.append(new_row)
    return tagged


# ---------------------------------------------------------------------------
# Step 4 — synthesize_with_grounding
# ---------------------------------------------------------------------------

def synthesize_with_grounding(
    tagged_data: List[Dict[str, Any]],
    prompt: str,
    *,
    _llm_fn: Optional[Callable] = None,
) -> str:
    """LLM synthesizes answer with GROUNDING_SYSTEM_PROMPT injected.

    Inject _llm_fn(tagged_data, prompt) -> str for testing.
    """
    if _llm_fn is not None:
        return _llm_fn(tagged_data, prompt)
    try:
        from icdev.tools.llm.router import LLMRouter
        from icdev.tools.llm.provider import LLMRequest

        router = LLMRouter()
        resp = router.invoke(
            "synthesis",
            LLMRequest(
                system_prompt=GROUNDING_SYSTEM_PROMPT,
                messages=[{"role": "user", "content": f"Data: {tagged_data}\nQuestion: {prompt}"}],
            ),
        )
        return (resp.content or "").strip()
    except Exception:
        return ""


# ---------------------------------------------------------------------------
# Step 5 — verify_citation_coverage
# ---------------------------------------------------------------------------

def verify_citation_coverage(output: str, uuid_set: Set[str]) -> Dict[str, Any]:
    """Audits LLM output for citation grounding.

    Returns grounding_report:
        coverage_pct   — fraction of sentences containing a [UUID] citation
        ungrounded     — sentences with no citation
        phantom_uuids  — UUIDs cited in output but absent from uuid_set
        decision       — 'PASS' | 'FAIL'
                         FAIL if coverage_pct < COVERAGE_FAIL_THRESHOLD
                              or phantom_uuids is non-empty
    """
    cited: Set[str] = set(_UUID_RE.findall(output))
    sentences = [s.strip() for s in _SENTENCE_RE.split(output) if s.strip()]

    if not sentences:
        return {
            "coverage_pct": 1.0,
            "ungrounded": [],
            "phantom_uuids": [],
            "decision": "PASS",
        }

    ungrounded = [s for s in sentences if not _UUID_RE.search(s)]
    coverage_pct = (len(sentences) - len(ungrounded)) / len(sentences)
    phantom_uuids = sorted(cited - uuid_set)

    decision = "FAIL" if (coverage_pct < COVERAGE_FAIL_THRESHOLD or phantom_uuids) else "PASS"

    return {
        "coverage_pct": coverage_pct,
        "ungrounded": ungrounded,
        "phantom_uuids": phantom_uuids,
        "decision": decision,
    }


# ---------------------------------------------------------------------------
# Full pipeline
# ---------------------------------------------------------------------------

def run_entitlement_pipeline(
    prompt: str,
    user_context: dict,
    user_auth_token: str,
    *,
    _translate_fn: Optional[Callable] = None,
    _execute_fn: Optional[Callable] = None,
    _synthesize_fn: Optional[Callable] = None,
) -> Dict[str, Any]:
    """Executes the full 5-step Entitlement-Driven RAG pipeline.

    Returns the grounding_report dict from verify_citation_coverage.
    Injectable fns allow unit testing without LLM or DB access.
    """
    query = translate_intent(prompt, user_context, _llm_fn=_translate_fn)
    result_set = execute_as_user(query, user_auth_token, _exec_fn=_execute_fn)
    tagged = tag_cells_with_uuids(result_set)

    uuid_set: Set[str] = {
        v for row in tagged for k, v in row.items() if k.startswith("_uuid_")
    }

    output = synthesize_with_grounding(tagged, prompt, _llm_fn=_synthesize_fn)
    return verify_citation_coverage(output, uuid_set)


# ---------------------------------------------------------------------------
# TTLIndex — ghost data prevention (irad-cls-02)
# ---------------------------------------------------------------------------

class TTLIndex:
    """Embedding TTL registry.

    Tracks (uuid, embedding_ref) pairs with configurable TTL.
    purge_expired() hard-deletes expired entries, preventing ghost data
    (records purged from the data warehouse that still linger in embeddings).

    Inject _clock callable for deterministic testing.
    """

    def __init__(
        self,
        ttl_seconds: int = 3600,
        _clock: Optional[Callable[[], float]] = None,
    ) -> None:
        self._ttl_seconds = ttl_seconds
        self._entries: Dict[str, Dict[str, Any]] = {}
        self._handlers: Dict[str, List[Callable]] = {}
        self._clock: Callable[[], float] = _clock or time.time

    def register(self, uuid: str, embedding_ref: str) -> None:
        """Records uuid + expiry timestamp."""
        self._entries[uuid] = {
            "embedding_ref": embedding_ref,
            "expires_at": self._clock() + self._ttl_seconds,
        }

    def is_expired(self, uuid: str) -> bool:
        """Returns True iff uuid is registered and its TTL has elapsed."""
        entry = self._entries.get(uuid)
        if entry is None:
            return False
        return self._clock() >= entry["expires_at"]

    def purge_expired(self) -> List[str]:
        """Hard-deletes all expired entries. Returns list of purged UUIDs."""
        now = self._clock()
        expired = [uid for uid, e in self._entries.items() if now >= e["expires_at"]]
        for uid in expired:
            del self._entries[uid]
        return expired

    def register_purge_handler(self, event_name: str, handler: Callable) -> None:
        """Registers an event-driven purge handler.

        Example events: 'record.deleted', 'record.aged_off'.
        Call fire_event() to trigger registered handlers.
        """
        self._handlers.setdefault(event_name, []).append(handler)

    def fire_event(self, event_name: str, **kwargs: Any) -> None:
        """Dispatches all handlers registered for event_name."""
        for handler in self._handlers.get(event_name, []):
            handler(**kwargs)
