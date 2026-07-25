#!/usr/bin/env python3
# CUI // SP-CTI
"""Contextual retrieval — Anthropic "contextual retrieval" pattern (rce-ctx-01).

Before a chunk is embedded, prepend a short (~50-100 token) context prefix that
situates the chunk within its source document (e.g. framework revision / control
family for NIST corpora). The EMBEDDING is then computed on the contextualized
text (prefix + original content) while the STORED / CITED ``content`` stays the
original chunk text — preserving citation integrity and stable dedup hashing.

Design invariants (all enforced by tests):
  * OPT-IN — behavior is gated by ``rag.contextual_retrieval.enabled`` in
    ``args/rag_config.yaml``, DEFAULT FALSE. When off, this module is a no-op and
    the ingestion pipeline behaves exactly as before.
  * AIR-GAP SAFE — the LLM prefix is generated through the LLM router
    (``rag_evaluate`` cheap-tier function by default; NEVER a hardcoded model
    ID). If the router / provider is unavailable it returns "" gracefully, and
    an optional deterministic heuristic fallback (built from source_type /
    metadata) keeps the feature useful with no LLM.
  * TRUST — the prefix is LLM-generated content entering the index, so
    provenance (generator, prompt version, method, timestamp) is persisted in
    chunk metadata alongside the stored prefix.

Usage (library):
    from tools.rag.contextual_retrieval import contextualize_chunk, is_enabled
    if is_enabled():
        contextualize_chunk(chunk, document_text)

CLI (re-index existing corpora) is provided by ``tools/rag/reindex_contextual.py``.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional

BASE_DIR = Path(__file__).resolve().parent.parent.parent

# ---------------------------------------------------------------------------
# Module-level fallback constants — all overridable from args/rag_config.yaml
# under rag.contextual_retrieval.  Change config, not code.
# ---------------------------------------------------------------------------
_DEFAULT_FUNCTION = "rag_evaluate"   # cheap-tier router function (no hardcoded model)
_DEFAULT_MAX_TOKENS = 120            # small budget — a prefix is 50-100 tokens
_DEFAULT_TOKEN_BUDGET = 100          # cap the returned prefix to ~this many tokens
_DEFAULT_PROMPT_VERSION = "ctx-v1"   # bump when the prompt template changes
_CHARS_PER_TOKEN = 4                 # coarse estimate, matches chunker.py

_SYSTEM_PROMPT = (
    "You situate a text chunk within its source document for retrieval. "
    "Given the whole document and one chunk, write a single short standalone "
    "context sentence (50-100 tokens) that states what the chunk is about and "
    "where it sits in the document (e.g. framework revision, control family, "
    "section). Output ONLY the context sentence — no preamble, no quotes."
)

_USER_TEMPLATE = (
    "<document>\n{document}\n</document>\n\n"
    "Here is the chunk to situate within the document:\n"
    "<chunk>\n{chunk}\n</chunk>\n\n"
    "Give the short standalone context to prepend to this chunk."
)

# how much of the (possibly large) document to send to the LLM
_MAX_DOC_CHARS = 6000


def _load_config() -> dict:
    """Load ``rag.contextual_retrieval`` config from args/rag_config.yaml.

    Returns an empty dict on any error (treated as disabled / defaults).
    """
    config_path = BASE_DIR / "args" / "rag_config.yaml"
    if not config_path.exists():
        return {}
    try:
        import yaml

        with open(config_path, "r", encoding="utf-8") as f:
            cfg = yaml.safe_load(f) or {}
        return cfg.get("rag", {}).get("contextual_retrieval", {}) or {}
    except Exception:
        return {}


def is_enabled(config: Optional[dict] = None) -> bool:
    """True when contextual retrieval is enabled (DEFAULT FALSE)."""
    cfg = config if config is not None else _load_config()
    return bool(cfg.get("enabled", False))


def _cap_to_budget(text: str, token_budget: int) -> str:
    """Trim a prefix to roughly ``token_budget`` tokens (char-estimate)."""
    text = " ".join(text.split())  # normalize whitespace
    max_chars = max(1, token_budget) * _CHARS_PER_TOKEN
    if len(text) <= max_chars:
        return text
    return text[:max_chars].rsplit(" ", 1)[0].strip()


def _heuristic_prefix(
    source_type: str = "",
    metadata: Optional[Dict[str, Any]] = None,
    token_budget: int = _DEFAULT_TOKEN_BUDGET,
) -> str:
    """Deterministic, no-LLM context prefix built from source_type / metadata.

    Air-gap safe: produces a useful (if terse) situating sentence without any
    model call. Returns "" when there is nothing meaningful to say.
    """
    metadata = metadata or {}
    bits = []
    if source_type:
        bits.append(f"Source: {str(source_type).replace('_', ' ')}")
    # Surface a few common, human-meaningful metadata keys when present.
    for key in ("framework", "revision", "control_family", "control_id",
                "family", "section", "title", "doc_type", "standard"):
        val = metadata.get(key)
        if val is not None and str(val).strip():
            bits.append(f"{key.replace('_', ' ')}: {str(val).strip()}")
    if not bits:
        return ""
    prefix = "This chunk is from " + "; ".join(bits) + "."
    return _cap_to_budget(prefix, token_budget)


def _llm_prefix(document_text: str, chunk_text: str, config: dict) -> str:
    """Generate a context prefix via the LLM router. Returns "" on any failure."""
    function = config.get("function", _DEFAULT_FUNCTION)
    max_tokens = int(config.get("max_tokens", _DEFAULT_MAX_TOKENS))
    doc = (document_text or "")[:_MAX_DOC_CHARS]
    try:
        from tools.llm.router import LLMRouter
        from tools.llm.provider import LLMRequest

        router = LLMRouter()
        request = LLMRequest(
            messages=[{
                "role": "user",
                "content": _USER_TEMPLATE.format(document=doc, chunk=chunk_text),
            }],
            system_prompt=_SYSTEM_PROMPT,
            max_tokens=max_tokens,
            temperature=0.0,
            classification="CUI",
        )
        response = router.invoke(function, request)
        return (response.content or "").strip()
    except Exception:
        return ""


def generate_context_prefix(
    document_text: str,
    chunk_text: str,
    config: Optional[dict] = None,
    source_type: str = "",
    metadata: Optional[Dict[str, Any]] = None,
) -> str:
    """Generate a context prefix for ``chunk_text`` within ``document_text``.

    Returns "" gracefully when disabled or when neither the LLM nor the
    heuristic can produce anything. ``config`` may be injected for tests.

    Precedence: LLM (if enabled and reachable) -> heuristic fallback (if
    ``fallback_heuristic``) -> "".
    """
    prefix, _ = generate_context_prefix_with_provenance(
        document_text, chunk_text, config=config,
        source_type=source_type, metadata=metadata,
    )
    return prefix


def generate_context_prefix_with_provenance(
    document_text: str,
    chunk_text: str,
    config: Optional[dict] = None,
    source_type: str = "",
    metadata: Optional[Dict[str, Any]] = None,
) -> "tuple[str, dict]":
    """Like :func:`generate_context_prefix` but also returns provenance.

    Returns ``(prefix, provenance)``. ``provenance`` records the method
    ("llm" / "heuristic" / "none"), generator function, prompt version and a
    UTC timestamp — persisted alongside the prefix for TRUST auditing.
    """
    cfg = config if config is not None else _load_config()
    prompt_version = cfg.get("prompt_version", _DEFAULT_PROMPT_VERSION)
    token_budget = int(cfg.get("token_budget", _DEFAULT_TOKEN_BUDGET))

    def _prov(method: str, generator: str) -> dict:
        return {
            "method": method,
            "generator": generator,
            "prompt_version": prompt_version,
            "generated_at": datetime.now(timezone.utc).isoformat(),
        }

    if not is_enabled(cfg):
        return "", _prov("none", "disabled")

    if not chunk_text or not chunk_text.strip():
        return "", _prov("none", "empty_chunk")

    # 1) LLM-generated prefix (cheap-tier function, provider abstraction).
    prefix = _llm_prefix(document_text, chunk_text, cfg)
    if prefix:
        return _cap_to_budget(prefix, token_budget), _prov(
            "llm", cfg.get("function", _DEFAULT_FUNCTION)
        )

    # 2) Deterministic heuristic fallback (air-gap safe), if enabled.
    if cfg.get("fallback_heuristic", True):
        heur = _heuristic_prefix(source_type=source_type, metadata=metadata,
                                 token_budget=token_budget)
        if heur:
            return heur, _prov("heuristic", "source_type+metadata")

    return "", _prov("none", "unavailable")


def contextualize_chunk(
    chunk,
    document_text: str,
    config: Optional[dict] = None,
) -> bool:
    """Apply a context prefix to ``chunk`` in place (used at ingestion).

    Sets ``chunk.context_prefix`` and ``chunk.embed_text`` (prefix + original
    content) so the embedding is computed on contextualized text while the
    stored ``content`` — and its ``content_hash`` — stay original. Persists the
    prefix and provenance into ``chunk.metadata`` for TRUST auditing.

    Returns True when a non-empty prefix was applied, False otherwise (in which
    case the chunk is left untouched and embeds on its original content).
    """
    cfg = config if config is not None else _load_config()
    if not is_enabled(cfg):
        return False

    prefix, provenance = generate_context_prefix_with_provenance(
        document_text=document_text,
        chunk_text=chunk.content,
        config=cfg,
        source_type=getattr(chunk, "source_type", ""),
        metadata=getattr(chunk, "metadata", {}) or {},
    )
    if not prefix:
        return False

    chunk.context_prefix = prefix
    chunk.embed_text = f"{prefix}\n\n{chunk.content}"
    if chunk.metadata is None:
        chunk.metadata = {}
    chunk.metadata["context_prefix"] = prefix
    chunk.metadata["context_provenance"] = provenance
    return True
