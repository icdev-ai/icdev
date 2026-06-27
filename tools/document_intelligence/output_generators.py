# CUI // SP-CTI
"""DIC Output Generators — study guide, FAQ, timeline, audio overview.

Dual-mode design:
  Air-gap: deterministic extraction (keyword-based, no LLM) + local TTS (pyttsx3/espeak).
  Online:  LLMRouter synthesis (Anthropic/OpenAI/Ollama fallback chain) + cloud TTS optional.

All generators degrade gracefully — the air-gap path always produces usable output,
the online path produces richer, more coherent output.

Capabilities exercised:
  - filesystem: persists generated outputs to dic_generated_outputs via DB + audio to data/dic_audio/.
  - network_egress: LLMRouter may call cloud LLM APIs (Anthropic/OpenAI) when API keys are set.
"""
from __future__ import annotations

import html as _html
import json
import re
import uuid
from datetime import datetime, timezone
from typing import Any

from tools.logging.icdev_logger import get_logger

logger = get_logger(__name__)

# ── DB persistence ────────────────────────────────────────────────────────────

def _conn():
    from tools.db.storage import get_connection
    return get_connection()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _save_output(output_type: str, collection_id: str, tenant_id: str,
                 content: dict, provider: str) -> str:
    output_id = f"dicout-{uuid.uuid4().hex[:12]}"
    try:
        conn = _conn()
        conn.execute(
            """INSERT INTO dic_generated_outputs
               (id, output_type, collection_id, tenant_id, content_json, provider,
                status, created_at, classification)
               VALUES (%s,%s,%s,%s,%s,%s,'done',%s,%s)""",
            (output_id, output_type, collection_id, tenant_id,
             json.dumps(content), provider, _now(), "CUI"),
        )
        conn.commit()
        conn.close()
    except Exception as exc:
        logger.warning("dic.output_generators: save error: %s", exc)
    return output_id


# ── RAG chunk retrieval ────────────────────────────────────────────────────────

def _get_ranked_chunks(collection_id: str, tenant_id: str, query: str,
                       limit: int = 30, doc_id: str | None = None) -> list[dict]:
    """BM25-ranked chunk retrieval via DICSearchEngine (10s timeout, falls back to sequential).

    Uses the existing RAG+KG search engine to surface the most relevant chunks
    for a given query, then maps results to the standard chunk dict format used
    by all generators.  Falls back to sequential retrieval on any error or timeout.
    """
    import concurrent.futures as _cf

    def _do_search():
        from tools.document_intelligence.search_engine import DICSearchEngine
        engine = DICSearchEngine(tenant_id=tenant_id)
        return engine.search(
            query=query,
            collection_id=collection_id,
            top_k=limit * 2 if doc_id else limit,
            mode="grounded",
        )

    try:
        _ex2 = _cf.ThreadPoolExecutor(max_workers=1)
        _fut2 = _ex2.submit(_do_search)
        _ex2.shutdown(wait=False)
        results = _fut2.result(timeout=10)  # 10s ceiling — falls back if vector store hangs
        if results:
            chunks = []
            for r in results:
                if doc_id and getattr(r, "doc_id", None) != doc_id:
                    continue
                chunks.append({
                    "chunk_text": getattr(r, "content", "") or "",
                    "source_doc": getattr(r, "doc_id", ""),
                    "page_number": getattr(r, "page", None),
                    "chunk_index": 0,
                    "score": getattr(r, "score", 0.0),
                })
                if len(chunks) >= limit:
                    break
            if chunks:
                return chunks
    except Exception as exc:  # includes concurrent.futures.TimeoutError
        logger.debug("dic.output_generators: ranked retrieval error/timeout: %s", exc)
    return _get_chunks(collection_id, tenant_id, limit=limit, doc_id=doc_id)


def _get_chunks(collection_id: str, tenant_id: str, limit: int = 200,
                doc_id: str | None = None) -> list[dict]:
    """Retrieve raw text chunks for a collection via dic_chunk_links → rag_chunks.

    If ``doc_id`` is provided, only chunks from that specific document are returned
    so that generated outputs (FAQ, study guide, etc.) are scoped to it.
    """
    try:
        conn = _conn()
        if doc_id:
            rows = conn.execute(
                """SELECT rc.content   AS chunk_text,
                          dcl.doc_id  AS source_doc,
                          dcl.page    AS page_number,
                          dcl.chunk_index
                   FROM dic_chunk_links dcl
                   JOIN rag_chunks rc ON rc.id = dcl.rag_chunk_id
                   JOIN dic_documents dd ON dd.doc_id = dcl.doc_id
                   WHERE dd.collection_id = %s AND dd.tenant_id = %s AND dcl.doc_id = %s
                   ORDER BY dcl.chunk_index
                   LIMIT %s""",
                (collection_id, tenant_id, doc_id, limit),
            ).fetchall()
        else:
            rows = conn.execute(
                """SELECT rc.content   AS chunk_text,
                          dcl.doc_id  AS source_doc,
                          dcl.page    AS page_number,
                          dcl.chunk_index
                   FROM dic_chunk_links dcl
                   JOIN rag_chunks rc ON rc.id = dcl.rag_chunk_id
                   JOIN dic_documents dd ON dd.doc_id = dcl.doc_id
                   WHERE dd.collection_id = %s AND dd.tenant_id = %s
                   ORDER BY dcl.doc_id, dcl.chunk_index
                   LIMIT %s""",
                (collection_id, tenant_id, limit),
            ).fetchall()
        conn.close()
        return [dict(r) if hasattr(r, "keys") else {
            "chunk_text": r[0], "source_doc": r[1],
            "page_number": r[2], "chunk_index": r[3],
        } for r in rows]
    except Exception as exc:
        logger.warning("dic.output_generators: chunk retrieval error: %s", exc)
        return []


def _corpus_text(chunks: list[dict], max_chars: int = 12_000) -> str:
    parts = []
    total = 0
    for c in chunks:
        t = _html.unescape((c.get("chunk_text") or "").strip())
        if not t:
            continue
        total += len(t)
        parts.append(t)
        if total >= max_chars:
            break
    return "\n\n".join(parts)


# ── LLM helper ────────────────────────────────────────────────────────────────

def _has_cloud_keys() -> bool:
    """Return True if any cloud LLM API key is present in the environment."""
    import os
    return bool(
        os.environ.get("ANTHROPIC_API_KEY")
        or os.environ.get("OPENAI_API_KEY")
        or os.environ.get("AZURE_OPENAI_API_KEY")
    )


def _try_llm(prompt: str, function: str = "document_qna",
             max_seconds: int = 90) -> tuple[str | None, str]:
    """Invoke LLMRouter for synthesis, falling back to deterministic on timeout.

    Hard ceiling: max_seconds (default 90) — falls back to deterministic after.
    Priority:
      1. Cloud API — when ANTHROPIC_API_KEY / OPENAI_API_KEY are set.
      2. Ollama    — last resort via router's Ollama entry.
    """
    import concurrent.futures as _cf
    _ex = _cf.ThreadPoolExecutor(max_workers=1)
    _fut = _ex.submit(_try_llm_inner, prompt, function)
    _ex.shutdown(wait=False)  # don't block here; thread runs to completion in bg
    try:
        return _fut.result(timeout=max_seconds)
    except (_cf.TimeoutError, Exception) as _exc:
        logger.debug("dic._try_llm: timed out or failed after %ds: %s", max_seconds, _exc)
        return None, "air-gap-deterministic"


def _try_llm_inner(prompt: str, function: str) -> tuple[str | None, str]:
    """Actual LLM call (cloud → Ollama) — runs inside a thread with outer timeout."""
    # ── 1. Cloud path (API key present) ──────────────────────────────────────
    if _has_cloud_keys():
        try:
            from tools.llm.provider import LLMRequest
            from tools.llm.router import LLMRouter
            router = LLMRouter()
            _, model, _ = router.get_provider_for_function(function)
            req = LLMRequest(
                messages=[{"role": "user", "content": prompt}],
                max_tokens=2048,
                temperature=0.3,
            )
            result = router.invoke(function, req)
            if isinstance(result, dict):
                text = (result.get("text") or result.get("content") or "").strip()
            elif hasattr(result, "content"):
                text = (result.content or "").strip()
            elif hasattr(result, "text"):
                text = (result.text or "").strip()
            else:
                text = str(result).strip()
            if text:
                return text, model or "cloud"
        except Exception as exc:
            logger.debug("dic._try_llm: cloud invoke failed (%s) — trying Ollama", exc)

    # ── 2. Ollama / last resort via router ────────────────────────────────────
    try:
        from tools.llm.provider import LLMRequest
        from tools.llm.router import LLMRouter
        router = LLMRouter()
        _, model, _ = router.get_provider_for_function(function)
        req = LLMRequest(
            messages=[{"role": "user", "content": prompt}],
            max_tokens=2048,
            temperature=0.3,
        )
        result = router.invoke(function, req)
        if isinstance(result, dict):
            text = (result.get("text") or result.get("content") or "").strip()
        elif hasattr(result, "content"):
            text = (result.content or "").strip()
        elif hasattr(result, "text"):
            text = (result.text or "").strip()
        else:
            text = str(result).strip()
        return text or None, model or "llm"
    except Exception as exc:
        logger.debug("dic.output_generators: LLM unavailable: %s", exc)
        return None, "air-gap-deterministic"


def _is_air_gap() -> bool:
    try:
        from tools.airgap import is_airgap
        return is_airgap()
    except Exception:
        import os
        return os.environ.get("ICDEV_AIRGAP", "").lower() in ("true", "1", "yes")


def _llm_available() -> bool:
    """True when any LLM provider is reachable (cloud keys, CLI binary, or Ollama)."""
    import os
    import shutil
    if os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("OPENAI_API_KEY"):
        return True
    if shutil.which(os.environ.get("ICDEV_CLI_BRIDGE_BINARY") or "claude"):
        return True
    try:
        import urllib.request
        urllib.request.urlopen("http://localhost:11434/api/tags", timeout=1)
        return True
    except Exception:
        return False


def _kg_entities_for_collection(collection_id: str, tenant_id: str,
                                 doc_id: str | None = None, limit: int = 20) -> list[str]:
    """Return top entity labels from KG nodes linked to this collection (by centrality)."""
    try:
        conn = _conn()
        if doc_id:
            rows = conn.execute(
                """SELECT DISTINCT n.label FROM kg_nodes n
                   JOIN kg_graphs g ON g.id = n.graph_id
                   WHERE g.source_doc_id = %s
                   ORDER BY n.centrality DESC NULLS LAST LIMIT %s""",
                (doc_id, limit),
            ).fetchall()
        else:
            rows = conn.execute(
                """SELECT DISTINCT n.label FROM kg_nodes n
                   JOIN kg_graphs g ON g.id = n.graph_id
                   JOIN dic_documents d ON d.doc_id = g.source_doc_id
                   WHERE d.collection_id = %s AND d.tenant_id = %s
                   ORDER BY n.centrality DESC NULLS LAST LIMIT %s""",
                (collection_id, tenant_id, limit),
            ).fetchall()
        conn.close()
        return [r[0] for r in rows if r[0]]
    except Exception as exc:
        logger.debug("dic.output_generators: KG entities error: %s", exc)
        return []


# ── Study Guide ───────────────────────────────────────────────────────────────

def generate_study_guide(collection_id: str, tenant_id: str = "default",
                         doc_id: str | None = None) -> dict:
    """Generate a structured study guide instantly via BM25+KG (no LLM).

    Returns immediately (<2s). The caller can invoke enhance_with_llm() on the
    returned output_id to layer narrative coherence on top.
    """
    chunks = _get_ranked_chunks(
        collection_id, tenant_id,
        query="key concepts main points important principles overview sections",
        limit=40, doc_id=doc_id,
    )
    if not chunks:
        chunks = _get_chunks(collection_id, tenant_id, limit=150, doc_id=doc_id)
    if not chunks:
        return {"error": "No documents found in collection.", "collection_id": collection_id}

    content = _deterministic_study_guide(chunks, collection_id, tenant_id, doc_id)
    content["provider"] = "bm25-kg"
    output_id = _save_output("study_guide", collection_id, tenant_id, content, "bm25-kg")
    content["output_id"] = output_id
    return content


def _deterministic_study_guide(chunks: list[dict], collection_id: str,
                               tenant_id: str = "default",
                               doc_id: str | None = None) -> dict:
    # Patterns indicating boilerplate / navigation text to skip
    _NAV_PATTERNS = re.compile(
        r"(jump to content|skip navigation|skip to content|skip to main|"
        r"main menu|navigation menu|move to sidebar|"
        r"personal tools|search\s+search|log in|create account|"
        r"donate|recent changes|upload file|special pages|"
        r"toggle\s+\w+\s+subsection|retrieved from|"
        r"wikipedia|wikimedia|this page was last|help:)", re.I
    )

    # Key points: meaningful sentences, skip bracket-heavy infobox and nav lines
    key_points: list[str] = []
    seen_sigs: set[str] = set()
    for c in chunks[:60]:
        text = _html.unescape((c.get("chunk_text") or "").strip())
        text = re.sub(r"\s+", " ", text)
        # Skip chunks that are predominantly navigation / boilerplate
        if _NAV_PATTERNS.search(text[:200]):
            continue
        for s in re.split(r"(?<=[.!?])\s+", text)[:3]:
            s = s.strip()
            # Skip short, citation-heavy, infobox, or nav lines
            if len(s) < 60 or len(re.findall(r"[#\[\]|]", s)) > 4:
                continue
            if _NAV_PATTERNS.search(s):
                continue
            sig = re.sub(r"\W", "", s[:30].lower())
            if sig not in seen_sigs:
                key_points.append(s)
                seen_sigs.add(sig)
        if len(key_points) >= 12:
            break

    # Key terms: prefer KG entities (high-quality), fall back to proper noun extraction
    kg_terms = _kg_entities_for_collection(collection_id, tenant_id, doc_id, limit=20)
    if kg_terms:
        top_terms = kg_terms[:15]
    else:
        # Extract proper nouns (CapitalizedWord or Multi Word Proper Noun)
        all_text = " ".join(_html.unescape(c.get("chunk_text", "")) for c in chunks)
        # Only exclude pure grammatical/structural connectors — do NOT exclude domain terms
        # (prior version excluded "Amendment", "Constitution", "Rights", etc. which are
        # exactly the most important terms for legal/constitutional documents)
        noise_caps = {
            "The", "This", "That", "These", "Those", "Such", "Any", "All",
            "Each", "No", "Not", "Its", "Their", "His", "Her", "Our",
            "Your", "And", "But", "For", "Nor", "Yet",
            "With", "From", "When", "Where", "Which", "What", "How", "Who",
            "Was", "Were", "Has", "Have", "Had", "Are", "Been", "Being",
            "Shall", "Will", "May", "Can", "Must", "Should", "Would", "Could",
        }
        freq: dict[str, int] = {}
        for m in re.finditer(r"\b([A-Z][a-z]{3,}(?:\s+[A-Z][a-z]{3,}){0,2})\b", all_text):
            term = m.group(1)
            if term not in noise_caps:
                freq[term] = freq.get(term, 0) + 1
        # Supplement with domain nouns (length ≥ 7, alpha, not stopwords)
        _SW = {
            "the", "a", "an", "is", "in", "of", "and", "to", "for", "with", "that",
            "this", "are", "be", "as", "at", "by", "or", "it", "its", "on", "not",
            "was", "were", "has", "have", "from", "but", "which", "who", "what",
            "when", "where", "how", "any", "all", "each", "shall", "will", "may",
            "can", "must", "should", "would", "could", "been", "they", "their",
            "them", "into", "than", "more", "also", "such", "said", "states",
            "united", "state", "other", "person", "persons", "you", "your",
            "things", "president", "congress", "section", "article", "rights",
            "powers", "court", "house", "senate", "federal", "national", "under",
            "people", "number", "without", "between", "against", "before", "after",
            "during", "within", "unless", "provided", "required", "including",
        }
        for w in re.findall(r"\b[a-z]{7,}\b", all_text.lower()):
            if w not in _SW:
                freq[w] = freq.get(w, 0) + 1
        top_terms = sorted(freq, key=lambda t: -freq[t])[:15]

    sources = list({c.get("source_doc", "unknown") for c in chunks})[:10]
    # Build a meaningful overview from document title or key points
    doc_titles = [s for s in sources if s and s != "unknown"]
    if doc_titles:
        title_str = ", ".join(doc_titles[:3])
        overview = (
            f"This study guide covers {title_str}. "
            f"It is based on {len(chunks)} extracted passages."
        )
    else:
        overview = (
            f"Study guide for collection '{collection_id}' — "
            f"{len(chunks)} passages across {len(sources)} document(s)."
        )
    return {
        "format": "bm25-kg",
        "collection_id": collection_id,
        "chunks_used": len(chunks),
        "overview": overview,
        "key_points": key_points,
        "key_terms": top_terms,
        "sources": sources,
        "llm_available": _llm_available(),
    }


# ── FAQ Generator ─────────────────────────────────────────────────────────────

def generate_faq(collection_id: str, tenant_id: str = "default", n: int = 10,
                 doc_id: str | None = None) -> dict:
    """Generate a FAQ instantly via BM25+KG (no LLM). Use enhance_with_llm() for richer Q&A."""
    chunks = _get_ranked_chunks(
        collection_id, tenant_id,
        query="questions answers what how why when who",
        limit=30, doc_id=doc_id,
    )
    if not chunks:
        chunks = _get_chunks(collection_id, tenant_id, limit=120, doc_id=doc_id)
    if not chunks:
        return {"error": "No documents found in collection.", "collection_id": collection_id}

    content = _deterministic_faq(chunks, collection_id, n)
    content["provider"] = "bm25-kg"
    output_id = _save_output("faq", collection_id, tenant_id, content, "bm25-kg")
    content["output_id"] = output_id
    return content


_FAQ_SKIP_STARTERS = frozenset([
    "but", "when", "if", "as", "since", "after", "before", "while",
    "a", "an", "resolved", "that", "these",
    "those", "such", "no", "not", "and", "or", "for", "nor", "so", "yet",
    "be", "is", "it", "he", "she", "they", "we", "you", "i", "any",
    "all", "each", "both", "neither", "either", "whenever", "wherever",
    "thereafter", "hereby", "thereof", "hereof", "therein", "herein",
    "whereas", "provided", "except", "unless", "until", "upon", "during",
    "section", "paragraph", "article",  # structural labels, not topics
])

# Trailing verb phrases that get captured in the topic match and should be stripped
_TRAILING_VERB_RE = re.compile(
    r"\s+(is|are|was|were|means|refers\s+to|defined\s+as|used\s+to|defines|establishes|provides)\s*$",
    re.I,
)


_FAQ_DEFN_VERB_RE = re.compile(
    r"\b(is|are|means|refers\s+to|defined\s+as|defines|establishes|provides)\b", re.I
)


def _deterministic_faq(chunks: list[dict], collection_id: str, n: int) -> dict:
    """Mine definition-like sentences and turn them into Q&A pairs.

    Extracts the topic as everything BEFORE the first definition verb
    ("Bill of Rights refers to..." → topic = "Bill of Rights").
    Filters out topics that start with conjunctions, procedural legalese, etc.
    """
    pairs = []
    seen_topics: set[str] = set()
    for c in chunks:
        text = (c.get("chunk_text") or "").strip()
        sentences = re.split(r"(?<=[.!?])\s+", text)
        for s in sentences:
            if len(s) < 60:
                continue
            verb_m = _FAQ_DEFN_VERB_RE.search(s)
            if not verb_m:
                continue
            # Topic = everything before the definition verb
            topic = s[:verb_m.start()].strip().rstrip(" ,;:")
            if not topic:
                continue
            # Strip leading "The " / "This " — keep the noun phrase
            topic = re.sub(r"^(The|This|A)\s+", "", topic)
            topic = topic.strip()
            if not topic:
                continue
            # Filter: must start with a capital letter (proper noun or formal term)
            if not topic[0].isupper():
                continue
            first_word = re.sub(r"[^\w]", "", topic.split()[0]).lower()
            if first_word in _FAQ_SKIP_STARTERS:
                continue
            # Require ≥2 words OR a single word ≥8 chars
            words = topic.split()
            if len(words) < 2 and len(topic) < 8:
                continue
            # Must not be excessively long (>7 words = likely a clause, not a noun phrase)
            if len(words) > 7:
                continue
            sig = re.sub(r"\W", "", topic[:25].lower())
            if sig in seen_topics:
                continue
            seen_topics.add(sig)
            pairs.append({"q": f"What is {topic}?", "a": s.strip()})
            if len(pairs) >= n:
                break
        if len(pairs) >= n:
            break

    return {
        "format": "bm25-kg",
        "collection_id": collection_id,
        "pairs": pairs[:n],
        "llm_available": _llm_available(),
    }


# ── Timeline Extractor ────────────────────────────────────────────────────────

def generate_timeline(collection_id: str, tenant_id: str = "default",
                      doc_id: str | None = None) -> dict:
    """Extract a chronological timeline instantly via BM25+KG (no LLM)."""
    chunks = _get_ranked_chunks(
        collection_id, tenant_id,
        query="dates events timeline history milestones when year",
        limit=40, doc_id=doc_id,
    )
    if not chunks:
        chunks = _get_chunks(collection_id, tenant_id, limit=100, doc_id=doc_id)
    if not chunks:
        return {"error": "No documents found in collection.", "collection_id": collection_id}

    content = _deterministic_timeline(chunks, collection_id)
    content["provider"] = "bm25-kg"
    output_id = _save_output("timeline", collection_id, tenant_id, content, "bm25-kg")
    content["output_id"] = output_id
    return content


def _deterministic_timeline(chunks: list[dict], collection_id: str) -> dict:
    # Regex patterns for dates + surrounding context
    date_patterns = [
        r"\b(January|February|March|April|May|June|July|August|September|October|November|December)\s+\d{1,2},?\s+\d{4}\b",
        r"\b\d{1,2}/\d{1,2}/\d{4}\b",
        r"\b\d{4}-\d{2}-\d{2}\b",
        r"\bFY\s*\d{2,4}\b",
        r"\bQ[1-4]\s*\d{4}\b",
        r"\b(20\d{2}|19\d{2})\b",
    ]
    events = []
    seen_dates = set()
    for c in chunks:
        text = (c.get("chunk_text") or "").strip()
        sentences = re.split(r"(?<=[.!?])\s+", text)
        for s in sentences:
            for pat in date_patterns:
                m = re.search(pat, s, re.I)
                if m:
                    date_str = m.group(0).strip()
                    key = (date_str, s[:40])
                    if key not in seen_dates and len(s) > 20:
                        seen_dates.add(key)
                        events.append({
                            "date": date_str,
                            "event": s.strip()[:200],
                            "source": c.get("source_doc", "unknown"),
                        })
                    break
        if len(events) >= 30:
            break

    # Sort by year if possible
    def _year(e: dict) -> int:
        m = re.search(r"\b(20\d{2}|19\d{2})\b", e.get("date", ""))
        return int(m.group(1)) if m else 9999

    events.sort(key=_year)
    return {
        "format": "deterministic",
        "collection_id": collection_id,
        "events": events,
        "llm_available": _llm_available(),
    }


# ── Audio Overview ────────────────────────────────────────────────────────────

def generate_audio_overview(collection_id: str, tenant_id: str = "default",
                            doc_id: str | None = None) -> dict:
    """Generate an audio podcast-style overview of a collection (or a single document).

    Step 1: Generate script (air-gap: deterministic summary, online: LLM podcast script).
    Step 2: TTS synthesis (air-gap: pyttsx3/espeak, online: same local TTS for now;
            cloud TTS providers can be plugged in via ICDEV_TTS_PROVIDER env var).
    Returns a dict with script text + audio_path (if TTS succeeded).
    """
    chunks = _get_chunks(collection_id, tenant_id, limit=80, doc_id=doc_id)
    if not chunks:
        return {"error": "No documents found in collection.", "collection_id": collection_id}

    # Generate script via deterministic BM25 path (instant).
    # Use enhance_with_llm() on the returned output_id for a narrative podcast script.
    script = _deterministic_script(chunks, collection_id)
    provider = "bm25-kg"

    # TTS synthesis on the deterministic script
    audio_path, tts_provider, tts_error = _synthesize_tts(script, collection_id)

    content: dict[str, Any] = {
        "format": "bm25-kg",
        "provider": provider,
        "tts_provider": tts_provider,
        "script": script,
        "collection_id": collection_id,
        "chunks_used": len(chunks),
        "llm_available": _llm_available(),
    }
    if audio_path:
        content["audio_path"] = audio_path
    if tts_error:
        content["tts_warning"] = tts_error

    output_id = _save_output("audio_overview", collection_id, tenant_id, content, provider)
    content["output_id"] = output_id
    return content


def _deterministic_script(chunks: list[dict], collection_id: str) -> str:
    sources = list({c.get("source_doc", "unknown") for c in chunks})[:5]
    key_sentences = []
    seen = set()
    for c in chunks[:30]:
        text = (c.get("chunk_text") or "").strip()
        sentences = re.split(r"(?<=[.!?])\s+", text)
        for s in sentences[:3]:
            s = s.strip()
            if len(s) > 60 and s not in seen:
                key_sentences.append(s)
                seen.add(s)
            if len(key_sentences) >= 8:
                break
        if len(key_sentences) >= 8:
            break

    intro = (
        f"Welcome to this document intelligence overview for collection '{collection_id}'. "
        f"This collection contains content from {len(sources)} source document{'s' if len(sources) != 1 else ''}."
    )
    body = " ".join(key_sentences)
    closing = "This concludes the overview. Refer to the full documents for complete details."
    return f"{intro} {body} {closing}"


def _synthesize_tts(script: str, collection_id: str) -> tuple[str | None, str, str | None]:
    """Attempt TTS synthesis. Returns (audio_path_or_None, provider, error_or_None)."""
    import os
    from pathlib import Path

    tts_provider_env = os.environ.get("ICDEV_TTS_PROVIDER", "auto").lower()
    audio_dir = Path("data") / "dic_audio"
    audio_dir.mkdir(parents=True, exist_ok=True)
    out_path = str(audio_dir / f"{collection_id}-{uuid.uuid4().hex[:8]}.mp3")

    # Try pyttsx3 (local, cross-platform, air-gap safe)
    if tts_provider_env in ("auto", "pyttsx3", "local"):
        try:
            import pyttsx3  # type: ignore[import]
            engine = pyttsx3.init()
            engine.setProperty("rate", 165)
            # pyttsx3 saves as .aiff on macOS, .wav on Windows — rename to .mp3 as a label
            wav_path = out_path.replace(".mp3", ".wav")
            engine.save_to_file(script[:5000], wav_path)
            engine.runAndWait()
            if Path(wav_path).exists() and Path(wav_path).stat().st_size > 100:
                return wav_path, "pyttsx3", None
        except Exception as exc:
            logger.debug("dic.tts: pyttsx3 failed: %s", exc)

    # Fallback: script-only (no audio file)
    return None, "script-only", (
        "TTS synthesis unavailable. Install pyttsx3 (pip install pyttsx3) for local audio generation. "
        "The script text is available for copy and use with any TTS tool."
    )


# ── LLM Enhancement (on-demand) ───────────────────────────────────────────────

def _update_output(output_id: str, content: dict, provider: str) -> None:
    """Overwrite content_json and provider for an existing dic_generated_outputs row."""
    try:
        conn = _conn()
        conn.execute(
            "UPDATE dic_generated_outputs SET content_json=%s, provider=%s WHERE id=%s",
            (json.dumps(content), provider, output_id),
        )
        conn.commit()
        conn.close()
    except Exception as exc:
        logger.warning("dic.output_generators: update error: %s", exc)


_LLM_PROMPTS: dict[str, str] = {
    "study_guide": (
        "You are a study guide author. Based on the following content, produce a structured "
        "study guide with:\n1. Overview (2-3 sentences)\n2. Key Topics (5-8 bullet points "
        "with 1-2 sentence descriptions)\n3. Key Terms (10-15 terms with brief definitions)\n"
        "4. Review Questions (5-7 questions)\n5. Summary\n\nContent:\n{corpus}"
    ),
    "faq": (
        "Generate a FAQ with exactly {n} Q&A pairs based on the following content. "
        "Format as JSON array: [{\"q\": \"question\", \"a\": \"answer\"}, ...]\n\nContent:\n{corpus}"
    ),
    "timeline": (
        "Extract a chronological timeline of key events from the following content. "
        "Format as JSON array: [{\"date\": \"YYYY or description\", \"event\": \"brief description\", "
        "\"context\": \"one sentence of context\"}]\n\nContent:\n{corpus}"
    ),
    "audio_overview": (
        "Write a podcast-style audio overview (500-700 words, conversational tone). "
        "Include: brief intro, 3-4 key insights, practical implications, closing summary. "
        "Sound natural when read aloud.\n\nContent:\n{corpus}"
    ),
}


def enhance_with_llm(output_id: str, collection_id: str, tenant_id: str,
                     output_type: str, doc_id: str | None = None,
                     n: int = 10) -> dict:
    """Layer LLM narrative coherence on top of a BM25+KG output (on-demand, 90s ceiling).

    Loads the existing output, fetches BM25 chunks, calls _try_llm() with a bounded
    90s timeout, updates the stored output, and returns the enhanced content.
    """
    # Retrieve existing output for reference
    existing: dict = {}
    try:
        conn = _conn()
        row = conn.execute(
            "SELECT content_json FROM dic_generated_outputs WHERE id=%s", (output_id,)
        ).fetchone()
        if row:
            existing = json.loads(row[0] or "{}")
        conn.close()
    except Exception:
        pass

    # Fetch ranked chunks for the LLM prompt
    chunks = _get_ranked_chunks(
        collection_id, tenant_id,
        query={"study_guide": "key concepts overview topics", "faq": "questions answers what how",
               "timeline": "dates events milestones", "audio_overview": "summary overview key points"
               }.get(output_type, "key information"),
        limit=40, doc_id=doc_id,
    )
    if not chunks:
        chunks = _get_chunks(collection_id, tenant_id, limit=120, doc_id=doc_id)
    if not chunks:
        return {**existing, "error": "No content available for LLM enhancement."}

    corpus = _corpus_text(chunks, max_chars=8_000)
    prompt_tmpl = _LLM_PROMPTS.get(output_type, _LLM_PROMPTS["study_guide"])
    prompt = prompt_tmpl.format(corpus=corpus, n=n)

    llm_text, provider = _try_llm(prompt, "document_qna")
    if not llm_text:
        return {**existing, "enhance_error": "LLM timed out or unavailable. Try again later."}

    # Build enhanced content depending on output type
    if output_type in ("study_guide", "audio_overview"):
        enhanced = {
            **existing,
            "format": "llm-synthesized",
            "provider": provider,
            "text": llm_text,
            "enhanced": True,
        }
        if output_type == "audio_overview":
            enhanced["script"] = llm_text
    elif output_type in ("faq", "timeline"):
        key = "pairs" if output_type == "faq" else "events"
        try:
            import ast as _ast
            m = re.search(r"\[.*\]", llm_text, re.S)
            parsed = json.loads(m.group(0)) if m else _ast.literal_eval(llm_text)
        except Exception:
            parsed = [{"q": "Summary", "a": llm_text}] if output_type == "faq" else []
        enhanced = {**existing, "format": "llm-synthesized", "provider": provider,
                    key: parsed[:n], "enhanced": True}
    else:
        enhanced = {**existing, "format": "llm-synthesized", "provider": provider,
                    "text": llm_text, "enhanced": True}

    _update_output(output_id, enhanced, provider)
    enhanced["output_id"] = output_id
    return enhanced
